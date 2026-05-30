# [Purpose]: 룰북 엔진 패키지의 공개 인터페이스 노출.
# [Assumptions]: 순환 import 없음. rule_model → rulebook_engine → screening_engine 단방향 의존.
# [Vulnerability & Risks]: __all__ 누락 시 외부에서 잘못된 심볼 접근 가능 — 명시적 관리.
# [Improvement]: 룰북 레지스트리 (이름 기반 캐시) 추가.
"""룰북 엔진 — 조건 모델, 인디케이터 평가, 종목 스크리닝."""
from rule_watcher.engine.rule_model import (
    INDICATOR_TYPES,
    VALID_OPERATORS,
    RuleBook,
    RuleCondition,
    RuleGroup,
)
from rule_watcher.engine.rulebook_engine import (
    IndicatorNotSupportedError,
    RuleEvaluator,
)
from rule_watcher.engine.screening_engine import ScreeningEngine, ScreenResult

__all__ = [
    # rule_model
    "INDICATOR_TYPES",
    "VALID_OPERATORS",
    "RuleBook",
    "RuleCondition",
    "RuleGroup",
    # rulebook_engine
    "IndicatorNotSupportedError",
    "RuleEvaluator",
    # screening_engine
    "ScreeningEngine",
    "ScreenResult",
]
