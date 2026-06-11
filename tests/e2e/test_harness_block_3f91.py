"""
test_harness_block_3f91.py — IMP-20260611-3F91 MT-2: Harness Three-Gate Block E2E Tests

Purpose: pipeline.py harness 명령이 Three-Gate 차단 메시지를 반환하는지
         subprocess 기반 CLI 호출로 검증.
         PIPELINE_STATE_PATH 격리 + final_state assertion 포함.

CLI Evidence Contract (IMP-20260525-6FAC):
- 상태 변경 CLI 호출: PIPELINE_STATE_PATH 격리 + final_state assertion 필수
- stdout-only 검증 금지

설계 결정:
  cmd_harness는 _load_branch_state()로 상태를 먼저 로드한 뒤 _die([THREE GATE BLOCKED])를
  실행한다. 따라서 유효한 파이프라인 state가 있어야 THREE GATE BLOCKED 경로에 도달한다.
  각 테스트는 tmp_path에 격리된 state 파일을 생성하기 위해 먼저
  `pipeline.py new --type IMP --desc ...`를 실행한 뒤 harness를 검증한다.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"


def run_cli(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """pipeline.py CLI를 subprocess로 실행."""
    cmd = [sys.executable, str(PIPELINE_PY)] + args
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=merged_env,
    )


def read_state(state_path: Path) -> Dict[str, Any]:
    """격리된 state 파일 읽기."""
    if not state_path.exists():
        return {}
    with open(state_path, encoding="utf-8") as f:
        return json.load(f)


def init_isolated_state(tmp_path: Path) -> Dict[str, str]:
    """tmp_path에 격리된 파이프라인 state를 생성하고 env를 반환.

    cmd_harness는 _load_branch_state() 호출 후 _die([THREE GATE BLOCKED])를 실행하므로
    유효한 state가 없으면 '활성 파이프라인 없음' 오류가 먼저 발생한다.
    이를 방지하기 위해 pipeline.py new로 최소 state를 생성한다.

    CLI Evidence Contract (IMP-20260525-6FAC):
    PIPELINE_STATE_PATH 격리 + final_state assertion 포함.
    """
    state_file = tmp_path / "pipeline_state.json"
    env = {
        "PIPELINE_STATE_PATH": str(state_file),
        "PYTHONIOENCODING": "utf-8",
    }
    result = run_cli(
        ["new", "--type", "IMP", "--desc", "test-harness-block-isolation"],
        env=env,
    )
    assert result.returncode == 0, (
        f"격리 state 초기화 실패(pipeline.py new).\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert state_file.exists(), "pipeline.py new 후 state 파일이 생성되지 않았습니다."

    # final_state assertion: new 이후 current_phase가 'pm'이어야 함
    final_state = read_state(state_file)
    assert final_state.get("current_phase") == "pm", (
        f"pipeline.py new 후 예상 상태(pm)가 아닙니다: {final_state.get('current_phase')}"
    )
    assert final_state.get("pipeline_id"), "pipeline.py new 후 pipeline_id가 없습니다."
    return env


class TestHarnessThreeGateBlock:
    """AC-1, AC-2: harness 명령이 항상 THREE GATE BLOCKED + exit 1 반환."""

    def test_harness_no_args_blocked(self, tmp_path: Path) -> None:
        """AC-1: python pipeline.py harness 단독 실행 시 THREE GATE BLOCKED + non-zero exit.

        argparse 오류(exit 2, 'error: the following arguments are required')가 아닌
        [THREE GATE BLOCKED] 메시지 + exit 1이어야 함.
        """
        env = init_isolated_state(tmp_path)
        state_file = Path(env["PIPELINE_STATE_PATH"])

        result = run_cli(["harness"], env=env)

        # exit code 검증: non-zero (1)이어야 함. argparse 오류는 exit 2
        assert result.returncode != 0, (
            f"exit code가 0입니다. stdout: {result.stdout!r} stderr: {result.stderr!r}"
        )
        assert result.returncode != 2, (
            f"argparse 오류(exit 2)가 발생했습니다 — --score/--verdict가 여전히 required=True입니다.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )

        # stdout에 THREE GATE BLOCKED 포함 여부
        combined = result.stdout + result.stderr
        assert "THREE GATE BLOCKED" in combined, (
            f"[THREE GATE BLOCKED] 메시지가 없습니다.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )

        # final_state assertion: harness 명령은 state를 완료 상태로 바꾸지 않음
        # (_die()로 즉시 종료하므로 harness phase가 DONE으로 기록되지 않아야 함)
        state = read_state(state_file)
        phases = state.get("phases", {})
        assert "harness" not in phases or phases.get("harness", {}).get("status") != "DONE", (
            "harness phase가 DONE으로 기록되었습니다 — THREE GATE BLOCKED가 우회되었습니다."
        )

    def test_harness_with_score_arg_blocked(self, tmp_path: Path) -> None:
        """AC-2: python pipeline.py harness --score 100 실행 시
        argparse 오류가 아닌 [THREE GATE BLOCKED] + exit 1 반환.
        """
        env = init_isolated_state(tmp_path)
        state_file = Path(env["PIPELINE_STATE_PATH"])

        result = run_cli(["harness", "--score", "100"], env=env)

        # argparse 오류(exit 2) 아님 확인
        assert result.returncode != 2, (
            f"argparse 오류(exit 2)가 발생했습니다.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )

        # non-zero exit 확인
        assert result.returncode != 0, (
            f"exit code가 0입니다 — harness가 성공으로 처리되었습니다.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )

        # THREE GATE BLOCKED 메시지 확인
        combined = result.stdout + result.stderr
        assert "THREE GATE BLOCKED" in combined, (
            f"[THREE GATE BLOCKED] 메시지가 없습니다.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )

        # final_state assertion
        state = read_state(state_file)
        phases = state.get("phases", {})
        assert "harness" not in phases or phases.get("harness", {}).get("status") != "DONE", (
            "harness --score 100 호출 후 harness phase가 DONE으로 기록되었습니다."
        )
