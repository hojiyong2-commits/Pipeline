# [Purpose]: IMP-20260626-4121 Codex Review Loop 회귀 테스트. REJECT 원문 exit2 재주입,
#   APPROVE ACCEPT-pipeline_id 출력(nonce 미노출), 중복 주입 차단, APPROVED stale 재트리거 방지,
#   reject_count 상한 중단, pipeline.py _check_codex_review_gate BLOCKED/PASS 판정을 검증한다.
# [Assumptions]: .claude/hooks/codex_user_acceptance_review.py와 pipeline.py가 import 가능하다.
#   gh/codex CLI는 monkeypatch로 대체하여 실제 호출하지 않는다. PIPELINE_STATE_PATH로 격리한다.
# [Vulnerability & Risks]: main() 경로 테스트는 외부 CLI를 monkeypatch하므로 실제 gh/codex
#   응답 형식 변화는 잡지 못한다. 소스 텍스트 검사로 gh pr comment / gates accept 결합 부재를 보강한다.
# [Improvement]: 시간이 더 있다면 subprocess 인자 그래프를 AST로 추적할 것이다.
"""Codex Review Loop 회귀 테스트 (IMP-20260626-4121)."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_HOOK_PATH = _REPO_ROOT / ".claude" / "hooks" / "codex_user_acceptance_review.py"
_ORACLE_DIR = _REPO_ROOT / "tests" / "oracles" / "IMP-20260626-4121"

_PIPELINE_ID = "IMP-20260626-4121"
_PR_URL = "https://github.com/hojiyong2-commits/Pipeline/pull/777"


def _load_hook():
    """hook 모듈을 importlib로 로드하여 반환."""
    spec = importlib.util.spec_from_file_location(
        "codex_loop_under_test", str(_HOOK_PATH)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cx = _load_hook()
import pipeline  # noqa: E402  (project root on sys.path via conftest)


def _trigger_block(accept_code: str = f"ACCEPT-{_PIPELINE_ID}") -> str:
    """5요소를 모두 포함한 트리거 블록 텍스트를 만든다."""
    return (
        "사용자 승인 요청\n\n"
        f"PR: {_PR_URL}\n\n"
        "승인 코드:\n"
        f"{accept_code}\n\n"
        "CODEX 검토 필요"
    )


# ---------------------------------------------------------------------------
# 1. 트리거 5요소 감지
# ---------------------------------------------------------------------------
def test_trigger_detect_five_elements():
    """5요소 모두 있으면 parse_acceptance_block이 블록을 반환한다 (Codex 경로 진입)."""
    block = cx.parse_acceptance_block(_trigger_block())
    assert block is not None
    assert block["pr_url"] == _PR_URL
    assert block["accept_code"] == f"ACCEPT-{_PIPELINE_ID}"
    assert block["pipeline_id"] == _PIPELINE_ID

    # 5요소 중 하나(CODEX 검토 필요)가 없으면 None
    no_marker = _trigger_block().replace("CODEX 검토 필요", "끝")
    assert cx.parse_acceptance_block(no_marker) is None


# ---------------------------------------------------------------------------
# 2~3. REJECT 원문 출력 + exit code 2
# ---------------------------------------------------------------------------
def test_reject_raw_output_no_prefix():
    """REJECT 원문이 prefix/suffix 없이 그대로 output에 담긴다."""
    reason = "REJECT - PR 본문에 변경 파일 설명이 누락되었습니다."
    out = cx.process_verdict(reason, _PIPELINE_ID, _PR_URL, reject_count=1)
    assert out["decision"] == "REJECT"
    assert out["output"] == reason  # 단 한 글자도 바뀌지 않음
    assert not out["output"].startswith("[")
    assert "Codex" not in out["output"]


def test_reject_exit_code_2():
    """REJECT 처리 후 exit code 2를 반환한다."""
    out = cx.process_verdict(
        "REJECT - 사유", _PIPELINE_ID, _PR_URL, reject_count=1
    )
    assert out["exit_code"] == 2


def test_reject_channel_stdout_exit2():
    """REJECT 재주입 채널: output은 원문 그대로(prefix/suffix 없음) + exit_code=2.

    Stop hook은 이 output을 print(stdout) + sys.exit(2)로 그대로 재주입한다.
    """
    out = cx.process_verdict(
        "REJECT - 결함있음", _PIPELINE_ID, _PR_URL, reject_count=1
    )
    assert out["exit_code"] == 2
    assert out["decision"] == "REJECT"
    # output이 그대로 "REJECT - 결함있음" — prefix/suffix/번역/요약 없음
    assert out["output"] == "REJECT - 결함있음"
    assert out["reject_reason"] == "REJECT - 결함있음"


# ---------------------------------------------------------------------------
# 4. 중복 주입 차단 (같은 head/packet/reason)
# ---------------------------------------------------------------------------
def test_dedupe_same_head_packet_reason():
    """같은 pipeline/head/packet/reason 조합 반복 시 중복으로 판정한다 (exit 0)."""
    reason = "REJECT - 동일 사유"
    state = {
        "status": "REJECTED",
        "pipeline_id": _PIPELINE_ID,
        "pr_head_sha": "abc123",
        "packet_sha256": "pkt456",
        "last_reject_reason": reason,
    }
    assert cx._is_duplicate_reject(
        state, _PIPELINE_ID, "abc123", "pkt456", reason
    ) is True


def test_dedupe_different_head_sha_allows_retry():
    """head SHA가 다르면 같은 reason이어도 중복이 아니다 (재검토 허용)."""
    reason = "REJECT - 동일 사유"
    state = {
        "status": "REJECTED",
        "pipeline_id": _PIPELINE_ID,
        "pr_head_sha": "abc123",
        "packet_sha256": "pkt456",
        "last_reject_reason": reason,
    }
    assert cx._is_duplicate_reject(
        state, _PIPELINE_ID, "NEWSHA", "pkt456", reason
    ) is False


def test_dedupe_different_packet_sha_allows_retry():
    """packet SHA가 다르면 같은 reason이어도 중복이 아니다 (재검토 허용)."""
    reason = "REJECT - 동일 사유"
    state = {
        "status": "REJECTED",
        "pipeline_id": _PIPELINE_ID,
        "pr_head_sha": "abc123",
        "packet_sha256": "pkt456",
        "last_reject_reason": reason,
    }
    assert cx._is_duplicate_reject(
        state, _PIPELINE_ID, "abc123", "NEWPKT", reason
    ) is False


# ---------------------------------------------------------------------------
# 5. reject_count 상한 초과 시 루프 자동 중단
# ---------------------------------------------------------------------------
def test_reject_count_over_limit_halts():
    """reject_count > 5 (6번째)이면 REJECT_HALT로 자동 중단 + exit 0."""
    out = cx.process_verdict(
        "REJECT - 또 반려", _PIPELINE_ID, _PR_URL, reject_count=6
    )
    assert out["decision"] == "REJECT_HALT"
    assert out["exit_code"] == 0
    assert "자동 중단" in out["output"]
    assert "5" in out["output"]


# ---------------------------------------------------------------------------
# 6. APPROVE 상태 저장
# ---------------------------------------------------------------------------
def test_approve_saves_state(tmp_path):
    """_record_state가 codex_review_loop_state.json에 APPROVED를 저장한다."""
    state_path = tmp_path / "codex_review_loop_state.json"
    cx._record_state(
        state_path,
        {
            "pipeline_id": _PIPELINE_ID,
            "status": "APPROVED",
            "pr_head_sha": "abc123",
            "packet_sha256": "pkt456",
            "accept_code": f"ACCEPT-{_PIPELINE_ID}",
            "approved_at": cx._now_iso(),
        },
    )
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["status"] == "APPROVED"
    assert saved["pipeline_id"] == _PIPELINE_ID
    assert saved["accept_code"] == f"ACCEPT-{_PIPELINE_ID}"


# ---------------------------------------------------------------------------
# 7. APPROVED stale: head SHA가 다르면 gate BLOCKED
# ---------------------------------------------------------------------------
def test_approve_stale_different_head_sha(tmp_path, monkeypatch):
    """APPROVED 상태에서 head SHA가 바뀌면 _check_codex_review_gate가 BLOCKED.

    5개 필수 필드를 모두 채우되 pr_head_sha만 OLDSHA로 두어, 필드 누락이 아니라
    SHA 불일치로 BLOCKED됨을 확인한다.
    """
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "pipeline_state.json"))
    loop_path = tmp_path / ".pipeline" / "codex_review_loop_state.json"
    loop_path.parent.mkdir(parents=True, exist_ok=True)
    loop_path.write_text(
        json.dumps(
            {
                "status": "APPROVED",
                "pipeline_id": _PIPELINE_ID,
                "pr_head_sha": "OLDSHA",
                "packet_sha256": "pkt1",
                "pr_body_sha256": "body1",
                "accept_code": f"ACCEPT-{_PIPELINE_ID}",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "_get_current_pr_head_sha", lambda: "NEWSHA")
    monkeypatch.setattr(
        pipeline,
        "_load_acceptance_request",
        lambda: {"packet_sha256": "pkt1", "pr_body_sha256": "body1"},
    )
    result = pipeline._check_codex_review_gate(_PIPELINE_ID, {})
    assert result["status"] == "BLOCKED"
    assert result["failure_code"] == "codex_review_stale"


# ---------------------------------------------------------------------------
# 8. state 파일 없을 때 gate BLOCKED
# ---------------------------------------------------------------------------
def test_check_gate_no_file_blocked(tmp_path, monkeypatch):
    """codex_review_loop_state.json이 없으면 _check_codex_review_gate가 BLOCKED."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "pipeline_state.json"))
    result = pipeline._check_codex_review_gate(_PIPELINE_ID, {})
    assert result["status"] == "BLOCKED"
    assert result["failure_code"] == "codex_review_not_approved"


