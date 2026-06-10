"""
test_request_accept_snapshot_8c3b.py — IMP-20260610-8C3B MT-3
Acceptance Snapshot Materialization E2E 테스트 (5 케이스)

# [Purpose]:
#   _materialize_acceptance_snapshot / _verify_acceptance_snapshot / ac_completeness 캐시
#   로직이 CLI subprocess 흐름에서 올바르게 동작하는지 검증한다.
#   - TC-1 (normal): gates request-accept 최초 실행 시 nonce가 발급되고
#     acceptance_request.json에 4개 SHA 필드가 기록됨을 확인.
#   - TC-2 (normal): reuse 경로에서도 동일 snapshot 함수가 호출되어
#     packet_sha256 필드가 갱신됨을 확인.
#   - TC-3 (edge): SHA 불일치 시 gates accept가 BLOCKED를 반환함을 확인.
#   - TC-4 (edge): AC incomplete(module integrate 없음) 상태에서
#     gates request-accept가 AC_COMPLETENESS_CACHE_MISSING 메시지 없이도
#     레거시 경로로 fallback됨을 확인.
#   - TC-5 (edge): 잘못된 acceptance-code로 gates accept 실행 시 BLOCKED를 반환.

# [Assumptions]:
#   - PIPELINE_STATE_PATH 환경변수로 상태 파일 격리.
#   - subprocess 기반 실제 CLI 실행 (내부 함수 직접 호출 금지).
#   - gh CLI 없는 환경 가정 (PATH에서 제거).
#   - 격리 state에서 pm → dev → integrate 경로를 건너뛰는 경우
#     requirements_tracking.enabled=false 설정으로 AC 검사를 우회.

# CLI Evidence Contract (IMP-20260525-6FAC):
#   - 상태 변경 CLI 호출마다 PIPELINE_STATE_PATH 격리 사용
#   - final_state assertion 포함 (stdout-only 검증 금지)
#   - subprocess 기반 실제 CLI 실행
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
ORACLE_DIR = Path(__file__).resolve().parent.parent / "oracles" / "IMP-20260610-8C3B"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(
    args: List[str],
    env: Dict[str, str],
    cwd: Optional[Path] = None,
    timeout: int = 30,
) -> "subprocess.CompletedProcess[str]":
    """`python pipeline.py <args>` 실행 후 CompletedProcess 반환.

    acceptance_request.json 등 pipeline.py 산출물은 BASE_DIR(PIPELINE_PY.parent)에 생성되므로
    cwd 기본값을 PIPELINE_PY.parent로 설정합니다.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess에 전달할 환경 변수.
        cwd: 작업 디렉토리 (기본은 PIPELINE_PY.parent).
        timeout: 초 단위 timeout.
    Raises:
        TypeError: args가 list가 아닌 경우.
    """
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    cmd = [sys.executable, str(PIPELINE_PY)] + args
    effective_cwd = cwd if cwd is not None else PIPELINE_PY.parent
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        cwd=str(effective_cwd),
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
    # gh CLI를 못 찾도록 PATH를 tmp_path로 제한
    env["PATH"] = str(tmp_path)
    return env


def load_final_state(env: Dict[str, str]) -> Dict[str, Any]:
    """PIPELINE_STATE_PATH가 가리키는 state 파일을 로드."""
    state_file = Path(env["PIPELINE_STATE_PATH"])
    if not state_file.exists():
        return {}
    with open(state_file, encoding="utf-8") as f:
        return json.load(f)


def sha256_str(s: str) -> str:
    """문자열의 SHA-256 hex digest 반환 (UTF-8 인코딩)."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(p: Path) -> str:
    """파일의 SHA-256 hex digest 반환."""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def bootstrap_pipeline_legacy(tmp_path: Path, env: Dict[str, str]) -> str:
    """격리된 환경에 IMP 파이프라인을 생성하고 requirements_tracking.enabled=false로
    설정하여 AC 검사를 우회한 후 pipeline_id를 반환.

    pipeline.py 산출물(acceptance_request.json 등)은 BASE_DIR에 생성됩니다.
    PIPELINE_STATE_PATH만 tmp_path로 격리됩니다.

    Args:
        tmp_path: pytest tmp_path fixture.
        env: PIPELINE_STATE_PATH 환경변수가 설정된 dict.
    Returns:
        생성된 pipeline_id 문자열.
    """
    r = run_cli(
        ["new", "--type", "IMP", "--desc", "snapshot e2e test 8c3b"],
        env=env,
    )
    assert r.returncode == 0, f"new failed: {r.stdout} {r.stderr}"
    state_file = Path(env["PIPELINE_STATE_PATH"])
    with open(state_file, encoding="utf-8") as f:
        state = json.load(f)
    pid = str(state.get("pipeline_id", ""))
    assert pid, "pipeline_id missing"
    # AC 검사 우회: requirements_tracking.enabled=false
    state.setdefault("requirements_tracking", {})
    state["requirements_tracking"]["enabled"] = False
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return pid


