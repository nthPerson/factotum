"""The Review card as sentences a voice can say (spoken Review, stage 2e, design §6.10). Stage 3 hands these to the
Claude app's voice mode as they are, so they are the product: what the item is, why it is in front of the owner and
the one fact that decides it, then the recommendation's basis and its yes-or-no question, each kept short enough to
follow by ear (a sentence of about 25 words at most, `say` at most MAX_SAY words, a list of at most three things).

Built only from the View's entry the pass already built (`review_pass._entry`, `_group_entry`) and `names` ({task id:
name} for a `Task-N` the words would name): never from HTML, never with a query. Pure.

Nothing this module returns, but `more.number`, holds `Task-N` or a bare task number, an id, a URL or anything
host-shaped, an email address, a score, a priority, an ISO date or time, markdown, emoji or a symbol. Text a model or a
stranger wrote (a proposal's reason, a suggestion's why, a mail's subject and sender, Slack text) and the owner's own
text go through `flat` / `subject` / `person`: line breaks removed, markdown removed, `Task-N` replaced by that task's
short name, every web and email address removed with the ripple guard's tested patterns, long hex runs and ids
removed, every pattern bounded, and a sentence holding a word the project never shows a person dropped whole. A
stranger's words are said inside quotes, as what they wrote, never as something to do. Every fixed sentence is in
TEMPLATES (the forbidden-words test reads them)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from . import dates
from .prompts import one_line
from .ripple import EMAIL_RE, _web_spans

MAX_SAY = 45                    # `say` is at most this many words and aims at about 25
SENTENCE_AIM = 25               # a sentence is built to stay under this many words
NAME_WORDS = 8                  # `short`: a task's name in at most this many words
SUBJECT_WORDS = 8               # a mail's subject
PERSON_WORDS = 6                # a display name
QUOTE_WORDS = 12                # a Slack message quoted
MORE_WORDS = 30                 # more.why, more.links, more.evidence
TEXT_SENTENCES, TEXT_WORDS = 3, 60      # more.text
INPUT_CAP = 4000                # what any one cleaning reads of a value

TEMPLATES = {
    # why it is here, at most five words; then ": <name>, due <day>, firm."
    "new": "New", "new_rec": "New from {whose} {title}", "new_rec_day": "New from {whose} recording", "new_rec_title": "New from the {title}",
    "new_recording": "New from a recording", "new_course": "New in {title}", "new_mail": "New from your mail", "new_canvas": "New from Canvas",
    "new_chat": "New from a chat", "new_hand": "New, added by hand", "new_slack": "New from Slack",
    "link_head": "Suggested link", "free_head": "Free now", "gone_head": "A task it waited on closed", "steps_head": "All steps done",
    "changed_head": "A linked task changed", "snooze_head": "Back from Later", "slipped_head": "Soft date slipped", "fading_head": "Fading",
    "last_call_head": "Last call", "back_head": "Back, mentioned again", "slack_head": "Mentioned in Slack",
    "due": "due {day}", "firm": "firm", "no_date": "no date", "quiet_since": "untouched since {day}",
    "fades": "It fades {on} unless you keep it.", "lapses": "Its firm date has passed; it lapses unless you keep it.",
    "mail_on": "New mail on {name}.", "wrote_about": "{who} wrote about “{subject}”",
    "wrote": "{who} wrote", "replied_about": "{who} replied about “{subject}”", "maybe_from": "a mail that may be from {who}, about “{subject}”",
    "more_mail_1": "One more mail came in.", "more_mail_n": "{n} more mails came in.",
    "slack_wrote": "{who} wrote in {where}: “{text}”.", "slack_label": "A Slack message: “{text}”.",
    "mail_from": "A mail from {who}: “{subject}”.", "mail_from_bare": "A mail from {who}.", "looks_about": "It looks to be about {name}.",
    "suggests": "{by} suggests a change to {name}.", "would_mean": "It would mean {change}.", "suggests_also": "The {by} suggests {change}.",
    "more_suggestions": "And {n} more suggestions.",
    "mail_also": "A mail looks to be about it.",
    "planner": "Planner", "ripple": "Ripple", "planner_low": "planner", "ripple_low": "ripple",
    "links_1": "One link suggested: {a}.", "links_n": "{n} links suggested.", "links_first": "The first: {a}.", "links_second": "The second: {a}.",
    "links_more": "And {n} more.",
    "lp_step_of": "it is a step of {other}", "lp_has_step": "{other} is a step of it", "lp_waits": "it waits on {other}",
    "lp_frees": "{other} waits on it", "lp_same": "it may be the same task as {other}", "lp_related": "it is related to {other}",
    "is_done": "{other} is done.", "did_done": "is done", "did_dropped": "was dropped", "did_changed": "changed", "did_merged": "was merged",
    "did_reopened": "was reopened", "did_faded": "faded", "did_lapsed": "lapsed", "did_other": "changed",
    "text_updated": "Its text was brought up to date.",
    "card_mail": "From your mail: {who}, about “{subject}”.", "card_mail_bare": "From your mail: {who}.",
    "card_slack": "Mentioned in Slack: {who} wrote in {where}.", "card_slack_bare": "Mentioned in Slack.",
    "card_planner": "Planner suggests a task: {name}.", "card_ripple": "Ripple suggests a task: {name}.",
    "suggested_task": "Suggested task: {name}.", "suggested_quote": "Suggested task: “{name}”{due}.",
    "card_step": "It would be a step of {other}.", "card_waits": "It would wait on {other}.",
    "group_rec": "{n} new from {whose} {title}", "group_rec_day": "{n} new from {whose} recording", "group_rec_title": "{n} new from the {title}",
    "group_recording": "{n} new from a recording", "group_course": "{n} new in {title}", "group_thread": "{n} new from one mail thread",
    "group_new": "{n} new tasks", "group_decided": "{n} decided already", "group_all_due": "all due {day}", "group_both_due": "both due {day}",
    "group_other_1": "The other one: ", "group_other_n": "The other {n}: ", "group_then": "Then ", "group_more": "And {n} more.",
    "settled": "{name}: this one is settled.", "staged": "Staged already: {what}.", "answered": "Answered on another card.",
    "staged_keep": "kept", "staged_done": "done", "staged_drop": "dropped", "staged_later": "set for later", "staged_dismiss": "dismissed",
    "staged_create": "to be created", "staged_other": "decided",
    "q_task": "Keep, done, or drop?", "q_fading": "Keep, drop, or long-term?", "q_slipped": "Keep, later, or drop?",
    "q_snooze": "Keep, later again, or drop?", "q_last_call": "Done, keep, or drop?", "q_mail": "Done, keep, or later?",
    "q_gone": "Still needed? Keep, or drop?", "q_steps": "Done, or keep?", "q_merge": "Merge them, or not the same?",
    "q_new": "Keep, or drop?", "q_card": "Create it, or dismiss?", "q_group": "Keep them all, or one by one?", "q_decided": "Leave it as staged?",
    # more
    "why_head": "It is here because {reasons}.", "why_card": "The suggestion says: {reason}", "why_group": "They are new, and came from one {what}.",
    "why_recording": "recording", "why_course": "course", "why_thread": "mail thread",
    "r_new": "it is new", "r_link_suggested": "a link is suggested", "r_thread_updated": "new mail came on its thread",
    "r_mentioned": "it was mentioned in Slack", "r_planner": "the planner suggests a change", "r_ripple": "a linked task's change suggests one",
    "r_freed": "what it waited on is done", "r_blocker_gone": "a task it waited on closed", "r_steps_done": "all its steps are done",
    "r_linked_changed": "a linked task changed", "r_snooze_over": "its Later day came", "r_slipped": "its soft date passed",
    "r_fading": "it has been quiet and will fade", "r_last_call": "its firm date passed", "r_back": "it was mentioned again after it faded",
    "r_other": "something on it changed", "r_none": "it was asked about",
    "link_step_of": "It is a step of {other}.", "link_has_step": "{other} is a step of it.", "link_waits": "It waits on {other}.",
    "link_frees": "{other} waits on it.", "no_links": "It has no links.",
    "ev_mail": "{who} wrote about “{subject}”, {day}.", "ev_slack": "{who} wrote in {where}: “{text}”.", "ev_label": "A message: “{text}”.",
    "ev_reason": "The {by} says: {reason}", "no_evidence": "Nothing else came in on it.",
    "number_task": "Task {n}", "number_tasks": "Tasks {list}", "number_card": "It has no number yet: it is a suggestion.",
    "this_task": "this task", "another_task": "another task", "someone": "someone",
    # the options in words
    "o_rec": "yes", "o_done": "done", "o_keep": "keep", "o_later": "later, with a day", "o_drop": "drop", "o_pin": "pin it to Top",
    "o_unpin": "take it off Top", "o_longterm": "long-term", "o_not_longterm": "not long-term", "o_create": "create it",
    "o_card_dismiss": "dismiss it", "o_keep_all": "keep them all", "o_undecide": "take it back",
    "o_link_1": "link it", "o_link_n": "link the {nth}", "o_not_related_1": "not related", "o_not_related_n": "the {nth} is not related",
    "o_merge_1": "merge them", "o_merge_n": "merge the {nth}", "o_step_1": "a step of it", "o_step_n": "the {nth} is a step of it",
    "o_waits_1": "waits on it", "o_waits_n": "the {nth} waits on it", "o_same_1": "the same task", "o_same_n": "the {nth} is the same task",
    "o_accept_1": "accept the change", "o_accept_n": "accept the {nth} change", "o_dismiss_1": "no to the change", "o_dismiss_n": "no to the {nth} change",
    "o_follow_1": "follow the thread", "o_follow_n": "follow the {nth} thread", "o_not_about_1": "not about this", "o_not_about_n": "the {nth} is not about this",
    "member": "the {nth}, {name}",
    # a change in words
    "c_due": "making it due {day}", "c_no_due": "removing its date", "c_snooze": "setting it aside until {day}", "c_project": "moving it to {project}",
    "c_rename": "renaming it", "c_text": "changing its text", "c_effort": "changing its effort", "c_who": "changing who it is with",
    "c_details": "changing its details", "c_done": "marking it done", "c_drop": "dropping it", "c_keep": "keeping it",
    "c_pin_top": "pinning it to Top", "c_unpin": "taking it off Top", "c_longterm": "marking it long-term", "c_reminder": "adding a reminder",
    "c_reminder_at": "adding a reminder for {day}", "c_add_note": "adding a note", "c_step_of": "making it a step of {other}",
    "c_waits_on": "making it wait on {other}", "c_unlink": "unlinking it from {other}", "c_merge": "merging it into {other}",
    "c_reopen": "reopening it", "c_follow": "following its mail thread", "c_create": "making a new task", "c_other": "changing it",
}
T = TEMPLATES

FORBIDDEN = re.compile(r"\bblocked\b|\bdependenc", re.I)          # the words no person or agent reads (CLAUDE.md, 2026-11-10)
TASK_REF = re.compile(r"\bTask-([0-9]{1,9})\b")
_BREAKS = re.compile(r"[\r\n\x0b\x0c\x1c-\x1e\x85  ]")
_MD_LINE = re.compile(r"^[ \t]{0,8}(?:#{1,6}|[-*+>•]|[0-9]{1,3}[.)])[ \t]{1,8}")
_MD_LINK = re.compile(r"!?\[([^\]\n]{0,200})\]\([^)\n]{0,2000}\)")
_UUID = re.compile(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b")
_HEXISH = re.compile(r"\b(?:0[xX])?[A-Za-z]{0,8}_?[0-9A-Fa-f]{12,64}\b")
_LONG_DIGITS = re.compile(r"\b[0-9]{9,40}\b")
_ISO = re.compile(r"\b([0-9]{4}-[0-9]{2}-[0-9]{2})(?:[T ]([0-9]{2}:[0-9]{2})(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?\b")
_SLASH_WORDS = re.compile(r"(?<=[A-Za-z])/(?=[A-Za-z])")
_WEEKDAY_ABBR = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
_MONTH_ABBR = {m.lower()[:3]: i for i, m in enumerate(dates.MONTH_NAMES)} | {"sept": 8}
_MONTH_DAY = re.compile(r"\b(?:(Mon|Tues?|Wed|Thu(?:rs?)?|Fri|Sat|Sun)\.?,?\s{1,3})?"
                        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
                        r"\.?\s{1,3}([0-9]{1,2})(?:st|nd|rd|th)?\b")
_ARROW = re.compile(r"\s{0,3}(?:->|=>|→|⇒)\s{0,3}")
_DROP_CHARS = re.compile(r"[*`_#~^|<>\[\]{}\\=]")
_EMPTY_BRACKETS = re.compile(r"[(\[{][\s,;:.]{0,20}[)\]}]")
_SPACE_PUNCT = re.compile(r"\s{1,40}([.,;:!?])")
_REPEAT_PUNCT = re.compile(r"([.,;:])(?:\s{0,3}[.,;:]){1,20}")
_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_PARENS = re.compile(r"\([^()]{0,200}\)|\[[^\[\]]{0,200}\]")
_WORD_HYPHEN = re.compile(r"(?<=[A-Za-z])-(?=[A-Za-z])")
_PERSON = re.compile(r'^\s{0,8}"?([^<>"]{0,120}?)"?\s{0,8}<([^<>]{0,320})>\s{0,8}$')
_SUBJECT_PREFIX = re.compile(r"^\s{0,4}(?:re|fwd?|fw|aw|wg|sv)\s{0,2}:\s{0,4}", re.I)
_SUBJECT_CUT = re.compile(r"\s(?:@|\||–|—|-)\s")
# Quote marks in what a stranger wrote: they could close the “…” it is said inside (review 2026-11-20). An apostrophe
# inside a word (O'Brien, don't) stays.
_QUOTES = re.compile("[\"“”„‟«»‹›`´]|(?<![A-Za-z])['‘’‚‛]|['‘’‚‛](?![A-Za-z])")
_DANGLING = re.compile(r"\b(?:on|with|for|to|of|from|about|after|before|by|under|in)\s{1,3}(?=(?:and|or)\b|[,.;:]|$)", re.I)
_MD_DATE = re.compile(r"\b[0-9]{1,2}/[0-9]{1,2}(?:/[0-9]{2,4})?\b")
SHOUTING = {"TOMORROW", "TODAY", "TONIGHT", "URGENT", "REMINDER", "IMPORTANT", "DEADLINE", "NOW", "FREE", "ACTION", "REQUIRED",
            "LAST", "CHANCE", "FINAL", "NEW", "WEEK", "THIS", "NEXT", "DUE", "SOON", "PLEASE", "READ", "OPEN"}
SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "which", "with", "that", "but",
         "is", "are", "my", "his", "her", "their", "its", "your", "our", "into", "per", "via", "than", "so", "if", "be", "was", "were"}
# A name is cut before one of these when at least four words stand before it ("Send the screenshots for terrain selection").
CLAUSE = {"per", "before", "after", "for", "via", "across", "by", "so", "because", "once", "when", "while", "until", "if", "at",
          "with", "about", "including", "using", "during", "since", "within", "due"}
LONG_CLAUSE = {"for", "with"}     # these two only after five words ("Find a working link for the lab pads" keeps its object)
# Generic words of a recording's title, said in lower case ("yesterday's project meeting").
GENERIC = {"meeting", "project", "research", "lab", "team", "call", "sync", "standup", "weekly", "daily", "monthly", "interview",
           "lecture", "class", "discussion", "review", "workshop", "seminar", "office", "hours", "group", "planning", "update",
           "notes", "session", "chat", "talk", "recording", "conversation", "check-in", "one-on-one"}
_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
         "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


# --- small words -----------------------------------------------------------------------------------------------

def count_words(n: int) -> str:
    """0 to 99 in words ("forty-nine"); larger numbers as digits."""
    if 0 <= n < 20:
        return _ONES[n]
    if 20 <= n < 100:
        return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")
    return str(n)


def number_words(n: int) -> str:
    """A task's number as a person reads it: "forty-nine", "four seventy-seven", "four oh five", "four hundred",
    "twelve thirty-four"."""
    if n < 100:
        return count_words(n)
    if n < 1000:
        h, r = divmod(n, 100)
        return f"{_ONES[h]} hundred" if r == 0 else f"{_ONES[h]} oh {_ONES[r]}" if r < 10 else f"{_ONES[h]} {count_words(r)}"
    if n < 10000:
        h, r = divmod(n, 100)
        return f"{count_words(h)} hundred" if r == 0 else f"{count_words(h)} oh {_ONES[r]}" if r < 10 else f"{count_words(h)} {count_words(r)}"
    return str(n)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _lcfirst(s: str) -> str:
    """The first letter in lower case, unless the first word is written in capitals (an acronym: "RSVP for …")."""
    return s if len(s) > 1 and s[:2].isupper() else s[:1].lower() + s[1:]


def _words(s: str) -> int:
    return len(s.split())


def _trim_small(ws: list[str]) -> list[str]:
    ws = [w for w in ws]
    while ws and (ws[-1].lower().strip(".,;:!?") in SMALL or not ws[-1].strip(".,;:!?-–—'")):
        ws.pop()
    if ws:
        ws[-1] = ws[-1].rstrip(".,;:!?-–—")
    return ws


# --- cleaning ---------------------------------------------------------------------------------------------------

def _absolute_day(d: str, hm: str | None) -> str:
    """An ISO date with no Review's day to say it from: "November twenty-fourth" (at 2 PM)."""
    try:
        x = date.fromisoformat(d)
    except ValueError:
        return ""
    out = f"{dates.MONTH_NAMES[x.month - 1]} {dates.ordinal_words(x.day)}"
    if hm:
        h, m = (int(p) for p in hm.split(":"))
        if h < 24 and m < 60:
            out += " at " + dates.say_time(h, m)
    return out


