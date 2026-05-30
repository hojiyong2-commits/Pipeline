# [Purpose]: MT-5 룰북 엔진 (rule_model + rulebook_engine + screening_engine) 검증.
#            TC-1 (RSI 과매도), TC-2 (MACD 골든크로스) 오라클 + 유닛 케이스.
# [Assumptions]: pandas/json 표준 사용. 인디케이터 모듈은 MT-4에서 PASS 완료.
# [Vulnerability & Risks]: 오라클 파일 누락 시 pytest skip 대신 명시적 FileNotFoundError로 실패시켜
#                          오라클 보존 의무를 강제. 부동소수 비교는 사용 안 함 (bool 결과만 검증).
# [Improvement]: hypothesis 기반 property test, 다중 룰북 결합 시나리오.
"""룰북 엔진 / 스크리닝 엔진 / 오라클 검증."""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List

import pandas as pd
import pytest

from rule_watcher.engine.rule_model import (
    INDICATOR_TYPES,
    RuleBook,
    RuleCondition,
    RuleGroup,
)
from rule_watcher.engine.rulebook_engine import (
    IndicatorNotSupportedError,
    RuleEvaluator,
)
from rule_watcher.engine.screening_engine import (
    ScreenResult,
    ScreeningEngine,
)


# ---------- helpers ----------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ORACLE_BASE = _REPO_ROOT / "tests" / "oracles" / "FEAT-20260530-6F07"


def _load_oracle(case_id: str) -> Dict[str, Any]:
    """오라클 input/expected 로드."""
    base = _ORACLE_BASE / case_id
    inp_path = base / "input.json"
    exp_path = base / "expected.json"
    if not inp_path.exists():
        raise FileNotFoundError(f"oracle input.json 없음: {inp_path}")
    if not exp_path.exists():
        raise FileNotFoundError(f"oracle expected.json 없음: {exp_path}")
    return {
        "input": json.loads(inp_path.read_text(encoding="utf-8")),
        "expected": json.loads(exp_path.read_text(encoding="utf-8")),
    }


def _build_df(ohlcv: Dict[str, List[float]]) -> pd.DataFrame:
    """오라클 ohlcv_data의 한 종목 dict → DataFrame."""
    return pd.DataFrame(
        {
            "open": ohlcv["open"],
            "high": ohlcv["high"],
            "low": ohlcv["low"],
            "close": ohlcv["close"],
            "volume": ohlcv["volume"],
        }
    )


