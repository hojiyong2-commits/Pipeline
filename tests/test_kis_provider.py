# [Purpose]: KIS Provider 스텁이 BaseProvider 인터페이스를 준수하고, 실제 API 호출이 v1에서 발생하지 않음을 검증.
# [Assumptions]: requests 모듈이 설치되어 있음. patch.object로 get/post 호출을 가로채 검증.
# [Vulnerability & Risks]: 실제 KIS API key는 사용하지 않음 (dummy EXAMPLE 패딩만 사용).
# [Improvement]: v1.1에서 실제 HTTP 호출이 활성화되면 responses 또는 requests-mock 라이브러리로 응답 모킹.
"""KIS Provider 테스트 — 실제 API 호출 없이 인터페이스만 검증."""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from rule_watcher.providers.base import StockInfo
from rule_watcher.providers.kis_provider import KISProvider
from rule_watcher.providers.kis_websocket import KISWebSocket


class TestKISProviderInterface:
    """KIS provider 인터페이스 테스트."""

    def setup_method(self) -> None:
        with patch.dict("os.environ", {
            "KIS_APP_KEY": "dummy_key_EXAMPLE_AAAA",  # noqa: S105
            "KIS_APP_SECRET": "dummy_secret_EXAMPLE_AAAA",  # noqa: S105
        }):
            from importlib import reload

            import rule_watcher.config as cfg
            reload(cfg)
            self.provider = KISProvider()

    def test_provider_name(self) -> None:
        assert self.provider.name == "kis"

    def test_get_ohlcv_returns_dataframe(self) -> None:
        df = self.provider.get_ohlcv("005930", period=30)
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
        required_cols = {"open", "high", "low", "close", "volume"}
        assert required_cols.issubset(set(df.columns))

    def test_get_current_price_returns_stock_info(self) -> None:
        info = self.provider.get_current_price("005930")
        assert isinstance(info, StockInfo)
        assert info.ticker == "005930"

    def test_get_stock_list_returns_list(self) -> None:
        result = self.provider.get_stock_list()
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(item, StockInfo) for item in result)

    def test_no_real_api_call(self) -> None:
        """v1에서 실제 KIS API 호출이 없어야 함."""
        import requests
        with patch.object(requests, "get") as mock_get, \
             patch.object(requests, "post") as mock_post:
            self.provider.get_ohlcv("005930", period=5)
            self.provider.get_current_price("005930")
            self.provider.get_stock_list("KOSPI")
            mock_get.assert_not_called()
            mock_post.assert_not_called()

    def test_get_token_returns_stub(self) -> None:
        """v1 토큰은 스텁 문자열을 반환."""
        token = self.provider._get_token()
        assert isinstance(token, str)
        assert token.startswith("stub_")

    def test_input_validation_none(self) -> None:
        """ticker None 입력 시 TypeError."""
        try:
            self.provider.get_ohlcv(None, period=10)  # type: ignore[arg-type]
            raise AssertionError("None 입력 예외 미발생")
        except TypeError:
            pass

    def test_input_validation_wrong_type(self) -> None:
        """ticker 비정상 타입 시 TypeError."""
        try:
            self.provider.get_ohlcv(12345, period=10)  # type: ignore[arg-type]
            raise AssertionError("int 입력 예외 미발생")
        except TypeError:
            pass


class TestKISWebSocketStub:
    """KIS WebSocket 스텁 인터페이스 테스트."""

    def test_initial_state(self) -> None:
        ws = KISWebSocket()
        assert ws.is_running is False
        assert ws.subscribed_tickers == []

    def test_subscribe_and_unsubscribe(self) -> None:
        ws = KISWebSocket()
        ws.subscribe(["005930", "000660"])
        assert "005930" in ws.subscribed_tickers
        assert "000660" in ws.subscribed_tickers
        ws.unsubscribe(["005930"])
        assert "005930" not in ws.subscribed_tickers
        assert "000660" in ws.subscribed_tickers

    def test_start_stop(self) -> None:
        ws = KISWebSocket()
        ws.start()
        assert ws.is_running is True
        ws.stop()
        assert ws.is_running is False

    def test_subscribe_none_raises(self) -> None:
        ws = KISWebSocket()
        try:
            ws.subscribe(None)  # type: ignore[arg-type]
            raise AssertionError("None 입력 예외 미발생")
        except TypeError:
            pass

    def test_subscribe_wrong_type_raises(self) -> None:
        ws = KISWebSocket()
        try:
            ws.subscribe("005930")  # type: ignore[arg-type]
            raise AssertionError("str 입력 예외 미발생")
        except TypeError:
            pass
