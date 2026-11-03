"""Factotum as the OAuth 2.1 authorization server behind its own `/mcp` (SPEC.md §15 "the claude.ai
connector"). A claude.ai or Claude Desktop custom connector cannot send a bearer header, so it
discovers this server (RFC 9728 protected-resource metadata → RFC 8414 server metadata), identifies
itself — by its published Client ID Metadata Document (CIMD: the client_id is an https URL on
claude.ai / claude.com whose JSON names the client and its redirect URIs; nothing is registered)
or by registering (RFC 7591, only the claude.ai callbacks are accepted) — sends the user to the consent page,
and trades the code for tokens (PKCE S256 only, RFC 8707 resource = <public_url>/mcp). Tokens are
opaque, stored as SHA-256 hashes, and refresh tokens rotate. The consent page is the one place a
human is needed; it stays behind Cloudflare Access and verifies the signed assertion Access adds
to every request (`consent_identity`: RS256 against the team's published certs, audience = the
Access application, email ∈ owner.emails), and a connector that was never granted is approved only
while the owner has armed Settings → Connections for it (`armed`), so a link somebody else sends
cannot be approved by reflex. Everything a connector may do afterwards is what `/mcp` serves.

    resource_metadata(cfg), server_metadata(cfg)   -> the two well-known documents
    register(conn, cfg, body)                      -> RFC 7591 response (raises OAuthError)
    client(conn, client_id, cfg) / remember_client -> a registered client, or a CIMD client from its document (stored once consented)
    authorize_request(conn, cfg, params)           -> validated request for the consent page
    consent_identity(cfg, headers)                 -> the owner's email, or raises (403)
    arm(conn) / armed(conn)                        -> the window in which a new connector may be approved
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

import httpx

from . import config as C
from .db import get_settings, one, rows, set_setting

DEFAULT_REDIRECTS = ["https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback"]
SCOPE = "factotum"
AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")
GRANTS = ("authorization_code", "refresh_token")
CODE_TTL_S = 300
MAX_CLIENTS = 25                      # a registration endpoint is public: beyond this, revoke one in Settings
UNUSED_CLIENT_TTL_S = 3600            # a registration that never finished its flow is dropped after an hour
CONSENT_WINDOW_S = 600
CIMD_HOSTS = ["claude.ai", "claude.com"]   # hosts whose published client documents are accepted as client_ids (server.oauth.cimd_hosts)
CIMD_TTL_S = 3600
CIMD_MAX_BYTES = 16384
_CIMD: dict[str, tuple[float, dict]] = {}
LOOPBACK = ("localhost", "127.0.0.1", "[::1]")
ARM_MINUTES = 10                      # Settings → "Allow a new connector": how long a never-granted client may be approved
ARM_KEY = "state.oauth.arm_until"
CERTS_TTL_S = 3600
_CERTS: dict[str, tuple[float, list]] = {}
_SECRET = secrets.token_bytes(32)     # signs the consent form; per process, so a restart only re-renders the page
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


class OAuthError(Exception):
    def __init__(self, error: str, description: str = "", status: int = 400, redirect: dict | None = None):
        super().__init__(f"{error}: {description}" if description else error)
        self.error, self.description, self.status, self.redirect = error, description, status, redirect

    def body(self) -> dict:
        return {"error": self.error, "error_description": self.description} if self.description else {"error": self.error}


def enabled(cfg: dict) -> bool:
    """On only with a public URL: without one there is nothing to advertise as issuer or resource."""
    return bool(C.get(cfg, "server.oauth.enabled", False)) and bool(base_url(cfg))


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
            "client_id_metadata_document_supported": True, "authorization_response_iss_parameter_supported": True, "service_documentation": b + "/"}


# ---- clients --------------------------------------------------------------------------------------

def is_cimd(cfg: dict | None, client_id: str | None) -> bool:
    """A client_id that is a Client ID Metadata Document URL: https, a host from
    server.oauth.cimd_hosts (no port, no user info), a real path, no query or fragment."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(str(client_id or ""))
    except ValueError:
        return False
    hosts = [str(h).lower() for h in (C.get(cfg or {}, "server.oauth.cimd_hosts", None) or CIMD_HOSTS)]
    return u.scheme == "https" and u.netloc.lower() in hosts and len(u.path) > 1 and not u.query and not u.fragment and "@" not in u.netloc


