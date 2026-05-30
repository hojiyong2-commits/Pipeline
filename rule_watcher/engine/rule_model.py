# [Purpose]: 룰북 도메인 모델 — RuleCondition / RuleGroup / RuleBook + JSON 직렬화.
# [Assumptions]: 모든 dataclass는 in-memory 검증만 수행. DB persistence는 호출자(별도 모듈) 책임.
#               v1에서 RuleGroup.logic은 AND/OR 모두 평가 지원, RuleBook은 그룹들을 AND로 묶음.
# [Vulnerability & Risks]:
#   - INDICATOR_TYPES 외 인디케이터 입력 시 from_dict 단계에서 ValueError 발생.
#   - threshold가 float이 아닌 경우 from_dict가 float() 변환 — 변환 실패 시 ValueError 전파.
#   - 사용자가 직접 dataclass 인스턴스를 생성하면 validate() 호출 누락 위험 (호출자가 수동 validate 필요).
# [Improvement]: pydantic 모델로 자동 검증 강화, OR 그룹 간 연결, 사용자 정의 인디케이터 플러그인 등록.
"""룰북 도메인 모델 + JSON 직렬화."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 지원 인디케이터 목록 (v1) — 키는 RuleCondition.indicator 값, value는 메타데이터.
INDICATOR_TYPES: Dict[str, Dict[str, Any]] = {
    "RSI": {"params": ["period"], "output": "float"},
    "MACD_CROSS": {
        # lookback은 선택 파라미터: 0이면 데이터 전체, 양수면 최근 N봉 내 cross 발생 여부.
        "params": ["fast_period", "slow_period", "signal_period", "lookback"],
        "output": "bool",
    },
    "SMA": {"params": ["period"], "output": "float"},
    "EMA": {"params": ["period"], "output": "float"},
    "BOLLINGER_UPPER": {"params": ["period", "std_dev"], "output": "float"},
    "BOLLINGER_LOWER": {"params": ["period", "std_dev"], "output": "float"},
    "ATR": {"params": ["period"], "output": "float"},
    "ADX": {"params": ["period"], "output": "float"},
    "STOCH_K": {"params": ["k_period", "d_period"], "output": "float"},
    "STOCH_RSI_K": {"params": ["rsi_period", "stoch_period"], "output": "float"},
    "CCI": {"params": ["period"], "output": "float"},
    "ROC": {"params": ["period"], "output": "float"},
    "VOLUME_MA_RATIO": {"params": ["period"], "output": "float"},
    "OBV": {"params": [], "output": "float"},
    "MFI": {"params": ["period"], "output": "float"},
    "VWAP": {"params": [], "output": "float"},
    "PRICE": {"params": [], "output": "float"},
    "CHANGE_RATE": {"params": [], "output": "float"},
    "PREV_HIGH_BREAK": {"params": ["lookback"], "output": "bool"},
    "NEAR_HIGH": {"params": ["lookback", "threshold_pct"], "output": "bool"},
    "MA_CROSS_UP": {"params": ["ma_type", "period"], "output": "bool"},
    "MA_CROSS_DOWN": {"params": ["ma_type", "period"], "output": "bool"},
    "GAP_UP": {"params": ["threshold_pct"], "output": "bool"},
    "GAP_DOWN": {"params": ["threshold_pct"], "output": "bool"},
}

VALID_OPERATORS: List[str] = ["<", "<=", ">", ">=", "==", "!="]
_BOOL_OPERATOR = "is_true"  # bool 인디케이터 전용

_ALL_OPERATORS = VALID_OPERATORS + [_BOOL_OPERATOR]


@dataclass
class RuleCondition:
    """단일 조건. `indicator (params) operator threshold` 형태.

    bool 출력 인디케이터(MACD_CROSS 등)는 `operator="is_true"`, `threshold=0.0`로 표현.
    """

    indicator: str
    operator: str
    threshold: float = 0.0
    params: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def validate(self) -> None:
        """필드 정합성 검증.

        Raises:
            TypeError: indicator/operator/params/description이 None인 경우.
            ValueError: 지원하지 않는 인디케이터/연산자, 또는 빈 indicator.
        """
        if self.indicator is None:
            raise TypeError("RuleCondition.indicator must not be None")
        if not isinstance(self.indicator, str):
            raise TypeError(
                f"RuleCondition.indicator must be str, got {type(self.indicator).__name__}"
            )
        if not self.indicator.strip():
            # negative not allowed: 빈 인디케이터명은 허용 불가
            raise ValueError("RuleCondition.indicator: 빈 문자열은 허용되지 않습니다")
        if self.indicator not in INDICATOR_TYPES:
            raise ValueError(
                f"지원하지 않는 인디케이터: {self.indicator!r}. "
                f"지원 목록: {sorted(INDICATOR_TYPES.keys())}"
            )

        if self.operator is None:
            raise TypeError("RuleCondition.operator must not be None")
        if not isinstance(self.operator, str):
            raise TypeError(
                f"RuleCondition.operator must be str, got {type(self.operator).__name__}"
            )
        if self.operator not in _ALL_OPERATORS:
            raise ValueError(
                f"지원하지 않는 연산자: {self.operator!r}. 지원 목록: {_ALL_OPERATORS}"
            )

        if self.threshold is None:
            raise TypeError("RuleCondition.threshold must not be None")
        if not isinstance(self.threshold, (int, float)) or isinstance(
            self.threshold, bool
        ):
            raise TypeError(
                f"RuleCondition.threshold must be numeric, got {type(self.threshold).__name__}"
            )
        # threshold는 음수 허용 — CHANGE_RATE, PRICE 비교 등 음수 임계값 일반화 지원.

        if self.params is None:
            raise TypeError("RuleCondition.params must not be None")
        if not isinstance(self.params, dict):
            raise TypeError(
                f"RuleCondition.params must be dict, got {type(self.params).__name__}"
            )

        if self.description is None:
            raise TypeError("RuleCondition.description must not be None")
        if not isinstance(self.description, str):
            raise TypeError(
                f"RuleCondition.description must be str, "
                f"got {type(self.description).__name__}"
            )

        # bool 출력 인디케이터는 is_true 또는 ==/!= 만 허용 (수치 비교는 의미 없음)
        output = INDICATOR_TYPES[self.indicator]["output"]
        if output == "bool" and self.operator not in (
            _BOOL_OPERATOR,
            "==",
            "!=",
        ):
            raise ValueError(
                f"bool 출력 인디케이터({self.indicator})는 "
                f"'is_true' 또는 '==' / '!=' 연산자만 지원합니다. got {self.operator!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """JSON 호환 dict 변환."""
        return {
            "indicator": self.indicator,
            "operator": self.operator,
            "threshold": float(self.threshold),
            "params": dict(self.params),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuleCondition":
        """dict → RuleCondition. 누락 필드는 기본값 사용 후 validate.

        Raises:
            TypeError: data가 None이거나 dict가 아닌 경우.
            ValueError: indicator 누락 또는 검증 실패.
        """
        if data is None:
            raise TypeError("RuleCondition.from_dict: data must not be None")
        if not isinstance(data, dict):
            raise TypeError(
                f"RuleCondition.from_dict: dict 타입이어야 합니다, "
                f"got {type(data).__name__}"
            )

        indicator_raw = data.get("indicator")
        if indicator_raw is None or (
            isinstance(indicator_raw, str) and not indicator_raw.strip()
        ):
            raise ValueError("RuleCondition.from_dict: indicator 필드가 필요합니다")

        threshold_raw = data.get("threshold", 0.0)
        try:
            threshold = float(threshold_raw)  # allowed: dict에서 들어오는 숫자는 int/float 모두 허용
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"RuleCondition.from_dict: threshold 변환 실패 ({threshold_raw!r}): {exc}"
            ) from exc

        params_raw = data.get("params", {})
        if params_raw is None:
            params_raw = {}
        if not isinstance(params_raw, dict):
            raise TypeError(
                f"RuleCondition.from_dict: params는 dict여야 합니다, "
                f"got {type(params_raw).__name__}"
            )

        cond = cls(
            indicator=str(indicator_raw),  # allowed: indicator는 str 강제, validate에서 재검증
            operator=str(data.get("operator", "<")),  # allowed: 기본 연산자 fallback
            threshold=threshold,
            params=dict(params_raw),
            description=str(data.get("description", "")),  # allowed: description 누락 시 빈 문자열
        )
        cond.validate()
        return cond


@dataclass
class RuleGroup:
    """조건 묶음. logic=AND이면 모든 조건이 PASS여야 하고, OR이면 하나라도 PASS면 그룹 PASS."""

    name: str
    conditions: List[RuleCondition] = field(default_factory=list)
    logic: str = "AND"

    def validate(self) -> None:
        """그룹 정합성 검증.

        Raises:
            TypeError: 필드 타입 오류.
            ValueError: 빈 이름, 잘못된 logic, 빈 조건 목록.
        """
        if self.name is None:
            raise TypeError("RuleGroup.name must not be None")
        if not isinstance(self.name, str):
            raise TypeError(f"RuleGroup.name must be str, got {type(self.name).__name__}")
        if not self.name.strip():
            # negative not allowed: 빈 그룹명은 디버깅 어려움 — 명시적 이름 필수
            raise ValueError("RuleGroup.name이 빈 문자열입니다")

        if self.logic is None:
            raise TypeError("RuleGroup.logic must not be None")
        if self.logic not in ("AND", "OR"):
            raise ValueError(
                f"RuleGroup.logic은 'AND' 또는 'OR'여야 합니다, got {self.logic!r}"
            )

        if self.conditions is None:
            raise TypeError("RuleGroup.conditions must not be None")
        if not isinstance(self.conditions, list):
            raise TypeError(
                f"RuleGroup.conditions must be list, got {type(self.conditions).__name__}"
            )
        if len(self.conditions) == 0:
            raise ValueError(f"RuleGroup({self.name!r}): conditions가 비어 있습니다")

        for idx, cond in enumerate(self.conditions):
            if not isinstance(cond, RuleCondition):
                raise TypeError(
                    f"RuleGroup({self.name!r}).conditions[{idx}]는 "
                    f"RuleCondition 인스턴스여야 합니다"
                )
            cond.validate()

    def to_dict(self) -> Dict[str, Any]:
        """JSON 호환 dict 변환."""
        return {
            "name": self.name,
            "logic": self.logic,
            "conditions": [c.to_dict() for c in self.conditions],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuleGroup":
        """dict → RuleGroup.

        Raises:
            TypeError: data가 None이거나 dict가 아닌 경우.
            ValueError: 검증 실패.
        """
        if data is None:
            raise TypeError("RuleGroup.from_dict: data must not be None")
        if not isinstance(data, dict):
            raise TypeError(
                f"RuleGroup.from_dict: dict 타입이어야 합니다, "
                f"got {type(data).__name__}"
            )

        conditions_raw = data.get("conditions", [])
        if not isinstance(conditions_raw, list):
            raise TypeError(
                f"RuleGroup.from_dict: conditions는 list여야 합니다, "
                f"got {type(conditions_raw).__name__}"
            )

        group = cls(
            name=str(data.get("name", "")),  # allowed: validate에서 빈 문자열 차단
            logic=str(data.get("logic", "AND")),  # allowed: 기본 AND
            conditions=[RuleCondition.from_dict(c) for c in conditions_raw],
        )
        group.validate()
        return group


@dataclass
class RuleBook:
    """룰북 = RuleGroup 목록. 그룹 간은 AND로 평가 (v1 단순화)."""

    name: str
    groups: List[RuleGroup] = field(default_factory=list)
    description: str = ""
    rulebook_id: Optional[str] = None

    def validate(self) -> None:
        """룰북 정합성 검증.

        Raises:
            TypeError: 필드 타입 오류.
            ValueError: 빈 이름 또는 빈 그룹 목록.
        """
        if self.name is None:
            raise TypeError("RuleBook.name must not be None")
        if not isinstance(self.name, str):
            raise TypeError(f"RuleBook.name must be str, got {type(self.name).__name__}")
        if not self.name.strip():
            # negative not allowed: 룰북 이름은 사용자 식별자
            raise ValueError("RuleBook.name이 빈 문자열입니다")

        if self.description is None:
            raise TypeError("RuleBook.description must not be None")
        if not isinstance(self.description, str):
            raise TypeError(
                f"RuleBook.description must be str, "
                f"got {type(self.description).__name__}"
            )

        if self.rulebook_id is not None and not isinstance(self.rulebook_id, str):
            raise TypeError(
                f"RuleBook.rulebook_id must be str or None, "
                f"got {type(self.rulebook_id).__name__}"
            )

        if self.groups is None:
            raise TypeError("RuleBook.groups must not be None")
        if not isinstance(self.groups, list):
            raise TypeError(
                f"RuleBook.groups must be list, got {type(self.groups).__name__}"
            )
        if len(self.groups) == 0:
            raise ValueError(f"RuleBook({self.name!r}): groups가 비어 있습니다")

        for idx, group in enumerate(self.groups):
            if not isinstance(group, RuleGroup):
                raise TypeError(
                    f"RuleBook({self.name!r}).groups[{idx}]는 "
                    f"RuleGroup 인스턴스여야 합니다"
                )
            group.validate()

    def to_dict(self) -> Dict[str, Any]:
        """JSON 호환 dict 변환."""
        return {
            "rulebook_id": self.rulebook_id,
            "name": self.name,
            "description": self.description,
            "groups": [g.to_dict() for g in self.groups],
        }

    def serialize(self) -> str:
        """JSON 문자열로 직렬화 (UTF-8, 한글 보존)."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def deserialize(cls, json_str: str) -> "RuleBook":
        """JSON 문자열 → RuleBook.

        Raises:
            TypeError: json_str이 None이거나 str이 아닌 경우.
            ValueError: 빈 문자열 또는 JSON 파싱 실패, 검증 실패.
        """
        if json_str is None:
            raise TypeError("RuleBook.deserialize: json_str must not be None")
        if not isinstance(json_str, str):
            raise TypeError(
                f"RuleBook.deserialize: json_str must be str, "
                f"got {type(json_str).__name__}"
            )
        if not json_str.strip():
            raise ValueError("RuleBook.deserialize: 빈 문자열")

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"RuleBook.deserialize: JSON 파싱 실패: {exc}"
            ) from exc

        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuleBook":
        """dict → RuleBook.

        Raises:
            TypeError: data가 None이거나 dict가 아닌 경우.
            ValueError: 검증 실패.
        """
        if data is None:
            raise TypeError("RuleBook.from_dict: data must not be None")
        if not isinstance(data, dict):
            raise TypeError(
                f"RuleBook.from_dict: dict 타입이어야 합니다, "
                f"got {type(data).__name__}"
            )

        groups_raw = data.get("groups", [])
        if not isinstance(groups_raw, list):
            raise TypeError(
                f"RuleBook.from_dict: groups는 list여야 합니다, "
                f"got {type(groups_raw).__name__}"
            )

        rb = cls(
            name=str(data.get("name", "")),  # allowed: validate에서 빈 차단
            description=str(data.get("description", "")),  # allowed: 기본 빈 문자열
            rulebook_id=(
                str(data["rulebook_id"])  # allowed: rulebook_id는 식별자라 str 강제
                if data.get("rulebook_id") is not None
                else None
            ),
            groups=[RuleGroup.from_dict(g) for g in groups_raw],
        )
        rb.validate()
        return rb


