# [Purpose]: KIS Open API provider 인터페이스 정의. v1은 mock fallback, v1.1에서 실제 REST/WebSocket 활성화.
# [Assumptions]: KIS_APP_KEY/SECRET 환경변수 설정 시 DataService가 자동으로 이 provider를 선택.
# [Vulnerability & Risks]: v1에서 _get_token()이 stub_token만 반환. 실제 API 호출 코드는 미구현이므로 보안 위험 없음. v1.1에서 토큰 캐싱과 자동 갱신, rate limit 준수 필요.
# [Improvement]: v1.1: requests.Session + Retry adapter + (connect, read) timeout, 토큰 만료 자동 재발급, WebSocket 자동 재연결.
"""KIS Open API Provider — v1은 스텁, 실제 연결은 v1.1에서 활성화.

환경변수 KIS_APP_KEY, KIS_APP_SECRET이 설정되면 이 provider가 자동 선택됩니다.
API 키가 없으면 MockProvider로 자동 fallback됩니다 (DataService에서 처리).

KIS Open API 문서: https://apiportal.koreainvestment.com
인증 방식: OAuth2 client_credentials
Rate limit: REST 초당 15회
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import pandas as pd

from rule_watcher.providers.base import BaseProvider, StockInfo

logger = logging.getLogger(__name__)

# KIS API endpoint 상수 (v1.1 구현 시 활성화)
_KIS_TOKEN_URL = "/oauth2/tokenP"
_KIS_PRICE_URL = "/uapi/domestic-stock/v1/quotations/inquire-price"
_KIS_OHLCV_URL = "/uapi/domestic-stock/v1/quotations/inquire-daily-price"

# Rate limit 설정
_MAX_CALLS_PER_SEC = 15
_CALL_INTERVAL = 1.0 / _MAX_CALLS_PER_SEC


class KISProvider(BaseProvider):
    """한국투자증권 KIS Open API provider.

    v1: 인터페이스만 정의 (MockProvider fallback)
    v1.1: 실제 REST + WebSocket 구현 예정

    사용법:
        환경변수 설정: KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO
        키가 있으면 DataService가 자동으로 이 provider를 선택합니다.
    """

    def __init__(self) -> None:
        from rule_watcher.config import KIS_APP_KEY, KIS_APP_SECRET, KIS_BASE_URL

        self._app_key: str = KIS_APP_KEY
        self._app_secret: str = KIS_APP_SECRET
        self._base_url: str = KIS_BASE_URL
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._last_call_at: float = 0.0

        # v1: mock provider로 실제 데이터 제공
        from rule_watcher.providers.mock_provider import MockProvider

        self._mock_fallback: MockProvider = MockProvider()

        logger.info(
            "KIS Provider 초기화 — v1 mock fallback 모드. "
            "실제 API 연결은 v1.1에서 활성화됩니다."
        )

    @property
    def name(self) -> str:
        return "kis"

    def _throttle(self) -> None:
        """Rate limit 준수 — 초당 15회 이하 보장."""
        elapsed = time.time() - self._last_call_at
        if elapsed < _CALL_INTERVAL:
            time.sleep(_CALL_INTERVAL - elapsed)
        self._last_call_at = time.time()

    def _get_token(self) -> str:
        """OAuth2 access token 발급 / 갱신 (v1.1 구현 예정).

        실제 구현 (v1.1):
            POST {base_url}/oauth2/tokenP
            body: {"grant_type": "client_credentials", "appkey": ..., "appsecret": ...}
            Response: {"access_token": ..., "expires_in": 86400}
        """
        # v1: 스텁 — v1.1에서 실제 KIS API 호출로 교체
        logger.debug("KIS token 조회 (v1 stub)")
        return "stub_token_v1"

    def get_ohlcv(self, ticker: str, period: int = 120) -> pd.DataFrame:
        """일봉 OHLCV 조회 — v1은 mock fallback.

        v1.1 구현:
            GET /uapi/domestic-stock/v1/quotations/inquire-daily-price
            tr_id: FHKST03010100
            params: FID_COND_MRKT_DIV_CODE=J, FID_INPUT_ISCD={ticker},
                    FID_INPUT_DATE_1={start}, FID_INPUT_DATE_2={end}, FID_PERIOD_DIV_CODE=D

        Args:
            ticker: 종목코드 문자열.
            period: 조회할 봉 개수.
        Returns:
            OHLCV DataFrame (mock fallback).
        Raises:
            TypeError/ValueError: MockProvider.get_ohlcv 위임 (입력 검증 동일).
        """
        if ticker is None:
            raise TypeError("ticker must not be None")
        if not isinstance(ticker, str):
            raise TypeError(f"ticker must be str, got {type(ticker).__name__}")
        return self._mock_fallback.get_ohlcv(ticker, period)

    def get_current_price(self, ticker: str) -> StockInfo:
        """현재가 조회 — v1은 mock fallback.

        v1.1 구현:
            GET /uapi/domestic-stock/v1/quotations/inquire-price
            tr_id: FHKST01010100
            params: FID_COND_MRKT_DIV_CODE=J, FID_INPUT_ISCD={ticker}
        """
        if ticker is None:
            raise TypeError("ticker must not be None")
        if not isinstance(ticker, str):
            raise TypeError(f"ticker must be str, got {type(ticker).__name__}")
        return self._mock_fallback.get_current_price(ticker)

    def get_stock_list(self, market: str = "KOSPI") -> list[StockInfo]:
        """종목 목록 조회 — v1은 mock fallback.

        v1.1 구현:
            MasterBook API 또는 KRX 전종목 코드 파일 다운로드.
        """
        if market is None:
            raise TypeError("market must not be None")
        if not isinstance(market, str):
            raise TypeError(f"market must be str, got {type(market).__name__}")
        return self._mock_fallback.get_stock_list(market)

    def is_available(self) -> bool:
        """KIS API 키가 설정되어 있는지 여부."""
        return bool(self._app_key and self._app_secret)
