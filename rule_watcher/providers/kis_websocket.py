# [Purpose]: KIS WebSocket 실시간 시세 클라이언트 인터페이스 정의. v1.1에서 실제 구현.
# [Assumptions]: v1에서는 스텁만 제공. 실제 WebSocket 라이브러리(websockets, websocket-client 등)는 v1.1에서 의존성에 추가.
# [Vulnerability & Risks]: v1에서 실제 연결 없음. v1.1 도입 시 인증 토큰 노출 / 재연결 폭주 / 메시지 무한 큐 위험 고려 필요.
# [Improvement]: v1.1: 자동 재연결 (지수 백오프), heartbeat, 메시지 큐 사이즈 제한, 토큰 자동 갱신.
"""KIS WebSocket 실시간 시세 (v1.1 구현 예정).

v1: 스텁 파일 — 인터페이스 정의만 포함.

v1.1 구현 내용:
    - wss://openapi.koreainvestment.com:9443 연결
    - SUBSCRIBE 메시지로 관심종목 실시간 시세 구독
    - 연결 끊김 시 자동 재연결 (지수 백오프)
    - 수신 데이터를 DataService 캐시에 업데이트
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


class KISWebSocket:
    """KIS WebSocket 실시간 시세 클라이언트 (v1.1 예정)."""

    def __init__(self, on_price_update: Callable[[dict], None] | None = None) -> None:
        """v1 스텁 — 실제 WebSocket 연결 없음.

        Args:
            on_price_update: 시세 업데이트 콜백 (v1.1에서 호출).
        """
        self._on_price_update: Callable[[dict], None] | None = on_price_update
        self._subscribed: set[str] = set()
        self._is_running: bool = False
        logger.info("KISWebSocket: v1.1에서 실제 WebSocket 구현 예정")

    def subscribe(self, tickers: list[str]) -> None:
        """종목 구독 (v1 stub).

        Args:
            tickers: 구독할 종목코드 리스트.
        Raises:
            TypeError: tickers가 None이거나 list가 아닌 경우.
        """
        if tickers is None:
            raise TypeError("tickers must not be None")
        if not isinstance(tickers, list):
            raise TypeError(f"tickers must be list, got {type(tickers).__name__}")

        for t in tickers:
            if not isinstance(t, str) or not t:
                continue
            self._subscribed.add(t)
        logger.debug(f"WebSocket 구독 요청 (stub): {tickers}")

    def unsubscribe(self, tickers: list[str]) -> None:
        """종목 구독 해제 (v1 stub).

        Args:
            tickers: 해제할 종목코드 리스트.
        """
        if tickers is None:
            raise TypeError("tickers must not be None")
        if not isinstance(tickers, list):
            raise TypeError(f"tickers must be list, got {type(tickers).__name__}")

        for t in tickers:
            self._subscribed.discard(t)
        logger.debug(f"WebSocket 구독 해제 요청 (stub): {tickers}")

    def start(self) -> None:
        """WebSocket 연결 시작 (v1 stub)."""
        self._is_running = True
        logger.info("WebSocket 연결 (v1 stub — 실제 연결 없음)")

    def stop(self) -> None:
        """WebSocket 연결 종료 (v1 stub)."""
        self._is_running = False
        logger.info("WebSocket 종료 (v1 stub)")

    @property
    def is_running(self) -> bool:
        """WebSocket 동작 여부."""
        return self._is_running

    @property
    def subscribed_tickers(self) -> list[str]:
        """현재 구독 중인 종목 목록."""
        return sorted(self._subscribed)
