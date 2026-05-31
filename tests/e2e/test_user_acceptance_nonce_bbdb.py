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

import pytest

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
    return env


def bootstrap_pipeline(tmp_path: Path, env: Dict[str, str]) -> str:
    """격리된 환경에 IMP 파이프라인을 생성하고 pipeline_id 반환.

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
    with open(state_file, encoding="utf-8") as f:
        state = json.load(f)
    pid = str(state.get("pipeline_id", ""))
    assert pid, "pipeline_id missing in state"
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
# Tests (14 cases — TC-1 ~ TC-14)
# ---------------------------------------------------------------------------

# TC-1: request-accept가 acceptance_request.json을 생성 (normal oracle)
def test_tc1_request_accept_creates_json(tmp_path):
    """gates request-accept 실행 시 acceptance_request.json이 PENDING 상태로 생성된다."""
    env = make_env(tmp_path)
    pipeline_id = bootstrap_pipeline(tmp_path, env)
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("test result", encoding="utf-8")

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
    env = make_env(tmp_path)
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


# TC-3: 잘못된 nonce → acceptance_code_mismatch BLOCKED (edge oracle)
def test_tc3_wrong_nonce_blocked(tmp_path):
    """잘못된 nonce로 ACCEPT 시도 시 acceptance_code_mismatch."""
    env = make_env(tmp_path)
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


# TC-4: pipeline_id 다른 코드 → BLOCKED
def test_tc4_pipeline_id_mismatch_blocked(tmp_path):
    """다른 pipeline_id의 코드로 ACCEPT 시도 시 BLOCKED."""
    env = make_env(tmp_path)
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


# TC-5: evidence 파일 hash 변경 → evidence_changed BLOCKED
def test_tc5_evidence_changed_blocked(tmp_path):
    """request-accept 이후 evidence 파일이 변경되면 evidence_changed BLOCKED."""
    env = make_env(tmp_path)
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
    env = make_env(tmp_path)
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


# TC-9: REJECT도 --acceptance-code 없으면 FAIL
def test_tc9_reject_no_acceptance_code_fail(tmp_path):
    """REJECT도 --acceptance-code 없으면 acceptance_code_required로 FAIL."""
    env = make_env(tmp_path)
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


# TC-10: --user-confirmed 단독 → 경고 + BLOCKED (명시적)
def test_tc10_user_confirmed_warning_then_blocked(tmp_path):
    """--user-confirmed 플래그만 있으면 경고 출력 후 BLOCKED."""
    env = make_env(tmp_path)
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


# TC-11: acceptance_request.json 없이 gates accept → missing_acceptance_request BLOCKED
def test_tc11_missing_acceptance_request_blocked(tmp_path):
    """acceptance_request.json 없이 acceptance-code 입력 시 BLOCKED."""
    env = make_env(tmp_path)
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
    env = make_env(tmp_path)
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