def _month_day(m: re.Match) -> str:
    """"Thu Nov 19" → "Thursday, November nineteenth"; "Nov. 20" → "November twentieth"."""
    mo, day = _MONTH_ABBR.get(m.group(2).lower()[:4] if m.group(2).lower().startswith("sept") else m.group(2).lower()[:3]), int(m.group(3))
    if mo is None or not 1 <= day <= 31:
        return m.group(0)
    wd = _WEEKDAY_ABBR.get((m.group(1) or "").lower())
    return (f"{dates.DAY_NAMES[wd]}, " if wd is not None else "") + f"{dates.MONTH_NAMES[mo]} {dates.ordinal_words(day)}"


def _scrub(text, *, names=None, today: date | None = None, own: int | None = None, refs: str = "name") -> str:
    """One line of text with everything a voice must not say taken out (the rules in the module's docstring); no cut.
    `refs`: "name" says a `Task-N` as that task's short name ("this task" for `own`, "another task" when unknown),
    "drop" removes it."""
    s = str(text if text is not None else "")[:INPUT_CAP]
    lines = [ln.strip() for ln in _BREAKS.split(s.replace("\r\n", "\n"))]
    lines = [_MD_LINE.sub("", ln) for ln in lines if ln]
    s = " ".join(ln if i == len(lines) - 1 or ln[-1:] in ".!?:;," else ln + "." for i, ln in enumerate(lines))
    s = _MD_LINK.sub(r"\1", s)
    cut = sorted([m.span() for m in EMAIL_RE.finditer(s)] + [(a, b) for a, b, _ in _web_spans(s)])
    if cut:
        out, at = [], 0
        for a, b in cut:
            if a >= at:
                out.append(s[at:a])
                at = b
            elif b > at:
                at = b
        s = "".join(out) + s[at:]
    names = names or {}

    def ref(m):
        if refs == "drop":
            return ""
        n = int(m.group(1))
        if n == own:
            return T["this_task"]
        return short(names[n]) if names.get(n) else T["another_task"]
    s = TASK_REF.sub(ref, s)
    s = _ISO.sub(lambda m: (dates.say_day(m.group(0), today) if today else _absolute_day(m.group(1), m.group(2))) or "", s)
    s = _MONTH_DAY.sub(_month_day, s)
    s = _UUID.sub("", s)
    s = _HEXISH.sub(lambda m: "" if any(c.isdigit() for c in m.group(0)) else m.group(0), s)
    s = _LONG_DIGITS.sub("", s)
    s = s.replace("&", " and ").replace("@", " at ").replace("+", " plus ")
    s = _ARROW.sub(" to ", s)
    s = _SLASH_WORDS.sub(" or ", s)
    s = _DROP_CHARS.sub(" ", s)
    s = "".join(c for c in s if unicodedata.category(c) not in ("So", "Sk", "Cs", "Co", "Cn", "Cf", "Cc", "Sm") or c in "%$")
    s = _EMPTY_BRACKETS.sub(" ", s)
    s = one_line(s)
    s = _SPACE_PUNCT.sub(r"\1", s)
    s = _REPEAT_PUNCT.sub(r"\1", s)
    return s.strip(" ,;:")


