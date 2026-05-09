"""
main.py — Entry point for the Excel Report Automation desktop application.

Usage:
    python main.py

PyInstaller build:
    pyinstaller --noconfirm --onefile --windowed --name "ExcelReportApp" main.py
"""

import logging
import sys
from pathlib import Path


def _setup_logging() -> None:
    """Configure root logger: writes to app.log alongside the executable."""
    if getattr(sys, "frozen", False):
        # Running as PyInstaller EXE — log next to the EXE
        log_dir = Path(sys.executable).parent
    else:
        # Running as plain script — log in the working directory
        log_dir = Path(__file__).parent

    log_path = log_dir / "app.log"

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.getLogger(__name__).info("Logging initialised — log file: %s", log_path)


from ui.app import App


def main() -> None:
    """Create and run the main application window."""
    _setup_logging()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
