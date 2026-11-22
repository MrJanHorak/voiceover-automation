#!/usr/bin/env bash
# One-time setup of the studio website on a fresh Ubuntu 24.04 server. Run as root:
#   curl -fsSL https://raw.githubusercontent.com/MrJanHorak/voiceover-automation/main/deploy/setup.sh | bash
# or clone the repository and run: sudo bash deploy/setup.sh
set -euo pipefail

REPO="${REPO:-https://github.com/MrJanHorak/voiceover-automation.git}"
APP=/opt/voiceover
DATA=/var/lib/voiceover

echo "== Packages"
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git ffmpeg sqlite3 debian-keyring debian-archive-keyring apt-transport-https gnupg
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update && apt-get install -y caddy
fi

echo "== User and folders"
id voiceover >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin voiceover
mkdir -p "$DATA" && chown voiceover:voiceover "$DATA" && chmod 750 "$DATA"
if [ ! -d "$APP/.git" ]; then git clone "$REPO" "$APP"; fi
chown -R voiceover:voiceover "$APP"

echo "== Python 3.11 environment (CPU only)"
if ! command -v uv >/dev/null; then curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh; fi
sudo -u voiceover bash -c "cd $APP && uv venv --python 3.11 .venv \
  && uv pip install --python .venv/bin/python torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu \
  && uv pip install --python .venv/bin/python -r requirements-studio.txt huggingface_hub"

echo "== Models for the automatic checks (Whisper small.en and the voice encoder)"
sudo -u voiceover bash -c "cd $APP && .venv/bin/python - <<'PY'
from huggingface_hub import hf_hub_download, snapshot_download
snapshot_download('openai/whisper-small.en', local_dir='models/whisper-small.en',
                  allow_patterns=['*.json', '*.txt', 'model.safetensors'])
hf_hub_download('ResembleAI/chatterbox-turbo', 've.safetensors', revision='749d1c1a46eb10492095d68fbcf55691ccf137cd',
                local_dir='models/chatterbox-turbo')
PY"

echo "== Configuration"
if [ ! -f /etc/voiceover.env ]; then
  cp "$APP/deploy/voiceover.env.example" /etc/voiceover.env
  sed -i "s/^VOICEOVER_WORKER_TOKEN=.*/VOICEOVER_WORKER_TOKEN=$(openssl rand -hex 32)/" /etc/voiceover.env
fi
chown root:voiceover /etc/voiceover.env && chmod 640 /etc/voiceover.env
cp "$APP/deploy/voiceover.service" /etc/systemd/system/voiceover.service
cp "$APP/deploy/Caddyfile" /etc/caddy/Caddyfile
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironmentFile=/etc/voiceover.env\n' > /etc/systemd/system/caddy.service.d/voiceover.conf
install -m 755 "$APP/deploy/backup.sh" /usr/local/bin/voiceover-backup
echo "15 3 * * * root /usr/local/bin/voiceover-backup >> /var/log/voiceover-backup.log 2>&1" > /etc/cron.d/voiceover-backup

systemctl daemon-reload
systemctl enable voiceover caddy

cat <<'NEXT'

Setup finished. Next:
  1. Edit /etc/voiceover.env: VOICEOVER_DOMAIN, VOICEOVER_ALLOWED_HOSTS and the Runpod endpoint and API key.
     Copy VOICEOVER_WORKER_TOKEN into the Runpod endpoint's environment too.
  2. Point the domain's DNS A record at this server, then: systemctl restart caddy voiceover
  3. Read the setup code:  journalctl -u voiceover | grep "setup code"
     Open https://<your domain>/login and create the admin account.
NEXT