if __name__ == "__main__":
    # 자가 검증 블록 — 핵심 invariant 확인
    cond = RuleCondition(indicator="RSI", operator="<", threshold=30.0, params={"period": 14})
    cond.validate()
    assert cond.to_dict()["indicator"] == "RSI", "to_dict indicator mismatch"

    grp = RuleGroup(name="oversold", conditions=[cond])
    grp.validate()

    rb = RuleBook(name="과매도 룰", groups=[grp], description="RSI 과매도 단일 조건")
    rb.validate()
    serialized = rb.serialize()
    rb2 = RuleBook.deserialize(serialized)
    assert rb2.name == rb.name, "roundtrip name mismatch"
    assert len(rb2.groups[0].conditions) == 1, "roundtrip conditions mismatch"

    # 잘못된 인디케이터 → ValueError
    try:
        RuleCondition(indicator="NONEXISTENT", operator="<", threshold=0).validate()
        raise AssertionError("지원하지 않는 인디케이터에 ValueError가 발생해야 함")
    except ValueError:
        pass

    # 빈 그룹 → ValueError
    try:
        RuleGroup(name="empty").validate()
        raise AssertionError("빈 conditions에 ValueError가 발생해야 함")
    except ValueError:
        pass

    print("[SELF-VERIFY] rule_model OK")
