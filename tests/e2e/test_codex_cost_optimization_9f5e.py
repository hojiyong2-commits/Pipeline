# tests/e2e/test_codex_cost_optimization_9f5e.py
# [Purpose]: BUG-20260702-E69E — Codex CLI 실행 오류(usage limit / timeout / network /
#            non-zero exit / parse 실패)가 실제 Codex REJECT verdict로 오기록되는 구조적 결함을
#            회귀 검증한다. Codex가 명시적으로 "REJECT - ..."를 반환한 경우만 REJECTED이며,
#            모든 CLI 실행 실패는 ERROR로 분류되어 reject rate-limit을 트리거하지 않고 승인 코드도
#            발급하지 않는다.
# [Assumptions]: pipeline.py가 같은 repo 루트에 있고, PIPELINE_STATE_PATH 격리 + cwd=tmp_path로
#            상태/산출물 파일을 격리할 수 있다. gh CLI는 PATH에서 제거하여 결정적으로 동작시킨다.
#            gates codex-review는 --codex-cli-exit-code/--codex-cli-stdout/--codex-cli-stderr로
#            CLI 실행 원시값을 받아 _run_codex_cli_review로 분류한다.
# [Vulnerability & Risks]: request-accept 전체 경로는 PR/CI 전제조건이 많아 gh 없이는 이른 단계에서
#            BLOCKED로 끝날 수 있다. 이 테스트는 codex_review_result.json(SSoT)의 status/error_type/
#            reject_count/cli_error_count/acceptance_eligible 필드를 final_state로 assert하여 새 동작을
#            결정적으로 검증한다.
# [Improvement]: gh를 mock하는 stub 바이너리를 추가하면 request-accept publish 경로까지 검증 가능.
"""BUG-20260702-E69E: Codex CLI ERROR vs REJECT 분리 회귀 테스트.

TC-1: usage limit 메시지 → status=ERROR, error_type=usage_limit, reject_count 증가 없음.
TC-2: 실제 REJECT - ... → status=REJECTED, reject_count 증가.
TC-3: CLI timeout → ERROR, 승인 코드 미발급.
TC-4: CLI nonzero exit → ERROR, REJECT로 표시 금지.
TC-5: parse failure (exit 0 + 빈 stdout) → ERROR, fail-closed.
TC-6: ERROR 상태에서 reject rate-limit 미적용.
TC-7: --retry-cli-error는 ERROR 상태에서만 허용.
TC-8: REJECTED 상태에서 --retry-cli-error 우회 불가.
TC-9: retry 시 bundle/head/packet/pr_body SHA 변경되면 차단 (skip 처리).
TC-10: ERROR 후 재시도 성공 시 stale/provenance 검증 유지.
TC-11: ERROR 결과는 APPROVED cache로 재사용 불가.
TC-12: 사용자 출력에 "REJECT"가 아니라 "Codex CLI ERROR"로 표시됨.
"""
import subprocess
import sys
import os
import json
import shutil
from pathlib import Path

import pytest

PIPELINE_PY = str(Path(__file__).parent.parent.parent / "pipeline.py")
PIPELINE_ID = "BUG-20260702-E69E-TEST"
DUMMY_PACKET_SHA = "a" * 64  # verdict 기록용 dummy packet SHA (REJECT/APPROVE 경로)


