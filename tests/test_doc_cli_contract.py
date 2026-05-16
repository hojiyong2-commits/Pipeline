"""
tests/test_doc_cli_contract.py
------------------------------
IMP-20260515-020F: 문서-CLI contract 검증 테스트

pipeline.py의 구현이 CLAUDE.md/step_plan.xml에 기술된 CLI 계약을 충족하는지 검증합니다.

검증 항목:
  1. pm_planner phase 구현 검증
  2. pipeline_manager phase 구현 검증
  3. outputs add/status 서브커맨드 구현 검증
  4. patch plan/audit/verify/attest 서브커맨드 구현 검증
  5. done --phase pm argparse 인수 검증 (planner-run-id, manager-run-id, manager-report)
  6. 레거시 pm 호환 (agent_run_id)
"""
import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pipeline


# ---------------------------------------------------------------------------
# 1. pm_planner phase 구현 검증
# ---------------------------------------------------------------------------

class TestPmPlannerPhaseContract:
    """AGENT_RUN_PHASES에 pm_planner가 있고, PHASE_AGENT_IDS가 pm-planner-agent로 매핑돼야 한다."""

    def test_pm_planner_in_agent_run_phases(self) -> None:
        assert "pm_planner" in pipeline.AGENT_RUN_PHASES, (
            "pm_planner 페이즈가 AGENT_RUN_PHASES에 없습니다. "
            "agent start --phase pm_planner 명령이 작동하지 않습니다."
        )

    def test_pm_planner_agent_id_is_correct(self) -> None:
        assert pipeline.PHASE_AGENT_IDS.get("pm_planner") == "pm-planner-agent", (
            f"pm_planner 에이전트 ID가 잘못됐습니다: "
            f"expected 'pm-planner-agent', got '{pipeline.PHASE_AGENT_IDS.get('pm_planner')}'"
        )

    def test_pm_planner_receipt_run_phase_maps_to_pm_planner(self) -> None:
        # PHASE_RECEIPT_RUN_PHASES["pm"] == "pm_planner" 이어야 pm receipt가 pm_planner로 검증됨
        assert pipeline.PHASE_RECEIPT_RUN_PHASES.get("pm") == "pm_planner", (
            "PHASE_RECEIPT_RUN_PHASES['pm']이 'pm_planner'가 아닙니다. "
            "pm done 시 receipt 검증이 실패합니다."
        )


# ---------------------------------------------------------------------------
# 2. pipeline_manager phase 구현 검증
# ---------------------------------------------------------------------------

class TestPipelineManagerPhaseContract:
    """AGENT_RUN_PHASES에 pipeline_manager가 있고, PHASE_AGENT_IDS가 pipeline-manager-agent로 매핑돼야 한다."""

    def test_pipeline_manager_in_agent_run_phases(self) -> None:
        assert "pipeline_manager" in pipeline.AGENT_RUN_PHASES, (
            "pipeline_manager 페이즈가 AGENT_RUN_PHASES에 없습니다."
        )

    def test_pipeline_manager_agent_id_is_correct(self) -> None:
        assert pipeline.PHASE_AGENT_IDS.get("pipeline_manager") == "pipeline-manager-agent", (
            f"pipeline_manager 에이전트 ID가 잘못됐습니다: "
            f"expected 'pipeline-manager-agent', got '{pipeline.PHASE_AGENT_IDS.get('pipeline_manager')}'"
        )


# ---------------------------------------------------------------------------
# 3. 레거시 pm phase 호환 검증
# ---------------------------------------------------------------------------

class TestLegacyPmPhaseCompat:
    """레거시 pm phase도 AGENT_RUN_PHASES에 있어야 하고, pm-agent로 매핑돼야 한다."""

    def test_legacy_pm_in_agent_run_phases(self) -> None:
        assert "pm" in pipeline.AGENT_RUN_PHASES, (
            "레거시 'pm' 페이즈가 AGENT_RUN_PHASES에서 제거됐습니다. "
            "구 state 호환을 위해 유지해야 합니다."
        )

    def test_legacy_pm_agent_id_is_pm_agent(self) -> None:
        assert pipeline.PHASE_AGENT_IDS.get("pm") == "pm-agent", (
            f"레거시 pm 에이전트 ID가 잘못됐습니다: "
            f"expected 'pm-agent', got '{pipeline.PHASE_AGENT_IDS.get('pm')}'"
        )


# ---------------------------------------------------------------------------
# 4. outputs add/status argparse 구현 검증
# ---------------------------------------------------------------------------