# ---------------------------------------------------------------------------
# 9. APPROVED + SHA 일치 시 gate PASS
# ---------------------------------------------------------------------------
def test_check_gate_approved_pass(tmp_path, monkeypatch):
    """APPROVED이고 5개 필수 필드 + SHA/packet 일치 시 _check_codex_review_gate가 PASS."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "pipeline_state.json"))
    loop_path = tmp_path / ".pipeline" / "codex_review_loop_state.json"
    loop_path.parent.mkdir(parents=True, exist_ok=True)
    loop_path.write_text(
        json.dumps(
            {
                "status": "APPROVED",
                "pipeline_id": _PIPELINE_ID,
                "pr_head_sha": "SHA1",
                "packet_sha256": "pkt1",
                "pr_body_sha256": "body1",
                "accept_code": f"ACCEPT-{_PIPELINE_ID}",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(pipeline, "_get_current_pr_head_sha", lambda: "SHA1")
    monkeypatch.setattr(
        pipeline,
        "_load_acceptance_request",
        lambda: {"packet_sha256": "pkt1", "pr_body_sha256": "body1"},
    )
    result = pipeline._check_codex_review_gate(_PIPELINE_ID, {})
    assert result["status"] == "PASS"


def _approved_loop_state(**overrides) -> dict:
    """5개 필수 필드를 모두 채운 APPROVED loop_state fixture (override 가능)."""
    base = {
        "status": "APPROVED",
        "pipeline_id": _PIPELINE_ID,
        "pr_head_sha": "SHA1",
        "packet_sha256": "pkt1",
        "pr_body_sha256": "body1",
        "accept_code": f"ACCEPT-{_PIPELINE_ID}",
    }
    base.update(overrides)
    return base


def _write_loop_state(tmp_path, state: dict):
    """loop_state를 .pipeline/codex_review_loop_state.json에 기록한다."""
    loop_path = tmp_path / ".pipeline" / "codex_review_loop_state.json"
    loop_path.parent.mkdir(parents=True, exist_ok=True)
    loop_path.write_text(json.dumps(state), encoding="utf-8")
    return loop_path


# ---------------------------------------------------------------------------
# 9-b. fail-closed 강화 (2차 REJECT 재작업): pipeline_id 불일치 BLOCKED
# ---------------------------------------------------------------------------
def test_check_gate_pipeline_id_mismatch_blocked(tmp_path, monkeypatch):
    """loop_state의 pipeline_id가 현재 파이프라인과 다르면 BLOCKED."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "pipeline_state.json"))
    _write_loop_state(tmp_path, _approved_loop_state(pipeline_id="IMP-99999999-AAAA"))
    monkeypatch.setattr(pipeline, "_get_current_pr_head_sha", lambda: "SHA1")
    monkeypatch.setattr(
        pipeline,
        "_load_acceptance_request",
        lambda: {"packet_sha256": "pkt1", "pr_body_sha256": "body1"},
    )
    result = pipeline._check_codex_review_gate(_PIPELINE_ID, {})
    assert result["status"] == "BLOCKED"
    assert result["failure_code"] == "codex_review_stale"


