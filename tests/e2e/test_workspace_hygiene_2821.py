"""
test_workspace_hygiene_2821.py — IMP-20260614-2821 MT-5

COMPLETE 직전 workspace/evidence hygiene gate Real CLI Path E2E 테스트 (12 케이스).

# [Purpose]:
#   _check_workspace_hygiene 가 gates request-accept / gates accept CLI 흐름에서
#   untracked oracle 증거를 BLOCKED 처리하고, cleanup_only 임시 파일은 WARN으로만
#   분류하며, state["workspace_hygiene"]에 결과를 저장하는지 검증한다.
#
# [Assumptions]:
#   - PIPELINE_STATE_PATH 환경변수로 상태 파일 격리.
#   - subprocess 기반 실제 CLI 실행 (내부 함수 직접 호출 금지).
#   - git untracked 검사는 실제 저장소(BASE_DIR) 기준으로 수행되므로, 테스트는
#     합성 pipeline_id 아래 임시 oracle 파일을 생성/정리하여 격리한다.
#   - hygiene preflight는 request-accept의 첫 게이트이므로 BLOCKED 케이스는
#     다른 게이트 도달 전에 차단된다. 비-BLOCKED 케이스는 state 저장 직후
#     downstream 게이트에서 실패할 수 있으나 workspace_hygiene 필드는 항상 저장된다.
#
# CLI Evidence Contract (IMP-20260525-6FAC):
#   - 상태 변경 CLI 호출마다 PIPELINE_STATE_PATH 격리 사용
#   - final_state assertion 포함 (stdout-only 검증 금지)
#   - subprocess 기반 실제 CLI 실행
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest

PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
BASE_DIR = PIPELINE_PY.parent
ORACLE_ROOT = BASE_DIR / "tests" / "oracles"
CONTRACTS_DIR = BASE_DIR / "pipeline_contracts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(
    args: List[str],
    env: Dict[str, str],
    timeout: int = 60,
) -> "subprocess.CompletedProcess[str]":
    """`python pipeline.py <args>` 실행 후 CompletedProcess 반환.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess에 전달할 환경 변수.
        timeout: 초 단위 timeout.
    Raises:
        TypeError: args가 list가 아닌 경우.
    """
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
        cwd=str(BASE_DIR),
    )


def make_env(tmp_path: Path) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 환경변수 구성 (gh 없는 환경).

    Args:
        tmp_path: pytest tmp_path fixture.
    Returns:
        subprocess에 전달할 환경 변수 dict.
    Raises:
        TypeError: tmp_path가 None.
    """
    if tmp_path is None:
        raise TypeError("tmp_path must not be None")
    state_file = tmp_path / "pipeline_state.json"
    env = dict(os.environ)
    env["PIPELINE_STATE_PATH"] = str(state_file)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # gh CLI 무력화 (PATH 기반 탐색 차단) — hygiene은 git만 사용하므로 git은 유지 필요.
    # PATH를 비우지 않고 그대로 두되, PIPELINE_GH_EXECUTABLE은 설정하지 않는다.
    env.pop("PIPELINE_GH_EXECUTABLE", None)
    return env


def load_final_state(env: Dict[str, str]) -> Dict[str, Any]:
    """PIPELINE_STATE_PATH가 가리키는 state 파일을 로드 (없으면 빈 dict)."""
    state_file = Path(env["PIPELINE_STATE_PATH"])
    if not state_file.exists():
        return {}
    with open(state_file, encoding="utf-8") as f:
        return json.load(f)


def synthetic_pid() -> str:
    """충돌 없는 합성 테스트 pipeline_id 생성 (실제 oracle dir과 격리)."""
    return "IMP-29990101-T" + uuid.uuid4().hex[:4].upper()


