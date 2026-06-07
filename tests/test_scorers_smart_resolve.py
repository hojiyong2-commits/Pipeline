"""Tests for _smart_resolve fix in core/acceptance/scorers.py.

IMP-20260525-AA88 MT-1: Verifies that _smart_resolve uses parent.parent.parent
(project root) instead of parent.parent (core/ directory).
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path


# 프로젝트 루트를 sys.path에 추가
# 주의: test_packing_detail_oracle_4cd8.py 등이 FEAT-20260426-332E 디렉터리를
# sys.path[0]에 추가하여 'core' 모듈을 다른 패키지로 등록할 수 있다.
# sys.modules에서 기존 core/core.acceptance 등록을 제거하고 프로젝트 루트 기준으로
# 재로드하여 충돌을 해소한다.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCORERS_PATH = _PROJECT_ROOT / "core" / "acceptance" / "scorers.py"

# sys.modules에서 충돌 가능성이 있는 기존 core 관련 모듈을 제거
for _key in [k for k in sys.modules if k == "core" or k.startswith("core.")]:
    del sys.modules[_key]

# 프로젝트 루트를 sys.path 맨 앞에 삽입
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
elif sys.path[0] != str(_PROJECT_ROOT):
    # 이미 있더라도 맨 앞에 오도록 재배치
    sys.path.remove(str(_PROJECT_ROOT))
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.acceptance.scorers import _smart_resolve  # noqa: E402


# Project root is two levels up from this file: tests/ -> project root
PROJECT_ROOT = _PROJECT_ROOT


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
        # importlib를 통해 scorers 모듈 경로를 직접 확인 (sys.modules['core'] 충돌 우회)
        scorers_path = _SCORERS_PATH.resolve()
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