# ---------------------------------------------------------------------------
# 9-c. packet_sha256 불일치 BLOCKED
# ---------------------------------------------------------------------------
def test_check_gate_packet_sha_mismatch_blocked(tmp_path, monkeypatch):
    """acceptance_request.json의 packet_sha256이 loop_state와 다르면 BLOCKED."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "pipeline_state.json"))
    _write_loop_state(tmp_path, _approved_loop_state(packet_sha256="pkt1"))
    monkeypatch.setattr(pipeline, "_get_current_pr_head_sha", lambda: "SHA1")
    monkeypatch.setattr(
        pipeline,
        "_load_acceptance_request",
        lambda: {"packet_sha256": "DIFFERENT_PKT", "pr_body_sha256": "body1"},
    )
    result = pipeline._check_codex_review_gate(_PIPELINE_ID, {})
    assert result["status"] == "BLOCKED"
    assert result["failure_code"] == "codex_review_stale"


# ---------------------------------------------------------------------------
# 9-d. pr_body_sha256 불일치 BLOCKED
# ---------------------------------------------------------------------------
def test_check_gate_pr_body_sha_mismatch_blocked(tmp_path, monkeypatch):
    """acceptance_request.json의 pr_body_sha256이 loop_state와 다르면 BLOCKED."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "pipeline_state.json"))
    _write_loop_state(tmp_path, _approved_loop_state(pr_body_sha256="body1"))
    monkeypatch.setattr(pipeline, "_get_current_pr_head_sha", lambda: "SHA1")
    monkeypatch.setattr(
        pipeline,
        "_load_acceptance_request",
        lambda: {"packet_sha256": "pkt1", "pr_body_sha256": "DIFFERENT_BODY"},
    )
    result = pipeline._check_codex_review_gate(_PIPELINE_ID, {})
    assert result["status"] == "BLOCKED"
    assert result["failure_code"] == "codex_review_stale"


