import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest import mock

import pipeline
from core.contracts import build_initial_contract, build_initial_test_set


def _write_architect_report(
    directory: str,
    *,
    required: bool = False,
    reason: str = "none",
    scope: str = "none",
) -> Path:
    report = Path(directory) / "architect_report.xml"
    report.write_text(
        f"""<optimization_report>
  <protocol_evolution_decision>
    <required>{str(required).lower()}</required>
    <reason>{reason}</reason>
    <scope>{scope}</scope>
    <recommended_pipeline_type>IMP</recommended_pipeline_type>
  </protocol_evolution_decision>
</optimization_report>
""",
        encoding="utf-8",
    )
    return report


def _write_dev_handover_report(directory: str) -> Path:
    report = Path(directory) / "dev_handover.xml"
    report.write_text(
        """<handover>
  <from>dev-agent</from>
</handover>
<impact_analysis>
  <files_changed>1</files_changed>
</impact_analysis>
<scope_declaration>
  <within_pm_micro_tasks>true</within_pm_micro_tasks>
</scope_declaration>
""",
        encoding="utf-8",
    )
    return report


def _write_qa_report(directory: str, *, verdict: str = "PASS", score: int = 120) -> Path:
    report = Path(directory) / "qa_report.xml"
    report.write_text(
        f"""<qa_report>
  <verdict>{verdict}</verdict>
  <numeric_score>{score}</numeric_score>
  <micro_task_boundary>verified</micro_task_boundary>
</qa_report>
""",
        encoding="utf-8",
    )
    return report


def _install_completed_agent_run(state: dict, phase: str, output_file: Path, root: Path) -> str:
    run_id = f"{phase}-test-run"
    receipt_dir = root / "agent-receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_file.resolve()
    started = pipeline._now()
    completed = pipeline._now()
    receipt = {
        "schema_version": 1,
        "receipt_type": "agent-run-receipt-v1",
        "pipeline_id": state["pipeline_id"],
        "phase": phase,
        "agent_id": pipeline.PHASE_AGENT_IDS[phase],
        "run_id": run_id,
        "status": "COMPLETED",
        "started_at": started,
        "completed_at": completed,
        "output_file": str(output_file),
        "output_sha256": pipeline._sha256_file(output_file),
        "evidence_files": [],
        "commit_sha": "a" * 40,
    }
    receipt_path = receipt_dir / f"{run_id}.json"
    pipeline._write_json(receipt_path, receipt)
    state.setdefault("agent_runs", {})[run_id] = {
        "schema_version": 1,
        "run_id": run_id,
        "pipeline_id": state["pipeline_id"],
        "phase": phase,
        "agent_id": pipeline.PHASE_AGENT_IDS[phase],
        "status": "COMPLETED",
        "started_at": started,
        "completed_at": completed,
        "token_hash": "redacted",
        "output_file": str(output_file),
        "output_sha256": pipeline._sha256_file(output_file),
        "evidence_files": [],
        "receipt_path": str(receipt_path.resolve()),
        "receipt_sha256": pipeline._sha256_file(receipt_path),
        "used_by_phase": None,
        "used_at": None,
        "commit_sha": "a" * 40,
    }
    return run_id


