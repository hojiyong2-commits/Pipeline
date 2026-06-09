"""tests/test_request_accept_sst_2044.py

BUG-20260609-2044: gates request-accept AC tracking 0/N PASS 방어 및
final packet SSoT 단일화 — 승인 코드 발급 전 패킷 생성 원자성 보장.

검증 대상 함수:
  - _get_qa_verification_for_ac
  - _validate_ac_table_before_request_accept
  - _build_ac_fulfillment_table
  - _cmd_gates_request_accept (final packet 예외 BLOCKED 경로)
  - _build_verification_json (requirements[*]["status"] = ac_table[*]["result"])
  - _build_final_packet_content (pipeline_outputs/ 경로 레이블)

oracle 파일: tests/oracles/BUG-20260609-2044/
  - normal_ac_pass             (normal) — 모든 AC PASS → request-accept 정상 진행
  - edge_ac_pending_blocked    (edge)   — AC verification 없음 → BLOCKED
  - edge_req_0_n_blocked       (edge)   — 0/N PASS 상태 → BLOCKED
  - edge_packet_fail_blocked   (edge)   — packet 생성 예외 → BLOCKED
  - edge_ac_summary_mismatch   (edge)   — packet/JSON AC summary 불일치 → 재생성 후 일치
"""
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

# pipeline.py 직접 import
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pipeline  # noqa: E402


ORACLE_BASE = _PROJECT_ROOT / "tests" / "oracles" / "BUG-20260609-2044"


# ---------------------------------------------------------------------------
# Helper: oracle 로드
# ---------------------------------------------------------------------------

def _load_oracle(case_name: str) -> Dict[str, Any]:
    """oracle 케이스 input/expected 로드."""
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
    """테스트용 pipeline state 생성.

    impl_evidence_present=True이면 모든 AC를 커버하는 MT-1을 dev PASS 상태로 추가하고
    atomic_plan.micro_tasks에 covers_ac 연결을 포함한다.
    (_get_impl_evidence_for_ac는 atomic_plan.micro_tasks[*].covers_ac와
     module_gates.modules[*].dev.status="PASS" 조합으로 구현 근거를 반환한다)
    """
    # AC ID 목록 추출
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
            # atomic_plan covers_ac 연결 — impl_evidence_present일 때만
            if impl_evidence_present and ac_ids:
                micro_tasks_for_plan.append({
                    "id": mt_id,
                    "covers_ac": ac_ids,
                })
    elif impl_evidence_present:
        # QA report 없이 impl_evidence만 있는 경우
        modules["MT-1"] = {
            "id": "MT-1",
            "status": "QA_PASS",
            "dev": {"status": "PASS", "scope": {"files": ["pipeline.py"]}},
            "qa": {"status": "PASS", "report_file": None},
        }
        if ac_ids:
            micro_tasks_for_plan.append({
                "id": "MT-1",
                "covers_ac": ac_ids,
            })

    state: Dict[str, Any] = {
        "pipeline_id": "BUG-20260609-2044",
        "requirements_tracking": {"enabled": requirements_tracking_enabled},
        "structured_acceptance_criteria": structured_ac,
        "module_gates": {
            "enabled": True,
            "modules": modules,
        },
        "atomic_plan": {
            "micro_tasks": micro_tasks_for_plan,
        },
    }
    return state


def _write_qa_xml_fixture(tmpdir: str, xml_content: str, filename: str = "module_qa_MT-1.xml") -> str:
    """임시 디렉터리에 QA XML fixture 파일 작성 후 경로 반환."""
    path = os.path.join(tmpdir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml_content)
    return path


# ---------------------------------------------------------------------------
# Scenario 1: normal_ac_pass
# Format1 XML fixture에서 AC table이 PASS로 생성되고 validate가 None 반환
# ---------------------------------------------------------------------------

