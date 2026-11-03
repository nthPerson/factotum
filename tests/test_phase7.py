"""Wave 7: the OAuth server behind the claude.ai connector (SPEC.md §15): RFC 9728 / 8414 metadata,
RFC 7591 registration restricted to the claude.ai callbacks, PKCE S256, the Access-gated consent
page, opaque hashed tokens with refresh rotation, /mcp accepting either token kind, revocation."""
import base64
import hashlib
import json
import re
import secrets

import httpx
from urllib.parse import parse_qs, urlparse

import pytest

from factotum.db import one, rows, upsert_task

from conftest import task

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
OWNER = "rashe7414@sdsu.edu"
TEAM, AUD = "nthperson", "aud-tag-123"


def _oauth_cfg(cfg, **extra):
    cfg["server"]["public_url"] = "https://f.example"
    cfg["server"]["oauth"] = dict({"enabled": True, "consent_gate": "cloudflare_access", "access_team": TEAM, "access_aud": AUD}, **extra)
    return cfg


@pytest.fixture
def access(monkeypatch):
    """A Cloudflare Access team: one RSA signing key served as the team's certs, and a JWT maker."""
    import time
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from factotum import oauth
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nums = key.public_key().public_numbers()
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    jwk = {"kid": "k1", "kty": "RSA", "alg": "RS256", "use": "sig", "n": b64(nums.n.to_bytes(256, "big")), "e": b64(nums.e.to_bytes(3, "big"))}
    oauth._CERTS.clear()                                   # another test's key may sit in the cache under the same kid
    monkeypatch.setattr(oauth, "_fetch_certs", lambda team: [jwk] if team == TEAM else [])

    def jwt(email=OWNER, aud=AUD, team=TEAM, exp=3600, kid="k1", alg="RS256"):
        now = int(time.time())
        head = b64(json.dumps({"alg": alg, "kid": kid, "typ": "JWT"}).encode())
        body = b64(json.dumps({"aud": [aud], "email": email, "exp": now + exp, "iat": now - 5, "nbf": now - 5, "iss": f"https://{team}.cloudflareaccess.com", "sub": "u1", "type": "app"}).encode())
        sig = key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{b64(sig)}"
    return jwt


def _arm(client):
    r = client.post("/api/connectors/arm", json={})
    assert r.status_code == 200 and r.json()["armed_until"]


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _csrf(html: str) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "consent page has no csrf field"
    return m.group(1)


def _authorize(client, params, decision="allow", headers=None):
    r = client.get("/oauth/authorize", params=params, headers=headers or {})
    assert r.status_code == 200, r.text
    form = dict(params, csrf=_csrf(r.text), decision=decision)
    return client.post("/oauth/authorize", data=form, headers=headers or {}, follow_redirects=False)


