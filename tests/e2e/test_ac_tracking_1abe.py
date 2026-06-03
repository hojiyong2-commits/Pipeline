"""
test_ac_tracking_1abe.py — IMP-20260602-1ABE MT-8
Structured AC Tracking E2E 테스트

# [Purpose]: PM structured AC 파싱/검증, requirements_tracking 플래그 저장,
#            module qa ac_verification 강제, request-accept AC 충족표,
#            Dev scope_manifest implemented_tasks 검증, Oracle ac_ids 검증,
#            Codex Review coverage_checks hard gate가 정확히 동작하는지 검증.
# [Assumptions]: pipeline.py에서 함수 직접 import 가능 + subprocess CLI 호출 시
#                PIPELINE_STATE_PATH 격리. 일부 테스트는 함수 단위 검증으로 처리.
# [Vulnerability & Risks]:
#   - gh CLI 없는 환경에서 request-accept 일부 stale 검사는 skip.
#   - 함수 직접 import 테스트는 CLI 인자 파싱은 검증하지 않음 (별도 케이스에서 처리).
# [Improvement]: PIPELINE_STATE_PATH 격리한 full PM→Dev 흐름 테스트를 추가하면
#                CLI 인자 파싱까지 한 번에 검증 가능.

# CLI Evidence Contract (IMP-20260525-6FAC):
# - 상태 변경 CLI 호출은 PIPELINE_STATE_PATH 격리 + final_state assertion 포함
# - 함수 직접 호출 테스트는 별도로 비-CLI 영역에서만 사용
"""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

# pipeline.py를 import 가능하게
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pipeline import (  # noqa: E402
    ABSTRACT_AC_PATTERNS,
    CODEX_COVERAGE_CHECK_FIELDS,
    _parse_structured_ac,
    _validate_structured_ac_block,
    _check_module_qa_ac_verification,
    _get_mt_covers_ac,
    _get_mt_covers_iqr,
    _validate_implemented_tasks,
    _audit_oracle_quality,
    _validate_codex_coverage_checks,
    _build_ac_fulfillment_table,
    _format_ac_fulfillment_output,
)


def _step_plan_with_ac(criteria: List[Dict[str, Any]]) -> Any:
    """테스트용 step_plan XML element 생성 헬퍼."""
    root = ET.Element("step_plan")
    ac_root = ET.SubElement(root, "acceptance_criteria")
    for c in criteria:
        crit = ET.SubElement(ac_root, "criterion")
        crit.set("id", c["id"])
        if "must_verify" in c:
            crit.set("must_verify", str(c["must_verify"]).lower())
        if "source" in c:
            crit.set("source", c["source"])
        if "user_visible" in c:
            crit.set("user_visible", str(c["user_visible"]).lower())
        text = ET.SubElement(crit, "text")
        text.text = c["text"]
    return root


# ---------------------------------------------------------------------------
# AC-1: PM structured AC 파싱/검증
# ---------------------------------------------------------------------------

def test_pm_ac_missing_in_step_plan_returns_empty():
    """acceptance_criteria 블록 없는 step_plan → 빈 리스트 반환 (legacy 호환)."""
    root = ET.fromstring("<step_plan><micro_tasks/></step_plan>")
    acs = _parse_structured_ac(root)
    assert acs == []


def test_pm_covers_ac_missing_fails():
    """MT에 covers_ac도 covers_iqr도 없으면 _validate_structured_ac_block FAIL."""
    root = _step_plan_with_ac([
        {"id": "AC-1", "text": "구체적 성공 조건"},
    ])
    acs = _parse_structured_ac(root)
    mts = [{"id": "MT-1", "covers_ac": [], "covers_iqr": []}]
    r = _validate_structured_ac_block(acs, mts)
    assert not r["valid"]
    assert "covers_ac가 없습니다" in r["error"]


def test_pm_must_verify_ac_unlinked_fails():
    """must_verify=true AC가 어떤 MT covers_ac에도 없으면 FAIL."""
    root = _step_plan_with_ac([
        {"id": "AC-1", "text": "구체적 조건 A", "must_verify": True},
        {"id": "AC-2", "text": "구체적 조건 B", "must_verify": True},
    ])
    acs = _parse_structured_ac(root)
    mts = [{"id": "MT-1", "covers_ac": ["AC-1"]}]  # AC-2 미연결
    r = _validate_structured_ac_block(acs, mts)
    assert not r["valid"]
    assert "AC-2" in r["error"]
    assert "연결" in r["error"]


