"""IMP-20260531-4AC2: CI run ID 브랜치 필터링 E2E 테스트.

TC-BR1 ~ TC-BR5 — 함수 단위 헬퍼 테스트 (mock 기반).
TC-CLI-1, TC-CLI-3, TC-STALE-CLI — Real CLI Path E2E 테스트 (IMP-20260525-6FAC 준수).

[함수 단위 테스트] TC-BR1 ~ TC-BR5:
  TC-BR1 (normal): 현재 브랜치에서 _get_pr_branch_ci_run_id 직접 호출 — 브랜치 run 반환
  TC-BR2 (edge):   detached HEAD (branch='HEAD') → None 반환
  TC-BR3 (edge):   다른 브랜치 run이 globally latest여도 현재 브랜치 run 선택
  TC-BR4 (edge):   gh CLI FileNotFoundError → None 반환 (예외 전파 없음)
  TC-BR5 (edge):   gh CLI 빈 출력 → None 반환

[Real CLI Path E2E 테스트] TC-CLI-1, TC-CLI-3, TC-STALE-CLI (IMP-20260525-6FAC):
  TC-CLI-1 (normal): gates request-accept CLI가 acceptance_request.json에
                     브랜치 run ID(11111111)를 기록하는지 검증
  TC-CLI-3 (edge):   phase-attestation 브랜치 run이 globally latest여도
                     현재 브랜치 run이 acceptance_request.json에 저장되는지 검증
  TC-STALE-CLI (edge): acceptance_request.json의 run ID와 현재 run ID가 다를 때
                        gates accept가 stale_run_id BLOCKED를 반환하는지 검증

격리 전략 (함수 단위):
  - PIPELINE_STATE_PATH 환경 변수로 state 파일을 tmp_path 안에 격리
  - subprocess.run mock으로 fake gh CLI 응답 구현 (Windows PATH 우선순위 우회)
  - 전역 pipeline_state.json을 수정하지 않음
  - IMP-20260525-6FAC: 함수 단위 테스트라 final_state 없음
    (stateless 함수이므로 반환값 assertion이 final_state 역할)

격리 전략 (Real CLI Path E2E):
  - PIPELINE_STATE_PATH 환경 변수 + cwd=tmp_path 로 완전 격리
  - tmp_path/bin/ 에 fake git.cmd / fake gh.cmd 생성 → PATH 최우선 추가
  - shutil.which 기반 pipeline.py가 PATH fake bins를 올바르게 선택함
  - final_state assertion: acceptance_request.json 내용 직접 검증
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

# ─── 헬퍼 ──────────────────────────────────────────────────────────────────────

PIPELINE_PY = str(Path(__file__).resolve().parents[2] / "pipeline.py")
PIPELINE_DIR = Path(PIPELINE_PY).parent

# fake gh CLI가 반환할 run ID
IMPL_BRANCH = "impl/IMP-20260531-4AC2"
PHASE_ATTEST_BRANCH = "phase-attestation/IMP-20260531-4AC2-pm"
IMPL_BRANCH_RUN_ID = "11111111"
GLOBAL_LATEST_RUN_ID = "99999999"


def _import_pipeline():
    """pipeline 모듈을 동적으로 임포트."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", PIPELINE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_completed_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    """fake subprocess.CompletedProcess 생성."""
    result = subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
    return result


# ─── TC-BR1 (normal) ────────────────────────────────────────────────────────────

def test_tc_br1_normal_branch_run_id(tmp_path: Path) -> None:
    """TC-BR1: 현재 브랜치 기반 run ID 반환 — 정상 케이스.

    fake gh: --branch 옵션 있으면 IMPL_BRANCH_RUN_ID(11111111) 반환.
    _get_pr_branch_ci_run_id(branch=IMPL_BRANCH)가 11111111을 반환해야 한다.
    """
    pipeline = _import_pipeline()

    def fake_subprocess_run(cmd, **kwargs):
        # gh run list --branch <branch> → 11111111
        if "gh" in cmd[0] and "--branch" in cmd:
            return _make_completed_process(returncode=0, stdout=IMPL_BRANCH_RUN_ID + "\n")
        # gh run list (no --branch) → 99999999
        if "gh" in cmd[0]:
            return _make_completed_process(returncode=0, stdout=GLOBAL_LATEST_RUN_ID + "\n")
        return _make_completed_process(returncode=0, stdout="")

    with patch.object(pipeline.subprocess, "run", side_effect=fake_subprocess_run):
        result = pipeline._get_pr_branch_ci_run_id(branch=IMPL_BRANCH)

    assert result == IMPL_BRANCH_RUN_ID, (
        f"TC-BR1 FAIL: expected '{IMPL_BRANCH_RUN_ID}' but got {repr(result)}"
    )
    assert result != GLOBAL_LATEST_RUN_ID, (
        f"TC-BR1 FAIL: should NOT return global latest run ID '{GLOBAL_LATEST_RUN_ID}'"
    )


