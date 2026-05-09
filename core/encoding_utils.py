"""
encoding_utils.py -- Safe file reading utilities with 4-stage encoding fallback.

Encoding order: utf-8 -> cp949 -> euc-kr -> latin-1

FileNotFoundError and PermissionError are intentionally NOT caught here;
they propagate to the caller so the caller can handle missing/locked files
with appropriate defaults or error messages.
"""

import json
import logging
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_ENCODING_CHAIN: List[str] = ["utf-8", "cp949", "euc-kr", "latin-1"]


def safe_read_text(path: Path) -> str:
    """Read a text file with 4-stage encoding fallback.

    Tries encodings in order: utf-8, cp949, euc-kr, latin-1.
    Raises ValueError if all encodings fail.
    FileNotFoundError and PermissionError are NOT caught and propagate to caller.

    Args:
        path: Path to the text file.

    Returns:
        File contents as a string.

    Raises:
        FileNotFoundError: If the file does not exist (propagated, not caught).
        PermissionError: If the file cannot be read (propagated, not caught).
        ValueError: If all 4 encodings fail to decode the file.
    """
    if not isinstance(path, Path):
        path = Path(path)

    last_error: Optional[UnicodeDecodeError] = None
    for enc in _ENCODING_CHAIN:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError as exc:
            logger.debug("Encoding '%s' failed for %s: %s", enc, path, exc)
            last_error = exc

    raise ValueError(
        f"safe_read_text: all encodings {_ENCODING_CHAIN} failed for '{path}'. "
        f"Last error: {last_error}"
    )


def safe_read_json(path: Path) -> Any:
    """Read and parse a JSON file with 4-stage encoding fallback.

    Tries encodings in order: utf-8, cp949, euc-kr, latin-1.
    Raises ValueError on any failure (encoding or JSON parse).
    FileNotFoundError and PermissionError are NOT caught and propagate to caller.

    Args:
        path: Path to the JSON file.

    Returns:
        Parsed JSON value (typically dict or list).

    Raises:
        FileNotFoundError: If the file does not exist (propagated, not caught).
        PermissionError: If the file cannot be read (propagated, not caught).
        ValueError: If all encodings fail, or if JSON parsing fails.
    """
    if not isinstance(path, Path):
        path = Path(path)

    last_unicode_error: Optional[UnicodeDecodeError] = None
    for enc in _ENCODING_CHAIN:
        try:
            raw = path.read_text(encoding=enc)
        except UnicodeDecodeError as exc:
            logger.debug("Encoding '%s' failed for %s: %s", enc, path, exc)
            last_unicode_error = exc
            continue

        # Successfully decoded with this encoding; attempt JSON parse.
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"safe_read_json: JSON parse error in '{path}' "
                f"(decoded with '{enc}'): {exc}"
            ) from exc

    raise ValueError(
        f"safe_read_json: all encodings {_ENCODING_CHAIN} failed for '{path}'. "
        f"Last error: {last_unicode_error}"
    )
