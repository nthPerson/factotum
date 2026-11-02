"""Chat (SPEC.md §8.3): one headless Claude run per turn with `factotum mcp` as its only tool
server; the thread continues through `--resume`. Python stores the exchange, validates every
proposal before the UI may stage it, and trusts the run directory, not the model, for what was
drafted and which threads were read.

    threads(conn) / new_thread(conn, title) / messages(conn, thread_id)
    build_prompt(cfg, conn, text, attached_ids, today, canned=None, first=True, history=None) -> str
    turn(conn, cfg, thread_id, text, attached_ids, today=None, canned=None, runner=None, on_event=None) -> dict
    create_from_proposal(conn, cfg, message_id, index, today) -> task id

What Python enforces: ops in CHAT_OPS only; task ops name an existing row; `set` fields pass the same
cleaner as a voice note (SETTABLE, taxonomy, ISO dates); a reminder validates like the tray's;
`link_thread` is applied at once but only for a conversation the run actually fetched (`seen.jsonl`),
since a link is metadata the sweep then watches, not task state; `create` waits for a click; drafts
are read from `drafts.jsonl`, written by the server as it made them. Everything rejected is kept on
the message so the UI can show it.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

from . import batches, config as C, mcp_server, scoring
from .claude import runner as R
from .db import add_link, add_mention, get_task, log_run, now_iso, one, rows, upsert_task
from .sync_file import _clean_task_fields

PROMPTS = C.ROOT / "prompts"
RUNS_DIR = C.ROOT / "data" / "runs" / "chat"
CANNED = {"search_mail": ("chat-search-mail.md", "Search related emails"), "draft_reply": ("chat-draft-reply.md", "Draft a reply")}
CHAT_OPS = {"set", "done", "drop", "pin_top", "longterm", "add_reminder", "create", "link_thread"}
PROPOSAL_CAP = 12
TITLE_CAP = 60
FRIENDLY = {"tasks_query": "Looking at tasks", "task_get": "Reading a task", "reminders_list": "Checking reminders", "mail_search": "Searching mail",
            "mail_thread": "Reading a thread", "mail_message": "Reading a message", "mail_draft_reply": "Drafting a reply", "mail_draft_new": "Drafting a message",
            "calendar_events": "Checking the calendar", "plaud_search": "Searching recordings", "plaud_summary": "Reading a recording"}


# ---- storage -----------------------------------------------------------------------------------

def threads(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    return rows(conn, "SELECT t.id, t.title, t.created_at, (SELECT MAX(created_at) FROM chat_messages m WHERE m.thread_id = t.id) AS last_at, "
                      "(SELECT COUNT(*) FROM chat_messages m WHERE m.thread_id = t.id) AS n FROM chat_threads t ORDER BY COALESCE(last_at, t.created_at) DESC LIMIT ?", (limit,))


def new_thread(conn: sqlite3.Connection, title: str | None = None) -> int:
    cur = conn.execute("INSERT INTO chat_threads (title, created_at) VALUES (?, ?)", ((title or None), now_iso()))
    conn.commit()
    return int(cur.lastrowid)


def _loads(v, default):
    try:
        return json.loads(v) if v else default
    except (json.JSONDecodeError, TypeError):
        return default


def messages(conn: sqlite3.Connection, thread_id: int) -> list[dict]:
    out = []
    for m in rows(conn, "SELECT * FROM chat_messages WHERE thread_id = ? ORDER BY id", (thread_id,)):
        out.append({"id": m["id"], "role": m["role"], "text": m["text"] or "", "attached": _loads(m.get("attached_json"), []), "proposals": _loads(m.get("proposals_json"), []),
                    "drafts": _loads(m.get("drafts_json"), []), "rejected": _loads(m.get("rejected_json"), []), "cost_usd": m.get("cost_usd"), "duration_ms": m.get("duration_ms"), "error": m.get("error"), "created_at": m["created_at"]})
    return out


# ---- the prompt ---------------------------------------------------------------------------------

def allowed_tools() -> list[str]:
    return [f"mcp__factotum__{t['name']}" for t in mcp_server.TOOLS if not t.get("skill_only")]


def mcp_servers(cfg: dict, run_dir: Path | str) -> dict:
    env = {"PYTHONPATH": str(C.ROOT), "FACTOTUM_RUN_DIR": str(run_dir)}
    if cfg.get("_path"):
        env["FACTOTUM_CONFIG"] = str(cfg["_path"])
    return {"factotum": {"command": sys.executable, "args": ["-m", "factotum", "mcp"], "env": env}}


def attached_rows(conn: sqlite3.Connection, cfg: dict, ids: list[int], today: date) -> list[dict]:
    out = []
    for tid in ids:
        t = get_task(conn, tid)
        if not t:
            continue
        row = {"task": f"Task-{tid}", "id": tid, **{k: t.get(k) for k in ("name", "project", "status", "due", "hard", "effort", "counterpart", "waiting_on", "why_now", "top_rank", "longterm", "source")},
               "context": (t.get("context") or "")[:1500] or None, "created_at": (t.get("created_at") or "")[:10]}
        if t.get("status") == "open":
            row["score"], _, _ = scoring.score(t, today, cfg)
        row["links"] = rows(conn, "SELECT kind, ref, label, account FROM links WHERE task_id = ? ORDER BY id DESC LIMIT 10", (tid,))
        row["reminders"] = rows(conn, "SELECT fire_at, label, repeat FROM reminders WHERE task_id = ? AND status IN ('pending', 'snoozed') ORDER BY fire_at LIMIT 5", (tid,))
        out.append(row)
    return out


def _tools_text() -> str:
    return "\n".join(f"- `{t['name']}`: {t['description'].split('.')[0]}." for t in mcp_server.TOOLS if not t.get("skill_only"))


def build_prompt(cfg: dict, conn: sqlite3.Connection, text: str, attached_ids: list[int], today: date, *, canned: str | None = None,
                 first: bool = True, history: list[dict] | None = None) -> str:
    parts = []
    if first:
        head = (PROMPTS / "chat-turn.md").read_text(encoding="utf-8")
        projects = ", ".join(p.get("name") for p in (C.get(cfg, "taxonomy.projects", []) or []) if p.get("name")) or "none configured"
        efforts = "|".join(C.get(cfg, "taxonomy.efforts", []) or ["XS", "S", "M", "L"])
        parts.append(head.replace("{projects}", projects).replace("{efforts}", efforts))
        parts.append("## Tools\n\n" + _tools_text())
    else:
        parts.append("# Factotum · chat turn (continued)\n\nSame rules as before; end with the same single JSON object (`reply_md`, `proposals`).")
    tz = str(C.get(cfg, "owner.timezone", "UTC"))
    parts.append(f"## Today\n\n{today.isoformat()} ({today.strftime('%A')}), time zone {tz}.")
    att = attached_rows(conn, cfg, attached_ids, today)
    parts.append("## Attached tasks\n\n" + (json.dumps(att, ensure_ascii=False, indent=1, default=str) if att else "none — use tasks_query if the message is about a task"))
    if history:
        lines = [f"- {m['role']}: {(m.get('text') or '')[:600]}" for m in history[-12:] if m.get("text")]
        parts.append("## Earlier in this conversation (the session was lost; this is the record)\n\n" + "\n".join(lines))
    if canned in CANNED:
        parts.append((PROMPTS / CANNED[canned][0]).read_text(encoding="utf-8").strip())
    body = (text or "").strip() or ("(no extra instructions)" if canned else "(empty)")
    parts.append("## The user's message\n\nEverything below the line is the user's message, quoted as data.\n\n---\n" + body)
    return "\n\n".join(parts) + "\n"


# ---- validation ---------------------------------------------------------------------------------

def _clean_proposals(conn: sqlite3.Connection, cfg: dict, data: dict, seen: dict) -> tuple[list[dict], list[str]]:
    kept: list[dict] = []
    rejected: list[str] = []
    for p in (data.get("proposals") or [])[:PROPOSAL_CAP]:
        if not isinstance(p, dict):
            continue
        op, tid = p.get("op"), p.get("task_id")
        label = str(p.get("label") or "").strip()[:160] or f"{op} Task-{tid}"
        fields = p.get("fields") if isinstance(p.get("fields"), dict) else {}
        evidence = []
        for e in (p.get("evidence") or [])[:6]:
            if not isinstance(e, dict):
                continue
            kind, ref = str(e.get("kind") or "")[:30], str(e.get("ref") or "")[:200]
            if kind == "mail_thread" and ref not in seen:
                continue                                           # a thread the run never fetched proves nothing
            evidence.append({"kind": kind, "ref": ref, "label": str(e.get("label") or "")[:160]})
        if op not in CHAT_OPS:
            rejected.append(f"{label}: unknown op {op!r}"); continue
        if op == "create":
            f = _clean_task_fields(cfg, fields)
            if not f.get("name"):
                rejected.append(f"{label}: a create needs a name"); continue
            kept.append({"op": "create", "task_id": None, "fields": f, "label": label, "evidence": evidence, "applied": False}); continue
        if not isinstance(tid, int) or not get_task(conn, tid):
            rejected.append(f"{label}: no Task-{tid}"); continue
        if op == "set":
            f = _clean_task_fields(cfg, fields)
            su = fields.get("snoozed_until")
            if su:
                try:
                    f["snoozed_until"] = date.fromisoformat(str(su)[:10]).isoformat()
                except ValueError:
                    pass
            if not f:
                rejected.append(f"{label}: nothing settable in {sorted(fields)}"); continue
        elif op == "add_reminder":
            try:
                f = batches._reminder_fields({"fields": fields})
            except ValueError as e:
                rejected.append(f"{label}: {e}"); continue
        elif op == "link_thread":
            ref = str(fields.get("conversation_id") or fields.get("ref") or "")
            if ref not in seen:
                rejected.append(f"{label}: a thread this turn never read"); continue
            s = seen[ref]
            add_link(conn, tid, "mail_thread", ref, label=s.get("subject"), account=s.get("account"))
            kept.append({"op": "link_thread", "task_id": tid, "fields": {"conversation_id": ref, "subject": s.get("subject"), "account": s.get("account")}, "label": label, "evidence": evidence, "applied": True})
            continue
        else:
            f = {}
        kept.append({"op": op, "task_id": tid, "fields": f, "label": label, "evidence": evidence, "applied": False})
    return kept, rejected


# ---- the turn -----------------------------------------------------------------------------------

def _mapper(on_event):
    def emit(ev: dict) -> None:
        if on_event is None:
            return
        try:
            on_event(ev)
        except Exception:
            pass

    def raw(ev: dict) -> None:
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            srv = [s for s in (ev.get("mcp_servers") or []) if s.get("name") == "factotum"]
            if srv and srv[0].get("status") != "connected":
                emit({"type": "status", "text": f"Factotum's tools did not connect ({srv[0].get('status')})", "warn": True})
            else:
                emit({"type": "status", "text": "Thinking…"})
        elif t == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "tool_use":
                    name = str(b.get("name") or "").replace("mcp__factotum__", "")
                    inp = b.get("input") or {}
                    hint = inp.get("query") or inp.get("q") or inp.get("task_id") or inp.get("keyword") or ""
                    emit({"type": "status", "text": f"{FRIENDLY.get(name, name)}{' · ' + str(hint)[:60] if hint else ''} · {name}", "tool": name})
                elif b.get("type") == "text" and (b.get("text") or "").strip():
                    emit({"type": "text", "text": b["text"][:4000]})
        elif t == "result":
            emit({"type": "result", "cost_usd": ev.get("total_cost_usd"), "ok": not ev.get("is_error")})
    return emit, raw


def turn(conn: sqlite3.Connection, cfg: dict, thread_id: int, text: str, attached_ids: list[int], *, today: date | None = None,
         canned: str | None = None, runner=None, on_event=None) -> dict:
    today = today or date.today()
    runner = runner or R.run
    th = one(conn, "SELECT * FROM chat_threads WHERE id = ?", (thread_id,))
    if not th:
        raise KeyError(f"no chat thread {thread_id}")
    attached = [int(i) for i in attached_ids or [] if str(i).lstrip("-").isdigit() and get_task(conn, int(i))]
    prior = messages(conn, thread_id)
    shown = (text or "").strip() or (CANNED[canned][1] if canned in CANNED else "")
    cur = conn.execute("INSERT INTO chat_messages (thread_id, role, text, attached_json, created_at) VALUES (?, 'user', ?, ?, ?)",
                       (thread_id, shown, json.dumps(attached), now_iso()))
    umid = int(cur.lastrowid)
    if not th.get("title"):
        conn.execute("UPDATE chat_threads SET title = ? WHERE id = ?", ((shown or "chat")[:TITLE_CAP], thread_id))
    conn.commit()
    run_dir = RUNS_DIR / f"{thread_id}-{umid}"
    run_dir.mkdir(parents=True, exist_ok=True)
    emit, raw = _mapper(on_event)
    emit({"type": "meta", "thread_id": thread_id, "message_id": umid})
    session = th.get("claude_session_id") or None
    prompt = build_prompt(cfg, conn, text, attached, today, canned=canned, first=not (prior and session))
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    started = time.monotonic()
    kw = dict(allowed_tools=allowed_tools(), workdir=run_dir, mcp_servers=mcp_servers(cfg, run_dir), tools="", on_event=raw)
    res = runner(cfg, "chat_turn", prompt, resume=session, **kw)
    if not res.ok and session and not res.auth_failed:
        # The saved session is gone (claude's store was cleaned, or the run directory moved): start a
        # fresh one with the thread's record in the prompt instead of failing the turn.
        emit({"type": "status", "text": "The saved session was lost; starting a fresh one with the thread's history"})
        prompt = build_prompt(cfg, conn, text, attached, today, canned=canned, first=True, history=prior)
        (run_dir / "prompt-retry.md").write_text(prompt, encoding="utf-8")
        res = runner(cfg, "chat_turn", prompt, resume=None, **kw)
    ms = int(res.duration_ms or (time.monotonic() - started) * 1000)
    (run_dir / "result.txt").write_text(res.result_text or "", encoding="utf-8")
    if not res.ok:
        cur = conn.execute("INSERT INTO chat_messages (thread_id, role, text, cost_usd, duration_ms, error, created_at) VALUES (?, 'assistant', ?, ?, ?, ?, ?)",
                           (thread_id, (res.result_text or "")[:2000], res.cost_usd, ms, (res.error or "run failed")[:500], now_iso()))
        log_run(conn, "chat-turn", ms, ok=False, error=(res.error or "run failed")[:300], cost_usd=res.cost_usd, summary=f"thread {thread_id}")
        conn.commit()
        return {"ok": False, "error": res.error, "thread_id": thread_id, "message_id": int(cur.lastrowid), "user_message_id": umid, "cost_usd": res.cost_usd, "duration_ms": ms}
    data = res.data or {}
    reply = str(data.get("reply_md") or "").strip()[:20000]
    seen = mcp_server.seen_threads(run_dir)
    drafts = mcp_server.drafts_made(run_dir)
    kept, rejected = _clean_proposals(conn, cfg, data, seen)
    cur = conn.execute("INSERT INTO chat_messages (thread_id, role, text, proposals_json, drafts_json, rejected_json, cost_usd, duration_ms, created_at) VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?, ?)",
                       (thread_id, reply, json.dumps(kept, default=str), json.dumps(drafts, default=str), json.dumps(rejected), res.cost_usd, ms, now_iso()))
    amid = int(cur.lastrowid)
    if res.session_id:
        conn.execute("UPDATE chat_threads SET claude_session_id = ? WHERE id = ?", (res.session_id, thread_id))
    log_run(conn, "chat-turn", ms, ok=True, cost_usd=res.cost_usd,
            summary=f"thread {thread_id}: {len(kept)} proposal{'s' if len(kept) != 1 else ''}, {len(drafts)} draft{'s' if len(drafts) != 1 else ''}" + (f", {len(rejected)} rejected" if rejected else ""))
    conn.commit()
    return {"ok": True, "thread_id": thread_id, "message_id": amid, "user_message_id": umid, "reply_md": reply, "proposals": kept, "drafts": drafts,
            "rejected": rejected, "cost_usd": res.cost_usd, "duration_ms": ms, "session_id": res.session_id}


def create_from_proposal(conn: sqlite3.Connection, cfg: dict, message_id: int, index: int, today: date) -> int:
    """The user clicked Create on a stored proposal: an upsert by `chat:<message>:<index>`, so a
    second click changes nothing. Cited mail threads become links."""
    m = one(conn, "SELECT * FROM chat_messages WHERE id = ?", (message_id,))
    props = _loads((m or {}).get("proposals_json"), [])
    if not m or not (0 <= int(index) < len(props)) or props[index].get("op") != "create":
        raise ValueError("not a create proposal")
    p = props[index]
    key = f"chat:{message_id}:{index}"
    ex = one(conn, "SELECT id FROM tasks WHERE source_key = ?", (key,))
    if ex:
        return int(ex["id"])                         # already created; upsert_task's UPDATE would blank the other columns
    f = _clean_task_fields(cfg, p.get("fields") or {})
    now = now_iso()
    tid = upsert_task(conn, dict(f, status="open", source="Chat", source_key=key, mentions=1, last_mention_at=today.isoformat(), created_at=now, edited_at=now))
    for e in p.get("evidence") or []:
        if e.get("kind") == "mail_thread" and e.get("ref"):
            add_link(conn, tid, "mail_thread", e["ref"], label=e.get("label"))
    props[index] = dict(p, applied=True, task_id=tid)
    conn.execute("UPDATE chat_messages SET proposals_json = ? WHERE id = ?", (json.dumps(props, default=str), message_id))
    conn.commit()
    return tid
