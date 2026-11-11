"""Notifications from the app: the server's key pair and the devices that subscribed.

`push-keys.json` holds the server's P-256 key pair (base64url private scalar, public point,
created_at). It lives beside the database file, is written with mode 0600 and is never in git.
Only the public half is ever sent anywhere (to the page, so a browser can subscribe).
Every subscription is bound to the public key it was made with, so making a new pair
empties `push_subscriptions`. A pair is made only when the page asks for it
(`keys(conn, create=True)`); the sender never makes one, and an unreadable file is never
overwritten.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec

from . import webpush as W
from .db import now_iso

KEY_FILE = "push-keys.json"
ORIGIN_RE = re.compile(r"^https?://[^/?#\s]+$")


class KeyFileError(Exception):
    """The key file is missing or unreadable; the message names it."""


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


def subscribe(conn: sqlite3.Connection, endpoint: str, p256dh: str, auth: str, label: str, origin: str) -> int:
    """Validate and upsert a device by endpoint; returns its row id. Does not commit."""
    bad = ValueError("not a push service address")
    try:
        u = urlsplit(endpoint)
        host = u.hostname
    except ValueError:
        raise bad
    if u.scheme != "https" or not host or host.lower() == "localhost":
        raise bad
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise bad
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
    if not isinstance(origin, str) or not ORIGIN_RE.match(origin):
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
