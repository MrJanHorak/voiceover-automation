# VoxCPM2 GPU worker for the voice-over studio (Runpod Serverless, queue-based).
# Build context: the repository root, so the studio's engine code can be copied in.
#   docker buildx build --platform linux/amd64 -f worker/Dockerfile -t <namespace>/voiceover-worker:v1 .
# Or let Runpod build it from GitHub (Dockerfile path: worker/Dockerfile). See worker/README.md.
FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

COPY worker/requirements.txt /tmp/requirements.txt
RUN python -c "import torch; print(f'torch=={torch.__version__.split(\"+\")[0]}')" > /tmp/constraints.txt \
 && pip install -c /tmp/constraints.txt -r /tmp/requirements.txt

# Bake the pinned VoxCPM2 weights (about 5 GB) into the image, so workers start without downloading.
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('openbmb/VoxCPM2', revision='7936080b41163874f38610f46f438e2c0757f9fd', local_dir='/models/voxcpm2')"

WORKDIR /app
COPY studio/__init__.py studio/audio.py studio/trained.py /app/studio/
COPY worker/handler.py /app/worker/handler.py

ENV HF_HUB_OFFLINE=1 VOXCPM2_DIR=/models/voxcpm2 VOXCPM2_DEVICE=cuda
CMD ["python", "-u", "worker/handler.py"]
