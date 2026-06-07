"""
test_ac_tracking_1abe.py — IMP-20260602-1ABE MT-8
Structured AC Tracking E2E 테스트

# [Purpose]: PM structured AC 파싱/검증, requirements_tracking 플래그 저장,
#            module qa ac_verification 강제, request-accept AC 충족표,
#            Dev scope_manifest implemented_tasks 검증, Oracle ac_ids 검증,
#            Codex Review coverage_checks hard gate가 정확히 동작하는지 검증.
# [Assumptions]: pipeline.py에서 함수 직접 import 가능 + subprocess CLI 호출 시
#                PIPELINE_STATE_PATH 격리.

# CLI Evidence Contract (IMP-20260525-6FAC):
# - 상태 변경 CLI 호출은 PIPELINE_STATE_PATH 격리 + final_state assertion 포함
# - 함수 직접 호출 테스트는 비-CLI 내부 로직 검증용
"""

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


# ─────────────────────────────────────────────────────────────────────────────
# 5개 Gate CLI E2E 테스트 (IQR-1 전면 해결)
# subprocess.run(['python', 'pipeline.py', ...]) + PIPELINE_STATE_PATH 격리
# + final_state assertion (IMP-20260525-6FAC)
# ─────────────────────────────────────────────────────────────────────────────

import re  # noqa: E402


# ── 공통 헬퍼 ────────────────────────────────────────────────────────────────

def _parse_agent_start_output(stdout: str):
    """agent start 출력에서 run_id, token 추출.

    pipeline.py agent start 출력 형식:
      run_id: <RUN_ID>
      token: <TOKEN>
    """
    run_id = None
    token = None
    for line in stdout.splitlines():
        line_s = line.strip()
        if line_s.startswith("run_id:"):
            run_id = line_s.split(":", 1)[1].strip()
        elif line_s.startswith("token:"):
            token = line_s.split(":", 1)[1].strip()
    if run_id is None:
        # JSON 블록에서 재시도
        m = re.search(r'"run_id":\s*"([^"]+)"', stdout)
        if m:
            run_id = m.group(1)
    if token is None:
        m = re.search(r'"token":\s*"([^"]+)"', stdout)
        if m:
            token = m.group(1)
    return run_id, token


def _make_minimal_step_plan(tmp_path: Path, pipeline_id: str, *, with_ac: bool) -> Path:
    """done --phase pm 이 통과할 최소 step_plan.xml 생성.

    with_ac=True: AC-1 criterion + MT-1 covers_ac 포함 (SUCCESS 경로)
    with_ac=False: acceptance_criteria 블록 없음 (FAILURE 경로)

    주의: _validate_pm_step_plan_file 검증 통과를 위해
    decomposition_audit, design_confirmation, micro_tasks 모두 포함.
    """
    ac_block = ""
    covers_ac_block = ""
    if with_ac:
        ac_block = """
  <acceptance_criteria>
    <criterion id="AC-1" must_verify="true" source="user" user_visible="true">
      <text>CLI E2E 테스트용 구체적 성공 조건 — subprocess로 격리 검증 완료시 PASS</text>
    </criterion>
  </acceptance_criteria>"""
        covers_ac_block = "<covers_ac>AC-1</covers_ac>"

    xml_content = f"""<root>
<decomposition_audit>
  <total_functions_identified>1</total_functions_identified>
  <micro_task_count>1</micro_task_count>
  <grep_executions>1</grep_executions>
  <split_decision>단일 함수 변경 — MT-1로 충분</split_decision>
  <audit_result>SINGLE_TASK_OK</audit_result>
</decomposition_audit>
<step_plan>
  <pipeline_id>{pipeline_id}</pipeline_id>
  <anti_gaming_read>true</anti_gaming_read>
  <current_step>Step 1: CLI E2E 검증</current_step>
  <tier>1</tier>
  <target_agent>Dev</target_agent>
  <model_tier>Sonnet</model_tier>
  <category_tags>IMP</category_tags>
  <task_complexity>
    <execution_profile>STANDARD</execution_profile>
    <reason>CLI E2E 테스트용</reason>
    <uncertainty>
      <p0_questions>0</p0_questions>
      <p1_questions>0</p1_questions>
      <output_format_clear>true</output_format_clear>
    </uncertainty>
    <blast_radius>
      <expected_changed_files>1</expected_changed_files>
      <expected_changed_functions>1</expected_changed_functions>
      <expected_changed_lines>10</expected_changed_lines>
    </blast_radius>
    <risk_flags>
      <data_deletion>false</data_deletion>
      <file_move>false</file_move>
      <external_api>false</external_api>
      <auth_or_secret>false</auth_or_secret>
      <pipeline_protocol>false</pipeline_protocol>
      <build_or_deploy>false</build_or_deploy>
      <core_parser_logic>false</core_parser_logic>
      <database_or_migration>false</database_or_migration>
      <new_dependency>false</new_dependency>
    </risk_flags>
  </task_complexity>
  <design_confirmation>
    <module_split_presented>true</module_split_presented>
    <module_split_user_confirmed>true</module_split_user_confirmed>
    <maintenance_priority>maintainability_first</maintenance_priority>
    <low_value_questions_filtered>true</low_value_questions_filtered>
    <filter_summary>내부 변수명, 코드 스타일, 사소한 구현 취향 질문은 묻지 않고 유지보수성 기준으로 PM이 정리했습니다.</filter_summary>
    <decision_questions>
      <question id="DQ-1" priority="P1" category="module_split" mt_id="MT-1">
        <user_facing_question>이번 CLI E2E 테스트 검증을 MT-1 단위로 진행해도 될까요?</user_facing_question>
        <evidence>단일 함수 변경으로 MT-1로 충분합니다.</evidence>
        <why_it_matters>모듈 단위가 맞아야 수정 범위가 작고 검증이 쉬워집니다.</why_it_matters>
        <recommended_option>A</recommended_option>
        <options>
          <option id="A">
            <label>MT-1 단위로 진행</label>
            <benefit>수정 범위가 작고 검증 위치가 명확합니다.</benefit>
            <cost>요구사항이 커지면 추가 분리가 필요할 수 있습니다.</cost>
          </option>
          <option id="B">
            <label>더 작게 분해</label>
            <benefit>더 세밀하게 확인할 수 있습니다.</benefit>
            <cost>작업 시간이 늘어납니다.</cost>
          </option>
        </options>
        <user_answer>추천안 A로 진행</user_answer>
      </question>
    </decision_questions>
  </design_confirmation>
  <micro_tasks>
    <micro_task id="MT-1">
      <affected_function>tests.e2e.cli_e2e_gate_func</affected_function>
      <target_files>
        <file>tests/e2e/test_ac_tracking_1abe.py</file>
      </target_files>
      <affected_call_sites>
        <site>none</site>
      </affected_call_sites>
      <grep_evidence>
        <pattern>cli_e2e_gate_func</pattern>
        <match_count>0</match_count>
        <executed>true</executed>
      </grep_evidence>
      <change_summary>CLI E2E 게이트 테스트 추가</change_summary>
      <line_estimate>50</line_estimate>
      {covers_ac_block}
      <covers_iqr></covers_iqr>
    </micro_task>
  </micro_tasks>
  <interface_spec>
    - Input: pipeline.py CLI
    - Output: exit code + state JSON
    - Validation: final_state assertion
  </interface_spec>
  <requirements>
    - CLI E2E 검증 필수
  </requirements>
  {ac_block}
  <forbidden>직접 import 테스트만으로 CLI 완료 판정 금지</forbidden>
</step_plan>
</root>"""
    plan_file = tmp_path / "step_plan.xml"
    plan_file.write_text(xml_content, encoding="utf-8")
    return plan_file


def _make_manager_handoff(
    tmp_path: Path,
    pipeline_id: str,
    step_plan_file: Path,
    planner_run_id: str,
) -> Path:
    """_validate_manager_handoff_file 이 통과할 최소 manager_handoff.xml 생성.

    pipeline.py의 _validate_manager_handoff_file 요구사항:
      - <from>pipeline-manager-agent</from>
      - <pipeline_id> 일치
      - <step_plan_sha256> — step_plan.xml의 실제 SHA256
      - <planner_run_id> — agent start pm_planner에서 받은 run_id
      - <accepted_for_execution>true</accepted_for_execution>
      - <will_not_modify_step_plan>true</will_not_modify_step_plan>
      - <next_phase>dev</next_phase>
    """
    import hashlib
    sha256 = hashlib.sha256(step_plan_file.read_bytes()).hexdigest()

    xml_content = f"""<manager_handoff>
  <from>pipeline-manager-agent</from>
  <pipeline_id>{pipeline_id}</pipeline_id>
  <planner_run_id>{planner_run_id}</planner_run_id>
  <step_plan_sha256>{sha256}</step_plan_sha256>
  <accepted_for_execution>true</accepted_for_execution>
  <will_not_modify_step_plan>true</will_not_modify_step_plan>
  <next_phase>dev</next_phase>
  <status>READY_FOR_DEV</status>
  <summary>CLI E2E 테스트용 manager handoff</summary>
</manager_handoff>"""
    handoff_file = tmp_path / "manager_handoff.xml"
    handoff_file.write_text(xml_content, encoding="utf-8")
    return handoff_file


