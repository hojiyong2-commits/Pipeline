"""
test_oracle_quality_waiver_60bb.py — BUG-20260622-60BB MT-2 End-to-End CLI Path Tests

# [Purpose]: MT-1에서 pipeline.py의 oracle gate waiver PASS 경로(allow_no_oracle)에
#            추가된 state["oracle_quality"] 기록을 E2E로 검증한다. waiver PASS 시
#            oracle_quality.status가 "PASS"로 기록되어 _external_gate_blockers()가
#            Architect phase를 차단하지 않음을 보장하는 회귀 테스트 5개를 제공한다.
# [Assumptions]: pipeline.py가 tests/e2e의 2단계 상위 디렉토리에 위치하고,
#                CONTRACTS_DIR = BASE_DIR / "pipeline_contracts" 가 repo 루트 기준
#                절대 경로이므로, contract fixture는 실제 repo의 pipeline_contracts/
#                아래에 임시 디렉토리(TC-60BB-N)를 생성하고 teardown에서 삭제한다.
#                state는 PIPELINE_STATE_PATH 환경변수로 tmp_path 안에 격리한다.
# [Vulnerability & Risks]:
#   - contract fixture가 repo의 pipeline_contracts/ 아래 실제 디렉토리를 만들므로,
#     teardown 실패 시 잔여 디렉토리가 남을 수 있다. fixture는 try/finally + ignore_errors
#     rmtree로 best-effort 정리한다.
#   - subprocess 호출이 30초 timeout 초과 시 실패한다(병리적 환경 방어).
#   - PIPELINE_STATE_PATH는 pipeline.py import 시점에 평가되므로 subprocess 환경변수로 주입.
# [Improvement]: 향후 _external_gate_blockers()를 직접 import하여 단위 수준에서도
#               oracle_quality blocker 부재를 교차 검증할 수 있음.

# CLI Evidence Contract (BUG-20260525-39DE):
# - 상태 변경 CLI 호출은 반드시 PIPELINE_STATE_PATH 격리 + final_state assertion 포함
# - stdout-only 검증 금지 (CLI 출력만으로 PASS 판정 금지)
# - read-only CLI에는 # CLI_EVIDENCE_ALLOW_READ_ONLY: <reason> 주석 사용
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# pipeline.py는 tests/e2e의 2단계 상위 디렉토리에 위치
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_PY = REPO_ROOT / "pipeline.py"
# CONTRACTS_DIR = BASE_DIR / "pipeline_contracts" 와 동일 (pipeline.py SSoT)
CONTRACTS_DIR = REPO_ROOT / "pipeline_contracts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """`python pipeline.py <args>` 실행 후 CompletedProcess 반환.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess에 전달할 환경 변수 딕셔너리(None이면 부모 환경).
        timeout: 초 단위 타임아웃 (기본 30초, 0/음수 금지).
    Returns:
        subprocess.CompletedProcess (stdout/stderr/returncode 포함).
    Raises:
        TypeError: args가 None이거나 list가 아닌 경우.
        ValueError: timeout이 0 이하인 경우.
    """
    if args is None:
        raise TypeError("args must not be None")
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    if not isinstance(timeout, int):
        raise TypeError(f"timeout must be int, got {type(timeout).__name__}")
    if timeout <= 0:
        raise ValueError(f"timeout must be > 0, got {timeout}")  # 0/음수 금지: 무한 대기 방지
    cmd = [sys.executable, str(PIPELINE_PY)] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        cwd=str(REPO_ROOT),
    )


def make_env(state_file: Path) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 + 대시보드/네트워크 호출 차단 환경변수.

    Args:
        state_file: 격리된 state JSON 파일 경로.
    Returns:
        부모 환경에 격리 변수를 덮어쓴 새 dict.
    Raises:
        TypeError: state_file이 None인 경우.
    """
    if state_file is None:
        raise TypeError("state_file must not be None")
    return {
        **os.environ,
        "PIPELINE_STATE_PATH": str(state_file),
        "PIPELINE_NO_DASHBOARD": "1",
    }


