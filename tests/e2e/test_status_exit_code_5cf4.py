"""
test_status_exit_code_5cf4.py — BUG-20260527-5CF4 MT-1 End-to-End CLI Path Tests

# [Purpose]: pipeline.py status 명령이 terminal_state(COMPLETE/FAILED/TERMINATED/None),
#            blocked=true, 활성 파이프라인 없음 모든 케이스에서 항상 exit code 0을
#            반환하는지 subprocess 기반 E2E 테스트로 검증한다. 이전에는
#            state["phases"][phase] KeyError 등으로 unhandled exception이 발생하여
#            exit 1(PowerShell 255)이 반환되는 버그가 있었다.
# [Assumptions]: PIPELINE_STATE_PATH 환경변수가 pipeline.py import 시점에 평가되어
#                subprocess에 주입한 임시 state 파일이 STATE_FILE로 사용됨.
#                pytest tmp_path fixture로 테스트별 독립 격리.
# [Vulnerability & Risks]:
#   - subprocess timeout 30초 초과 시 실패 (정상 status는 1초 내 종료).
#   - cmd_status가 의존하는 보조 함수들(_advisory_status_summary 등)이 예외를
#     던지더라도 status는 exit 0이어야 하므로, 부분 출력 누락은 허용한다.
#   - Windows PowerShell과 POSIX 양쪽에서 동일하게 returncode == 0이어야 한다.

# CLI Evidence Contract (BUG-20260525-39DE):
# - 상태 변경 CLI 호출 없음 (status는 read-only).
# - CLI_EVIDENCE_ALLOW_READ_ONLY: status command is read-only; tests assert stdout
#   tokens AND returncode together to satisfy "PASS judgment requires more than
#   stdout alone" — returncode is the primary contract here, stdout tokens are
#   secondary regression guards.

# [Improvement]: 향후 손상된 JSON 파일(파싱 실패) 케이스, phases 키 부분 누락 등
#                추가 fuzzing 케이스로 확장 가능.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


# pipeline.py는 tests/e2e의 2단계 상위 디렉토리에 위치
PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_status(
    state_file: Optional[Path],
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """`python pipeline.py status` 실행 후 CompletedProcess 반환.

    Args:
        state_file: PIPELINE_STATE_PATH로 주입할 임시 state 파일 경로.
                    None이면 환경변수 미설정 (전역 STATE_FILE 사용).
        timeout: 초 단위 타임아웃 (기본 30초).
    Returns:
        subprocess.CompletedProcess — returncode, stdout, stderr 포함.
    Raises:
        TypeError: timeout이 int가 아닌 경우.
        ValueError: timeout이 0 이하인 경우 (negative not allowed: must be positive).
    """
    if not isinstance(timeout, int):
        raise TypeError(f"timeout must be int, got {type(timeout).__name__}")
    if timeout <= 0:
        # negative not allowed: subprocess timeout은 양의 정수만 허용
        raise ValueError(f"timeout must be > 0, got {timeout}")

    env: Dict[str, str] = {**os.environ}
    if state_file is not None:
        env["PIPELINE_STATE_PATH"] = str(state_file)
    env["PIPELINE_NO_DASHBOARD"] = "1"

    cmd = [sys.executable, str(PIPELINE_PY), "status"]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def write_state(state_file: Path, state: Dict[str, Any]) -> None:
    """임시 state 파일에 JSON 직렬화.

    Args:
        state_file: 작성할 경로.
        state: 직렬화할 state dict.
    Raises:
        TypeError: 인자가 None이거나 타입이 잘못된 경우.
    """
    if state_file is None:
        raise TypeError("state_file must not be None")
    if state is None:
        raise TypeError("state must not be None")
    if not isinstance(state, dict):
        raise TypeError(f"state must be dict, got {type(state).__name__}")
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def make_complete_state() -> Dict[str, Any]:
    """terminal_state=COMPLETE인 최소 state fixture."""
    return {
        "pipeline_id": "TEST-COMPLETE-5CF4",
        "description": "BUG-20260527-5CF4 regression — COMPLETE state",
        "type": "FEAT",
        "created_at": "2026-05-27T00:00:00Z",
        "updated_at": "2026-05-27T00:00:00Z",
        "current_phase": "COMPLETE",
        "terminal_state": "COMPLETE",
        "blocked": False,
        "blocked_reason": None,
        "phases": {},
        "event_log": [],
    }


def make_blocked_state() -> Dict[str, Any]:
    """blocked=true + current_phase=dev인 최소 state fixture."""
    return {
        "pipeline_id": "TEST-BLOCKED-5CF4",
        "description": "BUG-20260527-5CF4 regression — blocked state",
        "type": "BUG",
        "created_at": "2026-05-27T00:00:00Z",
        "updated_at": "2026-05-27T00:00:00Z",
        "current_phase": "dev",
        "terminal_state": None,
        "blocked": True,
        "blocked_reason": "test reason for E2E",
        "phases": {},
        "event_log": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_status_exits_zero_when_complete(tmp_path: Path) -> None:
    """terminal_state=COMPLETE인 state에서 status는 항상 exit 0이어야 한다.

    이전 버그: state["phases"][phase] KeyError → unhandled exception → exit 1/255.
    수정 후: 누락된 phase는 PENDING으로 표시되며 exit 0 반환.
    """
    state_file = tmp_path / "state_complete.json"
    write_state(state_file, make_complete_state())

    result = run_status(state_file)

    # Primary contract: returncode must be 0 regardless of terminal_state.
    assert result.returncode == 0, (
        f"status returned non-zero exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    # Secondary regression guards: critical stdout tokens preserved.
    assert "파이프라인:" in result.stdout, f"missing '파이프라인:' in stdout:\n{result.stdout}"
    assert "Phase 현황" in result.stdout, f"missing 'Phase 현황' in stdout:\n{result.stdout}"
    assert "terminal_state=" in result.stdout, (
        f"missing 'terminal_state=' marker in stdout:\n{result.stdout}"
    )


def test_status_exits_zero_when_blocked(tmp_path: Path) -> None:
    """blocked=true인 state에서도 status는 안내 출력 후 exit 0이어야 한다."""
    state_file = tmp_path / "state_blocked.json"
    write_state(state_file, make_blocked_state())

    result = run_status(state_file)

    assert result.returncode == 0, (
        f"status returned non-zero exit code {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "차단" in result.stdout, (
        f"missing '차단' marker in stdout for blocked pipeline:\n{result.stdout}"
    )
    assert "파이프라인:" in result.stdout, f"missing '파이프라인:' in stdout:\n{result.stdout}"


def test_status_exits_zero_when_no_pipeline(tmp_path: Path) -> None:
    """state 파일이 없을 때(활성 파이프라인 없음) status는 안내 출력 후 exit 0이어야 한다."""
    # tmp_path 안에 존재하지 않는 경로를 지정 — _load()는 STATE_FILE.exists()로 None 반환.
    missing_state = tmp_path / "does_not_exist.json"
    assert not missing_state.exists(), "fixture precondition: state file must not exist"

    result = run_status(missing_state)

    assert result.returncode == 0, (
        f"status returned non-zero exit code {result.returncode} when no pipeline exists\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "활성 파이프라인 없음" in result.stdout, (
        f"missing '활성 파이프라인 없음' guidance in stdout:\n{result.stdout}"
    )


def test_status_exits_zero_when_terminal_failed(tmp_path: Path) -> None:
    """terminal_state=FAILED인 state에서도 status는 exit 0이어야 한다 (추가 회귀)."""
    state = make_complete_state()
    state["pipeline_id"] = "TEST-FAILED-5CF4"
    state["terminal_state"] = "FAILED"
    state["current_phase"] = "qa"
    state_file = tmp_path / "state_failed.json"
    write_state(state_file, state)

    result = run_status(state_file)

    assert result.returncode == 0, (
        f"status returned non-zero exit code {result.returncode} for FAILED state\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "terminal_state=FAILED" in result.stdout, (
        f"missing 'terminal_state=FAILED' marker in stdout:\n{result.stdout}"
    )


def test_status_exits_zero_when_terminal_terminated(tmp_path: Path) -> None:
    """terminal_state=TERMINATED인 state에서도 status는 exit 0이어야 한다 (추가 회귀)."""
    state = make_complete_state()
    state["pipeline_id"] = "TEST-TERMINATED-5CF4"
    state["terminal_state"] = "TERMINATED"
    state["current_phase"] = "TERMINATED"
    state_file = tmp_path / "state_terminated.json"
    write_state(state_file, state)

    result = run_status(state_file)

    assert result.returncode == 0, (
        f"status returned non-zero exit code {result.returncode} for TERMINATED state\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "terminal_state=TERMINATED" in result.stdout, (
        f"missing 'terminal_state=TERMINATED' marker in stdout:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# Self-Verification block (Self-Verification Protocol)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 정상 입력 — None 방어
    try:
        run_status(None, timeout=0)
        assert False, "ValueError 미발생"
    except ValueError:
        pass
    try:
        run_status(None, timeout="bad")  # type: ignore[arg-type]
        assert False, "TypeError 미발생"
    except TypeError:
        pass
    try:
        write_state(None, {})  # type: ignore[arg-type]
        assert False, "TypeError 미발생"
    except TypeError:
        pass
    print("[SELF-VERIFY] OK")
