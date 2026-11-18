# Voice-over studio project plan

## Goal

Turn customer scripts into voice-over audio using a clone of the owner's voice. Start with a private browser-based studio on the owner's Windows laptop. The owner reviews every recording and downloads the finished file for manual delivery. Self-host the studio once the local workflow is reliable. Add customer access, subscriptions, and automated fulfillment in Phase 2.

## Stack

| Area | Choice | Reason |
| --- | --- | --- |
| Local runtime | Python 3.11, `uv`, and PyTorch | Native Windows trial, with Linux available for self-hosting. Use a project-local environment and separate CPU and CUDA setup instructions. [uv installation](https://docs.astral.sh/uv/getting-started/installation/), [PyTorch wheels](https://pytorch.org/get-started/previous-versions/) |
| Voice cloning | Resemble AI Chatterbox Turbo | English-first zero-shot cloning with a short reference recording and built-in watermarking. The 350M-parameter Turbo model is designed to reduce compute and VRAM use. Resemble publishes both the implementation and Turbo weights under MIT. [Code license](https://github.com/resemble-ai/chatterbox/blob/master/LICENSE), [Turbo model card](https://huggingface.co/ResembleAI/chatterbox-turbo) |
| Website | FastAPI and Uvicorn, Jinja templates, vanilla JavaScript and CSS | One Python application for file intake, job status, and browser audio playback. The synthesis worker can use the same Python code headlessly. [FastAPI templates](https://fastapi.tiangolo.com/advanced/templates/) |
| Jobs and storage | SQLite and a private local data directory | Persist draft scripts, voice profile metadata, job status, and export records. Keep the actual audio in files. Run one inference job at a time on the laptop. [SQLite use cases](https://sqlite.org/whentouse.html) |
| Audio | SoundFile for native-rate PCM WAV and FFmpeg for export | Keep a lossless master and offer MP3 for delivery. Apply loudness and format changes to the assembled export using the client's spec where available. [FFmpeg loudness filter](https://ffmpeg.org/ffmpeg-filters.html#loudnorm) |

Download packages and model weights during setup. Synthesize with cached local files and offline mode. Operating costs for Phase 1 are hardware, storage, and electricity. No subscription or per-character inference fee is required. A permanently running self-hosted machine may add power or hosting costs.

## First milestone · Evaluate the owner's voice

The laptop reports an NVIDIA GeForce RTX 3050 Laptop GPU with 4 GB of VRAM. FFmpeg is available. CPU is the baseline and CUDA is a performance trial. Peak VRAM, speed, and recording quality have to be measured before choosing the everyday device and engine.

1. Record two clean samples of the owner's normal speaking voice. Start with 10–15 seconds of continuous speech per take in a quiet room at a consistent mic distance. Save as PCM WAV. Keep the reference and any transcripts in the private data directory.
2. Use the [voice trial](README.md#first-voice-trial) on a conversational line, an ad read, and a script with names, numbers, and acronyms. Generate at least two takes of each. Record the exact audio and model revision used along with the cold load time, synthesis time, audio duration, real-time factor, and peak CUDA memory when available.
3. Listen for resemblance, pacing, clean transitions, and script fidelity. The owner should recognize the voice and be willing to send an approved sample to a customer. A test read passes when all words are present, names and numbers sound right, and the take has no audible clipping or distracting artifacts. Compare the turnaround with a manual recording on the same script.
4. If CUDA runs out of memory, measure CPU turnaround and evaluate Chatterbox Nano as a smaller English candidate with a compatible upstream runtime. If resemblance or language coverage is the issue, compare Qwen3-TTS 0.6B Base, whose weights are Apache-2.0. Use the same owner recordings and scripts, supplying an accurate reference transcript for Qwen. Record the selected engine, revision, and device after the comparison. [Chatterbox Nano model card](https://huggingface.co/ResembleAI/chatterbox-nano), [Qwen model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base)

The public XTTS-v2 checkpoint is excluded from this paid workflow because its license restricts the model and its output to noncommercial use. [XTTS-v2 model license](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt)

The pilot CLI is in this checkout. Offline Windows CPU tests with a synthetic reference produced valid 24 kHz, 16-bit mono WAVs from both typed text and a `.txt` file. In one run the CPU generated 8.8 seconds of audio in 18.7 seconds, plus 6.8 seconds to load the model. CUDA performance remains to be measured.

The owner preferred `data/second-take.wav` for its emphasis and pauses. It came directly from the pinned Chatterbox Turbo checkpoint, using `chatterbox-tts` 0.1.7 on CPU, temperature `0.8`, seed `35`, and the owner's existing reference. The owner heard a mechanical quality in the pitch-shifted midpoint. A second listener familiar with the owner's voice found the midpoint closer in resemblance and heard more open vowels in the second take and more nasality in the higher-pitch take. Use the second take as the pacing benchmark. For a direct comparison of vowel sound and resonance, record the owner reading the same test passage in their normal voice.

## Phase 1 · Private studio

The local studio implements enrollment, pasted text and UTF-8 `.txt` import, section-by-section generation, review, retry, and WAV and MP3 downloads. It starts on loopback and keeps submitted scripts and recordings under the ignored `data/studio/` directory. Word and PDF import and private network hosting remain later steps.

### Build the operator workflow

- **Voice profile** takes the owner's reference WAV, validates duration and format, and keeps a normalized copy with an internal ID and revision. Let the owner replace the active reference without changing previous exports. Keep enrollment owner-only throughout both phases.
- **Script editor** accepts pasted text or a UTF-8 `.txt` upload. Show the full editable text and the exact version queued. Add `.docx` and text-based `.pdf` import with local parsers once the basic workflow is working. Show extracted text for review and tell the operator when a scanned PDF has no extractable text. Set explicit upload and character limits based on the benchmark.
- **Generation** splits longer scripts at paragraph and sentence boundaries, with a bounded fallback for long sentences. Preserve the ordered chunks and avoid silent truncation. The worker loads the engine once, conditions on the selected voice profile, and renders one job at a time. Record progress and actionable failures, and let the operator retry a failed chunk. After a restart, mark interrupted jobs for retry. Store the engine and reference revision with each job.
- **Review and export** provide browser playback for each chunk and for the assembled take. Let the operator correct pronunciation in the synthesis text or regenerate individual chunks. Assemble approved chunks in order with pauses and offer WAV and MP3. Use the source rate for the WAV master. Resample delivery copies only when requested. Keep the engine's watermarking enabled and preserve prior usable takes during regeneration.
- **History and storage** provide status, playback, download, and deletion for local jobs. Store uploads, generated audio, and SQLite under `data/` or an explicitly configured private directory. Expose files by internal IDs through app routes. Keep private files out of static assets, logs, and Git.

### Verify the workflow

- Open the app at `127.0.0.1`. Enroll a reference, generate from pasted and uploaded text, play the result, and download a valid WAV and MP3. Repeat after restarting the app and with runtime internet access disabled.
- Use a multi-paragraph script and verify that the first and last lines and every intervening chunk are present in the assembled file. Regenerate a single chunk and verify the other takes are preserved. Check that failed generation or export leaves any preceding valid download intact.
- Test empty and malformed uploads, size limits, unavailable weights, and a simulated model failure. Exercise deterministic features with a fake engine and run actual inference separately. Review final sound and export specs before delivering real customer work.

### Self-host privately after local validation

Run the same application and worker on a machine the owner controls. Start with owner access over a private VPN or an authenticated HTTPS reverse proxy, with persistent storage and a tested restore of the database and voice assets. Keep the worker reachable only by the app. Size the machine using measured synthesis time and storage use. Containerize when there is a target host and a verified GPU or CPU configuration.

## Phase 2 · Customer access and fulfillment

Keep the voice profile and inference worker private. Add a customer portal for account management, script submission, order status, and time-limited downloads. Add subscription entitlements and usage limits after measuring job costs and capacity. Use a payment provider for payment collection, with idempotent webhook handling. Provider fees are separate from model inference costs.

Start with owner approval before delivery. Define permitted uses, customer disclosure, retention, and an owner-controlled way to pause future generation before expanding automated fulfillment. Add per-customer access checks, abuse controls, and job quotas before opening customer access. Migrate from SQLite to PostgreSQL if multiple workers or server instances need concurrent writes. Add object storage if local disk no longer meets access or backup needs.
