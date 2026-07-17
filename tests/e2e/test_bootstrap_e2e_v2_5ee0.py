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


# ===========================================================================
# REJECT#3 P0-1~P0-5 검증 (TC-32 ~ TC-42)
# ===========================================================================


def _write_permit(pipe, review_plan_sha="rp_sha", contract_sha="c_sha",
                  pr_head_sha="head123", consumed=False):
    """격리된 경로에 permit 파일을 기록하고 경로를 반환한다."""
    permit_path = pipe._shard_review_permit_path()
    permit_path.parent.mkdir(parents=True, exist_ok=True)
    permit = {
        "pipeline_id": PIPELINE_ID,
        "pr_head_sha": pr_head_sha,
        "review_plan_sha": review_plan_sha,
        "contract_sha": contract_sha,
        "issued_at": "2026-07-17T00:00:00Z",
        "expires_at": "2099-12-31T23:59:59Z",
        "consumed": consumed,
        "permit_id": "test-permit-id",
    }
    permit_path.write_text(json.dumps(permit), encoding="utf-8")
    return permit_path


# ---------------------------------------------------------------------------
# TC-32: review_plan_sha 불일치 permit → permit_review_plan_stale (P0-1)
# ---------------------------------------------------------------------------
def test_tc32_permit_review_plan_stale_blocks(pipe, tmp_path, monkeypatch):
    """permit.review_plan_sha != 현재 → permit_review_plan_stale로 SystemExit, CLI 미호출."""
    state_path = tmp_path / "pipeline_state.json"
    _write_isolated_state(state_path)
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_path))
    _write_permit(pipe, review_plan_sha="sha_A", contract_sha="cX", pr_head_sha="head123")

    with pytest.raises(SystemExit):
        # current review_plan_sha "sha_B" (불일치) → 차단
        pipe._check_and_consume_permit(PIPELINE_ID, "head123", "sha_B", "cX")


# ---------------------------------------------------------------------------
# TC-33: contract_sha 불일치 permit → permit_contract_stale (P0-1)
# ---------------------------------------------------------------------------
def test_tc33_permit_contract_stale_blocks(pipe, tmp_path, monkeypatch):
    """permit.contract_sha != 현재 → permit_contract_stale로 SystemExit."""
    state_path = tmp_path / "pipeline_state.json"
    _write_isolated_state(state_path)
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_path))
    _write_permit(pipe, review_plan_sha="sha_A", contract_sha="cX", pr_head_sha="head123")

    with pytest.raises(SystemExit):
        # review_plan_sha 일치("sha_A")하지만 contract_sha 불일치("cY") → 차단
        pipe._check_and_consume_permit(PIPELINE_ID, "head123", "sha_A", "cY")


# ---------------------------------------------------------------------------
# TC-34: review_plan_sha + contract_sha 모두 일치 → 정상 소비 (P0-1)
# ---------------------------------------------------------------------------
def test_tc34_permit_all_match_consumes(pipe, tmp_path, monkeypatch):
    """모든 SHA 일치 시 permit이 정상 소비되고 contract_sha가 보존된다."""
    state_path = tmp_path / "pipeline_state.json"
    _write_isolated_state(state_path)
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_path))
    _write_permit(pipe, review_plan_sha="sha_A", contract_sha="cX", pr_head_sha="head123")

    consumed = pipe._check_and_consume_permit(PIPELINE_ID, "head123", "sha_A", "cX")
    assert consumed["consumed"] is True
    assert consumed["contract_sha"] == "cX"
    # 재사용 시 permit_consumed로 차단
    with pytest.raises(SystemExit):
        pipe._check_and_consume_permit(PIPELINE_ID, "head123", "sha_A", "cX")


# ---------------------------------------------------------------------------
# TC-35: 결과 파일의 유효 APPROVE verdict 파싱 (P0-2)
# ---------------------------------------------------------------------------
def test_tc35_parser_valid_approve(pipe, tmp_path):
    """--output-last-message 파일의 유효 APPROVE_TO_USER를 파싱한다."""
    result_file = tmp_path / "verdict.json"
    result_file.write_text(json.dumps({"verdict": "APPROVE_TO_USER", "findings": []}), encoding="utf-8")
    out = pipe._parse_codex_cli_shard_output(str(result_file), "s1", "abc")
    assert out["verdict"] == "APPROVE_TO_USER"
    assert out["findings"] == []
    assert "error_reason" not in out


