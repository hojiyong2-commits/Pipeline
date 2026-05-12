"""
automation.py
-------------
Pipeline: FEAT-20260425-6FFC

Watches an EmailMonitor parent folder for new CustomerOrderLines*.xlsx files.
On detection, parses the parent folder name, runs order_mapper, creates CORB
folder, copies source files, and updates the IC-Part R column with the CORB path.

Python 3.9 compatible.

# pip install watchdog openpyxl
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileCreatedEvent
except ImportError as _e:
    raise ImportError(
        "watchdog is not installed. Run: pip install watchdog"
    ) from _e

import order_mapper

logger = logging.getLogger("automation")


def _is_relative_to(path: Path, root: Path) -> bool:
    """Return True when path is inside root, compatible with Python 3.9."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

# ---------------------------------------------------------------------------
# Folder name parser
# ---------------------------------------------------------------------------

def parse_folder_name(folder_name: str) -> Dict[str, Optional[Any]]:
    """Parse EmailMonitor folder name and extract contract metadata.

    Expected format: "(ACT) 314134143 - 294,000 - 2026-04-16 - EXW"
    Segments separated by " - ". The last three segments are:
      amount - date - incoterm (final segment is incoterm).

    Args:
        folder_name: EmailMonitor folder name string.

    Returns:
        Dict with keys:
          "contract_amount" (str or None),
          "folder_date" (date or None),
          "incoterm" (str or None).

    Raises:
        TypeError: If folder_name is None or not str.
    """
    if folder_name is None:
        raise TypeError("folder_name must not be None")
    if not isinstance(folder_name, str):
        raise TypeError(f"folder_name must be str, got {type(folder_name).__name__}")

    result: Dict[str, Optional[Any]] = {
        "contract_amount": None,
        "folder_date": None,
        "incoterm": None,
    }

    stripped = folder_name.strip()
    if not stripped:
        return result

    parts = [p.strip() for p in stripped.split(" - ")]
    if len(parts) < 3:
        logger.warning("Folder name has fewer than 3 dash-separated segments: %r", folder_name)
        return result

    incoterm_raw = parts[-1].strip()
    if incoterm_raw:
        result["incoterm"] = incoterm_raw

    date_raw = parts[-2].strip()
    date_match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", date_raw)
    if date_match:
        try:
            result["folder_date"] = date(
                int(date_match.group(1)),
                int(date_match.group(2)),
                int(date_match.group(3)),
            )
        except ValueError:
            logger.warning("Invalid date in folder name: %r", date_raw)
    else:
        logger.warning("Cannot parse date from folder segment: %r", date_raw)

    amount_raw = parts[-3].strip()
    amount_match = re.fullmatch(r"[\d,]+(?:\.\d+)?", amount_raw)
    if amount_match:
        result["contract_amount"] = amount_raw
    else:
        logger.warning("Cannot parse contract amount from folder segment: %r", amount_raw)

    return result


# ---------------------------------------------------------------------------
# CORB path builder
# ---------------------------------------------------------------------------

_DATETIME_STR_PATTERN: re.Pattern = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}"
)
_WIN_INVALID_CHARS: re.Pattern = re.compile(r'[<>:"/\\|?*]')


def _sanitize_name_component(s: str) -> str:
    """Sanitize a folder-name component for Windows filesystem use.

    Args:
        s: Raw string component (project_id, po_no, or order_no).

    Returns:
        Sanitized string safe for use in a Windows path component.

    Raises:
        TypeError: If *s* is None or not str.
    """
    if s is None:
        raise TypeError("s must not be None")
    if not isinstance(s, str):
        raise TypeError(f"s must be str, got {type(s).__name__}")

    m = _DATETIME_STR_PATTERN.match(s)
    if m:
        s = m.group(1)
    s = _WIN_INVALID_CHARS.sub("", s)
    return s.strip(" .")


