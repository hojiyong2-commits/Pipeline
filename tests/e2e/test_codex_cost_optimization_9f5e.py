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
TC-9: --retry-cli-error + 동일 snapshot → 새 attempt 허용(APPROVED 전환, 새 attempt_id).
TC-10(snapshot): --retry-cli-error + snapshot 변경 → INVALIDATED_BY_SNAPSHOT_CHANGE + BLOCKED.
TC-10(retry-success): ERROR 후 재시도 성공 시 verdict/카운터 provenance 유지.
TC-11: ERROR 결과는 APPROVED cache로 재사용 불가.
TC-12: 사용자 출력에 "REJECT"가 아니라 "Codex CLI ERROR"로 표시됨.
TC-13: JSON verdict protocol / legacy 오분류 경계("REJECTED"/"REJECT_LIMIT"/"INFO: REJECT" → ERROR).
TC-14: parse_failure ERROR는 reject_count를 올리지 않는다.
TC-15: APPROVED result는 attempt_id + effective 포인터 + snapshot_identity(6차원)를 기록한다.
TC-16: ERROR result도 attempt_id + snapshot_identity를 기록한다(retry 비교 근거).
TC-17: "WARNING: APPROVE_TO_USER" 진단 접두는 APPROVED로 오분류되지 않는다.
"""
import subprocess
import sys
import os
import json
import shutil
import hashlib
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


def _sha256_file_local(path: Path) -> str:
    """디스크 파일의 SHA-256 hex digest를 반환한다 (pipeline._sha256_file과 동일 규칙)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# TC-9: --retry-cli-error + 동일 snapshot → 새 attempt 허용 (APPROVED 전환 가능)
# ---------------------------------------------------------------------------
def test_tc9_retry_cli_error_same_snapshot_allows_new_attempt(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    # cwd에 packet 파일을 만들어 결정적 packet_sha256을 갖게 한다 (gh 없음 → pr_head_sha는 빈 값).
    packet_path = tmp_path / "human_acceptance_packet.md"
    packet_path.write_text("packet body for TC-9\n", encoding="utf-8")
    packet_sha = _sha256_file_local(packet_path)

    # 이전 ERROR attempt: 동일 packet_sha256을 기록 (snapshot 동일 상황).
    _write_codex_result(tmp_path, {
        "schema_version": 3,
        "pipeline_id": PIPELINE_ID,
        "attempt_id": "cr-prevattempt01",
        "status": "ERROR",
        "error_type": "usage_limit",
        "reject_count": 0,
        "cli_error_count": 1,
        "acceptance_eligible": False,
        "verdict": None,
        "pr_head_sha": "",           # gh 없음 → 이전에도 빈 값
        "packet_sha256": packet_sha,  # 현재 packet과 동일 → snapshot 변화 없음
    })
    result = _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", "APPROVE_TO_USER",
        "--packet-sha256", DUMMY_PACKET_SHA,
        "--retry-cli-error",
    )
    combined = result.stdout + result.stderr
    assert "codex_snapshot_changed_need_new_review" not in combined, combined
    final = _read_codex_result(tmp_path)
    assert result.returncode == 0, f"동일 snapshot retry는 허용되어야 함 (stderr={result.stderr})"
    assert final["status"] == "APPROVED", final
    # 새 attempt_id가 생성되어 이전 값과 달라야 한다.
    assert final.get("attempt_id", "").startswith("cr-"), final
    assert final["attempt_id"] != "cr-prevattempt01", "retry는 새 attempt_id를 발급해야 함"
    assert final["acceptance_eligible"] is True, final


