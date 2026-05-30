# [Purpose]: RuleCondition / RuleGroup / RuleBook을 OHLCV DataFrame에 평가하고
#            (충족 여부, 한국어 설명)을 반환한다.
# [Assumptions]:
#   - df는 ['open','high','low','close','volume'] 컬럼을 가진 시간순 DataFrame.
#   - 인디케이터 함수들은 rule_watcher.indicators.* 의 검증된 시그니처 사용.
#   - 데이터 길이가 인디케이터의 요구 기간보다 짧으면 빈 Series/NaN 반환 → "데이터 부족".
# [Vulnerability & Risks]:
#   - params 키 누락 시 기본값으로 fallback — 사용자 의도와 다를 수 있음.
#   - bool 인디케이터의 operator는 is_true/==/!= 만 지원 (rule_model에서 차단).
#   - NaN 비교는 항상 False로 처리 — 명시적 "데이터 부족" 메시지로 사용자에게 전달.
# [Improvement]:
#   - 인디케이터 dispatch dict로 분기 단순화.
#   - 캐싱 (같은 df + 같은 params 호출 시 재사용).
#   - 멀티프레임 (1분/일봉 등) 조건 결합.
"""룰북 평가 엔진 — 조건/그룹/룰북 → (bool, str) 평가."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from rule_watcher.engine.rule_model import (
    INDICATOR_TYPES,
    RuleBook,
    RuleCondition,
    RuleGroup,
)
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
)
from rule_watcher.indicators.trend import (
    calc_adx,
    calc_ema,
    calc_macd,
    calc_sma,
)
from rule_watcher.indicators.volatility import calc_atr, calc_bollinger
from rule_watcher.indicators.volume import (
    calc_mfi,
    calc_obv,
    calc_volume_ma,
    calc_vwap,
)


class IndicatorNotSupportedError(ValueError):
    """등록되지 않은 인디케이터 평가 요청."""


_NO_DATA_MSG = "데이터 부족으로 계산 불가"


def _to_float_or_none(value: Any) -> Optional[float]:
    """pandas/numpy 스칼라를 안전하게 float으로 변환. NaN은 None으로."""
    if value is None:
        return None
    if pd.isna(value):
        return None
    try:
        return float(value)  # allowed: pandas/numpy 스칼라는 float() 호환
    except (TypeError, ValueError):
        return None


def _compare(value: float, operator: str, threshold: float) -> bool:
    """수치 비교 — 지원 연산자만. 비교 불가 시 False."""
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    if operator == ">":
        return value > threshold
    if operator == ">=":
        return value >= threshold
    if operator == "==":
        # 부동소수 동등은 작은 epsilon 적용 (가격/지표는 일반적으로 정수~소수점 2자리)
        return abs(value - threshold) < 1e-6
    if operator == "!=":
        return abs(value - threshold) >= 1e-6
    return False


def _op_text(operator: str) -> str:
    """연산자 한국어 표현."""
    mapping = {
        "<": "보다 낮음",
        "<=": "이하",
        ">": "보다 높음",
        ">=": "이상",
        "==": "와 같음",
        "!=": "와 다름",
        "is_true": "충족",
    }
    return mapping.get(operator, operator)


class RuleEvaluator:
    """단일 종목 OHLCV DataFrame에 RuleCondition / RuleGroup / RuleBook을 평가한다.

    모든 평가 메서드는 (bool, str) 튜플을 반환한다.
    - bool: 조건/그룹/룰북 충족 여부.
    - str: 사용자에게 표시할 한국어 설명 (예: "RSI(14)가 27.3으로 30보다 낮음").
    """

    # ---------- public API ----------

    def evaluate_condition(
        self, df: pd.DataFrame, condition: RuleCondition
    ) -> Tuple[bool, str]:
        """단일 조건 평가.

        Args:
            df: OHLCV DataFrame.
            condition: RuleCondition 인스턴스 (사전 validate 완료 가정, 내부에서 재검증).
        Returns:
            (충족 여부, 한국어 설명).
        Raises:
            TypeError: df 또는 condition이 None 또는 타입 불일치.
            IndicatorNotSupportedError: 등록되지 않은 인디케이터.
        """
        if df is None:
            raise TypeError("evaluate_condition: df must not be None")
        if not isinstance(df, pd.DataFrame):
            raise TypeError(
                f"evaluate_condition: df must be pd.DataFrame, "
                f"got {type(df).__name__}"
            )
        if condition is None:
            raise TypeError("evaluate_condition: condition must not be None")
        if not isinstance(condition, RuleCondition):
            raise TypeError(
                f"evaluate_condition: condition must be RuleCondition, "
                f"got {type(condition).__name__}"
            )

        # 외부에서 직접 생성된 RuleCondition도 안전하게 검증
        condition.validate()

        if condition.indicator not in INDICATOR_TYPES:
            raise IndicatorNotSupportedError(
                f"지원하지 않는 인디케이터: {condition.indicator!r}"
            )

        # 데이터 길이 최소 요건 (모든 인디케이터 공통 — 너무 짧으면 평가 무의미)
        if df.empty or len(df) < 2:
            return False, _NO_DATA_MSG

        try:
            return self._dispatch(df, condition)
        except (TypeError, ValueError) as exc:
            # 인디케이터 호출 단계에서 잘못된 params (예: period=음수) → 사용자 친화 메시지
            return False, f"룰 평가 실패: {exc}"

    def evaluate_group(
        self, df: pd.DataFrame, group: RuleGroup
    ) -> Tuple[bool, str]:
        """RuleGroup 평가. AND이면 전부 PASS, OR이면 하나라도 PASS.

        Args:
            df: OHLCV DataFrame.
            group: RuleGroup 인스턴스.
        Returns:
            (충족 여부, 한국어 설명 — 조건별 결과를 연결).
        """
        if group is None:
            raise TypeError("evaluate_group: group must not be None")
        if not isinstance(group, RuleGroup):
            raise TypeError(
                f"evaluate_group: group must be RuleGroup, got {type(group).__name__}"
            )

        group.validate()

        results = []
        explanations = []
        for cond in group.conditions:
            ok, reason = self.evaluate_condition(df, cond)
            results.append(ok)
            explanations.append(reason)

        if group.logic == "AND":
            matched = all(results)
        else:  # OR
            matched = any(results)

        connector = " AND " if group.logic == "AND" else " OR "
        combined = connector.join(explanations) if explanations else _NO_DATA_MSG
        prefix = "충족" if matched else "미충족"
        return matched, f"[{group.name}] {prefix}: {combined}"

    def evaluate_rulebook(
        self, df: pd.DataFrame, rulebook: RuleBook
    ) -> Tuple[bool, str]:
        """RuleBook 평가. 그룹들은 AND로 묶인다 (v1).

        Args:
            df: OHLCV DataFrame.
            rulebook: RuleBook 인스턴스.
        Returns:
            (충족 여부, 한국어 설명).
        """
        if rulebook is None:
            raise TypeError("evaluate_rulebook: rulebook must not be None")
        if not isinstance(rulebook, RuleBook):
            raise TypeError(
                f"evaluate_rulebook: rulebook must be RuleBook, "
                f"got {type(rulebook).__name__}"
            )

        rulebook.validate()

        results = []
        explanations = []
        for group in rulebook.groups:
            ok, reason = self.evaluate_group(df, group)
            results.append(ok)
            explanations.append(reason)

        matched = all(results)
        joined = " AND ".join(explanations) if explanations else _NO_DATA_MSG
        return matched, joined

    # ---------- internal dispatch ----------

    def _dispatch(
        self, df: pd.DataFrame, cond: RuleCondition
    ) -> Tuple[bool, str]:
        """인디케이터별 평가 로직 분기."""
        params: Dict[str, Any] = dict(cond.params)
        ind = cond.indicator

        # --- 모멘텀 ---
        if ind == "RSI":
            period = int(params.get("period", 14))
            series = calc_rsi(df, period=period)
            return self._eval_series_scalar(series, cond, label=f"RSI({period})")

        if ind == "STOCH_K":
            k_period = int(params.get("k_period", 14))
            d_period = int(params.get("d_period", 3))
            res = calc_stochastic(df, k_period=k_period, d_period=d_period)
            return self._eval_series_scalar(
                res.k, cond, label=f"Stochastic %K({k_period},{d_period})"
            )

        if ind == "STOCH_RSI_K":
            rsi_period = int(params.get("rsi_period", 14))
            stoch_period = int(params.get("stoch_period", 14))
            series = calc_stoch_rsi(
                df, rsi_period=rsi_period, stoch_period=stoch_period
            )
            return self._eval_series_scalar(
                series, cond, label=f"StochRSI K({rsi_period},{stoch_period})"
            )

        if ind == "CCI":
            period = int(params.get("period", 20))
            series = calc_cci(df, period=period)
            return self._eval_series_scalar(series, cond, label=f"CCI({period})")

        if ind == "ROC":
            period = int(params.get("period", 12))
            series = calc_roc(df, period=period)
            return self._eval_series_scalar(series, cond, label=f"ROC({period})")

        # --- 추세 ---
        if ind == "SMA":
            period = int(params.get("period", 20))
            series = calc_sma(df, period=period)
            return self._eval_series_scalar(series, cond, label=f"SMA({period})")

        if ind == "EMA":
            period = int(params.get("period", 20))
            series = calc_ema(df, period=period)
            return self._eval_series_scalar(series, cond, label=f"EMA({period})")

        if ind == "ADX":
            period = int(params.get("period", 14))
            series = calc_adx(df, period=period)
            return self._eval_series_scalar(series, cond, label=f"ADX({period})")

        if ind == "MACD_CROSS":
            fast = int(params.get("fast_period", 12))
            slow = int(params.get("slow_period", 26))
            signal = int(params.get("signal_period", 9))
            # lookback 옵션: 최근 N봉 내 cross 발생 여부.
            # 미지정/0 이하/데이터 길이 초과 시 데이터 전체에서 cross 발생 여부를 검사.
            lookback_raw = params.get("lookback", 0)
            try:
                lookback = int(lookback_raw)
            except (TypeError, ValueError):
                lookback = 0
            res = calc_macd(df, fast=fast, slow=slow, signal=signal)
            cross_found = self._detect_macd_cross(
                res.macd, res.signal, lookback=lookback
            )
            label = f"MACD({fast},{slow},{signal})"
            if lookback > 0:
                label = f"{label} 최근 {lookback}봉"
            return self._eval_bool(
                cross_found,
                cond,
                label_true=f"{label} 골든크로스 발생",
                label_false=f"{label} 골든크로스 미발생",
            )

        # --- 변동성 ---
        if ind == "BOLLINGER_UPPER":
            period = int(params.get("period", 20))
            std_dev = float(params.get("std_dev", 2.0))
            res = calc_bollinger(df, period=period, std_dev=std_dev)
            return self._eval_series_scalar(
                res.upper, cond, label=f"볼린저 상단({period},{std_dev})"
            )

        if ind == "BOLLINGER_LOWER":
            period = int(params.get("period", 20))
            std_dev = float(params.get("std_dev", 2.0))
            res = calc_bollinger(df, period=period, std_dev=std_dev)
            return self._eval_series_scalar(
                res.lower, cond, label=f"볼린저 하단({period},{std_dev})"
            )

        if ind == "ATR":
            period = int(params.get("period", 14))
            series = calc_atr(df, period=period)
            return self._eval_series_scalar(series, cond, label=f"ATR({period})")

        # --- 거래량 ---
        if ind == "VOLUME_MA_RATIO":
            period = int(params.get("period", 20))
            vol_ma = calc_volume_ma(df, period=period)
            return self._eval_volume_ratio(df, vol_ma, cond, period)

        if ind == "OBV":
            series = calc_obv(df)
            return self._eval_series_scalar(series, cond, label="OBV")

        if ind == "MFI":
            period = int(params.get("period", 14))
            series = calc_mfi(df, period=period)
            return self._eval_series_scalar(series, cond, label=f"MFI({period})")

        if ind == "VWAP":
            series = calc_vwap(df)
            return self._eval_series_scalar(series, cond, label="VWAP")

        # --- 가격 직접 ---
        if ind == "PRICE":
            close_val = _to_float_or_none(df["close"].iloc[-1])
            if close_val is None:
                return False, _NO_DATA_MSG
            ok = _compare(close_val, cond.operator, float(cond.threshold))
            return ok, (
                f"현재가가 {close_val:.2f}으로 "
                f"{cond.threshold:.2f}{_op_text(cond.operator)}"
            )

        if ind == "CHANGE_RATE":
            if len(df) < 2:
                return False, _NO_DATA_MSG
            curr = _to_float_or_none(df["close"].iloc[-1])
            prev = _to_float_or_none(df["close"].iloc[-2])
            if curr is None or prev is None or prev == 0:
                return False, _NO_DATA_MSG
            change_rate = (curr - prev) / prev * 100
            ok = _compare(change_rate, cond.operator, float(cond.threshold))
            return ok, (
                f"등락률이 {change_rate:.2f}%로 "
                f"{cond.threshold:.2f}%{_op_text(cond.operator)}"
            )

        if ind == "PREV_HIGH_BREAK":
            lookback = int(params.get("lookback", 52))
            result = calc_prev_high_breakout(df, lookback=lookback)
            return self._eval_bool(
                result,
                cond,
                label_true=f"전고점({lookback}봉) 돌파",
                label_false=f"전고점({lookback}봉) 미돌파",
            )

        if ind == "NEAR_HIGH":
            lookback = int(params.get("lookback", 52))
            threshold_pct = float(params.get("threshold_pct", 5.0))
            result = calc_near_high(
                df, lookback=lookback, threshold_pct=threshold_pct
            )
            return self._eval_bool(
                result,
                cond,
                label_true=f"{lookback}봉 신고가 {threshold_pct}% 이내 근접",
                label_false=f"{lookback}봉 신고가 {threshold_pct}% 밖",
            )

        if ind == "MA_CROSS_UP":
            ma_type_raw = params.get("ma_type", "sma")
            ma_type = str(ma_type_raw).lower()  # allowed: ma_type은 짧은 enum, 대소문자 정규화
            if ma_type not in ("sma", "ema"):
                return False, f"룰 평가 실패: ma_type은 'sma' 또는 'ema'여야 합니다, got {ma_type!r}"
            period = int(params.get("period", 200))
            res = calc_ma_cross(df, ma_type=ma_type, period=period)
            return self._eval_bool(
                res.is_cross_up,
                cond,
                label_true=f"{ma_type.upper()}({period}) 상향 돌파",
                label_false=f"{ma_type.upper()}({period}) 상향 돌파 없음",
            )

        if ind == "MA_CROSS_DOWN":
            ma_type_raw = params.get("ma_type", "sma")
            ma_type = str(ma_type_raw).lower()  # allowed: enum 정규화
            if ma_type not in ("sma", "ema"):
                return False, f"룰 평가 실패: ma_type은 'sma' 또는 'ema'여야 합니다, got {ma_type!r}"
            period = int(params.get("period", 200))
            res = calc_ma_cross(df, ma_type=ma_type, period=period)
            return self._eval_bool(
                res.is_cross_down,
                cond,
                label_true=f"{ma_type.upper()}({period}) 하향 돌파",
                label_false=f"{ma_type.upper()}({period}) 하향 돌파 없음",
            )

        if ind == "GAP_UP":
            threshold_pct = float(params.get("threshold_pct", 2.0))
            res = calc_gap(df, threshold_pct=threshold_pct)
            return self._eval_bool(
                res.is_gap_up,
                cond,
                label_true=f"갭상승 {res.gap_pct:.2f}% (기준 {threshold_pct}% 이상)",
                label_false=f"갭상승 미발생 (현재 {res.gap_pct:.2f}%)",
            )

        if ind == "GAP_DOWN":
            threshold_pct = float(params.get("threshold_pct", 2.0))
            res = calc_gap(df, threshold_pct=threshold_pct)
            return self._eval_bool(
                res.is_gap_down,
                cond,
                label_true=f"갭하락 {res.gap_pct:.2f}% (기준 -{threshold_pct}% 이하)",
                label_false=f"갭하락 미발생 (현재 {res.gap_pct:.2f}%)",
            )

        # 이론적으로 도달 불가 (INDICATOR_TYPES 사전 검사 통과 시)
        raise IndicatorNotSupportedError(
            f"내부 디스패치 누락: {ind!r} (INDICATOR_TYPES에는 있으나 _dispatch 분기 없음)"
        )

    # ---------- helpers ----------

    def _eval_series_scalar(
        self, series: pd.Series, cond: RuleCondition, label: str
    ) -> Tuple[bool, str]:
        """Series의 마지막 값 vs threshold 비교."""
        if series is None or len(series) == 0:
            return False, _NO_DATA_MSG
        last_val = _to_float_or_none(series.iloc[-1])
        if last_val is None:
            return False, _NO_DATA_MSG
        threshold = float(cond.threshold)
        ok = _compare(last_val, cond.operator, threshold)
        return ok, (
            f"{label}가 {last_val:.2f}로 "
            f"{threshold:.2f}{_op_text(cond.operator)}"
        )

    def _eval_bool(
        self,
        value: bool,
        cond: RuleCondition,
        label_true: str,
        label_false: str,
    ) -> Tuple[bool, str]:
        """bool 인디케이터 결과 + operator(is_true/==/!=) 평가."""
        observed = bool(value)
        op = cond.operator

        if op == "is_true":
            target = True
        elif op == "==":
            target = bool(cond.threshold != 0)  # 0이면 False, 그 외는 True
        elif op == "!=":
            target = not bool(cond.threshold != 0)
        else:
            # rule_model validate에서 차단되지만 안전망
            return False, (
                f"룰 평가 실패: bool 인디케이터는 'is_true'/'=='/'!='만 지원합니다, "
                f"got {op!r}"
            )

        matched = observed == target
        return matched, (label_true if observed else label_false)

    def _detect_macd_cross(
        self,
        macd: pd.Series,
        signal: pd.Series,
        lookback: int = 0,
    ) -> bool:
        """MACD가 Signal을 상향 돌파(golden cross)했는지 검사.

        Args:
            macd: MACD line Series.
            signal: Signal line Series.
            lookback: 0 또는 음수면 데이터 전체, 양수면 최근 lookback봉만 검사.
        Returns:
            cross가 한 번이라도 발생하면 True.
        """
        if macd is None or signal is None or len(macd) < 2 or len(signal) < 2:
            return False
        diff = macd - signal
        prev_diff = diff.shift(1)
        cross_mask = (prev_diff < 0) & (diff >= 0)
        # NaN은 False로 처리 (Series boolean 연산에서 NaN은 False가 됨)
        cross_mask = cross_mask.fillna(False)
        if lookback <= 0 or lookback >= len(cross_mask):
            return bool(cross_mask.any())
        # 최근 lookback봉만 검사
        return bool(cross_mask.tail(lookback).any())

    def _eval_volume_ratio(
        self,
        df: pd.DataFrame,
        vol_ma: pd.Series,
        cond: RuleCondition,
        period: int,
    ) -> Tuple[bool, str]:
        """VOLUME_MA_RATIO = 현재 거래량 / 거래량 이동평균."""
        if vol_ma is None or len(vol_ma) == 0:
            return False, _NO_DATA_MSG
        ma_val = _to_float_or_none(vol_ma.iloc[-1])
        cur_vol = _to_float_or_none(df["volume"].iloc[-1])
        if ma_val is None or cur_vol is None or ma_val == 0:
            return False, _NO_DATA_MSG
        ratio = cur_vol / ma_val
        ok = _compare(ratio, cond.operator, float(cond.threshold))
        return ok, (
            f"거래량/거래량MA({period}) 비율이 {ratio:.2f}로 "
            f"{cond.threshold:.2f}{_op_text(cond.operator)}"
        )


if __name__ == "__main__":
    # 자가 검증 — 강한 하락 시계열에서 RSI<30 검출, 약한 데이터에서는 "데이터 부족"
    import pandas as pd

    falling_close = [55000 - i * 500 for i in range(20)]
    df = pd.DataFrame(
        {
            "open": [c + 200 for c in falling_close],
            "high": [c + 500 for c in falling_close],
            "low": [c - 200 for c in falling_close],
            "close": falling_close,
            "volume": [1_000_000 - i * 30_000 for i in range(20)],
        }
    )

    ev = RuleEvaluator()
    cond_rsi = RuleCondition(
        indicator="RSI",
        operator="<",
        threshold=30.0,
        params={"period": 14},
    )
    ok, reason = ev.evaluate_condition(df, cond_rsi)
    assert ok is True, f"강한 하락 데이터에서 RSI<30이 True여야 함: ({ok}, {reason})"
    assert "RSI(14)" in reason, f"한국어 설명에 RSI(14) 포함 필요: {reason!r}"

    # 너무 짧은 데이터 → 데이터 부족
    short_df = df.head(1)
    ok2, reason2 = ev.evaluate_condition(short_df, cond_rsi)
    assert ok2 is False, "데이터 부족이면 False여야 함"
    assert "데이터 부족" in reason2, f"'데이터 부족' 설명 필요: {reason2!r}"

    print("[SELF-VERIFY] rulebook_engine OK")
