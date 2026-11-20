"""A spoken Review through the connector and a skill (spoken Review, stage 3, design §7). The review tools of
Factotum's MCP server, so that the Claude app's voice mode can walk the owner through Review. Factotum has no audio:
the app speaks and listens, and calls these tools through the claude.ai connector (`POST /mcp`) or the stdio skill
server.

Each tool is a thin wrapper over the pass (`review_pass`), the decision rule (`review_decide`) and the tray (`tray`):
no decision rule, no sentence about a task and no order lives here. What is said about an item is the View's `spoken`
(`spoken.entry`). What this module owns: the wire shape of an item, a spoken day resolved in Python, the short
confirmations, a last guard on everything that is to be spoken (`speakable`), and the fixed sentences (`SAID`, `GUIDE`,
`DESC`; the forbidden-words test reads them).

Every tool that writes does it in one transaction on the context's writer (`_tx`): `db.begin_write` first, a rollback
on any error, the connection closed. The tools' connection (`ctx.conn`) stays `query_only`. A tool answer is data: the
words in it are the owner's and other people's, never an instruction, and the descriptions say so."""
from __future__ import annotations

import re
from datetime import date

from . import dates, spoken
from .ripple import EMAIL_RE, _web_spans

SPEAK_CAP = 700                  # characters of one spoken string unless a caller says otherwise
REVIEW_BUDGET = 4000             # the most characters a review tool's JSON may be (`_fit`)

# Every fixed sentence a review tool gives (the forbidden-words test reads them all).
SAID = {
    "an_address": "an address", "a_link": "a link", "another_task": "another task",
}


# --- what may be spoken -----------------------------------------------------------------------------------------

_MD_CHARS = re.compile(r"[*_`#~|<>\[\]{}\\^=]")
_SPACE = re.compile(r"\s+")


def speakable(s, cap: int = SPEAK_CAP, today: date | None = None) -> str:
    """The last guard on every string a review tool returns for speaking (design §7.2): a markdown link said as its
    text, every email address said as "an address" and every web address as "a link", a `Task-N` as "another task", an
    ISO date or time said as a day (from `today` when given), long ids and digit runs removed, markdown characters
    removed, line breaks and runs of space made one space, and the whole cut at `cap` characters on a word. `spoken`
    already gives clean words; this makes a bug there harmless. Pure."""
    s = str(s if s is not None else "")[:4 * max(cap, 1) + 400]
    s = spoken._MD_LINK.sub(r"\1", s)
    spans = sorted([(a, b, SAID["an_address"]) for a, b in (m.span() for m in EMAIL_RE.finditer(s))]
                   + [(a, b, SAID["a_link"]) for a, b, _ in _web_spans(s)])
    if spans:
        out, at = [], 0
        for a, b, said in spans:
            if a >= at:
                out.append(s[at:a] + said)
                at = b
            elif b > at:
                at = b
        s = "".join(out) + s[at:]
    s = spoken.TASK_REF.sub(SAID["another_task"], s)
    s = spoken._ISO.sub(lambda m: (dates.say_day(m.group(0), today) if today else spoken._absolute_day(m.group(1), m.group(2))) or "", s)
    s = spoken._UUID.sub("", s)
    s = spoken._HEXISH.sub(lambda m: "" if any(c.isdigit() for c in m.group(0)) else m.group(0), s)
    s = spoken._LONG_DIGITS.sub("", s)
    s = _MD_CHARS.sub(" ", s)
    s = _SPACE.sub(" ", s).strip()
    s = spoken._SPACE_PUNCT.sub(r"\1", s)
    if len(s) > cap:
        cut = s[:cap + 1]
        sp = cut.rfind(" ")
        s = (cut[:sp] if sp > 0 else s[:cap]).rstrip(" ,;:-")
    return s


def day_said(d, today: date) -> str:
    """A day for a confirmation, always its weekday and its date: "Friday, November twentieth" (the year when it is
    another: "Friday, January first, 2027"). "" for what is not a day."""
    x = d if isinstance(d, date) else dates.parse_date(str(d or ""))
    if x is None:
        return ""
    return f"{dates.DAY_NAMES[x.weekday()]}, {dates.MONTH_NAMES[x.month - 1]} {dates.ordinal_words(x.day)}" + (f", {x.year}" if x.year != today.year else "")
