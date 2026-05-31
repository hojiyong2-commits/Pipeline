"""IMP-20260531-AEF0: gates request-accept nonce 재사용 E2E 테스트.

TC-R1 ~ TC-R6 — PIPELINE_STATE_PATH 격리 + final_state assertion 필수.

아래 6개 시나리오를 테스트합니다:
  TC-R1 (normal): 동일 조건 재실행 → 기존 nonce 재사용
  TC-R2 (edge):   --force-new-code → 새 nonce 강제 발급
  TC-R3 (edge):   evidence SHA-256 변경 → 새 nonce 발급
  TC-R4 (edge):   PR head SHA 변경 → 새 nonce 발급
  TC-R5 (edge):   CI run ID 변경 → 새 nonce 발급
  TC-R6 (edge):   기존 코드 status=CONSUMED → 새 nonce 발급

격리 전략:
  - PIPELINE_STATE_PATH 환경 변수로 state 파일을 tmp_path 안에 격리
  - subprocess cwd=tmp_path로 실행하여 acceptance_request.json(상대경로)도 격리
  - 전역 pipeline_state.json 및 acceptance_request.json을 수정하지 않음
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# ─── 헬퍼 ──────────────────────────────────────────────────────────────────────

PIPELINE_PY = str(Path(__file__).resolve().parents[2] / "pipeline.py")

DUMMY_PIPELINE_ID = "IMP-20260531-AEF0"
DUMMY_NONCE = "AAAABBBB"
DUMMY_REQUEST_ID = "aef0test"


def _sha256_of(text: str) -> str:
    """텍스트의 SHA-256 hex digest."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_pipeline(
    args: list,
    state_path: Path,
    cwd: Path,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """PIPELINE_STATE_PATH 격리 + cwd 격리 환경에서 pipeline.py 실행.

    acceptance_request.json은 상대 경로("acceptance_request.json")로 저장되므로
    cwd를 tmp_path로 설정하여 격리합니다.
    """
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    # Windows cp949 인코딩 문제 방지: PYTHONIOENCODING=utf-8 강제 설정
    env["PYTHONIOENCODING"] = "utf-8"
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