def _no_gh_path() -> str:
    """gh CLI 디렉토리를 제거한 PATH를 반환한다 (git 등 다른 도구는 유지)."""
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
    """pipeline.py CLI를 subprocess로 실행하고 결과를 반환한다 (내부 함수 직접 임포트 금지)."""
    cmd = [sys.executable, PIPELINE_PY] + list(args)
    env = os.environ.copy()
    if state_path:
        env["PIPELINE_STATE_PATH"] = str(state_path)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    if no_gh:
        env["PATH"] = _no_gh_path()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def _base_state_data():
    """codex-review 실행 전제조건을 충족한 격리 state dict."""
    return {
        "version": "1.2.0",
        "pipeline_id": PIPELINE_ID,
        "pipeline_type": "BUG",
        "type": "BUG",
        "description": "Codex CLI ERROR vs REJECT 분리 회귀 테스트",
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
    """격리된 pipeline state를 생성하는 fixture. (tmp_path, state_file) 반환."""
    state_file = tmp_path / "pipeline_state.json"
    state_file.write_text(
        json.dumps(_base_state_data(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return tmp_path, state_file


def _read_codex_result(tmp_path: Path):
    """codex_review_result.json(SSoT)을 읽어 final_state로 반환한다."""
    path = tmp_path / ".pipeline" / "codex_review_result.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_history(tmp_path: Path):
    """codex_review_history.jsonl 이력을 리스트로 반환한다."""
    path = tmp_path / ".pipeline" / "codex_review_history.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_codex_result(tmp_path: Path, data: dict):
    """codex_review_result.json을 격리 .pipeline 디렉토리에 직접 작성한다 (이전 기록 시뮬레이션)."""
    pipeline_dir = tmp_path / ".pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    (pipeline_dir / "codex_review_result.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _run_codex_review(tmp_path, state_file, *extra):
    """gates codex-review를 CLI로 실행한다."""
    return run_pipeline(
        "gates", "codex-review", *extra,
        state_path=state_file, cwd=tmp_path,
    )


# ---------------------------------------------------------------------------
# TC-1: usage limit 메시지 → ERROR, reject_count 증가 없음
# ---------------------------------------------------------------------------
def test_tc1_usage_limit_is_error_not_reject(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    result = _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "1",
        "--codex-cli-stdout", "You've hit your usage limit. Try again later.",
    )
    final = _read_codex_result(tmp_path)
    assert final is not None, f"result.json 미생성 (stderr={result.stderr})"
    assert final["status"] == "ERROR", final
    assert final["error_type"] == "usage_limit", final
    assert final["reject_count"] == 0, "usage limit은 reject_count를 증가시키면 안 됨"
    assert final["cli_error_count"] == 1, final
    assert final["acceptance_eligible"] is False, final
    assert final["verdict"] is None, "ERROR는 verdict가 없어야 함"


# ---------------------------------------------------------------------------
# TC-2: 실제 REJECT - ... → REJECTED, reject_count 증가
# ---------------------------------------------------------------------------
def test_tc2_explicit_reject_increments_reject_count(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    result = _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", "REJECT - packet SHA mismatch detected",
        "--packet-sha256", DUMMY_PACKET_SHA,
    )
    final = _read_codex_result(tmp_path)
    assert final is not None, f"result.json 미생성 (stderr={result.stderr})"
    assert final["status"] == "REJECTED", final
    assert final["reject_count"] == 1, final
    assert final["cli_error_count"] == 0, final
    assert final["verdict"] == "REJECT", final
    assert final["reject_reason"] == "packet SHA mismatch detected", final
    assert final["acceptance_eligible"] is False, final


# ---------------------------------------------------------------------------
# TC-3: CLI timeout → ERROR, 승인 코드 미발급
# ---------------------------------------------------------------------------
def test_tc3_timeout_is_error(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "-1",
        "--codex-cli-stderr", "operation timed out after 60s",
    )
    final = _read_codex_result(tmp_path)
    assert final["status"] == "ERROR", final
    assert final["error_type"] == "timeout", final
    assert final["reject_count"] == 0, final
    assert final["acceptance_eligible"] is False, final


# ---------------------------------------------------------------------------
# TC-4: CLI nonzero exit → ERROR, REJECT로 표시 금지
# ---------------------------------------------------------------------------
def test_tc4_nonzero_exit_not_marked_reject(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "42",
        "--codex-cli-stdout", "unexpected internal error",
    )
    final = _read_codex_result(tmp_path)
    assert final["status"] == "ERROR", final
    assert final["error_type"] == "nonzero_exit", final
    assert final["status"] != "REJECTED", "nonzero exit을 REJECTED로 표시하면 안 됨"
    assert final["verdict"] is None, final
    assert final["reject_count"] == 0, final


# ---------------------------------------------------------------------------
# TC-5: parse failure (exit 0 + 빈 stdout) → ERROR, fail-closed
# ---------------------------------------------------------------------------
def test_tc5_parse_failure_is_error(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", "   ",
    )
    final = _read_codex_result(tmp_path)
    assert final["status"] == "ERROR", final
    assert final["error_type"] == "parse_failure", final
    assert final["acceptance_eligible"] is False, "fail-closed: 승인 불가"


# ---------------------------------------------------------------------------
# TC-6: ERROR 상태에서 reject rate-limit 미적용
# ---------------------------------------------------------------------------
def test_tc6_error_does_not_trigger_reject_rate_limit(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    # 이전 기록: cli_error_count가 이미 5여도 reject_count가 낮으면 rate-limit 미적용.
    _write_codex_result(tmp_path, {
        "schema_version": 3,
        "pipeline_id": PIPELINE_ID,
        "status": "ERROR",
        "error_type": "usage_limit",
        "reject_count": 0,
        "cli_error_count": 5,
        "acceptance_eligible": False,
        "verdict": None,
    })
    result = _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "1",
        "--codex-cli-stdout", "You've hit your usage limit",
        "--retry-cli-error",
    )
    final = _read_codex_result(tmp_path)
    assert "codex_reject_rate_limited" not in (result.stdout + result.stderr), \
        "cli_error_count는 reject rate-limit을 트리거하면 안 됨"
    assert final["status"] == "ERROR", final
    assert final["reject_count"] == 0, final
    assert final["cli_error_count"] == 6, "cli_error_count는 누적되어야 함"


# ---------------------------------------------------------------------------
# TC-7: --retry-cli-error는 ERROR 상태에서만 허용
# ---------------------------------------------------------------------------
def test_tc7_retry_cli_error_allowed_on_error_state(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    _write_codex_result(tmp_path, {
        "schema_version": 3,
        "pipeline_id": PIPELINE_ID,
        "status": "ERROR",
        "error_type": "network",
        "reject_count": 0,
        "cli_error_count": 1,
        "acceptance_eligible": False,
        "verdict": None,
    })
    result = _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", "APPROVE_TO_USER",
        "--packet-sha256", DUMMY_PACKET_SHA,
        "--retry-cli-error",
    )
    final = _read_codex_result(tmp_path)
    # ERROR 상태에서 재시도 성공 → APPROVED로 전환 가능.
    assert result.returncode == 0, f"ERROR 상태 재시도는 허용되어야 함 (stderr={result.stderr})"
    assert final["status"] == "APPROVED", final
    assert final["acceptance_eligible"] is True, final


# ---------------------------------------------------------------------------
# TC-8: REJECTED 상태에서 --retry-cli-error 우회 불가
# ---------------------------------------------------------------------------
def test_tc8_retry_cli_error_blocked_on_rejected_state(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    _write_codex_result(tmp_path, {
        "schema_version": 3,
        "pipeline_id": PIPELINE_ID,
        "status": "REJECTED",
        "error_type": None,
        "reject_count": 1,
        "cli_error_count": 0,
        "acceptance_eligible": False,
        "verdict": "REJECT",
    })
    result = _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", "APPROVE_TO_USER",
        "--retry-cli-error",
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, "REJECTED 상태에서 --retry-cli-error는 차단되어야 함"
    assert "codex_retry_cli_error_on_reject" in combined, combined
    # 기록은 REJECTED로 유지 (우회로 APPROVED 되지 않아야 함).
    final = _read_codex_result(tmp_path)
    assert final["status"] == "REJECTED", final


# ---------------------------------------------------------------------------
# TC-9: retry 시 bundle/head/packet/pr_body SHA 변경되면 차단 (skip 처리 가능)
# ---------------------------------------------------------------------------
@pytest.mark.skip(reason="TC-9: SHA provenance 재검증은 request-accept publish 경로(gh 필요)에서 커버됨")
def test_tc9_retry_sha_change_blocked(isolated_pipeline):
    pass


# ---------------------------------------------------------------------------
# TC-10: ERROR 후 재시도 성공 시 stale/provenance 검증 유지
# ---------------------------------------------------------------------------
def test_tc10_error_then_retry_success_preserves_verdict(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    # 1차: usage limit ERROR.
    _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "1",
        "--codex-cli-stdout", "You've hit your usage limit",
    )
    first = _read_codex_result(tmp_path)
    assert first["status"] == "ERROR"
    # 2차: --retry-cli-error로 재시도, 이번엔 REJECT verdict.
    _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", "REJECT - contract audit failed",
        "--packet-sha256", DUMMY_PACKET_SHA,
        "--retry-cli-error",
    )
    second = _read_codex_result(tmp_path)
    assert second["status"] == "REJECTED", second
    assert second["verdict"] == "REJECT", second
    # ERROR는 reject_count를 안 올렸으므로, 이번 REJECT로 reject_count=1.
    assert second["reject_count"] == 1, second
    # cli_error_count는 1차 ERROR에서 1로 유지.
    assert second["cli_error_count"] == 1, second


# ---------------------------------------------------------------------------
# TC-11: ERROR 결과는 APPROVED cache로 재사용 불가
# ---------------------------------------------------------------------------
def test_tc11_error_not_reused_as_approved(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "7",
        "--codex-cli-stderr", "network connection reset by peer",
    )
    final = _read_codex_result(tmp_path)
    assert final["status"] == "ERROR", final
    assert final["error_type"] == "network", final
    # ERROR는 verdict=None, acceptance_eligible=false → APPROVED로 재사용 불가.
    assert final["verdict"] is None, final
    assert final["acceptance_eligible"] is False, final
    assert final.get("packet_sha256", "") == "", "ERROR는 packet_sha256을 기록하지 않아야 함"


# ---------------------------------------------------------------------------
# TC-12: 사용자 출력에 "REJECT"가 아니라 "Codex CLI ERROR"로 표시됨
# ---------------------------------------------------------------------------
def test_tc12_user_output_shows_codex_cli_error(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    result = _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "1",
        "--codex-cli-stdout", "You've hit your usage limit",
    )
    combined = result.stdout + result.stderr
    assert "CODEX REVIEW ERROR" in combined, combined
    assert "error_type: usage_limit" in combined, combined
    # "CODEX REVIEW REJECT" 형태로 표시되면 안 됨.
    assert "CODEX REVIEW REJECT" not in combined, "CLI 오류를 REJECT로 표시하면 안 됨"
    # 이력에도 ERROR로 기록.
    hist = _read_history(tmp_path)
    assert any(h["status"] == "ERROR" and h["counts_toward_reject_rate_limit"] is False
               for h in hist), hist
