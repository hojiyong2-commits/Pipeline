"""
test_clarification_gate.py — IMP-20260523-D80A PM Clarification Gate 테스트

# [Purpose]: pipeline.py의 PM Clarification Gate (_check_pm_clarification_gate + cmd_check + cmd_done)가
#            PM 모호성을 Dev 진입 전에 차단하는지 검증
# [Assumptions]: tests/oracles/IMP-20260523-E047/TC-01~04 oracle 파일이 사전 작성되어 있고,
#                pipeline_state.json은 백업/복원 가능한 단일 파일 형태
# [Vulnerability & Risks]: pipeline_state.json을 테스트 중 임시 수정하므로,
#                          예외 발생 시 finally 블록에서 반드시 복원해야 함.
#                          subprocess 호출이 60초 이상 걸리면 timeout 발생
# [Improvement]: tmp_path fixture로 PIPELINE_STATE_PATH env var 주입 방식으로 격리도 향상 가능 (현재는 직접 백업/복원)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ORACLE_DIR = Path(__file__).parent / "oracles" / "IMP-20260523-E047"
PIPELINE_STATE = Path(__file__).parent.parent / "pipeline_state.json"
PIPELINE_PY = Path(__file__).parent.parent / "pipeline.py"


def load_state() -> dict:
    """현재 pipeline_state.json을 dict로 로드. 파일 없으면 빈 dict."""
    if PIPELINE_STATE.exists():
        return json.loads(PIPELINE_STATE.read_text(encoding="utf-8"))
    return {}


def patch_and_save(base_state: dict, patch: dict) -> dict:
    """base_state에 patch를 얕은 merge하여 pipeline_state.json에 기록."""
    merged = {**base_state, **patch}
    PIPELINE_STATE.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return merged


def restore_state(backup: dict) -> None:
    """백업 dict를 그대로 pipeline_state.json에 기록하여 원복."""
    PIPELINE_STATE.write_text(
        json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_check(extra_args=None) -> subprocess.CompletedProcess:
    """`python pipeline.py check --phase dev --codex-review-waiver legacy-bootstrap` 실행."""
    cmd = [
        sys.executable,
        str(PIPELINE_PY),
        "check",
        "--phase",
        "dev",
        "--codex-review-waiver",
        "legacy-bootstrap",
    ]
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    return result


def _run_tc(tc_dir: Path):
    """단일 TC를 실행. (result, expected, backup) 반환.

    호출자는 try/finally로 restore_state(backup)을 호출해야 함.
    """
    inp = json.loads((tc_dir / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((tc_dir / "expected.json").read_text(encoding="utf-8"))
    backup = load_state()
    try:
        patch_and_save(backup, inp.get("pipeline_state_patch", {}))
        result = run_check()
        return result, exp, backup
    except Exception:
        restore_state(backup)
        raise


def test_tc01_clarification_needed_true_blocks_dev():
    """TC-01: clarification_needed=true이면 check --phase dev가 exit 1."""
    tc_dir = ORACLE_DIR / "TC-01"
    result, exp, backup = _run_tc(tc_dir)
    try:
        combined = result.stdout + result.stderr
        assert result.returncode == exp["exit_code"], (
            f"exit code 불일치: {result.returncode} != {exp['exit_code']}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        for s in exp.get("stdout_contains", []):
            assert s in combined, (
                f"'{s}'가 출력에 없음\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )
    finally:
        restore_state(backup)


def test_tc02_empty_acceptance_criteria_blocks_dev():
    """TC-02: acceptance_criteria=[] 이면 clarification_needed=false여도 exit 1."""
    tc_dir = ORACLE_DIR / "TC-02"
    result, exp, backup = _run_tc(tc_dir)
    try:
        combined = result.stdout + result.stderr
        assert result.returncode == exp["exit_code"], (
            f"exit code 불일치: {result.returncode} != {exp['exit_code']}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        for s in exp.get("stdout_contains", []):
            assert s in combined, (
                f"'{s}'가 출력에 없음\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )
    finally:
        restore_state(backup)


def test_tc03_valid_criteria_allows_dev():
    """TC-03: clarification_needed=false + criteria 있으면 exit 0."""
    tc_dir = ORACLE_DIR / "TC-03"
    result, exp, backup = _run_tc(tc_dir)
    try:
        combined = result.stdout + result.stderr
        assert result.returncode == exp["exit_code"], (
            f"exit code 불일치: {result.returncode} != {exp['exit_code']}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        for s in exp.get("stdout_not_contains", []):
            assert s not in combined, (
                f"'{s}'가 출력에 있으면 안 됨\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )
    finally:
        restore_state(backup)


def test_tc04_legacy_no_field_passes():
    """TC-04: pm_clarification_gate 필드 없는 레거시 상태 → exit 0 (하위 호환)."""
    tc_dir = ORACLE_DIR / "TC-04"
    inp = json.loads((tc_dir / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((tc_dir / "expected.json").read_text(encoding="utf-8"))
    backup = load_state()
    try:
        # TC-04는 pm_clarification_gate 필드가 명시적으로 없어야 함.
        # backup에 기존 필드가 있을 수 있으므로 명시적으로 제거.
        merged = {**backup}
        if "pm_clarification_gate" in merged:
            merged.pop("pm_clarification_gate")
        # patch_and_save 대신 직접 merge 후 기록
        patch = inp.get("pipeline_state_patch", {})
        merged.update(patch)
        PIPELINE_STATE.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result = run_check()
        combined = result.stdout + result.stderr
        assert result.returncode == exp["exit_code"], (
            f"exit code 불일치: {result.returncode} != {exp['exit_code']}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        for s in exp.get("stdout_not_contains", []):
            assert s not in combined, (
                f"'{s}'가 출력에 있으면 안 됨\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )
    finally:
        restore_state(backup)


if __name__ == "__main__":
    # Self-verification block — pytest 없이 직접 실행 시
    print("[SELF-VERIFY] _check_pm_clarification_gate 단독 검증 시작")
    # pipeline.py 모듈을 직접 import하여 함수 단위 unit test
    sys.path.insert(0, str(PIPELINE_PY.parent))
    import pipeline as _pipeline_mod

    # 1) Legacy state (no field) → PASS
    ok, reason = _pipeline_mod._check_pm_clarification_gate({})
    assert ok is True, f"Legacy state should PASS, got ok={ok}, reason={reason}"

    # 2) clarification_needed=True → FAIL
    ok, reason = _pipeline_mod._check_pm_clarification_gate(
        {"pm_clarification_gate": {"clarification_needed": True, "acceptance_criteria": ["x"]}}
    )
    assert ok is False, "clarification_needed=True should FAIL"
    assert "clarification" in reason.lower()

    # 3) Empty acceptance_criteria → FAIL
    ok, reason = _pipeline_mod._check_pm_clarification_gate(
        {"pm_clarification_gate": {"clarification_needed": False, "acceptance_criteria": []}}
    )
    assert ok is False, "Empty acceptance_criteria should FAIL"
    assert "acceptance_criteria" in reason

    # 4) Valid state → PASS
    ok, reason = _pipeline_mod._check_pm_clarification_gate(
        {"pm_clarification_gate": {"clarification_needed": False, "acceptance_criteria": ["a", "b"]}}
    )
    assert ok is True, f"Valid state should PASS, got ok={ok}, reason={reason}"

    print("[SELF-VERIFY] OK — 4 unit assertions passed")
