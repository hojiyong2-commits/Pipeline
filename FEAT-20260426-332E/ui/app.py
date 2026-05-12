"""KittingMapper Tkinter GUI application."""

import json
import logging
import logging.handlers
import os
import queue
import re
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Hardcoded config values (not exposed in UI)
_FIXED_PACKING_SHEET = "2021년"
_FIXED_EXCEL_B_SHEET = "Customer Order Lines"
_FIXED_EXCEL_B_SEARCH_COL = "A"
_FIXED_EXCEL_A_COLUMNS = {
    "month": "A", "day": "B", "division": "C", "pm": "D", "sn": "E",
    "location": "F", "po": "G", "project_id": "H", "contract": "I",
    "incoterm": "J", "note": "K",
}
_FIXED_EXCEL_B_COLUMNS = {"fp": "J", "f_col": "F", "jm": "KJ", "if_col": "C", "jy_col": "E"}


from core.utils import _compress_line_nos


class KittingMapperApp(tk.Tk):
    """Main application window for KittingMapper."""

    def __init__(self) -> None:
        super().__init__()
        self.title("KittingMapper")
        self.minsize(640, 460)

        self._pm_map: Dict[str, str] = {}
        self._log_queue: queue.Queue = queue.Queue()
        self._polling: bool = False

        self._build_notebook()
        self._build_settings_tab()
        self._build_run_tab()
        self._load_config_into_ui()

    # ------------------------------------------------------------------
    # Notebook
    # ------------------------------------------------------------------

    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)

        self.tab_settings = ttk.Frame(self.notebook)
        self.tab_run = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_settings, text="설정")
        self.notebook.add(self.tab_run, text="실행")

    # ------------------------------------------------------------------
    # 설정 탭
    # ------------------------------------------------------------------

    def _build_settings_tab(self) -> None:
        outer = ttk.Frame(self.tab_settings)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        # Path fields
        path_frame = ttk.LabelFrame(outer, text="파일 경로")
        path_frame.pack(fill="x", pady=(0, 8))

        self._make_path_row(path_frame, 0, "출력 Excel (A)",
                            "entry_output_excel_path", "btn_browse_output_excel_path")
        self._make_path_row(path_frame, 1, "포장 상세 Excel",
                            "entry_packing_detail_path", "btn_browse_packing_detail_path")
        self._make_path_row(path_frame, 2, "Order Lines Excel (B)",
                            "entry_order_lines_path", "btn_browse_order_lines_path")

        # PM name mapping
        self._build_pm_name_map(outer)

        # Save button
        self.btn_save = ttk.Button(outer, text="설정 저장", command=self._save_config)
        self.btn_save.pack(pady=(8, 0))

    def _make_path_row(self, parent: ttk.Frame, row: int, label: str,
                       attr_entry: str, attr_btn: str) -> None:
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, width=22, anchor="e").grid(
            row=row, column=0, sticky="e", padx=(6, 4), pady=4)

        entry = ttk.Entry(parent)
        entry.grid(row=row, column=1, sticky="ew", padx=(0, 4), pady=4)
        setattr(self, attr_entry, entry)

        btn = ttk.Button(parent, text="찾아보기",
                         command=lambda e=entry: self._browse_file(e))
        btn.grid(row=row, column=2, sticky="w", padx=(0, 6), pady=4)
        setattr(self, attr_btn, btn)

    def _build_pm_name_map(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="PM 이름 매핑 (영문 → 한국어)")
        frame.pack(fill="x", pady=(0, 8))

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="x", padx=6, pady=(4, 2))

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.lbx_pm_name_map = tk.Listbox(
            list_frame, height=5,
            yscrollcommand=scrollbar.set, selectmode="single")
        scrollbar.config(command=self.lbx_pm_name_map.yview)
        self.lbx_pm_name_map.pack(side="left", fill="x", expand=True)
        scrollbar.pack(side="right", fill="y")

        input_row = ttk.Frame(frame)
        input_row.pack(fill="x", padx=6, pady=(0, 4))

        self.lbl_pm_en = ttk.Label(input_row, text="영문")
        self.lbl_pm_en.pack(side="left")
        self.entry_pm_en = ttk.Entry(input_row, width=22)
        self.entry_pm_en.pack(side="left", padx=(2, 8))
        self.entry_pm_en.bind("<Return>", lambda _e: self._pm_add())

        self.lbl_pm_ko = ttk.Label(input_row, text="한국어")
        self.lbl_pm_ko.pack(side="left")
        self.entry_pm_ko = ttk.Entry(input_row, width=22)
        self.entry_pm_ko.pack(side="left", padx=(2, 8))
        self.entry_pm_ko.bind("<Return>", lambda _e: self._pm_add())

        self.btn_pm_add = ttk.Button(input_row, text="추가", command=self._pm_add)
        self.btn_pm_add.pack(side="left", padx=(0, 4))

        self.btn_pm_delete = ttk.Button(input_row, text="삭제", command=self._pm_delete)
        self.btn_pm_delete.pack(side="left")

    # ------------------------------------------------------------------
    # 실행 탭
    # ------------------------------------------------------------------

    def _build_run_tab(self) -> None:
        outer = ttk.Frame(self.tab_run)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        # Date selector row
        date_frame = ttk.Frame(outer)
        date_frame.pack(fill="x", pady=(0, 6))

        self.lbl_target_date = ttk.Label(date_frame, text="처리 날짜:")
        self.lbl_target_date.pack(side="left", padx=(0, 6))

        self.entry_target_date = ttk.Entry(date_frame, width=12)
        self.entry_target_date.pack(side="left", padx=(0, 6))

        self.btn_prev_biz_day = ttk.Button(
            date_frame,
            text="이전 영업일",
            command=self._set_prev_biz_day,
        )
        self.btn_prev_biz_day.pack(side="left")

        # Default: previous business day
        from core.business_day import get_previous_business_day as _gpbd
        from datetime import date as _d
        _default_date = _gpbd(_d.today()).strftime("%Y-%m-%d")
        self.entry_target_date.insert(0, _default_date)

        self.btn_run = ttk.Button(outer, text="실행", command=self._run_pipeline)
        self.btn_run.pack(pady=(0, 6))

        self.pb_loading = ttk.Progressbar(outer, mode="indeterminate", length=400)
        self.pb_loading.pack(pady=(0, 6))

        log_frame = ttk.Frame(outer)
        log_frame.pack(fill="both", expand=True)

        log_scroll = ttk.Scrollbar(log_frame, orient="vertical")
        self.txt_log = tk.Text(
            log_frame, state="disabled", wrap="word",
            yscrollcommand=log_scroll.set)
        log_scroll.config(command=self.txt_log.yview)
        self.txt_log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")
        self.txt_log.tag_configure("error", foreground="red")

    def _set_prev_biz_day(self) -> None:
        """Fill entry_target_date with the previous business day (YYYY-MM-DD)."""
        from core.business_day import get_previous_business_day
        from datetime import date as _date
        prev = get_previous_business_day(_date.today())
        self.entry_target_date.delete(0, "end")
        self.entry_target_date.insert(0, prev.strftime("%Y-%m-%d"))

    # ------------------------------------------------------------------
    # File browse
    # ------------------------------------------------------------------

    def _browse_file(self, entry: ttk.Entry) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("Excel", "*.xlsx *.xls"), ("모든 파일", "*.*")])
        if path:
            entry.delete(0, tk.END)
            entry.insert(0, path)

    # ------------------------------------------------------------------
    # PM map helpers
    # ------------------------------------------------------------------

    def _pm_add(self) -> None:
        en: str = self.entry_pm_en.get().strip()
        ko: str = self.entry_pm_ko.get().strip()
        if not en:
            messagebox.showerror("입력 오류", "영문 이름을 입력하세요.")
            return
        if not ko:
            messagebox.showerror("입력 오류", "한국어 이름을 입력하세요.")
            return
        if en in self._pm_map:
            idx = self._find_listbox_index(en)
            if idx is not None:
                self.lbx_pm_name_map.delete(idx)
        self._pm_map[en] = ko
        self.lbx_pm_name_map.insert(tk.END, f"{en} → {ko}")
        self.entry_pm_en.delete(0, tk.END)
        self.entry_pm_ko.delete(0, tk.END)

    def _pm_delete(self) -> None:
        selection: Tuple[int, ...] = self.lbx_pm_name_map.curselection()
        if not selection:
            messagebox.showwarning("선택 없음", "삭제할 항목을 선택하세요.")
            return
        idx: int = selection[0]
        en: str = self.lbx_pm_name_map.get(idx).split(" → ")[0]
        self.lbx_pm_name_map.delete(idx)
        self._pm_map.pop(en, None)

    def _find_listbox_index(self, en_name: str) -> Optional[int]:
        for i in range(self.lbx_pm_name_map.size()):
            if self.lbx_pm_name_map.get(i).startswith(en_name + " → "):
                return i
        return None

    # ------------------------------------------------------------------
    # _save_config
    # ------------------------------------------------------------------

    def _save_config(self) -> None:
        # Load existing config to preserve keys not managed by UI (e.g. pm_location_overrides)
        if getattr(sys, "frozen", False):
            _cfg_dir = Path(sys.executable).parent
        else:
            _cfg_dir = Path(__file__).parent.parent
        _existing: Dict[str, object] = {}
        _cfg_path = _cfg_dir / "config.json"
        if _cfg_path.exists():
            for _enc in ("utf-8", "cp949", "latin-1"):
                try:
                    _existing = json.loads(_cfg_path.read_text(encoding=_enc))
                    break
                except (UnicodeDecodeError, LookupError, json.JSONDecodeError):
                    continue

        config: Dict[str, object] = {
            **_existing,  # preserve all existing keys (including pm_location_overrides)
            "output_excel_path":     self.entry_output_excel_path.get().strip(),
            "packing_detail_path":   self.entry_packing_detail_path.get().strip(),
            "order_lines_path":      self.entry_order_lines_path.get().strip(),
            "pm_name_map":           self._pm_map,
            # Fixed values
            "packing_detail_sheet":  _FIXED_PACKING_SHEET,
            "excel_b_sheet":         _FIXED_EXCEL_B_SHEET,
            "excel_b_search_column": _FIXED_EXCEL_B_SEARCH_COL,
            "excel_a_columns":       _FIXED_EXCEL_A_COLUMNS,
            "excel_b_columns":       _FIXED_EXCEL_B_COLUMNS,
        }

        config_path = _cfg_dir / "config.json"

        try:
            fd, tmp_name = tempfile.mkstemp(dir=str(_cfg_dir), suffix=".json.tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(config, fh, ensure_ascii=False, indent=2)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            os.replace(tmp_name, str(config_path))
        except Exception as exc:
            messagebox.showerror("저장 오류", f"설정 저장 중 오류 발생:\n{exc}")
            return

        messagebox.showinfo("저장 완료", "설정이 저장되었습니다.")

    # ------------------------------------------------------------------
    # _load_config_into_ui
    # ------------------------------------------------------------------

    def _load_config_into_ui(self) -> None:
        try:
            from core.config_loader import load_config
            config: dict = load_config()
        except FileNotFoundError:
            return
        except ValueError as exc:
            messagebox.showwarning("설정 오류", f"설정 파일 파싱 오류:\n{exc}")
            return
        except Exception:
            return

        self._set_entry(self.entry_output_excel_path, config.get("output_excel_path", ""))
        self._set_entry(self.entry_packing_detail_path, config.get("packing_detail_path", ""))
        self._set_entry(self.entry_order_lines_path, config.get("order_lines_path", ""))

        pm_map: dict = config.get("pm_name_map", {})
        if not isinstance(pm_map, dict):
            pm_map = {}
        self.lbx_pm_name_map.delete(0, tk.END)
        self._pm_map = {}
        for en, ko in pm_map.items():
            if isinstance(en, str) and isinstance(ko, str):
                self._pm_map[en] = ko
                self.lbx_pm_name_map.insert(tk.END, f"{en} → {ko}")

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    def _run_pipeline(self) -> None:
        self.btn_run.config(state="disabled")
        self.pb_loading.start(10)
        self.txt_log.config(state="normal")
        self.txt_log.delete("1.0", tk.END)
        self.txt_log.config(state="disabled")
        self.notebook.select(1)
        self._polling = True
        threading.Thread(target=self._pipeline_worker, daemon=True).start()
        self.after(100, self._poll_log_queue)

    def _pipeline_worker(self) -> None:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            queue_handler = logging.handlers.QueueHandler(self._log_queue)
            queue_handler.setLevel(logging.DEBUG)
            root_logger = logging.getLogger()
            root_logger.addHandler(queue_handler)

            try:
                from core.config_loader import load_config
                from core.business_day import get_previous_business_day
                from core.outlook_reader import find_kitting_email, parse_kitting_table
                from core.packing_detail_reader import lookup_packing_dimensions
                from core.order_lines_reader import lookup_order_lines
                from core.excel_mapper import write_to_excel_a, update_note_by_sn, read_note_by_sn
                from core.pending_store import load_pending, save_pending, PendingEntry
                from datetime import date

                config: dict = load_config()

                if getattr(sys, "frozen", False):
                    config_dir = Path(sys.executable).parent
                else:
                    config_dir = Path(__file__).parent.parent

                packing_detail_path: str = config.get("packing_detail_path", "")
                packing_detail_sheet: str = config.get("packing_detail_sheet", _FIXED_PACKING_SHEET)
                order_lines_path: str = config.get("order_lines_path", "")
                output_excel_path: str = config.get("output_excel_path", "")
                pm_name_map: dict = config.get("pm_name_map", {})
                pm_location_overrides: dict = config.get("pm_location_overrides", {})
                if not isinstance(pm_location_overrides, dict):
                    pm_location_overrides = {}
                excel_a_columns: dict = config.get("excel_a_columns", _FIXED_EXCEL_A_COLUMNS)
                excel_b_sheet: str = config.get("excel_b_sheet", _FIXED_EXCEL_B_SHEET)
                excel_b_search_col: str = config.get("excel_b_search_column", _FIXED_EXCEL_B_SEARCH_COL)
                excel_b_columns: dict = config.get("excel_b_columns", _FIXED_EXCEL_B_COLUMNS)

                today: date = date.today()
                _date_str: str = self.entry_target_date.get().strip()
                if _date_str:
                    try:
                        target_date: date = date.fromisoformat(_date_str)
                    except ValueError:
                        logger.warning("날짜 형식 오류 '%s' — 이전 영업일로 대체", _date_str)
                        target_date = get_previous_business_day(today)
                else:
                    target_date = get_previous_business_day(today)
                logger.info("오늘: %s | 검색 날짜: %s", today, target_date)
                logger.info("%s 이메일 검색 중...", target_date)
                html_body: Optional[str] = find_kitting_email(target_date)
                if html_body is None:
                    self._log_queue.put(("ERROR", "이메일을 찾을 수 없습니다."))
                    return

                kit_rows: list = parse_kitting_table(html_body)
                if not kit_rows:
                    self._log_queue.put(("ERROR", "처리할 행이 없습니다."))
                    return

                logger.info("%d건 파싱됨", len(kit_rows))
                mapped_rows: list = []
                conflict_rows: list = []
                pending_new: list = []

                for kit_row in kit_rows:
                    customer: str = kit_row["customer"]
                    project_id: str = kit_row["project_id"]
                    sn: str = kit_row["sn"]
                    kit_place: str = kit_row["kit_place"]
                    remark: str = kit_row["remark"]

                    note: str
                    if "창고" in kit_place:
                        note = f"창고 ({remark})"
                    elif "포장반" in kit_place:
                        try:
                            dims_result = lookup_packing_dimensions(
                                packing_detail_path, project_id,
                                sheet_name=packing_detail_sheet,
                            )
                        except Exception as exc:
                            logger.warning(
                                "치수 조회 실패 project_id '%s': %s", project_id, exc
                            )
                            dims_result = None

                        if isinstance(dims_result, str):
                            note = f"포장반 ({dims_result})"
                        else:  # None — no valid K-date rows found; record as pending for auto-update on next run
                            note = kit_place
                            pending_new.append({
                                "sn": sn,
                                "project_id": project_id,
                                "kit_place": kit_place,
                                "line_nos": [],
                                "f_col_conflict": False,  # updated in order-lookup block below
                            })
                    else:
                        note = kit_place

                    try:
                        order: dict = lookup_order_lines(
                            order_lines_path, project_id,
                            sheet_name=excel_b_sheet,
                            search_col_letter=excel_b_search_col,
                            fp_col_letter=excel_b_columns.get("fp", "J"),
                            f_col_letter=excel_b_columns.get("f_col", "F"),
                            jm_col_letter=excel_b_columns.get("jm", "G"),
                            if_col_letter=excel_b_columns.get("if_col", "C"),
                            jy_col_letter=excel_b_columns.get("jy_col", "E"),
                            planned_date=kit_row.get("planned_date"),
                        )
                    except Exception as exc:
                        logger.warning("Order Lines 조회 실패 '%s': %s", project_id, exc)
                        order = {"fp": "", "f_col": "", "jm": "", "if_col": "", "line_nos": [],
                                 "f_col_conflict": False, "f_col_all": [], "jy_col": "", "found": False}

                    # Skip if not found in CustomerOrderLines
                    if not order.get("found"):
                        logger.info("스킵: project_id='%s' — CustomerOrderLines 미존재", project_id)
                        continue

                    # Log conflict warning but continue processing with resolved row
                    if order.get("f_col_conflict"):
                        conflict_vals = order.get("f_col_all", [])
                        msg = (
                            f"[충돌] project_id='{project_id}' customer='{customer}' — "
                            f"Contract(F열) 날짜 {len(conflict_vals)}가지: {conflict_vals}"
                        )
                        logger.warning(msg)
                        conflict_rows.append(msg)
                        # Do NOT skip — execution falls through to use the (now-resolved) order data

                    line_nos: list = [str(n) for n in (order.get("line_nos") or [])]
                    if line_nos and order.get("f_col_conflict", False):
                        note = f"{note} #{_compress_line_nos(line_nos)}"

                    # Update line_nos and f_col_conflict in pending_new entry if this sn is pending
                    for _pe in pending_new:
                        if _pe["sn"] == sn:
                            _pe["line_nos"] = line_nos
                            _pe["f_col_conflict"] = order.get("f_col_conflict", False)
                            break

                    jm_value: str = str(order.get("jm", "")).strip()  # allowed: order dict values are Any; str() safe for dict key lookup
                    if jm_value in pm_location_overrides:
                        _loc_map: dict = pm_location_overrides[jm_value]
                        pm_korean = _loc_map.get(customer, _loc_map.get("_default", ""))
                        if not pm_korean:
                            logger.info("스킵: project_id='%s' — PM '%s' location override 매핑 없음", project_id, jm_value)
                            continue
                    elif jm_value and jm_value in pm_name_map:
                        pm_korean = pm_name_map[jm_value]
                    else:
                        logger.info("스킵: project_id='%s' — PM '%s' 매핑 없음", project_id, jm_value)
                        continue

                    # MT-3: Contract = F column (Target Date) value directly, no JY fallback
                    contract_val: str = str(order.get("f_col", ""))  # allowed: order dict values are Any; str() safe for f_col extraction

                    mapped_rows.append({
                        "month":      today.month,
                        "day":        today.day,
                        "pm":         pm_korean,
                        "sn":         sn,
                        "location":   customer,
                        "po":         str(order.get("if_col", "")),
                        "project_id": project_id,
                        "contract":   contract_val,
                        "incoterm":   str(order.get("fp", "")),
                        "note":       note,
                    })
                    logger.info("처리: %s / %s / %s", project_id, customer, note)

                write_to_excel_a(output_excel_path, mapped_rows,
                                 column_map=excel_a_columns if excel_a_columns else None)

                updated_count = 0  # defined before pending block so summary can reference it safely

                # --- Pending packing 처리 ---
                try:
                    existing_pending: list = load_pending(config_dir)
                    existing_sns: set = {e["sn"] for e in existing_pending}
                    for _pe in pending_new:
                        if _pe["sn"] not in existing_sns:
                            existing_pending.append(_pe)
                            existing_sns.add(_pe["sn"])

                    still_pending: list = []
                    for _pe in existing_pending:
                        try:
                            _dims = lookup_packing_dimensions(
                                packing_detail_path, _pe["project_id"],
                                sheet_name=packing_detail_sheet,
                            )
                        except Exception as _exc:
                            logger.warning(
                                "pending 재조회 실패 sn='%s': %s", _pe["sn"], _exc
                            )
                            _dims = None

                        if isinstance(_dims, str):
                            _compressed = _compress_line_nos(_pe.get("line_nos", []))
                            # Read current Note from Excel to preserve any manual prefix edits
                            # (e.g. "당일 포장반" instead of original "포장반")
                            _current_note: Optional[str] = read_note_by_sn(
                                output_excel_path, _pe["sn"],
                                column_map=excel_a_columns if excel_a_columns else None,
                            )
                            if _current_note is not None and _current_note.strip():
                                # Strip existing parenthesis suffix: "당일 포장반 (이전치수)" → "당일 포장반"
                                _base_note = re.sub(r'\s*\(.*', '', _current_note).strip()
                            else:
                                _base_note = _pe["kit_place"]
                            _new_note = f"{_base_note} ({_dims})"
                            if _compressed and _pe.get("f_col_conflict", False):
                                _new_note += f" #{_compressed}"
                            _found = update_note_by_sn(
                                output_excel_path, _pe["sn"], _new_note,
                                column_map=excel_a_columns if excel_a_columns else None,
                            )
                            if _found:
                                updated_count += 1
                                logger.info(
                                    "Pending 해소: sn='%s' note='%s'", _pe["sn"], _new_note
                                )
                            else:
                                logger.warning(
                                    "Pending SN 미발견 — 유지: sn='%s'", _pe["sn"]
                                )
                                still_pending.append(_pe)
                        else:
                            still_pending.append(_pe)

                    save_pending(config_dir, still_pending)
                    if updated_count:
                        logger.info(
                            "Pending 업데이트: %d건 Note 갱신됨", updated_count
                        )
                except Exception as _pexc:
                    logger.warning("Pending 처리 중 오류 (무시): %s", _pexc)
                # --- Pending 처리 끝 ---

                summary = f"완료: {len(mapped_rows)}건 처리됨"
                if updated_count:
                    summary += f" | 패킹 업데이트: {updated_count}건"
                if conflict_rows:
                    summary += f" | 충돌 제외: {len(conflict_rows)}건"
                    for cr in conflict_rows:
                        logger.warning(cr)
                self._log_queue.put(("DONE", summary))

            except Exception as exc:
                logger.error("오류: %s", exc, exc_info=True)
                self._log_queue.put(("ERROR", str(exc)))
            finally:
                for h in list(logging.getLogger().handlers):
                    if isinstance(h, logging.handlers.QueueHandler):
                        logging.getLogger().removeHandler(h)
        finally:
            pythoncom.CoUninitialize()

    def _poll_log_queue(self) -> None:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                                      datefmt="%H:%M:%S")
        keep_polling: bool = True

        while True:
            try:
                item = self._log_queue.get_nowait()
            except queue.Empty:
                break

            if isinstance(item, logging.LogRecord):
                self._append_log(formatter.format(item) + "\n")
            elif isinstance(item, tuple) and len(item) == 2:
                kind, msg = item[0], item[1]
                if kind == "DONE":
                    self._append_log(f"✓ {msg}\n")
                    self.btn_run.config(state="normal")
                    self.pb_loading.stop()
                    keep_polling = False
                elif kind == "ERROR":
                    self._append_log(f"오류: {msg}\n", tag="error")
                    self.btn_run.config(state="normal")
                    self.pb_loading.stop()
                    keep_polling = False

        if keep_polling and self._polling:
            self.after(100, self._poll_log_queue)
        else:
            self._polling = False

    def _append_log(self, text: str, tag: Optional[str] = None) -> None:
        self.txt_log.config(state="normal")
        if tag:
            self.txt_log.insert(tk.END, text, tag)
        else:
            self.txt_log.insert(tk.END, text)
        self.txt_log.see(tk.END)
        self.txt_log.config(state="disabled")

    def _set_entry(self, entry: ttk.Entry, value: str) -> None:
        if not isinstance(value, str):
            value = str(value) if value is not None else ""
        entry.delete(0, tk.END)
        entry.insert(0, value)


def run_app() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    app = KittingMapperApp()
    app.mainloop()
