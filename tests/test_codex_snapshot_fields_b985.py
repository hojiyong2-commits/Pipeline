"""
MT-33 회귀 테스트: codex_review_result snapshot 필드 검증
_check_codex_snapshot_fields 함수의 4개 required fields 존재 및 일치 검증.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import _check_codex_snapshot_fields


def _make_full_codex_result():
    return {
        "snapshot_id": "abc123",
        "pr_head_sha": "deadbeef",
        "packet_sha256": "aaa111",
        "pr_body_candidate_sha256": "bbb222",
        "github_canonical_pr_body_sha256": "ccc333",
        "approval_message_sha256": "ddd444",
        "pending_comment_sha256": "eee555",
    }


def _make_full_acceptance_request():
    return {
        "snapshot_id": "abc123",
        "github_canonical_pr_body_sha256": "ccc333",
        "approval_message_sha256": "ddd444",
        "pending_comment_sha256": "eee555",
    }


def test_blocked_missing_snapshot_id():
    codex = _make_full_codex_result()
    del codex["snapshot_id"]
    result = _check_codex_snapshot_fields({}, codex, None)
    assert result["status"] == "BLOCKED"
    assert "codex_snapshot_snapshot_id_missing" in result["failure_code"]


def test_blocked_empty_github_canonical_sha():
    codex = _make_full_codex_result()
    codex["github_canonical_pr_body_sha256"] = ""
    result = _check_codex_snapshot_fields({}, codex, None)
    assert result["status"] == "BLOCKED"
    assert "codex_snapshot_github_canonical_pr_body_sha256_missing" in result["failure_code"]


def test_blocked_missing_approval_message_sha():
    codex = _make_full_codex_result()
    del codex["approval_message_sha256"]
    result = _check_codex_snapshot_fields({}, codex, None)
    assert result["status"] == "BLOCKED"
    assert "codex_snapshot_approval_message_sha256_missing" in result["failure_code"]


def test_blocked_missing_pending_comment_sha():
    codex = _make_full_codex_result()
    del codex["pending_comment_sha256"]
    result = _check_codex_snapshot_fields({}, codex, None)
    assert result["status"] == "BLOCKED"
    assert "codex_snapshot_pending_comment_sha256_missing" in result["failure_code"]


def test_blocked_snapshot_id_mismatch():
    codex = _make_full_codex_result()
    req = _make_full_acceptance_request()
    req["snapshot_id"] = "DIFFERENT_VALUE"
    result = _check_codex_snapshot_fields({}, codex, req)
    assert result["status"] == "BLOCKED"
    assert "codex_snapshot_snapshot_id_mismatch" in result["failure_code"]


def test_blocked_github_canonical_sha_mismatch():
    codex = _make_full_codex_result()
    req = _make_full_acceptance_request()
    req["github_canonical_pr_body_sha256"] = "DIFFERENT_SHA"
    result = _check_codex_snapshot_fields({}, codex, req)
    assert result["status"] == "BLOCKED"
    assert "codex_snapshot_github_canonical_pr_body_sha256_mismatch" in result["failure_code"]


def test_ok_all_fields_present_and_match():
    codex = _make_full_codex_result()
    req = _make_full_acceptance_request()
    result = _check_codex_snapshot_fields({}, codex, req)
    assert result["status"] == "PASS"


def test_ok_no_acceptance_request():
    codex = _make_full_codex_result()
    result = _check_codex_snapshot_fields({}, codex, None)
    assert result["status"] == "PASS"
