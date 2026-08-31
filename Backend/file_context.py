"""
File upload -> query context (Phase 5.5, see ENHANCEMENT_PLAN.md).

Before this, Frontend/GUI.py's upload button worked (Phase 1 made it emit a
real signal instead of writing a dead Frontend/Files/UploadedFile.data), but
Backend/agent_worker.py only ever appended the file's *path* as a string to
the next query — the model never saw what was actually in the file, since
nothing read it.

Supports .txt/.md directly and .pdf via pypdf, per ENHANCEMENT_PLAN.md 5.5.
Anything else (including images — "if you add a vision model", which this
project doesn't have) falls back to naming the file only, same degraded-but-
not-broken behavior as before this module existed.
"""

import logging
import os

logger = logging.getLogger(__name__)

MAX_CHARS = 6000  # keeps a large upload from blowing the model's context window


def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _read_pdf_file(path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def extract_text(path: str) -> str:
    """Best-effort text extraction for a query attachment. Returns "" (not
    an exception) for anything unsupported or unreadable — the caller
    treats that as "describe the file by name only", not a hard failure;
    an unreadable attachment shouldn't block the rest of the query."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".txt", ".md"):
            text = _read_text_file(path)
        elif ext == ".pdf":
            text = _read_pdf_file(path)
        else:
            return ""
    except Exception as e:
        logger.warning("could not read %s: %s", path, e)
        return ""

    text = text.strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n...[truncated]"
    return text


def build_attachment_context(path: str) -> str:
    """Returns the text block to append to the user's next query."""
    filename = os.path.basename(path)
    text = extract_text(path)
    if text:
        return f"\n\n[User attached a file, {filename}, with this content:]\n{text}"
    return f"\n\n[User attached a file: {filename} (content could not be read/is unsupported)]"
