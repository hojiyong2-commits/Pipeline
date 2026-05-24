"""IMP-20260523-9189: return_phase Re-entry Path E2E 테스트 (helper 기반).

TC-01(return_phase=dev), TC-02(return_phase=pm), TC-03(BLOCKED) 3케이스.

oracle 파일 경로: tests/oracles/IMP-20260523-9189/TC-0N/
oracle 파일 수정 금지.
기존 tests/oracles/IMP-20260523-577F/ 수정 금지.
"""
import json
import sys
from pathlib import Path

from tests.helpers.cli_evidence import (
    CliResult,
    assert_post_state,
    run_cli_with_temp_state,
)

ORACLE_DIR = Path("tests/oracles/IMP-20260523-9189")
PIPELINE_PY = Path("pipeline.py").resolve()


def _run_revert(initial_state: dict, extra_args: list | None = None) -> CliResult:
    """run_cli_with_temp_state를 사용하여 pipeline.py revert를 격리된 환경에서 실행한다.

    MT-1(cli_evidence.py)의 run_cli_with_temp_state를 통해
    PIPELINE_STATE_PATH 격리, post_state 반환, temp_dir 반환을 일괄 처리한다.
    """
    extra_args = extra_args or []
    command = [sys.executable, str(PIPELINE_PY), "revert"] + extra_args
    return run_cli_with_temp_state(initial_state, command)


