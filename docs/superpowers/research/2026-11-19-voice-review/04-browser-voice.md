# 04 — What a page can do with voice (November 2026)

Research for a spoken Review session in Factotum. Research only: nothing in the repository was changed,
nothing was tested on a device. Date of research: 2026-11-19.

**Confidence marks used throughout**

- **[V]** verified today: read today in a primary source (MDN or its browser-compat-data, an Apple / Chrome /
  Mozilla / Microsoft release note or intent, a WebKit / Mozilla bug page). Still not tried on a device.
- **[D]** documented but untested: read today in a secondary source (developer write-up, forum, a third-party PR).
- **[R]** recalled from memory, not re-checked today.

Platform versions current today [V]: Chrome / Edge ~150–151 desktop (Chrome 150 ships the on-device
quality hint; MDN lists `unspokenPunctuation` at 151), Firefox 160 (released 2026-11-10), Safari 27 / iOS 27
(released 2026-09-14; 27.3 in beta). The owner's iPhone is very likely on iOS 27.x — the version where
Safari's speech recognition has a fresh, unfixed regression (see §1.4).

---

## 0. The short version

1. **Browser speech recognition is not a foundation on iPhone.** It exists in Safari and (now) in the
   installed web app, but on iOS 27 it goes deaf after the first session until the app is force-quit
   (WebKit bug 321436, open, no engineer reply, last touched 2026-11-18) [V]; it cannot run alongside
   `getUserMedia` [D]; it stops after audio plays [V/D]; it needs Siri/Dictation on [D]; and audio goes to
   Apple's service. Android Chrome's is cloud-only and ignores `continuous` [V]. Firefox Android has none [V].
2. **Microphone capture works everywhere** (`getUserMedia` + Web Audio / `AudioWorklet`, `MediaRecorder`)
   [V], so the one path that works on all six targets is: **capture in the page → detect the end of an
   utterance in the page → send the utterance to Factotum's server with `fetch` → server-side
   transcription → server-made (or browser) speech played back through one audio path unlocked by the
   Start tap.**
3. **Where more is available**, browser on-device recognition is excellent and free: Chrome desktop
   (`processLocally`, Chrome 139+; quality levels Chrome 150), Firefox desktop 160 (on-device only),
   Edge (on-device behind a flag in Canary/Dev 150) [V]. Use it as an optional engine, chosen by
   `SpeechRecognition.available()`, never as the only engine.
4. **Speech output**: `speechSynthesis` works everywhere [V] but iPhone exposes only its pre-installed
   compact voices — not Siri or downloaded voices [D] — and Chrome desktop's Google voices cut off at
   ~14–15 s [D]. Server-made audio played through one Web Audio context (or one reused `<audio>`) is
   the better-sounding and more predictable path; `speechSynthesis` is the zero-setup fallback.
5. **Hands-free on a phone is foreground-only.** Screen Wake Lock now works in every target including
   the installed iOS app (fixed in iOS 18.4) [V], so the screen can stay on while cooking. If the phone
   locks or the app is backgrounded, iOS stops audio in a standalone app and mutes/ends the mic track
   [D]; the session must pause and resume on `visibilitychange`.
6. **Echo:** the browser's echo canceller only reliably removes audio the page itself plays through the
   browser's audio pipeline (an `<audio>` element / Web Audio) [R]; `speechSynthesis` on iOS actively
   disturbs a live mic [D]. Design for **half-duplex** (don't listen while speaking; barge-in by a tap,
   a headset button or — on desktop / with earbuds — by voice), and treat full voice barge-in on the
   phone speaker as a stretch goal to be proven on a real iPhone.

---

## 1. Speech recognition in the browser (Web Speech API `SpeechRecognition`)

### 1.1 Availability (MDN browser-compat-data, fetched 2026-11-19, repo HEAD 2026-11-19T17:52Z) [V]

| Feature | Chrome desktop | Edge | Firefox desktop | Safari macOS | Chrome Android | Safari iOS |
|---|---|---|---|---|---|---|
| `SpeechRecognition` | 139 unprefixed (33 `webkit`) | mirrors Chrome | **160** | 14.1 (`webkit` only) | yes (mirrors Chrome) | mirrors Safari (14.5) |
| `continuous` | 33 | yes | 160 | 17 (14.1–17 partial) | **No — "can be set, but has no effect"** | mirrors Safari |
| `interimResults` | 33 | yes | 160 | 14.1 | yes | yes |
| `processLocally` | 139 | yes* | 160 | No | **No** | No |
| `available()` / `install()` | 139 | yes* | 160 | No | **No** | No |
| `phrases` (contextual biasing) | 142 | yes | 160, *set but no effect* | No | **No** | No |
| `start(audioTrack)` | 135 | yes | 160 | No | **No** | No |
| `speechstart` / `speechend` events | yes | yes | 160, *never fired* | yes | yes | yes |

\* Edge "mirrors" Chrome in the data, but Microsoft's own page (below) says Edge's local model is behind a
flag in Canary/Dev.

Sources: <https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition> (modified 2026-11-16);
<https://github.com/mdn/browser-compat-data/blob/main/api/SpeechRecognition.json>.

### 1.2 Where the audio goes [V]

- MDN (2026-11-16): "On some browsers, speech recognition uses a server-based recognition engine by
  default. Your audio is sent to a web service … recognition won't work offline unless you require
  on-device recognition using `processLocally`."
  <https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API/Using_the_Web_Speech_API>
