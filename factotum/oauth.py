"""Factotum as the OAuth 2.1 authorization server behind its own `/mcp` (SPEC.md §15 "the claude.ai
connector"). A claude.ai or Claude Desktop custom connector cannot send a bearer header, so it
discovers this server (RFC 9728 protected-resource metadata → RFC 8414 server metadata), registers
itself (RFC 7591, only the claude.ai callbacks are accepted), sends the user to the consent page,
and trades the code for tokens (PKCE S256 only, RFC 8707 resource = <public_url>/mcp). Tokens are
opaque, stored as SHA-256 hashes, and refresh tokens rotate. The consent page is the one place a
human is needed; it stays behind Cloudflare Access and trusts the identity header Access sets
(`consent_identity`). Everything a connector may do afterwards is what `/mcp` serves.

    resource_metadata(cfg), server_metadata(cfg)   -> the two well-known documents
    register(conn, cfg, body)                      -> RFC 7591 response (raises OAuthError)
    authorize_request(conn, cfg, params)           -> validated request for the consent page
    consent_identity(cfg, headers)                 -> the owner's email, or raises (403)
    consent_token(req) / consent_ok(req, token)    -> the consent form's CSRF token
    issue_code(conn, req) / error_url(req, err)    -> where to send the browser
    token(conn, cfg, form)                         -> the token response (raises OAuthError)
    check_access(conn, bearer)                     -> {client_id, name, scope} or None (stamps last_used_at)
    connectors(conn) / revoke_client(conn, id)     -> Settings → Connections
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from . import config as C
from .db import one, rows

DEFAULT_REDIRECTS = ["https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback"]
SCOPE = "factotum"
AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")
GRANTS = ("authorization_code", "refresh_token")
CODE_TTL_S = 300
MAX_CLIENTS = 25                      # a registration endpoint is public: beyond this, revoke one in Settings
UNUSED_CLIENT_TTL_S = 3600            # a registration that never finished its flow is dropped after an hour
CONSENT_WINDOW_S = 600
_SECRET = secrets.token_bytes(32)     # signs the consent form; per process, so a restart only re-renders the page
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


class OAuthError(Exception):
    def __init__(self, error: str, description: str = "", status: int = 400, redirect: dict | None = None):
        super().__init__(f"{error}: {description}" if description else error)
        self.error, self.description, self.status, self.redirect = error, description, status, redirect

    def body(self) -> dict:
        return {"error": self.error, "error_description": self.description} if self.description else {"error": self.error}


def enabled(cfg: dict) -> bool:
    return bool(C.get(cfg, "server.oauth.enabled", False))


def base_url(cfg: dict) -> str:
    return str(C.get(cfg, "server.public_url", "") or "").rstrip("/")


def resource(cfg: dict) -> str:
    return base_url(cfg) + "/mcp"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _plus(seconds: int) -> str:
    return (datetime.fromisoformat(_now()) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---- metadata -----------------------------------------------------------------------------------

def resource_metadata(cfg: dict) -> dict:
    return {"resource": resource(cfg), "authorization_servers": [base_url(cfg)], "scopes_supported": [SCOPE],
            "bearer_methods_supported": ["header"], "resource_name": "Factotum", "resource_documentation": base_url(cfg) + "/"}


def server_metadata(cfg: dict) -> dict:
    b = base_url(cfg)
    return {"issuer": b, "authorization_endpoint": f"{b}/oauth/authorize", "token_endpoint": f"{b}/oauth/token", "registration_endpoint": f"{b}/oauth/register",
            "response_types_supported": ["code"], "response_modes_supported": ["query"], "grant_types_supported": list(GRANTS),
            "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": list(AUTH_METHODS), "scopes_supported": [SCOPE],
            "authorization_response_iss_parameter_supported": True, "service_documentation": b + "/"}


# ---- clients --------------------------------------------------------------------------------------

def client(conn: sqlite3.Connection, client_id: str | None) -> dict | None:
    if not client_id:
        return None
    return one(conn, "SELECT * FROM oauth_clients WHERE client_id = ? AND revoked_at IS NULL", (str(client_id),))


def register(conn: sqlite3.Connection, cfg: dict, body) -> dict:
    """RFC 7591 with a short leash: every redirect_uri must be one of `server.oauth.redirect_uris`
    (the claude.ai callbacks by default), PKCE public clients need no secret, and the number of live
    registrations is capped because the endpoint is reachable by anyone."""
    if not isinstance(body, dict):
        raise OAuthError("invalid_client_metadata", "a JSON object is required")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and u for u in uris):
        raise OAuthError("invalid_redirect_uri", "redirect_uris must list at least one URI")
    allowed = [str(u).rstrip("/") for u in (C.get(cfg, "server.oauth.redirect_uris", None) or DEFAULT_REDIRECTS)]
    for u in uris:
        if u.rstrip("/") not in allowed:
            raise OAuthError("invalid_redirect_uri", f"{u} is not a known callback; see server.oauth.redirect_uris")
    method = str(body.get("token_endpoint_auth_method") or "none")
    if method not in AUTH_METHODS:
        raise OAuthError("invalid_client_metadata", f"token_endpoint_auth_method must be one of {', '.join(AUTH_METHODS)}")
    grants = body.get("grant_types") or ["authorization_code"]
    if not isinstance(grants, list) or any(g not in GRANTS for g in grants):
        raise OAuthError("invalid_client_metadata", "grant_types may hold authorization_code and refresh_token")
    now = _now()
    stale = (datetime.fromisoformat(now) - timedelta(seconds=UNUSED_CLIENT_TTL_S)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM oauth_clients WHERE revoked_at IS NULL AND last_used_at IS NULL AND created_at < ? AND client_id NOT IN (SELECT client_id FROM oauth_tokens)", (stale,))
    live = conn.execute("SELECT COUNT(*) FROM oauth_clients WHERE revoked_at IS NULL").fetchone()[0]
    if live >= MAX_CLIENTS:
        raise OAuthError("invalid_client_metadata", "too many registered clients; revoke one in Settings → Connections")
    cid = secrets.token_urlsafe(24)
    secret = secrets.token_urlsafe(32) if method != "none" else None
    name = " ".join(str(body.get("client_name") or "connector").split())[:100]
    conn.execute("INSERT INTO oauth_clients (client_id, name, secret_hash, redirect_uris_json, auth_method, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (cid, name, _h(secret) if secret else None, json.dumps(uris), method, now))
    conn.commit()
    out = {"client_id": cid, "client_id_issued_at": int(time.time()), "client_name": name, "redirect_uris": uris, "token_endpoint_auth_method": method,
           "grant_types": sorted(set(grants) | {"refresh_token"}), "response_types": ["code"], "scope": SCOPE}
    if secret:
        out["client_secret"], out["client_secret_expires_at"] = secret, 0
    return out


# ---- authorization --------------------------------------------------------------------------------

def authorize_request(conn: sqlite3.Connection, cfg: dict, p: dict) -> dict:
    """Validate an authorization request. A bad client or redirect_uri raises with no redirect (the
    browser must not be sent to an unknown place); anything after that raises with `redirect` set,
    and the caller sends the error back to the client's callback."""
    cl = client(conn, p.get("client_id"))
    if not cl:
        raise OAuthError("invalid_client", "unknown or revoked client_id", 400)
    uris = json.loads(cl["redirect_uris_json"])
    ru = p.get("redirect_uri") or (uris[0] if len(uris) == 1 else None)
    if not ru or ru not in uris:
        raise OAuthError("invalid_request", "redirect_uri is not one this client registered", 400)
    req = {"client": cl, "client_id": cl["client_id"], "redirect_uri": ru, "state": str(p.get("state") or "")}
    if p.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "response_type must be code", redirect=req)
    cc = str(p.get("code_challenge") or "")
    if not _VERIFIER.match(cc) or p.get("code_challenge_method") != "S256":
        raise OAuthError("invalid_request", "PKCE with code_challenge_method S256 is required", redirect=req)
    res = str(p.get("resource") or "").rstrip("/")
    if res and res != resource(cfg):
        raise OAuthError("invalid_target", f"this server is {resource(cfg)}", redirect=req)
    req.update(code_challenge=cc, scope=" ".join(str(p.get("scope") or SCOPE).split())[:200] or SCOPE, resource=resource(cfg))
    return req


