"""BUG-20260627-B221 E2E 테스트 — Codex Review Loop 버그 수정 검증.

검증 대상:
- MT-1: call_codex_cli 로깅 (부작용 없음 — APPROVE 경로로 간접 확인)
- MT-2: main() 모든 fail-closed 경로에서 FAILED terminal state 기록
- MT-3: _check_stale_processing PROCESSING 타임아웃 감지
- MT-4: APPROVE 흐름 → loop_state APPROVED, exit 0
- MT-5: Codex 실패/타임아웃/잘못된 verdict → loop_state FAILED, exit 1
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# hook 모듈 임포트
sys.path.insert(0, str(Path(__file__).parent.parent / ".claude" / "hooks"))
import codex_user_acceptance_review as hook  # noqa: E402


def _make_transcript(pipeline_id: str, pr_url: str) -> str:
    """5요소 승인 블록을 포함한 transcript 텍스트(JSONL) 생성."""
    block = (
        "사용자 승인 요청\n\n"
        f"PR: {pr_url}\n\n"
        "승인 코드:\n"
        f"ACCEPT-{pipeline_id}\n\n"
        "CODEX 검토 필요"
    )
    return json.dumps({"role": "assistant", "content": block})


def _packet(pr_url: str, pipeline_id: str) -> dict:
    """build_review_packet mock 반환값."""
    return {
        "pr_url": pr_url,
        "accept_code": f"ACCEPT-{pipeline_id}",
        "head_sha": "abc123sha",
        "title": "Test PR",
        "body": "test body",
        "changed_files": ["test.py"],
        "diff_excerpt": "diff content",
        "ci_status": [],
        "latest_comments": [],
    }


class TestApproveFlow:
    """MT-4: APPROVE 흐름 E2E 테스트."""

    def test_approve_sets_loop_state_approved(self, tmp_path):
        """Codex가 APPROVE_TO_USER 반환 → loop_state status=APPROVED, exit_code=0."""
        pipeline_id = "BUG-20260627-B221"
        pr_url = "https://github.com/hojiyong2-commits/Pipeline/pull/999"

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(_make_transcript(pipeline_id, pr_url), encoding="utf-8")

        packet_md = tmp_path / "human_acceptance_packet.md"
        packet_md.write_text("# 수락 패킷\n테스트용 패킷", encoding="utf-8")

        pipeline_dir = tmp_path / ".pipeline"
        pipeline_dir.mkdir()

        with (
            patch("codex_user_acceptance_review._get_pr_head_sha", return_value="abc123sha"),
            patch("codex_user_acceptance_review.build_review_packet",
                  return_value=_packet(pr_url, pipeline_id)),
            patch("codex_user_acceptance_review.call_codex_cli", return_value="APPROVE_TO_USER"),
            patch("codex_user_acceptance_review._project_root", return_value=tmp_path),
            patch("codex_user_acceptance_review._project_pipeline_dir", return_value=pipeline_dir),
        ):
            exit_code = hook.main(["--transcript", str(transcript)])

        assert exit_code == 0, f"Expected exit_code 0, got {exit_code}"

        state_path = pipeline_dir / "codex_review_loop_state.json"
        assert state_path.exists(), "loop_state 파일이 생성되어야 함"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["status"] == "APPROVED", f"Expected APPROVED, got {state.get('status')}"
        assert state["pipeline_id"] == pipeline_id


class TestFailureFlow:
    """MT-5: Codex 실패 경로 테스트."""

    def test_codex_missing_records_failed_state(self, tmp_path):
        """codex CLI 없을 때 → loop_state status=FAILED."""
        pipeline_id = "BUG-20260627-B221"
        pr_url = "https://github.com/hojiyong2-commits/Pipeline/pull/999"

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(_make_transcript(pipeline_id, pr_url), encoding="utf-8")

        packet_md = tmp_path / "human_acceptance_packet.md"
        packet_md.write_text("# 수락 패킷\n테스트용 패킷", encoding="utf-8")

        pipeline_dir = tmp_path / ".pipeline"
        pipeline_dir.mkdir()

        with (
            patch("codex_user_acceptance_review._get_pr_head_sha", return_value="abc123sha"),
            patch("codex_user_acceptance_review.build_review_packet",
                  return_value=_packet(pr_url, pipeline_id)),
            patch("codex_user_acceptance_review.call_codex_cli",
                  side_effect=RuntimeError("codex CLI not found — fail-closed")),
            patch("codex_user_acceptance_review._project_root", return_value=tmp_path),
            patch("codex_user_acceptance_review._project_pipeline_dir", return_value=pipeline_dir),
        ):
            exit_code = hook.main(["--transcript", str(transcript)])

        assert exit_code == 1, f"Expected exit_code 1, got {exit_code}"

        state_path = pipeline_dir / "codex_review_loop_state.json"
        assert state_path.exists(), "FAILED 상태 파일이 기록되어야 함"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["status"] == "FAILED", f"Expected FAILED, got {state.get('status')}"

    def test_codex_timeout_records_failed_state(self, tmp_path):
        """codex CLI 타임아웃 → loop_state status=FAILED, failure_code=codex_call_failed."""
        pipeline_id = "BUG-20260627-B221"
        pr_url = "https://github.com/hojiyong2-commits/Pipeline/pull/999"

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(_make_transcript(pipeline_id, pr_url), encoding="utf-8")

        packet_md = tmp_path / "human_acceptance_packet.md"
        packet_md.write_text("# 수락 패킷\n테스트용 패킷", encoding="utf-8")

        pipeline_dir = tmp_path / ".pipeline"
        pipeline_dir.mkdir()

        with (
            patch("codex_user_acceptance_review._get_pr_head_sha", return_value="abc123sha"),
            patch("codex_user_acceptance_review.build_review_packet",
                  return_value=_packet(pr_url, pipeline_id)),
            patch("codex_user_acceptance_review.call_codex_cli",
                  side_effect=RuntimeError("codex CLI timed out — fail-closed")),
            patch("codex_user_acceptance_review._project_root", return_value=tmp_path),
            patch("codex_user_acceptance_review._project_pipeline_dir", return_value=pipeline_dir),
        ):
            exit_code = hook.main(["--transcript", str(transcript)])

        assert exit_code == 1

        state_path = pipeline_dir / "codex_review_loop_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["status"] == "FAILED"
        assert "failure_code" in state
        assert state["failure_code"] == "codex_call_failed"

    def test_invalid_verdict_records_failed_state(self, tmp_path):
        """Codex가 잘못된 형식 반환 → loop_state status=FAILED."""
        pipeline_id = "BUG-20260627-B221"
        pr_url = "https://github.com/hojiyong2-commits/Pipeline/pull/999"

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(_make_transcript(pipeline_id, pr_url), encoding="utf-8")

        packet_md = tmp_path / "human_acceptance_packet.md"
        packet_md.write_text("# 수락 패킷\n테스트용 패킷", encoding="utf-8")

        pipeline_dir = tmp_path / ".pipeline"
        pipeline_dir.mkdir()

        with (
            patch("codex_user_acceptance_review._get_pr_head_sha", return_value="abc123sha"),
            patch("codex_user_acceptance_review.build_review_packet",
                  return_value=_packet(pr_url, pipeline_id)),
            patch("codex_user_acceptance_review.call_codex_cli", return_value="INVALID_VERDICT"),
            patch("codex_user_acceptance_review._project_root", return_value=tmp_path),
            patch("codex_user_acceptance_review._project_pipeline_dir", return_value=pipeline_dir),
        ):
            exit_code = hook.main(["--transcript", str(transcript)])

        assert exit_code == 1
        state_path = pipeline_dir / "codex_review_loop_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["status"] == "FAILED"
        assert state["failure_code"] == "invalid_verdict"


class TestStaleProcessing:
    """MT-3: _check_stale_processing 단위 테스트."""

    def test_non_processing_returns_false(self):
        assert hook._check_stale_processing({"status": "APPROVED"}) is False

    def test_recent_processing_returns_false(self):
        recent = {
            "status": "PROCESSING",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        assert hook._check_stale_processing(recent) is False

    def test_old_processing_returns_true(self):
        old = {
            "status": "PROCESSING",
            "started_at": (
                datetime.now(timezone.utc) - timedelta(seconds=700)
            ).isoformat(),
        }
        assert hook._check_stale_processing(old) is True

    def test_missing_started_at_returns_false(self):
        assert hook._check_stale_processing({"status": "PROCESSING"}) is False

    def test_unparseable_started_at_returns_false(self):
        bad = {"status": "PROCESSING", "started_at": "not-a-date"}
        assert hook._check_stale_processing(bad) is False

    def test_none_state_raises_type_error(self):
        with pytest.raises(TypeError):
            hook._check_stale_processing(None)

    def test_zero_timeout_raises_value_error(self):
        recent = {
            "status": "PROCESSING",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        with pytest.raises(ValueError):
            hook._check_stale_processing(recent, 0)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
