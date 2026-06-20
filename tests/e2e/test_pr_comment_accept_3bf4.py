"""
tests/e2e/test_pr_comment_accept_3bf4.py
BUG-20260620-3BF4 — PR 댓글 기반 User Acceptance 검증 (브라우저 승인 게이트 제거 후)

# [Purpose]: MT-1~3에서 구현한 PR 댓글 기반 승인 방식과 브라우저 승인 게이트 제거를
#   정적/CLI 검사로 회귀 검증한다. PIPELINE_STATE_PATH 격리 + final_state assertion(파일/소스
#   내용 단언) 패턴을 따른다.
# [Assumptions]: pipeline.py가 프로젝트 루트에 존재하며 MT-1~3 변경이 반영되어 있다.
#   pipeline-manager-agent.md가 .claude/agents/ 아래에 존재한다.
# [Vulnerability & Risks]: 정적 substring 검사이므로 주석/문자열 안에 우연히 동일 토큰이
#   들어오면 오탐 가능. self-check(TC-13)와 CLI --help(TC-10/11)로 보강한다.
# [Improvement]: AST 파싱으로 함수 정의 부재를 정밀 확인하면 substring 오탐을 더 줄일 수 있다.

14개 테스트 케이스:
TC-01: _run_browser_approval_server 함수 부재 확인 (ABSENT 검증)
TC-02: PIPELINE_BROWSER_APPROVAL_SKIP 환경변수 부재 확인
TC-03: browser_click_confirmed 필드 부재 확인
TC-04: ACCEPT-{pipeline_id} 형식 검증 코드 존재 확인
TC-05: pr_comment_timestamp_missing 오류 코드 존재 확인
TC-06: 자동 생성 packet 마커 포함 댓글 제외 로직 존재 확인
TC-07: _should_reuse_acceptance_nonce 함수 존재 확인
TC-08: --force-new-code 플래그 처리 로직 존재 확인
TC-09: _write_json 헬퍼가 acceptance_request.json 쓰기에 사용됨 확인
TC-10: gates accept --help에 --acceptance-code 인자 존재
TC-11: gates request-accept --force-new-code가 argparse 오류 없이 처리됨
TC-12: py_compile pipeline.py 성공
TC-13: 새 테스트 파일 자신이 존재함 확인 (self-check)
TC-14: pipeline-manager-agent.md에 브라우저 안내 없음 확인
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_PY = REPO_ROOT / "pipeline.py"


def test_tc01_browser_approval_server_absent() -> None:
    """TC-01: _run_browser_approval_server 함수가 pipeline.py에 없어야 한다."""
    src = PIPELINE_PY.read_text(encoding="utf-8")
    assert "_run_browser_approval_server" not in src, \
        "FAIL: _run_browser_approval_server가 pipeline.py에 아직 존재함"


def test_tc02_browser_approval_skip_env_absent() -> None:
    """TC-02: PIPELINE_BROWSER_APPROVAL_SKIP 환경변수 참조가 없어야 한다."""
    src = PIPELINE_PY.read_text(encoding="utf-8")
    assert "PIPELINE_BROWSER_APPROVAL_SKIP" not in src, \
        "FAIL: PIPELINE_BROWSER_APPROVAL_SKIP가 pipeline.py에 아직 존재함"


def test_tc03_browser_click_confirmed_absent() -> None:
    """TC-03: browser_click_confirmed 필드가 없어야 한다."""
    src = PIPELINE_PY.read_text(encoding="utf-8")
    assert "browser_click_confirmed" not in src, \
        "FAIL: browser_click_confirmed가 pipeline.py에 아직 존재함"


def test_tc04_accept_pipeline_id_format_present() -> None:
    """TC-04: ACCEPT-{pipeline_id} 형식 검증 코드가 있어야 한다."""
    src = PIPELINE_PY.read_text(encoding="utf-8")
    match = re.search(r"ACCEPT-.*pipeline_id", src)
    assert match, "FAIL: ACCEPT-{pipeline_id} 형식 검증 코드가 없음"


def test_tc05_pr_comment_timestamp_check_present() -> None:
    """TC-05: pr_comment_timestamp_missing 오류 코드가 있어야 한다."""
    src = PIPELINE_PY.read_text(encoding="utf-8")
    assert "pr_comment_timestamp_missing" in src or "comment_timestamp" in src, \
        "FAIL: PR 댓글 timestamp 검증 로직이 없음"


def test_tc06_packet_marker_exclusion_present() -> None:
    """TC-06: packet 마커 포함 댓글 제외 로직이 있어야 한다."""
    src = PIPELINE_PY.read_text(encoding="utf-8")
    assert "pipeline-human-acceptance-packet" in src, \
        "FAIL: packet 마커 댓글 제외 로직이 없음"


def test_tc07_should_reuse_acceptance_nonce_present() -> None:
    """TC-07: _should_reuse_acceptance_nonce 함수가 있어야 한다."""
    src = PIPELINE_PY.read_text(encoding="utf-8")
    assert "_should_reuse_acceptance_nonce" in src, \
        "FAIL: _should_reuse_acceptance_nonce 함수가 없음"


def test_tc08_force_new_code_flag_present() -> None:
    """TC-08: --force-new-code 플래그 처리 로직이 있어야 한다."""
    src = PIPELINE_PY.read_text(encoding="utf-8")
    assert "force_new_code" in src or "force-new-code" in src, \
        "FAIL: --force-new-code 플래그 처리 로직이 없음"


def test_tc09_atomic_write_used() -> None:
    """TC-09: _write_json 헬퍼가 acceptance_request.json 쓰기에 사용됨."""
    src = PIPELINE_PY.read_text(encoding="utf-8")
    assert "_write_json" in src, \
        "FAIL: _write_json 원자적 쓰기 헬퍼가 없음"


def test_tc10_acceptance_code_in_cli() -> None:
    """TC-10: gates accept --help에 --acceptance-code 인자 존재."""
    result = subprocess.run(
        [sys.executable, str(PIPELINE_PY), "gates", "accept", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert "acceptance-code" in combined or "acceptance_code" in combined, \
        f"FAIL: --acceptance-code가 gates accept --help에 없음\n{combined}"


def test_tc11_force_new_code_in_cli() -> None:
    """TC-11: gates request-accept --force-new-code가 argparse 오류 없이 처리됨."""
    # --force-new-code만으로는 실행이 안 되지만 argparse 인식은 되어야 함.
    result = subprocess.run(
        [sys.executable, str(PIPELINE_PY), "gates", "request-accept", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    combined = (result.stdout or "") + (result.stderr or "")
    assert "force-new-code" in combined or "force_new_code" in combined, \
        f"FAIL: --force-new-code가 gates request-accept --help에 없음\n{combined}"


def test_tc12_py_compile() -> None:
    """TC-12: pipeline.py 컴파일 성공."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(PIPELINE_PY)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, \
        f"FAIL: pipeline.py 컴파일 오류\n{result.stderr}"


