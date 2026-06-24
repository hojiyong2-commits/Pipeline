"""IMP-20260624-069A 회귀 테스트: 최소 고정 양식 준수 검증.

_render_pending_acceptance_comment 가 4요소 최소 고정 양식
(사용자 승인 요청 / PR / 승인 코드 / CODEX 검토 필요)을 출력하고
pending HTML 마커 2개를 유지하는지 검증한다.

주의: 실제 _render_pending_acceptance_comment 시그니처는 단일
display_model dict 를 받는다(pipeline_id / pr_url / approval_code 키 사용).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

# pipeline.py를 직접 임포트하기 위해 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))


def _get_render_pending_comment():
    """_render_pending_acceptance_comment 함수를 가져옵니다."""
    spec = importlib.util.spec_from_file_location(
        "pipeline",
        Path(__file__).parent.parent / "pipeline.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._render_pending_acceptance_comment


def _display_model(pr_url: str) -> dict:
    """테스트용 최소 display_model dict 를 만든다."""
    return {
        "pipeline_id": "IMP-20260624-069A",
        "pr_url": pr_url,
        "approval_code": "ACCEPT-IMP-20260624-069A",
    }


class TestCliOutputFormat:
    """MT-1/MT-2: 최소 양식 4요소 검증 (_render 함수 기반)."""

    def test_cli_output_contains_4_elements(self):
        """TC-1 (normal): 최소 양식 4요소가 순서대로 포함되어야 한다."""
        render = _get_render_pending_comment()
        result = render(
            _display_model("https://github.com/hojiyong2-commits/Pipeline/pull/999")
        )
        lines = result.splitlines()

        # 4요소가 순서대로 등장해야 함
        elements = ["사용자 승인 요청", "PR:", "승인 코드:", "CODEX 검토 필요"]
        found_positions = []
        for elem in elements:
            for i, line in enumerate(lines):
                if elem in line:
                    found_positions.append(i)
                    break
            else:
                pytest.fail(
                    f"필수 요소 '{elem}'가 출력에 없습니다.\n출력:\n{result}"
                )

        assert found_positions == sorted(found_positions), (
            f"4요소가 순서대로 등장하지 않습니다. 위치: {found_positions}\n"
            f"출력:\n{result}"
        )

    def test_cli_output_no_pr_url_fallback(self):
        """TC-2 (edge): PR URL 없는 경우 'PR 링크 없음' fallback."""
        render = _get_render_pending_comment()
        result = render(_display_model(""))
        assert "PR 링크 없음" in result, f"PR URL 없을 때 fallback 없음\n출력:\n{result}"
        assert "사용자 승인 요청" in result
        assert "CODEX 검토 필요" in result

    def test_cli_output_no_forbidden_strings(self):
        """TC-3 (error): 금지 문구가 출력에 없어야 한다."""
        render = _get_render_pending_comment()
        result = render(
            _display_model("https://github.com/hojiyong2-commits/Pipeline/pull/999")
        )

        forbidden = [
            "확인할 항목",
            "구현 요약",
            "GitHub Actions",
            "테스트",
            "댓글 게시 후 알려주세요",
        ]
        for term in forbidden:
            assert term not in result, (
                f"금지 문구 '{term}'이 출력에 포함됨\n출력:\n{result}"
            )


class TestPendingCommentFormat:
    """MT-2: PR pending 댓글 최소 양식 + 마커 검증."""

    def test_pending_comment_format(self):
        """TC-4 (normal): pending 댓글도 4요소 최소 양식 + pending 마커."""
        render = _get_render_pending_comment()
        result = render(
            _display_model("https://github.com/hojiyong2-commits/Pipeline/pull/999")
        )

        assert "사용자 승인 요청" in result
        assert "PR:" in result
        assert "승인 코드:" in result
        assert "CODEX 검토 필요" in result

        # pending 마커 유지 확인
        assert "<!-- pipeline-human-acceptance-packet -->" in result
        assert "<!-- pipeline-human-acceptance-packet-pending -->" in result

    def test_pending_comment_no_accepted_markers(self):
        """TC-5 (exception): pending 댓글에 ACCEPTED/완료 마커가 없어야 한다."""
        render = _get_render_pending_comment()
        result = render(
            _display_model("https://github.com/hojiyong2-commits/Pipeline/pull/999")
        )

        assert "ACCEPTED" not in result
        assert "승인 완료" not in result
        assert "배포 완료" not in result
        assert "<!-- pipeline-human-acceptance-packet-accepted -->" not in result

    def test_pending_comment_rejects_none(self):
        """TC-6 (exception): display_model None 입력 시 TypeError."""
        render = _get_render_pending_comment()
        with pytest.raises(TypeError):
            render(None)
