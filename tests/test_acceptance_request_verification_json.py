"""tests/test_acceptance_request_verification_json.py

IMP-20260605-58BF MT-3: _write_acceptance_request의 verification_json 필드 테스트.

검증 대상:
  - _write_acceptance_request 호출 시 verification_json_path / verification_json_sha256 포함
  - verification_json 파일 없을 때 None 필드로 graceful 처리
  - _cmd_gates_request_accept가 verification_json_path/sha256을 acceptance_request.json에 기록
"""
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import (  # type: ignore  # noqa: E402
    _write_acceptance_request,
    _load_acceptance_request,
    _build_verification_json,
    _write_verification_json,
)


def _make_vj_evidence(
    pipeline_id: str = "IMP-20260605-58BF",
    pr_head_sha: str = "abc1234",
    ci_run_id: str = "123456",
) -> Dict[str, Any]:
    """테스트용 verification_json evidence dict."""
    return {
        "pipeline_id": pipeline_id,
        "pr_url": "",
        "pr_number": "",
        "pr_head_sha": pr_head_sha,
        "ci_run_id": ci_run_id,
        "actions_url": "",
        "changed_files": ["pipeline.py"],
        "gate_status": {},
        "structured_ac": [],
        "ac_fulfillment_table": None,
        "acceptance_request": None,
        "generated_at": "2026-06-05T10:00:00Z",
    }


class TestWriteAcceptanceRequestVerificationJson(unittest.TestCase):
    """_write_acceptance_request의 verification_json 필드 테스트."""

    def test_normal_includes_vj_path_and_sha(self) -> None:
        """verification_json_path, verification_json_sha256가 acceptance_request.json에 기록된다 (normal)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                # verification_json 파일 생성
                vj_data = _build_verification_json(_make_vj_evidence())
                vj_path = _write_verification_json(vj_data)
                import hashlib
                vj_sha = hashlib.sha256(vj_path.read_bytes()).hexdigest()

                result = _write_acceptance_request(
                    pipeline_id="IMP-20260605-58BF",
                    evidence="test_evidence.md",
                    pr_url="",
                    pr_head_sha="abc1234",
                    ci_run_id="123456",
                    verification_json_path=str(vj_path),
                    verification_json_sha256=vj_sha,
                )
                self.assertEqual(result["verification_json_path"], str(vj_path))
                self.assertEqual(result["verification_json_sha256"], vj_sha)

                # 파일로도 확인
                loaded = _load_acceptance_request()
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertEqual(loaded["verification_json_path"], str(vj_path))
                self.assertEqual(loaded["verification_json_sha256"], vj_sha)
            finally:
                os.chdir(orig_cwd)

    def test_normal_none_when_no_vj(self) -> None:
        """verification_json_path/sha256 미제공 시 None이 기록된다 (normal)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                result = _write_acceptance_request(
                    pipeline_id="IMP-20260605-58BF",
                    evidence="test.md",
                    pr_url="",
                    pr_head_sha="abc1234",
                    ci_run_id="123456",
                )
                self.assertIsNone(result.get("verification_json_path"))
                self.assertIsNone(result.get("verification_json_sha256"))
            finally:
                os.chdir(orig_cwd)

    def test_edge_explicit_none_params(self) -> None:
        """verification_json_path=None, verification_json_sha256=None 명시 시 None 저장 (edge)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                result = _write_acceptance_request(
                    pipeline_id="IMP-20260605-58BF",
                    evidence="test.md",
                    pr_url="",
                    pr_head_sha="abc1234",
                    ci_run_id="123456",
                    verification_json_path=None,
                    verification_json_sha256=None,
                )
                self.assertIsNone(result["verification_json_path"])
                self.assertIsNone(result["verification_json_sha256"])
            finally:
                os.chdir(orig_cwd)

    def test_error_pipeline_id_empty(self) -> None:
        """pipeline_id가 빈 문자열이면 ValueError 발생 (error)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                with self.assertRaises(ValueError):
                    _write_acceptance_request(
                        pipeline_id="",
                        evidence="test.md",
                        pr_url="",
                        pr_head_sha="",
                        ci_run_id="",
                    )
            finally:
                os.chdir(orig_cwd)


