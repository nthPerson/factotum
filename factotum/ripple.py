"""Ripples (design §8): when a linked task is closed, materially changed or merged, one light Claude
run with no tools brings the tasks linked to it up to date. Python decides when: `batches.apply`
queues each such task in `ripple_queue` (never for a ripple's own batch), the scheduler runs a group
after the quiet period or at once for an outcome. A ripple only rewrites a neighbour's text (guarded,
undoable with its cause); everything else it suggests waits on Review as a card a person accepts.
"""
from __future__ import annotations

import bisect
import functools
import html
import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from . import batches
from . import config as C
from . import relations as R
from .db import get_task, log_run, now_iso, rows
from .planner import _same_suggestion
from .sync_file import _clean_task_fields

RUN_KIND = "ripple"                 # the runs row's kind, the batch source and the prompt's run kind
ACCOUNT = "ripple"                  # evidence.account of a card a ripple staged
PROMPT = C.ROOT / "prompts" / "ripple.md"
RUNS_DIR = C.ROOT / "data" / "runs" / "ripple"

WHAT_ORDER = ("closed", "merge", "name", "text", "due", "hard", "outcome")
CHANGED_CAP = 5                     # changed tasks per run; the rest stay queued
NEIGHBOUR_CAP = 12                  # linked tasks per run: open only, direct links first, then other steps of the same goal
TEXT_CAP = 1500                     # characters of each text in the prompt
REWRITE_CAP = 2000                  # characters of a rewrite
PROPOSAL_CAP = 6                    # cards per run
MAX_ATTEMPTS = 2                    # a group that fails this often is set aside and logged
MAX_AGE = timedelta(hours=24)       # a row queued longer ago than this is set aside unrun

_WAKER: Callable[[], None] | None = None