# ---------------------------------------------------------------------------
# 9-e. 필수 필드 누락 시 BLOCKED (fail-closed)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "missing_field",
    ["pipeline_id", "pr_head_sha", "packet_sha256", "pr_body_sha256", "accept_code"],
)
def test_check_gate_missing_field_blocked(tmp_path, monkeypatch, missing_field):
    """5개 필수 필드 중 하나라도 빈 값이면 비교 SKIP 없이 즉시 BLOCKED."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "pipeline_state.json"))
    # 해당 필드를 빈 문자열로 만들어 누락 상황을 재현
    _write_loop_state(tmp_path, _approved_loop_state(**{missing_field: ""}))
    # gh CLI / acceptance_request는 정상이지만 필드 누락이 우선 차단되어야 함
    monkeypatch.setattr(pipeline, "_get_current_pr_head_sha", lambda: "SHA1")
    monkeypatch.setattr(
        pipeline,
        "_load_acceptance_request",
        lambda: {"packet_sha256": "pkt1", "pr_body_sha256": "body1"},
    )
    result = pipeline._check_codex_review_gate(_PIPELINE_ID, {})
    assert result["status"] == "BLOCKED"
    assert result["failure_code"] == "codex_review_stale"
    assert missing_field in result["message"]


# ---------------------------------------------------------------------------
# 9-f. gh CLI로 head SHA를 못 얻으면 BLOCKED (fail-closed)
# ---------------------------------------------------------------------------
def test_check_gate_gh_cli_unavailable_blocked(tmp_path, monkeypatch):
    """gh CLI 없음/실패로 current head SHA가 None이면 SKIP이 아니라 BLOCKED."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "pipeline_state.json"))
    _write_loop_state(tmp_path, _approved_loop_state())
    monkeypatch.setattr(pipeline, "_get_current_pr_head_sha", lambda: None)
    monkeypatch.setattr(
        pipeline,
        "_load_acceptance_request",
        lambda: {"packet_sha256": "pkt1", "pr_body_sha256": "body1"},
    )
    result = pipeline._check_codex_review_gate(_PIPELINE_ID, {})
    assert result["status"] == "BLOCKED"
    assert result["failure_code"] == "codex_review_stale"


