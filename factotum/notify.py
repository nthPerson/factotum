"""ntfy publisher (SPEC.md §10). One function; the service owns the schedule and calls it at fire
time, because the public server caps delay at 3 days and 250 messages a day.

    publish(cfg, title, body, click=..., actions=[{"label", "url"}], tags=[...], priority=3) -> message id | None

Returns None without sending when ntfy is off or no topic is configured. Actions are `view`
links (an `http` action could not carry the Cloudflare Access session). `transport` is for tests.
"""
from __future__ import annotations

import os

import httpx

from . import config as C


def enabled(cfg: dict) -> bool:
    return bool(C.get(cfg, "notify.ntfy.enabled", True)) and bool(C.get(cfg, "notify.ntfy.topic"))


def publish(cfg: dict, title: str, body: str, *, click: str | None = None, actions: list[dict] | None = None,
            tags: list[str] | None = None, priority: int | None = None, transport=None) -> str | None:
    if not enabled(cfg):
        return None
    server = str(C.get(cfg, "notify.ntfy.server", "https://ntfy.sh")).rstrip("/")
    topic = C.get(cfg, "notify.ntfy.topic")
    headers = {"Title": _ascii(title)}
    if click:
        headers["Click"] = click
    if actions:
        headers["Actions"] = "; ".join(f"view, {_ascii(a['label']).replace(',', ' ')}, {a['url']}" for a in actions[:3])
    if tags:
        headers["Tags"] = ",".join(tags)
    if priority:
        headers["Priority"] = str(priority)
    token = os.environ.get("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=20, transport=transport) as client:
        r = client.post(f"{server}/{topic}", content=body.encode("utf-8"), headers=headers)
        r.raise_for_status()
        try:
            return str(r.json().get("id"))
        except ValueError:
            return "sent"


def _ascii(s: str) -> str:
    """HTTP headers are Latin-1 at best; ntfy reads them as UTF-8 only via the JSON API. Keep titles plain."""
    return "".join(ch for ch in s.encode("ascii", "ignore").decode() if ch.isprintable()).strip() or "Factotum"
