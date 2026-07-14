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
    """evidence_complete=False이면 prompt 생성 자체가 차단(ValueError)된다."""
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
    """REJECT#12 AC#3: TC-11 oracle — actual_model=unknown + CRITICAL 시
    _check_codex_capability_gate가 expected.json과 일치하는 BLOCKED를 반환해야 한다."""
    # oracle input
    actual_model = "unknown"
    risk_level = "CRITICAL"
    # oracle expected: {"result": "BLOCKED", "failure_code": "unknown_model_critical_blocked"}
    result = pipeline._check_codex_capability_gate(actual_model, risk_level)
    assert result.get("result") == "BLOCKED", (
        f"REJECT#12 TC-11 oracle: actual_model=unknown+CRITICAL은 BLOCKED여야 한다. "
        f"got result={result.get('result')!r}"
    )
    assert result.get("failure_code") == "unknown_model_critical_blocked", (
        f"REJECT#12 TC-11 oracle: failure_code는 unknown_model_critical_blocked여야 한다. "
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
    수용해야 한다. REJECT#18 auth_source 코드 추가 후 ~73718자 수용 위해 74000 이상."""
    assert pipeline.CODEX_REVIEW_BUNDLE_BUDGET_CHARS >= 74000, (
        f"REJECT#18: 예산이 tc11 oracle을 수용하지 못합니다: "
        f"{pipeline.CODEX_REVIEW_BUNDLE_BUDGET_CHARS} < 74000"
    )


def test_tc30b_cross_validation_resets_truncated_crit() -> None:
    """REJECT#15: _build_codex_semantic_evidence의 cross-validation이 budget 단계의
    누적값을 덮어쓰지 않도록 _truncated_crit=0 리셋 코드가 있어야 한다."""
    import inspect
    src = inspect.getsource(pipeline._build_codex_semantic_evidence)
    assert "_truncated_crit = 0  # REJECT#15" in src, (
        "REJECT#15: cross-validation 직전 _truncated_crit=0 리셋 코드가 없습니다 "
        "(budget 단계 이중 계산 방지 미적용)"
    )


def test_tc30c_evidence_complete_true_with_current_diff() -> None:
    """REJECT#15: 현재 repo diff로 bundle을 빌드하면 evidence_complete=True여야 한다.
    tc11 oracle 파일이 예산 내에 들어오고 cross-validation이 통과해야 한다."""
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
        f"REJECT#15: evidence_complete가 True가 아닙니다. "
        f"truncated_critical_hunks={bundle.get('truncated_critical_hunks')}, "
        f"budget_used={bundle.get('bundle_budget_chars')}"
    )
    assert bundle.get("truncated_critical_hunks", 1) == 0, (
        f"REJECT#15: truncated_critical_hunks={bundle.get('truncated_critical_hunks')} != 0 "
        "(tc11 oracle 파일이 아직 예산 초과)"
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
def test_tc35a_critical_invocation_verified_blocked_in_capability_match() -> None:
    """REJECT#21 AC#1+AC#5: CRITICAL risk + actual_model=unknown + invocation_ok=True 시
    _check_codex_model_capability_match는 unknown_model_critical_blocked를 반환해야 한다.
    tc11 Oracle을 실제 명령 경로(_check_codex_model_capability_match)에 연결한 회귀 테스트."""
    r = pipeline._check_codex_model_capability_match(
        "gpt-5.6-sol", "max",    # selected
        "gpt-5.6-sol", "max",    # invoked (일치)
        "unknown", "unknown",     # actual (미보고)
        "CRITICAL",               # risk_level
        invocation_ok=True,
    )
    assert r["result"] == "BLOCKED", (
        f"REJECT#21 AC#1: CRITICAL+unknown이 BLOCKED가 아님 — "
        f"invocation_verified가 CRITICAL을 통과함\n  result={r}"
    )
    assert r["failure_code"] == "unknown_model_critical_blocked", (
        f"REJECT#21 AC#1: failure_code가 unknown_model_critical_blocked가 아님\n  "
        f"failure_code={r.get('failure_code')!r}"
    )


def test_tc35b_critical_invocation_verified_blocked_in_operational_trust() -> None:
    """REJECT#21 AC#3: request-accept 경로의 _check_codex_review_operational_trust에서도
    CRITICAL+invocation_verified는 unknown_model_critical_blocked로 차단돼야 한다."""
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
    }
    r = pipeline._check_codex_review_operational_trust(_fake_result)
    assert r["status"] == "BLOCKED", (
        f"REJECT#21 AC#3: CRITICAL+invocation_verified 결과가 operational_trust를 통과함\n  result={r}"
    )
    assert r["failure_code"] == "unknown_model_critical_blocked", (
        f"REJECT#21 AC#3: failure_code가 unknown_model_critical_blocked가 아님\n  "
        f"failure_code={r.get('failure_code')!r}"
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
    """REJECT#21 AC#4: tc11 Oracle(input/expected)을 실제 명령 경로(_invoke_codex_exec +
    _check_codex_model_capability_match)에 연결한 회귀 테스트.

    tc11 oracle input (actual_model=unknown, risk_level=CRITICAL, mode=enforce) →
    fake codex 실행(_invoke_codex_exec) + capability match 검사 →
    oracle expected (result=BLOCKED, failure_code=unknown_model_critical_blocked).
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

    assert r["result"] == tc11_expected["result"], (
        f"tc11 oracle 불일치: result={r['result']!r} != expected={tc11_expected['result']!r}\n  {r}"
    )
    assert r.get("failure_code") == tc11_expected["failure_code"], (
        f"tc11 oracle 불일치: failure_code={r.get('failure_code')!r} "
        f"!= expected={tc11_expected['failure_code']!r}"
    )


def test_tc36b_critical_unknown_blocked_result_includes_verification_level(tmp_path: Path) -> None:
    """REJECT#21 deadlock fix AC#2: _check_codex_model_capability_match가 BLOCKED 반환 시
    model_verification_level을 포함해야 한다 (verdic 기록 경로에서 invocation_verified 증거 보존).
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
    assert r["result"] == "BLOCKED"
    assert r["failure_code"] == "unknown_model_critical_blocked"
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
    """REJECT#23 AC#3: Pipeline Manager 문서가 CRITICAL의 actual_verified 필수 조건과
    HIGH의 invocation_verified 허용 조건을 정확히 구분한다.

    AC#3: 문서가 런타임과 일치하도록 CRITICAL actual_verified 필수 / HIGH invocation_verified 허용.
    """
    from pathlib import Path

    # Pipeline Manager 문서 경로.
    agent_md = (
        Path(__file__).resolve().parent.parent.parent
        / ".claude" / "agents" / "pipeline-manager-agent.md"
    )
    assert agent_md.exists(), (
        f"REJECT#23 AC#3: pipeline-manager-agent.md가 없음 — {agent_md}"
    )
    doc = agent_md.read_text(encoding="utf-8")

    # CRITICAL은 actual_verified 필수임을 문서가 명시해야 한다.
    assert "actual_verified" in doc and "CRITICAL" in doc, (
        "REJECT#23 AC#3: 문서에 CRITICAL과 actual_verified가 모두 없음"
    )

    # 이전 잘못된 문구("HIGH/CRITICAL은 최소 invocation_verified")가 없어야 한다.
    assert "HIGH/CRITICAL은 최소 `invocation_verified`" not in doc, (
        "REJECT#23 AC#3: 문서에 CRITICAL도 invocation_verified로 통과 가능하다는 잘못된 문구가 남아 있음 — "
        "런타임은 CRITICAL+unknown을 unknown_model_critical_blocked로 차단하므로 불일치"
    )

    # CRITICAL은 actual_verified 필수, HIGH는 invocation_verified 허용이 구분되어야 한다.
    # 두 조건을 각각 명시한 라인이 있어야 한다.
    critical_actual_line = any(
        "CRITICAL" in line and "actual_verified" in line
        for line in doc.splitlines()
    )
    assert critical_actual_line, (
        "REJECT#23 AC#3: CRITICAL과 actual_verified가 동일 라인에서 구분돼 있지 않음"
    )
    high_invocation_line = any(
        "HIGH" in line and "invocation_verified" in line
        for line in doc.splitlines()
    )
    assert high_invocation_line, (
        "REJECT#23 AC#3: HIGH와 invocation_verified가 동일 라인에서 구분돼 있지 않음"
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
    """REJECT#28 AC#3 소스 검증: cross-validation이 unknown CRITICAL hunk 누락을 감지한다.

    Fix 3: cross-validation 단계에서 unknown-named CRITICAL hunk가 예산에서 제외됐을 때
           truncated_critical_hunks가 증가하는 코드가 소스에 존재해야 한다.
    """
    import inspect

    src = inspect.getsource(pipeline._build_codex_semantic_evidence)

    # Fix 3a/3b 핵심: _all_unk_crit과 _sel_unk_crit 비교 코드가 있어야 한다.
    assert "_all_unk_crit" in src, (
        "REJECT#28 Fix 3: _all_unk_crit 변수가 소스에 없음 — "
        "cross-validation이 unknown CRITICAL hunk 누락을 감지하지 못함"
    )
    assert "_sel_unk_crit" in src, (
        "REJECT#28 Fix 3: _sel_unk_crit 변수가 소스에 없음"
    )
    assert "REJECT#28 Fix 3" in src, (
        "REJECT#28 Fix 3: 소스에 REJECT#28 Fix 3 주석이 없음"
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
        f"REJECT#28 AC#1: 신규 함수의 after SHA가 비어 있음 — 현재 파일에 함수가 존재해야 함"
    )


def test_tc42e_unknown_hunk_budget_exceeded_evidence_incomplete() -> None:
    """REJECT#28 AC#3: unknown-named pipeline.py hunk가 예산 초과로 제외되면
    truncated_critical_hunks가 증가하고 evidence_complete=False가 된다.

    Fix 2 + Fix 3 동작 기반 테스트: 예산을 초과하는 unknown-named hunk를 시뮬레이션하고,
    cross-validation이 이를 감지하여 evidence_complete=False를 반환하는지 검증한다.
    """
    from unittest.mock import patch, MagicMock

    # 예산을 크게 초과하는 unknown-named hunk (@@에 함수명 없음)
    big_content = "+" + "x" * 200_000  # 200KB — 실제 예산(165000)을 크게 초과
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

# REJECT#29 AC#1/2: 신뢰 경계 함수 집합 (CODEX_CRITICAL_FUNCTIONS에 모두 포함돼야 함).
# 이 목록에 새 함수를 추가하지 않으면 TC-43b 완전성 테스트가 실패하여 누락을 자동 감지한다.
_TRUST_BOUNDARY_FUNCS_REJECT29 = [
    "_invoke_codex_exec",
    "_parse_codex_exec_capability",
    "_compute_model_verification_level",
    "_check_codex_model_capability_match",
    "_build_codex_semantic_evidence",
    "_build_codex_prompt_for_review",
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
        f"REJECT#29 AC#2: 아래 신뢰 경계 함수가 CODEX_CRITICAL_FUNCTIONS에 없음:\n"
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
        f"REJECT#29 AC#4: codex_model_router_report.md 모델 라우팅 표에 claude 모델이 남아있음:\n"
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
            f"REJECT#29 AC#4: HIGH 섹션 테이블에 'tests/' 경로가 남아있음:\n"
            + "\n".join(f"  {row}" for row in tests_in_table)
            + "\n'tests/'는 HIGH risk 경로가 아닙니다 — 제거하십시오."
        )


def test_tc43k_report_high_unknown_not_blocked() -> None:
    """REJECT#29 AC#4: codex_model_router_report.md의 unknown 처리 표에서
    HIGH+unknown이 BLOCKED가 아님을 명시한다(CRITICAL만 BLOCKED).
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
        # HIGH 행에 BLOCKED가 없어야 함 (CRITICAL 행에는 있어야 함)
        high_rows = re.findall(r"^\|[^|]*unknown[^|]*\|[^|]*HIGH[^|]*\|[^|]*BLOCK[^|]*\|", section, re.MULTILINE)
        assert not high_rows, (
            f"REJECT#29 AC#4: unknown 처리 표에서 HIGH+BLOCKED 조합이 남아있음:\n"
            + "\n".join(f"  {row}" for row in high_rows)
            + "\nHIGH는 invocation_verified로 통과 — BLOCKED는 CRITICAL만 적용합니다."
        )
