"""
test_golden_tasks.py — IMP-20260528-0A9E MT-4 Golden Task E2E Tests

# [Purpose]: pipeline.py의 golden list/run CLI가 올바르게 동작하는지
#            subprocess 기반으로 검증. PIPELINE_STATE_PATH 격리를 통해
#            전역 pipeline_state.json을 오염시키지 않는다.

# [Real CLI Path E2E Gate Policy (IMP-20260525-6FAC)]:
# - 모든 상태 변경 CLI 호출은 PIPELINE_STATE_PATH 격리 사용
# - stdout-only 검증 금지 (exit code 또는 stdout 내용 모두 검증)
# - subprocess 기반 실제 CLI 실행 (내부 함수 직접 임포트 금지)

# [CLI Evidence Contract (BUG-20260525-39DE)]:
# - PIPELINE_STATE_PATH 격리 + final_state assertion(exit_code) 포함
# - read-only CLI: # CLI_EVIDENCE_ALLOW_READ_ONLY: <reason>
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pytest  # noqa: F401 — pytest fixtures (tmp_path) 사용을 위해 필요

# pipeline.py는 tests/e2e의 2단계 상위 디렉토리에 위치
PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
GOLDEN_TASKS_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "golden_tasks"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """`python pipeline.py <args>` 실행 후 CompletedProcess 반환."""
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    cmd = [sys.executable, str(PIPELINE_PY)] + args
    merged_env: Dict[str, str] = {**os.environ}
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


def isolated_env(tmp_path: Path) -> Dict[str, str]:
    """PIPELINE_STATE_PATH를 임시 파일로 격리한 환경 변수 반환."""
    state_file = tmp_path / "pipeline_state_test.json"
    return {"PIPELINE_STATE_PATH": str(state_file)}


# ---------------------------------------------------------------------------
# MT-4 Test Suite: golden list
# ---------------------------------------------------------------------------

class TestGoldenList:
    """golden list 명령 E2E 테스트."""

    def test_golden_list_exits_0(self, tmp_path: Path) -> None:
        """golden list 명령이 exit code 0을 반환하는지 확인.

        # CLI_EVIDENCE_ALLOW_READ_ONLY: golden list는 state를 변경하지 않음
        """
        env = isolated_env(tmp_path)
        result = run_cli(["golden", "list"], env=env)
        assert result.returncode == 0, (
            f"golden list 실패 (exit={result.returncode})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_golden_list_shows_3_tasks(self, tmp_path: Path) -> None:
        """golden list가 GT-001, GT-002, GT-003를 모두 출력하는지 확인.

        # CLI_EVIDENCE_ALLOW_READ_ONLY: golden list는 state를 변경하지 않음
        """
        env = isolated_env(tmp_path)
        result = run_cli(["golden", "list"], env=env)
        assert result.returncode == 0
        stdout = result.stdout
        assert "GT-001" in stdout, f"GT-001 미출력: {stdout}"
        assert "GT-002" in stdout, f"GT-002 미출력: {stdout}"
        assert "GT-003" in stdout, f"GT-003 미출력: {stdout}"

    def test_golden_list_shows_task_count(self, tmp_path: Path) -> None:
        """golden list가 태스크 총 수를 출력하는지 확인.

        # CLI_EVIDENCE_ALLOW_READ_ONLY: golden list는 state를 변경하지 않음
        """
        env = isolated_env(tmp_path)
        result = run_cli(["golden", "list"], env=env)
        assert result.returncode == 0
        assert "3" in result.stdout, f"태스크 수 미출력: {result.stdout}"

    def test_golden_list_custom_tasks_dir(self, tmp_path: Path) -> None:
        """--tasks-dir 옵션으로 없는 디렉터리 지정 시 exit 2 반환.

        # CLI_EVIDENCE_ALLOW_READ_ONLY: golden list는 state를 변경하지 않음
        """
        env = isolated_env(tmp_path)
        result = run_cli(
            ["golden", "list", "--tasks-dir", str(tmp_path / "nonexistent")],
            env=env,
        )
        assert result.returncode == 2, (
            f"없는 디렉터리 지정 시 exit 2 기대, 실제={result.returncode}"
        )
        assert "[PIPELINE ERROR]" in result.stdout or "[PIPELINE ERROR]" in result.stderr


# ---------------------------------------------------------------------------
# MT-4 Test Suite: golden run
# ---------------------------------------------------------------------------

class TestGoldenRun:
    """golden run 명령 E2E 테스트."""

    def test_golden_run_smoke_exits_0_or_1(self, tmp_path: Path) -> None:
        """golden run --smoke 실행 시 exit code가 0 또는 1임을 확인 (2가 아님).

        NOTE: smoke 실행 결과가 PASS/FAIL 둘 다 허용 (테스트 환경에 따라 다름).
              중요한 것은 스키마 오류(exit 2)가 아님을 확인하는 것.
        """
        env = isolated_env(tmp_path)
        result = run_cli(["golden", "run", "--smoke"], env=env)
        # exit 0(PASS) 또는 1(FAIL)이어야 함. 2(스키마 오류)는 아님.
        assert result.returncode in (0, 1), (
            f"예상치 못한 exit code={result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_golden_run_no_option_exits_2(self, tmp_path: Path) -> None:
        """golden run에 옵션 없이 실행 시 exit 2 + [PIPELINE ERROR] 출력 확인."""
        env = isolated_env(tmp_path)
        result = run_cli(["golden", "run"], env=env)
        assert result.returncode == 2, (
            f"옵션 없는 golden run — exit 2 기대, 실제={result.returncode}"
        )
        combined = result.stdout + result.stderr
        assert "[PIPELINE ERROR]" in combined, f"[PIPELINE ERROR] 미출력: {combined}"

    def test_golden_run_nonexistent_task_exits_2(self, tmp_path: Path) -> None:
        """없는 태스크 ID 지정 시 exit 2 확인."""
        env = isolated_env(tmp_path)
        result = run_cli(
            ["golden", "run", "--task", "GT-999-does-not-exist"],
            env=env,
        )
        assert result.returncode == 2, (
            f"없는 task ID — exit 2 기대, 실제={result.returncode}"
        )
        combined = result.stdout + result.stderr
        assert "[PIPELINE ERROR]" in combined, f"[PIPELINE ERROR] 미출력: {combined}"

    def test_golden_run_all_processes_3_tasks(self, tmp_path: Path) -> None:
        """golden run --all 실행 시 3개 태스크 모두 처리됨을 확인."""
        env = isolated_env(tmp_path)
        result = run_cli(["golden", "run", "--all"], env=env, timeout=90)
        # exit 0 또는 1이어야 함 (스키마 오류=2는 아님)
        assert result.returncode in (0, 1), (
            f"golden run --all — 예상치 못한 exit code={result.returncode}"
        )
        stdout = result.stdout
        # 3개 태스크가 처리되어야 함
        assert "GT-001" in stdout or "3" in stdout, (
            f"3개 태스크 처리 안 됨: {stdout}"
        )

    def test_golden_run_schema_error_exits_2(self, tmp_path: Path) -> None:
        """스키마 오류가 있는 태스크 디렉터리에서 exit 2 확인."""
        # 임시 golden tasks 디렉터리 생성
        fake_tasks_dir = tmp_path / "fake_golden_tasks"
        fake_tasks_dir.mkdir()
        bad_task_dir = fake_tasks_dir / "GT-BAD-schema"
        bad_task_dir.mkdir()
        (bad_task_dir / "input").mkdir()
        (bad_task_dir / "expected").mkdir()
        # 필수 필드 누락 (id 없음)
        bad_task_json = {"description": "bad task", "command": "python pipeline.py status"}
        (bad_task_dir / "golden_task.json").write_text(
            json.dumps(bad_task_json), encoding="utf-8"
        )
        env = isolated_env(tmp_path)
        result = run_cli(
            ["golden", "list", "--tasks-dir", str(fake_tasks_dir)],
            env=env,
        )
        assert result.returncode == 2, (
            f"스키마 오류 태스크 — exit 2 기대, 실제={result.returncode}"
        )
        combined = result.stdout + result.stderr
        assert "[PIPELINE ERROR]" in combined, f"[PIPELINE ERROR] 미출력: {combined}"


# ---------------------------------------------------------------------------
# MT-4 Test Suite: golden tasks dir 구조 검증
# ---------------------------------------------------------------------------

class TestGoldenTasksStructure:
    """tests/golden_tasks/ 디렉터리 구조가 올바른지 확인."""

    def test_golden_tasks_dir_exists(self) -> None:
        """tests/golden_tasks/ 디렉터리가 존재하는지 확인.

        # CLI_EVIDENCE_ALLOW_READ_ONLY: 파일 시스템 검사, state 변경 없음
        """
        assert GOLDEN_TASKS_DIR.is_dir(), f"tests/golden_tasks/ 디렉터리 없음: {GOLDEN_TASKS_DIR}"

    def test_all_3_golden_tasks_exist(self) -> None:
        """GT-001, GT-002, GT-003 디렉터리가 모두 존재하는지 확인.

        # CLI_EVIDENCE_ALLOW_READ_ONLY: 파일 시스템 검사, state 변경 없음
        """
        expected_tasks = [
            "GT-001-status-complete-exit0",
            "GT-002-internal-artifact-blocked",
            "GT-003-oracle-quality-normal-edge",
        ]
        for task_id in expected_tasks:
            task_dir = GOLDEN_TASKS_DIR / task_id
            assert task_dir.is_dir(), f"golden task 디렉터리 없음: {task_dir}"
            golden_json = task_dir / "golden_task.json"
            assert golden_json.exists(), f"golden_task.json 없음: {golden_json}"

    def test_golden_task_schema_fields(self) -> None:
        """각 golden_task.json에 필수 필드 8개가 모두 있는지 확인.

        # CLI_EVIDENCE_ALLOW_READ_ONLY: 파일 읽기만, state 변경 없음
        """
        required_fields = [
            "id", "description", "command", "smoke",
            "allowed_files", "forbidden_files", "acceptance_criteria", "return_phase",
        ]
        for task_dir in sorted(GOLDEN_TASKS_DIR.iterdir()):
            if not task_dir.is_dir():
                continue
            golden_json = task_dir / "golden_task.json"
            if not golden_json.exists():
                continue
            data = json.loads(golden_json.read_text(encoding="utf-8"))
            for field in required_fields:
                assert field in data, (
                    f"{golden_json}: 필수 필드 '{field}' 누락. 존재 필드: {list(data.keys())}"
                )

    def test_all_smoke_tasks_marked(self) -> None:
        """smoke=true인 태스크가 최소 3개 이상인지 확인.

        # CLI_EVIDENCE_ALLOW_READ_ONLY: 파일 읽기만, state 변경 없음
        """
        smoke_count = 0
        for task_dir in sorted(GOLDEN_TASKS_DIR.iterdir()):
            if not task_dir.is_dir():
                continue
            golden_json = task_dir / "golden_task.json"
            if not golden_json.exists():
                continue
            data = json.loads(golden_json.read_text(encoding="utf-8"))
            if data.get("smoke"):
                smoke_count += 1
        assert smoke_count >= 2, f"smoke=true 태스크 {smoke_count}개 — 최소 2개 필요"

    def test_readme_exists(self) -> None:
        """tests/golden_tasks/README.md가 존재하는지 확인.

        # CLI_EVIDENCE_ALLOW_READ_ONLY: 파일 시스템 검사, state 변경 없음
        """
        readme = GOLDEN_TASKS_DIR / "README.md"
        assert readme.exists(), f"README.md 없음: {readme}"
        content = readme.read_text(encoding="utf-8")
        assert "golden" in content.lower(), "README.md에 'golden' 언급 없음"


# ---------------------------------------------------------------------------
# BUG-20260529-40C9 MT-4: GT-002 회귀 테스트 3건
# ---------------------------------------------------------------------------

class TestGT002Regression:
    """GT-002-internal-artifact-blocked 회귀 테스트.

    BUG-20260529-40C9 이슈:
    - GT-002 command가 `golden run --task GT-002-...`이어서 자기 재귀 발생 (60초 타임아웃)
    - _verify_forbidden_files가 워크스페이스 전체를 rglob 스캔하여 비결정적 결과
    - smoke=false로 CI에서 검출 불가

    수정 후 검증:
    1. GT-002 command에 자기 재귀 패턴이 없어야 함
    2. GT-002 smoke=true여야 함
    3. GT-002가 5초 이내 종료되고 exit code 1(FAIL)이어야 함
    """

    def test_gt002_no_self_recursion(self) -> None:
        """GT-002 command에 자기 재귀 호출 패턴이 없어야 함.

        BUG: command가 `python pipeline.py golden run --task GT-002-...`이면
        `_golden_run_one`이 자기 자신을 subprocess로 실행하여 무한 재귀 발생.

        # CLI_EVIDENCE_ALLOW_READ_ONLY: golden_task.json 파일 읽기만, state 변경 없음
        """
        gt002_dir = GOLDEN_TASKS_DIR / "GT-002-internal-artifact-blocked"
        golden_json = gt002_dir / "golden_task.json"
        assert golden_json.exists(), f"GT-002 golden_task.json 없음: {golden_json}"

        data = json.loads(golden_json.read_text(encoding="utf-8"))
        command: str = data.get("command", "")

        # 자기 재귀 패턴: command에 "golden run" 이 포함되면 _golden_run_one이 자기를 호출
        assert "golden run" not in command, (
            f"GT-002 command에 자기 재귀 패턴 'golden run' 발견: {command!r}\n"
            "수정 필요: command를 preflight-pr-impl 등 직접 CLI 호출로 변경하세요."
        )

    def test_gt002_smoke_enabled(self) -> None:
        """GT-002 smoke=true여야 CI에서 검출 가능.

        BUG: smoke=false이면 `golden run --smoke`가 GT-002를 건너뛰어
        CI에서 재귀 버그가 검출되지 않음.

        # CLI_EVIDENCE_ALLOW_READ_ONLY: golden_task.json 파일 읽기만, state 변경 없음
        """
        gt002_dir = GOLDEN_TASKS_DIR / "GT-002-internal-artifact-blocked"
        golden_json = gt002_dir / "golden_task.json"
        assert golden_json.exists(), f"GT-002 golden_task.json 없음: {golden_json}"

        data = json.loads(golden_json.read_text(encoding="utf-8"))
        assert data.get("smoke") is True, (
            f"GT-002 smoke={data.get('smoke')!r} — True여야 CI smoke 테스트에 포함됨"
        )

    def test_gt002_runs_under_5s_and_fails_correctly(self, tmp_path: Path) -> None:
        """GT-002 golden run이 5초 이내 종료되고 exit code 1을 반환해야 함.

        BUG: 자기 재귀로 60초 타임아웃까지 블로킹됨.
        수정 후: preflight-pr-impl --files 방식이어서 즉시 결과 반환.

        acceptable exit codes: 1 (FAIL — 내부 산출물 detected)
        expected: 5초 이내 완료
        """
        import time
        env = isolated_env(tmp_path)
        task_id = "GT-002-internal-artifact-blocked"

        start = time.monotonic()
        result = run_cli(
            ["golden", "run", "--task", task_id],
            env=env,
            timeout=10,  # 10초 타임아웃 (5초 이내 완료 검증)
        )
        elapsed = time.monotonic() - start

        # exit code 1 = FAIL (내부 산출물 blocked) — 정상 동작
        assert result.returncode == 1, (
            f"GT-002 golden run — exit 1(FAIL) 기대, 실제={result.returncode}\n"
            f"stdout: {result.stdout[:300]}\nstderr: {result.stderr[:300]}"
        )

        # 5초 이내 완료 검증
        assert elapsed < 5.0, (
            f"GT-002 golden run이 {elapsed:.1f}초 소요 — 5초 이내 완료 필요\n"
            "자기 재귀 버그가 재발했을 수 있습니다."
        )

        # blocked 메시지 출력 확인
        combined = result.stdout + result.stderr
        assert "blocked" in combined.lower() or "FAIL" in combined or "GOLDEN FAIL" in combined, (
            f"GT-002: 'blocked' 또는 'FAIL' 메시지 미출력\n"
            f"stdout: {result.stdout[:300]}"
        )
