# [Purpose]: 알림 전송 채널 추상화 — InApp / Sound 등 다양한 notifier를 단일 인터페이스로 통합.
# [Assumptions]: 각 notifier는 stateless 또는 thread-safe instance attribute만 보유. 동일 프로세스 내 사용.
# [Vulnerability & Risks]:
#   - SoundNotifier는 Windows 환경에서만 winsound로 실제 소리 재생, 비Windows는 silent fail.
#   - InAppNotifier._log는 deque 기반 FIFO 200 cap — 200 초과 시 가장 오래된 항목 자동 삭제.
#   - NotifierRegistry는 thread-safe 보장 — register/enable/disable에 Lock 적용.
# [Improvement]: 이메일/Slack/Telegram notifier 추가, 로그 영속화(SQLite), notifier별 비동기 큐.
"""알림 전송 채널 추상화 + 기본 notifier 구현."""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional


@dataclass
class AlertPayload:
    """알림 내용을 표준화한 데이터 클래스.

    Attributes:
        ticker: 종목 코드 (1~20자).
        name: 종목명 (1~64자).
        rule_id: 룰 식별자 (1~255자).
        rule_name: 룰 표시명 (1~255자).
        reason: 한국어 사유 설명.
        price: 현재가 (v1에서는 0.0이 기본값, 실제 가격은 DataService 연동 후 채움).
        triggered_at: ISO 8601 datetime 문자열.
        provider: "mock" 또는 "kis".
    """

    ticker: str
    name: str
    rule_id: str
    rule_name: str
    reason: str
    price: float
    triggered_at: str
    provider: str

    def __post_init__(self) -> None:
        # AL 항목 1: None 입력 방어
        if self.ticker is None:
            raise TypeError("AlertPayload: ticker must not be None")
        if self.name is None:
            raise TypeError("AlertPayload: name must not be None")
        if self.rule_id is None:
            raise TypeError("AlertPayload: rule_id must not be None")
        if self.rule_name is None:
            raise TypeError("AlertPayload: rule_name must not be None")
        if self.reason is None:
            raise TypeError("AlertPayload: reason must not be None")
        if self.price is None:
            raise TypeError("AlertPayload: price must not be None")
        if self.triggered_at is None:
            raise TypeError("AlertPayload: triggered_at must not be None")
        if self.provider is None:
            raise TypeError("AlertPayload: provider must not be None")

        # AL 항목 3: isinstance 가드
        if not isinstance(self.ticker, str):
            raise TypeError(
                f"AlertPayload: ticker must be str, got {type(self.ticker).__name__}"
            )
        if not isinstance(self.rule_id, str):
            raise TypeError(
                f"AlertPayload: rule_id must be str, got {type(self.rule_id).__name__}"
            )
        if not isinstance(self.provider, str):
            raise TypeError(
                f"AlertPayload: provider must be str, got {type(self.provider).__name__}"
            )

        # AL 항목 2/4: 경계값 — price는 음수 허용 (지수/스프레드 등에서 음수 가능, 기본 0.0)
        # negative allowed: price는 일반적으로 양수이나, 일부 파생/스프레드 지표는 음수가 정상값
        # ID 파라미터 길이 검증 (1~255자)
        if not self.ticker.strip():
            raise ValueError("AlertPayload: ticker must not be empty")
        if len(self.ticker) > 255:
            raise ValueError(
                f"AlertPayload: ticker length must be 1~255, got {len(self.ticker)}"
            )
        if not self.rule_id.strip():
            raise ValueError("AlertPayload: rule_id must not be empty")
        if len(self.rule_id) > 255:
            raise ValueError(
                f"AlertPayload: rule_id length must be 1~255, got {len(self.rule_id)}"
            )

    def to_dict(self) -> Dict[str, object]:
        """JSON 직렬화 가능한 dict 변환."""
        return {
            "ticker": self.ticker,
            "name": self.name,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "reason": self.reason,
            "price": self.price,
            "triggered_at": self.triggered_at,
            "provider": self.provider,
        }


class BaseNotifier(ABC):
    """알림 채널 인터페이스 — 모든 notifier는 이 클래스를 상속한다."""

    def __init__(self) -> None:
        self._enabled: bool = True

    @property
    @abstractmethod
    def name(self) -> str:
        """notifier 식별자 (registry에서 unique key)."""
        ...

    @abstractmethod
    def notify(self, payload: AlertPayload) -> bool:
        """알림 전송. 성공 시 True, 실패 시 False (예외 던지지 않음)."""
        ...

    @property
    def enabled(self) -> bool:
        """현재 활성 상태."""
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        """활성/비활성 토글.

        Args:
            value: True=활성, False=비활성.
        Raises:
            TypeError: value가 bool이 아닌 경우.
        """
        if value is None:
            raise TypeError("set_enabled: value must not be None")
        if not isinstance(value, bool):
            raise TypeError(
                f"set_enabled: value must be bool, got {type(value).__name__}"
            )
        self._enabled = value


# InAppNotifier 로그 최대 보관 개수 (FIFO)
_INAPP_LOG_MAX = 200


