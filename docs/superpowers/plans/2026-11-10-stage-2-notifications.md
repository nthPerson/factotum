# Stage 2: notifications from the app — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Factotum sends its own notifications (reminders and the evening review nudge) to every device where the owner turned them on, and a tap opens the task they are about. ntfy stays available as a choice.

**Architecture:** A small protocol module (`factotum/webpush.py`) does Web Push message encryption (RFC 8291, `aes128gcm`) and VAPID (RFC 8292, ES256) with the `cryptography` package and posts with `httpx`. An app module (`factotum/app_push.py`) owns the server's key pair (a `0600` file beside the database), the `push_subscriptions` table, the declarative payload, and sending to every device. `notify.provider` (ntfy, the app, or both) decides where `reminders.fire` and the review nudge go. A service worker at `/sw.js` that only handles `push` and `notificationclick` shows the same payload on browsers that do not show declarative messages themselves, and Settings gets a Notifications section with "Turn on notifications on this device".

**Tech Stack:** Python 3.12, FastAPI/Starlette, SQLite, `cryptography` 50, `httpx` 0.28, pytest (offline, `httpx.MockTransport` for push services); plain JavaScript and CSS with no build step; a service worker; Playwright (outside the repository) for UI checks.

**Spec:** `docs/superpowers/specs/2026-11-10-linked-tasks-design.md` — §14 (all of it), §5.2 (`push_subscriptions`), §2 goal 9 and anti-goals, §3, §4 (words), §13 (what stage 1 built: `/t/<id>`, the build stamp, the signed-out rule), §16 (checks every stage passes), §18 (key storage and encryption left to this plan). Read `CLAUDE.md` in full.

**Out of this stage:** offline caching of any kind; a "free now" notification (stage 5); any change to what `/r/<token>` does or to the page it shows after Done or Snooze; a badge on the icon (stage 8); sending sync-file's failure alert anywhere but ntfy.

## Global Constraints