def test_pm_abstract_ac_text_fails():
    """AC requirement가 단독 추상 문구이면 차단."""
    root = _step_plan_with_ac([
        {"id": "AC-1", "text": "정상 동작"},
    ])
    acs = _parse_structured_ac(root)
    r = _validate_structured_ac_block(acs, [{"id": "MT-1", "covers_ac": ["AC-1"]}])
    assert not r["valid"]
    assert "추상적" in r["error"]
    assert "정상 동작" in r["error"]


def test_pm_abstract_ac_with_concrete_value_allowed():
    """추상 문구 + 구체값 결합은 허용 (단독 추상만 차단)."""
    root = _step_plan_with_ac([
        {"id": "AC-1", "text": "정상 동작 — 5초 이내 응답"},
    ])
    acs = _parse_structured_ac(root)
    r = _validate_structured_ac_block(acs, [{"id": "MT-1", "covers_ac": ["AC-1"]}])
    assert r["valid"], r


def test_pm_duplicate_ac_id_fails():
    """중복 AC id 차단."""
    root = _step_plan_with_ac([
        {"id": "AC-1", "text": "조건 A"},
        {"id": "AC-1", "text": "조건 B 중복"},
    ])
    acs = _parse_structured_ac(root)
    r = _validate_structured_ac_block(acs, [{"id": "MT-1", "covers_ac": ["AC-1"]}])
    assert not r["valid"]
    assert "중복" in r["error"]


def test_pm_ac_id_format_invalid_fails():
    """AC id가 AC-숫자 형식이 아니면 차단."""
    root = _step_plan_with_ac([
        {"id": "BC-1", "text": "잘못된 prefix"},
    ])
    acs = _parse_structured_ac(root)
    r = _validate_structured_ac_block(acs, [{"id": "MT-1", "covers_ac": ["BC-1"]}])
    assert not r["valid"]
    assert "형식 오류" in r["error"]


def test_pm_unknown_ac_id_in_covers_fails():
    """covers_ac에 PM에 없는 AC id 참조 시 FAIL."""
    root = _step_plan_with_ac([
        {"id": "AC-1", "text": "조건 A"},
    ])
    acs = _parse_structured_ac(root)
    r = _validate_structured_ac_block(acs, [{"id": "MT-1", "covers_ac": ["AC-99"]}])
    assert not r["valid"]
    assert "알 수 없는" in r["error"]


def test_pm_abstract_pattern_set_complete():
    """ABSTRACT_AC_PATTERNS에 명세된 15개 패턴이 모두 포함됨을 검증."""
    expected = {
        "정상 동작", "테스트 통과", "문제 없음", "잘 처리됨", "오류 없음",
        "사용자 요구 반영", "작동", "동작", "works", "working", "implemented",
        "기능 구현", "완료", "done", "finished",
    }
    assert expected.issubset(ABSTRACT_AC_PATTERNS)


def test_pm_covers_iqr_only_mt_allowed():
    """covers_iqr만 있는 문서 전용 MT는 PASS."""
    root = _step_plan_with_ac([
        {"id": "AC-1", "text": "조건 A"},
    ])
    acs = _parse_structured_ac(root)
    mts = [
        {"id": "MT-1", "covers_ac": ["AC-1"]},
        {"id": "MT-2", "covers_ac": [], "covers_iqr": ["IQR-1"]},
    ]
    r = _validate_structured_ac_block(acs, mts)
    assert r["valid"], r


# ---------------------------------------------------------------------------
# AC-3: module qa ac_verification 검증
# ---------------------------------------------------------------------------

def test_module_qa_legacy_state_passes_without_ac_check():
    """requirements_tracking 없는 legacy state → ac_verification 없이도 PASS."""
    state = {"atomic_plan": {}}
    r = _check_module_qa_ac_verification(state, "MT-1", None)
    assert r["valid"]