class InAppNotifier(BaseNotifier):
    """앱 내부 알림 — 메모리 로그 큐에만 추가 (Streamlit session_state 연동은 MT-7에서).

    최대 200개 로그를 FIFO로 유지한다. 200 초과 시 가장 오래된 항목 자동 삭제.
    """

    def __init__(self) -> None:
        super().__init__()
        self._log: Deque[Dict[str, object]] = deque(maxlen=_INAPP_LOG_MAX)
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "in_app"

    def notify(self, payload: AlertPayload) -> bool:
        """payload를 메모리 로그에 추가.

        Args:
            payload: 알림 내용.
        Returns:
            True (추가 성공) — 메모리 작업이므로 일반적으로 실패하지 않음.
        Raises:
            TypeError: payload가 None이거나 AlertPayload가 아닌 경우.
        """
        if payload is None:
            raise TypeError("InAppNotifier.notify: payload must not be None")
        if not isinstance(payload, AlertPayload):
            raise TypeError(
                f"InAppNotifier.notify: payload must be AlertPayload, "
                f"got {type(payload).__name__}"
            )
        try:
            with self._lock:
                self._log.append(payload.to_dict())
            return True
        except Exception:  # noqa: BLE001 — notifier 실패가 앱을 중단하면 안 됨
            return False

    def get_logs(self, limit: Optional[int] = None) -> List[Dict[str, object]]:
        """저장된 로그 조회 (최신 항목이 마지막).

        Args:
            limit: 반환 최대 개수. None이면 전체.
        Returns:
            로그 dict 리스트 (deque 스냅샷).
        Raises:
            TypeError: limit이 None이 아니면서 int가 아닌 경우.
            ValueError: limit이 음수인 경우.
        """
        if limit is not None:
            if not isinstance(limit, int) or isinstance(limit, bool):
                raise TypeError(
                    f"get_logs: limit must be int or None, got {type(limit).__name__}"
                )
            if limit < 0:
                # negative not allowed: limit은 0 이상
                raise ValueError(f"get_logs: limit must be >= 0, got {limit}")
        with self._lock:
            snapshot = list(self._log)
        if limit is None:
            return snapshot
        if limit == 0:
            return []
        return snapshot[-limit:]

    def clear(self) -> None:
        """로그 전체 삭제 (테스트/수동 클리어)."""
        with self._lock:
            self._log.clear()


class SoundNotifier(BaseNotifier):
    """소리 알림 — Windows: winsound.Beep, 비Windows: 조용히 무시.

    frequency/duration은 인스턴스별 설정 가능.
    """

    def __init__(self, frequency: int = 1000, duration: int = 300) -> None:
        super().__init__()
        # AL 항목 1: None 방어
        if frequency is None:
            raise TypeError("SoundNotifier: frequency must not be None")
        if duration is None:
            raise TypeError("SoundNotifier: duration must not be None")
        # AL 항목 3: isinstance 가드
        if not isinstance(frequency, int) or isinstance(frequency, bool):
            raise TypeError(
                f"SoundNotifier: frequency must be int, got {type(frequency).__name__}"
            )
        if not isinstance(duration, int) or isinstance(duration, bool):
            raise TypeError(
                f"SoundNotifier: duration must be int, got {type(duration).__name__}"
            )
        # AL 항목 2/4: 경계값 (Windows winsound 허용 범위는 37~32767 Hz, duration >=1)
        if frequency <= 0:
            # negative not allowed: frequency는 양수여야 함 (Hz)
            raise ValueError(f"SoundNotifier: frequency must be > 0, got {frequency}")
        if duration <= 0:
            # negative not allowed: duration은 양수여야 함 (ms)
            raise ValueError(f"SoundNotifier: duration must be > 0, got {duration}")
        self.frequency: int = frequency
        self.duration: int = duration

    @property
    def name(self) -> str:
        return "sound"

    def notify(self, payload: AlertPayload) -> bool:
        """소리 재생. Windows가 아니면 silent True 반환.

        Args:
            payload: 알림 내용 (소리에는 사용되지 않으나 인터페이스 유지).
        Returns:
            True: 재생 성공, Windows 오디오 장치 없음, 또는 비Windows (silent skip).
        Raises:
            TypeError: payload가 None이거나 AlertPayload가 아닌 경우.
        """
        if payload is None:
            raise TypeError("SoundNotifier.notify: payload must not be None")
        if not isinstance(payload, AlertPayload):
            raise TypeError(
                f"SoundNotifier.notify: payload must be AlertPayload, "
                f"got {type(payload).__name__}"
            )
        try:
            import winsound  # type: ignore[import-not-found]
        except ImportError:
            # 비Windows 환경 — 조용히 무시
            return True
        try:
            winsound.Beep(self.frequency, self.duration)
            return True
        except Exception:  # noqa: BLE001 — notifier 실패가 앱을 중단하면 안 됨
            # 소리 재생 실패(오디오 장치 없음 등)는 앱에 영향 없음 — True 반환
            return True


