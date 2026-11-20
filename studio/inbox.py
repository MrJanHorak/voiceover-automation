"""Hands-off intake: script files dropped in an inbox folder become recordings awaiting the owner's approval.

A file is picked up once its size and modification time are unchanged between two scans, so files still
being copied or saved are left alone. Each processed file moves to done/ or, with a note explaining why,
to failed/. Nothing is deleted.
"""
import logging
import re
from datetime import datetime
from pathlib import Path

from .store import StudioError

LOG = logging.getLogger(__name__)
SUFFIXES = {".txt", ".docx", ".pdf"}
SCAN_SECONDS = 3.0


def ignored(path):
    """Hidden files, Office lock files and partial downloads are never scripts."""
    name = path.name
    return (name.startswith((".", "~$")) or name.lower().endswith((".tmp", ".crdownload", ".part", ".partial"))
            or name.lower() in {"desktop.ini", "thumbs.db"})


def title_for(path):
    title = re.sub(r"[_]+", " ", path.stem).strip()
    return title[:100] or "Inbox recording"


def unique(folder, name):
    """A file name in folder that does not overwrite an existing file."""
    candidate = folder / name
    stem, suffix = candidate.stem, candidate.suffix
    number = 2
    while candidate.exists():
        candidate = folder / f"{stem} ({number}){suffix}"
        number += 1
    return candidate


class Inbox:
    def __init__(self, folder, submit):
        """submit(title, script) creates a recording and returns its id, or raises StudioError."""
        self.folder = Path(folder)
        self.done = self.folder / "done"
        self.failed = self.folder / "failed"
        self.submit = submit
        self.seen = {}
        self.handled = {}  # Files that could not be moved after processing; never submitted twice.

    def prepare(self):
        for folder in (self.folder, self.done, self.failed):
            folder.mkdir(parents=True, exist_ok=True)

    def scan(self):
        """Process every stable file once; return the ids of the recordings created."""
        self.prepare()
        created, present = [], set()
        for path in sorted(self.folder.iterdir()):
            if not path.is_file() or ignored(path):
                continue
            present.add(path)
            try:
                stat = path.stat()
            except OSError:
                continue
            signature = (stat.st_size, stat.st_mtime_ns)
            if self.handled.get(path) == signature:
                continue
            if self.seen.get(path) != signature:
                self.seen[path] = signature  # Wait one more scan to make sure the file is complete.
                continue
            job_id = self.process(path)
            if job_id:
                created.append(job_id)
            self.seen.pop(path, None)
            if job_id and path.exists():
                self.handled[path] = signature
        self.seen = {path: signature for path, signature in self.seen.items() if path in present}
        self.handled = {path: signature for path, signature in self.handled.items() if path in present}
        return created

    def process(self, path):
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        if path.suffix.lower() not in SUFFIXES:
            self.reject(path, stamp, "Only .txt, .docx and .pdf scripts can be recorded from the inbox.")
            return None
        from .script_import import MAX_SCRIPT_FILE_BYTES, extract_script  # Word and PDF readers load on first use.

        try:
            with path.open("rb") as handle:
                contents = handle.read(MAX_SCRIPT_FILE_BYTES + 1)
        except OSError:
            return None  # Still open in another program; try again on the next scan.
        try:
            script = extract_script(path.name, contents)["text"]
            job_id = self.submit(title_for(path), script)
        except StudioError as error:
            self.reject(path, stamp, str(error))
            return None
        except Exception as error:
            LOG.error("Inbox file could not be recorded (%s)", type(error).__name__)
            self.reject(path, stamp, "The studio could not start this recording. Check the terminal, then drop the file in again.")
            return None
        self.move(path, self.done, f"{stamp} {path.name}")
        return job_id

    def reject(self, path, stamp, reason):
        moved = self.move(path, self.failed, f"{stamp} {path.name}")
        if moved is not None:
            note = moved.with_name(moved.name + ".why.txt")
            note.write_text(reason + "\n", encoding="utf-8")

    def move(self, path, folder, name):
        target = unique(folder, name)
        try:
            path.replace(target)
        except OSError as error:
            LOG.warning("Could not move an inbox file (%s)", type(error).__name__)
            return None
        return target