def test_oauth_metadata_registration_consent_tokens_and_the_mcp_route(cfg, tmp_path, monkeypatch, access):
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    _oauth_cfg(cfg)
    H = {"Cf-Access-Jwt-Assertion": access()}
    monkeypatch.delenv("FACTOTUM_MCP_TOKEN", raising=False)
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    upsert_task(app.state.conn, task(id=7, name="OAuth one")); app.state.conn.commit()
    client = TestClient(app)
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude", "version": "0"}}}

    # discovery: a bare request gets 401 + where the metadata is; both well-known paths answer
    r = client.post("/mcp", json=init)
    assert r.status_code == 401 and 'resource_metadata="https://f.example/.well-known/oauth-protected-resource"' in r.headers["www-authenticate"]
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        prm = client.get(path).json()
        assert prm["resource"] == "https://f.example/mcp" and prm["authorization_servers"] == ["https://f.example"] and "header" in prm["bearer_methods_supported"]
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["issuer"] == "https://f.example" and asm["authorization_endpoint"] == "https://f.example/oauth/authorize" and asm["token_endpoint"] == "https://f.example/oauth/token"
    assert asm["registration_endpoint"] == "https://f.example/oauth/register" and asm["code_challenge_methods_supported"] == ["S256"]
    assert set(asm["grant_types_supported"]) == {"authorization_code", "refresh_token"} and "none" in asm["token_endpoint_auth_methods_supported"]

    # registration: only while the owner has armed Settings, only the claude.ai callbacks, a public client with PKCE
    assert client.post("/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": "Early"}).status_code == 400
    _arm(client)
    bad = client.post("/oauth/register", json={"redirect_uris": ["https://evil.example/cb"], "client_name": "Evil"})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_redirect_uri"
    assert client.post("/oauth/register", json={"client_name": "No URIs"}).status_code == 400
    reg = client.post("/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": "Claude", "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"]})
    assert reg.status_code == 201, reg.text
    cid = reg.json()["client_id"]
    assert reg.json()["redirect_uris"] == [CALLBACK] and reg.json()["token_endpoint_auth_method"] == "none" and "client_secret" not in reg.json()

    # consent: a verified Access assertion for an owner address, and Factotum must be expecting a new connector
    verifier, challenge = _pkce()
    params = {"response_type": "code", "client_id": cid, "redirect_uri": CALLBACK, "state": "xyz", "code_challenge": challenge, "code_challenge_method": "S256",
              "scope": "factotum", "resource": "https://f.example/mcp"}
    assert client.get("/oauth/authorize", params=params).status_code == 403                                                   # no assertion at all
    assert client.get("/oauth/authorize", params=params, headers={"Cf-Access-Authenticated-User-Email": OWNER}).status_code == 403   # a bare header is not identity
    b64 = lambda x: base64.urlsafe_b64encode(x.encode()).rstrip(b"=").decode()
    for bad in (access(email="x@evil.example"), access(aud="other-app"), access(team="elsewhere"), access(exp=-10), access(alg="none").rsplit(".", 1)[0] + ".", "garbage",
                f"{b64('[1]')}.{b64('{}')}.{b64('x')}", f"{b64('{\"alg\":\"RS256\"}')}.{b64('[]')}.{b64('x')}"):
        assert client.get("/oauth/authorize", params=params, headers={"Cf-Access-Jwt-Assertion": bad}).status_code == 403, bad[:20]
    from factotum import oauth as _oauth
    from factotum.db import set_setting
    good = _oauth._fetch_certs
    monkeypatch.setattr(_oauth, "_fetch_certs", lambda team: (_ for _ in ()).throw(httpx.ConnectError("certs down")))
    _oauth._CERTS.clear()
    assert client.get("/oauth/authorize", params=params, headers=H).status_code == 403                                      # certs unreachable: refused, not a 500
    monkeypatch.setattr(_oauth, "_fetch_certs", good)
    set_setting(app.state.conn, _oauth.ARM_KEY, "2000-01-01T00:00:00+00:00"); app.state.conn.commit()
    not_expected = client.get("/oauth/authorize", params=params, headers=H)
    assert not_expected.status_code == 403 and "Allow a new connector" in not_expected.text                                  # never granted, arming over
    _arm(client)
    assert client.get("/api/connectors").json()["armed_until"]
    page = client.get("/oauth/authorize", params=params, headers=H)
    assert page.status_code == 200 and "Claude" in page.text and "Factotum" in page.text and page.headers.get("x-frame-options") == "DENY"
    assert "https://claude.ai" in page.headers["content-security-policy"]                                                   # the Allow redirect must not be blocked by form-action
    assert cid[:8] in page.text and "Deny" in page.text and OWNER in page.text                                              # who is asking, as whom
    assert client.get("/oauth/authorize", params=dict(params, client_id="nope"), headers=H).status_code == 400
    assert client.get("/oauth/authorize", params=dict(params, redirect_uri="https://evil.example/cb"), headers=H).status_code == 400
    plain = client.get("/oauth/authorize", params=dict(params, code_challenge_method="plain"), headers=H, follow_redirects=False)
    assert plain.status_code == 302 and "error=invalid_request" in plain.headers["location"] and "state=xyz" in plain.headers["location"]
    nocsrf = client.post("/oauth/authorize", data=dict(params, decision="allow"), headers=H, follow_redirects=False)
    assert nocsrf.status_code == 400
    deny = _authorize(client, params, decision="deny", headers=H)
    assert deny.status_code == 302 and "error=access_denied" in deny.headers["location"]
    r = _authorize(client, params, headers=H)
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    assert loc.scheme == "https" and loc.netloc == "claude.ai" and loc.path == "/api/mcp/auth_callback"
    q = parse_qs(loc.query)
    assert q["state"] == ["xyz"] and q["code"]
    code = q["code"][0]

    # the token endpoint: PKCE verified, a code is single use
    bad = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "client_id": cid, "code_verifier": "wrong" * 9})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
    tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "client_id": cid, "code_verifier": verifier, "resource": "https://f.example/mcp"})
    assert tok.status_code == 200, tok.text
    t = tok.json()
    assert t["token_type"] == "Bearer" and t["access_token"] and t["refresh_token"] and t["expires_in"] > 0 and tok.headers["cache-control"] == "no-store"
    again = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "client_id": cid, "code_verifier": verifier})
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert client.post("/oauth/token", data={"grant_type": "password", "client_id": cid}).json()["error"] == "unsupported_grant_type"
    assert not rows(app.state.conn, "SELECT 1 AS x FROM oauth_tokens WHERE token_hash = ?", (t["access_token"],))        # stored hashed, never raw

    # the MCP route takes the OAuth token like the static one, and a bad token says why
    h = {"Authorization": f"Bearer {t['access_token']}"}
    r = client.post("/mcp", json=init, headers=h)
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "factotum"
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "tasks_query", "arguments": {"q": "oauth"}}}, headers=h)
    assert json.loads(r.json()["result"]["content"][0]["text"])["tasks"][0]["id"] == 7
    r = client.post("/mcp", json=init, headers={"Authorization": "Bearer fct_nope"})
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
    monkeypatch.setenv("FACTOTUM_MCP_TOKEN", "s3cret")
    assert client.post("/mcp", json=init, headers={"Authorization": "Bearer s3cret"}).status_code == 200                   # the static token still works beside OAuth

    # refresh rotates; the old refresh token dies with it
    rf = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": cid})
    assert rf.status_code == 200 and rf.json()["access_token"] != t["access_token"] and rf.json()["refresh_token"] != t["refresh_token"]
    assert client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": t["refresh_token"], "client_id": cid}).json()["error"] == "invalid_grant"
    h2 = {"Authorization": f"Bearer {rf.json()['access_token']}"}
    assert client.post("/mcp", json=init, headers=h2).status_code == 200

    # Settings → Connections: the client, when it last spoke; Revoke kills every token
    cons = client.get("/api/connectors").json()["connectors"]
    assert len(cons) == 1 and cons[0]["client_id"] == cid and cons[0]["name"] == "Claude" and cons[0]["last_used_at"] and cons[0]["tokens"] >= 1
    assert client.post(f"/api/connectors/{cid}/revoke", json={}).status_code == 200
    assert client.post("/mcp", json=init, headers=h2).status_code == 401
    assert client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": rf.json()["refresh_token"], "client_id": cid}).json()["error"] == "invalid_client"
    assert client.get("/api/connectors").json()["connectors"][0]["revoked_at"]
    assert client.get("/oauth/authorize", params=params, headers=H).status_code == 400                                       # a revoked client cannot ask again


