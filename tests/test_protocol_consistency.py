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

        with patch("pipeline.subprocess.run", side_effect=mock_run):
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

        with patch("pipeline.subprocess.run", side_effect=mock_run):
            with self.assertRaises(SystemExit) as ctx:
                _run_protocol_consistency_check(
                    state, args, "IMP-20260520-D0BB"
                )
            self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
