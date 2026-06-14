# [Purpose]: BUG-20260614-B32A MT-2 — MT-1에서 추가된
#   _build_acceptance_display_model(state, evidence, packet_evidence=None)의
#   세 번째 파라미터(packet_evidence)가 evidence_integrity_summary/
#   workspace_hygiene_summary를 packet 기준으로 산출하는지, None일 때 기존 state
#   fallback(NOT_CHECKED 허용)을 보존하는지, pending/accepted 댓글 렌더러가 서로의
#   상태 마커/완료 문구를 침범하지 않는지 E2E로 검증한다.
# [Assumptions]: pipeline.py가 import 가능하고 세 대상 함수가 존재한다. gh CLI는
#   선택적이며 없으면 PR/CI 정보는 None으로 graceful degradation된다(테스트는
#   evidence_integrity/workspace_hygiene 요약만 단언하므로 네트워크 비의존).
# [Vulnerability & Risks]: 실제 GitHub 게시는 절대 호출하지 않는다(렌더러는 순수
#   문자열 반환). packet_evidence가 비어 있으면 packet 기준 NOT_CHECKED가 표시되는
#   것이 정상 동작이며 fallback과 구분된다.
# [Improvement]: packet_evidence 파생 요약 계산을 헬퍼로 추출하면 단언을
#   요약 문자열 파싱이 아닌 구조화 값으로 강화할 수 있다.
"""BUG-20260614-B32A: pending 댓글 SSoT 정합성 + packet_evidence 경로 E2E.

TC-1~TC-6은 pipeline.py 내부 함수를 직접 import하여 검증한다(렌더링/모델 산출은
CLI가 아닌 함수 수준 책임이므로 subprocess 불필요). 실제 GitHub 댓글 게시는
호출하지 않으며 fake/mock dict만 입력한다.
"""
import os
import sys

import pytest

# pipeline.py를 직접 임포트 (tests/e2e/ -> 프로젝트 루트).
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

import pipeline  # noqa: E402

_PIPELINE_ID = "BUG-20260614-B32A"


def _fake_packet_evidence_ok():
    """evidence_integrity OK + workspace_hygiene OK 인 fake packet evidence."""
    return {
        "evidence_integrity": {
            "status": "OK",
            "protected_evidence_count": 3,
            "tracked_count": 4,
            "pr_included_count": 3,
        },
        "workspace_hygiene": {
            "status": "OK",
            "blocking_items": [],
            "cleanup_only_items": ["build_report.xml"],
        },
    }


def _fake_packet_evidence_warn():
    """workspace_hygiene WARN(cleanup_only 다수) 인 fake packet evidence."""
    return {
        "evidence_integrity": {
            "status": "OK",
            "protected_evidence_count": 2,
            "tracked_count": 5,
            "pr_included_count": 2,
        },
        "workspace_hygiene": {
            "status": "WARN",
            "blocking_items": [],
            "cleanup_only_items": ["build_report.xml", "tmp_tc1_given.json"],
        },
    }


def _base_display_model():
    """렌더러 테스트용 표준 display model (환경 비의존)."""
    return {
        "pipeline_id": _PIPELINE_ID,
        "pr_url": "https://github.com/hojiyong2-commits/Pipeline/pull/602",
        "github_actions_url": "https://github.com/hojiyong2-commits/Pipeline/actions/runs/1000",
        "ci_run_id": "1000",
        "evidence_path": "human_acceptance_packet.md",
        "gates": {
            "technical": "PASS",
            "oracle": "PASS",
            "github_ci": "PASS",
            "acceptance": "PENDING",
        },
        "acceptance_display": "PENDING",
        "requirements_summary": {"total": 3, "passed": 3, "failed": 0},
        "evidence_integrity_summary": "OK (protected:3, tracked:4, pr_included:3)",
        "workspace_hygiene_summary": {
            "status": "OK",
            "blocking_items": [],
            "cleanup_only_items": ["build_report.xml"],
        },
        "approval_code": f"ACCEPT-{_PIPELINE_ID}-ABCD1234",
        "reject_example": f"REJECT-{_PIPELINE_ID}-ABCD1234: 이유",
        "user_checklist": [
            "PR 링크를 연다.",
            "GitHub Actions 자동 검사가 성공인지 본다.",
        ],
        "changed_files": ["pipeline.py", "tests/e2e/test_pending_comment_sst_b32a.py"],
        "ac_table": [
            {"ac_id": "AC-1", "requirement": "x", "result": "PASS", "user_visible": True}
        ],
    }


