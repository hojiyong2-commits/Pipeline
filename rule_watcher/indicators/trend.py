# [Purpose]: 추세 인디케이터(SMA, EMA, MACD, ADX) 순수 계산 함수 제공.
# [Assumptions]: 입력 DataFrame은 ['open','high','low','close','volume'] 컬럼을 가지며 시간순(오름차순) 정렬됨.
# [Vulnerability & Risks]: 데이터 길이가 period보다 짧으면 빈 Series 반환 (호출자가 .empty/.dropna() 확인 필요). NaN division은 numpy 경고만 발생.
# [Improvement]: numba JIT 적용으로 대용량 종목 일괄 계산 가속, TA-Lib 호환 옵션.
"""추세 인디케이터: SMA, EMA, MACD, ADX."""
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
        # negative not allowed: 이동평균 period는 양수여야 함 (0 또는 음수는 의미 없음)
        raise ValueError(f"{name} must be > 0, got {period}")


def calc_sma(df: pd.DataFrame, period: int = 20, col: str = _DEFAULT_COL) -> pd.Series:
    """단순이동평균 (SMA).

    Args:
        df: OHLCV DataFrame.
        period: 이동평균 기간 (양수).
        col: 계산 기준 컬럼명 (기본 'close').
    Returns:
        SMA Series (길이는 입력과 동일, 초기 period-1 봉은 NaN).
    """
    _validate_df(df, {col})
    _validate_period(period)
    if len(df) < period:
        return pd.Series(dtype=float, name=f"sma_{period}")
    return df[col].rolling(window=period).mean().rename(f"sma_{period}")


def calc_ema(df: pd.DataFrame, period: int = 20, col: str = _DEFAULT_COL) -> pd.Series:
    """지수이동평균 (EMA)."""
    _validate_df(df, {col})
    _validate_period(period)
    if len(df) < period:
        return pd.Series(dtype=float, name=f"ema_{period}")
    return df[col].ewm(span=period, adjust=False).mean().rename(f"ema_{period}")


@dataclass
class MACDResult:
    macd: pd.Series
    signal: pd.Series
    histogram: pd.Series
    golden_cross: bool = False  # 가장 최근 봉 기준


def calc_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    col: str = _DEFAULT_COL,
) -> MACDResult:
    """MACD (Moving Average Convergence Divergence).

    Args:
        df: OHLCV DataFrame.
        fast: 단기 EMA 기간 (양수).
        slow: 장기 EMA 기간 (양수, fast보다 커야 함).
        signal: 시그널 EMA 기간 (양수).
        col: 계산 기준 컬럼명.
    Returns:
        MACDResult (macd, signal, histogram + 최근 봉 골든크로스 여부).
    """
    _validate_df(df, {col})
    _validate_period(fast, "fast")
    _validate_period(slow, "slow")
    _validate_period(signal, "signal")
    if fast >= slow:
        raise ValueError(f"fast({fast}) must be < slow({slow})")

    if len(df) < slow:
        empty = pd.Series(dtype=float)
        return MACDResult(empty, empty, empty)

    ema_fast = df[col].ewm(span=fast, adjust=False).mean()
    ema_slow = df[col].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    # 골든크로스: MACD가 Signal을 상향 돌파 (최근 봉 기준)
    golden_cross = False
    if len(macd_line) >= 2:
        prev_diff = macd_line.iloc[-2] - signal_line.iloc[-2]
        curr_diff = macd_line.iloc[-1] - signal_line.iloc[-1]
        if pd.notna(prev_diff) and pd.notna(curr_diff):
            golden_cross = bool(prev_diff < 0 and curr_diff >= 0)

    return MACDResult(
        macd=macd_line.rename("macd"),
        signal=signal_line.rename("macd_signal"),
        histogram=histogram.rename("macd_hist"),
        golden_cross=golden_cross,
    )


def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index (ADX) — 추세 강도 (0~100).

    Args:
        df: OHLCV DataFrame (high/low/close 필수).
        period: ADX 기간 (양수).
    Returns:
        ADX Series.
    """
    _validate_df(df, {"high", "low", "close"})
    _validate_period(period)

    if len(df) < period + 1:
        return pd.Series(dtype=float, name="adx")

    high = df["high"]
    low = df["low"]
    close = df["close"]

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

    dm_plus = high.diff()
    dm_minus = -low.diff()
    dm_plus = dm_plus.where((dm_plus > 0) & (dm_plus > dm_minus), 0.0)
    dm_minus = dm_minus.where((dm_minus > 0) & (dm_minus > dm_plus), 0.0)

    atr = tr.ewm(span=period, adjust=False).mean()
    di_plus = 100 * (dm_plus.ewm(span=period, adjust=False).mean() / atr.replace(0, np.nan))
    di_minus = 100 * (dm_minus.ewm(span=period, adjust=False).mean() / atr.replace(0, np.nan))

    dx = 100 * ((di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan))
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx.rename("adx")