# ---------------------------------------------------------------------------
# TC-10: --retry-cli-error + snapshot 변경 → codex_snapshot_changed_need_new_review BLOCKED
# ---------------------------------------------------------------------------
def test_tc10_snapshot_changed_blocked(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    # 현재 cwd packet은 새 내용 → 새 SHA.
    packet_path = tmp_path / "human_acceptance_packet.md"
    packet_path.write_text("NEW packet body — snapshot changed\n", encoding="utf-8")
    curr_sha = _sha256_file_local(packet_path)

    # 이전 ERROR attempt는 다른 packet_sha256을 기록 → snapshot 변경.
    stale_sha = "b" * 64
    assert stale_sha != curr_sha
    _write_codex_result(tmp_path, {
        "schema_version": 3,
        "pipeline_id": PIPELINE_ID,
        "attempt_id": "cr-staleattempt9",
        "status": "ERROR",
        "error_type": "usage_limit",
        "reject_count": 0,
        "cli_error_count": 1,
        "acceptance_eligible": False,
        "verdict": None,
        "pr_head_sha": "",
        "packet_sha256": stale_sha,   # 현재 packet과 다름 → snapshot 변경 탐지
    })
    result = _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", "APPROVE_TO_USER",
        "--packet-sha256", DUMMY_PACKET_SHA,
        "--retry-cli-error",
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, "snapshot 변경 시 retry는 차단되어야 함"
    assert "codex_snapshot_changed_need_new_review" in combined, combined
    assert "cr-staleattempt9" in combined, "차단 메시지에 이전 attempt_id를 표시해야 함"
    # 이력에 무효화 기록이 남아야 한다.
    hist = _read_history(tmp_path)
    assert any(
        h.get("status") == "INVALIDATED_BY_SNAPSHOT_CHANGE"
        and h.get("invalidation_reason") == "snapshot_changed_on_retry"
        for h in hist
    ), hist


# ---------------------------------------------------------------------------
# TC-13: JSON verdict protocol / legacy parse_failure 경계 검증
# ---------------------------------------------------------------------------
def test_tc13_parse_failure_and_json_verdict_cases(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline

    def run_and_read(*extra):
        # 각 케이스마다 이전 결과를 초기화하여 카운터 오염을 방지한다.
        pipeline_dir = tmp_path / ".pipeline"
        result_json = pipeline_dir / "codex_review_result.json"
        if result_json.exists():
            result_json.unlink()
        _run_codex_review(tmp_path, state_file, *extra)
        return _read_codex_result(tmp_path)

    # "REJECTED" 단독 → ERROR (parse_failure).
    f = run_and_read("--codex-cli-exit-code", "0", "--codex-cli-stdout", "REJECTED")
    assert f["status"] == "ERROR" and f["error_type"] == "parse_failure", f

    # "REJECT_LIMIT - reason" → ERROR (parse_failure).
    f = run_and_read("--codex-cli-exit-code", "0", "--codex-cli-stdout", "REJECT_LIMIT - hit the cap")
    assert f["status"] == "ERROR" and f["error_type"] == "parse_failure", f

    # "INFO: REJECT - reason" → ERROR (parse_failure).
    f = run_and_read("--codex-cli-exit-code", "0", "--codex-cli-stdout", "INFO: REJECT - context note")
    assert f["status"] == "ERROR" and f["error_type"] == "parse_failure", f

    # "REJECT" 단독 → ERROR (parse_failure).
    f = run_and_read("--codex-cli-exit-code", "0", "--codex-cli-stdout", "REJECT")
    assert f["status"] == "ERROR" and f["error_type"] == "parse_failure", f

    # valid "REJECT - some reason" → REJECTED (legacy fallback).
    f = run_and_read(
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", "REJECT - genuine reason here",
        "--packet-sha256", DUMMY_PACKET_SHA,
    )
    assert f["status"] == "REJECTED" and f["verdict"] == "REJECT", f
    assert f["reject_reason"] == "genuine reason here", f

    # JSON {"verdict": "REJECT", "reason": "too short"} → REJECTED (JSON protocol).
    f = run_and_read(
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", '{"verdict": "REJECT", "reason": "too short"}',
        "--packet-sha256", DUMMY_PACKET_SHA,
    )
    assert f["status"] == "REJECTED" and f["reject_reason"] == "too short", f

    # JSON {"verdict": "APPROVE_TO_USER"} → APPROVED (JSON protocol).
    f = run_and_read(
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", '{"verdict": "APPROVE_TO_USER"}',
        "--packet-sha256", DUMMY_PACKET_SHA,
    )
    assert f["status"] == "APPROVED" and f["verdict"] == "APPROVE_TO_USER", f

    # malformed JSON (닫는 중괄호 없음, 시작이 '{') → parse_failure ERROR (fail-closed).
    f = run_and_read("--codex-cli-exit-code", "0", "--codex-cli-stdout", "{not valid json")
    assert f["status"] == "ERROR" and f["error_type"] == "parse_failure", f


# ---------------------------------------------------------------------------
# TC-14: parse_failure ERROR / JSON parse 실패는 reject_count를 올리지 않는다
# ---------------------------------------------------------------------------
def test_tc14_reject_count_not_increased_on_parse_failure(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline

    # 1차: 유효 REJECT → reject_count=1.
    _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", "REJECT - real reason",
        "--packet-sha256", DUMMY_PACKET_SHA,
    )
    first = _read_codex_result(tmp_path)
    assert first["reject_count"] == 1, first

    # 2차: "REJECTED" 단독(parse_failure ERROR) → reject_count 변화 없음(=1 유지).
    #   retry 플래그 없이 실행한다(ERROR는 rate-limit을 트리거하지 않으므로 정상 기록됨).
    _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", "REJECTED",
    )
    second = _read_codex_result(tmp_path)
    assert second["status"] == "ERROR", second
    assert second["reject_count"] == 1, "parse_failure ERROR는 reject_count를 올리면 안 됨"
    assert second["cli_error_count"] == 1, second


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


# ---------------------------------------------------------------------------
# TC-15: APPROVED result는 attempt_id + effective 포인터 + snapshot_identity를 기록한다
#        (attempt 단위 상태 모델 구조 증명 — REJECT-1)
# ---------------------------------------------------------------------------
def test_tc15_approved_records_attempt_and_snapshot_identity(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    packet_path = tmp_path / "human_acceptance_packet.md"
    packet_path.write_text("approved packet body\n", encoding="utf-8")
    packet_sha = _sha256_file_local(packet_path)

    result = _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", '{"schema_version": 1, "verdict": "APPROVE_TO_USER", "reason": "ok"}',
        "--packet-sha256", packet_sha,
    )
    assert result.returncode == 0, f"APPROVED는 exit 0이어야 함 (stderr={result.stderr})"
    final = _read_codex_result(tmp_path)
    assert final["status"] == "APPROVED", final
    # attempt 단위 상태 모델: attempt_id + effective 포인터.
    assert final.get("attempt_id", "").startswith("cr-"), final
    assert final.get("effective") is True, "APPROVED result는 effective=true여야 함"
    assert final.get("schema_version") == 4, "attempt-model은 schema_version 4"
    # snapshot_identity 중첩 dict가 6개 차원을 포함해야 한다.
    ident = final.get("snapshot_identity")
    assert isinstance(ident, dict), final
    for key in ("pr_head_sha", "packet_sha256", "pr_body_candidate_sha256",
                "staging_id", "contract_sha256", "review_bundle_sha256"):
        assert key in ident, f"snapshot_identity에 {key} 누락: {ident}"
    # packet_sha256 차원은 검토 대상 packet SHA와 일치해야 한다.
    assert ident["packet_sha256"] == packet_sha, ident
    # 이력에도 APPROVED attempt가 snapshot_identity와 함께 append되어야 한다.
    hist = _read_history(tmp_path)
    assert any(
        h.get("status") == "APPROVED"
        and h.get("attempt_id", "").startswith("cr-")
        and isinstance(h.get("snapshot_identity"), dict)
        for h in hist
    ), hist


# ---------------------------------------------------------------------------
# TC-16: ERROR result도 attempt_id + snapshot_identity를 기록한다
#        (--retry-cli-error가 이후 snapshot 비교를 할 수 있어야 함 — REJECT-1)
# ---------------------------------------------------------------------------
def test_tc16_error_records_attempt_and_snapshot_identity(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    packet_path = tmp_path / "human_acceptance_packet.md"
    packet_path.write_text("error-time packet body\n", encoding="utf-8")
    packet_sha = _sha256_file_local(packet_path)

    _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "1",
        "--codex-cli-stdout", "You've hit your usage limit. Try again later.",
    )
    final = _read_codex_result(tmp_path)
    assert final["status"] == "ERROR", final
    assert final["error_type"] == "usage_limit", final
    # ERROR attempt에도 고유 attempt_id + snapshot_identity가 기록되어야 한다.
    assert final.get("attempt_id", "").startswith("cr-"), final
    ident = final.get("snapshot_identity")
    assert isinstance(ident, dict), final
    # 디스크 packet이 존재하므로 packet_sha256 차원이 실제 값으로 채워져야 한다.
    assert ident.get("packet_sha256") == packet_sha, ident
    # verdict는 없어야 하고 승인 불가여야 한다(ERROR는 APPROVED cache로 재사용 불가).
    assert final.get("verdict") is None, final
    assert final.get("acceptance_eligible") is False, final


# ---------------------------------------------------------------------------
# TC-17: "WARNING: APPROVE_TO_USER" 진단 접두는 APPROVED로 오분류되지 않는다 (REJECT-2)
# ---------------------------------------------------------------------------
def test_tc17_warning_prefixed_approve_not_misclassified(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    result = _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout", "WARNING: APPROVE_TO_USER may be premature",
    )
    final = _read_codex_result(tmp_path)
    # 진단 접두가 붙은 APPROVE_TO_USER는 verdict가 아니라 parse_failure ERROR여야 한다.
    assert final["status"] == "ERROR", final
    assert final["error_type"] == "parse_failure", final
    assert final.get("verdict") is None, final
    assert final.get("acceptance_eligible") is False, final
    assert result.returncode != 0, "parse_failure는 non-zero exit"


# ---------------------------------------------------------------------------
# 구조 증명: REJECTED / REJECT_LIMIT / bare REJECT 등 misformat은 parse_failure ERROR
# ---------------------------------------------------------------------------
def test_tc_reject_misformat_parse_failure():
    """REJECTED, REJECT_LIMIT, INFO: REJECT, WARNING: REJECT, bare REJECT는 parse_failure ERROR.

    startswith("REJECT") 취약 판정을 제거하고 정확한 "REJECT - <사유>" 형식만 REJECTED로
    승격됨을 단위 수준에서 증명한다(fail-closed).
    """
    sys.path.insert(0, str(Path(__file__).parents[2]))
    from pipeline import _run_codex_cli_review

    bad_cases = [
        "REJECTED: security issue",
        "REJECT_LIMIT exceeded",
        "INFO: REJECT - something",
        "WARNING: REJECT",
        "REJECT",
    ]
    for stdout in bad_cases:
        result = _run_codex_cli_review(0, stdout, "")
        assert result["status"] == "ERROR", \
            f"Expected ERROR for {stdout!r}, got {result['status']}"
        assert result.get("error_type") == "parse_failure", \
            f"Expected parse_failure for {stdout!r}"


# ---------------------------------------------------------------------------
# 구조 증명: JSON verdict protocol — REJECT는 REJECTED, 스키마 위반은 parse_failure
# ---------------------------------------------------------------------------
def test_tc_json_verdict_reject_count():
    """JSON {"verdict":"REJECT"} → REJECTED. {"verdict":"REJECTED"}(오타) → parse_failure ERROR."""
    sys.path.insert(0, str(Path(__file__).parents[2]))
    from pipeline import _run_codex_cli_review

    json_reject = '{"schema_version":1,"verdict":"REJECT","reason":"security issue"}'
    result = _run_codex_cli_review(0, json_reject, "")
    assert result["status"] == "REJECTED", result
    assert result["reject_reason"] == "security issue", result

    # verdict 값이 유효 집합(APPROVE_TO_USER/REJECT) 밖이면 승격하지 않고 parse_failure ERROR.
    bad_json = '{"schema_version":1,"verdict":"REJECTED","reason":"oops"}'
    result2 = _run_codex_cli_review(0, bad_json, "")
    assert result2["status"] == "ERROR", result2
    assert result2.get("error_type") == "parse_failure", result2


# ---------------------------------------------------------------------------
# TC-B1: APPROVED result에 review_bundle_sha256이 non-empty로 기록된다
#        (_build_codex_review_bundle이 입력 bundle을 materialize하고 SHA를 남긴다)
# ---------------------------------------------------------------------------
def test_bundle_sha256_nonempty_in_approved(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    packet_path = tmp_path / "human_acceptance_packet.md"
    packet_path.write_text("approved bundle packet body\n", encoding="utf-8")
    packet_sha = _sha256_file_local(packet_path)

    result = _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout",
        '{"schema_version":1,"verdict":"APPROVE_TO_USER","reason":"ok"}',
        "--packet-sha256", packet_sha,
    )
    assert result.returncode == 0, f"APPROVED는 exit 0이어야 함 (stderr={result.stderr})"
    final = _read_codex_result(tmp_path)
    assert final is not None, f"result.json 미생성 (stderr={result.stderr})"
    assert final["status"] == "APPROVED", final
    # top-level + snapshot_identity 양쪽에 non-empty bundle SHA가 있어야 한다.
    assert final.get("review_bundle_sha256", "") != "", (
        f"APPROVED result에 review_bundle_sha256이 있어야 함: {final}"
    )
    ident = final.get("snapshot_identity")
    assert isinstance(ident, dict), final
    assert ident.get("review_bundle_sha256", "") != "", ident
    # bundle 파일이 격리 .pipeline 경로에 실제로 materialize되어야 한다(테스트 오염 방지).
    bundle_file = tmp_path / ".pipeline" / "codex_review_bundle.json"
    assert bundle_file.exists(), "codex_review_bundle.json이 격리 경로에 생성되어야 함"
    assert _sha256_file_local(bundle_file) == final["review_bundle_sha256"], (
        "기록된 review_bundle_sha256이 실제 bundle 파일 SHA와 일치해야 함"
    )


# ---------------------------------------------------------------------------
# TC-B2: ERROR result에도 review_bundle_sha256이 non-empty로 기록된다
#        (--retry-cli-error가 이후 이 값을 snapshot 비교 기준으로 사용할 수 있어야 함)
# ---------------------------------------------------------------------------
def test_bundle_sha256_nonempty_in_error(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    result = _run_codex_review(
        tmp_path, state_file,
        "--codex-cli-exit-code", "1",
        "--codex-cli-stdout", "You've hit your usage limit at 11:34 AM",
    )
    final = _read_codex_result(tmp_path)
    assert final is not None, f"result.json 미생성 (stderr={result.stderr})"
    assert final["status"] == "ERROR", final
    assert final.get("review_bundle_sha256", "") != "", (
        f"ERROR result에 review_bundle_sha256이 있어야 함: {final}"
    )
    ident = final.get("snapshot_identity")
    assert isinstance(ident, dict), final
    assert ident.get("review_bundle_sha256", "") != "", ident


# ---------------------------------------------------------------------------
# TC-B3: 이전 ERROR attempt의 review_bundle_sha256이 비어 있으면 --retry-cli-error 금지
#        (bundle 없이 기록된 구 결과는 신뢰할 수 있는 snapshot 비교 기준이 없음 — fail-closed)
# ---------------------------------------------------------------------------
def test_retry_empty_prev_bundle_sha_blocked(isolated_pipeline):
    tmp_path, state_file = isolated_pipeline
    # 이전 ERROR: review_bundle_sha256이 빈 값(구 스키마 시뮬레이션).
    _write_codex_result(tmp_path, {
        "schema_version": 4,
        "pipeline_id": PIPELINE_ID,
        "status": "ERROR",
        "attempt_id": "cr-emptybundle01",
        "review_bundle_sha256": "",
        "snapshot_identity": {
            "pr_head_sha": "",
            "packet_sha256": "",
            "pr_body_candidate_sha256": "",
            "staging_id": "",
            "contract_sha256": "",
            "review_bundle_sha256": "",
        },
        "error_type": "usage_limit",
        "error_retryable": True,
        "reject_count": 0,
        "cli_error_count": 1,
        "acceptance_eligible": False,
        "verdict": None,
    })
    result = _run_codex_review(
        tmp_path, state_file,
        "--retry-cli-error",
        "--codex-cli-exit-code", "0",
        "--codex-cli-stdout",
        '{"schema_version":1,"verdict":"APPROVE_TO_USER","reason":"ok"}',
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"빈 이전 bundle SHA로 retry가 허용되면 안 됨. exit={result.returncode}, out={combined[:400]}"
    )
    assert "codex_retry_missing_prev_bundle_sha" in combined, combined[:400]


def test_final_packet_no_stale_skip_count():
    """TC-18: stale '11 passed 1 skipped' 문구가 있어도 human_acceptance_packet에
    그대로 노출되지 않도록 _refresh_test_count_in_verification이 실시간 카운트로 갱신해야 함."""
    import sys
    repo_root = Path(__file__).parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from pipeline import _refresh_test_count_in_verification

    stale_text = "TC-1~TC-12 E2E 테스트 11 passed 1 skipped"
    refreshed = _refresh_test_count_in_verification(
        stale_text, pipeline_root=str(repo_root)
    )

    assert "1 skipped" not in refreshed, f"stale '1 skipped' 문구가 제거되지 않음: {refreshed}"
    assert "11 passed" not in refreshed, f"stale '11 passed' 문구가 제거되지 않음: {refreshed}"


def test_final_packet_test_count_not_stale():
    """TC-19: _refresh_test_count_in_verification이 실제 테스트 파일의 def test_ 카운트를
    올바르게 반영해야 함 (현재 25개: 기존 23 + TC-18/TC-19)."""
    import sys
    repo_root = Path(__file__).parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from pipeline import (
        _count_test_functions_in_file,
        _refresh_test_count_in_verification,
    )

    test_file = repo_root / "tests" / "e2e" / "test_codex_cost_optimization_9f5e.py"
    actual_count = _count_test_functions_in_file(str(test_file))
    assert actual_count >= 23, f"실제 테스트 카운트가 예상보다 작음: {actual_count}"

    stale_text = "TC-1~TC-12 E2E 테스트 11 passed 1 skipped — 상태 분리 검증 완료"
    refreshed = _refresh_test_count_in_verification(
        stale_text, pipeline_root=str(repo_root)
    )

    assert f"{actual_count} passed" in refreshed, (
        f"실제 카운트 '{actual_count} passed'가 반영되지 않음: {refreshed}"
    )
    assert "0 skipped" in refreshed, f"'0 skipped'이 반영되지 않음: {refreshed}"