def test_module_qa_requirements_tracking_without_structured_ac_fails():
    """requirements_tracking.enabled=true인데 structured_ac 비어있음 → FAIL."""
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [],
    }
    r = _check_module_qa_ac_verification(state, "MT-1", None)
    assert not r["valid"]
    assert "structured_acceptance_criteria" in r["error"]


def test_module_qa_no_covers_ac_mt_passes():
    """covers_ac 없는 MT는 ac_verification 없이도 PASS (legacy MT)."""
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [{"ac_id": "AC-1"}],
        "atomic_plan": {"micro_tasks": [{"id": "MT-1", "covers_ac": [], "covers_iqr": []}]},
    }
    r = _check_module_qa_ac_verification(state, "MT-1", None)
    assert r["valid"]


def test_module_qa_covers_iqr_only_mt_passes():
    """covers_iqr만 있는 문서 MT는 ac_verification 면제."""
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [{"ac_id": "AC-1"}],
        "atomic_plan": {
            "micro_tasks": [{"id": "MT-1", "covers_ac": [], "covers_iqr": ["IQR-1"]}]
        },
    }
    r = _check_module_qa_ac_verification(state, "MT-1", None)
    assert r["valid"]


def test_module_qa_covers_ac_without_report_fails():
    """covers_ac 있는데 report 파일 없음 → FAIL."""
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [{"ac_id": "AC-1"}],
        "atomic_plan": {"micro_tasks": [{"id": "MT-1", "covers_ac": ["AC-1"]}]},
    }
    r = _check_module_qa_ac_verification(state, "MT-1", "/nonexistent_report.xml")
    assert not r["valid"]
    assert "report 파일이 없습니다" in r["error"]


def test_module_qa_covers_ac_missing_in_report_fails(tmp_path):
    """report에 ac_verification 블록 없으면 FAIL."""
    report = tmp_path / "qa_no_ac.xml"
    report.write_text(
        "<module_qa_report><mt_id>MT-1</mt_id><verdict>PASS</verdict>"
        "<verification_evidence/></module_qa_report>",
        encoding="utf-8",
    )
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [{"ac_id": "AC-1"}],
        "atomic_plan": {"micro_tasks": [{"id": "MT-1", "covers_ac": ["AC-1"]}]},
    }
    r = _check_module_qa_ac_verification(state, "MT-1", str(report))
    assert not r["valid"]
    assert "ac_verification 블록이 없습니다" in r["error"]


def test_module_qa_all_covers_ac_in_report_passes(tmp_path):
    """report에 covers_ac 모든 AC id가 있으면 PASS."""
    report = tmp_path / "qa_with_ac.xml"
    report.write_text(
        '<module_qa_report><mt_id>MT-1</mt_id><verdict>PASS</verdict>'
        '<verification_evidence/>'
        '<ac_verification>'
        '<criterion ac_id="AC-1" status="PASS" evidence="evidence A"/>'
        '<criterion ac_id="AC-2" status="PASS" evidence="evidence B"/>'
        '</ac_verification>'
        '</module_qa_report>',
        encoding="utf-8",
    )
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [
            {"ac_id": "AC-1"}, {"ac_id": "AC-2"},
        ],
        "atomic_plan": {
            "micro_tasks": [{"id": "MT-1", "covers_ac": ["AC-1", "AC-2"]}]
        },
    }
    r = _check_module_qa_ac_verification(state, "MT-1", str(report))
    assert r["valid"], r


def test_get_mt_covers_helpers():
    """_get_mt_covers_ac / _get_mt_covers_iqr 헬퍼 동작."""
    state = {
        "atomic_plan": {
            "micro_tasks": [
                {"id": "MT-1", "covers_ac": ["AC-1", "AC-2"], "covers_iqr": ["IQR-1"]},
                {"id": "MT-2", "covers_ac": "AC-3, AC-4"},  # 문자열 형태
            ]
        }
    }
    assert _get_mt_covers_ac(state, "MT-1") == ["AC-1", "AC-2"]
    assert _get_mt_covers_iqr(state, "MT-1") == ["IQR-1"]
    assert _get_mt_covers_ac(state, "MT-2") == ["AC-3", "AC-4"]
    assert _get_mt_covers_ac(state, "MT-99") == []