# ---------------------------------------------------------------------------
# 9-g. acceptance_request.json이 없으면 BLOCKED (fail-closed)
# ---------------------------------------------------------------------------
def test_check_gate_acceptance_request_none_blocked(tmp_path, monkeypatch):
    """acceptance_request.json이 None이면 SKIP이 아니라 BLOCKED."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "pipeline_state.json"))
    _write_loop_state(tmp_path, _approved_loop_state())
    monkeypatch.setattr(pipeline, "_get_current_pr_head_sha", lambda: "SHA1")
    monkeypatch.setattr(pipeline, "_load_acceptance_request", lambda: None)
    result = pipeline._check_codex_review_gate(_PIPELINE_ID, {})
    assert result["status"] == "BLOCKED"
    assert result["failure_code"] == "codex_review_stale"


# ---------------------------------------------------------------------------
# 9-h. E2E: APPROVE_TO_USER → _record_state → _check_codex_review_gate PASS
# ---------------------------------------------------------------------------
def test_approve_to_check_gate_e2e(tmp_path, monkeypatch):
    """APPROVE_TO_USER → _record_state → _check_codex_review_gate PASS E2E (5개 필드 모두)."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "pipeline_state.json"))
    loop_path = tmp_path / ".pipeline" / "codex_review_loop_state.json"
    loop_path.parent.mkdir(parents=True, exist_ok=True)

    # 1단계: APPROVE 시 기록되는 5개 필드 시뮬레이션 (_record_state 직접 호출)
    cx._record_state(
        loop_path,
        {
            "pipeline_id": _PIPELINE_ID,
            "status": "APPROVED",
            "pr_head_sha": "HEADSHA1",
            "pr_body_sha256": "BODYSHA1",
            "packet_sha256": "PKTSHA1",
            "review_packet_sha256": "REVIEWPKT1",
            "accept_code": f"ACCEPT-{_PIPELINE_ID}",
            "approved_at": cx._now_iso(),
        },
    )

    # 2단계: _check_codex_review_gate가 PASS하는지 확인
    monkeypatch.setattr(pipeline, "_get_current_pr_head_sha", lambda: "HEADSHA1")
    monkeypatch.setattr(
        pipeline,
        "_load_acceptance_request",
        lambda: {"packet_sha256": "PKTSHA1", "pr_body_sha256": "BODYSHA1"},
    )
    result = pipeline._check_codex_review_gate(_PIPELINE_ID, {})
    assert result["status"] == "PASS"


# ---------------------------------------------------------------------------
# 10. APPROVE 출력에 CODEX 검토 필요 마커 없음
# ---------------------------------------------------------------------------
def test_approve_output_no_codex_marker():
    """APPROVE 출력에 'CODEX 검토 필요' 재트리거 마커가 없어야 한다."""
    out = cx.process_verdict("APPROVE_TO_USER", _PIPELINE_ID, _PR_URL, reject_count=0)
    assert out["decision"] == "APPROVE"
    assert "CODEX 검토 필요" not in out["output"]


# ---------------------------------------------------------------------------
# 11. APPROVE 출력에 nonce 미노출 (ACCEPT-pipeline_id 형식만)
# ---------------------------------------------------------------------------
def test_approve_output_no_nonce():
    """APPROVE 출력의 승인 코드는 ACCEPT-{pipeline_id} 형식이며 nonce가 없다."""
    out = cx.process_verdict("APPROVE_TO_USER", _PIPELINE_ID, _PR_URL, reject_count=0)
    assert f"ACCEPT-{_PIPELINE_ID}" in out["output"]
    # nonce 형식(ACCEPT-<pid>-<8자 base32>)이 노출되면 안 됨
    assert re.search(rf"ACCEPT-{re.escape(_PIPELINE_ID)}-[A-Z2-7]{{8}}", out["output"]) is None