class ThreeGatePipelineTests(unittest.TestCase):
    def test_help_description_uses_dash_not_option_like_phase(self) -> None:
        parser = pipeline.build_parser()
        self.assertIn("Enforcer — Phase", parser.description or "")
        self.assertNotIn("Enforcer -Phase", parser.description or "")

    def test_contract_actions_before_init_are_user_friendly(self) -> None:
        pid = f"TMP-NO-CONTRACT-{uuid.uuid4().hex[:10]}"
        cases = [
            ["add-module", "--id", "M1", "--name", "Module"],
            ["add-question", "--severity", "P0", "--question", "Question?"],
            ["answer", "--id", "Q001", "--answer", "Answer"],
            ["add-test", "--id", "T1", "--module", "M1", "--test-type", "json_exact_match", "--points", "1"],
            ["add-oracle", "--input", "missing-input.txt", "--expected", "missing-expected.txt"],
            ["audit"],
            ["ready"],
            ["freeze"],
            ["show"],
        ]
        root = Path(__file__).resolve().parent
        for args in cases:
            result = subprocess.run(
                [sys.executable, "pipeline.py", "contract", *args, "--pipeline-id", pid],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            combined = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn("[CONTRACT NOT INITIALIZED]", combined, args)
            self.assertIn("contract init", combined, args)
            self.assertNotIn("Traceback", combined, args)

    def test_global_exception_wrapper_hides_expected_traceback(self) -> None:
        pid = f"TMP-GLOBAL-WRAP-{uuid.uuid4().hex[:10]}"
        root = Path(__file__).resolve().parent
        result = subprocess.run(
            [sys.executable, "pipeline.py", "acceptance", "run", "--pipeline-id", pid],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        combined = result.stdout + result.stderr

        self.assertEqual(result.returncode, 1)
        self.assertIn("[PIPELINE ERROR]", combined)
        self.assertIn("missing JSON file", combined)
        self.assertNotIn("Traceback", combined)

    def test_atomic_path_normalization_preserves_dotfiles(self) -> None:
        self.assertEqual(pipeline._normalize_rel_path("./.env.example"), ".env.example")

    def test_technical_gate_strict_fails_without_python_evidence(self) -> None:
        state = pipeline._new_state("TMP-TECH", "FEAT", "sample")
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(pipeline, "BASE_DIR", Path(tmp)):
                with mock.patch.object(pipeline, "_dev_evidence_files", return_value=[]):
                    result = pipeline._run_technical_gate(state, strict_tools=True, timeout=5)

        self.assertEqual(result["status"], "FAIL")
        py_compile_check = next(item for item in result["checks"] if item["name"] == "py_compile")
        self.assertEqual(py_compile_check["status"], "FAIL")
        self.assertIn("command", py_compile_check)

    def test_technical_gate_defaults_to_strict_missing_tool_fails(self) -> None:
        state = pipeline._new_state("TMP-TECH-MISSING", "FEAT", "sample")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "main.py"
            target.write_text("def run() -> None:\n    return None\n", encoding="utf-8")
            with mock.patch.object(pipeline, "BASE_DIR", root):
                with mock.patch.object(pipeline, "_dev_evidence_files", return_value=[target]):
                    with mock.patch.object(pipeline.importlib.util, "find_spec", return_value=None):
                        result = pipeline._run_technical_gate(state, timeout=5)

        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["strict_tools"])
        ruff_check = next(item for item in result["checks"] if item["name"] == "ruff")
        self.assertEqual(ruff_check["status"], "FAIL")

    def test_technical_gate_strict_allows_non_python_evidence(self) -> None:
        state = pipeline._new_state("TMP-TECH-DOCS", "IMP", "docs task")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "README.md"
            doc.write_text("# Done\n", encoding="utf-8")
            with mock.patch.object(pipeline, "BASE_DIR", root):
                with mock.patch.object(pipeline, "_dev_evidence_files", return_value=[doc]):
                    with mock.patch.object(pipeline.importlib.util, "find_spec", return_value=None):
                        result = pipeline._run_technical_gate(state, timeout=5)

        self.assertEqual(result["status"], "PASS")
        py_compile_check = next(item for item in result["checks"] if item["name"] == "py_compile")
        self.assertEqual(py_compile_check["status"], "SKIP")
        self.assertIn("README.md", result["evidence_files"][0])

    def test_relaxed_technical_gate_cannot_satisfy_complete(self) -> None:
        state = pipeline._new_state("TMP-TECH-RELAXED", "FEAT", "sample")
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        state["pipeline_id"] = "TMP-TECH-RELAXED"
        with tempfile.TemporaryDirectory() as tmp:
            paths = {"technical_result": Path(tmp) / "technical_result.json"}
            args = argparse.Namespace(gates_action="technical", strict_tools=False, relaxed_tools=True, timeout=5)
            with mock.patch.object(pipeline, "_require_state", return_value=state):
                with mock.patch.object(pipeline, "_contract_paths", return_value=paths):
                    with mock.patch.object(
                        pipeline,
                        "_run_technical_gate",
                        return_value={
                            "schema_version": 1,
                            "pipeline_id": "TMP-TECH-RELAXED",
                            "status": "PASS",
                            "strict_tools": False,
                            "checks": [],
                        },
                    ):
                        with mock.patch.object(pipeline, "_save"):
                            with self.assertRaises(SystemExit) as ctx:
                                pipeline.cmd_gates(args)

            result = pipeline._load_json_file(paths["technical_result"])

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["complete_eligible"])
        self.assertEqual(state["external_gates"]["technical"]["status"], "FAIL")

    def test_advisory_resolve_rejects_unknown_finding_id(self) -> None:
        state = pipeline._new_state("TMP-ADV-UNKNOWN", "FEAT", "sample")
        state["pipeline_id"] = "TMP-ADV-UNKNOWN"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "advisory_root": root / "advisory",
                "advisory_resolutions": root / "advisory" / "resolutions.json",
            }
            paths["advisory_root"].mkdir(parents=True)
            pipeline._write_json(
                paths["advisory_root"] / "gpt-code_review.json",
                {
                    "schema_version": 1,
                    "pipeline_id": "TMP-ADV-UNKNOWN",
                    "status": "COMPLETED",
                    "generated_at": "2026-05-09T00:00:00Z",
                    "findings": [{
                        "id": "C1",
                        "level": "CRITICAL",
                        "file": "pipeline.py",
                        "line": 1,
                        "message": "real finding",
                        "recommendation": "fix it",
                    }],
                },
            )
            args = argparse.Namespace(
                advisory_action="resolve",
                id="FAKE-001",
                resolution="waived",
                notes="not real",
                review_file=None,
            )
            with mock.patch.object(pipeline, "_require_state", return_value=state):
                with mock.patch.object(pipeline, "_contract_paths", return_value=paths):
                    with self.assertRaises(SystemExit) as ctx:
                        pipeline.cmd_advisory(args)

        self.assertEqual(ctx.exception.code, 1)

    def test_advisory_resolution_is_bound_to_current_finding_fingerprint(self) -> None:
        state = pipeline._new_state("TMP-ADV-FP", "FEAT", "sample")
        state["pipeline_id"] = "TMP-ADV-FP"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "advisory_root": root / "advisory",
                "advisory_resolutions": root / "advisory" / "resolutions.json",
            }
            review_path = paths["advisory_root"] / "gpt-code_review.json"
            paths["advisory_root"].mkdir(parents=True)
            pipeline._write_json(
                review_path,
                {
                    "schema_version": 1,
                    "pipeline_id": "TMP-ADV-FP",
                    "status": "COMPLETED",
                    "generated_at": "2026-05-09T00:00:00Z",
                    "findings": [{
                        "id": "C1",
                        "level": "CRITICAL",
                        "file": "pipeline.py",
                        "line": 1,
                        "message": "real finding",
                        "recommendation": "fix it",
                    }],
                },
            )
            args = argparse.Namespace(
                advisory_action="resolve",
                id="C1",
                resolution="fixed",
                notes="fixed",
                review_file=None,
            )
            with mock.patch.object(pipeline, "_require_state", return_value=state):
                with mock.patch.object(pipeline, "_contract_paths", return_value=paths):
                    pipeline.cmd_advisory(args)
                    self.assertEqual(pipeline._unresolved_critical_advisories("TMP-ADV-FP"), [])
                    pipeline._write_json(
                        review_path,
                        {
                            "schema_version": 1,
                            "pipeline_id": "TMP-ADV-FP",
                            "status": "COMPLETED",
                            "generated_at": "2026-05-09T00:01:00Z",
                            "findings": [{
                                "id": "C1",
                                "level": "CRITICAL",
                                "file": "pipeline.py",
                                "line": 1,
                                "message": "new finding with reused id",
                                "recommendation": "fix the new issue",
                            }],
                        },
                    )
                    unresolved = pipeline._unresolved_critical_advisories("TMP-ADV-FP")

        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["message"], "new finding with reused id")

    def test_advisory_status_summary_shows_not_run_and_missing_api_key(self) -> None:
        pid = "TMP-ADV-STATUS"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "advisory_root": root / "advisory",
                "advisory_resolutions": root / "advisory" / "resolutions.json",
            }
            with mock.patch.object(pipeline, "_contract_paths", return_value=paths), \
                 mock.patch.dict("os.environ", {}, clear=True), \
                 mock.patch.object(pipeline, "_openai_api_key", return_value=(None, "missing")):
                summary = pipeline._advisory_status_summary(pid)
                self.assertFalse(summary["api_key_present"])
                self.assertEqual(summary["api_key_source"], "missing")
                self.assertFalse(summary["enabled"])
                self.assertEqual(summary["review_count"], 0)
                self.assertEqual(summary["api_call_count"], 0)

                paths["advisory_root"].mkdir(parents=True)
                (paths["advisory_root"] / "gpt-code_review.json").write_text(
                    json.dumps({
                        "status": "SKIPPED",
                        "reason": "OPENAI_API_KEY is not set",
                        "api_called": False,
                        "findings": [],
                    }),
                    encoding="utf-8",
                )
                summary = pipeline._advisory_status_summary(pid)
                self.assertEqual(summary["review_count"], 1)
                self.assertEqual(summary["api_call_count"], 0)
                self.assertEqual(summary["status_counts"]["SKIPPED"], 1)

    def test_openai_advisory_without_api_key_is_explicitly_not_called(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(pipeline, "_openai_api_key", return_value=(None, "missing")):
            result = pipeline._call_openai_advisory("review", model="gpt-5.5", timeout=1)

        self.assertEqual(result["status"], "SKIPPED")
        self.assertFalse(result["api_called"])
        self.assertIn("OPENAI_API_KEY", result["reason"])

    def test_advisory_model_is_fixed_to_gpt_55(self) -> None:
        state = pipeline._new_state("TMP-ADV-MODEL", "FEAT", "sample")
        state["pipeline_id"] = "TMP-ADV-MODEL"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "main.py"
            target.write_text("print('review me')\n", encoding="utf-8")
            paths = {
                "advisory_root": root / "advisory",
                "advisory_resolutions": root / "advisory" / "resolutions.json",
            }
            args = argparse.Namespace(
                advisory_action="gpt-code",
                files=str(target),
                model=None,
                max_chars=10000,
                timeout=1,
                require=False,
            )
            with mock.patch.dict("os.environ", {"OPENAI_REVIEW_MODEL": "gpt-5.2"}, clear=False):
                with mock.patch.object(pipeline, "_require_state", return_value=state):
                    with mock.patch.object(pipeline, "_contract_paths", return_value=paths):
                        with mock.patch.object(
                            pipeline,
                            "_call_openai_advisory",
                            return_value={"status": "SKIPPED", "reason": "test", "api_called": False, "findings": []},
                        ) as call:
                            with self.assertRaises(SystemExit) as ctx:
                                pipeline.cmd_advisory(args)

            self.assertEqual(ctx.exception.code, 0)

            call.assert_called_once()
            self.assertEqual(call.call_args.kwargs["model"], "gpt-5.5")
            review = pipeline._load_json_file(paths["advisory_root"] / "gpt-code_review.json")
            self.assertEqual(review["model"], "gpt-5.5")

    def test_advisory_rejects_non_fixed_model_argument(self) -> None:
        state = pipeline._new_state("TMP-ADV-MODEL-ARG", "FEAT", "sample")
        args = argparse.Namespace(
            advisory_action="gpt-code",
            files=None,
            model="gpt-5.2",
            max_chars=10000,
            timeout=1,
            require=False,
        )
        with mock.patch.object(pipeline, "_require_state", return_value=state):
            with self.assertRaises(SystemExit) as ctx:
                pipeline.cmd_advisory(args)

        self.assertEqual(ctx.exception.code, 1)

    def test_auto_advisory_records_fixed_model_and_skipped_without_key(self) -> None:
        state = pipeline._new_state("TMP-AUTO-ADV", "FEAT", "sample")
        state["pipeline_id"] = "TMP-AUTO-ADV"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "advisory_root": root / "advisory",
                "advisory_resolutions": root / "advisory" / "resolutions.json",
            }
            target = root / "main.py"
            target.write_text("print('review me')\n", encoding="utf-8")
            with mock.patch.object(pipeline, "_contract_paths", return_value=paths):
                with mock.patch.dict("os.environ", {}, clear=True), \
                     mock.patch.object(pipeline, "_openai_api_key", return_value=(None, "missing")):
                    result = pipeline._auto_run_openai_advisory(
                        state,
                        kind="gpt-code",
                        files=[target],
                    )

            review = pipeline._load_json_file(paths["advisory_root"] / "gpt-code_review.json")

        self.assertEqual(result["status"], "SKIPPED")
        self.assertFalse(result["api_called"])
        self.assertEqual(review["model"], "gpt-5.5")
        self.assertTrue(review["auto_run"])

    def test_oracle_gate_requires_technical_gate_pass_first(self) -> None:
        state = pipeline._new_state("TMP-ORACLE-ORDER", "FEAT", "sample")
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        state["pipeline_id"] = "TMP-ORACLE-ORDER"
        args = argparse.Namespace(gates_action="oracle", user_confirmed=True)
        with mock.patch.object(pipeline, "_require_state", return_value=state):
            with self.assertRaises(SystemExit) as ctx:
                pipeline.cmd_gates(args)

        self.assertEqual(ctx.exception.code, 1)

    def test_phase_attestation_blocks_next_phase_until_github_pass(self) -> None:
        state = pipeline._new_state("TMP-PHASE-BLOCK", "IMP", "sample")
        state["current_phase"] = "dev"
        state["phases"]["pm"]["status"] = "DONE"
        state["phase_attestations"] = pipeline._new_phase_attestations(enabled=True)

        ok, reason = pipeline.check_gate(state, "dev")
        self.assertFalse(ok)
        self.assertIn("pm GitHub phase attestation must be PASS", reason)

        state["phase_attestations"]["phases"]["pm"]["status"] = "PASS"
        ok, reason = pipeline.check_gate(state, "dev")
        self.assertTrue(ok, reason)

    def test_prepare_phase_attestation_request_copies_report_evidence(self) -> None:
        state = pipeline._new_state("TMP-PHASE-PREP", "IMP", "sample")
        state["phase_attestations"] = pipeline._new_phase_attestations(enabled=True)
        state["phases"]["pm"]["status"] = "DONE"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "step_plan.xml"
            report.write_text("<step_plan></step_plan>", encoding="utf-8")
            request_path = root / ".pipeline" / "phase_attestation_request.json"
            evidence_dir = root / ".pipeline" / "phase_evidence"
            state["phases"]["pm"]["report_file"] = str(report)
            run_id = _install_completed_agent_run(state, "pm", report, root)
            state["phases"]["pm"]["agent_run_id"] = run_id
            with mock.patch.object(pipeline, "PHASE_ATTESTATION_REQUEST", request_path), \
                 mock.patch.object(pipeline, "PHASE_ATTESTATION_EVIDENCE_DIR", evidence_dir), \
                 mock.patch.object(pipeline, "_git_rev_parse", return_value="a" * 40):
                request = pipeline._prepare_phase_attestation_request(state, "pm")

            self.assertEqual(request["phase"], "pm")
            self.assertEqual(request["pipeline_id"], "TMP-PHASE-PREP")
            self.assertEqual(request["agent_run"]["agent_id"], "pm-agent")
            self.assertEqual(request["agent_run"]["used_by_phase"], "pm")
            self.assertTrue(request_path.exists())
            labels = {item["label"] for item in request["copied_evidence"]}
            self.assertIn("agent_receipt", labels)
            self.assertIn("report", labels)
            receipt_copy = next(item for item in request["copied_evidence"] if item["label"] == "agent_receipt")
            self.assertEqual(request["agent_run"]["receipt_path"], receipt_copy["path"])
            self.assertEqual(request["agent_run"]["receipt_sha256"], receipt_copy["sha256"])
            for copied in request["copied_evidence"]:
                copied_path = Path(copied["path"])
                if not copied_path.is_absolute():
                    copied_path = pipeline.BASE_DIR / copied_path
                self.assertTrue(copied_path.exists())

    def test_agent_run_receipt_roundtrip_validates_token_and_output(self) -> None:
        state = pipeline._new_state("TMP-AGENT-RUN", "IMP", "sample")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "step_plan.xml"
            output.write_text("<step_plan></step_plan>", encoding="utf-8")
            with mock.patch.object(pipeline, "AGENT_RECEIPT_DIR", root / "receipts"), \
                 mock.patch.object(pipeline, "_git_rev_parse", return_value="a" * 40):
                run, token = pipeline._agent_run_start(state, "pm", "pm-agent")
                completed = pipeline._agent_run_finish(
                    state,
                    run_id=run["run_id"],
                    token=token,
                    output_file=str(output),
                    evidence=None,
                    notes="done",
                )
                expected_output_hash = pipeline._sha256_file(output)
                receipt_exists = Path(completed["receipt_path"]).exists()

        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(completed["agent_id"], "pm-agent")
        self.assertEqual(completed["output_sha256"], expected_output_hash)
        self.assertTrue(receipt_exists)

    def test_phase_submission_requires_agent_run_when_phase_attestations_enabled(self) -> None:
        state = pipeline._new_state("TMP-PM-AGENT-REQ", "IMP", "sample")
        state["phase_attestations"] = pipeline._new_phase_attestations(enabled=True)
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "step_plan.xml"
            report.write_text(
                """
<decomposition_audit>
  <total_functions_identified>1</total_functions_identified>
  <micro_task_count>1</micro_task_count>
  <grep_executions>1</grep_executions>
  <split_decision>single function</split_decision>
  <audit_result>SINGLE_TASK_OK</audit_result>
</decomposition_audit>
<step_plan>
  <pipeline_id>TMP-PM-AGENT-REQ</pipeline_id>
  <micro_tasks>
    <micro_task id="MT-1">
      <affected_function>main.run</affected_function>
      <target_files><file>main.py</file></target_files>
      <grep_evidence>
        <pattern>def run</pattern>
        <match_count>1</match_count>
        <executed>true</executed>
      </grep_evidence>
      <change_summary>Update run behavior</change_summary>
    </micro_task>
  </micro_tasks>
</step_plan>
""",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                branch=None,
                phase="pm",
                decomp=True,
                clarification=True,
                roadmap=True,
                judgment_confirmed=False,
                report_file=str(report),
                files=None,
                agent_run_id=None,
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_done(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["pm"]["status"], "PENDING")

    def test_phase_submission_accepts_matching_agent_run_receipt(self) -> None:
        state = pipeline._new_state("TMP-PM-AGENT-OK", "IMP", "sample")
        state["phase_attestations"] = pipeline._new_phase_attestations(enabled=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "step_plan.xml"
            report.write_text(
                """
<decomposition_audit>
  <total_functions_identified>1</total_functions_identified>
  <micro_task_count>1</micro_task_count>
  <grep_executions>1</grep_executions>
  <split_decision>single function</split_decision>
  <audit_result>SINGLE_TASK_OK</audit_result>
</decomposition_audit>
<step_plan>
  <pipeline_id>TMP-PM-AGENT-OK</pipeline_id>
  <micro_tasks>
    <micro_task id="MT-1">
      <affected_function>main.run</affected_function>
      <target_files><file>main.py</file></target_files>
      <grep_evidence>
        <pattern>def run</pattern>
        <match_count>1</match_count>
        <executed>true</executed>
      </grep_evidence>
      <change_summary>Update run behavior</change_summary>
    </micro_task>
  </micro_tasks>
</step_plan>
""",
                encoding="utf-8",
            )
            run_id = _install_completed_agent_run(state, "pm", report, root)
            args = argparse.Namespace(
                branch=None,
                phase="pm",
                decomp=True,
                clarification=True,
                roadmap=True,
                judgment_confirmed=False,
                report_file=str(report),
                files=None,
                agent_run_id=run_id,
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state), \
                 mock.patch.object(pipeline, "_save_state_for"), \
                 mock.patch.object(pipeline, "_record_snapshot"):
                pipeline.cmd_done(args)

        self.assertEqual(state["phases"]["pm"]["status"], "DONE")
        self.assertEqual(state["phases"]["pm"]["agent_run_id"], run_id)
        self.assertEqual(state["agent_runs"][run_id]["used_by_phase"], "pm")

    def test_gates_phase_ci_records_phase_attestation(self) -> None:
        state = pipeline._new_state("TMP-PHASE-CI", "IMP", "sample")
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        state["phase_attestations"] = pipeline._new_phase_attestations(enabled=True)
        verification = {
            "schema_version": 1,
            "status": "PASS",
            "repository": "hojiyong2-commits/Pipeline",
            "run_id": "321",
            "commit_sha": "a" * 40,
            "pipeline_id": "TMP-PHASE-CI",
            "phase": "pm",
            "attestation": {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {"phase_ci_root": root / "gates" / "phase_ci"}
            args = argparse.Namespace(
                gates_action="phase-ci",
                phase="pm",
                repo="hojiyong2-commits/Pipeline",
                run_id="321",
                commit="a" * 40,
                workflow="CI",
                artifact="pipeline-phase-attestation",
                token_env="GITHUB_TOKEN",
            )
            with mock.patch.object(pipeline, "_require_state", return_value=state), \
                 mock.patch.object(pipeline, "_contract_paths", return_value=paths), \
                 mock.patch.object(pipeline, "_verify_github_phase_attestation_run", return_value=verification), \
                 mock.patch.object(pipeline, "_save"):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_gates(args)

        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(state["phase_attestations"]["phases"]["pm"]["status"], "PASS")
        self.assertEqual(state["phase_attestations"]["phases"]["pm"]["run_id"], "321")

    def test_phase_ci_validation_rejects_wrong_agent_receipt(self) -> None:
        attestation = {
            "schema_version": 1,
            "attestation_type": "pipeline-phase-v1",
            "repository": "hojiyong2-commits/Pipeline",
            "run_id": "321",
            "pipeline_id": "TMP-PHASE-CI",
            "phase": "dev",
            "head_sha": "a" * 40,
            "validation": {"status": "PASS", "blockers": []},
            "request": {
                "agent_run": {
                    "run_id": "pm-test-run",
                    "phase": "dev",
                    "agent_id": "pm-agent",
                    "status": "COMPLETED",
                    "used_by_phase": "dev",
                }
            },
        }

        result = pipeline._validate_github_phase_attestation(
            attestation,
            repo="hojiyong2-commits/Pipeline",
            run_id="321",
            commit_sha="a" * 40,
            pipeline_id="TMP-PHASE-CI",
            phase="dev",
        )

        self.assertEqual(result["status"], "FAIL")
        self.assertIn("request.agent_run agent_id must be dev-agent", result["blockers"])

    def test_accept_gate_requires_github_ci_pass_before_deploy(self) -> None:
        state = pipeline._new_state("TMP-ACCEPT-BLOCK", "FEAT", "sample")
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        state["external_gates"]["technical"]["status"] = "PASS"
        state["external_gates"]["oracle"]["status"] = "PASS"

        args = argparse.Namespace(
            gates_action="accept",
            result="ACCEPT",
            evidence="result.txt",
            notes="looks good",
            user_confirmed=True,
        )
        with mock.patch.object(pipeline, "_require_state", return_value=state), \
             mock.patch.object(pipeline, "_deploy_accepted_outputs") as deploy:
            with self.assertRaises(SystemExit) as ctx:
                pipeline.cmd_gates(args)

        self.assertEqual(ctx.exception.code, 1)
        deploy.assert_not_called()
        self.assertEqual(state["external_gates"]["acceptance"]["status"], "PENDING")

    def test_accept_gate_deploys_real_artifact_after_ci_pass(self) -> None:
        state = pipeline._new_state("TMP-ACCEPT-DEPLOY", "FEAT", "sample")
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        for gate_name in ("technical", "oracle", "github_ci"):
            state["external_gates"][gate_name]["status"] = "PASS"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "result.txt"
            artifact.write_text("accepted output", encoding="utf-8")
            deploy_root = root / "deploy-root"
            paths = {"user_validation": root / "user_validation.json"}
            args = argparse.Namespace(
                gates_action="accept",
                result="ACCEPT",
                evidence=str(artifact),
                notes="approved by user",
                user_confirmed=True,
            )

            with mock.patch.object(pipeline, "_require_state", return_value=state), \
                 mock.patch.object(pipeline, "_contract_paths", return_value=paths), \
                 mock.patch.object(pipeline, "_record_snapshot"), \
                 mock.patch.object(pipeline, "_save"), \
                 mock.patch.dict(pipeline.os.environ, {"PIPELINE_DEPLOY_ROOT": str(deploy_root)}):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_gates(args)

            deployment = state.get("deployment")
            deployed_file = deploy_root / "TMP-ACCEPT-DEPLOY" / artifact.name
            manifest = deploy_root / "TMP-ACCEPT-DEPLOY" / "deployment_manifest.json"

            self.assertEqual(ctx.exception.code, 0)
            self.assertIsInstance(deployment, dict)
            self.assertEqual(state["external_gates"]["acceptance"]["status"], "PASS")
            self.assertTrue(deployed_file.exists())
            self.assertTrue(manifest.exists())
            self.assertEqual(deployed_file.read_text(encoding="utf-8"), "accepted output")

    def test_pm_done_requires_atomic_step_plan_for_all_pipelines(self) -> None:
        state = pipeline._new_state("TMP-PM", "FEAT", "sample")
        args = argparse.Namespace(
            branch=None,
            phase="pm",
            decomp=True,
            clarification=True,
            roadmap=True,
            judgment_confirmed=False,
            report_file=None,
            files=None,
        )

        with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
            with self.assertRaises(SystemExit) as ctx:
                pipeline.cmd_done(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["pm"]["status"], "PENDING")

    def test_pm_done_records_atomic_step_plan_without_three_gate(self) -> None:
        state = pipeline._new_state("TMP-PM-OK", "FEAT", "sample")
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "step_plan.xml"
            report.write_text(
                """
<decomposition_audit>
  <total_functions_identified>1</total_functions_identified>
  <micro_task_count>1</micro_task_count>
  <grep_executions>1</grep_executions>
  <split_decision>single function</split_decision>
  <audit_result>SINGLE_TASK_OK</audit_result>
</decomposition_audit>
<step_plan>
  <pipeline_id>TMP-PM-OK</pipeline_id>
  <anti_gaming_read>true</anti_gaming_read>
  <micro_tasks>
    <micro_task id="MT-1">
      <affected_function>main.run</affected_function>
      <target_files><file>main.py</file></target_files>
      <grep_evidence>
        <pattern>def run</pattern>
        <match_count>1</match_count>
        <executed>true</executed>
      </grep_evidence>
      <change_summary>Update run behavior</change_summary>
    </micro_task>
  </micro_tasks>
</step_plan>
""",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                branch=None,
                phase="pm",
                decomp=True,
                clarification=True,
                roadmap=True,
                judgment_confirmed=False,
                report_file=str(report),
                files=None,
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with mock.patch.object(pipeline, "_save_state_for"):
                    with mock.patch.object(pipeline, "_record_snapshot"):
                        pipeline.cmd_done(args)

        self.assertEqual(state["phases"]["pm"]["status"], "DONE")
        self.assertEqual(state["atomic_plan"]["micro_task_count"], 1)
        self.assertEqual(state["atomic_plan"]["micro_tasks"][0]["id"], "MT-1")
        self.assertIn("project_snapshot", state["atomic_plan"])

    def test_pm_done_rejects_dev_output_or_handover_in_pm_report(self) -> None:
        state = pipeline._new_state("TMP-PM-POLLUTED", "FEAT", "sample")
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "step_plan.xml"
            report.write_text(
                """
<decomposition_audit>
  <total_functions_identified>1</total_functions_identified>
  <micro_task_count>1</micro_task_count>
  <grep_executions>1</grep_executions>
  <split_decision>single function</split_decision>
  <audit_result>SINGLE_TASK_OK</audit_result>
</decomposition_audit>
<step_plan>
  <pipeline_id>TMP-PM-POLLUTED</pipeline_id>
  <micro_tasks>
    <micro_task id="MT-1">
      <affected_function>main.run</affected_function>
      <target_files><file>main.py</file></target_files>
      <grep_evidence>
        <pattern>def run</pattern>
        <match_count>1</match_count>
        <executed>true</executed>
      </grep_evidence>
      <change_summary>Plan only</change_summary>
    </micro_task>
  </micro_tasks>
</step_plan>
<dev_output>PM must not implement</dev_output>
<handover><from>dev-agent</from></handover>
""",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                branch=None,
                phase="pm",
                decomp=True,
                clarification=True,
                roadmap=True,
                judgment_confirmed=False,
                report_file=str(report),
                files=None,
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_done(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["pm"]["status"], "PENDING")
        self.assertNotIn("atomic_plan", state)

    def test_dev_scope_manifest_blocks_file_outside_pm_micro_task(self) -> None:
        state = pipeline._new_state("TMP-DEV", "FEAT", "sample")
        state["current_phase"] = "dev"
        state["phases"]["pm"]["status"] = "DONE"
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        state["atomic_plan"] = {
            "project_snapshot": {},
            "micro_tasks": [{
                "id": "MT-1",
                "affected_function": "main.run",
                "target_files": ["main.py"],
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
            manifest = root / "scope_manifest.json"
            manifest.write_text(
                """{
  "pipeline_id": "TMP-DEV",
  "micro_tasks": [
    {
      "id": "MT-1",
      "files": ["main.py", "extra.py"],
      "affected_functions": ["main.run"]
    }
  ]
}
""",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                branch=None,
                phase="dev",
                files="main.py",
                report_file=str(_write_dev_handover_report(tmp)),
                scope_declared=True,
                scope_manifest=str(manifest),
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_done(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["dev"]["status"], "PENDING")

    def test_dev_done_records_atomic_scope_manifest(self) -> None:
        state = pipeline._new_state("TMP-DEV-OK", "FEAT", "sample")
        state["current_phase"] = "dev"
        state["phases"]["pm"]["status"] = "DONE"
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "main.py"
            target.write_text("print('ok')\n", encoding="utf-8")
            state["atomic_plan"] = {
                "project_snapshot": {},
                "micro_tasks": [{
                    "id": "MT-1",
                    "affected_function": "main.run",
                    "target_files": [str(target)],
                }]
            }
            manifest = root / "scope_manifest.json"
            target_json = str(target).replace("\\", "\\\\")
            manifest.write_text(
                f"""{{
  "pipeline_id": "TMP-DEV-OK",
  "micro_tasks": [
    {{
      "id": "MT-1",
      "files": ["{target_json}"],
      "affected_functions": ["main.run"]
    }}
  ]
}}
""",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                branch=None,
                phase="dev",
                files=str(target),
                report_file=str(_write_dev_handover_report(tmp)),
                scope_declared=True,
                scope_manifest=str(manifest),
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with mock.patch.object(pipeline, "_save_state_for"):
                    with mock.patch.object(pipeline, "_record_snapshot"):
                        with mock.patch.object(
                            pipeline,
                            "_atomic_changed_files",
                            return_value={"added": [], "modified": [], "deleted": [], "changed": []},
                        ):
                            pipeline.cmd_done(args)

        self.assertEqual(state["phases"]["dev"]["status"], "DONE")
        self.assertEqual(state["atomic_scope"]["micro_task_ids"], ["MT-1"])

    def test_dev_scope_manifest_blocks_actual_file_change_outside_pm_plan(self) -> None:
        state = pipeline._new_state("TMP-DEV-ACTUAL", "FEAT", "sample")
        state["current_phase"] = "dev"
        state["phases"]["pm"]["status"] = "DONE"
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        state["atomic_plan"] = {
            "project_snapshot": {},
            "micro_tasks": [{
                "id": "MT-1",
                "affected_function": "main.run",
                "target_files": ["main.py"],
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "scope_manifest.json"
            manifest.write_text(
                """{
  "pipeline_id": "TMP-DEV-ACTUAL",
  "micro_tasks": [
    {"id": "MT-1", "files": ["main.py"], "affected_functions": ["main.run"]}
  ]
}
""",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                branch=None,
                phase="dev",
                files="main.py",
                report_file=str(_write_dev_handover_report(tmp)),
                scope_declared=True,
                scope_manifest=str(manifest),
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with mock.patch.object(
                    pipeline,
                    "_atomic_changed_files",
                    return_value={"added": ["extra.py"], "modified": [], "deleted": [], "changed": ["extra.py"]},
                ):
                    with self.assertRaises(SystemExit) as ctx:
                        pipeline.cmd_done(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["dev"]["status"], "PENDING")

    def test_dev_scope_manifest_blocks_actual_change_missing_from_manifest(self) -> None:
        state = pipeline._new_state("TMP-DEV-UNDECLARED", "FEAT", "sample")
        state["current_phase"] = "dev"
        state["phases"]["pm"]["status"] = "DONE"
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        state["atomic_plan"] = {
            "project_snapshot": {},
            "micro_tasks": [{
                "id": "MT-1",
                "affected_function": "main.run",
                "target_files": ["main.py", "helper.py"],
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "scope_manifest.json"
            manifest.write_text(
                """{
  "pipeline_id": "TMP-DEV-UNDECLARED",
  "micro_tasks": [
    {"id": "MT-1", "files": ["main.py"], "affected_functions": ["main.run"]}
  ]
}
""",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                branch=None,
                phase="dev",
                files="main.py",
                report_file=str(_write_dev_handover_report(tmp)),
                scope_declared=True,
                scope_manifest=str(manifest),
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with mock.patch.object(
                    pipeline,
                    "_atomic_changed_files",
                    return_value={"added": ["helper.py"], "modified": [], "deleted": [], "changed": ["helper.py"]},
                ):
                    with self.assertRaises(SystemExit) as ctx:
                        pipeline.cmd_done(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["dev"]["status"], "PENDING")

    def test_dev_scope_manifest_requires_micro_task_id(self) -> None:
        state = pipeline._new_state("TMP-DEV-ID", "FEAT", "sample")
        state["current_phase"] = "dev"
        state["phases"]["pm"]["status"] = "DONE"
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        state["atomic_plan"] = {
            "project_snapshot": {},
            "micro_tasks": [{
                "id": "MT-1",
                "affected_function": "main.run",
                "target_files": ["main.py"],
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "scope_manifest.json"
            manifest.write_text(
                """{
  "pipeline_id": "TMP-DEV-ID",
  "micro_tasks": [
    {"files": ["main.py"], "affected_functions": ["main.run"]}
  ]
}
""",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                branch=None,
                phase="dev",
                files="main.py",
                report_file=str(_write_dev_handover_report(tmp)),
                scope_declared=True,
                scope_manifest=str(manifest),
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_done(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["dev"]["status"], "PENDING")

    def test_dev_done_requires_scope_manifest_without_three_gate(self) -> None:
        state = pipeline._new_state("TMP-DEV-SCOPE-ALL", "FEAT", "sample")
        state["current_phase"] = "dev"
        state["phases"]["pm"]["status"] = "DONE"
        state["atomic_plan"] = {
            "project_snapshot": {},
            "micro_tasks": [{
                "id": "MT-1",
                "affected_function": "main.run",
                "target_files": ["main.py"],
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                branch=None,
                phase="dev",
                files="main.py",
                report_file=str(_write_dev_handover_report(tmp)),
                scope_declared=True,
                scope_manifest=None,
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_done(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["dev"]["status"], "PENDING")

    def test_qa_requires_report_file_with_micro_task_boundary(self) -> None:
        state = pipeline._new_state("TMP-QA-REPORT", "FEAT", "sample")
        state["current_phase"] = "qa"
        state["phases"]["pm"]["status"] = "DONE"
        state["phases"]["dev"]["status"] = "DONE"
        args = argparse.Namespace(
            result="PASS",
            numeric_score="120",
            failure_sig=None,
            agent_id=None,
            report_file=None,
            branch=None,
        )

        with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
            with self.assertRaises(SystemExit) as ctx:
                pipeline.cmd_qa(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["qa"]["status"], "PENDING")

    def test_qa_records_valid_report_file(self) -> None:
        state = pipeline._new_state("TMP-QA-REPORT-OK", "FEAT", "sample")
        state["current_phase"] = "qa"
        state["phases"]["pm"]["status"] = "DONE"
        state["phases"]["dev"]["status"] = "DONE"
        with tempfile.TemporaryDirectory() as tmp:
            report = _write_qa_report(tmp, verdict="PASS", score=120)
            args = argparse.Namespace(
                result="PASS",
                numeric_score="120",
                failure_sig=None,
                agent_id="qa-agent",
                report_file=str(report),
                branch=None,
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with mock.patch.object(pipeline, "_save_state_for"):
                    with mock.patch.object(pipeline, "_record_snapshot"):
                        pipeline.cmd_qa(args)

        self.assertEqual(state["phases"]["qa"]["status"], "PASS")
        self.assertEqual(state["qa_report_validation"]["numeric_score"], 120)

    def test_architect_rejects_fail_mode_report_after_harness_pass(self) -> None:
        state = pipeline._new_state("TMP-ARCH-RCA", "FEAT", "sample")
        state["current_phase"] = "architect"
        for phase in ("pm", "dev", "qa", "sec", "build"):
            state["phases"][phase]["status"] = "DONE" if phase in {"pm", "dev", "build"} else "PASS"
        state["phases"]["harness"]["status"] = "PASS"
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "architect_report.xml"
            report.write_text(
                """<optimization_report>
  <rca_mode>HARNESS_FAIL_ANALYSIS</rca_mode>
  <protocol_evolution_decision>
    <required>false</required>
    <reason>none</reason>
    <scope>none</scope>
    <recommended_pipeline_type>IMP</recommended_pipeline_type>
  </protocol_evolution_decision>
</optimization_report>
""",
                encoding="utf-8",
            )
            args = argparse.Namespace(report_file=str(report), branch=None)
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_architect(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["architect"]["status"], "PENDING")

    def test_contract_audit_blocks_shallow_tests_without_oracle(self) -> None:
        contract = build_initial_contract("TMP-GATE", "sample task")
        contract["modules"].append({
            "id": "M1",
            "name": "Parser",
            "inputs": [],
            "outputs": [],
            "acceptance_rules": [],
            "exceptions": [],
        })
        test_set = build_initial_test_set("TMP-GATE")
        test_set["tests"].append({
            "id": "T1",
            "module": "M1",
            "type": "file_exists_check",
            "priority": "P0",
            "case_kind": "normal",
            "points": 1,
            "given": {},
            "when": {},
            "then": {},
        })
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "oracle_manifest": root / "oracle_manifest.json",
            }
            audit = pipeline._audit_contract_bundle(contract, test_set, paths)

        self.assertEqual(audit["status"], "FAIL")
        self.assertTrue(any("then.path" in item for item in audit["blockers"]))
        self.assertTrue(any("P0 behavior test" in item for item in audit["blockers"]))
        self.assertTrue(any("oracle_manifest" in item for item in audit["blockers"]))

    def test_contract_audit_requires_normal_and_edge_oracles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual.json"
            expected = root / "expected.json"
            oracle_input = root / "oracle_input.json"
            oracle_expected = root / "oracle_expected.json"
            for path in (actual, expected, oracle_input, oracle_expected):
                path.write_text('{"value": 1}\n', encoding="utf-8")

            contract = build_initial_contract("TMP-ORACLE-KIND", "sample task")
            contract["definition_of_ready"]["min_edge_cases_total"] = 0
            contract["modules"].append({
                "id": "M1",
                "name": "Parser",
                "inputs": [],
                "outputs": [],
                "acceptance_rules": [],
                "exceptions": [],
            })
            test_set = build_initial_test_set("TMP-ORACLE-KIND")
            test_set["tests"].append({
                "id": "T1",
                "module": "M1",
                "type": "json_exact_match",
                "priority": "P0",
                "case_kind": "normal",
                "points": 1,
                "given": {"actual_file": str(actual)},
                "when": {},
                "then": {"expected_file": str(expected)},
            })
            oracle_manifest = {
                "schema_version": 1,
                "pipeline_id": "TMP-ORACLE-KIND",
                "oracles": [{
                    "name": "O1",
                    "source": "user",
                    "case_kind": "normal",
                    "input_path": str(oracle_input),
                    "expected_path": str(oracle_expected),
                    "input_sha256": pipeline._sha256_file(oracle_input),
                    "expected_sha256": pipeline._sha256_file(oracle_expected),
                }],
            }
            paths = {"oracle_manifest": root / "oracle_manifest.json"}
            pipeline._write_json(paths["oracle_manifest"], oracle_manifest)

            audit = pipeline._audit_contract_bundle(contract, test_set, paths)

        self.assertEqual(audit["status"], "FAIL")
        self.assertTrue(any("edge/exception/error oracle" in item for item in audit["blockers"]))

    def test_contract_audit_blocks_empty_oracle_expected_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual.json"
            expected = root / "expected.json"
            normal_input = root / "normal_input.json"
            normal_expected = root / "normal_expected.json"
            edge_input = root / "edge_input.json"
            edge_expected = root / "edge_expected.json"
            for path in (actual, expected, normal_input, normal_expected, edge_input):
                path.write_text('{"value": 1}\n', encoding="utf-8")
            edge_expected.write_text("{}\n", encoding="utf-8")

            contract = build_initial_contract("TMP-ORACLE-EMPTY", "sample task")
            contract["definition_of_ready"]["min_edge_cases_total"] = 0
            contract["modules"].append({
                "id": "M1",
                "name": "Parser",
                "inputs": [],
                "outputs": [],
                "acceptance_rules": [],
                "exceptions": [],
            })
            test_set = build_initial_test_set("TMP-ORACLE-EMPTY")
            test_set["tests"].append({
                "id": "T1",
                "module": "M1",
                "type": "json_exact_match",
                "priority": "P0",
                "case_kind": "normal",
                "points": 1,
                "given": {"actual_file": str(actual)},
                "when": {},
                "then": {"expected_file": str(expected)},
            })
            oracle_manifest = {
                "schema_version": 1,
                "pipeline_id": "TMP-ORACLE-EMPTY",
                "oracles": [
                    {
                        "name": "O1",
                        "source": "user",
                        "case_kind": "normal",
                        "input_path": str(normal_input),
                        "expected_path": str(normal_expected),
                        "input_sha256": pipeline._sha256_file(normal_input),
                        "expected_sha256": pipeline._sha256_file(normal_expected),
                    },
                    {
                        "name": "O2",
                        "source": "user",
                        "case_kind": "edge",
                        "input_path": str(edge_input),
                        "expected_path": str(edge_expected),
                        "input_sha256": pipeline._sha256_file(edge_input),
                        "expected_sha256": pipeline._sha256_file(edge_expected),
                    },
                ],
            }
            paths = {"oracle_manifest": root / "oracle_manifest.json"}
            pipeline._write_json(paths["oracle_manifest"], oracle_manifest)

            audit = pipeline._audit_contract_bundle(contract, test_set, paths)

        self.assertEqual(audit["status"], "FAIL")
        self.assertTrue(any("expected JSON is empty" in item for item in audit["blockers"]))

    def test_contract_audit_requires_user_oracle_source_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual.json"
            expected = root / "expected.json"
            oracle_input = root / "oracle_input.json"
            oracle_expected = root / "oracle_expected.json"
            for path in (actual, expected, oracle_input, oracle_expected):
                path.write_text('{"value": 1}\n', encoding="utf-8")

            contract = build_initial_contract("TMP-ORACLE-SOURCE", "sample task")
            contract["definition_of_ready"]["min_edge_cases_total"] = 0
            contract["modules"].append({
                "id": "M1",
                "name": "Parser",
                "inputs": [],
                "outputs": [],
                "acceptance_rules": [],
                "exceptions": [],
            })
            test_set = build_initial_test_set("TMP-ORACLE-SOURCE")
            test_set["tests"].append({
                "id": "T1",
                "module": "M1",
                "type": "json_exact_match",
                "priority": "P0",
                "case_kind": "normal",
                "points": 1,
                "given": {"actual_file": str(actual)},
                "when": {},
                "then": {"expected_file": str(expected)},
            })
            oracle_manifest = {
                "schema_version": 1,
                "pipeline_id": "TMP-ORACLE-SOURCE",
                "oracles": [{
                    "name": "O1",
                    "source": "pm",
                    "case_kind": "edge",
                    "input_path": str(oracle_input),
                    "expected_path": str(oracle_expected),
                }],
            }
            paths = {"oracle_manifest": root / "oracle_manifest.json"}
            pipeline._write_json(paths["oracle_manifest"], oracle_manifest)

            audit = pipeline._audit_contract_bundle(contract, test_set, paths)

        self.assertEqual(audit["status"], "FAIL")
        self.assertTrue(any("source must be user" in item for item in audit["blockers"]))
        self.assertTrue(any("input_sha256 is required" in item for item in audit["blockers"]))
        self.assertTrue(any("expected_sha256 is required" in item for item in audit["blockers"]))

    def test_oracle_waiver_does_not_cover_weak_oracle_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual.json"
            expected = root / "expected.json"
            oracle_input = root / "oracle_input.json"
            oracle_expected = root / "oracle_expected.json"
            for path in (actual, expected, oracle_input):
                path.write_text('{"value": 1}\n', encoding="utf-8")
            oracle_expected.write_text('{"result": "TODO"}\n', encoding="utf-8")

            contract = build_initial_contract("TMP-ORACLE-WAIVER", "docs task")
            contract["task_profile"]["deliverable_kind"] = "docs"
            contract["definition_of_ready"]["min_edge_cases_total"] = 0
            contract["modules"].append({
                "id": "M1",
                "name": "Docs",
                "inputs": [],
                "outputs": [],
                "acceptance_rules": [],
                "exceptions": [],
            })
            test_set = build_initial_test_set("TMP-ORACLE-WAIVER")
            test_set["tests"].append({
                "id": "T1",
                "module": "M1",
                "type": "json_exact_match",
                "priority": "P0",
                "case_kind": "normal",
                "points": 1,
                "given": {"actual_file": str(actual)},
                "when": {},
                "then": {"expected_file": str(expected)},
            })
            oracle_manifest = {
                "schema_version": 1,
                "pipeline_id": "TMP-ORACLE-WAIVER",
                "oracles": [{
                    "name": "O1",
                    "source": "user",
                    "case_kind": "normal",
                    "input_path": str(oracle_input),
                    "expected_path": str(oracle_expected),
                    "input_sha256": pipeline._sha256_file(oracle_input),
                    "expected_sha256": pipeline._sha256_file(oracle_expected),
                }],
            }
            paths = {"oracle_manifest": root / "oracle_manifest.json"}
            pipeline._write_json(paths["oracle_manifest"], oracle_manifest)

            audit = pipeline._audit_contract_bundle(
                contract,
                test_set,
                paths,
                allow_no_oracle=True,
                waiver_reason="docs-only task",
            )

        self.assertEqual(audit["status"], "FAIL")
        self.assertTrue(any("waiver cannot cover" in item for item in audit["blockers"]))

    def test_oracle_waiver_can_cover_missing_oracle_for_explicit_docs_task(self) -> None:
        contract = build_initial_contract("TMP-ORACLE-DOCS", "docs task")
        contract["task_profile"]["deliverable_kind"] = "docs"
        contract["definition_of_ready"]["min_edge_cases_total"] = 0
        contract["modules"].append({
            "id": "M1",
            "name": "Docs",
            "inputs": [],
            "outputs": [],
            "acceptance_rules": [],
            "exceptions": [],
        })
        test_set = build_initial_test_set("TMP-ORACLE-DOCS")
        test_set["tests"].append({
            "id": "T1",
            "module": "M1",
            "type": "json_exact_match",
            "priority": "P0",
            "case_kind": "normal",
            "points": 1,
            "given": {"actual_file": "actual.json"},
            "when": {},
            "then": {"expected_file": "expected.json"},
        })
        with tempfile.TemporaryDirectory() as tmp:
            paths = {"oracle_manifest": Path(tmp) / "oracle_manifest.json"}
            audit = pipeline._audit_contract_bundle(
                contract,
                test_set,
                paths,
                allow_no_oracle=True,
                waiver_reason="docs-only task",
            )

        self.assertEqual(audit["status"], "PASS")
        self.assertTrue(any("oracle gate waived" in item for item in audit["warnings"]))

    def test_architect_complete_blocks_until_external_gates_pass(self) -> None:
        state = pipeline._new_state("TMP-ARCH", "FEAT", "sample")
        state["current_phase"] = "architect"
        state["phases"]["pm"]["status"] = "DONE"
        state["phases"]["dev"]["status"] = "DONE"
        state["phases"]["qa"]["status"] = "PASS"
        state["phases"]["sec"]["status"] = "SKIP"
        state["phases"]["build"]["status"] = "DONE"
        state["phases"]["harness"]["status"] = "PASS"
        state["external_gates"] = pipeline._new_external_gates(enabled=True)

        with tempfile.TemporaryDirectory() as tmp:
            report = _write_architect_report(tmp)
            args = argparse.Namespace(branch=None, report_file=str(report))
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_architect(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertNotEqual(state.get("terminal_state"), "COMPLETE")

    def test_architect_records_protocol_evolution_decision_without_auto_phase9(self) -> None:
        state = pipeline._new_state("TMP-ARCH-DECISION", "IMP", "protocol docs")
        state["current_phase"] = "architect"
        state["phases"]["pm"]["status"] = "DONE"
        state["phases"]["dev"]["status"] = "DONE"
        state["phases"]["qa"]["status"] = "PASS"
        state["phases"]["sec"]["status"] = "SKIP"
        state["phases"]["build"]["status"] = "DONE"
        state["phases"]["harness"]["status"] = "PASS"
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        for gate in ("technical", "oracle", "acceptance", "github_ci"):
            state["external_gates"][gate]["status"] = "PASS"

        saved = {}
        with tempfile.TemporaryDirectory() as tmp:
            report = _write_architect_report(
                tmp,
                required=True,
                reason="task skill still documents legacy harness scoring",
                scope="task_skill",
            )
            args = argparse.Namespace(branch=None, report_file=str(report))
            history = Path(tmp) / "history"
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state), \
                 mock.patch.object(pipeline, "_record_snapshot"), \
                 mock.patch.object(pipeline, "_save_state_for", side_effect=lambda s, b: saved.update(s)), \
                 mock.patch.object(pipeline, "HISTORY_DIR", history):
                pipeline.cmd_architect(args)

        self.assertEqual(state["terminal_state"], "COMPLETE")
        self.assertEqual(state["current_phase"], "COMPLETE")
        decision = state["protocol_evolution_decision"]
        self.assertIsNotNone(decision)
        self.assertTrue(decision["required"])
        self.assertEqual(decision["scope"], "task_skill")
        self.assertEqual(decision["recommended_pipeline_type"], "IMP")
        self.assertEqual(saved["current_phase"], "COMPLETE")

    def test_legacy_harness_score_blocked_in_three_gate_mode(self) -> None:
        state = pipeline._new_state("TMP-HARNESS", "FEAT", "sample")
        state["current_phase"] = "harness"
        state["phases"]["pm"]["status"] = "DONE"
        state["phases"]["dev"]["status"] = "DONE"
        state["phases"]["qa"]["status"] = "PASS"
        state["phases"]["sec"]["status"] = "SKIP"
        state["phases"]["build"]["status"] = "DONE"
        state["external_gates"] = pipeline._new_external_gates(enabled=True)

        args = argparse.Namespace(
            branch=None,
            user_confirmed=True,
            test_output_file=None,
            score=100,
            verdict="PASS",
        )
        with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
            with self.assertRaises(SystemExit) as ctx:
                pipeline.cmd_harness(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["harness"]["status"], "PENDING")

    def test_oracle_gate_accepts_user_waiver_from_passed_audit(self) -> None:
        state = pipeline._new_state("TMP-WAIVE", "IMP", "docs task")
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        state["external_gates"]["technical"]["status"] = "PASS"
        state["pipeline_id"] = "TMP-WAIVE"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "oracle_manifest": root / "oracle_manifest.json",
                "contract_audit": root / "contract_audit.json",
                "oracle_result": root / "gates" / "oracle_result.json",
            }
            audit = {
                "schema_version": 1,
                "pipeline_id": "TMP-WAIVE",
                "status": "PASS",
                "allow_no_oracle": True,
                "waiver_reason": "docs-only task",
            }
            pipeline._write_json(paths["contract_audit"], audit)
            args = argparse.Namespace(gates_action="oracle", user_confirmed=True)
            with mock.patch.object(pipeline, "_require_state", return_value=state):
                with mock.patch.object(pipeline, "_contract_paths", return_value=paths):
                    with mock.patch.object(pipeline, "_save"):
                        pipeline.cmd_gates(args)

        self.assertEqual(state["external_gates"]["oracle"]["status"], "PASS")
        self.assertEqual(state["external_gates"]["oracle"]["evidence"], "oracle_waived_by_user")

    def test_github_repo_from_remote_parses_https_and_ssh_urls(self) -> None:
        self.assertEqual(
            pipeline._github_repo_from_remote("https://github.com/hojiyong2-commits/Pipeline.git"),
            "hojiyong2-commits/Pipeline",
        )
        self.assertEqual(
            pipeline._github_repo_from_remote("git@github.com:hojiyong2-commits/Pipeline.git"),
            "hojiyong2-commits/Pipeline",
        )

    def test_github_attestation_zip_reader_loads_json(self) -> None:
        payload = {
            "schema_version": 1,
            "attestation_type": "pipeline-ci-v1",
            "repository": "hojiyong2-commits/Pipeline",
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("pipeline_attestation.json", json.dumps(payload))

        self.assertEqual(pipeline._read_attestation_from_zip(buffer.getvalue()), payload)

    def test_github_attestation_validation_passes_for_matching_ci_result(self) -> None:
        commit_sha = "a" * 40
        tree_sha = "b" * 40
        attestation = {
            "schema_version": 1,
            "attestation_type": "pipeline-ci-v1",
            "repository": "hojiyong2-commits/Pipeline",
            "run_id": "123456",
            "commit_sha": commit_sha,
            "tree_sha": tree_sha,
            "tests": {
                "command": "python -m pytest -q",
                "status": "PASS",
            },
        }

        result = pipeline._validate_github_ci_attestation(
            attestation,
            repo="hojiyong2-commits/Pipeline",
            run_id="123456",
            commit_sha=commit_sha,
            tree_sha=tree_sha,
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["blockers"], [])

    def test_github_attestation_validation_blocks_mismatch(self) -> None:
        attestation = {
            "schema_version": 1,
            "attestation_type": "pipeline-ci-v1",
            "repository": "hojiyong2-commits/Pipeline",
            "run_id": "123456",
            "commit_sha": "a" * 40,
            "tree_sha": "b" * 40,
            "tests": {
                "command": "python -m pytest -q",
                "status": "FAIL",
            },
        }

        result = pipeline._validate_github_ci_attestation(
            attestation,
            repo="hojiyong2-commits/Pipeline",
            run_id="654321",
            commit_sha="c" * 40,
            tree_sha="d" * 40,
        )

        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("run_id mismatch" in item for item in result["blockers"]))
        self.assertIn("commit_sha mismatch", result["blockers"])
        self.assertIn("tree_sha comparison skipped for pull_request merge checkout", result["warnings"])
        self.assertIn("tests.status must be PASS", result["blockers"])

    def test_github_attestation_validation_accepts_pull_request_merge_checkout(self) -> None:
        head_sha = "a" * 40
        merge_sha = "b" * 40
        attestation = {
            "schema_version": 1,
            "attestation_type": "pipeline-ci-v1",
            "repository": "hojiyong2-commits/Pipeline",
            "run_id": "123456",
            "commit_sha": head_sha,
            "head_sha": head_sha,
            "checkout_sha": merge_sha,
            "tree_sha": "merge-tree",
            "tests": {
                "command": "python -m pytest -q",
                "status": "PASS",
            },
        }

        result = pipeline._validate_github_ci_attestation(
            attestation,
            repo="hojiyong2-commits/Pipeline",
            run_id="123456",
            commit_sha=head_sha,
            tree_sha="head-tree",
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["blockers"], [])
        self.assertIn("tree_sha comparison skipped for pull_request merge checkout", result["warnings"])

    def test_github_command_is_registered(self) -> None:
        parser = pipeline.build_parser()
        args = parser.parse_args(["github", "verify-run"])

        self.assertEqual(args.command, "github")
        self.assertEqual(args.github_action, "verify-run")
        self.assertIsNone(args.run_id)
        self.assertIs(pipeline.COMMAND_MAP["github"], pipeline.cmd_github)

    def test_github_token_falls_back_to_git_credentials(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["git", "credential", "fill"],
            returncode=0,
            stdout="protocol=https\nhost=github.com\nusername=x-access-token\npassword=gho_secret\n",
            stderr="",
        )

        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(pipeline.subprocess, "run", return_value=completed):
            self.assertEqual(pipeline._github_token("GITHUB_TOKEN"), "gho_secret")

    def test_github_verify_record_satisfies_external_ci_gate(self) -> None:
        state = pipeline._new_state("TMP-GH-CI", "IMP", "external runner")
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        commit_sha = "a" * 40
        tree_sha = "b" * 40
        attestation = {
            "schema_version": 1,
            "attestation_type": "pipeline-ci-v1",
            "repository": "hojiyong2-commits/Pipeline",
            "run_id": "123456",
            "commit_sha": commit_sha,
            "tree_sha": tree_sha,
            "tests": {
                "command": "python -m pytest -q",
                "status": "PASS",
            },
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("pipeline_attestation.json", json.dumps(attestation))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "github_ci_result": root / "gates" / "github_ci_result.json",
            }
            args = argparse.Namespace(
                github_action="verify-run",
                repo="hojiyong2-commits/Pipeline",
                run_id="123456",
                commit=commit_sha,
                workflow="CI",
                artifact="pipeline-attestation",
                token_env="GITHUB_TOKEN",
                record=True,
            )
            run_payload = {
                "id": 123456,
                "name": "CI",
                "html_url": "https://github.com/hojiyong2-commits/Pipeline/actions/runs/123456",
                "status": "completed",
                "conclusion": "success",
                "head_sha": commit_sha,
            }
            artifact_payload = {
                "artifacts": [{
                    "id": 99,
                    "name": "pipeline-attestation",
                    "archive_download_url": "https://api.github.com/artifact.zip",
                    "size_in_bytes": 512,
                    "expired": False,
                }]
            }
            with mock.patch.object(pipeline, "_require_state", return_value=state), \
                 mock.patch.object(pipeline, "_contract_paths", return_value=paths), \
                 mock.patch.object(pipeline, "_github_token", return_value="token"), \
                 mock.patch.object(pipeline, "_github_api_json", side_effect=[run_payload, artifact_payload]), \
                 mock.patch.object(pipeline, "_github_download_bytes", return_value=buffer.getvalue()), \
                 mock.patch.object(pipeline, "_git_rev_parse", return_value=tree_sha), \
                 mock.patch.object(pipeline, "_save"):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_github(args)

            result = pipeline._load_json_file(paths["github_ci_result"])

        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(state["external_gates"]["github_ci"]["status"], "PASS")
        self.assertEqual(
            state["external_gates"]["github_ci"]["evidence"],
            "github_actions_run:123456",
        )

    def test_gates_github_ci_finds_latest_run_and_records_gate(self) -> None:
        state = pipeline._new_state("TMP-GH-LATEST", "IMP", "external runner")
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        commit_sha = "a" * 40
        tree_sha = "b" * 40
        attestation = {
            "schema_version": 1,
            "attestation_type": "pipeline-ci-v1",
            "repository": "hojiyong2-commits/Pipeline",
            "run_id": "777",
            "commit_sha": commit_sha,
            "head_sha": commit_sha,
            "checkout_sha": commit_sha,
            "tree_sha": tree_sha,
            "tests": {
                "command": "python -m pytest -q",
                "status": "PASS",
            },
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("pipeline_attestation.json", json.dumps(attestation))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "github_ci_result": root / "gates" / "github_ci_result.json",
            }
            args = argparse.Namespace(
                gates_action="github-ci",
                repo="hojiyong2-commits/Pipeline",
                run_id=None,
                commit=commit_sha,
                workflow="CI",
                artifact="pipeline-attestation",
                token_env="GITHUB_TOKEN",
            )
            runs_payload = {
                "workflow_runs": [{
                    "id": 777,
                    "name": "CI",
                    "html_url": "https://github.com/hojiyong2-commits/Pipeline/actions/runs/777",
                    "status": "completed",
                    "conclusion": "success",
                    "head_sha": commit_sha,
                }]
            }
            artifact_payload = {
                "artifacts": [{
                    "id": 99,
                    "name": "pipeline-attestation",
                    "archive_download_url": "https://api.github.com/artifact.zip",
                    "size_in_bytes": 512,
                    "expired": False,
                }]
            }
            with mock.patch.object(pipeline, "_require_state", return_value=state), \
                 mock.patch.object(pipeline, "_contract_paths", return_value=paths), \
                 mock.patch.object(pipeline, "_github_token", return_value="token"), \
                 mock.patch.object(pipeline, "_github_api_json", side_effect=[runs_payload, artifact_payload]), \
                 mock.patch.object(pipeline, "_github_download_bytes", return_value=buffer.getvalue()), \
                 mock.patch.object(pipeline, "_git_rev_parse", return_value=tree_sha), \
                 mock.patch.object(pipeline, "_save"):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_gates(args)

            result = pipeline._load_json_file(paths["github_ci_result"])

        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["run_id"], "777")
        self.assertEqual(state["external_gates"]["github_ci"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
