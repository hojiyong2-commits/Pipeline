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


def test_three_gate_cli_e2e_blocks_complete_without_github_ci(tmp_path: Path) -> None:
    work = _copy_cli_workspace(tmp_path)

    _ok(work, "new", "--type", "FEAT", "--desc", "three gate cli smoke", "--no-dashboard")
    pid = _state(work)["pipeline_id"]

    oracle_input = work / "oracle_input.json"
    oracle_expected = work / "oracle_expected.json"
    oracle_edge_input = work / "oracle_edge_input.json"
    oracle_edge_expected = work / "oracle_edge_expected.json"
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
    _ok(work, "done", "--phase", "pm", "--report-file", str(step_plan), "--decomp", "--clarification", "--roadmap")

    main_py = work / "main.py"
    main_py.write_text(
        "def greet(name: str) -> dict[str, str]:\n"
        "    return {'greeting': f'Hello, {name}'}\n",
        encoding="utf-8",
    )
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
    )
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
    _ok(work, "qa", "--result", "PASS", "--numeric-score", "120", "--report-file", str(qa_report))
    _ok(work, "sec", "--skip")
    _ok(work, "build", "--exe", "N/A", "--skip-reason", "no-code", "--user-confirmed")

    _ok(work, "gates", "technical")
    technical_result = json.loads(
        (work / "pipeline_contracts" / pid / "gates" / "technical_result.json").read_text(encoding="utf-8")
    )
    assert technical_result["status"] == "PASS"
    assert technical_result["strict_tools"] is True
    assert technical_result["complete_eligible"] is True
    assert {item["name"] for item in technical_result["checks"]} >= {"py_compile", "ruff", "mypy", "bandit", "pytest"}
    _ok(work, "gates", "oracle", "--user-confirmed")
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
