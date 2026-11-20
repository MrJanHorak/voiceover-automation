# Local voice-over studio

This project runs a private browser studio for turning scripts into recordings in your voice. The command-line voice trial is also available for short model comparisons. See [plan.md](plan.md) for the project milestones and [AGENTS.md](AGENTS.md) for development instructions.

For the latest project status and how to resume in a new chat, see [Current handoff](plan.md#current-handoff--2026-11-18). The studio is local-only at present; the owner has verified Word import, while listening comparisons of the newest GPU and advanced settings are still outstanding.

## Start the browser studio

If the existing Python environment and model files are in place, start the studio from the project directory in PowerShell.

For the installation that includes **Special instructions**, use its local Python environment.

```powershell
.\.tools\directed-env\Scripts\python.exe -m uvicorn studio.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

For the Chatterbox-only installation, use the original environment.

```powershell
.\.venv\Scripts\python.exe -m uvicorn studio.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open [the local studio](http://127.0.0.1:8000). A fresh checkout needs Python and model setup from step 1 below, `uv pip install -r requirements-studio.txt`, and FFmpeg on `PATH`. The studio works offline after setup. It uses the CPU by default. A verified CUDA environment can set `VOICEOVER_DEVICE=cuda` before starting.

### Use the NVIDIA GPU for Chatterbox Turbo

This computer has a tested GPU environment. Once the current studio has finished any recording, stop its server and run these commands from the project directory in PowerShell.

```powershell
$env:VOICEOVER_DEVICE = 'cuda'
.\.tools\cuda-benchmark-env\Scripts\python.exe -m uvicorn studio.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

Chatterbox uses the GPU in this mode. VoxCPM2 uses the CPU unless its optional GPU mode below is enabled. The RTX 3050 has 4 GB of VRAM, so other GPU workloads and longer generation can affect available memory. To return to the CPU environment, stop the server and set both `$env:VOICEOVER_DEVICE = 'cpu'` and `$env:VOICEOVER_DIRECTED_DEVICE = 'cpu'` before the directed-environment command above.

To set up an equivalent GPU environment on a fresh checkout, create the environment, install the CUDA wheels, then install the studio dependencies. Install `uv` first, and download both model checkpoints as described below.

```powershell
uv venv --python 3.11 .tools\cuda-benchmark-env
$cudaPython = '.\.tools\cuda-benchmark-env\Scripts\python.exe'
uv pip install --python $cudaPython --index-url https://download.pytorch.org/whl/cu126 'torch==2.6.0' 'torchaudio==2.6.0'
uv pip install --python $cudaPython -r requirements-directed.txt
```

### Try VoxCPM2 on the GPU

This computer has the optional GPU runtime and compressed model installed. Once the current recording finishes, stop the studio and start it with:

```powershell
$env:VOICEOVER_DEVICE = 'cuda'
$env:VOICEOVER_DIRECTED_DEVICE = 'cuda'
.\.tools\cuda-benchmark-env\Scripts\python.exe -m uvicorn studio.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

The voice panel shows which device each engine uses. Special instructions use the compressed VoxCPM2 Q8 model; blank instructions use Chatterbox. To fit both on this 4 GB GPU, the studio unloads Chatterbox before a VoxCPM2 section and reloads it when needed. It generates the Chatterbox sections of a recording first, so it switches engines at most once per run. Other GPU applications and longer sections may cause an out-of-memory failure; retry after freeing GPU memory, or restart with `$env:VOICEOVER_DIRECTED_DEVICE = 'cpu'`. Your previous successful recordings are kept. Listen to the GPU version before customer delivery: compression and the different inference runtime can change the sound or response to instructions. Watermarking and loudness balancing remain in place.

For a fresh Windows setup, first complete the CUDA Python setup above. Download [audio.cpp v0.9.4](https://github.com/0xShug0/audio.cpp/releases/tag/v0.9.4) Windows CUDA 12.4 binary and CUDA runtime archives, verify their SHA-256 checksums, and extract both into `.tools\audio-cpp`. Download the [VoxCPM2 Q8 GGUF](https://huggingface.co/audio-cpp/audio.cpp-gguf/blob/6f45df3/VoxCPM2-GGUF/voxcpm2-q8_0.gguf) revision `6f45df3` into `models\voxcpm2-gguf\voxcpm2-q8_0.gguf`. Verify these hashes:

| File | SHA-256 |
| --- | --- |
| `audio-v0.9.4-bin-windows-x64-cuda12.4.zip` | `e85d829e121a424a960d9ad293536b7ada74ea845b1c12fcf2991ec09a224d1e` |
| `audio-v0.9.4-cudart-windows-x64-cuda12.4.zip` | `efb58667b95bcb32e89ddf87811664e6ffdced3fe7015ddcc077599ed5f44bb2` |
| `voxcpm2-q8_0.gguf` | `8df1faf274e1bdeedef1fd9ddd6aba6632536f66f2e080991d2352af35ceef72` |

The optional runtime and model are Apache-2.0 licensed. Linux users can supply compatible local runtime and model paths with `VOICEOVER_AUDIOCPP_PATH` and `VOICEOVER_VOXCPM2_GGUF`; the Windows CUDA build is the one tested here. Setup requires downloads, but generation runs locally without an API subscription.

1. The studio imports `data\reference.wav` on the first run. Use **Add reference WAV** to save another 6–30 second PCM WAV under 16 MB. The studio keeps an untouched copy of each new upload under `data\studio\voices\originals` for future fine-tuning. Give it a name such as *Warm narration*, then switch between recordings under **Saved references**. Each job keeps the reference it used.
2. Paste text or use **Import script** to load a UTF-8 `.txt`, `.docx`, or text-based `.pdf` file. Files can be up to 10 MB and extracted scripts can be up to 12,000 characters. Edit the text and select **Generate voice-over**.
3. Listen to the result and download a **WAV** or **MP3**. Open a section to adjust its wording and regenerate it. If generation stops partway through, use **Retry generation** to continue from the saved sections.

The studio saves multiple references but uses one selected reference per job. Under **Combine reference excerpts**, add excerpts from your saved recordings in the order you want, listen to each source, and enter start and end times in seconds. Joins fade over 15 ms so they do not click. Preview the combined audio, listen for natural joins, then save it as a new active reference. The original recordings remain unchanged. The combination must be 6–30 seconds; start with 10–15 seconds and compare it with your best single reference using the same script and settings. A combined sample may sound better or worse; it is not training or a multi-reference model. Chatterbox uses only the first 10 and 15 seconds for certain conditioning components, though another speaker embedding uses the reference more broadly. Chatterbox caches its prepared reference while the engine remains loaded, but prepares it again after a reference or normalization change, a restart, or unloading the GPU model. Both current VoxCPM2 paths prepare the reference for each section; the GPU path also starts a new model process per section. None of these steps permanently trains the model on the reference.

After regenerating a section, open **All takes** inside that section to play and compare its current and earlier takes. Select **Use this take** to restore an earlier version and rebuild the finished WAV and MP3 without generating speech again. Its saved words and instructions return with it. If regeneration fails, you can also choose a saved take instead of retrying. Takes stay on your computer until you delete their recording and use additional disk space. Takes generated before this feature are only available when the studio still has their information; files from earlier regenerations were not indexed and cannot be listed automatically. For an older interrupted edit whose original words are unknown, the audio can be played but cannot be restored.

Open a completed section to see the voice reference, engine, and device used for its current audio. New recordings also retain the runtime version, seed, and SHA-256 of the primary model file locally for troubleshooting and comparisons. Reading a large model file to identify it may add time to the first recording after the studio starts. Older sections show that their engine details are unavailable; their audio is unchanged. The reference listed for a recording remains the one selected when that recording was created, even if you switch the active reference later.

Review imported text and reading order before generating. Word extraction includes body paragraphs and tables. Headers, footers, text boxes, and tracked changes may be omitted. Image-only or scanned PDFs need text recognition before import. PDFs with some textless pages show a notice so you can check for missing material.

Use **Insert sound** in the script or a section to add a chuckle, laugh, or sigh. Listen to the result to check the effect. For a styled read, try a recording of your own voice in that delivery under **Saved references**. In a finished recording with multiple sections, use **Extra pause after section** to set the gap between clips from 0 to 5,000 ms. New recordings trim the silence the model leaves at the start and end of each section, so the value is the whole gap: 350 ms after a sentence and 750 ms after a paragraph by default. For example, `500` gives half a second. Recordings created before this change keep their original joins, where the value is added to the model's own silence. **Update pause** rebuilds both export formats from the existing speech.

The studio balances the loudness of each section for playback and exports. It keeps the original generated sections and adjusts their gain toward −20 LUFS with peak headroom. Peak limits and very short or quiet clips can affect the final level. For a recording created before this feature, open it in history and select **Balance volume** to rebuild its WAV and MP3 from its saved sections.

New recordings are also assembled as a 48 kHz master and get a final loudness pass. Choose **Delivery loudness** in the Recording panel: −16 LUFS for podcasts and the web (default), −14 LUFS for online video and streaming, −23 LUFS for EBU R128 broadcast, −24 LUFS for US broadcast, or off. The pass uses FFmpeg's two-pass `loudnorm` with a −1 dBTP true-peak ceiling, and the panel shows the measured result. Changing the target rebuilds the WAV and MP3 without generating speech again. Recordings made before this change keep 24 kHz exports and no final pass unless you choose a target. VoxCPM2 sections keep its 48 kHz output; the watermark is applied at 32 kHz internally, so their detail extends to about 16 kHz. Chatterbox produces 24 kHz audio, which is upsampled in the master.

New recordings also shorten pauses longer than 0.55 seconds inside a section. VoxCPM2 on the GPU sometimes leaves a gap of about two seconds between short sentences. Section playback in the review list plays the untouched section; the full recording and downloads use the shortened pauses.

### Special instructions

Use **Choose an example** or write delivery directions under **Special instructions**. Instructions above **Generate voice-over** apply to the whole script. To direct one section, open it, edit its instructions, and select **Regenerate section**. An empty instructions field uses Chatterbox Turbo. A filled field uses VoxCPM2 with the saved voice reference, on the CPU by default or on the GPU when the optional mode above is enabled. Use plain dialogue for VoxCPM2 and put directions in the instructions field. Bracketed sound cues are for Chatterbox Turbo. Directed reads can take substantially longer on the CPU, especially when the model first loads.

To plan different deliveries before generating, add blank lines where you want sections, then select **Prepare sections**. Each section has its own text, sound menu, and special instructions. You can edit, add, or remove sections before generating. Sound cues and instructions apply to separate sections. The sound menu includes the vocal sounds documented for Chatterbox and experimental delivery tags that may have little audible effect.

Special instructions accept your own plain-language description of emotion, pace, tone, or emphasis. The examples are starting points. VoxCPM2 tries to follow the instructions, and the result can vary, so listen to each take.

### Automatic checks

The studio can check each new section by itself. It transcribes the take with a local Whisper model and compares it with the words the voice was asked to say, after both are written out in full. It also flags a mumbled opening, scores the take's resemblance to your voice reference with Chatterbox's speaker encoder, and checks the speaking pace. Checks run on the CPU so they do not compete with the voice models for the GPU, and nothing leaves the computer.

Set up the speech-recognition model once from the project directory (about 1 GB; Whisper is MIT-licensed):

```powershell
.\.tools\directed-env\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('openai/whisper-small.en', local_dir='models/whisper-small.en', allow_patterns=['*.json', '*.txt', 'model.safetensors'])"
```

Restart the studio afterwards. Both studio environments include the required libraries. To use a different Whisper model, download it to another folder and set `VOICEOVER_ASR_DIR` to that folder before starting.

Under **If a section fails its automatic check**, choose to generate once more and keep the better take (default), up to twice more, or keep the take and mark it for review. Every take stays under **All takes** with its check result, so you can still choose a different one. Each section shows **Check passed** or **Needs review** with the reason and what the transcription heard, and the Recording panel counts sections that need review. Select **Check recording** to check the current takes of a finished or older recording without generating new speech.

A check is a guide, not a guarantee. Transcription can miss a subtle mispronunciation or mishear a correct word, and the voice-match threshold (0.6) is provisional until it is calibrated with your recordings. Listen before delivery.

### Spoken words and pronunciations

Before a section is generated, the studio writes out dates, times, prices, percentages, phone numbers, years, ordinals, number ranges, web addresses and common abbreviations as words. For example, `Jan. 5 at 9 a.m.` is read as "January fifth at nine A M" and `$1,299.99` as "one thousand two hundred ninety-nine dollars and ninety-nine cents". Your script and sections keep the text as written, and bracketed sound cues are left alone.

Open **Pronunciations and spoken words** under the script to add words the voice gets wrong, such as names, brands or acronyms. A saved word matches whole words regardless of capitalization, and your list is applied before the automatic rules. Changes apply to sections generated afterwards. Select **Preview spoken words** to see exactly what the voice will read. Each section shows the words its take used when they differ from the script.

Very long sentences are split at a comma or other clause break near the middle rather than at a fixed length.

### Maximum resemblance

Maximum resemblance uses VoxCPM2's continuation cloning: it receives the voice reference together with an exact transcript of what you say in it, and continues in your voice. It aims for closer timbre, rhythm and delivery than Chatterbox, but it is slower and needs the VoxCPM2 setup above.

1. Open **Reference transcript** under **Voice reference** and type exactly what you say in the active reference, word for word. Select **Save transcript**. Each saved reference has its own transcript; a combined reference needs the words of its excerpts in order.
2. Choose **Maximum resemblance** under **Voice engine** and generate. To try it on one section, open the section, change its engine and select **Regenerate section**.

The engine choice applies to sections without special instructions. Sections with special instructions still use VoxCPM2's directed mode. Remove bracketed sound cues before using maximum resemblance, because VoxCPM2 would read them aloud. A wrong transcript can cause mispronunciations or artifacts, so check it against the recording. The first section starts with seed 35; regeneration picks a new seed. On the GPU, the studio passes the transcript to audio.cpp with its `ultimate_clone` option; the transcript appears in that process's arguments, but script text does not. Compare the result with Chatterbox on the same script before customer use.

### Advanced Chatterbox settings

Open **Advanced Chatterbox settings** below the script to adjust seed, temperature, Top P, Top K, repetition penalty, and reference loudness normalization. Select the question mark beside a control for an explanation. These settings apply to Chatterbox sections; sections with special instructions use VoxCPM2 instead. Open a completed section to change its settings when regenerating; the settings and actual seed for new takes appear in **All takes**. The initial seed defaults to `35` to preserve the studio's previous behavior; `0` selects a new random seed, and regeneration starts with `0` for variety. Using a fixed seed can help repeat a take, but results can vary across devices or runtime versions.

Reference loudness normalization adjusts the reference toward −27 LUFS before Chatterbox analyzes it. It is separate from the studio's output balancing at −20 LUFS, which applies to both models. The Min P control shown in the online demo is not offered because this installed Turbo version ignores it. Top P starts at 0.01 because its sampler requires a value greater than zero. See the [Resemble demo source](https://huggingface.co/spaces/ResembleAI/chatterbox-turbo-demo/blob/main/app.py) and [Turbo implementation](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/tts_turbo.py).

For a fresh directed installation, complete the Chatterbox model download from the trial setup below and run the PowerShell commands from the project directory. VoxCPM2 2.0.3 uses the pinned Apache-2.0 checkpoint below. Allow about 5 GB for its model files. Runtime synthesis stays offline. [VoxCPM2 model card](https://huggingface.co/openbmb/VoxCPM2)

```powershell
uv venv --python 3.11 .tools\directed-env
$directedPython = '.\.tools\directed-env\Scripts\python.exe'
uv pip install --python $directedPython --index-url https://download.pytorch.org/whl/cpu 'torch==2.6.0' 'torchaudio==2.6.0'
uv pip install --python $directedPython -r requirements-directed.txt
$env:HF_HUB_DISABLE_XET = '1'
.\.tools\directed-env\Scripts\hf.exe download openbmb/VoxCPM2 `
  audiovae.pth model.safetensors config.json special_tokens_map.json tokenization_voxcpm2.py tokenizer.json tokenizer_config.json `
  --revision 7936080b41163874f38610f46f438e2c0757f9fd --local-dir .\models\voxcpm2
```

Recording history and private files live under `data\studio`. The web server listens only on this computer with the launch command above. Only `studio\static` is served as public assets. The local server is intended for owner use. A self-hosted deployment needs owner authentication before it can be exposed through a network.

## First voice trial

### 1. Set up Python and Chatterbox Turbo

If this checkout already has `.venv\Scripts\python.exe` and the model files, skip to step 2. For a fresh checkout, install uv, then run these commands from the project directory in PowerShell. If this checkout already has `.tools\uv\uv.exe`, use that executable in place of `uv`. The commands below set up the CPU baseline. To set up a fresh environment for CUDA, use `https://download.pytorch.org/whl/cu126` for the first `uv pip install` command. [uv installation](https://docs.astral.sh/uv/getting-started/installation/)

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.tools\cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path (Get-Location) '.tools\python'
uv venv --python 3.11 .venv
uv pip install --index-url https://download.pytorch.org/whl/cpu 'torch==2.6.0' 'torchaudio==2.6.0'
uv pip install -r requirements-voice-trial.txt
uv pip check
```

Fetch the official model files once. Allow about 3 GB for the checkpoint files, plus temporary download and dependency space. A Hugging Face account is optional for this public download. The trial loads the pinned files from disk and enables offline mode.

```powershell
$env:HF_HOME = Join-Path (Get-Location) '.tools\hf'
$env:HF_HUB_DISABLE_IMPLICIT_TOKEN = '1'
$env:HF_HUB_DISABLE_TELEMETRY = '1'
$env:HF_HUB_DISABLE_UPDATE_CHECK = '1'
$env:HF_HUB_DISABLE_XET = '1'
.\.venv\Scripts\hf.exe download ResembleAI/chatterbox-turbo `
  ve.safetensors t3_turbo_v1.safetensors s3gen_meanflow.safetensors `
  added_tokens.json merges.txt special_tokens_map.json tokenizer_config.json vocab.json `
  --revision 749d1c1a46eb10492095d68fbcf55691ccf137cd --local-dir .\models\chatterbox-turbo
```

### 2. Prepare your reference

Record 10–15 seconds of clean English speech in your natural speaking voice, with one speaker and minimal silence. Save it as `data\reference.wav`. The trial accepts a 6–30 second PCM WAV. If you have a different audio format, convert a clean take with FFmpeg.

You can read this passage aloud. “I like a quiet start to the day. I make a cup of coffee, open the window, and take a minute to think. Then I sit down, check my notes, and get ready for the work ahead.”

```powershell
New-Item -ItemType Directory -Force .\data | Out-Null
ffmpeg -n -i 'C:\path\to\your-recording.m4a' -vn -ac 1 -ar 24000 -c:a pcm_s16le .\data\reference.wav
```

### 3. Generate and listen

```powershell
.\.venv\Scripts\python.exe .\scripts\voice_trial.py `
  --reference .\data\reference.wav `
  --text 'Thanks for listening. Your next story starts here.' `
  --output .\data\first-take.wav
```

Use `--script .\data\script.txt` in place of `--text` to read a UTF-8 file. This trial handles one English passage up to 250 characters and writes a mono PCM WAV at the model's native rate. Give each take a new output filename. Add `--device cuda` if you set up CUDA wheels.

The command prints the model load time, synthesis time, duration, and real-time factor. A real-time factor of `2.00x` means a ten-second take took about twenty seconds to synthesize. Use the [milestone in the plan](plan.md#first-milestone--evaluate-the-owners-voice) to check resemblance, pronunciation, and speed. Keep recordings and customer scripts in the ignored `data/` directory.