def _cut(s: str, words: int) -> str:
    ws = s.split()
    if len(ws) <= words:
        return s
    ws = _trim_small(ws[:words])
    return " ".join(ws) + "." if ws else ""


def flat(text, *, sentences: int = 1, words: int = 25, names: dict | None = None, today: date | None = None, own: int | None = None) -> str:
    """Text a model, a stranger or the owner wrote, as words for the ear: one line, no markdown, `Task-N` as that task's
    short name, no web or email address, no id or long hex run, an ISO date said (from `today` when given), the first
    `sentences` sentences that hold no forbidden word, at most `words` words (cut on a word, never on a small one)."""
    s = _scrub(text, names=names, today=today, own=own)
    kept = [x for x in _SENTENCES.split(s) if x and not FORBIDDEN.search(x)][:max(1, sentences)]
    return _cut(" ".join(kept), words)


def short(name, words: int = NAME_WORDS, *, names: dict | None = None, own: int | None = None) -> str:
    """A task's name to say: no `(…)` or `[…]`, no address; a `Task-N` said as that task's short name when `names` has it,
    else left out (and a word left hanging before it, "on and", with it); cut before a trailing clause ("per", "for",
    "at", … with at least four words before it), at most `words` words (an " and " that starts a second action is a
    good place to stop), never ending on a small word."""
    s = _PARENS.sub(" ", str(name if name is not None else "")[:INPUT_CAP])
    if names:
        s = TASK_REF.sub(lambda m: (T["this_task"] if int(m.group(1)) == own else
                                    short(names[int(m.group(1))], words=6) if names.get(int(m.group(1))) else m.group(0)), s)
    s = _scrub(s, refs="drop")
    s = _DANGLING.sub("", s)
    s = _WORD_HYPHEN.sub(" ", s)
    ws = s.split()
    for i, w in enumerate(ws):
        if i >= 3 and w[-1:] in ",;:" or (i >= 3 and w in ("–", "—", "-")):
            ws = ws[:i + 1] if w[-1:] in ",;:" else ws[:i]
            break
        if i >= (5 if w.lower() in LONG_CLAUSE else 4) and w.lower() in CLAUSE and not ws[i - 1].lower().endswith("ing"):
            ws = ws[:i]
            break
    if len(ws) > words:
        j = next((j for j in range(min(words, len(ws) - 1), 2, -1) if ws[j].lower() == "and" and ws[j + 1][:1].islower()), None)
        ws = ws[:j] if j else ws[:words]
    return " ".join(_trim_small(ws))