def test_oauth_is_off_by_default_and_registration_is_capped(cfg, tmp_path, monkeypatch, access):
    from fastapi.testclient import TestClient
    from factotum import oauth
    from factotum.api import create_app
    monkeypatch.delenv("FACTOTUM_MCP_TOKEN", raising=False)
    cfg["server"]["public_url"] = "https://f.example"
    app = create_app(cfg, db_path=str(tmp_path / "off.db"))
    client = TestClient(app)
    assert client.get("/.well-known/oauth-protected-resource").status_code == 404 and client.get("/.well-known/oauth-authorization-server").status_code == 404
    assert client.post("/oauth/register", json={"redirect_uris": [CALLBACK]}).status_code == 404
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 404                     # neither a static token nor OAuth: no route
    cfg["server"]["oauth"] = {"enabled": True}; cfg["server"]["public_url"] = ""
    assert client.get("/.well-known/oauth-authorization-server").status_code == 404                                        # no public URL: nothing to advertise
    _oauth_cfg(cfg, consent_gate="none")
    monkeypatch.setattr(oauth, "MAX_CLIENTS", 2)
    app2 = create_app(cfg, db_path=str(tmp_path / "on.db"))
    c2 = TestClient(app2)
    _arm(c2)
    ids = [c2.post("/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": f"c{i}"}) for i in range(3)]
    assert [r.status_code for r in ids] == [201, 201, 400] and ids[2].json()["error"] == "invalid_client_metadata"
    # consent_gate none: the page renders without an Access assertion (a loopback-only deployment) while armed
    _, challenge = _pkce()
    params = {"response_type": "code", "client_id": ids[0].json()["client_id"], "redirect_uri": CALLBACK, "state": "s", "code_challenge": challenge, "code_challenge_method": "S256"}
    assert c2.get("/oauth/authorize", params=params).status_code == 200
    # the Access gate without a team and audience configured refuses rather than falling back to a header
    cfg["server"]["oauth"] = {"enabled": True, "consent_gate": "cloudflare_access"}
    app3 = create_app(cfg, db_path=str(tmp_path / "half.db"))
    c3 = TestClient(app3)
    _arm(c3)
    cid3 = c3.post("/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": "c"}).json()["client_id"]
    r = c3.get("/oauth/authorize", params=dict(params, client_id=cid3), headers={"Cf-Access-Authenticated-User-Email": OWNER})
    assert r.status_code == 403 and "access_team" in r.text
    # owner.emails empty: fail closed, whatever Access let through
    _oauth_cfg(cfg); cfg["owner"]["emails"] = []
    app4 = create_app(cfg, db_path=str(tmp_path / "noowner.db"))
    c4 = TestClient(app4)
    _arm(c4)
    cid4 = c4.post("/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": "c"}).json()["client_id"]
    r = c4.get("/oauth/authorize", params=dict(params, client_id=cid4), headers={"Cf-Access-Jwt-Assertion": access()})
    assert r.status_code == 403 and "owner.emails" in r.text


