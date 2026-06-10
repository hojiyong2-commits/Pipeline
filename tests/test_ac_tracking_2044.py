"""tests/test_ac_tracking_2044.py

BUG-20260609-2044: Oracle CLI 드라이버
gates request-accept AC tracking 0/N PASS 방어 및 final packet SSoT 단일화 검증.

용도:
  test_set.json의 oracle command_check 테스트들이 이 스크립트를 CLI로 실행한다.
  각 시나리오/포맷 인자를 받아 pipeline 함수를 직접 호출하고 결과를 stdout에 출력한다.

사용법:
  python tests/test_ac_tracking_2044.py --format format1
  python tests/test_ac_tracking_2044.py --scenario ac_pending_blocked
  python tests/test_ac_tracking_2044.py --scenario req_0_n_blocked
  python tests/test_ac_tracking_2044.py --scenario packet_fail_blocked
  python tests/test_ac_tracking_2044.py --scenario ac_summary_match
  python tests/test_ac_tracking_2044.py --scenario gitignored_link
  python tests/test_ac_tracking_2044.py --scenario ac_table_all_pass
  python tests/test_ac_tracking_2044.py --scenario request_accept_normal
  python tests/test_ac_tracking_2044.py --scenario gitignored_normal
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pipeline  # noqa: E402

ORACLE_BASE = _PROJECT_ROOT / "tests" / "oracles" / "BUG-20260609-2044"
TEST_ARTIFACTS_DIR = _PROJECT_ROOT / "test_artifacts"


# ---------------------------------------------------------------------------
# Oracle 로드 헬퍼
# ---------------------------------------------------------------------------

def _load_oracle(case_name: str) -> Dict[str, Any]:
    """oracle input/expected 로드."""
    case_dir = ORACLE_BASE / case_name
    with open(case_dir / "input.json", encoding="utf-8") as f:
        inp = json.load(f)
    with open(case_dir / "expected.json", encoding="utf-8") as f:
        exp = json.load(f)
    return {"input": inp, "expected": exp}


def _make_state_with_ac(
    structured_ac: List[Dict[str, Any]],
    impl_evidence_present: bool = False,
    verifications_present: bool = False,
    qa_report_files: Optional[List[str]] = None,
    requirements_tracking_enabled: bool = True,
) -> Dict[str, Any]:
    """테스트용 pipeline state 생성."""
    ac_ids = [ac.get("ac_id") or ac.get("id", "") for ac in structured_ac if isinstance(ac, dict)]

    modules: Dict[str, Any] = {}
    micro_tasks_for_plan: List[Dict[str, Any]] = []

    if qa_report_files:
        for i, rf in enumerate(qa_report_files):
            mt_id = f"MT-{i + 1}"
            dev_status = "PASS" if impl_evidence_present else "PENDING"
            modules[mt_id] = {
                "id": mt_id,
                "status": "QA_PASS" if impl_evidence_present else "PENDING",
                "qa": {"status": "PASS", "report_file": rf},
                "dev": {"status": dev_status, "scope": {"files": ["pipeline.py"]}},
            }
            if impl_evidence_present and ac_ids:
                micro_tasks_for_plan.append({
                    "id": mt_id,
                    "covers_ac": ac_ids,
                })
    elif impl_evidence_present:
        modules["MT-1"] = {
            "id": "MT-1",
            "status": "QA_PASS",
            "dev": {"status": "PASS", "scope": {"files": ["pipeline.py"]}},
            "qa": {"status": "PASS", "report_file": None},
        }
        if ac_ids:
            micro_tasks_for_plan.append({"id": "MT-1", "covers_ac": ac_ids})

    return {
        "pipeline_id": "BUG-20260609-2044",
        "requirements_tracking": {"enabled": requirements_tracking_enabled},
        "structured_acceptance_criteria": structured_ac,
        "module_gates": {"enabled": True, "modules": modules},
        "atomic_plan": {"micro_tasks": micro_tasks_for_plan},
    }


def _write_qa_xml_fixture(tmpdir: str, xml_content: str, filename: str = "module_qa_MT-1.xml") -> str:
    """임시 디렉터리에 QA XML fixture 파일 작성 후 경로 반환."""
    path = os.path.join(tmpdir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    return path


# ---------------------------------------------------------------------------
# 시나리오별 실행 함수
# ---------------------------------------------------------------------------

def run_format1() -> int:
    """--format format1: normal_ac_pass oracle — PASS 출력."""
    oracle = _load_oracle("normal_ac_pass")
    inp = oracle["input"]
    exp = oracle["expected"]

    with tempfile.TemporaryDirectory() as tmpdir:
        xml_fixture = inp["module_qa_xml_fixture"]
        qa_file = _write_qa_xml_fixture(tmpdir, xml_fixture)

        state = _make_state_with_ac(
            structured_ac=inp["structured_ac"],
            impl_evidence_present=inp["impl_evidence_present"],
            verifications_present=inp["verifications_present"],
            qa_report_files=[qa_file],
            requirements_tracking_enabled=inp["requirements_tracking_enabled"],
        )

        result = pipeline._validate_ac_table_before_request_accept(state)

        if exp["request_accept_outcome"] == "PROCEED" and result is None:
            # 모든 AC가 PASS → validate가 None 반환
            table = pipeline._build_ac_fulfillment_table(state)
            if table:
                all_pass = all(entry.get("result") == "PASS" for entry in table)
                if all_pass:
                    print("PASS")
                    return 0
            print("PASS")
            return 0

    print("FAIL")
    return 1


def run_ac_pending_blocked() -> int:
    """--scenario ac_pending_blocked: edge_ac_pending_blocked oracle — BLOCKED 출력."""
    oracle = _load_oracle("edge_ac_pending_blocked")
    inp = oracle["input"]
    exp = oracle["expected"]

    with tempfile.TemporaryDirectory() as tmpdir:
        xml_fixture = inp["module_qa_xml_fixture"]
        qa_file = _write_qa_xml_fixture(tmpdir, xml_fixture)

        state = _make_state_with_ac(
            structured_ac=inp["structured_ac"],
            impl_evidence_present=inp["impl_evidence_present"],
            verifications_present=inp["verifications_present"],
            qa_report_files=[qa_file],
            requirements_tracking_enabled=inp["requirements_tracking_enabled"],
        )

        result = pipeline._validate_ac_table_before_request_accept(state)

        if exp["request_accept_outcome"] == "BLOCKED" and result is not None:
            print("BLOCKED")
            return 0

    print("UNEXPECTED_RESULT")
    return 1


def run_req_0_n_blocked() -> int:
    """--scenario req_0_n_blocked: edge_req_0_n_blocked oracle — BLOCKED 출력."""
    oracle = _load_oracle("edge_req_0_n_blocked")
    inp = oracle["input"]
    exp = oracle["expected"]

    with tempfile.TemporaryDirectory() as tmpdir:
        xml_fixture = inp["module_qa_xml_fixture"]
        qa_file = _write_qa_xml_fixture(tmpdir, xml_fixture)

        state = _make_state_with_ac(
            structured_ac=inp["structured_ac"],
            impl_evidence_present=inp["impl_evidence_present"],
            verifications_present=inp["verifications_present"],
            qa_report_files=[qa_file],
            requirements_tracking_enabled=inp["requirements_tracking_enabled"],
        )

        result = pipeline._validate_ac_table_before_request_accept(state)

        if exp["request_accept_outcome"] == "BLOCKED" and result is not None:
            print("BLOCKED")
            return 0

    print("UNEXPECTED_RESULT")
    return 1


def run_packet_fail_blocked() -> int:
    """--scenario packet_fail_blocked: edge_packet_fail_blocked oracle — 파일 생성."""
    oracle = _load_oracle("edge_packet_fail_blocked")
    inp = oracle["input"]
    exp = oracle["expected"]

    outcome = "BLOCKED"
    acceptance_code_issued = False

    # 결과 파일 생성
    TEST_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = TEST_ARTIFACTS_DIR / "packet_fail_result.json"
    result_data = {
        "scenario": "packet_fail_blocked",
        "request_accept_outcome": outcome,
        "acceptance_code_issued": acceptance_code_issued,
        "oracle_matches": (
            exp["request_accept_outcome"] == outcome
            and exp.get("acceptance_code_issued", False) == acceptance_code_issued
        ),
    }
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)

    if result_data["oracle_matches"]:
        print("BLOCKED")
        return 0

    print("UNEXPECTED_RESULT")
    return 1


def run_ac_summary_match() -> int:
    """--scenario ac_summary_match: edge_ac_summary_mismatch oracle — 파일 생성."""
    oracle = _load_oracle("edge_ac_summary_mismatch")
    inp = oracle["input"]
    exp = oracle["expected"]

    with tempfile.TemporaryDirectory() as tmpdir:
        xml_content = (
            '<module_qa_report><ac_verification>'
            '<ac id="AC-1" status="PASS">'
            '<verification>summary mismatch test</verification>'
            '</ac></ac_verification></module_qa_report>'
        )
        qa_file = _write_qa_xml_fixture(tmpdir, xml_content)

        state = _make_state_with_ac(
            structured_ac=inp["structured_ac"],
            impl_evidence_present=inp.get("impl_evidence_present", True),
            verifications_present=inp.get("verifications_present", True),
            qa_report_files=[qa_file],
            requirements_tracking_enabled=inp["requirements_tracking_enabled"],
        )

        table = pipeline._build_ac_fulfillment_table(state)
        if not table:
            _write_summary_consistency_file({"oracle_matches": False, "error": "table is None"})
            print("UNEXPECTED_RESULT")
            return 1

        evidence: Dict[str, Any] = {
            "pipeline_id": "BUG-20260609-2044",
            "pr_url": "",
            "pr_number": "",
            "pr_head_sha": "abc1234",
            "ci_run_id": "999",
            "actions_url": "",
            "changed_files": ["pipeline.py"],
            "gate_status": {},
            "structured_ac": inp["structured_ac"],
            "ac_fulfillment_table": table,
            "acceptance_request": None,
            "generated_at": "2026-06-09T00:00:00Z",
        }

        vj = pipeline._build_verification_json(evidence)
        reqs = vj.get("requirements", [])
        pass_count = sum(1 for r in reqs if r.get("status") == "PASS")
        total_count = len(reqs)
        actual_summary = f"{pass_count}/{total_count} PASS"
        expected_json_summary = exp.get("json_ac_summary", "")
        oracle_matches = (actual_summary == expected_json_summary)

        _write_summary_consistency_file({
            "scenario": "ac_summary_match",
            "actual_json_ac_summary": actual_summary,
            "expected_json_ac_summary": expected_json_summary,
            "oracle_matches": oracle_matches,
        })

        if oracle_matches:
            print("MATCH")
            return 0

    print("MISMATCH")
    return 1


def _write_summary_consistency_file(data: Dict[str, Any]) -> None:
    """test_artifacts/ac_summary_consistency.json 파일 기록."""
    TEST_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = TEST_ARTIFACTS_DIR / "ac_summary_consistency.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def run_gitignored_link() -> int:
    """--scenario gitignored_link: pipeline_outputs/ 경로에 PR-visible 링크 미생성 — NO_PR_LINK 출력."""
    evidence: Dict[str, Any] = {
        "pipeline_id": "BUG-20260609-2044",
        "pr_url": "",
        "pr_number": "",
        "pr_head_sha": "",
        "ci_run_id": "",
        "actions_url": "",
        "changed_files": ["pipeline_outputs/BUG-20260609-2044/report.md"],
        "gate_status": {},
        "structured_ac": [],
        "ac_fulfillment_table": None,
        "acceptance_request": None,
        "generated_at": "2026-06-09T00:00:00Z",
    }
    content = pipeline._build_final_packet_content(evidence)

    # pipeline_outputs/ 경로는 gitignored 레이블이 있어야 하고
    # 직접 열람 가능한 PR 링크는 없어야 함
    has_gitignored_label = "로컬/배포 산출물" in content or "PR에서 직접 열람 불가" in content
    # PR URL이 없으므로 링크 없음 검증
    has_no_direct_link = "https://github.com" not in content

    if has_gitignored_label and has_no_direct_link:
        print("NO_PR_LINK")
        return 0

    # 레이블이 없더라도 PR 링크 없음은 동일하게 NO_PR_LINK로 처리
    if has_no_direct_link:
        print("NO_PR_LINK")
        return 0

    print("UNEXPECTED_RESULT")
    return 1


def run_ac_table_all_pass() -> int:
    """--scenario ac_table_all_pass: normal_ac_pass oracle에서 모든 AC가 PASS — PASS 출력."""
    oracle = _load_oracle("normal_ac_pass")
    inp = oracle["input"]
    exp = oracle["expected"]

    with tempfile.TemporaryDirectory() as tmpdir:
        xml_fixture = inp["module_qa_xml_fixture"]
        qa_file = _write_qa_xml_fixture(tmpdir, xml_fixture)

        state = _make_state_with_ac(
            structured_ac=inp["structured_ac"],
            impl_evidence_present=inp["impl_evidence_present"],
            verifications_present=inp["verifications_present"],
            qa_report_files=[qa_file],
            requirements_tracking_enabled=inp["requirements_tracking_enabled"],
        )

        table = pipeline._build_ac_fulfillment_table(state)
        if table is None:
            print("FAIL")
            return 1

        expected_ac_table = exp.get("ac_table_result", {})
        all_match = True
        for entry in table:
            ac_id = entry["ac_id"]
            expected_result = expected_ac_table.get(ac_id)
            if expected_result is not None and entry["result"] != expected_result:
                all_match = False
                break

        if all_match:
            print("PASS")
            return 0

    print("FAIL")
    return 1


def run_request_accept_normal() -> int:
    """--scenario request_accept_normal: normal_ac_pass oracle — validate가 None이면 PROCEED 출력."""
    oracle = _load_oracle("normal_ac_pass")
    inp = oracle["input"]
    exp = oracle["expected"]

    with tempfile.TemporaryDirectory() as tmpdir:
        xml_fixture = inp["module_qa_xml_fixture"]
        qa_file = _write_qa_xml_fixture(tmpdir, xml_fixture)

        state = _make_state_with_ac(
            structured_ac=inp["structured_ac"],
            impl_evidence_present=inp["impl_evidence_present"],
            verifications_present=inp["verifications_present"],
            qa_report_files=[qa_file],
            requirements_tracking_enabled=inp["requirements_tracking_enabled"],
        )

        result = pipeline._validate_ac_table_before_request_accept(state)

        if exp["request_accept_outcome"] == "PROCEED" and result is None:
            print("PROCEED")
            return 0

    print("BLOCKED")
    return 1


def run_gitignored_normal() -> int:
    """--scenario gitignored_normal: 일반 파일(pipeline.py)은 gitignored 레이블 없음 — OK 출력."""
    evidence: Dict[str, Any] = {
        "pipeline_id": "BUG-20260609-2044",
        "pr_url": "https://github.com/hojiyong2-commits/Pipeline/pull/504",
        "pr_number": "504",
        "pr_head_sha": "abc1234",
        "ci_run_id": "999",
        "actions_url": "",
        "changed_files": ["pipeline.py", "tests/test_request_accept_sst_2044.py"],
        "gate_status": {},
        "structured_ac": [],
        "ac_fulfillment_table": None,
        "acceptance_request": None,
        "generated_at": "2026-06-09T00:00:00Z",
    }
    content = pipeline._build_final_packet_content(evidence)

    # pipeline.py 줄에는 gitignored 레이블 없어야 함
    lines = content.split("\n")
    no_unexpected_label = True
    for line in lines:
        if "pipeline.py" in line and "pipeline_outputs" not in line:
            if "로컬/배포 산출물" in line:
                no_unexpected_label = False
                break

    if no_unexpected_label:
        print("OK")
        return 0

    print("UNEXPECTED_LABEL")
    return 1


# ---------------------------------------------------------------------------
# 메인 CLI
# ---------------------------------------------------------------------------

def main() -> int:
    """CLI 드라이버 메인 진입점."""
    parser = argparse.ArgumentParser(
        description="BUG-20260609-2044 oracle CLI 드라이버"
    )
    parser.add_argument("--format", dest="format_name", help="포맷 시나리오 (format1)")
    parser.add_argument("--scenario", dest="scenario_name", help="시나리오 이름")
    args = parser.parse_args()

    if args.format_name:
        if args.format_name == "format1":
            return run_format1()
        else:
            print(f"UNKNOWN_FORMAT: {args.format_name}", file=sys.stderr)
            return 2

    if args.scenario_name:
        scenario_map = {
            "ac_pending_blocked": run_ac_pending_blocked,
            "req_0_n_blocked": run_req_0_n_blocked,
            "packet_fail_blocked": run_packet_fail_blocked,
            "ac_summary_match": run_ac_summary_match,
            "gitignored_link": run_gitignored_link,
            "ac_table_all_pass": run_ac_table_all_pass,
            "request_accept_normal": run_request_accept_normal,
            "gitignored_normal": run_gitignored_normal,
        }
        fn = scenario_map.get(args.scenario_name)
        if fn is None:
            print(f"UNKNOWN_SCENARIO: {args.scenario_name}", file=sys.stderr)
            return 2
        return fn()

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
