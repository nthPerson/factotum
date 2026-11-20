"""Review as a pass held on the server (spoken Review, stage 2a, design §6.1-6.6): one walk through the Review queue,
its order and one cursor, which the page, the connector and the stdio skill server move alike.

A pass holds the ORDER and WHERE THE OWNER STANDS. It never holds what an item says (every read builds the item from
the live queue, `review_queue.items`) and never what was decided (the tray's `decides` column, `tray.decided`), so a
dead page, another device or the connector cannot disagree with the tray. Next leaves an item undecided and writes
nothing to the task; Back returns to anything that is not gone; Next after the last comes round to what was left.

Every function that writes starts with `db.begin_write(conn)` and reads after it, never commits (the caller does) and
never calls Claude or the network. A pass write is compare-and-set on `rev`; a relative move names the item it moves
from, and a move from an item no longer in front moves nothing (`moved: False`). Reads (`view`, `now`, `plan`,
`options`) write nothing: what a read finds changed (an arrival, an end with something undecided again) is written by
the next write. Arriving at a task writes its offered stamp (`tasks.last_offered_at`, what `lifecycle.expire` acts on),
a stamp and not a change: no batch, no History."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config as C, dates, lifecycle, recommend, review_cards, review_decide, review_queue, spoken as spoken_words, suggestions, tray
from .dates import parse_date
from .db import begin_write, get_task, now_iso, rows
from .prompts import one_line

URGENT_DAYS = review_queue.URGENT_DAYS        # an arrival this urgent (order_key's tier 0) goes right after the item in front
GROUP_MIN, GROUP_MAX = 2, 8                   # alike new tasks together (2d)
GROUP_NEAR_DAYS = 2                           # a firm date this near (or past) keeps a new task out of a group
REGROUPS = ("take_out", "one_by_one")         # what the group route does: regroups of the pass, not options (design §4.2)
SECONDS_PER_ITEM = 12                         # for "~n min" left
SPEAKING_S = 600                              # a connector's or skill's last look this recent: it is speaking
MOVES = ("next", "back", "goto", "first", "continue", "end")
ENDS = ("applied", "finished", "replaced", "another_day")
DRIVERS = ("page", "connector", "skill")
PASS_ONLY = ("rec", "keep_all")               # options only a pass offers (rec: 2c, keep_all: 2d); the rule refuses every other option itself
UNDECIDED = ("waiting", "passed")
SETTLED = ("decided", "answered")             # counted as decided for moving and counting
EVIDENCE_REASONS = ("thread_updated", "mentioned", "planner", "ripple")
ANSWERABLE = EVIDENCE_REASONS + ("link_suggested",)   # reasons the tray can answer without a decision

Refused = review_decide.Refused
Gone = review_decide.Gone

# Every fixed word the pass gives a person (the forbidden-words test reads them all).
WORDS = {
    "done": "Done", "keep": "Keep", "later": "Later", "drop": "Drop",
    "accept": "Accept", "dismiss": "Dismiss", "follow": "Follow its thread", "not_about": "Not about this",
    "link": "Link", "not_related": "Not related", "not_same": "Not the same", "merge": "Merge", "merge_into": "Merge into Task-",
    "step": "is a step of it", "waits": "waits on it", "same": "is the same task",
    "pin": "★ Pin to Top", "unpin": "★ On Top · unpin", "longterm": "Mark long-term (never fades)", "not_longterm": "Long-term · clear",
    "create": "Create", "card_dismiss": "Dismiss", "undecide": "Take back", "applied": "applied",
    "settled_done": "It is done.", "settled_dropped": "It was dropped.", "settled_faded": "It faded.", "settled_lapsed": "It lapsed.",
    "settled_later": "It is set for later.", "settled_quiet": "Nothing on it needs a decision now.",
    "settled_card": "The suggestion was answered.", "settled_applied": "Its decision was applied.", "settled_missing": "It no longer exists.",
    "card_mail": "From your mail", "card_slack": "Mentioned in Slack", "card_ripple": "Ripple suggests", "card_planner": "Planner suggests",
    "ended": "this review has ended", "no_item": "that item is not in this review", "no_groups": "this review has no groups to change",
    "moved_meanwhile": "the review moved meanwhile; read it again",
    "rec_changed": "the suggested answer changed meanwhile; read it again",
    "rec_steps": "a suggested answer names the steps it was shown", "rec_members": "Keep all names the tasks it was shown",
    "group_n": "{n} new tasks", "group_recording": "New from a recording", "group_course": "New in a course",
    "group_thread": "New from one mail thread", "keep_all": "Keep all", "one_by_one": "One by one",
    "settled_group": "Every task in it was settled elsewhere.", "not_group": "that is not a group in this review",
    "not_member": "that task is not in this group", "group_done": "every task in this group is decided already",
    "group_note": "a note belongs to one task: decide it by itself", "bad_except": "each exception names a task of this group and one option",
}


class NoPass(LookupError):
    """No pass with that id. → 404"""


class Ended(Exception):
    """The pass has ended, or is from another day (the next begin ends it). → 409"""


# --- the plan: a pure reading of the pass against the queue and the tray -------------------------------------

@dataclass
class Plan:
    order: list[str]                         # every item, stored and arriving, in `pos` order
    pos: dict[str, float]
    kinds: dict[str, str]
    states: dict[str, str]                   # waiting | passed | decided | answered | gone (§6.2)
    arrivals: list[tuple[str, float, str]]   # (key, pos, kind): queue items the pass does not hold yet
    entries: dict[str, dict]                 # the queue's entries by key
    state: str                               # the pass's state as it reads now (an end with something undecided is back on an item)
    cursor: str | None
    members: dict[str, list[str]] = field(default_factory=dict)     # a group's members (t:<id>) in order (2d)
    member_of: dict[str, str] = field(default_factory=dict)         # a member's group
    mstates: dict[str, str] = field(default_factory=dict)           # a member's state: waiting | decided | gone
    regrouped: dict[str, list[int]] = field(default_factory=dict)   # a stored group whose members changed: its ids now
    dissolved: dict[str, str | None] = field(default_factory=dict)  # a stored group left with one member (that one), or none
    batch: dict[str, int] = field(default_factory=dict)             # an arrival out of a group that carries its done_batch_id
    alike: dict[str, dict] = field(default_factory=dict)            # alike(): the groupable queue tasks' group and provenance

    def undecided(self) -> list[str]:
        return [k for k in self.order if self.states[k] in UNDECIDED]

    def shown(self) -> list[str]:
        return [k for k in self.order if self.states[k] != "gone"]


def _kind(key: str) -> str:
    return {"t": "task", "s": "card", "g": "group"}.get(str(key)[:1], "task")


def _trows(tray_entries) -> list[tuple[str, dict, dict]]:
    out = []
    for r in tray_entries or []:
        r = list(r) + [None, None]
        out.append((str(r[0]), r[1] if isinstance(r[1], dict) else {}, r[2] if isinstance(r[2], dict) else {}))
    return out


def _answered(entry: dict, keys: set[str], evs: set[int]) -> bool:
    """No decision, but every reason is answered by the tray: each shown link suggestion is the `ev` of a staged link
    or merge or is judged under `e:<eid>`, and each piece of evidence behind Thread updated, Mentioned, Planner or
    Ripple is judged under `e:<eid>`. Any other reason needs a decision."""
    reasons = entry.get("reasons") or []
    if "task" not in entry or not reasons or any(r not in ANSWERABLE for r in reasons):
        return False
    if "link_suggested" in reasons:
        sugg = entry.get("link_suggestions") or []
        if not sugg or any(s["evidence_id"] not in evs and f"e:{s['evidence_id']}" not in keys for s in sugg):
            return False
    if any(r in EVIDENCE_REASONS for r in reasons):
        ev = entry.get("evidence") or []
        if not ev or any(f"e:{e['id']}" not in keys for e in ev):
            return False
    return True


def _state(key: str, stored: dict | None, entry: dict | None, deciding: dict, keys: set[str], evs: set[int]) -> str:
    if key in deciding:
        return "decided"
    if entry is None:
        return "decided" if stored and stored.get("done_batch_id") else "gone"
    if _answered(entry, keys, evs):
        return "answered"
    return "passed" if stored and stored.get("passes") else "waiting"


def _member_ids(row: dict) -> list[int]:
    try:
        ids = json.loads(row.get("members_json") or "[]")
    except ValueError:
        return []
    return [i for i in ids if type(i) is int] if isinstance(ids, list) else []


def _mstate(m: str, grow: dict | None, entries: dict, deciding: dict) -> str:
    """A group member's state: decided (a tray row decides it, or it left the queue after this group's Apply), waiting
    (in the queue), else gone."""
    if m in deciding:
        return "decided"
    if m in entries:
        return "waiting"
    return "decided" if grow and grow.get("done_batch_id") else "gone"


def _gstate(ms: list[str], mst: dict, grow: dict | None) -> str:
    """A group's state from its members': gone when every one is, decided when every one left is, else waiting or
    passed (Next left the group, `passes`)."""
    live = [m for m in ms if mst[m] != "gone"]
    if not live:
        return "gone"
    if all(mst[m] in SETTLED for m in live):
        return "decided"
    return "passed" if grow and grow.get("passes") else "waiting"


def _by_due(entries: dict):
    def k(m):
        t = (entries.get(m) or {}).get("task") or {}
        d = t.get("due") if isinstance(t.get("due"), str) and parse_date(t.get("due")) else None
        return (d or "9999", int(m[2:]))
    return k


def plan(pass_row: dict, stored_items: list[dict], queue: dict, tray_entries, today: date, alike: dict | None = None,
         form: bool = False) -> Plan:
    """Every stored item's state (§6.2) and the arrivals: queue items the pass does not hold, each placed at the end
    (in Review's order among themselves) or, when urgent (order_key's tier 0) while an item is in front, right after
    it. A pass in the state `end` or `empty` with something undecided reads as back on its first undecided item, and a
    checkpoint with nothing undecided as the end. Pure.

    Groups (2d, design §6.9): a stored group holds its members; a member in the queue that is no longer groupable
    (`alike` has no entry for it: it gained a reason, Top, a near firm date) leaves it and arrives as itself right after
    the group; a group left with one member is that task, in its place (and the cursor with it); a group's state is its
    members'. With `form` (review.group_alike), arrivals alike in `alike` (2 to GROUP_MAX of one recording, course or
    thread, more split) arrive as a new group; without it nothing new is grouped and the groups the pass holds stay."""
    alike = alike or {}
    entries = {e["key"]: e for e in list(queue.get("tasks") or []) + list(queue.get("cards") or [])}
    trows = _trows(tray_entries)
    deciding: dict[str, list[str]] = {}
    for k, _, m in trows:
        if m.get("decides"):
            deciding.setdefault(m["decides"], []).append(k)
    keys = {k for k, _, _ in trows}
    evs = {e.get("ev") for _, e, _ in trows if type(e.get("ev")) is int}
    stored = {r["key"]: r for r in stored_items}
    P = Plan([], {}, {}, {}, [], entries, pass_row.get("state") or "item", pass_row.get("cursor_key"), alike=alike)
    spos = sorted(float(r["pos"]) for r in stored.values())
    placed: list[tuple[str, float, str]] = []
    old_pos: dict[str, float] = {}
    for k in sorted(stored, key=lambda k: (float(stored[k]["pos"]), k)):
        r, p = stored[k], float(stored[k]["pos"])
        if r["kind"] != "group":
            P.pos[k], P.kinds[k] = p, r["kind"]
            P.states[k] = _state(k, r, entries.get(k), deciding, keys, evs)
            continue
        mkeys = [f"t:{i}" for i in _member_ids(r)]
        outs = [m for m in mkeys if m in entries and m not in alike and m not in stored]
        kept = [m for m in mkeys if m not in outs and m not in stored]
        mst = {m: _mstate(m, r, entries, deciding) for m in kept}
        live = [m for m in kept if mst[m] != "gone"]
        nxt = min((q for q in spos if q > p), default=p + 1)
        if len(live) == 1 or not kept:
            P.dissolved[k] = live[0] if live else None
            old_pos[k] = p
            if live:
                placed.append((live[0], p, "task"))
                if r.get("done_batch_id"):
                    P.batch[live[0]] = r["done_batch_id"]
        else:
            P.pos[k], P.kinds[k], P.members[k] = p, "group", kept
            for m in kept:
                P.member_of[m], P.mstates[m] = k, mst[m]
            if kept != mkeys:
                P.regrouped[k] = [int(m[2:]) for m in kept]
            P.states[k] = _gstate(kept, mst, r)
        placed += [(m, p + (nxt - p) * (i + 1) / (len(outs) + 1), "task") for i, m in enumerate(outs)]
    for k, p, kind in placed:
        P.pos[k], P.kinds[k] = p, kind
        P.states[k] = _state(k, {"done_batch_id": P.batch.get(k)}, entries.get(k), deciding, keys, evs)
    P.arrivals += placed
    covered = set(P.pos) | set(P.member_of) | set(stored)
    new = [k for k in entries if k not in covered]
    units: list[tuple[str, dict]] = []                      # (key, the entry order_key reads)
    grouped: set[str] = set()
    if form:
        by_base: dict[str, list[str]] = {}
        for k in new:
            if k in alike:
                by_base.setdefault(alike[k]["base"], []).append(k)
        used = set(stored) | set(P.pos)
        for base, ks in by_base.items():
            if len(ks) < GROUP_MIN:
                continue
            ks = sorted(ks, key=_by_due(entries))
            n = math.ceil(len(ks) / GROUP_MAX)
            size = math.ceil(len(ks) / n)
            for j in range(0, len(ks), size):
                ch = ks[j:j + size]
                gk, i = base, 2
                while gk in used:
                    gk, i = f"{base}:{i}", i + 1
                used.add(gk)
                P.members[gk] = ch
                for m in ch:
                    P.member_of[m], P.mstates[m] = gk, _mstate(m, None, entries, deciding)
                P.states[gk] = _gstate(ch, P.mstates, None)
                units.append((gk, {"key": gk, "members": [entries[m] for m in ch]}))
                grouped |= set(ch)
    units += [(k, entries[k]) for k in new if k not in grouped]
    units.sort(key=lambda u: review_queue.order_key(u[1], today))
    st, cursor = P.state, P.cursor
    if cursor in P.dissolved:                                # the group in front became its one task, or nothing
        cursor = P.dissolved[cursor]
        if cursor is None:
            after = [k for k in sorted(P.pos, key=lambda k: (P.pos[k], k)) if P.pos[k] > old_pos[pass_row.get("cursor_key")] and P.states[k] in UNDECIDED]
            cursor = after[0] if after else None
            if cursor is None:
                st = "end"
    urgent = [k for k, e in units if review_queue.order_key(e, today)[0] == 0] if cursor in P.pos and st in ("item", "checkpoint") else []
    if urgent:
        c = P.pos[cursor]
        nxt = min((p for p in P.pos.values() if p > c), default=c + 1)
        P.arrivals += [(k, c + (nxt - c) * (i + 1) / (len(urgent) + 1), _kind(k)) for i, k in enumerate(urgent)]
    last = math.floor(max(P.pos.values(), default=0.0))
    P.arrivals += [(k, last + i + 1.0, _kind(k)) for i, k in enumerate(k for k, _ in units if k not in urgent)]
    for k, p, kind in P.arrivals:
        P.pos[k], P.kinds[k] = p, kind
        if k not in P.states:
            P.states[k] = _state(k, None, entries[k], deciding, keys, evs)
    P.order = sorted(P.pos, key=lambda k: (P.pos[k], k))
    undecided = [k for k in P.order if P.states[k] in UNDECIDED]
    if st in ("end", "empty") and undecided:
        st, cursor = "item", undecided[0]
    elif st == "checkpoint" and not undecided:              # decided meanwhile elsewhere: nothing to keep going over
        st, cursor = "end", None
    P.state, P.cursor = st, cursor
    return P


# --- alike new tasks (2d, design §6.9) ---------------------------------------------------------------------

REC_KEY = re.compile(r"^plaud:([A-Za-z0-9_.\-]{1,120}):[0-9]{1,6}$")                 # sync_file's source_key
LEAD_DATE = re.compile(r"^\s{0,4}[0-9]{1,4}[-/.][0-9]{1,2}(?:[-/.][0-9]{1,4})?[\s,·:\u2013\u2014-]{0,6}")


def groupable(entry: dict, today: date) -> bool:
    """A task item that may stand in a group (design §6.9): its reasons are exactly New, it is not firm-dated within
    GROUP_NEAR_DAYS (or past), not on Top, has no unjudged evidence and no shown link suggestion, and its
    recommendation, if any, is Keep. So a group never holds a last call, a suggested close or a merge. Pure."""
    t = entry.get("task")
    if not isinstance(t, dict) or list(entry.get("reasons") or []) != ["new"] or t.get("status", "open") != "open":
        return False
    d = parse_date(t.get("due")) if isinstance(t.get("due"), str) else None
    if t.get("hard") and d is not None and d <= today + timedelta(days=GROUP_NEAR_DAYS):
        return False
    if t.get("top_rank") or entry.get("evidence") or entry.get("link_suggestions"):
        return False
    rec = entry.get("recommendation")
    return rec is None or rec.get("answer") == "keep"


def _title(label) -> str | None:
    """A recording's name as a group's title: its leading date dropped, cut at the first colon; when what is before the
    colon is one word ("Meeting"), the first part of what follows it is kept too ("Meeting: Vehicle-Sound AI Development")."""
    s = LEAD_DATE.sub("", one_line(str(label or ""))[:300].strip(), count=1)
    head, _, rest = s.partition(":")
    head, rest = head.strip(), re.split(r",|;| and ", rest, maxsplit=1)[0].strip()
    t = f"{head}: {rest}" if len(head.split()) == 1 and rest else head or rest
    return t[:80] or None


def _day(cfg: dict, at) -> str | None:
    try:
        d = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is not None:
        try:
            d = d.astimezone(ZoneInfo(str(C.get(cfg, "owner.timezone", "UTC"))))
        except (KeyError, ValueError):
            pass
    return d.date().isoformat()


def _alike_of(conn: sqlite3.Connection, cfg: dict, tasks: list[dict]) -> dict[int, dict]:
    """{task id: {base, prov}} for the tasks that say where they came from: a recording (`source_key` plaud:<id>:<n>;
    the title from its plaud_note link), else a Canvas course (`source` Canvas, by project), else the first mail
    thread it follows. Reads only."""
    ids = [t["id"] for t in tasks if isinstance(t.get("id"), int)]
    if not ids:
        return {}
    links: dict[int, list[dict]] = {}
    for r in rows(conn, "SELECT task_id, kind, ref, label FROM links WHERE kind IN ('plaud_note', 'mail_thread')"
                        " AND task_id IN (SELECT value FROM json_each(?)) ORDER BY id", (json.dumps(ids),)):
        links.setdefault(r["task_id"], []).append(r)
    out: dict[int, dict] = {}
    for t in tasks:
        ls = links.get(t.get("id"), [])
        m = REC_KEY.match(str(t.get("source_key") or ""))
        threads = [x for x in ls if x["kind"] == "mail_thread"]
        if m:
            rid = m.group(1)
            note = next((x for x in ls if x["kind"] == "plaud_note" and x["ref"] == rid), None) or next((x for x in ls if x["kind"] == "plaud_note"), None)
            out[t["id"]] = {"base": f"g:rec:{rid}", "prov": {"kind": "recording", "title": _title(note["label"]) if note else None,
                                                             "day": _day(cfg, t.get("created_at"))}}
        elif t.get("source") == "Canvas" and str(t.get("project") or "").strip():
            pr = str(t["project"]).strip()[:80]
            out[t["id"]] = {"base": f"g:course:{pr}", "prov": {"kind": "course", "title": pr, "day": None}}
        elif threads:
            out[t["id"]] = {"base": f"g:thread:{str(threads[0]['ref'])[:120]}",
                            "prov": {"kind": "thread", "title": one_line(str(threads[0]["label"] or ""))[:120].strip() or None, "day": None}}
    return out


def alike(conn: sqlite3.Connection, cfg: dict, queue: dict, today: date) -> dict[str, dict]:
    """{task key: {base, prov}} for every groupable task item of the queue that came from a recording, a course or a
    thread: what `plan` groups arrivals by, and how it tells a member that may stay in its group. Asked whatever
    `review.group_alike` says (a pass begun with groups keeps them). Reads only."""
    es = [e for e in queue.get("tasks") or [] if groupable(e, today)]
    got = _alike_of(conn, cfg, [e["task"] for e in es])
    return {e["key"]: got[e["task"]["id"]] for e in es if e["task"]["id"] in got}


def _after(P: Plan, cur: str | None, skip_cur: bool = False) -> tuple[str | None, bool]:
    """The next undecided item after `cur`; with none after it, the first undecided from the start (came round,
    possibly `cur` itself unless `skip_cur`). (None, False) when nothing is undecided."""
    und = set(P.undecided())
    i = P.order.index(cur) if cur in P.order else -1
    for k in P.order[i + 1:]:
        if k in und:
            return k, False
    for k in P.order:
        if k in und and not (skip_cur and k == cur):
            return k, True
    return None, False


def _cap(cfg: dict) -> int:
    """`lifecycle.review_cap`, now items per sitting: the checkpoint comes after this many."""
    try:
        return max(1, int(C.value(cfg, "lifecycle.review_cap")))
    except (TypeError, ValueError):
        return int(C.DEFAULTS["lifecycle.review_cap"])


# --- reading the tables ----------------------------------------------------------------------------------------

def live(conn: sqlite3.Connection) -> dict | None:
    """The live pass (ended_at NULL), whatever its day, or None. Reads only."""
    r = rows(conn, "SELECT * FROM review_passes WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1")
    return r[0] if r else None


def _row(conn: sqlite3.Connection, pass_id) -> dict:
    r = rows(conn, "SELECT * FROM review_passes WHERE id = ?", (pass_id,))
    if not r:
        raise NoPass(f"no review {str(pass_id)[:20]}")
    return r[0]


def _live_row(conn: sqlite3.Connection, pass_id, today: date) -> dict:
    """The pass to write on: NoPass when there is none by that id, Ended when it has ended or is from another day
    (a move never ends it: the next begin does, as `another_day`)."""
    row = _row(conn, pass_id)
    if row["ended_at"] or row["day"] != today.isoformat():
        raise Ended(WORDS["ended"])
    return row


def _stored(conn: sqlite3.Connection, pass_id: int) -> list[dict]:
    return rows(conn, "SELECT * FROM review_pass_items WHERE pass_id = ? ORDER BY pos", (pass_id,))


def _plan(conn: sqlite3.Connection, cfg: dict, today: date, row: dict, queue: dict, tray_entries=None) -> Plan:
    return plan(row, _stored(conn, row["id"]), queue, tray.read(conn)["entries"] if tray_entries is None else tray_entries, today,
                alike=alike(conn, cfg, queue, today), form=bool(C.value(cfg, "review.group_alike")))


def _read(conn: sqlite3.Connection, cfg: dict, today: date, row: dict, queue: dict | None = None) -> tuple[dict, Plan]:
    queue = review_queue.items(conn, cfg, today) if queue is None else queue
    return queue, _plan(conn, cfg, today, row, queue)


def _drivers(row: dict) -> dict:
    try:
        d = json.loads(row.get("drivers_json") or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def _speaking(row: dict) -> str | None:
    """The connector or skill when it last looked under SPEAKING_S ago (stage 3 writes it through `seen`)."""
    now = datetime.now(timezone.utc)
    for drv in ("connector", "skill"):
        at = (_drivers(row).get(drv) or {}).get("at")
        try:
            t = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if (now - t).total_seconds() < SPEAKING_S:
            return drv
    return None


# --- one write to a pass -----------------------------------------------------------------------------------

class _Write:
    """What one call changes on a pass: the arrivals persisted, the items arrived at (with the offered stamp), the
    row's columns; saved once with `rev + 1` where `rev` is still what was read (compare-and-set)."""

    def __init__(self, conn: sqlite3.Connection, cfg: dict, today: date, row: dict, P: Plan, by: str | None):
        self.conn, self.cfg, self.today, self.row, self.P, self.by = conn, cfg, today, row, P, by
        self.upd: dict = {}
        self.dirty = False

    def get(self, col):
        return self.upd.get(col, self.row[col])

    def set(self, **cols):
        self.upd.update(cols)
        self.dirty = True

    def persist(self):
        """The plan as it reads: groups left with one member removed, members that left a group written, the arrivals
        inserted (a new group with its members), and an end with something undecided back on an item."""
        now = now_iso()
        pid = self.row["id"]
        for g in self.P.dissolved:
            self.conn.execute("DELETE FROM review_pass_items WHERE pass_id = ? AND key = ?", (pid, g))
            self.dirty = True
        for g, ids in self.P.regrouped.items():
            self.conn.execute("UPDATE review_pass_items SET members_json = ? WHERE pass_id = ? AND key = ?", (json.dumps(ids), pid, g))
            self.dirty = True
        for k, p, kind in self.P.arrivals:
            mem = json.dumps([int(m[2:]) for m in self.P.members[k]]) if kind == "group" else None
            self.conn.execute("INSERT OR IGNORE INTO review_pass_items (pass_id, key, pos, kind, members_json, done_batch_id, added_at)"
                              " VALUES (?, ?, ?, ?, ?, ?, ?)", (pid, k, p, kind, mem, self.P.batch.get(k), now))
            self.dirty = True
        replaced = self.get("cursor_key") in self.P.dissolved           # the group in front became its one task: not a step forward
        self.P.dissolved, self.P.regrouped, self.P.arrivals = {}, {}, []      # written: a second persist writes only what is new
        if self.P.state != self.get("state") or self.P.cursor != self.get("cursor_key"):
            if self.P.cursor and self.P.state == "item":
                self.arrive(self.P.cursor, forward=not replaced)
            else:
                self.set(state=self.P.state, cursor_key=self.P.cursor)

    def arrive(self, key: str, forward: bool):
        """The item comes in front (§6.4): arrived_at if null, the offered stamp on its task (a card: none), and on a
        forward move one more looked at."""
        self.set(cursor_key=key, state="item")
        if forward:
            self.set(looked=self.get("looked") + 1)
        day = self.today.isoformat()
        self.conn.execute("UPDATE review_pass_items SET arrived_at = COALESCE(arrived_at, ?) WHERE pass_id = ? AND key = ?",
                          (now_iso(), self.row["id"], key))
        mem = self.P.members.get(key, [key] if key.startswith("t:") else [])     # a group: every member
        ids = [int(m[2:]) for m in mem if m[2:].isdigit()]
        if ids:
            self.conn.execute(f"UPDATE tasks SET last_offered_at = ? WHERE id IN ({','.join('?' * len(ids))})"
                              " AND (last_offered_at IS NULL OR last_offered_at < ?)", (day, *ids, day))
            for m in mem:
                e = self.P.entries.get(m)
                if e and (e["task"].get("last_offered_at") or "") < day:
                    e["task"]["last_offered_at"] = day

    def forward(self, target: str):
        """A forward move to `target`: the checkpoint (the cursor already on it, not yet in front) once `looked`
        reached the cap, else arrival."""
        if self.get("looked") >= _cap(self.cfg):
            self.set(state="checkpoint", cursor_key=target)
        else:
            self.arrive(target, forward=True)

    def save(self, moved: bool = True) -> bool:
        cols = dict(self.upd)
        if moved:
            cols.update(moved_at=now_iso(), moved_by=self.by)
        sets = "".join(f"{c} = ?, " for c in cols)
        n = self.conn.execute(f"UPDATE review_passes SET {sets}rev = rev + 1 WHERE id = ? AND rev = ?",
                              (*cols.values(), self.row["id"], self.row["rev"])).rowcount
        return n == 1


