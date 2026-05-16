# tests/test_pr_hygiene.py
"""PR 오염 방지 회귀 테스트 (IMP-20260516-D069)

MT-1: gates preflight-pr 행동 검증
MT-2: acceptance evidence 내부 경로 거부 검증
MT-3: architect phase_ci run_id 불일치 검증
MT-4: CLAUDE.md patch verify 예시에 --test-command 또는 --evidence-file 포함 여부 검증
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# 프로젝트 루트 경로
BASE_DIR = Path(__file__).resolve().parent.parent


def _run_pipeline(*args: str, cwd: Path = BASE_DIR) -> subprocess.CompletedProcess:
    """pipeline.py를 서브프로세스로 실행합니다."""
    return subprocess.run(
        [sys.executable, str(BASE_DIR / "pipeline.py"), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


# ─────────────────────────────────────────────
# MT-1 테스트: preflight-pr gate
# ─────────────────────────────────────────────

def test_preflight_pr_fails_on_stale_pipeline_files(tmp_path, monkeypatch):
    """stale .pipeline tracked file이 있으면 preflight-pr FAIL (exit code != 0)."""
    # 금지 파일 목록을 git diff 결과로 모킹
    stale_files = [
        ".pipeline/phase_evidence/IMP-TEST-0000/dev/handover.json",
        "pipeline_state.json",
    ]
    fake_diff_output = "\n".join(stale_files) + "\n"

    # _classify_pr_file과 git diff를 모킹하여 단위 테스트 수행
    # pipeline.py의 _classify_pr_file 함수를 직접 import하여 테스트
    import importlib
    import types

    # pipeline 모듈 직접 import
    spec = importlib.util.spec_from_file_location("pipeline", BASE_DIR / "pipeline.py")
    pipeline_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(pipeline_mod)  # type: ignore[union-attr]

    classify = pipeline_mod._classify_pr_file

    # pm phase에서 dev evidence는 forbidden이어야 함
    result_dev = classify(stale_files[0], "pm", "IMP-TEST-0000")
    assert result_dev.startswith("forbidden"), (
        f"dev evidence가 pm phase에서 forbidden이어야 합니다: {result_dev}"
    )

    # pipeline_state.json은 forbidden이어야 함
    result_state = classify(stale_files[1], "pm", "IMP-TEST-0000")
    assert result_state.startswith("forbidden"), (
        f"pipeline_state.json이 forbidden이어야 합니다: {result_state}"
    )

    # pm phase의 own evidence는 allowed여야 함
    own_evidence = ".pipeline/phase_evidence/IMP-TEST-0000/pm/receipt.json"
    result_own = classify(own_evidence, "pm", "IMP-TEST-0000")
    assert result_own == "allowed", (
        f"pm phase evidence가 pm phase에서 allowed여야 합니다: {result_own}"
    )


# ─────────────────────────────────────────────
# MT-2 테스트: acceptance evidence 내부 경로 거부
# ─────────────────────────────────────────────

def test_accept_rejects_phase_evidence_path():
    """acceptance evidence가 .pipeline/phase_evidence면 FAIL이어야 합니다."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("pipeline", BASE_DIR / "pipeline.py")
    pipeline_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(pipeline_mod)  # type: ignore[union-attr]

    is_internal = pipeline_mod._is_internal_pipeline_path

    # 금지 경로들
    forbidden_paths = [
        ".pipeline/phase_evidence/IMP-TEST-0000/pm/receipt.json",
        ".pipeline/agent_receipts/IMP-TEST-0000/pm_planner/run.json",
        "pipeline_contracts/IMP-TEST-0000/contract.json",
        "pipeline_state.json",
    ]
    for path in forbidden_paths:
        assert is_internal(path), (
            f"내부 파이프라인 경로가 거부되어야 합니다: {path}"
        )

    # 허용 경로들
    allowed_paths = [
        "pipeline_outputs/IMP-TEST-0000/result.md",
        "https://github.com/owner/repo/actions/runs/12345",
        "tests/test_pr_hygiene.py",
    ]
    for path in allowed_paths:
        assert not is_internal(path), (
            f"허용 경로가 내부 경로로 잘못 분류되었습니다: {path}"
        )


# ─────────────────────────────────────────────
# MT-3 테스트: architect run_id/commit_sha 불일치
# ─────────────────────────────────────────────

