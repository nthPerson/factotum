"""Mailbird Next's MCP server as a plain HTTP client (SPEC.md §6 "Mailbird", §2). Streamable HTTP
with a bearer token on the Windows host; from WSL it is reachable only with mirrored networking.
TLS verification may be off for that loopback URL only, never for anything else. This client is
read-only by construction: a tool outside READ_TOOLS is refused before any request is made.

    Client(cfg).call(tool, args) -> the tool's JSON (or text) result
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse

import httpx

from . import config as C

READ_TOOLS = {"list_accounts", "list_folders", "list_conversations", "search_conversations", "get_conversation",
              "get_message", "list_attachments", "get_attachment_content", "get_ui_state", "get_help", "get_send_status"}
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class MailbirdError(RuntimeError):
    pass


class Client:
    def __init__(self, cfg: dict, token: str | None = None, transport=None, timeout: float = 60):
        self.url = str(C.get(cfg, "sources.mail.url", "https://127.0.0.1:52000/mcp"))
        self.token = token or os.environ.get("MAILBIRD_TOKEN") or ""
        verify = bool(C.get(cfg, "sources.mail.tls_verify", True))
        if not verify and urlparse(self.url).hostname not in LOOPBACK:
            raise MailbirdError(f"tls_verify: false is allowed only for a loopback URL, not {self.url}")
        self._client = httpx.Client(verify=verify, timeout=timeout, transport=transport)
        self._id = 0
        self.session: str | None = None

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if self.session:
            h["Mcp-Session-Id"] = self.session
        return h

    def _rpc(self, method: str, params: dict | None) -> dict:
        self._id += 1
        body: dict = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params
        try:
            r = self._client.post(self.url, headers=self._headers(), json=body)
        except httpx.HTTPError as e:
            raise MailbirdError(f"Mailbird MCP unreachable at {self.url}: {type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise MailbirdError(f"Mailbird MCP {method} → HTTP {r.status_code}: {r.text[:200]}")
        if r.headers.get("mcp-session-id"):
            self.session = r.headers["mcp-session-id"]
        if "text/event-stream" in r.headers.get("content-type", ""):
            data = [line[5:].strip() for line in r.text.splitlines() if line.startswith("data:")]
            env = json.loads(data[-1]) if data else {}
        else:
            env = r.json() if r.content else {}
        if env.get("error"):
            raise MailbirdError(f"Mailbird MCP {method}: {env['error']}")
        return env.get("result") or {}

    def call(self, tool: str, args: dict | None = None):
        if tool not in READ_TOOLS:
            raise MailbirdError(f"{tool} is not a read tool; the sweep never writes to mail")
        res = self._rpc("tools/call", {"name": tool, "arguments": args or {}})
        content = res.get("content") or []
        text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
        if res.get("isError"):
            raise MailbirdError(f"{tool}: {text[:300]}")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    def close(self) -> None:
        self._client.close()
