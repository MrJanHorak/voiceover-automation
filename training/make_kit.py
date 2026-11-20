"""Build voice-training-kit.zip: the newest dataset export, the training files and the active reference.

Run from the project folder:
  .\\.tools\\directed-env\\Scripts\\python.exe training\\make_kit.py
"""

import sqlite3
import sys
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
STUDIO = PROJECT / "data" / "studio"
KIT_FILES = ["voxcpm2_lora.yaml", "train_on_pod.sh", "make_samples.py"]


def main():
    exports = sorted(path for path in (STUDIO / "dataset" / "exports").glob("*") if (path / "train.jsonl").is_file())
    if not exports:
        sys.exit("No dataset export found. Use Export training set on the Voice training page first.")
    export = exports[-1]
    with sqlite3.connect(STUDIO / "studio.sqlite3") as db:
        row = db.execute("SELECT voice_id FROM voice_selection WHERE id = 1").fetchone()
    reference = STUDIO / "voices" / f"{row[0]}.wav" if row else None
    destination = export.with_name(f"{export.name}-voice-training-kit.zip")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in KIT_FILES:
            archive.write(Path(__file__).with_name(name), name)
        for path in sorted(export.rglob("*")):
            if path.is_file():
                archive.write(path, Path("dataset") / path.relative_to(export))
        if reference and reference.is_file():
            archive.write(reference, "reference.wav")
    size = destination.stat().st_size / 1024 / 1024
    print(f"Created {destination} ({size:.0f} MB) from {export.name}"
          + ("" if reference and reference.is_file() else " (no active reference found; samples will skip it)"))


if __name__ == "__main__":
    main()
