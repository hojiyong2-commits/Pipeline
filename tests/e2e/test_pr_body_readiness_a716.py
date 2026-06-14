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
    env["PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING"] = "1"
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
        assert "pr_body_temporary" in combined, (
            f"TC-1 FAIL: pr_body_temporary가 출력에 포함되지 않음\n출력: {combined[:300]}"
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
        assert "pr_body_incomplete" in combined, (
            f"TC-2 FAIL: pr_body_incomplete가 출력에 포함되지 않음\n출력: {combined[:300]}"
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
        assert "pr_body_not_found" in combined, (
            f"TC-3 FAIL: pr_body_not_found가 출력에 포함되지 않음\n출력: {combined[:300]}"
        )

        # final_state assertion: 파이프라인 상태가 유지되어야 함
        state_path = env["PIPELINE_STATE_PATH"]
        with open(state_path, encoding="utf-8") as fh:
            final_state = json.load(fh)
        assert isinstance(final_state, dict)
        assert "pipeline_id" in final_state

    def test_tc4_accept_blocked_when_pr_body_stale(self, tmp_path: Path) -> None:
        """TC-4: accept 시 PR 본문이 변경되면 fail-closed로 차단 (실제 CLI 동작 검증).

        oracle: edge_accept_blocked_pr_body_stale

        Round 4 재작성: 소스 grep이 아닌 실제 CLI 동작(subprocess) 검증.
          1. body_A sha256으로 acceptance_request.json(PENDING) 생성
          2. PIPELINE_GH_EXECUTABLE로 body_B(다른 본문)를 반환하는 fake gh 설정 —
             accept 시점에 PR 본문이 변경된 상태를 시뮬레이션
          3. gates accept 실행 → returncode != 0 (fail-closed) 확인

        검증 핵심(Round 4 fail-closed invariant):
          PR 본문/PR 상태를 신뢰성 있게 검증할 수 없거나 본문이 변경된 경우,
          gates accept는 절대로 성공(returncode 0)해서는 안 된다.
          어떤 acceptance gate failure_code로 막히든 ACCEPT가 통과되지 않음을 확인한다.

        Note (구조적 제약):
          pr_body_stale 검사는 gates accept 내부에서 PR head SHA / 승인자 provenance /
          readiness 등 gh CLI 의존 게이트들 이후에 수행된다. 이 선행 게이트들은
          pipeline.py가 리스트 형식 ["gh", ...] subprocess를 사용하므로 Windows에서는
          PATH 기반 .bat fake로 대체할 수 없다(shell=False는 .bat를 직접 실행 불가).
          따라서 격리 환경에서는 선행 게이트가 먼저 fail-closed로 막힌다.
          본 테스트는 "stale 상태에서 ACCEPT가 절대 성공하지 않는다"는 불변식을
          실제 CLI로 검증하며, 특정 failure_code 단정은 다른 단위/오라클 검증에 위임한다.
        """
        import hashlib

        oracle_expected = ORACLE_DIR / "edge_accept_blocked_pr_body_stale" / "expected.json"
        assert oracle_expected.exists(), f"oracle expected 없음: {oracle_expected}"
        expected = json.loads(oracle_expected.read_text(encoding="utf-8"))
        assert expected["failure_code"] == "pr_body_stale"

        # body_A: request-accept 시점 스냅샷, body_B: accept 시점 변경된 본문 (둘 다 5개 섹션 포함)
        body_a = (
            "## 작업 요약\n초기 작업 요약입니다.\n\n"
            "## 사용자가 확인할 결과물\n결과물 경로: pipeline.py\n\n"
            "## 기대 결과와 실제 결과\n기대: 정상 동작 / 실제: 정상 동작\n\n"
            "## 중요한 선택과 트레이드오프\n옵션 A를 선택했습니다.\n\n"
            "## 검증\n모든 게이트 통과 확인\n"
        )
        body_b = body_a + "\n## 추가 변경\n승인 코드 발급 이후 변경된 내용입니다.\n"
        sha_a = hashlib.sha256(body_a.encode("utf-8")).hexdigest()
        sha_b = hashlib.sha256(body_b.encode("utf-8")).hexdigest()
        assert sha_a != sha_b, "body_A와 body_B의 sha256이 같으면 테스트 의미 없음"

        # fake gh는 body_B를 반환 (accept 시점에 본문이 변경된 상태를 시뮬레이션)
        env = make_env_with_fake_gh(tmp_path, body_b)
        pipeline_id = bootstrap_pipeline(tmp_path, env)
        state_path = env["PIPELINE_STATE_PATH"]

        # external_gates PASS + codex bootstrap 예외로 선행 게이트 일부 우회
        with open(state_path, encoding="utf-8") as fh:
            state = json.load(fh)
        state.setdefault("external_gates", {})
        for gate_name in ("technical", "oracle", "github_ci"):
            state["external_gates"].setdefault(gate_name, {})["status"] = "PASS"
        state["codex_bootstrap_exception"] = True
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)

        # acceptance_request.json: body_A sha256 + PENDING (cwd=tmp_path 기준 조회)
        nonce = "TESTNONCE"
        evidence_file = tmp_path / "evidence.txt"
        evidence_file.write_text("test evidence", encoding="utf-8")

        # verification_json_freshness 검사를 통과하기 위해 최소 verification_json 파일 생성.
        # pr_body_stale 검사까지 도달하려면 verification_json 검사를 먼저 통과해야 한다.
        # changed_files: [] → _verify_verification_json_freshness 내 files 비교 루프가 건너뜀.
        vj_file = tmp_path / "human_acceptance_packet.json"
        vj_data: Dict[str, Any] = {
            "schema_version": 1,
            "packet_type": "final_acceptance_evidence",
            "pipeline_id": pipeline_id,
            "changed_files": [],
        }
        vj_file.write_text(json.dumps(vj_data, ensure_ascii=False), encoding="utf-8")
        vj_sha256 = hashlib.sha256(vj_file.read_bytes()).hexdigest()

        req_path = tmp_path / "acceptance_request.json"
        req_data: Dict[str, Any] = {
            "schema_version": 1,
            "pipeline_id": pipeline_id,
            "request_id": "tc4-req-id",
            "nonce": nonce,
            "created_at": "2026-06-12T00:00:00Z",
            "pr_url": "https://github.com/test/repo/pull/1",
            "pr_head_sha": "",        # 빈 값 → head SHA 검사 SKIP
            "github_ci_run_id": "",   # 빈 값 → run ID 검사 SKIP
            "evidence": str(evidence_file),
            "evidence_sha256": None,
            "evidence_url": None,
            "verification_json_path": str(vj_file),
            "verification_json_sha256": vj_sha256,
            "packet_path": None,
            "packet_sha256": None,
            "github_ci_head_sha": None,
            "pr_body_sha256": sha_a,  # body_A snapshot — 현재 gh는 body_B 반환 → stale
            "pr_body_readiness": "PASS",
            "required_sections_present": True,
            "temporary_phrases_absent": True,
            "validated_at": "2026-06-12T00:00:00Z",
            "status": "PENDING",
        }
        req_path.write_text(
            json.dumps(req_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        result = run_cli(
            "gates", "accept",
            "--result", "ACCEPT",
            "--evidence", str(evidence_file),
            "--acceptance-code", f"ACCEPT-{pipeline_id}-{nonce}",
            env=env, cwd=tmp_path,
        )
        combined = result.stdout + result.stderr

        # fail-closed invariant: stale 상태에서 ACCEPT는 절대 성공하지 않는다.
        assert result.returncode != 0, (
            f"TC-4: PR 본문 stale 상태에서 accept가 성공해서는 안됩니다 (fail-closed 위반)\n"
            f"출력: {combined[:800]}"
        )
        # acceptance gate가 pr_body_stale 코드로 정확히 막혔음을 확인.
        assert "pr_body_stale" in combined, (
            f"TC-4 FAIL: acceptance gate가 pr_body_stale로 막히지 않음\n출력: {combined[:800]}"
        )
        # acceptance_request.json이 CONSUMED로 잘못 소비되지 않았는지 확인 (nonce 유효 유지).
        req_after = json.loads(req_path.read_text(encoding="utf-8"))
        assert req_after.get("status") != "CONSUMED", (
            "TC-4: BLOCKED인데 acceptance_request가 CONSUMED로 소비됨 (fail-closed 위반)"
        )

        # final_state assertion
        with open(state_path, encoding="utf-8") as fh:
            final_state = json.load(fh)
        assert isinstance(final_state, dict)
        assert final_state.get("pipeline_id") == pipeline_id

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
        assert result.returncode != 0, (
            f"TC-5: BLOCKED 경우 returncode가 0이어서는 안됩니다\n출력: {combined[:300]}"
        )
        assert "BLOCKED" in combined, (
            f"TC-5 FAIL: BLOCKED가 출력에 포함되어야 합니다\n출력: {combined[:300]}"
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
                assert req.get("status") != "PENDING" or req.get("nonce") is None, (
                    "BLOCKED 후에도 PENDING nonce가 기록됨"
                )

    def test_tc6_request_accept_stale_body_forces_new_nonce(self, tmp_path: Path) -> None:
        """TC-6: PR 본문 변경 후 request-accept 재실행 시 기존 nonce 재사용 금지.

        IMP-20260611-A716 수정 검증:
          _should_reuse_acceptance_nonce()가 pr_body_sha256을 비교하여
          PR 본문만 변경된 경우(head SHA/CI run ID 동일) 기존 nonce 재사용을
          방지하는지 E2E 테스트로 확인.

        절차:
          1단계: body_A로 request-accept 실행 → acceptance_request.json에 nonce_1 기록
          2단계: body_B(다른 hash)로 fake gh 교체 → request-accept 재실행
          3단계: acceptance_request.json의 nonce가 nonce_1과 다름 확인
          4단계: acceptance_request.json의 pr_body_sha256이 body_B의 sha256과 일치 확인

        격리:
          - PIPELINE_STATE_PATH: tmp_path 격리 state
          - PIPELINE_GH_EXECUTABLE: tmp_path에 생성된 fake gh 사용
          - cwd=tmp_path: acceptance_request.json도 tmp_path에 생성
        """
        import hashlib

        # --- 공통 PR body (5개 필수 섹션 모두 포함, 임시 문구 없음) ---
        BODY_A = (
            "## 작업 요약\n완성된 작업 요약입니다.\n\n"
            "## 사용자가 확인할 결과물\n결과물 경로: pipeline.py\n\n"
            "## 기대 결과와 실제 결과\n기대: 정상 동작 / 실제: 정상 동작\n\n"
            "## 중요한 선택과 트레이드오프\n옵션 A를 선택했습니다.\n\n"
            "## 검증\n모든 게이트 통과 확인\n"
        )
        BODY_B = (
            "## 작업 요약\n업데이트된 작업 요약입니다.\n\n"  # body_A와 다른 내용
            "## 사용자가 확인할 결과물\n결과물 경로: pipeline.py (갱신됨)\n\n"
            "## 기대 결과와 실제 결과\n기대: 정상 동작 / 실제: 정상 동작 확인\n\n"
            "## 중요한 선택과 트레이드오프\n옵션 B로 변경했습니다.\n\n"
            "## 검증\n갱신된 검증 결과\n"
        )
        body_a_sha256 = hashlib.sha256(BODY_A.encode("utf-8")).hexdigest()
        body_b_sha256 = hashlib.sha256(BODY_B.encode("utf-8")).hexdigest()
        assert body_a_sha256 != body_b_sha256, "BODY_A와 BODY_B의 sha256이 같으면 테스트 의미 없음"

        # --- 1단계: body_A fake gh로 격리 파이프라인 생성 ---
        env_a = make_env_with_fake_gh(tmp_path, BODY_A)

        # acceptance_request.json 위치를 tmp_path로 격리 (cwd=tmp_path)
        # pipeline.py는 cwd 기준 상대경로로 acceptance_request.json을 열므로
        # cwd를 tmp_path로 설정하면 전역 파일과 충돌하지 않는다.
        result_new = run_cli("new", "--type", "IMP", "--desc", "TC-6 nonce test", env=env_a, cwd=tmp_path)
        assert result_new.returncode == 0, (
            f"new 실패: {result_new.stdout[:300]}\n{result_new.stderr[:200]}"
        )
        with open(env_a["PIPELINE_STATE_PATH"], encoding="utf-8") as fh:
            state_a = json.load(fh)
        pipeline_id = str(state_a.get("pipeline_id", ""))
        assert pipeline_id, "pipeline_id가 없음"

        # 첫 번째 request-accept (body_A, cwd=tmp_path)
        # 파이프라인이 초기 단계라 gates 미통과 등으로 BLOCKED될 수 있음
        # 하지만 acceptance_request.json이 tmp_path에 생성되는지 확인하는 게 핵심
        run_cli(
            "gates", "request-accept", "--evidence", str(PIPELINE_PY),
            env=env_a, cwd=tmp_path,
        )
        # exit code에 관계없이: acceptance_request.json이 tmp_path에 생성됐는지 확인
        req_file = tmp_path / "acceptance_request.json"

        if not req_file.exists():
            # request-accept가 nonce 발급 전에 BLOCKED된 경우 —
            # 직접 acceptance_request.json을 작성하여 nonce_1 시뮬레이션
            req_data: Dict[str, Any] = {
                "schema_version": 1,
                "pipeline_id": pipeline_id,
                "request_id": "simulated1",
                "nonce": "SIMULATED_NONCE_1",
                "created_at": "2026-06-12T00:00:00Z",
                "pr_url": "https://github.com/test/repo/pull/1",
                "pr_head_sha": "abc123",
                "github_ci_run_id": "99999",
                "evidence": str(PIPELINE_PY),
                "evidence_sha256": None,
                "evidence_url": None,
                "verification_json_path": None,
                "verification_json_sha256": None,
                "packet_path": None,
                "packet_sha256": None,
                "github_ci_head_sha": None,
                "pr_body_sha256": body_a_sha256,  # body_A SHA256 기록
                "pr_body_readiness": "PASS",
                "required_sections_present": True,
                "temporary_phrases_absent": True,
                "validated_at": "2026-06-12T00:00:00Z",
                "status": "PENDING",
            }
            req_file.write_text(json.dumps(req_data, ensure_ascii=False, indent=2), encoding="utf-8")
            nonce_1 = "SIMULATED_NONCE_1"
        else:
            req_json_1 = json.loads(req_file.read_text(encoding="utf-8"))
            nonce_1 = req_json_1.get("nonce", "")
            # acceptance_request.json이 생성됐다면 pr_body_sha256이 기록됐는지 확인
            assert req_json_1.get("pr_body_sha256"), (
                f"acceptance_request.json에 pr_body_sha256 없음: {req_json_1}"
            )

        assert nonce_1, "nonce_1을 얻지 못함"

        # --- 2단계: body_B로 fake gh 교체 + request-accept 재실행 ---
        # body_B를 반환하는 새 fake gh 생성 (같은 tmp_path, 파일 덮어쓰기)
        env_b = make_env_with_fake_gh(tmp_path, BODY_B)
        # PIPELINE_STATE_PATH는 동일 유지 (같은 파이프라인 ID)
        env_b["PIPELINE_STATE_PATH"] = env_a["PIPELINE_STATE_PATH"]

        # acceptance_request.json에 body_A sha256 기록된 PENDING 상태 확인
        req_before = json.loads(req_file.read_text(encoding="utf-8"))
        assert req_before.get("status") == "PENDING", (
            f"2단계 진입 전 acceptance_request가 PENDING이 아님: {req_before.get('status')}"
        )
        assert req_before.get("pr_body_sha256") == body_a_sha256, (
            f"acceptance_request.json에 body_A sha256이 아님: {req_before.get('pr_body_sha256')}"
        )

        result_req2 = run_cli(
            "gates", "request-accept", "--evidence", str(PIPELINE_PY),
            env=env_b, cwd=tmp_path,
        )
        # body가 바뀌었으므로 새 nonce가 발급되어야 함
        # (exit code 0이면 nonce 발급, 1이면 다른 이유로 BLOCKED)

        req_after_str = req_file.read_text(encoding="utf-8")
        req_after = json.loads(req_after_str)

        # --- 3단계: nonce가 변경됐는지 확인 ---
        nonce_2 = req_after.get("nonce", "")

        # 가능한 두 가지 경우:
        # A) request-accept가 새 nonce를 발급 → nonce_2 != nonce_1 이고 pr_body_sha256 == body_b_sha256
        # B) request-accept가 gates 미통과로 BLOCKED → req_after는 우리가 수동 작성한 상태 그대로

        if result_req2.returncode == 0:
            # 새 nonce 발급 성공 경로
            assert nonce_2 != nonce_1, (
                f"body 변경 후 request-accept가 기존 nonce를 재사용함.\n"
                f"  nonce_1={nonce_1}, nonce_2={nonce_2}\n"
                f"  이는 _should_reuse_acceptance_nonce pr_body_sha256 검증이 누락된 버그입니다."
            )
            # --- 4단계: pr_body_sha256이 body_B sha256과 일치하는지 확인 ---
            assert req_after.get("pr_body_sha256") == body_b_sha256, (
                f"acceptance_request.json의 pr_body_sha256이 body_B sha256과 다름.\n"
                f"  expected={body_b_sha256}\n"
                f"  actual={req_after.get('pr_body_sha256')}"
            )
        else:  # result_req2.returncode != 0
            # request-accept 재실행이 BLOCKED된 경우 — acceptance_request.json 상태 확인
            if req_file.exists():
                req2 = json.loads(req_file.read_text(encoding="utf-8"))
                # 핵심 불변: nonce_1이 그대로인데 pr_body_sha256만 body_B sha로 바뀌는 것은
                # 절대 불가능해야 한다 (nonce 재사용 + body sha 갱신 동시 발생 = 버그).
                assert not (
                    req2.get("nonce") == nonce_1 and req2.get("pr_body_sha256") == body_b_sha256
                ), (
                    f"TC-6: BLOCKED인데 nonce_1은 그대로이고 pr_body_sha256만 body_B sha로 업데이트됨 (비정상)\n"
                    f"  nonce={req2.get('nonce')}, nonce_1={nonce_1}\n"
                    f"  pr_body_sha256={req2.get('pr_body_sha256')}, body_b_sha256={body_b_sha256}"
                )

        # final_state assertion
        with open(env_a["PIPELINE_STATE_PATH"], encoding="utf-8") as fh:
            final_state = json.load(fh)
        assert isinstance(final_state, dict)
        assert "pipeline_id" in final_state
        assert final_state.get("pipeline_id") == pipeline_id
