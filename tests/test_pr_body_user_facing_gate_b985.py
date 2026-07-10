# [Purpose]: IMP-20260703-B985 MT-34 회귀 테스트 — _check_pr_body_user_facing 함수가
#   BOM / stale acceptance / requirements_summary N/A 3개 케이스를 차단하고 정상 body는
#   PASS하는지 검증한다 (REJECT #27 재발 방지).
# [Assumptions]: pipeline.py가 import 가능하며 _check_pr_body_user_facing를 export한다.
# [Vulnerability & Risks]: oracle JSON 경로가 바뀌면 테스트가 깨진다 — ORACLE_DIR 상수로 관리.
# [Improvement]: _strip_stale_metrics_block 통합 테스트를 추가할 수 있다.
"""MT-34 회귀 테스트: _check_pr_body_user_facing 함수 검증."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import _check_pr_body_user_facing  # noqa: E402

ORACLE_DIR = os.path.join(
    os.path.dirname(__file__), "oracles", "IMP-20260703-B985", "TC-34"
)


def _load_oracle(name):
    with open(os.path.join(ORACLE_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


def test_blocked_bom_start():
    """BOM 유니코드 문자로 시작하는 PR body -> BLOCKED: pr_body_bom_detected."""
    body = "﻿## 작업 요약\n정상 내용"
    result = _check_pr_body_user_facing(body, "10/10 PASS")
    expected = _load_oracle("expected_blocked_bom.json")
    assert result["status"] == expected["status"]
    assert result["failure_code"] == expected["failure_code"]


def test_blocked_utf8_bom_bytes():
    """UTF-8 BOM bytes(EF BB BF)로 인코딩된 PR body -> BLOCKED: pr_body_bom_detected."""
    raw = b"\xef\xbb\xbf" + "## 작업 요약\n정상 내용".encode("utf-8")
    body = raw.decode("utf-8")
    result = _check_pr_body_user_facing(body, "10/10 PASS")
    expected = _load_oracle("expected_blocked_bom.json")
    assert result["status"] == expected["status"]
    assert result["failure_code"] == expected["failure_code"]


def test_blocked_stale_fail_after_end_marker():
    """FINAL_PACKET_END 뒤 acceptance: FAIL -> BLOCKED: pr_body_stale_metrics_block."""
    body = (
        "## 작업 요약\n내용\n"
        "<!-- PIPELINE_FINAL_PACKET_START -->\n"
        "패킷 내용\n"
        "<!-- PIPELINE_FINAL_PACKET_END -->\n"
        "acceptance: FAIL\n"
        "technical: PASS\n"
    )
    result = _check_pr_body_user_facing(body, "10/10 PASS")
    expected = _load_oracle("expected_blocked_stale_fail.json")
    assert result["status"] == expected["status"]
    assert result["failure_code"] == expected["failure_code"]


def test_blocked_stale_rejected_after_end_marker():
    """FINAL_PACKET_END 뒤 acceptance: REJECTED -> BLOCKED: pr_body_stale_metrics_block."""
    body = (
        "## 작업 요약\n내용\n"
        "<!-- PIPELINE_FINAL_PACKET_END -->\n"
        "acceptance: REJECTED\n"
    )
    result = _check_pr_body_user_facing(body, "10/10 PASS")
    expected = _load_oracle("expected_blocked_stale_fail.json")
    assert result["status"] == expected["status"]
    assert result["failure_code"] == expected["failure_code"]


def test_blocked_stale_fail_before_packet():
    """packet 블록 앞에 acceptance: FAIL -> BLOCKED: pr_body_stale_acceptance_fail."""
    body = (
        "## 작업 요약\n내용\n"
        "acceptance: FAIL\n"
        "<!-- PIPELINE_FINAL_PACKET_START -->\n"
        "패킷 내용\n"
        "<!-- PIPELINE_FINAL_PACKET_END -->\n"
    )
    result = _check_pr_body_user_facing(body, "10/10 PASS")
    assert result["status"] == "BLOCKED"
    assert result["failure_code"] == "pr_body_stale_acceptance_fail"


def test_blocked_requirements_na():
    """requirements_summary = 'N/A' -> BLOCKED: pr_body_requirements_summary_na."""
    body = "## 작업 요약\n정상 내용"
    result = _check_pr_body_user_facing(body, "N/A")
    expected = _load_oracle("expected_blocked_req_na.json")
    assert result["status"] == expected["status"]
    assert result["failure_code"] == expected["failure_code"]


def test_blocked_requirements_na_legacy_text():
    """requirements_summary = 'N/A (structured AC 없음 — legacy 파이프라인)' -> BLOCKED."""
    body = "## 작업 요약\n정상 내용"
    result = _check_pr_body_user_facing(
        body, "N/A (structured AC 없음 — legacy 파이프라인)"
    )
    expected = _load_oracle("expected_blocked_req_na.json")
    assert result["status"] == expected["status"]
    assert result["failure_code"] == expected["failure_code"]


def test_ok_clean_body_oracle_based():
    """oracle 기반 requirements_summary + 깔끔한 body -> PASS."""
    body = "## 작업 요약\n정상 내용\n## 검증\n완료"
    result = _check_pr_body_user_facing(
        body, "oracle 기반 검증 (14개 케이스 PASS — structured AC 없음)"
    )
    expected = _load_oracle("expected_ok.json")
    assert result["status"] == expected["status"]


def test_ok_clean_body_with_ac_count():
    """10/10 PASS requirements_summary + 깔끔한 body -> PASS."""
    body = "## 작업 요약\n정상 내용"
    result = _check_pr_body_user_facing(body, "10/10 PASS")
    expected = _load_oracle("expected_ok.json")
    assert result["status"] == expected["status"]


def test_ok_no_packet_markers():
    """packet 마커 없는 깔끔한 body + oracle requirements -> PASS."""
    body = "## 작업 요약\n정상 내용\n## 검증\n완료"
    result = _check_pr_body_user_facing(
        body, "oracle 기반 검증 (5개 케이스 PASS — structured AC 없음)"
    )
    expected = _load_oracle("expected_ok.json")
    assert result["status"] == expected["status"]


def test_ok_clean_packet_pending_inside_block():
    """FINAL_PACKET 블록 안의 acceptance: PENDING은 PASS (stale FAIL/REJECTED만 차단)."""
    body = (
        "## 작업 요약\n내용\n"
        "<!-- PIPELINE_FINAL_PACKET_START -->\n"
        "technical: PASS\n"
        "acceptance: PENDING\n"
        "<!-- PIPELINE_FINAL_PACKET_END -->\n"
        "## 검증\n완료\n"
    )
    result = _check_pr_body_user_facing(body, "10/10 PASS")
    assert result["status"] == "PASS"


def test_type_error_on_none_body():
    """pr_body_text가 None이면 TypeError."""
    try:
        _check_pr_body_user_facing(None, "10/10 PASS")  # type: ignore[arg-type]
        assert False, "예외 미발생"
    except TypeError:
        pass


def test_type_error_on_none_requirements():
    """requirements_summary가 None이면 TypeError."""
    try:
        _check_pr_body_user_facing("## 작업 요약", None)  # type: ignore[arg-type]
        assert False, "예외 미발생"
    except TypeError:
        pass


if __name__ == "__main__":
    test_blocked_bom_start()
    test_blocked_utf8_bom_bytes()
    test_blocked_stale_fail_after_end_marker()
    test_blocked_stale_rejected_after_end_marker()
    test_blocked_stale_fail_before_packet()
    test_blocked_requirements_na()
    test_blocked_requirements_na_legacy_text()
    test_ok_clean_body_oracle_based()
    test_ok_clean_body_with_ac_count()
    test_ok_no_packet_markers()
    test_ok_clean_packet_pending_inside_block()
    test_type_error_on_none_body()
    test_type_error_on_none_requirements()
    print("[SELF-VERIFY] OK")
