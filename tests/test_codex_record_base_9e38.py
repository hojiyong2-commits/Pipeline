"""tests/test_codex_record_base_9e38.py

IMP-20260606-9E38 MT-1: codex-record --base 인수 추가 E2E 테스트

IMP-20260525-6FAC 요건:
  - subprocess 기반 실제 CLI 실행 (내부 함수 직접 import 금지)
  - PIPELINE_STATE_PATH 격리 (전역 pipeline_state.json 오염 방지)
  - final_state assertion 포함 (stdout-only 검증 금지)

테스트 목록:
  1. test_base_auto_diff_sha256       — --base main 사용 시 diff_sha256 자동 계산 기록
  2. test_reviewed_files_auto         — reviewed_files 자동 채움 확인
  3. test_manual_sha_backward_compat  — --diff-sha256 수동 방식 동작 유지 (하위 호환)
  4. test_invalid_base_error          — 잘못된 base ref 시 한국어 오류 메시지 + exit code 1

Oracle 파일: tests/oracles/IMP-20260606-9E38/
  - case_base_auto/  (normal)
  - case_manual_sha/ (edge)
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 경로 설정
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_PY = PROJECT_ROOT / "pipeline.py"
ORACLE_DIR = PROJECT_ROOT / "tests" / "oracles" / "IMP-20260606-9E38"

# ---------------------------------------------------------------------------
# 헬퍼 함수
# ---------------------------------------------------------------------------


def run_cli(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """python pipeline.py <args> 실제 CLI 실행.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess 환경 변수. None이면 현재 환경을 그대로 사용.
        timeout: 초 단위 타임아웃.

    Returns:
        subprocess.CompletedProcess 인스턴스.
    """
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    cmd = [sys.executable, str(PIPELINE_PY)] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def make_env(state_file: Path, output_file: Optional[Path] = None) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 환경 변수 딕셔너리 반환.

    Args:
        state_file: 격리된 임시 state 파일 경로.
        output_file: codex_review_result.json 출력 경로 (선택).

    Returns:
        subprocess에 전달할 환경 변수 딕셔너리.
    """
    if state_file is None:
        raise TypeError("state_file must not be None")
    env = {
        **os.environ,
        "PIPELINE_STATE_PATH": str(state_file),
        "PIPELINE_NO_DASHBOARD": "1",
    }
    return env


def write_state(state_file: Path, state: Dict[str, Any]) -> None:
    """state dict를 JSON으로 직렬화하여 state 파일에 기록.

    Args:
        state_file: 기록할 파일 경로.
        state: 기록할 state 딕셔너리.
    """
    if state_file is None:
        raise TypeError("state_file must not be None")
    if not isinstance(state, dict):
        raise TypeError(f"state must be dict, got {type(state).__name__}")
    state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_state(state_file: Path) -> Dict[str, Any]:
    """state 파일을 JSON으로 파싱하여 dict 반환.

    Args:
        state_file: 읽을 파일 경로.

    Returns:
        파싱된 state 딕셔너리.

    Raises:
        FileNotFoundError: state 파일이 없을 때.
    """
    if state_file is None:
        raise TypeError("state_file must not be None")
    if not state_file.exists():
        raise FileNotFoundError(f"state file not found: {state_file}")
    return json.loads(state_file.read_text(encoding="utf-8"))


def load_oracle(case_name: str) -> Dict[str, Any]:
    """oracle 케이스 input/expected 로드.

    Args:
        case_name: oracle 케이스 디렉토리명 (예: 'case_base_auto').

    Returns:
        {'input': ..., 'expected': ...} 딕셔너리.
    """
    case_dir = ORACLE_DIR / case_name
    with open(case_dir / "input.json", encoding="utf-8") as f:
        inp = json.load(f)
    with open(case_dir / "expected.json", encoding="utf-8") as f:
        exp = json.load(f)
    return {"input": inp, "expected": exp}


def _min_pipeline_state(pipeline_id: str = "IMP-20260606-9E38") -> Dict[str, Any]:
    """codex-record CLI 실행을 위한 최소 유효 state 딕셔너리.

    Args:
        pipeline_id: 파이프라인 ID.

    Returns:
        최소 state 딕셔너리.
    """
    return {
        "version": "1.2.0",
        "pipeline_id": pipeline_id,
        "type": "IMP",
        "description": "E2E 테스트용",
        "created_at": "2026-06-06T00:00:00Z",
        "updated_at": "2026-06-06T00:00:00Z",
        "pipeline_started_at": "2026-06-06T00:00:00Z",
        "pipeline_completed_at": None,
        "terminal_state": None,
        "current_phase": "dev",
        "blocked": False,
        "blocked_reason": None,
        "phases": {
            "pm": {"status": "DONE"},
            "dev": {"status": "DONE"},
            "qa": {"status": "PENDING"},
            "sec": {"status": "PENDING"},
            "build": {"status": "PENDING"},
            "harness": {"status": "PENDING"},
            "architect": {"status": "PENDING"},
        },
        "event_log": [],
        "agent_runs": {},
        "codex_review_gates": {},
        "external_gates": {
            "enabled": True,
            "mode": "three_gate",
        },
        "phase_attestations": {
            "enabled": True,
            "mode": "github_actions_per_phase",
            "required_phases": ["pm", "dev", "qa", "build"],
            "phases": {
                "pm": {"status": "PASS"},
                "dev": {"status": "PENDING"},
                "qa": {"status": "PENDING"},
                "build": {"status": "PENDING"},
            },
        },
    }