# ---------------------------------------------------------------------------
# TC-36: 결과 파일 없음 → verdict_parse_failure ERROR (P0-2)
# ---------------------------------------------------------------------------
def test_tc36_parser_missing_file_is_error(pipe, tmp_path):
    """결과 파일이 없으면 verdict_parse_failure ERROR(reject_count 미소모)."""
    missing = tmp_path / "nonexistent.json"
    out = pipe._parse_codex_cli_shard_output(str(missing), "s1", "abc")
    assert out["verdict"] == "ERROR"
    assert out["error_reason"] == "verdict_parse_failure"


# ---------------------------------------------------------------------------
# TC-37: REJECT + 빈 findings → ERROR / REJECT + findings → REJECT (P0-2)
# ---------------------------------------------------------------------------
def test_tc37_reject_findings_consistency(pipe, tmp_path):
    """REJECT는 최소 1개 finding이 있어야 유효하다."""
    empty_reject = tmp_path / "empty_reject.json"
    empty_reject.write_text(json.dumps({"verdict": "REJECT", "findings": []}), encoding="utf-8")
    out = pipe._parse_codex_cli_shard_output(str(empty_reject), "s1", "abc")
    assert out["verdict"] == "ERROR"
    assert out["error_reason"] == "verdict_parse_failure"

    valid_reject = tmp_path / "valid_reject.json"
    valid_reject.write_text(json.dumps({"verdict": "REJECT", "findings": [{"issue": "x"}]}), encoding="utf-8")
    out2 = pipe._parse_codex_cli_shard_output(str(valid_reject), "s1", "abc")
    assert out2["verdict"] == "REJECT"
    assert len(out2["findings"]) == 1


# ---------------------------------------------------------------------------
# TC-38: APPROVE + 비어있지 않은 findings / 추가 필드 → ERROR (P0-2 schema)
# ---------------------------------------------------------------------------
def test_tc38_approve_findings_and_schema_violation(pipe, tmp_path):
    """APPROVE인데 findings가 있거나 스키마 위반이면 verdict_parse_failure."""
    approve_with_findings = tmp_path / "aw.json"
    approve_with_findings.write_text(
        json.dumps({"verdict": "APPROVE_TO_USER", "findings": [{"issue": "x"}]}), encoding="utf-8"
    )
    out = pipe._parse_codex_cli_shard_output(str(approve_with_findings), "s1", "abc")
    assert out["verdict"] == "ERROR"
    assert out["error_reason"] == "verdict_parse_failure"

    # additionalProperties=False 위반
    extra_field = tmp_path / "extra.json"
    extra_field.write_text(
        json.dumps({"verdict": "APPROVE_TO_USER", "findings": [], "unexpected": 1}), encoding="utf-8"
    )
    out2 = pipe._parse_codex_cli_shard_output(str(extra_field), "s1", "abc")
    assert out2["verdict"] == "ERROR"

    # verdict enum 위반
    bad_enum = tmp_path / "bad.json"
    bad_enum.write_text(json.dumps({"verdict": "MAYBE"}), encoding="utf-8")
    out3 = pipe._parse_codex_cli_shard_output(str(bad_enum), "s1", "abc")
    assert out3["verdict"] == "ERROR"