def _load(v):
    try:
        return json.loads(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _what(cur, batch_id: int) -> dict[int, set[str]]:
    """What this batch did that ripples, by task id, read from its own `changes` rows."""
    rows = cur.execute("SELECT task_id, field, old, new FROM changes WHERE batch_id = ? ORDER BY id", (batch_id,)).fetchall()
    merged = {r["task_id"] for r in rows if r["field"] == "closed_by" and _load(r["new"]) == "merge"}
    reopened = {r["task_id"] for r in rows if r["field"] == "status" and _load(r["new"]) == "open"}     # a reopen clears the outcome and never ripples
    out: dict[int, set[str]] = {}
    for r in rows:
        f, old, new = r["field"], _load(r["old"]), _load(r["new"])
        if f == "status":
            tag = "closed" if old == "open" and new in ("done", "dropped") and r["task_id"] not in merged else None
        elif f in ("name", "due", "hard", "outcome"):
            tag = None if f == "outcome" and r["task_id"] in reopened else f
        elif f == "context":
            tag = "text" if batches._norm_text(old) != batches._norm_text(new) else None
        elif f == "link":
            tag = "merge" if old is None and isinstance(new, dict) and new.get("kind") == "same_as" and new.get("dir") == "in" else None
        else:
            tag = None
        if tag:
            out.setdefault(r["task_id"], set()).add(tag)
    return out


def _ordered(what) -> list[str]:
    return [w for w in WHAT_ORDER if w in what]


def enqueue(cur, batch_id: int, source: str) -> dict[int, list[str]]:
    """Queue every task this batch closed, renamed, re-texted, re-dated, gave an outcome or merged
    into, when it has at least one active link after the batch; one row per task, merged with what
    is already waiting. Runs on the batch's own cursor, inside its transaction. Returns task id →
    what for this batch; nothing for a ripple's own batch (a ripple never triggers a ripple)."""
    if source == RUN_KIND:
        return {}
    queued: dict[int, list[str]] = {}
    for tid, what in _what(cur, batch_id).items():
        if not cur.execute("SELECT 1 FROM relations WHERE (src = ? OR dst = ?) AND removed_at IS NULL LIMIT 1", (tid, tid)).fetchone():
            continue
        now = 1 if "outcome" in what else 0
        row = cur.execute("SELECT batches_json, what_json, now FROM ripple_queue WHERE task_id = ?", (tid,)).fetchone()
        if row:
            ids = (_load(row["batches_json"]) or []) + [batch_id]
            merged = _ordered(set(_load(row["what_json"]) or []) | what)
            cur.execute("UPDATE ripple_queue SET queued_at = ?, batches_json = ?, last_batch_id = ?, what_json = ?, now = ?, attempts = 0 WHERE task_id = ?",
                        (now_iso(), json.dumps(ids), batch_id, json.dumps(merged), max(row["now"], now), tid))
        else:
            cur.execute("INSERT INTO ripple_queue (task_id, queued_at, batches_json, last_batch_id, what_json, now) VALUES (?, ?, ?, ?, ?, ?)",
                        (tid, now_iso(), json.dumps([batch_id]), batch_id, json.dumps(_ordered(what)), now))
        queued[tid] = _ordered(what)
    return queued


def _what_of(cur, task_id: int, batch_ids: list[int]) -> set[str]:
    """What these batches did to one task that ripples (`_what` over each)."""
    out: set[str] = set()
    for bid in batch_ids:
        out |= _what(cur, bid).get(task_id, set())
    return out


def unqueue(cur, batch_id: int) -> list[int]:
    """Take an undone batch out of the queue, in the undo's own transaction: a row that held it keeps
    only its other batches, its `what` and `now` recomputed from them and its `last_batch_id` the
    newest left (so a run in flight that read it does not delete it, and the rest ripples again); a
    row left with no batch, or with nothing that ripples, is deleted. Returns those rows' task ids."""
    gone: list[int] = []
    for r in cur.execute("SELECT task_id, batches_json FROM ripple_queue ORDER BY task_id").fetchall():
        ids = _load(r["batches_json"]) or []
        if batch_id not in ids:
            continue
        left = [i for i in ids if i != batch_id]
        what = _what_of(cur, r["task_id"], left)
        if not left or not what:
            cur.execute("DELETE FROM ripple_queue WHERE task_id = ?", (r["task_id"],))
            gone.append(r["task_id"])
        else:
            cur.execute("UPDATE ripple_queue SET batches_json = ?, last_batch_id = ?, what_json = ?, now = ? WHERE task_id = ?",
                        (json.dumps(left), max(left), json.dumps(_ordered(what)), 1 if "outcome" in what else 0, r["task_id"]))
    return gone


def pending(conn: sqlite3.Connection) -> list[dict]:
    """The queued rows, `batches` and `what` parsed, oldest `queued_at` first."""
    out = []
    for r in conn.execute("SELECT * FROM ripple_queue ORDER BY queued_at, task_id").fetchall():
        d = dict(r)
        d["batches"] = _load(d.pop("batches_json")) or []
        d["what"] = _load(d.pop("what_json")) or []
        out.append(d)
    return out


def set_waker(fn: Callable[[], None] | None) -> None:
    """The scheduler's wake (set by `Scheduler.start`, cleared by `stop`)."""
    global _WAKER
    _WAKER = fn


def poke() -> None:
    """Wake the scheduler when one runs in this process; never raises (the batch is already written)."""
    fn = _WAKER
    if fn is None:
        return
    try:
        fn()
    except Exception:
        pass


# --- the run's input (design §8.2) -------------------------------------------------------------

BEFORE_FIELDS = ("name", "context", "due", "hard", "outcome", "status")


def _material_batches(conn: sqlite3.Connection, tid: int) -> tuple[list[int], list[str]]:
    """A task with no queue row (asked for from the CLI): its newest non-ripple, not undone batch
    that changed it by `enqueue`'s rule, as ([batch id], what); ([], ["asked"]) when there is none."""
    for r in conn.execute("SELECT DISTINCT b.id FROM batches b JOIN changes c ON c.batch_id = b.id "
                          "WHERE c.task_id = ? AND b.source != ? AND b.undone_at IS NULL ORDER BY b.id DESC", (tid, RUN_KIND)).fetchall():
        what = _what(conn, r["id"]).get(tid)
        if what:
            return [r["id"]], _ordered(what)
    return [], ["asked"]


def _changed(conn: sqlite3.Connection, t: dict, batch_ids: list[int], what: list[str]) -> dict:
    """One changed task: the current values ("after"), the first `old` of each field in its batches
    ("before"), and the duplicates those batches merged into it."""
    before: dict[str, Any] = {}
    merged: list[int] = []
    if batch_ids:
        marks = ",".join("?" * len(batch_ids))
        for r in conn.execute(f"SELECT field, old, new FROM changes WHERE task_id = ? AND batch_id IN ({marks}) ORDER BY batch_id, id",
                              (t["id"], *batch_ids)).fetchall():
            if r["field"] in BEFORE_FIELDS and r["field"] not in before:
                before[r["field"]] = _load(r["old"])
            elif r["field"] == "link" and r["old"] is None:
                new = _load(r["new"])
                if isinstance(new, dict) and new.get("kind") == "same_as" and new.get("dir") == "in" and new.get("other") not in merged:
                    merged.append(new.get("other"))
    return {"id": t["id"], "name": t["name"], "status": t["status"], "outcome": t.get("outcome"), "due": t["due"], "hard": t["hard"],
            "context": t["context"], "what": what, "before": before, "merged": merged}


NET_FIELD = {"closed": "status", "name": "name", "text": "context", "due": "due", "hard": "hard", "outcome": "outcome"}


def _net(conn: sqlite3.Connection, c: dict) -> dict | None:
    """A queued task as it stands now: a field whose value is back to its "before" (closed, then
    reopened; renamed and renamed back) is no change, a duplicate whose `same_as` is gone (reopened)
    is no longer merged into it, and `what` keeps only what still holds. None when nothing does."""
    before = {f: v for f, v in c["before"].items() if not batches._same_value(v, c.get(f))}
    merged = [m for m in c["merged"] if conn.execute("SELECT 1 FROM relations WHERE src = ? AND dst = ? AND kind = 'same_as' "
                                                     "AND removed_at IS NULL", (m, c["id"])).fetchone()]
    what = [w for w in c["what"] if (merged if w == "merge" else NET_FIELD.get(w) in before)]
    if not what:
        return None
    return dict(c, before=before, merged=merged, what=what)


def _neighbour_links(conn: sqlite3.Connection, changed: list[int]) -> list[tuple[int, str]]:
    """(task id, how it is linked, from its side) for every task one link away from a changed task,
    in relation order, then the other steps of each changed task's goal."""
    out: list[tuple[int, str]] = []
    goals: list[int] = []
    for c in changed:
        for r in conn.execute("SELECT src, dst, kind FROM relations WHERE (src = ? OR dst = ?) AND removed_at IS NULL ORDER BY id", (c, c)).fetchall():
            if r["kind"] == "same_as":
                out.append((r["dst"] if r["src"] == c else r["src"], f"same task as Task-{c}"))
            elif r["src"] == c:
                out.append((r["dst"], f"Task-{c} waits on it" if r["kind"] == "waits_on" else f"the goal of Task-{c}"))
                if r["kind"] == "part_of":
                    goals.append(r["dst"])
            else:
                out.append((r["src"], f"waits on Task-{c}" if r["kind"] == "waits_on" else f"a step of Task-{c}"))
    for g in goals:
        for r in conn.execute("SELECT src FROM relations WHERE dst = ? AND kind = 'part_of' AND removed_at IS NULL ORDER BY id", (g,)).fetchall():
            out.append((r["src"], f"another step of Task-{g}"))
    return out


def bundle(conn: sqlite3.Connection, cfg: dict, task_ids: list[int], today: date) -> dict:
    """What one ripple run reads (design §8.2), capped: each changed task before and after, and each
    open task one link away from one, then the other steps of the same goal (`NEIGHBOUR_CAP` in
    all, each once, never a changed task). `texts` keeps each neighbour's whole text as it was read,
    the `expect` of its rewrite; `queue` the `last_batch_id` the run may delete a queue row for.
    A queued task's `what` is read again from its batches that are not undone, and a task with
    nothing left (every batch undone, or each change put back: `_net`) is not a changed task at all;
    its row stays in `queue`, so the run deletes it."""
    rows = {r["task_id"]: r for r in pending(conn)}
    changed: list[dict] = []
    queue: dict[int, int] = {}
    causes: set[int] = set()
    for tid in task_ids:
        t = get_task(conn, tid)
        if tid in rows:
            queue[tid] = rows[tid]["last_batch_id"]
        if not t:
            continue
        if tid in rows:
            q = rows[tid]
            marks = ",".join("?" * len(q["batches"]))
            ids = [r["id"] for r in conn.execute(f"SELECT id FROM batches WHERE id IN ({marks}) AND undone_at IS NULL ORDER BY id",
                                                 tuple(q["batches"])).fetchall()] if q["batches"] else []
            c = _net(conn, _changed(conn, t, ids, _ordered(_what_of(conn, tid, ids))))
            if c is None:
                continue
        else:
            ids, what = _material_batches(conn, tid)       # asked for from the CLI: as asked
            c = _changed(conn, t, ids, what)
        causes.update(ids)
        changed.append(c)
    from_ids = [c["id"] for c in changed]
    neighbours: list[dict] = []
    texts: dict[int, str] = {}
    for nid, via in _neighbour_links(conn, from_ids):
        if len(neighbours) >= NEIGHBOUR_CAP:
            break
        if nid in from_ids or nid in texts:
            continue
        n = get_task(conn, nid)
        if not n or n["status"] != "open":
            continue
        waits = [r["dst"] for r in conn.execute("SELECT r.dst FROM relations r JOIN tasks t ON t.id = r.dst WHERE r.src = ? AND r.kind = 'waits_on' "
                                                "AND r.removed_at IS NULL AND t.status = 'open' ORDER BY r.id", (nid,)).fetchall()]
        texts[nid] = n["context"] or ""
        neighbours.append({"id": nid, "name": n["name"], "context": texts[nid][:TEXT_CAP], "cut": _cut(texts[nid]), "due": n["due"], "hard": n["hard"],
                           "waiting_on": n["waiting_on"], "waits_on": waits, "via": via})
    return {"changed": changed, "neighbours": neighbours, "texts": texts, "ids": from_ids + [n["id"] for n in neighbours],
            "from": from_ids, "queue": queue, "batches": sorted(causes), "what": {str(c["id"]): c["what"] for c in changed}}


# A neighbour's text longer than TEXT_CAP is shown cut, so a rewrite would be built from part of it and
# replace all of it: such a text is never rewritten; its ripple becomes the dated note (apply).
TOO_LONG = "the text is too long to rewrite safely, so a note was added"


def _cut(text: str | None) -> bool:
    """True when the prompt shows only the first TEXT_CAP characters of this text."""
    return len(text or "") > TEXT_CAP


LINE_CAP = 300                      # characters of a name, an outcome or a waiting_on in the prompt
FIELD_CAP = {"context": TEXT_CAP}   # any other field's value: LINE_CAP
# What JSON leaves raw with ensure_ascii=False and a reader may still take for a line break; and < and >,
# written as JSON escapes so no value ever reads as a tag, which a JSON reader turns back into the same
# characters (never HTML entities: a faithful rewrite would write "&amp;" into the owner's text).
_BREAKS = str.maketrans({"\u2028": "\\u2028", "\u2029": "\\u2029", "\x85": "\\u0085", "<": "\\u003c", ">": "\\u003e"})


def _esc(v) -> str:
    return html.escape(str(v or ""), quote=False)


def _q(v, cap: int = LINE_CAP) -> str:
    """A value a task holds, as the prompt shows it: cut to `cap`, then ONE JSON string, so no line break
    it holds ever starts a line of the prompt (a stranger's "# 5. Return" stays inside quotes). Only JSON
    escapes: what a JSON reader gets back is exactly what the task holds, so a rewrite that keeps the
    owner's words keeps them character for character."""
    return json.dumps(str(v)[:cap], ensure_ascii=False).translate(_BREAKS)


def _shown(field: str, v) -> str:
    """A before or after value in the prompt: a JSON string cut to its field's cap, an empty value said."""
    if field == "hard":
        return "hard" if v else "not hard"
    if v is None or v == "":
        return "(none)"
    return _q(v, FIELD_CAP.get(field, LINE_CAP))


def _changed_text(c: dict) -> str:
    lines = [f"Task-{c['id']} | {_esc(c['status'])} | {_q(c['name'])}"]
    for f, old in c["before"].items():
        lines.append(f"  {f}: {_shown(f, old)} → {_shown(f, c.get(f))}")
    if c.get("outcome"):
        lines.append(f"  outcome: {_q(c['outcome'])}")
    lines += [f"  merged into it: Task-{m}" for m in c["merged"]]
    return "\n".join(lines)


def _neighbour_text(n: dict) -> str:
    due = f"due {_q(n['due'], 20)}{' hard' if n['hard'] else ''}" if n["due"] else "undated"
    waits = "waits on " + (", ".join(f"Task-{w}" for w in n["waits_on"]) or "no task")
    if n["waiting_on"]:
        waits += f"; on {_q(n['waiting_on'])}"
    text = _q(n["context"], TEXT_CAP) if n["context"] else "(none)"
    cut = (f"\n  (cut: only its first {TEXT_CAP} characters are shown, so this text is never rewritten: "
           "an effect `text` adds your `why` to it as a dated note)") if n.get("cut") else ""
    return f"Task-{n['id']} | {n['via']} | {due} | {waits} | {_q(n['name'])}\n  text: {text}{cut}"


def build_prompt(cfg: dict, b: dict, today: date, clock: str = "") -> str:
    """`prompts/ripple.md` filled from a bundle; everything a task says goes in escaped."""
    fill = {"now": today.strftime("%A %Y-%m-%d") + (f" · it is {clock}" if clock else ""),
            "changed": "\n\n".join(_changed_text(c) for c in b["changed"]) or "(nothing)",
            "neighbours": "\n\n".join(_neighbour_text(n) for n in b["neighbours"]) or "(none)",
            "rewrite_cap": str(REWRITE_CAP)}
    # one pass, so a task's text that says "{{neighbours}}" is never filled in itself
    return re.sub(r"\{\{(\w+)\}\}", lambda m: fill.get(m.group(1), m.group(0)), PROMPT.read_text(encoding="utf-8"))


# --- the reply (design §8.3, §8.6) ---------------------------------------------------------------

EFFECTS = ("none", "text", "propose")
WHY_CAP = 300
# Bounded patterns: the old text is a task's text and may hold a stranger's words.
TASK_REF_RE = re.compile(r"\bTask-(\d{1,6})\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}")
SET_FIELDS = ("name", "due", "hard", "effort", "project")     # what a ripple's `set` or `create` card may carry
NEVER_FIELDS = ("counterpart", "waiting_on")                  # a ripple never copies an address or a person (design §8.6)
# A web address a ripple may not bring to a task on its own, nor drop (design §8.6). The rule is "flag
# unless it is plainly not one": over-flagging costs a Review card where the owner sees before and after,
# under-flagging writes a stranger's address into the owner's text unseen. `_web_spans` looks at every
# place in a text where a host could start, and a host never swallows what follows it, so nothing rides
# unseen in another token's path, user-info or tail. An address is
#   - anything after a scheme (http, https, ftp, the de-fanged hxxp);
#   - a host: labels joined by full stops (also 。．｡ and one-dot leaders, [.] (.) {.} [dot] (dot), and
#     invisible characters beside them) whose last label is letters, two or more, and no file extension (report.pdf, Node.js:
#     no such host exists), or an IPv4 address. A host with any strong sign is an address: a port, a path,
#     query or fragment, www., a non-ASCII letter, a de-fanged dot, or anything else in its whitespace-
#     separated token but brackets, quotes, sentence punctuation, a call's "(x)" and "'s" (@evil.com,
#     evil.com@, see:evil.com, notes.txt/evil.com). Only a bare one (the whole token) is checked against
#     the closed lists: its last label must be a known TLD (COMMON_TLDS) and the token no degree (M.Sc);
#   - "name dot com" with a known TLD or a path.
# Not one: e.g., a.m., U.S., 3.5, v2.1, 9.30, Dr. Lee (a space), report.pdf, README.md, user.email.
# Bounded like every pattern here.
_INV = "[\u00ad\u200b-\u200f\u2060-\u2064\ufeff]"           # a soft hyphen, zero-width spaces and joiners, marks
# No bound a stranger can exceed: a label, the number of labels, a path, a port, a spaced run are as long
# as they are (a cut match would be judged on part of an address). Still linear: a label run starts only
# where a run of label characters starts, and nothing else here can match two ways.
_DOT = r"(?:[.\u3002\uff0e\uff61\u2024\ufe52]|\[\.\]|\(\.\)|\{\.\}|\[dot\]|\(dot\))"
_LABEL = r"(?:[\w-]|" + _INV + r")+"                          # with any invisible characters inside or beside it
_START = r"(?<![\w-])(?<!" + _INV + r")"
_TAIL = r"(?:\[\.\]|\(\.\)|[^\s<>\"')\]])*"
SCHEME_RE = re.compile(r"(?:h(?:tt|xx)ps?|ftp)(?::|\[:\])//(?:\[\.\]|\(\.\)|[^\s<>\"')\]])+", re.I)
HOST_RE = re.compile(_START + r"(?P<host>" + _LABEL + r"(?:" + _DOT + _LABEL + r")+)"
                     r"(?=(?P<port>:\d+(?![\w.]))?(?P<path>[/?#])?)", re.I)    # a lookahead: the scan resumes after the host
SPACED_RE = re.compile(_START + _LABEL + r"(?:[ \t]+dot[ \t]+" + _LABEL + r")+(?P<path>/" + _TAIL + r")?", re.I)
_FANG = re.compile(_INV + r"|\[\.\]|\(\.\)|\{\.\}|\[dot\]|\(dot\)|[\u3002\uff0e\uff61\u2024\ufe52]", re.I)
_WRAP_L, _WRAP_R = "([{<\"'\u201c\u2018\u00ab", ")]}>\"'\u201d\u2019\u00bb.,;:!?"
_BARE_TAIL = re.compile(r"(?:\(\w{0,20}\)?|['\u2019]s)?")   # what may follow a bare host in its token: m.group(1), Dana.Lee's
# Country codes and generic TLDs, a few hundred. Left out: the ones that are much more often a file name
# or a name in code than a bare address (md py rs sh pl cc ps so am pm id al: README.md, setup.py, row.id,
# et.al), and generic ones that are common words after a full stop in code or config (name email int post
# group run fit show today date help menu …: user.name, user.email, requests.post, m.group, date.today).
# With a scheme, www., a port or a path they are still addresses.
COMMON_TLDS = frozenset("""
ac ad ae af ag ai ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bm bn bo br bs bt bw by bz ca cd cf cg
ch ci ck cl cm cn co cr cu cv cw cx cy cz de dj dk dm do dz ec ee eg er es et eu fi fj fk fm fo fr ga gd ge gf gg
gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu ie il im in io iq ir is it je jm jo jp ke kg kh ki km kn
kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc me mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my mz na
nc ne nf ng ni nl no np nr nu nz om pa pe pf pg ph pk pn pr pt pw qa re ro ru rw sa sb sc sd se sg si sk sl sm sn
sr ss st su sv sx sy sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug uk us uy uz va vc ve vg vi vn vu wf
ws ye yt za zm zw
com net org edu gov mil biz info pro mobi asia tel travel jobs museum aero coop cat xxx
app dev page xyz top site online shop store tech cloud live life world news blog link click space website club
win bid vip icu fun art design digital network systems solutions services agency company media studio center zip
mov foo dad phd prof esq nexus meme boo rsvp academy africa amsterdam bar beer berlin bet bio boston buzz cafe cam
camp capital cards care cash casino chat city codes coffee community consulting cool dating deals download earth
energy events expert express family fans farm fashion film finance fitness football foundation fund games gallery
garden gay gift gifts global gold golf graphics guide guru health host hosting house inc ink institute
international investments kim land law lawyer legal lgbt lol london love ltd luxury market marketing mba memorial
miami moe mom money monster movie ngo ninja nyc onl ooo organic paris partners party photo photography photos pics
pictures pizza poker porn press productions promo properties property pub racing radio realestate realty recipes
rent rentals repair reviews rip rocks sale salon school science security sex sexy shoes shopping singles ski
soccer social software solar stream study style sucks support surf tattoo tax taxi team tennis theater tokyo tours
town toys trade trading training tube university uno vegas ventures vet video vision vodka vote voting voyage
wales wang webcam wedding wiki wine works wtf yoga zone bond cfd sbs cyou quest beauty hair skin makeup autos boats
homes motorcycles yachts llc loan loans accountant cricket faith lat
""".split())
# File extensions that are no TLD: such a last label is never an address, path or not (Node.js/React).
FILE_EXTS = frozenset("js mjs cjs jsx ts tsx pdf txt doc docx xls xlsx ppt pptx csv tsv json yaml yml toml ini cfg conf log "
                      "html htm css scss xml svg png jpg jpeg gif webp bmp tif tiff heic wav flac ogg webm mkv avi tex bib "
                      "ipynb java kt cpp hpp rb php swift lua sql db sqlite bak tmp gz tgz tar rar dmg exe dll apk iso odt ods "
                      "rtf epub psd".split())
# Degrees written like a host with a known TLD: a bare one is not an address (M.Sc, B.Tech, B.Com).
DEGREES = frozenset("b.sc m.sc b.tech m.tech b.com m.com b.arch m.arch b.ed m.ed b.eng m.eng b.des m.des b.pharm m.pharm "
                    "m.phil d.phil b.mus m.mus".split())
_SPACED_TLDS = frozenset("com net org edu gov io".split())    # "labdata dot com", but never "put a dot in the box"
LEFT_OUT = "[address left out]"
WITHHELD = "its reason named an address this task did not have, so it is left out"    # a note's fixed words then
NAME_MAX = 80                                                 # a task's name, as `mcp_server.NAME_MAX` and the cleaner's cut
SHAPE = 'the reply is not {"ripples": [...]}'


def _short(v) -> str:
    """A value the model wrote, as a log line names it: a string as itself, anything else as its repr, cut."""
    r = " ".join(v.split()) if isinstance(v, str) else repr(v)
    return r if len(r) <= 40 else r[:37] + "..."


class ReplyError(ValueError):
    """A reply of the wrong shape; the whole run is refused and nothing is applied."""


def parse(data, b: dict) -> list[dict]:
    """The reply `{"ripples": [entry, ...]}` checked whole against the bundle it answers: one entry per
    linked task at most, each `{task_id, effect, context, why, proposals}`. Returns the entries
    normalised (`context` None unless the effect is `text`, `why` one line of at most `WHY_CAP`,
    `proposals` a list). Raises `ReplyError` naming the first thing wrong."""
    if not isinstance(data, dict) or not isinstance(data.get("ripples"), list):
        raise ReplyError(SHAPE)
    out: list[dict] = []
    seen: set[int] = set()
    for i, e in enumerate(data["ripples"]):
        if not isinstance(e, dict):
            raise ReplyError(f"entry {i} is not an object")
        tid = e.get("task_id")
        if type(tid) is not int or tid not in b["texts"]:
            raise ReplyError(f"entry {i}: task_id {_short(tid)} is not one of the linked tasks")
        if tid in seen:
            raise ReplyError(f"Task-{tid} has two entries")
        seen.add(tid)
        effect = e.get("effect")
        if effect not in EFFECTS:
            raise ReplyError(f"Task-{tid}: effect {_short(effect)} is not none, text or propose")
        if effect == "text" and not isinstance(e.get("context"), str):
            raise ReplyError(f"Task-{tid}: effect text without a context string")
        why = e.get("why", "")
        if not isinstance(why, str):
            raise ReplyError(f"Task-{tid}: why is not a string")
        props = e.get("proposals", [])
        if not isinstance(props, list) or not all(isinstance(p, dict) for p in props):
            raise ReplyError(f"Task-{tid}: proposals is not a list of objects")
        out.append({"task_id": tid, "effect": effect, "context": e["context"] if effect == "text" else None,
                    "why": " ".join(why.split())[:WHY_CAP], "proposals": props})
    return out


def _emails(text: str) -> list[str]:
    return list(dict.fromkeys(m.lower() for m in EMAIL_RE.findall(text or "")))


def _key(tok: str) -> str:
    """A web address as it is compared: no trailing punctuation, no http(s) or ftp scheme, no trailing
    slash, the scheme and the host (with its port) lowercased, and everything else exactly as written:
    a path, query or fragment is case-sensitive, and a percent-escape, a de-fanged dot, an invisible
    character, a default port or a www. makes a different address."""
    t = re.sub(r"^(?:https?|ftp)://", "", tok.rstrip(".,;:!?"), flags=re.I)
    h = re.match(r"(?:[a-z]{1,8}(?::|\[:\])//)?[^/?#]*", t, re.I).end()     # a de-fanged scheme left (hxxp://) and the host
    return (t[:h].lower() + t[h:]).rstrip("/")


def _plain(text: str) -> str:
    """`text` lowercased with every de-fanged or full-width dot written plainly and every invisible
    character gone: the host shape it stands for. Only for telling whether a token is an address,
    never for comparing two."""
    return _FANG.sub(lambda m: "" if re.fullmatch(_INV, m.group(0)) else ".", text or "").lower()


def _tail_ends(text: str) -> list[int]:
    """For each place in `text`, where a path starting there ends (`_TAIL`'s stop characters; a ) or ]
    closing a de-fanged (.) or [.] does not stop it): one pass, so a long path holding many hosts costs
    no more than a short one."""
    ends = [len(text)] * (len(text) + 1)
    for i in range(len(text) - 1, -1, -1):
        c = text[i]
        stop = c.isspace() or c in "<>\"'" or (c in ")]" and text[max(0, i - 2):i + 1] not in ("[.]", "(.)"))
        ends[i] = i if stop else ends[i + 1]
    return ends


def _host_is_address(text: str, m: re.Match, tokens: list[tuple[int, int]], path: str) -> bool:
    """Whether one `HOST_RE` match of `text` is a web address (the rules above `_INV`); `tokens`: the
    spans of `text`'s whitespace-separated tokens; `path`: the path, query or fragment after it."""
    raw = m.group("host")
    labels = _plain(raw).split(".")
    last = labels[-1]
    ipv4 = len(labels) == 4 and all(lb.isdigit() for lb in labels)         # any four numbers: 185.12.4.999 too
    if not ipv4 and (not (last.isalpha() and len(last) >= 2) or last in FILE_EXTS):
        return False                                         # 3.5, v2.1, 9.30, e.g, a.m, report.pdf, Node.js: no host
    if (ipv4 or m.group("port") or path.rstrip(".,;:!?") or labels[0] == "www" or _plain(raw) != raw.lower()
            or any(ord(c) > 127 for c in raw if c.isalpha())):
        return True                                          # a strong sign ("Is it St.Louis?" has no query)
    i = bisect.bisect_right(tokens, (m.start(), len(text))) - 1          # the whitespace-separated token holding the host
    token = text[tokens[i][0]:tokens[i][1]].lstrip(_WRAP_L).rstrip(_WRAP_R)
    if not (token.startswith(raw) and _BARE_TAIL.fullmatch(token[len(raw):]) is not None):
        return True                                          # not bare: something is glued to it
    return last in COMMON_TLDS and _plain(raw) not in DEGREES


def _web_spans(text: str) -> list[tuple[int, int, str]]:
    """Every web address in `text`: (start, end without trailing punctuation, `_key`), by start, an outer
    one before what lies inside it. A host lying wholly inside an email address of the same text is the
    email rule's (`EMAIL_RE`); nothing else is skipped."""
    text = text or ""
    mail = [m.span() for m in EMAIL_RE.finditer(text)]
    tokens = [m.span() for m in re.finditer(r"\S+", text)]
    tails = _tail_ends(text)
    out = []

    def add(a: int, b: int) -> None:
        said = text[a:b].rstrip(".,;:!?")
        if said:
            out.append((a, a + len(said), _key(said)))
    for m in SCHEME_RE.finditer(text):
        add(*m.span())
    for m in HOST_RE.finditer(text):
        after = m.end("port") if m.group("port") else m.end()
        end = tails[after] if m.group("path") else after          # the host with its port and path
        if not any(x <= m.start() and end <= y for x, y in mail) and _host_is_address(text, m, tokens, text[after:end]):
            add(m.start(), end)
    for m in SPACED_RE.finditer(text):
        if m.group("path") or _plain(m.group(0)).split()[-1] in _SPACED_TLDS:
            add(*m.span())
    return sorted(set(out), key=lambda s: (s[0], -s[1]))


@functools.lru_cache(maxsize=64)
def _web_in(text: str) -> tuple[tuple[str, str], ...]:
    """Every web address `text` holds, once each: (as written, without trailing punctuation; as `_key`
    compares it). Cached: `_had` asks it of the same text once per address."""
    out: dict[str, str] = {}
    for a, b, k in _web_spans(text):
        if k:
            out.setdefault(k, (text or "")[a:b])
    return tuple((said, k) for k, said in out.items())


def _had(addr: str, text: str) -> bool:
    """True when `text` holds `addr` (a web address as `_key` gives it): the same address, with or
    without a scheme, a trailing slash or a full stop, or one that goes on from it with a path, query or
    fragment (x.com in x.com/a). Never as the tail of a longer host (x.com is not in fox.com nor in a.x.com),
    the head of a longer one or a longer path (x.com is not in x.community, x.com/a not in x.com/ab), nor
    in another case of a path (x.com/A is not x.com/a)."""
    return any(w == addr or (w.startswith(addr) and w[len(addr)] in "/?#") for _, w in _web_in(text))


def _new_addresses(old: str, new: str) -> tuple[list[str], list[str]]:
    """The email addresses and the web addresses `new` names that `old` did not."""
    had = set(_emails(old))
    mail = [m for m in _emails(new) if m not in had]
    web = [w for _, w in _web_in(new) if not _had(w, old)]
    return mail, web


def _loss(old: str, new: str) -> str | None:
    """Why a rewrite loses something, or None: empty, over `REWRITE_CAP`, or a web address (scheme
    or not, anything `_web_spans` finds), an email address or a `Task-N` of the old text missing. The same
    address with or without its scheme, a trailing slash or a full stop after it is kept. Such a rewrite
    is never applied nor offered: it becomes a note."""
    old, new = old or "", new or ""
    if not new.strip():
        return "empty"
    if len(new) > REWRITE_CAP:
        return f"longer than {REWRITE_CAP} characters"
    for said, w in _web_in(old):
        if not _had(w, new):
            return f"drops {said}"
    mail = set(_emails(new))
    for m in _emails(old):
        if m not in mail:
            return f"drops {m}"
    refs = {int(n) for n in TASK_REF_RE.findall(new)}
    for n in dict.fromkeys(int(n) for n in TASK_REF_RE.findall(old)):
        if n not in refs:
            return f"drops Task-{n}"
    return None


def guard(old: str, new: str) -> str | None:
    """Why a rewrite of `old` into `new` is not applied on its own, or None: what `_loss` says, else
    an address it adds."""
    why = _loss(old, new)
    if why:
        return why
    mail, web = _new_addresses(old, new)
    if mail:
        return f"adds the address {_short(mail[0])}"
    if web:
        return f"adds the web address {_short(web[0])}"
    return None


def _no_new_addresses(text: str, old: str) -> str:
    """`text` with every email or web address `old` lacks replaced by `LEFT_OUT`: the union of their
    spans (an overlapping or adjacent one included, and an address `old` has that overlaps one) is cut out,
    so no character of a new address is copied. If the result still names an address `old` lacks, the
    whole of it is `LEFT_OUT`."""
    had = set(_emails(old))
    text = EMAIL_RE.sub(lambda m: m.group(0) if m.group(0).lower() in had else LEFT_OUT, text or "")
    cut: list[list[int]] = []
    for a, b, k in sorted((a, b, k) for a, b, k in _web_spans(text) if not _had(k, old)):
        if cut and a <= cut[-1][1]:
            cut[-1][1] = max(cut[-1][1], b)
        else:
            cut.append([a, b])
    out, at = [], 0
    for a, b in cut:
        out.append(text[at:a] + LEFT_OUT)
        at = b
    out = "".join(out) + text[at:]
    mail, web = _new_addresses(old, out)
    return LEFT_OUT if mail or web else out


def _task_ref(v) -> int | None:
    """A task id the model wrote: an int (never a bool), "12" or "Task-12"."""
    if type(v) is int:
        return v
    if isinstance(v, str):
        m = re.fullmatch(r"\s*(?:Task-)?(\d{1,6})\s*", v)
        if m:
            return int(m.group(1))
    return None


def _said(field: str, v) -> str:
    """A cleaned value in a card's label."""
    if field == "hard":
        return "true" if v else "false"
    return "none" if v is None or v == "" else str(v)


def clean_proposal(conn: sqlite3.Connection, cfg: dict, p: dict, task_id: int, b: dict) -> tuple[dict | None, str]:
    """One proposal of the entry for `task_id` → `(change, label)` for a Review card, or
    `(None, why it is refused)`. Never `counterpart`, `waiting_on`, `context` or `merge`; a `set` or
    `create` carries only `SET_FIELDS`, cleaned like a recording's; a link names a task of the bundle."""
    op = p.get("op")
    head = f"Task-{task_id} {_short(op)}"
    given = p.get("task_id")
    if given is not None and given != task_id:
        return None, f"{head}: names Task-{_short(given)}, not the task it is about"
    fields = p.get("fields", {})
    if fields is None:
        fields = {}
    if not isinstance(fields, dict):
        return None, f"{head}: fields is not an object"
    never = [k for k in NEVER_FIELDS if k in fields]
    if never:
        return None, f"{head}: a ripple never sets {' or '.join(never)}"
    if "context" in fields:
        return None, f"{head}: the text is the rewrite's job, not a proposal's"
    if op in ("set", "create") and "name" in fields:        # the cleaner would stage "set name none" or cut it silently
        n = fields["name"]
        if not isinstance(n, str) or not n.strip():
            return None, f"{head}: a name cannot be blank"
        if len(n.strip()) > NAME_MAX:
            return None, f"{head}: the name is over {NAME_MAX} characters"
    if op in ("done", "drop"):
        return {"task_id": task_id, "op": op, "fields": {}}, f"{op} Task-{task_id}"
    if op in ("set", "create"):
        other = [k for k in fields if k not in SET_FIELDS]
        if other:
            return None, f"{head}: a ripple may propose only {', '.join(SET_FIELDS)}, not {_short(', '.join(map(str, other)))}"
        f = _clean_task_fields(cfg, fields)
        dropped = [k for k in fields if k not in f or (k == "project" and f[k] != fields[k])]
        if dropped:
            return None, f"{head}: {', '.join(dropped)} is not a value Factotum takes"
        if op == "create":
            if not f.get("name"):
                return None, f"{head}: a new task needs a name"
            return {"task_id": None, "op": "create", "fields": f}, f"create: {f['name']}"
        if not f:
            return None, f"{head}: nothing to set"
        said = ", ".join(f"{k} {_said(k, f[k])}" for k in fields)
        return {"task_id": task_id, "op": "set", "fields": f}, f"set {said} on Task-{task_id}"
    if op == "add_reminder":
        try:
            rf = batches._reminder_fields({"fields": fields})
        except ValueError as e:
            return None, f"{head}: {e}"
        return {"task_id": task_id, "op": "add_reminder", "fields": rf}, f"a reminder on Task-{task_id} at {rf['fire_at'][:16].replace('T', ' ')}"
    if op in ("link", "unlink"):
        kind, other = fields.get("kind"), _task_ref(fields.get("other"))
        if kind not in R.LINK_KINDS:
            return None, f"{head}: kind must be part_of or waits_on, not {_short(kind)}"
        if other is None or other == task_id or other not in b["ids"]:
            return None, f"{head}: other must be another task listed in the run, not {_short(fields.get('other'))}"
        why = R.link_problem(conn, op, task_id, kind, other)
        if why:
            return None, f"{head}: {why}"
        words = R.WORDS[kind] if op == "link" else f"no longer {R.WORDS[kind]}"
        return {"task_id": task_id, "op": op, "fields": {"kind": kind, "other": other}}, f"Task-{task_id} {words} Task-{other}"
    if op == "merge":
        return None, f"{head}: a ripple never merges"
    return None, f"{head}: not an op a ripple may propose"


def apply(conn: sqlite3.Connection, cfg: dict, b: dict, entries: list[dict], run_id: int | None, today: date) -> dict:
    """Write what a parsed reply decided. A text rewrite that passes `guard` is a `set` carrying the
    text the run read as `expect` (a hand edit made meanwhile wins); one that loses something (`_loss`)
    becomes a dated `add_note` (its reason, or fixed words when the reason names an address the old
    text lacked); both ride in ONE `ripple`
    batch with the cause and each entry's `why` as the change's note. A rewrite that only adds an
    email or web address is a Review card (the same `set` with its `expect`), or a note when no card
    can be staged. A neighbour no longer open is left alone. Every proposal is a Review card
    (`proposal` evidence, `account = 'ripple'`), at most `PROPOSAL_CAP`, never one already waiting. Commits."""
    first = b["from"][0] if b["from"] else None
    changes: list[dict] = []
    rewrites: list[int] = []
    noted: list[dict] = []
    staged: list[str] = []
    refused: list[str] = []
    skipped: list[str] = []
    waiting: list[dict] = []
    for r in rows(conn, "SELECT ref FROM evidence WHERE kind = 'proposal' AND judged_at IS NULL"):
        w = _load(r["ref"])
        if isinstance(w, dict):
            waiting.append(w)

    def stage(ch: dict, label: str, reason: str) -> bool:
        """One Review card, unless the same one waits or the run has staged `PROPOSAL_CAP`."""
        if any(_same_suggestion(ch, w) for w in waiting):
            skipped.append(f"{label}: already waiting in Review")
            return False
        if len(staged) >= PROPOSAL_CAP:
            skipped.append(f"{label}: over the {PROPOSAL_CAP} cards a run may stage")
            return False
        conn.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (?, 'proposal', ?, ?, ?, ?, ?)",
                     (ch["task_id"], json.dumps(ch), ACCOUNT, label, reason, now_iso()))
        waiting.append(ch)
        staged.append(label)
        return True

    for e in entries:
        tid = e["task_id"]
        if e["effect"] == "none":
            continue
        t = get_task(conn, tid)
        if not t or t["status"] != "open":
            skipped.append(f"Task-{tid} is {t['status']} now" if t else f"Task-{tid} no longer exists")
            continue
        if e["effect"] == "text":
            old = b["texts"][tid]
            cut = _cut(old)                                  # the run saw part of it: a rewrite would lose the rest
            why_not = TOO_LONG if cut else guard(old, e["context"])
            why = _no_new_addresses(e["why"], old)           # History's line for the change brings no address either
            if why_not is None:
                changes.append({"task_id": tid, "op": "set", "fields": {"context": e["context"]}, "expect": {"context": old}, "note": why})
                rewrites.append(tid)
            elif not cut and _loss(old, e["context"]) is None and stage(
                    {"task_id": tid, "op": "set", "fields": {"context": e["context"]}, "expect": {"context": old}},
                    f"rewrite the text of Task-{tid}: it {why_not}", e["why"]):
                pass                                         # it only adds an address: the owner decides on Review (design §8.6)
            else:
                # the note is task text: a reason that named a new address is not written at all
                said = f"Ripple from Task-{first}: {e['why'] if why == e['why'] else WITHHELD}" if why else f"Ripple from Task-{first}"
                note = (f"{why} ({TOO_LONG})" if why else TOO_LONG[0].upper() + TOO_LONG[1:]) if cut else why    # History says why in words
                changes.append({"task_id": tid, "op": "add_note", "fields": {"text": said}, "note": note})
                noted.append({"task_id": tid, "why": why_not})
        for p in e["proposals"]:
            ch, label = clean_proposal(conn, cfg, p, tid, b)
            if ch is None:
                refused.append(label)
            else:
                stage(ch, label, " ".join(str(p.get("reason") or "").split())[:WHY_CAP])
    batch_id = None
    rewritten: list[int] = []
    if changes:
        cause = {"kind": RUN_KIND, "from": b["from"], "run": run_id, "batches": b["batches"], "what": b["what"],
                 "noted": [n["task_id"] for n in noted]}
        res = batches.apply(conn, changes, RUN_KIND, today, cause=cause)
        batch_id = res["batch_id"]
        wrote = {r["task_id"] for r in conn.execute("SELECT DISTINCT task_id FROM changes WHERE batch_id = ? AND field = 'context'", (batch_id,))}
        rewritten = [tid for tid in rewrites if tid in wrote]
        for s in res["skipped"]:
            why, tid = str(s.get("why") or ""), s.get("task_id")
            skipped.append(why if f"Task-{tid}" in why else f"Task-{tid}: {why}")
    conn.commit()
    return {"batch_id": batch_id, "rewritten": rewritten, "noted": noted, "staged": staged, "refused": refused, "skipped": skipped}


