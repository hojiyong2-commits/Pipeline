# [Purpose]: indicators 패키지의 공개 인터페이스를 한 곳에서 re-export.
# [Assumptions]: 순환 import 없음. dataclass 결과 객체도 함께 export 가능.
# [Vulnerability & Risks]: __all__ 누락 시 IDE 자동 완성 부정확. 명시적으로 관리.
# [Improvement]: 인디케이터 등록 dict + 동적 dispatch로 룰 엔진에 노출.
"""인디케이터 엔진 — UI와 분리된 순수 계산 모듈."""
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

__all__ = [
    # trend
    "calc_sma", "calc_ema", "calc_macd", "calc_adx",
    # momentum
    "calc_rsi", "calc_stochastic", "calc_stoch_rsi", "calc_cci", "calc_roc",
    # volatility
    "calc_bollinger", "calc_atr", "calc_donchian",
    # volume
    "calc_volume_ma", "calc_obv", "calc_mfi", "calc_vwap",
    # price
    "calc_prev_high_breakout", "calc_near_high", "calc_ma_cross", "calc_gap", "calc_price_condition",
]
