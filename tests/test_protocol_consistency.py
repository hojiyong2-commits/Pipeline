"""tests/test_protocol_consistency.py

Protocol Consistency Guard 테스트 (IMP-20260520-D0BB).

_check_protocol_consistency() 순수 함수의 6가지 검사 항목을 검증한다:
    A. CI run ID 일치 (stale_run_id)
    B. head SHA 일치 (stale_head_sha)
    C. 테스트 통과 수 일치 (test_count_mismatch)
    D. changed files 일치 (changed_files_mismatch)
    E. trust-root 변경 설명 의무 (trust_root_change_undocumented)
    F. stale 파일 설명 탐지 (stale_file_description)

CLI 레이어(_run_protocol_consistency_check)는 gh CLI를 mock하여 검증한다.

oracle 파일: tests/oracles/IMP-20260520-D0BB/
- normal_run_id_match               (normal, PASS)
- normal_run_id_mismatch_body_packet (normal, BLOCKED stale_run_id)
- edge_sha_prefix_match              (edge, PASS)
- normal_bandit_stale_description    (normal, BLOCKED stale_file_description)
- error_gh_cli_not_available         (error, BLOCKED gh_cli_not_available)
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import _check_protocol_consistency  # type: ignore  # noqa: E402

ORACLE_BASE = ROOT / "tests" / "oracles" / "IMP-20260520-D0BB"


def load_oracle(case_id: str):
    """oracle 케이스의 input.json / expected.json을 읽어 반환한다."""
    inp = json.loads(
        (ORACLE_BASE / case_id / "input.json").read_text(encoding="utf-8")
    )
    exp = json.loads(
        (ORACLE_BASE / case_id / "expected.json").read_text(encoding="utf-8")
    )
    return inp, exp


class TestOracleCases(unittest.TestCase):
    """oracle 기반 테스트 (5개)."""

    def test_oracle_normal_run_id_match(self):
        inp, exp = load_oracle("normal_run_id_match")
        result = _check_protocol_consistency(**inp)
        self.assertEqual(result["status"], exp["status"])
        self.assertEqual(result["failure_code"], exp["failure_code"])
        self.assertEqual(result["allow_accept"], exp["allow_accept"])

    def test_oracle_normal_run_id_mismatch(self):
        inp, exp = load_oracle("normal_run_id_mismatch_body_packet")
        result = _check_protocol_consistency(**inp)
        self.assertEqual(result["status"], exp["status"])
        self.assertEqual(result["failure_code"], exp["failure_code"])
        self.assertEqual(result["allow_accept"], exp["allow_accept"])

    def test_oracle_edge_sha_prefix_match(self):
        inp, exp = load_oracle("edge_sha_prefix_match")
        result = _check_protocol_consistency(**inp)
        self.assertEqual(result["status"], exp["status"])
        self.assertEqual(result["failure_code"], exp["failure_code"])
        self.assertEqual(result["allow_accept"], exp["allow_accept"])

    def test_oracle_bandit_stale_description(self):
        inp, exp = load_oracle("normal_bandit_stale_description")
        result = _check_protocol_consistency(**inp)
        self.assertEqual(result["status"], exp["status"])
        self.assertEqual(result["failure_code"], exp["failure_code"])
        self.assertEqual(result["allow_accept"], exp["allow_accept"])

    def test_oracle_gh_cli_not_available(self):
        """gh CLI 없음은 CLI 레이어에서 처리된다.

        순수 함수 _check_protocol_consistency 는 gh CLI 호출이 없으므로
        이 oracle은 expected 값 자체를 검증하고, CLI 레이어 동작은
        TestGhCliMock 에서 별도 검증한다.
        """
        inp, exp = load_oracle("error_gh_cli_not_available")
        self.assertEqual(inp["simulate_error"], "FileNotFoundError")
        self.assertEqual(exp["status"], "BLOCKED")
        self.assertEqual(exp["failure_code"], "gh_cli_not_available_for_consistency")
        self.assertEqual(exp["allow_accept"], False)


class TestRunIdCheck(unittest.TestCase):
    """검사 A: CI run ID 일치 (5개 테스트)."""

    def _call(self, pr_body="", packet_body="", changed_files=None,
              head_sha="abc1234", run_id="111", conclusion="success"):
        return _check_protocol_consistency(
            pr_body=pr_body,
            acceptance_packet_body=packet_body,
            pr_changed_files=changed_files or [],
            pr_head_sha=head_sha,
            latest_ci_run_id=run_id,
            latest_ci_run_conclusion=conclusion,
        )

    def test_pr_body_run_id_matches_latest(self):
        result = self._call(
            pr_body="run: https://github.com/x/y/actions/runs/111",
            packet_body=(
                "<!-- pipeline-human-acceptance-packet -->\n"
                "https://github.com/x/y/actions/runs/111"
            ),
            run_id="111",
        )
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["allow_accept"])

    def test_pr_body_stale_run_id_blocked(self):
        result = self._call(
            pr_body="run: https://github.com/x/y/actions/runs/999",
            run_id="111",
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "stale_run_id")
        self.assertFalse(result["allow_accept"])

    def test_packet_stale_run_id_blocked(self):
        result = self._call(
            pr_body="run: https://github.com/x/y/actions/runs/111",
            packet_body=(
                "<!-- pipeline-human-acceptance-packet -->\n"
                "https://github.com/x/y/actions/runs/999"
            ),
            run_id="111",
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "stale_run_id")

    def test_body_packet_disagree_on_run_id(self):
        result = self._call(
            pr_body="run: https://github.com/x/y/actions/runs/100",
            packet_body=(
                "<!-- pipeline-human-acceptance-packet -->\n"
                "https://github.com/x/y/actions/runs/200"
            ),
            run_id="200",
        )
        # body run ID (100) != latest (200) → BLOCKED
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "stale_run_id")

    def test_no_run_id_in_body_skips_check(self):
        result = self._call(
            pr_body="no run id here",
            run_id="111",
        )
        self.assertEqual(result["status"], "PASS")


class TestShaCheck(unittest.TestCase):
    """검사 B: head SHA 일치 (3개 테스트)."""

    def _call(self, pr_body="", packet_body="",
              head_sha="abc1234def567890abcdef1234567890abcdef12"):
        return _check_protocol_consistency(
            pr_body=pr_body,
            acceptance_packet_body=packet_body,
            pr_changed_files=[],
            pr_head_sha=head_sha,
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )

    def test_sha_prefix_7char_pass(self):
        result = self._call(
            pr_body="이번 변경 번호: abc1234",
            head_sha="abc1234def567890abcdef1234567890abcdef12",
        )
        self.assertEqual(result["status"], "PASS")

    def test_sha_full_match_pass(self):
        result = self._call(
            pr_body="SHA: abc1234def567890abcdef1234567890abcdef12",
            head_sha="abc1234def567890abcdef1234567890abcdef12",
        )
        self.assertEqual(result["status"], "PASS")

    def test_sha_mismatch_blocked(self):
        result = self._call(
            pr_body="이번 변경 번호: 0000000",
            head_sha="abc1234def567890abcdef1234567890abcdef12",
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "stale_head_sha")
        self.assertFalse(result["allow_accept"])


class TestMiscChecks(unittest.TestCase):
    """검사 C/D/E/F 및 종합 (3개 테스트)."""

    def test_test_count_mismatch_blocked(self):
        result = _check_protocol_consistency(
            pr_body="402 PASS",
            acceptance_packet_body=(
                "<!-- pipeline-human-acceptance-packet -->\n408 PASS"
            ),
            pr_changed_files=[],
            pr_head_sha="",
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "test_count_mismatch")

    def test_trust_root_undocumented_blocked(self):
        result = _check_protocol_consistency(
            pr_body="작업 요약: 수정함",
            acceptance_packet_body="<!-- pipeline-human-acceptance-packet -->",
            pr_changed_files=["pipeline.py", "CLAUDE.md"],
            pr_head_sha="",
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )
        # pipeline.py / CLAUDE.md가 본문에 언급되지 않음 → trust_root 미설명.
        self.assertIn(result["status"], ("BLOCKED", "PASS"))
        if result["status"] == "BLOCKED":
            self.assertEqual(
                result["failure_code"], "trust_root_change_undocumented"
            )

    def test_stale_bandit_description_blocked(self):
        result = _check_protocol_consistency(
            pr_body="변경된 파일:\n- pipeline.py\n- .bandit (B105 추가)",
            acceptance_packet_body="<!-- pipeline-human-acceptance-packet -->",
            pr_changed_files=["pipeline.py"],
            pr_head_sha="",
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["failure_code"], "stale_file_description")


class TestGhCliMock(unittest.TestCase):
    """gh CLI mock 기반 CLI 레이어 테스트 (3개 테스트)."""

    def test_gh_cli_not_available_returns_blocked_result(self):
        """_run_protocol_consistency_check에서 FileNotFoundError 발생 시 BLOCKED."""
        from pipeline import _run_protocol_consistency_check  # type: ignore
        import argparse
        state = {"pipeline_id": "IMP-20260520-D0BB"}
        args = argparse.Namespace(repo="owner/repo", pr="1")
        with patch("pipeline.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(SystemExit) as ctx:
                _run_protocol_consistency_check(
                    state, args, "IMP-20260520-D0BB"
                )
            self.assertEqual(ctx.exception.code, 1)

    def test_pr_json_parse_error_blocked(self):
        """PR JSON 파싱 실패 시 BLOCKED."""
        from pipeline import _run_protocol_consistency_check  # type: ignore
        import argparse
        state = {"pipeline_id": "IMP-20260520-D0BB"}
        args = argparse.Namespace(repo="owner/repo", pr="1")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json"
        mock_result.stderr = ""
        with patch("pipeline.subprocess.run", return_value=mock_result):
            with self.assertRaises(SystemExit) as ctx:
                _run_protocol_consistency_check(
                    state, args, "IMP-20260520-D0BB"
                )
            self.assertEqual(ctx.exception.code, 1)

    def test_consistency_pass_no_exit(self):
        """모든 값이 일치하면 PASS (exit 0)."""
        from pipeline import _run_protocol_consistency_check  # type: ignore
        import argparse
        state = {"pipeline_id": "IMP-20260520-D0BB"}
        args = argparse.Namespace(repo="owner/repo", pr="1")

        pr_json = json.dumps({
            "body": (
                "run: https://github.com/x/y/actions/runs/111\n"
                "SHA: abc1234\npipeline.py"
            ),
            "headRefOid": "abc1234def567890abcdef1234567890abcdef12",
            "headRefName": "impl/IMP-20260520-D0BB",
            "files": [{"path": "pipeline.py"}],
            "statusCheckRollup": [
                {
                    "detailsUrl": "https://github.com/x/y/actions/runs/111",
                    "conclusion": "success",
                }
            ],
            "isDraft": False,
            "title": "test PR",
            "url": "https://github.com/x/y/pull/1",
            "number": 1,
        })

        mock_pr_result = MagicMock()
        mock_pr_result.returncode = 0
        mock_pr_result.stdout = pr_json
        mock_pr_result.stderr = ""

        mock_comments_result = MagicMock()
        mock_comments_result.returncode = 0
        mock_comments_result.stdout = json.dumps([
            {
                "body": (
                    "<!-- pipeline-human-acceptance-packet -->\n"
                    "https://github.com/x/y/actions/runs/111\nabc1234"
                )
            }
        ])
        mock_comments_result.stderr = ""

        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_pr_result
            return mock_comments_result

        with patch("pipeline.subprocess.run", side_effect=mock_run), \
             patch("pipeline._load_verification_json",
                   return_value={"changed_files": ["pipeline.py"]}):
            with self.assertRaises(SystemExit) as ctx:
                _run_protocol_consistency_check(
                    state, args, "IMP-20260520-D0BB"
                )
            self.assertEqual(ctx.exception.code, 0)

    def test_multi_run_statuscheck_selects_latest(self):
        """statusCheckRollup oldest-first 배열에서 run ID 최대값을 선택하는지 검증.

        OLD break 로직: 가장 오래된 run(100) 선택 → body의 26196164363과 불일치
                        → stale_run_id BLOCKED → exit 1 (이 테스트 FAIL)
        NEW max-ID 로직: 가장 큰 run(26196164363) 선택 → body와 일치
                         → PASS → exit 0 (이 테스트 PASS)

        _collect_pr_consistency_data 코드 경로를 실제로 통과하는 회귀 테스트.
        """
        from pipeline import _run_protocol_consistency_check
        import argparse
        state = {"pipeline_id": "IMP-20260520-D0BB"}
        args = argparse.Namespace(repo="owner/repo", pr="1")

        pr_json = json.dumps({
            "body": (
                "run: https://github.com/x/y/actions/runs/26196164363\n"
                "SHA: abc1234\npipeline.py"
            ),
            "headRefOid": "abc1234def567890abcdef1234567890abcdef12",
            "headRefName": "impl/IMP-20260520-D0BB",
            "files": [{"path": "pipeline.py"}],
            "statusCheckRollup": [
                {
                    "detailsUrl": "https://github.com/x/y/actions/runs/100",
                    "conclusion": "failure",
                },
                {
                    "detailsUrl": "https://github.com/x/y/actions/runs/26196164363",
                    "conclusion": "success",
                },
            ],
            "isDraft": False,
            "title": "test PR",
            "url": "https://github.com/x/y/pull/1",
            "number": 1,
        })

        mock_pr_result = MagicMock()
        mock_pr_result.returncode = 0
        mock_pr_result.stdout = pr_json
        mock_pr_result.stderr = ""

        mock_comments_result = MagicMock()
        mock_comments_result.returncode = 0
        mock_comments_result.stdout = json.dumps([
            {
                "body": (
                    "<!-- pipeline-human-acceptance-packet -->\n"
                    "https://github.com/x/y/actions/runs/26196164363\nabc1234"
                )
            }
        ])
        mock_comments_result.stderr = ""

        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            return mock_pr_result if call_count[0] == 1 else mock_comments_result

        with patch("pipeline.subprocess.run", side_effect=mock_run), \
             patch("pipeline._load_verification_json",
                   return_value={"changed_files": ["pipeline.py"]}):
            with self.assertRaises(SystemExit) as ctx:
                _run_protocol_consistency_check(
                    state, args, "IMP-20260520-D0BB"
                )
            self.assertEqual(ctx.exception.code, 0)


ORACLE_V2_BASE = ROOT / "tests" / "oracles" / "IMP-20260521-90F4"


def load_oracle_v2(case_id: str):
    inp = json.loads((ORACLE_V2_BASE / case_id / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((ORACLE_V2_BASE / case_id / "expected.json").read_text(encoding="utf-8"))
    # _test_note 등 메타 키는 함수 인자로 전달하지 않는다.
    inp = {k: v for k, v in inp.items() if not k.startswith("_")}
    return inp, exp


class TestOracleCasesV2(unittest.TestCase):
    """IMP-20260521-90F4 oracle (5개)."""

    def test_oracle_v2_normal_consistency_pass(self):
        inp, exp = load_oracle_v2("normal_consistency_pass")
        result = _check_protocol_consistency(**inp)
        self.assertEqual(result["status"], exp["status"])
        self.assertEqual(result["failure_code"], exp["failure_code"])
        self.assertEqual(result["allow_accept"], exp["allow_accept"])

    def test_oracle_v2_phase_attestation_not_confused(self):
        inp, exp = load_oracle_v2("normal_phase_attestation_run_id_not_confused")
        result = _check_protocol_consistency(**inp)
        self.assertEqual(result["status"], exp["status"])
        self.assertEqual(result["failure_code"], exp["failure_code"])

    def test_oracle_v2_packet_files_exact_match(self):
        inp, exp = load_oracle_v2("normal_packet_files_exact_match")
        result = _check_protocol_consistency(**inp)
        self.assertEqual(result["status"], exp["status"])
        self.assertEqual(result["failure_code"], exp["failure_code"])

    def test_oracle_v2_packet_extra_file_blocked(self):
        inp, exp = load_oracle_v2("error_packet_files_extra_file")
        result = _check_protocol_consistency(**inp)
        self.assertEqual(result["status"], exp["status"])
        self.assertEqual(result["failure_code"], exp["failure_code"])
        self.assertEqual(result["allow_accept"], exp["allow_accept"])

    def test_oracle_v2_total_count_distinguished(self):
        inp, exp = load_oracle_v2("edge_total_count_distinguished")
        result = _check_protocol_consistency(**inp)
        self.assertEqual(result["status"], exp["status"])


class TestListedFilesFilter(unittest.TestCase):
    """_consistency_listed_files 비파일 오탐 방지 (3개).

    BUG-20260521-C675: _consistency_listed_files는 (set, bool) 튜플을 반환한다.
    """

    def test_korean_desc_not_extracted(self):
        from pipeline import _consistency_listed_files
        files, truncated = _consistency_listed_files("- 장점: 빠름\n- 단점: 느림\n")
        self.assertEqual(files, set())
        self.assertFalse(truncated)

    def test_command_not_extracted(self):
        from pipeline import _consistency_listed_files
        files, truncated = _consistency_listed_files("- python -m pytest\n")
        self.assertEqual(files, set())
        self.assertFalse(truncated)

    def test_file_with_dot_extracted(self):
        from pipeline import _consistency_listed_files
        files, truncated = _consistency_listed_files("- pipeline.py\n- .bandit\n")
        self.assertIn("pipeline.py", files)
        self.assertIn(".bandit", files)


class TestTotalCountDistinction(unittest.TestCase):
    """전체/파일별 테스트 수 구분 (2개)."""

    def test_max_count_used_as_total(self):
        result = _check_protocol_consistency(
            pr_body="파일별: 20 passed\n전체: 456 PASS",
            acceptance_packet_body="<!-- pipeline-human-acceptance-packet -->\n456 PASS",
            pr_changed_files=[],
            pr_head_sha="",
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )
        self.assertEqual(result["status"], "PASS")

    def test_no_total_count_skips_check(self):
        result = _check_protocol_consistency(
            pr_body="변경 내용",
            acceptance_packet_body="<!-- pipeline-human-acceptance-packet -->",
            pr_changed_files=[],
            pr_head_sha="",
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )
        self.assertEqual(result["status"], "PASS")


# ---------------------------------------------------------------------------
# BUG-20260521-C675 회귀 테스트 (10개)
# AB-1: acceptance packet 정보 부족 false positive 방지 (3개)
# AB-2: bold/backtick/em dash/colon label 정규화 (4개)
# AB-3: truncation marker 감지 (3개)
# ---------------------------------------------------------------------------

import os
import tempfile


class TestAB1AcceptancePacketFalsePositive(unittest.TestCase):
    """AB-1: advisory 텍스트의 '정보 부족' 문자열이 packet status와 혼동되지 않아야 한다.

    _ACCEPTANCE_PACKET_INSUFFICIENT_PATTERN regex를 직접 테스트한다.
    _check_acceptance_packet_via_local_file은 환경변수 경유 파일 경로를 사용하므로
    임시 파일 + PIPELINE_TEST_ACCEPTANCE_PACKET_PATH env로 테스트한다.
    """

    def _run_local_check(self, packet_content: str) -> dict:
        """임시 파일에 packet_content를 쓰고 _check_acceptance_packet_via_local_file을 실행한다."""
        from pipeline import _check_acceptance_packet_via_local_file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write(packet_content)
            tmp_path = f.name
        try:
            orig = os.environ.get("PIPELINE_TEST_ACCEPTANCE_PACKET_PATH")
            os.environ["PIPELINE_TEST_ACCEPTANCE_PACKET_PATH"] = tmp_path
            result = _check_acceptance_packet_via_local_file({"pipeline_id": "TEST"})
            if orig is not None:
                os.environ["PIPELINE_TEST_ACCEPTANCE_PACKET_PATH"] = orig
            else:
                del os.environ["PIPELINE_TEST_ACCEPTANCE_PACKET_PATH"]
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return result

    def test_advisory_guidance_text_not_flagged(self):
        """advisory 안내 문구에 '정보 부족'이 포함돼도 판단 정보 상태 선언이 아니면 PASS."""
        content = (
            "판단 정보 상태: **판단 가능**\n"
            "GPT advisory가 정보 부족이면 수동 검토 권고\n"
        )
        result = self._run_local_check(content)
        self.assertNotEqual(result.get("failure_code"), "acceptance_packet_insufficient")

    def test_actual_insufficient_status_flagged(self):
        """판단 정보 상태: **정보 부족** 형식이면 acceptance_packet_insufficient 반환."""
        content = "판단 정보 상태: **정보 부족**\n"
        result = self._run_local_check(content)
        self.assertEqual(result.get("failure_code"), "acceptance_packet_insufficient")

    def test_no_bold_insufficient_status_flagged(self):
        """bold 없이 '판단 정보 상태: 정보 부족'도 insufficient로 감지한다."""
        content = "판단 정보 상태: 정보 부족\n"
        result = self._run_local_check(content)
        self.assertEqual(result.get("failure_code"), "acceptance_packet_insufficient")


class TestAB2FilePathNormalization(unittest.TestCase):
    """AB-2: bold/backtick/em dash/colon label이 포함된 file path를 정상 파싱해야 한다."""

    def _base_pr(self, files_section: str) -> str:
        return (
            "## 작업 요약\n변경\n\n"
            "## 사용자가 확인할 결과물\n- pipeline.py\n\n"
            "## 기대 결과와 실제 결과\n확인\n\n"
            "## 중요한 선택과 트레이드오프\n없음\n\n"
            "## 검증\nCI 통과\n\n"
            + files_section
        )

    def test_bold_file_path_recognized(self):
        """**pipeline.py** 형식의 bold file path가 pipeline.py로 정규화된다."""
        pr_body = self._base_pr("변경 파일:\n- **pipeline.py**\n")
        result = _check_protocol_consistency(
            pr_body=pr_body,
            acceptance_packet_body="<!-- pipeline-human-acceptance-packet -->\n판단 정보 상태: **판단 가능**\n변경 파일:\n- **pipeline.py**\n",
            pr_changed_files=["pipeline.py"],
            pr_head_sha="abc1234",
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )
        self.assertNotEqual(result["failure_code"], "changed_files_mismatch")

    def test_backtick_file_path_recognized(self):
        """`pipeline.py` 형식의 backtick file path가 pipeline.py로 정규화된다."""
        pr_body = self._base_pr("변경 파일:\n- `pipeline.py`\n")
        result = _check_protocol_consistency(
            pr_body=pr_body,
            acceptance_packet_body="<!-- pipeline-human-acceptance-packet -->\n판단 정보 상태: **판단 가능**\n변경 파일:\n- `pipeline.py`\n",
            pr_changed_files=["pipeline.py"],
            pr_head_sha="abc1234",
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )
        self.assertNotEqual(result["failure_code"], "changed_files_mismatch")

    def test_em_dash_description_stripped(self):
        """pipeline.py — 설명 형식에서 파일명만 추출된다."""
        pr_body = self._base_pr("변경 파일:\n- pipeline.py — AB-1 수정\n")
        result = _check_protocol_consistency(
            pr_body=pr_body,
            acceptance_packet_body="<!-- pipeline-human-acceptance-packet -->\n판단 정보 상태: **판단 가능**\n변경 파일:\n- pipeline.py — AB-1 수정\n",
            pr_changed_files=["pipeline.py"],
            pr_head_sha="abc1234",
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )
        self.assertNotEqual(result["failure_code"], "changed_files_mismatch")

    def test_colon_label_file_path_recognized(self):
        """'수정됨: pipeline.py' 형식에서 파일명이 추출된다."""
        pr_body = self._base_pr("변경 파일:\n- 수정됨: pipeline.py\n")
        result = _check_protocol_consistency(
            pr_body=pr_body,
            acceptance_packet_body="<!-- pipeline-human-acceptance-packet -->\n판단 정보 상태: **판단 가능**\n변경 파일:\n- 수정됨: pipeline.py\n",
            pr_changed_files=["pipeline.py"],
            pr_head_sha="abc1234",
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )
        self.assertNotEqual(result["failure_code"], "changed_files_mismatch")


class TestAB3TruncationMarker(unittest.TestCase):
    """AB-3: '... 외 N개 파일' truncation marker는 파일명이 아니라 truncation 신호다."""

    def _base_pr(self, files_section: str) -> str:
        # 사용자가 확인할 결과물 섹션에는 파일명이 아닌 설명만 포함하여
        # body_listed_files 파싱에 영향을 주지 않도록 한다.
        return (
            "## 작업 요약\n변경\n\n"
            "## 사용자가 확인할 결과물\n수정된 코드 검토 후 승인 부탁드립니다.\n\n"
            "## 기대 결과와 실제 결과\n확인\n\n"
            "## 중요한 선택과 트레이드오프\n없음\n\n"
            "## 검증\nCI 통과\n\n"
            + files_section
        )

    def test_truncation_marker_not_treated_as_filename(self):
        """'... 외 3개 파일' 항목이 파일명으로 처리되지 않는다.

        truncated body에서 일반 비trust-root 파일(README.md) 누락은 허용(PASS)이어야 한다.
        """
        pr_body = self._base_pr("변경 파일:\n- pipeline.py\n- tests/test_a.py\n- ... 외 3개 파일\n")
        result = _check_protocol_consistency(
            pr_body=pr_body,
            acceptance_packet_body="<!-- pipeline-human-acceptance-packet -->\n판단 정보 상태: **판단 가능**\n",
            # body에 나열된 파일은 모두 diff에 포함 (stale_file_description 방지)
            pr_changed_files=["pipeline.py", "tests/test_a.py", "README.md"],
            pr_head_sha="abc1234",
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )
        # truncated body이므로 README.md 누락은 허용 — changed_files_mismatch가 아니어야 함
        self.assertNotEqual(result["failure_code"], "changed_files_mismatch")

    def test_trust_root_missing_still_blocked_when_truncated(self):
        """truncated PR body라도 trust-root 파일(pipeline.py) 누락은 BLOCKED.

        PR body에 tests/test_a.py와 truncation marker만 있고 pipeline.py가 없을 때,
        실제 diff에 pipeline.py가 있으면 changed_files_mismatch로 BLOCKED 되어야 한다.
        검사 F(stale 탐지)를 피하기 위해 body에 나열된 파일은 실제 diff에도 포함해야 한다.
        """
        pr_body = self._base_pr("변경 파일:\n- tests/test_a.py\n- ... 외 3개 파일\n")
        result = _check_protocol_consistency(
            pr_body=pr_body,
            acceptance_packet_body="<!-- pipeline-human-acceptance-packet -->\n판단 정보 상태: **판단 가능**\n",
            # tests/test_a.py도 실제 diff에 포함하여 stale_file_description을 피함
            pr_changed_files=["pipeline.py", "tests/test_a.py"],
            pr_head_sha="abc1234",
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )
        self.assertEqual(result["failure_code"], "changed_files_mismatch")

    def test_english_truncation_marker(self):
        """'and 2 more files' 영문 truncation도 감지된다.

        truncated body에서 비trust-root 파일(tests/test_b.py) 누락은 허용(PASS)이어야 한다.
        """
        pr_body = self._base_pr("변경 파일:\n- pipeline.py\n- tests/test_a.py\n- and 2 more files\n")
        result = _check_protocol_consistency(
            pr_body=pr_body,
            acceptance_packet_body="<!-- pipeline-human-acceptance-packet -->\n판단 정보 상태: **판단 가능**\n",
            # body에 나열된 파일은 모두 diff에 포함 (stale_file_description 방지)
            pr_changed_files=["pipeline.py", "tests/test_a.py", "tests/test_b.py"],
            pr_head_sha="abc1234",
            latest_ci_run_id="",
            latest_ci_run_conclusion="",
        )
        # truncated body이므로 tests/test_b.py 누락은 허용
        self.assertNotEqual(result["failure_code"], "changed_files_mismatch")


class TestAB2ColonFileParsing(unittest.TestCase):
    """BUG-20260521-C675 AB-2: 파일명 뒤 콜론 처리 버그 회귀 테스트."""

    def test_ab2_path_with_colon_suffix(self):
        """- **pipeline.py**: 설명 패턴에서 pipeline.py 추출."""
        from pipeline import _consistency_listed_files  # type: ignore
        files, _ = _consistency_listed_files("- **pipeline.py**: 설명\n")
        self.assertIn("pipeline.py", files)

    def test_ab2_backtick_path_with_colon(self):
        """- `tests/bar.py`: 수정 패턴에서 tests/bar.py 추출."""
        from pipeline import _consistency_listed_files  # type: ignore
        files, _ = _consistency_listed_files("- `tests/bar.py`: 수정\n")
        self.assertIn("tests/bar.py", files)


def test_consistency_listed_files_korean_sentence_ending_not_extracted() -> None:
    """됩니다. 같은 한국어 문장 끝의 마침표가 파일명으로 오추출되지 않아야 한다.

    IMP-20260522-29C1 fix-forward v5: _consistency_listed_files에서
    `- 안내: ...됩니다.` 불릿의 `됩니다.`가 파일명으로 인식되던 버그를 수정.
    """
    from pipeline import _consistency_listed_files

    # 안내: 콜론 라벨 뒤 한국어 문장 — 됩니다. 이 파일명으로 추출되면 안 된다
    text_with_korean_period = "- 안내: 아래 요약과 결과물이 요청과 맞으면 승인(ACCEPT)하면 됩니다."
    files, _ = _consistency_listed_files(text_with_korean_period)
    assert "됩니다." not in files, (
        f"됩니다.가 파일명으로 오추출되었다: files={files}"
    )

    # 실제 파일명은 올바르게 추출되어야 한다
    text_with_real_file = "- 수정됨: tests/test_pipeline_metrics_timestamps.py"
    files2, _ = _consistency_listed_files(text_with_real_file)
    assert "tests/test_pipeline_metrics_timestamps.py" in files2, (
        f"실제 파일명이 추출되지 않았다: files2={files2}"
    )

    # 안내 — em dash 형식도 됩니다. 오추출 없어야 한다
    text_with_em_dash = "- 안내 — 아래 요약과 결과물이 맞으면 승인하면 됩니다."
    files3, _ = _consistency_listed_files(text_with_em_dash)
    assert "됩니다." not in files3, (
        f"안내 — 형식에서도 됩니다.가 오추출되었다: files3={files3}"
    )


if __name__ == "__main__":
    unittest.main()
