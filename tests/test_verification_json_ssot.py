"""tests/test_verification_json_ssot.py

IMP-20260605-58BF MT-1: Verification JSON SSoT 테스트.

검증 대상:
  - _build_verification_json: evidence dict → verification JSON 변환
  - _write_verification_json: JSON 파일 쓰기
  - _packet_json_output_path: 파일 경로 반환
  - _cmd_report_final_packet: MD + JSON 동시 생성 확인
  - _auto_generate_final_packet_and_update_pr: json_path 반환 확인
"""
import json
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
    _build_verification_json,
    _write_verification_json,
    _packet_json_output_path,
    _load_verification_json,
)


def _make_evidence(
    pipeline_id: str = "IMP-20260605-58BF",
    pr_head_sha: str = "abc123def456",
    ci_run_id: str = "99999999",
    changed_files: Optional[list] = None,
    acceptance_request: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """테스트용 evidence dict 생성."""
    return {
        "pipeline_id": pipeline_id,
        "pr_url": "https://github.com/owner/repo/pull/42",
        "pr_number": "42",
        "pr_head_sha": pr_head_sha,
        "ci_run_id": ci_run_id,
        "actions_url": f"https://github.com/owner/repo/actions/runs/{ci_run_id}",
        "changed_files": changed_files or ["pipeline.py", "tests/test_foo.py"],
        "gate_status": {"technical": "PASS", "oracle": "PASS"},
        "structured_ac": [],
        "ac_fulfillment_table": None,
        "acceptance_request": acceptance_request,
        "generated_at": "2026-06-05T10:00:00Z",
    }


class TestBuildVerificationJson(unittest.TestCase):
    """_build_verification_json 단위 테스트."""

    def test_normal_basic_fields(self) -> None:
        """기본 필드가 올바르게 매핑된다 (normal)."""
        evidence = _make_evidence()
        result = _build_verification_json(evidence)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["pipeline_id"], "IMP-20260605-58BF")
        self.assertEqual(result["pr_head_sha"], "abc123def456")
        self.assertEqual(result["ci_run_id"], "99999999")
        self.assertIn("pipeline.py", result["changed_files"])
        self.assertIsNone(result["acceptance_code"])

    def test_normal_acceptance_code_included(self) -> None:
        """acceptance_request에 nonce가 있으면 acceptance_code가 포함된다 (normal)."""
        evidence = _make_evidence(
            acceptance_request={"nonce": "ABCDEFGH", "status": "PENDING"}
        )
        result = _build_verification_json(evidence)
        self.assertEqual(result["acceptance_code"], "ACCEPT-IMP-20260605-58BF-ABCDEFGH")

    def test_edge_empty_changed_files(self) -> None:
        """evidence에 빈 changed_files가 전달되면 빈 리스트로 반환된다 (edge)."""
        # _build_verification_json은 evidence dict를 그대로 매핑하므로
        # evidence["changed_files"]가 명시적으로 빈 리스트이면 결과도 빈 리스트여야 한다.
        evidence: Dict[str, Any] = {
            "pipeline_id": "IMP-20260605-58BF",
            "pr_url": "",
            "pr_number": "",
            "pr_head_sha": "abc123",
            "ci_run_id": "99999",
            "actions_url": "",
            "changed_files": [],
            "gate_status": {},
            "structured_ac": [],
            "ac_fulfillment_table": None,
            "acceptance_request": None,
            "generated_at": "2026-06-05T10:00:00Z",
        }
        result = _build_verification_json(evidence)
        self.assertEqual(result["changed_files"], [])

    def test_edge_none_acceptance_request(self) -> None:
        """acceptance_request가 None이면 acceptance_code도 None (edge)."""
        evidence = _make_evidence(acceptance_request=None)
        result = _build_verification_json(evidence)
        self.assertIsNone(result["acceptance_code"])

    def test_error_nonce_missing_in_request(self) -> None:
        """acceptance_request에 nonce 키가 없으면 acceptance_code는 None (error)."""
        evidence = _make_evidence(acceptance_request={"status": "PENDING"})
        result = _build_verification_json(evidence)
        self.assertIsNone(result["acceptance_code"])


class TestWriteVerificationJson(unittest.TestCase):
    """_write_verification_json 단위 테스트."""

    def test_normal_writes_json_file(self) -> None:
        """JSON 파일이 현재 cwd에 기록된다 (normal)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                evidence = _make_evidence()
                verification_json = _build_verification_json(evidence)
                path = _write_verification_json(verification_json)
                self.assertTrue(path.exists())
                loaded = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(loaded["pipeline_id"], "IMP-20260605-58BF")
                self.assertEqual(loaded["schema_version"], 1)
            finally:
                os.chdir(orig_cwd)

    def test_normal_json_path_filename(self) -> None:
        """파일명이 human_acceptance_packet.json이다 (normal)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                path = _packet_json_output_path()
                self.assertEqual(path.name, "human_acceptance_packet.json")
            finally:
                os.chdir(orig_cwd)

    def test_edge_load_roundtrip(self) -> None:
        """write → load 왕복이 동일 데이터를 반환한다 (edge)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                evidence = _make_evidence(
                    acceptance_request={"nonce": "ZZZZZZZZ", "status": "PENDING"}
                )
                vj = _build_verification_json(evidence)
                _write_verification_json(vj)
                loaded = _load_verification_json()
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertEqual(loaded["acceptance_code"], "ACCEPT-IMP-20260605-58BF-ZZZZZZZZ")
            finally:
                os.chdir(orig_cwd)

    def test_error_load_missing_file(self) -> None:
        """파일이 없으면 _load_verification_json은 None을 반환한다 (error)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                result = _load_verification_json()
                self.assertIsNone(result)
            finally:
                os.chdir(orig_cwd)


class TestAutoGeneratePacket(unittest.TestCase):
    """_auto_generate_final_packet_and_update_pr json_path 반환 테스트."""

    def test_normal_returns_json_path(self) -> None:
        """_auto_generate_final_packet_and_update_pr가 json_path를 반환한다 (normal)."""
        from pipeline import _auto_generate_final_packet_and_update_pr  # type: ignore

        state = {"pipeline_id": "IMP-20260605-58BF", "external_gates": {}}
        acceptance_request = {"nonce": "ABCDEFGH", "status": "PENDING", "pipeline_id": "IMP-20260605-58BF"}

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with patch("pipeline.shutil.which", return_value=None):
                    with patch("pipeline._collect_packet_evidence") as mock_ev:
                        mock_ev.return_value = _make_evidence(
                            acceptance_request=acceptance_request
                        )
                        result = _auto_generate_final_packet_and_update_pr(
                            state, acceptance_request
                        )
                self.assertIn("json_path", result)
                self.assertIsNotNone(result["json_path"])
            finally:
                os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
