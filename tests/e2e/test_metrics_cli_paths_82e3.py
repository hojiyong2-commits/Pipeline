"""
test_metrics_cli_paths_82e3.py — IMP-20260526-82E3 MT-5 End-to-End CLI Path Tests

# [Purpose]: pipeline.py의 metrics report --json / --markdown 서브명령이 PIPELINE_STATE_PATH로
#            격리된 state 파일을 정확히 읽고 한국어 "확인 불가" 정책과 oracle 사양을
#            준수하는지 subprocess 기반 E2E로 검증.
# [Assumptions]: tests/oracles/IMP-20260526-82E3/TC-normal-metrics-report-json/expected.json,
#                tests/oracles/IMP-20260526-82E3/TC-edge-unavailable-values/expected.json 사전 작성됨.
#                tmp_path pytest fixture로 격리.
# [Vulnerability & Risks]:
#   - subprocess 호출 30초 timeout 초과 시 실패.
#   - PIPELINE_STATE_PATH는 pipeline.py가 평가하지 않으면 cmd_metrics report에서 처리되어야 함.

# CLI Evidence Contract (BUG-20260525-39DE):
# - 상태 조회 CLI(metrics report)는 read-only지만 격리된 state 파일을 PIPELINE_STATE_PATH로 주입하여
#   실제 state 입력에 대한 출력 결과를 final_state(JSON deserialized output) 기준으로 assert.
# - stdout-only 검증 금지 — JSON parse 후 키-값 단위로 assertion.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# pipeline.py는 tests/e2e의 2단계 상위 디렉토리에 위치
PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
ORACLE_DIR = Path(__file__).resolve().parent.parent / "oracles" / "IMP-20260526-82E3"


def run_cli(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """`python pipeline.py <args>` 실행 후 CompletedProcess 반환.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess에 전달할 환경 변수 딕셔너리.
        timeout: 초 단위 타임아웃 (기본 30초).
    """
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    cmd = [sys.executable, str(PIPELINE_PY)] + args
    return subprocess.run(
        cmd,
        env=env or os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def _write_state(state_path: Path, state_dict: Dict[str, Any]) -> None:
    """격리된 state 파일에 dict를 JSON으로 기록."""
    state_path.write_text(json.dumps(state_dict, ensure_ascii=False), encoding="utf-8")


def _isolated_env(state_path: Path) -> Dict[str, str]:
    """PIPELINE_STATE_PATH를 격리된 임시 경로로 설정한 env 반환."""
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    # dashboard auto-start 비활성화 (CI/local 모두에서 안전)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    return env


# ── Test 1: metrics report --json — TC-normal-metrics-report-json oracle 매칭 ──


def test_metrics_report_json_with_phase_timings(tmp_path):
    """metrics report --json이 격리된 state의 phase_timings/github_actions_timings를 정확히 읽는지 검증.

    오라클: TC-normal-metrics-report-json.
    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + JSON 출력 deserialized 검증.
    """
    state_path = tmp_path / "test_state.json"
    input_state = json.loads(
        (ORACLE_DIR / "TC-normal-metrics-report-json" / "input.json").read_text(encoding="utf-8")
    )
    expected = json.loads(
        (ORACLE_DIR / "TC-normal-metrics-report-json" / "expected.json").read_text(encoding="utf-8")
    )
    _write_state(state_path, input_state)
    env = _isolated_env(state_path)

    result = run_cli(["metrics", "report", "--json"], env=env)

    assert result.returncode == 0, (
        f"metrics report --json은 성공해야 합니다. stderr={result.stderr}"
    )
    # final_state(=출력 JSON) 검증 — stdout-only 검증 금지 규칙 준수
    final_state = json.loads(result.stdout)
    for key, val in expected.items():
        assert final_state.get(key) == val, (
            f"metrics report --json 결과의 '{key}'가 oracle expected와 다릅니다. "
            f"result[{key}]={final_state.get(key)}, expected={val}"
        )


# ── Test 2: metrics report --json — TC-edge-unavailable-values oracle 매칭 ────


def test_metrics_report_json_unavailable_values(tmp_path):
    """phase_timings가 비어 있고 total_elapsed_seconds가 null일 때 "확인 불가" 표시 검증.

    오라클: TC-edge-unavailable-values.
    """
    state_path = tmp_path / "test_state.json"
    input_state = json.loads(
        (ORACLE_DIR / "TC-edge-unavailable-values" / "input.json").read_text(encoding="utf-8")
    )
    expected = json.loads(
        (ORACLE_DIR / "TC-edge-unavailable-values" / "expected.json").read_text(encoding="utf-8")
    )
    _write_state(state_path, input_state)
    env = _isolated_env(state_path)

    result = run_cli(["metrics", "report", "--json"], env=env)

    assert result.returncode == 0
    final_state = json.loads(result.stdout)
    for key, val in expected.items():
        assert final_state.get(key) == val, (
            f"metrics report --json 결과의 '{key}'가 oracle expected와 다릅니다. "
            f"result[{key}]={final_state.get(key)}, expected={val}"
        )


# ── Test 3: metrics report --markdown 한국어 섹션 + final_state(stdout) 검증 ───


def test_metrics_report_markdown_includes_korean_sections(tmp_path):
    """metrics report --markdown이 4개 한국어 섹션 헤더를 포함하는지 검증.

    CLI Evidence Contract: PIPELINE_STATE_PATH 격리 + 출력 내용(final_state로서의 stdout text)을
    section header 단위로 assert. 단순 키워드 검사가 아니라 markdown 구조 검증.
    """
    state_path = tmp_path / "test_state.json"
    input_state = {
        "pipeline_id": "TEST-MD-001",
        "phase_timings": {"dev": {"elapsed_seconds": 600}, "qa": {"elapsed_seconds": 120}},
        "gate_timings": {},
        "github_actions_timings": {
            "WAITING_FOR_TRIGGER": 5,
            "QUEUED": 0,
            "IN_PROGRESS": 90,
            "COMPLETED": 0,
            "TIMEOUT": 0,
        },
        "failure_summary": {"some_code": {"count": 2, "is_repeat": True}},
        "total_elapsed_seconds": 720,
    }
    _write_state(state_path, input_state)
    env = _isolated_env(state_path)

    result = run_cli(["metrics", "report", "--markdown"], env=env)

    assert result.returncode == 0, f"stderr={result.stderr}"
    text = result.stdout
    # 4개 한국어 섹션 헤더가 모두 존재해야 함 (final_state 검증)
    assert "# 파이프라인 메트릭 보고서 [TEST-MD-001]" in text
    assert "## 전체 소요 시간" in text
    assert "## Phase별 소요 시간" in text
    assert "## GitHub Actions 대기 시간" in text
    assert "## 실패/재시도 요약" in text
    # phase별 elapsed가 정확히 보고되어야 함 (dev=600)
    assert "dev: 600초" in text
    # github IN_PROGRESS=90이 정확히 보고됨
    assert "IN_PROGRESS: 90초" in text
    # 실패 요약: some_code 2회, 반복 표시
    assert "some_code: 2회 (반복)" in text
