"""IMP-20260717-5EE0 REJECT#2 rework 검증 테스트 (TC-23, TC-24, TC-25).

# [Purpose]: 5개 REJECT 문제 수정 후 신규 동작을 검증한다.
#   TC-23: _run_codex_review_preflight가 8개 필드 포함 dict 반환
#   TC-24: CODEX_REVIEW_SHARDING 환경변수 없이도 sharding 계산 실행 (guard 제거)
#   TC-25: CODEX_CLI_MOCK=1로 _call_codex_cli_for_shard가 APPROVE_TO_USER 반환 + aggregate APPROVED
# [Assumptions]: 신규 헬퍼 함수(_run_codex_review_preflight/_call_codex_cli_for_shard)는 CLI 미노출
#   구조이므로 함수를 직접 import하여 검증한다. PIPELINE_STATE_PATH로 격리하여 운영 상태를 오염하지 않는다.
# [Vulnerability & Risks]: 실제 Codex CLI를 호출하지 않는다(mock/미연결 경로만 검증).
# [Improvement]: 실제 subprocess CLI 경로를 모킹하면 통합 흐름까지 검증 가능.

CLI Evidence Contract:
- 신규 헬퍼는 CLI 미노출이라 함수 직접 import를 예외적으로 허용(태스크 지침).
- 상태 격리는 PIPELINE_STATE_PATH로 수행한다.
"""

import importlib.util
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PID = "IMP-20260717-5EE0"


def _get_pipeline_module():
    spec = importlib.util.spec_from_file_location("pipeline_rework", BASE_DIR / "pipeline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    state_file = tmp_path / "pipeline_state.json"
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
    return state_file


@pytest.fixture(scope="module")
def pipe():
    return _get_pipeline_module()


# ---------------------------------------------------------------------------
# TC-23: _run_codex_review_preflight가 8개 필드 포함 dict 반환
# ---------------------------------------------------------------------------
def test_tc23_preflight_runs_full_pipeline(pipe, isolated_state):
    """_run_codex_review_preflight가 8개 필드를 모두 반환한다."""
    result = pipe._run_codex_review_preflight(PID, skip_cli=True)
    required_fields = {
        "review_plan", "shard_plan", "total_estimated_chars", "per_shard_chars",
        "budget_ok", "coverage_ok", "convergence_ok", "blocked_reason",
    }
    assert required_fields.issubset(set(result.keys())), (
        f"누락 필드: {required_fields - set(result.keys())}"
    )
    assert isinstance(result["shard_plan"], list)
    assert isinstance(result["per_shard_chars"], dict)
    assert isinstance(result["total_estimated_chars"], int)
    assert isinstance(result["review_plan"], dict)


# ---------------------------------------------------------------------------
# TC-24: CODEX_REVIEW_SHARDING 환경변수 없이도 sharding 실행 (guard 제거 확인)
# ---------------------------------------------------------------------------
def test_tc24_sharding_on_by_default(pipe, isolated_state, monkeypatch):
    """CODEX_REVIEW_SHARDING 환경변수 없이 _run_codex_review_preflight 실행 → shard 계산 수행."""
    # CODEX_REVIEW_SHARDING 환경변수 제거 (설정되어 있어도 영향 없어야 함)
    monkeypatch.delenv("CODEX_REVIEW_SHARDING", raising=False)
    result = pipe._run_codex_review_preflight(PID, skip_cli=True)
    # guard 제거로 sharding이 기본 활성화 → shard_plan/review_plan이 정상 타입으로 반환됨
    assert isinstance(result["shard_plan"], list), "sharding이 기본 활성화되지 않음"
    assert isinstance(result["review_plan"], dict)
    # per_shard_chars와 total_estimated_chars가 일관성 있게 계산됨
    assert result["total_estimated_chars"] == sum(result["per_shard_chars"].values())


# ---------------------------------------------------------------------------
# TC-25: CODEX_CLI_MOCK=1로 _call_codex_cli_for_shard 실행 + aggregate 확인
# ---------------------------------------------------------------------------
def test_tc25_aggregate_called_via_mock(pipe, monkeypatch):
    """CODEX_CLI_MOCK=1로 _call_codex_cli_for_shard가 APPROVE_TO_USER를 반환하고,
    _aggregate_shard_verdicts가 APPROVED를 반환한다."""
    monkeypatch.setenv("CODEX_CLI_MOCK", "1")
    shard = {"shard_id": "shard-core", "pr_head_sha": "a" * 40}
    result = pipe._call_codex_cli_for_shard(shard)
    assert result["verdict"] == "APPROVE_TO_USER"
    assert result["shard_id"] == "shard-core"

    # aggregate: 모든 shard APPROVE_TO_USER → APPROVED
    shard_results = [
        {"shard_id": "s1", "verdict": "APPROVE_TO_USER", "pr_head_sha": "a" * 40, "findings": []},
        {"shard_id": "s2", "verdict": "APPROVE_TO_USER", "pr_head_sha": "a" * 40, "findings": []},
    ]
    agg = pipe._aggregate_shard_verdicts(shard_results)
    assert agg["verdict"] == "APPROVED"
    assert agg["approved_shards"] == 2
    assert agg["total_shards"] == 2


# ---------------------------------------------------------------------------
# TC-25b: 미연결(non-mock) 환경에서는 ERROR verdict → fake approval 방지
# ---------------------------------------------------------------------------
def test_tc25b_no_mock_returns_error(pipe, monkeypatch):
    """CODEX_CLI_MOCK 미설정 시 _call_codex_cli_for_shard는 ERROR를 반환한다(fail-closed)."""
    monkeypatch.delenv("CODEX_CLI_MOCK", raising=False)
    result = pipe._call_codex_cli_for_shard({"shard_id": "s1", "pr_head_sha": "b" * 40})
    assert result["verdict"] == "ERROR"
    assert result["error_reason"] == "codex_cli_not_connected"
    # aggregate가 비승인 처리
    agg = pipe._aggregate_shard_verdicts([result])
    assert agg["verdict"] != "APPROVED"
