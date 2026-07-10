"""IMP-20260710-DB54 MT-6: Codex Preflight 검사 회귀 테스트.

_run_codex_preflight_checks 함수를 직접 호출하여 preflight 10개 검사의 PASS/BLOCKED
판정과 failure_codes 집계를 검증한다. oracle: TC-preflight-normal / TC-preflight-edge.
"""
import sys
from pathlib import Path

# pipeline.py import
sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline import _run_codex_preflight_checks


def _make_state(tech="PASS", oracle="PASS", ci="PASS", frozen=True, reject=0):
    return {
        "pipeline_id": "IMP-20260710-DB54",
        "external_gates": {
            "technical": {"status": tech},
            "oracle": {"status": oracle},
            "github_ci": {"status": ci},
        },
        "contract": {"frozen": frozen},
        "codex_review_loop_state": {"status": "IDLE", "reject_count": reject},
    }


def _make_bundle(raw_accept=False, accept_val="", oracle_count=0):
    b = {
        "pipeline_id": "IMP-20260710-DB54",
        "contract_sha256": "abc123",
        "changed_files_count": 5,
        "oracle_count": oracle_count,
    }
    if raw_accept:
        b["_test_raw_accept"] = accept_val
    return b


class TestPreflight_TC1_AllPass:
    """TC-preflight-normal: 모든 검사 통과."""

    def test_result_pass(self):
        state = _make_state()
        bundle = _make_bundle()
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert result["result"] == "PASS"
        assert result["blocked"] is False
        assert result["preflight_checks_failed"] == 0
        assert result["failure_codes"] == []

    def test_checks_passed_count(self):
        state = _make_state()
        bundle = _make_bundle()
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert result["preflight_checks_passed"] == 10


class TestPreflight_TC2_RawAcceptCode:
    """TC-preflight-edge: bundle에 raw ACCEPT 코드 포함 → BLOCKED."""

    def test_blocked_on_raw_accept(self):
        state = _make_state()
        bundle = _make_bundle(
            raw_accept=True, accept_val="ACCEPT-IMP-20260710-DB54-deadbeef"
        )
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert result["result"] == "BLOCKED"
        assert result["blocked"] is True
        assert "bundle_contains_raw_accept_code" in result["failure_codes"]

    def test_raw_accept_only_one_failure(self):
        state = _make_state()
        bundle = _make_bundle(
            raw_accept=True, accept_val="ACCEPT-IMP-20260710-DB54-deadbeef"
        )
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert result["preflight_checks_passed"] == 9
        assert result["preflight_checks_failed"] == 1
        assert result["failure_codes"] == ["bundle_contains_raw_accept_code"]


class TestPreflight_TC3_GateFail:
    """technical/oracle gate PASS 필요."""

    def test_blocked_on_tech_fail(self):
        state = _make_state(tech="FAIL")
        bundle = _make_bundle()
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert result["result"] == "BLOCKED"
        assert result["blocked"] is True

    def test_blocked_on_oracle_fail(self):
        state = _make_state(oracle="FAIL")
        bundle = _make_bundle()
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert result["result"] == "BLOCKED"

    def test_blocked_on_ci_fail(self):
        state = _make_state(ci="FAIL")
        bundle = _make_bundle()
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert result["result"] == "BLOCKED"

    def test_blocked_on_contract_not_frozen(self):
        state = _make_state(frozen=False)
        bundle = _make_bundle()
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert result["result"] == "BLOCKED"


class TestPreflight_TC4_NotConfigured:
    """NOT_CONFIGURED 패턴 → BLOCKED."""

    def test_blocked_on_not_configured(self):
        state = _make_state()
        bundle = _make_bundle()
        bundle["_test_not_configured"] = "NOT_CONFIGURED"
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert result["result"] == "BLOCKED"
        assert "no_not_configured_fallback" in result["failure_codes"]


class TestPreflight_TC5_RejectRateLimit:
    """reject_count >= 3 → BLOCKED."""

    def test_blocked_on_rate_limit(self):
        state = _make_state(reject=3)
        bundle = _make_bundle()
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert result["result"] == "BLOCKED"
        assert "reject_count_below_limit" in result["failure_codes"]

    def test_reject_two_still_passes(self):
        state = _make_state(reject=2)
        bundle = _make_bundle()
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert "reject_count_below_limit" not in result["failure_codes"]


class TestPreflight_TC6_TypeGuards:
    """None/비dict 입력 방어."""

    def test_none_bundle_raises(self):
        state = _make_state()
        try:
            _run_codex_preflight_checks(None, state, "IMP-20260710-DB54")
            assert False, "None bundle should raise TypeError"
        except TypeError:
            pass

    def test_none_state_raises(self):
        bundle = _make_bundle()
        try:
            _run_codex_preflight_checks(bundle, None, "IMP-20260710-DB54")
            assert False, "None state should raise TypeError"
        except TypeError:
            pass

    def test_non_str_pipeline_id_raises(self):
        state = _make_state()
        bundle = _make_bundle()
        try:
            _run_codex_preflight_checks(bundle, state, 123)
            assert False, "non-str pipeline_id should raise TypeError"
        except TypeError:
            pass


class TestPreflight_TC7_NonceNoFalsePositive:
    """pipeline_id 날짜/소문자 SHA가 nonce로 오탐되지 않아야 한다."""

    def test_pipeline_id_date_not_flagged_as_nonce(self):
        state = _make_state()
        bundle = _make_bundle()
        # pipeline_id "IMP-20260710-DB54" 와 contract_sha256 "abc123" 이 있음.
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert "bundle_contains_nonce" not in result["failure_codes"]

    def test_uppercase_base32_nonce_flagged(self):
        state = _make_state()
        bundle = _make_bundle()
        bundle["_leak"] = "A2B3C4D5"  # 8자 base32 uppercase + 알파벳 포함
        result = _run_codex_preflight_checks(bundle, state, "IMP-20260710-DB54")
        assert "bundle_contains_nonce" in result["failure_codes"]


if __name__ == "__main__":
    import subprocess

    sys.exit(
        subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"])
    )
