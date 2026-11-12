"""Notifications from the app: the server's key pair and the devices that subscribed.

`push-keys.json` holds the server's P-256 key pair (base64url private scalar, public point,
created_at). It lives beside the database file, is written with mode 0600 and is never in git.
Only the public half is ever sent anywhere (to the page, so a browser can subscribe).
Every subscription is bound to the public key it was made with, so making a new pair
empties `push_subscriptions`. A pair is made only when the page asks for it
(`keys(conn, create=True)`); the sender never makes one, and an unreadable file is never
overwritten.

A *notice* is what a caller wants shown: `{"title", "body", "path", "tag", "actions": [{"action",
"title", "path"}], "urgency", "ttl", "data"}` (`actions` and `data` may be absent; paths start with
`/`). `payload` turns it into the Push API's declarative message (`"web_push": 8030`) with its
links on each device's own origin, trimmed to `webpush.MAX_PLAINTEXT`; `send_all` delivers it to
every device and records what each push service answered (its status, never its body).

The server posts only to known push services (`PUSH_HOSTS`, `PUSH_SUFFIXES`, plus the config file's
`notify.app.push_hosts`), checked when a device subscribes and again before every send.
"""
from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.asymmetric import ec

from . import config as C
from . import webpush as W
from .db import now_iso

KEY_FILE = "push-keys.json"
ORIGIN_RE = re.compile(r"https?://[^/?#\s]+")            # used with fullmatch: a trailing newline is refused too
ELLIPSIS = "…"

# The push services a subscription may name (a browser picks its own; the server POSTs to it). Anything
# else is refused when a device subscribes AND again before every send, so the server never posts to an
# address a caller chose (an internal host, say). More hosts: `notify.app.push_hosts` in the config file
# (exact names). `.invalid` never resolves (RFC 2606); the UI checks use it.
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com")
PUSH_SUFFIXES = (".push.apple.com", ".notify.windows.com", ".invalid")
HOST_RE = re.compile(r"([a-z0-9-]+\.)*[a-z][a-z0-9-]*")    # a name whose last label starts with a letter: never 2130706433 or 0x7f000001
NOT_PUSH = "not a push service address"

TEST_NOTICE = {"title": "Factotum", "body": "Notifications work on this device.", "path": "/settings", "tag": "test",
               "actions": [], "urgency": "high", "ttl": 600}


class KeyFileError(Exception):
    """The key file is missing or unreadable; the message names it."""


class NothingSent(Exception):
    """At least one device existed and none accepted the push; the message is each outcome, joined."""


class Keys(NamedTuple):
    private: ec.EllipticCurvePrivateKey
    public: str          # base64url, what the page passes to `subscribe`


def key_path(conn: sqlite3.Connection) -> Path:
    for _, name, file in conn.execute("PRAGMA database_list"):
        if name == "main" and file:
            return Path(file).parent / KEY_FILE
    raise KeyFileError("the database has no file, so there is nowhere to keep the notification keys")


def _read(path: Path) -> Keys:
    bad = KeyFileError(f"the notification key file {path} is unreadable")
    try:
        data = json.loads(path.read_text())
        private = ec.derive_private_key(int.from_bytes(W.unb64u(data["private"]), "big"), ec.SECP256R1())
        public = data["public"]
        created = data["created_at"]
        if not isinstance(public, str) or not isinstance(created, str) or W.b64u(W.public_bytes(private)) != public:
            raise bad
    except KeyFileError:
        raise
    except Exception as e:
        raise bad from e
    return Keys(private, public)


