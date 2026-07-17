"""IMP-20260717-5EE0 REJECT#2 rework E2E/unit 검증 (TC-23 ~ TC-31).

# [Purpose]: REJECT#2 9개 요구사항의 신규/수정 동작을 검증한다.
#   TC-23: preflight의 어떤 shard도 pipeline.py 전체(>1M자)를 단일 evidence로 담지 않음(<=250k).
#   TC-24: preflight가 coverage/budget 필드를 반환하고 coverage 부재로 blocked되지 않음.
#   TC-25: CODEX_CLI_MOCK=1만으로는 production `gates codex-review`가 승인되지 않음(permit 필요).
#   TC-26: shard 하나 REJECT → 전체 REJECTED.
#   TC-27: shard 하나 ERROR → 전체 ERROR.
#   TC-28: permit 없으면 production `gates codex-review`가 BLOCKED(비정상 종료).
#   TC-29: PR head 변경 후 stale permit → BLOCKED(SystemExit).
#   TC-30: 입력 크기 초과 → ERROR(input_too_large)이며 reject_count를 소모하지 않음.
#   TC-31: 모든 shard APPROVE + 동일 SHA + required 존재 → 전체 APPROVED.
# [Assumptions]: 신규/수정 헬퍼는 CLI 미노출 함수도 있어 직접 import를 예외적으로 허용(태스크 지침).
#   상태 변경/서브프로세스 CLI는 PIPELINE_STATE_PATH로 격리한다.
# [Vulnerability & Risks]: 실제 Codex CLI를 호출하지 않는다(mock/미연결/permit gate 경로만 검증).
# [Improvement]: fake codex bin을 PATH에 주입하면 subprocess verdict 파싱까지 통합 검증 가능.

CLI Evidence Contract:
- 상태 변경 서브프로세스는 PIPELINE_STATE_PATH 격리를 사용한다.
- 신규 헬퍼는 CLI 미노출이라 함수 직접 import를 예외적으로 허용(태스크 지침).
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_PY = REPO_ROOT / "pipeline.py"
PIPELINE_ID = "IMP-20260717-5EE0"


def _get_pipeline_module():
    """pipeline.py를 동적으로 로드한다(신규 헬퍼 직접 검증용)."""
    spec = importlib.util.spec_from_file_location("pipeline_e2e_v2", PIPELINE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pipe():
    return _get_pipeline_module()


def _write_isolated_state(state_path: Path) -> None:
    """외부 gate가 모두 PASS인 최소 pipeline_state.json을 기록한다."""
    state = {
        "version": "1.2.0",
        "pipeline_id": PIPELINE_ID,
        "current_phase": "architect",
        "phases": {
            "dev": {"status": "DONE"},
            "qa": {"status": "PASS"},
            "harness": {"status": "PASS"},
        },
        "external_gates": {
            "technical": {"status": "PASS"},
            "oracle": {"status": "PASS"},
            "github_ci": {"status": "PASS"},
            "acceptance": {"status": "PENDING"},
        },
        "module_gates": {"enabled": True, "sequence": [], "modules": {}, "integration": {"status": "PASS"}},
        "requirements_tracking": {"enabled": False},
        "event_log": [],
        "history": [],
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def isolated_env(tmp_path):
    """PIPELINE_STATE_PATH로 격리된 서브프로세스 환경(dict)을 반환한다."""
    state_path = tmp_path / "pipeline_state.json"
    _write_isolated_state(state_path)
    env = {**os.environ, "PIPELINE_STATE_PATH": str(state_path), "PYTHONUTF8": "1"}
    return env


def _run_cli(env, *cli_args, timeout=90):
    """pipeline.py CLI를 서브프로세스로 실행한다."""
    return subprocess.run(
        [sys.executable, str(PIPELINE_PY), *cli_args],
        env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), timeout=timeout, check=False,
    )


# ---------------------------------------------------------------------------
# TC-23: preflight의 모든 shard가 250k 이하 (pipeline.py 전체 미포함)
# ---------------------------------------------------------------------------
def test_tc23_preflight_excludes_full_pipeline_py(isolated_env):
    """preflight의 어떤 shard도 250,000자를 초과하지 않는다."""
    result = _run_cli(isolated_env, "gates", "codex-review", "--preflight-only")
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"preflight stdout is not JSON: {result.stdout[:500]} / stderr={result.stderr[:300]}")

    shard_plan = output.get("shard_plan", [])
    for shard in shard_plan:
        prompt_chars = int(shard.get("prompt_chars", 0) or 0)
        assert prompt_chars <= 250_000, (
            f"shard {shard.get('shard_id')} prompt_chars={prompt_chars} > 250,000"
        )


# ---------------------------------------------------------------------------
# TC-24: preflight가 coverage/budget 필드를 반환하고 coverage 부재로 blocked되지 않음
# ---------------------------------------------------------------------------
def test_tc24_coverage_manifest_present(isolated_env):
    """preflight 결과에 budget_ok/coverage_ok가 존재하고 coverage 사유로 blocked되지 않는다."""
    result = _run_cli(isolated_env, "gates", "codex-review", "--preflight-only")
    try:
        output = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"preflight stdout is not JSON: {result.stdout[:500]}")

    assert "budget_ok" in output
    assert "coverage_ok" in output
    blocked = str(output.get("blocked_reason") or "")
    assert "coverage" not in blocked.lower() or output.get("coverage_ok") is True


# ---------------------------------------------------------------------------
# TC-25: CODEX_CLI_MOCK=1만으로 production 승인 불가 (permit 없음 → 비정상 종료)
# ---------------------------------------------------------------------------
def test_tc25_no_mock_approval_in_production(isolated_env):
    """CODEX_CLI_MOCK=1만으로는 production gates codex-review가 성공하지 못한다(permit 없음)."""
    env = {**isolated_env, "CODEX_CLI_MOCK": "1"}
    result = _run_cli(env, "gates", "codex-review")
    assert result.returncode != 0, (
        "CODEX_CLI_MOCK=1만으로 production codex-review가 성공하면 안 됩니다(permit 필요)."
    )


# ---------------------------------------------------------------------------
# TC-26: shard 하나 REJECT → 전체 REJECTED
# ---------------------------------------------------------------------------
def test_tc26_shard_reject_propagates(pipe):
    finding = {
        "scope": "IN_SCOPE", "severity": "P0", "root_cause_category": "correctness",
        "evidence": "e", "reproduction": "r", "required_fix": "f", "acceptance_criteria": [],
    }
    shard_results = [
        {"shard_id": "s1", "verdict": "APPROVE_TO_USER", "findings": [], "pr_head_sha": "abc"},
        {"shard_id": "s2", "verdict": "REJECT", "findings": [finding], "pr_head_sha": "abc"},
    ]
    agg = pipe._aggregate_shard_verdicts(shard_results, required_shard_ids=["s1", "s2"], pr_head_sha="abc")
    assert agg["verdict"] == "REJECTED", f"expected REJECTED, got {agg['verdict']}"
    assert agg["rejected_shards"] == 1


# ---------------------------------------------------------------------------
# TC-27: shard 하나 ERROR → 전체 ERROR
# ---------------------------------------------------------------------------
def test_tc27_shard_error_propagates(pipe):
    shard_results = [
        {"shard_id": "s1", "verdict": "APPROVE_TO_USER", "findings": [], "pr_head_sha": "abc"},
        {"shard_id": "s2", "verdict": "ERROR", "findings": [], "pr_head_sha": "abc", "error_reason": "timeout"},
    ]
    agg = pipe._aggregate_shard_verdicts(shard_results, required_shard_ids=["s1", "s2"], pr_head_sha="abc")
    assert agg["verdict"] == "ERROR", f"expected ERROR, got {agg['verdict']}"
    assert agg["error_shards"] >= 1


# ---------------------------------------------------------------------------
# TC-28: permit 없으면 production gates codex-review가 BLOCKED
# ---------------------------------------------------------------------------
def test_tc28_permit_required_before_cli(isolated_env):
    """permit 없으면 gates codex-review가 비정상 종료하고 안내가 출력된다."""
    result = _run_cli(isolated_env, "gates", "codex-review")
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert ("permit" in combined) or ("blocked" in combined) or ("authorized" in combined)


# ---------------------------------------------------------------------------
# TC-29: PR head 변경 후 stale permit → BLOCKED(SystemExit)
# ---------------------------------------------------------------------------
def test_tc29_permit_stale_blocks(pipe, tmp_path, monkeypatch):
    """permit의 pr_head_sha != 현재 → permit_stale로 SystemExit."""
    state_path = tmp_path / "pipeline_state.json"
    _write_isolated_state(state_path)
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_path))

    permit_path = pipe._shard_review_permit_path()
    permit_path.parent.mkdir(parents=True, exist_ok=True)
    permit = {
        "pipeline_id": PIPELINE_ID,
        "pr_head_sha": "old_sha_1234567890",
        "review_plan_sha": "plan_sha",
        "issued_at": "2026-07-17T00:00:00Z",
        "expires_at": "2099-12-31T23:59:59Z",
        "consumed": False,
        "permit_id": "test-permit-id",
    }
    permit_path.write_text(json.dumps(permit), encoding="utf-8")

    with pytest.raises(SystemExit):
        pipe._check_and_consume_permit(PIPELINE_ID, "new_sha_different", "plan_sha")


def test_tc29b_permit_consumed_single_use(pipe, tmp_path, monkeypatch):
    """정상 permit은 1회 소비 후 재사용 시 permit_consumed로 SystemExit."""
    state_path = tmp_path / "pipeline_state.json"
    _write_isolated_state(state_path)
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_path))

    permit_path = pipe._shard_review_permit_path()
    permit_path.parent.mkdir(parents=True, exist_ok=True)
    permit = {
        "pipeline_id": PIPELINE_ID,
        "pr_head_sha": "head123",
        "review_plan_sha": "plan_sha",
        "issued_at": "2026-07-17T00:00:00Z",
        "expires_at": "2099-12-31T23:59:59Z",
        "consumed": False,
        "permit_id": "test-permit-id",
    }
    permit_path.write_text(json.dumps(permit), encoding="utf-8")

    consumed = pipe._check_and_consume_permit(PIPELINE_ID, "head123", "plan_sha")
    assert consumed["consumed"] is True
    with pytest.raises(SystemExit):
        pipe._check_and_consume_permit(PIPELINE_ID, "head123", "plan_sha")


# ---------------------------------------------------------------------------
# TC-30: input_too_large → ERROR, reject_count 불변(직접 aggregate 미소모)
# ---------------------------------------------------------------------------
def test_tc30_input_too_large_is_error(pipe, monkeypatch):
    """250k 초과 shard는 CLI 호출 전에 input_too_large ERROR로 차단된다."""
    monkeypatch.delenv("CODEX_CLI_MOCK", raising=False)
    large_shard = {
        "shard_id": "large-shard",
        "pr_head_sha": "abc123",
        "contract_sha256": "contract_sha",
        "review_plan_sha256": "plan_sha",
        "evidence_sources": {"big": "x" * 300_000},
        "prompt_chars": 300_001,
    }
    permit = {
        "pipeline_id": PIPELINE_ID,
        "pr_head_sha": "abc123",
        "consumed": False,
        "expires_at": "2099-12-31T23:59:59Z",
    }
    result = pipe._call_codex_cli_for_shard(large_shard, permit)
    assert result["verdict"] == "ERROR"
    assert result.get("error_reason") == "input_too_large"

    # ERROR는 REJECT가 아니므로 집계에서도 reject로 카운트되지 않는다.
    agg = pipe._aggregate_shard_verdicts([result], required_shard_ids=["large-shard"], pr_head_sha="abc123")
    assert agg["rejected_shards"] == 0
    assert agg["verdict"] == "ERROR"


# ---------------------------------------------------------------------------
# TC-31: 모든 shard APPROVE + 동일 SHA + required 존재 → APPROVED
# ---------------------------------------------------------------------------
def test_tc31_all_shards_approve(pipe):
    shard_results = [
        {"shard_id": "s1", "verdict": "APPROVE_TO_USER", "findings": [], "pr_head_sha": "abc123"},
        {"shard_id": "s2", "verdict": "APPROVE_TO_USER", "findings": [], "pr_head_sha": "abc123"},
    ]
    agg = pipe._aggregate_shard_verdicts(
        shard_results,
        required_shard_ids=["s1", "s2"],
        pr_head_sha="abc123",
        contract_sha="c_sha",
        review_plan_sha="r_sha",
    )
    assert agg["verdict"] == "APPROVED", f"expected APPROVED, got {agg['verdict']}"
    assert agg["approved_shards"] == 2
    assert agg["error_shards"] == 0


# ---------------------------------------------------------------------------
# TC-31b: missing required shard → ERROR
# ---------------------------------------------------------------------------
def test_tc31b_missing_required_shard_errors(pipe):
    shard_results = [
        {"shard_id": "s1", "verdict": "APPROVE_TO_USER", "findings": [], "pr_head_sha": "abc123"},
    ]
    agg = pipe._aggregate_shard_verdicts(
        shard_results, required_shard_ids=["s1", "s2"], pr_head_sha="abc123",
    )
    assert agg["verdict"] == "ERROR"
    assert "s2" in agg.get("missing_shard_ids", [])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