def _write_state(state_path: Path, pipeline_id: str) -> None:
    """최소 pipeline_state.json 작성 (request-accept 실행에 필요한 필드만).

    event_log 필드를 포함하여 _log_event() KeyError를 방지합니다.
    """
    state: Dict[str, Any] = {
        "schema_version": 2,
        "pipeline_id": pipeline_id,
        "current_phase": "Phase 2 - Dev (Implementation)",
        "phases": {
            "pm": {"status": "DONE"},
            "dev": {"status": "PENDING"},
        },
        "external_gates": {
            "technical": {"status": "PENDING"},
            "oracle": {"status": "PENDING"},
            "acceptance": {"status": "PENDING"},
            "github_ci": {"status": "PENDING"},
        },
        "events": [],
        "event_log": [],
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_acceptance_request(
    req_path: Path,
    *,
    pipeline_id: str = DUMMY_PIPELINE_ID,
    nonce: str = DUMMY_NONCE,
    evidence: str,
    evidence_sha256: Optional[str],
    pr_head_sha: str = "abc1234",
    ci_run_id: str = "99999999",
    status: str = "PENDING",
) -> None:
    """acceptance_request.json 초기 상태를 직접 기록 (기존 코드가 있는 상황 시뮬레이션)."""
    data: Dict[str, Any] = {
        "schema_version": 1,
        "pipeline_id": pipeline_id,
        "request_id": DUMMY_REQUEST_ID,
        "nonce": nonce,
        "created_at": "2026-05-31T10:00:00Z",
        "pr_url": "",
        "pr_head_sha": pr_head_sha,
        "github_ci_run_id": ci_run_id,
        "evidence": evidence,
        "evidence_sha256": evidence_sha256,
        "evidence_url": None,
        "status": status,
    }
    req_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_acceptance_request(req_path: Path) -> Dict[str, Any]:
    """acceptance_request.json 읽기."""
    return json.loads(req_path.read_text(encoding="utf-8"))


def _write_evidence_file(path: Path, content: str = "test evidence content") -> str:
    """더미 evidence 파일 작성 후 SHA-256 반환."""
    path.write_text(content, encoding="utf-8")
    return _sha256_of(content)


# ─── TC-R1: 동일 조건 재실행 → nonce 재사용 ────────────────────────────────────────


def test_nonce_reused_on_same_conditions(tmp_path: Path) -> None:
    """TC-R1 (normal): 5-field 조건이 모두 같으면 기존 nonce를 재사용한다.

    Oracle: tests/oracles/IMP-20260531-AEF0/normal_nonce_reuse/expected.json
      nonce_reused=true, reason_contains="모두 같습니다"
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "evidence.txt"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_sha256 = _write_evidence_file(evidence_path)
    # gh CLI 없는 환경에서 pr_head_sha=""  ci_run_id="" 반환되므로 이에 맞춤
    _write_acceptance_request(
        req_path,
        evidence=str(evidence_path),
        evidence_sha256=evidence_sha256,
        pr_head_sha="",
        ci_run_id="",
        status="PENDING",
    )

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
    )

    # 프로세스가 정상 종료되어야 함
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    # stdout에 재사용 안내 메시지 포함
    assert "모두 같습니다" in result.stdout, (
        f"재사용 이유 메시지 누락\nstdout={result.stdout}"
    )
    assert "재사용" in result.stdout, (
        f"'재사용' 키워드 누락\nstdout={result.stdout}"
    )

    # final_state: acceptance_request.json의 nonce가 원래 값과 동일해야 함
    final_req = _read_acceptance_request(req_path)
    assert final_req["nonce"] == DUMMY_NONCE, (
        f"nonce가 변경됨 (재사용 실패): 기대={DUMMY_NONCE}, 실제={final_req['nonce']}"
    )
    assert final_req["status"] == "PENDING", (
        f"status가 변경됨: {final_req['status']}"
    )


# ─── TC-R2: --force-new-code → 새 nonce 강제 발급 ─────────────────────────────────


def test_force_new_code_always_new_nonce(tmp_path: Path) -> None:
    """TC-R2 (edge): --force-new-code 플래그가 있으면 조건과 무관하게 새 nonce를 발급한다.

    Oracle: tests/oracles/IMP-20260531-AEF0/edge_force_new_code/expected.json
      nonce_reused=false, reason_contains="--force-new-code 옵션이 지정되어"
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "evidence.txt"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_sha256 = _write_evidence_file(evidence_path)
    _write_acceptance_request(
        req_path,
        evidence=str(evidence_path),
        evidence_sha256=evidence_sha256,
        pr_head_sha="",
        ci_run_id="",
        status="PENDING",
    )

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path), "--force-new-code"],
        state_path,
        cwd=tmp_path,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    assert "--force-new-code 옵션이 지정되어" in result.stdout, (
        f"force-new-code 안내 메시지 누락\nstdout={result.stdout}"
    )

    # final_state: nonce가 새로 발급되어야 함
    final_req = _read_acceptance_request(req_path)
    assert final_req["nonce"] != DUMMY_NONCE, (
        f"nonce가 재사용됨 (force-new-code 실패): {final_req['nonce']}"
    )


# ─── TC-R3: evidence SHA-256 변경 → 새 nonce ──────────────────────────────────────


