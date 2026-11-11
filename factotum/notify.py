"""ntfy publisher (SPEC.md §10), and the choice between it and the app's own notifications. The
service owns the schedule and calls `publish` at fire time, because the public server caps delay
at 3 days and 250 messages a day.

    publish(cfg, title, body, click=..., actions=[{"label", "url"}], tags=[...], priority=3) -> message id | None
    providers(cfg) -> {"ntfy"} | {"app"} | {"ntfy", "app"}    notify.provider; absent or unknown means ntfy
    ready(conn, cfg) -> ["ntfy", "app"]                       the chosen providers that can deliver now
    send_each([(provider, send), ...]) -> (delivered, results, errors)   each provider in its own try

`publish` returns None without sending when ntfy is off or no topic is configured. Actions are
`view` links (an `http` action could not carry the Cloudflare Access session). `transport` is for tests.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Callable

import httpx

from . import config as C


def enabled(cfg: dict) -> bool:
    return bool(C.get(cfg, "notify.ntfy.enabled", True)) and bool(C.get(cfg, "notify.ntfy.topic"))


def providers(cfg: dict) -> set[str]:
    return {"app": {"app"}, "both": {"ntfy", "app"}}.get(str(C.value(cfg, "notify.provider")), {"ntfy"})


def ready(conn: sqlite3.Connection, cfg: dict) -> list[str]:
    """The chosen providers that can deliver: ntfy when it is on with a topic, the app when a device subscribed."""
    from . import app_push
    chosen = providers(cfg)
    out = []
    if "ntfy" in chosen and enabled(cfg):
        out.append("ntfy")
    if "app" in chosen and app_push.device_count(conn) > 0:
        out.append("app")
    return out


def _delivered(provider: str, result: Any) -> bool:
    """ntfy reached its server when publish returned an id (None: off or no topic); the app when a device took it."""
    if provider == "ntfy":
        return result is not None
    return bool(result) and result.get("sent", 0) > 0


def send_each(sends: list[tuple[str, Callable[[], Any]]]) -> tuple[list[str], dict[str, Any], list[str]]:
    """Call each provider's send in order, each in its own try, so one failing never holds the other.
    Returns (the providers that delivered, every result returned, "provider: error" for each that
    raised). When none delivered and at least one raised, the first exception is raised again."""
    delivered, results, errors, first = [], {}, [], None
    for provider, send in sends:
        try:
            results[provider] = send()
        except Exception as e:
            errors.append(f"{provider}: {e}")
            first = first or e
            continue
        if _delivered(provider, results[provider]):
            delivered.append(provider)
    if not delivered and first is not None:
        raise first
    return delivered, results, errors


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
