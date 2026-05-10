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


def _write_module_design(directory: str, mt_id: str = "MT-1") -> Path:
    report = Path(directory) / f"module_design_{mt_id}.xml"
    report.write_text(
        f"""<module_design>
  <mt_id>{mt_id}</mt_id>
  <interface_contract>input and output contract</interface_contract>
  <implementation_plan>small implementation plan</implementation_plan>
  <verification_plan>module-level QA plan</verification_plan>
</module_design>
""",
        encoding="utf-8",
    )
    return report


def _write_module_handover(directory: str, mt_id: str = "MT-1") -> Path:
    report = Path(directory) / f"module_handover_{mt_id}.xml"
    report.write_text(
        f"""<module_handover>
  <mt_id>{mt_id}</mt_id>
  <implemented_files><file>main.py</file></implemented_files>
  <self_check>PASS</self_check>
</module_handover>
""",
        encoding="utf-8",
    )
    return report


def _write_module_qa(directory: str, mt_id: str = "MT-1", verdict: str = "PASS") -> Path:
    report = Path(directory) / f"module_qa_{mt_id}.xml"
    report.write_text(
        f"""<module_qa_report>
  <mt_id>{mt_id}</mt_id>
  <verdict>{verdict}</verdict>
  <verification_evidence>module behavior verified</verification_evidence>
</module_qa_report>
""",
        encoding="utf-8",
    )
    return report


def _write_integration_report(directory: str, verdict: str = "PASS") -> Path:
    report = Path(directory) / "integration_report.xml"
    report.write_text(
        f"""<integration_report>
  <modules_integrated>MT-1</modules_integrated>
  <integration_verdict>{verdict}</integration_verdict>
</integration_report>
""",
        encoding="utf-8",
    )
    return report


def _design_confirmation_xml(mt_id: str = "MT-1") -> str:
    return f"""  <design_confirmation>
    <module_split_presented>true</module_split_presented>
    <module_split_user_confirmed>true</module_split_user_confirmed>
    <maintenance_priority>maintainability_first</maintenance_priority>
    <low_value_questions_filtered>true</low_value_questions_filtered>
    <filter_summary>내부 변수명과 코드 취향 질문은 묻지 않고 기존 패턴과 유지보수성을 우선했습니다.</filter_summary>
    <decision_questions>
      <question id="DQ-1" priority="P1" category="module_split" mt_id="{mt_id}">
        <user_facing_question>이번 작업은 {mt_id} 단위로 작게 나눠 진행해도 될까요?</user_facing_question>
        <evidence>사용자 요청과 Grep 결과 변경 범위가 이 모듈에 모였습니다.</evidence>
        <why_it_matters>모듈 단위가 맞아야 수정 범위가 작고 나중에 유지보수가 쉬워집니다.</why_it_matters>
        <recommended_option>A</recommended_option>
        <options>
          <option id="A">
            <label>{mt_id} 단위로 진행</label>
            <benefit>변경 범위가 작고 검증 위치가 명확합니다.</benefit>
            <cost>기능이 커지면 다음 작업에서 추가 분리가 필요할 수 있습니다.</cost>
          </option>
          <option id="B">
            <label>더 작게 다시 분해</label>
            <benefit>각 변경을 더 세밀하게 검토할 수 있습니다.</benefit>
            <cost>작업 시간이 늘고 불필요한 질문이 늘 수 있습니다.</cost>
          </option>
        </options>
        <user_answer>추천안 A로 진행</user_answer>
      </question>
    </decision_questions>
  </design_confirmation>
"""


def _task_complexity_xml(
    profile: str = "STANDARD",
    *,
    p0: int = 0,
    p1: int = 0,
    output_format_clear: bool = True,
    files: int = 1,
    functions: int = 0,
    lines: int = 40,
    risk_overrides: dict | None = None,
) -> str:
    risks = {
        "data_deletion": False,
        "file_move": False,
        "external_api": False,
        "auth_or_secret": False,
        "pipeline_protocol": False,
        "build_or_deploy": False,
        "core_parser_logic": False,
        "database_or_migration": False,
        "new_dependency": False,
    }
    if risk_overrides:
        risks.update(risk_overrides)
    risk_xml = "\n".join(
        f"    <{name}>{str(value).lower()}</{name}>"
        for name, value in risks.items()
    )
    return f"""  <task_complexity>
    <execution_profile>{profile}</execution_profile>
    <reason>테스트용 실행 프로필 선언</reason>
    <uncertainty>
      <p0_questions>{p0}</p0_questions>
      <p1_questions>{p1}</p1_questions>
      <output_format_clear>{str(output_format_clear).lower()}</output_format_clear>
    </uncertainty>
    <blast_radius>
      <expected_changed_files>{files}</expected_changed_files>
      <expected_changed_functions>{functions}</expected_changed_functions>
      <expected_changed_lines>{lines}</expected_changed_lines>
    </blast_radius>
    <risk_flags>
{risk_xml}
    </risk_flags>
  </task_complexity>
"""


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


def _write_manager_handoff(directory: Path, state: dict, step_plan: Path, planner_run_id: str) -> Path:
    report = directory / "manager_handoff.xml"
    report.write_text(
        f"""<manager_handoff>
  <pipeline_id>{state["pipeline_id"]}</pipeline_id>
  <from>pipeline-manager-agent</from>
  <step_plan_sha256>{pipeline._sha256_file(step_plan)}</step_plan_sha256>
  <planner_run_id>{planner_run_id}</planner_run_id>
  <accepted_for_execution>true</accepted_for_execution>
  <will_not_modify_step_plan>true</will_not_modify_step_plan>
  <next_phase>dev</next_phase>
</manager_handoff>
""",
        encoding="utf-8",
    )
    return report


def _install_pm_split_runs(state: dict, step_plan: Path, root: Path) -> tuple[str, str, Path]:
    planner_run_id = _install_completed_agent_run(state, "pm_planner", step_plan, root)
    manager_report = _write_manager_handoff(root, state, step_plan, planner_run_id)
    manager_run_id = _install_completed_agent_run(state, "pipeline_manager", manager_report, root)
    return planner_run_id, manager_run_id, manager_report


def _mark_phase_attestation_passed(state: dict, *phases: str) -> None:
    state["phase_attestations"] = pipeline._ensure_phase_attestations(state)
    state["phase_attestations"]["enabled"] = True
    for phase in phases:
        state["phase_attestations"]["phases"][phase]["status"] = "PASS"
        state["phase_attestations"]["phases"][phase]["phase"] = phase
        state["phase_attestations"]["phases"][phase]["completed_at"] = pipeline._now()


def _mark_module_gates_passed(state: dict) -> None:
    pipeline._init_module_gates_from_atomic_plan(state)
    gates = state["module_gates"]
    for mt_id in gates["sequence"]:
        module = gates["modules"][mt_id]
        module["design"]["status"] = "PASS"
        module["dev"]["status"] = "DONE"
        module["qa"]["status"] = "PASS"
        module["status"] = "PASS"
        module["checkpoint"] = pipeline._module_checkpoint_for_files(module.get("target_files", []))
    gates["integration"]["status"] = "PASS"
    gates["integration"]["completed_at"] = pipeline._now()


def _write_oracle_file(root: Path, name: str, payload: str = '{"value": 1}\n') -> Path:
    path = root / "tests" / "oracles" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