class TestNormalAcPass(unittest.TestCase):
    """Oracle: normal_ac_pass — 모든 AC가 PASS로 계산되면 validate는 None 반환."""

    def test_normal_ac_pass_validate_returns_none(self) -> None:
        """normal_ac_pass: validate_ac_table_before_request_accept가 None 반환 (oracle)."""
        oracle = _load_oracle("normal_ac_pass")
        inp = oracle["input"]
        exp = oracle["expected"]

        with tempfile.TemporaryDirectory() as tmpdir:
            # QA XML fixture 작성 (Format1)
            xml_fixture = inp["module_qa_xml_fixture"]
            qa_file = _write_qa_xml_fixture(tmpdir, xml_fixture)

            state = _make_state_with_ac(
                structured_ac=inp["structured_ac"],
                impl_evidence_present=inp["impl_evidence_present"],
                verifications_present=inp["verifications_present"],
                qa_report_files=[qa_file],
                requirements_tracking_enabled=inp["requirements_tracking_enabled"],
            )

            # _get_qa_verification_for_ac: Format1에서 verifications 수집
            for ac in inp["structured_ac"]:
                verifs = pipeline._get_qa_verification_for_ac(state, ac["ac_id"])
                if inp["verifications_present"]:
                    self.assertGreater(
                        len(verifs), 0,
                        f"{ac['ac_id']}: verifications should be non-empty for normal_ac_pass"
                    )

            # _validate_ac_table_before_request_accept: PASS 상태 → None 반환
            result = pipeline._validate_ac_table_before_request_accept(state)
            if exp["request_accept_outcome"] == "PROCEED":
                self.assertIsNone(
                    result,
                    f"normal_ac_pass: validate should return None, got: {result}"
                )

    def test_normal_ac_table_all_pass(self) -> None:
        """normal_ac_pass: _build_ac_fulfillment_table에서 모든 AC가 PASS (oracle)."""
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
            self.assertIsNotNone(table)
            assert table is not None  # type checker
            for entry in table:
                ac_id = entry["ac_id"]
                expected_result = exp["ac_table_result"].get(ac_id)
                if expected_result is not None:
                    self.assertEqual(
                        entry["result"],
                        expected_result,
                        f"{ac_id}: expected result={expected_result}, got={entry['result']}"
                    )


# ---------------------------------------------------------------------------
# Scenario 2: edge_ac_pending_blocked
# AC verification 없는 fixture → BLOCKED (oracle)
# ---------------------------------------------------------------------------

class TestEdgeAcPendingBlocked(unittest.TestCase):
    """Oracle: edge_ac_pending_blocked — verification 없으면 request-accept BLOCKED."""

    def test_empty_verification_is_blocked(self) -> None:
        """edge_ac_pending_blocked: 빈 <ac_verification> fixture → validate가 오류 메시지 반환."""
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

            # oracle: request_accept_outcome = "BLOCKED"
            self.assertEqual(exp["request_accept_outcome"], "BLOCKED")
            self.assertIsNotNone(
                result,
                "edge_ac_pending_blocked: validate should return error message, got None"
            )
            assert result is not None
            blocker_phrase = exp.get("blocker_message_contains", "미완료 항목")
            self.assertIn(
                blocker_phrase,
                result,
                f"Blocker message should contain '{blocker_phrase}', got: {result}"
            )

    def test_acceptance_code_not_in_output_when_blocked(self) -> None:
        """edge_ac_pending_blocked: BLOCKED 시 validate가 None이 아님을 확인 (승인 코드 미발급 보장)."""
        oracle = _load_oracle("edge_ac_pending_blocked")
        inp = oracle["input"]

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
            # Non-None result means _cmd_gates_request_accept will call _die → no acceptance_code output
            self.assertIsNotNone(result)


# ---------------------------------------------------------------------------
# Scenario 3: edge_req_0_n_blocked
# requirements_summary 0/N 상태 → 승인 코드 미출력 (oracle)
# ---------------------------------------------------------------------------

class TestEdgeReqZeroNBlocked(unittest.TestCase):
    """Oracle: edge_req_0_n_blocked — 0/N PASS 상태에서 승인 코드 미발급."""

    def test_zero_n_validate_blocked(self) -> None:
        """edge_req_0_n_blocked: impl_evidence=False, verifications=False → BLOCKED."""
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

            # oracle: request_accept_outcome = "BLOCKED"
            self.assertEqual(exp["request_accept_outcome"], "BLOCKED")
            self.assertIsNotNone(
                result,
                "edge_req_0_n_blocked: validate should return error message for 0/N state"
            )

    def test_zero_n_ac_table_all_pending(self) -> None:
        """edge_req_0_n_blocked: _build_ac_fulfillment_table에서 모든 AC가 PENDING."""
        oracle = _load_oracle("edge_req_0_n_blocked")
        inp = oracle["input"]

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
            self.assertIsNotNone(table)
            assert table is not None
            for entry in table:
                self.assertEqual(
                    entry["result"],
                    "PENDING",
                    f"{entry['ac_id']}: expected PENDING in 0/N state, got {entry['result']}"
                )


