"""IMP-20260524-48C4 MT-3: Oracle Quality Gate CLI Evidence Contract 테스트.

이 테스트 파일은 oracle 파일(tests/oracles/IMP-20260524-48C4/TC01~TC04)을 입력으로 사용하여
_audit_oracle_quality() 함수의 동작을 검증합니다.

테스트 함수:
  - test_normal_plus_edge_passes: TC01 oracle (normal+edge) → status=PASS
  - test_normal_only_fails: TC02 oracle (normal-only) → status=FAIL, edge_required
  - test_placeholder_expected_fails: empty JSON expected → status=FAIL
  - test_agent_generated_blocked: TC04 oracle (agent_generated) → status=BLOCKED
  - test_allow_agent_generated_flag: allow_agent_generated=True → PASS
  - test_empty_entries_fails: 빈 entries → status=FAIL
  - test_case_summary_counts: case_summary 집계 정확성
  - test_ensure_v210_oracle_quality_init: _ensure_v210_fields oracle_quality 초기화
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from typing import Any, Dict, List

import pytest

# pipeline 모듈 import
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pipeline

ORACLE_BASE = pathlib.Path(__file__).resolve().parent / "oracles" / "IMP-20260524-48C4"


def _load_input(tc: str) -> Dict[str, Any]:
    """oracle TC의 input.json 로드."""
    path = ORACLE_BASE / tc / "input.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_expected(tc: str) -> Dict[str, Any]:
    """oracle TC의 expected.json 로드."""
    path = ORACLE_BASE / tc / "expected.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_normal_plus_edge_passes() -> None:
    """TC01: normal+edge oracle → _audit_oracle_quality PASS 반환."""
    data = _load_input("TC01")
    expected = _load_expected("TC01")
    oracles = data["oracles"]
    allow = data.get("allow_agent_generated", False)

    result = pipeline._audit_oracle_quality(oracles, allow_agent_generated=allow)

    assert result["status"] == expected["oracle_quality"]["status"], (
        f"status mismatch: got {result['status']!r}, expected {expected['oracle_quality']['status']!r}"
    )
    assert result["failures"] == expected["oracle_quality"]["failures"], (
        f"failures mismatch: got {result['failures']!r}"
    )
    assert result["case_summary"] == expected["oracle_quality"]["case_summary"], (
        f"case_summary mismatch: got {result['case_summary']!r}"
    )


def test_normal_only_fails() -> None:
    """TC02: normal-only oracle (edge 없음) → status=FAIL, edge_required 메시지 포함."""
    data = _load_input("TC02")
    expected = _load_expected("TC02")
    oracles = data["oracles"]
    allow = data.get("allow_agent_generated", False)

    result = pipeline._audit_oracle_quality(oracles, allow_agent_generated=allow)

    assert result["status"] == "FAIL", f"expected FAIL, got {result['status']!r}"
    assert result["case_summary"]["edge"] == 0
    assert result["case_summary"]["error"] == 0
    edge_failure_msgs = [f for f in result["failures"] if "edge_required" in f]
    assert edge_failure_msgs, (
        f"edge_required failure 메시지 없음. failures: {result['failures']}"
    )


def test_placeholder_expected_fails() -> None:
    """empty JSON expected {} → status=FAIL, 빈 값 failure 메시지 포함."""
    with tempfile.TemporaryDirectory() as tmpdir:
        empty_json = pathlib.Path(tmpdir) / "expected.json"
        empty_json.write_text("{}", encoding="utf-8")

        oracles = [
            {
                "name": "tc-placeholder-normal",
                "case_kind": "normal",
                "expected_source": "user_provided",
                "expected_path": str(empty_json),
            },
            {
                "name": "tc-placeholder-edge",
                "case_kind": "edge",
                "expected_source": "user_provided",
            },
        ]
        result = pipeline._audit_oracle_quality(oracles, allow_agent_generated=False)

    assert result["status"] == "FAIL", f"expected FAIL, got {result['status']!r}"
    empty_failures = [f for f in result["failures"] if "빈 값" in f or "empty" in f.lower()]
    assert empty_failures, (
        f"빈 값 failure 메시지 없음. failures: {result['failures']}"
    )


def test_agent_generated_blocked() -> None:
    """TC04: expected_source=agent_generated (allow=False) → status=BLOCKED."""
    data = _load_input("TC04")
    expected = _load_expected("TC04")
    oracles = data["oracles"]
    allow = data.get("allow_agent_generated", False)

    result = pipeline._audit_oracle_quality(oracles, allow_agent_generated=allow)

    assert result["status"] == "BLOCKED", f"expected BLOCKED, got {result['status']!r}"
    assert len(result["failures"]) == len(expected["oracle_quality"]["failures"]), (
        f"failures 수 불일치: got {len(result['failures'])}, expected {len(expected['oracle_quality']['failures'])}"
    )
    # 모든 failure에 agent_generated 키워드 포함
    for failure in result["failures"]:
        assert "agent_generated" in failure, (
            f"failure 메시지에 agent_generated 없음: {failure!r}"
        )


def test_allow_agent_generated_flag() -> None:
    """TC04 oracle에 allow_agent_generated=True → BLOCKED가 아닌 PASS 반환."""
    data = _load_input("TC04")
    oracles = data["oracles"]

    result = pipeline._audit_oracle_quality(oracles, allow_agent_generated=True)

    assert result["status"] == "PASS", (
        f"allow_agent_generated=True 시 PASS 기대, got {result['status']!r}. "
        f"failures: {result['failures']}"
    )
    # agent_generated 관련 failure 없어야 함
    ag_failures = [f for f in result["failures"] if "agent_generated" in f]
    assert not ag_failures, f"allow=True에도 agent_generated failure 남음: {ag_failures}"


def test_empty_entries_fails() -> None:
    """빈 entries 리스트 → status=FAIL (oracle 없음)."""
    result = pipeline._audit_oracle_quality([], allow_agent_generated=False)

    assert result["status"] == "FAIL", f"expected FAIL for empty entries, got {result['status']!r}"
    assert result["case_summary"]["normal"] == 0
    assert result["case_summary"]["edge"] == 0


def test_case_summary_counts() -> None:
    """TC01 oracle의 case_summary 집계가 정확한지 확인."""
    data = _load_input("TC01")
    expected = _load_expected("TC01")
    oracles = data["oracles"]

    result = pipeline._audit_oracle_quality(oracles, allow_agent_generated=False)

    cs = result["case_summary"]
    exp_cs = expected["oracle_quality"]["case_summary"]
    assert cs["normal"] == exp_cs["normal"], f"normal count mismatch: {cs['normal']} != {exp_cs['normal']}"
    assert cs["edge"] == exp_cs["edge"], f"edge count mismatch: {cs['edge']} != {exp_cs['edge']}"
    assert cs["error"] == exp_cs["error"], f"error count mismatch: {cs['error']} != {exp_cs['error']}"
    assert cs["regression"] == exp_cs["regression"], (
        f"regression count mismatch: {cs['regression']} != {exp_cs['regression']}"
    )


def test_ensure_v210_oracle_quality_init() -> None:
    """_ensure_v210_fields가 oracle_quality 필드를 빈 dict로 초기화하는지 확인."""
    state: Dict[str, Any] = {}
    pipeline._ensure_v210_fields(state)

    assert "oracle_quality" in state, "_ensure_v210_fields 후 oracle_quality 필드 없음"
    assert state["oracle_quality"] == {}, (
        f"oracle_quality 초기값이 {{}} 아님: {state['oracle_quality']!r}"
    )

    # 이미 있으면 덮어쓰지 않아야 함
    existing = {"status": "PASS", "failures": []}
    state2: Dict[str, Any] = {"oracle_quality": existing}
    pipeline._ensure_v210_fields(state2)
    assert state2["oracle_quality"] is existing, (
        "_ensure_v210_fields가 기존 oracle_quality를 덮어씀"
    )
