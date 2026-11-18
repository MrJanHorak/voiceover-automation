# Local voice-over studio

This project starts with a command-line voice trial. It generates a short WAV from your own reference recording and either typed text or a `.txt` script. The browser studio and later self-hosting milestones are in [plan.md](plan.md). Development instructions are in [AGENTS.md](AGENTS.md).

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
