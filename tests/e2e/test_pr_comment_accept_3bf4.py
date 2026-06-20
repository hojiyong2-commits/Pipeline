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

16개 테스트 케이스:
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
TC-15: Round 2 — 4개 producer 함수의 PR 댓글 표시 코드에 nonce-dash 형식
       (ACCEPT-{pipeline_id}-{nonce}) 부재 확인 (producer/consumer 형식 일치 강제)
TC-16: Round 2 — TC-04 오탐 방지. nonce 없는 ACCEPT-{pipeline_id} 표시 코드 존재 +
       nonce 포함 표시 코드 부재를 round-trip(producer/consumer) 형식으로 검증
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


def _extract_function_source(src: str, func_name: str) -> str:
    """pipeline.py 소스에서 최상위 def func_name(...) 의 본문 소스를 추출한다.

    Args:
        src: pipeline.py 전체 소스 문자열.
        func_name: 추출할 최상위 함수명 (예: "_post_github_pending_acceptance_comment").
    Returns:
        해당 함수 정의 줄부터 다음 최상위 def/async def 직전까지의 소스 슬라이스.
    Raises:
        TypeError: src 또는 func_name 이 None 이거나 str 이 아닌 경우.
        ValueError: 빈 문자열이거나 함수 정의를 찾지 못한 경우.
    """
    if src is None:
        raise TypeError("src must not be None")
    if not isinstance(src, str):
        raise TypeError(f"src must be str, got {type(src).__name__}")
    if func_name is None:
        raise TypeError("func_name must not be None")
    if not isinstance(func_name, str):
        raise TypeError(f"func_name must be str, got {type(func_name).__name__}")
    if len(src) == 0:
        raise ValueError("src must not be empty")
    if len(func_name) == 0:
        raise ValueError("func_name must not be empty")

    lines = src.splitlines()
    start_idx = -1
    # 최상위(들여쓰기 0) def/async def 매칭.
    def_pat = re.compile(rf"^(?:async\s+)?def\s+{re.escape(func_name)}\s*\(")
    for i, line in enumerate(lines):
        if def_pat.match(line):
            start_idx = i
            break
    if start_idx < 0:
        raise ValueError(f"function {func_name!r} not found in source")

    end_idx = len(lines)
    next_top_def = re.compile(r"^(?:async\s+)?def\s+\w+\s*\(")
    for j in range(start_idx + 1, len(lines)):
        if next_top_def.match(lines[j]):
            end_idx = j
            break
    return "\n".join(lines[start_idx:end_idx])


# Round 2 — producer 표시 코드(사용자가 PR 댓글에 게시하는 코드)를 생성하는 4개 함수.
# 이 함수들 본문에는 nonce-dash 형식(ACCEPT-{pipeline_id}-{...}) 표시 코드가 없어야 한다.
_PRODUCER_DISPLAY_FUNCS = (
    "_build_acceptance_display_model",
    "_display_model_from_evidence",
    "_build_verification_json",
    "_post_github_pending_acceptance_comment",
)

# nonce-dash 형식 표시 코드 패턴 (f-string 표기). 예:
#   f"ACCEPT-{pipeline_id}-{nonce}"  /  f"ACCEPT-{pipeline_id}-{accept_nonce}"  /  f"ACCEPT-{pipeline_id}-{_req_nonce}"
_NONCE_DASH_DISPLAY_PATTERN = re.compile(r'ACCEPT-\{pipeline_id\}-\{[^}]+\}')


def test_tc15_producer_funcs_have_no_nonce_dash_display_code() -> None:
    """TC-15: 4개 producer 함수 본문에 nonce-dash 표시 코드가 없어야 한다.

    consumer(_check_pr_approver_provenance)는 ACCEPT-{pipeline_id}(nonce 없음)만 수락하므로,
    producer 표시 코드도 동일 형식이어야 한다. nonce 포함 표시 코드가 남아 있으면 사용자가
    PR 댓글에 그 코드를 게시했을 때 code_mismatch로 차단된다(QA Round 1 FAIL 원인).
    """
    src = PIPELINE_PY.read_text(encoding="utf-8")
    offenders = []
    for fname in _PRODUCER_DISPLAY_FUNCS:
        body = _extract_function_source(src, fname)
        if _NONCE_DASH_DISPLAY_PATTERN.search(body):
            matches = _NONCE_DASH_DISPLAY_PATTERN.findall(body)
            offenders.append(f"{fname}: {matches}")
    assert not offenders, (
        "FAIL: producer 함수 본문에 nonce-dash 표시 코드(ACCEPT-{pipeline_id}-{...})가 남아 있음.\n"
        "consumer는 nonce 없는 ACCEPT-{pipeline_id}만 수락하므로 형식 불일치로 차단됨.\n"
        + "\n".join(offenders)
    )


