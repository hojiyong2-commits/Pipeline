# [Purpose]: 변동성 인디케이터(Bollinger Bands, ATR, Donchian Channel) 순수 계산.
# [Assumptions]: ATR은 EWM 방식 (Wilder smoothing 근사). Bollinger는 모표준편차(ddof=0) 사용.
# [Vulnerability & Risks]: middle=0인 비정상 데이터에서 bandwidth div by zero 방어.
# [Improvement]: ATR Wilder smoothing 엄밀 모드, Keltner Channel 추가.
"""변동성 인디케이터: Bollinger Bands, ATR, Donchian Channel."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_DEFAULT_COL = "close"


def _validate_df(df: pd.DataFrame, required_cols: set[str]) -> None:
    if df is None:
        raise TypeError("df must not be None")
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"df must be pd.DataFrame, got {type(df).__name__}")
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"df missing required columns: {sorted(missing)}")


def _validate_period(period: int, name: str = "period") -> None:
    if period is None:
        raise TypeError(f"{name} must not be None")
    if not isinstance(period, int) or isinstance(period, bool):
        raise TypeError(f"{name} must be int, got {type(period).__name__}")
    if period <= 0:
        # negative not allowed: 변동성 period는 양수
        raise ValueError(f"{name} must be > 0, got {period}")


@dataclass
class BollingerResult:
    upper: pd.Series
    middle: pd.Series
    lower: pd.Series
    bandwidth: pd.Series


def calc_bollinger(
    df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
    col: str = _DEFAULT_COL,
) -> BollingerResult:
    """볼린저 밴드 (Bollinger Bands).

    Args:
        df: OHLCV DataFrame.
        period: 이동평균 기간 (양수).
        std_dev: 표준편차 배수 (양수).
        col: 계산 기준 컬럼.
    Returns:
        BollingerResult (upper, middle, lower, bandwidth).
    """
    _validate_df(df, {col})
    _validate_period(period)
    if std_dev is None:
        raise TypeError("std_dev must not be None")
    if not isinstance(std_dev, (int, float)) or isinstance(std_dev, bool):
        raise TypeError(f"std_dev must be numeric, got {type(std_dev).__name__}")
    if std_dev <= 0:
        # negative not allowed: 표준편차 배수는 양수
        raise ValueError(f"std_dev must be > 0, got {std_dev}")

    if len(df) < period:
        empty = pd.Series(dtype=float)
        return BollingerResult(empty, empty, empty, empty)

    middle = df[col].rolling(period).mean()
    std = df[col].rolling(period).std(ddof=0)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    bandwidth = ((upper - lower) / middle.replace(0, np.nan)) * 100

    return BollingerResult(
        upper=upper.rename("bb_upper"),
        middle=middle.rename("bb_middle"),
        lower=lower.rename("bb_lower"),
        bandwidth=bandwidth.rename("bb_bandwidth"),
    )


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (ATR) — 변동성 지표.

    Args:
        df: OHLCV DataFrame (high/low/close 필수).
        period: ATR 기간 (양수).
    Returns:
        ATR Series.
    """
    _validate_df(df, {"high", "low", "close"})
    _validate_period(period)

    if len(df) < period + 1:
        return pd.Series(dtype=float, name=f"atr_{period}")

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)

    atr = tr.ewm(com=period - 1, adjust=False).mean()
    return atr.rename(f"atr_{period}")


@dataclass
class DonchianResult:
    upper: pd.Series
    lower: pd.Series
    middle: pd.Series


def calc_donchian(df: pd.DataFrame, period: int = 20) -> DonchianResult:
    """Donchian Channel — 고가/저가 채널.

    Args:
        df: OHLCV DataFrame (high/low 필수).
        period: 채널 기간 (양수).
    Returns:
        DonchianResult (upper, lower, middle).
    """
    _validate_df(df, {"high", "low"})
    _validate_period(period)

    if len(df) < period:
        empty = pd.Series(dtype=float)
        return DonchianResult(empty, empty, empty)

    upper = df["high"].rolling(period).max()
    lower = df["low"].rolling(period).min()
    middle = (upper + lower) / 2

    return DonchianResult(
        upper=upper.rename("donchian_upper"),
        lower=lower.rename("donchian_lower"),
        middle=middle.rename("donchian_middle"),
    )
