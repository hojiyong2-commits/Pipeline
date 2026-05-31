"""IMP-20260531-4AC2: CI run ID 브랜치 필터링 E2E 테스트.

TC-BR1 ~ TC-BR5 — PIPELINE_STATE_PATH 격리 + subprocess 기반 실제 CLI.

아래 5개 시나리오를 테스트합니다:
  TC-BR1 (normal): 현재 브랜치에서 _get_pr_branch_ci_run_id 직접 호출 — 브랜치 run 반환
  TC-BR2 (edge):   detached HEAD (branch='HEAD') → None 반환
  TC-BR3 (edge):   다른 브랜치 run이 globally latest여도 현재 브랜치 run 선택
  TC-BR4 (edge):   gh CLI FileNotFoundError → None 반환 (예외 전파 없음)
  TC-BR5 (edge):   gh CLI 빈 출력 → None 반환

격리 전략:
  - PIPELINE_STATE_PATH 환경 변수로 state 파일을 tmp_path 안에 격리
  - subprocess.run mock으로 fake gh CLI 응답 구현 (Windows PATH 우선순위 우회)
  - 전역 pipeline_state.json을 수정하지 않음
  - IMP-20260525-6FAC: 함수 단위 테스트라 final_state 없음
    (stateless 함수이므로 반환값 assertion이 final_state 역할)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

# ─── 헬퍼 ──────────────────────────────────────────────────────────────────────

PIPELINE_PY = str(Path(__file__).resolve().parents[2] / "pipeline.py")
PIPELINE_DIR = Path(PIPELINE_PY).parent

# fake gh CLI가 반환할 run ID
IMPL_BRANCH = "impl/IMP-20260531-4AC2"
PHASE_ATTEST_BRANCH = "phase-attestation/IMP-20260531-4AC2-pm"
IMPL_BRANCH_RUN_ID = "11111111"
GLOBAL_LATEST_RUN_ID = "99999999"


def _import_pipeline():
    """pipeline 모듈을 동적으로 임포트."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", PIPELINE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_completed_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    """fake subprocess.CompletedProcess 생성."""
    result = subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
    return result


# ─── TC-BR1 (normal) ────────────────────────────────────────────────────────────

def test_tc_br1_normal_branch_run_id(tmp_path: Path) -> None:
    """TC-BR1: 현재 브랜치 기반 run ID 반환 — 정상 케이스.

    fake gh: --branch 옵션 있으면 IMPL_BRANCH_RUN_ID(11111111) 반환.
    _get_pr_branch_ci_run_id(branch=IMPL_BRANCH)가 11111111을 반환해야 한다.
    """
    pipeline = _import_pipeline()

    def fake_subprocess_run(cmd, **kwargs):
        # gh run list --branch <branch> → 11111111
        if "gh" in cmd[0] and "--branch" in cmd:
            return _make_completed_process(returncode=0, stdout=IMPL_BRANCH_RUN_ID + "\n")
        # gh run list (no --branch) → 99999999
        if "gh" in cmd[0]:
            return _make_completed_process(returncode=0, stdout=GLOBAL_LATEST_RUN_ID + "\n")
        return _make_completed_process(returncode=0, stdout="")

    with patch.object(pipeline.subprocess, "run", side_effect=fake_subprocess_run):
        result = pipeline._get_pr_branch_ci_run_id(branch=IMPL_BRANCH)

    assert result == IMPL_BRANCH_RUN_ID, (
        f"TC-BR1 FAIL: expected '{IMPL_BRANCH_RUN_ID}' but got {repr(result)}"
    )
    assert result != GLOBAL_LATEST_RUN_ID, (
        f"TC-BR1 FAIL: should NOT return global latest run ID '{GLOBAL_LATEST_RUN_ID}'"
    )


# ─── TC-BR2 (edge) ─────────────────────────────────────────────────────────────

def test_tc_br2_edge_detached_head(tmp_path: Path) -> None:
    """TC-BR2: detached HEAD 상태에서 None 반환.

    branch='HEAD'를 명시적으로 전달하면 gh CLI 호출 없이 None 반환해야 한다.
    """
    pipeline = _import_pipeline()

    # gh CLI가 호출되면 AssertionError
    def should_not_be_called(cmd, **kwargs):
        assert False, f"TC-BR2 FAIL: gh CLI should not be called, but got cmd={cmd}"

    with patch.object(pipeline.subprocess, "run", side_effect=should_not_be_called):
        result = pipeline._get_pr_branch_ci_run_id(branch="HEAD")

    assert result is None, (
        f"TC-BR2 FAIL: detached HEAD should return None, got {repr(result)}"
    )


