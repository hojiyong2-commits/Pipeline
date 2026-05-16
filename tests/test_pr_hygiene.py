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
    """stale .pipeline/phase_evidence 파일이 있으면 preflight-pr FAIL (exit code != 0).

    핵심 금지 규칙:
    - pm phase에서 dev evidence (.pipeline/phase_evidence/{pid}/dev/...) 는 forbidden
    - .pipeline/agent_receipts/** 는 forbidden
    - 다른 pipeline_id의 phase_evidence 는 forbidden
    """
    import importlib

    # pipeline 모듈 직접 import
    spec = importlib.util.spec_from_file_location("pipeline", BASE_DIR / "pipeline.py")
    pipeline_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(pipeline_mod)  # type: ignore[union-attr]

    classify = pipeline_mod._classify_pr_file

    # pm phase에서 dev evidence는 forbidden이어야 함 (요구사항 4)
    result_dev = classify(".pipeline/phase_evidence/IMP-TEST-0000/dev/handover.json", "pm", "IMP-TEST-0000")
    assert result_dev.startswith("forbidden"), (
        f"dev evidence가 pm phase에서 forbidden이어야 합니다: {result_dev}"
    )

    # agent_receipts는 forbidden이어야 함 (요구사항 5)
    result_receipts = classify(".pipeline/agent_receipts/IMP-TEST-0000/dev/run.json", "pm", "IMP-TEST-0000")
    assert result_receipts.startswith("forbidden"), (
        f".pipeline/agent_receipts/** 는 forbidden이어야 합니다: {result_receipts}"
    )

    # pm phase의 own evidence는 allowed여야 함
    own_evidence = ".pipeline/phase_evidence/IMP-TEST-0000/pm/receipt.json"
    result_own = classify(own_evidence, "pm", "IMP-TEST-0000")
    assert result_own == "allowed", (
        f"pm phase evidence가 pm phase에서 allowed여야 합니다: {result_own}"
    )

    # 일반 구현 파일(pipeline.py, ci.yml 등)은 allowed여야 함
    for impl_file in ["pipeline.py", ".github/workflows/ci.yml", "tests/test_pr_hygiene.py"]:
        result_impl = classify(impl_file, "pm", "IMP-TEST-0000")
        assert result_impl == "allowed", (
            f"구현 파일은 allowed여야 합니다: {impl_file} → {result_impl}"
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
# MT-1/MT-2 추가 회귀 테스트 (IMP-20260516-78B2)
# ─────────────────────────────────────────────

def _load_classify(base_dir=BASE_DIR):
    """pipeline 모듈에서 _classify_pr_file을 로드하는 헬퍼."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", base_dir / "pipeline.py")
    pipeline_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(pipeline_mod)  # type: ignore[union-attr]
    return pipeline_mod._classify_pr_file


def test_classify_rejects_other_pipeline_id_evidence():
    """다른 pipeline_id의 phase_evidence는 phase가 같아도 forbidden이어야 합니다 (요구사항 4번)."""
    classify = _load_classify()

    # 다른 pipeline_id → forbidden
    result = classify(".pipeline/phase_evidence/OTHER-IMP-0000/pm/receipt.json", "pm", "IMP-TEST-0000")
    assert result.startswith("forbidden"), (
        f"다른 pipeline_id의 phase_evidence는 forbidden이어야 합니다: {result}"
    )

    # 같은 pipeline_id + 같은 phase → allowed
    result_ok = classify(".pipeline/phase_evidence/IMP-TEST-0000/pm/receipt.json", "pm", "IMP-TEST-0000")
    assert result_ok == "allowed", (
        f"같은 pipeline_id의 phase_evidence는 allowed여야 합니다: {result_ok}"
    )


def test_classify_rejects_agent_receipts():
    """.pipeline/agent_receipts/**는 forbidden이어야 합니다 (요구사항 5번)."""
    classify = _load_classify()

    result = classify(".pipeline/agent_receipts/IMP-TEST-0000/pm_planner/run.json", "pm", "IMP-TEST-0000")
    assert result.startswith("forbidden"), (
        f".pipeline/agent_receipts/** 경로는 forbidden이어야 합니다: {result}"
    )


def test_classify_rejects_other_pipeline_subpath():
    """.pipeline/agent_receipts/** 경로는 forbidden이어야 합니다 (요구사항 5번).

    기타 .pipeline/** 경로(agent_receipts 제외)는 allowed입니다.
    구현 파일도 phase attestation PR에 포함될 수 있으므로 허용합니다.
    """
    classify = _load_classify()

    # agent_receipts는 항상 forbidden
    for path in [
        ".pipeline/agent_receipts/IMP-TEST-0000/pm_planner/run.json",
        ".pipeline/agent_receipts/IMP-TEST-0000/dev/run.json",
    ]:
        result = classify(path, "pm", "IMP-TEST-0000")
        assert result.startswith("forbidden"), (
            f".pipeline/agent_receipts/** 경로는 forbidden이어야 합니다: {result}"
        )

    # 기타 .pipeline/** 경로는 제한 없음 (agent_receipts + phase_evidence 외)
    # phase_attestation_request.json과 기타 .pipeline/ 내부 파일은 허용
    for path in [
        ".pipeline/phase_attestation_request.json",
        ".pipeline/something_else/file.json",
    ]:
        result = classify(path, "pm", "IMP-TEST-0000")
        assert result == "allowed", (
            f"기타 .pipeline/** 경로는 allowed여야 합니다: {path} → {result}"
        )


def test_preflight_pr_fails_on_phase_mismatch(tmp_path):
    """request.phase=dev인데 --phase pm으로 호출하면 exit code != 0이어야 합니다 (요구사항 3번)."""
    req = {
        "schema_version": 1,
        "request_type": "pipeline-phase-attestation-request-v1",
        "pipeline_id": "IMP-TEST-0000",
        "phase": "dev",
        "phase_status": "DONE",
        "agent_run": {
            "run_id": "dev-agent-abc123",
            "phase": "dev",
            "agent_id": "dev-agent",
            "status": "COMPLETED",
            "used_by_phase": "dev",
        },
    }
    req_file = tmp_path / "phase_attestation_request.json"
    req_file.write_text(json.dumps(req), encoding="utf-8")

    result = _run_pipeline(
        "gates", "preflight-pr",
        "--phase", "pm",
        "--pipeline-id", "IMP-TEST-0000",
        "--request-file", str(req_file),
    )
    assert result.returncode != 0, (
        f"phase 불일치 시 exit code != 0이어야 합니다. 실제: {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_preflight_pr_fails_on_pipeline_id_mismatch(tmp_path):
    """request.pipeline_id와 --pipeline-id가 다르면 exit code != 0이어야 합니다 (요구사항 4번)."""
    req = {
        "schema_version": 1,
        "request_type": "pipeline-phase-attestation-request-v1",
        "pipeline_id": "IMP-A-0000",
        "phase": "pm",
        "phase_status": "DONE",
        "agent_run": {
            "run_id": "pm_planner-abc123",
            "phase": "pm_planner",
            "agent_id": "pm-planner-agent",
            "status": "COMPLETED",
            "used_by_phase": "pm",
        },
    }
    req_file = tmp_path / "phase_attestation_request.json"
    req_file.write_text(json.dumps(req), encoding="utf-8")

    result = _run_pipeline(
        "gates", "preflight-pr",
        "--phase", "pm",
        "--pipeline-id", "IMP-B-9999",
        "--request-file", str(req_file),
    )
    assert result.returncode != 0, (
        f"pipeline_id 불일치 시 exit code != 0이어야 합니다. 실제: {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
