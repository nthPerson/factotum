"""The Plaud trigger (SPEC.md §6 "Plaud"): IMAP IDLE on the mailbox with the `plaud_trigger` role.
A mail from the Plaud sender whose subject carries the AutoFlow prefix starts one sync-file run.
Ported from plaud-sync's watcher with its lessons: `\\Seen` after handling, the uid remembered in
`trigger_seen` so a lost flag never reprocesses, one retry inside the run, and (legacy/README) the
sender checked on the parsed `From` address, not on the IMAP search alone; the subject is data.
"""
from __future__ import annotations

import email
import sqlite3
import threading
import time
import traceback
from datetime import timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from . import config as C
from .db import now_iso

IDLE_TIMEOUT_S = 29 * 60      # re-issue IDLE before Gmail's ~30 min cutoff
RECONNECT_S = 15
DEFAULT_AUTHSERV = "mx.google.com"


def _dom_ok(value: str, domain: str) -> bool:
    return value.strip().lower().lstrip("@") == domain


def _statements(text: str) -> list[list[str]] | None:
    """Tokenize an Authentication-Results value (RFC 8601 over RFC 5322): statements split on ';'
    and tokens on whitespace, both only outside quoted-strings and comments; comments (nested)
    are dropped; a quoted-string yields its content with backslash escapes resolved. Returns None
    when quotes or parentheses do not balance: such a header proves nothing."""
    stmts, toks, tok = [], [], []
    depth, quoted, esc = 0, False, False
    i = 0
    while i < len(text):
        ch = text[i]
        i += 1
        if quoted:
            if esc:
                tok.append(ch); esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                quoted = False
            else:
                tok.append(ch)
            continue
        if depth:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            continue
        if ch == "(":
            depth += 1
        elif ch == '"':
            quoted = True
        elif ch == ";":
            if tok:
                toks.append("".join(tok)); tok = []
            stmts.append(toks); toks = []
        elif ch.isspace():
            if tok:
                toks.append("".join(tok)); tok = []
        else:
            tok.append(ch)
    if quoted or depth:
        return None
    if tok:
        toks.append("".join(tok))
    stmts.append(toks)
    return stmts


def authenticated(headers, authserv: str, domain: str) -> bool:
    """True when the receiving server's own Authentication-Results header (authserv-id `authserv`,
    e.g. mx.google.com) shows dkim=pass or dmarc=pass for exactly `domain`. Only the topmost such
    header counts: the receiving server prepends its own and (RFC 8601 §5) removes any arriving
    header that claims its name, so a sender can forge From and append headers of its own, but not
    the first one. The value is tokenized with quoted-strings and comments honoured; an unbalanced
    header is rejected."""
    get_all = getattr(headers, "get_all", None)
    values = (get_all("Authentication-Results") or []) if get_all else (headers.get("Authentication-Results") or [])
    if isinstance(values, str):
        values = [values]
    if not values:
        return False
    domain = domain.lower()
    stmts = _statements(" ".join(str(values[0]).split()))
    if not stmts or not stmts[0] or stmts[0][0].lower() != authserv.lower():
        return False
    for toks in stmts[1:]:
        if not toks:
            continue
        method = toks[0].lower()
        props = dict(t.split("=", 1) for t in toks[1:] if "=" in t)
        if method == "dkim=pass" and any(_dom_ok(props.get(k, ""), domain) for k in ("header.i", "header.d")):
            return True
        if method == "dmarc=pass" and _dom_ok(props.get("header.from", ""), domain):
            return True
    return False


def _decode(raw) -> str:
    if raw is None:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


