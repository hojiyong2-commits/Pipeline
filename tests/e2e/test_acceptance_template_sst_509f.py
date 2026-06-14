"""
IMP-20260614-509F: User Acceptance 안내 템플릿 SSoT 정합성 테스트
TC-1~TC-8 E2E 테스트 (+ renderer 분리 검증)

acceptance template 렌더링은 CLI가 아닌 내부 함수 수준에서 검증하므로,
subprocess 대신 pipeline.py 함수를 직접 import 하여 테스트한다.
"""
import inspect
import os
import re
import sys

import pytest

# pipeline.py를 직접 임포트
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

import pipeline  # noqa: E402


def _base_display_model():
    """렌더러 테스트용 표준 display model (환경 비의존)."""
    return {
        "pipeline_id": "IMP-20260614-509F",
        "pr_url": "https://github.com/hojiyong2-commits/Pipeline/pull/123",
        "github_actions_url": "https://github.com/hojiyong2-commits/Pipeline/actions/runs/999",
        "ci_run_id": "999",
        "evidence_path": "human_acceptance_packet.md",
        "gates": {
            "technical": "PASS",
            "oracle": "PASS",
            "github_ci": "PASS",
            "acceptance": "PENDING",
        },
        "acceptance_display": "PENDING",
        "requirements_summary": {"total": 3, "passed": 3, "failed": 0},
        "evidence_integrity_summary": "OK (protected:2, tracked:2, pr_included:2)",
        "workspace_hygiene_summary": {
            "status": "OK",
            "blocking_items": [],
            "cleanup_only_items": ["build_report.xml"],
        },
        "approval_code": "ACCEPT-IMP-20260614-509F-ABCD1234",
        "reject_example": "REJECT-IMP-20260614-509F-ABCD1234: 이유",
        "user_checklist": [
            "PR 링크를 연다.",
            "GitHub Actions 자동 검사가 성공인지 본다.",
        ],
        "changed_files": ["pipeline.py", "tests/e2e/test_acceptance_template_sst_509f.py"],
        "ac_table": [
            {"ac_id": "AC-1", "requirement": "x", "result": "PASS", "user_visible": True}
        ],
    }


# ─── TC-1 ───────────────────────────────────────────────────────────────────
def test_tc1_display_model_fields():
    """_build_acceptance_display_model 반환에 필수 키가 모두 존재한다."""
    model = pipeline._build_acceptance_display_model({}, None)
    for key in (
        "pipeline_id",
        "pr_url",
        "github_actions_url",
        "ci_run_id",
        "evidence_path",
        "gates",
        "acceptance_display",
        "requirements_summary",
        "evidence_integrity_summary",
        "workspace_hygiene_summary",
        "approval_code",
        "reject_example",
        "user_checklist",
    ):
        assert key in model, f"display model에 '{key}' 키가 없습니다."

    for gkey in ("technical", "oracle", "github_ci", "acceptance"):
        assert gkey in model["gates"], f"gates에 '{gkey}' 키가 없습니다."

    for rkey in ("total", "passed", "failed"):
        assert rkey in model["requirements_summary"], f"requirements_summary에 '{rkey}' 키가 없습니다."

    assert isinstance(model["user_checklist"], list)


# ─── TC-2 ───────────────────────────────────────────────────────────────────
def test_tc2_pending_comment_no_accepted_phrases():
    """pending 댓글에 완료 문구가 없고 pending 마커가 포함된다."""
    body = pipeline._render_pending_acceptance_comment(_base_display_model())
    for forbidden in ("ACCEPTED", "승인 완료", "배포 완료", "완료됐습니다"):
        assert forbidden not in body, f"pending 댓글에 금지 문구 '{forbidden}' 포함."
    assert "<!-- pipeline-human-acceptance-packet -->" in body
    assert "<!-- pipeline-human-acceptance-packet-pending -->" in body


# ─── TC-3 ───────────────────────────────────────────────────────────────────
def test_tc3_accepted_comment_no_pending_marker():
    """accepted 댓글에 pending 마커/승인 코드 입력 안내가 없고 accepted 마커가 포함된다."""
    model = _base_display_model()
    model["accepted_by"] = "hojiyong2-commits"
    model["accepted_at"] = "2026-06-14T00:00:00"
    model["acceptance_display"] = "ACCEPTED"
    body = pipeline._render_accepted_completion_comment(model)
    assert "<!-- pipeline-human-acceptance-packet-pending -->" not in body
    assert "아래 승인 코드를 입력" not in body
    assert "승인 코드를 입력해 주세요" not in body
    assert "<!-- pipeline-human-acceptance-packet-accepted -->" in body