def build_corb_path(corb_base: str, project_id: str, po_no: str, order_no: str) -> str:
    """Build the CORB folder path for one IC-Part row.

    Folder name mirrors the IC-Distribution F1 formula:
      "{project_id} {po_no} {order_no}" (non-empty parts only, joined by space)

    Args:
        corb_base: Base directory for CORB folders (from GUI input).
        project_id: IC-Part project ID value.
        po_no: IC-Part PO No value.
        order_no: IC-Part Order No value (may include -2/-3 suffix).

    Returns:
        Absolute path string: corb_base / "project_id po_no order_no".

    Raises:
        TypeError: If any argument is None or not str.
        ValueError: If corb_base is empty.
    """
    if corb_base is None:
        raise TypeError("corb_base must not be None")
    if not isinstance(corb_base, str):
        raise TypeError(f"corb_base must be str, got {type(corb_base).__name__}")
    if not corb_base.strip():
        raise ValueError("corb_base must not be empty")

    if project_id is None:
        raise TypeError("project_id must not be None")
    if not isinstance(project_id, str):
        raise TypeError(f"project_id must be str, got {type(project_id).__name__}")

    if po_no is None:
        raise TypeError("po_no must not be None")
    if not isinstance(po_no, str):
        raise TypeError(f"po_no must be str, got {type(po_no).__name__}")

    if order_no is None:
        raise TypeError("order_no must not be None")
    if not isinstance(order_no, str):
        raise TypeError(f"order_no must be str, got {type(order_no).__name__}")

    safe_project_id = _sanitize_name_component(project_id)
    safe_po_no = _sanitize_name_component(po_no)
    safe_order_no = _sanitize_name_component(order_no)

    # Join only non-empty parts to avoid leading/trailing spaces
    _parts = [x for x in [safe_project_id, safe_po_no, safe_order_no] if x]
    folder_name = " ".join(_parts) if _parts else safe_order_no
    return str(Path(corb_base) / folder_name)


# ---------------------------------------------------------------------------
# File copier
# ---------------------------------------------------------------------------

def copy_files_to_corb(src_folder: Path, corb_path: Path) -> List[str]:
    """Copy all files (non-recursive) from src_folder to corb_path.

    Skips Excel/Word lock files (names starting with "~$").
    Uses atomic tempfile -> os.replace() copy for each file.
    Creates corb_path directory if it does not exist.

    Args:
        src_folder: Source directory (EmailMonitor folder).
        corb_path: Destination CORB directory.

    Returns:
        List of filenames successfully copied.

    Raises:
        TypeError: If any argument is None.
        FileNotFoundError: If src_folder does not exist.
    """
    if src_folder is None:
        raise TypeError("src_folder must not be None")
    if not isinstance(src_folder, Path):
        raise TypeError(f"src_folder must be Path, got {type(src_folder).__name__}")
    if corb_path is None:
        raise TypeError("corb_path must not be None")
    if not isinstance(corb_path, Path):
        raise TypeError(f"corb_path must be Path, got {type(corb_path).__name__}")
    if not src_folder.exists():
        raise FileNotFoundError(f"Source folder not found: {src_folder}")

    os.makedirs(str(corb_path), exist_ok=True)
    copied: List[str] = []

    for src_file in src_folder.iterdir():
        if not src_file.is_file():
            continue
        # Skip Excel/Word lock files (e.g. ~$CustomerOrderLines.xlsx)
        if src_file.name.startswith("~$"):
            logger.info("Skipping lock file: %s", src_file.name)
            continue
        dst = corb_path / src_file.name
        tmp_path: str = ""
        try:
            fd, tmp_path = tempfile.mkstemp(dir=str(corb_path))
            os.close(fd)
            shutil.copy2(str(src_file), tmp_path)
            os.replace(tmp_path, str(dst))
            copied.append(src_file.name)
            logger.info("Copied: %s → %s", src_file.name, corb_path)
        except Exception as exc:
            logger.warning("Failed to copy %s: %s", src_file.name, exc)
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    logger.info("copy_files_to_corb: %d files copied to %s", len(copied), corb_path)
    return copied


# ---------------------------------------------------------------------------
# Process orchestrator
# ---------------------------------------------------------------------------

