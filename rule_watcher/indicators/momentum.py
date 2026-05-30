# [Purpose]: 모멘텀 인디케이터(RSI, Stochastic, StochRSI, CCI, ROC) 순수 계산.
# [Assumptions]: 입력 DataFrame은 시간 순서 정렬, RSI/Stoch는 EWM 방식 사용 (TradingView 호환).
# [Vulnerability & Risks]: avg_loss=0인 강한 상승 구간에서 RSI=100 한계값, 보정 위해 replace(0, np.nan) 적용.
# [Improvement]: 다양한 RSI variants(Wilder smoothing 강제 옵션), 다중 시간프레임 지원.
"""모멘텀 인디케이터: RSI, Stochastic, StochRSI, CCI, ROC."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_DEFAULT_COL = "close"


def _validate_df(df: pd.DataFrame, required_cols: set[str]) -> None:
    """공통 입력 검증."""
    if df is None:
        raise TypeError("df must not be None")
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"df must be pd.DataFrame, got {type(df).__name__}")
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"df missing required columns: {sorted(missing)}")


def _validate_period(period: int, name: str = "period") -> None:
    """period 양수 검증."""
    if period is None:
        raise TypeError(f"{name} must not be None")
    if not isinstance(period, int) or isinstance(period, bool):
        raise TypeError(f"{name} must be int, got {type(period).__name__}")
    if period <= 0:
        # negative not allowed: RSI/Stoch period는 양수여야 함
        raise ValueError(f"{name} must be > 0, got {period}")


def calc_rsi(df: pd.DataFrame, period: int = 14, col: str = _DEFAULT_COL) -> pd.Series:
    """RSI (Relative Strength Index) — 0~100 범위.

    Args:
        df: OHLCV DataFrame.
        period: RSI 기간 (양수).
        col: 계산 기준 컬럼.
    Returns:
        RSI Series.
    """
    _validate_df(df, {col})
    _validate_period(period)

    if len(df) < period + 1:
        return pd.Series(dtype=float, name=f"rsi_{period}")

    delta = df[col].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # avg_loss=0인 경우 (강한 상승): RSI=100
    rsi = rsi.where(avg_loss != 0, 100.0)
    return rsi.rename(f"rsi_{period}")


@dataclass
class StochasticResult:
    k: pd.Series
    d: pd.Series


def calc_stochastic(
    df: pd.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
) -> StochasticResult:
    """Stochastic Oscillator (%K, %D).

    Args:
        df: OHLCV DataFrame (high/low/close 필수).
        k_period: %K 기간 (양수).
        d_period: %D 기간 (양수).
    Returns:
        StochasticResult (k, d).
    """
    _validate_df(df, {"high", "low", "close"})
    _validate_period(k_period, "k_period")
    _validate_period(d_period, "d_period")

    if len(df) < k_period:
        empty = pd.Series(dtype=float)
        return StochasticResult(empty, empty)

    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()

    denom = (high_max - low_min).replace(0, np.nan)
    k = 100 * (df["close"] - low_min) / denom
    d = k.rolling(d_period).mean()

    return StochasticResult(k=k.rename("stoch_k"), d=d.rename("stoch_d"))


def calc_stoch_rsi(
    df: pd.DataFrame,
    rsi_period: int = 14,
    stoch_period: int = 14,
    col: str = _DEFAULT_COL,
) -> pd.Series:
    """Stochastic RSI — RSI에 Stoch 적용.

    Args:
        df: OHLCV DataFrame.
        rsi_period: RSI 기간.
        stoch_period: Stoch 기간.
        col: 계산 기준 컬럼.
    Returns:
        StochRSI Series (0~1 범위).
    """
    _validate_df(df, {col})
    _validate_period(rsi_period, "rsi_period")
    _validate_period(stoch_period, "stoch_period")

    rsi = calc_rsi(df, rsi_period, col)
    if rsi.empty or len(rsi.dropna()) < stoch_period:
        return pd.Series(dtype=float, name="stoch_rsi")

    rsi_min = rsi.rolling(stoch_period).min()
    rsi_max = rsi.rolling(stoch_period).max()
    denom = (rsi_max - rsi_min).replace(0, np.nan)
    stoch_rsi = (rsi - rsi_min) / denom
    return stoch_rsi.rename("stoch_rsi")


def calc_cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Commodity Channel Index (CCI) — typical price 기반.

    Args:
        df: OHLCV DataFrame (high/low/close 필수).
        period: CCI 기간 (양수).
    Returns:
        CCI Series.
    """
    _validate_df(df, {"high", "low", "close"})
    _validate_period(period)

    if len(df) < period:
        return pd.Series(dtype=float, name=f"cci_{period}")

    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    sma_tp = typical_price.rolling(period).mean()
    mean_dev = typical_price.rolling(period).apply(
        lambda x: np.mean(np.abs(x - x.mean())), raw=True
    )
    cci = (typical_price - sma_tp) / (0.015 * mean_dev.replace(0, np.nan))
    return cci.rename(f"cci_{period}")


def calc_roc(df: pd.DataFrame, period: int = 12, col: str = _DEFAULT_COL) -> pd.Series:
    """Rate of Change (ROC) — 변화율 (%).

    Args:
        df: OHLCV DataFrame.
        period: ROC 기간 (양수).
        col: 계산 기준 컬럼.
    Returns:
        ROC Series (%).
    """
    _validate_df(df, {col})
    _validate_period(period)

    if len(df) < period + 1:
        return pd.Series(dtype=float, name=f"roc_{period}")

    prev = df[col].shift(period)
    roc = ((df[col] - prev) / prev.replace(0, np.nan)) * 100
    return roc.rename(f"roc_{period}")
