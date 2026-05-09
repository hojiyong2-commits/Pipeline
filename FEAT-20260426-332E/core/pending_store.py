"""Persistent store for packing-pending rows (pending_packing.json)."""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Optional, TypedDict

logger = logging.getLogger(__name__)

_FILENAME = "pending_packing.json"


class PendingEntry(TypedDict):
    """TypedDict schema for a single pending packing row."""

    sn: str
    project_id: str
    kit_place: str
    line_nos: List[str]


def load_pending(config_dir: Path) -> List[PendingEntry]:
    """Load pending entries from pending_packing.json.

    Returns [] if file missing, unreadable, or malformed.
    Encoding fallback: utf-8 -> cp949 -> latin-1.

    Args:
        config_dir: Directory that contains pending_packing.json.

    Returns:
        List of PendingEntry dicts. Empty list on any read/parse failure.

    Raises:
        TypeError: If config_dir is None or not a Path instance.
    """
    # AL item 1: None guard
    if config_dir is None:
        raise TypeError("config_dir must not be None")
    # AL item 3: isinstance type guard
    if not isinstance(config_dir, Path):
        raise TypeError(
            f"config_dir must be Path, got {type(config_dir).__name__}"
        )
    # AL item 2 & 4: config_dir is a Path (non-numeric); 0/negative boundary not applicable
    # for Path objects — no numeric boundary check needed here.

    path = config_dir / _FILENAME
    if not path.exists():
        return []

    # FS.encoding: utf-8 -> cp949 -> latin-1 fallback (single encoding forbidden)
    raw: Optional[str] = None
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            raw = path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if raw is None:
        logger.warning("pending_packing.json: 인코딩 감지 실패, 빈 목록 사용")
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning(
            "pending_packing.json JSON 파싱 실패: %s — 빈 목록 사용", exc
        )
        return []

    if not isinstance(data, list):
        logger.warning("pending_packing.json 루트가 list가 아님 — 빈 목록 사용")
        return []

    return data


def save_pending(config_dir: Path, entries: List[PendingEntry]) -> None:
    """Atomically save pending entries to pending_packing.json.

    Uses tempfile -> os.replace for atomic write (FS.safe_write pattern).

    Args:
        config_dir: Directory that will contain pending_packing.json.
        entries: List of PendingEntry dicts to persist.

    Raises:
        TypeError: If config_dir is None / not Path, or entries is None / not list.
        RuntimeError: If write fails, chained from the underlying exception.
    """
    # AL item 1: None guard — config_dir
    if config_dir is None:
        raise TypeError("config_dir must not be None")
    # AL item 3: isinstance type guard — config_dir
    if not isinstance(config_dir, Path):
        raise TypeError(
            f"config_dir must be Path, got {type(config_dir).__name__}"
        )
    # AL item 1: None guard — entries
    if entries is None:
        raise TypeError("entries must not be None")
    # AL item 3: isinstance type guard — entries
    if not isinstance(entries, list):
        raise TypeError(
            f"entries must be list, got {type(entries).__name__}"
        )
    # AL item 2 & 4: empty list is allowed — empty pending means nothing is pending;
    # no negative/zero boundary applies to list length in this context.

    content = json.dumps(entries, ensure_ascii=False, indent=2)
    fd, tmp_path_str = tempfile.mkstemp(
        dir=str(config_dir), suffix=".json.tmp"
    )
    tmp_path: Optional[str] = tmp_path_str
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path_str, str(config_dir / _FILENAME))
        tmp_path = None  # rename succeeded; no cleanup needed
    except Exception as exc:
        raise RuntimeError(
            f"pending_packing.json 저장 실패: {exc}"
        ) from exc
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


if __name__ == "__main__":
    import tempfile as _tempfile

    with _tempfile.TemporaryDirectory() as _tmp:
        _d = Path(_tmp)

        # 1. Missing file -> []
        assert load_pending(_d) == [], "missing file should return []"

        # 2. Save + load roundtrip
        _entries: List[PendingEntry] = [
            PendingEntry(
                sn="SN001", project_id="P001",
                kit_place="포장반", line_nos=["1", "2"]
            )
        ]
        save_pending(_d, _entries)
        _loaded = load_pending(_d)
        assert len(_loaded) == 1, "roundtrip length should be 1"
        assert _loaded[0]["sn"] == "SN001", "roundtrip sn mismatch"

        # 3. Save empty -> load []
        save_pending(_d, [])
        assert load_pending(_d) == [], "empty save should return []"

        # 4. TypeError: None config_dir for load
        try:
            load_pending(None)  # type: ignore[arg-type]
            assert False, "예외 미발생"
        except TypeError:
            pass

        # 5. TypeError: None config_dir for save
        try:
            save_pending(None, [])  # type: ignore[arg-type]
            assert False, "예외 미발생"
        except TypeError:
            pass

        # 6. TypeError: None entries for save
        try:
            save_pending(_d, None)  # type: ignore[arg-type]
            assert False, "예외 미발생"
        except TypeError:
            pass

        # 7. TypeError: wrong type for entries
        try:
            save_pending(_d, "not a list")  # type: ignore[arg-type]
            assert False, "예외 미발생"
        except TypeError:
            pass

    print("[SELF-VERIFY] pending_store.py OK")
