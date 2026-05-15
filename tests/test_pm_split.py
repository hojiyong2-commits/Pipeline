"""
tests/test_pm_split.py
----------------------
IMP-20260515-020F: PM split (pm_planner + pipeline_manager) 단위 테스트

검증 항목:
  - agent start --phase pm_planner 성공
  - agent start --phase pipeline_manager 성공
  - legacy agent start --phase pm 호환 동작
  - done --phase pm 새 플로우(planner+manager run_id 필수)
  - done --phase pm 레거시 플로우(pm agent_run_id만 허용)
"""
import argparse
import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pipeline


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _minimal_step_plan_xml(pipeline_id: str = "TMP-PM-SPLIT-TEST") -> str:
    return f"""
<decomposition_audit>
  <total_functions_identified>1</total_functions_identified>
  <micro_task_count>1</micro_task_count>
  <grep_executions>1</grep_executions>
  <split_decision>single function</split_decision>
  <audit_result>SINGLE_TASK_OK</audit_result>
</decomposition_audit>
<step_plan>
  <pipeline_id>{pipeline_id}</pipeline_id>
  <task_complexity>
    <execution_profile>STANDARD</execution_profile>
    <reason>테스트용 단순 계획</reason>
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
    <filter_summary>내부 변수명과 코드 취향 질문은 묻지 않고 기존 패턴과 유지보수성을 우선했습니다.</filter_summary>
    <decision_questions>
      <question id="DQ-1" priority="P1" category="module_split" mt_id="MT-1">
        <user_facing_question>이번 작업은 MT-1 단위로 작게 나눠 진행해도 될까요?</user_facing_question>
        <evidence>사용자 요청과 Grep 결과 변경 범위가 이 모듈에 모였습니다.</evidence>
        <why_it_matters>모듈 단위가 맞아야 수정 범위가 작고 나중에 유지보수가 쉬워집니다.</why_it_matters>
        <recommended_option>A</recommended_option>
        <options>
          <option id="A">
            <label>MT-1 단위로 진행</label>
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
"""


def _install_agent_run(state: dict, phase: str, output_file: Path, root: Path) -> str:
    """완료된 agent run receipt를 state에 설치하고 run_id 반환."""
    run_id = f"{phase}-split-test-run"
    receipt_dir = root / "agent-receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    output_file_resolved = output_file.resolve()
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
        "output_file": str(output_file_resolved),
        "output_sha256": pipeline._sha256_file(output_file_resolved),
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
        "output_file": str(output_file_resolved),
        "output_sha256": pipeline._sha256_file(output_file_resolved),
        "evidence_files": [],
        "receipt_path": str(receipt_path.resolve()),
        "receipt_sha256": pipeline._sha256_file(receipt_path),
        "used_by_phase": None,
        "used_at": None,
        "commit_sha": "a" * 40,
    }
    return run_id


# ---------------------------------------------------------------------------
# Item 5-A: agent start --phase pm_planner 성공
# ---------------------------------------------------------------------------

def test_pm_planner_phase_starts() -> None:
    """agent start --phase pm_planner가 성공적으로 run_id와 token을 발행해야 한다."""
    state = pipeline._new_state("TMP-PM-PLANNER-START", "IMP", "테스트")
    state["phase_attestations"] = pipeline._new_phase_attestations(enabled=True)

    # _agent_run_start를 직접 호출하여 검증
    run, token = pipeline._agent_run_start(state, "pm_planner", None)

    assert run["phase"] == "pm_planner"
    assert run["agent_id"] == "pm-planner-agent"
    assert run["status"] == "RUNNING"
    assert token and len(token) > 10, "token이 발행되어야 한다"

    # state에도 등록됐는지 확인
    runs = state.get("agent_runs", {})
    assert run["run_id"] in runs


# ---------------------------------------------------------------------------
# Item 5-B: agent start --phase pipeline_manager 성공
# ---------------------------------------------------------------------------

def test_pipeline_manager_phase_starts() -> None:
    """agent start --phase pipeline_manager가 성공적으로 run_id와 token을 발행해야 한다."""
    state = pipeline._new_state("TMP-PM-MANAGER-START", "IMP", "테스트")
    state["phase_attestations"] = pipeline._new_phase_attestations(enabled=True)

    run, token = pipeline._agent_run_start(state, "pipeline_manager", None)

    assert run["phase"] == "pipeline_manager"
    assert run["agent_id"] == "pipeline-manager-agent"
    assert run["status"] == "RUNNING"
    assert token and len(token) > 10

    runs = state.get("agent_runs", {})
    assert run["run_id"] in runs


# ---------------------------------------------------------------------------
# Item 5-C: PM split 이후 agent start --phase pm 거부 검증
# ---------------------------------------------------------------------------

