"""Google Calendar (SPEC.md §6; docs/SETUP.md §6). OAuth Desktop-app client, consent once with
`python -m factotum google-auth`, token refreshed here. The REST API is called with httpx; the
Google libraries are used only for the consent flow and the token refresh.

    events(cfg, start, end)        -> [{id, summary, start, end, all_day, self_status, link}], local wall time
    sweep(conn, cfg, events, now)  -> closes open tasks linked (links.kind = 'calendar') to an event
                                      that has ended and that you accepted; automation batch, closed_by 'calendar'
    sweep_job(conn, cfg, now)      -> what the scheduler calls every sources.calendar.sweep_minutes

A task gets a `calendar` link from the Plaud run (phase 3) or by hand; nothing is matched by name,
so an auto-created event never closes anything (the old sync put every dated task on the calendar).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from . import batches, config as C
from .db import rows

SCOPES = ["https://www.googleapis.com/auth/calendar.events", "https://www.googleapis.com/auth/calendar.readonly"]
API = "https://www.googleapis.com/calendar/v3"


def paths(cfg: dict) -> tuple[Path, Path]:
    def p(key, default):
        v = Path(C.get(cfg, key, default))
        return v if v.is_absolute() else C.ROOT / v
    return p("sources.calendar.credentials_file", "config/google-credentials.json"), p("sources.calendar.token_file", "config/google-token.json")


def manual_token_kwargs(pasted: str) -> dict:
    """What `--manual` hands to fetch_token. Google redirects to `http://localhost:8765/?code=...`,
    and oauthlib refuses any non-https response URL (RFC 6749); the local-server flow rewrites the
    scheme the same way (google_auth_oauthlib.flow.run_local_server). A bare code is accepted too."""
    s = pasted.strip()
    if not s:
        raise ValueError("nothing pasted")
    if "://" not in s:
        return {"code": s}
    if s.startswith("http://"):
        s = "https://" + s[len("http://"):]
    return {"authorization_response": s}


def authorize(cfg: dict, manual: bool = False) -> Path:
    """Run the one-time consent flow and write the token file. Returns its path."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    creds_file, token_file = paths(cfg)
    if not creds_file.exists():
        raise SystemExit(f"{creds_file} is missing: download the Desktop-app client JSON (docs/SETUP.md §6 step 5)")
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
    if manual:
        flow.redirect_uri = "http://localhost:8765/"
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        print(f"\nOpen this URL in any browser, consent, then paste the address of the page you land on\n(even if it says the site cannot be reached):\n\n{url}\n")
        flow.fetch_token(**manual_token_kwargs(input("Pasted address: ")))
        creds = flow.credentials
    else:
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent", open_browser=True,
                                      authorization_prompt_message="Opening the browser for Google consent; if nothing opens, visit:\n{url}\n",
                                      success_message="Factotum is authorised. You can close this tab.")
    token_file.write_text(creds.to_json(), encoding="utf-8")
    token_file.chmod(0o600)
    return token_file


def access_token(cfg: dict) -> str:
    """Load the stored token, refresh it when expired, save it back."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    _, token_file = paths(cfg)
    if not token_file.exists():
        raise RuntimeError(f"{token_file} is missing: run `python -m factotum google-auth` (docs/SETUP.md §6)")
    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds.valid:
        if not creds.refresh_token:
            raise RuntimeError("the Google token has no refresh token: run `python -m factotum google-auth` again")
        creds.refresh(Request())   # raises RefreshError on invalid_grant (expired Testing-mode token, revoked consent)
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds.token


def _when(v: dict) -> tuple[str, bool]:
    if "date" in v:
        return v["date"], True
    return str(v.get("dateTime", ""))[:16], False


def fetch_events(cfg: dict, token: str, time_min: datetime, time_max: datetime, transport=None) -> list[dict]:
    cal = C.get(cfg, "sources.calendar.calendar_id", "primary")
    tz = str(C.get(cfg, "owner.timezone", "UTC"))
    params = {"timeMin": time_min.strftime("%Y-%m-%dT%H:%M:%S") + "Z" if time_min.tzinfo is None else time_min.isoformat(),
              "timeMax": time_max.strftime("%Y-%m-%dT%H:%M:%S") + "Z" if time_max.tzinfo is None else time_max.isoformat(),
              "singleEvents": "true", "orderBy": "startTime", "maxResults": "250", "timeZone": tz}
    out: list[dict] = []
    with httpx.Client(timeout=30, transport=transport) as client:
        page_token = None
        while True:
            if page_token:
                params["pageToken"] = page_token
            r = client.get(f"{API}/calendars/{cal}/events", params=params, headers={"Authorization": f"Bearer {token}"})
            r.raise_for_status()
            data = r.json()
            for e in data.get("items", []):
                if e.get("status") == "cancelled":
                    continue
                start, all_day = _when(e.get("start", {}))
                end, _ = _when(e.get("end", {}))
                me = next((a for a in e.get("attendees", []) if a.get("self")), None)
                out.append({"id": e.get("id"), "summary": e.get("summary"), "start": start, "end": end, "all_day": all_day,
                            "self_status": me.get("responseStatus") if me else None, "link": e.get("htmlLink")})
            page_token = data.get("nextPageToken")
            if not page_token:
                return out


def events(cfg: dict, start: datetime, end: datetime) -> list[dict]:
    return fetch_events(cfg, access_token(cfg), start, end)


def sweep(conn: sqlite3.Connection, cfg: dict, events: list[dict], now_local: datetime) -> str | None:
    by_id = {e["id"]: e for e in events if e.get("id")}
    links = rows(conn, "SELECT l.task_id, l.ref FROM links l JOIN tasks t ON t.id = l.task_id WHERE l.kind = 'calendar' AND t.status = 'open'")
    closes = []
    for l in links:
        e = by_id.get(l["ref"])
        if not e:
            continue
        conn.execute("UPDATE links SET last_seen_at = ? WHERE task_id = ? AND kind = 'calendar' AND ref = ?", (now_local.isoformat(), l["task_id"], l["ref"]))
        ended = e["end"] and (e["end"] <= now_local.strftime("%Y-%m-%dT%H:%M") if not e.get("all_day") else e["end"] <= now_local.date().isoformat())
        if ended and e.get("self_status") == "accepted":
            closes.append(l["task_id"])
    if not closes:
        conn.commit()
        return None
    batches.apply(conn, [{"task_id": tid, "op": "done"} for tid in closes], "automation", now_local.date(), closed_by="calendar")
    return "closed on passed, accepted events: " + " ".join(f"Task-{t}" for t in closes)


_cache: dict = {"at": None, "events": []}


def sweep_job(conn: sqlite3.Connection, cfg: dict, now_local: datetime) -> str | None:
    days = int(C.get(cfg, "sources.calendar.days_ahead", 14))
    evs = events(cfg, now_local - timedelta(days=1), now_local + timedelta(days=days))
    _cache.update(at=now_local, events=evs)
    return sweep(conn, cfg, evs, now_local)