def write_state(state_file: Path, state: Dict[str, Any]) -> None:
    """state dict를 JSON으로 직렬화하여 state 파일에 기록.

    Args:
        state_file: 기록 대상 state 파일 경로.
        state: 직렬화할 state 딕셔너리.
    Raises:
        TypeError: state_file이 None이거나 state가 dict가 아닌 경우.
    """
    if state_file is None:
        raise TypeError("state_file must not be None")
    if state is None:
        raise TypeError("state must not be None")
    if not isinstance(state, dict):
        raise TypeError(f"state must be dict, got {type(state).__name__}")
    state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_state(state_file: Path) -> Dict[str, Any]:
    """state 파일을 JSON으로 파싱하여 dict로 반환.

    Args:
        state_file: 읽을 state 파일 경로.
    Returns:
        파싱된 state 딕셔너리.
    Raises:
        TypeError: state_file이 None인 경우.
        FileNotFoundError: 파일이 존재하지 않는 경우.
    """
    if state_file is None:
        raise TypeError("state_file must not be None")
    if not state_file.exists():
        raise FileNotFoundError(f"state file not found: {state_file}")
    return json.loads(state_file.read_text(encoding="utf-8"))


def write_json(path: Path, data: Dict[str, Any]) -> None:
    """dict를 JSON 파일로 기록 (부모 디렉토리 자동 생성).

    Args:
        path: 기록 대상 경로.
        data: 직렬화할 딕셔너리.
    Raises:
        TypeError: path가 None이거나 data가 dict가 아닌 경우.
    """
    if path is None:
        raise TypeError("path must not be None")
    if not isinstance(data, dict):
        raise TypeError(f"data must be dict, got {type(data).__name__}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# State / contract fixtures
# ---------------------------------------------------------------------------

def _technical_pass_state(pipeline_id: str, technical_status: str = "PASS") -> Dict[str, Any]:
    """oracle gate 실행에 필요한 minimal state.

    technical gate status를 지정 가능하게 하여, oracle gate가 technical PASS를
    선행 요구하는지 검증할 수 있다.

    Args:
        pipeline_id: 테스트용 파이프라인 ID (1~255자).
        technical_status: technical gate status ("PASS" / "PENDING").
    Returns:
        minimal state 딕셔너리.
    Raises:
        TypeError: pipeline_id가 None이거나 str이 아닌 경우.
        ValueError: pipeline_id가 빈 문자열이거나 255자 초과인 경우.
    """
    if pipeline_id is None:
        raise TypeError("pipeline_id must not be None")
    if not isinstance(pipeline_id, str):
        raise TypeError(f"pipeline_id must be str, got {type(pipeline_id).__name__}")
    if len(pipeline_id) == 0:
        raise ValueError("pipeline_id must not be empty")
    if len(pipeline_id) > 255:
        raise ValueError(f"pipeline_id must be <= 255 chars, got {len(pipeline_id)}")
    return {
        "version": "1.2.0",
        "pipeline_id": pipeline_id,
        "type": "BUG",
        "description": "BUG-20260622-60BB MT-2 회귀 테스트용",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "pipeline_started_at": "2026-01-01T00:00:00Z",
        "terminal_state": None,
        "current_phase": "harness",
        "blocked": False,
        "phases": {
            "pm": {"status": "DONE", "notes": []},
            "dev": {"status": "DONE", "notes": []},
            "qa": {"status": "DONE", "notes": []},
            "sec": {"status": "SKIPPED", "notes": []},
            "build": {"status": "DONE", "notes": []},
            "harness": {"status": "PENDING", "notes": []},
            "architect": {"status": "PENDING", "notes": []},
        },
        "event_log": [],
        "external_gates": {
            "enabled": True,
            "mode": "three_gate",
            "technical": {
                "status": technical_status,
                "started_at": "2026-01-01T00:00:00Z" if technical_status == "PASS" else None,
                "completed_at": "2026-01-01T00:01:00Z" if technical_status == "PASS" else None,
                "evidence": "deterministic_tool_gate" if technical_status == "PASS" else None,
                "report_file": None,
                "notes": [],
            },
            "oracle": {
                "status": "PENDING", "started_at": None, "completed_at": None,
                "evidence": None, "report_file": None, "notes": [],
            },
            "acceptance": {
                "status": "PENDING", "started_at": None, "completed_at": None,
                "evidence": None, "report_file": None, "notes": [],
            },
            "github_ci": {
                "status": "PENDING", "started_at": None, "completed_at": None,
                "evidence": None, "report_file": None, "notes": [],
            },
        },
        "oracle_quality": {},
    }


def _make_contract_dir(pipeline_id: str, allow_no_oracle: bool, with_technical_result: bool) -> Path:
    """pipeline_contracts/<pid>/ 아래 oracle waiver 검증용 contract 파일 생성.

    Args:
        pipeline_id: 테스트용 파이프라인 ID.
        allow_no_oracle: contract_audit.json의 allow_no_oracle 값.
        with_technical_result: gates/technical_result.json 생성 여부.
    Returns:
        생성된 contract 루트 디렉토리 경로.
    Raises:
        TypeError: pipeline_id가 None이거나 str이 아닌 경우.
        ValueError: pipeline_id가 빈 문자열인 경우.
    """
    if pipeline_id is None:
        raise TypeError("pipeline_id must not be None")
    if not isinstance(pipeline_id, str):
        raise TypeError(f"pipeline_id must be str, got {type(pipeline_id).__name__}")
    if len(pipeline_id) == 0:
        raise ValueError("pipeline_id must not be empty")
    root = CONTRACTS_DIR / pipeline_id
    # contract_audit.json — status PASS + allow_no_oracle 토글
    write_json(root / "contract_audit.json", {
        "schema_version": 1,
        "status": "PASS",
        "allow_no_oracle": allow_no_oracle,
        "waiver_reason": "user-approved non-oracle task" if allow_no_oracle else "",
        "blockers": [],
        "warnings": [],
    })
    # oracle_manifest.json — 빈 entries (waivable blocker 유발)
    write_json(root / "oracle_manifest.json", {
        "schema_version": 1,
        "pipeline_id": pipeline_id,
        "oracles": [],
    })
    if with_technical_result:
        write_json(root / "gates" / "technical_result.json", {
            "schema_version": 1,
            "status": "PASS",
            "complete_eligible": True,
            "checks": [],
        })
    return root


@pytest.fixture
def contract_env(tmp_path):
    """테스트별 격리된 state 파일 + contract 디렉토리 생성/정리 fixture.

    yield된 헬퍼로 (pipeline_id, allow_no_oracle, technical_status)를 받아
    state 파일 경로와 env를 반환한다. teardown에서 생성한 모든 contract
    디렉토리를 best-effort로 삭제한다.
    """
    created_roots: List[Path] = []
    state_files: Dict[str, Path] = {}

    def _setup(
        pipeline_id: str,
        allow_no_oracle: bool = True,
        technical_status: str = "PASS",
        with_technical_result: bool = True,
    ):
        state_file = tmp_path / f"state_{pipeline_id}.json"
        write_state(state_file, _technical_pass_state(pipeline_id, technical_status))
        root = _make_contract_dir(pipeline_id, allow_no_oracle, with_technical_result)
        created_roots.append(root)
        state_files[pipeline_id] = state_file
        return state_file, make_env(state_file)

    try:
        yield _setup
    finally:
        for root in created_roots:
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)  # best-effort 정리


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_allow_no_oracle_waiver_records_oracle_quality_pass(contract_env):
    """AC-1/AC-2/AC-3: allow_no_oracle waiver PASS 시 oracle_quality.status == PASS 기록."""
    pid = "TC-60BB-1"
    state_file, env = contract_env(pid, allow_no_oracle=True, technical_status="PASS")
    # PIPELINE_STATE_PATH isolation via make_env() — state 격리 확인

    result = run_cli(["gates", "oracle"], env=env)

    assert result.returncode == 0, (
        f"waiver PASS 경로는 exit 0 이어야 함. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # final_state assertion — stdout만으로 판정 금지
    final_state = read_state(state_file)
    oq = final_state.get("oracle_quality")
    assert isinstance(oq, dict), f"oracle_quality must be dict, got {oq!r}"
    assert oq.get("status") == "PASS", f"oracle_quality.status must be PASS, got {oq!r}"
    assert oq.get("waived") is True, f"oracle_quality.waived must be True, got {oq!r}"
    assert oq.get("allow_no_oracle") is True, f"oracle_quality.allow_no_oracle must be True, got {oq!r}"
    assert "reason" in oq, f"oracle_quality must have 'reason' key, got {oq!r}"
    assert "checked_at" in oq, f"oracle_quality must have 'checked_at' key, got {oq!r}"
    # oracle 외부 게이트도 PASS로 전이되었는지 확인
    assert final_state["external_gates"]["oracle"]["status"] == "PASS"


def test_no_oracle_without_waiver_flag_stays_blocked(contract_env):
    """AC-4: allow_no_oracle=false 이면 waiver 미적용 — oracle gate FAIL + oracle_quality != PASS."""
    pid = "TC-60BB-2"
    state_file, env = contract_env(pid, allow_no_oracle=False, technical_status="PASS")
    # PIPELINE_STATE_PATH isolation via make_env() — state 격리 확인

    result = run_cli(["gates", "oracle"], env=env)

    assert result.returncode == 1, (
        f"allow_no_oracle=false 이면 waivable blocker라도 차단되어 exit 1 이어야 함. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # final_state assertion — oracle_quality가 PASS로 기록되지 않아야 함
    final_state = read_state(state_file)
    oq = final_state.get("oracle_quality")
    oq_status = (oq.get("status") if isinstance(oq, dict) else None)
    assert oq_status != "PASS", (
        f"waiver 미적용 시 oracle_quality.status는 PASS가 아니어야 함 (PENDING/없음), got {oq!r}"
    )
    # oracle 게이트도 PASS가 아니어야 함
    assert final_state["external_gates"]["oracle"]["status"] != "PASS"


def test_technical_gate_required_before_oracle(contract_env):
    """AC-4(추가 보호): technical gate PENDING 이면 oracle gate가 즉시 차단(exit 1)."""
    pid = "TC-60BB-3"
    # PIPELINE_STATE_PATH isolation via make_env() — state 격리 확인
    # technical_status=PENDING, technical_result.json도 미생성
    state_file, env = contract_env(
        pid, allow_no_oracle=True, technical_status="PENDING", with_technical_result=False
    )

    result = run_cli(["gates", "oracle"], env=env)

    assert result.returncode == 1, (
        f"technical gate PENDING 이면 oracle gate는 선행 요구 위반으로 exit 1 이어야 함. "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # final_state assertion — technical PENDING 으로 인해 oracle_quality가 PASS로
    # 기록되지 않았음을 확인 (waiver 경로에 도달하지 못함)
    final_state = read_state(state_file)
    oq = final_state.get("oracle_quality")
    oq_status = (oq.get("status") if isinstance(oq, dict) else None)
    assert oq_status != "PASS", (
        f"technical 미통과 시 oracle_quality는 PASS로 기록되면 안 됨, got {oq!r}"
    )


def test_oracle_quality_waiver_has_reason_and_timestamp(contract_env):
    """AC-2: waiver PASS 기록의 reason/checked_at 필드가 빈 값이 아님."""
    pid = "TC-60BB-4"
    state_file, env = contract_env(pid, allow_no_oracle=True, technical_status="PASS")
    # PIPELINE_STATE_PATH isolation via make_env() — state 격리 확인

    result = run_cli(["gates", "oracle"], env=env)

    assert result.returncode == 0, (
        f"waiver PASS 경로는 exit 0 이어야 함. stderr={result.stderr!r}"
    )
    final_state = read_state(state_file)
    oq = final_state.get("oracle_quality")
    assert isinstance(oq, dict), f"oracle_quality must be dict, got {oq!r}"
    reason = oq.get("reason")
    checked_at = oq.get("checked_at")
    assert isinstance(reason, str) and reason.strip() != "", (
        f"oracle_quality.reason must be non-empty string, got {reason!r}"
    )
    assert isinstance(checked_at, str) and checked_at.strip() != "", (
        f"oracle_quality.checked_at must be non-empty string, got {checked_at!r}"
    )


def test_waiver_pass_clears_oracle_quality_blocker(contract_env):
    """AC-3: waiver PASS 후 oracle_quality.status == PASS 기록으로 blocker가 제거됨.

    oracle_quality.status == PASS 기록이 'oracle_quality gate must be PASS' blocker를
    제거하는지 final_state로 직접 검증한다. gates oracle 완료 후 state를 읽어
    oracle_quality 필드와 external_gates.oracle 상태를 확인한다.
    """
    pid = "TC-60BB-5"
    state_file, env = contract_env(pid, allow_no_oracle=True, technical_status="PASS")
    # PIPELINE_STATE_PATH isolation via make_env() — state 격리 확인

    # waiver PASS 실행
    oracle_result = run_cli(["gates", "oracle"], env=env)
    assert oracle_result.returncode == 0, (
        f"waiver PASS 경로는 exit 0 이어야 함. stderr={oracle_result.stderr!r}"
    )
    # final_state assertion — oracle_quality.status == PASS 확인 + blocker 부재 확인
    final_state = read_state(state_file)
    oq = final_state.get("oracle_quality")
    assert isinstance(oq, dict) and oq.get("status") == "PASS", (
        f"oracle_quality.status must be PASS after waiver, got {oq!r}"
    )
    # oracle 외부 게이트 PASS 확인 (blocker 제거 검증)
    oracle_gate_status = final_state.get("external_gates", {}).get("oracle", {}).get("status")
    assert oracle_gate_status == "PASS", (
        f"external_gates.oracle.status must be PASS after waiver, got {oracle_gate_status!r}"
    )
    # oracle_quality.status == PASS 이면 _external_gate_blockers()의
    # 'oracle_quality gate must be PASS' 조건이 충족되어 blocker가 발생하지 않음
    # (blocker 로직은 pipeline.py _external_gate_blockers 참조)
    assert oq.get("status") == "PASS", (
        f"oracle_quality blocker 차단 조건: oracle_quality.status != PASS, got {oq!r}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
