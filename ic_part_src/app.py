"""
ic_part_src/app.py — Tkinter GUI for IC-Part automation.

Pipeline: FEAT-20260425-6D65

Design constraints:
- Python 3.9 compatible type hints (typing module).
- All widget instance variables follow naming conventions:
    self.btn_[action], self.entry_[field], self.lbl_[description],
    self.txt_[field]
- Long-running operations (watcher.start) run in daemon threads via
  threading.Thread; UI callbacks arrive via self.after() to satisfy the
  Async-Bridge rule.
- Input is validated before FolderWatcher is created.
- config.json is written atomically (tempfile → os.replace).
- config.json is read with utf-8 → cp949 → latin-1 fallback.
- mainloop() is called by main.py, NOT here.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from typing import Callable, Dict, Optional

from automation import FolderWatcher

# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# config.json path helper (frozen EXE or dev)
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    _CONFIG_DIR: Path = Path(sys.executable).parent
else:
    _CONFIG_DIR: Path = Path(__file__).parent

_CONFIG_PATH: Path = _CONFIG_DIR / "config.json"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class App(tk.Tk):
    """Main application window for IC-Part folder automation.

    Responsibilities:
      - Provide path inputs for IC-Part template, watch directory, and three
        CORB base directories (SoCal, SG, AT).
      - Validate inputs and start/stop a FolderWatcher instance.
      - Persist and restore settings via config.json (atomic write, multi-
        encoding read).
      - Route log_callback messages from the watcher thread to txt_log via
        self.after() (UI/asyncio bridge pattern).

    mainloop() is called by main.py — never called inside this class.
    """

    def __init__(self) -> None:
        """Initialise window, build widgets, load config."""
        super().__init__()
        self.title("IC-Part 자동화")
        self.resizable(True, True)
        self.minsize(700, 560)

        # Watcher state
        self._watcher: Optional[FolderWatcher] = None
        self._watcher_lock: threading.Lock = threading.Lock()

        self._build_ui()
        self._load_config()

        # Graceful shutdown
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        logger.info("[UI] App window initialised")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Build all widgets and lay them out in the main window."""
        pad: Dict[str, int] = {"padx": 8, "pady": 4}

        # ---- top frame: path inputs ----------------------------------------
        frm_inputs = tk.Frame(self)
        frm_inputs.pack(fill=tk.X, padx=8, pady=(8, 0))
        frm_inputs.columnconfigure(1, weight=1)

        row = 0

        # IC-Part file path
        self.lbl_ic_part_path = tk.Label(frm_inputs, text="IC-Part 파일 (.xlsx)")
        self.lbl_ic_part_path.grid(row=row, column=0, sticky=tk.W, **pad)
        self.entry_ic_part_path = tk.Entry(frm_inputs, width=52)
        self.entry_ic_part_path.grid(row=row, column=1, sticky=tk.EW, **pad)
        self.btn_browse_ic_part = tk.Button(
            frm_inputs, text="찾아보기", command=self._on_browse_ic_part
        )
        self.btn_browse_ic_part.grid(row=row, column=2, **pad)
        row += 1

        # Watch directory
        self.lbl_watch_dir = tk.Label(frm_inputs, text="감지 폴더 경로")
        self.lbl_watch_dir.grid(row=row, column=0, sticky=tk.W, **pad)
        self.entry_watch_dir = tk.Entry(frm_inputs, width=52)
        self.entry_watch_dir.grid(row=row, column=1, sticky=tk.EW, **pad)
        self.btn_browse_watch_dir = tk.Button(
            frm_inputs, text="찾아보기", command=self._on_browse_watch_dir
        )
        self.btn_browse_watch_dir.grid(row=row, column=2, **pad)
        row += 1

        # Separator label
        lbl_corb_section = tk.Label(
            frm_inputs, text="CORB 기본 경로", font=("", 9, "bold")
        )
        lbl_corb_section.grid(
            row=row, column=0, columnspan=3, sticky=tk.W, padx=8, pady=(10, 2)
        )
        row += 1

        # CORB SoCal
        self.lbl_corb_socal = tk.Label(frm_inputs, text="SoCal")
        self.lbl_corb_socal.grid(row=row, column=0, sticky=tk.W, **pad)
        self.entry_corb_socal = tk.Entry(frm_inputs, width=52)
        self.entry_corb_socal.grid(row=row, column=1, sticky=tk.EW, **pad)
        self.btn_browse_corb_socal = tk.Button(
            frm_inputs, text="찾아보기", command=self._on_browse_corb_socal
        )
        self.btn_browse_corb_socal.grid(row=row, column=2, **pad)
        row += 1

        # CORB SG
        self.lbl_corb_sg = tk.Label(frm_inputs, text="SG")
        self.lbl_corb_sg.grid(row=row, column=0, sticky=tk.W, **pad)
        self.entry_corb_sg = tk.Entry(frm_inputs, width=52)
        self.entry_corb_sg.grid(row=row, column=1, sticky=tk.EW, **pad)
        self.btn_browse_corb_sg = tk.Button(
            frm_inputs, text="찾아보기", command=self._on_browse_corb_sg
        )
        self.btn_browse_corb_sg.grid(row=row, column=2, **pad)
        row += 1

        # CORB AT
        self.lbl_corb_at = tk.Label(frm_inputs, text="AT")
        self.lbl_corb_at.grid(row=row, column=0, sticky=tk.W, **pad)
        self.entry_corb_at = tk.Entry(frm_inputs, width=52)
        self.entry_corb_at.grid(row=row, column=1, sticky=tk.EW, **pad)
        self.btn_browse_corb_at = tk.Button(
            frm_inputs, text="찾아보기", command=self._on_browse_corb_at
        )
        self.btn_browse_corb_at.grid(row=row, column=2, **pad)
        row += 1

        # ---- control buttons -----------------------------------------------
        frm_ctrl = tk.Frame(self)
        frm_ctrl.pack(fill=tk.X, padx=8, pady=(10, 0))

        self.btn_start = tk.Button(
            frm_ctrl,
            text="감시 시작",
            width=14,
            command=self._on_start,
            bg="#4CAF50",
            fg="white",
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_stop = tk.Button(
            frm_ctrl,
            text="감시 중지",
            width=14,
            command=self._on_stop,
            state=tk.DISABLED,
            bg="#F44336",
            fg="white",
        )
        self.btn_stop.pack(side=tk.LEFT)

        # ---- status label --------------------------------------------------
        self.lbl_status = tk.Label(self, text="대기 중", anchor=tk.W, foreground="gray")
        self.lbl_status.pack(fill=tk.X, padx=8, pady=(6, 0))

        # ---- log area ------------------------------------------------------
        lbl_log_header = tk.Label(self, text="실행 로그", anchor=tk.W)
        lbl_log_header.pack(anchor=tk.W, padx=8, pady=(6, 0))

        frm_log = tk.Frame(self)
        frm_log.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.txt_log = tk.Text(
            frm_log,
            state=tk.DISABLED,
            wrap=tk.WORD,
            font=("Consolas", 9),
            background="#1e1e1e",
            foreground="#d4d4d4",
        )
        log_scroll = tk.Scrollbar(
            frm_log, orient=tk.VERTICAL, command=self.txt_log.yview
        )
        self.txt_log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # Browse handlers
    # ------------------------------------------------------------------

    def _on_browse_ic_part(self) -> None:
        """Open file dialog for IC-Part .xlsx and populate entry_ic_part_path."""
        path: str = filedialog.askopenfilename(
            title="IC-Part 파일 선택",
            filetypes=[("Excel 파일", "*.xlsx"), ("모든 파일", "*.*")],
        )
        if path:
            self.entry_ic_part_path.delete(0, tk.END)
            self.entry_ic_part_path.insert(0, path)
            logger.info("[UI] IC-Part 파일 선택: %s", path)

    def _on_browse_watch_dir(self) -> None:
        """Open directory dialog and populate entry_watch_dir."""
        path: str = filedialog.askdirectory(title="감지 폴더 선택")
        if path:
            self.entry_watch_dir.delete(0, tk.END)
            self.entry_watch_dir.insert(0, path)
            logger.info("[UI] 감지 폴더 선택: %s", path)

    def _on_browse_corb_socal(self) -> None:
        """Open directory dialog and populate entry_corb_socal."""
        path: str = filedialog.askdirectory(title="CORB SoCal 폴더 선택")
        if path:
            self.entry_corb_socal.delete(0, tk.END)
            self.entry_corb_socal.insert(0, path)
            logger.info("[UI] CORB SoCal 선택: %s", path)

    def _on_browse_corb_sg(self) -> None:
        """Open directory dialog and populate entry_corb_sg."""
        path: str = filedialog.askdirectory(title="CORB SG 폴더 선택")
        if path:
            self.entry_corb_sg.delete(0, tk.END)
            self.entry_corb_sg.insert(0, path)
            logger.info("[UI] CORB SG 선택: %s", path)

    def _on_browse_corb_at(self) -> None:
        """Open directory dialog and populate entry_corb_at."""
        path: str = filedialog.askdirectory(title="CORB AT 폴더 선택")
        if path:
            self.entry_corb_at.delete(0, tk.END)
            self.entry_corb_at.insert(0, path)
            logger.info("[UI] CORB AT 선택: %s", path)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_inputs(self) -> bool:
        """Validate all five path inputs.

        Shows messagebox.showerror on the first failing field.

        Returns:
            True if all inputs pass validation, False otherwise.
        """
        ic_part_path: str = self.entry_ic_part_path.get().strip()
        if not ic_part_path:
            messagebox.showerror("입력 오류", "IC-Part 파일 경로를 입력해 주세요.")
            return False
        if not ic_part_path.lower().endswith(".xlsx"):
            messagebox.showerror(
                "입력 오류",
                "IC-Part 파일은 .xlsx 확장자여야 합니다.\n"
                f"현재 입력값: {ic_part_path}",
            )
            return False
        if not Path(ic_part_path).is_file():
            messagebox.showerror(
                "입력 오류",
                f"IC-Part 파일을 찾을 수 없습니다:\n{ic_part_path}",
            )
            return False

        watch_dir: str = self.entry_watch_dir.get().strip()
        if not watch_dir:
            messagebox.showerror("입력 오류", "감지 폴더 경로를 입력해 주세요.")
            return False
        if not Path(watch_dir).is_dir():
            messagebox.showerror(
                "입력 오류",
                f"감지 폴더를 찾을 수 없습니다:\n{watch_dir}",
            )
            return False

        corb_socal: str = self.entry_corb_socal.get().strip()
        if not corb_socal:
            messagebox.showerror("입력 오류", "CORB SoCal 경로를 입력해 주세요.")
            return False

        corb_sg: str = self.entry_corb_sg.get().strip()
        if not corb_sg:
            messagebox.showerror("입력 오류", "CORB SG 경로를 입력해 주세요.")
            return False

        corb_at: str = self.entry_corb_at.get().strip()
        if not corb_at:
            messagebox.showerror("입력 오류", "CORB AT 경로를 입력해 주세요.")
            return False

        return True

    # ------------------------------------------------------------------
    # Start / Stop handlers
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        """Validate inputs, create FolderWatcher, start it, save config."""
        logger.info("[UI] 감시 시작 버튼 클릭")

        if not self._validate_inputs():
            return

        ic_part_path: str = self.entry_ic_part_path.get().strip()
        watch_dir: str = self.entry_watch_dir.get().strip()
        corb_socal: str = self.entry_corb_socal.get().strip()
        corb_sg: str = self.entry_corb_sg.get().strip()
        corb_at: str = self.entry_corb_at.get().strip()

        corb_base_map: Dict[str, str] = {
            "SoCal": corb_socal,
            "SG": corb_sg,
            "AT": corb_at,
        }

        try:
            watcher = FolderWatcher(
                watch_dir=watch_dir,
                ic_part_template=ic_part_path,
                corb_base_map=corb_base_map,
                log_callback=self._on_log,
            )
        except (TypeError, ValueError) as exc:
            messagebox.showerror("초기화 오류", str(exc))
            logger.error("[UI] FolderWatcher 초기화 실패: %s", exc)
            return

        # Start the observer in a daemon thread so it does not block UI
        def _start_watcher() -> None:
            try:
                watcher.start()
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("시작 오류", str(exc)))
                logger.exception("[UI] watcher.start() 예외: %s", exc)

        with self._watcher_lock:
            self._watcher = watcher

        t = threading.Thread(target=_start_watcher, daemon=True)
        t.start()

        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.lbl_status.config(text=f"감시 중: {watch_dir}", foreground="green")
        self._append_log(f"[감시 시작] {watch_dir}")

        self._save_config(ic_part_path, watch_dir, corb_socal, corb_sg, corb_at)

    def _on_stop(self) -> None:
        """Stop the FolderWatcher and reset button states."""
        logger.info("[UI] 감시 중지 버튼 클릭")

        with self._watcher_lock:
            watcher = self._watcher
            self._watcher = None

        if watcher is not None:
            def _stop_watcher() -> None:
                try:
                    watcher.stop()
                except Exception as exc:
                    logger.warning("[UI] watcher.stop() 예외: %s", exc)

            t = threading.Thread(target=_stop_watcher, daemon=True)
            t.start()

        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self.lbl_status.config(text="감시 중지됨", foreground="gray")
        self._append_log("[감시 중지]")

    # ------------------------------------------------------------------
    # Log callback (thread-safe via self.after)
    # ------------------------------------------------------------------

    def _on_log(self, msg: str) -> None:
        """Receive a log message from the watcher thread and schedule UI update.

        This method may be called from any thread. It posts work to the main
        Tkinter thread using self.after(0, ...) — the UI/asyncio bridge.

        Args:
            msg: The log message string from automation.py.
        """
        self.after(0, lambda: self._append_log(msg))

    def _append_log(self, message: str) -> None:
        """Append a timestamped line to txt_log on the main thread.

        Args:
            message: Text to append.
        """
        ts: str = datetime.datetime.now().strftime("%H:%M:%S")
        line: str = f"[{ts}] {message}\n"
        logger.info("[LOG] %s", message)
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.insert(tk.END, line)
        self.txt_log.see(tk.END)
        self.txt_log.config(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Config persistence (FS category — atomic write, multi-encoding read)
    # ------------------------------------------------------------------

    def _save_config(
        self,
        ic_part_path: str,
        watch_dir: str,
        corb_socal: str,
        corb_sg: str,
        corb_at: str,
    ) -> None:
        """Persist current settings to config.json using atomic write.

        Uses tempfile → os.replace() to prevent partial writes corrupting the
        saved config file.

        Args:
            ic_part_path: Absolute path to the IC-Part .xlsx file.
            watch_dir: Absolute path to the folder to watch.
            corb_socal: CORB base path for SoCal.
            corb_sg: CORB base path for SG.
            corb_at: CORB base path for AT.
        """
        data: Dict[str, str] = {
            "ic_part_path": ic_part_path,
            "watch_dir": watch_dir,
            "corb_socal": corb_socal,
            "corb_sg": corb_sg,
            "corb_at": corb_at,
        }
        tmp_path: str = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=str(_CONFIG_DIR),
                delete=False,
                suffix=".tmp",
                encoding="utf-8",
            ) as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                tmp_path = f.name
            os.replace(tmp_path, str(_CONFIG_PATH))
            logger.info("[UI] config.json 저장 완료: %s", _CONFIG_PATH)
        except Exception as exc:
            logger.error("[UI] config.json 저장 실패: %s", exc)
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def _load_config(self) -> None:
        """Read config.json with utf-8 → cp949 → latin-1 fallback and populate entries."""
        if not _CONFIG_PATH.exists():
            logger.info("[UI] config.json 없음 — 기본값 사용")
            return

        data: Optional[Dict[str, str]] = None
        for enc in ("utf-8", "cp949", "latin-1"):
            try:
                with open(str(_CONFIG_PATH), encoding=enc) as f:
                    data = json.load(f)
                logger.info("[UI] config.json 로드 완료 (encoding=%s)", enc)
                break
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

        if data is None:
            logger.warning("[UI] config.json 파싱 실패 — 기본값 사용")
            return

        def _set_entry(entry: tk.Entry, key: str) -> None:
            val: str = data.get(key, "")  # type: ignore[union-attr]
            if val:
                entry.delete(0, tk.END)
                entry.insert(0, val)

        _set_entry(self.entry_ic_part_path, "ic_part_path")
        _set_entry(self.entry_watch_dir, "watch_dir")
        _set_entry(self.entry_corb_socal, "corb_socal")
        _set_entry(self.entry_corb_sg, "corb_sg")
        _set_entry(self.entry_corb_at, "corb_at")

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        """Stop the watcher (if running) before destroying the window."""
        logger.info("[UI] 창 닫기 요청")
        with self._watcher_lock:
            watcher = self._watcher
            self._watcher = None

        if watcher is not None:
            try:
                watcher.stop()
            except Exception as exc:
                logger.warning("[UI] 종료 시 watcher.stop() 예외: %s", exc)

        self.destroy()