- **Chrome desktop / Android:** default is Google's cloud service [R]. With `processLocally = true`
  (desktop only) audio stays on the device after a one-time language-pack download (`install()`); a
  missing pack makes `start()` fail with `service-not-allowed` [V, MDN]. `available()` / `install()` are
  gated by the `on-device-speech-recognition` Permissions-Policy, default `self` [V, MDN].
- **Chrome 150 (desktop):** a `quality` option (`command` / `dictation` / `conversation`) on
  `available()` / `install()` lets a page ask whether the local model is good enough and otherwise fall
  back to the cloud. Intent to Ship posted 2026-05-05; "On Android and ChromeOS … on-device Web Speech
  is not currently supported." [V]
  <https://groups.google.com/a/chromium.org/g/blink-dev/c/P8P-x7AnC6I>
- **Edge:** default recognition is Microsoft's cloud [R]. Local model: "available in Microsoft Edge
  Canary or Dev (version 150.0.4076 or later)" behind `edge://flags` → *Speech Recognition with on-device
  model*; languages en-US, de-DE, it-IT, pt-PT, es-ES, ko-KR (page dated 2026-06-01, updated
  2026-11-18) [V]. <https://learn.microsoft.com/en-us/microsoft-edge/web-platform/speech-recognition-api>
- **Firefox 160 desktop (released 2026-11-10):** "On-device speech recognition using the Web Speech API
  is now available on desktop … in 40 languages. Recognition is not supported on Android, and neither is
  remote (cloud) recognition." (bug 2080908) [V]
  <https://developer.mozilla.org/en-US/docs/Mozilla/Firefox/Releases/160>
- **Safari (macOS / iOS):** uses Apple's Siri/Dictation speech service; works only with Siri or
  Dictation enabled in system settings (WebKit engineer, bug 225298, 2021) [D/V]. Whether a given
  utterance is processed on device or on Apple's servers is not documented for the web API — unknown.

### 1.3 Gesture, time limits, continuous mode

- `start()` is called from a click in every MDN example; MDN does not state a hard gesture requirement
  [V]. In practice Safari requires a user gesture for the first start and shows the mic permission
  prompt [R]. Chrome shows its own mic prompt; Chrome Android plays a start/stop beep [R].
- `continuous`: ignored on Chrome Android [V]; on Safari ≤ 16 it returned multiple results when false
  [V]. Chrome desktop's cloud recognizer ends a continuous session after a stretch of silence or about a
  minute and the page must restart it in `onend` [R]. Edge's local model "might also stop automatically
  after a long period of silence" [V].
- Firefox 160 never fires `speechstart` / `speechend` [V] — end-of-utterance logic must not depend on them.

### 1.4 The iPhone picture, in detail

- **Installed web app:** In 2021 a WebKit engineer wrote "SpeechRecognition API is not available in
  SafariViewController and web apps added to Home Screen for now" (bug 225298, RESOLVED LATER; last
  comment 2025-03-01 asking for news) [V] <https://bugs.webkit.org/show_bug.cgi?id=225298>. A
  third-party PR of 2026-10-26 still assumed that, untested on hardware [D]
  <https://github.com/profullstack/qryptchat-web/pull/323>. **But** WebKit bug 321436 (filed
  2026-08-10, still NEW, modified 2026-11-18) has several reporters running recognition **in
  standalone Home Screen apps** (`display-mode: standalone` true) on iOS 26.6 and 27.x, so the API does
  exist there now; the exact iOS version where it arrived is not documented [V]
  <https://bugs.webkit.org/show_bug.cgi?id=321436>.
- **iOS 27 regression (bug 321436) [V]:** "The first dictation after launch always works." Later sessions
  get `start` and `audiostart` but never `speechstart` or `result`, with no `error` or `end`. "Bug was
  introduced with iOS 27" (worked on 26.6.1); reproduced on iOS 27.0–27.3 beta 1, iPadOS 27.2.1, in
  Safari tabs, Chrome on iOS and Home Screen apps, even with no media on the page. "Only force-quitting
  the browser (or the Home Screen app) … restores one working session." Reported workaround: speak a
  `speechSynthesis` phrase before each session, use `continuous = false`, and never cut a session with
  `stop()` / `abort()`. No WebKit engineer has replied.
- **Silent failure after audio plays (iOS 18.7–26.6) [V/D]:** after an `<audio>` element finishes,
  the next `start()` produces nothing, silently; waiting 10–15 s sometimes helps; switching TTS from
  `HTMLAudioElement` to Web Audio avoided it for one developer. A related WebKit fix ("Speech recognition
  microphone source should make sure to keep its audio session active while capturing", bug 317741,
  fixed on main 2026-06-26) [V] — whether it is in iOS 27.x is unknown, and 27 regressed anyway.
  <https://github.com/WebAudio/web-speech-api/issues/96>, <https://bugs.webkit.org/show_bug.cgi?id=317741>
- **Cannot coexist with `getUserMedia`** ("Web Speech recognition and getUserMedia cannot coexist";
  `speechSynthesis` playback blocks recognizer input, so voice barge-in fails), and setting
  `navigator.audioSession.type = "play-and-record"` "made every dictation deaf" — bug 321436 comments
  2–3 [D, reporters' statements on a primary tracker].
- Safari 26 made `SpeechRecognition` secure-context only (fine: Factotum is HTTPS) [V]
  <https://developer.apple.com/documentation/safari-release-notes/safari-26-release-notes.md>.

**Verdict for Factotum:** usable only as an opportunistic engine on Chrome / Firefox / Edge desktop
with `processLocally`; never on iPhone; on Android only if the owner accepts Google's cloud (a config
switch, off by default — it sends audio to an outside service).

