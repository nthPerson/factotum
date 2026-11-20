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
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone

from . import config as C, lifecycle, review_cards, review_decide, review_queue, suggestions, tray
from .db import begin_write, get_task, now_iso, rows

URGENT_DAYS = review_queue.URGENT_DAYS        # an arrival this urgent (order_key's tier 0) goes right after the item in front
GROUP_MIN, GROUP_MAX = 2, 8                   # alike new tasks together (2d)
SECONDS_PER_ITEM = 12                         # for "~n min" left
SPEAKING_S = 600                              # a connector's or skill's last look this recent: it is speaking
MOVES = ("next", "back", "goto", "first", "continue", "end")
ENDS = ("applied", "finished", "replaced", "another_day")
DRIVERS = ("page", "connector", "skill")
PASS_ONLY = ("rec", "keep_all")               # options only a pass offers (2c, 2d); the rule refuses every other option itself
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
    "create": "Create", "card_dismiss": "Dismiss", "undecide": "Take back",
    "settled_done": "It is done.", "settled_dropped": "It was dropped.", "settled_faded": "It faded.", "settled_lapsed": "It lapsed.",
    "settled_later": "It is set for later.", "settled_quiet": "Nothing on it needs a decision now.",
    "settled_card": "The suggestion was answered.", "settled_applied": "Its decision was applied.", "settled_missing": "It no longer exists.",
    "card_mail": "From your mail", "card_slack": "Mentioned in Slack", "card_ripple": "Ripple suggests", "card_planner": "Planner suggests",
    "ended": "this review has ended", "no_item": "that item is not in this review", "no_groups": "this review has no groups to change",
    "moved_meanwhile": "the review moved meanwhile; read it again",
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


def plan(pass_row: dict, stored_items: list[dict], queue: dict, tray_entries, today: date) -> Plan:
    """Every stored item's state (§6.2) and the arrivals: queue items the pass does not hold, each placed at the end
    (in Review's order among themselves) or, when urgent (order_key's tier 0) while an item is in front, right after
    it. A pass in the state `end` or `empty` with something undecided reads as back on its first undecided item, and a
    checkpoint with nothing undecided as the end. Pure."""
    entries = {e["key"]: e for e in list(queue.get("tasks") or []) + list(queue.get("cards") or [])}
    trows = _trows(tray_entries)
    deciding: dict[str, list[str]] = {}
    for k, _, m in trows:
        if m.get("decides"):
            deciding.setdefault(m["decides"], []).append(k)
    keys = {k for k, _, _ in trows}
    evs = {e.get("ev") for _, e, _ in trows if type(e.get("ev")) is int}
    stored = {r["key"]: r for r in stored_items}
    pos = {k: float(r["pos"]) for k, r in stored.items()}
    kinds = {k: r["kind"] for k, r in stored.items()}
    states = {k: _state(k, stored[k], entries.get(k), deciding, keys, evs) for k in stored}
    st, cursor = pass_row.get("state") or "item", pass_row.get("cursor_key")
    new = sorted((k for k in entries if k not in stored), key=lambda k: review_queue.order_key(entries[k], today))
    urgent = [k for k in new if review_queue.order_key(entries[k], today)[0] == 0] if cursor in pos and st in ("item", "checkpoint") else []
    arrivals: list[tuple[str, float, str]] = []
    if urgent:
        c = pos[cursor]
        nxt = min((p for p in pos.values() if p > c), default=c + 1)
        arrivals += [(k, c + (nxt - c) * (i + 1) / (len(urgent) + 1), _kind(k)) for i, k in enumerate(urgent)]
    last = math.floor(max(pos.values(), default=0.0))
    arrivals += [(k, last + i + 1.0, _kind(k)) for i, k in enumerate(k for k in new if k not in urgent)]
    for k, p, kind in arrivals:
        pos[k], kinds[k] = p, kind
        states[k] = _state(k, None, entries[k], deciding, keys, evs)
    order = sorted(pos, key=lambda k: (pos[k], k))
    undecided = [k for k in order if states[k] in UNDECIDED]
    if st in ("end", "empty") and undecided:
        st, cursor = "item", undecided[0]
    elif st == "checkpoint" and not undecided:              # decided meanwhile elsewhere: nothing to keep going over
        st, cursor = "end", None
    return Plan(order, pos, kinds, states, arrivals, entries, st, cursor)


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


