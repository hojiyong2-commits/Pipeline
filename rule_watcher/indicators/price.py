# [Purpose]: 가격 조건 인디케이터(전고점 돌파, 신고가 근접, MA 돌파, 갭, 특정 가격) 평가.
# [Assumptions]: 입력 DataFrame은 시간순 정렬 (마지막 행이 최신). 모든 함수는 bool 또는 단일 dataclass 반환.
# [Vulnerability & Risks]: lookback 기간보다 데이터가 짧으면 False 반환 — 호출자가 데이터 충분성 사전 확인 권장. div by zero 방어.
# [Improvement]: lookback 다중 비교 (52주 + 200일 동시), 갭 거래량 보정.
"""가격 조건 인디케이터: 전고점 돌파, 신고가, MA 돌파, 갭, 특정 가격 조건."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from rule_watcher.indicators.trend import calc_ema, calc_sma


def _validate_df(df: pd.DataFrame, required_cols: set[str]) -> None:
    if df is None:
        raise TypeError("df must not be None")
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"df must be pd.DataFrame, got {type(df).__name__}")
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"df missing required columns: {sorted(missing)}")


def _validate_lookback(lookback: int) -> None:
    if lookback is None:
        raise TypeError("lookback must not be None")
    if not isinstance(lookback, int) or isinstance(lookback, bool):
        raise TypeError(f"lookback must be int, got {type(lookback).__name__}")
    if lookback <= 0:
        # negative not allowed: lookback 기간은 양수
        raise ValueError(f"lookback must be > 0, got {lookback}")


def calc_prev_high_breakout(df: pd.DataFrame, lookback: int = 52) -> bool:
    """전고점 돌파 여부 (lookback 기간 내 최고가를 현재 종가가 돌파).

    Args:
        df: OHLCV DataFrame (high/close 필수).
        lookback: 비교 기간 (양수).
    Returns:
        True: 현재 종가가 lookback 이전 봉들의 최고가보다 큼.
    """
    _validate_df(df, {"high", "close"})
    _validate_lookback(lookback)

    if len(df) < lookback + 1:
        return False

    prev_high = df["high"].iloc[-(lookback + 1):-1].max()
    current_close = df["close"].iloc[-1]
    if pd.isna(prev_high) or pd.isna(current_close):
        return False
    return bool(current_close > prev_high)


def calc_near_high(df: pd.DataFrame, lookback: int = 52, threshold_pct: float = 5.0) -> bool:
    """신고가 근접 여부 (전고점 대비 threshold_pct% 이내).

    Args:
        df: OHLCV DataFrame (high/close 필수).
        lookback: 비교 기간 (양수).
        threshold_pct: 근접 기준 % (양수).
    Returns:
        True: 현재 종가가 lookback 기간 최고가의 threshold_pct% 이내.
    """
    _validate_df(df, {"high", "close"})
    _validate_lookback(lookback)
    if threshold_pct is None:
        raise TypeError("threshold_pct must not be None")
    if not isinstance(threshold_pct, (int, float)) or isinstance(threshold_pct, bool):
        raise TypeError(f"threshold_pct must be numeric, got {type(threshold_pct).__name__}")
    if threshold_pct <= 0:
        # negative not allowed: 근접 기준 %는 양수
        raise ValueError(f"threshold_pct must be > 0, got {threshold_pct}")

    if len(df) < lookback:
        return False

    period_high = df["high"].tail(lookback).max()
    current_close = df["close"].iloc[-1]

    if pd.isna(period_high) or pd.isna(current_close) or period_high == 0:
        return False

    gap_pct = abs(period_high - current_close) / period_high * 100
    return bool(gap_pct <= threshold_pct)


@dataclass
class MACrossResult:
    is_cross_up: bool    # 현재 봉에서 상향 돌파
    is_cross_down: bool  # 현재 봉에서 하향 돌파
    above_ma: bool       # 현재 종가 > MA


def calc_ma_cross(
    df: pd.DataFrame,
    ma_type: Literal["sma", "ema"] = "sma",
    period: int = 200,
) -> MACrossResult:
    """이동평균선 상향/하향 돌파 감지 (최근 봉 기준).

    Args:
        df: OHLCV DataFrame (close 필수).
        ma_type: 'sma' 또는 'ema'.
        period: MA 기간 (양수).
    Returns:
        MACrossResult (is_cross_up, is_cross_down, above_ma).
    """
    _validate_df(df, {"close"})
    if ma_type not in ("sma", "ema"):
        raise ValueError(f"ma_type must be 'sma' or 'ema', got {ma_type!r}")
    if period is None:
        raise TypeError("period must not be None")
    if not isinstance(period, int) or isinstance(period, bool):
        raise TypeError(f"period must be int, got {type(period).__name__}")
    if period <= 0:
        # negative not allowed: MA period는 양수
        raise ValueError(f"period must be > 0, got {period}")

    if len(df) < period + 1:
        return MACrossResult(False, False, False)

    ma = calc_sma(df, period) if ma_type == "sma" else calc_ema(df, period)

    if ma.empty or pd.isna(ma.iloc[-1]) or pd.isna(ma.iloc[-2]):
        return MACrossResult(False, False, False)

    prev_close = df["close"].iloc[-2]
    curr_close = df["close"].iloc[-1]
    prev_ma = ma.iloc[-2]
    curr_ma = ma.iloc[-1]

    above_ma = bool(curr_close > curr_ma)
    is_cross_up = bool(prev_close <= prev_ma and curr_close > curr_ma)
    is_cross_down = bool(prev_close >= prev_ma and curr_close < curr_ma)

    return MACrossResult(
        is_cross_up=is_cross_up,
        is_cross_down=is_cross_down,
        above_ma=above_ma,
    )


@dataclass
class GapResult:
    is_gap_up: bool
    is_gap_down: bool
    gap_pct: float


def calc_gap(df: pd.DataFrame, threshold_pct: float = 2.0) -> GapResult:
    """갭상승/갭하락 감지 (전일 종가 → 당일 시가).

    Args:
        df: OHLCV DataFrame (open/close 필수).
        threshold_pct: 갭 기준 % (양수).
    Returns:
        GapResult (is_gap_up, is_gap_down, gap_pct).
    """
    _validate_df(df, {"open", "close"})
    if threshold_pct is None:
        raise TypeError("threshold_pct must not be None")
    if not isinstance(threshold_pct, (int, float)) or isinstance(threshold_pct, bool):
        raise TypeError(f"threshold_pct must be numeric, got {type(threshold_pct).__name__}")
    if threshold_pct <= 0:
        # negative not allowed: 갭 기준 %는 양수
        raise ValueError(f"threshold_pct must be > 0, got {threshold_pct}")

    if len(df) < 2:
        return GapResult(False, False, 0.0)

    prev_close = df["close"].iloc[-2]
    curr_open = df["open"].iloc[-1]

    if pd.isna(prev_close) or pd.isna(curr_open) or prev_close == 0:
        return GapResult(False, False, 0.0)

    gap_pct = (curr_open - prev_close) / prev_close * 100

    return GapResult(
        is_gap_up=bool(gap_pct >= threshold_pct),
        is_gap_down=bool(gap_pct <= -threshold_pct),
        gap_pct=round(float(gap_pct), 2),
    )


def calc_price_condition(
    df: pd.DataFrame,
    operator: Literal[">", ">=", "<", "<=", "=="],
    threshold: float,
) -> bool:
    """특정 가격 조건 평가 (현재 종가 기준).

    Args:
        df: OHLCV DataFrame (close 필수).
        operator: 비교 연산자.
        threshold: 비교 기준값 (음수 허용 — 가격이 0 이하면 거의 없지만 표시 가능).
    Returns:
        조건 만족 시 True. 음수 임계값도 허용 (수익률/델타 등 일반화 가능성).
    """
    _validate_df(df, {"close"})
    if operator not in (">", ">=", "<", "<=", "=="):
        raise ValueError(f"operator must be one of '>','>=','<','<=','==', got {operator!r}")
    if threshold is None:
        raise TypeError("threshold must not be None")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        raise TypeError(f"threshold must be numeric, got {type(threshold).__name__}")
    # threshold는 음수 허용 — 일반화된 비교 조건 지원 (예: 변화율, 델타)

    if df.empty:
        return False

    current_price = float(df["close"].iloc[-1])
    if pd.isna(current_price):
        return False

    ops = {
        ">": current_price > threshold,
        ">=": current_price >= threshold,
        "<": current_price < threshold,
        "<=": current_price <= threshold,
        "==": abs(current_price - threshold) < 1.0,
    }
    return bool(ops.get(operator, False))