# --- the run (design §8.1, §8.5) -----------------------------------------------------------------

def _tasks(ids) -> str:
    return ", ".join(f"Task-{i}" for i in ids)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _drop_queued(conn: sqlite3.Connection, b: dict) -> None:
    """Delete the group's queue rows that nothing queued again while the run was in flight."""
    for tid, last in b["queue"].items():
        conn.execute("DELETE FROM ripple_queue WHERE task_id = ? AND last_batch_id = ?", (tid, last))


def _cannot_run(conn: sqlite3.Connection, b: dict, why: str, err: str, ms: int, today: date) -> dict:
    """Claude cannot run on this machine (no program, or not signed in): the group's rows, as the run read
    them, are set aside at once (a retry would fail the same way), the cause is noted for Settings, and one
    failed row a day is logged for it. Commits. `unavailable` tells the scheduler to log nothing more."""
    from .claude import runner as _runner
    aside = [tid for tid, last in b["queue"].items()
             if conn.execute("DELETE FROM ripple_queue WHERE task_id = ? AND last_batch_id = ?", (tid, last)).rowcount]
    if _runner.note_unavailable(conn, RUN_KIND, why, today):
        log_run(conn, RUN_KIND, ms, ok=False, summary="ripple",
                error=f"{err[:600]} · ripples wait until Claude runs on this machine; set aside: {_tasks(aside) or 'nothing'}")
    conn.commit()
    return {"ok": False, "error": err, "unavailable": why, "cost_usd": 0.0}