# ─── TC-4 ───────────────────────────────────────────────────────────────────
def test_tc4_pr_body_md_state_consistency():
    """PR body renderer가 display model의 acceptance_display 상태를 포함한다."""
    for state in ("PENDING", "ACCEPTED", "REJECTED"):
        model = _base_display_model()
        model["acceptance_display"] = state
        pr_body = pipeline._render_pr_body_final_packet(model)
        assert state in pr_body, f"PR body에 acceptance_display='{state}'가 없습니다."
        # MD final packet과 PR body가 동일 표시 상태를 사용하는지 확인
        # (_display_model_from_evidence로 만든 model도 동일 키를 사용)
        assert f"승인 표시 상태: {state}" in pr_body


# ─── TC-5 ───────────────────────────────────────────────────────────────────
def test_tc5_requirements_summary_consistency():
    """requirements_summary는 음이 아닌 정수이며 passed+failed <= total 이다."""
    model = pipeline._build_acceptance_display_model({}, None)
    rs = model["requirements_summary"]
    assert isinstance(rs["total"], int)
    assert isinstance(rs["passed"], int)
    assert isinstance(rs["failed"], int)
    assert rs["total"] >= 0
    assert rs["passed"] >= 0
    assert rs["failed"] >= 0
    assert rs["passed"] + rs["failed"] <= rs["total"]


# ─── TC-6 ───────────────────────────────────────────────────────────────────
def test_tc6_evidence_workspace_hygiene_summary():
    """evidence_integrity_summary는 비어있지 않은 문자열, workspace_hygiene_summary는 키를 갖춘 dict."""
    model = pipeline._build_acceptance_display_model({}, None)
    eis = model["evidence_integrity_summary"]
    assert isinstance(eis, str)
    assert eis.strip() != ""
    whs = model["workspace_hygiene_summary"]
    assert isinstance(whs, dict)
    for key in ("status", "blocking_items", "cleanup_only_items"):
        assert key in whs, f"workspace_hygiene_summary에 '{key}' 키가 없습니다."


# ─── TC-7 ───────────────────────────────────────────────────────────────────
def test_tc7_no_hardcoded_test_counts():
    """renderer 함수 본문에 하드코딩된 '숫자+단위'(개/passed/tests/테스트/AC) 패턴이 없다."""
    pattern = re.compile(r"\b\d+\s*(개|passed|tests|테스트|AC)\b")
    for fn_name in (
        "_render_pending_acceptance_comment",
        "_render_accepted_completion_comment",
        "_render_pr_body_final_packet",
    ):
        fn = getattr(pipeline, fn_name)
        src = inspect.getsource(fn)
        # 주석 라인은 제외하고 검사 (코드 라인만)
        code_lines = []
        for line in src.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # 인라인 주석 제거 (단순)
            code_lines.append(line.split("#")[0])
        code_only = "\n".join(code_lines)
        match = pattern.search(code_only)
        assert match is None, (
            f"{fn_name}에 하드코딩된 숫자+단위 패턴 '{match.group(0) if match else ''}'이 있습니다."
        )


# ─── TC-8 ───────────────────────────────────────────────────────────────────
def test_tc8_workspace_hygiene_reflected():
    """workspace_hygiene 상태가 display model에 올바르게 반영된다."""
    state_ok = {"pipeline_id": "IMP-X", "workspace_hygiene": {"status": "OK"}}
    state_blocked = {
        "pipeline_id": "IMP-X",
        "workspace_hygiene": {
            "status": "BLOCKED",
            "blocking_items": ["failure_code=untracked_oracle_evidence: foo"],
        },
    }
    m_ok = pipeline._build_acceptance_display_model(state_ok, None)
    m_blocked = pipeline._build_acceptance_display_model(state_blocked, None)
    assert m_ok["workspace_hygiene_summary"]["status"] == "OK"
    assert m_blocked["workspace_hygiene_summary"]["status"] == "BLOCKED"
    assert len(m_blocked["workspace_hygiene_summary"]["blocking_items"]) == 1


def test_tc_renderer_separation():
    """렌더러/모델 함수들이 실제로 존재한다."""
    for fn_name in (
        "_build_acceptance_display_model",
        "_render_pending_acceptance_comment",
        "_render_accepted_completion_comment",
        "_render_pr_body_final_packet",
    ):
        assert hasattr(pipeline, fn_name), f"{fn_name} 함수가 존재하지 않습니다."
        assert callable(getattr(pipeline, fn_name))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