def process_event(
    customer_order_lines_path: Path,
    email_monitor_folder: Path,
    ic_part_template_path: Path,
    corb_base_map: Dict[str, str],
    log_callback: Optional[Callable[[str], None]] = None,
) -> None:
    """Run full automation pipeline for one CustomerOrderLines file.

    Order of operations:
      1. Parse folder name -> metadata
      2. Read CustomerOrderLines
      3. Apply sub-order suffixes
      4. Build CORB path from first group
      5. Inject folder fields + corb_path into all groups
      6. Write IC-Part via order_mapper (accumulates; new rows appended)
      7. Copy EmailMonitor folder contents to CORB

    Args:
        customer_order_lines_path: Path to the detected CustomerOrderLines*.xlsx.
        email_monitor_folder: Parent folder of customer_order_lines_path.
        ic_part_template_path: IC-Part template .xlsx (written in-place).
        corb_base_map: Dict mapping IC code -> CORB base path.
        log_callback: Optional GUI log function (thread-safe via caller).
    """
    def _log(msg: str) -> None:
        logger.info(msg)
        if log_callback is not None:
            try:
                log_callback(msg)
            except Exception:
                pass

    _log(f"[자동화 시작] {email_monitor_folder.name}")

    meta = parse_folder_name(email_monitor_folder.name)
    folder_date: date = date.today()
    contract_amount: Optional[str] = meta["contract_amount"]
    incoterm: Optional[str] = meta["incoterm"]
    _log(f"  폴더 파싱: 날짜={folder_date}, 금액={contract_amount}, 인코텀={incoterm}")

    groups = order_mapper.read_customer_order_lines(customer_order_lines_path)
    if not groups:
        _log("  WARNING: CustomerOrderLines에 데이터 없음 — 처리 건너뇜")
        return
    order_mapper.apply_sub_order_suffixes(groups)
    _log(f"  주문 그룹 {len(groups)}개 파싱 완료")

    first = groups[0]
    ic_value = order_mapper.map_ic(first.customer_name)
    if ic_value in corb_base_map:
        corb_base = corb_base_map[ic_value]
    elif corb_base_map:
        corb_base = next(iter(corb_base_map.values()))
        _log(f"  WARNING: IC '{ic_value}' not in corb_base_map — using fallback: {corb_base}")
    else:
        raise ValueError("corb_base_map is empty — cannot determine CORB base path")
    corb_folder_path = build_corb_path(
        corb_base,
        first.project_id or "",
        first.po_no or "",
        first.order_no or "",
    )
    _log(f"  IC={ic_value}, CORB 경로: {corb_folder_path}")

    for g in groups:
        g.folder_date = folder_date
        if contract_amount is not None:
            g.contract_amount = contract_amount
        if incoterm is not None:
            g.incoterm = incoterm
        g.corb_path = corb_folder_path

    _log(f"  IC-Part 작성 중 (in-place): {ic_part_template_path.name}")
    order_mapper.write_ic_part_zip(groups, ic_part_template_path, ic_part_template_path)
    _log(f"  IC-Part 완료: {ic_part_template_path}")

    _log(f"  파일 복사: {email_monitor_folder} → {corb_folder_path}")
    copied = copy_files_to_corb(email_monitor_folder, Path(corb_folder_path))
    _log(f"  복사 완료: {len(copied)}개 파일 → {corb_folder_path}")

    # CORB 경로 텍스트 파일 생성
    _corb_txt = email_monitor_folder / "corb_path.txt"
    try:
        _corb_txt.write_text(corb_folder_path, encoding="utf-8")
        _log(f"  CORB 경로 파일 생성: {_corb_txt}")
    except OSError as _exc:
        _log(f"  WARNING: CORB 경로 파일 생성 실패: {_exc}")

    _log(f"[자동화 완료] {email_monitor_folder.name}")


# ---------------------------------------------------------------------------
# Watchdog integration
# ---------------------------------------------------------------------------

