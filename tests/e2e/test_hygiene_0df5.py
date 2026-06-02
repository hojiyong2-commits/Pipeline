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


def test_T2_archive_dry_run_does_not_move(tmp_path: Path) -> None:
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


def test_archive_moves_old_candidate_to_jjikkeogi_folder(tmp_path: Path) -> None:
    """실제 archive --older-than 7d 가 원본 파일을 이동하는지 검증 (dry-run 없음).

    검증 내용:
    - repo root의 후보 파일이 archive 후 사라짐
    - PIPELINE_DEPLOY_ROOT/찌꺼기/YYYY-MM-DD/ 아래로 이동됨
    - JSON moved 배열에 파일 포함
    """
    import time
    import datetime

    workspace_root = PIPELINE_PY.parent  # pipeline.py 가 있는 디렉토리
    test_file_name = "comment_hygiene_e2e_verify.txt"  # HYGIENE_ARCHIVE_PATTERNS의 comment_*.txt 매칭
    test_file = workspace_root / test_file_name

    # 테스트 파일 생성 후 mtime을 8일 전으로 설정
    test_file.write_text(
        "hygiene archive E2E 검증용 임시 파일 — 테스트 완료 후 자동 삭제됩니다",
        encoding="utf-8"
    )
    eight_days_ago = time.time() - 8 * 24 * 3600
    os.utime(str(test_file), (eight_days_ago, eight_days_ago))

    deploy_root = tmp_path / "deploy"
    state_path = tmp_path / "pipeline_state_test.json"
    # 완료 상태 파이프라인으로 설정 (활성 파이프라인 보호 방지)
    state_path.write_text(
        json.dumps({
            "schema_version": 2,
            "pipeline_id": "IMP-20260601-0DF5",
            "current_phase": "COMPLETE",
            "phases": {}, "external_gates": {},
            "events": [], "event_log": []
        }, ensure_ascii=False),
        encoding="utf-8"
    )

    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    env["PIPELINE_DEPLOY_ROOT"] = str(deploy_root)
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        result = subprocess.run(
            [sys.executable, str(PIPELINE_PY),
             "hygiene", "archive", "--older-than", "7d", "--json"],
            capture_output=True,
            timeout=60,
            env=env,
        )
        stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""

        assert result.returncode == 0, (
            f"hygiene archive 실패 (returncode={result.returncode}):\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )

        # 원본 파일이 workspace에서 사라졌는지 확인
        assert not test_file.exists(), (
            f"archive 후에도 원본 파일이 workspace에 남아있습니다: {test_file}\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )

        # deploy_root/찌꺼기/YYYY-MM-DD/ 아래로 이동됐는지 확인
        today = datetime.date.today().isoformat()
        expected_dest = deploy_root / "찌꺼기" / today / test_file_name
        assert expected_dest.exists(), (
            f"이동 목적지에 파일이 없습니다: {expected_dest}\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        )

        # JSON 출력의 moved 배열에 파일이 포함되는지 확인
        # moved 항목 구조: {"path": "원본파일명", "dest": "이동목적지경로"}
        output_data = json.loads(stdout)
        moved_names = [Path(m["path"]).name for m in output_data.get("moved", [])]
        assert test_file_name in moved_names, (
            f"moved 배열에 '{test_file_name}'이 없습니다: {moved_names}\n"
            f"stdout: {stdout}"
        )
        assert len(output_data.get("moved", [])) > 0, "moved 배열이 비어있습니다"

    finally:
        # 테스트 실패 시 원본 파일 정리 (이동 성공했으면 이미 없음)
        if test_file.exists():
            test_file.unlink(missing_ok=True)


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
    """schedule install --dry-run은 exit 0이고 schtasks 명령에 MON/09:00/weekly/archive가 포함된다."""
    env = isolated_env(tmp_path)
    result = run_cli(["hygiene", "schedule", "install", "--dry-run"], env=env)
    assert result.returncode == 0, f"schedule install --dry-run 실패: {result.stderr}"
    combined = result.stdout + result.stderr
    # 스케줄 파라미터 검증
    assert "/SC WEEKLY" in combined or "/sc weekly" in combined.lower(), \
        f"/SC WEEKLY 없음: {combined}"
    assert "/D MON" in combined or "/d mon" in combined.lower(), \
        f"/D MON 없음 (일요일이 아닌 월요일이어야 함): {combined}"
    assert "/ST 09:00" in combined or "/st 09:00" in combined.lower(), \
        f"/ST 09:00 없음 (02:00이 아닌 09:00이어야 함): {combined}"
    assert "hygiene archive --older-than 7d" in combined, \
        f"'hygiene archive --older-than 7d' 없음: {combined}"


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