# --- begin, move, decide, end ------------------------------------------------------------------------------

def begin_or_resume(conn: sqlite3.Connection, cfg: dict, today: date, *, by: str, fresh: bool = False) -> dict:
    """The live pass of Review's day, resumed (nothing rebuilt; arrivals persisted), or a new one: a live pass from
    another day ends `another_day` first, and `fresh` ends a live one `replaced`. Beginning writes, in one
    transaction, the day's link suggestions (`suggestions.session`, before the queue is read), the pass and its items
    in Review's order, and the arrival of the first undecided item. A resume asks `suggestions.session` again, which
    tops up the day's set while fewer than the day's number were offered (what GET /api/review did on every load). Two begins at once make one pass: the second waits
    for the write lock and resumes (and on a connection already inside a transaction the unique index refuses the
    second row, and it resumes). Answers the View. No commit."""
    begin_write(conn)
    row = live(conn)
    if row is not None and row["day"] != today.isoformat():
        end(conn, row["id"], "another_day")
        row = None
    elif row is not None and fresh:
        end(conn, row["id"], "replaced")
        row = None
    if row is None:
        suggestions.session(conn, today, suggestions.per_review(cfg))
        queue = review_queue.items(conn, cfg, today)
        now = now_iso()
        try:
            pid = conn.execute("INSERT INTO review_passes (day, started_at, started_by, state, moved_at, moved_by) VALUES (?, ?, ?, 'empty', ?, ?)",
                               (today.isoformat(), now, by, now, by)).lastrowid
        except sqlite3.IntegrityError:                # another process began one inside our transaction: resume it
            row = live(conn)
            if row is None:
                raise
        else:
            row = _row(conn, pid)
            _, P = _read(conn, cfg, today, row, queue)       # every item arrives, in Review's order, alike new tasks grouped
            w = _Write(conn, cfg, today, row, P, by)
            w.persist()                                      # and the first undecided comes in front
            if not P.undecided():
                w.set(state="end" if P.shown() else "empty", cursor_key=None)
            w.save()
            return _view(conn, cfg, today, _row(conn, pid), queue)
    suggestions.session(conn, today, suggestions.per_review(cfg))      # tops up the day's set (review 2026-11-20): what the
    queue, P = _read(conn, cfg, today, row)                           # detectors found since the begin arrives
    w = _Write(conn, cfg, today, row, P, by)
    w.persist()
    if w.dirty:
        w.save(moved=False)
    return _view(conn, cfg, today, _row(conn, row["id"]), queue)