def _fetch_cimd(url: str) -> dict:
    """The document itself. Monkeypatched by tests."""
    r = httpx.get(url, timeout=10, follow_redirects=False, headers={"Accept": "application/json"})
    r.raise_for_status()
    if len(r.content) > CIMD_MAX_BYTES:
        raise ValueError("client metadata document too large")
    return r.json()


def cimd_document(cfg: dict, url: str) -> dict:
    """Fetch (cached) and validate a client's published document: it must name itself (client_id ==
    the URL it came from), be a public client, and list redirect URIs."""
    at, doc = _CIMD.get(url, (0.0, None))
    if doc is None or time.time() - at > CIMD_TTL_S:
        try:
            doc = _fetch_cimd(url)
        except (httpx.HTTPError, ValueError) as e:
            raise OAuthError("invalid_client", f"the client's metadata document could not be read ({type(e).__name__})", 400) from e
        _CIMD[url] = (time.time(), doc)
    if not isinstance(doc, dict) or doc.get("client_id") != url:
        raise OAuthError("invalid_client", "the client's metadata document does not name itself (client_id must equal its URL)", 400)
    if str(doc.get("token_endpoint_auth_method") or "none") != "none":
        raise OAuthError("invalid_client", "a client identified by its document is a public client (token_endpoint_auth_method none)", 400)
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and u for u in uris):
        raise OAuthError("invalid_client", "the client's metadata document lists no redirect_uris", 400)
    return doc


def client(conn: sqlite3.Connection, client_id: str | None, cfg: dict | None = None) -> dict | None:
    """A live registered client; or, for a CIMD client_id, the client its document describes — not
    stored until the owner reaches the consent page (`remember_client`), and revived there after a
    revocation. None for anything else."""
    if not client_id:
        return None
    row = one(conn, "SELECT * FROM oauth_clients WHERE client_id = ?", (str(client_id),))
    if row and not row["revoked_at"]:
        return row
    if cfg is not None and is_cimd(cfg, client_id):
        doc = cimd_document(cfg, str(client_id))
        name = " ".join(str(doc.get("client_name") or "connector").split())[:100]
        return {"client_id": str(client_id), "name": name, "secret_hash": None, "redirect_uris_json": json.dumps(doc["redirect_uris"]), "auth_method": "cimd",
                "created_at": row["created_at"] if row else _now(), "revoked_at": None, "last_used_at": row["last_used_at"] if row else None, "_unsaved": True}
    return None


def remember_client(conn: sqlite3.Connection, cl: dict) -> None:
    """Store (or revive) a CIMD client once the owner is on its consent page."""
    if not cl.get("_unsaved"):
        return
    conn.execute("INSERT INTO oauth_clients (client_id, name, secret_hash, redirect_uris_json, auth_method, created_at) VALUES (?, ?, NULL, ?, 'cimd', ?) "
                 "ON CONFLICT(client_id) DO UPDATE SET name = excluded.name, redirect_uris_json = excluded.redirect_uris_json, revoked_at = NULL",
                 (cl["client_id"], cl["name"], cl["redirect_uris_json"], cl["created_at"]))
    conn.commit()


def _loopback(uri: str):
    from urllib.parse import urlsplit
    u = urlsplit(uri)
    return (u.hostname or "").lower() in ("localhost", "127.0.0.1", "::1") and u.scheme == "http"