# ---------------------------------------------------------------------------
# 12. hook이 gh pr comment / gates accept를 실행하지 않음
# ---------------------------------------------------------------------------
def test_hook_no_pr_comment_no_gates_accept():
    """hook 소스에 gh pr comment / gates accept 자동 실행 결합이 없어야 한다."""
    src = _HOOK_PATH.read_text(encoding="utf-8")
    # 'gh pr comment' subprocess 실행 결합 패턴 부재 (리스트 인자 형태)
    assert re.search(r'["\']pr["\']\s*,\s*["\']comment["\']', src) is None
    # gates accept subprocess 실행 결합 패턴 부재 (리스트 인자 형태)
    assert re.search(r'["\']gates["\']\s*,\s*["\']accept["\']', src) is None
    # acceptance_request.json 읽기/수정 금지: 코드 라인(주석/docstring 제외)에 등장하지 않음.
    code_lines = []
    in_doc = False
    for ln in src.splitlines():
        stripped = ln.lstrip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            # 단일 라인 docstring이 아니면 토글
            if stripped.count('"""') == 1 and stripped.count("'''") == 0:
                in_doc = not in_doc
            continue
        if in_doc:
            continue
        if stripped.startswith("#"):
            continue
        # 인라인 주석 제거 (단순화: # 이후 절단 — 문자열 내 # 오탐 가능하나 본 검사엔 충분)
        code_lines.append(ln.split("#", 1)[0])
    code_only = "\n".join(code_lines)
    assert "acceptance_request" not in code_only, (
        "코드에서 acceptance_request 참조 발견 — codex_review_loop_state.json만 사용해야 함"
    )
    # 실제 subprocess 호출은 gh(pr view/diff)와 codex(exec)에만 결합
    subprocess_calls = re.findall(r'subprocess\.run\(\s*\[\s*["\'](\w+)["\']', src)
    assert set(subprocess_calls) <= {"gh", "codex"}, (
        f"예상치 못한 subprocess 결합: {subprocess_calls}"
    )


# ---------------------------------------------------------------------------
# 13. oracle 케이스 일치 검증
# ---------------------------------------------------------------------------
def test_oracle_reject_normal_matches():
    """TC-REJECT-NORMAL oracle: REJECT 원문 출력 + exit 2 + no_prefix."""
    expected = json.loads(
        (_ORACLE_DIR / "TC-REJECT-NORMAL" / "expected.json").read_text(encoding="utf-8")
    )
    verdict = (_ORACLE_DIR / "TC-REJECT-NORMAL" / "input.txt").read_text(encoding="utf-8")
    out = cx.process_verdict(
        verdict, expected["pipeline_id"], _PR_URL, expected["reject_count"]
    )
    assert out["exit_code"] == expected["exit_code"]
    assert out["decision"] == expected["decision"]
    assert (out["output"] == verdict) == expected["output_equals_verdict"]
    assert (verdict in out["output"]) == expected["stdout_contains_reject_reason"]


def test_oracle_dedupe_edge_matches():
    """TC-DEDUPE-EDGE oracle: 같은 조합 반복 시 중복 차단."""
    expected = json.loads(
        (_ORACLE_DIR / "TC-DEDUPE-EDGE" / "expected.json").read_text(encoding="utf-8")
    )
    reason = (_ORACLE_DIR / "TC-DEDUPE-EDGE" / "input.txt").read_text(encoding="utf-8")
    prior = expected["prior_state"]
    is_dupe = cx._is_duplicate_reject(
        prior,
        prior["pipeline_id"],
        prior["pr_head_sha"],
        prior["packet_sha256"],
        reason,
    )
    assert is_dupe == expected["is_duplicate_reject"]
    assert expected["action"] == "dedupe_skip"
    assert expected["exit_code"] == 0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
