"""
main.py -- PO Automation GUI (Tkinter) v3.

email_monitor 백엔드 모듈을 Tkinter GUI 로 래핑합니다.
- 루트 폴더 선택, 기준 날짜/시각 입력, 발신자/수신자 필터 입력.
- 과거 스캔 + 실시간 모니터링.

Python 3.9 호환.
sys._MEIPASS 경로 방어 포함 (PyInstaller EXE 대상).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext
from typing import Optional, Set

# ---------------------------------------------------------------------------
# BASE_DIR: PyInstaller EXE 및 스크립트 모드 공용 경로 방어
# ---------------------------------------------------------------------------
BASE_DIR: Path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


# ---------------------------------------------------------------------------
# 로깅 설정 — 파일(app.log) + 콘솔 핸들러 동시 등록
# ---------------------------------------------------------------------------
def _resolve_log_path() -> Path:
    """EXE 옆(또는 스크립트 옆) app.log 경로를 반환합니다.

    Returns:
        app.log 의 절대 경로 Path.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "app.log"
    return Path(__file__).parent / "app.log"


_log_file_handler = logging.FileHandler(
    _resolve_log_path(), encoding="utf-8", delay=False
)
_log_file_handler.setLevel(logging.INFO)
_log_file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        _log_file_handler,
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 백엔드 임포트 (MVC: 비즈니스 로직은 email_monitor 에서만 import)
# excel_handler, folder_handler 는 이번 재작성 범위 외 — import 금지
# ---------------------------------------------------------------------------
try:
    from email_monitor import (  # noqa: E402
        load_processed_set,
        save_processed_set,
        scan_past_emails,
        start_monitoring,
    )
except ImportError as _import_err:
    logging.critical("email_monitor 임포트 실패: %s", _import_err)
    raise

try:
    from follow_up_tracker import FollowUpTracker  # noqa: E402
except ImportError as _fut_err:
    logging.critical("follow_up_tracker 임포트 실패: %s", _fut_err)
    raise


