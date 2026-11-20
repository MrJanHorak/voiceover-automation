#!/usr/bin/env bash
# Train a VoxCPM2 LoRA on the owner's voice on a rented GPU, then make comparison samples.
# Usage, from a terminal on the pod after uploading the kit zip to /workspace:
#   cd /workspace && unzip -o voice-training-kit.zip -d kit && bash kit/train_on_pod.sh
set -euo pipefail
cd /workspace

echo "== GPU"
nvidia-smi --query-gpu=name,memory.total --format=csv

echo "== VoxCPM training code (release 2.0.3, the version the studio uses, when tagged)"
if [ ! -d VoxCPM ]; then git clone https://github.com/OpenBMB/VoxCPM.git; fi
cd VoxCPM
git fetch --tags --quiet || true
git checkout 2.0.3 2>/dev/null || git checkout v2.0.3 2>/dev/null || echo "No 2.0.3 tag found; using the default branch."
pip install --quiet -e .
cd /workspace

echo "== VoxCPM2 model, same revision as the studio"
if [ ! -f VoxCPM2/model.safetensors ]; then
  pip install --quiet "huggingface_hub[cli]"
  huggingface-cli download openbmb/VoxCPM2 --revision 7936080b41163874f38610f46f438e2c0757f9fd --local-dir VoxCPM2
fi

echo "== Training (checkpoints every 50 steps under /workspace/output/lora)"
cd VoxCPM
python scripts/train_voxcpm_finetune.py --config_path /workspace/kit/voxcpm2_lora.yaml
cd /workspace

echo "== Comparison samples"
python kit/make_samples.py

echo "== Packing results (adapter weights and settings only, no optimizer state)"
rm -rf results && mkdir -p results
for step in output/lora/step_*; do
  name=$(basename "$step")
  mkdir -p "results/$name"
  cp "$step"/lora_weights.* "$step"/lora_config.json "results/$name/" 2>/dev/null || true
done
cp -r output/samples results/samples
cd results && zip -qr ../voice-lora-results.zip . && cd ..
echo "Done. Download /workspace/voice-lora-results.zip, then stop and delete the pod."
