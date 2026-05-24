"""tests/test_wait_github_ci.py

IMP-20260524-C097 MT-3: gates wait-github-ci CLI Evidence Contract 준수 테스트.

CLI Evidence Contract 규칙 (BUG-20260524-897B):
  - PIPELINE_STATE_PATH 환경변수로 state 격리 필수
  - final_state / event_log 검증 필수
  - stdout-only 테스트 금지
  - oracle TC01/TC02: tests/oracles/IMP-20260524-C097/ 기반
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any, Dict
from unittest.mock import patch

# pipeline.py를 import 하기 위해 프로젝트 루트를 sys.path에 추가
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pipeline  # noqa: E402


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _load_oracle(case_id: str, kind: str) -> Dict[str, Any]:
    """tests/oracles/IMP-20260524-C097/<case_id>/<kind>.json 로드."""
    oracle_path = (
        _PROJECT_ROOT
        / "tests"
        / "oracles"
        / "IMP-20260524-C097"
        / case_id
        / f"{kind}.json"
    )
    return json.loads(oracle_path.read_bytes())


def _make_minimal_state(pipeline_id: str = "IMP-20260524-C097") -> Dict[str, Any]:
    """테스트용 최소 pipeline_state 구조 반환.

    _log_event가 state["event_log"]를 직접 접근하므로 event_log 필드를 포함해야 한다.
    """
    return {
        "pipeline_id": pipeline_id,
        "current_phase": "build",
        "phase_history": [],
        "events": [],
        "event_log": [],
        "external_gates": {},
        "agent_runs": {},
        "failure_packets": [],
    }


def _run_cli(argv: list, env: Dict[str, str]) -> Dict[str, Any]:
    """pipeline.py CLI를 in-process로 실행하고 exit_code를 반환.

    PIPELINE_STATE_PATH 환경변수가 주입되므로 state 격리가 보장된다.
    stdout-only 검증 금지 원칙: exit_code와 final_state를 함께 반환한다.

    STATE_FILE은 모듈 임포트 시점에 고정되므로 직접 패치한다.
    """
    old_environ = dict(os.environ)
    old_argv = sys.argv[:]
    exit_code = 0

    # PIPELINE_STATE_PATH 환경변수에서 state 파일 경로 추출
    state_path_str = env.get("PIPELINE_STATE_PATH")
    state_path_obj = pathlib.Path(state_path_str) if state_path_str else None

    try:
        os.environ.update(env)
        sys.argv = ["pipeline.py"] + argv
        # STATE_FILE을 직접 패치하여 격리 보장
        with patch.object(pipeline, "STATE_FILE", state_path_obj or pipeline.STATE_FILE):
            try:
                pipeline.main()  # type: ignore[attr-defined]
            except SystemExit as exc:
                exit_code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        os.environ.clear()
        os.environ.update(old_environ)
        sys.argv = old_argv

    return {"exit_code": exit_code}


# ---------------------------------------------------------------------------
# TC01 — SHA 일치 + success: exit 0, state 기록 확인
# ---------------------------------------------------------------------------

class TestTC01ShaMatchSuccess:
    """TC01: SHA 일치 + conclusion=success -> PASS, state[github_ci_wait] 기록."""

    def test_state_github_ci_wait_recorded(self, tmp_path: pathlib.Path) -> None:
        """CLI Evidence Contract: final_state에 github_ci_wait 블록이 기록되어야 한다."""
        inp = _load_oracle("TC01", "input")
        expected = _load_oracle("TC01", "expected")

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(
            json.dumps(_make_minimal_state(), ensure_ascii=False), encoding="utf-8"
        )

        mock_poll_result = {
            "wait_status": expected["wait_status"],
            "matched_head_sha": expected["matched_head_sha"],
            "conclusion": expected["conclusion"],
            "run_id": "123456",
            "elapsed_sec": 15.0,
        }

        env = {**os.environ, "PIPELINE_STATE_PATH": str(state_file)}

        with patch.object(pipeline, "_poll_github_ci_run", return_value=mock_poll_result):
            with patch.object(pipeline, "_github_repo_from_remote", return_value="owner/repo"):
                result = _run_cli(
                    [
                        "gates",
                        "wait-github-ci",
                        "--repo",
                        "owner/repo",
                        "--head-sha",
                        inp["expected_headSha"],
                    ],
                    env=env,
                )

        assert result["exit_code"] == 0, (
            f"exit_code 기대 0, 실제 {result['exit_code']}"
        )

        final_state = json.loads(state_file.read_text(encoding="utf-8"))
        assert "github_ci_wait" in final_state, (
            "state에 github_ci_wait 블록이 없습니다 (CLI Evidence Contract 위반)"
        )
        recorded = final_state["github_ci_wait"]
        assert recorded.get("wait_status") == expected["wait_status"], (
            f"wait_status 불일치: {recorded.get('wait_status')} != {expected['wait_status']}"
        )
        assert recorded.get("matched_head_sha") == expected["matched_head_sha"], (
            "matched_head_sha 불일치"
        )

    def test_event_log_contains_wait_entry(self, tmp_path: pathlib.Path) -> None:
        """CLI Evidence Contract: event_log에 gates wait-github-ci 이벤트가 기록되어야 한다."""
        inp = _load_oracle("TC01", "input")
        expected = _load_oracle("TC01", "expected")

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(
            json.dumps(_make_minimal_state(), ensure_ascii=False), encoding="utf-8"
        )

        mock_poll_result = {
            "wait_status": expected["wait_status"],
            "matched_head_sha": expected["matched_head_sha"],
            "conclusion": expected["conclusion"],
            "run_id": "123456",
            "elapsed_sec": 15.0,
        }

        env = {**os.environ, "PIPELINE_STATE_PATH": str(state_file)}

        with patch.object(pipeline, "_poll_github_ci_run", return_value=mock_poll_result):
            with patch.object(pipeline, "_github_repo_from_remote", return_value="owner/repo"):
                _run_cli(
                    [
                        "gates",
                        "wait-github-ci",
                        "--repo",
                        "owner/repo",
                        "--head-sha",
                        inp["expected_headSha"],
                    ],
                    env=env,
                )

        final_state = json.loads(state_file.read_text(encoding="utf-8"))
        # pipeline.py _log_event는 event_log 키를 사용 (msg 필드)
        event_log = final_state.get("event_log", [])
        wait_events = [e for e in event_log if "wait-github-ci" in str(e.get("msg", ""))]
        assert wait_events, (
            "event_log에 wait-github-ci 이벤트가 없습니다 (CLI Evidence Contract 위반)"
        )


# ---------------------------------------------------------------------------
# TC02 — SHA 불일치: exit 1, WAITING_FOR_TRIGGER
# ---------------------------------------------------------------------------

class TestTC02ShaMismatch:
    """TC02: SHA 불일치 -> WAITING_FOR_TRIGGER, exit 1."""

    def test_exit_code_1_on_sha_mismatch(self, tmp_path: pathlib.Path) -> None:
        """SHA 불일치 시 exit 1 반환."""
        inp = _load_oracle("TC02", "input")
        expected = _load_oracle("TC02", "expected")

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(
            json.dumps(_make_minimal_state(), ensure_ascii=False), encoding="utf-8"
        )

        mock_poll_result = {
            "wait_status": expected["wait_status"],
            "matched_head_sha": expected["matched_head_sha"],
            "conclusion": None,
            "run_id": None,
            "elapsed_sec": 5.0,
        }

        env = {**os.environ, "PIPELINE_STATE_PATH": str(state_file)}

        with patch.object(pipeline, "_poll_github_ci_run", return_value=mock_poll_result):
            with patch.object(pipeline, "_github_repo_from_remote", return_value="owner/repo"):
                result = _run_cli(
                    [
                        "gates",
                        "wait-github-ci",
                        "--repo",
                        "owner/repo",
                        "--head-sha",
                        inp["expected_headSha"],
                    ],
                    env=env,
                )

        assert result["exit_code"] == 1, (
            f"exit_code 기대 1, 실제 {result['exit_code']}"
        )

    def test_state_records_waiting_for_trigger(self, tmp_path: pathlib.Path) -> None:
        """SHA 불일치 시 state에 WAITING_FOR_TRIGGER 기록."""
        inp = _load_oracle("TC02", "input")
        expected = _load_oracle("TC02", "expected")

        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(
            json.dumps(_make_minimal_state(), ensure_ascii=False), encoding="utf-8"
        )

        mock_poll_result = {
            "wait_status": expected["wait_status"],
            "matched_head_sha": expected["matched_head_sha"],
            "conclusion": None,
            "run_id": None,
            "elapsed_sec": 5.0,
        }

        env = {**os.environ, "PIPELINE_STATE_PATH": str(state_file)}

        with patch.object(pipeline, "_poll_github_ci_run", return_value=mock_poll_result):
            with patch.object(pipeline, "_github_repo_from_remote", return_value="owner/repo"):
                _run_cli(
                    [
                        "gates",
                        "wait-github-ci",
                        "--repo",
                        "owner/repo",
                        "--head-sha",
                        inp["expected_headSha"],
                    ],
                    env=env,
                )

        final_state = json.loads(state_file.read_text(encoding="utf-8"))
        assert "github_ci_wait" in final_state, "state에 github_ci_wait 없음"
        recorded = final_state["github_ci_wait"]
        assert recorded.get("wait_status") == expected["wait_status"], (
            f"wait_status 기대 {expected['wait_status']}, 실제 {recorded.get('wait_status')}"
        )


# ---------------------------------------------------------------------------
# TC03 — timeout: exit 1, TIMEOUT
# ---------------------------------------------------------------------------

class TestTC03Timeout:
    """TC03: 타임아웃 -> TIMEOUT, exit 1."""

    def test_timeout_exit_code_1(self, tmp_path: pathlib.Path) -> None:
        """타임아웃 시 exit 1."""
        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(
            json.dumps(_make_minimal_state(), ensure_ascii=False), encoding="utf-8"
        )

        mock_poll_result = {
            "wait_status": "TIMEOUT",
            "matched_head_sha": False,
            "conclusion": None,
            "run_id": None,
            "elapsed_sec": 600.0,
        }

        env = {**os.environ, "PIPELINE_STATE_PATH": str(state_file)}

        with patch.object(pipeline, "_poll_github_ci_run", return_value=mock_poll_result):
            with patch.object(pipeline, "_github_repo_from_remote", return_value="owner/repo"):
                result = _run_cli(
                    ["gates", "wait-github-ci", "--repo", "owner/repo", "--head-sha", "abc123"],
                    env=env,
                )

        assert result["exit_code"] == 1

    def test_timeout_state_recorded(self, tmp_path: pathlib.Path) -> None:
        """타임아웃 시 state에 TIMEOUT 기록."""
        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(
            json.dumps(_make_minimal_state(), ensure_ascii=False), encoding="utf-8"
        )

        mock_poll_result = {
            "wait_status": "TIMEOUT",
            "matched_head_sha": False,
            "conclusion": None,
            "run_id": None,
            "elapsed_sec": 600.0,
        }

        env = {**os.environ, "PIPELINE_STATE_PATH": str(state_file)}

        with patch.object(pipeline, "_poll_github_ci_run", return_value=mock_poll_result):
            with patch.object(pipeline, "_github_repo_from_remote", return_value="owner/repo"):
                _run_cli(
                    ["gates", "wait-github-ci", "--repo", "owner/repo", "--head-sha", "abc123"],
                    env=env,
                )

        final_state = json.loads(state_file.read_text(encoding="utf-8"))
        recorded = final_state.get("github_ci_wait", {})
        assert recorded.get("wait_status") == "TIMEOUT"


# ---------------------------------------------------------------------------
# TC04 — cancelled: exit 1, CANCELLED
# ---------------------------------------------------------------------------

class TestTC04Cancelled:
    """TC04: CI run 취소 -> CANCELLED, exit 1."""

    def test_cancelled_exit_code_1(self, tmp_path: pathlib.Path) -> None:
        """취소된 run -> exit 1."""
        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(
            json.dumps(_make_minimal_state(), ensure_ascii=False), encoding="utf-8"
        )

        mock_poll_result = {
            "wait_status": "CANCELLED",
            "matched_head_sha": True,
            "conclusion": "cancelled",
            "run_id": "99999",
            "elapsed_sec": 8.0,
        }

        env = {**os.environ, "PIPELINE_STATE_PATH": str(state_file)}

        with patch.object(pipeline, "_poll_github_ci_run", return_value=mock_poll_result):
            with patch.object(pipeline, "_github_repo_from_remote", return_value="owner/repo"):
                result = _run_cli(
                    ["gates", "wait-github-ci", "--repo", "owner/repo", "--head-sha", "abc123"],
                    env=env,
                )

        assert result["exit_code"] == 1

    def test_cancelled_state_recorded(self, tmp_path: pathlib.Path) -> None:
        """취소 시 state에 CANCELLED 기록."""
        state_file = tmp_path / "pipeline_state.json"
        state_file.write_text(
            json.dumps(_make_minimal_state(), ensure_ascii=False), encoding="utf-8"
        )

        mock_poll_result = {
            "wait_status": "CANCELLED",
            "matched_head_sha": True,
            "conclusion": "cancelled",
            "run_id": "99999",
            "elapsed_sec": 8.0,
        }

        env = {**os.environ, "PIPELINE_STATE_PATH": str(state_file)}

        with patch.object(pipeline, "_poll_github_ci_run", return_value=mock_poll_result):
            with patch.object(pipeline, "_github_repo_from_remote", return_value="owner/repo"):
                _run_cli(
                    ["gates", "wait-github-ci", "--repo", "owner/repo", "--head-sha", "abc123"],
                    env=env,
                )

        final_state = json.loads(state_file.read_text(encoding="utf-8"))
        recorded = final_state.get("github_ci_wait", {})
        assert recorded.get("wait_status") == "CANCELLED"