# ---------------------------------------------------------------------------
# 메인 애플리케이션 클래스
# ---------------------------------------------------------------------------
class POAutomationApp:
    """PO Automation Tkinter 메인 애플리케이션.

    루트 폴더 선택 → 기준 날짜/시각 입력 → 과거 스캔 → 실시간 모니터링.
    """

    def __init__(self, root: tk.Tk) -> None:
        """애플리케이션 초기화 및 위젯 생성.

        Args:
            root: Tkinter 루트 윈도우 객체.
        """
        if root is None:
            raise TypeError("root must not be None")
        if not isinstance(root, tk.Tk):
            raise TypeError(f"root must be tk.Tk, got {type(root).__name__}")

        self._root: tk.Tk = root
        self._root.title("PO Automation v3")
        self._root.geometry("860x640")
        self._root.resizable(True, True)

        # 공유 로그 큐 (백엔드 → UI 메시지 전달)
        self._log_queue: "queue.Queue[str]" = queue.Queue(maxsize=2000)

        # 처리 완료 PO 번호 집합
        self._processed_set: Set[str] = set()

        # 모니터링 스레드 참조
        self._monitor_thread: Optional[threading.Thread] = None

        # 모니터링 중지 이벤트
        self._stop_event: Optional[threading.Event] = None

        # 작업 실행 중 여부 플래그
        self._running: bool = False

        self._build_widgets()
        self._poll_log_queue()
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_settings()

        # FollowUpTracker: 별도 daemon 스레드로 실행 (기존 기능과 독립)
        _tracker_dir: Path = (
            Path(sys.executable).parent
            if getattr(sys, "frozen", False)
            else Path(__file__).parent
        )
        self._follow_up_tracker: FollowUpTracker = FollowUpTracker(
            self._log_queue, _tracker_dir
        )
        self._follow_up_tracker.start()

    # ------------------------------------------------------------------
    # 위젯 생성 (명명 규칙: self.lbl_*, self.btn_*, self.entry_*, self.txt_*)
    # ------------------------------------------------------------------
    def _build_widgets(self) -> None:
        """Tkinter 위젯을 생성하고 배치합니다."""
        pad = {"padx": 10, "pady": 4}

        # --- 루트 폴더 선택 ---
        frm_folder = tk.Frame(self._root)
        frm_folder.pack(fill=tk.X, **pad)

        tk.Label(frm_folder, text="저장 루트 폴더:", width=14, anchor="w").pack(side=tk.LEFT)

        self.entry_root_folder: tk.Entry = tk.Entry(frm_folder, width=50)
        self.entry_root_folder.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_browse: tk.Button = tk.Button(
            frm_folder,
            text="찾아보기",
            command=self._on_browse,
        )
        self.btn_browse.pack(side=tk.LEFT)

        # --- 기준 날짜 ---
        frm_date = tk.Frame(self._root)
        frm_date.pack(fill=tk.X, **pad)

        tk.Label(frm_date, text="기준 날짜 (YYYY-MM-DD):", width=22, anchor="w").pack(side=tk.LEFT)

        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        self.entry_since_date: tk.Entry = tk.Entry(frm_date, width=14)
        self.entry_since_date.insert(0, yesterday)
        self.entry_since_date.pack(side=tk.LEFT, padx=(0, 4))

        # --- 기준 시각 ---
        frm_time = tk.Frame(self._root)
        frm_time.pack(fill=tk.X, **pad)

        tk.Label(frm_time, text="기준 시각 (HH:MM):", width=22, anchor="w").pack(side=tk.LEFT)

        self.entry_since_time: tk.Entry = tk.Entry(frm_time, width=10)
        self.entry_since_time.insert(0, "17:00")
        self.entry_since_time.pack(side=tk.LEFT, padx=(0, 4))

        # --- 발신자 필터 ---
        frm_senders = tk.Frame(self._root)
        frm_senders.pack(fill=tk.X, **pad)

        self.lbl_senders: tk.Label = tk.Label(
            frm_senders,
            text="발신자 필터 (쉼표 구분, 비우면 전체):",
            width=30,
            anchor="w",
        )
        self.lbl_senders.pack(side=tk.LEFT)

        self.entry_senders: tk.Entry = tk.Entry(frm_senders, width=60)
        self.entry_senders.insert(
            0,
            "affidah.amin@imi-critical.com, angeline.lim@imi-critical.com, oscar.pozos@imi-critical.com",
        )
        self.entry_senders.pack(side=tk.LEFT, padx=(0, 4))

        # --- 수신자 필터 ---
        frm_recipients = tk.Frame(self._root)
        frm_recipients.pack(fill=tk.X, **pad)

        self.lbl_recipients: tk.Label = tk.Label(
            frm_recipients,
            text="수신자 필터 (쉼표 구분, 비우면 전체):",
            width=30,
            anchor="w",
        )
        self.lbl_recipients.pack(side=tk.LEFT)

        self.entry_recipients: tk.Entry = tk.Entry(frm_recipients, width=60)
        # 기본값: 빈 문자열 (전체 허용) — 음수 개념 없음, 빈 입력은 정상 상태
        self.entry_recipients.insert(0, "")
        self.entry_recipients.pack(side=tk.LEFT, padx=(0, 4))

        # --- 시작 버튼 ---
        frm_btn = tk.Frame(self._root)
        frm_btn.pack(fill=tk.X, **pad)

        self.btn_start: tk.Button = tk.Button(
            frm_btn,
            text="스캔 & 모니터링 시작",
            font=("Malgun Gothic", 10, "bold"),
            width=24,
            command=self._on_start,
        )
        self.btn_start.pack(anchor="w")

        self.btn_stop: tk.Button = tk.Button(
            frm_btn,
            text="모니터링 중지",
            font=("Malgun Gothic", 10, "bold"),
            width=24,
            state=tk.DISABLED,
            command=self._on_stop,
        )
        self.btn_stop.pack(anchor="w", pady=(4, 0))

        # --- 상태 라벨 ---
        frm_status = tk.Frame(self._root)
        frm_status.pack(fill=tk.X, padx=10, pady=(0, 2))

        self.lbl_status: tk.Label = tk.Label(
            frm_status,
            text="대기 중",
            font=("Malgun Gothic", 10),
            anchor="w",
            fg="#333333",
        )
        self.lbl_status.pack(fill=tk.X)

        # --- 로그 창 ---
        frm_log = tk.Frame(self._root)
        frm_log.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        tk.Label(frm_log, text="실행 로그", font=("Malgun Gothic", 9), anchor="w").pack(fill=tk.X)

        self.txt_log: scrolledtext.ScrolledText = scrolledtext.ScrolledText(
            frm_log,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 9),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
        )
        self.txt_log.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # 버튼 핸들러: 폴더 선택
    # ------------------------------------------------------------------
    def _on_browse(self) -> None:
        """폴더 선택 대화상자를 열어 entry_root_folder 에 경로를 입력합니다."""
        folder = filedialog.askdirectory(title="저장 루트 폴더 선택")
        if folder:
            self.entry_root_folder.delete(0, tk.END)
            self.entry_root_folder.insert(0, folder)

    # ------------------------------------------------------------------
    # 버튼 핸들러: 스캔 & 모니터링 시작
    # ------------------------------------------------------------------
    def _on_start(self) -> None:
        """'스캔 & 모니터링 시작' 버튼 클릭 핸들러.

        1. root_folder 존재 검증
        2. since_date + since_time 파싱
        3. load_processed_set() 호출
        4. 별도 스레드에서 scan_past_emails() 실행
        5. 스캔 완료 콜백에서 start_monitoring() 호출
        6. btn_start 비활성화
        """
        if self._running:
            messagebox.showinfo("알림", "이미 실행 중입니다.")
            return

        # ① root_folder 검증
        folder_str: str = self.entry_since_date.master.tk.eval(
            f"set dummy [{self.entry_root_folder._w} get]"
        ) if False else self.entry_root_folder.get().strip()

        if not folder_str:
            messagebox.showwarning("경고", "저장 루트 폴더를 선택해 주세요.")
            return

        root_folder = Path(folder_str)
        if not root_folder.exists():
            messagebox.showwarning("경고", f"폴더가 존재하지 않습니다:\n{root_folder}")
            return

        # ② since_dt 파싱
        date_str: str = self.entry_since_date.get().strip()
        time_str: str = self.entry_since_time.get().strip()
        try:
            since_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        except ValueError as exc:
            messagebox.showerror("오류", f"날짜/시각 형식 오류:\n{exc}\n형식: YYYY-MM-DD HH:MM")
            return

        # ③ 발신자/수신자 필터 파싱
        def _parse_email_set(raw: str) -> Set[str]:
            """쉼표 구분 이메일 문자열을 소문자 set 으로 변환.

            Args:
                raw: 쉼표로 구분된 이메일 주소 문자열 (빈 문자열 허용).

            Returns:
                소문자 이메일 주소 집합. 빈 문자열이면 빈 set 반환.
            """
            if not raw.strip():
                return set()
            parts: Set[str] = set()
            for part in raw.split(","):
                part = part.strip().lower()
                if part:
                    parts.add(part)
            return parts

        senders_raw: str = self.entry_senders.get()
        recipients_raw: str = self.entry_recipients.get()
        allowed_senders: Set[str] = _parse_email_set(senders_raw)
        allowed_recipients: Set[str] = _parse_email_set(recipients_raw)

        # ④ processed_set 로드 (base_dir = root_folder)
        try:
            self._processed_set = load_processed_set(root_folder)
            self._append_log(f"[초기화] 처리 완료 목록 로드: {len(self._processed_set)}건")
        except Exception as exc:
            self._append_log(f"[경고] processed_set 로드 실패 (빈 set 사용): {exc}")
            self._processed_set = set()

        # ⑥ 버튼 비활성화
        self.btn_start.config(state=tk.DISABLED)
        self._running = True
        self._stop_event = threading.Event()
        self._set_status("과거 이메일 스캔 중...", color="#005588")
        self._append_log(
            f"[시작] 루트 폴더: {root_folder} | 기준: {since_dt.strftime('%Y-%m-%d %H:%M')} "
            f"| 발신자: {allowed_senders or '전체'} | 수신자: {allowed_recipients or '전체'}"
        )
        logger.info(
            "스캔 시작: root=%s, since=%s, senders=%s, recipients=%s",
            root_folder, since_dt, allowed_senders, allowed_recipients,
        )

        # ⑤ 별도 스레드에서 scan_past_emails() 실행
        def _scan_worker() -> None:
            """과거 스캔 워커 (daemon 스레드)."""
            try:
                count = scan_past_emails(
                    root_folder,
                    since_dt,
                    self._log_queue,
                    self._processed_set,
                    allowed_senders=allowed_senders,
                    allowed_recipients=allowed_recipients,
                )
                # processed_set 영속화
                try:
                    save_processed_set(root_folder, self._processed_set)
                except Exception as save_exc:
                    logger.error("processed_set 저장 실패: %s", save_exc)
                    try:
                        self._log_queue.put_nowait(f"[경고] processed_set 저장 실패: {save_exc}")
                    except queue.Full:
                        pass
                # 스캔 완료 콜백에서 start_monitoring() 호출
                self._root.after(
                    0, self._on_scan_complete, root_folder, count,
                    allowed_senders, allowed_recipients, self._stop_event,
                )
            except Exception as exc:
                logger.error("스캔 워커 오류: %s", exc)
                try:
                    self._log_queue.put_nowait(f"[오류] 스캔 중 오류: {exc}")
                except queue.Full:
                    pass
                self._root.after(0, self._on_scan_error, str(exc))

        scan_thread = threading.Thread(target=_scan_worker, daemon=True, name="ScanWorker")
        scan_thread.start()

    # ------------------------------------------------------------------
    # 스캔 완료 콜백 (메인 스레드 — root.after() 로 위임)
    # ------------------------------------------------------------------
    def _on_scan_complete(
        self,
        root_folder: Path,
        count: int,
        allowed_senders: Optional[Set[str]] = None,
        allowed_recipients: Optional[Set[str]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        """과거 스캔 완료 후 실시간 모니터링을 시작합니다.

        Args:
            root_folder: PO 폴더 루트 Path.
            count: 스캔에서 처리된 메일 수 (0 이상).
            allowed_senders: 허용 발신자 이메일 집합 (None = 전체 허용).
            allowed_recipients: 허용 수신자 이메일 집합 (None = 전체 허용).
            stop_event: 모니터링 루프 중지 신호 Event (None = 내부 생성).
        """
        # count 는 0 이상 허용 (0 = 처리 대상 없음, 정상)
        self._append_log(f"[스캔 완료] {count}건 처리")
        self._set_status("실시간 모니터링 중...", color="#007700")

        try:
            self._monitor_thread = start_monitoring(
                root_folder,
                self._log_queue,
                self._processed_set,
                allowed_senders=allowed_senders,
                allowed_recipients=allowed_recipients,
                stop_event=stop_event,
            )
            self._append_log("[모니터] Outlook 실시간 감시 시작")
            logger.info("실시간 모니터링 스레드 시작")
            self.btn_stop.config(state=tk.NORMAL)
        except Exception as exc:
            logger.error("모니터링 시작 실패: %s", exc)
            self._append_log(f"[오류] 모니터링 시작 실패: {exc}")
            self._set_status(f"오류: {exc}", color="#cc0000")
            self.btn_start.config(state=tk.NORMAL)
            self._running = False

    def _on_stop(self) -> None:
        """'모니터링 중지' 버튼 클릭 핸들러."""
        if self._stop_event is not None:
            self._stop_event.set()
        self._running = False
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self._set_status(
            "모니터링 중지됨. 재시작하려면 '스캔 & 모니터링 시작'을 클릭하세요.",
            color="#884400",
        )
        self._append_log("[중지] 모니터링 중지 요청 전송됨")
        logger.info("모니터링 중지 요청: stop_event.set() 호출")

    def _on_scan_error(self, error_msg: str) -> None:
        """스캔 실패 콜백 (메인 스레드).

        Args:
            error_msg: 오류 메시지 문자열.
        """
        self._set_status(f"오류: {error_msg}", color="#cc0000")
        messagebox.showerror("오류", f"과거 스캔 중 오류:\n{error_msg}")
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)
        self._running = False

    # ------------------------------------------------------------------
    # 로그 큐 폴링 (100ms 주기 — Async-Bridge)
    # ------------------------------------------------------------------
    def _poll_log_queue(self) -> None:
        """log_queue 를 100ms 주기로 폴링하여 로그 창에 메시지를 출력합니다.

        Async-Bridge: queue.Queue 를 통해 백그라운드 스레드 메시지를
        메인 UI 스레드(root.after) 에서 안전하게 처리합니다.
        """
        try:
            while True:
                message: str = self._log_queue.get_nowait()
                self._append_log(message)
        except queue.Empty:
            pass
        finally:
            self._root.after(100, self._poll_log_queue)

    # ------------------------------------------------------------------
    # 설정 영속화 — JSON 저장/로드
    # ------------------------------------------------------------------
    def _settings_path(self) -> Path:
        """설정 파일(EmailMonitor_settings.json) 의 절대 경로를 반환합니다.

        EXE 모드에서는 실행 파일 옆, 스크립트 모드에서는 __file__ 옆에 저장합니다.

        Returns:
            설정 파일 Path 객체.
        """
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path(__file__).parent
        return base / "EmailMonitor_settings.json"

    def _load_settings(self) -> None:
        """설정 파일이 존재하면 읽어 각 entry 위젯에 값을 복원합니다.

        파일 미존재 또는 파싱 오류는 경고 로그만 남기고 무시합니다.
        """
        p = self._settings_path()
        if not p.exists():
            return
        try:
            raw = None
            for enc in ("utf-8", "cp949", "latin-1"):
                try:
                    raw = p.read_text(encoding=enc)
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            if raw is None:
                logger.warning("설정 파일 인코딩 감지 실패, 기본값 사용")
                return
            data = json.loads(raw)
            mapping = [
                ("root_folder", self.entry_root_folder),
                ("since_date", self.entry_since_date),
                ("since_time", self.entry_since_time),
                ("senders", self.entry_senders),
                ("recipients", self.entry_recipients),
            ]
            for key, entry in mapping:
                val = data.get(key, "")
                if val:
                    entry.delete(0, tk.END)
                    entry.insert(0, val)
            logger.info("설정 로드: %s", p)
        except Exception as exc:
            logger.warning("설정 로드 실패: %s", exc)

    def _save_settings(self) -> None:
        """현재 entry 위젯 값을 설정 파일(JSON)에 저장합니다.

        저장 실패 시 경고 로그만 남기고 무시합니다.
        """
        data = {
            "root_folder": self.entry_root_folder.get().strip(),
            "since_date": self.entry_since_date.get().strip(),
            "since_time": self.entry_since_time.get().strip(),
            "senders": self.entry_senders.get().strip(),
            "recipients": self.entry_recipients.get().strip(),
        }
        try:
            p = self._settings_path()
            content = json.dumps(data, ensure_ascii=False, indent=2)
            import tempfile
            fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp, str(p))
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            logger.info("설정 저장: %s", p)
        except Exception as exc:
            logger.warning("설정 저장 실패: %s", exc)

    def _on_close(self) -> None:
        """WM_DELETE_WINDOW 핸들러 — 설정을 저장하고 윈도우를 닫습니다."""
        self._follow_up_tracker.stop()
        self._save_settings()
        self._root.destroy()

    # ------------------------------------------------------------------
    # 헬퍼: 상태 라벨 업데이트
    # ------------------------------------------------------------------
    def _set_status(self, text: str, color: str = "#333333") -> None:
        """상태 라벨 텍스트와 색상을 업데이트합니다.

        Args:
            text: 표시할 상태 메시지 문자열.
            color: 텍스트 색상 hex 코드 (기본: #333333).

        Raises:
            TypeError: text 또는 color 가 None 이거나 str 이 아닐 때.
        """
        if text is None:
            raise TypeError("text must not be None")
        if not isinstance(text, str):
            raise TypeError(f"text must be str, got {type(text).__name__}")
        if color is None:
            raise TypeError("color must not be None")
        if not isinstance(color, str):
            raise TypeError(f"color must be str, got {type(color).__name__}")

        self.lbl_status.config(text=text, fg=color)
        logger.info("상태 업데이트: %s", text)

    # ------------------------------------------------------------------
    # 헬퍼: 로그 창에 메시지 추가
    # ------------------------------------------------------------------
    def _append_log(self, message: str) -> None:
        """로그 텍스트 창에 메시지를 한 줄 추가합니다.

        Args:
            message: 로그 창에 출력할 메시지 (str 강제 변환).
        """
        if not isinstance(message, str):
            message = str(message)

        self.txt_log.config(state=tk.NORMAL)
        self.txt_log.insert(tk.END, message + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state=tk.DISABLED)
        logger.info("로그 출력: %s", message)


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------
def main() -> None:
    """애플리케이션 진입점."""
    root = tk.Tk()
    app = POAutomationApp(root)
    _ = app  # GC 방지
    root.mainloop()


if __name__ == "__main__":
    main()