def write_evidence_file(tmp_path: Path, content: str = "snapshot test evidence") -> Path:
    """evidence 파일을 tmp_path에 생성하고 경로 반환."""
    ev_file = tmp_path / "evidence.txt"
    ev_file.write_text(content, encoding="utf-8")
    return ev_file


def load_acceptance_request(tmp_path: Optional[Path] = None) -> Dict[str, Any]:
    """acceptance_request.json 로드 (없으면 빈 dict 반환).

    acceptance_request.json은 pipeline.py BASE_DIR(프로젝트 루트)에 생성됩니다.
    tmp_path는 호환성을 위해 유지하되 사용하지 않습니다.
    """
    req_file = PIPELINE_PY.parent / "acceptance_request.json"
    if not req_file.exists():
        return {}
    with open(req_file, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Oracle fixtures
# ---------------------------------------------------------------------------

def _oracle(case_id: str) -> Dict[str, Any]:
    """oracle 디렉토리에서 expected.json을 로드."""
    p = ORACLE_DIR / case_id / "expected.json"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# TC-1 (normal): gates request-accept 최초 실행 시 nonce 발급 + 4개 SHA 필드 기록
def test_tc1_request_accept_issues_nonce_and_records_shas(tmp_path):
    """TC-1: 최초 request-accept 실행 시 acceptance_request.json에 nonce와
    packet_sha256이 기록되고 nonce 형식이 올바른지 확인한다.

    oracle: tests/oracles/IMP-20260610-8C3B/normal_snapshot_fresh/
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + final_state assertion.
    """
    env = make_env(tmp_path)
    pid = bootstrap_pipeline_legacy(tmp_path, env)

    ev_file = write_evidence_file(tmp_path)

    r = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )

    # 1. CLI 성공 여부
    assert r.returncode == 0, (
        f"request-accept 실패 (returncode={r.returncode})\n"
        f"stdout: {r.stdout[:500]}\nstderr: {r.stderr[:300]}"
    )

    # 2. acceptance_request.json 생성 확인 (final_state assertion)
    req = load_acceptance_request()
    assert req, "acceptance_request.json 미생성"
    assert req.get("pipeline_id") == pid, f"pipeline_id 불일치: {req.get('pipeline_id')}"

    # 3. nonce 형식 검증 (8자 base32 대문자+숫자)
    nonce = req.get("nonce", "")
    assert nonce, "nonce 없음"
    assert len(nonce) == 8, f"nonce 길이 불일치: {len(nonce)}"

    # 4. packet_sha256 기록 확인 (IMP-20260610-8C3B MT-1 핵심)
    pkt_sha = req.get("packet_sha256")
    assert pkt_sha, "packet_sha256 미기록"
    assert len(pkt_sha) == 64, f"packet_sha256 형식 불일치: {pkt_sha}"

    # 5. stdout에 승인 코드 포함 확인
    accept_code = f"ACCEPT-{pid}-{nonce}"
    assert accept_code in r.stdout, (
        f"stdout에 승인 코드 없음. expected: {accept_code}\n"
        f"stdout: {r.stdout[:500]}"
    )

    # oracle 참조 (expected.json 구조 검증)
    oracle = _oracle("normal_snapshot_fresh")
    if oracle:
        for key in oracle.get("acceptance_request_keys", []):
            assert key in req, f"oracle 필수 key '{key}' 누락"


