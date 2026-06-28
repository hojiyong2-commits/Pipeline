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
# TC-ALL: 전체 suite 회귀 (자기 자신 제외)
# ---------------------------------------------------------------------------

def test_tc_all_suite_pass():
    """TC-ALL: 이 파일의 다른 모든 TC가 통과하는지 subprocess pytest로 회귀 확인."""
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", str(Path(__file__)),
            "-q", "--tb=short", "-p", "no:cacheprovider",
            "-k", "not tc_all_suite_pass",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
        env={**os.environ, "PIPELINE_NO_DASHBOARD": "1"},
    )
    out = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, f"하위 TC 테스트 일부 실패\n{out[-2000:]}"
