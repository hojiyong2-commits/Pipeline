"""tests/e2e/test_auto_accept_guard_8011.py

BUG-20260616-8011 MT-3: 자동 ACCEPT 차단 가드 Real CLI Path E2E.

[Purpose]: Pipeline Manager(또는 산하 에이전트)가 사용자를 대신해 gates accept를
    자동 실행하는 프로토콜 위반을 차단하는지 검증한다. 핵심 방어선은
    `_check_pr_approver_provenance`의 packet 마커 댓글 인용 코드 탐지
    (failure_code=protocol_violation_auto_accept)이다. AC-1~AC-6을 커버한다.
[Assumptions]: pipeline.py가 프로젝트 루트에 위치한다. PIPELINE_STATE_PATH 환경 변수로
    state 파일을 tmp_path 안에 격리하고, subprocess cwd=tmp_path로 실행하여
    acceptance_request.json / failure_packet.json(상대 경로)도 격리한다. gh CLI는
    tmp_path 안의 fake gh.bat(+ python 핸들러)로 시뮬레이션하며, PATH를 tmp_path로
    제한하여 실제 gh를 무력화한다. fake gh는 git rev-parse / gh pr list / gh pr view
    --json comments 호출에 대해 시나리오별 PR 댓글을 반환한다.
[Vulnerability & Risks]: 실제 gh가 PATH에 남아 있으면 fake gh 대신 실제 gh가 호출되어
    의도와 다른 결과가 나올 수 있다. 이를 막기 위해 PATH를 tmp_path로 제한한다.
    또한 _check_pr_approver_provenance는 git rev-parse로 현재 브랜치를 조회하므로
    fake gh가 git 호출도 흡수해야 한다(fake gh.bat이 git을 가로채지 않으므로,
    브랜치 매칭 실패 시 첫 번째 PR fallback 경로를 사용한다).
[Improvement]: 시간이 더 있다면 fake gh 핸들러를 pytest fixture로 공통화하고,
    각 시나리오의 댓글 구성을 parametrize로 추출해 중복을 줄인다.
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

# 본 테스트 전역에서 사용하는 가상 pipeline_id / nonce.
# pipeline.py new 가 실제 pipeline_id를 생성하므로, 코드/댓글은 동적으로 구성한다.
_DUMMY_NONCE = "AUTONON1"  # 8자 base32-like nonce


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
        cwd: 작업 디렉토리 (acceptance_request.json/failure_packet.json 격리용).
        timeout: 초 단위 timeout.
    Returns:
        subprocess.CompletedProcess.
    Raises:
        TypeError: args가 list가 아닌 경우.
    """
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
    """PIPELINE_STATE_PATH 격리 + PATH 제한(실제 gh 무력화) 환경 dict.

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


def _write_fake_gh(tmp_path: Path, comments: List[Dict[str, Any]]) -> Path:
    """시나리오별 PR 댓글을 반환하는 fake gh 핸들러(.py) + 런처(.bat)를 생성.

    fake gh 는 아래 gh 호출을 처리한다:
      - gh pr list --state open --json number,headRefName → [{number:1, headRefName:"x"}]
      - gh pr view <n> --json comments               → {comments: [...]}
      - 기타(run/pr body 등)                          → 빈/기본 응답
    PIPELINE_GH_EXECUTABLE 가 .py 를 직접 실행하지 못하는 환경을 피하기 위해,
    shutil.which("gh") 가 찾을 수 있는 gh.bat 런처를 만들어 python 핸들러로 위임한다.

    Args:
        tmp_path: pytest tmp_path fixture.
        comments: gh pr view 가 반환할 PR 댓글 목록.
    Returns:
        생성된 gh 런처 경로(gh.bat 또는 gh).
    Raises:
        TypeError: comments가 None/리스트가 아닌 경우.
    """
    if comments is None:
        raise TypeError("comments must not be None")
    if not isinstance(comments, list):
        raise TypeError(f"comments must be list, got {type(comments).__name__}")

    comments_json = json.dumps(comments)
    handler = tmp_path / "fake_gh_handler_8011.py"
    handler.write_text(
        "import sys, io, json\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        f"COMMENTS = {comments_json}\n"
        "args = sys.argv[1:]\n"
        # gh pr list
        'if "pr" in args and "list" in args:\n'
        '    print(json.dumps([{"number": 1, "headRefName": "impl-test-branch"}])); sys.exit(0)\n'
        # gh pr view <n> --json comments
        'if "pr" in args and "view" in args and "comments" in " ".join(args):\n'
        '    print(json.dumps({"comments": COMMENTS})); sys.exit(0)\n'
        # gh pr view --json body --jq .body 등 — 빈 응답
        'if "--jq" in args:\n'
        "    print(''); sys.exit(0)\n"
        'if "run" in args and "list" in args:\n'
        "    print('[]'); sys.exit(0)\n"
        'if "run" in args and "view" in args:\n'
        "    print(json.dumps({})); sys.exit(0)\n"
        'if "pr" in args and "view" in args:\n'
        '    print(json.dumps({"comments": COMMENTS, "number": 1, "isDraft": False,\n'
        '                       "state": "OPEN", "files": [], "body": ""})); sys.exit(0)\n'
        "print('[]')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )

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


def _gh_env(tmp_path: Path, comments: List[Dict[str, Any]]) -> Dict[str, str]:
    """fake gh 런처를 PATH 선두에 두고 실제 gh를 무력화한 환경 dict.

    Args:
        tmp_path: pytest tmp_path fixture.
        comments: fake gh가 반환할 PR 댓글 목록.
    Returns:
        환경 변수 dict.
    """
    env = _base_env(tmp_path)
    _write_fake_gh(tmp_path, comments)
    # PATH를 tmp_path만으로 제한 → shutil.which("gh")가 fake gh.bat을 찾고 실제 gh는 무력화.
    env["PATH"] = str(tmp_path)
    env["PIPELINE_GH_EXECUTABLE"] = "gh"
    return env


def _bootstrap(tmp_path: Path, env: Dict[str, str]) -> str:
    """격리 환경에 IMP 파이프라인을 생성하고 pipeline_id 반환.

    Args:
        tmp_path: pytest tmp_path fixture.
        env: PIPELINE_STATE_PATH 환경변수가 설정된 dict.
    Returns:
        생성된 pipeline_id.
    Raises:
        AssertionError: pipeline.py new 실패 시.
    """
    r = _run_cli(["new", "--type", "BUG", "--desc", "auto-accept guard e2e"], env=env, cwd=tmp_path)
    assert r.returncode == 0, f"new failed: stdout={r.stdout}\nstderr={r.stderr}"
    state_file = Path(env["PIPELINE_STATE_PATH"])
    assert state_file.exists(), "pipeline_state.json not created"
    final_state = json.loads(state_file.read_text(encoding="utf-8"))
    pid = str(final_state.get("pipeline_id", ""))
    assert pid, f"pipeline_id missing: {final_state}"
    return pid


def _write_verification_json(tmp_path: Path, pipeline_id: str) -> tuple:
    """최소 human_acceptance_packet.json(verification_json)을 작성하고 (경로, SHA256) 반환.

    changed_files를 빈 배열로 두어 _verify_verification_json_freshness의 changed_files
    재검사 루프가 통과하도록 한다. gates accept의 verification_json freshness 검사를
    통과시켜 provenance 검사(자동 ACCEPT 탐지)에 도달하기 위함이다.

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
    vj_text = json.dumps(vj_data, ensure_ascii=False, indent=2)
    vj_path.write_text(vj_text, encoding="utf-8")
    vj_sha = hashlib.sha256(vj_path.read_bytes()).hexdigest()
    return str(vj_path), vj_sha