---

## 2. Speech synthesis

### 2.1 `speechSynthesis` availability [V]

Supported everywhere since long ago (Chrome 33, Firefox 49, Safari 7; `voiceschanged` in Safari only from
16). Chrome Android: "`pause()` ends the current utterance … behaves the same as `cancel()`." Safari 27
fixed `cancel()` removing utterances queued by later `speak()` calls; Safari 26 fixed pending utterances
getting no `error` on cancel. <https://github.com/mdn/browser-compat-data/blob/main/api/SpeechSynthesis.json>,
<https://developer.apple.com/documentation/safari-release-notes/safari-27-release-notes.md>

### 2.2 Gesture requirement

- Chrome since M71: `speak()` fires a `not-allowed` error unless the frame "has ever had user activation"
  (sticky activation) [V, blink-dev intent] <https://groups.google.com/a/chromium.org/g/blink-dev/c/WsnBm53M4Pc>.
  So the Start tap is enough for the whole page life.
- Safari / iOS: the first `speak()` must come from a gesture; after that later utterances in the same page
  generally work [R]. A long-standing hack is to speak an empty utterance inside the Start tap [R].
- Some engines don't throw on refusal; check `onerror` and `speaking` [D].

### 2.3 Voice quality per platform (Readium's survey, data April 2024, no page date) [D]

<https://readium.org/speech/docs/WebSpeech.html>

- **iPhone / Safari:** only pre-installed voices; "Downloadable voices don't appear in the API"; Siri
  voices unavailable; every voice reports `default: true`; only one selectable voice per locale [D].
  iOS 18 removed some compact voices on real devices (Apple forum, Sept 2024) [D]
  <https://developer.apple.com/forums/thread/764438>. Expect robotic-to-OK English.
- **Edge desktop:** 250+ cloud "Natural" voices, the best free browser TTS; online-only. Edge Android
  returns an empty voice list ("making the API unusable") [D]. Edge 150 briefly returned "undefined"
  voice names (fixed in 150.0.4078.99) [D].
