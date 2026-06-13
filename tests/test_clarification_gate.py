"""
test_clarification_gate.py — IMP-20260523-D80A PM Clarification Gate 테스트

# [Purpose]: pipeline.py의 PM Clarification Gate (_check_pm_clarification_gate + cmd_check + cmd_done)가
#            PM 모호성을 Dev 진입 전에 차단하는지 검증
# [Assumptions]: tests/oracles/IMP-20260523-E047/TC-01~04 oracle 파일이 사전 작성되어 있고,
#                tmp_path pytest fixture로 테스트마다 독립적인 상태 파일을 사용함
# [Isolation]: PIPELINE_STATE_PATH 환경 변수를 tmp_path 하위 경로로 주입하므로
#              실제 pipeline_state.json을 절대 변경하지 않음
# [Vulnerability & Risks]: subprocess 호출이 60초 이상 걸리면 timeout 발생
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

ORACLE_DIR = Path(__file__).parent / "oracles" / "IMP-20260523-E047"
PIPELINE_PY = Path(__file__).parent.parent / "pipeline.py"

BASE_PM_ATTESTATION_STATE: Dict = {
    "terminal_state": None,
    "current_phase": "dev",
    "phases": {
        "pm": {"status": "DONE"},
        "dev": {"status": "PENDING"},
    },
    "phase_attestations": {
        "enabled": True,
        "phases": {
            "pm": {"status": "PASS"},
        },
    },
}


def _make_state(pm_clarification_gate: Optional[Dict] = None) -> Dict:
    """minimal clean state dict를 생성. pm_clarification_gate를 선택적으로 포함."""
    import copy

    state = copy.deepcopy(BASE_PM_ATTESTATION_STATE)
    if pm_clarification_gate is not None:
        state["pm_clarification_gate"] = pm_clarification_gate
    return state


def run_check(
    extra_args: Optional[list] = None,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """`python pipeline.py check --phase dev` 실행.

    Args:
        extra_args: 추가 CLI 인자.
        env: 서브프로세스에 전달할 환경 변수 (None이면 현재 환경 그대로 사용).
    """
    cmd = [
        sys.executable,
        str(PIPELINE_PY),
        "check",
        "--phase",
        "dev",
    ]
    if extra_args:
        cmd.extend(extra_args)

    effective_env = env if env is not None else None
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=effective_env,
    )
    return result


def test_tc01_clarification_needed_true_blocks_dev(tmp_path: Path) -> None:
    """TC-01: clarification_needed=true이면 check --phase dev가 exit 1."""
    tc_dir = ORACLE_DIR / "TC-01"
    inp = json.loads((tc_dir / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((tc_dir / "expected.json").read_text(encoding="utf-8"))

    state = _make_state(inp.get("pipeline_state_patch", {}).get("pm_clarification_gate"))
    state.update(
        {k: v for k, v in inp.get("pipeline_state_patch", {}).items() if k != "pm_clarification_gate"}
    )

    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    env = {**os.environ, "PIPELINE_STATE_PATH": str(state_file)}
    result = run_check(env=env)
    combined = result.stdout + result.stderr

    assert result.returncode == exp["exit_code"], (
        f"exit code 불일치: {result.returncode} != {exp['exit_code']}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    for s in exp.get("stdout_contains", []):
        assert s in combined, (
            f"'{s}'가 출력에 없음\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


def test_tc02_empty_acceptance_criteria_blocks_dev(tmp_path: Path) -> None:
    """TC-02: acceptance_criteria=[] 이면 clarification_needed=false여도 exit 1."""
    tc_dir = ORACLE_DIR / "TC-02"
    inp = json.loads((tc_dir / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((tc_dir / "expected.json").read_text(encoding="utf-8"))

    state = _make_state(inp.get("pipeline_state_patch", {}).get("pm_clarification_gate"))
    state.update(
        {k: v for k, v in inp.get("pipeline_state_patch", {}).items() if k != "pm_clarification_gate"}
    )

    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    env = {**os.environ, "PIPELINE_STATE_PATH": str(state_file)}
    result = run_check(env=env)
    combined = result.stdout + result.stderr

    assert result.returncode == exp["exit_code"], (
        f"exit code 불일치: {result.returncode} != {exp['exit_code']}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    for s in exp.get("stdout_contains", []):
        assert s in combined, (
            f"'{s}'가 출력에 없음\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


def test_tc03_valid_criteria_allows_dev(tmp_path: Path) -> None:
    """TC-03: clarification_needed=false + criteria 있으면 exit 0."""
    tc_dir = ORACLE_DIR / "TC-03"
    inp = json.loads((tc_dir / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((tc_dir / "expected.json").read_text(encoding="utf-8"))

    state = _make_state(inp.get("pipeline_state_patch", {}).get("pm_clarification_gate"))
    state.update(
        {k: v for k, v in inp.get("pipeline_state_patch", {}).items() if k != "pm_clarification_gate"}
    )

    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    env = {**os.environ, "PIPELINE_STATE_PATH": str(state_file)}
    result = run_check(env=env)
    combined = result.stdout + result.stderr

    assert result.returncode == exp["exit_code"], (
        f"exit code 불일치: {result.returncode} != {exp['exit_code']}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    for s in exp.get("stdout_not_contains", []):
        assert s not in combined, (
            f"'{s}'가 출력에 있으면 안 됨\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


def test_tc04_legacy_no_field_passes(tmp_path: Path) -> None:
    """TC-04: pm_clarification_gate 필드 없는 레거시 상태 → exit 0 (하위 호환)."""
    tc_dir = ORACLE_DIR / "TC-04"
    inp = json.loads((tc_dir / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((tc_dir / "expected.json").read_text(encoding="utf-8"))

    # TC-04는 pm_clarification_gate 필드가 명시적으로 없어야 함.
    state = _make_state(pm_clarification_gate=None)
    patch = inp.get("pipeline_state_patch", {})
    state.update({k: v for k, v in patch.items() if k != "pm_clarification_gate"})
    # pm_clarification_gate 키가 patch에 있어도 강제 제거
    state.pop("pm_clarification_gate", None)

    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    env = {**os.environ, "PIPELINE_STATE_PATH": str(state_file)}
    result = run_check(env=env)
    combined = result.stdout + result.stderr

    assert result.returncode == exp["exit_code"], (
        f"exit code 불일치: {result.returncode} != {exp['exit_code']}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    for s in exp.get("stdout_not_contains", []):
        assert s not in combined, (
            f"'{s}'가 출력에 있으면 안 됨\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


if __name__ == "__main__":
    # Self-verification block — pytest 없이 직접 실행 시
    print("[SELF-VERIFY] _check_pm_clarification_gate 단독 검증 시작")
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
