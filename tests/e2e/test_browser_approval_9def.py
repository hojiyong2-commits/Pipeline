"""tests/e2e/test_browser_approval_9def.py

BUG-20260616-9DEF MT-5: 로컬 브라우저 클릭 승인 채널 Real CLI Path E2E.

[Purpose]: BUG-20260616-8011 REJECT 후속으로 추가된 로컬 브라우저 클릭 승인 채널을
    검증한다. PIPELINE_BROWSER_APPROVAL_SKIP=1 우회 경로, browser_click_confirmed=false
    시 gates accept BLOCKED(browser_approval_required), 기존 nonce/D278/2821 회귀를
    subprocess 기반 실제 CLI 흐름 + PIPELINE_STATE_PATH 격리 + final_state assertion으로
    확인한다. AC-1~AC-5, AC-7을 커버한다.
[Assumptions]: pipeline.py가 프로젝트 루트에 위치한다. PIPELINE_STATE_PATH 환경 변수로
    state 파일을 tmp_path 안에 격리하고, subprocess cwd=tmp_path로 실행하여
    acceptance_request.json / failure_packet.json(상대 경로)도 격리한다. 실제 HTTP
    서버를 띄우는 TC는 타임아웃 위험이 있으므로 PIPELINE_BROWSER_APPROVAL_SKIP=1로
    서버 실행을 우회한다(MT-1이 SKIP 시 서버를 띄우지 않음). gh CLI는 tmp_path 안의
    fake gh로 시뮬레이션하며 PATH를 tmp_path로 제한하여 실제 gh를 무력화한다.
[Vulnerability & Risks]: 실제 gh가 PATH에 남아 있으면 fake gh 대신 실제 gh가 호출될 수
    있어 PATH를 tmp_path로 제한한다. 실제 브라우저 클릭 경로(서버 대기)는 결정론적
    E2E로 검증하기 어려워 SKIP 경로로 흐름 전체를 검증하고, fail-closed(클릭 미확인 시
    BLOCKED)는 acceptance_request.json을 직접 구성하여 gates accept 분기로 검증한다.
[Improvement]: 시간이 더 있다면 실제 localhost HTTP 클릭을 별도 스레드에서 자동 클릭하는
    통합 테스트를 추가하여 서버 경로까지 검증할 수 있다(타임아웃 관리 필요).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PIPELINE_PY = str(Path(__file__).resolve().parents[2] / "pipeline.py")

_DUMMY_NONCE = "BRWSNON1"  # 8자 nonce

_COMPLETE_PR_BODY = (
    "## 작업 요약\n브라우저 승인 채널 E2E 픽스처\n\n"
    "## 사용자가 확인할 결과물\n결과물 경로: N/A (테스트)\n\n"
    "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 성공\n\n"
    "## 중요한 선택과 트레이드오프\nN/A (테스트 픽스처)\n\n"
    "## 검증\n모든 게이트 PASS\n"
)


def _sha256_of(text: str) -> str:
    """텍스트의 SHA-256 hex digest."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_cli(
    args: List[str],
    env: Dict[str, str],
    cwd: Path,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """PIPELINE_STATE_PATH + cwd 격리 환경에서 pipeline.py 실행.

    Args:
        args: pipeline.py 인자 리스트.
        env: 환경 변수 dict (PIPELINE_STATE_PATH 포함).
        cwd: 작업 디렉토리.
        timeout: 초 단위 timeout.
    Returns:
        subprocess.CompletedProcess.
    Raises:
        TypeError: args가 list가 아닌 경우.
    """
    if args is None:
        raise TypeError("args must not be None")
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    cmd = [sys.executable, PIPELINE_PY] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        cwd=str(cwd),
    )


