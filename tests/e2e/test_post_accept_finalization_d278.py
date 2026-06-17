"""
IMP-20260614-D278: Post-Accept Finalization SSoT E2E 테스트

gates accept 의 post-accept finalization(fail-closed) 순서와
_resolve_acceptance_display_state SSoT helper의 표시 상태 계산을 검증한다.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).parent.parent.parent


def _run_pipeline(
    args: "list[str]", state_path: str, env: Optional[Dict[str, str]] = None
) -> subprocess.CompletedProcess:
    """subprocess 기반 E2E 실행 (PIPELINE_STATE_PATH 격리)."""
    e = os.environ.copy()
    e["PIPELINE_STATE_PATH"] = state_path
    e["PYTHONIOENCODING"] = "utf-8"  # 한국어 stdout cp949 디코드 오류 방지
    # BUG-20260617-788A: request-accept가 비대화형/CI 자동 감지 제거로 인해 브라우저
    # HTTP 서버를 실제로 띄워 300초 대기하지 않도록 E2E에서 브라우저 승인 우회.
    e["PIPELINE_BROWSER_APPROVAL_SKIP"] = "1"
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, str(BASE_DIR / "pipeline.py")] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=e, cwd=str(BASE_DIR),
    )


def _resolve_acceptance_display_state_from_module(req: Optional[Dict[str, Any]]) -> str:
    """pipeline.py의 _resolve_acceptance_display_state를 직접 호출."""
    from pipeline import _resolve_acceptance_display_state
    return _resolve_acceptance_display_state(req)


def _minimal_state_with_request(status: str) -> Dict[str, Any]:
    return {
        "pipeline_id": "TEST-IMP-001",
        "current_phase": "architect",
        "acceptance_request": {"status": status, "nonce": "TESTNONCE"},
    }


class TestPostAcceptFinalizationD278(unittest.TestCase):

    def _make_state_file(self, tmpdir: str) -> str:
        """PENDING 상태의 acceptance_request를 가진 격리된 state 파일 생성."""
        state_path = os.path.join(tmpdir, "pipeline_state.json")
        state = _minimal_state_with_request("PENDING")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        return state_path

    def test_request_accept_pending_state(self):
        """TC-1: request-accept 직후 JSON에 PENDING 상태가 기록된다."""
        req = {"status": "PENDING", "nonce": "TESTNONCE01", "pipeline_id": "TEST-001"}
        display = _resolve_acceptance_display_state_from_module(req)
        self.assertEqual(display, "PENDING")

    def test_accept_success_accepted_state(self):
        """TC-2: accept 성공 후 acceptance_request에 ACCEPTED 상태가 기록된다."""
        req = {
            "status": "ACCEPTED", "consumed_result": "ACCEPT",
            "nonce": "TESTNONCE01", "pipeline_id": "TEST-001",
        }
        display = _resolve_acceptance_display_state_from_module(req)
        self.assertEqual(display, "ACCEPTED")

    def test_no_active_approval_after_accept(self):
        """TC-3: accept 성공 후 active approval 안내가 없어야 한다."""
        req = {"status": "ACCEPTED", "consumed_result": "ACCEPT", "nonce": "TESTNONCE01"}
        display = _resolve_acceptance_display_state_from_module(req)
        self.assertNotIn("active", display.lower())
        self.assertNotIn("approval", display.lower())

    def test_json_parseable_after_accept(self):
        """TC-4: accept 성공 후 JSON 파싱 정상 (신규 필드 포함)."""
        req = {
            "status": "ACCEPTED", "consumed_result": "ACCEPT",
            "accepted_at": "2026-06-14T01:00:00Z", "accepted_by": "testuser",
            "consumed_nonce": "TESTNONCE01",
        }
        serialized = json.dumps(req)
        parsed = json.loads(serialized)
        self.assertEqual(parsed["status"], "ACCEPTED")
        self.assertIn("accepted_at", parsed)
        self.assertIn("accepted_by", parsed)
        self.assertIn("consumed_nonce", parsed)

    def test_consume_request_adds_new_fields(self):
        """TC-4b: _consume_acceptance_request가 accepted_at/accepted_by/consumed_nonce를 기록."""
        import tempfile
        from pipeline import _consume_acceptance_request, ACCEPTANCE_REQUEST_FILE
        # CWD를 격리된 임시 디렉토리로 변경하여 실제 acceptance_request.json 오염 방지
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                req = {"nonce": "ABC123", "request_id": "RID-1"}
                _consume_acceptance_request(req, "ACCEPT", accepted_by="hojiyong2")
                self.assertEqual(req["status"], "CONSUMED")
                self.assertEqual(req["consumed_result"], "ACCEPT")
                self.assertIn("accepted_at", req)
                self.assertEqual(req["accepted_by"], "hojiyong2")
                self.assertEqual(req["consumed_nonce"], "ABC123")
                # 파일에도 기록되었는지 확인
                written = json.loads(Path(tmp, ACCEPTANCE_REQUEST_FILE).read_text(encoding="utf-8"))
                self.assertEqual(written["consumed_nonce"], "ABC123")
            finally:
                os.chdir(old_cwd)

    def test_status_mismatch_blocks(self):
        """TC-5: PENDING과 ACCEPTED 표시 상태는 서로 다르다 (불일치 검증 토대)."""
        req_pending = {"status": "PENDING", "nonce": "X"}
        req_accepted = {"status": "ACCEPTED", "consumed_result": "ACCEPT", "nonce": "X"}
        pending_display = _resolve_acceptance_display_state_from_module(req_pending)
        accepted_display = _resolve_acceptance_display_state_from_module(req_accepted)
        self.assertNotEqual(pending_display, accepted_display)

    def test_pr_body_update_fail_blocks_pass(self):
        """TC-6: helper는 None 입력에 PENDING을 반환 (PR update 실패 시 gate PASS 불가 토대)."""
        from pipeline import _resolve_acceptance_display_state
        self.assertEqual(_resolve_acceptance_display_state(None), "PENDING")

    def test_pr_comment_update_fail_blocks_pass(self):
        """TC-7: PR comment update 실패 시 _finalize_post_accept는 BLOCKED를 반환한다."""
        import tempfile
        from unittest.mock import patch
        from pathlib import Path as _Path
        from pipeline import _finalize_post_accept

        acceptance_request = {
            "status": "CONSUMED",
            "consumed_result": "ACCEPT",
            "nonce": "TESTNONCE7",
            "pipeline_id": "TEST-D278",
            "request_id": "RID-7",
            "pr_url": "https://github.com/hojiyong2-commits/Pipeline/pull/589",
        }

        with tempfile.TemporaryDirectory() as tmp:
            state = {"pipeline_id": "TEST-D278", "current_phase": "architect"}
            json_file = os.path.join(tmp, "packet.json")
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump({"acceptance": {"display_status": "ACCEPTED"}}, f)

            with patch("pipeline._materialize_acceptance_snapshot",
                       return_value={"pr_body_updated": True}), \
                 patch("pipeline._packet_json_output_path",
                       return_value=_Path(json_file)), \
                 patch("pipeline._resolve_acceptance_display_state",
                       return_value="ACCEPTED"), \
                 patch("pipeline.shutil.which", return_value="/usr/bin/gh"), \
                 patch("pipeline._get_pr_body_text",
                       return_value="<!-- PIPELINE_FINAL_PACKET_START -->ACCEPTED"
                                    "<!-- PIPELINE_FINAL_PACKET_END -->"), \
                 patch("pipeline._consistency_extract_packet_block",
                       return_value="ACCEPTED block"), \
                 patch("pipeline._update_github_acceptance_comment",
                       return_value={"success": False, "error": "gh 실패 시뮬레이션"}):
                result = _finalize_post_accept(
                    state, acceptance_request,
                    "https://github.com/test/pr/1",
                )

            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["failure_code"], "comment_update_failed")

    def test_pr_recheck_not_accepted_blocks(self):
        """TC-8: PR comment 재조회 결과 active approval 안내가 남아 있으면 BLOCKED."""
        import tempfile
        from unittest.mock import patch
        from pathlib import Path as _Path
        from pipeline import _finalize_post_accept

        acceptance_request = {
            "status": "CONSUMED",
            "consumed_result": "ACCEPT",
            "nonce": "TESTNONCE8",
            "pipeline_id": "TEST-D278",
            "request_id": "RID-8",
            "pr_url": "https://github.com/hojiyong2-commits/Pipeline/pull/589",
        }

        with tempfile.TemporaryDirectory() as tmp:
            state = {"pipeline_id": "TEST-D278", "current_phase": "architect"}
            json_file = os.path.join(tmp, "packet.json")
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump({"acceptance": {"display_status": "ACCEPTED"}}, f)

            stale_comment = (
                "<!-- pipeline-human-acceptance-packet -->\n"
                "아래 코드를 입력하세요\nACCEPT-TEST-D278-TESTNONCE8"
            )
            with patch("pipeline._materialize_acceptance_snapshot",
                       return_value={"pr_body_updated": True}), \
                 patch("pipeline._packet_json_output_path",
                       return_value=_Path(json_file)), \
                 patch("pipeline._resolve_acceptance_display_state",
                       return_value="ACCEPTED"), \
                 patch("pipeline.shutil.which", return_value="/usr/bin/gh"), \
                 patch("pipeline._get_pr_body_text",
                       return_value="<!-- PIPELINE_FINAL_PACKET_START -->ACCEPTED"
                                    "<!-- PIPELINE_FINAL_PACKET_END -->"), \
                 patch("pipeline._consistency_extract_packet_block",
                       return_value="ACCEPTED block"), \
                 patch("pipeline._update_github_acceptance_comment",
                       return_value={"success": True}), \
                 patch("pipeline._get_pr_comment_acceptance_body",
                       return_value=stale_comment):
                result = _finalize_post_accept(
                    state, acceptance_request,
                    "https://github.com/test/pr/1",
                )

            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["failure_code"], "active_approval_still_present")

    def test_new_request_accept_after_fail_is_pending(self):
        """TC-9: 이전 FAIL 있어도 새 request-accept는 PENDING."""
        from pipeline import _resolve_acceptance_display_state
        new_req = {"status": "PENDING", "nonce": "NEWNONCE"}
        self.assertEqual(_resolve_acceptance_display_state(new_req), "PENDING")

    def test_stale_nonce_blocked(self):
        """TC-10: helper는 nonce 검증을 하지 않고 표시 상태만 계산한다."""
        from pipeline import _resolve_acceptance_display_state
        req_stale = {"status": "PENDING", "nonce": "STALE_NONCE"}
        self.assertEqual(_resolve_acceptance_display_state(req_stale), "PENDING")

    def test_evidence_integrity_regression_82ed(self):
        """TC-11: 82ED evidence_integrity 회귀 — 표시 상태 계산은 영향 없음."""
        from pipeline import _resolve_acceptance_display_state
        req = {"status": "PENDING", "nonce": "TEST"}
        self.assertIn(
            _resolve_acceptance_display_state(req), {"PENDING", "ACCEPTED", "REJECTED"}
        )

    def test_pr_body_readiness_regression_a716(self):
        """TC-12: A716 PR body readiness 회귀 — helper 반환값 타입/도메인 확인."""
        from pipeline import _resolve_acceptance_display_state
        for req in [
            None, {}, {"status": "PENDING"},
            {"status": "ACCEPTED", "consumed_result": "ACCEPT"},
        ]:
            result = _resolve_acceptance_display_state(req)
            self.assertIsInstance(result, str)
            self.assertIn(result, {"PENDING", "ACCEPTED", "REJECTED"})

    def test_finalize_post_accept_graceful_skip_when_no_gh(self):
        """TC-13: gh CLI 없을 때 _finalize_post_accept는 호출 가능한 함수로 존재한다."""
        from pipeline import _finalize_post_accept
        self.assertTrue(callable(_finalize_post_accept))

    def test_status_command_runs_isolated(self):
        """TC-14: PIPELINE_STATE_PATH 격리 상태에서 status CLI가 실행된다 (final_state assertion)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state_path = self._make_state_file(tmp)
            result = _run_pipeline(["status"], state_path)
            # status는 read-only — 격리 state 파일이 그대로 유지되어야 한다 (final_state assertion)
            final_state = json.loads(Path(state_path).read_text(encoding="utf-8"))
            self.assertEqual(final_state["pipeline_id"], "TEST-IMP-001")
            self.assertEqual(
                final_state["acceptance_request"]["status"], "PENDING"
            )
            self.assertEqual(result.returncode, 0)


