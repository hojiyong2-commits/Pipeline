"""Configuration loader for AFM-Kitting automation.

Loads config.json from the same directory as main.py with encoding fallback.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _read_text_with_fallback(path: Path) -> str:
    """Read a text file with utf-8 → cp949 → latin-1 encoding fallback.

    Args:
        path: Path to the file to read.

    Returns:
        File contents as a string.

    Raises:
        UnicodeDecodeError: If no encoding succeeds.
    """
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            text = path.read_text(encoding=enc)
            logger.info("Read '%s' with encoding '%s'", path, enc)
            return text
        except (UnicodeDecodeError, LookupError):
            continue
    raise UnicodeDecodeError(
        "utf-8", b"", 0, 1,
        f"Cannot decode '{path}' with any supported encoding (utf-8, cp949, latin-1)"
    )


def load_config(config_path: Optional[Path] = None) -> dict:
    """Load and return configuration from config.json.

    Searches for config.json in the parent directory of this module's parent
    (i.e., the same directory as main.py) unless config_path is explicitly given.

    Args:
        config_path: Optional explicit path to config.json. If None, defaults to
                     the directory containing main.py (parent of core/).

    Returns:
        Parsed configuration as a dict.

    Raises:
        TypeError: If config_path is not None and not a str/Path.
        FileNotFoundError: If the config file does not exist.
        ValueError: If the file exists but contains invalid JSON.
    """
    # None is allowed — we default to standard location
    if config_path is not None and not isinstance(config_path, (str, Path)):
        raise TypeError(
            f"config_path must be str, Path, or None, got {type(config_path).__name__}"
        )

    if config_path is None:
        # When frozen (PyInstaller EXE), sys.executable is the EXE path.
        # When running as source, __file__ is core/config_loader.py → go up 2 levels.
        import sys as _sys
        if getattr(_sys, "frozen", False):
            resolved_path: Path = Path(_sys.executable).parent / "config.json"
        else:
            resolved_path = Path(__file__).parent.parent / "config.json"
    else:
        resolved_path = Path(config_path)

    if not resolved_path.exists():
        logger.error("config.json not found at '%s'", resolved_path)
        raise FileNotFoundError(f"Configuration file not found: '{resolved_path}'")

    logger.info("Loading config from '%s'", resolved_path)

    try:
        raw_text = _read_text_with_fallback(resolved_path)
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Cannot decode config file '{resolved_path}': {exc}"
        ) from exc

    try:
        config = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in config file '%s': %s", resolved_path, exc)
        raise ValueError(
            f"Invalid JSON in configuration file '{resolved_path}': {exc}"
        ) from exc

    if not isinstance(config, dict):
        raise ValueError(
            f"Configuration root must be a JSON object, got {type(config).__name__}"
        )

    logger.info("Config loaded successfully (%d keys)", len(config))
    return config


if __name__ == "__main__":
    import tempfile
    import os

    # Test: invalid path → FileNotFoundError
    try:
        load_config(Path("/nonexistent/config.json"))
        assert False, "Expected FileNotFoundError not raised"
    except FileNotFoundError:
        pass

    # Test: bad type → TypeError
    try:
        load_config(12345)  # type: ignore[arg-type]
        assert False, "Expected TypeError not raised"
    except TypeError:
        pass

    # Test: valid JSON file
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write('{"key": "value", "num": 42}')
        tmp_path = tmp.name

    try:
        result = load_config(Path(tmp_path))
        assert result["key"] == "value", "key mismatch"
        assert result["num"] == 42, "num mismatch"
    finally:
        os.unlink(tmp_path)

    # Test: invalid JSON → ValueError
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write("NOT VALID JSON {{{")
        tmp_bad = tmp.name

    try:
        try:
            load_config(Path(tmp_bad))
            assert False, "Expected ValueError not raised"
        except ValueError:
            pass
    finally:
        os.unlink(tmp_bad)

    print("[SELF-VERIFY] config_loader.py OK")