def move(conn: sqlite3.Connection, cfg: dict, today: date, pass_id: int, to: str, *, key: str | None = None,
         from_key: str | None = None, by: str | None) -> dict:
    """One move of the cursor (§6.3). `next` and `back` must name the item in front (`from_key`; None at the end),
    else {moved: False, why: "moved", by} and nothing is written. Next marks an undecided item in front passed and goes
    to the next undecided item, coming round to the first from the start ({came_round, left}); from a checkpoint it goes
    on. Back goes to the previous item that is not gone ({moved: False, why: "first"} at the first). `goto` names an
    item that is not gone (Refused otherwise); `first` the first undecided; `continue` leaves a checkpoint; `end` ends
    the pass `finished` and leaves the tray. Raises NoPass, Ended, Refused. No commit (a caller rolls back a
    `moved: False`)."""
    if to not in MOVES:
        raise Refused(f"unknown move {str(to)[:20]!r}")
    begin_write(conn)
    row = _live_row(conn, pass_id, today)
    if to == "end":
        end(conn, pass_id, "finished")
        return {"moved": True, "ended": True}
    _, P = _read(conn, cfg, today, row)
    cur = P.cursor
    if to in ("next", "back") and (from_key or None) != cur:
        return {"moved": False, "why": "moved", "by": row["moved_by"] or row["started_by"]}
    w = _Write(conn, cfg, today, row, P, by)
    w.persist()
    out: dict = {"moved": True}
    if to in ("next", "continue") and P.state == "checkpoint":
        w.set(looked=0)
        w.arrive(cur, forward=True)
    elif to == "next":
        if cur and P.states.get(cur) in UNDECIDED:
            conn.execute("UPDATE review_pass_items SET passes = passes + 1, passed_at = ? WHERE pass_id = ? AND key = ?", (now_iso(), row["id"], cur))
            P.states[cur] = "passed"
        target, came = _after(P, cur)
        if target is None:
            w.set(state="end", cursor_key=None)
        else:
            if came:
                w.set(lap=row["lap"] + 1)
                out.update(came_round=True, left=len(P.undecided()))
            w.forward(target)
    elif to == "back":
        shown = P.shown()
        if P.state == "end" or cur is None:
            target = shown[-1] if shown else None
        else:
            before = [k for k in shown if P.pos[k] < P.pos[cur]]
            target = before[-1] if before else None
        if target is None:
            return {"moved": False, "why": "first"}
        w.arrive(target, forward=False)
    elif to == "goto":
        key = P.member_of.get(key, key) if isinstance(key, str) else key       # a group's member: the group
        if not isinstance(key, str) or key not in P.pos or P.states[key] == "gone":
            raise Refused(WORDS["no_item"])
        w.arrive(key, forward=False)
    elif to == "first":
        und = P.undecided()
        if und:
            w.arrive(und[0], forward=True)
        else:
            w.set(state="end", cursor_key=None)
    if not w.save():
        return {"moved": False, "why": "moved", "by": row["moved_by"] or row["started_by"]}
    return out