def _build_watchlist(ohlcv_data: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """오라클 ohlcv_data → ScreeningEngine.screen_watchlist 입력 형태."""
    items = []
    for ticker, payload in ohlcv_data.items():
        items.append(
            {
                "ticker": payload["ticker"],
                "name": payload["name"],
                "df": _build_df(payload),
            }
        )
    return items


# ---------- 1. TC-1 oracle: RSI(14) < 30 ----------


def test_tc1_rsi_oversold_oracle():
    """TC-1: RSI(14) < 30 — 005930 선별, 035720 미선별."""
    data = _load_oracle("TC-1")
    rule_def = data["input"]["rule"]
    expected = data["expected"]

    rulebook = RuleBook(
        name="RSI 과매도",
        description="TC-1 oracle rule",
        groups=[
            RuleGroup(
                name="oversold",
                conditions=[
                    RuleCondition(
                        indicator=rule_def["indicator"],
                        operator=rule_def["operator"],
                        threshold=float(rule_def["threshold"]),
                        params={"period": rule_def["period"]},
                    )
                ],
            )
        ],
    )

    engine = ScreeningEngine(rulebook=rulebook)
    results = engine.screen_watchlist(_build_watchlist(data["input"]["ohlcv_data"]))

    matched_tickers = [r.ticker for r in results if r.matched]
    not_matched_tickers = [r.ticker for r in results if not r.matched]

    assert matched_tickers == expected["matched_tickers"], (
        f"matched 불일치: got {matched_tickers}, expected {expected['matched_tickers']}"
    )
    assert set(not_matched_tickers) == set(expected["not_matched_tickers"]), (
        f"not_matched 불일치: got {not_matched_tickers}, "
        f"expected {expected['not_matched_tickers']}"
    )
    assert len(matched_tickers) == expected["result_count"], (
        f"result_count 불일치: {len(matched_tickers)} vs {expected['result_count']}"
    )
    # 한국어 설명에 RSI(14) 표기 포함
    matched_result = next(r for r in results if r.matched)
    assert "RSI(14)" in matched_result.reason, (
        f"한국어 설명에 RSI(14) 포함 필요: {matched_result.reason!r}"
    )


# ---------- 2. TC-2 oracle: MACD 골든크로스 ----------


def test_tc2_macd_golden_cross_oracle():
    """TC-2: MACD 골든크로스 — 000660 선별."""
    data = _load_oracle("TC-2")
    rule_def = data["input"]["rule"]
    expected = data["expected"]

    rulebook = RuleBook(
        name="MACD 골든크로스",
        description="TC-2 oracle rule",
        groups=[
            RuleGroup(
                name="golden_cross",
                conditions=[
                    RuleCondition(
                        indicator=rule_def["indicator"],
                        operator="is_true",
                        threshold=0.0,
                        params={
                            "fast_period": rule_def["fast_period"],
                            "slow_period": rule_def["slow_period"],
                            "signal_period": rule_def["signal_period"],
                        },
                    )
                ],
            )
        ],
    )

    engine = ScreeningEngine(rulebook=rulebook)
    results = engine.screen_watchlist(_build_watchlist(data["input"]["ohlcv_data"]))

    matched_tickers = [r.ticker for r in results if r.matched]
    assert matched_tickers == expected["matched_tickers"], (
        f"matched 불일치: got {matched_tickers}, expected {expected['matched_tickers']}"
    )
    assert len(matched_tickers) == expected["result_count"], (
        f"result_count 불일치: {len(matched_tickers)} vs {expected['result_count']}"
    )
    matched_result = next(r for r in results if r.matched)
    assert "골든크로스" in matched_result.reason, (
        f"한국어 설명에 '골든크로스' 포함 필요: {matched_result.reason!r}"
    )


# ---------- 3. AND 조건 복합 룰북 ----------


def test_and_logic_combined_rulebook():
    """AND 조건: RSI<30 AND MACD 골든크로스 — 둘 다 충족해야 matched."""
    # TC-1의 005930은 RSI<30 충족이지만 MACD는 골든크로스 없음 → AND이면 미충족.
    data = _load_oracle("TC-1")
    rulebook = RuleBook(
        name="복합 진입 룰",
        groups=[
            RuleGroup(
                name="combined",
                logic="AND",
                conditions=[
                    RuleCondition(
                        indicator="RSI",
                        operator="<",
                        threshold=30.0,
                        params={"period": 14},
                    ),
                    RuleCondition(
                        indicator="MACD_CROSS",
                        operator="is_true",
                        params={"fast_period": 12, "slow_period": 26, "signal_period": 9},
                    ),
                ],
            )
        ],
    )
    engine = ScreeningEngine(rulebook=rulebook)
    results = engine.screen_watchlist(_build_watchlist(data["input"]["ohlcv_data"]))
    # 둘 다 충족하는 종목 없어야 함 (005930은 RSI만, 035720은 둘 다 아님)
    assert not any(r.matched for r in results), (
        f"AND 조건에서 매칭 종목이 없어야 함, got {[r.ticker for r in results if r.matched]}"
    )


# ---------- 4. 잘못된 인디케이터 이름 → ValueError + 한국어 메시지 ----------


def test_invalid_indicator_raises_value_error():
    """지원하지 않는 인디케이터는 ValueError + 한국어 안내."""
    with pytest.raises(ValueError) as excinfo:
        RuleCondition(indicator="NONEXISTENT_X", operator="<", threshold=0).validate()
    assert "지원하지 않는 인디케이터" in str(excinfo.value), (
        f"한국어 안내 메시지 필요: {excinfo.value}"
    )


# ---------- 5. 빈 룰북 그룹 → ValueError ----------


def test_empty_rulegroup_raises_value_error():
    """conditions가 비어 있는 RuleGroup은 ValueError."""
    with pytest.raises(ValueError) as excinfo:
        RuleGroup(name="empty").validate()
    assert "비어 있" in str(excinfo.value) or "empty" in str(excinfo.value), (
        f"빈 conditions 안내 필요: {excinfo.value}"
    )


def test_empty_rulebook_groups_raises_value_error():
    """groups가 비어 있는 RuleBook은 ValueError."""
    with pytest.raises(ValueError):
        RuleBook(name="X", groups=[]).validate()


# ---------- 6. NaN / 데이터 부족 ----------


def test_short_data_returns_no_data_message():
    """데이터 길이가 부족하면 (False, '데이터 부족...') 반환."""
    df = pd.DataFrame(
        {
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [1000.0],
        }
    )
    evaluator = RuleEvaluator()
    cond = RuleCondition(
        indicator="RSI",
        operator="<",
        threshold=30.0,
        params={"period": 14},
    )
    ok, reason = evaluator.evaluate_condition(df, cond)
    assert ok is False, "단일 봉 데이터에서 False여야 함"
    assert "데이터 부족" in reason, f"'데이터 부족' 설명 필요: {reason!r}"


def test_evaluate_condition_none_df_raises_type_error():
    """df=None → TypeError."""
    evaluator = RuleEvaluator()
    cond = RuleCondition(
        indicator="RSI",
        operator="<",
        threshold=30.0,
        params={"period": 14},
    )
    with pytest.raises(TypeError):
        evaluator.evaluate_condition(None, cond)  # type: ignore[arg-type]


def test_evaluate_condition_none_condition_raises_type_error():
    """condition=None → TypeError."""
    evaluator = RuleEvaluator()
    df = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]}
    )
    with pytest.raises(TypeError):
        evaluator.evaluate_condition(df, None)  # type: ignore[arg-type]


