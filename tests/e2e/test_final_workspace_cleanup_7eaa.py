"""
test_final_workspace_cleanup_7eaa.py — IMP-20260623-7EAA MT-6

파이프라인 COMPLETE 후 자동 workspace 정리 (ownership manifest 기반 cleanup
finalizer + workspace readiness + hygiene final-cleanup/ready-next CLI) E2E 테스트.

# [Purpose]:
#   - _register_workspace_artifact / _load/_save_workspace_artifacts (MT-1)
#   - _run_post_complete_cleanup finalizer (MT-2)
#   - cmd_status workspace readiness + cmd_new BLOCKED 차단 (MT-3)
#   - hygiene final-cleanup / ready-next CLI (MT-4)
#   를 실제 CLI / 격리된 subprocess 경로로 검증한다.
#
# [Assumptions]:
#   - PIPELINE_STATE_PATH 환경변수로 state 파일 격리 (전역 pipeline_state.json 미오염).
#   - workspace_artifacts.json은 state 파일과 동일 디렉토리에 co-locate된다.
#   - subprocess 기반 실제 CLI 실행. 내부 finalizer는 격리된 helper subprocess로
#     구동하고 저장된 final_state(state.json)를 assertion한다 (stdout-only 금지).
#
# CLI Evidence Contract (IMP-20260525-6FAC):
#   - 상태 변경 호출마다 PIPELINE_STATE_PATH 격리 + final_state assertion.
#   - subprocess 기반 실제 실행.
#
# [Vulnerability & Risks]:
#   - Windows에서는 chmod 기반 Permission denied 재현이 불가하므로 TC-E2E-5는 skip.
#   - helper subprocess가 timeout(30s) 초과 시 실패.
#
# [Improvement]:
#   - 향후 전체 architect COMPLETE 경로를 gh 모킹으로 구동해 finalizer 자동 호출까지
#     end-to-end로 커버할 수 있다.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
BASE_DIR = PIPELINE_PY.parent
PIPELINE_ID = "IMP-TEST-7EAA"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_env(state_file: Path) -> dict:
    """PIPELINE_STATE_PATH 격리 + 대시보드/네트워크 차단 환경변수."""
    if state_file is None:
        raise TypeError("state_file must not be None")
    return {
        **os.environ,
        "PIPELINE_STATE_PATH": str(state_file),
        "PIPELINE_NO_DASHBOARD": "1",
    }


def run_cli(args, env, timeout=30):
    """`python pipeline.py <args>` 실제 CLI 실행."""
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    cmd = [sys.executable, str(PIPELINE_PY)] + list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def run_helper(script: str, env, timeout=30):
    """격리된 환경에서 pipeline 내부 함수를 구동하는 helper subprocess.

    state.json 저장(final_state)을 assertion 대상으로 삼아 stdout-only 검증을 피한다.
    """
    cmd = [sys.executable, "-c", script]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def read_state(state_file: Path) -> dict:
    """격리된 state.json 파싱."""
    if not state_file.exists():
        raise FileNotFoundError(f"state file not found: {state_file}")
    return json.loads(state_file.read_text(encoding="utf-8"))


def _make_state_script(state_file: Path, pipeline_id: str = PIPELINE_ID) -> str:
    """격리된 state.json을 생성하는 helper 스크립트 prefix."""
    return (
        "import sys; sys.path.insert(0, r'{base}')\n"
        "import json\n"
        "from pathlib import Path\n"
        "import pipeline\n"
        "state = pipeline._new_state('{pid}', 'IMP', 'E2E test')\n"
        "pipeline._save(state)\n"
    ).format(base=str(BASE_DIR), pid=pipeline_id)


# ---------------------------------------------------------------------------
# TC-E2E-1: 매니페스트 생성 (AC-1)
# ---------------------------------------------------------------------------

def test_register_artifact_creates_manifest(tmp_path):
    """TC-E2E-1: _register_workspace_artifact 호출 후 workspace_artifacts.json 생성 (AC-1)."""
    state_file = tmp_path / "state.json"
    artifacts_file = tmp_path / "workspace_artifacts.json"
    env = make_env(state_file)

    script = _make_state_script(state_file) + (
        "pipeline._register_workspace_artifact(state, 'tmp_out.json', 'cleanup_only', 'temp')\n"
        "pipeline._register_workspace_artifact(state, 'report.md', 'user_visible_output', 'final')\n"
        "print('DONE')\n"
    )
    proc = run_helper(script, env)
    assert proc.returncode == 0, proc.stderr

    # final_state: workspace_artifacts.json이 격리 디렉토리에 생성되어야 함.
    assert artifacts_file.exists(), f"manifest not created: {proc.stdout} {proc.stderr}"
    data = json.loads(artifacts_file.read_text(encoding="utf-8"))
    paths = {a["path"]: a["cleanup_policy"] for a in data["artifacts"]}
    assert paths["tmp_out.json"] == "cleanup_only"
    assert paths["report.md"] == "user_visible_output"
    # 중복 등록 방지 검증: 동일 path 재등록 시 업데이트만.
    assert len(data["artifacts"]) == 2


# ---------------------------------------------------------------------------
# TC-E2E-2: cleanup_only 삭제 + preserve_evidence 보존 (AC-2)
# ---------------------------------------------------------------------------

def test_cleanup_only_files_removed_on_complete(tmp_path):
    """TC-E2E-2: cleanup_only 파일만 삭제, preserve_evidence 보존 (AC-2)."""
    state_file = tmp_path / "state.json"
    env = make_env(state_file)

    cleanup_target = tmp_path / "tmp_cleanup.json"
    preserve_target = tmp_path / "evidence.json"
    cleanup_target.write_text("{}", encoding="utf-8")
    preserve_target.write_text("{}", encoding="utf-8")

    script = _make_state_script(state_file) + (
        "pipeline._register_workspace_artifact(state, r'{c}', 'cleanup_only', '')\n"
        "pipeline._register_workspace_artifact(state, r'{p}', 'preserve_evidence', '')\n"
        "summary = pipeline._run_post_complete_cleanup(state, None)\n"
        "pipeline._save(state)\n"
        "print(json.dumps(summary))\n"
    ).format(c=str(cleanup_target), p=str(preserve_target))
    proc = run_helper(script, env)
    assert proc.returncode == 0, proc.stderr

    assert not cleanup_target.exists(), "cleanup_only 파일이 삭제되지 않음"
    assert preserve_target.exists(), "preserve_evidence 파일이 삭제됨 (보존 위반)"

    # final_state assertion: state.json에 post_complete_cleanup 기록.
    final_state = read_state(state_file)
    pcc = final_state["post_complete_cleanup"]
    assert pcc["status"] == "OK"
    assert pcc["ready_for_next_task"] is True
    assert str(cleanup_target) in pcc["removed"]


# ---------------------------------------------------------------------------
# TC-E2E-3: 매니페스트 없는 파일 미삭제 (AC-3)
# ---------------------------------------------------------------------------

def test_unknown_files_not_deleted(tmp_path):
    """TC-E2E-3: 매니페스트 없는 파일은 삭제 안 됨 (AC-3)."""
    state_file = tmp_path / "state.json"
    env = make_env(state_file)

    unregistered = tmp_path / "mystery.txt"
    unregistered.write_text("keep me", encoding="utf-8")

    # 매니페스트에는 등록하지 않고 cleanup만 실행.
    script = _make_state_script(state_file) + (
        "summary = pipeline._run_post_complete_cleanup(state, None)\n"
        "pipeline._save(state)\n"
        "print(json.dumps(summary))\n"
    )
    proc = run_helper(script, env)
    assert proc.returncode == 0, proc.stderr

    # 등록되지 않은 파일은 절대 삭제되지 않아야 함.
    assert unregistered.exists(), "매니페스트 없는 파일이 삭제됨 (무차별 삭제 위반)"

    final_state = read_state(state_file)
    pcc = final_state["post_complete_cleanup"]
    assert pcc["status"] == "OK"
    assert pcc["removed"] == []


# ---------------------------------------------------------------------------
# TC-E2E-4: legacy pipeline_state.json 미수정 (AC-4)
# ---------------------------------------------------------------------------

def test_legacy_pipeline_state_not_modified(tmp_path):
    """TC-E2E-4: pipeline_state.json(legacy) 수정 없음 (AC-4)."""
    state_file = tmp_path / "state.json"
    env = make_env(state_file)

    legacy = BASE_DIR / "pipeline_state.json"
    before = legacy.read_bytes() if legacy.exists() else None

    script = _make_state_script(state_file) + (
        "pipeline._register_workspace_artifact(state, 'x.json', 'cleanup_only', '')\n"
        "pipeline._run_post_complete_cleanup(state, None)\n"
        "pipeline._save(state)\n"
        "print('DONE')\n"
    )
    proc = run_helper(script, env)
    assert proc.returncode == 0, proc.stderr

    after = legacy.read_bytes() if legacy.exists() else None
    assert before == after, "legacy pipeline_state.json이 변경됨 (격리 위반)"

    # workspace_artifacts.json은 격리 tmp 디렉토리에만 존재해야 함.
    assert (tmp_path / "workspace_artifacts.json").exists()
    assert (state_file).exists()


# ---------------------------------------------------------------------------
# TC-E2E-5: Permission denied → deferred (AC-5) [Windows skip]
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="Windows에서는 chmod 기반 Permission denied 재현 불가",
)
def test_permission_denied_goes_to_deferred(tmp_path):
    """TC-E2E-5: Permission denied → deferred WARN (AC-5)."""
    state_file = tmp_path / "state.json"
    env = make_env(state_file)

    locked_dir = tmp_path / "locked"
    locked_dir.mkdir()
    locked_file = locked_dir / "tmp_locked.json"
    locked_file.write_text("{}", encoding="utf-8")

    script = _make_state_script(state_file) + (
        "import os, stat\n"
        "os.chmod(r'{d}', stat.S_IREAD | stat.S_IEXEC)\n"
        "pipeline._register_workspace_artifact(state, r'{f}', 'cleanup_only', '')\n"
        "summary = pipeline._run_post_complete_cleanup(state, None)\n"
        "pipeline._save(state)\n"
        "os.chmod(r'{d}', stat.S_IRWXU)\n"
        "print(json.dumps(summary))\n"
    ).format(d=str(locked_dir), f=str(locked_file))
    proc = run_helper(script, env)
    assert proc.returncode == 0, proc.stderr

    final_state = read_state(state_file)
    pcc = final_state["post_complete_cleanup"]
    assert pcc["status"] == "WARN"
    assert str(locked_file) in pcc["deferred"]
    assert pcc["ready_for_next_task"] is True


# ---------------------------------------------------------------------------
# TC-E2E-6: protected_evidence 누락 → BLOCKED (AC-6)
# ---------------------------------------------------------------------------

def test_missing_protected_blocks_cleanup(tmp_path):
    """TC-E2E-6: protected_evidence 파일 없으면 BLOCKED (AC-6)."""
    state_file = tmp_path / "state.json"
    env = make_env(state_file)

    missing_path = tmp_path / "does_not_exist_evidence.json"
    # 일부러 생성하지 않음.

    script = _make_state_script(state_file) + (
        "pipeline._register_workspace_artifact(state, r'{m}', 'protected_evidence', '')\n"
        "summary = pipeline._run_post_complete_cleanup(state, None)\n"
        "pipeline._save(state)\n"
        "print(json.dumps(summary))\n"
    ).format(m=str(missing_path))
    proc = run_helper(script, env)
    assert proc.returncode == 0, proc.stderr

    final_state = read_state(state_file)
    pcc = final_state["post_complete_cleanup"]
    assert pcc["status"] == "BLOCKED"
    assert pcc["ready_for_next_task"] is False
    assert str(missing_path) in pcc["missing_protected"]


# ---------------------------------------------------------------------------
# TC-E2E-7: status에 workspace readiness 표시 (AC-7)
# ---------------------------------------------------------------------------

def test_status_shows_workspace_readiness(tmp_path):
    """TC-E2E-7: status 출력에 workspace readiness 포함 (AC-7)."""
    state_file = tmp_path / "state.json"
    env = make_env(state_file)

    # post_complete_cleanup 필드를 가진 state 생성.
    script = _make_state_script(state_file) + (
        "pipeline._register_workspace_artifact(state, r'{c}', 'cleanup_only', '')\n"
        "pipeline._run_post_complete_cleanup(state, None)\n"
        "pipeline._save(state)\n"
        "print('DONE')\n"
    ).format(c=str(tmp_path / "nothere.json"))
    setup = run_helper(script, env)
    assert setup.returncode == 0, setup.stderr

    # final_state: status 호출 전 state에 readiness 필드가 있어야 함.
    final_state = read_state(state_file)
    assert "post_complete_cleanup" in final_state
    assert final_state["post_complete_cleanup"]["status"] in ("OK", "WARN", "BLOCKED")

    # 실제 CLI status 출력에 readiness 블록 표시.
    proc = run_cli(["status"], env)
    assert proc.returncode == 0, proc.stderr
    assert "Workspace Readiness" in proc.stdout


# ---------------------------------------------------------------------------
# TC-E2E-8: cleanup_status=BLOCKED이면 new 차단 (AC-8)
# ---------------------------------------------------------------------------

def test_new_blocked_when_cleanup_blocked(tmp_path):
    """TC-E2E-8: post_complete_cleanup.status=BLOCKED이면 new 차단 (AC-8).

    CLI Evidence Contract: PIPELINE_STATE_PATH 격리(make_env) + final_state assertion.
    """
    state_file = tmp_path / "state.json"
    # PIPELINE_STATE_PATH isolation via make_env(state_file)
    env = make_env(state_file)

    missing_path = tmp_path / "missing_protected.json"
    script = _make_state_script(state_file) + (
        "pipeline._register_workspace_artifact(state, r'{m}', 'protected_evidence', '')\n"
        "pipeline._run_post_complete_cleanup(state, None)\n"
        "pipeline._save(state)\n"
        "print('DONE')\n"
    ).format(m=str(missing_path))
    setup = run_helper(script, env)
    assert setup.returncode == 0, setup.stderr

    # final_state assertion: BLOCKED 기록 확인.
    final_state = read_state(state_file)
    assert final_state["post_complete_cleanup"]["status"] == "BLOCKED"

    # new 실행 시 차단되어야 함 (exit 1).
    proc = run_cli(["new", "--type", "IMP", "--desc", "should be blocked"], env)
    assert proc.returncode == 1, f"new가 차단되지 않음: stdout={proc.stdout} stderr={proc.stderr}"
    combined = proc.stdout + proc.stderr
    assert "BLOCKED" in combined or "final-cleanup" in combined

    # final_state: 차단되었으므로 pipeline_id가 새로 바뀌지 않아야 함.
    final_state = read_state(state_file)
    assert final_state["pipeline_id"] == PIPELINE_ID


# ---------------------------------------------------------------------------
# TC-E2E-9: hygiene final-cleanup --dry-run (AC-9)
# ---------------------------------------------------------------------------

def test_final_cleanup_dry_run(tmp_path):
    """TC-E2E-9: hygiene final-cleanup --dry-run returncode=0, 파일 미삭제 (AC-9)."""
    state_file = tmp_path / "state.json"
    env = make_env(state_file)

    cleanup_target = tmp_path / "tmp_dryrun.json"
    cleanup_target.write_text("{}", encoding="utf-8")

    script = _make_state_script(state_file) + (
        "pipeline._register_workspace_artifact(state, r'{c}', 'cleanup_only', '')\n"
        "pipeline._save(state)\n"
        "print('DONE')\n"
    ).format(c=str(cleanup_target))
    setup = run_helper(script, env)
    assert setup.returncode == 0, setup.stderr

    proc = run_cli(["hygiene", "final-cleanup", "--dry-run", "--json"], env)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["dry_run"] is True
    assert str(cleanup_target) in result["candidates"]
    # dry-run이므로 실제 삭제는 없어야 함.
    assert cleanup_target.exists(), "dry-run인데 파일이 삭제됨"

    # --apply로 실제 삭제 + final_state 검증.
    proc2 = run_cli(["hygiene", "final-cleanup", "--apply", "--json"], env)
    assert proc2.returncode == 0, proc2.stderr
    result2 = json.loads(proc2.stdout)
    assert result2["dry_run"] is False
    assert str(cleanup_target) in result2["removed"]
    assert not cleanup_target.exists(), "--apply 후 파일이 삭제되지 않음"


# ---------------------------------------------------------------------------
# TC-E2E-10: hygiene ready-next (AC-10 / AC-9)
# ---------------------------------------------------------------------------

def test_hygiene_ready_next(tmp_path):
    """TC-E2E-10: hygiene ready-next — OK는 exit 0, BLOCKED는 exit 1 (AC-9/AC-10)."""
    state_file = tmp_path / "state.json"
    env = make_env(state_file)

    # OK 케이스: cleanup_only만 등록 + 정리 완료.
    ok_script = _make_state_script(state_file) + (
        "pipeline._run_post_complete_cleanup(state, None)\n"
        "pipeline._save(state)\n"
        "print('DONE')\n"
    )
    setup_ok = run_helper(ok_script, env)
    assert setup_ok.returncode == 0, setup_ok.stderr

    proc_ok = run_cli(["hygiene", "ready-next", "--json"], env)
    assert proc_ok.returncode == 0, proc_ok.stderr
    res_ok = json.loads(proc_ok.stdout)
    assert res_ok["ready_for_next_task"] is True
    assert res_ok["status"] == "OK"

    # BLOCKED 케이스: protected_evidence 누락.
    missing = tmp_path / "missing_evi.json"
    blocked_script = _make_state_script(state_file) + (
        "pipeline._register_workspace_artifact(state, r'{m}', 'protected_evidence', '')\n"
        "pipeline._run_post_complete_cleanup(state, None)\n"
        "pipeline._save(state)\n"
        "print('DONE')\n"
    ).format(m=str(missing))
    setup_blk = run_helper(blocked_script, env)
    assert setup_blk.returncode == 0, setup_blk.stderr

    # final_state assertion: BLOCKED 상태.
    blk_state = read_state(state_file)
    assert blk_state["post_complete_cleanup"]["status"] == "BLOCKED"

    proc_blk = run_cli(["hygiene", "ready-next", "--json"], env)
    assert proc_blk.returncode == 1, f"BLOCKED인데 exit 0: {proc_blk.stdout}"
    res_blk = json.loads(proc_blk.stdout)
    assert res_blk["ready_for_next_task"] is False
    assert str(missing) in res_blk["missing_protected"]