def _redirect_ok(cfg: dict, cl: dict, ru: str) -> bool:
    """A registered client's URIs are already allowlisted. A CIMD document's URIs are the client's own
    word, so a non-loopback one must also be in server.oauth.redirect_uris; a loopback one matches
    with the port ignored (Claude Code picks an ephemeral port)."""
    from urllib.parse import urlsplit
    uris = json.loads(cl["redirect_uris_json"])
    if cl.get("auth_method") != "cimd":
        return ru in uris
    if _loopback(ru):
        r = urlsplit(ru)
        return any(_loopback(u) and urlsplit(u).path == r.path and (urlsplit(u).hostname or "").lower() == (r.hostname or "").lower() for u in uris)
    allowed = [str(u).rstrip("/") for u in (C.get(cfg, "server.oauth.redirect_uris", None) or DEFAULT_REDIRECTS)]
    return ru in uris and ru.rstrip("/") in allowed


def register(conn: sqlite3.Connection, cfg: dict, body) -> dict:
    """RFC 7591 with a short leash: only while the owner has armed Settings → Connections (so a
    public endpoint cannot be filled by strangers), every redirect_uri must be one of
    `server.oauth.redirect_uris` (the claude.ai callbacks by default), PKCE public clients need no
    secret, and the number of live registrations is capped."""
    if not isinstance(body, dict):
        raise OAuthError("invalid_client_metadata", "a JSON object is required")
    if not armed(conn):
        raise OAuthError("invalid_client_metadata", f"Factotum is not expecting a new connector: tap \"Allow a new connector\" in Settings → Connections ({ARM_MINUTES} minutes), then connect again")
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
    cl = client(conn, p.get("client_id"), cfg)
    if not cl:
        raise OAuthError("invalid_client", "unknown or revoked client_id", 400)
    uris = json.loads(cl["redirect_uris_json"])
    ru = str(p.get("redirect_uri") or (uris[0] if len(uris) == 1 else "") or "")
    if not ru or not _redirect_ok(cfg, cl, ru):
        raise OAuthError("invalid_request", "redirect_uri is not one this client may use", 400)
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


def _b64d(x: str) -> bytes:
    return base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))


def _fetch_certs(team: str) -> list[dict]:
    """The team's current signing keys (JWKS). Monkeypatched by tests."""
    r = httpx.get(f"https://{team}.cloudflareaccess.com/cdn-cgi/access/certs", timeout=10)
    r.raise_for_status()
    return list((r.json() or {}).get("keys") or [])


def _certs(team: str, kid: str) -> list[dict]:
    at, keys = _CERTS.get(team, (0.0, []))
    if not keys or time.time() - at > CERTS_TTL_S or (kid and not any(k.get("kid") == kid for k in keys) and time.time() - at > 60):
        keys = _fetch_certs(team)
        _CERTS[team] = (time.time(), keys)
    return keys