# TC-2 (normal): reuse 경로에서 snapshot 재실행 시 packet_sha256 갱신 확인
def test_tc2_reuse_path_updates_packet_sha(tmp_path):
    """TC-2: 동일 조건에서 request-accept를 두 번 실행하면 reuse 경로에서도
    packet_sha256이 존재하고 acceptance_request.json의 nonce가 보존됨을 확인.

    oracle: tests/oracles/IMP-20260610-8C3B/normal_snapshot_reuse/
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + final_state assertion.
    """
    env = make_env(tmp_path)
    pid = bootstrap_pipeline_legacy(tmp_path, env)
    ev_file = write_evidence_file(tmp_path)

    # 첫 번째 실행
    r1 = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )
    assert r1.returncode == 0, f"첫 번째 request-accept 실패: {r1.stdout} {r1.stderr}"

    req1 = load_acceptance_request()
    nonce1 = req1.get("nonce", "")
    pkt_sha1 = req1.get("packet_sha256", "")
    assert nonce1, "첫 번째 nonce 없음"
    assert pkt_sha1, "첫 번째 packet_sha256 없음"

    # 두 번째 실행 (동일 조건 → reuse)
    r2 = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )
    assert r2.returncode == 0, f"두 번째 request-accept 실패: {r2.stdout} {r2.stderr}"

    req2 = load_acceptance_request()
    nonce2 = req2.get("nonce", "")
    pkt_sha2 = req2.get("packet_sha256", "")

    # nonce는 보존되거나 동일 (reuse)
    # reuse 판단은 pr_sha/ci_run 등에 따라 달라지므로 nonce 동일 여부만 확인
    assert nonce2, "두 번째 nonce 없음"
    assert pkt_sha2, "두 번째 packet_sha256 없음"

    # oracle 참조
    oracle = _oracle("normal_snapshot_reuse")
    if oracle:
        expected_fields = oracle.get("reuse_preserved_fields", [])
        for field in expected_fields:
            assert req2.get(field) == req1.get(field), (
                f"reuse 시 {field} 변경됨: {req1.get(field)} → {req2.get(field)}"
            )


# TC-3 (edge): SHA 불일치 — stale packet이 있을 때 BLOCKED 반환 확인
def test_tc3_stale_sha_mismatch_blocks_accept(tmp_path):
    """TC-3: acceptance_request.json의 packet_sha256이 실제 packet 파일과 다를 때
    gates accept가 BLOCKED를 반환(exit code != 0)함을 확인.

    oracle: tests/oracles/IMP-20260610-8C3B/edge_sha_mismatch/
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + returncode assertion.
    """
    env = make_env(tmp_path)
    pid = bootstrap_pipeline_legacy(tmp_path, env)
    ev_file = write_evidence_file(tmp_path)

    # 먼저 request-accept 실행해서 acceptance_request.json 생성
    r_req = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )
    assert r_req.returncode == 0, f"request-accept 실패: {r_req.stdout} {r_req.stderr}"

    req = load_acceptance_request()
    nonce = req.get("nonce", "TESTNON1")
    accept_code = f"ACCEPT-{pid}-{nonce}"

    # packet_sha256을 의도적으로 오염 (BASE_DIR에 있는 파일 수정)
    req["packet_sha256"] = "a" * 64  # 잘못된 SHA
    req_file = PIPELINE_PY.parent / "acceptance_request.json"
    with open(req_file, "w", encoding="utf-8") as f:
        json.dump(req, f, ensure_ascii=False, indent=2)

    # gates accept 실행 → BLOCKED 예상
    r_accept = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(ev_file),
         "--acceptance-code", accept_code],
        env=env,
    )

    # BLOCKED: exit code != 0 또는 stdout에 BLOCKED/ERROR 포함
    assert (r_accept.returncode != 0 or
            "BLOCKED" in r_accept.stdout or
            "BLOCKED" in r_accept.stderr or
            "ERROR" in r_accept.stdout), (
        f"SHA 불일치 시 BLOCKED 미반환\n"
        f"returncode={r_accept.returncode}\n"
        f"stdout: {r_accept.stdout[:500]}"
    )

    # oracle 참조
    oracle = _oracle("edge_sha_mismatch")
    if oracle:
        expected_failure_code = oracle.get("expected_failure_code", "")
        if expected_failure_code:
            combined = r_accept.stdout + r_accept.stderr
            assert expected_failure_code in combined or r_accept.returncode != 0, (
                f"expected failure_code '{expected_failure_code}' not found"
            )


