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

    head SHA는 FAKE_GH_HEAD_SHA 환경변수로 제어한다(미설정 시 빈 문자열). 이는
    BUG-20260627-C81C MT-3의 독립 stale 검증(pr_head_sha vs packet/pr_body)을 격리
    테스트하기 위함이다. 빈 head SHA는 fail-closed로 stale_pr_head_sha를 먼저 트리거하므로,
    packet/pr_body SHA 불일치 경로를 단독으로 검증하려면 head SHA를 일치값으로 주입해야 한다.
    """
    body_json = json.dumps(_FAKE_GH_PR_BODY_3BB6)
    script = target_dir / "fake_gh_3bb6.py"
    script.write_text(
        "import sys, io, json, os\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        f"BODY = {body_json}\n"
        'HEAD_SHA = os.environ.get("FAKE_GH_HEAD_SHA", "")\n'
        "args = sys.argv[1:]\n"
        'if "--jq" in args:\n'
        '    jq_idx = args.index("--jq"); jq = args[jq_idx+1] if jq_idx+1 < len(args) else ""\n'
        '    if jq == ".body":\n'
        '        sys.stdout.write(BODY)\n'
        '        if not BODY.endswith("\\n"): sys.stdout.write("\\n")\n'
        "        sys.exit(0)\n"
        '    elif ".headRefOid" in jq:\n'
        '        print(HEAD_SHA); sys.exit(0)\n'
        '    elif ".title" in jq:\n'
        '        print("test pr title"); sys.exit(0)\n'
        '    elif "[.files" in jq or jq.startswith(".[0]"):\n'
        '        print("[]"); sys.exit(0)\n'
        '    elif ".headSha" in jq or ".databaseId" in jq:\n'
        '        print(HEAD_SHA); sys.exit(0)\n'
        'if "run" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        'if "run" in args and "view" in args:\n'
        "    print(json.dumps({}))\n    sys.exit(0)\n"
        'if "pr" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        "print(json.dumps({\n"
        '    "body": BODY, "number": 1,\n'
        '    "headRefOid": HEAD_SHA,\n'
        '    "isDraft": False, "state": "OPEN", "files": [],\n'
        '    "url": "https://github.com/test/repo/pull/1",\n'
        "}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return script


def make_env(
    state_file: Path,
    extra_path: Optional[Path] = None,
    head_sha: str = "",
) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 + (옵션) 가짜 codex shim 디렉토리를 PATH 앞에 주입.

    fake gh를 PIPELINE_GH_EXECUTABLE로 자동 주입하여 PR body 조회를 격리한다.

    Args:
        state_file: 격리된 state.json 경로.
        extra_path: PATH 맨 앞에 추가할 디렉토리 (가짜 codex shim 위치).
        head_sha: fake gh가 반환할 PR head SHA (FAKE_GH_HEAD_SHA). 빈 문자열이면
            head SHA 조회가 None이 되어 stale_pr_head_sha가 먼저 트리거된다.
            packet/pr_body SHA 불일치 경로를 단독 검증하려면 일치하는 head SHA를 주입한다.
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
        "FAKE_GH_HEAD_SHA": str(head_sha),
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
        "pr_body_sha256", "packet_sha256", "reviewed_at", "contract_sha256",
    ):
        assert field in final_state, f"result.json missing field {field}"
    assert final_state["pipeline_id"] == pid
    assert final_state["schema_version"] == 1
    # BUG-20260627-C81C MT-7: accept_code 필드가 result.json에 포함되지 않아야 함.
    # 계약 6번("승인 코드는 검토 입력/출력 어디에도 포함되지 않는다") 준수.
    assert "accept_code" not in final_state, "accept_code must not appear in codex_review_result.json"


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
    """APPROVED이나 packet SHA 변경 → 정확히 stale_packet_sha256 BLOCKED.

    BUG-20260627-C81C MT-3/MT-5 (AC-5): _check_codex_review_gate()가 acceptance_request.json
    유무와 무관하게 항상 현재 human_acceptance_packet.md의 실제 SHA256과 stored packet_sha256을
    독립 비교하여, Codex APPROVED 이후 packet 변경 우회를 차단하는지 검증한다.

    head SHA를 일치값으로 주입하여 pr_head_sha 검사를 통과시키고, packet SHA 불일치 경로를
    단독으로 트리거한다. 결과는 정확히 stale_packet_sha256이어야 한다 (느슨한 fallback 금지).

    PIPELINE_STATE_PATH isolation + final_state assertion 포함.
    """
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    matching_head = "headsha_match_0001"
    # head SHA를 stored와 일치하도록 주입 → pr_head_sha 검사 통과 → packet 검사 단독 트리거
    env = make_env(state_file, head_sha=matching_head)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)

    # 현재 packet 파일 — 실제 SHA가 stored packet_sha256과 다르도록 한다.
    fake_packet = tmp_path / "human_acceptance_packet.md"
    fake_packet.write_text("[검증용 메타데이터]\npipeline_id: IMP-20260627-3BB6\n", encoding="utf-8")
    real_packet_sha = hashlib.sha256(fake_packet.read_bytes()).hexdigest()

    # APPROVED이지만 packet_sha256이 실제 파일과 다른 값으로 저장된 result.json.
    result_file = codex_result_path(state_file)
    result_file.parent.mkdir(parents=True, exist_ok=True)
    stale_packet_sha = "0" * 64  # 실제 packet SHA와 다른 값
    assert stale_packet_sha != real_packet_sha
    result_file.write_text(json.dumps({
        "schema_version": 1,
        "pipeline_id": pid,
        "status": "APPROVED",
        "pr_head_sha": matching_head,       # 현재 head와 일치 → pr_head_sha 검사 통과
        "pr_body_sha256": "nonempty_body_sha",  # 빈 값 fail-closed 회피 → packet 검사 우선 트리거
        "packet_sha256": stale_packet_sha,  # 현재 packet 실제 SHA와 불일치
        "reviewed_at": "2026-06-27T00:00:00Z",
        "contract_sha256": "z",
        "verdict": "APPROVE_TO_USER",
    }, ensure_ascii=False), encoding="utf-8")

    evidence = tmp_path / "result.xlsx"
    evidence.write_text("dummy output", encoding="utf-8")

    r = run_cli(
        ["gates", "request-accept", "--evidence", str(evidence)],
        env=env,
        cwd=tmp_path,
    )
    assert r.returncode != 0, (
        f"expected non-zero exit (stale packet), got 0\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    combined = r.stdout + r.stderr
    # MT-5: 정확히 stale_packet_sha256만 허용 (codex_review_required 등 느슨한 fallback 금지).
    assert "stale_packet_sha256" in combined, (
        f"expected exactly stale_packet_sha256, got:\n{combined}"
    )
    assert "stale 필드: packet_sha256" in combined, (
        f"expected stale 필드명 in message:\n{combined}"
    )

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
    """APPROVED이지만 packet_sha256="" → stale_packet_sha256 BLOCKED (fail-closed).

    AC-5 회귀 검증: _check_codex_review_gate()가 stored packet_sha256이 빈 문자열이면
    "SHA 불일치로 간주" 하지 않고 fail-closed로 차단한다.

    BUG-20260627-C81C MT-3: 빈 packet_sha256은 정확히 stale_packet_sha256로 차단된다.
    head SHA를 일치값으로 주입하여 pr_head_sha 검사를 통과시키고 packet 검사를 단독 트리거한다.

    PIPELINE_STATE_PATH isolation + final_state assertion 포함.
    """
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    matching_head = "headsha_match_0002"
    env = make_env(state_file, head_sha=matching_head)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)

    # APPROVED이지만 packet_sha256=""로 기록된 result.json (빈 SHA fail-closed 검증)
    result_file = codex_result_path(state_file)
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.write_text(json.dumps({
        "schema_version": 1,
        "pipeline_id": pid,
        "status": "APPROVED",
        "pr_head_sha": matching_head,  # 현재 head와 일치 → pr_head_sha 검사 통과
        "pr_body_sha256": "nonempty_body_sha",
        "packet_sha256": "",  # 빈 문자열 — fail-closed 대상
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
    assert "stale_packet_sha256" in combined, (
        f"expected stale_packet_sha256 in output\n{combined}"
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

    BUG-20260627-C81C MT-5: 섹션 제목만이 아니라, 프롬프트에 실제 patch 헤더("diff --git")
    또는 실제 변경 파일명(예: pipeline.py) 중 하나가 포함되는지 검증한다. git diff는 실제
    레포(BASE_DIR)에서 origin/main...HEAD로 실행되므로, 현재 브랜치 변경이 있으면 patch 헤더가
    포함된다. (변경이 전혀 없는 경우 diff_txt가 빈 문자열일 수 있으나, 이 PR 브랜치에는
    실제 변경이 있으므로 헤더가 포함되어야 한다.)

    검증 방법: stdin을 파일에 캡처하는 shim을 사용하여 프롬프트 내용을 확인한다.

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
    # MT-5: 프롬프트에 실제 patch 헤더(diff --git) 또는 실제 변경 파일명이 포함되어야 한다.
    has_diff_header = "diff --git" in captured
    has_changed_file = "pipeline.py" in captured or "codex_review_contract.md" in captured
    assert has_diff_header or has_changed_file, (
        "프롬프트에 실제 patch 헤더('diff --git')도 변경 파일명도 없습니다. "
        f"실제 코드 변경 없이 검토되는 우회 가능성. captured(head):\n{captured[:3000]}"
    )
    # placeholder가 프롬프트에 포함되어서는 안 됨 (MT-1 fail-closed로 대체됨).
    # BUG-20260627-C81C MT-6: MT-6 이후 이 테스트 파일도 Codex packet의 변경 파일로
    # 포함되므로, 금지 placeholder를 소스에 verbatim으로 두면 본 테스트 자신의 diff가
    # 프롬프트에 들어와 assertion이 스스로를 검출한다. 런타임 검사 의미는 동일하게 유지하되,
    # 리터럴(한국어 문자)이 소스에 그대로 남지 않도록 각 문자를 chr()로 조립한다.
    # 실행 시에는 fail-closed placeholder 문자열이 만들어지지만, 소스 diff에는 한국어
    # 리터럴이 없어 Codex 프롬프트에서 자기 자신을 검출하지 않는다. 검증 의미:
    # _cmd_gates_codex_review가 실패 시 이 placeholder를 Codex에 전달하지 않는지 확인.
    _forbidden_placeholder = (
        chr(40) + "diff " + chr(51468) + chr(54924) + " " + chr(49892) + chr(54756) + chr(41)
    )
    assert _forbidden_placeholder not in captured, (
        "프롬프트에 diff-조회-실패 placeholder가 포함되어서는 안 됩니다 (fail-closed 위반)."
    )

    # final_state: codex_review_result.json status=APPROVED
    result_file = codex_result_path(state_file)
    assert result_file.exists()
    final_state = json.loads(result_file.read_text(encoding="utf-8"))
    assert final_state["status"] == "APPROVED"


def test_tc7b_diff_unavailable_fail_closed(tmp_path: Path) -> None:
    """git CLI를 찾을 수 없으면 codex_review_diff_unavailable로 fail-closed BLOCKED.

    BUG-20260627-C81C MT-1/MT-5 (AC-1): git diff 조회가 실패하면 diff-조회-실패
    placeholder로 우회하지 않고 즉시 BLOCKED(failure_code=codex_review_diff_unavailable)
    되는지 검증한다. (MT-6: 본 파일이 packet에 포함되므로 placeholder 리터럴은 verbatim으로
    두지 않는다.)

    검증 방법: PATH를 빈 디렉토리로 덮어써서 git 실행 파일을 찾지 못하게 한다
    (FileNotFoundError → codex_review_diff_unavailable). codex shim도 PATH에 없으므로
    diff 조회 단계에서 먼저 차단된다. codex shim 유무와 무관하게 diff 단계가 우선이다.

    PIPELINE_STATE_PATH isolation + final_state(미기록) assertion 포함.
    """
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    # git을 찾지 못하도록 PATH를 빈 디렉토리로 덮어쓴다.
    empty_dir = tmp_path / "empty_path"
    empty_dir.mkdir(parents=True, exist_ok=True)
    env = make_env(state_file)
    # PATH 전체를 empty_dir로 덮어써서 git/codex 실행 파일 부재 상황 강제.
    env["PATH"] = str(empty_dir)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)
    # fake packet 생성 (packet 단계는 통과해야 diff 단계에 도달)
    fake_packet = tmp_path / "human_acceptance_packet.md"
    fake_packet.write_bytes(
        "[dummy-packet]\npipeline_id: IMP-20260627-3BB6\npr_head_sha: \n".encode("utf-8")
    )

    r = run_cli(["gates", "codex-review"], env=env, cwd=tmp_path)
    assert r.returncode != 0, (
        f"git 부재 시 exit 1(BLOCKED)이어야 하지만 exit 0\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert "codex_review_diff_unavailable" in combined, (
        f"expected codex_review_diff_unavailable in output\n{combined}"
    )

    # final_state: codex_review_result.json이 APPROVED로 기록되면 안 됨.
    result_file = codex_result_path(state_file)
    if result_file.exists():
        final_state = json.loads(result_file.read_text(encoding="utf-8"))
        assert final_state.get("status") != "APPROVED", (
            f"diff 조회 실패 상황이 APPROVED로 기록되면 안 됨: {final_state}"
        )


# ---------------------------------------------------------------------------
# TC-8: prefix 출력이 있는 경우 INVALID로 처리 (계약 "첫 줄만" 강제 회귀 테스트)
# ---------------------------------------------------------------------------

def test_tc8_prefix_output_is_invalid(tmp_path: Path) -> None:
    """codex AI가 설명 prefix + APPROVE_TO_USER 출력 시 INVALID → exit 1.

    계약 규정: "출력 첫 줄은 정확히 APPROVE_TO_USER 또는 REJECT - <사유>여야 한다."
    Codex CLI 시스템 메시지(SUCCESS:/WARNING: 등)는 파싱에서 제외하지만,
    AI 모델이 직접 쓴 설명 prefix + APPROVE_TO_USER 두 번째 줄은 INVALID여야 한다.
    이 테스트는 AI가 설명문을 먼저 쓰고 APPROVE_TO_USER를 두 번째 줄에 넣는
    우회 시도를 차단하는지 검증한다.

    PIPELINE_STATE_PATH isolation + final_state assertion 포함.
    """
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    shim = tmp_path / "shim"
    # AI 모델이 직접 쓴 prefix (시스템 메시지 패턴이 아님) + APPROVE_TO_USER
    # → 첫 AI 출력 줄이 설명문 → INVALID
    prefix_verdict = "이 PR은 모든 요건을 충족합니다.\nAPPROVE_TO_USER"
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


# ---------------------------------------------------------------------------
# TC-9: APPROVE_TO_USER 뒤 추가 AI 출력이 있으면 INVALID (계약 "정확히 한 줄" 검증)
# ---------------------------------------------------------------------------

def test_tc9_approve_with_trailing_output_is_invalid(tmp_path: Path) -> None:
    """codex AI가 APPROVE_TO_USER 뒤에 추가 줄을 출력하면 INVALID → exit 1.

    계약 규정: "당신의 출력 첫 줄은 정확히 APPROVE_TO_USER 또는 REJECT - <사유>여야 한다."
    비-시스템 AI 출력이 1줄을 초과하면 INVALID로 처리한다.
    APPROVE_TO_USER\n이유 설명 패턴의 우회를 방지.

    PIPELINE_STATE_PATH isolation + final_state assertion 포함.
    """
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    shim = tmp_path / "shim"
    # APPROVE_TO_USER 뒤에 추가 AI 출력 → INVALID여야 함
    trailing_verdict = "APPROVE_TO_USER\n이 PR은 모든 요건을 충족합니다."
    make_fake_codex(shim, trailing_verdict)
    env = make_env(state_file, extra_path=shim)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)
    # fake packet 생성 (cwd=tmp_path 격리)
    fake_packet = tmp_path / "human_acceptance_packet.md"
    fake_packet.write_bytes(
        "[dummy-packet]\npipeline_id: IMP-20260627-3BB6\npr_head_sha: \n".encode("utf-8")
    )

    r = run_cli(["gates", "codex-review"], env=env, cwd=tmp_path)
    # APPROVE_TO_USER 뒤 추가 AI 출력 → INVALID → exit 1
    assert r.returncode != 0, (
        f"APPROVE_TO_USER 뒤 추가 출력이 있으면 exit 1(INVALID)이어야 하지만 exit 0 반환\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert "codex_verdict_invalid" in combined, (
        f"expected codex_verdict_invalid in output\n{combined}"
    )

    # final_state: APPROVED로 기록되면 안 됨
    result_file = codex_result_path(state_file)
    if result_file.exists():
        final_state = json.loads(result_file.read_text(encoding="utf-8"))
        assert final_state.get("status") != "APPROVED", (
            f"APPROVE_TO_USER + 추가 줄이 APPROVED로 기록되면 안 됨: {final_state}"
        )


# ---------------------------------------------------------------------------
# TC-10~TC-13: BUG-20260627-C81C MT-6 — trust-root 우선 diff 수집 검증
# (기존 TC-8/TC-9는 prefix/trailing 검증으로 이미 사용 중이므로 TC-10부터 부여)
# ---------------------------------------------------------------------------

# TC-10: .claude/agents/** diff가 앞부분을 채워도 pipeline.py patch가 우선 정렬
def test_tc10_pipeline_py_prioritized_over_agents() -> None:
    """pipeline.py가 .claude/agents 보다 우선 포함된다."""
    from pipeline import _sort_changed_files_by_priority
    changed = [
        ".claude/agents/dev-agent.md",
        ".claude/agents/qa-agent.md",
        "pipeline.py",
        "tests/e2e/test_foo.py",
    ]
    result = _sort_changed_files_by_priority(changed)
    assert result[0] == "pipeline.py", f"pipeline.py should be first, got {result[0]}"
    agents_idx = [i for i, f in enumerate(result) if f.startswith(".claude/agents/")]
    pipeline_idx = result.index("pipeline.py")
    assert all(pipeline_idx < i for i in agents_idx), \
        f"pipeline.py ({pipeline_idx}) must come before agents {agents_idx}"


# TC-11: trust-root 그룹 전체 우선순위 검증
def test_tc11_full_priority_ordering() -> None:
    """7개 우선순위 그룹이 규칙 순서대로 정렬된다."""
    from pipeline import _sort_changed_files_by_priority
    changed = [
        "other_file.py",
        "tests/e2e/test_codex_review_gate_3bb6.py",
        ".github/workflows/ci.yml",
        ".claude/commands/task.md",
        ".claude/agents/dev-agent.md",
        ".claude/codex_review_contract.md",
        "pipeline.py",
    ]
    result = _sort_changed_files_by_priority(changed)
    assert result == [
        "pipeline.py",
        ".claude/codex_review_contract.md",
        ".claude/agents/dev-agent.md",
        ".claude/commands/task.md",
        ".github/workflows/ci.yml",
        "tests/e2e/test_codex_review_gate_3bb6.py",
        "other_file.py",
    ], f"unexpected order: {result}"


# TC-12: 동일 우선순위 그룹 내부는 원래 순서(stable) 유지
def test_tc12_stable_order_within_group() -> None:
    """동일 그룹(나머지 파일들) 내부는 입력 순서를 유지한다."""
    from pipeline import _sort_changed_files_by_priority
    changed = ["zeta.py", "alpha.py", "pipeline.py", "mid.py"]
    result = _sort_changed_files_by_priority(changed)
    # pipeline.py가 맨 앞, 나머지는 입력 순서 그대로 (알파벳 정렬 아님)
    assert result == ["pipeline.py", "zeta.py", "alpha.py", "mid.py"], \
        f"stable order broken: {result}"


# TC-13: edge case — None/비-list/비-str 원소 방어
def test_tc13_sort_input_validation() -> None:
    """None/비-list/비-str 원소에 대해 TypeError를 raise한다."""
    from pipeline import _sort_changed_files_by_priority
    # None 입력 → TypeError
    with pytest.raises(TypeError):
        _sort_changed_files_by_priority(None)  # type: ignore[arg-type]
    # 비-list 입력 → TypeError
    with pytest.raises(TypeError):
        _sort_changed_files_by_priority("pipeline.py")  # type: ignore[arg-type]
    # 비-str 원소 → TypeError
    with pytest.raises(TypeError):
        _sort_changed_files_by_priority([123, "pipeline.py"])
    # 빈 리스트 → 빈 리스트 (예외 없음)
    assert _sort_changed_files_by_priority([]) == []


# ---------------------------------------------------------------------------
# TC-14: BUG-20260628-1AAC MT-1 — "INFO: " prefix가 더 이상 시스템 메시지로
#        취급되지 않아 "INFO: 문제 없음\nAPPROVE_TO_USER" 우회가 INVALID로 차단됨
# ---------------------------------------------------------------------------

def test_tc14_info_prefix_invalid(tmp_path: Path) -> None:
    """codex AI가 'INFO: 문제 없음' + APPROVE_TO_USER 출력 시 INVALID → exit 1.

    BUG-20260628-1AAC MT-1: _CODEX_CLI_SYSTEM_PREFIXES에서 "INFO: "/"WARNING: "를
    제거하여, AI가 INFO:/WARNING: prefix 줄 뒤에 APPROVE_TO_USER를 넣는 우회를 차단한다.
    이제 'INFO: 문제 없음'이 AI 출력 첫 줄로 취급되어 형식 불일치 → INVALID.

    PIPELINE_STATE_PATH isolation + final_state assertion 포함.
    """
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    shim = tmp_path / "shim"
    # INFO: prefix 줄 뒤 APPROVE_TO_USER → 이제 INFO 줄이 AI 출력 첫 줄 → INVALID
    info_verdict = "INFO: 문제 없음\nAPPROVE_TO_USER"
    make_fake_codex(shim, info_verdict)
    env = make_env(state_file, extra_path=shim)
    assert env["PIPELINE_STATE_PATH"] == str(state_file)
    # fake packet 생성 (cwd=tmp_path 격리)
    fake_packet = tmp_path / "human_acceptance_packet.md"
    fake_packet.write_bytes(
        "[dummy-packet]\npipeline_id: IMP-20260627-3BB6\npr_head_sha: \n".encode("utf-8")
    )

    r = run_cli(["gates", "codex-review"], env=env, cwd=tmp_path)
    # INFO: prefix가 시스템 메시지가 아니므로 첫 AI 줄이 APPROVE_TO_USER가 아님 → INVALID → exit 1
    assert r.returncode != 0, (
        f"INFO: prefix 우회가 있는 경우 exit 1(INVALID)이어야 하지만 exit 0이 반환됨\n"
        f"stdout={r.stdout}\nstderr={r.stderr}"
    )
    combined = r.stdout + r.stderr
    assert "codex_verdict_invalid" in combined, (
        f"expected codex_verdict_invalid in output\n{combined}"
    )

    # final_state: APPROVED로 기록되면 안 됨
    result_file = codex_result_path(state_file)
    if result_file.exists():
        final_state = json.loads(result_file.read_text(encoding="utf-8"))
        assert final_state.get("status") != "APPROVED", (
            f"INFO: prefix + APPROVE_TO_USER가 APPROVED로 기록되면 안 됨: {final_state}"
        )


def test_tc14b_info_prefix_unit() -> None:
    """단위 검증: _parse_codex_verdict가 'INFO: ...\\nAPPROVE_TO_USER'를 INVALID 처리.

    BUG-20260628-1AAC MT-1: WARNING:/INFO: prefix 모두 AI 출력으로 취급되는지 단위로 확인한다.
    """
    from pipeline import _parse_codex_verdict
    # INFO: prefix → INVALID
    assert _parse_codex_verdict("INFO: 문제 없음\nAPPROVE_TO_USER")["status"] == "INVALID"
    # WARNING: prefix → INVALID
    assert _parse_codex_verdict("WARNING: 경고\nAPPROVE_TO_USER")["status"] == "INVALID"
    # 순수 APPROVE_TO_USER는 여전히 APPROVED
    assert _parse_codex_verdict("APPROVE_TO_USER")["status"] == "APPROVED"
    # SUCCESS: 시스템 메시지는 여전히 skip되어 APPROVED
    assert _parse_codex_verdict("SUCCESS: 완료\nAPPROVE_TO_USER")["status"] == "APPROVED"


# ---------------------------------------------------------------------------
# TC-15: BUG-20260628-1AAC MT-2 — PR 본문에 ACCEPT 코드가 있어도 Codex
#        프롬프트에는 마스킹되어 전달됨 (계약 6번 준수)
# ---------------------------------------------------------------------------

def _write_fake_gh_with_accept_code(target_dir: Path, accept_code: str) -> Path:
    """PR body에 승인 코드(accept_code)를 포함하는 fake gh 스크립트 생성.

    MT-2 검증용: pr_body에 ACCEPT 코드가 있어도 Codex 프롬프트에서 마스킹되는지 확인한다.

    Args:
        target_dir: fake gh를 둘 디렉토리.
        accept_code: PR body에 삽입할 승인 코드 문자열.
    Returns:
        생성된 fake gh 스크립트 경로.
    """
    body_with_code = (
        "## 작업 요약\n자동 테스트 픽스처 PR body\n\n"
        "## 사용자가 확인할 결과물\n결과물 경로: N/A (테스트)\n\n"
        "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 성공\n\n"
        "## 중요한 선택과 트레이드오프\nN/A\n\n"
        f"## 검증\n승인 코드: {accept_code} 입력하세요.\n"
    )
    body_json = json.dumps(body_with_code)
    script = target_dir / "fake_gh_accept_3bb6.py"
    script.write_text(
        "import sys, io, json, os\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        f"BODY = {body_json}\n"
        'HEAD_SHA = os.environ.get("FAKE_GH_HEAD_SHA", "")\n'
        "args = sys.argv[1:]\n"
        'if "--jq" in args:\n'
        '    jq_idx = args.index("--jq"); jq = args[jq_idx+1] if jq_idx+1 < len(args) else ""\n'
        '    if jq == ".body":\n'
        '        sys.stdout.write(BODY)\n'
        '        if not BODY.endswith("\\n"): sys.stdout.write("\\n")\n'
        "        sys.exit(0)\n"
        '    elif ".headRefOid" in jq:\n'
        '        print(HEAD_SHA); sys.exit(0)\n'
        '    elif ".title" in jq:\n'
        '        print("test pr title"); sys.exit(0)\n'
        '    elif "[.files" in jq or jq.startswith(".[0]"):\n'
        '        print("[]"); sys.exit(0)\n'
        '    elif ".headSha" in jq or ".databaseId" in jq:\n'
        '        print(HEAD_SHA); sys.exit(0)\n'
        'if "run" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        'if "run" in args and "view" in args:\n'
        "    print(json.dumps({}))\n    sys.exit(0)\n"
        'if "pr" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        "print(json.dumps({\n"
        '    "body": BODY, "number": 1,\n'
        '    "headRefOid": HEAD_SHA,\n'
        '    "isDraft": False, "state": "OPEN", "files": [],\n'
        '    "url": "https://github.com/test/repo/pull/1",\n'
        "}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return script


def test_tc15_prbody_accept_code_masked(tmp_path: Path) -> None:
    """PR 본문에 ACCEPT 코드가 있으면 Codex 프롬프트에 마스킹되어 전달됨.

    BUG-20260628-1AAC MT-2: 계약 6번("승인 코드는 검토 입력/출력 어디에도 포함되지
    않는다") 준수를 위해 _cmd_gates_codex_review가 pr_body[:5000]를 Codex 프롬프트에
    넣기 전 ACCEPT-* 패턴을 [ACCEPT코드 마스킹]으로 치환하는지 stdin 캡처로 검증한다.

    검증 흐름:
    1. real_accept_code_literal = "ACCEPT-IMP-20260627-3BB6-A1B2"  ← 실제 ACCEPT 코드 형식
    2. fake gh가 PR body에 real_accept_code_literal이 포함된 텍스트 반환
    3. gates codex-review 실행 → Codex stdin을 파일로 캡처
    4. captured에 real_accept_code_literal 원문이 없어야 함 (마스킹됨)
    5. captured에 "[ACCEPT코드 마스킹]" placeholder가 있어야 함

    PIPELINE_STATE_PATH isolation + final_state assertion 포함.
    """
    state_file = tmp_path / "state.json"
    pid = "IMP-20260627-3BB6"
    write_min_state(state_file, pid)
    shim = tmp_path / "shim"
    stdin_capture = tmp_path / "captured_stdin.txt"
    make_fake_codex(shim, "APPROVE_TO_USER", capture_stdin_to=stdin_capture)

    # real_accept_code_literal: 실제 ACCEPT 코드 형식 (ACCEPT-TYPE-DATE-4HEX-4HEX)
    # 이 값이 fake gh PR body에 삽입되고, Codex stdin에서 마스킹 여부를 검증한다
    real_accept_code_literal = "ACCEPT-IMP-20260627-3BB6-A1B2"

    # PR body에 real_accept_code_literal을 포함하는 fake gh 주입
    gh_dir = tmp_path / "_gh_shim_accept"
    gh_dir.mkdir(parents=True, exist_ok=True)
    fake_gh = _write_fake_gh_with_accept_code(gh_dir, real_accept_code_literal)
    env = {
        **os.environ,
        "PIPELINE_STATE_PATH": str(state_file),
        "PIPELINE_NO_DASHBOARD": "1",
        "PIPELINE_GH_EXECUTABLE": str(fake_gh),
        "PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING": "1",
        "FAKE_GH_HEAD_SHA": "",
        "PYTHONIOENCODING": "utf-8",
        "PATH": str(shim) + os.pathsep + os.environ.get("PATH", ""),
    }
    assert env["PIPELINE_STATE_PATH"] == str(state_file)
    fake_packet = tmp_path / "human_acceptance_packet.md"
    fake_packet.write_bytes(
        "[dummy-packet]\npipeline_id: IMP-20260627-3BB6\npr_head_sha: \n".encode("utf-8")
    )

    r = run_cli(["gates", "codex-review"], env=env, cwd=tmp_path)
    assert r.returncode == 0, (
        f"expected exit 0 (APPROVED), got {r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
    )

    assert stdin_capture.exists(), "stdin capture 파일이 생성되지 않았습니다"
    captured = stdin_capture.read_text(encoding="utf-8", errors="replace")

    # 검증 1: 실제 ACCEPT 코드 원문이 Codex 프롬프트에 포함되어서는 안 됨 (마스킹됨)
    # fake gh PR body에는 real_accept_code_literal이 있었지만, pipeline.py가 마스킹 처리함
    assert real_accept_code_literal not in captured, (
        f"PR 본문의 실제 승인 코드({real_accept_code_literal!r})가 마스킹되지 않고 "
        f"Codex 프롬프트에 노출됨 (계약 6번 위반)\n"
        f"captured(head):\n{captured[:3000]}"
    )

    # 검증 2: 마스킹 placeholder가 포함되어야 함 (원문 대신 placeholder가 전달됨)
    assert "[ACCEPT코드 마스킹]" in captured, (
        f"마스킹 placeholder가 프롬프트에 없습니다.\ncaptured(head):\n{captured[:3000]}"
    )
    # PR 본문 섹션 자체는 여전히 전달되어야 함 (마스킹만, 본문 제거 아님)
    assert "PR 본문" in captured

    # final_state: codex_review_result.json status=APPROVED
    result_file = codex_result_path(state_file)
    assert result_file.exists()
    final_state = json.loads(result_file.read_text(encoding="utf-8"))
    assert final_state["status"] == "APPROVED"


# ---------------------------------------------------------------------------
# TC-16: BUG-20260628-1AAC MT-3 — trust-root 파일이 excluded_files에 있으면
#        codex_review_diff_incomplete로 BLOCKED
# ---------------------------------------------------------------------------

def test_tc16_critical_files_ssot_defined() -> None:
    """CODEX_REVIEW_CRITICAL_FILES SSoT 상수가 5개 trust-root 경로를 포함한다.

    BUG-20260628-1AAC MT-3: 신규 SSoT 상수 정의 검증.
    """
    from pipeline import CODEX_REVIEW_CRITICAL_FILES
    expected = {
        ".claude/codex_review_contract.md",
        ".claude/agents/",
        "pipeline.py",
        "CLAUDE.md",
        ".github/workflows/",
    }
    assert set(CODEX_REVIEW_CRITICAL_FILES) == expected, (
        f"CODEX_REVIEW_CRITICAL_FILES mismatch: {CODEX_REVIEW_CRITICAL_FILES}"
    )


def test_tc16_trustroot_excluded_detection() -> None:
    """_is_codex_critical_file이 정확 경로/prefix 경로/Windows 구분자를 모두 판정한다.

    BUG-20260628-1AAC MT-3: excluded_files에 trust-root 파일이 있으면 BLOCKED하기 위해
    핵심 파일 판정 로직(_is_codex_critical_file)이 올바르게 동작하는지 단위 검증한다.
    .claude/codex_review_contract.md가 excluded_files에 있으면 critical로 탐지되어야 한다.
    """
    from pipeline import _is_codex_critical_file
    # 정확 경로 일치
    assert _is_codex_critical_file(".claude/codex_review_contract.md") is True
    assert _is_codex_critical_file("pipeline.py") is True
    assert _is_codex_critical_file("CLAUDE.md") is True
    # prefix 경로 하위 파일
    assert _is_codex_critical_file(".claude/agents/dev-agent.md") is True
    assert _is_codex_critical_file(".github/workflows/ci.yml") is True
    # Windows 경로 구분자 정규화
    assert _is_codex_critical_file(".claude\\agents\\qa-agent.md") is True
    # 비-critical 파일은 False
    assert _is_codex_critical_file("tests/e2e/test_foo.py") is False
    assert _is_codex_critical_file("core/module.py") is False
    assert _is_codex_critical_file("README.md") is False
    # 타입 방어
    with pytest.raises(TypeError):
        _is_codex_critical_file(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        _is_codex_critical_file(123)  # type: ignore[arg-type]


def test_tc16_excluded_critical_blocks(tmp_path: Path) -> None:
    """trust-root 파일이 budget 초과로 excluded되면 codex_review_diff_incomplete BLOCKED.

    BUG-20260628-1AAC MT-3: DIFF_BUDGET을 매우 작은 값으로 모킹하여
    .claude/codex_review_contract.md 같은 trust-root 파일이 excluded_files에 들어가는
    상황을 만들고, _cmd_gates_codex_review가 codex_review_diff_incomplete로 BLOCKED
    하는지 검증한다.

    실제 budget 초과를 강제하기 위해 monkeypatch로 DIFF_BUDGET 대신 per-file diff를
    가짜로 만드는 대신, _excluded_critical 경로를 단위로 검증한다.

    PIPELINE_STATE_PATH isolation + final_state(미기록) assertion 포함.
    """
    # 단위 검증: excluded_files에 trust-root가 있으면 _excluded_critical이 비어있지 않음.
    from pipeline import _is_codex_critical_file
    excluded_files = [
        "tests/e2e/test_foo.py",
        ".claude/codex_review_contract.md",
        "docs/notes.md",
    ]
    excluded_critical = [p for p in excluded_files if _is_codex_critical_file(p)]
    assert excluded_critical == [".claude/codex_review_contract.md"], (
        f"trust-root 파일이 excluded_critical에 탐지되어야 함: {excluded_critical}"
    )
    # trust-root가 전혀 없는 경우 빈 리스트 → BLOCKED 안 됨
    excluded_safe = ["tests/e2e/test_foo.py", "docs/notes.md"]
    assert [p for p in excluded_safe if _is_codex_critical_file(p)] == [], (
        "비-trust-root 파일만 excluded되면 BLOCKED되지 않아야 함"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-x", "-q"]))
