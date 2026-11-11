import json, os, re, stat

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from factotum import app_push as AP
from factotum import webpush as W


# --- Task 1: encryption and VAPID

RFC = dict(  # RFC 8291 Appendix A, checked against cryptography 50 on 2026-11-10
    plaintext="V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24",
    as_private="yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    as_public="BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
    ua_private="q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94",
    ua_public="BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    salt="DGv6ra1nlYgDCS1FRnbzlw",
    auth="BTBZMqHH6r4Tts7J_aSIgg",
    body="DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
)
_priv = lambda s: ec.derive_private_key(int.from_bytes(W.unb64u(s), "big"), ec.SECP256R1())   # noqa: E731


def test_encrypt_matches_rfc_8291_appendix_a():
    sp = _priv(RFC["as_private"])
    assert W.b64u(W.public_bytes(sp)) == RFC["as_public"]
    body = W.encrypt(W.unb64u(RFC["plaintext"]), W.unb64u(RFC["ua_public"]), W.unb64u(RFC["auth"]),
                     salt=W.unb64u(RFC["salt"]), sender_private=sp)
    assert W.b64u(body) == RFC["body"] and len(body) == 144
    assert W.decrypt(body, _priv(RFC["ua_private"]), W.unb64u(RFC["auth"])) == W.unb64u(RFC["plaintext"])


def test_encrypt_round_trips_with_fresh_keys_and_salt():
    ua = ec.generate_private_key(ec.SECP256R1()); auth = os.urandom(16)
    a = W.encrypt(b'{"x": 1}', W.public_bytes(ua), auth); b = W.encrypt(b'{"x": 1}', W.public_bytes(ua), auth)
    assert a != b                                              # a new salt and sender key each time
    assert W.decrypt(a, ua, auth) == b'{"x": 1}' and W.decrypt(b, ua, auth) == b'{"x": 1}'


def test_encrypt_refuses_a_record_that_does_not_fit():
    ua = ec.generate_private_key(ec.SECP256R1())
    assert len(W.encrypt(b"x" * W.MAX_PLAINTEXT, W.public_bytes(ua), os.urandom(16))) == 4096
    with pytest.raises(ValueError):
        W.encrypt(b"x" * (W.MAX_PLAINTEXT + 1), W.public_bytes(ua), os.urandom(16))      # one byte over: the body would be 4097


def test_vapid_header_is_a_signed_es256_jwt_for_the_push_service():
    key = ec.generate_private_key(ec.SECP256R1())
    h = W.vapid_header("https://push.example:8443/send/abc?x=1", key, "https://tasks.example.org", now=1_800_000_000)
    m = re.fullmatch(r"vapid t=([\w-]+)\.([\w-]+)\.([\w-]+), k=([\w-]+)", h)
    assert m and W.unb64u(m.group(4)) == W.public_bytes(key)
    assert json.loads(W.unb64u(m.group(1))) == {"typ": "JWT", "alg": "ES256"}
    claims = json.loads(W.unb64u(m.group(2)))
    assert claims == {"aud": "https://push.example:8443", "exp": 1_800_000_000 + 12 * 3600, "sub": "https://tasks.example.org"}
    sig = W.unb64u(m.group(3)); assert len(sig) == 64
    der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    key.public_key().verify(der, f"{m.group(1)}.{m.group(2)}".encode(), ec.ECDSA(hashes.SHA256()))   # raises if wrong
    assert "sub" not in json.loads(W.unb64u(W.vapid_header("https://p.example/x", key, None).split("t=")[1].split(".")[1]))


