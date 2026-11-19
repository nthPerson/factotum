"""Search over every task, open or closed (design: docs/superpowers/specs/2026-11-18-search-and-review-editing-design.md).

    run(conn, cfg, today, q="", mode="match", within="all", status=[], project=[], source=[], effort=[],
        date_field="due", date_from=None, date_to=None, flags=[], sort="relevance", limit=50, offset=0) -> dict

Reads only. The rows are read once and matched in Python: a task list is hundreds or a few thousand rows, so one
pass is milliseconds, and ranking by field, close spellings and the matched text under a name are plain code with
no second copy of the text to keep in step. The Search tab (`GET /api/search/advanced`), the agent's `tasks_search`
tool and `python -m factotum search` all call `run`; `by_hand.search` (Find any task, the task picker) is separate
and unchanged.

    q        words (every one must match); "a phrase"; -word leaves a row out; a lone 412, #412 or Task-412 is that task
    mode     match: a word is part of the searched text (case and accents folded)
             fuzzy: a word also matches a word spelt nearly the same or sharing its stem
             match that finds nothing answers with the fuzzy result and `fell_back: true`
    within   all | name | text | people
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import date, datetime, timezone
from functools import lru_cache

from . import config as C

MODES = ("match", "fuzzy")
STATUSES = ("open", "done", "dropped", "faded", "lapsed")
WITHIN = {"all": ("name", "project", "counterpart", "waiting_on", "context", "why_now", "outcome"),
          "name": ("name",), "text": ("context", "why_now", "outcome"), "people": ("counterpart", "waiting_on")}
WEIGHT = {"name": 10.0, "project": 6.0, "counterpart": 6.0, "waiting_on": 6.0, "context": 3.0, "why_now": 3.0, "outcome": 2.0}
TEXT_FIELDS = ("context", "why_now", "outcome")                 # where a snippet comes from
DATE_FIELDS = {"due": "due", "created": "created_at", "closed": "completed_at", "changed": "edited_at"}
DATE_WORDS = {"due": "due", "created": "created", "closed": "closed", "changed": "changed"}
FLAGS = {"hard": "a hard deadline", "undated": "no date", "waiting": "waiting on someone", "top": "pinned to Top", "longterm": "long-term",
         "linked": "linked to another task", "later": "set for later", "reminder": "a reminder set"}
SORTS = ("relevance", "due", "created", "closed", "changed")
FACETS = ("status", "project", "source", "effort")
COLS = ("id", "name", "status", "project", "due", "hard", "effort", "source", "created_at", "edited_at", "completed_at", "closed_by",
        "outcome", "counterpart", "waiting_on", "context", "why_now", "top_rank", "longterm", "snoozed_until")
SHIPPED = ("id", "name", "status", "project", "due", "hard", "effort", "source", "created_at", "edited_at", "completed_at", "closed_by",
           "outcome", "counterpart", "waiting_on", "top_rank", "longterm", "snoozed_until")
LIMIT_MAX, TERMS_MAX, Q_MAX = 200, 8, 200
NONE = "(none)"                                                # a filter value: rows with no project / source / effort
SNIPPET_BEFORE, SNIPPET_AFTER = 50, 120
ID_RE = re.compile(r"^(?:task[-\s]?|#)?(\d{1,7})$", re.I)
_TOKEN = re.compile(r'(-?)"([^"]*)"|(\S+)')
_WORD = re.compile(r"\w+")


# ---- text ---------------------------------------------------------------------------------------

@lru_cache(maxsize=4096)
def _fold_char(ch: str) -> str:
    base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)).lower()
    return base[:1] or " "


def fold(s) -> str:
    """Lower case with accents removed, one character for one character, so a position in the folded text is the
    same position in the text as written."""
    s = "" if s is None else str(s)
    return s.lower() if s.isascii() else "".join(map(_fold_char, s))


def parse(q: str) -> dict:
    """The words of a query: {"terms": [{"text", "neg", "phrase"}], "id": the task number a lone number names}."""
    q = " ".join(str(q or "")[:Q_MAX].split())
    m = ID_RE.match(q)
    terms = []
    for neg, phrase, word in _TOKEN.findall(q):
        if phrase or not word:
            text, is_phrase = " ".join(fold(phrase).split()), True
        else:
            is_neg = word.startswith("-") and len(word) > 1
            neg, text, is_phrase = "-" if is_neg else "", fold(word[1:] if is_neg else word).strip('"'), False
        if text:
            terms.append({"text": text, "neg": bool(neg), "phrase": is_phrase})
    if m:                                                    # "Task-412": the number is what is looked for in text
        terms = [{"text": m.group(1), "neg": False, "phrase": False}]
    return {"terms": terms[:TERMS_MAX], "id": int(m.group(1)) if m else None}


def _distance(a: str, b: str, most: int) -> int:
    """Edits between two words (a swap of neighbours is one), or `most + 1` as soon as it is more than `most`."""
    la, lb = len(a), len(b)
    if abs(la - lb) > most:
        return most + 1
    prev2, prev = None, list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        low = cur[0]
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                v = min(v, prev2[j - 2] + 1)
            cur[j] = v
            low = min(low, v)
        if low > most:
            return most + 1
        prev2, prev = prev, cur
    return prev[lb]


def closeness(term: str, word: str) -> float:
    """How nearly `word` is `term`, 0 (not close) to 1 (the same). Close is: one slip in a word of 4 to 6 letters,
    two in a longer one, or a shared stem of at least five letters that is most of the shorter word
    (prioritize / prioritization). A word of three letters or fewer is never matched loosely."""
    if term == word:
        return 1.0
    la, lb = len(term), len(word)
    if la < 4 or lb < 4:
        return 0.0
    p = 0
    for x, y in zip(term, word):
        if x != y:
            break
        p += 1
    if p >= 5 and p >= 0.7 * min(la, lb):
        return 0.8
    most = 1 if max(la, lb) <= 6 else 2
    d = _distance(term, word, most)
    return 0.0 if d > most else round(1 - d / max(la, lb), 3)


# ---- matching -----------------------------------------------------------------------------------

def _exact(text: str, term: str) -> tuple[float, int] | None:
    """(quality, position) of `term` in `text`: a whole word is worth more than a word's start, more than a part of one."""
    i = text.find(term)
    if i < 0:
        return None
    best = (1.0, i)
    while i >= 0:
        before = i == 0 or not text[i - 1].isalnum()
        after = i + len(term) == len(text) or not text[i + len(term)].isalnum()
        if before and after:
            return (1.3, i)
        if before and best[0] < 1.15:
            best = (1.15, i)
        i = text.find(term, i + 1)
    return best