def _failed(conn: sqlite3.Connection, b: dict, err: str, ms: int, cost: float, dry_run: bool, run_id: int | None = None) -> dict:
    """A run that failed: one more attempt on each of the group's rows still as the run read them, a
    row reaching `MAX_ATTEMPTS` set aside; the run logged with its cost. Commits."""
    if not dry_run:
        aside = []
        for tid, last in b["queue"].items():
            conn.execute("UPDATE ripple_queue SET attempts = attempts + 1 WHERE task_id = ? AND last_batch_id = ?", (tid, last))
            if conn.execute("DELETE FROM ripple_queue WHERE task_id = ? AND last_batch_id = ? AND attempts >= ?", (tid, last, MAX_ATTEMPTS)).rowcount:
                aside.append(tid)
        if aside:
            err += f" · failed twice on {_tasks(aside)}: set aside"
    # an "applying" row a batch already committed is this run's row: it says the failure, not a second row
    if run_id is not None and conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone():
        conn.execute("UPDATE runs SET ok = 0, summary = 'ripple', error = ? WHERE id = ?", (err[:1000], run_id))
    else:
        log_run(conn, RUN_KIND, ms, ok=False, summary="ripple", error=err[:1000], cost_usd=cost)
    conn.commit()
    return {"ok": False, "error": err, "cost_usd": cost}


