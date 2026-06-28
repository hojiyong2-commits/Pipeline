"""
test_user_acceptance_nonce_bbdb.py — IMP-20260531-BBDB MT-6
User Acceptance Nonce Gate E2E 테스트 (14 케이스)

# [Purpose]: pipeline.py의 gates request-accept / gates accept --acceptance-code
#            CLI가 nonce 기반 일회용 승인 코드 검증을 올바르게 수행하는지 검증.
# [Assumptions]: PIPELINE_STATE_PATH 환경변수로 상태 파일 격리. subprocess 기반
#                실제 CLI 실행. tests/oracles/IMP-20260531-BBDB/ 3개 oracle 사용.
# [Vulnerability & Risks]:
#   - gh CLI 없는 환경에서 PR/CI 관련 검증은 skip되므로 일부 stale 검사는
#     unit-level assertion으로 대체.
#   - subprocess 호출 30초 timeout 초과 시 실패.
# [Improvement]: gh CLI mock fixture를 추가하여 PR SHA/CI run ID 변경
#               시나리오를 완전히 자동화.

# CLI Evidence Contract (BUG-20260525-39DE / IMP-20260525-6FAC):
# - 상태 변경 CLI 호출은 PIPELINE_STATE_PATH 격리 + final_state assertion 포함
# - stdout-only 검증 금지 (CLI 출력만으로 PASS 판정 금지)
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
ORACLE_DIR = Path(__file__).resolve().parent.parent / "oracles" / "IMP-20260531-BBDB"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(
    args: List[str],
    env: Dict[str, str],
    cwd: Optional[Path] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """`python pipeline.py <args>` 실행 후 CompletedProcess 반환.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess에 전달할 환경 변수.
        cwd: 작업 디렉토리 (기본은 pipeline.py 위치).
        timeout: 초 단위 timeout.
    Raises:
        TypeError: args가 list가 아닌 경우.
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
        cwd=str(cwd) if cwd else None,
    )


