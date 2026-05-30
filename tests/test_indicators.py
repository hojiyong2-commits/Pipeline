# [Purpose]: 16+ 인디케이터 함수가 입력 검증, 결과 형식, 수학적 범위를 만족함을 검증.
# [Assumptions]: numpy default_rng(42) 시드로 테스트 재현성 확보.
# [Vulnerability & Risks]: 부동소수점 비교 시 isclose 사용 권장 — 현재 (>=0) (<=100) 형태만 사용.
# [Improvement]: TA-Lib 또는 pandas-ta와 결과 비교하는 reference 테스트 추가.
"""인디케이터 엔진 테스트 — 순수 계산 함수 검증."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from rule_watcher.indicators.momentum import (
    calc_cci,
    calc_roc,
    calc_rsi,
    calc_stoch_rsi,
    calc_stochastic,
)
from rule_watcher.indicators.price import (
    calc_gap,
    calc_ma_cross,
    calc_near_high,
    calc_prev_high_breakout,
    calc_price_condition,
)
from rule_watcher.indicators.trend import calc_adx, calc_ema, calc_macd, calc_sma
from rule_watcher.indicators.volatility import calc_atr, calc_bollinger, calc_donchian
from rule_watcher.indicators.volume import calc_mfi, calc_obv, calc_volume_ma, calc_vwap


def make_ohlcv(n: int = 60, base: float = 50000.0, seed: int = 42) -> pd.DataFrame:
    """테스트용 OHLCV 데이터 생성."""
    rng = np.random.default_rng(seed)
    close = base + np.cumsum(rng.normal(0, 500, n))
    close = np.maximum(close, 1000.0)
    high = close * (1 + rng.uniform(0.001, 0.01, n))
    low = close * (1 - rng.uniform(0.001, 0.01, n))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    volume = rng.integers(500_000, 5_000_000, n).astype(float)

    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=dates)


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return make_ohlcv(120)


class TestTrendIndicators:
    def test_sma_length(self, sample_df: pd.DataFrame) -> None:
        result = calc_sma(sample_df, period=20)
        assert len(result) == len(sample_df)
        assert result.name == "sma_20"

    def test_ema_length(self, sample_df: pd.DataFrame) -> None:
        result = calc_ema(sample_df, period=20)
        assert len(result) == len(sample_df)
        assert result.name == "ema_20"

    def test_macd_returns_result(self, sample_df: pd.DataFrame) -> None:
        result = calc_macd(sample_df)
        assert not result.macd.empty
        assert not result.signal.empty
        assert not result.histogram.empty
        assert isinstance(result.golden_cross, bool)

    def test_macd_validates_fast_less_than_slow(self, sample_df: pd.DataFrame) -> None:
        with pytest.raises(ValueError):
            calc_macd(sample_df, fast=26, slow=12)

    def test_adx_range(self, sample_df: pd.DataFrame) -> None:
        result = calc_adx(sample_df)
        valid = result.dropna()
        assert (valid >= 0).all()

    def test_sma_validates_none(self) -> None:
        with pytest.raises(TypeError):
            calc_sma(None, period=10)  # type: ignore[arg-type]

    def test_sma_validates_period_negative(self, sample_df: pd.DataFrame) -> None:
        with pytest.raises(ValueError):
            calc_sma(sample_df, period=-1)


class TestMomentumIndicators:
    def test_rsi_range(self, sample_df: pd.DataFrame) -> None:
        result = calc_rsi(sample_df)
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_rsi_oversold_detection(self) -> None:
        """하락 추세 데이터에서 RSI < 30 감지."""
        n = 50
        close = 50000.0 - np.arange(n) * 500  # 연속 하락
        df = pd.DataFrame({
            "open": close + 100,
            "high": close + 200,
            "low": close - 200,
            "close": close,
            "volume": np.ones(n) * 1_000_000,
        }, index=pd.date_range("2025-01-01", periods=n, freq="B"))

        rsi = calc_rsi(df, period=14)
        valid_rsi = rsi.dropna()
        assert len(valid_rsi) > 0
        assert valid_rsi.iloc[-1] < 30, (
            f"하락 추세에서 RSI가 30 이하여야 함. 실제: {valid_rsi.iloc[-1]:.1f}"
        )

    def test_stochastic_range(self, sample_df: pd.DataFrame) -> None:
        result = calc_stochastic(sample_df)
        valid_k = result.k.dropna()
        assert (valid_k >= 0).all()
        assert (valid_k <= 100).all()

    def test_stoch_rsi(self, sample_df: pd.DataFrame) -> None:
        result = calc_stoch_rsi(sample_df)
        valid = result.dropna()
        assert len(valid) > 0
        assert (valid >= 0).all()
        assert (valid <= 1).all()

    def test_cci_not_empty(self, sample_df: pd.DataFrame) -> None:
        result = calc_cci(sample_df)
        assert not result.dropna().empty

    def test_roc_not_empty(self, sample_df: pd.DataFrame) -> None:
        result = calc_roc(sample_df)
        assert not result.dropna().empty


class TestVolatilityIndicators:
    def test_bollinger_bands_order(self, sample_df: pd.DataFrame) -> None:
        result = calc_bollinger(sample_df)
        valid_idx = result.upper.dropna().index
        assert (result.upper[valid_idx] >= result.lower[valid_idx]).all()

    def test_bollinger_validates_std_dev(self, sample_df: pd.DataFrame) -> None:
        with pytest.raises(ValueError):
            calc_bollinger(sample_df, std_dev=-1.0)

    def test_atr_positive(self, sample_df: pd.DataFrame) -> None:
        result = calc_atr(sample_df)
        valid = result.dropna()
        assert (valid > 0).all()

    def test_donchian_order(self, sample_df: pd.DataFrame) -> None:
        result = calc_donchian(sample_df)
        valid_idx = result.upper.dropna().index
        assert (result.upper[valid_idx] >= result.lower[valid_idx]).all()


class TestVolumeIndicators:
    def test_volume_ma(self, sample_df: pd.DataFrame) -> None:
        result = calc_volume_ma(sample_df)
        assert not result.dropna().empty

    def test_obv_cumulative(self, sample_df: pd.DataFrame) -> None:
        result = calc_obv(sample_df)
        assert len(result) == len(sample_df)
        assert result.name == "obv"

    def test_mfi_range(self, sample_df: pd.DataFrame) -> None:
        result = calc_mfi(sample_df)
        valid = result.dropna()
        if not valid.empty:
            assert (valid >= 0).all()
            assert (valid <= 100).all()

    def test_vwap_positive(self, sample_df: pd.DataFrame) -> None:
        result = calc_vwap(sample_df)
        valid = result.dropna()
        assert (valid > 0).all()


class TestPriceConditions:
    def test_prev_high_breakout(self) -> None:
        n = 60
        close = np.ones(n) * 50000.0
        close[-1] = 60000.0  # 마지막 봉 신고가 돌파
        df = pd.DataFrame({
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.ones(n) * 1_000_000,
        }, index=pd.date_range("2025-01-01", periods=n, freq="B"))
        assert calc_prev_high_breakout(df, lookback=52) is True

    def test_prev_high_breakout_negative(self) -> None:
        """돌파 아닌 경우 False."""
        n = 60
        close = np.ones(n) * 50000.0
        df = pd.DataFrame({
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.ones(n) * 1_000_000,
        }, index=pd.date_range("2025-01-01", periods=n, freq="B"))
        assert calc_prev_high_breakout(df, lookback=52) is False

    def test_near_high(self) -> None:
        """52주 고가 근처 (3% 이내)."""
        n = 60
        close = np.ones(n) * 50000.0
        close[10] = 52000.0  # 고가
        close[-1] = 51500.0  # 현재가는 고가 대비 약 1% 이내
        df = pd.DataFrame({
            "open": close,
            "high": close,
            "low": close * 0.99,
            "close": close,
            "volume": np.ones(n) * 1_000_000,
        }, index=pd.date_range("2025-01-01", periods=n, freq="B"))
        assert calc_near_high(df, lookback=52, threshold_pct=3.0) is True

    def test_ma_cross_up(self) -> None:
        """MA 상향 돌파."""
        n = 250
        close = np.concatenate([
            np.ones(200) * 50000.0,
            np.linspace(50000.0, 55000.0, 50),  # 상승 추세
        ])
        df = pd.DataFrame({
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": np.ones(n) * 1_000_000,
        }, index=pd.date_range("2025-01-01", periods=n, freq="B"))
        result = calc_ma_cross(df, ma_type="sma", period=200)
        assert result.above_ma is True

    def test_price_condition_gt(self, sample_df: pd.DataFrame) -> None:
        current = float(sample_df["close"].iloc[-1])
        assert calc_price_condition(sample_df, ">", current - 1) is True
        assert calc_price_condition(sample_df, ">", current + 1) is False

    def test_price_condition_invalid_operator(self, sample_df: pd.DataFrame) -> None:
        with pytest.raises(ValueError):
            calc_price_condition(sample_df, "!=", 50000.0)  # type: ignore[arg-type]

    def test_gap_up_detection(self) -> None:
        df = pd.DataFrame({
            "open": [50000.0, 52000.0],   # 4% 갭상승
            "high": [50500.0, 52500.0],
            "low": [49500.0, 51500.0],
            "close": [50000.0, 52000.0],
            "volume": [1_000_000.0, 1_000_000.0],
        }, index=pd.date_range("2025-01-01", periods=2, freq="B"))
        result = calc_gap(df, threshold_pct=2.0)
        assert result.is_gap_up is True
        assert result.gap_pct == 4.0

    def test_gap_down_detection(self) -> None:
        df = pd.DataFrame({
            "open": [50000.0, 48000.0],   # 4% 갭하락
            "high": [50500.0, 48500.0],
            "low": [49500.0, 47500.0],
            "close": [50000.0, 48000.0],
            "volume": [1_000_000.0, 1_000_000.0],
        }, index=pd.date_range("2025-01-01", periods=2, freq="B"))
        result = calc_gap(df, threshold_pct=2.0)
        assert result.is_gap_down is True
        assert result.gap_pct == -4.0


class TestInputValidation:
    """공통 입력 검증 테스트 — AL.type_valid 4-item 적용."""

    def test_calc_sma_none(self) -> None:
        with pytest.raises(TypeError):
            calc_sma(None, period=10)  # type: ignore[arg-type]

    def test_calc_rsi_wrong_type(self) -> None:
        with pytest.raises(TypeError):
            calc_rsi("not a df", period=14)  # type: ignore[arg-type]

    def test_calc_atr_missing_columns(self) -> None:
        df = pd.DataFrame({"close": [1.0, 2.0]})
        with pytest.raises(ValueError):
            calc_atr(df, period=5)

    def test_calc_bollinger_zero_period(self, sample_df: pd.DataFrame) -> None:
        with pytest.raises(ValueError):
            calc_bollinger(sample_df, period=0)