class _CustomerOrderHandler(FileSystemEventHandler):
    """Watchdog event handler: triggers on CustomerOrderLines*.xlsx creation."""

    def __init__(
        self,
        ic_part_template: str,
        corb_base_map: Dict[str, str],
        log_callback: Optional[Callable[[str], None]],
        debounce_seconds: float = 1.5,
    ) -> None:
        super().__init__()
        self._ic_part_template = Path(ic_part_template)
        self._corb_base_map = corb_base_map
        self._corb_roots: List[Path] = [
            Path(str(path_value))
            for path_value in corb_base_map.values()
            if str(path_value).strip()
        ]
        self._log_callback = log_callback
        self._debounce_seconds = debounce_seconds
        self._timers: Dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._ic_part_lock = threading.Lock()

    def _is_corb_output(self, path: Path) -> bool:
        """Return True when path is under one of the configured CORB roots."""
        return any(_is_relative_to(path, root) for root in self._corb_roots)

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        src_path = Path(event.src_path)
        if not (src_path.name.startswith("CustomerOrderLines") and src_path.suffix.lower() == ".xlsx") or src_path.name.startswith("~$"):
            return
        if self._is_corb_output(src_path):
            logger.info("CORB 경로 안의 CustomerOrderLines 파일은 재처리하지 않음: %s", src_path)
            return
        key = str(src_path)
        with self._lock:
            if key in self._timers:
                self._timers[key].cancel()
            timer = threading.Timer(self._debounce_seconds, self._handle, args=[src_path])
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    def _handle(self, src_path: Path) -> None:
        with self._lock:
            self._timers.pop(str(src_path), None)

        # Resolve the actual file to process after debounce delay.
        # The original file may have been moved or deleted by the time the timer fires.
        if src_path.exists():
            resolved_path = src_path
        else:
            # Search the same folder for any surviving CustomerOrderLines*.xlsx.
            # Exclude Excel/Word lock files (names starting with "~$").
            candidates: List[Path] = [
                p for p in src_path.parent.glob("CustomerOrderLines*.xlsx")
                if not p.name.startswith("~$")
                and not self._is_corb_output(p)
            ]
            if candidates:
                resolved_path = max(candidates, key=lambda p: p.stat().st_mtime)
                logger.info(
                    "원본 파일 없음, 대체 파일 사용: %s → %s",
                    src_path.name,
                    resolved_path.name,
                )
            else:
                logger.warning(
                    "CustomerOrderLines 파일 없음 (삭제/이동 추정) — 처리 건너뜀: %s",
                    src_path.name,
                )
                return

        def _run() -> None:
            with self._ic_part_lock:
                try:
                    process_event(
                        customer_order_lines_path=resolved_path,
                        email_monitor_folder=resolved_path.parent,
                        ic_part_template_path=self._ic_part_template,
                        corb_base_map=self._corb_base_map,
                        log_callback=self._log_callback,
                    )
                except Exception as exc:
                    msg = f"[오류] {resolved_path.name}: {exc}"
                    logger.exception(msg)
                    if self._log_callback:
                        try:
                            self._log_callback(msg)
                        except Exception:
                            pass

        t = threading.Thread(target=_run, daemon=True)
        t.start()


