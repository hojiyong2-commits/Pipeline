"""
follow_up_tracker.py -- Sent Items follow-up 추적 모듈.

보낸편지함(Sent Items)에서 "Please advise us accordingly"가 포함된 메일을
추적 대상으로 등록하고, 72시간+ 경과 후 같은 ConversationID로 답장이 없으면
원본 메일에 UnRead = True 처리합니다.

별도 daemon 스레드로 실행되며 기존 EmailMonitor 코드와 완전히 독립됩니다.

실행 주기:
- 앱 시작 시 즉시 1회 스캔
- 이후 3600초(1시간) 간격으로 반복

상태 영속화:
- follow_up_tracker.json (tracker_dir 내)
  스키마: {"items": [{"entry_id": ..., "sent_time": ISO8601, "conversation_id": ...,
                       "subject": ..., "status": "pending|replied|marked"}]}
"""

import json
import logging
import os
import queue
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 추적 키워드 (대소문자 무관 비교)
_FOLLOWUP_KEYWORD: str = "please advise us accordingly"

# 72시간 임계값
_FOLLOWUP_HOURS: int = 72

# JSON 파일명
_TRACKER_FILENAME: str = "follow_up_tracker.json"


# ---------------------------------------------------------------------------
# 헬퍼: 타임스탬프 변환
# ---------------------------------------------------------------------------

def _to_utc_naive(dt_obj: object) -> Optional[datetime]:
    """win32com pywintypes.datetime 또는 일반 datetime을 naive UTC datetime으로 변환.

    Args:
        dt_obj: pywintypes.datetime 또는 datetime 객체, None 허용.
                # ② 경계값: None이면 None 반환 허용 (호출자가 처리)

    Returns:
        naive datetime 객체 (tzinfo 제거), 변환 실패 시 None.
    """
    # ① None 방어
    if dt_obj is None:
        return None

    try:
        if hasattr(dt_obj, "replace"):
            # ② pywintypes.datetime은 datetime 서브클래스 — tzinfo 제거로 naive 변환
            return dt_obj.replace(tzinfo=None)
        # 속성으로 datetime 재구성 (fallback)
        return datetime(
            dt_obj.year, dt_obj.month, dt_obj.day,
            dt_obj.hour, dt_obj.minute, dt_obj.second,
        )
    except Exception as exc:
        logger.warning("datetime 변환 실패: %s", exc)
        return None


# ---------------------------------------------------------------------------
# FollowUpTracker 클래스
# ---------------------------------------------------------------------------

