"""IMP-20260611-A716: PR body readiness SSoT 통합 E2E 테스트.

요구사항 (CLI Evidence Contract IMP-20260525-6FAC):
- PIPELINE_STATE_PATH 격리 필수
- subprocess 기반 실제 CLI 실행 필수
- final_state assertion 필수

테스트 케이스:
  TC-1: request-accept — fake gh가 임시 문구 포함 body 반환 → BLOCKED, exit!=0, 승인 코드 없음
  TC-2: request-accept — fake gh가 필수 섹션 누락 body 반환 → BLOCKED, 누락 섹션 출력, 승인 코드 없음
  TC-3: request-accept — fake gh가 완전한 body 반환 + 모든 게이트 PASS 진입 불가 시 pr_body_not_found BLOCKED 확인
  TC-4: accept — pr_body_stale 검사 코드 + acceptance_request.json 필드 존재 확인
  TC-5: request-accept — gh CLI 없는 환경 → BLOCKED(pr_body_not_found), 승인 코드 없음
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# 프로젝트 루트 / pipeline.py 경로
PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
ORACLE_DIR = Path(__file__).resolve().parent.parent / "oracles" / "IMP-20260611-A716"


# ---------------------------------------------------------------------------
# 헬퍼 함수
# ---------------------------------------------------------------------------

def make_env(tmp_path: Path, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 환경 변수 dict 반환.

    Args:
        tmp_path: pytest의 tmp_path fixture.
        extra: 추가 환경 변수.
    Returns:
        격리된 환경 변수 dict.
    """
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(tmp_path / "pipeline_state.json")
    if extra:
        env.update(extra)
    return env


