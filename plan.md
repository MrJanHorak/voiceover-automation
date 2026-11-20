# Voice-over studio project plan

## Goal

Turn customer scripts into voice-over audio using a clone of the owner's voice. Start with a private browser-based studio on the owner's Windows laptop. The owner reviews every recording and downloads the finished file for manual delivery. Self-host the studio once the local workflow is reliable. Add customer access, subscriptions, and automated fulfillment in Phase 2.

## Current handoff · 2026-11-19

The 2026-11-19 review found that the code is sound but that several details limit realism. The first fixes are implemented and not yet heard by the owner:

- **Section joins.** New recordings trim each section's leading and trailing silence, keep 30 ms before and 80 ms after the speech, and fade the edges. Before this change, the measured gaps varied from about 0.36 to 0.85 seconds because each generated section has 12–199 ms of leading and 185–486 ms of trailing silence. The pause setting is now the full gap. Recordings created before this change keep their original joins; the `trim_edges` job column records which behavior applies.
- **Pause defaults.** Sections now get 350 ms after a sentence, 750 ms after a paragraph and 150 ms where an overlong sentence is wrapped. Prepared sections created from blank lines use the paragraph pause.
- **Sentence splitting.** Abbreviations such as Dr., Jan. 5, p.m., e.g. and the U.S. no longer end a section. An abbreviation followed by a capitalized word is still treated as a sentence end.
- **Original references.** New uploads keep the untouched WAV under `data/studio/voices/originals/` for future fine-tuning. The eight references saved before this change exist only as 24 kHz mono copies; the original `data/reference.wav` is still on disk.
- **MP3 export** now uses 192 kbps instead of 96 kbps.

New model-free tests are in `tests/test_joins.py`; the long-workflow test now expects the paragraph pause. Run the full suite on Windows with the studio environment. The owner should listen to a new multi-paragraph recording and adjust the pause constants in `studio/store.py` if needed.

### Earlier handoff · 2026-11-18


The private local studio is implemented and the owner reports that it works, including Word document import. The latest changes add Chatterbox advanced controls with explanations and per-take settings, plus section take history and restoration. These changes were exercised with isolated browser tests and 12 automated tests; actual Chatterbox CPU inference with custom settings used a synthetic reference. The owner has not yet reported a listening comparison of the new GPU VoxCPM2 or advanced settings. Do not treat technical test success as confirmation that the generated voice is ready for customer delivery.

The **multi-sample reference builder** can select ordered excerpts from saved references, preview them, and save a new reference without changing the originals. The owner heard a difference between a combined and a single reference but has not chosen a preference; further listening experiments are needed. Compare a combined 10–15 second sample against the best single reference with the same script and settings; the owner and a familiar listener should assess resemblance, vowels, pacing and artifacts. More samples may help but do not guarantee improvement. Chatterbox and the current VoxCPM2 integration each receive one reference WAV per job. Chatterbox uses only the first 10 and 15 seconds for certain conditioning components, although another speaker embedding uses the reference more broadly. A combined WAV is not permanent training or a multi-reference model.

The long-script workflow was checked with four synthetic sections: a model-free test verified ordering, WAV and MP3 preservation after a simulated export failure, and retry. A separate offline Chatterbox CPU run with a synthetic tone reference generated four sections and valid 24 kHz mono WAV and MP3 exports in about 69 seconds. This does not verify pronunciation or resemblance. The owner still needs to listen through a longer customer-style script, including the beginning and end, and assess the optional compressed GPU VoxCPM2 against the CPU version. Private hosting, backup/restore, authentication and deployment come after the local workflow is validated; subscriptions and automation remain Phase 2. Preserve the owner's voice samples, scripts, recordings, database and existing exports. Never use them as test fixtures without explicit authorization; use synthetic fixtures. No reference samples or model files should be committed.

