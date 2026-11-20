# Voice-over studio project plan

## Goal

Turn customer scripts into voice-over audio using a clone of the owner's voice. Start with a private browser-based studio on the owner's Windows laptop. The owner reviews every recording and downloads the finished file for manual delivery. Self-host the studio once the local workflow is reliable. Add customer access, subscriptions, and automated fulfillment in Phase 2.

## Current handoff · 2026-11-19 (end of day)

### Resume here

All code from 2026-11-19 is committed (latest code commit "add auto checks of generated text…"). This end-of-day update to `plan.md`, `README.md` and `AGENTS.md` was the only uncommitted change at handoff; run `git status` to confirm it was committed. The Whisper model for automatic checks finished downloading to `models/whisper-small.en`: `model.safetensors` is 966,992,008 bytes, matching its header.

**Status on 2026-11-20:** the steps below were completed (see the 2026-11-20 session notes under the change log). What remains is listening to the redesigned studio and the pronunciation fix, then roadmap step 6.

**Start of a session, in PowerShell from the project folder:**

1. Confirm the download: `dir models\whisper-small.en` should list `model.safetensors`, `config.json` and `preprocessor_config.json`. If files are missing, rerun the README download command; it resumes.
2. Run the tests: `.\.tools\directed-env\Scripts\python.exe -m unittest discover -s tests`. They have not yet been run on Windows since the 2026-11-19 changes; report any failure.
3. Start the GPU studio:
   ```powershell
   $env:VOICEOVER_DEVICE = 'cuda'
   $env:VOICEOVER_DIRECTED_DEVICE = 'cuda'
   .\.tools\cuda-benchmark-env\Scripts\python.exe -m uvicorn studio.app:app --host 127.0.0.1 --port 8000 --no-access-log
   ```
4. Confirm that **If a section fails its automatic check** is selectable under the script (it is greyed out when the model is missing).
5. Open the "number test" recording and select **Check recording**. Section 2 ("The Pro plan costs…", garbled at the start) should show **Needs review**. Whisper and the voice encoder have never run on this computer, so watch the terminal for errors on this first check.
6. Regenerate that section with the instruction "energetic, upbeat TV commercial announcer, clear and confident", and once with the instruction cleared (maximum resemblance). Compare under **All takes**.
7. Note the voice-match scores of takes that sound right and wrong, to calibrate the provisional 0.6 threshold (`MIN_SIMILARITY` in `studio/quality.py`).

**Still to hear or confirm from 2026-11-19:**

- The "Short and sweet. That's the goal." gap after the pause cap; the −16 LUFS export after the limiter fix (the Recording panel should show about −16.0 LUFS, mode linear); number handling with the test lines below.
- Whether 350 ms after a sentence and 750 ms after a paragraph feel right (`SENTENCE_PAUSE_MS`, `PARAGRAPH_PAUSE_MS` in `studio/store.py`).
- That the CPU and GPU maximum-resemblance paths both sound right; the GPU path works.

Number test lines:

```
Our spring sale starts on Mar. 3, 2027 at 10:30 a.m. and runs through the 15th.

The Pro plan costs $1,299.99, about 40% less than last year, and the Basic plan is just $19.

Call us at (555) 123-4567 or visit www.example.com. We've served customers since 1998, from 2020-2026 alone over 12,000 of them.
```

**Next roadmap step:** step 6, the fine-tuning dataset builder (see the roadmap below). Then step 7: train a VoxCPM2 LoRA on a rented GPU (see the hosting table). audio.cpp v0.9.4 can load a LoRA adapter through its `lora` session option, so a trained adapter may run on the existing GPU path.

**Working notes for a new chat:**

- Read `AGENTS.md`, this section and `README.md`, then run `git status`. Preserve the owner's voice samples, recordings, database and exports; never use them as test fixtures. Look at them only read-only and only for diagnosis.
- The cloud workspace cannot install packages or download models, and file deletion in the project folder needs the owner's permission. Run tests in a scratch folder outside the project when temporary-directory cleanup would need deletion.
- Some tests import FastAPI, soundfile or torch and run only in the Windows environments. The model-free tests (`test_joins`, `test_spoken_text`, `test_quality`, `test_voice_match`, `test_delivery`) mostly run anywhere with numpy and FFmpeg.
- Windows terminal noise to ignore: `ConnectionResetError [WinError 10054]`, the `pkg_resources` and `weight_norm` warnings, and VoxCPM2 progress bars stopping at 20–35%.
- Do not upgrade huggingface_hub or other pinned packages without testing; the `hf download --include` form fetched nothing in 1.33, so the README uses `snapshot_download`.