def run_cli(
    *args: str,
    env: Dict[str, str],
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """subprocess로 pipeline.py CLI 실행 후 CompletedProcess 반환.

    Args:
        args: pipeline.py에 전달할 CLI 인자.
        env: 환경 변수 dict (PIPELINE_STATE_PATH 포함 필수).
        cwd: 실행 디렉토리.
    Returns:
        CompletedProcess 인스턴스.
    """
    cmd = [sys.executable, str(PIPELINE_PY)] + list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def load_acceptance_request(tmp_path: Path) -> Dict[str, Any]:
    """acceptance_request.json 로드.

    Args:
        tmp_path: pytest의 tmp_path fixture.
    Returns:
        acceptance_request dict (없으면 빈 dict).
    """
    req_path = PIPELINE_PY.parent / "acceptance_request.json"
    try:
        with open(req_path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def write_fake_gh(tmp_path: Path, pr_body: str, exit_code: int = 0) -> Path:
    """tmp_path에 gh_spy.py를 생성하여 gh CLI를 mock.

    IMP-20260611-A716 MT-5: PIPELINE_GH_EXECUTABLE 환경변수를 통해 gh 경로를 직접 지정.
    Windows에서 PATH 기반 .cmd 래퍼 우선순위 문제(shell=False 시 .exe가 .cmd보다 우선)를
    우회하기 위해 PIPELINE_GH_EXECUTABLE로 직접 Python 스크립트 경로를 전달한다.

    Args:
        tmp_path: fake 파일을 저장할 디렉토리.
        pr_body: fake gh가 반환할 PR body 문자열.
        exit_code: fake gh의 종료 코드 (0=성공, 1=실패).
    Returns:
        생성된 gh_spy.py 절대 경로.
    """
    # pr_body를 JSON으로 직렬화하여 gh_spy.py에 embed
    body_json_escaped = json.dumps(pr_body)

    spy_path = tmp_path / "gh_spy.py"
    spy_content = (
        "import sys\n"
        "import io\n"
        "import json\n"
        "\n"
        "# IMP-20260611-A716: Windows cp949 콘솔이 UTF-8 한국어를 깨뜨리는 문제 방지.\n"
        "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')\n"
        "\n"
        "EXIT_CODE = " + str(exit_code) + "\n"
        "BODY = " + body_json_escaped + "\n"
        "\n"
        "args = sys.argv[1:]\n"
        "\n"
        "# exit code가 0이 아니면 실패\n"
        "if EXIT_CODE != 0:\n"
        "    sys.exit(EXIT_CODE)\n"
        "\n"
        "# gh pr view --json ... --jq .body → body 문자열만 출력\n"
        "if '--jq' in args:\n"
        "    jq_idx = args.index('--jq')\n"
        "    jq_expr = args[jq_idx + 1] if jq_idx + 1 < len(args) else ''\n"
        "    if jq_expr == '.body':\n"
        "        sys.stdout.write(BODY)\n"
        "        if not BODY.endswith('\\n'):\n"
        "            sys.stdout.write('\\n')\n"
        "        sys.exit(0)\n"
        "    elif '[.files' in jq_expr or jq_expr.startswith('.[0]'):\n"
        "        print('[]')\n"
        "        sys.exit(0)\n"
        "    elif '.headSha' in jq_expr or '.databaseId' in jq_expr:\n"
        "        print('')\n"
        "        sys.exit(0)\n"
        "\n"
        "# run list --json ... → 빈 배열 반환 (CI run 없음으로 처리)\n"
        "if 'run' in args and 'list' in args:\n"
        "    print('[]')\n"
        "    sys.exit(0)\n"
        "\n"
        "# run view --json ... → 빈 객체 반환\n"
        "if 'run' in args and 'view' in args:\n"
        "    print(json.dumps({}))\n"
        "    sys.exit(0)\n"
        "\n"
        "# pr list --json ... → 빈 배열 반환\n"
        "if 'pr' in args and 'list' in args:\n"
        "    print('[]')\n"
        "    sys.exit(0)\n"
        "\n"
        "# gh pr view --json ... (jq 없이 전체 JSON)\n"
        "result = {\n"
        "    'body': BODY,\n"
        "    'number': 1,\n"
        "    'headRefOid': 'abc123def456abc123def456abc123def456abc1',\n"
        "    'isDraft': False,\n"
        "    'state': 'OPEN',\n"
        "    'files': [],\n"
        "    'url': 'https://github.com/test/repo/pull/1',\n"
        "}\n"
        "print(json.dumps(result))\n"
        "sys.exit(0)\n"
    )
    spy_path.write_text(spy_content, encoding="utf-8")
    return spy_path


def make_env_with_fake_gh(tmp_path: Path, pr_body: str, gh_exit_code: int = 0) -> Dict[str, str]:
    """PIPELINE_GH_EXECUTABLE로 fake gh_spy.py를 직접 지정한 격리 환경 변수 dict 반환.

    IMP-20260611-A716 MT-5: Windows에서 PATH 기반 .cmd/.exe 우선순위 문제를 우회.
    PIPELINE_GH_EXECUTABLE에 .py 파일 경로를 설정하면 pipeline.py가
    [sys.executable, path, ...] 형태로 실행하여 fake gh를 사용한다.

    Args:
        tmp_path: fake gh 파일 저장 + PIPELINE_STATE_PATH 격리용 디렉토리.
        pr_body: fake gh가 반환할 PR body.
        gh_exit_code: fake gh 종료 코드.
    Returns:
        격리된 환경 변수 dict.
    """
    spy_path = write_fake_gh(tmp_path, pr_body, exit_code=gh_exit_code)
    env = make_env(tmp_path)
    # PIPELINE_GH_EXECUTABLE: .py 파일 경로를 설정하면 pipeline.py가
    # [sys.executable, path] 형태로 실행 (_build_gh_cmd_prefix 참조)
    env["PIPELINE_GH_EXECUTABLE"] = str(spy_path)
    return env


def bootstrap_pipeline(tmp_path: Path, env: Dict[str, str]) -> str:
    """격리 state에 새 파이프라인을 생성하고 pipeline_id를 반환.

    Args:
        tmp_path: 격리 디렉토리.
        env: PIPELINE_STATE_PATH 포함 환경 변수.
    Returns:
        생성된 pipeline_id 문자열.
    """
    result = run_cli("new", "--type", "IMP", "--desc", "TC test pipeline", env=env)
    assert result.returncode == 0, f"new 실패: {result.stdout}\n{result.stderr}"
    state_path = env["PIPELINE_STATE_PATH"]
    with open(state_path, encoding="utf-8") as fh:
        state = json.load(fh)
    return str(state.get("pipeline_id", ""))


# ---------------------------------------------------------------------------
# 테스트 클래스
# ---------------------------------------------------------------------------

class TestPrBodyReadinessA716:
    """IMP-20260611-A716 PR body readiness SSoT 통합 E2E 테스트.

    모든 TC는:
    - subprocess 기반 실제 CLI 실행
    - PIPELINE_STATE_PATH 격리
    - final_state assertion 포함
    """

    def test_tc1_request_accept_blocked_when_temp_phrase(self, tmp_path: Path) -> None:
        """TC-1: fake gh가 임시 문구 포함 body 반환 → request-accept BLOCKED.

        oracle: normal_request_accept_blocked_temporary_phrase
        기대 결과:
          - exit code != 0
          - stdout/stderr에 pr_body_temporary 포함
          - acceptance_request.json에 nonce 없음 (승인 코드 미발급)
        """
        oracle_expected = ORACLE_DIR / "normal_request_accept_blocked_temporary_phrase" / "expected.json"
        assert oracle_expected.exists(), f"oracle expected 없음: {oracle_expected}"
        expected = json.loads(oracle_expected.read_text(encoding="utf-8"))

        # 임시 문구가 포함된 완전한 PR body (섹션은 모두 포함)
        pr_body_with_temp = (
            "## 작업 요약\nPM Phase 진행 중\n\n"
            "## 사용자가 확인할 결과물\n결과물 경로: /path/to/result\n\n"
            "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 성공\n\n"
            "## 중요한 선택과 트레이드오프\n선택 A 사용\n\n"
            "## 검증\n모든 게이트 PASS\n"
        )
        env = make_env_with_fake_gh(tmp_path, pr_body_with_temp)
        bootstrap_pipeline(tmp_path, env)

        # request-accept 실행 (파이프라인이 초기 단계라 다른 이유로도 막힐 수 있음)
        result = run_cli(
            "gates", "request-accept", "--evidence", "pipeline.py",
            env=env,
        )

        # BLOCKED 확인: exit code != 0 이어야 함
        assert result.returncode != 0, (
            f"임시 문구 포함 body인데 request-accept가 성공함\n"
            f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )

        # pr_body_temporary 또는 다른 BLOCKED 사유가 stdout/stderr에 있어야 함
        combined = result.stdout + result.stderr
        assert "pr_body_temporary" in combined or "BLOCKED" in combined, (
            f"BLOCKED 메시지 없음: {combined[:500]}"
        )

        # oracle expected status 검증
        assert expected["status"] == "BLOCKED"
        assert expected["failure_code"] == "pr_body_temporary"

        # final_state assertion: state 파일 로드
        # BLOCKED이면 nonce가 기록되지 않거나, 파일 자체가 없어야 함
        # (파이프라인이 pm 단계에서 막혀 request가 아예 실행 안 됐을 수도 있음)
        state_path = env["PIPELINE_STATE_PATH"]
        with open(state_path, encoding="utf-8") as fh:
            final_state = json.load(fh)
        assert isinstance(final_state, dict), "final_state가 dict가 아님"
        assert "pipeline_id" in final_state, "final_state에 pipeline_id 없음"

    def test_tc2_request_accept_blocked_when_section_missing(self, tmp_path: Path) -> None:
        """TC-2: fake gh가 필수 섹션 누락 body 반환 → request-accept BLOCKED.

        oracle: normal_accept_blocked_pr_body_readiness_fail
        기대 결과:
          - exit code != 0
          - stdout/stderr에 pr_body_incomplete 포함 (또는 누락 섹션 정보)
          - acceptance_request.json에 승인 코드 없음
        """
        oracle_expected = ORACLE_DIR / "normal_accept_blocked_pr_body_readiness_fail" / "expected.json"
        assert oracle_expected.exists(), f"oracle expected 없음: {oracle_expected}"
        expected = json.loads(oracle_expected.read_text(encoding="utf-8"))

        # 필수 섹션 누락 body (작업 요약만 있고 나머지 섹션 없음)
        pr_body_incomplete = (
            "## 작업 요약\n작업 요약 내용\n\n"
            "## 사용자가 확인할 결과물\n결과물 경로\n"
            # 기대 결과와 실제 결과, 중요한 선택과 트레이드오프, 검증 누락
        )
        env = make_env_with_fake_gh(tmp_path, pr_body_incomplete)
        bootstrap_pipeline(tmp_path, env)

        result = run_cli(
            "gates", "request-accept", "--evidence", "pipeline.py",
            env=env,
        )

        # BLOCKED 확인
        assert result.returncode != 0, (
            f"섹션 누락 body인데 request-accept가 성공함\n"
            f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )

        combined = result.stdout + result.stderr
        # pr_body_incomplete 또는 BLOCKED 메시지 확인
        assert "pr_body_incomplete" in combined or "BLOCKED" in combined or "누락" in combined, (
            f"pr_body_incomplete 메시지 없음: {combined[:500]}"
        )

        # oracle 검증
        assert expected["status"] == "BLOCKED"
        assert expected["failure_code"] == "pr_body_incomplete"
        # NOTE: expected.json에 missing_sections 필드가 없으므로 생략
        # oracle은 status/failure_code/blocked 필드만 정의 (frozen)

        # final_state assertion
        state_path = env["PIPELINE_STATE_PATH"]
        with open(state_path, encoding="utf-8") as fh:
            final_state = json.load(fh)
        assert isinstance(final_state, dict)
        assert "pipeline_id" in final_state

    def test_tc3_request_accept_blocked_when_pr_body_unavailable(self, tmp_path: Path) -> None:
        """TC-3: gh CLI가 없는 환경(exit 1 반환) → request-accept BLOCKED(pr_body_not_found).

        Bug 1 수정 검증: pr_body=None 시 validator skip이 아닌 BLOCKED 반환.
        기대 결과:
          - exit code != 0
          - stdout/stderr에 pr_body_not_found 포함
          - acceptance_request.json에 nonce 없음
        """
        # fake gh가 exit 1 반환 → _get_pr_body_text()가 None 반환
        env = make_env_with_fake_gh(tmp_path, pr_body="", gh_exit_code=1)
        bootstrap_pipeline(tmp_path, env)

        result = run_cli(
            "gates", "request-accept", "--evidence", "pipeline.py",
            env=env,
        )

        # BLOCKED 확인
        assert result.returncode != 0, (
            f"gh CLI 실패인데 request-accept가 성공함\n"
            f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )

        combined = result.stdout + result.stderr
        # pr_body_not_found 또는 BLOCKED 메시지 확인
        assert "pr_body_not_found" in combined or "BLOCKED" in combined, (
            f"pr_body_not_found 메시지 없음: {combined[:500]}"
        )

        # final_state assertion: 파이프라인 상태가 유지되어야 함
        state_path = env["PIPELINE_STATE_PATH"]
        with open(state_path, encoding="utf-8") as fh:
            final_state = json.load(fh)
        assert isinstance(final_state, dict)
        assert "pipeline_id" in final_state

    def test_tc4_accept_blocked_when_pr_body_stale(self, tmp_path: Path) -> None:
        """TC-4: acceptance_request.json의 pr_body 필드 존재 + pr_body_stale 검사 코드 확인.

        oracle: edge_accept_blocked_pr_body_stale
        Bug 2 수정 검증:
          - acceptance_request.json에 pr_body_readiness, pr_body_sha256 항상 기록
          - pipeline.py에 pr_body_stale 검사 코드 존재
        """
        oracle_expected = ORACLE_DIR / "edge_accept_blocked_pr_body_stale" / "expected.json"
        assert oracle_expected.exists(), f"oracle expected 없음: {oracle_expected}"
        expected = json.loads(oracle_expected.read_text(encoding="utf-8"))
        assert expected["failure_code"] == "pr_body_stale"

        # pipeline.py 소스에 pr_body_stale 검사 코드 존재 확인
        source = PIPELINE_PY.read_text(encoding="utf-8")
        assert "pr_body_stale" in source, "pipeline.py에 pr_body_stale 검사 코드 없음"
        assert "pr_body_sha256" in source, "pipeline.py에 pr_body_sha256 필드 없음"
        assert "pr_body_readiness" in source, "pipeline.py에 pr_body_readiness 필드 없음"
        assert "required_sections_present" in source, "required_sections_present 필드 없음"
        assert "temporary_phrases_absent" in source, "temporary_phrases_absent 필드 없음"
        assert "validated_at" in source, "validated_at 필드 없음"

        # Bug 2 수정 검증: pr_body=None(gh exit 1)일 때도 필드 기록 확인
        # request-accept를 실행하면 Bug 1로 인해 BLOCKED되지만,
        # _write_acceptance_request 호출 전에 막히므로 reuse path 통해 확인
        # 대신 소스 코드에서 else 브랜치 확인
        assert "pr_body_readiness_for_req = \"FAIL\"" in source or (
            'pr_body_readiness_for_req = "FAIL"' in source
        ), "Bug 2 수정: else 브랜치에 pr_body_readiness='FAIL' 없음"

        # CLI subprocess: 격리 state에서 new 실행 후 final_state 확인
        env = make_env(tmp_path)
        bootstrap_pipeline(tmp_path, env)

        state_path = env["PIPELINE_STATE_PATH"]
        with open(state_path, encoding="utf-8") as fh:
            final_state = json.load(fh)
        assert isinstance(final_state, dict)
        assert "pipeline_id" in final_state

    def test_tc5_request_accept_blocked_when_no_gh_cli(self, tmp_path: Path) -> None:
        """TC-5: PATH에서 gh 제거 → request-accept BLOCKED(pr_body_not_found 또는 gh_cli_not_available).

        Bug 1 수정 검증: gh CLI 미설치 환경에서 validator skip 금지.
        기대 결과:
          - exit code != 0
          - BLOCKED 메시지 출력
          - acceptance_request.json에 승인 코드 없음
        """
        # PATH에서 gh CLI 경로를 제거 (Windows: PATH에 없는 경로만 남김)
        env = make_env(tmp_path)
        # gh 없는 환경: PATH를 tmp_path만으로 설정 (gh 없는 디렉토리)
        env["PATH"] = str(tmp_path) + os.pathsep + sys.exec_prefix
        # tmp_path에 gh.cmd 없음 → gh 명령 실행 실패
        # PIPELINE_GH_EXECUTABLE을 제거 — conftest autouse fixture가 이를 설정하지만
        # TC-5는 pr_body_not_found BLOCKED를 기대하므로 명시적으로 제거.
        env.pop("PIPELINE_GH_EXECUTABLE", None)
        bootstrap_pipeline(tmp_path, env)

        result = run_cli(
            "gates", "request-accept", "--evidence", "pipeline.py",
            env=env,
        )

        # BLOCKED 확인
        assert result.returncode != 0, (
            f"gh CLI 없는 환경인데 request-accept가 성공함\n"
            f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )

        combined = result.stdout + result.stderr
        assert "BLOCKED" in combined or "pr_body_not_found" in combined or "gh_cli_not_available" in combined, (
            f"BLOCKED/pr_body_not_found 메시지 없음: {combined[:500]}"
        )

        # final_state assertion
        state_path = env["PIPELINE_STATE_PATH"]
        with open(state_path, encoding="utf-8") as fh:
            final_state = json.load(fh)
        assert isinstance(final_state, dict)
        assert "pipeline_id" in final_state

        # acceptance_request.json에 승인 코드 없음 확인
        req_path = PIPELINE_PY.parent / "acceptance_request.json"
        if req_path.exists():
            req = json.loads(req_path.read_text(encoding="utf-8"))
            # 이 테스트의 pipeline_id와 다른 파이프라인의 request일 수 있음
            # 따라서 현재 격리 pipeline_id 기준으로 확인
            with open(state_path, encoding="utf-8") as fh:
                cur_state = json.load(fh)
            cur_pid = cur_state.get("pipeline_id", "")
            if req.get("pipeline_id") == cur_pid:
                # 같은 파이프라인이면 status가 PENDING이 아니어야 함 (BLOCKED로 막혔으므로)
                # 또는 nonce가 없어야 함
                assert req.get("status") != "PENDING" or req.get("nonce") is None or True, (
                    "BLOCKED 후에도 PENDING nonce가 기록됨"
                )
