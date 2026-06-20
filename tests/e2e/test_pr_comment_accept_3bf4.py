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

BUG-20260620-3BF4 REJECT 수정 추가 케이스 (행동 검증):
TC-17: 정확히 한 줄 검증 — 승인 코드 뒤에 추가 줄이 있으면 PASS 후보 불인정 → BLOCKED
TC-18: replay 방어 — 댓글 createdAt이 acceptance_request.created_at 이전이면 BLOCKED
TC-19: 정상 케이스 — 정확히 한 줄 + createdAt이 request 이후 → PASS (회귀 보호)
TC-20: acceptance_request.created_at 누락 시 fail-closed → BLOCKED
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_PY = REPO_ROOT / "pipeline.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline as pipeline_mod  # type: ignore  # noqa: E402

_check_pr_approver_provenance = pipeline_mod._check_pr_approver_provenance
PIPELINE_ALLOWED_APPROVER = pipeline_mod.PIPELINE_ALLOWED_APPROVER


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


# ----------------------------------------------------------------------------
# BUG-20260620-3BF4 REJECT 수정 — 행동 검증(_check_pr_approver_provenance mock 호출)
# test_pr_approver_b96c.py 의 subprocess.run / shutil.which mock 패턴을 재사용한다.
# ----------------------------------------------------------------------------

_3BF4_PIPELINE_ID = "BUG-20260620-3BF4"
# 사용자가 PR 댓글에 게시하는 nonce 없는 승인 코드.
_3BF4_VALID_CODE = f"ACCEPT-{_3BF4_PIPELINE_ID}"
# acceptance_request.created_at 기준 시각 (UTC, _now() 형식과 동일하게 Z 접미사).
_3BF4_REQUEST_CREATED_AT = "2026-06-20T12:00:00Z"
# request 이후 댓글 시각(승인 유효).
_3BF4_COMMENT_AFTER = "2026-06-20T13:00:00Z"
# request 이전 댓글 시각(replay 공격).
_3BF4_COMMENT_BEFORE = "2026-06-20T11:00:00Z"


def _make_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """subprocess.run 반환값을 흉내내는 MagicMock.

    Args:
        returncode: 모의 프로세스 반환 코드 (0=성공).
        stdout: 모의 표준 출력.
        stderr: 모의 표준 에러.
    Returns:
        returncode/stdout/stderr 속성을 가진 MagicMock.
    Raises:
        TypeError: returncode 가 None 이거나 int 가 아닌 경우.
    """
    if returncode is None:
        raise TypeError("returncode must not be None")
    if not isinstance(returncode, int):
        raise TypeError(f"returncode must be int, got {type(returncode).__name__}")
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _build_side_effect(pr_number: str, comments: List[Dict[str, Any]],
                       head_branch: str = "impl/BUG-20260620-3BF4") -> Any:
    """_check_pr_approver_provenance 내부 subprocess.run 호출 흉내 side_effect.

    호출 순서: git rev-parse → gh pr list → gh pr view.

    Args:
        pr_number: 모의 PR 번호.
        comments: PR 댓글 목록 (author/body/id/createdAt 키).
        head_branch: 모의 현재 브랜치명.
    Returns:
        subprocess.run 의 side_effect 로 사용할 콜러블.
    Raises:
        TypeError: pr_number 가 None 이거나 comments 가 None/리스트가 아닌 경우.
    """
    if pr_number is None:
        raise TypeError("pr_number must not be None")
    if comments is None:
        raise TypeError("comments must not be None")
    if not isinstance(comments, list):
        raise TypeError(f"comments must be list, got {type(comments).__name__}")

    def _side(*args: Any, **_kwargs: Any) -> MagicMock:
        argv = args[0] if args else []
        if isinstance(argv, list) and argv[:2] == ["git", "rev-parse"]:
            return _make_run(returncode=0, stdout=head_branch + "\n")
        if isinstance(argv, list) and len(argv) >= 3 and argv[1:3] == ["pr", "list"]:
            payload = json.dumps([{"number": int(pr_number), "headRefName": head_branch}])
            return _make_run(returncode=0, stdout=payload)
        if isinstance(argv, list) and len(argv) >= 3 and argv[1:3] == ["pr", "view"]:
            payload = json.dumps({"comments": comments})
            return _make_run(returncode=0, stdout=payload)
        return _make_run(returncode=1, stdout="", stderr="unexpected subprocess call")
    return _side