# ---------------------------------------------------------------------------
# AC-5: Dev scope_manifest implemented_tasks 검증
# ---------------------------------------------------------------------------

def test_dev_scope_legacy_state_passes_without_implemented_tasks():
    """legacy state → implemented_tasks 없어도 PASS."""
    r = _validate_implemented_tasks({}, {"micro_tasks": [{"id": "MT-1"}]})
    assert r["valid"]


def test_dev_scope_new_pipeline_missing_implemented_tasks_fails():
    """requirements_tracking.enabled=true + structured_ac 있는데 implemented_tasks 없음 → FAIL."""
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [{"ac_id": "AC-1"}],
        "atomic_plan": {"micro_tasks": [{"id": "MT-1"}]},
    }
    r = _validate_implemented_tasks(state, {"micro_tasks": [{"id": "MT-1"}]})
    assert not r["valid"]
    assert "implemented_tasks" in r["error"]


def test_dev_scope_unknown_ac_id_fails():
    """implemented_ac에 PM에 없는 AC id 참조 → FAIL."""
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [{"ac_id": "AC-1"}],
        "atomic_plan": {"micro_tasks": [{"id": "MT-1"}]},
    }
    manifest = {"micro_tasks": [{
        "id": "MT-1",
        "implemented_tasks": [{
            "mt_id": "MT-1", "implemented_ac": ["AC-99"],
        }],
    }]}
    r = _validate_implemented_tasks(state, manifest)
    assert not r["valid"]
    assert "AC-99" in r["error"]


def test_dev_scope_unknown_mt_id_fails():
    """implemented_tasks의 mt_id가 PM에 없는 id면 FAIL."""
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [{"ac_id": "AC-1"}],
        "atomic_plan": {"micro_tasks": [{"id": "MT-1"}]},
    }
    manifest = {"micro_tasks": [{
        "id": "MT-1",
        "implemented_tasks": [{
            "mt_id": "MT-999", "implemented_ac": ["AC-1"],
        }],
    }]}
    r = _validate_implemented_tasks(state, manifest)
    assert not r["valid"]
    assert "MT-999" in r["error"]


def test_dev_scope_abstract_evidence_fails():
    """implementation_evidence가 단독 추상 문구만이면 FAIL."""
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [{"ac_id": "AC-1"}],
        "atomic_plan": {"micro_tasks": [{"id": "MT-1"}]},
    }
    manifest = {"micro_tasks": [{
        "id": "MT-1",
        "implemented_tasks": [{
            "mt_id": "MT-1", "implemented_ac": ["AC-1"],
            "implementation_evidence": ["구현 완료"],
        }],
    }]}
    r = _validate_implemented_tasks(state, manifest)
    assert not r["valid"]
    assert "추상적" in r["error"]


def test_dev_scope_valid_implemented_tasks_passes():
    """정상 implemented_tasks → PASS."""
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [{"ac_id": "AC-1"}],
        "atomic_plan": {"micro_tasks": [{"id": "MT-1"}]},
    }
    manifest = {"micro_tasks": [{
        "id": "MT-1",
        "implemented_tasks": [{
            "mt_id": "MT-1", "implemented_ac": ["AC-1"],
            "implementation_evidence": [
                "_parse_structured_ac() 함수 추가 + 8개 검증 규칙 구현",
            ],
        }],
    }]}
    r = _validate_implemented_tasks(state, manifest)
    assert r["valid"], r


# ---------------------------------------------------------------------------
# AC-6: Oracle ac_ids 검증
# ---------------------------------------------------------------------------

# tests/oracles/IMP-20260602-1ABE/ 케이스 경로 (실제 PM이 등록한 것)
ORACLE_NORMAL_EXPECTED = (
    Path(__file__).resolve().parent.parent
    / "oracles" / "IMP-20260602-1ABE" / "case_normal_01" / "expected.json"
)
ORACLE_EDGE_EXPECTED = (
    Path(__file__).resolve().parent.parent
    / "oracles" / "IMP-20260602-1ABE" / "case_edge_01" / "expected.json"
)


def _oracle_entries_minimum():
    return [
        {
            "case_id": "C1", "case_kind": "normal",
            "expected_path": str(ORACLE_NORMAL_EXPECTED),
        },
        {
            "case_id": "C2", "case_kind": "edge",
            "expected_path": str(ORACLE_EDGE_EXPECTED),
        },
    ]


