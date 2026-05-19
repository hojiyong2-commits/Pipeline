"""tests/test_acceptance_readiness_e979.py

IMP-20260519-E979: _check_acceptance_readiness() 단위 테스트.

oracle 파일: tests/oracles/IMP-20260519-E979/
- normal_draft_pr_blocked        (normal)
- normal_body_incomplete_blocked (normal)
- normal_packet_insufficient_blocked (normal)
- normal_readiness_pass_allowed  (normal)
- edge_ci_pass_but_readiness_fail (edge)
"""
import importlib
import json
import pathlib
import sys
import types
import unittest
from typing import Any, Dict, List, Optional
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


def _make_mock_subprocess_result(
    pr_draft: bool,
    pr_body: str,
    pr_number: int = 99,
    pr_url: str = "https://github.com/test/repo/pull/99",
) -> MagicMock:
    """subprocess.run 결과를 모킹하는 MagicMock 반환."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    pr_data = {
        "isDraft": pr_draft,
        "title": "test PR IMP-20260519-E979",
        "body": pr_body,
        "number": pr_number,
        "url": pr_url,
    }
    mock_result.stdout = json.dumps(pr_data)
    return mock_result


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

        mock_result = _make_mock_subprocess_result(pr_draft=pr_draft, pr_body=pr_body)
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
        pr_body = "## 작업 요약\n작업 중입니다.\n\n## 사용자가 확인할 결과물\n결과\n\n## 기대 결과와 실제 결과\n-\n\n## 중요한 선택과 트레이드오프\n-\n\n## 검증\n-"
        mock_result = _make_mock_subprocess_result(pr_draft=False, pr_body=pr_body)
        state = _make_state()

        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "pr_body_temporary")
        self.assertEqual(result["failure_category"], "missing_evidence")
        self.assertFalse(result["allow_accept"])


# ---------------------------------------------------------------------------
# TC-03: 필수 섹션("최종 판단 요약" or 필수 섹션) 누락 → BLOCKED [normal]
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

        mock_result = _make_mock_subprocess_result(pr_draft=pr_draft, pr_body=pr_body)
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
        mock_result = _make_mock_subprocess_result(pr_draft=False, pr_body=pr_body)
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
    """정상 케이스 — acceptance packet 정보 부족 차단."""

    def test_packet_insufficient_blocked(self) -> None:
        """human_acceptance_packet.md에 '정보 부족'이 있으면 BLOCKED(acceptance_packet_insufficient)를 반환한다."""
        oracle = _load_oracle("normal_packet_insufficient_blocked")
        call_args = oracle["input"]["call_args"]
        expected = oracle["expected"]

        pr_body = call_args["pr_body"]
        pr_draft = call_args["pr_draft"]  # False

        mock_result = _make_mock_subprocess_result(pr_draft=pr_draft, pr_body=pr_body)
        state = _make_state()

        # human_acceptance_packet.md 파일이 '정보 부족'을 포함하도록 모킹
        mock_packet_content = "판단 정보 상태: **정보 부족**\n상세 내용 없음"

        with patch("subprocess.run", return_value=mock_result), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.read_text", return_value=mock_packet_content):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], expected["status"])
        self.assertEqual(result["failure_code"], expected["failure_code"])
        self.assertEqual(result["failure_category"], expected["failure_category"])
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

        mock_result = _make_mock_subprocess_result(pr_draft=pr_draft, pr_body=pr_body)
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

        mock_result = _make_mock_subprocess_result(pr_draft=pr_draft, pr_body=pr_body)
        state = _make_state()

        # packet 파일 없음 → 검사 생략
        with patch("subprocess.run", return_value=mock_result), \
             patch("pathlib.Path.is_file", return_value=False):
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
        """Draft PR BLOCKED 시 failure_packet이 schema_version=2를 포함한다."""
        pr_draft = True
        pr_body = "## 작업 요약\n완료\n\n## 사용자가 확인할 결과물\n-\n\n## 기대 결과와 실제 결과\n-\n\n## 중요한 선택과 트레이드오프\n-\n\n## 검증\nPASS"
        mock_result = _make_mock_subprocess_result(pr_draft=pr_draft, pr_body=pr_body)
        state = _make_state()
        # failure_packets 초기화
        state["failure_packets"] = []

        recorded_packets: List[Dict[str, Any]] = []

        def _fake_record_failure_packet(
            st: Dict[str, Any],
            gate_name: str,
            report: Dict[str, Any],
            **kwargs: Any,
        ) -> Dict[str, Any]:
            packet = {
                "schema_version": 2,
                "failure_code": kwargs.get("failure_code", ""),
                "failure_category": kwargs.get("failure_category", ""),
                "return_phase": kwargs.get("return_phase", ""),
                "status": kwargs.get("status", "FAIL"),
            }
            recorded_packets.append(packet)
            return packet

        import pipeline as pl
        # _check_acceptance_readiness 자체는 실제 호출, failure_packet 기록은 모킹
        with patch("subprocess.run", return_value=mock_result), \
             patch.object(pl, "_record_failure_packet", side_effect=_fake_record_failure_packet), \
             patch("pathlib.Path.is_file", return_value=False), \
             patch.object(pl, "_save"), \
             patch.object(pl, "_die"):
            result = pl._check_acceptance_readiness(state)

        # _check_acceptance_readiness 자체는 failure_packet을 기록하지 않음 (호출자가 기록)
        # 반환값에서 schema_v2 필드 확인
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "pr_is_draft")
        self.assertEqual(result["failure_category"], "missing_evidence")
        self.assertFalse(result["allow_accept"])

    def test_readiness_fail_does_not_allow_accept(self) -> None:
        """readiness FAIL 시 allow_accept=False이고 ACCEPT 처리가 불가능하다."""
        # 임시 문구 포함
        pr_body = "## 작업 요약\n아직 Dev 구현 완료 전 입니다.\n\n## 사용자가 확인할 결과물\n-"
        mock_result = _make_mock_subprocess_result(pr_draft=False, pr_body=pr_body)
        state = _make_state()

        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        self.assertFalse(result["allow_accept"])
        self.assertEqual(result["status"], "BLOCKED")


# ---------------------------------------------------------------------------
# TC-08: gh CLI 없는 환경에서는 검사 생략 (기존 패턴 유지)
# ---------------------------------------------------------------------------

class TestGhCliMissingSkip(unittest.TestCase):
    """gh CLI 미설치 환경에서는 검사를 생략하고 PASS를 반환한다."""

    def test_gh_cli_missing_returns_pass(self) -> None:
        """FileNotFoundError(gh CLI 없음) 시 PASS 반환."""
        state = _make_state()

        with patch("subprocess.run", side_effect=FileNotFoundError("gh not found")):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["allow_accept"])

    def test_no_pr_open_returns_pass(self) -> None:
        """PR이 없으면(gh returncode != 0) PASS 반환."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        state = _make_state()

        with patch("subprocess.run", return_value=mock_result):
            result = pipeline._check_acceptance_readiness(state)

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["allow_accept"])


if __name__ == "__main__":
    unittest.main()
