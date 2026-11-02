"""The Slack mention sweep (SPEC.md §6 "Slack"): every `sources.slack.sweep_minutes`, Python (no
Claude) reads each configured workspace's joined channels since each channel's watermark and keeps
the messages that @-mention the token's user and are not the user's own:
  - one naming an OPEN `Task-<n>` is evidence on that task (kind `slack_mention`), a mention (the
    fade clock moves) and a `slack_message` link; Review shows it as "Mentioned in Slack",
  - any other is evidence with no task: a suggestion card on Review (Create task / Dismiss) and a
    candidate in the planner's §3 (a hint there, never proof: planner.NON_PROVING),
  - one naming only closed tasks is skipped: nothing could judge it.
Reads only, bounded: joined channels (capped), one page of history per channel per pass, message
text treated as untrusted data; thread replies are not in a channel's history and are not swept.
Permalinks are built from auth.test's team URL, so no extra calls. No transaction is open while
Slack is on the line: each channel's hits are collected, names resolved, then written and
committed in one short step (the 2026-11-01 lock bug). A channel that fails keeps its watermark
and is named in the summary; a transport error is a note, not a failed sweep.

    sweep(conn, cfg, now_local, clients=None) -> summary | None
    sweep_job(conn, cfg, now_local)           -> what the scheduler calls
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from . import config as C, slack
from .db import add_link, add_mention, get_settings, rows, set_setting

TASK_RE = re.compile(r"\bTask-(\d{1,6})\b", re.I)
LEAD_MENTIONS_RE = re.compile(r"^(?:@\S+[\s,:;-]*)+")
# System notices that carry an @-mention but are nobody asking for anything; other subtypes
# (thread_broadcast, file_share, me_message) are people writing.
SKIP_SUBTYPES = {"bot_message", "channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name", "channel_archive", "channel_unarchive",
                 "group_join", "group_leave", "pinned_item", "unpinned_item", "reminder_add", "tombstone", "ekm_access_denied", "bot_add", "bot_remove"}
EXCERPT = 500
PAGE = 200
Errors = (slack.SlackError, httpx.HTTPError)


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
    wanted = [str(x).lstrip("#").lower() for x in (w.get("channels") or [])]
    out, cursor = [], None
    while True:
        r = client.call("conversations.list", {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": 200, "cursor": cursor})
        for c in r.get("channels") or []:
            if c.get("is_member") and (not wanted or c["id"].lower() in wanted or str(c.get("name") or "").lower() in wanted):
                out.append(c)
        cursor = ((r.get("response_metadata") or {}).get("next_cursor") or "").strip()
        if not cursor or len(out) >= cap:
            break
    return out[:cap]


def sweep(conn: sqlite3.Connection, cfg: dict, now_local: datetime, clients=None) -> str | None:
    st = get_settings(conn)
    cap = int(C.get(cfg, "sources.slack.max_channels", 40))
    status = {int(t["id"]): t["status"] for t in rows(conn, "SELECT id, status FROM tasks")}
    found = on_task = 0
    notes: list[str] = []          # something went wrong while reading: always reported
    config_notes: list[str] = []   # a workspace without a token: reported only beside activity
    ok_ws = 0
    make = clients or (lambda tok: slack.Client(tok))
    since = _since_ts(now_local, cfg)
    for w in slack.workspaces(cfg):
        if not w["ok"]:
            config_notes.append(f"{w['name']}: {w['why']}"); continue
        wcfg = next((x for x in (C.get(cfg, "sources.slack.workspaces", []) or []) if str(x.get("name") or "").strip() == w["name"]), {})
        client = make(os.environ[w["token_env"]])
        try:
            me = client.call("auth.test")
            uid = str(me.get("user_id") or "")
            base = str(me.get("url") or "").rstrip("/")
            mention = re.compile(rf"<@{re.escape(uid)}(?:\|[^>]*)?>")
            key = f"state.slack_sweep.last_ts.{w['name']}"
            wms = dict(st.get(key) or {}) if isinstance(st.get(key), dict) else {}
            names: dict[str, str] = {}

            def who(u: str) -> str:
                if u not in names:
                    try:
                        user = client.call("users.info", {"user": u}).get("user") or {}
                        names[u] = str(user.get("real_name") or user.get("name") or u)
                    except Errors:
                        names[u] = u
                return names[u]

            for ch in _channels(client, wcfg, cap):
                oldest = wms.get(ch["id"]) or since
                chan = str(ch.get("name") or ch["id"])
                try:
                    msgs = client.call("conversations.history", {"channel": ch["id"], "oldest": oldest, "limit": PAGE}).get("messages") or []
                except Errors as e:            # one channel the token lost is not a failed sweep; its watermark stays
                    notes.append(f"{w['name']} #{chan}: {type(e).__name__ if not isinstance(e, slack.SlackError) else ''}{str(e)[:80]}".replace(": :", ": ")); continue
                newest, hits = oldest, []
                for m in msgs:                 # read and resolve first: no row is written while Slack is still being asked
                    ts = str(m.get("ts") or "")
                    if ts > newest:
                        newest = ts
                    if m.get("subtype") in SKIP_SUBTYPES or m.get("user") == uid or not mention.search(str(m.get("text") or "")):
                        continue
                    named = [int(x) for x in TASK_RE.findall(str(m.get("text") or "")) if int(x) in status]
                    open_ids = list(dict.fromkeys(i for i in named if status[i] == "open"))
                    if named and not open_ids:
                        continue               # about closed rows only: nothing could judge it
                    text = slack.render(m.get("text"), {u: who(u) for u in set(re.findall(r"<@([A-Z0-9]+)", str(m.get("text") or "")))})
                    poster = who(str(m.get("user") or "?"))
                    link = f"{base}/archives/{ch['id']}/p{ts.replace('.', '')}" if base else f"slack://{w['name']}/{ch['id']}/{ts}"
                    at = slack.ts_iso(ts) or now_local.isoformat(timespec="seconds")
                    short = LEAD_MENTIONS_RE.sub("", " ".join(text.split())).strip() or text.strip()
                    hits.append((open_ids, link, f"#{chan} · {poster}: {short[:80]}", f"{poster} in #{chan} ({w['name']}), {at[:16]}: {text}", at))
                for open_ids, link, label, excerpt, at in hits:
                    if open_ids:
                        for tid in open_ids:
                            if _evidence(conn, tid, link, w["name"], label, excerpt, at):
                                add_mention(conn, tid, "slack", link, at)
                                add_link(conn, tid, "slack_message", link, label=label[:120], account=w["name"])
                                found += 1; on_task += 1
                    elif _evidence(conn, None, link, w["name"], label, excerpt, at):
                        found += 1
                if newest != oldest:
                    wms[ch["id"]] = newest
                    set_setting(conn, key, wms)
                conn.commit()                  # one short write per channel; the UI's Apply never waits on Slack
            ok_ws += 1
        except Errors as e:
            conn.rollback()
            notes.append(f"{w['name']}: {type(e).__name__}: {str(e)[:120]}")
        finally:
            try:
                client.close()
            except Exception:   # noqa: BLE001
                pass
    conn.commit()
    if not ok_ws and (notes or config_notes):
        raise slack.SlackError("; ".join((notes + config_notes)[:3]))      # every workspace failed: the scheduler logs it and backs off
    if not found and not notes:
        return None
    tail = "; ".join((notes + (config_notes if found else []))[:3])
    return f"{found} mention{'s' if found != 1 else ''} ({on_task} on a task)" + (f" · {tail}" if tail else "")


def sweep_job(conn: sqlite3.Connection, cfg: dict, now_local: datetime) -> str | None:
    if not slack.workspaces(cfg):
        return None
    return sweep(conn, cfg, now_local)