def test_legacy_pm_phase_still_works() -> None:
    """PM split 이후 agent start --phase pm은 거부돼야 한다.

    pm_planner와 pipeline_manager를 별도로 사용해야 하며,
    레거시 'pm' 페이즈 직접 사용은 AGENT_RUN_PHASES에서 제거됐다.
    """
    state = pipeline._new_state("TMP-PM-LEGACY", "IMP", "테스트")
    state["phase_attestations"] = pipeline._new_phase_attestations(enabled=True)

    # 'pm' 페이즈는 AGENT_RUN_PHASES에 없으므로 SystemExit이 발생해야 함
    with pytest.raises(SystemExit) as exc_info:
        pipeline._agent_run_start(state, "pm", None)
    # exit code 2 = AGENT_RUN_PHASES 위반
    assert exc_info.value.code == 2, (
        f"pm 페이즈 거부 시 exit code 2여야 한다, got {exc_info.value.code}"
    )


# ---------------------------------------------------------------------------
# Item 5-D: done --phase pm 새 플로우 (planner+manager run_id 필수)
# ---------------------------------------------------------------------------

def test_done_pm_requires_planner_and_manager_run_id() -> None:
    """새 state에서 done --phase pm은 planner_run_id와 manager_run_id 모두 필요하다."""
    pipeline_id = "TMP-PM-SPLIT-DONE"
    state = pipeline._new_state(pipeline_id, "IMP", "테스트")
    state["phase_attestations"] = pipeline._new_phase_attestations(enabled=True)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        step_plan = root / "step_plan.xml"
        step_plan.write_text(_minimal_step_plan_xml(pipeline_id), encoding="utf-8")
        planner_run_id = _install_agent_run(state, "pm_planner", step_plan, root)

        step_plan_sha = pipeline._sha256_file(step_plan)
        manager_report = root / "manager_handoff.xml"
        manager_report.write_text(
            f"<manager_handoff>"
            f"<pipeline_id>{pipeline_id}</pipeline_id>"
            f"<from>pipeline-manager-agent</from>"
            f"<step_plan_sha256>{step_plan_sha}</step_plan_sha256>"
            f"<planner_run_id>{planner_run_id}</planner_run_id>"
            f"<accepted_for_execution>true</accepted_for_execution>"
            f"<will_not_modify_step_plan>true</will_not_modify_step_plan>"
            f"<next_phase>dev</next_phase>"
            f"</manager_handoff>",
            encoding="utf-8",
        )

        manager_run_id = _install_agent_run(state, "pipeline_manager", manager_report, root)

        args = argparse.Namespace(
            branch=None,
            phase="pm",
            decomp=True,
            clarification=True,
            roadmap=True,
            judgment_confirmed=False,
            report_file=str(step_plan),
            files=None,
            planner_run_id=planner_run_id,
            manager_run_id=manager_run_id,
            manager_report=str(manager_report),
            agent_run_id=None,
        )
        with mock.patch.object(pipeline, "_load_branch_state", return_value=state), \
             mock.patch.object(pipeline, "_save_state_for"), \
             mock.patch.object(pipeline, "_record_snapshot"):
            pipeline.cmd_done(args)

    assert state["phases"]["pm"]["status"] == "DONE"
    # planner_run은 agent_run_id로 저장됨 (pm phase의 primary run)
    assert state["phases"]["pm"]["agent_run_id"] == planner_run_id
    # manager_run_id는 별도 키로 저장됨
    assert state["phases"]["pm"]["manager_run_id"] == manager_run_id


# ---------------------------------------------------------------------------
# Item 5-E: done --phase pm 에서 --agent-run-id 거부 검증 (PM split 이후)
# ---------------------------------------------------------------------------

def test_done_pm_legacy_accepts_pm_run_id() -> None:
    """PM split 이후 done --phase pm에 --agent-run-id만 전달하면 거부돼야 한다.

    PM split 이후 done --phase pm은 반드시 --planner-run-id와 --manager-run-id를 요구한다.
    --agent-run-id는 [PM SPLIT GATE]에 의해 거부된다.
    """
    pipeline_id = "TMP-PM-LEGACY-DONE"
    state = pipeline._new_state(pipeline_id, "IMP", "테스트")
    state["phase_attestations"] = pipeline._new_phase_attestations(enabled=True)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        step_plan = root / "step_plan.xml"
        step_plan.write_text(_minimal_step_plan_xml(pipeline_id), encoding="utf-8")

        # pm_planner로 receipt 생성 (PHASE_AGENT_IDS["pm_planner"] 사용)
        run_id = _install_agent_run(state, "pm_planner", step_plan, root)

        args = argparse.Namespace(
            branch=None,
            phase="pm",
            decomp=True,
            clarification=True,
            roadmap=True,
            judgment_confirmed=False,
            report_file=str(step_plan),
            files=None,
            planner_run_id=None,
            manager_run_id=None,
            manager_report=None,
            agent_run_id=run_id,  # --agent-run-id만 전달: PM SPLIT GATE가 거부해야 함
        )
        with mock.patch.object(pipeline, "_load_branch_state", return_value=state), \
             mock.patch.object(pipeline, "_save_state_for"), \
             mock.patch.object(pipeline, "_record_snapshot"):
            # [PM SPLIT GATE]가 --agent-run-id를 거부 → SystemExit 1
            with pytest.raises(SystemExit) as exc_info:
                pipeline.cmd_done(args)
            assert exc_info.value.code == 1, (
                f"PM SPLIT GATE 거부 시 exit code 1이어야 한다, got {exc_info.value.code}"
            )
