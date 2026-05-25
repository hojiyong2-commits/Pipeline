"""Tests for _smart_resolve fix in core/acceptance/scorers.py.

IMP-20260525-AA88 MT-1: Verifies that _smart_resolve uses parent.parent.parent
(project root) instead of parent.parent (core/ directory).
"""
from __future__ import annotations

from pathlib import Path


from core.acceptance.scorers import _smart_resolve


# Project root is three levels up from this file: tests/ -> project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class TestSmartResolveUsesProjectRoot:
    """parent.parent.parent (project root) 기준 경로 탐색 검증."""

    def test_finds_oracle_file_via_project_root(self) -> None:
        """실제 oracle 파일을 project root 기준으로 찾아야 한다."""
        path_str = "tests/oracles/IMP-20260525-AA88/TC-normal-smart-resolve/input.json"
        # base_dir을 다른 경로로 주더라도 project root 기준으로 찾아야 한다.
        fake_base_dir = str(PROJECT_ROOT / "core" / "acceptance")
        result = _smart_resolve(fake_base_dir, path_str)
        assert Path(result).exists(), (
            f"_smart_resolve should find '{path_str}' via project root, "
            f"but result '{result}' does not exist"
        )

    def test_project_root_is_not_core_dir(self) -> None:
        """parent.parent.parent가 core/ 아닌 실제 project root를 가리켜야 한다.

        scorers.py 위치: <project_root>/core/acceptance/scorers.py
        - parent        = acceptance/
        - parent.parent = core/
        - parent.parent.parent = <project_root>  ← 올바른 위치
        """
        from core.acceptance import scorers as scorers_module
        scorers_path = Path(scorers_module.__file__).resolve()
        # parent.parent는 core/ 디렉터리
        core_dir = scorers_path.parent.parent
        # parent.parent.parent는 project root
        project_root = scorers_path.parent.parent.parent

        # core/ 기준으로 tests/oracles/... 경로를 찾으면 존재하지 않아야 한다.
        oracle_rel = "tests/oracles/IMP-20260525-AA88/TC-normal-smart-resolve/input.json"
        core_candidate = core_dir / oracle_rel
        assert not core_candidate.exists(), (
            f"parent.parent (core/) 기준 경로 '{core_candidate}' 가 존재하면 안 됩니다 "
            f"— project root 오판 위험"
        )

        # project root 기준으로는 존재해야 한다.
        root_candidate = project_root / oracle_rel
        assert root_candidate.exists(), (
            f"parent.parent.parent (project root) 기준 경로 '{root_candidate}' 가 존재해야 합니다"
        )

    def test_absolute_path_returned_unchanged(self) -> None:
        """절대 경로는 그대로 반환해야 한다."""
        abs_path = str(PROJECT_ROOT / "tests" / "oracles" / "IMP-20260525-AA88" / "TC-normal-smart-resolve" / "input.json")
        result = _smart_resolve(str(PROJECT_ROOT), abs_path)
        assert result == abs_path

    def test_project_dir_kwarg_takes_priority(self) -> None:
        """project_dir 인자가 주어지면 그것을 최우선으로 탐색해야 한다."""
        path_str = "tests/oracles/IMP-20260525-AA88/TC-normal-smart-resolve/input.json"
        result = _smart_resolve(
            base_dir=str(PROJECT_ROOT / "core"),
            path_str=path_str,
            project_dir=str(PROJECT_ROOT),
        )
        assert Path(result).exists(), f"project_dir 기준 탐색 결과 '{result}' 가 존재해야 합니다"

    def test_fallback_to_base_dir_when_not_found_in_project_root(self, tmp_path: Path) -> None:
        """project root에도 없으면 base_dir 기준 경로를 반환해야 한다."""
        # tmp_path에 임시 파일 생성
        dummy_file = tmp_path / "dummy_test.txt"
        dummy_file.write_text("hello")

        result = _smart_resolve(str(tmp_path), "dummy_test.txt")
        assert result == str(tmp_path / "dummy_test.txt")
