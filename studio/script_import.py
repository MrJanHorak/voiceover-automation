from io import BytesIO
from pathlib import PurePosixPath
from zipfile import ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from .store import SCRIPT_LIMIT, StudioError


MAX_SCRIPT_FILE_BYTES = 10 * 1024 * 1024
MAX_DOCX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_PDF_PAGES = 100


def document_blocks(container):
    for block in container.iter_inner_content():
        if isinstance(block, Paragraph):
            yield block.text
        elif isinstance(block, Table):
            seen = set()
            for row in block.rows:
                for cell in row.cells:
                    if cell._tc in seen:
                        continue
                    seen.add(cell._tc)
                    yield from document_blocks(cell)


def extract_script(filename, contents):
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if extension not in {".txt", ".docx", ".pdf"}:
        raise StudioError("Choose a .txt, .docx, or .pdf script.")
    if len(contents) > MAX_SCRIPT_FILE_BYTES:
        raise StudioError("Choose a script file under 10 MB.", 413)

    warnings = []
    if extension == ".txt":
        try:
            text = contents.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise StudioError("This text file is not valid UTF-8.") from error
    elif extension == ".docx":
        try:
            with ZipFile(BytesIO(contents)) as archive:
                if sum(item.file_size for item in archive.infolist()) > MAX_DOCX_EXPANDED_BYTES:
                    raise StudioError("This Word document is too large to extract.", 413)
            document = Document(BytesIO(contents))
            text = "\n\n".join(document_blocks(document))
        except StudioError:
            raise
        except Exception as error:
            raise StudioError("This Word document could not be read. Choose a valid .docx file.") from error
        warnings.append("Review the text. Headers, footers, text boxes, and tracked changes may be omitted.")
    else:
        if not contents.startswith(b"%PDF-"):
            raise StudioError("This PDF could not be read. Choose a valid PDF file.")
        try:
            reader = PdfReader(BytesIO(contents))
            if reader.is_encrypted:
                raise StudioError("This PDF is password-protected. Choose an unlocked copy.")
            if len(reader.pages) > MAX_PDF_PAGES:
                raise StudioError("This PDF has more than 100 pages.", 413)
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
        except StudioError:
            raise
        except Exception as error:
            raise StudioError("This PDF could not be read. Choose a valid PDF file.") from error
        text = "\n\n".join(page for page in pages if page)
        if text and any(not page for page in pages):
            warnings.append("Some pages contained no extractable text. Review the script for missing pages.")
        if not text:
            raise StudioError("No text could be extracted. The PDF may be scanned or image-based.")
        warnings.append("Review the extracted text and reading order before generating.")

    text = text.strip()
    if not text:
        raise StudioError("The file does not contain any script text.")
    if len(text) > SCRIPT_LIMIT:
        raise StudioError(f"The extracted script exceeds {SCRIPT_LIMIT:,} characters. Shorten it and try again.", 413)
    return {"text": text, "warnings": warnings}