def _preview(conn: sqlite3.Connection, cfg: dict, b: dict, entries: list[dict]) -> list[dict]:
    """What `apply` would do with each entry and each proposal, writing nothing."""
    out: list[dict] = []
    for e in entries:
        tid = e["task_id"]
        verdict = "nothing"
        if e["effect"] == "text":
            cut = _cut(b["texts"][tid])
            why_not = TOO_LONG if cut else guard(b["texts"][tid], e["context"])
            verdict = ("would rewrite" if why_not is None else f"would add a note: {why_not}" if cut or _loss(b["texts"][tid], e["context"])
                       else f"would stage a card: it {why_not}")
        out.append({"task_id": tid, "effect": e["effect"], "verdict": verdict})
        for p in e["proposals"]:
            ch, label = clean_proposal(conn, cfg, p, tid, b)
            out.append({"task_id": tid, "proposal": label if ch else None, "verdict": "would stage" if ch else f"refused: {label}"})
    return out


def _line(b: dict, out: dict) -> str:
    """The Log's line for an applied run."""
    k = len(out["rewritten"])
    line = f"Ripple from {_tasks(b['from'])}: {k} task{'' if k == 1 else 's'} updated" + (f" ({_tasks(out['rewritten'])})" if k else "")
    if out["noted"]:
        line += f", {_plural(len(out['noted']), 'note')} (" + "; ".join(f"Task-{n['task_id']}: {n['why']}" for n in out["noted"]) + ")"
    if out["staged"]:
        line += f", {_plural(len(out['staged']), 'card')} for Review"
    if out["refused"]:
        line += f", {len(out['refused'])} refused"
    if out["skipped"]:
        line += f", {len(out['skipped'])} skipped"
    if out["batch_id"]:
        line += f"; batch {out['batch_id']}"
    return line


