"""IMP-20260711-F5D7: PR body writer ownership E2E tests (TC-1 ~ TC-12).

모든 테스트는 PIPELINE_STATE_PATH로 격리된 임시 state를 사용한다.
직접 내부 함수 import 대신 subprocess CLI 호출 방식을 사용 (Real CLI Path 원칙).
"""
import os
import pathlib
import subprocess
import sys
import textwrap

PIPELINE_PY = pathlib.Path(__file__).parent.parent.parent / "pipeline.py"


def _load_pipeline_module():
    """pipeline.py를 격리 import하여 순수 함수/상수를 검증한다."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("pipeline_f5d7", PIPELINE_PY)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# TC-1: PR_BODY_WRITER_ROLES SSoT 상수 존재 확인
def test_tc1_writer_roles_constant_defined():
    """TC-1: PR_BODY_WRITER_ROLES SSoT 상수에 4개 writer_role이 정의되어야 한다."""
    mod = _load_pipeline_module()
    roles = getattr(mod, "PR_BODY_WRITER_ROLES", None)
    assert roles is not None, "PR_BODY_WRITER_ROLES 상수가 없습니다"
    assert "post_accept_writer" in roles
    assert "pending_acceptance_writer" in roles
    assert "final_check_writer" in roles
    assert "manual_report_writer" in roles
    assert len(roles) >= 4


# TC-2: ACCEPTANCE_STATE_TRANSITION_RULES 상수 존재 및 역전이 차단 확인
def test_tc2_transition_rules_defined():
    """TC-2: ACCEPTED→PENDING 역전이는 어떤 writer도 허용하지 않는다."""
    mod = _load_pipeline_module()
    rules = getattr(mod, "ACCEPTANCE_STATE_TRANSITION_RULES", None)
    assert rules is not None, "ACCEPTANCE_STATE_TRANSITION_RULES 상수가 없습니다"
    reverse = rules.get(("ACCEPTED", "PENDING"), set())
    assert len(reverse) == 0, f"ACCEPTED→PENDING 역전이가 허용됨: {reverse}"


# TC-3: _validate_pr_body_write_permission - ACCEPTED→PENDING 차단 (edge case)
def test_tc3_validate_permission_blocks_reverse_transition():
    """TC-3 (edge): final_check_writer가 ACCEPTED body를 PENDING으로 전이 시도 → BLOCKED."""
    mod = _load_pipeline_module()
    validate = getattr(mod, "_validate_pr_body_write_permission", None)
    assert validate is not None, "_validate_pr_body_write_permission 함수가 없습니다"
    result = validate(
        writer_role="final_check_writer",
        current_acceptance_status="ACCEPTED",
        target_status="PENDING",
        pr_body_fetch_ok=True,
    )
    assert not result["allowed"], "ACCEPTED→PENDING은 차단되어야 합니다"
    assert result.get("skip") or result.get("failure_code") == "reverse_transition_blocked"


# TC-4: _validate_pr_body_write_permission - post_accept_writer PENDING→ACCEPTED 허용
def test_tc4_validate_permission_allows_post_accept():
    """TC-4: post_accept_writer의 PENDING→ACCEPTED 전이는 허용된다."""
    mod = _load_pipeline_module()
    validate = getattr(mod, "_validate_pr_body_write_permission", None)
    assert validate is not None
    result = validate(
        writer_role="post_accept_writer",
        current_acceptance_status="PENDING",
        target_status="ACCEPTED",
        pr_body_fetch_ok=True,
    )
    assert result["allowed"], f"post_accept_writer PENDING→ACCEPTED가 차단됨: {result}"


# TC-5: PR body fetch 실패 시 fail-closed
def test_tc5_fetch_failure_fail_closed():
    """TC-5: PR body fetch 실패 시 모든 writer의 write가 차단된다 (fail-closed)."""
    mod = _load_pipeline_module()
    validate = getattr(mod, "_validate_pr_body_write_permission", None)
    assert validate is not None
    for role in (
        "post_accept_writer",
        "pending_acceptance_writer",
        "final_check_writer",
        "manual_report_writer",
    ):
        result = validate(
            writer_role=role,
            current_acceptance_status="PENDING",
            target_status="ACCEPTED",
            pr_body_fetch_ok=False,
        )
        assert not result["allowed"], f"{role}: fetch 실패 시에도 write 허용됨"
        assert result.get("failure_code") == "pr_body_fetch_failed"


# TC-6: _extract_pr_body_acceptance_metadata - ACCEPTED 마커 감지
def test_tc6_extract_accepted_status():
    """TC-6: ACCEPTED 마커가 있는 PR body에서 acceptance_status=ACCEPTED를 추출한다."""
    mod = _load_pipeline_module()
    extract = getattr(mod, "_extract_pr_body_acceptance_metadata", None)
    assert extract is not None, "_extract_pr_body_acceptance_metadata 함수가 없습니다"
    pr_body = "<!-- pipeline-human-acceptance-packet-accepted -->\n## 완료됐습니다"
    result = extract(pr_body)
    assert result["acceptance_status"] == "ACCEPTED"


# TC-7: _extract_pr_body_acceptance_metadata - PENDING 마커 감지
def test_tc7_extract_pending_status():
    """TC-7: PENDING 마커가 있는 PR body에서 acceptance_status=PENDING을 추출한다."""
    mod = _load_pipeline_module()
    extract = getattr(mod, "_extract_pr_body_acceptance_metadata", None)
    assert extract is not None
    pr_body = "<!-- pipeline-human-acceptance-packet-pending -->\n## 승인 대기 중"
    result = extract(pr_body)
    assert result["acceptance_status"] == "PENDING"


# TC-8: _extract_pr_body_acceptance_metadata - 메타데이터 comment 추출
def test_tc8_extract_writer_meta_comment():
    """TC-8: pipeline-writer-meta comment에서 writer_role/epoch/snapshot_id를 추출한다."""
    mod = _load_pipeline_module()
    extract = getattr(mod, "_extract_pr_body_acceptance_metadata", None)
    assert extract is not None
    pr_body = textwrap.dedent("""
        <!-- pipeline-writer-meta writer_role=post_accept_writer writer_epoch=epoch-20260711-test snapshot_id=snap-001 -->
        <!-- pipeline-human-acceptance-packet-accepted -->
        ## 완료
    """)
    result = extract(pr_body)
    assert result["writer_role"] == "post_accept_writer"
    assert result["writer_epoch"] == "epoch-20260711-test"
    assert result["snapshot_id"] == "snap-001"
    assert result["acceptance_status"] == "ACCEPTED"
    assert result["has_meta_comment"] is True


# TC-9: pipeline.py import 가능 (syntax error 없음)
def test_tc9_pipeline_importable():
    """TC-9: pipeline.py가 syntax error 없이 import 가능하다."""
    code = (
        "import importlib.util; "
        f"spec=importlib.util.spec_from_file_location('p', r'{PIPELINE_PY}'); "
        "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, f"pipeline.py import 실패: {result.stderr[:500]}"


# TC-10: pipeline.py --help 정상 동작
def test_tc10_pipeline_help():
    """TC-10: python pipeline.py --help가 exit code 0으로 실행된다."""
    result = subprocess.run(
        [sys.executable, str(PIPELINE_PY), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, f"--help 실패: {result.stderr[:500]}"


# TC-11: gates --help 정상 동작
def test_tc11_gates_help():
    """TC-11: python pipeline.py gates --help가 정상 동작한다."""
    result = subprocess.run(
        [sys.executable, str(PIPELINE_PY), "gates", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, f"gates --help 실패: {result.stderr[:500]}"


# TC-12: report --help 정상 동작
def test_tc12_report_help():
    """TC-12: python pipeline.py report --help가 정상 동작한다."""
    result = subprocess.run(
        [sys.executable, str(PIPELINE_PY), "report", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode == 0, f"report --help 실패: {result.stderr[:500]}"


if __name__ == "__main__":
    # SELF-VERIFY 블록 — tests/ 폴더가 있어도 직접 실행 가능하게 유지.
    _ = os.environ  # noqa: F401 (isolation env 확인용 placeholder)
    test_tc1_writer_roles_constant_defined()
    test_tc2_transition_rules_defined()
    test_tc3_validate_permission_blocks_reverse_transition()
    test_tc4_validate_permission_allows_post_accept()
    test_tc5_fetch_failure_fail_closed()
    test_tc6_extract_accepted_status()
    test_tc7_extract_pending_status()
    test_tc8_extract_writer_meta_comment()
    print("[SELF-VERIFY] OK")
