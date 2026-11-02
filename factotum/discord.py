"""Discord, read-only (SPEC.md §6 "Discord"): one bot token (.env DISCORD_BOT_TOKEN), invited to
every server named under `sources.discord.guilds`. The client only has `get`; `post` exists to
refuse, so nothing here can ever send, react or edit. Reading message content needs the bot's
"Message Content" privileged intent (docs/SETUP.md §13); without it `content` comes back empty.

    guilds(cfg)                  -> [{name, id}] from config
    find_guild(cfg, name)        -> the config entry (case-insensitive, prefix allowed)
    Client(token).get(path, params) -> JSON (raises DiscordError on a non-2xx)
    is_text(channel)             -> channel types that hold messages (text, announcement, threads)
    is_forum(channel)            -> forum / media channels: their posts are threads, they hold no messages
    is_thread(channel)           -> a thread (its parent_id names the channel or forum it lives in)

Discord has no message search for bots: `discord_search` in mcp_server reads the newest messages
of each text channel and active thread (bounded) and filters them here.
"""
from __future__ import annotations

import os
import time

import httpx

from . import config as C

API = "https://discord.com/api/v10"
USER_AGENT = "Factotum (https://github.com/nthPerson/factotum, 0.6)"
TEXT_TYPES = {0, 5, 10, 11, 12}          # guild text, announcement, announcement thread, public thread, private thread
THREAD_TYPES = {10, 11, 12}
FORUM_TYPES = {15, 16}                   # forum, media: each post is a thread


class DiscordError(Exception):
    pass


def guilds(cfg: dict) -> list[dict]:
    return [{"name": str(g.get("name") or "").strip(), "id": str(g.get("id") or "").strip()} for g in (C.get(cfg, "sources.discord.guilds", []) or []) if g.get("id")]


def find_guild(cfg: dict, name: str | None) -> dict:
    gs = guilds(cfg)
    if not gs:
        raise DiscordError("no servers under sources.discord.guilds in the config file")
    key = str(name or "").strip().lower()
    if not key:
        if len(gs) == 1:
            return gs[0]
        raise DiscordError("say which server: " + ", ".join(g["name"] for g in gs))
    exact = [g for g in gs if g["name"].lower() == key or g["id"] == key]
    pref = exact or [g for g in gs if g["name"].lower().startswith(key) or key in g["name"].lower()]
    if len(pref) != 1:
        raise DiscordError(f"no server matches {name!r}; known: " + ", ".join(g["name"] for g in gs))
    return pref[0]


def token() -> str:
    t = os.environ.get("DISCORD_BOT_TOKEN")
    if not t:
        raise DiscordError("DISCORD_BOT_TOKEN is not set in .env (docs/SETUP.md §13)")
    return t


def is_text(ch: dict) -> bool:
    return int(ch.get("type", -1)) in TEXT_TYPES


def is_forum(ch: dict) -> bool:
    return int(ch.get("type", -1)) in FORUM_TYPES


def is_thread(ch: dict) -> bool:
    return int(ch.get("type", -1)) in THREAD_TYPES


class Client:
    def __init__(self, tok: str, transport=None, timeout: float = 30):
        self._http = httpx.Client(timeout=timeout, transport=transport, headers={"Authorization": f"Bot {tok}", "User-Agent": USER_AGENT})

    def get(self, path: str, params: dict | None = None):
        for attempt in (1, 2):
            r = self._http.get(f"{API}{path}", params={k: v for k, v in (params or {}).items() if v is not None})
            if r.status_code == 429 and attempt == 1:
                try:
                    wait = float(r.json().get("retry_after", 1))
                except ValueError:
                    wait = 1.0
                time.sleep(min(wait, 5))
                continue
            break
        if r.status_code >= 300:
            try:
                msg = r.json().get("message") or r.text
            except ValueError:
                msg = r.text
            raise DiscordError(f"{r.status_code} {str(msg)[:200]}")
        return r.json()

    def post(self, path: str, body: dict | None = None):
        raise DiscordError("this client is read-only; Factotum never writes to Discord")

    def close(self) -> None:
        self._http.close()
