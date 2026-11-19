# 05 · Speech engines for a spoken Review (as of 2026-11-19)

Question: what should turn the owner's speech into text (STT) and the system's text into speech (TTS)
for a spoken Review session in Factotum, and how should the choice be configurable?

Evidence labels used throughout:

- **[verified today]**: measured on this machine or read on the vendor's own page on 2026-11-19.
- **[documented, untested]**: stated by a vendor or a third party page; not tried here.
- **[recalled]**: from memory (training data to mid 2026); not re-checked today.

Contents: 0 Summary · 1 The machine · 2 Code facts that constrain the design · 3 Local STT · 4 STT as a
service · 5 Anthropic and speech · 6 Browser speech (Web Speech API) · 7 Local TTS · 8 TTS as a service ·
9 Realtime speech-to-speech and voice-agent frameworks · 10 Where the time goes in one spoken turn ·
11 Three complete stacks · 12 The provider seam · 13 Recommendations · 14 Open questions and what I could not
find out · 15 Sources.

---

## 0. Summary

1. **The long pole is Claude, not speech.** A headless `claude -p` call that interprets one sentence took
   1.8 to 2.8 s wall clock warm, and 15.7 s on the first, cold call [verified today, 4 probe runs, §10].
   The live database's 27 Chat turns (with tools) have a median of 11.6 s and p90 of 17.9 s [verified
   today, `runs` table]. Every STT and TTS option below adds 0.2 to 1.5 s. A spoken shorthand
   ("done", "skip", "next", "push to Monday") must be handled by a deterministic grammar with no
   Claude run, or every decision will feel slow; Claude is for free-form sentences and questions.
2. **This machine can run good local speech.** It has an RTX 3070 (8 GB) that WSL2 exposes to CUDA,
   16 cores, 47 GB RAM, Python 3.12.3, 499 GB free [verified today]. Cached Whisper large-v3 decoded a
   5 s clip in about 0.45 s warm on the GPU and about 6 s on the CPU; Whisper small took about 0.2 s on
   the GPU and 1.2 s on the CPU (transformers, not the faster CTranslate2 runtime) [verified today].
   ffmpeg is not installed, and nothing speech-related is in Factotum's `.venv`.
3. **A new person's machine may have none of this.** A fresh install should default to the browser's
   own recognition and synthesis (no install, no key, no cost), with local and paid providers chosen
   in `config/factotum.yaml`.
4. **The browser alone is shaky on the owner's phone.** iOS Safari supports `SpeechRecognition` from
   14.5, but several reports say it fails in Home Screen web apps (the installed Factotum app), and I
   found no 2026 confirmation either way [documented, untested]. Recording audio in the page and
   transcribing on the server (local or paid) works the same in every browser. It is the reliable
   path for the installed app.
5. **Paid speech is cheap at this volume.** For 20 minutes of review a day, transcription costs under
   $1 a month at any listed vendor and speech output costs $3 to $17 a month (§11). Realtime
   speech-to-speech models (OpenAI gpt-live / gpt-realtime, Gemini Live) would replace Claude as the
   model that talks and decides. That conflicts with "Claude decides, Python writes" and with the
   owner's subscription, and buys about 1 s per turn at best (§9).
6. **Anthropic offers no speech API.** Claude Code's `/voice` dictation is interactive only: it
   needs a local microphone and a claude.ai login, and it has no programmatic interface. The Claude
   API takes no audio input and produces no audio output [verified today, §5].

---

## 1. The machine (read-only checks, 2026-11-19)

| Item | Value | How checked |
|---|---|---|
| CPU | Intel Core Ultra 9 285H, `nproc` = 16 | `lscpu`, `nproc` |
| RAM | 47 GiB total, 34 GiB available; 16 GiB swap | `free -g` |
| GPU | NVIDIA GeForce RTX 3070, 8192 MiB, driver 610.57.01, CUDA UMD 13.3, 0 MiB in use at the time | `nvidia-smi` |
| CUDA from WSL | works: torch 2.11.0+cu128 in another project's venv (`~/vadc-a2t/venv`) sees the 3070; 50 fp16 4k×4k matmuls in 0.475 s; onnxruntime-gpu providers TensorRT, CUDA, CPU | one-off Python run, scratch dir, `-I` |
| CUDA libraries on the system | `libcublas.so.12`, `libcublasLt.so.12` in `/lib/x86_64-linux-gnu`; **no cuDNN** in `ldconfig`; `nvcc` present | `ldconfig -p` |
| Python | 3.12.3 in `/home/robert/factotum/.venv` | `.venv/bin/python --version` |
| Factotum `.venv` | fastapi 0.162.0, uvicorn 0.54.0, httpx 0.28.1; **no** numpy, torch, onnxruntime, ctranslate2, whisper, kokoro, piper | `pip list` |
| Disk | `/` 1007 G, 457 G used, 499 G free | `df -h` |
| ffmpeg | **not installed** (`ffmpeg: command not found`) | `which ffmpeg` |
| Cached models | `openai/whisper-large-v3`, `openai/whisper-small` (and unrelated audio models: Qwen2-Audio-7B, Qwen2.5-Omni-3B, CLAP, wav2vec2) in `~/.cache/huggingface/hub` | `ls` |
| Claude CLI | 2.1.319 (Claude Code); `claude --help` mentions no voice or audio flag | `claude --version`, `--help` |
| Serving | the app is reached over HTTPS through a Cloudflare tunnel and Access (`docs/SETUP.md:186`), so the page is a secure context, which `getUserMedia` requires [recalled: secure-context rule] | `docs/SETUP.md` |

Notes for a designer:

- The GPU is shared with the owner's other ML work (other venvs hold CUDA torch; the HF cache holds
  7B audio models). A resident speech model takes 1 to 3 GB of the 8 GB. Plan for "GPU busy or absent"
  and fall back to the CPU or the browser.
- `faster-whisper` needs cuBLAS 12 and **cuDNN 9** for the GPU. cuDNN is not on the system, but the
  pip wheels `nvidia-cublas-cu12` and `nvidia-cudnn-cu12==9.*` supply it if `LD_LIBRARY_PATH` is set
  before Python starts (faster-whisper README) [documented, untested]. Under systemd that means an
  `Environment=` line in the unit, which is easy to get wrong.
- No ffmpeg is needed if the page sends raw 16 kHz PCM. faster-whisper also decodes compressed
  audio itself through PyAV, which bundles FFmpeg's libraries (README) [documented, untested].

---

## 2. Code facts that constrain the design

