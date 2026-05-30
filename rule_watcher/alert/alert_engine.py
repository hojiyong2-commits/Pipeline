# [Purpose]: 조건 충족 시 알림을 트리거하고 (ticker, rule_id) 기준 cooldown으로 중복 억제 + SQLite 영속 기록.
# [Assumptions]:
#   - alerts_log 테이블이 사전 init_db() 호출로 존재 (없으면 자동 init_db 시도).
#   - cooldown_minutes >= 0 (0이면 cooldown 없음).
#   - NotifierRegistry는 thread-safe.
# [Vulnerability & Risks]:
#   - in-memory _cooldown_cache는 프로세스 재시작 시 휘발 — v2에서 alerts_log.triggered_at 조회로 복원 가능.
#   - SQLite WAL이 켜져있지만 단일 connection 사용으로 deadlock 가능성 낮음.
#   - notifier 예외는 swallow — 알림 실패가 다음 cooldown을 막지 않음 (cooldown은 trigger 시점에 무조건 시작).
# [Improvement]: DB 기반 cooldown 복원, 다중 rulebook 우선순위, 알림 큐 비동기 처리, 알림 ACK 흐름.
"""알림 엔진 — cooldown + DB 기록 + notifier 디스패치."""
from __future__ import annotations

import datetime
import logging
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from rule_watcher import config
from rule_watcher.alert.notifiers import AlertPayload, NotifierRegistry
from rule_watcher.db import db_conn, init_db
from rule_watcher.engine.screening_engine import ScreenResult

logger = logging.getLogger(__name__)

# rule_id가 str인데 DB는 INTEGER → 해시 매핑 안전 변환
# alerts_log.rule_id는 INTEGER NOT NULL 이므로 str rule_id를 안정적으로 정수화한다.
# (sqlite3는 type affinity로 ASCII 숫자 문자열을 받아도 동작하지만, rule_id가 "rsi_oversold"
#  같은 문자열인 경우 INTEGER 컬럼에 그대로 들어갈 수 없으므로 명시적 변환을 적용한다.)
def _rule_id_to_int(rule_id: str) -> int:
    """str rule_id를 안정적인 양의 정수로 변환 (DB INTEGER 컬럼 호환)."""
    if rule_id is None:
        raise TypeError("_rule_id_to_int: rule_id must not be None")
    if not isinstance(rule_id, str):
        raise TypeError(
            f"_rule_id_to_int: rule_id must be str, got {type(rule_id).__name__}"
        )
    if not rule_id.strip():
        # negative not allowed: 빈 rule_id는 식별 불가
        raise ValueError("_rule_id_to_int: rule_id must not be empty")
    # 우선 순수 정수 문자열이면 그대로 사용 (UI에서 rulebook PK를 그대로 넘긴 경우)
    try:
        return int(rule_id)
    except ValueError:
        # 문자열 → 안정적 양의 정수 (Python hash 시드 영향을 피하기 위해 sum 사용)
        return abs(sum(ord(c) * (i + 1) for i, c in enumerate(rule_id))) or 1