def person(value) -> str:
    """A person by display name, never an address: {"email", "displayName"} (Mailbird), {"address", "name"},
    "Name <address>", or a plain name; "" when only an address is known."""
    if isinstance(value, dict):
        value = value.get("displayName") or value.get("name") or ""
    s = one_line(str(value or ""))[:400]
    m = _PERSON.match(s)
    if m:
        s = m.group(1)
    s = _PARENS.sub(" ", s)
    s = _QUOTES.sub(" ", _scrub(s, refs="drop")).replace(":", " ").replace(";", " ")
    s = " ".join(s.split()).strip(" ,.")
    if not any(c.isalpha() for c in s) or FORBIDDEN.search(s):
        return ""
    return " ".join(s.split()[:PERSON_WORDS])


def sender(value) -> str:
    """Who wrote a mail: `person`, or "someone"."""
    return person(value) or T["someone"]


def subject(value) -> str:
    """A mail's subject to say inside quotes: no Re: or Fwd:, nothing in brackets, cut at a spaced dash, bar or @, no
    numeric date, a shouted word in lower case, no address, at most SUBJECT_WORDS words."""
    s = one_line(str(value or ""))[:500]
    for _ in range(5):
        s2 = _SUBJECT_PREFIX.sub("", s)
        if s2 == s:
            break
        s = s2
    s = _PARENS.sub(" ", s)
    m = _SUBJECT_CUT.search(s)
    if m and m.start() > 0:
        s = s[:m.start()]
    s = _MD_DATE.sub(" ", s)
    s = " ".join(w.lower() if w.strip("!?.,:;") in SHOUTING else w for w in s.split())
    s = _QUOTES.sub(" ", _scrub(s, refs="drop")).rstrip(" !?.,:;")
    if FORBIDDEN.search(s):                          # a stranger's subject with a word the project never says: not said
        return ""
    return " ".join(_trim_small(s.split()[:SUBJECT_WORDS]))


def _name_of(n, names: dict, own=None) -> str:
    if n is not None and n == own:
        return T["this_task"]
    return short((names or {}).get(n)) if (names or {}).get(n) else T["another_task"]


def change(change: dict, today: date, names: dict | None = None) -> str:
    """A proposed change in words, after "suggests": "making it due tomorrow at 2 PM", "marking it done"."""
    c = change if isinstance(change, dict) else {}
    op, f = c.get("op"), c.get("fields") if isinstance(c.get("fields"), dict) else {}
    names = names or {}
    if op == "set":
        parts = []
        if "due" in f:
            v = f.get("due")
            parts.append(T["c_due"].format(day=dates.say_day(v, today)) if v and dates.say_day(v, today) else T["c_no_due"])
        if f.get("snoozed_until"):
            parts.append(T["c_snooze"].format(day=dates.say_day(f["snoozed_until"], today, time=False)))
        if f.get("project"):
            p = short(f["project"], 4)
            parts.append(T["c_project"].format(project=p) if p else T["c_details"])
        if "top_rank" in f:
            parts.append(T["c_pin_top"] if f["top_rank"] else T["c_unpin"])
        rest = set(f) - {"due", "due_is_datetime", "hard", "snoozed_until", "project", "top_rank"}
        for k, w in (("name", "c_rename"), ("context", "c_text"), ("effort", "c_effort")):
            if k in rest:
                parts.append(T[w])
                rest.discard(k)
        if rest & {"counterpart", "waiting_on"}:
            parts.append(T["c_who"])
            rest -= {"counterpart", "waiting_on"}
        if rest:
            parts.append(T["c_details"])
        return " and ".join(parts[:2]) if parts else T["c_details"]
    if op == "add_reminder":
        day = dates.say_day(f.get("fire_at"), today)
        return T["c_reminder_at"].format(day=day) if day else T["c_reminder"]
    if op in ("link", "unlink"):
        o = _name_of(f.get("other"), names, c.get("task_id"))
        if op == "unlink":
            return T["c_unlink"].format(other=o)
        return T["c_step_of" if f.get("kind") == "part_of" else "c_waits_on"].format(other=o)
    if op == "merge":
        return T["c_merge"].format(other=_name_of(f.get("into"), names, c.get("task_id")))
    simple = {"done": "c_done", "drop": "c_drop", "keep": "c_keep", "pin_top": "c_pin_top", "unpin": "c_unpin", "longterm": "c_longterm",
              "add_note": "c_add_note", "reopen": "c_reopen", "follow_thread": "c_follow", "create": "c_create"}
    return T[simple[op]] if op in simple else T["c_other"]