def test_oauth_tokens_expire_and_a_client_only_redeems_its_own_code(cfg, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from fastapi.testclient import TestClient
    from factotum import oauth
    from factotum.api import create_app
    _oauth_cfg(cfg, consent_gate="none", access_token_hours=1)
    app = create_app(cfg, db_path=str(tmp_path / "exp.db"))
    client = TestClient(app)
    _arm(client)
    a = client.post("/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": "A"}).json()["client_id"]
    b = client.post("/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": "B"}).json()["client_id"]
    verifier, challenge = _pkce()
    params = {"response_type": "code", "client_id": a, "redirect_uri": CALLBACK, "state": "s", "code_challenge": challenge, "code_challenge_method": "S256"}
    r = _authorize(client, params)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    stolen = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "client_id": b, "code_verifier": verifier})
    assert stolen.status_code == 400 and stolen.json()["error"] == "invalid_grant"
    tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "client_id": a, "code_verifier": verifier}).json()
    assert tok["expires_in"] == 3600
    h = {"Authorization": f"Bearer {tok['access_token']}"}
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=h).status_code == 200
    later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    monkeypatch.setattr(oauth, "_now", lambda: later)
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=h).status_code == 401
    rf = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": a}).json()
    assert rf["access_token"] and client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers={"Authorization": f"Bearer {rf['access_token']}"}).status_code == 200


