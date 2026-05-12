import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def _copy_cli_workspace(tmp_path: Path) -> Path:
    work = tmp_path / "workspace"
    work.mkdir()
    shutil.copy2(PROJECT_DIR / "pipeline.py", work / "pipeline.py")
    shutil.copytree(
        PROJECT_DIR / "core",
        work / "core",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return work


def _run(work: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIPELINE_NO_DASHBOARD"] = "1"
    env["PIPELINE_DEPLOY_ROOT"] = str(work / "deploy")
    return subprocess.run(
        [sys.executable, "pipeline.py", *args],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _ok(work: Path, *args: str) -> subprocess.CompletedProcess:
    result = _run(work, *args)
    assert result.returncode == 0, (
        f"command failed: {' '.join(args)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert "Traceback" not in (result.stdout + result.stderr)
    return result


def _state(work: Path) -> dict:
    return json.loads((work / "pipeline_state.json").read_text(encoding="utf-8"))


def _write_state(work: Path, state: dict) -> None:
    (work / "pipeline_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _json_from_stdout(stdout: str) -> dict:
    start = stdout.find("{")
    assert start >= 0, stdout
    return json.loads(stdout[start:])


def _design_confirmation_xml(mt_id: str = "MT-1") -> str:
    return f"""  <design_confirmation>
    <module_split_presented>true</module_split_presented>
    <module_split_user_confirmed>true</module_split_user_confirmed>
    <maintenance_priority>maintainability_first</maintenance_priority>
    <low_value_questions_filtered>true</low_value_questions_filtered>
    <filter_summary>내부 구현 취향 질문은 묻지 않고 유지보수성 기준으로 정리했습니다.</filter_summary>
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


def _task_complexity_xml(profile: str = "STANDARD") -> str:
    return f"""  <task_complexity>
    <execution_profile>{profile}</execution_profile>
    <reason>CLI E2E smoke test profile</reason>
    <uncertainty>
      <p0_questions>0</p0_questions>
      <p1_questions>1</p1_questions>
      <output_format_clear>true</output_format_clear>
    </uncertainty>
    <blast_radius>
      <expected_changed_files>1</expected_changed_files>
      <expected_changed_functions>1</expected_changed_functions>
      <expected_changed_lines>40</expected_changed_lines>
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
"""


def _agent_run(work: Path, phase: str, output_file: Path, evidence: str | None = None) -> str:
    started = _ok(work, "agent", "start", "--phase", phase)
    payload = _json_from_stdout(started.stdout)
    run_id = payload["run"]["run_id"]
    token = payload["token"]
    args = ["agent", "finish", "--run-id", run_id, "--token", token, "--output-file", str(output_file)]
    if evidence:
        args.extend(["--evidence", evidence])
    _ok(work, *args)
    return run_id


def _manager_handoff(work: Path, pid: str, step_plan: Path, planner_run_id: str) -> Path:
    digest = __import__("hashlib").sha256(step_plan.read_bytes()).hexdigest()
    handoff = work / "manager_handoff.xml"
    handoff.write_text(
        f"""<manager_handoff>
  <pipeline_id>{pid}</pipeline_id>
  <from>pipeline-manager-agent</from>
  <step_plan_sha256>{digest}</step_plan_sha256>
  <planner_run_id>{planner_run_id}</planner_run_id>
  <accepted_for_execution>true</accepted_for_execution>
  <will_not_modify_step_plan>true</will_not_modify_step_plan>
  <next_phase>dev</next_phase>
</manager_handoff>
""",
        encoding="utf-8",
    )
    return handoff


def _mark_phase_ci_passed(work: Path, phase: str) -> None:
    state = _state(work)
    phase_state = state.setdefault("phase_attestations", {}).setdefault("phases", {}).setdefault(phase, {})
    phase_state["status"] = "PASS"
    phase_state["phase"] = phase
    phase_state["completed_at"] = "2026-01-01T00:00:00Z"
    _write_state(work, state)


def test_three_gate_cli_e2e_blocks_complete_without_github_ci(tmp_path: Path) -> None:
    work = _copy_cli_workspace(tmp_path)

    _ok(work, "new", "--type", "FEAT", "--desc", "three gate cli smoke", "--no-dashboard")
    pid = _state(work)["pipeline_id"]

    oracle_root = work / "tests" / "oracles" / pid
    oracle_root.mkdir(parents=True, exist_ok=True)
    oracle_input = oracle_root / "normal_input.json"
    oracle_expected = oracle_root / "normal_expected.json"
    oracle_edge_input = oracle_root / "edge_input.json"
    oracle_edge_expected = oracle_root / "edge_expected.json"
    oracle_input.write_text(json.dumps({"name": "Ada"}, ensure_ascii=False), encoding="utf-8")
    oracle_expected.write_text(json.dumps({"greeting": "Hello, Ada"}, ensure_ascii=False), encoding="utf-8")
    oracle_edge_input.write_text(json.dumps({"name": ""}, ensure_ascii=False), encoding="utf-8")
    oracle_edge_expected.write_text(json.dumps({"error": "name is required"}, ensure_ascii=False), encoding="utf-8")

    actual_normal = work / "actual_normal.json"
    expected_normal = work / "expected_normal.json"
    actual_normal.write_text(json.dumps({"greeting": "Hello, Ada"}, ensure_ascii=False), encoding="utf-8")
    expected_normal.write_text(json.dumps({"greeting": "Hello, Ada"}, ensure_ascii=False), encoding="utf-8")

    edge_files = []
    for index in range(1, 4):
        actual = work / f"actual_edge_{index}.json"
        expected = work / f"expected_edge_{index}.json"
        payload = {"edge": index, "ok": True}
        actual.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        expected.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        edge_files.append((actual, expected))

    _ok(work, "contract", "init", "--three-gate")
    _ok(work, "contract", "add-module", "--id", "M1", "--name", "Greeting")
    _ok(
        work,
        "contract",
        "add-oracle",
        "--input",
        str(oracle_input),
        "--expected",
        str(oracle_expected),
        "--case-kind",
        "normal",
        "--description",
        "normal greeting oracle",
    )
    _ok(
        work,
        "contract",
        "add-oracle",
        "--input",
        str(oracle_edge_input),
        "--expected",
        str(oracle_edge_expected),
        "--case-kind",
        "edge",
        "--description",
        "empty name edge oracle",
    )
    _ok(
        work,
        "contract",
        "add-test",
        "--id",
        "T1",
        "--module",
        "M1",
        "--test-type",
        "json_exact_match",
        "--priority",
        "P0",
        "--case-kind",
        "normal",
        "--points",
        "70",
        "--given-json",
        json.dumps({"actual_file": str(actual_normal)}),
        "--then-json",
        json.dumps({"expected_file": str(expected_normal)}),
    )
    for index, (actual, expected) in enumerate(edge_files, start=1):
        _ok(
            work,
            "contract",
            "add-test",
            "--id",
            f"T-EDGE-{index}",
            "--module",
            "M1",
            "--test-type",
            "json_exact_match",
            "--priority",
            "P1",
            "--case-kind",
            "edge",
            "--points",
            "10",
            "--given-json",
            json.dumps({"actual_file": str(actual)}),
            "--then-json",
            json.dumps({"expected_file": str(expected)}),
        )

    _ok(work, "contract", "audit")
    _ok(work, "contract", "ready")
    _ok(work, "contract", "freeze")

    step_plan = work / "step_plan.xml"
    step_plan.write_text(
        f"""
<decomposition_audit>
  <total_functions_identified>1</total_functions_identified>
  <micro_task_count>1</micro_task_count>
  <grep_executions>1</grep_executions>
  <split_decision>single function smoke</split_decision>
  <audit_result>SINGLE_TASK_OK</audit_result>
</decomposition_audit>
<step_plan>
  <pipeline_id>{pid}</pipeline_id>
  <anti_gaming_read>true</anti_gaming_read>
{_design_confirmation_xml()}
{_task_complexity_xml()}
  <micro_tasks>
    <micro_task id="MT-1">
      <affected_function>main.greet</affected_function>
      <target_files><file>main.py</file></target_files>
      <grep_evidence>
        <pattern>def greet</pattern>
        <match_count>1</match_count>
        <executed>true</executed>
      </grep_evidence>
      <change_summary>Create deterministic greeting function</change_summary>
    </micro_task>
  </micro_tasks>
</step_plan>
""",
        encoding="utf-8",
    )
    planner_run_id = _agent_run(work, "pm_planner", step_plan)
    manager_report = _manager_handoff(work, pid, step_plan, planner_run_id)
    manager_run_id = _agent_run(work, "pipeline_manager", manager_report)
    _ok(
        work,
        "done",
        "--phase",
        "pm",
        "--report-file",
        str(step_plan),
        "--decomp",
        "--clarification",
        "--roadmap",
        "--planner-run-id",
        planner_run_id,
        "--manager-run-id",
        manager_run_id,
        "--manager-report",
        str(manager_report),
    )
    _ok(work, "gates", "prepare-phase", "--phase", "pm")
    _mark_phase_ci_passed(work, "pm")

    main_py = work / "main.py"
    main_py.write_text(
        "def greet(name: str) -> dict[str, str]:\n"
        "    return {'greeting': f'Hello, {name}'}\n",
        encoding="utf-8",
    )
    module_design = work / "module_design_MT-1.xml"
    module_design.write_text(
        """<module_design>
  <mt_id>MT-1</mt_id>
  <interface_contract>greet(name) returns a greeting dict</interface_contract>
  <implementation_plan>create the greeting function</implementation_plan>
  <verification_plan>verify module output shape</verification_plan>
</module_design>
""",
        encoding="utf-8",
    )
    _ok(work, "module", "design", "--mt-id", "MT-1", "--report-file", str(module_design))
    scope_manifest = work / "scope_manifest.json"
    scope_manifest.write_text(
        json.dumps(
            {
                "pipeline_id": pid,
                "micro_tasks": [
                    {
                        "id": "MT-1",
                        "files": ["main.py"],
                        "affected_functions": ["main.greet"],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    module_handover = work / "module_handover_MT-1.xml"
    module_handover.write_text(
        """<module_handover>
  <mt_id>MT-1</mt_id>
  <implemented_files><file>main.py</file></implemented_files>
  <self_check>PASS</self_check>
</module_handover>
""",
        encoding="utf-8",
    )
    _ok(
        work,
        "module",
        "dev",
        "--mt-id",
        "MT-1",
        "--files",
        "main.py",
        "--report-file",
        str(module_handover),
        "--scope-manifest",
        str(scope_manifest),
    )
    module_qa = work / "module_qa_MT-1.xml"
    module_qa.write_text(
        """<module_qa_report>
  <mt_id>MT-1</mt_id>
  <verdict>PASS</verdict>
  <verification_evidence>module output verified</verification_evidence>
</module_qa_report>
""",
        encoding="utf-8",
    )
    _ok(work, "module", "qa", "--mt-id", "MT-1", "--result", "PASS", "--report-file", str(module_qa))
    integration_report = work / "integration_report.xml"
    integration_report.write_text(
        """<integration_report>
  <modules_integrated>MT-1</modules_integrated>
  <integration_verdict>PASS</integration_verdict>
</integration_report>
""",
        encoding="utf-8",
    )
    _ok(work, "module", "integrate", "--result", "PASS", "--report-file", str(integration_report))
    dev_handover = work / "dev_handover.xml"
    dev_handover.write_text(
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
    dev_run_id = _agent_run(work, "dev", dev_handover, "main.py")
    _ok(
        work,
        "done",
        "--phase",
        "dev",
        "--files",
        "main.py",
        "--report-file",
        str(dev_handover),
        "--scope-declared",
        "--scope-manifest",
        str(scope_manifest),
        "--agent-run-id",
        dev_run_id,
    )
    _ok(work, "gates", "prepare-phase", "--phase", "dev")
    _mark_phase_ci_passed(work, "dev")
    qa_report = work / "qa_report.xml"
    qa_report.write_text(
        """<qa_report>
  <verdict>PASS</verdict>
  <numeric_score>120</numeric_score>
  <micro_task_boundary>verified</micro_task_boundary>
</qa_report>
""",
        encoding="utf-8",
    )
    qa_run_id = _agent_run(work, "qa", qa_report)
    _ok(
        work,
        "qa",
        "--result",
        "PASS",
        "--numeric-score",
        "120",
        "--report-file",
        str(qa_report),
        "--agent-run-id",
        qa_run_id,
    )
    _ok(work, "gates", "prepare-phase", "--phase", "qa")
    _mark_phase_ci_passed(work, "qa")
    _ok(work, "sec", "--skip")
    build_output = work / "build_agent_output.xml"
    build_output.write_text("<build_report><status>N/A</status></build_report>\n", encoding="utf-8")
    build_run_id = _agent_run(work, "build", build_output)
    _ok(
        work,
        "build",
        "--exe",
        "N/A",
        "--skip-reason",
        "no-code",
        "--agent-run-id",
        build_run_id,
    )
    _ok(work, "gates", "prepare-phase", "--phase", "build")
    _mark_phase_ci_passed(work, "build")

    _ok(work, "gates", "technical")
    technical_result = json.loads(
        (work / "pipeline_contracts" / pid / "gates" / "technical_result.json").read_text(encoding="utf-8")
    )
    assert technical_result["status"] == "PASS"
    assert technical_result["strict_tools"] is True
    assert technical_result["complete_eligible"] is True
    assert {item["name"] for item in technical_result["checks"]} >= {"py_compile", "ruff", "mypy", "bandit", "pytest"}
    _ok(work, "gates", "oracle")
    blocked_accept = _run(work, "gates", "accept", "--result", "ACCEPT", "--evidence", "manual-smoke", "--user-confirmed")
    assert blocked_accept.returncode == 1
    assert "github_ci gate must be PASS" in (blocked_accept.stdout + blocked_accept.stderr)
    architect_report = work / "architect_report.xml"
    architect_report.write_text(
        """<optimization_report>
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
    blocked = _run(work, "architect", "--report-file", str(architect_report))
    assert blocked.returncode == 1
    assert "current_phase" in (blocked.stdout + blocked.stderr)

    final_state = _state(work)
    assert final_state["terminal_state"] is None
    assert final_state["current_phase"] == "harness"
    assert final_state["external_gates"]["technical"]["status"] == "PASS"
    assert final_state["external_gates"]["oracle"]["status"] == "PASS"
    assert final_state["external_gates"]["acceptance"]["status"] == "PENDING"
    assert final_state["external_gates"]["github_ci"]["status"] == "PENDING"