class FolderWatcher:
    """Watches a directory for new CustomerOrderLines*.xlsx files."""

    def __init__(
        self,
        watch_dir: str,
        ic_part_template: str,
        corb_base_map: Dict[str, str],
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        if watch_dir is None:
            raise TypeError("watch_dir must not be None")
        if not isinstance(watch_dir, str):
            raise TypeError(f"watch_dir must be str, got {type(watch_dir).__name__}")
        if not watch_dir.strip():
            raise ValueError("watch_dir must not be empty")

        if ic_part_template is None:
            raise TypeError("ic_part_template must not be None")
        if not isinstance(ic_part_template, str):
            raise TypeError(f"ic_part_template must be str, got {type(ic_part_template).__name__}")

        if corb_base_map is None:
            raise TypeError("corb_base_map must not be None")
        if not isinstance(corb_base_map, dict):
            raise TypeError(f"corb_base_map must be dict, got {type(corb_base_map).__name__}")
        if not corb_base_map:
            raise ValueError("corb_base_map must not be empty")

        self._watch_dir = watch_dir
        self._handler = _CustomerOrderHandler(
            ic_part_template=ic_part_template,
            corb_base_map=corb_base_map,
            log_callback=log_callback,
        )
        self._observer: Optional[Observer] = None

    def start(self) -> None:
        if self._observer is not None and self._observer.is_alive():
            return
        self._observer = Observer()
        self._observer.schedule(self._handler, self._watch_dir, recursive=True)
        self._observer.start()
        logger.info("FolderWatcher started: %s", self._watch_dir)

    def stop(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=3.0)
            except Exception as exc:
                logger.warning("FolderWatcher stop error: %s", exc)
            finally:
                self._observer = None
        logger.info("FolderWatcher stopped")


# ---------------------------------------------------------------------------
# Self-verification block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path

    print("=== automation.py SELF-VERIFY START ===")

    _result = parse_folder_name("(ACT) 314134143 - 294,000 - 2026-04-16 - EXW")
    assert _result["contract_amount"] == "294,000"
    assert _result["folder_date"] == date(2026, 4, 16)
    assert _result["incoterm"] == "EXW"

    _result_empty = parse_folder_name("")
    assert _result_empty["contract_amount"] is None
    assert _result_empty["folder_date"] is None
    assert _result_empty["incoterm"] is None

    try:
        parse_folder_name(None)  # type: ignore[arg-type]
        assert False
    except TypeError:
        pass

    assert _sanitize_name_component("2026-06-02 00:00:00") == "2026-06-02"
    assert _sanitize_name_component("A:B<C>D") == "ABCD"
    assert _sanitize_name_component("  .hello. ") == "hello"

    try:
        _sanitize_name_component(None)  # type: ignore[arg-type]
        assert False
    except TypeError:
        pass

    _corb = build_corb_path("C:/CORB", "B1234", "PO999", "X100050542")
    assert _corb.endswith("B1234 PO999 X100050542"), f"build_corb_path mismatch: {_corb!r}"

    _corb_dt = build_corb_path("C:/CORB", "B1234", "PO999", "2026-06-02 00:00:00")
    assert ":" not in _corb_dt.split("CORB", 1)[-1]
    assert _corb_dt.endswith("B1234 PO999 2026-06-02")

    # empty project_id -> no leading space
    _p = build_corb_path("C:/CORB", "", "PO999", "X100050542")
    assert not _Path(_p).name.startswith(" "), f"folder name must not start with space: {_Path(_p).name!r}"
    assert _Path(_p).name == "PO999 X100050542", f"expected 'PO999 X100050542', got {_Path(_p).name!r}"

    _p2 = build_corb_path("C:/CORB", "", "", "X100050542")
    assert _Path(_p2).name == "X100050542", f"expected 'X100050542', got {_Path(_p2).name!r}"

    try:
        build_corb_path(None, "B1234", "PO999", "X100")  # type: ignore[arg-type]
        assert False
    except TypeError:
        pass

    try:
        build_corb_path("   ", "B1234", "PO999", "X100")
        assert False
    except ValueError:
        pass

    _cmap = {"SoCal": "C:/CORB/SoCal", "SG": "C:/CORB/SG", "AT": "C:/CORB/AT"}
    try:
        FolderWatcher(None, "t.xlsx", _cmap)  # type: ignore[arg-type]
        assert False
    except TypeError:
        pass

    try:
        FolderWatcher("", "t.xlsx", _cmap)
        assert False
    except ValueError:
        pass

    try:
        FolderWatcher("C:/watch", "t.xlsx", None)  # type: ignore[arg-type]
        assert False
    except TypeError:
        pass

    try:
        FolderWatcher("C:/watch", "t.xlsx", {})
        assert False
    except ValueError:
        pass

    try:
        copy_files_to_corb(None, _Path("C:/out"))  # type: ignore[arg-type]
        assert False
    except TypeError:
        pass

    try:
        copy_files_to_corb(_Path("C:/nonexistent_xyzzy_12345"), _Path("C:/out"))
        assert False
    except FileNotFoundError:
        pass

    # ~$ file should be skipped
    import tempfile as _tf, os as _os, shutil as _sh
    _tmp_src = _Path(_tf.mkdtemp())
    (_tmp_src / "~$CustomerOrderLines.xlsx").write_text("lock")
    (_tmp_src / "real.pdf").write_text("data")
    _tmp_dst = _Path(_tf.mkdtemp())
    _copied = copy_files_to_corb(_tmp_src, _tmp_dst)
    assert "~$CustomerOrderLines.xlsx" not in _copied, "~$ file must be skipped"
    assert "real.pdf" in _copied, "real file must be copied"
    _sh.rmtree(str(_tmp_src))
    _sh.rmtree(str(_tmp_dst))

    print("=== automation.py SELF-VERIFY OK ===")