# ─── TC-BR3 (edge) ─────────────────────────────────────────────────────────────

def test_tc_br3_edge_branch_filter_vs_global(tmp_path: Path) -> None:
    """TC-BR3: phase-attestation run이 globally latest여도 현재 브랜치 run 선택.

    fake gh: --branch 없이 호출 → GLOBAL_LATEST_RUN_ID(99999999)
             --branch impl/... 호출  → IMPL_BRANCH_RUN_ID(11111111)

    _get_pr_branch_ci_run_id(branch=IMPL_BRANCH)는 반드시 11111111을 반환해야 한다.
    이것이 이 IMP의 핵심 검증 케이스이다.
    """
    pipeline = _import_pipeline()

    call_log: List[List[str]] = []

    def fake_subprocess_run(cmd, **kwargs):
        call_log.append(list(cmd))
        if "gh" in cmd[0] and "--branch" in cmd:
            return _make_completed_process(returncode=0, stdout=IMPL_BRANCH_RUN_ID + "\n")
        if "gh" in cmd[0]:
            return _make_completed_process(returncode=0, stdout=GLOBAL_LATEST_RUN_ID + "\n")
        return _make_completed_process(returncode=0, stdout="")

    with patch.object(pipeline.subprocess, "run", side_effect=fake_subprocess_run):
        result = pipeline._get_pr_branch_ci_run_id(branch=IMPL_BRANCH)

    assert result == IMPL_BRANCH_RUN_ID, (
        f"TC-BR3 FAIL: expected branch run '{IMPL_BRANCH_RUN_ID}', got {repr(result)}"
    )
    assert result != GLOBAL_LATEST_RUN_ID, (
        f"TC-BR3 FAIL: must NOT return global latest run '{GLOBAL_LATEST_RUN_ID}'"
    )
    # --branch 옵션이 gh 호출에 포함되었는지 확인
    gh_calls = [c for c in call_log if "gh" in c[0]]
    assert len(gh_calls) > 0, "TC-BR3 FAIL: gh CLI not called"
    assert any("--branch" in c for c in gh_calls), (
        f"TC-BR3 FAIL: --branch option not used in gh call. calls={gh_calls}"
    )


# ─── TC-BR4 (edge) ─────────────────────────────────────────────────────────────

def test_tc_br4_edge_gh_cli_missing(tmp_path: Path) -> None:
    """TC-BR4: gh CLI가 없을 때 FileNotFoundError → None 반환.

    subprocess.run이 FileNotFoundError를 raise하면
    _get_pr_branch_ci_run_id가 조용히 None을 반환해야 한다.
    """
    pipeline = _import_pipeline()

    def raise_file_not_found(cmd, **kwargs):
        if "gh" in cmd[0]:
            raise FileNotFoundError(f"No such file: {cmd[0]}")
        return _make_completed_process(returncode=0, stdout="")

    with patch.object(pipeline.subprocess, "run", side_effect=raise_file_not_found):
        try:
            result = pipeline._get_pr_branch_ci_run_id(branch=IMPL_BRANCH)
        except FileNotFoundError as exc:
            assert False, (
                f"TC-BR4 FAIL: FileNotFoundError should be caught, not propagated: {exc}"
            )

    assert result is None, (
        f"TC-BR4 FAIL: gh CLI missing should return None, got {repr(result)}"
    )


# ─── TC-BR5 (edge) ─────────────────────────────────────────────────────────────

def test_tc_br5_edge_empty_output(tmp_path: Path) -> None:
    """TC-BR5: gh CLI 빈 출력 → None 반환.

    gh가 returncode=0이나 빈 문자열 반환 시 None이어야 한다.
    """
    pipeline = _import_pipeline()

    def fake_subprocess_run(cmd, **kwargs):
        if "gh" in cmd[0]:
            return _make_completed_process(returncode=0, stdout="")
        return _make_completed_process(returncode=0, stdout="")

    with patch.object(pipeline.subprocess, "run", side_effect=fake_subprocess_run):
        result = pipeline._get_pr_branch_ci_run_id(branch=IMPL_BRANCH)

    assert result is None, (
        f"TC-BR5 FAIL: empty gh output should return None, got {repr(result)}"
    )