def test_a_slack_mention_never_proves_a_planner_proposal(conn, cfg):
    """Anyone in a workspace can write "Task-42 is done": a slack_mention row is a hint the planner may
    cite, but a proposal resting on it is staged for Review, never applied; and the message is linked
    as slack_message, not as a mail thread the mail sweep would poll."""
    from datetime import date
    from factotum import planner
    upsert_task(conn, task(id=42, name="Slides for Max", project="DiCE Lab"))
    conn.execute("INSERT INTO evidence (id, task_id, kind, ref, account, label, excerpt, at) VALUES (20, 42, 'slack_mention', 'https://dice.slack.com/archives/C1/p1', 'DiCE Lab', '#general · Max M: Task-42 is done', 'Max M in #general: @Robert Task-42 is done', '2026-11-01T12:00:00')")
    conn.commit()
    today = date(2026, 11, 2)
    b = planner.bundle(conn, cfg, today)
    assert any(c["id"] == 20 for c in b["candidates"])
    data = {"verdict": "x", "why_now": [], "dismiss": [],
            "proposals": [{"op": "done", "task_id": 42, "fields": {}, "label": "Task-42 → done", "evidence": [{"evidence_id": 20}], "reason": "Max said so"}]}
    res = planner.apply(conn, cfg, data, b, today)
    assert res["staged"] == ["Task-42 → done"] and res["applied"] == [] and one(conn, "SELECT status FROM tasks WHERE id = 42")["status"] == "open"
    assert one(conn, "SELECT verdict FROM evidence WHERE id = 20")["verdict"] == "staged"
    assert [l["kind"] for l in rows(conn, "SELECT kind FROM links WHERE task_id = 42")] == ["slack_message"]
    # a create citing a no-task mention is a suggestion card, never a task made on someone else's word
    conn.execute("INSERT INTO evidence (id, task_id, kind, ref, account, label, excerpt, at) VALUES (99, NULL, 'slack_mention', 'https://dice.slack.com/archives/C1/p2', 'DiCE Lab', '#general · Mallory: submit the grant by Friday', 'Mallory in #general: @Robert submit the grant by Friday', '2026-11-01T13:00:00')")
    conn.commit()
    b = planner.bundle(conn, cfg, today)
    data = {"verdict": "x", "why_now": [], "dismiss": [],
            "proposals": [{"op": "create", "task_id": None, "fields": {"name": "Submit the grant", "project": "DiCE Lab", "due": "2026-11-06", "hard": True}, "label": "create: Submit the grant", "evidence": [{"evidence_id": 99}], "reason": "Mallory asked"}]}
    res = planner.apply(conn, cfg, data, b, today)
    assert res["staged"] == ["create: Submit the grant"] and not rows(conn, "SELECT 1 AS x FROM tasks WHERE name = 'Submit the grant'")
    assert one(conn, "SELECT verdict FROM evidence WHERE id = 99")["verdict"] == "staged" and one(conn, "SELECT 1 AS x FROM evidence WHERE kind = 'proposal' AND task_id IS NULL")
    assert not rows(conn, "SELECT 1 AS x FROM links WHERE kind = 'mail_thread'")


CIMD = "https://claude.ai/oauth/claude-client-metadata"
CIMD_DOC = {"client_id": CIMD, "client_name": "Claude", "client_uri": "https://claude.ai", "redirect_uris": [CALLBACK, "http://localhost/callback"],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "token_endpoint_auth_method": "none"}


