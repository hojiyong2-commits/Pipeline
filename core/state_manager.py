"""
state_manager.py -- StateManager class.

Persists and restores the last processed row index (last_row) to a JSON
state file.  All file I/O is atomic (tempfile → os.replace).
"""

import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from core.encoding_utils import safe_read_json

logger = logging.getLogger(__name__)


def get_state_path() -> Path:
    """Return the absolute path to state.json beside the EXE or script.

    In a frozen (PyInstaller) build the file lives beside the EXE.
    In development it lives beside this module file's parent directory.

    Returns:
        Absolute Path to state.json.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "state.json"
    return Path(__file__).resolve().parent.parent / "state.json"


class StateManager:
    """Persists last_row state to a JSON file with atomic write semantics.

    - load(): never raises; returns {"last_row": None} on any failure.
    - save(): raises OSError on write failure so the caller can respond.
    - get_start_row(): AL.type_valid 4-item compliant.
    """

    def load(self, path: str) -> Dict[str, Any]:
        """Load state from the JSON file at *path*.

        Returns {"last_row": None} when the file is absent, unreadable, or
        contains invalid JSON.  Exceptions are swallowed and logged so the
        caller always receives a valid (if empty) state dict.

        Args:
            path: Absolute or relative path to the state JSON file.

        Returns:
            A dict with at least the key "last_row" (value int or None).
        """
        # AL.type_valid — None check before isinstance
        if path is None:
            raise TypeError("path must not be None")
        if not isinstance(path, str):
            raise TypeError(
                f"path must be str, got {type(path).__name__}"
            )
        # path may be empty string (edge case) — treat as no state file
        if not path:
            logger.warning("StateManager.load: empty path — returning default state")
            return {"last_row": None}

        state_path = Path(path)
        if not state_path.is_file():
            logger.info("StateManager.load: state file not found at %s — returning default", path)
            return {"last_row": None}

        try:
            data: Any = safe_read_json(state_path)
        except (ValueError, OSError) as exc:
            logger.warning(
                "StateManager.load: failed to read state file '%s': %s — returning default",
                path,
                exc,
            )
            return {"last_row": None}

        if not isinstance(data, dict):
            logger.warning(
                "StateManager.load: state file does not contain a dict (got %s) — "
                "returning default",
                type(data).__name__,
            )
            return {"last_row": None}

        # Ensure last_row key is always present
        if "last_row" not in data:
            data["last_row"] = None

        logger.debug("StateManager.load: loaded state=%s from %s", data, path)
        return data

    def save(self, path: str, data: Dict[str, Any]) -> None:
        """Atomically write *data* as JSON to the state file at *path*.

        Uses tempfile → os.replace to prevent partial writes.

        Args:
            path: Absolute or relative path to the target state JSON file.
            data: The state dict to persist (must be JSON-serialisable).

        Raises:
            TypeError:  If *path* is not a str, or *data* is not a dict.
            OSError:    If the write or rename operation fails.
        """
        # AL.type_valid — None + isinstance checks
        if path is None:
            raise TypeError("path must not be None")
        if not isinstance(path, str):
            raise TypeError(
                f"path must be str, got {type(path).__name__}"
            )
        if data is None:
            raise TypeError("data must not be None")
        if not isinstance(data, dict):
            raise TypeError(
                f"data must be dict, got {type(data).__name__}"
            )

        import json

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        tmp_fd: Optional[int] = None
        tmp_path: Optional[str] = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                suffix=".tmp", dir=str(target.parent)
            )
            try:
                os.write(tmp_fd, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
            finally:
                os.close(tmp_fd)
                tmp_fd = None
            os.replace(tmp_path, str(target))
            tmp_path = None  # rename succeeded; no cleanup needed
            logger.debug("StateManager.save: state written to %s", target)
        except OSError:
            if tmp_fd is not None:
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise

    def get_start_row(self, state: Dict[str, Any], user_start_row: int) -> int:
        """Determine the first row to process in the current run.

        Resolution logic:
        - state["last_row"] is None           → return user_start_row
        - state["last_row"] is a positive int → return last_row + 1
        - state["last_row"] is anything else  → return user_start_row (fallback)

        AL.type_valid 4 items:
        1. user_start_row is None → TypeError
        2. user_start_row ≤ 0    → ValueError
        3. non-int type           → TypeError (float/str blocked — no implicit cast)
        4. positive integer allowed — used as 1-indexed Excel row number (inline below)

        Args:
            state:          State dict loaded via load().
            user_start_row: The GUI-configured starting row (1-indexed, must be ≥ 1).

        Returns:
            The 1-indexed row number from which to begin reading Excel data.

        Raises:
            TypeError:  If user_start_row is None or not an int (float/str blocked).
            ValueError: If user_start_row is ≤ 0.
        """
        # state None check (AL.type_valid item-1 for state param)
        if state is None:
            raise TypeError("state must not be None")
        # 1. None check
        if user_start_row is None:
            raise TypeError("user_start_row must not be None")
        # 3. isinstance check — float/str/bool are all rejected
        if isinstance(user_start_row, bool) or not isinstance(user_start_row, int):
            raise TypeError(
                f"user_start_row must be int, got {type(user_start_row).__name__}"
            )
        # 2. Boundary check — positive integer allowed (1-indexed row number)
        if user_start_row <= 0:
            raise ValueError(
                f"user_start_row must be positive (>= 1), got {user_start_row}"
            )
        # 4. positive integer allowed — used as 1-indexed Excel row number

        last_row: Any = state.get("last_row") if isinstance(state, dict) else None

        if last_row is None:
            logger.debug(
                "get_start_row: no previous state — returning user_start_row=%d",
                user_start_row,
            )
            return user_start_row

        # Accept only genuine int (not bool) for last_row
        if isinstance(last_row, bool) or not isinstance(last_row, int):
            logger.warning(
                "get_start_row: state last_row is not int (got %s) — "
                "falling back to user_start_row=%d",
                type(last_row).__name__,
                user_start_row,
            )
            return user_start_row

        result = last_row + 1
        logger.debug(
            "get_start_row: last_row=%d → start_row=%d", last_row, result
        )
        return result


# ---------------------------------------------------------------------------
# Self-verification block
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile as _tmpmod
    import os as _os

    mgr = StateManager()

    # --- get_start_row: normal input ---
    s1 = mgr.get_start_row({"last_row": None}, 3)
    assert s1 == 3, f"Expected 3, got {s1}"

    s2 = mgr.get_start_row({"last_row": 7}, 3)
    assert s2 == 8, f"Expected 8, got {s2}"

    s3 = mgr.get_start_row({"last_row": "bad"}, 5)
    assert s3 == 5, f"Expected fallback 5, got {s3}"

    # --- get_start_row: None input raises ---
    try:
        mgr.get_start_row({}, None)  # type: ignore[arg-type]
        assert False, "Expected TypeError"
    except TypeError:
        pass

    # --- get_start_row: float raises TypeError ---
    try:
        mgr.get_start_row({}, 1.0)  # type: ignore[arg-type]
        assert False, "Expected TypeError"
    except TypeError:
        pass

    # --- get_start_row: zero raises ValueError ---
    try:
        mgr.get_start_row({}, 0)
        assert False, "Expected ValueError"
    except ValueError:
        pass

    # --- save / load round-trip ---
    with _tmpmod.TemporaryDirectory() as tmp_dir:
        tmp_state = _os.path.join(tmp_dir, "state.json")
        mgr.save(tmp_state, {"last_row": 42})
        loaded = mgr.load(tmp_state)
        assert loaded.get("last_row") == 42, f"Round-trip failed: {loaded}"

    # --- load: missing file returns default ---
    default_state = mgr.load("/nonexistent/path/state.json")
    assert default_state == {"last_row": None}, f"Expected default, got {default_state}"

    print("[SELF-VERIFY] OK")