# --- the entry --------------------------------------------------------------------------------------------------

def _due(due, hard, today: date, *, lead: str = ", ") -> str:
    """", due today, firm" (or "" with no date)."""
    day = dates.say_day(due, today) if isinstance(due, str) and due else ""
    if not day:
        return ""
    return lead + T["due"].format(day=day) + (", " + T["firm"] if hard else "")


def _task_due(t: dict, today: date) -> str:
    return _due(t.get("due"), t.get("hard"), today)


def _title(title) -> str:
    """A recording's title in words: generic words in lower case, "Meeting: X" as "meeting on X"."""
    s = _WORD_HYPHEN.sub(" ", _scrub(title, refs="drop"))
    head, sep, rest = s.partition(":")
    ws = [w.lower() if w.lower() in GENERIC else w for w in head.split()]
    if sep and rest.strip() and len(ws) == 1:
        s = f"{ws[0]} on {rest.strip()}"
    else:
        s = " ".join(ws)
    return " ".join(_trim_small(s.split()[:NAME_WORDS]))


def _from(prov: dict | None, t: dict, today: date, *, group: int | None = None) -> str:
    """Where a new task (or a group of `group`) came from, in five words or so."""
    p = prov or {}
    k = "group_" if group else "new_"
    fill = {"n": _cap(count_words(group)) if group else ""}
    if p.get("kind") == "recording":
        title, day = _title(p.get("title")) if p.get("title") else "", p.get("day")
        whose = dates.say_whose(day, today) if day else ""
        if title and whose:
            return T[k + "rec"].format(whose=whose, title=title, **fill)
        if whose:
            return T[k + "rec_day"].format(whose=whose, **fill)
        if title:
            return T[k + "rec_title"].format(title=title, **fill)
        return T[k + "recording"].format(**fill)
    if p.get("kind") == "course" and p.get("title"):
        c = short(p["title"], 5)
        if c:
            return T[k + "course"].format(title=c, **fill)
    if p.get("kind") == "thread":
        return T["group_thread"].format(**fill) if group else T["new_mail"]
    if group:
        return T["group_new"].format(**fill)
    src = str(t.get("source") or "")
    return T.get({"Plaud": "new_recording", "Canvas": "new_canvas", "Chat": "new_chat", "Manual": "new_hand", "Slack": "new_slack",
                  "Mail": "new_mail"}.get(src, "new"))


def _link_phrase(s: dict, names: dict) -> str:
    o = short(s.get("other_name")) or _name_of(s.get("other"), names)
    kind, out = s.get("kind"), s.get("dir") != "in"
    if kind == "part_of":
        return T["lp_step_of"].format(other=o) if out else T["lp_has_step"].format(other=o)
    if kind == "waits_on":
        return T["lp_waits"].format(other=o) if out else T["lp_frees"].format(other=o)
    if kind == "same_as":
        return T["lp_same"].format(other=o)
    return T["lp_related"].format(other=o)


def _links_sentences(sugg: list[dict], names: dict) -> list[str]:
    """One link in one sentence; two or more one by one ("The first: …"), in the order of the options."""
    ps = [_link_phrase(s, names) for s in sugg]
    if len(ps) == 1:
        return [T["links_1"].format(a=ps[0])]
    out = [T["links_n"].format(n=_cap(count_words(len(ps)))), T["links_first"].format(a=ps[0]), T["links_second"].format(a=ps[1])]
    return out + ([T["links_more"].format(n=count_words(len(ps) - 2))] if len(ps) > 2 else [])


def _proposals(item: dict) -> list[dict]:
    return [e for e in item.get("evidence") or [] if isinstance(e.get("change"), dict)]


def _mail_of(e: dict) -> dict:
    c = e.get("change") or {}
    m = c.get("mail") if isinstance(c.get("mail"), dict) else e.get("mail") if isinstance(e.get("mail"), dict) else {}
    f = c.get("fields") if isinstance(c.get("fields"), dict) else {}
    return {"sender": m.get("sender"), "subject": m.get("subject") or f.get("subject")}


def _mail_line(l: dict) -> str:
    who, subj = sender(l.get("who")), subject(l.get("subject"))
    if l.get("maybe"):
        return T["maybe_from"].format(who=who, subject=subj) if subj else T["wrote"].format(who=who)
    if l.get("reply"):
        return T["replied_about"].format(who=who, subject=subj) if subj else T["wrote"].format(who=who)
    return T["wrote_about"].format(who=who, subject=subj) if subj else T["wrote"].format(who=who)


def _quoted(s: str) -> str:
    """Text a stranger wrote, to say inside “…”: no quote mark of its own, no forbidden word, no closing full stop."""
    s = " ".join(_QUOTES.sub(" ", s or "").split()).rstrip(".")
    return "" if FORBIDDEN.search(s) else s


def _slack_line(l: dict, today: date, tmpl="slack_wrote") -> str:
    if l.get("label") is not None and not l.get("who"):
        q = _quoted(flat(l.get("label"), words=QUOTE_WORDS, today=today))
        return T["slack_label"].format(text=q) if q else ""
    who = sender(l.get("who"))
    where = _quoted(_cut(_scrub(l.get("where"), refs="drop"), 4)) or "Slack"
    q = _quoted(flat(l.get("text"), words=QUOTE_WORDS, today=today))
    return T[tmpl].format(who=who, where=where, text=q) if q else T["wrote"].format(who=who) + "."


def _says_others(ve: dict) -> bool:
    """Whether `say` holds words a stranger wrote (a sender, a subject, Slack text), so stage 3 marks it as data."""
    it = ve.get("item") or {}
    if ve.get("settled"):
        return False
    if ve.get("kind") == "card":
        return isinstance(it.get("mail"), dict) or it.get("kind") == "slack_mention"
    if ve.get("kind") != "task":
        return False
    reasons = list(it.get("reasons") or [])
    primary = it.get("primary") or (reasons[0] if reasons else None)
    lines = it.get("evidence_lines") or []
    if primary == "thread_updated":
        return any(l.get("kind") == "mail" for l in lines)
    if primary == "mentioned":
        return any(l.get("kind") == "slack" for l in lines)
    if primary in ("planner", "ripple"):
        props = _proposals(it)
        return bool(props) and all(e["change"].get("op") == "follow_thread" for e in props)
    return False