# ─── TC-1 (covers AC-1) ──────────────────────────────────────────────────────
def test_tc1_evidence_integrity_from_packet():
    """packet_evidence가 주어지면 evidence_integrity_summary가 packet 기준값이며
    NOT_CHECKED fallback이 아니다."""
    packet = _fake_packet_evidence_ok()
    model = pipeline._build_acceptance_display_model(
        {"pipeline_id": _PIPELINE_ID}, None, packet
    )
    summary = model["evidence_integrity_summary"]
    assert isinstance(summary, str)
    # packet의 status(OK)가 반영되고, 빈 state로 인한 NOT_CHECKED가 아니어야 한다.
    assert summary.startswith("OK"), f"packet 기준 status가 반영되지 않음: {summary}"
    assert "NOT_CHECKED" not in summary, (
        f"packet_evidence가 있는데 NOT_CHECKED fallback이 표시됨: {summary}"
    )
    # packet의 카운트(protected:3, tracked:4, pr_included:3)가 그대로 표시.
    assert "protected:3" in summary
    assert "tracked:4" in summary
    assert "pr_included:3" in summary


# ─── TC-2 (covers AC-2) ──────────────────────────────────────────────────────
def test_tc2_workspace_hygiene_from_packet():
    """packet_evidence WARN 케이스에서 workspace_hygiene_summary가 packet에서 파생된다."""
    packet = _fake_packet_evidence_warn()
    model = pipeline._build_acceptance_display_model(
        {"pipeline_id": _PIPELINE_ID}, None, packet
    )
    whs = model["workspace_hygiene_summary"]
    assert isinstance(whs, dict)
    # packet의 WARN status가 그대로 반영(state의 NOT_CHECKED가 아님).
    assert whs["status"] == "WARN", f"packet 기준 WARN status가 반영되지 않음: {whs}"
    assert whs["status"] != "NOT_CHECKED"
    # cleanup_only_items가 packet의 목록을 그대로 담는다.
    assert "build_report.xml" in whs["cleanup_only_items"]
    assert "tmp_tc1_given.json" in whs["cleanup_only_items"]
    assert whs["blocking_items"] == []


# ─── TC-3 (covers AC-5) ──────────────────────────────────────────────────────
def test_tc3_fallback_without_packet_evidence():
    """packet_evidence=None이면 state에서 읽으며, state에 evidence_integrity가 없으면
    NOT_CHECKED를 표시하는 것이 정상 fallback이다(하위 호환)."""
    # packet_evidence 인자 자체를 생략 — 기존 시그니처 호환 경로.
    model = pipeline._build_acceptance_display_model({"pipeline_id": _PIPELINE_ID}, None)
    summary = model["evidence_integrity_summary"]
    whs = model["workspace_hygiene_summary"]
    # state에 evidence_integrity/workspace_hygiene가 없으므로 NOT_CHECKED fallback.
    assert "NOT_CHECKED" in summary, (
        f"packet_evidence 미제공 + state 비어있음 → NOT_CHECKED fallback 기대: {summary}"
    )
    assert whs["status"] == "NOT_CHECKED"
    # 명시적으로 None을 넘긴 경우도 동일 fallback 이어야 한다.
    model_none = pipeline._build_acceptance_display_model(
        {"pipeline_id": _PIPELINE_ID}, None, None
    )
    assert "NOT_CHECKED" in model_none["evidence_integrity_summary"]
    assert model_none["workspace_hygiene_summary"]["status"] == "NOT_CHECKED"


# ─── TC-4 (covers AC-6) ──────────────────────────────────────────────────────
def test_tc4_accepted_comment_no_pending_marker():
    """_render_accepted_completion_comment 출력에 pending 마커가 없다."""
    model = _base_display_model()
    model["accepted_by"] = "hojiyong2-commits"
    model["accepted_at"] = "2026-06-14T00:00:00"
    model["acceptance_display"] = "ACCEPTED"
    body = pipeline._render_accepted_completion_comment(model)
    assert "<!-- pipeline-human-acceptance-packet-pending -->" not in body, (
        "accepted 댓글에 pending 마커가 잘못 포함됨"
    )
    # accepted 마커는 포함되어야 한다(올바른 상태 분리 확인).
    assert "<!-- pipeline-human-acceptance-packet-accepted -->" in body


# ─── TC-5 (covers AC-7) ──────────────────────────────────────────────────────
def test_tc5_pending_comment_no_completion_phrases():
    """_render_pending_acceptance_comment 출력에 완료/승인 문구가 없다."""
    body = pipeline._render_pending_acceptance_comment(_base_display_model())
    for forbidden in ("ACCEPTED", "승인 완료", "배포 완료", "완료됐습니다"):
        assert forbidden not in body, (
            f"pending 댓글에 금지 완료 문구 '{forbidden}'가 포함됨"
        )
    # pending 마커는 포함되어야 한다(상태 분리 확인).
    assert "<!-- pipeline-human-acceptance-packet-pending -->" in body


# ─── TC-6 (covers AC-8) ──────────────────────────────────────────────────────
def test_tc6_nonce_regression_file_exists():
    """기존 nonce 회귀 테스트 파일이 그대로 존재한다(회귀 보호 유지 확인)."""
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    nonce_test = os.path.join(
        project_root, "tests", "e2e", "test_request_accept_nonce_reuse_aef0.py"
    )
    assert os.path.exists(nonce_test), (
        f"nonce 회귀 테스트 파일이 없습니다: {nonce_test}"
    )
    assert os.path.getsize(nonce_test) > 0, "nonce 회귀 테스트 파일이 비어 있습니다."


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
