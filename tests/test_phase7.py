"""Wave 7: the OAuth server behind the claude.ai connector (SPEC.md §15): RFC 9728 / 8414 metadata,
RFC 7591 registration restricted to the claude.ai callbacks, PKCE S256, the Access-gated consent
page, opaque hashed tokens with refresh rotation, /mcp accepting either token kind, revocation."""
import base64
import hashlib
import json
import re
import secrets
from urllib.parse import parse_qs, urlparse

import pytest

from factotum.db import one, rows, upsert_task

from conftest import task

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
OWNER = "rashe7414@sdsu.edu"


def _oauth_cfg(cfg, **extra):
    cfg["server"]["public_url"] = "https://f.example"
    cfg["server"]["oauth"] = dict({"enabled": True, "consent_gate": "cloudflare_access"}, **extra)
    return cfg


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _csrf(html: str) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "consent page has no csrf field"
    return m.group(1)


def _authorize(client, params, decision="allow", email=OWNER):
    r = client.get("/oauth/authorize", params=params, headers={"Cf-Access-Authenticated-User-Email": email})
    assert r.status_code == 200, r.text
    form = dict(params, csrf=_csrf(r.text), decision=decision)
    return client.post("/oauth/authorize", data=form, headers={"Cf-Access-Authenticated-User-Email": email}, follow_redirects=False)


def test_oauth_metadata_registration_consent_tokens_and_the_mcp_route(cfg, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from factotum.api import create_app
    _oauth_cfg(cfg)
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

    # registration: only the claude.ai callbacks, a public client with PKCE
    bad = client.post("/oauth/register", json={"redirect_uris": ["https://evil.example/cb"], "client_name": "Evil"})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_redirect_uri"
    assert client.post("/oauth/register", json={"client_name": "No URIs"}).status_code == 400
    reg = client.post("/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": "Claude", "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"]})
    assert reg.status_code == 201, reg.text
    cid = reg.json()["client_id"]
    assert reg.json()["redirect_uris"] == [CALLBACK] and reg.json()["token_endpoint_auth_method"] == "none" and "client_secret" not in reg.json()

    # consent: Access identity required, the owner's email only; bad client / redirect never redirect
    verifier, challenge = _pkce()
    params = {"response_type": "code", "client_id": cid, "redirect_uri": CALLBACK, "state": "xyz", "code_challenge": challenge, "code_challenge_method": "S256",
              "scope": "factotum", "resource": "https://f.example/mcp"}
    assert client.get("/oauth/authorize", params=params).status_code == 403                                                   # no Access header
    assert client.get("/oauth/authorize", params=params, headers={"Cf-Access-Authenticated-User-Email": "x@evil.example"}).status_code == 403
    page = client.get("/oauth/authorize", params=params, headers={"Cf-Access-Authenticated-User-Email": OWNER})
    assert page.status_code == 200 and "Claude" in page.text and "Factotum" in page.text and page.headers.get("x-frame-options") == "DENY"
    assert client.get("/oauth/authorize", params=dict(params, client_id="nope"), headers={"Cf-Access-Authenticated-User-Email": OWNER}).status_code == 400
    assert client.get("/oauth/authorize", params=dict(params, redirect_uri="https://evil.example/cb"), headers={"Cf-Access-Authenticated-User-Email": OWNER}).status_code == 400
    plain = client.get("/oauth/authorize", params=dict(params, code_challenge_method="plain"), headers={"Cf-Access-Authenticated-User-Email": OWNER}, follow_redirects=False)
    assert plain.status_code == 302 and "error=invalid_request" in plain.headers["location"] and "state=xyz" in plain.headers["location"]
    nocsrf = client.post("/oauth/authorize", data=dict(params, decision="allow"), headers={"Cf-Access-Authenticated-User-Email": OWNER}, follow_redirects=False)
    assert nocsrf.status_code == 400
    deny = _authorize(client, params, decision="deny")
    assert deny.status_code == 302 and "error=access_denied" in deny.headers["location"]
    r = _authorize(client, params)
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
    assert client.get("/oauth/authorize", params=params, headers={"Cf-Access-Authenticated-User-Email": OWNER}).status_code == 400   # a revoked client cannot ask again


def test_oauth_is_off_by_default_and_registration_is_capped(cfg, tmp_path, monkeypatch):
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
    _oauth_cfg(cfg, consent_gate="none")
    monkeypatch.setattr(oauth, "MAX_CLIENTS", 2)
    app2 = create_app(cfg, db_path=str(tmp_path / "on.db"))
    c2 = TestClient(app2)
    ids = [c2.post("/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": f"c{i}"}) for i in range(3)]
    assert [r.status_code for r in ids] == [201, 201, 400] and ids[2].json()["error"] == "invalid_client_metadata"
    # consent_gate none: the page renders without an Access header (a loopback-only deployment)
    _, challenge = _pkce()
    params = {"response_type": "code", "client_id": ids[0].json()["client_id"], "redirect_uri": CALLBACK, "state": "s", "code_challenge": challenge, "code_challenge_method": "S256"}
    assert c2.get("/oauth/authorize", params=params).status_code == 200


def test_oauth_tokens_expire_and_a_client_only_redeems_its_own_code(cfg, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from fastapi.testclient import TestClient
    from factotum import oauth
    from factotum.api import create_app
    _oauth_cfg(cfg, consent_gate="none", access_token_hours=1)
    app = create_app(cfg, db_path=str(tmp_path / "exp.db"))
    client = TestClient(app)
    a = client.post("/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": "A"}).json()["client_id"]
    b = client.post("/oauth/register", json={"redirect_uris": [CALLBACK], "client_name": "B"}).json()["client_id"]
    verifier, challenge = _pkce()
    params = {"response_type": "code", "client_id": a, "redirect_uri": CALLBACK, "state": "s", "code_challenge": challenge, "code_challenge_method": "S256"}
    r = _authorize(client, params, email=None) if False else client.post("/oauth/authorize", data=dict(params, csrf=_csrf(client.get("/oauth/authorize", params=params).text), decision="allow"), follow_redirects=False)
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