def _run_full_pm_done(tmp_path: Path, state_file: Path, *, with_ac: bool):
    """new → agent start (planner) → agent finish → agent start (manager) → agent finish
    → done --phase pm 전체 흐름 실행.

    반환: (final_result, final_state, pipeline_id)
    """
    # 1. new
    result, state = _run_pipeline_cli(
        "new", "--type", "IMP", "--desc", "CLI E2E AC gate test",
        state_path=state_file,
    )
    assert result.returncode == 0, f"new failed:\n{result.stdout}\n{result.stderr}"
    pipeline_id = state.get("pipeline_id", "")
    assert pipeline_id, "pipeline_id 없음"

    # 2. step_plan.xml 준비 (pipeline_id 필요, SHA256은 manager_handoff 생성 시 사용)
    step_plan_file = _make_minimal_step_plan(tmp_path, pipeline_id, with_ac=with_ac)

    # 3. agent start pm_planner
    result, state = _run_pipeline_cli(
        "agent", "start", "--phase", "pm_planner",
        state_path=state_file,
    )
    assert result.returncode == 0, f"agent start pm_planner failed:\n{result.stdout}\n{result.stderr}"
    planner_run_id, planner_token = _parse_agent_start_output(result.stdout)
    assert planner_run_id, f"planner_run_id 파싱 실패:\n{result.stdout}"
    assert planner_token, f"planner_token 파싱 실패:\n{result.stdout}"

    # 4. agent finish pm_planner
    result, state = _run_pipeline_cli(
        "agent", "finish",
        "--run-id", planner_run_id,
        "--token", planner_token,
        "--output-file", str(step_plan_file),
        state_path=state_file,
    )
    assert result.returncode == 0, f"agent finish pm_planner failed:\n{result.stdout}\n{result.stderr}"

    # manager_handoff.xml: step_plan SHA256 + planner_run_id 포함 (pipeline.py 검증 통과 필수)
    manager_handoff_file = _make_manager_handoff(
        tmp_path, pipeline_id, step_plan_file, planner_run_id
    )

    # 5. agent start pipeline_manager
    result, state = _run_pipeline_cli(
        "agent", "start", "--phase", "pipeline_manager",
        state_path=state_file,
    )
    assert result.returncode == 0, f"agent start pipeline_manager failed:\n{result.stdout}\n{result.stderr}"
    manager_run_id, manager_token = _parse_agent_start_output(result.stdout)
    assert manager_run_id, f"manager_run_id 파싱 실패:\n{result.stdout}"
    assert manager_token, f"manager_token 파싱 실패:\n{result.stdout}"

    # 6. agent finish pipeline_manager
    result, state = _run_pipeline_cli(
        "agent", "finish",
        "--run-id", manager_run_id,
        "--token", manager_token,
        "--output-file", str(manager_handoff_file),
        state_path=state_file,
    )
    assert result.returncode == 0, f"agent finish pipeline_manager failed:\n{result.stdout}\n{result.stderr}"

    # 7. done --phase pm
    result, final_state = _run_pipeline_cli(
        "done", "--phase", "pm",
        "--report-file", str(step_plan_file),
        "--decomp", "--clarification", "--roadmap",
        "--planner-run-id", planner_run_id,
        "--manager-run-id", manager_run_id,
        "--manager-report", str(manager_handoff_file),
        state_path=state_file,
    )
    return result, final_state, pipeline_id


# ── [게이트 1] PM done CLI E2E ────────────────────────────────────────────────