class _Row:
    __slots__ = ("t", "texts", "_words")

    def __init__(self, t: dict, scope: tuple[str, ...]):
        self.t = t
        self.texts = {f: fold(t.get(f)) for f in scope}
        self._words = None

    def words(self) -> dict[str, list[tuple[int, str]]]:
        if self._words is None:
            self._words = {f: [(m.start(), m.group()) for m in _WORD.finditer(x)] for f, x in self.texts.items() if x}
        return self._words


def _match(row: _Row, parsed: dict, scope: tuple[str, ...], close: dict[str, dict[str, float]] | None) -> dict | None:
    """None when the row does not match; else {"score", "fields", "marks": {field: [(pos, length)]}}."""
    score, fields, marks = 0.0, [], {}
    tid = parsed["id"]
    if tid is not None and row.t["id"] == tid:
        return {"score": 1000.0, "fields": ["id"], "marks": {}}
    for term in parsed["terms"]:
        text = term["text"]
        if term["neg"]:
            if any(text in row.texts[f] for f in scope):
                return None
            continue
        best = None                                           # (points, field, pos, length)
        for f in scope:
            hit = _exact(row.texts[f], text)
            if hit and (best is None or WEIGHT[f] * hit[0] > best[0]):
                best = (WEIGHT[f] * hit[0], f, hit[1], len(text))
        if best is None and close is not None and not term["phrase"]:
            near = close.get(text) or {}
            if near:
                for f, ws in row.words().items():
                    for pos, w in ws:
                        s = near.get(w)
                        if s and (best is None or WEIGHT[f] * 0.6 * s > best[0]):
                            best = (WEIGHT[f] * 0.6 * s, f, pos, len(w))
        if best is None:
            return None
        score += best[0]
        if best[1] not in fields:
            fields.append(best[1])
        marks.setdefault(best[1], []).append((best[2], best[3]))
        # every other place the same words stand, so the page can mark them all
        for f in scope:
            x, i = row.texts[f], row.texts[f].find(text)
            while i >= 0 and len(marks.setdefault(f, [])) < 40:
                if (i, len(text)) not in marks[f]:
                    marks[f].append((i, len(text)))
                i = x.find(text, i + 1)
    pos = [t for t in parsed["terms"] if not t["neg"]]
    if len(pos) > 1 and all(_exact(row.texts.get("name", ""), t["text"]) for t in pos):
        score += 5.0                                          # every word in the name
    return {"score": round(score, 3), "fields": fields, "marks": {f: sorted(set(v)) for f, v in marks.items() if v}}


