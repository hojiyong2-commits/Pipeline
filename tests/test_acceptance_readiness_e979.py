"""tests/test_acceptance_readiness_e979.py

IMP-20260519-E979: _check_acceptance_readiness() 단위 테스트.

User Acceptance REJECT 재작업 (3 Blockers) 반영:
- Blocker 1: gh CLI 없음 / gh pr view 실패 / PR 없음 / JSON 파싱 실패는
  PASS가 아니라 BLOCKED를 반환한다.
- Blocker 2: human acceptance packet readiness는 GitHub PR 댓글을 기본 검증한다.
  로컬 파일 검사는 PIPELINE_TEST_ACCEPTANCE_PACKET_PATH 환경변수 fallback.
- Blocker 3: 필수 섹션 첫 항목 OR 조건(작업 요약/최종 판단 요약/이번 요청과 완료 결과),
  TEMPORARY_PR_BODY_PATTERNS에 "Draft PR" 추가.

oracle 파일: tests/oracles/IMP-20260519-E979/
- normal_draft_pr_blocked        (normal)
- normal_body_incomplete_blocked (normal)
- normal_packet_insufficient_blocked (normal)
- normal_readiness_pass_allowed  (normal)
- edge_ci_pass_but_readiness_fail (edge)
"""
import json
import os
import pathlib
import sys
import tempfile
import unittest
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

# pipeline.py 직접 import — 테스트 실행 디렉토리에서 찾을 수 있도록 sys.path 보정
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.resolve()
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pipeline  # noqa: E402


ORACLE_BASE = _PROJECT_ROOT / "tests" / "oracles" / "IMP-20260519-E979"


# ---------------------------------------------------------------------------
# Helper: oracle 기반 테스트 실행
# ---------------------------------------------------------------------------

def _load_oracle(case_name: str) -> Dict[str, Any]:
    """oracle 케이스 input/expected 로드."""
    case_dir = ORACLE_BASE / case_name
    with open(case_dir / "input.json", encoding="utf-8") as f:
        inp = json.load(f)
    with open(case_dir / "expected.json", encoding="utf-8") as f:
        exp = json.load(f)
    return {"input": inp, "expected": exp}


def _make_state(pipeline_id: str = "IMP-20260519-E979") -> Dict[str, Any]:
    """최소한의 state dict 생성."""
    return {"pipeline_id": pipeline_id}


