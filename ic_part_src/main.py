"""
ic_part_src/main.py — PyInstaller entry point for ICPartAutomation EXE.

Resolves sys.path so that the project-root `core/` package and the sibling
modules `automation` and `order_mapper` are importable when running as a
frozen EXE or during development.

Logging is injected here as required by the Build Agent gate rule:
  app.log is written next to the EXE (frozen) or next to this file (dev).
"""

import logging
import os
import sys
from pathlib import Path


def _setup_logging() -> None:
    """Configure root logger to write app.log alongside the executable."""
    if getattr(sys, "frozen", False):
        # Running as PyInstaller EXE
        log_dir = Path(sys.executable).parent
    else:
        # Running as plain script — log next to this file
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
        force=True,
    )
    logging.getLogger(__name__).info(
        "ICPartAutomation logging initialised — log file: %s", log_path
    )


def _patch_sys_path() -> None:
    """Ensure project root and ic_part_src are on sys.path.

    Needed when this script is frozen by PyInstaller: the project root
    must be on sys.path so that `core.*` packages resolve correctly,
    and ic_part_src must be present so that `automation` and
    `order_mapper` are importable.
    """
    here = Path(__file__).resolve().parent          # …/ic_part_src/
    project_root = here.parent                       # …/Really good agents…/

    for p in (str(project_root), str(here)):
        if p not in sys.path:
            sys.path.insert(0, p)


def main() -> None:
    """Entry point: patch path, setup logging, then launch the GUI."""
    _patch_sys_path()
    _setup_logging()

    # Import after path patch so that core.* is resolvable
    from app import App  # ic_part_src/app.py

    application = App()
    application.mainloop()


if __name__ == "__main__":
    main()
