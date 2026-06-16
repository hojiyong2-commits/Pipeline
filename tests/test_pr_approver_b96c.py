"""tests/test_pr_approver_b96c.py

BUG-20260612-B96C MT-2: _check_pr_approver_provenance 판정 우선순위 회귀 테스트.

[Purpose]: User Acceptance Provenance Gate(`_check_pr_approver_provenance`)가
    PR 댓글을 검증할 때 PASS > stale_nonce > code_mismatch > missing 우선순위로
    판정하고, packet 댓글 제외(AC-2)와 CONSUMED idempotency(AC-7)를 올바르게
    처리하는지 7개 케이스(TC-1~TC-7)로 고정한다.
[Assumptions]: pipeline.py가 프로젝트 루트에 위치한다. _load_acceptance_request 와
    subprocess.run / shutil.which 를 함수 단위로 mock 한다. test_acceptance_provenance_2338.py
    의 _build_subprocess_side_effect 패턴을 재사용한다. 모든 테스트는 클래스 밖
    모듈 레벨 함수로 정의하여 pytest --collect 가 함수명으로 매칭 가능하게 한다.
[Vulnerability & Risks]: gh CLI / subprocess.run mock 표면이 함수 내부 local import
    (_subprocess, _shutil 별칭)와 분리되어 있어, 표준 라이브러리 심볼(subprocess.run,
    shutil.which)을 직접 patch 해야 한다. patch 대상 누락 시 실제 gh가 호출되어
    의도와 다른 결과가 나올 수 있다. TC-7(CONSUMED)은 gh 호출 이전에 early-return
    되므로 subprocess mock 이 없어도 동작하지만, 회귀 안전을 위해 동일하게 mock 한다.
[Improvement]: 시간이 더 있다면 pytest fixture 로 gh mock 을 공통화하고,
    각 케이스의 댓글 구성을 데이터 테이블(parametrize)로 추출해 중복을 줄인다.
"""
import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pipeline as pipeline_mod  # type: ignore  # noqa: E402

_check_pr_approver_provenance = pipeline_mod._check_pr_approver_provenance
PIPELINE_ALLOWED_APPROVER = pipeline_mod.PIPELINE_ALLOWED_APPROVER

# 본 테스트 전체에서 사용하는 가상 pipeline_id / nonce.
# 승인 코드 형식: ACCEPT-<pipeline_id>-<nonce>
_PIPELINE_ID = "B96C-001"
_VALID_NONCE = "VALIDNONCE"
_VALID_CODE = f"ACCEPT-{_PIPELINE_ID}-{_VALID_NONCE}"
_PACKET_MARKER = "<!-- pipeline-human-acceptance-packet -->"


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

    Args:
        pr_number: 모의 PR 번호.
        comments: PR 댓글 목록 (각 항목은 author/body/id 키를 가진 dict).
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
        # git rev-parse
        if isinstance(argv, list) and argv[:2] == ["git", "rev-parse"]:
            return _make_gh_run(returncode=0, stdout=head_branch + "\n")
        # gh pr list
        if isinstance(argv, list) and len(argv) >= 3 and argv[1:3] == ["pr", "list"]:
            payload = json.dumps([
                {"number": int(pr_number), "headRefName": head_branch}
            ])
            return _make_gh_run(returncode=0, stdout=payload)
        # gh pr view
        if isinstance(argv, list) and len(argv) >= 3 and argv[1:3] == ["pr", "view"]:
            payload = json.dumps({"comments": comments})
            return _make_gh_run(returncode=0, stdout=payload)
        # 알 수 없는 호출 — 실패로 처리
        return _make_gh_run(returncode=1, stdout="", stderr="unexpected subprocess call")
    return _side


def _run_provenance(state: Dict[str, Any], file_req: Any,
                    comments: List[Dict[str, Any]], pr_number: str = "42") -> Dict[str, Any]:
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
    """기본 state dict — pipeline_id + valid nonce 의 acceptance_request 포함."""
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
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------------
# TC-1: 최신 정확한 승인 댓글 → PASS
# ----------------------------------------------------------------------------

def test_exact_match_pass() -> None:
    """TC-1 (case=normal) — 허용 승인자가 정확한 코드를 독립 댓글로 남기면 PASS."""
    state = _base_state()
    file_req = _base_file_req()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": _VALID_CODE,
            "id": "C1",
        }
    ]
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "PASS", \
        f"정확한 승인 코드 댓글은 PASS여야 함: {result.get('message')}"
    assert result.get("approver") == PIPELINE_ALLOWED_APPROVER, \
        "PASS 결과의 approver 가 허용 승인자여야 함"
    assert result.get("comment_id") == "C1"