def test_send_posts_one_encrypted_record_with_the_push_headers():
    seen = []
    def handler(req):
        seen.append(req); return httpx.Response(201)
    ua = ec.generate_private_key(ec.SECP256R1()); auth = os.urandom(16); key = ec.generate_private_key(ec.SECP256R1())
    r = W.send("https://push.example/send/abc", W.b64u(W.public_bytes(ua)), W.b64u(auth), b'{"hi": 1}', key,
               sub=None, ttl=600, urgency="high", topic="task-12", transport=httpx.MockTransport(handler))
    assert r.status_code == 201 and len(seen) == 1
    q = seen[0]
    assert q.method == "POST" and str(q.url) == "https://push.example/send/abc"
    assert q.headers["Content-Encoding"] == "aes128gcm" and q.headers["Content-Type"] == "application/octet-stream"
    assert q.headers["TTL"] == "600" and q.headers["Urgency"] == "high" and q.headers["Topic"] == "task-12"
    assert q.headers["Authorization"].startswith("vapid t=")
    assert W.decrypt(q.content, ua, auth) == b'{"hi": 1}'
    W.send("https://push.example/x", W.b64u(W.public_bytes(ua)), W.b64u(auth), b"{}", key, sub=None, ttl=1, urgency="normal",
           topic="not a valid topic!", transport=httpx.MockTransport(handler))
    assert "Topic" not in seen[1].headers                        # a topic outside [A-Za-z0-9_-]{1,32} is left out


# --- Task 2: the key pair and the devices table

def _device(conn, endpoint="https://push.example/d1", origin="https://f.example", label="Phone"):
    ua = ec.generate_private_key(ec.SECP256R1()); auth = os.urandom(16)
    did = AP.subscribe(conn, endpoint, W.b64u(W.public_bytes(ua)), W.b64u(auth), label, origin); conn.commit()
    return did, ua, auth


def test_the_key_pair_is_made_once_beside_the_database_and_kept_private(conn, tmp_path):
    p = AP.key_path(conn)
    assert p == tmp_path / "push-keys.json" and not p.exists()
    with pytest.raises(AP.KeyFileError, match="missing"):
        AP.keys(conn)
    k1 = AP.keys(conn, create=True)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert AP.keys(conn).public == k1.public == W.b64u(W.public_bytes(k1.private)) and len(W.unb64u(k1.public)) == 65
    assert set(json.loads(p.read_text())) == {"private", "public", "created_at"}


def test_a_missing_key_file_is_made_again_and_the_old_devices_go(conn):
    AP.keys(conn, create=True); _device(conn); assert AP.device_count(conn) == 1
    AP.key_path(conn).unlink()
    with pytest.raises(AP.KeyFileError, match="missing"):
        AP.keys(conn)                                            # the sender never makes a pair
    assert AP.device_count(conn) == 1
    AP.keys(conn, create=True)                                   # the page asking for the public key does
    assert AP.device_count(conn) == 0                            # each was bound to the old key and can never work again


def test_an_unreadable_key_file_is_never_overwritten(conn):
    AP.key_path(conn).write_text("not json")
    for create in (False, True):
        with pytest.raises(AP.KeyFileError, match="unreadable"):
            AP.keys(conn, create=create)
    assert AP.key_path(conn).read_text() == "not json"


def test_subscribe_validates_and_upserts_by_endpoint(conn):
    did, _, _ = _device(conn, label="  Chrome on Windows  ")
    row = AP.devices(conn)[0]
    assert row["id"] == did and row["label"] == "Chrome on Windows" and row["origin"] == "https://f.example" and row["created_at"]
    conn.execute("UPDATE push_subscriptions SET last_failure_at = 'x', last_error = 'y'")
    again, _, _ = _device(conn, label="")                        # same endpoint, new keys
    row2 = AP.devices(conn)[0]
    assert again == did and AP.device_count(conn) == 1 and row2["label"] == "A device"
    assert row2["created_at"] == row["created_at"] and row2["last_failure_at"] is None and row2["last_error"] is None
    good = dict(endpoint="https://push.example/d2", p256dh=row2["p256dh"], auth=row2["auth"], label="x", origin="http://127.0.0.1:8751")
    for change, msg in [({"endpoint": "http://push.example/d2"}, "not a push service address"),
                        ({"endpoint": "https://10.0.0.5/d2"}, "not a push service address"),
                        ({"endpoint": "https://localhost/d2"}, "not a push service address"),
                        ({"p256dh": W.b64u(b"\x04" + b"\x01" * 64)}, "the device key is not a P-256 public key"),
                        ({"auth": W.b64u(b"short")}, "the device secret must be 16 bytes"),
                        ({"origin": "https://f.example/settings"}, "origin must be scheme://host\\[:port\\]")]:
        with pytest.raises(ValueError, match=msg):
            AP.subscribe(conn, **{**good, **change})
    AP.subscribe(conn, **good); assert AP.device_count(conn) == 2   # http://127.0.0.1 is a fine origin (the dev instance)
    assert AP.unsubscribe(conn, endpoint="https://push.example/d2") == 1 and AP.unsubscribe(conn, device_id=did) == 1
    assert AP.device_count(conn) == 0 and AP.unsubscribe(conn, device_id=999) == 0