class TestReturnPhaseReentry:
    """TC-01 ~ TC-03: revert 서브커맨드 E2E 검증 (helper 기반)."""

    def test_tc01_return_phase_dev_resets_dev_and_later(self) -> None:
        """TC-01 (normal): return_phase=dev failure_packet 시 dev 이후 phase PENDING, exit 0, 한국어 안내."""
        oracle_input = json.loads((ORACLE_DIR / "TC-01" / "input.json").read_text(encoding="utf-8"))
        oracle_expected = json.loads((ORACLE_DIR / "TC-01" / "expected.json").read_text(encoding="utf-8"))

        initial_state = oracle_input["initial_state"]
        failure_packets = initial_state.get("failure_packets", [])
        return_phase = failure_packets[0]["return_phase"] if failure_packets else "dev"

        result = _run_revert(initial_state, extra_args=["--to", return_phase])

        # exit code 검증
        assert result.returncode == oracle_expected["exit_code"], (
            f"TC-01: exit_code 불일치. 기대={oracle_expected['exit_code']}, 실제={result.returncode}\n출력:\n{result.output}"
        )
        # stdout 키워드 검증
        for keyword in oracle_expected["stdout_contains"]:
            assert keyword in result.output, (
                f"TC-01: stdout에 '{keyword}'가 없음.\n출력:\n{result.output}"
            )

        # assert_post_state로 oracle expected post_state 검증
        expected_post = oracle_expected.get("post_state", {})
        phases_spec = expected_post.get("phases", {})
        if phases_spec:
            assert_post_state(result, {"phases": phases_spec})

        # current_phase 검증 (oracle에 있는 경우)
        if "current_phase" in expected_post:
            assert_post_state(result, {"current_phase": expected_post["current_phase"]})

        # phase_attempt_history 검증
        history_contains = expected_post.get("phase_attempt_history_contains")
        if history_contains:
            history = result.final_state.get("phase_attempt_history", [])
            matched = any(
                all(entry.get(k) == v for k, v in history_contains.items())
                for entry in history
            )
            assert matched, (
                f"TC-01: phase_attempt_history에 {history_contains} 항목이 없음.\n실제={history}"
            )

    def test_tc02_return_phase_pm_resets_all_after_pm(self) -> None:
        """TC-02 (normal): return_phase=pm 시 pm 이후 모든 phase PENDING, exit 0."""
        oracle_input = json.loads((ORACLE_DIR / "TC-02" / "input.json").read_text(encoding="utf-8"))
        oracle_expected = json.loads((ORACLE_DIR / "TC-02" / "expected.json").read_text(encoding="utf-8"))

        initial_state = oracle_input["initial_state"]
        failure_packets = initial_state.get("failure_packets", [])
        return_phase = failure_packets[0]["return_phase"] if failure_packets else "pm"

        result = _run_revert(initial_state, extra_args=["--to", return_phase])

        assert result.returncode == oracle_expected["exit_code"], (
            f"TC-02: exit_code 불일치. 기대={oracle_expected['exit_code']}, 실제={result.returncode}\n출력:\n{result.output}"
        )
        for keyword in oracle_expected["stdout_contains"]:
            assert keyword in result.output, (
                f"TC-02: stdout에 '{keyword}'가 없음.\n출력:\n{result.output}"
            )

        # assert_post_state로 phases 검증
        expected_post = oracle_expected.get("post_state", {})
        phases_spec = expected_post.get("phases", {})
        if phases_spec:
            assert_post_state(result, {"phases": phases_spec})

        if "current_phase" in expected_post:
            assert_post_state(result, {"current_phase": expected_post["current_phase"]})

        # phase_attempt_history 최소 건수 검증
        min_count = expected_post.get("phase_attempt_history_min_count")
        if min_count is not None:
            history = result.final_state.get("phase_attempt_history", [])
            assert len(history) >= min_count, (
                f"TC-02: phase_attempt_history는 {min_count}개 이상이어야 함. 실제={len(history)}\nfinal_state={result.final_state}"
            )

    def test_tc03_revert_budget_exceeded_blocked(self) -> None:
        """TC-03 (error): REVERT_BUDGET 초과 시 BLOCKED + exit 1 + RCA/architect 안내."""
        oracle_input = json.loads((ORACLE_DIR / "TC-03" / "input.json").read_text(encoding="utf-8"))
        oracle_expected = json.loads((ORACLE_DIR / "TC-03" / "expected.json").read_text(encoding="utf-8"))

        initial_state = oracle_input["initial_state"]
        failure_packets = initial_state.get("failure_packets", [])
        return_phase = failure_packets[0]["return_phase"] if failure_packets else "dev"

        # phase_attempt_history에 이미 3회 기록 (TC-03 oracle에 있음)
        assert len(initial_state.get("phase_attempt_history", [])) >= 3, (
            "TC-03 oracle: phase_attempt_history에 3회 이상 이력이 있어야 함"
        )

        result = _run_revert(initial_state, extra_args=["--to", return_phase])

        assert result.returncode == oracle_expected["exit_code"], (
            f"TC-03: exit_code 불일치. 기대={oracle_expected['exit_code']}, 실제={result.returncode}\n출력:\n{result.output}"
        )
        for keyword in oracle_expected["stdout_contains"]:
            assert keyword in result.output, (
                f"TC-03: stdout에 '{keyword}'가 없음.\n출력:\n{result.output}"
            )

        # assert_post_state로 BLOCKED 상태 검증
        expected_post = oracle_expected.get("post_state", {})
        assert_post_state(result, {
            "blocked": expected_post["blocked"],
            "revert_blocked": expected_post["revert_blocked"],
        })

        # blocked_reason_contains 검증
        blocked_reason_contains = expected_post.get("blocked_reason_contains")
        if blocked_reason_contains:
            blocked_reason = result.final_state.get("blocked_reason", "")
            assert blocked_reason_contains in blocked_reason, (
                f"TC-03: blocked_reason에 '{blocked_reason_contains}'가 없음. 실제={blocked_reason!r}"
            )

        # event_log_contains 검증
        event_log_contains = expected_post.get("event_log_contains")
        if event_log_contains:
            event_log = result.final_state.get("event_log", [])
            log_text = " ".join(str(e) for e in event_log)
            assert event_log_contains in log_text, (
                f"TC-03: event_log에 '{event_log_contains}'가 없음. 실제 event_log={event_log}"
            )
