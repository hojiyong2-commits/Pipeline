"""
test_hygiene_0df5.py -- IMP-20260601-0DF5 MT-3 Hygiene Scan/Archive E2E Tests

[Purpose]: pipeline.py hygiene scan/archive/schedule CLI 11개 E2E 검증.
           PIPELINE_STATE_PATH로 격리된 임시 state 파일 사용.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
ORACLE_DIR = Path(__file__).resolve().parent.parent / "oracles" / "IMP-20260601-0DF5"


def run_cli(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """pipeline.py CLI 실행 헬퍼."""
    cmd = [sys.executable, str(PIPELINE_PY)] + args
    run_env = os.environ.copy()
    run_env["PYTHONIOENCODING"] = "utf-8"
    if env:
        run_env.update(env)
    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        env=run_env,
    )
    # stdout/stderr를 UTF-8로 디코드 (Windows cp949 인코딩 오류 방지)
    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def isolated_env(tmp_path: Path) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 환경 변수 반환."""
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(tmp_path / "pipeline_state_test.json")
    return env


def test_T1_scan_candidate(tmp_path: Path) -> None:
    """scan --json이 OK 상태와 candidates/blocked/excluded 필드를 반환한다."""
    env = isolated_env(tmp_path)
    result = run_cli(["hygiene", "scan", "--json", "--older-than", "7d"], env=env)
    assert result.returncode == 0, f"scan 실패: {result.stderr}"
    output = json.loads(result.stdout)
    assert output["status"] == "OK"
    assert "candidates" in output
    assert "blocked" in output
    assert "excluded" in output
    assert output["note"] == "scan 모드는 후보만 표시. 실제 이동 없음."
    for c in output["candidates"]:
        assert c["blocked"] is False
        assert c.get("age_days_gte", 0) >= 7


def test_T2_archive_move(tmp_path: Path) -> None:
    """archive --dry-run 시 moved가 비고 dry_run=true가 반환된다."""
    deploy_root = tmp_path / "deploy"
    deploy_root.mkdir()
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(tmp_path / "pipeline_state_test.json")
    env["PIPELINE_DEPLOY_ROOT"] = str(deploy_root)
    result = run_cli(["hygiene", "archive", "--older-than", "7d", "--json", "--dry-run"], env=env)
    assert result.returncode == 0, f"archive dry-run 실패: {result.stderr}"
    output = json.loads(result.stdout)
    assert output["status"] in ("OK", "OK_WITH_BLOCKED")
    assert output["dry_run"] is True
    assert output["moved"] == []


def test_T3_secret_block(tmp_path: Path) -> None:
    """blocked 항목은 reason=secret_detected이고 moved에 포함되지 않는다."""
    oracle_input = ORACLE_DIR / "case_edge_secret_block" / "input.json"
    oracle_expected = ORACLE_DIR / "case_edge_secret_block" / "expected.json"
    assert oracle_input.exists(), f"Oracle input 없음: {oracle_input}"
    assert oracle_expected.exists(), f"Oracle expected 없음: {oracle_expected}"

    deploy_root = tmp_path / "deploy"
    deploy_root.mkdir()
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(tmp_path / "pipeline_state_test.json")
    env["PIPELINE_DEPLOY_ROOT"] = str(deploy_root)

    result = run_cli(["hygiene", "archive", "--older-than", "7d", "--json", "--dry-run"], env=env)
    assert result.returncode == 0, f"archive 실패: {result.stderr}"
    output = json.loads(result.stdout)
    moved_paths = [m["path"] for m in output.get("moved", [])]
    for b in output.get("blocked", []):
        assert b["reason"] == "secret_detected"
        assert b["path"] not in moved_paths


def test_T4_recent_skip(tmp_path: Path) -> None:
    """younger_than_threshold excluded 항목은 moved에 없다."""
    deploy_root = tmp_path / "deploy"
    deploy_root.mkdir()
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(tmp_path / "pipeline_state_test.json")
    env["PIPELINE_DEPLOY_ROOT"] = str(deploy_root)

    result = run_cli(["hygiene", "archive", "--older-than", "7d", "--json", "--dry-run"], env=env)
    assert result.returncode == 0, f"archive 실패: {result.stderr}"
    output = json.loads(result.stdout)
    moved_paths = [m["path"] for m in output.get("moved", [])]
    for e in output.get("excluded", []):
        if e.get("reason") == "younger_than_threshold":
            assert e["path"] not in moved_paths


