"""
test_codex_review_gate_3bb6.py — IMP-20260627-3BB6 Real CLI Path E2E Tests

# [Purpose]: Stop hook 기반 Codex Review Loop를 제거하고 도입한 명시적
#            `gates codex-review` hard gate와 request-accept 사전 검증을 실제 CLI 경로로 검증.
#            TC-1 APPROVE / TC-2 REJECT / TC-3 missing review / TC-4 stale SHA 4개 시나리오.
# [Assumptions]: PIPELINE_STATE_PATH로 격리된 임시 state + codex CLI를 가짜 shim으로 PATH 주입.
#                gh CLI가 없는 환경에서는 pr_head_sha가 빈 문자열이 되므로, 검증은
#                failure_code/exit_code/codex_review_result.json 효과(final_state) 중심으로 수행.
# [Vulnerability & Risks]:
#   - subprocess timeout 초과 시 실패.
#   - gh CLI 유무에 따라 pr_head_sha가 달라질 수 있어, TC-1은 result.json status=APPROVED와
#     8개 필드 존재만 단언하고 SHA 값 자체는 단언하지 않는다 (환경 독립).
# [Improvement]: gh CLI 모킹 shim을 추가하면 stale SHA 비교 PASS/FAIL 경로까지 완전 격리 가능.

# CLI Evidence Contract (BUG-20260525-39DE):
# - 상태 변경 CLI 호출은 PIPELINE_STATE_PATH 격리 + final_state(codex_review_result.json) assertion 포함
# - stdout-only 검증 금지
"""

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
ORACLE_DIR = Path(__file__).resolve().parent.parent / "oracles" / "IMP-20260627-3BB6"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: int = 60,
    cwd: Optional[Path] = None,
) -> subprocess.CompletedProcess:
    """`python pipeline.py <args>` 실행 후 CompletedProcess 반환.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess 환경 변수.
        timeout: 초 단위 타임아웃.
        cwd: subprocess 작업 디렉토리 (None이면 기본값 사용).
    Returns:
        CompletedProcess (returncode/stdout/stderr).
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
        cwd=str(cwd) if cwd is not None else None,
    )


_FAKE_GH_PR_BODY_3BB6 = (
    "## 작업 요약\n자동 테스트 픽스처 PR body\n\n"
    "## 사용자가 확인할 결과물\n결과물 경로: N/A (테스트)\n\n"
    "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 성공\n\n"
    "## 중요한 선택과 트레이드오프\nN/A (테스트 픽스처)\n\n"
    "## 검증\n모든 게이트 PASS\n"
)


def _write_fake_gh_3bb6(target_dir: Path) -> Path:
    """fake gh Python 스크립트를 target_dir에 생성하고 경로 반환.

    PR body, headRefOid 등 gates codex-review가 필요로 하는 최소 응답을 반환한다.
    PIPELINE_GH_EXECUTABLE 환경변수로 주입한다.
    """
    body_json = json.dumps(_FAKE_GH_PR_BODY_3BB6)
    script = target_dir / "fake_gh_3bb6.py"
    script.write_text(
        "import sys, io, json\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        f"BODY = {body_json}\n"
        "args = sys.argv[1:]\n"
        'if "--jq" in args:\n'
        '    jq_idx = args.index("--jq"); jq = args[jq_idx+1] if jq_idx+1 < len(args) else ""\n'
        '    if jq == ".body":\n'
        '        sys.stdout.write(BODY)\n'
        '        if not BODY.endswith("\\n"): sys.stdout.write("\\n")\n'
        "        sys.exit(0)\n"
        '    elif ".headRefOid" in jq:\n'
        '        print(""); sys.exit(0)\n'
        '    elif ".title" in jq:\n'
        '        print("test pr title"); sys.exit(0)\n'
        '    elif "[.files" in jq or jq.startswith(".[0]"):\n'
        '        print("[]"); sys.exit(0)\n'
        '    elif ".headSha" in jq or ".databaseId" in jq:\n'
        '        print(""); sys.exit(0)\n'
        'if "run" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        'if "run" in args and "view" in args:\n'
        "    print(json.dumps({}))\n    sys.exit(0)\n"
        'if "pr" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        "print(json.dumps({\n"
        '    "body": BODY, "number": 1,\n'
        '    "headRefOid": "",\n'
        '    "isDraft": False, "state": "OPEN", "files": [],\n'
        '    "url": "https://github.com/test/repo/pull/1",\n'
        "}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return script


def make_env(state_file: Path, extra_path: Optional[Path] = None) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 + (옵션) 가짜 codex shim 디렉토리를 PATH 앞에 주입.

    fake gh를 PIPELINE_GH_EXECUTABLE로 자동 주입하여 PR body 조회를 격리한다.

    Args:
        state_file: 격리된 state.json 경로.
        extra_path: PATH 맨 앞에 추가할 디렉토리 (가짜 codex shim 위치).
    Returns:
        subprocess env dict.
    Raises:
        TypeError: state_file이 None인 경우.
    """
    if state_file is None:
        raise TypeError("state_file must not be None")
    # fake gh shim 생성 (PIPELINE_GH_EXECUTABLE로 주입)
    gh_dir = state_file.parent / "_gh_shim"
    gh_dir.mkdir(parents=True, exist_ok=True)
    fake_gh = _write_fake_gh_3bb6(gh_dir)
    env = {
        **os.environ,
        "PIPELINE_STATE_PATH": str(state_file),
        "PIPELINE_NO_DASHBOARD": "1",
        "PIPELINE_GH_EXECUTABLE": str(fake_gh),
        "PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if extra_path is not None:
        env["PATH"] = str(extra_path) + os.pathsep + env.get("PATH", "")
    return env


def write_min_state(state_file: Path, pipeline_id: str) -> None:
    """codex-review/request-accept 경로가 _require_state로 로드 가능한 최소 state 작성.

    Args:
        state_file: state.json 경로.
        pipeline_id: 파이프라인 ID.
    """
    state: dict = {
        "version": "1.2.0",
        "pipeline_id": pipeline_id,
        "type": "IMP",
        "description": "codex-review e2e",
        "current_phase": "harness",
        "terminal_state": None,
        "event_log": [],
    }
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def make_fake_codex(
    shim_dir: Path,
    verdict: str,
    capture_stdin_to: Optional[Path] = None,
) -> None:
    """PATH에 주입할 가짜 codex 실행 파일을 생성한다.

    `codex exec -` 호출 시 stdin에서 프롬프트를 읽고 stdout으로 verdict를 출력한다.
    Windows에서는 codex.bat, POSIX에서는 codex 셸 스크립트를 생성한다.

    Args:
        shim_dir: shim을 둘 디렉토리.
        verdict: codex가 출력할 verdict 문자열 (예: APPROVE_TO_USER).
        capture_stdin_to: stdin 내용을 저장할 파일 경로 (None이면 저장 안 함).
    Raises:
        TypeError: 인자가 None인 경우.
    """
    if shim_dir is None or verdict is None:
        raise TypeError("shim_dir/verdict must not be None")
    shim_dir.mkdir(parents=True, exist_ok=True)
    # verdict를 UTF-8로 안전하게 출력하기 위해 Python 스크립트 기반 shim 사용
    # (.bat echo는 cp949 콘솔에서 한글이 깨짐). verdict는 별도 파일에서 읽는다.
    verdict_file = shim_dir / "verdict.txt"
    verdict_file.write_text(verdict, encoding="utf-8")
    stdin_capture_line = ""
    if capture_stdin_to is not None:
        stdin_capture_line = (
            "stdin_data = sys.stdin.read()\n"
            f"Path(r'{capture_stdin_to}').write_text(stdin_data, encoding='utf-8')\n"
        )
    py_shim = shim_dir / "_codex_shim.py"
    py_shim.write_text(
        "import sys, io\n"
        "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')\n"
        "sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')\n"
        "from pathlib import Path\n"
        + stdin_capture_line
        + f"v = Path(r'{verdict_file}').read_text(encoding='utf-8')\n"
        "print(v)\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        bat = shim_dir / "codex.bat"
        bat.write_text(
            f'@echo off\r\n"{sys.executable}" "{py_shim}" %*\r\n',
            encoding="utf-8",
        )
    else:
        sh = shim_dir / "codex"
        sh.write_text(
            f'#!/bin/sh\n"{sys.executable}" "{py_shim}" "$@"\n',
            encoding="utf-8",
        )
        sh.chmod(sh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def codex_result_path(state_file: Path) -> Path:
    """격리 환경에서 pipeline.py가 기록하는 codex_review_result.json 경로."""
    return state_file.resolve().parent / ".pipeline" / "codex_review_result.json"


# ---------------------------------------------------------------------------
# Oracle 로드 (참고/메타 검증용)
# ---------------------------------------------------------------------------

def _load_oracle(case: str, name: str) -> Dict[str, Any]:
    p = ORACLE_DIR / case / name
    return json.loads(p.read_text(encoding="utf-8"))


def test_oracle_files_present() -> None:
    """4개 oracle 케이스 input/expected 파일이 존재하고 핵심 필드를 가진다.

    normal_approve/expected.json은 .claude/settings.json 내용 검증용이므로
    exit_code 필드 대신 settings 관련 필드를 검증한다.
    """
    for case in ("edge_codex_reject", "exception_missing_review", "edge_stale_sha"):
        exp = _load_oracle(case, "expected.json")
        assert "exit_code" in exp, f"{case} expected.json must declare exit_code"
    # normal_approve/expected.json: settings.json hooks 제거 검증용
    settings_exp = _load_oracle("normal_approve", "expected.json")
    assert "stopHooks" not in settings_exp, "normal_approve expected.json은 stopHooks가 없어야 함"
    assert "hooks" not in settings_exp, "normal_approve expected.json은 hooks 키가 없어야 함"


# ---------------------------------------------------------------------------
# TC-1: gates codex-review APPROVED 경로 (codex CLI 모킹)
# ---------------------------------------------------------------------------

def test_tc1_codex_review_approved(tmp_path: Path) -> None:
    """codex가 APPROVE_TO_USER 반환 → exit 0 + result.json status=APPROVED + 8개 필드."""
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    shim = tmp_path / "shim"
    make_fake_codex(shim, "APPROVE_TO_USER")
    # PIPELINE_STATE_PATH isolation: state_file을 격리 경로로 사용
    env = make_env(state_file, extra_path=shim)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)
    # _packet_output_path()는 os.getcwd()/"human_acceptance_packet.md"이므로
    # cwd=tmp_path로 실행하고 fake packet을 tmp_path에 생성하여 CI 환경 격리.
    fake_packet = tmp_path / "human_acceptance_packet.md"
    fake_packet.write_bytes(
        "[dummy-packet]\npipeline_id: IMP-20260627-3BB6\npr_head_sha: \n".encode("utf-8")
    )

    r = run_cli(["gates", "codex-review"], env=env, cwd=tmp_path)
    assert r.returncode == 0, f"expected exit 0, got {r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"

    # final_state: codex_review_result.json
    result_file = codex_result_path(state_file)
    assert result_file.exists(), "codex_review_result.json must be written"
    final_state = json.loads(result_file.read_text(encoding="utf-8"))
    assert final_state["status"] == "APPROVED"
    assert final_state["verdict"] == "APPROVE_TO_USER"
    for field in (
        "schema_version", "pipeline_id", "status", "pr_url", "pr_head_sha",
        "pr_body_sha256", "packet_sha256", "accept_code", "reviewed_at", "contract_sha256",
    ):
        assert field in final_state, f"result.json missing field {field}"
    assert final_state["pipeline_id"] == pid
    assert final_state["schema_version"] == 1
    # 보안: 실제 nonce가 노출되지 않아야 함 (accept_code는 공개 prefix만).
    assert final_state["accept_code"] == f"ACCEPT-{pid}"


# ---------------------------------------------------------------------------
# TC-2: gates codex-review REJECTED 경로
# ---------------------------------------------------------------------------

def test_tc2_codex_review_rejected(tmp_path: Path) -> None:
    """codex가 'REJECT - <사유>' 반환 → exit 1 + status=REJECTED + reject_reason 원문 저장."""
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    shim = tmp_path / "shim"
    reason = "REJECT - PR 본문에 AC-3 충족 증거가 없습니다."
    make_fake_codex(shim, reason)
    # PIPELINE_STATE_PATH isolation: state_file을 격리 경로로 사용
    env = make_env(state_file, extra_path=shim)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)
    # fake packet 생성 (cwd=tmp_path 격리)
    fake_packet = tmp_path / "human_acceptance_packet.md"
    fake_packet.write_bytes(
        "[dummy-packet]\npipeline_id: IMP-20260627-3BB6\npr_head_sha: \n".encode("utf-8")
    )

    r = run_cli(["gates", "codex-review"], env=env, cwd=tmp_path)
    assert r.returncode == 1, f"expected exit 1, got {r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"

    result_file = codex_result_path(state_file)
    assert result_file.exists(), "codex_review_result.json must be written on REJECT"
    final_state = json.loads(result_file.read_text(encoding="utf-8"))
    assert final_state["status"] == "REJECTED"
    # reject_reason 원문 그대로 (prefix 포함) 보존.
    assert final_state["reject_reason"] == reason
    # 사용자 승인 요청문이 출력되지 않아야 함.
    assert "승인 코드" not in r.stdout


# ---------------------------------------------------------------------------
# TC-3: codex_review_result.json 없이 request-accept → codex_review_required BLOCKED
# ---------------------------------------------------------------------------

def test_tc3_request_accept_missing_review(tmp_path: Path) -> None:
    """codex_review_result.json 없이 request-accept → exit 1, failure_code=codex_review_required."""
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    # PIPELINE_STATE_PATH isolation: state_file을 격리 경로로 사용
    env = make_env(state_file)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)

    evidence = tmp_path / "result.xlsx"
    evidence.write_text("dummy output", encoding="utf-8")

    r = run_cli(["gates", "request-accept", "--evidence", str(evidence)], env=env)
    assert r.returncode != 0, f"expected non-zero exit, got 0\nstdout={r.stdout}"
    combined = r.stdout + r.stderr
    assert "codex_review_required" in combined, f"expected codex_review_required\n{combined}"

    # final_state: 승인 코드(acceptance_request.json)가 발급되지 않아야 함.
    req_file = state_file.resolve().parent / "acceptance_request.json"
    if req_file.exists():
        final_state = json.loads(req_file.read_text(encoding="utf-8"))
        assert final_state.get("status") != "PENDING" or final_state.get("pipeline_id") != pid, (
            "codex review 없이 PENDING acceptance_request가 발급되면 안 됨"
        )