def run(conn: sqlite3.Connection, cfg: dict, task_ids: list[int], today: date | None = None, *, runner=None,
        dry_run: bool = False, at: str | None = None) -> dict:
    """One ripple run from the changed tasks `task_ids` (a group from the queue, or one task asked
    for from the CLI): the bundle, one `claude -p` with no tools on the Settings model and budget,
    the whole reply checked, then `apply`. A queue row is deleted only when nothing queued it again
    during the run; a failed run counts an attempt, and a group that fails `MAX_ATTEMPTS` times is
    set aside. A dry run makes the real run and says what it would do, writing nothing but the Log row."""
    from . import reminders
    from .claude import runner as _runner
    runner = runner or _runner.run
    if today is None or at is None:
        now = reminders.local_now(cfg)
        today = today or now.date()
        at = at or now.strftime("%Y-%m-%dT%H:%M")
    b = bundle(conn, cfg, task_ids, today)
    if not b["changed"]:                                    # every change that queued it was undone or put back
        if not dry_run:
            _drop_queued(conn, b)
            conn.commit()
        return {"ok": True, "line": "nothing left to ripple: what queued it was undone or put back", "cost_usd": 0.0}
    if not b["neighbours"]:
        if not dry_run:
            _drop_queued(conn, b)
            conn.commit()
        return {"ok": True, "line": "nothing linked is open", "cost_usd": 0.0}
    started = time.monotonic()
    prompt = build_prompt(cfg, b, today, at[11:16])
    workdir = RUNS_DIR / today.isoformat() / f"{at[11:16].replace(':', '')}-{b['from'][0]}"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "prompt.md").write_text(prompt, encoding="utf-8")
    # tools="": every task text in this prompt may hold a stranger's words, so the run gets no tool at all
    res = runner(cfg, RUN_KIND, prompt, allowed_tools=[], tools="", workdir=workdir,
                 model=C.value(cfg, "claude.ripple.model"), budget_usd=float(C.value(cfg, "claude.budgets_usd.ripple")))
    (workdir / "result.txt").write_text(res.result_text or "", encoding="utf-8")
    ms = int((time.monotonic() - started) * 1000)
    why = _runner.unavailable(res)
    if why and not dry_run:
        return _cannot_run(conn, b, why, res.error or why, ms, today)
    if not res.ok:
        return _failed(conn, b, res.error or "no JSON", ms, res.cost_usd, dry_run)
    if not dry_run:
        _runner.clear_unavailable(conn)
    try:
        entries = parse(res.data, b)
    except ReplyError as e:
        return _failed(conn, b, f"{SHAPE}: {'no list under ripples' if str(e) == SHAPE else e}", ms, res.cost_usd, dry_run)
    if dry_run:
        preview = _preview(conn, cfg, b, entries)
        log_run(conn, RUN_KIND, ms, ok=True, summary=f"dry run: {_tasks(b['from'])}, {len(entries)} entries", cost_usd=res.cost_usd)
        conn.commit()
        return {"ok": True, "dry_run": True, "data": res.data, "entries": entries, "preview": preview, "prompt": prompt, "cost_usd": res.cost_usd}
    gone = [r["id"] for r in conn.execute(f"SELECT id FROM batches WHERE id IN ({','.join('?' * len(b['batches']))}) AND undone_at IS NOT NULL "
                                          "ORDER BY id", tuple(b["batches"])).fetchall()] if b["batches"] else []
    if gone:
        # Undone while Claude was thinking: the reply answers a change that is no longer there. Nothing is
        # applied; undo already took that batch out of the queue, so what is left there ripples again.
        line = f"Ripple from {_tasks(b['from'])}: not applied, batch {', '.join(map(str, gone))} was undone during the run"
        run_id = log_run(conn, RUN_KIND, ms, ok=True, summary=line, cost_usd=res.cost_usd)
        conn.commit()
        return {"ok": True, "line": line, "cost_usd": res.cost_usd, "run_id": run_id, "batch_id": None,
                "rewritten": [], "noted": [], "staged": [], "refused": [], "skipped": []}
    run_id = log_run(conn, RUN_KIND, ms, ok=True, summary="applying", cost_usd=res.cost_usd)
    try:
        out = apply(conn, cfg, b, entries, run_id, today)
    except Exception as e:                                   # the run is paid for: log it even when applying it fails
        conn.rollback()
        return _failed(conn, b, f"applying the reply failed: {type(e).__name__}: {e}", ms, res.cost_usd, dry_run, run_id)
    line = _line(b, out)
    conn.execute("UPDATE runs SET summary = ? WHERE id = ?", (line[:1000], run_id))
    _drop_queued(conn, b)
    conn.commit()
    return dict(out, ok=True, line=line, cost_usd=res.cost_usd, run_id=run_id)


