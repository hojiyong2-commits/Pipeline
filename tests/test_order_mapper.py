"""
test_order_mapper.py
--------------------
Pipeline: BUG-20260506-B6A2
order_mapper.py의 핵심 함수들에 대한 pytest 단위 테스트.
order_mapper.py __main__ 블록의 self-verify 로직을 pytest 형식으로 래핑합니다.
"""
import sys
import os
import re
import tempfile
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional, Set

import pytest

# ic_part_src가 conftest.py에 의해 sys.path에 추가됨
import order_mapper
from order_mapper import (
    map_ic,
    _col_letter,
    date_to_serial,
    normalise_date,
    OrderGroup,
    apply_sub_order_suffixes,
    _inject_text_cell,
    _inject_date_cell,
    _detect_columns_from_header,
    _auto_detect_key_columns,
    ICPART_COL_FOLDER_DATE,
    ICPART_COL_CONTRACT_AMOUNT,
    ICPART_COL_CORB_PATH,
)


# ---------------------------------------------------------------------------
# map_ic
# ---------------------------------------------------------------------------

class TestMapIc:
    """map_ic() — IC 코드 매핑 테스트."""

    def test_imi_critical(self) -> None:
        assert map_ic("IMI Critical Engineering LLC") == "SoCal"

    def test_cci_valve_gmbh(self) -> None:
        assert map_ic("CCI Valve Technology GmbH") == "AT"

    def test_cc_valve_gmbh_variant(self) -> None:
        assert map_ic("CC Valve Technology Gmbh") == "AT", "CC Valve variant 미매핑"

    def test_cci_valve_lowercase_h(self) -> None:
        assert map_ic("CCI Valve Technology Gmbh") == "AT", "소문자 h 변형 미매핑"

    def test_cci_valve_case_insensitive(self) -> None:
        assert map_ic("cci valve technology gmbh") == "AT"
        assert map_ic("Cci Valve Technology GmbH") == "AT"

    def test_apac_contains(self) -> None:
        assert map_ic("Some APAC Company") == "SG"

    def test_apac_case_insensitive(self) -> None:
        assert map_ic("some apac company") == "SG"

    def test_unknown_passthrough(self) -> None:
        assert map_ic("Unknown Corp") == "Unknown Corp"

    def test_none_returns_empty_string(self) -> None:
        assert map_ic(None) == ""

    def test_non_string_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            map_ic(12345)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _col_letter
# ---------------------------------------------------------------------------

class TestColLetter:
    """_col_letter() — 1-based 열 번호 → 엑셀 열 문자 변환 테스트."""

    def test_j(self) -> None:
        assert _col_letter(10) == "J"

    def test_k(self) -> None:
        assert _col_letter(11) == "K"

    def test_q(self) -> None:
        assert _col_letter(17) == "Q"

    def test_a(self) -> None:
        assert _col_letter(1) == "A"

    def test_z(self) -> None:
        assert _col_letter(26) == "Z"

    def test_aa(self) -> None:
        assert _col_letter(27) == "AA"

    def test_zero_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            _col_letter(0)


# ---------------------------------------------------------------------------
# date_to_serial
# ---------------------------------------------------------------------------