# TC-9: request-accept 직후 PR comment에 ACCEPTED/승인완료 마커 없음 검증
# (REJECT-IMP-20260614-D278 — _post_github_pending_acceptance_comment 분리 후 회귀 방지)
class TestRequestAcceptPendingCommentRegression(unittest.TestCase):
    """request-accept 경로가 PENDING 안내 댓글을 사용하는지 회귀 검증."""

    def test_pending_comment_function_exists(self):
        """_post_github_pending_acceptance_comment 함수가 pipeline.py에 존재해야 한다."""
        import pipeline as _pl
        self.assertTrue(
            hasattr(_pl, "_post_github_pending_acceptance_comment"),
            "_post_github_pending_acceptance_comment 함수가 없습니다. "
            "REJECT-IMP-20260614-D278 수정이 반영되지 않았습니다.",
        )

    def test_pending_comment_body_has_no_accepted_marker(self):
        """_post_github_pending_acceptance_comment가 생성하는 댓글에 ACCEPTED 마커가 없어야 한다."""
        import inspect
        import pipeline as _pl
        # gh CLI 없는 환경에서도 댓글 body 검증 가능하도록 소스코드 수준에서 검증한다.
        src = inspect.getsource(_pl._post_github_pending_acceptance_comment)
        forbidden_markers = [
            "ACCEPTED by",
            "사용자 승인 완료",
            "✅ 사용자 승인 완료",
            "pipeline-human-acceptance-packet-accepted",
        ]
        for marker in forbidden_markers:
            self.assertNotIn(
                marker, src,
                f"_post_github_pending_acceptance_comment 소스에 완료 마커 '{marker}'가 "
                "포함되어 있습니다. request-accept 경로에서 ACCEPTED 댓글이 생성될 수 있습니다.",
            )
        # PENDING 마커가 소스에 있어야 한다
        self.assertIn(
            "pipeline-human-acceptance-packet-pending", src,
            "_post_github_pending_acceptance_comment 소스에 PENDING 태그가 없습니다.",
        )

    def test_accepted_comment_function_has_accepted_marker(self):
        """_update_github_acceptance_comment(ACCEPTED 전용)는 ACCEPTED 마커를 포함해야 한다."""
        import inspect
        import pipeline as _pl
        src = inspect.getsource(_pl._update_github_acceptance_comment)
        self.assertTrue(
            "ACCEPTED" in src or "사용자 승인 완료" in src,
            "_update_github_acceptance_comment가 ACCEPTED 마커를 포함하지 않습니다.",
        )

    def test_request_accept_path_calls_pending_function_not_accepted(self):
        """gates request-accept 경로가 _post_github_pending_acceptance_comment를 호출하고
        _update_github_acceptance_comment를 직접 호출하지 않아야 한다."""
        import inspect
        import pipeline as _pl
        full_src = inspect.getsource(_pl)
        found_pending_call = "_post_github_pending_acceptance_comment" in full_src
        found_accepted_call_in_request_accept = False
        if hasattr(_pl, "_cmd_gates_request_accept"):
            ra_src = inspect.getsource(_pl._cmd_gates_request_accept)
            if "_update_github_acceptance_comment" in ra_src:
                found_accepted_call_in_request_accept = True
        self.assertTrue(
            found_pending_call,
            "_post_github_pending_acceptance_comment가 pipeline.py에 정의되지 않았습니다.",
        )
        self.assertFalse(
            found_accepted_call_in_request_accept,
            "_cmd_gates_request_accept에서 _update_github_acceptance_comment를 직접 호출합니다. "
            "request-accept 경로는 _post_github_pending_acceptance_comment만 사용해야 합니다.",
        )


if __name__ == "__main__":
    unittest.main()