def verify_access_jwt(token: str, team: str, aud: str) -> dict:
    """Cloudflare Access's `Cf-Access-Jwt-Assertion`: RS256, signed by one of the team's published
    keys, for this application (aud), from this team (iss), within its validity. Returns the claims."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    try:
        h64, p64, s64 = str(token or "").split(".")
        head, claims, sig = json.loads(_b64d(h64)), json.loads(_b64d(p64)), _b64d(s64)
    except (ValueError, TypeError) as e:
        raise OAuthError("access_denied", "the Access assertion is not a JWT", 403) from e
    if not isinstance(head, dict) or not isinstance(claims, dict):
        raise OAuthError("access_denied", "the Access assertion is not a JWT", 403)
    if head.get("alg") != "RS256" or not head.get("kid"):
        raise OAuthError("access_denied", "the Access assertion is not RS256 with a key id", 403)
    try:
        keys = _certs(team, str(head.get("kid")))
    except (httpx.HTTPError, ValueError) as e:
        raise OAuthError("access_denied", f"the team's signing keys could not be fetched ({type(e).__name__}); try again", 403) from e
    jwk = next((k for k in keys if isinstance(k, dict) and k.get("kid") == head.get("kid") and k.get("kty") == "RSA"), None)
    if not jwk:
        raise OAuthError("access_denied", "the Access assertion was signed by a key the team does not publish", 403)
    try:
        key = rsa.RSAPublicNumbers(int.from_bytes(_b64d(jwk["e"]), "big"), int.from_bytes(_b64d(jwk["n"]), "big")).public_key()
        key.verify(sig, f"{h64}.{p64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError, KeyError) as e:
        raise OAuthError("access_denied", "the Access assertion's signature does not verify", 403) from e
    now = time.time()
    auds = claims.get("aud") if isinstance(claims.get("aud"), list) else [claims.get("aud")]
    if aud not in auds:
        raise OAuthError("access_denied", "the Access assertion is for another application (aud)", 403)
    if claims.get("iss") != f"https://{team}.cloudflareaccess.com":
        raise OAuthError("access_denied", "the Access assertion is from another team (iss)", 403)
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now or (claims.get("nbf") or 0) > now + 60:
        raise OAuthError("access_denied", "the Access assertion has expired; reload the page", 403)
    return claims


def consent_identity(cfg: dict, headers) -> str:
    """Who is on the consent page. `cloudflare_access` (default): the JWT Access attaches to every
    request it let through, verified against the team's certs for this application, and its email
    must be one of owner.emails (fail closed when that list is empty). A bare identity header is
    never enough: anything that can reach the origin can send one. `none`: no identity (a
    deployment where nothing but the owner can reach the port)."""
    gate = str(C.get(cfg, "server.oauth.consent_gate", "cloudflare_access") or "cloudflare_access")
    if gate == "none":
        return "local"
    team, aud = str(C.get(cfg, "server.oauth.access_team", "") or "").strip(), str(C.get(cfg, "server.oauth.access_aud", "") or "").strip()
    if not team or not aud:
        raise OAuthError("access_denied", "server.oauth.access_team and server.oauth.access_aud must name the Access team and the application's Audience tag (docs/SETUP.md §16)", 403)
    token = str(headers.get("cf-access-jwt-assertion") or "").strip()
    if not token:
        raise OAuthError("access_denied", "open this page through the Access-protected hostname; no Access assertion arrived with the request", 403)
    claims = verify_access_jwt(token, team, aud)
    email = str(claims.get("email") or "").strip().lower()
    allowed = {str(e).strip().lower() for e in (C.get(cfg, "owner.emails", []) or []) if e}
    if not allowed:
        raise OAuthError("access_denied", "owner.emails is empty in the config; the consent page needs to know who the owner is", 403)
    if not email or email not in allowed:
        raise OAuthError("access_denied", f"{email or 'that identity'} is not an owner address (owner.emails)", 403)
    return email


def arm(conn: sqlite3.Connection) -> str:
    until = _plus(ARM_MINUTES * 60)
    set_setting(conn, ARM_KEY, until)
    conn.commit()
    return until


def armed(conn: sqlite3.Connection) -> str | None:
    until = str(get_settings(conn).get(ARM_KEY) or "")
    return until if until and until > _now() else None


def granted_before(conn: sqlite3.Connection, client_id: str) -> bool:
    return conn.execute("SELECT 1 FROM oauth_tokens WHERE client_id = ? LIMIT 1", (client_id,)).fetchone() is not None


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
    cl = client(conn, form.get("client_id"), cfg)
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
        if conn.execute("UPDATE oauth_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL", (_now(), row["code_hash"])).rowcount != 1:
            raise OAuthError("invalid_grant", "the code was redeemed concurrently")        # the claim is atomic: two racing exchanges get one token
        return _issue(conn, cfg, cl["client_id"], row["scope"] or SCOPE)
    if gt == "refresh_token":
        row = one(conn, "SELECT * FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh'", (_h(str(form.get("refresh_token") or "")),))
        if not row or row["revoked_at"] or row["expires_at"] < _now() or row["client_id"] != cl["client_id"]:
            raise OAuthError("invalid_grant", "the refresh token is unknown, revoked, expired, or not this client's")
        if conn.execute("UPDATE oauth_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (_now(), row["id"])).rowcount != 1:   # rotation: the old one dies here, once
            raise OAuthError("invalid_grant", "the refresh token was used concurrently")
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