# ─── TC-BR2 (edge) ─────────────────────────────────────────────────────────────

def test_tc_br2_edge_detached_head(tmp_path: Path) -> None:
    """TC-BR2: detached HEAD 상태에서 None 반환.

    branch='HEAD'를 명시적으로 전달하면 gh CLI 호출 없이 None 반환해야 한다.
    """
    pipeline = _import_pipeline()

    # gh CLI가 호출되면 AssertionError
    def should_not_be_called(cmd, **kwargs):
        assert False, f"TC-BR2 FAIL: gh CLI should not be called, but got cmd={cmd}"

    with patch.object(pipeline.subprocess, "run", side_effect=should_not_be_called):
        result = pipeline._get_pr_branch_ci_run_id(branch="HEAD")

    assert result is None, (
        f"TC-BR2 FAIL: detached HEAD should return None, got {repr(result)}"
    )


# ─── TC-BR3 (edge) ─────────────────────────────────────────────────────────────

def test_tc_br3_edge_branch_filter_vs_global(tmp_path: Path) -> None:
    """TC-BR3: phase-attestation run이 globally latest여도 현재 브랜치 run 선택.

    fake gh: --branch 없이 호출 → GLOBAL_LATEST_RUN_ID(99999999)
             --branch impl/... 호출  → IMPL_BRANCH_RUN_ID(11111111)

    _get_pr_branch_ci_run_id(branch=IMPL_BRANCH)는 반드시 11111111을 반환해야 한다.
    이것이 이 IMP의 핵심 검증 케이스이다.
    """
    pipeline = _import_pipeline()

    call_log: List[List[str]] = []

    def fake_subprocess_run(cmd, **kwargs):
        call_log.append(list(cmd))
        if "gh" in cmd[0] and "--branch" in cmd:
            return _make_completed_process(returncode=0, stdout=IMPL_BRANCH_RUN_ID + "\n")
        if "gh" in cmd[0]:
            return _make_completed_process(returncode=0, stdout=GLOBAL_LATEST_RUN_ID + "\n")
        return _make_completed_process(returncode=0, stdout="")

    with patch.object(pipeline.subprocess, "run", side_effect=fake_subprocess_run):
        result = pipeline._get_pr_branch_ci_run_id(branch=IMPL_BRANCH)

    assert result == IMPL_BRANCH_RUN_ID, (
        f"TC-BR3 FAIL: expected '{IMPL_BRANCH_RUN_ID}' but got {repr(result)}\n"
        f"calls: {call_log}"
    )
    assert result != GLOBAL_LATEST_RUN_ID, (
        f"TC-BR3 FAIL: 전역 최신 run({GLOBAL_LATEST_RUN_ID})이 반환됨 — 브랜치 필터 미작동\n"
        f"calls: {call_log}"
    )
    # gh에 --branch 옵션이 포함된 호출이 있어야 함
    branch_filtered_calls = [c for c in call_log if "gh" in c[0] and "--branch" in c]
    assert branch_filtered_calls, (
        f"TC-BR3 FAIL: gh CLI에 --branch 옵션 없이 호출됨\ncalls: {call_log}"
    )


# ─── TC-BR4 (edge) ─────────────────────────────────────────────────────────────