def keys(conn: sqlite3.Connection, create: bool = False) -> Keys:
    path = key_path(conn)
    if path.exists():
        return _read(path)
    if not create:
        raise KeyFileError(f"the notification key file {path} is missing")
    private = ec.generate_private_key(ec.SECP256R1())
    public = W.b64u(W.public_bytes(private))
    scalar = W.b64u(private.private_numbers().private_value.to_bytes(32, "big"))
    body = json.dumps({"private": scalar, "public": public, "created_at": now_iso()})
    tmp = path.with_name(f".{KEY_FILE}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    conn.execute("DELETE FROM push_subscriptions")     # each was bound to the old public key
    conn.commit()
    return Keys(private, public)


def extra_push_hosts(cfg: dict | None) -> set[str]:
    hosts = C.get(cfg or {}, "notify.app.push_hosts", []) or []
    if not isinstance(hosts, list):
        return set()
    return {str(h).strip().lower().rstrip(".") for h in hosts if str(h).strip()}


def endpoint_problem(endpoint, cfg: dict | None = None) -> str | None:
    """Why the server must not post to this endpoint, or None when it is a known push service.

    `NOT_PUSH` for what is no push service at all (not https, credentials in it, an IP literal,
    localhost, a host that is not a plain DNS name or that a resolver may read as a number); a plain
    name that is not on the list gets a reason that names it and the config key that would allow it."""
    if not isinstance(endpoint, str):
        return NOT_PUSH
    try:
        u = urlsplit(endpoint)
        host = (u.hostname or "").rstrip(".")
        u.port                                            # raises on a port that is not a number
        other = httpx.URL(endpoint).raw_host.decode("ascii").lower().rstrip(".")
    except (ValueError, httpx.InvalidURL, UnicodeError):
        return NOT_PUSH
    if u.scheme != "https" or not host or u.username is not None or other != host:
        return NOT_PUSH                                   # the two parsers must agree on where it goes
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return NOT_PUSH
    if host == "localhost" or host.endswith(".localhost") or not HOST_RE.fullmatch(host):
        return NOT_PUSH
    if host in PUSH_HOSTS or host.endswith(PUSH_SUFFIXES) or host in extra_push_hosts(cfg):
        return None
    return f"{host} is not a known push service (add it to notify.app.push_hosts in the config file to allow it)"


def subscribe(conn: sqlite3.Connection, endpoint: str, p256dh: str, auth: str, label: str, origin: str,
              *, cfg: dict | None = None) -> int:
    """Validate and upsert a device by endpoint; returns its row id. Does not commit.
    `cfg` supplies `notify.app.push_hosts`; without it only the built-in push services are taken."""
    problem = endpoint_problem(endpoint, cfg)
    if problem:
        raise ValueError(problem)
    try:
        W.load_public(W.unb64u(p256dh))
    except Exception:
        raise ValueError("the device key is not a P-256 public key")
    try:
        secret = W.unb64u(auth)
    except Exception:
        secret = b""
    if len(secret) != 16:
        raise ValueError("the device secret must be 16 bytes")
    if not isinstance(origin, str) or not ORIGIN_RE.fullmatch(origin):
        raise ValueError("origin must be scheme://host[:port]")
    label = (label or "").strip()[:60] or "A device"
    conn.execute(
        """INSERT INTO push_subscriptions (endpoint, p256dh, auth, label, origin, created_at) VALUES (?,?,?,?,?,?)
           ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth, label = excluded.label,
             origin = excluded.origin, last_failure_at = NULL, last_error = NULL""",
        (endpoint, p256dh, auth, label, origin, now_iso()))
    return conn.execute("SELECT id FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()[0]


def unsubscribe(conn: sqlite3.Connection, *, endpoint: str | None = None, device_id: int | None = None) -> int:
    """Rows deleted. Does not commit."""
    if endpoint is not None:
        return conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).rowcount
    if device_id is not None:
        return conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (device_id,)).rowcount
    return 0


def devices(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute("SELECT * FROM push_subscriptions ORDER BY id")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def device_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0]


# ---------- the payload ----------

def _encode(message: dict) -> bytes:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def payload(notice: dict, origin: str) -> bytes:
    """The declarative message for one device, compact UTF-8 JSON, trimmed to fit `webpush.MAX_PLAINTEXT`
    bytes. Over the limit: the body is cut from the end (ending in "…", down to "…" alone), then the
    title (down to its first character and "…"), then the buttons are dropped. `navigate` (the path),
    the tag and `data` are never cut, so a notice whose path, tag or data alone is too large still
    comes back over the limit; `webpush.encrypt` then refuses it and `deliver` records that as a
    failure. Every notice Factotum makes (`reminders.notice`, the review nudge, the test) has a short
    path, tag and data.

    A notice may carry `"badge"`, an integer >= 0 (the Review count): it becomes the message's top-level
    `app_badge`, never a member of `notification`, because that is where WebKit reads it; it is counted
    in the size before anything is trimmed and never cut."""
    n: dict = {"title": notice["title"], "body": notice["body"], "navigate": origin + notice["path"]}
    if notice.get("tag"):
        n["tag"] = notice["tag"]
        n["renotify"] = True
    if notice.get("actions"):
        n["actions"] = [{"action": a["action"], "title": a["title"], "navigate": origin + a["path"]}
                        for a in notice["actions"]]
    if notice.get("data") is not None:
        n["data"] = notice["data"]
    message = {"web_push": 8030, "notification": n}
    b = notice.get("badge")
    if type(b) is int and b >= 0:
        message["app_badge"] = b
    raw = _encode(message)
    if len(raw) <= W.MAX_PLAINTEXT:
        return raw
    for field, keep in (("body", 0), ("title", 1)):
        text = n[field]
        if len(text) <= keep:
            continue                                     # nothing left to cut here
        lo, hi, best = keep, len(text) - 1, None         # the longest text[:k] + "…" that fits
        while lo <= hi:
            mid = (lo + hi) // 2
            n[field] = text[:mid] + ELLIPSIS
            if len(_encode(message)) <= W.MAX_PLAINTEXT:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        n[field] = text[:keep if best is None else best] + ELLIPSIS
        if best is not None:
            return _encode(message)
    n.pop("actions", None)
    return _encode(message)


def vapid_sub(cfg: dict, origin: str) -> str | None:
    """The VAPID `sub` claim: the public address when it is https, else the device's origin when that is."""
    public = str(C.get(cfg, "server.public_url", "") or "").rstrip("/")
    if public.startswith("https://"):
        return public
    if origin.startswith("https://"):
        return origin
    return None