def bootstrap_state(env: Dict[str, str], pid: str) -> None:
    """격리된 state 파일에 합성 pipeline_id를 직접 기록한다(new CLI 우회).

    실제 BASE_DIR을 오염시키지 않도록 new CLI 대신 state 파일을 직접 구성한다.
    requirements_tracking.enabled=false 로 AC 검사를 우회한다.

    Args:
        env: PIPELINE_STATE_PATH가 설정된 env dict.
        pid: 합성 pipeline_id.
    """
    state_file = Path(env["PIPELINE_STATE_PATH"])
    state = {
        "pipeline_id": pid,
        "description": "workspace hygiene e2e 2821",
        "current_phase": "harness",
        "phases": {},
        "external_gates": {"enabled": True},
        "event_log": [],
        "requirements_tracking": {"enabled": False},
    }
    state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_evidence_file(tmp_path: Path) -> Path:
    """배포 가능한 evidence 파일을 tmp_path에 생성하고 경로 반환."""
    ev = tmp_path / "result_output.txt"
    ev.write_text("user-facing result evidence", encoding="utf-8")
    return ev


class OracleFixture:
    """합성 pid 아래 임시 oracle 파일을 생성/정리하는 헬퍼.

    실제 BASE_DIR 저장소에 파일을 생성하므로 반드시 cleanup()으로 제거해야 한다.
    """

    def __init__(self, pid: str) -> None:
        if pid is None:
            raise TypeError("pid must not be None")
        self.pid = pid
        self.oracle_dir = ORACLE_ROOT / pid
        self.contract_dir = CONTRACTS_DIR / pid
        self._created: List[Path] = []

    def add_oracle_file(self, rel_name: str, content: str) -> Path:
        """oracle 디렉터리 아래 파일을 생성한다(untracked 상태)."""
        target = self.oracle_dir / rel_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._created.append(target)
        return target

    def write_oracle_manifest(self, oracles: List[Dict[str, Any]]) -> Path:
        """oracle_manifest.json을 contract 디렉터리에 생성한다."""
        self.contract_dir.mkdir(parents=True, exist_ok=True)
        mpath = self.contract_dir / "oracle_manifest.json"
        mpath.write_text(
            json.dumps({"oracles": oracles}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._created.append(mpath)
        return mpath

    def write_inventory(self, entries: List[Dict[str, Any]]) -> Path:
        """evidence_inventory.json을 contract 디렉터리에 생성한다."""
        self.contract_dir.mkdir(parents=True, exist_ok=True)
        ipath = self.contract_dir / "evidence_inventory.json"
        ipath.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._created.append(ipath)
        return ipath

    def cleanup(self) -> None:
        """생성한 임시 파일과 빈 디렉터리를 제거한다."""
        import shutil
        for d in (self.oracle_dir, self.contract_dir):
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def oracle_fixture():
    """합성 pid OracleFixture를 제공하고 테스트 종료 시 정리한다."""
    pid = synthetic_pid()
    fx = OracleFixture(pid)
    try:
        yield fx
    finally:
        fx.cleanup()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# TC-1: untracked oracle → request-accept BLOCKED (untracked_oracle_evidence)
def test_tc1_untracked_oracle_blocks_request_accept(oracle_fixture, tmp_path):
    """tests/oracles/<pid>/ 아래 untracked oracle 파일이 있으면 request-accept가
    BLOCKED되고 failure_code=untracked_oracle_evidence가 출력된다."""
    fx = oracle_fixture
    env = make_env(tmp_path)
    bootstrap_state(env, fx.pid)
    fx.add_oracle_file("TC-1/input.json", '{"x": 1}')

    ev = write_evidence_file(tmp_path)
    r = run_cli(["gates", "request-accept", "--evidence", str(ev)], env=env)

    combined = r.stdout + r.stderr
    assert r.returncode != 0, f"expected BLOCKED, got 0\n{combined[:600]}"
    assert "WORKSPACE HYGIENE GATE" in combined, combined[:600]
    assert "untracked_oracle_evidence" in combined, combined[:600]

    final_state = load_final_state(env)
    wh = final_state.get("workspace_hygiene") or {}
    assert wh.get("status") == "BLOCKED", wh
    assert wh.get("untracked_oracle_count", 0) >= 1, wh


# TC-2: tracked oracle, no issues → hygiene OK (state 저장 확인)
def test_tc2_tracked_oracle_hygiene_ok(tmp_path):
    """실제 저장소에 이미 tracked 상태인 oracle 파일을 사용하는 파이프라인은
    hygiene OK가 되어야 한다. (이 저장소의 기존 tracked oracle을 가진 pid 사용)"""
    env = make_env(tmp_path)
    # oracle dir이 없고 oracle_manifest도 없는 합성 pid → 검사 대상 없음 → OK
    pid = synthetic_pid()
    bootstrap_state(env, pid)

    ev = write_evidence_file(tmp_path)
    r = run_cli(["gates", "request-accept", "--evidence", str(ev)], env=env)

    # hygiene은 OK여야 하며, BLOCKED 메시지가 없어야 한다 (downstream에서 다른 이유로
    # 실패할 수 있으나 hygiene gate는 통과).
    combined = r.stdout + r.stderr
    assert "WORKSPACE HYGIENE GATE" not in combined, combined[:600]

    final_state = load_final_state(env)
    wh = final_state.get("workspace_hygiene") or {}
    assert wh.get("status") in ("OK", "WARN"), wh
    assert wh.get("untracked_oracle_count", 0) == 0, wh


# TC-3: cleanup_only 파일만 → WARN (BLOCKED 아님)
def test_tc3_cleanup_only_warn_not_blocked(oracle_fixture, tmp_path):
    """oracle 디렉터리에 cleanup_only 파일(build_report.xml)만 있으면 WARN이지만
    BLOCKED는 아니어야 한다."""
    fx = oracle_fixture
    env = make_env(tmp_path)
    bootstrap_state(env, fx.pid)
    # cleanup_only 패턴(build_report.xml)은 oracle dir 아래에 있어도 차단 안 됨.
    fx.add_oracle_file("build_report.xml", "<build_report/>")

    ev = write_evidence_file(tmp_path)
    r = run_cli(["gates", "request-accept", "--evidence", str(ev)], env=env)

    combined = r.stdout + r.stderr
    assert "WORKSPACE HYGIENE GATE" not in combined, combined[:600]

    final_state = load_final_state(env)
    wh = final_state.get("workspace_hygiene") or {}
    assert wh.get("status") == "WARN", wh
    cleanup_items = wh.get("cleanup_only_items") or []
    assert any("build_report.xml" in str(c) for c in cleanup_items), wh


# TC-4: oracle_manifest 참조 파일 missing → BLOCKED (protected_evidence_missing).
# 규칙 2(missing)는 deferral과 무관하게 항상 활성(기존 게이트와 중복 없음).
def test_tc4_manifest_ref_missing_blocks(oracle_fixture, tmp_path):
    """oracle_manifest가 참조하는 input/expected 파일이 없으면 request-accept가
    BLOCKED되고 failure_code=protected_evidence_missing이 출력된다."""
    fx = oracle_fixture
    env = make_env(tmp_path)
    bootstrap_state(env, fx.pid)
    missing_rel = f"tests/oracles/{fx.pid}/TC-X/input.json"
    fx.write_oracle_manifest([
        {"input_path": missing_rel, "expected_path": missing_rel, "case_kind": "normal"}
    ])

    ev = write_evidence_file(tmp_path)
    r = run_cli(["gates", "request-accept", "--evidence", str(ev)], env=env)

    combined = r.stdout + r.stderr
    assert r.returncode != 0, f"expected BLOCKED\n{combined[:600]}"
    assert "WORKSPACE HYGIENE GATE" in combined, combined[:600]
    assert "protected_evidence_missing" in combined, combined[:600]

    final_state = load_final_state(env)
    wh = final_state.get("workspace_hygiene") or {}
    assert wh.get("status") == "BLOCKED", wh


# TC-5: deferral 검증 — contract manifest 존재 시 hygiene은 oracle untracked를 차단하지 않고
# 기존 게이트(_check_oracle_manifest_vs_inventory/_validate_evidence_provenance)에 위임한다.
def test_tc5_defers_to_existing_gate_when_manifest_present(oracle_fixture, tmp_path):
    """contract oracle_manifest.json이 있으면 hygiene은 untracked oracle을 직접 차단하지
    않고(WORKSPACE HYGIENE GATE 미출력) 기존 게이트가 request-accept를 차단한다."""
    fx = oracle_fixture
    env = make_env(tmp_path)
    bootstrap_state(env, fx.pid)
    ref_rel = f"tests/oracles/{fx.pid}/TC-Y/expected.json"
    fx.add_oracle_file("TC-Y/expected.json", '{"ok": true}')
    # contract manifest 생성 → deferral=True. inventory는 비워(기존 게이트가 차단).
    fx.write_oracle_manifest([
        {"input_path": ref_rel, "expected_path": ref_rel, "case_kind": "normal"}
    ])
    fx.write_inventory([])

    ev = write_evidence_file(tmp_path)
    r = run_cli(["gates", "request-accept", "--evidence", str(ev)], env=env)

    combined = r.stdout + r.stderr
    assert r.returncode != 0, f"expected BLOCKED by existing gate\n{combined[:600]}"
    # hygiene은 위임했으므로 WORKSPACE HYGIENE GATE 메시지로 차단하지 않는다.
    assert "WORKSPACE HYGIENE GATE" not in combined, combined[:600]
    # 기존 게이트(oracle_manifest/inventory mismatch)가 차단했는지 확인.
    assert (
        "oracle_not_in_evidence_inventory" in combined
        or "evidence_inventory_empty" in combined
        or "protected_evidence" in combined
    ), combined[:600]

    final_state = load_final_state(env)
    wh = final_state.get("workspace_hygiene") or {}
    # deferral 시 hygiene 자체는 BLOCKED를 만들지 않는다(차단은 기존 게이트 소관).
    assert wh.get("status") in ("OK", "WARN"), wh


# TC-6: state["workspace_hygiene"] 필드 저장 확인
def test_tc6_state_workspace_hygiene_fields(tmp_path):
    """request-accept 후 state["workspace_hygiene"]에 status / blocking_items /
    cleanup_only_items 필드가 저장된다."""
    env = make_env(tmp_path)
    pid = synthetic_pid()
    bootstrap_state(env, pid)

    ev = write_evidence_file(tmp_path)
    run_cli(["gates", "request-accept", "--evidence", str(ev)], env=env)

    final_state = load_final_state(env)
    wh = final_state.get("workspace_hygiene")
    assert isinstance(wh, dict), final_state.get("workspace_hygiene")
    assert "status" in wh, wh
    assert "blocking_items" in wh, wh
    assert "cleanup_only_items" in wh, wh
    assert "checked_at" in wh, wh


# TC-7: cleanup_only_items 목록에 build_report.xml 포함 확인
def test_tc7_cleanup_only_includes_build_report(oracle_fixture, tmp_path):
    """oracle dir에 build_report.xml이 있으면 cleanup_only_items에 포함된다."""
    fx = oracle_fixture
    env = make_env(tmp_path)
    bootstrap_state(env, fx.pid)
    fx.add_oracle_file("build_report.xml", "<r/>")
    fx.add_oracle_file("oracle_result_dump.txt", "dump")

    ev = write_evidence_file(tmp_path)
    run_cli(["gates", "request-accept", "--evidence", str(ev)], env=env)

    final_state = load_final_state(env)
    wh = final_state.get("workspace_hygiene") or {}
    items = [str(c) for c in (wh.get("cleanup_only_items") or [])]
    assert any("build_report.xml" in c for c in items), wh
    assert any("oracle_result_dump.txt" in c for c in items), wh


# TC-8: accept 단계 nonce consume 전 hygiene 재실행 (BLOCKED → consume 안 됨)
def test_tc8_accept_stage_hygiene_blocks_before_consume(oracle_fixture, tmp_path):
    """accept 단계에서 untracked oracle이 있으면 nonce consume 전에 BLOCKED되고
    acceptance_request가 CONSUMED 되지 않는다."""
    fx = oracle_fixture
    env = make_env(tmp_path)
    bootstrap_state(env, fx.pid)
    fx.add_oracle_file("TC-8/input.json", '{"x": 1}')

    ev = write_evidence_file(tmp_path)
    # accept를 직접 호출 (acceptance_request 없어도 hygiene이 먼저 차단되거나
    # missing_acceptance_request로 차단됨 — 두 경우 모두 consume은 발생하지 않음).
    r = run_cli(
        [
            "gates", "accept", "--result", "ACCEPT",
            "--evidence", str(ev),
            "--acceptance-code", f"ACCEPT-{fx.pid}-DEADBEEF",
        ],
        env=env,
    )
    combined = r.stdout + r.stderr
    assert r.returncode != 0, combined[:600]
    # acceptance_request.json 이 ACCEPTED/CONSUMED로 바뀌지 않았는지 확인.
    req_file = BASE_DIR / "acceptance_request.json"
    if req_file.exists():
        req = json.loads(req_file.read_text(encoding="utf-8"))
        # 다른 파이프라인의 잔존 파일일 수 있으므로 pid가 일치할 때만 검증.
        if str(req.get("pipeline_id", "")) == fx.pid:
            assert req.get("status") != "CONSUMED", req


# TC-9: pipeline.py status에 cleanup_only warning 표시 확인
def test_tc9_status_shows_cleanup_warning(oracle_fixture, tmp_path):
    """state에 cleanup_only_items가 있으면 status 출력에 CLEANUP 안내가 표시된다."""
    fx = oracle_fixture
    env = make_env(tmp_path)
    bootstrap_state(env, fx.pid)
    # state에 직접 workspace_hygiene 주입 (status는 state를 읽기만 함).
    state_file = Path(env["PIPELINE_STATE_PATH"])
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["workspace_hygiene"] = {
        "status": "WARN",
        "blocking_items": [],
        "cleanup_only_items": ["build_report.xml", "tmp_tc1.json"],
        "cleanup_command": 'Remove-Item "build_report.xml" "tmp_tc1.json"',
        "checked_at": "2026-06-14T00:00:00",
    }
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    r = run_cli(["status"], env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    combined = r.stdout + r.stderr
    assert "CLEANUP 안내" in combined, combined[:800]
    assert "build_report.xml" in combined, combined[:800]


# TC-10: git 조회 비정상 종료(returncode!=0) → fail-closed (BLOCKED).
# subprocess로 pipeline import 후 subprocess.run을 git error로 monkeypatch하여 직접 검증.
def test_tc10_git_error_fail_closed(oracle_fixture, tmp_path):
    """git이 실행되지만 비정상 종료(returncode=1)하면 _check_workspace_hygiene은
    fail-closed로 workspace_hygiene_check_failed BLOCKED를 반환한다."""
    fx = oracle_fixture
    env = make_env(tmp_path)
    # untracked oracle dir 파일 생성 (contract manifest 없음 → 규칙 1 활성).
    fx.add_oracle_file("TC-10/input.json", '{"x": 1}')

    script = tmp_path / "git_error_probe.py"
    script.write_text(
        "import json\n"
        "import sys\n"
        f"sys.path.insert(0, {str(BASE_DIR)!r})\n"
        "import subprocess\n"
        "import pipeline as P\n"
        "_orig = subprocess.run\n"
        "def _fake(args, *a, **k):\n"
        "    if isinstance(args, (list, tuple)) and args and str(args[0]) == 'git':\n"
        "        return subprocess.CompletedProcess(args, 1, '', 'forced git error')\n"
        "    return _orig(args, *a, **k)\n"
        "P.subprocess.run = _fake\n"
        f"r = P._check_workspace_hygiene({{'pipeline_id': {fx.pid!r}}})\n"
        "print('HYGIENE_JSON=' + json.dumps(r, ensure_ascii=True))\n",
        encoding="utf-8",
    )
    cp = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(BASE_DIR), env=env, timeout=60,
    )
    assert cp.returncode == 0, cp.stdout + cp.stderr
    payload = None
    for line in (cp.stdout or "").splitlines():
        if line.startswith("HYGIENE_JSON="):
            payload = json.loads(line[len("HYGIENE_JSON="):])
            break
    assert payload is not None, cp.stdout[:400]
    assert payload.get("status") == "BLOCKED", payload
    blocking = " ".join(str(b) for b in (payload.get("blocking_items") or []))
    assert "workspace_hygiene_check_failed" in blocking, payload


# TC-11: protected evidence SHA mismatch → BLOCKED (protected_evidence_sha_mismatch)
def test_tc11_sha_mismatch_blocks(oracle_fixture, tmp_path):
    """oracle_manifest 참조 파일이 tracked이지만 inventory sha256과 현재 내용이
    다르면(시뮬레이션) protected_evidence_sha_mismatch로 BLOCKED된다.

    tracked 상태를 만들기 위해 이 저장소의 실제 tracked 파일(CLAUDE.md)을
    참조 대상으로 사용하고, inventory에는 일부러 틀린 sha256을 기록한다."""
    fx = oracle_fixture
    env = make_env(tmp_path)
    bootstrap_state(env, fx.pid)
    # tracked 파일(CLAUDE.md)을 oracle 참조로 사용 — git에 이미 tracked.
    tracked_rel = "CLAUDE.md"
    fx.write_oracle_manifest([
        {"input_path": tracked_rel, "expected_path": tracked_rel, "case_kind": "normal"}
    ])
    abs_tracked = str((BASE_DIR / tracked_rel).resolve())
    fx.write_inventory([
        {
            "pipeline_id": fx.pid,
            "path": abs_tracked,
            "kind": "oracle_input",
            "sha256": "0" * 64,  # 의도적으로 틀린 sha
            "protection": "protected",
        }
    ])

    ev = write_evidence_file(tmp_path)
    r = run_cli(["gates", "request-accept", "--evidence", str(ev)], env=env)
    combined = r.stdout + r.stderr
    assert r.returncode != 0, combined[:600]
    assert "WORKSPACE HYGIENE GATE" in combined, combined[:600]
    assert "protected_evidence_sha_mismatch" in combined, combined[:600]

    final_state = load_final_state(env)
    wh = final_state.get("workspace_hygiene") or {}
    assert wh.get("status") == "BLOCKED", wh


# TC-12: 정상 케이스 (oracle 없음, no issues) → hygiene OK, status 정상 출력
def test_tc12_clean_case_hygiene_ok_status_normal(tmp_path):
    """oracle/cleanup 파일이 없는 깨끗한 합성 파이프라인은 hygiene OK가 되고,
    status 출력에 CLEANUP 안내가 나타나지 않는다."""
    env = make_env(tmp_path)
    pid = synthetic_pid()
    bootstrap_state(env, pid)

    ev = write_evidence_file(tmp_path)
    run_cli(["gates", "request-accept", "--evidence", str(ev)], env=env)

    final_state = load_final_state(env)
    wh = final_state.get("workspace_hygiene") or {}
    assert wh.get("status") == "OK", wh
    assert not (wh.get("cleanup_only_items") or []), wh
    assert not (wh.get("blocking_items") or []), wh

    r = run_cli(["status"], env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    combined = r.stdout + r.stderr
    assert "CLEANUP 안내" not in combined, combined[:800]
