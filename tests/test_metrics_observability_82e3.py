"""Unit tests for IMP-20260526-82E3 Observability Metrics Gate.

7개 단위 테스트:
1. _new_state — 5개 alias 키 존재
2. _ensure_v210_fields — 구버전 state에 5개 alias 키 자동 마이그레이션
3. _record_phase_timing — TC-normal-phase-timing oracle 매칭
4. _record_gate_timing — TC-normal-gate-timing oracle 매칭
5. _aggregate_github_actions_state_durations — TC-normal-github-actions-timing oracle 매칭
6. _compute_failure_summary_from_list — TC-normal-failure-summary oracle 매칭
7. _format_metrics_report_json — TC-normal-metrics-report-json + TC-edge-unavailable-values oracle 매칭
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pipeline  # noqa: E402


_ORACLE_ROOT = _ROOT / "tests" / "oracles" / "IMP-20260526-82E3"


def _load_oracle(case_id: str, filename: str) -> dict:
    """oracle 파일을 dict로 로드."""
    path = _ORACLE_ROOT / case_id / filename
    return json.loads(path.read_text(encoding="utf-8"))


# ── Test 1: _new_state 5개 alias 키 존재 ──────────────────────────────────────


def test_new_state_contains_five_alias_keys():
    state = pipeline._new_state("TEST-1", "IMP", "test")
    for key in (
        "phase_timings",
        "gate_timings",
        "agent_timings",
        "github_actions_timings",
        "failure_summary",
    ):
        assert key in state, f"_new_state는 alias 키 '{key}'를 포함해야 합니다."
    # github_actions_timings는 5상태 키를 가진 dict
    gh = state["github_actions_timings"]
    assert isinstance(gh, dict)
    for st in ("WAITING_FOR_TRIGGER", "QUEUED", "IN_PROGRESS", "COMPLETED", "TIMEOUT"):
        assert gh.get(st) == 0, f"github_actions_timings.{st}의 기본값은 0이어야 합니다."


# ── Test 2: _ensure_v210_fields가 5개 alias 키를 idempotent 마이그레이션 ────


def test_ensure_v210_fields_migrates_five_alias_keys():
    legacy_state = {"version": 1, "pipeline_id": "OLD-1", "phases": {}, "event_log": []}
    migrated = pipeline._ensure_v210_fields(legacy_state)
    for key in (
        "phase_timings",
        "gate_timings",
        "agent_timings",
        "github_actions_timings",
        "failure_summary",
    ):
        assert key in migrated, f"_ensure_v210_fields는 '{key}' 키를 추가해야 합니다."
    # idempotent
    migrated_again = pipeline._ensure_v210_fields(migrated)
    assert migrated_again["github_actions_timings"]["IN_PROGRESS"] == 0


# ── Test 3: _record_phase_timing — TC-normal-phase-timing 오라클 ──────────────


def test_record_phase_timing_matches_oracle_tc_normal():
    input_data = _load_oracle("TC-normal-phase-timing", "input.json")
    expected = _load_oracle("TC-normal-phase-timing", "expected.json")
    result = pipeline._record_phase_timing(
        input_data["phase"], input_data["start"], input_data["end"]
    )
    assert result == expected, (
        f"_record_phase_timing이 oracle expected와 일치해야 합니다. "
        f"result={result}, expected={expected}"
    )


# ── Test 4: _record_gate_timing — TC-normal-gate-timing 오라클 ───────────────


def test_record_gate_timing_matches_oracle_tc_normal():
    input_data = _load_oracle("TC-normal-gate-timing", "input.json")
    expected = _load_oracle("TC-normal-gate-timing", "expected.json")
    result = pipeline._record_gate_timing(
        input_data["gate"], input_data["start"], input_data["end"]
    )
    assert result == expected


# ── Test 5: _aggregate_github_actions_state_durations — TC-normal-github 오라클


def test_aggregate_github_actions_state_durations_matches_oracle():
    input_data = _load_oracle("TC-normal-github-actions-timing", "input.json")
    expected = _load_oracle("TC-normal-github-actions-timing", "expected.json")
    result = pipeline._aggregate_github_actions_state_durations(input_data["state_transitions"])
    assert result == expected


def test_aggregate_github_actions_returns_zeros_for_empty():
    result = pipeline._aggregate_github_actions_state_durations([])
    assert result == {
        "WAITING_FOR_TRIGGER": 0,
        "QUEUED": 0,
        "IN_PROGRESS": 0,
        "COMPLETED": 0,
        "TIMEOUT": 0,
    }


def test_aggregate_github_actions_ignores_invalid_state():
    result = pipeline._aggregate_github_actions_state_durations(
        [{"state": "INVALID_STATE", "duration": 999}]
    )
    assert result["WAITING_FOR_TRIGGER"] == 0
    assert result["IN_PROGRESS"] == 0


# ── Test 6: _compute_failure_summary_from_list — TC-normal-failure-summary 오라클


def test_compute_failure_summary_matches_oracle():
    input_data = _load_oracle("TC-normal-failure-summary", "input.json")
    expected = _load_oracle("TC-normal-failure-summary", "expected.json")
    result = pipeline._compute_failure_summary_from_list(input_data["failures"])
    assert result == expected


def test_compute_failure_summary_single_occurrence_is_not_repeat():
    result = pipeline._compute_failure_summary_from_list([{"code": "single_error"}])
    assert result == {"single_error": {"count": 1, "is_repeat": False}}


# ── Test 7: _format_metrics_report_json — 2개 오라클 모두 검증 ────────────────


def test_format_metrics_report_json_matches_tc_normal_metrics_report_json():
    input_data = _load_oracle("TC-normal-metrics-report-json", "input.json")
    expected = _load_oracle("TC-normal-metrics-report-json", "expected.json")
    result = pipeline._format_metrics_report_json(input_data)
    # expected의 키-값이 result에 정확히 포함되어야 한다 (다른 키는 무관)
    for key, val in expected.items():
        assert result.get(key) == val, (
            f"_format_metrics_report_json 결과의 '{key}'가 oracle expected와 다릅니다. "
            f"result[{key}]={result.get(key)}, expected={val}"
        )


def test_format_metrics_report_json_matches_tc_edge_unavailable_values():
    input_data = _load_oracle("TC-edge-unavailable-values", "input.json")
    expected = _load_oracle("TC-edge-unavailable-values", "expected.json")
    result = pipeline._format_metrics_report_json(input_data)
    for key, val in expected.items():
        assert result.get(key) == val, (
            f"_format_metrics_report_json 결과의 '{key}'가 oracle expected와 다릅니다. "
            f"result[{key}]={result.get(key)}, expected={val}"
        )