def test_oracle_legacy_state_passes_without_ac_ids():
    """legacy state(state=None) → ac_ids 검증 skip."""
    r = _audit_oracle_quality(_oracle_entries_minimum())
    # status는 PASS 또는 FAIL일 수 있지만 ac_ids 관련 failure는 없어야 함
    ac_failures = [f for f in r["failures"] if "ORACLE AC GATE" in f]
    assert not ac_failures


def test_oracle_new_pipeline_missing_ac_ids_fails():
    """requirements_tracking.enabled=true + ac_ids 없음 → FAIL."""
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [{"ac_id": "AC-1"}, {"ac_id": "AC-2"}],
    }
    r = _audit_oracle_quality(_oracle_entries_minimum(), state=state)
    ac_failures = [f for f in r["failures"] if "ac_ids" in f]
    assert ac_failures


def test_oracle_unknown_ac_id_fails():
    """ac_ids에 PM에 없는 AC id → FAIL."""
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [{"ac_id": "AC-1"}],
    }
    entries = _oracle_entries_minimum()
    entries[0]["ac_ids"] = ["AC-99"]
    entries[1]["ac_ids"] = ["AC-1"]
    r = _audit_oracle_quality(entries, state=state)
    ac_failures = [f for f in r["failures"] if "AC-99" in f]
    assert ac_failures


def test_oracle_valid_ac_ids_passes():
    """모든 ac_ids가 PM에 있으면 PASS."""
    state = {
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [{"ac_id": "AC-1"}, {"ac_id": "AC-2"}],
    }
    entries = _oracle_entries_minimum()
    entries[0]["ac_ids"] = ["AC-1"]
    entries[1]["ac_ids"] = ["AC-2"]
    r = _audit_oracle_quality(entries, state=state)
    ac_failures = [f for f in r["failures"] if "ORACLE AC GATE" in f]
    assert not ac_failures


# ---------------------------------------------------------------------------
# AC-7: Codex Review coverage_checks hard gate (회귀 케이스 포함)
# ---------------------------------------------------------------------------

def _all_true_coverage():
    return {f: True for f in CODEX_COVERAGE_CHECK_FIELDS}


def test_codex_legacy_review_without_coverage_checks_passes():
    """coverage_checks 키 자체 없음 → legacy로 PASS."""
    r = _validate_codex_coverage_checks({"result": "ACCEPT"})
    assert r["valid"]


def test_codex_all_coverage_true_passes():
    """7개 필드 모두 true → PASS."""
    r = _validate_codex_coverage_checks({"coverage_checks": _all_true_coverage()})
    assert r["valid"]


def test_codex_coverage_all_ac_reviewed_false_blocks():
    """all_ac_reviewed=false → FAIL."""
    ck = _all_true_coverage()
    ck["all_ac_reviewed"] = False
    r = _validate_codex_coverage_checks({"coverage_checks": ck})
    assert not r["valid"]
    assert any("all_ac_reviewed" in e for e in r["errors"])


def test_codex_coverage_diff_values_false_blocks():
    """diff_values_match_ac=false → FAIL (회귀 1)."""
    ck = _all_true_coverage()
    ck["diff_values_match_ac"] = False
    r = _validate_codex_coverage_checks({"coverage_checks": ck})
    assert not r["valid"]
    assert any("diff_values_match_ac" in e for e in r["errors"])


def test_codex_coverage_tests_assert_false_blocks():
    """tests_assert_core_values=false → FAIL."""
    ck = _all_true_coverage()
    ck["tests_assert_core_values"] = False
    r = _validate_codex_coverage_checks({"coverage_checks": ck})
    assert not r["valid"]
    assert any("tests_assert_core_values" in e for e in r["errors"])


def test_codex_coverage_no_dry_run_false_blocks():
    """no_dry_run_substitution=false → FAIL (회귀 2)."""
    ck = _all_true_coverage()
    ck["no_dry_run_substitution"] = False
    r = _validate_codex_coverage_checks({"coverage_checks": ck})
    assert not r["valid"]
    assert any("no_dry_run_substitution" in e for e in r["errors"])