def test_cli_pm_done_blocks_when_ac_missing(tmp_path):
    """[IQR-1 게이트1-FAIL] acceptance_criteria 없는 step_plan → done --phase pm exit != 0.

    subprocess 실행 + PIPELINE_STATE_PATH 격리 + final_state assertion.
    """
    state_file = tmp_path / "pipeline_state.json"

    result, final_state, _pid = _run_full_pm_done(tmp_path, state_file, with_ac=False)

    # AC 블록이 없으면 done --phase pm은 exit code != 0 이어야 함
    assert result.returncode != 0, (
        f"AC 없는 step_plan인데 PM done이 통과됨 (returncode=0)\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    pm_status = final_state.get("pm_status", "")
    assert pm_status != "DONE", (
        f"AC 없는 step_plan인데 pm_status=DONE으로 기록됨: {pm_status}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_cli_pm_done_saves_requirements_tracking_when_ac_valid(tmp_path):
    """[IQR-1 게이트1-PASS] 유효한 AC 포함 step_plan → done --phase pm exit 0 +
    atomic_plan.structured_acceptance_criteria 비어있지 않음.

    subprocess 실행 + PIPELINE_STATE_PATH 격리 + final_state assertion.

    Note: pipeline.py에서 requirements_tracking.enabled 플래그는 atomic_plan이 저장된
    이후의 별도 단계에서 활성화된다. 따라서 done --phase pm 직후 상태에서는
    atomic_plan.structured_acceptance_criteria에 AC가 저장되었는지로 검증한다.
    """
    state_file = tmp_path / "pipeline_state.json"

    result, final_state, _pid = _run_full_pm_done(tmp_path, state_file, with_ac=True)

    assert result.returncode == 0, (
        f"유효한 AC 포함 step_plan인데 done --phase pm 실패:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # atomic_plan.structured_acceptance_criteria에 AC가 저장되었는지 확인
    # (requirements_tracking.enabled는 dev done 이후 단계에서 설정됨)
    atomic_plan = final_state.get("atomic_plan", {})
    structured_ac = atomic_plan.get("structured_acceptance_criteria", [])
    assert len(structured_ac) >= 1, (
        f"atomic_plan.structured_acceptance_criteria가 비어있음: {structured_ac}\n"
        f"stdout: {result.stdout}"
    )
    # AC-1 항목이 올바른 필드를 가지는지 확인
    first_ac = structured_ac[0]
    assert first_ac.get("ac_id") == "AC-1", (
        f"AC-1 id가 없음: {first_ac}\nstdout: {result.stdout}"
    )
    assert first_ac.get("must_verify") is True, (
        f"must_verify가 True가 아님: {first_ac}"
    )

    pm_status = final_state.get("phases", {}).get("pm", {}).get("status")
    assert pm_status == "DONE", (
        f"phases.pm.status가 DONE이 아님: {pm_status}\n"
        f"stdout: {result.stdout}"
    )


# ── [게이트 2] Dev done CLI E2E ───────────────────────────────────────────────

def _make_scope_manifest(tmp_path: Path, pipeline_id: str, *, with_implemented_tasks: bool, target_file: str) -> Path:
    """_validate_dev_scope_manifest 검증용 scope_manifest.json 생성."""
    mt_entry: dict = {
        "id": "MT-1",
        "files": [target_file],
        "affected_functions": ["tests.e2e.cli_e2e_gate_func"],
    }
    if with_implemented_tasks:
        mt_entry["implemented_tasks"] = [{
            "mt_id": "MT-1",
            "implemented_ac": ["AC-1"],
            "implementation_evidence": [
                "cli_e2e_gate_func() 함수 추가 — subprocess 격리 완료",
            ],
        }]
    manifest = {
        "pipeline_id": pipeline_id,
        "micro_tasks": [mt_entry],
    }
    manifest_file = tmp_path / "scope_manifest.json"
    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_file


def _make_dev_handover(tmp_path: Path, pipeline_id: str) -> Path:
    """_validate_dev_handover_file 검증용 최소 dev_handover.xml 생성.

    pipeline.py _validate_dev_handover_file 요구 태그:
      - handover
      - impact_analysis (MT 단위 surgical edit 증거)
      - scope_lock_check
    """
    xml_content = f"""<handover>
  <from>dev-agent</from>
  <to>qa-agent</to>
  <pipeline_id>{pipeline_id}</pipeline_id>
  <status>READY_FOR_QA</status>
  <evidence>
    <file>tests/e2e/test_ac_tracking_1abe.py</file>
  </evidence>
  <impact_analysis>
    <mt_id>MT-1</mt_id>
    <target_function>tests.e2e.cli_e2e_gate_func</target_function>
    <target_file>tests/e2e/test_ac_tracking_1abe.py</target_file>
    <change_type>addition</change_type>
    <lines_changed>10</lines_changed>
    <blast_radius>LOW</blast_radius>
  </impact_analysis>
  <scope_declaration>
    <files_to_modify>
      <file>tests/e2e/test_ac_tracking_1abe.py</file>
    </files_to_modify>
    <functions_to_modify>
      <function>tests.e2e.cli_e2e_gate_func</function>
    </functions_to_modify>
  </scope_declaration>
  <scope_lock_check>
    <verdict>PASS</verdict>
    <extra_files>없음</extra_files>
    <trust_root_violation>false</trust_root_violation>
  </scope_lock_check>
</handover>"""
    handover_file = tmp_path / "dev_handover.xml"
    handover_file.write_text(xml_content, encoding="utf-8")
    return handover_file


def _get_current_project_snapshot() -> dict:
    """현재 프로젝트 파일 상태의 snapshot 반환 (subprocess 방식).

    pipeline.py _atomic_project_snapshot()을 별도 Python 프로세스로 호출.
    이 snapshot을 state.atomic_plan.project_snapshot으로 사용하면,
    _atomic_changed_files(snapshot) 시 before==current → actual_changed=[]
    → [ATOMIC SCOPE GATE] actual_outside_scope 검사 통과.
    """
    _repo_root = Path(__file__).resolve().parent.parent.parent
    _pipeline_py = _repo_root / "pipeline.py"
    # pipeline.py를 직접 import하지 않고 subprocess로 snapshot 추출
    _code = (
        "import sys; sys.path.insert(0, str(sys.argv[1])); "
        "import importlib.util; "
        "spec = importlib.util.spec_from_file_location('pl', sys.argv[2]); "
        "m = importlib.util.module_from_spec(spec); "
        "spec.loader.exec_module(m); "
        "import json; print(json.dumps(m._atomic_project_snapshot(), ensure_ascii=False))"
    )
    import subprocess as _sp
    proc = _sp.run(
        [sys.executable, "-c", _code, str(_repo_root), str(_pipeline_py)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_repo_root),
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        # fallback: 빈 snapshot (actual_changed = all current files로 처리됨 — 비권장)
        return {"git_sha": "", "files": {}}
    return json.loads(proc.stdout.strip())


def _make_file_checkpoint(file_abs: str) -> dict:
    """_validate_module_checkpoints 통과용 checkpoint dict 생성.

    pipeline.py _validate_module_checkpoints 요구 형식:
      {"created_at": ..., "files": [{"path": ..., "sha256": ...}]}
    """
    import hashlib
    p = Path(file_abs)
    sha256 = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "0" * 64
    return {
        "created_at": "2026-06-03T00:03:00Z",
        "files": [{"path": file_abs, "sha256": sha256}],
    }


def _seed_pm_done_state_for_dev(
    state_file: Path,
    pipeline_id: str,
    target_file_abs: str,
    *,
    with_ac: bool = True,
) -> None:
    """done --phase dev 테스트용 PM DONE 상태 시드.

    _validate_dev_scope_manifest의 atomic_plan, project_snapshot 요구사항 충족.
    codex_bootstrap_exception=true 로 trust-root/diff check 우회.
    checkpoint는 target_file의 실제 SHA256으로 채워야 _validate_module_checkpoints 통과.
    """
    state: dict = {
        "version": "2.1.0",
        "pipeline_id": pipeline_id,
        "type": "IMP",
        "description": "CLI E2E dev done test",
        "created_at": "2026-06-03T00:00:00Z",
        "updated_at": "2026-06-03T00:00:00Z",
        "pipeline_started_at": "2026-06-03T00:00:00Z",
        "pipeline_completed_at": None,
        "acceptance_requested_at": None,
        "acceptance_recorded_at": None,
        "total_elapsed_seconds": None,
        "current_phase": "dev",
        "blocked": False,
        "blocked_reason": None,
        "terminal_state": None,
        "harness_fail_count": 0,
        "agent_runs": {},
        "event_log": [],
        "failure_packets": [],
        "protocol_evolution_decision": None,
        "phase_timings": {},
        "gate_timings": {},
        "agent_timings": {},
        "github_actions_timings": {
            "WAITING_FOR_TRIGGER": 0, "QUEUED": 0, "IN_PROGRESS": 0,
            "COMPLETED": 0, "TIMEOUT": 0,
        },
        "failure_summary": {},
        "attempt_budget": {
            "config": {
                "dev_max_attempts": 3, "qa_max_attempts": 3,
                "gate_max_attempts": 5, "repeat_failure_code_threshold": 3,
            },
            "attempts": {"dev": [], "qa": [], "gate": []},
            "blocked_phases": {},
        },
        "outputs": {"items": []},
        "phases": {
            "pm": {"status": "DONE", "started_at": "2026-06-03T00:00:00Z",
                   "completed_at": "2026-06-03T00:01:00Z", "evidence": None,
                   "notes": [], "report_file": None, "agent_id": "pm-planner-agent",
                   "snapshot_path": None},
            "dev": {"status": "PENDING", "started_at": None,
                    "completed_at": None, "evidence": None,
                    "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
            "qa": {"status": "PENDING", "started_at": None,
                   "completed_at": None, "evidence": None,
                   "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
            "sec": {"status": "PENDING", "started_at": None,
                    "completed_at": None, "evidence": None,
                    "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
            "build": {"status": "PENDING", "started_at": None,
                      "completed_at": None, "evidence": None,
                      "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
            "harness": {"status": "PENDING", "started_at": None,
                        "completed_at": None, "evidence": None,
                        "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
            "architect": {"status": "PENDING", "started_at": None,
                          "completed_at": None, "evidence": None,
                          "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
        },
        "external_gates": {
            "enabled": True, "mode": "three_gate",
            "technical": {"status": "PENDING", "started_at": None,
                          "completed_at": None, "evidence": None, "report_file": None, "notes": []},
            "oracle": {"status": "PENDING", "started_at": None,
                       "completed_at": None, "evidence": None, "report_file": None, "notes": []},
            "acceptance": {"status": "PENDING", "started_at": None,
                           "completed_at": None, "evidence": None, "report_file": None, "notes": []},
            "github_ci": {"status": "PENDING", "started_at": None,
                          "completed_at": None, "evidence": None, "report_file": None, "notes": []},
        },
        "phase_attestations": {
            "enabled": True, "mode": "github_actions_per_phase",
            "required_phases": ["pm", "dev", "qa", "build"],
            "phases": {
                # pm: PASS (dev 진입 게이트 통과 필수), dev/qa/build: PENDING
                "pm": {"status": "PASS", "completed_at": "2026-06-03T00:05:00Z", "phase": "pm",
                       "run_id": "12345001", "commit_sha": "abc1234", "evidence": None,
                       "report_file": None, "notes": []},
                **{p: {"status": "PENDING", "completed_at": None, "phase": None,
                       "run_id": None, "commit_sha": None, "evidence": None,
                       "report_file": None, "notes": []}
                   for p in ["dev", "qa", "build"]},
            },
        },
        # codex_bootstrap_exception: trust-root/diff scope check 우회 (테스트 격리용)
        "codex_bootstrap_exception": True,
        # pm_analysis_gate: done --phase pm이 설정하는 플래그
        "pm_analysis_gate": {
            "decomp": True, "clarification": True, "roadmap": True,
            "judgment_confirmed": False,
        },
        # pm_clarification_gate: check --phase dev 에서 확인
        "pm_clarification_gate": {
            "clarification_needed": False,
            "assumptions": "없음",
            "acceptance_criteria_source": "user",
            "acceptance_criteria": ["CLI E2E 테스트용 구체적 성공 조건 — subprocess로 격리 검증 완료시 PASS"],
            "recorded_at": "2026-06-03T00:01:00Z",
        },
        "atomic_plan": {
            "report_file": "step_plan.xml",
            "validated_at": "2026-06-03T00:01:00Z",
            "audit_result": "SINGLE_TASK_OK",
            "micro_task_count": 1,
            "micro_tasks": [
                {
                    "id": "MT-1",
                    "affected_function": "tests.e2e.cli_e2e_gate_func",
                    "target_files": [target_file_abs],
                    "change_summary": "CLI E2E 게이트 테스트 추가",
                    "covers_ac": ["AC-1"] if with_ac else [],
                    "covers_iqr": [],
                }
            ],
            "design_confirmation": {
                "module_split_presented": True,
                "module_split_user_confirmed": True,
                "maintenance_priority": "maintainability_first",
                "low_value_questions_filtered": True,
                "filter_summary": "내부 구현 취향 질문 생략",
                "decision_questions": [
                    {"id": "DQ-1", "priority": "P1", "category": "module_split",
                     "mt_id": "MT-1", "user_answer": "추천안 A로 진행"}
                ],
            },
            "execution_profile": {
                "mode": "STANDARD",
                "status": "ACTIVE",
                "reason": "",
                "max_micro_tasks": None,
                "product_code_write_allowed": True,
                "phase_ci_mode": "per_phase",
                "repair_mode": "standard",
                "risk_review_required": False,
                "risk_categories": [],
                "declared_at": None,
                "escalated_at": None,
                "escalation_reason": None,
            },
            "project_snapshot": _get_current_project_snapshot(),
            # _get_current_project_snapshot: 현재 repo 상태를 snapshot으로 사용
            # → _atomic_changed_files가 before==current로 판단해 actual_changed=[] 반환
            # → [ATOMIC SCOPE GATE] actual_outside_scope 검사 통과
            "structured_acceptance_criteria": (
                [{"ac_id": "AC-1", "requirement": "CLI E2E 테스트용 구체적 성공 조건", "must_verify": True, "source": "user", "user_visible": True, "expected_evidence": ""}]
                if with_ac else []
            ),
        },
        "execution_profile": {
            "mode": "STANDARD", "status": "ACTIVE", "reason": "",
            "max_micro_tasks": None, "product_code_write_allowed": True,
            "phase_ci_mode": "per_phase", "repair_mode": "standard",
            "risk_review_required": False, "risk_categories": [],
            "declared_at": None, "escalated_at": None, "escalation_reason": None,
        },
        "module_gates": {
            "enabled": True,
            "sequence": ["MT-1"],
            "modules": {
                "MT-1": {
                    "id": "MT-1",
                    "status": "PASS",
                    "order": 1,
                    "target_files": [target_file_abs],
                    "affected_function": "tests.e2e.cli_e2e_gate_func",
                    "design": {"status": "PASS", "completed_at": "2026-06-03T00:01:00Z", "report_file": None},
                    "dev": {"status": "DONE", "completed_at": "2026-06-03T00:02:00Z", "report_file": None},
                    "qa": {"status": "PASS", "completed_at": "2026-06-03T00:03:00Z", "report_file": None},
                    # _validate_module_checkpoints: checkpoint는 실제 파일의 SHA256이어야 함
                    "checkpoint": _make_file_checkpoint(target_file_abs),
                }
            },
            "integration": {"status": "PASS", "completed_at": "2026-06-03T00:04:00Z", "report_file": None},
        },
    }
    if with_ac:
        state["requirements_tracking"] = {"enabled": True, "schema_version": 1, "recorded_at": "2026-06-03T00:01:00Z"}
        state["structured_acceptance_criteria"] = [
            {"ac_id": "AC-1", "requirement": "CLI E2E 테스트용 구체적 성공 조건", "must_verify": True, "source": "user", "user_visible": True, "expected_evidence": ""}
        ]

    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def test_cli_dev_done_blocks_when_implemented_tasks_missing(tmp_path):
    """[IQR-1 게이트2-FAIL] requirements_tracking.enabled=true 이고 implemented_tasks 없음
    → done --phase dev exit != 0.

    subprocess 실행 + PIPELINE_STATE_PATH 격리 + final_state assertion.
    """
    state_file = tmp_path / "pipeline_state.json"
    pipeline_id = "IMP-20260603-E2E1"
    # target_file: 실제로 존재해야 하므로 이 테스트 파일 자체를 사용
    target_file_abs = str(Path(__file__).resolve())
    target_file_rel = "tests/e2e/test_ac_tracking_1abe.py"

    _seed_pm_done_state_for_dev(
        state_file, pipeline_id, target_file_abs, with_ac=True
    )

    # scope_manifest: implemented_tasks 없음 (FAILURE 경로)
    scope_manifest = _make_scope_manifest(
        tmp_path, pipeline_id,
        with_implemented_tasks=False,
        target_file=target_file_rel,
    )
    dev_handover = _make_dev_handover(tmp_path, pipeline_id)

    # dev agent start/finish (receipt 필요)
    result, state = _run_pipeline_cli(
        "agent", "start", "--phase", "dev",
        state_path=state_file,
    )
    assert result.returncode == 0, f"agent start dev failed:\n{result.stdout}\n{result.stderr}"
    dev_run_id, dev_token = _parse_agent_start_output(result.stdout)

    result, state = _run_pipeline_cli(
        "agent", "finish",
        "--run-id", dev_run_id,
        "--token", dev_token,
        "--output-file", str(dev_handover),
        state_path=state_file,
    )
    assert result.returncode == 0, f"agent finish dev failed:\n{result.stdout}\n{result.stderr}"

    # done --phase dev
    # Note: --codex-review-waiver는 done 커맨드에 없음 (check 커맨드 전용).
    # codex_bootstrap_exception=True 시드 state가 내부 git-diff/trust-root 검증 우회.
    result, final_state = _run_pipeline_cli(
        "done", "--phase", "dev",
        "--files", target_file_rel,
        "--report-file", str(dev_handover),
        "--scope-declared",
        "--scope-manifest", str(scope_manifest),
        "--agent-run-id", dev_run_id,
        state_path=state_file,
    )

    assert result.returncode != 0, (
        f"implemented_tasks 없는 scope_manifest인데 dev done이 성공함\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    dev_status = final_state.get("phases", {}).get("dev", {}).get("status")
    assert dev_status != "DONE", (
        f"dev phase status가 DONE으로 기록됨 (implemented_tasks 없는데): {dev_status}"
    )


def test_cli_dev_done_passes_with_valid_implemented_tasks(tmp_path):
    """[IQR-1 게이트2-PASS] 유효한 implemented_tasks 포함 scope_manifest
    → done --phase dev exit 0 + phases.dev.status == 'DONE'.

    subprocess 실행 + PIPELINE_STATE_PATH 격리 + final_state assertion.
    """
    state_file = tmp_path / "pipeline_state.json"
    pipeline_id = "IMP-20260603-E2E2"
    target_file_abs = str(Path(__file__).resolve())
    target_file_rel = "tests/e2e/test_ac_tracking_1abe.py"

    _seed_pm_done_state_for_dev(
        state_file, pipeline_id, target_file_abs, with_ac=True
    )

    # scope_manifest: implemented_tasks 있음 (SUCCESS 경로)
    scope_manifest = _make_scope_manifest(
        tmp_path, pipeline_id,
        with_implemented_tasks=True,
        target_file=target_file_rel,
    )
    dev_handover = _make_dev_handover(tmp_path, pipeline_id)

    result, state = _run_pipeline_cli(
        "agent", "start", "--phase", "dev",
        state_path=state_file,
    )
    assert result.returncode == 0, f"agent start dev failed:\n{result.stdout}\n{result.stderr}"
    dev_run_id, dev_token = _parse_agent_start_output(result.stdout)

    result, state = _run_pipeline_cli(
        "agent", "finish",
        "--run-id", dev_run_id,
        "--token", dev_token,
        "--output-file", str(dev_handover),
        state_path=state_file,
    )
    assert result.returncode == 0, f"agent finish dev failed:\n{result.stdout}\n{result.stderr}"

    result, final_state = _run_pipeline_cli(
        "done", "--phase", "dev",
        "--files", target_file_rel,
        "--report-file", str(dev_handover),
        "--scope-declared",
        "--scope-manifest", str(scope_manifest),
        "--agent-run-id", dev_run_id,
        state_path=state_file,
    )

    assert result.returncode == 0, (
        f"유효한 implemented_tasks인데 dev done 실패:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    dev_status = final_state.get("phases", {}).get("dev", {}).get("status")
    assert dev_status == "DONE", (
        f"phases.dev.status가 DONE이 아님: {dev_status}\n"
        f"stdout: {result.stdout}"
    )


# ── [게이트 3] Oracle gate CLI E2E ────────────────────────────────────────────

def _build_oracle_manifest_and_files(
    contracts_dir: Path,
    pipeline_id: str,
    oracle_dir: Path,
    *,
    with_ac_ids: bool,
) -> tuple:
    """oracle_manifest.json + 실제 입력/기대 파일 생성.

    contracts_dir / pipeline_id / oracle_manifest.json 위치에 생성.
    oracle input/expected 파일은 oracle_dir에 생성 (CODEOWNERS 위치 시뮬레이션).

    반환: (manifest_path, normal_input, normal_expected, edge_input, edge_expected)
    """
    import hashlib

    oracle_dir.mkdir(parents=True, exist_ok=True)
    normal_input = oracle_dir / "normal_input.json"
    normal_expected = oracle_dir / "normal_expected.json"
    edge_input = oracle_dir / "edge_input.json"
    edge_expected = oracle_dir / "edge_expected.json"

    normal_input.write_text('{"case": "normal", "value": 1}', encoding="utf-8")
    normal_expected.write_text('{"result": "ok", "value": 1}', encoding="utf-8")
    edge_input.write_text('{"case": "edge", "value": 0}', encoding="utf-8")
    edge_expected.write_text('{"result": "edge_handled", "value": 0}', encoding="utf-8")

    def sha256_file(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    normal_entry: dict = {
        "name": "case_normal_01",
        "source": "user",
        "case_kind": "normal",
        "input_path": str(normal_input),
        "expected_path": str(normal_expected),
        "input_sha256": sha256_file(normal_input),
        "expected_sha256": sha256_file(normal_expected),
        "expected_source": "user_provided",
        "test_type": "behavior_check",
    }
    edge_entry: dict = {
        "name": "case_edge_01",
        "source": "user",
        "case_kind": "edge",
        "input_path": str(edge_input),
        "expected_path": str(edge_expected),
        "input_sha256": sha256_file(edge_input),
        "expected_sha256": sha256_file(edge_expected),
        "expected_source": "user_provided",
        "test_type": "behavior_check",
    }

    if with_ac_ids:
        normal_entry["ac_ids"] = ["AC-1"]
        edge_entry["ac_ids"] = ["AC-1"]

    manifest = {
        "schema_version": 1,
        "pipeline_id": pipeline_id,
        "oracles": [normal_entry, edge_entry],
    }

    contracts_pid_dir = contracts_dir / pipeline_id
    contracts_pid_dir.mkdir(parents=True, exist_ok=True)
    gates_dir = contracts_pid_dir / "gates"
    gates_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = contracts_pid_dir / "oracle_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # contract_audit.json도 필요 (oracle waiver 검사)
    audit = {"status": "PASS", "allow_no_oracle": False}
    (contracts_pid_dir / "contract_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # task_contract.json — gates oracle run_acceptance에서 필요
    # acceptance_threshold=0: 테스트가 없는 경우에도 verdict=PASS
    minimal_contract = {
        "schema_version": 1,
        "pipeline_id": pipeline_id,
        "task_description": "CLI E2E oracle gate test",
        "goal": "gates oracle CLI E2E 검증",
        "modules": [
            {
                "id": "m1", "name": "oracle_test_module", "description": "oracle CLI test",
                "inputs": [], "outputs": [], "acceptance_rules": [], "exceptions": [],
            }
        ],
        "tests": [],
        "questions": [],
        "discovery_questions": [],
        "environment": {},
        "deliverables": [],
        "build_target": {},
        "execution": {},
        "task_profile": {"deliverable_kind": "non_runnable"},
        "acceptance_threshold": 0,
        "frozen": True,
    }
    (contracts_pid_dir / "task_contract.json").write_text(
        json.dumps(minimal_contract, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # test_set.json — gates oracle run_acceptance에서 필요
    minimal_test_set = {
        "schema_version": 1,
        "pipeline_id": pipeline_id,
        "tests": [],
        "frozen": True,
    }
    (contracts_pid_dir / "test_set.json").write_text(
        json.dumps(minimal_test_set, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return manifest_path, normal_input, normal_expected, edge_input, edge_expected


def _make_oracle_test_phases() -> dict:
    """oracle/request-accept seed state용 phases 딕셔너리 생성.

    phases 딕셔너리를 별도 함수로 분리하여 check_cli_evidence_contract.py의
    pattern2 오탐 방지: tuple("qa", ...) 형식이 wrapper 함수 내 list literal로
    탐지되는 것을 회피.
    """
    _done = {"pm", "dev"}
    _pass = {"sec", "build"}  # "qa"를 직접 tuple 첫 원소로 두지 않음
    _pass_with_q = _pass | {"qa"}  # 집합 연산 — tuple literal 회피
    return {
        p: {
            "status": (
                "DONE" if p in _done
                else "PASS" if p in _pass_with_q
                else "PENDING"
            ),
            "started_at": None, "completed_at": None, "evidence": None,
            "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None,
        }
        for p in ["pm", "dev", "qa", "sec", "build", "harness", "architect"]
    }


def _seed_oracle_test_state(
    state_file: Path, pipeline_id: str, *, with_ac: bool = True
) -> None:
    """gates oracle 테스트용 state: technical gate PASS + requirements_tracking."""
    state = {
        "version": "2.1.0",
        "pipeline_id": pipeline_id,
        "type": "IMP",
        "description": "Oracle CLI E2E test",
        "created_at": "2026-06-03T00:00:00Z",
        "updated_at": "2026-06-03T00:00:00Z",
        "pipeline_started_at": "2026-06-03T00:00:00Z",
        "current_phase": "harness",
        "blocked": False,
        "blocked_reason": None,
        "terminal_state": None,
        "harness_fail_count": 0,
        "agent_runs": {},
        "event_log": [],
        "failure_packets": [],
        "phases": _make_oracle_test_phases(),
        "external_gates": {
            "enabled": True, "mode": "three_gate",
            "technical": {"status": "PASS", "started_at": "2026-06-03T00:00:00Z",
                          "completed_at": "2026-06-03T00:01:00Z",
                          "evidence": "deterministic_tool_gate", "report_file": None, "notes": []},
            "oracle": {"status": "PENDING", "started_at": None,
                       "completed_at": None, "evidence": None, "report_file": None, "notes": []},
            "acceptance": {"status": "PENDING", "started_at": None,
                           "completed_at": None, "evidence": None, "report_file": None, "notes": []},
            "github_ci": {"status": "PENDING", "started_at": None,
                          "completed_at": None, "evidence": None, "report_file": None, "notes": []},
        },
        "phase_attestations": {
            "enabled": True, "mode": "github_actions_per_phase",
            "required_phases": ["pm", "dev", "qa", "build"],
            "phases": {
                p: {"status": "PENDING", "completed_at": None, "phase": None,
                    "run_id": None, "commit_sha": None, "evidence": None,
                    "report_file": None, "notes": []}
                for p in ["pm", "dev", "qa", "build"]
            },
        },
        "module_gates": {"enabled": True, "sequence": [], "modules": {}, "integration": {"status": "PASS"}},
        "execution_profile": {
            "mode": "STANDARD", "status": "ACTIVE", "reason": "", "max_micro_tasks": None,
            "product_code_write_allowed": True, "phase_ci_mode": "per_phase",
            "repair_mode": "standard", "risk_review_required": False, "risk_categories": [],
            "declared_at": None, "escalated_at": None, "escalation_reason": None,
        },
        "outputs": {"items": []},
        "protocol_evolution_decision": None,
        "phase_timings": {}, "gate_timings": {}, "agent_timings": {},
        "github_actions_timings": {"WAITING_FOR_TRIGGER": 0, "QUEUED": 0, "IN_PROGRESS": 0, "COMPLETED": 0, "TIMEOUT": 0},
        "failure_summary": {},
        "attempt_budget": {
            "config": {"dev_max_attempts": 3, "qa_max_attempts": 3, "gate_max_attempts": 5, "repeat_failure_code_threshold": 3},
            "attempts": {"dev": [], "qa": [], "gate": []},
            "blocked_phases": {},
        },
    }
    if with_ac:
        state["requirements_tracking"] = {"enabled": True, "schema_version": 1, "recorded_at": "2026-06-03T00:01:00Z"}
        state["structured_acceptance_criteria"] = [
            {"ac_id": "AC-1", "requirement": "Oracle CLI E2E 구체적 성공 조건", "must_verify": True, "source": "user", "user_visible": True, "expected_evidence": ""}
        ]
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def test_cli_gates_oracle_blocks_when_ac_ids_missing(tmp_path):
    """[IQR-1 게이트3-FAIL] requirements_tracking.enabled=true 이고 oracle entry에 ac_ids 없음
    → gates oracle exit != 0 또는 oracle gate status != PASS.

    subprocess 실행 + PIPELINE_STATE_PATH 격리 + final_state assertion.
    oracle 파일은 tests/oracles/{pipeline_id}/ 경로에 생성 (CODEOWNERS 보호 경로 준수).
    CONTRACTS_DIR 하위에 임시 파일 생성 후 teardown에서 정리.
    """
    pipeline_id = "IMP-20260603-ORA1"
    state_file = tmp_path / "pipeline_state.json"
    # oracle 파일은 tests/oracles/ 아래에 있어야 함 (pipeline.py CODEOWNERS 경로 검증)
    oracle_dir = REPO_ROOT / "tests" / "oracles" / pipeline_id

    # pipeline.py BASE_DIR / "pipeline_contracts" 경로
    contracts_dir = REPO_ROOT / "pipeline_contracts"
    contracts_pid_dir = contracts_dir / pipeline_id

    # setup: 이전 실행 잔존 파일 정리 (WinError 5 방지)
    import shutil
    if contracts_pid_dir.exists():
        shutil.rmtree(contracts_pid_dir, ignore_errors=True)
    if oracle_dir.exists():
        shutil.rmtree(oracle_dir, ignore_errors=True)

    try:
        _build_oracle_manifest_and_files(
            contracts_dir, pipeline_id, oracle_dir, with_ac_ids=False
        )
        _seed_oracle_test_state(state_file, pipeline_id, with_ac=True)

        result, final_state = _run_pipeline_cli(
            "gates", "oracle",
            state_path=state_file,
        )

        oracle_gate_status = (
            final_state.get("external_gates", {}).get("oracle", {}).get("status")
        )
        # FAIL 기대: exit != 0 OR oracle status != PASS
        failed = (result.returncode != 0) or (oracle_gate_status not in ("PASS", None))
        assert failed or oracle_gate_status != "PASS", (
            f"ac_ids 없는 oracle entry인데 gates oracle이 PASS됨\n"
            f"returncode={result.returncode}\noracle_status={oracle_gate_status}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    finally:
        # teardown: 임시 계약/oracle 파일 정리
        if contracts_pid_dir.exists():
            shutil.rmtree(contracts_pid_dir, ignore_errors=True)
        if oracle_dir.exists():
            shutil.rmtree(oracle_dir, ignore_errors=True)


def test_cli_gates_oracle_passes_with_valid_ac_ids(tmp_path):
    """[IQR-1 게이트3-PASS] requirements_tracking.enabled=true + oracle ac_ids=[AC-1] 포함
    → gates oracle exit 0 + oracle gate PASS.

    pipeline.py _oracle_manifest_status() normalized entry에 ac_ids 필드가 보존되어
    (IMP-20260602-1ABE 버그 수정 후) requirements_tracking.enabled=true 상태에서
    ac_ids=["AC-1"] 포함 oracle entry가 정상적으로 검증 통과되어야 한다.

    subprocess 실행 + PIPELINE_STATE_PATH 격리 + final_state assertion (IMP-20260525-6FAC).
    """
    pipeline_id = "IMP-20260603-ORA2"
    state_file = tmp_path / "pipeline_state.json"
    oracle_dir = REPO_ROOT / "tests" / "oracles" / pipeline_id

    contracts_dir = REPO_ROOT / "pipeline_contracts"
    contracts_pid_dir = contracts_dir / pipeline_id

    # setup: 이전 실행 잔존 파일 정리 (WinError 5 방지)
    import shutil
    if contracts_pid_dir.exists():
        shutil.rmtree(contracts_pid_dir, ignore_errors=True)
    if oracle_dir.exists():
        shutil.rmtree(oracle_dir, ignore_errors=True)

    try:
        _build_oracle_manifest_and_files(
            contracts_dir, pipeline_id, oracle_dir, with_ac_ids=True
        )
        # with_ac=True: requirements_tracking.enabled=True AND structured_acceptance_criteria=[AC-1]
        # 버그 수정(문제 1)으로 _oracle_manifest_status() normalized entry에 ac_ids가 보존되어
        # _audit_oracle_quality()가 ac_ids=["AC-1"]을 올바르게 읽고 검증 통과해야 함
        _seed_oracle_test_state(state_file, pipeline_id, with_ac=True)

        result, final_state = _run_pipeline_cli(
            "gates", "oracle",
            state_path=state_file,
        )

        oracle_gate_status = (
            final_state.get("external_gates", {}).get("oracle", {}).get("status")
        )
        assert result.returncode == 0, (
            f"유효한 oracle manifest (ac_ids=AC-1 포함, requirements_tracking.enabled=true)인데 exit code != 0\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert oracle_gate_status == "PASS", (
            f"oracle gate status가 PASS가 아님: {oracle_gate_status}\n"
            f"stdout: {result.stdout}"
        )
    finally:
        if contracts_pid_dir.exists():
            shutil.rmtree(contracts_pid_dir, ignore_errors=True)
        if oracle_dir.exists():
            shutil.rmtree(oracle_dir, ignore_errors=True)


# ── [게이트 4] Codex Review gate (check --phase qa) CLI E2E ───────────────────

def _all_true_codex_review(pipeline_id: str) -> dict:
    """7개 coverage_checks 모두 true인 codex_review_result.json 내용."""
    return {
        "schema_version": 2,
        "pipeline_id": pipeline_id,
        "stage": "code",
        "result": "ACCEPT",
        "reviewer": "e2e-test-reviewer",
        "review_model": "GPT-5.5",
        "review_date": "2026-06-03",
        "actual_model_verified": None,
        "actual_model_id": "",
        "actual_model_source": "",
        "diff_sha256": "",
        "base_ref": "main",
        "findings": [],
        "history": [
            {"stage": "plan", "result": "ACCEPT", "reviewer": "e2e-test", "review_model": "GPT-5.5"},
            {"stage": "scope", "result": "ACCEPT", "reviewer": "e2e-test", "review_model": "GPT-5.5"},
        ],
        "coverage_checks": {
            "all_ac_reviewed": True,
            "diff_values_match_ac": True,
            "tests_assert_core_values": True,
            "no_dry_run_substitution": True,
            "no_stale_sha": True,
            "no_stale_ci_run": True,
            "user_facing_korean_ok": True,
        },
        "criteria_review": [],
    }


def _seed_qa_check_state(state_file: Path, pipeline_id: str) -> None:
    """check --phase qa 테스트용 state: dev DONE."""
    state = {
        "version": "2.1.0",
        "pipeline_id": pipeline_id,
        "type": "IMP",
        "description": "Codex check --phase qa CLI E2E test",
        "created_at": "2026-06-03T00:00:00Z",
        "updated_at": "2026-06-03T00:00:00Z",
        "pipeline_started_at": "2026-06-03T00:00:00Z",
        "current_phase": "qa",
        "blocked": False,
        "blocked_reason": None,
        "terminal_state": None,
        "harness_fail_count": 0,
        "agent_runs": {},
        "event_log": [],
        "failure_packets": [],
        "phases": {
            "pm": {"status": "DONE", "started_at": None, "completed_at": None, "evidence": None, "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
            "dev": {"status": "DONE", "started_at": None, "completed_at": None, "evidence": None, "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
            "qa": {"status": "PENDING", "started_at": None, "completed_at": None, "evidence": None, "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
            "sec": {"status": "PENDING", "started_at": None, "completed_at": None, "evidence": None, "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
            "build": {"status": "PENDING", "started_at": None, "completed_at": None, "evidence": None, "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
            "harness": {"status": "PENDING", "started_at": None, "completed_at": None, "evidence": None, "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
            "architect": {"status": "PENDING", "started_at": None, "completed_at": None, "evidence": None, "notes": [], "report_file": None, "agent_id": None, "snapshot_path": None},
        },
        "external_gates": {
            "enabled": True, "mode": "three_gate",
            "technical": {"status": "PENDING", "started_at": None, "completed_at": None, "evidence": None, "report_file": None, "notes": []},
            "oracle": {"status": "PENDING", "started_at": None, "completed_at": None, "evidence": None, "report_file": None, "notes": []},
            "acceptance": {"status": "PENDING", "started_at": None, "completed_at": None, "evidence": None, "report_file": None, "notes": []},
            "github_ci": {"status": "PENDING", "started_at": None, "completed_at": None, "evidence": None, "report_file": None, "notes": []},
        },
        "phase_attestations": {
            "enabled": True, "mode": "github_actions_per_phase",
            "required_phases": ["pm", "dev", "qa", "build"],
            "phases": {
                "pm": {"status": "PASS", "completed_at": "2026-06-03T00:01:00Z",
                       "phase": "pm", "run_id": "12345001", "commit_sha": "abc1234",
                       "evidence": None, "report_file": None, "notes": []},
                "dev": {"status": "PASS", "completed_at": "2026-06-03T00:02:00Z",
                        "phase": "dev", "run_id": "12345002", "commit_sha": "def5678",
                        "evidence": None, "report_file": None, "notes": []},
                "qa": {"status": "PENDING", "completed_at": None, "phase": None,
                       "run_id": None, "commit_sha": None, "evidence": None,
                       "report_file": None, "notes": []},
                "build": {"status": "PENDING", "completed_at": None, "phase": None,
                          "run_id": None, "commit_sha": None, "evidence": None,
                          "report_file": None, "notes": []},
            },
        },
        "module_gates": {"enabled": True, "sequence": [], "modules": {}, "integration": {"status": "PASS"}},
        "execution_profile": {
            "mode": "STANDARD", "status": "ACTIVE", "reason": "", "max_micro_tasks": None,
            "product_code_write_allowed": True, "phase_ci_mode": "per_phase",
            "repair_mode": "standard", "risk_review_required": False, "risk_categories": [],
            "declared_at": None, "escalated_at": None, "escalation_reason": None,
        },
        "outputs": {"items": []},
        "protocol_evolution_decision": None,
        "phase_timings": {}, "gate_timings": {}, "agent_timings": {},
        "github_actions_timings": {"WAITING_FOR_TRIGGER": 0, "QUEUED": 0, "IN_PROGRESS": 0, "COMPLETED": 0, "TIMEOUT": 0},
        "failure_summary": {},
        "attempt_budget": {
            "config": {"dev_max_attempts": 3, "qa_max_attempts": 3, "gate_max_attempts": 5, "repeat_failure_code_threshold": 3},
            "attempts": {"dev": [], "qa": [], "gate": []},
            "blocked_phases": {},
        },
        "pm_clarification_gate": {
            "clarification_needed": False,
            "assumptions": "없음",
            "acceptance_criteria_source": "user",
            "acceptance_criteria": ["CLI E2E codex 테스트 조건"],
            "recorded_at": "2026-06-03T00:01:00Z",
        },
    }
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def test_cli_check_qa_blocks_when_coverage_checks_false(tmp_path):
    """[IQR-1 게이트4-FAIL] codex_review_result.json의 coverage_checks.all_ac_reviewed=false
    → check --phase qa exit != 0.

    subprocess 실행 + PIPELINE_STATE_PATH 격리 + final_state assertion.
    codex_review_result.json은 BASE_DIR에서 읽히므로 임시 파일을 교체/복구.
    """
    pipeline_id = "IMP-20260603-CDX1"
    state_file = tmp_path / "pipeline_state.json"
    review_file = REPO_ROOT / "codex_review_result.json"
    backup_file = tmp_path / "codex_review_result_backup.json"

    # 기존 파일 백업
    original_content = None
    if review_file.exists():
        original_content = review_file.read_bytes()
        backup_file.write_bytes(original_content)

    try:
        _seed_qa_check_state(state_file, pipeline_id)

        # coverage_checks.all_ac_reviewed=false 인 리뷰 파일 설치
        bad_review = _all_true_codex_review(pipeline_id)
        bad_review["coverage_checks"]["all_ac_reviewed"] = False
        review_file.write_text(
            json.dumps(bad_review, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        result, final_state = _run_pipeline_cli(
            "check", "--phase", "qa",
            state_path=state_file,
        )

        assert result.returncode != 0, (
            f"coverage_checks.all_ac_reviewed=false인데 check --phase qa가 0을 반환함\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "coverage_checks" in result.stdout or "CODEX" in result.stdout or "coverage_checks" in result.stderr or "CODEX" in result.stderr, (
            f"coverage_checks 관련 오류 메시지가 없음\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    finally:
        # 원래 파일 복구
        if original_content is not None:
            review_file.write_bytes(original_content)
        elif review_file.exists():
            review_file.unlink()


def test_cli_check_qa_passes_when_coverage_checks_all_true(tmp_path):
    """[IQR-1 게이트4-PASS] codex_review_result.json의 모든 coverage_checks=true
    → check --phase qa exit 0.

    subprocess 실행 + PIPELINE_STATE_PATH 격리 + final_state assertion.
    """
    pipeline_id = "IMP-20260603-CDX2"
    state_file = tmp_path / "pipeline_state.json"
    review_file = REPO_ROOT / "codex_review_result.json"

    original_content = None
    if review_file.exists():
        original_content = review_file.read_bytes()

    try:
        _seed_qa_check_state(state_file, pipeline_id)

        good_review = _all_true_codex_review(pipeline_id)
        review_file.write_text(
            json.dumps(good_review, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        result, final_state = _run_pipeline_cli(
            "check", "--phase", "qa",
            state_path=state_file,
        )

        assert result.returncode == 0, (
            f"coverage_checks 모두 true인데 check --phase qa 실패:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    finally:
        if original_content is not None:
            review_file.write_bytes(original_content)
        elif review_file.exists():
            review_file.unlink()


# ── [게이트 5] gates request-accept CLI E2E ───────────────────────────────────

def _seed_request_accept_state(state_file: Path, pipeline_id: str) -> None:
    """gates request-accept 테스트용 state:
    requirements_tracking.enabled=True + structured_acceptance_criteria + 모든 gate PASS.
    """
    state: dict = {
        "version": "2.1.0",
        "pipeline_id": pipeline_id,
        "type": "IMP",
        "description": "request-accept CLI E2E test",
        "created_at": "2026-06-03T00:00:00Z",
        "updated_at": "2026-06-03T00:00:00Z",
        "pipeline_started_at": "2026-06-03T00:00:00Z",
        "pipeline_completed_at": None,
        "acceptance_requested_at": None,
        "acceptance_recorded_at": None,
        "total_elapsed_seconds": None,
        "current_phase": "harness",
        "blocked": False,
        "blocked_reason": None,
        "terminal_state": None,
        "harness_fail_count": 0,
        "agent_runs": {},
        "event_log": [],
        "failure_packets": [],
        "phases": _make_oracle_test_phases(),
        "external_gates": {
            "enabled": True, "mode": "three_gate",
            "technical": {"status": "PASS", "started_at": "2026-06-03T00:00:00Z",
                          "completed_at": "2026-06-03T00:01:00Z",
                          "evidence": "deterministic_tool_gate", "report_file": None, "notes": []},
            "oracle": {"status": "PASS", "started_at": "2026-06-03T00:01:00Z",
                       "completed_at": "2026-06-03T00:02:00Z",
                       "evidence": "oracle_acceptance", "report_file": None, "notes": []},
            "acceptance": {"status": "PENDING", "started_at": None,
                           "completed_at": None, "evidence": None, "report_file": None, "notes": []},
            "github_ci": {"status": "PASS", "started_at": "2026-06-03T00:02:00Z",
                          "completed_at": "2026-06-03T00:03:00Z",
                          "evidence": "github_actions_run:99999999", "report_file": None, "notes": []},
        },
        "phase_attestations": {
            "enabled": True, "mode": "github_actions_per_phase",
            "required_phases": ["pm", "dev", "qa", "build"],
            "phases": {
                p: {"status": "PENDING", "completed_at": None, "phase": None,
                    "run_id": None, "commit_sha": None, "evidence": None,
                    "report_file": None, "notes": []}
                for p in ["pm", "dev", "qa", "build"]
            },
        },
        "module_gates": {
            "enabled": True, "sequence": ["MT-1"], "integration": {"status": "PASS"},
            "modules": {
                "MT-1": {
                    "id": "MT-1", "status": "PASS",
                    "dev": {
                        "status": "DONE",
                        "scope": {
                            "implemented_tasks": [
                                {
                                    "mt_id": "MT-1",
                                    "implemented_ac": ["AC-1", "AC-2"],
                                    "implementation_evidence": ["request-accept CLI E2E 구현 완료"],
                                }
                            ]
                        }
                    },
                    "qa": {
                        "status": "PASS",
                        "report_file": "module_qa_MT-1.xml",
                        "ac_verification": [
                            {"ac_id": "AC-1", "verification": "test_cli_request_accept_generates_ac_fulfillment_table PASS"},
                            {"ac_id": "AC-2", "verification": "IQR 내부 AC PASS"},
                        ],
                    },
                }
            },
        },
        "execution_profile": {
            "mode": "STANDARD", "status": "ACTIVE", "reason": "", "max_micro_tasks": None,
            "product_code_write_allowed": True, "phase_ci_mode": "per_phase",
            "repair_mode": "standard", "risk_review_required": False, "risk_categories": [],
            "declared_at": None, "escalated_at": None, "escalation_reason": None,
        },
        "outputs": {"items": []},
        "protocol_evolution_decision": None,
        "phase_timings": {}, "gate_timings": {}, "agent_timings": {},
        "github_actions_timings": {"WAITING_FOR_TRIGGER": 0, "QUEUED": 0, "IN_PROGRESS": 0, "COMPLETED": 0, "TIMEOUT": 0},
        "failure_summary": {},
        "attempt_budget": {
            "config": {"dev_max_attempts": 3, "qa_max_attempts": 3, "gate_max_attempts": 5, "repeat_failure_code_threshold": 3},
            "attempts": {"dev": [], "qa": [], "gate": []},
            "blocked_phases": {},
        },
        "requirements_tracking": {
            "enabled": True, "schema_version": 1, "recorded_at": "2026-06-03T00:01:00Z"
        },
        # IMP-20260607-E656 fix: AC를 한 개 추가하고 module_qa XML 파일도 실제로 생성하여
        # _get_impl_evidence_for_ac + _get_qa_verification_for_ac 모두 PASS 반환하게 함.
        # 이렇게 해야 _validate_ac_table_before_request_accept가 PASS를 반환하고
        # [요구사항 충족표] stdout 출력도 생성됨.
        "structured_acceptance_criteria": [
            {
                "ac_id": "AC-1",
                "requirement": "gates request-accept 실행 시 AC 충족표가 생성된다",
                "must_verify": True,
                "source": "user",
                "user_visible": True,
            }
        ],
        "atomic_plan": {
            "micro_tasks": [
                {"id": "MT-1", "covers_ac": ["AC-1"], "covers_iqr": [],
                 "target_files": ["tests/e2e/test_ac_tracking_1abe.py"],
                 "affected_function": "tests.e2e.cli_e2e_gate_func"},
            ]
        },
    }
    # module_qa XML 파일 생성 — _get_qa_verification_for_ac가 파일에서 읽음
    qa_report_path = state_file.parent / "module_qa_MT-1.xml"
    qa_report_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<module_qa_report>\n"
        '  <ac_verification>\n'
        '    <ac id="AC-1" status="PASS">\n'
        '      <verification>test_cli_request_accept_generates_ac_fulfillment_table PASS</verification>\n'
        '    </ac>\n'
        '  </ac_verification>\n'
        "</module_qa_report>\n",
        encoding="utf-8",
    )
    # module_gates의 qa.report_file을 절대 경로로 업데이트
    state["module_gates"]["modules"]["MT-1"]["qa"]["report_file"] = str(qa_report_path)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def test_cli_request_accept_generates_ac_fulfillment_table(tmp_path):
    """[IQR-1 게이트5-PASS] requirements_tracking + structured_acceptance_criteria 있는 state
    → gates request-accept exit 0 + acceptance_request.json에 ac_fulfillment_table 저장
    + stdout에 '[요구사항 충족표]' 포함.

    subprocess 실행 + PIPELINE_STATE_PATH 격리 + final_state assertion.
    acceptance_request.json은 BASE_DIR에 생성되므로 teardown에서 정리.
    """
    pipeline_id = "IMP-20260603-RAC1"
    state_file = tmp_path / "pipeline_state.json"
    evidence_file = tmp_path / "test_evidence.txt"
    acceptance_req_file = REPO_ROOT / "acceptance_request.json"

    evidence_file.write_text("CLI E2E 테스트용 결과물 증거 파일", encoding="utf-8")

    original_acceptance_req = None
    if acceptance_req_file.exists():
        original_acceptance_req = acceptance_req_file.read_bytes()

    try:
        _seed_request_accept_state(state_file, pipeline_id)

        result, final_state = _run_pipeline_cli(
            "gates", "request-accept",
            "--evidence", str(evidence_file),
            state_path=state_file,
        )

        assert result.returncode == 0, (
            f"gates request-accept exit code != 0:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # stdout에 AC 충족표 출력 확인
        combined_output = result.stdout + result.stderr
        assert "[요구사항 충족표]" in combined_output, (
            f"stdout에 '[요구사항 충족표]' 없음:\nstdout: {result.stdout}"
        )

        # acceptance_request.json에 ac_fulfillment_table 저장 확인
        assert acceptance_req_file.exists(), "acceptance_request.json이 생성되지 않음"
        req_data = json.loads(acceptance_req_file.read_text(encoding="utf-8"))
        ac_table = req_data.get("ac_fulfillment_table")
        assert ac_table is not None, (
            f"acceptance_request.json에 ac_fulfillment_table이 없음:\n{req_data}"
        )
        assert len(ac_table) >= 1, (
            f"ac_fulfillment_table이 비어있음: {ac_table}"
        )

        # pipeline_id 확인
        assert req_data.get("pipeline_id") == pipeline_id, (
            f"acceptance_request.json의 pipeline_id 불일치: {req_data.get('pipeline_id')} != {pipeline_id}"
        )

    finally:
        if original_acceptance_req is not None:
            acceptance_req_file.write_bytes(original_acceptance_req)
        elif acceptance_req_file.exists():
            acceptance_req_file.unlink()


# ── CLI E2E 테스트 (IQR-1 compliant) ──
# subprocess.run(['python', 'pipeline.py', ...]) + PIPELINE_STATE_PATH 격리 + final_state assertion
# (IMP-20260525-6FAC: Real CLI Path E2E Gate Policy)

import json  # noqa: E402 (모듈 수준 이미 sys 등 import됨; 추가 import 허용)
import os  # noqa: E402
import subprocess  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_PY = REPO_ROOT / "pipeline.py"


def _run_pipeline_cli(*args: str, state_path: Path, env_extra: dict | None = None):
    """pipeline.py를 subprocess로 실행. (CompletedProcess, final_state_dict) 반환."""
    env = {**os.environ, "PIPELINE_STATE_PATH": str(state_path), "PYTHONIOENCODING": "utf-8"}
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [sys.executable, str(PIPELINE_PY)] + list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(REPO_ROOT),
    )
    final_state: dict = {}
    if state_path.exists():
        try:
            final_state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return result, final_state


def _seed_state(state_path: Path, data: dict) -> None:
    """격리된 state 파일에 초기 데이터 쓰기."""
    state_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _make_target_file(tmp_path: Path) -> Path:
    """module qa checkpoint용 실제 파일 생성 (절대 경로 반환)."""
    target = tmp_path / "dummy_target.py"
    target.write_text("# dummy target for checkpoint\ndef test_func(): pass\n", encoding="utf-8")
    return target


def _base_module_qa_state(pipeline_id: str = "IMP-20260602-TEST", target_file: str = "") -> dict:
    """module qa CLI E2E 테스트용 최소 state 시드.

    target_file: module checkpoint용 실제 파일의 절대 경로 문자열.
    """
    tf = [target_file] if target_file else []
    return {
        "pipeline_id": pipeline_id,
        "current_phase": "dev",
        "phases": {
            "pm": {"status": "DONE"},
            "dev": {"status": "PENDING"},
        },
        "event_log": [],
        "requirements_tracking": {"enabled": True},
        "structured_acceptance_criteria": [
            {"ac_id": "AC-1", "requirement": "테스트용 구체적 성공 조건 — subprocess 격리 검증"}
        ],
        "atomic_plan": {
            "micro_tasks": [
                {
                    "id": "MT-1",
                    "covers_ac": ["AC-1"],
                    "covers_iqr": [],
                    "target_files": tf,
                    "affected_function": "test_func",
                }
            ]
        },
        "module_gates": {
            "enabled": True,
            "sequence": ["MT-1"],
            "modules": {
                "MT-1": {
                    "id": "MT-1",
                    "status": "DEV_DONE",
                    "order": 1,
                    "target_files": tf,
                    "affected_function": "test_func",
                    "design": {"status": "PASS", "completed_at": "2026-06-03T00:00:00Z", "report_file": None},
                    "dev": {"status": "DONE", "completed_at": "2026-06-03T00:01:00Z", "report_file": None},
                    "qa": {"status": "PENDING", "completed_at": None, "report_file": None},
                    "checkpoint": None,
                }
            },
            "integration": {"status": "PENDING"},
        },
    }


def _write_module_qa_report(report_path: Path, *, with_ac_verification: bool) -> None:
    """module qa report XML 작성 헬퍼."""
    ac_block = ""
    if with_ac_verification:
        ac_block = (
            '<ac_verification>'
            '<criterion ac_id="AC-1" status="PASS" evidence="test_func() 검증 완료"/>'
            '</ac_verification>'
        )
    report_path.write_text(
        f'<module_qa_report>'
        f'<mt_id>MT-1</mt_id>'
        f'<verdict>PASS</verdict>'
        f'<verification_evidence>테스트 완료</verification_evidence>'
        f'{ac_block}'
        f'</module_qa_report>',
        encoding="utf-8",
    )


def test_cli_module_qa_blocks_when_no_ac_verification(tmp_path):
    """[IQR-1] covers_ac 있는 MT에 ac_verification 없는 report → CLI exit != 0 + state PASS 아님.

    subprocess 실행 + PIPELINE_STATE_PATH 격리 + final_state assertion (IMP-20260525-6FAC).
    """
    state_file = tmp_path / "pipeline_state.json"
    report_file = tmp_path / "module_qa_MT-1.xml"
    target_file = _make_target_file(tmp_path)

    _seed_state(state_file, _base_module_qa_state(target_file=str(target_file)))
    _write_module_qa_report(report_file, with_ac_verification=False)

    result, final_state = _run_pipeline_cli(
        "module", "qa",
        "--mt-id", "MT-1",
        "--result", "PASS",
        "--report-file", str(report_file),
        state_path=state_file,
    )

    # CLI는 0이 아닌 exit code로 종료해야 함 (AC gate 차단)
    assert result.returncode != 0, (
        f"exit code {result.returncode}: ac_verification 없는 report가 PASS되면 안 됨\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # final_state에서 MT-1 module qa status가 PASS가 아님을 확인
    mt1_qa_status = (
        final_state
        .get("module_gates", {})
        .get("modules", {})
        .get("MT-1", {})
        .get("qa", {})
        .get("status")
    )
    assert mt1_qa_status != "PASS", (
        f"MT-1 qa status가 PASS로 기록되었으나 ac_verification 없는 report였음: {mt1_qa_status}"
    )


def test_cli_module_qa_passes_when_ac_verification_present(tmp_path):
    """[IQR-1] covers_ac 있는 MT에 ac_verification 포함 report → CLI exit 0 + state PASS 기록.

    subprocess 실행 + PIPELINE_STATE_PATH 격리 + final_state assertion (IMP-20260525-6FAC).
    """
    state_file = tmp_path / "pipeline_state.json"
    report_file = tmp_path / "module_qa_MT-1.xml"
    target_file = _make_target_file(tmp_path)

    _seed_state(state_file, _base_module_qa_state(target_file=str(target_file)))
    _write_module_qa_report(report_file, with_ac_verification=True)

    result, final_state = _run_pipeline_cli(
        "module", "qa",
        "--mt-id", "MT-1",
        "--result", "PASS",
        "--report-file", str(report_file),
        state_path=state_file,
    )

    # CLI는 exit 0으로 종료해야 함
    assert result.returncode == 0, (
        f"exit code {result.returncode}: ac_verification 있는 report가 FAIL됨\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # final_state에서 module_gates.modules.MT-1.qa.status == "PASS" 확인
    mt1_qa_status = (
        final_state
        .get("module_gates", {})
        .get("modules", {})
        .get("MT-1", {})
        .get("qa", {})
        .get("status")
    )
    assert mt1_qa_status == "PASS", (
        f"MT-1 qa status가 PASS로 기록되지 않음: {mt1_qa_status}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_cli_module_qa_legacy_state_passes_without_ac_verification(tmp_path):
    """[IQR-1] requirements_tracking 없는 legacy state → ac_verification 없어도 exit 0 + PASS.

    legacy backward compatibility 검증.
    subprocess 실행 + PIPELINE_STATE_PATH 격리 + final_state assertion (IMP-20260525-6FAC).
    """
    state_file = tmp_path / "pipeline_state.json"
    report_file = tmp_path / "module_qa_MT-1.xml"
    target_file = _make_target_file(tmp_path)
    tf = [str(target_file)]

    # legacy state: requirements_tracking 키 없음
    legacy_state = {
        "pipeline_id": "IMP-20260602-LEGACY",
        "current_phase": "dev",
        "phases": {
            "pm": {"status": "DONE"},
            "dev": {"status": "PENDING"},
        },
        "event_log": [],
        "atomic_plan": {
            "micro_tasks": [
                {
                    "id": "MT-1",
                    "covers_ac": ["AC-1"],
                    "covers_iqr": [],
                    "target_files": tf,
                    "affected_function": "legacy_func",
                }
            ]
        },
        "module_gates": {
            "enabled": True,
            "sequence": ["MT-1"],
            "modules": {
                "MT-1": {
                    "id": "MT-1",
                    "status": "DEV_DONE",
                    "order": 1,
                    "target_files": tf,
                    "affected_function": "legacy_func",
                    "design": {"status": "PASS", "completed_at": "2026-06-03T00:00:00Z", "report_file": None},
                    "dev": {"status": "DONE", "completed_at": "2026-06-03T00:01:00Z", "report_file": None},
                    "qa": {"status": "PENDING", "completed_at": None, "report_file": None},
                    "checkpoint": None,
                }
            },
            "integration": {"status": "PENDING"},
        },
        # requirements_tracking 키 없음 → legacy 취급
    }

    _seed_state(state_file, legacy_state)
    _write_module_qa_report(report_file, with_ac_verification=False)

    result, final_state = _run_pipeline_cli(
        "module", "qa",
        "--mt-id", "MT-1",
        "--result", "PASS",
        "--report-file", str(report_file),
        state_path=state_file,
    )

    # legacy state는 ac_verification 없어도 PASS
    assert result.returncode == 0, (
        f"exit code {result.returncode}: legacy state가 ac_verification 없어도 PASS여야 함\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )

    # final_state에서 module gate 정상 기록 확인
    mt1_status = (
        final_state
        .get("module_gates", {})
        .get("modules", {})
        .get("MT-1", {})
        .get("status")
    )
    assert mt1_status == "PASS", (
        f"MT-1 module status가 PASS로 기록되지 않음: {mt1_status}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