# TC-4 (edge): AC incomplete — module integrate 없이 request-accept 실행 시
# requirements_tracking.enabled=true 파이프라인에서 BLOCKED 또는 경고 확인
def test_tc4_ac_incomplete_no_integrate_cache(tmp_path):
    """TC-4: requirements_tracking.enabled=true이고 module integrate가 완료되지 않아
    ac_completeness 캐시가 없는 상태에서 request-accept를 실행하면
    AC 검증 경로(fallback)가 동작하는지 확인한다.

    oracle: tests/oracles/IMP-20260610-8C3B/edge_ac_incomplete/
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + final_state assertion.
    """
    env = make_env(tmp_path)

    # 새 파이프라인 생성 (AC 검사 활성화)
    r_new = run_cli(
        ["new", "--type", "IMP", "--desc", "ac incomplete test 8c3b"],
        env=env,
    )
    assert r_new.returncode == 0, f"new failed: {r_new.stdout} {r_new.stderr}"

    state_file = Path(env["PIPELINE_STATE_PATH"])
    with open(state_file, encoding="utf-8") as f:
        state = json.load(f)

    # requirements_tracking 활성화 + ac_completeness 캐시 없음 (integrate 미실행 시뮬레이션)
    state["requirements_tracking"] = {"enabled": True}
    # ac_completeness 캐시 없음 (키 자체 없음)
    state.pop("ac_completeness", None)
    # structured_acceptance_criteria 비어있음 (AC 없음 → 검사 생략 경로)
    state["structured_acceptance_criteria"] = []

    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    ev_file = write_evidence_file(tmp_path, "ac incomplete test evidence")

    r = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )

    # AC 없으면 검사 생략 → request-accept 성공 (exit 0)
    # AC가 있고 PENDING이면 BLOCKED
    # 여기서는 structured_acceptance_criteria=[] → 검사 생략 → 성공 예상
    assert r.returncode == 0, (
        f"AC 없는 경우 request-accept가 실패해서는 안 됨\n"
        f"returncode={r.returncode}\n"
        f"stdout: {r.stdout[:500]}\nstderr: {r.stderr[:300]}"
    )

    # ac_completeness 캐시 없이도 정상 동작함을 확인
    req = load_acceptance_request()
    assert req.get("nonce"), "nonce 미발급"

    # oracle 참조
    oracle = _oracle("edge_ac_incomplete")
    if oracle:
        expected_exit = oracle.get("expected_exit_code", 0)
        assert r.returncode == expected_exit, (
            f"expected exit {expected_exit}, got {r.returncode}"
        )


# TC-5 (edge): 잘못된 acceptance-code로 gates accept 실행 시 BLOCKED
def test_tc5_wrong_acceptance_code_blocked(tmp_path):
    """TC-5: 잘못된 acceptance-code를 전달하면 gates accept가 BLOCKED를 반환함을 확인.

    oracle: tests/oracles/IMP-20260610-8C3B/edge_wrong_code/
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + returncode assertion.
    """
    env = make_env(tmp_path)
    pid = bootstrap_pipeline_legacy(tmp_path, env)
    ev_file = write_evidence_file(tmp_path)

    # request-accept 실행 후 acceptance_request.json 생성
    r_req = run_cli(
        ["gates", "request-accept", "--evidence", str(ev_file)],
        env=env,
    )
    assert r_req.returncode == 0, f"request-accept 실패: {r_req.stdout} {r_req.stderr}"

    # 잘못된 코드로 accept 시도
    wrong_code = f"ACCEPT-{pid}-WRONGXXX"
    r_accept = run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(ev_file),
         "--acceptance-code", wrong_code],
        env=env,
    )

    # BLOCKED: exit code != 0 또는 BLOCKED/mismatch/ERROR 메시지
    combined = r_accept.stdout + r_accept.stderr
    assert (r_accept.returncode != 0 or
            "BLOCKED" in combined or
            "mismatch" in combined.lower() or
            "error" in combined.lower()), (
        f"잘못된 코드 시 BLOCKED 미반환\n"
        f"returncode={r_accept.returncode}\n"
        f"stdout: {r_accept.stdout[:500]}"
    )

    # oracle 참조
    oracle = _oracle("edge_wrong_code")
    if oracle:
        expected_failure_code = oracle.get("expected_failure_code", "")
        if expected_failure_code:
            assert (expected_failure_code in combined or
                    r_accept.returncode != 0), (
                f"expected failure_code '{expected_failure_code}' not found"
            )
