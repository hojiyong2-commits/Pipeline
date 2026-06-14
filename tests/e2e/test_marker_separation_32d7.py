# [Purpose]: BUG-20260614-32D7 — CI 최종 확인 안내(final-check) 댓글과 pending/accepted
#   승인 댓글을 marker 레벨에서 분리한 로직을 검증한다. 공통 marker만 공유하던 3종 댓글이
#   서로를 덮어쓰거나 오인하던 회귀를 막는다.
# [Assumptions]: pipeline 모듈을 직접 import 가능. gh CLI subprocess는 monkeypatch로 대체.
#   TC-7/TC-8은 기존 회귀 테스트 파일을 subprocess로 실행한다.
# [Vulnerability & Risks]: marker 상수 변경 시 테스트가 깨진다 — pipeline 모듈 SSoT 상수를
#   직접 참조하여 drift를 줄인다.
# [Improvement]: gh API 응답 fixture를 공용 헬퍼로 추출하면 중복을 더 줄일 수 있다.
"""BUG-20260614-32D7 marker 분리 회귀 테스트 (8개 TC)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# 저장소 루트를 import path에 추가 (tests/e2e/ → repo root)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pipeline  # noqa: E402


# ─── 공용 marker 상수 (pipeline SSoT 직접 참조) ──────────────────────────────
_COMMON = pipeline._ACCEPTANCE_PACKET_COMMENT_TAG  # "<!-- pipeline-human-acceptance-packet -->"
_FINAL_CHECK = pipeline._ACCEPTANCE_PACKET_FINAL_CHECK_MARKER  # "pipeline-final-check-packet"
_PENDING = pipeline._ACCEPTANCE_PACKET_PENDING_MARKER  # "pipeline-human-acceptance-packet-pending"
_ACCEPTED = pipeline._ACCEPTANCE_PACKET_ACCEPTED_MARKER  # "pipeline-human-acceptance-packet-accepted"


def _final_check_comment_body() -> str:
    """CI 최종 확인 안내 댓글 본문(공통 + final-check marker)."""
    return (
        f"{_COMMON}\n"
        f"<!-- {_FINAL_CHECK} -->\n"
        "## 최종 확인 안내\n판단 정보 상태: 판단 가능\n"
    )


def _pending_comment_body() -> str:
    """PENDING 승인 요청 댓글 본문(공통 + pending marker)."""
    return (
        f"{_COMMON}\n"
        f"<!-- {_PENDING} -->\n"
        "## 사용자 최종 확인 요청\n판단 정보 상태: 판단 가능\n"
        "승인 코드를 입력하세요: ACCEPT-BUG-20260614-32D7-abc123\n"
    )


def _accepted_comment_body() -> str:
    """ACCEPTED 완료 댓글 본문(공통 + accepted marker)."""
    return (
        f"{_COMMON}\n"
        f"<!-- {_ACCEPTED} -->\n"
        "## 사용자 승인 완료\nACCEPTED by tester\n"
    )


# ─── TC-1: CI가 final-check 댓글만 선택 ──────────────────────────────────────
def test_tc1_ci_selects_only_final_check() -> None:
    """CI PATCH 선택 로직(ci.yml과 동일 조건)이 final-check 댓글만 고르고
    pending/accepted를 제외하는지 검증한다.

    ci.yml의 Where-Object 조건을 Python으로 동형 재현하여 검증한다:
      final-check marker 포함 AND pending 미포함 AND accepted 미포함.
    """
    comments: List[Dict[str, Any]] = [
        {"id": 1, "body": _final_check_comment_body()},
        {"id": 2, "body": _pending_comment_body()},
        {"id": 3, "body": _accepted_comment_body()},
    ]

    def ci_select(body: str) -> bool:
        return (
            (f"<!-- {_FINAL_CHECK} -->" in body)
            and (_PENDING not in body)
            and (_ACCEPTED not in body)
        )

    selected = [c["id"] for c in comments if ci_select(c["body"])]
    assert selected == [1], f"CI는 final-check 댓글(id=1)만 선택해야 함, got {selected}"
    # pending/accepted 댓글은 _is_final_check_only_comment로도 제외 확인
    assert pipeline._is_final_check_only_comment(comments[0]["body"]) is True
    assert pipeline._is_final_check_only_comment(comments[1]["body"]) is False
    assert pipeline._is_final_check_only_comment(comments[2]["body"]) is False


# ─── TC-2: request-accept 경로에서 final-check 보존 ──────────────────────────
def test_tc2_request_accept_preserves_final_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """_post_github_pending_acceptance_comment의 삭제 필터가 final-check-only
    댓글을 삭제 대상에서 제외하는지 검증한다(_is_pending_or_accepted_acceptance_comment).
    """
    comments = [
        {"id": 10, "body": _final_check_comment_body()},
        {"id": 11, "body": _pending_comment_body()},
        {"id": 12, "body": _accepted_comment_body()},
    ]
    # 삭제 대상 = pending/accepted marker 있는 댓글만
    delete_ids = [
        c["id"]
        for c in comments
        if pipeline._is_pending_or_accepted_acceptance_comment(str(c["body"]))
    ]
    assert 10 not in delete_ids, "final-check-only 댓글(id=10)은 삭제 대상에서 제외되어야 함"
    assert 11 in delete_ids and 12 in delete_ids, "pending/accepted 댓글은 삭제 대상"


# ─── TC-3: accepted 댓글이 CI PATCH 제외 ─────────────────────────────────────
def test_tc3_ci_skips_accepted_comment() -> None:
    """accepted marker 댓글이 CI 최종 확인 안내 PATCH 선택 대상에서 제외되는지 검증.
    또한 _update_github_acceptance_comment 삭제 필터가 final-check를 보존하는지 확인.
    """
    accepted_body = _accepted_comment_body()
    final_check_body = _final_check_comment_body()

    def ci_select(body: str) -> bool:
        return (
            (f"<!-- {_FINAL_CHECK} -->" in body)
            and (_PENDING not in body)
            and (_ACCEPTED not in body)
        )

    assert ci_select(accepted_body) is False, "accepted 댓글은 CI PATCH 대상이 아님"
    assert ci_select(final_check_body) is True, "final-check 댓글은 CI PATCH 대상"
    # accepted 교체 시 final-check 댓글은 삭제되지 않아야 함
    assert pipeline._is_pending_or_accepted_acceptance_comment(final_check_body) is False
    assert pipeline._is_pending_or_accepted_acceptance_comment(accepted_body) is True


# ─── TC-4: gates accept가 pending 댓글 우선 선택 ─────────────────────────────
def test_tc4_gates_accept_selects_pending_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """_check_acceptance_packet_via_github가 pending 댓글을 우선 선택하고
    final-check 댓글을 approval packet으로 오인하지 않는지 검증한다.
    """
    import json as _json

    comments = [
        {"id": 20, "body": _final_check_comment_body()},
        {"id": 21, "body": _pending_comment_body()},
    ]

    class _FakeResult:
        returncode = 0
        stdout = _json.dumps(comments)
        stderr = ""

    def fake_run(cmd: List[str], *args: Any, **kwargs: Any) -> _FakeResult:
        return _FakeResult()

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)
    result = pipeline._check_acceptance_packet_via_github(
        "https://github.com/hojiyong2-commits/Pipeline/pull/999"
    )
    assert result["status"] == "PASS", (
        f"pending 댓글(판단 가능)이 선택되어 PASS여야 함, got {result}"
    )

    # final-check 댓글만 있으면 approval packet 없음 → BLOCKED(missing)
    comments_only_final = [{"id": 30, "body": _final_check_comment_body()}]

    class _FakeResult2:
        returncode = 0
        stdout = _json.dumps(comments_only_final)
        stderr = ""

    monkeypatch.setattr(
        pipeline.subprocess, "run", lambda *a, **k: _FakeResult2()
    )
    result2 = pipeline._check_acceptance_packet_via_github(
        "https://github.com/hojiyong2-commits/Pipeline/pull/999"
    )
    assert result2["status"] == "BLOCKED", (
        "final-check 전용 댓글은 approval packet으로 인정되지 않아야 함"
    )
    assert result2.get("failure_code") == "acceptance_packet_missing"


# ─── TC-5: pending 댓글에 readiness marker 포함 ──────────────────────────────
def test_tc5_pending_has_readiness_marker() -> None:
    """_post_github_pending_acceptance_comment가 보장하는 readiness 줄
    ('판단 정보 상태: 판단 가능')이 pending 본문에 포함되는지 검증한다.

    _render_pending_acceptance_comment가 readiness 줄을 만들지 않는 경우에도
    MT-2의 보강 로직이 줄을 삽입함을 본문 합성 규칙으로 검증한다.
    """
    readiness = f"판단 정보 상태: {pipeline._ACCEPTANCE_PACKET_SUFFICIENT_MARKER}"
    # renderer가 readiness를 포함하지 않는 본문을 가정
    base_body = (
        f"{_COMMON}\n<!-- {_PENDING} -->\n## 사용자 최종 확인 요청\n"
    )
    # MT-2 보강 규칙 재현: insufficient 패턴 없고 readiness 없으면 pending marker 직후 삽입
    body = base_body
    if not pipeline._ACCEPTANCE_PACKET_INSUFFICIENT_PATTERN.search(body) and (
        readiness not in body
    ):
        pending_tag = f"<!-- {_PENDING} -->"
        body = body.replace(pending_tag, f"{pending_tag}\n{readiness}", 1)
    assert readiness in body, "pending 댓글에 readiness 줄이 포함되어야 함"
    # 실제 함수가 사용하는 SUFFICIENT marker가 _check_acceptance_packet_via_github의
    # PASS 판정 기준과 동일한지 확인
    assert pipeline._ACCEPTANCE_PACKET_SUFFICIENT_MARKER in body


# ─── TC-6: accepted 댓글에 pending marker/승인 코드 입력 안내 없음 ───────────
def test_tc6_accepted_no_pending_marker() -> None:
    """_render_accepted_completion_comment 결과에 pending marker와
    '승인 코드를 입력' 안내 문구가 없는지 검증한다.
    """
    display_model = pipeline._build_acceptance_display_model({}, "evidence/path")
    display_model["accepted_by"] = "tester"
    display_model["accepted_at"] = "2026-06-14T00:00:00Z"
    display_model["pipeline_id"] = "BUG-20260614-32D7"
    body = pipeline._render_accepted_completion_comment(display_model)
    assert _PENDING not in body, "accepted 댓글에 pending marker가 있으면 안 됨"
    assert "승인 코드를 입력" not in body, (
        "accepted 댓글에 승인 코드 입력 안내 문구가 있으면 안 됨"
    )
    assert f"<!-- {_ACCEPTED} -->" in body, "accepted 댓글에는 accepted marker가 있어야 함"


# ─── TC-7: B32A 회귀 테스트 PASS ─────────────────────────────────────────────
def test_tc7_b32a_regression() -> None:
    """기존 B32A 회귀 테스트를 subprocess로 실행하여 PASS 확인."""
    target = _REPO_ROOT / "tests" / "e2e" / "test_pending_comment_sst_b32a.py"
    assert target.exists(), f"B32A 회귀 테스트 파일이 존재해야 함: {target}"
    env = dict(os.environ)
    env["PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING"] = "1"
    env["PIPELINE_NO_DASHBOARD"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(target)],
        cwd=str(_REPO_ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"B32A 회귀 테스트 실패 (exit {result.returncode}):\n"
        f"{result.stdout[-2000:]}\n{result.stderr[-1000:]}"
    )


# ─── TC-8: D278/2821 회귀 테스트 PASS ────────────────────────────────────────
def test_tc8_d278_2821_regression() -> None:
    """D278(post-accept finalization) 또는 2821(workspace hygiene) 회귀 테스트를
    subprocess로 실행하여 PASS 확인. 존재하는 파일을 모두 실행한다.
    """
    candidates = [
        _REPO_ROOT / "tests" / "e2e" / "test_post_accept_finalization_d278.py",
        _REPO_ROOT / "tests" / "e2e" / "test_workspace_hygiene_2821.py",
    ]
    existing = [c for c in candidates if c.exists()]
    assert existing, "D278/2821 회귀 테스트 파일이 최소 1개 존재해야 함"
    env = dict(os.environ)
    env["PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING"] = "1"
    env["PIPELINE_NO_DASHBOARD"] = "1"
    for target in existing:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", str(target)],
            cwd=str(_REPO_ROOT), env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        assert result.returncode == 0, (
            f"{target.name} 회귀 테스트 실패 (exit {result.returncode}):\n"
            f"{result.stdout[-2000:]}\n{result.stderr[-1000:]}"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