def test_codex_coverage_no_stale_sha_false_blocks():
    """no_stale_sha=false → FAIL."""
    ck = _all_true_coverage()
    ck["no_stale_sha"] = False
    r = _validate_codex_coverage_checks({"coverage_checks": ck})
    assert not r["valid"]


def test_codex_coverage_no_stale_ci_false_blocks():
    """no_stale_ci_run=false → FAIL."""
    ck = _all_true_coverage()
    ck["no_stale_ci_run"] = False
    r = _validate_codex_coverage_checks({"coverage_checks": ck})
    assert not r["valid"]


def test_codex_coverage_user_facing_korean_false_blocks():
    """user_facing_korean_ok=false → FAIL."""
    ck = _all_true_coverage()
    ck["user_facing_korean_ok"] = False
    r = _validate_codex_coverage_checks({"coverage_checks": ck})
    assert not r["valid"]


def test_codex_coverage_missing_field_blocks():
    """필수 필드 누락 → FAIL."""
    ck = _all_true_coverage()
    del ck["no_dry_run_substitution"]
    r = _validate_codex_coverage_checks({"coverage_checks": ck})
    assert not r["valid"]
    assert any("no_dry_run_substitution" in e and "누락" in e for e in r["errors"])


def test_codex_coverage_check_fields_count():
    """CODEX_COVERAGE_CHECK_FIELDS는 정확히 7개."""
    assert len(CODEX_COVERAGE_CHECK_FIELDS) == 7
    assert "all_ac_reviewed" in CODEX_COVERAGE_CHECK_FIELDS
    assert "diff_values_match_ac" in CODEX_COVERAGE_CHECK_FIELDS
    assert "no_dry_run_substitution" in CODEX_COVERAGE_CHECK_FIELDS


# ---------------------------------------------------------------------------
# 회귀 케이스 (SUN/02:00 vs MON/09:00, dry-run vs 실제 동작)
# ---------------------------------------------------------------------------

def test_regression_sun_02_vs_mon_09_diff_values_mismatch():
    """회귀1: 사용자 AC=MON/09:00, diff=SUN/02:00 → diff_values_match_ac=false 시
    QA 차단됨을 검증. _validate_codex_coverage_checks가 그 false를 잡아낸다.
    """
    ck = _all_true_coverage()
    ck["diff_values_match_ac"] = False
    review_data = {
        "schema_version": 1,
        "stage": "code",
        "result": "ACCEPT",
        "reviewer": "qa-test",
        "review_model": "GPT-5.5",
        "coverage_checks": ck,
        # 회귀를 설명하는 메타 정보:
        "criteria_review": [
            {
                "ac_id": "AC-1",
                "status": "FAIL",
                "blocking": True,
                "reason": "사용자 AC=MON/09:00, diff=SUN/02:00 불일치",
            }
        ],
    }
    cov_r = _validate_codex_coverage_checks(review_data)
    assert not cov_r["valid"]
    assert any("diff_values_match_ac" in e for e in cov_r["errors"])


def test_regression_dry_run_substitution_for_real_move():
    """회귀2: 사용자 AC=실제 파일 이동, 테스트=dry-run만 → no_dry_run_substitution=false → FAIL."""
    ck = _all_true_coverage()
    ck["no_dry_run_substitution"] = False
    review_data = {
        "schema_version": 1,
        "stage": "code",
        "result": "ACCEPT",
        "reviewer": "qa-test",
        "review_model": "GPT-5.5",
        "coverage_checks": ck,
        "criteria_review": [
            {
                "ac_id": "AC-2",
                "status": "FAIL",
                "blocking": True,
                "reason": "사용자 AC=실제 파일 이동, 테스트는 dry-run만 검증",
            }
        ],
    }
    cov_r = _validate_codex_coverage_checks(review_data)
    assert not cov_r["valid"]
    assert any("no_dry_run_substitution" in e for e in cov_r["errors"])


# ---------------------------------------------------------------------------
# AC-4: request-accept AC 충족표
# ---------------------------------------------------------------------------

def test_ac_table_legacy_returns_none():
    """legacy state(structured_ac 없음) → 충족표 None."""
    assert _build_ac_fulfillment_table({}) is None
    assert _build_ac_fulfillment_table({"structured_acceptance_criteria": []}) is None


