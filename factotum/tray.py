"""The tray on the server (spoken Review, stage 1, design §5.3): everything staged and not yet applied, one tray
for every device. A row is the page's staged entry under its `stageKey`, in the order shown and applied, with
the Review item its decision settles (`decides`). An item is decided when at least one row decides it.

Every write starts with `db.begin_write` and reads after it, so the API's connection, the connector's writer
and the stdio skill server take turns. Nothing here commits except `apply` (its batch commits); the caller
commits `write` and `carry`. `apply` deletes the rows it applies inside the batch's transaction: both or neither."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date

from . import batches
from .db import begin_write, now_iso, set_setting

STRIP = ("source_key", "created_source", "source_link", "account", "reminder_status")   # as api.apply_batch: the page never chooses these
SENT = ("task_id", "op", "fields", "expect", "note", "from_chat")     # what reaches batches.apply
META = ("label", "ev", "fsug", "prop")                                 # kept, never sent
OPS = batches.OPS | {"create_suggestion"}
MAX_ENTRIES = 300
MAX_ENTRY_BYTES = 16_384
MAX_OPS = 200
KEY_RE = re.compile(r"[A-Za-z0-9_:.-]{1,120}")
REF_RE = re.compile(r"m[0-9]{1,15}-[0-9]{1,6}")                         # a chat create's tray ref (chat.tray_ref)
ITEM_RE = re.compile(r"[tsg]:[A-Za-z0-9_:.-]{1,100}")
_DIGITS = re.compile(r"[0-9]{1,15}")
_E_KEY = re.compile(r"e:([0-9]{1,15})")
_LEAD = re.compile(r"[0-9]{1,15}")
DECIDING = ("done", "drop", "keep", "reopen")                          # the ops that decide a task under its bare id (and a `set` of snoozed_until)
ORIGINS = ("page", "carry", "connector", "skill")


class Stale(Exception):
    """Apply named rows that changed after the revision the caller had seen (or another Apply took them): nothing
    is written. `changed` lists their keys."""

    def __init__(self, words: str, changed: list[str]):
        super().__init__(words)
        self.changed = changed


def _canon(entry) -> str:
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _key(k) -> str:
    """A key as text: the page's Map holds a task's bare id as a number."""
    if type(k) is int and k >= 0:
        return str(k)
    if not isinstance(k, str) or not KEY_RE.fullmatch(k):
        raise ValueError(f"{str(k)[:40]}: not a tray key")
    return k


def clean_entry(key, entry) -> dict:
    """The entry as the tray keeps it (SENT + META, STRIP and anything else dropped), or ValueError("<key>: <why>")."""
    k = _key(key)

    def bad(why):
        return ValueError(f"{k}: {why}")
    if not isinstance(entry, dict):
        raise bad("an entry is an object")
    if len(_canon(entry).encode()) > MAX_ENTRY_BYTES:
        raise bad(f"an entry is at most {MAX_ENTRY_BYTES} bytes")
    if entry.get("local"):
        raise bad("a provisional entry is never stored")
    op = entry.get("op")
    if not isinstance(op, str) or op not in OPS:
        raise bad(f"unknown op {str(op)[:40]!r}")
    tid = entry.get("task_id")
    if not (tid is None or type(tid) is int or (isinstance(tid, str) and REF_RE.fullmatch(tid))):
        raise bad("task_id is a task id, null or a new task's ref")
    if _DIGITS.fullmatch(k) and tid != int(k):
        raise bad("a task's own key names that task")
    for f, kinds in (("fields", dict), ("expect", dict), ("from_chat", dict), ("note", str), ("label", str), ("prop", str)):
        if entry.get(f) is not None and not isinstance(entry[f], kinds):
            raise bad(f"{f} has the wrong type")
    for f in ("ev", "fsug"):
        if entry.get(f) is not None and type(entry[f]) is not int:
            raise bad(f"{f} is an evidence id")
    return {f: entry[f] for f in SENT + META if f in entry and f not in STRIP}


def decides_of(key, entry: dict, given: str | None = None) -> str | None:
    """The item key a row's decision settles, or None when it stands beside. `given` when the rule supplied it;
    else a bare task id holding `done`, `drop`, `keep`, `reopen` or a `set` of `snoozed_until` decides that task
    (an old page's Pin or Long-term under the bare id decides nothing); else a card's Create or Dismissal
    (`e:<n>`, no task) decides the card."""
    if given:
        return given
    k = str(key)
    op, f = entry.get("op"), entry.get("fields") or {}
    if _DIGITS.fullmatch(k):
        return f"t:{k}" if op in DECIDING or (op == "set" and isinstance(f, dict) and "snoozed_until" in f) else None
    m = _E_KEY.fullmatch(k)
    if m and entry.get("task_id") is None:
        if op == "create_suggestion" or (op == "judge_evidence" and isinstance(f, dict) and f.get("verdict") == "dismissed"):
            return f"s:{m.group(1)}"
    return None


def rev(conn: sqlite3.Connection) -> int:
    r = conn.execute("SELECT rev FROM tray_rev WHERE id = 1").fetchone()
    return int(r[0]) if r else 0


def _bump(conn: sqlite3.Connection) -> int:
    n = rev(conn) + 1
    conn.execute("INSERT INTO tray_rev (id, rev) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET rev = excluded.rev", (n,))
    return n


def _rows(conn: sqlite3.Connection, keys: list[str] | None = None) -> list[tuple[str, dict, dict]]:
    sql = "SELECT key, entry_json, decides, origin, device, rev, updated_at FROM tray"
    rs = conn.execute(sql + " ORDER BY pos").fetchall()
    out = [(r[0], json.loads(r[1]), {"decides": r[2], "origin": r[3], "device": r[4], "rev": r[5], "updated_at": r[6]}) for r in rs]
    if keys is not None:
        want = set(keys)
        out = [r for r in out if r[0] in want]
    return out


def read(conn: sqlite3.Connection, since: int | None = None) -> dict:
    """{"rev", "entries": [[key, entry, meta], ...]} in `pos` order, meta = {decides, origin, device, rev,
    updated_at}; {"rev", "same": True} when `since` is the current revision. Reads only."""
    r = rev(conn)
    if since is not None and since == r:
        return {"rev": r, "same": True}
    return {"rev": r, "entries": [[k, e, m] for k, e, m in _rows(conn)]}


def write(conn: sqlite3.Connection, ops: list, origin: str, device: str | None = None) -> list[str]:
    """Puts and deletes in one revision: `ops` is [{"put": key, "entry": {...}, "decides": item key | None} |
    {"del": key}], at most MAX_OPS, every entry through `clean_entry` first (one failure raises ValueError and
    nothing is written). An identical put changes nothing; a key put again keeps its place; a new key goes last.
    When anything changed the revision moves once and the changed rows carry it. Returns the changed keys. No commit."""
    if not isinstance(ops, list) or len(ops) > MAX_OPS:
        raise ValueError(f"at most {MAX_OPS} changes at once")
    if origin not in ORIGINS:
        raise ValueError(f"unknown origin {origin!r}")
    device = device[:80] if isinstance(device, str) and device.strip() else None
    plan: list[tuple[str, str, dict | None, str | None]] = []
    for o in ops:
        if not isinstance(o, dict) or ("put" in o) == ("del" in o):
            raise ValueError("each change is {put, entry} or {del}")
        if "del" in o:
            plan.append(("del", _key(o["del"]), None, None))
            continue
        k = _key(o["put"])
        if "entry" not in o:
            raise ValueError(f"{k}: a put needs its entry")
        e = clean_entry(k, o["entry"])
        given = o.get("decides")
        if given is not None and not (isinstance(given, str) and ITEM_RE.fullmatch(given)):
            raise ValueError(f"{k}: decides names an item")
        plan.append(("put", k, e, decides_of(k, e, given)))
    begin_write(conn)
    have = {r[0]: (json.loads(r[1]), r[2]) for r in conn.execute("SELECT key, entry_json, decides FROM tray")}
    after = set(have)
    for what, k, _, _ in plan:
        (after.discard if what == "del" else after.add)(k)
    if len(after) > max(len(have), MAX_ENTRIES):                         # checked before anything is written
        raise ValueError(f"the tray holds at most {MAX_ENTRIES} changes; apply or remove some first")
    changed: list[str] = []
    new_rev = None
    now = now_iso()
    for what, k, e, d in plan:
        if what == "del":
            if k in have:
                new_rev = new_rev or _bump(conn)
                conn.execute("DELETE FROM tray WHERE key = ?", (k,))
                del have[k]
                changed.append(k)
            continue
        if k in have and have[k][0] == e and have[k][1] == d:
            continue
        new_rev = new_rev or _bump(conn)
        if k in have:
            conn.execute("UPDATE tray SET entry_json = ?, decides = ?, origin = ?, device = ?, rev = ?, updated_at = ? WHERE key = ?",
                         (json.dumps(e, ensure_ascii=False, default=str), d, origin, device, new_rev, now, k))
        else:
            pos = conn.execute("SELECT COALESCE(MAX(pos), 0) + 1 FROM tray").fetchone()[0]
            conn.execute("INSERT INTO tray (key, pos, entry_json, decides, origin, device, rev, staged_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (k, pos, json.dumps(e, ensure_ascii=False, default=str), d, origin, device, new_rev, now, now))
        have[k] = (e, d)
        if k not in changed:
            changed.append(k)
    return changed


def decided(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """{item key: [the tray keys that decide it]} from the `decides` column, in `pos` order. Reads only."""
    out: dict[str, list[str]] = {}
    for k, d in conn.execute("SELECT key, decides FROM tray WHERE decides IS NOT NULL ORDER BY pos"):
        out.setdefault(d, []).append(k)
    return out


def summary(conn: sqlite3.Connection) -> dict:
    """Counts for a read-back: {"entries", "decisions": {keep, done, drop, later, create, dismiss}, "beside": rows
    that decide nothing, "by": {origin: rows}, "decided_by": {origin: decisions}}. A decision is counted once per item, by its deciding row."""
    rows = _rows(conn)
    dec = dict.fromkeys(("keep", "done", "drop", "later", "create", "dismiss"), 0)
    by: dict[str, int] = {}
    decided_by: dict[str, int] = {}
    beside = 0
    for k, e, m in rows:
        by[m["origin"]] = by.get(m["origin"], 0) + 1
        d = m["decides"]
        if not d:
            beside += 1
            continue
        op, f, one = e.get("op"), e.get("fields") or {}, None
        if d.startswith("t:") and k == d[2:]:
            if op in ("keep", "done", "drop"):
                one = op
            elif op == "set" and "snoozed_until" in f:
                one = "later"
        elif d.startswith("s:") and k == f"e:{d[2:]}":
            one = "create" if op == "create_suggestion" else "dismiss"
        if one:
            dec[one] += 1
            decided_by[m["origin"]] = decided_by.get(m["origin"], 0) + 1
    return {"entries": len(rows), "decisions": dec, "beside": beside, "by": by, "decided_by": decided_by}


def _old_key(k: str, e: dict) -> str:
    """An old page's Pin or Long-term under the bare id moves beside, where Stage 1 stages them."""
    if not _DIGITS.fullmatch(k):
        return k
    op, f = e.get("op"), e.get("fields") if isinstance(e.get("fields"), dict) else {}
    if op in ("pin_top", "unpin") or (op == "set" and f and set(f) == {"top_rank"}):
        return f"{k}:top"
    if op == "longterm" or (op == "set" and f and set(f) == {"longterm"}):
        return f"{k}:lt"
    return k


def carry(conn: sqlite3.Connection, carry_id: str, entries: list, device: str | None) -> dict:
    """What one browser's old tray (`localStorage['factotum.staged']`) hands over, once. For each [key, entry]: an
    entry carried before (its fingerprint, the entry as received) is skipped silently; an old bare-key Pin or
    Long-term moves to `<id>:top` / `<id>:lt`; a missing task_id gets the old fix (the key's leading number,
    except on a create); a key already in the tray is kept out (another device staged it; that one is kept); one
    `clean_entry` refuses is kept out with the reason; the rest are put with origin `carry`. A carried verdict
    `reviewed` on a task whose decision was carried with it belongs to that decision. No commit.
    Answers {"carried", "kept_out": [{key, why}], "skipped"}."""
    if not isinstance(carry_id, str) or not KEY_RE.fullmatch(carry_id):
        raise ValueError("carry_id: not a carry id")
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError(f"an old tray holds at most {MAX_ENTRIES} changes")
    begin_write(conn)
    have = {r[0] for r in conn.execute("SELECT key FROM tray")}
    room = MAX_ENTRIES - len(have)
    seen = {r[0] for r in conn.execute("SELECT fp FROM tray_carried")}
    kept_out: list[dict] = []
    skipped = 0
    puts: list[tuple[str, dict, str]] = []          # (key, entry, fingerprint)
    for pair in entries:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            kept_out.append({"key": str(pair[0] if isinstance(pair, (list, tuple)) and pair else pair)[:120], "why": "not a staged change"})
            continue
        raw_key, e = pair
        k = str(raw_key)[:120]
        if not isinstance(e, dict):
            kept_out.append({"key": k, "why": "not a staged change"})
            continue
        fp = hashlib.sha256((k + "\n" + _canon(e)).encode()).hexdigest()
        if fp in seen:
            skipped += 1
            continue
        e = dict(e)
        if e.get("task_id") is None and e.get("op") != "create":             # a tray saved before entries carried task_id
            m = _LEAD.match(k)
            if m:
                e["task_id"] = int(m.group(0))
        try:
            k = _old_key(_key(raw_key), e)
            clean = clean_entry(k, e)
        except ValueError as ex:
            kept_out.append({"key": k, "why": str(ex).split(": ", 1)[-1]})
            continue
        if k in have:
            kept_out.append({"key": k, "why": "staged on another device too; that one is kept"})
            continue
        if room <= 0:
            kept_out.append({"key": k, "why": f"the tray holds at most {MAX_ENTRIES} changes"})
            continue
        room -= 1
        have.add(k)
        seen.add(fp)
        puts.append((k, clean, fp))
    deciding = {k for k, e, _ in puts if decides_of(k, e)}
    ops = []
    for k, e, _ in puts:
        f = e.get("fields") or {}
        tied = (k.startswith("e:") and e.get("op") == "judge_evidence" and f.get("verdict") == "reviewed"
                and type(e.get("task_id")) is int and str(e["task_id"]) in deciding)
        ops.append(dict({"put": k, "entry": e}, **({"decides": f"t:{e['task_id']}"} if tied else {})))
    for i in range(0, len(ops), MAX_OPS):
        write(conn, ops[i:i + MAX_OPS], "carry", device)
    now = now_iso()
    for _, _, fp in puts:
        conn.execute("INSERT OR IGNORE INTO tray_carried (fp, carry_id, at) VALUES (?, ?, ?)", (fp, carry_id, now))
    return {"carried": len(puts), "kept_out": kept_out, "skipped": skipped}


def _change(e: dict) -> dict:
    """What batches.apply takes from a row (the page's applyAll): task_id, op, fields, and expect, note and
    from_chat when set. Never the label, `ev`, `fsug` or `prop`."""
    c = {"task_id": e.get("task_id"), "op": e.get("op"), "fields": e.get("fields") or {}}
    for f in ("expect", "note", "from_chat"):
        if e.get(f):
            c[f] = e[f]
    return c


def changes_for(conn: sqlite3.Connection, cfg: dict, rows: list, today: date) -> tuple[list[dict], list[dict], list[dict]]:
    """The batch's changes for `rows` (key, entry, meta): new tasks first (a chat create, a card's Create built by
    `review_cards.card_changes`), then the rest in `pos` order. A card's Create that can no longer be made (answered
    since, made already) is left out with its reason. Returns (changes, gone: [{task_id, why}], cards: [{evidence_id,
    plan}]). Reads only."""
    from . import review_cards                     # lazy: review_cards imports ripple, which imports the planner
    first: list[dict] = []
    rest: list[dict] = []
    gone: list[dict] = []
    cards: list[dict] = []
    for _, e, _ in rows:
        op = e.get("op")
        if op == "create_suggestion":
            f = e.get("fields") or {}
            eid = f.get("evidence_id")
            if type(eid) is not int:
                gone.append({"task_id": None, "why": "a card's Create names no card"})
                continue
            try:
                plan = review_cards.card_changes(conn, cfg, eid, f.get("draft"), today)
            except LookupError as ex:
                gone.append({"task_id": None, "why": f"suggestion {eid}: {ex}"})
                continue
            if not plan.changes:
                gone.extend({"task_id": plan.summary.get("task_id"), "why": w} for w in plan.skipped or [f"suggestion {eid}: nothing to make"])
                continue
            first.extend(plan.changes)
            cards.append({"evidence_id": eid, "plan": plan})
        elif op == "create":
            first.append(_change(e))
        else:
            rest.append(_change(e))
    return first + rest, gone, cards


def finishes(rows: list, whole_tray: bool) -> bool:
    """Whether an Apply is a review (the day is marked reviewed): always for the whole tray; for some keys only
    when a row decides an item or judges evidence. An Apply of only the editor's edits on a Review card is not."""
    return bool(whole_tray) or any((m or {}).get("decides") or (e or {}).get("op") == "judge_evidence" for _, e, m in rows)


def _after_apply(conn: sqlite3.Connection, cfg: dict, today: date, decided: set[str], batch_id) -> None:
    """The live Review pass learns what the batch applied (review_pass.after_apply), inside a savepoint: the batch is
    committed already, so a failure here lets go of only its own writes and never fails the Apply (its items then read
    as gone, which is harmless), nor does a write lock it cannot take (review 2026-11-20). review_pass imports this
    module, so it is imported here, lazily."""
    from . import review_pass
    try:
        begin_write(conn)                          # chat.mark_staged committed: a new write starts here
    except sqlite3.OperationalError:               # another writer held the lock past the busy timeout: the follow-up is skipped
        return
    conn.execute("SAVEPOINT after_apply")
    try:
        review_pass.after_apply(conn, decided, batch_id, cfg=cfg, today=today)
    except Exception:
        conn.execute("ROLLBACK TO after_apply")
    conn.execute("RELEASE after_apply")


def apply(conn: sqlite3.Connection, cfg: dict, today: date, *, keys: list[str] | None = None, seen_rev: int | None = None,
          source: str = "review", finish: bool = False, whole_tray: bool = True, cause: dict | None = None,
          max_rows: int | None = None) -> dict:
    """Apply the tray (or `keys` of it) as one batch, then the follow-up writes; commits (design §5.3).
    1. Take the write lock, then read the rows; keys not there are `gone`. With `seen_rev`, a row written after it
       raises Stale and nothing is written. 2. The changes (`changes_for`; chat creates resolved inside the lock, so
       two Applies never make one twice). 3. Delete the rows (the count must match, else Stale). 4. Nothing left
       to apply: commit and answer the empty batch. 5. `batches.apply` (it commits, also when everything was
       skipped; on an error its rollback puts the rows back). 6. The follow-ups: chat proposals marked applied, a
       Slack card's message link, the live Review pass told (`review_pass.after_apply`: the items it decided get the
       batch, and a pass left with nothing undecided ends `applied`), the day marked reviewed when `finish` and `finishes`. 7. The answer:
       batches.apply's, with `skipped` holding every reason, plus `gone`, `cards`, `finished` and `tray`.
    Raises Stale, and ValueError for a row that names no stored chat create; with `max_rows` (a spoken Apply under the
    hourly cap) `batches.TooMany` when the batch would write more rows, and then the tray is as it was."""
    from . import chat                             # lazy: chat pulls in the MCP server and the runner
    from .db import add_link
    begin_write(conn)
    try:
        rows = _rows(conn, None if keys is None else [str(k) for k in keys])
        have = {k for k, _, _ in rows}
        gone = [str(k) for k in (keys or []) if str(k) not in have]
        if seen_rev is not None:
            changed = [k for k, _, m in rows if m["rev"] > seen_rev]
            if changed:
                raise Stale("the tray changed since it was shown", changed)
        changes, gone2, cards = changes_for(conn, cfg, rows, today)
        left_out: list[dict] = []
        changes = chat.resolve_staged(conn, cfg, changes, left_out)
        if rows:
            n = conn.execute(f"DELETE FROM tray WHERE key IN ({','.join('?' * len(rows))})", [k for k, _, _ in rows]).rowcount
            if n != len(rows):
                raise Stale("another Apply took these changes", [k for k, _, _ in rows])
            _bump(conn)
        staged_by: dict[str, int] = {}
        for _, _, m in rows:
            staged_by[m["origin"]] = staged_by.get(m["origin"], 0) + 1
        if not changes:
            conn.commit()
            return {"batch_id": None, "applied": 0, "skipped": left_out + gone2, "created": [], "refs": {}, "ripple_queued": [],
                    "gone": gone, "cards": [], "finished": False, "tray": read(conn)}
        res = batches.apply(conn, changes, source, today, cause=dict(cause or {}, staged_by=staged_by), max_rows=max_rows)
    except Exception:
        conn.rollback()
        raise
    # 6. Follow-ups, each short and safe to run again.
    chat.mark_staged(conn, changes, res)
    made = []
    for c in cards:
        plan, s = c["plan"], c["plan"].summary
        tid, already = s.get("task_id"), bool(s.get("already"))
        if tid is None:
            tid = res["refs"].get(s["ref"])
            if tid is None:                            # made meanwhile by another process: that task is the answer
                key = plan.changes[0].get("source_key")
                again = conn.execute("SELECT id FROM tasks WHERE source_key = ?", (key,)).fetchone()
                tid, already = (again[0] if again else None), True
            else:
                for p in plan.post:                    # a Slack card's message link: provenance, beside the batch
                    add_link(conn, tid, p["kind"], p["ref"], label=p["label"], account=p["account"])
        made.append({"evidence_id": c["evidence_id"], "task_id": tid, "reopened": bool(s.get("reopened")), "already": already})
    _after_apply(conn, cfg, today, {m["decides"] for _, _, m in rows if m["decides"]}, res["batch_id"])
    finished = bool(finish and finishes(rows, whole_tray))
    if finished:
        set_setting(conn, "state.last_review_at", today.isoformat())
    conn.commit()
    return dict(res, skipped=left_out + gone2 + res["skipped"], gone=gone, cards=made, finished=finished, tray=read(conn))