def _make_pr_view_result(
    pr_draft: bool,
    pr_body: str,
    pr_number: int = 99,
    pr_url: str = "https://github.com/test/repo/pull/99",
) -> MagicMock:
    """`gh pr view --json` 결과를 모킹하는 MagicMock 반환."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""
    pr_data = {
        "isDraft": pr_draft,
        "title": "test PR IMP-20260519-E979",
        "body": pr_body,
        "number": pr_number,
        "url": pr_url,
    }
    mock_result.stdout = json.dumps(pr_data)
    return mock_result


# 하위 호환 alias — 기존 테스트 명칭 유지
_make_mock_subprocess_result = _make_pr_view_result


def _make_comments_result(
    comment_bodies: List[str],
    returncode: int = 0,
    stderr: str = "",
) -> MagicMock:
    """`gh api repos/.../issues/.../comments` 결과를 모킹하는 MagicMock 반환.

    Args:
        comment_bodies: 각 댓글의 body 문자열 목록.
        returncode: gh api exit code (0=성공).
        stderr: gh api 표준 에러 출력.
    """
    mock_result = MagicMock()
    mock_result.returncode = returncode
    mock_result.stderr = stderr
    if returncode == 0:
        comments = [{"id": i, "body": body} for i, body in enumerate(comment_bodies)]
        mock_result.stdout = json.dumps(comments)
    else:
        mock_result.stdout = ""
    return mock_result


def _packet_comment(status_text: str) -> str:
    """acceptance packet 태그가 포함된 댓글 body 생성."""
    return (
        f"{pipeline._ACCEPTANCE_PACKET_COMMENT_TAG}\n"
        f"## 최종 확인 안내\n"
        f"판단 정보 상태: **{status_text}**\n"
    )


def _make_subprocess_side_effect(
    pr_view_result: MagicMock,
    comments_result: MagicMock,
) -> Any:
    """subprocess.run 호출을 명령어에 따라 분기하는 side_effect 함수 반환.

    `gh pr view ...` → pr_view_result, `gh api ...` → comments_result.
    """
    def _side_effect(cmd: List[str], *args: Any, **kwargs: Any) -> MagicMock:
        if len(cmd) >= 2 and cmd[0] == "gh" and cmd[1] == "api":
            return comments_result
        return pr_view_result
    return _side_effect


# 완전한 필수 섹션을 갖춘 정상 PR 본문 (테스트 공용)
_COMPLETE_PR_BODY = (
    "## 작업 요약\n작업 완료\n\n"
    "## 사용자가 확인할 결과물\n- pipeline.py 수정\n\n"
    "## 기대 결과와 실제 결과\n| 항목 | 기대 | 실제 |\n|---|---|---|\n| 검증 | 통과 | 통과 |\n\n"
    "## 중요한 선택과 트레이드오프\n정규식 사용\n\n"
    "## 검증\n테스트 PASS"
)


# ---------------------------------------------------------------------------
# TC-01: Draft PR이면 BLOCKED (failure_code: pr_is_draft) [normal]
# ---------------------------------------------------------------------------

class TestDraftPrBlocked(unittest.TestCase):
    """정상 케이스 — Draft PR 차단."""

    def test_draft_pr_blocked(self) -> None:
        """Draft PR이면 _check_acceptance_readiness가 BLOCKED(pr_is_draft)를 반환한다."""
        oracle = _load_oracle("normal_draft_pr_blocked")
        call_args = oracle["input"]["call_args"]
        expected = oracle["expected"]

        pr_body = call_args["pr_body"]
        pr_draft = call_args["pr_draft"]  # True

        mock_result = _make_pr_view_result(pr_draft=pr_draft, pr_body=pr_body)
        state = _make_state()

        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], expected["status"])
        self.assertEqual(result["failure_code"], expected["failure_code"])
        self.assertEqual(result["failure_category"], expected["failure_category"])
        self.assertFalse(result["allow_accept"])


# ---------------------------------------------------------------------------
# TC-02: PR 본문 임시 문구 → BLOCKED (pr_body_temporary) [normal]
# ---------------------------------------------------------------------------

class TestTempBodyBlocked(unittest.TestCase):
    """정상 케이스 — 임시 문구 PR 본문 차단."""

    def test_temp_body_blocked(self) -> None:
        """PR 본문에 '작업 중입니다' 임시 문구가 있으면 BLOCKED(pr_body_temporary)를 반환한다."""
        pr_body = (
            "## 작업 요약\n작업 중입니다.\n\n"
            "## 사용자가 확인할 결과물\n결과\n\n"
            "## 기대 결과와 실제 결과\n-\n\n"
            "## 중요한 선택과 트레이드오프\n-\n\n"
            "## 검증\n-"
        )
        mock_result = _make_pr_view_result(pr_draft=False, pr_body=pr_body)
        state = _make_state()

        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "pr_body_temporary")
        self.assertEqual(result["failure_category"], "missing_evidence")
        self.assertFalse(result["allow_accept"])


# ---------------------------------------------------------------------------
# TC-03: 필수 섹션 누락 → BLOCKED [normal]
# ---------------------------------------------------------------------------

class TestMissingSectionBlocked(unittest.TestCase):
    """정상 케이스 — 필수 섹션 누락 차단."""

    def test_missing_section_blocked(self) -> None:
        """PR 본문에 필수 섹션('기대 결과와 실제 결과')이 없으면 BLOCKED(pr_body_incomplete)를 반환한다."""
        oracle = _load_oracle("normal_body_incomplete_blocked")
        call_args = oracle["input"]["call_args"]
        expected = oracle["expected"]

        pr_body = call_args["pr_body"]  # 불완전한 PR 본문
        pr_draft = call_args["pr_draft"]  # False

        mock_result = _make_pr_view_result(pr_draft=pr_draft, pr_body=pr_body)
        state = _make_state()

        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], expected["status"])
        self.assertEqual(result["failure_code"], expected["failure_code"])
        self.assertEqual(result["failure_category"], expected["failure_category"])
        self.assertFalse(result["allow_accept"])
        # 누락 섹션이 반환되어야 한다
        self.assertIsInstance(result.get("missing_sections"), list)
        self.assertGreater(len(result.get("missing_sections", [])), 0)

    def test_missing_result_section_blocked(self) -> None:
        """PR 본문에 '사용자가 확인할 결과물' 섹션이 없으면 BLOCKED를 반환한다."""
        # 작업 요약은 있지만 결과물 섹션 없음
        pr_body = (
            "## 작업 요약\n완료\n\n"
            "## 기대 결과와 실제 결과\n-\n\n"
            "## 중요한 선택과 트레이드오프\n-\n\n"
            "## 검증\nPASS"
        )
        mock_result = _make_pr_view_result(pr_draft=False, pr_body=pr_body)
        state = _make_state()

        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "pr_body_incomplete")
        self.assertFalse(result["allow_accept"])
        missing = result.get("missing_sections", [])
        self.assertTrue(any("사용자가 확인할 결과물" in s for s in missing))


# ---------------------------------------------------------------------------
# TC-04: acceptance packet "정보 부족" → BLOCKED [normal]
# ---------------------------------------------------------------------------

class TestPacketInsufficientBlocked(unittest.TestCase):
    """정상 케이스 — acceptance packet 정보 부족 차단 (GitHub 댓글 기준)."""

    def test_packet_insufficient_blocked(self) -> None:
        """GitHub PR 댓글의 acceptance packet 상태가 '정보 부족'이면 BLOCKED를 반환한다."""
        oracle = _load_oracle("normal_packet_insufficient_blocked")
        call_args = oracle["input"]["call_args"]
        expected = oracle["expected"]

        pr_body = call_args["pr_body"]
        pr_draft = call_args["pr_draft"]  # False

        pr_view = _make_pr_view_result(pr_draft=pr_draft, pr_body=pr_body)
        # acceptance packet 댓글이 '정보 부족' 상태
        comments = _make_comments_result([_packet_comment("정보 부족")])
        state = _make_state()

        with patch("subprocess.run",
                   side_effect=_make_subprocess_side_effect(pr_view, comments)):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], expected["status"])
        self.assertEqual(result["failure_code"], expected["failure_code"])
        self.assertEqual(result["failure_category"], expected["failure_category"])
        self.assertFalse(result["allow_accept"])

    def test_packet_insufficient_via_local_file_fallback(self) -> None:
        """환경변수 fallback: 로컬 파일에 '정보 부족'이 있으면 BLOCKED를 반환한다."""
        pr_view = _make_pr_view_result(pr_draft=False, pr_body=_COMPLETE_PR_BODY)
        state = _make_state()

        with tempfile.TemporaryDirectory() as tmp:
            packet_path = pathlib.Path(tmp) / "human_acceptance_packet.md"
            packet_path.write_text(
                "판단 정보 상태: **정보 부족**\n상세 내용 없음", encoding="utf-8")
            env_patch = {"PIPELINE_TEST_ACCEPTANCE_PACKET_PATH": str(packet_path)}
            with patch("subprocess.run", return_value=pr_view), \
                 patch.dict(os.environ, env_patch):
                result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "acceptance_packet_insufficient")
        self.assertFalse(result["allow_accept"])


# ---------------------------------------------------------------------------
# TC-05: GitHub CI PASS여도 Draft PR이면 BLOCKED (엣지 케이스) [edge]
# ---------------------------------------------------------------------------

class TestCiPassButDraftStillBlocked(unittest.TestCase):
    """엣지 케이스 — GitHub CI PASS여도 Draft PR이면 차단."""

    def test_ci_pass_draft_still_blocked(self) -> None:
        """GitHub CI가 PASS여도 PR이 Draft이면 BLOCKED(pr_is_draft)를 반환한다."""
        oracle = _load_oracle("edge_ci_pass_but_readiness_fail")
        call_args = oracle["input"]["call_args"]
        expected = oracle["expected"]

        pr_draft = call_args["pr_draft"]  # True
        pr_body = call_args["pr_body"]

        mock_result = _make_pr_view_result(pr_draft=pr_draft, pr_body=pr_body)
        state = _make_state()

        # CI 상태는 외부에서 확인됨 — _check_acceptance_readiness는 PR 메타만 체크
        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], expected["status"])
        self.assertEqual(result["failure_code"], expected["failure_code"])
        self.assertFalse(result["allow_accept"])


# ---------------------------------------------------------------------------
# TC-06: 모든 조건 통과 시 PASS + allow_accept=True [normal]
# ---------------------------------------------------------------------------

class TestAllPassAllowed(unittest.TestCase):
    """정상 케이스 — 모든 readiness 조건 통과."""

    def test_all_pass_allowed(self) -> None:
        """모든 readiness 조건 통과 시 PASS + allow_accept=True를 반환한다."""
        oracle = _load_oracle("normal_readiness_pass_allowed")
        call_args = oracle["input"]["call_args"]
        expected = oracle["expected"]

        pr_body = call_args["pr_body"]
        pr_draft = call_args["pr_draft"]  # False

        pr_view = _make_pr_view_result(pr_draft=pr_draft, pr_body=pr_body)
        # acceptance packet 댓글이 '판단 가능' 상태
        comments = _make_comments_result([_packet_comment("판단 가능")])
        state = _make_state()

        with patch("subprocess.run",
                   side_effect=_make_subprocess_side_effect(pr_view, comments)):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], expected["status"])
        self.assertTrue(result["allow_accept"])
        self.assertEqual(result["failure_code"], expected["failure_code"])
        self.assertIsNone(result["blocked_reason"])


# ---------------------------------------------------------------------------
# TC-07: BLOCKED 시 failure_packet schema_v2 생성 검증
# ---------------------------------------------------------------------------

class TestFailurePacketSchemaV2(unittest.TestCase):
    """failure_packet이 schema_version=2 형식으로 생성되는지 확인."""

    def test_failure_packet_schema_v2(self) -> None:
        """Draft PR BLOCKED 시 readiness 결과가 schema_v2 호환 필드를 포함한다."""
        pr_draft = True
        pr_body = _COMPLETE_PR_BODY
        mock_result = _make_pr_view_result(pr_draft=pr_draft, pr_body=pr_body)
        state = _make_state()

        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        # _check_acceptance_readiness 자체는 failure_packet을 기록하지 않음 (호출자가 기록)
        # 반환값에서 schema_v2 호환 필드 확인
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "pr_is_draft")
        self.assertEqual(result["failure_category"], "missing_evidence")
        self.assertEqual(result["return_phase"], "build")
        self.assertFalse(result["allow_accept"])

    def test_readiness_fail_does_not_allow_accept(self) -> None:
        """readiness FAIL 시 allow_accept=False이고 ACCEPT 처리가 불가능하다."""
        # 임시 문구 포함 + 필수 섹션 완비
        pr_body = (
            "## 작업 요약\n아직 Dev 구현 완료 전 입니다.\n\n"
            "## 사용자가 확인할 결과물\n-\n\n"
            "## 기대 결과와 실제 결과\n-\n\n"
            "## 중요한 선택과 트레이드오프\n-\n\n"
            "## 검증\n-"
        )
        mock_result = _make_pr_view_result(pr_draft=False, pr_body=pr_body)
        state = _make_state()

        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        self.assertFalse(result["allow_accept"])
        self.assertEqual(result["status"], "BLOCKED")


# ---------------------------------------------------------------------------
# Blocker 1 추가 테스트: gh CLI 실패는 PASS가 아니라 BLOCKED
# ---------------------------------------------------------------------------

class TestGhCliFailureBlocked(unittest.TestCase):
    """Blocker 1 — gh CLI 없음/실패는 PASS가 아니라 BLOCKED."""

    def test_gh_cli_not_available_blocked(self) -> None:
        """추가 케이스 1: gh CLI가 없으면 readiness BLOCKED(gh_cli_not_available)."""
        state = _make_state()

        with patch("subprocess.run", side_effect=FileNotFoundError("gh not found")):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "gh_cli_not_available")
        self.assertEqual(result["failure_category"], "missing_evidence")
        self.assertEqual(result["return_phase"], "build")
        self.assertFalse(result["allow_accept"])

    def test_gh_pr_view_failed_blocked(self) -> None:
        """추가 케이스 2: gh pr view 실패(exit code != 0)이면 BLOCKED(pr_view_failed)."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "gh: authentication required"
        state = _make_state()

        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "pr_view_failed")
        self.assertEqual(result["return_phase"], "build")
        self.assertFalse(result["allow_accept"])

    def test_pr_not_found_blocked(self) -> None:
        """gh pr view 가 'no pull requests found'를 반환하면 BLOCKED(pr_not_found)."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "no pull requests found for branch impl/IMP-20260519-E979"
        state = _make_state()

        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "pr_not_found")
        self.assertFalse(result["allow_accept"])

    def test_pr_metadata_parse_error_blocked(self) -> None:
        """gh pr view 출력이 JSON이 아니면 BLOCKED(pr_metadata_parse_error)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "this is not json {{{"
        mock_result.stderr = ""
        state = _make_state()

        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "pr_metadata_parse_error")
        self.assertFalse(result["allow_accept"])


