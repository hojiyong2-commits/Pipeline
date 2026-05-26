"""Tests for _consistency_listed_files false positive filter.

IMP-20260525-AA88 MT-2: Verifies that score notations (120/120) and timing
notations (34.74s, 0.5s) are not extracted as file paths.
"""
from __future__ import annotations


# pipeline.py에서 _consistency_listed_files 가져오기
import importlib
import importlib.util
from pathlib import Path

# pipeline.py는 패키지가 아닌 단일 파일 — 직접 임포트
_pipeline_path = Path(__file__).resolve().parent.parent / "pipeline.py"
_spec = importlib.util.spec_from_file_location("pipeline_module", _pipeline_path)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

_consistency_listed_files = _mod._consistency_listed_files


class TestScoreNotationFilter:
    """숫자/숫자 패턴 (점수 표기) 필터 검증."""

    def test_score_120_120_not_extracted(self) -> None:
        """120/120 패턴은 파일 경로가 아니므로 추출되지 않아야 한다."""
        body = "- 120/120"
        files, _ = _consistency_listed_files(body)
        assert "120/120" not in files, f"120/120 should not be extracted as a file, got: {files}"
        assert len(files) == 0, f"No files expected, got: {files}"

    def test_score_notation_with_context(self) -> None:
        """PR body에 점수 표기가 포함되어도 파일 경로만 추출해야 한다."""
        body = "- 테스트 결과: 120/120\n- tests/test_x.py"
        files, _ = _consistency_listed_files(body)
        assert "120/120" not in files, f"120/120 should not be extracted, got: {files}"

    def test_various_score_patterns(self) -> None:
        """다양한 숫자/숫자 형태가 모두 필터링되어야 한다."""
        lines = [
            "- 0/100",
            "- 99/100",
            "- 412/412",
        ]
        for line in lines:
            body = line
            files, _ = _consistency_listed_files(body)
            token = line.strip("- ").strip()
            assert token not in files, f"Score pattern '{token}' should not be extracted, got: {files}"


class TestTimingNotationFilter:
    """숫자.숫자s 패턴 (타이밍 표기) 필터 검증."""

    def test_timing_34_74s_not_extracted(self) -> None:
        """34.74s 패턴은 타이밍 표기로 파일 경로가 아니어야 한다."""
        body = "- 34.74s"
        files, _ = _consistency_listed_files(body)
        assert "34.74s" not in files, f"34.74s should not be extracted as a file, got: {files}"
        assert len(files) == 0, f"No files expected, got: {files}"

    def test_timing_0_5s_not_extracted(self) -> None:
        """0.5s 패턴은 타이밍 표기로 파일 경로가 아니어야 한다."""
        body = "- 0.5s"
        files, _ = _consistency_listed_files(body)
        assert "0.5s" not in files, f"0.5s should not be extracted as a file, got: {files}"

    def test_integer_seconds_not_extracted(self) -> None:
        """1s, 10s 같은 정수초 표기도 파일 경로가 아니어야 한다."""
        for timing in ["1s", "10s", "100s"]:
            body = f"- {timing}"
            files, _ = _consistency_listed_files(body)
            assert timing not in files, f"Timing '{timing}' should not be extracted, got: {files}"


class TestRealFilesStillExtracted:
    """실제 파일 경로는 정상적으로 추출되어야 함 (기존 동작 보존)."""

    def test_real_python_file_extracted(self) -> None:
        """실제 .py 파일 경로는 추출되어야 한다."""
        body = "- tests/test_x.py"
        files, _ = _consistency_listed_files(body)
        assert "tests/test_x.py" in files, f"Real file path should be extracted, got: {files}"

    def test_real_pipeline_py_extracted(self) -> None:
        """pipeline.py도 추출되어야 한다."""
        body = "- pipeline.py"
        files, _ = _consistency_listed_files(body)
        assert "pipeline.py" in files, f"pipeline.py should be extracted, got: {files}"

    def test_mixed_real_and_score(self) -> None:
        """실제 파일과 점수 표기가 섞여 있으면 파일만 추출되어야 한다."""
        body = (
            "- tests/test_scorers_smart_resolve.py\n"
            "- 120/120\n"
            "- pipeline.py\n"
            "- 34.74s\n"
        )
        files, _ = _consistency_listed_files(body)
        assert "tests/test_scorers_smart_resolve.py" in files
        assert "pipeline.py" in files
        assert "120/120" not in files
        assert "34.74s" not in files
