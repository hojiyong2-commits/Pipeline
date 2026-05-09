"""
ui/app.py — Tkinter GUI application class for the Excel-to-Outlook automation tool.

Design constraints:
- Python 3.9 compatible type hints (typing module).
- All widget instance variables follow naming conventions:
    self.btn_[action], self.entry_[field], self.lbl_[description],
    self.txt_[field], self.chk_[field], self.spn_[field]
- Long-running tasks (run, scheduler loop) execute in daemon threads.
- Progress bar runs in indeterminate mode while a task is active.
- Input is validated before save: HH:MM format, basic email format.
"""

import logging
import re
import threading
import tkinter as tk
import uuid
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from core.automation_runner import AutomationRunner
from core.config_manager import AppConfig, ConfigManager
from core.scheduler import Scheduler


# ---------------------------------------------------------------------------
# Module logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex constants for validation
# ---------------------------------------------------------------------------
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HEX_COLOR_RE = re.compile(r"^[0-9A-Fa-f]{6}$")

# ---------------------------------------------------------------------------
# Excel 56-color standard palette (index 0-55, RRGGBB strings)
# ---------------------------------------------------------------------------
EXCEL_56_COLORS: List[str] = [
    # Row 1
    "000000", "FFFFFF", "FF0000", "00FF00", "0000FF", "FFFF00", "FF00FF", "00FFFF",
    # Row 2
    "800000", "008000", "000080", "808000", "800080", "008080", "C0C0C0", "808080",
    # Row 3
    "9999FF", "993366", "FFFFCC", "CCFFFF", "660066", "FF8080", "0066CC", "CCCCFF",
    # Row 4
    "000080", "FF00FF", "FFFF00", "00FFFF", "800080", "800000", "FF6600", "FFCC99",
    # Row 5
    "FFFF99", "CCFFCC", "CCFFFF", "99CCFF", "FF99CC", "CC99FF", "FFCC99", "3366FF",
    # Row 6
    "33CCCC", "99CC00", "FFCC00", "FF9900", "FF6600", "666699", "969696", "003366",
    # Row 7
    "339966", "003300", "333300", "993300", "993366", "333399", "333333", "FFFFFF",
]

# ---------------------------------------------------------------------------
# UI input length limits (SEC-002)
# ---------------------------------------------------------------------------
_UI_MAX_PATH_LEN: int = 2048
_UI_MAX_SHEET_NAME_LEN: int = 31
_UI_MAX_SUBJECT_TEMPLATE_LEN: int = 255
_UI_MAX_BODY_TEMPLATE_LEN: int = 100_000


