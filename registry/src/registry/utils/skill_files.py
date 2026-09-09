"""Pure helpers for inline skill-file content handling.

Shared by the Skill CRUD service (`services/skill_service.py`) and the GitHub
sync apply service (`services/skill_sync_apply_service.py`) so text/binary
detection stays a single source of truth.
"""

import mimetypes

_TEXT_SNIFF_BYTES = 8192


def is_text_content(content: bytes) -> bool:
    """Heuristic: text when the first 8 KiB has no NUL byte and decodes as UTF-8."""
    if b"\x00" in content[:_TEXT_SNIFF_BYTES]:
        return False
    try:
        content[:_TEXT_SNIFF_BYTES].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def guess_mime_type(relative_path: str) -> str:
    """Guess a MIME type from the path, falling back to a binary default."""
    return mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
