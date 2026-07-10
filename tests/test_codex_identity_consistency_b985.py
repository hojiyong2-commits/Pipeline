# [Purpose]: IMP-20260703-B985 MT-35 — _check_codex_identity_consistency와
#            _check_ci_head_sha_current 두 hard gate의 회귀 테스트. REJECT #28
#            (top-level ↔ snapshot_identity drift + 구 head CI run 참조) 재발 방지.
# [Assumptions]: pipeline.py가 import 가능하고 두 함수가 top-level 정의되어 있다.
# [Vulnerability & Risks]: 함수 시그니처 변경 시 이 테스트가 실패하여 회귀를 알린다.
# [Improvement]: 실제 request-accept 통합 흐름 E2E까지 확장하면 배선 검증이 강화된다.
"""_check_codex_identity_consistency / _check_ci_head_sha_current 단위 테스트 (MT-35).

Oracle:
  tests/oracles/IMP-20260703-B985/TC-35/expected_blocked_identity_mismatch.json
  tests/oracles/IMP-20260703-B985/TC-35/expected_blocked_ci_head_stale.json
  tests/oracles/IMP-20260703-B985/TC-35/expected_ok.json
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import (  # noqa: E402
    _check_codex_identity_consistency,
    _check_ci_head_sha_current,
)


def _full_identity() -> dict:
    """모든 필수/cross-check 필드가 채워진 snapshot_identity."""
    return {
        "snapshot_id": "snap_001",
        "pr_head_sha": "abc1234",
        "packet_sha256": "aaa",
        "pr_body_candidate_sha256": "bbb",
        "github_canonical_pr_body_sha256": "ccc",
        "approval_message_sha256": "ddd",
        "pending_comment_sha256": "eee",
    }


def _full_codex_result() -> dict:
    """top-level과 snapshot_identity가 완전히 일치하는 codex_review_result."""
    identity = _full_identity()
    result = dict(identity)
    result["snapshot_identity"] = identity
    return result


class TestCodexIdentityConsistency(unittest.TestCase):
    """_check_codex_identity_consistency 테스트."""

    def test_blocked_no_snapshot_identity(self) -> None:
        """snapshot_identity가 없으면 BLOCKED (edge)."""
        result = _check_codex_identity_consistency({})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "codex_snapshot_identity_missing")

    def test_blocked_snapshot_id_missing_in_identity(self) -> None:
        """snapshot_identity에 snapshot_id 없음 → BLOCKED (exception)."""
        identity = _full_identity()
        identity["snapshot_id"] = ""
        codex_result = {"snapshot_identity": identity}
        result = _check_codex_identity_consistency(codex_result)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "codex_identity_snapshot_id_missing")

    def test_blocked_github_canonical_missing_in_identity(self) -> None:
        """snapshot_identity에 github_canonical_pr_body_sha256 없음 → BLOCKED (exception)."""
        identity = _full_identity()
        identity["github_canonical_pr_body_sha256"] = ""
        codex_result = {"snapshot_identity": identity}
        result = _check_codex_identity_consistency(codex_result)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(
            result["failure_code"],
            "codex_identity_github_canonical_pr_body_sha256_missing",
        )

    def test_blocked_approval_message_sha_missing_in_identity(self) -> None:
        """snapshot_identity에 approval_message_sha256 없음 → BLOCKED (exception)."""
        identity = _full_identity()
        identity["approval_message_sha256"] = ""
        codex_result = {"snapshot_identity": identity}
        result = _check_codex_identity_consistency(codex_result)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(
            result["failure_code"], "codex_identity_approval_message_sha256_missing"
        )

    def test_blocked_pending_comment_sha_missing_in_identity(self) -> None:
        """snapshot_identity에 pending_comment_sha256 없음 → BLOCKED (exception)."""
        identity = _full_identity()
        identity["pending_comment_sha256"] = ""
        codex_result = {"snapshot_identity": identity}
        result = _check_codex_identity_consistency(codex_result)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(
            result["failure_code"], "codex_identity_pending_comment_sha256_missing"
        )

    def test_blocked_top_vs_identity_mismatch(self) -> None:
        """top-level pr_body_candidate_sha256 != snapshot_identity → BLOCKED (error).

        Oracle: expected_blocked_identity_mismatch.json
        """
        identity = _full_identity()
        codex_result = dict(identity)
        codex_result["snapshot_identity"] = identity
        # top-level만 다른 값으로 오염 (drift 시뮬레이션)
        codex_result["pr_body_candidate_sha256"] = "97d79813"
        result = _check_codex_identity_consistency(codex_result)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "codex_identity_field_mismatch")

    def test_ok_all_identity_consistent(self) -> None:
        """모든 필드가 top-level ↔ nested 일치하면 PASS (normal).

        Oracle: expected_ok.json
        """
        result = _check_codex_identity_consistency(_full_codex_result())
        self.assertEqual(result["status"], "PASS")


class TestCiHeadShaCurrent(unittest.TestCase):
    """_check_ci_head_sha_current 테스트."""

    def test_blocked_ci_head_sha_missing(self) -> None:
        """final packet에 ci_head_sha가 없으면 BLOCKED (edge)."""
        result = _check_ci_head_sha_current({}, "30c42160")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "ci_head_sha_missing")

    def test_blocked_ci_head_sha_stale(self) -> None:
        """packet ci_head가 현재 head와 다르면 BLOCKED (error).

        Oracle: expected_blocked_ci_head_stale.json
        """
        packet = {
            "github_actions": {
                "head_sha": "77e0ff9e5dcf1d94a1b4f1114358c4d1f98b1a22"
            }
        }
        result = _check_ci_head_sha_current(
            packet, "30c421603f6423272eff8ce2b6f4286d037e3bab"
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "ci_head_sha_stale")

    def test_ok_ci_head_sha_matches(self) -> None:
        """packet ci_head == 현재 head이면 PASS (normal).

        Oracle: expected_ok.json
        """
        packet = {
            "github_actions": {
                "head_sha": "30c421603f6423272eff8ce2b6f4286d037e3bab"
            }
        }
        result = _check_ci_head_sha_current(
            packet, "30c421603f6423272eff8ce2b6f4286d037e3bab"
        )
        self.assertEqual(result["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
