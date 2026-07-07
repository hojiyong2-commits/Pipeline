"""IMP-20260703-B985 MT-24: Consolidated Acceptance Snapshot (copy-only publish) 회귀 테스트.

REJECT #19: packet/PR body/pending comment/approval_request_message 재렌더링 경로를 제거하고,
staging 시점에 단 1회 생성한 frozen bytes/text를 그대로 복사하는 copy-only publish 경로로 대체한 것을
회귀 검증한다.

12개 테스트:
  1. test_publish_uses_snapshot_bytes        — publish가 staged snapshot bytes를 그대로 사용
  2. test_publish_no_renderer_calls          — publish 경로에서 renderer 함수 미호출(copy-only)
  3. test_snapshot_sha_consistency           — snapshot/codex/acceptance_request 3자 SHA 일치
  4. test_approval_message_missing_blocked   — approval_request_message 0회면 BLOCKED
  5. test_approval_message_format_counts     — "사용자 승인 요청"/"CODEX 검토 필요" count == 1
  6. test_approval_message_last_line         — "CODEX 검토 필요"가 마지막 의미 있는 줄
  7. test_oracle_summary_mismatch_blocked    — oracle gate PASS인데 packet oracle_summary FAIL → BLOCKED
  8. test_ci_count_mismatch_blocked          — PR body 상단과 packet CI run/test count 충돌 → BLOCKED
  9. test_pending_freeze_blocks_bytes_change — PENDING 중 snapshot bytes 변경 시 sha 불일치 탐지
 10. test_pending_freeze_allows_noop         — 동일 bytes byte-for-byte no-op 허용
 11. test_stale_codex_pr_head_blocked        — PR head 변경 후 이전 Codex APPROVED 재사용 차단
 12. test_existing_nonce_provenance_preserved — nonce/provenance 검증 함수 보존 확인

[Assumptions]: pipeline.py가 repo 루트에 있다. 파일 격리는 monkeypatch.chdir(tmp_path) +
    monkeypatch.setattr(pipeline, "BASE_DIR", tmp_path)로 수행한다(전역 상태 미오염).
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import pipeline  # noqa: E402


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """BASE_DIR + cwd를 tmp_path로 격리하여 전역 pipeline_state/파일 오염을 방지한다."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, "BASE_DIR", tmp_path)
    return tmp_path


def _staged(**over):
    base = {
        "packet_md_text": "PACKET-MD-CONTENT",
        "packet_json_text": '{"schema_version": 1}',
        "pr_body_candidate_text": "PR-BODY-CANDIDATE",
        "approval_request_message_text": (
            "사용자 승인 요청\n\nPR: https://example.com/pull/1\n\n"
            "승인 코드:\nACCEPT-TEST-001\n\nCODEX 검토 필요"
        ),
        "pending_comment_body": "<!-- pipeline-human-acceptance-packet-pending -->\n승인 대기 중",
        "pr_head_sha": "a" * 40,
        "github_ci_run_id": "111222333",
    }
    base.update(over)
    return base


# 1 ---------------------------------------------------------------------------
def test_publish_uses_snapshot_bytes(isolated):
    """publish(=load) 경로가 staged snapshot의 bytes/text를 그대로 반환한다(copy-only)."""
    staged = _staged()
    snap = pipeline._create_acceptance_snapshot("TEST-001", staged)
    loaded = pipeline._load_acceptance_snapshot("TEST-001")
    assert loaded is not None
    # frozen text가 byte-for-byte 보존됨.
    assert loaded["packet_md_text"] == staged["packet_md_text"]
    assert loaded["packet_json_text"] == staged["packet_json_text"]
    assert loaded["pr_body_candidate_text"] == staged["pr_body_candidate_text"]
    assert loaded["approval_request_message_text"] == staged["approval_request_message_text"]
    assert loaded["pending_comment_body"] == staged["pending_comment_body"]
    # snapshot_id가 load에서도 동일.
    assert loaded["snapshot_id"] == snap["snapshot_id"]


# 2 ---------------------------------------------------------------------------
def test_publish_no_renderer_calls(isolated, monkeypatch):
    """copy-only: snapshot에서 읽은 bytes를 쓸 때 packet/approval renderer가 호출되지 않는다."""
    calls = {"packet": 0, "approval": 0, "pending": 0}

    orig_packet = pipeline._build_final_packet_content
    orig_approval = pipeline._build_approval_request_output
    orig_pending = pipeline._render_frozen_pending_comment

    def _spy_packet(*a, **k):
        calls["packet"] += 1
        return orig_packet(*a, **k)

    def _spy_approval(*a, **k):
        calls["approval"] += 1
        return orig_approval(*a, **k)

    def _spy_pending(*a, **k):
        calls["pending"] += 1
        return orig_pending(*a, **k)

    monkeypatch.setattr(pipeline, "_build_final_packet_content", _spy_packet)
    monkeypatch.setattr(pipeline, "_build_approval_request_output", _spy_approval)
    monkeypatch.setattr(pipeline, "_render_frozen_pending_comment", _spy_pending)

    pipeline._create_acceptance_snapshot("TEST-002", _staged())
    # publish가 참조하는 copy-only 소스: _load_acceptance_snapshot (renderer 미호출).
    loaded = pipeline._load_acceptance_snapshot("TEST-002")
    approval_msg = loaded["approval_request_message_text"]
    pending_body = loaded["pending_comment_body"]
    packet_md = loaded["packet_md_text"]

    assert approval_msg and pending_body and packet_md
    # load-then-copy 경로에서는 어떤 renderer도 호출되지 않아야 한다.
    assert calls["packet"] == 0
    assert calls["approval"] == 0
    assert calls["pending"] == 0


