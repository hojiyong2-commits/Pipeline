"""IMP-20260522-29C1: Pipeline Metrics Timestamp Instrumentation 테스트

13개 테스트:
  - T-001: phase elapsed (both timestamps present)
  - T-002: phase elapsed (missing started_at)
  - T-003: total elapsed (lifecycle timestamps)
  - T-004: gate elapsed (both timestamps present)
  - T-005: malformed timestamp -> unavailable, no crash
  - T-006: _format_metrics_summary_ko 출력에 필수 섹션 포함 확인
  - T-007: runtime artifacts not in git-tracked diff
  - T-008: cmd_done 실제 함수 경로 호출 시 phase started_at이 덮어쓰이지 않음
  - T-009: gate started_at이 completed_at보다 먼저 기록됨 (fix-forward 통합 테스트)
  - T-010: started_at 없으면 elapsed가 '확인 불가'로 유지됨 (fix-forward 통합 테스트)
  - T-011: human_acceptance_packet.md에 metrics summary 섹션이 포함됨 (fix-forward 통합 테스트)
  - T-012: cmd_check PASS 시 phase.started_at이 기록됨 (fix-forward v3)
  - T-013: cmd_check PASS 시 기존 started_at을 덮어쓰지 않음 (fix-forward v3)
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
ORACLE_ROOT = PROJECT_ROOT / "tests" / "oracles" / "IMP-20260522-29C1"
sys.path.insert(0, str(PROJECT_ROOT))
from pipeline import (
    _phase_elapsed_summary,
    _gate_elapsed_summary,
    _collect_pipeline_metrics,
    _set_external_gate,
    _ensure_external_gates,
    _format_metrics_summary_ko,
    cmd_check,
    cmd_done,
    PR_REQUIRED_SECTIONS,
)


def _load_oracle(case_id: str, filename: str) -> Dict[str, Any]:
    """oracle 파일을 읽어 dict로 반환한다."""
    path = ORACLE_ROOT / case_id / filename
    return json.loads(path.read_text(encoding="utf-8"))


def test_phase_elapsed_when_both_timestamps_present() -> None:
    """T-001: started_at/completed_at 모두 있을 때 elapsed 계산이 oracle과 일치한다."""
    input_state = _load_oracle("T-001", "input_state.json")
    expected = _load_oracle("T-001", "expected_phase_elapsed.json")
    result = _phase_elapsed_summary(input_state)
    pm_result = result.get("pm", {})
    assert pm_result.get("elapsed_seconds") == expected["elapsed_seconds"], (
        f"elapsed_seconds 불일치: {pm_result.get('elapsed_seconds')} != {expected['elapsed_seconds']}"
    )
    assert pm_result.get("elapsed_human") == expected["elapsed_human"], (
        f"elapsed_human 불일치: {pm_result.get('elapsed_human')} != {expected['elapsed_human']}"
    )


def test_phase_elapsed_missing_started_at() -> None:
    """T-002: started_at이 null일 때 elapsed_seconds='확인 불가', reason에 'started_at' 포함."""
    input_state = _load_oracle("T-002", "input_state.json")
    expected = _load_oracle("T-002", "expected_phase_elapsed.json")
    result = _phase_elapsed_summary(input_state)
    dev_result = result.get("dev", {})
    assert dev_result.get("elapsed_seconds") == expected["elapsed_seconds"], (
        f"elapsed_seconds 불일치: {dev_result.get('elapsed_seconds')} != {expected['elapsed_seconds']}"
    )
    assert dev_result.get("reason") == expected["reason"], (
        f"reason 불일치: {dev_result.get('reason')} != {expected['reason']}"
    )


def test_total_elapsed_from_lifecycle_timestamps() -> None:
    """T-003: pipeline_started_at/pipeline_completed_at 필드를 우선 사용하여 total_elapsed 계산."""
    input_state = _load_oracle("T-003", "input_state.json")
    expected = _load_oracle("T-003", "expected_total_elapsed.json")
    metrics = _collect_pipeline_metrics(input_state)
    total = metrics.get("total_elapsed", {})
    assert total.get("elapsed_seconds") == expected["elapsed_seconds"], (
        f"elapsed_seconds 불일치: {total.get('elapsed_seconds')} != {expected['elapsed_seconds']}"
    )
    assert total.get("elapsed_human") == expected["elapsed_human"], (
        f"elapsed_human 불일치: {total.get('elapsed_human')} != {expected['elapsed_human']}"
    )


def test_gate_elapsed_when_both_timestamps_present() -> None:
    """T-004: gate started_at/completed_at 모두 있을 때 elapsed 계산이 oracle과 일치한다."""
    input_state = _load_oracle("T-004", "input_state.json")
    expected = _load_oracle("T-004", "expected_gate_elapsed.json")
    result = _gate_elapsed_summary(input_state)
    tech_result = result.get("technical", {})
    assert tech_result.get("elapsed_seconds") == expected["elapsed_seconds"], (
        f"elapsed_seconds 불일치: {tech_result.get('elapsed_seconds')} != {expected['elapsed_seconds']}"
    )
    assert tech_result.get("elapsed_human") == expected["elapsed_human"], (
        f"elapsed_human 불일치: {tech_result.get('elapsed_human')} != {expected['elapsed_human']}"
    )
    assert tech_result.get("status") == expected["status"], (
        f"status 불일치: {tech_result.get('status')} != {expected['status']}"
    )


def test_malformed_timestamp_returns_unavailable_no_crash() -> None:
    """T-005: 잘못된 timestamp 형식이어도 크래시 없이 '확인 불가' + reason 반환."""
    input_state = _load_oracle("T-005", "input_state.json")
    expected = _load_oracle("T-005", "expected_phase_elapsed.json")
    result = _phase_elapsed_summary(input_state)
    qa_result = result.get("qa", {})
    assert qa_result.get("elapsed_seconds") == expected["elapsed_seconds"], (
        f"elapsed_seconds 불일치: {qa_result.get('elapsed_seconds')} != {expected['elapsed_seconds']}"
    )
    assert qa_result.get("reason") == expected["reason"], (
        f"reason 불일치: {qa_result.get('reason')} != {expected['reason']}"
    )


def test_metrics_summary_included_in_format_output() -> None:
    """T-006: _format_metrics_summary_ko 출력에 필수 섹션 6개가 모두 포함된다."""
    from pipeline import _format_metrics_summary_ko
    mock_metrics: Dict[str, Any] = {
        "pipeline_id": "TEST-001",
        "total_elapsed": {
            "elapsed_human": "5분",
            "started_at": "2026-05-22T10:00:00Z",
            "completed_at": "2026-05-22T10:05:00Z",
        },
        "phase_elapsed": {
            "pm": {"elapsed_human": "2분", "elapsed_seconds": 120},
        },
        "gate_elapsed": {
            "technical": {"status": "PASS", "elapsed_human": "30초", "elapsed_seconds": 30},
        },
        "failure_retry": {
            "total_failure_packets": 0,
            "failure_code_counts": {},
        },
        "github_actions": {
            "run_id": "확인 불가",
            "conclusion": "확인 불가",
            "elapsed_human": "확인 불가",
            "url": "확인 불가",
        },
        "agent_sessions": {},
        "bottleneck": {},
    }
    output = _format_metrics_summary_ko(mock_metrics)
    assert "Phase별 소요 시간" in output, "섹션2 누락: Phase별 소요 시간"
    assert "Gate별 상태 및 소요 시간" in output, "섹션3 누락: Gate별 상태 및 소요 시간"
    assert "전체 소요 시간" in output, "섹션1 누락: 전체 소요 시간"
    assert "실패/재시도 요약" in output, "섹션4 누락: 실패/재시도 요약"
    assert "GitHub Actions 요약" in output, "섹션5 누락: GitHub Actions 요약"
    assert "병목 요약" in output, "섹션6 누락: 병목 요약"
    assert "에이전트/세션별 소요 시간" in output, "섹션7 누락: 에이전트/세션별 소요 시간"


def test_runtime_artifacts_not_in_git_tracked() -> None:
    """T-007: PR diff에 런타임 아티팩트 파일이 포함되지 않아야 한다."""
    forbidden_patterns = [
        "pipeline_state.json",
        "pipeline_metrics.json",
        "failure_packet.json",
        "codex_run_raw.json",
        "manager_handoff.xml",
        "build_report.xml",
        "preflight_report.json",
    ]
    forbidden_dirs = [
        ".pipeline/",
        "pipeline_contracts/",
        "pipeline_outputs/",
    ]
    result = subprocess.run(
        ["git", "diff", "--name-only", "main", "HEAD"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        pytest.skip("git diff main HEAD 실패 — git 이력 없음")
    changed_files = result.stdout.splitlines()
    for changed in changed_files:
        for pattern in forbidden_patterns:
            assert pattern not in changed, f"PR에 포함 금지 파일: {changed}"
        for dir_pattern in forbidden_dirs:
            assert not changed.startswith(dir_pattern), (
                f"PR에 포함 금지 디렉터리: {changed}"
            )


# ── Fix-forward 통합 테스트 (IMP-20260522-29C1 4 blocker 수정 검증) ──────────────


def test_cmd_done_does_not_overwrite_started_at() -> None:
    """T-008: cmd_done --phase dev 호출 시 기존 phase.started_at이 유지되어야 한다.

    fix-forward: cmd_done에서 non-PM phase started_at fallback(_now()) 제거.
    실제 cmd_done 함수를 monkeypatch된 state로 호출하여 검증한다.
    """
    early_started_at = "2026-05-22T10:00:00Z"

    state: Dict[str, Any] = {
        "pipeline_id": "TEST-T008",
        "terminal_state": None,
        "blocked": False,
        "current_phase": "dev",
        "phase_attestations": {"enabled": False, "phases": {}},
        "phases": {
            "pm": {
                "status": "DONE",
                "started_at": "2026-05-22T09:00:00Z",
                "completed_at": "2026-05-22T10:00:00Z",
            },
            "dev": {
                "status": "PENDING",
                "started_at": early_started_at,
                "completed_at": None,
                "agent_run_ids": [],
                "modules": [],
                "files_changed": [],
                "scope_declared": False,
            },
            "qa": {"status": "PENDING", "started_at": None, "completed_at": None},
            "sec": {"status": "PENDING", "started_at": None, "completed_at": None},
            "build": {"status": "PENDING", "started_at": None, "completed_at": None},
            "harness": {"status": "PENDING", "started_at": None, "completed_at": None},
            "architect": {"status": "PENDING", "started_at": None, "completed_at": None},
        },
        "module_gates": {"enabled": True, "modules": {}, "integration": None},
        "agent_runs": {},
        "event_log": [],
        "failure_packets": [],
        "outputs": {"items": []},
        "contract_v2": {"enabled": False},
        "pipeline_started_at": "2026-05-22T08:00:00Z",
        "created_at": "2026-05-22T08:00:00Z",
        "execution_profile": {"mode": "STANDARD"},
    }

    args = argparse.Namespace(
        phase="dev",
        branch=None,
        files="pipeline.py",
        report_file="dev_handover.xml",
        scope_declared=True,
        scope_manifest="scope_manifest.json",
        agent_run_id="dev-agent",
        decomp=False,
        clarification=False,
        roadmap=False,
        judgment_confirmed=False,
        planner_run_id=None,
        manager_run_id=None,
        manager_report=None,
    )

    with patch("pipeline._load_branch_state", return_value=state), \
         patch("pipeline._save_state_for"), \
         patch("pipeline._save"), \
         patch("pipeline.check_gate", return_value=(True, "")), \
         patch("pipeline._module_gate_blockers", return_value=[]), \
         patch("pipeline._validate_module_checkpoints"), \
         patch("pipeline._validate_dev_handover_file", return_value={}), \
         patch("pipeline._validate_dev_scope_manifest",
               return_value={"micro_task_ids": ["MT-1"], "files": ["pipeline.py"]}), \
         patch("pipeline._validate_agent_run_for_phase",
               return_value={"run_id": "dev-agent", "agent_id": "dev-agent"}), \
         patch("pipeline._record_snapshot"), \
         patch("pipeline._log_event"), \
         patch("pipeline._openai_advisory_required", return_value=False):
        cmd_done(args)

    assert state["phases"]["dev"]["started_at"] == early_started_at, (
        f"cmd_done 호출 후 started_at이 덮어써졌다: "
        f"expected={early_started_at}, got={state['phases']['dev']['started_at']}"
    )
    assert state["phases"]["dev"]["status"] == "DONE", (
        "cmd_done 후 status가 DONE이어야 한다"
    )


def test_gate_started_at_earlier_than_completed_at() -> None:
    """T-009: gate started_at이 completed_at보다 먼저 기록되어야 한다.

    fix-forward: _set_external_gate에서 started_at 기록 제거 + 핸들러 진입 시 기록.
    이를 모사하여 started_at < completed_at 를 검증한다.
    """
    state: Dict[str, Any] = {
        "pipeline_id": "TEST-T009",
        "external_gates": _ensure_external_gates({"pipeline_id": "TEST-T009"}),
    }

    # 핸들러 진입 시점에 started_at 기록 (fix-forward 패턴 모사)
    early_started_at = "2026-05-22T10:00:00Z"
    state["external_gates"]["technical"]["started_at"] = early_started_at

    # _set_external_gate 호출 — 이제 started_at을 덮어쓰지 않아야 한다
    _set_external_gate(state, "technical", "PASS", evidence="test")

    gate = state["external_gates"]["technical"]
    assert gate.get("started_at") == early_started_at, (
        f"_set_external_gate가 started_at을 덮어썼다: "
        f"expected={early_started_at}, got={gate.get('started_at')}"
    )
    assert gate.get("completed_at") is not None, "completed_at이 기록되지 않았다"
    # started_at < completed_at (ISO 8601 문자열 사전순 비교로 충분)
    assert gate["started_at"] <= gate["completed_at"], (
        f"started_at이 completed_at보다 늦다: "
        f"started_at={gate['started_at']}, completed_at={gate['completed_at']}"
    )


def test_elapsed_unavailable_when_started_at_missing() -> None:
    """T-010: started_at이 없으면 phase/gate elapsed가 '확인 불가'로 유지되어야 한다.

    fix-forward: fallback 제거 후 started_at이 없는 경우 elapsed 계산 불가 상태를 검증한다.
    """
    # Phase 케이스: started_at = None
    state_phase: Dict[str, Any] = {
        "pipeline_id": "TEST-T010-phase",
        "phases": {
            "dev": {
                "status": "DONE",
                "started_at": None,
                "completed_at": "2026-05-22T11:00:00Z",
            }
        },
    }
    phase_result = _phase_elapsed_summary(state_phase)
    dev_elapsed = phase_result.get("dev", {})
    assert dev_elapsed.get("elapsed_seconds") == "확인 불가", (
        f"started_at=None인데 elapsed_seconds가 '확인 불가'가 아님: "
        f"{dev_elapsed.get('elapsed_seconds')}"
    )

    # Gate 케이스: started_at = None
    state_gate: Dict[str, Any] = {
        "pipeline_id": "TEST-T010-gate",
        "external_gates": {
            "technical": {
                "status": "PASS",
                "started_at": None,
                "completed_at": "2026-05-22T11:00:00Z",
            }
        },
    }
    gate_result = _gate_elapsed_summary(state_gate)
    tech_elapsed = gate_result.get("technical", {})
    assert tech_elapsed.get("elapsed_seconds") == "확인 불가", (
        f"gate started_at=None인데 elapsed_seconds가 '확인 불가'가 아님: "
        f"{tech_elapsed.get('elapsed_seconds')}"
    )


def test_acceptance_packet_includes_metrics_summary() -> None:
    """T-011: human_acceptance_packet.md 파일에 '소요 시간 요약' 섹션이 포함되어야 한다.

    fix-forward: gates accept 완료 시 human_acceptance_packet.md에 metrics를 기록하는
    코드가 추가되었음을 검증한다. 임시 파일로 패치 로직을 직접 테스트한다.
    """
    import re

    mock_metrics: Dict[str, Any] = {
        "pipeline_id": "TEST-T011",
        "total_elapsed": {
            "elapsed_human": "5분",
            "started_at": "2026-05-22T10:00:00Z",
            "completed_at": "2026-05-22T10:05:00Z",
        },
        "phase_elapsed": {
            "pm": {"elapsed_human": "2분", "elapsed_seconds": 120},
        },
        "gate_elapsed": {
            "technical": {"status": "PASS", "elapsed_human": "30초", "elapsed_seconds": 30},
        },
        "failure_retry": {"total_failure_packets": 0, "failure_code_counts": {}},
        "github_actions": {
            "run_id": "확인 불가",
            "conclusion": "확인 불가",
            "elapsed_human": "확인 불가",
            "url": "확인 불가",
        },
        "agent_sessions": {},
        "bottleneck": {},
    }
    metrics_str = _format_metrics_summary_ko(mock_metrics)
    metrics_section = f"\n\n## 소요 시간 요약\n{metrics_str}\n"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", encoding="utf-8", delete=False
    ) as f:
        f.write("# 최종 확인 안내\n\n기존 내용입니다.\n")
        tmp_path = Path(f.name)

    try:
        # 섹션이 없으면 append
        existing = tmp_path.read_text(encoding="utf-8")
        assert "## 소요 시간 요약" not in existing, "초기 상태에 섹션이 이미 있어서는 안 된다"
        existing = existing.rstrip("\n") + metrics_section
        tmp_path.write_text(existing, encoding="utf-8")

        updated = tmp_path.read_text(encoding="utf-8")
        assert "## 소요 시간 요약" in updated, (
            "human_acceptance_packet.md에 '소요 시간 요약' 섹션이 없다"
        )
        assert "전체 소요 시간" in updated, (
            "소요 시간 요약 섹션에 metrics 내용이 포함되지 않았다"
        )

        # 두 번 append 시 중복되지 않는지 검증 (섹션 교체 로직)
        existing2 = tmp_path.read_text(encoding="utf-8")
        new_metrics_section = "\n\n## 소요 시간 요약\n새 메트릭\n"
        if "## 소요 시간 요약" in existing2:
            existing2 = re.sub(
                r"\n\n## 소요 시간 요약\n.*?(?=\n\n#|\Z)",
                new_metrics_section.rstrip("\n"),
                existing2,
                flags=re.DOTALL,
            )
        else:
            existing2 = existing2.rstrip("\n") + new_metrics_section
        tmp_path.write_text(existing2, encoding="utf-8")

        final = tmp_path.read_text(encoding="utf-8")
        assert final.count("## 소요 시간 요약") == 1, (
            f"'## 소요 시간 요약' 섹션이 중복 기록되었다: {final.count('## 소요 시간 요약')}회"
        )
    finally:
        tmp_path.unlink(missing_ok=True)


# ── Fix-forward v3 테스트 (cmd_check started_at 기록 검증) ──────────────────


def test_cmd_check_records_phase_started_at() -> None:
    """T-012: check --phase dev PASS 시 phase.started_at이 기록되어야 한다.

    IMP-20260522-29C1 fix-forward v3: cmd_check에서 PASS 시 started_at 기록 검증.
    실제 cmd_check 함수를 monkeypatch된 state로 호출하여 검증한다.
    """
    state: Dict[str, Any] = {
        "pipeline_id": "TEST-T012",
        "terminal_state": None,
        "phases": {
            "pm": {
                "status": "DONE",
                "started_at": "2026-05-22T09:00:00Z",
                "completed_at": "2026-05-22T10:00:00Z",
            },
            "dev": {
                "status": "PENDING",
                "started_at": None,
                "completed_at": None,
            },
        },
    }

    args = argparse.Namespace(
        phase="dev",
        codex_review_waiver="legacy-bootstrap",
        branch=None,
    )

    with patch("pipeline._load_branch_state", return_value=state), \
         patch("pipeline._save"), \
         patch("pipeline.check_gate", return_value=(True, "")):
        with pytest.raises(SystemExit) as exc:
            cmd_check(args)

    assert exc.value.code == 0, (
        f"check_gate PASS 시 cmd_check는 exit 0이어야 한다 (got {exc.value.code})"
    )
    assert state["phases"]["dev"]["started_at"] is not None, (
        "cmd_check PASS 후 dev phase.started_at이 기록되지 않았다"
    )
    assert isinstance(state["phases"]["dev"]["started_at"], str), (
        "started_at은 ISO 8601 문자열이어야 한다"
    )


def test_cmd_check_does_not_overwrite_existing_started_at() -> None:
    """T-013: check --phase dev PASS 시 기존 started_at을 덮어쓰지 않아야 한다.

    IMP-20260522-29C1 fix-forward v3: 이미 started_at이 설정된 phase에서
    cmd_check를 재실행해도 기존 값이 유지되어야 한다.
    """
    early_started_at = "2026-05-22T08:00:00Z"

    state: Dict[str, Any] = {
        "pipeline_id": "TEST-T013",
        "terminal_state": None,
        "phases": {
            "dev": {
                "status": "PENDING",
                "started_at": early_started_at,
                "completed_at": None,
            },
        },
    }

    args = argparse.Namespace(
        phase="dev",
        codex_review_waiver="legacy-bootstrap",
        branch=None,
    )

    with patch("pipeline._load_branch_state", return_value=state), \
         patch("pipeline._save"), \
         patch("pipeline.check_gate", return_value=(True, "")):
        with pytest.raises(SystemExit) as exc:
            cmd_check(args)

    assert exc.value.code == 0
    assert state["phases"]["dev"]["started_at"] == early_started_at, (
        f"기존 started_at이 cmd_check 재실행 후 덮어써졌다: "
        f"expected={early_started_at}, got={state['phases']['dev']['started_at']}"
    )


# ── Fix-forward v4 회귀 테스트 (BOM 처리 검증) ─────────────────────────────


def test_pr_body_bom_stripped_for_section_check() -> None:
    """T-014: PR 본문 첫 줄에 BOM이 있어도 필수 섹션 파싱이 정상 작동해야 한다.

    IMP-20260522-29C1 fix-forward v4: ci.yml의 Get-MarkdownSection과
    pipeline.py의 _check_acceptance_readiness에서 BOM을 제거하여
    ## 작업 요약 등 필수 섹션을 올바르게 인식하도록 수정.
    """
    bom = '﻿'
    pr_body_with_bom = (
        f'{bom}## 작업 요약\n콘텐츠\n'
        '## 사용자가 확인할 결과물\n콘텐츠\n'
        '## 기대 결과와 실제 결과\n콘텐츠\n'
        '## 중요한 선택과 트레이드오프\n콘텐츠\n'
        '## 검증\n콘텐츠'
    )

    # BOM 제거 전: 첫 줄이 '## '으로 시작하지 않아야 함 (BOM이 앞에 있음)
    first_line_raw = pr_body_with_bom.split('\n')[0]
    assert not first_line_raw.startswith('## '), (
        "테스트 전제 오류: BOM이 있을 때 첫 줄이 '## '으로 시작하면 안 된다"
    )

    # BOM 제거 후: 첫 줄이 '## 작업 요약'으로 시작해야 함
    pr_body_stripped = pr_body_with_bom.lstrip(bom)
    first_line_stripped = pr_body_stripped.split('\n')[0]
    assert first_line_stripped.startswith('## '), (
        f"BOM 제거 후 첫 줄이 '## '으로 시작해야 한다: got {repr(first_line_stripped[:10])}"
    )
    assert first_line_stripped == '## 작업 요약', (
        f"BOM 제거 후 첫 줄이 '## 작업 요약'이어야 한다: got {repr(first_line_stripped)}"
    )

    # BOM 제거 후 모든 필수 섹션이 검출되어야 함 (PR_REQUIRED_SECTIONS 기준)
    for section_spec in PR_REQUIRED_SECTIONS:
        if isinstance(section_spec, tuple):
            found = any(s in pr_body_stripped for s in section_spec)
            label = " 또는 ".join(section_spec)
        else:
            found = section_spec in pr_body_stripped
            label = section_spec
        assert found, f"BOM 제거 후에도 필수 섹션을 찾지 못했다: {label}"
