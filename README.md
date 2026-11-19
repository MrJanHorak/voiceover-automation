# Local voice-over studio

This project runs a private browser studio for turning scripts into recordings in your voice. The command-line voice trial is also available for short model comparisons. See [plan.md](plan.md) for the project milestones and [AGENTS.md](AGENTS.md) for development instructions.

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

The voice panel shows which device each engine uses. Special instructions use the compressed VoxCPM2 Q8 model; blank instructions use Chatterbox. To fit both on this 4 GB GPU, the studio unloads Chatterbox before a directed section and reloads it when needed. This takes extra time when switching engines. Other GPU applications and longer sections may cause an out-of-memory failure; retry after freeing GPU memory, or restart with `$env:VOICEOVER_DIRECTED_DEVICE = 'cpu'`. Your previous successful recordings are kept. Listen to the GPU version before customer delivery: compression and the different inference runtime can change the sound or response to instructions. Watermarking and loudness balancing remain in place.

For a fresh Windows setup, first complete the CUDA Python setup above. Download [audio.cpp v0.9.4](https://github.com/0xShug0/audio.cpp/releases/tag/v0.9.4) Windows CUDA 12.4 binary and CUDA runtime archives, verify their SHA-256 checksums, and extract both into `.tools\audio-cpp`. Download the [VoxCPM2 Q8 GGUF](https://huggingface.co/audio-cpp/audio.cpp-gguf/blob/6f45df3/VoxCPM2-GGUF/voxcpm2-q8_0.gguf) revision `6f45df3` into `models\voxcpm2-gguf\voxcpm2-q8_0.gguf`. Verify these hashes:

| File | SHA-256 |
| --- | --- |
| `audio-v0.9.4-bin-windows-x64-cuda12.4.zip` | `e85d829e121a424a960d9ad293536b7ada74ea845b1c12fcf2991ec09a224d1e` |
| `audio-v0.9.4-cudart-windows-x64-cuda12.4.zip` | `efb58667b95bcb32e89ddf87811664e6ffdced3fe7015ddcc077599ed5f44bb2` |
| `voxcpm2-q8_0.gguf` | `8df1faf274e1bdeedef1fd9ddd6aba6632536f66f2e080991d2352af35ceef72` |

The optional runtime and model are Apache-2.0 licensed. Linux users can supply compatible local runtime and model paths with `VOICEOVER_AUDIOCPP_PATH` and `VOICEOVER_VOXCPM2_GGUF`; the Windows CUDA build is the one tested here. Setup requires downloads, but generation runs locally without an API subscription.

1. The studio imports `data\reference.wav` on the first run. Use **Add reference WAV** to save another 6–30 second PCM WAV under 16 MB. Give it a name such as *Warm narration*, then switch between recordings under **Saved references**. Each job keeps the reference it used.
2. Paste text or use **Import script** to load a UTF-8 `.txt`, `.docx`, or text-based `.pdf` file. Files can be up to 10 MB and extracted scripts can be up to 12,000 characters. Edit the text and select **Generate voice-over**.
3. Listen to the result and download a **WAV** or **MP3**. Open a section to adjust its wording and regenerate it. If generation stops partway through, use **Retry generation** to continue from the saved sections.

Open a completed section to see the voice reference, engine, and device used for its current audio. New recordings also retain the runtime version, seed, and SHA-256 of the primary model file locally for troubleshooting and comparisons. Reading a large model file to identify it may add time to the first recording after the studio starts. Older sections show that their engine details are unavailable; their audio is unchanged. The reference listed for a recording remains the one selected when that recording was created, even if you switch the active reference later.

Review imported text and reading order before generating. Word extraction includes body paragraphs and tables. Headers, footers, text boxes, and tracked changes may be omitted. Image-only or scanned PDFs need text recognition before import. PDFs with some textless pages show a notice so you can check for missing material.

Use **Insert sound** in the script or a section to add a chuckle, laugh, or sigh. Listen to the result to check the effect. For a styled read, try a recording of your own voice in that delivery under **Saved references**. In a finished recording with multiple sections, use **Extra pause after section** to add 0–5,000 ms of silence between clips. For example, `500` adds half a second. **Update pause** rebuilds both export formats from the existing speech.

The studio balances the loudness of each section for playback and exports. It keeps the original generated sections and adjusts their gain toward −20 LUFS with peak headroom. Peak limits and very short or quiet clips can affect the final level. For a recording created before this feature, open it in history and select **Balance volume** to rebuild its WAV and MP3 from its saved sections.

### Special instructions

Use **Choose an example** or write delivery directions under **Special instructions**. Instructions above **Generate voice-over** apply to the whole script. To direct one section, open it, edit its instructions, and select **Regenerate section**. An empty instructions field uses Chatterbox Turbo. A filled field uses VoxCPM2 with the saved voice reference, on the CPU by default or on the GPU when the optional mode above is enabled. Use plain dialogue for VoxCPM2 and put directions in the instructions field. Bracketed sound cues are for Chatterbox Turbo. Directed reads can take substantially longer on the CPU, especially when the model first loads.

To plan different deliveries before generating, add blank lines where you want sections, then select **Prepare sections**. Each section has its own text, sound menu, and special instructions. You can edit, add, or remove sections before generating. Sound cues and instructions apply to separate sections. The sound menu includes the vocal sounds documented for Chatterbox and experimental delivery tags that may have little audible effect.

Special instructions accept your own plain-language description of emotion, pace, tone, or emphasis. The examples are starting points. VoxCPM2 tries to follow the instructions, and the result can vary, so listen to each take.

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