# ---------------------------------------------------------------------------
# Scenario 4: edge_packet_fail_blocked
# final packet 생성 예외 시 request-accept BLOCKED (oracle)
# ---------------------------------------------------------------------------

class TestEdgePacketFailBlocked(unittest.TestCase):
    """Oracle: edge_packet_fail_blocked — packet 생성 실패 시 request-accept BLOCKED."""

    def test_packet_generation_failure_causes_blocked(self) -> None:
        """edge_packet_fail_blocked: _auto_generate_final_packet_and_update_pr OSError → SystemExit."""
        oracle = _load_oracle("edge_packet_fail_blocked")
        inp = oracle["input"]
        exp = oracle["expected"]

        with tempfile.TemporaryDirectory() as tmpdir:
            # AC가 모두 PASS인 상태 (validate는 통과)
            xml_content = (
                '<module_qa_report><ac_verification>'
                '<ac id="AC-1" status="PASS">'
                '<verification>packet fail test verification</verification>'
                '</ac></ac_verification></module_qa_report>'
            )
            qa_file = _write_qa_xml_fixture(tmpdir, xml_content)

            state = _make_state_with_ac(
                structured_ac=inp["structured_ac"],
                impl_evidence_present=inp["impl_evidence_present"],
                verifications_present=inp["verifications_present"],
                qa_report_files=[qa_file],
                requirements_tracking_enabled=inp["requirements_tracking_enabled"],
            )

            # MT-3 수정 검증: OSError 발생 시 SystemExit(1) — _die() 호출
            # _cmd_gates_request_accept 내 auto_generate 호출 부분을 mock으로 주입
            def _mock_auto_generate(*args: Any, **kwargs: Any) -> Dict[str, Any]:
                raise OSError("Simulated packet generation failure")

            orig_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)

                # _auto_generate_final_packet_and_update_pr를 OSError를 발생시키는 mock으로 교체
                with patch.object(
                    pipeline,
                    "_auto_generate_final_packet_and_update_pr",
                    side_effect=OSError("Simulated packet generation failure"),
                ):
                    # _cmd_gates_request_accept 내 패킷 생성 실패가 SystemExit(1)을 일으키는지 확인
                    # _validate_ac_table_before_request_accept: None (PASS) 상태로 validate mock
                    with patch.object(
                        pipeline,
                        "_validate_ac_table_before_request_accept",
                        return_value=None,
                    ):
                        with patch.object(pipeline, "_get_pr_body_text", return_value=""):
                            with patch.object(pipeline, "_get_current_pr_url", return_value=""):
                                with patch.object(pipeline, "_get_current_pr_head_sha", return_value=""):
                                    with patch.object(pipeline, "_get_pr_branch_ci_run_id", return_value=""):
                                        with patch.object(pipeline, "_get_git_diff_files", return_value=[]):
                                            with patch.object(pipeline, "_check_packet_freshness_against_actual", return_value=None):
                                                with patch.object(pipeline, "_load_acceptance_request", return_value=None):
                                                    with patch.object(pipeline, "_write_acceptance_request", return_value={"nonce": "TESTNONCE", "request_id": "R1"}):
                                                        with patch.object(pipeline, "_build_ac_fulfillment_table", return_value=None):
                                                            with patch.object(pipeline, "_update_github_acceptance_comment", return_value=None):
                                                                args_ns = MagicMock()
                                                                args_ns.evidence = "test_evidence.md"
                                                                args_ns.force_new_code = False

                                                                # OSError → _die() → SystemExit(1) 확인
                                                                with self.assertRaises(SystemExit) as cm:
                                                                    pipeline._cmd_gates_request_accept(args_ns, state)
                                                                self.assertEqual(cm.exception.code, 1)

            finally:
                os.chdir(orig_cwd)

        # oracle: request_accept_outcome = "BLOCKED"
        self.assertEqual(exp["request_accept_outcome"], "BLOCKED")
        self.assertFalse(exp["acceptance_code_issued"])


# ---------------------------------------------------------------------------
# Scenario 5: edge_ac_summary_mismatch
# human_acceptance_packet.json과 PR 본문의 AC summary 일치 검증
# (packet 재생성 후 양쪽이 동일해야 함)
# ---------------------------------------------------------------------------

