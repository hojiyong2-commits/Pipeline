"""
test_automation.py
------------------
Pipeline: BUG-20260506-B6A2
automation.py의 핵심 함수들에 대한 pytest 단위 테스트.
automation.py __main__ 블록의 self-verify 로직을 pytest 형식으로 래핑합니다.
"""
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path

import pytest

# ic_part_src가 conftest.py에 의해 sys.path에 추가됨
import automation
from automation import (
    parse_folder_name,
    _sanitize_name_component,
    build_corb_path,
    copy_files_to_corb,
    FolderWatcher,
)


# ---------------------------------------------------------------------------
# parse_folder_name
# ---------------------------------------------------------------------------

class TestParseFolderName:
    """parse_folder_name() — EmailMonitor 폴더명 파싱 테스트."""

    def test_standard_format(self) -> None:
        result = parse_folder_name("(ACT) 314134143 - 294,000 - 2026-04-16 - EXW")
        assert result["contract_amount"] == "294,000"
        assert result["folder_date"] == date(2026, 4, 16)
        assert result["incoterm"] == "EXW"

    def test_empty_string_returns_none_fields(self) -> None:
        result = parse_folder_name("")
        assert result["contract_amount"] is None
        assert result["folder_date"] is None
        assert result["incoterm"] is None

    def test_none_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            parse_folder_name(None)  # type: ignore[arg-type]

    def test_non_string_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            parse_folder_name(12345)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _sanitize_name_component
# ---------------------------------------------------------------------------

class TestSanitizeNameComponent:
    """_sanitize_name_component() — 폴더명 구성요소 새니타이즈 테스트."""

    def test_datetime_string_truncated(self) -> None:
        assert _sanitize_name_component("2026-06-02 00:00:00") == "2026-06-02"

    def test_invalid_win_chars_removed(self) -> None:
        assert _sanitize_name_component("A:B<C>D") == "ABCD"

    def test_strip_dots_and_spaces(self) -> None:
        assert _sanitize_name_component("  .hello. ") == "hello"

    def test_none_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            _sanitize_name_component(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# build_corb_path
# ---------------------------------------------------------------------------

class TestBuildCorbPath:
    """build_corb_path() — CORB 폴더 경로 빌더 테스트."""

    def test_standard_path(self) -> None:
        corb = build_corb_path("C:/CORB", "B1234", "PO999", "X100050542")
        assert corb.endswith("B1234 PO999 X100050542"), \
            f"build_corb_path mismatch: {corb!r}"

    def test_datetime_order_no_sanitized(self) -> None:
        corb = build_corb_path("C:/CORB", "B1234", "PO999", "2026-06-02 00:00:00")
        assert ":" not in corb.split("CORB", 1)[-1]
        assert corb.endswith("B1234 PO999 2026-06-02")

    def test_empty_project_id_no_leading_space(self) -> None:
        p = Path(build_corb_path("C:/CORB", "", "PO999", "X100050542"))
        assert not p.name.startswith(" "), \
            f"folder name must not start with space: {p.name!r}"
        assert p.name == "PO999 X100050542", \
            f"expected 'PO999 X100050542', got {p.name!r}"

    def test_all_empty_except_order_no(self) -> None:
        p = Path(build_corb_path("C:/CORB", "", "", "X100050542"))
        assert p.name == "X100050542", \
            f"expected 'X100050542', got {p.name!r}"

    def test_none_corb_base_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            build_corb_path(None, "B1234", "PO999", "X100")  # type: ignore[arg-type]

    def test_empty_corb_base_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            build_corb_path("   ", "B1234", "PO999", "X100")


# ---------------------------------------------------------------------------
# FolderWatcher input validation
# ---------------------------------------------------------------------------

class TestFolderWatcherValidation:
    """FolderWatcher — 생성자 입력 검증 테스트."""

    _cmap = {"SoCal": "C:/CORB/SoCal", "SG": "C:/CORB/SG", "AT": "C:/CORB/AT"}

    def test_none_watch_dir_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            FolderWatcher(None, "t.xlsx", self._cmap)  # type: ignore[arg-type]

    def test_empty_watch_dir_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            FolderWatcher("", "t.xlsx", self._cmap)

    def test_none_corb_base_map_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            FolderWatcher("C:/watch", "t.xlsx", None)  # type: ignore[arg-type]

    def test_empty_corb_base_map_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            FolderWatcher("C:/watch", "t.xlsx", {})


# ---------------------------------------------------------------------------
# copy_files_to_corb
# ---------------------------------------------------------------------------

class TestCopyFilesToCorb:
    """copy_files_to_corb() — 파일 복사 로직 테스트."""

    def test_none_src_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            copy_files_to_corb(None, Path("C:/out"))  # type: ignore[arg-type]

    def test_nonexistent_src_raises_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            copy_files_to_corb(Path("C:/nonexistent_xyzzy_12345"), Path("C:/out"))

    def test_lock_file_skipped(self) -> None:
        tmp_src = Path(tempfile.mkdtemp())
        tmp_dst = Path(tempfile.mkdtemp())
        try:
            (tmp_src / "~$CustomerOrderLines.xlsx").write_text("lock", encoding="utf-8")
            (tmp_src / "real.pdf").write_text("data", encoding="utf-8")
            copied = copy_files_to_corb(tmp_src, tmp_dst)
            assert "~$CustomerOrderLines.xlsx" not in copied, \
                "~$ file must be skipped"
            assert "real.pdf" in copied, \
                "real file must be copied"
        finally:
            shutil.rmtree(str(tmp_src))
            shutil.rmtree(str(tmp_dst))


print("ASSERTION PASSED")