# --- Task 3: the payload and sending

NOTICE = {"title": "Call Reza", "body": "before the meeting · due Nov 5", "path": "/t/12", "tag": "task-12",
          "actions": [{"action": "done", "title": "Done", "path": "/r/tok?a=done"}, {"action": "snooze", "title": "Snooze 1 h", "path": "/r/tok?a=snooze"}],
          "urgency": "high", "ttl": 86400, "data": {"task_id": 12}}


def _service(answers):
    """A fake push service: answers[endpoint] is a status code or an exception; records (endpoint, request)."""
    got = []
    def handler(req):
        got.append((str(req.url), req))
        a = answers.get(str(req.url), 201)
        if isinstance(a, Exception):
            raise a
        return httpx.Response(a, text="nope" if a >= 400 else "")
    return got, httpx.MockTransport(handler)


def test_the_payload_is_the_declarative_format_on_the_devices_origin():
    p = json.loads(AP.payload(NOTICE, "https://f.example"))
    assert p == {"web_push": 8030, "notification": {
        "title": "Call Reza", "body": "before the meeting · due Nov 5", "navigate": "https://f.example/t/12", "tag": "task-12", "renotify": True,
        "actions": [{"action": "done", "title": "Done", "navigate": "https://f.example/r/tok?a=done"},
                    {"action": "snooze", "title": "Snooze 1 h", "navigate": "https://f.example/r/tok?a=snooze"}],
        "data": {"task_id": 12}}}
    bare = json.loads(AP.payload({**NOTICE, "actions": [], "data": None, "path": "/review", "tag": "review"}, "http://127.0.0.1:8751"))
    assert "actions" not in bare["notification"] and "data" not in bare["notification"] and "mutable" not in bare
    assert bare["notification"]["navigate"] == "http://127.0.0.1:8751/review"


def test_a_payload_never_exceeds_the_limit_and_keeps_its_links():
    big = {**NOTICE, "title": "T" * 5000, "body": "é" * 5000}
    raw = AP.payload(big, "https://f.example")
    n = json.loads(raw)["notification"]
    assert len(raw) <= W.MAX_PLAINTEXT and n["navigate"] == "https://f.example/t/12" and n["tag"] == "task-12"
    assert n["title"].startswith("TTT") and n["body"].endswith("…") and n["actions"][0]["navigate"].endswith("/r/tok?a=done")
    huge = {**NOTICE, "title": "T" * 9000, "body": ""}
    assert len(AP.payload(huge, "https://f.example")) <= W.MAX_PLAINTEXT


def test_send_all_reaches_every_device_with_its_own_links(conn, cfg):
    AP.keys(conn, create=True)
    _, ua1, a1 = _device(conn, "https://push.example/d1", "https://f.example")
    _, ua2, a2 = _device(conn, "https://push.example/d2", "http://127.0.0.1:8751")
    got, t = _service({})
    res = AP.send_all(conn, cfg, NOTICE, transport=t)
    assert res == {"devices": 2, "sent": 2, "removed": 0, "failed": 0, "errors": []}
    bodies = {u: json.loads(W.decrypt(r.content, ua, a)) for (u, r), ua, a in zip(got, (ua1, ua2), (a1, a2))}
    assert bodies["https://push.example/d1"]["notification"]["navigate"] == "https://f.example/t/12"
    assert bodies["https://push.example/d2"]["notification"]["navigate"] == "http://127.0.0.1:8751/t/12"
    assert all(r.headers["Topic"] == "task-12" and r.headers["Urgency"] == "high" and r.headers["TTL"] == "86400" for _, r in got)
    assert all(d["last_success_at"] for d in AP.devices(conn))