class AlertEngine:
    """알림 엔진.

    - trigger(): 조건 충족 시 알림을 처리. cooldown 적용 + DB 기록 + notifier 전달.
    - check_cooldown(): (ticker, rule_id) 기준 cooldown 상태 확인 (True=억제).
    - log_alert(): SQLite alerts_log 테이블에 기록.
    - get_alert_log(): 최근 알림 로그 조회.
    - clear_cooldown_cache(): 테스트/수동 클리어.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        cooldown_minutes: Optional[int] = None,
        registry: Optional[NotifierRegistry] = None,
        auto_init_db: bool = True,
    ) -> None:
        """AlertEngine 생성.

        Args:
            db_path: SQLite 파일 경로 (None이면 config.DB_PATH 사용, ":memory:" 가능).
            cooldown_minutes: cooldown 분 (None이면 config.ALERT_COOLDOWN_MIN 사용, 0이면 cooldown 없음).
            registry: NotifierRegistry (None이면 빈 registry 생성).
            auto_init_db: True면 init_db()를 호출하여 테이블 생성 보장.
        Raises:
            TypeError: db_path/cooldown_minutes 타입 오류.
            ValueError: db_path 빈 문자열, cooldown_minutes 음수.
        """
        # AL 항목 1+3: None 입력 처리 + isinstance 검증
        if db_path is not None:
            if not isinstance(db_path, str):
                raise TypeError(
                    f"AlertEngine: db_path must be str, got {type(db_path).__name__}"
                )
            if len(db_path) == 0:
                raise ValueError("AlertEngine: db_path must not be empty")
        if cooldown_minutes is not None:
            if not isinstance(cooldown_minutes, int) or isinstance(
                cooldown_minutes, bool
            ):
                raise TypeError(
                    f"AlertEngine: cooldown_minutes must be int, "
                    f"got {type(cooldown_minutes).__name__}"
                )
            if cooldown_minutes < 0:
                # negative not allowed: cooldown은 0 이상 (0이면 비활성)
                raise ValueError(
                    f"AlertEngine: cooldown_minutes must be >= 0, got {cooldown_minutes}"
                )
        if registry is not None and not isinstance(registry, NotifierRegistry):
            raise TypeError(
                f"AlertEngine: registry must be NotifierRegistry, "
                f"got {type(registry).__name__}"
            )

        self._db_path: str = db_path if db_path is not None else config.DB_PATH
        self._cooldown_minutes: int = (
            cooldown_minutes
            if cooldown_minutes is not None
            else config.ALERT_COOLDOWN_MIN
        )
        self._registry: NotifierRegistry = registry or NotifierRegistry()
        self._lock = threading.Lock()
        # in-memory cooldown cache: (ticker, rule_id) → last_alert datetime
        self._cooldown_cache: Dict[Tuple[str, str], datetime.datetime] = {}

        if auto_init_db:
            try:
                init_db(self._db_path)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"AlertEngine: init_db 실패, 이미 존재할 수 있음: {e}")

    @property
    def registry(self) -> NotifierRegistry:
        """NotifierRegistry 노출 (UI/테스트에서 notifier 등록용)."""
        return self._registry

    @property
    def cooldown_minutes(self) -> int:
        """현재 cooldown 분."""
        return self._cooldown_minutes

    def check_cooldown(self, ticker: str, rule_id: str) -> bool:
        """cooldown 상태 확인.

        Args:
            ticker: 종목 코드.
            rule_id: 룰 식별자.
        Returns:
            True: cooldown 중 (알림 억제 필요).
            False: 전송 가능.
        Raises:
            TypeError: ticker/rule_id가 None이거나 str이 아닌 경우.
            ValueError: ticker/rule_id가 빈 문자열인 경우.
        """
        self._validate_ticker_rule(ticker, rule_id)
        # cooldown_minutes == 0이면 cooldown 비활성 (항상 전송 가능)
        if self._cooldown_minutes == 0:
            return False
        key = (ticker, rule_id)
        with self._lock:
            last = self._cooldown_cache.get(key)
            if last is None:
                return False  # 전송 가능
            elapsed_minutes = (
                datetime.datetime.now() - last
            ).total_seconds() / 60.0
        return elapsed_minutes < self._cooldown_minutes

    def trigger(
        self,
        result: ScreenResult,
        rule_id: str,
        rule_name: str,
        provider: str = "mock",
        price: float = 0.0,
    ) -> bool:
        """조건 충족 시 알림 전송.

        Args:
            result: ScreenResult 인스턴스 (ticker/name/reason 사용).
            rule_id: 룰 식별자 (cooldown key).
            rule_name: 룰 표시명 (DB/notifier에 기록).
            provider: "mock" 또는 "kis".
            price: 현재가 (v1 기본 0.0).
        Returns:
            True: 알림 전송 성공 (DB 기록 + notifier 전달).
            False: cooldown으로 억제됨.
        Raises:
            TypeError: result/rule_id/rule_name/provider/price 타입 오류.
            ValueError: 빈 문자열, ScreenResult.matched=False.
        """
        # AL 항목 1+3
        if result is None:
            raise TypeError("trigger: result must not be None")
        if not isinstance(result, ScreenResult):
            raise TypeError(
                f"trigger: result must be ScreenResult, got {type(result).__name__}"
            )
        if rule_id is None:
            raise TypeError("trigger: rule_id must not be None")
        if not isinstance(rule_id, str):
            raise TypeError(
                f"trigger: rule_id must be str, got {type(rule_id).__name__}"
            )
        if not rule_id.strip():
            # negative not allowed: 빈 rule_id 는 식별 불가
            raise ValueError("trigger: rule_id must not be empty")
        if rule_name is None:
            raise TypeError("trigger: rule_name must not be None")
        if not isinstance(rule_name, str):
            raise TypeError(
                f"trigger: rule_name must be str, got {type(rule_name).__name__}"
            )
        if not rule_name.strip():
            raise ValueError("trigger: rule_name must not be empty")
        if provider is None:
            raise TypeError("trigger: provider must not be None")
        if not isinstance(provider, str):
            raise TypeError(
                f"trigger: provider must be str, got {type(provider).__name__}"
            )
        if not provider.strip():
            raise ValueError("trigger: provider must not be empty")
        if price is None:
            raise TypeError("trigger: price must not be None")
        # price는 int/float 허용 (bool 제외)
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise TypeError(
                f"trigger: price must be int or float, got {type(price).__name__}"
            )

        # matched=False인 결과는 트리거 의미 없음 → 명시적 거부
        if not result.matched:
            return False

        # cooldown 확인
        if self.check_cooldown(result.ticker, rule_id):
            logger.debug(
                f"alert suppressed by cooldown: ticker={result.ticker} "
                f"rule_id={rule_id} cooldown_min={self._cooldown_minutes}"
            )
            return False

        # cooldown 캐시 업데이트 (알림 전 — 전송 실패해도 cooldown 시작)
        now = datetime.datetime.now()
        with self._lock:
            self._cooldown_cache[(result.ticker, rule_id)] = now

        # payload 생성
        payload = AlertPayload(
            ticker=result.ticker,
            name=result.name,
            rule_id=rule_id,
            rule_name=rule_name,
            reason=result.reason,
            price=float(price),  # allowed: float() 변환은 int->float 안전, AL 항목 4 근거
            triggered_at=now.isoformat(),
            provider=provider,
        )

        # DB 기록 — 실패해도 notifier는 계속 시도
        try:
            self.log_alert(payload)
        except Exception as e:  # noqa: BLE001
            logger.error(f"log_alert 실패: {e}")

        # notifier 전달 — 개별 실패 무시
        for notifier in self._registry.get_active_notifiers():
            try:
                notifier.notify(payload)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"notifier {notifier.name} 실패 (계속 진행): {e}"
                )

        return True

    def log_alert(self, payload: AlertPayload) -> None:
        """SQLite alerts_log 테이블에 알림 기록.

        Args:
            payload: AlertPayload 인스턴스.
        Raises:
            TypeError: payload가 None이거나 AlertPayload가 아닌 경우.
            RuntimeError: DB 기록 실패 시 (sqlite3 오류 chain).
        """
        if payload is None:
            raise TypeError("log_alert: payload must not be None")
        if not isinstance(payload, AlertPayload):
            raise TypeError(
                f"log_alert: payload must be AlertPayload, "
                f"got {type(payload).__name__}"
            )
        # alerts_log 스키마: (id, ticker, rule_id INTEGER, rule_name, triggered_at, reason_ko, acknowledged)
        # provider/price는 reason_ko에 통합 기록 (스키마 변경 없이 정보 보존)
        rule_id_int = _rule_id_to_int(payload.rule_id)
        reason_ko = (
            f"{payload.reason} [provider={payload.provider} "
            f"rule_str={payload.rule_id} price={payload.price}]"
        )
        try:
            with db_conn(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO alerts_log "
                    "(ticker, rule_id, rule_name, triggered_at, reason_ko, acknowledged) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (
                        payload.ticker,
                        rule_id_int,
                        payload.rule_name,
                        payload.triggered_at,
                        reason_ko,
                    ),
                )
        except sqlite3.Error as e:
            raise RuntimeError(
                f"log_alert: DB INSERT 실패 ticker={payload.ticker} rule={payload.rule_id}: {e}"
            ) from e

    def get_alert_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """최근 알림 로그 조회 (triggered_at DESC).

        Args:
            limit: 반환 최대 개수 (양수). 0이면 빈 리스트.
        Returns:
            dict 리스트: {id, ticker, rule_id, rule_name, triggered_at, reason_ko, acknowledged}.
        Raises:
            TypeError: limit이 None이거나 int가 아닌 경우.
            ValueError: limit이 음수인 경우.
        """
        if limit is None:
            raise TypeError("get_alert_log: limit must not be None")
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise TypeError(
                f"get_alert_log: limit must be int, got {type(limit).__name__}"
            )
        if limit < 0:
            # negative not allowed: limit은 0 이상
            raise ValueError(f"get_alert_log: limit must be >= 0, got {limit}")
        if limit == 0:
            return []
        try:
            with db_conn(self._db_path) as conn:
                cursor = conn.execute(
                    "SELECT id, ticker, rule_id, rule_name, triggered_at, "
                    "reason_ko, acknowledged "
                    "FROM alerts_log ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
                rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"get_alert_log 실패: {e}")
            # 조회 실패 시 빈 리스트 — UI가 멈추면 안 됨
            return []

    def clear_cooldown_cache(self) -> None:
        """in-memory cooldown 캐시 초기화 (테스트용)."""
        with self._lock:
            self._cooldown_cache.clear()

    def _validate_ticker_rule(self, ticker: str, rule_id: str) -> None:
        """ticker/rule_id 공통 검증."""
        if ticker is None:
            raise TypeError("ticker must not be None")
        if not isinstance(ticker, str):
            raise TypeError(f"ticker must be str, got {type(ticker).__name__}")
        if not ticker.strip():
            # negative not allowed: 빈 ticker 는 식별 불가
            raise ValueError("ticker must not be empty")
        if rule_id is None:
            raise TypeError("rule_id must not be None")
        if not isinstance(rule_id, str):
            raise TypeError(
                f"rule_id must be str, got {type(rule_id).__name__}"
            )
        if not rule_id.strip():
            raise ValueError("rule_id must not be empty")


if __name__ == "__main__":
    # 자가 검증 — TC-3 oracle 시나리오 단축 버전
    import tempfile
    import os as _os

    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        engine = AlertEngine(db_path=tmp_db, cooldown_minutes=30)
        result = ScreenResult(
            ticker="005930",
            name="삼성전자",
            matched=True,
            reason="RSI 조건 충족",
            matched_at=datetime.datetime.now().isoformat(),
        )
        # 첫 트리거: True
        ok1 = engine.trigger(result, rule_id="rsi_oversold", rule_name="RSI 과매도")
        assert ok1 is True, f"첫 트리거는 True여야 함, got {ok1}"
        # 두 번째 트리거: cooldown으로 False
        ok2 = engine.trigger(result, rule_id="rsi_oversold", rule_name="RSI 과매도")
        assert ok2 is False, f"두 번째 트리거는 False여야 함 (cooldown), got {ok2}"
        # DB 기록 1건
        logs = engine.get_alert_log(limit=10)
        assert len(logs) == 1, f"DB 로그 1건이어야 함, got {len(logs)}"

        # cooldown 캐시 초기화 후 재전송 가능
        engine.clear_cooldown_cache()
        ok3 = engine.trigger(result, rule_id="rsi_oversold", rule_name="RSI 과매도")
        assert ok3 is True, f"캐시 클리어 후 True여야 함, got {ok3}"

        # None 방어
        try:
            engine.trigger(None, "x", "y")  # type: ignore[arg-type]
            raise AssertionError("None 예외 미발생")
        except TypeError:
            pass

        # 음수 cooldown 거부
        try:
            AlertEngine(db_path=tmp_db, cooldown_minutes=-1, auto_init_db=False)
            raise AssertionError("음수 cooldown 예외 미발생")
        except ValueError:
            pass

        print("[SELF-VERIFY] alert_engine.py OK")
    finally:
        if _os.path.exists(tmp_db):
            try:
                _os.unlink(tmp_db)
            except OSError:
                pass