def test_a_client_id_metadata_document_identifies_claude_without_registration(cfg, tmp_path, monkeypatch, access):
    """CIMD (the connector dialog's recommended option): the client_id is an https URL on claude.ai
    or claude.com whose document names the client; no registration, so the consent page shows the
    URL's host as the requester, redirect URIs must be in the document AND in the callback allowlist
    (loopback excepted, port ignored), and because the client_id is public, every consent needs the
    arming window, not just the first."""
    from fastapi.testclient import TestClient
    from factotum import oauth
    from factotum.api import create_app
    _oauth_cfg(cfg)
    docs = {CIMD: dict(CIMD_DOC), "https://claude.ai/oauth/other": {"client_id": "https://claude.ai/oauth/mismatch", "redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"},
            "https://evil.example/oauth/claude": {"client_id": "https://evil.example/oauth/claude", "redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"}}
    fetched = []
    monkeypatch.setattr(oauth, "_fetch_cimd", lambda url: (fetched.append(url), docs[url])[1])
    oauth._CIMD.clear()
    app = create_app(cfg, db_path=str(tmp_path / "cimd.db"))
    client = TestClient(app)
    H = {"Cf-Access-Jwt-Assertion": access()}
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["client_id_metadata_document_supported"] is True and "none" in asm["token_endpoint_auth_methods_supported"]
    verifier, challenge = _pkce()
    params = {"response_type": "code", "client_id": CIMD, "redirect_uri": CALLBACK, "state": "s1", "code_challenge": challenge, "code_challenge_method": "S256", "resource": "https://f.example/mcp"}
    # never granted and not armed: refused before anything is fetched or stored
    r = client.get("/oauth/authorize", params=params, headers=H)
    assert r.status_code == 403 and "Allow a new connector" in r.text and not rows(app.state.conn, "SELECT 1 AS x FROM oauth_clients")
    _arm(client)
    page = client.get("/oauth/authorize", params=params, headers=H)
    assert page.status_code == 200 and "claude.ai" in page.text and fetched == [CIMD]
    assert not rows(app.state.conn, "SELECT 1 AS x FROM oauth_clients")                                   # viewing the page stores nothing; Allow does
    # the document is the authority on redirect URIs, and the server's allowlist still applies
    assert client.get("/oauth/authorize", params=dict(params, redirect_uri="https://claude.ai/other"), headers=H).status_code == 400
    docs[CIMD]["redirect_uris"].append("https://evil.example/cb"); oauth._CIMD.clear()
    assert client.get("/oauth/authorize", params=dict(params, redirect_uri="https://evil.example/cb"), headers=H).status_code == 400
    loop = client.get("/oauth/authorize", params=dict(params, redirect_uri="http://localhost:3118/callback"), headers=H)
    assert loop.status_code == 200                                                                         # loopback: port ignored, allowlist not needed
    # a document that does not name itself, or a host outside server.oauth.cimd_hosts, is no client
    assert client.get("/oauth/authorize", params=dict(params, client_id="https://claude.ai/oauth/other"), headers=H).status_code == 400
    assert client.get("/oauth/authorize", params=dict(params, client_id="https://evil.example/oauth/claude"), headers=H).status_code == 400
    assert client.get("/oauth/authorize", params=dict(params, client_id="http://claude.ai/oauth/x"), headers=H).status_code == 400
    # consent → code → tokens, the client_id URL as the public client
    r = _authorize(client, params, headers=H)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    assert one(app.state.conn, "SELECT auth_method, name FROM oauth_clients WHERE client_id = ?", (CIMD,)) == {"auth_method": "cimd", "name": "Claude"}
    tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK, "client_id": CIMD, "code_verifier": verifier})
    assert tok.status_code == 200, tok.text
    h = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=h).status_code == 200
    assert client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok.json()["refresh_token"], "client_id": CIMD}).status_code == 200
    cons = client.get("/api/connectors").json()["connectors"]
    assert cons[0]["client_id"] == CIMD and cons[0]["auth_method"] == "cimd"
    # a public client_id can be put in anyone's link: once the window closes, a new consent needs arming again
    from factotum.db import set_setting
    set_setting(app.state.conn, oauth.ARM_KEY, "2000-01-01T00:00:00+00:00"); app.state.conn.commit()
    assert client.get("/oauth/authorize", params=dict(params, state="s2"), headers=H).status_code == 403
    # revoke kills the tokens AND a code issued just before it; the token endpoint never revives a revoked client
    _arm(client)
    v2, c2 = _pkce()
    r = _authorize(client, dict(params, state="s2b", code_challenge=c2), headers=H)
    pending = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    assert client.post(f"/api/connectors/{CIMD}/revoke", json={}).status_code == 200
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=h).status_code == 401
    dead = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": pending, "redirect_uri": CALLBACK, "client_id": CIMD, "code_verifier": v2})
    assert dead.status_code in (400, 401) and dead.json()["error"] in ("invalid_client", "invalid_grant")
    assert client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok.json()["refresh_token"], "client_id": CIMD}).json()["error"] in ("invalid_client", "invalid_grant")
    _arm(client)
    assert client.get("/oauth/authorize", params=dict(params, state="s3"), headers=H).status_code == 200
    assert one(app.state.conn, "SELECT revoked_at FROM oauth_clients WHERE client_id = ?", (CIMD,))["revoked_at"]      # viewing revives nothing
    assert _authorize(client, dict(params, state="s3"), headers=H).status_code == 302
    assert one(app.state.conn, "SELECT revoked_at FROM oauth_clients WHERE client_id = ?", (CIMD,))["revoked_at"] is None   # Allow does