def _task_sentences(ve: dict, today: date, names: dict) -> list[str]:
    it = ve.get("item") or {}
    t = it.get("task") or {}
    own = t.get("id")
    reasons = list(it.get("reasons") or [])
    primary = it.get("primary") or (reasons[0] if reasons else None)
    name = short(t.get("name"), names=names, own=own) or T["this_task"]
    due = _task_due(t, today)
    nd = name + due
    sugg = list(it.get("link_suggestions") or [])
    props = _proposals(it)
    follows = [e for e in props if e["change"].get("op") == "follow_thread"]
    others = [e for e in props if e["change"].get("op") != "follow_thread"]
    lines = list(it.get("evidence_lines") or [])
    mails = [l for l in lines if l.get("kind") == "mail"]
    slacks = [l for l in lines if l.get("kind") == "slack"]
    out: list[str] = []
    done_links = done_props = done_fade = False

    def by_of(e):
        return "ripple" if e.get("by") == "ripple" else "planner"

    if primary == "new" or (primary == "link_suggested" and "new" in reasons):
        out.append(f"{_from(ve.get('provenance'), t, today)}: {nd}.")
    elif primary == "link_suggested":
        out.append(f"{T['link_head']}: {nd}.")
    elif primary == "thread_updated":
        out.append(T["mail_on"].format(name=nd))
        if mails:
            out.append(_cap(_mail_line(mails[0])) + ".")
        if len(mails) > 1:
            out.append(T["more_mail_1"] if len(mails) == 2 else T["more_mail_n"].format(n=_cap(count_words(len(mails) - 1))))
    elif primary == "mentioned":
        out.append(f"{T['slack_head']}: {nd}.")
        if slacks:
            out.append(_slack_line(slacks[0], today))
    elif primary in ("planner", "ripple") and props:
        if follows and not others:
            m = _mail_of(follows[0])
            subj = subject(m.get("subject"))
            out.append(T["mail_from"].format(who=sender(m.get("sender")), subject=subj) if subj else T["mail_from_bare"].format(who=sender(m.get("sender"))))
            out.append(T["looks_about"].format(name=nd))
        else:
            e = others[0]
            out.append(T["suggests"].format(by=T[by_of(e)], name=nd))
            out.append(T["would_mean"].format(change=change(e["change"], today, names)))
            if len(others) > 1:
                out.append(T["more_suggestions"].format(n=count_words(len(others) - 1)))
        done_props = True
    elif primary == "freed":
        out.append(f"{T['free_head']}: {nd}.")
        m = TASK_REF.search(str((it.get("texts") or {}).get("freed") or ""))
        if m:
            out.append(T["is_done"].format(other=_cap(_name_of(int(m.group(1)), names, own))))
    elif primary == "blocker_gone":
        out.append(f"{T['gone_head']}: {nd}.")
        said = flat((it.get("texts") or {}).get("blocker_gone"), names=names, today=today, own=own)
        if said and not said.endswith("?"):
            out.append(_cap(said))
    elif primary == "steps_done":
        out.append(f"{T['steps_head']}: {nd}.")
    elif primary == "linked_changed":
        out.append(f"{T['changed_head']}: {nd}.")
        since = it.get("since") or {}
        by = [b for b in since.get("by") or [] if isinstance(b, dict)][:2]
        if by:
            did = T.get(f"did_{by[0].get('did')}", T["did_other"])
            who = " and ".join(short(b.get("name")) or T["another_task"] for b in by)
            out.append(f"{_cap(who)} {did if len(by) == 1 else T['did_changed']}.")
        if since.get("text") and not since.get("undone") and not ve.get("recommendation"):      # else the basis says it
            out.append(T["text_updated"])
    elif primary == "snooze_over":
        out.append(f"{T['snooze_head']}: {nd}.")
    elif primary == "slipped":
        out.append(f"{T['slipped_head']}: {nd}.")
    elif primary == "fading":
        anchor = max([str(t.get(k) or "")[:10] for k in ("last_mention_at", "last_reviewed_at")] or [""])
        quiet = dates.say_day(anchor, today, time=False) if anchor else ""
        head = f"{T['fading_head']}: {name}{due or ', ' + T['no_date']}" + (", " + T["quiet_since"].format(day=quiet) if quiet else "") + "."
        out.append(head)
        done_fade = True
        fa = dates.say_day(t.get("fade_at"), today, time=False)
        if fa:
            out.append(T["fades"].format(on=fa if fa in ("today", "tomorrow") else "on " + fa))
    elif primary == "last_call":
        out.append(f"{T['last_call_head']}: {nd}.")
        out.append(T["lapses"])
    elif primary == "back":
        out.append(f"{T['back_head']}: {nd}.")
    else:
        out.append(f"{_cap(nd)}.")

    if sugg and not done_links:                     # the suggested links, in the order of the options
        out += _links_sentences(sugg, names)
    if props and not done_props:
        if others:
            e = others[0]
            out.append(T["suggests_also"].format(by=T[by_of(e) + "_low"], change=change(e["change"], today, names)))
        elif follows:
            out.append(T["mail_also"])
    if "fading" in reasons and not done_fade:
        fa = dates.say_day(t.get("fade_at"), today, time=False)
        if fa:
            out.append(T["fades"].format(on=fa if fa in ("today", "tomorrow") else "on " + fa))
    return out


def _card_sentences(ve: dict, today: date, names: dict) -> list[str]:
    it = ve.get("item") or {}
    f = it.get("fields") or {}
    due = _due(f.get("due"), f.get("hard"), today)
    out = []
    if it.get("kind") == "slack_mention":
        from .review_queue import SLACK_LABEL
        m = SLACK_LABEL.match(one_line(str(it.get("label") or ""))[:400])
        if m:
            where = _quoted(_cut(_scrub(m.group(1), refs="drop"), 4)) or "Slack"
            out.append(T["card_slack"].format(who=sender(m.group(2)), where=where))
        else:
            out.append(T["card_slack_bare"])
        q = _quoted(flat(f.get("name"), words=QUOTE_WORDS, today=today))
        out.append(T["suggested_quote"].format(name=q, due=due) if q else T["suggested_task"].format(name=T["another_task"]))
    else:
        mail = it.get("mail") if isinstance(it.get("mail"), dict) else None
        if mail:
            subj = subject(mail.get("subject"))
            out.append(T["card_mail"].format(who=sender(mail.get("sender")), subject=subj) if subj else T["card_mail_bare"].format(who=sender(mail.get("sender"))))
            out.append(T["suggested_task"].format(name=(short(f.get("name"), names=names) or T["another_task"]) + due))
        else:
            out.append(T["card_ripple" if it.get("by") == "ripple" else "card_planner"].format(name=(short(f.get("name"), names=names) or T["another_task"]) + due))
    for l in (it.get("links") or [])[:1]:
        o = short(l.get("other_name")) or _name_of(l.get("other"), names)
        out.append(T["card_step" if l.get("kind") == "part_of" else "card_waits"].format(other=o))
    return out


