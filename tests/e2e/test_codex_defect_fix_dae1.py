"""test_codex_defect_fix_dae1.py — IMP-20260712-DAE1 12개 결함 수정 회귀 테스트.

TC-D1: 빈 review_epoch → codex_review_epoch_missing BLOCKED (Defect 1)
TC-D2: epoch_legacy 이력 NON_CONVERGING 감지 (Defect 2/8)
TC-D3: findings=[] REJECT → ERROR, reject_count 불변 (Defect 6)
TC-D4: in_scope=0 + env_untrusted=0 → reject_count_delta=0 불변식 (Defect 7)
TC-D5: circuit breaker 발동 (Defect 5/9): reject_count_5x / same_category_3x
TC-D6: NON_CONVERGING 후 재실행 차단 (Defect 10)
TC-D7: 이전 epoch history 보존, counter 초기화 없음 (Defect 7/8)
TC-D8: NDJSON REJECT 경로에서 findings 분류 필드 전파 검증 (Defect 4)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pipeline  # noqa: E402


# ------------------------------------------------------------------ #
# TC-D1: review_epoch 비어있으면 codex_review_epoch_missing BLOCKED
# ------------------------------------------------------------------ #
def test_d1_epoch_missing_blocks_cli(tmp_path: Path) -> None:
    """빈 review_epoch에서 CLI를 호출하지 않고 codex_review_epoch_missing으로 BLOCKED."""
    state_path = tmp_path / "state.json"
    state_data = {
        "pipeline_id": "IMP-TEST-D1",
        "current_phase": "dev",
        "external_gates": {},
        # codex_review_contract_migration 없음 → review_epoch 비어있음
    }
    state_path.write_text(json.dumps(state_data), encoding="utf-8")

    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)

    result = subprocess.run(
        [sys.executable, str(_ROOT / "pipeline.py"), "gates", "codex-review"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_ROOT),
        timeout=60,
    )
    assert result.returncode != 0, "review_epoch 없으면 BLOCKED 이어야 함"
    combined = result.stdout + result.stderr
    assert "codex_review_epoch_missing" in combined, (
        f"failure_code=codex_review_epoch_missing이 출력에 없음: {combined[:500]}"
    )


# ------------------------------------------------------------------ #
# TC-D2: epoch_legacy 이력에서 circuit breaker NON_CONVERGING 감지 (Defect 2/8)
# ------------------------------------------------------------------ #
def test_d2_epoch_legacy_non_converging_detected() -> None:
    """review_epoch 필드가 없는 legacy 이력은 epoch_legacy로 정규화되어 CB가 발동한다."""
    # review_epoch 없는 legacy 항목 5개(distinct category) → reject_count_5x.
    legacy_hist = [
        {
            "status": "REJECTED",
            "verdict_scope": "IN_SCOPE",
            "root_cause_category": "legacy_cat_%d" % i,
            "counts_toward_reject_rate_limit": True,
        }
        for i in range(5)
    ]
    cb = pipeline._check_codex_circuit_breaker(legacy_hist, "epoch_legacy")
    assert cb["triggered"], f"epoch_legacy 5회 REJECT는 NON_CONVERGING이어야 함: {cb}"
    assert cb["effective_rejects"] == 5, f"effective_rejects=5여야 함: {cb}"


# ------------------------------------------------------------------ #
# TC-D3: findings=[] REJECT → ERROR (parse_failure), reject_count 불변
# ------------------------------------------------------------------ #
def test_d3_empty_findings_reject_is_error() -> None:
    """findings=[] + verdict=REJECT → _parse_json_verdict가 None 반환 (parse_failure)."""
    stdout_with_empty_findings = json.dumps({
        "verdict": "REJECT",
        "findings": [],
        "reason": "test reason",
    })
    result = pipeline._parse_json_verdict(stdout_with_empty_findings)
    assert result is None, (
        f"findings=[] REJECT는 None(parse_failure)이어야 하지만 {result!r}을 반환했음"
    )


def test_d3_empty_findings_in_ndjson() -> None:
    """NDJSON 경로에서 findings=[] REJECT는 parse_failure."""
    ndjson = (
        '{"type":"thread.started"}\n'
        '{"type":"turn.started"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":'
        + json.dumps(json.dumps({"verdict": "REJECT", "findings": [], "reason": "x"}))
        + '}}\n'
    )
    result = pipeline._run_codex_cli_review(0, ndjson, "")
    assert result["status"] == "ERROR", f"findings=[] REJECT는 ERROR여야 함: {result}"
    assert result.get("error_type") == "parse_failure", (
        f"error_type이 parse_failure여야 함: {result}"
    )


# ------------------------------------------------------------------ #
# TC-D4: in_scope_count=0 + environment_untrusted_count=0 → reject_count_delta=0
# ------------------------------------------------------------------ #
def test_d4_zero_in_scope_delta_invariant() -> None:
    """in_scope=0 and env_untrusted=0 이면 reject_count_delta=0 (불변식)."""
    out_cat = (
        list(pipeline.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC)[0]
        if pipeline.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC
        else "supply_chain_provenance_gap"
    )
    findings = [
        {
            "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
            "severity": "P2",
            "root_cause_category": out_cat,
            "evidence": "test",
            "reproduction": "test",
            "required_fix": "test",
            "acceptance_criteria": ["test"],
        }
    ]
    cls = pipeline._classify_codex_findings(findings)
    assert cls["in_scope_count"] == 0, f"in_scope_count이 0이어야 함: {cls}"
    assert cls["reject_count_delta"] == 0, f"reject_count_delta이 0이어야 함: {cls}"


# ------------------------------------------------------------------ #
# TC-D5: circuit breaker 발동 (Defect 5/9)
# ------------------------------------------------------------------ #
def test_d5_circuit_breaker_5x_reject() -> None:
    """동일 epoch에서 5회 IN_SCOPE REJECT(서로 다른 category) → reject_count_5x 발동.

    NOTE: same_category(3회) 규칙이 reject_count(5회) 규칙보다 먼저 평가되므로,
    reject_count_5x 규칙을 정확히 검증하려면 category가 서로 달라야 한다.
    """
    epoch = "epoch_20260101_001"
    history = [
        {
            "review_epoch": epoch,
            "status": "REJECTED",
            "verdict_scope": "IN_SCOPE",
            "root_cause_category": "category_%d" % i,
            "counts_toward_reject_rate_limit": True,
        }
        for i in range(5)
    ]
    cb = pipeline._check_codex_circuit_breaker(history, epoch)
    assert cb["triggered"], f"5회 REJECT 후 circuit breaker가 발동해야 함: {cb}"
    assert "reject_count_5x" in cb["reason"], f"reason이 reject_count_5x여야 함: {cb}"


def test_d5_circuit_breaker_3x_same_category() -> None:
    """동일 category 3회 반복 → same_category_3x 발동."""
    epoch = "epoch_20260101_001"
    history = [
        {
            "review_epoch": epoch,
            "status": "REJECTED",
            "verdict_scope": "IN_SCOPE",
            "root_cause_category": "duplicate_category",
            "counts_toward_reject_rate_limit": True,
        }
        for _ in range(3)
    ]
    cb = pipeline._check_codex_circuit_breaker(history, epoch)
    assert cb["triggered"], f"3회 same_category 후 circuit breaker가 발동해야 함: {cb}"
    assert "same_category_3x" in cb["reason"], f"reason이 same_category_3x여야 함: {cb}"


# ------------------------------------------------------------------ #
# TC-D6: NON_CONVERGING 후 재실행 차단
# ------------------------------------------------------------------ #
def test_d6_non_converging_blocks_rerun(tmp_path: Path) -> None:
    """이전 상태가 NON_CONVERGING이면 codex_review_non_converging_blocked BLOCKED."""
    state_path = tmp_path / "state.json"
    state_data = {
        "pipeline_id": "IMP-TEST-D6",
        "current_phase": "dev",
        "external_gates": {},
        "codex_review_contract_migration": {
            "review_epoch": "epoch_20260101_001",
        },
    }
    state_path.write_text(json.dumps(state_data), encoding="utf-8")

    # NON_CONVERGING 결과 파일 생성.
    #   _codex_review_result_path()는 PIPELINE_STATE_PATH의 부모 디렉토리 하위
    #   .pipeline/codex_review_result.json을 SSoT 경로로 사용한다.
    pipeline_dir = tmp_path / ".pipeline"
    pipeline_dir.mkdir(parents=True)
    result_file = pipeline_dir / "codex_review_result.json"
    result_file.write_text(
        json.dumps({
            "pipeline_id": "IMP-TEST-D6",
            "status": "NON_CONVERGING",
            "reject_count": 5,
            "cli_error_count": 0,
        }),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)

    result = subprocess.run(
        [sys.executable, str(_ROOT / "pipeline.py"), "gates", "codex-review"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_ROOT),
        timeout=60,
    )
    assert result.returncode != 0, "NON_CONVERGING 상태에서 BLOCKED여야 함"
    combined = result.stdout + result.stderr
    assert "non_converging_blocked" in combined or "NON_CONVERGING" in combined, (
        f"non_converging_blocked 관련 메시지가 없음: {combined[:500]}"
    )


# ------------------------------------------------------------------ #
# TC-D7: 이전 epoch history 보존 - counter 초기화 없음
# ------------------------------------------------------------------ #
def test_d7_history_preserved_no_reset() -> None:
    """circuit breaker가 이전 epoch history를 초기화하지 않고 epoch 격리한다."""
    old_epoch = "epoch_20260101_001"
    new_epoch = "epoch_20260102_001"
    history = [
        # 이전 epoch 항목 (새 epoch 계수에 포함되지 않아야 함)
        {
            "review_epoch": old_epoch,
            "status": "REJECTED",
            "verdict_scope": "IN_SCOPE",
            "root_cause_category": "cat_A",
            "counts_toward_reject_rate_limit": True,
        }
        for _ in range(5)
    ]
    # 새 epoch 항목 (계수되어야 함 - 1개)
    history.append({
        "review_epoch": new_epoch,
        "status": "REJECTED",
        "verdict_scope": "IN_SCOPE",
        "root_cause_category": "cat_B",
        "counts_toward_reject_rate_limit": True,
    })
    cb = pipeline._check_codex_circuit_breaker(history, new_epoch)
    assert not cb["triggered"], f"새 epoch에서는 1회만 있으므로 CB가 발동하지 않아야 함: {cb}"
    assert cb["effective_rejects"] == 1, f"새 epoch 유효 reject가 1이어야 함: {cb}"


# ------------------------------------------------------------------ #
# TC-D8: NDJSON REJECT 경로에서 findings 분류 필드 전파
# ------------------------------------------------------------------ #
def _ndjson_agent_message(verdict_json: str) -> str:
    """agent_message NDJSON 스트림을 조립한다."""
    return (
        '{"type":"thread.started"}\n'
        '{"type":"turn.started"}\n'
        + '{"type":"item.completed","item":{"type":"agent_message","text":'
        + json.dumps(verdict_json)
        + '}}\n'
    )


def test_d8_ndjson_reject_propagates_findings_fields() -> None:
    """NDJSON 경로 REJECT가 findings scope 분류 필드를 전파함.

    in_scope_count=1인 finding이 있으면 reject_count_delta=1이 결과에 전파되어야 한다.
    """
    in_scope_cat = (
        list(pipeline.CODEX_BOUNDED_TRUST_IN_SCOPE)[0]
        if pipeline.CODEX_BOUNDED_TRUST_IN_SCOPE
        else "trust_chain_root_modification"
    )
    finding_in_scope = {
        "scope": "IN_SCOPE",
        "severity": "P0",
        "root_cause_category": in_scope_cat,
        "evidence": "test evidence",
        "reproduction": "test reproduction",
        "required_fix": "test fix",
        "acceptance_criteria": ["test criterion"],
    }
    verdict_json = json.dumps({
        "verdict": "REJECT",
        "findings": [finding_in_scope],
        "reason": "test findings reject",
    })
    result = pipeline._run_codex_cli_review(0, _ndjson_agent_message(verdict_json), "")
    assert result["status"] == "REJECTED", f"REJECTED여야 함: {result}"
    # Defect 4 fix: findings 분류 필드가 전파되어야 함.
    assert "in_scope_count" in result, f"in_scope_count이 결과에 없음: {result}"
    assert result["in_scope_count"] == 1, f"in_scope_count=1이어야 함: {result}"
    assert result.get("reject_count_delta") == 1, f"reject_count_delta=1이어야 함: {result}"


def test_d8_ndjson_reject_out_of_scope_delta_zero() -> None:
    """NDJSON 경로에서 out_of_scope_only REJECT → reject_count_delta=0 전파."""
    out_scope_cat = (
        list(pipeline.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC)[0]
        if pipeline.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC
        else "supply_chain_provenance_gap"
    )
    finding_out = {
        "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
        "severity": "P2",
        "root_cause_category": out_scope_cat,
        "evidence": "test",
        "reproduction": "test",
        "required_fix": "test",
        "acceptance_criteria": ["test"],
    }
    verdict_json = json.dumps({
        "verdict": "REJECT",
        "findings": [finding_out],
        "reason": "test out of scope",
    })
    result = pipeline._run_codex_cli_review(0, _ndjson_agent_message(verdict_json), "")
    assert "reject_count_delta" in result, f"reject_count_delta이 결과에 없음: {result}"
    assert result["reject_count_delta"] == 0, (
        f"out_of_scope reject_count_delta는 0이어야 함: {result}"
    )