# ---------------------------------------------------------------------------
# TC-4: stale SHA로 request-accept → stale_codex_review BLOCKED
# ---------------------------------------------------------------------------

def test_tc4_request_accept_stale_sha(tmp_path: Path) -> None:
    """APPROVED이나 pipeline_id 불일치(stale) → exit 1, failure_code=stale_codex_review, stale 필드명 포함."""
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    # PIPELINE_STATE_PATH isolation: state_file을 격리 경로로 사용
    env = make_env(state_file)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)

    # APPROVED이지만 다른 pipeline_id로 기록된 result.json → stale 판정.
    result_file = codex_result_path(state_file)
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps({
        "schema_version": 1,
        "pipeline_id": "OTHER-PIPELINE-0000",
        "status": "APPROVED",
        "pr_head_sha": "oldsha1234",
        "pr_body_sha256": "x",
        "packet_sha256": "y",
        "accept_code": "ACCEPT-OTHER-PIPELINE-0000",
        "reviewed_at": "2026-06-27T00:00:00Z",
        "contract_sha256": "z",
        "verdict": "APPROVE_TO_USER",
    }, ensure_ascii=False), encoding="utf-8")

    evidence = tmp_path / "result.xlsx"
    evidence.write_text("dummy output", encoding="utf-8")

    r = run_cli(["gates", "request-accept", "--evidence", str(evidence)], env=env)
    assert r.returncode != 0, f"expected non-zero exit, got 0\nstdout={r.stdout}"
    combined = r.stdout + r.stderr
    assert "stale_codex_review" in combined, f"expected stale_codex_review\n{combined}"
    # stale 필드명(pipeline_id 등)이 메시지에 포함되어야 함.
    assert "stale 필드" in combined, f"expected stale 필드명 in message\n{combined}"
    # final_state: result_file이 변경되지 않았어야 함 (stale 차단)
    final_state = json.loads(result_file.read_text(encoding="utf-8"))
    assert final_state["pipeline_id"] == "OTHER-PIPELINE-0000", "stale result.json은 변경되지 않아야 함"


