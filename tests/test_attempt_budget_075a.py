"""
IMP-20260527-075A: Cost/Attempt Budget Gate 단위 테스트

oracle 기반:
  - TC-normal-budget-pass: 한도 내 재시도는 허용
  - TC-normal-budget-blocked: 한도 초과 시 BLOCKED + failure_packet
  - TC-edge-repeat-failure-code: 동일 failure_code 3회 시 REPEAT_FAILURE_CODE
  - TC-edge-reset-no-reason: --reason 누락 시 비-0 exit

모든 CLI 테스트는 PIPELINE_STATE_PATH 격리된 임시 state 파일 사용.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PIPELINE_PY = str(Path(__file__).parent.parent / "pipeline.py")
ORACLE_DIR = Path(__file__).parent / "oracles" / "IMP-20260527-075A"


def _load_pipeline_module():
    spec = importlib.util.spec_from_file_location("pipeline_under_test", PIPELINE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_isolated_state(extra=None):
    """격리된 임시 pipeline_state.json 구조 반환 (PIPELINE_STATE_PATH용)."""
    base = {
        "pipeline_id": "IMP-20260527-075A",
        "current_phase": "dev",
        "phases": {},
        "phase_states": {},
        "terminal_state": None,
        "attempt_budget": {
            "config": {
                "dev_max_attempts": 3,
                "qa_max_attempts": 3,
                "gate_max_attempts": 5,
                "repeat_failure_code_threshold": 3,
            },
            "attempts": {"dev": [], "qa": [], "gate": []},
            "blocked_phases": {},
        },
    }
    if extra:
        base.update(extra)
    return base


def test_budget_pass_within_limit():
    """oracle TC-normal-budget-pass: 1회 FAIL 후 한도 내라 blocked=False."""
    mod = _load_pipeline_module()
    state = make_isolated_state()
    state["attempt_budget"]["attempts"]["dev"] = [
        {"outcome": "FAIL", "failure_code": "technical_test_failed"}
    ]
    mod._ensure_attempt_budget_keys(state)
    result = mod._check_attempt_budget(state, "dev")

    assert result["blocked"] is False
    assert result["attempts_used"] == 1
    assert result["max_attempts"] == 3


def test_budget_exceeded_blocks_phase():
    """oracle TC-normal-budget-blocked: 3회 FAIL 후 blocked=True."""
    mod = _load_pipeline_module()
    state = make_isolated_state()
    # 3회 모두 다른 failure_code로 만들어 BUDGET_EXCEEDED 트리거 (REPEAT 회피)
    state["attempt_budget"]["attempts"]["dev"] = [
        {"outcome": "FAIL", "failure_code": f"err_{i}"} for i in range(3)
    ]
    result = mod._check_attempt_budget(state, "dev")

    assert result["blocked"] is True
    # 동일 failure_code가 아니므로 BUDGET_EXCEEDED
    assert result["failure_code"] == "BUDGET_EXCEEDED"


def test_repeat_failure_code_triggers_rca():
    """oracle TC-edge-repeat-failure-code: 동일 failure_code 3회 -> REPEAT_FAILURE_CODE."""
    mod = _load_pipeline_module()
    state = make_isolated_state()
    for _ in range(3):
        state["attempt_budget"]["attempts"]["dev"].append(
            {"outcome": "FAIL", "failure_code": "technical_test_failed"}
        )

    repeat_fc = mod._detect_repeat_failure_code(state, "dev")
    assert repeat_fc == "technical_test_failed"

    result = mod._check_attempt_budget(state, "dev")
    assert result["blocked"] is True
    assert result["repeat_failure_code"] == "technical_test_failed"


def test_cli_budget_status_command():
    """budget status CLI가 exit 0으로 한국어 출력하고 attempt_budget 유지."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        state = make_isolated_state()
        json.dump(state, f, ensure_ascii=False)
        state_path = f.name

    try:
        env = {**os.environ, "PIPELINE_STATE_PATH": state_path}
        result = subprocess.run(
            [sys.executable, PIPELINE_PY, "budget", "status"],
            capture_output=True, text=True, env=env, encoding='utf-8'
        )
        assert result.returncode == 0, f"exit code: {result.returncode}\nstderr: {result.stderr}"
        output = (result.stdout or "") + (result.stderr or "")
        # 한국어 키워드 확인
        assert any(kw in output for kw in ["재시도", "한도", "dev"])

        final_state = json.loads(Path(state_path).read_text(encoding="utf-8"))
        assert "attempt_budget" in final_state
    finally:
        os.unlink(state_path)


def test_cli_budget_reset_requires_reason():
    """oracle TC-edge-reset-no-reason: --reason 없으면 비-0 exit."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        state = make_isolated_state()
        json.dump(state, f, ensure_ascii=False)
        state_path = f.name

    try:
        env = {**os.environ, "PIPELINE_STATE_PATH": state_path}
        result = subprocess.run(
            [sys.executable, PIPELINE_PY, "budget", "reset", "--phase", "dev"],
            capture_output=True, text=True, env=env, encoding='utf-8'
        )
        assert result.returncode != 0, "reason 없으면 비-0 exit이어야 합니다"
        output = ((result.stdout or "") + (result.stderr or "")).lower()
        assert "reason" in output or "required" in output
    finally:
        os.unlink(state_path)


def test_cli_budget_reset_with_reason():
    """budget reset --reason TEXT 시 attempts 초기화 + exit 0."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        state = make_isolated_state()
        state["attempt_budget"]["attempts"]["dev"] = [
            {"outcome": "FAIL", "failure_code": "technical_test_failed"}
        ]
        json.dump(state, f, ensure_ascii=False)
        state_path = f.name

    try:
        env = {**os.environ, "PIPELINE_STATE_PATH": state_path}
        result = subprocess.run(
            [sys.executable, PIPELINE_PY, "budget", "reset", "--phase", "dev", "--reason", "테스트 확인 후 재시작"],
            capture_output=True, text=True, env=env, encoding='utf-8'
        )
        assert result.returncode == 0, f"exit code: {result.returncode}\nstderr: {result.stderr}"

        final_state = json.loads(Path(state_path).read_text(encoding="utf-8"))
        assert final_state["attempt_budget"]["attempts"]["dev"] == []
    finally:
        os.unlink(state_path)


def test_check_phase_within_budget():
    """budget 한도 내일 때 status는 정상 출력."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        state = make_isolated_state()
        state["attempt_budget"]["attempts"]["dev"] = [
            {"outcome": "FAIL", "failure_code": "technical_test_failed"}
        ]
        json.dump(state, f, ensure_ascii=False)
        state_path = f.name

    try:
        env = {**os.environ, "PIPELINE_STATE_PATH": state_path}
        result = subprocess.run(
            [sys.executable, PIPELINE_PY, "budget", "status"],
            capture_output=True, text=True, env=env, encoding='utf-8'
        )
        assert result.returncode == 0
        output = (result.stdout or "") + (result.stderr or "")
        # 차단 메시지가 없어야 함 (한도 내)
        assert "[차단됨]" not in output
    finally:
        os.unlink(state_path)
