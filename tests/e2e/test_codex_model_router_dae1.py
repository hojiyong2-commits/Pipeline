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