# ---------------------------------------------------------------------------
# 테스트 1: --base main 사용 시 diff_sha256 자동 계산 기록 (normal oracle)
# ---------------------------------------------------------------------------

class TestBaseAutoDiffSha256:
    """AC-1: --base main 사용 시 diff_sha256이 자동으로 계산되어 기록된다."""

    def test_base_auto_diff_sha256(self, tmp_path: Path) -> None:
        """--base main 실행 시 codex_review_result.json에 diff_sha256과 base_ref가 기록된다.

        PIPELINE_STATE_PATH 격리 + 실제 CLI subprocess 실행.
        final_state = codex_review_result.json 파일 내용을 검사한다.
        """
        oracle = load_oracle("case_base_auto")
        assert oracle["expected"]["base_ref"] == "main", "oracle: base_ref는 'main'이어야 함"
        assert oracle["expected"]["diff_sha256_auto"] is True, "oracle: diff_sha256_auto=true"

        state_file = tmp_path / "pipeline_state.json"
        output_file = tmp_path / "codex_review_result_auto.json"
        write_state(state_file, _min_pipeline_state())

        env = make_env(state_file)
        result = run_cli(
            [
                "review", "codex-record",
                "--stage", "code",
                "--result", "ACCEPT",
                "--review-model", "GPT-5.5",
                "--base", "main",
                "--head-sha", "EXAMPLE_DUMMY_AAAA",
                "--reviewer", "test-e2e",
                "--output", str(output_file),
            ],
            env=env,
        )

        # CLI가 exit code 0으로 성공해야 함
        assert result.returncode == 0, (
            f"exit code {result.returncode}: stdout={result.stdout[:300]} stderr={result.stderr[:300]}"
        )

        # final_state assertion: output 파일이 생성되어야 함
        assert output_file.exists(), "codex_review_result.json 파일이 생성되어야 함"
        final_state: Dict[str, Any] = json.loads(output_file.read_text(encoding="utf-8"))

        # base_ref 필드가 기록되어야 함
        assert final_state.get("base_ref") == "main", (
            f"base_ref가 'main'으로 기록되어야 함. 실제: {final_state.get('base_ref')}"
        )

        # diff_sha256이 비어있지 않아야 함 (자동 계산)
        diff_sha256 = final_state.get("diff_sha256", "")
        assert diff_sha256, f"diff_sha256이 비어있지 않아야 함. 실제: '{diff_sha256}'"

        # stage, result, review_model 필드 확인
        assert final_state.get("stage") == "code"
        assert final_state.get("result") == "ACCEPT"
        assert final_state.get("review_model") == "GPT-5.5"


# ---------------------------------------------------------------------------
# 테스트 2: reviewed_files 자동 채움 확인 (normal oracle)
# ---------------------------------------------------------------------------

class TestReviewedFilesAuto:
    """AC-4: --base 사용 시 reviewed_files가 git diff --name-only 기준으로 자동 채워진다."""

    def test_reviewed_files_auto(self, tmp_path: Path) -> None:
        """--base main 실행 시 reviewed_files 필드가 자동으로 채워진다.

        reviewed_files는 리스트 타입이어야 하며 None이 아니어야 한다.
        """
        oracle = load_oracle("case_base_auto")
        assert oracle["expected"]["reviewed_files_auto"] is True, "oracle: reviewed_files_auto=true"

        state_file = tmp_path / "pipeline_state.json"
        output_file = tmp_path / "codex_review_result_rf.json"
        write_state(state_file, _min_pipeline_state())

        env = make_env(state_file)
        result = run_cli(
            [
                "review", "codex-record",
                "--stage", "code",
                "--result", "ACCEPT",
                "--review-model", "GPT-5.5",
                "--base", "main",
                "--head-sha", "EXAMPLE_DUMMY_BBBB",
                "--reviewer", "test-rf",
                "--output", str(output_file),
            ],
            env=env,
        )

        assert result.returncode == 0, (
            f"exit code {result.returncode}: stdout={result.stdout[:300]} stderr={result.stderr[:300]}"
        )

        assert output_file.exists(), "output 파일이 생성되어야 함"
        final_state = json.loads(output_file.read_text(encoding="utf-8"))

        reviewed_files = final_state.get("reviewed_files")
        assert reviewed_files is not None, "reviewed_files 필드가 존재해야 함"
        assert isinstance(reviewed_files, list), (
            f"reviewed_files는 리스트여야 함. 실제 타입: {type(reviewed_files).__name__}"
        )