For a new chat: open this repository, read `AGENTS.md`, this plan and `README.md`, then inspect `git status` before making changes. The working tree currently contains uncommitted implementation and documentation work; preserve it and do not assume it was committed or pushed. Local models, environments, verification artifacts and private data are ignored by Git; the `tests/` source files are not. A new chat in this same checkout can use local files; another machine or a fresh clone needs its own private data and model setup. Do not restart a running studio during a job. To use new code, finish any active job, restart using the desired command in the README, and reload the browser.

## Stack

| Area | Choice | Reason |
| --- | --- | --- |
| Local runtime | Python 3.11, `uv`, and PyTorch | Native Windows trial, with Linux available for self-hosting. Use a project-local environment and separate CPU and CUDA setup instructions. [uv installation](https://docs.astral.sh/uv/getting-started/installation/), [PyTorch wheels](https://pytorch.org/get-started/previous-versions/) |
| Voice cloning | Resemble AI Chatterbox Turbo | English-first zero-shot cloning with a short reference recording and built-in watermarking. The 350M-parameter Turbo model is designed to reduce compute and VRAM use. Resemble publishes both the implementation and Turbo weights under MIT. [Code license](https://github.com/resemble-ai/chatterbox/blob/master/LICENSE), [Turbo model card](https://huggingface.co/ResembleAI/chatterbox-turbo) |
| Website | FastAPI and Uvicorn, Jinja templates, vanilla JavaScript and CSS | One Python application for file intake, job status, and browser audio playback. The synthesis worker can use the same Python code headlessly. [FastAPI templates](https://fastapi.tiangolo.com/advanced/templates/) |
| Jobs and storage | SQLite and a private local data directory | Persist submitted scripts, voice profile metadata, job status, and export records. Keep the actual audio in files. Run one inference job at a time on the laptop. [SQLite use cases](https://sqlite.org/whentouse.html) |
| Audio | SoundFile for native-rate PCM WAV and FFmpeg for export | Keep a lossless master and offer MP3 for delivery. Apply loudness and format changes to the assembled export using the client's spec where available. [FFmpeg loudness filter](https://ffmpeg.org/ffmpeg-filters.html#loudnorm) |

Download packages and model weights during setup. Synthesize with cached local files and offline mode. Operating costs for Phase 1 are hardware, storage, and electricity. No subscription or per-character inference fee is required. A permanently running self-hosted machine may add power or hosting costs.

## First milestone · Evaluate the owner's voice

The laptop reports an NVIDIA GeForce RTX 3050 Laptop GPU with 4 GB of VRAM. FFmpeg is available. The tested CUDA configuration accelerates Chatterbox Turbo, and an optional CUDA backend is available for VoxCPM2.

The optional [audio.cpp](https://github.com/0xShug0/audio.cpp) v0.9.4 CUDA backend uses the [VoxCPM2 Q8 GGUF](https://huggingface.co/audio-cpp/audio.cpp-gguf/blob/6f45df3/VoxCPM2-GGUF/voxcpm2-q8_0.gguf); the CPU backend remains the default. A separate GPU test generated 11.52 seconds of audio in 22.81 seconds using a synthetic reference, with approximately 3,951 MiB of GPU memory sampled at peak. A 30-second synthetic reference and switching from Chatterbox to the GPU directed engine and back also worked. The studio applies its existing watermark and releases Chatterbox's GPU memory before a directed section. Compare the owner's voice and instruction following with the CPU version before customer use, and verify longer projects under real conditions.

1. Record two clean samples of the owner's normal speaking voice. Start with 10–15 seconds of continuous speech per take in a quiet room at a consistent mic distance. Save as PCM WAV. Keep the reference and any transcripts in the private data directory.
2. Use the [voice trial](README.md#first-voice-trial) on a conversational line, an ad read, and a script with names, numbers, and acronyms. Generate at least two takes of each. Record the exact audio and model revision used along with the cold load time, synthesis time, audio duration, real-time factor, and peak CUDA memory when available.
3. Listen for resemblance, pacing, clean transitions, and script fidelity. The owner should recognize the voice and be willing to send an approved sample to a customer. A test read passes when all words are present, names and numbers sound right, and the take has no audible clipping or distracting artifacts. Compare the turnaround with a manual recording on the same script.
4. If CUDA runs out of memory, measure CPU turnaround and evaluate Chatterbox Nano as a smaller English candidate with a compatible upstream runtime. If resemblance or language coverage is the issue, compare Qwen3-TTS 0.6B Base, whose weights are Apache-2.0. Use the same owner recordings and scripts, supplying an accurate reference transcript for Qwen. Record the selected engine, revision, and device after the comparison. [Chatterbox Nano model card](https://huggingface.co/ResembleAI/chatterbox-nano), [Qwen model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base)

The public XTTS-v2 checkpoint is excluded from this paid workflow because its license restricts the model and its output to noncommercial use. [XTTS-v2 model license](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt)

The pilot CLI is in this checkout. Offline Windows tests with the same synthetic reference, model revision, seed, and three scripts compared Chatterbox Turbo on CPU and the RTX 3050. On the longest 195-character passage, the CPU generated 10.28 seconds of audio in 23.78 seconds and the GPU generated 10.72 seconds in 4.40 seconds. Audio duration varied slightly between devices. Peak PyTorch GPU memory reserved across the three GPU takes was 3,158 MiB. The GPU test loaded the model in 7.57 seconds and prepared the reference in 12.45 seconds. A separate isolated studio run produced valid WAV and MP3 files using CUDA. These timings are a local sample, and longer sessions, scripts, or other programs using the GPU may change performance. Tests used `chatterbox-tts` 0.1.7, checkpoint `749d1c1`, and PyTorch 2.6.0 with CUDA 12.6.

The owner preferred `data/second-take.wav` for its emphasis and pauses. It came directly from the pinned Chatterbox Turbo checkpoint, using `chatterbox-tts` 0.1.7 on CPU, temperature `0.8`, seed `35`, and the owner's existing reference. The owner heard a mechanical quality in the pitch-shifted midpoint. A second listener familiar with the owner's voice found the midpoint closer in resemblance and heard more open vowels in the second take and more nasality in the higher-pitch take. Use the second take as the pacing benchmark. For a direct comparison of vowel sound and resonance, record the owner reading the same test passage in their normal voice.

## Phase 1 · Private studio

The local studio implements enrollment, pasted text, UTF-8 `.txt`, Word `.docx`, and text-based PDF import, section-by-section generation, review, retry, and WAV and MP3 downloads. It starts on loopback and keeps submitted scripts and recordings under the ignored `data/studio/` directory. Private network hosting remains a later step.

### Build the operator workflow

- **Voice profile** takes the owner's reference WAV, validates duration and format, and keeps a normalized copy with an internal ID and revision. Let the owner replace the active reference without changing previous exports. Keep enrollment owner-only throughout both phases.
- **Script editor** accepts pasted text and UTF-8 `.txt`, `.docx`, and text-based `.pdf` uploads. It shows extracted text for review, flags PDFs with no extractable text, and limits files to 10 MB and scripts to 12,000 characters.
- **Generation** splits longer scripts at paragraph and sentence boundaries, with a bounded fallback for long sentences. Preserve the ordered chunks and avoid silent truncation. The worker conditions on the selected voice profile and renders one job at a time. Record progress and actionable failures, and let the operator retry a failed chunk. After a restart, mark interrupted jobs for retry. New section audio records its actual engine, device, runtime, seed, and primary model hash; each job retains its original reference ID. Older sections retain unknown engine details rather than inferred values.
- **Review and export** provide browser playback for each chunk and for the assembled take. Let the operator correct pronunciation in the synthesis text or regenerate individual chunks. New section takes retain their audio, words, instructions, and available generation details. Operators can compare takes and restore one, rebuilding the WAV and MP3 without synthesizing again. Earlier unindexed audio cannot be reliably attributed to a take. Assemble approved chunks in order with pauses and offer WAV and MP3. The current studio converts output to 24 kHz mono; configurable delivery formats and native-rate masters remain future work. Keep the engine's watermarking enabled and preserve prior usable takes during regeneration.
- **History and storage** provide status, playback, download, and deletion for local jobs. Store uploads, generated audio, and SQLite under `data/` or an explicitly configured private directory. Expose files by internal IDs through app routes. Keep private files out of static assets, logs, and Git.

### Verify the workflow

- Open the app at `127.0.0.1`. Enroll a reference, generate from pasted and uploaded text, play the result, and download a valid WAV and MP3. Repeat after restarting the app and with runtime internet access disabled.
- Use a multi-paragraph script and verify that the first and last lines and every intervening chunk are present in the assembled file. Regenerate a single chunk and verify the other takes are preserved. Check that failed generation or export leaves any preceding valid download intact.
- Test empty and malformed uploads, size limits, unavailable weights, and a simulated model failure. Exercise deterministic features with a fake engine and run actual inference separately. Review final sound and export specs before delivering real customer work.

### Delivery controls

1. **Extra pause after section** adjusts the gap from 0 to 5,000 ms and rebuilds the WAV and MP3 from the existing section recordings. New recordings trim section edge silence, so the value is the full gap: 350 ms after a sentence and 750 ms after a paragraph by default. Older recordings keep the untrimmed joins and their 160 ms default.
2. **Insert sound** offers `[chuckle]`, `[laugh]`, and `[sigh]` in the script or a section. Resemble documents these vocal sounds for Turbo. The owner found that whispering, dramatic, and narration tags gave weak or unclear results, and the installed Turbo engine ignores `exaggeration`. [Supported sounds](https://huggingface.co/ResembleAI/chatterbox-turbo/discussions/21), [Turbo implementation](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/tts_turbo.py)
3. **Saved references** lets the owner name new samples and switch between previously saved ones. Compare conversational, warm, dramatic, and whispered recordings using the same script, then choose the references that carry over the intended delivery while retaining the owner's voice.
4. **Special instructions** uses local VoxCPM2 for directed reads. Keep the reference and script fixed while comparing neutral, whispered, subtly dramatic, and strongly dramatic directions. Evaluate resemblance, style adherence, and turnaround before applying a style to customer work. Sections with blank instructions use Chatterbox Turbo. [VoxCPM2 controllable cloning](https://huggingface.co/openbmb/VoxCPM2#controllable-voice-cloning)
5. **Advanced Chatterbox settings** controls seed, temperature, Top P, Top K, repetition penalty, and reference loudness normalization. Each control includes a plain-language explanation. The settings and resolved seed are stored with new takes, and section settings can be changed when regenerating. The installed Turbo implementation ignores Min P; do not expose it as an active control. Reference normalization is separate from the studio's output loudness balancing.

## Roadmap · realism and automation

Work in this order. Each step should be heard and compared with the previous best before the next one starts.

1. **Done, pending listening:** section edge trimming, sentence and paragraph pauses, abbreviation-aware splitting, kept original references and 192 kbps MP3.
2. **Maximum-resemblance mode.** Add VoxCPM2 "ultimate cloning", which passes the reference audio and its exact transcript (`prompt_wav_path`, `prompt_text`, plus the same file as `reference_wav_path`). Store a transcript with each reference, drafted by local speech recognition and corrected by the owner. Compare against Chatterbox and controllable cloning on the same scripts.
3. **Text normalization and pronunciation list.** Expand numbers, currency, dates, phone numbers and units before synthesis, and keep an owner-edited list of word replacements that applies to every recording.
4. **Delivery quality.** Keep a 48 kHz master (VoxCPM2 outputs 48 kHz; it is currently resampled to 24 kHz), add a final loudness and true-peak pass with selectable targets such as −16 LUFS and −23 LUFS, add crossfades to combined references, and render Chatterbox sections before directed ones to avoid reloading models on the 4 GB GPU.
5. **Automatic checks.** Transcribe each new section locally and compare it with the script to catch skipped, repeated or invented words; score speaker similarity against the reference; flag clipping and unusual length. Then generate two or three seeds per section and keep the best-scoring take automatically, marking only failures for review.
6. **Fine-tuning dataset builder.** Present sentences to read, record them in the browser at 48 kHz, trim and transcribe automatically, and export a training set (start with 30–60 minutes; 2–3 hours for the best result). Keep this owner-only and outside Git.
7. **Fine-tune.** Train a VoxCPM2 LoRA first (officially supported from 5–10 minutes of audio), then evaluate a Chatterbox Turbo fine-tune with the community toolkit. Use the fine-tune together with a reference. Record the base revision, data size, training settings and license with each adapter, and compare blind against zero-shot results.
8. **Hands-off intake.** A watched folder or local API turns a script file into a checked WAV and MP3 for owner approval. Customer access and billing remain Phase 2.

Avoid engines whose weights are licensed for noncommercial use only, such as XTTS-v2, F5-TTS and the Fish/OpenAudio releases, for paid work.

## Hosting options for training and rendering (researched 2026-11-19, not deployed)

The RTX 3050's 4 GB cannot train VoxCPM2 (about 8 GB of VRAM just for inference). Rent a GPU only for training and heavy batch rendering, and keep everyday studio rendering local until volume justifies more.

| Use | Recommended | Indicative price | Notes |
| --- | --- | --- | --- |
| LoRA training runs (a few hours each) | RunPod Pod, RTX 4090 24 GB or A6000/L40S 48 GB | 4090 about $0.34/h Community, $0.89/h Secure after the October 2026 increase; A6000 about $0.59/h; L40S about $1.09/h | Per-second billing, no egress fees. Secure Cloud for voice data. Network volumes are about $0.07/GB/month and keep billing while stopped. |
| Cheapest experiments | Vast.ai marketplace, RTX 4090 | Advertised from about $0.13/h; median about $0.36–0.47/h | Hosts are third parties with variable reliability. Use only verified datacenter hosts for private voice data, or use synthetic data for tests. |
| On-demand rendering API later | Modal serverless, L4 24 GB or A10 | About $0.80/h (L4) or $1.10/h (A10) billed only while running; $30 free credit a month on Starter | Scales to zero; cold starts add delay. Fits occasional renders without a running server. |
| Always-on private studio later | A machine the owner controls with a 24 GB GPU, or a reserved cloud GPU | A rented 4090 running 24/7 costs roughly $250–650 a month | Revisit after measuring real job volume. |

A typical LoRA run on 1–3 hours of audio should take a few hours on a 24 GB GPU, so each training attempt costs a few dollars of GPU time; data preparation is the larger effort. Prices change often; check before renting. Encrypt or delete voice data and adapters on rented machines after each run.

### Self-host privately after local validation

Run the same application and worker on a machine the owner controls. Start with owner access over a private VPN or an authenticated HTTPS reverse proxy, with persistent storage and a tested restore of the database and voice assets. Keep the worker reachable only by the app. Size the machine using measured synthesis time and storage use. Containerize when there is a target host and a verified GPU or CPU configuration.

## Phase 2 · Customer access and fulfillment

Keep the voice profile and inference worker private. Add a customer portal for account management, script submission, order status, and time-limited downloads. Add subscription entitlements and usage limits after measuring job costs and capacity. Use a payment provider for payment collection, with idempotent webhook handling. Provider fees are separate from model inference costs.

Start with owner approval before delivery. Define permitted uses, customer disclosure, retention, and an owner-controlled way to pause future generation before expanding automated fulfillment. Add per-customer access checks, abuse controls, and job quotas before opening customer access. Migrate from SQLite to PostgreSQL if multiple workers or server instances need concurrent writes. Add object storage if local disk no longer meets access or backup needs.