def test_ac_table_all_pending_when_no_impl():
    """structured AC만 있고 implementation/qa 없으면 모두 PENDING."""
    state = {
        "structured_acceptance_criteria": [
            {"ac_id": "AC-1", "requirement": "조건 A", "user_visible": True, "must_verify": True},
            {"ac_id": "AC-2", "requirement": "조건 B", "user_visible": True, "must_verify": True},
        ],
        "atomic_plan": {"micro_tasks": []},
        "module_gates": {"modules": {}},
    }
    table = _build_ac_fulfillment_table(state)
    assert table is not None
    assert len(table) == 2
    assert all(e["result"] == "PENDING" for e in table)


def test_ac_table_format_user_visible_only_at_top():
    """user_visible=True는 [요구사항 충족표] 섹션, False는 [자동 검증 요약] 섹션."""
    state = {
        "structured_acceptance_criteria": [
            {"ac_id": "AC-1", "requirement": "유저 조건", "user_visible": True},
            {"ac_id": "AC-2", "requirement": "내부 조건", "user_visible": False},
        ],
        "atomic_plan": {"micro_tasks": []},
        "module_gates": {"modules": {}},
    }
    table = _build_ac_fulfillment_table(state)
    out = _format_ac_fulfillment_output(table)
    assert "[요구사항 충족표]" in out
    assert "[자동 검증 요약]" in out
    # 충족표 부분에 AC-1이 자동 검증 요약보다 먼저 등장
    pos_table = out.find("[요구사항 충족표]")
    pos_summary = out.find("[자동 검증 요약]")
    pos_ac1 = out.find("AC-1:")
    pos_ac2 = out.find("AC-2:")
    assert pos_table < pos_ac1 < pos_summary
    assert pos_summary < pos_ac2


def test_ac_table_mobile_format_no_long_lines():
    """모바일 친화 형식: 한 줄이 너무 길지 않게 줄바꿈."""
    state = {
        "structured_acceptance_criteria": [
            {
                "ac_id": "AC-1",
                "requirement": "x" * 200,  # 매우 긴 requirement
                "user_visible": True,
            }
        ],
        "atomic_plan": {"micro_tasks": []},
        "module_gates": {"modules": {}},
    }
    table = _build_ac_fulfillment_table(state)
    out = _format_ac_fulfillment_output(table)
    # 가장 긴 줄도 100자 미만이 되도록 줄바꿈됨 (포맷 헤더 라인 제외 본문)
    body_lines = [
        line for line in out.split("\n")
        if line.startswith("  ") and "x" in line
    ]
    assert body_lines
    for line in body_lines:
        assert len(line) < 100, f"line too long: {len(line)}: {line[:50]}"


def test_ac_table_linked_mt_correct():
    """충족표가 covers_ac 기반으로 linked_mt를 정확히 추출."""
    state = {
        "structured_acceptance_criteria": [
            {"ac_id": "AC-1", "requirement": "A", "user_visible": True},
            {"ac_id": "AC-2", "requirement": "B", "user_visible": True},
        ],
        "atomic_plan": {
            "micro_tasks": [
                {"id": "MT-1", "covers_ac": ["AC-1"]},
                {"id": "MT-2", "covers_ac": ["AC-1", "AC-2"]},
            ]
        },
        "module_gates": {"modules": {}},
    }
    table = _build_ac_fulfillment_table(state)
    ac1 = next(e for e in table if e["ac_id"] == "AC-1")
    ac2 = next(e for e in table if e["ac_id"] == "AC-2")
    assert set(ac1["linked_mt"]) == {"MT-1", "MT-2"}
    assert ac2["linked_mt"] == ["MT-2"]


if __name__ == "__main__":
    # Smoke test direct run
    test_pm_ac_missing_in_step_plan_returns_empty()
    test_pm_covers_ac_missing_fails()
    test_pm_abstract_ac_text_fails()
    test_codex_all_coverage_true_passes()
    test_codex_coverage_diff_values_false_blocks()
    test_codex_coverage_no_dry_run_false_blocks()
    test_codex_coverage_check_fields_count()
    test_ac_table_legacy_returns_none()
    print("[SELF-VERIFY] OK")