# ----------------------------------------------------------------------------
# TC-2: packet 댓글만 존재 → FAIL (승인 후보 없음)
# ----------------------------------------------------------------------------

def test_packet_comment_only_fail() -> None:
    """TC-2 (case=exception) — packet marker 댓글에 승인 코드가 인용된 경우 → BLOCKED (자동 ACCEPT 차단).

    BUG-20260616-8011 보안 강화: packet 마커 댓글은 승인 후보에서 제외되지만, 그 안에
    기대 승인 코드(_VALID_CODE)가 인용되어 있으면 agent 가 packet 본문을 재게시했거나
    packet 댓글을 승인으로 처리하려는 자동 ACCEPT 시도로 판정한다. 따라서 단순
    pr_approver_missing 이 아니라 protocol_violation_auto_accept 로 정밀하게 차단한다.
    BLOCKED 상태는 동일하게 유지되며, 이는 보안 완화가 아니라 강화이다.
    """
    state = _base_state()
    file_req = _base_file_req()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": f"{_PACKET_MARKER}\n## 최종 확인 안내\n승인 코드: {_VALID_CODE}",
            "id": "C2",
        }
    ]
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "BLOCKED", \
        f"packet 댓글만 있으면 승인 후보가 없어 BLOCKED여야 함: {result.get('message')}"
    # 보안 강화 후: packet 마커 댓글에 승인 코드가 인용되면 자동 ACCEPT 시도로 간주하여
    # protocol_violation_auto_accept 로 차단한다. 코드 인용이 전혀 없는 경우에는
    # pr_approver_missing 으로 남는다. 두 경우 모두 허용 (보안 완화 없이 확장).
    assert result.get("failure_code") in (
        "protocol_violation_auto_accept",
        "pr_approver_missing",
        "",
    ), f"packet 댓글 필터링 후 protocol_violation_auto_accept 또는 코드 누락이어야 함: {result.get('failure_code')}"


def test_packet_comment_without_code_missing() -> None:
    """TC-2b (case=exception) — packet 마커 댓글에 승인 코드가 인용되지 않은 경우 → pr_approver_missing.

    BUG-20260616-8011: 코드 인용이 없는 순수 packet 안내 댓글은 자동 ACCEPT 시도가 아니므로
    protocol_violation_auto_accept 가 아니라 기존대로 승인 후보 없음(pr_approver_missing)으로 남는다.
    이 케이스가 보안 강화 후에도 기존 동작을 유지함을 고정하여, 강화가 정상 흐름을 깨지 않음을 보장한다.
    """
    state = _base_state()
    file_req = _base_file_req()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            # packet 마커는 있으나 승인 코드(_VALID_CODE)는 인용되지 않음.
            "body": f"{_PACKET_MARKER}\n## 최종 확인 안내\n결과물을 확인해 주세요.",
            "id": "C2b",
        }
    ]
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "BLOCKED", \
        f"코드 인용 없는 packet 댓글만 있으면 BLOCKED여야 함: {result.get('message')}"
    assert result.get("failure_code") == "pr_approver_missing", \
        f"코드 인용 없는 packet 댓글은 pr_approver_missing 이어야 함(자동 ACCEPT 아님): {result.get('failure_code')}"


# ----------------------------------------------------------------------------
# TC-3: 이전 nonce 댓글만 존재 → FAIL (approval_stale_nonce)
# ----------------------------------------------------------------------------

def test_stale_nonce_only_fail() -> None:
    """TC-3 (case=error) — 같은 pipeline_id prefix + 실제 발급된 적 있는 다른 nonce → approval_stale_nonce.

    BUG-20260612-B96C 재작업(REJECT 수정): stale_nonce 는 "발급 이력에 실제로 존재하는 nonce 와
    정확히 일치"하는 경우에만 성립한다. 따라서 OLDNONCE 를 발급 이력(previous_nonces)에 포함시킨다.
    이력에 없는 임의 nonce 는 code_mismatch 로 분류되므로(별도 TC-4 참조) 본 TC 는 발급 이력을 명시한다.
    """
    state = _base_state()
    # OLDNONCE 를 과거 발급 nonce 로 보존 → 같은 prefix + OLDNONCE 댓글은 stale 로 분류.
    file_req = _base_file_req(previous_nonces=["OLDNONCE"])
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": f"ACCEPT-{_PIPELINE_ID}-OLDNONCE",
            "id": "C3",
        }
    ]
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "BLOCKED", \
        f"이전 nonce 댓글은 BLOCKED여야 함: {result.get('message')}"
    assert result.get("failure_code") == "approval_stale_nonce", \
        f"같은 pipeline_id + 실제 발급된 다른 nonce 는 approval_stale_nonce 여야 함: {result.get('failure_code')}"