- **Every state-changing `/api` call must be `application/json`.** `JsonOnlyMutations`
  (`factotum/api.py:243-257`) answers 415 to any non-GET `/api/` request without that content type.
  An audio upload as `multipart/form-data` or `audio/webm` would be refused. Either send the audio
  as base64 inside JSON (16 kHz mono 16-bit PCM is 32 KB/s, so a 5 s utterance is 160 KB, or
  about 213 KB as base64), or deliberately exempt one route, which weakens the CSRF reasoning in
  that docstring. TTS audio can come back on a GET, which the middleware does not touch.
- **There is no audio code anywhere** in `factotum/` (a grep for audio, speech, microphone and
  getUserMedia across `.py`, `.js` and `.html` finds nothing) [verified today].
- **There is no page-wide CSP or Permissions-Policy header.** The only CSP is on the OAuth consent
  page (`factotum/api.py:1158`), so the microphone is not blocked by headers today.
- **Claude runs go through `factotum/claude/runner.py`**: `command()` at `runner.py:74-90` builds
  `claude -p --model … --output-format json|stream-json --max-budget-usd … --setting-sources ""
  --disable-slash-commands --strict-mcp-config …`. The per-kind tables `BUDGETS_USD`, `TIMEOUTS_S`
  and `MODELS` are at `runner.py:34-41`. A new run kind (for example `voice_turn`) needs a line in all
  three and in the example config (CLAUDE.md rule). No other model access exists. `.env.example`
  names no OpenAI, Deepgram, ElevenLabs, Google or Groq key [verified today].
- **The existing command grammar needs task numbers.** `commands.VERBS` (`factotum/commands.py:30-38`:
  done, drop, snooze, push/defer/postpone/move, nodue, top/pin, unpin, longterm, start, reopen) and
  `parse()` (`commands.py:46`) expect forms like `done 389 392`. A spoken grammar needs (a) an
  implicit "the current card" target, (b) spoken numbers turned into digits ("four twelve",
  "four hundred and twelve" → 412), and (c) navigation words (next, skip, back, repeat, stop) that
  are not task commands. `dates.resolve_when` (`factotum/dates.py:25`) already turns "monday" and
  similar into a date.
- **The Review queue route writes.** `GET /api/review` (`api.py:526`) stamps `last_offered_at` and
  takes `commit_lock`. A voice loop must read the queue the way the page does (once, with
  `exclude`), never poll it. `GET /api/review/count` (`api.py:536`) reads only.
- **Measured Claude latencies from the live DB (read-only, all rows to 2026-11-19)** [verified today]:

  | run kind | n | p10 | median | p90 | median cost (API-equivalent) |
  |---|---|---|---|---|---|
  | chat-turn (Sonnet, MCP tools) | 27 | 4.5 s | 11.6 s | 17.9 s | $0.064 |
  | today-summary (no tools) | 29 | 2.9 s | 4.0 s | 6.1 s | $0.051 |
  | mail-triage (no tools) | 95 | 2.1 s | 2.9 s | 6.8 s | $0.056 |
  | ripple (no tools) | 5 | 4.0 s | 5.6 s | 8.1 s | $0.027 |

---

## 3. Speech-to-text that runs locally

### 3.1 Candidates

| Engine / model | Size | Licence | Strengths for "decide on a card" | Weaknesses | Vocabulary biasing | Streaming |
|---|---|---|---|---|---|---|
| **faster-whisper** (CTranslate2) with `large-v3-turbo` | 809 M params; int8 about 1.5 GB VRAM (HF discussion on the CT2 conversion) | MIT (code); Whisper weights MIT [documented / recalled] | mature Python package; good on names, numbers and punctuation; runs on CPU or GPU; Silero VAD built in (`vad_filter`) | Whisper pads every input to a 30 s window, so a 3 s clip pays close to the encoder cost of 30 s [recalled]; can invent text on silence or noise (e.g. "Thank you.") [recalled] | `hotwords` ("Hotwords/hint phrases") and `initial_prompt` parameters exist in `WhisperModel.transcribe` [verified today, source read] | no native streaming; transcribe each utterance after VAD |
| faster-whisper `small.en` / `distil-large-v3` | 244 M / 756 M | MIT | CPU-friendly | lower accuracy on names | same | same |
| **whisper.cpp** | same models in GGUF | MIT [recalled] | no Python deps, CPU (AVX/OpenVINO) or CUDA; similar GPU speed to faster-whisper (README table) | a separate binary to build or ship | `--prompt` [recalled] | a `stream` example exists [recalled] |
| **NVIDIA Parakeet TDT 0.6B v3** | 600 M | **CC-BY-4.0** (model card, read today) | 6.34 % average WER on the Open ASR Leaderboard; RTFx 3,333 (card); 25 European languages | needs NeMo + PyTorch (heavy) or an ONNX export (sherpa-onnx, istupakov ONNX); noise: WER rises to 11.7 % at 0 dB SNR (card) | not mentioned on the card [verified today]; sherpa-onnx has hotword support for transducers [recalled, not confirmed for this model] | chunked streaming via a NeMo script (card) |
| **Nemotron Speech Streaming EN 0.6B** (Jan 2026, updated Mar 2026) and Nemotron 3.5 ASR Streaming (multilingual, mid 2026) | 600 M | sources disagree: NVIDIA Open Model License vs OpenMDW-1.1 [documented, unresolved] | true cache-aware streaming, 80 ms minimum chunk, about 160 ms default latency [documented, untested] | NeMo stack; licence unclear | unknown | yes |
| **Moonshine v2 Streaming** (tiny 34 M, small 123 M, medium 245 M; Feb 2026, arXiv 2602.12241) | tiny to medium | "permissive" in the paper; MIT on derived cards [documented, unconfirmed] | built for low-latency edge streaming; medium 2.1 % on LibriSpeech clean | English only; cards warn it can hallucinate words; weaker on accented or noisy speech (AMI 10.7 % for medium) | not documented | yes |
| **Vosk** (Kaldi) | 50 MB small model | Apache-2.0 [recalled] | tiny, CPU, streaming; can be **restricted to a word list or grammar** (pass a JSON list of phrases to the recognizer) [recalled]: ideal for a closed command set | poor on open-vocabulary speech (task text, names) compared with Whisper-class models [recalled] | grammar restriction (hard bias) | yes |
| **Voxtral Mini 4B Realtime 2602** (Mistral, Feb 2026) | 4 B, 8.87 GB download | Apache-2.0 (open weights) [documented] | streaming; strong accuracy per Mistral | too large for comfort on an 8 GB card next to other work | context biasing claimed for the API [documented, untested] | yes |
| Leaders on the HF Open ASR Leaderboard (dataset view, read today): ARK-ASR-3B 4.76 %, MOSS-Transcribe-preview-2B 4.87 %, parakeet-ultra 5.32 %, Cohere Transcribe 03-2026 5.42 % (Apache-2.0), ARK-ASR-0.6B 5.53 %, Qwen3-ASR-1.7B 5.76 % | 0.6 to 3 B | various | best average WER on long-form benchmarks | young runtimes; RTFx not shown in what I could read; average WER on podcasts says little about 3 s commands | mostly undocumented | mostly offline |