def _close_words(rows_: list[_Row], parsed: dict) -> dict[str, dict[str, float]]:
    """For each word of the query, the words of the task list that are close to it and how close."""
    vocab: set[str] = set()
    for r in rows_:
        for ws in r.words().values():
            vocab.update(w for _, w in ws if len(w) >= 4)
    out = {}
    for term in parsed["terms"]:
        if term["neg"] or term["phrase"] or len(term["text"]) < 4:
            continue
        near = {}
        for w in vocab:
            s = closeness(term["text"], w)
            if s:
                near[w] = s
        out[term["text"]] = near
    return out


def _spans(marks: list[tuple[int, int]], lo: int, hi: int) -> list[list[int]]:
    """Marks inside [lo, hi), relative to lo, merged where they touch or overlap."""
    out: list[list[int]] = []
    for pos, n in sorted(marks):
        a, b = max(pos, lo) - lo, min(pos + n, hi) - lo
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _snippet(t: dict, marks: dict) -> dict | None:
    """The text around the first match outside the name: {"field", "text", "spans"}; None when only the name matched."""
    for f in TEXT_FIELDS:
        ms = marks.get(f)
        raw = t.get(f)
        if not ms or not raw:
            continue
        raw = str(raw)
        first = ms[0][0]
        lo, hi = max(0, first - SNIPPET_BEFORE), min(len(raw), first + SNIPPET_AFTER)
        if lo > 0:                                           # start and end on a word, not inside one
            sp = raw.find(" ", lo, first)
            lo = sp + 1 if sp >= 0 else lo
        if hi < len(raw):
            sp = raw.rfind(" ", first, hi)
            hi = sp if sp > first + ms[0][1] else hi
        piece = raw[lo:hi]
        flat = "".join(" " if c in "\r\n\t  \x85" else c for c in piece)     # one line, positions kept
        return {"field": f, "text": ("…" if lo > 0 else "") + flat + ("…" if hi < len(raw) else ""),
                "spans": [[a + (1 if lo > 0 else 0), b + (1 if lo > 0 else 0)] for a, b in _spans(ms, lo, hi)]}
    return None


# ---- filters ------------------------------------------------------------------------------------

def _list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = v.split(",")
    return [str(x).strip() for x in v if str(x).strip()]