def _read(conn: sqlite3.Connection, cfg: dict, today: date, row: dict, queue: dict | None = None) -> tuple[dict, Plan]:
    queue = review_queue.items(conn, cfg, today) if queue is None else queue
    return queue, plan(row, _stored(conn, row["id"]), queue, tray.read(conn)["entries"], today)


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
        """The plan as it reads: the arrivals inserted, and an end with something undecided back on an item."""
        now = now_iso()
        for k, p, kind in self.P.arrivals:
            self.conn.execute("INSERT OR IGNORE INTO review_pass_items (pass_id, key, pos, kind, added_at) VALUES (?, ?, ?, ?, ?)",
                              (self.row["id"], k, p, kind, now))
            self.dirty = True
        if self.P.state != self.row["state"] or self.P.cursor != self.row["cursor_key"]:
            if self.P.cursor and self.P.state == "item":
                self.arrive(self.P.cursor, forward=True)
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
        ids = [int(key[2:])] if key.startswith("t:") and key[2:].isdigit() else []
        if ids:
            self.conn.execute(f"UPDATE tasks SET last_offered_at = ? WHERE id IN ({','.join('?' * len(ids))})"
                              " AND (last_offered_at IS NULL OR last_offered_at < ?)", (day, *ids, day))
            e = self.P.entries.get(key)
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
    in Review's order, and the arrival of the first undecided item. Two begins at once make one pass: the second waits
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
            order = sorted(queue["tasks"] + queue["cards"], key=lambda e: review_queue.order_key(e, today))
            conn.executemany("INSERT INTO review_pass_items (pass_id, key, pos, kind, added_at) VALUES (?, ?, ?, ?, ?)",
                             [(pid, e["key"], float(i + 1), _kind(e["key"]), now) for i, e in enumerate(order)])
            row = _row(conn, pid)
            _, P = _read(conn, cfg, today, row, queue)
            w = _Write(conn, cfg, today, row, P, by)
            und = P.undecided()
            if und:
                w.arrive(und[0], forward=True)
            else:
                w.set(state="end" if P.shown() else "empty", cursor_key=None)
            w.save()
            return _view(conn, cfg, today, _row(conn, pid), queue)
    queue, P = _read(conn, cfg, today, row)
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
    the item in front, anything else moves nothing. A key the pass does not hold, or holds as gone, is staged as the
    rule allows (an open task outside the queue still takes a decision from its card's conversation; Gone only when
    the rule says so). `rec` and `keep_all` are the pass's own options, offered from 2c and 2d: Refused until then.
    Answers the stage's {keys, removed, said} and `advanced`. Raises NoPass, Ended, Refused, Gone, Stale. No commit."""
    if from_chat is None and option in PASS_ONLY:
        raise Refused(f"{option} is not offered on this item")
    begin_write(conn)
    row = _live_row(conn, pass_id, today)
    args = dict(args or {})
    if note is not None:
        args["note"] = note
    queue, P = _read(conn, cfg, today, row)
    w = _Write(conn, cfg, today, row, P, by)
    w.persist()
    front = P.state == "item" and P.cursor == key
    out = review_decide.stage(conn, cfg, key, option, args, today, origin=origin, device=device, from_chat=from_chat)
    P2 = plan(dict(row, **w.upd), _stored(conn, row["id"]), queue, tray.read(conn)["entries"], today)
    w.P = P2
    advanced = False
    if from_chat is None and option == "undecide" and key in P2.pos and P2.states[key] != "gone":
        w.arrive(key, forward=False)
    elif front and P2.states.get(key) in SETTLED:
        target, _ = _after(P2, key)
        if target is None:
            w.set(state="end", cursor_key=None)
        else:
            w.forward(target)
        advanced = True
    if not w.save():
        raise review_decide.Stale(WORDS["moved_meanwhile"])
    return dict(out, advanced=advanced)


def regroup(conn: sqlite3.Connection, cfg: dict, today: date, pass_id: int, key: str, op: str, *, task_id: int | None = None,
            by: str | None) -> dict:
    """Take a member out of a group or break a group into its members (2d). This pass holds no groups yet: Refused."""
    raise Refused(WORDS["no_groups"])


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
    `spoken` is for 2e's sentences; this pass has none yet."""
    row = live(conn)
    if row is None or row["day"] != today.isoformat():
        return None
    return _view(conn, cfg, today, row)


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
    st = P.states.get(key, "gone")
    item, settled = P.entries.get(key), None
    if item is None:
        item, settled = _settled(conn, cfg, today, key, st)
    kind = P.kinds.get(key) or _kind(key)
    out = {"key": key, "kind": kind, "state": st, "item": item, "provenance": None,
           "evidence_lines": (item.get("evidence_lines") or []) if kind == "task" else [],
           "recommendation": None, "options": options(item, None, trows, today),
           "later_choices": review_decide.later_choices(today) if kind == "task" else [],
           "staged": _staged(key, trows)}
    if settled:
        out["settled"] = settled
    return out


def _line(conn: sqlite3.Connection, cfg: dict, key: str, P: Plan, trows) -> dict:
    """One row of the View's list: {key, kind, name, why, state, staged, due}."""
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
    return {"key": key, "kind": P.kinds.get(key) or _kind(key), "name": name, "why": why or "", "state": P.states[key],
            "staged": staged["label"] if staged else None, "due": review_decide.day_words(due) if isinstance(due, str) and due else None}


def _view(conn: sqlite3.Connection, cfg: dict, today: date, row: dict, queue: dict | None = None) -> dict:
    queue = review_queue.items(conn, cfg, today) if queue is None else queue
    tr = tray.read(conn)
    trows = _trows(tr["entries"])
    P = plan(row, _stored(conn, row["id"]), queue, tr["entries"], today)
    shown, und = P.shown(), P.undecided()
    cur = P.cursor if P.state in ("item", "checkpoint") else None
    counts = {"items": len(shown), "position": shown.index(cur) + 1 if cur in shown else None, "undecided": len(und),
              "decided": sum(1 for k in shown if P.states[k] in SETTLED), "passed": sum(1 for k in shown if P.states[k] == "passed"),
              "left": review_queue.undecided_count(conn, cfg, today), "staged": len(trows),
              "minutes_left": math.ceil(len(und) * SECONDS_PER_ITEM / 60)}
    ahead = _after(P, cur, skip_cur=True)[0] if P.state == "item" and cur else None
    return {"id": row["id"], "rev": row["rev"], "day": row["day"], "today": today.isoformat(), "state": P.state, "cursor_key": cur,
            "lap": row["lap"], "started_by": row["started_by"], "moved_by": row["moved_by"], "moved_at": row["moved_at"],
            "tray_rev": tr["rev"], "speaking": _speaking(row), "counts": counts, "checkpoint": {"looked": row["looked"], "cap": _cap(cfg)},
            "labels": lifecycle.REVIEW_LABELS, "current": _entry(conn, cfg, today, cur, P, trows),
            "ahead": _entry(conn, cfg, today, ahead, P, trows), "list": [_line(conn, cfg, k, P, trows) for k in P.order]}
