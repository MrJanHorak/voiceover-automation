# Putting the studio online (split hosting)

The website, database and automatic checks run on a small Linux server (VPS). VoxCPM2 runs on a Runpod Serverless GPU that is billed only while it renders (see `worker/README.md`).

| Piece | Where | Cost |
| --- | --- | --- |
| Website, accounts, database, checks | Hetzner cost-optimized VPS, 2 vCPU / 4 GB, Ubuntu 24.04 | About €6–8 a month |
| HTTPS | Caddy on the VPS (free certificates) | Free |
| Rendering | Runpod Serverless endpoint, 24 GB GPU, scales to zero | Per second while rendering |
| Domain | Any registrar | About $10–20 a year |

## 1. Server

1. Create the server: Hetzner Cloud → Add server → Ubuntu 24.04, a cost-optimized plan with 4 GB RAM, your SSH key, and an IPv4 address.
2. At the domain registrar, add an **A record** for the studio's address (for example `voices.example.com`) pointing at the server's IPv4.
3. On the server, as root: `git clone https://github.com/MrJanHorak/voiceover-automation.git /opt/voiceover && bash /opt/voiceover/deploy/setup.sh`

   The script installs Caddy, FFmpeg, a Python 3.11 environment with CPU PyTorch, and the check models. It also creates `/etc/voiceover.env` with a random worker token and sets up nightly backups. If the repository is private, clone it with a GitHub deploy key or token first.
4. Edit `/etc/voiceover.env`: set the domain and the Runpod endpoint ID and API key. Copy the `VOICEOVER_WORKER_TOKEN` value for the next step.

## 2. GPU endpoint

Follow `worker/README.md`: create a Serverless endpoint from the GitHub repository with Dockerfile `worker/Dockerfile`. Set its environment variables to `VOICEOVER_STUDIO_URL=https://voices.example.com` and the same `VOICEOVER_WORKER_TOKEN`.

## 3. Start

```bash
systemctl restart caddy voiceover
journalctl -u voiceover | grep "setup code"
```

Open `https://voices.example.com/login` and create the admin account with the code.

## 4. Move the voices over from the Windows studio

The simplest route is to copy the data folder, so recordings, accounts, the catalog and consent records come along.

1. Stop the Windows studio.
2. Back it up: zip `data\studio`.
3. Copy it over: `scp -r data\studio root@<server>:/var/lib/voiceover/`
4. On the server: `chown -R voiceover:voiceover /var/lib/voiceover && systemctl restart voiceover`

If you copy the data, the admin account you already created on Windows comes along, and no setup code is shown. Alternatively, start fresh: on the server, add references under **References**, upload trained voices on **Voices**, and create the catalog voices again with their consent documents.

## Day to day

- **Update:**
  ```bash
  cd /opt/voiceover && sudo -u voiceover git pull && sudo -u voiceover uv pip install --python .venv/bin/python -r requirements-studio.txt && systemctl restart voiceover
  ```
  Rebuild the GPU worker on Runpod when `studio/audio.py`, `studio/trained.py` or `worker/` changed.
- **Logs:** `journalctl -u voiceover -f`
- **Backups:** `/var/backups/voiceover/` (nightly, last 14 days). Copy them off the server regularly, for example to a Hetzner Storage Box.
- **Restore:** `bash /opt/voiceover/deploy/restore.sh /var/backups/voiceover/<archive>`. It keeps the current data aside as `studio.before-restore-*`.
- **Costs:** watch the Runpod balance and usage. With flex workers only and the idle timeout at about 60 seconds, nothing is billed while nobody records.

## Before inviting customers

Email verification and password reset are not built yet; they are the next part of this step. Until then, sign-up works, but a forgotten password cannot be reset yet.