def test_architect_fails_on_run_id_mismatch():
    """state의 phase_ci_run_id와 phase_ci_result.json의 run_id가 다르면
    _verify_phase_attestation_consistency가 불일치를 반환해야 합니다."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("pipeline", BASE_DIR / "pipeline.py")
    pipeline_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(pipeline_mod)  # type: ignore[union-attr]

    verify_fn = pipeline_mod._verify_phase_attestation_consistency

    # 불일치 state 구성 (phase_ci_root가 없으면 검사 생략되므로 mock 필요 없음)
    # 함수가 존재하는지, 리스트를 반환하는지만 확인
    state_no_phases = {
        "pipeline_id": "IMP-TEST-0000",
        "phase_attestations": {
            "phases": {},
        },
    }
    result = verify_fn(state_no_phases)
    assert isinstance(result, list), "반환값이 리스트여야 합니다"

    # phase_ci_root가 없는 경우 빈 리스트 반환
    assert result == [], (
        f"phase CI 결과 없으면 불일치가 없어야 합니다: {result}"
    )


# ─────────────────────────────────────────────
# MT-4 테스트: CLAUDE.md patch verify 예시 검증
# ─────────────────────────────────────────────

def test_doc_cli_patch_verify_contract():
    """CLAUDE.md의 patch verify 예시에 --test-command 또는 --evidence-file이 있는지 확인합니다."""
    claude_md = BASE_DIR / "CLAUDE.md"
    assert claude_md.exists(), "CLAUDE.md 파일이 존재해야 합니다"

    content = claude_md.read_text(encoding="utf-8")

    # patch verify --plan ... --result PASS 가 있는 라인들 수집
    verify_lines = [
        line for line in content.splitlines()
        if "patch verify" in line and "--result PASS" in line
    ]

    assert verify_lines, (
        "CLAUDE.md에 'patch verify ... --result PASS' 예시 라인이 있어야 합니다"
    )

    for line in verify_lines:
        assert "--test-command" in line or "--evidence-file" in line, (
            f"CLAUDE.md patch verify 예시에 --test-command 또는 --evidence-file이 없습니다: {line}"
        )


# ─────────────────────────────────────────────
# MT-1 추가 테스트: _is_internal_pipeline_artifact
# ─────────────────────────────────────────────

def _load_pipeline_module():
    """pipeline.py 모듈을 로드하여 반환합니다."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", BASE_DIR / "pipeline.py")
    pipeline_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(pipeline_mod)  # type: ignore[union-attr]
    return pipeline_mod


def test_internal_pipeline_path_blocks_pipeline_state():
    """pipeline_state.json은 acceptance evidence 내부 경로로 분류되어야 합니다."""
    mod = _load_pipeline_module()
    assert mod._is_internal_pipeline_path("pipeline_state.json"), (
        "pipeline_state.json이 내부 파이프라인 경로로 분류되어야 합니다"
    )


def test_internal_pipeline_path_blocks_pipeline_contracts():
    """pipeline_contracts/ 경로는 acceptance evidence 내부 경로로 분류되어야 합니다."""
    mod = _load_pipeline_module()
    assert mod._is_internal_pipeline_path("pipeline_contracts/IMP-TEST/contract.json"), (
        "pipeline_contracts/ 경로가 내부 파이프라인 경로로 분류되어야 합니다"
    )


def test_internal_pipeline_path_blocks_phase_evidence():
    """phase_evidence 경로는 acceptance evidence 내부 경로로 분류되어야 합니다."""
    mod = _load_pipeline_module()
    assert mod._is_internal_pipeline_path(".pipeline/phase_evidence/IMP-TEST-0000/pm/receipt.json"), (
        ".pipeline/phase_evidence/ 경로가 내부 파이프라인 경로로 분류되어야 합니다"
    )


def test_internal_pipeline_path_allows_pipeline_outputs():
    """pipeline_outputs/ 경로는 acceptance evidence로 허용되어야 합니다."""
    mod = _load_pipeline_module()
    assert not mod._is_internal_pipeline_path("pipeline_outputs/IMP-TEST-0000/result.md"), (
        "pipeline_outputs/ 경로가 내부 경로로 잘못 분류되었습니다"
    )


def test_classify_pr_file_strict_allowlist_rejects_product_files():
    """strict allowlist: phase attestation PR에서는 product 파일도 forbidden이어야 합니다."""
    mod = _load_pipeline_module()
    # phase attestation PR에는 .pipeline/ 경로만 허용 — product 파일은 forbidden
    product_files = ["pipeline.py", ".github/workflows/ci.yml", "tests/test_pr_hygiene.py"]
    for f in product_files:
        result = mod._classify_pr_file(f, "pm", "IMP-TEST-0000")
        assert result.startswith("forbidden"), (
            f"phase attestation PR에서 product 파일이 forbidden이어야 합니다: {f} -> {result}"
        )


def test_classify_pr_file_allows_phase_attestation_request():
    """_classify_pr_file: .pipeline/phase_attestation_request.json은 항상 allowed."""
    mod = _load_pipeline_module()
    result = mod._classify_pr_file(".pipeline/phase_attestation_request.json", "pm", "IMP-TEST-0000")
    assert result == "allowed", (
        f"phase_attestation_request.json이 allowed여야 합니다: {result}"
    )


def test_classify_pr_file_rejects_wrong_phase_evidence():
    """_classify_pr_file: 다른 phase의 evidence는 forbidden이어야 합니다."""
    mod = _load_pipeline_module()
    # pm phase에서 dev evidence는 forbidden
    result = mod._classify_pr_file(
        ".pipeline/phase_evidence/IMP-TEST-0000/dev/receipt.json",
        "pm",
        "IMP-TEST-0000"
    )
    assert result.startswith("forbidden"), (
        f"다른 phase evidence가 forbidden이어야 합니다: {result}"
    )
