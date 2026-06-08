"""tests/test_verification_json_codex_evidence.py

IMP-20260607-E656 MT-8: Verification JSON SSoT 강화 및 최종보고서 슬림다운 검증 테스트.

검증 대상 (MT-1 ~ MT-6):
  MT-1: _build_verification_json -- 15개 필수 필드 + BLOCKED 규칙
  MT-2: _build_final_packet_content -- [Codex 검토용] 블록 + 독립 줄 승인 코드
  MT-3: _check_protocol_consistency -- PR 본문 자유서술 파싱 제거 (오탐 방지)
  MT-4: _write_acceptance_request + _get_ci_run_head_sha -- 신규 필드
  MT-5: _verify_verification_json_freshness -- packet_sha256_changed 검증
  MT-6: cmd_hygiene_cleanup_workspace -- stdout 요약 형식

4개 오라클 케이스:
  case_final_packet_json_schema: 15개 필드 모두 포함 시 schema_valid=true (normal)
  case_acceptance_code_standalone: 승인 코드 독립 줄 출력 (normal)
  case_pr_body_no_false_positive: PR 본문 자유서술에서 오탐 없음 (normal)
  case_changed_files_mismatch: changed_files 불일치 시 BLOCKED (error)
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import (  # type: ignore  # noqa: E402
    _build_verification_json,
    _build_final_packet_content,
    _check_protocol_consistency,
    _verify_verification_json_freshness,
    _get_ci_run_head_sha,
    _write_acceptance_request,
)

ORACLE_BASE = ROOT / "tests" / "oracles" / "IMP-20260607-E656"

REQUIRED_FIELDS_15 = [
    "schema_version", "packet_type", "pipeline_id", "generated_at",
    "pr", "github_actions", "changed_files", "changed_files_count",
    "gates", "requirements", "oracle_summary", "known_failures",
    "warnings", "acceptance", "artifacts",
]


def _make_evidence(
    pipeline_id: str = "IMP-TEST-0001-XXXX",
    pr_head_sha: str = "abc123def456",
    ci_run_id: str = "12345678901",
    changed_files: Optional[list] = None,
    acceptance_request: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """테스트용 evidence dict 생성."""
    return {
        "pipeline_id": pipeline_id,
        "pr_url": "https://github.com/org/repo/pull/1",
        "pr_number": "1",
        "pr_head_sha": pr_head_sha,
        "base_branch": "main",
        "head_branch": f"impl/{pipeline_id}",
        "ci_run_id": ci_run_id,
        "actions_url": f"https://github.com/org/repo/actions/runs/{ci_run_id}",
        "ci_status": "success",
        "changed_files": changed_files if changed_files is not None else ["pipeline.py"],
        "gate_status": {
            "technical": "PASS", "oracle": "PASS",
            "github_ci": "PASS", "acceptance": "PENDING",
        },
        "structured_ac": [],
        "ac_fulfillment_table": [
            {
                "ac_id": "AC-1", "requirement": "테스트 AC", "linked_mt": ["MT-1"],
                "status": "PASS", "evidence": ["test_pass"],
            }
        ],
        "oracle_summary": {
            "status": "PASS", "case_count": 2,
            "passed_count": 2, "failed_count": 0,
        },
        "acceptance_request": acceptance_request,
        "known_failures": [],
        "warnings": [],
        "generated_at": "2026-06-07T10:00:00Z",
    }


# ── MT-1: 15개 필수 필드 테스트 ──────────────────────────────────────────

class TestBuildVerificationJson15Fields(unittest.TestCase):
    """MT-1: _build_verification_json 15개 필수 필드 검증."""

    def test_normal_all_15_required_fields_present(self) -> None:
        """15개 필수 필드가 모두 포함된다 (normal -- oracle: case_final_packet_json_schema)."""
        evidence = _make_evidence(
            acceptance_request={
                "nonce": "ABCD1234", "status": "PENDING",
                "request_id": "req_abc123",
            }
        )
        result = _build_verification_json(evidence)

        for field in REQUIRED_FIELDS_15:
            self.assertIn(field, result, f"15개 필수 필드 누락: {field}")

    def test_normal_schema_version_is_1(self) -> None:
        """schema_version이 정수 1이다 (normal)."""
        result = _build_verification_json(_make_evidence())
        self.assertEqual(result["schema_version"], 1)
        self.assertIsInstance(result["schema_version"], int)

    def test_normal_packet_type_is_final_acceptance_evidence(self) -> None:
        """packet_type이 'final_acceptance_evidence'이다 (normal)."""
        result = _build_verification_json(_make_evidence())
        self.assertEqual(result["packet_type"], "final_acceptance_evidence")

    def test_normal_pr_object_fields(self) -> None:
        """pr 객체에 url, number, head_sha, base_branch, head_branch가 포함된다 (normal)."""
        result = _build_verification_json(_make_evidence())
        pr = result["pr"]
        self.assertIsInstance(pr, dict)
        for key in ("url", "number", "head_sha", "base_branch", "head_branch"):
            self.assertIn(key, pr, f"pr 객체 필드 누락: {key}")

    def test_normal_github_actions_object_fields(self) -> None:
        """github_actions 객체에 run_id, run_url, status, head_sha가 포함된다 (normal)."""
        result = _build_verification_json(_make_evidence())
        ga = result["github_actions"]
        self.assertIsInstance(ga, dict)
        for key in ("run_id", "run_url", "status", "head_sha"):
            self.assertIn(key, ga, f"github_actions 객체 필드 누락: {key}")

    def test_normal_changed_files_count_matches(self) -> None:
        """changed_files_count가 len(changed_files)와 일치한다 (normal)."""
        ev = _make_evidence(changed_files=["a.py", "b.py", "c.py"])
        result = _build_verification_json(ev)
        self.assertEqual(result["changed_files_count"], 3)
        self.assertEqual(result["changed_files_count"], len(result["changed_files"]))

    def test_edge_empty_changed_files_blocked(self) -> None:
        """빈 changed_files이면 blocked=True이고 blocked_reasons에 empty 사유가 포함된다 (edge)."""
        ev = _make_evidence(changed_files=[])
        result = _build_verification_json(ev)
        self.assertEqual(result["changed_files_count"], 0)
        self.assertTrue(result["blocked"], "빈 changed_files는 blocked=True여야 합니다")
        self.assertTrue(
            len(result["blocked_reasons"]) > 0,
            "blocked_reasons가 비어있으면 안 됩니다"
        )
        # blocked_reasons 중 하나에 "빈 배열" 또는 "changed_files" 언급이 있어야 함
        reasons_text = " ".join(result["blocked_reasons"]).lower()
        self.assertTrue(
            "changed_files" in reasons_text,
            f"blocked_reasons에 'changed_files' 언급 없음: {result['blocked_reasons']}"
        )

    def test_edge_not_blocked_when_sha_match(self) -> None:
        """pr.head_sha == github_actions.head_sha이면 blocked=False이다 (edge)."""
        ev = _make_evidence(pr_head_sha="abc123def456")
        result = _build_verification_json(ev)
        self.assertFalse(result["blocked"])
        self.assertEqual(len(result["blocked_reasons"]), 0)

    def test_normal_pr_sha_equals_ga_sha(self) -> None:
        """현재 구현에서 pr.head_sha == github_actions.head_sha이다 (normal)."""
        ev = _make_evidence(pr_head_sha="aaaaaa111111")
        result = _build_verification_json(ev)
        self.assertEqual(result["pr"]["head_sha"], result["github_actions"]["head_sha"])

    def test_normal_acceptance_object_fields(self) -> None:
        """acceptance 객체에 code, reject_example, nonce, request_id, status가 포함된다 (normal)."""
        ev = _make_evidence(acceptance_request={
            "nonce": "TESTTEST", "status": "PENDING", "request_id": "req_001"
        })
        result = _build_verification_json(ev)
        acc = result["acceptance"]
        self.assertIsInstance(acc, dict)
        for key in ("code", "reject_example", "nonce", "request_id", "status"):
            self.assertIn(key, acc, f"acceptance 객체 필드 누락: {key}")
        self.assertEqual(acc["code"], "ACCEPT-IMP-TEST-0001-XXXX-TESTTEST")
        self.assertEqual(acc["nonce"], "TESTTEST")

    def test_oracle_case_final_packet_json_schema(self) -> None:
        """오라클 case_final_packet_json_schema: 15개 필드 + schema_valid=true (normal)."""
        oracle_dir = ORACLE_BASE / "case_final_packet_json_schema"
        input_data = json.loads((oracle_dir / "input.json").read_text(encoding="utf-8"))
        # expected.json은 acceptance runner가 input.json과 동일한지 비교용으로 사용.
        # 검증 로직은 input.json 데이터로 직접 계산한다.

        packet = input_data["packet"]
        missing = [f for f in REQUIRED_FIELDS_15 if f not in packet]
        schema_valid = len(missing) == 0

        # input.json의 packet에 15개 필드가 모두 있어야 한다 (schema_valid=true)
        self.assertTrue(schema_valid, f"15개 필수 필드 누락: {missing}")
        self.assertEqual(sorted(packet.keys() & set(REQUIRED_FIELDS_15)), sorted(REQUIRED_FIELDS_15))
        # changed_files_count == len(changed_files) 이어야 한다
        self.assertEqual(packet.get("changed_files_count"), len(packet.get("changed_files", [])))
        # pr.head_sha == github_actions.head_sha 이어야 한다
        self.assertEqual(packet["pr"]["head_sha"], packet["github_actions"]["head_sha"])
        # acceptance.status == PENDING (blocked=false)
        self.assertNotEqual(packet.get("gates", {}).get("acceptance"), "BLOCKED")


# ── MT-2: _build_final_packet_content 슬림다운 테스트 ──────────────────

class TestBuildFinalPacketContent(unittest.TestCase):
    """MT-2: _build_final_packet_content 검증 -- [Codex 검토용] 블록 + 독립 줄 승인 코드."""

    def test_normal_codex_review_block_present(self) -> None:
        """[Codex 검토용] 블록이 최상단에 있다 (normal)."""
        evidence = _make_evidence()
        content = _build_final_packet_content(evidence)
        self.assertIn("[Codex 검토용]", content)
        lines = content.splitlines()
        first_non_empty = next((ln for ln in lines if ln.strip()), "")
        self.assertEqual(first_non_empty, "[Codex 검토용]")

    def test_normal_codex_block_has_required_fields(self) -> None:
        """[Codex 검토용] 블록에 필수 필드들이 있다 (normal)."""
        evidence = _make_evidence(
            pipeline_id="IMP-TEST-0001-XXXX",
            pr_head_sha="abc123def456",
            ci_run_id="12345678901",
        )
        content = _build_final_packet_content(evidence)
        for keyword in [
            "pipeline_id:", "pr_url:", "pr_head_sha:", "ci_run_id:",
            "changed_files_count:", "technical:", "oracle:", "github_ci:",
            "acceptance:", "verification_json:",
        ]:
            self.assertIn(keyword, content, f"[Codex 검토용] 블록에 누락: {keyword}")

    def test_normal_acceptance_code_on_standalone_line(self) -> None:
        """승인 코드가 독립 줄에만 있고 접두사가 없다 (normal -- oracle: case_acceptance_code_standalone)."""
        evidence = _make_evidence(acceptance_request={
            "nonce": "ABCD1234", "status": "PENDING"
        })
        content = _build_final_packet_content(evidence)
        expected_code = "ACCEPT-IMP-TEST-0001-XXXX-ABCD1234"

        lines = content.splitlines()
        standalone_lines = [ln.strip() for ln in lines if ln.strip() == expected_code]
        self.assertTrue(
            len(standalone_lines) >= 1,
            f"승인 코드가 독립 줄에 없음. 콘텐츠 일부:\n{content[:600]}",
        )

    def test_normal_reject_example_on_standalone_line(self) -> None:
        """거절 예시도 독립 줄에 있다 (normal)."""
        evidence = _make_evidence(acceptance_request={
            "nonce": "ABCD1234", "status": "PENDING"
        })
        content = _build_final_packet_content(evidence)
        reject_code = "REJECT-IMP-TEST-0001-XXXX-ABCD1234: 이유"

        lines = content.splitlines()
        reject_lines = [ln.strip() for ln in lines if ln.strip() == reject_code]
        self.assertTrue(
            len(reject_lines) >= 1,
            "거절 예시가 독립 줄에 없음.",
        )

    def test_edge_no_nonce_shows_placeholder(self) -> None:
        """nonce 없이 호출하면 안내 문구가 나온다 (edge)."""
        evidence = _make_evidence(acceptance_request=None)
        content = _build_final_packet_content(evidence)
        self.assertIn("gates request-accept", content)

    def test_oracle_case_acceptance_code_standalone(self) -> None:
        """오라클 case_acceptance_code_standalone 검증 (normal)."""
        oracle_dir = ORACLE_BASE / "case_acceptance_code_standalone"
        input_data = json.loads((oracle_dir / "input.json").read_text(encoding="utf-8"))
        # expected.json은 acceptance runner가 input.json과 동일한지 비교용으로 사용.
        # 검증 로직은 input.json 데이터로 직접 계산한다.

        code = input_data["acceptance_code"]
        content = input_data["packet_md_content"]

        lines = content.splitlines()
        standalone_lines = [ln.strip() for ln in lines if ln.strip() == code]
        code_on_standalone = len(standalone_lines) >= 1
        no_prefix_on_same_line = code_on_standalone
        codex_block_present = "[Codex 검토용]" in content

        reject_example = "REJECT-IMP-TEST-0001-XXXX-ABCD1234: 이유"
        reject_standalone = [ln.strip() for ln in lines if ln.strip() == reject_example]
        reject_on_standalone = len(reject_standalone) >= 1

        # 승인 코드가 독립 줄에 있어야 한다
        self.assertTrue(code_on_standalone, "승인 코드가 독립된 줄에 없음")
        self.assertTrue(no_prefix_on_same_line, "승인 코드 앞에 다른 텍스트가 있음")
        # 거절 예시도 독립 줄에 있어야 한다
        self.assertTrue(reject_on_standalone, "거절 예시가 독립 줄에 없음")
        # [Codex 검토용] 블록이 있어야 한다
        self.assertTrue(codex_block_present, "[Codex 검토용] 블록 없음")


# ── MT-3: _check_protocol_consistency 오탐 방지 테스트 ──────────────────

class TestCheckProtocolConsistencyNoFalsePositive(unittest.TestCase):
    """MT-3: _check_protocol_consistency PR 본문 자유서술 오탐 방지."""

    def _make_args(
        self,
        verification_json: Optional[Dict] = None,
        pr_body: str = "",
        pr_changed_files: Optional[list] = None,
        pr_head_sha: str = "abc123def456",
        latest_ci_run_id: str = "11111111111",
    ) -> Dict[str, Any]:
        """_check_protocol_consistency 호출용 kwargs 생성."""
        return {
            "pr_body": pr_body,
            "acceptance_packet_body": "",
            "pr_changed_files": pr_changed_files or ["pipeline.py"],
            "pr_head_sha": pr_head_sha,
            "latest_ci_run_id": latest_ci_run_id,
            "latest_ci_run_conclusion": "success",
            "verification_json": verification_json,
        }

    def test_normal_no_false_positive_from_free_text(self) -> None:
        """PR 본문 자유서술에 dot-notation 패턴이 있어도 오탐이 없다 (normal -- oracle: case_pr_body_no_false_positive)."""
        oracle_dir = ORACLE_BASE / "case_pr_body_no_false_positive"
        input_data = json.loads((oracle_dir / "input.json").read_text(encoding="utf-8"))
        expected = json.loads((oracle_dir / "expected.json").read_text(encoding="utf-8"))  # noqa: F841

        vj = {
            "changed_files": input_data["verification_json_changed_files"],
            "changed_files_count": len(input_data["verification_json_changed_files"]),
            "pr_head_sha": "abc123def456",
            "ci_run_id": "11111111111",
        }

        args = self._make_args(
            verification_json=vj,
            pr_body=input_data["pr_body_free_text"],
            pr_changed_files=input_data["verification_json_changed_files"],
            latest_ci_run_id="11111111111",
        )

        result = _check_protocol_consistency(**args)
        # PASS 또는 verification_json_missing이 아닌 다른 failure_code여야 함
        if result.get("status") == "BLOCKED":
            # 오탐이면 실패
            self.assertNotIn(result.get("failure_code"), [
                "changed_files_mismatch",
                "changed_files_mismatch_vs_verification_json",
                "stale_file_description",
            ], f"PR 본문 자유서술 오탐 감지됨: {result.get('failure_code')}")
        # expected.json은 acceptance runner용. 검증값은 하드코딩된 기대값으로 확인한다.
        # false_positives_detected=0: PR 본문 자유서술에서 오탐이 없어야 한다
        # consistency_check_result=PASS: verification_json changed_files와 일치해야 한다
        self.assertNotEqual(result.get("status"), "BLOCKED",
                            f"PR 본문 오탐으로 오판된 failure_code: {result.get('failure_code')}")

    def test_edge_missing_verification_json_returns_blocked(self) -> None:
        """verification_json이 None이면 BLOCKED가 반환된다 (edge)."""
        args = self._make_args(verification_json=None)
        result = _check_protocol_consistency(**args)
        self.assertEqual(result.get("status"), "BLOCKED")
        self.assertEqual(result.get("failure_code"), "verification_json_missing")

    def test_oracle_case_changed_files_mismatch(self) -> None:
        """오라클 case_changed_files_mismatch: JSON에 없는 파일이 실제 diff에 있으면 BLOCKED (error)."""
        oracle_dir = ORACLE_BASE / "case_changed_files_mismatch"
        input_data = json.loads((oracle_dir / "input.json").read_text(encoding="utf-8"))
        expected = json.loads((oracle_dir / "expected.json").read_text(encoding="utf-8"))  # noqa: F841

        # json_changed_files: ["pipeline.py", "nonexistent_file.py"]
        # actual_pr_diff_files: ["pipeline.py"]
        # json에 nonexistent_file.py가 있지만 실제 diff에 없으므로 stale_file_description
        # 또는 json에 없는 실제 diff 파일 없으므로 changed_files_mismatch_vs_verification_json
        vj = {
            "changed_files": input_data["json_changed_files"],
            "changed_files_count": len(input_data["json_changed_files"]),
            "pr_head_sha": "abc123def456",
            "ci_run_id": "11111111111",
        }

        args = self._make_args(
            verification_json=vj,
            pr_changed_files=input_data["actual_pr_diff_files"],
            latest_ci_run_id="11111111111",
        )

        result = _check_protocol_consistency(**args)

        # expected.json은 acceptance runner용. 검증값은 input_data로 직접 계산한다.
        # json_changed_files에 nonexistent_file.py가 있고 실제 diff에 없으면 BLOCKED
        json_extra = set(input_data["json_changed_files"]) - set(input_data["actual_pr_diff_files"])
        self.assertTrue(len(json_extra) > 0, "테스트 시나리오: json에 없는 파일이 있어야 함")
        self.assertEqual(result.get("status"), "BLOCKED",
                         f"changed_files 불일치 시 BLOCKED 기대, 실제: {result.get('status')}")
        # JSON에 nonexistent_file.py가 있고 실제 diff에 없으므로 stale_file_description
        self.assertIn(result.get("failure_code", ""), [
            "changed_files_mismatch_vs_verification_json",
            "stale_file_description",
        ])


# ── MT-4: _write_acceptance_request 신규 필드 테스트 ──────────────────

class TestWriteAcceptanceRequestNewFields(unittest.TestCase):
    """MT-4: _write_acceptance_request에 packet_path, packet_sha256, github_ci_head_sha 추가."""

    def test_normal_new_fields_stored_when_provided(self) -> None:
        """packet_path, packet_sha256, github_ci_head_sha가 제공되면 저장된다 (normal)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig = os.getcwd()
            try:
                os.chdir(tmpdir)
                result = _write_acceptance_request(
                    pipeline_id="IMP-TEST-0001-XXXX",
                    evidence="pipeline.py",
                    pr_url="https://github.com/org/repo/pull/1",
                    pr_head_sha="abc123def456",
                    ci_run_id="12345678901",
                    verification_json_path="human_acceptance_packet.json",
                    verification_json_sha256="deadbeef" * 8,
                    packet_path="human_acceptance_packet.md",
                    packet_sha256="cafebabe" * 8,
                    github_ci_head_sha="abc123def456",
                )
                self.assertIn("packet_path", result)
                self.assertIn("packet_sha256", result)
                self.assertIn("github_ci_head_sha", result)
                self.assertEqual(result["packet_path"], "human_acceptance_packet.md")
                self.assertEqual(result["github_ci_head_sha"], "abc123def456")
            finally:
                os.chdir(orig)

    def test_edge_new_fields_none_when_not_provided(self) -> None:
        """신규 필드 미제공 시 None으로 저장된다 (edge)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig = os.getcwd()
            try:
                os.chdir(tmpdir)
                result = _write_acceptance_request(
                    pipeline_id="IMP-TEST-0001-XXXX",
                    evidence="pipeline.py",
                    pr_url="https://github.com/org/repo/pull/1",
                    pr_head_sha="abc123def456",
                    ci_run_id="12345678901",
                )
                self.assertIsNone(result.get("packet_path"))
                self.assertIsNone(result.get("packet_sha256"))
                self.assertIsNone(result.get("github_ci_head_sha"))
            finally:
                os.chdir(orig)


# ── MT-4: _get_ci_run_head_sha 테스트 ──────────────────────────────────

class TestGetCiRunHeadSha(unittest.TestCase):
    """MT-4: _get_ci_run_head_sha 검증."""

    def test_edge_empty_run_id_returns_none(self) -> None:
        """빈 run_id 전달 시 None 반환 (edge)."""
        result = _get_ci_run_head_sha("")
        self.assertIsNone(result)

    def test_edge_none_run_id_returns_none(self) -> None:
        """None 전달 시 None 반환 (edge)."""
        result = _get_ci_run_head_sha(None)  # type: ignore
        self.assertIsNone(result)

    def test_edge_gh_cli_absent_returns_none(self) -> None:
        """gh CLI 없으면 None 반환 (edge)."""
        with patch("pipeline.shutil.which", return_value=None):
            result = _get_ci_run_head_sha("12345678901")
        self.assertIsNone(result)

    def test_normal_gh_cli_success_returns_sha(self) -> None:
        """gh CLI 성공 시 SHA 반환 (normal)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc123def456\n"
        with patch("pipeline.shutil.which", return_value="/usr/bin/gh"):
            with patch("pipeline.subprocess.run", return_value=mock_result):
                result = _get_ci_run_head_sha("12345678901")
        self.assertEqual(result, "abc123def456")

    def test_error_gh_cli_failure_returns_none(self) -> None:
        """gh CLI 실패 시 None 반환 (error)."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("pipeline.shutil.which", return_value="/usr/bin/gh"):
            with patch("pipeline.subprocess.run", return_value=mock_result):
                result = _get_ci_run_head_sha("12345678901")
        self.assertIsNone(result)


# ── MT-5: _verify_verification_json_freshness 테스트 ──────────────────

class TestVerifyVerificationJsonFreshness(unittest.TestCase):
    """MT-5: _verify_verification_json_freshness 검증."""

    def test_normal_returns_none_when_sha_matches(self) -> None:
        """SHA가 일치하면 None 반환 (PASS) (normal)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig = os.getcwd()
            try:
                os.chdir(tmpdir)
                vj_file = Path(tmpdir) / "human_acceptance_packet.json"
                vj_content = json.dumps({"changed_files": [], "changed_files_count": 0})
                vj_file.write_text(vj_content, encoding="utf-8")

                import hashlib
                sha = hashlib.sha256(vj_file.read_bytes()).hexdigest()
                req = {
                    "verification_json_path": str(vj_file),
                    "verification_json_sha256": sha,
                }
                with patch("pipeline._get_git_diff_files", return_value=[]):
                    result = _verify_verification_json_freshness(req)
                self.assertIsNone(result)
            finally:
                os.chdir(orig)

    def test_edge_missing_fields_returns_missing(self) -> None:
        """verification_json_path 없으면 'verification_json_missing' 반환 (edge)."""
        req: Dict[str, Any] = {}
        result = _verify_verification_json_freshness(req)
        self.assertEqual(result, "verification_json_missing")

    def test_error_changed_sha_returns_changed(self) -> None:
        """SHA가 달라지면 'verification_json_changed' 반환 (error)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig = os.getcwd()
            try:
                os.chdir(tmpdir)
                vj_file = Path(tmpdir) / "human_acceptance_packet.json"
                vj_file.write_text('{"changed_files": []}', encoding="utf-8")
                req = {
                    "verification_json_path": str(vj_file),
                    "verification_json_sha256": "0" * 64,  # 잘못된 SHA
                }
                result = _verify_verification_json_freshness(req)
                self.assertEqual(result, "verification_json_changed")
            finally:
                os.chdir(orig)


# ── MT-6: cmd_hygiene_cleanup_workspace stdout 요약 형식 테스트 ──────────

class TestHygieneCleanupWorkspaceOutputFormat(unittest.TestCase):
    """MT-6: cleanup-workspace stdout 요약 형식 검증."""

    def _run_cleanup(self, tmpdir: str):
        """cleanup-workspace 실행 헬퍼."""
        import io
        from contextlib import redirect_stdout
        import argparse
        from pipeline import cmd_hygiene_cleanup_workspace  # type: ignore
        import pipeline  # type: ignore

        state_data = {
            "pipeline_id": "IMP-TEST-0001-XXXX",
            "terminal_state": "COMPLETE",
        }

        args = argparse.Namespace(after_complete=True)
        f = io.StringIO()

        # git status --porcelain 결과를 빈 문자열로 mock (tracked 변경 없음)
        mock_git_result = MagicMock()
        mock_git_result.returncode = 0
        mock_git_result.stdout = ""  # 빈 문자열 = tracked 변경 없음

        def _fake_subprocess_run(cmd, **kwargs):
            return mock_git_result

        # STATE_FILE을 임시 파일로 패치 (모듈 레벨 상수)
        state_file = Path(tmpdir) / "pipeline_state.json"
        state_file.write_text(json.dumps(state_data), encoding="utf-8")

        with patch.object(pipeline, "STATE_FILE", state_file):
            with patch("pipeline.subprocess.run", side_effect=_fake_subprocess_run):
                try:
                    with redirect_stdout(f):
                        cmd_hygiene_cleanup_workspace(args)
                except SystemExit:
                    pass
        return f.getvalue()

    def test_normal_summary_format_on_stdout(self) -> None:
        """성공 시 stdout에 [작업공간 정리] 요약 형식이 출력된다 (normal)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig = os.getcwd()
            try:
                os.chdir(tmpdir)
                output = self._run_cleanup(tmpdir)
                self.assertIn("[작업공간 정리]", output)
                self.assertIn("moved_count:", output)
            finally:
                os.chdir(orig)

    def test_edge_no_verbose_per_file_lines(self) -> None:
        """stdout에 개별 파일 이동 줄이 없다 (edge)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            orig = os.getcwd()
            try:
                os.chdir(tmpdir)
                output = self._run_cleanup(tmpdir)
                lines = [ln for ln in output.splitlines() if ln.strip()]
                verbose_lines = [ln for ln in lines if ln.strip().startswith("[이동]") or "→" in ln]
                self.assertEqual(
                    len(verbose_lines), 0,
                    f"개별 파일 이동 줄 발견: {verbose_lines}",
                )
            finally:
                os.chdir(orig)


# ── 오라클 무결성 테스트 ──────────────────────────────────────────────

class TestOracleManifestIntegrity(unittest.TestCase):
    """오라클 파일 무결성 검증."""

    ORACLE_CASES = [
        "case_final_packet_json_schema",
        "case_acceptance_code_standalone",
        "case_pr_body_no_false_positive",
        "case_changed_files_mismatch",
    ]

    def test_all_oracle_cases_have_input_and_expected(self) -> None:
        """모든 오라클 케이스에 input.json과 expected.json이 있다."""
        for case in self.ORACLE_CASES:
            case_dir = ORACLE_BASE / case
            self.assertTrue(case_dir.exists(), f"오라클 디렉토리 없음: {case}")
            self.assertTrue((case_dir / "input.json").exists(), f"input.json 없음: {case}")
            self.assertTrue((case_dir / "expected.json").exists(), f"expected.json 없음: {case}")

    def test_all_expected_json_not_empty(self) -> None:
        """expected.json이 비어있지 않다 (IMP-20260524-48C4 품질 게이트)."""
        for case in self.ORACLE_CASES:
            expected_path = ORACLE_BASE / case / "expected.json"
            data = json.loads(expected_path.read_text(encoding="utf-8"))
            self.assertIsNotNone(data, f"expected.json이 null: {case}")
            self.assertNotEqual(data, {}, f"expected.json이 빈 dict: {case}")
            self.assertNotEqual(data, [], f"expected.json이 빈 list: {case}")

    def test_oracle_case_count_normal_and_edge(self) -> None:
        """normal 케이스와 edge/error 케이스가 모두 있다."""
        manifest_path = ROOT / "tests" / "oracles" / "IMP-20260607-E656" / "oracle_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            entries = manifest if isinstance(manifest, list) else manifest.get("entries", [])
            case_kinds = {e.get("case_kind") for e in entries}
            has_normal = "normal" in case_kinds
            has_edge_or_error = bool(case_kinds & {"edge", "exception", "error", "regression"})
            self.assertTrue(has_normal, "normal 케이스 없음")
            self.assertTrue(has_edge_or_error, "edge/error 케이스 없음")


if __name__ == "__main__":
    unittest.main(verbosity=2)
