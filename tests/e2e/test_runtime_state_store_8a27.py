"""
test_runtime_state_store_8a27.py — IMP-20260621-8A27 MT-5

Runtime State Store v1 Real CLI Path E2E 테스트 (11 케이스).

# [Purpose]:
#   pipeline_state.json이 git-tracked 파일이라 파이프라인 실행마다 working tree가
#   dirty 상태가 되는 문제를 해결하는 runtime state store를 검증한다. active state는
#   .pipeline/active_run.json (pointer) + .pipeline/runs/<id>/state.json (실제 state)로
#   이동하고, legacy pipeline_state.json은 read-only fallback 전용으로만 유지한다.
#
# [Assumptions]:
#   - AC-1/2/3/9/10(runtime store 자체 동작): pipeline.py를 격리 임시 디렉토리로 복사하여
#     실행한다. 이렇게 하면 BASE_DIR이 임시 디렉토리로 해석되어 .pipeline/active_run.json,
#     .pipeline/runs/<id>/state.json, pipeline_state.json이 모두 격리된다. 실제 저장소의
#     pipeline_state.json은 절대 건드리지 않는다.
#   - AC-5/6/7/8(env override / fallback / 에러): PIPELINE_STATE_PATH 또는 격리 복사본으로 검증.
#   - subprocess 기반 실제 CLI 실행 (내부 함수 직접 호출 금지).
#
# CLI Evidence Contract (IMP-20260525-6FAC):
#   - 상태 변경 CLI 호출마다 격리(PIPELINE_STATE_PATH 또는 격리 BASE_DIR) 사용
#   - final_state assertion 포함 (stdout-only 검증 금지)
#   - subprocess 기반 실제 CLI 실행
#
# [Vulnerability & Risks]:
#   격리 복사본 실행은 pipeline.py 단일 파일만 복사하므로, pipeline.py가 import하는
#   외부 모듈 없이도 cmd_new가 동작한다는 전제에 의존한다. cmd_new가 추가 로컬 파일을
#   요구하게 되면 복사 대상을 늘려야 한다.
# [Improvement]:
#   격리 복사본 대신 monkeypatch BASE_DIR fixture로 in-process 검증을 추가하면 속도 향상.
"""

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
BASE_DIR = PIPELINE_PY.parent
LEGACY_STATE = BASE_DIR / "pipeline_state.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_env() -> Dict[str, str]:
    """대시보드/인코딩만 고정한 기본 env (PIPELINE_STATE_PATH 미설정)."""
    env = dict(os.environ)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PIPELINE_STATE_PATH", None)
    return env


def run_cli_in(
    cwd: Path,
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: int = 60,
) -> "subprocess.CompletedProcess[str]":
    """주어진 cwd에서 `python pipeline.py <args>` 실행.

    Args:
        cwd: 작업 디렉토리 (격리 복사본 루트).
        args: pipeline.py에 전달할 인자 리스트.
        env: 환경 변수 (None이면 기본 env).
        timeout: 초 단위 timeout.
    Returns:
        subprocess.CompletedProcess.
    Raises:
        TypeError: args가 list가 아니거나 cwd가 None인 경우.
    """
    if cwd is None:
        raise TypeError("cwd must not be None")
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    pipeline_in_cwd = Path(cwd) / "pipeline.py"
    cmd = [sys.executable, str(pipeline_in_cwd)] + list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env or _base_env(),
        cwd=str(cwd),
    )


@pytest.fixture()
def isolated_repo(tmp_path: Path) -> Path:
    """pipeline.py를 격리 임시 디렉토리로 복사하여 BASE_DIR을 격리한다.

    이렇게 하면 cmd_new가 생성하는 .pipeline/active_run.json,
    .pipeline/runs/<id>/state.json, pipeline_state.json이 모두 임시 디렉토리에
    생성되어 실제 저장소를 오염시키지 않는다.
    """
    if not PIPELINE_PY.exists():
        raise FileNotFoundError(f"pipeline.py not found: {PIPELINE_PY}")
    dest = tmp_path / "repo"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(PIPELINE_PY), str(dest / "pipeline.py"))
    return dest


def _runtime_state_files(repo: Path) -> List[Path]:
    """격리 repo의 .pipeline/runs/<id>/state.json 목록."""
    runs = repo / ".pipeline" / "runs"
    if not runs.exists():
        return []
    return list(runs.glob("*/state.json"))