def test_tc_br4_edge_gh_file_not_found(tmp_path: Path) -> None:
    """TC-BR4: gh CLI FileNotFoundError → None 반환 (예외 전파 없음).

    gh CLI가 설치되어 있지 않은 환경에서도 파이프라인이 중단되지 않아야 한다.
    """
    pipeline = _import_pipeline()

    def fake_subprocess_run(cmd, **kwargs):
        if "gh" in str(cmd[0]):
            raise FileNotFoundError("gh not found")
        return _make_completed_process(returncode=0, stdout="")

    with patch.object(pipeline.subprocess, "run", side_effect=fake_subprocess_run):
        result = pipeline._get_pr_branch_ci_run_id(branch=IMPL_BRANCH)

    assert result is None, (
        f"TC-BR4 FAIL: FileNotFoundError should return None, got {repr(result)}"
    )


# ─── TC-BR5 (edge) ─────────────────────────────────────────────────────────────

def test_tc_br5_edge_gh_empty_output(tmp_path: Path) -> None:
    """TC-BR5: gh CLI 빈 출력 → None 반환.

    gh run list가 결과 없이 빈 문자열을 반환할 때 None을 반환해야 한다.
    """
    pipeline = _import_pipeline()

    def fake_subprocess_run(cmd, **kwargs):
        if "gh" in str(cmd[0]):
            return _make_completed_process(returncode=0, stdout="")
        return _make_completed_process(returncode=0, stdout="")

    with patch.object(pipeline.subprocess, "run", side_effect=fake_subprocess_run):
        result = pipeline._get_pr_branch_ci_run_id(branch=IMPL_BRANCH)

    assert result is None, (
        f"TC-BR5 FAIL: empty output should return None, got {repr(result)}"
    )


# ─── CLI E2E 공통 헬퍼 ─────────────────────────────────────────────────────────

DUMMY_CLI_PIPELINE_ID = "IMP-20260531-4AC2"