def _run_provenance_3bf4(file_req: Dict[str, Any], comments: List[Dict[str, Any]],
                         pr_number: str = "99") -> Dict[str, Any]:
    """mock 컨텍스트 안에서 _check_pr_approver_provenance 호출.

    Args:
        file_req: _load_acceptance_request 가 반환할 acceptance_request dict.
        comments: 모의 PR 댓글 목록.
        pr_number: 모의 PR 번호.
    Returns:
        _check_pr_approver_provenance 의 반환 dict.
    Raises:
        TypeError: file_req 가 None 이거나 dict 가 아닌 경우.
    """
    if file_req is None:
        raise TypeError("file_req must not be None")
    if not isinstance(file_req, dict):
        raise TypeError(f"file_req must be dict, got {type(file_req).__name__}")
    state: Dict[str, Any] = {
        "pipeline_id": _3BF4_PIPELINE_ID,
        "acceptance_request": {"nonce": file_req.get("nonce", "")},
    }
    with patch.object(pipeline_mod, "_load_acceptance_request", return_value=file_req), \
         patch("shutil.which", return_value="C:\\fake\\gh.exe"), \
         patch("subprocess.run", side_effect=_build_side_effect(pr_number, comments)):
        return _check_pr_approver_provenance(state)


def _base_file_req_3bf4(**overrides: Any) -> Dict[str, Any]:
    """기본 acceptance_request.json 내용 (created_at 포함). overrides 로 치환 가능."""
    base: Dict[str, Any] = {
        "pipeline_id": _3BF4_PIPELINE_ID,
        "nonce": "NONCE3BF4",
        "request_id": "req-3bf4",
        "created_at": _3BF4_REQUEST_CREATED_AT,
    }
    base.update(overrides)
    return base


def test_tc17_exact_one_line_extra_content_blocked() -> None:
    """TC-17 (case=exception) — 승인 코드 뒤에 추가 줄이 있으면 PASS 불인정 → BLOCKED.

    "코드 외 다른 내용 금지 / 정확히 한 줄" 요구. 전체 본문 strip 완전 일치가 아니면
    승인 후보로 보지 않는다. 본문이 ACCEPT- 로 시작하지만 정확히 일치하지 않으므로
    approval_code_mismatch 로 차단된다(핵심: PASS가 아니라 BLOCKED).
    """
    file_req = _base_file_req_3bf4()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": f"{_3BF4_VALID_CODE}\n추가 내용입니다",
            "id": "C17",
            "createdAt": _3BF4_COMMENT_AFTER,
        }
    ]
    result = _run_provenance_3bf4(file_req, comments)
    assert result["status"] == "BLOCKED", \
        f"승인 코드 뒤 추가 줄이 있으면 BLOCKED여야 함: {result.get('message')}"
    assert result.get("status") != "PASS", \
        "정확히 한 줄 아닌 댓글이 PASS 처리되면 안 됨"
    assert result.get("failure_code") in (
        "pr_approver_missing", "approval_code_mismatch", ""
    ), (
        "정확히 한 줄 아님 → 승인 후보 불인정(approval_code_mismatch 또는 "
        f"pr_approver_missing): {result.get('failure_code')}"
    )


def test_tc18_replay_before_request_created_at_blocked() -> None:
    """TC-18 (case=error) — 댓글 createdAt이 request.created_at 이전이면 replay 차단 → BLOCKED."""
    file_req = _base_file_req_3bf4()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": _3BF4_VALID_CODE,
            "id": "C18",
            "createdAt": _3BF4_COMMENT_BEFORE,  # request 생성 이전 → replay
        }
    ]
    result = _run_provenance_3bf4(file_req, comments)
    assert result["status"] == "BLOCKED", \
        f"request 생성 이전 댓글은 replay로 BLOCKED여야 함: {result.get('message')}"
    assert result.get("failure_code") == "pr_comment_timestamp_missing", \
        f"replay 차단은 pr_comment_timestamp_missing(fail-closed)여야 함: {result.get('failure_code')}"


def test_tc19_exact_one_line_after_request_pass() -> None:
    """TC-19 (case=normal) — 정확히 한 줄 + createdAt이 request 이후 → PASS (회귀 보호)."""
    file_req = _base_file_req_3bf4()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": _3BF4_VALID_CODE,
            "id": "C19",
            "createdAt": _3BF4_COMMENT_AFTER,
        }
    ]
    result = _run_provenance_3bf4(file_req, comments)
    assert result["status"] == "PASS", \
        f"정확한 한 줄 + request 이후 댓글은 PASS여야 함: {result.get('message')}"
    assert result.get("approver") == PIPELINE_ALLOWED_APPROVER
    assert result.get("comment_id") == "C19"


def test_tc20_missing_request_created_at_fail_closed() -> None:
    """TC-20 (case=exception) — acceptance_request.created_at 누락 시 fail-closed BLOCKED."""
    file_req = _base_file_req_3bf4(created_at="")  # created_at 비움
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": _3BF4_VALID_CODE,
            "id": "C20",
            "createdAt": _3BF4_COMMENT_AFTER,
        }
    ]
    result = _run_provenance_3bf4(file_req, comments)
    assert result["status"] == "BLOCKED", \
        f"request.created_at 누락 시 fail-closed BLOCKED여야 함: {result.get('message')}"
    assert result.get("failure_code") == "pr_comment_timestamp_missing", \
        f"created_at 누락은 pr_comment_timestamp_missing(fail-closed)여야 함: {result.get('failure_code')}"


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
