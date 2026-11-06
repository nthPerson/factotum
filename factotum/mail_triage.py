"""Tasks suggested from mail (SPEC.md §8.5). The mail sweep keeps new Inbox mail that is about no
task as `mail_new` evidence (mail_sweep.py); this module shows a batch of it to one light Claude run
and turns the answer into suggestion cards.

    pending(conn, limit, today)      -> the unjudged candidates of the last week, oldest first
    build_prompt(cfg, conn, cands, today, clock) -> str
    apply(conn, cfg, data, cands)    -> {suggested: [names], set_aside: n}
    run(conn, cfg, today, runner=None, at=None) -> {ok, suggested, set_aside, left, line, cost_usd}

What Python enforces, whatever the model says: the run has no tools and reads only what is in the
prompt; it can only name mail from the batch it was shown; a suggestion is a `proposal` card in
Review (Create / Dismiss) and never a task, a change or a link; the fields go through the same
cleaner as every other create; a counterpart is the address Mailbird reported for the sender, never
one the model wrote; a suggestion whose name an open task or a waiting card already has is set
aside; and every mail in the batch is judged by the end of the run (`staged` or `dismissed`), so
nothing is asked about twice. A failed run judges nothing and the batch waits for the next one.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from . import config as C
from .db import get_settings, log_run, now_iso, rows, set_setting

KIND = "mail_new"
PROMPT = C.ROOT / "prompts" / "mail-triage.md"
RUNS_DIR = C.ROOT / "data" / "runs" / "triage"
BATCH = 15                 # mail per run; more waits for the next one
MAX_SUGGEST = 8
MAX_AGE_DAYS = 7           # a candidate nobody triaged within a week is set aside unread
HEAD_RE = re.compile(r"^from (.*) <([^<>\s]+@[^<>\s]+)>")


def _esc(v) -> str:
    return html.escape(str(v or ""), quote=False)


def _norm(v) -> str:
    return " ".join(str(v or "").split()).casefold()


def _since(today: date | None) -> str:
    return ((today or date.today()) - timedelta(days=MAX_AGE_DAYS)).isoformat()


def pending(conn: sqlite3.Connection, limit: int = BATCH, today: date | None = None) -> list[dict]:
    return rows(conn, "SELECT * FROM evidence WHERE kind = ? AND judged_at IS NULL AND task_id IS NULL AND substr(at, 1, 10) >= ? ORDER BY id LIMIT ?",
                (KIND, _since(today), limit))


def sender(e: dict) -> tuple[str, str]:
    """(display name, address) from the first line the sweep wrote: `from Name <addr> · …`."""
    m = HEAD_RE.match(str(e.get("excerpt") or "").split("\n", 1)[0])
    return (m.group(1).strip(), m.group(2).lower()) if m else ("", "")


def build_prompt(cfg: dict, conn: sqlite3.Connection, cands: list[dict], today: date, clock: str = "") -> str:
    from .sync_file import open_rows_text, taxonomy_text
    blocks = []
    for c in cands:
        head, _, body = str(c.get("excerpt") or "").partition("\n")
        blocks.append(f"evidence {c['id']} | {str(c.get('at') or '')[:16]} | {_esc(c.get('account'))} | {_esc(c.get('label'))[:200]}\n"
                      f"  {_esc(head)[:300]}\n  {_esc(' '.join(body.split()))[:1500]}")
    waiting = rows(conn, "SELECT label FROM evidence WHERE kind = 'proposal' AND judged_at IS NULL ORDER BY id DESC LIMIT 20")
    fill = {"today": today.strftime("%A %Y-%m-%d") + (f" · it is {clock}" if clock else ""),
            "taxonomy": taxonomy_text(cfg), "efforts": ", ".join(C.get(cfg, "taxonomy.efforts", []) or ["XS", "S", "M", "L"]),
            "rows": open_rows_text(conn), "pending": "\n".join("- " + _esc(w["label"])[:160] for w in waiting) or "(nothing)",
            "mail": "\n\n".join(blocks)}
    tpl = PROMPT.read_text(encoding="utf-8")
    for k, v in fill.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    return tpl


def apply(conn: sqlite3.Connection, cfg: dict, data: dict, cands: list[dict]) -> dict:
    from .planner import _same_suggestion
    from .sync_file import _clean_task_fields
    by = {c["id"]: c for c in cands}
    taken = {_norm(r["name"]) for r in rows(conn, "SELECT name FROM tasks WHERE status = 'open'")}
    waiting: list[dict] = []
    for r in rows(conn, "SELECT ref FROM evidence WHERE kind = 'proposal' AND judged_at IS NULL"):
        try:
            waiting.append(json.loads(r["ref"]))
        except (TypeError, ValueError):
            pass
    suggested: list[str] = []
    staged: set[int] = set()
    over: set[int] = set()            # good suggestions past the cap: left unjudged, the next run takes them
    for s in data["suggest"][: BATCH * 2]:
        if not isinstance(s, dict) or type(s.get("evidence_id")) is not int:
            continue
        e = by.get(s["evidence_id"])
        if not e or e["id"] in staged:
            continue
        f = {k: v for k, v in _clean_task_fields(cfg, {k: s.get(k) for k in ("name", "project", "due", "hard", "effort", "context") if k in s}).items() if v is not None}
        if not f.get("name"):
            continue
        who, addr = sender(e)
        if s.get("reply_expected") and addr:
            f["counterpart"] = addr                          # Mailbird's address for the sender; the model supplies none
        card = {"op": "create", "fields": f,
                "mail": {"ref": e["ref"], "account": e.get("account"), "subject": e.get("label"), "sender": f"{who} <{addr}>" if addr else who, "at": e.get("at")}}
        if _norm(f["name"]) in taken or any(_same_suggestion(card, w) for w in waiting):
            continue                                         # already a task or already a card: set aside below
        if len(suggested) >= MAX_SUGGEST:
            over.add(e["id"])
            continue
        conn.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (NULL, 'proposal', ?, ?, ?, ?, ?)",
                     (json.dumps(card), e.get("account"), f"create: {f['name']}", str(s.get("reason") or "")[:300], now_iso()))
        conn.execute("UPDATE evidence SET judged_at = ?, verdict = 'staged' WHERE id = ?", (now_iso(), e["id"]))
        staged.add(e["id"]); suggested.append(f["name"]); waiting.append(card)
    aside = [i for i in by if i not in staged and i not in over]
    for i in aside:
        conn.execute("UPDATE evidence SET judged_at = ?, verdict = 'dismissed' WHERE id = ? AND judged_at IS NULL", (now_iso(), i))
    conn.commit()
    return {"suggested": suggested, "set_aside": len(aside)}


def run(conn: sqlite3.Connection, cfg: dict, today: date, *, runner=None, at: str | None = None) -> dict:
    from . import reminders
    from .claude import runner as _runner
    runner = runner or _runner.run
    at = at or reminders.local_now(cfg).strftime("%Y-%m-%dT%H:%M")
    conn.execute("UPDATE evidence SET judged_at = ?, verdict = 'dismissed' WHERE kind = ? AND judged_at IS NULL AND substr(at, 1, 10) < ?", (now_iso(), KIND, _since(today)))
    conn.commit()
    cands = pending(conn, BATCH, today)
    if not cands:
        return {"ok": True, "suggested": [], "set_aside": 0, "left": 0, "cost_usd": 0.0, "line": "no new mail"}
    started = time.monotonic()
    prompt = build_prompt(cfg, conn, cands, today, at[11:16])
    workdir = Path(RUNS_DIR) / today.isoformat() / at[11:16].replace(":", "")      # one directory per run: several a day
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "prompt.md").write_text(prompt, encoding="utf-8")
    # tools="": every word of this prompt's §4 was written by strangers, so the run gets no tool at all
    res = runner(cfg, "mail_triage", prompt, allowed_tools=[], tools="", workdir=workdir)
    (workdir / "result.txt").write_text(res.result_text or "", encoding="utf-8")
    ms = int((time.monotonic() - started) * 1000)
    err = None
    out = None
    if not res.ok or not isinstance(res.data, dict):
        err = res.error or "no JSON"
    elif not isinstance(res.data.get("suggest"), list) or not isinstance(res.data.get("dismiss"), list):
        err = "the reply is not {suggest: [...], dismiss: [...]}"       # some other JSON: judging the batch by it would set real mail aside
    else:
        try:
            out = apply(conn, cfg, res.data, cands)
        except Exception as e:                               # the run is paid for: log it even when applying it fails
            conn.rollback()
            err = f"applying the reply failed: {type(e).__name__}: {e}"
    if err:
        # The same batch failing twice is a batch that will keep failing (a mail the model refuses, a
        # reply that is never JSON): it is set aside unread, so it stops costing a run an hour and
        # stops holding newer mail behind it. The mail is still in the mailbox.
        first, mark = cands[0]["id"], get_settings(conn).get("state.mail_triage.failed")
        if isinstance(mark, dict) and mark.get("first") == first:
            for c in cands:
                conn.execute("UPDATE evidence SET judged_at = ?, verdict = 'dismissed' WHERE id = ? AND judged_at IS NULL", (now_iso(), c["id"]))
            set_setting(conn, "state.mail_triage.failed", None)
            err += f" · failed twice on the same {len(cands)} mail: set aside unread"
        else:
            set_setting(conn, "state.mail_triage.failed", {"first": first})
        log_run(conn, "mail-triage", ms, ok=False, summary="mail triage", error=err[:1000], cost_usd=res.cost_usd)
        conn.commit()
        return {"ok": False, "error": err[:1000], "cost_usd": res.cost_usd}
    set_setting(conn, "state.mail_triage.failed", None)
    left = len(pending(conn, 10**6, today))
    n = len(out["suggested"])
    line = (f"{len(cands)} mail: {n} suggested" + (" (" + "; ".join(s[:50] for s in out["suggested"][:3]) + ")" if n else "")
            + f", {out['set_aside']} set aside" + (f", {left} waiting for the next run" if left else ""))
    log_run(conn, "mail-triage", ms, ok=True, summary=line[:1000], cost_usd=res.cost_usd)
    conn.commit()
    return dict(out, ok=True, left=left, cost_usd=res.cost_usd, line=line)