class App(tk.Tk):
    """Main application window.

    Tabs:
        Settings  — edit and persist AppConfig fields.
        Run       — immediate execution, scheduler control, and live log.
    """

    def __init__(self) -> None:
        """Initialise the main window, load config, and build all widgets."""
        super().__init__()
        self.title("Excel Report Automation")
        self.resizable(True, True)
        self.minsize(680, 520)

        self._config_manager: ConfigManager = ConfigManager()
        self._config: AppConfig = self._config_manager.load()

        # Scheduler state
        self._scheduler_running: bool = False
        self._apscheduler: Optional[Scheduler] = None

        # Runner state
        self._runner_thread: Optional[threading.Thread] = None

        # Highlight state: 6-char HEX without '#'
        self._current_highlight_color: str = "FFFF00"

        self._build_ui()
        self._populate_fields()

        # Graceful shutdown
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """Create the top-level Notebook and both tabs."""
        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        tab_settings = ttk.Frame(notebook)
        tab_run = ttk.Frame(notebook)
        notebook.add(tab_settings, text="Settings")
        notebook.add(tab_run, text="Run")

        self._build_settings_tab(tab_settings)
        self._build_run_tab(tab_run)

    def _build_settings_tab(self, parent: ttk.Frame) -> None:
        """Populate the Settings tab with all configuration widgets.

        Args:
            parent: The ttk.Frame that hosts the settings tab content.
        """
        canvas = tk.Canvas(parent, borderwidth=0, highlightthickness=0)
        vscroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = ttk.Frame(canvas)
        canvas_window = canvas.create_window((0, 0), window=inner, anchor=tk.NW)

        def _on_inner_configure(event: tk.Event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event: tk.Event) -> None:
            canvas.itemconfig(canvas_window, width=event.width)

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        pad: Dict[str, Any] = {"padx": 8, "pady": 4}
        row = 0

        # -- Excel file path --------------------------------------------------
        self.lbl_excel_path = ttk.Label(inner, text="엑셀 파일 경로")
        self.lbl_excel_path.grid(row=row, column=0, sticky=tk.W, **pad)
        self.entry_excel_path = ttk.Entry(inner, width=48)
        self.entry_excel_path.grid(row=row, column=1, sticky=tk.EW, **pad)
        self.btn_browse_excel = ttk.Button(
            inner, text="찾아보기", command=self._on_browse_excel
        )
        self.btn_browse_excel.grid(row=row, column=2, **pad)
        row += 1

        # -- Section: Cell Range ----------------------------------------------
        self.lbl_section_cell_range = ttk.Label(
            inner, text="셀 범위", font=("", 9, "bold")
        )
        self.lbl_section_cell_range.grid(
            row=row, column=0, columnspan=3, sticky=tk.W, padx=8, pady=(12, 2)
        )
        row += 1

        self.lbl_sheet_name = ttk.Label(inner, text="시트명")
        self.lbl_sheet_name.grid(row=row, column=0, sticky=tk.W, **pad)
        self.entry_sheet_name = ttk.Entry(inner, width=24)
        self.entry_sheet_name.grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        spin_cfg: Dict[str, Any] = {
            "from_": 1,
            "to": 9999,
            "width": 7,
            "validate": "key",
        }
        vcmd = (self.register(self._validate_spin_input), "%P")

        self.lbl_start_row = ttk.Label(inner, text="첫 실행 시작 행")
        self.lbl_start_row.grid(row=row, column=0, sticky=tk.W, **pad)
        self.spn_start_row = ttk.Spinbox(
            inner, **spin_cfg, validatecommand=vcmd
        )
        self.spn_start_row.grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        self.lbl_start_col = ttk.Label(inner, text="시작 열")
        self.lbl_start_col.grid(row=row, column=0, sticky=tk.W, **pad)
        self.spn_start_col = ttk.Spinbox(
            inner, **spin_cfg, validatecommand=vcmd
        )
        self.spn_start_col.grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        self.lbl_end_col = ttk.Label(inner, text="끝 열")
        self.lbl_end_col.grid(row=row, column=0, sticky=tk.W, **pad)
        self.spn_end_col = ttk.Spinbox(
            inner, **spin_cfg, validatecommand=vcmd
        )
        self.spn_end_col.grid(row=row, column=1, sticky=tk.W, **pad)
        row += 1

        # -- Mark color -------------------------------------------------------
        self.lbl_section_color = ttk.Label(
            inner, text="처리 완료 색상", font=("", 9, "bold")
        )
        self.lbl_section_color.grid(
            row=row, column=0, columnspan=3, sticky=tk.W, padx=8, pady=(12, 2)
        )
        row += 1

        self.lbl_mark_color = ttk.Label(inner, text="색상 (HEX 6자리)")
        self.lbl_mark_color.grid(row=row, column=0, sticky=tk.W, **pad)
        self._var_mark_color = tk.StringVar()
        self.entry_mark_color = ttk.Entry(inner, width=12, textvariable=self._var_mark_color)
        self.entry_mark_color.grid(row=row, column=1, sticky=tk.W, **pad)
        self.cvs_color_preview = tk.Canvas(
            inner, width=28, height=22, bd=1, relief="solid"
        )
        self.cvs_color_preview.grid(row=row, column=2, sticky=tk.W, padx=(0, 4), pady=4)
        self.btn_pick_color = ttk.Button(
            inner, text="...", width=4, command=self._on_pick_color
        )
        self.btn_pick_color.grid(row=row, column=3, sticky=tk.W, padx=(0, 8), pady=4)
        self._var_mark_color.trace_add("write", self._on_mark_color_var_changed)
        row += 1

        # -- Email templates --------------------------------------------------
        self.lbl_section_email = ttk.Label(
            inner, text="이메일 템플릿", font=("", 9, "bold")
        )
        self.lbl_section_email.grid(
            row=row, column=0, columnspan=3, sticky=tk.W, padx=8, pady=(12, 2)
        )
        row += 1

        self.lbl_subject_template = ttk.Label(inner, text="제목 템플릿")
        self.lbl_subject_template.grid(row=row, column=0, sticky=tk.W, **pad)
        self.entry_subject_template = ttk.Entry(inner, width=48)
        self.entry_subject_template.grid(row=row, column=1, sticky=tk.EW, **pad)
        self.lbl_subject_hint = ttk.Label(
            inner, text="{date} 사용 가능", foreground="gray"
        )
        self.lbl_subject_hint.grid(row=row, column=2, sticky=tk.W, **pad)
        row += 1

        self.lbl_body_template = ttk.Label(inner, text="본문 템플릿")
        self.lbl_body_template.grid(row=row, column=0, sticky=tk.NW, **pad)

        # Highlight toolbar — sits above the body Text widget in column 1
        self.frm_highlight_toolbar = ttk.Frame(inner)
        self.frm_highlight_toolbar.grid(row=row, column=1, sticky=tk.EW, padx=8, pady=(4, 0))

        self.btn_pick_highlight_color = ttk.Button(
            self.frm_highlight_toolbar, text="색상 선택", command=self._on_choose_highlight_color
        )
        self.btn_pick_highlight_color.pack(side=tk.LEFT, padx=(0, 4))

        self.lbl_highlight_color_preview = tk.Label(
            self.frm_highlight_toolbar,
            width=3,
            height=1,
            bg=f"#{self._current_highlight_color}",
            relief="solid",
            bd=1,
        )
        self.lbl_highlight_color_preview.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_apply_highlight = ttk.Button(
            self.frm_highlight_toolbar, text="하이라이트 적용", command=self._on_apply_highlight
        )
        self.btn_apply_highlight.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_clear_highlight = ttk.Button(
            self.frm_highlight_toolbar, text="하이라이트 제거", command=self._on_clear_highlight
        )
        self.btn_clear_highlight.pack(side=tk.LEFT)

        row += 1

        self.lbl_body_template_spacer = ttk.Label(inner, text="")
        self.lbl_body_template_spacer.grid(row=row, column=0, sticky=tk.NW, **pad)
        self.txt_body_template = tk.Text(inner, height=5, width=48, wrap=tk.WORD)
        self.txt_body_template.grid(row=row, column=1, sticky=tk.EW, **pad)
        self.lbl_body_hint = ttk.Label(
            inner, text="{date},{data}\n사용 가능", foreground="gray", justify=tk.LEFT
        )
        self.lbl_body_hint.grid(row=row, column=2, sticky=tk.NW, **pad)
        row += 1

        # -- Recipients -------------------------------------------------------
        self.lbl_section_recipients = ttk.Label(
            inner, text="수신자", font=("", 9, "bold")
        )
        self.lbl_section_recipients.grid(
            row=row, column=0, columnspan=3, sticky=tk.W, padx=8, pady=(12, 2)
        )
        row += 1

        self.lbl_to_recipients = ttk.Label(inner, text="받는 사람 (To)")
        self.lbl_to_recipients.grid(row=row, column=0, sticky=tk.NW, **pad)
        self.txt_to_recipients = tk.Text(inner, height=4, width=48, wrap=tk.NONE)
        self.txt_to_recipients.grid(row=row, column=1, sticky=tk.EW, **pad)
        self.lbl_to_hint = ttk.Label(
            inner, text="줄 당 1개\n이메일 주소", foreground="gray", justify=tk.LEFT
        )
        self.lbl_to_hint.grid(row=row, column=2, sticky=tk.NW, **pad)
        row += 1

        self.lbl_cc_recipients = ttk.Label(inner, text="참조 (CC)")
        self.lbl_cc_recipients.grid(row=row, column=0, sticky=tk.NW, **pad)
        self.txt_cc_recipients = tk.Text(inner, height=3, width=48, wrap=tk.NONE)
        self.txt_cc_recipients.grid(row=row, column=1, sticky=tk.EW, **pad)
        self.lbl_cc_hint = ttk.Label(
            inner, text="줄 당 1개\n이메일 주소", foreground="gray", justify=tk.LEFT
        )
        self.lbl_cc_hint.grid(row=row, column=2, sticky=tk.NW, **pad)
        row += 1

        # -- Schedule ---------------------------------------------------------
        self.lbl_section_schedule = ttk.Label(
            inner, text="스케줄", font=("", 9, "bold")
        )
        self.lbl_section_schedule.grid(
            row=row, column=0, columnspan=3, sticky=tk.W, padx=8, pady=(12, 2)
        )
        row += 1

        self.lbl_schedule_time = ttk.Label(inner, text="실행 시간")
        self.lbl_schedule_time.grid(row=row, column=0, sticky=tk.W, **pad)
        self.entry_schedule_time = ttk.Entry(inner, width=10)
        self.entry_schedule_time.grid(row=row, column=1, sticky=tk.W, **pad)
        self.lbl_schedule_hint = ttk.Label(
            inner, text="HH:MM 형식 (예: 09:30)", foreground="gray"
        )
        self.lbl_schedule_hint.grid(row=row, column=2, sticky=tk.W, **pad)
        row += 1

        # -- Repeat enabled ---------------------------------------------------
        self._var_repeat_enabled = tk.BooleanVar(value=True)
        self.chk_repeat_enabled = ttk.Checkbutton(
            inner,
            text="평일 반복 실행 활성화 (스케줄러 시작 시 평일에만 자동 발송)",
            variable=self._var_repeat_enabled,
        )
        self.chk_repeat_enabled.grid(
            row=row, column=0, columnspan=3, sticky=tk.W, padx=8, pady=4
        )
        row += 1

        # -- Check modified ---------------------------------------------------
        self._var_check_modified = tk.BooleanVar(value=True)
        self.chk_check_modified = ttk.Checkbutton(
            inner,
            text="변경된 경우에만 실행 (전날 수정 여부 확인)",
            variable=self._var_check_modified,
        )
        self.chk_check_modified.grid(
            row=row, column=0, columnspan=3, sticky=tk.W, padx=8, pady=4
        )
        row += 1

        # -- Save button ------------------------------------------------------
        self.btn_save = ttk.Button(
            inner, text="설정 저장", command=self._on_save_settings
        )
        self.btn_save.grid(
            row=row, column=0, columnspan=3, pady=(16, 8)
        )
        row += 1

        # Make column 1 expand
        inner.columnconfigure(1, weight=1)

    def _build_run_tab(self, parent: ttk.Frame) -> None:
        """Populate the Run tab with execution controls, status, and log.

        Args:
            parent: The ttk.Frame that hosts the run tab content.
        """
        ctrl_frame = ttk.Frame(parent)
        ctrl_frame.pack(fill=tk.X, padx=8, pady=8)

        self.btn_run_now = ttk.Button(
            ctrl_frame, text="지금 실행", command=self._on_run_now, width=14
        )
        self.btn_run_now.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_force_send = ttk.Button(
            ctrl_frame, text="지금 강제 발송", command=self._on_force_send, width=16
        )
        self.btn_force_send.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_start_scheduler = ttk.Button(
            ctrl_frame, text="스케줄러 시작", command=self._on_toggle_scheduler, width=14
        )
        self.btn_start_scheduler.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_status = ttk.Label(
            ctrl_frame, text="대기 중", width=36, anchor=tk.W
        )
        self.lbl_status.pack(side=tk.LEFT, padx=(12, 0))

        # Progress bar
        self.prg_run = ttk.Progressbar(
            parent, mode="indeterminate", length=400
        )
        self.prg_run.pack(fill=tk.X, padx=8, pady=(0, 6))

        # Log area
        self.lbl_log = ttk.Label(parent, text="실행 로그")
        self.lbl_log.pack(anchor=tk.W, padx=8)

        log_frame = ttk.Frame(parent)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.txt_log = tk.Text(
            log_frame, state=tk.DISABLED, wrap=tk.WORD,
            font=("Consolas", 9), background="#1e1e1e", foreground="#d4d4d4",
        )
        log_scroll = ttk.Scrollbar(
            log_frame, orient=tk.VERTICAL, command=self.txt_log.yview
        )
        self.txt_log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tag for error lines
        self.txt_log.tag_configure("error", foreground="#f44747")
        self.txt_log.tag_configure("success", foreground="#4ec9b0")
        self.txt_log.tag_configure("info", foreground="#9cdcfe")

    # ------------------------------------------------------------------
    # Field population from config
    # ------------------------------------------------------------------

    def _populate_fields(self) -> None:
        """Write all config values into the Settings tab widgets."""
        c = self._config

        self.entry_excel_path.delete(0, tk.END)
        self.entry_excel_path.insert(0, c.get("excel_path", ""))

        cr = c.get("cell_range", {})
        self.entry_sheet_name.delete(0, tk.END)
        self.entry_sheet_name.insert(0, str(cr.get("sheet_name", "Sheet1")))

        self.spn_start_row.set(c.get("user_start_row", 1))
        self.spn_start_col.set(c.get("col_start", 1))
        self.spn_end_col.set(c.get("col_end", 10))

        self._var_mark_color.set(c.get("mark_color", "FFD700"))

        self.entry_subject_template.delete(0, tk.END)
        self.entry_subject_template.insert(0, c.get("email_subject_template", ""))

        self.txt_body_template.delete("1.0", tk.END)
        self.txt_body_template.insert("1.0", c.get("email_body_template", ""))

        self.txt_to_recipients.delete("1.0", tk.END)
        self.txt_to_recipients.insert("1.0", "\n".join(c.get("to_recipients", [])))

        self.txt_cc_recipients.delete("1.0", tk.END)
        self.txt_cc_recipients.insert("1.0", "\n".join(c.get("cc_recipients", [])))

        self.entry_schedule_time.delete(0, tk.END)
        self.entry_schedule_time.insert(0, c.get("schedule_time", "10:00"))

        self._var_check_modified.set(bool(c.get("check_modified", True)))
        self._var_repeat_enabled.set(bool(c.get("repeat_enabled", True)))

        highlight_ranges: List[Dict[str, str]] = c.get("highlight_ranges", [])
        self._restore_highlights(highlight_ranges)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_spin_input(value: str) -> bool:
        """Allow only digits (and empty string) in Spinbox fields.

        Args:
            value: The proposed new content of the Spinbox entry.

        Returns:
            True if the value is acceptable, False to reject the keypress.
        """
        return value == "" or value.isdigit()

    def _collect_emails(self, widget: tk.Text) -> Optional[List[str]]:
        """Parse newline-delimited email addresses from a Text widget.

        Args:
            widget: The tk.Text widget containing email addresses.

        Returns:
            A list of non-empty email strings, or None if any line is invalid.
        """
        raw = widget.get("1.0", tk.END).strip()
        if not raw:
            return []
        emails: List[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if not _EMAIL_RE.match(line):
                return None
            emails.append(line)
        return emails

    def _read_settings_from_ui(self) -> Optional[AppConfig]:
        """Collect and validate all Settings tab fields.

        Returns:
            An AppConfig dict on success, or None if validation fails
            (an error dialog is shown to the user).
        """
        excel_path = self.entry_excel_path.get().strip()
        if not excel_path:
            messagebox.showerror("입력 오류", "엑셀 파일 경로를 입력해 주세요.")
            return None
        if len(excel_path) > _UI_MAX_PATH_LEN:
            messagebox.showerror(
                "입력 오류",
                f"엑셀 파일 경로가 너무 깁니다. ({_UI_MAX_PATH_LEN}자 이하로 입력해 주세요.)",
            )
            return None

        sheet_name = self.entry_sheet_name.get().strip()
        if not sheet_name:
            messagebox.showerror("입력 오류", "시트명을 입력해 주세요.")
            return None
        if len(sheet_name) > _UI_MAX_SHEET_NAME_LEN:
            messagebox.showerror(
                "입력 오류",
                f"시트명이 너무 깁니다. ({_UI_MAX_SHEET_NAME_LEN}자 이하로 입력해 주세요.)",
            )
            return None

        def _spin_int(widget: ttk.Spinbox, label: str) -> Optional[int]:
            val = widget.get().strip()
            if not val.isdigit() or int(val) < 1:
                messagebox.showerror("입력 오류", f"{label} 값이 올바르지 않습니다.")
                return None
            return int(val)

        start_row = _spin_int(self.spn_start_row, "첫 실행 시작 행")
        if start_row is None:
            return None
        start_col = _spin_int(self.spn_start_col, "시작 열")
        if start_col is None:
            return None
        end_col = _spin_int(self.spn_end_col, "끝 열")
        if end_col is None:
            return None

        if start_col > end_col:
            messagebox.showerror(
                "입력 오류",
                "시작 열이 끝 열보다 클 수 없습니다.",
            )
            return None

        mark_color = self._var_mark_color.get().strip()
        if not _HEX_COLOR_RE.match(mark_color):
            messagebox.showerror(
                "입력 오류",
                "처리 완료 색상은 6자리 HEX 값이어야 합니다. (예: FFD700)",
            )
            return None

        subject_template = self.entry_subject_template.get().strip()
        if not subject_template:
            messagebox.showerror("입력 오류", "이메일 제목 템플릿을 입력해 주세요.")
            return None
        if len(subject_template) > _UI_MAX_SUBJECT_TEMPLATE_LEN:
            messagebox.showerror(
                "입력 오류",
                f"이메일 제목 템플릿이 너무 깁니다. ({_UI_MAX_SUBJECT_TEMPLATE_LEN}자 이하로 입력해 주세요.)",
            )
            return None

        body_template = self.txt_body_template.get("1.0", tk.END).rstrip("\n")
        if len(body_template) > _UI_MAX_BODY_TEMPLATE_LEN:
            messagebox.showerror(
                "입력 오류",
                f"이메일 본문 템플릿이 너무 깁니다. ({_UI_MAX_BODY_TEMPLATE_LEN:,}자 이하로 입력해 주세요.)",
            )
            return None

        to_recipients = self._collect_emails(self.txt_to_recipients)
        if to_recipients is None:
            messagebox.showerror(
                "입력 오류",
                "받는 사람(To)에 유효하지 않은 이메일 주소가 있습니다.",
            )
            return None

        cc_recipients = self._collect_emails(self.txt_cc_recipients)
        if cc_recipients is None:
            messagebox.showerror(
                "입력 오류",
                "참조(CC)에 유효하지 않은 이메일 주소가 있습니다.",
            )
            return None

        schedule_time = self.entry_schedule_time.get().strip()
        if not _TIME_RE.match(schedule_time):
            messagebox.showerror(
                "입력 오류",
                "실행 시간은 HH:MM 형식이어야 합니다. (예: 09:30)",
            )
            return None

        return AppConfig(
            excel_path=excel_path,
            user_start_row=start_row,
            col_start=start_col,
            col_end=end_col,
            cell_range={
                "sheet_name": sheet_name,
                "start_row": start_row,
                "start_col": start_col,
                "end_row": 9999,   # placeholder — 실제는 동적 감지
                "end_col": end_col,
            },
            mark_color=mark_color.upper(),
            email_subject_template=subject_template,
            email_body_template=body_template,
            to_recipients=to_recipients,
            cc_recipients=cc_recipients,
            schedule_time=schedule_time,
            check_modified=self._var_check_modified.get(),
            repeat_enabled=self._var_repeat_enabled.get(),
            highlight_ranges=self._get_highlight_ranges(),
        )

    # ------------------------------------------------------------------
    # Highlight helpers
    # ------------------------------------------------------------------

    def _on_choose_highlight_color(self) -> None:
        """Open the Excel 56-color palette popup and update the highlight color."""
        def _callback(chosen_hex: str) -> None:
            self._current_highlight_color = chosen_hex
            self.lbl_highlight_color_preview.config(bg=f"#{chosen_hex}")

        self._show_excel_color_palette(_callback)

    def _on_apply_highlight(self) -> None:
        """Apply a background-color tag to the currently selected text in txt_body_template.

        If no text is selected, a warning dialog is shown and nothing else happens.
        """
        try:
            sel_start: str = self.txt_body_template.index(tk.SEL_FIRST)
            sel_end: str = self.txt_body_template.index(tk.SEL_LAST)
        except tk.TclError:
            messagebox.showwarning("선택 없음", "하이라이트를 적용할 텍스트를 먼저 선택해 주세요.")
            return

        short_uuid: str = uuid.uuid4().hex[:8]
        color: str = self._current_highlight_color.upper()
        tag_name: str = f"hl_{color}_{short_uuid}"

        self.txt_body_template.tag_add(tag_name, sel_start, sel_end)
        self.txt_body_template.tag_configure(tag_name, background=f"#{color}")

    def _on_clear_highlight(self) -> None:
        """Remove all 'hl_' prefixed tags from the currently selected text range.

        If no text is selected, a warning dialog is shown and nothing else happens.
        """
        try:
            sel_start: str = self.txt_body_template.index(tk.SEL_FIRST)
            sel_end: str = self.txt_body_template.index(tk.SEL_LAST)
        except tk.TclError:
            messagebox.showwarning("선택 없음", "하이라이트를 제거할 텍스트를 먼저 선택해 주세요.")
            return

        all_tags: Any = self.txt_body_template.tag_names()
        for tag in all_tags:
            if isinstance(tag, str) and tag.startswith("hl_"):
                self.txt_body_template.tag_remove(tag, sel_start, sel_end)

    def _get_highlight_ranges(self) -> List[Dict[str, str]]:
        """Collect all active highlight tags and their ranges from txt_body_template.

        Iterates every tag whose name starts with 'hl_', skipping the 'sel' tag,
        and builds a list of dicts with keys 'start', 'end', and 'color' (6-char
        HEX without '#').

        Returns:
            A list of highlight range dicts suitable for persistence in AppConfig.
        """
        ranges: List[Dict[str, str]] = []
        all_tags: Any = self.txt_body_template.tag_names()
        for tag in all_tags:
            if not isinstance(tag, str):
                continue
            if tag == "sel" or not tag.startswith("hl_"):
                continue
            # Extract color from tag configuration
            try:
                tag_cfg: Any = self.txt_body_template.tag_configure(tag)
                # tag_configure returns a dict keyed by option name; value is a tuple
                bg_entry: Any = tag_cfg.get("background")
                if bg_entry is None:
                    continue
                # bg_entry is typically a 5-tuple; the last element is the current value
                if isinstance(bg_entry, (tuple, list)) and len(bg_entry) >= 1:
                    bg_value: Any = bg_entry[-1]
                else:
                    bg_value = bg_entry
                if not isinstance(bg_value, str) or not bg_value:
                    continue
                color_hex: str = bg_value.lstrip("#").upper()
                if not _HEX_COLOR_RE.match(color_hex):
                    continue
            except tk.TclError:
                continue

            # Extract ranges for this tag
            tag_ranges: Any = self.txt_body_template.tag_ranges(tag)
            # tag_ranges returns a flat tuple: (start1, end1, start2, end2, ...)
            idx: int = 0
            while idx + 1 < len(tag_ranges):
                start_index: str = str(tag_ranges[idx])
                end_index: str = str(tag_ranges[idx + 1])
                ranges.append(
                    {
                        "start": start_index,
                        "end": end_index,
                        "color": color_hex,
                    }
                )
                idx += 2

        return ranges

    def _restore_highlights(self, highlight_ranges: List[Dict[str, str]]) -> None:
        """Re-apply highlight tags to txt_body_template from persisted config data.

        Invalid entries (bad color HEX or malformed Tkinter indices) are silently
        skipped so that the rest of the restore continues uninterrupted.

        Args:
            highlight_ranges: List of dicts with keys 'start', 'end', 'color'.
        """
        _TK_INDEX_RE = re.compile(r"^\d+\.\d+$")

        if not highlight_ranges:
            return

        for rng in highlight_ranges:
            if not isinstance(rng, dict):
                continue
            color: Any = rng.get("color", "")
            start_idx: Any = rng.get("start", "")
            end_idx: Any = rng.get("end", "")

            # Validate color
            if not isinstance(color, str) or not _HEX_COLOR_RE.match(color):
                continue  # skip invalid color (e.g. "ZZZZZZ")

            # Validate Tkinter index format
            if not isinstance(start_idx, str) or not _TK_INDEX_RE.match(start_idx):
                continue
            if not isinstance(end_idx, str) or not _TK_INDEX_RE.match(end_idx):
                continue

            short_uuid: str = uuid.uuid4().hex[:8]
            tag_name: str = f"hl_{color.upper()}_{short_uuid}"
            try:
                self.txt_body_template.tag_add(tag_name, start_idx, end_idx)
                self.txt_body_template.tag_configure(
                    tag_name, background=f"#{color.upper()}"
                )
            except tk.TclError:
                # Invalid index after text reload — skip silently
                continue

    # ------------------------------------------------------------------
    # Color preview helpers
    # ------------------------------------------------------------------

    def _on_mark_color_var_changed(self, *args: Any) -> None:
        """Update the color preview canvas whenever the mark-color StringVar changes.

        Args:
            *args: Variable trace arguments (name, index, mode) — unused.
        """
        val = self._var_mark_color.get().strip()
        if _HEX_COLOR_RE.match(val):
            self.cvs_color_preview.config(bg=f"#{val.upper()}")
        else:
            self.cvs_color_preview.config(bg="#ffffff")

    def _show_excel_color_palette(self, callback: Callable[[str], None]) -> None:
        """Open a Toplevel popup showing the Excel 56-color palette as an 8x7 grid.

        Clicking a color button calls callback with the 6-char uppercase HEX string
        and closes the popup.

        Args:
            callback: A callable that receives the selected color as a 6-char HEX
                      string (no '#' prefix, uppercase).
        """
        popup: tk.Toplevel = tk.Toplevel(self)
        popup.title("색상 선택")
        popup.resizable(False, False)
        popup.grab_set()

        cols: int = 8
        for idx, hex_color in enumerate(EXCEL_56_COLORS):
            r: int = idx // cols
            c: int = idx % cols
            bg: str = f"#{hex_color}"

            def _make_cmd(hx: str, win: tk.Toplevel) -> Callable[[], None]:
                def _cmd() -> None:
                    callback(hx)
                    win.destroy()
                return _cmd

            btn: tk.Button = tk.Button(
                popup,
                bg=bg,
                activebackground=bg,
                width=2,
                height=1,
                bd=1,
                relief="raised",
                command=_make_cmd(hex_color, popup),
            )
            btn.grid(row=r, column=c, padx=1, pady=1)

    def _on_pick_color(self) -> None:
        """Open the Excel 56-color palette popup and write the chosen hex value back."""
        def _callback(chosen_hex: str) -> None:
            self._var_mark_color.set(chosen_hex)

        self._show_excel_color_palette(_callback)

    # ------------------------------------------------------------------
    # Event handlers — Settings tab
    # ------------------------------------------------------------------

    def _on_browse_excel(self) -> None:
        """Open a file dialog to choose an Excel file and populate the path entry."""
        path = filedialog.askopenfilename(
            title="엑셀 파일 선택",
            filetypes=[("Excel files", "*.xlsx *.xls *.xlsm"), ("All files", "*.*")],
        )
        if path:
            self.entry_excel_path.delete(0, tk.END)
            self.entry_excel_path.insert(0, path)

    def _on_save_settings(self) -> None:
        """Validate inputs, persist to config.json, and reschedule if needed."""
        new_config = self._read_settings_from_ui()
        if new_config is None:
            return

        ok = self._config_manager.save(new_config)
        if ok:
            self._config = new_config
            # If scheduler is running, reschedule at new time
            if self._scheduler_running:
                self._reschedule_job()
            messagebox.showinfo("저장 완료", "설정이 저장되었습니다.")
        else:
            messagebox.showerror("저장 실패", "config.json 저장 중 오류가 발생했습니다.")

    # ------------------------------------------------------------------
    # Event handlers — Run tab
    # ------------------------------------------------------------------

    def _on_run_now(self) -> None:
        """Start the automation pipeline in a background thread."""
        if self._runner_thread and self._runner_thread.is_alive():
            messagebox.showwarning("실행 중", "이미 자동화가 실행 중입니다.")
            return

        self._set_running_state(True)
        self._append_log("자동화 실행 시작...", tag="info")

        self._runner_thread = threading.Thread(
            target=self._run_automation_task, daemon=True
        )
        self._runner_thread.start()

    def _on_force_send(self) -> None:
        """Start the automation pipeline in forced mode, bypassing the modification check.

        Runs in a daemon background thread. Disables both execution buttons
        while the task is in progress and re-enables them on completion or error.
        """
        if self._runner_thread and self._runner_thread.is_alive():
            messagebox.showwarning("실행 중", "이미 자동화가 실행 중입니다.")
            return

        self._set_running_state(True)
        self._append_log("[강제 발송 모드] 수정 확인 건너뜀", tag="info")

        self._runner_thread = threading.Thread(
            target=self._run_automation_task,
            kwargs={"force": True},
            daemon=True,
        )
        self._runner_thread.start()

    def _run_automation_task(self, force: bool = False) -> None:
        """Execute AutomationRunner.run() in a background thread and update UI.

        This method must only be called from a non-main thread.

        Args:
            force: When True, the modification check inside AutomationRunner is
                   bypassed and the pipeline proceeds from Step 2 onward.
                   Defaults to False to preserve the existing _on_run_now() behaviour.
        """
        try:
            runner = AutomationRunner(self._config)
            result: Dict[str, Any] = runner.run(force=force)
        except Exception as exc:
            logger.exception("_run_automation_task: unexpected exception: %s", exc)
            generic_msg: str = "자동화 실행 중 예기치 않은 오류가 발생했습니다."
            self.after(0, self._on_task_error, generic_msg)
            return

        self.after(0, self._on_task_done, result)

    def _on_task_done(self, result: Dict[str, Any]) -> None:
        """Handle pipeline completion on the main thread.

        Args:
            result: The dict returned by AutomationRunner.run().
        """
        self._set_running_state(False)
        status = result.get("status", "unknown")

        if status == "success":
            subject = result.get("subject", "")
            mark_note = " (셀 표시 실패)" if result.get("mark_failed") else ""
            msg = f"성공{mark_note} — 제목: {subject}"
            self.lbl_status.config(text=f"마지막 실행: 성공{mark_note}")
            self._append_log(msg, tag="success")
        elif status == "skipped":
            self.lbl_status.config(text="마지막 실행: 건너뜀 (변경 없음)")
            self._append_log("건너뜀 — 전날 파일 변경 없음.", tag="info")
        else:
            stage = result.get("stage", "")
            error = result.get("error", "알 수 없는 오류")
            self.lbl_status.config(text=f"마지막 실행: 오류 ({stage})")
            self._append_log(f"오류 [{stage}]: {error}", tag="error")

    def _on_task_error(self, message: str) -> None:
        """Handle an unexpected exception from the runner thread.

        Args:
            message: The string representation of the exception.
        """
        self._set_running_state(False)
        self.lbl_status.config(text="마지막 실행: 예외 발생")
        self._append_log(f"예외: {message}", tag="error")

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------

    def _on_toggle_scheduler(self) -> None:
        """Start or stop the background scheduler."""
        if self._scheduler_running:
            self._stop_scheduler()
        else:
            self._start_scheduler()

    def _start_scheduler(self) -> None:
        """Instantiate APScheduler Scheduler and start the background job."""
        schedule_time: str = self._config.get("schedule_time", "10:00")
        hour: int = int(schedule_time.split(":")[0])
        minute: int = int(schedule_time.split(":")[1])
        self._apscheduler = Scheduler(job_func=self._scheduled_run, hour=hour, minute=minute)
        self._apscheduler.start()
        self._scheduler_running = True
        self.btn_start_scheduler.config(text="스케줄러 중지")
        self.lbl_status.config(text=f"스케줄러 실행 중 ({schedule_time}) [평일만]")
        self._append_log(f"스케줄러 시작 — 평일 {schedule_time}에 실행 예정", tag="info")

    def _stop_scheduler(self) -> None:
        """Stop the APScheduler background job and reset state."""
        if self._apscheduler is not None:
            self._apscheduler.stop()
            self._apscheduler = None
        self._scheduler_running = False
        self.btn_start_scheduler.config(text="스케줄러 시작")
        self.lbl_status.config(text="스케줄러 중지됨")
        self._append_log("스케줄러 중지.", tag="info")

    def _reschedule_job(self) -> None:
        """Update the APScheduler job to the currently configured time."""
        if self._apscheduler is not None:
            schedule_time: str = self._config.get("schedule_time", "10:00")
            hour: int = int(schedule_time.split(":")[0])
            minute: int = int(schedule_time.split(":")[1])
            self._apscheduler.update_time(hour, minute)

    def _scheduled_run(self) -> None:
        """Trigger a run from the scheduler thread, posting work to the main thread."""
        self.after(0, self._on_run_now)

    # ------------------------------------------------------------------
    # UI state helpers
    # ------------------------------------------------------------------

    def _set_running_state(self, running: bool) -> None:
        """Enable/disable controls and toggle the progress bar.

        Disables both btn_run_now and btn_force_send while a pipeline task is
        active, and restores them to NORMAL state on completion or error.

        Args:
            running: True while the automation pipeline is executing.
        """
        if running:
            self.btn_run_now.config(state=tk.DISABLED)
            self.btn_force_send.config(state=tk.DISABLED)
            self.lbl_status.config(text="실행 중...")
            self.prg_run.start(15)
        else:
            self.btn_run_now.config(state=tk.NORMAL)
            self.btn_force_send.config(state=tk.NORMAL)
            self.prg_run.stop()

    def _append_log(self, message: str, tag: str = "info") -> None:
        """Append a timestamped line to the log Text widget.

        Args:
            message: The text to append.
            tag:     One of 'info', 'success', 'error' — controls colour.
        """
        import datetime

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"
        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.insert(tk.END, line, tag)
        self.txt_log.see(tk.END)
        self.txt_log.config(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        """Stop the scheduler thread before destroying the window."""
        if self._scheduler_running:
            self._stop_scheduler()
        self.destroy()