def decide(conn: sqlite3.Connection, cfg: dict, today: date, pass_id: int, key: str, option, *, args: dict | None = None,
           note: str | None = None, from_chat: dict | None = None, origin: str, device: str | None = None, by: str | None) -> dict:
    """One option on one item, staged by the rule (`review_decide.stage`), then the cursor: a decision that settles
    the item in front moves on by the rule of Next (nothing marked passed; the checkpoint as Next), `undecide` puts
    the item in front, anything else moves nothing; on an item in front that was settled before the call only a
    decision moves on (`_decision`), so Pin, Long-term or an answer pressed there after Back stays on it. A key the pass does not hold, or holds as gone, is staged as the
    rule allows (an open task outside the queue still takes a decision from its card's conversation; Gone only when
    the rule says so). `rec` stages every step of the item's recommendation (`_stage_steps`; Refused when the item has
    none, or recommendations are off).

    A group (2d): the tray never sees its key. `keep_all` (and `rec`, which is Keep all there) stages one Keep per
    undecided member, and each `except` member ({key, option, args?, note?}) as given, every one its own row through
    the rule, in one write; no note on the group itself. A member is decided by its own key (`t:<id>`): the group stays
    in front until the last undecided member is decided, and `undecide` on a member puts the group in front.
    Answers the stage's {keys, removed, said} and `advanced`. Raises NoPass, Ended, Refused, Gone, Stale. No commit."""
    begin_write(conn)
    row = _live_row(conn, pass_id, today)
    args = dict(args or {})
    if note is not None:
        args["note"] = note
    queue, P = _read(conn, cfg, today, row)
    w = _Write(conn, cfg, today, row, P, by)
    w.persist()
    group = P.member_of.get(key)                         # a member decided by itself: its group is the item
    item = group or key
    front = P.state == "item" and P.cursor == item
    rec = None
    shown = _shown(args) if from_chat is None and option == "rec" else None
    if from_chat is None and key in P.members:
        if option == "rec":
            rec = _group_rec(cfg, today, P, key)
            und = sorted(m for m in P.members[key] if P.mstates[m] in UNDECIDED)
            if shown[1] is None:
                raise Refused(WORDS["rec_members"])
            if not rec or shown[0] != rec["steps"] or sorted(shown[1]) != und:
                raise review_decide.Stale(WORDS["rec_changed"])
        elif option != "keep_all":
            raise Refused(f"{str(option)[:20]} is not offered on a group: Keep all, or decide its tasks one by one")
        out = _stage_many(conn, cfg, today, _keep_all_steps(P, key, args), origin=origin, device=device)
    elif from_chat is None and option == "keep_all":
        raise Refused(f"{option} is not offered on this item")
    elif from_chat is None and option == "rec":
        rec = (P.entries.get(key) or {}).get("recommendation")
        if not rec or shown[0] != rec["steps"]:
            raise review_decide.Stale(WORDS["rec_changed"])
        out = _stage_steps(conn, cfg, today, key, rec["steps"], args, origin=origin, device=device)
    else:
        out = review_decide.stage(conn, cfg, key, option, args, today, origin=origin, device=device, from_chat=from_chat)
    P2 = _plan(conn, cfg, today, dict(row, **w.upd), queue)
    w.P = P2
    advanced = False
    back_to = P2.member_of.get(key, key)
    if from_chat is None and option == "undecide" and back_to in P2.pos and P2.states[back_to] != "gone":
        w.arrive(back_to, forward=False)
    elif front and P2.states.get(item) in SETTLED and (P.states.get(item) in UNDECIDED or
                                                        (group is None and _decision(P.entries.get(key), key, option if option != "rec" or from_chat is not None else rec))):
        target, _ = _after(P2, item)
        if target is None:
            w.set(state="end", cursor_key=None)
        else:
            w.forward(target)
        advanced = True
    if not w.save():
        raise review_decide.Stale(WORDS["moved_meanwhile"])
    return dict(out, advanced=advanced)


