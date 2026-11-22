# GPU worker (Runpod Serverless)

The studio's split hosting: the website runs on a small VPS, and VoxCPM2 runs here, on a serverless GPU that scales to zero and is billed per second. The studio talks to it with `VOICEOVER_DIRECTED_DEVICE=remote` (`studio/remote.py`).

## What it does

Each studio section becomes one queue job:

- **Input:** `{"input": {"text", "seed", "transcript", "reference", "adapter"}}`. The reference is a base64 WAV; the adapter is a trained voice's name and SHA-256.
- **Output:** `{"audio": base64 48 kHz WAV, "details": {...}}`, or `{"error": "..."}`.

The worker applies the Perth watermark. It downloads each trained voice once from the studio (`/api/worker/adapters/...`, authorized by a shared token), checks it against the hash, and caches it while the worker is warm.

The image bakes in VoxCPM2 revision `7936080b41163874f38610f46f438e2c0757f9fd` (about 5 GB) and copies the studio's engine code (`studio/audio.py`, `studio/trained.py`). Rebuild the image when either changes.

## Create the endpoint (Runpod console)

Creating the endpoint costs nothing until it renders.

1. **Build from GitHub.** Push the repository (or your fork) to GitHub, connect GitHub in the Runpod console, then choose Serverless → New endpoint → GitHub repo → your repository, branch `main`, Dockerfile path `worker/Dockerfile`, build context the repository root. Runpod builds the image; the first build takes a while because of PyTorch and the model.
2. **GPU:** 24 GB (L4, A5000, RTX 3090 or 4090 class). VoxCPM2 with a trained voice uses about 8–10 GB.
3. **Workers:** max 1 (raise later if customers wait), active (minimum) 0, so it scales to zero. **Idle timeout:** about 60 seconds, so all sections of a recording reuse the warm worker. **Execution timeout:** 600 seconds.
4. **Container disk:** 30 GB. **FlashBoot:** on.
5. **Environment variables:**
   - `VOICEOVER_STUDIO_URL`: the studio's public address, for example `https://voices.example.com`.
   - `VOICEOVER_WORKER_TOKEN`: a long random secret (at least 32 characters), the same value as on the VPS. Store it as a Runpod secret.
6. **On the VPS**, set:
   - `VOICEOVER_DIRECTED_DEVICE=remote`
   - `VOICEOVER_RUNPOD_ENDPOINT=<endpoint id>`
   - `VOICEOVER_RUNPOD_API_KEY=<an API key>`
   - `VOICEOVER_WORKER_TOKEN=<same secret>`
   - `VOICEOVER_DEVICE=off`

   See `deploy/README.md`.

## Test before customers use it

- **Without spending:** `python -m unittest tests.test_remote` runs the handler and the studio's remote engine against fakes.
- **On Runpod:** in the console's Requests tab, send `{"input": {"text": "Hello there.", "seed": 35, "reference": null, "adapter": null}}`. It should fail with "Choose a voice reference or a trained voice", which proves the worker starts. Then make a voice sample from the studio's Voices page; it exercises the full path, including downloading the trained voice.

## Privacy

Script text and the reference clip are sent to Runpod for each section and are not stored by the worker. Trained voices stay in the worker's temporary cache until the worker stops. Use Runpod's Secure Cloud for the endpoint if available for the chosen GPU.
