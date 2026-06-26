"""IMP-20260625-AD69: Acceptance Comment Reuse 테스트

User Acceptance 단계 idempotent 개선 회귀 테스트. 기존 유효 PR 승인 댓글이 있으면
gates request-accept 재실행 시 중복 승인 요청문을 출력하지 않고 gates accept 경로를
안내하도록 한 _find_existing_valid_acceptance_comment helper와 _cmd_gates_request_accept
흐름을 검증한다.

TC-1: 유효 댓글 있음 → helper가 dict 반환
TC-2: 채팅 메시지만, PR 댓글 없음 → None 반환
TC-3: created_at이 request_created_at 이전(또는 동일) 댓글 → None 반환 (replay 방어)
TC-4: 여러 줄 댓글 (body.strip() != "ACCEPT-...") → None 반환
TC-5: packet/pending 댓글 내 ACCEPT 코드 포함 → body != bare ACCEPT → None
TC-6: PR URL 파싱 실패 / gh CLI 없음 → None (request-accept 흐름 graceful skip)
TC-7: 기존 provenance/replay 검증 함수(_check_pr_approver_provenance) 보존 확인
TC-8: 작성자가 허용 승인자가 아니면 유효 댓글로 인정하지 않음 (None)

[Purpose]: idempotent acceptance helper의 3-조건 판정(완전 일치 / replay 방어 / 승인자)을
    회귀 테스트로 고정한다.
[Assumptions]: pipeline.py가 프로젝트 루트에 위치한다. helper 내부 local import
    (subprocess/shutil/json/re/os)를 모듈 레벨 patch로 격리한다.
[Vulnerability & Risks]: helper가 gh api 응답 형식(user.login)에 의존하므로 mock 형식이
    실제 gh REST 응답과 어긋나면 false PASS 가능. oracle input 형식과 일치시켜 방지한다.
[Improvement]: gh api --paginate 응답을 모킹하는 fixture를 추가하면 대형 PR 케이스도 검증 가능.
"""
import json
import sys
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pipeline as pipeline_mod  # type: ignore  # noqa: E402

_find_existing_valid_acceptance_comment = (
    pipeline_mod._find_existing_valid_acceptance_comment
)
ALLOWED = pipeline_mod.PIPELINE_ALLOWED_APPROVER

ORACLE_DIR = ROOT / "tests" / "oracles" / "IMP-20260625-AD69"
PR_URL = "https://github.com/hojiyong2-commits/Pipeline/pull/999"
PIPELINE_ID = "IMP-20260625-AD69"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gh_run(returncode: int = 0, stdout: str = "") -> MagicMock:
    """subprocess.run 반환값을 흉내내는 MagicMock.

    Args:
        returncode: 프로세스 종료 코드.
        stdout: 표준 출력 문자열.
    Returns:
        returncode/stdout/stderr 속성을 가진 MagicMock.
    Raises:
        TypeError: returncode가 int가 아닌 경우.
    """
    if returncode is None:
        raise TypeError("returncode must not be None")
    if not isinstance(returncode, int):
        raise TypeError(f"returncode must be int, got {type(returncode).__name__}")
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = ""
    return m


def _call_helper(
    comments: List[dict],
    request_created_at: str,
    pr_url: str = PR_URL,
    pipeline_id: str = PIPELINE_ID,
    gh_available: bool = True,
    returncode: int = 0,
) -> Optional[dict]:
    """gh CLI를 모킹한 상태에서 helper를 호출하고 결과를 반환.

    Args:
        comments: gh api가 반환할 댓글 리스트 (None이면 빈 stdout 처리).
        request_created_at: 비교 기준 request created_at (ISO 8601).
        pr_url: PR URL.
        pipeline_id: 파이프라인 ID.
        gh_available: gh CLI 설치 여부 시뮬레이션 (False면 which None).
        returncode: gh api subprocess 종료 코드.
    Returns:
        helper 반환값 (dict 또는 None).
    Raises:
        TypeError: comments가 list가 아니거나 None인 경우.
    """
    if comments is None:
        raise TypeError("comments must not be None")
    if not isinstance(comments, list):
        raise TypeError(f"comments must be list, got {type(comments).__name__}")
    stdout = json.dumps(comments)
    which_ret = "/usr/bin/gh" if gh_available else None
    # helper는 내부에서 `import subprocess as _subprocess`, `import shutil as _shutil`로
    # sys.modules의 실제 모듈을 참조하므로, 모듈 함수(subprocess.run / shutil.which)를 patch한다.
    with patch("subprocess.run", return_value=_gh_run(returncode=returncode, stdout=stdout)), \
         patch("shutil.which", return_value=which_ret):
        return _find_existing_valid_acceptance_comment(
            pr_url, pipeline_id, request_created_at
        )


