"""tests/test_consistency_guard_a_b_json_basis.py

IMP-20260605-9EF5: _check_protocol_consistency 검사 A(run_id) / B(head SHA)
verification JSON SSoT 기준 변경 회귀 테스트.

- 검사 A: PR 본문 자유서술의 stale run_id 무시, PIPELINE_FINAL_PACKET 블록/vj 기준
- 검사 B: PR 본문 자유서술의 stale SHA 무시, PIPELINE_FINAL_PACKET 블록/vj 기준
- verification_json=None: 즉시 BLOCKED(verification_json_missing)
- 기존 test_protocol_consistency.py + test_consistency_guard_json_basis.py 회귀 없음

[Purpose]: IMP-20260605-9EF5 정책 변경의 회귀 보호 — PR 본문 자유서술의 stale 값이
  검증 대상이 아님을 명시적으로 검증한다.
[Assumptions]: pipeline._check_protocol_consistency가 verification_json 파라미터를
  지원하고, PIPELINE_FINAL_PACKET 마커 상수가 SSoT로 정의되어 있다.
[Vulnerability & Risks]: PIPELINE_FINAL_PACKET 마커 문자열이 변경되면 본 테스트의
  PACKET_BLOCK_START/END 상수도 함께 수정 필요. 마커는 pipeline.py SSoT 상수이므로
  변경 빈도가 낮다.
[Improvement]: 향후 더 많은 위치 분기(packet_block vs verification_json vs
  acceptance_packet vs pr_body 자유서술)를 분리 테스트하면 회귀 보호 강도가 더 높아짐.
"""
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import _check_protocol_consistency  # type: ignore  # noqa: E402


# ── 상수 ────────────────────────────────────────────────────────────────
LATEST_RUN_ID = "27024225022"
STALE_RUN_ID = "27015551111"
HEAD_SHA = "9ef5abc1234567890abcdef1234567890abcdef1"
STALE_SHA = "deadbeefcafe1234567890abcdefdeadbeefcafe"
# pipeline.py는 trust-root 파일이므로 PR 본문에 명시 설명이 필요하다.
# 본 테스트의 changed_files는 pipeline.py를 포함하지 않아 검사 E를 우회한다.
CHANGED_FILES: List[str] = ["docs/notes.md", "tests/example.py"]

PACKET_BLOCK_START = "<!-- PIPELINE_FINAL_PACKET_START -->"
PACKET_BLOCK_END = "<!-- PIPELINE_FINAL_PACKET_END -->"