# --- when (design §8.1): the scheduler's side of the queue ---------------------------------------

def _utc(stamp) -> datetime | None:
    """A stored timestamp as an aware UTC datetime (a naive one is taken as UTC), or None."""
    try:
        d = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _int(cfg: dict, key: str) -> int:
    """A numeric Settings key, its code default when the value is not a number."""
    try:
        return int(C.value(cfg, key))
    except (TypeError, ValueError):
        return int(C.DEFAULTS[key])


def clear(conn: sqlite3.Connection) -> int:
    """Empty the queue (ripples are off, so switching them on later starts clean). The caller commits."""
    return conn.execute("DELETE FROM ripple_queue").rowcount


def drop_stale(conn: sqlite3.Connection, now_utc: datetime | None = None) -> list[int]:
    """Delete the rows queued longer ago than `MAX_AGE` and those whose task has no active link any
    more (nothing left to ripple to); returns their task ids. The caller commits."""
    now_utc = now_utc or datetime.now(timezone.utc)
    out: list[int] = []
    for r in pending(conn):
        at = _utc(r["queued_at"])
        linked = conn.execute("SELECT 1 FROM relations WHERE (src = ? OR dst = ?) AND removed_at IS NULL LIMIT 1",
                              (r["task_id"], r["task_id"])).fetchone()
        if at is None or now_utc - at > MAX_AGE or not linked:
            conn.execute("DELETE FROM ripple_queue WHERE task_id = ?", (r["task_id"],))
            out.append(r["task_id"])
    return out


