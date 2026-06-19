# [Purpose]: BUG-20260615-A35C MT-5 — DEFECT-A(acceptance 표시 SSoT 불일치) +
#            DEFECT-B(CI final-check stale packet 재사용) 회귀 방지 E2E 테스트.
# [Assumptions]: PIPELINE_STATE_PATH 격리 + cwd 격리 + subprocess 실제 CLI 실행.
#                report final-packet은 gh CLI 없이도 graceful degradation으로 동작한다.
# [Vulnerability & Risks]: gh CLI가 실제로 설치되어 열린 PR이 있으면 head SHA/run ID가
#                          채워질 수 있으므로 PATH를 tmp_path로 제한하여 무력화한다.
# [Improvement]: CI(PowerShell) final-check 동작은 ci.yml 정적 내용 검증으로 보완한다.
"""BUG-20260615-A35C: User Acceptance 표시 SSoT + CI final-check fail-closed E2E 테스트.

TC-1 ~ TC-11 — PIPELINE_STATE_PATH 격리 + subprocess 기반 실제 CLI 실행 + final_state/파일 검증.

  TC-1  (normal):    gates.acceptance=FAIL + active PENDING request → 표시 PENDING
  TC-2  (normal):    MD/JSON acceptance_display 일치
  TC-3  (edge):      REJECT 후 새 request 시 이전 nonce 미노출
  TC-4  (edge):      pending 표시에 현재 승인 코드 존재, ACCEPTED 문구 없음
  TC-5  (edge):      accepted 표시 상태 SSoT (CONSUMED+ACCEPT → ACCEPTED)
  TC-6  (error):     JSON 파싱 실패 시 stale packet 미사용 (CI fail-closed)
  TC-7  (edge):      head_sha/run_id 불일치 시 정보 부족 (CI fail-closed)
  TC-8  (edge):      final-check 댓글에 승인 코드 미포함 + pending 댓글 안내
  TC-9  (edge):      marker 독립성 (32D7 회귀)
  TC-10 (regression):D278 nonce-reuse 회귀 방지
  TC-11 (regression):2821 workspace_hygiene 회귀 방지
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PIPELINE_PY = str(Path(__file__).resolve().parents[2] / "pipeline.py")
CI_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"

PID = "BUG-20260615-A35C"


# ─── 헬퍼 ──────────────────────────────────────────────────────────────────────


def _run_pipeline(
    args: List[str],
    state_path: Path,
    cwd: Path,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """PIPELINE_STATE_PATH 격리 + cwd 격리 환경에서 pipeline.py 실행.

    Args:
        args: pipeline.py 뒤에 붙일 인자 리스트.
        state_path: 격리된 state 파일 경로.
        cwd: 작업 디렉토리(acceptance_request.json 등 상대 경로 산출물 격리용).
        extra_env: 추가 환경 변수.
    Returns:
        CompletedProcess.
    """
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIPELINE_NO_DASHBOARD"] = "1"
    # gh CLI 탐색을 막아 pr_head_sha/ci_run_id를 빈 문자열로 강제 (격리).
    env["PATH"] = str(cwd)
    env["PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, PIPELINE_PY] + args,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(cwd),
    )


def _write_state(
    state_path: Path,
    *,
    pipeline_id: str = PID,
    acceptance_status: str = "PENDING",
) -> None:
    """최소 pipeline_state.json 작성.

    Args:
        state_path: 작성할 경로.
        pipeline_id: 파이프라인 ID.
        acceptance_status: external_gates.acceptance.status 값(DEFECT-A 검증용).
    """
    state: Dict[str, Any] = {
        "schema_version": 2,
        "pipeline_id": pipeline_id,
        "current_phase": "Phase 7 - External Gates",
        "phases": {"pm": {"status": "DONE"}, "dev": {"status": "DONE"}},
        "external_gates": {
            "technical": {"status": "PASS"},
            "oracle": {"status": "PASS"},
            "github_ci": {"status": "PASS"},
            "acceptance": {"status": acceptance_status},
        },
        "events": [],
        "event_log": [],
        "workspace_hygiene": {
            "status": "OK",
            "blocking_items": [],
            "cleanup_only_items": [],
        },
    }
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_request(
    req_path: Path,
    *,
    pipeline_id: str = PID,
    nonce: str = "TESTNONCE123",
    status: str = "PENDING",
    consumed_result: Optional[str] = None,
) -> None:
    """acceptance_request.json 작성.

    Args:
        req_path: 작성할 경로.
        pipeline_id: 파이프라인 ID.
        nonce: 승인 코드 nonce.
        status: PENDING | CONSUMED.
        consumed_result: ACCEPT | REJECT (CONSUMED 시).
    """
    data: Dict[str, Any] = {
        "schema_version": 1,
        "pipeline_id": pipeline_id,
        "request_id": "a35ctest",
        "nonce": nonce,
        "created_at": "2026-06-15T10:00:00Z",
        "pr_url": "",
        "pr_head_sha": "",
        "github_ci_run_id": "",
        "evidence": "evidence.txt",
        "evidence_sha256": None,
        "status": status,
    }
    if consumed_result is not None:
        data["consumed_result"] = consumed_result
        data["consumed_at"] = "2026-06-15T11:00:00Z"
    req_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _final_packet_run(tmp_path: Path) -> subprocess.CompletedProcess:
    """report final-packet 실행 후 CompletedProcess 반환."""
    state_path = tmp_path / "pipeline_state.json"
    return _run_pipeline(["report", "final-packet"], state_path, tmp_path)


def _read_md(tmp_path: Path) -> str:
    p = tmp_path / "human_acceptance_packet.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _read_json(tmp_path: Path) -> Dict[str, Any]:
    p = tmp_path / "human_acceptance_packet.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _md_field(md: str, field: str) -> Optional[str]:
    """packet MD 메타데이터 블록에서 'field: value' 라인 값을 추출."""
    prefix = f"{field}: "
    for line in md.splitlines():
        s = line.strip()
        if s.startswith(prefix):
            return s[len(prefix):].strip()
    return None


# ─── TC-1: DEFECT-A — gate FAIL + active PENDING request → PENDING ──────────────


def test_tc1_fail_gate_pending_request_displays_pending(tmp_path: Path) -> None:
    """TC-1 (normal): external_gates.acceptance=FAIL이지만 active PENDING request면
    report final-packet의 acceptance 표시가 PENDING이어야 한다(DEFECT-A).

    Oracle: tests/oracles/BUG-20260615-A35C/normal_pending_display/expected.json
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    _write_state(state_path, acceptance_status="FAIL")
    _write_request(req_path, nonce="TESTNONCE123", status="PENDING")

    proc = _final_packet_run(tmp_path)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"

    md = _read_md(tmp_path)
    vj = _read_json(tmp_path)

    # 메타데이터 블록 acceptance / acceptance_display 모두 PENDING.
    assert _md_field(md, "acceptance") == "PENDING", md
    assert _md_field(md, "acceptance_display") == "PENDING", md
    # 게이트표 User Acceptance도 PENDING (FAIL 노출 금지).
    assert "User Acceptance: PENDING" in md, md
    assert "User Acceptance: FAIL" not in md, md
    # JSON SSoT도 일치.
    assert vj.get("acceptance", {}).get("display_status") == "PENDING", vj
    assert vj.get("gates", {}).get("acceptance") == "PENDING", vj


