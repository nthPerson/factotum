import json, os, re

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

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
