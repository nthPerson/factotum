"""The Slack mention sweep (SPEC.md §6 "Slack"): every `sources.slack.sweep_minutes`, Python (no
Claude) reads each configured workspace's joined channels since that workspace's watermark and
keeps the messages that @-mention the token's user and are not the user's own:
  - one naming `Task-<n>` is evidence on that task (kind `slack_mention`), a mention (the fade
    clock moves) and a `slack_message` link; Review shows it as "Mentioned in Slack",
  - any other is evidence with no task: a suggestion card on Review (Create task / Dismiss) and a
    candidate in the planner's §3.
Reads only, bounded: joined channels (capped), one page of history per channel per pass, message
text treated as untrusted data. Permalinks are built from auth.test's team URL, so no extra calls.

    sweep(conn, cfg, now_local, clients=None) -> summary | None
    sweep_job(conn, cfg, now_local)           -> what the scheduler calls
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config as C, slack
from .db import add_link, add_mention, get_settings, rows, set_setting

TASK_RE = re.compile(r"\bTask-(\d{1,6})\b", re.I)
LEAD_MENTIONS_RE = re.compile(r"^(?:@\S+[\s,:;-]*)+")
EXCERPT = 500
PAGE = 200


def _since_ts(now_local: datetime, cfg: dict) -> str:
    tz = ZoneInfo(str(C.get(cfg, "owner.timezone", "UTC")))
    days = int(C.get(cfg, "sources.slack.lookback_days", 2))
    return f"{(now_local.replace(tzinfo=tz) - timedelta(days=days)).timestamp():.6f}"


def _evidence(conn, task_id, ref, account, label, excerpt, at) -> bool:
    if conn.execute("SELECT 1 FROM evidence WHERE kind = 'slack_mention' AND ref = ? AND task_id IS ?", (ref, task_id)).fetchone():
        return False
    conn.execute("INSERT INTO evidence (task_id, kind, ref, account, label, excerpt, at) VALUES (?, 'slack_mention', ?, ?, ?, ?, ?)",
                 (task_id, ref, account, (label or "")[:200], (excerpt or "")[:EXCERPT], at))
    return True


def _channels(client, w: dict, cap: int) -> list[dict]:
    out, cursor = [], None
    while True:
        r = client.call("conversations.list", {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": 200, "cursor": cursor})
        out += [c for c in (r.get("channels") or []) if c.get("is_member")]
        cursor = ((r.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor or len(out) >= cap:
            break
    wanted = [str(x).lstrip("#").lower() for x in (w.get("channels") or [])]
    if wanted:
        out = [c for c in out if c["id"].lower() in wanted or str(c.get("name") or "").lower() in wanted]
    return out[:cap]


def sweep(conn: sqlite3.Connection, cfg: dict, now_local: datetime, clients=None) -> str | None:
    st = get_settings(conn)
    cap = int(C.get(cfg, "sources.slack.max_channels", 40))
    tasks = {int(t["id"]): t for t in rows(conn, "SELECT id, status FROM tasks")}
    found = on_task = 0
    notes: list[str] = []
    ok_ws = 0
    make = clients or (lambda tok: slack.Client(tok))
    for w in slack.workspaces(cfg):
        if not w["ok"]:
            notes.append(f"{w['name']}: {w['why']}"); continue
        wcfg = next((x for x in (C.get(cfg, "sources.slack.workspaces", []) or []) if str(x.get("name") or "").strip() == w["name"]), {})
        client = make(os.environ[w["token_env"]])
        try:
            me = client.call("auth.test")
            uid = str(me.get("user_id") or "")
            base = str(me.get("url") or "").rstrip("/")
            mention = re.compile(rf"<@{re.escape(uid)}(?:\|[^>]*)?>")
            key = f"state.slack_sweep.last_ts.{w['name']}"
            wm = str(st.get(key) or _since_ts(now_local, cfg))
            newest = wm
            names: dict[str, str] = {}

            def who(u: str) -> str:
                if u not in names:
                    try:
                        user = client.call("users.info", {"user": u}).get("user") or {}
                        names[u] = str(user.get("real_name") or user.get("name") or u)
                    except slack.SlackError:
                        names[u] = u
                return names[u]

            for ch in _channels(client, wcfg, cap):
                try:
                    msgs = client.call("conversations.history", {"channel": ch["id"], "oldest": wm, "limit": PAGE}).get("messages") or []
                except slack.SlackError as e:            # one channel the token lost is not a failed sweep
                    notes.append(f"{w['name']} #{ch.get('name')}: {str(e)[:80]}"); continue
                for m in msgs:
                    ts = str(m.get("ts") or "")
                    if ts > newest:
                        newest = ts
                    if m.get("subtype") or m.get("user") == uid or not mention.search(str(m.get("text") or "")):
                        continue
                    text = slack.render(m.get("text"), {u: who(u) for u in set(re.findall(r"<@([A-Z0-9]+)", str(m.get("text") or "")))})
                    poster, chan = who(str(m.get("user") or "?")), str(ch.get("name") or ch["id"])
                    link = f"{base}/archives/{ch['id']}/p{ts.replace('.', '')}" if base else f"slack://{w['name']}/{ch['id']}/{ts}"
                    at = slack.ts_iso(ts) or now_local.isoformat(timespec="seconds")
                    short = LEAD_MENTIONS_RE.sub("", " ".join(text.split())).strip() or text.strip()
                    label = f"#{chan} · {poster}: {short[:80]}"
                    excerpt = f"{poster} in #{chan} ({w['name']}), {at[:16]}: {text}"
                    ids = [int(x) for x in TASK_RE.findall(text) if int(x) in tasks]
                    if ids:
                        for tid in dict.fromkeys(ids):
                            if _evidence(conn, tid, link, w["name"], label, excerpt, at):
                                add_mention(conn, tid, "slack", link, at)
                                add_link(conn, tid, "slack_message", link, label=label[:120], account=w["name"])
                                found += 1; on_task += 1
                    elif _evidence(conn, None, link, w["name"], label, excerpt, at):
                        found += 1
            if newest != wm:
                set_setting(conn, key, newest)
            ok_ws += 1
        except slack.SlackError as e:
            notes.append(f"{w['name']}: {str(e)[:120]}")
        finally:
            try:
                client.close()
            except Exception:   # noqa: BLE001
                pass
    conn.commit()
    if not ok_ws and notes:
        raise slack.SlackError("; ".join(notes[:3]))           # every workspace failed: the scheduler logs it and backs off
    if not found:
        return None
    return f"{found} mention{'s' if found != 1 else ''} ({on_task} on a task)" + (f" · {'; '.join(notes[:3])}" if notes else "")


def sweep_job(conn: sqlite3.Connection, cfg: dict, now_local: datetime) -> str | None:
    if not slack.workspaces(cfg):
        return None
    return sweep(conn, cfg, now_local)