def _shown(args: dict) -> tuple[list[str], list[str] | None]:
    """What a `rec` was shown (review 2026-11-20): `steps`, the recommendation's step list as drawn or said, and on a
    group `members`, the member keys drawn undecided. `decide` stages `rec` only when the recommendation worked out now
    is that one (else Stale: the item is read again), so evidence that came after the View never turns a yes into an
    answer nobody saw. Both leave `args` here; Refused when `steps` is missing or not a list of option ids."""
    steps = args.pop("steps", None)
    members = args.pop("members", None)
    if not isinstance(steps, list) or not steps or len(steps) > 20 or not all(isinstance(x, str) and 0 < len(x) <= 40 for x in steps):
        raise Refused(WORDS["rec_steps"])
    if members is not None and (not isinstance(members, list) or len(members) > GROUP_MAX
                                or not all(isinstance(x, str) and len(x) <= 40 for x in members)):
        raise Refused(WORDS["rec_members"])
    return steps, members


def _group_rec(cfg: dict, today: date, P: Plan, gk: str) -> dict | None:
    """A group's recommendation (Keep all; recommend.recommend), or None when recommendations are off or nothing is undecided."""
    if not C.value(cfg, "review.recommend") or P.states.get(gk) == "gone":
        return None
    ms = P.members.get(gk, [])
    return recommend.recommend({"key": gk, "members": [{"key": m, "state": P.mstates[m]} for m in ms],
                                "undecided": [m for m in ms if P.mstates[m] in UNDECIDED]}, today)


def _keep_all_steps(P: Plan, gk: str, args: dict) -> list[tuple[str, str, dict]]:
    """Keep all on a group, as (member key, option, args) steps: Keep on each undecided member, the `except` members as
    given (their own option, args and note: the rule checks them). A note on the group is refused (it belongs to one
    task), so is an exception that names no member or a pass-only option; nothing left to decide is refused too."""
    if args.get("note") not in (None, ""):
        raise Refused(WORDS["group_note"])
    ms = P.members[gk]
    ex = args.get("except")
    ex = [] if ex is None else ex
    if not isinstance(ex, list) or len(ex) > GROUP_MAX:
        raise Refused(WORDS["bad_except"])
    spec: dict[str, tuple[str, dict]] = {}
    for x in ex:
        if not isinstance(x, dict) or x.get("key") not in ms or not isinstance(x.get("option"), str) \
                or x["option"] in PASS_ONLY or x["option"] == "undecide" or not isinstance(x.get("args") or {}, dict):
            raise Refused(WORDS["bad_except"])
        a = {k: v for k, v in (x.get("args") or {}).items() if k not in review_decide.FROM_CHAT_ARGS}
        if x.get("note") not in (None, ""):
            a["note"] = x["note"]
        spec[x["key"]] = (x["option"], a)
    steps = [(m, *spec[m]) if m in spec else (m, "keep", {}) for m in ms if m in spec or P.mstates[m] in UNDECIDED]
    if not steps:
        raise Refused(WORDS["group_done"])
    return steps


def _decision(entry: dict | None, key: str, option) -> bool:
    """Whether an option is a decision (design §4.1): done, drop, keep, later; Create or Dismiss on a card; an accepted
    done or drop proposal; a recommendation (given as its dict) whose steps hold one. On an item in front that was
    settled already (reached with Back), only a decision moves on: Pin, Long-term or an answer there changes what stands
    beside it and leaves the cursor where the owner is (review 2026-11-20). A decision changed there moves on, as the
    page draws at the click."""
    if isinstance(option, dict):
        return any(_decision(entry, key, s) for s in option.get("steps") or [])
    if not isinstance(option, str):
        return False
    if option == "keep_all":
        return True
    if key.startswith("s:"):
        return option in ("create", "dismiss")
    if option in ("done", "drop", "keep", "later"):
        return True
    if option.startswith("accept:") and option[7:].isdigit():
        eid = int(option[7:])
        for e in (entry or {}).get("evidence") or []:
            if e.get("id") == eid and isinstance(e.get("change"), dict):
                return e["change"].get("op") in ("done", "drop")
    return False


NOTE_TAKERS = ("done", "drop", "keep", "later")      # the options a Review note rides on (review_decide.plan)


def _stage_steps(conn: sqlite3.Connection, cfg: dict, today: date, key: str, steps: list[str], args: dict, *, origin: str,
                 device: str | None) -> dict:
    """The option `rec`: every step of the item's recommendation planned by the rule against the tray as the steps
    before it leave it, then one write (`_stage_many`): the same rows the same presses one by one would stage. A note
    rides on the last step that takes one (Refused when none does)."""
    note = args.get("note")
    takers = [s for s in steps if s in NOTE_TAKERS]
    if note not in (None, "") and not takers:
        raise Refused("a note goes with Done, Drop, Keep or Later; this answer has none")
    return _stage_many(conn, cfg, today, [(key, s, {"note": note} if note not in (None, "") and s == takers[-1] else {}) for s in steps],
                       origin=origin, device=device)