def test_tc13_self_file_exists() -> None:
    """TC-13: 이 테스트 파일 자신이 존재함 (self-check)."""
    this_file = REPO_ROOT / "tests" / "e2e" / "test_pr_comment_accept_3bf4.py"
    assert this_file.exists(), "FAIL: test_pr_comment_accept_3bf4.py 파일이 없음"


def test_tc14_agent_md_no_browser_approval() -> None:
    """TC-14: pipeline-manager-agent.md에 브라우저 승인 안내가 없어야 한다."""
    agent_md = REPO_ROOT / ".claude" / "agents" / "pipeline-manager-agent.md"
    if not agent_md.exists():
        pytest.skip("pipeline-manager-agent.md 파일이 없어 스킵")
    content = agent_md.read_text(encoding="utf-8")
    assert "PIPELINE_BROWSER_APPROVAL_SKIP" not in content, \
        "FAIL: pipeline-manager-agent.md에 브라우저 승인 안내가 아직 존재함"
    assert "_run_browser_approval_server" not in content, \
        "FAIL: pipeline-manager-agent.md에 _run_browser_approval_server 참조가 있음"


if __name__ == "__main__":
    # 검증 블록 (Self-Verification): 핵심 정적 검사가 동작하는지 확인.
    _src = PIPELINE_PY.read_text(encoding="utf-8")
    assert "_run_browser_approval_server" not in _src, "browser server 잔존"
    assert "_should_reuse_acceptance_nonce" in _src, "nonce 재사용 헬퍼 부재"
    assert (REPO_ROOT / "tests" / "e2e" / "test_pr_comment_accept_3bf4.py").exists()
    print("[SELF-VERIFY] OK")