# ---------------------------------------------------------------------------
# Blocker 2 추가 테스트: GitHub PR 댓글 기반 acceptance packet readiness
# ---------------------------------------------------------------------------

class TestAcceptancePacketViaGithub(unittest.TestCase):
    """Blocker 2 — GitHub PR 댓글 기반 acceptance packet readiness 검증."""

    def test_acceptance_packet_comment_missing_blocked(self) -> None:
        """추가 케이스 3: acceptance packet GitHub 댓글이 없으면 BLOCKED."""
        pr_view = _make_pr_view_result(pr_draft=False, pr_body=_COMPLETE_PR_BODY)
        # acceptance packet 태그가 없는 일반 댓글만 존재
        comments = _make_comments_result(["일반 댓글입니다. 태그 없음."])
        state = _make_state()

        with patch("subprocess.run",
                   side_effect=_make_subprocess_side_effect(pr_view, comments)):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "acceptance_packet_missing")
        self.assertFalse(result["allow_accept"])

    def test_acceptance_packet_no_comments_blocked(self) -> None:
        """댓글이 0개이면 acceptance packet 댓글 없음으로 BLOCKED."""
        pr_view = _make_pr_view_result(pr_draft=False, pr_body=_COMPLETE_PR_BODY)
        comments = _make_comments_result([])  # 댓글 0개
        state = _make_state()

        with patch("subprocess.run",
                   side_effect=_make_subprocess_side_effect(pr_view, comments)):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "acceptance_packet_missing")
        self.assertFalse(result["allow_accept"])

    def test_acceptance_packet_insufficient_in_comment_blocked(self) -> None:
        """추가 케이스 4: GitHub 댓글에 '정보 부족'이 있으면 BLOCKED."""
        pr_view = _make_pr_view_result(pr_draft=False, pr_body=_COMPLETE_PR_BODY)
        comments = _make_comments_result([_packet_comment("정보 부족")])
        state = _make_state()

        with patch("subprocess.run",
                   side_effect=_make_subprocess_side_effect(pr_view, comments)):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "acceptance_packet_insufficient")
        self.assertEqual(result["failure_category"], "missing_evidence")
        self.assertFalse(result["allow_accept"])

    def test_acceptance_packet_pass_without_local_file(self) -> None:
        """추가 케이스 5: 로컬 packet 파일이 없어도 GitHub 댓글이 '판단 가능'이면 PASS."""
        pr_view = _make_pr_view_result(pr_draft=False, pr_body=_COMPLETE_PR_BODY)
        comments = _make_comments_result([_packet_comment("판단 가능")])
        state = _make_state()

        # 로컬 파일은 일부러 모킹하지 않음 — GitHub 댓글만으로 PASS 되어야 함
        with patch("subprocess.run",
                   side_effect=_make_subprocess_side_effect(pr_view, comments)), \
             patch("pathlib.Path.is_file", return_value=False):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["allow_accept"])
        self.assertEqual(result["failure_code"], "")
        self.assertIsNone(result["blocked_reason"])

    def test_pr_comments_fetch_failed_blocked(self) -> None:
        """추가 케이스 6: GitHub 댓글 조회 실패(API 오류)이면 BLOCKED(pr_comments_fetch_failed)."""
        pr_view = _make_pr_view_result(pr_draft=False, pr_body=_COMPLETE_PR_BODY)
        # gh api 가 exit code != 0 으로 실패
        comments = _make_comments_result(
            [], returncode=1, stderr="API rate limit exceeded")
        state = _make_state()

        with patch("subprocess.run",
                   side_effect=_make_subprocess_side_effect(pr_view, comments)):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "pr_comments_fetch_failed")
        self.assertFalse(result["allow_accept"])

    def test_pr_comments_invalid_json_blocked(self) -> None:
        """GitHub 댓글 응답이 JSON이 아니면 BLOCKED(pr_comments_fetch_failed)."""
        pr_view = _make_pr_view_result(pr_draft=False, pr_body=_COMPLETE_PR_BODY)
        bad_comments = MagicMock()
        bad_comments.returncode = 0
        bad_comments.stderr = ""
        bad_comments.stdout = "not-json-content }}}"
        state = _make_state()

        with patch("subprocess.run",
                   side_effect=_make_subprocess_side_effect(pr_view, bad_comments)):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "pr_comments_fetch_failed")
        self.assertFalse(result["allow_accept"])