### 2026-11-20 session

- **First real run of automatic checks.** Whisper and the voice encoder ran on the owner's GPU studio. All Windows tests passed (54). The "Self-check test" recording exported at −16.0 LUFS in linear mode, confirming the limiter fix. Voice match for good sections was 0.94–0.98.
- **Pronunciation respellings.** "noo-VEX-uh" caused a gap and, in a directed section, a spoken "dash". `speakable()` in `studio/normalize.py` now joins hyphenated or mixed-case respellings into one lowercase word ("noovexuh") before synthesis; spaced letters and all-capital acronyms are unchanged, as are hyphenated words in the script itself.
- **Check false alarms fixed.** A pronunciation-list word can never match its transcript, so `expected_text()` turns listed words into open words that match up to three heard words. The voice match is ignored for clips under 3 seconds (a good 2-second take scored 0.59), and its threshold is now 0.80 based on the owner's scores.
- **Interface redesign.** Three pages: **New recording** (steps: Script, Settings, then the recording opens under Recordings), **Recordings** (history; player, downloads, loudness and actions; a compact section list with status dots and a **Needs review** filter; one section editor with previous/next, takes as tabs, and **More options** for pauses, engine details and Chatterbox settings), and **Voice and words** (references, transcripts, combining excerpts, pronunciations). The page is remembered in the address (`#create`, `#recordings`, `#voice`). Checked with a mock backend and screenshots at desktop and narrow widths in the cloud workspace; the owner has not used it yet.

- **Owner feedback on the redesign (same day) and second pass.** Adding pronunciations and previewing spoken words were not intuitive, the preview was an unformatted block, and preparing sections was long and overwhelming. Changes:
  - The Script step has an **Edit / Preview spoken** toggle. The preview replaces the script box with paragraphs where changed words are highlighted (automatic in green, pronunciation list in purple, original on hover); selecting any word fills the pronunciation form. `/api/spoken-preview` now also returns `paragraphs` from `spoken_paragraphs()` (difflib word alignment per paragraph).
  - **Pronunciations** sit under the script: chips for saved words that occur in it, and a one-line add form. The full list stays on Voice and words.
  - **Prepare sections** was removed. The Settings step has **Delivery style: Whole script / Per paragraph**; per paragraph shows one row per paragraph with a style menu (Default, examples, Custom). Long paragraphs still split into several sections, all with the paragraph's style. "Special instructions" is renamed "Delivery style" throughout the interface.
  - A **Guide** page, a dismissible first-visit welcome (remembered in browser storage when available), and **?** tips on the main controls.
  - Checked with the mock backend and screenshots; tests for `spoken_paragraphs` added.

Next for the owner: restart the studio, try the Script preview, a per-paragraph style and the Guide, regenerate the Nuvexa and Pro plan sections, and report anything still confusing. Then continue with roadmap step 6 (dataset builder).

### First LoRA training run · 2026-11-20 (roadmap step 7, trained; listening pending)

- The run used Runpod pod `w8emlwn48mo89p`: an NVIDIA A40 48 GB on Secure Cloud in EU-SE-1, at $0.59 an hour, with the `runpod-torch-v280` template (torch 2.8.0+cu128). The pod was deleted on the owner's approval at about 16:45 New York time, and the account now has no pods. Total cost was about $0.35, leaving a balance of $9.65.
- **Bug found:** the trainer resolves `audio` paths against its working folder, not against the jsonl's folder. `train_on_pod.sh` now rewrites both lists to absolute paths before training.
- **Training:** 300 steps took about 14 minutes, at about 2 seconds a step plus audio previews every 25 steps; 300 steps is about 27 passes over the data. Validation results:

  | Step | Total loss | Diff loss | Stop loss |
  | ---: | ---: | ---: | ---: |
  | 0 | 0.947 | 0.796 | 0.150 |
  | 50 | 0.870 | 0.786 | 0.084 |
  | 100 | 0.871 | 0.764 | 0.107 |
  | 150 | 0.887 | 0.756 | 0.131 |
  | 200 | 0.891 | 0.751 | 0.139 |
  | 225 | 0.926 | 0.772 | 0.155 |
  | 299 | 0.950 | 0.775 | 0.175 |

  The diff loss is best around step 200, while the stop loss gets worse after step 50, because training data is overfitting on when to stop. Steps 150–200 are the expected sweet spot. If a next run is needed, use a lower learning rate or fewer steps (about 150), or more recordings.