def _stage_many(conn: sqlite3.Connection, cfg: dict, today: date, steps: list[tuple[str, str, dict]], *, origin: str,
                device: str | None) -> dict:
    """Several options on one or more items, each planned by the rule (`review_decide.plan`) against the tray as the
    steps before it leave it, then one `tray.write` (Trap 13): the same rows the same presses one by one would stage.
    Answers {keys, removed, said}."""
    items: dict[str, dict] = {}
    tr = tray.read(conn)
    rows_ = [[k, e, dict(m or {})] for k, e, m in _trows(tr["entries"])]
    ops: list[dict] = []
    keys: list[str] = []
    said = ""
    for key, s, a in steps:
        if key not in items:
            items[key] = review_decide.load_item(conn, cfg, today, key)
        p = review_decide.plan(conn, cfg, items[key], s, a, {"rev": tr["rev"], "entries": rows_}, today)
        rows_ = [r for r in rows_ if r[0] not in p.dels]
        for k, e, d in p.puts:
            d = tray.decides_of(k, e, d)
            for r in rows_:
                if r[0] == k:
                    r[1], r[2] = e, {"decides": d}
                    break
            else:
                rows_.append([k, e, {"decides": d}])
        ops += [{"del": k} for k in p.dels] + [{"put": k, "entry": e, "decides": d} for k, e, d in p.puts]
        keys = [k for k in keys if k not in p.dels] + [k for k, _, _ in p.puts if k not in keys]
        said = p.said or said
    tray.write(conn, ops, origin, device)
    staged = {r[0] for r in rows_}
    return {"keys": [k for k in keys if k in staged], "removed": sorted({o["del"] for o in ops if "del" in o} - staged),
            "said": said if said == review_decide.SAID["long_note"] else ""}


def regroup(conn: sqlite3.Connection, cfg: dict, today: date, pass_id: int, key: str, op: str, *, task_id: int | None = None,
            by: str | None) -> dict:
    """Change a group of the pass (2d): `take_out` one member (`task_id`, or `key` naming the member): it becomes its
    own item right after the group; `one_by_one`: the group becomes its members in its place, and when it was in front
    the first undecided of them comes in front. `key` is the group or one of its members. A group left with one member
    is that task (the plan's rule, written here too). Nothing is staged and the tray is untouched. Answers {regrouped,
    key}: the member taken out, or the item now in its place. Raises NoPass, Ended, Refused, Stale. No commit."""
    if op not in REGROUPS:
        raise Refused(f"unknown change {str(op)[:20]!r}")
    begin_write(conn)
    row = _live_row(conn, pass_id, today)
    queue, P = _read(conn, cfg, today, row)
    w = _Write(conn, cfg, today, row, P, by)
    w.persist()
    gk = key if isinstance(key, str) and key in P.members else P.member_of.get(key) if isinstance(key, str) else None
    if gk is None or P.states.get(gk) == "gone":
        raise Refused(WORDS["not_group"])
    ms = P.members[gk]
    p = P.pos[gk]
    nxt = min((q for q in P.pos.values() if q > p), default=p + 1)
    grow = next((r for r in _stored(conn, row["id"]) if r["key"] == gk), {})
    carry = lambda m: grow.get("done_batch_id") if m not in P.entries else None       # an applied member keeps its Apply
    if op == "take_out":
        m = f"t:{task_id}" if type(task_id) is int else key if key in P.member_of else None
        if m not in ms:
            raise Refused(WORDS["not_member"])
        conn.execute("UPDATE review_pass_items SET members_json = ? WHERE pass_id = ? AND key = ?",
                     (json.dumps([int(x[2:]) for x in ms if x != m]), row["id"], gk))
        conn.execute("INSERT OR IGNORE INTO review_pass_items (pass_id, key, pos, kind, done_batch_id, added_at) VALUES (?, ?, ?, 'task', ?, ?)",
                     (row["id"], m, p + (nxt - p) / 2, carry(m), now_iso()))
        out = {"regrouped": op, "key": m}
    else:
        live = [m for m in ms if P.mstates[m] != "gone"]
        conn.execute("DELETE FROM review_pass_items WHERE pass_id = ? AND key = ?", (row["id"], gk))
        conn.executemany("INSERT OR IGNORE INTO review_pass_items (pass_id, key, pos, kind, done_batch_id, added_at) VALUES (?, ?, ?, 'task', ?, ?)",
                         [(row["id"], m, p + (nxt - p) * i / (len(live) + 1), carry(m), now_iso()) for i, m in enumerate(live)])
        first = next((m for m in live if P.mstates[m] in UNDECIDED), live[0] if live else None)
        out = {"regrouped": op, "key": first}
        if P.cursor == gk and first is not None:
            w.P = _plan(conn, cfg, today, dict(row, **w.upd), queue)
            w.arrive(first, forward=False)
    w.P = _plan(conn, cfg, today, dict(row, **w.upd), queue)
    w.persist()
    if not w.save():
        raise review_decide.Stale(WORDS["moved_meanwhile"])
    return out


def end(conn: sqlite3.Connection, pass_id: int, why: str) -> None:
    """Ends the pass (`applied`, `finished`, `replaced`, `another_day`). Never touches the tray or a task. No commit."""
    if why not in ENDS:
        raise ValueError(f"unknown end {why!r}")
    begin_write(conn)
    conn.execute("UPDATE review_passes SET ended_at = ?, end_why = ?, rev = rev + 1 WHERE id = ? AND ended_at IS NULL", (now_iso(), why, pass_id))


def after_apply(conn: sqlite3.Connection, decided, batch_id: int | None, *, cfg: dict | None = None, today: date | None = None) -> None:
    """tray.apply's follow-up: the live pass's items whose decisions the batch applied get its `done_batch_id` (they
    stay decided once they leave the queue; if the batch is undone and they come back, they are undecided again). With
    `cfg` and `today` (Review's day), an Apply that leaves nothing undecided ends the pass `applied`. Starts its own
    write (tray.apply's chat follow-up committed before it). No commit."""
    begin_write(conn)
    row = live(conn)
    if row is None:
        return
    keys = sorted(k for k in (decided or ()) if isinstance(k, str))
    changed = False
    if keys and batch_id is not None:
        groups = [r["key"] for r in _stored(conn, row["id"]) if r["kind"] == "group" and any(f"t:{i}" in keys for i in _member_ids(r))]
        keys = sorted(set(keys) | set(groups))                 # a group whose member's decision it applied: the member stays decided
        changed = conn.execute(f"UPDATE review_pass_items SET done_batch_id = ? WHERE pass_id = ? AND key IN ({','.join('?' * len(keys))})",
                               (batch_id, row["id"], *keys)).rowcount > 0
    if cfg is not None and today is not None and row["day"] == today.isoformat():
        _, P = _read(conn, cfg, today, row)
        if not P.undecided():
            end(conn, row["id"], "applied")
            return
    if changed:
        conn.execute("UPDATE review_passes SET rev = rev + 1 WHERE id = ?", (row["id"],))


def seen(conn: sqlite3.Connection, pass_id: int, driver: str) -> None:
    """What a driver has now seen (stage 3 reads it to say what changed on the screen meanwhile): the tray's
    revision, the decided items and the cursor, with the time. Not a move: `rev` stays. No commit."""
    if driver not in DRIVERS:
        raise Refused(f"unknown driver {str(driver)[:20]!r}")
    begin_write(conn)
    row = _row(conn, pass_id)
    d = _drivers(row)
    d[driver] = {"tray_rev": tray.rev(conn), "decided": sorted(tray.decided(conn)), "cursor": row["cursor_key"], "at": now_iso()}
    conn.execute("UPDATE review_passes SET drivers_json = ? WHERE id = ?", (json.dumps(d), pass_id))


# --- reads -------------------------------------------------------------------------------------------------

def now(conn: sqlite3.Connection) -> dict:
    """What a follower compares (§6.5): {pass_id, rev, state, cursor_key, tray_rev, speaking, day} from two small
    reads, the pass row as stored. Reads only; takes no lock."""
    row = live(conn)
    if row is None:
        return {"pass_id": None, "rev": None, "state": None, "cursor_key": None, "tray_rev": tray.rev(conn), "speaking": None, "day": None}
    return {"pass_id": row["id"], "rev": row["rev"], "state": row["state"], "cursor_key": row["cursor_key"],
            "tray_rev": tray.rev(conn), "speaking": _speaking(row), "day": row["day"]}


def view(conn: sqlite3.Connection, cfg: dict, today: date, *, spoken: bool = True) -> dict | None:
    """The View of the live pass of Review's day (§6.6), or None (no live pass, or one from another day). Reads only.
    `spoken` False leaves out the sentences for the ear (`current.spoken`, `ahead.spoken`, a member's `spoken`)."""
    row = live(conn)
    if row is None or row["day"] != today.isoformat():
        return None
    return _view(conn, cfg, today, row, spoken=spoken)