# ---------- 7. RuleBook serialize/deserialize 라운드트립 ----------


def test_rulebook_serialize_deserialize_roundtrip():
    """RuleBook → JSON → RuleBook 라운드트립이 동등."""
    rb = RuleBook(
        name="라운드트립 룰",
        description="설명 한글 포함",
        rulebook_id="rb-001",
        groups=[
            RuleGroup(
                name="g1",
                logic="OR",
                conditions=[
                    RuleCondition(
                        indicator="RSI",
                        operator="<",
                        threshold=30.0,
                        params={"period": 14},
                        description="과매도",
                    ),
                    RuleCondition(
                        indicator="MACD_CROSS",
                        operator="is_true",
                        params={"fast_period": 12, "slow_period": 26, "signal_period": 9},
                    ),
                ],
            )
        ],
    )

    json_str = rb.serialize()
    rb2 = RuleBook.deserialize(json_str)

    assert rb2.name == rb.name
    assert rb2.description == rb.description
    assert rb2.rulebook_id == rb.rulebook_id
    assert len(rb2.groups) == 1
    assert rb2.groups[0].logic == "OR"
    assert len(rb2.groups[0].conditions) == 2
    assert rb2.groups[0].conditions[0].indicator == "RSI"
    assert rb2.groups[0].conditions[0].params == {"period": 14}
    assert rb2.groups[0].conditions[1].indicator == "MACD_CROSS"
    # 한글 보존 (ensure_ascii=False)
    assert "한글" in json_str


def test_rulebook_deserialize_empty_string_raises():
    """빈 JSON 문자열은 ValueError."""
    with pytest.raises(ValueError):
        RuleBook.deserialize("")


def test_rulebook_deserialize_malformed_json_raises():
    """깨진 JSON은 ValueError (JSONDecodeError → ValueError 래핑)."""
    with pytest.raises(ValueError):
        RuleBook.deserialize("{not valid json")


# ---------- 8. RuleGroup OR 로직 ----------


def test_rulegroup_or_logic_evaluation():
    """OR 그룹: 한 조건만 충족해도 그룹 PASS."""
    data = _load_oracle("TC-1")
    # 005930 (하락 데이터)는 RSI<30 충족, MACD 미충족.
    # OR 룰북이면 매칭됨.
    rb = RuleBook(
        name="OR 룰",
        groups=[
            RuleGroup(
                name="or_group",
                logic="OR",
                conditions=[
                    RuleCondition(
                        indicator="RSI",
                        operator="<",
                        threshold=30.0,
                        params={"period": 14},
                    ),
                    RuleCondition(
                        indicator="MACD_CROSS",
                        operator="is_true",
                        params={"fast_period": 12, "slow_period": 26, "signal_period": 9},
                    ),
                ],
            )
        ],
    )
    engine = ScreeningEngine(rulebook=rb)
    results = engine.screen_watchlist(_build_watchlist(data["input"]["ohlcv_data"]))
    matched_tickers = [r.ticker for r in results if r.matched]
    assert "005930" in matched_tickers, (
        f"OR 그룹에서 005930이 RSI 단독으로 매칭되어야 함, got {matched_tickers}"
    )


# ---------- 9. ScreeningEngine 입력 검증 ----------


def test_screening_engine_rulebook_none_raises():
    """rulebook=None → TypeError."""
    with pytest.raises(TypeError):
        ScreeningEngine(rulebook=None)  # type: ignore[arg-type]


