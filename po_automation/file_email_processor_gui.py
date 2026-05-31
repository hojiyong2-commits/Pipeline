"""Tkinter GUI for the file-based EmailMonitor processor."""

from __future__ import annotations

import json
import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from po_automation.file_email_processor import process_folder
except ModuleNotFoundError:
    from file_email_processor import process_folder  # type: ignore[no-redef]

SETTINGS_FILE = "emailmonitor_file_settings.json"


class EmailMonitorFileApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("EmailMonitor File Processor")
        self.root.geometry("760x520")
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._load_settings()
        self.root.after(150, self._drain_log)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}

        frame = ttk.Frame(self.root)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Inbox 폴더 (.msg/.eml)").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="선택", command=self._choose_input).grid(row=0, column=2, **pad)

        ttk.Label(frame, text="Output 폴더").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="선택", command=self._choose_output).grid(row=1, column=2, **pad)

        ttk.Checkbutton(frame, text="하위 폴더까지 처리", variable=self.recursive_var).grid(
            row=2, column=1, sticky="w", **pad
        )

        self.run_button = ttk.Button(frame, text="처리 시작", command=self._start)
        self.run_button.grid(row=3, column=1, sticky="ew", **pad)

        self.log_text = tk.Text(frame, height=20, wrap="word")
        self.log_text.grid(row=4, column=0, columnspan=3, sticky="nsew", padx=10, pady=10)

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(4, weight=1)

    def _settings_path(self) -> Path:
        base = Path(self.output_var.get()).parent if self.output_var.get().strip() else Path.cwd()
        return base / SETTINGS_FILE

    def _load_settings(self) -> None:
        for base in [Path.cwd(), Path(r"G:\내 드라이브\터미널\실행결과")]:
            path = base / SETTINGS_FILE
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            self.input_var.set(data.get("input_dir", ""))
            self.output_var.set(data.get("output_dir", ""))
            self.recursive_var.set(bool(data.get("recursive", False)))
            break

    def _save_settings(self) -> None:
        path = self._settings_path()
        path.write_text(
            json.dumps(
                {
                    "input_dir": self.input_var.get().strip(),
                    "output_dir": self.output_var.get().strip(),
                    "recursive": self.recursive_var.get(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _choose_input(self) -> None:
        folder = filedialog.askdirectory(title="Inbox 폴더 선택")
        if folder:
            self.input_var.set(folder)

    def _choose_output(self) -> None:
        folder = filedialog.askdirectory(title="Output 폴더 선택")
        if folder:
            self.output_var.set(folder)

    def _log(self, message: str) -> None:
        self.log_queue.put(message)

    def _drain_log(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
        self.root.after(150, self._drain_log)

    def _start(self) -> None:
        input_dir = Path(self.input_var.get().strip())
        output_dir = Path(self.output_var.get().strip())
        if not input_dir.exists():
            messagebox.showerror("오류", f"Inbox 폴더가 없습니다:\n{input_dir}")
            return
        if not output_dir:
            messagebox.showerror("오류", "Output 폴더를 선택하세요.")
            return
        self._save_settings()
        self.run_button.config(state=tk.DISABLED)
        self.log_text.delete("1.0", tk.END)
        self._log("처리를 시작합니다.")

        def run() -> None:
            try:
                logging.basicConfig(
                    filename=str(output_dir.parent / "emailmonitor_file.log"),
                    level=logging.INFO,
                    encoding="utf-8",
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                )
                logging.getLogger("extract_msg").setLevel(logging.WARNING)
                results = process_folder(
                    input_dir,
                    output_dir,
                    recursive=self.recursive_var.get(),
                    log=self._log,
                )
                counts = {"DONE": 0, "SKIP": 0, "ERROR": 0}
                for result in results:
                    counts[result.status] = counts.get(result.status, 0) + 1
                self._log(f"완료: DONE={counts['DONE']} SKIP={counts['SKIP']} ERROR={counts['ERROR']}")
            except Exception as exc:
                self._log(f"오류: {exc}")
                messagebox.showerror("오류", str(exc))
            finally:
                self.root.after(0, lambda: self.run_button.config(state=tk.NORMAL))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    EmailMonitorFileApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