- **Chrome desktop:** ~19 Google cloud voices + OS voices; Google voices **cut off after ~14–15 s** with
  no `end` event; workaround: one sentence per utterance [D]
  (<https://github.com/phetsims/utterance-queue/issues/60>).
- **Android Chrome:** system TTS voices, good quality, varies by OEM [D].
- **Firefox desktop:** OS voices only [D].

### 2.4 iOS quirks [D unless noted]

- **Ringer / silent switch:** reports conflict — some say `speechSynthesis` is silent with the switch on
  silent, others that it ignores the switch. **Web Audio is muted by the silent switch**; an `<audio>`
  element plays regardless (audjust, 2024-02-13) <https://www.audjust.com/blog/unmute-web-audio-on-ios>.
  Setting `navigator.audioSession.type = 'playback'` (Safari 16.4+) is reported to put Web Audio on the
  media channel so it ignores the switch [R]. Must be checked on a real iPhone.
- `speechSynthesis` "aggressive ducking" of other audio (WebKit 278598) and interrupting active
  `getUserMedia` streams (WebKit 218815) are on file [D].
- Long text: chunk by sentence everywhere; it also lets the page highlight and lets a barge-in stop
  at a sentence boundary.

### 2.5 Playing server-made audio instead

- **Autoplay:** an `<audio>.play()` or `AudioContext.resume()` outside a gesture is refused until the page
  has been "unlocked" by one inside a gesture [R]. Standard pattern: in the Start tap, create one
  `AudioContext` and `resume()` it (and/or `play()` a short silent clip on one `<audio>` element you then
  reuse for every reply). After that the page can play whenever it likes while it stays in the foreground [R].
- **Web Audio vs `<audio>` on iOS:** Web Audio avoided the "recognition dies after `<audio>` plays" bug
  [D]; `<audio>` survives the silent switch, Web Audio may not unless the audio session type is
  `playback` [D/R]. Safari 27 fixed `HTMLMediaElement.volume` being ignored when routed through an
  `AudioContext` [V].
- **Streaming:** chunks can be played as they arrive by decoding each (`decodeAudioData` on whole
  MP3/Opus/WAV pieces, e.g. one per sentence) and scheduling `AudioBufferSourceNode`s back to back [R];
  Media Source Extensions are another route but not on every iPhone context [R]. Sentence-sized
  requests are the simplest stream: speak sentence 1 while sentence 2 is generated.
- Safari 27 fixed MP4-with-Opus not decoding in `decodeAudioData` [V]; MP3 or WAV are the safest formats
  to send to the page [R].

---

## 3. Capturing the microphone for server-side recognition

### 3.1 `getUserMedia` [V unless noted]

Supported everywhere (Chrome 53, Firefox 36, Safari 11, iOS mirrors Safari).
<https://github.com/mdn/browser-compat-data/blob/main/api/MediaDevices.json>

iPhone, installed app:

- **Permission persistence:** historically the prompt reappeared in standalone mode on hash changes and
  was not kept across launches (WebKit 215884) [D]. Safari 18.4 fixed "voice search to not re-prompt for
  camera or microphone permission after a page-initiated same origin navigation" and
  "`MediaSession.setMicrophoneActive(true)` prompting repeatedly" [V]
  <https://developer.apple.com/documentation/safari-release-notes/safari-18_4-release-notes.md>.
  Whether a 2026 standalone app keeps the grant across launches: **unknown — device test.**
  Factotum changes its address with `history.pushState` (real paths, not hashes — CLAUDE.md "Tab and task
  addresses are real paths"), which should not count as a navigation [R].
- **Backgrounding:** the audio track goes `ended` after > 30 s in the background, more often stays
  OS-`muted` or silently broken; an incoming call mutes it and it never unmutes; re-call
  `getUserMedia` on `visibilitychange` → visible when the track is `muted` or `ended` [D].
- **Media-services reset:** after an `audiomxd` reset `getUserMedia` audio failed with `NotAllowedError`
  until Safari was relaunched (WebKit 319706, ~20 % of one site's iPhone visits on iOS 26.5; fixed on
  main 2026-07-31, backported to the Safari 26 branch 2026-09-08) [V]
  <https://bugs.webkit.org/show_bug.cgi?id=319706>.
- Safari 27: "Fixed the WebProcess `AudioSession` to remain active while microphone capture is live" [V].
- **Playback gets quieter / moves to the earpiece** while the mic is open, because the audio session
  becomes `play-and-record`; workarounds with `navigator.audioSession.type` are reported to work for some
  and not others [D]. Audio Session API: Safari 16.4+ only (`type`), types `auto`, `playback`,
  `transient`, `transient-solo`, `ambient`, `play-and-record` [V]
  <https://developer.mozilla.org/en-US/docs/Web/API/Audio_Session_API>.

### 3.2 `MediaRecorder` formats [V unless noted]

- Chrome / Edge / Firefox: `audio/webm;codecs=opus` [R]; Chrome also records MP4 since ~126 [R].
- Safari / iOS: `audio/mp4` (AAC) since 14.x [R]; **WebM (Opus) since Safari 18.4** ("Added WebM support
  to MediaRecorder") [V]; ALAC and PCM since Safari 26 [V].
- Pick with `MediaRecorder.isTypeSupported()` in order `audio/webm;codecs=opus`, `audio/mp4`; the server
  must accept both (ffmpeg decodes both; a third-party test found Whisper-on-Workers accepted both) [D].

### 3.3 `AudioWorklet` for raw PCM [V]

`AudioWorkletNode`: Chrome 66, Firefox 76, Safari 14.1, iOS mirrors. Raw Float32 frames → resample to
16 kHz mono → 16-bit WAV is the **one format every recognizer accepts and the same on every browser**
(~32 KB/s; a 6 s utterance ≈ 190 KB). The VAD library in §4 already hands over 16 kHz Float32 segments,
so `MediaRecorder` is not needed at all on that path.

### 3.4 `fetch` versus a WebSocket through Cloudflare Access

- **`fetch` POST per utterance** rides on the `CF_Authorization` cookie like every other `/api` call and
  keeps Factotum's existing behaviour: the page calls with `redirect: 'manual'` and treats a redirect /
  401 / 403 as "Sign in again" (`factotum/web/app.js:82`; CLAUDE.md:449 "`/api` must never answer 401,
  403 or a redirect"). Latency cost: one request per utterance — negligible next to transcription.
- **Constraint in the code:** `JsonOnlyMutations` answers 415 to any state-changing `/api` call without
  `Content-Type: application/json` (`factotum/api.py:244-255`; CLAUDE.md:229). An audio upload must either
  send JSON (base64, +33 %) or get a deliberate exemption for `audio/*` / `application/octet-stream`;
  both of those types already force a CORS preflight, so the CSRF reasoning still holds [R].
- **WebSocket:** Cloudflare proxies WebSockets on all plans; idle connections are closed after an
  unstated time, and Cloudflare restarts on releases close them, so heartbeats and reconnects are
  required (page dateModified 2026-08-14) [V] <https://developers.cloudflare.com/network/websockets/>.
  The browser sends the Access cookie on the same-origin upgrade request [D]; but forum reports show
  WebSocket paths behind Access sometimes needing a Bypass policy [D], Worker-level Access policies
  refuse WebSockets with 403 [D, not Factotum's setup], and an expired Access session turns the upgrade
  into a redirect the socket API cannot show the user [R]. Factotum is reached through `cloudflared`
  (docs/SETUP.md:186-232) and uvicorn[standard] would serve WebSockets (`requirements.txt:2`) [V], so it
  is possible, but **not needed**: per-utterance `fetch` is simpler and fits the existing sign-in handling.
- Cloudflare's proxied HTTP response timeout (~100 s → 524) [R] bounds how long one request may wait for a
  transcription or a Claude answer; long work should answer quickly and be polled, as Chat already does.

---

## 4. Hands-free mechanics

### 4.1 Voice activity detection in the page

**`@ricky0123/vad-web`** — Silero VAD on onnxruntime-web [V, npm registry and jsDelivr today]:

| | |
|---|---|
| Version | 0.0.31, published 2026-09-12 |
| Licence | ISC (onnxruntime-web MIT; Silero VAD model MIT [R]) |
| No build step | yes: README loads `ort.wasm.min.js` and `bundle.min.js` by script tag and calls `vad.MicVAD.new({...})`; `baseAssetPath` / `onnxWASMBasePath` point at where the files live |
| Sizes | `bundle.min.js` 69 KB; `silero_vad_v5.onnx` / `v6` 2.3 MB (legacy 1.8 MB); onnxruntime-web 1.22.0 `ort.wasm.min.js` 48 KB + `ort-wasm-simd-threaded.wasm` **11.2 MB** (latest ORT is 1.31.1, 2026-10-26; README pins 1.22.0) |
| Output | `onSpeechStart`, `onSpeechEnd(Float32Array @ 16 kHz)` |

Notes: load it only when a voice session starts (an 11 MB WASM on a phone's first use); serve the files from
Factotum itself (they then sit behind Access with the page, and need no CDN); Factotum's service worker must
never get a fetch handler (CLAUDE.md:474), so caching is ordinary HTTP caching on a versioned path. Without
cross-origin isolation ORT runs single-threaded, which is plenty for this tiny model [R]. iOS behaviour of
the library is undocumented — **device test**. Thresholds (`positiveSpeechThreshold`, `redemptionMs`,
`minSpeechMs`) exist [R] and matter in a noisy kitchen.

Lighter alternative: an RMS-energy gate in an `AudioWorklet` with a calibrated noise floor (a few KB; the
qryptchat PR did this) [D] — cheaper but much worse with a running tap or extractor fan [R].

### 4.2 Echo cancellation

- `echoCancellation` constraint: Chrome 59, Firefox 50, Safari 11 (iOS mirrors); `noiseSuppression` and
  `autoGainControl` are **not** supported in Safari [V] (browser-compat-data `MediaStreamTrack.json`,
  `MediaDevices.json`).
- Chrome's software canceller takes its reference from Chrome's own playout ("internal loopback") [D,
  <https://developer.chrome.com/blog/more-native-echo-cancellation/>]; Chrome added `echoCancellation:
  "all" | "remote-only"` (Intent to Ship 2025-08-22, M141, all Blink platforms) [V]
  <https://groups.google.com/a/chromium.org/g/blink-dev/c/x5nfoaUAUWY>. The spec's `true` must cancel
  at least remote audio and "should" cancel all [D].
- **`speechSynthesis` vs page audio:** OS text-to-speech is usually played outside the browser's audio
  pipeline, so it is not in the canceller's reference and the mic hears it [R]. Audio the page plays
  through `<audio>` / Web Audio is the case echo cancellers are built for [R]. On iOS the system voice
  processing unit applies to the whole device output while `play-and-record` is active [R]. Nobody
  documents a clean result for a phone speaker at kitchen volume — **device test**.
- Feed an echo-cancelled `getUserMedia` track to browser recognition with `start(track)` (Chrome 135,
  Firefox 160; not Safari) [V].
- **Earbuds** remove most echo by construction; the **phone speaker** is the hard case.

### 4.3 Barge-in

- Requires the mic to stay open while the system speaks, plus good echo cancellation; otherwise the VAD
  fires on the system's own voice. Practical design: half-duplex by default (VAD paused or its result
  ignored while speaking), with barge-in by (a) a tap anywhere / a big Stop button, (b) a headset button
  (Media Session, §5.3), (c) by voice only when earbuds are detected or the user turns it on.
- On iOS, voice barge-in with `speechSynthesis` "fails" — playback blocks recognizer input (bug 321436
  comment 2) [D].

### 4.4 Wake words

- Picovoice Porcupine Web runs in Chrome / Edge / Firefox / Safari via WASM but needs a Picovoice
  AccessKey (commercial, account per user) [D]; openWakeWord-on-onnxruntime-web ports exist but are
  community-grade [D]; Howl (Firefox Voice, 2020) is dated [D].
- A wake word only helps while the page is foreground and listening anyway — and on iOS it cannot listen in
  the background. **Not recommended.** Once a session is started, every utterance is addressed to Factotum;
  an optional "Factotum, …" prefix can be checked on the transcript if accidental speech becomes a problem.

---

## 5. Staying alive

### 5.1 Screen Wake Lock [V]

`navigator.wakeLock.request('screen')`: Chrome 84, Firefox 126, Safari 16.4, iOS 18.4 for the installed app
(16.4–18.4 "Does not work in standalone Home Screen Web Apps", WebKit 254545; Safari 18.4 notes "Fixed Wake
Lock API for Home Screen Web Apps"). Re-acquire on `visibilitychange` → visible (released whenever the page
is hidden; a related WebKit bug 255363 covers not re-acquiring) [V/D].

### 5.2 Locked phone / background

- **iPhone installed app:** audio stops when the standalone app leaves the foreground or the screen locks
  (WebKit 198277-era reports; still reported on iOS 26 for sequential playback) [D]; mic track muted/ended
  as in §3.1 [D]; there is no background microphone for web pages [D,
  <https://developer.apple.com/forums/thread/774239>]. Safari 27 fixed "media playback could not move to
  the next item in a playlist when the tab was in the background" — Safari tabs, not necessarily
  standalone apps [V].
- **Android Chrome:** a page keeps capturing in the background with a "using your microphone"
  notification, and audio elements keep playing [R] — untested.
- Design: keep the screen on (wake lock); on `visibilitychange` hidden → pause the session, stop speaking,
  keep the current item; on visible → show "Tap to resume" (a tap is needed again to unlock audio on iOS).

### 5.3 Media Session API [V]

`navigator.mediaSession` (`metadata`, `playbackState`, `setActionHandler`): Chrome 73, Chrome Android 57,
Firefox 82, Safari 15, iOS mirrors; `setMicrophoneActive`: Chrome 93, Safari 18.4. Safari 27 fixed
`setActionHandler` throwing. Handlers for `nexttrack` / `previoustrack` / `play` / `pause` could map to
next item / repeat / pause. **Caveats [R/D]:** a page becomes the "now playing" target only while it plays
media through a media element; Web Audio or `speechSynthesis` alone may not register; with the mic open a
Bluetooth headset is in call mode, where its button may mean answer / hang up rather than play / pause.
Lock-screen artwork has been flaky on iPhone [D]. **Device test.**

### 5.4 Bluetooth headsets [D]

Opening the mic switches most Bluetooth headsets (AirPods included) from A2DP to the hands-free profile:
mono, voice-band, noticeably worse playback, for the whole session
(<https://www.daily.co/blog/why-bluetooth-audio-quality-suffers-in-video-calls/>). Web pages cannot pick
"phone mic + earbud output" on iOS: `selectAudioOutput` is Firefox-only [V]; Safari 26 notes "Added support
for the Speaker Selection API on iOS and iPadOS" [V], which may allow choosing an output — unclear whether
that keeps the headset out of hands-free mode. Releasing the mic between turns brings A2DP back but costs a
profile switch (~1 s, audible gap) each time [R].

---

## 6. Support table

Legend: ✅ works · ⚠️ works with serious caveats · ❌ no · (V/D/R) confidence as above.

| | Browser recognition | Browser synthesis | Mic capture | VAD in page | Echo cancellation | Wake lock | Media Session |
|---|---|---|---|---|---|---|---|
| **Desktop Chrome / Edge** | ✅ Chrome: cloud by default, on-device with `processLocally` (139+, quality hint 150). Edge: cloud; local behind flag (V) | ✅ Edge's Natural voices excellent; Chrome Google voices cut at ~15 s (D) | ✅ (V) | ✅ AudioWorklet + WASM (V/R) | ✅ for page-played audio; `"all"` mode M141 (V/R) | ✅ 84 (V) | ✅ 73 (V) |
| **Desktop Firefox** | ✅ 160, on-device only, 40 langs; no `speechstart/end` (V) | ✅ OS voices (D) | ✅ (V) | ✅ (V/R) | ✅ constraint (V); quality R | ✅ 126 (V) | ✅ 82 (V) |
| **Desktop Safari** | ⚠️ needs Siri/Dictation; `webkit` prefix; no on-device control (V/D) | ✅ good macOS voices, not Siri's (D) | ✅ (V) | ✅ (V/R) | ⚠️ echo yes; no noise suppression / AGC constraints (V) | ✅ 16.4 (V) | ✅ 15 (V) |
| **Android Chrome** | ⚠️ Google cloud only; `continuous` ignored; no `processLocally`, `phrases` or `start(track)` (V) | ✅ system voices; `pause()` = `cancel()` (V/D) | ✅ (V) | ✅ (R) | ✅ (V/R) | ✅ (V) | ✅ 57 (V) |
| **iPhone Safari tab** | ❌ in practice: iOS 27 deaf after first session (V); stops after audio (V/D); can't coexist with gUM (D) | ⚠️ compact voices only; silent-switch behaviour unclear (D) | ✅ (V); playback quieter while open (D) | ⚠️ should work (AudioWorklet 14.1, WASM); library on iOS undocumented (R) | ⚠️ yes, quality on speaker unknown; no NS/AGC (V/R) | ✅ 16.4 (V) | ⚠️ 15; reliability with mic open unknown (V/R) |
| **iPhone installed app** | ❌ same as tab; was absent until some iOS 26.x (V/D) | ⚠️ as tab (D) | ⚠️ works; permission persistence and background loss to test (D) | ⚠️ as tab (R) | ⚠️ as tab | ✅ since 18.4 (V) | ⚠️ as tab; lock-screen flaky (D) |

---

## 7. Recommendation

### 7.1 The one path that works on all six

```
Start tap ─► unlock audio (AudioContext.resume + silent <audio>.play) ─► getUserMedia({audio:{echoCancellation:true}})
          ─► wakeLock.request('screen') ─► mediaSession handlers ─► load VAD (lazy)
loop: speak item (server audio via the unlocked path; speechSynthesis fallback)
      ─► listen: VAD on (half-duplex: ignore VAD while speaking unless barge-in is on)
      ─► onSpeechEnd(16 kHz Float32) ─► WAV ─► fetch POST /api/voice/… (same origin, Access cookie)
      ─► server: transcribe ─► interpret (command grammar first, Claude only when needed) ─► reply text (+audio)
      ─► page: show transcript + proposed change in the card, stage it in the tray as today, speak the reply
visibilitychange hidden ─► pause; visible ─► "Tap to resume" (re-check mic track, re-acquire wake lock)
```

Why: it uses only `getUserMedia`, Web Audio / `AudioWorklet`, WASM, `fetch` and an audio element — all solid
on every target including the installed iPhone app — and it never touches Safari's broken recognizer. It keeps
Factotum's sign-in handling, its single page with no build step, and its "Claude decides, Python writes" rule:
the transcript is just another text turn. A big on-screen push-to-talk button doubles as the fallback when the
VAD misbehaves, and every spoken step has a visible control, so review-by-page is untouched.

What the server path needs (for the designer; choosing an engine is another agent's question): a
transcription engine that runs on the owner's machine or a paid API behind a config switch (absent = off,
per CLAUDE.md "Absent means off"), text-to-speech likewise, and a way to accept audio past
`JsonOnlyMutations` (`factotum/api.py:244-255`).

### 7.2 Best quality where more is available

- **Recognition:** if `SpeechRecognition.available({langs:[lang], processLocally:true, quality:'command'})`
  says `available` (Chrome desktop 139+/150+, Firefox desktop 160, Edge once on), use it with
  `start(echoCancelledTrack)` and `phrases` (Chrome 142+) boosted with the commands and the current
  card's task name. Free, private, fast. Fall back to the server path on any error. Never use browser
  recognition on Safari (macOS or iOS); on Android Chrome only behind an explicit "send my voice to
  Google" switch.
- **Synthesis:** server-made neural audio everywhere if configured; otherwise Edge desktop's Natural
  voices are the best free option, then Android's, then macOS'; iPhone's compact voices last.
- **Earbuds** detected (`enumerateDevices` labels, or a user toggle) → allow voice barge-in.

### 7.3 Things only a test on a real iPhone (iOS 27.x, installed app) can settle

1. Is the mic permission kept across launches of the installed app, across `pushState` address changes,
   and across an hour of use, or does it prompt again?
2. With `getUserMedia` live: does reply audio play through the bottom speaker at full volume, via Web
   Audio and via `<audio>`, with each `navigator.audioSession.type` (`auto`, `play-and-record`,
   `playback`)?
3. With the ringer switch on silent: does `speechSynthesis`, Web Audio, and `<audio>` each play?
4. Echo: phone on the counter, speaker at cooking volume — does the VAD fire on Factotum's own voice
   with `echoCancellation: true`? With `speechSynthesis` versus server audio?
5. Does `@ricky0123/vad-web` (11 MB WASM, AudioWorklet) load, run and stay accurate on the phone, and
   what does it cost in battery over 20 minutes with the screen held on?
6. Wake lock holds for 20+ minutes in the installed app; what happens on an incoming call, Siri, a timer
   alarm, Control Center, and coming back?
7. After lock / unlock or switching apps: does the mic track come back `muted`/`ended`, and does one tap
   restore both mic and audio?
8. Media Session: do AirPods presses (single / double / triple) and lock-screen buttons reach
   `play/pause/nexttrack/previoustrack` while the mic is open (hands-free profile)?
9. AirPods / Bluetooth: how bad is reply audio in hands-free mode, and does the Safari 26 Speaker
   Selection API change anything?
10. For the record: does `SpeechRecognition` exist in the installed app on the owner's iOS version, and
    does the iOS 27 "deaf after first session" bug reproduce there (to decide whether it can ever be an
    option)?
11. Whether recorded WAV/MP4 sent with `fetch` through Access works from the installed app after a long
    idle (Access cookie expiry → the page's "Sign in again").

---

## 8. Code facts relevant to the design (Factotum, read today) [V]

- One page, one script, no build step: `factotum/web/index.html:55` loads `/static/app.js`; the build
  stamp covers `api.STAMPED = ("index.html", "app.js", "app.css", "manifest.webmanifest")`
  (`factotum/api.py:56`) — any new file the page loads (a voice module, the VAD bundle) joins that list
  or is served under a versioned path (CLAUDE.md:451).
- iPhone detection and installed-app detection already exist: `isIOS`, `installed`
  (`factotum/web/app.js:203-205`).
- The permission-in-the-tap rule is already practised for notifications: `pushOn` calls
  `Notification.requestPermission()` before any `await` (`factotum/web/app.js:309-311`; CLAUDE.md:498).
  A voice Start button must do the same for `getUserMedia`, `AudioContext.resume()` and the first
  `speechSynthesis.speak()`/`<audio>.play()`.
- The API helper sets JSON and `redirect: 'manual'` (`factotum/web/app.js:82`); the server refuses a
  non-JSON state-changing `/api` call with 415 (`factotum/api.py:244-255`).
- The service worker has only `install`, `activate`, `push`, `notificationclick` listeners
  (`factotum/web/sw.js:103-110`) and by rule never a fetch handler (CLAUDE.md:474): no offline caching of
  VAD/WASM assets through it.
- The page itself is served without a Content-Security-Policy (the only CSP is on the OAuth consent page,
  `factotum/api.py:1158`), so WASM and workers are not blocked today; adding a CSP later would need
  `'wasm-unsafe-eval'` for onnxruntime-web [R].
- `manifest.webmanifest` declares `display: standalone`, `scope: /` (`factotum/web/manifest.webmanifest:6-8`).
- Reached through a Cloudflare Tunnel behind Access (docs/SETUP.md:186-232); uvicorn[standard] is installed
  (`requirements.txt:2`).

---

## 9. What I could not find out

- The iOS version in which `SpeechRecognition` became available in Home Screen apps, and whether Apple
  processes web dictation on-device or on servers.
- Any statement from Apple/WebKit on bug 321436 (iOS 27 recognition) or a fix date.
- Whether iOS `speechSynthesis` obeys the silent switch on iOS 26/27 (sources conflict).
- Whether the installed iOS app keeps microphone permission across launches in 2026.
- How Chrome's `echoCancellation: "all"` treats OS text-to-speech, and whether it shipped on Android/iOS
  as planned (M141 intent; not in browser-compat-data yet).
- Cloudflare's exact WebSocket idle timeout for non-Enterprise plans; an official Cloudflare statement that
  Access-protected self-hosted apps pass WebSocket upgrades with the cookie.
- Any documentation of `@ricky0123/vad-web` on iOS Safari; Picovoice's 2026 free-tier terms.
- Android Chrome's background microphone behaviour in 2026 (recalled only).

## 10. Sources (all fetched or searched 2026-11-19)

- MDN SpeechRecognition (mod. 2026-11-16): <https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition>
- MDN processLocally (mod. 2026-11-06): <https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/processLocally>
- MDN Using the Web Speech API (mod. 2026-11-16): <https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API/Using_the_Web_Speech_API>
- MDN Audio Session API (mod. 2026-08-19): <https://developer.mozilla.org/en-US/docs/Web/API/Audio_Session_API>
- MDN browser-compat-data (HEAD 2026-11-19): `api/SpeechRecognition.json`, `SpeechSynthesis.json`, `WakeLock.json`, `MediaSession.json`, `MediaRecorder.json`, `AudioWorkletNode.json`, `AudioSession.json`, `MediaDevices.json`, `MediaStreamTrack.json` at <https://github.com/mdn/browser-compat-data/tree/main/api>
- Firefox 160 for developers (released 2026-11-10): <https://developer.mozilla.org/en-US/docs/Mozilla/Firefox/Releases/160>
- Chrome Intent to Ship, on-device recognition quality (2026-05-05, M150): <https://groups.google.com/a/chromium.org/g/blink-dev/c/P8P-x7AnC6I>
- Chrome Intent to Ship, echoCancellation modes (2025-08-22, M141): <https://groups.google.com/a/chromium.org/g/blink-dev/c/x5nfoaUAUWY>
- Chrome speak() without activation (M71): <https://groups.google.com/a/chromium.org/g/blink-dev/c/WsnBm53M4Pc>
- Chrome "More native echo cancellation" (M68): <https://developer.chrome.com/blog/more-native-echo-cancellation/>
- Microsoft Edge SpeechRecognition (2026-06-01, upd. 2026-11-18): <https://learn.microsoft.com/en-us/microsoft-edge/web-platform/speech-recognition-api>
- Safari 18.4 notes (2025-03-31): <https://developer.apple.com/documentation/safari-release-notes/safari-18_4-release-notes.md>
- Safari 26.0 notes (2025-09-15): <https://developer.apple.com/documentation/safari-release-notes/safari-26-release-notes.md>
- Safari 27.0 notes (2026-09-14): <https://developer.apple.com/documentation/safari-release-notes/safari-27-release-notes.md>
- WebKit 225298 (recognition not in Home Screen apps, 2021–2025): <https://bugs.webkit.org/show_bug.cgi?id=225298>
- WebKit 321436 (iOS recognition deaf after audio / iOS 27, 2026-08-10 → 2026-11-18): <https://bugs.webkit.org/show_bug.cgi?id=321436>
- WebKit 317741 (recognition audio session, fixed 2026-06-26): <https://bugs.webkit.org/show_bug.cgi?id=317741>
- WebKit 319706 (gUM after audiomxd reset, fixed 2026-07-31/09-08): <https://bugs.webkit.org/show_bug.cgi?id=319706>
- WebKit 254545 (wake lock in Home Screen apps): <https://bugs.webkit.org/show_bug.cgi?id=254545>
- WebKit 215884 (standalone gUM re-prompts): <https://bugs.webkit.org/show_bug.cgi?id=215884>
- Web Speech API issue 96 (Safari reports through 2026-08): <https://github.com/WebAudio/web-speech-api/issues/96>
- qryptchat PR 323 (2026-10-26, iOS voice mode by recording; untested on hardware): <https://github.com/profullstack/qryptchat-web/pull/323>
- Readium, SpeechSynthesis in browsers and OSes (data Apr 2024): <https://readium.org/speech/docs/WebSpeech.html>
- Apple forum, iOS 18 voices missing (Sep 2024): <https://developer.apple.com/forums/thread/764438>
- Apple forum, background WebRTC: <https://developer.apple.com/forums/thread/774239>
- audjust, unmute Web Audio on iOS (2024-02-13): <https://www.audjust.com/blog/unmute-web-audio-on-ios>
- PhET utterance-queue issue 60 (Chrome 15 s cut-off): <https://github.com/phetsims/utterance-queue/issues/60>
- Cloudflare WebSockets (dateModified 2026-08-14): <https://developers.cloudflare.com/network/websockets/>
- Daily.co, Bluetooth in calls: <https://www.daily.co/blog/why-bluetooth-audio-quality-suffers-in-video-calls/>
- ricky0123/vad: <https://github.com/ricky0123/vad>; npm `@ricky0123/vad-web` 0.0.31 (2026-09-12, ISC); `onnxruntime-web` 1.31.1 (2026-10-26, MIT); file sizes from <https://data.jsdelivr.com/v1/packages/npm/@ricky0123/vad-web@0.0.31?structure=flat>
- Picovoice web quick start: <https://picovoice.ai/docs/quick-start/picovoice-web>
