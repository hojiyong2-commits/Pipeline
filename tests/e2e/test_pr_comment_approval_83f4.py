"""tests/e2e/test_pr_comment_approval_83f4.py

BUG-20260619-F41F MT-2: _check_pr_approver_provenance 댓글 timestamp 처리 회귀 테스트.

[Purpose]: User Acceptance Provenance Gate(`_check_pr_approver_provenance`)가
    PR 댓글의 timestamp를 (1) created_at/createdAt 양방향으로 읽고, (2) timestamp
    누락 시 fail-closed(해당 댓글 skip), (3) timestamp 파싱 실패 시 fail-closed,
    (4) 발급 시각 이전 과거 댓글은 pr_comment_too_old로 차단하는지 고정한다.
    BUG-20260618-83F4 후속 — REJECT 사유(createdAt 미지원, timestamp fail-open)를 수정한다.
[Assumptions]: pipeline.py가 프로젝트 루트에 위치한다. _load_acceptance_request 와
    subprocess.run / shutil.which 를 함수 단위로 mock 한다. tests/test_pr_approver_b96c.py
    의 _build_subprocess_side_effect 패턴을 재사용하되, 본 파일에서는 댓글 timestamp를
    그대로 보존(자동 주입 없음)하여 fail-closed 경로를 정확히 검증한다.
[Vulnerability & Risks]: gh CLI / subprocess.run mock 표면이 함수 내부 local import
    (_subprocess, _shutil 별칭)과 분리되어 있어, 표준 라이브러리 심볼(subprocess.run,
    shutil.which)을 직접 patch 해야 한다. patch 대상 누락 시 실제 gh가 호출되어
    의도와 다른 결과가 나올 수 있다 — 본 파일은 표준 라이브러리 심볼을 직접 patch 한다.
[Improvement]: 시간이 더 있다면 oracle JSON(tests/oracles/BUG-20260619-F41F/*)을
    parametrize 로 직접 로드해 테스트와 oracle 의 입력/기대를 단일 SSoT 로 통합한다.
"""
import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pipeline as pipeline_mod  # type: ignore  # noqa: E402

_check_pr_approver_provenance = pipeline_mod._check_pr_approver_provenance
PIPELINE_ALLOWED_APPROVER = pipeline_mod.PIPELINE_ALLOWED_APPROVER

# 본 테스트 전체에서 사용하는 가상 pipeline_id / nonce.
# MT-3 이후 게시/표시 승인 코드는 nonce 없는 ACCEPT-<pipeline_id> 형식이며,
# provenance 검증도 이 형식을 PASS 후보로 받아들인다.
_PIPELINE_ID = "BUG-20260619-F41F"
_VALID_NONCE = "TESTNONC"
_NONCELESS_CODE = f"ACCEPT-{_PIPELINE_ID}"
_REQ_CREATED_AT = "2026-06-19T10:00:00Z"
_COMMENT_NEWER = "2026-06-19T11:00:00Z"
_COMMENT_OLDER = "2026-06-19T09:00:00Z"


def _make_gh_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
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


