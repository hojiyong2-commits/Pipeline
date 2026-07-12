"""
test_codex_model_router_dae1.py — IMP-20260712-DAE1 Codex Model Router E2E Tests

TC-1~TC-5: _classify_codex_review_risk 분류 검증
TC-6~TC-10: _build_codex_model_policy 정책 검증
TC-11~TC-14: _check_codex_capability_gate 검증
TC-15: _detect_codex_cli_capability 반환 구조 검증
TC-16~TC-17: 캐시 허용/금지 정책 검증
TC-18: _classify_codex_cli_error ERROR/REJECT 분리 검증
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pipeline  # noqa: E402


# --- TC-1~TC-5: _classify_codex_review_risk ---

def test_tc01_low_risk_docs() -> None:
    """docs/report.md만 변경 → LOW."""
    assert pipeline._classify_codex_review_risk(["docs/report.md"], [])["risk_level"] == "LOW"


def test_tc02_high_risk_pipeline_non_critical_func() -> None:
    """pipeline.py + non-critical 함수 → HIGH (CRITICAL_FUNCTIONS에 없음)."""
    result = pipeline._classify_codex_review_risk(["pipeline.py"], ["_cmd_gates_codex_review"])
    assert result["risk_level"] == "HIGH"


def test_tc03_critical_acceptance_function() -> None:
    """pipeline.py + _cmd_gates_request_accept → CRITICAL."""
    result = pipeline._classify_codex_review_risk(["pipeline.py"], ["_cmd_gates_request_accept"])
    assert result["risk_level"] == "CRITICAL"


def test_tc04_high_risk_workflow_path() -> None:
    """.github/workflows/ci.yml → HIGH."""
    result = pipeline._classify_codex_review_risk([".github/workflows/ci.yml"], [])
    assert result["risk_level"] == "HIGH"


def test_tc05_empty_inputs_low() -> None:
    """빈 입력 → LOW (default)."""
    assert pipeline._classify_codex_review_risk([], [])["risk_level"] == "LOW"


# --- TC-6~TC-10: _build_codex_model_policy ---

def test_tc06_low_policy_observe() -> None:
    assert pipeline._build_codex_model_policy("LOW")["mode"] == "observe"


def test_tc07_medium_policy_observe() -> None:
    assert pipeline._build_codex_model_policy("MEDIUM")["mode"] == "observe"


def test_tc08_high_policy_enforce_limited_cache() -> None:
    policy = pipeline._build_codex_model_policy("HIGH")
    assert policy["mode"] == "enforce"
    assert policy["cache_allowed"] == "limited"


def test_tc09_critical_policy_no_cache_force_review() -> None:
    policy = pipeline._build_codex_model_policy("CRITICAL")
    assert policy["cache_allowed"] is False
    assert policy["force_review_required"] is True


def test_tc10_high_downgrade_blocked() -> None:
    result = pipeline._build_codex_model_policy("HIGH", downgrade_requested=True)
    assert result["result"] == "BLOCKED"
    assert result["failure_code"] == "downgrade_blocked"


# --- TC-11~TC-14: _check_codex_capability_gate ---

def test_tc11_unknown_model_critical_blocked() -> None:
    result = pipeline._check_codex_capability_gate("unknown", "CRITICAL")
    assert result["result"] == "BLOCKED"
    assert result["failure_code"] == "unknown_model_critical_blocked"


def test_tc12_unknown_model_high_blocked() -> None:
    assert pipeline._check_codex_capability_gate("unknown", "HIGH")["result"] == "BLOCKED"


def test_tc13_unknown_model_low_ok() -> None:
    """LOW/MEDIUM은 unknown이어도 BLOCKED 아님."""
    assert pipeline._check_codex_capability_gate("unknown", "LOW")["result"] == "OK"


def test_tc14_known_model_critical_ok() -> None:
    """known model은 CRITICAL이어도 BLOCKED 아님."""
    assert pipeline._check_codex_capability_gate("claude-sonnet", "CRITICAL")["result"] == "OK"


# --- TC-15: _detect_codex_cli_capability ---

def test_tc15_detect_capability_structure() -> None:
    """반환값은 dict이며 available/actual_model/model_source 키 포함."""
    result = pipeline._detect_codex_cli_capability()
    assert isinstance(result, dict)
    assert "available" in result
    assert "actual_model" in result
    assert "model_source" in result


# --- TC-16~TC-17: 캐시 허용/금지 정책 ---

def test_tc16_low_policy_cache_allowed() -> None:
    assert pipeline._build_codex_model_policy("LOW")["cache_allowed"] is True


def test_tc17_critical_policy_cache_forbidden() -> None:
    assert pipeline._build_codex_model_policy("CRITICAL")["cache_allowed"] is False


# --- TC-18: _classify_codex_cli_error ERROR/REJECT 분리 ---

def test_tc18_usage_limit_is_error_not_reject() -> None:
    """usage limit은 ERROR(error_type=usage_limit)이며 REJECT가 아니다 (reject_count 미변경)."""
    result = pipeline._classify_codex_cli_error(1, "you've hit your usage limit", "")
    assert result["error_type"] == "usage_limit"
    assert result["error_retryable"] is True
    # ERROR 분류 결과는 reject_count 필드를 담지 않는다 (reject 카운터와 무관).
    assert "reject_count" not in result
