"""IMP-20260523-577F: return_phase Re-entry Path E2E 테스트.

MT-4: TC-01(return_phase=dev), TC-02(return_phase=pm), TC-03(BLOCKED) 3케이스.

oracle 파일 경로: tests/oracles/IMP-20260523-577F/TC-0N/
oracle 파일 수정 금지.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ORACLE_DIR = Path("tests/oracles/IMP-20260523-577F")
PIPELINE_PY = Path("pipeline.py")


def _run_revert(initial_state: dict, extra_args: list | None = None) -> tuple[int, str, dict]:
    """임시 디렉터리에서 pipeline.py revert를 실행하고 (returncode, stdout+stderr, post_state)를 반환한다.

    PIPELINE_STATE_PATH 환경변수를 사용하여 임시 state 파일을 pipeline.py가 읽도록 한다.
    BASE_DIR가 pipeline.py 위치를 가리키므로 cwd 변경만으로는 state 파일 경로가 바뀌지 않는다.
    post_state는 subprocess 완료 후 state_file을 다시 읽어 반환한다.
    """
    import os
    extra_args = extra_args or []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # pipeline_state.json 설치
        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(json.dumps(initial_state, ensure_ascii=False, indent=2), encoding="utf-8")
        env = os.environ.copy()
        env["PIPELINE_STATE_PATH"] = str(state_file)
        result = subprocess.run(
            [sys.executable, str(PIPELINE_PY.resolve()), "revert"] + extra_args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        after_state: dict = {}
        if state_file.exists():
            after_state = json.loads(state_file.read_text(encoding="utf-8"))
        return result.returncode, result.stdout + result.stderr, after_state


class TestReturnPhaseReentry:
    """TC-01 ~ TC-03: revert 서브커맨드 E2E 검증."""

    def test_tc01_return_phase_dev_resets_dev_and_later(self) -> None:
        """TC-01 (normal): return_phase=dev failure_packet 시 dev 이후 phase PENDING, exit 0, 한국어 안내."""
        oracle_input = json.loads((ORACLE_DIR / "TC-01" / "input.json").read_text(encoding="utf-8"))
        oracle_expected = json.loads((ORACLE_DIR / "TC-01" / "expected.json").read_text(encoding="utf-8"))

        initial_state = oracle_input["initial_state"]
        # failure_packet.json을 임시 디렉터리에 배치하기 위해 --to 오버라이드 사용
        failure_packets = initial_state.get("failure_packets", [])
        return_phase = failure_packets[0]["return_phase"] if failure_packets else "dev"

        # --to 오버라이드로 failure_packet 없이도 동일 동작 검증
        returncode, output, post_state = _run_revert(initial_state, extra_args=["--to", return_phase])

        assert returncode == oracle_expected["exit_code"], (
            f"TC-01: exit_code 불일치. 기대={oracle_expected['exit_code']}, 실제={returncode}\n출력:\n{output}"
        )
        for keyword in oracle_expected["stdout_contains"]:
            assert keyword in output, (
                f"TC-01: stdout에 '{keyword}'가 없음.\n출력:\n{output}"
            )

        # post_state 검증: pm=DONE, dev/qa/sec/build/harness/architect=PENDING
        phases_after = post_state.get("phases", {})
        assert phases_after.get("pm", {}).get("status") == "DONE", (
            f"TC-01: pm phase는 DONE이어야 함. 실제={phases_after.get('pm', {}).get('status')}\npost_state={post_state}"
        )
        for phase in ["dev", "qa", "sec", "build", "harness", "architect"]:
            assert phases_after.get(phase, {}).get("status") == "PENDING", (
                f"TC-01: {phase} phase는 PENDING이어야 함. 실제={phases_after.get(phase, {}).get('status')}\npost_state={post_state}"
            )

    def test_tc02_return_phase_pm_resets_all_after_pm(self) -> None:
        """TC-02 (normal): return_phase=pm 시 pm 이후 모든 phase PENDING, exit 0."""
        oracle_input = json.loads((ORACLE_DIR / "TC-02" / "input.json").read_text(encoding="utf-8"))

        initial_state = oracle_input["initial_state"]
        failure_packets = initial_state.get("failure_packets", [])
        return_phase = failure_packets[0]["return_phase"] if failure_packets else "pm"

        returncode, output, post_state = _run_revert(initial_state, extra_args=["--to", return_phase])

        assert returncode == 0, (
            f"TC-02: exit_code 불일치. 기대=0, 실제={returncode}\n출력:\n{output}"
        )
        for keyword in ("pm", "되돌아", "PENDING"):
            assert keyword in output, (
                f"TC-02: stdout에 '{keyword}'가 없음.\n출력:\n{output}"
            )

        # post_state 검증: pm 포함 모든 7개 phase PENDING, phase_attempt_history 1개 이상
        phases_after = post_state.get("phases", {})
        for phase in ["pm", "dev", "qa", "sec", "build", "harness", "architect"]:
            assert phases_after.get(phase, {}).get("status") == "PENDING", (
                f"TC-02: {phase} phase는 PENDING이어야 함. 실제={phases_after.get(phase, {}).get('status')}\npost_state={post_state}"
            )
        attempt_history = post_state.get("phase_attempt_history", [])
        assert len(attempt_history) >= 1, (
            f"TC-02: phase_attempt_history는 1개 이상이어야 함. 실제={len(attempt_history)}\npost_state={post_state}"
        )

    def test_tc03_revert_budget_exceeded_blocked(self) -> None:
        """TC-03 (edge): REVERT_BUDGET 초과 시 BLOCKED + exit 1 + RCA/architect 안내."""
        oracle_input = json.loads((ORACLE_DIR / "TC-03" / "input.json").read_text(encoding="utf-8"))

        initial_state = oracle_input["initial_state"]
        failure_packets = initial_state.get("failure_packets", [])
        return_phase = failure_packets[0]["return_phase"] if failure_packets else "dev"

        # phase_attempt_history에 이미 3회 기록 (TC-03 oracle에 있음)
        assert len(initial_state.get("phase_attempt_history", [])) >= 3, (
            "TC-03 oracle: phase_attempt_history에 3회 이상 이력이 있어야 함"
        )

        returncode, output, post_state = _run_revert(initial_state, extra_args=["--to", return_phase])

        assert returncode == 1, (
            f"TC-03: exit_code 불일치. 기대=1 (BLOCKED), 실제={returncode}\n출력:\n{output}"
        )
        for keyword in ("BLOCKED", "RCA", "architect"):
            assert keyword in output, (
                f"TC-03: stdout에 '{keyword}'가 없음.\n출력:\n{output}"
            )

        # post_state 검증: blocked=True, revert_blocked=True, blocked_reason에 "동일한 실패가 반복" 포함
        assert post_state.get("blocked") is True, (
            f"TC-03: state['blocked']은 True여야 함. 실제={post_state.get('blocked')}\npost_state={post_state}"
        )
        assert post_state.get("revert_blocked") is True, (
            f"TC-03: state['revert_blocked']은 True여야 함. 실제={post_state.get('revert_blocked')}\npost_state={post_state}"
        )
        blocked_reason = post_state.get("blocked_reason", "")
        assert "동일한 실패가 반복" in blocked_reason, (
            f"TC-03: blocked_reason에 '동일한 실패가 반복'이 없음. 실제={blocked_reason!r}\npost_state={post_state}"
        )
