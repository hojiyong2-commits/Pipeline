"""
test_codex_model_router_e2e.py — IMP-20260712-DAE1 rework Codex Model Router subprocess E2E

# [Purpose]: 사용자 REJECT 7대 근본 문제 중 문제2(실제 codex exec 호출), 문제3(external_verdict
#            capability 우회 차단), 문제4(actual==selected capability 강제), 문제5(risk fail-closed),
#            문제6(subprocess E2E)을 실제 fake codex 실행 파일 + PIPELINE_STATE_PATH 격리로 검증한다.
# [Assumptions]: fake codex는 임시 PATH에 주입되며, 받은 --model / model_reasoning_effort 인자를
#            argv 캡처 파일에 기록하고 JSON({"model","reasoning_effort","verdict"})을 stdout으로 출력한다.
# [Vulnerability & Risks]: subprocess timeout 초과 시 실패. Windows/POSIX shim을 모두 생성한다.
# [Improvement]: full `gates codex-review --auto-codex-cli` 전체 preflight까지 격리하면 end-to-end 완전 검증 가능.

# CLI Evidence Contract:
# - 상태/효과 검증은 PIPELINE_STATE_PATH로 격리된 임시 경로에 산출물(final_state)을 기록하고 assert한다.
# - subprocess 기반 실제 실행만 사용한다(내부 함수 직접 import는 격리된 자식 프로세스 안에서만).
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_PY = _ROOT / "pipeline.py"


# ---------------------------------------------------------------------------
# fake codex 실행 파일 (PATH 주입)
# ---------------------------------------------------------------------------

def make_fake_codex(
    shim_dir: Path,
    verdict: str = "APPROVE_TO_USER",
    reason: str = "",
    override_model: Optional[str] = None,
    override_effort: Optional[str] = None,
    omit_model: bool = False,
    exit_code: int = 0,
    stderr_text: str = "",
    capture_argv_to: Optional[Path] = None,
) -> None:
    """PATH에 주입할 가짜 codex 실행 파일을 생성한다.

    `codex exec --model <M> -c model_reasoning_effort=<E> --json -` 호출 시:
      - argv를 capture_argv_to에 기록(모델/effort가 실제 전달됐는지 증거).
      - stdout에 JSON {"model": M, "reasoning_effort": E, "verdict": verdict} 출력.
        override_model/override_effort로 echo 값을 바꿔 actual!=selected 상황을 만들 수 있다.
        omit_model=True면 model 키를 빼서 actual_model=unknown 상황을 만든다.

    Args:
        shim_dir: shim을 둘 디렉토리.
        verdict: 출력할 verdict (APPROVE_TO_USER 또는 REJECT).
        reason: REJECT 사유(REJECT일 때 JSON reason).
        override_model: argv 대신 echo할 모델(actual!=selected 재현용).
        override_effort: argv 대신 echo할 effort.
        omit_model: True면 model 키 제거(actual_model=unknown).
        exit_code: codex 프로세스 종료 코드.
        stderr_text: stderr에 출력할 문자열(usage_limit/network 재현용).
        capture_argv_to: argv를 JSON list로 저장할 경로.
    Raises:
        TypeError: shim_dir 또는 verdict가 None인 경우.
    """
    if shim_dir is None or verdict is None:
        raise TypeError("shim_dir/verdict must not be None")
    shim_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "verdict": verdict,
        "reason": reason,
        "override_model": override_model,
        "override_effort": override_effort,
        "omit_model": omit_model,
        "exit_code": int(exit_code),
        "stderr_text": stderr_text,
        "capture_argv_to": str(capture_argv_to) if capture_argv_to else "",
    }
    cfg_path = shim_dir / "codex_cfg.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    py_shim = shim_dir / "_codex_router_shim.py"
    py_shim.write_text(
        "import sys, io, json\n"
        "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')\n"
        "sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')\n"
        "from pathlib import Path\n"
        f"cfg = json.loads(Path(r'{cfg_path}').read_text(encoding='utf-8'))\n"
        "argv = sys.argv[1:]\n"
        "if cfg['capture_argv_to']:\n"
        "    Path(cfg['capture_argv_to']).write_text(json.dumps(argv), encoding='utf-8')\n"
        "model = None; effort = None\n"
        "for i, a in enumerate(argv):\n"
        "    if a == '--model' and i + 1 < len(argv):\n"
        "        model = argv[i + 1]\n"
        "    if a.startswith('model_reasoning_effort='):\n"
        "        effort = a.split('=', 1)[1]\n"
        "    if a == '-c' and i + 1 < len(argv) and argv[i + 1].startswith('model_reasoning_effort='):\n"
        "        effort = argv[i + 1].split('=', 1)[1]\n"
        "if cfg['override_model'] is not None:\n"
        "    model = cfg['override_model']\n"
        "if cfg['override_effort'] is not None:\n"
        "    effort = cfg['override_effort']\n"
        "if not cfg['omit_model']:\n"
        "    m = model if model is not None else 'unknown'\n"
        "    e = effort if effort is not None else 'unknown'\n"
        "    sys.stdout.write(json.dumps({'type': 'model_info', 'model': m, 'reasoning_effort': e}) + '\\n')\n"
        "v = cfg['verdict']\n"
        "if v == 'APPROVE_TO_USER':\n"
        "    txt = 'APPROVE_TO_USER'\n"
        "else:\n"
        "    r = cfg.get('reason', 'test rejection') or 'test rejection'\n"
        # IMP-20260712-DAE1 MT-13 Finding 4: 구 4-필드 REJECT 포맷 제거 — pipeline.py
        # _parse_json_verdict가 요구하는 7-필드 findings[] 스키마(scope/severity/root_cause_category/
        # evidence/reproduction/required_fix/acceptance_criteria)로 shim 출력을 교체한다.
        "    txt = json.dumps({'schema_version': 6, 'verdict': 'REJECT',\n"
        "        'findings': [{'id': 'F-1', 'scope': 'IN_SCOPE', 'severity': 'P0',\n"
        "            'root_cause_category': 'test_reject', 'evidence': r, 'reproduction': r,\n"
        "            'required_fix': r, 'acceptance_criteria': [r]}],\n"
        "        'pipeline_id': 'TEST-PIPELINE', 'reviewed_at': '2026-01-01T00:00:00Z',\n"
        "        'model_used': 'gpt-5.6-sol', 'review_id': 'test-review-id'})\n"
        "sys.stdout.write(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': txt}}) + '\\n')\n"
        "if cfg['stderr_text']:\n"
        "    sys.stderr.write(cfg['stderr_text'])\n"
        "sys.exit(int(cfg['exit_code']))\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        bat = shim_dir / "codex.bat"
        bat.write_text(
            f'@echo off\r\n"{sys.executable}" "{py_shim}" %*\r\n',
            encoding="utf-8",
        )
    else:
        sh = shim_dir / "codex"
        sh.write_text(
            f'#!/bin/sh\n"{sys.executable}" "{py_shim}" "$@"\n',
            encoding="utf-8",
        )
        sh.chmod(sh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _harness_env(state_file: Path, shim_dir: Optional[Path] = None,
                 test_mode: bool = False) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 + (옵션) fake codex shim PATH 주입 env를 만든다."""
    env = {
        **os.environ,
        "PIPELINE_STATE_PATH": str(state_file),
        "PIPELINE_NO_DASHBOARD": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if test_mode:
        env["PIPELINE_TEST_MODE"] = "1"
    else:
        env.pop("PIPELINE_TEST_MODE", None)
    if shim_dir is not None:
        env["PATH"] = str(shim_dir) + os.pathsep + env.get("PATH", "")
    return env


def run_harness(code: str, env: Dict[str, str], timeout: int = 60) -> subprocess.CompletedProcess:
    """격리된 자식 프로세스에서 pipeline을 import하여 code를 실행한다(subprocess E2E)."""
    prelude = (
        "import sys, json\n"
        f"sys.path.insert(0, r'{_ROOT}')\n"
        "import pipeline as p\n"
    )
    return subprocess.run(
        [sys.executable, "-c", prelude + code],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env,
    )


# ---------------------------------------------------------------------------
# 문제2/6: 실제 codex exec 호출 + 모델/effort 인자 전달 검증
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("risk,exp_model,exp_effort", [
    ("MEDIUM", "gpt-5.6-terra", "high"),
    ("HIGH", "gpt-5.6-sol", "high"),
    ("CRITICAL", "gpt-5.6-sol", "max"),
])
def test_real_codex_exec_passes_model_and_effort(
    tmp_path: Path, risk: str, exp_model: str, exp_effort: str
) -> None:
    """정책이 선택한 모델/effort가 실제 `codex exec` argv로 전달되고 actual로 관측된다."""
    state_file = tmp_path / "state.json"
    shim = tmp_path / "shim"
    argv_cap = tmp_path / f"argv_{risk}.json"
    final_state = tmp_path / f"final_{risk}.json"
    make_fake_codex(shim, verdict="APPROVE_TO_USER", capture_argv_to=argv_cap)
    env = _harness_env(state_file, shim_dir=shim)
    code = (
        "pol = p._build_codex_model_policy(%r)\n"
        "run = p._invoke_codex_exec(pol['selected_model'], pol['selected_reasoning_effort'], 'review prompt')\n"
        "out = {'selected_model': pol['selected_model'],\n"
        "       'selected_effort': pol['selected_reasoning_effort'],\n"
        "       'actual_model': run['actual_model'],\n"
        "       'actual_effort': run['actual_effort'],\n"
        "       'codex_cli_command': run['codex_cli_command'],\n"
        "       'invoked': run['invoked'], 'exit_code': run['exit_code']}\n"
        "open(%r, 'w', encoding='utf-8').write(json.dumps(out))\n"
        % (risk, str(final_state))
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"harness failed\n{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    # 정책이 기대 모델/effort를 선택했는지
    assert fs["selected_model"] == exp_model
    assert fs["selected_effort"] == exp_effort
    # 실제 argv에 --model/effort가 전달됐는지 (증거 파일)
    # CLI alias: "max" -> "xhigh" (_CLI_EFFORT_ALIAS 적용 후 argv에 전달됨)
    _CLI_EFFORT_ALIAS = {"max": "xhigh"}
    cli_effort = _CLI_EFFORT_ALIAS.get(exp_effort, exp_effort)
    argv = json.loads(argv_cap.read_text(encoding="utf-8"))
    assert "--model" in argv and exp_model in argv, f"argv missing model: {argv}"
    assert any(a == f"model_reasoning_effort={cli_effort}" for a in argv), f"argv missing effort: {argv}"
    # sanitized 명령 기록 — CLI alias 값으로 기록된다
    assert exp_model in fs["codex_cli_command"]
    assert f"model_reasoning_effort={cli_effort}" in fs["codex_cli_command"]
    # CLI가 실제로 그 모델/effort를 사용했다고 관측 — canonicalization 후 selected_effort와 일치
    assert fs["actual_model"] == exp_model
    assert fs["actual_effort"] == exp_effort
    assert fs["invoked"] is True and fs["exit_code"] == 0


# ---------------------------------------------------------------------------
# 문제4: capability — actual != selected → BLOCKED
# ---------------------------------------------------------------------------

def test_capability_actual_reported_mismatch_blocked(tmp_path: Path) -> None:
    """CLI가 selected와 다른 모델을 보고하면 capability match BLOCKED (actual_model_mismatch).

    IMP-20260712-DAE1 REJECT#3: invoked==selected(gpt-5.6-sol)이지만 CLI가 gpt-4o를 보고하면
    actual!=selected → BLOCKED.
    """
    state_file = tmp_path / "state.json"
    shim = tmp_path / "shim"
    final_state = tmp_path / "final.json"
    make_fake_codex(shim, override_model="gpt-4o", override_effort="high")
    env = _harness_env(state_file, shim_dir=shim)
    code = (
        "pol = p._build_codex_model_policy('HIGH')\n"
        "run = p._invoke_codex_exec(pol['selected_model'], pol['selected_reasoning_effort'], 'x')\n"
        "chk = p._check_codex_model_capability_match(pol['selected_model'], pol['selected_reasoning_effort'],\n"
        "       run['invoked_model'], run['invoked_effort'],\n"
        "       run['actual_model'], run['actual_effort'], 'HIGH', invocation_ok=(run['exit_code']==0))\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps({'actual': run['actual_model'], 'chk': chk}))\n"
        % str(final_state)
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    assert fs["actual"] == "gpt-4o"
    assert fs["chk"]["result"] == "BLOCKED"
    assert fs["chk"]["failure_code"] == "actual_model_mismatch"


def test_capability_unreported_model_exit0_invocation_verified(tmp_path: Path) -> None:
    """exit 0인데 CLI가 모델을 보고하지 않으면 invocation_verified로 통과한다(요구4).

    실제 codex exec NDJSON은 model/effort 필드를 보고하지 않는다. _invoke_codex_exec는
    actual을 unknown으로 남기고(허위 기록 금지), capability match는 invoked==selected +
    exit 0 성공이므로 invocation_verified로 판정하여 HIGH에서 OK가 된다.
    """
    state_file = tmp_path / "state.json"
    shim = tmp_path / "shim"
    final_state = tmp_path / "final.json"
    make_fake_codex(shim, omit_model=True)  # model 키 없음 → NDJSON 미보고 재현
    env = _harness_env(state_file, shim_dir=shim)
    code = (
        "pol = p._build_codex_model_policy('HIGH')\n"
        "run = p._invoke_codex_exec(pol['selected_model'], pol['selected_reasoning_effort'], 'x')\n"
        "chk = p._check_codex_model_capability_match(pol['selected_model'], pol['selected_reasoning_effort'],\n"
        "       run['invoked_model'], run['invoked_effort'],\n"
        "       run['actual_model'], run['actual_effort'], 'HIGH', invocation_ok=(run['exit_code']==0))\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps({'actual': run['actual_model'], 'chk': chk}))\n"
        % str(final_state)
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    assert fs["actual"] == "unknown"  # 허위 기록 금지: 미보고는 unknown 유지
    assert fs["chk"]["result"] == "OK"
    assert fs["chk"]["model_verification_level"] == "invocation_verified"


def test_capability_unverified_critical_blocked(tmp_path: Path) -> None:
    """unknown model + invocation 실패(exit≠0) + CRITICAL → BLOCKED (unverified, fail-closed)."""
    state_file = tmp_path / "state.json"
    final_state = tmp_path / "final.json"
    env = _harness_env(state_file)
    code = (
        "chk = p._check_codex_model_capability_match('gpt-5.6-sol','max','gpt-5.6-sol','max',\n"
        "       'unknown','unknown','CRITICAL', invocation_ok=False)\n"
        "leg = p._check_codex_capability_gate('unknown','CRITICAL')\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps({'chk': chk, 'leg': leg}))\n"
        % str(final_state)
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    assert fs["chk"]["result"] == "BLOCKED"
    assert fs["chk"]["failure_code"] == "model_verification_unverified"
    assert fs["leg"]["result"] == "BLOCKED"


# ---------------------------------------------------------------------------
# 문제3: external_verdict → acceptance_eligible=false (운영), test-mode에서만 허용
# ---------------------------------------------------------------------------

def test_external_verdict_acceptance_eligible_always_false(tmp_path: Path) -> None:
    """external_verdict → acceptance_eligible=false (요구5: PIPELINE_TEST_MODE 우회 제거).

    이제 어떤 환경에서도 manual/external 주입은 승인 자격이 없다. 운영 신뢰 게이트가
    verdict_source in (codex_cli, verified_cache)만 허용하므로 external은 항상 차단된다.
    """
    state_file = tmp_path / "state.json"
    final_state = tmp_path / "final.json"
    env = _harness_env(state_file)
    code = (
        "chk = p._check_codex_review_operational_trust({\n"
        "  'verdict_source': 'external_verdict', 'acceptance_eligible': True,\n"
        "  'router_version': '2.0.0', 'risk_level': 'HIGH',\n"
        "  'model_policy_signature': 'sig', 'codex_cli_command': 'N/A (external verdict)',\n"
        "  'selected_model': 'gpt-5.6-sol', 'selected_reasoning_effort': 'high',\n"
        "  'invoked_model': 'gpt-5.6-sol', 'invoked_effort': 'high',\n"
        "  'actual_model': 'unknown', 'actual_effort': 'unknown',\n"
        "  'model_verification_level': 'invocation_verified', 'auth_source': 'chatgpt'})\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps(chk))\n"
        % str(final_state)
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    assert fs["status"] == "BLOCKED"
    assert fs["failure_code"] == "codex_review_untrusted_verdict_source"


# ---------------------------------------------------------------------------
# 문제6: 모델/effort/정책 변경 → cache miss (policy signature 반영)
# ---------------------------------------------------------------------------

def test_cache_key_changes_on_policy_change(tmp_path: Path) -> None:
    """같은 contract+bundle이라도 정책(모델/effort)이 바뀌면 cache_key가 달라져 miss가 된다."""
    state_file = tmp_path / "state.json"
    final_state = tmp_path / "final.json"
    env = _harness_env(state_file)
    code = (
        "c, b = 'contract_sha', 'bundle_sha'\n"
        "k_low = p._codex_cache_key(c, b, p._codex_policy_signature(p._build_codex_model_policy('LOW')))\n"
        "k_high = p._codex_cache_key(c, b, p._codex_policy_signature(p._build_codex_model_policy('HIGH')))\n"
        "k_crit = p._codex_cache_key(c, b, p._codex_policy_signature(p._build_codex_model_policy('CRITICAL')))\n"
        "k_none = p._codex_cache_key(c, b)\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps({'low':k_low,'high':k_high,'crit':k_crit,'none':k_none}))\n"
        % str(final_state)
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    # 정책별로 모두 달라야 한다(정책 변경 → cache miss).
    assert len({fs["low"], fs["high"], fs["crit"]}) == 3, f"정책별 cache_key가 구분되지 않음: {fs}"
    # 기본(policy 없음) 키는 정책 포함 키와 달라야 한다(하위 호환 경로 구분).
    assert fs["none"] not in {fs["low"], fs["high"], fs["crit"]}


# ---------------------------------------------------------------------------
# 문제6: CLI usage limit / network / timeout → ERROR (REJECT 아님, reject_count 미증가)
# ---------------------------------------------------------------------------

def test_cli_usage_limit_is_error_not_reject(tmp_path: Path) -> None:
    """CLI usage limit 에러 → ERROR (reject_count 증가 금지)."""
    state_file = tmp_path / "state.json"
    shim = tmp_path / "shim"
    final_state = tmp_path / "final.json"
    make_fake_codex(shim, exit_code=1, stderr_text="you have hit your usage limit", omit_model=True)
    env = _harness_env(state_file, shim_dir=shim)
    code = (
        "run = p._invoke_codex_exec('gpt-5.5','high','x')\n"
        "cls = p._run_codex_cli_review(run['exit_code'], run['stdout'], run['stderr'])\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps({'status': cls['status'],\n"
        "  'error_type': cls['error_type'], 'retryable': cls['error_retryable'],\n"
        "  'has_reject_count': 'reject_count' in cls}))\n"
        % str(final_state)
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    assert fs["status"] == "ERROR"
    assert fs["error_type"] == "usage_limit"
    assert fs["retryable"] is True
    assert fs["has_reject_count"] is False  # ERROR는 reject 카운터와 무관


def test_cli_network_and_timeout_are_error(tmp_path: Path) -> None:
    """CLI network/timeout 에러 → ERROR (reject_count 증가 없음)."""
    state_file = tmp_path / "state.json"
    final_state = tmp_path / "final.json"
    env = _harness_env(state_file)
    code = (
        "net = p._run_codex_cli_review(1, '', 'connection reset by peer network error')\n"
        "tmo = p._run_codex_cli_review(-1, '', '')\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps({\n"
        "  'net_status': net['status'], 'net_type': net['error_type'],\n"
        "  'tmo_status': tmo['status'], 'tmo_type': tmo['error_type']}))\n"
        % str(final_state)
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    assert fs["net_status"] == "ERROR" and fs["net_type"] == "network"
    assert fs["tmo_status"] == "ERROR" and fs["tmo_type"] == "timeout"


# ---------------------------------------------------------------------------
# 문제5: risk classifier fail-closed (빈 changeset) + unknown risk → policy BLOCKED
# ---------------------------------------------------------------------------

def test_risk_fail_closed_and_tests_inheritance(tmp_path: Path) -> None:
    """빈 changeset→BLOCKED, tests-only→LOW(risk 상속 안함), unknown risk_level→policy BLOCKED."""
    state_file = tmp_path / "state.json"
    final_state = tmp_path / "final.json"
    env = _harness_env(state_file)
    code = (
        "empty = p._classify_codex_review_risk([], [])\n"
        "tests_only = p._classify_codex_review_risk(['tests/e2e/test_x.py'], [])\n"
        "tests_plus = p._classify_codex_review_risk(['tests/e2e/test_x.py','pipeline.py'], [])\n"
        "router_crit = p._classify_codex_review_risk(['pipeline.py'], ['_build_codex_model_policy'])\n"
        "unk_pol = p._build_codex_model_policy('WEIRD')\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps({\n"
        "  'empty': empty['risk_level'], 'empty_blocked': empty.get('blocked'),\n"
        "  'tests_only': tests_only['risk_level'], 'tests_plus': tests_plus['risk_level'],\n"
        "  'router_crit': router_crit['risk_level'], 'unk_pol': unk_pol}))\n"
        % str(final_state)
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    assert fs["empty"] == "BLOCKED" and fs["empty_blocked"] is True
    assert fs["tests_only"] == "LOW"       # tests-only는 risk를 올리지 않음
    assert fs["tests_plus"] == "HIGH"      # 함께 변경된 pipeline.py의 risk 상속
    assert fs["router_crit"] == "CRITICAL"  # 라우터 함수 변경 → CRITICAL
    assert fs["unk_pol"]["result"] == "BLOCKED"
    assert fs["unk_pol"]["failure_code"] == "unknown_risk_level_blocked"


# ---------------------------------------------------------------------------
# 문제1: 모델 정책이 gpt-5.5인지 (Claude 아님)
# ---------------------------------------------------------------------------

def test_model_policies_are_gpt56(tmp_path: Path) -> None:
    """LOW=luna/low, MEDIUM=terra/high, HIGH=sol/high, CRITICAL=sol/max (모두 gpt-5.6 계열)."""
    state_file = tmp_path / "state.json"
    final_state = tmp_path / "final.json"
    env = _harness_env(state_file)
    code = (
        "out = {r: (p._build_codex_model_policy(r)['selected_model'],\n"
        "           p._build_codex_model_policy(r)['selected_reasoning_effort'])\n"
        "       for r in ('LOW','MEDIUM','HIGH','CRITICAL')}\n"
        "out['allowed'] = sorted(p.CODEX_ALLOWED_MODELS)\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps(out))\n"
        % str(final_state)
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    assert fs["LOW"] == ["gpt-5.6-luna", "low"]
    assert fs["MEDIUM"] == ["gpt-5.6-terra", "high"]
    assert fs["HIGH"] == ["gpt-5.6-sol", "high"]
    assert fs["CRITICAL"] == ["gpt-5.6-sol", "max"]
    for m in fs["LOW"][0], fs["MEDIUM"][0], fs["HIGH"][0], fs["CRITICAL"][0]:
        assert m.startswith("gpt-5.6-"), f"gpt-5.6 계열이 아님: {m}"
        assert "claude" not in m and "5.5" not in m, f"구 모델이 남아 있음: {m}"
    assert fs["allowed"] == ["gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra"]


# ---------------------------------------------------------------------------
# 문제7: 승인 요청 출력 중복 방지 (approval_request_message 1회 + JSON 단독 채널)
# ---------------------------------------------------------------------------

def test_approval_request_message_single_occurrence(tmp_path: Path) -> None:
    """approval_request_message는 4요소를 정확히 1회씩만 포함하고 JSON에 alias 중복 키가 없다."""
    state_file = tmp_path / "state.json"
    final_state = tmp_path / "final.json"
    env = _harness_env(state_file)
    code = (
        "out = p._build_approval_request_output('IMP-20260712-DAE1', 'https://github.com/o/r/pull/1')\n"
        "msg = out['approval_request_message']\n"
        "counts = {k: msg.count(k) for k in ('사용자 승인 요청', '\\nPR:', '승인 코드:', 'CODEX 검토 필요')}\n"
        "forbidden = [k for k in ('approval_display', 'message_file') if k in out]\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps({'counts': counts,\n"
        "  'forbidden': forbidden, 'keys': sorted(out.keys())}))\n"
        % str(final_state)
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    # 각 필수 문구가 정확히 1회 (중복이면 count != 1 → BLOCKED 대상).
    for k, c in fs["counts"].items():
        assert c == 1, f"'{k}' 중복/누락: count={c}"
    # 이중 relay 경로가 되는 alias 키가 JSON에 없어야 한다.
    assert fs["forbidden"] == [], f"금지 alias 키 존재: {fs['forbidden']}"
    # user-facing 본문 채널은 approval_request_message 하나뿐.
    assert "approval_request_message" in fs["keys"]


def test_approval_message_stdout_json_only_channel(tmp_path: Path) -> None:
    """machine-readable 조립 결과를 JSON 1줄로 emit 시 승인 문구가 stdout에만 있고 stderr엔 없다."""
    state_file = tmp_path / "state.json"
    env = _harness_env(state_file)
    # 격리 프로세스: JSON을 stdout 1줄로만 출력하고 stderr에는 진단만 쓴다(중복 emit 금지 규칙 재현).
    code = (
        "out = p._build_approval_request_output('IMP-20260712-DAE1', 'https://github.com/o/r/pull/1')\n"
        "sys.stderr.write('diagnostic: building approval output\\n')\n"
        "sys.stdout.write(json.dumps(out))\n"
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    # stdout은 정확히 1개의 JSON object.
    parsed = json.loads(r.stdout.strip())
    arm = parsed["approval_request_message"]
    assert arm.count("승인 코드:") == 1
    # 승인 본문(및 승인 코드)이 stderr로 새어나가면 안 된다(이중 중계 방지).
    assert "승인 코드:" not in r.stderr
    assert "ACCEPT-IMP-20260712-DAE1" not in r.stderr


# ---------------------------------------------------------------------------
# rework#2 요구4: request-accept 신뢰 게이트 _check_codex_review_operational_trust
# ---------------------------------------------------------------------------

_CODEX_CLI_CMD = (
    "codex exec --model gpt-5.6-sol -c model_reasoning_effort=high "
    "--sandbox read-only --ephemeral --json -C <repo-root> -"
)


def _trust_result(**overrides) -> dict:
    """운영 신뢰 게이트 통과용 기준 codex_review_result dict를 만들고 override를 적용한다.

    IMP-20260712-DAE1 REJECT#3: gpt-5.6 + invoked/verification_level/auth_source 반영.
    기본은 HIGH + invocation_verified(actual 미보고) 통과 케이스.
    """
    base = {
        "verdict_source": "codex_cli",
        "acceptance_eligible": True,
        "router_version": "2.0.0",
        "risk_level": "HIGH",
        "model_policy_signature": "HIGH:gpt-5.6-sol:high:enforce",
        "codex_cli_command": _CODEX_CLI_CMD,
        "selected_model": "gpt-5.6-sol",
        "selected_reasoning_effort": "high",
        "invoked_model": "gpt-5.6-sol",
        "invoked_effort": "high",
        "actual_model": "unknown",
        "actual_effort": "unknown",
        "model_verification_level": "invocation_verified",
        "auth_source": "chatgpt",
        # IMP-20260712-DAE1 REJECT#16: codex_cli/verified_cache에 binary 신뢰 증거 필수.
        "codex_binary_path": "/usr/local/bin/codex",
        "codex_binary_sha256": "e" * 64,
        # REJECT#LATEST: native/vendor binary 신뢰 증거 필수 (test-only dummy).
        "codex_native_binary_path": "/usr/local/lib/codex/vendor/codex",
        "codex_native_binary_sha256": "a" * 64,
    }
    base.update(overrides)
    return base


def _run_trust(tmp_path: Path, result: dict) -> dict:
    """격리 subprocess에서 _check_codex_review_operational_trust(result)를 실행하고 결과 반환."""
    state_file = tmp_path / "state.json"
    final_state = tmp_path / "trust_out.json"
    env = _harness_env(state_file)
    # IMP-20260712-DAE1 MT-13 Finding 2: 강화된 운영 신뢰 게이트는 schema_version/contract_sha256을
    #   검증한다. 실제 codex_review_result는 이 값을 포함하므로, 테스트 result에 없으면 실제 계약값을
    #   setdefault로 주입한다(명시적으로 다른 값을 준 테스트는 그 값을 유지 → mismatch 검증 가능).
    code = (
        "res = %r\n" % (json.dumps(result),) +
        "_r = json.loads(res)\n"
        "_r.setdefault('schema_version', p.CODEX_REVIEW_RESULT_SCHEMA_VERSION)\n"
        "_r.setdefault('contract_sha256', p._compute_codex_contract_sha256())\n"
        "chk = p._check_codex_review_operational_trust(_r)\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps(chk))\n" % str(final_state)
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    return json.loads(final_state.read_text(encoding="utf-8"))


def test_operational_trust_passes_real_codex_cli(tmp_path: Path) -> None:
    """실제 codex_cli 실행 결과(selected==actual, HIGH actual!=unknown) → PASS."""
    out = _run_trust(tmp_path, _trust_result())
    assert out["status"] == "PASS", out


def test_operational_trust_passes_verified_cache(tmp_path: Path) -> None:
    """verified_cache(원 codex_cli 승계, cache-hit 마커 명령) → PASS."""
    out = _run_trust(tmp_path, _trust_result(
        verdict_source="verified_cache", codex_cli_command="N/A (cache hit)",
        risk_level="MEDIUM", model_policy_signature="MEDIUM:gpt-5.6-terra:high:observe",
        selected_model="gpt-5.6-terra", invoked_model="gpt-5.6-terra",
    ))
    assert out["status"] == "PASS", out


def test_operational_trust_blocks_external_verdict(tmp_path: Path) -> None:
    """맨몸 --verdict 주입(external_verdict) → BLOCKED (untrusted verdict source)."""
    out = _run_trust(tmp_path, _trust_result(
        verdict_source="external_verdict", acceptance_eligible=False,
        codex_cli_command="N/A (external verdict)", actual_model="unknown",
        actual_effort="unknown",
    ))
    assert out["status"] == "BLOCKED"
    assert out["failure_code"] == "codex_review_untrusted_verdict_source"


def test_operational_trust_blocks_external_cli_injection(tmp_path: Path) -> None:
    """--codex-cli-* 주입(external_cli_injection) → BLOCKED (untrusted verdict source)."""
    out = _run_trust(tmp_path, _trust_result(
        verdict_source="external_cli_injection", acceptance_eligible=False,
        codex_cli_command="external CLI injection (--codex-cli-*)",
    ))
    assert out["status"] == "BLOCKED"
    assert out["failure_code"] == "codex_review_untrusted_verdict_source"


def test_operational_trust_blocks_not_eligible(tmp_path: Path) -> None:
    """acceptance_eligible=false → BLOCKED (codex_cli여도 자격 없음)."""
    out = _run_trust(tmp_path, _trust_result(acceptance_eligible=False))
    assert out["status"] == "BLOCKED"
    assert out["failure_code"] == "codex_review_not_acceptance_eligible"


def test_operational_trust_blocks_unverified_high(tmp_path: Path) -> None:
    """HIGH + model_verification_level=unverified → BLOCKED (fail-closed).

    IMP-20260712-DAE1 REJECT#3: actual 미보고라도 invocation_verified면 통과하지만,
    verification_level이 unverified면 HIGH/CRITICAL에서 차단된다.
    """
    out = _run_trust(tmp_path, _trust_result(model_verification_level="unverified"))
    assert out["status"] == "BLOCKED"
    assert out["failure_code"] == "codex_review_unverified_high_critical"


def test_operational_trust_blocks_model_mismatch(tmp_path: Path) -> None:
    """selected_model != invoked_model → BLOCKED (요구9.7)."""
    out = _run_trust(tmp_path, _trust_result(invoked_model="gpt-5.6-luna"))
    assert out["status"] == "BLOCKED"
    assert out["failure_code"] == "codex_review_model_mismatch"


def test_operational_trust_blocks_actual_reported_mismatch(tmp_path: Path) -> None:
    """CLI actual 보고했는데 selected 불일치 → BLOCKED (요구4)."""
    out = _run_trust(tmp_path, _trust_result(
        actual_model="gpt-4o", actual_effort="high", model_verification_level="unverified",
    ))
    assert out["status"] == "BLOCKED"
    assert out["failure_code"] == "codex_review_actual_model_mismatch"


def test_operational_trust_blocks_effort_mismatch(tmp_path: Path) -> None:
    """selected_effort != invoked_effort → BLOCKED (요구9.8)."""
    out = _run_trust(tmp_path, _trust_result(invoked_effort="low"))
    assert out["status"] == "BLOCKED"
    assert out["failure_code"] == "codex_review_effort_mismatch"


def test_operational_trust_blocks_missing_router_version(tmp_path: Path) -> None:
    """router_version 누락 → BLOCKED."""
    out = _run_trust(tmp_path, _trust_result(router_version=""))
    assert out["status"] == "BLOCKED"
    assert out["failure_code"] == "codex_review_router_version_missing"


def test_operational_trust_blocks_missing_policy_signature(tmp_path: Path) -> None:
    """model_policy_signature 누락 → BLOCKED."""
    out = _run_trust(tmp_path, _trust_result(model_policy_signature=""))
    assert out["status"] == "BLOCKED"
    assert out["failure_code"] == "codex_review_model_policy_signature_missing"


def test_operational_trust_blocks_placeholder_cli_command(tmp_path: Path) -> None:
    """codex_cli(실행) 경로인데 codex_cli_command가 placeholder → BLOCKED."""
    out = _run_trust(tmp_path, _trust_result(codex_cli_command="N/A (external verdict)"))
    assert out["status"] == "BLOCKED"
    assert out["failure_code"] == "codex_review_cli_command_placeholder"


# ---------------------------------------------------------------------------
# rework#2 요구3: verdict_source 결정 + 운영/테스트 모드 acceptance_eligible 게이팅
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source,trusted", [
    ("external_verdict", False),
    ("external_cli_injection", False),
    ("codex_cli", True),
    ("verified_cache", True),
])
def test_acceptance_eligible_ops_gating(
    tmp_path: Path, source: str, trusted: bool
) -> None:
    """요구5/9: codex_cli/verified_cache만 신뢰. external/manual 주입은 PIPELINE_TEST_MODE와 무관하게 차단."""
    state_file = tmp_path / "state.json"
    final_state = tmp_path / f"elig_{source}.json"
    env = _harness_env(state_file)
    _cmd = (
        "codex exec --model gpt-5.6-sol -c model_reasoning_effort=high "
        "--sandbox read-only --ephemeral --json -C <repo-root> -"
    ) if trusted else "N/A (external verdict)"
    # 정책 시그니처: "<risk>:<model>:<effort>:<mode>" 형식 (HIGH → enforce 모드)
    _policy_sig = "HIGH:gpt-5.6-sol:high:enforce"
    # REJECT#16: codex_cli/verified_cache에는 binary 신뢰 증거 필수 (테스트용 dummy 값).
    _bin_path = "/usr/local/bin/codex" if trusted else ""
    _bin_sha = "f" * 64 if trusted else ""
    # REJECT#LATEST: native/vendor binary 신뢰 증거 필수 (test-only dummy).
    _nat_path = "/usr/local/lib/codex/vendor/codex" if trusted else ""
    _nat_sha = "a" * 64 if trusted else ""
    code = (
        "res = {'verdict_source': %r, 'acceptance_eligible': True,\n" % source +
        "  'router_version': '2.0.0', 'risk_level': 'HIGH',\n"
        "  'model_policy_signature': %r, 'codex_cli_command': %r,\n" % (_policy_sig, _cmd) +
        "  'selected_model': 'gpt-5.6-sol', 'selected_reasoning_effort': 'high',\n"
        "  'invoked_model': 'gpt-5.6-sol', 'invoked_effort': 'high',\n"
        "  'actual_model': 'unknown', 'actual_effort': 'unknown',\n"
        "  'model_verification_level': 'invocation_verified', 'auth_source': 'chatgpt',\n"
        "  'codex_native_binary_path': %r, 'codex_native_binary_sha256': %r,\n" % (_nat_path, _nat_sha) +
        "  'codex_binary_path': %r, 'codex_binary_sha256': %r}\n" % (_bin_path, _bin_sha) +
        # IMP-20260712-DAE1 MT-13 Finding 2: 강화된 게이트가 요구하는 schema_version/contract_sha256 주입.
        "res.setdefault('schema_version', p.CODEX_REVIEW_RESULT_SCHEMA_VERSION)\n"
        "res.setdefault('contract_sha256', p._compute_codex_contract_sha256())\n"
        "chk = p._check_codex_review_operational_trust(res)\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps(chk))\n" % str(final_state)
    )
    r = run_harness(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    assert (fs["status"] == "PASS") is trusted, f"source={source} -> {fs}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-x", "-q"]))
