"""tests/test_gates_accept_verification_json.py

IMP-20260605-58BF MT-4: _verify_verification_json_freshness 테스트.

검증 대상:
  - req에 vj 경로/SHA가 없으면 None (스킵)
  - vj 파일이 없으면 "verification_json_changed" 반환
  - vj SHA가 다르면 "verification_json_changed" 반환
  - vj SHA가 동일하고 changed_files도 일치하면 None (PASS)
  - vj SHA가 동일하지만 changed_files 불일치 시 "changed_files_mismatch_vs_verification_json"
"""
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import (  # type: ignore  # noqa: E402
    _verify_verification_json_freshness,
    _build_verification_json,
    _write_verification_json,
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_vj_file(tmpdir: Path, changed_files: Optional[list] = None) -> tuple:
    """verification_json 파일을 생성하고 (path, sha256) 반환."""
    evidence: Dict[str, Any] = {
        "pipeline_id": "IMP-20260605-58BF",
        "pr_url": "",
        "pr_number": "",
        "pr_head_sha": "abc1234",
        "ci_run_id": "123456",
        "actions_url": "",
        "changed_files": changed_files if changed_files is not None else ["pipeline.py"],
        "gate_status": {},
        "structured_ac": [],
        "ac_fulfillment_table": None,
        "acceptance_request": None,
        "generated_at": "2026-06-05T10:00:00Z",
    }
    vj_data = _build_verification_json(evidence)
    orig_cwd = os.getcwd()
    os.chdir(str(tmpdir))
    try:
        vj_path = _write_verification_json(vj_data)
    finally:
        os.chdir(orig_cwd)
    sha = _sha256_bytes(vj_path.read_bytes())
    return vj_path, sha


class TestVerifyVerificationJsonFreshness(unittest.TestCase):
    """_verify_verification_json_freshness 단위 테스트."""

    def test_normal_pass_when_sha_matches(self) -> None:
        """vj 파일과 SHA가 일치하고 changed_files도 일치하면 None (normal)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vj_path, vj_sha = _make_vj_file(Path(tmpdir), changed_files=["pipeline.py"])
            req = {
                "verification_json_path": str(vj_path),
                "verification_json_sha256": vj_sha,
            }
            with patch("pipeline._get_git_diff_files", return_value=["pipeline.py"]):
                result = _verify_verification_json_freshness(req)
            self.assertIsNone(result)

    def test_normal_skip_when_no_vj_fields(self) -> None:
        """req에 vj 경로/SHA가 없으면 None (스킵) (normal)."""
        req: Dict[str, Any] = {
            "pipeline_id": "IMP-20260605-58BF",
            "nonce": "ABCDEFGH",
        }
        result = _verify_verification_json_freshness(req)
        self.assertIsNone(result)

    def test_edge_returns_changed_when_file_missing(self) -> None:
        """vj 파일이 없으면 verification_json_changed 반환 (edge)."""
        req = {
            "verification_json_path": "/nonexistent/path/human_acceptance_packet.json",
            "verification_json_sha256": "aabbcc1122334455",
        }
        result = _verify_verification_json_freshness(req)
        self.assertEqual(result, "verification_json_changed")

    def test_edge_returns_changed_when_sha_differs(self) -> None:
        """vj 파일이 있지만 SHA가 다르면 verification_json_changed 반환 (edge)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vj_path, _ = _make_vj_file(Path(tmpdir), changed_files=["pipeline.py"])
            req = {
                "verification_json_path": str(vj_path),
                "verification_json_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            }
            result = _verify_verification_json_freshness(req)
            self.assertEqual(result, "verification_json_changed")

    def test_edge_returns_mismatch_when_changed_files_differ(self) -> None:
        """vj SHA는 일치하지만 changed_files가 실제 diff와 다르면 mismatch 반환 (edge)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # vj에는 pipeline.py만 기록
            vj_path, vj_sha = _make_vj_file(Path(tmpdir), changed_files=["pipeline.py"])
            req = {
                "verification_json_path": str(vj_path),
                "verification_json_sha256": vj_sha,
            }
            # 실제 PR diff에는 pipeline.py와 extra.py가 있음 → extra.py가 vj에 없으면 OK
            # 하지만 vj에 pipeline.py가 있는데 실제에 없으면 mismatch
            # 즉, vj의 changed_files 중 현재 diff에 없는 파일 = stale_file
            with patch("pipeline._get_git_diff_files", return_value=["extra.py"]):
                result = _verify_verification_json_freshness(req)
            # pipeline.py가 vj에 있는데 실제 diff(["extra.py"])에 없으므로 mismatch
            self.assertEqual(result, "changed_files_mismatch_vs_verification_json")

    def test_error_none_req(self) -> None:
        """req가 None이면 None 반환 (error)."""
        result = _verify_verification_json_freshness(None)  # type: ignore
        self.assertIsNone(result)

    def test_error_empty_req(self) -> None:
        """빈 dict req이면 None 반환 (스킵) (error)."""
        result = _verify_verification_json_freshness({})
        self.assertIsNone(result)

    def test_normal_skip_when_only_sha_field(self) -> None:
        """sha만 있고 path가 없으면 None 반환 (스킵) (normal)."""
        req = {
            "verification_json_sha256": "aabbcc",
        }
        result = _verify_verification_json_freshness(req)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