class NotifierRegistry:
    """활성화된 notifier 컬렉션 관리 — name 기준 unique."""

    def __init__(self) -> None:
        self._notifiers: Dict[str, BaseNotifier] = {}
        self._lock = threading.Lock()

    def register(self, notifier: BaseNotifier) -> None:
        """notifier 등록. 동일 name이 이미 있으면 덮어씀.

        Args:
            notifier: BaseNotifier 인스턴스.
        Raises:
            TypeError: notifier가 None이거나 BaseNotifier가 아닌 경우.
        """
        if notifier is None:
            raise TypeError("NotifierRegistry.register: notifier must not be None")
        if not isinstance(notifier, BaseNotifier):
            raise TypeError(
                f"NotifierRegistry.register: notifier must be BaseNotifier, "
                f"got {type(notifier).__name__}"
            )
        with self._lock:
            self._notifiers[notifier.name] = notifier

    def unregister(self, name: str) -> bool:
        """name으로 notifier 제거.

        Args:
            name: notifier 이름.
        Returns:
            True: 제거 성공. False: 등록되지 않음.
        Raises:
            TypeError: name이 None이거나 str이 아닌 경우.
            ValueError: name이 빈 문자열인 경우.
        """
        self._validate_name(name)
        with self._lock:
            return self._notifiers.pop(name, None) is not None

    def enable(self, name: str) -> None:
        """notifier 활성화.

        Args:
            name: notifier 이름.
        Raises:
            TypeError: name이 None이거나 str이 아닌 경우.
            ValueError: name이 빈 문자열이거나 등록되지 않은 경우.
        """
        self._validate_name(name)
        with self._lock:
            if name not in self._notifiers:
                raise ValueError(
                    f"NotifierRegistry.enable: '{name}' notifier가 등록되지 않았습니다"
                )
            self._notifiers[name].set_enabled(True)

    def disable(self, name: str) -> None:
        """notifier 비활성화.

        Args:
            name: notifier 이름.
        Raises:
            TypeError: name이 None이거나 str이 아닌 경우.
            ValueError: name이 빈 문자열이거나 등록되지 않은 경우.
        """
        self._validate_name(name)
        with self._lock:
            if name not in self._notifiers:
                raise ValueError(
                    f"NotifierRegistry.disable: '{name}' notifier가 등록되지 않았습니다"
                )
            self._notifiers[name].set_enabled(False)

    def get(self, name: str) -> Optional[BaseNotifier]:
        """name으로 notifier 조회.

        Args:
            name: notifier 이름.
        Returns:
            BaseNotifier 인스턴스 또는 None.
        Raises:
            TypeError: name이 None이거나 str이 아닌 경우.
            ValueError: name이 빈 문자열인 경우.
        """
        self._validate_name(name)
        with self._lock:
            return self._notifiers.get(name)

    def get_active_notifiers(self) -> List[BaseNotifier]:
        """현재 enabled=True인 notifier 목록 (스냅샷).

        Returns:
            활성 notifier 리스트 (호출 시점 스냅샷).
        """
        with self._lock:
            return [n for n in self._notifiers.values() if n.enabled]

    def get_all_notifiers(self) -> List[BaseNotifier]:
        """등록된 모든 notifier (enabled 무관)."""
        with self._lock:
            return list(self._notifiers.values())

    @staticmethod
    def _validate_name(name: str) -> None:
        """name 파라미터 검증 (None/타입/빈 문자열)."""
        if name is None:
            raise TypeError("NotifierRegistry: name must not be None")
        if not isinstance(name, str):
            raise TypeError(
                f"NotifierRegistry: name must be str, got {type(name).__name__}"
            )
        if not name.strip():
            # negative not allowed: 빈 name은 식별 불가
            raise ValueError("NotifierRegistry: name must not be empty")


if __name__ == "__main__":
    # 자가 검증
    payload = AlertPayload(
        ticker="005930",
        name="삼성전자",
        rule_id="rsi_oversold",
        rule_name="RSI 과매도",
        reason="RSI(14) < 30 충족",
        price=0.0,
        triggered_at="2026-05-30T12:00:00",
        provider="mock",
    )
    in_app = InAppNotifier()
    assert in_app.notify(payload) is True
    assert len(in_app.get_logs()) == 1

    # FIFO 200 cap
    for i in range(250):
        in_app.notify(payload)
    assert len(in_app.get_logs()) == 200, f"max 200, got {len(in_app.get_logs())}"

    # Registry
    reg = NotifierRegistry()
    reg.register(in_app)
    sound = SoundNotifier()
    reg.register(sound)
    assert len(reg.get_active_notifiers()) == 2
    reg.disable("sound")
    assert len(reg.get_active_notifiers()) == 1
    assert reg.get_active_notifiers()[0].name == "in_app"

    # None 방어
    try:
        in_app.notify(None)  # type: ignore[arg-type]
        raise AssertionError("None 예외 미발생")
    except TypeError:
        pass

    # SoundNotifier 경계값
    try:
        SoundNotifier(frequency=-1)
        raise AssertionError("음수 frequency 예외 미발생")
    except ValueError:
        pass

    print("[SELF-VERIFY] notifiers.py OK")