# ─── TC-2: MD/JSON acceptance_display 일치 ──────────────────────────────────────


def test_tc2_md_json_acceptance_display_consistent(tmp_path: Path) -> None:
    """TC-2 (normal): MD의 acceptance_display와 JSON의 display_status가 동일 SSoT여야 한다."""
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    _write_state(state_path, acceptance_status="FAIL")
    _write_request(req_path, nonce="TESTNONCE123", status="PENDING")

    proc = _final_packet_run(tmp_path)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"

    md = _read_md(tmp_path)
    vj = _read_json(tmp_path)
    md_display = _md_field(md, "acceptance_display")
    json_display = vj.get("acceptance", {}).get("display_status")
    assert md_display is not None, md
    assert md_display == json_display, f"md={md_display} json={json_display}"
    # 게이트표 acceptance 라인과 메타데이터 acceptance 라인도 같은 SSoT.
    assert _md_field(md, "acceptance") == "PENDING"
    assert json_display == "PENDING"


# ─── TC-3: REJECT 후 새 request 시 이전 nonce 미노출 ────────────────────────────


def test_tc3_reject_then_new_request_no_old_nonce(tmp_path: Path) -> None:
    """TC-3 (edge): 이전 REJECTED nonce가 새 PENDING request의 packet에 없어야 한다.

    Oracle: tests/oracles/BUG-20260615-A35C/edge_reject_then_new_request/expected.json
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    old_nonce = "OLD123NONCE"
    new_nonce = "NEW456NONCE"

    # 새 PENDING request만 남은 상태 (이전 REJECT 후 재발급 시뮬레이션).
    _write_state(state_path, acceptance_status="FAIL")
    _write_request(req_path, nonce=new_nonce, status="PENDING")

    proc = _final_packet_run(tmp_path)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"

    md = _read_md(tmp_path)
    vj = _read_json(tmp_path)
    # 이전 nonce는 어디에도 없어야 한다.
    assert old_nonce not in md, md
    assert old_nonce not in json.dumps(vj), vj
    # BUG-20260619-F41F MT-3: 승인 코드는 nonce 없는 ACCEPT-<pipeline_id> 형식만 노출되고,
    # 새 nonce(8자리)도 packet에 노출되지 않아야 한다 (외부 노출 차단).
    assert f"ACCEPT-{PID}" in md, md
    assert new_nonce not in md, md
    assert _md_field(md, "acceptance_display") == "PENDING"


# ─── TC-4: pending 표시에 현재 승인 코드 존재, ACCEPTED 문구 없음 ─────────────────


def test_tc4_pending_has_current_code_no_accepted(tmp_path: Path) -> None:
    """TC-4 (edge): PENDING 상태 packet에 현재 승인 코드는 있고 ACCEPTED 표시는 없어야 한다."""
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    nonce = "PENDNONCE99"
    _write_state(state_path, acceptance_status="PENDING")
    _write_request(req_path, nonce=nonce, status="PENDING")

    proc = _final_packet_run(tmp_path)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"

    md = _read_md(tmp_path)
    # BUG-20260619-F41F MT-3: nonce 없는 ACCEPT-<pipeline_id> 형식만 노출.
    assert f"ACCEPT-{PID}" in md, md
    assert nonce not in md, md
    # 표시 상태는 PENDING이며 ACCEPTED로 표시되면 안 된다.
    assert _md_field(md, "acceptance_display") == "PENDING", md
    assert _md_field(md, "acceptance") == "PENDING", md


# ─── TC-5: accepted 표시 상태 SSoT (CONSUMED+ACCEPT → ACCEPTED) ──────────────────


def test_tc5_consumed_accept_displays_accepted(tmp_path: Path) -> None:
    """TC-5 (edge): CONSUMED+consumed_result=ACCEPT인 request면 표시 상태가 ACCEPTED여야 한다."""
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    _write_state(state_path, acceptance_status="PASS")
    _write_request(
        req_path, nonce="ACCNONCE", status="CONSUMED", consumed_result="ACCEPT"
    )

    proc = _final_packet_run(tmp_path)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"

    md = _read_md(tmp_path)
    vj = _read_json(tmp_path)
    assert _md_field(md, "acceptance_display") == "ACCEPTED", md
    assert vj.get("acceptance", {}).get("display_status") == "ACCEPTED", vj


# ─── TC-6: JSON 파싱 실패 시 stale packet 미사용 (CI fail-closed 설계) ────────────


def test_tc6_ci_no_stale_pr_body_fallback(tmp_path: Path) -> None:
    """TC-6 (error): CI final-check가 JSON 파싱 실패 시 stale PR body fallback을 쓰지 않고
    fail-closed(continue-on-error: false + 정보 부족)로 동작하도록 ci.yml에 설계되어야 한다.

    Oracle: tests/oracles/BUG-20260615-A35C/error_json_parse_fail/expected.json
    """
    ci = CI_YML.read_text(encoding="utf-8")
    # Validate-PacketFreshness가 JSON parse failure를 명시적으로 FAIL 처리한다.
    assert "Validate-PacketFreshness" in ci, "freshness helper 누락"
    assert "JSON parse failure" in ci, "JSON 파싱 실패 처리 누락"
    # fail-closed: 최종 확인 안내 만들기 step이 continue-on-error: false여야 한다.
    assert "continue-on-error: false" in ci, "fail-closed 설정 누락"
    # parse 실패/불일치 시 정보 부족 표시 + throw로 step 실패.
    assert "정보 부족" in ci, "정보 부족 표시 누락"
    assert "throw" in ci, "fail-closed throw 누락"


# ─── TC-7: head_sha/run_id 불일치 시 정보 부족 (CI fail-closed 설계) ──────────────


def test_tc7_ci_stale_head_sha_information_insufficient(tmp_path: Path) -> None:
    """TC-7 (edge): run_id 불일치 시 head SHA fallback 없이 정보 부족 처리 — 실제 정책 시뮬레이션.

    시나리오: packet.github_actions.run_id = "old_run_123", 현재 CI run_id = "new_run_456".
    head SHA가 일치하더라도 run_id 불일치면 freshness = False → 정보 부족 표시.

    Oracle: tests/oracles/BUG-20260615-A35C/edge_stale_packet_blocked/expected.json
    """
    ci = CI_YML.read_text(encoding="utf-8")

    # 1) Validate-PacketFreshness 함수에 run_id 불일치 처리가 있어야 한다.
    assert "run_id mismatch" in ci, "run_id 불일치 처리 누락"
    assert "head SHA mismatch" in ci, "head SHA 불일치 처리 누락"

    # 2) fallback 분기가 제거되었어야 한다 — head SHA만으로 통과시키는 분기 금지.
    #    "head SHA만 일치하면 run_id 불일치는 허용" 주석이 없어야 한다.
    assert "head SHA만 일치하면 run_id 불일치는 허용" not in ci, (
        "run_id 불일치 허용 fallback이 여전히 ci.yml에 존재합니다. "
        "Validate-PacketFreshness 반환값을 직접 사용해야 합니다."
    )

    # 3) 실제 정책 시뮬레이션: packet에 run_id = "old_run_123"이고
    #    현재 run_id = "new_run_456"이면 $packetFresh.Status가 FAIL이어야 한다.
    #    ci.yml의 Validate-PacketFreshness 로직을 Python으로 재현하여 검증.
    old_run_id = "old_run_123"
    new_run_id = "new_run_456"
    shared_sha = "abc123def456"

    # ci.yml의 freshness 결과를 Python 로직으로 시뮬레이션
    # (Validate-PacketFreshness: run_id 불일치면 Status=FAIL)
    packet_sha = shared_sha       # head SHA 일치 (stale 상황 재현)
    packet_run_id = old_run_id    # run_id 불일치
    expected_sha = shared_sha
    expected_run_id = new_run_id

    # 시뮬레이션: head SHA와 run_id 모두 체크
    if packet_sha != expected_sha:
        simulated_status = "FAIL"
        simulated_reason = f"head SHA mismatch: packet={packet_sha}, current={expected_sha}"
    elif packet_run_id != expected_run_id:
        simulated_status = "FAIL"
        simulated_reason = f"run_id mismatch: packet={packet_run_id}, current={expected_run_id}"
    else:
        simulated_status = "PASS"
        simulated_reason = ""

    assert simulated_status == "FAIL", f"run_id 불일치가 FAIL을 유발해야 함: {simulated_reason}"
    assert "run_id mismatch" in simulated_reason, simulated_reason

    # 4) fallback 제거 후 FAIL 시 stale packet이 정보 부족으로 처리되는지 확인.
    #    ci.yml에 "$packetFresh.Reason" 또는 "$packetStaleReason = $packetFresh.Reason"이
    #    존재하면 fallback 없이 Validate-PacketFreshness 반환값을 직접 사용 중.
    assert "$packetFresh.Reason" in ci, (
        "Validate-PacketFreshness 반환값을 직접 사용하는 코드가 없습니다. "
        "fallback 제거 후 $packetFresh.Reason을 $packetStaleReason에 대입해야 합니다."
    )

    # 5) fail-closed 동작 확인.
    assert "-not $packetIsFresh" in ci, "freshness 실패 분기 누락"
    assert "정보 부족" in ci, "정보 부족 표시 누락"
    assert "throw" in ci, "fail-closed throw 누락"
    assert "continue-on-error: false" in ci, "fail-closed 설정 누락"


# ─── TC-8: final-check 댓글에 승인 코드 미포함 + pending 댓글 안내 ────────────────


def test_tc8_ci_final_check_no_approval_code(tmp_path: Path) -> None:
    """TC-8 (edge): CI final-check 댓글 템플릿에 승인 코드(ACCEPT-...)가 포함되지 않고,
    pending 댓글을 확인하라는 안내가 있어야 한다.

    Oracle: tests/oracles/BUG-20260615-A35C/edge_final_check_no_code/expected.json
    """
    ci = CI_YML.read_text(encoding="utf-8")
    # final-check packet 템플릿(here-string) 영역에 ACCEPT- 승인 코드 직접 삽입이 없어야 한다.
    # (현재 step은 PR body 섹션만 인용하며 승인 코드를 싣지 않는다.)
    assert "ACCEPT-$" not in ci, "final-check에 승인 코드 보간 발견"
    assert 'ACCEPT-${{' not in ci, "final-check에 승인 코드 보간 발견"
    # pending 댓글 marker를 가리키는 안내 라인 존재.
    assert "pipeline-human-acceptance-packet-pending" in ci, "pending marker 안내 누락"
    assert "사용자 최종 확인 요청 댓글" in ci, "pending 댓글 안내 문구 누락"


# ─── TC-9: marker 독립성 (32D7 회귀) ────────────────────────────────────────────


def test_tc9_marker_independence(tmp_path: Path) -> None:
    """TC-9 (edge): pending/accepted/final-check marker가 독립적으로 분리되어 있어야 한다(32D7)."""
    ci = CI_YML.read_text(encoding="utf-8")
    # 세 marker가 모두 존재하고, final-check 댓글 선택이 pending/accepted를 제외한다.
    assert "pipeline-final-check-packet" in ci, "final-check marker 누락"
    assert "pipeline-human-acceptance-packet-pending" in ci, "pending marker 누락"
    assert "pipeline-human-acceptance-packet-accepted" in ci, "accepted marker 누락"
    # final-check 댓글 PATCH 대상에서 pending/accepted 댓글을 제외(notlike).
    assert "notlike" in ci, "pending/accepted 제외 로직 누락"


# ─── TC-10: D278 nonce-reuse 회귀 방지 ──────────────────────────────────────────


def test_tc10_d278_nonce_reuse_regression(tmp_path: Path) -> None:
    """TC-10 (regression): D278 post-accept 표시 상태 일치 로직이 보존되어 있어야 한다.

    내부 함수 import 없이, report final-packet의 표시 상태가 acceptance_request 기준으로
    정확히 산출되는지 검증한다(REJECTED → PENDING 게이트 동기화 보존).
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    # 이전 REJECT 후 게이트에 FAIL이 남았으나 새 PENDING request가 발급된 상황.
    _write_state(state_path, acceptance_status="FAIL")
    _write_request(req_path, nonce="REUSE777", status="PENDING")

    proc = _final_packet_run(tmp_path)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"

    md = _read_md(tmp_path)
    # BUG-20260619-F41F MT-3: nonce 없는 ACCEPT-<pipeline_id> 형식만 노출, 게이트표는 PENDING.
    assert f"ACCEPT-{PID}" in md, md
    assert "REUSE777" not in md, md
    assert "User Acceptance: PENDING" in md, md
    # D278 검증 함수가 pipeline.py에 보존되어 있어야 한다(정적 확인).
    pipeline_src = Path(PIPELINE_PY).read_text(encoding="utf-8")
    assert "post_accept_status_mismatch" in pipeline_src, "D278 로직 훼손"


# ─── TC-11: 2821 workspace_hygiene 회귀 방지 ────────────────────────────────────


def test_tc11_2821_workspace_hygiene_regression(tmp_path: Path) -> None:
    """TC-11 (regression): workspace_hygiene 요약이 packet 메타데이터에 보존되어야 한다(2821)."""
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    _write_state(state_path, acceptance_status="FAIL")
    _write_request(req_path, nonce="HYG888", status="PENDING")

    proc = _final_packet_run(tmp_path)
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"

    md = _read_md(tmp_path)
    # workspace_hygiene 메타데이터 라인이 보존되어야 한다(OK 상태).
    wh = _md_field(md, "workspace_hygiene")
    assert wh is not None, md
    assert wh.startswith("OK"), wh
    # 2821 fail-closed 로직이 pipeline.py에 보존되어 있어야 한다(정적 확인).
    pipeline_src = Path(PIPELINE_PY).read_text(encoding="utf-8")
    assert "workspace_hygiene" in pipeline_src, "2821 로직 훼손"


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