- **Outputs**, which the owner downloads from the pod's Jupyter before deletion:
  - `voice-samples.zip` (12 MB): 34 clips, including baseline, `step_*_alone_{1,2}` and `step_*_with-reference_{1,2}`.
  - `voice-lora-results.zip` (479 MB): `lora_config.json` and `lora_weights.safetensors` (70 MB each) for steps 50–300, plus the samples and `train.log`.
  - Both are kept out of Git.
- **Next:** the owner picks a step by ear. Then the studio loads that adapter, on CPU via `VoxCPM.from_pretrained(lora_config=..., lora_weights_path=...)` and on GPU via audio.cpp's `lora` session option, followed by a blind comparison with zero-shot takes.

### Rented-GPU training kit · 2026-11-20 (roadmap step 7)

The owner recorded and exported 184 clips (19.6 minutes; 175 train, 9 validation, 2.3–15.3 seconds each) to `data/studio/dataset/exports/2026-11-20_154735`. The `training/` folder holds the kit:

- `voxcpm2_lora.yaml`: keys from the VoxCPM fine-tuning guide (16 kHz, batch 16, max 8,192 batch tokens, learning rate 1e-4, LoRA r=32 and alpha=32 on the LM and DiT). It runs 300 steps with checkpoints every 50, because about 175 clips at batch 16 is roughly 11 steps per pass, and the guide suggests 1–3 passes for one speaker while small runs often need more. Checkpoints are compared by ear.
- `train_on_pod.sh`: installs VoxCPM from GitHub (tag 2.0.3 when present), downloads VoxCPM2 revision `7936080b41163874f38610f46f438e2c0757f9fd`, trains, runs `make_samples.py`, and zips adapter weights and settings plus samples to `voice-lora-results.zip`.
- `make_samples.py`: for each checkpoint, two test sentences with no reference and with the reference, plus a baseline without the adapter. It passes the checkpoint's own `lora_config.json`, because the library's default `LoRAConfig` uses r=8.
- `make_kit.py`: zips the newest export, the kit and the active reference. The first kit is `2026-11-20_154735-voice-training-kit.zip` (28 MB).

Per the guide, LoRA needs about 20 GB of VRAM; a 48 GB RTX A6000 is recommended, or a 4090 at batch 8. The owner added Runpod as a custom connector (`https://mcp.runpod.io/`) on 2026-11-20 and asked to be consulted before any charge: show the GPU, hourly price, disk and storage, and an estimated total, and wait for a yes before creating a pod; ask again before stopping or deleting it. The connector's tools did not load in the chat where it was added, so continue in a new chat. Still to do after training: load the chosen adapter in the studio (CPU via `lora_weights_path`; GPU via audio.cpp's `lora` session option) and compare it blind with zero-shot takes.

### Voice training dataset builder · 2026-11-20 (roadmap step 6, implemented, not yet used)

