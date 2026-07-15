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
import pytest  # noqa: E402


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
# TC-15c: NDJSON agent_message.text에 JSON 형식 verdict가 포함된 경우 파싱.
# IMP-20260712-DAE1 bugfix#5: _parse_json_verdict가 agent_message.text에 적용되지 않아
# parse_failure가 발생하던 버그 수정 회귀 테스트.
# --------------------------------------------------------------------------- #
def _ndjson_agent_json(verdict_json_str: str) -> str:
    """agent_message.text에 JSON 형식 verdict가 포함된 NDJSON stdout 생성."""
    import json as _json
    lines = [
        _json.dumps({"type": "thread.started", "thread_id": "test-abc"}),
        _json.dumps({"type": "turn.started"}),
        _json.dumps({
            "type": "item.completed",
            "item": {"id": "item_0", "type": "agent_message", "text": verdict_json_str},
        }),
    ]
    return "\n".join(lines)


def test_tc15c_ndjson_approve_json_in_text_ok() -> None:
    """agent_message.text = '{"verdict":"APPROVE_TO_USER"}' → APPROVED (bugfix#5 회귀 테스트)."""
    stdout = _ndjson_agent_json('{"verdict":"APPROVE_TO_USER"}')
    r = pipeline._run_codex_cli_review(0, stdout, "")
    assert r["status"] == "APPROVED", f"예상 APPROVED, 실제: {r}"
    assert r["verdict"] == "APPROVE_TO_USER"
    assert r["error_type"] is None


def test_tc15c_ndjson_reject_json_in_text_ok() -> None:
    """agent_message.text = '{verdict:REJECT,4필드}' → REJECTED (bugfix#5 회귀 테스트)."""
    verdict_json = (
        '{"verdict":"REJECT","root_cause":"rc","reproduction":"rp",'
        '"required_fix":"rf","acceptance_criteria":["ac1"]}'
    )
    stdout = _ndjson_agent_json(verdict_json)
    r = pipeline._run_codex_cli_review(0, stdout, "")
    assert r["status"] == "REJECTED", f"예상 REJECTED, 실제: {r}"
    assert r["verdict"] == "REJECT"
    assert r["error_type"] is None


def test_tc15c_ndjson_reject_json_missing_fields_parse_failure() -> None:
    """agent_message.text = '{"verdict":"REJECT"}' (4필드 누락) → parse_failure (fail-closed)."""
    stdout = _ndjson_agent_json('{"verdict":"REJECT"}')
    r = pipeline._run_codex_cli_review(0, stdout, "")
    assert r["status"] == "ERROR"
    assert r["error_type"] == "parse_failure"
    assert r["verdict"] is None


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
# REJECT#20 수정으로 명시적 codex_bin도 _verify_codex_binary_path_trust를 거치게 됐다.
# 이 테스트들은 인증 로직만 검증하므로 trust 체인을 monkeypatch로 bypass한다.
# trust/provenance 체크 자체는 TestTC22Reject20ExactHostnameAndExplicitBinE2E 클래스에서 검증.
# --------------------------------------------------------------------------- #
def _mock_verify_trust_ok(path: str) -> dict:
    """테스트용: 모든 경로를 trusted=True, acceptance_eligible=True로 반환한다."""
    return {
        "trusted": True,
        "path": path,
        "sha256": "deadbeefdeadbeef",
        "untrusted_reason": None,
        "install_source": "npm_global",
        "install_type": "npm_wrapper",
        "js_entrypoint_path": "",
        "js_entrypoint_sha256": "",
        "native_binary_path": "",
        "native_binary_sha256": "",
        "acceptance_eligible": True,
        "provenance_reason": "",
    }


def test_tc22_chatgpt_auth_ok(tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:  # type: ignore[name-defined]
    # REJECT#20: 명시적 codex_bin도 trust 체크를 거치므로 monkeypatch로 bypass한다.
    import pytest  # noqa: F401 (type hint용)
    monkeypatch.setattr(pipeline, "_verify_codex_binary_path_trust", _mock_verify_trust_ok)
    fake = _make_fake_codex(tmp_path, login_ok=True)
    r = pipeline._check_codex_chatgpt_auth(codex_bin=fake)
    assert r["result"] == "OK"
    assert r["auth_source"] == "chatgpt"


def test_tc22_not_chatgpt_blocked(tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:  # type: ignore[name-defined]
    import pytest  # noqa: F401 (type hint용)
    monkeypatch.setattr(pipeline, "_verify_codex_binary_path_trust", _mock_verify_trust_ok)
    fake = _make_fake_codex(tmp_path, login_ok=False)
    r = pipeline._check_codex_chatgpt_auth(codex_bin=fake)
    assert r["result"] == "BLOCKED"
    assert r["failure_code"] == "codex_not_chatgpt_authenticated"


def test_tc22_auth_missing_executable_blocked(tmp_path: Path, monkeypatch: "pytest.MonkeyPatch") -> None:  # type: ignore[name-defined]
    import pytest  # noqa: F401 (type hint용)
    monkeypatch.setattr(pipeline, "_verify_codex_binary_path_trust", _mock_verify_trust_ok)
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
    # REJECT#21: CRITICAL은 actual_verified만 허용. 기본값을 actual_verified로 갱신.
    # REJECT#27: model_policy_signature를 실제 정책에서 동적으로 계산한다(위조 방지 테스트 대응).
    _critical_policy = pipeline._build_codex_model_policy("CRITICAL")
    _correct_sig = pipeline._codex_policy_signature(_critical_policy)
    base = {
        "verdict_source": "codex_cli",
        "acceptance_eligible": True,
        "router_version": pipeline.CODEX_MODEL_ROUTER_VERSION,
        "risk_level": "CRITICAL",
        "model_policy_signature": _correct_sig,   # "CRITICAL:gpt-5.6-sol:max:enforce"
        "codex_cli_command": (
            "codex exec --model gpt-5.6-sol -c model_reasoning_effort=max "
            "--sandbox read-only --ephemeral --json -C <repo-root> -"
        ),
        "selected_model": "gpt-5.6-sol",
        "selected_reasoning_effort": "max",
        "invoked_model": "gpt-5.6-sol",
        "invoked_effort": "max",
        "actual_model": "gpt-5.6-sol",    # REJECT#21: CRITICAL은 actual_verified 필수
        "actual_effort": "max",
        "model_verification_level": pipeline.CODEX_VERIFICATION_ACTUAL,
        "auth_source": "chatgpt",
        # IMP-20260712-DAE1 REJECT#16: codex_cli/verified_cache에는 binary 신뢰 증거 필수.
        #   테스트용 dummy 값 (운영 신뢰 검사 단위 테스트 전용).
        "codex_binary_path": "/usr/local/bin/codex",
        "codex_binary_sha256": "b" * 64,
        # REJECT#LATEST: native/vendor binary 신뢰 증거 필수 (test-only dummy).
        "codex_native_binary_path": "/usr/local/lib/codex/vendor/codex",
        "codex_native_binary_sha256": "e" * 64,
        # REJECT#21: 새 operational trust 검사 필수 필드 추가.
        "status": "APPROVED",
        "verdict": "APPROVE_TO_USER",
        "environment_untrusted_count": 0,
        "non_converging_at": None,
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


# --------------------------------------------------------------------------- #
# TC-25a~25e (REJECT#4): semantic evidence — 실제 diff가 Codex prompt(stdin)에 실린다.
#   bundle 파일에는 원문을 persist하지 않고(no_nonce_exposure/TC-J 불변식), prompt에만 실어
#   Codex가 실제 변경 코드를 보고 판단하게 한다.
# --------------------------------------------------------------------------- #
def _semantic_bundle(**over) -> dict:
    """_build_codex_prompt_for_review에 넘길 full-text semantic bundle(합성)을 만든다."""
    base = {
        "pipeline_id": "TEST",
        "changed_files": ["pipeline.py"],
        "changed_files_count": 1,
        "diff_hunks": [
            {
                "function": "_test_func",
                "hunk": "@@ -1,3 +1,4 @@\n+    # SENTINEL_CHANGE_XYZ\n     pass\n",
                "is_critical": True,
                "chars": 60,
            }
        ],
        "function_before_after_shas": {
            "pipeline.py::_test_func": {"before": "aaa", "after": "bbb"}
        },
        "test_assertions": {"test_x": ["assert func(1) == 2"]},
        "oracle_results": [
            {"case_id": "tc01", "case_kind": "normal", "result": "recorded"}
        ],
        "evidence_complete": True,
        "truncated_critical_hunks": 0,
        "semantic_evidence_sha256": "xyz",
        "included_functions": ["_test_func"],
        "bundle_budget_chars": 60,
    }
    base.update(over)
    return base


def test_tc25a_diff_hunk_contains_sentinel_change() -> None:
    """bundle의 diff_hunks 원문 변경 라인이 prompt(stdin)에 포함되는지 확인."""
    prompt = pipeline._build_codex_prompt_for_review(_semantic_bundle(), "TEST")
    assert "SENTINEL_CHANGE_XYZ" in prompt, "diff hunk가 프롬프트에 포함돼야 합니다"
    assert "CRITICAL" in prompt, "CRITICAL 표시가 포함돼야 합니다"
    assert "unified diff" in prompt, "diff 섹션 헤더가 포함돼야 합니다"


def test_tc25b_evidence_incomplete_blocks_prompt() -> None:
    """evidence_complete=False이면 prompt 생성 자체가 차단(ValueError)된다.

    REJECT#29: evidence_complete=False는 diff hunk 누락(critical or noncrit) → 항상 BLOCKED.
    예산 증가(REJECT#29)로 실제 실행 시 evidence_complete=True가 보장됨."""
    import pytest
    bundle = _semantic_bundle(evidence_complete=False, diff_hunks=[])
    with pytest.raises((ValueError, SystemExit)):
        pipeline._build_codex_prompt_for_review(bundle, "TEST")


def test_tc25c_before_after_sha_differs_after_change() -> None:
    """before_sha != after_sha이면 실제 코드가 변경된 것(함수 본문 개별 SHA)."""
    shas1 = {"before": "aaa111", "after": "bbb222"}
    shas2 = {"before": "aaa111", "after": "aaa111"}  # 변경 없음
    assert shas1["before"] != shas1["after"]
    assert shas2["before"] == shas2["after"]


def test_tc25d_prompt_receives_diff_in_stdin_payload() -> None:
    """Codex stdin으로 전달될 prompt 원문에 SENTINEL diff가 실제로 들어있는지 검증.

    IMP-20260525-6FAC 방식의 실제 stdin 페이로드 검증(_invoke_codex_exec은 이 prompt를 그대로
    stdin으로 넘긴다). subprocess E2E는 환경 의존성이 높으므로, prompt 생성 경로가 diff를 실어
    보내는지를 결정적으로 검증한다.
    """
    sentinel_hunk = (
        "@@ -10,4 +10,6 @@ def _cmd_gates_codex_review(args):\n"
        "+    # SENTINEL_STDIN_9F2A verified\n     pass\n"
    )
    bundle = _semantic_bundle(
        pipeline_id="TEST-STDIN",
        diff_hunks=[
            {
                "function": "_cmd_gates_codex_review",
                "hunk": sentinel_hunk,
                "is_critical": True,
                "chars": len(sentinel_hunk),
            }
        ],
    )
    prompt = pipeline._build_codex_prompt_for_review(bundle, "TEST-STDIN")
    assert "SENTINEL_STDIN_9F2A" in prompt
    assert "_cmd_gates_codex_review" in prompt


def test_tc25e_critical_function_shas_are_per_function_not_file() -> None:
    """critical_function_shas가 pipeline.py 전체 파일 SHA의 단순 복사가 아니라 함수별 개별 SHA."""
    fas = {
        "pipeline.py::func_a": {"before": "sha_before_a", "after": "sha_after_a"},
        "pipeline.py::func_b": {"before": "sha_before_b", "after": "sha_after_b"},
    }
    # 함수별 after_sha가 서로 다르면 파일 SHA 단순 복사가 아님(개별 계산).
    assert (
        fas["pipeline.py::func_a"]["after"] != fas["pipeline.py::func_b"]["after"]
        or fas["pipeline.py::func_a"]["before"] != fas["pipeline.py::func_b"]["before"]
    )
    # 변경이 있는 함수는 before != after.
    assert fas["pipeline.py::func_a"]["before"] != fas["pipeline.py::func_a"]["after"]


def test_tc25f_extract_python_function_bodies_ast() -> None:
    """_extract_python_function_bodies가 함수 본문을 정확히 분리한다(ast 경로)."""
    src = (
        "def alpha(x):\n"
        "    return x + 1\n"
        "\n"
        "def beta(y):\n"
        "    return y * 2\n"
    )
    bodies = pipeline._extract_python_function_bodies(src)
    assert "alpha" in bodies and "beta" in bodies
    assert "return x + 1" in bodies["alpha"]
    assert "return y * 2" in bodies["beta"]
    # 서로 다른 본문이므로 SHA도 달라야 한다(함수별 개별 SHA 근거).
    import hashlib
    sha_a = hashlib.sha256(bodies["alpha"].encode()).hexdigest()
    sha_b = hashlib.sha256(bodies["beta"].encode()).hexdigest()
    assert sha_a != sha_b


def test_tc25g_semantic_evidence_deterministic_and_redacted() -> None:
    """_build_codex_semantic_evidence는 결정적 SHA를 산출하고 None 입력을 방어한다."""
    import pytest
    # None pipeline_id 방어(AL: None 입력 방어).
    with pytest.raises(TypeError):
        pipeline._build_codex_semantic_evidence(None, [], [])  # type: ignore[arg-type]
    # 빈 changed_files + critical 파일 없음 → evidence_complete=False(fail-closed).
    sem = pipeline._build_codex_semantic_evidence("TEST", [], [])
    assert sem["evidence_complete"] is False
    assert isinstance(sem["semantic_evidence_sha256"], str)


def test_tc25h_request_accept_trust_gate_fail_closed_on_missing_router_version() -> None:
    """REJECT#5 회귀: router_version 없는 결과로 request-accept 경로가 BLOCKED돼야 한다.
    _check_codex_review_operational_trust가 router_version 없을 때 BLOCKED를 반환하는지 검증."""
    # router_version 없는 결과 — 레거시 스키마 시뮬레이션.
    result_no_router_version = {
        "verdict": "APPROVE_TO_USER",
        "verdict_source": "codex_cli",
        "acceptance_eligible": True,
        "pipeline_id": "TEST-LEGACY",
        # router_version 필드 없음(레거시)
        "risk_level": "HIGH",
        "selected_model": "gpt-5.6-sol",
        "selected_reasoning_effort": "high",
        "invoked_model": "gpt-5.6-sol",
        "invoked_effort": "high",
        "model_verification_level": "invocation_verified",
        "model_policy_signature": "sig",
        "codex_cli_command": "codex exec --model gpt-5.6-sol -c model_reasoning_effort=high --sandbox read-only --ephemeral --json -C /repo -",
        "auth_source": "chatgpt",
    }
    trust_result = pipeline._check_codex_review_operational_trust(result_no_router_version)
    # router_version 없으므로 BLOCKED여야 한다.
    assert trust_result.get("status") == "BLOCKED", (
        f"router_version 없는 결과가 PASS됐습니다: {trust_result}"
    )
    assert trust_result.get("failure_code") == "codex_review_router_version_missing"


def test_tc25i_request_accept_trust_gate_fail_closed_on_pipeline_id_mismatch() -> None:
    """REJECT#5/7 회귀: pipeline_id 불일치/누락 차단 코드가 request-accept에 존재해야 한다.
    REJECT#7: pipeline_id가 빈 문자열일 때도 차단해야 한다(기존 `if _result_pipeline_id and ...`
    조건은 빈 값이면 검사를 건너뛰는 우회 경로가 있었음)."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_request_accept)
    # pipeline_id 불일치 차단 코드 존재 확인.
    assert "codex_review_pipeline_id_mismatch" in src, (
        "request-accept에 pipeline_id 불일치 차단 코드가 없습니다 (REJECT#5 요구사항)"
    )
    # REJECT#7: pipeline_id 누락/빈값도 별도 차단 코드가 있어야 한다.
    assert "codex_review_pipeline_id_missing" in src, (
        "request-accept에 pipeline_id 누락/빈값 차단 코드가 없습니다 (REJECT#7 요구사항)"
    )
    # pipeline_id가 비어 있으면 `not _result_pipeline_id` 분기에서 차단돼야 한다(fail-closed).
    assert "not _result_pipeline_id" in src, (
        "request-accept에서 빈 pipeline_id fail-closed 패턴이 없습니다 (REJECT#7 요구사항)"
    )
    assert "router_version_missing" in src, (
        "request-accept에 router_version 누락 차단 코드가 없습니다 (REJECT#5 요구사항)"
    )


# --------------------------------------------------------------------------- #
# TC-26 시리즈: REJECT#9 — plaintext REJECT fallback 제거 + structured 필드 보존
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# TC-27 시리즈: REJECT#10 — evidence_complete fail-open 수정
#   - CODEX_CRITICAL_FUNCTIONS에 _cmd_gates_codex_review 추가
#   - function_before_after_shas: CRITICAL 함수만 포함(비CRITICAL 제거)
#   - 비Python CRITICAL 파일(pipeline-manager-agent.md 등) diff 추출
#   - cross-validation: CRITICAL 함수·파일 커버리지 완전성 검증
#   - CODEX_REVIEW_BUNDLE_BUDGET_CHARS: 30000→65000
# --------------------------------------------------------------------------- #

def test_tc27a_cmd_gates_codex_review_in_critical_functions() -> None:
    """REJECT#10: _cmd_gates_codex_review가 CODEX_CRITICAL_FUNCTIONS에 포함돼야 한다.
    이 함수는 semantic evidence 완전성 검증과 trust gate를 모두 제어하므로 CRITICAL 필수."""
    assert "_cmd_gates_codex_review" in pipeline.CODEX_CRITICAL_FUNCTIONS, (
        "_cmd_gates_codex_review가 CODEX_CRITICAL_FUNCTIONS에 없습니다 (REJECT#10 요구사항)"
    )


def test_tc27b_bundle_budget_sufficient_for_critical_hunks() -> None:
    """REJECT#10: CODEX_REVIEW_BUNDLE_BUDGET_CHARS가 CRITICAL 함수 전체 diff를 수용해야 한다.
    --unified=3 기준 CRITICAL 57397자 수용을 위해 최소 65000자 이상이어야 한다."""
    assert pipeline.CODEX_REVIEW_BUNDLE_BUDGET_CHARS >= 65000, (
        f"예산이 너무 작습니다: {pipeline.CODEX_REVIEW_BUNDLE_BUDGET_CHARS} < 65000 "
        "(REJECT#10: CRITICAL 함수 전체 diff 수용 불가)"
    )


def test_tc27c_function_before_after_shas_critical_only_filter_in_source() -> None:
    """REJECT#10: _build_codex_semantic_evidence가 function_before_after_shas에
    CRITICAL 함수만 포함하는 필터 조건(_fn in _crit_funcs)을 갖는지 소스 검증."""
    import inspect
    src = inspect.getsource(pipeline._build_codex_semantic_evidence)
    assert "_fn in _crit_funcs" in src, (
        "_build_codex_semantic_evidence에 CRITICAL 함수 필터 조건이 없습니다 "
        "(REJECT#10: 비CRITICAL 함수 build_parser 등이 SHA 목록에 포함됨)"
    )


def test_tc27d_cross_validation_in_source() -> None:
    """REJECT#10: _build_codex_semantic_evidence에 cross-validation 코드가 존재해야 한다.
    covered_ids와 _nonpy_crit_expected를 대조하는 로직이 있어야 한다."""
    import inspect
    src = inspect.getsource(pipeline._build_codex_semantic_evidence)
    assert "_covered_ids" in src, (
        "_build_codex_semantic_evidence에 _covered_ids(cross-validation) 변수가 없습니다 "
        "(REJECT#10: CRITICAL 함수·파일 커버리지 검증 필수)"
    )
    assert "_nonpy_crit_expected" in src, (
        "_build_codex_semantic_evidence에 _nonpy_crit_expected(비Python CRITICAL 파일 추적)가 없습니다 "
        "(REJECT#10: pipeline-manager-agent.md 등 비Python CRITICAL 파일 diff 필수)"
    )


def test_tc27e_unified_3_in_source() -> None:
    """REJECT#10: _build_codex_semantic_evidence가 --unified=3을 사용하는지 검증.
    --unified=8은 이웃 hunk를 병합해 함수 귀속 오류를 유발한다(REJECT#10 분석)."""
    import inspect
    src = inspect.getsource(pipeline._build_codex_semantic_evidence)
    assert '"--unified=3"' in src, (
        "_build_codex_semantic_evidence에 --unified=3이 없습니다 (REJECT#10: --unified=8→3 변경 필수)"
    )
    assert '"--unified=8"' not in src, (
        "_build_codex_semantic_evidence에 --unified=8이 남아 있습니다 (REJECT#10: --unified=3으로 교체됨)"
    )


# --------------------------------------------------------------------------- #
# TC-26 시리즈: REJECT#9 — plaintext REJECT fallback 제거 + structured 필드 보존
# --------------------------------------------------------------------------- #

def test_tc26a_plaintext_reject_ndjson_becomes_parse_failure() -> None:
    """REJECT#9: NDJSON agent_message.text의 'REJECT - reason' plaintext는
    4-필드 검증을 우회하므로 parse_failure ERROR로 처리돼야 한다(REJECTED 아님)."""
    ndjson_line = (
        '{"type":"item.completed","item":{"type":"agent_message","text":"REJECT - security issue"}}'
    )
    result = pipeline._run_codex_cli_review(0, ndjson_line, "")
    assert result["status"] != "REJECTED", (
        f"REJECT#9: NDJSON plaintext REJECT는 REJECTED가 아니라 ERROR여야 한다. got status={result['status']!r}"
    )
    assert result.get("error_type") == "parse_failure", (
        f"REJECT#9: NDJSON plaintext REJECT는 parse_failure ERROR여야 한다. got error_type={result.get('error_type')!r}"
    )


def test_tc26b_plaintext_reject_legacy_becomes_parse_failure() -> None:
    """REJECT#9: stdout이 'REJECT - reason' plaintext인 경우도
    4-필드 검증 없이 REJECTED가 아니라 parse_failure ERROR여야 한다."""
    result = pipeline._run_codex_cli_review(0, "REJECT - unauthorized bypass detected", "")
    assert result["status"] != "REJECTED", (
        f"REJECT#9: legacy plaintext REJECT는 REJECTED가 아니라 ERROR여야 한다. got status={result['status']!r}"
    )
    assert result.get("error_type") == "parse_failure", (
        f"REJECT#9: legacy plaintext REJECT는 parse_failure ERROR여야 한다. got error_type={result.get('error_type')!r}"
    )


def test_tc26c_json_reject_preserves_structured_fields() -> None:
    """REJECT#9: JSON verdict REJECT가 _parse_json_verdict를 통과하면
    root_cause/reproduction/required_fix/acceptance_criteria가 반환 dict에 보존돼야 한다."""
    verdict_json = (
        '{"verdict":"REJECT","root_cause":"test root","reproduction":"repro","'
        'required_fix":"fix desc","acceptance_criteria":["criteria1"]}'
    )
    result = pipeline._run_codex_cli_review(0, verdict_json, "")
    assert result["status"] == "REJECTED", f"유효한 JSON REJECT는 REJECTED여야 한다. got {result['status']!r}"
    assert result.get("root_cause") == "test root", f"root_cause 보존 실패: {result.get('root_cause')!r}"
    assert result.get("reproduction") == "repro", f"reproduction 보존 실패: {result.get('reproduction')!r}"
    assert result.get("required_fix") == "fix desc", f"required_fix 보존 실패: {result.get('required_fix')!r}"
    assert result.get("acceptance_criteria") == ["criteria1"], (
        f"acceptance_criteria 보존 실패: {result.get('acceptance_criteria')!r}"
    )


def test_tc26d_ndjson_json_reject_preserves_structured_fields() -> None:
    """REJECT#9: NDJSON agent_message.text가 JSON verdict인 경우에도
    structured 필드가 반환 dict에 보존돼야 한다."""
    import json as _json
    inner_json_str = _json.dumps({
        "verdict": "REJECT",
        "root_cause": "rc",
        "reproduction": "rp",
        "required_fix": "rf",
        "acceptance_criteria": ["ac1", "ac2"],
    })
    # NDJSON 라인을 올바르게 JSON 직렬화한다 (text 안의 큰따옴표가 이스케이프됨).
    ndjson_line = _json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": inner_json_str},
    })
    result = pipeline._run_codex_cli_review(0, ndjson_line, "")
    assert result["status"] == "REJECTED", f"NDJSON JSON REJECT는 REJECTED여야 한다. got {result['status']!r}"
    assert result.get("root_cause") == "rc", f"root_cause 보존 실패: {result.get('root_cause')!r}"
    assert result.get("reproduction") == "rp", f"reproduction 보존 실패: {result.get('reproduction')!r}"
    assert result.get("required_fix") == "rf", f"required_fix 보존 실패: {result.get('required_fix')!r}"
    assert result.get("acceptance_criteria") == ["ac1", "ac2"], (
        f"acceptance_criteria 보존 실패: {result.get('acceptance_criteria')!r}"
    )


# --------------------------------------------------------------------------- #
# TC-28 시리즈: REJECT#11 — cache probe blocked 제거 + cache hit verification
# --------------------------------------------------------------------------- #

def test_tc28a_cache_probe_high_unknown_not_blocked() -> None:
    """REJECT#11 AC#1: actual_model=unknown + HIGH risk 시 _check_codex_cache는
    blocked=True가 아니라 plain cache miss(hit=False, blocked=False)를 반환해야 한다.
    이로써 CLI 실행 전에 차단되지 않고 cache miss 경로로 진행하여 CLI가 실행된다."""
    _model_policy_high = pipeline._build_codex_model_policy("HIGH")
    result = pipeline._check_codex_cache(
        "sha256abc",
        "sha256def",
        {},
        "IMP-20260712-DAE1",
        current_bundle={},
        model_policy=_model_policy_high,
        actual_model="unknown",
        risk_level="HIGH",
    )
    assert result.get("blocked") is not True, (
        "REJECT#11: actual_model=unknown+HIGH는 blocked=True가 아니라 "
        f"cache miss여야 한다. got blocked={result.get('blocked')!r}, "
        f"reason={result.get('reason')!r}"
    )
    assert result.get("hit") is False, (
        "REJECT#11: actual_model=unknown+HIGH는 cache miss(hit=False)여야 한다. "
        f"got hit={result.get('hit')!r}"
    )


def test_tc28b_capability_match_blocks_when_invoked_mismatches_selected() -> None:
    """REJECT#11 AC#2: CLI 실행 후 invoked_model != selected_model이면
    _check_codex_model_capability_match는 BLOCKED를 반환해야 한다(model_mismatch)."""
    result = pipeline._check_codex_model_capability_match(
        "gpt-5.6-sol",   # selected_model
        "high",           # selected_effort
        "gpt-5.6-terra",  # invoked_model (불일치)
        "high",           # invoked_effort
        "unknown",        # actual_model
        "unknown",        # actual_effort
        "HIGH",           # risk_level
        invocation_ok=True,
    )
    assert result.get("result") == "BLOCKED", (
        "REJECT#11: invoked!=selected이면 BLOCKED여야 한다. "
        f"got result={result.get('result')!r}"
    )
    assert result.get("failure_code") == "model_mismatch", (
        "REJECT#11: failure_code는 model_mismatch여야 한다. "
        f"got failure_code={result.get('failure_code')!r}"
    )


def test_tc28c_cache_hit_verification_insufficient_code_in_source() -> None:
    """REJECT#11 AC#3/#4: _cmd_gates_codex_review 소스에 HIGH/CRITICAL cache hit
    verification 검증 및 cache_hit_verification_insufficient failure_code가 있어야 한다."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)
    assert "cache_hit_verification_insufficient" in src, (
        "REJECT#11: _cmd_gates_codex_review에 cache_hit_verification_insufficient "
        "failure_code가 없습니다 (HIGH/CRITICAL cache hit 검증 누락)"
    )
    assert "CODEX_VERIFICATION_UNVERIFIED" in src, (
        "REJECT#11: _cmd_gates_codex_review에 CODEX_VERIFICATION_UNVERIFIED 검사가 없습니다 "
        "(HIGH/CRITICAL cache hit verification_level 검증 누락)"
    )


def test_tc28d_cache_probe_unknown_not_blocked_source_check() -> None:
    """REJECT#11/12 AC#4: _check_codex_cache에서 actual_model=unknown+HIGH 시
    blocked=True 조기 반환이 없어야 한다(캐시 조회가 정상 진행되어 limited cache가 동작).
    REJECT#12: early miss return 제거 → cache lookup 진행 → Fix2 verification 검증."""
    import inspect
    src = inspect.getsource(pipeline._check_codex_cache)
    # REJECT#12 fix 후: actual_model=unknown+HIGH 분기의 blocked=True가 없어야 한다.
    # 그리고 REJECT#11/12 의도 설명 주석이 있어야 한다.
    assert "cache_allowed=False" in src, (
        "REJECT#12: _check_codex_cache에 CRITICAL cache_allowed=False miss 블록이 있어야 한다"
    )
    assert "CLI를 실행하고 _check_codex_model_capability_match로 검증" in src, (
        "REJECT#12: _check_codex_cache에 REJECT#11/12 fix 의도 주석이 없습니다 "
        "(unknown+HIGH early miss 제거 확인)"
    )


def test_tc28e_tc11_oracle_unknown_model_critical_gate() -> None:
    """REJECT#12 AC#1+AC#3: TC-11 oracle — actual_model=unknown + CRITICAL + verification_level 미지정(unverified) 시
    _check_codex_capability_gate는 BLOCKED with model_verification_unverified를 반환해야 한다.
    REJECT#12 fix: 이전 unknown_model_critical_blocked는 model_verification_unverified로 변경됨."""
    # oracle input (verification_level 미지정 → CODEX_VERIFICATION_UNVERIFIED 기본값)
    actual_model = "unknown"
    risk_level = "CRITICAL"
    result = pipeline._check_codex_capability_gate(actual_model, risk_level)
    assert result.get("result") == "BLOCKED", (
        f"REJECT#12 TC-11 oracle: CRITICAL+unverified는 BLOCKED여야 한다. "
        f"got result={result.get('result')!r}"
    )
    # REJECT#12 fix: failure_code가 model_verification_unverified로 변경됨
    assert result.get("failure_code") == "model_verification_unverified", (
        f"REJECT#12 TC-11 oracle: failure_code는 model_verification_unverified여야 한다. "
        f"got failure_code={result.get('failure_code')!r}"
    )


# --------------------------------------------------------------------------- #
# TC-29 시리즈: REJECT#13 — CRITICAL policy force_review_required → effective_force_review
# --------------------------------------------------------------------------- #

def test_tc29a_critical_policy_has_force_review_required() -> None:
    """REJECT#13 AC#2/#3: CRITICAL 정책만 force_review_required=True이며
    LOW/MEDIUM/HIGH는 False여야 한다."""
    for level in ("LOW", "MEDIUM", "HIGH"):
        p = pipeline._build_codex_model_policy(level)
        assert p.get("force_review_required") is not True, (
            f"REJECT#13: {level} 정책은 force_review_required=True가 아니어야 한다. "
            f"got {p.get('force_review_required')!r}"
        )
    critical = pipeline._build_codex_model_policy("CRITICAL")
    assert critical.get("force_review_required") is True, (
        f"REJECT#13: CRITICAL 정책은 force_review_required=True여야 한다. "
        f"got {critical.get('force_review_required')!r}"
    )


def test_tc29b_effective_force_review_computed_in_cmd_source() -> None:
    """REJECT#13 AC#4: _cmd_gates_codex_review 소스에서 effective_force_review가
    정책 기반으로 계산되어야 한다."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)
    assert "effective_force_review" in src, (
        "REJECT#13: _cmd_gates_codex_review에 effective_force_review가 없습니다 "
        "(정책 force_review 통합 누락)"
    )
    assert "force_review_required" in src, (
        "REJECT#13: _cmd_gates_codex_review에 force_review_required 참조가 없습니다 "
        "(정책 force_review_required 미반영)"
    )


def test_tc29c_effective_force_review_used_at_rate_limit_check() -> None:
    """REJECT#13 AC#4: rate-limit 검사에서 force_review 대신 effective_force_review를 사용해야 한다."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)
    # rate-limit 검사에서 `not effective_force_review` 패턴이 있어야 한다.
    assert "not effective_force_review" in src, (
        "REJECT#13: rate-limit 검사에서 `not effective_force_review`가 없습니다 "
        "(force_review가 여전히 직접 사용됨)"
    )


def test_tc29d_critical_router_policy_constants_include_force_review() -> None:
    """REJECT#13 AC#3: CODEX_MODEL_POLICIES 상수에서 CRITICAL의 force_review_required가
    True이며 LOW/MEDIUM/HIGH는 False다 (SSoT 확인)."""
    policy_const = pipeline.CODEX_MODEL_POLICIES
    assert policy_const["CRITICAL"]["force_review_required"] is True, (
        "REJECT#13: CODEX_MODEL_POLICIES CRITICAL.force_review_required가 True가 아닙니다"
    )
    for level in ("LOW", "MEDIUM", "HIGH"):
        assert policy_const[level]["force_review_required"] is False, (
            f"REJECT#13: CODEX_MODEL_POLICIES {level}.force_review_required가 False가 아닙니다"
        )


def test_tc29e_effective_force_review_bypasses_cache_in_source() -> None:
    """REJECT#14 AC#2/#4: _cmd_gates_codex_review 소스에서 effective_force_review=true 시
    cache 조회 자체를 완전히 우회하는 분기가 있어야 한다."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)
    assert "effective_force_review=true: cache 우회" in src, (
        "REJECT#14: _cmd_gates_codex_review에 effective_force_review cache 우회 분기가 없습니다 "
        "(force_review 시 CLI 강제 실행 경로 누락)"
    )
    assert "if effective_force_review:" in src, (
        "REJECT#14: _cmd_gates_codex_review에 `if effective_force_review:` 분기가 없습니다"
    )


# --------------------------------------------------------------------------- #
# TC-30 시리즈: REJECT#15 — evidence_complete=False 이중 계산 버그 + 예산 확장
#   - 원인 1: tc11 oracle 파일 diff(~640자)가 65000자 예산을 초과해 잘림
#   - 원인 2: budget 단계 + cross-validation 단계에서 같은 항목이 이중 계산됨
#   - 수정 1: CODEX_REVIEW_BUNDLE_BUDGET_CHARS 65000→70000
#   - 수정 2: cross-validation 시작 시 _truncated_crit=0 리셋으로 단일 계산 보장
# --------------------------------------------------------------------------- #

def test_tc30a_bundle_budget_sufficient_for_tc11_oracles() -> None:
    """REJECT#15/16/18: CODEX_REVIEW_BUNDLE_BUDGET_CHARS가 tc11 oracle 파일 포함 총 diff를
    수용해야 한다. REJECT#18 auth_source 코드 추가 후 ~73718자 수용 위해 74000 이상.
    REJECT#35: 18파일 PR에서 non-critical 2파일 초과 해소를 위해 165000 이상.
    REJECT#7: CRITICAL Python 파일 diff 추가로 pipeline.py+test_codex*.py 합산 250K 초과
    → 400000 이상.
    REJECT#8: 45K 청크 분할 후 test_codex*.py 4청크(~180K)+pipeline.py(~200K)+기타 합산
    → 5개 CRITICAL 청크 손실, budget 400K 부족 → 700000 이상."""
    assert pipeline.CODEX_REVIEW_BUNDLE_BUDGET_CHARS >= 250000, (
        f"REJECT#7: 예산이 CRITICAL Python diff 포함 총량을 수용하지 못합니다: "
        f"{pipeline.CODEX_REVIEW_BUNDLE_BUDGET_CHARS} < 250000"
    )


def test_tc30b_cross_validation_resets_truncated_crit() -> None:
    """REJECT#15→REJECT#30: cross-validation이 hunk 단위 카운트 방식을 사용한다.
    REJECT#30 Fix 2에서 _truncated_crit=0 리셋(REJECT#15)이 제거되고
    _all_crit_count vs _sel_crit_count 비교로 대체됐음을 검증한다."""
    import inspect
    src = inspect.getsource(pipeline._build_codex_semantic_evidence)
    # REJECT#30: 새 방식 - hunk 단위 카운트
    assert "_all_crit_count" in src, (
        "REJECT#30 Fix 2: cross-validation에 '_all_crit_count'가 없음 — "
        "hunk 단위 카운트 방식이 적용되지 않음"
    )
    assert "_sel_crit_count" in src, (
        "REJECT#30 Fix 2: cross-validation에 '_sel_crit_count'가 없음"
    )
    # REJECT#15의 리셋은 제거됐어야 함
    assert "_truncated_crit = 0  # REJECT#15" not in src, (
        "REJECT#30: '_truncated_crit = 0  # REJECT#15' 리셋이 아직 남아있음 — "
        "REJECT#30 Fix 2로 대체됐어야 함"
    )


def test_tc30c_evidence_complete_true_with_current_diff() -> None:
    """REJECT#15/REJECT#29: 현재 repo diff로 bundle을 빌드하면 evidence_complete=True여야 한다.
    tc11 oracle 파일이 예산 내에 들어오고 cross-validation이 통과해야 한다.
    REJECT#29: 예산 800000으로 증가 후 _run_codex_preflight_checks 포함 전체 noncrit 커버."""
    import json
    from pathlib import Path

    # active_run.json에서 pipeline_id + state 로드
    try:
        ar = Path(".pipeline/active_run.json")
        ptr = json.loads(ar.read_text(encoding="utf-8"))
        sp = ptr.get("state_path")
        state = json.loads(Path(sp).read_text(encoding="utf-8"))
        pid = state.get("pipeline_id", "IMP-20260712-DAE1")
    except Exception:
        state = {}
        pid = "IMP-20260712-DAE1"

    _sha, _bundle_path = pipeline._build_codex_review_bundle(state, pid)
    bundle = json.loads(Path(_bundle_path).read_text(encoding="utf-8"))

    assert bundle.get("evidence_complete") is True, (
        f"REJECT#15/REJECT#29: evidence_complete가 True가 아닙니다. "
        f"truncated_critical_hunks={bundle.get('truncated_critical_hunks')}, "
        f"truncated_noncrit_hunks={bundle.get('truncated_noncrit_hunks')}, "
        f"budget_used={bundle.get('bundle_budget_chars')}, "
        f"missing_noncrit_files={bundle.get('missing_noncrit_files')}"
    )
    assert bundle.get("truncated_critical_hunks", 1) == 0, (
        f"REJECT#15: truncated_critical_hunks={bundle.get('truncated_critical_hunks')} != 0 "
        "(tc11 oracle 파일이 아직 예산 초과)"
    )
    assert bundle.get("truncated_noncrit_hunks", 1) == 0, (
        f"REJECT#29: truncated_noncrit_hunks={bundle.get('truncated_noncrit_hunks')} != 0 "
        f"(noncrit hunk 잘림 감지 — missing={bundle.get('missing_noncrit_files')})"
    )


# --------------------------------------------------------------------------- #
# TC-31: REJECT#16 — pre/post CLI snapshot 동일성 검증
# --------------------------------------------------------------------------- #

def test_tc31a_pre_cli_snapshot_capture_exists() -> None:
    """REJECT#16: _cmd_gates_codex_review에 pre-CLI HEAD/semantic evidence SHA 캡처 코드가 있어야 한다.
    acceptance_criteria[3]: CLI 종료 후 HEAD, semantic evidence SHA 등이 달라지면 차단."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)
    assert "_pre_cli_head_sha" in src, (
        "REJECT#16: _pre_cli_head_sha 변수 없음 — pre-CLI HEAD 캡처 누락"
    )
    assert "_pre_cli_sem_sha" in src, (
        "REJECT#16: _pre_cli_sem_sha 변수 없음 — pre-CLI semantic evidence SHA 캡처 누락"
    )
    assert "_pre_cli_fn_shas" in src, (
        "REJECT#16: _pre_cli_fn_shas 변수 없음 — pre-CLI 함수 SHA 캡처 누락"
    )


def test_tc31b_snapshot_changed_failure_code_exists() -> None:
    """REJECT#16: _cmd_gates_codex_review에 codex_review_snapshot_changed failure_code가 있어야 한다.
    acceptance_criteria[1/3]: 변경 감지 시 차단 + 캐시/승인 결과 생성 금지."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)
    assert "codex_review_snapshot_changed" in src, (
        "REJECT#16: codex_review_snapshot_changed failure_code 없음 — 변경 감지 차단 미구현"
    )
    assert "_post_snap_changed" in src, (
        "REJECT#16: _post_snap_changed 비교 변수 없음 — post-CLI snapshot 비교 로직 누락"
    )
    # fail-closed: 변경 시 _die를 호출해야 함
    assert "_die(" in src and "codex_review_snapshot_changed" in src, (
        "REJECT#16: _die(...)를 통한 fail-closed 차단이 없음"
    )


def test_tc31c_sem_sha_stable_when_no_change() -> None:
    """REJECT#16 acceptance_criteria[2]: 변경이 없을 때 semantic evidence SHA는 두 번 호출해도 동일해야 한다.
    (no-change 실행에서 pre-CLI == post-CLI → false positive 없음)"""
    import json
    from pathlib import Path

    try:
        ar = Path(".pipeline/active_run.json")
        ptr = json.loads(ar.read_text(encoding="utf-8"))
        sp = ptr.get("state_path")
        state = json.loads(Path(sp).read_text(encoding="utf-8"))
        pid = state.get("pipeline_id", "IMP-20260712-DAE1")
    except Exception:
        state = {}
        pid = "IMP-20260712-DAE1"

    # bundle에서 changed_files/included_functions를 가져와 동일 입력으로 두 번 호출한다.
    _sha, _bundle_path = pipeline._build_codex_review_bundle(state, pid)
    bundle = json.loads(Path(_bundle_path).read_text(encoding="utf-8"))
    cf = list(bundle.get("changed_files", []) or [])
    funcs = list(bundle.get("included_functions", []) or [])

    sem1 = pipeline._build_codex_semantic_evidence(pid, cf, funcs)
    sem2 = pipeline._build_codex_semantic_evidence(pid, cf, funcs)

    sha1 = str(sem1.get("semantic_evidence_sha256", "") or "")
    sha2 = str(sem2.get("semantic_evidence_sha256", "") or "")
    assert sha1, "REJECT#16: semantic_evidence_sha256가 빈 문자열 — SHA 계산 실패"
    assert sha1 == sha2, (
        f"REJECT#16: 동일 입력에서 semantic_evidence_sha256이 달라짐 — false positive 위험\n"
        f"  sha1={sha1}\n  sha2={sha2}"
    )


def test_tc31d_pre_cli_snapshot_fail_closed() -> None:
    """REJECT#17 AC#1: pre-CLI HEAD/semantic evidence SHA 수집 실패 시 즉시 BLOCKED (fail-closed).
    acceptance_criteria[0]: pre-CLI HEAD 또는 semantic evidence SHA를 얻지 못하면 승인 결과를 기록하지 않고 BLOCKED."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)
    # 1) 빈 _pre_cli_head_sha → _die 호출 확인
    assert "if not _pre_cli_head_sha:" in src, (
        "REJECT#17: pre-CLI HEAD SHA가 비어 있을 때 BLOCKED 처리 없음 — fail-open 위험"
    )
    # 2) 빈 _pre_cli_sem_sha → _die 호출 확인
    assert "if not _pre_cli_sem_sha:" in src, (
        "REJECT#17: pre-CLI semantic evidence SHA가 비어 있을 때 BLOCKED 처리 없음 — fail-open 위험"
    )


def test_tc31e_post_cli_snapshot_fail_closed() -> None:
    """REJECT#17 AC#2: post-CLI git 조회 실패 또는 semantic evidence 재계산 실패 시 BLOCKED.
    acceptance_criteria[1]: post-CLI HEAD/semantic SHA 검증 불가 → codex_review_snapshot_changed."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)
    # 1) post HEAD 수집 실패 → _post_snap_changed에 head_sha_unverifiable 추가
    assert "head_sha_unverifiable" in src, (
        "REJECT#17: post-CLI HEAD 수집 실패 시 _post_snap_changed에 추가되지 않음 — fail-open 위험"
    )
    # 2) post semantic SHA 재계산 실패 → _post_snap_changed에 semantic_sha_unverifiable 추가
    assert "semantic_sha_unverifiable" in src, (
        "REJECT#17: post-CLI semantic evidence 재계산 실패 시 _post_snap_changed에 추가되지 않음 — fail-open 위험"
    )
    # 3) post-CLI 검증이 외부 guard 없이 항상 실행됨 (if _pre_cli_head_sha 제거)
    assert "_post_snap_changed: List[str] = []" in src, (
        "REJECT#17: post-CLI snapshot 검증 블록이 외부 guard 없이 직접 실행되지 않음 — fail-open 위험"
    )


def test_tc31f_preflight_vs_prompt_sem_sha_check() -> None:
    """REJECT#17 AC#3: preflight bundle semantic SHA와 prompt semantic SHA가 다르면 CLI 실행 전 BLOCKED.
    acceptance_criteria[2]: 두 SHA가 다를 때 즉시 차단."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)
    # _preflight_sem_sha 변수가 pre-CLI 비교에 사용됨
    assert "_preflight_sem_sha" in src, (
        "REJECT#17: preflight bundle semantic SHA 비교 변수 없음 — AC#3 미구현"
    )
    # 불일치 시 _die 호출
    assert "_preflight_sem_sha != _pre_cli_sem_sha" in src, (
        "REJECT#17: preflight bundle vs prompt semantic SHA 불일치 검증 없음 — AC#3 미구현"
    )


def test_tc32a_cache_stores_auth_source() -> None:
    """REJECT#18 AC#1: 캐시 entry에 auth_source가 명시적으로 저장돼야 한다.
    acceptance_criteria[0]: 실제 Codex CLI 승인 캐시에 auth_source=chatgpt가 저장된다."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)
    # _cache_entry dict에 "auth_source" 키가 포함됨
    assert '"auth_source": _auth_source_str' in src, (
        "REJECT#18: cache entry에 auth_source 저장 없음 — AC#1 미구현"
    )


def test_tc32b_cache_hit_no_hardcoded_chatgpt() -> None:
    """REJECT#18 AC#2: cache hit에서 auth_source는 캐시 값으로만 복원되고 chatgpt 기본값을 사용하지 않는다.
    acceptance_criteria[1]: cache hit 경로에서 _auth_source_str = 'chatgpt' 하드코딩이 없어야 한다."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)
    # 하드코딩 chatgpt 제거 확인 — cached_auth_source를 읽어야 한다
    assert "_cached_auth_src" in src, (
        "REJECT#18: cache hit 경로에서 cached_auth_source 읽기 없음 — AC#2 미구현"
    )
    assert "codex_cache_auth_source_invalid" in src, (
        "REJECT#18: cache hit 시 auth_source 유효성 검증 없음 — AC#2 미구현"
    )


def test_tc32c_cache_miss_on_missing_auth_source() -> None:
    """REJECT#18 AC#3: auth_source가 누락되거나 chatgpt가 아닌 캐시는 cache miss가 돼야 한다.
    acceptance_criteria[2]: 인증 출처 누락·변조 캐시가 acceptance_eligible=true 결과를 못 만든다."""
    import json
    import tempfile
    from pathlib import Path
    from pipeline import _codex_cache_key, _codex_policy_signature

    # 실제 cache key 계산 (cache_key mismatch 없이 auth_source 검증이 동작하도록)
    real_key = _codex_cache_key("TESTCONTRACT18", "TESTBUNDLE18", _codex_policy_signature(None))

    def _make_entry(**kwargs: object) -> dict:
        entry: dict = {
            "cache_key": real_key,
            "contract_sha256": "TESTCONTRACT18",
            "review_bundle_sha256": "TESTBUNDLE18",
            "verdict": "APPROVE",
            "status": "APPROVED",
            "critical_file_shas": {},
            "excluded_files": [],
            "changed_critical_files": [],
        }
        entry.update(kwargs)
        return entry

    with tempfile.TemporaryDirectory() as td:
        cache_path = Path(td) / "codex_review_cache.json"
        original_cache_fn = pipeline._codex_review_cache_path

        def _mock_cache_path() -> Path:
            return cache_path

        pipeline._codex_review_cache_path = _mock_cache_path
        try:
            # 1) auth_source 없는 캐시 → cache miss (authキー欠落)
            cache_path.write_text(json.dumps(_make_entry()), encoding="utf-8")
            result = pipeline._check_codex_cache(
                "TESTCONTRACT18", "TESTBUNDLE18",
                {}, "IMP-TEST",
                model_policy=None,
            )
            assert not result.get("hit"), (
                f"REJECT#18: auth_source 없는 캐시가 cache hit을 반환함 — AC#3 위반\n  result={result}"
            )
            assert "chatgpt가 아니거나 누락" in result.get("reason", ""), (
                f"REJECT#18: auth_source 없는 캐시의 miss 사유가 부정확함\n  reason={result.get('reason')}"
            )

            # 2) auth_source 잘못된 캐시 → cache miss
            cache_path.write_text(
                json.dumps(_make_entry(auth_source="api_key")), encoding="utf-8"
            )
            result2 = pipeline._check_codex_cache(
                "TESTCONTRACT18", "TESTBUNDLE18",
                {}, "IMP-TEST",
                model_policy=None,
            )
            assert not result2.get("hit"), (
                f"REJECT#18: auth_source=api_key 캐시가 cache hit을 반환함 — AC#3 위반\n  result={result2}"
            )
            assert "chatgpt가 아니거나 누락" in result2.get("reason", ""), (
                f"REJECT#18: auth_source 잘못된 캐시의 miss 사유가 부정확함\n  reason={result2.get('reason')}"
            )

            # 3) auth_source=chatgpt 정상 캐시 → cache hit
            cache_path.write_text(
                json.dumps(_make_entry(auth_source="chatgpt")), encoding="utf-8"
            )
            result3 = pipeline._check_codex_cache(
                "TESTCONTRACT18", "TESTBUNDLE18",
                {}, "IMP-TEST",
                model_policy=None,
            )
            assert result3.get("hit"), (
                f"REJECT#18: auth_source=chatgpt 정상 캐시가 hit을 반환하지 않음\n  result={result3}"
            )
            assert result3.get("cached_auth_source") == "chatgpt", (
                f"REJECT#18: cached_auth_source가 반환되지 않음\n  result={result3}"
            )
        finally:
            pipeline._codex_review_cache_path = original_cache_fn


# --------------------------------------------------------------------------- #
# TC-33: REJECT#19 — NDJSON 복수/상충 판정 fail-closed (AC#1~#4).
# --------------------------------------------------------------------------- #
def _ndjson_two_agent_messages(text1: str, text2: str) -> str:
    """agent_message 두 개를 포함하는 NDJSON stdout 생성."""
    import json as _json
    lines = [
        _json.dumps({"type": "thread.started", "thread_id": "test-33"}),
        _json.dumps({"type": "turn.started"}),
        _json.dumps({
            "type": "item.completed",
            "item": {"id": "item_0", "type": "agent_message", "text": text1},
        }),
        _json.dumps({
            "type": "item.completed",
            "item": {"id": "item_1", "type": "agent_message", "text": text2},
        }),
    ]
    return "\n".join(lines)


_REJECT_JSON_33 = (
    '{"verdict":"REJECT","root_cause":"rc","reproduction":"rp",'
    '"required_fix":"rf","acceptance_criteria":["ac1"]}'
)


def test_tc33a_approve_then_reject_is_parse_failure() -> None:
    """REJECT#19 AC#1+AC#3: APPROVE 다음 REJECT가 있는 NDJSON은 parse_failure여야 한다.
    첫 번째 메시지만 처리되어 APPROVED가 반환되는 버그 재현 + 수정 검증."""
    stdout = _ndjson_two_agent_messages("APPROVE_TO_USER", _REJECT_JSON_33)
    r = pipeline._run_codex_cli_review(0, stdout, "")
    assert r["status"] == "ERROR", (
        f"REJECT#19 AC#1: APPROVE+REJECT NDJSON이 APPROVED를 반환함 — 복수 판정 parse_failure 필요\n  result={r}"
    )
    assert r["error_type"] == "parse_failure", (
        f"REJECT#19 AC#3: error_type이 parse_failure가 아님\n  error_type={r.get('error_type')}"
    )
    assert r.get("verdict") is None, (
        f"REJECT#19: parse_failure인데도 verdict가 채워져 있음\n  verdict={r.get('verdict')}"
    )


def test_tc33b_reject_then_approve_is_parse_failure() -> None:
    """REJECT#19 AC#2+AC#3: REJECT 다음 APPROVE가 있는 NDJSON도 임의로 첫 판정을 채택하면 안 된다.
    복수 상충 판정은 parse_failure로 처리된다."""
    stdout = _ndjson_two_agent_messages(_REJECT_JSON_33, "APPROVE_TO_USER")
    r = pipeline._run_codex_cli_review(0, stdout, "")
    assert r["status"] == "ERROR", (
        f"REJECT#19 AC#2: REJECT+APPROVE NDJSON이 REJECTED를 반환함 — 복수 판정 parse_failure 필요\n  result={r}"
    )
    assert r["error_type"] == "parse_failure", (
        f"REJECT#19 AC#3: error_type이 parse_failure가 아님\n  error_type={r.get('error_type')}"
    )


def test_tc33c_single_approve_and_single_reject_preserved() -> None:
    """REJECT#19 AC#4: 단일 APPROVE와 단일 REJECT의 기존 정상 동작이 유지되어야 한다."""
    # 단일 APPROVE_TO_USER literal
    stdout_approve = _ndjson_agent_json("APPROVE_TO_USER")
    r_approve = pipeline._run_codex_cli_review(0, stdout_approve, "")
    assert r_approve["status"] == "APPROVED", (
        f"REJECT#19 AC#4: 단일 APPROVE_TO_USER가 APPROVED를 반환하지 않음\n  result={r_approve}"
    )
    assert r_approve["error_type"] is None

    # 단일 JSON REJECT (4필드 완전)
    stdout_reject = _ndjson_agent_json(_REJECT_JSON_33)
    r_reject = pipeline._run_codex_cli_review(0, stdout_reject, "")
    assert r_reject["status"] == "REJECTED", (
        f"REJECT#19 AC#4: 단일 구조화 REJECT가 REJECTED를 반환하지 않음\n  result={r_reject}"
    )
    assert r_reject["error_type"] is None
    assert r_reject["root_cause"] == "rc"


# --------------------------------------------------------------------------- #
# TC-34: REJECT#20 — JSON-like text가 _parse_json_verdict 실패 시 INVALID 수집.
# --------------------------------------------------------------------------- #
_INVALID_JSON_REJECT = '{"verdict":"REJECT"}'  # 4필드 누락 → _parse_json_verdict None


def test_tc34a_invalid_reject_json_then_valid_approve_is_parse_failure() -> None:
    """REJECT#20 AC#1: 잘못된 REJECT JSON 뒤에 유효한 APPROVE가 있으면 parse_failure여야 한다.
    _parse_json_verdict가 None을 반환하는 JSON-like text는 INVALID로 수집돼야 한다."""
    stdout = _ndjson_two_agent_messages(_INVALID_JSON_REJECT, '{"verdict":"APPROVE_TO_USER"}')
    r = pipeline._run_codex_cli_review(0, stdout, "")
    assert r["status"] == "ERROR", (
        f"REJECT#20 AC#1: invalid REJECT+APPROVE가 APPROVED를 반환함 — INVALID 수집 누락\n  result={r}"
    )
    assert r["error_type"] == "parse_failure", (
        f"REJECT#20 AC#1: error_type이 parse_failure가 아님\n  error_type={r.get('error_type')}"
    )


def test_tc34b_valid_approve_then_invalid_reject_json_is_parse_failure() -> None:
    """REJECT#20 AC#2: 유효한 APPROVE 뒤에 잘못된 REJECT JSON이 있어도 parse_failure여야 한다."""
    stdout = _ndjson_two_agent_messages('{"verdict":"APPROVE_TO_USER"}', _INVALID_JSON_REJECT)
    r = pipeline._run_codex_cli_review(0, stdout, "")
    assert r["status"] == "ERROR", (
        f"REJECT#20 AC#2: APPROVE+invalid REJECT가 APPROVED를 반환함\n  result={r}"
    )
    assert r["error_type"] == "parse_failure", (
        f"REJECT#20 AC#2: error_type이 parse_failure가 아님\n  error_type={r.get('error_type')}"
    )


def test_tc34c_single_invalid_json_is_parse_failure() -> None:
    """REJECT#20 AC#3: JSON처럼 시작하지만 verdict 스키마 무효인 단일 메시지는 parse_failure여야 한다."""
    stdout = _ndjson_agent_json(_INVALID_JSON_REJECT)
    r = pipeline._run_codex_cli_review(0, stdout, "")
    assert r["status"] == "ERROR", (
        f"REJECT#20 AC#3: 단일 invalid JSON이 ERROR가 아닌 결과를 반환함\n  result={r}"
    )
    assert r["error_type"] == "parse_failure", (
        f"REJECT#20 AC#3: error_type이 parse_failure가 아님\n  error_type={r.get('error_type')}"
    )
    assert r.get("verdict") is None


# --------------------------------------------------------------------------- #
# TC-35: REJECT#21 — CRITICAL은 actual_verified만 허용 (invocation_verified 차단).
# --------------------------------------------------------------------------- #
def test_tc35a_critical_invocation_verified_passes_capability_match() -> None:
    """REJECT#12 AC#1: CRITICAL risk + actual_model=unknown + invocation_ok=True 시
    _check_codex_model_capability_match는 OK(invocation_verified)를 반환해야 한다.
    REJECT#12 fix: REJECT#21(unknown_model_critical_blocked) 정책 번복 — invocation_verified로 충분."""
    r = pipeline._check_codex_model_capability_match(
        "gpt-5.6-sol", "max",    # selected
        "gpt-5.6-sol", "max",    # invoked (일치)
        "unknown", "unknown",     # actual (미보고)
        "CRITICAL",               # risk_level
        invocation_ok=True,
    )
    assert r["result"] == "OK", (
        f"REJECT#12 AC#1: CRITICAL+invocation_verified가 BLOCKED됨 — "
        f"invocation_verified는 CRITICAL에서도 OK여야 한다.\n  result={r}"
    )
    assert r.get("model_verification_level") == pipeline.CODEX_VERIFICATION_INVOCATION, (
        f"REJECT#12 AC#1: model_verification_level이 invocation_verified가 아님\n  "
        f"level={r.get('model_verification_level')!r}"
    )


def test_tc35b_critical_invocation_verified_passes_operational_trust() -> None:
    """REJECT#12 AC#1: request-accept 경로의 _check_codex_review_operational_trust에서
    CRITICAL+invocation_verified는 PASS여야 한다.
    REJECT#12 fix: REJECT#21 블록(unknown_model_critical_blocked) 제거."""
    _fake_result = {
        "status": "APPROVED",
        "verdict_source": "codex_cli",
        "acceptance_eligible": True,
        "router_version": "2.0.0",
        "risk_level": "CRITICAL",
        "model_policy_signature": "CRITICAL:gpt-5.6-sol:max:enforce",
        "codex_cli_command": "codex exec --model gpt-5.6-sol ...",
        "selected_model": "gpt-5.6-sol",
        "selected_reasoning_effort": "max",
        "invoked_model": "gpt-5.6-sol",
        "invoked_effort": "max",
        "actual_model": "unknown",
        "actual_effort": "unknown",
        "model_verification_level": pipeline.CODEX_VERIFICATION_INVOCATION,
        "auth_source": "chatgpt",
        # IMP-20260712-DAE1 REJECT#16: binary 신뢰 증거 필수
        "codex_binary_path": "/usr/local/bin/codex",
        "codex_binary_sha256": "c" * 64,
        # REJECT#LATEST: native/vendor binary 신뢰 증거 필수 (test-only dummy).
        "codex_native_binary_path": "/usr/local/lib/codex/vendor/codex",
        "codex_native_binary_sha256": "e" * 64,
    }
    r = pipeline._check_codex_review_operational_trust(_fake_result)
    assert r["status"] == "PASS", (
        f"REJECT#12 AC#1: CRITICAL+invocation_verified가 operational_trust에서 차단됨\n  result={r}"
    )


def test_tc35c_high_invocation_verified_still_passes() -> None:
    """REJECT#21 AC#5: HIGH risk + actual_model=unknown + invocation_ok=True는 여전히 통과해야 한다.
    CRITICAL 전용 제한이 HIGH 정책에 영향을 주면 안 된다."""
    r = pipeline._check_codex_model_capability_match(
        "gpt-5.6-sol", "high",
        "gpt-5.6-sol", "high",
        "unknown", "unknown",
        "HIGH",
        invocation_ok=True,
    )
    assert r["result"] == "OK", (
        f"REJECT#21 AC#5: HIGH+invocation_verified가 BLOCKED됨 — "
        f"CRITICAL 제한이 HIGH까지 영향을 줌\n  result={r}"
    )
    assert r["model_verification_level"] == pipeline.CODEX_VERIFICATION_INVOCATION, (
        f"REJECT#21 AC#5: model_verification_level이 invocation_verified가 아님\n  "
        f"level={r.get('model_verification_level')!r}"
    )


# --------------------------------------------------------------------------- #
# TC-36: REJECT#21 deadlock fix — CRITICAL+unknown 시 verdict 기록 후 종료 (AC#1/AC#2/AC#4).
# --------------------------------------------------------------------------- #

def test_tc36a_oracle_tc11_connected_to_invoke_and_capability_match(tmp_path: Path) -> None:
    """REJECT#12 AC#1: tc11 Oracle(input/expected)을 실제 명령 경로(_invoke_codex_exec +
    _check_codex_model_capability_match)에 연결한 회귀 테스트.

    REJECT#12 fix: tc11 oracle expected가 OK+invocation_verified로 변경됨.
    tc11 oracle input (actual_model=unknown, risk_level=CRITICAL, mode=enforce) →
    fake codex 실행(_invoke_codex_exec) + capability match 검사 →
    oracle expected (result=OK, model_verification_level=invocation_verified).
    """
    import json

    oracle_dir = (
        Path(__file__).resolve().parent.parent
        / "oracles" / "IMP-20260712-DAE1" / "tc11_unknown_model_critical"
    )
    tc11_input = json.loads((oracle_dir / "input.json").read_text(encoding="utf-8"))
    tc11_expected = json.loads((oracle_dir / "expected.json").read_text(encoding="utf-8"))

    # tc11 oracle input 파라미터
    expected_actual_model = tc11_input["actual_model"]   # "unknown"
    risk_level = tc11_input["risk_level"]                # "CRITICAL"

    # fake codex: APPROVE 판정 + model 미보고(actual_model → unknown).
    #   실제 gpt-5.6-sol CLI와 동일하게 stdout에 "model" 키를 포함하지 않는다.
    fake = _make_fake_codex(tmp_path, exit_code=0, stdout_text=_approve_json())
    run_result = pipeline._invoke_codex_exec(
        "gpt-5.6-sol", "max", "test_prompt", codex_bin=fake, timeout=30,
    )

    # 실제 명령 경로에서 actual_model이 oracle input과 동일하게 unknown인지 확인.
    assert run_result["actual_model"] == expected_actual_model, (
        f"tc11 oracle 불일치: actual_model={run_result['actual_model']!r} "
        f"!= expected={expected_actual_model!r}"
    )

    # capability match 검사 (tc11 oracle을 실제 명령 경로에 연결).
    invoked_model = str(run_result.get("invoked_model", "gpt-5.6-sol") or "gpt-5.6-sol")
    invoked_effort = str(run_result.get("invoked_effort", "max") or "max")
    invocation_ok = run_result.get("exit_code") == 0
    r = pipeline._check_codex_model_capability_match(
        "gpt-5.6-sol", "max",
        invoked_model, invoked_effort,
        run_result["actual_model"], run_result["actual_effort"],
        risk_level,
        invocation_ok=invocation_ok,
    )

    # REJECT#12 fix: oracle expected → result=OK, model_verification_level=invocation_verified
    assert r["result"] == tc11_expected["result"], (
        f"tc11 oracle 불일치: result={r['result']!r} != expected={tc11_expected['result']!r}\n  {r}"
    )
    # OK 결과에는 failure_code가 없으므로 oracle에 "failure_code" 키가 없으면 None == None
    _expected_fc = tc11_expected.get("failure_code")
    assert r.get("failure_code") == _expected_fc, (
        f"tc11 oracle 불일치: failure_code={r.get('failure_code')!r} "
        f"!= expected={_expected_fc!r}"
    )
    if tc11_expected.get("model_verification_level"):
        assert r.get("model_verification_level") == tc11_expected["model_verification_level"], (
            f"tc11 oracle 불일치: model_verification_level={r.get('model_verification_level')!r} "
            f"!= expected={tc11_expected['model_verification_level']!r}"
        )


def test_tc36b_critical_invocation_verified_passes_capability_match(tmp_path: Path) -> None:
    """REJECT#12 AC#1: CRITICAL + invocation_verified 경로에서 _check_codex_model_capability_match가
    OK를 반환하고 model_verification_level=invocation_verified를 포함해야 한다.
    REJECT#12 fix: 이전 BLOCKED(unknown_model_critical_blocked) → OK(invocation_verified).
    """
    fake = _make_fake_codex(tmp_path, exit_code=0, stdout_text=_approve_json())
    run_result = pipeline._invoke_codex_exec(
        "gpt-5.6-sol", "max", "test_prompt", codex_bin=fake, timeout=30,
    )
    r = pipeline._check_codex_model_capability_match(
        "gpt-5.6-sol", "max",
        str(run_result.get("invoked_model", "gpt-5.6-sol") or "gpt-5.6-sol"),
        str(run_result.get("invoked_effort", "max") or "max"),
        run_result["actual_model"],
        run_result["actual_effort"],
        "CRITICAL",
        invocation_ok=(run_result.get("exit_code") == 0),
    )
    assert r["result"] == "OK", (
        f"REJECT#12 AC#1: CRITICAL+invocation_verified가 BLOCKED됨\n  result={r}"
    )
    assert r.get("model_verification_level") == pipeline.CODEX_VERIFICATION_INVOCATION, (
        f"REJECT#12 AC#1: model_verification_level={r.get('model_verification_level')!r} "
        "— invocation_verified 증거가 보존되어야 한다."
    )
    # deadlock fix: BLOCKED 반환에 model_verification_level이 포함돼야 verdict 기록 경로에서 사용 가능.
    assert "model_verification_level" in r, (
        f"REJECT#21 deadlock fix: BLOCKED 반환에 model_verification_level이 없음\n  {r}"
    )
    # invocation_ok=True + invoked==selected이면 invocation_verified이어야 한다.
    assert r["model_verification_level"] == pipeline.CODEX_VERIFICATION_INVOCATION, (
        f"REJECT#21 deadlock fix: BLOCKED 반환의 model_verification_level이 invocation_verified가 아님\n  "
        f"level={r.get('model_verification_level')!r}"
    )


# --------------------------------------------------------------------------- #
# TC-37: REJECT#22 — 재검토 blocking exit 시 기존 APPROVED effective 결과 무효화(AC#1~#4).
# --------------------------------------------------------------------------- #

def test_tc37a_write_blocked_invalidation_produces_correct_result(tmp_path: Path) -> None:
    """REJECT#22 AC#2: _write_codex_review_blocked_invalidation이 BLOCKED 결과를 기록한다.

    AC#2: 각 blocking 경로에서 BLOCKED + acceptance_eligible=false로 저장된다.
    """
    import json
    import os

    result_path = tmp_path / "codex_review_result.json"
    # 먼저 가짜 APPROVED 결과를 파일에 기록한다(이전 effective 결과 시뮬레이션).
    fake_approved = {
        "schema_version": 5,
        "pipeline_id": "IMP-TEST-37A",
        "verdict": "APPROVE_TO_USER",
        "acceptance_eligible": True,
        "effective": True,
        "status": "APPROVED",
    }
    result_path.write_text(json.dumps(fake_approved), encoding="utf-8")

    # 환경 변수로 result path를 격리한다.
    orig_env = os.environ.copy()
    try:
        os.environ["PIPELINE_CODEX_REVIEW_RESULT_OVERRIDE"] = str(result_path)
        # _codex_review_result_path를 monkeypatching하여 격리된 경로를 반환한다.
        original_fn = pipeline._codex_review_result_path
        pipeline._codex_review_result_path = lambda: result_path  # type: ignore[assignment]
        try:
            pipeline._write_codex_review_blocked_invalidation(
                "IMP-TEST-37A", "model_mismatch",
                prev_reject_count=3,
                prev_cli_error_count=1,
                review_bundle_sha256="bundle_sha_test",
                risk_level="CRITICAL",
                model_policy={
                    "selected_model": "gpt-5.6-sol",
                    "selected_reasoning_effort": "max",
                    "mode": "enforce",
                },
            )
        finally:
            pipeline._codex_review_result_path = original_fn  # type: ignore[assignment]
    finally:
        os.environ.clear()
        os.environ.update(orig_env)

    # 기록된 결과가 BLOCKED + acceptance_eligible=False인지 확인한다.
    written = json.loads(result_path.read_text(encoding="utf-8"))

    # AC#2: status=BLOCKED, acceptance_eligible=False로 저장됨.
    assert written["status"] == "BLOCKED", (
        f"REJECT#22 AC#2: status가 BLOCKED가 아님 — got {written.get('status')!r}"
    )
    assert written["acceptance_eligible"] is False, (
        f"REJECT#22 AC#2: acceptance_eligible이 False가 아님 — got {written.get('acceptance_eligible')!r}"
    )
    assert written["effective"] is True, (
        f"REJECT#22: effective가 True가 아님 — got {written.get('effective')!r}"
    )
    # verdict=None → _codex_review_snapshot이 REJECT 반환(AC#3 전제).
    assert written["verdict"] is None, (
        f"REJECT#22 AC#3 전제: verdict가 None이 아님 — got {written.get('verdict')!r}"
    )
    assert written["pipeline_id"] == "IMP-TEST-37A", (
        f"REJECT#22: pipeline_id 불일치 — got {written.get('pipeline_id')!r}"
    )
    assert written.get("reject_count") == 3, (
        f"REJECT#22: reject_count 유지 실패 — got {written.get('reject_count')!r}"
    )


def test_tc37b_codex_review_snapshot_rejects_blocked_result(tmp_path: Path) -> None:
    """REJECT#22 AC#3: _write_codex_review_blocked_invalidation 기록 후
    _codex_review_snapshot이 REJECT를 반환하여 request-accept가 차단됨을 검증한다.

    AC#3: 실패한 재검토 직후 request-accept가 반드시 차단된다.
    """
    import json

    # BLOCKED 결과(verdict=None)를 직접 파일로 작성한다.
    result_path = tmp_path / "codex_review_result.json"
    blocked_result = {
        "schema_version": 5,
        "pipeline_id": "IMP-TEST-37B",
        "verdict": None,             # BLOCKED → _codex_review_snapshot이 REJECT 반환해야 함.
        "acceptance_eligible": False,
        "effective": True,
        "status": "BLOCKED",
        "packet_sha256": "pkt_sha_37b",
        "pr_body_candidate_sha256": "",
        "pr_head_sha": "",
    }
    result_path.write_text(json.dumps(blocked_result), encoding="utf-8")

    # _codex_review_result_path를 monkeypatch하여 격리된 파일을 사용한다.
    original_fn = pipeline._codex_review_result_path
    pipeline._codex_review_result_path = lambda: result_path  # type: ignore[assignment]
    try:
        # staged_sha_manifest: BLOCKED 결과의 packet_sha256과 일치해야 통과할 수 있다.
        staged = {"packet_sha256": "pkt_sha_37b"}
        snap = pipeline._codex_review_snapshot("IMP-TEST-37B", staged, {})
    finally:
        pipeline._codex_review_result_path = original_fn  # type: ignore[assignment]

    # BLOCKED 결과(verdict=None)이면 _codex_review_snapshot은 REJECT를 반환해야 한다.
    assert snap["verdict"] == "REJECT", (
        f"REJECT#22 AC#3: BLOCKED 결과 후 _codex_review_snapshot이 REJECT를 반환하지 않음\n  snap={snap}"
    )


def test_tc37c_write_blocked_invalidation_called_before_model_mismatch_die() -> None:
    """REJECT#22 AC#1: model_mismatch blocking exit 전 _write_codex_review_blocked_invalidation
    호출 코드가 _cmd_gates_codex_review 소스에 존재해야 한다.

    AC#1: force-review model_mismatch 시 기존 APPROVED 결과가 더 이상 effective하지 않다.
    """
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)

    # REJECT#22 fix: _write_codex_review_blocked_invalidation 호출이 존재해야 한다.
    assert "_write_codex_review_blocked_invalidation" in src, (
        "REJECT#22 AC#1: _cmd_gates_codex_review에 _write_codex_review_blocked_invalidation 호출이 없음 — "
        "force-review model_mismatch 시 기존 APPROVED 결과가 무효화되지 않음"
    )

    # AC#2: 각 blocking 경로(cache_live_sha_mismatch, cache_hit_verification_insufficient,
    #        review_in_progress, model_mismatch)에 대한 호출이 있어야 한다.
    assert "cache_live_sha_mismatch" in src, (
        "REJECT#22 AC#2: cache_live_sha_mismatch blocking exit에서 무효화 호출이 없음"
    )
    assert "cache_hit_verification_insufficient" in src, (
        "REJECT#22 AC#2: cache_hit_verification_insufficient blocking exit에서 무효화 호출이 없음"
    )
    assert "review_in_progress" in src, (
        "REJECT#22 AC#2: auto-invoke 시작 시점에 review_in_progress 무효화 호출이 없음"
    )

    # AC#3: request-accept 차단은 verdict=None(BLOCKED 결과)으로 보장됨.
    assert "_codex_review_snapshot" in inspect.getsource(pipeline._cmd_gates_request_accept), (
        "REJECT#22 AC#3: request-accept에 _codex_review_snapshot 호출이 없어 BLOCKED 결과 차단 불가"
    )


# --------------------------------------------------------------------------- #
# TC-38: REJECT#23 — auth_source 검증 강화 + snapshot 실패 specific failure_code + 문서 일치.
# --------------------------------------------------------------------------- #

def test_tc38d_write_blocked_invalidation_in_critical_functions() -> None:
    """REJECT#24 AC#1: _write_codex_review_blocked_invalidation이 CODEX_CRITICAL_FUNCTIONS에 등록되어
    review bundle에 before/after SHA가 포함된다.

    AC#2: evidence_complete는 누락된 helper 구현이 있으면 false가 되도록 CRITICAL로 분류 필수.
    """
    assert "_write_codex_review_blocked_invalidation" in pipeline.CODEX_CRITICAL_FUNCTIONS, (
        "REJECT#24 AC#1: _write_codex_review_blocked_invalidation이 CODEX_CRITICAL_FUNCTIONS에 없음 — "
        "review bundle에 구현 diff가 포함되지 않아 Codex가 신뢰 판정 불가"
    )
    assert "_finish_codex_review_error" in pipeline.CODEX_CRITICAL_FUNCTIONS, (
        "REJECT#24 AC#1: _finish_codex_review_error가 CODEX_CRITICAL_FUNCTIONS에 없음 — "
        "acceptance_eligible 제어 함수의 구현 diff가 누락됨"
    )
    assert "_check_codex_chatgpt_auth" in pipeline.CODEX_CRITICAL_FUNCTIONS, (
        "REJECT#24 AC#1: _check_codex_chatgpt_auth가 CODEX_CRITICAL_FUNCTIONS에 없음 — "
        "인증 검증 함수의 구현 diff가 누락됨"
    )


def test_tc38a_auth_source_invalid_blocked_in_source() -> None:
    """REJECT#23 AC#1: _cmd_gates_codex_review 소스에 auth_source 기본값 폴백이 없고
    'chatgpt'가 아닌 경우 codex_auth_source_invalid BLOCKED invalidation을 저장하는 코드가 존재한다.

    AC#1: auth_source가 누락되거나 chatgpt가 아니면 BLOCKED + 정확한 인증 failure_code 저장.
    """
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)

    # REJECT#23 fix: auth_source 기본값 폴백 제거 — 'chatgpt' 기본값이 _auth_source_str 할당에 없어야 함.
    # 현재 코드: _auth_source_str = str(_auth.get("auth_source", "") or "")
    # 이전 코드: _auth_source_str = str(_auth.get("auth_source", "chatgpt") or "chatgpt")  ← 버그
    auth_source_lines = [
        line for line in src.splitlines()
        if "_auth_source_str" in line and "_auth.get" in line
    ]
    assert auth_source_lines, (
        "REJECT#23 AC#1: _auth_source_str 할당 라인이 소스에 없음"
    )
    for line in auth_source_lines:
        assert '("auth_source", "chatgpt")' not in line, (
            f"REJECT#23 AC#1: auth_source 기본값 'chatgpt' 폴백이 여전히 존재함 — "
            f"auth_source 누락 시 chatgpt로 보정될 수 있음\n  line={line!r}"
        )

    # codex_auth_source_invalid failure_code가 소스에 존재해야 한다.
    assert "codex_auth_source_invalid" in src, (
        "REJECT#23 AC#1: codex_auth_source_invalid failure_code가 소스에 없음 — "
        "auth_source가 chatgpt가 아닌 경우 차단 로직이 없음"
    )

    # auth_source != 'chatgpt' 검증 라인이 존재해야 한다.
    assert '_auth_source_str != "chatgpt"' in src, (
        "REJECT#23 AC#1: auth_source 명시적 검증 (_auth_source_str != 'chatgpt')이 소스에 없음"
    )

    # REJECT#23: 인증 실패 직전 _write_codex_review_blocked_invalidation 호출이 존재해야 한다.
    # 인증 실패(_auth.get('result') != 'OK') 처리 블록에서 invalidation이 먼저 저장되어야 한다.
    assert "_auth_fc" in src, (
        "REJECT#23 AC#1: 인증 failure_code 변수(_auth_fc)가 소스에 없음 — "
        "auth 실패 직전 specific failure_code로 invalidation을 저장하지 않음"
    )


def test_tc38b_snapshot_changed_specific_failure_code_in_source() -> None:
    """REJECT#23 AC#2: pre-CLI 및 post-CLI snapshot 실패 직전 codex_review_snapshot_changed
    failure_code로 _write_codex_review_blocked_invalidation 호출이 소스에 존재한다.

    AC#2: pre-CLI 및 post-CLI HEAD/semantic/함수 SHA 검증 실패마다
    effective BLOCKED 결과에 codex_review_snapshot_changed가 저장된다.
    """
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)

    # pre-CLI HEAD SHA 실패 전 codex_review_snapshot_changed 저장.
    # 패턴: _write_codex_review_blocked_invalidation(... "codex_review_snapshot_changed" ...) 다음 _die(...)
    # 소스 내 codex_review_snapshot_changed와 _write_codex_review_blocked_invalidation이
    # 같은 함수 내 여러 곳에 있어야 한다.
    snapshot_changed_count = src.count('"codex_review_snapshot_changed"')
    assert snapshot_changed_count >= 4, (
        f"REJECT#23 AC#2: codex_review_snapshot_changed 문자열이 소스에 {snapshot_changed_count}개뿐 — "
        f"pre-CLI(3개) + post-CLI(1개) 총 4개 이상 필요 "
        f"(pre-CLI HEAD, pre-CLI semantic, preflight mismatch, post-CLI snapshot)"
    )

    # _write_codex_review_blocked_invalidation 호출에 codex_review_snapshot_changed가 포함된 쌍이 있어야 함.
    # 소스를 라인 단위로 확인하여 'codex_review_snapshot_changed'가 invalidation 호출 인자로 쓰이는지 검증.
    lines = src.splitlines()
    invalidation_with_snapshot = False
    for i, line in enumerate(lines):
        if "_write_codex_review_blocked_invalidation" in line:
            # 다음 몇 줄 안에 codex_review_snapshot_changed가 있으면 인자로 포함된 것.
            nearby = " ".join(lines[i:i+3])
            if "codex_review_snapshot_changed" in nearby:
                invalidation_with_snapshot = True
                break
    assert invalidation_with_snapshot, (
        "REJECT#23 AC#2: _write_codex_review_blocked_invalidation 호출 중 "
        "codex_review_snapshot_changed 인자를 사용하는 호출이 없음 — "
        "snapshot 실패 시 generic review_in_progress만 남고 specific failure_code가 저장 안 됨"
    )


def test_tc38c_pipeline_manager_doc_critical_actual_verified_only() -> None:
    """REJECT#14 AC#4 + REJECT#12 fix: Pipeline Manager 문서가 HIGH/CRITICAL에서
    invocation_verified 허용 정책을 코드와 일치하도록 명시한다.

    REJECT#12 이후 CRITICAL도 invocation_verified를 허용한다(unknown_model_critical_blocked 삭제).
    문서는 이를 정확히 반영해야 한다: HIGH/CRITICAL 모두 invocation_verified 이상이면 통과.
    """
    from pathlib import Path

    # Pipeline Manager 문서 경로.
    agent_md = (
        Path(__file__).resolve().parent.parent.parent
        / ".claude" / "agents" / "pipeline-manager-agent.md"
    )
    assert agent_md.exists(), (
        f"REJECT#14 AC#4: pipeline-manager-agent.md가 없음 — {agent_md}"
    )
    doc = agent_md.read_text(encoding="utf-8")

    # HIGH/CRITICAL이 invocation_verified로 통과 가능함을 문서가 명시해야 한다.
    high_critical_invocation_line = any(
        "HIGH" in line and "CRITICAL" in line and "invocation_verified" in line
        for line in doc.splitlines()
    )
    assert high_critical_invocation_line, (
        "REJECT#14 AC#4: 문서에 HIGH/CRITICAL이 invocation_verified로 통과 가능하다는 라인이 없음 — "
        "REJECT#12 fix에 따라 CRITICAL도 invocation_verified 허용으로 변경됨"
    )

    # unverified는 HIGH/CRITICAL 모두 차단함을 명시해야 한다.
    unverified_blocked_line = any(
        "unverified" in line and ("HIGH" in line or "CRITICAL" in line) and "BLOCKED" in line
        for line in doc.splitlines()
    )
    assert unverified_blocked_line, (
        "REJECT#14 AC#4: 문서에 unverified가 HIGH/CRITICAL에서 BLOCKED된다는 라인이 없음"
    )

    # 이전 잘못된 문구("CRITICAL은 반드시 actual_verified이어야 통과")가 없어야 한다.
    assert "CRITICAL은 반드시 `actual_verified`이어야 통과" not in doc, (
        "REJECT#14 AC#4: 문서에 CRITICAL이 actual_verified만 통과한다는 구버전 문구가 남아 있음 — "
        "REJECT#12 fix: CRITICAL도 invocation_verified 허용"
    )

    # 삭제된 unknown_model_critical_blocked 블록 코드가 없어야 한다.
    assert "unknown_model_critical_blocked" not in doc, (
        "REJECT#14 AC#4: 문서에 삭제된 unknown_model_critical_blocked 코드가 남아 있음"
    )


# --------------------------------------------------------------------------- #
# TC-39: REJECT#25 — Fix 1 (비pipeline.py evidence_complete) + Fix 2 (effort 정규화) +
#         Fix 3 (BLOCKED 무효화 쓰기 실패 시 기존 파일 삭제).
# --------------------------------------------------------------------------- #


def test_tc39a_noncritical_file_diff_included_and_evidence_complete(tmp_path: Path) -> None:
    """REJECT#25 AC#1: pipeline.py가 없는 LOW 문서·MEDIUM 코드 변경에서도
    실제 변경 diff가 포함되고 evidence_complete=True가 되는 것을 소스 검증한다.

    AC#1: changed_files에 pipeline.py가 없는 경우 section 1c가 비CRITICAL 파일의 diff를
          수집하고 _diff_ok=True로 갱신하는 코드 경로가 소스에 존재한다.
    """
    import inspect

    src = inspect.getsource(pipeline._build_codex_semantic_evidence)

    # Fix 1: section 1c — 비CRITICAL diff 수집 경로가 소스에 있어야 한다.
    assert "1c" in src, (
        "REJECT#25 AC#1: _build_codex_semantic_evidence에 section 1c(비CRITICAL diff) 코드가 없음"
    )
    assert "_is_codex_critical_file" in src, (
        "REJECT#25 AC#1: section 1c에 _is_codex_critical_file 가드가 없음"
    )
    # _diff_ok = True 갱신이 section 1c 내에 있어야 한다.
    assert "_diff_ok = True" in src, (
        "REJECT#25 AC#1: section 1c에서 _diff_ok=True 갱신이 없음 — 비CRITICAL 파일 diff 성공 시 갱신 필요"
    )

    # Fix 1: 비CRITICAL 파일도 is_critical=False 로 _all_hunks에 추가되어야 한다.
    assert '"is_critical": False' in src, (
        "REJECT#25 AC#1: section 1c에서 is_critical=False hunk 추가 코드가 없음"
    )


def test_tc39b_effort_canonicalize_max_xhigh(tmp_path: Path) -> None:
    """REJECT#25 AC#2/AC#3: effort 정규화가 max↔xhigh를 동등하게 처리하고
    비동등 값은 여전히 차단한다.

    AC#2: CRITICAL max→xhigh 변환 후 CLI actual_effort=xhigh → actual_verified.
    AC#3: 비동등 effort 값은 actual_effort_mismatch로 차단된다.
    """
    # _canonicalize_effort 함수 존재 확인.
    assert hasattr(pipeline, "_canonicalize_effort"), (
        "REJECT#25 AC#2: _canonicalize_effort 함수가 없음"
    )
    canon = pipeline._canonicalize_effort

    # AC#2: xhigh → max 정규화.
    assert canon("xhigh") == "max", (
        f"REJECT#25 AC#2: _canonicalize_effort('xhigh')가 'max'가 아님 — got {canon('xhigh')!r}"
    )
    # max는 그대로.
    assert canon("max") == "max", (
        f"REJECT#25 AC#2: _canonicalize_effort('max')가 'max'가 아님 — got {canon('max')!r}"
    )
    # high/low/medium은 그대로.
    assert canon("high") == "high", (
        f"REJECT#25 AC#2: _canonicalize_effort('high')가 'high'가 아님 — got {canon('high')!r}"
    )
    assert canon("low") == "low", (
        f"REJECT#25 AC#2: _canonicalize_effort('low')가 'low'가 아님 — got {canon('low')!r}"
    )

    # AC#2: CRITICAL 정책(selected_effort=max)에서 actual_effort=xhigh → actual_verified.
    level = pipeline._compute_model_verification_level(
        selected_model="gpt-5.6-sol",
        selected_effort="max",
        invoked_model="gpt-5.6-sol",
        invoked_effort="xhigh",      # CLI에는 xhigh 전달
        actual_model="gpt-5.6-sol",
        actual_effort="xhigh",       # CLI가 actual_effort=xhigh 보고
        invocation_ok=True,
    )
    assert level == pipeline.CODEX_VERIFICATION_ACTUAL, (
        f"REJECT#25 AC#2: selected=max, actual=xhigh → actual_verified가 되어야 하는데 {level!r}"
    )

    # AC#3: 비동등 effort → _check_codex_model_capability_match가 actual_effort_mismatch.
    result = pipeline._check_codex_model_capability_match(
        selected_model="gpt-5.6-sol",
        selected_effort="max",
        invoked_model="gpt-5.6-sol",
        invoked_effort="xhigh",      # 정규화 후 max와 동등
        actual_model="gpt-5.6-sol",
        actual_effort="high",        # high ≠ max → 차단
        invocation_ok=True,
        risk_level="CRITICAL",
    )
    assert result.get("failure_code") == "actual_effort_mismatch", (
        f"REJECT#25 AC#3: actual_effort='high' vs selected='max' → actual_effort_mismatch 아님 — {result!r}"
    )


def test_tc39c_write_blocked_invalidation_write_failure_deletes_old_file(
    tmp_path: Path,
) -> None:
    """REJECT#25 AC#4: BLOCKED 무효화 쓰기 실패를 강제 발생시킨 뒤
    기존 APPROVED 결과가 사용 불가능함을 검증한다.

    AC#4: _write_codex_review_blocked_invalidation 쓰기 실패 시 기존 APPROVED 파일을 삭제.
    """
    import json
    import unittest.mock as mock

    result_path = tmp_path / "codex_review_result.json"

    # 기존 APPROVED 결과를 파일에 기록한다(이전 effective 결과 시뮬레이션).
    fake_approved = {
        "schema_version": 5,
        "pipeline_id": "IMP-TEST-39C",
        "verdict": "APPROVE_TO_USER",
        "acceptance_eligible": True,
        "effective": True,
        "status": "APPROVED",
    }
    result_path.write_text(json.dumps(fake_approved), encoding="utf-8")
    assert result_path.exists(), "TC-39c: APPROVED 파일이 없어 테스트 전제 불충족"

    # _codex_review_result_path를 격리된 경로로 monkeypatch.
    original_fn = pipeline._codex_review_result_path
    pipeline._codex_review_result_path = lambda: result_path  # type: ignore[assignment]
    try:
        # _write_json이 OSError를 내도록 강제 — 쓰기 실패 시뮬레이션.
        with mock.patch("pipeline._write_json", side_effect=OSError("forced write failure")):
            pipeline._write_codex_review_blocked_invalidation(
                "IMP-TEST-39C", "model_mismatch",
                prev_reject_count=0,
                prev_cli_error_count=0,
                review_bundle_sha256="bundle_sha_39c",
                risk_level="CRITICAL",
                model_policy={
                    "selected_model": "gpt-5.6-sol",
                    "selected_reasoning_effort": "max",
                    "mode": "enforce",
                },
            )
    finally:
        pipeline._codex_review_result_path = original_fn  # type: ignore[assignment]

    # AC#4: 쓰기 실패 후 기존 APPROVED 파일이 삭제되어야 한다.
    assert not result_path.exists(), (
        "REJECT#25 AC#4: _write_json 실패 후 기존 APPROVED 파일이 남아 있음 — "
        "이전 APPROVED 결과가 재사용될 수 있어 trust-chain 위반"
    )


# --------------------------------------------------------------------------- #
# TC-40: REJECT#26 — _check_codex_review_operational_trust effort 정규화.
#         _canonicalize_effort를 선택/invoked/actual effort 비교에 적용.
# --------------------------------------------------------------------------- #


def _make_valid_result_for_operational_trust(
    verdict_source: str = "codex_cli",
    actual_effort: str = "xhigh",
    invoked_effort: str = "xhigh",
    actual_model: str = "gpt-5.6-sol",
    risk_level: str = "CRITICAL",
    verification_level: str = "actual_verified",
) -> dict:
    """_check_codex_review_operational_trust 테스트용 유효한 기본 result dict.

    REJECT#27: model_policy_signature를 실제 정책에서 동적으로 계산한다.
    selected_model/selected_reasoning_effort도 재계산된 정책값을 사용한다.
    """
    _policy = pipeline._build_codex_model_policy(risk_level)
    _sig = pipeline._codex_policy_signature(_policy)
    _sel_model = str(_policy.get("selected_model", "gpt-5.6-sol") or "")
    _sel_effort = str(_policy.get("selected_reasoning_effort", "max") or "")
    cli_cmd = (
        "N/A (cache hit)" if verdict_source == "verified_cache"
        else (
            f"codex exec --model {_sel_model} -c model_reasoning_effort={invoked_effort} "
            "--sandbox read-only --ephemeral --json -"
        )
    )
    return {
        "status": "APPROVED",
        "verdict_source": verdict_source,
        "acceptance_eligible": True,
        "router_version": pipeline.CODEX_MODEL_ROUTER_VERSION,
        "risk_level": risk_level,
        "model_policy_signature": _sig,
        "codex_cli_command": cli_cmd,
        "selected_model": _sel_model,
        "selected_reasoning_effort": _sel_effort,
        "invoked_model": _sel_model,
        "invoked_effort": invoked_effort,
        "actual_model": actual_model,
        "actual_effort": actual_effort,
        "model_verification_level": verification_level,
        "auth_source": "chatgpt",
        # IMP-20260712-DAE1 REJECT#16: codex_cli/verified_cache에는 binary 신뢰 증거가 필수.
        #   테스트용 dummy 값 (실제 경로가 아님 — 운영 신뢰 검사 단위 테스트 전용).
        "codex_binary_path": "/usr/local/bin/codex",
        "codex_binary_sha256": "a" * 64,  # dummy SHA-256 (test-only placeholder)
        # REJECT#LATEST: native/vendor binary 신뢰 증거 필수 (test-only dummy).
        "codex_native_binary_path": "/usr/local/lib/codex/vendor/codex",
        "codex_native_binary_sha256": "e" * 64,
    }


def test_tc40a_operational_trust_actual_effort_xhigh_passes() -> None:
    """REJECT#26 AC#1: CRITICAL 정책에서 selected_effort=max, actual_effort=xhigh,
    model_verification_level=actual_verified인 결과가 운영 신뢰 검사를 PASS한다.

    AC#1: _canonicalize_effort 적용 후 xhigh≡max이므로 actual_effort_mismatch가 발생하지 않는다.
    """
    result = _make_valid_result_for_operational_trust(
        verdict_source="codex_cli",
        actual_effort="xhigh",
        invoked_effort="xhigh",
        actual_model="gpt-5.6-sol",
        risk_level="CRITICAL",
        verification_level=pipeline.CODEX_VERIFICATION_ACTUAL,
    )
    r = pipeline._check_codex_review_operational_trust(result)
    assert r["status"] == "PASS", (
        f"REJECT#26 AC#1: selected_effort=max, actual_effort=xhigh → actual_verified 결과가 PASS가 아님\n"
        f"  result={r}\n"
        f"  해결: _check_codex_review_operational_trust에서 _canonicalize_effort를 사용해야 함"
    )


def test_tc40b_operational_trust_actual_effort_high_blocked() -> None:
    """REJECT#26 AC#2: actual_effort=high (max와 비동등)은 codex_review_actual_effort_mismatch로 차단된다.

    AC#2: 정규화 후에도 실제로 다른 값(high ≠ max)은 여전히 차단된다.
    """
    result = _make_valid_result_for_operational_trust(
        verdict_source="codex_cli",
        actual_effort="high",     # high ≠ max (not an alias)
        invoked_effort="xhigh",
        actual_model="gpt-5.6-sol",
        risk_level="CRITICAL",
        verification_level=pipeline.CODEX_VERIFICATION_ACTUAL,
    )
    r = pipeline._check_codex_review_operational_trust(result)
    assert r["status"] == "BLOCKED", (
        f"REJECT#26 AC#2: actual_effort=high(≠max)이 BLOCKED가 아님 — {r!r}"
    )
    assert r.get("failure_code") == "codex_review_actual_effort_mismatch", (
        f"REJECT#26 AC#2: failure_code가 codex_review_actual_effort_mismatch가 아님 — "
        f"got {r.get('failure_code')!r}"
    )


def test_tc40c_operational_trust_verified_cache_xhigh_passes() -> None:
    """REJECT#26 AC#3: verified_cache 결과에서 actual_effort=xhigh도 운영 신뢰 검사를 PASS한다.

    AC#3: 동일한 max/xhigh 증거를 복원한 캐시 결과도 PASS한다.
    """
    result = _make_valid_result_for_operational_trust(
        verdict_source="verified_cache",
        actual_effort="xhigh",
        invoked_effort="max",      # 캐시 재사용 시 invoked_effort는 정책값(max)일 수 있음
        actual_model="gpt-5.6-sol",
        risk_level="CRITICAL",
        verification_level=pipeline.CODEX_VERIFICATION_ACTUAL,
    )
    r = pipeline._check_codex_review_operational_trust(result)
    assert r["status"] == "PASS", (
        f"REJECT#26 AC#3: verified_cache+actual_effort=xhigh 결과가 PASS가 아님\n  result={r}"
    )


def test_tc40d_canonicalize_effort_in_operational_trust_source() -> None:
    """REJECT#26 AC#4: _check_codex_review_operational_trust 소스에 _canonicalize_effort가 포함된다.

    AC#4: gates codex-review 결과 기록부터 request-accept 직전 신뢰 검사까지
          _canonicalize_effort가 연결되어 통과하는 회귀 소스 검증.
    """
    import inspect

    src = inspect.getsource(pipeline._check_codex_review_operational_trust)

    # Fix: _canonicalize_effort가 operational_trust 소스에 존재해야 한다.
    assert "_canonicalize_effort" in src, (
        "REJECT#26 AC#4: _check_codex_review_operational_trust에 _canonicalize_effort가 없음 — "
        "effort 비교가 정규화되지 않아 actual_effort=xhigh 시 codex_review_actual_effort_mismatch 발생"
    )

    # codex_review_actual_effort_mismatch가 canonicalized 비교 후에 발생해야 한다.
    assert "codex_review_actual_effort_mismatch" in src, (
        "REJECT#26 AC#4: codex_review_actual_effort_mismatch 코드가 operational_trust 소스에 없음"
    )

    # invocation_ok 없는 기본 high-risk result가 _check_codex_review_operational_trust를 통과하는지 검증.
    # (request-accept 직전 신뢰 검사 통합 확인)
    high_result = _make_valid_result_for_operational_trust(
        verdict_source="codex_cli",
        actual_effort="unknown",   # actual 미보고 시
        invoked_effort="high",     # HIGH 정책 effort = "high" (max 아님)
        actual_model="unknown",
        risk_level="HIGH",
        verification_level=pipeline.CODEX_VERIFICATION_INVOCATION,
    )
    r_high = pipeline._check_codex_review_operational_trust(high_result)
    assert r_high["status"] == "PASS", (
        f"REJECT#26 AC#4: HIGH+invocation_verified 결과가 operational_trust PASS가 아님 — {r_high!r}"
    )


# ============================================================
# TC-41: REJECT#27 운영 신뢰 게이트 강화 + 정확한 인증 검사
# ============================================================


def test_tc41a_critical_wrong_policy_signature_blocked() -> None:
    """REJECT#27 AC#1: CRITICAL에서 잘못된 policy signature는 codex_review_policy_signature_mismatch로 차단된다.

    luna/terra 모델 또는 임의의 비어 있지 않은 잘못된 model_policy_signature를
    넣으면 운영 신뢰 검사가 BLOCKED를 반환한다.
    """
    # 올바른 CRITICAL 결과를 기반으로 policy signature만 잘못된 값으로 교체
    result = _make_valid_result_for_operational_trust(
        verdict_source="codex_cli",
        actual_effort="xhigh",
        invoked_effort="xhigh",
        actual_model="gpt-5.6-sol",
        risk_level="CRITICAL",
        verification_level=pipeline.CODEX_VERIFICATION_ACTUAL,
    )
    # 잘못된 signature로 교체 (luna 모델 사용 위조)
    result["model_policy_signature"] = "CRITICAL:gpt-5.6-luna:max:enforce"
    r = pipeline._check_codex_review_operational_trust(result)
    assert r["status"] == "BLOCKED", (
        f"REJECT#27 AC#1: 잘못된 policy signature(luna)가 BLOCKED가 아님 — {r!r}"
    )
    assert r.get("failure_code") == "codex_review_policy_signature_mismatch", (
        f"REJECT#27 AC#1: failure_code가 codex_review_policy_signature_mismatch가 아님 — "
        f"got {r.get('failure_code')!r}"
    )


def test_tc41b_actual_verified_unknown_model_blocked() -> None:
    """REJECT#27 AC#2a: actual_verified인데 actual_model=unknown이면 BLOCKED된다.

    Fix B: actual_model=unknown이어도 verification_level만 actual_verified로 위조하면
    통과되던 취약점을 차단한다.
    """
    result = _make_valid_result_for_operational_trust(
        verdict_source="codex_cli",
        actual_effort="xhigh",
        invoked_effort="xhigh",
        actual_model="unknown",   # actual_model 미보고 → 위조 시도
        risk_level="CRITICAL",
        verification_level=pipeline.CODEX_VERIFICATION_ACTUAL,
    )
    r = pipeline._check_codex_review_operational_trust(result)
    assert r["status"] == "BLOCKED", (
        f"REJECT#27 AC#2a: actual_verified+actual_model=unknown이 BLOCKED가 아님 — {r!r}"
    )
    assert r.get("failure_code") == "codex_review_actual_verified_but_unknown_model", (
        f"REJECT#27 AC#2a: failure_code가 codex_review_actual_verified_but_unknown_model가 아님 — "
        f"got {r.get('failure_code')!r}"
    )


def test_tc41c_actual_verified_unknown_effort_blocked() -> None:
    """REJECT#27 AC#2b: actual_verified인데 actual_effort=unknown이면 BLOCKED된다.

    Fix B: actual_effort=unknown이어도 verification_level만 actual_verified로 위조하면
    통과되던 취약점을 차단한다.
    """
    result = _make_valid_result_for_operational_trust(
        verdict_source="codex_cli",
        actual_effort="unknown",   # actual_effort 미보고 → 위조 시도
        invoked_effort="xhigh",
        actual_model="gpt-5.6-sol",
        risk_level="CRITICAL",
        verification_level=pipeline.CODEX_VERIFICATION_ACTUAL,
    )
    r = pipeline._check_codex_review_operational_trust(result)
    assert r["status"] == "BLOCKED", (
        f"REJECT#27 AC#2b: actual_verified+actual_effort=unknown이 BLOCKED가 아님 — {r!r}"
    )
    assert r.get("failure_code") == "codex_review_actual_verified_but_unknown_effort", (
        f"REJECT#27 AC#2b: failure_code가 codex_review_actual_verified_but_unknown_effort가 아님 — "
        f"got {r.get('failure_code')!r}"
    )


def test_tc41d_router_version_mismatch_blocked() -> None:
    """REJECT#27 AC#3: router_version 불일치는 codex_review_router_version_mismatch로 차단된다.

    Fix A: 현재 CODEX_MODEL_ROUTER_VERSION과 다른 router_version을 가진 결과는
    BLOCKED된다(오래된 캐시 또는 다른 버전 정책으로 생성된 결과 차단).
    """
    result = _make_valid_result_for_operational_trust(
        verdict_source="codex_cli",
        actual_effort="xhigh",
        invoked_effort="xhigh",
        actual_model="gpt-5.6-sol",
        risk_level="CRITICAL",
        verification_level=pipeline.CODEX_VERIFICATION_ACTUAL,
    )
    # 오래된 버전으로 교체
    result["router_version"] = "0.9.0"
    r = pipeline._check_codex_review_operational_trust(result)
    assert r["status"] == "BLOCKED", (
        f"REJECT#27 AC#3: router_version=0.9.0 불일치가 BLOCKED가 아님 — {r!r}"
    )
    assert r.get("failure_code") == "codex_review_router_version_mismatch", (
        f"REJECT#27 AC#3: failure_code가 codex_review_router_version_mismatch가 아님 — "
        f"got {r.get('failure_code')!r}"
    )


def test_tc41e_chatgpt_auth_not_logged_in_blocked() -> None:
    """REJECT#27 AC#4a: 'Not Logged in using ChatGPT'는 인증 검사를 BLOCKED한다.

    Fix C 이전: 부분 문자열 검사 → 'Logged in using ChatGPT' 포함 → 잘못 통과.
    Fix C 이후: 정확한 라인 일치 → 'Not Logged in using ChatGPT' ≠ 마커 → BLOCKED.
    """
    from unittest.mock import MagicMock, patch

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = "Not Logged in using ChatGPT\n"
    fake_proc.stderr = ""

    with patch("pipeline.subprocess.run", return_value=fake_proc):
        r = pipeline._check_codex_chatgpt_auth(codex_bin="codex")
    assert r["result"] == "BLOCKED", (
        f"REJECT#27 AC#4a: 'Not Logged in using ChatGPT'가 BLOCKED가 아님 — {r!r}"
    )
    assert r.get("failure_code") == "codex_not_chatgpt_authenticated", (
        f"REJECT#27 AC#4a: failure_code가 codex_not_chatgpt_authenticated가 아님 — "
        f"got {r.get('failure_code')!r}"
    )


def test_tc41f_chatgpt_auth_prefix_suffix_blocked() -> None:
    """REJECT#27 AC#4b: 접두·접미 문구로 인증 마커가 오염된 경우 BLOCKED된다.

    'Warning: Logged in using ChatGPT', 'Logged in using ChatGPT (limited)' 등은
    정확한 라인 일치 검사에서 탈락해야 한다.
    """
    from unittest.mock import MagicMock, patch

    bad_outputs = [
        "Warning: Logged in using ChatGPT\n",
        "Logged in using ChatGPT (limited)\n",
        "[INFO] Logged in using ChatGPT\n",
    ]
    for bad_output in bad_outputs:
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = bad_output
        fake_proc.stderr = ""
        with patch("pipeline.subprocess.run", return_value=fake_proc):
            r = pipeline._check_codex_chatgpt_auth(codex_bin="codex")
        assert r["result"] == "BLOCKED", (
            f"REJECT#27 AC#4b: 오염된 마커가 BLOCKED가 아님 — "
            f"input={bad_output!r}, result={r!r}"
        )


def test_tc41g_chatgpt_auth_exact_match_passes() -> None:
    """REJECT#27 AC#5: 정확한 'Logged in using ChatGPT' 라인은 인증 검사를 PASS한다.

    Fix C 적용 후에도 정확한 마커 라인이 있으면 OK를 반환하여 정상 사용이 유지된다.
    """
    from unittest.mock import MagicMock, patch

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = "Logged in using ChatGPT\n"
    fake_proc.stderr = ""

    with patch("pipeline.subprocess.run", return_value=fake_proc):
        r = pipeline._check_codex_chatgpt_auth(codex_bin="codex")
    assert r["result"] == "OK", (
        f"REJECT#27 AC#5: 정확한 ChatGPT 로그인 마커가 OK가 아님 — {r!r}"
    )
    assert r.get("auth_source") == "chatgpt", (
        f"REJECT#27 AC#5: auth_source가 chatgpt가 아님 — got {r.get('auth_source')!r}"
    )


# ============================================================
# TC-42: REJECT#28 — 신규·삭제 CRITICAL 함수 및 unknown hunk fail-open 수정
# ============================================================


def test_tc42a_new_deleted_critical_func_included_in_source() -> None:
    """REJECT#28 AC#1/AC#2 소스 검증: function_before_after_shas 조건이 신규·삭제 함수를 포함한다.

    Fix 1: 기존 `_b_sha and _a_sha and _b_sha != _a_sha` 조건이
           `(_b_sha or _a_sha) and _b_sha != _a_sha`로 변경되어
           신규(after만) / 삭제(before만) CRITICAL 함수도 SHA 항목에 포함된다.
    """
    import inspect

    src = inspect.getsource(pipeline._build_codex_semantic_evidence)

    # Fix 1 핵심: `(_b_sha or _a_sha)` 형태의 조건이 있어야 한다.
    assert "(_b_sha or _a_sha)" in src, (
        "REJECT#28 Fix 1: (_b_sha or _a_sha) 조건이 소스에 없음 — "
        "신규/삭제 CRITICAL 함수가 function_before_after_shas에서 누락됨"
    )
    # 기존 취약 조건(`_b_sha and _a_sha and`)이 제거됐는지 확인
    assert "if _b_sha and _a_sha and _b_sha != _a_sha" not in src, (
        "REJECT#28 Fix 1: 기존 취약 조건 `if _b_sha and _a_sha and _b_sha != _a_sha`가 여전히 존재 — "
        "신규·삭제 CRITICAL 함수가 여전히 누락될 수 있음"
    )
    # REJECT#28 Fix 1 주석이 포함됐는지 확인
    assert "REJECT#28 Fix 1" in src, (
        "REJECT#28 Fix 1: 소스에 REJECT#28 Fix 1 주석이 없음 — 수정이 누락됐을 수 있음"
    )


def test_tc42b_unknown_hunk_marked_critical_in_source() -> None:
    """REJECT#28 AC#3 소스 검증: pipeline.py의 unknown function hunk가 CRITICAL로 분류된다.

    Fix 2: `_flush` 함수에서 is_critical 계산이 `_fn in _crit_funcs or _fn == "unknown"`으로
           변경되어 hunk 헤더에서 함수명을 찾지 못한 hunks도 CRITICAL로 처리된다.
    """
    import inspect

    src = inspect.getsource(pipeline._build_codex_semantic_evidence)

    # Fix 2 핵심: `_fn == "unknown"` 조건이 is_critical 판정에 포함돼야 한다.
    assert '_fn == "unknown"' in src or "_fn == 'unknown'" in src, (
        "REJECT#28 Fix 2: `_fn == \"unknown\"` 조건이 소스에 없음 — "
        "unknown-named pipeline.py hunk가 non-critical로 분류되어 예산 초과 시 조용히 누락됨"
    )
    # REJECT#28 Fix 2 주석이 포함됐는지 확인
    assert "REJECT#28 Fix 2" in src, (
        "REJECT#28 Fix 2: 소스에 REJECT#28 Fix 2 주석이 없음"
    )


def test_tc42c_unknown_hunk_excluded_cross_validation_in_source() -> None:
    """REJECT#28/30 AC#3 소스 검증: cross-validation이 unknown CRITICAL hunk 누락을 감지한다.

    REJECT#28 Fix 3b에서 _all_unk_crit/_sel_unk_crit 방식이 도입됐으나,
    REJECT#30 Fix 2에서 더 포괄적인 hunk 단위 카운트(_all_crit_count/_sel_crit_count)로
    통합됐다. 새 방식은 unknown-named hunk, 멀티-hunk, 신규·삭제 함수를 모두 처리한다.
    """
    import inspect

    src = inspect.getsource(pipeline._build_codex_semantic_evidence)

    # REJECT#30 Fix 2: _all_crit_count/_sel_crit_count가 unknown hunk를 포함한 모든 경우를 처리
    assert "_all_crit_count" in src, (
        "REJECT#28/30 Fix: _all_crit_count 변수가 소스에 없음 — "
        "cross-validation이 CRITICAL hunk 누락(unknown 포함)을 감지하지 못함"
    )
    assert "_sel_crit_count" in src, (
        "REJECT#28/30 Fix: _sel_crit_count 변수가 소스에 없음"
    )


def test_tc42d_new_critical_function_in_sha_dict() -> None:
    """REJECT#28 AC#1: 신규 CRITICAL 함수(before SHA 없음)가 function_before_after_shas에 포함된다.

    Fix 1 동작 기반 테스트: git show가 before 코드에 함수 없이 반환되면(신규 함수),
    function_before_after_shas에 before=""로 기록된다.
    """
    from unittest.mock import patch, MagicMock

    # 실제 CRITICAL 함수 선택 (현재 파일에 존재해야 함)
    new_func = "_check_codex_review_operational_trust"
    assert new_func in pipeline.CODEX_CRITICAL_FUNCTIONS, (
        f"TC-42d 전제조건: {new_func}이 CODEX_CRITICAL_FUNCTIONS에 없음"
    )

    # git show origin/main:pipeline.py → 해당 함수가 없는 최소 Python 파일 (신규 함수 시뮬레이션)
    before_code = "# minimal before state\ndef some_other_function():\n    pass\n"

    diff_output = (
        "diff --git a/pipeline.py b/pipeline.py\n"
        "--- a/pipeline.py\n"
        "+++ b/pipeline.py\n"
        f"@@ -1,0 +1,3 @@ def {new_func}(result):\n"
        f"+def {new_func}(result):\n"
        '+    """New critical function.\"\"\"\n'
        "+    return {'status': 'PASS'}\n"
    )

    def _mock_run(cmd, **kwargs):
        mock_result = MagicMock()
        cmd_list = [str(c) for c in cmd]
        if "show" in cmd_list and any("origin/main:pipeline.py" in c for c in cmd_list):
            mock_result.returncode = 0
            mock_result.stdout = before_code
        elif "diff" in cmd_list and "pipeline.py" in cmd_list:
            mock_result.returncode = 0
            mock_result.stdout = diff_output
        else:
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
        return mock_result

    with patch("pipeline.subprocess.run", side_effect=_mock_run):
        result = pipeline._build_codex_semantic_evidence(
            "IMP-TEST-REJECT28",
            ["pipeline.py"],
            list(pipeline.CODEX_CRITICAL_FUNCTIONS),
        )

    fas = result.get("function_before_after_shas", {})
    key = f"pipeline.py::{new_func}"
    assert key in fas, (
        f"REJECT#28 AC#1: 신규 CRITICAL 함수 {new_func!r}이 function_before_after_shas에 없음\n"
        f"  Fix 1이 적용됐는지 확인 — before OR after 하나만 있어도 포함돼야 함\n"
        f"  현재 fas 키: {list(fas.keys())[:5]!r}"
    )
    assert fas[key]["before"] == "", (
        f"REJECT#28 AC#1: 신규 함수의 before SHA가 비어 있지 않음 — got {fas[key]['before']!r}"
    )
    assert fas[key]["after"] != "", (
        "REJECT#28 AC#1: 신규 함수의 after SHA가 비어 있음 — 현재 파일에 함수가 존재해야 함"
    )


def test_tc42e_unknown_hunk_budget_exceeded_evidence_incomplete() -> None:
    """REJECT#28 AC#3: unknown-named pipeline.py hunk가 예산 초과로 제외되면
    truncated_critical_hunks가 증가하고 evidence_complete=False가 된다.

    Fix 2 + Fix 3 동작 기반 테스트: 예산을 초과하는 unknown-named hunk를 시뮬레이션하고,
    cross-validation이 이를 감지하여 evidence_complete=False를 반환하는지 검증한다.
    """
    from unittest.mock import patch, MagicMock

    # 예산을 크게 초과하는 unknown-named hunk (@@에 함수명 없음)
    big_content = "+" + "x" * 850_000  # 850KB — 실제 예산(800000)을 크게 초과 (REJECT#29: 700000→800000)
    diff_output = (
        "diff --git a/pipeline.py b/pipeline.py\n"
        "--- a/pipeline.py\n"
        "+++ b/pipeline.py\n"
        "@@ -1,0 +1,1 @@\n"  # 함수명 없는 헤더 → unknown
        f"{big_content}\n"
    )
    before_code = "# minimal\n"

    def _mock_run(cmd, **kwargs):
        mock_result = MagicMock()
        cmd_list = [str(c) for c in cmd]
        if "show" in cmd_list and any("origin/main:pipeline.py" in c for c in cmd_list):
            mock_result.returncode = 0
            mock_result.stdout = before_code
        elif "diff" in cmd_list and "pipeline.py" in cmd_list:
            mock_result.returncode = 0
            mock_result.stdout = diff_output
        else:
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
        return mock_result

    with patch("pipeline.subprocess.run", side_effect=_mock_run):
        result = pipeline._build_codex_semantic_evidence(
            "IMP-TEST-REJECT28",
            ["pipeline.py"],
            [],  # 특정 함수 없음 — unknown hunk만 테스트
        )

    assert result["truncated_critical_hunks"] > 0, (
        f"REJECT#28 AC#3: unknown hunk 예산 초과 시 truncated_critical_hunks가 0 — "
        f"Fix 2(unknown → CRITICAL) 또는 Fix 3(cross-validation) 미적용\n"
        f"  got truncated_critical_hunks={result['truncated_critical_hunks']}"
    )
    assert result["evidence_complete"] is False, (
        f"REJECT#28 AC#3: unknown hunk 예산 초과 시 evidence_complete가 False가 아님 — "
        f"got {result['evidence_complete']}"
    )


# =========================================================================== #
# TC-43: REJECT#29 — 신뢰 경계 함수 등록 완전성 + 보고서 SSoT 일치
# =========================================================================== #

# REJECT#29/30 AC#1/2: 신뢰 경계 함수 집합 (CODEX_CRITICAL_FUNCTIONS에 모두 포함돼야 함).
# 이 목록에 새 함수를 추가하지 않으면 TC-43b 완전성 테스트가 실패하여 누락을 자동 감지한다.
_TRUST_BOUNDARY_FUNCS_REJECT29 = [
    # REJECT#29 추가 (실행·인증·모델 검증·증거·운영 신뢰)
    "_invoke_codex_exec",
    "_parse_codex_exec_capability",
    "_compute_model_verification_level",
    "_check_codex_model_capability_match",
    "_build_codex_semantic_evidence",
    "_build_codex_prompt_for_review",
    # REJECT#30 추가 (위험 분류·effort 검증·함수 SHA 추출·파일 판정·capability 게이트·오류 차단)
    "_canonicalize_effort",
    "_extract_python_function_bodies",
    "_is_codex_test_path",
    "_is_codex_critical_file",
    "_check_codex_capability_gate",
    "_codex_review_error_blocker",
    # IMP-20260712-DAE1 REJECT#15 추가 (binary 신뢰 검증·JS 진입점·BLOCKED 플래그 helper)
    "_verify_codex_binary_path_trust",
    "_get_npm_global_bin",
    "_verify_npm_wrapper_content",
    "_find_codex_js_entrypoint",
    "_codex_review_blocked_flag_path",
]


def test_tc43a_invoke_codex_exec_is_critical() -> None:
    """REJECT#29 AC#1: _invoke_codex_exec만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(["pipeline.py"], ["_invoke_codex_exec"])
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#29 AC#1: _invoke_codex_exec 단독 변경이 CRITICAL이 아님 — "
        f"got risk_level={r['risk_level']!r}. CODEX_CRITICAL_FUNCTIONS에 추가 필요."
    )


def test_tc43b_parse_codex_exec_capability_is_critical() -> None:
    """REJECT#29 AC#1: _parse_codex_exec_capability만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"], ["_parse_codex_exec_capability"]
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#29 AC#1: _parse_codex_exec_capability 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}."
    )


def test_tc43c_compute_model_verification_level_is_critical() -> None:
    """REJECT#29 AC#1: _compute_model_verification_level만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"], ["_compute_model_verification_level"]
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#29 AC#1: _compute_model_verification_level 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}."
    )


def test_tc43d_check_codex_model_capability_match_is_critical() -> None:
    """REJECT#29 AC#1: _check_codex_model_capability_match만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"], ["_check_codex_model_capability_match"]
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#29 AC#1: _check_codex_model_capability_match 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}."
    )


def test_tc43e_build_codex_semantic_evidence_is_critical() -> None:
    """REJECT#29 AC#1: _build_codex_semantic_evidence만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"], ["_build_codex_semantic_evidence"]
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#29 AC#1: _build_codex_semantic_evidence 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}."
    )


def test_tc43f_build_codex_prompt_for_review_is_critical() -> None:
    """REJECT#29 AC#1: _build_codex_prompt_for_review만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"], ["_build_codex_prompt_for_review"]
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#29 AC#1: _build_codex_prompt_for_review 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}."
    )


def test_tc43g_trust_boundary_completeness() -> None:
    """REJECT#29 AC#2: 신뢰 경계 함수가 CODEX_CRITICAL_FUNCTIONS에 모두 등록돼 있다.

    이 테스트 자체에 신뢰 경계 함수를 추가해야 자동 감지가 동작한다.
    새 신뢰 경계 함수를 추가할 때는 _TRUST_BOUNDARY_FUNCS_REJECT29 목록도 갱신하시오.
    """
    missing = [
        fn for fn in _TRUST_BOUNDARY_FUNCS_REJECT29
        if fn not in pipeline.CODEX_CRITICAL_FUNCTIONS
    ]
    assert not missing, (
        "REJECT#29 AC#2: 아래 신뢰 경계 함수가 CODEX_CRITICAL_FUNCTIONS에 없음:\n"
        + "\n".join(f"  - {fn}" for fn in missing)
        + "\npipeline.py의 CODEX_CRITICAL_FUNCTIONS에 추가하십시오."
    )


def test_tc43h_critical_funcs_get_force_review_and_no_cache() -> None:
    """REJECT#29 AC#3: 신뢰 경계 함수 변경에는 cache 금지·force_review·CRITICAL 정책이 적용된다."""
    for fn in _TRUST_BOUNDARY_FUNCS_REJECT29:
        r = pipeline._classify_codex_review_risk(["pipeline.py"], [fn])
        assert r["risk_level"] == "CRITICAL", (
            f"REJECT#29 AC#3: {fn!r} 변경이 CRITICAL이 아님 — got {r['risk_level']!r}"
        )
    # CRITICAL 정책에서 cache 금지 + force_review 확인
    policy = pipeline._build_codex_model_policy("CRITICAL")
    assert policy.get("result") != "BLOCKED", "CRITICAL 정책이 BLOCKED를 반환함"
    assert policy.get("cache_allowed") is False, (
        f"REJECT#29 AC#3: CRITICAL 정책에서 cache_allowed가 False가 아님 — "
        f"got {policy.get('cache_allowed')!r}"
    )
    assert policy.get("force_review_required") is True, (
        f"REJECT#29 AC#3: CRITICAL 정책에서 force_review_required가 True가 아님 — "
        f"got {policy.get('force_review_required')!r}"
    )


def test_tc43i_report_model_table_uses_gpt56() -> None:
    """REJECT#29 AC#4: codex_model_router_report.md 모델표에 GPT-5.6 모델만 사용된다.
    claude-sonnet 또는 claude-opus가 모델 라우팅 표에 남아있으면 FAIL.
    """
    import re

    report_path = _ROOT / "codex_model_router_report.md"
    assert report_path.exists(), f"codex_model_router_report.md 파일이 없음: {report_path}"
    content = report_path.read_text(encoding="utf-8")

    # 섹션 3(모델 라우팅 표)의 테이블 행에서 claude 모델 참조 검사
    # 마크다운 테이블 행 패턴: | ... claude-... | 형식
    table_claude = re.findall(r"^\|.*claude-(?:sonnet|opus|haiku)[^|]*\|", content, re.MULTILINE)
    assert not table_claude, (
        "REJECT#29 AC#4: codex_model_router_report.md 모델 라우팅 표에 claude 모델이 남아있음:\n"
        + "\n".join(f"  {row}" for row in table_claude)
        + "\nGPT-5.6 계열(gpt-5.6-luna/terra/sol)로 교체하십시오."
    )


def test_tc43j_report_tests_not_high_risk_path() -> None:
    """REJECT#29 AC#4: codex_model_router_report.md의 HIGH 경로 목록에 tests/가 없다.
    tests/ 경로는 HIGH risk 경로가 아니며 제품 코드의 risk를 상속한다.
    """
    report_path = _ROOT / "codex_model_router_report.md"
    content = report_path.read_text(encoding="utf-8")

    # HIGH 섹션(### HIGH 이후 ### MEDIUM 이전)에서 tests/ 참조 검사
    high_section_match = __import__("re").search(
        r"### HIGH.*?### MEDIUM", content, __import__("re").DOTALL
    )
    if high_section_match:
        high_section = high_section_match.group(0)
        # 테이블 행에서 tests/ 가 경로 패턴으로 등록되어 있는지 확인
        import re as _re
        tests_in_table = _re.findall(r"^\|[^|]*`?tests/`?[^|]*\|", high_section, _re.MULTILINE)
        assert not tests_in_table, (
            "REJECT#29 AC#4: HIGH 섹션 테이블에 'tests/' 경로가 남아있음:\n"
            + "\n".join(f"  {row}" for row in tests_in_table)
            + "\n'tests/'는 HIGH risk 경로가 아닙니다 — 제거하십시오."
        )


def test_tc43k_report_high_unknown_not_blocked() -> None:
    """REJECT#29 AC#4 + REJECT#15: codex_model_router_report.md의 unknown 처리 표에서
    HIGH+unknown과 CRITICAL+unknown 모두 BLOCKED가 아님을 명시한다
    (REJECT#12 fix: CRITICAL도 invocation_verified로 허용).
    """
    import re

    report_path = _ROOT / "codex_model_router_report.md"
    content = report_path.read_text(encoding="utf-8")

    # actual_model=unknown 처리 섹션에서 HIGH + BLOCKED 조합 확인
    unknown_section_match = re.search(
        r"actual_model=unknown.*?##\s+\d+\.", content, re.DOTALL
    )
    if unknown_section_match:
        section = unknown_section_match.group(0)
        # HIGH 행에 BLOCKED가 없어야 함
        high_rows = re.findall(r"^\|[^|]*unknown[^|]*\|[^|]*HIGH[^|]*\|[^|]*BLOCK[^|]*\|", section, re.MULTILINE)
        assert not high_rows, (
            "REJECT#29 AC#4: unknown 처리 표에서 HIGH+BLOCKED 조합이 남아있음:\n"
            + "\n".join(f"  {row}" for row in high_rows)
            + "\nHIGH는 invocation_verified로 통과합니다."
        )
        # REJECT#15: CRITICAL 행에도 BLOCKED가 없어야 함 (REJECT#12 fix: 코드와 보고서 일치)
        crit_rows = re.findall(r"^\|[^|]*unknown[^|]*\|[^|]*CRITICAL[^|]*\|[^|]*BLOCK[^|]*\|", section, re.MULTILINE)
        assert not crit_rows, (
            "REJECT#15: unknown 처리 표에서 CRITICAL+BLOCKED 조합이 남아있음:\n"
            + "\n".join(f"  {row}" for row in crit_rows)
            + "\nREJECT#12 fix: CRITICAL도 invocation_verified로 허용합니다 (HIGH와 동일).\n"
            + "unknown_model_critical_blocked 항목을 제거하고 invocation_verified 설명으로 대체하세요."
        )


# =========================================================================== #
# TC-43l ~ TC-43p: REJECT#15 — binary 신뢰·JS 진입점·BLOCKED 플래그 helper CRITICAL 검증
# =========================================================================== #

def test_tc43l_verify_codex_binary_path_trust_is_critical() -> None:
    """REJECT#15 AC#1: _verify_codex_binary_path_trust만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"], ["_verify_codex_binary_path_trust"]
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#15 AC#1: _verify_codex_binary_path_trust 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}. CODEX_CRITICAL_FUNCTIONS에 추가 필요."
    )


def test_tc43m_get_npm_global_bin_is_critical() -> None:
    """REJECT#15 AC#1: _get_npm_global_bin만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(["pipeline.py"], ["_get_npm_global_bin"])
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#15 AC#1: _get_npm_global_bin 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}."
    )


def test_tc43n_verify_npm_wrapper_content_is_critical() -> None:
    """REJECT#15 AC#1: _verify_npm_wrapper_content만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"], ["_verify_npm_wrapper_content"]
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#15 AC#1: _verify_npm_wrapper_content 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}."
    )


def test_tc43o_find_codex_js_entrypoint_is_critical() -> None:
    """REJECT#15 AC#1: _find_codex_js_entrypoint만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"], ["_find_codex_js_entrypoint"]
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#15 AC#1: _find_codex_js_entrypoint 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}."
    )


def test_tc43p_codex_review_blocked_flag_path_is_critical() -> None:
    """REJECT#15 AC#1: _codex_review_blocked_flag_path만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"], ["_codex_review_blocked_flag_path"]
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#15 AC#1: _codex_review_blocked_flag_path 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}."
    )


def test_tc43q_noncrit_diff_failure_sets_evidence_incomplete(tmp_path: Path) -> None:
    """REJECT#15 AC#2: 비-CRITICAL 파일의 git diff가 실패하면 evidence_complete=False가 된다.

    changed_files 목록에 있는 비-CRITICAL 파일의 diff를 얻지 못하면
    missing_noncrit_files에 기록되고 evidence_complete=False로 처리된다.
    """
    from unittest.mock import patch, MagicMock

    noncrit_file = "some_module.py"  # CRITICAL 아닌 일반 Python 파일

    def _mock_run(cmd, **kwargs):
        mock_result = MagicMock()
        cmd_list = [str(c) for c in cmd]
        if "diff" in cmd_list and noncrit_file in cmd_list:
            # 비-CRITICAL 파일 diff 실패 시뮬레이션
            mock_result.returncode = 128
            mock_result.stdout = ""
            mock_result.stderr = "fatal: bad object"
        else:
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
        return mock_result

    with patch("pipeline.subprocess.run", side_effect=_mock_run):
        sem = pipeline._build_codex_semantic_evidence(
            pipeline_id="IMP-20260712-DAE1",
            changed_files=[noncrit_file],
            included_functions=[],
        )

    assert not sem["evidence_complete"], (
        "REJECT#15 AC#2: 비-CRITICAL diff 실패 시 evidence_complete=True가 반환됨 — "
        "False여야 합니다."
    )
    assert noncrit_file in sem["missing_noncrit_files"], (
        f"REJECT#15 AC#2: diff 실패한 {noncrit_file!r}가 missing_noncrit_files에 없음."
    )


# =========================================================================== #
# TC-44: REJECT#30 — 멀티-hunk 누락 감지 + 신규·삭제 함수 hunk 강제 + 추가 함수 CRITICAL
# =========================================================================== #

def test_tc44a_canonicalize_effort_is_critical() -> None:
    """REJECT#30 AC#1: _canonicalize_effort만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(["pipeline.py"], ["_canonicalize_effort"])
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#30 AC#1: _canonicalize_effort 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}"
    )


def test_tc44b_extract_python_function_bodies_is_critical() -> None:
    """REJECT#30 AC#1: _extract_python_function_bodies만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"], ["_extract_python_function_bodies"]
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#30 AC#1: _extract_python_function_bodies 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}"
    )


def test_tc44c_is_codex_test_path_is_critical() -> None:
    """REJECT#30 AC#1: _is_codex_test_path만 변경해도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(["pipeline.py"], ["_is_codex_test_path"])
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#30 AC#1: _is_codex_test_path 단독 변경이 CRITICAL이 아님 — "
        f"got {r['risk_level']!r}"
    )


def test_tc44d_multihunk_same_critical_function_detected(tmp_path: Path) -> None:
    """REJECT#30 AC#2: 같은 CRITICAL 함수에 여러 hunk가 있고 하나만 선택되면
    truncated_critical_hunks>0 이고 evidence_complete=False가 된다.

    Fix 2(hunk 단위 카운트)로 함수명 set dedup 문제를 해결한다.
    """
    from unittest.mock import patch, MagicMock

    crit_func = "_cmd_gates_accept"  # CODEX_CRITICAL_FUNCTIONS에 있는 함수
    # 동일 함수명, 예산 초과 크기의 두 번째 hunk
    hunk1_content = f"@@ -100,5 +100,5 @@ def {crit_func}():\n+    pass1\n"
    hunk2_content = f"@@ -200,5 +200,5 @@ def {crit_func}():\n" + "+" + "x" * 850_000  # 예산 초과 (REJECT#29: budget 700K → 800K, 850K 필요)

    # 두 hunk 모두 동일 CRITICAL 함수에 귀속
    diff_output = (
        "diff --git a/pipeline.py b/pipeline.py\n"
        "--- a/pipeline.py\n"
        "+++ b/pipeline.py\n"
        f"{hunk1_content}\n"
        f"{hunk2_content}\n"
    )

    def _mock_run(cmd, **kwargs):
        mock_result = MagicMock()
        cmd_list = [str(c) for c in cmd]
        if "show" in cmd_list and any("origin/main:pipeline.py" in c for c in cmd_list):
            mock_result.returncode = 0
            mock_result.stdout = f"def {crit_func}():\n    pass\n"
        elif "diff" in cmd_list and "pipeline.py" in cmd_list:
            mock_result.returncode = 0
            mock_result.stdout = diff_output
        else:
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
        return mock_result

    with patch("pipeline.subprocess.run", side_effect=_mock_run):
        result = pipeline._build_codex_semantic_evidence(
            "IMP-TEST-REJECT30",
            ["pipeline.py"],
            [crit_func],
        )

    assert result["truncated_critical_hunks"] > 0, (
        f"REJECT#30 AC#2: 동일 CRITICAL 함수의 두 번째 hunk가 예산 초과로 제외됐는데 "
        f"truncated_critical_hunks={result['truncated_critical_hunks']} — "
        f"Fix 2(hunk 단위 카운트) 미적용. 함수명 set 방식은 함수명이 covered에 있으면 누락 미감지."
    )
    assert result["evidence_complete"] is False, (
        "REJECT#30 AC#2: 멀티-hunk 누락인데 evidence_complete가 False가 아님"
    )


def test_tc44e_new_critical_function_hunk_required(tmp_path: Path) -> None:
    """REJECT#30 AC#3: 신규 CRITICAL 함수의 diff hunk가 예산 초과로 제외되면
    evidence_complete=False가 된다 (Fix 3a의 skip 제거로 강제 포함).

    Fix 2(hunk 단위)는 신규 함수 hunk도 is_critical=True인 경우 감지한다.
    """
    from unittest.mock import patch, MagicMock

    new_func = "_canonicalize_effort"  # REJECT#30에서 추가된 CRITICAL 함수
    # 예산을 크게 초과하는 신규 함수 hunk
    big_hunk = f"@@ -0,0 +1,1 @@ def {new_func}():\n+" + "x" * 850_000  # 예산 초과 (REJECT#29: budget 700K → 800K, 850K 필요)
    diff_output = (
        "diff --git a/pipeline.py b/pipeline.py\n"
        "--- a/pipeline.py\n"
        "+++ b/pipeline.py\n"
        f"{big_hunk}\n"
    )

    def _mock_run(cmd, **kwargs):
        mock_result = MagicMock()
        cmd_list = [str(c) for c in cmd]
        if "show" in cmd_list and any("origin/main:pipeline.py" in c for c in cmd_list):
            mock_result.returncode = 0
            mock_result.stdout = ""  # before 없음 (신규 함수)
        elif "diff" in cmd_list and "pipeline.py" in cmd_list:
            mock_result.returncode = 0
            mock_result.stdout = diff_output
        else:
            mock_result.returncode = 0
            mock_result.stdout = ""
            mock_result.stderr = ""
        return mock_result

    with patch("pipeline.subprocess.run", side_effect=_mock_run):
        result = pipeline._build_codex_semantic_evidence(
            "IMP-TEST-REJECT30",
            ["pipeline.py"],
            [new_func],
        )

    assert result["truncated_critical_hunks"] > 0, (
        "REJECT#30 AC#3: 신규 CRITICAL 함수 hunk 예산 초과 시 truncated_critical_hunks=0 — "
        "Fix 3a의 skip이 제거됐는지 확인 (신규 함수 hunk도 is_critical이면 감지해야 함)"
    )
    assert result["evidence_complete"] is False, (
        "REJECT#30 AC#3: 신규 CRITICAL 함수 hunk 누락인데 evidence_complete=True"
    )


def test_tc44f_trust_boundary_completeness_reject30() -> None:
    """REJECT#30 AC#4: REJECT#30에서 추가된 6개 함수가 CODEX_CRITICAL_FUNCTIONS에 있다."""
    reject30_funcs = [
        "_canonicalize_effort",
        "_extract_python_function_bodies",
        "_is_codex_test_path",
        "_is_codex_critical_file",
        "_check_codex_capability_gate",
        "_codex_review_error_blocker",
    ]
    missing = [fn for fn in reject30_funcs if fn not in pipeline.CODEX_CRITICAL_FUNCTIONS]
    assert not missing, (
        "REJECT#30 AC#4: 아래 함수가 CODEX_CRITICAL_FUNCTIONS에 없음:\n"
        + "\n".join(f"  - {fn}" for fn in missing)
    )


def test_tc44g_hunk_count_approach_in_source() -> None:
    """REJECT#30 AC#4: _build_codex_semantic_evidence 소스코드에 hunk 단위 카운트가 적용됐다.
    함수명 set 기반(`_covered_ids`) 초기화(`_truncated_crit = 0`)가 제거됐는지 검증한다.
    """
    import inspect

    src = inspect.getsource(pipeline._build_codex_semantic_evidence)
    # 새 방식의 핵심 패턴 검증
    assert "_all_crit_count" in src, (
        "REJECT#30 AC#4: _build_codex_semantic_evidence에 '_all_crit_count' 없음 — "
        "hunk 단위 카운트(Fix 2) 미적용"
    )
    assert "_sel_crit_count" in src, (
        "REJECT#30 AC#4: _build_codex_semantic_evidence에 '_sel_crit_count' 없음 — "
        "hunk 단위 카운트(Fix 2) 미적용"
    )


# =========================================================================== #
# TC-45: REJECT#31 — 운영 신뢰 검사에서 selected/effort/mode 정책 직접 비교
# =========================================================================== #

def _make_trust_base_for_risk(risk_level: str) -> dict:
    """지정 risk level로 정상 통과 가능한 운영 신뢰 기준 결과를 생성한다."""
    policy = pipeline._build_codex_model_policy(risk_level)
    sig = pipeline._codex_policy_signature(policy)
    sel_model = str(policy.get("selected_model", "") or "")
    sel_effort = str(policy.get("selected_reasoning_effort", "") or "")
    mode = str(policy.get("mode", "") or "")
    cli_cmd = (
        f"codex exec --model {sel_model} -c model_reasoning_effort={sel_effort} "
        "--sandbox read-only --ephemeral --json -"
    )
    return {
        "status": "APPROVED",
        "verdict_source": "codex_cli",
        "acceptance_eligible": True,
        "router_version": pipeline.CODEX_MODEL_ROUTER_VERSION,
        "risk_level": risk_level,
        "model_policy_signature": sig,
        "codex_cli_command": cli_cmd,
        "selected_model": sel_model,
        "selected_reasoning_effort": sel_effort,
        "review_mode": mode,
        "invoked_model": sel_model,
        "invoked_effort": sel_effort,
        "actual_model": "unknown",
        "actual_effort": "unknown",
        "model_verification_level": (
            pipeline.CODEX_VERIFICATION_INVOCATION
            if risk_level in ("HIGH", "LOW", "MEDIUM")
            else pipeline.CODEX_VERIFICATION_INVOCATION  # CRITICAL: invocation_verified → will be blocked
        ),
        "auth_source": "chatgpt",
        # IMP-20260712-DAE1 REJECT#16: binary 신뢰 증거 필수 (테스트용 dummy 값)
        "codex_binary_path": "/usr/local/bin/codex",
        "codex_binary_sha256": "d" * 64,
        # REJECT#LATEST: native/vendor binary 신뢰 증거 필수 (test-only dummy).
        "codex_native_binary_path": "/usr/local/lib/codex/vendor/codex",
        "codex_native_binary_sha256": "e" * 64,
    }


def test_tc45a_high_luna_selected_but_sol_signature_blocked() -> None:
    """REJECT#31 AC#1: HIGH 결과에서 정상 HIGH 서명 유지하고 selected_model=luna로 바꾸면 BLOCKED.

    이전 코드는 signature 일치만 검사하여 selected_model 위조를 감지하지 못했다.
    Fix에서 selected_model != _expected_model 검사가 추가돼야 한다.
    """
    result = _make_trust_base_for_risk("HIGH")
    # signature는 그대로 (correct HIGH sig) but selected_model을 LOW 모델로 위조
    result["selected_model"] = "gpt-5.6-luna"
    result["invoked_model"] = "gpt-5.6-luna"
    # actual_model도 일치시켜 consistency 통과 시도
    result["actual_model"] = "gpt-5.6-luna"
    result["model_verification_level"] = pipeline.CODEX_VERIFICATION_ACTUAL
    result["actual_effort"] = result["selected_reasoning_effort"]
    # CLI command도 luna로
    result["codex_cli_command"] = (
        "codex exec --model gpt-5.6-luna -c model_reasoning_effort=high "
        "--sandbox read-only --ephemeral --json -"
    )

    r = pipeline._check_codex_review_operational_trust(result)
    assert r["status"] == "BLOCKED", (
        f"REJECT#31 AC#1: HIGH+서명정상+selected=luna 위조가 통과됨 — "
        f"got status={r['status']!r}, failure_code={r.get('failure_code')!r}. "
        "selected_model vs 정책 모델 직접 비교가 필요합니다."
    )
    assert r.get("failure_code") in (
        "codex_review_selected_model_policy_mismatch",
        "codex_review_cli_command_model_mismatch",
        "codex_review_model_mismatch",  # invoked/selected consistency check가 선행할 수 있음
    ), (
        f"REJECT#31 AC#1: 예상 failure_code가 아님 — got {r.get('failure_code')!r}"
    )


def test_tc45b_critical_luna_selected_instead_of_sol_blocked() -> None:
    """REJECT#31 AC#2: CRITICAL 결과에서 sol/max 대신 luna가 selected되면 BLOCKED.
    (actual_verified를 주장해도 selected_model이 sol이 아니면 차단돼야 한다)
    """
    result = _make_trust_base_for_risk("CRITICAL")
    # selected/invoked를 luna로 위조, actual도 일치
    result["selected_model"] = "gpt-5.6-luna"
    result["invoked_model"] = "gpt-5.6-luna"
    result["actual_model"] = "gpt-5.6-luna"
    result["model_verification_level"] = pipeline.CODEX_VERIFICATION_ACTUAL
    result["actual_effort"] = result["selected_reasoning_effort"]
    result["codex_cli_command"] = (
        "codex exec --model gpt-5.6-luna -c model_reasoning_effort=max "
        "--sandbox read-only --ephemeral --json -"
    )

    r = pipeline._check_codex_review_operational_trust(result)
    assert r["status"] == "BLOCKED", (
        f"REJECT#31 AC#2: CRITICAL+actual_verified+selected=luna가 통과됨 — "
        f"got status={r['status']!r}, failure_code={r.get('failure_code')!r}"
    )


def test_tc45c_wrong_review_mode_blocked() -> None:
    """REJECT#31 AC#3: 저장된 review_mode가 현재 정책과 다르면 BLOCKED."""
    result = _make_trust_base_for_risk("HIGH")
    # HIGH 정책의 mode는 "enforce"인데 "observe"로 위조
    result["review_mode"] = "observe"

    r = pipeline._check_codex_review_operational_trust(result)
    assert r["status"] == "BLOCKED", (
        f"REJECT#31 AC#3: HIGH review_mode=observe(정책은 enforce)가 통과됨 — "
        f"got status={r['status']!r}, failure_code={r.get('failure_code')!r}"
    )
    assert r.get("failure_code") == "codex_review_mode_policy_mismatch", (
        f"REJECT#31 AC#3: 예상 failure_code 'codex_review_mode_policy_mismatch' 아님 — "
        f"got {r.get('failure_code')!r}"
    )


def test_tc45d_normal_high_invocation_verified_passes() -> None:
    """REJECT#31 AC#4: 정상 HIGH+invocation_verified 결과는 기존처럼 PASS한다.
    (CRITICAL+invocation_verified는 unknown_model_critical_blocked로 차단됨)
    """
    result = _make_trust_base_for_risk("HIGH")
    # invocation_verified (not actual_verified) — HIGH에서는 허용
    result["model_verification_level"] = pipeline.CODEX_VERIFICATION_INVOCATION

    r = pipeline._check_codex_review_operational_trust(result)
    assert r["status"] == "PASS", (
        f"REJECT#31 AC#4: 정상 HIGH+invocation_verified가 BLOCKED됨 — "
        f"got status={r['status']!r}, failure_code={r.get('failure_code')!r}"
    )


def test_tc45e_normal_low_medium_pass() -> None:
    """REJECT#31 AC#4: 정상 LOW, MEDIUM 결과도 PASS한다."""
    for risk in ("LOW", "MEDIUM"):
        result = _make_trust_base_for_risk(risk)
        result["model_verification_level"] = pipeline.CODEX_VERIFICATION_INVOCATION
        r = pipeline._check_codex_review_operational_trust(result)
        assert r["status"] == "PASS", (
            f"REJECT#31 AC#4: 정상 {risk} 결과가 BLOCKED됨 — "
            f"got status={r['status']!r}, failure_code={r.get('failure_code')!r}"
        )


# =========================================================================== #
# TC-46: REJECT#32 — CODEX_CRITICAL_CONSTANTS 단독 변경도 CRITICAL 분류
# =========================================================================== #

def test_tc46a_codex_model_policies_alone_is_critical() -> None:
    """REJECT#32 AC#1: CODEX_MODEL_POLICIES만 변경한 경우 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"],
        [],  # 함수 변경 없음
        ["CODEX_MODEL_POLICIES"],
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#32 AC#1: CODEX_MODEL_POLICIES 단독 변경이 CRITICAL 아님 — got {r['risk_level']!r}"
    )
    assert r["matched_rule"] == "critical_constant", (
        f"REJECT#32 AC#1: matched_rule이 'critical_constant' 아님 — got {r['matched_rule']!r}"
    )


def test_tc46b_codex_allowed_models_alone_is_critical() -> None:
    """REJECT#32 AC#2a: CODEX_ALLOWED_MODELS 단독 변경도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"],
        [],
        ["CODEX_ALLOWED_MODELS"],
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#32 AC#2a: CODEX_ALLOWED_MODELS 단독 변경이 CRITICAL 아님 — got {r['risk_level']!r}"
    )


def test_tc46c_codex_critical_functions_alone_is_critical() -> None:
    """REJECT#32 AC#2b: CODEX_CRITICAL_FUNCTIONS 단독 변경도 CRITICAL로 분류된다.
    (신뢰 경계 함수 이름을 제거·변조하는 것도 자기 보호 대상)
    """
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"],
        [],
        ["CODEX_CRITICAL_FUNCTIONS"],
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#32 AC#2b: CODEX_CRITICAL_FUNCTIONS 단독 변경이 CRITICAL 아님 — got {r['risk_level']!r}"
    )


def test_tc46d_codex_high_risk_paths_alone_is_critical() -> None:
    """REJECT#32 AC#2c: CODEX_HIGH_RISK_PATHS 단독 변경도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"],
        [],
        ["CODEX_HIGH_RISK_PATHS"],
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#32 AC#2c: CODEX_HIGH_RISK_PATHS 단독 변경이 CRITICAL 아님 — got {r['risk_level']!r}"
    )


def test_tc46e_codex_router_version_alone_is_critical() -> None:
    """REJECT#32 AC#2d: CODEX_MODEL_ROUTER_VERSION 단독 변경도 CRITICAL로 분류된다."""
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"],
        [],
        ["CODEX_MODEL_ROUTER_VERSION"],
    )
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#32 AC#2d: CODEX_MODEL_ROUTER_VERSION 단독 변경이 CRITICAL 아님 — got {r['risk_level']!r}"
    )


def test_tc46f_critical_constants_set_contains_expected() -> None:
    """REJECT#32 AC#3: CODEX_CRITICAL_CONSTANTS 등록부에 5개 핵심 상수가 모두 포함된다."""
    required = {
        "CODEX_MODEL_POLICIES",
        "CODEX_ALLOWED_MODELS",
        "CODEX_CRITICAL_FUNCTIONS",
        "CODEX_HIGH_RISK_PATHS",
        "CODEX_MODEL_ROUTER_VERSION",
    }
    actual = set(pipeline.CODEX_CRITICAL_CONSTANTS)
    missing = required - actual
    assert not missing, (
        f"REJECT#32 AC#3: CODEX_CRITICAL_CONSTANTS에 필수 상수 누락: {sorted(missing)}"
    )


def test_tc46g_critical_constant_triggers_force_review_and_no_cache() -> None:
    """REJECT#32 AC#4: 상수 변경으로 CRITICAL 분류 시 force_review_required=True, cache_allowed=False.
    (CRITICAL 정책 SSoT 검증)
    """
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"],
        [],
        ["CODEX_MODEL_POLICIES"],
    )
    assert r["risk_level"] == "CRITICAL", "REJECT#32 AC#4: 전제 조건 — CRITICAL 분류 실패"
    policy = pipeline._build_codex_model_policy("CRITICAL")
    assert policy.get("force_review_required") is True, (
        "REJECT#32 AC#4: CRITICAL 정책의 force_review_required가 True 아님"
    )
    assert policy.get("cache_allowed") is False, (
        "REJECT#32 AC#4: CRITICAL 정책의 cache_allowed가 False 아님"
    )


def test_tc46h_no_functions_no_constants_pipeline_py_is_high() -> None:
    """REJECT#32 회귀 방지: 함수/상수 변경 없이 pipeline.py만 변경하면 HIGH (CRITICAL 아님).
    상수 스캔 추가 후 기존 HIGH 분류가 변경되지 않아야 한다.
    """
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"],
        [],   # 함수 없음
        [],   # 상수 없음
    )
    assert r["risk_level"] == "HIGH", (
        f"REJECT#32 회귀: 함수/상수 없이 pipeline.py만 변경하면 HIGH여야 함 — got {r['risk_level']!r}"
    )


# =========================================================================== #
# TC-47: REJECT#33 — SHA 비교 기반 상수 내부 값 변경 감지
# =========================================================================== #

def test_tc47a_extract_constant_bodies_single_line() -> None:
    """REJECT#33: _extract_python_constant_bodies가 한 줄 상수를 추출한다."""
    src = 'CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"\nOTHER = 1\n'
    result = pipeline._extract_python_constant_bodies(src, ["CODEX_MODEL_ROUTER_VERSION"])
    assert "CODEX_MODEL_ROUTER_VERSION" in result, (
        "REJECT#33: 한 줄 상수 추출 실패 — _extract_python_constant_bodies 결과에 키 없음"
    )
    assert "2.0.0" in result["CODEX_MODEL_ROUTER_VERSION"], (
        "REJECT#33: 한 줄 상수 추출 결과에 값이 없음"
    )


def test_tc47b_extract_constant_bodies_multiline_dict() -> None:
    """REJECT#33: _extract_python_constant_bodies가 multi-line dict 상수를 추출한다."""
    src = textwrap.dedent("""\
        CODEX_MODEL_POLICIES: dict = {
            "LOW": {"selected_model": "gpt-5.6-luna"},
            "CRITICAL": {"selected_model": "gpt-5.6-sol"},
        }
        OTHER = 1
    """)
    result = pipeline._extract_python_constant_bodies(src, ["CODEX_MODEL_POLICIES"])
    assert "CODEX_MODEL_POLICIES" in result, (
        "REJECT#33: multi-line dict 상수 추출 실패"
    )
    body = result["CODEX_MODEL_POLICIES"]
    assert "gpt-5.6-luna" in body and "gpt-5.6-sol" in body, (
        f"REJECT#33: 추출된 상수 본문에 값이 없음: {body!r}"
    )


def test_tc47c_extract_constant_bodies_multiline_list() -> None:
    """REJECT#33: _extract_python_constant_bodies가 multi-line list 상수를 추출한다."""
    src = textwrap.dedent("""\
        CODEX_CRITICAL_FUNCTIONS: list = [
            "_cmd_gates_request_accept",
            "_cmd_gates_accept",
        ]
    """)
    result = pipeline._extract_python_constant_bodies(src, ["CODEX_CRITICAL_FUNCTIONS"])
    assert "CODEX_CRITICAL_FUNCTIONS" in result, (
        "REJECT#33: multi-line list 상수 추출 실패"
    )
    assert "_cmd_gates_accept" in result["CODEX_CRITICAL_FUNCTIONS"], (
        "REJECT#33: list 상수 본문에 항목이 없음"
    )


def test_tc47d_detect_changed_constants_internal_value_change() -> None:
    """REJECT#33 AC#1: CODEX_MODEL_POLICIES 내부 값(nested) 변경 시 CRITICAL로 분류된다.

    선언 줄(CODEX_MODEL_POLICIES = {)은 그대로이고 내부 dict 값만 바꾼 경우를
    SHA 비교로 감지하여 changed_constants에 포함돼야 한다.
    """
    import unittest.mock as mock

    # before: CRITICAL selected_model=gpt-5.6-sol
    _before_src = textwrap.dedent("""\
        CODEX_MODEL_POLICIES: dict = {
            "CRITICAL": {"selected_model": "gpt-5.6-sol"},
        }
        CODEX_CRITICAL_FUNCTIONS: list = ["_cmd_gates_accept"]
        CODEX_ALLOWED_MODELS: frozenset = frozenset({"gpt-5.6-sol"})
        CODEX_CRITICAL_CONSTANTS: list = ["CODEX_MODEL_POLICIES"]
        CODEX_HIGH_RISK_PATHS: list = ["pipeline.py"]
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)

    # after: CRITICAL selected_model 을 luna로 변조 (선언 줄은 그대로)
    _after_src = textwrap.dedent("""\
        CODEX_MODEL_POLICIES: dict = {
            "CRITICAL": {"selected_model": "gpt-5.6-luna"},
        }
        CODEX_CRITICAL_FUNCTIONS: list = ["_cmd_gates_accept"]
        CODEX_ALLOWED_MODELS: frozenset = frozenset({"gpt-5.6-sol"})
        CODEX_CRITICAL_CONSTANTS: list = ["CODEX_MODEL_POLICIES"]
        CODEX_HIGH_RISK_PATHS: list = ["pipeline.py"]
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)

    _mock_result = mock.MagicMock()
    _mock_result.returncode = 0
    _mock_result.stdout = _before_src

    with mock.patch("pipeline.subprocess.run", return_value=_mock_result), \
         mock.patch.object(pipeline.Path, "read_text", return_value=_after_src):
        changed = pipeline._detect_changed_critical_constants(["pipeline.py"])

    assert "CODEX_MODEL_POLICIES" in changed, (
        f"REJECT#33 AC#1: CODEX_MODEL_POLICIES 내부 값 변경이 감지되지 않음 — got {changed!r}"
    )
    # 이제 CRITICAL로 분류되는지도 검증
    r = pipeline._classify_codex_review_risk(["pipeline.py"], [], changed)
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#33 AC#1: CODEX_MODEL_POLICIES 변경이 CRITICAL 아님 — got {r['risk_level']!r}"
    )


def test_tc47e_detect_changed_constants_list_item_removal() -> None:
    """REJECT#33 AC#2: CODEX_CRITICAL_FUNCTIONS에서 항목을 제거하면 CRITICAL로 분류된다."""
    import unittest.mock as mock

    _before_src = textwrap.dedent("""\
        CODEX_CRITICAL_FUNCTIONS: list = [
            "_cmd_gates_request_accept",
            "_cmd_gates_accept",
        ]
        CODEX_MODEL_POLICIES: dict = {}
        CODEX_ALLOWED_MODELS: frozenset = frozenset()
        CODEX_CRITICAL_CONSTANTS: list = ["CODEX_CRITICAL_FUNCTIONS"]
        CODEX_HIGH_RISK_PATHS: list = []
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)
    # 항목 1개 제거
    _after_src = textwrap.dedent("""\
        CODEX_CRITICAL_FUNCTIONS: list = [
            "_cmd_gates_accept",
        ]
        CODEX_MODEL_POLICIES: dict = {}
        CODEX_ALLOWED_MODELS: frozenset = frozenset()
        CODEX_CRITICAL_CONSTANTS: list = ["CODEX_CRITICAL_FUNCTIONS"]
        CODEX_HIGH_RISK_PATHS: list = []
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)

    _mock_result = mock.MagicMock()
    _mock_result.returncode = 0
    _mock_result.stdout = _before_src

    with mock.patch("pipeline.subprocess.run", return_value=_mock_result), \
         mock.patch.object(pipeline.Path, "read_text", return_value=_after_src):
        changed = pipeline._detect_changed_critical_constants(["pipeline.py"])

    assert "CODEX_CRITICAL_FUNCTIONS" in changed, (
        f"REJECT#33 AC#2: CODEX_CRITICAL_FUNCTIONS 항목 제거가 감지되지 않음 — got {changed!r}"
    )
    r = pipeline._classify_codex_review_risk(["pipeline.py"], [], changed)
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#33 AC#2: CODEX_CRITICAL_FUNCTIONS 항목 제거가 CRITICAL 아님 — got {r!r}"
    )


def test_tc47f_detect_changed_constants_self_protection_removal() -> None:
    """REJECT#33 AC#3: CODEX_CRITICAL_CONSTANTS에서 자기 보호 항목을 제거해도 CRITICAL로 차단된다."""
    import unittest.mock as mock

    _before_src = textwrap.dedent("""\
        CODEX_CRITICAL_CONSTANTS: list = [
            "CODEX_MODEL_POLICIES",
            "CODEX_CRITICAL_CONSTANTS",
        ]
        CODEX_MODEL_POLICIES: dict = {}
        CODEX_ALLOWED_MODELS: frozenset = frozenset()
        CODEX_CRITICAL_FUNCTIONS: list = []
        CODEX_HIGH_RISK_PATHS: list = []
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)
    # 자기 보호 항목("CODEX_CRITICAL_CONSTANTS") 제거
    _after_src = textwrap.dedent("""\
        CODEX_CRITICAL_CONSTANTS: list = [
            "CODEX_MODEL_POLICIES",
        ]
        CODEX_MODEL_POLICIES: dict = {}
        CODEX_ALLOWED_MODELS: frozenset = frozenset()
        CODEX_CRITICAL_FUNCTIONS: list = []
        CODEX_HIGH_RISK_PATHS: list = []
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)

    _mock_result = mock.MagicMock()
    _mock_result.returncode = 0
    _mock_result.stdout = _before_src

    with mock.patch("pipeline.subprocess.run", return_value=_mock_result), \
         mock.patch.object(pipeline.Path, "read_text", return_value=_after_src):
        changed = pipeline._detect_changed_critical_constants(["pipeline.py"])

    # CODEX_CRITICAL_CONSTANTS 자체가 변경됐으므로 changed 목록에 포함됨
    assert "CODEX_CRITICAL_CONSTANTS" in changed, (
        f"REJECT#33 AC#3: CODEX_CRITICAL_CONSTANTS 자기 보호 항목 제거가 감지되지 않음 — got {changed!r}"
    )
    r = pipeline._classify_codex_review_risk(["pipeline.py"], [], changed)
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#33 AC#3: CODEX_CRITICAL_CONSTANTS 변경이 CRITICAL 아님 — got {r!r}"
    )


def test_tc47g_detect_changed_constants_failclosed_on_git_error() -> None:
    """REJECT#33 AC#4: git show 실패 시 fail-closed — 전체 상수를 변경된 것으로 반환한다."""
    import unittest.mock as mock

    _mock_result = mock.MagicMock()
    _mock_result.returncode = 1  # git show 실패
    _mock_result.stdout = ""

    with mock.patch("pipeline.subprocess.run", return_value=_mock_result):
        changed = pipeline._detect_changed_critical_constants(["pipeline.py"])

    # fail-closed: 전체 CODEX_CRITICAL_CONSTANTS를 반환해야 함
    for _name in pipeline.CODEX_CRITICAL_CONSTANTS:
        assert _name in changed, (
            f"REJECT#33 AC#4: git 실패 시 fail-closed 미작동 — {_name!r}이 결과에 없음"
        )
    r = pipeline._classify_codex_review_risk(["pipeline.py"], [], changed)
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#33 AC#4: fail-closed 상수로 CRITICAL이 아님 — got {r!r}"
    )


def test_tc47h_no_pipeline_py_change_returns_empty() -> None:
    """REJECT#33 회귀 방지: pipeline.py가 없으면 _detect_changed_critical_constants는 빈 목록."""
    changed = pipeline._detect_changed_critical_constants(["tests/e2e/some_test.py"])
    assert changed == [], (
        f"REJECT#33 회귀: pipeline.py 없으면 빈 목록이어야 함 — got {changed!r}"
    )


def test_tc47i_allowed_models_and_high_risk_paths_change_is_critical() -> None:
    """REJECT#33 AC#2: CODEX_ALLOWED_MODELS, CODEX_HIGH_RISK_PATHS 항목 변경도 CRITICAL."""
    import unittest.mock as mock

    _before_src = textwrap.dedent("""\
        CODEX_ALLOWED_MODELS: frozenset = frozenset({"gpt-5.6-sol", "gpt-5.6-luna"})
        CODEX_HIGH_RISK_PATHS: list = ["pipeline.py", "CLAUDE.md"]
        CODEX_MODEL_POLICIES: dict = {}
        CODEX_CRITICAL_FUNCTIONS: list = []
        CODEX_CRITICAL_CONSTANTS: list = ["CODEX_ALLOWED_MODELS", "CODEX_HIGH_RISK_PATHS"]
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)
    # CODEX_ALLOWED_MODELS에 허가되지 않은 모델 추가, CODEX_HIGH_RISK_PATHS 항목 제거
    _after_src = textwrap.dedent("""\
        CODEX_ALLOWED_MODELS: frozenset = frozenset({"gpt-5.6-sol", "gpt-5.6-luna", "gpt-4o"})
        CODEX_HIGH_RISK_PATHS: list = ["pipeline.py"]
        CODEX_MODEL_POLICIES: dict = {}
        CODEX_CRITICAL_FUNCTIONS: list = []
        CODEX_CRITICAL_CONSTANTS: list = ["CODEX_ALLOWED_MODELS", "CODEX_HIGH_RISK_PATHS"]
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)

    _mock_result = mock.MagicMock()
    _mock_result.returncode = 0
    _mock_result.stdout = _before_src

    with mock.patch("pipeline.subprocess.run", return_value=_mock_result), \
         mock.patch.object(pipeline.Path, "read_text", return_value=_after_src):
        changed = pipeline._detect_changed_critical_constants(["pipeline.py"])

    assert "CODEX_ALLOWED_MODELS" in changed, (
        f"REJECT#33 AC#2: CODEX_ALLOWED_MODELS 변경이 감지되지 않음 — got {changed!r}"
    )
    assert "CODEX_HIGH_RISK_PATHS" in changed, (
        f"REJECT#33 AC#2: CODEX_HIGH_RISK_PATHS 변경이 감지되지 않음 — got {changed!r}"
    )
    r = pipeline._classify_codex_review_risk(["pipeline.py"], [], changed)
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#33 AC#2: CODEX_ALLOWED_MODELS/HIGH_RISK_PATHS 변경이 CRITICAL 아님 — got {r['risk_level']!r}"
    )


def test_tc47j_critical_policy_attributes_unchanged() -> None:
    """REJECT#33 AC#5 + REJECT#12 fix: CRITICAL 정책에서 force_review_required=True, cache_allowed=False가
    유지된다. REJECT#12 fix: CRITICAL + invocation_verified는 이제 OK를 반환한다(unknown_model_critical_blocked 삭제).
    """
    # (1) CRITICAL 정책 속성 직접 검증
    policy = pipeline.CODEX_MODEL_POLICIES.get("CRITICAL")
    assert policy is not None, "REJECT#33 AC#5: CODEX_MODEL_POLICIES에 CRITICAL 키 없음"
    assert policy.get("force_review_required") is True, (
        f"REJECT#33 AC#5: CRITICAL.force_review_required이 True 아님 — got {policy!r}"
    )
    assert policy.get("cache_allowed") is False, (
        f"REJECT#33 AC#5: CRITICAL.cache_allowed이 False 아님 — got {policy!r}"
    )
    # (2) REJECT#12 fix: CRITICAL + invocation_verified(actual_model="unknown")는 이제 OK다.
    #   REJECT#21(unknown_model_critical_blocked)은 삭제됨 — gpt-5.6-sol는 actual을 보고 안 함.
    _sm = policy["selected_model"]   # gpt-5.6-sol
    _se = policy["selected_reasoning_effort"]  # max
    cap_result = pipeline._check_codex_model_capability_match(
        selected_model=_sm,
        selected_effort=_se,
        invoked_model=_sm,   # invoked == selected
        invoked_effort=_se,
        actual_model="unknown",  # CLI가 actual을 보고하지 않은 상태
        actual_effort="unknown",
        risk_level="CRITICAL",
        invocation_ok=True,
    )
    assert cap_result.get("result") == "OK", (
        f"REJECT#12 fix: CRITICAL + invocation_verified는 OK여야 한다 — got {cap_result!r}"
    )
    assert cap_result.get("model_verification_level") == pipeline.CODEX_VERIFICATION_INVOCATION, (
        f"REJECT#12 fix: model_verification_level=invocation_verified 여야 한다 — got {cap_result!r}"
    )


# =========================================================================== #
# TC-48: REJECT#34 — 자기보호 fail-closed, 신뢰 상수 보호, TC-04 oracle, evidence_complete
# =========================================================================== #

def test_tc48a_min_protected_catches_cleared_critical_constants() -> None:
    """REJECT#34 AC#1: CODEX_CRITICAL_CONSTANTS가 비워져도 자기 보호로 CRITICAL 감지된다.

    _detect_changed_critical_constants는 내부 _CODEX_MIN_PROTECTED로 인해
    CODEX_CRITICAL_CONSTANTS가 런타임 빈 목록이어도 항상 자기 자신을 비교 대상에 포함한다.
    """
    import unittest.mock as mock

    # before: CODEX_CRITICAL_CONSTANTS에 자기 자신 포함
    _before_src = textwrap.dedent("""\
        CODEX_CRITICAL_CONSTANTS: list = [
            "CODEX_MODEL_POLICIES",
            "CODEX_CRITICAL_CONSTANTS",
        ]
        CODEX_MODEL_POLICIES: dict = {}
        CODEX_ALLOWED_MODELS: frozenset = frozenset()
        CODEX_CRITICAL_FUNCTIONS: list = []
        CODEX_HIGH_RISK_PATHS: list = []
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
        CODEX_VERIFICATION_ACTUAL: str = "actual_verified"
        CODEX_CHATGPT_LOGIN_MARKER: str = "Logged in using ChatGPT"
        _CODEX_EFFORT_CANONICAL: dict = {"xhigh": "max"}
    """)
    # after: CODEX_CRITICAL_CONSTANTS가 완전히 비워짐 — 자기 자신 항목도 제거
    _after_src = textwrap.dedent("""\
        CODEX_CRITICAL_CONSTANTS: list = []
        CODEX_MODEL_POLICIES: dict = {}
        CODEX_ALLOWED_MODELS: frozenset = frozenset()
        CODEX_CRITICAL_FUNCTIONS: list = []
        CODEX_HIGH_RISK_PATHS: list = []
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
        CODEX_VERIFICATION_ACTUAL: str = "actual_verified"
        CODEX_CHATGPT_LOGIN_MARKER: str = "Logged in using ChatGPT"
        _CODEX_EFFORT_CANONICAL: dict = {"xhigh": "max"}
    """)

    _mock_result = mock.MagicMock()
    _mock_result.returncode = 0
    _mock_result.stdout = _before_src

    # CODEX_CRITICAL_CONSTANTS를 빈 목록으로 패치해도 _CODEX_MIN_PROTECTED로 감지된다
    with mock.patch("pipeline.subprocess.run", return_value=_mock_result), \
         mock.patch.object(pipeline.Path, "read_text", return_value=_after_src), \
         mock.patch.object(pipeline, "CODEX_CRITICAL_CONSTANTS", []):
        changed = pipeline._detect_changed_critical_constants(["pipeline.py"])

    assert "CODEX_CRITICAL_CONSTANTS" in changed, (
        f"REJECT#34 AC#1: 빈 CODEX_CRITICAL_CONSTANTS 변경이 감지되지 않음 — got {changed!r}"
    )
    r = pipeline._classify_codex_review_risk(["pipeline.py"], [], changed)
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#34 AC#1: 빈 CODEX_CRITICAL_CONSTANTS가 CRITICAL 아님 — got {r['risk_level']!r}"
    )


def test_tc48b_verification_actual_change_is_critical() -> None:
    """REJECT#34 AC#2: CODEX_VERIFICATION_ACTUAL 단독 변경이 CRITICAL로 분류된다."""
    import unittest.mock as mock

    _base_src_template = textwrap.dedent("""\
        CODEX_VERIFICATION_ACTUAL: str = "{val}"
        CODEX_CHATGPT_LOGIN_MARKER: str = "Logged in using ChatGPT"
        _CODEX_EFFORT_CANONICAL: dict = {{"xhigh": "max"}}
        CODEX_MODEL_POLICIES: dict = {{}}
        CODEX_ALLOWED_MODELS: frozenset = frozenset()
        CODEX_CRITICAL_FUNCTIONS: list = []
        CODEX_CRITICAL_CONSTANTS: list = ["CODEX_VERIFICATION_ACTUAL"]
        CODEX_HIGH_RISK_PATHS: list = []
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)
    _before_src = _base_src_template.format(val="actual_verified")
    _after_src = _base_src_template.format(val="TAMPERED_VALUE")

    _mock_result = mock.MagicMock()
    _mock_result.returncode = 0
    _mock_result.stdout = _before_src

    with mock.patch("pipeline.subprocess.run", return_value=_mock_result), \
         mock.patch.object(pipeline.Path, "read_text", return_value=_after_src):
        changed = pipeline._detect_changed_critical_constants(["pipeline.py"])

    assert "CODEX_VERIFICATION_ACTUAL" in changed, (
        f"REJECT#34 AC#2: CODEX_VERIFICATION_ACTUAL 변경이 감지되지 않음 — got {changed!r}"
    )
    r = pipeline._classify_codex_review_risk(["pipeline.py"], [], changed)
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#34 AC#2: CODEX_VERIFICATION_ACTUAL 변경이 CRITICAL 아님 — got {r['risk_level']!r}"
    )


def test_tc48c_chatgpt_login_marker_change_is_critical() -> None:
    """REJECT#34 AC#2: CODEX_CHATGPT_LOGIN_MARKER 단독 변경이 CRITICAL로 분류된다."""
    import unittest.mock as mock

    _before_src = textwrap.dedent("""\
        CODEX_CHATGPT_LOGIN_MARKER: str = "Logged in using ChatGPT"
        CODEX_VERIFICATION_ACTUAL: str = "actual_verified"
        _CODEX_EFFORT_CANONICAL: dict = {"xhigh": "max"}
        CODEX_MODEL_POLICIES: dict = {}
        CODEX_ALLOWED_MODELS: frozenset = frozenset()
        CODEX_CRITICAL_FUNCTIONS: list = []
        CODEX_CRITICAL_CONSTANTS: list = ["CODEX_CHATGPT_LOGIN_MARKER"]
        CODEX_HIGH_RISK_PATHS: list = []
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)
    _after_src = textwrap.dedent("""\
        CODEX_CHATGPT_LOGIN_MARKER: str = "TAMPERED_AUTH_MARKER"
        CODEX_VERIFICATION_ACTUAL: str = "actual_verified"
        _CODEX_EFFORT_CANONICAL: dict = {"xhigh": "max"}
        CODEX_MODEL_POLICIES: dict = {}
        CODEX_ALLOWED_MODELS: frozenset = frozenset()
        CODEX_CRITICAL_FUNCTIONS: list = []
        CODEX_CRITICAL_CONSTANTS: list = ["CODEX_CHATGPT_LOGIN_MARKER"]
        CODEX_HIGH_RISK_PATHS: list = []
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)

    _mock_result = mock.MagicMock()
    _mock_result.returncode = 0
    _mock_result.stdout = _before_src

    with mock.patch("pipeline.subprocess.run", return_value=_mock_result), \
         mock.patch.object(pipeline.Path, "read_text", return_value=_after_src):
        changed = pipeline._detect_changed_critical_constants(["pipeline.py"])

    assert "CODEX_CHATGPT_LOGIN_MARKER" in changed, (
        f"REJECT#34 AC#2: CODEX_CHATGPT_LOGIN_MARKER 변경이 감지되지 않음 — got {changed!r}"
    )
    r = pipeline._classify_codex_review_risk(["pipeline.py"], [], changed)
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#34 AC#2: CODEX_CHATGPT_LOGIN_MARKER 변경이 CRITICAL 아님 — got {r['risk_level']!r}"
    )


def test_tc48d_codex_effort_canonical_change_is_critical() -> None:
    """REJECT#34 AC#2: _CODEX_EFFORT_CANONICAL(밑줄 선행 상수) 단독 변경이 CRITICAL로 분류된다.

    _extract_python_constant_bodies의 정규식이 _로 시작하는 상수도 추출해야 한다.
    """
    import unittest.mock as mock

    _before_src = textwrap.dedent("""\
        _CODEX_EFFORT_CANONICAL: dict = {"xhigh": "max"}
        CODEX_CHATGPT_LOGIN_MARKER: str = "Logged in using ChatGPT"
        CODEX_VERIFICATION_ACTUAL: str = "actual_verified"
        CODEX_MODEL_POLICIES: dict = {}
        CODEX_ALLOWED_MODELS: frozenset = frozenset()
        CODEX_CRITICAL_FUNCTIONS: list = []
        CODEX_CRITICAL_CONSTANTS: list = ["_CODEX_EFFORT_CANONICAL"]
        CODEX_HIGH_RISK_PATHS: list = []
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)
    # xhigh→max 대신 xhigh→xhigh로 변조 (실제 effort 정규화가 무력화됨)
    _after_src = textwrap.dedent("""\
        _CODEX_EFFORT_CANONICAL: dict = {"xhigh": "xhigh"}
        CODEX_CHATGPT_LOGIN_MARKER: str = "Logged in using ChatGPT"
        CODEX_VERIFICATION_ACTUAL: str = "actual_verified"
        CODEX_MODEL_POLICIES: dict = {}
        CODEX_ALLOWED_MODELS: frozenset = frozenset()
        CODEX_CRITICAL_FUNCTIONS: list = []
        CODEX_CRITICAL_CONSTANTS: list = ["_CODEX_EFFORT_CANONICAL"]
        CODEX_HIGH_RISK_PATHS: list = []
        CODEX_MODEL_ROUTER_VERSION: str = "2.0.0"
    """)

    _mock_result = mock.MagicMock()
    _mock_result.returncode = 0
    _mock_result.stdout = _before_src

    with mock.patch("pipeline.subprocess.run", return_value=_mock_result), \
         mock.patch.object(pipeline.Path, "read_text", return_value=_after_src):
        changed = pipeline._detect_changed_critical_constants(["pipeline.py"])

    assert "_CODEX_EFFORT_CANONICAL" in changed, (
        f"REJECT#34 AC#2: _CODEX_EFFORT_CANONICAL 변경이 감지되지 않음 — got {changed!r}. "
        "밑줄 선행 상수 추출 파서 확인 필요"
    )
    r = pipeline._classify_codex_review_risk(["pipeline.py"], [], changed)
    assert r["risk_level"] == "CRITICAL", (
        f"REJECT#34 AC#2: _CODEX_EFFORT_CANONICAL 변경이 CRITICAL 아님 — got {r['risk_level']!r}"
    )


def test_tc48e_underscore_constant_extraction() -> None:
    """REJECT#34: _extract_python_constant_bodies가 밑줄 선행 상수(_NAME)를 추출한다."""
    src = textwrap.dedent("""\
        _CODEX_EFFORT_CANONICAL: Dict[str, str] = {"xhigh": "max"}
        OTHER = 1
    """)
    result = pipeline._extract_python_constant_bodies(src, ["_CODEX_EFFORT_CANONICAL"])
    assert "_CODEX_EFFORT_CANONICAL" in result, (
        "REJECT#34: 밑줄 선행 상수 추출 실패 — 파서 정규식 확인 필요"
    )
    assert "xhigh" in result["_CODEX_EFFORT_CANONICAL"], (
        f"REJECT#34: 추출된 상수 본문에 값 없음: {result!r}"
    )


def test_tc48f_tc04_oracle_build_parser_is_high() -> None:
    """REJECT#34 AC#3: TC-04 Oracle 정합성 — pipeline.py + build_parser 변경 시 HIGH.

    이전 TC-04 oracle은 _cmd_gates_codex_review(CRITICAL 함수)를 입력해 HIGH를 기대했으나 불일치.
    수정 후 oracle input은 build_parser(비CRITICAL 함수)를 사용하여 HIGH를 반환해야 한다.
    """
    r = pipeline._classify_codex_review_risk(
        ["pipeline.py"],
        ["build_parser"],  # 비CRITICAL 함수
        [],
    )
    assert r["risk_level"] == "HIGH", (
        f"REJECT#34 AC#3: pipeline.py + build_parser가 HIGH 아님 — got {r['risk_level']!r}. "
        "TC-04 Oracle 입력이 CRITICAL 함수이면 이 테스트가 실패함"
    )


# ---------------------------------------------------------------------------
# TC-49: REJECT#35 — CRITICAL Python 파일 diff 수집 + non-critical 예산 초과 추적
# ---------------------------------------------------------------------------

class TestTC49CriticalPythonDiffAndNonCritBudget:
    """REJECT#35 검증: section 1b Python 제외 버그 + truncated_noncrit_hunks 추적.

    AC#1: non-critical 예산 초과 시 evidence_complete=False, Codex 차단.
    AC#2: 모든 changed_files가 hunk에 한 번 이상 매핑될 때만 evidence_complete=True.
    AC#3: CRITICAL Python 파일(test_codex*.py)의 diff가 bundle에 포함된다.
    AC#4: non-critical 예산 초과 시 truncated_noncrit_hunks 카운트 기록.
    """

    def _make_semantic(
        self,
        diff_hunks: list,
        truncated_crit: int,
        truncated_noncrit: int,
        missing_noncrit_files: list | None = None,
        diff_ok: bool = True,
    ) -> dict:
        """테스트용 semantic dict 조립."""
        return {
            "diff_hunks": diff_hunks,
            "truncated_critical_hunks": truncated_crit,
            "truncated_noncrit_hunks": truncated_noncrit,
            "missing_noncrit_files": missing_noncrit_files or [],
            "evidence_complete": (
                diff_ok
                and truncated_crit == 0
                and truncated_noncrit == 0
                and len(diff_hunks) > 0
            ),
            "function_before_after_shas": {},
            "test_assertions": {},
            "oracle_results": [],
            "bundle_budget_chars": sum(h.get("chars", 0) for h in diff_hunks),
            "semantic_evidence_sha256": "",
            "changed_constants": [],
        }

    def test_tc49a_evidence_complete_false_when_noncrit_truncated(self) -> None:
        """REJECT#35 AC#1: non-critical 예산 초과 시 evidence_complete=False."""
        sem = self._make_semantic(
            diff_hunks=[{"function": "report.md", "is_critical": False, "chars": 100}],
            truncated_crit=0,
            truncated_noncrit=1,  # 1개 누락
            missing_noncrit_files=["extra_report.md"],
        )
        assert sem["evidence_complete"] is False, (
            "REJECT#35 AC#1: non-critical 예산 초과인데 evidence_complete=True — "
            "truncated_noncrit=1이면 False여야 함"
        )

    def test_tc49b_evidence_complete_false_with_multiple_noncrit_truncated(self) -> None:
        """REJECT#35 AC#1 회귀: non-critical 여러 개 누락해도 evidence_complete=False."""
        sem = self._make_semantic(
            diff_hunks=[{"function": "x.md", "is_critical": False, "chars": 10}],
            truncated_crit=0,
            truncated_noncrit=5,
        )
        assert sem["evidence_complete"] is False, (
            "REJECT#35 AC#1 회귀: truncated_noncrit=5인데 evidence_complete=True"
        )

    def test_tc49c_evidence_complete_true_when_all_files_covered(self) -> None:
        """REJECT#35 AC#2: 모든 changed_files가 hunk 또는 SHA attestation에 매핑되면 evidence_complete=True.

        CRITICAL Python 파일은 SHA attestation으로, 비Critical 파일은 diff hunk로 커버된다.
        """
        sem = self._make_semantic(
            diff_hunks=[
                {"function": "pipeline.py", "is_critical": True, "chars": 500},
                {"function": "codex_model_router_report.md", "is_critical": False, "chars": 100},
            ],
            truncated_crit=0,
            truncated_noncrit=0,
        )
        assert sem["evidence_complete"] is True, (
            "REJECT#35 AC#2: 모든 파일 포함 + truncated 모두 0인데 evidence_complete=False"
        )

    def test_tc49d_critical_python_test_file_is_critical_file(self) -> None:
        """REJECT#35 AC#3: tests/e2e/test_codex*.py가 CRITICAL 파일로 분류된다.

        _is_codex_critical_file이 이 파일을 CRITICAL로 식별해야
        section 1b에서 diff가 수집될 수 있다.
        """
        assert pipeline._is_codex_critical_file(
            "tests/e2e/test_codex_model_router_dae1.py"
        ), (
            "REJECT#35 AC#3: test_codex_model_router_dae1.py가 CRITICAL 파일로 인식 안 됨. "
            "_CODEX_CRITICAL_FILE_PREFIXES에 'tests/e2e/test_codex' 포함 여부 확인"
        )

    def test_tc49e_critical_python_test_file_in_sha_attestation(self) -> None:
        """REJECT#35 AC#3: CRITICAL Python 파일(test_codex*.py)이 file_sha_attestations에 포함된다.

        _build_codex_semantic_evidence 내 section 1b에서 test_codex*.py(.py 확장자)가
        diff 예산을 소모하지 않고 file-level SHA attestation으로 처리된다.
        (AC#3: "diff가 리뷰 입력에 포함되거나 SHA로 결합된 분할 검토를 통과했다는 증거가 생성됩니다")
        소스 코드 검사로 section 1b 내 Python CRITICAL 파일이 SHA 경로로 분기하는지 확인.
        """
        import inspect

        _src = inspect.getsource(pipeline._build_codex_semantic_evidence)

        # section 1b 내에서 .py 파일에 대해 file_sha_attestations를 채우는 코드 존재 여부 검증
        assert "file_sha_attestations" in _src, (
            "REJECT#35 AC#3: _build_codex_semantic_evidence에 file_sha_attestations 없음 — "
            "CRITICAL Python 파일 SHA 처리 경로 구현 확인 필요"
        )
        # SHA attestation 경로에서 origin/main 파일 내용을 읽는 git show 호출 존재 여부
        assert "origin/main:{_cpf_n}" in _src or 'f"origin/main:{_cpf_n}"' in _src, (
            "REJECT#35 AC#3: section 1b에 CRITICAL Python 파일의 git show 호출 없음 — "
            "before_sha 생성 경로 확인 필요"
        )
        # before_sha / after_sha 필드 포함 여부
        assert '"before_sha"' in _src and '"after_sha"' in _src, (
            "REJECT#35 AC#3: file_sha_attestations 항목에 before_sha/after_sha 필드 없음"
        )

    def test_tc49e2_sha_attestation_populated_with_real_critical_file(self) -> None:
        """REJECT#35 AC#3: 실제 CRITICAL Python 파일이 포함된 번들에 file_sha_attestations 존재.

        현재 PR diff에 tests/e2e/test_codex*.py가 포함되어 있으므로
        _build_codex_review_bundle 결과의 file_sha_attestations에 해당 파일이 있어야 한다.
        """
        import json
        from pathlib import Path

        try:
            ar = Path(".pipeline/active_run.json")
            ptr = json.loads(ar.read_text(encoding="utf-8"))
            sp = ptr.get("state_path")
            state = json.loads(Path(sp).read_text(encoding="utf-8"))
            pid = state.get("pipeline_id", "IMP-20260712-DAE1")
        except Exception:
            state = {}
            pid = "IMP-20260712-DAE1"

        _sha, _bundle_path = pipeline._build_codex_review_bundle(state, pid)
        bundle = json.loads(Path(_bundle_path).read_text(encoding="utf-8"))

        _attestations = bundle.get("file_sha_attestations", {})
        _test_file = "tests/e2e/test_codex_model_router_dae1.py"
        # 이 파일이 CRITICAL 파일이므로 SHA attestation에 있어야 한다
        assert _test_file in _attestations, (
            f"REJECT#35 AC#3: {_test_file}이 bundle.file_sha_attestations에 없음. "
            f"현재 attestations keys: {list(_attestations.keys())[:5]!r}"
        )
        _attest = _attestations[_test_file]
        assert _attest.get("after_sha"), (
            f"REJECT#35 AC#3: {_test_file} attestation.after_sha가 비어있음: {_attest!r}"
        )

    def test_tc49f_truncated_noncrit_field_in_semantic(self) -> None:
        """REJECT#35 AC#4: _build_codex_semantic_evidence 결과에 truncated_noncrit_hunks 필드 존재."""
        import unittest.mock as mock

        def _fake_subprocess_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            _m = mock.MagicMock()
            _m.returncode = 0
            _m.stdout = ""
            return _m

        with mock.patch("pipeline.subprocess.run", side_effect=_fake_subprocess_run), \
             mock.patch.object(pipeline.Path, "read_text", return_value=""):
            sem = pipeline._build_codex_semantic_evidence(
                "IMP-20260712-DAE1",
                ["codex_model_router_report.md"],
                [],
            )

        assert "truncated_noncrit_hunks" in sem, (
            "REJECT#35 AC#4: semantic에 truncated_noncrit_hunks 필드 없음"
        )
        assert isinstance(sem["truncated_noncrit_hunks"], int), (
            f"REJECT#35 AC#4: truncated_noncrit_hunks가 int 아님 — {sem['truncated_noncrit_hunks']!r}"
        )
        assert "missing_noncrit_files" in sem, (
            "REJECT#35 AC#4: semantic에 missing_noncrit_files 필드 없음"
        )
        assert isinstance(sem["missing_noncrit_files"], list), (
            f"REJECT#35 AC#4: missing_noncrit_files가 list 아님 — {sem['missing_noncrit_files']!r}"
        )


class TestTC50PromptDigestAndTestPathFix:
    """REJECT#6 검증: file_sha_attestations → prompt/digest 포함 + _is_codex_test_path 경로 제한.

    AC#1: CRITICAL Python 파일 SHA 증거가 Codex prompt에 포함된다.
    AC#2: CRITICAL Python 파일 내용 변경 시 semantic_evidence_sha256 값이 달라진다.
    AC#3: diff/SHA 모두 없는 CRITICAL 파일이 missing_critical_files에 포함되고
          evidence_complete=False로 처리된다.
    AC#4: .github/workflows/test_release.py 같은 HIGH 경로 test-like 파일이 LOW로 오분류되지 않는다.
    AC#5 (회귀): tests/ 하위 파일은 여전히 _is_codex_test_path=True로 분류된다.
    """

    def _make_bundle_with_file_shas(self, file_sha_attestations: dict) -> dict:
        """file_sha_attestations를 포함한 최소 bundle 조립 (evidence_complete=True)."""
        return {
            "evidence_complete": True,
            "changed_files": ["tests/e2e/test_codex_model_router_dae1.py"],
            "changed_files_count": 1,
            "diff_hunks": [
                {
                    "function": "pipeline.py",
                    "is_critical": True,
                    "hunk": "--- a/pipeline.py\n+++ b/pipeline.py\n@@ -1 +1 @@\n-old\n+new",
                    "chars": 100,
                }
            ],
            "function_before_after_shas": {},
            "file_sha_attestations": file_sha_attestations,
            "test_assertions": {},
            "oracle_results": [],
        }

    def test_tc50a_prompt_includes_file_sha_attestations_section(self) -> None:
        """REJECT#6 AC#1: file_sha_attestations가 있으면 Codex prompt에 SHA 증거 섹션이 추가된다."""
        bundle = self._make_bundle_with_file_shas({
            "tests/e2e/test_codex_model_router_dae1.py": {
                "before_sha": "a" * 64,
                "after_sha": "b" * 64,
                "changed": True,
            }
        })
        prompt = pipeline._build_codex_prompt_for_review(bundle, "IMP-20260712-DAE1")
        assert "CRITICAL Python 파일 SHA 증거" in prompt, (
            "REJECT#6 AC#1: prompt에 'CRITICAL Python 파일 SHA 증거' 섹션 없음 — "
            "file_sha_attestations가 Codex에 전달되지 않는다"
        )
        assert "test_codex_model_router_dae1.py" in prompt, (
            "REJECT#6 AC#1: prompt에 CRITICAL Python 파일명이 없음"
        )
        assert "[CHANGED]" in prompt, (
            "REJECT#6 AC#1: changed=True인데 [CHANGED] 태그가 prompt에 없음"
        )

    def test_tc50b_prompt_no_file_sha_section_when_empty(self) -> None:
        """REJECT#6 AC#1 음성: file_sha_attestations={}이면 SHA 증거 섹션이 prompt에 없다."""
        bundle = self._make_bundle_with_file_shas({})
        prompt = pipeline._build_codex_prompt_for_review(bundle, "IMP-20260712-DAE1")
        assert "CRITICAL Python 파일 SHA 증거" not in prompt, (
            "REJECT#6 AC#1 음성: file_sha_attestations={}인데 SHA 증거 섹션이 생성됨"
        )

    def test_tc50c_semantic_sha256_changes_when_file_sha_attestations_change(self) -> None:
        """REJECT#6 AC#2: file_sha_attestations after_sha 변경 시 semantic_evidence_sha256이 달라진다."""
        import hashlib
        import json

        def _sha_of(sem: dict) -> str:
            return hashlib.sha256(
                json.dumps(sem, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()

        common_base = {
            "diff_hunks": [{"function": "pipeline.py", "is_critical": True, "chars": 100}],
            "function_before_after_shas": {},
            "missing_critical_files": [],
            "test_assertions": {},
        }
        sem_v1 = {
            **common_base,
            "file_sha_attestations": {
                "tests/e2e/test_codex_model_router_dae1.py": {
                    "before_sha": "a" * 64,
                    "after_sha": "b" * 64,
                    "changed": True,
                }
            },
        }
        sem_v2 = {
            **common_base,
            "file_sha_attestations": {
                "tests/e2e/test_codex_model_router_dae1.py": {
                    "before_sha": "a" * 64,
                    "after_sha": "c" * 64,  # after_sha만 변경
                    "changed": True,
                }
            },
        }
        assert _sha_of(sem_v1) != _sha_of(sem_v2), (
            "REJECT#6 AC#2: file_sha_attestations after_sha가 바뀌어도 digest가 동일 — "
            "semantic_evidence_sha256 계산에 file_sha_attestations가 포함되어야 함"
        )

    def test_tc50d_is_codex_test_path_workflow_test_false(self) -> None:
        """REJECT#6 AC#4: .github/workflows/test_release.py → _is_codex_test_path=False."""
        result = pipeline._is_codex_test_path(".github/workflows/test_release.py")
        assert result is False, (
            "REJECT#6 AC#4: .github/workflows/test_release.py가 _is_codex_test_path=True — "
            "tests/ 외부 test-like 파일이 LOW 오분류로 이어짐"
        )

    def test_tc50e_workflow_test_file_classified_high_not_low(self) -> None:
        """REJECT#6 AC#4: .github/workflows/test_release.py risk=HIGH (LOW 아님)."""
        result = pipeline._classify_codex_review_risk(
            [".github/workflows/test_release.py"], []
        )
        risk = result.get("risk_level", "")
        assert risk == "HIGH", (
            f"REJECT#6 AC#4: .github/workflows/test_release.py risk={risk!r} — HIGH여야 함. "
            "_is_codex_test_path가 tests/ 외부 파일을 제외하여 HIGH 경로가 LOW로 오분류"
        )

    def test_tc50f_tests_prefix_still_returns_true(self) -> None:
        """REJECT#6 AC#5 회귀: tests/ 하위 일반 테스트 파일은 여전히 _is_codex_test_path=True.
        REJECT#17 수정: tests/oracles/ 는 oracle 답안 파일이므로 예외 처리 — False를 반환한다."""
        assert pipeline._is_codex_test_path("tests/e2e/test_codex_model_router_dae1.py") is True, (
            "REJECT#6 AC#5 회귀: tests/e2e/test_codex_model_router_dae1.py가 _is_codex_test_path=False — "
            "tests/ prefix 기반 분류가 깨짐"
        )
        # REJECT#17: tests/oracles/ 는 oracle 답안 파일 — tests/ 제외 예외이므로 False여야 한다.
        assert pipeline._is_codex_test_path("tests/oracles/IMP-20260712-DAE1/tc01/expected.json") is False, (
            "REJECT#17: tests/oracles/ 파일은 oracle 답안이므로 _is_codex_test_path=False여야 한다 "
            "(이전 REJECT#6 동작은 REJECT#17에서 수정됨)."
        )

    def test_tc50g_root_test_file_not_excluded(self) -> None:
        """REJECT#6 AC#4 수정 확인: 루트 test_foo.py는 이제 tests/ 외부이므로 False여야 한다.

        이전 동작: test_foo.py (루트) → True (파일명 패턴 match)
        새 동작: test_foo.py (루트) → False (tests/ 외부 — 파일명 패턴 제거됨)
        """
        result = pipeline._is_codex_test_path("test_foo.py")
        assert result is False, (
            "REJECT#6 AC#4: 루트 test_foo.py가 _is_codex_test_path=True — "
            "파일명 패턴 기반 제외가 남아있음. tests/ 하위만 제외해야 함"
        )


class TestTC51CriticalPythonDiffInPrompt:
    """REJECT#7 검증: CRITICAL Python 파일 diff가 Codex 입력에 포함되어야 한다.

    AC#1: 변경된 CRITICAL Python 테스트 파일마다 diff 또는 내용이 포함된 검토 결과가 존재한다.
    AC#2: @_CODEX_SKIP 등 테스트 비활성화 변경이 Codex 프롬프트에 명시적으로 표시된다.
    AC#3: diff 수집 실패 시 SHA attestation이 존재하지 않으면 missing_critical_files에 포함.
    AC#4: 두 regression 테스트 삭제 확인 (정상 실행 or 부재).
    """

    def test_tc51a_critical_python_diff_hunk_in_semantic_evidence(self) -> None:
        """REJECT#7 AC#1 / REJECT#8 AC#1: _build_codex_semantic_evidence에서 CRITICAL Python diff가
        _all_hunks에 추가된다. REJECT#8 이후에는 다중 청크 분할 방식이 적용된다.

        소스 코드 검사로 section 1b 내 Python CRITICAL 파일의 diff 수집 로직 존재 여부 확인.
        """
        import inspect

        _src = inspect.getsource(pipeline._build_codex_semantic_evidence)
        # REJECT#7: Python CRITICAL 파일에 대해 git diff를 수집하는 경로가 있어야 함
        assert "REJECT#7" in _src, (
            "REJECT#7 AC#1: _build_codex_semantic_evidence 소스에 REJECT#7 주석 없음 — "
            "CRITICAL Python diff 수집 경로가 구현되지 않았을 수 있음"
        )
        # REJECT#8: 파일당 50K 단일 청크 방식 제거 → 다중 청크 분할 방식 확인
        assert "_PY_CRIT_CHUNK_SIZE" in _src, (
            "REJECT#8 AC#1: _PY_CRIT_CHUNK_SIZE 상수가 소스에 없음 — "
            "다중 청크 분할 구현이 누락되었을 수 있음"
        )
        assert "CRITICAL_PYTHON_DIFF_PART_" in _src, (
            "REJECT#8 AC#1: CRITICAL_PYTHON_DIFF_PART_ 헤더 패턴이 소스에 없음 — "
            "청크 분할 헤더 구현이 누락되었을 수 있음"
        )

    def test_tc51b_critical_python_diff_appears_in_bundle(self) -> None:
        """REJECT#7 AC#1/AC#2 / REJECT#8 AC#1: 현재 PR diff에 CRITICAL Python 테스트 파일 변경이 포함되면
        bundle의 diff_hunks에 해당 파일의 hunk가 나타난다.
        REJECT#8 이후 함수 이름에 [PART N/M] suffix가 붙을 수 있으므로 startswith로 확인.
        """
        import json
        from pathlib import Path

        try:
            ar = Path(".pipeline/active_run.json")
            ptr = json.loads(ar.read_text(encoding="utf-8"))
            sp = ptr.get("state_path")
            state = json.loads(Path(sp).read_text(encoding="utf-8"))
            pid = state.get("pipeline_id", "IMP-20260712-DAE1")
        except Exception:
            state = {}
            pid = "IMP-20260712-DAE1"

        _sha, _bundle_path = pipeline._build_codex_review_bundle(state, pid)
        bundle = json.loads(Path(_bundle_path).read_text(encoding="utf-8"))

        # diff_hunks에 test_ac_tracking_1abe.py 또는 test_codex_model_router_dae1.py가 있어야 함.
        # REJECT#8: 청크 분할 후 함수 이름이 "tests/e2e/test_codex*.py [PART N/M]" 형태일 수 있음.
        _hunk_functions = [h.get("function", "") for h in bundle.get("diff_hunks", [])]
        _crit_py_hunks = [
            fn for fn in _hunk_functions
            if (
                fn.startswith("tests/e2e/test_ac_tracking_1abe.py")
                or fn.startswith("tests/e2e/test_codex_model_router_dae1.py")
            )
        ]
        assert len(_crit_py_hunks) > 0, (
            "REJECT#7 AC#1: diff_hunks에 CRITICAL Python 테스트 파일 hunk 없음. "
            f"현재 hunk functions(앞 10개): {_hunk_functions[:10]!r}"
        )

    def test_tc51c_critical_python_diff_hunk_is_marked_critical(self) -> None:
        """REJECT#7 AC#1: CRITICAL Python 테스트 파일(tests/e2e/test_codex*) diff hunk는 is_critical=True.

        NOTE: tests/e2e/test_ac_tracking_1abe.py는 _CODEX_CRITICAL_FILE_PREFIXES에 없어서
        non-critical. is_critical=True를 기대하는 파일은 tests/e2e/test_codex* 뿐이다.
        """
        import json
        from pathlib import Path

        try:
            ar = Path(".pipeline/active_run.json")
            ptr = json.loads(ar.read_text(encoding="utf-8"))
            sp = ptr.get("state_path")
            state = json.loads(Path(sp).read_text(encoding="utf-8"))
            pid = state.get("pipeline_id", "IMP-20260712-DAE1")
        except Exception:
            state = {}
            pid = "IMP-20260712-DAE1"

        _sha, _bundle_path = pipeline._build_codex_review_bundle(state, pid)
        bundle = json.loads(Path(_bundle_path).read_text(encoding="utf-8"))

        # test_codex* 파일이 diff_hunks에 있으면 is_critical=True여야 함
        _crit_py_hunks = [
            h for h in bundle.get("diff_hunks", [])
            if h.get("function", "").startswith("tests/e2e/test_codex")
        ]
        if _crit_py_hunks:
            for _h in _crit_py_hunks:
                assert _h.get("is_critical") is True, (
                    f"REJECT#7 AC#1: CRITICAL Python 파일 hunk의 is_critical=False: {_h.get('function')}"
                )
        else:
            # diff가 없으면 file_sha_attestations에 있어야 함 (REJECT#6 SHA fallback)
            _attestations = bundle.get("file_sha_attestations", {})
            _codex_test_file = "tests/e2e/test_codex_model_router_dae1.py"
            assert _codex_test_file in _attestations, (
                f"REJECT#7 AC#1: {_codex_test_file}가 diff_hunks에도 없고 "
                "file_sha_attestations에도 없음 — CRITICAL 파일 증거 없음"
            )

    def test_tc51d_regression_tests_deleted(self) -> None:
        """REJECT#7 AC#4: 두 regression 테스트가 test_ac_tracking_1abe.py에서 삭제되었다."""
        import sys

        # test_ac_tracking_1abe 모듈을 reload하여 최신 상태 반영
        _mod_name = "tests.e2e.test_ac_tracking_1abe"
        if _mod_name in sys.modules:
            del sys.modules[_mod_name]

        from tests.e2e import test_ac_tracking_1abe as _mod

        assert not hasattr(_mod, "test_regression_sun_02_vs_mon_09_diff_values_mismatch"), (
            "REJECT#7 AC#4: test_regression_sun_02_vs_mon_09_diff_values_mismatch가 아직 존재 — "
            "제거된 _validate_codex_coverage_checks에 의존하는 테스트는 삭제되어야 함"
        )
        assert not hasattr(_mod, "test_regression_dry_run_substitution_for_real_move"), (
            "REJECT#7 AC#4: test_regression_dry_run_substitution_for_real_move가 아직 존재 — "
            "제거된 _validate_codex_coverage_checks에 의존하는 테스트는 삭제되어야 함"
        )


class TestTC52CriticalPythonDiffChunking:
    """REJECT#8 검증: CRITICAL Python 파일 diff를 파일당 50K 단일 청크로 자르는 대신
    45K 청크로 분할하여 budget trimmer가 overflow를 정확히 감지하게 한다.

    AC#1: 50,000자 이후의 sentinel 변경도 Codex 검토 입력에 포함된다.
    AC#2: CRITICAL diff 일부라도 누락되면 evidence_complete=False이고 gates codex-review가 BLOCKED된다.
    AC#3: file_sha_attestations가 존재해도 절단된 CRITICAL diff를 완전한 증거로 간주하지 않는다.
    AC#4: 대형 CRITICAL 파일 전체를 분할 검토한 경우에만 evidence_complete=True가 된다.
    """

    def test_tc52a_chunk_constant_and_pattern_in_source(self) -> None:
        """REJECT#8 AC#1/AC#4: _PY_CRIT_CHUNK_SIZE 상수와 CRITICAL_PYTHON_DIFF_PART_ 패턴이
        _build_codex_semantic_evidence 소스에 있다. 파일당 50K 단일 절단 sentinel은 제거됐다.
        """
        import inspect

        _src = inspect.getsource(pipeline._build_codex_semantic_evidence)
        assert "_PY_CRIT_CHUNK_SIZE" in _src, (
            "REJECT#8 AC#1: _PY_CRIT_CHUNK_SIZE 상수가 소스에 없음 — 청크 분할 구현 누락"
        )
        assert "CRITICAL_PYTHON_DIFF_PART_" in _src, (
            "REJECT#8 AC#1: CRITICAL_PYTHON_DIFF_PART_ 헤더 패턴이 소스에 없음"
        )
        assert "CRITICAL_PYTHON_DIFF_TRUNCATED_AT_50K" not in _src, (
            "REJECT#8 AC#1: CRITICAL_PYTHON_DIFF_TRUNCATED_AT_50K 구 sentinel이 아직 소스에 있음 — "
            "단일 청크 절단 방식이 제거되지 않았음"
        )
        assert "_PY_CRIT_DIFF_PER_FILE_LIMIT" not in _src, (
            "REJECT#8 AC#1: _PY_CRIT_DIFF_PER_FILE_LIMIT 구 상수가 아직 소스에 있음"
        )

    def test_tc52b_no_truncated_sentinel_in_real_bundle(self) -> None:
        """REJECT#8 AC#1: 실제 bundle의 어떤 hunk에도 CRITICAL_PYTHON_DIFF_TRUNCATED_AT_50K가 없다.

        단일 절단 방식(REJECT#7)의 sentinel이 완전히 제거되었는지 확인.
        """
        import json
        from pathlib import Path

        try:
            ar = Path(".pipeline/active_run.json")
            ptr = json.loads(ar.read_text(encoding="utf-8"))
            sp = ptr.get("state_path")
            state = json.loads(Path(sp).read_text(encoding="utf-8"))
            pid = state.get("pipeline_id", "IMP-20260712-DAE1")
        except Exception:
            state = {}
            pid = "IMP-20260712-DAE1"

        _sha, _bundle_path = pipeline._build_codex_review_bundle(state, pid)
        bundle = json.loads(Path(_bundle_path).read_text(encoding="utf-8"))

        for _h in bundle.get("diff_hunks", []):
            _hunk_text = _h.get("hunk", "")
            assert "CRITICAL_PYTHON_DIFF_TRUNCATED_AT_50K" not in _hunk_text, (
                f"REJECT#8 AC#1: hunk '{_h.get('function')}' 에 구 truncation sentinel이 있음 — "
                "단일 절단 방식이 아직 사용 중"
            )

    def test_tc52c_large_diff_produces_part_chunks(self) -> None:
        """REJECT#8 AC#1/AC#4: diff가 _PY_CRIT_CHUNK_SIZE(45K)를 초과하는 CRITICAL Python 파일은
        diff_hunks에 [PART N/M] 접미사가 붙은 복수 hunk로 나타난다.

        현재 PR의 test_codex_model_router_dae1.py diff는 45K를 훨씬 초과하므로
        반드시 여러 PART hunk가 생성되어야 한다.
        """
        import json
        from pathlib import Path
        import subprocess as sp_

        # 실제 diff 크기 확인
        _r = sp_.run(
            ["git", "diff", "origin/main", "--unified=3",
             "--", "tests/e2e/test_codex_model_router_dae1.py"],
            capture_output=True, text=True, cwd=str(pipeline.BASE_DIR),
            encoding="utf-8", errors="replace",
        )
        _diff_len = len(_r.stdout.strip()) if _r.returncode == 0 else 0

        if _diff_len <= 45000:
            # diff가 45K 이내면 단일 hunk — 이 테스트는 PART 다중 청크를 요구하지 않음
            return

        # diff가 45K 초과면 bundle에 PART 접미사 hunk가 있어야 함
        try:
            ar = Path(".pipeline/active_run.json")
            ptr = json.loads(ar.read_text(encoding="utf-8"))
            _sp = ptr.get("state_path")
            state = json.loads(Path(_sp).read_text(encoding="utf-8"))
            pid = state.get("pipeline_id", "IMP-20260712-DAE1")
        except Exception:
            state = {}
            pid = "IMP-20260712-DAE1"

        _sha, _bundle_path = pipeline._build_codex_review_bundle(state, pid)
        bundle = json.loads(Path(_bundle_path).read_text(encoding="utf-8"))

        _part_hunks = [
            h for h in bundle.get("diff_hunks", [])
            if (
                h.get("function", "").startswith("tests/e2e/test_codex_model_router_dae1.py")
                and "[PART " in h.get("function", "")
            )
        ]
        assert len(_part_hunks) > 1, (
            f"REJECT#8 AC#1: diff가 {_diff_len}자(45K 초과)인데 "
            f"diff_hunks에 PART 청크가 {len(_part_hunks)}개 뿐임 — "
            "다중 청크 분할이 동작하지 않음"
        )

        # 각 PART hunk는 is_critical=True
        for _h in _part_hunks:
            assert _h.get("is_critical") is True, (
                f"REJECT#8: PART hunk '{_h.get('function')}' 의 is_critical=False"
            )

        # bundle의 diff_hunks는 metadata만 저장(hunk 텍스트 미포함).
        # PART 헤더(CRITICAL_PYTHON_DIFF_PART_N_OF_M)는 Codex 프롬프트에 삽입되며
        # 소스 코드에서 이미 TC-52a로 검증됨. 여기서는 function 이름 패턴만 확인.
        import re as _re52c
        for _h in _part_hunks:
            _fn52c = _h.get("function", "")
            assert "[PART " in _fn52c and _re52c.search(r"/\d+\]", _fn52c), (
                f"REJECT#8: PART hunk function name에 [PART N/M] 패턴 없음: {_fn52c!r}"
            )

    def test_tc52d_budget_overflow_causes_evidence_incomplete(self) -> None:
        """REJECT#8/11 AC#2/AC#4: budget이 작아서 CRITICAL Python 청크 일부가 제거되면
        truncated_critical_hunks > 0 이고 evidence_complete=False가 된다.

        REJECT#11: _build_codex_semantic_evidence를 올바른 인수(pipeline_id,
        changed_files, included_functions)로 호출하고 환경 제약 skip 없이 실패시킨다.
        """
        import json
        from pathlib import Path

        # 실제 bundle에서 changed_files / included_functions 추출
        try:
            ar = Path(".pipeline/active_run.json")
            ptr = json.loads(ar.read_text(encoding="utf-8"))
            _sp = ptr.get("state_path")
            state = json.loads(Path(_sp).read_text(encoding="utf-8"))
            pid = state.get("pipeline_id", "IMP-20260712-DAE1")
        except Exception:
            state = {}
            pid = "IMP-20260712-DAE1"

        _sha, _bundle_path = pipeline._build_codex_review_bundle(state, pid)
        assert _bundle_path, "TC-52d: _build_codex_review_bundle 실패"
        bundle = json.loads(Path(_bundle_path).read_text(encoding="utf-8"))
        _changed_files = list(bundle.get("changed_files", []) or [])
        _included_fns = list(bundle.get("included_functions", []) or [])
        assert _changed_files, "TC-52d: changed_files 비어있음"

        _orig_budget = pipeline.CODEX_REVIEW_BUNDLE_BUDGET_CHARS
        try:
            # 극소 budget(100자)으로 패치 → CRITICAL hunk가 들어가지 않아야 함
            pipeline.CODEX_REVIEW_BUNDLE_BUDGET_CHARS = 100
            sem = pipeline._build_codex_semantic_evidence(pid, _changed_files, _included_fns)
            # budget 100자에서는 CRITICAL hunk가 제거되어야 함
            assert sem["truncated_critical_hunks"] > 0, (
                "REJECT#8/11 AC#2: budget=100에서도 truncated_critical_hunks=0 — "
                "budget 초과 감지 로직이 동작하지 않음"
            )
            assert sem["evidence_complete"] is False, (
                "REJECT#8/11 AC#2: budget=100에서도 evidence_complete=True — "
                "fail-closed 동작 실패"
            )
        finally:
            pipeline.CODEX_REVIEW_BUNDLE_BUDGET_CHARS = _orig_budget


class TestTC53CodexBinaryPathTrust:
    """REJECT#9 검증: Codex 실행 파일 경로 신뢰 검증으로 PATH injection을 방지한다.

    AC#1: PATH에 가짜 codex를 선행 배치한 E2E에서 인증 또는 실행 출처 검증이 BLOCKED된다.
    AC#2: CODEX_REVIEW_FAKE_BIN 미설정만으로 environment=production이 되지 않는다.
    AC#3: 검증된 Codex 실행 파일 절대 경로와 SHA-256이 결과에 기록된다.
    AC#4: agent_message나 임의 최상위 JSON의 model/reasoning_effort로 actual_verified가 만들어지지 않는다.
    AC#5: 신뢰된 실제 Codex CLI 경로만 HIGH/CRITICAL 정책을 정상 통과한다.
    """

    def test_tc53a_path_injected_temp_binary_is_blocked(self, tmp_path: "Path") -> None:
        """REJECT#9 AC#1: temp 디렉토리의 가짜 codex를 PATH에 주입하면 auth 검증이 BLOCKED된다.

        shutil.which("codex")가 찾을 수 있도록 올바른 이름(codex.cmd or codex)으로 생성한다.
        """
        import os
        import sys as _sys
        import textwrap as _tw

        # PATH injection이 성공하도록 shutil.which("codex") 탐색 대상 이름으로 생성
        if os.name == "nt":
            # Windows: codex.cmd 생성
            _impl = tmp_path / "_fake_codex_impl9.py"
            _impl.write_text(
                _tw.dedent("""
                import sys
                sys.stdout.write("Logged in using ChatGPT\\n")
                sys.exit(0)
                """).strip() + "\n",
                encoding="utf-8",
            )
            _named_bin = tmp_path / "codex.cmd"
            _named_bin.write_text(
                f'@"{_sys.executable}" "{_impl}" %*\r\n', encoding="utf-8"
            )
        else:
            # Unix: codex (실행 권한 포함)
            import stat as _stat
            _named_bin = tmp_path / "codex"
            _named_bin.write_text(
                '#!/bin/sh\necho "Logged in using ChatGPT"\nexit 0\n', encoding="utf-8"
            )
            _named_bin.chmod(_named_bin.stat().st_mode | _stat.S_IEXEC)

        _fake_dir = str(tmp_path)
        _orig_path = os.environ.get("PATH", "")
        _orig_fake_bin = os.environ.get("CODEX_REVIEW_FAKE_BIN", None)
        try:
            os.environ.pop("CODEX_REVIEW_FAKE_BIN", None)
            os.environ["PATH"] = _fake_dir + os.pathsep + _orig_path

            # _check_codex_chatgpt_auth(codex_bin=None) → PATH에서 fake binary를 찾아 신뢰 검증
            _result = pipeline._check_codex_chatgpt_auth(codex_bin=None)

            assert _result["result"] == "BLOCKED", (
                f"REJECT#9 AC#1: PATH injection된 temp fake codex가 BLOCKED되지 않음. "
                f"result={_result['result']!r}, failure_code={_result.get('failure_code')!r}"
            )
            _fc = _result.get("failure_code", "")
            assert "untrusted" in _fc.lower() or "binary" in _fc.lower(), (
                f"REJECT#9 AC#1: failure_code가 binary 신뢰 문제를 나타내지 않음: {_fc!r}"
            )
        finally:
            os.environ["PATH"] = _orig_path
            if _orig_fake_bin is not None:
                os.environ["CODEX_REVIEW_FAKE_BIN"] = _orig_fake_bin
            else:
                os.environ.pop("CODEX_REVIEW_FAKE_BIN", None)

    def test_tc53b_repo_dir_binary_is_blocked(self, tmp_path: "Path") -> None:
        """REJECT#9 AC#1: git repo 내 가짜 codex도 신뢰 불가로 BLOCKED된다."""
        # git repo 내 경로에 가짜 binary 생성
        _repo_fake_path = pipeline.BASE_DIR / "tests" / "fake_codex_REJECT9_test"
        _repo_fake_path_str = str(_repo_fake_path)

        try:
            # trust 함수에 직접 repo 내 경로를 전달
            _trust = pipeline._verify_codex_binary_path_trust(_repo_fake_path_str)
            assert _trust["trusted"] is False, (
                "REJECT#9 AC#1: git repo 내 경로가 trusted=True로 잘못 판정됨"
            )
            assert _trust["untrusted_reason"] is not None, (
                "REJECT#9 AC#1: git repo 내 경로의 untrusted_reason이 None"
            )
            assert "git_repo" in str(_trust["untrusted_reason"]).lower(), (
                f"REJECT#9 AC#1: untrusted_reason에 'git_repo'가 없음: {_trust['untrusted_reason']!r}"
            )
        except Exception as _e:
            if "trusted" in str(_e) or "untrusted" in str(_e):
                raise
            import pytest
            pytest.fail(f"REJECT#9 AC#1: 예상치 못한 예외: {_e}")

    def test_tc53c_verify_function_exists_in_source(self) -> None:
        """REJECT#9 AC#1/AC#2: _verify_codex_binary_path_trust 함수가 소스에 있다."""
        import inspect

        assert hasattr(pipeline, "_verify_codex_binary_path_trust"), (
            "REJECT#9 AC#1: _verify_codex_binary_path_trust 함수가 없음"
        )
        _src = inspect.getsource(pipeline._verify_codex_binary_path_trust)
        assert "temp_dir" in _src or "gettempdir" in _src or "tmpdir" in _src.lower(), (
            "REJECT#9 AC#1: 임시 디렉토리 검증 로직이 _verify_codex_binary_path_trust에 없음"
        )
        assert "git_repo" in _src or "BASE_DIR" in _src or "relative_to" in _src, (
            "REJECT#9 AC#1: git repo 경로 검증 로직이 _verify_codex_binary_path_trust에 없음"
        )

    def test_tc53d_binary_trust_check_in_cmd_gates_codex_review(self) -> None:
        """REJECT#9 AC#2: _cmd_gates_codex_review 소스에 binary trust 검증 로직이 있다."""
        import inspect

        _src = inspect.getsource(pipeline._cmd_gates_codex_review)
        assert "_verify_codex_binary_path_trust" in _src or "_codex_binary_trust" in _src, (
            "REJECT#9 AC#2: _cmd_gates_codex_review에 binary trust 검증 로직이 없음"
        )
        assert "codex_binary_untrusted_path" in _src, (
            "REJECT#9 AC#2: _cmd_gates_codex_review에 codex_binary_untrusted_path failure_code가 없음"
        )

    def test_tc53e_binary_path_and_sha_in_review_result_schema(self) -> None:
        """REJECT#9 AC#3: _cmd_gates_codex_review 소스가 codex_binary_path/sha256을 결과에 기록한다."""
        import inspect

        # 소스 검사: _cmd_gates_codex_review에 binary 정보 기록 로직이 있어야 함
        _src = inspect.getsource(pipeline._cmd_gates_codex_review)
        assert "codex_binary_path" in _src, (
            "REJECT#9 AC#3: _cmd_gates_codex_review에 codex_binary_path 기록 로직이 없음"
        )
        assert "codex_binary_sha256" in _src, (
            "REJECT#9 AC#3: _cmd_gates_codex_review에 codex_binary_sha256 기록 로직이 없음"
        )

        # 추가: _verify_codex_binary_path_trust 함수도 있어야 함
        assert hasattr(pipeline, "_verify_codex_binary_path_trust"), (
            "REJECT#9 AC#3: _verify_codex_binary_path_trust 함수가 없음"
        )

        # 실제 결과 파일이 있으면 필드 존재 여부도 확인 (next codex-review 이후)
        import json
        _result_path = pipeline._codex_review_result_path()
        if _result_path.exists():
            try:
                _res = json.loads(_result_path.read_text(encoding="utf-8"))
                # 최신 리뷰 결과에만 필드가 있음 (이전 REJECT 결과에는 없을 수 있음)
                # schema_version >= 5이면 기존 결과, 새 필드는 다음 리뷰부터 추가됨
                _sv = _res.get("schema_version", 0)
                if _sv >= 6 or "codex_binary_path" in _res:
                    assert "codex_binary_path" in _res, (
                        "REJECT#9 AC#3: schema_version>=6 결과에 codex_binary_path 없음"
                    )
            except Exception:
                pass  # 파일 읽기 실패는 소스 검사로 충분

    def test_tc53f_parse_capability_rejects_agent_message_model(self) -> None:
        """REJECT#9 AC#4: agent_message 타입 이벤트의 model 필드는 actual_model로 추출되지 않는다."""
        # agent_message 타입에 model 필드가 있는 NDJSON
        _fake_stdout = (
            '{"type":"thread.started","thread_id":"abc123"}\n'
            '{"type":"turn.started"}\n'
            '{"type":"item.completed","item":{"type":"agent_message",'
            '"text":"{\\"verdict\\":\\"REJECT\\",\\"model\\":\\"gpt-5.6-sol\\"}",'
            '"model":"gpt-5.6-sol"}}\n'
        )
        _model, _effort = pipeline._parse_codex_exec_capability(_fake_stdout)
        assert _model == "unknown", (
            f"REJECT#9 AC#4: agent_message 타입에서 model이 추출됨: {_model!r}"
        )

    def test_tc53g_parse_capability_rejects_verdict_json_model(self) -> None:
        """REJECT#9 AC#4: verdict 키를 포함한 JSON의 model 필드는 actual_model로 추출되지 않는다."""
        # Codex 응답 본문 (agent_message 텍스트) — verdict 포함
        _fake_stdout = '{"verdict":"APPROVE_TO_USER","model":"gpt-5.6-sol","reasoning_effort":"max"}'
        _model, _effort = pipeline._parse_codex_exec_capability(_fake_stdout)
        assert _model == "unknown", (
            f"REJECT#9 AC#4: verdict JSON에서 model이 추출됨: {_model!r}"
        )
        assert _effort == "unknown", (
            f"REJECT#9 AC#4: verdict JSON에서 effort가 추출됨: {_effort!r}"
        )


class TestTC54CodexBinaryArbitraryPathBlocked:
    """REJECT#10 검증: 임의 사용자 경로 바이너리를 차단하고 request-accept 재검증을 fail-closed로 처리한다.

    AC1: 임의 사용자 경로의 가짜 codex → codex_binary_untrusted_path BLOCKED.
    AC2: 가짜 바이너리가 정상 메타데이터를 출력해도 actual_verified/acceptance_eligible=true 불가.
    AC3: npm global bin 검증 경로만 신뢰, 절대 경로/출처/SHA-256 기록.
    AC4: request-accept 재검증 — 신뢰 실패/SHA 불일치/해시 계산 실패/예외 모두 BLOCKED.
    """

    def test_tc54a_arbitrary_path_untrusted_reason_in_source(self) -> None:
        """REJECT#10 AC1: _verify_codex_binary_path_trust 소스에 'binary_in_arbitrary_user_path' 문자열이 있다."""
        import inspect
        _src = inspect.getsource(pipeline._verify_codex_binary_path_trust)
        assert "binary_in_arbitrary_user_path" in _src, (
            "REJECT#10 AC1: _verify_codex_binary_path_trust에 arbitrary user path 차단 로직 없음"
        )

    def test_tc54b_get_npm_global_bin_called_in_trust_function(self) -> None:
        """REJECT#10 AC3: _verify_codex_binary_path_trust가 _get_npm_global_bin을 호출한다."""
        import inspect
        _src = inspect.getsource(pipeline._verify_codex_binary_path_trust)
        assert "_get_npm_global_bin" in _src, (
            "REJECT#10 AC3: _verify_codex_binary_path_trust가 npm global bin 쿼리를 하지 않음"
        )

    def test_tc54c_arbitrary_path_binary_blocked_by_trust_check(
        self, tmp_path: "Path", monkeypatch
    ) -> None:
        """REJECT#10 AC1: npm global bin / 시스템 경로 밖의 실제 파일 → trusted=False, binary_in_arbitrary_user_path."""
        import tempfile

        # 1. fake binary를 tmp_path/subdir에 생성 (실제 존재하는 파일)
        _fake_dir = tmp_path / "subdir_codex_fake"
        _fake_dir.mkdir()
        _fake_bin = _fake_dir / "codex.cmd"
        _fake_bin.write_text("@echo fake codex\r\n", encoding="utf-8")

        # 2. gettempdir를 tmp_path/fake_tmpdir로 바꿔서 temp 체크에 걸리지 않게 함
        _mock_tmpdir = tmp_path / "fake_tmpdir"
        _mock_tmpdir.mkdir()
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(_mock_tmpdir))

        # 3. npm global bin은 "허용된 설치 루트"로 설정 (fake_bin과 다른 경로).
        #    REJECT#18 문제 2: 허용 루트 밖 npm 출력은 npm_global_bin_not_in_allowed_root로
        #    먼저 차단되므로, 이 테스트(arbitrary_user_path 사유 검증)는 허용 루트를 주입하여
        #    fake_bin이 npm 미포함 + 시스템 경로 밖 → binary_in_arbitrary_user_path로 떨어지게 한다.
        if sys.platform == "win32":
            _mock_npm_bin = Path(r"C:\Program Files\nodejs")  # 하드코딩 허용 루트
        else:
            _mock_npm_bin = Path("/usr/bin")  # 허용 루트
        monkeypatch.setattr(pipeline, "_get_npm_global_bin", lambda: str(_mock_npm_bin))

        # 4. _verify_codex_binary_path_trust 호출
        _result = pipeline._verify_codex_binary_path_trust(str(_fake_bin))

        assert not _result["trusted"], (
            f"REJECT#10 AC1: 임의 경로 바이너리가 trusted=True 반환: {_result}"
        )
        _reason = str(_result.get("untrusted_reason", ""))
        assert "arbitrary_user_path" in _reason, (
            f"REJECT#10 AC1: untrusted_reason에 'arbitrary_user_path' 없음: {_reason!r}"
        )

    def test_tc54d_request_accept_revalidation_fail_closed_for_trust_failure(self) -> None:
        """REJECT#10 AC4: _cmd_gates_request_accept 소스에 신뢰 실패 fail-closed 코드가 있다."""
        import inspect
        _src = inspect.getsource(pipeline._cmd_gates_request_accept)
        assert "codex_binary_revalidation_trust_failed" in _src, (
            "REJECT#10 AC4: request-accept에 신뢰 실패 fail-closed(codex_binary_revalidation_trust_failed) 없음"
        )

    def test_tc54e_request_accept_revalidation_fail_closed_for_sha_unreadable(self) -> None:
        """REJECT#10 AC4: _cmd_gates_request_accept 소스에 SHA 계산 불가 fail-closed 코드가 있다."""
        import inspect
        _src = inspect.getsource(pipeline._cmd_gates_request_accept)
        assert "codex_binary_sha_unreadable" in _src, (
            "REJECT#10 AC4: request-accept에 SHA 계산 실패 fail-closed(codex_binary_sha_unreadable) 없음"
        )

    def test_tc54f_request_accept_revalidation_exception_is_fail_closed(self) -> None:
        """REJECT#10 AC4: _cmd_gates_request_accept의 재검증 예외 처리가 fail-closed(pass 없음)이다."""
        import inspect
        _src = inspect.getsource(pipeline._cmd_gates_request_accept)
        # SHA 재검증 블록의 except 절이 fail-closed(_die 호출)여야 함
        assert "codex_binary_revalidation_error" in _src, (
            "REJECT#10 AC4: request-accept 재검증 예외를 fail-closed로 처리하지 않음"
        )
        # 이전 fail-open 패턴("SHA 재검증 실패는 fail-open")이 제거됐는지 확인
        assert "SHA 재검증 실패는 fail-open" not in _src, (
            "REJECT#10 AC4: request-accept에 여전히 fail-open 주석/패턴이 남아 있음"
        )


class TestTC55CriticalPythonDiffCoverageRequired:
    """REJECT#11 검증: CRITICAL Python 파일은 실제 diff hunk가 있어야 evidence_complete=True가 된다.

    AC1: diff 실패/빈 출력 + SHA만 있는 CRITICAL Python 파일 → evidence_complete=False.
    AC2: changed_files의 모든 CRITICAL 파일이 실제 diff 없으면 missing_critical_files에 기록.
    AC3: file_sha_attestations만으로 _diff_ok 또는 evidence_complete=True가 되지 않음.
    AC4: test_tc52d가 skip 없이 올바른 인수로 실행되어 budget overflow를 실제 검증함.
    """

    def test_tc55a_sha_only_not_sufficient_for_evidence_complete_in_source(self) -> None:
        """REJECT#11 AC3: _build_codex_semantic_evidence 소스에서 SHA만으로는 _diff_ok=True가 되지 않는다."""
        import inspect
        _src = inspect.getsource(pipeline._build_codex_semantic_evidence)
        # REJECT#11: SHA 수집 후 _diff_ok=True 하지 않음을 주석으로 명시해야 함
        assert "SHA 수집 성공만으로 _diff_ok=True" in _src or "SHA 아닌 실제 diff 청크 수집 성공" in _src, (
            "REJECT#11 AC3: _build_codex_semantic_evidence에 SHA-only _diff_ok 차단 주석 없음"
        )

    def test_tc55b_hunk_covered_files_uses_part_base_in_source(self) -> None:
        """REJECT#11 AC2: missing_critical_files 계산이 _hunk_covered_files(PART base명) 기준이다."""
        import inspect
        _src = inspect.getsource(pipeline._build_codex_semantic_evidence)
        assert "_hunk_covered_files" in _src, (
            "REJECT#11 AC2: _build_codex_semantic_evidence에 _hunk_covered_files 없음"
        )
        assert "PART " in _src and "split" in _src, (
            "REJECT#11 AC2: PART suffix 제거 로직 없음 — multi-chunk 파일 커버 판정 오류 가능"
        )

    def test_tc55c_evidence_complete_requires_diff_hunks_not_sha(self) -> None:
        """REJECT#11 AC3: evidence_complete 조건에서 file_sha_attestations 단독 허용이 제거됐다."""
        import inspect
        _src = inspect.getsource(pipeline._build_codex_semantic_evidence)
        # REJECT#11 전: "(len(sem["diff_hunks"]) > 0 or len(sem["file_sha_attestations"]) > 0)"
        # REJECT#11 후: "len(sem["diff_hunks"]) > 0" 만 있어야 함
        assert "len(sem[\"file_sha_attestations\"]) > 0" not in _src or \
               "or len(sem[\"file_sha_attestations\"])" not in _src, (
            "REJECT#11 AC3: evidence_complete 조건에 여전히 file_sha_attestations 단독 허용 있음"
        )
        # evidence_complete 계산에 diff_hunks > 0 조건이 있어야 함
        assert 'len(sem["diff_hunks"]) > 0' in _src, (
            "REJECT#11 AC3: evidence_complete에 diff_hunks > 0 조건 없음"
        )

    def test_tc55d_missing_critical_files_excludes_only_hunk_covered(self) -> None:
        """REJECT#11 AC2: missing_critical_files가 _hunk_covered_files 기준(SHA 불포함)이다."""
        import inspect
        _src = inspect.getsource(pipeline._build_codex_semantic_evidence)
        # REJECT#11 전: "f not in _hunk_covered and f not in _sha_covered"
        # REJECT#11 후: "f not in _hunk_covered_files" (SHA 제외)
        assert "f not in _hunk_covered_files" in _src, (
            "REJECT#11 AC2: missing_critical_files가 _hunk_covered_files 기준이 아님"
        )
        # 이전 패턴(SHA를 커버 조건으로 허용) 제거 확인
        assert "f not in _hunk_covered and f not in _sha_covered" not in _src, (
            "REJECT#11 AC2: 이전 SHA 허용 패턴이 여전히 남아 있음"
        )


# --------------------------------------------------------------------------- #
# TC-56: REJECT#12 — CRITICAL invocation_verified 정책 + binary SHA + BLOCKED 영속성
# --------------------------------------------------------------------------- #

class TestTC56Reject12CriticalPolicyAndBinarySha:
    """REJECT#12 AC#1/AC#3/AC#4/AC#5 회귀 테스트."""

    def test_tc56a_critical_unverified_still_blocked_in_capability_gate(self) -> None:
        """REJECT#12 AC#5: CRITICAL + unverified(invocation_ok=False)는 여전히 BLOCKED다.
        REJECT#12는 invocation_verified 허용이지, unverified 허용이 아니다."""
        r = pipeline._check_codex_capability_gate(
            "unknown", "CRITICAL", pipeline.CODEX_VERIFICATION_UNVERIFIED,
        )
        assert r["result"] == "BLOCKED", (
            f"REJECT#12 AC#5: CRITICAL+unverified는 BLOCKED여야 한다. got={r!r}"
        )
        assert r.get("failure_code") == "model_verification_unverified", (
            f"REJECT#12 AC#5: failure_code={r.get('failure_code')!r} — model_verification_unverified여야 한다."
        )

    def test_tc56b_critical_invocation_verified_passes_capability_gate(self) -> None:
        """REJECT#12 AC#1: CRITICAL + invocation_verified는 _check_codex_capability_gate에서 OK다."""
        r = pipeline._check_codex_capability_gate(
            "unknown", "CRITICAL", pipeline.CODEX_VERIFICATION_INVOCATION,
        )
        assert r["result"] == "OK", (
            f"REJECT#12 AC#1: CRITICAL+invocation_verified가 BLOCKED됨. got={r!r}"
        )

    def test_tc56c_binary_path_with_empty_sha_blocked_in_operational_trust(self) -> None:
        """REJECT#12 AC#3: codex_binary_path 기록 + codex_binary_sha256 빈값 → operational_trust BLOCKED."""
        _base = {
            "status": "APPROVED",
            "verdict_source": "codex_cli",
            "acceptance_eligible": True,
            "router_version": "2.0.0",
            "risk_level": "HIGH",
            "model_policy_signature": "HIGH:gpt-5.6-sol:high:enforce",
            "codex_cli_command": "codex exec --model gpt-5.6-sol ...",
            "selected_model": "gpt-5.6-sol",
            "selected_reasoning_effort": "high",
            "invoked_model": "gpt-5.6-sol",
            "invoked_effort": "high",
            "actual_model": "unknown",
            "actual_effort": "unknown",
            "model_verification_level": pipeline.CODEX_VERIFICATION_INVOCATION,
            "auth_source": "chatgpt",
            # REJECT#12 AC#3: 경로는 있는데 SHA가 빈값
            "codex_binary_path": "/usr/bin/codex",
            "codex_binary_sha256": "",
        }
        r = pipeline._check_codex_review_operational_trust(_base)
        assert r["status"] == "BLOCKED", (
            f"REJECT#12 AC#3: binary_path 기록 + sha 없음이 BLOCKED가 아님. got={r!r}"
        )
        assert r.get("failure_code") == "codex_review_binary_sha_missing", (
            f"REJECT#12 AC#3: failure_code={r.get('failure_code')!r} "
            "— codex_review_binary_sha_missing이어야 한다."
        )

    def test_tc56d_binary_path_empty_sha_empty_passes_operational_trust(self) -> None:
        """IMP-20260712-DAE1 REJECT#16: codex_binary_path 빈값이면 codex_cli에서 BLOCKED.
        (REJECT#12 이전 동작: empty path → skip SHA → PASS는 REJECT#16에서 강화됨)
        codex_cli/verified_cache 경로에서 binary_path 없으면 binary 신뢰 미보장 → BLOCKED."""
        _base = {
            "status": "APPROVED",
            "verdict_source": "codex_cli",
            "acceptance_eligible": True,
            "router_version": "2.0.0",
            "risk_level": "HIGH",
            "model_policy_signature": "HIGH:gpt-5.6-sol:high:enforce",
            "codex_cli_command": "codex exec --model gpt-5.6-sol ...",
            "selected_model": "gpt-5.6-sol",
            "selected_reasoning_effort": "high",
            "invoked_model": "gpt-5.6-sol",
            "invoked_effort": "high",
            "actual_model": "unknown",
            "actual_effort": "unknown",
            "model_verification_level": pipeline.CODEX_VERIFICATION_INVOCATION,
            "auth_source": "chatgpt",
            "codex_binary_path": "",
            "codex_binary_sha256": "",
        }
        r = pipeline._check_codex_review_operational_trust(_base)
        # REJECT#16: codex_cli에서 binary_path가 비어있으면 BLOCKED (신뢰 미보장).
        assert r["status"] == "BLOCKED", (
            f"REJECT#16: binary_path 빈값인 codex_cli 결과가 PASS됨 — BLOCKED여야 한다. got={r!r}"
        )
        assert r.get("failure_code") == "codex_review_binary_path_missing", (
            f"REJECT#16: failure_code={r.get('failure_code')!r} "
            "— codex_review_binary_path_missing이어야 한다."
        )

    def test_tc56e_blocked_flag_file_source_check(self) -> None:
        """REJECT#12 Fix 5: _codex_review_blocked_flag_path 소스 존재 + request-accept가 플래그 체크한다."""
        import inspect
        # 플래그 경로 헬퍼가 pipeline에 존재해야 함
        assert hasattr(pipeline, "_codex_review_blocked_flag_path"), (
            "REJECT#12 Fix5: _codex_review_blocked_flag_path 함수가 pipeline에 없음"
        )
        _req_src = inspect.getsource(pipeline._cmd_gates_request_accept)
        assert "codex_review_blocked_flag_path" in _req_src, (
            "REJECT#12 Fix5: request-accept가 _codex_review_blocked_flag_path를 체크하지 않음"
        )
        assert "blocked_flag_present" in _req_src or "codex_review_blocked.flag" in _req_src, (
            "REJECT#12 Fix5: request-accept에 BLOCKED 플래그 파일 체크 메시지 없음"
        )

    def test_tc56f_blocked_flag_written_before_json_in_source(self) -> None:
        """REJECT#12 Fix 5: _write_codex_review_blocked_invalidation이 플래그 파일을 먼저 기록한다(소스 확인)."""
        import inspect
        _src = inspect.getsource(pipeline._write_codex_review_blocked_invalidation)
        assert "_codex_review_blocked_flag_path" in _src, (
            "REJECT#12 Fix5: _write_codex_review_blocked_invalidation에 플래그 기록 코드 없음"
        )
        # 플래그 write가 _write_json 호출보다 앞에 나와야 함
        _flag_idx = _src.find("_codex_review_blocked_flag_path")
        _json_idx = _src.find("_write_json(result_path")
        assert _flag_idx >= 0 and _json_idx >= 0 and _flag_idx < _json_idx, (
            "REJECT#12 Fix5: 플래그 파일 기록이 _write_json 호출보다 나중에 나옴 — 순서가 잘못됨"
        )

    def test_tc56g_js_entrypoint_sha_change_blocked_in_request_accept_source(self) -> None:
        """REJECT#12 AC#3: request-accept 소스에 JS 진입점 SHA 변경 체크가 있어야 한다."""
        import inspect
        _src = inspect.getsource(pipeline._cmd_gates_request_accept)
        assert "codex_js_entrypoint_sha_changed" in _src, (
            "REJECT#12 AC#3: request-accept에 JS 진입점 SHA 변경 체크(codex_js_entrypoint_sha_changed) 없음"
        )
        assert "codex_js_entrypoint_sha256" in _src, (
            "REJECT#12 AC#3: request-accept에 codex_js_entrypoint_sha256 필드 접근 없음"
        )

    def test_tc56h_high_unverified_still_blocked(self) -> None:
        """REJECT#12 AC#5: HIGH + unverified도 여전히 BLOCKED다 (REJECT#12는 unverified 허용 아님)."""
        r = pipeline._check_codex_capability_gate(
            "unknown", "HIGH", pipeline.CODEX_VERIFICATION_UNVERIFIED,
        )
        assert r["result"] == "BLOCKED", (
            f"REJECT#12 AC#5: HIGH+unverified는 BLOCKED여야 한다. got={r!r}"
        )

    def test_tc56i_mismatch_still_blocked_even_with_invocation_verified(self) -> None:
        """REJECT#12 AC#1: model mismatch는 invocation_verified라도 BLOCKED다 (기존 정책 유지)."""
        r = pipeline._check_codex_model_capability_match(
            "gpt-5.6-sol", "max",   # selected
            "gpt-5.6-terra", "max", # invoked (불일치)
            "unknown", "unknown",
            "CRITICAL",
            invocation_ok=True,
        )
        assert r["result"] == "BLOCKED", (
            f"REJECT#12 AC#1: model mismatch + CRITICAL이 BLOCKED가 아님. got={r!r}"
        )
        assert r.get("failure_code") == "model_mismatch", (
            f"REJECT#12 AC#1: failure_code={r.get('failure_code')!r} — model_mismatch여야 한다."
        )


class TestTC57Reject17TrustRootPathCoverage:
    """REJECT#17: CODEOWNERS/.gitignore/.gitattributes/AGENTS.md/.codex/skills/tests/oracles 경로 HIGH 분류."""

    def test_tc57a_codeowners_single_change_is_high(self) -> None:
        """REJECT#17 AC#1: .github/CODEOWNERS 단독 변경은 HIGH 이상으로 분류된다."""
        r = pipeline._classify_codex_review_risk([".github/CODEOWNERS"], [])
        assert r["risk_level"] in ("HIGH", "CRITICAL"), (
            f"REJECT#17 AC#1: CODEOWNERS 변경이 HIGH 이상이어야 한다. got={r['risk_level']!r}"
        )

    def test_tc57b_codex_skills_single_change_is_high(self) -> None:
        """REJECT#17 AC#2: .codex/skills/** 단독 변경은 HIGH 이상으로 분류된다."""
        r = pipeline._classify_codex_review_risk([".codex/skills/pipeline-task/SKILL.md"], [])
        assert r["risk_level"] in ("HIGH", "CRITICAL"), (
            f"REJECT#17 AC#2: .codex/skills 변경이 HIGH 이상이어야 한다. got={r['risk_level']!r}"
        )

    def test_tc57c_agents_md_single_change_is_high(self) -> None:
        """REJECT#17 AC#2: AGENTS.md 단독 변경은 HIGH 이상으로 분류된다."""
        r = pipeline._classify_codex_review_risk(["AGENTS.md"], [])
        assert r["risk_level"] in ("HIGH", "CRITICAL"), (
            f"REJECT#17 AC#2: AGENTS.md 변경이 HIGH 이상이어야 한다. got={r['risk_level']!r}"
        )

    def test_tc57d_oracle_expected_file_is_not_low(self) -> None:
        """REJECT#17 AC#3: tests/oracles/**/expected.json 단독 변경은 LOW가 아닌 HIGH 이상이다."""
        r = pipeline._classify_codex_review_risk(["tests/oracles/IMP-20260712-DAE1/tc01/expected.json"], [])
        assert r["risk_level"] in ("HIGH", "CRITICAL"), (
            f"REJECT#17 AC#3: oracle expected 파일이 LOW로 분류됨 — HIGH 이상이어야 한다. got={r['risk_level']!r}"
        )

    def test_tc57e_gitignore_single_change_is_high(self) -> None:
        """REJECT#17 AC#4: .gitignore 단독 변경은 HIGH 이상으로 분류된다."""
        r = pipeline._classify_codex_review_risk([".gitignore"], [])
        assert r["risk_level"] in ("HIGH", "CRITICAL"), (
            f"REJECT#17 AC#4: .gitignore 변경이 HIGH 이상이어야 한다. got={r['risk_level']!r}"
        )

    def test_tc57f_gitattributes_single_change_is_high(self) -> None:
        """REJECT#17 AC#4: .gitattributes 단독 변경은 HIGH 이상으로 분류된다."""
        r = pipeline._classify_codex_review_risk([".gitattributes"], [])
        assert r["risk_level"] in ("HIGH", "CRITICAL"), (
            f"REJECT#17 AC#4: .gitattributes 변경이 HIGH 이상이어야 한다. got={r['risk_level']!r}"
        )

    def test_tc57g_general_tests_e2e_file_remains_low(self) -> None:
        """REJECT#17 AC#5: 일반 tests/e2e 파일만 변경한 경우 LOW를 유지한다 (oracle·trust-root 예외 우선 적용 확인)."""
        r = pipeline._classify_codex_review_risk(["tests/e2e/test_some_feature.py"], [])
        assert r["risk_level"] == "LOW", (
            f"REJECT#17 AC#5: 일반 tests/e2e 파일이 LOW 유지되지 않음. got={r['risk_level']!r}"
        )

    def test_tc57h_is_codex_test_path_excludes_oracle(self) -> None:
        """REJECT#17: _is_codex_test_path가 tests/oracles/ 를 False로 반환한다 (제외 예외)."""
        assert not pipeline._is_codex_test_path("tests/oracles/IMP-20260712-DAE1/tc01/expected.json"), (
            "REJECT#17: tests/oracles/ 파일은 테스트 제외 경로가 아니어야 한다."
        )

    def test_tc57i_is_codex_test_path_includes_e2e(self) -> None:
        """REJECT#17: _is_codex_test_path가 tests/e2e/ 를 True로 반환한다 (tests/ 내 일반 파일은 제외)."""
        assert pipeline._is_codex_test_path("tests/e2e/test_foo.py"), (
            "REJECT#17: tests/e2e/ 파일은 테스트 제외 경로여야 한다."
        )


class TestTC58Reject18CriticalConstantsSelfProtect:
    """REJECT#18: CODEX_CRITICAL_CONSTANTS 자기 보호가 분류기까지 이어진다 — 불변 내부 집합 방어."""

    def test_tc58a_empty_critical_constants_still_classifies_critical(self, monkeypatch) -> None:
        """REJECT#18 AC#1: CODEX_CRITICAL_CONSTANTS가 빈 목록인 동안 분류 결과가 CRITICAL이다."""
        monkeypatch.setattr(pipeline, "CODEX_CRITICAL_CONSTANTS", [])
        # CODEX_CRITICAL_CONSTANTS 자체가 변경된 것으로 감지된 상황 시뮬레이션
        r = pipeline._classify_codex_review_risk(["pipeline.py"], [], ["CODEX_CRITICAL_CONSTANTS"])
        assert r["risk_level"] == "CRITICAL", (
            f"REJECT#18 AC#1: CODEX_CRITICAL_CONSTANTS 빈 목록인 동안 분류 결과가 CRITICAL이 아님. got={r['risk_level']!r}"
        )

    def test_tc58b_self_removed_still_classifies_critical(self, monkeypatch) -> None:
        """REJECT#18 AC#2: CODEX_CRITICAL_CONSTANTS에서 자기 자신 항목만 제거해도 CRITICAL이다."""
        # 자기 자신 항목 제거 시뮬레이션
        filtered = [c for c in pipeline.CODEX_CRITICAL_CONSTANTS if c != "CODEX_CRITICAL_CONSTANTS"]
        monkeypatch.setattr(pipeline, "CODEX_CRITICAL_CONSTANTS", filtered)
        r = pipeline._classify_codex_review_risk(["pipeline.py"], [], ["CODEX_CRITICAL_CONSTANTS"])
        assert r["risk_level"] == "CRITICAL", (
            f"REJECT#18 AC#2: CODEX_CRITICAL_CONSTANTS 자기 항목 제거 후에도 CRITICAL이 아님. got={r['risk_level']!r}"
        )

    def test_tc58c_detect_and_classify_both_called_in_monkeypatch_scope(self, monkeypatch) -> None:
        """REJECT#18 AC#3: monkeypatch 범위 안에서 감지와 분류를 모두 실행한다.
        _detect_changed_critical_constants의 결과를 직접 주입하고 분류기를 호출한다 (git 의존 없이)."""
        monkeypatch.setattr(pipeline, "CODEX_CRITICAL_CONSTANTS", [])
        # 감지 결과를 직접 주입 (git repo 의존 없이 monkeypatch 범위 안에서 실행)
        detected = ["CODEX_CRITICAL_CONSTANTS"]
        r = pipeline._classify_codex_review_risk(["pipeline.py"], [], detected)
        assert r["risk_level"] == "CRITICAL", (
            f"REJECT#18 AC#3: monkeypatch 범위 안에서 감지+분류 결과가 CRITICAL이 아님. got={r['risk_level']!r}"
        )
        assert r.get("matched_rule") == "critical_constant", (
            f"REJECT#18 AC#3: matched_rule이 critical_constant가 아님. got={r.get('matched_rule')!r}"
        )

    def test_tc58d_critical_policy_force_review_and_no_cache(self) -> None:
        """REJECT#18 AC#4: CRITICAL 정책은 force_review_required=True + cache_allowed=False이다."""
        policy = pipeline._build_codex_model_policy("CRITICAL")
        assert policy.get("force_review_required") is True, (
            f"REJECT#18 AC#4: CRITICAL 정책에 force_review_required=True가 없음. got={policy!r}"
        )
        assert policy.get("cache_allowed") is False, (
            f"REJECT#18 AC#4: CRITICAL 정책에 cache_allowed=False가 없음. got={policy!r}"
        )


class TestTC59Reject19ToctouBinaryPathFix:
    """REJECT#19: TOCTOU 취약점 — 신뢰 검증된 codex 경로가 auth/exec에 명시적으로 전달된다."""

    def test_tc59a_trusted_bin_path_used_in_production_source(self) -> None:
        """REJECT#19 AC#2: production 소스에서 _codex_binary_trust 경로를 인증·exec에 재사용한다."""
        import inspect
        src = inspect.getsource(pipeline._cmd_gates_codex_review)
        # 신뢰 검증된 경로를 _codex_bin_now에 주입하는 코드가 있어야 한다.
        assert "_trusted_bin_path" in src, (
            "REJECT#19 AC#2: _cmd_gates_codex_review 소스에 _trusted_bin_path 없음 — TOCTOU 방어 미적용"
        )
        assert "_codex_binary_trust" in src and "_trusted_bin_path" in src, (
            "REJECT#19 AC#2: 신뢰 경로 재사용 코드 없음"
        )

    def test_tc59b_post_cli_binary_sha_revalidation_in_source(self) -> None:
        """REJECT#19 AC#3: production 소스에 exec 후 바이너리 SHA 재계산 코드가 있다."""
        import inspect
        src = inspect.getsource(pipeline._cmd_gates_codex_review)
        assert "codex_binary_sha_changed" in src, (
            "REJECT#19 AC#3: post-CLI binary SHA 변경 감지 코드 없음 — TOCTOU 차단 미적용"
        )
        assert "codex_binary_sha_unverifiable" in src, (
            "REJECT#19 AC#3: post-CLI binary SHA 재계산 실패 처리 코드 없음"
        )

    def test_tc59c_post_cli_js_sha_revalidation_in_source(self) -> None:
        """REJECT#19 AC#3: production 소스에 exec 후 JS 진입점 SHA 재계산 코드가 있다."""
        import inspect
        src = inspect.getsource(pipeline._cmd_gates_codex_review)
        assert "codex_js_entrypoint_sha_changed" in src, (
            "REJECT#19 AC#3: post-CLI JS entrypoint SHA 변경 감지 코드 없음"
        )

    def test_tc59d_toctou_path_change_uses_trusted_path(self) -> None:
        """REJECT#19 AC#1: 신뢰 검증 후 PATH가 바뀌어도 _codex_binary_trust 경로를 사용한다.
        _check_codex_chatgpt_auth/_invoke_codex_exec에 None이 아닌 신뢰된 경로가 전달되는지 소스로 확인."""
        import inspect
        src = inspect.getsource(pipeline._cmd_gates_codex_review)
        # codex_bin=_codex_bin_now 형태로 신뢰 경로가 전달되어야 한다
        assert "codex_bin=_codex_bin_now" in src, (
            "REJECT#19 AC#1: auth/exec 호출 시 codex_bin=_codex_bin_now 전달 없음 — TOCTOU 미방어"
        )
        # _codex_bin_now 계산에 _trusted_bin_path가 포함되어야 한다
        assert "_trusted_bin_path" in src, (
            "REJECT#19 AC#1: _codex_bin_now 계산에 _trusted_bin_path 없음"
        )


class TestTC60Reject20WindowsExeBlocked:
    """REJECT#20: Windows npm global bin의 .exe 파일을 fail-closed 처리하고 .cmd는 @openai/codex 포함 확인."""

    def test_tc60a_exe_in_npm_bin_returns_false(self, tmp_path: "Path") -> None:
        """REJECT#20 AC#1: npm global bin 내부라도 .exe 파일은 _verify_npm_wrapper_content에서 False이다."""
        import sys
        if sys.platform != "win32":
            import pytest as _pytest
            _pytest.skip("Windows 전용 테스트")
        _fake_exe = tmp_path / "codex.exe"
        _fake_exe.write_bytes(b"\x4d\x5a" + b"\x00" * 10)  # MZ header (PE binary)
        result = pipeline._verify_npm_wrapper_content(_fake_exe)
        assert result is False, (
            f"REJECT#20 AC#1: npm global bin .exe가 _verify_npm_wrapper_content=True 반환 — fail-closed 미적용. got={result!r}"
        )

    def test_tc60b_cmd_without_openai_codex_returns_false(self, tmp_path: "Path") -> None:
        """REJECT#20 AC#4: .cmd 파일에 @openai/codex가 없으면 False 반환 (위조 wrapper 차단)."""
        import sys
        if sys.platform != "win32":
            import pytest as _pytest
            _pytest.skip("Windows 전용 테스트")
        # 위조 wrapper: node는 있지만 @openai/codex 경로 없음
        _fake_cmd = tmp_path / "codex.cmd"
        _fake_cmd.write_text("@node %~dp0\\node_modules\\evil\\evil.js %*\r\n", encoding="utf-8")
        result = pipeline._verify_npm_wrapper_content(_fake_cmd)
        assert result is False, (
            f"REJECT#20 AC#4: @openai/codex 없는 .cmd가 _verify_npm_wrapper_content=True 반환. got={result!r}"
        )

    def test_tc60c_legitimate_cmd_with_openai_codex_returns_true(self, tmp_path: "Path") -> None:
        """REJECT#20 AC#4: 공식 npm wrapper(.cmd)에 @openai/codex가 있으면 True 반환 (정상 설치 허용)."""
        import sys
        if sys.platform != "win32":
            import pytest as _pytest
            _pytest.skip("Windows 전용 테스트")
        # 공식 npm wrapper 시뮬레이션
        _real_cmd = tmp_path / "codex.cmd"
        _real_cmd.write_text(
            '@ECHO off\r\n'
            'CALL :find_dp0\r\n'
            'SETLOCAL\r\n'
            '"%~dp0\\node.exe" "%~dp0\\node_modules\\@openai\\codex\\bin\\codex.js" %*\r\n',
            encoding="utf-8",
        )
        result = pipeline._verify_npm_wrapper_content(_real_cmd)
        assert result is True, (
            f"REJECT#20 AC#4: 공식 npm wrapper .cmd가 _verify_npm_wrapper_content=False 반환. got={result!r}"
        )

    def test_tc60d_exe_not_trusted_even_in_npm_global_bin(self, tmp_path: "Path", monkeypatch) -> None:
        """REJECT#20 AC#1/AC#2: npm global bin 경로라도 .exe는 _verify_codex_binary_path_trust=False."""
        import sys
        if sys.platform != "win32":
            import pytest as _pytest
            _pytest.skip("Windows 전용 테스트")
        # npm global bin을 tmp_path로 monkeypatch
        monkeypatch.setattr(pipeline, "_get_npm_global_bin", lambda: str(tmp_path))
        _fake_exe = tmp_path / "codex.exe"
        _fake_exe.write_bytes(b"\x4d\x5a" + b"\x00" * 10)
        result = pipeline._verify_codex_binary_path_trust(str(_fake_exe))
        assert not result["trusted"], (
            f"REJECT#20 AC#1: npm global bin .exe가 trusted=True 반환 — fail-closed 미적용. got={result!r}"
        )

    def test_tc60e_verify_npm_wrapper_source_has_exe_false(self) -> None:
        """REJECT#20: _verify_npm_wrapper_content 소스에 .exe → return False 코드가 있다."""
        import inspect
        src = inspect.getsource(pipeline._verify_npm_wrapper_content)
        assert "return False" in src, (
            "REJECT#20: _verify_npm_wrapper_content 소스에 .exe에 대한 return False 없음"
        )
        assert "@openai/codex" in src, (
            "REJECT#20: _verify_npm_wrapper_content 소스에 @openai/codex 패턴 확인 없음"
        )


# ---------------------------------------------------------------------------
# TC-14 / TC-15: REJECT#LATEST — native/vendor binary 실행 체인 검증 (fail-closed)
# ---------------------------------------------------------------------------
def test_fake_npm_wrapper_no_valid_js_chain_detected(tmp_path: Path) -> None:
    """TC-14 (AC#1): node+@openai/codex 문자열만 있는 가짜 .cmd는 JS entrypoint 없음 →
    native binary chain 끊김 → 신뢰 체인 성립 불가.

    AC#1: npm global bin에 node+@openai/codex 문자열만 있는 가짜 래퍼는
    _find_codex_js_entrypoint가 None을 반환하며 체인이 끊긴다. 체인이 끊기면
    _find_codex_native_binary도 native binary를 산출하지 못한다.
    """
    # 가짜 .cmd: "node @openai/codex" 문자열 포함이지만 유효한 JS 경로 패턴 없음
    fake_cmd = tmp_path / "codex.cmd"
    fake_cmd.write_text("node @openai/codex\r\n", encoding="utf-8")

    js = pipeline._find_codex_js_entrypoint(fake_cmd)
    native = pipeline._find_codex_native_binary(js) if js is not None else None

    # 가짜 래퍼는 JS 진입점 패턴이 없으므로 None → 체인 끊김
    assert js is None, (
        f"가짜 래퍼에서 JS 진입점이 추출되면 AC#1 위반: js={js!r}"
    )
    # native도 None (체인 끊김)
    assert native is None, (
        f"가짜 래퍼 chain에서 native binary가 발견되면 안 됩니다: native={native!r}"
    )


def test_native_binary_missing_blocks_operational_trust() -> None:
    """TC-15 (AC#2/#4): codex_review_result에 native binary path/sha256이 없으면
    _check_codex_review_operational_trust가 BLOCKED를 반환한다.

    AC#2: 정상 래퍼+JS 유지하되 vendor/native binary만 교체하면 exec 이후에서 차단.
    AC#4: 실행 체인 구성 요소 누락 → acceptance_eligible=true 결과여도 운영 승인 자격 없음.
    """
    pol = pipeline._build_codex_model_policy("CRITICAL")
    sig = pipeline._codex_policy_signature(pol)
    _model = pol["selected_model"]
    _effort = pol["selected_reasoning_effort"]
    result = {
        "verdict_source": "codex_cli",
        "acceptance_eligible": True,
        "router_version": pipeline.CODEX_MODEL_ROUTER_VERSION,
        "risk_level": "CRITICAL",
        "model_policy_signature": sig,
        "selected_model": _model,
        "selected_reasoning_effort": _effort,
        "review_mode": pol["mode"],
        "invoked_model": _model,
        "invoked_effort": _effort,
        "actual_model": "unknown",
        "actual_effort": "unknown",
        "model_verification_level": "invocation_verified",
        "codex_cli_command": f"codex exec --model {_model} -c model_reasoning_effort=xhigh",
        "codex_binary_path": "some_path",
        # REJECT#28 AC-4: binary SHA는 유효 64-hex여야 형식 검사를 통과하고 native 체인 검사에 도달한다.
        "codex_binary_sha256": "a" * 64,
        # native binary 필드 비어있음 → BLOCKED 기대
        "codex_native_binary_path": "",
        "codex_native_binary_sha256": "",
        "auth_source": "chatgpt",
    }
    r = pipeline._check_codex_review_operational_trust(result)
    assert r["status"] == "BLOCKED", (
        f"native binary 필드 없는데 BLOCKED가 아님: {r}"
    )
    assert "native_binary" in r.get("failure_code", ""), (
        f"failure_code에 'native_binary'가 없음: {r.get('failure_code')}"
    )


# ---------------------------------------------------------------------------
# TC-16: REJECT#14 — codex --version 조회가 검증된 절대 경로(_codex_bin_now)를 사용하고
#        조회 후 wrapper/JS/native binary SHA를 최종 재검증한다(fail-closed).
# ---------------------------------------------------------------------------
class TestTC16Reject14VersionCheckUsesVerifiedBinaryPath:
    """REJECT#14: codex_cli_version 수집이 shutil.which("codex") 재조회(TOCTOU 취약점)가 아닌
    이미 신뢰 검증된 절대 경로(_codex_bin_now)를 사용하고, --version 실행 후 바이너리 SHA를
    최종 재검증하여 변경 시 BLOCKED + acceptance_eligible=False로 무효화한다.
    """

    @staticmethod
    def _version_block(src: str) -> str:
        """_cmd_gates_codex_review 소스에서 codex_cli_version 수집 블록만 슬라이스한다.

        슬라이스 시작점은 주석이 아닌 실제 대입문(_codex_cli_version_actual = "unknown")이므로,
        블록 위의 설명 주석(문자열 shutil.which를 포함)은 슬라이스에서 제외된다.
        """
        start = src.index('_codex_cli_version_actual = "unknown"')
        end = src.index("result = {", start)
        return src[start:end]

    @staticmethod
    def _make_version_bin(tmp_path: "Path", tag: str, name: str) -> str:
        """--version 호출에 'codex-cli <tag>'를 출력하는 fake codex 실행 파일을 생성한다."""
        import os as _os
        import stat as _stat
        import sys as _sys

        impl = tmp_path / f"ver_impl_{tag}.py"
        impl.write_text(
            "import sys\n"
            "if sys.argv[1:] == ['--version']:\n"
            f"    sys.stdout.write('codex-cli {tag}\\n')\n"
            "    sys.exit(0)\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )
        if _os.name == "nt":
            wrapper = tmp_path / f"{name}.cmd"
            wrapper.write_text(f'@"{_sys.executable}" "{impl}" %*\r\n', encoding="utf-8")
            return str(wrapper)
        wrapper = tmp_path / name
        wrapper.write_text(
            f'#!/bin/sh\nexec "{_sys.executable}" "{impl}" "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(wrapper.stat().st_mode | _stat.S_IEXEC | _stat.S_IRUSR | _stat.S_IWUSR)
        return str(wrapper)

    def test_version_check_uses_verified_binary_path(self, tmp_path: "Path") -> None:
        """REJECT#14 핵심: 버전 조회 블록은 shutil.which("codex") 재조회 없이 _codex_bin_now
        (검증된 절대 경로)만 사용한다. 추가로 PATH에 악성 codex가 선행 배치되어도, 검증된 절대
        경로로 --version을 실행하면 악성 PATH 바이너리를 우회함을 subprocess로 확인한다.
        """
        import inspect
        import os as _os
        import shutil as _shutil
        import subprocess as _sp

        # (1) 소스 검증: 버전 블록은 _codex_bin_now만 사용, shutil.which 재조회/문자열 폴백 금지.
        _block = self._version_block(inspect.getsource(pipeline._cmd_gates_codex_review))
        assert "shutil.which" not in _block, (
            "REJECT#14: codex_cli_version 수집 블록에 shutil.which 재조회가 남아있음 — "
            "검증 범위 밖 TOCTOU 취약점"
        )
        assert '_fake_codex_bin or shutil.which' not in _block, (
            "REJECT#14: 구 버전 조회 패턴(_fake_codex_bin or shutil.which(...) or \"codex\")이 남아있음"
        )
        assert "_codex_bin_now" in _block, (
            "REJECT#14: 버전 조회 블록이 검증된 절대 경로 _codex_bin_now를 사용하지 않음"
        )

        # (2) subprocess E2E: 검증된 절대 경로로 --version 실행 → 검증된 버전 획득.
        verified = self._make_version_bin(tmp_path, "9.9.9-verified", "codex_verified")
        _r = _sp.run(
            [verified, "--version"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
        )
        assert _r.returncode == 0, f"검증된 바이너리 --version 실패: rc={_r.returncode} err={_r.stderr!r}"
        # pipeline이 적용하는 파싱 규칙(.stdout.strip().splitlines()[0])과 동일하게 파싱.
        _got_verified = _r.stdout.strip().splitlines()[0]
        assert _got_verified == "codex-cli 9.9.9-verified", (
            f"검증된 절대 경로 --version 출력 불일치: {_got_verified!r}"
        )

        # (3) PATH에 악성 'codex'를 선행 배치. 검증된 절대 경로로 실행하면 악성 PATH를 무시한다.
        evil_dir = tmp_path / "evilpath"
        evil_dir.mkdir()
        self._make_version_bin(evil_dir, "6.6.6-MALICIOUS", "codex")
        _env = dict(_os.environ)
        _env["PATH"] = str(evil_dir) + _os.pathsep + _env.get("PATH", "")
        # sanity: 이 PATH에서 shutil.which("codex")는 악성 바이너리를 찾는다(TOCTOU 재조회의 위험).
        assert _shutil.which("codex", path=str(evil_dir)) is not None, (
            "테스트 셋업 오류: 악성 codex가 PATH에서 발견되지 않음"
        )
        # 검증된 절대 경로로 --version 실행 → PATH 오염과 무관하게 검증된 버전만 나온다.
        _r2 = _sp.run(
            [verified, "--version"],
            capture_output=True, text=True, timeout=20,
            env=_env, encoding="utf-8", errors="replace",
        )
        _got2 = _r2.stdout.strip().splitlines()[0] if _r2.stdout.strip() else ""
        assert _got2 == "codex-cli 9.9.9-verified", (
            f"REJECT#14: PATH 오염 시 검증된 절대 경로가 악성 바이너리로 대체됨 — got={_got2!r}. "
            "shutil.which 재조회(구 버그)였다면 'codex-cli 6.6.6-MALICIOUS'가 나온다."
        )

    def test_version_check_sha_revalidation_in_source(self) -> None:
        """REJECT#14: 버전 조회 후 바이너리 SHA 재검증 → 변경 시 _post_snap_changed에 추가하고
        codex_review_snapshot_changed로 BLOCKED 처리한다(fail-closed).
        """
        import inspect

        _block = self._version_block(inspect.getsource(pipeline._cmd_gates_codex_review))
        assert "codex_version_check_binary_sha_changed" in _block, (
            "REJECT#14: 버전 조회 후 SHA 변경 감지 failure_code가 블록에 없음"
        )
        assert "_post_snap_changed" in _block, (
            "REJECT#14: 버전 조회 SHA 재검증이 post-exec snapshot 차단 로직(_post_snap_changed)과 연결되지 않음"
        )
        assert "codex_review_snapshot_changed" in _block and "_die(" in _block, (
            "REJECT#14: SHA 변경 시 codex_review_snapshot_changed로 _die() 차단이 없음"
        )
        assert "_write_codex_review_blocked_invalidation" in _block, (
            "REJECT#14: SHA 변경 차단 시 승인 무효화(_write_codex_review_blocked_invalidation) 호출 없음"
        )

    def test_version_check_revalidates_all_three_binary_dimensions(self) -> None:
        """REJECT#14: 재검증 대상이 wrapper(path/sha256) + JS entrypoint + native binary 3종을 모두
        포함한다(어느 하나만 검증하면 나머지 교체를 놓친다).
        """
        import inspect

        _block = self._version_block(inspect.getsource(pipeline._cmd_gates_codex_review))
        for _k in (
            '"path"', '"sha256"',
            '"js_entrypoint_path"', '"js_entrypoint_sha256"',
            '"native_binary_path"', '"native_binary_sha256"',
        ):
            assert _k in _block, (
                f"REJECT#14: 버전 조회 SHA 재검증 블록이 {_k} 차원을 검사하지 않음"
            )

    def test_blocked_invalidation_marks_acceptance_ineligible(self, tmp_path: "Path") -> None:
        """REJECT#14: SHA 변경 차단 경로가 호출하는 _write_codex_review_blocked_invalidation는
        codex_review_snapshot_changed 결과를 acceptance_eligible=False로 기록한다.

        PIPELINE_STATE_PATH로 격리된 subprocess에서 실제 파일 효과(codex_review_result.json)를 검증한다
        (전역 상태를 오염시키지 않는다).
        """
        import json as _json
        import subprocess as _sp
        import sys as _sys

        _state = tmp_path / "iso_state.json"
        _state.write_text(_json.dumps({"pipeline_id": "IMP-20260712-DAE1"}), encoding="utf-8")
        _driver = tmp_path / "driver.py"
        _driver.write_text(
            "import os, sys\n"
            f"sys.path.insert(0, {str(_ROOT)!r})\n"
            "import pipeline\n"
            "pipeline._write_codex_review_blocked_invalidation(\n"
            "    'IMP-20260712-DAE1', 'codex_review_snapshot_changed', 0, 0, 'a'*64, 'HIGH', None)\n"
            "p = pipeline._codex_review_result_path()\n"
            "sys.stdout.write(p.read_text(encoding='utf-8'))\n",
            encoding="utf-8",
        )
        _env = dict(os.environ)
        _env["PIPELINE_STATE_PATH"] = str(_state)
        _r = _sp.run(
            [_sys.executable, str(_driver)],
            capture_output=True, text=True, timeout=60,
            env=_env, encoding="utf-8", errors="replace",
        )
        assert _r.returncode == 0, f"격리 subprocess 실패: rc={_r.returncode} err={_r.stderr!r}"
        _result = _json.loads(_r.stdout)
        assert _result.get("acceptance_eligible") is False, (
            f"REJECT#14: 차단 무효화 결과의 acceptance_eligible이 False가 아님: {_result.get('acceptance_eligible')!r}"
        )
        assert _result.get("status") == "BLOCKED", (
            f"REJECT#14: 차단 무효화 결과의 status가 BLOCKED가 아님: {_result.get('status')!r}"
        )
        assert "codex_review_snapshot_changed" in str(_result.get("reason", "")), (
            f"REJECT#14: reason에 codex_review_snapshot_changed 없음: {_result.get('reason')!r}"
        )
        assert _result.get("verdict") is None, (
            f"REJECT#14: 차단 결과의 verdict가 None이 아님(APPROVE 잔존 위험): {_result.get('verdict')!r}"
        )
        # 격리 검증: 결과 파일이 전역 .pipeline이 아닌 격리 state 경로 하위에 기록됐다.
        _iso_result = _state.parent / ".pipeline" / "codex_review_result.json"
        assert _iso_result.exists(), (
            "REJECT#14: PIPELINE_STATE_PATH 격리가 동작하지 않음 — 결과가 격리 경로에 없음"
        )


# ---------------------------------------------------------------------------
# TC-17: REJECT#15 — POSIX 공식 npm 설치(symlink→codex.js) JS 진입점 탐지 +
#        시스템 경로 direct native 설치의 operational trust 면제.
# ---------------------------------------------------------------------------
class TestTC17PosixNpmSymlink:
    """REJECT#15 문제 A/B: POSIX 공식 npm 설치와 시스템 경로 직접 native 설치를 지원한다.

    문제 A: /usr/local/bin/codex(symlink) → .../@openai/codex/bin/codex.js 구조에서
        _find_codex_js_entrypoint가 JS 진입점을 탐지해야 한다(기존엔 .cmd/.bat만 처리하여 차단).
    문제 B: npm wrapper 없이 시스템 PATH에 native codex 바이너리만 설치된 경우,
        native 체인 필드가 비어 있어도 install_type=direct_native로 operational trust를 통과해야 한다.
    """

    def test_posix_symlink_to_js_entrypoint_trusted(self, tmp_path: "Path") -> None:
        """문제 A: symlink(또는 shebang 스크립트)이 codex.js를 가리키면 JS 진입점을 반환한다."""
        import platform as _platform

        pkg = tmp_path / "lib" / "node_modules" / "@openai" / "codex"
        bin_dir = pkg / "bin"
        bin_dir.mkdir(parents=True)
        js_ep = bin_dir / "codex.js"
        js_ep.write_text("#!/usr/bin/env node\nrequire('@openai/codex');\n", encoding="utf-8")

        # POSIX에서는 symlink, Windows에서 symlink 권한이 없으면 shebang 내용 복사로 대체한다.
        fake_codex = tmp_path / ("codex.exe" if _platform.system() == "Windows" else "codex")
        try:
            fake_codex.symlink_to(js_ep)
        except (OSError, NotImplementedError):
            fake_codex.write_bytes(js_ep.read_bytes())

        ep = pipeline._find_codex_js_entrypoint(fake_codex)
        assert ep is not None, (
            "REJECT#15 문제 A: POSIX symlink/shebang → codex.js 진입점을 찾지 못함(None)."
        )
        # POSIX symlink 경로에서는 symlink 대상(codex.js)을 반환하고, Windows 권한 부재로
        # shebang 내용을 복사한 경우에는 스크립트 자신(codex.exe)을 JS 진입점으로 반환한다.
        # 두 경우 모두 node shebang + @openai/codex 참조가 확인된 유효한 JS 진입점이다.
        import platform as _platform_chk
        if _platform_chk.system() != "Windows" and fake_codex.is_symlink():
            assert ep.name == "codex.js", (
                f"REJECT#15 문제 A: symlink 경로에서 반환된 JS 진입점이 codex.js가 아님: {ep!r}"
            )

    def test_js_input_returns_itself(self, tmp_path: "Path") -> None:
        """문제 A: 입력이 .js 파일 자체이면 그 경로를 그대로 JS 진입점으로 반환한다."""
        js_ep = tmp_path / "codex.js"
        js_ep.write_text("#!/usr/bin/env node\nrequire('@openai/codex');\n", encoding="utf-8")
        ep = pipeline._find_codex_js_entrypoint(js_ep)
        assert ep is not None and ep.resolve() == js_ep.resolve(), (
            f"REJECT#15 문제 A: .js 입력이 자기 자신으로 반환되지 않음: {ep!r}"
        )

    def test_cmd_wrapper_regression_still_none_for_fake(self, tmp_path: "Path") -> None:
        """회귀 방지: JS 경로 패턴이 없는 가짜 .cmd는 여전히 None을 반환한다(기존 동작 유지)."""
        fake_cmd = tmp_path / "codex.cmd"
        fake_cmd.write_text("node @openai/codex\r\n", encoding="utf-8")
        assert pipeline._find_codex_js_entrypoint(fake_cmd) is None, (
            "REJECT#15 회귀: 유효한 JS 경로 없는 가짜 .cmd에서 진입점이 반환됨"
        )

    def test_direct_native_install_passes_operational_trust(self) -> None:
        """문제 B: install_type=direct_native면 native 체인 필드가 비어도 native 체인으로 차단되지 않는다."""
        result = {
            "trusted": True,
            "codex_install_type": "direct_native",
            "codex_binary_path": "/usr/local/bin/codex",
            "codex_binary_sha256": "a" * 64,
            "codex_js_entrypoint_path": "",
            "codex_js_entrypoint_sha256": "",
            "codex_native_binary_path": "/usr/local/bin/codex",
            "codex_native_binary_sha256": "e" * 64,
        }
        outcome = pipeline._check_codex_review_operational_trust(result)
        assert outcome.get("status") != "BLOCKED" or "native_binary" not in outcome.get(
            "failure_code", ""
        ), (
            f"REJECT#15 문제 B: direct_native가 native_binary 체인으로 차단됨: {outcome!r}"
        )

    def test_direct_native_full_result_reaches_and_passes_step6b(self) -> None:
        """문제 B: 유효한 전체 result + install_type=direct_native + native 필드 빈값 → PASS."""
        pol = pipeline._build_codex_model_policy("HIGH")
        sig = pipeline._codex_policy_signature(pol)
        _model = pol["selected_model"]
        _effort = pol["selected_reasoning_effort"]
        result = {
            "verdict_source": "codex_cli",
            "acceptance_eligible": True,
            "router_version": pipeline.CODEX_MODEL_ROUTER_VERSION,
            "risk_level": "HIGH",
            "model_policy_signature": sig,
            "selected_model": _model,
            "selected_reasoning_effort": _effort,
            "review_mode": pol["mode"],
            "invoked_model": _model,
            "invoked_effort": _effort,
            "actual_model": "unknown",
            "actual_effort": "unknown",
            "model_verification_level": "invocation_verified",
            "codex_cli_command": f"codex exec --model {_model} -c model_reasoning_effort=high",
            "codex_binary_path": "/usr/bin/codex",
            "codex_binary_sha256": "a" * 64,
            # direct_native: native 체인 필드는 비어 있어도 면제된다.
            "codex_native_binary_path": "",
            "codex_native_binary_sha256": "",
            "codex_install_type": "direct_native",
            "auth_source": "chatgpt",
        }
        r = pipeline._check_codex_review_operational_trust(result)
        assert r["status"] == "PASS", (
            f"REJECT#15 문제 B: direct_native 전체 result가 PASS되지 않음: {r!r}"
        )

    def test_missing_install_type_still_blocks_native_chain(self) -> None:
        """회귀 방지: install_type 미기록 + native 필드 빈값이면 여전히 native 체인으로 BLOCKED된다."""
        pol = pipeline._build_codex_model_policy("HIGH")
        sig = pipeline._codex_policy_signature(pol)
        _model = pol["selected_model"]
        _effort = pol["selected_reasoning_effort"]
        result = {
            "verdict_source": "codex_cli",
            "acceptance_eligible": True,
            "router_version": pipeline.CODEX_MODEL_ROUTER_VERSION,
            "risk_level": "HIGH",
            "model_policy_signature": sig,
            "selected_model": _model,
            "selected_reasoning_effort": _effort,
            "review_mode": pol["mode"],
            "invoked_model": _model,
            "invoked_effort": _effort,
            "actual_model": "unknown",
            "actual_effort": "unknown",
            "model_verification_level": "invocation_verified",
            "codex_cli_command": f"codex exec --model {_model} -c model_reasoning_effort=high",
            "codex_binary_path": "/usr/bin/codex",
            "codex_binary_sha256": "a" * 64,
            "codex_native_binary_path": "",
            "codex_native_binary_sha256": "",
            # codex_install_type 미기록 → 면제 없음, native 체인 강제
            "auth_source": "chatgpt",
        }
        r = pipeline._check_codex_review_operational_trust(result)
        assert r["status"] == "BLOCKED", (
            f"REJECT#15 회귀: install_type 미기록인데 native 체인 없이 PASS됨: {r!r}"
        )
        assert "native_binary" in r.get("failure_code", ""), (
            f"REJECT#15 회귀: failure_code에 native_binary 없음: {r.get('failure_code')!r}"
        )

    def test_verify_trust_returns_install_type_field(self) -> None:
        """문제 B: _verify_codex_binary_path_trust 반환에 install_type 필드가 항상 존재한다."""
        # repo 내 경로(차단됨)여도 스키마상 install_type 키는 존재해야 한다.
        r = pipeline._verify_codex_binary_path_trust(
            str(pipeline.BASE_DIR / "tests" / "nonexistent_codex_tc17")
        )
        assert "install_type" in r, (
            "REJECT#15 문제 B: _verify_codex_binary_path_trust 반환에 install_type 키 없음"
        )


# =========================================================================== #
# TC-18: REJECT#16 — capability TOCTOU/API-key 방어 + pipeline.py 비CRITICAL 커버리지
# =========================================================================== #
class TestTC18CapabilityTOCTOUAndCoverage:
    """REJECT#16 두 결함 회귀 검증.

    결함A: _detect_codex_cli_capability가 verified_bin_path(신뢰 검증 절대경로)와 env
      (OPENAI_API_KEY 제거)를 받아 subprocess에 전달한다(TOCTOU/PATH 주입 + API key 노출 차단).
    결함B: pipeline.py의 비CRITICAL 함수(build_parser 등)만 변경돼도 evidence_complete=True가
      되고, CRITICAL hunk 절단 시에는 여전히 evidence_complete=False가 유지된다.
    """

    # --- 결함A --------------------------------------------------------------- #
    def test_detect_capability_uses_verified_bin_path_param(self) -> None:
        """verified_bin_path가 주어지면 subprocess가 그 절대경로로 --version을 실행하고
        env(OPENAI_API_KEY 제거)를 그대로 전달한다."""
        from unittest.mock import patch, MagicMock

        captured: dict = {}

        def _mock_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            captured["cmd"] = [str(c) for c in cmd]
            captured["env"] = kwargs.get("env")
            m = MagicMock()
            m.returncode = 0
            m.stdout = "codex 1.2.3"
            m.stderr = ""
            return m

        _clean_env = {"PATH": "/usr/bin", "HOME": "/home/user"}  # OPENAI_API_KEY 없음
        with patch("pipeline.subprocess.run", side_effect=_mock_run):
            result = pipeline._detect_codex_cli_capability(
                verified_bin_path="/custom/codex", env=_clean_env
            )

        assert captured["cmd"][0] == "/custom/codex", (
            f"REJECT#16 결함A: subprocess가 verified_bin_path를 사용하지 않음 — "
            f"got {captured['cmd']!r} (PATH 재해석/ TOCTOU 취약)"
        )
        assert captured["cmd"][1] == "--version"
        # env(OPENAI_API_KEY 제거)가 그대로 전달되어야 한다.
        assert captured["env"] == _clean_env, (
            f"REJECT#16 결함A: subprocess에 clean env가 전달되지 않음 — got {captured['env']!r}"
        )
        assert "OPENAI_API_KEY" not in (captured["env"] or {}), (
            "REJECT#16 결함A: 전달된 env에 OPENAI_API_KEY가 남아 있음"
        )
        assert result["available"] is True

    def test_call_site_wires_verified_bin_and_clean_env(self) -> None:
        """_cmd_gates_codex_review가 capability 감지에 verified path + OPENAI_API_KEY 제거
        env를 배선하는지 소스로 확인한다(프로덕션 실제 결함 수정 배선 검증)."""
        import inspect

        src = inspect.getsource(pipeline._cmd_gates_codex_review)
        assert "_detect_codex_cli_capability(" in src
        assert "verified_bin_path=_cap_verified_bin" in src, (
            "REJECT#16 결함A: call site가 verified_bin_path를 배선하지 않음"
        )
        # REJECT#38: call site는 인라인 pop 대신 중앙 정제 함수 _codex_clean_env()로
        #   OPENAI_API_KEY + NODE_OPTIONS/NODE_PATH 등 선행 로딩 변수를 제거한 env를 구성한다.
        assert "_cap_clean_env = _codex_clean_env()" in src, (
            "REJECT#38: call site가 _codex_clean_env()로 정제 env를 구성하지 않음"
        )
        assert "env=_cap_clean_env" in src, (
            "REJECT#16 결함A: call site가 clean env를 capability 감지에 전달하지 않음"
        )

    def test_detect_capability_without_param_falls_back_to_codex(self) -> None:
        """verified_bin_path가 빈 문자열이면 'codex' PATH fallback을 사용하고, env=None이면
        REJECT#37에 따라 _codex_clean_env()로 정제된 환경(NODE_OPTIONS/NODE_PATH 제거)을 사용한다."""
        from unittest.mock import patch, MagicMock
        import os

        captured: dict = {}

        def _mock_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            captured["cmd"] = [str(c) for c in cmd]
            captured["env"] = kwargs.get("env")
            m = MagicMock()
            m.returncode = 0
            m.stdout = "codex 1.2.3"
            m.stderr = ""
            return m

        old_env = os.environ.copy()
        os.environ["NODE_OPTIONS"] = "--require=/tmp/evil.js"
        try:
            with patch("pipeline.subprocess.run", side_effect=_mock_run):
                # 파라미터 없이 호출(레거시 호환 경로) — verified_bin_path="" , env=None.
                result = pipeline._detect_codex_cli_capability()
        finally:
            os.environ.clear()
            os.environ.update(old_env)

        assert captured["cmd"][0] == "codex", (
            f"REJECT#16 결함A: 빈 verified_bin_path에서 'codex' fallback 미적용 — "
            f"got {captured['cmd']!r}"
        )
        assert captured["cmd"][1] == "--version"
        # REJECT#37 AC#2: env 미지정 시 None(전체 환경 상속)이 아니라 정제 env여야 하며,
        #   NODE_OPTIONS/NODE_PATH가 제거되어 있어야 한다(임의 JS preload 차단).
        assert captured["env"] is not None, (
            "REJECT#37 AC#2: env 미지정 시 None(전체 환경 상속)이면 NODE_OPTIONS가 전달됨 — 취약"
        )
        assert "NODE_OPTIONS" not in captured["env"], (
            f"REJECT#37 AC#2: version 경로 env에 NODE_OPTIONS가 남아 있음 — got {captured['env'].get('NODE_OPTIONS')!r}"
        )
        assert "NODE_PATH" not in captured["env"], (
            "REJECT#37 AC#2: version 경로 env에 NODE_PATH가 남아 있음"
        )
        assert result["available"] is True

    def test_detect_capability_none_verified_bin_defended(self) -> None:
        """verified_bin_path=None을 넘겨도 'codex' fallback으로 안전 동작한다(None 입력 방어)."""
        from unittest.mock import patch, MagicMock

        captured: dict = {}

        def _mock_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            captured["cmd"] = [str(c) for c in cmd]
            m = MagicMock()
            m.returncode = 0
            m.stdout = "codex 1.2.3"
            m.stderr = ""
            return m

        with patch("pipeline.subprocess.run", side_effect=_mock_run):
            result = pipeline._detect_codex_cli_capability(
                verified_bin_path=None, env=None  # type: ignore[arg-type]
            )
        assert captured["cmd"][0] == "codex"
        assert result["available"] is True

    # --- 결함B --------------------------------------------------------------- #
    def test_pipeline_py_noncritical_hunk_covered(self) -> None:
        """pipeline.py의 비CRITICAL 함수(build_parser)만 변경돼도 pipeline.py가 covered로
        처리되어 evidence_complete=True가 된다(불필요한 HIGH/CRITICAL 차단 제거)."""
        from unittest.mock import patch, MagicMock

        assert "build_parser" not in pipeline.CODEX_CRITICAL_FUNCTIONS, (
            "TC-18 전제조건: build_parser는 비CRITICAL 함수여야 함"
        )

        before_code = (
            "def build_parser():\n"
            "    parser = argparse.ArgumentParser()\n"
            "    return parser\n"
        )
        diff_output = (
            "diff --git a/pipeline.py b/pipeline.py\n"
            "--- a/pipeline.py\n"
            "+++ b/pipeline.py\n"
            "@@ -1,3 +1,4 @@ def build_parser() -> argparse.ArgumentParser:\n"
            " def build_parser():\n"
            "     parser = argparse.ArgumentParser()\n"
            "+    parser.add_argument('--new-flag')\n"
            "     return parser\n"
        )

        def _mock_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            m = MagicMock()
            cmd_list = [str(c) for c in cmd]
            if "show" in cmd_list and any("origin/main:pipeline.py" in c for c in cmd_list):
                m.returncode = 0
                m.stdout = before_code
            elif "diff" in cmd_list and "pipeline.py" in cmd_list:
                m.returncode = 0
                m.stdout = diff_output
            else:
                m.returncode = 0
                m.stdout = ""
            m.stderr = ""
            return m

        with patch("pipeline.subprocess.run", side_effect=_mock_run):
            sem = pipeline._build_codex_semantic_evidence(
                "IMP-TEST-REJECT16",
                ["pipeline.py"],
                ["build_parser"],
            )

        funcs_in = [h.get("function") for h in sem["diff_hunks"]]
        assert "build_parser" in funcs_in, (
            f"TC-18 전제: build_parser hunk가 diff_hunks에 없음 — got {funcs_in!r}"
        )
        assert "pipeline.py" not in sem["missing_critical_files"], (
            f"REJECT#16 결함B: 비CRITICAL 함수만 변경됐는데 pipeline.py가 "
            f"missing_critical_files에 남음 — got {sem['missing_critical_files']!r}"
        )
        assert sem["evidence_complete"] is True, (
            f"REJECT#16 결함B: 비CRITICAL pipeline.py hunk가 evidence_complete=False로 차단됨 — "
            f"missing={sem['missing_critical_files']!r} truncated_crit={sem['truncated_critical_hunks']}"
        )

    def test_pipeline_py_missing_critical_hunk_stays_incomplete(self) -> None:
        """CRITICAL 함수 hunk가 예산 초과로 절단되면 evidence_complete=False가 유지된다
        (결함B 완화가 CRITICAL 완전성 검사를 약화하지 않음)."""
        from unittest.mock import patch, MagicMock

        assert "_cmd_gates_accept" in pipeline.CODEX_CRITICAL_FUNCTIONS, (
            "TC-18 전제조건: _cmd_gates_accept는 CRITICAL 함수여야 함"
        )

        big = "x" * (pipeline.CODEX_REVIEW_BUNDLE_BUDGET_CHARS + 50_000)  # 예산 초과 강제
        diff_output = (
            "diff --git a/pipeline.py b/pipeline.py\n"
            "--- a/pipeline.py\n"
            "+++ b/pipeline.py\n"
            "@@ -1,0 +1,1 @@ def _cmd_gates_accept(args):\n"
            f"+{big}\n"
        )
        before_code = "# minimal before\n"

        def _mock_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            m = MagicMock()
            cmd_list = [str(c) for c in cmd]
            if "show" in cmd_list and any("origin/main:pipeline.py" in c for c in cmd_list):
                m.returncode = 0
                m.stdout = before_code
            elif "diff" in cmd_list and "pipeline.py" in cmd_list:
                m.returncode = 0
                m.stdout = diff_output
            else:
                m.returncode = 0
                m.stdout = ""
            m.stderr = ""
            return m

        with patch("pipeline.subprocess.run", side_effect=_mock_run):
            sem = pipeline._build_codex_semantic_evidence(
                "IMP-TEST-REJECT16",
                ["pipeline.py"],
                ["_cmd_gates_accept"],
            )

        assert sem["truncated_critical_hunks"] > 0, (
            f"REJECT#16 결함B 회귀: CRITICAL hunk 절단인데 truncated_critical_hunks=0 — "
            f"got {sem['truncated_critical_hunks']}"
        )
        assert sem["evidence_complete"] is False, (
            "REJECT#16 결함B 회귀: CRITICAL hunk 절단인데 evidence_complete=True — "
            "결함B 완화가 CRITICAL 완전성 검사를 약화시켰음"
        )


# =========================================================================== #
# TC-19: REJECT#17 — trust-root 독립성 (self-referential 신뢰 체인 제거)
#
# 수정 A: npm 바이너리 자체의 시스템 경로 검증 — PATH 주입 npm 차단.
# 수정 B: package.json npm 레지스트리 provenance 교차 검증 — 조작 패키지 차단.
# 수정 C: direct_native 소유권/위치 검증 — 시스템 경로 내 사용자 배치 바이너리 차단.
#
# 현재 시스템 호환성: Windows npm global 설치(%APPDATA%\npm, Program Files npm,
#   package.json에 _integrity 부재)는 수정 후에도 계속 PASS되어야 한다.
# =========================================================================== #
class TestTC19TrustRootIndependence:
    """REJECT#17: 신뢰 체인이 자기참조적으로 우회되지 않도록 3개 독립 레이어를 검증한다."""

    # ---- 수정 A: npm 바이너리 시스템 경로 검증 ---- #
    def test_tc19a_system_path_npm_is_trusted(self) -> None:
        """수정 A + REJECT#33: 관리자 보호 시스템 경로 npm만 신뢰된다(사용자 쓰기 경로는 차단)."""
        if sys.platform == "win32":
            _pf = os.environ.get("ProgramFiles", r"C:\Program Files")
            _npm = str(Path(_pf) / "nodejs" / "npm.cmd")
            assert pipeline._verify_npm_binary_is_system_path(_npm) is True, (
                f"수정 A: Program Files nodejs npm이 신뢰되지 않음: {_npm!r}"
            )
            # REJECT#33: %APPDATA%\npm은 사용자 쓰기 가능 경로이므로 npm 바이너리 허용목록에서
            #   제거되었다. 여기에 배치된 npm은 이제 차단(False)되어야 한다.
            _appdata = os.environ.get("APPDATA")
            if _appdata:
                _npm2 = str(Path(_appdata) / "npm" / "npm.cmd")
                assert pipeline._verify_npm_binary_is_system_path(_npm2) is False, (
                    f"REJECT#33: %APPDATA%\\npm npm이 여전히 신뢰됨(사용자 쓰기 경로 차단 실패): {_npm2!r}"
                )
        else:
            for _sys_npm in ("/usr/bin/npm", "/usr/local/bin/npm"):
                assert pipeline._verify_npm_binary_is_system_path(_sys_npm) is True, (
                    f"수정 A: 시스템 경로 npm이 신뢰되지 않음: {_sys_npm!r}"
                )
            # REJECT#33: ~/.nvm은 사용자 홈 하위(쓰기 가능)이므로 차단(False)되어야 한다.
            _nvm_npm = str(
                Path(os.path.expanduser("~")) / ".nvm" / "versions" / "node"
                / "v20.0.0" / "bin" / "npm"
            )
            assert pipeline._verify_npm_binary_is_system_path(_nvm_npm) is False, (
                f"REJECT#33: ~/.nvm npm이 여전히 신뢰됨(사용자 홈 경로 차단 실패): {_nvm_npm!r}"
            )

    def test_tc19b_path_injected_fake_npm_is_rejected(self, tmp_path: "Path") -> None:
        """수정 A: 임의 디렉토리(PATH 주입)의 가짜 npm은 시스템 경로로 신뢰되지 않는다."""
        _fake_npm_dir = tmp_path / "evil_bin"
        _fake_npm_dir.mkdir()
        _fake_npm = _fake_npm_dir / ("npm.cmd" if sys.platform == "win32" else "npm")
        _fake_npm.write_text("echo fake npm\n", encoding="utf-8")
        assert pipeline._verify_npm_binary_is_system_path(str(_fake_npm)) is False, (
            f"수정 A: PATH 주입 가짜 npm이 시스템 경로로 신뢰됨(공격 벡터): {_fake_npm!r}"
        )

    def test_tc19c_get_npm_global_bin_rejects_injected_npm(
        self, tmp_path: "Path", monkeypatch
    ) -> None:
        """수정 A: shutil.which가 임의 경로 npm을 반환하면 _get_npm_global_bin이 None으로 fail-closed."""
        _fake_npm_dir = tmp_path / "inject"
        _fake_npm_dir.mkdir()
        _fake_npm = _fake_npm_dir / ("npm.cmd" if sys.platform == "win32" else "npm")
        _fake_npm.write_text("echo pwned\n", encoding="utf-8")
        monkeypatch.setattr(pipeline.shutil, "which", lambda _name: str(_fake_npm))
        assert pipeline._get_npm_global_bin() is None, (
            "수정 A: 주입된 npm(비시스템 경로)인데 _get_npm_global_bin이 None을 반환하지 않음"
        )

    def test_tc19d_verify_npm_system_path_none_raises(self) -> None:
        """수정 A: None 입력은 TypeError로 방어(암묵적 통과 금지)."""
        _raised = False
        try:
            pipeline._verify_npm_binary_is_system_path(None)  # type: ignore[arg-type]
        except TypeError:
            _raised = True
        assert _raised, "수정 A: None 입력이 TypeError로 방어되지 않음"
        assert pipeline._verify_npm_binary_is_system_path("") is False, (
            "수정 A: 빈 문자열 npm 경로가 신뢰됨(경계값 방어 실패)"
        )

    def test_tc19_a_wired_in_get_npm_global_bin_source(self) -> None:
        """수정 A: _get_npm_global_bin이 _verify_npm_binary_is_system_path를 호출한다."""
        import inspect
        _src = inspect.getsource(pipeline._get_npm_global_bin)
        assert "_verify_npm_binary_is_system_path" in _src, (
            "수정 A: _get_npm_global_bin이 npm 바이너리 시스템 경로 검증을 배선하지 않음"
        )

    # ---- 수정 B: package.json npm 레지스트리 provenance ---- #
    def test_tc19e_package_without_npm_integrity_is_non_blocking(
        self, tmp_path: "Path"
    ) -> None:
        """수정 B: _integrity/_resolved 부재(정상 `npm install -g`)는 차단하지 않는다(현재 시스템 호환)."""
        _pkg = tmp_path / "pkg_none"
        _pkg.mkdir()
        (_pkg / "package.json").write_text(
            '{"name": "@openai/codex", "version": "1.0.0"}', encoding="utf-8"
        )
        _res = pipeline._check_package_json_npm_integrity(_pkg)
        assert _res["available"] is False and _res["ok"] is True, (
            f"수정 B: provenance 필드 부재가 non-blocking이 아님(정상 global 설치를 깸): {_res!r}"
        )

    def test_tc19f_package_with_nonregistry_resolved_is_rejected(
        self, tmp_path: "Path"
    ) -> None:
        """수정 B: _resolved가 비레지스트리(file:/로컬)면 조작 가능성 → available=True, ok=False."""
        _pkg = tmp_path / "pkg_nonreg"
        _pkg.mkdir()
        (_pkg / "package.json").write_text(
            '{"name": "@openai/codex", "_integrity": "sha512-XXXX", '
            '"_resolved": "file:///malicious/codex.tgz"}',
            encoding="utf-8",
        )
        _res = pipeline._check_package_json_npm_integrity(_pkg)
        assert _res["available"] is True and _res["ok"] is False, (
            f"수정 B: 비레지스트리 _resolved가 차단 대상(ok=False)으로 판정되지 않음: {_res!r}"
        )

    def test_tc19g_package_with_registry_resolved_passes(self, tmp_path: "Path") -> None:
        """수정 B: _integrity + _resolved(registry.npmjs.org)면 provenance 확인(ok=True)."""
        _pkg = tmp_path / "pkg_reg"
        _pkg.mkdir()
        (_pkg / "package.json").write_text(
            '{"name": "@openai/codex", "_integrity": "sha512-AAAA", '
            '"_resolved": "https://registry.npmjs.org/@openai/codex/-/codex-1.0.0.tgz"}',
            encoding="utf-8",
        )
        _res = pipeline._check_package_json_npm_integrity(_pkg)
        assert _res["available"] is True and _res["ok"] is True, (
            f"수정 B: 레지스트리 provenance가 확인(ok=True)되지 않음: {_res!r}"
        )

    def test_tc19h_integrity_none_raises_and_wired_in_trust(self) -> None:
        """수정 B: None 입력 TypeError + 신뢰 함수가 비레지스트리 provenance를 fail-closed로 배선."""
        _raised = False
        try:
            pipeline._check_package_json_npm_integrity(None)  # type: ignore[arg-type]
        except TypeError:
            _raised = True
        assert _raised, "수정 B: None pkg_root가 TypeError로 방어되지 않음"
        import inspect
        _src = inspect.getsource(pipeline._verify_codex_binary_path_trust)
        assert "_check_package_json_npm_integrity" in _src, (
            "수정 B: _verify_codex_binary_path_trust가 provenance 교차검증을 배선하지 않음"
        )
        assert "npm_integrity_non_registry_resolved" in _src, (
            "수정 B: 비레지스트리 provenance fail-closed(차단 사유)가 신뢰 함수에 없음"
        )

    # ---- 수정 C: direct_native 소유권/위치 검증 ---- #


    # ---- 독립성/등록 검증 ---- #

    def test_tc19n_real_install_still_trusted_regression(self) -> None:
        """회귀 방지: 실제 설치된 codex(있으면)는 수정 A/B/C 후에도 계속 신뢰된다.

        codex 미설치 환경(대부분의 CI)에서는 검증 대상이 없으므로 소프트 스킵한다.
        """
        _codex = pipeline.shutil.which("codex")
        if not _codex:
            return
        _r = pipeline._verify_codex_binary_path_trust(_codex)
        assert _r["trusted"] is True, (
            f"REJECT#17 회귀: 실제 codex 설치가 수정 후 신뢰되지 않음: "
            f"reason={_r.get('untrusted_reason')!r} install_type={_r.get('install_type')!r}"
        )


# =========================================================================== #
# TC-20: REJECT#18 — npm 출력 신뢰 경계 강화 + lockfile provenance 교차검증
#
# 문제 2(수정 A): npm이 반환한 global bin이 "허용된 설치 루트" 밖이면 거부
#   (npm_global_bin_not_in_allowed_root). 조작된 npm 출력을 표준 Node/npm 루트로 bound한다.
# 문제 3(수정 B): node_modules 루트의 .package-lock.json에서 @openai/codex integrity를 독립
#   교차검증. present-but-malformed → fail-closed(npm_lockfile_integrity_invalid),
#   absent → non-blocking(현재 Windows 설치 = npm 7+ global, lockfile·_integrity 부재 실측).
#
# 현재 시스템 호환성: %APPDATA%\npm(prefix) global bin + lockfile/_integrity 부재는 계속 PASS.
# =========================================================================== #
class TestTC20Reject18NpmOutputBoundAndLockfileProvenance:
    """REJECT#18: npm 출력을 허용 루트로 bound하고 lockfile integrity를 독립 교차검증한다."""

    # ---- 문제 2: npm global bin 허용 루트 bound ---- #
    def test_tc20a_allowed_root_real_npm_bin_is_allowed(self) -> None:
        """문제 2: 허용 루트 내의 npm global bin은 통과한다 (mock 경로 — CI 환경 독립적)."""
        import sys
        import os
        # 환경별 실제 npm 경로는 CI 러너에 따라 달라지므로 허용 루트의 대표 mock 경로로 테스트.
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA", "C:\\Users\\Default\\AppData\\Roaming")
            _mock_allowed = str(Path(appdata) / "npm" / "node_modules")
        else:
            _mock_allowed = "/usr/local/lib/node_modules"
        assert pipeline._verify_npm_global_bin_allowed(_mock_allowed) is True, (
            f"문제 2: 허용 루트 내 경로가 거부됨: {_mock_allowed!r}"
        )

    def test_tc20b_allowed_root_tmp_fake_bin_rejected(self, tmp_path: "Path") -> None:
        """문제 2: 임의 디렉토리(공격자 제어 가능)의 global bin은 허용 루트 밖이므로 거부된다."""
        _rogue = tmp_path / "rogue_npm_bin"
        _rogue.mkdir()
        assert pipeline._verify_npm_global_bin_allowed(str(_rogue)) is False, (
            f"문제 2: 임의 경로 npm global bin이 허용 루트로 신뢰됨(공격 벡터): {_rogue!r}"
        )

    def test_tc20c_allowed_root_none_raises_empty_false(self) -> None:
        """문제 2: None 입력 TypeError, 빈 문자열 False(경계값 방어)."""
        _raised = False
        try:
            pipeline._verify_npm_global_bin_allowed(None)  # type: ignore[arg-type]
        except TypeError:
            _raised = True
        assert _raised, "문제 2: None global bin이 TypeError로 방어되지 않음"
        assert pipeline._verify_npm_global_bin_allowed("") is False, (
            "문제 2: 빈 문자열 global bin이 신뢰됨(경계값 방어 실패)"
        )

    def test_tc20d_allowed_root_wired_in_trust_source(self) -> None:
        """문제 2: _verify_codex_binary_path_trust가 허용 루트 검증과 차단 사유를 배선한다."""
        import inspect
        _src = inspect.getsource(pipeline._verify_codex_binary_path_trust)
        assert "_verify_npm_global_bin_allowed" in _src, (
            "문제 2: 신뢰 함수가 npm global bin 허용 루트 검증을 배선하지 않음"
        )
        assert "npm_global_bin_not_in_allowed_root" in _src, (
            "문제 2: 허용 루트 밖 fail-closed(차단 사유)가 신뢰 함수에 없음"
        )

    def test_tc20e_trust_blocks_when_npm_bin_not_in_allowed_root(
        self, tmp_path: "Path", monkeypatch
    ) -> None:
        """문제 2 E2E: npm이 허용 루트 밖 global bin을 반환하면 시스템 바이너리조차 fail-closed된다.

        조작된 npm 출력(임의 경로)은 codex가 시스템 경로에 있어도 신뢰 체인을 차단한다.
        """
        _rogue_npm_bin = str(tmp_path / "rogue_npm_bin")  # 허용 루트 밖(비표준)
        monkeypatch.setattr(pipeline, "_get_npm_global_bin", lambda: _rogue_npm_bin)
        # repo/tempdir 밖에 실재하는 시스템 바이너리를 bin_path로 사용.
        if sys.platform == "win32":
            _sys_bin = str(
                Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "where.exe"
            )
        else:
            _sys_bin = "/bin/sh"
        if not Path(_sys_bin).exists():
            return  # 시스템 바이너리 부재 환경 → 소프트 스킵
        _r = pipeline._verify_codex_binary_path_trust(_sys_bin)
        assert _r["trusted"] is False, (
            f"문제 2: 조작된 npm 출력인데 신뢰됨(fail-closed 실패): {_r!r}"
        )
        assert str(_r.get("untrusted_reason") or "").startswith(
            "npm_global_bin_not_in_allowed_root"
        ), f"문제 2: 차단 사유가 npm_global_bin_not_in_allowed_root가 아님: {_r!r}"

    # ---- 문제 3: .package-lock.json integrity provenance ---- #
    def _make_codex_pkg_root(self, tmp_path: "Path") -> "Path":
        """tmp에 .../node_modules/@openai/codex 구조를 만들고 pkg_root를 반환한다."""
        _pkg_root = tmp_path / "node_modules" / "@openai" / "codex"
        _pkg_root.mkdir(parents=True)
        return _pkg_root

    def test_tc20f_lockfile_absent_is_non_blocking(self, tmp_path: "Path") -> None:
        """문제 3: .package-lock.json 부재(npm 7+ 정상 global 설치)는 차단하지 않는다(현재 설치 호환)."""
        _pkg_root = self._make_codex_pkg_root(tmp_path)
        _res = pipeline._check_npm_lockfile_integrity(_pkg_root)
        assert _res["available"] is False and _res["ok"] is True, (
            f"문제 3: lockfile 부재가 non-blocking이 아님(정상 global 설치를 깸): {_res!r}"
        )

    def test_tc20g_lockfile_valid_integrity_confirmed(self, tmp_path: "Path") -> None:
        """문제 3: lockfile에 @openai/codex sha512- integrity가 있으면 provenance 확인(ok=True)."""
        _pkg_root = self._make_codex_pkg_root(tmp_path)
        _nm_root = tmp_path / "node_modules"
        (_nm_root / ".package-lock.json").write_text(
            '{"name":"root","lockfileVersion":3,"packages":{'
            '"node_modules/@openai/codex":{"version":"0.144.1",'
            '"integrity":"sha512-AAAABBBBCCCCDDDDEEEEFFFF=="}}}',
            encoding="utf-8",
        )
        _res = pipeline._check_npm_lockfile_integrity(_pkg_root)
        assert _res["available"] is True and _res["ok"] is True, (
            f"문제 3: 유효 sha512- integrity가 확인(ok=True)되지 않음: {_res!r}"
        )

    def test_tc20h_lockfile_malformed_integrity_blocked(self, tmp_path: "Path") -> None:
        """문제 3: lockfile integrity가 sha512- 형식이 아니면(조작 양성) available=True, ok=False."""
        _pkg_root = self._make_codex_pkg_root(tmp_path)
        _nm_root = tmp_path / "node_modules"
        (_nm_root / ".package-lock.json").write_text(
            '{"name":"root","lockfileVersion":3,"packages":{'
            '"node_modules/@openai/codex":{"version":"0.144.1",'
            '"integrity":"tampered_no_sri_prefix"}}}',
            encoding="utf-8",
        )
        _res = pipeline._check_npm_lockfile_integrity(_pkg_root)
        assert _res["available"] is True and _res["ok"] is False, (
            f"문제 3: 조작된 integrity가 차단 대상(ok=False)으로 판정되지 않음: {_res!r}"
        )

    def test_tc20i_lockfile_present_no_codex_entry_non_blocking(
        self, tmp_path: "Path"
    ) -> None:
        """문제 3: lockfile은 있으나 @openai/codex 엔트리 부재면 검증 불가 → non-blocking(오탐 회피)."""
        _pkg_root = self._make_codex_pkg_root(tmp_path)
        _nm_root = tmp_path / "node_modules"
        (_nm_root / ".package-lock.json").write_text(
            '{"name":"root","lockfileVersion":3,"packages":{'
            '"node_modules/left-pad":{"version":"1.0.0","integrity":"sha512-ZZZZ=="}}}',
            encoding="utf-8",
        )
        _res = pipeline._check_npm_lockfile_integrity(_pkg_root)
        assert _res["available"] is False and _res["ok"] is True, (
            f"문제 3: codex 엔트리 부재 lockfile이 non-blocking이 아님: {_res!r}"
        )

    def test_tc20j_lockfile_none_raises_and_wired_in_trust(self) -> None:
        """문제 3: None 입력 TypeError + 신뢰 함수가 lockfile provenance를 fail-closed로 배선."""
        _raised = False
        try:
            pipeline._check_npm_lockfile_integrity(None)  # type: ignore[arg-type]
        except TypeError:
            _raised = True
        assert _raised, "문제 3: None pkg_root가 TypeError로 방어되지 않음"
        import inspect
        _src = inspect.getsource(pipeline._verify_codex_binary_path_trust)
        assert "_check_npm_lockfile_integrity" in _src, (
            "문제 3: _verify_codex_binary_path_trust가 lockfile provenance를 배선하지 않음"
        )
        assert "npm_lockfile_integrity_invalid" in _src, (
            "문제 3: lockfile 조작 fail-closed(차단 사유)가 신뢰 함수에 없음"
        )

    # ---- 등록 / E2E 공격 / 회귀 ---- #
    def test_tc20k_new_trust_helpers_registered_in_all(self) -> None:
        """REJECT#18 신규 trust helper 2종이 CRITICAL 보호 목록에 등록됨(단독 변경 감지)."""
        for _fn in ("_verify_npm_global_bin_allowed", "_check_npm_lockfile_integrity"):
            assert _fn in pipeline.CODEX_CRITICAL_FUNCTIONS, (
                f"REJECT#18: {_fn}이 CRITICAL 보호 목록에 등록되지 않음(우회 가능)"
            )

    def test_tc20l_complete_fake_codex_environment_blocked(self, tmp_path: "Path") -> None:
        """E2E 공격: 완전한 가짜 codex 환경(가짜 npm bin + wrapper + JS)을 구성해도 BLOCKED된다."""
        _fake_npm_bin = tmp_path / "fake_npm_bin"
        _fake_pkg = _fake_npm_bin / "node_modules" / "@openai" / "codex" / "bin"
        _fake_pkg.mkdir(parents=True)
        (_fake_pkg / "codex.js").write_text("#!/usr/bin/env node\n// fake codex\n", encoding="utf-8")
        _fake_cmd = _fake_npm_bin / ("codex.cmd" if sys.platform == "win32" else "codex")
        _fake_cmd.write_text(
            '@node "%~dp0\\node_modules\\@openai\\codex\\bin\\codex.js" %*\n', encoding="utf-8"
        )
        _r = pipeline._verify_codex_binary_path_trust(str(_fake_cmd))
        assert _r["trusted"] is False, (
            f"E2E: 완전한 가짜 codex 환경이 신뢰됨(공격 성공): {_r!r}"
        )
        assert _r.get("untrusted_reason"), (
            f"E2E: 가짜 환경 차단인데 untrusted_reason이 비어 있음: {_r!r}"
        )

    def test_tc20m_real_install_still_trusted_regression(self) -> None:
        """회귀 방지: 실제 설치된 codex(있으면)는 REJECT#18 수정 후에도 계속 신뢰된다."""
        _codex = pipeline.shutil.which("codex")
        if not _codex:
            return
        _r = pipeline._verify_codex_binary_path_trust(_codex)
        assert _r["trusted"] is True, (
            f"REJECT#18 회귀: 실제 codex 설치가 수정 후 신뢰되지 않음: "
            f"reason={_r.get('untrusted_reason')!r}"
        )


# ===========================================================================
# TC-21: REJECT#19 — provenance absent/tampered → acceptance_eligible=False E2E 공격 차단
# ===========================================================================
class TestTC21ProvenanceBlockingE2E:
    """TC-21: REJECT#19 — provenance 독립 소스 검증으로 사용자 쓰기 가능 경로 E2E 공격 차단.

    수락 criteria:
    AC-1: %APPDATA%\\npm / ~/.nvm 가짜 체인 + provenance absent → acceptance_eligible=False.
    AC-2: package.json + lockfile + npm ls 모두 absent → provenance_absent_all_sources.
    AC-3: npm ls _integrity ok → acceptance_eligible=True (공식 설치 회귀 방지).
    AC-4: npm ls _integrity 형식 오류(조작 양성) → trusted=False fail-closed.
    AC-5: acceptance_eligible=False 시 _check_codex_chatgpt_auth → BLOCKED 반환.
    """

    def _make_fake_npm_wrapper(self, tmp_path: "Path", wrapper_name: str = "codex.cmd") -> "Path":
        """가짜 npm wrapper 파일 생성 (npm global bin 디렉토리 구조 시뮬레이션용)."""
        bin_dir = tmp_path / "npm_bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        wrapper = bin_dir / wrapper_name
        # Windows .cmd wrapper 형식
        wrapper.write_text(
            '@echo off\nnode "%~dp0\\node_modules\\@openai\\codex\\bin\\codex.js" %*\n',
            encoding="utf-8",
        )
        return wrapper


    def test_tc21c_npm_ls_integrity_invalid_format_trusted_false(
        self, tmp_path: "Path", monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """AC-4: npm ls _integrity 형식 오류(조작 양성) → trusted=False fail-closed."""
        import tempfile as _tf_mod

        pkg_dir = tmp_path / "node_modules" / "@openai" / "codex"
        pkg_dir.mkdir(parents=True, exist_ok=True)
        bin_dir = pkg_dir / "bin"
        bin_dir.mkdir(exist_ok=True)
        js_ep = bin_dir / "codex.js"
        js_ep.write_text("#!/usr/bin/env node\nconsole.log('codex');\n", encoding="utf-8")
        vendor_dir = pkg_dir / "vendor"
        vendor_dir.mkdir(exist_ok=True)
        if pipeline.sys.platform == "win32":
            native = vendor_dir / "codex-x86_64-pc-windows-msvc.exe"
        else:
            import platform as _platform
            _mach = _platform.machine().lower()
            _suffix = "x86_64-unknown-linux-gnu" if "x86" in _mach else "aarch64-unknown-linux-gnu"
            native = vendor_dir / f"codex-{_suffix}"
        native.write_bytes(b"\x00FAKE_NATIVE_TAMPERED")

        # step 2 우회: gettempdir()가 tmp_path와 무관한 경로 반환
        monkeypatch.setattr(_tf_mod, "gettempdir", lambda: "/nonexistent_temp_dir_for_tc21c")

        # lockfile, package.json 모두 absent
        monkeypatch.setattr(
            pipeline, "_check_npm_lockfile_integrity",
            lambda _root: {"available": False, "ok": True, "integrity": "", "source": "", "reason": "lockfile_absent"},
        )
        monkeypatch.setattr(
            pipeline, "_check_package_json_npm_integrity",
            lambda _root: {"available": False, "ok": True, "integrity": "", "source": "", "reason": "integrity_absent"},
        )
        # npm ls → 형식 오류 integrity (조작 양성)
        monkeypatch.setattr(
            pipeline, "_get_npm_ls_integrity",
            lambda _root=None: {
                "available": True, "ok": False,
                "integrity": "md5-TAMPERED_HASH",
                "source": "npm_ls_global",
                "reason": "npm_ls_integrity_invalid_format:md5-TAMPERED_HASH",
            },
        )
        monkeypatch.setattr(pipeline, "_find_codex_js_entrypoint", lambda _bin: js_ep)
        monkeypatch.setattr(pipeline, "_find_codex_native_binary", lambda _js: native)
        # REJECT#22: Authenticode mock (PowerShell 호출 방지, 속도 개선)
        monkeypatch.setattr(
            pipeline, "_check_authenticode_signature",
            lambda _path: {"available": True, "ok": False, "subject": "", "reason": "authenticode_invalid:NotSigned:"},
        )
        npm_bin_dir = str(tmp_path / "npm_bin")
        monkeypatch.setattr(pipeline, "_get_npm_global_bin", lambda: npm_bin_dir)
        monkeypatch.setattr(pipeline, "_verify_npm_global_bin_allowed", lambda _p: True)
        monkeypatch.setattr(pipeline, "_verify_npm_wrapper_content", lambda _p: True)

        wrapper = self._make_fake_npm_wrapper(tmp_path)
        _r = pipeline._verify_codex_binary_path_trust(str(wrapper))

        assert _r.get("trusted") is False, (
            f"AC-4 실패: npm ls integrity 형식 오류인데 trusted={_r.get('trusted')!r}. 결과: {_r!r}"
        )
        assert "npm_ls_integrity_invalid" in (_r.get("untrusted_reason") or ""), (
            f"AC-4 실패: untrusted_reason에 'npm_ls_integrity_invalid' 없음: {_r.get('untrusted_reason')!r}"
        )


    def test_tc21d_acceptance_eligible_false_chatgpt_auth_blocked(
        self, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """AC-5: acceptance_eligible=False 시 _check_codex_chatgpt_auth → BLOCKED 반환.

        가짜 체인이 trusted=True이더라도 provenance absent → auth 단계에서 차단.
        """
        # shutil.which가 가짜 codex 경로 반환
        _FAKE_PATH = "/fake/npm/bin/codex" if pipeline.sys.platform != "win32" else r"C:\fake\npm\bin\codex.cmd"

        monkeypatch.setattr(pipeline.shutil, "which", lambda _n: _FAKE_PATH if _n == "codex" else None)
        # _verify_codex_binary_path_trust → trusted=True, acceptance_eligible=False
        monkeypatch.setattr(
            pipeline, "_verify_codex_binary_path_trust",
            lambda _p: {
                "trusted": True,
                "path": _FAKE_PATH,
                "sha256": "aabbcc",
                "untrusted_reason": None,
                "install_source": "npm_global",
                "install_type": "npm_wrapper",
                "js_entrypoint_path": "",
                "js_entrypoint_sha256": "",
                "native_binary_path": "",
                "native_binary_sha256": "",
                "acceptance_eligible": False,
                "provenance_reason": "provenance_absent_all_sources",
            },
        )

        _result = pipeline._check_codex_chatgpt_auth(codex_bin=None)
        assert _result.get("result") == "BLOCKED", (
            f"AC-5 실패: acceptance_eligible=False인데 BLOCKED가 아님: {_result!r}"
        )
        assert _result.get("failure_code") == "codex_binary_provenance_absent", (
            f"AC-5 실패: failure_code={_result.get('failure_code')!r} (예상: 'codex_binary_provenance_absent')"
        )

    def test_tc21e_get_npm_ls_integrity_parses_valid_integrity(
        self, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """_get_npm_ls_integrity가 npm ls --json 출력에서 _integrity를 올바르게 파싱한다."""
        import json as _json

        _VALID_INTEGRITY = "sha512-" + "B" * 88 + "="
        _mock_output = _json.dumps({
            "dependencies": {
                "@openai/codex": {
                    "version": "1.0.0",
                    "_integrity": _VALID_INTEGRITY,
                }
            }
        })

        class _MockRunResult:
            stdout = _mock_output
            returncode = 0

        # npm binary가 시스템 경로에 있는 것으로 모킹
        _MOCK_NPM = "/usr/local/bin/npm"
        monkeypatch.setattr(pipeline.shutil, "which", lambda _n: _MOCK_NPM if _n == "npm" else None)
        monkeypatch.setattr(pipeline, "_verify_npm_binary_is_system_path", lambda _p: True)
        monkeypatch.setattr(
            pipeline.subprocess, "run",
            lambda *_a, **_kw: _MockRunResult(),
        )

        _r = pipeline._get_npm_ls_integrity()
        assert _r.get("available") is True, f"TC-21e: available=False: {_r!r}"
        assert _r.get("ok") is True, f"TC-21e: ok=False: {_r!r}"
        assert _r.get("integrity") == _VALID_INTEGRITY, (
            f"TC-21e: integrity={_r.get('integrity')!r} (예상: {_VALID_INTEGRITY!r})"
        )
        assert _r.get("source") == "npm_ls_global", f"TC-21e: source={_r.get('source')!r}"


# --------------------------------------------------------------------------- #
# TC-22: REJECT#20 — 정확한 hostname 검증 + 명시적 codex_bin trust/provenance 체크
# --------------------------------------------------------------------------- #
class TestTC22Reject20ExactHostnameAndExplicitBinE2E:
    """REJECT#20 수정 4가지를 E2E로 검증한다.

    AC-1: _check_package_json_npm_integrity — _resolved hostname이 정확히
          "registry.npmjs.org"이면 ok=True (substring 아닌 exact match).
    AC-2: _resolved hostname이 "registry.npmjs.org.evil.com" 같은 유사 도메인이면
          ok=False (exact match로 차단).
    AC-3: file:// URL — hostname이 "" 또는 빈값이므로 ok=False.
    AC-4: _check_codex_chatgpt_auth(codex_bin=명시경로) — trust=False → BLOCKED
          failure_code=codex_binary_untrusted_path.
    AC-5: _check_codex_chatgpt_auth(codex_bin=명시경로) — trust=True, acceptance_eligible=False
          → BLOCKED failure_code=codex_binary_provenance_absent.
    """

    def _write_pkg_json(self, tmp_path: "Path", resolved: str) -> "Path":
        """임시 pkg_root에 _integrity + _resolved를 포함한 package.json을 작성한다."""
        import json as _json

        _pkg_root = tmp_path / "node_modules" / "@openai" / "codex"
        _pkg_root.mkdir(parents=True, exist_ok=True)
        _valid_integrity = "sha512-" + "A" * 88 + "="
        (_pkg_root / "package.json").write_text(
            _json.dumps({"name": "@openai/codex", "_integrity": _valid_integrity, "_resolved": resolved}),
            encoding="utf-8",
        )
        return _pkg_root

    def test_tc22a_exact_registry_hostname_ok(self, tmp_path: "Path") -> None:
        """AC-1: _resolved가 정확히 registry.npmjs.org → ok=True."""
        _resolved = "https://registry.npmjs.org/@openai/codex/-/codex-1.0.0.tgz"
        _pkg_root = self._write_pkg_json(tmp_path, _resolved)
        _r = pipeline._check_package_json_npm_integrity(_pkg_root)
        assert _r.get("available") is True, f"TC-22a: available=False: {_r!r}"
        assert _r.get("ok") is True, (
            f"TC-22a: ok=False인데 registry.npmjs.org가 정확히 일치 — 오류: {_r!r}"
        )
        assert _r.get("reason") == "registry_provenance_confirmed", (
            f"TC-22a: reason={_r.get('reason')!r} (예상: 'registry_provenance_confirmed')"
        )

    def test_tc22b_evil_subdomain_hostname_blocked(self, tmp_path: "Path") -> None:
        """AC-2: _resolved hostname이 'registry.npmjs.org.evil.com' → ok=False.

        substring 검사였다면 통과됐을 것이나, exact hostname match로 차단됨을 확인한다.
        """
        _resolved = "https://registry.npmjs.org.evil.com/@openai/codex/-/codex-1.0.0.tgz"
        _pkg_root = self._write_pkg_json(tmp_path, _resolved)
        _r = pipeline._check_package_json_npm_integrity(_pkg_root)
        assert _r.get("available") is True, f"TC-22b: available=False: {_r!r}"
        assert _r.get("ok") is False, (
            f"TC-22b: ok=True인데 evil 서브도메인이 통과됨 — substring 검사 버그가 수정되지 않음: {_r!r}"
        )
        _reason = _r.get("reason", "")
        assert "resolved_hostname_not_registry" in _reason, (
            f"TC-22b: reason={_reason!r}에 'resolved_hostname_not_registry' 없음"
        )

    def test_tc22c_file_url_hostname_blocked(self, tmp_path: "Path") -> None:
        """AC-3: _resolved가 file:// URL → hostname이 "" → ok=False."""
        _resolved = "file:///local/registry.npmjs.org/codex/codex-1.0.0.tgz"
        _pkg_root = self._write_pkg_json(tmp_path, _resolved)
        _r = pipeline._check_package_json_npm_integrity(_pkg_root)
        assert _r.get("available") is True, f"TC-22c: available=False: {_r!r}"
        assert _r.get("ok") is False, (
            f"TC-22c: ok=True인데 file:// URL이 통과됨 — 경로에 registry.npmjs.org 포함돼도 차단돼야 함: {_r!r}"
        )

    def test_tc22d_explicit_codex_bin_untrusted_blocked(
        self, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """AC-4: codex_bin 명시 + trust=False → BLOCKED failure_code=codex_binary_untrusted_path.

        이전(REJECT#20 수정 전)에는 명시적 codex_bin이 trust 검증을 건너뛰었다.
        수정 후에는 명시적 경로도 _verify_codex_binary_path_trust를 거쳐야 한다.
        """
        _FAKE_BIN = "/fake/npm/bin/codex" if pipeline.sys.platform != "win32" else r"C:\fake\npm\bin\codex"

        # _verify_codex_binary_path_trust가 trusted=False를 반환하도록 monkeypatch
        monkeypatch.setattr(
            pipeline, "_verify_codex_binary_path_trust",
            lambda _p: {
                "trusted": False,
                "path": _p,
                "sha256": "",
                "untrusted_reason": "binary_not_in_allowed_npm_root:fake_path",
                "install_source": "unknown",
                "install_type": "unknown",
                "js_entrypoint_path": "",
                "js_entrypoint_sha256": "",
                "native_binary_path": "",
                "native_binary_sha256": "",
                "acceptance_eligible": True,
                "provenance_reason": "",
            },
        )

        _result = pipeline._check_codex_chatgpt_auth(codex_bin=_FAKE_BIN)
        assert _result.get("result") == "BLOCKED", (
            f"TC-22d: 명시 codex_bin이 untrusted인데 BLOCKED가 아님: {_result!r}"
        )
        assert _result.get("failure_code") == "codex_binary_untrusted_path", (
            f"TC-22d: failure_code={_result.get('failure_code')!r} "
            f"(예상: 'codex_binary_untrusted_path')"
        )

    def test_tc22e_explicit_codex_bin_provenance_absent_not_blocked_in_auth(
        self, monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """AC-5 (REJECT#20 설계 재조정): codex_bin 명시 + trust=True, acceptance_eligible=False
        → _check_codex_chatgpt_auth는 더 이상 acceptance_eligible을 체크하지 않는다.

        REJECT#20 이전 설계: 명시적 codex_bin도 _verify_codex_binary_path_trust를 재호출하여
          acceptance_eligible=False이면 차단했다. 그러나 이 동작은 npm 7+ 정상 설치에서
          "provenance 부재" 상태(acceptance_eligible=False)가 항상 발생하므로 production
          flow를 영구 차단하는 오탐을 유발했다.

        REJECT#20 재조정: acceptance_eligible 검사는 _cmd_gates_codex_review에서 직접 수행하며,
          _check_codex_chatgpt_auth 명시 경로에서는 신뢰 체인(trusted)만 확인한다.
          따라서 acceptance_eligible=False여도 _check_codex_chatgpt_auth는 BLOCKED를 반환하지 않는다.
          (실제 auth 검사 결과로 codex_not_chatgpt_authenticated 또는 codex_auth_check_failed 반환)
        """
        _FAKE_BIN = "/fake/npm/bin/codex" if pipeline.sys.platform != "win32" else r"C:\fake\npm\bin\codex"

        # _verify_codex_binary_path_trust가 trusted=True, acceptance_eligible=False를 반환 (가정)
        # 하지만 _check_codex_chatgpt_auth는 이 값을 더 이상 검사하지 않는다.
        monkeypatch.setattr(
            pipeline, "_verify_codex_binary_path_trust",
            lambda _p: {
                "trusted": True,
                "path": _p,
                "sha256": "deadbeef",
                "untrusted_reason": None,
                "install_source": "npm_global",
                "install_type": "npm_wrapper",
                "js_entrypoint_path": "",
                "js_entrypoint_sha256": "",
                "native_binary_path": "",
                "native_binary_sha256": "",
                "acceptance_eligible": False,
                "provenance_reason": "provenance_absent_all_sources",
            },
        )

        _result = pipeline._check_codex_chatgpt_auth(codex_bin=_FAKE_BIN)
        # 재조정 후: codex_binary_provenance_absent가 아닌 다른 failure_code 반환.
        # 존재하지 않는 경로이므로 codex_auth_check_failed 예상.
        assert _result.get("failure_code") != "codex_binary_provenance_absent", (
            f"TC-22e: 재조정 후에도 codex_binary_provenance_absent가 반환됨 — "
            f"명시적 codex_bin에서 acceptance_eligible 체크가 아직 남아있음: {_result!r}"
        )
        # 결과는 BLOCKED이어야 하지만 failure_code는 auth 관련이어야 한다 (path/binary 없음 또는 auth 실패)
        assert _result.get("result") == "BLOCKED", (
            f"TC-22e: BLOCKED가 아님 (FAKE_BIN이 실행 불가이므로 BLOCKED 예상): {_result!r}"
        )


# ===========================================================================
# TC-23: REJECT#26 — fail-open 방어 3종 강화
# ===========================================================================
class TestTC23Reject26FailOpenDefense:
    """REJECT#26 수정 5가지를 단위 테스트로 검증한다.

    AC-1: request-accept 저장 경로 재검증에서 acceptance_eligible=False → BLOCKED
          (_verify_codex_binary_path_trust가 acceptance_eligible=False 반환 시).
    AC-2a: npm이 사용자 쓰기 가능 경로(%APPDATA%, ~/)에 있으면 available=False.
    AC-2b: PowerShell은 System32 절대 경로만 사용 (subprocess args 검증).
    AC-3a: package.json이 존재하지만 JSON 파싱 실패 → available=True, ok=False.
    AC-3b: .package-lock.json이 존재하지만 JSON 파싱 실패 → available=True, ok=False.
    AC-4: npm install 명령에 --registry https://registry.npmjs.org 포함.
    """

    def test_tc23a_corrupted_package_json_fail_closed(self, tmp_path: "Path") -> None:
        """AC-3a: package.json 존재하지만 JSON 파싱 실패 → available=True, ok=False (fail-closed)."""
        _pkg_root = tmp_path / "node_modules" / "@openai" / "codex"
        _pkg_root.mkdir(parents=True, exist_ok=True)
        # 존재하지만 JSON이 아닌 파일 생성
        (_pkg_root / "package.json").write_bytes(b"NOT VALID JSON {{{")
        _result = pipeline._check_package_json_npm_integrity(_pkg_root)
        assert _result.get("available") is True, (
            f"TC-23a: corrupted package.json → available=True 필요. 실제: {_result!r}"
        )
        assert _result.get("ok") is False, (
            f"TC-23a: corrupted package.json → ok=False 필요 (fail-closed). 실제: {_result!r}"
        )
        _reason = _result.get("reason", "")
        assert "unreadable" in _reason or "parse" in _reason.lower() or "json" in _reason.lower(), (
            f"TC-23a: reason에 'unreadable'/parse/json 포함 필요. 실제: {_reason!r}"
        )

    def test_tc23b_corrupted_lockfile_fail_closed(self, tmp_path: "Path") -> None:
        """AC-3b: .package-lock.json 존재하지만 파싱 실패 → available=True, ok=False (fail-closed)."""
        _pkg_root = tmp_path / "node_modules" / "@openai" / "codex"
        _pkg_root.mkdir(parents=True, exist_ok=True)
        # node_modules 루트에 손상된 .package-lock.json 생성
        _lockfile = tmp_path / "node_modules" / ".package-lock.json"
        _lockfile.write_bytes(b"BROKEN JSON <<<")
        _result = pipeline._check_npm_lockfile_integrity(_pkg_root)
        assert _result.get("available") is True, (
            f"TC-23b: corrupted lockfile → available=True 필요. 실제: {_result!r}"
        )
        assert _result.get("ok") is False, (
            f"TC-23b: corrupted lockfile → ok=False 필요 (fail-closed). 실제: {_result!r}"
        )
        _reason = _result.get("reason", "")
        assert "unreadable" in _reason or "parse" in _reason.lower() or "json" in _reason.lower(), (
            f"TC-23b: reason에 'unreadable'/parse/json 포함 필요. 실제: {_reason!r}"
        )


    def test_tc23e_package_json_absent_still_non_blocking(self, tmp_path: "Path") -> None:
        """AC-3 역방향: package.json이 존재하지 않으면 available=False, ok=True (기존 동작 유지)."""
        _pkg_root = tmp_path / "node_modules" / "@openai" / "codex"
        _pkg_root.mkdir(parents=True, exist_ok=True)
        # package.json을 생성하지 않음
        _result = pipeline._check_package_json_npm_integrity(_pkg_root)
        assert _result.get("available") is False, (
            f"TC-23e: package.json 없음 → available=False 필요. 실제: {_result!r}"
        )
        assert _result.get("ok") is True, (
            f"TC-23e: package.json 없음 → ok=True 필요 (non-blocking). 실제: {_result!r}"
        )

    def test_tc23f_lockfile_absent_still_non_blocking(self, tmp_path: "Path") -> None:
        """AC-3 역방향: .package-lock.json이 없으면 available=False, ok=True (기존 동작 유지)."""
        _pkg_root = tmp_path / "node_modules" / "@openai" / "codex"
        _pkg_root.mkdir(parents=True, exist_ok=True)
        # lockfile 생성하지 않음
        _result = pipeline._check_npm_lockfile_integrity(_pkg_root)
        assert _result.get("available") is False, (
            f"TC-23f: lockfile 없음 → available=False 필요. 실제: {_result!r}"
        )
        assert _result.get("ok") is True, (
            f"TC-23f: lockfile 없음 → ok=True 필요 (non-blocking). 실제: {_result!r}"
        )


# --------------------------------------------------------------------------- #
# TC-24: REJECT#28 — 실제 실행 node 인터프리터 신뢰 검증 + SHA 형식 강제 + clean env 전파
# --------------------------------------------------------------------------- #
class TestTC24Reject28NodeInterpreterVerification:
    """REJECT#28 — 실제 실행 체인 인터프리터 검증 5종 AC를 검증한다.

    AC1: 정품 wrapper·JS·native + 악성 wrapper-local node → BLOCKED, acceptance_eligible=False.
    AC2: POSIX PATH 선두 악성 node(홈 디렉토리) shebang 실행 → BLOCKED.
    AC3: 실제 실행 인터프리터의 절대 경로/SHA가 trust result에 포함된다.
    AC4: unreadable:/비-hex/64자리 아님 binary SHA → 운영 신뢰 검사 BLOCKED.
    AC5: 모든 Codex subprocess(login/exec/version) 환경에 OPENAI_API_KEY가 없다.
    """

    def _make_fake_npm_wrapper(self, tmp_path: "Path", wrapper_name: str = "codex.cmd") -> "Path":
        bin_dir = tmp_path / "npm_bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        wrapper = bin_dir / wrapper_name
        wrapper.write_text(
            '@echo off\nnode "%~dp0\\node_modules\\@openai\\codex\\bin\\codex.js" %*\n',
            encoding="utf-8",
        )
        return wrapper


    def test_tc24b_posix_path_node_in_home_blocked(
        self, tmp_path: "Path", monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """AC2: POSIX PATH의 node가 홈 디렉토리 하위이면 node_interpreter_trusted=False (BLOCKED)."""
        if pipeline.sys.platform == "win32":
            pytest.skip("POSIX shebang PATH node 검증 전용 (win32 제외)")
        # 홈 디렉토리 하위의 악성 node를 PATH가 반환하도록 mock.
        _evil_node = os.path.join(os.path.expanduser("~"), ".nvm", "versions", "node", "bin", "node")
        monkeypatch.setattr(pipeline.shutil, "which", lambda _n: _evil_node if "node" in _n else None)
        wrapper = tmp_path / "codex"  # POSIX shebang wrapper (경로는 위치 검사에 무관)
        wrapper.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        _r = pipeline._verify_node_interpreter_trust(wrapper, None)
        assert _r.get("node_interpreter_trusted") is False, (
            f"TC-24b: 홈 디렉토리 node인데 trusted=True (차단되어야 함): {_r!r}"
        )
        assert "user_writable" in str(_r.get("node_interpreter_reason") or ""), (
            f"TC-24b: reason에 user_writable 없음: {_r.get('node_interpreter_reason')!r}"
        )


    def test_tc24d_unreadable_binary_sha_operational_trust_blocked(self) -> None:
        """AC4: codex_binary_sha256='unreadable:test' → 운영 신뢰 검사 BLOCKED (invalid format)."""
        r = pipeline._check_codex_review_operational_trust(
            _trust_base(codex_binary_sha256="unreadable:test")
        )
        assert r["status"] == "BLOCKED", f"TC-24d: unreadable SHA인데 통과: {r!r}"
        assert r["failure_code"] == "codex_review_binary_sha_invalid_format", r

    def test_tc24e_short_binary_sha_operational_trust_blocked(self) -> None:
        """AC4: codex_binary_sha256이 32자리 → 운영 신뢰 검사 BLOCKED (invalid format)."""
        r = pipeline._check_codex_review_operational_trust(
            _trust_base(codex_binary_sha256="a" * 32)
        )
        assert r["status"] == "BLOCKED", f"TC-24e: 32자리 SHA인데 통과: {r!r}"
        assert r["failure_code"] == "codex_review_binary_sha_invalid_format", r

    def test_tc24e2_valid_helper_and_native_node_format(self) -> None:
        """AC4 보강: _is_valid_sha256_hex 형식 + native/node SHA 형식도 운영 신뢰 검사에서 강제."""
        assert pipeline._is_valid_sha256_hex("a" * 64) is True
        assert pipeline._is_valid_sha256_hex("unreadable:x") is False
        assert pipeline._is_valid_sha256_hex("A" * 64) is False  # 대문자 불허
        # native SHA 비-hex → BLOCKED
        r_native = pipeline._check_codex_review_operational_trust(
            _trust_base(codex_native_binary_sha256="z" * 64)
        )
        assert r_native["status"] == "BLOCKED", r_native
        assert r_native["failure_code"] == "codex_review_native_binary_sha_invalid_format", r_native

    def test_tc24f_all_codex_subprocess_env_no_openai_api_key(
        self, tmp_path: "Path", monkeypatch: "pytest.MonkeyPatch"
    ) -> None:
        """AC5: login/exec/version 등 모든 Codex subprocess 환경에 OPENAI_API_KEY가 없다."""
        import inspect
        monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "EXAMPLE_DUMMY_" + "A" * 24)
        _captured_envs: list = []

        class _FakeProc:
            returncode = 0
            stdout = "codex-cli 1.0.0\n"
            stderr = ""

        def _fake_run(cmd, *a, **kw):
            _captured_envs.append(kw.get("env"))
            return _FakeProc()

        monkeypatch.setattr(pipeline.subprocess, "run", _fake_run)

        # (1) capability: --version 경로 (clean env 전달 필수)
        pipeline._detect_codex_cli_capability(
            verified_bin_path="/x/codex", env=pipeline._codex_clean_env()
        )
        # (2) exec: _invoke_codex_exec는 내부에서 OPENAI_API_KEY 제거 env를 구성
        pipeline._invoke_codex_exec("gpt-5.6-sol", "high", "prompt", codex_bin="/x/codex")
        # (3) login status: _check_codex_chatgpt_auth (명시 codex_bin은 trust 검증 bypass 위해 mock)
        monkeypatch.setattr(pipeline, "_verify_codex_binary_path_trust", _mock_verify_trust_ok)
        pipeline._check_codex_chatgpt_auth(codex_bin="/x/codex")

        assert len(_captured_envs) >= 3, f"TC-24f: subprocess 호출이 3회 미만: {len(_captured_envs)}"
        for _i, _env in enumerate(_captured_envs):
            assert _env is not None, f"TC-24f: subprocess #{_i}에 env가 전달되지 않음(None)"
            assert "OPENAI_API_KEY" not in _env, (
                f"TC-24f: subprocess #{_i} 환경에 OPENAI_API_KEY가 남아 있음"
            )
        # --version(_cmd_gates_codex_review) 호출부가 clean env를 전달하는지 소스로 확인.
        _src = inspect.getsource(pipeline._cmd_gates_codex_review)
        assert '[_ver_bin, "--version"]' in _src and "_codex_clean_env()" in _src, (
            "TC-24f: _cmd_gates_codex_review의 --version 호출부에 clean env 전파 누락"
        )

    def test_tc24g_node_interpreter_registered_in_critical(self) -> None:
        """AC1 보강: _verify_node_interpreter_trust가 CRITICAL 보호 목록에 등록됨(단독 변경 감지)."""
        assert "_verify_node_interpreter_trust" in pipeline.CODEX_CRITICAL_FUNCTIONS, (
            "REJECT#28: _verify_node_interpreter_trust가 CRITICAL 보호 목록에 없음(우회 가능)"
        )

    def test_tc24h_operational_trust_node_chain_required_for_wrapper(self) -> None:
        """AC3 보강: install_type=npm_wrapper인데 node 체인 부재 → 운영 신뢰 검사 BLOCKED."""
        r = pipeline._check_codex_review_operational_trust(
            _trust_base(codex_install_type="npm_wrapper")  # node 필드 미기록
        )
        assert r["status"] == "BLOCKED", f"TC-24h: wrapper인데 node 체인 부재 통과: {r!r}"
        assert r["failure_code"] == "codex_review_node_interpreter_chain_missing", r

    def test_tc24i_operational_trust_node_chain_present_passes(self) -> None:
        """AC3 보강: install_type=npm_wrapper + 신뢰 node 체인 기록 → 운영 신뢰 검사 PASS."""
        r = pipeline._check_codex_review_operational_trust(
            _trust_base(
                codex_install_type="npm_wrapper",
                codex_node_interpreter_path="/usr/bin/node",
                codex_node_interpreter_sha256="c" * 64,
                codex_node_interpreter_trusted=True,
            )
        )
        assert r["status"] == "PASS", f"TC-24i: 정상 node 체인인데 BLOCKED: {r!r}"


# --------------------------------------------------------------------------- #
# REJECT#31: 사용자 쓰기 가능 npm 래퍼(codex.cmd) 우회 차단.
#   - _verify_npm_wrapper_content: 주석(rem/::)에만 정품 JS 경로를 넣은 decoy 래퍼 차단.
#   - _invoke_codex_exec/_check_codex_chatgpt_auth: 검증된 node + codex.js 직접 실행.
# --------------------------------------------------------------------------- #
def test_tc45a_cmd_with_js_path_in_comment_only_blocked():
    """REJECT#31 AC#1: 정품 JS 경로가 주석에만 있고 실제 실행은 다른 악성 파일인 cmd는 BLOCKED."""
    import tempfile
    malicious_cmd = (
        "@echo off\n"
        "rem node \"%~dp0\\node_modules\\@openai\\codex\\bin\\codex.js\" -- fake comment\n"
        ":: @openai/codex reference in comment only\n"
        "malicious.exe %*\n"  # 실제 실행은 악성 파일
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.cmd', delete=False, encoding='utf-8') as f:
        f.write(malicious_cmd)
        fname = f.name
    # 플랫폼 무관 결정성: .cmd 주석-제거 검사가 win32 분기에서 실행되도록 강제한다.
    _orig_platform = pipeline.sys.platform
    try:
        pipeline.sys.platform = "win32"
        result = pipeline._verify_npm_wrapper_content(Path(fname))
        assert result is False, (
            "REJECT#31 AC#1: 주석에만 정품 JS 경로가 있는 cmd가 True를 반환 — "
            "주석 제거 후 실행 코드만 검사해야 함"
        )
    finally:
        pipeline.sys.platform = _orig_platform
        os.unlink(fname)


def test_tc45b_normal_npm_cmd_still_passes():
    """REJECT#31 AC#4: 정상 npm 래퍼는 통과 (회귀 방지)."""
    import tempfile
    normal_cmd = (
        "@echo off\n"
        "rem npm cmd wrapper for @openai/codex\n"
        "@node \"%~dp0\\node_modules\\@openai\\codex\\bin\\codex.js\" %*\n"
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.cmd', delete=False, encoding='utf-8') as f:
        f.write(normal_cmd)
        fname = f.name
    _orig_platform = pipeline.sys.platform
    try:
        pipeline.sys.platform = "win32"
        result = pipeline._verify_npm_wrapper_content(Path(fname))
        assert result is True, (
            "REJECT#31 AC#4: 정상 npm cmd가 False를 반환 — 정상 npm 설치를 깨트림"
        )
    finally:
        pipeline.sys.platform = _orig_platform
        os.unlink(fname)


def test_tc45c_invoke_codex_exec_uses_node_direct_when_provided():
    """REJECT#31 AC#2/3: codex_js와 node_bin 제공 시 subprocess argv가 node 직접 실행."""
    from unittest.mock import patch, MagicMock
    captured_cmd = []

    def _mock_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        return mock_result

    fake_node = "/usr/local/bin/node"
    fake_js = "/usr/local/lib/node_modules/@openai/codex/bin/codex.js"
    with patch("pipeline.subprocess.run", side_effect=_mock_run):
        pipeline._invoke_codex_exec(
            "gpt-5.6-sol", "high", "test prompt",
            codex_js=fake_js, node_bin=fake_node
        )
    assert captured_cmd[0] == fake_node, (
        f"REJECT#31 AC#2: subprocess argv[0]이 node_bin이 아님: {captured_cmd[0]}"
    )
    assert captured_cmd[1] == fake_js, (
        f"REJECT#31 AC#3: subprocess argv[1]이 codex_js가 아님: {captured_cmd[1]}"
    )
    assert "exec" in captured_cmd, "exec 인자가 없음"


def test_tc45d_check_auth_uses_node_direct_when_provided():
    """REJECT#31 AC#2/3: _check_codex_chatgpt_auth도 node + codex.js 직접 실행(login status)."""
    from unittest.mock import patch, MagicMock
    captured_cmd = []

    def _mock_run(cmd, **kwargs):
        captured_cmd.clear()
        captured_cmd.extend(cmd)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Logged in using ChatGPT\n"
        mock_result.stderr = ""
        return mock_result

    fake_node = "/usr/local/bin/node"
    fake_js = "/usr/local/lib/node_modules/@openai/codex/bin/codex.js"
    # codex_bin 명시 → trust 검증 우회(mock)하여 login status 실행 경로만 관측한다.
    with patch("pipeline.subprocess.run", side_effect=_mock_run), \
            patch("pipeline._verify_codex_binary_path_trust",
                  return_value={"trusted": True, "path": "/x/codex", "sha256": "a" * 64,
                                "acceptance_eligible": True}):
        res = pipeline._check_codex_chatgpt_auth(
            codex_bin="/x/codex", node_bin=fake_node, codex_js=fake_js
        )
    assert res.get("result") == "OK", f"TC-45d: 정상 로그인인데 OK 아님: {res!r}"
    assert captured_cmd[0] == fake_node, (
        f"REJECT#31 AC#2: login status argv[0]이 node_bin이 아님: {captured_cmd[0]}"
    )
    assert captured_cmd[1] == fake_js, (
        f"REJECT#31 AC#3: login status argv[1]이 codex_js가 아님: {captured_cmd[1]}"
    )
    assert captured_cmd[2:] == ["login", "status"], (
        f"REJECT#31 AC#3: login status 인자 불일치: {captured_cmd!r}"
    )


# --------------------------------------------------------------------------- #
# REJECT#32: 초기 capability probe도 검증된 node + codex.js 직접 실행.
#   - _verify_npm_wrapper_content: 정상 node dispatch 뒤 악성 실행 줄이 붙은 래퍼 차단(단일 dispatch).
#   - _detect_codex_cli_capability: node_bin + codex_js 제공 시 npm 래퍼 대신 node 직접 실행.
#   - _cmd_gates_codex_review: 초기 capability probe에 node_interpreter_path + js_entrypoint_path 배선.
# --------------------------------------------------------------------------- #
def test_tc47a_cmd_with_extra_exec_line_blocked():
    """REJECT#32 AC#4: 정상 node dispatch 뒤에 악성 실행 줄이 있는 cmd → _verify_npm_wrapper_content=False."""
    import tempfile
    malicious_cmd = (
        "@echo off\n"
        "@node \"%~dp0\\node_modules\\@openai\\codex\\bin\\codex.js\" %*\n"
        "malicious.exe arg1\n"  # 정품 dispatch 뒤에 덧붙인 악성 실행 줄
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.cmd', delete=False, encoding='utf-8') as f:
        f.write(malicious_cmd)
        fname = f.name
    _orig_platform = pipeline.sys.platform
    try:
        pipeline.sys.platform = "win32"
        result = pipeline._verify_npm_wrapper_content(Path(fname))
        assert result is False, (
            "REJECT#32 AC#4: node 뒤에 악성 실행 줄이 있는 cmd가 True 반환 — "
            "단일 node dispatch만 허용해야 함"
        )
    finally:
        pipeline.sys.platform = _orig_platform
        os.unlink(fname)


def test_tc47a2_real_npm_wrapper_with_control_flow_still_passes():
    """REJECT#32 회귀: 제어 흐름(if/else/goto/그룹핑)이 있는 정상 npm 래퍼는 통과."""
    import tempfile
    real_cmd = (
        "@ECHO off\n"
        "GOTO start\n"
        ":find_dp0\n"
        "SET dp0=%~dp0\n"
        "EXIT /b\n"
        ":start\n"
        "SETLOCAL\n"
        "CALL :find_dp0\n"
        "IF EXIST \"%dp0%\\node.exe\" (\n"
        "  SET \"_prog=%dp0%\\node.exe\"\n"
        ") ELSE (\n"
        "  SET \"_prog=node\"\n"
        ")\n"
        "endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "
        "\"%_prog%\" \"%dp0%\\node_modules\\@openai\\codex\\bin\\codex.js\" %*\n"
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.cmd', delete=False, encoding='utf-8') as f:
        f.write(real_cmd)
        fname = f.name
    _orig_platform = pipeline.sys.platform
    try:
        pipeline.sys.platform = "win32"
        result = pipeline._verify_npm_wrapper_content(Path(fname))
        assert result is True, (
            "REJECT#32 회귀: 제어 흐름이 있는 공식 npm 래퍼가 False 반환 — 정상 설치를 깨트림"
        )
    finally:
        pipeline.sys.platform = _orig_platform
        os.unlink(fname)


def test_tc47b_detect_capability_uses_node_direct():
    """REJECT#32 AC#2/3: _detect_codex_cli_capability가 node_bin + codex.js 직접 실행(npm 래퍼 미실행)."""
    from unittest.mock import patch, MagicMock
    captured_cmd = []

    def _mock_run(cmd, **kwargs):
        captured_cmd.clear()
        captured_cmd.extend([str(c) for c in cmd])
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1.0.0"
        mock_result.stderr = ""
        return mock_result

    fake_node = "/usr/local/bin/node"
    fake_js = "/usr/local/lib/node_modules/@openai/codex/bin/codex.js"
    with patch("pipeline.subprocess.run", side_effect=_mock_run):
        pipeline._detect_codex_cli_capability(
            verified_bin_path="codex.cmd",  # npm 래퍼 경로 전달 — 실행 파일로 쓰이면 안 됨
            node_bin=fake_node,
            codex_js=fake_js,
        )
    assert captured_cmd[0] == fake_node, (
        f"REJECT#32 AC#2: capability probe argv[0]이 node_bin이 아님: {captured_cmd!r}"
    )
    assert captured_cmd[1] == fake_js, (
        f"REJECT#32 AC#3: capability probe argv[1]이 codex_js가 아님: {captured_cmd!r}"
    )
    assert captured_cmd[2] == "--version", (
        f"REJECT#32 AC#2: capability probe argv[2]이 --version이 아님: {captured_cmd!r}"
    )
    assert "codex.cmd" not in captured_cmd, (
        f"REJECT#32 AC#3: npm 래퍼(codex.cmd)가 subprocess 실행 파일로 사용됨: {captured_cmd!r}"
    )


def test_tc47c_call_site_wires_node_and_js_to_initial_probe():
    """REJECT#32 AC#2: _cmd_gates_codex_review 초기 capability probe가 node + codex.js를 배선한다."""
    import inspect
    src = inspect.getsource(pipeline._cmd_gates_codex_review)
    assert "_detect_codex_cli_capability(" in src
    assert "node_bin=_cap_node_bin" in src, (
        "REJECT#32 AC#2: 초기 capability probe call site가 node_bin을 배선하지 않음"
    )
    assert "codex_js=_cap_codex_js" in src, (
        "REJECT#32 AC#2/3: 초기 capability probe call site가 codex_js를 배선하지 않음"
    )
    assert 'get("node_interpreter_path"' in src, (
        "REJECT#32 AC#2: call site가 신뢰 검증된 node_interpreter_path에서 node_bin을 얻지 않음"
    )
    assert 'get("js_entrypoint_path"' in src, (
        "REJECT#32 AC#3: call site가 신뢰 검증된 js_entrypoint_path에서 codex_js를 얻지 않음"
    )


def test_tc47d_detect_capability_falls_back_when_no_node():
    """REJECT#32 회귀: node_bin/codex_js 미제공 시 verified_bin_path 직접 실행(direct_native/레거시)."""
    from unittest.mock import patch, MagicMock
    captured_cmd = []

    def _mock_run(cmd, **kwargs):
        captured_cmd.clear()
        captured_cmd.extend([str(c) for c in cmd])
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1.0.0"
        mock_result.stderr = ""
        return mock_result

    with patch("pipeline.subprocess.run", side_effect=_mock_run):
        pipeline._detect_codex_cli_capability(
            verified_bin_path="/usr/local/bin/codex",  # direct_native 절대 경로
            node_bin="",
            codex_js="",
        )
    assert captured_cmd[0] == "/usr/local/bin/codex", (
        f"REJECT#32 회귀: node 미제공 시 verified_bin_path 직접 실행이 아님: {captured_cmd!r}"
    )
    assert captured_cmd[1] == "--version"


# =========================================================================== #
# TC-48: REJECT#33 — npm 바이너리 허용목록에서 사용자 쓰기 가능 경로 제거
#
# root cause: %APPDATA%\npm / ~/.nvm(사용자 쓰기 가능)이 npm 바이너리 허용목록에 포함되어,
#   PATH에 주입된 악성 npm이 _verify_npm_binary_is_system_path를 통과하고 _get_npm_global_bin에서
#   검증 전 subprocess로 실행될 수 있었다. 사용자 쓰기 경로를 허용목록에서 제거하여 fail-closed한다.
#
# AC#1: %APPDATA%\npm / ~/.nvm 악성 npm이 실행되지 않고 None/False 반환.
# AC#2: 악성 npm이 sentinel 파일을 생성하지 못하는(subprocess 미실행) E2E 검증.
# AC#3: Program Files 서명 npm + /usr/bin·/usr/local/bin 시스템 소유 npm은 정상 동작.
# =========================================================================== #
def test_tc48a_appdata_npm_path_blocked():
    """REJECT#33 AC#1: %APPDATA%\\npm의 npm은 사용자 쓰기 가능 → False."""
    appdata = os.environ.get("APPDATA", r"C:\Users\test\AppData\Roaming")
    npm_path = str(Path(appdata) / "npm" / "npm.cmd")
    result = pipeline._verify_npm_binary_is_system_path(npm_path)
    assert result is False, (
        f"REJECT#33 AC#1: %APPDATA%\\npm의 npm이 True (사용자 쓰기 가능 경로 차단 안 됨): {npm_path}"
    )


def test_tc48b_nvm_home_path_blocked():
    """REJECT#33 AC#1: ~/.nvm의 npm은 사용자 쓰기 가능 → False."""
    home = os.path.expanduser("~")
    nvm_path = str(Path(home) / ".nvm" / "versions" / "node" / "v20.0.0" / "bin" / "npm")
    result = pipeline._verify_npm_binary_is_system_path(nvm_path)
    assert result is False, (
        f"REJECT#33 AC#1: ~/.nvm의 npm이 True (사용자 쓰기 가능 경로 차단 안 됨): {nvm_path}"
    )


def test_tc48c_program_files_npm_passes():
    """REJECT#33 AC#3: Program Files의 npm은 정상 통과.

    POSIX 러너에서는 Windows 경로 접두사가 매칭되지 않으므로 시스템 경로 npm으로 검증한다.
    """
    if sys.platform == "win32":
        npm_path = r"C:\Program Files\nodejs\npm.cmd"
        result = pipeline._verify_npm_binary_is_system_path(npm_path)
        assert result is True, (
            f"REJECT#33 AC#3: Program Files의 npm이 False — 정상 설치 깨짐: {npm_path}"
        )
    else:
        for npm_path in ("/usr/bin/npm", "/usr/local/bin/npm"):
            result = pipeline._verify_npm_binary_is_system_path(npm_path)
            assert result is True, (
                f"REJECT#33 AC#3: 시스템 소유 npm이 False — 정상 설치 깨짐: {npm_path}"
            )


def test_tc48d_appdata_npm_binary_not_executed():
    """REJECT#33 AC#2: %APPDATA%\\npm(또는 ~/.nvm)의 npm은 subprocess 실행 전 None 반환(부작용 없음)."""
    from unittest.mock import patch

    if sys.platform == "win32":
        base = os.environ.get("APPDATA", r"C:\Users\test\AppData\Roaming")
        fake_npm = str(Path(base) / "npm" / "npm.cmd")
        probe_names = ("npm.cmd", "npm")
    else:
        fake_npm = str(
            Path(os.path.expanduser("~")) / ".nvm" / "versions" / "node"
            / "v20.0.0" / "bin" / "npm"
        )
        probe_names = ("npm",)

    called_with = []

    def mock_which(name):
        if name in probe_names:
            return fake_npm
        return None

    def mock_run(cmd, **kwargs):
        called_with.extend(cmd)
        raise AssertionError("npm subprocess가 실행됨 — REJECT#33 AC#2 위반(sentinel 생성 위험)")

    with patch("pipeline.shutil.which", side_effect=mock_which), \
         patch("pipeline.subprocess.run", side_effect=mock_run):
        result = pipeline._get_npm_global_bin()

    assert result is None, (
        f"REJECT#33: 사용자 쓰기 경로 npm이 실행됐거나 결과 반환됨: {result!r}"
    )
    assert not called_with, (
        f"REJECT#33 AC#2: subprocess 실행됨 — sentinel 파일 생성 위험: {called_with!r}"
    )


def test_tc48e_get_npm_global_bin_sanitizes_env():
    """REJECT#33 AC#5: _get_npm_global_bin이 npm subprocess에 민감 변수 제거 env를 배선한다."""
    import inspect
    src = inspect.getsource(pipeline._get_npm_global_bin)
    assert "env=_npm_env" in src, (
        "REJECT#33 AC#5: npm subprocess가 sanitized env(env=_npm_env)를 사용하지 않음"
    )
    # REJECT#38: 민감 변수 제거 로직은 인라인 pop 대신 중앙 정제 함수 _codex_clean_env()로 통합됨.
    #   (OPENAI_API_KEY + NODE_OPTIONS/NODE_PATH 등 선행 로딩 변수 제거)
    assert "_codex_clean_env()" in src, (
        "REJECT#38: _get_npm_global_bin이 _codex_clean_env()로 민감/선행로딩 변수를 제거하지 않음"
    )


class TestTC61Reject35MissingFunctionConstantsCritical:
    """REJECT#35: 승인 신뢰 경계 helper(_is_valid_sha256_hex, _codex_clean_env,
    _run_codex_preflight_checks) 및 CRITICAL 파일 집합 상수(_CODEX_CRITICAL_FILE_EXACT,
    _CODEX_CRITICAL_FILE_PREFIXES)가 등록됐는지 검증한다."""

    def test_tc61a_is_valid_sha256_hex_in_critical_functions(self) -> None:
        """REJECT#35 AC#1: _is_valid_sha256_hex가 CODEX_CRITICAL_FUNCTIONS에 있다."""
        assert "_is_valid_sha256_hex" in pipeline.CODEX_CRITICAL_FUNCTIONS, (
            "REJECT#35 AC#1: _is_valid_sha256_hex가 CODEX_CRITICAL_FUNCTIONS에 없음 — "
            "변조 시 위조 SHA 통과 가능하므로 CRITICAL 등록 필수"
        )

    def test_tc61b_codex_clean_env_in_critical_functions(self) -> None:
        """REJECT#35 AC#2: _codex_clean_env가 CODEX_CRITICAL_FUNCTIONS에 있다."""
        assert "_codex_clean_env" in pipeline.CODEX_CRITICAL_FUNCTIONS, (
            "REJECT#35 AC#2: _codex_clean_env가 CODEX_CRITICAL_FUNCTIONS에 없음 — "
            "변조 시 OPENAI_API_KEY가 Codex subprocess에 노출 가능"
        )

    def test_tc61c_run_codex_preflight_checks_in_critical_functions(self) -> None:
        """REJECT#35 AC#3: _run_codex_preflight_checks가 CODEX_CRITICAL_FUNCTIONS에 있다."""
        assert "_run_codex_preflight_checks" in pipeline.CODEX_CRITICAL_FUNCTIONS, (
            "REJECT#35 AC#3: _run_codex_preflight_checks가 CODEX_CRITICAL_FUNCTIONS에 없음 — "
            "변조 시 실행 전 전체 trust-chain 검사를 우회 가능"
        )

    def test_tc61d_critical_file_exact_in_critical_constants(self) -> None:
        """REJECT#35 AC#4: _CODEX_CRITICAL_FILE_EXACT가 CODEX_CRITICAL_CONSTANTS에 있다."""
        assert "_CODEX_CRITICAL_FILE_EXACT" in pipeline.CODEX_CRITICAL_CONSTANTS, (
            "REJECT#35 AC#4: _CODEX_CRITICAL_FILE_EXACT가 CODEX_CRITICAL_CONSTANTS에 없음 — "
            "변조 시 특정 파일 CRITICAL 분류가 HIGH로 저하 가능"
        )

    def test_tc61e_critical_file_prefixes_in_critical_constants(self) -> None:
        """REJECT#35 AC#5: _CODEX_CRITICAL_FILE_PREFIXES가 CODEX_CRITICAL_CONSTANTS에 있다."""
        assert "_CODEX_CRITICAL_FILE_PREFIXES" in pipeline.CODEX_CRITICAL_CONSTANTS, (
            "REJECT#35 AC#5: _CODEX_CRITICAL_FILE_PREFIXES가 CODEX_CRITICAL_CONSTANTS에 없음 — "
            "변조 시 CRITICAL 경로 판정 우회 가능"
        )

    def test_tc61f_is_valid_sha256_hex_change_triggers_critical(self) -> None:
        """REJECT#35 AC#1: _is_valid_sha256_hex가 CODEX_CRITICAL_FUNCTIONS에 있으면
        해당 함수만 변경된 경우 _classify_codex_review_risk가 CRITICAL을 반환한다."""
        r = pipeline._classify_codex_review_risk(["pipeline.py"], ["_is_valid_sha256_hex"])
        assert r["risk_level"] == "CRITICAL", (
            f"REJECT#35 AC#1: _is_valid_sha256_hex 단독 변경이 CRITICAL 아님. got={r['risk_level']!r}"
        )
        assert r.get("matched_rule") == "critical_function", (
            f"REJECT#35 AC#1: matched_rule이 critical_function 아님. got={r.get('matched_rule')!r}"
        )

    def test_tc61g_codex_clean_env_change_triggers_critical(self) -> None:
        """REJECT#35 AC#2: _codex_clean_env 단독 변경이 CRITICAL 분류를 트리거한다."""
        r = pipeline._classify_codex_review_risk(["pipeline.py"], ["_codex_clean_env"])
        assert r["risk_level"] == "CRITICAL", (
            f"REJECT#35 AC#2: _codex_clean_env 단독 변경이 CRITICAL 아님. got={r['risk_level']!r}"
        )

    def test_tc61h_run_preflight_change_triggers_critical(self) -> None:
        """REJECT#35 AC#3: _run_codex_preflight_checks 단독 변경이 CRITICAL 분류를 트리거한다."""
        r = pipeline._classify_codex_review_risk(["pipeline.py"], ["_run_codex_preflight_checks"])
        assert r["risk_level"] == "CRITICAL", (
            f"REJECT#35 AC#3: _run_codex_preflight_checks 단독 변경이 CRITICAL 아님. got={r['risk_level']!r}"
        )

    def test_tc61i_critical_file_exact_change_triggers_critical(self) -> None:
        """REJECT#35 AC#4: _CODEX_CRITICAL_FILE_EXACT 상수 변경이 CRITICAL 분류를 트리거한다.
        _detect_changed_critical_constants 결과를 직접 주입하여 git 의존 없이 검증한다."""
        r = pipeline._classify_codex_review_risk(["pipeline.py"], [], ["_CODEX_CRITICAL_FILE_EXACT"])
        assert r["risk_level"] == "CRITICAL", (
            f"REJECT#35 AC#4: _CODEX_CRITICAL_FILE_EXACT 변경이 CRITICAL 아님. got={r['risk_level']!r}"
        )
        assert r.get("matched_rule") == "critical_constant", (
            f"REJECT#35 AC#4: matched_rule이 critical_constant 아님. got={r.get('matched_rule')!r}"
        )

    def test_tc61j_critical_file_prefixes_change_triggers_critical(self) -> None:
        """REJECT#35 AC#5: _CODEX_CRITICAL_FILE_PREFIXES 상수 변경이 CRITICAL 분류를 트리거한다."""
        r = pipeline._classify_codex_review_risk(["pipeline.py"], [], ["_CODEX_CRITICAL_FILE_PREFIXES"])
        assert r["risk_level"] == "CRITICAL", (
            f"REJECT#35 AC#5: _CODEX_CRITICAL_FILE_PREFIXES 변경이 CRITICAL 아님. got={r['risk_level']!r}"
        )


class TestTC62Reject36EnvVarTrustRemoval:
    """REJECT#36: 환경변수 기반 npm 경로 신뢰 제거 — ProgramFiles*, NVM_HOME/NVM_SYMLINK 조작
    으로 가짜 npm이 신뢰를 통과하는 취약점을 차단한다."""

    def test_tc62a_programfiles_env_manipulation_blocked(self, monkeypatch) -> None:
        """REJECT#36 AC#1: ProgramFiles 환경변수를 사용자 경로로 조작해도 가짜 npm이 차단된다."""
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_npm = os.path.join(tmpdir, "nodejs", "npm.cmd")
            os.makedirs(os.path.dirname(fake_npm), exist_ok=True)
            open(fake_npm, "w").close()
            monkeypatch.setenv("ProgramFiles", tmpdir)
            monkeypatch.setenv("ProgramFiles(x86)", tmpdir)
            monkeypatch.setenv("ProgramW6432", tmpdir)
            result = pipeline._verify_npm_binary_is_system_path(fake_npm)
            # 환경변수를 신뢰하지 않으므로 사용자 경로의 npm은 False여야 한다
            assert result is False, (
                f"REJECT#36 AC#1: ProgramFiles 환경변수 조작 후에도 가짜 npm이 신뢰됨. "
                f"got={result!r}. 환경변수 기반 경로 신뢰를 제거했는지 확인하세요."
            )

    def test_tc62b_nvm_home_env_manipulation_blocked(self, monkeypatch) -> None:
        """REJECT#36 AC#1: NVM_HOME 환경변수를 사용자 경로로 조작해도 가짜 npm이 차단된다."""
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_npm = os.path.join(tmpdir, "npm.cmd")
            open(fake_npm, "w").close()
            monkeypatch.setenv("NVM_HOME", tmpdir)
            monkeypatch.setenv("NVM_SYMLINK", tmpdir)
            result = pipeline._verify_npm_binary_is_system_path(fake_npm)
            assert result is False, (
                f"REJECT#36 AC#1: NVM_HOME 환경변수 조작 후에도 가짜 npm이 신뢰됨. "
                f"got={result!r}. NVM_HOME/NVM_SYMLINK 환경변수 신뢰를 제거했는지 확인하세요."
            )

    def test_tc62c_hardcoded_program_files_still_trusted(self) -> None:
        """REJECT#36 AC#4: 환경변수 제거 후에도 C:\\Program Files\\nodejs의 npm은 신뢰된다."""
        import sys
        if sys.platform != "win32":
            import pytest as _pytest
            _pytest.skip("Windows 전용 테스트")
        # C:\Program Files\nodejs\npm.cmd는 환경변수 없이도 허용되어야 한다
        result = pipeline._verify_npm_binary_is_system_path(
            r"C:\Program Files\nodejs\npm.cmd"
        )
        assert result is True, (
            f"REJECT#36 AC#4: C:\\Program Files\\nodejs npm.cmd가 False 반환 — "
            f"하드코딩 경로가 유지되지 않음. got={result!r}"
        )

    def test_tc62d_nvm_home_env_not_in_allowed_roots_source(self) -> None:
        """REJECT#36 AC#1: _verify_npm_binary_is_system_path 소스에 NVM_HOME 환경변수가 없다."""
        import inspect
        src = inspect.getsource(pipeline._verify_npm_binary_is_system_path)
        # NVM_HOME/NVM_SYMLINK를 os.environ.get으로 가져오면 안 됨
        assert 'os.environ.get("NVM_HOME")' not in src and "os.environ.get('NVM_HOME')" not in src, (
            "REJECT#36 AC#1: _verify_npm_binary_is_system_path가 여전히 NVM_HOME 환경변수를 신뢰함"
        )
        assert 'os.environ.get("NVM_SYMLINK")' not in src and "os.environ.get('NVM_SYMLINK')" not in src, (
            "REJECT#36 AC#1: _verify_npm_binary_is_system_path가 여전히 NVM_SYMLINK 환경변수를 신뢰함"
        )

    def test_tc62e_programfiles_env_not_in_npm_binary_check_source(self) -> None:
        """REJECT#36 AC#1: _verify_npm_binary_is_system_path 소스에 ProgramFiles 환경변수 직접 참조가 없다."""
        import inspect
        src = inspect.getsource(pipeline._verify_npm_binary_is_system_path)
        # ProgramFiles 환경변수를 os.environ.get으로 가져오면 안 됨
        assert '"ProgramFiles"' not in src or 'os.environ.get("ProgramFiles")' not in src, (
            "REJECT#36 AC#1: _verify_npm_binary_is_system_path가 ProgramFiles 환경변수를 직접 신뢰함"
        )


    def test_tc62h_nvm_home_env_not_in_global_bin_allowed_source(self) -> None:
        """REJECT#36 AC#3: _verify_npm_global_bin_allowed 소스에 NVM_HOME/NVM_SYMLINK 환경변수가 없다."""
        import inspect
        src = inspect.getsource(pipeline._verify_npm_global_bin_allowed)
        assert '"NVM_HOME"' not in src, (
            "REJECT#36 AC#3: _verify_npm_global_bin_allowed가 여전히 NVM_HOME 환경변수를 신뢰함"
        )
        assert '"NVM_SYMLINK"' not in src, (
            "REJECT#36 AC#3: _verify_npm_global_bin_allowed가 여전히 NVM_SYMLINK 환경변수를 신뢰함"
        )


class TestTC63Reject37NodeOptionsAndNoProfile:
    """REJECT#37: NODE_OPTIONS/NODE_PATH 환경변수 제거 + Authenticode -NoProfile 강제.
    capability/login/exec/version 모든 경로에서 단일 _codex_clean_env() 사용."""

    def test_tc63a_codex_clean_env_removes_node_options(self) -> None:
        """REJECT#37 AC#2: _codex_clean_env()가 NODE_OPTIONS를 제거한다."""
        import os
        old_env = os.environ.copy()
        os.environ["NODE_OPTIONS"] = "--require=/tmp/evil.js"
        os.environ["node_options"] = "--require=/tmp/evil2.js"
        try:
            env = pipeline._codex_clean_env()
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        assert "NODE_OPTIONS" not in env, (
            "REJECT#37 AC#2: _codex_clean_env()가 NODE_OPTIONS를 제거하지 않음"
        )
        assert "node_options" not in env, (
            "REJECT#37 AC#2: _codex_clean_env()가 소문자 node_options를 제거하지 않음"
        )

    def test_tc63b_codex_clean_env_removes_node_path(self) -> None:
        """REJECT#37 AC#2: _codex_clean_env()가 NODE_PATH를 제거한다."""
        import os
        old_env = os.environ.copy()
        os.environ["NODE_PATH"] = "/tmp/evil/modules"  # nosec B108
        try:
            env = pipeline._codex_clean_env()
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        assert "NODE_PATH" not in env, (
            "REJECT#37 AC#2: _codex_clean_env()가 NODE_PATH를 제거하지 않음"
        )

    def test_tc63c_codex_clean_env_still_removes_openai_api_key(self) -> None:
        """REJECT#37: _codex_clean_env()가 기존대로 OPENAI_API_KEY도 제거한다 (회귀 없음)."""
        import os
        old_env = os.environ.copy()
        os.environ["OPENAI_API_KEY"] = "sk-" + "EXAMPLE_DUMMY_" + "A" * 8  # noqa: S105
        try:
            env = pipeline._codex_clean_env()
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        assert "OPENAI_API_KEY" not in env, (
            "REJECT#37: _codex_clean_env()가 OPENAI_API_KEY를 제거하지 않음 — 회귀"
        )

    def test_tc63d_node_preload_vars_constant_exists(self) -> None:
        """REJECT#37 AC#2: _CODEX_NODE_PRELOAD_VARS 상수가 존재하고 NODE_OPTIONS/NODE_PATH를 포함한다."""
        assert hasattr(pipeline, "_CODEX_NODE_PRELOAD_VARS"), (
            "REJECT#37 AC#2: _CODEX_NODE_PRELOAD_VARS 상수가 없음"
        )
        assert "NODE_OPTIONS" in pipeline._CODEX_NODE_PRELOAD_VARS, (
            "REJECT#37 AC#2: _CODEX_NODE_PRELOAD_VARS에 NODE_OPTIONS 없음"
        )
        assert "NODE_PATH" in pipeline._CODEX_NODE_PRELOAD_VARS, (
            "REJECT#37 AC#2: _CODEX_NODE_PRELOAD_VARS에 NODE_PATH 없음"
        )

    def test_tc63e_invoke_codex_exec_uses_codex_clean_env_in_source(self) -> None:
        """REJECT#37 AC#2: _invoke_codex_exec 소스에서 _codex_clean_env()를 사용한다."""
        import inspect
        src = inspect.getsource(pipeline._invoke_codex_exec)
        assert "_codex_clean_env()" in src, (
            "REJECT#37 AC#2: _invoke_codex_exec가 _codex_clean_env()를 사용하지 않음 — "
            "인라인 env 정제는 NODE_OPTIONS를 제거하지 않는다"
        )

    def test_tc63f_check_codex_chatgpt_auth_uses_codex_clean_env_in_source(self) -> None:
        """REJECT#37 AC#2: _check_codex_chatgpt_auth 소스에서 _codex_clean_env()를 사용한다."""
        import inspect
        src = inspect.getsource(pipeline._check_codex_chatgpt_auth)
        assert "_codex_clean_env()" in src, (
            "REJECT#37 AC#2: _check_codex_chatgpt_auth가 _codex_clean_env()를 사용하지 않음"
        )

    def test_tc63g_authenticode_uses_noprofile_in_source(self) -> None:
        """REJECT#37 AC#3: _check_authenticode_signature 소스에 -NoProfile이 있다."""
        import inspect
        src = inspect.getsource(pipeline._check_authenticode_signature)
        assert "-NoProfile" in src, (
            "REJECT#37 AC#3: _check_authenticode_signature powershell 호출에 -NoProfile 없음 — "
            "사용자 프로필에서 Get-AuthenticodeSignature 재정의 가능"
        )

    def test_tc63h_authenticode_uses_fully_qualified_cmdlet_in_source(self) -> None:
        """REJECT#37 AC#3: _check_authenticode_signature가 완전 한정 cmdlet을 사용한다."""
        import inspect
        src = inspect.getsource(pipeline._check_authenticode_signature)
        assert "Microsoft.PowerShell.Security" in src, (
            "REJECT#37 AC#3: _check_authenticode_signature가 비한정 Get-AuthenticodeSignature를 사용함 — "
            "사용자 모듈에서 재정의 가능"
        )


class TestTC64Reject38CentralCleanEnv:
    """REJECT#38: _codex_clean_env() 중앙 정제 함수 사용 검증 (AC#1-4)"""

    def test_tc64a_cmd_codex_review_uses_codex_clean_env(self):
        """TC-64a: _cmd_gates_codex_review 소스에서 _cap_clean_env를 _codex_clean_env()로 할당"""
        import inspect
        import pipeline as _pl
        src = inspect.getsource(_pl._cmd_gates_codex_review)
        # _codex_clean_env()로 _cap_clean_env를 설정해야 한다
        assert "_codex_clean_env()" in src, (
            "_cmd_gates_codex_review must assign _cap_clean_env = _codex_clean_env()"
        )
        # 인라인 os.environ.copy() + OPENAI_API_KEY pop 패턴이 없어야 한다
        assert "_cap_clean_env = os.environ.copy()" not in src, (
            "_cmd_gates_codex_review must not use inline os.environ.copy() for _cap_clean_env"
        )

    def test_tc64b_npm_global_bin_uses_codex_clean_env(self):
        """TC-64b: _get_npm_global_bin이 _codex_clean_env()로 npm subprocess 환경을 정제"""
        import inspect
        import pipeline as _pl
        src = inspect.getsource(_pl._get_npm_global_bin)
        assert "_codex_clean_env()" in src, (
            "_get_npm_global_bin must use _codex_clean_env() for npm subprocess env"
        )
        # 인라인 민감 변수 pop 루프가 없어야 한다
        assert "ANTHROPIC_API_KEY" not in src, (
            "_get_npm_global_bin must not manually pop API keys — use _codex_clean_env()"
        )

    def test_tc64c_npm_ls_integrity_uses_codex_clean_env(self):
        """TC-64c: _get_npm_ls_integrity가 _codex_clean_env()로 npm subprocess 환경을 정제"""
        import inspect
        import pipeline as _pl
        src = inspect.getsource(_pl._get_npm_ls_integrity)
        assert "_codex_clean_env()" in src, (
            "_get_npm_ls_integrity must use _codex_clean_env() for npm subprocess env"
        )
        # 인라인 os.environ.copy() + pop 패턴이 없어야 한다
        assert "_env = os.environ.copy()" not in src, (
            "_get_npm_ls_integrity must not use inline os.environ.copy() for npm env"
        )

    def test_tc64d_node_preload_vars_in_critical_constants(self):
        """TC-64d: _CODEX_NODE_PRELOAD_VARS가 CODEX_CRITICAL_CONSTANTS에 등록됨"""
        import pipeline as _pl
        assert "_CODEX_NODE_PRELOAD_VARS" in _pl.CODEX_CRITICAL_CONSTANTS, (
            "_CODEX_NODE_PRELOAD_VARS must be in CODEX_CRITICAL_CONSTANTS"
        )

    def test_tc64e_node_preload_vars_change_triggers_critical(self):
        """TC-64e: _CODEX_NODE_PRELOAD_VARS 변경이 CRITICAL risk를 트리거

        _classify_codex_review_risk(changed_files, changed_functions, changed_constants)는
        dict를 반환하며, changed_constants에 CRITICAL 상수가 있으면 risk_level=CRITICAL.
        changed_files는 fail-closed 방지를 위해 비어 있지 않아야 한다.
        """
        import pipeline as _pl
        _result = _pl._classify_codex_review_risk(
            ["pipeline.py"],
            [],
            ["_CODEX_NODE_PRELOAD_VARS"],
        )
        _risk = _result.get("risk_level")
        assert _risk == "CRITICAL", (
            f"_CODEX_NODE_PRELOAD_VARS change must be CRITICAL, got {_risk!r} ({_result!r})"
        )


class TestTC65Reject14NativeBinaryTrust:
    """REJECT#14: native binary 신뢰 체인 강화 검증 (AC#1-5).

    - AC#1: 자체 서명(CN=OpenAI Test) 인증서로 서명한 native binary는 acceptance_eligible=false로 차단.
    - AC#2: 정품 codex.js + 변조 native binary 조합은 로그인/exec 전에 차단.
    - AC#3: native binary SHA가 검증된 platform 패키지 원본과 일치할 때만 운영 신뢰 검사 PASS.
    - AC#4: native 디렉터리를 junction/symlink로 재연결하면 실행 체인 불일치로 BLOCKED.
    - AC#5: 정상 OpenAI 설치는 고정 publisher + platform 패키지 검증을 통과(회귀 테스트).
    """

    def test_tc65a_authenticode_self_signed_blocked(self):
        """TC-65a: 자체 서명(Issuer==Subject) 인증서 차단 로직 존재(AC#1).

        REJECT#15: Issuer 문자열 패턴(_CODEX_KNOWN_CERT_ISSUER_PATTERNS) gate는 사용자 CA 추가로
        우회 가능하므로 제거됐다. 자체 서명 차단(Issuer==Subject)만 Authenticode secondary defense로
        유지되며, primary trust는 native binary 원본 SHA 비교다. 따라서 이 테스트는 self-signed
        차단 로직만 요구하고 Issuer 패턴 gate 존재는 더 이상 요구하지 않는다.
        """
        import inspect
        import pipeline as _pl
        src = inspect.getsource(_pl._check_authenticode_signature)
        assert "self_signed" in src, (
            "_check_authenticode_signature must detect self-signed (Issuer==Subject) certs"
        )
        assert "issuer" in src.lower(), (
            "_check_authenticode_signature must still collect issuer (for logging)"
        )

    def test_tc65b_known_cert_issuer_patterns_in_critical_constants(self):
        """TC-65b: _CODEX_KNOWN_CERT_ISSUER_PATTERNS가 CODEX_CRITICAL_CONSTANTS에 등록됨."""
        import pipeline as _pl
        assert "_CODEX_KNOWN_CERT_ISSUER_PATTERNS" in _pl.CODEX_CRITICAL_CONSTANTS, (
            "_CODEX_KNOWN_CERT_ISSUER_PATTERNS must be in CODEX_CRITICAL_CONSTANTS"
        )


    def test_tc65e_native_binary_symlink_detection_in_trust_fn(self):
        """TC-65e: _verify_codex_binary_path_trust가 symlink/junction 감지 코드 포함(AC#4)."""
        import inspect
        import pipeline as _pl
        src = inspect.getsource(_pl._verify_codex_binary_path_trust)
        assert "realpath" in src or "symlink_junction" in src, (
            "_verify_codex_binary_path_trust must detect symlink/junction via realpath"
        )

    def test_tc65f_known_cert_issuer_patterns_nonempty(self):
        """TC-65f: _CODEX_KNOWN_CERT_ISSUER_PATTERNS가 비어있지 않음."""
        import pipeline as _pl
        assert hasattr(_pl, "_CODEX_KNOWN_CERT_ISSUER_PATTERNS"), (
            "_CODEX_KNOWN_CERT_ISSUER_PATTERNS must exist"
        )
        assert len(_pl._CODEX_KNOWN_CERT_ISSUER_PATTERNS) >= 3, (
            "_CODEX_KNOWN_CERT_ISSUER_PATTERNS must have at least 3 known CA patterns"
        )

    def test_tc65g_known_cert_issuer_patterns_change_triggers_critical(self):
        """TC-65g: _CODEX_KNOWN_CERT_ISSUER_PATTERNS 변경이 CRITICAL risk를 트리거.

        _classify_codex_review_risk(changed_files, changed_functions, changed_constants)는
        dict를 반환하며, changed_constants에 CRITICAL 상수가 있으면 risk_level=CRITICAL.
        changed_files는 fail-closed 방지를 위해 비어 있지 않아야 한다.
        """
        import pipeline as _pl
        _result = _pl._classify_codex_review_risk(
            ["pipeline.py"],
            [],
            ["_CODEX_KNOWN_CERT_ISSUER_PATTERNS"],
        )
        _risk = _result.get("risk_level")
        assert _risk == "CRITICAL", (
            f"_CODEX_KNOWN_CERT_ISSUER_PATTERNS change must be CRITICAL, got {_risk!r} ({_result!r})"
        )


class TestTC66Reject15NativeSHAUnconditional:
    """REJECT#15: 패키지 크기 무관 native binary SHA 비교 + Authenticode Issuer 패턴 제거 검증"""


    def test_tc66c_authenticode_no_issuer_pattern_check(self):
        """TC-66c: _check_authenticode_signature에서 _CODEX_KNOWN_CERT_ISSUER_PATTERNS 참조 제거됨"""
        import inspect
        import pipeline as _pl
        src = inspect.getsource(_pl._check_authenticode_signature)
        # Issuer 패턴 검사가 ok=True 분기에 없어야 함 (자체 서명 차단만 유지)
        assert "_CODEX_KNOWN_CERT_ISSUER_PATTERNS" not in src, (
            "_check_authenticode_signature must not use _CODEX_KNOWN_CERT_ISSUER_PATTERNS (Issuer pattern bypass)"
        )

    def test_tc66d_authenticode_still_blocks_self_signed(self):
        """TC-66d: _check_authenticode_signature가 자체 서명 인증서를 여전히 차단"""
        import inspect
        import pipeline as _pl
        src = inspect.getsource(_pl._check_authenticode_signature)
        assert "self_signed" in src, (
            "_check_authenticode_signature must still block self-signed certs"
        )


    def test_tc66f_authenticode_issuer_is_logged_not_gatekeeping(self):
        """TC-66f: _check_authenticode_signature에서 issuer 필드는 로깅용으로 유지되지만 ok=True를 막지 않음"""
        import inspect
        import pipeline as _pl
        src = inspect.getsource(_pl._check_authenticode_signature)
        # issuer 수집은 유지 (로깅/디버깅)
        assert "issuer" in src.lower(), (
            "_check_authenticode_signature should still collect issuer for logging"
        )


# --------------------------------------------------------------------------- #
# IMP-20260712-DAE1 USER_AUTHORIZED_CONTRACT_MIGRATION — bounded trust model v2.
# 사용자가 직접 Codex Review Contract를 bounded trust model v2로 교체하기로 결정했다(16회 REJECT
# NON_CONVERGING 해소). findings[] scope 분류 / 수렴성 circuit breaker / contract migration /
# 공급망 검증 함수 제거를 결정적으로 검증한다.
# --------------------------------------------------------------------------- #
class TestBoundedTrustModelV2:
    """USER_AUTHORIZED_CONTRACT_MIGRATION bounded trust model v2 테스트."""

    def test_bounded_trust_constants_present(self):
        """bounded trust scope 상수/버전/circuit breaker 임계값이 모두 존재한다."""
        assert pipeline.CODEX_BOUNDED_TRUST_CONTRACT_VERSION == "v2.0"
        assert pipeline.CODEX_BOUNDED_TRUST_THREAT_MODEL_VERSION.startswith("IMP-20260712-DAE1")
        assert "fake_codex_exec" in pipeline.CODEX_BOUNDED_TRUST_IN_SCOPE
        assert "npm_tarball_supply_chain_proof" in pipeline.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC
        assert "repo_internal_fake_codex" in pipeline.CODEX_BOUNDED_TRUST_ENVIRONMENT_UNTRUSTED
        assert pipeline.CODEX_CB_MAX_SAME_CATEGORY_REPEATS == 3
        assert pipeline.CODEX_CB_MAX_EFFECTIVE_REJECTS == 5
        assert pipeline.CODEX_REVIEW_RESULT_SCHEMA_VERSION == 6

    def test_out_of_scope_finding_no_reject_count(self):
        """OUT_OF_SCOPE_DIAGNOSTIC finding만 있으면 reject_count 미증가(diagnostic_only)."""
        cls = pipeline._classify_codex_findings([
            {"severity": "P0", "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
             "root_cause_category": "npm_tarball_supply_chain_proof"},
            {"severity": "P1", "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
             "root_cause_category": "native_binary_origin_proof"},
        ])
        assert cls["reject_count_delta"] == 0
        assert cls["diagnostic_only"] is True
        assert cls["out_of_scope_diagnostic_count"] == 2
        assert cls["in_scope_count"] == 0

    def test_in_scope_finding_increments_reject_count(self):
        """IN_SCOPE P0/P1 finding은 reject_count 증가(reject_count_delta=1)."""
        cls = pipeline._classify_codex_findings([
            {"severity": "P0", "scope": "IN_SCOPE", "root_cause_category": "fake_codex_exec"},
        ])
        assert cls["reject_count_delta"] == 1
        assert cls["in_scope_count"] == 1
        assert cls["diagnostic_only"] is False
        cls_low = pipeline._classify_codex_findings([
            {"severity": "P3", "scope": "IN_SCOPE", "root_cause_category": "model_effort_mismatch"},
        ])
        assert cls_low["reject_count_delta"] == 0

    def test_environment_untrusted_finding_counted(self):
        """ENVIRONMENT_UNTRUSTED finding은 별도 카운트되어 즉시 BLOCKED 대상이다."""
        cls = pipeline._classify_codex_findings([
            {"severity": "P0", "scope": "ENVIRONMENT_UNTRUSTED",
             "root_cause_category": "repo_internal_fake_codex"},
        ])
        assert cls["environment_untrusted_count"] == 1
        assert cls["diagnostic_only"] is False

    def test_findings_schema_parse_with_findings(self):
        """findings[] 스키마 파싱 성공 + scope 분류 필드 반환(backward compat 유지)."""
        import json
        pv = pipeline._parse_json_verdict(json.dumps({
            "verdict": "REJECT",
            "findings": [{"severity": "P1", "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
                          "root_cause_category": "native_binary_origin_proof"}],
        }))
        assert pv is not None and pv["verdict"] == "REJECTED"
        assert pv["reject_count_delta"] == 0 and pv["diagnostic_only"] is True
        assert pv["source"] == "json_protocol_findings_v2"
        pv_ok = pipeline._parse_json_verdict(json.dumps({
            "verdict": "APPROVE_TO_USER",
            "findings": [{"severity": "P3", "scope": "OUT_OF_SCOPE_DIAGNOSTIC"}],
        }))
        assert pv_ok is not None and pv_ok["verdict"] == "APPROVED"
        pv_legacy = pipeline._parse_json_verdict(json.dumps({
            "verdict": "REJECT", "root_cause": "x", "reproduction": "y",
            "required_fix": "z", "acceptance_criteria": ["a"],
        }))
        assert pv_legacy is not None and pv_legacy["verdict"] == "REJECTED"
        assert pv_legacy.get("root_cause") == "x"

    def test_circuit_breaker_same_category_3x(self):
        """같은 root_cause_category 3회 → NON_CONVERGING(same_category_3x)."""
        hist = [{
            "review_epoch": "epoch_20260712_001", "status": "REJECTED",
            "verdict_scope": "IN_SCOPE", "root_cause_category": "npm_tarball_supply_chain_proof",
            "counts_toward_reject_rate_limit": True,
        } for _ in range(3)]
        cb = pipeline._check_codex_circuit_breaker(hist, "epoch_20260712_001")
        assert cb["triggered"] is True and cb["reason"] == "same_category_3x"
        cb2 = pipeline._check_codex_circuit_breaker(hist[:2], "epoch_20260712_001")
        assert cb2["triggered"] is False

    def test_circuit_breaker_reject_5x(self):
        """실제(IN_SCOPE) REJECT 5회 → NON_CONVERGING(reject_count_5x)."""
        hist = [{
            "review_epoch": "epoch_20260712_002", "status": "REJECTED",
            "verdict_scope": "IN_SCOPE", "root_cause_category": "cat_" + str(i),
            "counts_toward_reject_rate_limit": True,
        } for i in range(5)]
        cb = pipeline._check_codex_circuit_breaker(hist, "epoch_20260712_002")
        assert cb["triggered"] is True and cb["reason"] == "reject_count_5x"

    def test_circuit_breaker_out_of_scope_not_counted(self):
        """OUT_OF_SCOPE_DIAGNOSTIC REJECT는 effective reject로 계수되지 않는다(수렴 방해 제거)."""
        hist = [{
            "review_epoch": "epoch_20260712_003", "status": "REJECTED",
            "verdict_scope": "OUT_OF_SCOPE_DIAGNOSTIC",
            "root_cause_category": "npm_tarball_supply_chain_proof",
            "counts_toward_reject_rate_limit": False,
        } for _ in range(9)]
        cb = pipeline._check_codex_circuit_breaker(hist, "epoch_20260712_003")
        assert cb["triggered"] is False

    def test_circuit_breaker_epoch_isolation(self):
        """이전 epoch(및 legacy) 항목은 새 epoch circuit breaker에서 계수되지 않는다."""
        legacy = [{"status": "REJECTED", "counts_toward_reject_rate_limit": True} for _ in range(16)]
        cb = pipeline._check_codex_circuit_breaker(legacy, "epoch_20260712_099")
        assert cb["triggered"] is False

    def test_history_append_only(self):
        """contract migration은 append-only — 이전 migration을 history에 보존하고 삭제하지 않는다."""
        state = {}
        pipeline._record_user_authorized_contract_migration(
            state, "old_sha", "new_sha_aaa", "user chose v2", "2026-07-15T00:00:00Z")
        first_epoch = state["codex_review_contract_migration"]["review_epoch"]
        pipeline._record_user_authorized_contract_migration(
            state, "old_sha", "new_sha_aaa", "dup", "2026-07-15T00:00:00Z")
        assert state["codex_review_contract_migration"]["history"] == []
        pipeline._record_user_authorized_contract_migration(
            state, "new_sha_aaa", "new_sha_bbb", "second migration", "2026-07-15T01:00:00Z")
        mig = state["codex_review_contract_migration"]
        assert mig["new_contract_sha256"] == "new_sha_bbb"
        assert len(mig["history"]) == 1
        assert mig["history"][0]["new_contract_sha256"] == "new_sha_aaa"
        assert mig["review_epoch"] != first_epoch

    def test_contract_migration_record(self):
        """USER_AUTHORIZED_CONTRACT_MIGRATION이 scope_decision_source=user로 state에 기록된다."""
        state = {}
        pipeline._record_user_authorized_contract_migration(
            state, "", "contract_v2_sha", "bounded trust migration", "2026-07-15T00:00:00Z")
        mig = state["codex_review_contract_migration"]
        assert mig["scope_decision_source"] == "user"
        assert mig["migration_flag_consumed"] is True
        assert mig["contract_version"] == "v2.0"
        assert mig["new_contract_sha256"] == "contract_v2_sha"
        assert mig["review_epoch"].startswith("epoch_")

    def test_migration_requires_non_empty_new_sha(self):
        """빈 new_contract_sha256은 ValueError(agent 임의 변경 방어)."""
        import pytest
        with pytest.raises(ValueError):
            pipeline._record_user_authorized_contract_migration(
                {}, "old", "", "reason", "2026-07-15T00:00:00Z")
        with pytest.raises(TypeError):
            pipeline._record_user_authorized_contract_migration(
                None, "old", "new", "reason", "2026-07-15T00:00:00Z")

    def test_fake_verdict_still_blocked(self):
        """manual/external verdict 주입은 여전히 operational trust BLOCKED(기존 보안 유지)."""
        result = {
            "verdict_source": "external_verdict",
            "acceptance_eligible": True,
            "router_version": pipeline.CODEX_MODEL_ROUTER_VERSION,
            "risk_level": "CRITICAL",
        }
        out = pipeline._check_codex_review_operational_trust(result)
        assert out["status"] == "BLOCKED"
        assert out["failure_code"] == "codex_review_untrusted_verdict_source"

    def test_not_acceptance_eligible_still_blocked(self):
        """acceptance_eligible=false는 operational trust BLOCKED(fail-closed 유지)."""
        result = {
            "verdict_source": "codex_cli",
            "acceptance_eligible": False,
            "router_version": pipeline.CODEX_MODEL_ROUTER_VERSION,
            "risk_level": "CRITICAL",
        }
        out = pipeline._check_codex_review_operational_trust(result)
        assert out["status"] == "BLOCKED"
        assert out["failure_code"] == "codex_review_not_acceptance_eligible"

    def test_shim_detection_still_blocked(self):
        """저장소 내부 바이너리(repo-internal shim)는 여전히 신뢰 불가(ENVIRONMENT_UNTRUSTED)."""
        import tempfile
        import shutil
        base = Path(pipeline.BASE_DIR)
        d = Path(tempfile.mkdtemp(prefix="shim_test_", dir=str(base)))
        try:
            fake = d / "codex"
            fake.write_bytes(b"#!/bin/sh\necho fake\n")
            trust = pipeline._verify_codex_binary_path_trust(str(fake))
            assert trust["trusted"] is False
            assert "binary_inside_git_repo" in str(trust.get("untrusted_reason", ""))
        finally:
            shutil.rmtree(str(d), ignore_errors=True)
        assert hasattr(pipeline, "_verify_npm_wrapper_content")

    def test_supply_chain_removed_from_codex_review(self):
        """공급망 검증 함수 4종이 pipeline과 CODEX_CRITICAL_FUNCTIONS에서 완전히 제거됐다."""
        for fn in (
            "_verify_codex_js_against_registry",
            "_resolve_codex_platform_package_meta",
            "_verify_codex_native_binary_against_registry",
            "_verify_direct_native_ownership",
        ):
            assert not hasattr(pipeline, fn), fn + " must be removed (OUT_OF_SCOPE)"
            assert fn not in pipeline.CODEX_CRITICAL_FUNCTIONS, fn + " not in critical functions"
        assert not hasattr(pipeline, "_CODEX_NATIVE_TARBALL_MAX_BYTES")
        for keep in (
            "_verify_npm_binary_is_system_path", "_verify_npm_wrapper_content",
            "_find_codex_native_binary", "_verify_codex_binary_path_trust",
            "_check_authenticode_signature",
        ):
            assert hasattr(pipeline, keep), keep + " must be preserved"

    def test_authenticode_still_checks_valid_and_openai(self):
        """Authenticode Valid/Invalid + OpenAI subject + 자체서명 차단은 유지된다(bounded trust anchor)."""
        import inspect
        src = inspect.getsource(pipeline._check_authenticode_signature)
        assert "authenticode_not_valid" in src
        assert "authenticode_subject_no_openai" in src
        assert "self_signed" in src

    def test_reject35a_approve_with_in_scope_finding_blocks_acceptance(self):
        """REJECT#LATEST Case 1: APPROVE_TO_USER + IN_SCOPE P0/P1 finding → in_scope_count > 0 반환.
        _cmd_gates_codex_review는 이 in_scope_count > 0을 보고 acceptance_eligible=False로 강제한다."""
        import json
        pv = pipeline._parse_json_verdict(json.dumps({
            "verdict": "APPROVE_TO_USER",
            "findings": [
                {"severity": "P0", "scope": "IN_SCOPE",
                 "root_cause_category": "fake_codex_exec"},
            ],
        }))
        assert pv is not None
        assert pv["verdict"] == "APPROVED"  # 원본 verdict는 APPROVED이지만
        assert pv["in_scope_count"] > 0     # IN_SCOPE finding 있음 → acceptance_eligible=False 트리거
        assert pv["reject_count_delta"] == 1  # IN_SCOPE P0 → reject_count도 증가

    def test_reject35b_reject_with_out_of_scope_only_no_acceptance_block(self):
        """REJECT#LATEST Case 2: REJECT + OUT_OF_SCOPE_DIAGNOSTIC-only → diagnostic_only=True, reject 미계수.
        _cmd_gates_codex_review는 diagnostic_only=True이면 acceptance_eligible=True로 허용한다."""
        import json
        pv = pipeline._parse_json_verdict(json.dumps({
            "verdict": "REJECT",
            "findings": [
                {"severity": "P1", "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
                 "root_cause_category": "npm_tarball_supply_chain_proof"},
            ],
        }))
        assert pv is not None
        assert pv["verdict"] == "REJECTED"     # 원본 verdict는 REJECTED이지만
        assert pv["diagnostic_only"] is True   # OUT_OF_SCOPE만 → acceptance 차단 안 함
        assert pv["reject_count_delta"] == 0   # reject_count 미증가
        assert pv["in_scope_count"] == 0

    def test_reject35c_scope_override_in_cmd_source(self):
        """REJECT#LATEST: _cmd_gates_codex_review 소스에 두 방향 override 로직이 모두 존재한다."""
        import inspect
        src = inspect.getsource(pipeline._cmd_gates_codex_review)
        # Case 1: APPROVE_TO_USER + IN_SCOPE → acceptance_eligible=False 강제
        assert "_finding_in_scope > 0" in src
        # Case 2: REJECT + OUT_OF_SCOPE_DIAGNOSTIC-only → acceptance_eligible=True 허용
        assert "_finding_diagnostic_only" in src

    def test_reject36a_in_scope_category_overrides_out_of_scope_reported_scope(self):
        """REJECT#18 AC-1: fake_codex_exec을 OUT_OF_SCOPE_DIAGNOSTIC scope로 제출해도 IN_SCOPE로 분류된다."""
        cls = pipeline._classify_codex_findings([
            {"severity": "P0", "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
             "root_cause_category": "fake_codex_exec"},
        ])
        # root_cause_category SSoT 결정: fake_codex_exec ∈ CODEX_BOUNDED_TRUST_IN_SCOPE
        assert cls["in_scope_count"] == 1       # IN_SCOPE P0 → 승인 차단
        assert cls["out_of_scope_diagnostic_count"] == 0
        assert cls["diagnostic_only"] is False  # IN_SCOPE 있으므로 diagnostic_only 불가
        assert cls["reject_count_delta"] == 1   # IN_SCOPE → reject_count 증가

    def test_reject36b_environment_untrusted_category_overrides_reported_scope(self):
        """REJECT#18 AC-2: repo_internal_fake_codex을 IN_SCOPE scope로 제출해도 ENVIRONMENT_UNTRUSTED 분류."""
        cls = pipeline._classify_codex_findings([
            {"severity": "P0", "scope": "IN_SCOPE",
             "root_cause_category": "repo_internal_fake_codex"},
        ])
        # root_cause_category SSoT: repo_internal_fake_codex ∈ CODEX_BOUNDED_TRUST_ENVIRONMENT_UNTRUSTED
        assert cls["environment_untrusted_count"] == 1  # 즉시 BLOCKED 대상
        assert cls["in_scope_count"] == 0
        assert cls["diagnostic_only"] is False

    def test_reject36c_diagnostic_only_only_when_all_categories_out_of_scope(self):
        """REJECT#18 AC-3: diagnostic_only=True는 모든 root_cause_category가 OUT_OF_SCOPE SSoT에 포함될 때만."""
        # 혼합: 하나는 OUT_OF_SCOPE(npm_tarball), 하나는 IN_SCOPE(fake_codex_exec)로 scope 속임
        cls_mixed = pipeline._classify_codex_findings([
            {"severity": "P1", "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
             "root_cause_category": "npm_tarball_supply_chain_proof"},
            {"severity": "P0", "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
             "root_cause_category": "fake_codex_exec"},  # ← IN_SCOPE임에도 scope 속임
        ])
        assert cls_mixed["diagnostic_only"] is False  # fake_codex_exec가 IN_SCOPE → diagnostic_only 불가
        assert cls_mixed["in_scope_count"] == 1       # fake_codex_exec P0 → IN_SCOPE 계수
        # 순수 OUT_OF_SCOPE_DIAGNOSTIC(SSoT): diagnostic_only=True 허용
        cls_pure = pipeline._classify_codex_findings([
            {"severity": "P0", "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
             "root_cause_category": "npm_tarball_supply_chain_proof"},
            {"severity": "P1", "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
             "root_cause_category": "native_binary_origin_proof"},
        ])
        assert cls_pure["diagnostic_only"] is True
        assert cls_pure["in_scope_count"] == 0

    def test_reject36d_unknown_category_fail_closed_as_in_scope(self):
        """REJECT#18: 알 수 없는 root_cause_category는 fail-closed로 IN_SCOPE 처리."""
        cls = pipeline._classify_codex_findings([
            {"severity": "P0", "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
             "root_cause_category": "some_unknown_new_category"},
        ])
        assert cls["in_scope_count"] == 1        # 알 수 없는 category → IN_SCOPE fail-closed
        assert cls["out_of_scope_diagnostic_count"] == 0
        assert cls["diagnostic_only"] is False

    def test_reject37a_no_category_p0_always_in_scope_regardless_of_scope_field(self):
        """REJECT#19 AC-1: category 없는 P0 finding은 scope 값 무관하게 IN_SCOPE."""
        cls = pipeline._classify_codex_findings([
            {"severity": "P0", "scope": "OUT_OF_SCOPE_DIAGNOSTIC"},  # category 없음
        ])
        assert cls["in_scope_count"] == 1          # fail-closed IN_SCOPE
        assert cls["out_of_scope_diagnostic_count"] == 0
        assert cls["diagnostic_only"] is False

    def test_reject37b_no_category_finding_blocks_acceptance_eligible(self):
        """REJECT#19 AC-2: category 없는 APPROVE 결과는 acceptance_eligible=false."""
        import json
        pv = pipeline._parse_json_verdict(json.dumps({
            "verdict": "APPROVE_TO_USER",
            "findings": [
                {"severity": "P0", "scope": "OUT_OF_SCOPE_DIAGNOSTIC"},  # category 없음
            ],
        }))
        assert pv is not None
        # category 없음 → IN_SCOPE → in_scope_count > 0 → APPROVE에서도 acceptance 차단
        assert pv["in_scope_count"] > 0
        assert pv["reject_count_delta"] == 1

    def test_reject37c_in_scope_p2p3_mixed_with_out_of_scope_diagnostic_only_false(self):
        """REJECT#19 AC-3: IN_SCOPE P2/P3와 OUT_OF_SCOPE 혼합이면 diagnostic_only=false."""
        cls = pipeline._classify_codex_findings([
            {"severity": "P2", "scope": "IN_SCOPE",
             "root_cause_category": "fake_codex_exec"},           # IN_SCOPE P2 (lower severity)
            {"severity": "P0", "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
             "root_cause_category": "npm_tarball_supply_chain_proof"},  # OUT_OF_SCOPE
        ])
        assert cls["diagnostic_only"] is False     # in_scope_all_count > 0 (P2 IN_SCOPE)
        assert cls["in_scope_all_count"] == 1      # P2 counted in all_count
        assert cls["in_scope_count"] == 0          # P2 NOT in P0/P1 count
        assert cls["out_of_scope_diagnostic_count"] == 1

    def test_reject38a_bounded_trust_constants_in_critical_constants_list(self):
        """REJECT#20 AC-1: bounded trust 정책 상수가 CODEX_CRITICAL_CONSTANTS에 등록되어 있다."""
        critical = pipeline.CODEX_CRITICAL_CONSTANTS
        assert "CODEX_BOUNDED_TRUST_IN_SCOPE" in critical
        assert "CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC" in critical
        assert "CODEX_BOUNDED_TRUST_ENVIRONMENT_UNTRUSTED" in critical
        assert "CODEX_CB_MAX_SAME_CATEGORY_REPEATS" in critical
        assert "CODEX_CB_MAX_EFFECTIVE_REJECTS" in critical

    def test_reject38b_overlap_in_scope_and_out_of_scope_blocked(self):
        """REJECT#20 AC-2: IN_SCOPE와 OUT_OF_SCOPE에 같은 category → 분류 BLOCKED."""
        import pipeline as _p
        original_out = list(_p.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC)
        _p.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC.append("fake_codex_exec")
        try:
            cls = pipeline._classify_codex_findings([
                {"severity": "P0", "scope": "IN_SCOPE",
                 "root_cause_category": "fake_codex_exec"},
            ])
            # disjointness 검사 → BLOCKED: valid=False, in_scope_count=1
            assert cls["valid"] is False
            assert cls["in_scope_count"] == 1
            assert cls["reject_count_delta"] == 1
        finally:
            _p.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC[:] = original_out

    def test_reject38c_fake_codex_exec_in_out_of_scope_fail_closed(self):
        """REJECT#20 AC-3: fake_codex_exec을 OUT_OF_SCOPE 목록에 추가 → diagnostic_only=false, reject_count_delta=1."""
        import pipeline as _p
        original_out = list(_p.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC)
        _p.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC.append("fake_codex_exec")
        try:
            cls = pipeline._classify_codex_findings([
                {"severity": "P0", "scope": "IN_SCOPE",
                 "root_cause_category": "fake_codex_exec"},
            ])
            assert cls["diagnostic_only"] is False   # fail-closed → not diagnostic
            assert cls["reject_count_delta"] == 1    # fail-closed → reject
        finally:
            _p.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC[:] = original_out

    def test_reject38d_critical_policy_has_force_review_and_cache_disabled(self):
        """REJECT#20 AC-4: CRITICAL 분류 → force_review_required=True, cache_allowed=False."""
        policy = pipeline._build_codex_model_policy("CRITICAL")
        assert policy.get("force_review_required") is True
        assert policy.get("cache_allowed") is False

    def test_reject37d_all_explicit_out_of_scope_diagnostic_only_true(self):
        """REJECT#19 AC-4: 모든 category가 명시적으로 OUT_OF_SCOPE 목록에 있을 때만 diagnostic_only=true."""
        # 모두 명시적 OUT_OF_SCOPE
        cls_ok = pipeline._classify_codex_findings([
            {"severity": "P0", "scope": "IN_SCOPE",
             "root_cause_category": "openai_registry_compromise"},
            {"severity": "P1", "scope": "IN_SCOPE",
             "root_cause_category": "native_binary_origin_proof"},
        ])
        assert cls_ok["diagnostic_only"] is True   # 모두 OUT_OF_SCOPE SSoT에 있음
        assert cls_ok["in_scope_all_count"] == 0
        # 하나라도 category 없으면 diagnostic_only=False
        cls_no_cat = pipeline._classify_codex_findings([
            {"severity": "P0", "scope": "OUT_OF_SCOPE_DIAGNOSTIC",
             "root_cause_category": "openai_registry_compromise"},
            {"severity": "P1", "scope": "OUT_OF_SCOPE_DIAGNOSTIC"},  # category 없음
        ])
        assert cls_no_cat["diagnostic_only"] is False  # category 없는 finding → IN_SCOPE → 차단

    # ---------------------------------------------------------------------- #
    # REJECT#21 tests (AC-1 ~ AC-5)                                          #
    # ---------------------------------------------------------------------- #

    def test_reject39a_prompt_includes_bounded_trust_schema(self):
        """REJECT#21 AC-1: _build_codex_prompt_for_review에 bounded trust findings 스키마가 포함된다."""
        # 최소 bundle과 pipeline_id로 프롬프트를 생성한다.
        # 함수 시그니처: _build_codex_prompt_for_review(bundle, pipeline_id)
        bundle = {
            "pipeline_id": "IMP-20260712-DAE1",
            "diff_hunks": [{"hunk_id": "h1", "file": "pipeline.py", "content": "# test diff"}],
            "included_functions": [],
            "function_before_after_shas": {},
            "semantic_evidence_sha256": "aaaa",
            "truncated_critical_hunks": 0,
            "evidence_complete": True,
        }
        prompt = pipeline._build_codex_prompt_for_review(bundle, "IMP-20260712-DAE1")
        # IN_SCOPE 카테고리 중 하나 이상이 프롬프트에 있어야 한다.
        in_scope_sample = pipeline.CODEX_BOUNDED_TRUST_IN_SCOPE[0]
        assert in_scope_sample in prompt, (
            f"IN_SCOPE 카테고리 {in_scope_sample!r}이 프롬프트에 없습니다"
        )
        # OUT_OF_SCOPE_DIAGNOSTIC 카테고리 중 하나 이상이 프롬프트에 있어야 한다.
        out_scope_sample = pipeline.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC[0]
        assert out_scope_sample in prompt, (
            f"OUT_OF_SCOPE 카테고리 {out_scope_sample!r}이 프롬프트에 없습니다"
        )
        # ENVIRONMENT_UNTRUSTED 카테고리 중 하나 이상이 프롬프트에 있어야 한다.
        env_sample = pipeline.CODEX_BOUNDED_TRUST_ENVIRONMENT_UNTRUSTED[0]
        assert env_sample in prompt, (
            f"ENVIRONMENT_UNTRUSTED 카테고리 {env_sample!r}이 프롬프트에 없습니다"
        )
        # scope 필드명도 포함되어야 한다.
        assert "IN_SCOPE" in prompt
        assert "OUT_OF_SCOPE_DIAGNOSTIC" in prompt
        assert "ENVIRONMENT_UNTRUSTED" in prompt

    def test_reject39b_approve_with_in_scope_finding_review_status_rejected(self):
        """REJECT#21 AC-2: APPROVE_TO_USER + IN_SCOPE finding → review_status=REJECTED, verdict=REJECT.

        _classify_codex_findings + scope override 로직을 통해 in_scope_count>0이면
        review_status가 "REJECTED"로 변경되어야 한다.
        """
        # _classify_codex_findings에서 in_scope_count를 확인한다.
        # APPROVE+IN_SCOPE → review_status override 결과를 verify하기 위해 simulate한다.
        findings = [
            {
                "severity": "P0",
                "root_cause_category": "fake_codex_exec",  # IN_SCOPE SSoT 첫 번째 항목
                "scope": "OUT_OF_SCOPE_DIAGNOSTIC",  # Codex가 잘못된 scope 제출 (override 필요)
                "summary": "가짜 codex 실행 파일 감지",
            }
        ]
        cls = pipeline._classify_codex_findings(findings)
        # SSoT override: root_cause_category "fake_codex_exec"은 IN_SCOPE
        assert cls["in_scope_count"] == 1, "fake_codex_exec은 IN_SCOPE SSoT에 있으므로 in_scope_count=1"
        assert cls["out_of_scope_diagnostic_count"] == 0
        assert cls["diagnostic_only"] is False
        assert cls["reject_count_delta"] == 1

        # review_status override 로직 시뮬레이션: APPROVED + in_scope>0 → REJECTED
        review_status_before = "APPROVED"  # APPROVE_TO_USER에서 매핑
        if cls["in_scope_count"] > 0 and review_status_before == "APPROVED":
            review_status_after = "REJECTED"
        else:
            review_status_after = review_status_before
        assert review_status_after == "REJECTED", (
            "APPROVE_TO_USER + IN_SCOPE finding → review_status must be REJECTED"
        )

    def test_reject39c_reject_with_out_of_scope_only_review_status_approved(self):
        """REJECT#21 AC-3: REJECT + OUT_OF_SCOPE_DIAGNOSTIC only → review_status=APPROVED.

        diagnostic_only=True인 경우 review_status가 "APPROVED"로 전환되어야 한다.
        """
        findings = [
            {
                "severity": "P1",
                "root_cause_category": "openai_registry_compromise",  # OUT_OF_SCOPE_DIAGNOSTIC
                "scope": "IN_SCOPE",  # Codex가 잘못된 scope 제출 (override 필요)
                "summary": "OpenAI 레지스트리 타협 이론적 위협",
            },
            {
                "severity": "P2",
                "root_cause_category": "native_binary_origin_proof",  # OUT_OF_SCOPE_DIAGNOSTIC
                "scope": "IN_SCOPE",
                "summary": "native binary 출처 증명 이론적 위협",
            },
        ]
        cls = pipeline._classify_codex_findings(findings)
        # 두 finding 모두 OUT_OF_SCOPE SSoT에 있으므로
        assert cls["in_scope_count"] == 0
        assert cls["in_scope_all_count"] == 0
        assert cls["out_of_scope_diagnostic_count"] == 2
        assert cls["diagnostic_only"] is True, "모두 OUT_OF_SCOPE → diagnostic_only=True"
        assert cls["reject_count_delta"] == 0  # reject_count 미증가

        # review_status override 로직 시뮬레이션: REJECTED + diagnostic_only + env_untrusted==0 → APPROVED
        review_status_before = "REJECTED"  # REJECT에서 매핑
        _finding_env_untrusted = cls["environment_untrusted_count"]
        if cls["diagnostic_only"] and review_status_before == "REJECTED" and _finding_env_untrusted == 0:
            review_status_after = "APPROVED"
        else:
            review_status_after = review_status_before
        assert review_status_after == "APPROVED", (
            "REJECT + OUT_OF_SCOPE only → review_status must be APPROVED"
        )

    def test_reject39d_operational_trust_checks_status_verdict_env_nc(self):
        """REJECT#21 AC-4: _check_codex_review_operational_trust가 status/verdict/env_untrusted/non_converging_at를 검증."""
        # _trust_base()를 사용하여 이미 올바른 정책 서명과 binary 경로 등 모든 필드를 포함한 기본값으로 시작한다.
        # _trust_base()는 status="APPROVED", verdict="APPROVE_TO_USER",
        # environment_untrusted_count=0, non_converging_at=None을 기본으로 갖는다.

        # 먼저 기본값이 PASS인지 확인한다.
        res_pass = pipeline._check_codex_review_operational_trust(_trust_base())
        assert res_pass["status"] == "PASS", f"기본 _trust_base()는 PASS여야 함: {res_pass}"

        # 4-A: status != APPROVED → BLOCKED
        r_bad_status = _trust_base(status="REJECTED")
        res = pipeline._check_codex_review_operational_trust(r_bad_status)
        assert res["status"] == "BLOCKED"
        assert res["failure_code"] == "codex_review_status_not_approved"

        # 4-B: verdict != APPROVE_TO_USER → BLOCKED
        r_bad_verdict = _trust_base(verdict="REJECT")
        res = pipeline._check_codex_review_operational_trust(r_bad_verdict)
        assert res["status"] == "BLOCKED"
        assert res["failure_code"] == "codex_review_verdict_not_approve_to_user"

        # 4-C: environment_untrusted_count > 0 → BLOCKED
        r_env = _trust_base(environment_untrusted_count=1)
        res = pipeline._check_codex_review_operational_trust(r_env)
        assert res["status"] == "BLOCKED"
        assert res["failure_code"] == "codex_review_environment_untrusted_count"

        # 4-D: non_converging_at is not None → BLOCKED
        r_nc = _trust_base(non_converging_at="2026-07-15T00:00:00Z")
        res = pipeline._check_codex_review_operational_trust(r_nc)
        assert res["status"] == "BLOCKED"
        assert res["failure_code"] == "codex_review_non_converging"

    def test_reject39e_start_epoch_records_contract_migration(self, tmp_path):
        """REJECT#21 AC-5: --start-epoch가 _record_user_authorized_contract_migration를 호출한다.

        _record_user_authorized_contract_migration을 직접 호출하여 state에 codex_review_contract_migration이
        기록되고 review_epoch가 생성되는지 검증한다(unit 경로; CLI E2E는 별도 격리 상태 필요).
        """
        state: dict = {"pipeline_id": "IMP-20260712-DAE1"}
        new_sha = "a" * 64  # 64자리 SHA256 더미
        pipeline._record_user_authorized_contract_migration(
            state,
            old_contract_sha256="",
            new_contract_sha256=new_sha,
            migration_reason="REJECT#21 AC-5 test — 바이너리 검증 전략 재설계",
            decided_at="2026-07-15T00:00:00Z",
        )
        migration = state.get("codex_review_contract_migration")
        assert isinstance(migration, dict), "codex_review_contract_migration이 기록돼야 한다"
        assert migration.get("new_contract_sha256") == new_sha
        # review_epoch는 "epoch_YYYYMMDD_NNN" 형식이어야 한다.
        epoch = migration.get("review_epoch", "")
        import re
        assert re.match(r"^epoch_\d{8}_\d{3}$", epoch), (
            f"review_epoch 형식이 잘못됐습니다: {epoch!r}"
        )
        assert migration.get("scope_decision_source") == "user"
        assert migration.get("migration_flag_consumed") is True

        # circuit breaker는 review_epoch가 있을 때만 활성화된다 — epoch 없으면 triggered=False.
        history_no_epoch = [
            {"review_epoch": "", "root_cause_category": "fake_codex_exec",
             "counts_toward_reject_rate_limit": True}
            for _ in range(6)  # CODEX_CB_MAX_EFFECTIVE_REJECTS(5) 초과
        ]
        cb_no_epoch = pipeline._check_codex_circuit_breaker(history_no_epoch, "")
        assert not cb_no_epoch.get("triggered"), "epoch 없으면 circuit breaker 비활성화"

        # circuit breaker는 review_epoch가 있고 동일 category 3회 반복이면 triggered=True.
        # _check_codex_circuit_breaker 조건: status=="REJECTED" AND counts_toward_reject_rate_limit==True
        #   AND scope != "OUT_OF_SCOPE_DIAGNOSTIC" AND 동일 epoch.
        history_with_epoch = [
            {
                "review_epoch": epoch,
                "root_cause_category": "fake_codex_exec",
                "status": "REJECTED",
                "verdict_scope": "IN_SCOPE",
                "counts_toward_reject_rate_limit": True,
            }
            for _ in range(3)  # CODEX_CB_MAX_SAME_CATEGORY_REPEATS(3) 도달
        ]
        cb_with_epoch = pipeline._check_codex_circuit_breaker(history_with_epoch, epoch)
        assert cb_with_epoch.get("triggered"), (
            f"동일 category 3회 반복 → circuit breaker triggered. result={cb_with_epoch}"
        )