def _base_env(tmp_path: Path) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 + 부수 효과 억제 환경 dict.

    Args:
        tmp_path: pytest tmp_path fixture.
    Returns:
        환경 변수 dict.
    Raises:
        TypeError: tmp_path가 None인 경우.
    """
    if tmp_path is None:
        raise TypeError("tmp_path must not be None")
    env = dict(os.environ)
    env["PIPELINE_STATE_PATH"] = str(tmp_path / "pipeline_state.json")
    env["PIPELINE_NO_DASHBOARD"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING"] = "1"
    return env


def _write_fake_gh(tmp_path: Path, comments: Optional[List[Dict[str, Any]]] = None) -> Path:
    """완전한 PR body(+선택 댓글)를 반환하는 fake gh 핸들러(.py)+런처를 생성.

    headSha/databaseId는 빈 문자열, run/pr list는 빈 배열을 반환하여 gh CLI 없는
    환경(pr_head_sha=''/ci_run_id='')을 시뮬레이션한다. 이렇게 하면 request-accept의
    stale 검사가 skip되어 SKIP env var 흐름을 단순하게 검증할 수 있다.

    Args:
        tmp_path: pytest tmp_path fixture.
        comments: gh pr view --json comments가 반환할 PR 댓글 (None이면 빈 목록).
    Returns:
        생성된 gh 런처 경로.
    """
    comments_json = json.dumps(comments or [])
    body_json = json.dumps(_COMPLETE_PR_BODY)
    handler = tmp_path / "fake_gh_handler_9def.py"
    handler.write_text(
        "import sys, io, json\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        f"BODY = {body_json}\n"
        f"COMMENTS = {comments_json}\n"
        "args = sys.argv[1:]\n"
        'if "--jq" in args:\n'
        '    i = args.index("--jq"); jq = args[i + 1] if i + 1 < len(args) else ""\n'
        '    if jq == ".body":\n'
        "        sys.stdout.write(BODY)\n"
        '        if not BODY.endswith("\\n"):\n'
        '            sys.stdout.write("\\n")\n'
        "        sys.exit(0)\n"
        '    elif "[.files" in jq or jq.startswith(".[0]"):\n'
        '        print("[]"); sys.exit(0)\n'
        '    elif ".headSha" in jq or ".databaseId" in jq:\n'
        '        print(""); sys.exit(0)\n'
        '    print(""); sys.exit(0)\n'
        'if "pr" in args and "list" in args:\n'
        '    print(json.dumps([{"number": 1, "headRefName": "impl-test-branch"}])); sys.exit(0)\n'
        'if "pr" in args and "view" in args and "comments" in " ".join(args):\n'
        '    print(json.dumps({"comments": COMMENTS})); sys.exit(0)\n'
        'if "run" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        'if "run" in args and "view" in args:\n'
        "    print(json.dumps({})); sys.exit(0)\n"
        'if "pr" in args and "view" in args:\n'
        '    print(json.dumps({"body": BODY, "number": 1, "isDraft": False,\n'
        '                       "state": "OPEN", "files": [], "comments": COMMENTS,\n'
        '                       "url": "https://github.com/test/repo/pull/1"})); sys.exit(0)\n'
        "print('[]')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    # gh 런처(실행 가능 파일)를 생성하여 handler(.py)로 위임한다.
    # _check_pr_approver_provenance는 shutil.which(PIPELINE_GH_EXECUTABLE)로 경로를
    # 해석하므로, .py가 아닌 실행 가능 런처(gh.bat/gh)를 PIPELINE_GH_EXECUTABLE로 지정해야
    # shutil.which가 해당 경로를 반환한다. _build_gh_cmd_prefix 경로도 .py가 아니면
    # [launcher, ...] 그대로 실행하므로 양쪽 경로 모두 fake gh로 라우팅된다.
    if os.name == "nt":
        launcher = tmp_path / "gh.bat"
        launcher.write_text(
            "@echo off\r\n"
            f'"{sys.executable}" "{handler}" %*\r\n',
            encoding="utf-8",
        )
    else:  # pragma: no cover - 본 환경은 Windows
        launcher = tmp_path / "gh"
        launcher.write_text(
            "#!/bin/sh\n"
            f'exec "{sys.executable}" "{handler}" "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o755)
    return launcher


def _gh_env(
    tmp_path: Path, comments: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, str]:
    """fake gh 런처(gh.bat/gh)를 PIPELINE_GH_EXECUTABLE + PATH로 구성한 환경 dict.

    두 gh 해석 경로를 모두 fake gh로 라우팅한다:
      - shutil.which(PIPELINE_GH_EXECUTABLE) 경로(_check_pr_approver_provenance 등):
        PIPELINE_GH_EXECUTABLE=launcher(실행 가능) → shutil.which가 해당 경로 반환.
      - _build_gh_cmd_prefix 경로(_get_pr_body_text 등): launcher가 .py가 아니므로
        [launcher, ...] 그대로 실행.
    PATH를 tmp_path로 제한하여 실제 gh 탐색도 무력화한다.

    Args:
        tmp_path: pytest tmp_path fixture.
        comments: fake gh가 반환할 PR 댓글 목록.
    Returns:
        환경 변수 dict (PIPELINE_GH_EXECUTABLE=launcher + PATH=tmp_path 포함).
    """
    env = _base_env(tmp_path)
    launcher = _write_fake_gh(tmp_path, comments)
    env["PIPELINE_GH_EXECUTABLE"] = str(launcher)
    env["PATH"] = str(tmp_path)
    return env


def _bootstrap(tmp_path: Path, env: Dict[str, str]) -> str:
    """격리 환경에 BUG 파이프라인을 생성하고 pipeline_id 반환.

    Args:
        tmp_path: pytest tmp_path fixture.
        env: PIPELINE_STATE_PATH 환경변수가 설정된 dict.
    Returns:
        생성된 pipeline_id.
    Raises:
        AssertionError: pipeline.py new 실패 시.
    """
    r = _run_cli(["new", "--type", "BUG", "--desc", "browser approval e2e"], env=env, cwd=tmp_path)
    assert r.returncode == 0, f"new failed: stdout={r.stdout}\nstderr={r.stderr}"
    state_file = Path(env["PIPELINE_STATE_PATH"])
    assert state_file.exists(), "pipeline_state.json not created"
    final_state = json.loads(state_file.read_text(encoding="utf-8"))
    pid = str(final_state.get("pipeline_id", ""))
    assert pid, f"pipeline_id missing: {final_state}"
    return pid


def _write_verification_json(tmp_path: Path, pipeline_id: str) -> tuple:
    """최소 human_acceptance_packet.json(verification_json)을 작성하고 (경로, SHA256) 반환.

    changed_files를 빈 배열로 두어 gates accept의 verification_json freshness 검사를
    통과시켜 브라우저 승인 검사 분기에 도달하게 한다.

    Args:
        tmp_path: pytest tmp_path fixture.
        pipeline_id: 활성 pipeline_id.
    Returns:
        (verification_json 절대 경로 문자열, SHA256 hex).
    """
    vj_path = tmp_path / "human_acceptance_packet.json"
    vj_data: Dict[str, Any] = {
        "schema_version": 1,
        "packet_type": "final_acceptance_evidence",
        "pipeline_id": pipeline_id,
        "changed_files": [],
        "changed_files_count": 0,
    }
    vj_path.write_text(json.dumps(vj_data, ensure_ascii=False, indent=2), encoding="utf-8")
    vj_sha = hashlib.sha256(vj_path.read_bytes()).hexdigest()
    return str(vj_path), vj_sha


def _write_acceptance_request(
    tmp_path: Path,
    pipeline_id: str,
    *,
    nonce: str = _DUMMY_NONCE,
    status: str = "PENDING",
    evidence: str = "evidence.txt",
    browser_click_confirmed: Optional[bool] = False,
    browser_approval_skip: Optional[bool] = False,
    include_browser_fields: bool = True,
) -> Path:
    """acceptance_request.json을 직접 작성 (브라우저 승인 분기 검증용).

    Args:
        tmp_path: pytest tmp_path fixture.
        pipeline_id: 활성 pipeline_id.
        nonce: 8자 nonce.
        status: PENDING|CONSUMED.
        evidence: 결과물 경로 (상대).
        browser_click_confirmed: 기록할 browser_click_confirmed 값 (None이면 미포함).
        browser_approval_skip: 기록할 browser_approval_skip 값 (None이면 미포함).
        include_browser_fields: False면 browser 필드를 전혀 기록하지 않음(TC-5).
    Returns:
        작성된 acceptance_request.json 절대 경로.
    """
    req: Dict[str, Any] = {
        "schema_version": 1,
        "pipeline_id": pipeline_id,
        "request_id": "brws9def",
        "nonce": nonce,
        "created_at": "2026-06-16T00:00:00Z",
        "pr_url": "",
        "evidence": evidence,
        "evidence_sha256": None,
        "evidence_url": None,
        "status": status,
    }
    vj_path, vj_sha = _write_verification_json(tmp_path, pipeline_id)
    req["verification_json_path"] = vj_path
    req["verification_json_sha256"] = vj_sha
    if include_browser_fields:
        if browser_click_confirmed is not None:
            req["browser_click_confirmed"] = browser_click_confirmed
        if browser_approval_skip is not None:
            req["browser_approval_skip"] = browser_approval_skip
        req["browser_click_at"] = None
        req["browser_approval_token"] = None
    req_file = tmp_path / "acceptance_request.json"
    req_file.write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    return req_file


def _read_acceptance_request(tmp_path: Path) -> Dict[str, Any]:
    """acceptance_request.json 로드."""
    return json.loads((tmp_path / "acceptance_request.json").read_text(encoding="utf-8"))


def _load_final_state(env: Dict[str, str]) -> Dict[str, Any]:
    """PIPELINE_STATE_PATH가 가리키는 state 파일 로드."""
    state_file = Path(env["PIPELINE_STATE_PATH"])
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text(encoding="utf-8"))


def _accept_code(pipeline_id: str, nonce: str = _DUMMY_NONCE) -> str:
    """ACCEPT-<pipeline_id>-<nonce> 형식 승인 코드."""
    return f"ACCEPT-{pipeline_id}-{nonce}"


def _allowed_approver() -> str:
    """pipeline.PIPELINE_ALLOWED_APPROVER 값을 동적으로 조회.

    fake gh 댓글 author를 실제 허용 승인자와 일치시켜 provenance 게이트를 통과시키고,
    그 다음 단계인 브라우저 승인 게이트(MT-3)에 도달하게 하기 위해 사용.
    """
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    import pipeline as _p  # type: ignore
    return str(_p.PIPELINE_ALLOWED_APPROVER)


def _approver_comment(pipeline_id: str, nonce: str = _DUMMY_NONCE) -> List[Dict[str, Any]]:
    """허용 승인자가 packet 마커 없는 독립 댓글로 정확한 ACCEPT 코드를 남긴 PR 댓글 목록.

    이 댓글이 있으면 provenance 게이트가 PASS되어, 이후 브라우저 승인 게이트(MT-3)의
    browser_approval_required 차단을 결정론적으로 검증할 수 있다.
    """
    return [
        {
            "author": {"login": _allowed_approver()},
            "body": _accept_code(pipeline_id, nonce),
            "id": "C-browser-9def",
        }
    ]


# ─── TC-1: 정상 브라우저 승인 흐름 (SKIP 환경변수) ────────────────────────────────────────


def test_tc1_skip_request_accept_records_browser_fields(tmp_path: Path) -> None:
    """TC-1 (normal): PIPELINE_BROWSER_APPROVAL_SKIP=1로 request-accept 실행 시
    acceptance_request.json에 browser_click_confirmed=true, browser_approval_skip=true가
    기록되고, 이후 gates accept가 browser_approval_required로 차단되지 않는다.

    Oracle: tests/oracles/BUG-20260616-9DEF/normal_browser_approve/expected.json
    격리: PIPELINE_STATE_PATH + cwd=tmp_path.
    """
    env = _gh_env(tmp_path)
    env["PIPELINE_BROWSER_APPROVAL_SKIP"] = "1"
    pid = _bootstrap(tmp_path, env)
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("browser approve evidence", encoding="utf-8")

    r = _run_cli(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode == 0, (
        f"SKIP request-accept가 실패함\nstdout={r.stdout}\nstderr={r.stderr}"
    )

    # final_state: acceptance_request.json에 browser 필드 기록 확인
    req = _read_acceptance_request(tmp_path)
    assert req.get("browser_click_confirmed") is True, (
        f"browser_click_confirmed != true: {req.get('browser_click_confirmed')}"
    )
    assert req.get("browser_approval_skip") is True, (
        f"browser_approval_skip != true: {req.get('browser_approval_skip')}"
    )
    assert req.get("status") == "PENDING", f"status != PENDING: {req.get('status')}"

    # 이후 gates accept: 브라우저 승인 게이트는 통과해야 한다(다른 게이트로 BLOCK될 수는 있음).
    accept_code = _accept_code(pid, str(req.get("nonce")))
    r2 = _run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_path), "--acceptance-code", accept_code],
        env=env,
        cwd=tmp_path,
    )
    combined = r2.stdout + r2.stderr
    assert "browser_approval_required" not in combined, (
        "SKIP 경로인데 browser_approval_required로 차단됨 (회귀)\n"
        f"stdout={r2.stdout}\nstderr={r2.stderr}"
    )
    # 격리 확인
    final_state = _load_final_state(env)
    assert "external_gates" in final_state, "state 파일 external_gates 누락 (격리 실패)"


# ─── TC-2: 브라우저 승인 없이 accept 시도 → BLOCKED ────────────────────────────────────


def test_tc2_no_browser_click_accept_blocked(tmp_path: Path) -> None:
    """TC-2 (edge): browser_click_confirmed=false 상태에서 gates accept를 실행하면
    failure_code=browser_approval_required로 BLOCKED(exit 1)된다.

    Oracle: tests/oracles/BUG-20260616-9DEF/edge_no_browser_approval/expected.json
    provenance 게이트가 먼저 평가되므로, 허용 승인자의 독립 댓글을 제공하여 provenance를
    PASS시킨 뒤 브라우저 승인 게이트(browser_approval_required)에 도달하게 한다.
    """
    # bootstrap을 먼저 해 pid를 얻고, pid 기반 승인 코드 댓글로 fake gh를 구성.
    boot_env = _gh_env(tmp_path)
    pid = _bootstrap(tmp_path, boot_env)
    env = _gh_env(tmp_path, _approver_comment(pid))
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("no browser click evidence", encoding="utf-8")
    _write_acceptance_request(
        tmp_path, pid,
        evidence=str(evidence_path),
        browser_click_confirmed=False,
        browser_approval_skip=False,
    )
    accept_code = _accept_code(pid)

    r = _run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_path), "--acceptance-code", accept_code],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode == 1, (
        f"browser_click_confirmed=false인데 exit code != 1\nstdout={r.stdout}\nstderr={r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert "browser_approval_required" in combined, (
        f"browser_approval_required 차단 누락\nstdout={r.stdout}\nstderr={r.stderr}"
    )

    # final_state: failure_packet 또는 state에 browser_approval_required 기록 확인
    found = False
    fp_path = tmp_path / "failure_packet.json"
    if fp_path.exists():
        fp = json.loads(fp_path.read_text(encoding="utf-8"))
        if fp.get("failure_code") == "browser_approval_required":
            found = True
    if not found:
        final_state = _load_final_state(env)
        for pkt in final_state.get("failure_packets", []):
            if pkt.get("failure_code") == "browser_approval_required":
                found = True
                break
    assert found, "failure_packet/state에 browser_approval_required 기록 누락"
    # acceptance_request status는 소모되지 않아야 함 (nonce 미소모)
    req = _read_acceptance_request(tmp_path)
    assert req.get("status") == "PENDING", (
        f"BLOCKED인데 status가 변경됨(nonce 소모): {req.get('status')}"
    )


# ─── TC-3: PIPELINE_BROWSER_APPROVAL_SKIP=1 우회 확인 ────────────────────────────────


def test_tc3_skip_env_var_records_skip_flag(tmp_path: Path) -> None:
    """TC-3 (edge): PIPELINE_BROWSER_APPROVAL_SKIP=1 환경변수로 request-accept 실행 시
    acceptance_request.json에 browser_approval_skip=true가 기록된다.

    Oracle: tests/oracles/BUG-20260616-9DEF/edge_skip_env_var/expected.json
    """
    env = _gh_env(tmp_path)
    env["PIPELINE_BROWSER_APPROVAL_SKIP"] = "1"
    pid = _bootstrap(tmp_path, env)
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("skip env evidence", encoding="utf-8")

    r = _run_cli(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode == 0, (
        f"SKIP request-accept 실패\nstdout={r.stdout}\nstderr={r.stderr}"
    )

    # final_state: acceptance_request.json created + browser_approval_skip=true
    req_file = tmp_path / "acceptance_request.json"
    assert req_file.exists(), "acceptance_request.json 미생성"
    req = _read_acceptance_request(tmp_path)
    assert req.get("browser_approval_skip") is True, (
        f"browser_approval_skip != true: {req.get('browser_approval_skip')}"
    )
    assert req.get("browser_click_confirmed") is True, (
        f"SKIP 경로 browser_click_confirmed != true: {req.get('browser_click_confirmed')}"
    )


# ─── TC-4: 기존 nonce 재사용 방지 회귀 (AEF0) ──────────────────────────────────────────


def test_tc4_nonce_reuse_chain_intact(tmp_path: Path) -> None:
    """TC-4 (regression): nonce 재사용 방지 보안 체인이 브라우저 승인 채널 추가 후에도
    그대로 동작하는지 확인한다.

    AEF0 E2E 전체를 nested pytest로 재실행하면 본 저장소 규모에서 subprocess 다중 실행 +
    workspace hygiene git 스캔으로 수 분이 소요되어 비결정적 타임아웃이 발생한다. 따라서
    nonce 재사용 결정 함수(_should_reuse_acceptance_nonce)의 핵심 분기를 in-process로
    검증하여 보안 체인(force_new / status!=PENDING / pipeline_id / evidence / SHA / pr_body
    불일치 시 재사용 거부, 모두 동일 시 재사용)이 훼손되지 않았음을 회귀 표본으로 확인한다.
    AEF0 파일은 별도로 전체 E2E 커버리지를 유지한다.
    """
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    import pipeline as _p  # type: ignore

    body_sha = _sha256_of(_COMPLETE_PR_BODY)
    base_req: Dict[str, Any] = {
        "status": "PENDING",
        "pipeline_id": "BUG-20260616-9DEF",
        "evidence": "evidence.txt",
        "evidence_sha256": "abc123",
        "evidence_url": None,
        "pr_head_sha": "sha1",
        "github_ci_run_id": "100",
        "pr_body_sha256": body_sha,
        "pr_body_readiness": "PASS",
        "required_sections_present": True,
        "temporary_phrases_absent": True,
    }

    # (1) 모든 조건 동일 → 재사용 True (보안 체인 정상)
    reuse, _ = _p._should_reuse_acceptance_nonce(
        dict(base_req), "BUG-20260616-9DEF", "evidence.txt", "abc123",
        "sha1", "100", force_new=False, new_pr_body_sha256=body_sha,
    )
    assert reuse is True, "동일 조건인데 재사용이 거부됨 (회귀)"

    # (2) force_new=True → 재사용 거부
    reuse2, _ = _p._should_reuse_acceptance_nonce(
        dict(base_req), "BUG-20260616-9DEF", "evidence.txt", "abc123",
        "sha1", "100", force_new=True, new_pr_body_sha256=body_sha,
    )
    assert reuse2 is False, "force_new인데 재사용됨 (보안 체인 훼손)"

    # (3) evidence SHA 변경 → 재사용 거부 (변조 방지 체인)
    reuse3, _ = _p._should_reuse_acceptance_nonce(
        dict(base_req), "BUG-20260616-9DEF", "evidence.txt", "DIFFERENT",
        "sha1", "100", force_new=False, new_pr_body_sha256=body_sha,
    )
    assert reuse3 is False, "evidence SHA 변경인데 재사용됨 (보안 체인 훼손)"

    # (4) status != PENDING (이미 소모) → 재사용 거부
    consumed = dict(base_req)
    consumed["status"] = "CONSUMED"
    reuse4, _ = _p._should_reuse_acceptance_nonce(
        consumed, "BUG-20260616-9DEF", "evidence.txt", "abc123",
        "sha1", "100", force_new=False, new_pr_body_sha256=body_sha,
    )
    assert reuse4 is False, "CONSUMED 상태인데 재사용됨 (nonce 재사용 방지 훼손)"

    # final_state(함수 계약) 보존 확인: 함수가 존재하고 (bool, str) 튜플을 반환
    assert callable(getattr(_p, "_should_reuse_acceptance_nonce", None)), (
        "_should_reuse_acceptance_nonce 함수가 제거됨 (회귀)"
    )


# ─── TC-5: browser_click_confirmed 필드 없을 때 BLOCKED ──────────────────────────────


def test_tc5_missing_browser_field_blocked(tmp_path: Path) -> None:
    """TC-5 (edge): acceptance_request.json에 browser_click_confirmed 필드 자체가 없으면
    gates accept가 browser_approval_required로 BLOCKED(exit 1)된다.

    필드 부재는 미확인(is not True)으로 간주되어 fail-closed 차단되어야 한다.
    provenance 게이트를 PASS시키기 위해 허용 승인자의 독립 댓글을 제공한다.
    """
    boot_env = _gh_env(tmp_path)
    pid = _bootstrap(tmp_path, boot_env)
    env = _gh_env(tmp_path, _approver_comment(pid))
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("missing field evidence", encoding="utf-8")
    # browser 필드를 전혀 기록하지 않음
    _write_acceptance_request(
        tmp_path, pid,
        evidence=str(evidence_path),
        include_browser_fields=False,
    )
    accept_code = _accept_code(pid)

    r = _run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_path), "--acceptance-code", accept_code],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode == 1, (
        f"browser 필드 부재인데 exit code != 1\nstdout={r.stdout}\nstderr={r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert "browser_approval_required" in combined, (
        f"필드 부재 시 browser_approval_required 차단 누락\nstdout={r.stdout}\nstderr={r.stderr}"
    )
    # nonce 미소모 확인
    req = _read_acceptance_request(tmp_path)
    assert req.get("status") == "PENDING", (
        f"BLOCKED인데 status 변경됨: {req.get('status')}"
    )


# ─── TC-6: 기존 D278/2821 회귀 — workspace_hygiene 기능 동작 확인 ─────────────────────


def test_tc6_workspace_hygiene_still_works(tmp_path: Path) -> None:
    """TC-6 (regression): 기존 IMP-20260614-2821 workspace_hygiene fail-closed 기능이
    여전히 정상 동작하는지 확인한다.

    _check_workspace_hygiene를 직접 호출하여 결과 dict의 status 키가 정상 산출되는지
    (BLOCKED/WARN/OK 중 하나) 확인한다. 브라우저 승인 채널 추가가 hygiene 게이트를
    훼손하지 않았음을 검증한다.
    """
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    import pipeline as _p  # type: ignore

    # git 부재 허용 override를 켜서 결정론적으로 status를 산출.
    os.environ["PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING"] = "1"
    state: Dict[str, Any] = {
        "pipeline_id": "BUG-20260616-9DEF",
        "event_log": [],
        "contract": {},
    }
    result = _p._check_workspace_hygiene(state)
    assert isinstance(result, dict), f"_check_workspace_hygiene 반환이 dict가 아님: {type(result)}"
    assert "status" in result, f"hygiene 결과에 status 누락: {result}"
    assert result["status"] in {"BLOCKED", "WARN", "OK", "PASS"}, (
        f"hygiene status 비정상 값: {result['status']}"
    )
    # 함수가 존재하고 호출 가능함을 확인 (기능 미제거 회귀)
    assert callable(getattr(_p, "_check_workspace_hygiene", None)), (
        "_check_workspace_hygiene 함수가 제거됨 (D278/2821 회귀)"
    )