def test_a_device_that_has_gone_is_removed_and_the_others_still_get_it(conn, cfg):
    AP.keys(conn, create=True)
    _device(conn, "https://push.example/gone"); _device(conn, "https://push.example/flaky"); _device(conn, "https://push.example/ok")
    _, t = _service({"https://push.example/gone": 410, "https://push.example/flaky": 503})
    res = AP.send_all(conn, cfg, NOTICE, transport=t)
    assert res["sent"] == 1 and res["removed"] == 1 and res["failed"] == 1
    left = {d["endpoint"]: d for d in AP.devices(conn)}
    assert set(left) == {"https://push.example/flaky", "https://push.example/ok"}
    assert left["https://push.example/flaky"]["last_error"] == "503 from push.example: nope"
    conn.rollback()                                              # send_all committed its own rows
    assert AP.device_count(conn) == 2


def test_when_no_device_accepts_send_all_records_then_raises(conn, cfg):
    AP.keys(conn, create=True); _device(conn, "https://push.example/a"); _device(conn, "https://push.example/b")
    _, t = _service({"https://push.example/a": 404, "https://push.example/b": httpx.ConnectError("down")})
    with pytest.raises(AP.NothingSent, match="could not reach push.example: ConnectError"):
        AP.send_all(conn, cfg, NOTICE, transport=t)
    conn.rollback()
    assert [d["endpoint"] for d in AP.devices(conn)] == ["https://push.example/b"] and AP.devices(conn)[0]["last_failure_at"]


def test_send_all_with_no_device_or_no_key_file(conn, cfg):
    assert AP.send_all(conn, cfg, NOTICE) == {"devices": 0, "sent": 0, "removed": 0, "failed": 0, "errors": []}   # no key file needed
    _device(conn)
    got, t = _service({})
    with pytest.raises(AP.KeyFileError, match="missing"):
        AP.send_all(conn, cfg, NOTICE, transport=t)
    assert got == [] and AP.devices(conn)[0]["last_failure_at"] is None      # nothing sent, nothing written


def test_send_test_reaches_only_that_device(conn, cfg):
    AP.keys(conn, create=True); _, ua, a = _device(conn, "https://push.example/me"); _device(conn, "https://push.example/other")
    got, t = _service({})
    out = AP.send_test(conn, cfg, "https://push.example/me", transport=t)
    assert out["ok"] and [u for u, _ in got] == ["https://push.example/me"]
    n = json.loads(W.decrypt(got[0][1].content, ua, a))["notification"]
    assert n["title"] == "Factotum" and n["body"] == "Notifications work on this device." and n["navigate"] == "https://f.example/settings"
    assert AP.send_test(conn, cfg, "https://push.example/nobody", transport=t) is None


# --- Task 4: a choice of provider

from datetime import date, datetime  # noqa: E402
from factotum import batches, notify, reminders, config as C  # noqa: E402
from factotum.db import upsert_task  # noqa: E402
from conftest import TODAY, task  # noqa: E402


class Rec:
    """Stands in for notify.publish and for app_push.send_all at once."""
    def __init__(self, fail=None):
        self.ntfy, self.app, self.fail = [], [], fail or set()
    def push(self, cfg, title, body, **kw):
        if "ntfy" in self.fail: raise OSError("ntfy down")
        self.ntfy.append(dict(kw, title=title, body=body)); return "m1"
    def device_push(self, conn, cfg, notice):
        if "app" in self.fail: raise AP.NothingSent("503 from push.example: nope")
        self.app.append(notice); return {"devices": 1, "sent": 1, "removed": 0, "failed": 0, "errors": []}


