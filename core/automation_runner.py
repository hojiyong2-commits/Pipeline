"""
automation_runner.py — AutomationRunner class.

Orchestrates the full pipeline:
  1. (Optional) check if the Excel file was modified yesterday
  2. Read the configured cell range
  3. Mark processed cells with the background colour
  4. Compose the email
  5. Send via Outlook COM

All logging goes to logs/app.log relative to the EXE (or script) directory.
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config_manager import AppConfig
from core.excel_handler import ExcelHandler
from core.email_composer import EmailComposer
from core.outlook_sender import OutlookSender
from core.state_manager import StateManager, get_state_path

# SEC-009: Generic init-error message — no exception detail exposed to the UI
_SENDER_UNAVAILABLE_MSG = (
    "Outlook COM 인터페이스를 초기화할 수 없습니다. "
    "pywin32가 설치되어 있고 Outlook이 실행 중인지 확인하십시오."
)


def _setup_logger() -> logging.Logger:
    """Configure the module-level logger to write to logs/app.log.

    The log directory is resolved relative to the EXE in a frozen build,
    or relative to this source file during development.

    Returns:
        A Logger instance for automation_runner.
    """
    if getattr(sys, "frozen", False):
        log_dir = Path(sys.executable).resolve().parent / "logs"
    else:
        log_dir = Path(__file__).resolve().parent.parent / "logs"

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Fall back to stderr-only logging if the log dir cannot be created.
        logging.basicConfig(level=logging.DEBUG)
        logging.warning("automation_runner: cannot create log dir %s: %s", log_dir, exc)
        return logging.getLogger(__name__)

    log_path = log_dir / "app.log"
    logger = logging.getLogger(__name__)
    if not logger.handlers:
        handler = logging.FileHandler(str(log_path), encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    return logger


logger = _setup_logger()


class AutomationRunner:
    """Runs the full daily Excel → Outlook email automation pipeline.

    AL-02 compliance:
    - check_modified=False: modification check is skipped entirely.
    - Excel read failure: pipeline stops; compose/send are never called.
    - mark_cells failure: non-fatal; pipeline continues with mark_failed=True.
    """

    def __init__(self, config: AppConfig) -> None:
        """Initialise the runner with all required sub-components.

        Args:
            config: The loaded AppConfig instance.
        """
        self._config = config
        self._excel_handler = ExcelHandler(config)
        self._email_composer = EmailComposer(config)

        # OutlookSender may raise ImportError if pywin32 is absent.
        try:
            self._outlook_sender: Optional[OutlookSender] = OutlookSender()
        except ImportError as exc:
            # SEC-009: log the real exception for diagnostics, but store only
            # the generic message so str(exc) / path info is never surfaced to
            # the UI or returned in the pipeline result dict.
            logger.error("AutomationRunner: OutlookSender unavailable: %s", exc)
            self._outlook_sender = None
            self._sender_init_error: str = _SENDER_UNAVAILABLE_MSG
        else:
            self._sender_init_error = ""

    def run(self, force: bool = False) -> Dict[str, Any]:
        """Execute the automation pipeline and return a status dict.

        Pipeline steps and their short-circuit behaviour:

        1. If force=False and check_modified is True: call is_modified_yesterday().
           → If False, return {"status": "skipped", "reason": "no_changes", "forced": False}.
           If force=True: Step 1 is bypassed entirely regardless of check_modified config.
        2. read_cells() — on failure return {"status": "error", "stage": "excel_read", ...}.
        3. mark_cells() — failure is non-fatal; sets "mark_failed": True in the
           eventual result but does NOT stop the pipeline.
        4. compose() — builds the email content dict.
        5. send() — on failure return {"status": "error", "stage": "email_send", ...}.
        6. On full success return {"status": "success", "timestamp": ISO str, "subject": str}.

        All returned dicts include a "forced" key indicating whether the modification
        check was bypassed by the caller.

        Args:
            force: When True, Step 1 (is_modified_yesterday check) is skipped
                   unconditionally. When False (default), existing check_modified
                   config behaviour is preserved exactly.

        Returns:
            A Dict[str, Any] describing the pipeline outcome, always containing
            a "forced" bool key.
        """
        logger.info("AutomationRunner.run() started (force=%s)", force)

        # Step 1 — modification check (optional, bypassed when force=True)
        if force:
            logger.info("run: force=True — bypassing modification check entirely")
        else:
            check_modified: bool = bool(self._config.get("check_modified", True))
            if check_modified:
                logger.info("run: check_modified=True — checking file mtime")
                if not self._excel_handler.is_modified_yesterday():
                    logger.info("run: file was not modified yesterday — skipping")
                    return {"status": "skipped", "reason": "no_changes", "forced": False}
            else:
                logger.info("run: check_modified=False — skipping mtime check")

        # Step 2 — StateManager: determine actual start/end rows dynamically
        state_mgr = StateManager()
        state_path_val: str = str(get_state_path())
        state: Dict[str, Any] = state_mgr.load(state_path_val)

        user_start_row: int = int(self._config.get("user_start_row", 1))
        col_start: int = int(self._config.get("col_start", 1))
        col_end: int = int(self._config.get("col_end", 10))

        try:
            actual_start_row: int = state_mgr.get_start_row(state, user_start_row)
        except (TypeError, ValueError) as exc:
            logger.error("run: get_start_row failed: %s — falling back to user_start_row", exc)
            actual_start_row = user_start_row

        # Detect end row dynamically from the worksheet
        excel_path_str: str = self._config.get("excel_path", "")
        actual_end_row: int = 0

        if excel_path_str:
            try:
                import openpyxl  # type: ignore[import]
                from core.excel_handler import _resolve_excel_path  # type: ignore[attr-defined]

                resolved = _resolve_excel_path(excel_path_str)
                if resolved is not None and resolved.is_file():
                    cell_range = self._config.get("cell_range", {})
                    sheet_name: str = cell_range.get("sheet_name", "Sheet1")
                    wb_detect = openpyxl.load_workbook(
                        str(resolved), read_only=False, data_only=True
                    )
                    try:
                        if sheet_name in wb_detect.sheetnames:
                            ws_detect = wb_detect[sheet_name]
                            actual_end_row = self._excel_handler.detect_end_row(
                                ws_detect, actual_start_row, col_start, col_end
                            )
                    finally:
                        wb_detect.close()
                else:
                    logger.warning("run: excel_path invalid for end-row detection")
            except Exception as exc:
                logger.error("run: end-row detection failed: %s", exc)

        if actual_end_row == 0:
            logger.info(
                "run: no new rows to process (start_row=%d is empty or file unreachable)",
                actual_start_row,
            )
            return {"status": "skipped", "reason": "no_new_rows", "forced": force}

        # Step 2b — read cells using dynamic range
        logger.info(
            "run: reading Excel cells (rows %d–%d, cols %d–%d)",
            actual_start_row, actual_end_row, col_start, col_end,
        )
        read_result = self._excel_handler.read_cells_dynamic(
            actual_start_row, actual_end_row, col_start, col_end
        )
        if not read_result.get("success", False):
            error_msg = read_result.get("error_msg", "unknown error")
            logger.error("run: Excel read failed: %s", error_msg)
            return {
                "status": "error",
                "stage": "excel_read",
                "error": error_msg,
                "forced": force,
            }

        # Step 3 — mark cells (non-fatal)
        logger.info("run: marking processed cells")
        mark_ok: bool = self._excel_handler.mark_cells()
        if not mark_ok:
            logger.warning("run: mark_cells() failed — continuing pipeline")

        # Step 4 — compose email (pass highlight_ranges from config if present)
        logger.info("run: composing email")
        highlight_ranges: List[Dict[str, str]] = self._config.get("highlight_ranges", [])
        email_content: Dict[str, Any] = self._email_composer.compose(
            read_result, highlight_ranges=highlight_ranges
        )

        # Step 5 — send via Outlook
        if self._outlook_sender is None:
            logger.error("run: OutlookSender not available: %s", self._sender_init_error)
            return {
                "status": "error",
                "stage": "email_send",
                "error": self._sender_init_error,
                "forced": force,
            }

        logger.info("run: sending email via Outlook COM")
        send_result = self._outlook_sender.send(email_content)
        if not send_result.get("success", False):
            error_msg = send_result.get("error_msg", "unknown send error")
            logger.error("run: email send failed: %s", error_msg)
            result: Dict[str, Any] = {
                "status": "error",
                "stage": "email_send",
                "error": error_msg,
                "forced": force,
            }
            if not mark_ok:
                result["mark_failed"] = True
            return result

        # Step 5b — persist last_row after successful send
        try:
            state_mgr.save(state_path_val, {"last_row": actual_end_row})
            logger.info("run: state updated — last_row=%d", actual_end_row)
        except OSError as exc:
            logger.error("run: failed to save state: %s", exc)
            # Non-fatal: pipeline result is still success

        # Step 6 — success
        timestamp_iso: str = datetime.now(tz=timezone.utc).isoformat()
        subject: str = email_content.get("subject", "")
        logger.info("run: pipeline completed successfully (subject_len=%d)", len(subject))

        success_result: Dict[str, Any] = {
            "status": "success",
            "timestamp": timestamp_iso,
            "subject": subject,
            "forced": force,
        }
        if not mark_ok:
            success_result["mark_failed"] = True
        return success_result
