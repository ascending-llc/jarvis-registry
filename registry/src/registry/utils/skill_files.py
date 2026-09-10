"""Pure helpers for inline skill-file content handling.

Shared by the Skill CRUD service (`services/skill_service.py`) and the GitHub
sync apply service (`services/skill_sync_apply_service.py`) so text/binary
detection stays a single source of truth.
"""

import mimetypes


def is_text_content(content: bytes) -> bool:
    """Treat as text only when the FULL payload contains no NUL bytes and fully decodes as UTF-8.

    Deliberately scans the whole payload, not just a prefix: the original GitHub-sync check only
    looked at the first 8192 bytes, which could misclassify a file as text and then have the sync
    path corrupt it via `content.decode("utf-8", errors="replace")`.
    """
    if b"\x00" in content:
        return False
    try:
        content.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def guess_mime_type(relative_path: str) -> str:
    """Guess a MIME type from the path, falling back to a binary default."""
    return mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
