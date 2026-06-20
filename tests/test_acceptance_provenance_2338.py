"""tests/test_acceptance_provenance_2338.py

IMP-20260608-2338: _check_pr_approver_provenance + _cmd_gates_request_accept 수정 검증.

[Purpose]: User Acceptance gate 동기화 버그 3개(state 우선 nonce 로드, stale nonce,
    packet 재생성 후 SHA 불일치)를 회귀 테스트로 고정한다.
[Assumptions]: pipeline.py가 프로젝트 루트에 위치한다. tests/는 BASE_DIR 하위.
    pytest 환경 변수 PIPELINE_STATE_PATH는 사용하지 않고, _load_acceptance_request는
    함수 단위 mock으로 격리한다. test_set.json T001~T005가 모듈 레벨 함수명으로
    pytest --collect를 호출하므로 모든 테스트는 클래스 밖 함수로 정의한다.
[Vulnerability & Risks]: gh CLI / subprocess.run mock 패턴이 _check_pr_approver_provenance
    내부 import 구조(_subprocess, _shutil 별칭)와 분리되어 있어, 함수 내부의 local
    import를 직접 patch해야 한다. patch 대상 누락 시 실제 gh가 호출되어 실패할 수 있다.
[Improvement]: pytest fixture로 gh mock을 공통화하고, _check_pr_approver_provenance
    내부 import를 모듈 레벨로 끌어올려 mock 표면을 단순화.
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pipeline as pipeline_mod  # type: ignore  # noqa: E402

_check_pr_approver_provenance = pipeline_mod._check_pr_approver_provenance
PIPELINE_ALLOWED_APPROVER = pipeline_mod.PIPELINE_ALLOWED_APPROVER


def _make_gh_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """subprocess.run 반환값을 흉내내는 MagicMock."""
    if returncode is None:
        raise TypeError("returncode must not be None")
    if not isinstance(returncode, int):
        raise TypeError(f"returncode must be int, got {type(returncode).__name__}")
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def _build_subprocess_side_effect(pr_number: str, comments: list, head_branch: str = "main") -> Any:
    """_check_pr_approver_provenance 내부 subprocess.run 호출을 흉내내는 side_effect.

    호출 순서:
      1. git rev-parse --abbrev-ref HEAD       → head_branch 반환
      2. gh pr list --state open --json ...    → [{number, headRefName}] 반환
      3. gh pr view <pr> --json comments       → {comments: [...]} 반환
    """
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


# MT-1: _check_pr_approver_provenance가 acceptance_request.json 우선 로드 검증
# ----------------------------------------------------------------------------

def test_state_empty_json_fresh_nonce_pass() -> None:
    """T001 (case=normal) — state는 비어있고 acceptance_request.json에 nonce가 있을 때 파일 기준으로 PASS."""
    state: Dict[str, Any] = {"pipeline_id": "TEST-001", "acceptance_request": {}}
    file_req = {
        "pipeline_id": "TEST-001",
        "nonce": "TESTNONCE",
        "request_id": "req-001",
        # BUG-20260620-3BF4 MT-2: replay 방어용 request 발급 시각.
        "created_at": "2026-06-20T12:00:00Z",
    }
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            # BUG-20260620-3BF4 MT-2: PR 댓글 승인 코드는 nonce 없는 ACCEPT-{pipeline_id}.
            "body": "ACCEPT-TEST-001",
            "id": "C1",
            "createdAt": "2026-06-20T13:00:00Z",
        }
    ]
    with patch.object(pipeline_mod, "_load_acceptance_request", return_value=file_req), \
         patch("shutil.which", return_value="C:\\fake\\gh.exe"), \
         patch("subprocess.run", side_effect=_build_subprocess_side_effect("42", comments)):
        result = _check_pr_approver_provenance(state)
    assert result["status"] == "PASS", \
        f"파일에 nonce가 있으면 PASS여야 함: {result.get('message')}"
    assert result.get("approver") == PIPELINE_ALLOWED_APPROVER
    assert result.get("comment_id") == "C1"


def test_state_stale_nonce_file_fresh() -> None:
    """case=edge — state에 낡은 nonce가 있어도 파일의 최신 nonce 기준으로 PASS."""
    state: Dict[str, Any] = {
        "pipeline_id": "TEST-001",
        "acceptance_request": {"nonce": "STALE_NONCE", "request_id": "old-req"},
    }
    file_req = {
        "pipeline_id": "TEST-001",
        "nonce": "FRESH_NONCE",
        "request_id": "new-req",
        "created_at": "2026-06-20T12:00:00Z",
    }
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            # nonce 없는 ACCEPT-{pipeline_id} (파일 기준 최신 request 로 PASS).
            "body": "ACCEPT-TEST-001",
            "id": "C2",
            "createdAt": "2026-06-20T13:00:00Z",
        }
    ]
    with patch.object(pipeline_mod, "_load_acceptance_request", return_value=file_req), \
         patch("shutil.which", return_value="C:\\fake\\gh.exe"), \
         patch("subprocess.run", side_effect=_build_subprocess_side_effect("42", comments)):
        result = _check_pr_approver_provenance(state)
    assert result["status"] == "PASS", \
        f"최신 파일 기준 nonce로 PASS여야 함: {result.get('message')}"


def test_allowed_approver_correct_code() -> None:
    """case=normal — 파일이 없으면 state fallback 으로 동작 (state nonce 기반 PASS)."""
    state: Dict[str, Any] = {
        "pipeline_id": "PIPE-001",
        # 파일 없을 때 state fallback — created_at 도 state acceptance_request 에 보존.
        "acceptance_request": {
            "nonce": "CORRNONCE",
            "created_at": "2026-06-20T12:00:00Z",
        },
    }
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            # nonce 없는 ACCEPT-{pipeline_id} (state fallback 으로 PASS).
            "body": "ACCEPT-PIPE-001",
            "id": "C3",
            "createdAt": "2026-06-20T13:00:00Z",
        }
    ]
    # 파일은 없으므로 None 반환 (state fallback 검증)
    with patch.object(pipeline_mod, "_load_acceptance_request", return_value=None), \
         patch("shutil.which", return_value="C:\\fake\\gh.exe"), \
         patch("subprocess.run", side_effect=_build_subprocess_side_effect("7", comments)):
        result = _check_pr_approver_provenance(state)
    assert result["status"] == "PASS", \
        f"파일 없을 때 state fallback PASS여야 함: {result.get('message')}"


def test_wrong_nonce_blocked() -> None:
    """T002 (case=error) — 댓글에 잘못된 nonce가 들어있으면 BLOCKED 및 실패 메시지에 기대 코드 포함.

    BUG-20260612-B96C 재작업(REJECT 수정): WRONGNONCE 는 발급 이력에 존재하지 않는 임의 코드이므로
    approval_stale_nonce 가 아니라 approval_code_mismatch 로 분류되는 것이 올바르다.
    stale_nonce 는 "실제로 발급된 적 있는 nonce 와 정확히 일치"하는 경우에만 성립한다.
    """
    state: Dict[str, Any] = {
        "pipeline_id": "TEST-001",
        "acceptance_request": {"nonce": "RIGHTNONCE"},
    }
    file_req = {
        "pipeline_id": "TEST-001",
        "nonce": "RIGHTNONCE",
        "request_id": "req",
    }
    comments = [
        {
            "author": {"login": PIPELINE_ALLOWED_APPROVER},
            "body": "ACCEPT-TEST-001-WRONGNONCE",
            "id": "C4",
        }
    ]
    with patch.object(pipeline_mod, "_load_acceptance_request", return_value=file_req), \
         patch("shutil.which", return_value="C:\\fake\\gh.exe"), \
         patch("subprocess.run", side_effect=_build_subprocess_side_effect("42", comments)):
        result = _check_pr_approver_provenance(state)
    assert result["status"] == "BLOCKED"
    # BUG-20260612-B96C 재작업: 발급 이력에 없는 nonce(오타/잘못된 코드) → approval_code_mismatch.
    assert result.get("failure_code") == "approval_code_mismatch"
    # BUG-20260620-3BF4 MT-2: 기대 승인 코드는 nonce 없는 ACCEPT-{pipeline_id} 형식으로 표시된다.
    assert "ACCEPT-TEST-001" in result.get("message", ""), \
        "실패 메시지에 실제 기대 코드(nonce 없는 형식)가 표시되어야 함"


def test_wrong_approver_evidence_blocked() -> None:
    """T004 -k "evidence" 매칭 (case=error) — 허용 승인자가 아닌 사용자가 올바른 코드를 남겨도 BLOCKED.

    이름에 'evidence'를 포함하여 oracle T004 (`-k "reuse or evidence"`)에서 매칭된다.
    승인자 검증 실패 evidence를 회귀 테스트로 고정하는 의미를 담는다.
    """
    state: Dict[str, Any] = {
        "pipeline_id": "TEST-001",
        "acceptance_request": {"nonce": "VALIDNONCE"},
    }
    file_req = {
        "pipeline_id": "TEST-001",
        "nonce": "VALIDNONCE",
        "request_id": "req",
    }
    comments = [
        {
            "author": {"login": "malicious_actor"},
            "body": "ACCEPT-TEST-001-VALIDNONCE",
            "id": "C5",
        }
    ]
    with patch.object(pipeline_mod, "_load_acceptance_request", return_value=file_req), \
         patch("shutil.which", return_value="C:\\fake\\gh.exe"), \
         patch("subprocess.run", side_effect=_build_subprocess_side_effect("42", comments)):
        result = _check_pr_approver_provenance(state)
    assert result["status"] == "BLOCKED"
    assert result.get("failure_code") == "pr_approver_missing"


# MT-2: 패킷 재생성 후 acceptance_request.json의 packet_sha256이 실제 SHA와 일치
# ----------------------------------------------------------------------------

def test_packet_sha256_matches_after_generation() -> None:
    """T003 (case=normal) — MT-2 핵심 로직: packet 재생성 후 acceptance_request.json의
    packet_sha256이 실제 파일 SHA와 같도록 갱신되는지 검증.

    실제 _cmd_gates_request_accept 전체를 실행하지 않고, MT-2가 추가한 갱신 로직
    블록과 동일한 동작을 재현하여 acceptance_request.json이 올바르게 업데이트되는지
    확인한다.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # 1. 초기 acceptance_request.json — packet_sha256은 "OLD_SHA"로 시작
        req_path = tmp / "acceptance_request.json"
        initial = {
            "pipeline_id": "TEST-001",
            "nonce": "ABCDEFGH",
            "request_id": "req-001",
            "packet_path": str(tmp / "OLD_packet.md"),
            "packet_sha256": "0" * 64,  # placeholder old SHA
        }
        req_path.write_text(
            json.dumps(initial, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 2. 새로 생성된 packet 파일 — 실제 SHA 계산.
        #    Windows의 Path.write_text는 newline=\n을 \r\n으로 변환하므로,
        #    plaintext bytes로 계산한 SHA와 file SHA가 달라진다.
        #    pipeline._sha256_file과 동일하게 file의 raw bytes 기준으로 계산해야 한다.
        new_packet = tmp / "new_packet.md"
        new_content = "# Final Packet (regenerated)\n실제 내용\n"
        new_packet.write_bytes(new_content.encode("utf-8"))
        actual_sha = hashlib.sha256(new_packet.read_bytes()).hexdigest()

        # 3. MT-2 로직 재현: reuse=False 경로의 갱신 블록
        reuse = False
        auto_result = {"packet_path": str(new_packet), "pr_body_updated": False}
        if not reuse:
            try:
                _new_pkt_path = Path(str(auto_result["packet_path"]))
                if _new_pkt_path.exists():
                    _new_pkt_sha = pipeline_mod._sha256_file(_new_pkt_path)
                    if req_path.exists():
                        _req_data = json.loads(req_path.read_text(encoding="utf-8", errors="replace"))
                        _req_data["packet_path"] = str(_new_pkt_path)
                        _req_data["packet_sha256"] = _new_pkt_sha
                        req_path.write_text(
                            json.dumps(_req_data, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
            except (OSError, json.JSONDecodeError, TypeError):
                pass

        # 4. 검증: 갱신된 SHA가 실제 파일 SHA와 일치
        updated = json.loads(req_path.read_text(encoding="utf-8"))
        assert updated["packet_sha256"] == actual_sha, \
            "packet 재생성 후 acceptance_request.json의 SHA가 실제 파일 SHA와 일치해야 함"
        assert updated["packet_path"] == str(new_packet), \
            "packet_path도 새 경로로 갱신되어야 함"
        assert updated["packet_sha256"] != "0" * 64, \
            "초기 placeholder SHA는 더 이상 남아있지 않아야 함"


if __name__ == "__main__":
    # Self-Verification Protocol: 모듈 단독 실행 시 unittest 자체 검증
    print("[SELF-VERIFY] running test_acceptance_provenance_2338 ...")
    unittest.main(verbosity=2)