class TestOutputsCliContract:
    """outputs add와 outputs status 서브커맨드가 argparse에 등록돼야 한다."""

    def _build_parser(self):
        return pipeline.build_parser()

    def test_outputs_add_subcommand_exists(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args([
            "outputs", "add",
            "--kind", "report",
            "--path", "report.md",
            "--label", "보고서",
        ])
        assert args.outputs_action == "add"
        assert args.kind == "report"
        assert args.path == "report.md"
        assert args.label == "보고서"

    def test_outputs_add_no_copy_flag(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args([
            "outputs", "add",
            "--kind", "log",
            "--path", "run.log",
            "--no-copy",
        ])
        assert args.no_copy is True

    def test_outputs_status_subcommand_exists(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(["outputs", "status"])
        assert args.outputs_action == "status"


# ---------------------------------------------------------------------------
# 5. patch 서브커맨드 argparse 구현 검증
# ---------------------------------------------------------------------------

class TestPatchCliContract:
    """patch plan/audit/verify/attest 서브커맨드가 argparse에 등록돼야 한다."""

    def _build_parser(self):
        return pipeline.build_parser()

    def test_patch_plan_subcommand_exists(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(["patch", "plan", "--plan", "my_patch.json"])
        assert args.patch_sub == "plan"
        assert args.plan == "my_patch.json"

    def test_patch_audit_subcommand_exists(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(["patch", "audit", "--plan", "my_patch.json"])
        assert args.patch_sub == "audit"

    def test_patch_verify_subcommand_has_test_command(self) -> None:
        """patch verify에 --test-command 인수가 있어야 한다 (Item 7)."""
        parser = self._build_parser()
        args = parser.parse_args([
            "patch", "verify",
            "--plan", "my_patch.json",
            "--result", "PASS",
            "--test-command", "python -m pytest -q",
        ])
        assert args.test_command == "python -m pytest -q"
        assert args.result == "PASS"

    def test_patch_verify_subcommand_has_evidence_file(self) -> None:
        """patch verify에 --evidence-file 인수가 있어야 한다 (Item 7)."""
        parser = self._build_parser()
        args = parser.parse_args([
            "patch", "verify",
            "--plan", "my_patch.json",
            "--result", "PASS",
            "--evidence-file", "test_output.txt",
        ])
        assert args.evidence_file == "test_output.txt"

    def test_patch_attest_subcommand_exists(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args(["patch", "attest", "--plan", "my_patch.json"])
        assert args.patch_sub == "attest"


# ---------------------------------------------------------------------------
# 6. done --phase pm argparse 인수 검증
# ---------------------------------------------------------------------------

class TestDonePmArgparseContract:
    """done --phase pm에 planner-run-id, manager-run-id, manager-report 인수가 있어야 한다."""

    def _build_parser(self):
        return pipeline.build_parser()

    def test_done_pm_has_planner_run_id(self) -> None:
        parser = self._build_parser()
        args = parser.parse_args([
            "done", "--phase", "pm",
            "--report-file", "step_plan.xml",
            "--decomp", "--clarification", "--roadmap",
            "--planner-run-id", "pm_planner-test-abc",
            "--manager-run-id", "pipeline_manager-test-xyz",
            "--manager-report", "manager_handoff.xml",
        ])
        assert args.planner_run_id == "pm_planner-test-abc"
        assert args.manager_run_id == "pipeline_manager-test-xyz"
        assert args.manager_report == "manager_handoff.xml"

    def test_done_pm_legacy_agent_run_id_still_parseable(self) -> None:
        """레거시 --agent-run-id도 argparse에서 파싱돼야 한다."""
        parser = self._build_parser()
        args = parser.parse_args([
            "done", "--phase", "pm",
            "--report-file", "step_plan.xml",
            "--decomp", "--clarification", "--roadmap",
            "--agent-run-id", "pm-legacy-run",
        ])
        assert args.agent_run_id == "pm-legacy-run"
        # 새 인수는 None이어야 함
        assert args.planner_run_id is None
        assert args.manager_run_id is None


# ---------------------------------------------------------------------------
# 7. patch verify PASS에 증거 요구 (Item 7)
# ---------------------------------------------------------------------------

class TestPatchVerifyEvidenceContract:
    """patch verify --result PASS에 --test-command 또는 --evidence-file이 없으면 실패해야 한다."""

    def test_patch_verify_pass_without_evidence_fails(self) -> None:
        """증거 없이 patch verify PASS → SystemExit 1."""
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        plan_data = {
            "schema_version": 1,
            "pipeline_id": "TMP-VERIFY-NO-EVIDENCE",
            "patch_scope": {"file": "x.py", "function": "f", "expected_lines_changed_max": 5},
            "forbidden": {
                "trust_root_changes": False,
                "new_dependencies": False,
                "file_move_or_delete": False,
                "packaging_changes": False,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = Path(tmp) / "patch_plan.json"
            plan_file.write_text(json.dumps(plan_data), encoding="utf-8")
            args = argparse.Namespace(
                plan=str(plan_file),
                result="PASS",
                test_command="",
                evidence_file="",
            )
            with mock.patch.object(pipeline, "_load_state", return_value={}), \
                 mock.patch.object(pipeline, "_save_state"):
                with pytest.raises(SystemExit) as exc:
                    pipeline._cmd_patch_verify(args)
                assert exc.value.code == 1

    def test_patch_verify_pass_with_test_command_succeeds(self) -> None:
        """--test-command 제공 시 patch verify PASS 성공."""
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        plan_data = {
            "schema_version": 1,
            "pipeline_id": "TMP-VERIFY-WITH-CMD",
            "patch_scope": {"file": "x.py", "function": "f", "expected_lines_changed_max": 5},
            "forbidden": {
                "trust_root_changes": False,
                "new_dependencies": False,
                "file_move_or_delete": False,
                "packaging_changes": False,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = Path(tmp) / "patch_plan.json"
            plan_file.write_text(json.dumps(plan_data), encoding="utf-8")
            state = {}
            args = argparse.Namespace(
                plan=str(plan_file),
                result="PASS",
                test_command="python -m pytest -q",
                evidence_file="",
            )
            with mock.patch.object(pipeline, "_load_state", return_value=state), \
                 mock.patch.object(pipeline, "_save_state"), \
                 mock.patch("builtins.print"):
                # SystemExit이 발생하지 않아야 함
                pipeline._cmd_patch_verify(args)

        assert state.get("patch_lane", {}).get("verify_passed") is True
        assert state["patch_lane"]["verify_test_command"] == "python -m pytest -q"


# ---------------------------------------------------------------------------
# 8. patch attest 게이트 (Item 6)
# ---------------------------------------------------------------------------

class TestPatchAttestGateContract:
    """patch attest는 plan/audit/verify PASS 없이는 실패해야 한다."""

    def test_patch_attest_without_prerequisites_fails(self) -> None:
        """plan/audit/verify PASS 없이 patch attest → SystemExit 1."""
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        plan_data = {
            "schema_version": 1,
            "pipeline_id": "TMP-ATTEST-NO-PRE",
            "patch_scope": {"file": "x.py", "function": "f", "expected_lines_changed_max": 5},
            "forbidden": {
                "trust_root_changes": False,
                "new_dependencies": False,
                "file_move_or_delete": False,
                "packaging_changes": False,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = Path(tmp) / "patch_plan.json"
            plan_file.write_text(json.dumps(plan_data), encoding="utf-8")
            args = argparse.Namespace(plan=str(plan_file))
            # 빈 state = plan/audit/verify PASS 없음
            with mock.patch.object(pipeline, "_load_state", return_value={}), \
                 mock.patch.object(pipeline, "_save_state"):
                with pytest.raises(SystemExit) as exc:
                    pipeline._cmd_patch_attest(args)
                assert exc.value.code == 1

    def test_patch_attest_after_all_passes_succeeds(self) -> None:
        """plan+audit+verify PASS 후 patch attest 성공."""
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        plan_data = {
            "schema_version": 1,
            "pipeline_id": "TMP-ATTEST-ALL-PASS",
            "patch_scope": {"file": "x.py", "function": "f", "expected_lines_changed_max": 5},
            "forbidden": {
                "trust_root_changes": False,
                "new_dependencies": False,
                "file_move_or_delete": False,
                "packaging_changes": False,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            plan_file = Path(tmp) / "patch_plan.json"
            plan_file.write_text(json.dumps(plan_data), encoding="utf-8")
            args = argparse.Namespace(plan=str(plan_file))
            state = {
                "patch_lane": {
                    "plan_passed": True,
                    "audit_passed": True,
                    "verify_passed": True,
                }
            }
            with mock.patch.object(pipeline, "_load_state", return_value=state), \
                 mock.patch.object(pipeline, "_save_state"), \
                 mock.patch("builtins.print"):
                # 예외 없이 성공해야 함
                pipeline._cmd_patch_attest(args)

        assert state["patch_lane"]["attested_at"] is not None