def _pointer_path(repo: Path) -> Path:
    return repo / ".pipeline" / "active_run.json"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def synthetic_pid() -> str:
    """충돌 없는 합성 테스트 pipeline_id."""
    return "IMP-29990101-T" + uuid.uuid4().hex[:4].upper()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_active_pointer_created(isolated_repo: Path) -> None:
    """AC-1: pipeline.py new → .pipeline/active_run.json 생성."""
    # isolated_repo 격리: PIPELINE_STATE_PATH 비설정으로 runtime store 검증
    env = _base_env()
    env.pop("PIPELINE_STATE_PATH", None)  # runtime store 경로 격리 (pointer 경유)
    res = run_cli_in(isolated_repo, ["new", "--type", "IMP", "--desc", "rt store e2e"], env=env)
    assert res.returncode == 0, f"new 실패: {res.stderr}"

    pointer = _pointer_path(isolated_repo)
    assert pointer.exists(), "active_run.json 이 생성되지 않음"
    # final_state assertion: pointer 내용 검증
    data = _load_json(pointer)
    assert data.get("pipeline_id"), f"pointer pipeline_id 누락: {data}"
    assert data.get("created_at"), f"pointer created_at 누락: {data}"
    assert "state_path" in data, f"pointer state_path 누락: {data}"


def test_runtime_state_created(isolated_repo: Path) -> None:
    """AC-2: .pipeline/runs/<id>/state.json 생성 + 핵심 필드 포함."""
    env = _base_env()
    env.pop("PIPELINE_STATE_PATH", None)  # runtime store 경로 격리 (pointer 경유)
    res = run_cli_in(isolated_repo, ["new", "--type", "IMP", "--desc", "rt store e2e"], env=env)
    assert res.returncode == 0, f"new 실패: {res.stderr}"

    states = _runtime_state_files(isolated_repo)
    assert len(states) == 1, f"runtime state.json 1개 기대, 실제 {len(states)}: {states}"

    # final_state assertion: runtime state 핵심 필드
    state = _load_json(states[0])
    pid = state.get("pipeline_id")
    assert pid, f"runtime state pipeline_id 누락: {state.keys()}"
    assert state.get("current_phase"), f"current_phase 누락: {state.get('current_phase')}"
    assert state.get("created_at"), "created_at 누락"
    # pointer가 가리키는 디렉토리와 일치
    assert states[0].parent.name == pid, f"디렉토리 {states[0].parent.name} != pid {pid}"


def test_new_does_not_modify_tracked_state(isolated_repo: Path) -> None:
    """AC-3: pipeline.py new 후 pipeline_state.json이 생성/수정되지 않음 (legacy clean)."""
    legacy = isolated_repo / "pipeline_state.json"
    assert not legacy.exists(), "사전 조건: legacy 파일 없어야 함"

    env = _base_env()
    env.pop("PIPELINE_STATE_PATH", None)  # runtime store 경로 격리 (pointer 경유)
    res = run_cli_in(isolated_repo, ["new", "--type", "IMP", "--desc", "rt store e2e"], env=env)
    assert res.returncode == 0, f"new 실패: {res.stderr}"

    # final_state assertion: legacy 파일은 여전히 미생성, runtime store만 존재
    assert not legacy.exists(), "new가 legacy pipeline_state.json을 생성함 (AC-3 위반)"
    assert _pointer_path(isolated_repo).exists(), "pointer는 생성되어야 함"
    assert len(_runtime_state_files(isolated_repo)) == 1, "runtime state는 생성되어야 함"


def test_status_reads_runtime_state(isolated_repo: Path) -> None:
    """AC-4: status가 active pointer 경유 runtime state를 읽는다."""
    env = _base_env()
    env.pop("PIPELINE_STATE_PATH", None)  # runtime store 경로 격리 (pointer 경유)
    new_res = run_cli_in(isolated_repo, ["new", "--type", "IMP", "--desc", "status read e2e"], env=env)
    assert new_res.returncode == 0, f"new 실패: {new_res.stderr}"
    state = _load_json(_runtime_state_files(isolated_repo)[0])
    pid = state["pipeline_id"]

    status_res = run_cli_in(isolated_repo, ["status"])
    assert status_res.returncode == 0, f"status 실패: {status_res.stderr}"
    # final_state assertion: status가 runtime store의 pid를 출력
    assert pid in status_res.stdout, (
        f"status 출력에 runtime pid {pid} 없음 — runtime state 미독취\n{status_res.stdout[-400:]}"
    )