def _due(conn, task_id=1, status="open"):
    upsert_task(conn, task(id=task_id, name="Call Reza", due="2026-11-05")); conn.commit()
    batches.apply(conn, [{"task_id": task_id, "op": "add_reminder", "fields": {"fire_at": "2026-11-03T09:00", "label": "before the meeting"}}], "ui", TODAY)
    if status != "open":                                         # closed after the reminder was set
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id)); conn.commit()


def test_provider_is_a_settings_key_that_defaults_to_ntfy(cfg):
    assert "notify.provider" in C.SETTINGS_KEYS and C.DEFAULTS["notify.provider"] == "ntfy"
    assert notify.providers(cfg) == {"ntfy"}                     # the owner's file has no provider key: ntfy, as before
    for v, want in (("app", {"app"}), ("both", {"ntfy", "app"}), ("pigeon", {"ntfy"})):
        C.set_(cfg, "notify.provider", v); assert notify.providers(cfg) == want
    assert C.get(C.load(C.EXAMPLE_CONFIG), "notify.provider") == "ntfy"


def test_ready_names_only_providers_that_can_deliver(conn, cfg):
    C.set_(cfg, "notify.ntfy.topic", ""); assert notify.ready(conn, cfg) == []
    C.set_(cfg, "notify.ntfy.topic", "t"); assert notify.ready(conn, cfg) == ["ntfy"]
    C.set_(cfg, "notify.provider", "both"); assert notify.ready(conn, cfg) == ["ntfy"]
    _device(conn); assert notify.ready(conn, cfg) == ["ntfy", "app"]
    C.set_(cfg, "notify.provider", "app"); assert notify.ready(conn, cfg) == ["app"]
    assert notify.ready(conn, C.load(C.STARTER_CONFIG)) == []


def test_a_reminder_goes_to_the_app_with_the_task_address(conn, cfg):
    _due(conn); C.set_(cfg, "notify.provider", "app"); rec = Rec()
    fired = reminders.fire(conn, cfg, datetime(2026, 11, 3, 9, 0, 30), rec.push, device_push=rec.device_push)
    assert rec.ntfy == [] and len(rec.app) == 1 and fired[0]["errors"] == []
    n = rec.app[0]
    assert n["title"] == "Call Reza" and n["body"] == "before the meeting · due Nov 5" and n["path"] == "/t/1" and n["tag"] == "task-1"
    assert [a["title"] for a in n["actions"]] == ["Done", "Snooze 1 h"] and n["urgency"] == "high" and n["ttl"] == 86400


def test_ntfy_and_the_app_carry_the_same_token_links(conn, cfg):
    _due(conn); C.set_(cfg, "notify.provider", "both"); rec = Rec()
    reminders.fire(conn, cfg, datetime(2026, 11, 3, 9, 0, 30), rec.push, device_push=rec.device_push)
    tok = conn.execute("SELECT token FROM reminders WHERE status = 'sent'").fetchone()[0]
    assert [a["url"].split("/r/")[1] for a in rec.ntfy[0]["actions"]][:2] == [f"{tok}?a=snooze", f"{tok}?a=done"]
    assert [a["path"] for a in rec.app[0]["actions"]] == [f"/r/{tok}?a=done", f"/r/{tok}?a=snooze"]


def test_a_reminder_for_a_closed_task_has_no_buttons(conn, cfg):
    _due(conn, status="dropped"); C.set_(cfg, "notify.provider", "app"); rec = Rec()
    reminders.fire(conn, cfg, datetime(2026, 11, 3, 9, 0, 30), rec.push, device_push=rec.device_push)
    assert rec.app[0]["actions"] == [] and "task is dropped" in rec.app[0]["body"] and rec.app[0]["path"] == "/t/1"