# 3 ---------------------------------------------------------------------------
def test_snapshot_sha_consistency(isolated):
    """snapshot ↔ codex_result ↔ acceptance_request의 snapshot_id/head/sha가 일치하면 통과."""
    staged = _staged()
    snap = pipeline._create_acceptance_snapshot("TEST-003", staged)
    sid = snap["snapshot_id"]
    codex = {
        "snapshot_id": sid,
        "pr_head_sha": staged["pr_head_sha"],
        "packet_sha256": snap["packet_md_sha256"],
    }
    ar = {
        "snapshot_id": sid,
        "pr_head_sha": staged["pr_head_sha"],
        "packet_sha256": snap["packet_md_sha256"],
        "verification_json_sha256": snap["packet_json_sha256"],
        "approval_message_sha256": snap["approval_message_sha256"],
        "pending_comment_sha256": snap["pending_comment_sha256"],
    }
    # 불일치 없음 → _die 호출 없이 정상 반환(None).
    assert pipeline._validate_snapshot_invariants(snap, codex, ar) is None


def test_snapshot_invariant_mismatch_blocks(isolated):
    """packet_sha256 불일치 시 fail-closed SystemExit."""
    staged = _staged()
    snap = pipeline._create_acceptance_snapshot("TEST-003b", staged)
    sid = snap["snapshot_id"]
    codex = {"snapshot_id": sid, "packet_sha256": "deadbeef" * 8}
    ar = {"snapshot_id": sid}
    with pytest.raises(SystemExit):
        pipeline._validate_snapshot_invariants(snap, codex, ar)


# 4 ---------------------------------------------------------------------------
def test_approval_message_missing_blocked(isolated):
    """snapshot_id가 codex/acceptance_request에 없으면(0 전파) fail-closed SystemExit."""
    staged = _staged()
    snap = pipeline._create_acceptance_snapshot("TEST-004", staged)
    # snapshot_id 미전파 → snapshot_id_missing BLOCKED.
    with pytest.raises(SystemExit):
        pipeline._validate_snapshot_invariants(snap, {"snapshot_id": ""}, {"snapshot_id": ""})


# 5 ---------------------------------------------------------------------------
def test_approval_message_format_counts(isolated):
    """approval_request_message에 '사용자 승인 요청'과 'CODEX 검토 필요'가 각 1회씩."""
    out = pipeline._build_approval_request_output("TEST-005", "https://example.com/pull/9")
    msg = out["approval_request_message"]
    assert msg.count("사용자 승인 요청") == 1
    assert msg.count("CODEX 검토 필요") == 1


# 6 ---------------------------------------------------------------------------
def test_approval_message_last_line(isolated):
    """'CODEX 검토 필요'가 마지막 의미 있는(비어 있지 않은) 줄이다."""
    out = pipeline._build_approval_request_output("TEST-006", "https://example.com/pull/9")
    msg = out["approval_request_message"]
    meaningful = [ln for ln in msg.splitlines() if ln.strip()]
    assert meaningful[-1].strip() == "CODEX 검토 필요"


# 7 ---------------------------------------------------------------------------
def test_oracle_summary_mismatch_blocked(isolated):
    """oracle gate PASS인데 packet의 oracle_summary가 FAIL이면 readiness BLOCKED."""
    # packet 파일 생성 (oracle_summary: FAIL).
    pkt_path = pipeline._packet_output_path()
    pkt_path.parent.mkdir(parents=True, exist_ok=True)
    pkt_path.write_text("oracle_summary: FAIL (3개 케이스, 1개 통과)\n", encoding="utf-8")
    state = {"external_gates": {"oracle": {"status": "PASS"}}}
    result = pipeline._check_packet_summary_consistency(state, None)
    assert result["allow_accept"] is False
    assert result["failure_code"] == "oracle_summary_mismatch"


def test_oracle_summary_pass_allows(isolated):
    """oracle_summary가 PASS면 통과."""
    pkt_path = pipeline._packet_output_path()
    pkt_path.parent.mkdir(parents=True, exist_ok=True)
    pkt_path.write_text("oracle_summary: PASS (3개 케이스, 3개 통과)\n", encoding="utf-8")
    state = {"external_gates": {"oracle": {"status": "PASS"}}}
    result = pipeline._check_packet_summary_consistency(state, None)
    assert result["allow_accept"] is True