class ThreeGatePipelineTests(unittest.TestCase):
    def test_new_state_enables_external_phase_and_module_gates_by_default(self) -> None:
        state = pipeline._new_state("TMP-DEFAULT-GATES", "FEAT", "sample")
        self.assertTrue(state["external_gates"]["enabled"])
        self.assertTrue(state["phase_attestations"]["enabled"])
        self.assertTrue(state["module_gates"]["enabled"])

    def test_task_command_forbids_classic_and_requires_external_gates(self) -> None:
        root = Path(__file__).resolve().parent
        task_md = (root / ".claude" / "commands" / "task.md").read_text(encoding="utf-8")
        self.assertIn("Classic 모드는 없다", task_md)
        self.assertIn("Three-Gate + Option A phase attestation + Incremental Module Gate", task_md)
        self.assertIn("pipeline.py gates technical", task_md)
        self.assertIn("pipeline.py module design", task_md)
        self.assertNotIn("pipeline.py harness --score [점수]", task_md)
        self.assertNotIn("PASS (≥ 80", task_md)

    def test_help_description_uses_dash_not_option_like_phase(self) -> None:
        parser = pipeline.build_parser()
        self.assertIn("Enforcer — Phase", parser.description or "")
        self.assertNotIn("Enforcer -Phase", parser.description or "")

    def test_github_review_surfaces_are_codeowned_and_templated(self) -> None:
        root = Path(__file__).resolve().parent
        codeowners = (root / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
        template = root / ".github" / "pull_request_template.md"
        workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        readme_text = (root / "README.md").read_text(encoding="utf-8")

        for pattern in (
            ".github/CODEOWNERS",
            ".gitattributes",
            ".github/pull_request_template.md",
            "tests/oracles/**",
            "tests/**",
            "test_*.py",
            "*_test.py",
        ):
            self.assertIn(pattern, codeowners)
        self.assertTrue(template.exists())
        template_text = template.read_text(encoding="utf-8")
        self.assertIn("최종 확인 안내", template_text)
        self.assertIn("결과물이 내가 요청한 내용과 맞다", template_text)
        self.assertIn("승인(ACCEPT)", template_text)
        self.assertIn("거절(REJECT)", template_text)
        self.assertIn("중요한 선택과 트레이드오프", template_text)
        self.assertIn("정보 부족", template_text)
        self.assertIn("최종-확인-안내", workflow)
        self.assertIn("pipeline-human-acceptance-packet", workflow)
        self.assertIn("최종 확인 안내", workflow)
        self.assertIn("코드를 읽지 말고", workflow)
        self.assertIn("같은 댓글을 갱신합니다", workflow)
        self.assertIn("마지막 갱신", workflow)
        self.assertIn("판단 정보 상태", workflow)
        self.assertIn("정보 부족", workflow)
        self.assertIn("중요한 선택과 트레이드오프", workflow)
        self.assertIn("Normalize-DecisionText", workflow)
        self.assertIn("기대 결과와 실제 결과 비교", workflow)
        self.assertIn("업무 결과물이 따로 없는 규칙/문서 작업이면", workflow)
        self.assertIn("Convert-FileStatusToKorean", workflow)
        self.assertIn("- ${statusKo}:", workflow)
        self.assertIn('"modified" { return "수정됨" }', workflow)
        self.assertIn('"added" { return "새 파일" }', workflow)
        self.assertIn("자동 검사: 통과", workflow)
        self.assertIn("첨부파일", workflow)
        self.assertIn("브랜치로 합치기", workflow)
        self.assertNotIn("- CI: PASS", workflow)
        self.assertNotIn("Actions 실행/첨부파일", workflow)
        self.assertNotIn("마지막 agent", workflow)
        self.assertNotIn("``$($file.status)``", workflow)
        self.assertIn("issues/comments/$($existing.id)", workflow)
        self.assertNotIn('"$commentsUri/$($existing.id)"', workflow)
        self.assertIn("마지막 작업 담당자", readme_text)
        self.assertIn("첨부파일 링크", readme_text)
        self.assertIn("판단 정보 상태: 정보 부족", readme_text)
        self.assertIn("중요한 선택과 트레이드오프", readme_text)
        self.assertNotIn("마지막 agent", readme_text)

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
        _mark_phase_attestation_passed(state, "build")
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
        args = argparse.Namespace(gates_action="oracle", user_confirmed=False)
        stderr = io.StringIO()
        with mock.patch.object(pipeline, "_require_state", return_value=state), \
             mock.patch("sys.stderr", stderr):
            with self.assertRaises(SystemExit) as ctx:
                pipeline.cmd_gates(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("technical gate PASS first", stderr.getvalue())

    def test_phase7_check_no_longer_requires_user_confirmed(self) -> None:
        state = pipeline._new_state("TMP-HARNESS-AUTO", "IMP", "sample")
        state["current_phase"] = "harness"
        state["phases"]["build"]["status"] = "DONE"
        _mark_phase_attestation_passed(state, "build")

        args = argparse.Namespace(phase="harness", branch=None, user_confirmed=False)
        with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
            with self.assertRaises(SystemExit) as ctx:
                pipeline.cmd_check(args)

        self.assertEqual(ctx.exception.code, 0)

    def test_na_build_no_longer_requires_user_confirmed(self) -> None:
        state = pipeline._new_state("TMP-NA-BUILD-AUTO", "IMP", "docs")
        state["current_phase"] = "build"
        state["phases"]["pm"]["status"] = "DONE"
        state["phases"]["dev"]["status"] = "DONE"
        state["phases"]["qa"]["status"] = "PASS"
        state["phases"]["sec"]["status"] = "SKIP"
        _mark_phase_attestation_passed(state, "qa")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "build_report.xml"
            output.write_text("<build_report><status>N/A</status></build_report>", encoding="utf-8")
            run_id = _install_completed_agent_run(state, "build", output, root)
            args = argparse.Namespace(
                branch=None,
                exe="N/A",
                skip_reason="docs-only",
                report_file=None,
                user_confirmed=False,
                agent_run_id=run_id,
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state), \
                 mock.patch.object(pipeline, "_record_snapshot"), \
                 mock.patch.object(pipeline, "_save_state_for"):
                pipeline.cmd_build(args)

        self.assertEqual(state["phases"]["build"]["status"], "DONE")
        self.assertEqual(state["phases"]["build"]["skip_reason"], "docs-only")

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
            planner_run_id, manager_run_id, manager_report = _install_pm_split_runs(state, report, root)
            state["phases"]["pm"]["agent_run_id"] = planner_run_id
            state["phases"]["pm"]["manager_run_id"] = manager_run_id
            state["phases"]["pm"]["manager_report_file"] = str(manager_report)
            with mock.patch.object(pipeline, "PHASE_ATTESTATION_REQUEST", request_path), \
                 mock.patch.object(pipeline, "PHASE_ATTESTATION_EVIDENCE_DIR", evidence_dir), \
                 mock.patch.object(pipeline, "_git_check_ignored", return_value=False), \
                 mock.patch.object(pipeline, "_git_rev_parse", return_value="a" * 40):
                request = pipeline._prepare_phase_attestation_request(state, "pm")

            self.assertEqual(request["phase"], "pm")
            self.assertEqual(request["pipeline_id"], "TMP-PHASE-PREP")
            self.assertEqual(request["agent_run"]["agent_id"], "pm-planner-agent")
            self.assertEqual(request["agent_run"]["phase"], "pm_planner")
            self.assertEqual(request["agent_run"]["used_by_phase"], "pm")
            self.assertEqual(request["manager_run"]["agent_id"], "pipeline-manager-agent")
            self.assertEqual(request["manager_run"]["used_by_phase"], "pm_manager")
            self.assertTrue(request_path.exists())
            labels = {item["label"] for item in request["copied_evidence"]}
            self.assertIn("agent_receipt", labels)
            self.assertIn("manager_receipt", labels)
            self.assertIn("manager_report", labels)
            self.assertIn("report", labels)
            receipt_copy = next(item for item in request["copied_evidence"] if item["label"] == "agent_receipt")
            self.assertEqual(request["agent_run"]["receipt_path"], receipt_copy["path"])
            self.assertEqual(request["agent_run"]["receipt_sha256"], receipt_copy["sha256"])
            for copied in request["copied_evidence"]:
                copied_path = Path(copied["path"])
                if not copied_path.is_absolute():
                    copied_path = pipeline.BASE_DIR / copied_path
                self.assertTrue(copied_path.exists())

    def test_phase_evidence_git_hygiene_is_documented_as_force_added_transient_evidence(self) -> None:
        root = Path(__file__).resolve().parent
        gitignore = (root / ".gitignore").read_text(encoding="utf-8")
        gitattributes = (root / ".gitattributes").read_text(encoding="utf-8")
        claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
        pm = (root / ".claude" / "agents" / "pm-agent.md").read_text(encoding="utf-8")
        architect = (root / ".claude" / "agents" / "prompt-architect-agent.md").read_text(encoding="utf-8")

        self.assertIn(".pipeline/", gitignore)
        self.assertIn("git add -f .pipeline/phase_attestation_request.json .pipeline/phase_evidence", gitignore)
        self.assertIn("*.xml text eol=lf", gitattributes)
        self.assertIn("*.json text eol=lf", gitattributes)
        self.assertIn("*.md text eol=lf", gitattributes)
        self.assertIn("Phase Evidence Git Hygiene", claude)
        self.assertIn("git add -f .pipeline/phase_attestation_request.json .pipeline/phase_evidence", claude)
        self.assertIn("`.gitattributes` must", claude)
        self.assertIn("git add -f .pipeline/phase_attestation_request.json .pipeline/phase_evidence", pm)
        self.assertIn("Phase attestation hash mismatch or missing evidence", architect)

    def test_copy_phase_evidence_marks_force_add_when_destination_is_gitignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "step_plan.xml"
            source.write_text("<step_plan></step_plan>", encoding="utf-8")
            evidence_dir = root / ".pipeline" / "phase_evidence"

            with mock.patch.object(pipeline, "BASE_DIR", root), \
                 mock.patch.object(pipeline, "PHASE_ATTESTATION_EVIDENCE_DIR", evidence_dir), \
                 mock.patch.object(pipeline, "_git_check_ignored", return_value=True):
                copied = pipeline._copy_phase_evidence_file("TMP-IGNORED", "build", "report", str(source))

        self.assertIsNotNone(copied)
        self.assertTrue(copied["requires_force_add"])

    def test_relaxed_tools_and_qa_numeric_score_are_documented_as_hard_gates(self) -> None:
        root = Path(__file__).resolve().parent
        claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
        dev = (root / ".claude" / "agents" / "dev-agent.md").read_text(encoding="utf-8")
        build = (root / ".claude" / "agents" / "build-agent.md").read_text(encoding="utf-8")
        qa = (root / ".claude" / "agents" / "qa-agent.md").read_text(encoding="utf-8")

        for text in (claude, dev):
            self.assertIn("--relaxed-tools", text)
            self.assertIn("diagnostic only", text)
            self.assertIn("non-complete-eligible FAIL", text)
        self.assertIn("진단용", build)
        self.assertIn("COMPLETE 불가 FAIL", build)
        self.assertIn('--numeric-score 값은 PASS/FAIL 모두 0~120 정수만 허용', qa)
        self.assertIn('"N/A", "NA", "PASS", "FAIL", "100%"', qa)
        self.assertIn("CLI `--numeric-score`는 항상 0~120 정수", qa)

    def test_agent_run_receipt_roundtrip_validates_token_and_output(self) -> None:
        state = pipeline._new_state("TMP-AGENT-RUN", "IMP", "sample")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "step_plan.xml"
            output.write_text("<step_plan></step_plan>", encoding="utf-8")
            with mock.patch.object(pipeline, "AGENT_RECEIPT_DIR", root / "receipts"), \
                 mock.patch.object(pipeline, "_git_rev_parse", return_value="a" * 40):
                run, token = pipeline._agent_run_start(state, "pm_planner", "pm-planner-agent")
                self.assertTrue(token.startswith("tok_"))
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
        self.assertEqual(completed["agent_id"], "pm-planner-agent")
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
""" + _design_confirmation_xml() + _task_complexity_xml("STANDARD", functions=1) + """
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
""" + _design_confirmation_xml() + _task_complexity_xml("STANDARD", functions=1) + """
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
            planner_run_id, manager_run_id, manager_report = _install_pm_split_runs(state, report, root)
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
                planner_run_id=planner_run_id,
                manager_run_id=manager_run_id,
                manager_report=str(manager_report),
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state), \
                 mock.patch.object(pipeline, "_save_state_for"), \
                 mock.patch.object(pipeline, "_record_snapshot"):
                pipeline.cmd_done(args)

        self.assertEqual(state["phases"]["pm"]["status"], "DONE")
        self.assertEqual(state["phases"]["pm"]["agent_run_id"], planner_run_id)
        self.assertEqual(state["phases"]["pm"]["manager_run_id"], manager_run_id)
        self.assertEqual(state["agent_runs"][planner_run_id]["used_by_phase"], "pm")
        self.assertEqual(state["agent_runs"][manager_run_id]["used_by_phase"], "pm_manager")

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

    def test_accept_gate_rejects_placeholder_evidence(self) -> None:
        state = pipeline._new_state("TMP-ACCEPT-PLACEHOLDER", "FEAT", "sample")
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        for gate_name in ("technical", "oracle", "github_ci"):
            state["external_gates"][gate_name]["status"] = "PASS"

        args = argparse.Namespace(
            gates_action="accept",
            result="ACCEPT",
            evidence="USER_CONFIRMED",
            notes="approved by user",
            user_confirmed=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {"user_validation": root / "user_validation.json"}
            with mock.patch.object(pipeline, "_require_state", return_value=state), \
                 mock.patch.object(pipeline, "_contract_paths", return_value=paths), \
                 mock.patch.object(pipeline, "_record_snapshot"), \
                 mock.patch.object(pipeline, "_save"):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_gates(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["external_gates"]["acceptance"]["status"], "PENDING")

    def test_accept_gate_rejects_unknown_result_value(self) -> None:
        state = pipeline._new_state("TMP-ACCEPT-UNKNOWN", "FEAT", "sample")
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        for gate_name in ("technical", "oracle", "github_ci"):
            state["external_gates"][gate_name]["status"] = "PASS"

        args = argparse.Namespace(
            gates_action="accept",
            result="MAYBE",
            evidence="https://github.com/hojiyong2-commits/Pipeline/pull/1",
            notes="invalid decision",
            user_confirmed=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {"user_validation": root / "user_validation.json"}
            with mock.patch.object(pipeline, "_require_state", return_value=state), \
                 mock.patch.object(pipeline, "_contract_paths", return_value=paths), \
                 mock.patch.object(pipeline, "_record_snapshot"), \
                 mock.patch.object(pipeline, "_save"):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_gates(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["external_gates"]["acceptance"]["status"], "PENDING")

    def test_accept_gate_reject_records_failure_packet(self) -> None:
        state = pipeline._new_state("TMP-ACCEPT-REJECT", "FEAT", "sample")
        state["external_gates"] = pipeline._new_external_gates(enabled=True)
        for gate_name in ("technical", "oracle", "github_ci"):
            state["external_gates"][gate_name]["status"] = "PASS"

        args = argparse.Namespace(
            gates_action="accept",
            result="REJECT",
            evidence="https://github.com/hojiyong2-commits/Pipeline/pull/1",
            notes="결과물이 요청과 다름",
            user_confirmed=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "user_validation": root / "user_validation.json",
                "failures_root": root / "failures",
            }
            with mock.patch.object(pipeline, "_require_state", return_value=state), \
                 mock.patch.object(pipeline, "_contract_paths", return_value=paths), \
                 mock.patch.object(pipeline, "_record_snapshot"), \
                 mock.patch.object(pipeline, "_save"):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_gates(args)
                packet = paths["failures_root"] / "acceptance_attempt_1.json"
                self.assertTrue(packet.exists())

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["external_gates"]["acceptance"]["status"], "FAIL")
        self.assertEqual(state["failure_packets"][0]["gate"], "acceptance")

    def test_acceptance_record_is_blocked_in_mandatory_three_gate(self) -> None:
        state = pipeline._new_state("TMP-ACCEPTANCE-RECORD", "IMP", "sample")
        state["current_phase"] = "harness"
        state["phases"]["build"]["status"] = "DONE"
        args = argparse.Namespace(
            acceptance_action="run",
            pipeline_id=None,
            output=None,
            record=True,
        )
        with mock.patch.object(
            pipeline,
            "_resolve_pipeline_context",
            return_value=("TMP-ACCEPTANCE-RECORD", None, None, state),
        ):
            with self.assertRaises(SystemExit) as ctx:
                pipeline.cmd_acceptance(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["harness"]["status"], "PENDING")

    def test_ast_forbidden_allows_safe_os_usage_but_blocks_os_exec(self) -> None:
        safe_code = """import os\n\n\ndef test_env_path():\n    assert os.path.join('a', 'b') == os.path.join('a', 'b')\n    assert os.environ.get('MISSING_KEY') is None\n"""
        unsafe_code = """import os\n\n\ndef test_shell():\n    os.system('echo forged')\n    assert True\n"""

        self.assertIsNone(pipeline._ast_forbidden_check(safe_code))
        self.assertIn("os.system", pipeline._ast_forbidden_check(unsafe_code) or "")

    def test_qa_failure_signature_requires_hash8(self) -> None:
        state = pipeline._new_state("TMP-QA-SIG", "IMP", "sample")
        args = argparse.Namespace(
            branch=None,
            result="FAIL",
            numeric_score="72",
            failure_sig="FS:enc_fallback_missing",
            report_file=None,
            agent_id=None,
            agent_run_id=None,
        )
        with mock.patch.object(pipeline, "_load_branch_state", return_value=state), \
             mock.patch.object(pipeline, "check_gate", return_value=(True, "")):
            with self.assertRaises(SystemExit) as ctx:
                pipeline.cmd_qa(args)

        self.assertEqual(ctx.exception.code, 1)

    def test_deployment_root_reports_missing_drive_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing-parent" / "deploy"
            with mock.patch.dict(pipeline.os.environ, {"PIPELINE_DEPLOY_ROOT": str(missing)}):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline._deployment_root()

        self.assertEqual(ctx.exception.code, 1)

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

    def test_pm_done_requires_user_design_confirmation(self) -> None:
        state = pipeline._new_state("TMP-PM-DESIGN-REQ", "FEAT", "sample")
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
  <pipeline_id>TMP-PM-DESIGN-REQ</pipeline_id>
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
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline._validate_pm_step_plan_file(str(report), state)

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("PM DESIGN GATE", stderr.getvalue())

    def test_pm_design_confirmation_filters_p2_questions(self) -> None:
        state = pipeline._new_state("TMP-PM-DESIGN-P2", "FEAT", "sample")
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
  <pipeline_id>TMP-PM-DESIGN-P2</pipeline_id>
  <design_confirmation>
    <module_split_presented>true</module_split_presented>
    <module_split_user_confirmed>true</module_split_user_confirmed>
    <maintenance_priority>maintainability_first</maintenance_priority>
    <low_value_questions_filtered>true</low_value_questions_filtered>
    <filter_summary>중요하지 않은 내부 구현 취향 질문은 묻지 않고 유지보수성 기준으로 정리했습니다.</filter_summary>
    <decision_questions>
      <question id="DQ-1" priority="P2" category="module_split" mt_id="MT-1">
        <user_facing_question>이번 작업은 MT-1 단위로 진행해도 될까요?</user_facing_question>
        <evidence>사용자 요청과 Grep 결과 변경 범위가 한 곳에 모였습니다.</evidence>
        <why_it_matters>분해 단위가 맞아야 수정 범위가 작고 유지보수가 쉬워집니다.</why_it_matters>
        <recommended_option>A</recommended_option>
        <options>
          <option id="A">
            <label>MT-1 단위로 진행</label>
            <benefit>변경 범위와 검증 지점이 명확합니다.</benefit>
            <cost>추가 요구가 생기면 새 모듈이 필요합니다.</cost>
          </option>
          <option id="B">
            <label>더 작게 다시 분해</label>
            <benefit>더 세밀한 검토가 가능합니다.</benefit>
            <cost>질문과 작업 시간이 늘어납니다.</cost>
          </option>
        </options>
        <user_answer>추천안 A로 진행</user_answer>
      </question>
    </decision_questions>
  </design_confirmation>
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
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline._validate_pm_step_plan_file(str(report), state)

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("P2/internal implementation preferences must be filtered", stderr.getvalue())

    def test_pm_done_requires_task_complexity_profile(self) -> None:
        state = pipeline._new_state("TMP-PM-PROFILE-REQ", "FEAT", "sample")
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
  <pipeline_id>TMP-PM-PROFILE-REQ</pipeline_id>
  <anti_gaming_read>true</anti_gaming_read>
""" + _design_confirmation_xml() + """
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
            with self.assertRaises(SystemExit) as ctx:
                pipeline._validate_pm_step_plan_file(str(report), state)

        self.assertEqual(ctx.exception.code, 1)

    def test_pm_done_records_atomic_step_plan_with_mandatory_gates(self) -> None:
        state = pipeline._new_state("TMP-PM-OK", "FEAT", "sample")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
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
""" + _design_confirmation_xml() + _task_complexity_xml("STANDARD", functions=1) + """
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
            planner_run_id, manager_run_id, manager_report = _install_pm_split_runs(state, report, root)
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
                planner_run_id=planner_run_id,
                manager_run_id=manager_run_id,
                manager_report=str(manager_report),
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with mock.patch.object(pipeline, "_save_state_for"):
                    with mock.patch.object(pipeline, "_record_snapshot"):
                        pipeline.cmd_done(args)

        self.assertEqual(state["phases"]["pm"]["status"], "DONE")
        self.assertEqual(state["atomic_plan"]["micro_task_count"], 1)
        self.assertEqual(state["atomic_plan"]["micro_tasks"][0]["id"], "MT-1")
        self.assertIn("project_snapshot", state["atomic_plan"])
        self.assertEqual(state["execution_profile"]["mode"], "STANDARD")

    def test_pm_done_rejects_manager_handoff_with_wrong_step_plan_hash(self) -> None:
        state = pipeline._new_state("TMP-PM-MANAGER-HASH", "FEAT", "sample")
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
  <pipeline_id>TMP-PM-MANAGER-HASH</pipeline_id>
  <anti_gaming_read>true</anti_gaming_read>
""" + _design_confirmation_xml() + _task_complexity_xml("STANDARD", functions=1) + """
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
            planner_run_id = _install_completed_agent_run(state, "pm_planner", report, root)
            manager_report = root / "manager_handoff.xml"
            manager_report.write_text(
                f"""<manager_handoff>
  <pipeline_id>{state["pipeline_id"]}</pipeline_id>
  <from>pipeline-manager-agent</from>
  <step_plan_sha256>{"0" * 64}</step_plan_sha256>
  <planner_run_id>{planner_run_id}</planner_run_id>
  <accepted_for_execution>true</accepted_for_execution>
  <will_not_modify_step_plan>true</will_not_modify_step_plan>
  <next_phase>dev</next_phase>
</manager_handoff>
""",
                encoding="utf-8",
            )
            manager_run_id = _install_completed_agent_run(state, "pipeline_manager", manager_report, root)
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
                planner_run_id=planner_run_id,
                manager_run_id=manager_run_id,
                manager_report=str(manager_report),
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_done(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["pm"]["status"], "PENDING")

    def test_pm_done_records_fast_analysis_execution_profile(self) -> None:
        state = pipeline._new_state("TMP-FAST-ANALYSIS", "IMP", "analyze logs")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "step_plan.xml"
            report.write_text(
                """
<decomposition_audit>
  <total_functions_identified>1</total_functions_identified>
  <micro_task_count>1</micro_task_count>
  <grep_executions>1</grep_executions>
  <split_decision>single report output</split_decision>
  <audit_result>SINGLE_TASK_OK</audit_result>
</decomposition_audit>
<step_plan>
  <pipeline_id>TMP-FAST-ANALYSIS</pipeline_id>
  <anti_gaming_read>true</anti_gaming_read>
""" + _design_confirmation_xml() + _task_complexity_xml("FAST_ANALYSIS", functions=0, lines=30) + """
  <micro_tasks>
    <micro_task id="MT-1">
      <affected_function>none.report</affected_function>
      <target_files><file>pipeline_outputs/TMP-FAST-ANALYSIS/report.md</file></target_files>
      <grep_evidence>
        <pattern>pipeline status</pattern>
        <match_count>1</match_count>
        <executed>true</executed>
      </grep_evidence>
      <change_summary>Write analysis report only</change_summary>
    </micro_task>
  </micro_tasks>
</step_plan>
""",
                encoding="utf-8",
            )
            planner_run_id, manager_run_id, manager_report = _install_pm_split_runs(state, report, root)
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
                planner_run_id=planner_run_id,
                manager_run_id=manager_run_id,
                manager_report=str(manager_report),
            )
            with mock.patch.object(pipeline, "BASE_DIR", root), \
                 mock.patch.object(pipeline, "_load_branch_state", return_value=state), \
                 mock.patch.object(pipeline, "_save_state_for"), \
                 mock.patch.object(pipeline, "_record_snapshot"):
                pipeline.cmd_done(args)

        profile = state["execution_profile"]
        self.assertEqual(profile["mode"], "FAST_ANALYSIS")
        self.assertFalse(profile["product_code_write_allowed"])
        self.assertEqual(profile["max_micro_tasks"], 1)
        self.assertEqual(profile["phase_ci_mode"], "batched")

    def test_pm_done_rejects_fast_analysis_with_multiple_micro_tasks(self) -> None:
        state = pipeline._new_state("TMP-FAST-MULTI", "IMP", "bad fast profile")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "step_plan.xml"
            report.write_text(
                """
<decomposition_audit>
  <total_functions_identified>2</total_functions_identified>
  <micro_task_count>2</micro_task_count>
  <grep_executions>2</grep_executions>
  <split_decision>two outputs</split_decision>
  <audit_result>SPLIT_REQUIRED</audit_result>
</decomposition_audit>
<step_plan>
  <pipeline_id>TMP-FAST-MULTI</pipeline_id>
  <anti_gaming_read>true</anti_gaming_read>
""" + _design_confirmation_xml() + _task_complexity_xml("FAST_ANALYSIS", files=2, functions=0) + """
  <micro_tasks>
    <micro_task id="MT-1">
      <affected_function>none.report_one</affected_function>
      <target_files><file>pipeline_outputs/TMP-FAST-MULTI/report1.md</file></target_files>
      <grep_evidence><pattern>one</pattern><match_count>1</match_count><executed>true</executed></grep_evidence>
      <change_summary>Write first report</change_summary>
    </micro_task>
    <micro_task id="MT-2">
      <affected_function>none.report_two</affected_function>
      <target_files><file>pipeline_outputs/TMP-FAST-MULTI/report2.md</file></target_files>
      <grep_evidence><pattern>two</pattern><match_count>1</match_count><executed>true</executed></grep_evidence>
      <change_summary>Write second report</change_summary>
    </micro_task>
  </micro_tasks>
</step_plan>
""",
                encoding="utf-8",
            )
            with mock.patch.object(pipeline, "BASE_DIR", root):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline._validate_pm_step_plan_file(str(report), state)
        self.assertEqual(ctx.exception.code, 1)

    def test_high_risk_profile_requires_explicit_risk_flag(self) -> None:
        state = pipeline._new_state("TMP-HIGH-RISK-NO-FLAG", "IMP", "bad high risk profile")
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "step_plan.xml"
            report.write_text(
                """
<decomposition_audit>
  <total_functions_identified>1</total_functions_identified>
  <micro_task_count>1</micro_task_count>
  <grep_executions>1</grep_executions>
  <split_decision>single risky task</split_decision>
  <audit_result>SINGLE_TASK_OK</audit_result>
</decomposition_audit>
<step_plan>
  <pipeline_id>TMP-HIGH-RISK-NO-FLAG</pipeline_id>
  <anti_gaming_read>true</anti_gaming_read>
""" + _design_confirmation_xml() + _task_complexity_xml("HIGH_RISK", functions=1) + """
  <micro_tasks>
    <micro_task id="MT-1">
      <affected_function>pipeline.update</affected_function>
      <target_files><file>pipeline.py</file></target_files>
      <grep_evidence><pattern>def update</pattern><match_count>1</match_count><executed>true</executed></grep_evidence>
      <change_summary>Risky protocol update</change_summary>
    </micro_task>
  </micro_tasks>
</step_plan>
""",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as ctx:
                pipeline._validate_pm_step_plan_file(str(report), state)
        self.assertEqual(ctx.exception.code, 1)

    def test_high_risk_profile_records_conservative_mode(self) -> None:
        state = pipeline._new_state("TMP-HIGH-RISK", "IMP", "protocol update")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "step_plan.xml"
            report.write_text(
                """
<decomposition_audit>
  <total_functions_identified>1</total_functions_identified>
  <micro_task_count>1</micro_task_count>
  <grep_executions>1</grep_executions>
  <split_decision>single risky task</split_decision>
  <audit_result>SINGLE_TASK_OK</audit_result>
</decomposition_audit>
<step_plan>
  <pipeline_id>TMP-HIGH-RISK</pipeline_id>
  <anti_gaming_read>true</anti_gaming_read>
""" + _design_confirmation_xml() + _task_complexity_xml(
                    "HIGH_RISK",
                    functions=1,
                    risk_overrides={"pipeline_protocol": True},
                ) + """
  <micro_tasks>
    <micro_task id="MT-1">
      <affected_function>pipeline.update</affected_function>
      <target_files><file>pipeline.py</file></target_files>
      <grep_evidence><pattern>def update</pattern><match_count>1</match_count><executed>true</executed></grep_evidence>
      <change_summary>Risky protocol update</change_summary>
    </micro_task>
  </micro_tasks>
</step_plan>
""",
                encoding="utf-8",
            )
            planner_run_id, manager_run_id, manager_report = _install_pm_split_runs(state, report, root)
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
                planner_run_id=planner_run_id,
                manager_run_id=manager_run_id,
                manager_report=str(manager_report),
            )
            with mock.patch.object(pipeline, "BASE_DIR", root), \
                 mock.patch.object(pipeline, "_load_branch_state", return_value=state), \
                 mock.patch.object(pipeline, "_save_state_for"), \
                 mock.patch.object(pipeline, "_record_snapshot"):
                pipeline.cmd_done(args)

        profile = state["execution_profile"]
        self.assertEqual(profile["mode"], "HIGH_RISK")
        self.assertEqual(profile["repair_mode"], "conservative")
        self.assertEqual(profile["phase_ci_mode"], "per_phase")
        self.assertTrue(profile["risk_review_required"])
        self.assertEqual(profile["risk_categories"], ["pipeline_protocol"])

    def test_fast_analysis_blocks_product_code_scope_manifest(self) -> None:
        state = pipeline._new_state("TMP-FAST-SCOPE", "IMP", "analysis only")
        state["execution_profile"] = pipeline._new_execution_profile("FAST_ANALYSIS")
        state["execution_profile"]["product_code_write_allowed"] = False
        state["atomic_plan"] = {
            "micro_tasks": [
                {
                    "id": "MT-1",
                    "affected_function": "main.run",
                    "target_files": ["main.py"],
                }
            ],
            "project_snapshot": {"files": {}, "skipped": []},
        }
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "scope_manifest.json"
            manifest.write_text(
                json.dumps({
                    "pipeline_id": "TMP-FAST-SCOPE",
                    "micro_tasks": [
                        {
                            "id": "MT-1",
                            "files": ["main.py"],
                            "affected_functions": ["main.run"],
                        }
                    ],
                }),
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as ctx:
                pipeline._validate_dev_scope_manifest(str(manifest), state, "main.py")
        self.assertEqual(ctx.exception.code, 1)

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
        _mark_phase_attestation_passed(state, "pm")
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
            _mark_module_gates_passed(state)
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
                agent_run_id=None,
            )
            args.agent_run_id = _install_completed_agent_run(state, "dev", Path(args.report_file), root)
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

    def test_dev_done_requires_scope_manifest_with_mandatory_gates(self) -> None:
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

    def test_dev_done_blocks_until_incremental_module_gates_pass(self) -> None:
        state = pipeline._new_state("TMP-MODULE-BLOCK", "FEAT", "sample")
        state["current_phase"] = "dev"
        state["phases"]["pm"]["status"] = "DONE"
        _mark_phase_attestation_passed(state, "pm")
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
                }],
            }
            pipeline._init_module_gates_from_atomic_plan(state)
            manifest = root / "scope_manifest.json"
            target_json = str(target).replace("\\", "\\\\")
            manifest.write_text(
                f"""{{
  "pipeline_id": "TMP-MODULE-BLOCK",
  "micro_tasks": [
    {{"id": "MT-1", "files": ["{target_json}"], "affected_functions": ["main.run"]}}
  ]
}}
""",
                encoding="utf-8",
            )
            handover = _write_dev_handover_report(tmp)
            run_id = _install_completed_agent_run(state, "dev", handover, root)
            args = argparse.Namespace(
                branch=None,
                phase="dev",
                files=str(target),
                report_file=str(handover),
                scope_declared=True,
                scope_manifest=str(manifest),
                agent_run_id=run_id,
            )
            with mock.patch.object(pipeline, "_load_branch_state", return_value=state):
                with self.assertRaises(SystemExit) as ctx:
                    pipeline.cmd_done(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(state["phases"]["dev"]["status"], "PENDING")

    def test_incremental_module_flow_records_checkpoint_and_integration(self) -> None:
        state = pipeline._new_state("TMP-MODULE-FLOW", "FEAT", "sample")
        state["current_phase"] = "dev"
        state["phases"]["pm"]["status"] = "DONE"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "main.py"
            target.write_text("print('module ok')\n", encoding="utf-8")
            state["atomic_plan"] = {
                "project_snapshot": {},
                "micro_tasks": [{
                    "id": "MT-1",
                    "affected_function": "main.run",
                    "target_files": [str(target)],
                }],
            }
            pipeline._init_module_gates_from_atomic_plan(state)
            manifest = root / "module_scope_manifest.json"
            target_json = str(target).replace("\\", "\\\\")
            manifest.write_text(
                f"""{{
  "pipeline_id": "TMP-MODULE-FLOW",
  "micro_tasks": [
    {{"id": "MT-1", "files": ["{target_json}"], "affected_functions": ["main.run"]}}
  ]
}}
""",
                encoding="utf-8",
            )
            with mock.patch.object(pipeline, "_require_state", return_value=state), \
                 mock.patch.object(pipeline, "_save"):
                pipeline.cmd_module(argparse.Namespace(
                    module_action="design",
                    mt_id="MT-1",
                    report_file=str(_write_module_design(tmp)),
                ))
                pipeline.cmd_module(argparse.Namespace(
                    module_action="dev",
                    mt_id="MT-1",
                    files=str(target),
                    report_file=str(_write_module_handover(tmp)),
                    scope_manifest=str(manifest),
                ))
                with self.assertRaises(SystemExit) as qa_exit:
                    pipeline.cmd_module(argparse.Namespace(
                        module_action="qa",
                        mt_id="MT-1",
                        result="PASS",
                        report_file=str(_write_module_qa(tmp)),
                    ))
                with self.assertRaises(SystemExit) as integration_exit:
                    pipeline.cmd_module(argparse.Namespace(
                        module_action="integrate",
                        result="PASS",
                        report_file=str(_write_integration_report(tmp)),
                    ))

        self.assertEqual(qa_exit.exception.code, 0)
        self.assertEqual(integration_exit.exception.code, 0)
        self.assertEqual(state["module_gates"]["modules"]["MT-1"]["status"], "PASS")
        self.assertIsInstance(state["module_gates"]["modules"]["MT-1"]["checkpoint"], dict)
        self.assertEqual(state["module_gates"]["integration"]["status"], "PASS")

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
        _mark_phase_attestation_passed(state, "pm", "dev")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = _write_qa_report(tmp, verdict="PASS", score=120)
            args = argparse.Namespace(
                result="PASS",
                numeric_score="120",
                failure_sig=None,
                agent_id="qa-agent",
                report_file=str(report),
                branch=None,
                agent_run_id=_install_completed_agent_run(state, "qa", report, root),
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
            oracle_input = _write_oracle_file(root, "kind/normal_input.json")
            oracle_expected = _write_oracle_file(root, "kind/normal_expected.json")
            for path in (actual, expected):
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

            with mock.patch.object(pipeline, "BASE_DIR", root):
                audit = pipeline._audit_contract_bundle(contract, test_set, paths)

        self.assertEqual(audit["status"], "FAIL")
        self.assertTrue(any("edge/exception/error oracle" in item for item in audit["blockers"]))

    def test_contract_audit_blocks_empty_oracle_expected_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual.json"
            expected = root / "expected.json"
            normal_input = _write_oracle_file(root, "empty/normal_input.json")
            normal_expected = _write_oracle_file(root, "empty/normal_expected.json")
            edge_input = _write_oracle_file(root, "empty/edge_input.json")
            edge_expected = root / "tests" / "oracles" / "empty" / "edge_expected.json"
            for path in (actual, expected):
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

            with mock.patch.object(pipeline, "BASE_DIR", root):
                audit = pipeline._audit_contract_bundle(contract, test_set, paths)

        self.assertEqual(audit["status"], "FAIL")
        self.assertTrue(any("expected JSON is empty" in item for item in audit["blockers"]))

    def test_contract_audit_requires_user_oracle_source_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual.json"
            expected = root / "expected.json"
            oracle_input = _write_oracle_file(root, "source/oracle_input.json")
            oracle_expected = _write_oracle_file(root, "source/oracle_expected.json")
            for path in (actual, expected):
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

            with mock.patch.object(pipeline, "BASE_DIR", root):
                audit = pipeline._audit_contract_bundle(contract, test_set, paths)

        self.assertEqual(audit["status"], "FAIL")
        self.assertTrue(any("source must be user" in item for item in audit["blockers"]))
        self.assertTrue(any("input_sha256 is required" in item for item in audit["blockers"]))
        self.assertTrue(any("expected_sha256 is required" in item for item in audit["blockers"]))

    def test_contract_audit_requires_oracle_files_under_codeowned_oracle_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual.json"
            expected = root / "expected.json"
            oracle_input = root / "loose_oracle_input.json"
            oracle_expected = root / "loose_oracle_expected.json"
            for path in (actual, expected, oracle_input, oracle_expected):
                path.write_text('{"value": 1}\n', encoding="utf-8")

            contract = build_initial_contract("TMP-ORACLE-ROOT", "sample task")
            contract["definition_of_ready"]["min_edge_cases_total"] = 0
            contract["modules"].append({
                "id": "M1",
                "name": "Parser",
                "inputs": [],
                "outputs": [],
                "acceptance_rules": [],
                "exceptions": [],
            })
            test_set = build_initial_test_set("TMP-ORACLE-ROOT")
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
                "pipeline_id": "TMP-ORACLE-ROOT",
                "oracles": [
                    {
                        "name": "O1",
                        "source": "user",
                        "case_kind": "normal",
                        "input_path": str(oracle_input),
                        "expected_path": str(oracle_expected),
                        "input_sha256": pipeline._sha256_file(oracle_input),
                        "expected_sha256": pipeline._sha256_file(oracle_expected),
                    },
                    {
                        "name": "O2",
                        "source": "user",
                        "case_kind": "edge",
                        "input_path": str(oracle_input),
                        "expected_path": str(oracle_expected),
                        "input_sha256": pipeline._sha256_file(oracle_input),
                        "expected_sha256": pipeline._sha256_file(oracle_expected),
                    },
                ],
            }
            paths = {"oracle_manifest": root / "oracle_manifest.json"}
            pipeline._write_json(paths["oracle_manifest"], oracle_manifest)

            with mock.patch.object(pipeline, "BASE_DIR", root):
                audit = pipeline._audit_contract_bundle(contract, test_set, paths)

        self.assertEqual(audit["status"], "FAIL")
        self.assertTrue(any("tests/oracles" in item for item in audit["blockers"]))

    def test_oracle_waiver_does_not_cover_weak_oracle_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual.json"
            expected = root / "expected.json"
            oracle_input = _write_oracle_file(root, "waiver/oracle_input.json")
            oracle_expected = root / "tests" / "oracles" / "waiver" / "oracle_expected.json"
            for path in (actual, expected):
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

            with mock.patch.object(pipeline, "BASE_DIR", root):
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
        _mark_phase_attestation_passed(state, "pm", "dev", "qa", "build")

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
            args = argparse.Namespace(gates_action="oracle", user_confirmed=False)
            with mock.patch.object(pipeline, "_require_state", return_value=state):
                with mock.patch.object(pipeline, "_contract_paths", return_value=paths):
                    with mock.patch.object(pipeline, "_save"):
                        pipeline.cmd_gates(args)

        self.assertEqual(state["external_gates"]["oracle"]["status"], "PASS")
        self.assertEqual(state["external_gates"]["oracle"]["evidence"], "oracle_waived_by_user")

    def test_active_architect_docs_are_external_gate_based(self) -> None:
        architect = Path(".claude/agents/prompt-architect-agent.md").read_text(encoding="utf-8")
        agents = Path(".claude/commands/agents.md").read_text(encoding="utf-8")
        arch_section = agents.split("## [ARCHITECT] — prompt-architect-agent", 1)[1]

        for text in (architect, arch_section):
            self.assertIn("External Gate RCA", text)
            self.assertIn("protocol_evolution_decision", text)
            self.assertNotIn("Harness-Driven Optimization Loop", text)
            self.assertNotIn("유효한 `test_results.jsonl` 로그", text)

    def test_global_wiki_pipeline_loop_has_no_numeric_harness_completion(self) -> None:
        wiki = Path(".claude/agents/shared/Global_Wiki.md").read_text(encoding="utf-8")
        self.assertIn("External Gates", wiki)
        self.assertIn(
            "done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id",
            wiki,
        )
        self.assertIn("--evidence <실제-결과물-경로-또는-첨부파일>", wiki)
        self.assertNotIn("QA numeric_score + BUILD 합산 채점", wiki)
        self.assertNotIn("Phase 7 재채점 의무", wiki)

    def test_agent_docs_match_mandatory_receipt_and_external_gate_flow(self) -> None:
        pm = Path(".claude/agents/pm-agent.md").read_text(encoding="utf-8")
        planner = Path(".claude/agents/pm-planner-agent.md").read_text(encoding="utf-8")
        manager = Path(".claude/agents/pipeline-manager-agent.md").read_text(encoding="utf-8")
        qa = Path(".claude/agents/qa-agent.md").read_text(encoding="utf-8")
        build = Path(".claude/agents/build-agent.md").read_text(encoding="utf-8")
        harness = Path(".claude/agents/test-harness-agent.md").read_text(encoding="utf-8")
        security = Path(".claude/agents/security-agent.md").read_text(encoding="utf-8")
        architect = Path(".claude/agents/prompt-architect-agent.md").read_text(encoding="utf-8")
        protocol = Path(".claude/agents/protocol-evolution-agent.md").read_text(encoding="utf-8")
        power_automate = Path(".claude/agents/power-automate-agent.md").read_text(encoding="utf-8")
        tech_tree = Path(".claude/agents/shared/tech_tree_examples.md").read_text(encoding="utf-8")
        agents_command = Path(".claude/commands/agents.md").read_text(encoding="utf-8")
        claude = Path("CLAUDE.md").read_text(encoding="utf-8")
        readme = Path("README.md").read_text(encoding="utf-8")
        pipeline_text = Path("pipeline.py").read_text(encoding="utf-8")

        self.assertIn("contract audit", pm)
        self.assertIn("--planner-run-id <planner_run_id>", pm)
        self.assertIn("--manager-run-id <manager_run_id>", pm)
        self.assertIn("--manager-report manager_handoff.xml", pm)
        self.assertIn("<anti_gaming_read>true</anti_gaming_read>", planner)
        self.assertIn("anti_gaming_rules.md", planner)
        self.assertIn("RECURRING", manager)
        self.assertIn("Do not start a Security agent receipt", manager)
        self.assertIn("gates phase-ci --phase pm", pm)
        self.assertIn("--agent-run-id <dev_run_id>", pm)
        self.assertIn("--agent-run-id <qa_run_id>", qa)
        self.assertIn("PASS(≥96/120)", qa)
        self.assertIn("FAIL(&lt;96/120)", qa)
        self.assertIn("--agent-run-id <build_run_id>", build)
        self.assertIn("test-harness-agent는 진단만 수행", build)
        self.assertNotIn("test-harness-agent 채점 필수", build)
        self.assertIn("there is no", security)
        self.assertIn("`--agent-run-id` argument", security)
        self.assertIn("pipeline.py sec --skip", security)
        self.assertIn("planner receipt, manager receipt", architect)
        self.assertIn("pm-planner-agent.md", protocol)
        self.assertIn("pipeline-manager-agent.md", protocol)
        self.assertIn("실제 결과물 경로 또는 첨부파일 링크", harness)
        self.assertIn("사용자가 실제로 확인할 항목", harness)
        self.assertIn("PA micro-task 순서", power_automate)
        self.assertIn("병렬 실행하지 않습니다", power_automate)
        self.assertIn("병렬 실행하지 않고 PM이 정한 `MT-N` 순서", tech_tree)
        self.assertIn("세션 언어 규칙", claude)
        self.assertIn("호출 주체", claude)
        self.assertIn("Pipeline Manager 기록 단계", claude)
        self.assertIn("Check latest status", claude)
        self.assertIn("--evidence <실제-결과물-경로-또는-첨부파일>", agents_command)
        self.assertNotIn("<real-result-path>", agents_command)
        self.assertIn("Phase 7: External Gate Phase", claude)
        self.assertIn("<design_confirmation>", pm)
        self.assertIn("PM DESIGN GATE", pipeline_text)
        self.assertIn("low_value_questions_filtered", pm)
        self.assertIn("maintainability_first", pm)
        self.assertIn("P2/internal implementation preferences must be filtered", pipeline_text)
        self.assertIn("agent start --phase pm_planner", pipeline_text)
        self.assertIn("agent start --phase pipeline_manager", pipeline_text)
        self.assertIn("세션 언어 규칙", pipeline_text)
        self.assertIn("최신 상태 확인", pipeline_text)
        self.assertIn("done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id", pipeline_text)
        self.assertIn("scope_manifest.json --agent-run-id <dev_run_id>", pipeline_text)
        self.assertIn("dist/build_report.xml --agent-run-id <build_run_id>", pipeline_text)
        self.assertIn("--manager-run-id <manager_run_id>", readme)
        self.assertIn("--scope-manifest scope_manifest.json --agent-run-id <dev_run_id>", readme)
        active_docs = "\n".join([pm, planner, manager, qa, build, harness, security, architect, protocol, power_automate, tech_tree, agents_command, claude, readme, pipeline_text])
        self.assertIn("Phase 6→7 자동 진행", claude)
        self.assertIn("사용자에게 묻는 지점은 마지막", pm)
        self.assertNotIn("gates oracle --user-confirmed", active_docs)
        self.assertNotIn('skip-reason "meta-task" --user-confirmed', active_docs)
        self.assertNotIn('skip-reason "docs-only" --user-confirmed', active_docs)
        self.assertNotIn("병렬 또는 직후", active_docs)
        self.assertNotIn("Dev DONE 기록 이후 QA로 직행", active_docs)

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

    def test_output_registry_copies_file_and_writes_manifest(self) -> None:
        state = pipeline._new_state("TMP-OUTPUTS", "IMP", "visible result")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "report.md"
            source.write_text("# 최종 보고서\n", encoding="utf-8")
            outputs_root = root / "pipeline_outputs"
            with mock.patch.object(pipeline, "BASE_DIR", root), \
                 mock.patch.object(pipeline, "OUTPUTS_ROOT", outputs_root):
                item = pipeline._register_output_item(
                    state,
                    kind="report",
                    path=str(source),
                    label="최종-보고서",
                    notes="사용자 확인용",
                )
                manifest = outputs_root / "TMP-OUTPUTS" / "outputs_manifest.json"
                copied = root / item["public_path"]
                self.assertTrue(copied.exists())
                self.assertTrue(manifest.exists())
                self.assertEqual(copied.read_text(encoding="utf-8"), "# 최종 보고서\n")
                self.assertEqual(state["outputs"]["items"][0]["label"], "최종-보고서")

    def test_gitignore_keeps_registered_outputs_trackable(self) -> None:
        text = (Path(__file__).resolve().parent / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("!pipeline_outputs/", text)
        self.assertIn("!pipeline_outputs/**", text)
        self.assertLess(text.index("*.zip"), text.index("!pipeline_outputs/**"))

    def test_failure_packet_records_attempts_and_owner(self) -> None:
        state = pipeline._new_state("TMP-FAIL-PACKET", "IMP", "gate failure")
        report = {
            "status": "FAIL",
            "checks": [
                {"name": "ruff", "status": "FAIL", "message": "unused import"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(pipeline, "CONTRACTS_DIR", root / "pipeline_contracts"):
                first = pipeline._record_failure_packet(state, "technical", report, command="python pipeline.py gates technical")
                second = pipeline._record_failure_packet(state, "technical", report, command="python pipeline.py gates technical")
                first_path = Path(first["packet_path"])
                second_path = Path(second["packet_path"])
                self.assertTrue(first_path.exists())
                self.assertTrue(second_path.exists())
                self.assertTrue(first_path.name.endswith("attempt_1.json"))
                self.assertTrue(second_path.name.endswith("attempt_2.json"))
                self.assertEqual(first["repair_owner"], "Dev tooling repair")
                self.assertEqual(len(state["failure_packets"]), 2)

    def test_outputs_command_is_registered(self) -> None:
        parser = pipeline.build_parser()
        args = parser.parse_args(["outputs", "add", "--kind", "report", "--path", "report.md", "--label", "최종 보고서"])

        self.assertEqual(args.command, "outputs")
        self.assertEqual(args.outputs_action, "add")
        self.assertIs(pipeline.COMMAND_MAP["outputs"], pipeline.cmd_outputs)


if __name__ == "__main__":
    unittest.main()