On short commands the leaderboard differences (5 % vs 6 % average WER) matter less than three things:
biasing toward the card's words, quick endpointing (knowing when the owner stopped), and not inventing
text from kitchen noise. Whisper-class models with `hotwords` / `initial_prompt` plus Silero VAD are
the most practical local choice from Python today. Parakeet is faster and as accurate, but its runtime
is heavier and its biasing is unconfirmed.

### 3.2 Speed on this machine (measured today, transformers 5.22.1, torch 2.11 cu128)

5 s clip of synthetic noise, decoding forced to 20 tokens (about a 12-word utterance), English, warm
runs (the first run of each process is slower):

| Model | Device | Load | Warm decode of one 5 s utterance |
|---|---|---|---|
| whisper-large-v3 | RTX 3070, fp16 | 7.6 s | **0.43 to 0.47 s** (first run 1.7 s) |
| whisper-small | RTX 3070, fp16 | 1.5 s | **0.15 to 0.21 s** |
| whisper-small | CPU, 8 threads, fp32 | 0.3 s | **1.17 to 1.26 s** |
| whisper-large-v3 | CPU, 8 threads, fp32 | 1.4 s | **about 6.4 to 6.8 s** (first run 18.7 s; one negative reading discarded as a WSL clock jump) |

Reading these numbers:

- These are upper bounds for faster-whisper. CTranslate2 int8 is typically 2 to 4× faster than
  transformers fp32 on a CPU, and `large-v3-turbo` has 4 decoder layers against large-v3's 32
  [recalled]. Expect turbo int8 on this CPU at roughly 1.5 to 3 s per utterance and on the GPU at
  0.2 to 0.4 s [estimate, untested].
- Keep the model loaded. A cold load costs 1.5 to 7.6 s and the first decode on the GPU is about
  4× slower.
- The faster-whisper README's GPU table uses an RTX 3070 Ti 8 GB: large-v2 int8, 13 minutes of
  audio, beam 5, 59 s and 2.9 GB VRAM [documented].

### 3.3 Biasing toward the card's vocabulary

The service knows the current card: its name and text, its project, its people (`counterpart`,
`waiting_on`), its linked task numbers, plus the configured project names and command verbs.

| Mechanism | Where | Limit | Kind |
|---|---|---|---|
| `hotwords`, `initial_prompt` | faster-whisper | Whisper's prompt is about 224 tokens [recalled; Groq documents the same 224-token limit] | soft |
| `prompt` | OpenAI, Groq Whisper | 224 tokens (Groq doc) | soft |
| `keywords` / context fields | OpenAI gpt-transcribe, gpt-live-transcribe | not stated; "a hint, not a vocabulary list" (developer guide) | soft |
| keyterm prompting | Deepgram Nova-3 / Flux | +$0.0013/min | soft |
| keyterm prompting | AssemblyAI | up to 1,000 terms batch, 100 terms streaming (free on streaming) | soft |
| keyterms | ElevenLabs Scribe | +$0.05/hr batch, +$0.08/hr realtime | soft |
| `phrases` (`SpeechRecognitionPhrase(text, boost)`) | Web Speech API | experimental; MDN documents it; Chrome intent to ship July 2025; I found no Chrome version that shipped it | soft |
| grammar / word list | Vosk | hard restriction | hard |