def test_one_provider_failing_does_not_hold_the_other(conn, cfg):
    _due(conn); C.set_(cfg, "notify.provider", "both"); rec = Rec(fail={"app"})
    fired = reminders.fire(conn, cfg, datetime(2026, 11, 3, 9, 0, 30), rec.push, device_push=rec.device_push)
    assert len(rec.ntfy) == 1 and fired[0]["errors"] == ["app: 503 from push.example: nope"]
    assert conn.execute("SELECT status FROM reminders").fetchone()[0] == "sent"
    _due(conn, task_id=2); rec2 = Rec(fail={"app", "ntfy"})
    with pytest.raises(OSError):
        reminders.fire(conn, cfg, datetime(2026, 11, 3, 9, 1), rec2.push, device_push=rec2.device_push)
    conn.rollback()
    assert conn.execute("SELECT status FROM reminders WHERE task_id = 2").fetchone()[0] == "pending"


def test_with_the_key_file_gone_a_reminder_waits_and_the_log_says_why(conn, cfg):
    from factotum.scheduler import Scheduler
    _due(conn); _device(conn)                                    # a device, but no key file
    C.set_(cfg, "notify.provider", "app")
    Scheduler(cfg, conn, push=Rec().push).tick(datetime(2026, 11, 3, 9, 0, 30))   # the real app_push.send_all
    assert conn.execute("SELECT status FROM reminders").fetchone()[0] == "pending"
    run = conn.execute("SELECT ok, error FROM runs WHERE kind = 'reminder'").fetchone()
    assert run[0] == 0 and "push-keys.json" in run[1] and "missing" in run[1]


def test_the_scheduler_waits_when_no_provider_can_deliver_and_sends_to_the_app_when_one_can(conn, cfg):
    from factotum.scheduler import Scheduler
    _due(conn); C.set_(cfg, "notify.provider", "app"); rec = Rec()
    s = Scheduler(cfg, conn, push=rec.push, device_push=rec.device_push)
    s.tick(datetime(2026, 11, 3, 9, 0, 30))
    assert rec.app == [] and conn.execute("SELECT status FROM reminders").fetchone()[0] == "pending"   # no device yet
    _device(conn); s.tick(datetime(2026, 11, 3, 9, 1))
    assert len(rec.app) == 1 and conn.execute("SELECT status FROM reminders").fetchone()[0] == "sent"


def test_the_review_nudge_opens_review_in_the_app(conn, cfg):
    from factotum.scheduler import Scheduler
    upsert_task(conn, task(id=1, due=None, created_at="2026-10-30T00:00:00+00:00", last_mention_at="2026-10-30")); conn.commit()
    C.set_(cfg, "notify.provider", "both"); rec = Rec()
    out = Scheduler(cfg, conn, push=rec.push, device_push=rec.device_push)._review_push(cfg, date(2026, 11, 1), {})
    assert out == "1 items" and len(rec.ntfy) == 1 and rec.ntfy[0]["click"].endswith("/review")
    n = rec.app[0]
    assert n["path"] == "/review" and n["tag"] == "review" and n["actions"] == [] and n["urgency"] == "normal" and n["ttl"] == 14400
    assert n["title"] == rec.ntfy[0]["title"] and n["body"] == rec.ntfy[0]["body"]


# --- Task 5: the device routes

from fastapi.testclient import TestClient  # noqa: E402
from factotum.api import create_app  # noqa: E402


def _client(cfg, tmp_path):
    app = create_app(cfg, db_path=str(tmp_path / "api.db"))
    return app, TestClient(app)


def _sub_body(endpoint="https://push.example/me", origin="https://f.example"):
    ua = ec.generate_private_key(ec.SECP256R1())
    return {"endpoint": endpoint, "keys": {"p256dh": W.b64u(W.public_bytes(ua)), "auth": W.b64u(os.urandom(16))},
            "label": "Chrome on Linux", "origin": origin}, ua


def test_the_page_gets_the_public_key_and_never_the_private_one(cfg, tmp_path):
    app, c = _client(cfg, tmp_path)
    r = c.get("/api/push"); j = r.json()
    assert r.status_code == 200 and j["error"] is None and j["provider"] == "ntfy" and j["devices"] == []
    secret = json.loads((tmp_path / "push-keys.json").read_text())["private"]
    assert len(W.unb64u(j["public_key"])) == 65 and c.get("/api/push").json()["public_key"] == j["public_key"]
    assert secret not in r.text and secret not in c.get("/api/settings").text and "push-keys" not in c.get("/api/settings").text