def _capped(conn: sqlite3.Connection, cfg: dict, now_utc: datetime) -> bool:
    """True when `claude.ripple.hourly_cap` runs of kind `ripple` started in the last hour."""
    cap = _int(cfg, "claude.ripple.hourly_cap")
    recent = conn.execute("SELECT started_at FROM runs WHERE kind = ? ORDER BY id DESC LIMIT ?", (RUN_KIND, max(cap, 0))).fetchall()
    return sum(1 for r in recent if (at := _utc(r["started_at"])) is not None and now_utc - at < timedelta(hours=1)) >= cap


def _groups(conn: sqlite3.Connection, rows_: list[dict]) -> list[list[dict]]:
    """The queued rows in connected groups: two rows go together when one task is linked to the
    other or both are linked to the same task (one link away, or steps of one goal)."""
    near = {r["task_id"]: {nid for nid, _ in _neighbour_links(conn, [r["task_id"]])} for r in rows_}
    parent = {tid: tid for tid in near}

    def root(t: int) -> int:
        while parent[t] != t:
            t = parent[t]
        return t
    ids = list(near)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if b in near[a] or a in near[b] or near[a] & near[b]:
                parent[root(b)] = root(a)
    out: dict[int, list[dict]] = {}
    for r in rows_:
        out.setdefault(root(r["task_id"]), []).append(r)
    return list(out.values())


def next_group(conn: sqlite3.Connection, cfg: dict, now_utc: datetime | None = None) -> list[int]:
    """The changed task ids of the one group a tick runs, or [] (the hourly cap reached, or nothing
    due). A group is due at once when an outcome arrived (`now`), otherwise when its newest row is
    `claude.ripple.delay_seconds` old; of the due groups the one waiting longest goes first, its ids
    in queue order and cut to `CHANGED_CAP` (the rest stay queued)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    queued = pending(conn)
    if not queued or _capped(conn, cfg, now_utc):
        return []
    quiet = timedelta(seconds=_int(cfg, "claude.ripple.delay_seconds"))
    at = {r["task_id"]: _utc(r["queued_at"]) or now_utc for r in queued}      # an unreadable stamp counts as just queued
    due: list[tuple[datetime, list[dict]]] = []
    for g in _groups(conn, queued):
        stamps = [at[r["task_id"]] for r in g]
        if any(r["now"] for r in g) or now_utc - max(stamps) >= quiet:
            due.append((min(stamps), sorted(g, key=lambda r: (at[r["task_id"]], r["task_id"]))))
    if not due:
        return []
    due.sort(key=lambda d: (d[0], d[1][0]["task_id"]))
    return [r["task_id"] for r in due[0][1][:CHANGED_CAP]]


def status(conn: sqlite3.Connection, cfg: dict, *, scheduler: bool, after: int | None = None) -> dict:
    """What `GET /api/ripple` answers: whether ripples are on, whether a scheduler runs them here, what
    waits, and the ripple batches that landed after batch `after` (newest first, at most 10)."""
    enabled = bool(C.value(cfg, "claude.ripple.enabled"))
    names = {r["id"]: r["name"] for r in conn.execute("SELECT t.id, t.name FROM tasks t JOIN ripple_queue q ON q.task_id = t.id")}
    waiting = [{"task_id": r["task_id"], "name": names.get(r["task_id"]), "queued_at": r["queued_at"], "now": bool(r["now"])}
               for r in pending(conn)]
    landed = []
    if after is not None:
        for b in conn.execute("SELECT id, applied_at, created_at, cause_json, undone_at FROM batches WHERE source = ? AND id > ? "
                              "ORDER BY id DESC LIMIT 10", (RUN_KIND, after)).fetchall():
            cause = _load(b["cause_json"])
            tasks = [{"id": r["id"], "name": r["name"]} for r in conn.execute(
                "SELECT t.id, t.name FROM (SELECT task_id, MIN(id) AS first FROM changes WHERE batch_id = ? GROUP BY task_id) c "
                "JOIN tasks t ON t.id = c.task_id ORDER BY c.first", (b["id"],))]
            landed.append({"batch_id": b["id"], "at": b["applied_at"] or b["created_at"],
                           "from": cause.get("from", []) if isinstance(cause, dict) else [], "tasks": tasks, "undone": b["undone_at"] is not None})
    return {"enabled": enabled, "scheduler": scheduler, "pending": waiting,
            "waiting_for_scheduler": bool(enabled and waiting and not scheduler), "landed": landed}


def since(conn: sqlite3.Connection, task_id: int, changed: dict | None) -> dict | None:
    """What Review's "Since you last looked" block says about a card a ripple changed: which linked
    tasks did what (with their outcome) and the text the ripple wrote, before and after. None when
    `changed` (annotate's `rel.changed`) is missing or its batch is not a ripple."""
    if not changed:
        return None
    bid = changed.get("batch_id")
    row = conn.execute("SELECT id, applied_at, undone_at, cause_json FROM batches WHERE id = ?", (bid,)).fetchone()
    cause = _load(row["cause_json"]) if row else None
    if not isinstance(cause, dict) or cause.get("kind") != RUN_KIND:
        return None
    by = []
    for fid in cause.get("from") or []:
        t = get_task(conn, fid)
        if not t:
            continue
        what = list((cause.get("what") or {}).get(str(fid)) or [])
        did = t["status"] if "closed" in what and t["status"] in ("done", "dropped") else "merged" if "merge" in what else "changed"
        by.append({"id": fid, "name": t["name"], "status": t["status"], "outcome": t.get("outcome"),
                   "completed_at": t.get("completed_at"), "did": did, "what": what})
    text = None
    c = conn.execute("SELECT old, new, note FROM changes WHERE batch_id = ? AND task_id = ? AND field = 'context' ORDER BY id LIMIT 1", (bid, task_id)).fetchone()
    if c:
        text = {"before": _load(c["old"]), "after": _load(c["new"]), "note": c["note"]}
    return {"batch_id": bid, "at": row["applied_at"], "undone": bool(row["undone_at"]), "cause_undone": bool(cause.get("cause_undone_at")),
            "by": by, "text": text, "noted": task_id in (cause.get("noted") or [])}