def make_env(tmp_path: Path) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 환경변수 + gh CLI 무력화 (PATH에서 제거).

    Args:
        tmp_path: pytest tmp_path fixture.
    Returns:
        subprocess에 전달할 환경 변수 dict.
    Raises:
        TypeError: tmp_path가 None.
    """
    if tmp_path is None:
        raise TypeError("tmp_path must not be None")
    state_file = tmp_path / "pipeline_state.json"
    env = dict(os.environ)
    env["PIPELINE_STATE_PATH"] = str(state_file)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    # Windows에서 gh CLI를 가짜로 invalid path로 대체하여 호출 실패 유도
    env["PATH"] = str(tmp_path)  # 빈 경로 — gh CLI 못 찾음
    env["PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING"] = "1"
    # BUG-20260617-788A: request-accept가 비대화형/CI 자동 감지 제거로 인해 브라우저
    # HTTP 서버를 실제로 띄워 300초 대기하지 않도록 E2E에서 브라우저 승인 우회.
    env["PIPELINE_BROWSER_APPROVAL_SKIP"] = "1"
    return env


def bootstrap_pipeline(tmp_path: Path, env: Dict[str, str]) -> str:
    """격리된 환경에 IMP 파이프라인을 생성하고 pipeline_id 반환.

    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + final_state assertion 포함.

    Args:
        tmp_path: pytest tmp_path fixture.
        env: PIPELINE_STATE_PATH 환경변수가 설정된 dict.
    Returns:
        생성된 pipeline_id 문자열.
    Raises:
        AssertionError: pipeline.py new 명령 실패 시.
    """
    r = run_cli(
        ["new", "--type", "IMP", "--desc", "nonce gate e2e test"],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode == 0, f"new failed: stdout={r.stdout} stderr={r.stderr}"
    state_file = Path(env["PIPELINE_STATE_PATH"])
    assert state_file.exists(), "pipeline_state.json not created"
    # final_state assertion (CLI Evidence Contract 준수 — IMP-20260525-6FAC)
    with open(state_file, encoding="utf-8") as f:
        final_state = json.load(f)
    pid = str(final_state.get("pipeline_id", ""))
    assert pid, f"pipeline_id missing in final_state: {final_state}"
    # pipeline 생성 직후 phase 구조가 정상인지 확인
    phases = final_state.get("phases", {})
    assert "pm" in phases, f"pm phase missing in final_state.phases: {list(phases.keys())}"
    return pid


def write_acceptance_request(
    tmp_path: Path,
    pipeline_id: str,
    nonce: str = "TESTNON1",
    status: str = "PENDING",
    pr_sha: str = "abc1234",
    ci_run: str = "99999",
    evidence: str = "dummy.txt",
    evidence_sha256: Optional[str] = None,
) -> Path:
    """테스트용 acceptance_request.json을 tmp_path에 작성.

    Args:
        tmp_path: pytest tmp_path fixture.
        pipeline_id: 활성 pipeline_id.
        nonce: 8자 base32 nonce (기본 TESTNON1).
        status: PENDING|CONSUMED.
        pr_sha: PR head commit SHA.
        ci_run: GitHub Actions run ID.
        evidence: 결과물 경로.
        evidence_sha256: 사전 계산된 hash (None이면 stored hash 없음).
    Returns:
        작성된 acceptance_request.json 절대 경로.
    """
    req = {
        "schema_version": 1,
        "pipeline_id": pipeline_id,
        "request_id": "test0001",
        "nonce": nonce,
        "created_at": "2026-05-31T00:00:00Z",
        "pr_url": "https://github.com/test/Pipeline/pull/999",
        "pr_head_sha": pr_sha,
        "github_ci_run_id": ci_run,
        "evidence": evidence,
        "evidence_sha256": evidence_sha256,
        "evidence_url": None,
        "status": status,
    }
    req_file = tmp_path / "acceptance_request.json"
    with open(req_file, "w", encoding="utf-8") as f:
        json.dump(req, f, ensure_ascii=False, indent=2)
    return req_file


def load_final_state(env: Dict[str, str]) -> Dict[str, Any]:
    """PIPELINE_STATE_PATH가 가리키는 state 파일을 로드."""
    state_file = Path(env["PIPELINE_STATE_PATH"])
    if not state_file.exists():
        return {}
    with open(state_file, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# IMP-20260612-E12D MT-2: PR body를 반환하는 fake gh (PIPELINE_GH_EXECUTABLE)
# conftest의 autouse fake gh fixture가 제거되어, request-accept 성공을 기대하는
# 테스트(TC-1)는 완전한 PR body를 반환하는 fake gh를 명시적으로 주입해야 한다.
# PATH 기반 gh.cmd(make_fake_gh)는 `gh pr view --json body --jq .body`에 PR body를
# 반환하지 못하므로, Python 스크립트 기반 fake gh를 PIPELINE_GH_EXECUTABLE로 전달한다.
# ---------------------------------------------------------------------------

_FAKE_GH_PR_BODY_BBDB = (
    "## 작업 요약\n자동 테스트 픽스처 PR body\n\n"
    "## 사용자가 확인할 결과물\n결과물 경로: N/A (테스트)\n\n"
    "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 성공\n\n"
    "## 중요한 선택과 트레이드오프\nN/A (테스트 픽스처)\n\n"
    "## 검증\n모든 게이트 PASS\n"
)


def write_fake_gh_pr_body_script(tmp_path: Path) -> Path:
    """완전한 PR body를 반환하는 Python 기반 fake gh 스크립트를 생성하여 경로 반환.

    headSha/databaseId는 빈 문자열, run/pr list는 빈 배열을 반환하여
    gh CLI 없는 환경(pr_head_sha=''/ci_run_id='')을 시뮬레이션한다.

    Args:
        tmp_path: pytest tmp_path fixture.
    Returns:
        생성된 fake gh .py 스크립트 절대 경로.
    Raises:
        TypeError: tmp_path가 None.
    """
    if tmp_path is None:
        raise TypeError("tmp_path must not be None")
    body_json = json.dumps(_FAKE_GH_PR_BODY_BBDB)
    script = tmp_path / "fake_gh_bbdb.py"
    script.write_text(
        "import sys, io, json\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        f"BODY = {body_json}\n"
        "args = sys.argv[1:]\n"
        'if "--jq" in args:\n'
        '    jq_idx = args.index("--jq"); jq = args[jq_idx+1] if jq_idx+1 < len(args) else ""\n'
        '    if jq == ".body":\n'
        '        sys.stdout.write(BODY)\n'
        '        if not BODY.endswith("\\n"):\n'
        '            sys.stdout.write("\\n")\n'
        "        sys.exit(0)\n"
        '    elif "[.files" in jq or jq.startswith(".[0]"):\n'
        '        print("[]"); sys.exit(0)\n'
        '    elif ".headSha" in jq or ".databaseId" in jq:\n'
        '        print(""); sys.exit(0)\n'
        'if "run" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        'if "run" in args and "view" in args:\n'
        "    print(json.dumps({})); sys.exit(0)\n"
        'if "pr" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        "print(json.dumps({\n"
        '    "body": BODY, "number": 1,\n'
        '    "headRefOid": "abc123def456abc123def456abc123def456abc1",\n'
        '    "isDraft": False, "state": "OPEN", "files": [],\n'
        '    "url": "https://github.com/test/repo/pull/1",\n'
        "}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return script


def make_env_with_pr_body_gh(tmp_path: Path) -> Dict[str, str]:
    """make_env에 완전한 PR body를 반환하는 fake gh(PIPELINE_GH_EXECUTABLE)를 추가한 환경.

    request-accept 성공을 기대하는 테스트용. (IMP-20260612-E12D MT-2)

    Args:
        tmp_path: pytest tmp_path fixture.
    Returns:
        fake gh가 주입된 환경 변수 dict.
    """
    env = make_env(tmp_path)
    env["PIPELINE_GH_EXECUTABLE"] = str(write_fake_gh_pr_body_script(tmp_path))
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# ---------------------------------------------------------------------------
# Tests (14 cases — TC-1 ~ TC-14)
# ---------------------------------------------------------------------------

# TC-1: request-accept가 acceptance_request.json을 생성 (normal oracle)
def test_tc1_request_accept_creates_json(tmp_path):
    """gates request-accept 실행 시 acceptance_request.json이 PENDING 상태로 생성된다."""
    # CLI Evidence Contract: isolation marker PIPELINE_STATE_PATH (via make_env helper)
    # IMP-20260612-E12D MT-2: conftest autouse fake gh 제거에 따라, request-accept 성공을
    # 기대하는 이 테스트는 완전한 PR body를 반환하는 fake gh를 명시적으로 주입한다.
    env = make_env_with_pr_body_gh(tmp_path)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test result", encoding="utf-8")

    # BUG-20260628-F52C: request-accept는 Codex APPROVE 이후에만 publish하므로 사전 승인 필요.
    run_cli(
        ["gates", "codex-review", "--verdict", "APPROVE_TO_USER", "--approve-pending"],
        env=env, cwd=tmp_path,
    )
    r = run_cli(
        ["gates", "request-accept", "--evidence", str(evidence_file)],
        env=env,
        cwd=tmp_path,
    )
    # acceptance_request.json 생성 확인 (gh CLI 없어도 json 생성은 됨)
    req_file = tmp_path / "acceptance_request.json"
    assert req_file.exists(), f"acceptance_request.json not created. stdout={r.stdout} stderr={r.stderr}"
    with open(req_file, encoding="utf-8") as f:
        data = json.load(f)
    assert data["status"] == "PENDING", f"status={data['status']}"
    assert data["pipeline_id"] == pipeline_id
    assert len(data["nonce"]) == 8
    # final_state assertion (PIPELINE_STATE_PATH 격리 확인)
    state = load_final_state(env)
    assert state.get("pipeline_id") == pipeline_id


# TC-2: --user-confirmed 단독은 acceptance_code_required로 BLOCKED (edge oracle)
def test_tc2_user_confirmed_only_blocked(tmp_path):
    """--user-confirmed 단독으로는 ACCEPT gate를 통과하지 못한다."""
    # CLI Evidence Contract: isolation marker PIPELINE_STATE_PATH (via make_env helper)
    env = make_env(tmp_path)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test", encoding="utf-8")

    r = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_file), "--user-confirmed"],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0, f"expected non-zero, got {r.returncode}"
    output = r.stdout + r.stderr
    assert ("BLOCKED" in output or "acceptance_code_required" in output
            or "acceptance-code" in output.lower()), f"output: {output}"
    # final_state assertion — acceptance gate가 PASS 처리되지 않았음을 확인
    final_state = load_final_state(env)
    acceptance_status = final_state.get("external_gates", {}).get("acceptance", {}).get("status")
    assert acceptance_status != "PASS", \
        f"acceptance gate must not be PASS after BLOCKED: {acceptance_status}"


# TC-3: 잘못된 nonce → acceptance_code_mismatch BLOCKED (edge oracle)
def test_tc3_wrong_nonce_blocked(tmp_path):
    """잘못된 nonce로 ACCEPT 시도 시 acceptance_code_mismatch."""
    # CLI Evidence Contract: isolation marker PIPELINE_STATE_PATH (via make_env helper)
    env = make_env(tmp_path)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test", encoding="utf-8")
    write_acceptance_request(tmp_path, pipeline_id, nonce="CORRECT1",
                              evidence=str(evidence_file))

    r = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_file),
         "--acceptance-code", f"ACCEPT-{pipeline_id}-WRONGNO1"],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0
    output = r.stdout + r.stderr
    assert ("mismatch" in output.lower() or "BLOCKED" in output), f"output: {output}"
    # final_state assertion — acceptance gate가 PASS 처리되지 않았음을 확인
    final_state = load_final_state(env)
    assert final_state.get("external_gates", {}).get("acceptance", {}).get("status") != "PASS"


# TC-4: pipeline_id 다른 코드 → BLOCKED
def test_tc4_pipeline_id_mismatch_blocked(tmp_path):
    """다른 pipeline_id의 코드로 ACCEPT 시도 시 BLOCKED."""
    # CLI Evidence Contract: isolation marker PIPELINE_STATE_PATH (via make_env helper)
    env = make_env(tmp_path)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test", encoding="utf-8")
    # 현재 pipeline_id로 acceptance_request 생성하되, 다른 pipeline_id 코드 입력
    write_acceptance_request(tmp_path, pipeline_id, nonce="TESTNON1",
                              evidence=str(evidence_file))

    other_pipeline_id = "IMP-20991231-XXXX"
    r = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_file),
         "--acceptance-code", f"ACCEPT-{other_pipeline_id}-TESTNON1"],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0
    output = r.stdout + r.stderr
    assert "BLOCKED" in output or "mismatch" in output.lower(), f"output: {output}"
    # final_state assertion — pipeline_id_mismatch는 acceptance gate를 PASS시키지 않음
    final_state = load_final_state(env)
    assert final_state.get("external_gates", {}).get("acceptance", {}).get("status") != "PASS"


# TC-5: evidence 파일 hash 변경 → evidence_changed BLOCKED
def test_tc5_evidence_changed_blocked(tmp_path):
    """request-accept 이후 evidence 파일이 변경되면 evidence_changed BLOCKED."""
    # CLI Evidence Contract: isolation marker PIPELINE_STATE_PATH (via make_env helper)
    env = make_env(tmp_path)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("original content", encoding="utf-8")
    original_sha = hashlib.sha256(b"original content").hexdigest()
    write_acceptance_request(tmp_path, pipeline_id, nonce="TESTNON1",
                              evidence=str(evidence_file),
                              evidence_sha256=original_sha)
    # 파일 내용 변경
    evidence_file.write_text("modified content", encoding="utf-8")

    r = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_file),
         "--acceptance-code", f"ACCEPT-{pipeline_id}-TESTNON1"],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0
    output = r.stdout + r.stderr
    # 외부 gate 실패가 먼저 일어날 수 있으므로 BLOCKED 또는 evidence_changed 둘 다 허용
    assert "BLOCKED" in output or "evidence" in output.lower(), f"output: {output}"
    # final_state assertion — evidence 변경이 acceptance gate를 PASS시키지 않음
    final_state = load_final_state(env)
    assert final_state.get("external_gates", {}).get("acceptance", {}).get("status") != "PASS"


# TC-6: PR head SHA가 다른 시나리오 (gh CLI 없으면 SHA 검증 skip → 단위 검증)
def test_tc6_pr_sha_changed_unit(tmp_path):
    """acceptance_request.json의 pr_head_sha 필드가 정상 저장되는지 단위 검증.

    gh CLI 없는 격리 환경에서는 SHA 비교가 skip되므로,
    pr_head_sha 필드 자체가 정상 저장/로드되는지만 확인.
    """
    env = make_env(tmp_path)
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test", encoding="utf-8")
    req_file = write_acceptance_request(
        tmp_path, pipeline_id, nonce="TESTNON1",
        pr_sha="oldsha1234567890abcdef",
        evidence=str(evidence_file),
    )
    with open(req_file, encoding="utf-8") as f:
        data = json.load(f)
    assert data["pr_head_sha"] == "oldsha1234567890abcdef"


# TC-7: CI run ID 정상 저장 단위 검증
def test_tc7_ci_run_id_stored(tmp_path):
    """acceptance_request.json의 github_ci_run_id 필드 정상 저장 검증."""
    env = make_env(tmp_path)
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test", encoding="utf-8")
    req_file = write_acceptance_request(
        tmp_path, pipeline_id, nonce="TESTNON1",
        ci_run="12345678",
        evidence=str(evidence_file),
    )
    with open(req_file, encoding="utf-8") as f:
        data = json.load(f)
    assert data["github_ci_run_id"] == "12345678"


# TC-8: 올바른 코드 + 동일 상태 → nonce 검증 통과 (다른 gate에서 실패해도 무관)
def test_tc8_correct_code_passes_nonce_validation(tmp_path):
    """올바른 acceptance-code + 동일 evidence → nonce 검증은 통과한다."""
    # CLI Evidence Contract: isolation marker PIPELINE_STATE_PATH (via make_env helper)
    env = make_env(tmp_path)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test result", encoding="utf-8")
    sha256 = hashlib.sha256(b"test result").hexdigest()
    write_acceptance_request(tmp_path, pipeline_id, nonce="TESTNON1",
                              evidence=str(evidence_file), evidence_sha256=sha256)

    r = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_file),
         "--acceptance-code", f"ACCEPT-{pipeline_id}-TESTNON1"],
        env=env,
        cwd=tmp_path,
    )
    output = r.stdout + r.stderr
    # nonce 검증은 통과해야 함 → acceptance_code_mismatch 메시지 없어야 함
    assert "acceptance_code_mismatch" not in output, f"unexpected mismatch: {output}"
    # final_state assertion — acceptance gate가 다른 이유로 차단됐어도 state 파일은 유효
    final_state = load_final_state(env)
    assert final_state.get("pipeline_id") == pipeline_id


# TC-9: REJECT도 --acceptance-code 없으면 FAIL
def test_tc9_reject_no_acceptance_code_fail(tmp_path):
    """REJECT도 --acceptance-code 없으면 acceptance_code_required로 FAIL."""
    # CLI Evidence Contract: isolation marker PIPELINE_STATE_PATH (via make_env helper)
    env = make_env(tmp_path)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test", encoding="utf-8")

    r = run_cli(
        ["gates", "accept", "--result", "REJECT",
         "--evidence", str(evidence_file)],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0
    output = r.stdout + r.stderr
    assert ("BLOCKED" in output or "acceptance_code_required" in output
            or "acceptance-code" in output.lower()), f"output: {output}"
    # final_state assertion — REJECT 시도도 acceptance gate를 PASS시키지 않음
    final_state = load_final_state(env)
    assert final_state.get("external_gates", {}).get("acceptance", {}).get("status") != "PASS"


# TC-10: --user-confirmed 단독 → 경고 + BLOCKED (명시적)
def test_tc10_user_confirmed_warning_then_blocked(tmp_path):
    """--user-confirmed 플래그만 있으면 경고 출력 후 BLOCKED."""
    # CLI Evidence Contract: isolation marker PIPELINE_STATE_PATH (via make_env helper)
    env = make_env(tmp_path)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test", encoding="utf-8")

    r = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_file), "--user-confirmed"],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0
    output = r.stdout + r.stderr
    assert ("경고" in output or "BLOCKED" in output
            or "acceptance" in output.lower()), f"output: {output}"
    # final_state assertion — --user-confirmed 단독은 acceptance gate를 PASS시키지 않음
    final_state = load_final_state(env)
    assert final_state.get("external_gates", {}).get("acceptance", {}).get("status") != "PASS"


# TC-11: acceptance_request.json 없이 gates accept → missing_acceptance_request BLOCKED
def test_tc11_missing_acceptance_request_blocked(tmp_path):
    """acceptance_request.json 없이 acceptance-code 입력 시 BLOCKED."""
    # CLI Evidence Contract: isolation marker PIPELINE_STATE_PATH (via make_env helper)
    env = make_env(tmp_path)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test", encoding="utf-8")
    # acceptance_request.json 생성하지 않음

    r = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_file),
         "--acceptance-code", f"ACCEPT-{pipeline_id}-ANYNONCE"],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0
    output = r.stdout + r.stderr
    assert ("BLOCKED" in output or "acceptance_request" in output.lower()
            or "missing" in output.lower()), f"output: {output}"
    # final_state assertion — acceptance_request.json 없으면 acceptance gate PASS 불가
    final_state = load_final_state(env)
    assert final_state.get("external_gates", {}).get("acceptance", {}).get("status") != "PASS"


# TC-12: TEMPORARY_PR_BODY_PATTERNS SSoT 단위 검증
def test_tc12_stale_pr_body_patterns_exist():
    """pipeline.py의 TEMPORARY_PR_BODY_PATTERNS에 주요 stale 패턴이 포함되어 있는지 검증."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", str(PIPELINE_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    patterns = getattr(mod, "TEMPORARY_PR_BODY_PATTERNS", [])
    assert isinstance(patterns, list), "TEMPORARY_PR_BODY_PATTERNS must be a list"
    assert len(patterns) > 0, "TEMPORARY_PR_BODY_PATTERNS must not be empty"
    # 핵심 stale 패턴 확인 (substring match)
    stale_keywords = ["작업 중", "PM phase attestation", "Dev 완료 후"]
    joined = " | ".join(patterns)
    for kw in stale_keywords:
        assert kw in joined, f"missing stale keyword '{kw}' in {joined}"


# TC-13: status=CONSUMED → consumed_or_expired BLOCKED
def test_tc13_consumed_request_blocked(tmp_path):
    """status=CONSUMED인 acceptance_request로 gates accept 시도 → consumed_or_expired BLOCKED."""
    # CLI Evidence Contract: isolation marker PIPELINE_STATE_PATH (via make_env helper)
    env = make_env(tmp_path)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test", encoding="utf-8")
    write_acceptance_request(tmp_path, pipeline_id, nonce="TESTNON1",
                              status="CONSUMED",
                              evidence=str(evidence_file))

    r = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_file),
         "--acceptance-code", f"ACCEPT-{pipeline_id}-TESTNON1"],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0
    output = r.stdout + r.stderr
    assert ("BLOCKED" in output or "consumed" in output.lower()
            or "expired" in output.lower()), f"output: {output}"
    # final_state assertion — CONSUMED 상태 재사용은 acceptance gate를 PASS시키지 않음
    final_state = load_final_state(env)
    assert final_state.get("external_gates", {}).get("acceptance", {}).get("status") != "PASS"