def _write_acceptance_request(
    tmp_path: Path,
    pipeline_id: str,
    *,
    nonce: str = _DUMMY_NONCE,
    status: str = "PENDING",
    evidence: str = "evidence.txt",
    with_verification_json: bool = True,
) -> Path:
    """acceptance_request.json 작성 (provenance 검사에 도달하도록 freshness 필드 구성).

    선택 freshness 필드(pr_head_sha / github_ci_run_id / evidence_sha256 /
    packet_sha256 / pr_body_sha256 / github_ci_head_sha)는 생략하여 해당 검사를 skip한다.
    단, verification_json freshness 검사는 무조건 수행되므로(필드 없으면
    verification_json_missing BLOCKED), with_verification_json=True면 유효한
    verification_json을 작성하여 통과시킨다.

    Args:
        tmp_path: pytest tmp_path fixture.
        pipeline_id: 활성 pipeline_id.
        nonce: 8자 nonce.
        status: PENDING|CONSUMED.
        evidence: 결과물 경로 (상대).
        with_verification_json: verification_json 필드 포함 여부.
    Returns:
        작성된 acceptance_request.json 절대 경로.
    """
    req: Dict[str, Any] = {
        "schema_version": 1,
        "pipeline_id": pipeline_id,
        "request_id": "auto8011",
        "nonce": nonce,
        "created_at": "2026-06-16T00:00:00Z",
        "pr_url": "",
        "evidence": evidence,
        "evidence_sha256": None,
        "evidence_url": None,
        "status": status,
    }
    if with_verification_json:
        vj_path, vj_sha = _write_verification_json(tmp_path, pipeline_id)
        req["verification_json_path"] = vj_path
        req["verification_json_sha256"] = vj_sha
    req_file = tmp_path / "acceptance_request.json"
    req_file.write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    return req_file


