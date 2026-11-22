# Trained voices

This file records every adapter trained in this studio, and how to use one in this studio or another program. The adapters themselves are private and never committed. Each one lives in `data/studio/trained/<name>/` with its own `VOICE.md` (usage) and `voice.json` (machine-readable details), so the folder still makes sense when copied to another project.

## Record

| Name | Date | Base model | Data | Settings | Checkpoint | Validation loss (total / diff) | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| step150 | 2026-11-20 | VoxCPM2 `7936080b`, voxcpm 2.0.3 | Export `2026-11-20_154735`: 184 clips, 19.6 min | `voxcpm2_lora.yaml`: r=32, alpha=32, lr 1e-4, 300 steps | 150 of 300 | 0.887 / 0.756 | **In use (2026-11-20).** In blind comparisons, Trained voice + reference sounded most like the speaker, with Maximum resemblance a close second. It is now the studio default. |

The same run's other checkpoints scored as follows. Lower is better. "Diff" tracks how the voice sounds; "total" also includes when to stop speaking.

| Step | 50 | 100 | 150 | 200 | 250–299 |
| --- | --- | --- | --- | --- | --- |
| Total | 0.870 | 0.871 | 0.887 | 0.891 | 0.95 at 299 |
| Diff | 0.786 | 0.764 | 0.756 | 0.751 | 0.775 at 299 |

All checkpoints and the listening samples are in `voice-lora-results.zip`. Keep it in private backup storage, not in Downloads.

When you add a voice, add a row, fill in the verdict from the studio's blind comparisons, and put a `VOICE.md` and `voice.json` in its folder. Copy and edit the ones in `data/studio/trained/step150/`.

## Using a trained voice elsewhere

A VoxCPM2 LoRA works in any program that runs VoxCPM2 with the same base model:

- **Python (`voxcpm` library):** load the base model with `LoRAConfig` built from the adapter's own `lora_config.json` and `lora_weights_path=<folder>`, then call `generate(text=...)`. A reference clip is optional.
- **audio.cpp (GGUF, CPU or GPU):** pass `--session-option voxcpm2.lora=<folder>`. Its `audiocpp_server` can serve the same model as a local API for other apps.
- **Hosted inference:** a serverless GPU (see the hosting table in `plan.md`) can run the Python path for apps that cannot run a model locally.

It does not work with other engines, such as Chatterbox, ElevenLabs or Piper. Those would need their own training on the same dataset. The exported dataset in `data/studio/dataset/exports/` is engine-neutral: 16 kHz mono WAVs with spoken-form text.

Full examples are in each voice's `VOICE.md`.

## Rules

- Use a trained voice only with its speaker's recorded consent, and only for the uses they agreed to; record that consent on the Voices page. Do not upload adapters to Hugging Face or shared drives.
- Keep the Perth watermark on generated audio, and label it as AI-generated. VoxCPM2's model card forbids impersonation, fraud and disinformation.
- VoxCPM2 and audio.cpp are Apache-2.0. Commercial use of the generated audio is allowed under the base model's license.