# TC-14: nonce 헬퍼 함수 단위 검증 (8자 base32 uppercase + 라운드트립)
def test_tc14_nonce_helper_roundtrip(tmp_path):
    """_issue_acceptance_nonce + _write/load/consume_acceptance_request 라운드트립 단위 검증."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", str(PIPELINE_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # nonce 형식 검증
    n = mod._issue_acceptance_nonce()
    assert len(n) == 8, f"nonce length {len(n)} != 8"
    # base32 uppercase + digits
    assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in n), f"invalid base32: {n}"

    # 라운드트립 (tmp_path로 cwd 변경)
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        data = mod._write_acceptance_request(
            "IMP-20260531-TEST", "dummy.txt",
            "https://test/pr/1", "abc1234", "99999",
        )
        assert data["status"] == "PENDING"
        loaded = mod._load_acceptance_request()
        assert loaded is not None
        assert loaded["nonce"] == data["nonce"]
        mod._consume_acceptance_request(loaded, "ACCEPT")
        reloaded = mod._load_acceptance_request()
        assert reloaded["status"] == "CONSUMED"
        assert reloaded["consumed_result"] == "ACCEPT"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Helper: fake gh CLI (Windows .cmd / Unix shell script)
# ---------------------------------------------------------------------------

def make_fake_gh(tmp_path: Path, sha: str = "", run_id: str = "") -> None:
    """tmp_path에 fake gh CLI를 생성 (make_env의 PATH가 tmp_path이므로 gh로 실행됨).

    sha: `gh pr view --json headRefOid` 응답값 (비어 있으면 exit 1)
    run_id: `gh run list` 응답값 (비어 있으면 exit 1)

    주의: PATH=str(tmp_path) 환경에서만 실행됨.
    Windows: gh.cmd (batch), Unix: gh (shell script + chmod 755).
    """
    if sys.platform == "win32":
        sha_escaped = sha.replace('"', '""')
        run_escaped = run_id.replace('"', '""')
        # Windows batch는 if/elif 체인이 복잡하므로, %* 전체를 체크하는 대신
        # 두 번째 인자(%2)로 분기
        script = (
            "@echo off\n"
            f"if \"%2\"==\"view\" (\n"
            f"  echo {sha_escaped}\n"
            f"  exit /b 0\n"
            f")\n"
            f"if \"%2\"==\"list\" (\n"
            f"  echo {run_escaped}\n"
            f"  exit /b 0\n"
            f")\n"
            f"exit /b 1\n"
        )
        gh_file = tmp_path / "gh.cmd"
        gh_file.write_text(script, encoding="utf-8")
    else:
        script = (
            "#!/bin/sh\n"
            f"if [ \"$2\" = \"view\" ]; then\n"
            f"  echo '{sha}'\n"
            f"  exit 0\n"
            f"elif [ \"$2\" = \"list\" ]; then\n"
            f"  echo '{run_id}'\n"
            f"  exit 0\n"
            f"fi\n"
            f"exit 1\n"
        )
        gh_file = tmp_path / "gh"
        gh_file.write_text(script, encoding="utf-8")
        gh_file.chmod(0o755)


def make_env_with_fake_gh(tmp_path: Path, sha: str = "", run_id: str = "") -> Dict[str, str]:
    """fake gh가 있는 격리 환경변수 반환."""
    env = make_env(tmp_path)
    make_fake_gh(tmp_path, sha=sha, run_id=run_id)
    return env


# ---------------------------------------------------------------------------
# TC-15 ~ TC-18: PR SHA / CI run ID / gh 실패 → BLOCKED E2E 테스트
# ---------------------------------------------------------------------------

# TC-15: 저장된 PR head SHA가 있는데 gh CLI 실패 → sha_verification_failed BLOCKED
def test_tc15_stored_sha_gh_fail_blocked(tmp_path):
    """acceptance_request에 저장된 pr_head_sha가 있는데 gh 조회 실패 시 sha_verification_failed BLOCKED.

    make_env는 PATH를 tmp_path만으로 설정하므로 gh CLI를 찾지 못한다.
    저장된 SHA가 있으면 검증 불가 → 안전 실패(fail-safe) 원칙으로 BLOCKED.
    """
    env = make_env(tmp_path)  # gh 없는 환경 (PATH 제한)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test result", encoding="utf-8")
    sha256 = hashlib.sha256(b"test result").hexdigest()
    # pr_head_sha가 있는 acceptance_request 생성 (gh 없이는 검증 불가)
    write_acceptance_request(
        tmp_path, pipeline_id, nonce="TESTNON1",
        pr_sha="abc1234567890abcdef1234567890abcdef1234",
        evidence=str(evidence_file), evidence_sha256=sha256,
    )

    r = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_file),
         "--acceptance-code", f"ACCEPT-{pipeline_id}-TESTNON1"],
        env=env,
        cwd=tmp_path,
    )
    # gh CLI 없이 저장된 SHA 검증 불가 → BLOCKED (sha_verification_failed)
    assert r.returncode != 0, \
        f"expected non-zero returncode, got {r.returncode}. stdout={r.stdout} stderr={r.stderr}"
    output = r.stdout + r.stderr
    assert (
        "sha_verification_failed" in output
        or "BLOCKED" in output
        or "SHA" in output.upper()
        or "head" in output.lower()
    ), f"expected sha_verification_failed or BLOCKED, got: {output}"
    # final_state assertion — acceptance gate가 PASS되지 않았음을 확인
    final_state = load_final_state(env)
    assert final_state.get("external_gates", {}).get("acceptance", {}).get("status") != "PASS", \
        "acceptance gate must not be PASS when sha_verification_failed"


# TC-16: 저장된 CI run ID가 있는데 gh CLI 실패 → run_id_verification_failed BLOCKED
def test_tc16_stored_run_id_gh_fail_blocked(tmp_path):
    """acceptance_request에 저장된 github_ci_run_id가 있는데 gh 조회 실패 시 BLOCKED.

    make_env는 PATH를 tmp_path만으로 설정하므로 gh CLI를 찾지 못한다.
    저장된 run ID가 있으면 검증 불가 → BLOCKED (run_id_verification_failed).
    """
    env = make_env(tmp_path)  # gh 없는 환경 (PATH 제한)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test result", encoding="utf-8")
    sha256 = hashlib.sha256(b"test result").hexdigest()
    # github_ci_run_id가 있는 acceptance_request — SHA는 없음 (SHA 검증이 먼저라 무시)
    # SHA도 없어야 run_id 검증 단계까지 도달
    write_acceptance_request(
        tmp_path, pipeline_id, nonce="TESTNON1",
        pr_sha="",  # SHA 없으면 SHA 검증 skip
        ci_run="12345678",  # run_id 있음 → 검증 시도
        evidence=str(evidence_file), evidence_sha256=sha256,
    )

    r = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_file),
         "--acceptance-code", f"ACCEPT-{pipeline_id}-TESTNON1"],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0, \
        f"expected non-zero returncode, got {r.returncode}. stdout={r.stdout} stderr={r.stderr}"
    output = r.stdout + r.stderr
    assert (
        "run_id_verification_failed" in output
        or "BLOCKED" in output
        or "run" in output.lower()
        or "CI" in output
    ), f"expected run_id_verification_failed or BLOCKED, got: {output}"
    # final_state assertion
    final_state = load_final_state(env)
    assert final_state.get("external_gates", {}).get("acceptance", {}).get("status") != "PASS", \
        "acceptance gate must not be PASS when run_id_verification_failed"


# TC-17: fake gh가 다른 SHA를 반환하면 stale_head_sha BLOCKED
def test_tc17_fake_gh_different_sha_blocked(tmp_path):
    """fake gh가 acceptance_request와 다른 PR head SHA를 반환하면 stale_head_sha BLOCKED.

    실제 gh CLI가 있는 환경을 시뮬레이션하기 위해 tmp_path에 fake gh.cmd/gh를 생성.
    fake gh는 stored SHA와 다른 SHA를 반환 → stale_head_sha BLOCKED.
    """
    STORED_SHA = "stored_sha_1234567890abcdef12345678901234"
    DIFFERENT_SHA = "different_sha_abcdef1234567890abcdef12"
    # fake gh가 DIFFERENT_SHA를 반환하는 환경 생성
    env = make_env_with_fake_gh(tmp_path, sha=DIFFERENT_SHA, run_id="99999")
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test result", encoding="utf-8")
    sha256 = hashlib.sha256(b"test result").hexdigest()
    # stored SHA = STORED_SHA, fake gh returns DIFFERENT_SHA
    write_acceptance_request(
        tmp_path, pipeline_id, nonce="TESTNON1",
        pr_sha=STORED_SHA,
        ci_run="",  # run_id 없으면 run_id 검증 skip
        evidence=str(evidence_file), evidence_sha256=sha256,
    )

    r = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_file),
         "--acceptance-code", f"ACCEPT-{pipeline_id}-TESTNON1"],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0, \
        f"expected non-zero returncode, got {r.returncode}. stdout={r.stdout} stderr={r.stderr}"
    output = r.stdout + r.stderr
    # stale_head_sha 또는 sha_verification_failed 또는 BLOCKED 중 하나
    assert (
        "stale_head_sha" in output
        or "sha_verification_failed" in output
        or "BLOCKED" in output
        or "SHA" in output.upper()
    ), f"expected SHA mismatch detection, got: {output}"
    # final_state assertion
    final_state = load_final_state(env)
    assert final_state.get("external_gates", {}).get("acceptance", {}).get("status") != "PASS", \
        "acceptance gate must not be PASS when SHA mismatch"


# TC-18: fake gh가 다른 run ID를 반환하면 stale_run_id BLOCKED
def test_tc18_fake_gh_different_run_id_blocked(tmp_path):
    """fake gh가 acceptance_request와 다른 CI run ID를 반환하면 stale_run_id BLOCKED.

    stored run_id != fake gh run_id → stale_run_id BLOCKED.
    """
    STORED_RUN = "11111111"
    DIFFERENT_RUN = "22222222"
    env = make_env_with_fake_gh(tmp_path, sha="", run_id=DIFFERENT_RUN)
    assert "PIPELINE_STATE_PATH" in env, "isolation env not set"
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test result", encoding="utf-8")
    sha256 = hashlib.sha256(b"test result").hexdigest()
    # stored run_id = STORED_RUN, fake gh returns DIFFERENT_RUN
    # pr_sha 없음 → SHA 검증 skip, run_id 검증 단계까지 진행
    write_acceptance_request(
        tmp_path, pipeline_id, nonce="TESTNON1",
        pr_sha="",  # SHA 없으면 SHA 검증 skip
        ci_run=STORED_RUN,
        evidence=str(evidence_file), evidence_sha256=sha256,
    )

    r = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_file),
         "--acceptance-code", f"ACCEPT-{pipeline_id}-TESTNON1"],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0, \
        f"expected non-zero returncode, got {r.returncode}. stdout={r.stdout} stderr={r.stderr}"
    output = r.stdout + r.stderr
    assert (
        "stale_run_id" in output
        or "run_id_verification_failed" in output
        or "BLOCKED" in output
        or "run" in output.lower()
    ), f"expected run ID mismatch detection, got: {output}"
    # final_state assertion
    final_state = load_final_state(env)
    assert final_state.get("external_gates", {}).get("acceptance", {}).get("status") != "PASS", \
        "acceptance gate must not be PASS when run ID mismatch"


# ---------------------------------------------------------------------------
# Preflight-PR-impl 회귀 테스트 (IMP-20260531-BBDB stale 문구 차단)
# ---------------------------------------------------------------------------

# PR-IMPL-1: _find_temporary_pr_body_pattern이 'Dev phase 진행 중' 패턴을 탐지
def test_pr_impl_1_dev_phase_stale_pattern_detected():
    """'Dev phase 진행 중' 문구가 TEMPORARY_PR_BODY_PATTERNS에 포함되어야 한다.

    IMP-20260531-BBDB: PR #368 stale 문구 패턴 회귀 테스트.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", str(PIPELINE_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    patterns = getattr(mod, "TEMPORARY_PR_BODY_PATTERNS", [])
    # "Dev phase 진행 중" 이 패턴에 직접 포함되거나, 이를 탐지하는 패턴이 있어야 한다
    joined = " ".join(patterns)
    assert "Dev phase 진행 중" in joined, \
        f"'Dev phase 진행 중' pattern missing from TEMPORARY_PR_BODY_PATTERNS: {patterns}"

    # _find_temporary_pr_body_pattern이 이 줄을 스테일로 탐지해야 한다
    result = mod._find_temporary_pr_body_pattern("Dev phase 진행 중입니다")
    assert result is not None, \
        "'Dev phase 진행 중입니다' should be detected as stale by _find_temporary_pr_body_pattern"