- **Voice training** page: one prompt at a time, record and stop with a button or the space bar, playback, a check result, and **Use this take for training**. It also has progress toward 10, 30 and 60 minutes, microphone choice and tips, **Add your own scripts**, and **Export training set**.
- Prompts: `studio/dataset_prompts.py` has 145 original prompts, about 13 minutes of speech. Custom prompts are split from pasted text at up to 220 characters. Tables `dataset_prompts` (built-in ids derived from the text, so seeding is idempotent) and `dataset_takes` (one take per prompt; a new take replaces the old file).
- Recording: browser AudioWorklet capture with echo cancellation, noise suppression and automatic gain off, encoded as 16-bit mono PCM WAV at the browser's rate. Uploads are validated (PCM WAV, 1–45 seconds, at most 32 MB) and stored unchanged under `data/studio/dataset/takes/`.
- Checks (`studio/dataset.py`): Whisper transcript against the spoken form of the prompt (at most 10% word differences, plus the opening-words check), clipping, quiet peak (below −20 dB), background noise (10th percentile of 50 ms RMS above −50 dB) and duration. Failed takes are saved but not kept. The checker now has a lock, because training uploads and the render worker can transcribe at the same time.
- Export: trimmed 16 kHz mono PCM clips (VoxCPM2's model config uses 16 kHz input and its validator rejects other rates), `train.jsonl` and `val.jsonl` with `audio` paths relative to the folder, `text` in spoken form and `duration`, plus a README. This matches `voxcpm/training/data.py` (`audio` and `text` columns; `ref_audio` and `dataset_id` are optional).
- Checked with a mock backend, including a fake-microphone recording in headless Chrome that uploaded a mono 16-bit WAV. Tests: `tests/test_dataset.py`; the export test needs the Windows environment. A copy of the live database migrated intact with 145 prompts.

Next for the owner: run the tests, restart the studio, record a few prompts with the real microphone, and check the level and noise readings; then build up 30–60 minutes over several sessions. Next roadmap step: 7, train a VoxCPM2 LoRA on a rented GPU with the exported folder, then load the adapter through audio.cpp's `lora` session option.

### Change log · 2026-11-19

The sections below record what changed and why, newest first.

### Automatic checks · 2026-11-19 (roadmap step 5, implemented, model downloaded, first run pending)

- `studio/quality.py` transcribes each new take with Whisper through `transformers` (already in both studio environments), on the CPU and offline. It compares the transcript with the spoken text after normalizing both (so "$19" and "nineteen dollars" match), flags more than 15% word differences, flags two or more errors in the first six words (VoxCPM2's mumbled openings), scores resemblance with Chatterbox's voice encoder (`ve.safetensors`; flagged below a provisional 0.6) and flags a pace outside 1.0–4.5 words per second.
- Results are stored per take (`section_takes.check_result`) and shown per section and take, with a count in the Recording panel.
- Best-of-N: new recordings default to one automatic retry (`jobs.auto_retries`, choice of 0–2). A failed take is regenerated with a new random seed, and the best-scoring take becomes current (`Store.select_take`); every attempt stays in the take history.
- **Check recording** (`jobs.check_requested`) checks the current takes of a finished or older recording without generating speech.
- Tests in `tests/test_quality.py` cover scoring, storage and the retry loop. The scoring and storage tests passed in a Linux check; the retry, routing and render-order tests passed there with stand-ins for the web libraries. Tests that supply their own engine run without checks unless they supply a checker, so the downloaded model never affects them.
- Setup: the owner must download `openai/whisper-small.en` with the README command (the cloud workspace cannot download models). The `hf download` CLI with repeated `--include` options fetched nothing in huggingface_hub 1.33 (it treated the patterns as file names), so the README uses `snapshot_download(allow_patterns=...)`. Actual Whisper and voice-encoder inference has not run yet; check the first results against what you hear, and note voice-match values for good and poor takes so the 0.6 threshold can be calibrated.

### Garbled section diagnosis · 2026-11-19

In the "number test" recording, the owner heard unclear speech at the start of "The Pro plan costs $1,299.99…". The normalized text sent to the model was correct and the 10.7-second length fits 28 words at an ad-read pace, so the model mispronounced words rather than adding any. The section had the instruction "Read like a TV advertisement", so it used VoxCPM2 Q8's directed mode on the GPU without the reference transcript, even though maximum resemblance was selected; its spectrogram shows a flatter, less defined pitch than the neighbouring section. Likely causes are the command-style instruction and the compressed Q8 model. Changes:

- Instruction examples are now voice descriptions (VoxCPM2's documented form), including a "TV advertisement" example, and the hint explains that descriptions work better than commands.
- The UI says when special instructions override maximum resemblance, for a section and for the whole script.
- The loudness pass undershot (−17.6 LUFS for −16) because loudnorm fell back to its dynamic mode when reaching the target would exceed the true-peak ceiling. It now raises the level inside a lookahead limiter (peaks held 2.5 dB under the ceiling, up to four passes) before the linear pass. On the owner's real sections, all four targets landed within 0.2 LU with true peaks at or below −1.2 dBTP, confirmed by an independent `ebur128` measurement.

Next for the owner: regenerate that section with a description such as "energetic, upbeat TV commercial announcer", and also try it with maximum resemblance and no instructions; compare with the CPU directed mode if the GPU take is still unclear. Automatic transcription checks (roadmap step 5) would catch this kind of problem without listening.

### Delivery quality · 2026-11-19 (roadmap step 4, implemented, not yet heard)

The owner confirmed that the GPU path for maximum resemblance works and is faster, and that the long-sentence and date fixes sound better. One remaining issue was a long pause between "Short and sweet." and "That's the goal." inside one section. Measurement of the section audio showed the model's own silence: about 2.0 s on the GPU, 0.77 s on the CPU and 0.44 s with Chatterbox.

- **Pause cap.** `tighten_pauses` shortens silences longer than 550 ms inside speech for recordings with `trim_edges`, keeping half the allowance on each side with a 10 ms crossfade. On the owner's GPU section it removes about 1.5 s. Section previews still play the untouched audio.
- **48 kHz master.** Both VoxCPM2 engines now write 48 kHz sections. New recordings (`jobs.master_rate = 48000`) assemble at 48 kHz, upsampling Chatterbox's 24 kHz sections; older recordings keep 24 kHz and resample any new 48 kHz sections down. Perth watermarks at 32 kHz internally and returns the whole signal through that rate, so VoxCPM2 detail now reaches about 16 kHz instead of 12 kHz. The watermark implementation is unchanged.
- **Final loudness pass.** New recordings default to −16 LUFS (`jobs.loudness_target`) with a −1 dBTP true-peak ceiling via FFmpeg two-pass `loudnorm` (linear mode where possible). Choices: −14, −16, −23, −24 or off; changing it rebuilds exports. The measured result is stored in `jobs.export_loudness` and shown in the Recording panel. A synthetic check reached −15.8 LUFS for a −16 target, confirmed by an independent `ebur128` measurement.
- **Combined references** fade 15 ms at each join.
- **GPU render order.** With the exclusive GPU backend, pending Chatterbox sections render before VoxCPM2 sections; the export keeps script order.

Tests: `tests/test_delivery.py`; `test_long_workflow`, `test_directed_gpu` and `test_reference_builder` were updated for 48 kHz and join fades. The model-free and FFmpeg tests passed in a Linux check; the routing tests need the Windows studio environment. A copy of the live database migrated intact.

Next for the owner: regenerate the test script, listen to the "Short and sweet" gap, compare a −16 LUFS export with an earlier one, and test number handling with a price, date, time and phone number.

### Listening feedback and spoken words · 2026-11-19 (roadmap step 3, implemented)

The owner reported that maximum resemblance on the CPU sounds much more realistic than before. Two issues were fixed:

- **Unnatural pause before "click buy".** A 316-character sentence was wrapped at 220 characters, mid-phrase. `split_long_sentence` now breaks at the clause punctuation (comma, semicolon, colon or dash) closest to a balanced split, falling back to the balanced word boundary only when there is no punctuation.
- **"Jan five" for "Jan. 5".** New `studio/normalize.py` converts written text into spoken words at render time: month abbreviations and dates with ordinals, titles, e.g./i.e./etc., times with A M/P M, money with cents and scales, percentages, ordinals, years, ranges, phone numbers, web and email addresses, initialisms and remaining numbers. Bracketed sound cues are untouched. No external package is needed.
- **Pronunciation list.** Owner-edited whole-word replacements (`pronunciations` table) apply before the automatic rules and to every newly rendered section. Each new take stores the spoken text it used (`section_takes.spoken`). The UI previews spoken words for a draft and shows them per section.

Tests: `tests/test_spoken_text.py` (passed in a Linux check, with the store and voice-match tests). A copy of the live database migrated intact, and all 192 existing section texts normalized without error.

The owner's maximum-resemblance run used the CPU because the studio was started with the directed environment and no device variables. To try the GPU path, start with `cuda-benchmark-env` and both `VOICEOVER_DEVICE` and `VOICEOVER_DIRECTED_DEVICE` set to `cuda`, as in the README. CPU generation took roughly 1–4 minutes per section at about 2.3 seconds per step; the progress bar stopping at 20–33% is normal because generation ends when the speech is complete.

Next for the owner: regenerate the test script, check the long-sentence join and the date, try the GPU path, and add any mispronounced names to the pronunciation list.

### Maximum resemblance · 2026-11-19 (roadmap step 2, implemented, not yet heard)

- Each saved reference has an editable **transcript** (`voices.transcript`, up to 1,000 characters), entered by the owner under **Voice reference**. No local speech-recognition model is installed, so drafting the transcript automatically is deferred; `funasr` is present in the directed environment but has no downloaded model.
- A **Voice engine** choice (`standard` or `maximum`) is stored per section and per take (`voice_match`). It applies to sections without special instructions: `standard` uses Chatterbox Turbo and `maximum` uses VoxCPM2 continuation cloning. Sections with instructions keep the directed mode. Restoring a take restores its engine choice.
- CPU path: `VoxCPM.generate(text, prompt_wav_path=reference, prompt_text=transcript, reference_wav_path=reference)`, as recommended in the VoxCPM2 README. GPU path: audio.cpp `--reference-text <transcript> --request-option ultimate_clone=true`. The GPU option names come from audio.cpp v0.9.4's model spec and help text and have not yet been run on this computer.
- New sections record their engine as "VoxCPM2 · maximum resemblance" (or "VoxCPM2 Q8 · maximum resemblance" on the GPU).
- Tests: `tests/test_voice_match.py`. The store tests passed in a Linux check; the routing and GPU-command tests need the Windows studio environment. A copy of the live database migrated with all references, recordings, sections and takes intact.

Next for the owner: enter the transcript for the preferred reference, then compare Chatterbox, maximum resemblance on the CPU and maximum resemblance on the GPU with the same script. Judge resemblance, vowels, pacing and artifacts, ideally with a familiar listener. Report any GPU error so the audio.cpp options can be adjusted.

### Review fixes · 2026-11-19 (section joins, pauses, splitting, originals)

The 2026-11-19 review found that the code is sound but that several details limit realism. First fixes:

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
- **Review and export** provide browser playback for each chunk and for the assembled take. Let the operator correct pronunciation in the synthesis text or regenerate individual chunks. New section takes retain their audio, words, instructions, and available generation details. Operators can compare takes and restore one, rebuilding the WAV and MP3 without synthesizing again. Earlier unindexed audio cannot be reliably attributed to a take. Assemble approved chunks in order with pauses and offer WAV and MP3. New recordings use a 48 kHz mono master with a selectable final loudness target; recordings made earlier keep 24 kHz. Keep the engine's watermarking enabled and preserve prior usable takes during regeneration.
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
2. **Implemented, pending listening: maximum-resemblance mode.** Add VoxCPM2 "ultimate cloning", which passes the reference audio and its exact transcript (`prompt_wav_path`, `prompt_text`, plus the same file as `reference_wav_path`). Store a transcript with each reference, drafted by local speech recognition and corrected by the owner. Compare against Chatterbox and controllable cloning on the same scripts.
3. **Implemented: text normalization and pronunciation list.** Expand numbers, currency, dates, phone numbers and units before synthesis, and keep an owner-edited list of word replacements that applies to every recording.
4. **Implemented, pending listening: delivery quality.** Keep a 48 kHz master (VoxCPM2 outputs 48 kHz; it is currently resampled to 24 kHz), add a final loudness and true-peak pass with selectable targets such as −16 LUFS and −23 LUFS, add crossfades to combined references, and render Chatterbox sections before directed ones to avoid reloading models on the 4 GB GPU.
5. **Implemented, first run pending: automatic checks.** Transcribe each new section locally and compare it with the script to catch skipped, repeated or invented words; score speaker similarity against the reference; flag clipping and unusual length. Then generate two or three seeds per section and keep the best-scoring take automatically, marking only failures for review.
6. **Implemented, not yet used: fine-tuning dataset builder.** Present sentences to read, record them in the browser at 48 kHz, trim and transcribe automatically, and export a training set (start with 30–60 minutes; 2–3 hours for the best result). Keep this owner-only and outside Git.
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
