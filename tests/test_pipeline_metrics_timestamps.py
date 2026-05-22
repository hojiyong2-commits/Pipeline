"""IMP-20260522-29C1: Pipeline Metrics Timestamp Instrumentation 테스트

7개 테스트:
  - T-001: phase elapsed (both timestamps present)
  - T-002: phase elapsed (missing started_at)
  - T-003: total elapsed (lifecycle timestamps)
  - T-004: gate elapsed (both timestamps present)
  - T-005: malformed timestamp -> unavailable, no crash
  - T-006: _format_metrics_summary_ko 출력에 필수 섹션 포함 확인
  - T-007: runtime artifacts not in git-tracked diff
"""
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
ORACLE_ROOT = PROJECT_ROOT / "tests" / "oracles" / "IMP-20260522-29C1"
sys.path.insert(0, str(PROJECT_ROOT))
from pipeline import _phase_elapsed_summary, _gate_elapsed_summary, _collect_pipeline_metrics


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