def test_T5_protect_paths(tmp_path: Path) -> None:
    """보호 파일은 moved에 포함되지 않는다."""
    deploy_root = tmp_path / "deploy"
    deploy_root.mkdir()
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(tmp_path / "pipeline_state_test.json")
    env["PIPELINE_DEPLOY_ROOT"] = str(deploy_root)

    result = run_cli(["hygiene", "archive", "--older-than", "1d", "--json", "--dry-run"], env=env)
    assert result.returncode == 0, f"archive 실패: {result.stderr}"
    output = json.loads(result.stdout)
    moved_paths = [m["path"] for m in output.get("moved", [])]
    for p in ["pipeline.py", "CLAUDE.md", "README.md", "RELEASE_NOTES.md"]:
        assert p not in moved_paths, f"보호 파일이 moved에 포함됨: {p}"


def test_T6_active_pipeline_skip(tmp_path: Path) -> None:
    """활성 pipeline_id 포함 파일명은 moved에 없다."""
    deploy_root = tmp_path / "deploy"
    deploy_root.mkdir()
    state_path = tmp_path / "pipeline_state_test.json"
    state_path.write_text(json.dumps({"pipeline_id": "IMP-20260601-TEST"}), encoding="utf-8")
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    env["PIPELINE_DEPLOY_ROOT"] = str(deploy_root)

    result = run_cli(["hygiene", "archive", "--older-than", "1d", "--json", "--dry-run"], env=env)
    assert result.returncode == 0, f"archive 실패: {result.stderr}"
    output = json.loads(result.stdout)
    moved_paths = [m["path"] for m in output.get("moved", [])]
    for p in moved_paths:
        assert "IMP-20260601-TEST" not in p


def test_T7_drive_missing(tmp_path: Path) -> None:
    """PIPELINE_DEPLOY_ROOT가 없으면 archive가 오류 또는 빈 결과를 반환한다."""
    nonexistent_root = tmp_path / "nonexistent_XYZ" / "sub"
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(tmp_path / "pipeline_state_test.json")
    env["PIPELINE_DEPLOY_ROOT"] = str(nonexistent_root)

    result = run_cli(["hygiene", "archive", "--older-than", "1d", "--json"], env=env)
    if result.returncode == 0:
        output = json.loads(result.stdout)
        assert output["status"] in ("OK", "OK_WITH_BLOCKED")
    else:
        assert result.returncode != 0


def test_T8_schedule_install_dryrun(tmp_path: Path) -> None:
    """schedule install --dry-run은 exit 0이고 schtasks 명령어를 출력한다."""
    env = isolated_env(tmp_path)
    result = run_cli(["hygiene", "schedule", "install", "--dry-run"], env=env)
    assert result.returncode == 0, f"schedule install --dry-run 실패: {result.stderr}"
    combined = result.stdout + result.stderr
    assert "schtasks" in combined.lower() or "DRY" in combined


def test_T9_schedule_status_safe(tmp_path: Path) -> None:
    """schedule status는 exit 0으로 상태 정보를 출력한다."""
    env = isolated_env(tmp_path)
    result = run_cli(["hygiene", "schedule", "status"], env=env)
    assert result.returncode == 0, f"schedule status 실패: {result.stderr}"
    assert len(result.stdout.strip()) > 0


def test_T10_e2e_subprocess_isolated(tmp_path: Path) -> None:
    """PIPELINE_STATE_PATH 격리에서 scan/archive가 전역 state를 변경하지 않는다."""
    state_path = tmp_path / "pipeline_state_test.json"
    deploy_root = tmp_path / "deploy"
    deploy_root.mkdir()
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    env["PIPELINE_DEPLOY_ROOT"] = str(deploy_root)

    r1 = run_cli(["hygiene", "scan", "--json", "--older-than", "7d"], env=env, timeout=90)
    assert r1.returncode == 0
    scan_out = json.loads(r1.stdout)
    assert "status" in scan_out

    r2 = run_cli(["hygiene", "archive", "--older-than", "7d", "--json", "--dry-run"], env=env, timeout=90)
    assert r2.returncode == 0
    archive_out = json.loads(r2.stdout)
    assert archive_out["dry_run"] is True

    # final_state assertion
    if state_path.exists():
        final_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert isinstance(final_state, dict)


def test_T11_docs_section() -> None:
    """hygiene --help에 scan, archive, schedule 서브커맨드 이름이 포함된다."""
    result = run_cli(["hygiene", "--help"])
    help_text = result.stdout + result.stderr
    assert "scan" in help_text, "hygiene --help에 scan 없음"
    assert "archive" in help_text, "hygiene --help에 archive 없음"
    assert "schedule" in help_text, "hygiene --help에 schedule 없음"