def test_new_nonce_when_evidence_sha_changed(tmp_path: Path) -> None:
    """TC-R3 (edge): evidence 파일 내용이 바뀌면(SHA-256 변경) 새 nonce를 발급한다.

    Oracle: tests/oracles/IMP-20260531-AEF0/edge_evidence_sha_changed/expected.json
      nonce_reused=false, reason_contains="결과물 파일 내용이 달라서"
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "evidence.txt"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    # 기존 요청: SHA-256은 구 내용 기준
    old_sha = _sha256_of("old evidence content")
    _write_acceptance_request(
        req_path,
        evidence=str(evidence_path),
        evidence_sha256=old_sha,
        pr_head_sha="",
        ci_run_id="",
        status="PENDING",
    )
    # 현재 파일: 내용이 달라져 SHA-256이 다름
    _write_evidence_file(evidence_path, content="new different evidence content")

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "결과물 파일 내용이 달라서" in result.stdout, (
        f"SHA 변경 안내 메시지 누락\nstdout={result.stdout}"
    )

    # final_state: 새 nonce 발급
    final_req = _read_acceptance_request(req_path)
    assert final_req["nonce"] != DUMMY_NONCE, (
        f"nonce가 재사용됨 (SHA 변경 미감지): {final_req['nonce']}"
    )


# ─── TC-R4: PR head SHA 변경 → 새 nonce ────────────────────────────────────────────


def test_new_nonce_when_pr_sha_changed(tmp_path: Path) -> None:
    """TC-R4 (edge): PR head SHA가 달라지면(새 커밋 push) 새 nonce를 발급한다.

    Oracle: tests/oracles/IMP-20260531-AEF0/edge_pr_sha_changed/expected.json
      nonce_reused=false, reason_contains="PR head SHA가 달라서"

    gh CLI 없는 환경에서는 pr_head_sha=""를 반환하므로,
    기존 요청의 pr_head_sha를 "old_sha_value"(비어 있지 않음)로 설정하여 불일치를 유발합니다.
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "evidence.txt"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_sha256 = _write_evidence_file(evidence_path)
    # 기존 요청: pr_head_sha="old_sha_value" → CLI 없는 환경에서 new는 ""이 되어 불일치
    _write_acceptance_request(
        req_path,
        evidence=str(evidence_path),
        evidence_sha256=evidence_sha256,
        pr_head_sha="old_sha_value",
        ci_run_id="",
        status="PENDING",
    )

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "PR head SHA가 달라서" in result.stdout, (
        f"PR SHA 변경 안내 메시지 누락\nstdout={result.stdout}"
    )

    # final_state: 새 nonce 발급
    final_req = _read_acceptance_request(req_path)
    assert final_req["nonce"] != DUMMY_NONCE, (
        f"nonce가 재사용됨 (PR SHA 변경 미감지): {final_req['nonce']}"
    )


# ─── TC-R5: CI run ID 변경 → 새 nonce ─────────────────────────────────────────────


def test_new_nonce_when_ci_run_changed(tmp_path: Path) -> None:
    """TC-R5 (edge): GitHub Actions run ID가 달라지면 새 nonce를 발급한다.

    Oracle: tests/oracles/IMP-20260531-AEF0/edge_ci_run_changed/expected.json
      nonce_reused=false, reason_contains="GitHub Actions run ID가 달라서"

    gh CLI 없는 환경: ci_run_id=""가 반환되므로,
    기존 요청의 ci_run_id를 "99999999"(비어 있지 않음)로 설정하여 불일치를 유발합니다.
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "evidence.txt"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_sha256 = _write_evidence_file(evidence_path)
    # 기존 요청: ci_run_id="99999999", 현재 환경: gh CLI 없어 "" 반환 → 불일치
    _write_acceptance_request(
        req_path,
        evidence=str(evidence_path),
        evidence_sha256=evidence_sha256,
        pr_head_sha="",
        ci_run_id="99999999",
        status="PENDING",
    )

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "GitHub Actions run ID가 달라서" in result.stdout, (
        f"CI run ID 변경 안내 메시지 누락\nstdout={result.stdout}"
    )

    # final_state: 새 nonce 발급
    final_req = _read_acceptance_request(req_path)
    assert final_req["nonce"] != DUMMY_NONCE, (
        f"nonce가 재사용됨 (CI run ID 변경 미감지): {final_req['nonce']}"
    )


# ─── TC-R6: status=CONSUMED → 새 nonce ────────────────────────────────────────────


def test_new_nonce_when_status_not_pending(tmp_path: Path) -> None:
    """TC-R6 (edge): 기존 코드 status=CONSUMED면 새 nonce를 발급한다.

    Oracle: tests/oracles/IMP-20260531-AEF0/edge_status_not_pending/expected.json
      nonce_reused=false, reason_contains="새 코드를 발급합니다"
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "evidence.txt"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_sha256 = _write_evidence_file(evidence_path)
    _write_acceptance_request(
        req_path,
        evidence=str(evidence_path),
        evidence_sha256=evidence_sha256,
        pr_head_sha="",
        ci_run_id="",
        status="CONSUMED",  # 이미 소비된 상태
    )

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "새 코드를 발급합니다" in result.stdout, (
        f"새 코드 발급 안내 메시지 누락\nstdout={result.stdout}"
    )

    # final_state: 새 nonce 발급 (CONSUMED 상태였으므로 새 파일이 써져야 함)
    final_req = _read_acceptance_request(req_path)
    assert final_req["nonce"] != DUMMY_NONCE, (
        f"nonce가 재사용됨 (CONSUMED 상태 미감지): {final_req['nonce']}"
    )
    assert final_req["status"] == "PENDING", (
        f"새 요청의 status가 PENDING이 아님: {final_req['status']}"
    )