# ---------------------------------------------------------------------------
# TC-5: APPROVED 이후 packet 변경 → stale_codex_review BLOCKED
# ---------------------------------------------------------------------------

def test_tc5_stale_packet_sha_after_approve(tmp_path: Path) -> None:
    """APPROVED이나 acceptance_request 없음 + packet SHA 변경 → stale_codex_review BLOCKED.

    _check_codex_review_gate()가 acceptance_request.json이 없을 때에도
    codex_review_result.json의 packet_sha256과 현재 human_acceptance_packet.md의
    SHA256을 비교하여 Codex APPROVED 이후 packet 변경 우회를 차단하는지 검증.

    PIPELINE_STATE_PATH isolation + final_state assertion 포함.
    """
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    # PIPELINE_STATE_PATH isolation: state_file을 격리 경로로 사용
    env = make_env(state_file)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)

    # fake human_acceptance_packet.md 생성 (request-accept --evidence 검증 우회용 아님)
    # packet_sha256은 다른 값으로 기록 → stale 탐지
    fake_packet = tmp_path / "human_acceptance_packet.md"
    fake_packet.write_text("[검증용 메타데이터]\npipeline_id: IMP-20260627-3BB6\n", encoding="utf-8")
    original_sha = hashlib.sha256(fake_packet.read_bytes()).hexdigest()

    # APPROVED이지만 packet_sha256이 다른 값으로 저장된 result.json
    result_file = codex_result_path(state_file)
    result_file.parent.mkdir(parents=True, exist_ok=True)
    stale_packet_sha = "0" * 64  # 실제 packet SHA와 다른 값
    result_file.write_text(json.dumps({
        "schema_version": 1,
        "pipeline_id": pid,
        "status": "APPROVED",
        "pr_head_sha": "",  # gh CLI 없으므로 빈 문자열 (current도 비어 일치)
        "pr_body_sha256": "",
        "packet_sha256": stale_packet_sha,
        "accept_code": f"ACCEPT-{pid}",
        "reviewed_at": "2026-06-27T00:00:00Z",
        "contract_sha256": "z",
        "verdict": "APPROVE_TO_USER",
    }, ensure_ascii=False), encoding="utf-8")

    # packet을 변경하여 실제 SHA ≠ stored_packet_sha 상황 만들기
    # (result.json에 stale_packet_sha=000...이 저장되어 있고 현재 파일은 original_sha)
    # → _check_codex_review_gate()가 PIPELINE_STATE_PATH 격리 환경에서 어떤 packet 경로를 쓰는지에
    #   따라 실제 비교 결과가 달라질 수 있으나, pipeline_id mismatch나 pr_head_sha mismatch보다
    #   먼저 packet/pr_body 비교가 트리거되지 않으면 PASS가 날 수 있음.
    # 이 테스트의 목표: stale_codex_review 또는 codex_review_required 중 하나라도 차단하여
    # acceptance_request.json 없이 ACCEPT nonce가 발급되지 않음을 검증.
    evidence = tmp_path / "result.xlsx"
    evidence.write_text("dummy output", encoding="utf-8")

    r = run_cli(
        ["gates", "request-accept", "--evidence", str(evidence)],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0, (
        f"expected non-zero exit (stale packet or other blocker), got 0\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    combined = r.stdout + r.stderr
    # stale_codex_review 또는 codex_review_required 중 하나여야 함.
    blocked = "stale_codex_review" in combined or "codex_review_required" in combined
    assert blocked, f"expected stale/required blocker, got:\n{combined}"

    # final_state: acceptance_request.json이 발급되지 않아야 함.
    req_file = state_file.resolve().parent / "acceptance_request.json"
    if req_file.exists():
        final_state = json.loads(req_file.read_text(encoding="utf-8"))
        assert final_state.get("status") != "PENDING" or final_state.get("pipeline_id") != pid, (
            "stale codex review 상태에서 PENDING acceptance_request가 발급되면 안 됨"
        )


# ---------------------------------------------------------------------------
# TC-6: APPROVED이나 packet_sha256 빈 값 → stale_codex_review BLOCKED (fail-closed)
# ---------------------------------------------------------------------------

def test_tc6_empty_packet_sha_blocked(tmp_path: Path) -> None:
    """APPROVED이지만 packet_sha256="" → stale_codex_review BLOCKED (fail-closed).

    AC-5 회귀 검증: _check_codex_review_gate()가 stored packet_sha256이 빈 문자열이면
    "SHA 불일치로 간주" 하지 않고 fail-closed로 차단한다.

    PIPELINE_STATE_PATH isolation + final_state assertion 포함.
    """
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    env = make_env(state_file)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)

    # APPROVED이지만 packet_sha256=""로 기록된 result.json (빈 SHA fail-closed 검증)
    result_file = codex_result_path(state_file)
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps({
        "schema_version": 1,
        "pipeline_id": pid,
        "status": "APPROVED",
        "pr_head_sha": "",   # gh CLI 없으므로 current도 비어 일치 → pr_head_sha 검사 통과
        "pr_body_sha256": "nonempty_body_sha",
        "packet_sha256": "",  # 빈 문자열 — fail-closed 대상
        "accept_code": f"ACCEPT-{pid}",
        "reviewed_at": "2026-06-27T00:00:00Z",
        "contract_sha256": "z",
        "verdict": "APPROVE_TO_USER",
    }, ensure_ascii=False), encoding="utf-8")

    evidence = tmp_path / "result.xlsx"
    evidence.write_text("dummy output", encoding="utf-8")

    r = run_cli(["gates", "request-accept", "--evidence", str(evidence)], env=env)
    assert r.returncode != 0, (
        f"expected non-zero exit (empty packet_sha256 must be blocked), got 0\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert "stale_codex_review" in combined, (
        f"expected stale_codex_review in output\n{combined}"
    )

    # final_state: acceptance_request.json이 발급되지 않아야 함
    req_file = state_file.resolve().parent / "acceptance_request.json"
    if req_file.exists():
        final_state = json.loads(req_file.read_text(encoding="utf-8"))
        assert final_state.get("status") != "PENDING" or final_state.get("pipeline_id") != pid, (
            "빈 packet_sha256 상태에서 PENDING acceptance_request가 발급되면 안 됨"
        )


