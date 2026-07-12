"""test_codex_model_router_dae1.py — IMP-20260712-DAE1 REJECT#3 Codex Model Router 검증.

TC-1~TC-24: gpt-5.6 모델 라우터의 정책 SSoT, invoked/actual/selected 분리,
model_verification_level, ChatGPT Plus 인증, OPENAI_API_KEY 제거(요구3), 실제 codex exec
호출 seam(fake executable dependency injection), verdict 4-필드 스키마(요구6), cache 정책(요구11),
운영 신뢰 게이트(요구9)를 결정적으로 검증한다.

fake executable 방식(요구13): 실제 codex CLI 대신 stdin/argv를 해석하는 테스트용 실행 파일을
주입(_invoke_codex_exec/_check_codex_chatgpt_auth의 codex_bin 인자)하여 subprocess 경로를 실행한다.
production argparse와 분리된 seam이며 environment=test로 기록되어 acceptance_eligible=false를 강제한다.
"""
import os
import stat
import sys
import textwrap
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pipeline  # noqa: E402


# --------------------------------------------------------------------------- #
# fake Codex executable helper (cross-platform).
# --------------------------------------------------------------------------- #
def _make_fake_codex(
    tmp_path: Path,
    *,
    login_ok: bool = True,
    exit_code: int = 0,
    stdout_text: str = "",
    stderr_text: str = "",
    hang_seconds: int = 0,
    echo_api_key: bool = False,
) -> str:
    """fake codex 실행 파일을 생성하고 경로를 반환한다(login status + exec 분기).

    Args:
        tmp_path: 임시 디렉토리.
        login_ok: True면 `login status`에서 "Logged in using ChatGPT" 출력 + exit 0.
        exit_code: exec 경로 종료 코드.
        stdout_text: exec 경로 stdout.
        stderr_text: exec 경로 stderr.
        hang_seconds: exec 경로에서 sleep할 초(타임아웃 테스트용).
        echo_api_key: exec 경로에서 OPENAI_API_KEY 환경변수 상태를 stdout에 출력.
    Returns:
        생성된 실행 파일의 절대 경로 문자열.
    """
    impl = tmp_path / "fake_codex_impl.py"
    impl.write_text(
        textwrap.dedent(
            f"""
            import os, sys, time
            argv = sys.argv[1:]
            if argv[:2] == ["login", "status"]:
                if {login_ok!r}:
                    sys.stdout.write("Logged in using ChatGPT\\n")
                    sys.exit(0)
                sys.stdout.write("Not logged in. Run codex login.\\n")
                sys.exit(1)
            _ = sys.stdin.read()
            if {hang_seconds!r} > 0:
                time.sleep({hang_seconds!r})
            if {echo_api_key!r}:
                sys.stdout.write("OPENAI_API_KEY=" + os.environ.get("OPENAI_API_KEY", "MISSING") + "\\n")
            sys.stdout.write({stdout_text!r})
            sys.stderr.write({stderr_text!r})
            sys.exit({exit_code!r})
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        wrapper = tmp_path / "fake_codex.cmd"
        wrapper.write_text(
            f'@"{sys.executable}" "{impl}" %*\r\n', encoding="utf-8"
        )
        return str(wrapper)
    wrapper = tmp_path / "fake_codex.sh"
    wrapper.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{impl}" "$@"\n', encoding="utf-8"
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR | stat.S_IWUSR)
    return str(wrapper)


def _approve_json() -> str:
    return '{"verdict": "APPROVE_TO_USER"}'


def _reject_json_full() -> str:
    return (
        '{"verdict": "REJECT", "root_cause": "널 역참조", '
        '"reproduction": "빈 입력으로 호출", "required_fix": "None 가드 추가", '
        '"acceptance_criteria": ["빈 입력에서 예외 없음"]}'
    )


# --------------------------------------------------------------------------- #
# TC-1~TC-4: risk → 정책 모델/effort SSoT (gpt-5.6-luna/terra/sol + low/high/high/max).
# --------------------------------------------------------------------------- #
def test_tc01_low_routes_luna_low() -> None:
    p = pipeline._build_codex_model_policy("LOW")
    assert p["selected_model"] == "gpt-5.6-luna"
    assert p["selected_reasoning_effort"] == "low"


def test_tc02_medium_routes_terra_high() -> None:
    p = pipeline._build_codex_model_policy("MEDIUM")
    assert p["selected_model"] == "gpt-5.6-terra"
    assert p["selected_reasoning_effort"] == "high"


def test_tc03_high_routes_sol_high() -> None:
    p = pipeline._build_codex_model_policy("HIGH")
    assert p["selected_model"] == "gpt-5.6-sol"
    assert p["selected_reasoning_effort"] == "high"


def test_tc04_critical_routes_sol_max() -> None:
    p = pipeline._build_codex_model_policy("CRITICAL")
    assert p["selected_model"] == "gpt-5.6-sol"
    assert p["selected_reasoning_effort"] == "max"


# --------------------------------------------------------------------------- #
# TC-5: model/effort(invoked != selected) 불일치 → BLOCKED.
# TC-6: CLI actual 보고했는데 selected 불일치 → BLOCKED.
# TC-7: unverified + HIGH/CRITICAL → BLOCKED.
# --------------------------------------------------------------------------- #
def test_tc05_invoked_mismatch_blocked() -> None:
    r = pipeline._check_codex_model_capability_match(
        "gpt-5.6-sol", "max", "gpt-5.6-luna", "max",
        "unknown", "unknown", "CRITICAL", invocation_ok=True,
    )
    assert r["result"] == "BLOCKED"
    assert r["failure_code"] == "model_mismatch"


def test_tc06_actual_reported_mismatch_blocked() -> None:
    r = pipeline._check_codex_model_capability_match(
        "gpt-5.6-sol", "max", "gpt-5.6-sol", "max",
        "gpt-5.6-luna", "max", "HIGH", invocation_ok=True,
    )
    assert r["result"] == "BLOCKED"
    assert r["failure_code"] == "actual_model_mismatch"


def test_tc07_unverified_high_critical_blocked() -> None:
    # invocation_ok=False → invoked 실행 증거 없음 → unverified → HIGH/CRITICAL 차단.
    r = pipeline._check_codex_model_capability_match(
        "gpt-5.6-sol", "max", "gpt-5.6-sol", "max",
        "unknown", "unknown", "CRITICAL", invocation_ok=False,
    )
    assert r["result"] == "BLOCKED"
    assert r["failure_code"] == "model_verification_unverified"


def test_tc07b_invocation_verified_high_passes() -> None:
    r = pipeline._check_codex_model_capability_match(
        "gpt-5.6-sol", "high", "gpt-5.6-sol", "high",
        "unknown", "unknown", "HIGH", invocation_ok=True,
    )
    assert r["result"] == "OK"
    assert r["model_verification_level"] == pipeline.CODEX_VERIFICATION_INVOCATION


# --------------------------------------------------------------------------- #
# model_verification_level 계산 (요구4).
# --------------------------------------------------------------------------- #
def test_verification_actual_verified() -> None:
    lv = pipeline._compute_model_verification_level(
        "gpt-5.6-sol", "max", "gpt-5.6-sol", "max", "gpt-5.6-sol", "max", True,
    )
    assert lv == pipeline.CODEX_VERIFICATION_ACTUAL


def test_verification_invocation_verified() -> None:
    lv = pipeline._compute_model_verification_level(
        "gpt-5.6-sol", "max", "gpt-5.6-sol", "max", "unknown", "unknown", True,
    )
    assert lv == pipeline.CODEX_VERIFICATION_INVOCATION


def test_verification_unverified_when_not_invoked() -> None:
    lv = pipeline._compute_model_verification_level(
        "gpt-5.6-sol", "max", "gpt-5.6-sol", "max", "unknown", "unknown", False,
    )
    assert lv == pipeline.CODEX_VERIFICATION_UNVERIFIED


# --------------------------------------------------------------------------- #
# TC-15: 유효한 구조화 REJECT → REJECTED.
# TC-16: root_cause 없는 REJECT → parse_failure(None).
# --------------------------------------------------------------------------- #
def test_tc15_structured_reject_ok() -> None:
    r = pipeline._parse_json_verdict(_reject_json_full())
    assert r is not None
    assert r["verdict"] == "REJECTED"
    assert r["root_cause"] == "널 역참조"
    assert isinstance(r["acceptance_criteria"], list) and r["acceptance_criteria"]


def test_tc16_reject_missing_root_cause_parse_failure() -> None:
    bad = (
        '{"verdict": "REJECT", "reproduction": "x", '
        '"required_fix": "y", "acceptance_criteria": ["z"]}'
    )
    assert pipeline._parse_json_verdict(bad) is None


def test_tc15b_approve_json_ok() -> None:
    r = pipeline._parse_json_verdict(_approve_json())
    assert r is not None
    assert r["verdict"] == "APPROVED"


# --------------------------------------------------------------------------- #
# TC-12: usage limit → ERROR, reject_count와 무관.
# TC-13: timeout → ERROR.
# TC-14: network → ERROR.
# --------------------------------------------------------------------------- #
def test_tc12_usage_limit_is_error() -> None:
    r = pipeline._run_codex_cli_review(1, "", "you've hit your usage limit")
    assert r["status"] == "ERROR"
    assert r["error_type"] == "usage_limit"
    assert r["verdict"] is None


def test_tc13_timeout_is_error() -> None:
    r = pipeline._run_codex_cli_review(-1, "", "operation timed out")
    assert r["status"] == "ERROR"
    assert r["error_type"] == "timeout"


def test_tc14_network_is_error() -> None:
    r = pipeline._run_codex_cli_review(1, "", "network connection refused")
    assert r["status"] == "ERROR"
    assert r["error_type"] == "network"


# --------------------------------------------------------------------------- #
# TC-17/TC-18: cache 정책 — LOW 허용, CRITICAL 금지.
# --------------------------------------------------------------------------- #
def test_tc17_low_cache_allowed() -> None:
    assert pipeline._build_codex_model_policy("LOW")["cache_allowed"] is True


def test_tc18_critical_cache_forbidden() -> None:
    assert pipeline._build_codex_model_policy("CRITICAL")["cache_allowed"] is False


def test_policy_signature_changes_with_model() -> None:
    sig_low = pipeline._codex_policy_signature(pipeline._build_codex_model_policy("LOW"))
    sig_high = pipeline._codex_policy_signature(pipeline._build_codex_model_policy("HIGH"))
    assert sig_low != sig_high  # 정책 변경 → cache key 변경 → cache miss(요구11).


# --------------------------------------------------------------------------- #
# risk classifier fail-closed (요구12).
# --------------------------------------------------------------------------- #
def test_tc_empty_changeset_blocked() -> None:
    r = pipeline._classify_codex_review_risk([], [])
    assert r["risk_level"] == "BLOCKED"
    assert r["blocked"] is True


def test_tc_critical_function_change() -> None:
    r = pipeline._classify_codex_review_risk(["pipeline.py"], ["_cmd_gates_request_accept"])
    assert r["risk_level"] == "CRITICAL"


def test_tc_router_self_change_is_critical() -> None:
    r = pipeline._classify_codex_review_risk(["pipeline.py"], ["_classify_codex_review_risk"])
    assert r["risk_level"] == "CRITICAL"


def test_tc_tests_inherit_not_raise() -> None:
    # tests/** 만 변경 → risk 상승 없음(LOW).
    r = pipeline._classify_codex_review_risk(["tests/e2e/test_x.py"], [])
    assert r["risk_level"] == "LOW"


# --------------------------------------------------------------------------- #
# TC-22: ChatGPT 로그인 아니면 BLOCKED / 로그인 맞으면 OK (fake executable).
# --------------------------------------------------------------------------- #
def test_tc22_chatgpt_auth_ok(tmp_path: Path) -> None:
    fake = _make_fake_codex(tmp_path, login_ok=True)
    r = pipeline._check_codex_chatgpt_auth(codex_bin=fake)
    assert r["result"] == "OK"
    assert r["auth_source"] == "chatgpt"


def test_tc22_not_chatgpt_blocked(tmp_path: Path) -> None:
    fake = _make_fake_codex(tmp_path, login_ok=False)
    r = pipeline._check_codex_chatgpt_auth(codex_bin=fake)
    assert r["result"] == "BLOCKED"
    assert r["failure_code"] == "codex_not_chatgpt_authenticated"


def test_tc22_auth_missing_executable_blocked(tmp_path: Path) -> None:
    r = pipeline._check_codex_chatgpt_auth(codex_bin=str(tmp_path / "does_not_exist"))
    assert r["result"] == "BLOCKED"
    assert r["failure_code"] == "codex_auth_check_failed"


# --------------------------------------------------------------------------- #
# TC-1~TC-4(실행): fake codex exec가 selected 모델/effort 인자를 받고 실행된다.
# TC-21: OPENAI_API_KEY가 codex subprocess 환경에서 제거된다.
# TC-23: sanitized codex_cli_command에 실제 인자가 완전히 담긴다.
# --------------------------------------------------------------------------- #
def test_tc_invoke_returns_invoked_fields(tmp_path: Path) -> None:
    fake = _make_fake_codex(tmp_path, exit_code=0, stdout_text=_approve_json())
    out = pipeline._invoke_codex_exec(
        "gpt-5.6-sol", "max", "리뷰 프롬프트", timeout=30, codex_bin=fake,
    )
    assert out["invoked"] is True
    assert out["exit_code"] == 0
    assert out["invoked_model"] == "gpt-5.6-sol"
    assert out["invoked_effort"] == "max"
    # 실제 CLI가 model을 보고하지 않으면 actual은 unknown으로 남는다(허위 기록 금지).
    assert out["actual_model"] == "unknown"


def test_tc23_sanitized_command_complete(tmp_path: Path) -> None:
    fake = _make_fake_codex(tmp_path, exit_code=0, stdout_text=_approve_json())
    out = pipeline._invoke_codex_exec(
        "gpt-5.6-terra", "high", "p", timeout=30, codex_bin=fake,
    )
    cmd = out["codex_cli_command"]
    assert "codex exec" in cmd
    assert "--model gpt-5.6-terra" in cmd
    assert "model_reasoning_effort=high" in cmd
    assert "--sandbox read-only" in cmd
    assert "--ephemeral" in cmd
    assert "--json" in cmd


def test_tc21_openai_api_key_removed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "EXAMPLE_DUMMY_" + "A" * 24)
    fake = _make_fake_codex(tmp_path, exit_code=0, echo_api_key=True, stdout_text=_approve_json())
    out = pipeline._invoke_codex_exec(
        "gpt-5.6-sol", "max", "p", timeout=30, codex_bin=fake,
    )
    # fake가 관측한 OPENAI_API_KEY는 MISSING이어야 한다(요구3: subprocess 환경에서 제거).
    assert "OPENAI_API_KEY=MISSING" in out["stdout"]


def test_tc13_invoke_timeout_returns_not_invoked(tmp_path: Path) -> None:
    fake = _make_fake_codex(tmp_path, hang_seconds=5, stdout_text=_approve_json())
    out = pipeline._invoke_codex_exec(
        "gpt-5.6-sol", "max", "p", timeout=1, codex_bin=fake,
    )
    assert out["invoked"] is False
    assert out["exit_code"] == -1


def test_tc12_invoke_usage_limit_nonzero(tmp_path: Path) -> None:
    fake = _make_fake_codex(tmp_path, exit_code=1, stderr_text="you've hit your usage limit")
    out = pipeline._invoke_codex_exec(
        "gpt-5.6-sol", "max", "p", timeout=30, codex_bin=fake,
    )
    assert out["invoked"] is True
    assert out["exit_code"] == 1
    cli = pipeline._run_codex_cli_review(out["exit_code"], out["stdout"], out["stderr"])
    assert cli["status"] == "ERROR"
    assert cli["error_type"] == "usage_limit"


# --------------------------------------------------------------------------- #
# TC-24: 운영 신뢰 게이트는 codex_cli 또는 verified_cache만 허용(요구9).
# TC-8/TC-9/TC-11: external/injection/environment=test는 승인 자격 없음.
# --------------------------------------------------------------------------- #
def _trust_base(**over) -> dict:
    base = {
        "verdict_source": "codex_cli",
        "acceptance_eligible": True,
        "router_version": pipeline.CODEX_MODEL_ROUTER_VERSION,
        "risk_level": "CRITICAL",
        "model_policy_signature": "sig",
        "codex_cli_command": (
            "codex exec --model gpt-5.6-sol -c model_reasoning_effort=max "
            "--sandbox read-only --ephemeral --json -C <repo-root> -"
        ),
        "selected_model": "gpt-5.6-sol",
        "selected_reasoning_effort": "max",
        "invoked_model": "gpt-5.6-sol",
        "invoked_effort": "max",
        "actual_model": "unknown",
        "actual_effort": "unknown",
        "model_verification_level": pipeline.CODEX_VERIFICATION_INVOCATION,
        "auth_source": "chatgpt",
    }
    base.update(over)
    return base


def test_tc24_codex_cli_trust_pass() -> None:
    assert pipeline._check_codex_review_operational_trust(_trust_base())["status"] == "PASS"


def test_tc24_verified_cache_trust_pass() -> None:
    r = pipeline._check_codex_review_operational_trust(_trust_base(verdict_source="verified_cache"))
    assert r["status"] == "PASS"


def test_tc24_external_verdict_blocked() -> None:
    r = pipeline._check_codex_review_operational_trust(_trust_base(verdict_source="external_verdict"))
    assert r["status"] == "BLOCKED"
    assert r["failure_code"] == "codex_review_untrusted_verdict_source"


def test_tc09_external_cli_injection_blocked() -> None:
    r = pipeline._check_codex_review_operational_trust(
        _trust_base(verdict_source="external_cli_injection")
    )
    assert r["status"] == "BLOCKED"


def test_tc08_not_acceptance_eligible_blocked() -> None:
    r = pipeline._check_codex_review_operational_trust(_trust_base(acceptance_eligible=False))
    assert r["status"] == "BLOCKED"
    assert r["failure_code"] == "codex_review_not_acceptance_eligible"


def test_trust_unverified_high_critical_blocked() -> None:
    r = pipeline._check_codex_review_operational_trust(
        _trust_base(model_verification_level=pipeline.CODEX_VERIFICATION_UNVERIFIED)
    )
    assert r["status"] == "BLOCKED"
    assert r["failure_code"] == "codex_review_unverified_high_critical"


def test_trust_invoked_mismatch_blocked() -> None:
    r = pipeline._check_codex_review_operational_trust(
        _trust_base(invoked_model="gpt-5.6-luna")
    )
    assert r["status"] == "BLOCKED"
    assert r["failure_code"] == "codex_review_model_mismatch"


def test_trust_auth_source_not_chatgpt_blocked() -> None:
    r = pipeline._check_codex_review_operational_trust(_trust_base(auth_source="apikey"))
    assert r["status"] == "BLOCKED"
    assert r["failure_code"] == "codex_review_auth_source_not_chatgpt"


def test_trust_actual_verified_pass() -> None:
    r = pipeline._check_codex_review_operational_trust(
        _trust_base(
            actual_model="gpt-5.6-sol", actual_effort="max",
            model_verification_level=pipeline.CODEX_VERIFICATION_ACTUAL,
        )
    )
    assert r["status"] == "PASS"