Two cautions. Task names and mail subjects can be written by strangers (CLAUDE.md, "Text written by
strangers goes through bounded patterns"), so hints must be capped and stripped like any other
prompt text. And with a paid provider the hints themselves (task names, people's names) leave the
machine.

---

## 4. Speech-to-text as a service

Prices are pay-as-you-go list prices.

| Vendor / model | Price | Short-clip latency | Streaming | Biasing | Notes / privacy | Source and label |
|---|---|---|---|---|---|---|
| **OpenAI gpt-transcribe** (file; launched 28/29 Jul 2026) | **$0.0045/min** | about 34× faster than real time (one review) | file model; `gpt-live-transcribe` streams at $0.017/min | context prompt + `keywords` | OpenAI API data is not used for training by default [recalled]; the 4o transcribe models and whisper-1 are reported deprecated for removal on 26 Feb 2027 [documented, two third-party sources] | developers.openai.com/api/docs/pricing [verified today] |
| OpenAI gpt-4o-mini-transcribe / gpt-4o-transcribe / whisper-1 | $0.003 / $0.006 / $0.006 per min | n/a | via Realtime transcription | prompt | see the deprecation note above | same [verified today] |
| **Deepgram Nova-3** (mono) | $0.0043/min pre-recorded, $0.0048/min streaming (promo) | sub-second [documented] | yes (WebSocket) | keyterm +$0.0013/min | $200 free credit, no card | deepgram.com/pricing [verified today] |
| **Deepgram Flux** (English) | $0.0065/min streaming | built for voice agents, with model-side end-of-turn detection [documented] | yes | keyterm | same | same [verified today] |
| **AssemblyAI** STT-3.7 / STT-3.7-Realtime | $0.21/hr ($0.0035/min) batch; $0.30/hr streaming, **billed on socket open time** | n/a | yes | keyterms: 1,000 batch (+$0.05/hr), 100 streaming (free) | $50 free credit | assemblyai.com/pricing [verified today] |
| **ElevenLabs Scribe v3 / v2** | $0.20/hr ($0.0033/min); realtime $0.22/hr | Scribe v2 Realtime about 150 ms | yes | keyterms +$0.05/hr (+$0.08/hr realtime) | Zero Retention Mode on the medical / enterprise tier | elevenlabs.io/pricing/api [verified today] |
| **Groq Whisper** large-v3-turbo / large-v3 | $0.04/hr / $0.111/hr, **minimum 10 s billed per request** | "near-instant"; RTF 216× turbo | no | `prompt` up to 224 tokens | — | console.groq.com/docs/speech-to-text [verified today] |
| **Mistral Voxtral Mini Transcribe V2** / Voxtral Realtime | $0.003/min / $0.006/min | n/a | Realtime yes | context biasing [documented] | Realtime weights are open (Apache-2.0) | mistral.ai news, Feb 2026 [documented] |
| **Google Gemini** `gemini-3.5-transcribe-live` | $0.005/min in + $0.004/min out | streams over WebSocket | yes | prompt | free-tier data is used to improve Google products (pricing page) | aistudio.google.com/docs/pricing [verified today] |
| Google Cloud Speech-to-Text (Chirp 3) | could not read the page | — | yes | phrase sets [recalled] | — | cloud.google.com/speech-to-text/pricing (page did not render) |
| Azure AI Speech | about $0.18/hr batch per aggregators; real-time price not found | — | yes | phrase lists [recalled] | — | aggregators only [documented, unconfirmed] |
| Soniox, Speechmatics, Gladia | not researched | | | | | |

The latency of a short clip is mostly network round trips plus queueing. A 3 to 8 s utterance comes
back in roughly 0.3 to 1 s from a batch endpoint, and the final transcript arrives 0.15 to 0.4 s after
end of speech from a streaming one [recalled, typical; not measured here].

---

## 5. Anthropic and speech (as of 2026-11-19)

- **Claude API**: the Features overview (platform.claude.com/docs/en/build-with-claude/overview, read
  today) lists vision, PDF, files, tools and so on. It lists no audio input, speech-to-text or
  text-to-speech feature [verified today]. A Feb 2026 SDK feature request for audio input in the
  Messages API exists [documented].
- **Claude Code voice dictation** (code.claude.com/docs/en/voice-dictation, read today) [verified
  today]:
  - `/voice` gives hold-to-talk or tap-to-talk dictation in the interactive CLI and the VS Code
    extension.
  - Audio streams to Anthropic's servers; transcription does not count toward usage limits.
  - It needs a claude.ai login (not an API key), a local microphone, and WSLg on WSL.
  - It only fills the prompt box; there are no voice replies.
  - Third-party coverage of the March 2026 launch says Anthropic confirmed it is not a programmatic
    interface for the Agent SDK [documented].
  - `claude --help` (2.1.319) shows no flag for audio in `-p` mode [verified today].
  - **Factotum cannot use it**: the service runs `claude -p` headless with no microphone and no
    terminal.
- **Conclusion**: STT and TTS must come from somewhere other than Anthropic. Claude stays the model
  that interprets the transcript.

---

## 6. The browser's own speech (Web Speech API)

| Point | Detail | Label |
|---|---|---|
| `SpeechRecognition` surface | `lang`, `continuous`, `interimResults`, `maxAlternatives`, `phrases` (biasing), `processLocally` (on-device), static `available()` / `install()` for on-device language packs; `grammars` removed and ignored | MDN, last modified 16 Nov 2026 [verified today] |
| Support | Chrome partial since 25 (Chrome 157 current); Safari partial 14.1 to 27.3; iOS Safari partial 14.5 to 27.2; Edge listed as not supported; Firefox disabled by default; Samsung partial | caniuse [verified today] |
| Where audio goes | Chrome uses a server engine by default, so audio goes to Google, unless `processLocally` is set and the pack is installed (on-device since Chrome 139 for en-US and a few others, Aug 2025) | MDN, blink-dev [documented] |
| Contextual biasing (`phrases`) | Chrome intent to ship July 2025; TPAC Nov 2025 said "shipping in Chrome"; I found no version number | [documented, unconfirmed] |
| iOS Home Screen web app | WebKit bug 225298 says recognition is not available in SafariViewController and Home Screen web apps; an Apple forum post reports a PWA whose mic button worked in Safari and stopped once added to the Home Screen; firt.dev's iOS PWA table (updated 13 Oct 2026) lists SpeechRecognition from 14.5 without separating installed apps; one site claims it works. Since iOS 26 every site added to the Home Screen opens as a web app | **unresolved; must be tried on the owner's iPhone** |
| `getUserMedia` in iOS Home Screen apps | firt.dev: supported since iOS 13; older WebKit bugs (185448, a 2024 report) describe breakage in standalone mode | [documented, test on device] |
| Screen Wake Lock | iOS 16.4 in Safari; iOS 18.4 for installed apps (firt.dev) | [documented]; the phone on the counter needs this so the screen, mic and page stay alive |
| `speechSynthesis` | iOS since 7; desktop Chrome has local OS voices plus "Google" network voices; quality varies by OS; iOS exposes downloaded enhanced voices [recalled]; Chrome cuts long utterances after about 15 s unless they are split [recalled] | mixed |
| Autoplay | iOS and Chrome need a user gesture before audio plays; unlock one `AudioContext` (or one `<audio>`) in the tap that starts the session and reuse it [recalled; common practice] | test on device |
| Mic and speaker together on iOS | opening the mic switches iOS to a play-and-record session; output can get quieter or move to the earpiece [recalled] | test on device |

What the browser path buys: no install, no key, no server cost, no audio passing through Factotum.
What it costs: behaviour differs per browser; there is no control over the model; biasing is
uncertain; audio may still go to Google or Apple; and it may not work at all in the installed iPhone
app.

---

## 7. Text-to-speech that runs locally

| Model | Licence | Voices | CPU speed | GPU | Notes | Label |
|---|---|---|---|---|---|---|
| **Kokoro-82M** v1.0 (27 Jan 2025) | Apache-2.0 | 54 voices, 8 languages; e.g. `af_heart` | Picovoice CPU benchmark (Ryzen 7 5700X): 3.66 s "first token to speech" while text streamed in from a small LLM; other reports range from about 5× to 30× real time on a CPU | fast; tiny model (needs about 2 to 3 GB VRAM in one guide) | `pip install kokoro>=0.9.2 soundfile` plus **`espeak-ng` system package** (apt, needs root); 24 kHz output; `kokoro-onnx` runs without torch [recalled]; no voice cloning | HF card [verified today]; speed [documented] |
| **Piper** | **GPL-3.0** since the move to OHF-Voice/piper1-gpl (old rhasspy/piper, MIT, archived Oct 2025); voices on HF under rhasspy/piper-voices | many, per language, VITS | Picovoice: 1.72 s FTTS on CPU (same LLM-streaming caveat); runs on a Raspberry Pi | not needed | GPL: for a tool meant to be handed to people, call it as a subprocess, not as an import. Factotum's `pyproject.toml` declares no licence and there is no LICENSE file [verified today] | [documented] |
| **Kyutai Pocket TTS** (Jan 2026, 100 M) | per Kyutai [not checked] | a few | 1.71 s FTTS on CPU (Picovoice), streaming | — | built for CPU | [documented] |
| Supertonic-2, Soprano, NeuTTS Nano | various | — | 1.7 to 3.0 s FTTS (Picovoice) | — | — | [documented] |
| Kitten TTS Nano, Chatterbox Turbo | MIT / MIT | — | 10.5 s / 48 s on CPU (no streaming) | Chatterbox is meant for a GPU | poor on CPU | [documented] |
| Picovoice Orca | proprietary, needs an access key | — | 106 ms FTTS | — | the vendor's own benchmark | [documented] |

Caveat on the Picovoice table: its latency includes waiting for an LLM to stream text. A complete short
sentence ("Task 412, email Dr. Lee about the TA schedule, due Saturday.") given to Kokoro or Piper at
once should start much sooner. My estimate is 0.2 to 0.8 s on this CPU and under 0.2 s for Kokoro on
the 3070 [estimate, untested]. This is the first thing to measure if a local TTS is chosen.

A calm assistant voice reading names and dates: Kokoro's American and British voices are widely judged
the best quality at its size [documented, community]. Any engine reads ISO dates and "Task-412" badly
unless the text is normalised first ("Saturday the twenty-first", "task four twelve"). Do that
normalisation in Python, once, for every provider.

---

## 8. Text-to-speech as a service

| Vendor / model | Price | Time to first audio | Streaming | Notes | Label |
|---|---|---|---|---|---|
| **ElevenLabs Flash / Turbo v2.5** | **$0.04 per 1K chars** | about 75 ms (vendor) | HTTP and WebSocket | 32 languages; v3 Conversational $0.04/1K, about 280 ms; v3 $0.08/1K; v2 Multilingual $0.08/1K | elevenlabs.io/pricing/api [verified today] |
| **Deepgram Aura-2** | **$0.030 per 1K chars** | about 90 ms steady-state TTFB, p95 under 200 ms (Deepgram blog); 246 ms in a competitor's test | yes | — | deepgram.com/pricing [verified today]; latency [documented] |
| **OpenAI tts-1 / tts-1-hd** | $15 / $30 per 1M chars | — | yes (chunked) | 2.85 s FTTS in Picovoice's test (which does not separate the models) | developers.openai.com pricing [verified today] |
| **OpenAI gpt-4o-mini-tts** | $0.60 per 1M text tokens + $12 per 1M audio tokens (about $0.015/min [recalled]) | — | yes | takes style instructions ("calm, unhurried") [recalled] | pricing [verified today] |
| **Google Gemini** `gemini-3.8-flash-tts` / `-flash-lite-tts` | $0.50 per 1M text in + $9 / $6 per 1M audio out **until 31 Dec 2026; doubles from 1 Jan 2027** | — | yes | free-tier data used to improve products | aistudio.google.com/docs/pricing [verified today] |
| **Cartesia Sonic** | 1 credit per char; Free 20K, Pro $5 for 100K, Startup $49 for 1.25M credits a month | not stated on the page (Sonic is marketed as about 40 to 90 ms [recalled]) | yes | plans, not pay-per-use | cartesia.ai/pricing [verified today] |
| Azure Neural / Neural HD | about $15 to $16 / $22 per 1M chars | 1.58 s (Picovoice) | yes | aggregators disagree | [documented, unconfirmed] |
| Amazon Polly | not researched (1.54 s in Picovoice) | | | | |

---

## 9. Realtime speech-to-speech APIs and voice-agent frameworks

| Option | What it is | Price | Fit for Factotum |
|---|---|---|---|
| **OpenAI gpt-live-1 / 1.1** (Live endpoint `v1/live/sessions`, in the API since Sep 2026 per one tutorial) | full-duplex voice model that hands reasoning and tools to a backend Responses model; Pipecat also supports client-side delegation with tools in your own backend | **$0.05/min active, $0.02/min standby**, plus backend model and tools | the voice and the turn-taking would be OpenAI's, and the "brain" would by default be an OpenAI model. Routing every decision to Claude means client-side delegation, so each turn becomes gpt-live → your server → `claude -p` (2 to 12 s) → gpt-live speaks. That keeps the slow step and adds a second model that can say things Claude did not decide |
| **OpenAI gpt-realtime-2.1 / -mini** | speech-to-speech with function calls | per 1M tokens: audio in $32 / $10, audio out $64 / $20 | about $0.02/min of the owner's speech and $0.08/min of the model's speech for 2.1 [estimate: my assumption of about 600 tokens/min in and 1,200/min out, recalled, not verified] plus context re-billed every turn. Same objection: the model deciding is not Claude |
| **Gemini Live** (`gemini-3.8-live`, `gemini-3.1-flash-live-preview`) | speech-to-speech with tools | $3 per 1M audio in ($0.005/min), $12 per 1M audio out ($0.018/min); thinking billed as text; a forum thread says context is billed again on every turn | same objection; free-tier audio is used to improve Google's products |
| Deepgram Voice Agent API | managed STT + LLM + TTS | $0.075/min standard, $0.163/min advanced | can bring your own LLM [recalled], but the LLM must be an HTTP endpoint, not `claude -p` |
| **Pipecat** (BSD-2-Clause) | Python pipeline framework: transports (SmallWebRTC, FastAPI WebSocket, Daily, LiveKit), VAD, STT, LLM, TTS; Anthropic LLM supported; local Whisper, Moonshine, Kokoro and Piper services; JS and React client SDKs; Python 3.11 to 3.14 | free | useful pieces (VAD and turn-taking, barge-in, transports), but it expects to own the conversation loop and call an LLM API. Factotum's LLM is a CLI subprocess, and decisions must end as staged batches. You would use Pipecat only as a transport and audio layer, which is more framework than this needs |
| **LiveKit Agents** (Apache-2.0; turn-detector model under the LiveKit Model License) | agent framework that needs a LiveKit server (self-hosted or cloud) | free plus a server | one more service to run; no Anthropic plugin listed on the README's front page |

**Is a turn-based pipeline good enough?** Yes, for this task. The pipeline is: record an utterance,
endpoint it with VAD, transcribe, interpret with the grammar first and Claude second, speak. "Decide
on a card" is a short command-and-response exchange, not a conversation where people talk over each
other. Where it feels slow:

1. **Free-form sentences that need Claude**: 2 to 4 s with a lean, tool-less prompt; 5 to 18 s if
   it has to look things up through tools (the Chat-turn numbers). Mitigations:
   - say an immediate short acknowledgement or play an earcon ("One moment.") while Claude runs;
   - stream Claude's reply (`stream-json`) and start speaking at the first complete sentence;
   - keep a dedicated `voice_turn` run kind on a lean prompt, with the card already inlined and no
     tools unless the owner asks a question that needs them.
2. **Endpointing**: a fixed 0.5 to 0.8 s silence wait adds directly to every turn. Too short, and a
   pause mid-sentence ("push it to… Monday") cuts the owner off. Model-based end-of-turn detection
   (Deepgram Flux, LiveKit's turn detector, Pipecat's smart-turn [recalled]) is the one thing the
   realtime stacks do better.
3. **Barge-in**: with half-duplex audio (the mic closed while the system speaks) the owner cannot
   interrupt by voice, only by tapping. Full-duplex needs echo cancellation that works with the
   phone on a counter. `getUserMedia({audio: {echoCancellation: true}})` helps, but kitchen noise
   plus speaker bleed will cause false triggers [recalled]. Recommend half-duplex with a large
   "stop" target first, and test barge-in later.

What realtime would buy: about 0.5 to 1 s less per turn and natural interruption. What it would cost:
Claude would no longer be the model that decides, `$0.05/min` or more ($30/month at 20 min/day for
gpt-live before backend costs), and a second model whose spoken words are not checked by Factotum.
**Not recommended.**

---

## 10. Where the time goes in one spoken turn (end of speech to first audio of the reply)

Measured today with four headless probes. Each run: `claude -p --model haiku|sonnet --output-format
json --max-turns 1 --setting-sources "" --disable-slash-commands --strict-mcp-config --tools ""`, with
a 70-word prompt (one card plus "push that to monday and remind me at nine", reply as JSON) piped on
stdin. Each run returned a correct JSON decision (due 2026-11-23, reminder 09:00) and a one-line
spoken confirmation.

| Run | Wall clock | `duration_ms` | API-equivalent cost |
|---|---|---|---|
| haiku, first (cold) | **15.73 s** | 2,623 | $0.00097 |
| sonnet | **1.83 s** | 1,015 | $0.0112 |
| haiku | **2.81 s** | 2,107 | $0.00052 |
| sonnet | **1.77 s** | 980 | $0.0038 |

The cold first run (15.7 s against 1.8 s warm) shows that the CLI's own start-up dominates after an
idle period. A voice session should start one throwaway run when the session opens, so the first real
turn is warm. Can a session keep one `claude` process alive with streaming input instead of a new
`-p` per turn? That is an open question (§14).

Budget per stage (warm):

| Stage | Browser | Local (this PC) | Paid |
|---|---|---|---|
| Endpointing (silence wait) | 0.5 to 1.0 s (browser's own) | 0.5 to 0.7 s (Silero VAD in the page) | 0.2 to 0.6 s (Flux end-of-turn, or VAD) |
| Upload of the clip | none (text only) | 0.1 to 0.3 s via the tunnel (160 KB) | streamed while speaking, about 0 |
| STT final | 0.3 to 0.8 s (server engine) | 0.2 to 0.5 s GPU turbo / 1.5 to 3 s CPU | 0.15 to 0.5 s |
| Interpretation: grammar hit | about 0.05 s | about 0.05 s | about 0.05 s |
| Interpretation: Claude, lean prompt | 1.8 to 2.8 s measured | same | same |
| TTS first audio | about 0.1 s (OS voice) | under 0.2 s Kokoro GPU / 0.2 to 0.8 s CPU [estimate] | 0.1 to 0.3 s + download |
| **Total, grammar turn** | **about 1.0 to 2.0 s** | **about 0.9 to 1.7 s (GPU)** | **about 0.6 to 1.4 s** |
| **Total, Claude turn** | **about 3 to 5 s** | **about 3 to 4.5 s** | **about 2.5 to 4 s** |

Speaking the next card can cost nothing at decision time: synthesise card N+1's summary while the owner
is still deciding on card N, and cache it.

---

## 11. Three complete stacks

Volume assumed for costs: 20 minutes of review a day, 30 days.

- The owner speaks about 5 minutes of that, in about 100 utterances a day, so 150 audio minutes a
  month.
- The system speaks about 8 minutes a day at about 900 characters a minute, so about 7,000 characters
  a day or 210,000 a month.
- About 30 of the 100 utterances a day need Claude; the rest hit the grammar.

### (a) Browser only

- **STT**: `SpeechRecognition` (Chrome: Google's servers unless on-device; Safari: Apple's service).
- **TTS**: `speechSynthesis` with an OS voice.
- **Install / sign-up**: nothing.
- **Leaves the machine**: the owner's audio to Google or Apple, decided by the browser, not Factotum.
  Only text reaches Factotum. Claude prompts leave as today.
- **Cost**: $0 for speech. Claude is on the subscription: about 30 lean runs a day at the measured
  $0.004 to $0.011 API-equivalent is about $4 to $10 a month equivalent; tool-using chat turns
  ($0.064 median) would be about $58 a month equivalent.
- **Latency**: grammar 1 to 2 s; Claude 3 to 5 s.
- **Degrades when**: there is no recognition API (Firefox, possibly the installed iPhone app). Then
  it becomes push-to-talk with server STT if configured, else text and buttons with spoken output
  only. No usable voice: read the card on screen and decide with the controls.
- **Quality**: fine for short commands on desktop Chrome; unknown biasing; robotic voices on some
  platforms.

### (b) All local on this machine

- **STT**: faster-whisper `large-v3-turbo` int8 or fp16 on the RTX 3070, Silero VAD (in the page via
  `@ricky0123/vad-web`, or server-side), `hotwords` from the card.
- **TTS**: Kokoro-82M on the GPU (or Piper on the CPU via a subprocess).
- **Install**, as an optional extra (e.g. `pip install factotum[voice-local]`), never a core
  dependency:
  - `faster-whisper` (pulls `ctranslate2`, `av`, `onnxruntime`, `tokenizers`), plus
    `nvidia-cublas-cu12` and `nvidia-cudnn-cu12==9.*` with `LD_LIBRARY_PATH` set in the unit;
  - `kokoro` + `soundfile` (pulls torch, about 2 GB with CUDA wheels) or `kokoro-onnx`;
  - `apt install espeak-ng`;
  - models: turbo about 1.6 GB, Kokoro about 330 MB.
  - None of this is installed now.
- **Leaves the machine**: no audio at all; only the Claude prompt.
- **Cost**: $0 plus electricity; the same Claude figures as (a).
- **Latency**: grammar 0.9 to 1.7 s; Claude 3 to 4.5 s.
- **Degrades when**:
  - GPU absent or busy: the CPU runs small.en, distil or turbo at 1.5 to 3 s per utterance.
  - Packages missing: fall back to the browser provider and say so once (as the starter banner does).
  - Model loading: the first turn waits 2 to 8 s, so load when the session starts, not at the first
    utterance.
- **Risks**: VRAM contention with the owner's other GPU work; loading a 1.5 to 3 GB model inside the
  uvicorn process (one process, one shared DB connection); systemd environment for CUDA.

### (c) Best quality with paid services

- **STT**: OpenAI `gpt-transcribe` per utterance with `keywords`, or Deepgram Flux streaming with
  keyterms for the fastest end-of-turn.
- **TTS**: ElevenLabs Flash v2.5 (or v3 Conversational for a warmer voice), or Deepgram Aura-2.
- **Sign-up**: one or two vendor accounts; keys in `.env` (new names in `.env.example`, e.g.
  `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`); the switch on in config, because it is
  a paid, outward-sending feature.
- **Leaves the machine**: the owner's audio (to the STT vendor); task names, people's names and dates
  as hints and as text to speak (to the TTS vendor).
- **Monthly cost** at the volume above (list prices read today):

  | Piece | Choice | Month |
  |---|---|---|
  | STT | OpenAI gpt-transcribe, 150 min × $0.0045 | $0.68 |
  | STT alt | Deepgram Nova-3 + keyterm, 150 min × $0.0056 | $0.84 |
  | STT alt | Deepgram Flux streaming the whole 20 min session + keyterm, 600 min × $0.0078 | $4.68 |
  | STT alt | Groq turbo, 3,000 requests × 10 s minimum = 500 min | $0.33 |
  | STT alt | ElevenLabs Scribe v3, 150 min | $0.50 |
  | TTS | ElevenLabs Flash, 210K chars × $0.04/1K | $8.40 |
  | TTS alt | ElevenLabs v3, $0.08/1K | $16.80 |
  | TTS alt | Deepgram Aura-2, $0.030/1K | $6.30 |
  | TTS alt | OpenAI tts-1 / tts-1-hd | $3.15 / $6.30 |
  | TTS alt | Gemini 3.8 Flash TTS (about 360K audio tokens) | about $3.24 to Dec 2026, about $6.48 after |
  | TTS alt | Cartesia | Startup plan $49 (Pro's 100K credits too few) |
  | **Typical total** | gpt-transcribe + ElevenLabs Flash | **about $9 a month**; with Deepgram for both, about $7 |

  Free credits cover months of this volume: Deepgram $200, AssemblyAI $50.
- **Latency**: grammar 0.6 to 1.4 s; Claude 2.5 to 4 s.
- **Degrades when**: there is no key or the vendor is down (an outward call failed: log it, fall back
  to local or browser, keep the card on screen); the session must never stall on a TTS error.

---

## 12. The provider seam

The smallest interface that lets config pick browser, local or a service for each direction:

```python
# factotum/voice/providers.py (sketch)
from dataclasses import dataclass
from typing import Protocol, Iterator

@dataclass
class Heard:
    text: str                 # final transcript, unnormalised
    ms: int                   # time spent
    confidence: float | None  # where the engine gives one (Whisper: from avg_logprob / no_speech_prob)

class Ear(Protocol):          # speech -> text
    name: str
    def ready(self) -> str | None: ...       # None = usable; else a sentence for Settings ("faster-whisper is not installed")
    def transcribe(self, pcm16: bytes, rate: int, *, hints: list[str], lang: str) -> Heard: ...

class Voice(Protocol):        # text -> speech
    name: str
    def ready(self) -> str | None: ...
    def speak(self, text: str, *, voice: str | None) -> Iterator[bytes]: ...  # chunks of one container (wav or mp3); mime from .mime
    mime: str

PROVIDERS = {"ear": {"browser": None, "local": ..., "openai": ..., "deepgram": ..., "groq": ...},
             "voice": {"browser": None, "local": ..., "openai": ..., "elevenlabs": ..., "deepgram": ...}}
```

Design points:

- **"browser" is a sentinel, not a server class.** The server tells the page which side does each
  direction (e.g. `GET /api/voice` → `{"ear": "browser"|"server", "voice": "browser"|"server",
  "hints": [...], "why_not": ...}`). With `ear: browser` the page posts text; with `ear: server` it
  posts base64 PCM in JSON (to satisfy `JsonOnlyMutations`, `api.py:243`). With `voice: server` it
  fetches `GET /api/voice/say?…` (a GET, so no 415), or the turn's JSON carries a URL for the audio.
- **Hints are built in Python only**, from the current card, the configured projects and the command
  verbs; capped (about 50 terms, about 200 tokens) and stripped of line breaks. They are never pasted
  from the page (CLAUDE.md: "Never paste a sentence the page wrote into a prompt").
- **Text normalisation for speaking lives above the seam**, shared by every provider: dates as words,
  "Task-412" as "task four twelve", no markdown, no URLs.
- **Spoken-number and date parsing lives above the seam too**, feeding the existing grammar
  (`commands.parse`, `dates.resolve_when`).
- **Paid providers are switches.** `voice.ear.provider` / `voice.voice.provider` absent means
  `browser` (absent means off for anything paid or outward-sending). That needs `config.DEFAULTS`
  entries, `SETTINGS_KEYS` if exposed in Settings, `CHOICES` for the select, the example YAML, the
  fixture, and the starter (CLAUDE.md conventions).
- **Local models load lazily, once, in a worker thread**, and never under `commit_lock`. Consider a
  separate small process (`python -m factotum voice-worker`) so a 2 GB model and its CUDA libraries
  stay out of the API process. That is an open design choice.
- **`ready()` reasons go to Settings**, the way `runner.note_unavailable` does for Claude.

Config sketch:

```yaml
voice:
  ear:   {provider: browser, model: large-v3-turbo, device: auto, language: en}   # browser | local | openai | deepgram | groq | elevenlabs
  voice: {provider: browser, name: af_heart, rate: 1.0}                            # browser | local | openai | elevenlabs | deepgram | google
  vad:   {silence_ms: 700}
```

---

## 13. Recommendations

**Fresh install (someone else's machine).** `ear: browser`, `voice: browser`. It needs nothing
installed and no key, and costs nothing. If the browser has no recognition (Firefox, possibly the
installed iPhone app), the page still speaks the card and offers the controls. The Settings line says
how to switch on a server provider.

**This owner.**

- **Ear**: `local`, faster-whisper `large-v3-turbo` on the 3070 with `hotwords` from the card and
  Silero VAD in the page. Fall back to OpenAI `gpt-transcribe` ($0.0045/min, `keywords`) if the GPU
  path proves fragile under systemd or competes with other GPU work. The browser is the last fallback.
  Server-side STT is chosen mainly because it works the same in the installed iPhone app, not because
  of accuracy.
- **Voice**: `local` Kokoro (`af_heart` or another calm voice) on the GPU, with Piper as a CPU
  subprocess fallback. If the owner wants a better voice, ElevenLabs Flash v2.5 (about $8.40 a month
  at the assumed volume) or Deepgram Aura-2 (about $6.30) are one config line away. The owner prefers
  quality over thrift; let him A/B three voices on real card text before choosing.
- **Interpretation**: a spoken grammar first (the existing verbs plus "this card", next, skip, back,
  repeat, stop, spoken numbers), then a lean `voice_turn` Claude run (Sonnet measured about 1.8 s warm,
  the card inlined, no tools), and the full chat turn with tools only for questions that need lookups.
- **Pre-warm** the Claude CLI and the local models when a session starts; pre-synthesise the next
  card.
- **Audio** half-duplex, with a big Stop / Next target and the screen wake lock on. Look at barge-in
  later.

---

## 14. Open questions and what I could not find out

1. **Does `SpeechRecognition` work in the installed Factotum app on the owner's iPhone (iOS 26)?**
   Sources conflict. The same question applies to `getUserMedia` in standalone mode and to playback
   volume while the mic is open. This needs a device test with a throwaway page before any design
   commits to browser STT on the phone.
2. **Which Chrome version shipped `phrases` biasing**, and does it work with server recognition or
   only on-device? Not found.
3. **Kokoro and Piper time to first audio for one short sentence on this CPU and GPU**: not measured
   (not installed; installing was out of scope). The Picovoice figures include waiting for streamed
   LLM text.
4. **faster-whisper `large-v3-turbo` int8 on this CPU and GPU**: not measured. I measured
   transformers large-v3 and small, which are slower; the turbo figures in §3.2 are estimates.
5. **Can the systemd service see the GPU and the pip CUDA libraries?** The libraries live in
   `/usr/lib/wsl/lib`, and `LD_LIBRARY_PATH` for the cuDNN wheel would be needed. Not tested.
6. **Can one long-lived `claude` process serve a whole voice session** (streaming JSON input, or the
   Agent SDK) to avoid the 15.7 s cold start and per-turn process start? Not researched. It would
   change `runner.py`'s one-process-per-run model.
7. **Licences unresolved**: Nemotron streaming EN (NVIDIA Open Model License vs OpenMDW), Moonshine v2
   (MIT per derived cards), Pocket TTS. Factotum itself declares no licence (`pyproject.toml` has none;
   there is no LICENSE file), which matters for GPL Piper.
8. **Not obtained from first-party pages**: Google Cloud STT (Chirp 3) and Azure Speech prices (pages
   did not render or showed none); OpenAI gpt-4o-mini-tts per-minute price (recalled about
   $0.015/min); the OpenAI realtime per-minute token rates.
9. **Whisper's invention of text on noise in a real kitchen**: needs a test with the actual phone mic
   and a running extractor fan. Mitigations: VAD gating, a `no_speech_prob` threshold, ignoring
   one-word "thank you" transcripts.
10. **Model end-of-turn detection** (Flux, LiveKit turn detector, Pipecat smart-turn) against a fixed
    silence timeout: is it worth the dependency to avoid cutting off "push it to… Monday"? Not
    evaluated.

---

## 15. Sources (accessed 2026-11-19)

First-party, read today:

- OpenAI pricing: https://developers.openai.com/api/docs/pricing
- OpenAI gpt-live-1 model page: https://developers.openai.com/api/docs/models/gpt-live-1
- Deepgram pricing: https://deepgram.com/pricing
- AssemblyAI pricing: https://www.assemblyai.com/pricing
- ElevenLabs API pricing: https://elevenlabs.io/pricing/api
- Cartesia pricing: https://cartesia.ai/pricing
- Groq speech-to-text docs: https://console.groq.com/docs/speech-to-text
- Google AI Studio / Gemini API pricing: https://aistudio.google.com/docs/pricing
- Claude features overview: https://platform.claude.com/docs/en/build-with-claude/overview
- Claude Code voice dictation: https://code.claude.com/docs/en/voice-dictation
- MDN SpeechRecognition (last modified 2026-11-16): https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition
- caniuse speech recognition: https://caniuse.com/speech-recognition
- firt.dev iOS PWA notes (updated 2026-10-13): https://firt.dev/notes/pwa-ios/
- faster-whisper README: https://github.com/SYSTRAN/faster-whisper
- faster-whisper transcribe.py: https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/faster_whisper/transcribe.py
- Parakeet TDT 0.6B v3 card: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3
- Kokoro-82M card: https://huggingface.co/hexgrad/Kokoro-82M
- Open ASR Leaderboard dataset view: https://huggingface.co/datasets/hf-audio/open-asr-leaderboard
- Pipecat: https://github.com/pipecat-ai/pipecat
- LiveKit Agents: https://github.com/livekit/agents
- Picovoice TTS benchmark (vendor-run, undated): https://picovoice.ai/docs/benchmark/tts/

Secondary, documented but not verified first-hand:

- Third-party OpenAI transcription pricing and deprecation: https://www.costbench.com/software/ai-transcription-apis/openai-whisper/ and https://www.developersdigest.tech/models/speech/openai
- gpt-transcribe launch: https://community.openai.com/t/gpt-live-transcribe-and-gpt-transcribe-two-new-transcription-models-in-the-api/1388318 and https://the-decoder.com/gpt-transcribe-improves-on-its-predecessor-but-cant-catch-elevenlabs-google-or-mistral-on-error-rates/
- gpt-live-1 tutorial (dates): https://flaviocopes.com/gpt-live-1/ ; Pipecat gpt-live delegation: https://docs.pipecat.ai/api-reference/server/services/s2s/openai-live
- Moonshine v2 paper: https://arxiv.org/html/2602.12241
- Nemotron Speech Streaming EN: https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b
- Voxtral Transcribe 2: https://mistral.ai/news/voxtral-transcribe-2
- Cohere Transcribe on the leaderboard: https://rits.shanghai.nyu.edu/ai/cohere-transcribe-2b-open-source-asr-model-takes-1-on-leaderboard/
- Piper licence move: https://github.com/OHF-Voice/piper1-gpl/releases and https://www.promptquorum.com/power-local-llm/piper-tts-review
- Pocket TTS: https://medium.com/@cooksusan482/kyutai-pocket-tts-100m-parameter-that-runs-on-your-cpu-6cae1fd812bf
- Deepgram Aura-2 TTFB: https://deepgram.com/learn/engineering-real-time-low-latency-voice-ai-at-scale
- Web Speech contextual biasing intent: https://groups.google.com/a/chromium.org/g/blink-dev/c/FAoeEf5hcTg ; on-device in Chrome 139: https://groups.google.com/a/chromium.org/g/chrome-ai-dev-preview-discuss/c/Rlyc2uAsFCY ; TPAC 2025: https://www.w3.org/2025/11/TPAC/demo-audio-wg-update.html
- WebKit bug 225298 (recognition in Home Screen apps): https://bugs.webkit.org/show_bug.cgi?id=225298 ; Apple forum: https://developer.apple.com/forums/thread/748048 ; getUserMedia standalone bug: https://bugs.webkit.org/show_bug.cgi?id=185448
- Gemini Live billing per turn: https://discuss.ai.google.dev/t/does-gemini-live-native-audio-bill-cumulative-prompt-tokens-on-every-turn-cost-seems-to-scale-with-turn-count-not-call-duration/173248
- Azure TTS prices (aggregators): https://perkstack.co/blog/cheapest-azure-tts-api and https://costbench.com/software/ai-voice-tools/microsoft-speech/
- Browser VAD (Silero in the page): https://docs.vad.ricky0123.com/user-guide/browser/
- Kokoro vs ONNX CPU timing: https://ocdevel.com/blog/20260711-quantized-cpu-tts
