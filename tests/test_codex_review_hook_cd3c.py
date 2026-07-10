# [Purpose]: IMP-20260625-CD3C Codex 사용자 승인 검토 hook helper의 회귀 테스트.
#   Parser/Dedupe/Verdict/Safety 4개 그룹으로 핵심 동작과 안전성 보장을 검증한다.
# [Assumptions]: .claude/hooks/codex_user_acceptance_review.py가 존재하고 import 가능하다.
#   gh/codex CLI는 호출하지 않는 순수 단위 테스트로 구성한다 (mock 불필요한 함수 위주).
# [Vulnerability & Risks]: Safety 테스트는 소스 텍스트 패턴 검사이므로 우회 패턴(난독화된
#   gh pr comment 호출)을 100% 잡지 못할 수 있다. 이를 위해 정규식으로 실제 실행 결합
#   패턴을 검사한다.
# [Improvement]: 시간이 더 있다면 AST 분석으로 subprocess 호출 인자 그래프를 추적할 것이다.
"""Codex 승인 검토 hook helper 회귀 테스트 (IMP-20260625-CD3C)."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

# --- 대상 helper 모듈 로드 ---
_REPO_ROOT = Path(__file__).resolve().parents[1]
_HELPER_PATH = _REPO_ROOT / ".claude" / "hooks" / "codex_user_acceptance_review.py"


def _load_helper():
    """helper 모듈을 importlib로 로드하여 반환."""
    spec = importlib.util.spec_from_file_location(
        "codex_user_acceptance_review_under_test", str(_HELPER_PATH)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cx = _load_helper()


# 트리거 5요소 모두 포함된 정상 블록
_NORMAL_BLOCK = (
    "사용자 승인 요청\n\n"
    "PR: https://github.com/hojiyong2-commits/Pipeline/pull/702\n\n"
    "승인 코드:\n"
    "ACCEPT-IMP-20260625-CD3C\n\n"
    "CODEX 검토 필요"
)


class TestParser:
    """parse_acceptance_block 파싱 동작 검증."""

    def test_parse_all_five_elements_present(self):
        """5요소 모두 있을 때 블록을 감지하고 PR URL/코드를 추출한다."""
        result = cx.parse_acceptance_block(_NORMAL_BLOCK)
        assert result is not None
        assert result["pr_url"] == (
            "https://github.com/hojiyong2-commits/Pipeline/pull/702"
        )
        assert result["accept_code"] == "ACCEPT-IMP-20260625-CD3C"

    def test_parse_missing_codex_required(self):
        """마지막 의미 줄이 'CODEX 검토 필요'가 아니면 None."""
        text = "다음 단계로 진행합니다.\n\nCODEX 검토 필요"
        assert cx.parse_acceptance_block(text) is None

    def test_parse_missing_accept_code(self):
        """승인 코드(ACCEPT-...)가 없으면 None."""
        text = (
            "사용자 승인 요청\n\n"
            "PR: https://github.com/hojiyong2-commits/Pipeline/pull/702\n\n"
            "승인 코드:\n\n"
            "CODEX 검토 필요"
        )
        assert cx.parse_acceptance_block(text) is None

    def test_parse_ignores_quoted_example(self):
        """인용(>) 내부 예시 블록에는 반응하지 않는다 (AC-3)."""
        text = (
            "> 사용자 승인 요청\n"
            "> PR: https://github.com/a/b/pull/1\n"
            "> 승인 코드:\n"
            "> ACCEPT-EXAMPLE\n"
            "> CODEX 검토 필요"
        )
        assert cx.parse_acceptance_block(text) is None


class TestDedupe:
    """신규 hook의 중복 REJECT 방지 동작 검증 (IMP-20260626-4121 API 갱신).

    구 check_dedupe/_record_dedupe는 제거되었고, 신규 hook은 _is_duplicate_reject로
    (pipeline_id, pr_head_sha, packet_sha256, last_reject_reason) 조합을 비교한다.
    """

    def test_first_review_not_duplicate(self):
        """state가 비어(첫 검토) REJECTED 기록이 없으면 중복 아님(False)."""
        empty_state = {}
        assert (
            cx._is_duplicate_reject(
                empty_state, "IMP-X", "sha1", "pkt1", "REJECT - 사유"
            )
            is False
        )

    def test_same_sha_is_duplicate(self):
        """같은 (pipeline_id, head_sha, packet_sha256, reason) 조합은 중복(True)."""
        state = {
            "status": "REJECTED",
            "pipeline_id": "IMP-X",
            "pr_head_sha": "sha1",
            "packet_sha256": "pkt1",
            "last_reject_reason": "REJECT - 사유",
        }
        assert (
            cx._is_duplicate_reject(state, "IMP-X", "sha1", "pkt1", "REJECT - 사유")
            is True
        )

    def test_different_sha_not_duplicate(self):
        """head SHA가 다르면 같은 reason이어도 중복 아님(재검토 허용)."""
        state = {
            "status": "REJECTED",
            "pipeline_id": "IMP-X",
            "pr_head_sha": "sha1",
            "packet_sha256": "pkt1",
            "last_reject_reason": "REJECT - 사유",
        }
        assert (
            cx._is_duplicate_reject(state, "IMP-X", "sha2", "pkt1", "REJECT - 사유")
            is False
        )


class TestVerdict:
    """process_verdict 형식 검증 및 출력 검증 (IMP-20260626-4121 API 갱신).

    신규 시그니처: process_verdict(verdict, pipeline_id, pr_url, reject_count)
    반환: {decision, output, exit_code, reject_reason}.
    decision은 APPROVE/REJECT/REJECT_HALT, 구 message/post_pr_comment/run_gates_accept 키는 제거됨.
    """

    def test_approve_to_user_output(self):
        """APPROVE_TO_USER 입력 시 decision=APPROVE, output은 빈 문자열, exit 0.

        IMP-20260703-B985 MT-28(이중 출력 방지): Stop hook의 APPROVE 출력은 이제 빈
        문자열이다. 승인 요청문은 gates request-accept --machine-readable JSON의
        approval_request_message로 Pipeline Manager가 1회만 relay하며, hook은 승인 블록을
        더 이상 출력하지 않는다(사용자 2회 수신 방지).
        """
        out = cx.process_verdict(
            "APPROVE_TO_USER", "IMP-X", "https://x/pull/1", 0
        )
        assert out["decision"] == "APPROVE"
        # MT-28: hook은 승인 블록을 출력하지 않는다 → output 빈 문자열, ACCEPT 코드 미포함.
        assert out["output"] == ""
        assert "ACCEPT-IMP-X" not in out["output"]
        assert out["exit_code"] == 0
        # 신규 hook은 PR 댓글 게시/gates accept 자동 실행 키를 두지 않는다 (부재로 안전 보장)
        assert "post_pr_comment" not in out
        assert "run_gates_accept" not in out

    def test_reject_output(self):
        """REJECT - 사유는 원문 그대로 output에 담고 exit 2 (재주입)."""
        out = cx.process_verdict(
            "REJECT - CI 실패", "IMP-X", "https://x/pull/1", 1
        )
        assert out["decision"] == "REJECT"
        assert out["output"] == "REJECT - CI 실패"  # 원문 그대로 (prefix 없음)
        assert out["exit_code"] == 2

    def test_invalid_verdict_raises(self):
        """APPROVE_TO_USER/REJECT 형식이 아니면 ValueError (fail-closed)."""
        with pytest.raises(ValueError):
            cx.process_verdict("MAYBE", "IMP-X", "https://x/pull/1", 0)


class TestSafety:
    """소스에 PR 댓글 게시 / gates accept 실행 경로가 없음을 검증 (AC-8)."""

    @staticmethod
    def _read_source() -> str:
        for enc in ("utf-8", "utf-8-sig", "cp949", "latin-1"):
            try:
                return _HELPER_PATH.read_text(encoding=enc)
            except (UnicodeDecodeError, LookupError):
                continue
        raise AssertionError("helper 소스를 읽을 수 없습니다")

    def test_no_gh_pr_comment_execution(self):
        """subprocess로 gh pr comment를 실행하는 경로가 없어야 한다."""
        src = self._read_source()
        # subprocess 인자 리스트에 'gh' + 'pr' + 'comment'가 함께 들어가는 패턴 부재
        # (docstring/프롬프트 텍스트의 단순 언급은 허용)
        exec_pattern = re.compile(
            r"subprocess[\s\S]{0,200}?[\"']pr[\"'][\s\S]{0,80}?[\"']comment[\"']"
        )
        assert exec_pattern.search(src) is None, (
            "gh pr comment 실행 경로가 발견되었습니다 (AC-8 위반)"
        )
        # ACCEPT 코드를 인자로 게시하는 결합 패턴 부재
        post_accept = re.compile(
            r"[\"']comment[\"'][\s\S]{0,200}?accept_code"
        )
        assert post_accept.search(src) is None, (
            "ACCEPT 코드 게시 경로가 발견되었습니다 (AC-8 위반)"
        )

    def test_no_gates_accept_execution(self):
        """subprocess로 pipeline.py gates accept를 실행하는 경로가 없어야 한다.

        주의: 금지 프롬프트 텍스트("gates accept를 실행하지 마세요")는 허용한다.
        오직 실제 명령 실행(subprocess/Popen/os.system 인자에 gates+accept가
        리스트 토큰으로 결합되는 경우)만 위반으로 판정한다.
        """
        src = self._read_source()
        # subprocess 인자 리스트에 'gates' + 'accept'가 토큰으로 함께 들어가는 패턴 부재
        exec_pattern = re.compile(
            r"(subprocess|Popen|os\.system|os\.popen)"
            r"[\s\S]{0,300}?[\"']gates[\"'][\s\S]{0,80}?[\"']accept[\"']"
        )
        assert exec_pattern.search(src) is None, (
            "gates accept 실행 경로가 발견되었습니다 (AC-8 위반)"
        )
        # subprocess 인자에 'pipeline.py' + 'gates' + 'accept' 토큰이 결합되는 실행 패턴 부재
        cli_exec = re.compile(
            r"(subprocess|Popen|os\.system|os\.popen)"
            r"[\s\S]{0,300}?pipeline\.py[\s\S]{0,80}?gates[\s\S]{0,40}?accept"
        )
        assert cli_exec.search(src) is None, (
            "pipeline.py gates accept 실행 명령이 발견되었습니다 (AC-8 위반)"
        )


HELPER_PATH = _HELPER_PATH


class TestNoTranscriptSilentExit:
    """transcript 없이 실행하면 stdout 없이 exit 0 (REJECT 수정 회귀 테스트)."""

    def test_no_transcript_arg_exits_silently(self):
        """--transcript 없이 실행하면 stdout 출력 없이 exit 0."""
        result = subprocess.run(
            [sys.executable, str(HELPER_PATH)],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_empty_transcript_exits_silently(self):
        """--transcript 값이 없으면(nargs=?) exit 0."""
        result = subprocess.run(
            [sys.executable, str(HELPER_PATH), "--transcript"],
            capture_output=True, text=True
        )
        # nargs='?' 이면 None이 되므로 조용히 종료
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_nonexistent_transcript_exits_silently(self):
        """존재하지 않는 transcript 경로 → 조용히 exit 0."""
        result = subprocess.run(
            [sys.executable, str(HELPER_PATH), "--transcript", "/nonexistent/path.jsonl"],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""


def test_oracle_cases_align():
    """oracle normal_01/edge_01 케이스가 parse 결과와 일치하는지 확인."""
    oracle_dir = _REPO_ROOT / "tests" / "oracles" / "IMP-20260625-CD3C"
    for case in ("normal_01", "edge_01"):
        inp = json.loads(
            (oracle_dir / case / "input.json").read_text(encoding="utf-8")
        )
        exp = json.loads(
            (oracle_dir / case / "expected.json").read_text(encoding="utf-8")
        )
        block = cx.parse_acceptance_block(inp["transcript_tail"])
        got = {
            "block_found": block is not None,
            "pr_url": block["pr_url"] if block else None,
            "accept_code": block["accept_code"] if block else None,
            "trigger_codex_review": block is not None,
        }
        assert got == exp, f"oracle {case} mismatch: {got} != {exp}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-x", "-q"]))