class TestCmdGatesRequestAcceptRecordsVjPath(unittest.TestCase):
    """_cmd_gates_request_accept가 verification_json 경로를 acceptance_request.json에 기록하는지 확인."""

    def test_normal_records_vj_when_file_exists(self) -> None:
        """verification_json 파일이 있으면 acceptance_request.json에 경로와 SHA가 기록된다 (normal)."""
        import argparse
        from pipeline import _cmd_gates_request_accept  # type: ignore

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                # verification_json 파일 생성
                evidence_path = Path(tmpdir) / "test_evidence.md"
                evidence_path.write_text("# 테스트", encoding="utf-8")
                vj_data = _build_verification_json(_make_vj_evidence())
                vj_path = _write_verification_json(vj_data)

                state: Dict[str, Any] = {
                    "pipeline_id": "IMP-20260605-58BF",
                    "external_gates": {},
                }
                args = argparse.Namespace(
                    evidence=str(evidence_path),
                    force_new_code=False,
                )

                with patch("pipeline._get_pr_body_text", return_value=""):
                    with patch("pipeline._get_current_pr_url", return_value=""):
                        with patch("pipeline._get_current_pr_head_sha", return_value="abc1234"):
                            with patch("pipeline._get_pr_branch_ci_run_id", return_value="123456"):
                                with patch("pipeline._get_git_diff_files", return_value=["pipeline.py"]):
                                    with patch("pipeline._check_packet_freshness_against_actual", return_value=None):
                                        with patch("pipeline._update_github_acceptance_comment"):
                                            with patch("pipeline._build_ac_fulfillment_table", return_value=None):
                                                with patch("pipeline._auto_generate_final_packet_and_update_pr",
                                                           return_value={"packet_path": "p.md", "pr_body_updated": False, "json_path": str(vj_path)}):
                                                    with patch("pipeline._save"):
                                                        with patch("pipeline._log_event"):
                                                            _cmd_gates_request_accept(args, state)

                loaded = _load_acceptance_request()
                self.assertIsNotNone(loaded)
                assert loaded is not None
                # verification_json_path가 기록되었는지 확인
                self.assertIsNotNone(loaded.get("verification_json_path"))
                self.assertIsNotNone(loaded.get("verification_json_sha256"))
            finally:
                os.chdir(orig_cwd)

    def test_edge_records_none_when_no_vj_file(self) -> None:
        """verification_json 파일이 없으면 None이 기록된다 (edge)."""
        import argparse
        from pipeline import _cmd_gates_request_accept  # type: ignore

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                evidence_path = Path(tmpdir) / "test_evidence.md"
                evidence_path.write_text("# 테스트", encoding="utf-8")
                # verification_json 파일 없이 진행

                state: Dict[str, Any] = {
                    "pipeline_id": "IMP-20260605-58BF",
                    "external_gates": {},
                }
                args = argparse.Namespace(
                    evidence=str(evidence_path),
                    force_new_code=False,
                )

                with patch("pipeline._get_pr_body_text", return_value=""):
                    with patch("pipeline._get_current_pr_url", return_value=""):
                        with patch("pipeline._get_current_pr_head_sha", return_value="abc1234"):
                            with patch("pipeline._get_pr_branch_ci_run_id", return_value="123456"):
                                with patch("pipeline._get_git_diff_files", return_value=["pipeline.py"]):
                                    with patch("pipeline._check_packet_freshness_against_actual", return_value=None):
                                        with patch("pipeline._update_github_acceptance_comment"):
                                            with patch("pipeline._build_ac_fulfillment_table", return_value=None):
                                                with patch("pipeline._auto_generate_final_packet_and_update_pr",
                                                           return_value={"packet_path": "p.md", "pr_body_updated": False, "json_path": None}):
                                                    with patch("pipeline._save"):
                                                        with patch("pipeline._log_event"):
                                                            _cmd_gates_request_accept(args, state)

                loaded = _load_acceptance_request()
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertIsNone(loaded.get("verification_json_path"))
                self.assertIsNone(loaded.get("verification_json_sha256"))
            finally:
                os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