# ---------------------------------------------------------------------------
# Blocker 3 추가 테스트: 필수 섹션 OR 조건 + TEMPORARY_PATTERNS
# ---------------------------------------------------------------------------

class TestRequiredSectionOrCondition(unittest.TestCase):
    """Blocker 3 — 필수 섹션 첫 항목 OR 조건 / TEMPORARY_PATTERNS."""

    def test_final_judgment_summary_section_passes(self) -> None:
        """추가 케이스 7-a: '최종 판단 요약' 섹션만 있는 PR body도 통과(첫 섹션 OR 조건)."""
        # 첫 섹션을 '작업 요약' 대신 '최종 판단 요약'으로 작성
        pr_body = (
            "## 최종 판단 요약\n작업 완료\n\n"
            "## 사용자가 확인할 결과물\n- pipeline.py 수정\n\n"
            "## 기대 결과와 실제 결과\n| 항목 | 기대 | 실제 |\n|---|---|---|\n| 검증 | 통과 | 통과 |\n\n"
            "## 중요한 선택과 트레이드오프\n정규식 사용\n\n"
            "## 검증\n테스트 PASS"
        )
        pr_view = _make_pr_view_result(pr_draft=False, pr_body=pr_body)
        comments = _make_comments_result([_packet_comment("판단 가능")])
        state = _make_state()

        with patch("subprocess.run",
                   side_effect=_make_subprocess_side_effect(pr_view, comments)):
            result = pipeline._check_acceptance_readiness(state)

        # 첫 섹션 OR 조건으로 '최종 판단 요약'이 인정되어 PASS 되어야 함
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["allow_accept"])
        self.assertEqual(result["missing_sections"], [])

    def test_request_and_result_section_passes(self) -> None:
        """추가 케이스 7-b: '이번 요청과 완료 결과' 섹션도 첫 섹션 OR 조건으로 통과."""
        pr_body = (
            "## 이번 요청과 완료 결과\n작업 완료\n\n"
            "## 사용자가 확인할 결과물\n- pipeline.py 수정\n\n"
            "## 기대 결과와 실제 결과\n결과 표\n\n"
            "## 중요한 선택과 트레이드오프\n키워드 방식\n\n"
            "## 검증\n테스트 PASS"
        )
        pr_view = _make_pr_view_result(pr_draft=False, pr_body=pr_body)
        comments = _make_comments_result([_packet_comment("판단 가능")])
        state = _make_state()

        with patch("subprocess.run",
                   side_effect=_make_subprocess_side_effect(pr_view, comments)):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["allow_accept"])

    def test_draft_pr_phrase_in_body_blocked(self) -> None:
        """추가 케이스 7-c: 'Draft PR' 문구만 있어도 BLOCKED(TEMPORARY_PATTERNS 확인).

        임시 문구 탐지는 줄 단위 접두 매칭 — 'Draft PR'로 시작하는 줄이 있으면
        필수 섹션을 모두 갖춰도 placeholder PR로 간주하여 차단한다.
        """
        # 필수 섹션은 모두 갖췄지만 본문 한 줄이 'Draft PR' 임시 문구로 시작
        pr_body = (
            "## 작업 요약\nDraft PR\n\n"
            "## 사용자가 확인할 결과물\n- pipeline.py 수정\n\n"
            "## 기대 결과와 실제 결과\n결과 표\n\n"
            "## 중요한 선택과 트레이드오프\n키워드 방식\n\n"
            "## 검증\n테스트 PASS"
        )
        pr_view = _make_pr_view_result(pr_draft=False, pr_body=pr_body)
        comments = _make_comments_result([_packet_comment("판단 가능")])
        state = _make_state()

        with patch("subprocess.run",
                   side_effect=_make_subprocess_side_effect(pr_view, comments)):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "pr_body_temporary")
        self.assertFalse(result["allow_accept"])

    def test_normal_body_mentioning_draft_pr_word_passes(self) -> None:
        """거짓 양성 방지: 정상 본문 문장 중간의 'Draft PR' 언급은 차단하지 않는다.

        oracle normal_readiness_pass_allowed 처럼 기능 설명 문장에
        'Draft PR' 단어가 포함되어도 줄 접두가 아니면 PASS 되어야 한다.
        """
        pr_body = (
            "## 작업 요약\n"
            "이 PR은 Draft PR 상태와 임시 본문 상태에서 gates accept를 차단합니다.\n\n"
            "## 사용자가 확인할 결과물\n- pipeline.py 수정\n\n"
            "## 기대 결과와 실제 결과\n결과 표\n\n"
            "## 중요한 선택과 트레이드오프\n키워드 방식\n\n"
            "## 검증\n테스트 PASS"
        )
        pr_view = _make_pr_view_result(pr_draft=False, pr_body=pr_body)
        comments = _make_comments_result([_packet_comment("판단 가능")])
        state = _make_state()

        with patch("subprocess.run",
                   side_effect=_make_subprocess_side_effect(pr_view, comments)):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["allow_accept"])

    def test_temporary_patterns_includes_draft_pr(self) -> None:
        """TEMPORARY_PR_BODY_PATTERNS 상수에 'Draft PR'이 포함되어 있다."""
        self.assertIn("Draft PR", pipeline.TEMPORARY_PR_BODY_PATTERNS)

    def test_required_sections_first_item_is_or_group(self) -> None:
        """PR_REQUIRED_SECTIONS 첫 항목이 3개 OR 후보 tuple이다."""
        first = pipeline.PR_REQUIRED_SECTIONS[0]
        self.assertIsInstance(first, tuple)
        self.assertIn("작업 요약", first)
        self.assertIn("최종 판단 요약", first)
        self.assertIn("이번 요청과 완료 결과", first)


if __name__ == "__main__":
    unittest.main()