def test_two_devices_are_two_rows_and_unsubscribe_removes_only_one(cfg, tmp_path):
    app, c = _client(cfg, tmp_path)
    c.get("/api/push")
    b1, _ = _sub_body("https://push.example/phone"); b2, _ = _sub_body("https://push.example/desk")
    assert c.post("/api/push/subscribe", json=b1).status_code == 200
    j = c.post("/api/push/subscribe", json=b2).json()
    assert [d["endpoint"] for d in j["devices"]] == ["https://push.example/phone", "https://push.example/desk"]
    assert all("p256dh" not in d and "auth" not in d for d in j["devices"])
    j = c.post("/api/push/unsubscribe", json={"endpoint": "https://push.example/phone"}).json()
    assert [d["endpoint"] for d in j["devices"]] == ["https://push.example/desk"]
    j = c.post("/api/push/unsubscribe", json={"id": j["devices"][0]["id"]}).json(); assert j["devices"] == []
    assert c.post("/api/push/unsubscribe", json={}).status_code == 400


def test_subscribe_refuses_what_is_not_a_device(cfg, tmp_path):
    app, c = _client(cfg, tmp_path)
    b, _ = _sub_body("http://push.example/x")
    r = c.post("/api/push/subscribe", json=b)
    assert r.status_code == 400 and r.json()["detail"] == "not a push service address"
    b, _ = _sub_body(); b["keys"] = {}
    assert c.post("/api/push/subscribe", json=b).status_code == 400
    assert c.post("/api/push/subscribe", content=json.dumps(_sub_body()[0])).status_code == 415      # JSON only, as every /api write
    (tmp_path / "push-keys.json").write_text("garbage")
    r = c.post("/api/push/subscribe", json=_sub_body()[0])
    assert r.status_code == 409 and "unreadable" in r.json()["detail"]
    j = c.get("/api/push").json(); assert j["public_key"] is None and "unreadable" in j["error"]


def test_send_a_test_to_this_device(cfg, tmp_path):
    app, c = _client(cfg, tmp_path)
    c.get("/api/push"); b, ua = _sub_body(); c.post("/api/push/subscribe", json=b)
    got = []
    app.state.push_transport = httpx.MockTransport(lambda req: (got.append(req), httpx.Response(201))[1])
    j = c.post("/api/push/test", json={"endpoint": b["endpoint"]}).json()
    assert j["ok"] is True and len(got) == 1
    assert json.loads(W.decrypt(got[0].content, ua, W.unb64u(b["keys"]["auth"])))["notification"]["navigate"] == "https://f.example/settings"
    app.state.push_transport = httpx.MockTransport(lambda req: httpx.Response(410))
    j = c.post("/api/push/test", json={"endpoint": b["endpoint"]}).json()
    assert j["ok"] is False and j["removed"] is True and j["error"] == "the push service no longer knows this device (410)"
    assert c.get("/api/push").json()["devices"] == []
    assert c.post("/api/push/test", json={"endpoint": b["endpoint"]}).status_code == 404


# --- Task 6: the service worker

def test_the_worker_is_served_at_the_root_and_has_no_fetch_handler(cfg, tmp_path):
    from factotum import api
    app, c = _client(cfg, tmp_path)
    r = c.get("/sw.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert "no-cache" in r.headers["cache-control"]
    js = r.text
    assert not re.search(r"""addEventListener\(\s*['"]fetch['"]|\bonfetch\b""", js)       # never stores or serves the app
    for needed in ("skipWaiting()", "clients.claim()", "addEventListener('push'", "addEventListener('notificationclick'",
                   "function shape(", "function handlePush(", "function handleClick(", "function openApp(", "redirect: 'manual'"):
        assert needed in js, needed
    assert "sw.js" not in api.STAMPED
