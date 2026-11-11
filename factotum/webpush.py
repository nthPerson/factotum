"""Web Push, the protocol only (design §14): message encryption (RFC 8291, the `aes128gcm` content
coding of RFC 8188) and VAPID (RFC 8292, an ES256 JWT), on the `cryptography` package, posted with
`httpx`. No keys, rows or payload shape here; `app_push` owns those.

One record per message, `rs = 4096`: the body is the 86-byte header (salt 16, rs 4, idlen 1, the
sender's public key 65) plus the plaintext, its delimiter (1) and the tag (16). The push services
refuse a body over 4096 bytes, so the plaintext is at most `MAX_PLAINTEXT = 3993` bytes and `encrypt`
refuses more (the RFC's own `len + 17 > rs` check alone would let a 4182-byte body through).

    encrypt(plaintext, ua_public, auth_secret) -> body     decrypt(body, ua_private, auth_secret) -> plaintext
    vapid_header(endpoint, private_key, sub) -> "vapid t=<jwt>, k=<public key>"
    send(endpoint, p256dh, auth, payload, private_key, sub=..., ttl=..., urgency=..., topic=...) -> httpx.Response
"""
from __future__ import annotations

import base64
import json
import os
import re
import struct
import time
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096
HEADER_SIZE = 16 + 4 + 1 + 65           # salt, rs, idlen, keyid (the sender's public key)
OVERHEAD = 1 + 16                       # the last record's delimiter, the AES-GCM tag
MAX_PLAINTEXT = RECORD_SIZE - HEADER_SIZE - OVERHEAD    # 3993

VAPID_LIFETIME = 12 * 3600              # RFC 8292 §2: at most 24 h
TOPIC_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")           # RFC 8030 §5.4, matched whole (fullmatch)


def b64u(data: bytes) -> str:
    """URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(s: str) -> bytes:
    """URL-safe base64, with or without its padding."""
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def public_bytes(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> bytes:
    """The 65-byte uncompressed point of a P-256 key (or of a private key's public half)."""
    if isinstance(key, ec.EllipticCurvePrivateKey):
        key = key.public_key()
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def load_public(raw: bytes) -> ec.EllipticCurvePublicKey:
    """A P-256 public key from its 65-byte uncompressed point; ValueError for anything else."""
    if not isinstance(raw, bytes) or len(raw) != 65 or raw[0] != 0x04:
        raise ValueError("not an uncompressed P-256 point")
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)   # ValueError off the curve


def _hkdf(length: int, salt: bytes, info: bytes, ikm: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _keys(ecdh: bytes, auth_secret: bytes, ua_public: bytes, as_public: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """RFC 8291 §3.3–3.4: the content encryption key and the nonce."""
    ikm = _hkdf(32, auth_secret, b"WebPush: info\x00" + ua_public + as_public, ecdh)
    cek = _hkdf(16, salt, b"Content-Encoding: aes128gcm\x00", ikm)
    nonce = _hkdf(12, salt, b"Content-Encoding: nonce\x00", ikm)
    return cek, nonce


def encrypt(plaintext: bytes, ua_public: bytes, auth_secret: bytes, *, salt: bytes | None = None,
            sender_private: ec.EllipticCurvePrivateKey | None = None, record_size: int = RECORD_SIZE) -> bytes:
    """The whole `aes128gcm` body for one push: the header and a single record.

    Raises ValueError when the body would be longer than `record_size` bytes."""
    if HEADER_SIZE + len(plaintext) + OVERHEAD > record_size:
        raise ValueError(f"a push body holds at most {record_size - HEADER_SIZE - OVERHEAD} bytes of plaintext, "
                         f"not {len(plaintext)}")
    salt = os.urandom(16) if salt is None else salt
    if len(salt) != 16:
        raise ValueError("the salt is 16 bytes")
    sender_private = sender_private or ec.generate_private_key(ec.SECP256R1())
    as_public = public_bytes(sender_private)
    ecdh = sender_private.exchange(ec.ECDH(), load_public(ua_public))
    cek, nonce = _keys(ecdh, auth_secret, ua_public, as_public, salt)
    record = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    return salt + struct.pack(">IB", record_size, len(as_public)) + as_public + record


def decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth_secret: bytes) -> bytes:
    """The inverse of `encrypt`, for one record (what a fake push service in a test received)."""
    if len(body) < 21:
        raise ValueError("too short for an aes128gcm header")
    salt = body[:16]
    rs, idlen = struct.unpack(">IB", body[16:21])
    as_public = body[21:21 + idlen]
    record = body[21 + idlen:]
    if len(record) > rs:
        raise ValueError("more than one record")
    ecdh = ua_private.exchange(ec.ECDH(), load_public(as_public))
    cek, nonce = _keys(ecdh, auth_secret, public_bytes(ua_private), as_public, salt)
    padded = AESGCM(cek).decrypt(nonce, record, None).rstrip(b"\x00")
    if not padded.endswith(b"\x02"):
        raise ValueError("no last-record delimiter")
    return padded[:-1]


def vapid_header(endpoint: str, private_key: ec.EllipticCurvePrivateKey, sub: str | None,
                 now: int | None = None) -> str:
    """The `Authorization` value for a push to `endpoint` (RFC 8292 §2–3)."""
    u = urlsplit(endpoint)
    claims: dict = {"aud": f"{u.scheme}://{u.netloc}",
                    "exp": (int(time.time()) if now is None else now) + VAPID_LIFETIME}
    if sub is not None:
        claims["sub"] = sub
    seg = lambda obj: b64u(json.dumps(obj, separators=(",", ":")).encode())    # noqa: E731
    signing_input = f'{seg({"typ": "JWT", "alg": "ES256"})}.{seg(claims)}'
    r, s = decode_dss_signature(private_key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"vapid t={signing_input}.{b64u(signature)}, k={b64u(public_bytes(private_key))}"


def send(endpoint: str, p256dh: str, auth: str, payload: bytes, private_key: ec.EllipticCurvePrivateKey, *,
         sub: str | None, ttl: int, urgency: str, topic: str | None, transport=None,
         timeout: float = 10) -> httpx.Response:
    """POST one encrypted message to a subscription's endpoint. The caller reads the status; this
    never raises for one (only for no answer at all, or a payload that does not fit)."""
    headers = {"Authorization": vapid_header(endpoint, private_key, sub),
               "Content-Encoding": "aes128gcm",
               "Content-Type": "application/octet-stream",
               "TTL": str(ttl),
               "Urgency": urgency}
    if topic and TOPIC_RE.fullmatch(topic):
        headers["Topic"] = topic
    body = encrypt(payload, unb64u(p256dh), unb64u(auth))
    with httpx.Client(timeout=timeout, transport=transport) as client:
        return client.post(endpoint, content=body, headers=headers)
