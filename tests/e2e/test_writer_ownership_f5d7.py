"""IMP-20260711-F5D7: PR body writer ownership E2E tests (TC-1 ~ TC-16).

모든 테스트는 PIPELINE_STATE_PATH로 격리된 임시 state를 사용한다.
직접 내부 함수 import 대신 subprocess CLI 호출 방식을 사용 (Real CLI Path 원칙).
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import textwrap

PIPELINE_PY = pathlib.Path(__file__).parent.parent.parent / "pipeline.py"
CI_YML = pathlib.Path(__file__).parent.parent.parent / ".github" / "workflows" / "ci.yml"


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


# TC-13: report update-pr-body — PR body fetch 실패 시 CLI가 BLOCKED(exit 1) (real CLI path)
def test_tc13_update_pr_body_fetch_fail_blocked():
    """TC-13: PR body fetch 실패 시 report update-pr-body가 BLOCKED(exit 1)되는지 검증.

    PIPELINE_FAKE_PR_FETCH_FAIL=1 훅으로 gh 없이도 fetch 실패 경로를 강제한다.
    _validate_pr_body_write_permission이 pr_body_fetch_failed(allowed=False, skip=False)를
    반환하면 CLI가 sys.exit(1)로 BLOCKED해야 한다(이전 버그: skip=True만 return하고 write 진행).
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        # _packet_output_path()는 cwd 기준이므로 cwd에 packet md를 만들어 존재 조건을 만족시킨다.
        (tmp_path / "human_acceptance_packet.md").write_text(
            "[검증용 메타데이터]\npipeline_id: IMP-TEST\n", encoding="utf-8"
        )
        env = dict(os.environ)
        env["PIPELINE_STATE_PATH"] = str(tmp_path / "isolated_state.json")
        env["PIPELINE_FAKE_PR_FETCH_FAIL"] = "1"
        env.pop("PIPELINE_FAKE_PR_BODY", None)
        result = subprocess.run(
            [sys.executable, str(PIPELINE_PY), "report", "update-pr-body"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, cwd=str(tmp_path), env=env,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        assert result.returncode == 1, (
            f"fetch 실패 시 BLOCKED(exit 1)여야 함 — returncode={result.returncode}\n{combined[:800]}"
        )
        assert "BLOCKED" in combined, f"BLOCKED 미표시: {combined[:800]}"
        assert "pr_body_fetch_failed" in combined, f"failure_code 미표시: {combined[:800]}"


# TC-14: manual_report_writer는 ACCEPTED PR body를 PENDING으로 덮을 수 없다
def test_tc14_manual_writer_cannot_overwrite_accepted():
    """TC-14: ACCEPTED 상태 PR body를 manual_report_writer가 PENDING으로 덮을 수 없음.

    (1) _validate_pr_body_write_permission이 ACCEPTED→PENDING을 허용하지 않는다(allowed=False).
    (2) CLI(report update-pr-body)가 ACCEPTED body를 감지하면 write하지 않고 skip 안내 후 종료한다.
    """
    mod = _load_pipeline_module()
    validate = getattr(mod, "_validate_pr_body_write_permission", None)
    assert validate is not None
    # (1) 함수 수준 — manual_report_writer는 ACCEPTED body를 PENDING으로 되돌릴 수 없다.
    perm = validate(
        writer_role="manual_report_writer",
        current_acceptance_status="ACCEPTED",
        target_status="PENDING",
        pr_body_fetch_ok=True,
    )
    assert not perm["allowed"], f"manual_report_writer가 ACCEPTED를 덮을 수 있음: {perm}"

    # (2) CLI 수준 — fake ACCEPTED body 주입 시 write하지 않고 skip한다.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        (tmp_path / "human_acceptance_packet.md").write_text(
            "[검증용 메타데이터]\npipeline_id: IMP-TEST\n", encoding="utf-8"
        )
        accepted_body = (
            "<!-- PIPELINE_FINAL_PACKET_START -->\n"
            "<!-- pipeline-writer-meta writer_role=post_accept_writer "
            "writer_epoch=epoch-x snapshot_id=snap-1 acceptance_status=ACCEPTED -->\n"
            "[검증용 메타데이터]\nacceptance_display: ACCEPTED\n"
            "<!-- PIPELINE_FINAL_PACKET_END -->\n"
        )
        body_file = tmp_path / "fake_pr_body.txt"
        body_file.write_text(accepted_body, encoding="utf-8")
        env = dict(os.environ)
        env["PIPELINE_STATE_PATH"] = str(tmp_path / "isolated_state.json")
        env["PIPELINE_FAKE_PR_BODY"] = str(body_file)
        env.pop("PIPELINE_FAKE_PR_FETCH_FAIL", None)
        result = subprocess.run(
            [sys.executable, str(PIPELINE_PY), "report", "update-pr-body"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, cwd=str(tmp_path), env=env,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        # ACCEPTED→PENDING 역전이는 graceful skip(exit 0) + skip 안내이며, write는 발생하지 않는다.
        assert result.returncode == 0, (
            f"ACCEPTED skip은 exit 0이어야 함 — returncode={result.returncode}\n{combined[:800]}"
        )
        assert "ACCEPTED" in combined, f"ACCEPTED skip 안내 미표시: {combined[:800]}"


# TC-15: 생성된 packet content에 writer metadata가 실제로 포함된다
def test_tc15_writer_metadata_in_generated_packet():
    """TC-15: publish 경로가 생성하는 packet content에 pipeline-writer-meta 블록이 포함된다.

    _build_final_packet_content(문제 2에서 metadata가 0회 포함되던 함수)에 writer 파라미터를
    전달하면 실제 packet 본문에 writer-meta가 들어가고, _materialize_acceptance_snapshot이
    그 파라미터를 실제로 전달(wiring)한다는 것을 검증한다.
    """
    import inspect

    mod = _load_pipeline_module()
    build = getattr(mod, "_build_final_packet_content", None)
    extract = getattr(mod, "_extract_pr_body_acceptance_metadata", None)
    materialize = getattr(mod, "_materialize_acceptance_snapshot", None)
    assert build is not None and extract is not None and materialize is not None

    evidence = {
        "pipeline_id": "IMP-20260711-F5D7",
        "pr_url": "",
        "pr_head_sha": "deadbeef",
        "ci_run_id": "12345",
        "changed_files": ["pipeline.py"],
        "gate_status": {"technical": "PASS", "oracle": "PASS"},
        "oracle_summary": {"status": "PASS", "case_count": 3, "passed_count": 3},
    }
    # post-accept publish 경로 — post_accept_writer + ACCEPTED.
    content = build(
        evidence,
        acceptance_status_override="ACCEPTED",
        writer_role="post_accept_writer",
        writer_epoch="epoch-20260711-tc15",
        snapshot_id="snap-tc15",
    )
    assert "pipeline-writer-meta" in content, "packet에 writer-meta가 없습니다(문제 2 미해결)"
    assert "writer_role=post_accept_writer" in content
    assert "acceptance_status=ACCEPTED" in content
    meta = extract(content)
    assert meta["writer_role"] == "post_accept_writer"
    assert meta["acceptance_status"] == "ACCEPTED"

    # pending publish 경로 — pending_acceptance_writer + PENDING.
    content_pending = build(
        evidence,
        acceptance_status_override="승인 대기 중 (PENDING)",
        writer_role="pending_acceptance_writer",
        writer_epoch="epoch-20260711-tc15b",
        snapshot_id="snap-tc15b",
    )
    assert "writer_role=pending_acceptance_writer" in content_pending
    assert "acceptance_status=PENDING" in content_pending

    # wiring 검증 — _materialize_acceptance_snapshot이 _build_final_packet_content에 writer 파라미터를
    # 실제로 전달한다(문제 2의 핵심: 호출부에서 값이 전달되지 않던 것을 수정).
    src = inspect.getsource(materialize)
    assert "writer_role=writer_role" in src, "materialize가 writer_role을 전달하지 않습니다"
    assert "snapshot_id=(snapshot_id if writer_role else None)" in src


# TC-16: ci.yml final-check writer가 ACCEPTED PR body를 감지하여 skip한다
def test_tc16_ci_writer_skips_on_accepted_pr_body():
    """TC-16: ci.yml의 ACCEPTED 감지 로직이 실제 PR body metadata 패턴에 맞는지 검증.

    문제 3: 이전 ci.yml은 comment 전용 marker(pipeline-human-acceptance-packet-accepted)를
    PR body에서 찾아 skip이 발동하지 않았다. 이제 PR body FINAL_PACKET 블록의 writer-meta
    (acceptance_status=ACCEPTED)를 기준으로 한다.
    """
    assert CI_YML.exists(), "ci.yml이 없습니다"
    ci_text = CI_YML.read_text(encoding="utf-8", errors="replace")

    # (1) ci.yml이 PR body 기준(writer-meta acceptance_status=ACCEPTED)으로 skip을 판정한다.
    assert "pipeline-writer-meta" in ci_text
    assert "acceptance_status=ACCEPTED" in ci_text
    assert "SKIP_FINAL_CHECK_WRITER=true" in ci_text
    # comment 전용 marker를 PR body 기준 ACCEPTED 판정에 단독 사용하지 않는다(회귀 방지).
    assert '$prBody -match "pipeline-human-acceptance-packet-accepted"' not in ci_text

    # (2) ci.yml이 사용하는 writer-meta 정규식을 Python으로 재현하여, 실제 post-accept PR body는
    #     매치하고 PENDING body는 매치하지 않는지 검증한다(SSoT: _build_final_packet_content 산출물).
    mod = _load_pipeline_module()
    build = getattr(mod, "_build_final_packet_content", None)
    replace_block = getattr(mod, "_replace_pr_body_packet_block", None)
    assert build is not None and replace_block is not None
    evidence = {
        "pipeline_id": "IMP-20260711-F5D7",
        "pr_url": "", "pr_head_sha": "abc", "ci_run_id": "9",
        "changed_files": ["pipeline.py"],
        "gate_status": {"technical": "PASS"},
    }
    accepted_content = build(
        evidence, acceptance_status_override="ACCEPTED",
        writer_role="post_accept_writer", writer_epoch="epoch-x", snapshot_id="snap-1",
    )
    accepted_body = replace_block("", accepted_content)
    pending_content = build(
        evidence, acceptance_status_override="승인 대기 중 (PENDING)",
        writer_role="pending_acceptance_writer", writer_epoch="epoch-y", snapshot_id="snap-2",
    )
    pending_body = replace_block("", pending_content)

    # ci.yml의 PowerShell 정규식과 동일한 패턴(줄 안에서 acceptance_status=ACCEPTED).
    ci_pattern = r"pipeline-writer-meta[^\r\n]*acceptance_status=ACCEPTED"
    assert re.search(ci_pattern, accepted_body), "ACCEPTED PR body가 ci.yml 패턴에 매치되지 않음"
    assert not re.search(ci_pattern, pending_body), "PENDING PR body가 잘못 매치됨(오탐)"


# TC-17: manual_report_writer가 UNKNOWN body(fresh/미발급 PR)에서 exit 0으로 성공하는 positive path
def test_tc17_manual_report_writer_unknown_body_success():
    """TC-17: manual_report_writer + UNKNOWN body(마커 없음) → exit 0(정상 전이 허용).

    회귀 방지: ACCEPTANCE_STATE_TRANSITION_RULES에 ("UNKNOWN","PENDING") 항목이 없으면
    기본값 set()으로 인해 manual_report_writer가 transition_not_allowed로 BLOCKED(exit 1)된다.
    수정 후 UNKNOWN→PENDING 전이가 허용되어, fresh PR body에서 report update-pr-body가
    정상 진행(gh 부재 시 graceful skip, exit 0)한다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        # _packet_output_path()는 cwd 기준이므로 packet md를 만들어 존재 조건을 만족시킨다.
        (tmp_path / "human_acceptance_packet.md").write_text(
            "[검증용 메타데이터]\npipeline_id: IMP-TEST\n", encoding="utf-8"
        )
        # 마커가 없는 fresh PR body → _extract_pr_body_acceptance_metadata가 UNKNOWN을 반환.
        unknown_body = "# 작업 요약\n내용\n\n## 사용자가 확인할 결과물\n결과\n"
        body_file = tmp_path / "fake_pr_body.txt"
        body_file.write_text(unknown_body, encoding="utf-8")
        env = dict(os.environ)
        env["PIPELINE_STATE_PATH"] = str(tmp_path / "isolated_state.json")
        env["PIPELINE_FAKE_PR_BODY"] = str(body_file)  # fetch 성공(UNKNOWN body 주입)
        env.pop("PIPELINE_FAKE_PR_FETCH_FAIL", None)
        # gh 설치 여부와 무관하게 결정적이도록 존재하지 않는 gh 실행 경로를 강제
        # → _gh_available()=False → _gh_edit_pr_body()가 graceful하게 False 반환 → exit 0.
        env["PIPELINE_GH_EXECUTABLE"] = str(tmp_path / "no_such_gh_binary")
        result = subprocess.run(
            [sys.executable, str(PIPELINE_PY), "report", "update-pr-body"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, cwd=str(tmp_path), env=env,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        # UNKNOWN→PENDING 전이는 manual_report_writer에 허용되므로 BLOCKED되지 않는다.
        assert result.returncode == 0, (
            f"TC-17: manual_report_writer UNKNOWN→PENDING은 exit 0이어야 함 — "
            f"returncode={result.returncode}\n{combined[:800]}"
        )
        # 회귀 시 나타나던 writer ownership BLOCKED 메시지가 없어야 한다.
        assert "transition_not_allowed" not in combined, (
            f"TC-17: UNKNOWN→PENDING이 잘못 차단됨(transition_not_allowed): {combined[:800]}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# IMP-20260711-F5D7 MT-12 rework: _write_pr_body_packet_block_with_writer_guard
# 통합 helper 및 _materialize_acceptance_snapshot Phase 4 writer guard 배선 검증.
# ─────────────────────────────────────────────────────────────────────────────

_ACCEPTED_BODY = (
    "<!-- PIPELINE_FINAL_PACKET_START -->\n"
    "<!-- pipeline-human-acceptance-packet-accepted -->\n"
    "<!-- pipeline-writer-meta writer_role=post_accept_writer writer_epoch=epoch-x "
    "snapshot_id=snap-1 acceptance_status=ACCEPTED -->\n"
    "[검증용 메타데이터]\nacceptance_display: ACCEPTED\n"
    "<!-- PIPELINE_FINAL_PACKET_END -->\n"
)
_PENDING_BODY = (
    "<!-- PIPELINE_FINAL_PACKET_START -->\n"
    "<!-- pipeline-human-acceptance-packet-pending -->\n"
    "[검증용 메타데이터]\nacceptance_display: 승인 대기 중 (PENDING)\n"
    "<!-- PIPELINE_FINAL_PACKET_END -->\n"
)
_UNKNOWN_BODY = "# 작업 요약\n내용\n\n## 사용자가 확인할 결과물\n결과\n"


def _guard_with_edit_counter(mod, **kwargs):
    """helper를 호출하되 _gh_edit_pr_body 호출 횟수를 세는 카운터로 monkeypatch한다.

    Returns:
        (result_dict, edit_call_count) 튜플.
    """
    guard = getattr(mod, "_write_pr_body_packet_block_with_writer_guard", None)
    assert guard is not None, "_write_pr_body_packet_block_with_writer_guard 함수가 없습니다"
    _calls = {"n": 0}

    def _fake_edit(new_body):
        _calls["n"] += 1
        return True

    _orig = mod._gh_edit_pr_body
    mod._gh_edit_pr_body = _fake_edit
    try:
        result = guard(**kwargs)
    finally:
        mod._gh_edit_pr_body = _orig
    return result, _calls["n"]


# TC-A: ACCEPTED body + target=PENDING → _gh_edit_pr_body 미호출 (역전이 방지)
def test_tca_accepted_body_pending_target_no_write():
    """TC-A: 이미 ACCEPTED인 PR body에 pending writer가 PENDING을 쓰려 하면 write되지 않는다."""
    mod = _load_pipeline_module()
    result, edit_calls = _guard_with_edit_counter(
        mod,
        current_body=_ACCEPTED_BODY,
        packet_content=_PENDING_BODY,
        writer_role="pending_acceptance_writer",
        target_status="PENDING",
    )
    assert not result["allowed"], f"ACCEPTED→PENDING이 허용됨: {result}"
    assert result.get("skip") is True, f"역전이는 skip이어야 함: {result}"
    assert result["pr_updated"] is False
    assert edit_calls == 0, f"_gh_edit_pr_body가 호출됨(역전이인데 write 발생): {edit_calls}"


# TC-B: pending path → writer guard 통과하여 PR body 업데이트
def test_tcb_pending_writer_unknown_body_writes():
    """TC-B: pending_acceptance_writer가 UNKNOWN body에 PENDING을 쓰면 write가 발생한다."""
    mod = _load_pipeline_module()
    result, edit_calls = _guard_with_edit_counter(
        mod,
        current_body=_UNKNOWN_BODY,
        packet_content=_PENDING_BODY,
        writer_role="pending_acceptance_writer",
        target_status="PENDING",
    )
    assert result["allowed"], f"UNKNOWN→PENDING pending writer가 차단됨: {result}"
    assert result["pr_updated"] is True
    assert edit_calls == 1, f"_gh_edit_pr_body가 1회 호출되어야 함: {edit_calls}"


# TC-C: accepted path → writer guard 통과하여 PR body 업데이트
def test_tcc_post_accept_writer_pending_to_accepted_writes():
    """TC-C: post_accept_writer가 PENDING body를 ACCEPTED로 승격하면 write가 발생한다."""
    mod = _load_pipeline_module()
    result, edit_calls = _guard_with_edit_counter(
        mod,
        current_body=_PENDING_BODY,
        packet_content=_ACCEPTED_BODY,
        writer_role="post_accept_writer",
        target_status="ACCEPTED",
    )
    assert result["allowed"], f"PENDING→ACCEPTED post_accept_writer가 차단됨: {result}"
    assert result["pr_updated"] is True
    assert edit_calls == 1, f"_gh_edit_pr_body가 1회 호출되어야 함: {edit_calls}"


# TC-D: writer_role 빈 문자열("")로 호출 → BLOCKED (write 없음)
def test_tcd_empty_writer_role_blocked():
    """TC-D: writer_role=""는 unknown_writer_role로 BLOCKED되고 write하지 않는다."""
    mod = _load_pipeline_module()
    result, edit_calls = _guard_with_edit_counter(
        mod,
        current_body=_UNKNOWN_BODY,
        packet_content=_PENDING_BODY,
        writer_role="",
        target_status="PENDING",
    )
    assert not result["allowed"], f"빈 writer_role이 허용됨: {result}"
    assert result.get("skip") is False, "빈 role은 skip이 아니라 BLOCKED여야 함"
    assert result.get("failure_code") == "unknown_writer_role"
    assert result["pr_updated"] is False
    assert edit_calls == 0, f"빈 role인데 write 발생: {edit_calls}"


# TC-E: current_body=None → 모든 쓰기 경로 fail-closed (pr_updated=False)
def test_tce_none_body_fail_closed():
    """TC-E: current_body=None(fetch 실패)이면 어떤 writer도 write하지 못한다(fail-closed)."""
    mod = _load_pipeline_module()
    for role in (
        "pending_acceptance_writer",
        "post_accept_writer",
        "final_check_writer",
        "manual_report_writer",
    ):
        result, edit_calls = _guard_with_edit_counter(
            mod,
            current_body=None,
            packet_content=_PENDING_BODY,
            writer_role=role,
            target_status="PENDING",
        )
        assert not result["allowed"], f"{role}: None body인데 write 허용됨"
        assert result.get("failure_code") == "pr_body_fetch_failed"
        assert result["pr_updated"] is False
        assert edit_calls == 0, f"{role}: None body인데 write 발생: {edit_calls}"


# TC-E2: _materialize_acceptance_snapshot Phase 4가 직접 write 대신 writer guard를 배선한다 (wiring)
def test_tce2_materialize_phase4_wired_to_writer_guard():
    """TC-E2: _materialize_acceptance_snapshot이 _get_pr_body_text() or "" 직접 write를 제거하고
    _write_pr_body_packet_block_with_writer_guard를 경유하는지 소스로 검증한다(회귀 방지)."""
    import inspect

    mod = _load_pipeline_module()
    materialize = getattr(mod, "_materialize_acceptance_snapshot", None)
    assert materialize is not None
    src = inspect.getsource(materialize)
    # Phase 4가 writer guard 관문을 경유한다.
    assert "_write_pr_body_packet_block_with_writer_guard" in src, (
        "Phase 4가 writer guard를 경유하지 않습니다(직접 write 잔존 가능)"
    )
    # 직접 write 패턴(_get_pr_body_text() or "" → _gh_edit)이 제거되었다.
    assert '_get_pr_body_text() or ""' not in src, (
        "직접 write 패턴 _get_pr_body_text() or \"\" 가 아직 남아 있습니다"
    )


# TC-F: gates request-accept 흐름 내 approval_request_message 단일 출력 불변식
def test_tcf_approval_message_emitted_once():
    """TC-F: _cmd_gates_request_accept에서 approval_request_message는 stdout에 최대 1회만 출력된다.

    재사용(read-only) 경로와 신규 발급 경로는 상호배타이며(재사용 경로는 print 후 return),
    각 print 사이트는 정확히 1회씩만 존재해야 한다(이중 출력 회귀 방지).
    """
    import inspect

    mod = _load_pipeline_module()
    cmd = getattr(mod, "_cmd_gates_request_accept", None)
    assert cmd is not None, "_cmd_gates_request_accept 함수가 없습니다"
    src = inspect.getsource(cmd)
    # 재사용 경로의 message print 사이트 1회.
    reuse_prints = src.count('print(_reuse_approval_out["approval_request_message"])')
    # 신규 발급 경로의 message print 사이트 1회.
    new_prints = src.count("print(_approval_msg)")
    assert reuse_prints == 1, f"재사용 경로 approval message print가 {reuse_prints}회(1회여야 함)"
    assert new_prints == 1, f"신규 경로 approval message print가 {new_prints}회(1회여야 함)"
    # 재사용 경로는 print 후 return하여 신규 경로 print와 동시에 실행되지 않는다.
    reuse_idx = src.find('print(_reuse_approval_out["approval_request_message"])')
    new_idx = src.find("print(_approval_msg)")
    assert reuse_idx != -1 and new_idx != -1
    between = src[reuse_idx:new_idx]
    assert "return" in between, (
        "재사용 경로 print와 신규 경로 print 사이에 return이 없어 이중 출력 가능"
    )


if __name__ == "__main__":
    # SELF-VERIFY 블록 — tests/ 폴더가 있어도 직접 실행 가능하게 유지.
    _ = os.environ  # noqa: F401 (isolation env 확인용 placeholder)
    test_tca_accepted_body_pending_target_no_write()
    test_tcb_pending_writer_unknown_body_writes()
    test_tcc_post_accept_writer_pending_to_accepted_writes()
    test_tcd_empty_writer_role_blocked()
    test_tce_none_body_fail_closed()
    test_tce2_materialize_phase4_wired_to_writer_guard()
    test_tcf_approval_message_emitted_once()
    test_tc1_writer_roles_constant_defined()
    test_tc2_transition_rules_defined()
    test_tc3_validate_permission_blocks_reverse_transition()
    test_tc4_validate_permission_allows_post_accept()
    test_tc5_fetch_failure_fail_closed()
    test_tc6_extract_accepted_status()
    test_tc7_extract_pending_status()
    test_tc8_extract_writer_meta_comment()
    test_tc13_update_pr_body_fetch_fail_blocked()
    test_tc14_manual_writer_cannot_overwrite_accepted()
    test_tc15_writer_metadata_in_generated_packet()
    test_tc16_ci_writer_skips_on_accepted_pr_body()
    test_tc17_manual_report_writer_unknown_body_success()
    print("[SELF-VERIFY] OK")