def _make_vj(
    run_id: str = LATEST_RUN_ID,
    sha: str = HEAD_SHA,
    changed_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """테스트용 verification_json (human_acceptance_packet.json 본문 시뮬레이션)."""
    return {
        "schema_version": 1,
        "pipeline_id": "IMP-20260605-9EF5",
        "pr_head_sha": sha,
        "ci_run_id": run_id,
        "changed_files": (
            changed_files if changed_files is not None else CHANGED_FILES
        ),
    }


def _make_pr_body(
    free_text_run_id: Optional[str] = None,
    free_text_sha: Optional[str] = None,
    block_run_id: Optional[str] = None,
    block_sha: Optional[str] = None,
) -> str:
    """PR 본문 빌더 — 자유서술/블록 내용을 독립 제어한다.

    block_run_id / block_sha가 None이면 블록 안에 해당 값이 들어가지 않는다.
    free_text_run_id / free_text_sha가 None이면 자유서술에 해당 값이 들어가지 않는다.
    """
    lines = [
        "## 작업 요약",
        "IMP-20260605-9EF5 회귀 테스트 시나리오.",
    ]
    # 변경 파일을 PR 본문에 언급하여 검사 E(trust_root_change_undocumented)를 우회.
    lines.append("변경된 파일:")
    for f in CHANGED_FILES:
        lines.append(f"- {f}")
    if free_text_run_id:
        lines.append(
            f"이전 실행: https://github.com/x/y/actions/runs/{free_text_run_id}"
        )
    if free_text_sha:
        lines.append(f"이전 커밋 참고: {free_text_sha}")
    lines.append("")
    lines.append(PACKET_BLOCK_START)
    lines.append("## 최종 확인 안내")
    if block_run_id:
        lines.append(
            f"GitHub Actions: https://github.com/x/y/actions/runs/{block_run_id}"
        )
    if block_sha:
        lines.append(f"PR head SHA: {block_sha}")
    lines.append(PACKET_BLOCK_END)
    return "\n".join(lines)


def _base_args(
    pr_body: str,
    vj: Optional[Dict[str, Any]],
    changed_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """_check_protocol_consistency 기본 인자."""
    files = changed_files if changed_files is not None else CHANGED_FILES
    return {
        "pr_body": pr_body,
        "acceptance_packet_body": (
            "<!-- pipeline-human-acceptance-packet -->\n"
            f"head_sha: {HEAD_SHA}\nrun_id: {LATEST_RUN_ID}\n"
        ),
        "pr_changed_files": files,
        "pr_head_sha": HEAD_SHA,
        "latest_ci_run_id": LATEST_RUN_ID,
        "latest_ci_run_conclusion": "success",
        "verification_json": vj,
    }


# ── 검사 A 테스트 ───────────────────────────────────────────────────────
class TestCheckA_RunIdJsonBasis(unittest.TestCase):
    """검사 A (CI run ID) — verification JSON SSoT 기준 회귀 테스트."""

    def test_a_free_text_stale_run_id_ignored_when_packet_block_fresh(
        self,
    ) -> None:
        """자유서술 상단 stale run_id, 블록+vj 최신 → PASS.

        AC-1 검증 / oracle: case_normal_01, case_regression_01.
        """
        pr_body = _make_pr_body(
            free_text_run_id=STALE_RUN_ID,
            block_run_id=LATEST_RUN_ID,
            block_sha=HEAD_SHA,
        )
        vj = _make_vj(run_id=LATEST_RUN_ID)
        result = _check_protocol_consistency(**_base_args(pr_body, vj))
        self.assertTrue(
            result.get("allow_accept", False),
            f"BLOCKED unexpectedly: {result}",
        )
        self.assertNotEqual(result.get("failure_code"), "stale_run_id")

    def test_a_packet_block_stale_run_id_blocked(self) -> None:
        """PIPELINE_FINAL_PACKET 블록 안 stale run_id → BLOCKED(stale_run_id).

        AC-2 검증 / oracle: case_edge_01.
        """
        pr_body = _make_pr_body(
            block_run_id=STALE_RUN_ID,
            block_sha=HEAD_SHA,
        )
        vj = _make_vj(run_id=LATEST_RUN_ID)
        result = _check_protocol_consistency(**_base_args(pr_body, vj))
        self.assertFalse(result.get("allow_accept", True))
        self.assertEqual(result.get("failure_code"), "stale_run_id")
        # location은 packet_block — verification_json은 latest와 일치하므로 vj 검증은 통과.
        details = result.get("details", {})
        self.assertEqual(details.get("location"), "packet_block")

    def test_a_verification_json_stale_blocked(self) -> None:
        """verification_json.ci_run_id != latest → BLOCKED(stale_run_id, verification_json).

        AC-6 검증.
        """
        pr_body = _make_pr_body(block_run_id=STALE_RUN_ID, block_sha=HEAD_SHA)
        vj = _make_vj(run_id=STALE_RUN_ID)
        result = _check_protocol_consistency(**_base_args(pr_body, vj))
        self.assertFalse(result.get("allow_accept", True))
        self.assertEqual(result.get("failure_code"), "stale_run_id")
        details = result.get("details", {})
        self.assertEqual(details.get("location"), "verification_json")

    def test_a_verification_json_missing_blocked(self) -> None:
        """verification_json=None → BLOCKED(verification_json_missing).

        AC-4 검증 / oracle: case_error_01.
        """
        pr_body = _make_pr_body(block_run_id=LATEST_RUN_ID, block_sha=HEAD_SHA)
        result = _check_protocol_consistency(**_base_args(pr_body, None))
        self.assertFalse(result.get("allow_accept", True))
        self.assertEqual(result.get("failure_code"), "verification_json_missing")


# ── 검사 B 테스트 ───────────────────────────────────────────────────────
class TestCheckB_ShaJsonBasis(unittest.TestCase):
    """검사 B (head SHA) — verification JSON SSoT 기준 회귀 테스트."""

    def test_b_free_text_stale_sha_ignored_when_packet_block_fresh(
        self,
    ) -> None:
        """자유서술 상단 stale SHA, 블록+vj 최신 SHA → PASS.

        AC-3 검증 / oracle: case_edge_02.
        """
        pr_body = _make_pr_body(
            free_text_sha=STALE_SHA,
            block_run_id=LATEST_RUN_ID,
            block_sha=HEAD_SHA,
        )
        vj = _make_vj(sha=HEAD_SHA)
        result = _check_protocol_consistency(**_base_args(pr_body, vj))
        self.assertTrue(
            result.get("allow_accept", False),
            f"BLOCKED unexpectedly: {result}",
        )
        self.assertNotEqual(result.get("failure_code"), "stale_head_sha")

    def test_b_packet_block_stale_sha_blocked(self) -> None:
        """PIPELINE_FINAL_PACKET 블록 안 stale SHA → BLOCKED(stale_head_sha, packet_block)."""
        pr_body = _make_pr_body(
            block_run_id=LATEST_RUN_ID,
            block_sha=STALE_SHA,
        )
        vj = _make_vj(sha=HEAD_SHA)
        result = _check_protocol_consistency(**_base_args(pr_body, vj))
        self.assertFalse(result.get("allow_accept", True))
        self.assertEqual(result.get("failure_code"), "stale_head_sha")
        details = result.get("details", {})
        self.assertEqual(details.get("location"), "packet_block")

    def test_b_verification_json_stale_blocked(self) -> None:
        """verification_json.pr_head_sha != pr_head_sha → BLOCKED(stale_head_sha, verification_json)."""
        pr_body = _make_pr_body(block_run_id=LATEST_RUN_ID, block_sha=HEAD_SHA)
        vj = _make_vj(sha=STALE_SHA)
        result = _check_protocol_consistency(**_base_args(pr_body, vj))
        self.assertFalse(result.get("allow_accept", True))
        self.assertEqual(result.get("failure_code"), "stale_head_sha")
        details = result.get("details", {})
        self.assertEqual(details.get("location"), "verification_json")


# ── 통합 + 기존 회귀 보호 ─────────────────────────────────────────────────
class TestCheckAB_Consistency(unittest.TestCase):
    """검사 A/B 통합 및 기존 회귀 테스트 보존 검증."""

    def test_a_b_pass_when_all_consistent(self) -> None:
        """자유서술/블록/vj 모두 최신 일관 → PASS."""
        pr_body = _make_pr_body(
            block_run_id=LATEST_RUN_ID,
            block_sha=HEAD_SHA,
        )
        vj = _make_vj()
        result = _check_protocol_consistency(**_base_args(pr_body, vj))
        self.assertTrue(
            result.get("allow_accept", False),
            f"BLOCKED unexpectedly: {result}",
        )

    def test_existing_protocol_consistency_tests_intact(self) -> None:
        """기존 회귀 패턴 보존 확인 — vj가 있고 모든 값 일치 시 PASS (smoke)."""
        pr_body = _make_pr_body(block_run_id=LATEST_RUN_ID, block_sha=HEAD_SHA)
        vj = _make_vj(changed_files=CHANGED_FILES)
        result = _check_protocol_consistency(
            pr_body=pr_body,
            acceptance_packet_body=(
                "<!-- pipeline-human-acceptance-packet -->\n"
                f"head_sha: {HEAD_SHA}\nrun_id: {LATEST_RUN_ID}\n"
            ),
            pr_changed_files=CHANGED_FILES,
            pr_head_sha=HEAD_SHA,
            latest_ci_run_id=LATEST_RUN_ID,
            latest_ci_run_conclusion="success",
            verification_json=vj,
        )
        self.assertTrue(
            result.get("allow_accept", False),
            f"Regression detected: {result}",
        )

    def test_a_b_existing_43_tests_intact(self) -> None:
        """기존 43개 회귀 테스트가 새 정책에 맞춰 모두 PASS함을 검증한다.

        IMP-20260605-9EF5 정책 변경 후 tests/test_protocol_consistency.py의
        43개 테스트가 PASS이어야 한다 (5개 테스트가 새 정책에 맞춰 갱신됨).
        본 테스트는 subprocess로 pytest를 호출해 정확한 PASS 수를 확인한다.
        """
        import subprocess
        cmd = [
            sys.executable, "-m", "pytest",
            str(ROOT / "tests" / "test_protocol_consistency.py"),
            "-q", "--no-header", "-p", "no:cacheprovider",
        ]
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=str(ROOT),
        )
        # pytest output에서 "43 passed" 패턴 확인
        combined = (completed.stdout or "") + "\n" + (completed.stderr or "")
        self.assertIn(
            "43 passed",
            combined,
            f"Expected 43 passed in tests/test_protocol_consistency.py, "
            f"got returncode={completed.returncode}\n"
            f"stdout tail: {(completed.stdout or '')[-500:]}\n"
            f"stderr tail: {(completed.stderr or '')[-500:]}",
        )
        self.assertEqual(
            completed.returncode, 0,
            f"pytest exited non-zero: {combined[-1000:]}",
        )


if __name__ == "__main__":
    unittest.main()
