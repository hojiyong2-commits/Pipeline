"""IMP-20260710-DB54 MT-9: Codex Rate-limit + ERROR/REJECT 분리 테스트.

_run_codex_cli_review 의 ERROR/REJECT/APPROVE 분류와 _check_codex_rate_limit 의
reject_count 기반 rate-limit(cli_error 제외)을 검증한다. oracle: TC-error-split / TC-rate-limit-normal.

주의: 실제 구현의 승인 verdict는 정확히 "APPROVE_TO_USER"이며, rate-limit 기본 임계값은 2다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import pipeline


class TestErrorSplit_TC14_RejectOnly:
    """TC-error-split: REJECT만 REJECTED, CLI ERROR는 별개."""

    def test_cli_error_is_error_status(self):
        result = pipeline._run_codex_cli_review(1, "", "connection error")
        assert result["status"] == "ERROR"
        assert result.get("verdict") != "REJECT"

    def test_explicit_reject_is_rejected(self):
        result = pipeline._run_codex_cli_review(0, "REJECT - 코드 문제", "")
        assert result["status"] == "REJECTED"
        assert result["verdict"] == "REJECT"

    def test_approve_to_user_is_approved(self):
        result = pipeline._run_codex_cli_review(0, "APPROVE_TO_USER", "")
        assert result["status"] == "APPROVED"


class TestErrorSplit_TC15_CliError:
    """TC-15: CLI 오류/timeout/parse 실패는 모두 ERROR."""

    def test_cli_error_classified_as_error(self):
        result = pipeline._run_codex_cli_review(1, "", "connection refused")
        assert result["status"] == "ERROR"
        assert result.get("verdict", "") != "REJECT"

    def test_timeout_classified_as_error(self):
        result = pipeline._run_codex_cli_review(-1, "", "timeout")
        assert result["status"] == "ERROR"

    def test_parse_failure_is_error(self):
        # exit 0 이지만 유효 verdict 형식이 아님(bare APPROVE) → parse_failure ERROR.
        result = pipeline._run_codex_cli_review(0, "APPROVE", "")
        assert result["status"] == "ERROR"

    def test_empty_stdout_is_error(self):
        result = pipeline._run_codex_cli_review(0, "", "")
        assert result["status"] == "ERROR"


class TestRateLimit_TC16_RejectThreshold:
    """TC-16: rate-limit은 reject_count에만 기반한다 (기본 임계값 2)."""

    def test_default_threshold_two_triggers(self):
        result = pipeline._check_codex_rate_limit(2, 0, retry_cli_error=False)
        assert result["status"] == "RATE_LIMITED"

    def test_reject_one_not_rate_limited(self):
        result = pipeline._check_codex_rate_limit(1, 0, retry_cli_error=False)
        assert result["status"] == "OK"

    def test_explicit_threshold_three(self):
        # 명시적 임계값 3: reject 2 → OK, reject 3 → RATE_LIMITED.
        assert (
            pipeline._check_codex_rate_limit(2, 0, reject_threshold=3)["status"] == "OK"
        )
        assert (
            pipeline._check_codex_rate_limit(3, 0, reject_threshold=3)["status"]
            == "RATE_LIMITED"
        )

    def test_cli_error_does_not_trigger_rate_limit(self):
        # cli_error_count만 높아도 rate-limit 아님 (reject_count=0).
        result = pipeline._check_codex_rate_limit(0, 5, retry_cli_error=False)
        assert result["status"] != "RATE_LIMITED"

    def test_negative_reject_count_clamped(self):
        result = pipeline._check_codex_rate_limit(-3, 0)
        assert result["status"] == "OK"


class TestRateLimit_TC17_ErrorNotCountedAsReject:
    """TC-rate-limit-normal: CLI ERROR는 reject_count를 증가시키지 않는다."""

    def test_error_verdict_is_none(self):
        result = pipeline._run_codex_cli_review(1, "", "rate limit exceeded")
        assert result["status"] == "ERROR"
        # verdict가 None이므로 reject 카운트 로직에서 REJECTED로 집계되지 않는다.
        assert result["verdict"] is None

    def test_reject_and_error_are_distinct_statuses(self):
        err = pipeline._run_codex_cli_review(1, "", "rate limit exceeded")
        rej = pipeline._run_codex_cli_review(0, "REJECT - reason here", "")
        assert err["status"] == "ERROR"
        assert rej["status"] == "REJECTED"
        assert err["status"] != rej["status"]


class TestTypeGuards:
    """None/비int 입력 방어."""

    def test_none_exit_code_raises(self):
        try:
            pipeline._run_codex_cli_review(None, "", "")
            assert False, "None exit_code should raise"
        except TypeError:
            pass

    def test_none_reject_count_raises(self):
        try:
            pipeline._check_codex_rate_limit(None)
            assert False, "None reject_count should raise"
        except TypeError:
            pass

    def test_threshold_below_one_raises(self):
        try:
            pipeline._check_codex_rate_limit(1, 0, reject_threshold=0)
            assert False, "threshold < 1 should raise ValueError"
        except ValueError:
            pass


if __name__ == "__main__":
    import subprocess

    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