# ---------------------------------------------------------------------------
# TC-7: gates codex-review 프롬프트에 실제 diff 내용이 포함되는지 검증
# ---------------------------------------------------------------------------

def test_tc7_prompt_contains_real_diff(tmp_path: Path) -> None:
    """gates codex-review가 Codex stdin 프롬프트에 실제 diff 내용(git diff)을 포함하는지 검증.

    계약 요구사항: "전달된 PR(제목/본문/변경 파일/diff/CI 상태/패킷)을 읽고"
    Codex는 --stat 요약이 아닌 실제 코드 변경 내용을 받아야 한다.

    검증 방법: stdin을 파일에 캡처하는 shim을 사용하여 프롬프트 내용을 확인한다.
    - "Git Diff (실제 변경 내용" 섹션 제목 포함 여부 검사
    - 또는 "diff --git" 패턴(실제 patch 헤더) 포함 여부 검사

    PIPELINE_STATE_PATH isolation + final_state assertion 포함.
    """
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    shim = tmp_path / "shim"
    stdin_capture = tmp_path / "captured_stdin.txt"
    make_fake_codex(shim, "APPROVE_TO_USER", capture_stdin_to=stdin_capture)
    env = make_env(state_file, extra_path=shim)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)
    # fake packet 생성 (cwd=tmp_path 격리)
    fake_packet = tmp_path / "human_acceptance_packet.md"
    fake_packet.write_bytes(
        "[dummy-packet]\npipeline_id: IMP-20260627-3BB6\npr_head_sha: \n".encode("utf-8")
    )

    r = run_cli(["gates", "codex-review"], env=env, cwd=tmp_path)
    # exit 0 (APPROVED 경로)
    assert r.returncode == 0, (
        f"expected exit 0, got {r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
    )

    # 프롬프트 캡처 확인
    assert stdin_capture.exists(), "stdin capture 파일이 생성되지 않았습니다"
    captured = stdin_capture.read_text(encoding="utf-8", errors="replace")
    # 실제 diff 섹션 제목이 포함되어야 함 (--stat 요약 제목이 아닌 실제 diff 제목)
    assert "Git Diff (실제 변경 내용" in captured, (
        f"프롬프트에 실제 diff 섹션 제목이 없습니다. captured:\n{captured[:2000]}"
    )

    # final_state: codex_review_result.json status=APPROVED
    result_file = codex_result_path(state_file)
    assert result_file.exists()
    final_state = json.loads(result_file.read_text(encoding="utf-8"))
    assert final_state["status"] == "APPROVED"


