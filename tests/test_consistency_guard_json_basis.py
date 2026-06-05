"""tests/test_consistency_guard_json_basis.py

IMP-20260605-58BF MT-2: _check_protocol_consistency JSON-basis 검사 테스트.

검증 대상:
  - verification_json 제공 시 D 검사(changed_files_mismatch_vs_verification_json)가
    JSON changed_files 기준으로 동작한다.
  - verification_json 제공 시 F 검사(stale_file_description)가
    JSON changed_files 기준으로 동작한다.
  - verification_json 미제공 시 기존 텍스트 파싱 경로로 fallback된다.
  - _run_protocol_consistency_inline 이 _load_verification_json()를 호출하고
    그 결과를 _check_protocol_consistency에 전달한다.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
import unittest
from unittest.mock import patch, MagicMock, call

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import (  # type: ignore  # noqa: E402
    _check_protocol_consistency,
    _load_verification_json,
    _write_verification_json,
    _build_verification_json,
)


def _make_vj(
    changed_files: Optional[List[str]] = None,
    pipeline_id: str = "IMP-20260605-58BF",
    pr_head_sha: str = "abc1234",
    ci_run_id: str = "123456",
) -> Dict[str, Any]:
    """테스트용 verification_json dict 생성."""
    return {
        "schema_version": 1,
        "pipeline_id": pipeline_id,
        "generated_at": "2026-06-05T00:00:00Z",
        "pr_url": "https://github.com/owner/repo/pull/1",
        "pr_number": "1",
        "pr_head_sha": pr_head_sha,
        "ci_run_id": ci_run_id,
        "actions_url": f"https://github.com/owner/repo/actions/runs/{ci_run_id}",
        "changed_files": changed_files if changed_files is not None else ["src/module.py"],
        "gate_status": {"technical": "PASS", "oracle": "PASS"},
        "structured_ac": [],
        "ac_fulfillment_table": None,
        "acceptance_code": None,
    }


def _base_args(
    pr_head_sha: str = "abc1234",
    ci_run_id: str = "123456",
    changed_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """_check_protocol_consistency 기본 호출 인자 (gh 의존성 없는 최소 셋).

    Note: trust-root 파일(pipeline.py)은 pr_body에 언급해야 E 검사가 통과한다.
    테스트에서 pipeline.py를 pr_changed_files에 포함 시 pr_body에도 반드시 언급.
    """
    files = changed_files if changed_files is not None else ["src/module.py"]
    # trust-root 파일이 있으면 pr_body에 언급
    trust_root_mentions = ""
    for f in files:
        base = f.rsplit("/", 1)[-1]
        if base in {"pipeline.py", "CLAUDE.md"} or ".github/workflows" in f or ".claude/agents" in f:
            trust_root_mentions += f"\n{f} 변경: 기능 추가"
    return {
        "pr_body": (
            f"head_sha: {pr_head_sha}\n"
            f"run_id: {ci_run_id}\n"
            "테스트 통과: 10개\n"
            + trust_root_mentions
        ),
        "acceptance_packet_body": (
            f"head_sha: {pr_head_sha}\n"
            f"run_id: {ci_run_id}\n"
            "테스트 통과: 10개\n"
        ),
        "pr_changed_files": files,
        "pr_head_sha": pr_head_sha,
        "latest_ci_run_id": ci_run_id,
        "latest_ci_run_conclusion": "success",
    }


class TestCheckProtocolConsistencyJsonBasis(unittest.TestCase):
    """_check_protocol_consistency에 verification_json을 제공할 때의 동작 테스트."""

    def test_normal_json_d_check_pass_when_files_match(self) -> None:
        """verification_json.changed_files가 PR diff와 일치하면 D 검사 PASS (normal)."""
        vj = _make_vj(changed_files=["src/module.py"])
        args = _base_args(changed_files=["src/module.py"])
        result = _check_protocol_consistency(**args, verification_json=vj)
        # D/F 검사 관련 failure_code가 없어야 함
        self.assertTrue(result.get("allow_accept", False))
        self.assertNotEqual(result.get("failure_code"), "changed_files_mismatch_vs_verification_json")

    def test_normal_json_f_check_stale_listed_in_vj_blocked(self) -> None:
        """vj에 stale 파일이 있고 실제 diff에 없으면 F 검사 BLOCKED (normal)."""
        # vj에 stale_module.py가 포함되어 있지만 실제 pr_changed_files에는 없음
        vj = _make_vj(changed_files=["src/module.py", "stale_module.py"])
        args = _base_args(changed_files=["src/module.py"])
        result = _check_protocol_consistency(**args, verification_json=vj)
        self.assertFalse(result.get("allow_accept", True))
        self.assertEqual(result.get("failure_code"), "stale_file_description")

    def test_edge_json_d_check_extra_trust_root_in_pr_diff(self) -> None:
        """vj에 없는 trust-root 파일(pipeline.py)이 pr_changed_files에 있으면 D 검사 BLOCKED (edge)."""
        # vj에는 src/module.py만, 실제 diff에는 pipeline.py 추가
        vj = _make_vj(changed_files=["src/module.py"])
        # pipeline.py가 pr_changed_files에 있으므로 pr_body에도 언급 필요
        args = _base_args(changed_files=["src/module.py", "pipeline.py"])
        result = _check_protocol_consistency(**args, verification_json=vj)
        # pipeline.py는 trust-root 파일이므로 D 검사에서 BLOCKED
        self.assertFalse(result.get("allow_accept", True))
        self.assertEqual(result.get("failure_code"), "changed_files_mismatch_vs_verification_json")

    def test_edge_json_fallback_when_no_vj(self) -> None:
        """verification_json 미제공 시 텍스트 파싱 경로로 fallback (edge)."""
        args = _base_args(changed_files=["src/module.py"])
        result = _check_protocol_consistency(**args, verification_json=None)
        # fallback 경로에서 changed_files_mismatch_vs_verification_json failure_code 없음
        self.assertNotEqual(result.get("failure_code"), "changed_files_mismatch_vs_verification_json")

    def test_error_json_d_check_empty_changed_files(self) -> None:
        """vj.changed_files가 빈 리스트이고 pr_changed_files도 비어있으면 PASS (error/edge)."""
        vj = _make_vj(changed_files=[])
        args = _base_args(changed_files=[])
        result = _check_protocol_consistency(**args, verification_json=vj)
        # 빈 리스트 vs 빈 리스트 → D/F 검사 PASS
        self.assertNotEqual(result.get("failure_code"), "changed_files_mismatch_vs_verification_json")
        self.assertNotEqual(result.get("failure_code"), "stale_file_description")


class TestRunProtocolConsistencyPassesVj(unittest.TestCase):
    """_run_protocol_consistency_check 및 _run_protocol_consistency_inline이
    _load_verification_json을 호출하고 결과를 _check_protocol_consistency에 전달하는지 확인."""

    def test_normal_run_check_loads_vj(self) -> None:
        """_run_protocol_consistency_check가 _load_verification_json을 호출한다 (normal)."""
        import argparse
        from pipeline import _run_protocol_consistency_check  # type: ignore

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                state: Dict[str, Any] = {"pipeline_id": "IMP-20260605-58BF"}
                args = argparse.Namespace(repo="owner/repo", pr="1", dry_run=False)
                # _collect_pr_consistency_data를 패치하여 gh CLI 불필요하게 만듦
                collected_ok = {
                    "ok": True,
                    "pr_body": "head_sha: abc1234\nrun_id: 123456\n",
                    "acceptance_packet_body": "head_sha: abc1234\nrun_id: 123456\n",
                    "pr_changed_files": ["src/module.py"],
                    "pr_head_sha": "abc1234",
                    "latest_ci_run_id": "123456",
                    "latest_ci_run_conclusion": "success",
                }
                with patch("pipeline._collect_pr_consistency_data", return_value=collected_ok):
                    with patch("pipeline._write_consistency_result"):
                        with patch("pipeline._load_verification_json", return_value=None) as mock_load:
                            try:
                                _run_protocol_consistency_check(state, args, "IMP-20260605-58BF")
                            except SystemExit:
                                pass
                            mock_load.assert_called()
            finally:
                os.chdir(orig_cwd)

    def test_normal_inline_loads_vj(self) -> None:
        """_run_protocol_consistency_inline이 _load_verification_json을 호출한다 (normal)."""
        from pipeline import _run_protocol_consistency_inline  # type: ignore

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                state: Dict[str, Any] = {"pipeline_id": "IMP-20260605-58BF"}
                collected_ok = {
                    "ok": True,
                    "pr_body": "head_sha: abc1234\nrun_id: 123456\n",
                    "acceptance_packet_body": "head_sha: abc1234\nrun_id: 123456\n",
                    "pr_changed_files": ["src/module.py"],
                    "pr_head_sha": "abc1234",
                    "latest_ci_run_id": "123456",
                    "latest_ci_run_conclusion": "success",
                }
                with patch("pipeline._collect_pr_consistency_data", return_value=collected_ok):
                    with patch("pipeline._load_verification_json", return_value=None) as mock_load:
                        with patch("pipeline._check_protocol_consistency",
                                   return_value={"allow_accept": True}) as mock_check:
                            _run_protocol_consistency_inline(
                                state, "owner/repo", "1", "IMP-20260605-58BF"
                            )
                            mock_load.assert_called()
            finally:
                os.chdir(orig_cwd)

    def test_edge_vj_passed_to_check(self) -> None:
        """_run_protocol_consistency_inline이 로드된 vj를 _check_protocol_consistency에 전달한다 (edge)."""
        from pipeline import _run_protocol_consistency_inline  # type: ignore

        vj_data = _make_vj(changed_files=["src/module.py"])

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                state: Dict[str, Any] = {"pipeline_id": "IMP-20260605-58BF"}
                collected_ok = {
                    "ok": True,
                    "pr_body": "head_sha: abc1234\nrun_id: 123456\n",
                    "acceptance_packet_body": "head_sha: abc1234\nrun_id: 123456\n",
                    "pr_changed_files": ["src/module.py"],
                    "pr_head_sha": "abc1234",
                    "latest_ci_run_id": "123456",
                    "latest_ci_run_conclusion": "success",
                }
                with patch("pipeline._collect_pr_consistency_data", return_value=collected_ok):
                    with patch("pipeline._load_verification_json", return_value=vj_data):
                        with patch("pipeline._check_protocol_consistency") as mock_check:
                            mock_check.return_value = {"allow_accept": True}
                            _run_protocol_consistency_inline(
                                state, "owner/repo", "1", "IMP-20260605-58BF"
                            )
                            # verification_json=vj_data가 전달되었는지 확인
                            call_kwargs = mock_check.call_args
                            self.assertIsNotNone(call_kwargs)
                            # keyword argument로 전달된 verification_json 확인
                            if call_kwargs.kwargs:
                                passed_vj = call_kwargs.kwargs.get("verification_json")
                            else:
                                # positional args의 마지막 인자
                                passed_vj = call_kwargs.args[-1] if call_kwargs.args else None
                            self.assertEqual(passed_vj, vj_data)
            finally:
                os.chdir(orig_cwd)

    def test_error_inline_ok_false_returns_early(self) -> None:
        """_collect_pr_consistency_data가 ok=False이면 early return된다 (error)."""
        from pipeline import _run_protocol_consistency_inline  # type: ignore

        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                state: Dict[str, Any] = {"pipeline_id": "IMP-20260605-58BF"}
                with patch("pipeline._collect_pr_consistency_data") as mock_collect:
                    mock_collect.return_value = {
                        "ok": False,
                        "result": {"allow_accept": True, "status": "PASS"},
                    }
                    with patch("pipeline._check_protocol_consistency") as mock_check:
                        result = _run_protocol_consistency_inline(
                            state, "owner/repo", "1", "IMP-20260605-58BF"
                        )
                        # ok=False 이면 early return이므로 _check가 호출되지 않아야 함
                        mock_check.assert_not_called()
                        self.assertTrue(result.get("allow_accept", False))
            finally:
                os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