- Work only in `/home/robert/factotum/.worktrees/linked-tasks` (branch `linked-tasks`), called WT, and never touch the main checkout or another worktree: never read from, write to, or run git in `/home/robert/factotum` outside `.worktrees/linked-tasks`. The main checkout serves a live app from its files.
- Python is `/home/robert/factotum/.venv/bin/python`, tests `/home/robert/factotum/.venv/bin/pytest`, run from WT.
- Do not install packages. Push uses only `cryptography` (installed, 50.x) and `httpx` (a dependency). No `pywebpush`, no `py_vapid`, no `jwt` library.
- No real Claude runs, no scheduler, no sweeps, no sudo, no `systemctl`.
- Never open `config/factotum.yaml`, `.env` or `/home/robert/factotum/data`.
- No network calls to a real push service from tests or scenarios. Tests pass an `httpx.MockTransport`; scenarios use endpoints on the reserved `.invalid` domain (`https://push.invalid/...`), which never resolves, or stub the route.
- Commit rules from CLAUDE.md: small commits on branch `linked-tasks`, the whole `pytest` suite green at every commit, never push. Every commit message ends with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` (CLAUDE.md's trailer, exactly). Message style: `area: what changed and why`.
- The service worker has NO `fetch` handler, ever (no `addEventListener('fetch'`, no `onfetch`). It never stores or serves the app.
- Every push shows a notification; none is silent. A payload the worker cannot read still shows "Factotum".
- `/api` never answers 401, 403 or a redirect (the page reads each as an expired sign-in). Use 400, 404, 409 for refusals. Every state-changing `/api` call carries `Content-Type: application/json` (the page's `api()` helper sets it; tests pass `json=...`).
- Absent means off for anything that costs money or sends mail (`config.DEFAULTS`, read with `C.value`). A push costs nothing and sends no mail; `notify.provider` defaults to `ntfy` so an existing configuration keeps ntfy, and the starter (ntfy off, no topic, no provider key) sends nothing.
- The key pair is a secret: never in git, never in any `/api` response except the public half, never in `/api/settings`.
- `/r/<token>`, `reminders.act` and `api._page` are unchanged. Notification buttons call the same token links ntfy uses; nothing bypasses the sign-in.
- Every surface is checked at 1280 px and 390 px.
- User-visible strings this plan gives in backticks or quotes are used exactly as written. Words shown to the user follow design §4; "blocked" and "dependency" are not used.
- `db.log_run` and `set_setting` do not commit; the caller does. A new connection used from several threads needs `connect(path, shared=True)`.
- `app.js` stays one file; new functions go beside the code they belong with, under the existing `// ---------- … ----------` comments (a new `// ---------- notifications (stage 2) ----------` section for this stage). What is typed lives in `draft`; this stage adds no text box.
- New tests go in `tests/test_stage2.py`, one section comment per task (`# --- Task N: …`), importing `TODAY`, `task`, `FIXTURE` from `conftest` like `tests/test_stage1.py`. The `cfg` fixture loads `tests/fixtures/factotum.fixture.yaml` (stage 3); `conn` is a file database under `tmp_path`.

**How a UI task is verified** (there are no JS tests; a UI task is not done on a clean pytest run alone):

- Dev instance on a COPY of the database, no scheduler, at http://127.0.0.1:8751:
  `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev/serve.sh restart`
  (restart after any Python change; files under `factotum/web/` are served straight from WT, and `/sw.js` is read from disk per request). The dev database lives in that `dev/` folder, so the dev key file is `dev/push-keys.json`, never the owner's.
- A real browser at desktop (1280x900) and phone (390x844, touch, an iPhone user agent on Chromium) size:
  `cd /tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev && node pw.mjs scenarios/<name>.mjs --device both`
  A scenario is an ES module, `export default async ({ page, context, shot, base, device, log }) => { ... }` (Playwright API; `context.grantPermissions(['notifications'], { origin: base })`, `context.route`, `page.addInitScript`, `context.serviceWorkers()` / `context.waitForEvent('serviceworker')` and `worker.evaluate` are available); `shot('name')` saves `.../scratchpad/shots/<name>-<device>.png`; console errors, failed requests and HTTP >= 400 are printed at the end. Scenarios for this stage are `scenarios/s2-<task>-<name>.mjs` (not committed). The implementer runs each for both devices, LOOKS at every screenshot (the Read tool shows images) and reports what was seen.
- What a headless browser can and cannot show. It CAN register the worker and see it control the page; render every state of the Settings control (states it cannot reach for real are stubbed with `page.addInitScript` or `context.route`, and the report says which were stubbed); run the worker's handlers through `worker.evaluate` (they are global functions for that reason) and see where a click lands. It very likely CANNOT obtain a real subscription from a push service (the scenarios stub `PushManager.prototype.subscribe` with a real P-256 key made by `crypto.subtle` and an endpoint on `push.invalid`), and no real notification reaches a real phone. `registration.getNotifications()` may return nothing headless; when it does, the report says so and relies on the logged output of `shape()`.
- Do not load the Review tab in a scenario. Never press Apply. Leave the dev database as found: turn notifications off again and set "Send reminders to" back to `ntfy` at the end of a scenario that changed them.

## Decisions this plan makes

- **Encryption with `cryptography`**, not a library: `webpush.encrypt` is pinned by RFC 8291 Appendix A (every key, the salt and the expected body; the values below were checked against `cryptography` 50 before this plan was written). One record, `rs = 4096`, so the plaintext is at most `4096 − 86 (header) − 1 (delimiter) − 16 (tag) = 3993` bytes (`webpush.MAX_PLAINTEXT`); `app_push.payload` trims to fit.
- **The key pair lives in `push-keys.json` beside the database** (the directory of the file the connection has open, read with `PRAGMA database_list`), written atomically with mode `0600`: `{"private": "<base64url 32-byte scalar>", "public": "<base64url 65-byte uncompressed point>", "created_at": "<iso>"}`. It is made by the first `GET /api/push` (no `.env` edit, survives restarts, never in the repository; `.gitignore` also lists `push-keys.json` in case someone puts the database in the repository root). Making a new pair deletes every row of `push_subscriptions`, because each was made with the old public key and can never work again. The scheduler never makes a pair: a missing or unreadable file at send time raises `KeyFileError`, nothing is sent, and the Log says why. An unreadable file is never overwritten.
- **One extra column, `origin`,** on `push_subscriptions`: the app's origin on that device (`location.origin`, sent when it subscribes). A notification's `navigate` and button links are absolute URLs on it, which is what the declarative format needs and what keeps the dev instance's links on the dev instance. The other columns are the design's: endpoint, the two keys (`p256dh`, `auth`), a label, created, last success, last failure (+ `last_error`, the reason shown).
- **`notify.provider`**: `ntfy` | `app` | `both`, a Settings key, `DEFAULTS["notify.provider"] = "ntfy"`. Absent means ntfy, so the owner's file keeps ntfy; the starter has no provider key and ntfy off, so it sends nothing. An unknown value is treated as `ntfy`.
- **Payload: the declarative format, without `mutable`.** Safari (iOS 18.4+, macOS 15.5+) shows it without running the worker; Chrome, Edge and Firefox hand the same JSON to the worker's `push` handler, which shows it. One payload for every device, buttons included: where the platform shows declarative buttons they navigate to the token link (the page "Task-N marked done", as from ntfy today); where the worker runs, it fetches the link and opens the task instead when that fails.
- **Buttons: Done, then Snooze 1 h** (browsers show at most two). Open is the tap. A reminder whose task is no longer open gets no buttons; its body says "task is done" (today's body) and the tap opens the task page, which says it is closed.
- **Repeats replace each other:** `tag` `task-<id>` (review nudge `review`, test `test`) with `renotify: true`, and the same value as the push service's `Topic` header, so an undelivered older one is replaced at the service too.
- **TTL and urgency:** a reminder 86400 s (the token's life) and `high`; the review nudge 14400 s and `normal`; a test 600 s and `high`.
- **VAPID `sub`:** `server.public_url` when it is `https://…`, else the device's origin when that is `https://…`, else no `sub` claim. No new config key. `exp` is now + 12 h.
- **Delivery semantics.** `send_all` makes every network call before its first write, then writes the device rows and commits them itself (so a removal survives the caller's rollback), then raises `NothingSent` when there was at least one device and none accepted. A provider that raises does not stop the other: a reminder is marked sent when at least one chosen provider did not raise; when every chosen provider raised, the first error propagates, the reminder stays pending and the scheduler logs it once an hour, as an ntfy failure does today. The scheduler fires reminders only when `notify.ready` names at least one provider that can deliver (ntfy enabled with a topic; the app with at least one device), so nothing is marked sent into the void.
- **404 or 410 from a push service removes the device.** Anything else non-2xx, or no answer, is a failure recorded on the row (`last_failure_at`, `last_error`) and kept.
- **The worker** is `factotum/web/sw.js`, served at `/sw.js` by its own route with `Cache-Control: no-cache, must-revalidate` (so it controls the whole app) and registered with `updateViaCache: 'none'`; `skipWaiting()` on install, `clients.claim()` on activate. It is not in `api.STAMPED`: the browser re-checks a worker itself on every navigation, and a worker change must not reload open pages. Its handlers are global functions (`shape`, `handlePush`, `handleClick`, `openApp`) so a scenario can run them.
- **A click opens the app without reloading it when a window is open:** the worker posts `{type: 'open', path}` to an open window of the app and focuses it; the page follows the path through stage 1's `applyAddress`. With no window it calls `clients.openWindow`.
- **The permission comes from the tap:** `Notification.requestPermission()` is the first call in the button's handler, before any `await` (iOS refuses it otherwise).
- **Sending a test is synchronous in the API route** (one device, 10 s timeout). It is a direct user action, not a sweep.

## Review Focus

1. **A subscription that has gone away** (the app was deleted from the phone, the browser's data cleared): the push service answers 410 or 404 and the device must disappear without failing the others or the reminder — Task 3 `test_a_device_that_has_gone_is_removed_and_the_others_still_get_it`.
2. **The key file missing or unreadable at send time**: nothing may be sent, nothing silently regenerated, the reminder must stay pending and the Log must say why; the next `GET /api/push` makes a new pair only when the file is missing and clears the dead subscriptions — Task 2 `test_a_missing_key_file_is_made_again_and_the_old_devices_go`, `test_an_unreadable_key_file_is_never_overwritten`; Task 4 `test_with_the_key_file_gone_a_reminder_waits_and_the_log_says_why`.
3. **Two devices** (the owner's phone and desktop): both get every reminder; a failure or removal of one leaves the other — Task 3 (same test as 1), Task 5 `test_two_devices_are_two_rows_and_unsubscribe_removes_only_one`.
4. **A payload over the push service's 4096-byte limit** (a task name or reminder label pasted from a mail): it must be trimmed, never refused — Task 1 `test_encrypt_refuses_a_record_that_does_not_fit`, Task 3 `test_a_payload_never_exceeds_the_limit_and_keeps_its_links`.
5. **A reminder whose task was closed meanwhile**: no Done or Snooze button that would act on a closed task; the body says the task's status; the tap opens the task page — Task 4 `test_a_reminder_for_a_closed_task_has_no_buttons`.
6. **A push while the sign-in has expired**: a button press must open the task page (which shows "Sign in again"), never fail silently — pinned by Task 6's scenario (the dev server answers 404 for a made-up token, and a stubbed 302 stands for Access); the server side, that the buttons use the same token links as ntfy and nothing bypasses the sign-in, by Task 4 `test_ntfy_and_the_app_carry_the_same_token_links`.

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `factotum/webpush.py` (new) | the protocol: base64url, RFC 8291 encrypt/decrypt, VAPID header, one HTTP send | 1 |
| `factotum/app_push.py` (new) | the key pair file, `push_subscriptions` rows, the declarative payload, delivering to devices and recording the outcome | 2, 3 |
| `factotum/db.py` | `push_subscriptions` in `SCHEMA` | 2 |
| `.gitignore` | `push-keys.json` | 2 |
| `factotum/config.py`, `config/factotum.example.yaml` | `notify.provider`: `SETTINGS_KEYS`, `DEFAULTS`, documented | 4 |
| `factotum/notify.py` | `providers`, `ready` | 4 |
| `factotum/reminders.py` | `notice`, `fire` sends to the chosen providers | 4 |
| `factotum/scheduler.py` | `device_push`, the reminders gate, the review nudge to the app | 4 |
| `factotum/api.py` | `/api/push`, `/api/push/subscribe`, `/api/push/unsubscribe`, `/api/push/test`; `/sw.js` | 5, 6 |
| `factotum/web/sw.js` (new) | the notifications-only service worker | 6 |
| `factotum/web/app.js`, `factotum/web/app.css` | registering the worker, following its clicks; Settings → Notifications; the header's review pill | 6, 7 |
| `tests/test_stage2.py` (new) | every new test of this stage | 1–6 |
| `SPEC.md`, `CLAUDE.md`, `docs/SETUP.md` | the stage's decisions, gotchas and setup | 8 |

---

### Task 1: Web Push encryption and VAPID

Implementer: **opus** (careful cryptography). UI task: **no**.

**Files:**
- Create: `factotum/webpush.py`
- Test: `tests/test_stage2.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (all in `factotum/webpush.py`):
  - `MAX_PLAINTEXT = 3993`; `RECORD_SIZE = 4096`
  - `b64u(data: bytes) -> str` (URL-safe, no padding); `unb64u(s: str) -> bytes` (accepts missing padding)
  - `public_bytes(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> bytes` (65-byte uncompressed point)
  - `load_public(raw: bytes) -> ec.EllipticCurvePublicKey` (raises `ValueError` unless a P-256 point)
  - `encrypt(plaintext: bytes, ua_public: bytes, auth_secret: bytes, *, salt: bytes | None = None, sender_private: ec.EllipticCurvePrivateKey | None = None, record_size: int = RECORD_SIZE) -> bytes` — the whole `aes128gcm` body (header + one record); raises `ValueError` when `len(plaintext) + 17 > record_size`
  - `decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth_secret: bytes) -> bytes` (the inverse; tests use it to read what a fake push service received; raises `ValueError` on a bad delimiter)
  - `vapid_header(endpoint: str, private_key: ec.EllipticCurvePrivateKey, sub: str | None, now: int | None = None) -> str` — `"vapid t=<jwt>, k=<b64u public>"`
  - `send(endpoint: str, p256dh: str, auth: str, payload: bytes, private_key, *, sub: str | None, ttl: int, urgency: str, topic: str | None, transport=None, timeout: float = 10) -> httpx.Response` — never calls `raise_for_status`

- [ ] **Step 1: Write the failing tests** in `tests/test_stage2.py` under `# --- Task 1: encryption and VAPID`.

```python
import json, os, re, stat
import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from factotum import webpush as W

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
        W.encrypt(b"x" * (W.RECORD_SIZE - 16), W.public_bytes(ua), os.urandom(16))

def test_vapid_header_is_a_signed_es256_jwt_for_the_push_service():
    key = ec.generate_private_key(ec.SECP256R1())
    h = W.vapid_header("https://push.example:8443/send/abc?x=1", key, "https://factotum.example.org", now=1_800_000_000)
    m = re.fullmatch(r"vapid t=([\w-]+)\.([\w-]+)\.([\w-]+), k=([\w-]+)", h)
    assert m and W.unb64u(m.group(4)) == W.public_bytes(key)
    assert json.loads(W.unb64u(m.group(1))) == {"typ": "JWT", "alg": "ES256"}
    claims = json.loads(W.unb64u(m.group(2)))
    assert claims == {"aud": "https://push.example:8443", "exp": 1_800_000_000 + 12 * 3600, "sub": "https://factotum.example.org"}
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
```
(`import pytest` at the top of the file with the other imports.)

- [ ] **Step 2: Run them to see them fail.**
Run: `/home/robert/factotum/.venv/bin/pytest tests/test_stage2.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'factotum.webpush'`.

- [ ] **Step 3: Implement `factotum/webpush.py`.** A module docstring naming RFC 8291, RFC 8188 and RFC 8292 and the limit. The algorithm (RFC 8291 §3.3–3.4, RFC 8188 §2):
  - `ecdh = sender_private.exchange(ec.ECDH(), load_public(ua_public))`; `ikm = HKDF(SHA256, length=32, salt=auth_secret, info=b"WebPush: info\x00" + ua_public + as_public).derive(ecdh)`; `cek = HKDF(SHA256, 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)`; `nonce = HKDF(SHA256, 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)` (a fresh `HKDF` object per derive).
  - Record: `AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)` (the last record's delimiter, no padding).
  - Header: `salt (16) || struct.pack(">IB", record_size, 65) || as_public (65)`.
  - Defaults: `salt = os.urandom(16)`, `sender_private = ec.generate_private_key(ec.SECP256R1())`.
  - `decrypt`: read salt, rs, idlen, keyid (the sender's public key) from the header, derive the same way with `ua_private.exchange(...)` and `ua_public = public_bytes(ua_private)`, decrypt, strip trailing `0x00` padding, require the last byte `0x02` and drop it.
  - `vapid_header`: JWT `b64u(json.dumps({"typ":"JWT","alg":"ES256"}, separators=(",",":")))`, claims `{"aud": f"{scheme}://{netloc}", "exp": now + 43200, "sub": sub}` (`sub` omitted when None; `now` defaults to `int(time.time())`), signature `private_key.sign(input, ec.ECDSA(SHA256()))` turned from DER into raw `r || s` (32 bytes each) with `decode_dss_signature`.
  - `send`: headers `Authorization`, `Content-Encoding: aes128gcm`, `Content-Type: application/octet-stream`, `TTL: str(ttl)`, `Urgency`, and `Topic` only when it matches `^[A-Za-z0-9_-]{1,32}$`; `httpx.Client(timeout=timeout, transport=transport).post(endpoint, content=encrypt(payload, unb64u(p256dh), unb64u(auth)), headers=...)`.

- [ ] **Step 4: Run the tests to see them pass.**
Run: `/home/robert/factotum/.venv/bin/pytest tests/test_stage2.py -q` — Expected: 5 passed. Then the whole suite: `/home/robert/factotum/.venv/bin/pytest -q` — all pass.

- [ ] **Step 5: Commit** (`webpush: Web Push message encryption (RFC 8291, aes128gcm, pinned by the RFC's Appendix A vector) and VAPID (RFC 8292, an ES256 JWT) with the cryptography package already installed; one record of at most 3993 bytes of plaintext` + trailer).

---

### Task 2: The key pair and the devices table

Implementer: **sonnet**. UI task: **no**.

**Files:**
- Create: `factotum/app_push.py`
- Modify: `factotum/db.py` (`SCHEMA`), `.gitignore`
- Test: `tests/test_stage2.py`

**Interfaces:**
- Consumes: `webpush.b64u`, `unb64u`, `public_bytes`, `load_public` (Task 1).
- Produces (in `factotum/app_push.py`):
  - `KEY_FILE = "push-keys.json"`
  - `class KeyFileError(Exception)` — its message names the file and says "missing" or "unreadable"
  - `Keys = NamedTuple("Keys", [("private", ec.EllipticCurvePrivateKey), ("public", str)])` (`public` is base64url, what the page passes to `subscribe`)
  - `key_path(conn) -> Path` — the directory of the connection's main database file (`PRAGMA database_list`, the row named `main`) joined with `KEY_FILE`; raises `KeyFileError("the database has no file, so there is nowhere to keep the notification keys")` for an in-memory database
  - `keys(conn, create: bool = False) -> Keys` — reads the file; when it is missing and `create` is True, makes a pair, writes it atomically (a temporary file in the same directory opened with `os.open(..., O_WRONLY|O_CREAT|O_EXCL, 0o600)`, then `os.replace`), runs `DELETE FROM push_subscriptions` and commits; missing and not `create` → `KeyFileError(f"the notification key file {path} is missing")`; present but not JSON, missing a field, or its private key does not give its public key → `KeyFileError(f"the notification key file {path} is unreadable")`, whatever `create` says, and the file is left as it is
  - `subscribe(conn, endpoint: str, p256dh: str, auth: str, label: str, origin: str) -> int` — validates, upserts by `endpoint`, returns the row id, does not commit; raises `ValueError` with exactly: `"not a push service address"` (not `https`, no host, an IP literal, or `localhost`), `"the device key is not a P-256 public key"`, `"the device secret must be 16 bytes"`, `"origin must be scheme://host[:port]"` (must match `^https?://[^/?#\s]+$`). `label` is stripped and cut to 60 characters, `"A device"` when empty. An upsert of a known endpoint replaces the keys, label and origin, clears `last_failure_at` and `last_error`, and keeps `id` and `created_at`.
  - `unsubscribe(conn, *, endpoint: str | None = None, device_id: int | None = None) -> int` — rows deleted, does not commit
  - `devices(conn) -> list[dict]` — every row, ordered by `id`
  - `device_count(conn) -> int`
  - Table (append to `db.SCHEMA`):
    ```sql
    CREATE TABLE IF NOT EXISTS push_subscriptions (    -- one row per device that turned app notifications on (stage 2)
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      endpoint TEXT NOT NULL UNIQUE,                  -- the push service's address for this device
      p256dh TEXT NOT NULL, auth TEXT NOT NULL,       -- the device's public key and secret, base64url as the browser gives them
      label TEXT NOT NULL,                            -- "iPhone", "Chrome on Windows"
      origin TEXT NOT NULL,                           -- the app's origin on that device; a notification's links are built on it
      created_at TEXT NOT NULL,
      last_success_at TEXT, last_failure_at TEXT, last_error TEXT
    );
    ```
  - Test helper (in `tests/test_stage2.py`, used by Tasks 3–5): `_device(conn, endpoint="https://push.example/d1", origin="https://f.example", label="Phone") -> tuple[int, ec.EllipticCurvePrivateKey, bytes]` — makes a P-256 key and a 16-byte secret, calls `app_push.subscribe`, commits, returns `(id, ua_private, auth)`.

- [ ] **Step 1: Write the failing tests** under `# --- Task 2: the key pair and the devices table`.

```python
from factotum import app_push as AP

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
```

- [ ] **Step 2: Run them to see them fail.**
Run: `/home/robert/factotum/.venv/bin/pytest tests/test_stage2.py -q` — Expected: FAIL with `ModuleNotFoundError: No module named 'factotum.app_push'`.

- [ ] **Step 3: Implement.** The table in `db.SCHEMA` (above; an existing database gets it from `CREATE TABLE IF NOT EXISTS`, no `MIGRATIONS` entry). `factotum/app_push.py` with a docstring saying what the file holds, where it lives, that it is never sent anywhere but the public half, and that a new pair empties `push_subscriptions`. The IP-literal check uses `ipaddress.ip_address(host)` succeeding. The point check uses `webpush.load_public`. Add `push-keys.json` to `.gitignore` under `# secrets and per-install config`.

- [ ] **Step 4: Run the tests to see them pass.** `/home/robert/factotum/.venv/bin/pytest tests/test_stage2.py -q` then `/home/robert/factotum/.venv/bin/pytest -q` — all pass.

- [ ] **Step 5: Commit** (`app_push: the notification key pair, made once in push-keys.json beside the database (0600, never in git, never overwritten when unreadable; a new pair empties the devices, which were bound to the old one), and push_subscriptions, one row per device with its own origin` + trailer).

---

### Task 3: The payload and sending to every device

Implementer: **opus**. UI task: **no**.

**Files:**
- Modify: `factotum/app_push.py`
- Test: `tests/test_stage2.py`

**Interfaces:**
- Consumes: Task 1's `webpush.send`, `MAX_PLAINTEXT`, `decrypt` (tests); Task 2's `keys`, `devices`, `KeyFileError`, `_device` helper.
- Produces (in `factotum/app_push.py`):
  - A *notice* is a plain dict: `{"title": str, "body": str, "path": str, "tag": str, "actions": [{"action": str, "title": str, "path": str}], "urgency": "high" | "normal", "ttl": int, "data": dict}` (`actions` and `data` may be absent). Paths start with `/`.
  - `class NothingSent(Exception)` — raised by `send_all` after recording, when at least one device existed and none accepted; its message is the outcomes joined with `"; "`.
  - `payload(notice: dict, origin: str) -> bytes` — the declarative message, compact JSON, UTF-8, at most `webpush.MAX_PLAINTEXT` bytes:
    ```json
    {"web_push": 8030,
     "notification": {"title": "Call Reza", "body": "before the meeting · due Nov 5",
                      "navigate": "https://f.example/t/12", "tag": "task-12", "renotify": true,
                      "actions": [{"action": "done", "title": "Done", "navigate": "https://f.example/r/<token>?a=done"},
                                  {"action": "snooze", "title": "Snooze 1 h", "navigate": "https://f.example/r/<token>?a=snooze"}],
                      "data": {"task_id": 12}}}
    ```
    (`navigate` = `origin + path`; `renotify` true whenever there is a tag; `actions` omitted when the notice has none; `data` omitted when absent or None; no `mutable` member.) Over the limit: cut `body` from the end, ending it with `…`, down to `…` alone if need be; then `title` the same way, down to its first character and `…`; then drop `actions`. `navigate` and `tag` are never cut.
  - `vapid_sub(cfg: dict, origin: str) -> str | None` — `server.public_url` (stripped of a trailing `/`) when it starts with `https://`, else `origin` when it does, else None.
  - `deliver(device: dict, notice: dict, keys: Keys, cfg: dict, transport=None) -> dict` — network only, never raises, writes nothing. Returns `{"id": int, "ok": bool, "status": int | None, "removed": bool, "error": str | None}`. `ok` for 2xx; `removed` for 404 and 410 with error `f"the push service no longer knows this device ({status})"`; another status gives `f"{status} from {host}: {text}"` (`text` = the response body stripped, first 200 characters); an `httpx.HTTPError` gives `f"could not reach {host}: {type(e).__name__}"`.
  - `record(conn, outcome: dict) -> None` — deletes the row when `removed`; else sets `last_success_at = now_iso()` when `ok`, or `last_failure_at = now_iso()` and `last_error`; does not commit.
  - `send_all(conn, cfg: dict, notice: dict, *, transport=None) -> dict` — `{"devices": n, "sent": k, "removed": r, "failed": f, "errors": [str]}`. With no device: returns zeros without reading the key file. Otherwise `keys(conn)` (never `create`; `KeyFileError` propagates and nothing is written), `deliver` to each device, then `record` each, then `conn.commit()`, then raises `NothingSent` when `sent == 0`.
  - `send_test(conn, cfg: dict, endpoint: str, *, transport=None) -> dict | None` — None when no row has that endpoint; otherwise one `deliver` of `TEST_NOTICE`, `record`, `commit`, and the outcome; a `KeyFileError` becomes `{"id": ..., "ok": False, "status": None, "removed": False, "error": str(e)}` with nothing written.
  - `TEST_NOTICE = {"title": "Factotum", "body": "Notifications work on this device.", "path": "/settings", "tag": "test", "actions": [], "urgency": "high", "ttl": 600}`

- [ ] **Step 1: Write the failing tests** under `# --- Task 3: the payload and sending`.

```python
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
```
- [ ] **Step 2: Run them to see them fail.** `/home/robert/factotum/.venv/bin/pytest tests/test_stage2.py -q` — Expected: FAIL with `AttributeError: module 'factotum.app_push' has no attribute 'payload'`.

- [ ] **Step 3: Implement** the functions above in `factotum/app_push.py`. `deliver` calls `webpush.send(device["endpoint"], device["p256dh"], device["auth"], payload(notice, device["origin"]), keys.private, sub=vapid_sub(cfg, device["origin"]), ttl=notice["ttl"], urgency=notice["urgency"], topic=notice["tag"], transport=transport)`. A comment above `send_all` says why it commits itself (a removal must survive the caller's rollback) and why every network call comes before the first write (CLAUDE.md: the write lock is never held across the network).

- [ ] **Step 4: Run the tests to see them pass.** `/home/robert/factotum/.venv/bin/pytest tests/test_stage2.py -q`, then `/home/robert/factotum/.venv/bin/pytest -q` — all pass.

- [ ] **Step 5: Commit** (`app_push: the declarative payload (web_push 8030, links on each device's own origin, trimmed to the 4096-byte limit, never its links) and sending to every device: a 404 or 410 removes the device, any other failure is kept on its row, the network before the first write` + trailer).

---

### Task 4: A choice of provider; reminders and the review nudge go to it

Implementer: **opus**. UI task: **no**.

**Files:**
- Modify: `factotum/config.py` (`SETTINGS_KEYS`, `DEFAULTS`), `config/factotum.example.yaml` (`notify:`), `factotum/notify.py`, `factotum/reminders.py`, `factotum/scheduler.py`
- Test: `tests/test_stage2.py`

**Interfaces:**
- Consumes: `app_push.send_all`, `device_count`, `KeyFileError`, `NothingSent`, the notice shape (Task 3); stage 3's `C.value`, `C.DEFAULTS`.
- Produces:
  - Settings key `notify.provider`, values `"ntfy" | "app" | "both"`, `DEFAULTS["notify.provider"] = "ntfy"`, listed in `SETTINGS_KEYS` after `"notify.ntfy.topic"`.
  - `notify.providers(cfg) -> set[str]` — `{"ntfy"}`, `{"app"}` or `{"ntfy", "app"}` from `C.value(cfg, "notify.provider")`; anything else → `{"ntfy"}`.
  - `notify.ready(conn, cfg) -> list[str]` — in the order `["ntfy", "app"]`, the chosen providers that can deliver: `"ntfy"` when chosen and `enabled(cfg)`; `"app"` when chosen and `app_push.device_count(conn) > 0`.
  - `reminders.notice(r: dict, token: str) -> dict` — `{"title": r["task_name"], "body": _body(r), "path": f"/t/{task_id}", "tag": f"task-{task_id}", "actions": [Done, Snooze] when r["task_status"] == "open" else [], "urgency": "high", "ttl": 86400, "data": {"task_id": task_id}}`, the actions being `{"action": "done", "title": "Done", "path": f"/r/{token}?a=done"}` then `{"action": "snooze", "title": "Snooze 1 h", "path": f"/r/{token}?a=snooze"}`.
  - `reminders.fire(conn, cfg, now_local, push, device_push=None) -> list[dict]` — per due reminder, one token; ntfy through `push(...)` exactly as today when `"ntfy" in providers(cfg)`; the app through `device_push(conn, cfg, notice(r, token))` when `"app" in providers(cfg)` and `device_push` is given. Each call in its own `try`. When every attempted provider raised, re-raise the first exception (the row stays pending). Otherwise mark sent as today and add `"errors": [f"{provider}: {e}"]` (empty when none) and `"app": <send_all result or None>` to the returned item. When neither provider was attempted (no `device_push` and provider `app`), the reminder is not marked sent.
  - `Scheduler.__init__(..., triage=None, device_push=None)` — `self.device_push = device_push or app_push.send_all`.
  - `Scheduler._reminders`: returns early when `not notify.ready(self.conn, ecfg)` (replaces `notify.enabled`); calls `reminders.fire(self.conn, ecfg, now, self.push, device_push=self.device_push)`; the success summary appends `" · " + "; ".join(errors)` when any item carries errors.
  - `Scheduler._review_push`: the ntfy call is unchanged and happens when `"ntfy" in notify.providers(ecfg)`; when `"app"` is chosen, also `self.device_push(self.conn, ecfg, {"title": <same title>, "body": <same body>, "path": "/review", "tag": "review", "actions": [], "urgency": "normal", "ttl": 14400})`. Each in its own `try`; when every attempted one raised, raise the first (the job retries in an hour); otherwise return `f"{n} items"` plus `" · " + errors` when any.

- [ ] **Step 1: Write the failing tests** under `# --- Task 4: a choice of provider`.

```python
from datetime import date, datetime
from factotum import batches, notify, reminders, config as C
from factotum.db import upsert_task

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
```
(The review summary keeps today's `f"{n} items"` wording, so `"1 items"` is expected.)

- [ ] **Step 2: Run them to see them fail.** `/home/robert/factotum/.venv/bin/pytest tests/test_stage2.py -q` — Expected: FAIL (`notify.providers` missing, `device_push` unexpected keyword).

- [ ] **Step 3: Implement.** `notify.py`: import `app_push` inside `ready` (avoids an import cycle if one appears); module docstring gains the two functions. `config/factotum.example.yaml`, under `notify:` before `ntfy:`: `provider: ntfy                # Settings → Notifications. Where reminders and the evening review nudge go: ntfy (its app and the topic below), app (Factotum's own notifications, on every device where they were turned on in Settings) or both`. Do not add the key to `config/factotum.starter.yaml` (no provider there; ntfy is off, so nothing is sent). `reminders.py` docstring: reminders go to the chosen providers; one token serves both. Scheduler docstring line for `reminders`: "fire due rows (reminders.fire) when a chosen provider can deliver (notify.ready)".

- [ ] **Step 4: Run the tests to see them pass.** `/home/robert/factotum/.venv/bin/pytest tests/test_stage2.py -q`, then `/home/robert/factotum/.venv/bin/pytest -q` — all pass, including `tests/test_stage3.py` (`DEFAULTS` equals `SETTINGS_KEYS`, the example documents every Settings key, no literal default disagrees, a day on the starter sends nothing) and the existing reminder and review-push tests in `tests/test_core.py` and `tests/test_stage1.py`.

- [ ] **Step 5: Commit** (`notify: a choice of provider (ntfy, the app, or both; absent means ntfy, so an existing configuration keeps it): reminders and the evening review nudge go to each chosen one with the same token links, a closed task's reminder has no buttons, one provider failing never holds the other, and nothing fires until a chosen provider can deliver` + trailer).

---

### Task 5: The device routes

Implementer: **sonnet**. UI task: **no**.

**Files:**
- Modify: `factotum/api.py` (docstring, three body models, four routes, `app.state.push_transport`)
- Test: `tests/test_stage2.py`

**Interfaces:**
- Consumes: `app_push.keys`, `subscribe`, `unsubscribe`, `devices`, `send_test`, `KeyFileError` (Tasks 2–3); `notify.provider` (Task 4).
- Produces (routes; every response JSON; never 401, 403 or a redirect):
  - `GET /api/push` → `{"public_key": str | None, "error": str | None, "provider": "ntfy"|"app"|"both", "devices": [{"id", "label", "endpoint", "origin", "created_at", "last_success_at", "last_failure_at", "last_error"}]}`. Calls `keys(conn, create=True)`; a `KeyFileError` gives `public_key: null` and `error: str(e)`. The device rows never carry `p256dh` or `auth`. `provider` is `C.value(eff_cfg(), "notify.provider")`.
  - `POST /api/push/subscribe` body `SubscribeBody{endpoint: str, keys: dict[str, str], label: str = "", origin: str}` (the browser's `PushSubscription.toJSON()` plus `label` and `origin`) → `keys(conn, create=True)` first (`KeyFileError` → 409 `{"detail": str(e)}`); `subscribe(...)` (`ValueError` → 400 `{"detail": str(e)}`; a missing `p256dh` or `auth` in `keys` → 400 `"the device key is not a P-256 public key"` / `"the device secret must be 16 bytes"`); commit; answers what `GET /api/push` answers.
  - `POST /api/push/unsubscribe` body `UnsubscribeBody{endpoint: str | None = None, id: int | None = None}` → neither given → 400 `"name a device"`; deletes, commits (an unknown device is not an error); answers what `GET /api/push` answers.
  - `POST /api/push/test` body `PushTestBody{endpoint: str}` → `send_test(conn, eff_cfg(), endpoint, transport=app.state.push_transport)`; None → 404 `{"detail": "This device is not subscribed"}`; else the outcome `{"id", "ok", "status", "removed", "error"}`.
  - `app.state.push_transport = None` (tests set an `httpx.MockTransport`).

- [ ] **Step 1: Write the failing tests** under `# --- Task 5: the device routes`.

```python
from fastapi.testclient import TestClient
from factotum.api import create_app

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
```

- [ ] **Step 2: Run them to see them fail.** `/home/robert/factotum/.venv/bin/pytest tests/test_stage2.py -q` — Expected: FAIL with 404 / 405 on `/api/push`.

- [ ] **Step 3: Implement** the routes beside `/api/settings` in `create_app`, a helper `push_state() -> dict` that builds the `GET` answer, and the docstring lines: `GET /api/push · POST /api/push/subscribe|unsubscribe|test   this device's notifications: the public key, the devices, a test (stage 2)`.

- [ ] **Step 4: Run the tests to see them pass.** `/home/robert/factotum/.venv/bin/pytest tests/test_stage2.py -q`, then `/home/robert/factotum/.venv/bin/pytest -q` — all pass.

- [ ] **Step 5: Commit** (`api: /api/push (the public key made on first ask, the devices, the provider), subscribe, unsubscribe and a test to this device; a refusal is a 400, 404 or 409, never a 401 or 403; the private key is in no answer` + trailer).

---

### Task 6: The service worker

Implementer: **opus**. UI task: **yes**.

**Files:**
- Create: `factotum/web/sw.js`
- Modify: `factotum/api.py` (the `/sw.js` route), `factotum/web/app.js` (registration, following a click)
- Test: `tests/test_stage2.py`

**Interfaces:**
- Consumes: the payload of Task 3 (the worker reads `notification.title`, `body`, `navigate`, `tag`, `renotify`, `actions[].action|title|navigate`, `data`); stage 1's `setAddress(path, replace)` and `applyAddress(ev)`.
- Produces:
  - `GET /sw.js` — the file's bytes, `Content-Type: text/javascript; charset=utf-8`, `Cache-Control: no-cache, must-revalidate`. Registered with `app.add_api_route` before the static mount. `sw.js` is NOT added to `api.STAMPED`.
  - In `sw.js`, global functions (function declarations at top level, no IIFE, so `worker.evaluate` reaches them):
    - `shape(json) -> {title: string, options: object}` — pure. `title`: a non-empty string `notification.title`, else `'Factotum'`; `options.body`: `notification.body` when a string, else `'Open Factotum'` when the title fell back, else `''`; `options.tag`: the tag when a non-empty string (else no `tag` and no `renotify`); `options.renotify`: true when there is a tag; `options.icon`: `'/static/icon-192.png'`; `options.actions`: at most two entries of `notification.actions` that have string `action`, `title` and `navigate`, as `{action, title}`; `options.data`: `{navigate: <notification.navigate when a string, else '/'>, title, actions: [{action, title, navigate}], task_id: data && data.task_id}`.
    - `handlePush(json) -> Promise` — `self.registration.showNotification(s.title, s.options)`; if that rejects, `showNotification('Factotum', { body: 'Open Factotum', data: { navigate: '/' } })`.
    - `openApp(href) -> Promise` — `href` resolved against `self.location.origin`; when its origin is not the worker's, use `'/'`. `clients.matchAll({ type: 'window', includeUncontrolled: true })`; the first window on the worker's origin gets `postMessage({ type: 'open', path: url.pathname + url.search })` and `focus()` (a rejected focus is ignored); with none, `clients.openWindow(url.href)`.
    - `handleClick(data, action) -> Promise` — no action, or an action not in `data.actions`, or its `navigate` not on the worker's origin → `openApp(data.navigate)`. Otherwise `fetch(a.navigate, { credentials: 'include', redirect: 'manual' })`; when the answer is `ok` (a sign-in redirect is an `opaqueredirect`, never ok), show the confirmation (`'done'` → title `Marked done`, `'snooze'` → `Snoozed for 1 hour`; body `data.title`; same `tag`; `silent: true`; `data: { navigate: data.navigate, title: data.title, actions: [] }`); otherwise, or when `fetch` throws (offline), `openApp(data.navigate)`.
    - Listeners: `install` → `self.skipWaiting()`; `activate` → `event.waitUntil(self.clients.claim())`; `push` → parse `event.data.json()` inside `try` (null on failure) and `event.waitUntil(handlePush(json))`; `notificationclick` → `event.notification.close()`, `event.waitUntil(handleClick(event.notification.data || {}, event.action))`. No other listener.
    - A header comment: notifications only; no fetch handler, ever (it would let the worker store or serve the app and make it stale; design §14); the payload is the declarative format, which Safari shows without this script, so what a notification shows must change in `app_push.payload` and `shape` together.
  - In `app.js` (new section `// ---------- notifications (stage 2) ----------`):
    - `registerWorker() -> Promise<ServiceWorkerRegistration | null>` — when `'serviceWorker' in navigator`: `navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' })`; a rejection is kept in `workerError` (a string) and resolves null. Called once in the start-up chain: `load().then(() => { applyAddress(); registerWorker(); return loadSettings(); })`.
    - `followPath(path)` — only a path matching `^/(t/\d+|[a-z]+)?$`; `setAddress(path); applyAddress({ type: 'worker' })` (on a phone this opens the task page; on a desktop it opens the row; `/review` opens the Review tab).
    - `navigator.serviceWorker.addEventListener('message', e => { if (e.data && e.data.type === 'open' && typeof e.data.path === 'string') followPath(e.data.path); })`, installed only when `'serviceWorker' in navigator`.

- [ ] **Step 1: Write the failing test** under `# --- Task 6: the service worker`.

```python
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
```

- [ ] **Step 2: Run it to see it fail.** `/home/robert/factotum/.venv/bin/pytest tests/test_stage2.py -q -k worker` — Expected: FAIL (404 on `/sw.js`).

- [ ] **Step 3: Implement** `sw.js`, the route and the `app.js` lines above.

- [ ] **Step 4: Run the suite.** `/home/robert/factotum/.venv/bin/pytest -q` — all pass.

- [ ] **Step 5: Verify by rendering.** `<dev>` below is `/tmp/claude-1000/-home-robert-factotum/85fa6951-ca9c-416e-9691-1a412f0fadea/scratchpad/dev`. `<dev>/serve.sh restart`. Scenario `scenarios/s2-6-worker.mjs`:
  1. `await context.grantPermissions(['notifications'], { origin: base })`; open `base + '/'`; wait (10 s) for `navigator.serviceWorker.controller` to be non-null (`page.waitForFunction`); get the worker (`context.serviceWorkers()[0]` or `await context.waitForEvent('serviceworker')`) and log its URL (expected `…/sw.js`). Pick `id` = the first task of `GET /api/tasks`.
  2. `worker.evaluate` → `shape({web_push: 8030, notification: {title: 'Scenario reminder', body: 'before the meeting · due Nov 5', navigate: self.location.origin + '/t/' + id, tag: 'task-' + id, renotify: true, actions: [{action: 'done', title: 'Done', navigate: self.location.origin + '/r/s2-made-up-token?a=done'}, {action: 'snooze', title: 'Snooze 1 h', navigate: self.location.origin + '/r/s2-made-up-token?a=snooze'}]}})`; log it. Expected: that title, the tag, two actions, `data.navigate` ending `/t/<id>`. Then `shape(null)` and `shape({})`: title `Factotum`, body `Open Factotum`.
  3. `worker.evaluate` → `handlePush(<the same payload>)` twice, then `(await self.registration.getNotifications()).map(n => [n.title, n.tag, (n.actions || []).map(a => a.title)])`; log. Expected: ONE notification with that tag (the second replaced the first) and buttons `Done`, `Snooze 1 h`. If the list is empty or `handlePush` rejected, log that headless Chromium shows no notifications and go on (this is a known headless limit; report it).
  4. The expired-sign-in path, real: `worker.evaluate(d => handleClick(d, 'done'), <shape(...).options.data from step 2>)`. The dev server answers the made-up token with 404, so the worker must open the task. Wait 1 s; log `location.pathname`. Expected on the phone: `/t/<id>` and the task page; on the desktop: the task's row opened in its list (the address goes back to the tab's, as stage 1 does for a desktop task address). `shot('s2-6-click-fallback')`.
  5. The same with Access stubbed: `context.route('**/r/**', r => r.fulfill({ status: 302, headers: { location: 'https://login.invalid/' } }))`, go back to `base + '/'`, run step 4 again. Same expectation. Then the success path: route `**/r/**` to `fulfill({ status: 200, contentType: 'text/html', body: '<p>ok</p>' })`; run `handleClick(data, 'done')`; log `location.pathname` (expected unchanged) and the notifications (expected one titled `Marked done` with the task's title as body, if headless shows notifications at all). If `context.route` does not see the worker's request (the dev server's 404 then decides), say so in the report: the success path is then unverified headless.
  6. Look at both screenshots on both devices; report the logged lines; no console error, no failed request except the 404 for the made-up token and the stubbed 302.

- [ ] **Step 6: Commit** (`web: a service worker for notifications only (no fetch handler, ever; served at /sw.js with no-cache, active at once): it shows the declarative payload where the browser does not, a tap opens the task in an open window or a new one, Done and Snooze call the reminder's token link and open the task instead when that does not succeed (an expired sign-in, an error, offline)` + trailer).

---

### Task 7: Settings → Notifications

Implementer: **opus**. UI task: **yes**.

**Files:**
- Modify: `factotum/web/app.js` (the notifications section, `renderSettings`, `wire()`'s Settings handlers, `render()`'s Review-at pill), `factotum/web/app.css`

**Interfaces:**
- Consumes: `GET /api/push`, `POST /api/push/subscribe|unsubscribe|test` (Task 5); `registerWorker()`, `workerError` (Task 6); stage 3's Settings machinery (`settingsDirty`, `controlValue`, `saveSettings`, a `[data-key]` control saves its one key; `bool(key)`), `settings.effective['notify.provider']` (Task 4).
- Produces (in the notifications section of `app.js`):
  - `let pushInfo = null;` (the `GET /api/push` answer) and `let pushDev = { sub: null, busy: false, reason: '' };` (the browser's `PushSubscription`, a call in flight, a refusal from the last attempt)
  - `isIOS() -> boolean` (`/iPhone|iPad|iPod/` in the user agent, or `navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1`); `installed() -> boolean` (`matchMedia('(display-mode: standalone)').matches || navigator.standalone === true`)
  - `pushSupport() -> 'install' | 'unsupported' | 'ok'` — `'install'` when `isIOS() && !installed()`; `'unsupported'` when `serviceWorker`, `PushManager` or `Notification` is missing; else `'ok'`
  - `deviceLabel() -> string` — `iPhone`, `iPad`, `<Browser> on Android`, or `<Browser> on <Windows|Mac|Linux|ChromeOS>` with Browser one of `Edge` (`Edg/`), `Firefox`, `Chrome`, `Safari`; `This browser` when nothing matches
  - `b64uBytes(s) -> Uint8Array`; `sameKey(sub, publicKey) -> boolean` (compares `sub.options.applicationServerKey` with the server's key)
  - `loadPush() -> Promise` — `pushInfo = await api('/api/push')`; when supported, `pushDev.sub = await (await navigator.serviceWorker.ready).pushManager.getSubscription()` (only when a registration exists, so it never waits forever); `render()` when on Settings. Called by `renderSettings` when `pushInfo === null` (guarded against re-entry), at start-up (Task 6's chain becomes `load().then(() => { applyAddress(); registerWorker(); return loadSettings(); }).then(loadPush)`, so the header pill knows the devices), and after `load()` in `onReturn`'s reload path.
  - `pushView() -> { state, reason, row }` — states exactly: `'working'` (busy), `'install'`, `'unsupported'`, `'loading'` (no `pushInfo`), `'server'` (`pushInfo.error`), `'denied'` (`Notification.permission === 'denied'`), `'refused'` (`pushDev.reason`, or this device's row has `last_failure_at` later than `last_success_at`: reason = `row.last_error`), `'off'` (no subscription, a subscription made with another key, or no row with its endpoint), `'on'`.
  - `pushOn()`, `pushOff()`, `pushTest()`, `pushRemove(id)` — the button handlers below.
  - `reviewPushOn() -> boolean` — the review nudge has somewhere to go: (`notify.provider` is `ntfy` or `both`, and `notify.ntfy.enabled` and `notify.ntfy.topic`) or (`app` or `both`, and `pushInfo` lists at least one device).

- [ ] **Step 1: Implement the Settings section.** In `renderSettings`, a new `<h3>Notifications</h3>` directly after the "Review and lifecycle" fields, holding, in order:
  1. `f('notify.provider', 'Send reminders to', 'Reminders and the evening review nudge go here. ntfy needs its app and a topic; The app needs notifications turned on below, on each device.', <select id="s-notify.provider" data-key="notify.provider">)` with options `ntfy` → `ntfy`, `app` → `The app`, `both` → `Both`, the effective value selected. It saves through stage 3's one-key save like every `[data-key]` control.
  2. A field `<div class="field" id="push-device" data-state="<state>">` labelled `This device` whose control is, per state (exact strings):
     - `loading`: `Checking this device…`
     - `unsupported`: `<b>This browser cannot show notifications from Factotum.</b> Use Chrome, Edge or Firefox on a computer or an Android phone, or the installed app on an iPhone.`
     - `install`: `<b>On an iPhone, notifications work only in the installed app.</b> In Safari tap Share, then Add to Home Screen, and open Factotum from the Home Screen.`
     - `denied`: `<b>Notifications are blocked for Factotum on this device.</b> Allow them in the browser's site settings (on an iPhone: Settings › Notifications › Factotum), then come back here.`
     - `server`: `<b>Factotum cannot send notifications right now:</b> <error>`
     - `off`: button `#push-on` `Turn on notifications on this device` (class `btn primary`) and the hint `Reminders and the evening review nudge will appear here and open the task they are about.`
     - `working`: the same button, disabled, reading `Turning on…`
     - `on`: `<b>Notifications are on for this device.</b>` and buttons `#push-test` `Send a test` and `#push-off` `Turn off`; when `notify.provider` is `ntfy`, a hint `Reminders go to ntfy only at the moment. Choose The app or Both in "Send reminders to" to get them here.`
     - `refused`: `<b>The push service refused:</b> <reason>` and buttons `#push-on` `Try again` and, when this device has a subscription, `#push-off` `Turn off`.
     (`<error>` and `<reason>` escaped with `esc`.)
  3. `Devices with notifications on`: a table (the connectors table's look) with columns `device`, `added`, `last sent`, `last failure`, and a last column; one row per `pushInfo.devices` entry: the label plus ` (this device)` for the one whose endpoint is `pushDev.sub.endpoint`; `created_at` as a date; `last_success_at` as `Nov 10 19:30` or `—`; `last_error` only when its failure is later than the last success, else `—`; a `Remove` button `data-push-remove="<id>"` on every row but this device's. With no device: `<p class="hint">No device has notifications on yet.</p>`.
  4. The `ntfy push` field (moved here from Outputs, unchanged control, sub-text `Reminders and the review nudge through the ntfy app, when "Send reminders to" includes ntfy.`).
  The "Review reminder" field's sub-text becomes `The evening review nudge, sent where reminders go. Quiet if you already reviewed today.`
- [ ] **Step 2: Implement the handlers** (wired in `wire()` on the Settings tab):
  - `pushOn()`: its FIRST statement is `const asked = Notification.permission === 'granted' ? Promise.resolve('granted') : Notification.requestPermission();` (no `await` before it: iOS only asks from a tap). Then `pushDev.busy = true; pushDev.reason = ''; render();`. Permission `denied` → `render()` (the denied state); `default` (dismissed) → toast `Notifications were not allowed`. Then the registration (`registerWorker()` if none, else `navigator.serviceWorker.ready`); a subscription made with another key is unsubscribed first; `pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64uBytes(pushInfo.public_key) })`; `POST /api/push/subscribe` with `Object.assign(sub.toJSON(), { label: deviceLabel(), origin: location.origin })`; `pushInfo` = the answer; toast `Notifications are on for this device`. A rejection from `subscribe` sets `pushDev.reason` to its `message` (or `name`); a failed `POST` sets it to `Factotum did not take the subscription: <message>`. Always `pushDev.busy = false; render()` at the end.
  - `pushTest()`: `POST /api/push/test {endpoint}`; `ok` → toast `Test sent · it should appear in a few seconds`; `removed` → `pushDev.reason = 'it no longer knows this device. Turn notifications on again.'`; otherwise `pushDev.reason = <error>`; reload `pushInfo` with `loadPush()`.
  - `pushOff()`: `POST /api/push/unsubscribe {endpoint}` then `sub.unsubscribe()` (a failure of the second is ignored), `pushDev.sub = null`, `pushDev.reason = ''`, toast `Notifications are off for this device`.
  - `pushRemove(id)`: `POST /api/push/unsubscribe {id}`, toast `Device removed`.
- [ ] **Step 3: The header's review pill.** Where `render()` shows `Review at HH:MM` (stage 3 shows it only when ntfy can send), show it when `reviewPushOn()`.
- [ ] **Step 4: CSS.** `#push-device` buttons wrap and stay full thumb size at 390 px; the devices table scrolls inside its own box (`overflow-x: auto` on a wrapper) rather than the page.
- [ ] **Step 5: Check the suite.** `/home/robert/factotum/.venv/bin/pytest -q` — all pass.
- [ ] **Step 6: Verify by rendering.** `<dev>/serve.sh restart`. Two scenarios, both devices each, every screenshot looked at:
  - `scenarios/s2-7-onoff.mjs` (the real path with a stubbed push service). `context.grantPermissions(['notifications'], { origin: base })`. On the phone only, `page.addInitScript(() => Object.defineProperty(navigator, 'standalone', { get: () => true }))` (stands for the installed app). `page.addInitScript` replaces `PushManager.prototype.subscribe` and `getSubscription` with a fake that keeps one subscription per page: endpoint `https://push.invalid/s2-<random>`, keys from `crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits'])` exported `raw` (65 bytes) and 16 random bytes, `options.applicationServerKey` = the key it was given, `toJSON()` in the browser's shape, `unsubscribe()` clearing it. Open `base + '/settings'`, wait for `#push-device`; log `data-state` (expected `off`), `shot('s2-7-off')`. Click `#push-on`; wait for `data-state="on"`; `shot('s2-7-on')`; log the devices table's rows (this device marked `(this device)`) and that the ntfy-only hint shows (the dev provider is `ntfy`). Set "Send reminders to" to `The app`, wait 1 s, `GET /api/settings` → log `effective['notify.provider']` (expected `app`) and that the hint is gone. Click `#push-test`: the server tries `push.invalid`, which never resolves, so expected `data-state="refused"` with `could not reach push.invalid: ConnectError` (or another `httpx` error name) — this IS the refused state, reached for real; `shot('s2-7-refused')`. Click `#push-off`: `data-state="off"`, the device gone from the table; `shot('s2-7-off-again')`. Put "Send reminders to" back to `ntfy`. Log `document.documentElement.scrollWidth <= innerWidth` (expected true).
  - `scenarios/s2-7-states.mjs` (the states a headless browser cannot reach, each STUBBED, each in a fresh `context.newPage()` with its own `addInitScript`, opening `base + '/settings'` and shooting `#push-device`'s field): `s2-7-install` (phone only, no stub: Chromium with an iPhone user agent and no standalone is that state for real; on the desktop skip it); `s2-7-unsupported` (`delete window.PushManager`); `s2-7-denied` (`Object.defineProperty(Notification, 'permission', { get: () => 'denied' })`, plus standalone on the phone); `s2-7-server` (`page.route('**/api/push', r => r.fulfill({ json: { public_key: null, error: 'the notification key file /x/push-keys.json is unreadable', provider: 'ntfy', devices: [] } }))`); `s2-7-browser-refused` (the fake `subscribe` rejects with `new DOMException('Registration failed - push service error', 'AbortError')`; click `#push-on`; expected `The push service refused: Registration failed - push service error`). Also open `base + '/'` in one page and log whether the `Review at` pill shows (expected: as before on the dev copy, which has ntfy on with a topic, or not when it has none; report which).
  Report, per screenshot, the state seen and whether it was real or stubbed; nothing scrolls sideways at 390 px; every button is reachable with a thumb.
- [ ] **Step 7: Commit** (`web: Settings → Notifications: where reminders go (ntfy, the app, both), "Turn on notifications on this device" with the permission asked from the tap, every state named (cannot, install the app first on an iPhone, blocked, off, on with Send a test and Turn off, the push service refused and why, Factotum cannot send), the devices with Remove; the review pill shows when the nudge can reach somewhere` + trailer).

---

### Task 8: The stage's documents

Implementer: **opus**. UI task: **no**.

**Files:**
- Modify: `SPEC.md`, `CLAUDE.md`, `docs/SETUP.md`

**Interfaces:**
- Consumes: every name above (`webpush.encrypt`, `vapid_header`, `MAX_PLAINTEXT`; `app_push.keys`, `KEY_FILE`, `payload`, `send_all`, `send_test`, `NothingSent`, `KeyFileError`; `notify.providers`, `ready`; `reminders.notice`; `Scheduler.device_push`; `/api/push*`; `/sw.js`, `shape`, `handlePush`, `handleClick`, `openApp`; `pushView`, `pushOn`, `reviewPushOn`).

- [ ] **Step 1: `SPEC.md`.** §4: the `push_subscriptions` table, one line per column, and that the key pair is a file beside the database, not a table. §9 (Settings): the Notifications section and its states. §10 "Reminders and push": providers (`notify.provider`; absent means ntfy; the starter sends nothing); the app's notifications (the declarative payload and why there is no `mutable`; links on each device's own origin; tag and Topic per task; TTL and urgency; Done and Snooze call the same token links as ntfy, which stay behind the sign-in, and a worker that cannot reach one opens the task; no buttons on a closed task's reminder); the worker (notifications only, no fetch handler, `/sw.js`, no-cache, not stamped); delivery semantics (a 404 or 410 removes a device; one provider failing never holds the other; nothing fires until a chosen provider can deliver). §13 "Cost and safety": push costs nothing; the private key never leaves the server and a new one empties the devices; the payload is encrypted end to end, so the push service cannot read a task's name or its token links (ntfy's topic readers can).
- [ ] **Step 2: `CLAUDE.md`.** Phase status: one sentence for stage 2 (2026-11-10): notifications from the app (`factotum/webpush.py`, `factotum/app_push.py`, `web/sw.js`, Settings → Notifications, `notify.provider`). Where things live, one row: "notifications from the app: the key pair, devices, payload, sending, the worker, Settings → Notifications" → design §14, SPEC §10 → `factotum/webpush.py` (`encrypt`, `vapid_header`, `send`), `factotum/app_push.py` (`keys`, `subscribe`, `payload`, `send_all`, `send_test`), `notify.py` (`providers`, `ready`), `reminders.py` (`notice`), `api.py` (`/api/push*`, `/sw.js`), `web/sw.js` (`shape`, `handlePush`, `handleClick`, `openApp`), `web/app.js` (`pushView`, `pushOn`, `followPath`, `reviewPushOn`). Conventions, each dated 2026-11-10:
  - **The service worker has no fetch handler, ever**: it would let the worker store or serve the app and make it stale. `/sw.js` is served from its own route with no-cache and is not in `api.STAMPED` (the browser re-checks a worker on each navigation; a worker change must not reload open pages).
  - **The push key pair is `push-keys.json` beside the database** (0600, never in git, never in an `/api` answer but its public half). Only `GET /api/push` and subscribe make one, and a new pair empties `push_subscriptions` (every device was bound to the old key). The scheduler never makes one: missing or unreadable at send time is a `KeyFileError`, nothing sent, the reminder waits, the Log says why. An unreadable file is never overwritten; fix or delete it by hand.
  - **A notification's look is defined twice**: Safari shows the declarative payload (`app_push.payload`) without running the worker; other browsers run `sw.js` `shape()` on the same JSON. Change both together. No `mutable` member: with it Safari would run the worker and wait on it.
  - **`app_push.send_all` makes every network call before its first write, then commits the device rows itself** (a 410 removal must survive the caller's rollback) and raises `NothingSent` when no device took it.
  - **The payload is at most 3993 bytes of plaintext** (`webpush.MAX_PLAINTEXT`, one 4096-byte record); `payload()` trims the body, then the title, never a link.
  - **The permission is asked first thing in the tap handler** (`pushOn`): an `await` before `Notification.requestPermission()` makes iOS refuse it.
  - **Notification buttons use the reminder's token links, which stay behind the sign-in**: never add `/r/*` or `/sw.js` to an Access bypass; a button that gets a redirect, an error or no network opens the task page.
  - **A device's links are built on its own origin** (`push_subscriptions.origin`), so the dev instance's test notifications open the dev instance.
- [ ] **Step 3: `docs/SETUP.md`.** A new last section, "Notifications from the app" (numbered after the current last section):
  - Turning them on: on a desktop (Chrome, Edge, Firefox): Settings → Notifications → "Turn on notifications on this device", allow, "Send a test". On an iPhone (iOS 16.4 or later; the declarative format needs 18.4): install the app first (the "Installing the app" section), open it from the Home Screen, then the same; the permission prompt appears only from that tap. Android: as a desktop (not tested). Each device is turned on separately and listed under "Devices with notifications on".
  - Choosing where reminders go: "Send reminders to" (ntfy, The app, Both). ntfy stays available and unchanged; an existing setup keeps ntfy until this is changed. Moving fully to the app: choose The app, check a test arrives on each device, then the ntfy app may be removed.
  - Where the key lives (`push-keys.json` beside the database, back it up with the database; deleting it means turning notifications on again on every device), and that nothing goes in `.env`.
  - What was verified and what was not: verified offline — the encryption against RFC 8291's own test vector, VAPID signatures, sending, removal on 404/410, trimming, the provider choice, the routes; verified in a headless browser — the worker registers and controls the app, its payload handling, a button press that cannot reach its link opening the task, every Settings state (some stubbed: unsupported, blocked, Factotum cannot send, a refusal from the browser). Not verified: a real subscription with a push service, any real delivery, Apple's acceptance of the VAPID claims, the declarative display on an iPhone, the notification icon behind the sign-in, Android.
  - For the owner to check on real devices (a checklist): (1) iPhone, installed app: turn on, the prompt appears, "Send a test" arrives within seconds; tapping it opens Settings in the app. (2) iPhone, a real reminder: the task's name and body, a tap opens the task page, not Today; no buttons (or, if iOS shows them, Done opens the "marked done" page). (3) iPhone in a Safari tab: the "install first" line. (4) Desktop Edge or Chrome: turn on, test; a reminder shows Done and Snooze 1 h; Done closes the task and the notification becomes "Marked done"; a second reminder for the same task replaces the first. (5) After the sign-in has expired (or in a private window signed out), Done opens the task page and "Sign in again" shows. (6) Two devices both get a reminder; Remove on one stops it there only. (7) "Both": ntfy behaves exactly as before and the app also gets it. (8) The review nudge opens Review. (9) The icon on the notification (it sits behind the sign-in and may show a generic one). (10) Delete the app from the iPhone: after the next reminder its row is gone from the devices list.
  - The "Installing the app" section's line "Notifications come in stage 2" becomes a pointer to the new section; §4 (ntfy) gains one line: ntfy is one of the choices in "Send reminders to".
- [ ] **Step 4: Check.** `/home/robert/factotum/.venv/bin/pytest -q` — all pass. `grep -n "push-keys.json" CLAUDE.md SPEC.md docs/SETUP.md .gitignore` lists all four files.
- [ ] **Step 5: Commit** (`docs (stage 2): SPEC §4, §9, §10, §13; CLAUDE.md phase status, table row and the 2026-11-10 gotchas (no fetch handler, the key file, a notification defined twice, send_all's order, the payload limit, the permission from the tap, buttons behind the sign-in, a device's own origin); SETUP: notifications from the app, what was verified, the owner's checks on real devices` + trailer).
