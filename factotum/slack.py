"""Slack, read-only (SPEC.md §6 "Slack"): several workspaces, one token each, named in config and
kept in .env. The client refuses any Web API method outside READ_METHODS before a request is made,
so a Chat turn or a skill can search and read but never post, react or edit.

    workspaces(cfg)                -> [{name, token_env, ok, why}]   (ok = the token is set)
    find_workspace(cfg, name)      -> the config entry (case-insensitive, prefix allowed)
    Client(token).call(method, params) -> the API payload (raises SlackError on ok: false)
    render(text, names)            -> <@U1> → @Name, <#C1|general> → #general, <url|label> → label (url)
    ts_iso(ts)                     -> "2025-11-01T11:06:40Z"

A `search.messages` call needs a USER token (xoxp-…, scope search:read); a bot token gets
`not_allowed_token_type`, which the client turns into a sentence. Everything else works with either
token kind given channels:history / groups:history / channels:read / users:read.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone

import httpx

from . import config as C

API = "https://slack.com/api"
READ_METHODS = {"auth.test", "team.info", "conversations.list", "conversations.info", "conversations.history", "conversations.replies",
                "search.messages", "users.info", "users.list"}
FRIENDLY = {"not_allowed_token_type": "this method needs a user token (xoxp-…) with search:read; the workspace's token is a bot token",
            "missing_scope": "the token lacks a scope this method needs (channels:history, groups:history, channels:read, users:read, search:read)",
            "invalid_auth": "the token was rejected (revoked, or the wrong workspace)", "channel_not_found": "no such channel, or the token's user is not in it",
            "not_in_channel": "the token's user is not a member of that channel", "ratelimited": "Slack rate-limited the request; try again in a minute"}
_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
_CHANNEL = re.compile(r"<#([A-Z0-9]+)\|([^>]*)>")
_LINK = re.compile(r"<(https?://[^|>]+)\|([^>]*)>")
_BARE = re.compile(r"<(https?://[^>]+)>")


class SlackError(Exception):
    pass


def workspaces(cfg: dict) -> list[dict]:
    out = []
    for w in C.get(cfg, "sources.slack.workspaces", []) or []:
        name, env = str(w.get("name") or "").strip(), str(w.get("token_env") or "").strip()
        ok = bool(env and os.environ.get(env))
        out.append({"name": name, "token_env": env, "ok": ok, "why": None if ok else (f"{env} is not set in .env" if env else "no token_env in config")})
    return out


def find_workspace(cfg: dict, name: str | None) -> dict:
    ws = workspaces(cfg)
    if not ws:
        raise SlackError("no workspaces under sources.slack.workspaces in the config file")
    key = str(name or "").strip().lower()
    if not key:
        if len(ws) == 1:
            return ws[0]
        raise SlackError("say which workspace: " + ", ".join(w["name"] for w in ws))
    exact = [w for w in ws if w["name"].lower() == key]
    pref = exact or [w for w in ws if w["name"].lower().startswith(key) or key in w["name"].lower()]
    if len(pref) != 1:
        raise SlackError(f"no workspace matches {name!r}; known: " + ", ".join(w["name"] for w in ws))
    w = pref[0]
    if not w["ok"]:
        raise SlackError(f"workspace {w['name']}: {w['why']}")
    return w


class Client:
    def __init__(self, token: str, transport=None, timeout: float = 30):
        self.token = token
        self._http = httpx.Client(timeout=timeout, transport=transport, headers={"Authorization": f"Bearer {token}"})

    def call(self, method: str, params: dict | None = None):
        if method not in READ_METHODS:
            raise SlackError(f"{method} is not a read method; this client never writes to Slack")
        data = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in (1, 2):
            r = self._http.post(f"{API}/{method}", data=data)
            if r.status_code == 429 and attempt == 1:
                time.sleep(min(float(r.headers.get("Retry-After", "1") or 1), 5))
                continue
            break
        if r.status_code != 200:
            raise SlackError(f"{method} → HTTP {r.status_code} {r.text[:200]}")
        body = r.json()
        if not body.get("ok"):
            err = str(body.get("error") or "unknown_error")
            raise SlackError(f"{method}: {FRIENDLY.get(err, err)}" + (f" ({body.get('needed')})" if body.get("needed") else ""))
        return body

    def close(self) -> None:
        self._http.close()


def ts_iso(ts) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def render(text: str | None, names: dict[str, str]) -> str:
    """Slack's mrkdwn references, made readable. `names` maps user ids to display names."""
    s = str(text or "")
    s = _MENTION.sub(lambda m: "@" + names.get(m.group(1), m.group(1)), s)
    s = _CHANNEL.sub(lambda m: "#" + (m.group(2) or m.group(1)), s)
    s = _LINK.sub(lambda m: f"{m.group(2)} ({m.group(1)})", s)
    s = _BARE.sub(lambda m: m.group(1), s)
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