def consent_identity(cfg: dict, headers) -> str:
    """Who is on the consent page. `cloudflare_access` (default): the email header Access sets after
    its login, and it must be one of owner.emails — the origin listens on loopback, so only Cloudflare
    can set it, and /oauth/authorize must never be in an Access bypass policy. `none`: no identity
    (a deployment where nothing but the owner can reach the port)."""
    gate = str(C.get(cfg, "server.oauth.consent_gate", "cloudflare_access") or "cloudflare_access")
    if gate == "none":
        return "local"
    email = str(headers.get("cf-access-authenticated-user-email") or "").strip().lower()
    if not email:
        raise OAuthError("access_denied", "open this page through the Access-protected hostname; no identity arrived with the request", 403)
    allowed = {str(e).strip().lower() for e in (C.get(cfg, "owner.emails", []) or []) if e}
    if email not in allowed:
        raise OAuthError("access_denied", f"{email} is not an owner address (owner.emails)", 403)
    return email


def _consent_msg(req: dict, window: int) -> bytes:
    return "|".join([req["client_id"], req["redirect_uri"], req["state"], req["code_challenge"], str(window)]).encode()


def consent_token(req: dict) -> str:
    return hmac.new(_SECRET, _consent_msg(req, int(time.time() // CONSENT_WINDOW_S)), "sha256").hexdigest()


def consent_ok(req: dict, token: str | None) -> bool:
    w = int(time.time() // CONSENT_WINDOW_S)
    return bool(token) and any(hmac.compare_digest(hmac.new(_SECRET, _consent_msg(req, x), "sha256").hexdigest(), str(token)) for x in (w, w - 1))


def _redirect(req: dict, params: dict) -> str:
    if req.get("state"):
        params["state"] = req["state"]
    return f"{req['redirect_uri']}?{urlencode(params)}"


def issue_code(conn: sqlite3.Connection, cfg: dict, req: dict) -> str:
    code = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO oauth_codes (code_hash, client_id, redirect_uri, code_challenge, scope, resource, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (_h(code), req["client_id"], req["redirect_uri"], req["code_challenge"], req["scope"], req["resource"], _plus(CODE_TTL_S)))
    conn.commit()
    return _redirect(req, {"code": code, "iss": base_url(cfg)})


def error_url(req: dict, err: OAuthError) -> str:
    return _redirect(req, {"error": err.error, "error_description": err.description} if err.description else {"error": err.error})


# ---- tokens ---------------------------------------------------------------------------------------

def _issue(conn: sqlite3.Connection, cfg: dict, client_id: str, scope: str) -> dict:
    access, refresh = "fct_" + secrets.token_urlsafe(32), "fcr_" + secrets.token_urlsafe(32)
    a_ttl = int(float(C.get(cfg, "server.oauth.access_token_hours", 24)) * 3600)
    r_ttl = int(float(C.get(cfg, "server.oauth.refresh_token_days", 90)) * 86400)
    now = _now()
    conn.execute("INSERT INTO oauth_tokens (token_hash, kind, client_id, scope, expires_at, created_at) VALUES (?, 'access', ?, ?, ?, ?)", (_h(access), client_id, scope, _plus(a_ttl), now))
    conn.execute("INSERT INTO oauth_tokens (token_hash, kind, client_id, scope, expires_at, created_at) VALUES (?, 'refresh', ?, ?, ?, ?)", (_h(refresh), client_id, scope, _plus(r_ttl), now))
    conn.execute("UPDATE oauth_clients SET last_used_at = ? WHERE client_id = ?", (now, client_id))
    conn.commit()
    return {"access_token": access, "token_type": "Bearer", "expires_in": a_ttl, "refresh_token": refresh, "scope": scope}


def token(conn: sqlite3.Connection, cfg: dict, form: dict) -> dict:
    cl = client(conn, form.get("client_id"))
    if not cl:
        raise OAuthError("invalid_client", "unknown or revoked client_id", 401)
    if cl["secret_hash"] and not hmac.compare_digest(_h(str(form.get("client_secret") or "")), cl["secret_hash"]):
        raise OAuthError("invalid_client", "client_secret does not match", 401)
    gt = form.get("grant_type")
    if gt == "authorization_code":
        row = one(conn, "SELECT * FROM oauth_codes WHERE code_hash = ?", (_h(str(form.get("code") or "")),))
        if not row or row["used_at"] or row["expires_at"] < _now() or row["client_id"] != cl["client_id"] or str(form.get("redirect_uri") or "") != row["redirect_uri"]:
            raise OAuthError("invalid_grant", "the code is unknown, used, expired, or not this client's")
        verifier = str(form.get("code_verifier") or "")
        digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not _VERIFIER.match(verifier) or not hmac.compare_digest(digest, row["code_challenge"]):
            raise OAuthError("invalid_grant", "code_verifier does not match the code_challenge")
        res = str(form.get("resource") or "").rstrip("/")
        if res and res != row["resource"]:
            raise OAuthError("invalid_target", f"this server is {row['resource']}")
        conn.execute("UPDATE oauth_codes SET used_at = ? WHERE code_hash = ?", (_now(), row["code_hash"]))
        return _issue(conn, cfg, cl["client_id"], row["scope"] or SCOPE)
    if gt == "refresh_token":
        row = one(conn, "SELECT * FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh'", (_h(str(form.get("refresh_token") or "")),))
        if not row or row["revoked_at"] or row["expires_at"] < _now() or row["client_id"] != cl["client_id"]:
            raise OAuthError("invalid_grant", "the refresh token is unknown, revoked, expired, or not this client's")
        conn.execute("UPDATE oauth_tokens SET revoked_at = ? WHERE id = ?", (_now(), row["id"]))      # rotation: the old one dies here
        return _issue(conn, cfg, cl["client_id"], row["scope"] or SCOPE)
    raise OAuthError("unsupported_grant_type", "authorization_code or refresh_token")


def check_access(conn: sqlite3.Connection, bearer: str | None) -> dict | None:
    """The client behind a live access token, or None. Stamps when the token and client last spoke."""
    if not bearer or not bearer.startswith("fct_"):
        return None
    row = one(conn, "SELECT t.*, c.name, c.revoked_at AS client_revoked FROM oauth_tokens t JOIN oauth_clients c ON c.client_id = t.client_id WHERE t.token_hash = ? AND t.kind = 'access'", (_h(bearer),))
    if not row or row["revoked_at"] or row["client_revoked"] or row["expires_at"] < _now():
        return None
    now = _now()
    conn.execute("UPDATE oauth_tokens SET last_used_at = ? WHERE id = ?", (now, row["id"]))
    conn.execute("UPDATE oauth_clients SET last_used_at = ? WHERE client_id = ?", (now, row["client_id"]))
    conn.commit()
    return {"client_id": row["client_id"], "name": row["name"], "scope": row["scope"]}


# ---- Settings → Connections ---------------------------------------------------------------------

def connectors(conn: sqlite3.Connection) -> list[dict]:
    now = _now()
    out = []
    for c in rows(conn, "SELECT client_id, name, auth_method, created_at, last_used_at, revoked_at FROM oauth_clients ORDER BY created_at DESC"):
        c["tokens"] = conn.execute("SELECT COUNT(*) FROM oauth_tokens WHERE client_id = ? AND kind = 'access' AND revoked_at IS NULL AND expires_at > ?", (c["client_id"], now)).fetchone()[0]
        out.append(c)
    return out


def revoke_client(conn: sqlite3.Connection, client_id: str) -> bool:
    now = _now()
    cur = conn.execute("UPDATE oauth_clients SET revoked_at = ? WHERE client_id = ? AND revoked_at IS NULL", (now, client_id))
    conn.execute("UPDATE oauth_tokens SET revoked_at = COALESCE(revoked_at, ?) WHERE client_id = ?", (now, client_id))
    conn.commit()
    return cur.rowcount > 0