def _group_sentences(ve: dict, today: date, names: dict) -> list[str]:
    it = ve.get("item") or {}
    live = [m for m in it.get("members") or [] if m.get("state") != "gone"]
    und = [m for m in live if m.get("state") in ("waiting", "passed")]
    n_dec = len(live) - len(und)
    prov = ve.get("provenance") or it.get("provenance")
    head = _from(prov, {}, today, group=len(live) or 1)
    ts = [m.get("task") or {} for m in und]
    common = ""
    if len(ts) > 1 and ts[0].get("due") and all((x.get("due"), bool(x.get("hard"))) == (ts[0].get("due"), bool(ts[0].get("hard"))) for x in ts):
        common = _due(ts[0]["due"], ts[0].get("hard"), today, lead="")
        common = common.replace(T["due"].format(day=""), (T["group_both_due"] if len(ts) == 2 else T["group_all_due"]).format(day=""), 1)
    if n_dec:
        head += ", " + T["group_decided"].format(n=count_words(n_dec))
    if common:
        head += ", " + common
    out = [head + "."]
    items = [(short(x.get("name"), names=names, own=x.get("id")) or T["another_task"]) + ("" if common else _task_due(x, today)) for x in ts]
    used = _words(out[0])
    i = 0
    while i < len(items):
        chunk = [items[i]]                          # at most three to a sentence, and about SENTENCE_AIM words
        while len(chunk) < 3 and i + len(chunk) < len(items) and _words("; ".join(chunk + [items[i + len(chunk)]])) + 1 <= SENTENCE_AIM:
            chunk.append(items[i + len(chunk)])
        body = "; ".join(chunk[:-1]) + ("; and " if len(chunk) > 1 else "") + chunk[-1]
        lead = (T["group_other_1"] if len(und) == 1 else T["group_other_n"].format(n=count_words(len(und)))) if (i == 0 and n_dec) else T["group_then"] if i else ""
        s = f"{lead}{_cap(body) if not lead else body}."
        if used + _words(s) > MAX_SAY - 4 and i:
            out.append(T["group_more"].format(n=count_words(len(items) - i)))
            break
        out.append(s)
        used += _words(s)
        i += len(chunk)
    return out


def _fit(sentences: list[str], cap: int = MAX_SAY) -> str:
    """The sentences that fit in `cap` words, in order (the first always, cut if it must be). Text others wrote has been
    through `flat`, `subject`, `person` (a forbidden word drops it there); the owner's names and the templates are said
    as they are (review 2026-11-20: a task named "… blocked" was said as nothing)."""
    ss = [s for s in sentences if s]
    out, n = [], 0
    for s in ss:
        w = _words(s)
        if not out and w > cap:
            return _cut(s, cap)
        if n + w > cap:
            break
        out.append(s)
        n += w
    return " ".join(out)


def _option_words(ve: dict) -> list[dict]:
    it = ve.get("item") or {}
    sugg = [s.get("evidence_id") for s in it.get("link_suggestions") or []]
    props = _proposals(it)
    fol = [e["id"] for e in props if e["change"].get("op") == "follow_thread"]
    oth = [e["id"] for e in props if e["change"].get("op") != "follow_thread"]
    out = []
    for o in ve.get("options") or []:
        i = str(o.get("id") or "")
        label = str(o.get("label") or "")
        m = re.fullmatch(r"([a-z_]{1,20}):([0-9]{1,12})", i)
        if m:
            name, e = m.group(1), int(m.group(2))
            fam = fol if name in ("follow", "not_about") else oth if name in ("accept", "dismiss") else sugg
            k = fam.index(e) if e in fam else 0
            key = f"o_{name}_1" if len(fam) <= 1 else f"o_{name}_n"
            w = T.get(key, "").format(nth=dates.ordinal_words(k + 1)) if key in T else _scrub(label, refs="drop").lower()
        elif i == "pin":
            w = T["o_unpin" if label.startswith("★ On Top") else "o_pin"]
        elif i == "longterm":
            w = T["o_not_longterm" if label.lower().startswith("long-term ·") else "o_longterm"]
        elif i == "dismiss" and ve.get("kind") == "card":
            w = T["o_card_dismiss"]
        elif f"o_{i}" in T:
            w = T[f"o_{i}"]
        else:
            w = _scrub(label, refs="drop").lower()
        out.append({"id": i, "words": w})
    return out


def _question(ve: dict, today: date, names: dict) -> str:
    rec = ve.get("recommendation")
    if rec and rec.get("said"):
        return _scrub(rec["said"], names=names, today=today)
    kind = ve.get("kind")
    if kind == "card":
        return T["q_card"]
    if kind == "group":
        return T["q_group"]
    it = ve.get("item") or {}
    if any(s.get("kind") == "same_as" for s in it.get("link_suggestions") or []):
        return T["q_merge"]
    primary = it.get("primary")
    return T.get({"fading": "q_fading", "slipped": "q_slipped", "snooze_over": "q_snooze", "last_call": "q_last_call",
                  "thread_updated": "q_mail", "mentioned": "q_mail", "planner": "q_mail", "ripple": "q_mail", "blocker_gone": "q_gone",
                  "steps_done": "q_steps", "new": "q_new"}.get(primary, "q_task"))


def _at(value, today: date, tz=None) -> str:
    """When a mail or a message came, said where the owner is (`tz`): "yesterday at 10:57 PM". A value with no offset is
    said as written."""
    if tz is not None and isinstance(value, str) and re.search(r"(?:Z|[+-][0-9]{2}:?[0-9]{2})$", value[:40]):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(tz).replace(tzinfo=None)
        except ValueError:
            return ""
    return dates.say_day(value, today)


