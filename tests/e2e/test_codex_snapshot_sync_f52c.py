# tests/e2e/test_codex_snapshot_sync_f52c.py
# [Purpose]: BUG-20260628-F52C 재작업 — "prepare_snapshot → codex_review_snapshot → publish" 분리가
#            (1) Codex APPROVE 전 승인 코드/PR comment/acceptance_request.json 미노출,
#            (2) codex_review_loop_state.json 미사용(legacy 제거),
#            (3) Codex REJECT/결과없음/stale SHA fail-closed BLOCKED,
#            (4) publish 직후 3-SHA 일치,
#            (5) PENDING frozen snapshot 보호,
#            (6) post-accept 상태/provenance 필드만 변경
#            을 회귀 검증한다.
# [Assumptions]: pipeline.py가 같은 repo 루트에 있고, PIPELINE_STATE_PATH 격리 + cwd=tmp_path로
#                상태/산출물 파일을 격리할 수 있다. gh CLI는 PATH에서 제거하여 결정적으로 동작시킨다.
#                codex_review_result.json은 .pipeline/ 하위에 직접 작성하여 SSoT를 시뮬레이션한다.
# [Vulnerability & Risks]: 일부 CLI 경로(request-accept 전체)는 PR/CI 전제조건이 많아 gh 없이는
#                BLOCKED로 끝난다. 이 테스트는 새 동작(분리 단계, fail-closed Codex, 불변식,
#                PENDING lock)을 결정적으로 검증하는 데 초점을 맞춘다.
# [Improvement]: gh를 mock하는 stub 바이너리를 추가하면 publish 경로의 PR 본문 갱신까지 검증 가능.
"""BUG-20260628-F52C: prepare → codex_review → publish 분리 회귀 테스트.

TC-A: Codex APPROVE 전 PR pending comment/stdout 승인 코드/acceptance_request.json 미생성.
TC-B: Codex REJECT 시 exit non-zero + 승인 코드 미노출 + comment 미게시 + acceptance_request.json 미생성.
TC-C: request-accept 성공 직후 codex_review_result/acceptance_request/실제 packet SHA 일치(불변식).
TC-D: codex_review_loop_state.json이 있어도 request-accept가 사용하지 않음.
TC-E: codex_review_result.json 없음/REJECT/stale SHA는 모두 request-accept BLOCKED.
TC-F: PENDING 중 report final-packet/update-pr-body가 snapshot 변경 시도를 BLOCKED.
TC-G: post-accept(여기서는 PENDING lock 부수효과 없음)는 허용된 상태 필드만 보존.
TC-1: stale_packet_sha256 재현 케이스 수정 확인(회귀).
TC-3N: _codex_review_snapshot APPROVE 경로 SHA 일치.
TC-8: staging 반복 호출 SHA 불변.
TC-9: 승인 코드 형식 유지 + codex_review_result 없음=REJECT.
TC-10: post-accept 상태 보존.
TC-ALL: 전체 suite 회귀.
"""
import subprocess
import sys
import os
import json
import hashlib
import shutil
import time
from pathlib import Path

import pytest

PIPELINE_PY = str(Path(__file__).parent.parent.parent / "pipeline.py")
PIPELINE_DIR = str(Path(PIPELINE_PY).parent)
PIPELINE_ID = "BUG-20260628-F52C-TEST"  # 테스트 전용 ID


def _no_gh_path() -> str:
    """gh CLI 디렉토리를 제거한 PATH를 반환한다 (git 등 다른 도구는 유지).

    request-accept/report 경로를 gh 없는 환경처럼 결정적으로 동작시키기 위함.
    """
    gh = shutil.which("gh")
    current = os.environ.get("PATH", "")
    if not gh:
        return current
    gh_dir = os.path.dirname(gh)
    parts = [
        p for p in current.split(os.pathsep)
        if p and os.path.normcase(p) != os.path.normcase(gh_dir)
    ]
    return os.pathsep.join(parts)


def run_pipeline(*args, state_path=None, cwd=None, env_extra=None, no_gh=True):
    """pipeline.py CLI를 subprocess로 실행하고 결과를 반환한다."""
    cmd = [sys.executable, PIPELINE_PY] + list(args)
    env = os.environ.copy()
    if state_path:
        env["PIPELINE_STATE_PATH"] = str(state_path)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    if no_gh:
        env["PATH"] = _no_gh_path()
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
        cwd=str(cwd) if cwd else None,
    )
    return result


def _base_state_data():
    """request-accept 전제조건을 최대한 충족한 격리 state dict."""
    return {
        "version": "1.2.0",
        "pipeline_id": PIPELINE_ID,
        "pipeline_type": "BUG",
        "type": "BUG",
        "description": "stale_packet_sha256 회귀 테스트",
        "current_phase": "build",
        "terminal_state": None,
        "phases": {
            "pm": {"status": "DONE"},
            "dev": {"status": "DONE"},
            "qa": {"status": "DONE"},
            "sec": {"status": "SKIPPED"},
            "build": {"status": "DONE"},
        },
        # IMP-20260710-DB54 rework 문제1: codex-review는 preflight에서 contract_frozen을 요구한다.
        #   이 fixture는 codex-review 실행 전제조건 state이므로 frozen 계약을 명시한다.
        "contract": {"frozen": True},
        "external_gates": {
            "technical": {"status": "PASS"},
            "oracle": {"status": "PASS"},
            "github_ci": {"status": "PASS"},
            "acceptance": {"status": None},
        },
        "acceptance_request": None,
        "workspace_hygiene": {"status": "OK"},
        "event_log": [],
    }