def _load_final_state(env: Dict[str, str]) -> Dict[str, Any]:
    """PIPELINE_STATE_PATH가 가리키는 state 파일 로드."""
    state_file = Path(env["PIPELINE_STATE_PATH"])
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text(encoding="utf-8"))


def _accept_code(pipeline_id: str, nonce: str = _DUMMY_NONCE) -> str:
    """ACCEPT-<pipeline_id>-<nonce> 형식 승인 코드."""
    return f"ACCEPT-{pipeline_id}-{nonce}"


_PACKET_MARKER = "<!-- pipeline-human-acceptance-packet -->"
_PACKET_PENDING_MARKER = "<!-- pipeline-human-acceptance-packet-pending -->"


def _allowed_approver() -> str:
    """pipeline.PIPELINE_ALLOWED_APPROVER 값을 동적으로 조회.

    fake gh 댓글 author를 실제 허용 승인자와 일치시키기 위해 사용.
    """
    _root = str(Path(__file__).resolve().parents[2])
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import pipeline as _p  # type: ignore  # noqa: E402
    return str(_p.PIPELINE_ALLOWED_APPROVER)


# ─── TC-1: packet 마커 댓글에 코드 인용 → gates accept BLOCKED (protocol_violation_auto_accept) ──


def test_tc1_auto_accept_via_packet_marker_blocked(tmp_path: Path) -> None:
    """TC-1 (case=edge): agent가 packet 마커 댓글에서 승인 코드를 인용 → BLOCKED.

    허용 승인자가 packet 마커가 포함된 댓글에 승인 코드를 인용한 경우, 사용자 직접
    승인이 아니라 자동 ACCEPT 시도로 판정하여 protocol_violation_auto_accept로 차단한다.
    격리: PIPELINE_STATE_PATH + cwd=tmp_path. final_state(acceptance != PASS) assertion 포함.
    """
    env = _base_env(tmp_path)
    pid = _bootstrap(tmp_path, env)
    code = _accept_code(pid)
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("auto-accept guard evidence", encoding="utf-8")
    _write_acceptance_request(tmp_path, pid, evidence=str(evidence_path))

    # packet 마커 댓글 안에 승인 코드를 인용 → 자동 ACCEPT 시도.
    comments = [
        {
            "author": {"login": _allowed_approver()},
            "body": f"{_PACKET_PENDING_MARKER}\n## 최종 확인 안내\n승인 코드: {code}",
            "id": "C-auto",
        }
    ]
    env = _gh_env(tmp_path, comments)
    env["PIPELINE_STATE_PATH"] = str(tmp_path / "pipeline_state.json")

    result = _run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_path), "--acceptance-code", code],
        env=env,
        cwd=tmp_path,
    )

    # CLI는 BLOCKED로 종료(exit code != 0)되어야 한다.
    assert result.returncode != 0, (
        f"자동 ACCEPT 시도는 BLOCKED여야 함 (exit!=0)\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "protocol_violation_auto_accept" in combined or "프로토콜 위반" in combined or "자동 ACCEPT" in combined, (
        f"protocol_violation_auto_accept 차단 메시지 누락\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    # final_state: acceptance gate가 PASS가 아니어야 한다.
    final_state = _load_final_state(env)
    acc = final_state.get("external_gates", {}).get("acceptance", {}).get("status")
    assert acc != "PASS", f"자동 ACCEPT가 PASS로 통과됨 (보안 위반): acceptance={acc}"


# ─── TC-2: acceptance_request.json 없음 → missing_acceptance_request (기존 nonce 체인) ───────


def test_tc2_no_request_blocked(tmp_path: Path) -> None:
    """TC-2 (case=exception): acceptance_request.json이 없으면 missing_acceptance_request로 차단.

    승인 코드가 PR body/packet에 있어도, 사용자가 발급받은 nonce 요청이 없으면
    gates accept는 통과하지 못한다. 기존 nonce 검증 체인이 유지됨을 확인한다.
    """
    env = _base_env(tmp_path)
    pid = _bootstrap(tmp_path, env)
    code = _accept_code(pid)
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("no request evidence", encoding="utf-8")
    # acceptance_request.json을 작성하지 않음 → missing.

    result = _run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_path), "--acceptance-code", code],
        env=env,
        cwd=tmp_path,
    )

    assert result.returncode != 0, (
        f"acceptance_request.json 없으면 BLOCKED여야 함\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "missing_acceptance_request" in combined, (
        f"missing_acceptance_request 차단 메시지 누락\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    final_state = _load_final_state(env)
    acc = final_state.get("external_gates", {}).get("acceptance", {}).get("status")
    assert acc != "PASS", f"요청 없이 ACCEPT 통과됨: acceptance={acc}"


# ─── TC-3: 정상 수동 ACCEPT (분리된 독립 댓글의 정확한 코드) → provenance 미차단 ─────────────


def test_tc3_manual_accept_independent_comment_passes_provenance(tmp_path: Path) -> None:
    """TC-3 (case=normal): 허용 승인자가 packet과 분리된 독립 댓글로 정확한 코드를 남기면
    provenance가 통과하여 protocol_violation_auto_accept로 차단되지 않는다.

    provenance 통과 이후 다른 게이트(technical/oracle/ci)에서 BLOCK될 수 있으나,
    본 TC의 핵심은 "자동 ACCEPT 오탐 없이 정상 수동 승인 흐름이 살아 있는가"이다.
    따라서 protocol_violation_auto_accept가 발생하지 않음을 단언한다.
    """
    env = _base_env(tmp_path)
    pid = _bootstrap(tmp_path, env)
    code = _accept_code(pid)
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("manual accept evidence", encoding="utf-8")
    _write_acceptance_request(tmp_path, pid, evidence=str(evidence_path))

    # packet 마커 없는 독립 댓글에 정확한 코드만 → 정상 수동 승인.
    comments = [
        {
            "author": {"login": _allowed_approver()},
            "body": code,
            "id": "C-manual",
        }
    ]
    env = _gh_env(tmp_path, comments)
    env["PIPELINE_STATE_PATH"] = str(tmp_path / "pipeline_state.json")

    result = _run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_path), "--acceptance-code", code],
        env=env,
        cwd=tmp_path,
    )

    combined = result.stdout + result.stderr
    # 핵심 단언: 정상 수동 승인 흐름에서는 자동 ACCEPT 오탐이 발생하면 안 된다.
    assert "protocol_violation_auto_accept" not in combined, (
        "정상 수동 ACCEPT 흐름이 자동 ACCEPT로 오탐됨 (회귀)\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    # final_state는 존재해야 한다(state 격리 확인).
    final_state = _load_final_state(env)
    assert "external_gates" in final_state, "state 파일에 external_gates 누락 (격리 실패)"


# ─── TC-4: 기존 b96c provenance 회귀 (정상 분류 유지) ────────────────────────────────────────


def test_tc4_b96c_regression_intact(tmp_path: Path) -> None:
    """TC-4 (case=error): 기존 b96c provenance 회귀 테스트가 여전히 통과한다.

    본 BUG 수정이 stale_nonce/code_mismatch 정상 분류(b96c) 및 nonce 보안 체인을
    훼손하지 않았음을 subprocess로 pytest 실행하여 확인한다.
    """
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_pr_approver_b96c.py", "-q"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(root),
        timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    # final_state: pytest exit code 0 (전체 통과).
    assert result.returncode == 0, (
        f"b96c 회귀 테스트 실패 (정상 분류/보안 체인 훼손)\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "passed" in result.stdout, f"pytest 통과 표시 누락: {result.stdout}"


# ─── TC-5: 자동 ACCEPT 차단 시 failure_packet.json에 protocol_violation 기록 ──────────────────


def test_tc5_protocol_violation_failure_packet(tmp_path: Path) -> None:
    """TC-5 (case=edge): 자동 ACCEPT 차단 시 failure_packet에 failure_category=protocol_violation 기록.

    TC-1과 동일 시나리오를 실행한 뒤, cwd에 생성된 failure_packet.json(또는 state의
    failure_packets)에 protocol_violation 카테고리가 기록되었는지 확인한다.
    """
    env = _base_env(tmp_path)
    pid = _bootstrap(tmp_path, env)
    code = _accept_code(pid)
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("failure packet evidence", encoding="utf-8")
    _write_acceptance_request(tmp_path, pid, evidence=str(evidence_path))

    comments = [
        {
            "author": {"login": _allowed_approver()},
            "body": f"{_PACKET_MARKER}\n승인 코드: {code}",
            "id": "C-fp",
        }
    ]
    env = _gh_env(tmp_path, comments)
    env["PIPELINE_STATE_PATH"] = str(tmp_path / "pipeline_state.json")

    result = _run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_path), "--acceptance-code", code],
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode != 0, (
        f"자동 ACCEPT 시도는 BLOCKED여야 함\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    # failure_packet.json 또는 state.failure_packets에서 protocol_violation 확인.
    found_protocol_violation = False
    fp_path = tmp_path / "failure_packet.json"
    if fp_path.exists():
        fp = json.loads(fp_path.read_text(encoding="utf-8"))
        if (
            fp.get("failure_category") == "protocol_violation"
            or fp.get("failure_code") == "protocol_violation_auto_accept"
        ):
            found_protocol_violation = True

    if not found_protocol_violation:
        final_state = _load_final_state(env)
        for pkt in final_state.get("failure_packets", []):
            if (
                pkt.get("failure_category") == "protocol_violation"
                or pkt.get("failure_code") == "protocol_violation_auto_accept"
            ):
                found_protocol_violation = True
                break
        # auto_accept_attempts 감사 카운터도 함께 확인 (보조 증거).
        if not found_protocol_violation:
            acc_audit = final_state.get("acceptance", {})
            if int(acc_audit.get("auto_accept_attempts", 0) or 0) >= 1:
                found_protocol_violation = True

    assert found_protocol_violation, (
        "자동 ACCEPT 차단 시 protocol_violation failure packet/감사 기록이 없음\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


# ─── TC-6: 정상 provenance(마커 없는 정확 코드) PASS 회귀 ─────────────────────────────────────


def test_tc6_normal_provenance_no_false_positive(tmp_path: Path) -> None:
    """TC-6 (case=normal): 마커 없는 정확한 코드 댓글은 자동 ACCEPT로 오탐되지 않는다.

    여러 댓글(packet 안내 + 독립 정확 코드)이 섞여 있어도, packet 마커 댓글에 코드가
    인용되지 않은 경우에는 자동 ACCEPT가 발생하지 않고, 독립 정확 코드 댓글이 정상
    승인 후보로 인정된다. (오탐 0건 회귀)
    """
    env = _base_env(tmp_path)
    pid = _bootstrap(tmp_path, env)
    code = _accept_code(pid)
    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("normal provenance evidence", encoding="utf-8")
    _write_acceptance_request(tmp_path, pid, evidence=str(evidence_path))

    comments = [
        {
            # packet 안내 댓글 — 코드 인용 없음 (자동 ACCEPT 아님).
            "author": {"login": _allowed_approver()},
            "body": f"{_PACKET_MARKER}\n## 최종 확인 안내\n결과물을 확인해 주세요.",
            "id": "C-packet",
        },
        {
            # 허용 승인자의 독립 정확 코드 — 정상 수동 승인.
            "author": {"login": _allowed_approver()},
            "body": code,
            "id": "C-ok",
        },
    ]
    env = _gh_env(tmp_path, comments)
    env["PIPELINE_STATE_PATH"] = str(tmp_path / "pipeline_state.json")

    result = _run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence_path), "--acceptance-code", code],
        env=env,
        cwd=tmp_path,
    )

    combined = result.stdout + result.stderr
    # 핵심 단언: 정상 케이스에서 자동 ACCEPT 오탐이 발생하면 안 된다.
    assert "protocol_violation_auto_accept" not in combined, (
        "정상 provenance 흐름이 자동 ACCEPT로 오탐됨 (회귀)\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    final_state = _load_final_state(env)
    assert "external_gates" in final_state, "state 파일에 external_gates 누락 (격리 실패)"


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
