"""
config_manager.py — AppConfig TypedDict definitions and ConfigManager class.

Handles persistent JSON configuration for the daily Excel report automation app.
PyInstaller frozen-environment aware (sys.frozen / sys._MEIPASS).
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.encoding_utils import safe_read_json

from typing import TypedDict

logger = logging.getLogger(__name__)


class CellRange(TypedDict):
    """Defines the rectangular cell range to read from an Excel sheet."""

    sheet_name: str
    start_row: int
    start_col: int
    end_row: int
    end_col: int


class AppConfig(TypedDict):
    """Full application configuration persisted to config.json."""

    excel_path: str
    cell_range: CellRange
    mark_color: str
    email_subject_template: str
    email_body_template: str
    to_recipients: List[str]
    cc_recipients: List[str]
    schedule_time: str
    check_modified: bool
    highlight_ranges: List[Dict[str, str]]
    user_start_row: int      # GUI에서 첫 실행 시작 행 지정
    col_start: int           # 복사할 열 시작 (1-indexed)
    col_end: int             # 복사할 열 끝 (1-indexed)


class ExcelReadResult(TypedDict):
    """Result container returned by ExcelHandler.read_cells()."""

    success: bool
    data: List[List[Any]]
    data_as_text: str
    error_msg: Optional[str]


class SendResult(TypedDict):
    """Result container returned by OutlookSender.send()."""

    success: bool
    error_msg: Optional[str]


# SEC-005: Maximum length for string config field values
_STR_FIELD_MAX_LEN = 2048

# SEC-008: Maximum list length and maximum string length per list item
_LIST_FIELD_MAX_LEN = 50
_LIST_ITEM_MAX_STR_LEN = 254

# SEC-006: CellRange integer field validation constants
_CELL_RANGE_INT_FIELDS = {"start_row", "start_col", "end_row", "end_col"}
_CELL_INDEX_MIN = 1
_CELL_INDEX_MAX = 10000


def _validate_cell_range(raw_range: Dict[str, Any], default_range: "CellRange") -> "CellRange":
    """Validate and sanitise a raw dict into a CellRange, clamping out-of-bound integers.

    SEC-006 compliance:
    - Integer fields (start_row, start_col, end_row, end_col) must be genuine int
      (bool excluded).  Non-int values fall back to the default; out-of-range values
      are clamped to [_CELL_INDEX_MIN, _CELL_INDEX_MAX] with a warning.
    - sheet_name must be a str and is truncated to 31 characters if longer.
    - Unknown keys are silently ignored.

    Args:
        raw_range:     The raw dict read from config.json under the "cell_range" key.
        default_range: The CellRange produced by _default_config() to use as fallback.

    Returns:
        A new CellRange with validated/sanitised field values.
    """
    result: CellRange = CellRange(
        sheet_name=default_range["sheet_name"],
        start_row=default_range["start_row"],
        start_col=default_range["start_col"],
        end_row=default_range["end_row"],
        end_col=default_range["end_col"],
    )

    for rkey, rval in raw_range.items():
        if rkey not in result:
            # Unknown key — ignore
            continue

        if rkey in _CELL_RANGE_INT_FIELDS:
            if not isinstance(rval, int) or isinstance(rval, bool):
                logger.warning(
                    "_validate_cell_range: field '%s' is not an integer (got %s) — "
                    "keeping default %s",
                    rkey,
                    type(rval).__name__,
                    result[rkey],  # type: ignore[literal-required]
                )
                continue
            if rval < _CELL_INDEX_MIN or rval > _CELL_INDEX_MAX:
                clamped = max(_CELL_INDEX_MIN, min(_CELL_INDEX_MAX, rval))
                logger.warning(
                    "_validate_cell_range: field '%s' value %d out of range "
                    "[%d, %d] — clamped to %d",
                    rkey,
                    rval,
                    _CELL_INDEX_MIN,
                    _CELL_INDEX_MAX,
                    clamped,
                )
                result[rkey] = clamped  # type: ignore[literal-required]
            else:
                result[rkey] = rval  # type: ignore[literal-required]

        elif rkey == "sheet_name":
            if not isinstance(rval, str):
                logger.warning(
                    "_validate_cell_range: 'sheet_name' is not a string (got %s) — "
                    "keeping default '%s'",
                    type(rval).__name__,
                    result["sheet_name"],
                )
                continue
            if len(rval) > 31:
                logger.warning(
                    "_validate_cell_range: 'sheet_name' truncated from %d to 31 chars",
                    len(rval),
                )
                rval = rval[:31]
            result["sheet_name"] = rval

    return result


def _default_config() -> AppConfig:
    """Return a fresh AppConfig with default values."""
    return AppConfig(
        excel_path="",
        cell_range=CellRange(
            sheet_name="Sheet1",
            start_row=1,
            start_col=1,
            end_row=10,
            end_col=5,
        ),
        mark_color="FFD700",
        email_subject_template="[일일보고] {date}",
        email_body_template="일자: {date}\n\n내용:\n{data}",
        to_recipients=[],
        cc_recipients=[],
        schedule_time="10:00",
        check_modified=True,
        highlight_ranges=[],
        user_start_row=1,
        col_start=1,
        col_end=10,
    )


class ConfigManager:
    """Manages loading and saving the application configuration from config.json.

    The config file is stored next to the EXE in a frozen PyInstaller build,
    or next to this source file during development.
    """

    def get_config_path(self) -> Path:
        """Return the absolute path to config.json.

        In a frozen (PyInstaller) environment the file lives beside the EXE.
        In development it lives beside this module file.
        """
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
        else:
            base = Path(__file__).resolve().parent
        return base / "config.json"

    def load(self) -> AppConfig:
        """Load AppConfig from config.json.

        EC-05: If the file does not exist or contains invalid JSON, a default
        config is written to disk and returned — JSONDecodeError is never
        propagated to the caller.
        """
        config_path = self.get_config_path()
        if not config_path.is_file():
            logger.info("config.json not found — creating default at %s", config_path)
            default = _default_config()
            self.save(default)
            return default

        try:
            data: Dict[str, Any] = safe_read_json(config_path)
        except ValueError as exc:
            logger.error("config.json is malformed or undecodable (%s) — returning default", exc)
            return _default_config()
        except OSError as exc:
            logger.error("Cannot read config.json: %s — returning default", exc)
            return _default_config()

        if not isinstance(data, dict):
            logger.error(
                "config.json top-level value is not a dict (got %s) — returning default",
                type(data).__name__,
            )
            return _default_config()

        # Merge loaded values over defaults so any missing keys are filled in.
        default = _default_config()
        for key, value in data.items():
            if key in default:
                # SEC-005: truncate oversized string values before merging
                if isinstance(value, str) and len(value) > _STR_FIELD_MAX_LEN:
                    logger.warning(
                        "load: field '%s' value truncated from %d to %d chars",
                        key,
                        len(value),
                        _STR_FIELD_MAX_LEN,
                    )
                    value = value[:_STR_FIELD_MAX_LEN]
                # SEC-008: sanitise list fields — keep only str items, truncate long
                # items, and cap list length at _LIST_FIELD_MAX_LEN.
                # highlight_ranges is List[Dict[str, str]] and handled separately below.
                elif isinstance(value, list) and key != "highlight_ranges":
                    sanitised: List[str] = []
                    for item in value:
                        if not isinstance(item, str):
                            logger.warning(
                                "load: field '%s' — non-string item removed (type=%s)",
                                key,
                                type(item).__name__,
                            )
                            continue
                        if len(item) > _LIST_ITEM_MAX_STR_LEN:
                            logger.warning(
                                "load: field '%s' — item truncated from %d to %d chars",
                                key,
                                len(item),
                                _LIST_ITEM_MAX_STR_LEN,
                            )
                            item = item[:_LIST_ITEM_MAX_STR_LEN]
                        sanitised.append(item)
                    if len(sanitised) > _LIST_FIELD_MAX_LEN:
                        logger.warning(
                            "load: field '%s' — list capped from %d to %d items",
                            key,
                            len(sanitised),
                            _LIST_FIELD_MAX_LEN,
                        )
                        sanitised = sanitised[:_LIST_FIELD_MAX_LEN]
                    value = sanitised
                default[key] = value  # type: ignore[literal-required]

        # Ensure nested CellRange keys are also filled (SEC-006: validated/clamped).
        raw_range: Dict[str, Any] = data.get("cell_range", {})
        if isinstance(raw_range, dict):
            default["cell_range"] = _validate_cell_range(raw_range, default["cell_range"])

        # Backward-compat: highlight_ranges may be absent in older config files.
        # Validate that each entry is a dict with str values only; discard invalid items.
        raw_highlights: Any = data.get("highlight_ranges")
        if raw_highlights is None:
            # Key absent → default already set to []
            logger.debug("load: 'highlight_ranges' key absent — defaulting to []")
        elif not isinstance(raw_highlights, list):
            logger.warning(
                "load: 'highlight_ranges' is not a list (got %s) — defaulting to []",
                type(raw_highlights).__name__,
            )
            default["highlight_ranges"] = []
        else:
            validated_ranges: List[Dict[str, str]] = []
            for item in raw_highlights:
                if not isinstance(item, dict):
                    logger.warning("load: highlight_ranges item is not a dict — skipped")
                    continue
                clean_item: Dict[str, str] = {}
                for k, v in item.items():
                    if isinstance(k, str) and isinstance(v, str):
                        clean_item[k] = v
                    else:
                        logger.warning(
                            "load: highlight_ranges item key/value not str — skipped"
                        )
                validated_ranges.append(clean_item)
            default["highlight_ranges"] = validated_ranges

        logger.debug("config.json loaded successfully from %s", config_path)
        return default

    def save(self, config: AppConfig) -> bool:
        """Persist an AppConfig to config.json.

        Returns True on success, False on any OS-level failure.
        """
        config_path = self.get_config_path()
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = config_path.with_suffix(".json.tmp")
            temp_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(config_path)
            logger.debug("config.json saved to %s", config_path)
            return True
        except OSError as exc:
            logger.error("Failed to save config.json: %s", exc)
            return False
