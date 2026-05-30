# [Purpose]: 거래량 인디케이터(Volume MA, OBV, MFI, VWAP) 순수 계산.
# [Assumptions]: volume 컬럼은 float/int 양수. 누적 VWAP은 세션 단위가 아닌 전체 누적 (intraday 세션 분할은 호출자가 사전 분리).
# [Vulnerability & Risks]: cumsum / division zero 방어. MFI는 typical_price 변화 방향에 따라 분기.
# [Improvement]: rolling VWAP, 세션 리셋 옵션, Volume Profile.
"""거래량 인디케이터: 거래량 이평, OBV, MFI, VWAP."""
from __future__ import annotations

import numpy as np
import pandas as pd


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
        # negative not allowed: volume 관련 period는 양수
        raise ValueError(f"{name} must be > 0, got {period}")


def calc_volume_ma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """거래량 이동평균.

    Args:
        df: OHLCV DataFrame (volume 필수).
        period: 이동평균 기간 (양수).
    Returns:
        거래량 MA Series.
    """
    _validate_df(df, {"volume"})
    _validate_period(period)
    if len(df) < period:
        return pd.Series(dtype=float, name=f"vol_ma_{period}")
    return df["volume"].rolling(period).mean().rename(f"vol_ma_{period}")


def calc_obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume (OBV) — 종가 변동 방향에 거래량 가중 누적.

    Args:
        df: OHLCV DataFrame (close/volume 필수).
    Returns:
        OBV Series.
    """
    _validate_df(df, {"close", "volume"})
    if len(df) < 2:
        return pd.Series(dtype=float, name="obv")

    direction = df["close"].diff().apply(
        lambda x: 1 if x > 0 else (-1 if x < 0 else 0)
    )
    obv = (df["volume"] * direction).cumsum()
    return obv.rename("obv")


def calc_mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Money Flow Index (MFI) — RSI의 거래량 가중 변종.

    Args:
        df: OHLCV DataFrame (high/low/close/volume 필수).
        period: MFI 기간 (양수).
    Returns:
        MFI Series (0~100).
    """
    _validate_df(df, {"high", "low", "close", "volume"})
    _validate_period(period)

    if len(df) < period + 1:
        return pd.Series(dtype=float, name=f"mfi_{period}")

    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    money_flow = typical_price * df["volume"]

    pos_flow = money_flow.where(typical_price > typical_price.shift(1), 0.0)
    neg_flow = money_flow.where(typical_price < typical_price.shift(1), 0.0)

    pos_sum = pos_flow.rolling(period).sum()
    neg_sum = neg_flow.rolling(period).sum()

    mfr = pos_sum / neg_sum.replace(0, np.nan)
    mfi = 100 - (100 / (1 + mfr))
    return mfi.rename(f"mfi_{period}")


def calc_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP (Volume Weighted Average Price) — 누적 기준.

    Args:
        df: OHLCV DataFrame (high/low/close/volume 필수).
    Returns:
        VWAP Series.
    """
    _validate_df(df, {"high", "low", "close", "volume"})
    if df.empty:
        return pd.Series(dtype=float, name="vwap")

    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical_price * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()

    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
    return vwap.rename("vwap")