def test_screen_ticker_empty_ticker_raises():
    """빈 ticker → ValueError."""
    rb = RuleBook(
        name="X",
        groups=[
            RuleGroup(
                name="g",
                conditions=[
                    RuleCondition(
                        indicator="RSI",
                        operator="<",
                        threshold=30.0,
                        params={"period": 14},
                    )
                ],
            )
        ],
    )
    engine = ScreeningEngine(rulebook=rb)
    with pytest.raises(ValueError):
        engine.screen_ticker("", "이름", pd.DataFrame({"close": [1.0]}))


def test_screen_watchlist_empty_returns_empty_list():
    """빈 watchlist는 ValueError가 아니라 빈 결과."""
    rb = RuleBook(
        name="X",
        groups=[
            RuleGroup(
                name="g",
                conditions=[
                    RuleCondition(
                        indicator="RSI",
                        operator="<",
                        threshold=30.0,
                        params={"period": 14},
                    )
                ],
            )
        ],
    )
    engine = ScreeningEngine(rulebook=rb)
    assert engine.screen_watchlist([]) == []


def test_screen_watchlist_missing_keys_raises():
    """watchlist 항목에 ticker 누락 → ValueError."""
    rb = RuleBook(
        name="X",
        groups=[
            RuleGroup(
                name="g",
                conditions=[
                    RuleCondition(
                        indicator="RSI",
                        operator="<",
                        threshold=30.0,
                        params={"period": 14},
                    )
                ],
            )
        ],
    )
    engine = ScreeningEngine(rulebook=rb)
    with pytest.raises(ValueError):
        engine.screen_watchlist([{"name": "X", "df": pd.DataFrame({"close": [1.0]})}])


# ---------- 10. INDICATOR_TYPES 메타데이터 무결성 ----------


def test_indicator_types_are_dispatched():
    """INDICATOR_TYPES에 등록된 모든 인디케이터는 _dispatch에서 처리 가능."""
    evaluator = RuleEvaluator()
    # 충분한 길이의 더미 데이터 (60봉)
    n = 60
    close = [100.0 + i * 0.5 for i in range(n)]
    df = pd.DataFrame(
        {
            "open": [c - 0.2 for c in close],
            "high": [c + 0.5 for c in close],
            "low": [c - 0.5 for c in close],
            "close": close,
            "volume": [10_000.0 + i * 100 for i in range(n)],
        }
    )

    for ind_name, meta in INDICATOR_TYPES.items():
        is_bool = meta["output"] == "bool"
        operator = "is_true" if is_bool else "<"
        # 기본 파라미터 채움 (안전한 값)
        params: Dict[str, Any] = {}
        for p in meta["params"]:
            if p == "ma_type":
                params[p] = "sma"
            elif p == "std_dev" or p == "threshold_pct":
                params[p] = 2.0
            elif p == "lookback":
                params[p] = 20
            else:
                params[p] = 14
        # MACD 슬로우 보정
        if ind_name == "MACD_CROSS":
            params = {"fast_period": 12, "slow_period": 26, "signal_period": 9}

        cond = RuleCondition(
            indicator=ind_name,
            operator=operator,
            threshold=0.0 if is_bool else 1e9,  # 수치 조건은 거의 항상 False가 되는 큰 값
            params=params,
        )
        # 어떤 결과든 예외 없이 (bool, str) 반환되어야 함
        ok, reason = evaluator.evaluate_condition(df, cond)
        assert isinstance(ok, bool), f"{ind_name}: bool 결과 필요, got {type(ok).__name__}"
        assert isinstance(reason, str) and reason, (
            f"{ind_name}: 비어 있지 않은 한국어 설명 필요, got {reason!r}"
        )


# ---------- 11. ScreenResult dataclass 직렬화 ----------


def test_screen_result_to_dict():
    """ScreenResult.to_dict가 JSON 호환 dict 반환."""
    r = ScreenResult(
        ticker="005930",
        name="삼성전자",
        matched=True,
        reason="RSI(14)가 27.3으로 30.00보다 낮음",
        matched_at="2026-05-30T00:00:00+00:00",
    )
    d = r.to_dict()
    assert d["ticker"] == "005930"
    assert d["matched"] is True
    assert d["reason"].startswith("RSI(14)")
    # json.dumps 호환
    json.dumps(d, ensure_ascii=False)


# ---------- 12. IndicatorNotSupportedError ----------


def test_indicator_not_supported_error_is_value_error():
    """IndicatorNotSupportedError는 ValueError의 서브클래스."""
    assert issubclass(IndicatorNotSupportedError, ValueError)