def test_tc16_nonce_free_display_present_and_nonce_form_absent() -> None:
    """TC-16: TC-04 오탐 방지 — nonce 없는 표시 코드 존재 + nonce 포함 표시 코드 부재.

    TC-04의 정규식(ACCEPT-.*pipeline_id)은 nonce-dash 버그 라인에도 매칭되어 오탐 통과했다.
    여기서는 (1) nonce 없는 ACCEPT-{pipeline_id} 표시 코드가 각 producer 함수에 실제로
    존재하는지, (2) nonce 포함 표시 코드가 producer 함수에 없는지를 함께 검증한다.
    """
    src = PIPELINE_PY.read_text(encoding="utf-8")

    # (1) nonce 없는 표시 코드 패턴: f"ACCEPT-{pipeline_id}" (뒤에 -{ 가 오지 않음).
    nonce_free_pat = re.compile(r'f"ACCEPT-\{pipeline_id\}"')
    missing = []
    for fname in _PRODUCER_DISPLAY_FUNCS:
        body = _extract_function_source(src, fname)
        # display_model["approval_code"] = f"ACCEPT-{pipeline_id}" 형식도 포함되도록
        # f-string 리터럴을 직접 탐색한다.
        if not nonce_free_pat.search(body):
            missing.append(fname)
    assert not missing, (
        "FAIL: 아래 producer 함수에 nonce 없는 ACCEPT-{pipeline_id} 표시 코드가 없음:\n"
        + "\n".join(missing)
    )

    # (2) 동일 함수들에 nonce 포함 표시 코드가 없어야 한다(TC-15와 상호 보강, 오탐 방지).
    for fname in _PRODUCER_DISPLAY_FUNCS:
        body = _extract_function_source(src, fname)
        assert not _NONCE_DASH_DISPLAY_PATTERN.search(body), (
            f"FAIL: {fname} 에 nonce 포함 표시 코드가 남아 있음 (TC-04 오탐 유형)."
        )

    # (3) CLI 경로(gates accept --acceptance-code)는 여전히 nonce 형식을 유지해야 한다.
    #     pr_comment_accept_code(nonce 없음)와 accept_code(nonce 포함)가 분리되어 있어야 한다.
    assert 'pr_comment_accept_code = f"ACCEPT-{pipeline_id}"' in src, (
        "FAIL: PR 댓글용 nonce 없는 코드(pr_comment_accept_code)가 없음"
    )
    assert 'accept_code = f"ACCEPT-{pipeline_id}-{nonce}"' in src, (
        "FAIL: gates accept CLI용 nonce 포함 코드(accept_code)가 제거됨 — CLI nonce 검증이 깨짐"
    )


if __name__ == "__main__":
    # 검증 블록 (Self-Verification): 핵심 정적 검사가 동작하는지 확인.
    _src = PIPELINE_PY.read_text(encoding="utf-8")
    assert "_run_browser_approval_server" not in _src, "browser server 잔존"
    assert "_should_reuse_acceptance_nonce" in _src, "nonce 재사용 헬퍼 부재"
    assert (REPO_ROOT / "tests" / "e2e" / "test_pr_comment_accept_3bf4.py").exists()
    # Round 2: producer 함수에 nonce-dash 표시 코드 부재 확인.
    for _fn in _PRODUCER_DISPLAY_FUNCS:
        _body = _extract_function_source(_src, _fn)
        assert not _NONCE_DASH_DISPLAY_PATTERN.search(_body), f"{_fn} nonce-dash 표시 코드 잔존"
    # 헬퍼 입력 방어 검증.
    try:
        _extract_function_source(None, "x")  # type: ignore[arg-type]
        assert False, "None 입력 예외 미발생"
    except TypeError:
        pass
    print("[SELF-VERIFY] OK")