def _build_subprocess_side_effect(pr_number: str, comments: List[Dict[str, Any]],
                                  head_branch: str = "main") -> Any:
    """_check_pr_approver_provenance 내부 subprocess.run 호출을 흉내내는 side_effect.

    호출 순서:
      1. git rev-parse --abbrev-ref HEAD       → head_branch 반환
      2. gh pr list --state open --json ...    → [{number, headRefName}] 반환
      3. gh pr view <pr> --json comments       → {comments: [...]} 반환

    본 헬퍼는 댓글 timestamp 를 자동 주입하지 않는다. fail-closed 경로(누락/파싱실패)를
    정확히 검증하기 위해 입력 comments 를 그대로 전달한다.

    Args:
        pr_number: 모의 PR 번호.
        comments: PR 댓글 목록 (각 항목은 author/body/id/created_at 키를 가진 dict).
        head_branch: 모의 현재 브랜치명.
    Returns:
        subprocess.run 의 side_effect 로 사용할 콜러블.
    Raises:
        TypeError: pr_number 가 None 이거나, comments 가 None/리스트가 아닌 경우.
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
            return _make_gh_run(returncode=0, stdout=head_branch + "\n")
        if isinstance(argv, list) and len(argv) >= 3 and argv[1:3] == ["pr", "list"]:
            payload = json.dumps([
                {"number": int(pr_number), "headRefName": head_branch}
            ])
            return _make_gh_run(returncode=0, stdout=payload)
        if isinstance(argv, list) and len(argv) >= 3 and argv[1:3] == ["pr", "view"]:
            payload = json.dumps({"comments": comments})
            return _make_gh_run(returncode=0, stdout=payload)
        return _make_gh_run(returncode=1, stdout="", stderr="unexpected subprocess call")
    return _side


def _run_provenance(state: Dict[str, Any], file_req: Any,
                    comments: List[Dict[str, Any]], pr_number: str = "100") -> Dict[str, Any]:
    """공통 실행 헬퍼: mock 컨텍스트 안에서 _check_pr_approver_provenance 호출.

    Args:
        state: 파이프라인 상태 dict.
        file_req: _load_acceptance_request 가 반환할 값 (dict 또는 None).
        comments: 모의 PR 댓글 목록.
        pr_number: 모의 PR 번호.
    Returns:
        _check_pr_approver_provenance 의 반환 dict.
    Raises:
        TypeError: state 가 None 이거나 dict 가 아닌 경우.
    """
    if state is None:
        raise TypeError("state must not be None")
    if not isinstance(state, dict):
        raise TypeError(f"state must be dict, got {type(state).__name__}")
    with patch.object(pipeline_mod, "_load_acceptance_request", return_value=file_req), \
         patch("shutil.which", return_value="C:\\fake\\gh.exe"), \
         patch("subprocess.run", side_effect=_build_subprocess_side_effect(pr_number, comments)):
        return _check_pr_approver_provenance(state)


def _base_state() -> Dict[str, Any]:
    """기본 state dict — pipeline_id + nonce 의 acceptance_request 포함."""
    return {
        "pipeline_id": _PIPELINE_ID,
        "acceptance_request": {"nonce": _VALID_NONCE},
    }


def _base_file_req(**overrides: Any) -> Dict[str, Any]:
    """기본 acceptance_request.json 내용 — overrides 로 일부 필드 치환 가능."""
    base = {
        "pipeline_id": _PIPELINE_ID,
        "nonce": _VALID_NONCE,
        "request_id": "req-001",
        "status": "PENDING",
        "created_at": _REQ_CREATED_AT,
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------------
# AC-4: createdAt(GraphQL) 필드만 있는 댓글 → PASS
# ----------------------------------------------------------------------------

def test_createdAt_field_only() -> None:
    """Oracle edge_createdAt_field — created_at 없이 createdAt 만 있어도 PASS.

    GitHub GraphQL 응답은 createdAt(camelCase) 키를 사용한다. _check_pr_approver_provenance
    가 created_at(REST) 뿐 아니라 createdAt(GraphQL) 도 양방향으로 읽어야 PASS 된다.
    """
    state = _base_state()
    file_req = _base_file_req()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": _NONCELESS_CODE,
            "id": "C1",
            # created_at 없음 — createdAt(GraphQL) 만 존재.
            "createdAt": _COMMENT_NEWER,
        }
    ]
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "PASS", \
        f"createdAt 필드만 있어도 PASS여야 함: {result.get('message')}"
    assert result.get("approver") == PIPELINE_ALLOWED_APPROVER, \
        "PASS 결과의 approver 가 허용 승인자여야 함"
    assert result.get("failure_code", "") == ""


# ----------------------------------------------------------------------------
# AC-2 + AC-6: timestamp 필드 누락 댓글만 존재 → fail-closed, pr_approver_missing
# ----------------------------------------------------------------------------

def test_timestamp_missing_fail_closed() -> None:
    """Oracle edge_timestamp_missing — timestamp 누락 댓글은 skip → pr_approver_missing.

    created_at 도 createdAt 도 없는 댓글은 신뢰할 수 없으므로 승인 후보에서 제외(skip)한다.
    유효한 다른 댓글이 없으면 pr_approver_missing 으로 BLOCKED 되어야 한다 (fail-closed).
    """
    state = _base_state()
    file_req = _base_file_req()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": _NONCELESS_CODE,
            "id": "C1",
            # created_at / createdAt 모두 없음.
        }
    ]
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "BLOCKED", \
        f"timestamp 누락 댓글만 있으면 BLOCKED여야 함: {result.get('message')}"
    assert result.get("failure_code") == "pr_approver_missing", \
        f"timestamp 누락 fail-closed 후 pr_approver_missing 이어야 함: {result.get('failure_code')}"
    assert result.get("approver") in (None, ""), \
        "BLOCKED 결과의 approver 는 비어 있어야 함"


# ----------------------------------------------------------------------------
# AC-3 + AC-6: timestamp 파싱 실패 댓글만 존재 → fail-closed, pr_approver_missing
# ----------------------------------------------------------------------------

def test_timestamp_parse_error_fail_closed() -> None:
    """Oracle edge_timestamp_parse_error — 파싱 불가 timestamp 는 skip → pr_approver_missing.

    'invalid-date-format' 같이 파싱할 수 없는 timestamp 댓글은 승인 후보에서 제외(skip)한다.
    유효한 다른 댓글이 없으면 pr_approver_missing 으로 BLOCKED 되어야 한다 (fail-closed).
    """
    state = _base_state()
    file_req = _base_file_req()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": _NONCELESS_CODE,
            "id": "C1",
            "created_at": "invalid-date-format",
        }
    ]
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "BLOCKED", \
        f"파싱 불가 timestamp 댓글만 있으면 BLOCKED여야 함: {result.get('message')}"
    assert result.get("failure_code") == "pr_approver_missing", \
        f"timestamp 파싱실패 fail-closed 후 pr_approver_missing 이어야 함: {result.get('failure_code')}"


# ----------------------------------------------------------------------------
# AC-7: 발급 시각 이전(과거) 댓글 → pr_comment_too_old (회귀 없음)
# ----------------------------------------------------------------------------

def test_edge_pr_comment_too_old() -> None:
    """발급 시각(created_at) 이전에 작성된 과거 댓글은 pr_comment_too_old 로 차단된다.

    승인 요청 발급 시각(_REQ_CREATED_AT = 10:00) 보다 이전(09:00)에 작성된 댓글은
    현재 발급된 승인 코드와 무관한 과거 댓글이므로 승인 후보에서 제외되고,
    pr_comment_too_old 로 BLOCKED 되어야 한다 (fail-closed).
    """
    state = _base_state()
    file_req = _base_file_req()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": _NONCELESS_CODE,
            "id": "C1",
            "created_at": _COMMENT_OLDER,  # 발급 시각 이전.
        }
    ]
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "BLOCKED", \
        f"발급 시각 이전 과거 댓글은 BLOCKED여야 함: {result.get('message')}"
    assert result.get("failure_code") == "pr_comment_too_old", \
        f"과거 댓글은 pr_comment_too_old 여야 함: {result.get('failure_code')}"


# ----------------------------------------------------------------------------
# 보강 회귀: created_at(REST) 정상 케이스 → PASS (Oracle normal_provenance_pass)
# ----------------------------------------------------------------------------

def test_created_at_rest_field_pass() -> None:
    """Oracle normal_provenance_pass — created_at(REST) 정상 + 발급 이후 댓글 → PASS."""
    state = _base_state()
    file_req = _base_file_req()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": _NONCELESS_CODE,
            "id": "C1",
            "created_at": _COMMENT_NEWER,
        }
    ]
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "PASS", \
        f"created_at 정상 + 발급 이후 댓글은 PASS여야 함: {result.get('message')}"
    assert result.get("approver") == PIPELINE_ALLOWED_APPROVER


if __name__ == "__main__":
    # Self-Verification Protocol: 모듈 단독 실행 시 unittest 자체 검증.
    print("[SELF-VERIFY] running test_pr_comment_approval_83f4 ...")
    unittest.main(verbosity=2)