def _now_on(task: dict, trows) -> tuple[bool, bool]:
    """Pinned and long-term as the task will be once the tray applies (the rule's reading of `<id>:top` / `<id>:lt`)."""
    whole = {k: e for k, e, _ in trows}
    tid = task.get("id")
    top, lt = whole.get(f"{tid}:top"), whole.get(f"{tid}:lt")
    pinned = bool(task.get("top_rank")) if top is None else (top.get("op") == "pin_top" or (top.get("op") == "set" and bool((top.get("fields") or {}).get("top_rank"))))
    longterm = bool(task.get("longterm")) if lt is None else (lt.get("op") == "longterm" or bool((lt.get("fields") or {}).get("longterm")))
    return pinned, longterm


def _opt(id_: str, label: str, kind: str, key: str | None = None, **more) -> dict:
    return dict({"id": id_, "label": label, "kind": kind}, **({"key": key} if key else {}), **more)


def options(entry: dict, rec, tray_entries, today: date) -> list[dict]:
    """What the owner may choose on an item, as option ids (design §4.2) with the page's words: on an open task the
    four decisions (keys d k s x), an answer for each proposal and mail on it and each suggested link (`staged` when the
    tray already holds one), Pin and Long-term beside (t l, worded by what the tray will make them); on a waiting card
    Create and Dismiss; Take back on anything the tray decides; first the recommendation when there is one. Pure."""
    key = review_decide.item_key(entry)
    trows = _trows(tray_entries)
    keys = {k for k, _, _ in trows}
    evs = {e.get("ev") for _, e, _ in trows if type(e.get("ev")) is int}
    out: list[dict] = []
    if rec:
        out.append(_opt("rec", str(rec.get("label") or ""), "rec", "a"))
    if key.startswith("s:"):
        if entry.get("fields") is not None:
            out += [_opt("create", WORDS["create"], "card"), _opt("dismiss", WORDS["card_dismiss"], "card")]
    else:
        t = entry.get("task") or {}
        if t.get("status") == "open":
            out += [_opt("done", WORDS["done"], "decision", "d"), _opt("keep", WORDS["keep"], "decision", "k"),
                    _opt("later", WORDS["later"], "decision", "s"), _opt("drop", WORDS["drop"], "decision", "x")]
            for e in entry.get("evidence") or []:
                ch = e.get("change")
                if not isinstance(ch, dict):
                    continue
                pair = (("follow", WORDS["follow"]), ("not_about", WORDS["not_about"])) if ch.get("op") == "follow_thread" \
                    else (("accept", WORDS["accept"]), ("dismiss", WORDS["dismiss"]))
                more = {"staged": True} if f"e:{e['id']}" in keys else {}
                out += [_opt(f"{n}:{e['id']}", w, "answer", evidence=e["id"], **more) for n, w in pair]
            for s in entry.get("link_suggestions") or []:
                E = s["evidence_id"]
                if s.get("kind") == "same_as":
                    pair = (("merge", WORDS["merge_into"] + str(s["keep"]) if s.get("keep") is not None else WORDS["merge"]), ("not_related", WORDS["not_same"]))
                elif s.get("kind") in ("part_of", "waits_on"):
                    pair = (("link", WORDS["link"]), ("not_related", WORDS["not_related"]))
                else:
                    pair = (("step", WORDS["step"]), ("waits", WORDS["waits"]), ("same", WORDS["same"]), ("not_related", WORDS["not_related"]))
                more = {"staged": True} if E in evs or f"e:{E}" in keys else {}
                out += [_opt(f"{n}:{E}", w, "answer", evidence=E, **more) for n, w in pair]
            pinned, longterm = _now_on(t, trows)
            out += [_opt("pin", WORDS["unpin" if pinned else "pin"], "beside", "t"),
                    _opt("longterm", WORDS["not_longterm" if longterm else "longterm"], "beside", "l")]
    if any(m.get("decides") == key for _, _, m in trows):
        out.append(_opt("undecide", WORDS["undecide"], "take_back"))
    return out


def _staged(key: str, trows) -> dict:
    """What the tray holds for an item: its decision (the rows that decide it, the label of its main row) and the
    rows beside it on the same task that decide nothing."""
    mine = [(k, e) for k, e, m in trows if m.get("decides") == key]
    n = key[2:]
    main = next((r for r in mine if r[0] in (n, f"e:{n}")), mine[0] if mine else None)
    decision = {"keys": [k for k, _ in mine], "label": main[1].get("label") or "", "op": main[1].get("op")} if main else None
    beside = []
    if key.startswith("t:") and n.isdigit():
        tid = int(n)
        for k, e, m in trows:
            if m.get("decides"):
                continue
            parts = k.split(":")
            if e.get("task_id") == tid or parts[0] == n or (parts[0] == "rel" and len(parts) == 4 and n in (parts[1], parts[3])):
                beside.append({"key": k, "label": e.get("label") or ""})
    return {"decision": decision, "beside": beside}


def _card_why(e: dict) -> str:
    if e.get("kind") == "slack_mention":
        return WORDS["card_slack"]
    if e.get("by") == "ripple":
        return WORDS["card_ripple"]
    return WORDS["card_mail"] if e.get("mail") else WORDS["card_planner"]


def _settled(conn: sqlite3.Connection, cfg: dict, today: date, key: str, state: str) -> tuple[dict, str]:
    """An item no longer in the queue, as far as it can be shown, and why it is settled."""
    kind, n = review_decide.parse_item_key(key)
    why = WORDS["settled_applied"] if state == "decided" else None
    if kind == "t":
        try:
            item = review_decide.load_item(conn, cfg, today, key)          # open, with no reason today (or set for later)
            later = str(item["task"].get("snoozed_until") or "")[:10] > today.isoformat()
            return item, why or WORDS["settled_later" if later else "settled_quiet"]
        except Gone:
            t = get_task(conn, n)
        if t is None:
            return {"key": key, "task": None}, why or WORDS["settled_missing"]
        keep = ("id", "name", "status", "due", "hard", "project", "context", "outcome", "top_rank", "longterm")
        return {"key": key, "task": {k: t.get(k) for k in keep}}, why or WORDS.get(f"settled_{t['status']}", WORDS["settled_quiet"])
    r = review_cards.row(conn, n)
    name = review_cards.suggestion_fields(cfg, r).get("name") if r else None
    return {"key": key, "id": n, "kind": r["kind"] if r else None, "label": r["label"] if r else None, "name": name}, why or WORDS["settled_card"]


def _entry(conn: sqlite3.Connection, cfg: dict, today: date, key: str | None, P: Plan, trows) -> dict | None:
    """One item as the View carries it: the queue's entry (today's shape, so the page's builders keep working) or,
    for one that left the queue, what is left of it with `settled`; its state, options, Later's days and what the tray
    holds for it."""
    if key is None:
        return None
    if key.startswith("g:"):
        return _group_entry(conn, cfg, today, key, P, trows)
    st = P.states.get(key, "gone")
    item, settled = P.entries.get(key), None
    if item is None:
        item, settled = _settled(conn, cfg, today, key, st)
    kind = P.kinds.get(key) or _kind(key)
    prov = None
    if kind == "task" and isinstance(item.get("task"), dict) and isinstance(item["task"].get("id"), int):      # where a task came from (2e)
        prov = (P.alike.get(key) or {}).get("prov") or (_alike_of(conn, cfg, [item["task"]]).get(item["task"]["id"]) or {}).get("prov")
    out = {"key": key, "kind": kind, "state": st, "item": item, "provenance": dict(prov) if prov else None,
           "evidence_lines": (item.get("evidence_lines") or []) if kind == "task" else [],
           "recommendation": None if settled else item.get("recommendation"), "rec_held": None if settled else item.get("rec_held"),
           "options": options(item, None if settled else item.get("recommendation"), trows, today),
           "later_choices": review_decide.later_choices(today) if kind == "task" else [],
           "staged": _staged(key, trows)}
    if settled:
        out["settled"] = settled
    return out


def _members(conn: sqlite3.Connection, cfg: dict, today: date, gk: str, P: Plan, trows, full: bool) -> list[dict]:
    """A group's members as the View carries them: each task item as the queue ships it (or what is left of it, for one
    that left the queue) with its key, state, what the tray decides for it (`staged`) and `applied`; `full` False keeps
    {key, name, due, state, staged, applied} only (the list)."""
    out = []
    for m in P.members.get(gk, []):
        st, it = P.mstates[m], P.entries.get(m)
        if it is None and full:
            it = _settled(conn, cfg, today, m, st)[0]
        d = _staged(m, trows)["decision"]
        applied = d is None and st == "decided"
        mine = {"key": m, "state": st, "staged": WORDS["applied"] if applied else d["label"] if d else None, "applied": applied}
        if full:
            out.append(dict(it or {}, **mine))
        else:
            t = (it or {}).get("task") or get_task(conn, int(m[2:])) or {}
            out.append(dict(mine, name=t.get("name"), due=review_decide.day_words(t["due"]) if isinstance(t.get("due"), str) and t["due"] else None))
    return out