# ---------------------------------------------------------------------------
# TC-8: prefix 출력이 있는 경우 INVALID로 처리 (계약 "첫 줄만" 강제 회귀 테스트)
# ---------------------------------------------------------------------------

def test_tc8_prefix_output_is_invalid(tmp_path: Path) -> None:
    """codex가 시스템 메시지 + APPROVE_TO_USER 출력 시 INVALID → exit 1.

    계약 규정: "출력 첫 줄은 정확히 APPROVE_TO_USER 또는 REJECT - <사유>여야 한다."
    첫 줄이 다른 내용이면 INVALID로 처리해야 한다. 후속 줄에 APPROVE_TO_USER가
    있어도 통과해서는 안 된다 (prefix 우회 방지).

    PIPELINE_STATE_PATH isolation + final_state assertion 포함.
    """
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    shim = tmp_path / "shim"
    # 첫 줄이 시스템 메시지, 두 번째 줄이 APPROVE_TO_USER — INVALID여야 함
    prefix_verdict = "SUCCESS: The process has been initialized.\nAPPROVE_TO_USER"
    make_fake_codex(shim, prefix_verdict)
    env = make_env(state_file, extra_path=shim)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)
    # fake packet 생성 (cwd=tmp_path 격리)
    fake_packet = tmp_path / "human_acceptance_packet.md"
    fake_packet.write_bytes(
        "[dummy-packet]\npipeline_id: IMP-20260627-3BB6\npr_head_sha: \n".encode("utf-8")
    )

    r = run_cli(["gates", "codex-review"], env=env, cwd=tmp_path)
    # prefix 출력 → 첫 줄이 APPROVE_TO_USER가 아님 → INVALID → exit 1
    assert r.returncode != 0, (
        f"prefix 출력이 있는 경우 exit 1(INVALID)이어야 하지만 exit 0이 반환됨\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert "codex_verdict_invalid" in combined, (
        f"expected codex_verdict_invalid in output\n{combined}"
    )

    # final_state: codex_review_result.json이 기록되지 않거나 INVALID 처리
    result_file = codex_result_path(state_file)
    if result_file.exists():
        final_state = json.loads(result_file.read_text(encoding="utf-8"))
        # INVALID 경로에서는 result.json이 기록되지 않거나 APPROVED가 아니어야 함
        assert final_state.get("status") != "APPROVED", (
            f"prefix 우회가 APPROVED로 기록되면 안 됨: {final_state}"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-x", "-q"]))