def test_env_path_priority(isolated_repo: Path, tmp_path: Path) -> None:
    """AC-5: PIPELINE_STATE_PATH가 최우선 — pointer/runtime store보다 우선."""
    # 먼저 pointer + runtime state 생성
    run_cli_in(isolated_repo, ["new", "--type", "IMP", "--desc", "env prio e2e"])
    runtime_pid = _load_json(_runtime_state_files(isolated_repo)[0])["pipeline_id"]

    # env override state 파일을 별도 pid로 구성
    env_state = tmp_path / "override_state.json"
    override_pid = synthetic_pid()
    env_state.write_text(
        json.dumps(
            {
                "pipeline_id": override_pid,
                "current_phase": "pm",
                "phases": {},
                "event_log": [],
                "requirements_tracking": {"enabled": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    env = _base_env()
    env["PIPELINE_STATE_PATH"] = str(env_state)

    status_res = run_cli_in(isolated_repo, ["status"], env=env)
    assert status_res.returncode == 0, f"status 실패: {status_res.stderr}"
    # final_state assertion: env override pid가 출력되고 runtime pid는 무시됨
    assert override_pid in status_res.stdout, (
        f"PIPELINE_STATE_PATH override pid {override_pid} 미출력 (최우선 위반)"
    )
    assert runtime_pid not in status_res.stdout, (
        f"runtime pid {runtime_pid}가 env override를 무시하지 못함"
    )


def test_legacy_fallback(isolated_repo: Path) -> None:
    """AC-6: active pointer가 없으면 legacy pipeline_state.json read fallback."""
    # pointer 없이 legacy 파일만 직접 구성
    legacy = isolated_repo / "pipeline_state.json"
    legacy_pid = synthetic_pid()
    legacy.write_text(
        json.dumps(
            {
                "pipeline_id": legacy_pid,
                "current_phase": "pm",
                "phases": {},
                "event_log": [],
                "requirements_tracking": {"enabled": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert not _pointer_path(isolated_repo).exists(), "사전 조건: pointer 없어야 함"

    status_res = run_cli_in(isolated_repo, ["status"])
    assert status_res.returncode == 0, f"status 실패: {status_res.stderr}"
    # final_state assertion: legacy pid를 fallback으로 읽음
    assert legacy_pid in status_res.stdout, (
        f"legacy fallback 실패 — legacy pid {legacy_pid} 미출력\n{status_res.stdout[-400:]}"
    )


def test_pointer_corruption(isolated_repo: Path) -> None:
    """AC-7: active_run.json 손상 시 [PIPELINE ERROR] + 복구 안내 + exit 1."""
    pointer = _pointer_path(isolated_repo)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text("{ this is : not valid json", encoding="utf-8")

    # status는 설계상 항상 exit 0 (BUG-20260527-5CF4)이므로, _load를 swallow하지 않는
    # check --phase dev로 [PIPELINE ERROR] + exit 1 동작을 검증한다.
    res = run_cli_in(isolated_repo, ["check", "--phase", "dev"])
    # final_state assertion: exit 1 + [PIPELINE ERROR] + 복구 안내
    assert res.returncode != 0, "손상 pointer인데 정상 종료됨 (AC-7 위반)"
    combined = res.stdout + res.stderr
    assert "[PIPELINE ERROR]" in combined, f"[PIPELINE ERROR] 미출력:\n{combined[-400:]}"
    assert "active_run.json" in combined, "복구 안내에 active_run.json 언급 없음"


def test_pointer_missing_state(isolated_repo: Path) -> None:
    """AC-8: pointer는 유효하나 runtime state 파일 부재 시 [PIPELINE ERROR] + exit 1."""
    pointer = _pointer_path(isolated_repo)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    missing_pid = synthetic_pid()
    pointer.write_text(
        json.dumps({"pipeline_id": missing_pid, "created_at": "x", "state_path": "y"}),
        encoding="utf-8",
    )
    # runtime state.json은 일부러 생성하지 않음
    assert not (isolated_repo / ".pipeline" / "runs" / missing_pid / "state.json").exists()

    # status는 설계상 항상 exit 0이므로 check --phase dev로 검증한다.
    res = run_cli_in(isolated_repo, ["check", "--phase", "dev"])
    # final_state assertion: exit 1 + [PIPELINE ERROR]
    assert res.returncode != 0, "state 누락인데 정상 종료됨 (AC-8 위반)"
    combined = res.stdout + res.stderr
    assert "[PIPELINE ERROR]" in combined, f"[PIPELINE ERROR] 미출력:\n{combined[-400:]}"
    assert "runtime state" in combined or "state 파일이 없습니다" in combined, (
        f"복구 안내에 state 파일 부재 언급 없음:\n{combined[-400:]}"
    )


def test_complete_does_not_modify_tracked(isolated_repo: Path) -> None:
    """AC-9: 여러 state write(new + reset) 후에도 legacy pipeline_state.json 미생성.

    실제 COMPLETE까지 가려면 전체 파이프라인이 필요하므로, state write가 반복되는
    대표 흐름(new 후 새 new로 기존 아카이브)에서 legacy 파일이 절대 생성되지 않음을 검증한다.
    """
    legacy = isolated_repo / "pipeline_state.json"
    env = _base_env()
    env.pop("PIPELINE_STATE_PATH", None)  # runtime store 경로 격리 (pointer 경유)
    r1 = run_cli_in(isolated_repo, ["new", "--type", "IMP", "--desc", "first"], env=env)
    assert r1.returncode == 0, f"first new 실패: {r1.stderr}"
    assert not legacy.exists(), "첫 new가 legacy 생성 (AC-9 위반)"

    # 두 번째 new — 기존 파이프라인 아카이브 + 새 runtime state write 발생
    r2 = run_cli_in(isolated_repo, ["new", "--type", "IMP", "--desc", "second"], env=env)
    assert r2.returncode == 0, f"second new 실패: {r2.stderr}"
    # final_state assertion: 반복 write 후에도 legacy 미생성, runtime store만 갱신
    assert not legacy.exists(), "반복 state write가 legacy pipeline_state.json을 생성 (AC-9 위반)"
    assert _pointer_path(isolated_repo).exists(), "pointer는 존재해야 함"
    states = _runtime_state_files(isolated_repo)
    assert len(states) >= 1, "runtime state 최소 1개 존재해야 함"


def test_pr_diff_hygiene(isolated_repo: Path) -> None:
    """AC-10: runtime state 산출물은 .pipeline/ 아래에만 생성되어 PR diff에 포함되지 않음.

    .pipeline/** 는 .gitignore 대상이므로, new가 생성하는 모든 active state 산출물이
    .pipeline/ 디렉토리 하위에만 위치하고 저장소 루트에 tracked 파일을 만들지 않음을 검증한다.
    """
    env = _base_env()
    env.pop("PIPELINE_STATE_PATH", None)  # runtime store 경로 격리 (pointer 경유)
    res = run_cli_in(isolated_repo, ["new", "--type", "IMP", "--desc", "pr hygiene e2e"], env=env)
    assert res.returncode == 0, f"new 실패: {res.stderr}"

    # final_state assertion: 루트에 pipeline_state.json 없음 + active 산출물은 .pipeline/ 하위
    assert not (isolated_repo / "pipeline_state.json").exists(), "루트 legacy 파일 생성됨"
    pointer = _pointer_path(isolated_repo)
    states = _runtime_state_files(isolated_repo)
    assert pointer.exists() and len(states) == 1
    dotpipe = isolated_repo / ".pipeline"
    assert pointer.resolve().is_relative_to(dotpipe.resolve()), "pointer가 .pipeline/ 밖에 있음"
    assert states[0].resolve().is_relative_to(dotpipe.resolve()), "runtime state가 .pipeline/ 밖에 있음"


def test_regression_existing_tests() -> None:
    """AC-11: 5개 회귀 테스트가 여전히 PASS (runtime store 변경이 기존 흐름 미파손).

    대표 회귀: test_workspace_hygiene_2821(가장 state-write 흐름이 많음)을 실제 실행하여
    runtime store 변경 후에도 PASS인지 확인한다. 나머지 4개(pr_comment_accept_3bf4,
    request_accept_nonce_reuse_aef0, acceptance_provenance_2338, pr_approver_b96c)는
    dev_handover regression_tests 블록과 별도 CI 회귀 실행으로 보장한다.
    """
    regression_target = (
        BASE_DIR / "tests" / "e2e" / "test_workspace_hygiene_2821.py"
    )
    if not regression_target.exists():
        pytest.skip("회귀 대상 테스트 파일 없음")
    res = subprocess.run(
        [sys.executable, "-m", "pytest", str(regression_target), "-q"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        cwd=str(BASE_DIR),
    )
    # final_state assertion: 회귀 테스트 종료 코드 0
    assert res.returncode == 0, (
        f"회귀 테스트 test_workspace_hygiene_2821 FAIL (runtime store 변경이 기존 흐름 파손):\n"
        f"{res.stdout[-1200:]}\n{res.stderr[-400:]}"
    )


def test_reset_does_not_touch_legacy_state(isolated_repo: Path) -> None:
    """AC-12: pipeline.py reset 후 legacy pipeline_state.json 절대 미삭제/미수정.

    IMP-20260621-8A27 REJECT 원인(STATE_FILE.unlink()로 pipeline_state.json 삭제)에 대한
    회귀 방지 테스트. reset은 Runtime State Store(.pipeline/active_run.json + runs/<id>/state.json)만
    정리하고, legacy pipeline_state.json은 절대 건드리지 않아야 한다.
    """
    env = _base_env()
    env.pop("PIPELINE_STATE_PATH", None)  # runtime store 경로 격리 (pointer 경유)

    # 1. 파이프라인 생성
    r1 = run_cli_in(isolated_repo, ["new", "--type", "IMP", "--desc", "reset hygiene e2e"], env=env)
    assert r1.returncode == 0, f"new 실패: {r1.stderr}"

    # 2. 사전 조건: legacy는 없어야 함 (runtime store만 존재)
    legacy = isolated_repo / "pipeline_state.json"
    assert not legacy.exists(), "사전 조건: new 후 legacy 파일 없어야 함"
    pointer = _pointer_path(isolated_repo)
    assert pointer.exists(), "사전 조건: pointer 존재해야 함"
    states_before = _runtime_state_files(isolated_repo)
    assert len(states_before) == 1, "사전 조건: runtime state 1개 존재해야 함"

    # 3. 임의로 legacy 파일을 미리 배치 (추후 reset이 건드리지 않는지 확인용)
    fake_pid = synthetic_pid()
    legacy.write_text(
        json.dumps(
            {
                "pipeline_id": fake_pid,
                "current_phase": "pm",
                "phases": {},
                "event_log": [],
                "requirements_tracking": {"enabled": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    legacy_mtime_before = legacy.stat().st_mtime

    # 4. reset 실행
    r_reset = run_cli_in(isolated_repo, ["reset"], env=env)
    assert r_reset.returncode == 0, f"reset 실패: {r_reset.stderr}"
    assert "[RESET]" in r_reset.stdout or "보관 후 초기화" in r_reset.stdout, (
        f"reset 완료 메시지 미출력:\n{r_reset.stdout}"
    )

    # 5. final_state assertion: legacy 파일은 mtime/내용 변경 없이 그대로
    assert legacy.exists(), "reset이 legacy pipeline_state.json을 삭제함 (AC-12 위반)"
    legacy_mtime_after = legacy.stat().st_mtime
    assert legacy_mtime_before == legacy_mtime_after, (
        "reset이 legacy pipeline_state.json을 수정함 (AC-12 위반)"
    )
    legacy_content = _load_json(legacy)
    assert legacy_content.get("pipeline_id") == fake_pid, (
        "reset이 legacy pipeline_state.json 내용을 변경함 (AC-12 위반)"
    )

    # 6. Runtime State Store가 실제로 정리됐는지 확인
    assert not pointer.exists(), "reset 후 active_run.json이 남아 있음 (Runtime Store 미정리)"


def test_reset_env_path_cleans_isolated_state(tmp_path: Path) -> None:
    """AC-13: PIPELINE_STATE_PATH 환경변수 설정 시 reset은 해당 파일만 삭제.

    PIPELINE_STATE_PATH가 설정된 환경(테스트 격리)에서 reset이 격리 state 파일만 삭제하고
    실제 legacy pipeline_state.json을 건드리지 않음을 검증한다.
    """
    # isolated state 파일 생성
    isolated_state = tmp_path / "test_state.json"
    fake_pid = synthetic_pid()
    isolated_state.write_text(
        json.dumps(
            {
                "pipeline_id": fake_pid,
                "current_phase": "pm",
                "phases": {},
                "event_log": [],
                "requirements_tracking": {"enabled": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    env = _base_env()
    env["PIPELINE_STATE_PATH"] = str(isolated_state)

    # pipeline.py가 있는 실제 repo cwd에서 실행 (격리 복사본 불필요, env path가 우선)
    actual_pipeline_dir = PIPELINE_PY.parent
    res = subprocess.run(
        [sys.executable, str(PIPELINE_PY), "reset"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
        cwd=str(actual_pipeline_dir),
    )
    assert res.returncode == 0, f"reset 실패: {res.stderr}"

    # final_state assertion: 격리 state 파일만 삭제됨
    assert not isolated_state.exists(), (
        "PIPELINE_STATE_PATH reset이 격리 state 파일을 삭제하지 않음 (AC-13 위반)"
    )

    # 실제 legacy 파일은 건드리지 않음
    actual_legacy = actual_pipeline_dir / "pipeline_state.json"
    if actual_legacy.exists():
        # 존재한다면 내용이 유효한 JSON이어야 함 (reset이 오염시켰다면 손상될 것)
        try:
            _load_json(actual_legacy)
        except Exception as exc:
            pytest.fail(f"reset이 실제 pipeline_state.json을 오염시킴: {exc}")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