# ---------------------------------------------------------------------------
# TC-39: CODEX_CLI_MOCK 제거 확인 + _cli_factory DI seam (P0-3)
# ---------------------------------------------------------------------------
def test_tc39_mock_removed_factory_works(pipe, monkeypatch):
    """CODEX_CLI_MOCK=1은 더 이상 승인하지 않고, 결정성은 _cli_factory로만 확보된다."""
    monkeypatch.setenv("CODEX_CLI_MOCK", "1")
    shard = {"shard_id": "s1", "pr_head_sha": "a" * 40}
    # mock 분기 제거 → permit None이므로 codex_cli_not_connected ERROR (승인 아님)
    out = pipe._call_codex_cli_for_shard(shard)
    assert out["verdict"] != "APPROVE_TO_USER"
    assert out["verdict"] == "ERROR"

    # _cli_factory DI seam은 정상 동작
    def _fac(s, _p):
        return {"shard_id": s["shard_id"], "verdict": "APPROVE_TO_USER",
                "findings": [], "pr_head_sha": s["pr_head_sha"]}
    out2 = pipe._call_codex_cli_for_shard(shard, _cli_factory=_fac)
    assert out2["verdict"] == "APPROVE_TO_USER"


# ---------------------------------------------------------------------------
# TC-40: fake_codex.py가 --output-last-message에 verdict 기록 + 파서 통합 (P0-3)
# ---------------------------------------------------------------------------
def test_tc40_fake_codex_writes_and_parses(pipe, tmp_path):
    """fake_codex.py가 결과 파일을 기록하고, 그 파일을 파서가 읽어 verdict를 산출한다."""
    fake_codex = REPO_ROOT / "tests" / "e2e" / "fake_codex.py"
    assert fake_codex.exists(), "fake_codex.py가 존재해야 함"
    out_file = tmp_path / "last_message.json"

    # APPROVE 경로
    env = {**os.environ, "FAKE_CODEX_VERDICT": "APPROVE_TO_USER", "FAKE_CODEX_FINDINGS": "[]"}
    proc = subprocess.run(
        [sys.executable, str(fake_codex), "exec", "--json",
         "--output-last-message", str(out_file), "-"],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0
    parsed = pipe._parse_codex_cli_shard_output(str(out_file), "s1", "abc")
    assert parsed["verdict"] == "APPROVE_TO_USER"

    # REJECT 경로
    env2 = {**os.environ, "FAKE_CODEX_VERDICT": "REJECT",
            "FAKE_CODEX_FINDINGS": json.dumps([{"issue": "bug"}])}
    out_file2 = tmp_path / "last_message2.json"
    subprocess.run(
        [sys.executable, str(fake_codex), "--output-last-message", str(out_file2)],
        env=env2, capture_output=True, text=True, timeout=60, check=False,
    )
    parsed2 = pipe._parse_codex_cli_shard_output(str(out_file2), "s2", "abc")
    assert parsed2["verdict"] == "REJECT"


# ---------------------------------------------------------------------------
# TC-41: OS별 authorize-run 안내 명령 (P0-4)
# ---------------------------------------------------------------------------
def test_tc41_os_specific_authorize_hint(pipe, monkeypatch):
    """win32는 PowerShell $env: 형식, POSIX는 inline env prefix 형식으로 안내한다."""
    monkeypatch.setattr(pipe.sys, "platform", "win32")
    win_hint = pipe._codex_authorize_cmd_hint()
    assert "$env:CODEX_RUN_AUTHORIZED" in win_hint
    assert "Remove-Item Env:CODEX_RUN_AUTHORIZED" in win_hint

    monkeypatch.setattr(pipe.sys, "platform", "linux")
    posix_hint = pipe._codex_authorize_cmd_hint()
    assert posix_hint.startswith("CODEX_RUN_AUTHORIZED=1 python")
    assert "$env:" not in posix_hint


# ---------------------------------------------------------------------------
# TC-42: working tree 정합성 검증 (P0-5)
# ---------------------------------------------------------------------------
def test_tc42_working_tree_integrity(pipe, monkeypatch):
    """clean working tree는 통과, dirty tree는 working_tree_dirty로 SystemExit."""
    import types

    def _fake_run_clean(cmd, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pipe.subprocess, "run", _fake_run_clean)
    # clean → 예외 없이 통과
    pipe._verify_working_tree_integrity()

    def _fake_run_dirty(cmd, **kwargs):
        return types.SimpleNamespace(returncode=0, stdout="pipeline.py\ncore/x.py\n", stderr="")

    monkeypatch.setattr(pipe.subprocess, "run", _fake_run_dirty)
    with pytest.raises(SystemExit):
        pipe._verify_working_tree_integrity()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
