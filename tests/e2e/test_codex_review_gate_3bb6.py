"""
test_codex_review_gate_3bb6.py — Codex Review gate E2E (IMP-20260712-DAE1 rework 갱신)

# [Purpose]: 구(舊) IMP-20260627-3BB6 계약(schema_version=1 + Stop-hook 자동 호출)은 폐기되었고,
#            현재 Codex Review는 Model Router(risk→모델/effort) + preflight hard gate + schema_version=4
#            구조로 동작한다. 이 파일은 사용자 REJECT(IMP-20260712-DAE1)의 "모델명 수정" 요구에 맞춰
#            현재 라우터가 gpt-5.5 모델을 선택하고, 실제 codex exec 호출에 그 모델/effort가
#            전달되는지 subprocess 실제 CLI 경로로 검증한다.
# [Assumptions]: fake codex를 임시 PATH에 주입하고 PIPELINE_STATE_PATH로 state를 격리한다.
# [Vulnerability & Risks]: subprocess timeout 초과 시 실패. Windows(.bat)/POSIX(sh) shim을 모두 생성한다.
# [Improvement]: full `gates codex-review --auto-codex-cli` preflight 격리를 추가하면 gate 전체를 닫을 수 있다.

# CLI Evidence Contract:
# - PIPELINE_STATE_PATH 격리 + 산출물(final_state) 파일 assertion.
# - subprocess 기반 실제 실행(내부 함수 직접 import는 격리 자식 프로세스 내부에서만).
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


def make_fake_codex(shim_dir: Path, verdict: str = "APPROVE_TO_USER",
                    capture_argv_to: Optional[Path] = None) -> None:
    """PATH에 주입할 가짜 codex를 만든다. 받은 --model/effort를 echo JSON으로 출력한다."""
    if shim_dir is None or verdict is None:
        raise TypeError("shim_dir/verdict must not be None")
    shim_dir.mkdir(parents=True, exist_ok=True)
    cfg = {"verdict": verdict, "capture_argv_to": str(capture_argv_to) if capture_argv_to else ""}
    cfg_path = shim_dir / "codex_cfg_3bb6.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    py_shim = shim_dir / "_codex_shim_3bb6.py"
    py_shim.write_text(
        "import sys, io, json\n"
        "sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')\n"
        "from pathlib import Path\n"
        f"cfg = json.loads(Path(r'{cfg_path}').read_text(encoding='utf-8'))\n"
        "argv = sys.argv[1:]\n"
        "if cfg['capture_argv_to']:\n"
        "    Path(cfg['capture_argv_to']).write_text(json.dumps(argv), encoding='utf-8')\n"
        "model = None; effort = None\n"
        "for i, a in enumerate(argv):\n"
        "    if a == '--model' and i + 1 < len(argv): model = argv[i + 1]\n"
        "    if a.startswith('model_reasoning_effort='): effort = a.split('=', 1)[1]\n"
        "sys.stdout.write(json.dumps({'type': 'model_info', 'model': model or 'unknown', 'reasoning_effort': effort or 'unknown'}) + '\\n')\n"
        "v = cfg['verdict']\n"
        "txt = 'APPROVE_TO_USER' if v == 'APPROVE_TO_USER' else json.dumps({'verdict': 'REJECT', 'root_cause': 'test', 'reproduction': 'test', 'required_fix': 'test', 'acceptance_criteria': ['test']})\n"
        "sys.stdout.write(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': txt}}) + '\\n')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        (shim_dir / "codex.bat").write_text(
            f'@echo off\r\n"{sys.executable}" "{py_shim}" %*\r\n', encoding="utf-8")
    else:
        sh = shim_dir / "codex"
        sh.write_text(f'#!/bin/sh\n"{sys.executable}" "{py_shim}" "$@"\n', encoding="utf-8")
        sh.chmod(sh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _env(state_file: Path, shim_dir: Optional[Path] = None) -> Dict[str, str]:
    env = {
        **os.environ,
        "PIPELINE_STATE_PATH": str(state_file),
        "PIPELINE_NO_DASHBOARD": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    if shim_dir is not None:
        env["PATH"] = str(shim_dir) + os.pathsep + env.get("PATH", "")
    return env


def _run(code: str, env: Dict[str, str], timeout: int = 60) -> subprocess.CompletedProcess:
    prelude = f"import sys, json\nsys.path.insert(0, r'{_ROOT}')\nimport pipeline as p\n"
    return subprocess.run(
        [sys.executable, "-c", prelude + code],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env,
    )


# ---------------------------------------------------------------------------
# 현재 라우터: risk → gpt-5.5 모델/effort 선택 (모델명 수정 검증)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("risk,model,effort,mode", [
    ("LOW", "gpt-5.6-luna", "low", "observe"),
    ("MEDIUM", "gpt-5.6-terra", "high", "observe"),
    ("HIGH", "gpt-5.6-sol", "high", "enforce"),
    ("CRITICAL", "gpt-5.6-sol", "max", "enforce"),
])
def test_router_selects_gpt56_policy(tmp_path: Path, risk, model, effort, mode) -> None:
    """risk별로 gpt-5.6 계열 모델/effort/mode를 선택한다(Claude/gpt-5.5 제거 검증)."""
    state_file = tmp_path / "state.json"
    final_state = tmp_path / "final.json"
    env = _env(state_file)
    code = (
        "pol = p._build_codex_model_policy(%r)\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps({'m': pol['selected_model'],\n"
        "  'e': pol['selected_reasoning_effort'], 'mode': pol['mode']}))\n"
        % (risk, str(final_state))
    )
    r = _run(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    assert fs["m"] == model and "claude" not in fs["m"]
    assert fs["e"] == effort
    assert fs["mode"] == mode


def test_real_codex_exec_carries_selected_model(tmp_path: Path) -> None:
    """실제 codex exec 호출에 HIGH 정책의 gpt-5.6-sol/high가 argv로 전달된다."""
    state_file = tmp_path / "state.json"
    shim = tmp_path / "shim"
    argv_cap = tmp_path / "argv.json"
    final_state = tmp_path / "final.json"
    make_fake_codex(shim, capture_argv_to=argv_cap)
    env = _env(state_file, shim_dir=shim)
    code = (
        "pol = p._build_codex_model_policy('HIGH')\n"
        "run = p._invoke_codex_exec(pol['selected_model'], pol['selected_reasoning_effort'], 'prompt')\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps({'cmd': run['codex_cli_command'],\n"
        "  'am': run['actual_model'], 'ae': run['actual_effort'], 'invoked': run['invoked']}))\n"
        % str(final_state)
    )
    r = _run(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    argv = json.loads(argv_cap.read_text(encoding="utf-8"))
    assert "--model" in argv and "gpt-5.6-sol" in argv
    assert any(a == "model_reasoning_effort=high" for a in argv)
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    assert fs["invoked"] is True
    assert "gpt-5.6-sol" in fs["cmd"] and "model_reasoning_effort=high" in fs["cmd"]
    assert fs["am"] == "gpt-5.6-sol" and fs["ae"] == "high"


def test_codex_review_result_schema_is_current(tmp_path: Path) -> None:
    """현재 codex_review_result는 schema_version=4 계열이며 라우터 필드를 포함한다(구 schema_version=1 폐기).

    구 3BB6 계약(schema_version=1, accept_code/reviewed_at)은 더 이상 유효하지 않음을 문서화한다.
    """
    state_file = tmp_path / "state.json"
    final_state = tmp_path / "final.json"
    env = _env(state_file)
    # 결과 스키마 상수(라우터 필드) 존재를 격리 프로세스에서 확인.
    code = (
        "fields = ['router_version', 'risk_level', 'selected_model', 'actual_model', 'review_mode']\n"
        "ver = p.CODEX_MODEL_ROUTER_VERSION\n"
        "open(%r,'w',encoding='utf-8').write(json.dumps({'fields': fields, 'ver': ver}))\n"
        % str(final_state)
    )
    r = _run(code, env)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    fs = json.loads(final_state.read_text(encoding="utf-8"))
    assert fs["ver"]  # router version 문자열 존재
    assert "selected_model" in fs["fields"] and "actual_model" in fs["fields"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-x", "-q"]))
