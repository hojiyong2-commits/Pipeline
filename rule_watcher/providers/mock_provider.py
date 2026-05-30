# [Purpose]: 실제 API 호출 없이 결정론적 OHLCV / 종목 데이터를 반환하여 개발 및 테스트를 가능하게 한다.
# [Assumptions]: ticker 문자열을 시드로 사용하므로 동일 ticker는 항상 동일 데이터 반환 (테스트 재현성 확보).
# [Vulnerability & Risks]: ticker가 알 수 없는 코드일 때 _BASE_PRICES 기본값(50000) 사용 — 명시적으로 처리되었으나 사용자에게 안내 부족.
# [Improvement]: tests/fixtures/mock_ohlcv.json 같은 외부 fixture에서 데이터 로드, 시나리오별 분기.
"""Mock provider — 테스트 및 개발용 결정론적 샘플 데이터 제공."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from rule_watcher.providers.base import BaseProvider, StockInfo

logger = logging.getLogger(__name__)

# 샘플 종목 목록 (ticker, name, market)
_SAMPLE_STOCKS: list[tuple[str, str, str]] = [
    ("005930", "삼성전자", "KOSPI"),
    ("000660", "SK하이닉스", "KOSPI"),
    ("035720", "카카오", "KOSDAQ"),
    ("035420", "NAVER", "KOSPI"),
    ("051910", "LG화학", "KOSPI"),
    ("006400", "삼성SDI", "KOSPI"),
    ("207940", "삼성바이오로직스", "KOSPI"),
    ("068270", "셀트리온", "KOSPI"),
    ("028260", "삼성물산", "KOSPI"),
    ("017670", "SK텔레콤", "KOSPI"),
]

# 베이스 가격 (실제 근사값)
_BASE_PRICES: dict[str, float] = {
    "005930": 75000.0,
    "000660": 165000.0,
    "035720": 50000.0,
    "035420": 195000.0,
    "051910": 310000.0,
    "006400": 280000.0,
    "207940": 750000.0,
    "068270": 165000.0,
    "028260": 135000.0,
    "017670": 55000.0,
}

_DEFAULT_BASE_PRICE: float = 50000.0
_MAX_PERIOD: int = 5000  # 병리적 입력 방어 상한


class MockProvider(BaseProvider):
    """테스트 및 개발용 mock 데이터 provider.

    실제 API를 호출하지 않고 결정론적 샘플 데이터를 반환합니다.
    """

    @property
    def name(self) -> str:
        return "mock"

    def get_ohlcv(self, ticker: str, period: int = 120) -> pd.DataFrame:
        """결정론적 OHLCV 데이터 생성.

        Args:
            ticker: 종목코드 문자열. 시드로 사용되므로 동일 ticker는 항상 동일 데이터 반환.
            period: 생성할 봉 개수 (1 이상 _MAX_PERIOD 이하).
        Returns:
            columns=['open','high','low','close','volume'], index=DatetimeIndex.
        Raises:
            TypeError: ticker가 None/str이 아니거나 period가 int가 아닌 경우.
            ValueError: ticker가 빈 문자열이거나 period가 음수/0 또는 _MAX_PERIOD 초과.
        """
        if ticker is None:
            raise TypeError("ticker must not be None")
        if not isinstance(ticker, str):
            raise TypeError(f"ticker must be str, got {type(ticker).__name__}")
        if len(ticker) == 0:
            raise ValueError("ticker must not be empty")
        if period is None:
            raise TypeError("period must not be None")
        if not isinstance(period, int) or isinstance(period, bool):
            raise TypeError(f"period must be int, got {type(period).__name__}")
        if period <= 0:
            # negative not allowed: period는 양수여야 함 (0개 봉은 무의미)
            raise ValueError(f"period must be > 0, got {period}")
        if period > _MAX_PERIOD:
            raise ValueError(f"period must be <= {_MAX_PERIOD}, got {period}")

        seed = sum(ord(c) for c in ticker) % (2**32 - 1)
        rng = np.random.default_rng(seed)

        base_price = float(_BASE_PRICES.get(ticker, _DEFAULT_BASE_PRICE))

        end_date = datetime(2026, 5, 30)
        # 영업일만 추출 (period보다 충분히 넉넉하게 생성한 뒤 슬라이싱)
        raw_dates = [end_date - timedelta(days=i) for i in range(period * 2)]
        biz_dates = [d for d in raw_dates if d.weekday() < 5][:period]
        biz_dates.sort()

        if not biz_dates:
            biz_dates = [end_date - timedelta(days=i) for i in range(period - 1, -1, -1)]
            biz_dates.sort()

        # 가격 시계열 생성 (GBM 유사)
        returns = rng.normal(0.0003, 0.015, len(biz_dates))
        prices: list[float] = [base_price]
        for r in returns[1:]:
            prices.append(prices[-1] * (1 + r))

        data: list[dict[str, float]] = []
        for i, (date, close) in enumerate(zip(biz_dates, prices)):
            volatility = abs(rng.normal(0, 0.008))
            high = close * (1 + volatility)
            low = close * (1 - volatility)
            open_price = prices[i - 1] if i > 0 else close
            volume = float(int(rng.uniform(500_000, 5_000_000)))
            data.append({
                "open": round(open_price, 0),
                "high": round(high, 0),
                "low": round(low, 0),
                "close": round(close, 0),
                "volume": volume,
            })

        df = pd.DataFrame(data, index=pd.DatetimeIndex(biz_dates))
        return df

    def get_current_price(self, ticker: str) -> StockInfo:
        """mock 현재가 반환.

        Args:
            ticker: 종목코드 문자열.
        Returns:
            StockInfo (current_price, change_rate, volume 포함).
        Raises:
            TypeError: ticker가 None이거나 str이 아닌 경우.
            ValueError: ticker가 빈 문자열인 경우.
        """
        if ticker is None:
            raise TypeError("ticker must not be None")
        if not isinstance(ticker, str):
            raise TypeError(f"ticker must be str, got {type(ticker).__name__}")
        if len(ticker) == 0:
            raise ValueError("ticker must not be empty")

        ohlcv = self.get_ohlcv(ticker, period=2)
        if ohlcv.empty:
            return StockInfo(ticker=ticker, name=ticker)

        last = ohlcv.iloc[-1]
        prev = ohlcv.iloc[-2] if len(ohlcv) > 1 else last
        prev_close = float(prev["close"])
        change_rate = (
            (float(last["close"]) - prev_close) / prev_close * 100
            if prev_close
            else 0.0
        )

        name = next((n for t, n, _ in _SAMPLE_STOCKS if t == ticker), ticker)
        market = next((m for t, _, m in _SAMPLE_STOCKS if t == ticker), "KOSPI")

        return StockInfo(
            ticker=ticker,
            name=name,
            market=market,
            current_price=float(last["close"]),
            change_rate=round(change_rate, 2),
            volume=int(last["volume"]),
            updated_at=datetime.now().strftime("%H:%M:%S"),
        )

    def get_stock_list(self, market: str = "KOSPI") -> list[StockInfo]:
        """mock 종목 목록 반환.

        Args:
            market: "KOSPI" | "KOSDAQ" | "ALL".
        Returns:
            지정 시장의 StockInfo 리스트.
        Raises:
            TypeError: market이 None이거나 str이 아닌 경우.
            ValueError: market이 빈 문자열인 경우.
        """
        if market is None:
            raise TypeError("market must not be None")
        if not isinstance(market, str):
            raise TypeError(f"market must be str, got {type(market).__name__}")
        if len(market) == 0:
            raise ValueError("market must not be empty")

        result: list[StockInfo] = []
        for ticker, _name, mkt in _SAMPLE_STOCKS:
            if market == "ALL" or mkt == market:
                info = self.get_current_price(ticker)
                info.market = mkt
                result.append(info)
        return result