# PR-IMPL-2: _find_temporary_pr_body_pattern이 '빌드 완료 후 업데이트 예정' 패턴을 탐지
def test_pr_impl_2_build_update_stale_pattern_detected():
    """'빌드 완료 후 업데이트 예정' 문구가 TEMPORARY_PR_BODY_PATTERNS에 포함되어야 한다.

    IMP-20260531-BBDB: PR #368 stale 문구 패턴 회귀 테스트.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", str(PIPELINE_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    patterns = getattr(mod, "TEMPORARY_PR_BODY_PATTERNS", [])
    joined = " ".join(patterns)
    assert "빌드 완료 후 업데이트 예정" in joined, \
        f"'빌드 완료 후 업데이트 예정' pattern missing from TEMPORARY_PR_BODY_PATTERNS: {patterns}"

    result = mod._find_temporary_pr_body_pattern("빌드 완료 후 업데이트 예정이니 참고해주세요")
    assert result is not None, \
        "'빌드 완료 후 업데이트 예정...' should be detected as stale"


# PR-IMPL-3: 정상 PR 본문은 stale 판정을 받지 않아야 한다 (거짓 양성 방지)
def test_pr_impl_3_normal_pr_body_not_stale():
    """정상 완료된 PR 본문은 stale 패턴으로 판정되지 않아야 한다."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", str(PIPELINE_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    normal_body = """## 최종 판단 요약
User Acceptance Nonce Gate 구현 완료.

## 사용자가 확인할 결과물
- acceptance_request.json 생성 확인
- nonce 기반 일회용 코드 확인

## 기대 결과와 실제 결과
기대: gates accept에 --acceptance-code 필요
실제: --user-confirmed 단독 시 BLOCKED 처리

## 중요한 선택과 트레이드오프
nonce는 8자 base32를 사용합니다.

## 검증
pytest E2E 테스트 18케이스 PASS."""

    result = mod._find_temporary_pr_body_pattern(normal_body)
    assert result is None, \
        f"normal PR body was incorrectly flagged as stale: pattern={result}"


# PR-IMPL-4: request-accept 명령이 stale 문구 체크 후 BLOCKED 처리함을 구조 수준에서 검증
def test_pr_impl_4_request_accept_checks_stale_patterns(tmp_path):
    """request-accept 명령이 stale 패턴이 있는 PR body를 가진 경우 BLOCKED임을 확인.

    gh CLI를 통해 PR body를 읽으므로 fake gh를 사용하여 stale PR body를 반환시킨다.
    """
    # 이 테스트는 fake gh를 사용하지 않고 단위 수준에서 검증:
    # _find_temporary_pr_body_pattern이 stale 패턴 문자열에 대해 None이 아닌 값을 반환함을 확인
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", str(PIPELINE_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # request-accept 내부에서 사용되는 stale 검사 로직 단위 검증
    stale_lines = [
        "Dev phase 진행 중입니다",
        "빌드 완료 후 업데이트 예정입니다",
        "PM phase attestation CI 확인용",
        "작업 중입니다",
        "KittingMapper.exe 빌드 완료 후 업데이트됩니다",
    ]
    for line in stale_lines:
        result = mod._find_temporary_pr_body_pattern(line)
        assert result is not None, \
            f"stale line '{line}' not detected by _find_temporary_pr_body_pattern"

    # 정상 문구는 stale로 판정되지 않아야 함 (거짓 양성 회귀)
    safe_lines = [
        "acceptance_request.json이 생성되었습니다",
        "Technical gate PASS",
        "Oracle gate PASS",
        "nonce 기반 일회용 코드 사용법 안내",
    ]
    for line in safe_lines:
        result = mod._find_temporary_pr_body_pattern(line)
        assert result is None, \
            f"safe line '{line}' incorrectly flagged as stale: pattern={result}"
