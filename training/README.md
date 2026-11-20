# Training a voice adapter on a rented GPU

This folder is the kit for roadmap step 7: training a VoxCPM2 LoRA adapter on the owner's voice. The studio's **Voice training** page exports the dataset; `make_kit.py` zips it with these files and the active voice reference.

| File | Purpose |
| --- | --- |
| `voxcpm2_lora.yaml` | Training settings from the VoxCPM fine-tuning guide, with checkpoints every 50 steps |
| `train_on_pod.sh` | Run on the GPU machine: installs VoxCPM 2.0.3, downloads the matching VoxCPM2 revision, trains, makes samples and zips the results |
| `make_samples.py` | Renders test sentences for every checkpoint, with and without the reference, plus a baseline from the original model |
| `make_kit.py` | Run on Windows: builds `voice-training-kit.zip` from the latest export |

LoRA training of VoxCPM2 needs about 20 GB of GPU memory, according to the VoxCPM guide. A 48 GB RTX A6000 leaves headroom; a 24 GB RTX 4090 may work at `batch_size: 8`. The training data is the owner's voice: use a secure, data-centre GPU, and delete the pod and any storage after downloading the results.

## Gotchas found on the first run (2026-11-20)

- The trainer resolves `audio` paths in the manifests against its own working folder, not the manifest's folder. `train_on_pod.sh` rewrites both manifests to absolute paths before training.
- With about 18 minutes of speech, validation loss on the diffusion part kept improving until about step 200, while the stop loss worsened after step 50 (the model learns when to end too specifically). Steps 150–200 were the candidates; at 300 steps the run had made about 27 passes over the data.

## Using the result

Download the results zip before deleting the pod. Copy the chosen checkpoint folder (for example `lora/step_0000150`) to `data/studio/trained/<name>/` and restart the studio; see "Trained voice" in the main README. Add a row to [TRAINED_VOICES.md](TRAINED_VOICES.md), and a `VOICE.md` and `voice.json` in the voice folder.