class PlaudTrigger:
    def __init__(self, cfg: dict, conn: sqlite3.Connection, handler, *, account: str, log=print):
        self.cfg, self.conn, self.handler, self.account, self.log = cfg, conn, handler, account, log
        self.sender = str(C.get(cfg, "sources.plaud.sender", "") or "").strip().lower()
        if "@" not in self.sender:
            raise ValueError("sources.plaud.sender must be the Plaud sender address (fail closed; see docs/SETUP.md §9)")
        self.domain = self.sender.rsplit("@", 1)[1]
        self.authserv = str(C.get(cfg, "sources.plaud.auth_server", DEFAULT_AUTHSERV) or DEFAULT_AUTHSERV)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- one pass over the mailbox -----------------------------------------------------------

    def _seen(self, uid: int) -> bool:
        return self.conn.execute("SELECT 1 FROM trigger_seen WHERE account = ? AND uid = ?", (self.account, uid)).fetchone() is not None

    def _remember(self, uid: int, subject: str, ok: bool | None) -> None:
        self.conn.execute("INSERT OR REPLACE INTO trigger_seen (account, uid, subject, at, ok) VALUES (?, ?, ?, ?, ?)",
                          (self.account, uid, subject[:300], now_iso(), None if ok is None else int(ok)))
        self.conn.commit()

    def process_mailbox(self, server) -> int:
        """Handle every unseen Plaud mail once. Returns how many runs were started."""
        sender = self.sender
        search_from = str(C.get(self.cfg, "sources.plaud.from_filter", "") or sender)
        prefix = str(C.get(self.cfg, "sources.plaud.subject_prefix", "") or "")
        criteria = ["UNSEEN"] + (["FROM", search_from] if search_from else [])
        started = 0
        for uid in server.search(criteria):
            uid = int(uid)
            if self._seen(uid):
                continue
            raw = server.fetch([uid], ["RFC822"])[uid][b"RFC822"]
            msg = email.message_from_bytes(raw)
            subject = _decode(msg.get("Subject")).replace("\r", " ").replace("\n", " ").strip()
            from_addr = parseaddr(msg.get("From", ""))[1].lower()
            if from_addr != sender:
                self.log(f"trigger: uid={uid} from {from_addr!r} is not {sender!r}; ignored")
                continue
            if not authenticated(msg, self.authserv, self.domain):
                self.log(f"trigger: uid={uid} claims {sender!r} but {self.authserv} shows no DKIM/DMARC pass for {self.domain}; ignored")
                continue
            if prefix and not subject.lower().startswith(prefix.lower()):
                continue
            try:
                received = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat()
            except Exception:
                received = now_iso()
            self.log(f"trigger: uid={uid} {subject!r}")
            ok = None
            try:
                res = self.handler(subject, received)
                ok = bool(res.get("ok")) if isinstance(res, dict) else None
            except Exception:
                traceback.print_exc()
                ok = False
            try:
                server.add_flags([uid], [b"\\Seen"])
            except Exception as e:
                self.log(f"trigger: could not mark uid={uid} seen: {e}")
            self._remember(uid, subject, ok)
            started += 1
        return started

    # ---- the thread --------------------------------------------------------------------------

    def run_forever(self, imap_settings: dict) -> None:
        from imapclient import IMAPClient
        while not self._stop.is_set():
            try:
                with IMAPClient(imap_settings["host"], port=int(imap_settings.get("port") or 993), use_uid=True, ssl=True) as server:
                    server.login(imap_settings["user"], imap_settings["password"])
                    server.select_folder(imap_settings.get("folder") or "INBOX")
                    self.log(f"trigger: connected as {imap_settings['user']}; clearing the backlog, then idling")
                    self.process_mailbox(server)
                    while not self._stop.is_set():
                        server.idle()
                        responses = server.idle_check(timeout=IDLE_TIMEOUT_S)
                        server.idle_done()
                        if responses:
                            self.process_mailbox(server)
            except Exception as e:
                self.log(f"trigger: connection error ({type(e).__name__}: {e}); reconnecting in {RECONNECT_S}s")
                self._stop.wait(RECONNECT_S)

    def start(self, imap_settings: dict) -> None:
        self._thread = threading.Thread(target=self.run_forever, args=(imap_settings,), name="factotum-plaud-trigger", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def imap_settings() -> dict | None:
    """IMAP_* from .env (the brief's SMTP falls back to the same account). None when unset."""
    import os
    user, password = os.environ.get("IMAP_USER"), os.environ.get("IMAP_PASSWORD")
    if not user or not password:
        return None
    return {"host": os.environ.get("IMAP_HOST", "imap.gmail.com"), "port": os.environ.get("IMAP_PORT", "993"), "user": user,
            "password": password, "folder": os.environ.get("IMAP_FOLDER", "INBOX")}