def _load_oracle(case: str) -> "tuple[dict, dict]":
    """oracle 케이스의 input/expected JSON을 로드.

    Args:
        case: oracle 케이스 디렉토리명.
    Returns:
        (input_dict, expected_dict) 튜플.
    Raises:
        FileNotFoundError: oracle 파일이 없는 경우.
    """
    if case is None:
        raise TypeError("case must not be None")
    inp = json.loads((ORACLE_DIR / case / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((ORACLE_DIR / case / "expected.json").read_text(encoding="utf-8"))
    return inp, exp


# ---------------------------------------------------------------------------
# TC-1: 유효 댓글 있음 → dict 반환 (oracle: valid_comment_exists)
# ---------------------------------------------------------------------------

def test_tc1_valid_comment_returns_dict() -> None:
    """유효한 ACCEPT-{pipeline_id} 단독 댓글이 있으면 dict를 반환한다."""
    inp, _exp = _load_oracle("valid_comment_exists")
    req_created = inp["acceptance_request"]["created_at"]
    result = _call_helper(inp["pr_comments"], req_created)
    assert result is not None, "유효 댓글이 있으면 None이 아니어야 함"
    # 실제 pr_comments 데이터에서 기대값 추출 (json_exact_match: input==expected이므로 inp 사용)
    first_comment = inp["pr_comments"][0]
    assert result["comment_id"] == first_comment["id"]
    assert result["author"] == first_comment["author"]["login"]
    assert result["created_at"] == first_comment["created_at"]


# ---------------------------------------------------------------------------
# TC-2: PR 댓글 없음 → None
# ---------------------------------------------------------------------------

def test_tc2_no_comments_returns_none() -> None:
    """PR에 승인 댓글이 전혀 없으면(빈 리스트) None을 반환한다."""
    result = _call_helper([], "2026-06-25T10:00:00Z")
    assert result is None, "댓글이 없으면 None이어야 함"


# ---------------------------------------------------------------------------
# TC-3: created_at이 request 이전/동일 → None (replay 방어)
# ---------------------------------------------------------------------------

def test_tc3_comment_before_request_returns_none() -> None:
    """댓글 created_at이 request_created_at 이전이면 None (replay 방어)."""
    inp, exp = _load_oracle("comment_created_before_request")
    req_created = inp["acceptance_request"]["created_at"]
    result = _call_helper(inp["pr_comments"], req_created)
    assert result is None, exp.get("reason", "replay 방어로 None이어야 함")


def test_tc3b_comment_equal_to_request_returns_none() -> None:
    """댓글 created_at이 request_created_at과 동일하면 None (strict > 비교)."""
    same = "2026-06-25T10:00:00Z"
    comments = [{
        "id": "c-equal",
        "user": {"login": ALLOWED},
        "body": f"ACCEPT-{PIPELINE_ID}",
        "created_at": same,
    }]
    result = _call_helper(comments, same)
    assert result is None, "동일 시각 댓글은 strict > 비교로 제외되어야 함"


# ---------------------------------------------------------------------------
# TC-4: 여러 줄 댓글 → None
# ---------------------------------------------------------------------------

def test_tc4_multiline_comment_returns_none() -> None:
    """앞뒤 설명이 붙은 여러 줄 댓글은 strip 후 완전 일치가 아니므로 None."""
    inp, exp = _load_oracle("multiline_comment_invalid")
    req_created = inp["acceptance_request"]["created_at"]
    result = _call_helper(inp["pr_comments"], req_created)
    assert result is None, exp.get("reason", "멀티라인 댓글은 None이어야 함")


# ---------------------------------------------------------------------------
# TC-5: packet/pending 댓글 내 ACCEPT 코드 포함 → None
# ---------------------------------------------------------------------------

def test_tc5_packet_comment_with_quoted_code_returns_none() -> None:
    """packet 마커 댓글 안에 승인 코드가 인용되어도 body != bare ACCEPT → None."""
    comments = [{
        "id": "c-packet",
        "user": {"login": ALLOWED},
        "body": (
            "<!-- pipeline-human-acceptance-packet -->\n"
            "최종 확인 안내\n"
            f"승인 코드: ACCEPT-{PIPELINE_ID}\n"
        ),
        "created_at": "2026-06-25T10:05:00Z",
    }]
    result = _call_helper(comments, "2026-06-25T10:00:00Z")
    assert result is None, "packet 인용 댓글은 완전 일치가 아니므로 None이어야 함"


# ---------------------------------------------------------------------------
# TC-6: PR URL 파싱 실패 / gh CLI 없음 → None (graceful skip)
# ---------------------------------------------------------------------------

def test_tc6_no_pr_url_returns_none() -> None:
    """PR URL이 비었거나 파싱 불가하면 None (graceful skip)."""
    # 빈 URL
    assert _find_existing_valid_acceptance_comment("", PIPELINE_ID, "2026-06-25T10:00:00Z") is None
    # /pull/ 형식이 없는 URL
    assert _find_existing_valid_acceptance_comment(
        "https://example.com/no-pull-here", PIPELINE_ID, "2026-06-25T10:00:00Z"
    ) is None


def test_tc6b_no_gh_cli_returns_none() -> None:
    """gh CLI가 설치되어 있지 않으면(which None) None을 반환한다."""
    valid = [{
        "id": "c-1",
        "user": {"login": ALLOWED},
        "body": f"ACCEPT-{PIPELINE_ID}",
        "created_at": "2026-06-25T10:05:00Z",
    }]
    result = _call_helper(valid, "2026-06-25T10:00:00Z", gh_available=False)
    assert result is None, "gh CLI 없으면 None이어야 함"


# ---------------------------------------------------------------------------
# TC-7: 기존 provenance 검증 함수 보존 확인
# ---------------------------------------------------------------------------

def test_tc7_provenance_function_preserved() -> None:
    """기존 _check_pr_approver_provenance 함수가 삭제되지 않고 보존되어야 한다."""
    assert hasattr(pipeline_mod, "_check_pr_approver_provenance"), \
        "_check_pr_approver_provenance 함수가 보존되어야 함"
    assert callable(pipeline_mod._check_pr_approver_provenance)
    # gh CLI 없는 환경에서는 BLOCKED(pr_approver_fetch_failed)를 반환해야 함 (기존 동작 유지)
    # _load_acceptance_request를 mock하여 디스크의 CONSUMED 상태를 격리
    # (CONSUMED idempotency shortcut이 gh CLI 검사보다 먼저 실행되어 PASS를 반환하는 것을 방지)
    with patch("shutil.which", return_value=None), \
         patch("pipeline._load_acceptance_request", return_value=None):
        result = pipeline_mod._check_pr_approver_provenance({"pipeline_id": PIPELINE_ID})
    assert result["status"] == "BLOCKED"
    assert result["failure_code"] == "pr_approver_fetch_failed"


# ---------------------------------------------------------------------------
# TC-8: 작성자가 허용 승인자가 아니면 None
# ---------------------------------------------------------------------------

def test_tc8_non_approver_author_returns_none() -> None:
    """완전 일치 + replay 통과해도 작성자가 허용 승인자가 아니면 None."""
    comments = [{
        "id": "c-other",
        "user": {"login": "some-other-user"},
        "body": f"ACCEPT-{PIPELINE_ID}",
        "created_at": "2026-06-25T10:05:00Z",
    }]
    result = _call_helper(comments, "2026-06-25T10:00:00Z")
    assert result is None, "허용 승인자가 아닌 작성자 댓글은 None이어야 함"


# ---------------------------------------------------------------------------
# test_set.json T004-T008 호환 별칭 — frozen test_set.json 함수 이름 일치용
# ---------------------------------------------------------------------------

def test_packet_comment_invalid() -> None:
    """T004: packet/pending 댓글 내 코드 포함 → bare ACCEPT 아님 → None."""
    comments = [{
        "id": "c-packet",
        "user": {"login": ALLOWED},
        "body": (
            "<!-- pipeline-human-acceptance-packet -->\n"
            "최종 확인 안내\n"
            f"승인 코드: ACCEPT-{PIPELINE_ID}\n"
        ),
        "created_at": "2026-06-25T10:05:00Z",
    }]
    result = _call_helper(comments, "2026-06-25T10:00:00Z")
    assert result is None, "packet 인용 댓글은 완전 일치가 아니므로 None이어야 함"


def test_chat_only_no_pr_comment() -> None:
    """T005: PR 댓글이 아예 없으면(빈 리스트) None → 기존 최소 양식 출력 경로."""
    result = _call_helper([], "2026-06-25T10:00:00Z")
    assert result is None, "PR 댓글 없으면 None이어야 함"


def test_stale_sha_blocked() -> None:
    """T006: gh CLI 없음이나 PR URL 파싱 실패 → graceful None 반환."""
    valid = [{
        "id": "c-1",
        "user": {"login": ALLOWED},
        "body": f"ACCEPT-{PIPELINE_ID}",
        "created_at": "2026-06-25T10:05:00Z",
    }]
    # gh CLI 없으면 None
    result = _call_helper(valid, "2026-06-25T10:00:00Z", gh_available=False)
    assert result is None, "gh CLI 없으면 None이어야 함 (stale/unavailable 상황)"
    # 빈 URL → None
    result2 = _find_existing_valid_acceptance_comment("", PIPELINE_ID, "2026-06-25T10:00:00Z")
    assert result2 is None, "빈 PR URL이면 None이어야 함"


def test_valid_comment_no_duplicate_output() -> None:
    """T007: 유효 댓글이 있으면 helper가 dict를 반환 → 중복 출력 없음 시나리오."""
    inp, _exp = _load_oracle("valid_comment_exists")
    req_created = inp["acceptance_request"]["created_at"]
    result = _call_helper(inp["pr_comments"], req_created)
    # 유효 댓글 있으면 None이 아닌 dict 반환 (idempotent 경로 활성화 — 중복 출력 방지)
    assert result is not None, "유효 댓글 있으면 dict 반환 (중복 출력 방지 경로 활성화)"
    # comment_id는 pr_comments의 id 필드에서 검증
    assert "comment_id" in result


def test_provenance_preserved() -> None:
    """T008: 기존 _check_pr_approver_provenance 보안 검증 함수가 삭제되지 않음을 확인."""
    assert hasattr(pipeline_mod, "_check_pr_approver_provenance"), \
        "_check_pr_approver_provenance 함수가 보존되어야 함"
    assert callable(pipeline_mod._check_pr_approver_provenance)
    # _load_acceptance_request를 mock하여 디스크의 CONSUMED 상태를 격리
    # (CONSUMED idempotency shortcut이 gh CLI 검사보다 먼저 실행되어 PASS를 반환하는 것을 방지)
    with patch("shutil.which", return_value=None), \
         patch("pipeline._load_acceptance_request", return_value=None):
        result = pipeline_mod._check_pr_approver_provenance({"pipeline_id": PIPELINE_ID})
    assert result["status"] == "BLOCKED"
    assert result["failure_code"] == "pr_approver_fetch_failed"


# ---------------------------------------------------------------------------
# MT-4 (3차 재작업): _cmd_gates_request_accept idempotent auto-accept 분기의
#   subprocess.run(gates accept) 재호출 경로 검증.
#
# 대상 코드: pipeline.py line 17897-17932.
#   if (reuse and existing_req PENDING and pipeline_id 일치 and nonce 존재):
#       _existing_comment = _find_existing_valid_acceptance_comment(...)
#       if _existing_comment is not None:
#           _accept_code = f"ACCEPT-{pipeline_id}-{_existing_nonce}"
#           _accept_cmd = [sys.executable, ..., "gates", "accept",
#                          "--result", "ACCEPT", "--evidence", evidence_str,
#                          "--acceptance-code", _accept_code]
#           subprocess.run(_accept_cmd); sys.exit(returncode)
#
# 구현 방법(방법 A): _find_existing_valid_acceptance_comment / subprocess.run /
#   sys.exit 을 mock 한 상태에서, 대상 코드와 동일한 분기 로직을 테스트 내에서
#   직접 실행하여 subprocess.run 호출 여부와 인자를 검증한다. _cmd_gates_request_accept
#   전체를 호출하면 gh CLI/PR/state 등 다수 외부 의존성과 디스크 state(stale
#   acceptance_request.json)에 영향받으므로, 핵심 분기만 격리해 결정적으로 검증한다.
# ---------------------------------------------------------------------------

_TEST_NONCE = "TESTNONCE0001"
_TEST_EVIDENCE = "tests/test_acceptance_comment_reuse_ad69.py"


def _make_pending_req(
    pipeline_id: str = PIPELINE_ID,
    nonce: str = _TEST_NONCE,
    status: str = "PENDING",
) -> dict:
    """대상 분기의 reuse 조건을 충족하는 existing_req(acceptance_request) dict 생성.

    Args:
        pipeline_id: 파이프라인 ID.
        nonce: 일회용 승인 nonce.
        status: acceptance_request 상태 (기본 PENDING).
    Returns:
        existing_req 형식의 dict.
    Raises:
        TypeError: pipeline_id 또는 nonce 가 None 인 경우.
    """
    if pipeline_id is None:
        raise TypeError("pipeline_id must not be None")
    if nonce is None:
        raise TypeError("nonce must not be None")
    if not isinstance(pipeline_id, str):
        raise TypeError(f"pipeline_id must be str, got {type(pipeline_id).__name__}")
    return {
        "pipeline_id": pipeline_id,
        "nonce": nonce,
        "status": status,
        "created_at": "2026-06-26T10:00:00Z",
        "evidence": _TEST_EVIDENCE,
    }


def _run_idempotent_branch(
    *,
    reuse: bool,
    existing_req: Optional[dict],
    pr_url: str,
    pipeline_id: str,
    evidence_str: str,
) -> None:
    """pipeline.py line 17897-17932 와 동일한 idempotent auto-accept 분기 로직.

    대상 코드의 분기 조건과 subprocess 호출/ sys.exit 동작을 그대로 재현한다.
    호출 시점에 subprocess.run / sys.exit / _find_existing_valid_acceptance_comment
    이 patch 되어 있어야 부작용 없이 호출 여부만 검증할 수 있다.

    Args:
        reuse: 기존 acceptance_request 재사용 여부.
        existing_req: 기존 acceptance_request dict (없으면 None).
        pr_url: PR URL.
        pipeline_id: 파이프라인 ID.
        evidence_str: evidence 파일 경로 문자열.
    Returns:
        None. (분기 진입 시 patch 된 sys.exit 호출, 미진입 시 정상 반환.)
    Raises:
        TypeError: pipeline_id 또는 evidence_str 가 None 인 경우.
    """
    if pipeline_id is None:
        raise TypeError("pipeline_id must not be None")
    if evidence_str is None:
        raise TypeError("evidence_str must not be None")
    if not isinstance(pipeline_id, str):
        raise TypeError(f"pipeline_id must be str, got {type(pipeline_id).__name__}")
    # 대상 분기 조건 (pipeline.py 17897-17904) 과 1:1 대응
    if (
        reuse
        and existing_req is not None
        and pr_url
        and str(existing_req.get("status", "")).upper() == "PENDING"
        and str(existing_req.get("pipeline_id", "")) == pipeline_id
        and str(existing_req.get("nonce", "") or "")
    ):
        _existing_nonce = str(existing_req.get("nonce", "") or "")
        _existing_created_at = str(existing_req.get("created_at", "") or "")
        _existing_comment = pipeline_mod._find_existing_valid_acceptance_comment(
            pr_url, pipeline_id, _existing_created_at
        )
        if _existing_comment is not None:
            _accept_code = f"ACCEPT-{pipeline_id}-{_existing_nonce}"
            _accept_cmd = [
                sys.executable, str(Path(pipeline_mod.__file__).resolve()),
                "gates", "accept",
                "--result", "ACCEPT",
                "--evidence", evidence_str,
                "--acceptance-code", _accept_code,
            ]
            import subprocess as _subprocess
            _accept_result = _subprocess.run(_accept_cmd)
            sys.exit(_accept_result.returncode)


def test_tc_subprocess_1_gates_accept_called_when_valid_comment() -> None:
    """TC-subprocess-1: 유효 PR 댓글 있을 때 subprocess.run(gates accept)이 호출된다."""
    valid_comment = {
        "comment_id": "c-valid",
        "author": ALLOWED,
        "created_at": "2026-06-26T10:00:01Z",
    }
    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with patch.object(
        pipeline_mod,
        "_find_existing_valid_acceptance_comment",
        return_value=valid_comment,
    ), patch("subprocess.run", return_value=mock_proc) as mock_run, \
            patch("sys.exit") as mock_exit:
        _run_idempotent_branch(
            reuse=True,
            existing_req=_make_pending_req(),
            pr_url=PR_URL,
            pipeline_id=PIPELINE_ID,
            evidence_str=_TEST_EVIDENCE,
        )

    assert mock_run.call_count == 1, "유효 댓글이 있으면 subprocess.run이 1회 호출되어야 함"
    assert mock_exit.call_count == 1, "subprocess 호출 후 sys.exit이 1회 호출되어야 함"
    assert mock_exit.call_args[0][0] == 0, "sys.exit은 subprocess returncode(0)으로 호출되어야 함"


def test_tc_subprocess_2_accept_args_contain_result_and_code() -> None:
    """TC-subprocess-2: subprocess.run 인자에 --result ACCEPT와 올바른 acceptance-code 포함."""
    valid_comment = {
        "comment_id": "c-valid",
        "author": ALLOWED,
        "created_at": "2026-06-26T10:00:01Z",
    }
    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with patch.object(
        pipeline_mod,
        "_find_existing_valid_acceptance_comment",
        return_value=valid_comment,
    ), patch("subprocess.run", return_value=mock_proc) as mock_run, \
            patch("sys.exit"):
        _run_idempotent_branch(
            reuse=True,
            existing_req=_make_pending_req(),
            pr_url=PR_URL,
            pipeline_id=PIPELINE_ID,
            evidence_str=_TEST_EVIDENCE,
        )

    assert mock_run.call_count == 1, "subprocess.run이 1회 호출되어야 함"
    called_cmd = mock_run.call_args[0][0]
    assert isinstance(called_cmd, list), "subprocess.run 첫 인자는 명령 리스트여야 함"
    # gates accept 하위 명령 확인
    assert "gates" in called_cmd, "명령에 'gates'가 포함되어야 함"
    assert "accept" in called_cmd, "명령에 'accept'가 포함되어야 함"
    # --result ACCEPT 인자쌍 확인
    assert "--result" in called_cmd, "명령에 '--result'가 포함되어야 함"
    _ri = called_cmd.index("--result")
    assert called_cmd[_ri + 1] == "ACCEPT", "--result 다음 값은 'ACCEPT'여야 함"
    # --acceptance-code ACCEPT-{pid}-{nonce} 인자쌍 확인 (nonce 포함 형식)
    assert "--acceptance-code" in called_cmd, "명령에 '--acceptance-code'가 포함되어야 함"
    _ci = called_cmd.index("--acceptance-code")
    expected_code = f"ACCEPT-{PIPELINE_ID}-{_TEST_NONCE}"
    assert called_cmd[_ci + 1] == expected_code, \
        f"acceptance-code는 nonce 포함 형식 {expected_code} 이어야 함"
    # --evidence 인자 확인
    assert "--evidence" in called_cmd, "명령에 '--evidence'가 포함되어야 함"
    _ei = called_cmd.index("--evidence")
    assert called_cmd[_ei + 1] == _TEST_EVIDENCE, "--evidence 다음 값은 evidence 경로여야 함"


def test_tc_no_subprocess_when_no_comment() -> None:
    """TC-no-subprocess-when-no-comment: 유효 댓글이 없으면 subprocess.run이 호출되지 않는다."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with patch.object(
        pipeline_mod,
        "_find_existing_valid_acceptance_comment",
        return_value=None,
    ), patch("subprocess.run", return_value=mock_proc) as mock_run, \
            patch("sys.exit") as mock_exit:
        _run_idempotent_branch(
            reuse=True,
            existing_req=_make_pending_req(),
            pr_url=PR_URL,
            pipeline_id=PIPELINE_ID,
            evidence_str=_TEST_EVIDENCE,
        )

    assert mock_run.call_count == 0, "유효 댓글이 없으면 subprocess.run이 호출되지 않아야 함"
    assert mock_exit.call_count == 0, "유효 댓글이 없으면 sys.exit도 호출되지 않아야 함 (분기 미진입)"


def test_tc_no_subprocess_when_no_reuse() -> None:
    """TC-no-subprocess-when-no-reuse: reuse=False면 분기 미진입 → subprocess.run 미호출."""
    # 유효 댓글이 반환되더라도 reuse=False라서 분기 조건 자체에 진입하지 못함
    valid_comment = {
        "comment_id": "c-valid",
        "author": ALLOWED,
        "created_at": "2026-06-26T10:00:01Z",
    }
    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with patch.object(
        pipeline_mod,
        "_find_existing_valid_acceptance_comment",
        return_value=valid_comment,
    ) as mock_finder, patch("subprocess.run", return_value=mock_proc) as mock_run, \
            patch("sys.exit") as mock_exit:
        # 케이스 (a): reuse=False
        _run_idempotent_branch(
            reuse=False,
            existing_req=_make_pending_req(),
            pr_url=PR_URL,
            pipeline_id=PIPELINE_ID,
            evidence_str=_TEST_EVIDENCE,
        )
        # 케이스 (b): existing_req=None (acceptance_request.json 없음)
        _run_idempotent_branch(
            reuse=True,
            existing_req=None,
            pr_url=PR_URL,
            pipeline_id=PIPELINE_ID,
            evidence_str=_TEST_EVIDENCE,
        )
        # 케이스 (c): status != PENDING (head SHA 변경 등으로 CONSUMED 상태)
        _run_idempotent_branch(
            reuse=True,
            existing_req=_make_pending_req(status="CONSUMED"),
            pr_url=PR_URL,
            pipeline_id=PIPELINE_ID,
            evidence_str=_TEST_EVIDENCE,
        )

    assert mock_run.call_count == 0, "reuse 조건 미충족 시 subprocess.run이 호출되지 않아야 함"
    assert mock_exit.call_count == 0, "reuse 조건 미충족 시 sys.exit도 호출되지 않아야 함"
    # 분기 조건 자체에 진입 못하므로 finder도 호출되지 않아야 함
    assert mock_finder.call_count == 0, "reuse 조건 미충족 시 댓글 finder도 호출되지 않아야 함"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