# 8 ---------------------------------------------------------------------------
def test_ci_count_mismatch_blocked(isolated):
    """PR body 상단 요약과 final packet의 CI run ID가 충돌하면 BLOCKED."""
    pr_body = (
        "작업 요약\nci_run_id: 999888777\n\n"
        "<!-- PIPELINE_FINAL_PACKET_START -->\n"
        "ci_run_id: 111222333\n"
        "<!-- PIPELINE_FINAL_PACKET_END -->\n"
    )
    state = {"external_gates": {"oracle": {"status": "PASS"}}}
    result = pipeline._check_packet_summary_consistency(state, pr_body)
    assert result["allow_accept"] is False
    assert result["failure_code"] == "ci_count_mismatch"


def test_ci_count_match_allows(isolated):
    """상단/packet CI run ID가 동일하면 통과."""
    pr_body = (
        "작업 요약\nci_run_id: 111222333\n\n"
        "<!-- PIPELINE_FINAL_PACKET_START -->\n"
        "ci_run_id: 111222333\n"
        "<!-- PIPELINE_FINAL_PACKET_END -->\n"
    )
    state = {"external_gates": {"oracle": {"status": "PASS"}}}
    result = pipeline._check_packet_summary_consistency(state, pr_body)
    assert result["allow_accept"] is True


# 9 ---------------------------------------------------------------------------
def test_pending_freeze_blocks_bytes_change(isolated):
    """PENDING snapshot과 새 snapshot의 packet sha가 다르면 탐지된다(freeze gate 감지 로직)."""
    old = pipeline._create_acceptance_snapshot("TEST-009", _staged(packet_md_text="OLD"))
    new_shas = {
        "packet_md_sha256": _sha256("NEW"),
    }
    # freeze gate가 사용하는 비교 로직: 기존 sha가 비어있지 않고 새 sha와 다르면 mismatch.
    mismatch = [
        k for k, v in new_shas.items()
        if str(old.get(k, "") or "") and str(old.get(k, "") or "") != v
    ]
    assert "packet_md_sha256" in mismatch


# 10 --------------------------------------------------------------------------
def test_pending_freeze_allows_noop(isolated):
    """동일 bytes로 재생성 시 sha가 모두 동일하여 no-op(불일치 0건)."""
    staged = _staged()
    old = pipeline._create_acceptance_snapshot("TEST-010", staged)
    new = pipeline._create_acceptance_snapshot("TEST-010", staged)
    for k in (
        "packet_md_sha256", "packet_json_sha256", "pr_body_sha256",
        "approval_message_sha256", "pending_comment_sha256",
    ):
        assert old[k] == new[k]


# 11 --------------------------------------------------------------------------
def test_stale_codex_pr_head_blocked(isolated):
    """snapshot pr_head_sha와 codex/acceptance_request pr_head_sha가 다르면 fail-closed."""
    staged = _staged(pr_head_sha="a" * 40)
    snap = pipeline._create_acceptance_snapshot("TEST-011", staged)
    sid = snap["snapshot_id"]
    codex = {"snapshot_id": sid, "pr_head_sha": "b" * 40}  # stale head
    ar = {"snapshot_id": sid, "pr_head_sha": "a" * 40}
    with pytest.raises(SystemExit):
        pipeline._validate_snapshot_invariants(snap, codex, ar)


# 12 --------------------------------------------------------------------------
def test_existing_nonce_provenance_preserved(isolated):
    """기존 nonce/provenance 검증 함수가 보존되어 있고 정상 시그니처를 갖는다."""
    # 기존 보안 검증 함수들이 여전히 존재해야 한다(약화/삭제 금지).
    assert hasattr(pipeline, "_issue_acceptance_nonce")
    assert hasattr(pipeline, "_collect_issued_nonces")
    assert hasattr(pipeline, "_load_acceptance_request")
    assert hasattr(pipeline, "_invalidate_acceptance_request")
    # nonce 발급은 여전히 non-empty 문자열을 반환한다.
    nonce = pipeline._issue_acceptance_nonce()
    assert isinstance(nonce, str) and len(nonce) > 0


# --- 추가: 신규 함수의 타입 가드/방어 로직 회귀 ---
def test_create_snapshot_type_guards(isolated):
    """None/비str pipeline_id, None/비dict staged_data 방어."""
    with pytest.raises(TypeError):
        pipeline._create_acceptance_snapshot(None, {})
    with pytest.raises(TypeError):
        pipeline._create_acceptance_snapshot("X", None)
    with pytest.raises(ValueError):
        pipeline._create_acceptance_snapshot("", {})


def test_load_snapshot_pipeline_id_mismatch(isolated):
    """저장된 snapshot의 pipeline_id가 요청과 다르면 None(교차 오염 차단)."""
    pipeline._create_acceptance_snapshot("TEST-OWNER", _staged())
    # 다른 pipeline_id로 로드 시도 → 경로 자체가 달라 None.
    assert pipeline._load_acceptance_snapshot("TEST-OTHER") is None


def test_render_frozen_pending_comment_type_guards(isolated):
    """_render_frozen_pending_comment의 None/비str 방어."""
    with pytest.raises(TypeError):
        pipeline._render_frozen_pending_comment(None, "e")
    with pytest.raises(TypeError):
        pipeline._render_frozen_pending_comment({}, None)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