# ---------- sending ----------

def deliver(device: dict, notice: dict, keys: Keys, cfg: dict, transport=None) -> dict:
    """One push to one device. Network only: never raises, writes nothing.

    The endpoint is checked against the push services again first (a row stored before the list, or
    written by hand, is never posted to). The error is one of a fixed set of reasons with the status
    code: a push service's own answer is never repeated, so a request the server makes cannot be
    turned into a way to read what some other host says."""
    out = {"id": device["id"], "ok": False, "status": None, "removed": False, "error": None, "factotum_refused": False}
    problem = endpoint_problem(device["endpoint"], cfg)
    if problem:                                           # Factotum's own refusal, not the push service's (the page says which)
        out["error"] = f"not sent: {problem}"
        out["factotum_refused"] = True
        return out
    host = urlsplit(device["endpoint"]).hostname
    try:
        r = W.send(device["endpoint"], device["p256dh"], device["auth"], payload(notice, device["origin"]),
                   keys.private, sub=vapid_sub(cfg, device["origin"]), ttl=notice["ttl"],
                   urgency=notice["urgency"], topic=notice["tag"], transport=transport)
    except httpx.HTTPError as e:
        out["error"] = f"could not reach {host}: {type(e).__name__}"
        return out
    except Exception as e:                                # a payload or key that cannot be encrypted
        out["error"] = f"could not send to {host}: {type(e).__name__}: {e}"
        return out
    out["status"] = r.status_code
    if 200 <= r.status_code < 300:
        out["ok"] = True
    elif r.status_code in (404, 410):
        out["removed"] = True
        out["error"] = f"the push service no longer knows this device ({r.status_code})"
    else:
        out["error"] = f"HTTP {r.status_code} from {host}"              # never the body: see the docstring
    return out


def record(conn: sqlite3.Connection, outcome: dict) -> None:
    """Write one delivery's outcome on its row (a removed device's row goes). Does not commit."""
    if outcome["removed"]:
        conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (outcome["id"],))
    elif outcome["ok"]:
        conn.execute("UPDATE push_subscriptions SET last_success_at = ? WHERE id = ?", (now_iso(), outcome["id"]))
    else:
        conn.execute("UPDATE push_subscriptions SET last_failure_at = ?, last_error = ? WHERE id = ?",
                     (now_iso(), outcome["error"], outcome["id"]))


def with_badge(conn: sqlite3.Connection, cfg: dict, notice: dict) -> dict:
    """`notice` with the Review count as `badge`, unless it has one or `notify.app.badge` is off."""
    if "badge" in notice or not C.value(cfg, "notify.app.badge"):
        return notice
    try:
        from . import review_queue
        return dict(notice, badge=review_queue.count(conn, cfg, date.today()))
    except Exception:
        return notice               # the number is never a reason not to send


# Every network call comes before the first write, so the SQLite write lock is never held across
# the network (CLAUDE.md: a thread holding it makes every UI Apply fail with "database is locked").
# send_all commits its own rows: a device the push service no longer knows must stay removed even
# when the caller rolls back (a reminder that stays pending because nothing was sent, say).
def send_all(conn: sqlite3.Connection, cfg: dict, notice: dict, *, transport=None) -> dict:
    """Push `notice` to every device. Raises KeyFileError (nothing sent or written) when the key file
    is missing or unreadable, and NothingSent after recording when no device accepted."""
    devs = devices(conn)
    res = {"devices": len(devs), "sent": 0, "removed": 0, "failed": 0, "errors": []}
    if not devs:
        return res
    notice = with_badge(conn, cfg, notice)
    k = keys(conn)
    outcomes = [deliver(d, notice, k, cfg, transport) for d in devs]
    for o in outcomes:
        record(conn, o)
        if o["ok"]:
            res["sent"] += 1
        elif o["removed"]:
            res["removed"] += 1
        else:
            res["failed"] += 1
        if o["error"]:
            res["errors"].append(o["error"])
    conn.commit()
    if res["sent"] == 0:
        raise NothingSent("; ".join(res["errors"]))
    return res


def send_test(conn: sqlite3.Connection, cfg: dict, endpoint: str, *, transport=None, lock=None) -> dict | None:
    """`TEST_NOTICE` to the one device with this endpoint; None when there is none. `lock` (the API's
    commit_lock) is taken for the write after the push, never across it."""
    cur = conn.execute("SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    row = cur.fetchone()
    if row is None:
        return None
    device = dict(zip([c[0] for c in cur.description], row))
    try:
        k = keys(conn)
    except KeyFileError as e:
        return {"id": device["id"], "ok": False, "status": None, "removed": False, "error": str(e), "factotum_refused": False}
    out = deliver(device, with_badge(conn, cfg, TEST_NOTICE), k, cfg, transport)
    with lock or contextlib.nullcontext():
        record(conn, out)
        conn.commit()
    return out