def _day(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _zone(cfg: dict):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(str(C.get(cfg, "owner.timezone", "UTC") or "UTC"))
    except Exception:   # noqa: BLE001 — an unknown zone name reads as UTC
        return timezone.utc


def _local_day(stamp, zone) -> date | None:
    """The day a stored value falls on where the owner is: a date is itself, a timestamp is converted."""
    s = str(stamp or "")
    if not s:
        return None
    if len(s) <= 10:
        return _day(s)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return _day(s)
    return d.astimezone(zone).date() if d.tzinfo else d.date()


def _linked_ids(conn: sqlite3.Connection) -> set[int]:
    out: set[int] = set()
    for src, dst in conn.execute("SELECT src, dst FROM relations WHERE removed_at IS NULL AND kind IN ('part_of', 'waits_on')"):
        out.add(src)
        out.add(dst)
    return out


def _reminder_ids(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT DISTINCT task_id FROM reminders WHERE status IN ('pending', 'snoozed')")}


def normalise(params: dict) -> dict:
    """The parameters as `run` uses them: unknown values dropped, lists from lists or comma-separated text."""
    p = dict(params or {})
    mode = p.get("mode") if p.get("mode") in MODES else "match"
    within = p.get("within") if p.get("within") in WITHIN else "all"
    date_field = p.get("date_field") if p.get("date_field") in DATE_FIELDS else "due"
    d_from, d_to = _day(p.get("date_from")), _day(p.get("date_to"))
    if d_from and d_to and d_from > d_to:
        d_from, d_to = d_to, d_from
    sort = p.get("sort") if p.get("sort") in SORTS else "relevance"
    try:
        limit = max(1, min(LIMIT_MAX, int(p.get("limit") or 50)))
        offset = max(0, int(p.get("offset") or 0))
    except (TypeError, ValueError):
        limit, offset = 50, 0
    status = [s for s in _list(p.get("status")) if s in STATUSES]
    some = lambda key: ["" if x == NONE else x for x in _list(p.get(key))]      # noqa: E731 — "(none)": rows with no such value
    return {"q": " ".join(str(p.get("q") or "")[:Q_MAX].split()), "mode": mode, "within": within, "status": status,
            "project": some("project"), "source": some("source"), "effort": some("effort"),
            "date_field": date_field, "date_from": d_from, "date_to": d_to,
            "flags": [f for f in _list(p.get("flags")) if f in FLAGS], "sort": sort, "limit": limit, "offset": offset}


def said(p: dict, fuzzy: bool = False) -> str:
    """The search in words, for the page's result line and for a chat turn asked from the Search tab."""
    parts = []
    if p["q"]:
        parts.append(f"“{p['q']}”" + (" (close spellings too)" if fuzzy else "") + ("" if p["within"] == "all" else f" in the {p['within']}"))
    if p["status"]:
        parts.append(" or ".join(p["status"]))
    for key, word in (("project", "project"), ("source", "from"), ("effort", "effort")):
        if p[key]:
            parts.append(f"{word} {' or '.join(x or 'none' for x in p[key])}")
    if p["date_from"] or p["date_to"]:
        w = DATE_WORDS[p["date_field"]]
        a, b = p["date_from"], p["date_to"]
        parts.append(f"{w} {a.isoformat()} to {b.isoformat()}" if a and b else f"{w} on or after {a.isoformat()}" if a else f"{w} on or before {b.isoformat()}")
    if p["flags"]:
        parts.append("only with " + ", ".join(FLAGS[f] for f in p["flags"]))
    return " · ".join(parts) or "every task"


# ---- the search ---------------------------------------------------------------------------------

def run(conn: sqlite3.Connection, cfg: dict, today: date, **params) -> dict:
    p = normalise(params)
    parsed = parse(p["q"])
    scope = WITHIN[p["within"]]
    zone = _zone(cfg)
    cur = conn.execute(f"SELECT {', '.join(COLS)} FROM tasks")
    names = [c[0] for c in cur.description]
    tasks = [dict(zip(names, r)) for r in cur.fetchall()]
    linked = _linked_ids(conn) if "linked" in p["flags"] else set()
    reminded = _reminder_ids(conn) if "reminder" in p["flags"] else set()
    col = DATE_FIELDS[p["date_field"]]
    today_s = today.isoformat()

    def passes(t: dict) -> bool:
        if p["date_from"] or p["date_to"]:
            d = _local_day(t.get(col), zone)
            if d is None or (p["date_from"] and d < p["date_from"]) or (p["date_to"] and d > p["date_to"]):
                return False
        for f in p["flags"]:
            if f == "hard" and not (t.get("hard") and t.get("due")):
                return False
            if f == "undated" and t.get("due"):
                return False
            if f == "waiting" and not (t.get("waiting_on") or "").strip():
                return False
            if f == "top" and not t.get("top_rank"):
                return False
            if f == "longterm" and not t.get("longterm"):
                return False
            if f == "linked" and t["id"] not in linked:
                return False
            if f == "later" and not (t.get("snoozed_until") and str(t["snoozed_until"]) > today_s):
                return False
            if f == "reminder" and t["id"] not in reminded:
                return False
        return True

    base = [_Row(t, scope) for t in tasks if passes(t)]
    has_words = bool(parsed["terms"]) or parsed["id"] is not None
    fuzzy_used, fell_back = p["mode"] == "fuzzy", False

    def matched(close) -> list[tuple[_Row, dict]]:
        if not has_words:
            return [(r, {"score": 0.0, "fields": [], "marks": {}}) for r in base]
        out = []
        for r in base:
            m = _match(r, parsed, scope, close)
            if m:
                out.append((r, m))
        return out

    hits = matched(_close_words(base, parsed) if fuzzy_used else None)
    if not hits and has_words and not fuzzy_used and any(not t["neg"] for t in parsed["terms"]):
        hits = matched(_close_words(base, parsed))            # nothing matched as written: the close spellings
        fuzzy_used = fell_back = bool(hits)

    want = {"status": set(p["status"]), "project": set(p["project"]), "source": set(p["source"]), "effort": set(p["effort"])}

    def value(t: dict, dim: str) -> str:
        return str(t.get(dim) or "")

    def ok(t: dict, skip: str | None = None) -> bool:
        return all(not want[d] or value(t, d) in want[d] for d in FACETS if d != skip)

    facets: dict[str, dict[str, int]] = {d: {} for d in FACETS}
    for r, _ in hits:
        for d in FACETS:
            if ok(r.t, d):
                k = value(r.t, d)
                facets[d][k] = facets[d].get(k, 0) + 1
    kept = [(r, m) for r, m in hits if ok(r.t)]

    def stamp(t: dict, key: str) -> str:
        return str(t.get(key) or "")

    # The order with nothing else to go on: open tasks by date (undated last), then closed ones, newest closed first.
    kept.sort(key=lambda x: -x[0].t["id"])
    opened = sorted((x for x in kept if x[0].t["status"] == "open"), key=lambda x: (not x[0].t.get("due"), stamp(x[0].t, "due")))
    closed = sorted((x for x in kept if x[0].t["status"] != "open"), key=lambda x: stamp(x[0].t, "completed_at") or stamp(x[0].t, "edited_at"), reverse=True)
    kept = opened + closed
    sort = p["sort"]
    if sort == "relevance":
        kept.sort(key=lambda x: -x[1]["score"])
    elif sort == "due":
        kept.sort(key=lambda x: (not x[0].t.get("due"), stamp(x[0].t, "due")))
    else:
        key = {"created": "created_at", "closed": "completed_at", "changed": "edited_at"}[sort]
        kept.sort(key=lambda x: stamp(x[0].t, key), reverse=True)
        kept.sort(key=lambda x: not x[0].t.get(key))          # rows with no such date last

    total = len(kept)
    page = kept[p["offset"]:p["offset"] + p["limit"]]
    merged = {}
    if page:
        ids = [r.t["id"] for r, _ in page]
        for src, dst in conn.execute(f"SELECT src, dst FROM relations WHERE kind = 'same_as' AND removed_at IS NULL AND src IN ({','.join('?' * len(ids))})", ids):
            merged[src] = dst
    results = []
    for r, m in page:
        row = {k: r.t.get(k) for k in SHIPPED}
        row["merged_into"] = merged.get(r.t["id"])
        row["score"] = m["score"]
        row["fields"] = m["fields"]
        row["name_spans"] = _spans(m["marks"].get("name", []), 0, len(str(r.t.get("name") or "")))
        row["snippet"] = _snippet(r.t, m["marks"])
        results.append(row)
    return {"results": results, "total": total, "offset": p["offset"], "limit": p["limit"], "more": p["offset"] + len(page) < total,
            "mode": "fuzzy" if fuzzy_used else "match", "fell_back": fell_back, "facets": facets, "said": said(p, fuzzy_used and has_words),
            "query": {"q": p["q"], "mode": p["mode"], "within": p["within"], "status": p["status"], "project": p["project"], "source": p["source"],
                      "effort": p["effort"], "date_field": p["date_field"], "date_from": p["date_from"].isoformat() if p["date_from"] else None,
                      "date_to": p["date_to"].isoformat() if p["date_to"] else None, "flags": p["flags"], "sort": p["sort"]}}