class TestEdgeAcSummaryMismatch(unittest.TestCase):
    """Oracle: edge_ac_summary_mismatch — packet/JSON AC summary 재생성 후 일치."""

    def test_verification_json_requirements_status_uses_result_key(self) -> None:
        """MT-4 버그 수정: _build_verification_json의 requirements[*]["status"]는 ac_table[*]["result"] 사용."""
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
            self.assertIsNotNone(table)
            assert table is not None

            # evidence dict 구성
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
            self.assertGreater(len(reqs), 0)

            # MT-4 버그 수정 검증: "result" 키 사용 → UNKNOWN이 아닌 올바른 값
            for req_entry in reqs:
                status_val = req_entry.get("status", "")
                self.assertNotEqual(
                    status_val,
                    "UNKNOWN",
                    f"MT-4 fix: requirements[*]['status'] should not be UNKNOWN; ac_table has 'result' not 'status'. Got: {status_val}"
                )
                self.assertIn(
                    status_val,
                    ["PASS", "PENDING", "FAIL"],
                    f"requirements[*]['status'] should be PASS/PENDING/FAIL, got: {status_val}"
                )

            # oracle: json_ac_summary = "1/1 PASS"
            pass_count = sum(1 for r in reqs if r.get("status") == "PASS")
            total_count = len(reqs)
            actual_summary = f"{pass_count}/{total_count} PASS"
            expected_summary = exp["json_ac_summary"]
            self.assertEqual(
                actual_summary,
                expected_summary,
                f"JSON AC summary mismatch: expected {expected_summary}, got {actual_summary}"
            )


# ---------------------------------------------------------------------------
# Scenario 6: pipeline_outputs gitignored 경로 PR-visible 링크 미생성
# _build_final_packet_content에서 pipeline_outputs/ 경로에 레이블 추가 확인
# ---------------------------------------------------------------------------

class TestPipelineOutputsLabel(unittest.TestCase):
    """MT-5: _build_final_packet_content에서 pipeline_outputs/ 경로는 PR 직접 열람 불가 레이블."""

    def _make_evidence(
        self,
        changed_files: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """테스트용 evidence dict."""
        return {
            "pipeline_id": "BUG-20260609-2044",
            "pr_url": "",
            "pr_number": "",
            "pr_head_sha": "",
            "ci_run_id": "",
            "actions_url": "",
            "changed_files": changed_files or [],
            "gate_status": {},
            "structured_ac": [],
            "ac_fulfillment_table": None,
            "acceptance_request": None,
            "generated_at": "2026-06-09T00:00:00Z",
        }

    def test_pipeline_outputs_path_has_label(self) -> None:
        """pipeline_outputs/ 경로에 '로컬/배포 산출물 — PR에서 직접 열람 불가' 레이블 포함."""
        evidence = self._make_evidence(
            changed_files=[
                "pipeline.py",
                "pipeline_outputs/BUG-20260609-2044/report.md",
            ]
        )
        content = pipeline._build_final_packet_content(evidence)
        self.assertIn(
            "pipeline_outputs/BUG-20260609-2044/report.md",
            content,
            "pipeline_outputs path should appear in packet content"
        )
        self.assertIn(
            "로컬/배포 산출물 — PR에서 직접 열람 불가",
            content,
            "pipeline_outputs path should have gitignored label"
        )

    def test_non_pipeline_outputs_path_no_label(self) -> None:
        """pipeline.py 같은 일반 파일 경로에는 gitignored 레이블 없음."""
        evidence = self._make_evidence(
            changed_files=["pipeline.py", "tests/test_foo.py"]
        )
        content = pipeline._build_final_packet_content(evidence)
        # pipeline.py 줄에는 "(로컬/배포 산출물..." 레이블 없음
        lines = content.split("\n")
        for line in lines:
            if "pipeline.py" in line and "pipeline_outputs" not in line:
                self.assertNotIn(
                    "로컬/배포 산출물",
                    line,
                    f"Non-pipeline_outputs path should not have gitignored label: {line}"
                )

    def test_empty_changed_files_no_label(self) -> None:
        """changed_files가 비어있으면 gitignored 레이블 없음."""
        evidence = self._make_evidence(changed_files=[])
        content = pipeline._build_final_packet_content(evidence)
        self.assertNotIn("로컬/배포 산출물", content)


if __name__ == "__main__":
    unittest.main()
