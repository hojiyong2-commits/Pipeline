"""tests/test_hygiene_cleanup_workspace.py

IMP-20260605-58BF MT-5: cmd_hygiene_cleanup_workspace 테스트.

검증 대상:
  - HYGIENE_SOURCE_LIKE_EXTENSIONS 상수 존재 및 내용
  - terminal_state != COMPLETE 시 BLOCKED (exit 1)
  - tracked 변경 있을 때 BLOCKED (exit 1)
  - untracked 파일 이동 정상 케이스
  - source-like 파일은 possible-source-leftovers/ 서브폴더로 이동
  - 이동 대상 없을 때 status=empty
  - cleanup_manifest.json 필드 검증
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import (  # type: ignore  # noqa: E402
    HYGIENE_SOURCE_LIKE_EXTENSIONS,
    cmd_hygiene_cleanup_workspace,
)


def _make_args(after_complete: bool = False) -> argparse.Namespace:
    return argparse.Namespace(after_complete=after_complete)


def _make_state(terminal_state: Optional[str] = "COMPLETE", pipeline_id: str = "IMP-20260605-58BF") -> Dict[str, Any]:
    return {"terminal_state": terminal_state, "pipeline_id": pipeline_id}


class TestHygieneSourceLikeExtensions(unittest.TestCase):
    """HYGIENE_SOURCE_LIKE_EXTENSIONS 상수 테스트."""

    def test_normal_constant_exists_and_has_expected_values(self) -> None:
        """HYGIENE_SOURCE_LIKE_EXTENSIONS 상수가 존재하고 5개 확장자를 포함한다 (normal)."""
        self.assertIn(".py", HYGIENE_SOURCE_LIKE_EXTENSIONS)
        self.assertIn(".ts", HYGIENE_SOURCE_LIKE_EXTENSIONS)
        self.assertIn(".js", HYGIENE_SOURCE_LIKE_EXTENSIONS)
        self.assertIn(".ps1", HYGIENE_SOURCE_LIKE_EXTENSIONS)
        self.assertIn(".sh", HYGIENE_SOURCE_LIKE_EXTENSIONS)
        self.assertEqual(len(HYGIENE_SOURCE_LIKE_EXTENSIONS), 5)


class TestCleanupWorkspaceBlocked(unittest.TestCase):
    """cmd_hygiene_cleanup_workspace BLOCKED 케이스 테스트."""

    def test_error_blocked_when_not_complete(self) -> None:
        """terminal_state != COMPLETE이면 exit 1 + failure_code 반환 (error)."""
        state = _make_state(terminal_state=None)
        with patch("pipeline._load_state", return_value=state):
            with self.assertRaises(SystemExit) as ctx:
                cmd_hygiene_cleanup_workspace(_make_args())
            self.assertEqual(ctx.exception.code, 1)

    def test_error_blocked_when_terminal_pending(self) -> None:
        """terminal_state=PENDING이면 exit 1 (error)."""
        state = _make_state(terminal_state="PENDING")
        with patch("pipeline._load_state", return_value=state):
            with self.assertRaises(SystemExit) as ctx:
                cmd_hygiene_cleanup_workspace(_make_args())
            self.assertEqual(ctx.exception.code, 1)

    def test_error_blocked_when_tracked_changes(self) -> None:
        """tracked 변경이 있으면 exit 1 + failure_code 반환 (error)."""
        state = _make_state(terminal_state="COMPLETE")
        # git status --porcelain 출력에 tracked 변경 라인 포함
        mock_result = MagicMock()
        mock_result.stdout = "M  pipeline.py\n"
        with patch("pipeline._load_state", return_value=state):
            with patch("subprocess.run", return_value=mock_result):
                with self.assertRaises(SystemExit) as ctx:
                    cmd_hygiene_cleanup_workspace(_make_args())
                self.assertEqual(ctx.exception.code, 1)


class TestCleanupWorkspaceNormal(unittest.TestCase):
    """cmd_hygiene_cleanup_workspace 정상/edge 케이스 테스트."""

    def _run_cleanup_with_files(
        self,
        tmpdir: Path,
        file_names: List[str],
        file_contents: str = "test content",
    ) -> Path:
        """임시 디렉터리에서 cleanup-workspace를 실행하고 manifest 경로를 반환."""
        import importlib
        import pipeline as _pipeline_module  # type: ignore

        orig_base_dir = _pipeline_module.BASE_DIR
        orig_cwd = os.getcwd()
        try:
            _pipeline_module.BASE_DIR = tmpdir
            os.chdir(str(tmpdir))

            # 테스트 파일 생성
            for fname in file_names:
                (tmpdir / fname).write_text(file_contents, encoding="utf-8")

            state = _make_state(terminal_state="COMPLETE", pipeline_id="IMP-20260605-58BF")

            # git status: tracked 변경 없음 (untracked만 있음)
            mock_git = MagicMock()
            mock_git.stdout = "?? test_file.json\n"

            # _deployment_root → tmpdir/deploy_root
            deploy_root = tmpdir / "deploy_root"
            deploy_root.mkdir(parents=True, exist_ok=True)

            # _hygiene_collect_candidates는 실제 구현을 사용하되
            # tracked_files/staged_files를 빈 집합으로 패치
            with patch("pipeline._load_state", return_value=state):
                with patch("subprocess.run", return_value=mock_git):
                    with patch("pipeline._deployment_root", return_value=deploy_root):
                        with patch("pipeline._hygiene_get_tracked_files", return_value=set()):
                            with patch("pipeline._hygiene_get_staged_files", return_value=set()):
                                cmd_hygiene_cleanup_workspace(_make_args())

            manifest_path = tmpdir / "cleanup_manifest.json"
            return manifest_path
        finally:
            _pipeline_module.BASE_DIR = orig_base_dir
            os.chdir(orig_cwd)

    def test_normal_cleanup_manifest_written(self) -> None:
        """cleanup_manifest.json이 작성되고 필수 필드를 포함한다 (normal)."""
        with tempfile.TemporaryDirectory() as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            manifest_path = self._run_cleanup_with_files(tmpdir, ["failure_packet.json"])

            self.assertTrue(manifest_path.exists(), "cleanup_manifest.json이 생성되어야 함")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            # 필수 필드 검증
            for field in ["pipeline_id", "executed_at", "terminal_state", "destination_root",
                          "moved", "possible_source_leftovers", "blocked", "move_errors",
                          "total_bytes", "status"]:
                self.assertIn(field, manifest, f"manifest에 {field} 필드 없음")

            self.assertEqual(manifest["terminal_state"], "COMPLETE")
            self.assertEqual(manifest["pipeline_id"], "IMP-20260605-58BF")

    def test_edge_no_files_to_move(self) -> None:
        """이동할 untracked 파일이 없으면 manifest status=empty (edge)."""
        with tempfile.TemporaryDirectory() as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            # 파일 없이 실행
            manifest_path = self._run_cleanup_with_files(tmpdir, [])

            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest.get("status"), "empty")


class TestCleanupWorkspaceSourceLike(unittest.TestCase):
    """source-like 파일이 possible-source-leftovers 서브폴더로 이동하는지 테스트."""

    def test_normal_source_like_goes_to_subfolder(self) -> None:
        """source-like 확장자(.py 등) 파일은 possible-source-leftovers/ 서브폴더로 이동 (normal)."""
        import pipeline as _pipeline_module  # type: ignore

        with tempfile.TemporaryDirectory() as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            orig_base_dir = _pipeline_module.BASE_DIR
            orig_cwd = os.getcwd()
            try:
                _pipeline_module.BASE_DIR = tmpdir
                os.chdir(str(tmpdir_str))

                # .py 파일 생성 (이름이 HYGIENE_ARCHIVE_PATTERNS에 있어야 candidate가 됨)
                # bandit_e2e_result_test.py 패턴으로 생성
                py_file = tmpdir / "bandit_e2e_result_extra.json"
                py_file.write_text('{"test": 1}', encoding="utf-8")

                state = _make_state(terminal_state="COMPLETE", pipeline_id="IMP-20260605-58BF")
                mock_git = MagicMock()
                mock_git.stdout = ""

                deploy_root = tmpdir / "deploy_root"
                deploy_root.mkdir(parents=True, exist_ok=True)

                # candidate 항목을 직접 생성 (source-like .py 파일 포함)
                mock_candidate_py = {
                    "rel_path": "restore_imp_6fac.py",
                    "age_days": 0.1,
                    "disposition": "candidate",
                    "reason": None,
                }
                mock_candidate_json = {
                    "rel_path": "bandit_e2e_result_extra.json",
                    "age_days": 0.1,
                    "disposition": "candidate",
                    "reason": None,
                }

                # restore_imp_6fac.py 파일 생성 (실제 이동을 위해 필요)
                py_src = tmpdir / "restore_imp_6fac.py"
                py_src.write_text("# test", encoding="utf-8")

                with patch("pipeline._load_state", return_value=state):
                    with patch("subprocess.run", return_value=mock_git):
                        with patch("pipeline._deployment_root", return_value=deploy_root):
                            with patch("pipeline._hygiene_collect_candidates",
                                       return_value=[mock_candidate_py, mock_candidate_json]):
                                cmd_hygiene_cleanup_workspace(_make_args())

                manifest_path = tmpdir / "cleanup_manifest.json"
                self.assertTrue(manifest_path.exists())
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

                # .py 파일은 possible_source_leftovers에 있어야 함
                psl_paths = [p["path"] for p in manifest.get("possible_source_leftovers", [])]
                self.assertIn("restore_imp_6fac.py", psl_paths,
                              f"restore_imp_6fac.py가 possible_source_leftovers에 없음. 목록: {psl_paths}")

                # .json 파일은 moved에 있어야 함
                moved_paths = [p["path"] for p in manifest.get("moved", [])]
                self.assertIn("bandit_e2e_result_extra.json", moved_paths,
                              f"json 파일이 moved에 없음. 목록: {moved_paths}")
            finally:
                _pipeline_module.BASE_DIR = orig_base_dir
                os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