def _more(ve: dict, today: date, names: dict, tz=None) -> tuple[dict, list[str]]:
    kind, it = ve.get("kind"), ve.get("item") or {}
    rec = ve.get("recommendation") or {}
    others: list[str] = []
    if kind == "group":
        live = [m for m in it.get("members") or [] if m.get("state") != "gone"]
        ids = [(m.get("task") or {}).get("id") for m in live]
        what = T.get("why_" + str((ve.get("provenance") or {}).get("kind")), T["why_recording"])
        return {"why": T["why_group"].format(what=what), "text": "", "links": "", "evidence": "",
                "number": T["number_tasks"].format(list=", ".join(number_words(i) for i in ids[:-1] if type(i) is int) + (" and " if len(ids) > 1 else "")
                                                    + (number_words(ids[-1]) if ids and type(ids[-1]) is int else ""))}, others
    if kind == "card":
        f = it.get("fields") or {}
        why = T["why_card"].format(reason=flat(it.get("reason"), words=MORE_WORDS - 4, names=names, today=today)) if it.get("reason") else ""
        ev = ""
        mail = it.get("mail") if isinstance(it.get("mail"), dict) else None
        if mail:
            ev = T["ev_mail"].format(who=sender(mail.get("sender")), subject=subject(mail.get("subject")), day=_at(mail.get("at"), today, tz) or "")
            ev = ev.replace(", .", ".")
        elif it.get("kind") == "slack_mention":
            ev = T["ev_label"].format(text=flat(it.get("label"), words=QUOTE_WORDS, today=today).rstrip("."))
        links = " ".join(T["card_step" if l.get("kind") == "part_of" else "card_waits"].format(other=short(l.get("other_name")) or _name_of(l.get("other"), names))
                         for l in (it.get("links") or [])[:3])
        others += [k for k, v in (("why", why), ("text", f.get("context")), ("evidence", ev)) if v]
        return {"why": _cut(why, MORE_WORDS), "text": flat(f.get("context"), sentences=TEXT_SENTENCES, words=TEXT_WORDS, names=names, today=today),
                "links": _cut(links, MORE_WORDS), "evidence": _cut(ev, MORE_WORDS), "number": T["number_card"]}, others
    t = it.get("task") or {}
    own = t.get("id")
    rs = [T.get(f"r_{r}", T["r_other"]) for r in it.get("reasons") or []] or [T["r_none"]]
    why = T["why_head"].format(reasons=", and ".join(rs[:3]) if len(rs) <= 2 else ", ".join(rs[:2]) + ", and " + rs[2])
    if rec.get("basis"):
        why += " " + flat(rec["basis"], sentences=2, words=MORE_WORDS, names=names, today=today, own=own)
        if str(rec.get("source") or "").startswith("proposal:"):
            others.append("why")
    elif ve.get("rec_held"):
        why += " " + flat(ve["rec_held"], names=names, today=today, own=own)
    phr = []
    for l in ((t.get("rel") or {}).get("links") or [])[:3]:
        o = _name_of(l.get("other"), names, own)
        out = l.get("dir") != "in"
        phr.append(T[("link_step_of" if out else "link_has_step") if l.get("kind") == "part_of" else ("link_waits" if out else "link_frees")].format(other=o if out else _cap(o)))
    ev = []
    for l in it.get("evidence_lines") or []:
        if l.get("kind") == "mail":
            when = _at(l.get("at"), today, tz)
            ev.append(_cap(_mail_line(l)) + (f", {when}." if when else "."))
        else:
            ev.append(_slack_line(l, today, "ev_slack"))
    for e in _proposals(it)[:2]:
        if e.get("reason"):
            ev.append(T["ev_reason"].format(by=T[("ripple" if e.get("by") == "ripple" else "planner") + "_low"],
                                            reason=flat(e.get("reason"), words=20, names=names, today=today, own=own)))
    if ev:
        others.append("evidence")
    return {"why": _fit([why], MORE_WORDS), "text": flat(t.get("context"), sentences=TEXT_SENTENCES, words=TEXT_WORDS, names=names, today=today, own=own),
            "links": _fit(phr, MORE_WORDS) if phr else T["no_links"], "evidence": _fit(ev, MORE_WORDS) if ev else T["no_evidence"],
            "number": T["number_task"].format(n=number_words(own)) if type(own) is int else ""}, others


def _members(ve: dict, names: dict | None = None) -> list[dict]:
    live = [m for m in (ve.get("item") or {}).get("members") or [] if m.get("state") != "gone"]
    return [{"key": m.get("key"), "words": T["member"].format(nth=dates.ordinal_words(i + 1),
                                                               name=_lcfirst(short((m.get("task") or {}).get("name"), names=names, own=(m.get("task") or {}).get("id")) or T["another_task"]))}
            for i, m in enumerate(live)]


def entry(view_entry: dict, today: date, names: dict | None = None, *, tz=None) -> dict:
    """The View's entry (a task item, a suggestion card or a group, as `review_pass` builds it) as words for the ear:
    {say, question, basis, more: {why, text, links, evidence, number}, options: [{id, words}], members: [{key, words}]
    (a group's), from_others: "say" when it holds a sender, a subject or Slack text, and the parts of `more` that hold
    words others wrote}. `say` is at most MAX_SAY words: why it
    is here, its short name and the fact that decides it; `basis` (the recommendation's `basis_said`) and `question`
    (its `said`, else the usual choices) are never cut. Pure."""
    ve = view_entry or {}
    names = names or {}
    kind = ve.get("kind")
    rec = ve.get("recommendation") or None
    it = ve.get("item") or {}
    if ve.get("settled"):
        nm = short((it.get("task") or {}).get("name") or (it.get("fields") or {}).get("name") or it.get("name") or it.get("label")) or T["this_task"]
        more, others = _more(ve, today, names, tz) if kind != "group" else ({"why": "", "text": "", "links": "", "evidence": "", "number": ""}, [])
        return {"say": _fit([T["settled"].format(name=nm), _cap(flat(ve["settled"]))]), "question": "", "basis": "", "more": more,
                "options": _option_words(ve), "members": [], "from_others": others}
    if kind == "group":
        ss = _group_sentences(ve, today, names)
    elif kind == "card":
        ss = _card_sentences(ve, today, names)
    else:
        ss = _task_sentences(ve, today, names)
    question = _question(ve, today, names)
    st = ve.get("state")
    d = (ve.get("staged") or {}).get("decision")
    if st == "decided" and d:
        op = d.get("op")
        what = T.get({"keep": "staged_keep", "done": "staged_done", "drop": "staged_drop", "set": "staged_later", "judge_evidence": "staged_dismiss",
                      "create_suggestion": "staged_create"}.get(op, "staged_other"))
        ss = ss[:1] + [T["staged"].format(what=what)]
        question = T["q_decided"]
    elif st == "answered":
        ss = ss[:1] + [T["answered"]]
        question = ""
    if rec:
        basis = _scrub(rec.get("basis_said") or "", names=names, today=today, own=(it.get("task") or {}).get("id"))
    else:
        basis = _scrub(ve.get("rec_held") or "", names=names, today=today) if kind == "task" else ""
    more, others = _more(ve, today, names, tz)
    if _says_others(ve):
        others = ["say"] + others
    return {"say": _fit(ss), "question": question, "basis": basis if not FORBIDDEN.search(basis) else "", "more": more,
            "options": _option_words(ve), "members": _members(ve, names) if kind == "group" else [], "from_others": others}


def wants(view_entry: dict) -> set[int]:
    """The task ids whose names the words of this entry may say (a `Task-N` in its texts, its links, its proposals),
    for the caller to read once (`review_pass`). Pure."""
    ve = view_entry or {}
    it = ve.get("item") or {}
    out: set[int] = set()
    blobs = [it.get("reason"), (it.get("fields") or {}).get("context"), ve.get("rec_held")]
    t = it.get("task") or {}
    blobs += [t.get("name"), t.get("context"), (it.get("fields") or {}).get("name"), *(it.get("texts") or {}).values()]
    blobs += [(m.get("task") or {}).get("name") for m in it.get("members") or []]
    for e in it.get("evidence") or []:
        blobs.append(e.get("reason"))
        f = (e.get("change") or {}).get("fields") or {}
        for k in ("other", "into"):
            if type(f.get(k)) is int:
                out.add(f[k])
    for s in it.get("link_suggestions") or []:
        blobs.append(s.get("why"))
        if type(s.get("other")) is int:
            out.add(s["other"])
    for l in (t.get("rel") or {}).get("links") or []:
        if type(l.get("other")) is int:
            out.add(l["other"])
    rec = ve.get("recommendation") or {}
    blobs += [rec.get("basis"), rec.get("basis_said")]
    for b in blobs:
        out.update(int(n) for n in TASK_REF.findall(str(b or "")[:INPUT_CAP]))
    return out