# ---------------------------------------------------------------------------
# 테스트 3: --diff-sha256 수동 방식 동작 유지 — 하위 호환 (edge oracle)
# ---------------------------------------------------------------------------

class TestManualShaBackwardCompat:
    """AC-2: --diff-sha256 수동 방식이 --base 없이도 계속 동작한다."""

    def test_manual_sha_backward_compat(self, tmp_path: Path) -> None:
        """--diff-sha256 수동 입력 시 해당 SHA가 그대로 기록된다.

        --base 없이 기존 방식으로 실행했을 때 하위 호환이 유지됨을 검증.
        """
        oracle = load_oracle("case_manual_sha")
        assert oracle["expected"]["manual_sha_preserved"] is True, "oracle: manual_sha_preserved=true"
        assert oracle["expected"]["backward_compatible"] is True, "oracle: backward_compatible=true"

        state_file = tmp_path / "pipeline_state.json"
        output_file = tmp_path / "codex_review_result_manual.json"
        write_state(state_file, _min_pipeline_state())

        # 기존 방식: --diff-sha256 수동 지정, --base 미사용
        manual_sha = "EXAMPLE_DUMMY_AAAA1234567890abcdef"

        env = make_env(state_file)
        result = run_cli(
            [
                "review", "codex-record",
                "--stage", "code",
                "--result", "ACCEPT",
                "--review-model", "GPT-5.5",
                "--diff-sha256", manual_sha,
                "--head-sha", "EXAMPLE_DUMMY_CCCC",
                "--reviewer", "test-compat",
                "--output", str(output_file),
            ],
            env=env,
        )

        # 수동 sha 방식도 exit code 0이어야 함 (하위 호환)
        assert result.returncode == 0, (
            f"exit code {result.returncode}: stdout={result.stdout[:300]} stderr={result.stderr[:300]}"
        )

        assert output_file.exists(), "output 파일이 생성되어야 함"
        final_state = json.loads(output_file.read_text(encoding="utf-8"))

        # 수동 입력한 SHA가 그대로 기록되어야 함
        assert final_state.get("diff_sha256") == manual_sha, (
            f"수동 diff_sha256이 그대로 기록되어야 함. "
            f"기대: {manual_sha}, 실제: {final_state.get('diff_sha256')}"
        )

        # result가 ACCEPT로 기록되어야 함
        assert final_state.get("result") == "ACCEPT"


# ---------------------------------------------------------------------------
# 테스트 4: 잘못된 base ref 시 한국어 오류 메시지 + exit code 1 (edge oracle)
# ---------------------------------------------------------------------------

class TestInvalidBaseError:
    """AC-3: 존재하지 않는 --base ref 입력 시 한국어 오류 메시지 + exit code 1."""

    def test_invalid_base_error(self, tmp_path: Path) -> None:
        """존재하지 않는 브랜치를 --base에 지정하면 exit code 1 + 한국어 오류가 출력된다."""
        state_file = tmp_path / "pipeline_state.json"
        output_file = tmp_path / "codex_review_result_invalid.json"
        write_state(state_file, _min_pipeline_state())

        env = make_env(state_file)
        result = run_cli(
            [
                "review", "codex-record",
                "--stage", "code",
                "--result", "ACCEPT",
                "--review-model", "GPT-5.5",
                "--base", "INVALID_BRANCH_THAT_DOES_NOT_EXIST_9e38",
                "--head-sha", "EXAMPLE_DUMMY_DDDD",
                "--reviewer", "test-invalid",
                "--output", str(output_file),
            ],
            env=env,
        )

        # exit code 1이어야 함 (오류 시 1 반환)
        assert result.returncode == 1, (
            f"잘못된 base ref는 exit code 1이어야 함. 실제: {result.returncode}. "
            f"stdout={result.stdout[:300]} stderr={result.stderr[:300]}"
        )

        # 오류 메시지 확인 (stdout 또는 stderr에 한국어 오류 포함)
        combined_output = result.stdout + result.stderr
        assert "기준 diff 계산에 실패했습니다" in combined_output or \
               "브랜치 이름이 올바른지 확인하세요" in combined_output, (
            f"한국어 오류 메시지가 출력되어야 함. 실제 출력: {combined_output[:500]}"
        )

        # output 파일이 생성되지 않아야 함 (오류 시 파일 미생성)
        assert not output_file.exists(), "오류 발생 시 output 파일이 생성되면 안 됨"