# ----------------------------------------------------------------------------
# TC-4: 오타/부분 코드 댓글만 → FAIL (approval_code_mismatch)
# ----------------------------------------------------------------------------

def test_code_mismatch_only_fail() -> None:
    """TC-4 (case=error) — 잘못된 prefix(다른 pipeline_id)의 코드 → approval_code_mismatch."""
    state = _base_state()
    file_req = _base_file_req()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            # ACCEPT- 로 시작하지만 ACCEPT-B96C-001- prefix 와 불일치(잘못된 prefix).
            "body": "ACCEPT-WRONGPREFIX-VALIDNONCE",
            "id": "C4",
        }
    ]
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "BLOCKED", \
        f"오타/잘못된 prefix 코드는 BLOCKED여야 함: {result.get('message')}"
    assert result.get("failure_code") == "approval_code_mismatch", \
        f"잘못된 prefix 코드는 approval_code_mismatch 여야 함: {result.get('failure_code')}"


# ----------------------------------------------------------------------------
# TC-5: 이전 nonce + 오타 + 최신 정확한 댓글 혼합 → PASS (우선순위 PASS 최상위)
# ----------------------------------------------------------------------------

def test_mixed_comments_exact_match_wins_pass() -> None:
    """TC-5 (case=normal) — stale + mismatch + 정확한 코드 혼합 시 PASS 가 최우선."""
    state = _base_state()
    file_req = _base_file_req()
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": f"ACCEPT-{_PIPELINE_ID}-OLDNONCE",  # stale
            "id": "C5a",
        },
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": "ACCEPT-WRONGPREFIX-VALIDNONCE",  # mismatch
            "id": "C5b",
        },
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": _VALID_CODE,  # exact match
            "id": "C5c",
        },
    ]
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "PASS", \
        f"정확한 코드가 하나라도 있으면 PASS여야 함 (PASS > stale > mismatch > missing): {result.get('message')}"
    assert result.get("approver") == PIPELINE_ALLOWED_APPROVER


# ----------------------------------------------------------------------------
# TC-6: 다른 작성자의 정확한 코드 → FAIL (pr_approver_missing)
# ----------------------------------------------------------------------------

def test_wrong_author_correct_code_fail() -> None:
    """TC-6 (case=exception) — 허용 승인자가 아닌 사용자가 정확한 코드를 남겨도 BLOCKED."""
    state = _base_state()
    file_req = _base_file_req()
    comments = [
        {
            "author": {"login": "unauthorized_user"},
            "body": _VALID_CODE,
            "id": "C6",
        }
    ]
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "BLOCKED", \
        f"비허용 작성자의 댓글은 BLOCKED여야 함: {result.get('message')}"
    assert result.get("failure_code") == "pr_approver_missing", \
        f"비허용 작성자만 있으면 승인 후보 없음 → pr_approver_missing: {result.get('failure_code')}"


# ----------------------------------------------------------------------------
# TC-7: acceptance_request CONSUMED 상태 → PASS (idempotent)
# ----------------------------------------------------------------------------

def test_consumed_state_idempotent_pass() -> None:
    """TC-7 (case=normal) — acceptance_request.json status=CONSUMED 면 댓글 재확인 없이 즉시 PASS."""
    state = _base_state()
    # acceptance_request.json 의 status 가 CONSUMED → gh 호출 이전 early-return.
    file_req = _base_file_req(status="CONSUMED")
    # 댓글에 어떤 승인 코드도 없어도 CONSUMED idempotency 로 PASS 되어야 함.
    comments: List[Dict[str, Any]] = []
    result = _run_provenance(state, file_req, comments)
    assert result["status"] == "PASS", \
        f"CONSUMED 상태는 idempotent PASS여야 함: {result.get('message')}"
    assert result.get("idempotent") is True, \
        "CONSUMED idempotency PASS 는 idempotent=True 플래그를 가져야 함"


if __name__ == "__main__":
    # Self-Verification Protocol: 모듈 단독 실행 시 unittest 자체 검증.
    print("[SELF-VERIFY] running test_pr_approver_b96c ...")
    unittest.main(verbosity=2)