class FollowUpTracker:
    """Sent Items follow-up 추적기.

    "Please advise us accordingly"가 포함된 보낸 메일을 등록하고,
    72시간+ 경과 후 답장이 없으면 UnRead = True 처리합니다.

    별도 daemon 스레드로 실행됩니다. 기존 EmailMonitor와 완전히 독립됩니다.
    """

    def __init__(self, log_queue: "queue.Queue[str]", tracker_dir: Path) -> None:
        """FollowUpTracker 초기화.

        Args:
            log_queue: GUI 로그 전달용 Queue (POAutomationApp._log_queue 공유).
            tracker_dir: follow_up_tracker.json 저장 위치 절대 경로.
                         # ③ Path 타입 강제, str 금지
                         # ② 경계값: 빈 Path는 ValueError 발생 (아래 검증)

        Raises:
            TypeError: log_queue가 None이거나 queue.Queue가 아닐 때.
            TypeError: tracker_dir가 None이거나 Path가 아닐 때.
            ValueError: tracker_dir가 절대 경로가 아닐 때.
        """
        # ① None 방어 + ③ isinstance 체크 (log_queue)
        if log_queue is None:
            raise TypeError("log_queue must not be None")
        if not isinstance(log_queue, queue.Queue):
            raise TypeError(
                f"log_queue must be queue.Queue, got {type(log_queue).__name__}"
            )

        # ① None 방어 + ③ isinstance 체크 (tracker_dir)
        if tracker_dir is None:
            raise TypeError("tracker_dir must not be None")
        if not isinstance(tracker_dir, Path):
            raise TypeError(
                f"tracker_dir must be Path, got {type(tracker_dir).__name__}"
            )
        # FS③: 절대 경로 강제 (경로 탈출 방지)
        if not tracker_dir.is_absolute():
            raise ValueError(
                f"tracker_dir must be an absolute path, got: {tracker_dir}"
            )

        self._log_queue: "queue.Queue[str]" = log_queue
        self._tracker_dir: Path = tracker_dir
        self._tracker_path: Path = tracker_dir / _TRACKER_FILENAME

        self._stop_event: threading.Event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # 공개 인터페이스
    # ------------------------------------------------------------------

    def start(self) -> None:
        """daemon 스레드를 시작합니다. 중복 호출 시 경고 후 무시."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("FollowUpTracker: 이미 실행 중 — start() 무시")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="FollowUpTracker",
        )
        self._thread.start()
        logger.info("FollowUpTracker 스레드 시작: %s", self._thread.name)

    def stop(self) -> None:
        """스레드 중지 신호를 보냅니다. 스레드 종료를 기다리지 않습니다."""
        self._stop_event.set()
        logger.info("FollowUpTracker 중지 신호 전송")

    # ------------------------------------------------------------------
    # 내부 루프
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """follow-up 추적 메인 루프 (daemon 스레드 내 실행).

        앱 시작 시 즉시 1회 스캔, 이후 3600초 간격으로 반복.
        # ④ _FOLLOWUP_HOURS(72), scan interval(3600): 양수 상수 — 음수/0 불가 (로직상 양수 보장)
        """
        self._log("[FollowUp] 추적 스레드 시작")
        logger.info("FollowUpTracker 루프 진입")

        while not self._stop_event.is_set():
            try:
                self._scan_sent_items()
            except Exception as exc:
                logger.error("FollowUpTracker 스캔 오류: %s", exc)
                self._log(f"[FollowUp 오류] 스캔 중 예외 발생: {exc}")

            # 3600초 대기 또는 stop_event 신호 수신
            # ④ 3600: 양수 초 값, 0이나 음수면 즉시 반환하므로 양수 보장 필요
            # 여기서는 상수로 고정 — 런타임 변경 없음
            stopped = self._stop_event.wait(3600)
            if stopped:
                break

        self._log("[FollowUp] 추적 스레드 종료")
        logger.info("FollowUpTracker 루프 정상 종료")

    # ------------------------------------------------------------------
    # Outlook 스캔
    # ------------------------------------------------------------------

    def _scan_sent_items(self) -> None:
        """Outlook Sent Items를 스캔하여 추적 등록 및 follow-up 처리를 수행합니다.

        pythoncom.CoInitialize() / CoUninitialize() 쌍을 보장합니다.
        """
        try:
            import pythoncom  # type: ignore[import]
            import win32com.client  # type: ignore[import]
        except ImportError as exc:
            logger.error("win32com/pythoncom import 실패: %s", exc)
            self._log(f"[FollowUp 오류] win32com import 실패: {exc}")
            return

        # 트래커 데이터 로드
        tracker_data: Dict = self._load_tracker()
        items_list: List[Dict] = tracker_data.get("items", [])
        # ② 빈 리스트는 정상 상태 (처음 실행 시 항목 없음)

        pythoncom.CoInitialize()
        try:
            try:
                outlook = win32com.client.Dispatch("Outlook.Application")
                namespace = outlook.GetNamespace("MAPI")

                # olFolderSentMail = 5
                sent_folder = namespace.GetDefaultFolder(5)
                sent_items = sent_folder.Items

                self._log("[FollowUp] Sent Items 스캔 시작")
                logger.info("FollowUpTracker: Sent Items 스캔 시작")

                new_registrations: int = 0
                item = sent_items.GetFirst()
                while item is not None:
                    try:
                        # MailItem 여부 확인 (Class == 43 = olMail)
                        item_class = getattr(item, "Class", None)
                        if item_class != 43:
                            item = sent_items.GetNext()
                            continue

                        if self._should_track(item):
                            entry_id: str = (getattr(item, "EntryID", "") or "")
                            # ① entry_id None/빈 문자열 방어
                            if not entry_id:
                                item = sent_items.GetNext()
                                continue

                            sent_time_raw = getattr(item, "SentOn", None)
                            sent_dt: Optional[datetime] = _to_utc_naive(sent_time_raw)
                            if sent_dt is None:
                                item = sent_items.GetNext()
                                continue

                            sent_time_iso: str = sent_dt.strftime("%Y-%m-%dT%H:%M:%S")
                            conv_id: str = (getattr(item, "ConversationID", "") or "")
                            subject: str = (getattr(item, "Subject", "") or "")

                            registered = self._register_item(
                                items_list=items_list,
                                entry_id=entry_id,
                                sent_time_iso=sent_time_iso,
                                conversation_id=conv_id,
                                subject=subject,
                            )
                            if registered:
                                new_registrations += 1

                    except Exception as item_exc:
                        logger.error("FollowUpTracker: 항목 처리 오류: %s", item_exc)

                    item = sent_items.GetNext()

                self._log(
                    f"[FollowUp] 신규 등록: {new_registrations}건"
                )
                logger.info("FollowUpTracker: 신규 등록 %d건", new_registrations)

                # ② pending 항목이 없어도 정상 (빈 리스트 방어)
                pending_items = [
                    it for it in items_list if it.get("status") == "pending"
                ]
                checked: int = 0
                for tracked in pending_items:
                    try:
                        changed = self._check_followup(
                            namespace=namespace,
                            entry_id=tracked.get("entry_id", ""),
                            sent_time_iso=tracked.get("sent_time", ""),
                            conversation_id=tracked.get("conversation_id", ""),
                            subject=tracked.get("subject", ""),
                            items_list=items_list,
                        )
                        if changed:
                            checked += 1
                    except Exception as chk_exc:
                        logger.error(
                            "FollowUpTracker: follow-up 체크 오류 (entry_id=%s): %s",
                            tracked.get("entry_id", "?"), chk_exc,
                        )

                if checked > 0:
                    self._log(f"[FollowUp] UnRead 처리: {checked}건")

                # 변경 사항 저장
                tracker_data["items"] = items_list
                self._save_tracker(tracker_data)

            except Exception as scan_exc:
                logger.error("FollowUpTracker: Outlook 스캔 오류: %s", scan_exc)
                self._log(f"[FollowUp 오류] Outlook 스캔 실패: {scan_exc}")

        finally:
            pythoncom.CoUninitialize()
            logger.info("FollowUpTracker: COM 해제 완료")

    # ------------------------------------------------------------------
    # 추적 여부 판단
    # ------------------------------------------------------------------

    def _should_track(self, mail_item: object) -> bool:
        """메일 본문에 추적 키워드가 포함되어 있는지 검사합니다.

        Body 또는 HTMLBody에 "Please advise us accordingly"가 포함되면 True.

        Args:
            mail_item: win32com MailItem 객체.
                       # ① None 방어 필요

        Returns:
            추적 대상이면 True, 아니면 False.
            # ② 빈 본문은 유효한 입력 — 키워드 없음으로 False 반환

        Raises:
            TypeError: mail_item이 None이면 raise.
        """
        # ① None 방어
        if mail_item is None:
            raise TypeError("mail_item must not be None")
        # ③ isinstance: win32com COM 객체는 정적 타입 불가 — object 타입 허용
        # ④ 수치형 파라미터 없음

        body: str = ""
        try:
            body = (getattr(mail_item, "Body", "") or "")
        except Exception:
            body = ""

        html_body: str = ""
        try:
            html_body = (getattr(mail_item, "HTMLBody", "") or "")
        except Exception:
            html_body = ""

        # ② 빈 문자열은 정상 — 키워드 없음으로 False 반환
        combined: str = (body + "\n" + html_body).lower()
        return _FOLLOWUP_KEYWORD in combined

    # ------------------------------------------------------------------
    # 항목 등록
    # ------------------------------------------------------------------

    def _register_item(
        self,
        items_list: List[Dict],
        entry_id: str,
        sent_time_iso: str,
        conversation_id: str,
        subject: str,
    ) -> bool:
        """추적 목록에 새 항목을 등록합니다. 중복이면 스킵합니다.

        Args:
            items_list: 현재 추적 항목 리스트 (변경됨).
            entry_id: Outlook EntryID 문자열.
                      # ① None 방어 + ② 빈 문자열 거부 (식별자)
            sent_time_iso: 발송 시각 ISO8601 문자열.
            conversation_id: Outlook ConversationID 문자열.
            subject: 이메일 제목.

        Returns:
            새로 등록되면 True, 중복이면 False.

        Raises:
            TypeError: items_list가 None이거나 list가 아닐 때.
            TypeError: entry_id가 None이거나 str가 아닐 때.
            ValueError: entry_id가 빈 문자열일 때.
        """
        # ① None 방어 + ③ isinstance (items_list)
        if items_list is None:
            raise TypeError("items_list must not be None")
        if not isinstance(items_list, list):
            raise TypeError(
                f"items_list must be list, got {type(items_list).__name__}"
            )

        # ① None 방어 + ③ isinstance (entry_id)
        if entry_id is None:
            raise TypeError("entry_id must not be None")
        if not isinstance(entry_id, str):
            raise TypeError(
                f"entry_id must be str, got {type(entry_id).__name__}"
            )
        # ② 빈 문자열은 식별자로 부적합 → ValueError
        if not entry_id.strip():
            raise ValueError("entry_id must not be empty")

        # ③ isinstance (sent_time_iso)
        if sent_time_iso is None:
            raise TypeError("sent_time_iso must not be None")
        if not isinstance(sent_time_iso, str):
            raise TypeError(
                f"sent_time_iso must be str, got {type(sent_time_iso).__name__}"
            )

        # ③ isinstance (conversation_id, subject) — None은 빈 문자열로 허용
        if conversation_id is None:
            conversation_id = ""
        if subject is None:
            subject = ""

        # 중복 체크
        for existing in items_list:
            if existing.get("entry_id") == entry_id:
                logger.debug(
                    "FollowUpTracker: 이미 등록된 항목 스킵: entry_id=%s", entry_id
                )
                return False

        new_item: Dict = {
            "entry_id": entry_id,
            "sent_time": sent_time_iso,
            "conversation_id": conversation_id,
            "subject": subject,
            "status": "pending",
        }
        items_list.append(new_item)
        logger.info(
            "FollowUpTracker: 신규 등록 — subject=%s | sent=%s",
            subject, sent_time_iso,
        )
        self._log(f"[FollowUp 등록] {subject} (발송: {sent_time_iso})")
        return True

    # ------------------------------------------------------------------
    # Follow-up 체크
    # ------------------------------------------------------------------

    def _check_followup(
        self,
        namespace: object,
        entry_id: str,
        sent_time_iso: str,
        conversation_id: str,
        subject: str,
        items_list: List[Dict],
    ) -> bool:
        """72시간 경과 + 답장 없음 여부를 확인하고 처리합니다.

        Args:
            namespace: win32com MAPI Namespace 객체.
            entry_id: 원본 메일 EntryID.
            sent_time_iso: 발송 시각 ISO8601 문자열.
            conversation_id: ConversationID.
            subject: 이메일 제목 (로그용).
            items_list: 추적 항목 리스트 (status 업데이트).

        Returns:
            상태가 변경(marked 또는 replied)되면 True, 아니면 False.

        Raises:
            TypeError: namespace 또는 items_list가 None일 때.
        """
        # ① None 방어
        if namespace is None:
            raise TypeError("namespace must not be None")
        if items_list is None:
            raise TypeError("items_list must not be None")

        # ① entry_id None 방어
        if not entry_id or not isinstance(entry_id, str):
            return False

        # sent_time 파싱
        # ② 빈 문자열이면 파싱 불가 → 스킵
        if not sent_time_iso:
            return False

        try:
            sent_dt: datetime = datetime.strptime(sent_time_iso, "%Y-%m-%dT%H:%M:%S")
        except ValueError as exc:
            logger.warning(
                "FollowUpTracker: sent_time 파싱 실패 (entry_id=%s): %s", entry_id, exc
            )
            return False

        now_utc: datetime = datetime.utcnow()
        elapsed: timedelta = now_utc - sent_dt

        # ④ timedelta(hours=72): 양수 상수 — 72시간 경계값, 0 이하면 즉시 해당하여 의미 없음
        # 72시간 미경과 → 스킵
        if elapsed < timedelta(hours=_FOLLOWUP_HOURS):
            return False

        # 72시간 경과 → 답장 존재 여부 확인
        has_reply: bool = self._has_reply(
            namespace=namespace,
            conversation_id=conversation_id,
            sent_dt=sent_dt,
        )

        # items_list에서 해당 entry_id 찾기
        target_item: Optional[Dict] = None
        for it in items_list:
            if it.get("entry_id") == entry_id:
                target_item = it
                break

        if target_item is None:
            logger.warning(
                "FollowUpTracker: items_list에서 entry_id 미발견: %s", entry_id
            )
            return False

        if has_reply:
            target_item["status"] = "replied"
            logger.info(
                "FollowUpTracker: 답장 확인 — status=replied | subject=%s", subject
            )
            self._log(f"[FollowUp 완료] 답장 확인: {subject}")
            return True

        # 답장 없음 → UnRead = True + Save()
        try:
            mail_item = namespace.GetItemFromID(entry_id)
            if mail_item is not None:
                mail_item.UnRead = True
                mail_item.Save()
                logger.info(
                    "FollowUpTracker: UnRead 처리 완료 — subject=%s", subject
                )
                self._log(f"[FollowUp] 읽지않음 처리: {subject}")
        except Exception as exc:
            logger.error(
                "FollowUpTracker: UnRead 처리 실패 (entry_id=%s): %s", entry_id, exc
            )

        target_item["status"] = "marked"
        return True

    # ------------------------------------------------------------------
    # 답장 존재 여부 확인
    # ------------------------------------------------------------------

    def _has_reply(
        self,
        namespace: object,
        conversation_id: str,
        sent_dt: datetime,
    ) -> bool:
        """Inbox와 Sent Items에서 동일 ConversationID로 sent_dt 이후 메일이 있는지 확인합니다.

        Args:
            namespace: win32com MAPI Namespace 객체.
            conversation_id: 원본 메일 ConversationID.
                             # ② 빈 문자열이면 답장 탐지 불가 → False 반환
            sent_dt: 원본 메일 발송 시각 (naive datetime).

        Returns:
            답장이 존재하면 True, 없으면 False.

        Raises:
            TypeError: namespace 또는 sent_dt가 None일 때.
        """
        # ① None 방어
        if namespace is None:
            raise TypeError("namespace must not be None")
        if sent_dt is None:
            raise TypeError("sent_dt must not be None")
        if not isinstance(sent_dt, datetime):
            raise TypeError(
                f"sent_dt must be datetime, got {type(sent_dt).__name__}"
            )

        # ② 빈 conversation_id는 탐지 불가
        if not conversation_id or not isinstance(conversation_id, str):
            logger.warning("FollowUpTracker: conversation_id 없음 — 답장 탐지 불가")
            return False

        conv_id_lower: str = conversation_id.lower()

        # 검색 대상 폴더: Inbox(6) + Sent Items(5)
        folder_ids: List[int] = [6, 5]

        for folder_id in folder_ids:
            try:
                folder = namespace.GetDefaultFolder(folder_id)
                items = folder.Items
                mail = items.GetFirst()
                while mail is not None:
                    try:
                        # ConversationID 비교
                        mail_conv: str = (
                            getattr(mail, "ConversationID", "") or ""
                        ).lower()
                        if mail_conv != conv_id_lower:
                            mail = items.GetNext()
                            continue

                        # 원본보다 나중에 발송/수신된 메일인지 확인
                        # Inbox: ReceivedTime / Sent: SentOn
                        time_attr: str = "ReceivedTime" if folder_id == 6 else "SentOn"
                        mail_time_raw = getattr(mail, time_attr, None)
                        mail_dt: Optional[datetime] = _to_utc_naive(mail_time_raw)

                        if mail_dt is not None and mail_dt > sent_dt:
                            logger.info(
                                "FollowUpTracker: 답장 발견 (folder=%d, time=%s)",
                                folder_id, mail_dt,
                            )
                            return True

                    except Exception as mail_exc:
                        logger.debug(
                            "FollowUpTracker: 메일 항목 비교 오류 (folder=%d): %s",
                            folder_id, mail_exc,
                        )

                    mail = items.GetNext()

            except Exception as folder_exc:
                logger.error(
                    "FollowUpTracker: 폴더(%d) 접근 오류: %s", folder_id, folder_exc
                )

        return False

    # ------------------------------------------------------------------
    # 영속화: 로드 / 저장
    # ------------------------------------------------------------------

    def _load_tracker(self) -> Dict:
        """follow_up_tracker.json을 로드합니다.

        FS②: utf-8 → cp949 → latin-1 다중 fallback.

        Returns:
            tracker dict. 파일 없거나 파싱 실패 시 {"items": []} 반환.
        """
        if not self._tracker_path.exists():
            logger.info(
                "FollowUpTracker: tracker 파일 없음, 빈 dict 반환: %s",
                self._tracker_path,
            )
            return {"items": []}

        # FS②: utf-8 → cp949 → latin-1 다중 fallback
        for enc in ("utf-8", "cp949", "latin-1"):
            try:
                text: str = self._tracker_path.read_text(encoding=enc)
                data: Dict = json.loads(text)
                if not isinstance(data, dict):
                    logger.warning(
                        "FollowUpTracker: tracker JSON 형식 오류 (dict 아님), 빈 dict 반환"
                    )
                    return {"items": []}
                # items 키 없으면 초기화
                if "items" not in data:
                    data["items"] = []
                logger.info(
                    "FollowUpTracker: tracker 로드 완료 (enc=%s): %d 항목",
                    enc, len(data["items"]),
                )
                return data
            except (UnicodeDecodeError, LookupError):
                continue
            except json.JSONDecodeError as exc:
                logger.error(
                    "FollowUpTracker: tracker JSON 파싱 실패: %s", exc
                )
                return {"items": []}

        logger.error(
            "FollowUpTracker: tracker 모든 인코딩 실패: %s", self._tracker_path
        )
        return {"items": []}

    def _save_tracker(self, data: Dict) -> None:
        """follow_up_tracker.json을 원자적으로 저장합니다.

        FS①: tempfile + os.replace() 원자적 쓰기 패턴.

        Args:
            data: 저장할 tracker dict.
                  # ① None 방어 + ③ isinstance 체크

        Raises:
            TypeError: data가 None이거나 dict가 아닐 때.
            RuntimeError: 파일 저장 실패 시 원인 체이닝.
        """
        # ① None 방어
        if data is None:
            raise TypeError("data must not be None")
        # ③ isinstance 체크
        if not isinstance(data, dict):
            raise TypeError(
                f"data must be dict, got {type(data).__name__}"
            )
        # ② 빈 dict는 유효한 입력 (초기 상태)

        # FS③: tracker_dir 존재 확인 및 생성
        try:
            self._tracker_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"tracker_dir 생성 실패: {self._tracker_dir}"
            ) from exc

        # FS①: 원자적 쓰기 — tempfile + os.replace()
        tmp_path: Optional[Path] = None
        try:
            fd, tmp_str = tempfile.mkstemp(
                dir=str(self._tracker_dir), suffix=".tmp"
            )
            tmp_path = Path(tmp_str)
            content: str = json.dumps(data, ensure_ascii=False, indent=2)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(str(tmp_path), str(self._tracker_path))
            logger.info(
                "FollowUpTracker: tracker 저장 완료: %d 항목",
                len(data.get("items", [])),
            )
        except Exception as exc:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise RuntimeError(
                f"tracker 저장 실패: {self._tracker_path}"
            ) from exc

    # ------------------------------------------------------------------
    # 로그 헬퍼
    # ------------------------------------------------------------------

    def _log(self, message: str) -> None:
        """GUI 로그 큐와 logger에 동시 전달.

        Args:
            message: 전달할 로그 메시지.
                     # ② 빈 문자열은 허용 (내용 없는 로그도 전달 가능)
        """
        # ① None 방어
        if message is None:
            message = ""
        # ③ str 강제 변환 (비정상 타입 방어)
        if not isinstance(message, str):
            message = str(message)

        logger.info(message)
        try:
            self._log_queue.put_nowait(message)
        except queue.Full:
            logger.warning(
                "FollowUpTracker: log_queue 가득 참, 메시지 드롭: %s", message
            )


# ---------------------------------------------------------------------------
# Self-Verification Block
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile as _tempfile

    # --- _to_utc_naive ---
    _dt = datetime(2025, 1, 1, 12, 0, 0)
    assert _to_utc_naive(_dt) == _dt, "_to_utc_naive 일반 datetime 변환 실패"
    assert _to_utc_naive(None) is None, "_to_utc_naive None → None 실패"
    print("[SELF-VERIFY] _to_utc_naive OK")

    # --- FollowUpTracker 초기화 방어 ---
    _q: queue.Queue = queue.Queue()

    # tracker_dir None → TypeError
    try:
        FollowUpTracker(_q, None)  # type: ignore[arg-type]
        assert False, "tracker_dir None TypeError 미발생"
    except TypeError:
        pass

    # tracker_dir str → TypeError
    try:
        FollowUpTracker(_q, "/tmp/test")  # type: ignore[arg-type]
        assert False, "tracker_dir str TypeError 미발생"
    except TypeError:
        pass

    # log_queue None → TypeError
    try:
        FollowUpTracker(None, Path("C:/tmp"))  # type: ignore[arg-type]
        assert False, "log_queue None TypeError 미발생"
    except TypeError:
        pass

    # tracker_dir 상대 경로 → ValueError
    try:
        FollowUpTracker(_q, Path("relative/path"))
        assert False, "상대 경로 ValueError 미발생"
    except ValueError:
        pass

    print("[SELF-VERIFY] FollowUpTracker 초기화 방어 OK")

    # --- _should_track 로직 단독 테스트 (Outlook 불필요) ---
    with _tempfile.TemporaryDirectory() as _tmp_dir:
        _tracker_dir = Path(_tmp_dir).resolve()
        _tracker = FollowUpTracker(_q, _tracker_dir)

        # _should_track: mail_item None → TypeError
        try:
            _tracker._should_track(None)
            assert False, "_should_track None TypeError 미발생"
        except TypeError:
            pass

        # _should_track: mock 객체로 키워드 포함 여부 검증
        class _MockMail:
            """mock mail_item for testing."""
            def __init__(self, body: str = "", html: str = "") -> None:
                self.Body = body
                self.HTMLBody = html

        _mail_with_kw = _MockMail(body="Please advise us accordingly on this matter.")
        _mail_without_kw = _MockMail(body="Thank you for your email.")
        _mail_empty = _MockMail(body="", html="")
        _mail_case = _MockMail(body="PLEASE ADVISE US ACCORDINGLY today.")
        _mail_html = _MockMail(html="<p>Please advise us accordingly</p>")

        assert _tracker._should_track(_mail_with_kw) is True, "키워드 포함 → True 실패"
        assert _tracker._should_track(_mail_without_kw) is False, "키워드 없음 → False 실패"
        assert _tracker._should_track(_mail_empty) is False, "빈 본문 → False 실패"
        assert _tracker._should_track(_mail_case) is True, "대소문자 무관 → True 실패"
        assert _tracker._should_track(_mail_html) is True, "HTML 본문 키워드 → True 실패"

        print("[SELF-VERIFY] _should_track OK")

        # --- _register_item ---
        _items: List[Dict] = []

        # 정상 등록
        result = _tracker._register_item(
            items_list=_items,
            entry_id="ENTRYID001",
            sent_time_iso="2025-01-01T10:00:00",
            conversation_id="CONVID001",
            subject="Test Subject",
        )
        assert result is True, "_register_item 신규 등록 True 실패"
        assert len(_items) == 1, "_register_item 항목 수 오류"
        assert _items[0]["status"] == "pending", "status pending 실패"

        # 중복 등록 → False
        result2 = _tracker._register_item(
            items_list=_items,
            entry_id="ENTRYID001",
            sent_time_iso="2025-01-01T10:00:00",
            conversation_id="CONVID001",
            subject="Test Subject",
        )
        assert result2 is False, "_register_item 중복 → False 실패"
        assert len(_items) == 1, "중복 후 항목 수 증가 오류"

        # entry_id None → TypeError
        try:
            _tracker._register_item(_items, None, "2025-01-01T00:00:00", "", "")  # type: ignore[arg-type]
            assert False, "entry_id None TypeError 미발생"
        except TypeError:
            pass

        # entry_id 빈 문자열 → ValueError
        try:
            _tracker._register_item(_items, "", "2025-01-01T00:00:00", "", "")
            assert False, "entry_id 빈 문자열 ValueError 미발생"
        except ValueError:
            pass

        print("[SELF-VERIFY] _register_item OK")

        # --- FS 로드/저장 ---
        _test_data: Dict = {
            "items": [
                {
                    "entry_id": "TEST001",
                    "sent_time": "2025-01-01T09:00:00",
                    "conversation_id": "CONV001",
                    "subject": "Test",
                    "status": "pending",
                }
            ]
        }

        # 저장
        _tracker._save_tracker(_test_data)
        assert _tracker._tracker_path.exists(), "tracker 파일 저장 실패"

        # 로드
        _loaded = _tracker._load_tracker()
        assert isinstance(_loaded, dict), "로드 결과 dict 아님"
        assert "items" in _loaded, "items 키 없음"
        assert len(_loaded["items"]) == 1, "items 항목 수 오류"
        assert _loaded["items"][0]["entry_id"] == "TEST001", "entry_id 불일치"

        # _save_tracker None → TypeError
        try:
            _tracker._save_tracker(None)  # type: ignore[arg-type]
            assert False, "_save_tracker None TypeError 미발생"
        except TypeError:
            pass

        # _save_tracker 비dict → TypeError
        try:
            _tracker._save_tracker([1, 2, 3])  # type: ignore[arg-type]
            assert False, "_save_tracker list TypeError 미발생"
        except TypeError:
            pass

        print("[SELF-VERIFY] FS 로드/저장 OK")

        # --- 72시간 체크 로직 (Outlook 없이 timedelta 확인) ---
        _now = datetime.utcnow()
        _old_dt = _now - timedelta(hours=73)
        _new_dt = _now - timedelta(hours=10)

        assert (_now - _old_dt) >= timedelta(hours=_FOLLOWUP_HOURS), "73h 경과 체크 실패"
        assert (_now - _new_dt) < timedelta(hours=_FOLLOWUP_HOURS), "10h 미경과 체크 실패"

        print("[SELF-VERIFY] timedelta 72h 체크 OK")

    print("[SELF-VERIFY] follow_up_tracker.py OK")