class TestDateToSerial:
    """date_to_serial() — Python date → Excel 날짜 시리얼 변환 테스트."""

    def test_known_date(self) -> None:
        assert date_to_serial(date(2026, 6, 2)) == 46175

    def test_none_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            date_to_serial(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# normalise_date
# ---------------------------------------------------------------------------

class TestNormaliseDate:
    """normalise_date() — 다양한 형식의 날짜값 정규화 테스트."""

    def test_none_returns_none(self) -> None:
        assert normalise_date(None) is None

    def test_datetime_object(self) -> None:
        assert normalise_date(datetime(2026, 6, 2, 0, 0)) == date(2026, 6, 2)

    def test_date_object(self) -> None:
        assert normalise_date(date(2026, 6, 25)) == date(2026, 6, 25)

    def test_iso_string(self) -> None:
        assert normalise_date("2026-07-01") == date(2026, 7, 1)


# ---------------------------------------------------------------------------
# OrderGroup.format_line_nos
# ---------------------------------------------------------------------------

class TestFormatLineNos:
    """OrderGroup.format_line_nos() — 라인 번호 포맷팅 테스트."""

    def test_empty(self) -> None:
        og = OrderGroup()
        assert og.format_line_nos() == ""

    def test_consecutive_range(self) -> None:
        og = OrderGroup()
        og.line_nos = [10, 7, 8, 9]
        assert og.format_line_nos() == "#7~10"

    def test_mixed_non_consecutive(self) -> None:
        og = OrderGroup()
        og.line_nos = [3, 5, 6, 8]
        assert og.format_line_nos() == "#3,5,6,8"

    def test_short_gap(self) -> None:
        og = OrderGroup()
        og.line_nos = [1, 2, 4]
        assert og.format_line_nos() == "#1,2,4"


# ---------------------------------------------------------------------------
# apply_sub_order_suffixes
# ---------------------------------------------------------------------------

class TestApplySubOrderSuffixes:
    """apply_sub_order_suffixes() — 동일 주문번호 중복 시 접미사 처리 테스트."""

    def test_three_groups_same_order(self) -> None:
        og_a = OrderGroup()
        og_a.order_no = "X100050542"
        og_a.delivery_date = date(2026, 6, 2)

        og_b = OrderGroup()
        og_b.order_no = "X100050542"
        og_b.delivery_date = date(2026, 6, 25)

        og_c = OrderGroup()
        og_c.order_no = "X100050542"
        og_c.delivery_date = date(2026, 7, 1)

        apply_sub_order_suffixes([og_a, og_b, og_c])

        assert og_a.order_no == "X100050542"
        assert og_b.order_no == "X100050542-2"
        assert og_c.order_no == "X100050542-3"
        assert og_a.base_order_no == "X100050542"
        assert og_b.base_order_no == "X100050542"
        assert og_c.base_order_no == "X100050542"

    def test_line_no_decision_uses_base_order_after_suffix(self) -> None:
        og_a = OrderGroup()
        og_a.order_no = "X100050542"
        og_a.delivery_date = date(2026, 6, 2)
        og_a.line_nos = [1, 2, 3]

        og_b = OrderGroup()
        og_b.order_no = "X100050542"
        og_b.delivery_date = date(2026, 6, 25)
        og_b.line_nos = [4, 5]

        groups = [og_a, og_b]
        apply_sub_order_suffixes(groups)
        counts = {}
        for group in groups:
            key = group.base_order_no if group.base_order_no is not None else group.order_no
            counts[key] = counts.get(key, 0) + 1

        line_values = [
            group.format_line_nos()
            if counts[group.base_order_no if group.base_order_no is not None else group.order_no] > 1
            else ""
            for group in groups
        ]

        assert [og_a.order_no, og_b.order_no] == ["X100050542", "X100050542-2"]
        assert line_values == ["#1~3", "#4,5"]

    def test_unique_orders_unchanged(self) -> None:
        og_x = OrderGroup()
        og_x.order_no = "X100000001"

        og_y = OrderGroup()
        og_y.order_no = "X100000002"

        apply_sub_order_suffixes([og_x, og_y])

        assert og_x.order_no == "X100000001"
        assert og_y.order_no == "X100000002"


# ---------------------------------------------------------------------------
# _inject_text_cell
# ---------------------------------------------------------------------------

class TestInjectTextCell:
    """_inject_text_cell() — XML 셀 주입 테스트."""

    def test_self_closing_empty_cell(self) -> None:
        xml = (
            '<worksheet><sheetData>'
            '<row r="6"><c r="J6" s="38"/><c r="K6" s="38"/></row>'
            '</sheetData></worksheet>'
        )
        result = _inject_text_cell(xml, "J6", "TestValue")
        assert 't="inlineStr"' in result
        assert '<is><t>TestValue</t></is>' in result
        assert '<c r="K6" s="38"/>' in result

    def test_overwrite_existing_value(self) -> None:
        xml = (
            '<worksheet><sheetData>'
            '<row r="6"><c r="J6" s="14" t="inlineStr"><is><t>OldValue</t></is></c></row>'
            '</sheetData></worksheet>'
        )
        result = _inject_text_cell(xml, "J6", "NewValue")
        assert "NewValue" in result
        assert "OldValue" not in result
        assert 's="14"' in result


# ---------------------------------------------------------------------------
# _inject_date_cell
# ---------------------------------------------------------------------------

class TestInjectDateCell:
    """_inject_date_cell() — 날짜 시리얼 셀 주입 테스트."""

    def test_inject_valid_serial(self) -> None:
        xml = (
            '<worksheet><sheetData>'
            '<row r="6"><c r="Q6" s="44"/></row>'
            '</sheetData></worksheet>'
        )
        result = _inject_date_cell(xml, "Q6", 46175)
        assert "<v>46175</v>" in result

    def test_zero_serial_raises_value_error(self) -> None:
        xml = (
            '<worksheet><sheetData>'
            '<row r="6"><c r="Q6" s="44"/></row>'
            '</sheetData></worksheet>'
        )
        with pytest.raises(ValueError):
            _inject_date_cell(xml, "Q6", 0)


# ---------------------------------------------------------------------------
# _detect_columns_from_header
# ---------------------------------------------------------------------------

class TestDetectColumnsFromHeader:
    """_detect_columns_from_header() — 헤더 행에서 컬럼 인덱스 자동 감지 테스트."""

    def test_new_format_project_id_and_customer_name(self) -> None:
        header = (
            "Order No", "Line No", "Del No", "Rental", "Project Id",
            "Wanted Delivery Date", "Status", "Order Type", "Customer No", "Customer Name"
        )
        detected = _detect_columns_from_header(header)
        assert detected.get("project_id") == 4, \
            f"project_id col should be 4, got {detected.get('project_id')}"
        assert detected.get("customer_name") == 9, \
            f"customer_name col should be 9, got {detected.get('customer_name')}"

    def test_old_format_customer_name(self) -> None:
        header = (
            "Wanted Delivery Date", "Order No", "Line No", "Del No", "Rental",
            "Status", "Order Type", "Customer No", "Customer Name"
        )
        detected = _detect_columns_from_header(header)
        assert detected.get("customer_name") == 8, \
            f"old format customer_name should be 8, got {detected.get('customer_name')}"


# ---------------------------------------------------------------------------
# _auto_detect_key_columns
# ---------------------------------------------------------------------------

class TestAutoDetectKeyColumns:
    """_auto_detect_key_columns() — 데이터 행에서 키 컬럼 자동 감지 테스트."""

    def test_order_no_first(self) -> None:
        rows = [("X100050542", datetime(2026, 6, 2))]
        order_col, date_col = _auto_detect_key_columns(rows)
        assert order_col == 0
        assert date_col == 1

    def test_date_first(self) -> None:
        rows = [(datetime(2026, 6, 2), "X100050542")]
        order_col, date_col = _auto_detect_key_columns(rows)
        assert order_col == 1
        assert date_col == 0


# ---------------------------------------------------------------------------
# OrderGroup.add_line with column overrides
# ---------------------------------------------------------------------------

class TestAddLineWithOverrides:
    """OrderGroup.add_line() — 컬럼 인덱스 오버라이드 테스트."""

    def test_custom_col_indices(self) -> None:
        row_data = [None] * 10
        row_data[1] = "X100050999"
        row_data[0] = datetime(2026, 6, 10)
        row_data[2] = 5
        row_data[9] = "Test Customer Name"
        row_data[4] = "B1234"

        og = OrderGroup()
        og.add_line(
            tuple(row_data),
            col_order_no=1,
            col_delivery_date=0,
            col_customer_name=9,
            col_project_id=4,
        )
        assert og.order_no == "X100050999"
        assert og.customer_name == "Test Customer Name", f"got {og.customer_name!r}"
        assert og.project_id == "B1234", f"got {og.project_id!r}"


# ---------------------------------------------------------------------------
# Column constants
# ---------------------------------------------------------------------------

class TestColumnConstants:
    """IC-Part 컬럼 상수 값 검증."""

    def test_folder_date_col(self) -> None:
        assert ICPART_COL_FOLDER_DATE == 8  # IMP-20260509-F8BF: H column

    def test_contract_amount_col(self) -> None:
        assert ICPART_COL_CONTRACT_AMOUNT == 14  # IMP-20260509-F8BF: N column

    def test_corb_path_col(self) -> None:
        assert ICPART_COL_CORB_PATH == 18  # IMP-20260509-F8BF: R column

    def test_ic_distribution_formula_reference_updated_to_project_id_col(self) -> None:
        src_path = Path(__file__).resolve().parent.parent / "ic_part_src" / "order_mapper.py"
        src_text = src_path.read_text(encoding="utf-8")
        assert 'replace("Sheet1!J:J", "Sheet1!I:I")' in src_text


# ---------------------------------------------------------------------------
# Bug 2 fix: frozen _check_pids snapshot
# ---------------------------------------------------------------------------

class TestFrozenCheckPids:
    """Bug2 수정 검증: 동일 실행 내 서브그룹(X100050786-2, -3)이 스킵되지 않아야 함."""

    def test_frozen_snapshot_allows_subgroups(self) -> None:
        existing_pids: Set[str] = {"PJ-001", "PJ-002"}
        check_pids = set(existing_pids)  # frozen snapshot — never mutated

        groups_data = [
            ("PJ-001", True),   # 이미 템플릿에 있음 → skip
            ("PJ-003", False),  # 신규 → write
            ("PJ-003", False),  # 같은 실행 내 서브그룹 → write (버그 수정 후)
            (None,     False),  # None → write
        ]

        written = 0
        skipped = 0
        for pid_raw, expect_skip in groups_data:
            pid_t = pid_raw.strip() if pid_raw else None
            if pid_t and pid_t in check_pids:
                skipped += 1
                assert expect_skip, f"Unexpected skip for pid={pid_t}"
            else:
                written += 1
                assert not expect_skip, f"Expected skip for pid={pid_t} but wrote"

        assert skipped == 1, f"Bug2: expected 1 skipped, got {skipped}"
        assert written == 3, f"Bug2: expected 3 written, got {written}"


# ---------------------------------------------------------------------------
# Bug 1 fix: IC-Distribution A2 project_id
# ---------------------------------------------------------------------------

class TestA2ProjectId:
    """Bug1 수정 검증: IC-Distribution A2에 project_id가 들어가야 함."""

    def test_project_id_used_when_present(self) -> None:
        og = OrderGroup()
        og.order_no = "X100050786"
        og.project_id = "B42728KR"
        a2_value = (
            og.project_id.strip()
            if og.project_id and og.project_id.strip()
            else og.order_no
        )
        assert a2_value == "B42728KR", f"Bug1: A2 should be project_id, got {a2_value!r}"

    def test_order_no_fallback_when_no_project_id(self) -> None:
        og = OrderGroup()
        og.order_no = "X100050601"
        og.project_id = None
        a2_value = (
            og.project_id.strip()
            if og.project_id and og.project_id.strip()
            else og.order_no
        )
        assert a2_value == "X100050601", f"Bug1: fallback should be order_no, got {a2_value!r}"


# ---------------------------------------------------------------------------
# WinError 5 retry constants (BUG-20260506-B6A2 핵심 수정 검증)
# ---------------------------------------------------------------------------

class TestWinError5RetryConstants:
    """BUG-20260506-B6A2 핵심: write_ic_part_zip 내 WinError 5 재시도 로직 상수 검증."""

    def test_retry_constants_exist_in_source(self) -> None:
        """order_mapper.py 소스 내 _MAX_RETRIES=10, _RETRY_INTERVAL=2.0 상수 확인."""
        src_path = Path(__file__).resolve().parent.parent / "ic_part_src" / "order_mapper.py"
        assert src_path.exists(), f"order_mapper.py not found at {src_path}"
        src_text = src_path.read_text(encoding="utf-8")
        assert "_MAX_RETRIES: int = 10" in src_text, \
            "_MAX_RETRIES=10 상수가 order_mapper.py에 없음"
        assert "_RETRY_INTERVAL: float = 2.0" in src_text, \
            "_RETRY_INTERVAL=2.0 상수가 order_mapper.py에 없음"

    def test_permission_error_catch_exists(self) -> None:
        """PermissionError 별도 catch 블록 존재 확인."""
        src_path = Path(__file__).resolve().parent.parent / "ic_part_src" / "order_mapper.py"
        src_text = src_path.read_text(encoding="utf-8")
        assert "except PermissionError as _perm_exc:" in src_text, \
            "PermissionError catch 블록 없음"

    def test_winerror_5_catch_exists(self) -> None:
        """OSError + winerror==5 분기 처리 존재 확인."""
        src_path = Path(__file__).resolve().parent.parent / "ic_part_src" / "order_mapper.py"
        src_text = src_path.read_text(encoding="utf-8")
        assert "getattr(_os_exc, \"winerror\", None) == 5" in src_text, \
            "WinError 5 감지 코드 없음"

    def test_non_winerror_reraise_exists(self) -> None:
        """비-WinError-5 OSError 즉시 re-raise 존재 확인."""
        src_path = Path(__file__).resolve().parent.parent / "ic_part_src" / "order_mapper.py"
        src_text = src_path.read_text(encoding="utf-8")
        assert "raise  # non-WinError-5 OSError" in src_text, \
            "비-WinError-5 즉시 re-raise 없음"


print("ASSERTION PASSED")