def _write_harness_state(state_path: Path, pipeline_id: str) -> None:
    """Real CLI Path E2E 테스트용 격리 파이프라인 state 작성."""
    state = {
        "schema_version": 3,
        "pipeline_id": pipeline_id,
        "type": "IMP",
        "description": "CI run ID 브랜치 필터 테스트",
        "created_at": "2026-05-31T00:00:00Z",
        "updated_at": "2026-05-31T00:00:00Z",
        "pipeline_started_at": "2026-05-31T00:00:00Z",
        "pipeline_completed_at": None,
        "acceptance_requested_at": None,
        "acceptance_recorded_at": None,
        "total_elapsed_seconds": None,
        "current_phase": "harness",
        "blocked": False,
        "blocked_reason": None,
        "terminal_state": None,
        "harness_fail_count": 0,
        "agent_runs": {},
        "phase_attestations": {"enabled": False},
        "codex_bootstrap_exception": True,
        "phases": {
            "pm": {"status": "DONE"},
            "dev": {"status": "DONE"},
            "qa": {"status": "PASS"},
            "sec": {"status": "SKIP"},
            "build": {"status": "DONE"},
            "harness": {"status": "PENDING"},
            "architect": {"status": "PENDING"},
        },
        "external_gates": {
            "technical": {"status": "PASS"},
            "oracle": {"status": "PASS"},
            "acceptance": {"status": "PENDING"},
            "github_ci": {"status": "PASS"},
        },
        "event_log": [],
        "events": [],
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# IMP-20260612-E12D MT-2: conftest의 autouse fake gh fixture 제거에 따라,
# request-accept의 PR body readiness 검사(IMP-20260611-A716 Bug 1)를 통과하려면
# PATH 기반 fake gh가 `gh pr view --json body --jq .body`에도 완전한 PR body를
# 반환해야 한다. 기존 run ID 브랜치 필터 동작(run list --branch)은 그대로 보존한다.
_FAKE_GH_PR_BODY_4AC2 = (
    "## 작업 요약\n자동 테스트 픽스처 PR body\n\n"
    "## 사용자가 확인할 결과물\n결과물 경로: N/A (테스트)\n\n"
    "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 성공\n\n"
    "## 중요한 선택과 트레이드오프\nN/A (테스트 픽스처)\n\n"
    "## 검증\n모든 게이트 PASS\n"
)


def _setup_fake_bins(tmp_path: Path, stale_mode: bool = False) -> Dict[str, str]:
    """Real CLI Path E2E용 fake git/gh 바이너리 생성 및 PATH 환경 반환.

    Windows에서 subprocess.run(['git', ...])가 PATH의 git.cmd보다 git.exe를
    우선 선택하는 PATHEXT 문제를 pipeline.py의 shutil.which 사용으로 해결한다.
    shutil.which는 PATH 순서를 올바르게 따르므로 tmp_path/bin/ 앞에 추가하면
    fake git.cmd / fake gh.cmd가 우선 선택된다.

    IMP-20260612-E12D MT-2: fake gh가 `pr view --json body --jq .body`에
    완전한 PR body를 반환하도록 보강한다(autouse fake gh fixture 제거 대응).
    run list --branch 기반 run ID 필터 동작은 그대로 유지한다.

    Args:
        tmp_path: pytest tmp_path 픽스처
        stale_mode: True이면 gh run list가 항상 22222222 반환 (stale 시나리오)

    Returns:
        {"PATH": "<bin_dir>;<original_PATH>"} 형태의 env dict
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "fake_git.py").write_text(
        "import sys\nprint('impl/IMP-20260531-4AC2')\nsys.exit(0)\n",
        encoding="utf-8",
    )
    (bin_dir / "git.cmd").write_text(
        '@echo off\npython "%~dp0fake_git.py" %*\nexit /b %errorlevel%\n',
        encoding="utf-8",
    )
    body_json = json.dumps(_FAKE_GH_PR_BODY_4AC2)
    # run list run ID: stale_mode이면 항상 22222222, 아니면 --branch면 11111111 / 무분기면 99999999
    run_id_expr = (
        "'22222222'"
        if stale_mode
        else "('11111111' if '--branch' in args else '99999999')"
    )
    fake_gh_src = (
        "import sys, io, json\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        f"BODY = {body_json}\n"
        "args = sys.argv[1:]\n"
        # run ID 브랜치 필터 (핵심 검증 대상): gh run list --branch <b> --json databaseId --jq .[0].databaseId
        # 이 호출은 --jq .[0].databaseId를 포함하므로 PR body 분기보다 먼저 처리해야 한다.
        'if "run" in args and "list" in args:\n'
        f"    print({run_id_expr}); sys.exit(0)\n"
        # PR body 조회: gh pr view --json body --jq .body
        'if "--jq" in args:\n'
        '    jq_idx = args.index("--jq"); jq = args[jq_idx+1] if jq_idx+1 < len(args) else ""\n'
        '    if jq == ".body":\n'
        '        sys.stdout.write(BODY)\n'
        '        if not BODY.endswith("\\n"):\n'
        '            sys.stdout.write("\\n")\n'
        "        sys.exit(0)\n"
        '    elif "[.files" in jq or jq.startswith(".[0]"):\n'
        '        print("[]"); sys.exit(0)\n'
        '    elif ".headSha" in jq or ".databaseId" in jq:\n'
        '        print(""); sys.exit(0)\n'
        # 기타 pr/run list 형태는 빈 배열
        'if "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        # 그 외 pr view 등 — 빈 PR 메타데이터 반환
        "print(json.dumps({\n"
        '    "body": BODY, "number": 1,\n'
        '    "headRefOid": "",\n'
        '    "isDraft": False, "state": "OPEN", "files": [],\n'
        '    "url": "https://github.com/test/repo/pull/1",\n'
        "}))\n"
        "sys.exit(0)\n"
    )
    (bin_dir / "fake_gh.py").write_text(fake_gh_src, encoding="utf-8")
    (bin_dir / "gh.cmd").write_text(
        '@echo off\npython "%~dp0fake_gh.py" %*\nexit /b %errorlevel%\n',
        encoding="utf-8",
    )

    # IMP-20260612-E12D MT-2: _get_pr_body_text()는 _build_gh_cmd_prefix()를 거쳐
    # 기본적으로 bare ["gh"]를 호출한다. Windows에서 bare ["gh"]는 PATHEXT 우선순위(.EXE > .CMD)
    # 때문에 실제 gh.exe를 선택해 fake gh.cmd를 가리지 못한다. (run ID 필터는 shutil.which를 쓰므로
    # 영향 없음.) 따라서 PR body 조회만 PIPELINE_GH_EXECUTABLE(Python fake gh)로 라우팅하여
    # 완전한 PR body를 반환시킨다. run list 호출은 여전히 PATH 기반 gh.cmd가 처리하므로
    # 브랜치 필터(11111111 vs 99999999/22222222) 검증 의도가 그대로 유지된다.
    pr_body_gh = bin_dir / "pr_body_gh.py"
    pr_body_gh.write_text(fake_gh_src, encoding="utf-8")

    original_path = os.environ.get("PATH", "")
    new_path = str(bin_dir) + os.pathsep + original_path
    return {
        "PATH": new_path,
        "PIPELINE_GH_EXECUTABLE": str(pr_body_gh),
    }


def _run_cli(
    args: List[str],
    state_path: Path,
    cwd: Path,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """PIPELINE_STATE_PATH 격리 + cwd 격리 + fake PATH 환경에서 pipeline.py 실행."""
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, PIPELINE_PY] + args,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(cwd),
    )


# ─── TC-CLI-1 (normal): request-accept가 브랜치 run ID를 기록 ──────────────────

def test_tc_cli1_request_accept_stores_branch_run_id(tmp_path: Path) -> None:
    """TC-CLI-1 (normal): gates request-accept --evidence <file>이 acceptance_request.json에
    github_ci_run_id = '11111111' (브랜치 필터 결과)을 기록한다.

    fake PATH bins: git.cmd → impl/IMP-20260531-4AC2, gh.cmd --branch → 11111111
    shutil.which가 fake bin을 우선 선택하므로 실제 git/gh 없이도 동작.

    final_state: acceptance_request.json.github_ci_run_id == '11111111'
                 acceptance_request.json.github_ci_run_id != '99999999'
    격리: PIPELINE_STATE_PATH + cwd=tmp_path + fake PATH bins
    """
    state_path = tmp_path / "pipeline_state.json"
    evidence_path = tmp_path / "evidence.txt"
    req_path = tmp_path / "acceptance_request.json"

    _write_harness_state(state_path, DUMMY_CLI_PIPELINE_ID)
    evidence_path.write_text("test evidence", encoding="utf-8")

    fake_env = _setup_fake_bins(tmp_path, stale_mode=False)

    result = _run_cli(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=fake_env,
    )

    assert result.returncode == 0, (
        f"TC-CLI-1 FAIL: exit={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert req_path.exists(), "TC-CLI-1 FAIL: acceptance_request.json 생성 안 됨"

    # final_state assertion
    final_req = json.loads(req_path.read_text(encoding="utf-8"))
    assert final_req.get("github_ci_run_id") == IMPL_BRANCH_RUN_ID, (
        f"TC-CLI-1 FAIL: github_ci_run_id={final_req.get('github_ci_run_id')!r}, "
        f"expected={IMPL_BRANCH_RUN_ID!r}"
    )
    assert final_req.get("github_ci_run_id") != GLOBAL_LATEST_RUN_ID, (
        f"TC-CLI-1 FAIL: 전역 최신 run ID({GLOBAL_LATEST_RUN_ID})가 저장됨 — 브랜치 필터 미작동"
    )


# ─── TC-CLI-3 (edge): phase-attestation run이 globally latest여도 브랜치 run 선택 ─

def test_tc_cli3_branch_filter_not_global_latest(tmp_path: Path) -> None:
    """TC-CLI-3 (edge): 전역 최신 run이 99999999이더라도
    gh.cmd --branch → 11111111 (브랜치 필터 결과)이
    acceptance_request.json에 기록된다.

    이것이 이 IMP의 핵심 검증 케이스 (CLI path).

    fake PATH bins: git.cmd → impl/IMP-20260531-4AC2
                   gh.cmd --branch → 11111111, gh.cmd 무분기 → 99999999
    shutil.which가 fake bin을 우선 선택.

    final_state: acceptance_request.json.github_ci_run_id == '11111111'
                 (99999999가 아니라 브랜치 필터 결과가 저장됨)
    격리: PIPELINE_STATE_PATH + cwd=tmp_path + fake PATH bins
    """
    state_path = tmp_path / "pipeline_state.json"
    evidence_path = tmp_path / "evidence.txt"
    req_path = tmp_path / "acceptance_request.json"

    _write_harness_state(state_path, DUMMY_CLI_PIPELINE_ID)
    evidence_path.write_text("test evidence", encoding="utf-8")
    fake_env = _setup_fake_bins(tmp_path, stale_mode=False)

    result = _run_cli(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=fake_env,
    )

    assert result.returncode == 0, (
        f"TC-CLI-3 FAIL: exit={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert req_path.exists(), "TC-CLI-3 FAIL: acceptance_request.json 생성 안 됨"

    # final_state: 브랜치 필터링 결과(11111111)가 저장되어야 함
    final_req = json.loads(req_path.read_text(encoding="utf-8"))
    ci_run = final_req.get("github_ci_run_id")
    assert ci_run == IMPL_BRANCH_RUN_ID, (
        f"TC-CLI-3 FAIL: 브랜치 필터 결과({IMPL_BRANCH_RUN_ID}) 대신 {ci_run!r} 기록됨"
    )
    assert ci_run != GLOBAL_LATEST_RUN_ID, (
        f"TC-CLI-3 FAIL: 전역 최신({GLOBAL_LATEST_RUN_ID})이 기록됨 — "
        f"phase-attestation run 오염 방지 실패"
    )


# ─── TC-STALE-CLI (edge): stale run ID → gates accept BLOCKED ─────────────────

def test_tc_stale_cli_gates_accept_blocks_on_stale_run_id(tmp_path: Path) -> None:
    """TC-STALE-CLI (edge): acceptance_request.json에 github_ci_run_id=11111111이 저장된 후,
    현재 run이 22222222 (다른 값)이면 gates accept가 stale_run_id BLOCKED를 반환한다.

    단계:
      1. acceptance_request.json에 github_ci_run_id=11111111 직접 기록
      2. fake PATH bins: gh.cmd → 항상 22222222 반환 (stale 시나리오)
      3. gates accept 실행 → stale_run_id BLOCKED (exit != 0)

    final_state: exit code != 0, stdout/stderr에 'stale_run_id' 또는 'run' 포함
    격리: PIPELINE_STATE_PATH + cwd=tmp_path + fake PATH bins (stale_mode=True)
    """
    state_path = tmp_path / "pipeline_state.json"
    evidence_path = tmp_path / "evidence.txt"
    req_path = tmp_path / "acceptance_request.json"

    _write_harness_state(state_path, DUMMY_CLI_PIPELINE_ID)
    evidence_path.write_text("test evidence", encoding="utf-8")

    # acceptance_request.json에 github_ci_run_id=11111111 직접 기록 (request-accept 시점)
    nonce = "TESTNNCE"  # 8자 더미 nonce (A-Z2-7: E는 유효)
    req_data = {
        "schema_version": 1,
        "pipeline_id": DUMMY_CLI_PIPELINE_ID,
        "request_id": "cli_stale_test",
        "nonce": nonce,
        "created_at": "2026-05-31T10:00:00Z",
        "pr_url": "",
        "pr_head_sha": "",
        "github_ci_run_id": IMPL_BRANCH_RUN_ID,  # "11111111" 저장
        "evidence": str(evidence_path),
        "evidence_sha256": None,
        "evidence_url": None,
        "status": "PENDING",
    }
    req_path.write_text(
        json.dumps(req_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # fake PATH bins: gh.cmd → 22222222 (stale_mode=True)
    fake_env = _setup_fake_bins(tmp_path, stale_mode=True)

    # gates accept 실행: ACCEPT 코드로 시도 → stale_run_id BLOCKED 예상
    accept_code = f"ACCEPT-{DUMMY_CLI_PIPELINE_ID}-{nonce}"
    result = _run_cli(
        [
            "gates", "accept",
            "--result", "ACCEPT",
            "--evidence", str(evidence_path),
            "--acceptance-code", accept_code,
        ],
        state_path,
        cwd=tmp_path,
        extra_env=fake_env,
    )

    # final_state: stale run ID로 인해 BLOCKED (exit code != 0)
    assert result.returncode != 0, (
        f"TC-STALE-CLI FAIL: stale run ID임에도 accept이 성공함 (exit=0)\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    combined_output = result.stdout + result.stderr
    assert "stale_run_id" in combined_output or "run" in combined_output.lower(), (
        f"TC-STALE-CLI FAIL: stale_run_id 오류 메시지 없음\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
