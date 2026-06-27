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
) -> subprocess.CompletedProcess:
    """`python pipeline.py <args>` 실행 후 CompletedProcess 반환.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess 환경 변수.
        timeout: 초 단위 타임아웃.
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
    )


def make_env(state_file: Path, extra_path: Optional[Path] = None) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 + (옵션) 가짜 codex shim 디렉토리를 PATH 앞에 주입.

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
    env = {
        **os.environ,
        "PIPELINE_STATE_PATH": str(state_file),
        "PIPELINE_NO_DASHBOARD": "1",
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


def make_fake_codex(shim_dir: Path, verdict: str) -> None:
    """PATH에 주입할 가짜 codex 실행 파일을 생성한다.

    `codex exec <prompt>` 호출 시 stdout으로 verdict를 출력하고 exit 0.
    Windows에서는 codex.bat, POSIX에서는 codex 셸 스크립트를 생성한다.

    Args:
        shim_dir: shim을 둘 디렉토리.
        verdict: codex가 출력할 verdict 문자열 (예: APPROVE_TO_USER).
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
    py_shim = shim_dir / "_codex_shim.py"
    py_shim.write_text(
        "import sys, io\n"
        "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')\n"
        "from pathlib import Path\n"
        f"v = Path(r'{verdict_file}').read_text(encoding='utf-8')\n"
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

    r = run_cli(["gates", "codex-review"], env=env)
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

    r = run_cli(["gates", "codex-review"], env=env)
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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-x", "-q"]))