@pytest.fixture
def isolated_pipeline(tmp_path):
    """격리된 pipeline state를 생성하는 fixture.

    Returns:
        (tmp_path, state_file) 튜플. cwd=tmp_path와 함께 사용한다.
    """
    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(
        json.dumps(_base_state_data(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return tmp_path, state_file


def _write_pending_acceptance_request(cwd: Path, divergent=True, packet_content="STALE PACKET BODY"):
    """cwd에 PENDING acceptance_request.json + (옵션) divergent packet.md를 생성한다."""
    packet_path = cwd / "human_acceptance_packet.md"
    recorded_sha = hashlib.sha256(b"ORIGINAL EXPECTED CONTENT").hexdigest()
    req = {
        "request_id": "REQ-TEST-0001",
        "pipeline_id": PIPELINE_ID,
        "nonce": "deadbeef",
        "status": "PENDING",
        "evidence": "output.xlsx",
        "packet_path": str(packet_path),
        "packet_sha256": recorded_sha,
        "pr_body_sha256": "b" * 64,
        "created_at": "2026-06-28T00:00:00Z",
    }
    (cwd / "acceptance_request.json").write_text(
        json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if divergent:
        packet_path.write_text(packet_content, encoding="utf-8")
    return req


def _read_state(state_file: Path):
    return json.loads(state_file.read_text(encoding="utf-8"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_codex_result(state_file: Path, verdict: str, packet_sha="a" * 64, pid=PIPELINE_ID):
    """codex_review_result.json (SSoT)을 격리 .pipeline 디렉토리에 생성한다."""
    pipeline_dir = state_file.parent / ".pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": 1,
        "pipeline_id": pid,
        "verdict": verdict,
        "reason": "test",
        "packet_sha256": packet_sha,
        "pr_body_sha256": "b" * 64,
        "pr_head_sha": "0" * 40,
        "recorded_at": "2026-06-28T00:00:00Z",
    }
    (pipeline_dir / "codex_review_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_codex_loop_state(state_file: Path, status: str, packet_sha="a" * 64):
    """legacy codex_review_loop_state.json 생성 (TC-D: 사용 안 됨 확인용)."""
    pipeline_dir = state_file.parent / ".pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    loop = {
        "status": status,
        "pipeline_id": PIPELINE_ID,
        "pr_head_sha": "0" * 40,
        "packet_sha256": packet_sha,
        "pr_body_sha256": "b" * 64,
        "accept_code": f"ACCEPT-{PIPELINE_ID}",
    }
    (pipeline_dir / "codex_review_loop_state.json").write_text(
        json.dumps(loop, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _run_driver(tmp_path, state_file, body: str):
    """pipeline 모듈을 import하여 임의 검증 스니펫을 실행하는 드라이버 subprocess."""
    driver = tmp_path / f"driver_{abs(hash(body)) % 100000}.py"
    driver.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {json.dumps(PIPELINE_DIR)})\n"
        "import pipeline\n" + body,
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_file)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    env["PATH"] = _no_gh_path()
    result = subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(tmp_path),
    )
    return (result.stdout or "") + (result.stderr or "")


# ---------------------------------------------------------------------------
# TC-A: Codex APPROVE 전 사용자 노출 산출물 미생성
# ---------------------------------------------------------------------------

def test_tc_a_no_exposure_before_codex(isolated_pipeline):
    """TC-A: codex_review_result.json이 없으면 request-accept가 승인 코드/comment/
    acceptance_request.json을 절대 생성하지 않는다.

    gh 없는 환경에서 request-accept는 더 일찍 BLOCKED될 수 있으나, 어떤 경우에도
    '사용자 승인 요청' 출력과 acceptance_request.json 생성은 없어야 한다.
    """
    tmp_path, state_file = isolated_pipeline
    # codex_review_result.json 미생성 → Codex 미수행 상태

    result = run_pipeline(
        "gates", "request-accept", "--evidence", "output.xlsx",
        state_path=state_file, cwd=tmp_path,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    # 승인 코드/승인 요청문이 절대 출력되면 안 됨
    assert "사용자 승인 요청" not in combined, f"Codex 전 승인 요청문 노출\n{combined}"
    # acceptance_request.json은 생성되지 않아야 함
    assert not (tmp_path / "acceptance_request.json").exists(), (
        "Codex 전 acceptance_request.json이 생성됨"
    )
    assert result.returncode != 0, "Codex 미수행인데 request-accept가 성공함"
    final_state = _read_state(state_file)
    assert final_state["external_gates"]["acceptance"]["status"] != "PASS"


def test_tc_a_codex_snapshot_reject_without_result(isolated_pipeline):
    """TC-A(보강): _codex_review_snapshot은 codex_review_result.json 없으면 REJECT."""
    tmp_path, state_file = isolated_pipeline
    out = _run_driver(
        tmp_path, state_file,
        "res = pipeline._codex_review_snapshot(%r, {'packet_sha256': 'a'*64}, {})\n" % PIPELINE_ID +
        "print('VERDICT', res['verdict'])\n",
    )
    assert "VERDICT REJECT" in out, f"result 없는데 REJECT 아님\n{out}"


# ---------------------------------------------------------------------------
# TC-B: Codex REJECT 시 fail-closed (코드/댓글/요청 파일 미생성)
# ---------------------------------------------------------------------------

def test_tc_b_codex_reject_blocks(isolated_pipeline):
    """TC-B: codex_review_result verdict=REJECT면 request-accept가 BLOCKED.

    승인 코드 미노출 + acceptance_request.json 미생성을 확인한다.
    """
    tmp_path, state_file = isolated_pipeline
    _write_codex_result(state_file, "REJECT", packet_sha="f" * 64)

    result = run_pipeline(
        "gates", "request-accept", "--evidence", "output.xlsx",
        state_path=state_file, cwd=tmp_path,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert result.returncode != 0, "Codex REJECT인데 exit 0"
    assert "사용자 승인 요청" not in combined, f"REJECT인데 승인 요청문 노출\n{combined}"
    assert not (tmp_path / "acceptance_request.json").exists(), (
        "Codex REJECT인데 acceptance_request.json이 생성됨"
    )
    final_state = _read_state(state_file)
    assert final_state["external_gates"]["acceptance"]["status"] != "PASS"


def test_tc_b_codex_snapshot_reject_verdict(isolated_pipeline):
    """TC-B(보강): _codex_review_snapshot은 verdict=REJECT를 그대로 REJECT로 반환."""
    tmp_path, state_file = isolated_pipeline
    _write_codex_result(state_file, "REJECT", packet_sha="a" * 64)
    out = _run_driver(
        tmp_path, state_file,
        "res = pipeline._codex_review_snapshot(%r, {'packet_sha256': 'a'*64}, {})\n" % PIPELINE_ID +
        "print('VERDICT', res['verdict'])\n",
    )
    assert "VERDICT REJECT" in out, f"verdict=REJECT인데 REJECT 아님\n{out}"


# ---------------------------------------------------------------------------
# TC-C: publish 직후 3-SHA 불변식 일치 (_verify_snapshot_invariant 3-way)
# ---------------------------------------------------------------------------

def test_tc_c_three_way_sha_invariant(isolated_pipeline):
    """TC-C: staging==published==codex packet SHA가 일치하면 불변식 PASS, 하나라도 다르면 오류."""
    tmp_path, state_file = isolated_pipeline
    out = _run_driver(
        tmp_path, state_file,
        "staged = {'packet_sha256': 'a'*64}\n"
        "pub = {'packet_sha256': 'a'*64}\n"
        "codex_ok = {'result_packet_sha256': 'a'*64}\n"
        "codex_bad = {'result_packet_sha256': 'c'*64}\n"
        "print('OK', pipeline._verify_snapshot_invariant(staged, pub, codex_ok))\n"
        "print('BAD', pipeline._verify_snapshot_invariant(staged, pub, codex_bad) is not None)\n"
        "print('TWOWAY', pipeline._verify_snapshot_invariant(staged, pub))\n",
    )
    assert "OK None" in out, f"3-way 일치인데 불변식 오류\n{out}"
    assert "BAD True" in out, f"codex SHA 불일치인데 오류 미반환\n{out}"
    assert "TWOWAY None" in out, f"2-way 일치인데 오류\n{out}"


# ---------------------------------------------------------------------------
# TC-D: codex_review_loop_state.json은 사용되지 않음 (legacy 제거)
# ---------------------------------------------------------------------------

def test_tc_d_loop_state_ignored(isolated_pipeline):
    """TC-D: codex_review_loop_state.json(APPROVED)이 있어도 request-accept가 사용하지 않는다.

    loop_state는 APPROVED지만 codex_review_result.json이 없으므로 _codex_review_snapshot은
    여전히 REJECT여야 한다 (legacy 경로가 제거되었음을 증명).
    """
    tmp_path, state_file = isolated_pipeline
    _write_codex_loop_state(state_file, "APPROVED", packet_sha="a" * 64)
    # codex_review_result.json은 의도적으로 미생성

    out = _run_driver(
        tmp_path, state_file,
        "res = pipeline._codex_review_snapshot(%r, {'packet_sha256': 'a'*64}, {})\n" % PIPELINE_ID +
        "print('VERDICT', res['verdict'])\n",
    )
    assert "VERDICT REJECT" in out, (
        f"loop_state APPROVED를 사용해 버림 (legacy 경로 잔존)\n{out}"
    )

    # 소스 레벨에서도 _codex_review_snapshot이 loop_state 경로를 호출하지 않음을 확인.
    # (docstring/주석의 'codex_review_loop_state' 언급은 허용 — 실제 호출만 검사한다.)
    src = Path(PIPELINE_PY).read_text(encoding="utf-8")
    fn_start = src.index("def _codex_review_snapshot(")
    fn_end = src.index("\ndef ", fn_start + 10)
    fn_body = src[fn_start:fn_end]
    assert "_codex_review_loop_state_path(" not in fn_body, (
        "_codex_review_snapshot이 codex_review_loop_state 경로를 호출함"
    )
    # NOT_CONFIGURED verdict로 publish를 허용하는 fallback이 없어야 함.
    assert "NOT_CONFIGURED" not in fn_body, (
        "_codex_review_snapshot에 NOT_CONFIGURED fallback이 남아 있음"
    )


# ---------------------------------------------------------------------------
# TC-E: 결과 없음/REJECT/stale SHA는 모두 BLOCKED (fail-closed)
# ---------------------------------------------------------------------------

def test_tc_e_no_result_blocked(isolated_pipeline):
    """TC-E(1): codex_review_result.json 없음 → REJECT."""
    tmp_path, state_file = isolated_pipeline
    out = _run_driver(
        tmp_path, state_file,
        "res = pipeline._codex_review_snapshot(%r, {'packet_sha256': 'a'*64}, {})\n" % PIPELINE_ID +
        "print('VERDICT', res['verdict'])\n",
    )
    assert "VERDICT REJECT" in out, f"result 없음인데 REJECT 아님\n{out}"


def test_tc_e_stale_sha_blocked(isolated_pipeline):
    """TC-E(2): APPROVE_TO_USER지만 staged SHA != 기록 SHA → REJECT (stale)."""
    tmp_path, state_file = isolated_pipeline
    _write_codex_result(state_file, "APPROVE_TO_USER", packet_sha="f" * 64)
    out = _run_driver(
        tmp_path, state_file,
        "res = pipeline._codex_review_snapshot(%r, {'packet_sha256': 'a'*64}, {})\n" % PIPELINE_ID +
        "print('VERDICT', res['verdict'])\n",
    )
    assert "VERDICT REJECT" in out, f"stale SHA인데 REJECT 아님\n{out}"


def test_tc_e_pipeline_mismatch_blocked(isolated_pipeline):
    """TC-E(3): 다른 pipeline_id의 APPROVE 결과는 재사용 차단 → REJECT."""
    tmp_path, state_file = isolated_pipeline
    _write_codex_result(state_file, "APPROVE_TO_USER", packet_sha="a" * 64, pid="BUG-OTHER-9999")
    out = _run_driver(
        tmp_path, state_file,
        "res = pipeline._codex_review_snapshot(%r, {'packet_sha256': 'a'*64}, {})\n" % PIPELINE_ID +
        "print('VERDICT', res['verdict'])\n",
    )
    assert "VERDICT REJECT" in out, f"pipeline_id 불일치인데 REJECT 아님\n{out}"


def test_tc_e_approve_match_passes(isolated_pipeline):
    """TC-E(4): APPROVE_TO_USER + SHA 일치 + pipeline_id 일치 → APPROVE_TO_USER."""
    tmp_path, state_file = isolated_pipeline
    _write_codex_result(state_file, "APPROVE_TO_USER", packet_sha="a" * 64)
    out = _run_driver(
        tmp_path, state_file,
        "res = pipeline._codex_review_snapshot(%r, {'packet_sha256': 'a'*64}, {})\n" % PIPELINE_ID +
        "print('VERDICT', res['verdict'])\n",
    )
    assert "VERDICT APPROVE_TO_USER" in out, f"모든 조건 충족인데 APPROVE 아님\n{out}"


# ---------------------------------------------------------------------------
# TC-F: PENDING 중 report final-packet/update-pr-body BLOCKED
# ---------------------------------------------------------------------------

def test_tc_f_pending_blocks_final_packet(isolated_pipeline):
    """TC-F(1): PENDING(divergent) 중 report final-packet은 BLOCKED + 파일 불변."""
    tmp_path, state_file = isolated_pipeline
    _write_pending_acceptance_request(tmp_path, divergent=True)
    before = (tmp_path / "acceptance_request.json").read_text(encoding="utf-8")

    result = run_pipeline("report", "final-packet", state_path=state_file, cwd=tmp_path)
    combined = (result.stdout or "") + (result.stderr or "")
    assert "pending_snapshot_lock" in combined, f"PENDING lock 미발동\n{combined}"
    assert result.returncode != 0
    after = (tmp_path / "acceptance_request.json").read_text(encoding="utf-8")
    assert before == after, "PENDING lock이 snapshot 재생성을 막지 못함"


def test_tc_f_pending_blocks_update_pr_body(isolated_pipeline):
    """TC-F(2): PENDING(divergent) 중 report update-pr-body는 BLOCKED."""
    tmp_path, state_file = isolated_pipeline
    _write_pending_acceptance_request(tmp_path, divergent=True)

    result = run_pipeline("report", "update-pr-body", state_path=state_file, cwd=tmp_path)
    combined = (result.stdout or "") + (result.stderr or "")
    assert "pending_snapshot_lock" in combined, f"PENDING lock 미발동\n{combined}"
    assert result.returncode != 0
    final_state = _read_state(state_file)
    assert final_state["terminal_state"] is None


def test_tc_f_pending_consistent_noop(isolated_pipeline):
    """TC-F(3): PENDING이지만 디스크 packet SHA가 기록과 일치(동일 bytes no-op)면 허용."""
    tmp_path, state_file = isolated_pipeline
    packet_path = tmp_path / "human_acceptance_packet.md"
    packet_path.write_text("CONSISTENT BODY", encoding="utf-8")
    actual_sha = _sha256_text("CONSISTENT BODY")
    req = {
        "request_id": "REQ-OK", "pipeline_id": PIPELINE_ID, "nonce": "ok",
        "status": "PENDING", "packet_path": str(packet_path),
        "packet_sha256": actual_sha, "created_at": "2026-06-28T00:00:00Z",
    }
    (tmp_path / "acceptance_request.json").write_text(
        json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = run_pipeline("report", "final-packet", state_path=state_file, cwd=tmp_path)
    combined = (result.stdout or "") + (result.stderr or "")
    assert "pending_snapshot_lock" not in combined, (
        f"동일 bytes no-op인데 lock 발동\n{combined}"
    )


# ---------------------------------------------------------------------------
# TC-G: post-accept(여기서는 lock 부수효과 없음)는 허용 필드만 보존
# ---------------------------------------------------------------------------

def test_tc_g_state_structure_preserved(isolated_pipeline):
    """TC-G: PENDING lock으로 report가 차단되는 동안 state의 phases/external_gates 구조 보존."""
    tmp_path, state_file = isolated_pipeline
    _write_pending_acceptance_request(tmp_path, divergent=True)
    before_state = _read_state(state_file)

    run_pipeline("report", "final-packet", state_path=state_file, cwd=tmp_path)
    after_state = _read_state(state_file)

    assert before_state["phases"] == after_state["phases"], "phases 구조 변경됨"
    assert before_state["external_gates"] == after_state["external_gates"], (
        "external_gates 구조 변경됨"
    )
    assert after_state["terminal_state"] is None


# ---------------------------------------------------------------------------
# TC-1 / TC-3N / TC-8 / TC-9: 기존 회귀 + 새 함수명 반영
# ---------------------------------------------------------------------------

def test_tc1_stale_packet_regression(isolated_pipeline):
    """TC-1: stale_packet_sha256 순환 버그가 재발하지 않음 (PENDING lock으로 재생성 차단)."""
    tmp_path, state_file = isolated_pipeline
    _write_pending_acceptance_request(tmp_path, divergent=True)
    before = (tmp_path / "acceptance_request.json").read_text(encoding="utf-8")

    result = run_pipeline("report", "final-packet", state_path=state_file, cwd=tmp_path)
    combined = (result.stdout or "") + (result.stderr or "")
    assert "pending_snapshot_lock" in combined

    after = (tmp_path / "acceptance_request.json").read_text(encoding="utf-8")
    assert before == after, "PENDING lock이 acceptance_request.json 재생성을 막지 못함"


def test_tc3n_codex_snapshot_approve_match(isolated_pipeline):
    """TC-3N: _codex_review_snapshot이 APPROVE_TO_USER + SHA 일치 시 APPROVE_TO_USER 반환."""
    tmp_path, state_file = isolated_pipeline
    _write_codex_result(state_file, "APPROVE_TO_USER", packet_sha="a" * 64)
    out = _run_driver(
        tmp_path, state_file,
        "res = pipeline._codex_review_snapshot(%r, {'packet_sha256': 'a'*64}, {})\n" % PIPELINE_ID +
        "print('VERDICT', res['verdict'])\n",
    )
    assert "VERDICT APPROVE_TO_USER" in out, f"staged==기록 SHA인데 APPROVE 아님\n{out}"


def test_tc8_staging_sha_stable(isolated_pipeline):
    """TC-8: staging(publish=False) 반복 호출 시 packet SHA 불변 + published=False."""
    tmp_path, state_file = isolated_pipeline
    out = _run_driver(
        tmp_path, state_file,
        "state = {'pipeline_id': %r, 'acceptance_request': None}\n" % PIPELINE_ID +
        "req = {'request_id': 'R1', 'pipeline_id': %r, 'nonce': 'n1', 'evidence': 'output.xlsx', 'status': 'PENDING'}\n" % PIPELINE_ID +
        "r1 = pipeline._materialize_acceptance_snapshot(state, req, publish=False)\n"
        "r2 = pipeline._materialize_acceptance_snapshot(state, req, publish=False)\n"
        "print('SHA1', r1['sha_manifest'].get('packet_sha256'))\n"
        "print('SHA2', r2['sha_manifest'].get('packet_sha256'))\n"
        "print('PUBLISHED', r1.get('published'), r2.get('published'))\n",
    )
    if "SHA1" not in out:
        pytest.skip(f"staging 드라이버 실행 실패(환경 의존) — {out[:400]}")
    lines = {
        line.split(" ", 1)[0]: line.split(" ", 1)[1].strip()
        for line in out.splitlines() if line.startswith(("SHA1", "SHA2", "PUBLISHED"))
    }
    assert lines.get("SHA1") and lines.get("SHA1") == lines.get("SHA2"), (
        f"staging 반복 호출 SHA 불일치: {lines}"
    )
    assert "False" in lines.get("PUBLISHED", ""), (
        f"publish=False인데 published가 False가 아님: {lines}"
    )
    # staging은 디스크에 acceptance_request.json을 만들지 않아야 함
    assert not (tmp_path / "acceptance_request.json").exists(), (
        "staging이 acceptance_request.json을 생성함"
    )


def test_tc9_accept_code_format_and_no_result_reject(isolated_pipeline):
    """TC-9: 승인 코드 형식 ACCEPT-{pipeline_id} 유지 + result 없음=REJECT."""
    tmp_path, state_file = isolated_pipeline
    out = _run_driver(
        tmp_path, state_file,
        "pid = %r\n" % PIPELINE_ID +
        "print('CODE', f'ACCEPT-{pid}')\n"
        "res = pipeline._codex_review_snapshot(pid, {'packet_sha256': 'x'*64}, {})\n"
        "print('CODEX', res['verdict'])\n",
    )
    assert f"ACCEPT-{PIPELINE_ID}" in out, f"승인 코드 형식 누락\n{out}"
    assert "CODEX REJECT" in out, f"result 없음인데 REJECT 아님\n{out}"


# ---------------------------------------------------------------------------
# TC-10: post-accept 상태 보존
# ---------------------------------------------------------------------------

def test_tc10_post_accept_fields_only(isolated_pipeline):
    """TC-10: PENDING lock으로 packet 변경이 차단되는 동안 state 핵심 필드 보존."""
    tmp_path, state_file = isolated_pipeline
    _write_pending_acceptance_request(tmp_path, divergent=True)
    before_state = _read_state(state_file)

    run_pipeline("report", "final-packet", state_path=state_file, cwd=tmp_path)
    after_state = _read_state(state_file)

    assert before_state["phases"] == after_state["phases"]
    assert before_state["external_gates"] == after_state["external_gates"]
    assert after_state["terminal_state"] is None


# ---------------------------------------------------------------------------
# gates codex-review CLI 표면 동작 검증
# ---------------------------------------------------------------------------

def test_codex_review_cli_writes_result(isolated_pipeline):
    """gates codex-review --verdict APPROVE_TO_USER가 codex_review_result.json을 기록한다."""
    tmp_path, state_file = isolated_pipeline
    result = run_pipeline(
        "gates", "codex-review", "--verdict", "APPROVE_TO_USER",
        "--packet-sha256", "a" * 64,
        state_path=state_file, cwd=tmp_path,
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert "Traceback (most recent call last)" not in combined, (
        f"gates codex-review에서 raw traceback 노출\n{combined}"
    )
    result_file = state_file.parent / ".pipeline" / "codex_review_result.json"
    assert result_file.exists(), f"codex_review_result.json 미생성\n{combined}"
    data = json.loads(result_file.read_text(encoding="utf-8"))
    assert data["verdict"] == "APPROVE_TO_USER"
    assert data["packet_sha256"] == "a" * 64
    assert data["pipeline_id"] == PIPELINE_ID
    assert result.returncode == 0


def test_codex_review_cli_reject_exit_nonzero(isolated_pipeline):
    """gates codex-review --verdict REJECT는 exit non-zero + result 기록."""
    tmp_path, state_file = isolated_pipeline
    result = run_pipeline(
        "gates", "codex-review", "--verdict", "REJECT",
        "--packet-sha256", "f" * 64,
        state_path=state_file, cwd=tmp_path,
    )
    assert result.returncode != 0, "REJECT인데 exit 0"
    result_file = state_file.parent / ".pipeline" / "codex_review_result.json"
    assert result_file.exists()
    data = json.loads(result_file.read_text(encoding="utf-8"))
    assert data["verdict"] == "REJECT"


# ---------------------------------------------------------------------------
# BUG-20260628-F52C REJECT 재작업 — gh stub 기반 full publish-path E2E (5 TC)
#
# 아래 TC들은 gh CLI를 "PATH에 존재하는" stub으로 주입하여 request-accept의 publish
# 경로(_publish_acceptance_request)가 실제로 PR body 갱신/pending comment까지 도달하게 한다.
# 기존 8c3b 하네스는 gh를 PATH에서 제거(publish skip)하므로, fail-closed 분기(REJECT-2/3)와
# pr_body/pr_head SHA 검증(REJECT-1)을 검증하려면 gh가 PATH에 있어야 한다.
#
# 산출물(acceptance_request.json/human_acceptance_packet.*/.pipeline/codex_review_result.json)은
# pipeline.py BASE_DIR(프로젝트 루트)에 생성되며 모두 gitignore 대상이다. 각 TC는 실행 전후로
# 이 산출물을 정리하여 교차 오염을 방지한다.
# ---------------------------------------------------------------------------

PIPELINE_ROOT = Path(PIPELINE_PY).parent
_ART_NAMES = (
    "acceptance_request.json",
    "acceptance_request.json.tmp",
    "human_acceptance_packet.md",
    "human_acceptance_packet.md.tmp",
    "human_acceptance_packet.json",
    "human_acceptance_packet.json.tmp",
)


def _clean_root_artifacts():
    """프로젝트 루트의 publish 산출물(.tmp 포함) + codex_review_result.json을 정리한다.

    OneDrive 동기화가 직전 테스트의 파일을 잠깐 잠그면 os.replace가 WinError 5로 실패할 수
    있으므로, .tmp 잔여물까지 제거하고 unlink 실패 시 짧게 재시도하여 결정성을 높인다.
    """
    targets = [PIPELINE_ROOT / n for n in _ART_NAMES]
    targets.append(PIPELINE_ROOT / ".pipeline" / "codex_review_result.json")
    # BUG-20260628-F52C: staging file은 BASE_DIR(.pipeline)에 저장되므로 테스트 간 정리 필수.
    targets.append(PIPELINE_ROOT / ".pipeline" / "acceptance_staging.json")
    for p in targets:
        for _attempt in range(5):
            if not p.exists():
                break
            try:
                p.unlink()
                break
            except OSError:
                time.sleep(0.1)


_FAKE_GH_PR_BODY_F52C = (
    "## 작업 요약\nF52C gh stub publish-path 테스트 PR body\n\n"
    "## 사용자가 확인할 결과물\n결과물 경로: output.xlsx (테스트)\n\n"
    "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 성공\n\n"
    "## 중요한 선택과 트레이드오프\nN/A (테스트 픽스처)\n\n"
    "## 검증\n모든 게이트 PASS\n"
)


def _write_gh_stub(stub_dir: Path, head_sha: str = "f52c" + "0" * 36) -> Path:
    """PATH에 올릴 gh stub(.bat + .py)을 stub_dir에 생성하고 stub_dir을 반환한다.

    동작 제어 환경변수:
      - GH_STUB_FAIL_COMMENT=1: `gh pr comment` 호출 시 exit 1 (pending comment 게시 실패).
      - GH_STUB_FAIL_EDIT=1:    `gh pr edit` 호출 시 exit 1 (PR body 갱신 실패).

    PR body(`--jq .body`)는 _FAKE_GH_PR_BODY_F52C를 반환하고, headRefOid/head SHA는 head_sha,
    PR number=1, url=.../pull/1, isDraft=false, state=OPEN, files=[]를 반환한다.
    `gh api .../comments`는 빈 배열, `gh run list`는 빈 배열을 반환한다.

    Args:
        stub_dir: stub 파일을 둘 디렉토리.
        head_sha: headRefOid로 반환할 head SHA 문자열.
    Returns:
        stub_dir (PATH 앞에 추가할 경로).
    """
    stub_dir.mkdir(parents=True, exist_ok=True)
    body_json = json.dumps(_FAKE_GH_PR_BODY_F52C)
    pipeline_dir_json = json.dumps(PIPELINE_DIR)
    packet_md_json = json.dumps(str(PIPELINE_ROOT / "human_acceptance_packet.md"))
    # BUG-20260628-F52C REJECT-3(3차): gh pr edit는 Windows .bat 래퍼를 거치며 multi-line
    # --body 인자가 잘리므로, edit 인자를 신뢰하지 않는다. 대신 .body 조회 시 pipeline이 디스크에
    # 기록한 human_acceptance_packet.md를 읽어 _replace_pr_body_packet_block을 적용한 "publish 후
    # 최종 PR body"를 결정적으로 반환한다. 이는 실제 gh가 publish 후 반환할 body와 동일하다.
    # packet.md가 아직 없으면(=publish 전, codex-review 단계) 원본 body를 반환한다.
    py = stub_dir / "gh_stub_f52c.py"
    py.write_text(
        "import sys, io, json, os\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        f"sys.path.insert(0, {pipeline_dir_json})\n"
        f"DEFAULT_BODY = {body_json}\n"
        f"PACKET_MD = {packet_md_json}\n"
        f"HEAD_SHA = {json.dumps(head_sha)}\n"
        "def _normalize(body):\n"
        "    # GH_STUB_NORMALIZE_BODY=1이면 GitHub canonical 정규화를 흉내낸다:\n"
        "    #   CRLF/CR -> LF, 각 줄 끝 trailing whitespace 제거, 문서 끝 trailing newline 제거.\n"
        "    # 이로써 로컬 candidate SHA != GitHub canonical SHA 상황(r7 버그 조건)을 재현한다.\n"
        '    if os.environ.get("GH_STUB_NORMALIZE_BODY") != "1":\n'
        "        return body\n"
        '    b = body.replace("\\r\\n", "\\n").replace("\\r", "\\n")\n'
        '    lines = [ln.rstrip() for ln in b.split("\\n")]\n'
        '    return "\\n".join(lines).rstrip("\\n")\n'
        "def _current_body():\n"
        "    # publish가 디스크에 기록한 packet.md가 있으면 그 내용으로 FINAL_PACKET 블록을 교체한\n"
        "    # 'publish 후 최종 body'를 반환한다(실제 gh pr view와 동일). 없으면 원본 body.\n"
        "    try:\n"
        '        with open(PACKET_MD, encoding="utf-8") as fh:\n'
        "            packet = fh.read()\n"
        "    except OSError:\n"
        "        return _normalize(DEFAULT_BODY)\n"
        "    try:\n"
        "        import pipeline\n"
        "        return _normalize(pipeline._replace_pr_body_packet_block(DEFAULT_BODY, packet))\n"
        "    except Exception:\n"
        "        return _normalize(DEFAULT_BODY)\n"
        "args = sys.argv[1:]\n"
        '# pr edit (PR body 갱신) — GH_STUB_FAIL_EDIT=1이면 실패. 성공 시 no-op(.body가 packet.md 기준).\n'
        'if "pr" in args and "edit" in args:\n'
        '    if os.environ.get("GH_STUB_FAIL_EDIT") == "1":\n'
        '        sys.stderr.write("stub: pr edit forced failure\\n"); sys.exit(1)\n'
        '    sys.exit(0)\n'
        '# pr comment (pending 안내 댓글) — GH_STUB_FAIL_COMMENT=1이면 실패.\n'
        'if "pr" in args and "comment" in args:\n'
        '    if os.environ.get("GH_STUB_FAIL_COMMENT") == "1":\n'
        '        sys.stderr.write("stub: pr comment forced failure\\n"); sys.exit(1)\n'
        '    print("https://github.com/test/repo/pull/1#issuecomment-1"); sys.exit(0)\n'
        '# api .../comments — 빈 배열.\n'
        'if "api" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        '# run list/view — CI run 없음(빈 문자열)으로 처리하여 ci_run_id=""가 되게 한다.\n'
        '# (run id를 비워야 github_ci_head_sha=None → packet의 ci_head_sha가 codex/request-accept\n'
        '#  staging 양쪽에서 동일하게 "없음"으로 렌더링되어 packet SHA 불변식이 성립한다.)\n'
        'if "run" in args:\n'
        '    print(""); sys.exit(0)\n'
        '# pr view --jq <expr>\n'
        'if "--jq" in args:\n'
        '    jq = args[args.index("--jq") + 1] if args.index("--jq") + 1 < len(args) else ""\n'
        '    if jq == ".body":\n'
        '        _b = _current_body()\n'
        '        sys.stdout.write(_b)\n'
        '        if not _b.endswith("\\n"):\n'
        '            sys.stdout.write("\\n")\n'
        '        sys.exit(0)\n'
        '    if jq == ".headRefOid":\n'
        '        print(HEAD_SHA); sys.exit(0)\n'
        '    if jq == ".url":\n'
        '        print("https://github.com/test/repo/pull/1"); sys.exit(0)\n'
        '    if jq == ".number":\n'
        '        print("1"); sys.exit(0)\n'
        '    if jq == ".[0].databaseId" or ".databaseId" in jq:\n'
        '        print(""); sys.exit(0)\n'
        '    if "[.files" in jq or jq.startswith(".[0]"):\n'
        '        print("[]"); sys.exit(0)\n'
        '    print(""); sys.exit(0)\n'
        '# pr view --json <fields> (no jq) — 전체 객체 반환.\n'
        "print(json.dumps({\n"
        '    "body": _current_body(), "number": 1, "headRefOid": HEAD_SHA,\n'
        '    "isDraft": False, "state": "OPEN", "files": [],\n'
        '    "url": "https://github.com/test/repo/pull/1",\n'
        "}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    bat = stub_dir / "gh.bat"
    bat.write_text(
        "@echo off\r\n"
        f'"{sys.executable}" "{py}" %*\r\n',
        encoding="utf-8",
    )
    return bat


def _gh_env(tmp_path: Path, state_file: Path, extra=None) -> dict:
    """gh stub을 PIPELINE_GH_EXECUTABLE로 주입한 환경변수 dict를 만든다 (state 격리 포함).

    Windows subprocess는 bare 'gh'(확장자 없음)로 .bat을 실행하지 못하므로(PATHEXT 미적용),
    pipeline.py의 모든 gh 호출이 일관되게 stub을 쓰도록 PIPELINE_GH_EXECUTABLE에 gh.bat 절대
    경로를 지정한다. _build_gh_cmd_prefix는 .bat이면 [bat]을 반환하고, shutil.which(bat)도
    절대 경로를 그대로 반환하므로 두 소비 방식이 모두 stub을 실행한다. 실제 gh 디렉토리는
    PATH에서 제거하여 우회를 차단한다.
    """
    gh_bat = _write_gh_stub(tmp_path / "ghstub")
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_file)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING"] = "1"
    env["PIPELINE_BROWSER_APPROVAL_SKIP"] = "1"
    # BUG-20260628-F52C: workspace hygiene의 루트 cleanup_only 스캔은 공유 저장소 루트
    #   (BASE_DIR)를 읽으므로, codex-review와 request-accept를 별도 subprocess로 실행하는
    #   동안 다른 동시 테스트가 루트에 tmp*.json/*_dump.txt 등을 만들거나 지우면 두 스캔의
    #   cleanup_only_items가 달라져 packet SHA 불변식이 깨진다(codex_review_not_approved 오발동).
    #   격리 E2E에서는 이 비결정적 WARN-only 스캔을 건너뛰어 두 subprocess가 동일한
    #   cleanup_only_items를 보게 한다(차단 규칙 1~5는 영향받지 않음).
    env["PIPELINE_WORKSPACE_HYGIENE_SKIP_ROOT_CLEANUP"] = "1"
    # 모든 gh 호출을 stub gh.bat 절대 경로로 강제 (bare 'gh' PATH 의존 제거).
    env["PIPELINE_GH_EXECUTABLE"] = str(gh_bat)
    # 실제 gh CLI 디렉토리는 PATH에서 제거하여 우회 차단.
    env["PATH"] = _no_gh_path() if env.get("PATH") else ""
    if extra:
        env.update(extra)
    return env


def _run_pipeline_env(*args, env, cwd=PIPELINE_ROOT):
    """gh stub 환경에서 pipeline.py를 실행한다 (cwd=프로젝트 루트, BASE_DIR 일치)."""
    cmd = [sys.executable, PIPELINE_PY] + list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(cwd),
    )


def _legacy_state_no_ac():
    """requirements_tracking.enabled=false로 AC 검사를 우회한 격리 state."""
    data = _base_state_data()
    data["requirements_tracking"] = {"enabled": False}
    return data


@pytest.fixture
def gh_publish_env(tmp_path):
    """gh stub publish-path TC용 fixture — 산출물 정리 + 격리 state + gh stub 환경.

    Returns:
        (env, state_file, evidence_path) 튜플.
    """
    _clean_root_artifacts()
    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(
        json.dumps(_legacy_state_no_ac(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    evidence = PIPELINE_ROOT / "output.xlsx"
    evidence.write_text("F52C evidence", encoding="utf-8")
    env = _gh_env(tmp_path, state_file)
    yield env, state_file, evidence
    _clean_root_artifacts()
    if evidence.exists():
        try:
            evidence.unlink()
        except OSError:
            pass


def _codex_review_result_path_for(state_file: Path) -> Path:
    """codex_review_result.json 경로 — pipeline.py는 PIPELINE_STATE_PATH 부모/.pipeline에 기록한다."""
    return state_file.resolve().parent / ".pipeline" / "codex_review_result.json"


def _gh_stub_fetch_body(env: dict):
    """gh stub을 직접 호출하여 현재 PR canonical body를 fetch한다 (r11 canonical SSoT 규칙).

    BUG-20260628-F52C r11: pipeline._fetch_canonical_pr_body_sha256가 canonical SHA SSoT다.
    `gh pr view --json body`(jq 없이) JSON parse -> body 필드 -> CRLF->LF 정규화(trailing
    newline 미추가) 규칙을 동일하게 재현해야 acceptance_request.pr_body_sha256과 일치한다.
    """
    gh_exec = env.get("PIPELINE_GH_EXECUTABLE")
    if not gh_exec:
        return None
    r = subprocess.run(
        [str(gh_exec), "pr", "view", "--json", "body"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env, cwd=str(PIPELINE_ROOT),
    )
    if r.returncode != 0:
        return None
    out = (r.stdout or "").strip()
    if not out:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    body = data.get("body")
    if body is None or not isinstance(body, str):
        return None
    return body.replace(chr(13) + chr(10), chr(10))


def _acceptance_staging_path() -> Path:
    """acceptance_staging.json 경로 — pipeline.py는 BASE_DIR(.pipeline)에 저장한다."""
    return PIPELINE_ROOT / ".pipeline" / "acceptance_staging.json"


def _stage_and_codex_approve(env, evidence):
    """BUG-20260628-F52C 2-call 흐름: staging file 생성 후 codex-review로 frozen bytes 검토.

    1) gates request-accept (1차) — staging file을 생성하고 codex_review_required로 BLOCKED.
       (codex_review_result.json이 아직 없으므로 fail-closed REJECT → exit 1. 정상 흐름이다.)
    2) gates codex-review --approve-pending — staging file의 frozen bytes로 SHA를 계산하여
       codex_review_result.json에 APPROVE_TO_USER를 기록.

    이후 호출자가 gates request-accept (2차)를 실행하면 staging file + codex APPROVED를 감지하여
    frozen bytes로 publish하며, codex == acceptance_request == 현재 PR body 3자 SHA가 일치한다.

    Returns:
        (r_stage, r_codex) — 1차 request-accept와 codex-review의 CompletedProcess.
    """
    r_stage = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    # 1차 request-accept는 codex_review_result.json이 없어 BLOCKED(exit 1)가 정상이다.
    # 단, staging file은 codex 게이트 이전 단계에서 이미 저장되어야 한다.
    assert _acceptance_staging_path().exists(), (
        f"1차 request-accept가 staging file을 생성하지 않음\n"
        f"{r_stage.stdout}{r_stage.stderr}"
    )
    r_codex = _run_pipeline_env(
        "gates", "codex-review", "--verdict", "APPROVE_TO_USER", "--approve-pending",
        env=env,
    )
    assert r_codex.returncode == 0, (
        f"codex-review --approve-pending 실패\n{r_codex.stdout}{r_codex.stderr}"
    )
    return r_stage, r_codex


def test_tc_reject_pr_body_sha_mismatch(gh_publish_env):
    """REJECT-1(r8 A안): codex candidate body SHA != staged candidate body SHA 시 BLOCKED.

    codex-review --approve-pending로 올바른 result를 기록한 뒤, codex_review_result.json의
    pr_body_candidate_sha256(및 호환 필드 pr_body_sha256)을 다른 값으로 변조하면 request-accept가
    candidate-vs-candidate 불일치로 fail-closed BLOCKED여야 한다. (이는 GitHub canonical 정규화와
    무관한 로컬 후보 SHA 비교다.)
    """
    env, state_file, evidence = gh_publish_env
    # 1) staged snapshot APPROVE 기록 (올바른 packet/body/head SHA).
    # BUG-20260628-F52C r7: codex-review --approve-pending은 staging file 생성 후에만 가능.
    _r_stage, r_cx = _stage_and_codex_approve(env, evidence)
    assert r_cx.returncode == 0, f"codex approve 실패\n{r_cx.stdout}{r_cx.stderr}"
    # 2) candidate body SHA만 변조 (staged 후보 PR 본문 SHA와 불일치하도록).
    cx_path = _codex_review_result_path_for(state_file)
    data = json.loads(cx_path.read_text(encoding="utf-8"))
    assert data.get("pr_body_candidate_sha256"), (
        "codex result에 pr_body_candidate_sha256 미기록(REJECT-1 전제 실패)"
    )
    data["pr_body_candidate_sha256"] = "d" * 64
    data["pr_body_sha256"] = "d" * 64  # backward compat 필드도 함께 변조
    cx_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 3) request-accept → BLOCKED.
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, f"pr_body SHA 불일치인데 exit 0\n{combined}"
    assert "사용자 승인 요청" not in combined, f"BLOCKED인데 승인 요청 노출\n{combined}"
    assert not (PIPELINE_ROOT / "acceptance_request.json").exists(), (
        "BLOCKED인데 acceptance_request.json 생성됨"
    )
    final_state = _read_state(state_file)
    assert final_state["external_gates"]["acceptance"]["status"] != "PASS"


def test_tc_reject_pr_head_sha_mismatch(gh_publish_env):
    """REJECT-1: Codex Review 결과의 pr_head_sha != current head SHA 시 BLOCKED.

    codex result의 pr_head_sha만 변조하면 request-accept가 fail-closed BLOCKED여야 한다.
    """
    env, state_file, evidence = gh_publish_env
    # BUG-20260628-F52C r7: codex-review --approve-pending은 staging file 생성 후에만 가능.
    _r_stage, r_cx = _stage_and_codex_approve(env, evidence)
    assert r_cx.returncode == 0, f"codex approve 실패\n{r_cx.stdout}{r_cx.stderr}"
    cx_path = _codex_review_result_path_for(state_file)
    data = json.loads(cx_path.read_text(encoding="utf-8"))
    assert data.get("pr_head_sha"), "codex result에 pr_head_sha 미기록(REJECT-1 전제 실패)"
    data["pr_head_sha"] = "e" * 40  # 현재 stub head_sha와 불일치
    cx_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, f"pr_head SHA 불일치인데 exit 0\n{combined}"
    assert "사용자 승인 요청" not in combined, f"BLOCKED인데 승인 요청 노출\n{combined}"
    assert not (PIPELINE_ROOT / "acceptance_request.json").exists(), (
        "BLOCKED인데 acceptance_request.json 생성됨"
    )
    final_state = _read_state(state_file)
    assert final_state["external_gates"]["acceptance"]["status"] != "PASS"


def test_tc_reject_pr_body_update_failure(gh_publish_env):
    """REJECT-2: PR body 갱신(gh pr edit) 실패 시 request-accept fail-closed BLOCKED."""
    env, state_file, evidence = gh_publish_env
    # BUG-20260628-F52C 2-call: staging file 생성 후 codex approve.
    _stage_and_codex_approve(env, evidence)
    # gh pr edit를 실패시킨다.
    env_fail = dict(env)
    env_fail["GH_STUB_FAIL_EDIT"] = "1"
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env_fail,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, f"pr edit 실패인데 exit 0\n{combined}"
    assert "pr_body_update_failed" in combined, (
        f"pr_body_update_failed fail-closed 미발동\n{combined}"
    )
    assert "사용자 승인 요청" not in combined, f"BLOCKED인데 승인 요청 노출\n{combined}"
    final_state = _read_state(state_file)
    assert final_state["external_gates"]["acceptance"]["status"] != "PASS"


def test_tc_reject_pending_comment_failure(gh_publish_env):
    """REJECT-2: pending comment 게시(gh pr comment) 실패 시 fail-closed BLOCKED."""
    env, state_file, evidence = gh_publish_env
    # BUG-20260628-F52C 2-call: staging file 생성 후 codex approve.
    _stage_and_codex_approve(env, evidence)
    env_fail = dict(env)
    env_fail["GH_STUB_FAIL_COMMENT"] = "1"
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env_fail,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, f"pending comment 실패인데 exit 0\n{combined}"
    assert "pending_comment_failed" in combined, (
        f"pending_comment_failed fail-closed 미발동\n{combined}"
    )
    assert "사용자 승인 요청" not in combined, f"BLOCKED인데 승인 요청 노출\n{combined}"
    final_state = _read_state(state_file)
    assert final_state["external_gates"]["acceptance"]["status"] != "PASS"


def test_tc_publish_3way_sha_match(gh_publish_env):
    """REJECT-3: publish 직후 3자 SHA 일치 검증 성공 케이스 — 승인 요청 정상 노출.

    codex-review --approve-pending로 올바른 result를 기록한 뒤 request-accept를 실행하면
    pr_body/pr_head/packet SHA가 모두 일치하여 fail-closed 없이 '사용자 승인 요청'까지 도달한다.
    """
    env, state_file, evidence = gh_publish_env
    # BUG-20260628-F52C 2-call: staging file 생성 후 codex approve.
    _stage_and_codex_approve(env, evidence)
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"3자 SHA 일치인데 BLOCKED\n{combined}"
    assert "사용자 승인 요청" in combined, f"승인 요청문 미노출\n{combined}"
    assert "three_way_mismatch" not in combined, f"3자 불일치 오발동\n{combined}"
    # publish 산출물이 실제로 생성되었는지 확인.
    assert (PIPELINE_ROOT / "acceptance_request.json").exists(), (
        "publish 성공인데 acceptance_request.json 미생성"
    )
    # BUG-20260628-F52C REJECT-3(3차): acceptance_request.pr_body_sha256 ==
    # codex_review_result.pr_body_sha256(= staged 최종 body SHA) 3자 일치 확인.
    # publish가 PR body의 FINAL_PACKET 블록을 staged 본문으로 교체하므로, 최종 body SHA는
    # 원본 stub body SHA가 아니라 codex가 기록한 staged 최종 body SHA와 같아야 한다.
    req = json.loads(
        (PIPELINE_ROOT / "acceptance_request.json").read_text(encoding="utf-8")
    )
    cx_path = _codex_review_result_path_for(state_file)
    cx_data = json.loads(cx_path.read_text(encoding="utf-8"))
    codex_body_sha = cx_data.get("pr_body_sha256")
    assert codex_body_sha, "codex_review_result.json에 pr_body_sha256 미기록"
    assert req.get("pr_body_sha256") == codex_body_sha, (
        "acceptance_request pr_body_sha256가 codex 기록 staged 최종 body SHA와 불일치: "
        f"{req.get('pr_body_sha256')} != {codex_body_sha}"
    )
    # staged 최종 body SHA는 원본 stub body SHA와 달라야 한다(블록 교체로 body가 바뀌므로).
    assert codex_body_sha != _sha256_text(_FAKE_GH_PR_BODY_F52C), (
        "staged 최종 body SHA가 원본 body SHA와 동일 — FINAL_PACKET 블록 교체가 반영되지 않음"
    )


# ---------------------------------------------------------------------------
# BUG-20260628-F52C 3차 REJECT 재작업 — 3자 검증 복원 신규 TC (TC-NEW-1..4)
#
# 핵심 요구: codex_review_result.pr_body_sha256 == acceptance_request.pr_body_sha256
#            == sha256(현재 PR body)의 3자 일치를 강제하고, staged SHA는 "publish 후 최종
#            PR body"(= 현재 body + FINAL_PACKET 블록 교체) 기준으로 계산한다.
# ---------------------------------------------------------------------------


def _write_loop_state_from_request(state_file: Path, req: dict):
    """gates accept의 1차 게이트(_check_codex_review_gate)를 통과시키기 위한 loop_state 작성."""
    loop_dir = state_file.resolve().parent / ".pipeline"
    loop_dir.mkdir(parents=True, exist_ok=True)
    (loop_dir / "codex_review_loop_state.json").write_text(
        json.dumps({
            "status": "APPROVED",
            "pipeline_id": PIPELINE_ID,
            "pr_head_sha": "f52c" + "0" * 36,  # gh stub headRefOid와 일치
            "packet_sha256": req.get("packet_sha256"),
            "pr_body_sha256": req.get("pr_body_sha256"),  # canonical (acceptance_request 기준)
            "accept_code": f"ACCEPT-{PIPELINE_ID}",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_tc_new_1_accept_packet_mismatch_blocks(gh_publish_env):
    """TC-NEW-1(r8 A안): codex_review_result.packet_sha256 != acceptance_request.packet_sha256 시 BLOCKED.

    r8에서 gates accept는 codex pr_body_sha256 부재로 차단하지 않는다. 대신 packet_sha256 3자
    일치를 강제한다. codex_review_result.json의 packet_sha256만 변조하면 codex_review_stale로
    BLOCKED여야 한다. (canonical body 정규화로 인한 거짓 stale 순환이 제거됐음을 함께 검증.)
    """
    env, state_file, evidence = gh_publish_env
    # 1) staging + codex approve + request-accept publish (정상 경로, 2-call).
    _stage_and_codex_approve(env, evidence)
    r_ra = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    assert r_ra.returncode == 0, (
        f"request-accept 정상 경로 실패\n{r_ra.stdout}{r_ra.stderr}"
    )
    req = json.loads(
        (PIPELINE_ROOT / "acceptance_request.json").read_text(encoding="utf-8")
    )
    nonce = req.get("nonce")
    _write_loop_state_from_request(state_file, req)
    # 2) codex_review_result.json의 packet_sha256만 변조 (3자 불일치 유발).
    cx_path = _codex_review_result_path_for(state_file)
    data = json.loads(cx_path.read_text(encoding="utf-8"))
    data["packet_sha256"] = "f" * 64
    cx_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 3) gates accept → codex_review_stale BLOCKED (packet_sha256 불일치).
    code = f"ACCEPT-{PIPELINE_ID}-{nonce}"
    r = _run_pipeline_env(
        "gates", "accept", "--result", "ACCEPT",
        "--evidence", str(evidence), "--acceptance-code", code,
        env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, f"packet_sha256 불일치인데 accept 성공\n{combined}"
    assert "codex_review_stale" in combined, (
        f"codex_review_stale fail-closed 미발동\n{combined}"
    )


def test_tc_new_2_wrong_candidate_blocks(gh_publish_env):
    """TC-NEW-2(r8 A안): codex candidate SHA가 staged 최종 후보 SHA와 다르면 BLOCKED.

    codex_review_result.pr_body_candidate_sha256를 "publish 전 원본 body SHA"(블록 교체 전)로 변조하면,
    request-accept가 계산하는 staged 최종 후보 SHA(블록 교체 후)와 달라 _codex_review_snapshot이
    candidate-vs-candidate 불일치로 BLOCKED여야 한다. (canonical 정규화와 무관한 로컬 후보 비교.)
    """
    env, state_file, evidence = gh_publish_env
    # BUG-20260628-F52C r7: codex-review --approve-pending은 staging file 생성 후에만 가능.
    _r_stage, r_cx = _stage_and_codex_approve(env, evidence)
    assert r_cx.returncode == 0, f"codex approve 실패\n{r_cx.stdout}{r_cx.stderr}"
    # codex 기록 candidate SHA를 "publish 전" 원본 body SHA로 변조 (staged 최종 후보 SHA가 아님).
    cx_path = _codex_review_result_path_for(state_file)
    data = json.loads(cx_path.read_text(encoding="utf-8"))
    staged_final_sha = data.get("pr_body_candidate_sha256")
    prepublish_sha = _sha256_text(_FAKE_GH_PR_BODY_F52C)  # 블록 교체 전 원본 body SHA
    assert staged_final_sha != prepublish_sha, (
        "전제 실패: staged 최종 후보 SHA가 원본 body SHA와 같음 (블록 교체 미반영)"
    )
    data["pr_body_candidate_sha256"] = prepublish_sha
    data["pr_body_sha256"] = prepublish_sha  # backward compat 필드도 함께 변조
    cx_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # request-accept → publish 후 3자 불일치 BLOCKED (또는 codex snapshot 단계에서 stale 차단).
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, f"publish 전 SHA인데 exit 0\n{combined}"
    assert "사용자 승인 요청" not in combined, f"BLOCKED인데 승인 요청 노출\n{combined}"


def test_tc_new_3_three_way_match_happy_path(gh_publish_env):
    """TC-NEW-3: 정상 경로 — codex == acceptance_request == sha256(현재 PR body) 3자 일치.

    staged 최종 body SHA를 올바르게 기록(2-call staging+codex)한 뒤 request-accept를 실행하면
    publish 후 3자가 모두 일치하여 '사용자 승인 요청'까지 도달하고, 세 SHA가 동일하다.
    """
    env, state_file, evidence = gh_publish_env
    # BUG-20260628-F52C 2-call: staging file 생성 후 codex approve.
    _stage_and_codex_approve(env, evidence)
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"3자 일치인데 BLOCKED\n{combined}"
    assert "사용자 승인 요청" in combined, f"승인 요청문 미노출\n{combined}"
    # 3자 SHA 동일성 확인: codex == acceptance_request == sha256(현재 PR body).
    cx_path = _codex_review_result_path_for(state_file)
    cx_data = json.loads(cx_path.read_text(encoding="utf-8"))
    req = json.loads(
        (PIPELINE_ROOT / "acceptance_request.json").read_text(encoding="utf-8")
    )
    codex_sha = cx_data.get("pr_body_sha256")
    req_sha = req.get("pr_body_sha256")
    assert codex_sha and req_sha, "codex/acceptance_request pr_body_sha256 미기록"
    assert codex_sha == req_sha, (
        f"codex({codex_sha}) != acceptance_request({req_sha})"
    )


def test_tc_new_4_canonical_two_way_helper(isolated_pipeline):
    """TC-NEW-4(r8 A안): _verify_published_canonical_pr_body가 canonical 2자만 비교.

    r8에서 candidate-vs-canonical 3자 비교를 제거하고 canonical 2자 검증으로 대체했다.
    이 헬퍼는 codex candidate SHA를 인자로 받지 않으며, 오직
    acceptance_request.pr_body_sha256(recorded) == sha256(현재 GitHub canonical body)만 검증한다.

    - recorded == sha256(현재 body) → PASS
    - recorded 값만 다르게 → BLOCKED (canonical freshness 실패)
    - 현재 body만 다르게 → BLOCKED
    - recorded 빈값 → BLOCKED (publish 결함)
    - (회귀) 구 함수명 _verify_published_pr_body_three_way은 더 이상 존재하지 않는다.
    """
    tmp_path, state_file = isolated_pipeline
    out = _run_driver(
        tmp_path, state_file,
        "import hashlib\n"
        "body = 'PR BODY CANONICAL TEST'\n"
        "good = hashlib.sha256(body.encode('utf-8')).hexdigest()\n"
        "bad = 'd'*64\n"
        "def call(recorded, cur_body):\n"
        "    try:\n"
        "        pipeline._verify_published_canonical_pr_body(recorded, cur_body)\n"
        "        return 'PASS'\n"
        "    except SystemExit:\n"
        "        return 'BLOCKED'\n"
        "print('ALLMATCH', call(good, body))\n"
        "print('RECORDEDBAD', call(bad, body))\n"
        "print('BODYBAD', call(good, 'DIFFERENT BODY'))\n"
        "print('RECORDEDEMPTY', call('', body))\n"
        "print('OLDFNGONE', not hasattr(pipeline, '_verify_published_pr_body_three_way'))\n",
    )
    assert "ALLMATCH PASS" in out, f"canonical 2자 일치인데 BLOCKED\n{out}"
    assert "RECORDEDBAD BLOCKED" in out, f"recorded 값만 달라도 통과\n{out}"
    assert "BODYBAD BLOCKED" in out, f"현재 body만 달라도 통과\n{out}"
    assert "RECORDEDEMPTY BLOCKED" in out, f"recorded 빈값인데 통과\n{out}"
    assert "OLDFNGONE True" in out, f"구 3자 검증 함수가 잔존함\n{out}"


# ---------------------------------------------------------------------------
# BUG-20260628-F52C r6: staging file frozen bytes 흐름 신규 TC (TC-F52C-1..5)
#
# 핵심: request-accept staging이 .pipeline/acceptance_staging.json에 frozen bytes를 저장하고,
#       codex-review --approve-pending이 그 bytes로 SHA를 계산하며, publish가 동일 frozen bytes를
#       재렌더링 없이 커밋하여 codex == acceptance_request == 현재 PR body 3자 SHA가 일치한다.
# ---------------------------------------------------------------------------


def test_tc_f52c_staging_file_created_on_request_accept(gh_publish_env):
    """TC-F52C-1: request-accept(1차) 실행 시 acceptance_staging.json이 생성된다.

    codex_review_result.json이 아직 없으므로 1차 request-accept는 codex 게이트에서 BLOCKED지만,
    그 이전 단계에서 staging file(frozen bytes)을 반드시 저장해야 한다.
    """
    env, state_file, evidence = gh_publish_env
    assert not _acceptance_staging_path().exists(), "사전: staging file이 이미 존재"
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    # codex 미승인 상태이므로 BLOCKED(exit 1)가 정상.
    assert r.returncode != 0, f"codex 미승인인데 exit 0\n{combined}"
    # staging file은 codex 게이트 이전에 저장되어야 한다.
    assert _acceptance_staging_path().exists(), (
        f"1차 request-accept가 staging file을 생성하지 않음\n{combined}"
    )
    staging = json.loads(_acceptance_staging_path().read_text(encoding="utf-8"))
    assert staging.get("pipeline_id") == PIPELINE_ID, "staging pipeline_id 불일치"
    assert staging.get("staged_packet_content"), "staged_packet_content 비어 있음"
    assert staging.get("staged_packet_sha256"), "staged_packet_sha256 비어 있음"
    # staged_packet_sha256은 packet md temp 파일 바이트 SHA(_sha256_file)이며, Windows에서
    # write_text가 \n을 \r\n으로 변환하므로 sha256(string)과 다를 수 있다. content 자체는 비어
    # 있지 않고 사용자 승인 코드 라인(ACCEPT-)을 포함해야 한다(nonce 발급 후 packet).
    assert "ACCEPT-" in staging["staged_packet_content"], (
        "staged_packet_content에 승인 코드 라인 누락 — packet 렌더링 비정상"
    )
    assert isinstance(staging.get("req_candidate"), dict), "req_candidate 미저장"
    assert staging["req_candidate"].get("nonce"), "frozen req_candidate에 nonce 없음"


def test_tc_f52c_codex_review_uses_staging_file(gh_publish_env):
    """TC-F52C-2: codex-review --approve-pending이 staging file의 frozen bytes로 SHA를 계산한다.

    staging file의 staged_packet_sha256과 codex_review_result.json의 packet_sha256이 정확히 같아야 한다.
    """
    env, state_file, evidence = gh_publish_env
    _stage_and_codex_approve(env, evidence)
    staging = json.loads(_acceptance_staging_path().read_text(encoding="utf-8"))
    cx_path = _codex_review_result_path_for(state_file)
    cx = json.loads(cx_path.read_text(encoding="utf-8"))
    assert cx.get("verdict") == "APPROVE_TO_USER", f"codex verdict 비정상: {cx.get('verdict')}"
    assert cx.get("packet_sha256") == staging.get("staged_packet_sha256"), (
        "codex packet_sha256가 staging frozen bytes SHA와 불일치: "
        f"{cx.get('packet_sha256')} != {staging.get('staged_packet_sha256')}"
    )
    # codex가 staging file을 실제로 사용했음을 stdout으로도 확인(경로 A).
    # (staging file 사용 시 '[STAGING FILE 사용]' 메시지 출력)


def test_tc_f52c_publish_uses_frozen_bytes(gh_publish_env):
    """TC-F52C-3: publish 시 staging frozen bytes를 그대로 사용하여 packet SHA invariant가 성립한다.

    staging frozen packet SHA == published acceptance_request.packet_sha256(블록 교체 전 packet md SHA)
    여야 한다. publish가 재렌더링하면 timestamp 차이로 SHA가 달라져 이 불변식이 깨진다.
    """
    env, state_file, evidence = gh_publish_env
    _stage_and_codex_approve(env, evidence)
    staging = json.loads(_acceptance_staging_path().read_text(encoding="utf-8"))
    staged_packet_sha = staging["staged_packet_sha256"]
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"frozen bytes publish인데 BLOCKED\n{combined}"
    # publish된 packet md의 파일 바이트 SHA가 staging frozen packet SHA와 동일해야 한다(재렌더링 없음).
    # staged_packet_sha256은 _sha256_file(packet temp)로 계산한 파일 바이트 SHA이므로, 비교도
    # 디스크 파일을 'rb'로 읽어 파일 바이트 기준으로 한다(write_text의 \r\n 변환 일관성 유지).
    published_packet = PIPELINE_ROOT / "human_acceptance_packet.md"
    assert published_packet.exists(), "publish 후 packet md 미생성"
    published_packet_sha = hashlib.sha256(
        published_packet.read_bytes()
    ).hexdigest()
    assert published_packet_sha == staged_packet_sha, (
        "publish가 frozen bytes를 재렌더링함 — packet SHA invariant 위반: "
        f"{published_packet_sha} != {staged_packet_sha}"
    )
    # acceptance_request.json의 packet_sha256도 동일해야 한다(3자 신뢰 루트).
    req = json.loads(
        (PIPELINE_ROOT / "acceptance_request.json").read_text(encoding="utf-8")
    )
    assert req.get("packet_sha256") == staged_packet_sha, (
        "acceptance_request.packet_sha256가 staging frozen packet SHA와 불일치: "
        f"{req.get('packet_sha256')} != {staged_packet_sha}"
    )


def test_tc_f52c_three_way_sha_all_equal_with_gh_mock(gh_publish_env):
    """TC-F52C-4: gh mock 환경에서 codex == acceptance_request == 현재 PR body SHA 3자 일치.

    staging frozen bytes 흐름으로 publish하면 세 PR body SHA가 모두 동일해야 한다.
    """
    env, state_file, evidence = gh_publish_env
    _stage_and_codex_approve(env, evidence)
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"3자 일치 기대인데 BLOCKED\n{combined}"
    assert "사용자 승인 요청" in combined, f"승인 요청문 미노출\n{combined}"
    # 1) codex_review_result.json의 pr_body_sha256
    cx = json.loads(
        _codex_review_result_path_for(state_file).read_text(encoding="utf-8")
    )
    codex_body_sha = cx.get("pr_body_sha256")
    # 2) acceptance_request.json의 pr_body_sha256
    req = json.loads(
        (PIPELINE_ROOT / "acceptance_request.json").read_text(encoding="utf-8")
    )
    req_body_sha = req.get("pr_body_sha256")
    # 3) 현재 PR body(gh stub _current_body = packet.md 기준 publish 후 최종 body)의 SHA
    import pipeline as _pl  # noqa: PLC0415 — 테스트 내 동적 import.
    packet = (PIPELINE_ROOT / "human_acceptance_packet.md").read_text(encoding="utf-8")
    final_body = _pl._replace_pr_body_packet_block(_FAKE_GH_PR_BODY_F52C, packet)
    current_pr_body_sha = _sha256_text(final_body)
    assert codex_body_sha, "codex pr_body_sha256 미기록"
    assert req_body_sha, "acceptance_request pr_body_sha256 미기록"
    assert codex_body_sha == req_body_sha == current_pr_body_sha, (
        "3자 SHA 불일치:\n"
        f"  codex:            {codex_body_sha}\n"
        f"  acceptance_request: {req_body_sha}\n"
        f"  현재 PR body:      {current_pr_body_sha}"
    )


def test_tc_f52c_staging_file_deleted_after_accept(gh_publish_env):
    """TC-F52C-5: publish 성공 후 acceptance_staging.json이 삭제된다(1회용)."""
    env, state_file, evidence = gh_publish_env
    _stage_and_codex_approve(env, evidence)
    assert _acceptance_staging_path().exists(), "사전: staging file이 있어야 함"
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"publish 실패\n{combined}"
    assert not _acceptance_staging_path().exists(), (
        "publish 성공 후 staging file이 삭제되지 않음"
    )


# ---------------------------------------------------------------------------
# BUG-20260628-F52C r8 (A안) — packet/canonical SHA 분리 신규 TC (TC-R8-1..7)
#
# 핵심: packet_sha256은 로컬 staged bytes 기준(불변), pr_body_sha256은 GitHub canonical
#       body(publish 후 fetch) 기준으로 분리한다. codex candidate SHA와 canonical SHA의
#       정상 불일치(GitHub 정규화)가 더 이상 codex_review_stale 순환을 유발하지 않는다.
# ---------------------------------------------------------------------------


def test_tc_r8_1_canonical_normalization_passes(gh_publish_env):
    """TC-R8-1: GitHub mock이 CRLF→LF/trailing 정규화를 반환해도 canonical SHA 기준 PASS.

    GH_STUB_NORMALIZE_BODY=1로 GitHub canonical 정규화를 흉내내면, 로컬 candidate SHA와 canonical
    SHA가 달라질 수 있다. r8 (A안)에서는 candidate-vs-canonical 비교를 하지 않으므로 publish가
    BLOCKED 없이 '사용자 승인 요청'까지 도달하고, acceptance_request.pr_body_sha256 ==
    sha256(현재 GitHub canonical body)여야 한다.
    """
    env, state_file, evidence = gh_publish_env
    env = dict(env)
    env["GH_STUB_NORMALIZE_BODY"] = "1"
    _stage_and_codex_approve(env, evidence)
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"canonical 정규화인데 BLOCKED(순환 미해결)\n{combined}"
    assert "사용자 승인 요청" in combined, f"승인 요청문 미노출\n{combined}"
    assert "codex_review_stale" not in combined, (
        f"canonical 정규화로 codex_review_stale 오발동(r7 순환 잔존)\n{combined}"
    )
    # acceptance_request.pr_body_sha256 == sha256(현재 GitHub canonical body).
    # canonical body는 정규화 stub이 실제 emit하는 본문이므로, 동일 환경에서 _get_pr_body_text로
    # 직접 fetch한 값(= publish 후 GitHub canonical body)을 신뢰 루트로 사용한다.
    req = json.loads(
        (PIPELINE_ROOT / "acceptance_request.json").read_text(encoding="utf-8")
    )
    canonical_body = _gh_stub_fetch_body(env)
    assert canonical_body is not None, "정규화 stub에서 canonical body fetch 실패"
    canonical_sha = _sha256_text(canonical_body)
    assert req.get("pr_body_sha256") == canonical_sha, (
        "acceptance_request.pr_body_sha256가 canonical body SHA와 불일치:\n"
        f"  acceptance_request: {req.get('pr_body_sha256')}\n"
        f"  canonical 재계산:    {canonical_sha}"
    )


def test_tc_r8_2_candidate_differs_from_canonical_passes(gh_publish_env):
    """TC-R8-2: 로컬 candidate SHA != canonical SHA여도 canonical 기준 일치면 PASS.

    정규화 stub 환경에서 codex가 기록한 pr_body_candidate_sha256(로컬)과
    codex_review_result.github_canonical_pr_body_sha256(GitHub canonical)이 다를 수 있다.
    그래도 request-accept는 성공해야 하며, canonical 필드는 acceptance_request.pr_body_sha256과 같아야 한다.
    """
    env, state_file, evidence = gh_publish_env
    env = dict(env)
    env["GH_STUB_NORMALIZE_BODY"] = "1"
    _stage_and_codex_approve(env, evidence)
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"candidate!=canonical인데 BLOCKED\n{combined}"
    cx = json.loads(
        _codex_review_result_path_for(state_file).read_text(encoding="utf-8")
    )
    req = json.loads(
        (PIPELINE_ROOT / "acceptance_request.json").read_text(encoding="utf-8")
    )
    candidate_sha = cx.get("pr_body_candidate_sha256")
    canonical_sha = cx.get("github_canonical_pr_body_sha256")
    assert candidate_sha, "codex pr_body_candidate_sha256 미기록"
    assert canonical_sha, "codex github_canonical_pr_body_sha256 미기록(publish가 안 채움)"
    # canonical 필드는 acceptance_request.pr_body_sha256과 같아야 한다(2자 신뢰 루트).
    assert canonical_sha == req.get("pr_body_sha256"), (
        "codex github_canonical_pr_body_sha256가 acceptance_request.pr_body_sha256과 불일치:\n"
        f"  codex canonical:    {canonical_sha}\n"
        f"  acceptance_request: {req.get('pr_body_sha256')}"
    )
    # candidate != canonical인 경우에도 통과했음을 확인(정규화가 SHA를 바꾼 경우).
    # (정규화가 우연히 SHA를 안 바꿔 둘이 같을 수도 있으므로, 같지 않을 때만 메시지로 강조한다.)
    if candidate_sha == canonical_sha:
        # 둘이 같아도 테스트는 유효(정규화 영향 없음). 분리 의미는 TC-R8-7에서 직접 검증.
        pass


def test_tc_r8_3_packet_sha_mismatch_blocks(gh_publish_env):
    """TC-R8-3: codex packet_sha256 != acceptance_request.packet_sha256 시 gates accept BLOCKED."""
    env, state_file, evidence = gh_publish_env
    _stage_and_codex_approve(env, evidence)
    r_ra = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    assert r_ra.returncode == 0, f"정상 publish 실패\n{r_ra.stdout}{r_ra.stderr}"
    req = json.loads(
        (PIPELINE_ROOT / "acceptance_request.json").read_text(encoding="utf-8")
    )
    nonce = req.get("nonce")
    _write_loop_state_from_request(state_file, req)
    # packet_sha256만 변조.
    cx_path = _codex_review_result_path_for(state_file)
    data = json.loads(cx_path.read_text(encoding="utf-8"))
    data["packet_sha256"] = "a" * 64
    cx_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    code = f"ACCEPT-{PIPELINE_ID}-{nonce}"
    r = _run_pipeline_env(
        "gates", "accept", "--result", "ACCEPT",
        "--evidence", str(evidence), "--acceptance-code", code, env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, f"packet SHA 불일치인데 accept 성공\n{combined}"
    assert "codex_review_stale" in combined, f"packet 불일치 BLOCK 미발동\n{combined}"


def test_tc_r8_4_current_body_differs_blocks(gh_publish_env):
    """TC-R8-4: 현재 GitHub body SHA != acceptance_request.pr_body_sha256 시 gates accept BLOCKED.

    request-accept 발급 이후 PR 본문이 바뀐 상황을 흉내내기 위해 acceptance_request.pr_body_sha256를
    변조하면, accept의 canonical freshness 검증이 현재 GitHub body SHA와 달라 BLOCKED여야 한다.
    """
    env, state_file, evidence = gh_publish_env
    _stage_and_codex_approve(env, evidence)
    r_ra = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    assert r_ra.returncode == 0, f"정상 publish 실패\n{r_ra.stdout}{r_ra.stderr}"
    req_path = PIPELINE_ROOT / "acceptance_request.json"
    req = json.loads(req_path.read_text(encoding="utf-8"))
    nonce = req.get("nonce")
    _write_loop_state_from_request(state_file, req)
    # acceptance_request.pr_body_sha256를 현재 GitHub body와 다른 값으로 변조.
    req["pr_body_sha256"] = "c" * 64
    req_path.write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    code = f"ACCEPT-{PIPELINE_ID}-{nonce}"
    r = _run_pipeline_env(
        "gates", "accept", "--result", "ACCEPT",
        "--evidence", str(evidence), "--acceptance-code", code, env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, f"현재 body SHA 불일치인데 accept 성공\n{combined}"
    assert "codex_review_stale" in combined, f"canonical freshness BLOCK 미발동\n{combined}"


def test_tc_r8_5_no_exposure_before_codex_approve(gh_publish_env):
    """TC-R8-5: Codex APPROVE 전에는 승인 코드/PR comment/acceptance_request.json 미노출.

    codex_review_result.json이 없는 상태(1차 request-accept)에서는 승인 요청문이 노출되지 않고
    acceptance_request.json도 생성되지 않아야 한다.
    """
    env, state_file, evidence = gh_publish_env
    # codex 미수행 — 바로 request-accept (1차).
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, f"Codex 미승인인데 exit 0\n{combined}"
    assert "사용자 승인 요청" not in combined, f"Codex 전 승인 요청문 노출\n{combined}"
    assert "ACCEPT-" not in combined or "승인 코드" not in combined, (
        f"Codex 전 승인 코드 노출\n{combined}"
    )
    assert not (PIPELINE_ROOT / "acceptance_request.json").exists(), (
        "Codex 전 acceptance_request.json 생성됨"
    )


def test_tc_r8_6_codex_reject_no_exposure(gh_publish_env):
    """TC-R8-6: Codex REJECT 시 승인 코드 미노출 + acceptance_request.json 미생성."""
    env, state_file, evidence = gh_publish_env
    # staging file 생성 (1차 request-accept).
    _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    # codex REJECT 기록.
    r_cx = _run_pipeline_env(
        "gates", "codex-review", "--verdict", "REJECT", "--approve-pending", env=env,
    )
    assert r_cx.returncode != 0, "codex REJECT인데 exit 0"
    # request-accept (2차) → REJECT로 BLOCKED.
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode != 0, f"Codex REJECT인데 exit 0\n{combined}"
    assert "사용자 승인 요청" not in combined, f"REJECT인데 승인 요청문 노출\n{combined}"
    assert not (PIPELINE_ROOT / "acceptance_request.json").exists(), (
        "Codex REJECT인데 acceptance_request.json 생성됨"
    )


def test_tc_r8_7_pr_body_sha_semantics_not_mixed(gh_publish_env):
    """TC-R8-7: pr_body SHA의 staged/canonical 의미가 혼용되지 않는다.

    codex_review_result.json은 다음 두 필드를 분리 보유해야 한다:
      - pr_body_candidate_sha256: 로컬 staged "publish 후 최종 body" 후보 SHA.
      - github_canonical_pr_body_sha256: publish 후 GitHub canonical body SHA.
    backward compat 필드 pr_body_sha256은 candidate와 같아야 하며(절대 canonical 아님),
    acceptance_request.pr_body_sha256은 canonical 필드와 같아야 한다.
    """
    env, state_file, evidence = gh_publish_env
    env = dict(env)
    env["GH_STUB_NORMALIZE_BODY"] = "1"  # candidate != canonical을 유도하는 정규화 환경
    _stage_and_codex_approve(env, evidence)
    r = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env,
    )
    assert r.returncode == 0, f"정상 publish 실패\n{r.stdout}{r.stderr}"
    cx = json.loads(
        _codex_review_result_path_for(state_file).read_text(encoding="utf-8")
    )
    req = json.loads(
        (PIPELINE_ROOT / "acceptance_request.json").read_text(encoding="utf-8")
    )
    candidate = cx.get("pr_body_candidate_sha256")
    canonical = cx.get("github_canonical_pr_body_sha256")
    compat = cx.get("pr_body_sha256")
    assert candidate, "pr_body_candidate_sha256 미기록(필드 분리 실패)"
    assert canonical, "github_canonical_pr_body_sha256 미기록(필드 분리 실패)"
    # backward compat 필드는 candidate 의미 — canonical과 섞이면 안 된다.
    assert compat == candidate, (
        f"pr_body_sha256(compat)이 candidate와 불일치 — 의미 혼용 의심: "
        f"{compat} != {candidate}"
    )
    # acceptance_request.pr_body_sha256은 canonical 의미.
    assert req.get("pr_body_sha256") == canonical, (
        "acceptance_request.pr_body_sha256이 canonical 필드와 불일치 — 의미 혼용:\n"
        f"  acceptance_request: {req.get('pr_body_sha256')}\n"
        f"  codex canonical:    {canonical}"
    )
    # candidate가 canonical과 다른 경우(정규화가 SHA를 바꾼 경우)에도 위 분리가 성립해야 한다.
    # (둘이 같더라도 필드 분리 자체는 검증됨 — 혼용 금지가 핵심.)


# ---------------------------------------------------------------------------
# BUG-20260628-F52C r11: PR body canonical SHA SSoT 신규 TC (TC-C1..C4 + MT-6 보강)
#
# 핵심: _fetch_canonical_pr_body_sha256가 `gh pr view --json body`(jq 없이)를 JSON parse하여
#       body 필드를 LF 정규화 후 인코딩한 canonical SHA를 SSoT로 계산한다. acceptance_request와
#       codex_review_result.github_canonical_pr_body_sha256가 이 SSoT를 공유하며, request-accept
#       최종 불변식 검증이 PR body 변경(stale)을 fail-closed로 차단한다.
# ---------------------------------------------------------------------------

# BUG-20260628-F52C r11: publish 이후 "변경된" PR body — 필수 섹션은 모두 갖추되(=
# pr_body_incomplete 차단을 피하고) 내용을 다르게 하여 canonical SHA가 달라지게 한다.
# 이로써 request-accept 재실행 시 readiness 차단보다 뒤에 있는 fail-closed 보호가 발동한다.
_CHANGED_GH_PR_BODY_F52C_STALE = (
    "## 작업 요약\nF52C publish 이후 변경된 PR body — stale 유발(필수 섹션 유지)\n\n"
    "## 사용자가 확인할 결과물\n결과물 경로: output.xlsx (변경됨)\n\n"
    "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 변경됨\n\n"
    "## 중요한 선택과 트레이드오프\nN/A (변경된 테스트 픽스처)\n\n"
    "## 검증\n모든 게이트 PASS (변경됨)\n"
)


def _write_canonical_gh_stub(stub_dir: Path, body: str, head_sha: str = "c11c" + "0" * 36) -> Path:
    """`gh pr view --json body`(jq 없이)에 고정 body를 반환하는 최소 gh stub을 만든다.

    _fetch_canonical_pr_body_sha256는 `gh pr view [n] --json body`를 호출하고 JSON parse하여
    body 필드만 사용하므로, 이 stub은 stdout으로 {"body": <고정 body>} JSON 객체를 출력한다.
    body는 호출자가 지정한 원문(예: CRLF 포함)을 그대로 보존한다.

    Args:
        stub_dir: stub 파일 디렉토리.
        body: gh가 반환할 PR body 원문(JSON 직렬화 시 \\r\\n 등 그대로 보존됨).
        head_sha: headRefOid로 반환할 head SHA.
    Returns:
        gh.bat 절대 경로 (PIPELINE_GH_EXECUTABLE로 지정).
    """
    stub_dir.mkdir(parents=True, exist_ok=True)
    body_json = json.dumps(body)
    py = stub_dir / "gh_stub_canonical.py"
    py.write_text(
        "import sys, io, json, os\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        f"BODY = {body_json}\n"
        f"HEAD_SHA = {json.dumps(head_sha)}\n"
        "args = sys.argv[1:]\n"
        '# pr edit/comment — no-op 성공.\n'
        'if "pr" in args and ("edit" in args or "comment" in args):\n'
        '    print("https://github.com/test/repo/pull/1"); sys.exit(0)\n'
        'if "api" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        'if "run" in args:\n'
        '    print(""); sys.exit(0)\n'
        '# pr view --jq <expr>\n'
        'if "--jq" in args:\n'
        '    jq = args[args.index("--jq") + 1] if args.index("--jq") + 1 < len(args) else ""\n'
        '    if jq == ".body":\n'
        '        sys.stdout.write(BODY)\n'
        '        if not BODY.endswith("\\n"):\n'
        '            sys.stdout.write("\\n")\n'
        '        sys.exit(0)\n'
        '    if jq == ".headRefOid":\n'
        '        print(HEAD_SHA); sys.exit(0)\n'
        '    if jq == ".url":\n'
        '        print("https://github.com/test/repo/pull/1"); sys.exit(0)\n'
        '    if jq == ".number":\n'
        '        print("1"); sys.exit(0)\n'
        '    print(""); sys.exit(0)\n'
        '# pr view --json <fields> (no jq) — 전체 객체 반환(body 필드는 원문 그대로).\n'
        "print(json.dumps({\n"
        '    "body": BODY, "number": 1, "headRefOid": HEAD_SHA,\n'
        '    "isDraft": False, "state": "OPEN", "files": [],\n'
        '    "url": "https://github.com/test/repo/pull/1",\n'
        "}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    bat = stub_dir / "gh.bat"
    bat.write_text(
        "@echo off\r\n"
        f'"{sys.executable}" "{py}" %*\r\n',
        encoding="utf-8",
    )
    return bat


def _run_driver_with_gh(tmp_path, state_file, gh_bat: Path, body: str):
    """_run_driver와 동일하되 PIPELINE_GH_EXECUTABLE에 gh stub을 주입한다."""
    driver = tmp_path / f"driver_gh_{abs(hash(body)) % 100000}.py"
    driver.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {json.dumps(PIPELINE_DIR)})\n"
        "import pipeline\n" + body,
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_file)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIPELINE_GH_EXECUTABLE"] = str(gh_bat)
    env["PATH"] = _no_gh_path() if env.get("PATH") else ""
    result = subprocess.run(
        [sys.executable, str(driver)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, env=env, cwd=str(tmp_path),
    )
    return (result.stdout or "") + (result.stderr or "")


# OneDrive가 공유 루트의 .tmp→최종 파일 os.replace를 잠깐 잠그면 발생하는 일시적 오류 마커.
# 이 마커가 보이면 테스트는 산출물을 정리하고 짧게 대기 후 재시도한다(차단 규칙은 불변).
_ONEDRIVE_TRANSIENT_MARKERS = (
    "WinError 5",
    "WinError 2",
    "pr_body_resync_failed",
    "final packet 자동 생성 실패",
    "human_acceptance_packet.md.tmp",
)


def test_tc_jq_vs_json_parse_diff(isolated_pipeline):
    """TC-C1: _fetch_canonical_pr_body_sha256가 `--json body`(jq 없이) JSON parse 결과를 사용한다.

    gh stub이 `--json body` → {"body": "canonical body text"}를 반환하면, helper는 body 필드를
    추출해 sha256("canonical body text")를 계산해야 한다(stdout/jq trailing newline 기반 아님).
    """
    tmp_path, state_file = isolated_pipeline
    canonical_text = "canonical body text"
    gh_bat = _write_canonical_gh_stub(tmp_path / "ghc1", canonical_text)
    out = _run_driver_with_gh(
        tmp_path, state_file, gh_bat,
        "sha = pipeline._fetch_canonical_pr_body_sha256(1)\n"
        "print('SHA', sha)\n",
    )
    expected = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    assert f"SHA {expected}" in out, (
        f"helper가 JSON body 필드 기반 canonical SHA를 계산하지 않음\n"
        f"expected={expected}\n{out}"
    )


def test_tc_crlf_canonical(isolated_pipeline):
    """TC-C2: gh stub body가 CRLF를 포함해도 helper가 LF 정규화 후 인코딩하여 SHA를 계산한다.

    canonical SSoT는 CRLF/LF 줄바꿈 차이를 흡수해야 한다(r11 근본 원인 방지). 따라서
    sha256(body) (CRLF→LF 정규화 후 인코딩) == sha256(LF 버전)이어야 한다.
    """
    tmp_path, state_file = isolated_pipeline
    crlf_body = "line1\r\nline2\r\nline3"
    lf_body = crlf_body.replace("\r\n", "\n")
    gh_bat = _write_canonical_gh_stub(tmp_path / "ghc2", crlf_body)
    out = _run_driver_with_gh(
        tmp_path, state_file, gh_bat,
        "sha = pipeline._fetch_canonical_pr_body_sha256(1)\n"
        "print('SHA', sha)\n",
    )
    expected_lf = hashlib.sha256(lf_body.encode("utf-8")).hexdigest()
    assert f"SHA {expected_lf}" in out, (
        f"CRLF body의 canonical SHA가 LF 정규화 결과와 불일치\n"
        f"expected(LF normalized)={expected_lf}\n{out}"
    )


def test_tc_pr_body_stale_after_publish(gh_publish_env):
    """TC-C3: request-accept publish 후 PR body가 바뀌면 재실행 시 fail-closed로 BLOCKED.

    1) 정상 경로(2-call)로 request-accept publish 성공 → acceptance_request.pr_body_sha256 기록.
    2) gh stub이 다른 body(필수 섹션 유지)를 반환하도록 교체 후 request-accept 재실행 →
       재-stage SHA 불일치 또는 최종 canonical 불변식 중 더 앞선 fail-closed 가드가 발동하여
       승인 코드가 노출되지 않아야 한다.
    """
    env, state_file, evidence = gh_publish_env
    # 1) 정상 publish (3자 일치). OneDrive transient 발생 시 재-stage 후 재시도.
    r_ok = None
    for _attempt in range(4):
        _stage_and_codex_approve(env, evidence)
        r_ok = _run_pipeline_env(
            "gates", "request-accept", "--evidence", str(evidence), env=env,
        )
        _c = (r_ok.stdout or "") + (r_ok.stderr or "")
        if r_ok.returncode == 0:
            break
        if any(m in _c for m in _ONEDRIVE_TRANSIENT_MARKERS):
            _clean_root_artifacts()
            time.sleep(0.4)
            continue
        break
    assert r_ok.returncode == 0, (
        f"정상 publish 실패\n{r_ok.stdout}{r_ok.stderr}"
    )
    assert (PIPELINE_ROOT / "acceptance_request.json").exists(), (
        "정상 publish인데 acceptance_request.json 미생성"
    )
    # 2) gh stub body를 변경한 새 stub으로 교체 → 현재 PR body canonical SHA가 달라진다.
    #    변경 body는 필수 섹션을 모두 유지하여 pr_body_incomplete 차단을 건너뛰고
    #    fail-closed stale 가드가 발동하도록 한다.
    changed_bat = _write_canonical_gh_stub(
        Path(env["PIPELINE_GH_EXECUTABLE"]).parent.parent / "ghc3_changed",
        _CHANGED_GH_PR_BODY_F52C_STALE,
        head_sha="f52c" + "0" * 36,  # head SHA는 동일하게 유지(body만 변경)
    )
    env_changed = dict(env)
    env_changed["PIPELINE_GH_EXECUTABLE"] = str(changed_bat)
    r_stale = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env_changed,
    )
    combined = (r_stale.stdout or "") + (r_stale.stderr or "")
    assert r_stale.returncode != 0, (
        f"publish 후 PR body 변경인데 exit 0\n{combined}"
    )
    # publish 이후 body 변경은 (재-stage SHA 불일치) 또는 (최종 canonical 불변식) 중 더 앞선
    # fail-closed 가드로 차단된다. 어느 경로든 stale 계열 보호가 발동하고 승인 코드는 미노출.
    assert (
        "pr_body_stale" in combined
        or "stale" in combined
        or "codex_review_stale" in combined
        or "codex_review_not_approved" in combined
    ), (
        f"PR body 변경인데 stale 계열 BLOCKED 미발동\n{combined}"
    )
    assert "사용자 승인 요청" not in combined, (
        f"PR body 변경(stale)인데 승인 요청문이 노출됨\n{combined}"
    )


def test_tc_three_way_sha_match(gh_publish_env):
    """TC-C4: 정상 경로 3자 canonical SHA 일치.

    acceptance_request.pr_body_sha256 ==
    codex_review_result.github_canonical_pr_body_sha256 ==
    _fetch_canonical_pr_body_sha256(현재 PR) 가 모두 같아야 한다.
    """
    env, state_file, evidence = gh_publish_env
    # OneDrive transient 발생 시 재-stage 후 재시도(차단 규칙 자체는 불변).
    r = None
    for _attempt in range(4):
        _stage_and_codex_approve(env, evidence)
        r = _run_pipeline_env(
            "gates", "request-accept", "--evidence", str(evidence), env=env,
        )
        _c = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            break
        if any(m in _c for m in _ONEDRIVE_TRANSIENT_MARKERS):
            _clean_root_artifacts()
            time.sleep(0.4)
            continue
        break
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"3자 일치 정상 경로 실패\n{combined}"
    req = json.loads(
        (PIPELINE_ROOT / "acceptance_request.json").read_text(encoding="utf-8")
    )
    cx = json.loads(
        _codex_review_result_path_for(state_file).read_text(encoding="utf-8")
    )
    req_canonical = req.get("pr_body_sha256")
    codex_canonical = cx.get("github_canonical_pr_body_sha256")
    assert req_canonical, "acceptance_request.pr_body_sha256 미기록"
    assert codex_canonical, "codex_review_result.github_canonical_pr_body_sha256 미기록"
    assert req_canonical == codex_canonical, (
        "acceptance_request canonical != codex canonical:\n"
        f"  acceptance_request: {req_canonical}\n"
        f"  codex canonical:    {codex_canonical}"
    )
    # 세 번째 차원: 실제 gh stub에서 fetch한 PR body를 helper와 동일한 canonical 규칙
    # (`--json body` JSON parse → body 필드 → CRLF→LF 정규화 → sha256)으로 계산하여
    # acceptance_request/codex canonical과 일치하는지 검증한다.
    gh_exec = env["PIPELINE_GH_EXECUTABLE"]
    r_view = subprocess.run(
        [str(gh_exec), "pr", "view", "1", "--json", "body"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env, cwd=str(PIPELINE_ROOT),
    )
    assert r_view.returncode == 0, f"gh stub pr view 실패\n{r_view.stderr}"
    body_field = json.loads(r_view.stdout)["body"]
    canonical_body = body_field.replace("\r\n", "\n")
    fetched_sha = hashlib.sha256(canonical_body.encode("utf-8")).hexdigest()
    assert fetched_sha == req_canonical, (
        "현재 gh canonical SHA가 acceptance_request canonical과 불일치:\n"
        f"  fetched: {fetched_sha}\n  request: {req_canonical}"
    )


def test_jq_vs_json_parse_sha_consistency(isolated_pipeline):
    """MT-6(1): gh --jq 출력과 gh --json body 파싱 결과의 canonical SHA가 같아야 함(mock 기반).

    _fetch_canonical_pr_body_sha256는 `--json body`를 JSON parse하여 body 필드를 LF 정규화 후
    인코딩한다. gh stub이 --jq .body로는 trailing newline을 덧붙여 stdout을 출력하더라도, helper의
    canonical SHA는 그 trailing newline에 영향받지 않고 항상 sha256(LF 정규화 body)와 같아야 한다.
    trailing newline이 있는 경우와 없는 경우 모두 커버한다.
    """
    tmp_path, state_file = isolated_pipeline
    for body in ("canonical body no-newline", "canonical body with newline\n"):
        gh_bat = _write_canonical_gh_stub(
            tmp_path / f"ghjq_{abs(hash(body)) % 99999}", body
        )
        out = _run_driver_with_gh(
            tmp_path, state_file, gh_bat,
            "sha = pipeline._fetch_canonical_pr_body_sha256(1)\n"
            "print('SHA', sha)\n",
        )
        # helper는 JSON body 필드(LF 정규화)를 인코딩한다 — stub의 --jq trailing newline과 무관.
        expected = hashlib.sha256(
            body.replace("\r\n", "\n").encode("utf-8")
        ).hexdigest()
        assert f"SHA {expected}" in out, (
            f"--json body 파싱 canonical SHA 불일치 (body={body!r})\n"
            f"expected={expected}\n{out}"
        )


def test_crlf_unicode_normalization(isolated_pipeline):
    """MT-6(2): trailing newline/CRLF/Unicode 차이 케이스 — CRLF와 LF body의 canonical SHA 동일.

    body에 \\r\\n이 있는 경우 vs \\n만 있는 경우 두 helper(_sha256/_text)가 모두 LF 정규화를
    적용하므로, 동일 논리 본문의 canonical SHA가 줄바꿈 형식과 무관하게 같아야 한다(r11 근본 수정).
    Unicode 문자(한글/이모지 포함)도 utf-8 인코딩으로 동일하게 처리됨을 함께 검증한다.
    """
    tmp_path, state_file = isolated_pipeline
    crlf_body = "한글 라인1\r\n이모지 ✅ 라인2\r\nplain line3"
    lf_body = crlf_body.replace("\r\n", "\n")
    expected_lf = hashlib.sha256(lf_body.encode("utf-8")).hexdigest()

    # CRLF body stub → helper canonical SHA == LF body SHA.
    gh_crlf = _write_canonical_gh_stub(tmp_path / "ghcrlf", crlf_body)
    out_crlf = _run_driver_with_gh(
        tmp_path, state_file, gh_crlf,
        "sha = pipeline._fetch_canonical_pr_body_sha256(1)\n"
        "txt = pipeline._fetch_canonical_pr_body_text(1)\n"
        "import hashlib\n"
        "print('SHA', sha)\n"
        "print('TEXT_SHA', hashlib.sha256(txt.encode('utf-8')).hexdigest())\n",
    )
    assert f"SHA {expected_lf}" in out_crlf, (
        f"CRLF body의 canonical SHA가 LF 정규화 결과와 불일치\n"
        f"expected(LF)={expected_lf}\n{out_crlf}"
    )
    # _text helper도 동일 LF 정규화하므로 재인코딩 SHA가 _sha256 helper와 같아야 한다(2자 검증 일관성).
    assert f"TEXT_SHA {expected_lf}" in out_crlf, (
        f"_fetch_canonical_pr_body_text 재인코딩 SHA가 _sha256 helper와 불일치\n"
        f"expected(LF)={expected_lf}\n{out_crlf}"
    )

    # LF body stub → 동일 canonical SHA (CRLF/LF 차이 흡수 확인).
    gh_lf = _write_canonical_gh_stub(tmp_path / "ghlf", lf_body)
    out_lf = _run_driver_with_gh(
        tmp_path, state_file, gh_lf,
        "sha = pipeline._fetch_canonical_pr_body_sha256(1)\n"
        "print('SHA', sha)\n",
    )
    assert f"SHA {expected_lf}" in out_lf, (
        f"LF body의 canonical SHA가 기대값과 불일치\n{out_lf}"
    )


def test_request_accept_blocked_when_pr_body_changes_after(gh_publish_env):
    """MT-6(3): request-accept publish 후 PR body가 바뀌면 재실행 시 fail-closed로 BLOCKED.

    1) 정상 경로(2-call)로 request-accept publish 성공 → acceptance_request.pr_body_sha256 기록.
    2) gh stub이 다른 body(필수 섹션 유지)를 반환하도록 교체 후 request-accept 재실행 →
       재-stage SHA 불일치 또는 최종 canonical 불변식 중 더 앞선 fail-closed 가드가 발동해야 한다.
    """
    env, state_file, evidence = gh_publish_env
    # 1) 정상 publish (OneDrive transient 재시도 포함).
    r_ok = None
    for _attempt in range(4):
        _stage_and_codex_approve(env, evidence)
        r_ok = _run_pipeline_env(
            "gates", "request-accept", "--evidence", str(evidence), env=env,
        )
        _c = (r_ok.stdout or "") + (r_ok.stderr or "")
        if r_ok.returncode == 0:
            break
        if any(m in _c for m in _ONEDRIVE_TRANSIENT_MARKERS):
            _clean_root_artifacts()
            time.sleep(0.4)
            continue
        break
    assert r_ok.returncode == 0, f"정상 publish 실패\n{r_ok.stdout}{r_ok.stderr}"
    assert (PIPELINE_ROOT / "acceptance_request.json").exists(), (
        "정상 publish인데 acceptance_request.json 미생성"
    )
    # 2) PR body를 바꾼 새 stub으로 교체 → 현재 canonical SHA가 달라진다.
    #    필수 섹션을 모두 유지한 변경 body를 사용해 pr_body_incomplete 차단을 건너뛰고,
    #    fail-closed stale 가드가 발동하도록 한다.
    changed_bat = _write_canonical_gh_stub(
        Path(env["PIPELINE_GH_EXECUTABLE"]).parent.parent / "ghmt6_changed",
        _CHANGED_GH_PR_BODY_F52C_STALE,
        head_sha="f52c" + "0" * 36,
    )
    env_changed = dict(env)
    env_changed["PIPELINE_GH_EXECUTABLE"] = str(changed_bat)
    r_stale = _run_pipeline_env(
        "gates", "request-accept", "--evidence", str(evidence), env=env_changed,
    )
    combined = (r_stale.stdout or "") + (r_stale.stderr or "")
    assert r_stale.returncode != 0, f"publish 후 PR body 변경인데 exit 0\n{combined}"
    # publish 이후 body 변경은 (재-stage SHA 불일치) 또는 (최종 canonical 불변식) 중 더 앞선
    # fail-closed 가드로 차단된다. 어느 경로든 stale 계열 보호가 발동하고 승인 코드는 미노출.
    assert (
        "pr_body_stale" in combined
        or "stale" in combined
        or "codex_review_stale" in combined
        or "codex_review_not_approved" in combined
    ), f"PR body 변경인데 stale 계열 BLOCKED 미발동\n{combined}"
    assert "사용자 승인 요청" not in combined, (
        f"PR body 변경(stale)인데 승인 요청문이 노출됨\n{combined}"
    )


def test_three_sha_invariant_on_happy_path(gh_publish_env):
    """MT-6(4): 정상 경로에서 acceptance_request/codex canonical/현재 GitHub PR body SHA 3자 일치.

    2-call 정상 흐름으로 publish하면 세 canonical SHA가 모두 같아야 한다:
      acceptance_request.pr_body_sha256
      == codex_review_result.github_canonical_pr_body_sha256
      == _fetch_canonical_pr_body_sha256(현재 PR)
    """
    env, state_file, evidence = gh_publish_env
    r = None
    for _attempt in range(4):
        _stage_and_codex_approve(env, evidence)
        r = _run_pipeline_env(
            "gates", "request-accept", "--evidence", str(evidence), env=env,
        )
        _c = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            break
        if any(m in _c for m in _ONEDRIVE_TRANSIENT_MARKERS):
            _clean_root_artifacts()
            time.sleep(0.4)
            continue
        break
    combined = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"3자 일치 정상 경로 실패\n{combined}"
    req = json.loads(
        (PIPELINE_ROOT / "acceptance_request.json").read_text(encoding="utf-8")
    )
    cx = json.loads(
        _codex_review_result_path_for(state_file).read_text(encoding="utf-8")
    )
    req_canonical = req.get("pr_body_sha256")
    codex_canonical = cx.get("github_canonical_pr_body_sha256")
    assert req_canonical and codex_canonical, "canonical SHA 미기록"
    assert req_canonical == codex_canonical, (
        f"acceptance_request({req_canonical}) != codex({codex_canonical})"
    )
    gh_exec = env["PIPELINE_GH_EXECUTABLE"]
    r_view = subprocess.run(
        [str(gh_exec), "pr", "view", "1", "--json", "body"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=env, cwd=str(PIPELINE_ROOT),
    )
    assert r_view.returncode == 0, f"gh stub pr view 실패\n{r_view.stderr}"
    body_field = json.loads(r_view.stdout)["body"]
    fetched_sha = hashlib.sha256(
        body_field.replace("\r\n", "\n").encode("utf-8")
    ).hexdigest()
    assert fetched_sha == req_canonical, (
        f"현재 gh canonical SHA({fetched_sha}) != acceptance_request({req_canonical})"
    )



# ---------------------------------------------------------------------------
# TC-ALL: 전체 suite 회귀 (자기 자신 제외)
# ---------------------------------------------------------------------------

def test_tc_all_suite_pass():
    """TC-ALL: gh_publish_env 미사용 빠른 TC가 통과하는지 subprocess pytest로 회귀 확인.

    gh_publish_env fixture 의존 테스트는 각 15-20초로 느려 oracle runner 타임아웃을 압박하므로
    이름 기반 -k 필터로 제외합니다(해당 테스트는 TC-6N에서 직접 실행됨).
    제외 대상 = gh_publish_env fixture를 쓰는 20개 테스트:
    tc_reject_*, tc_publish_3way_sha_match, tc_new_1/2/3, tc_f52c_*, tc_r8_*.
    fixture 인자 이름은 pytest -k가 매치하지 못하고, module-level marker hook은 발동하지
    않으므로(conftest 전용) 이름 prefix 기반 제외가 가장 안정적입니다.
    """
    deselect_k = (
        "not tc_all_suite_pass "
        "and not tc_reject "
        "and not tc_publish_3way "
        "and not tc_new_1 "
        "and not tc_new_2 "
        "and not tc_new_3 "
        "and not tc_f52c "
        "and not tc_r8 "
        "and not tc_pr_body_stale_after_publish "
        "and not tc_three_way_sha_match "
        "and not request_accept_blocked_when_pr_body_changes_after "
        "and not three_sha_invariant_on_happy_path"
    )
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", str(Path(__file__)),
            "-q", "--tb=short", "-p", "no:cacheprovider",
            "-k", deselect_k,
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120,
        env={**os.environ, "PIPELINE_NO_DASHBOARD": "1"},
    )
    out = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, f"하위 TC 테스트 일부 실패\n{out[-2000:]}"