def _provenance(conn: sqlite3.Connection, cfg: dict, P: Plan, gk: str) -> dict:
    """Where a group's tasks came from: {kind: recording | course | thread, title, day}, from its first member that says."""
    for m in P.members.get(gk, []):
        if m in P.alike:
            return dict(P.alike[m]["prov"])
    ts = [t for t in (get_task(conn, int(m[2:])) for m in P.members.get(gk, [])) if t]
    got = _alike_of(conn, cfg, ts)
    for t in ts:
        if t["id"] in got:
            return dict(got[t["id"]]["prov"])
    kind = {"rec": "recording", "course": "course", "thread": "thread"}.get(gk.split(":")[1] if gk.count(":") else "", "recording")
    return {"kind": kind, "title": None, "day": None}


def _from_words(prov: dict, today: date) -> str:
    """Where a group's tasks came from, after its why line (design 6.9): "yesterday's Project Meeting" for a recording,
    the course or the thread by its name; the day in the server's words (dates.say_whose), never the browser's."""
    title = one_line(str(prov.get("title") or ""))[:120].strip()
    whose = dates.say_whose(prov.get("day"), today) if prov.get("kind") == "recording" and prov.get("day") else ""
    return f"{whose} {title}".strip() if title else (f"{whose[:1].upper()}{whose[1:]} recording" if whose else "")


def _group_entry(conn: sqlite3.Connection, cfg: dict, today: date, gk: str, P: Plan, trows) -> dict:
    """A group as the View carries it (2d): `item` {label, provenance, members: [task item with key, state, staged,
    applied]}, its recommendation (Keep all), the options Keep all (key k) and, first, `rec`; Later's days for a
    member's Later. A group the pass no longer holds, or whose members all went, is settled."""
    st = P.states.get(gk, "gone")
    ms = _members(conn, cfg, today, gk, P, trows, True) if gk in P.members else []
    und = [m["key"] for m in ms if m["state"] in UNDECIDED]
    live = [m for m in ms if m["state"] != "gone"]
    prov = _provenance(conn, cfg, P, gk)
    rec = _group_rec(cfg, today, P, gk) if gk in P.members else None
    opts = ([_opt("rec", rec["label"], "rec", "a")] if rec else []) + \
        ([_opt("keep_all", recommend.keep_all_words(len(und), len(live))[0], "group", "k")] if und else [])
    out = {"key": gk, "kind": "group", "state": st, "item": {"key": gk, "label": WORDS["group_n"].format(n=len(live)), "why": WORDS.get(f"group_{prov['kind']}", ""),
                                                   "from_words": _from_words(prov, today), "provenance": prov, "members": ms},
           "provenance": prov, "evidence_lines": [], "recommendation": rec, "rec_held": None, "options": opts,
           "later_choices": review_decide.later_choices(today), "staged": {"decision": None, "beside": []}}
    if st == "gone" or gk not in P.members:
        out["settled"] = WORDS["settled_group"]
    return out


def _line(conn: sqlite3.Connection, cfg: dict, key: str, P: Plan, trows) -> dict:
    """One row of the View's list: {key, kind, name, why, state, staged, applied, due}; `applied` when the decision
    was applied by this pass's Apply (no tray row is left to take back; review 2026-11-20)."""
    if key in P.members:                                  # a group: its title, where it came from, and each member's state
        prov = _provenance(conn, cfg, P, key)
        ms = _members(conn, cfg, None, key, P, trows, False)
        n = sum(1 for m in ms if m["state"] != "gone")
        name = WORDS["group_n"].format(n=n) + (f" · {prov['title']}" if prov.get("title") else "")
        return {"key": key, "kind": "group", "name": name, "why": WORDS.get(f"group_{prov['kind']}", ""), "state": P.states[key],
                "staged": None, "applied": False, "due": None, "members": ms}
    e = P.entries.get(key)
    name = why = due = None
    if e is not None and "task" in e:
        name, why, due = e["task"].get("name"), lifecycle.REVIEW_LABELS.get(e.get("primary"), ""), e["task"].get("due")
    elif e is not None:
        name, why, due = (e.get("fields") or {}).get("name"), _card_why(e), (e.get("fields") or {}).get("due")
    elif key.startswith("t:") and key[2:].isdigit():
        t = get_task(conn, int(key[2:]))
        name, due = (t or {}).get("name"), (t or {}).get("due")
    elif key.startswith("s:") and key[2:].isdigit():
        r = review_cards.row(conn, int(key[2:]))
        name = review_cards.suggestion_fields(cfg, r).get("name") if r else None
    staged = _staged(key, trows)["decision"]
    applied = staged is None and P.states[key] == "decided"      # decided by an Apply of this pass (done_batch_id): nothing to take back
    return {"key": key, "kind": P.kinds.get(key) or _kind(key), "name": name, "why": why or "", "state": P.states[key],
            "staged": WORDS["applied"] if applied else staged["label"] if staged else None, "applied": applied,
            "due": review_decide.day_words(due) if isinstance(due, str) and due else None}


def _tz(cfg: dict):
    try:
        return ZoneInfo(str(C.get(cfg, "owner.timezone", "UTC")))
    except (KeyError, ValueError):
        return None


def _speak(conn: sqlite3.Connection, cfg: dict, today: date, entries: list[dict | None]) -> None:
    """`spoken` (spoken.entry) on each entry and on each member of a group, in place: the names its words may say read
    once (the queue's own, `recommend.names_for`, and every other task `spoken.wants`). Reads only."""
    es = [e for e in entries if e]
    if not es:
        return
    items = [e["item"] for e in es if isinstance(e.get("item"), dict)]
    for e in es:
        items += [m for m in (e.get("item") or {}).get("members") or [] if isinstance(m, dict)]
    names = recommend.names_for(conn, items)
    want = set()
    for e in es:
        want |= spoken_words.wants(e)
        for m in (e.get("item") or {}).get("members") or []:
            want |= spoken_words.wants({"item": m})
    from .relations import briefs
    for i, b in briefs(conn, want - set(names)).items():
        if b.get("name"):
            names[i] = b["name"]
    tz = _tz(cfg)
    for e in es:
        e["spoken"] = spoken_words.entry(e, today, names, tz=tz)
        for m in (e.get("item") or {}).get("members") or []:
            m["spoken"] = spoken_words.entry({"key": m.get("key"), "kind": "task", "state": m.get("state"), "item": m, "provenance": e.get("provenance"),
                                              "recommendation": m.get("recommendation"), "options": [], "staged": {"decision": None, "beside": []}},
                                             today, names, tz=tz)


def _view(conn: sqlite3.Connection, cfg: dict, today: date, row: dict, queue: dict | None = None, *, spoken: bool = True) -> dict:
    queue = review_queue.items(conn, cfg, today) if queue is None else queue
    tr = tray.read(conn)
    trows = _trows(tr["entries"])
    P = _plan(conn, cfg, today, row, queue, tr["entries"])
    shown, und = P.shown(), P.undecided()
    cur = P.cursor if P.state in ("item", "checkpoint") else None
    counts = {"items": len(shown), "position": shown.index(cur) + 1 if cur in shown else None, "undecided": len(und),
              "decided": sum(1 for k in shown if P.states[k] in SETTLED), "passed": sum(1 for k in shown if P.states[k] == "passed"),
              "left": review_queue.undecided_count(conn, cfg, today), "staged": len(trows),
              "minutes_left": math.ceil(len(und) * SECONDS_PER_ITEM / 60)}
    ahead = _after(P, cur, skip_cur=True)[0] if P.state == "item" and cur else None
    current, ahead_entry = _entry(conn, cfg, today, cur, P, trows), _entry(conn, cfg, today, ahead, P, trows)
    if spoken:
        _speak(conn, cfg, today, [current, ahead_entry])
    return {"id": row["id"], "rev": row["rev"], "day": row["day"], "today": today.isoformat(), "state": P.state, "cursor_key": cur,
            "lap": row["lap"], "started_by": row["started_by"], "moved_by": row["moved_by"], "moved_at": row["moved_at"],
            "tray_rev": tr["rev"], "speaking": _speaking(row), "counts": counts, "checkpoint": {"looked": row["looked"], "cap": _cap(cfg)},
            "labels": lifecycle.REVIEW_LABELS, "current": current, "ahead": ahead_entry, "list": [_line(conn, cfg, k, P, trows) for k in P.order]}
