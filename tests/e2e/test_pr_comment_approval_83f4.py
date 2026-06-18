"""
BUG-20260618-83F4: PR 댓글 provenance 검증 테스트.

브라우저 승인 채널 제거 후, 승인은 오직 GitHub PR 댓글의
작성자(provenance) + 작성 시각(timestamp) 검증으로만 처리된다.

이 테스트는 pipeline._check_pr_approver_provenance(state) 를 직접 호출한다.
실제 함수는 gh CLI(subprocess) 로 PR 댓글을 조회하므로, shutil.which 와
subprocess.run 을 monkeypatch 하여 오라클이 정의한 PR 댓글 목록을 주입한다.

함수 반환값은 status(PASS|BLOCKED) 이며, 오라클의 ok 필드와 다음과 같이 대응한다:
  status == "PASS"     -> ok True
  status == "BLOCKED"  -> ok False
"""
import json
import sys
import types
from pathlib import Path

import pytest

PROJ_ROOT = Path(__file__).parent.parent.parent
ORACLE_DIR = PROJ_ROOT / "tests" / "oracles" / "BUG-20260618-83F4"
sys.path.insert(0, str(PROJ_ROOT))

import pipeline as p  # noqa: E402


def _load_oracle(case_dir: str):
    """오라클 case 디렉토리의 input.json / expected.json 을 로드한다."""
    base = ORACLE_DIR / case_dir
    input_data = json.loads((base / "input.json").read_text(encoding="utf-8"))
    expected = json.loads((base / "expected.json").read_text(encoding="utf-8"))
    return input_data, expected


def _install_fake_gh(monkeypatch, pr_comments, pr_number=100):
    """shutil.which 와 subprocess.run 을 monkeypatch 하여 fake gh 를 설치한다.

    _check_pr_approver_provenance 내부에서:
      - shutil.which(gh) -> "/fake/gh" 반환
      - git rev-parse --abbrev-ref HEAD -> 임의 브랜치
      - gh pr list --json number,headRefName -> [{number, headRefName}]
      - gh pr view <n> --json comments -> {"comments": pr_comments}
    를 모킹한다.
    """
    import subprocess as _subprocess

    def fake_which(name):
        return "/fake/gh"

    monkeypatch.setattr(p, "_shutil", types.SimpleNamespace(which=fake_which), raising=False)
    # 함수 내부는 `import shutil as _shutil` 후 _shutil.which 를 호출하므로 shutil.which 도 패치.
    import shutil as _real_shutil
    monkeypatch.setattr(_real_shutil, "which", fake_which, raising=True)

    branch_name = "impl/BUG-20260618-83F4"

    def fake_run(cmd, *args, **kwargs):
        argv = list(cmd)
        joined = " ".join(str(c) for c in argv)
        # git 현재 브랜치
        if "rev-parse" in joined:
            return _subprocess.CompletedProcess(argv, 0, stdout=branch_name + "\n", stderr="")
        # gh pr list
        if "pr" in argv and "list" in argv:
            payload = json.dumps([{"number": pr_number, "headRefName": branch_name}])
            return _subprocess.CompletedProcess(argv, 0, stdout=payload, stderr="")
        # gh pr view <n> --json comments
        if "pr" in argv and "view" in argv:
            payload = json.dumps({"comments": pr_comments})
            return _subprocess.CompletedProcess(argv, 0, stdout=payload, stderr="")
        return _subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(_subprocess, "run", fake_run, raising=True)


def _comments_to_gh_format(pr_comments):
    """오라클의 pr_comments(author/body/created_at) 를 gh JSON 댓글 포맷으로 변환한다.

    gh pr view --json comments 의 댓글은 {"author": {"login": ...}, "body": ..., "createdAt"/"created_at": ...}
    형식이다. _check_pr_approver_provenance 는 author.login 과 body 를 읽고,
    created_at(snake_case) 으로 작성 시각을 읽는다.
    """
    out = []
    for c in pr_comments:
        out.append({
            "author": {"login": c.get("author", "")},
            "body": c.get("body", ""),
            "created_at": c.get("created_at", ""),
            "id": c.get("id", "c1"),
        })
    return out


def _run_check(input_data, monkeypatch, tmp_path):
    """acceptance_request.json 을 tmp_path 에 쓰고 _check_pr_approver_provenance 를 호출한다.

    반환된 dict 에 ok 필드(status==PASS)를 부가하여 오라클과 대조할 수 있게 한다.
    """
    req = input_data["acceptance_request"]
    pipeline_id = req.get("pipeline_id", "")

    monkeypatch.chdir(tmp_path)
    # acceptance_request.json 은 상대 경로(cwd 기준)로 로드된다.
    (tmp_path / p.ACCEPTANCE_REQUEST_FILE).write_text(
        json.dumps(req, ensure_ascii=False), encoding="utf-8"
    )

    pr_comments = _comments_to_gh_format(input_data.get("pr_comments", []))
    pr_number = req.get("pr_number", 100)
    _install_fake_gh(monkeypatch, pr_comments, pr_number=pr_number)

    state = {"pipeline_id": pipeline_id}
    result = p._check_pr_approver_provenance(state)
    result["ok"] = result.get("status") == "PASS"
    return result


def test_normal_pr_comment_approval(monkeypatch, tmp_path):
    """OC-1 정상 경로: 허용 승인자가 request 이후 올바른 코드 댓글 → ok=True."""
    input_data, expected = _load_oracle("normal_pr_comment_approval")
    result = _run_check(input_data, monkeypatch, tmp_path)
    assert result["ok"] == expected["ok"], result.get("message")
    assert result.get("failure_code", "") == expected["failure_code"]


def test_wrong_author(monkeypatch, tmp_path):
    """OC-2 edge: 다른 사용자(other-user) 댓글 → pr_approver_missing."""
    input_data, expected = _load_oracle("edge_pr_comment_wrong_author")
    result = _run_check(input_data, monkeypatch, tmp_path)
    assert result["ok"] == expected["ok"], result.get("message")
    assert result.get("failure_code", "") == expected["failure_code"]


def test_too_old_comment(monkeypatch, tmp_path):
    """OC-3 edge: 댓글 시각이 request 생성 이전 → pr_comment_too_old."""
    input_data, expected = _load_oracle("edge_pr_comment_too_old")
    result = _run_check(input_data, monkeypatch, tmp_path)
    assert result["ok"] == expected["ok"], result.get("message")
    assert result.get("failure_code", "") == expected["failure_code"]


def test_missing_acceptance_request(monkeypatch, tmp_path):
    """acceptance_request.json 없을 때 → ok=False (PASS 불가)."""
    monkeypatch.chdir(tmp_path)
    # 댓글은 비어 있어도 됨 — gh 모킹만 설치.
    _install_fake_gh(monkeypatch, [], pr_number=100)
    state = {"pipeline_id": "BUG-20260618-83F4"}
    result = p._check_pr_approver_provenance(state)
    result["ok"] = result.get("status") == "PASS"
    assert result["ok"] is False


def test_no_matching_comment(monkeypatch, tmp_path):
    """ACCEPT 코드 포함 댓글이 없을 때 → pr_approver_missing."""
    req = {
        "pipeline_id": "BUG-20260618-83F4",
        "status": "PENDING",
        "nonce": "TESTNONC",
        "created_at": "2026-06-18T10:00:00Z",
        "pr_number": 100,
    }
    monkeypatch.chdir(tmp_path)
    (tmp_path / p.ACCEPTANCE_REQUEST_FILE).write_text(
        json.dumps(req, ensure_ascii=False), encoding="utf-8"
    )
    _install_fake_gh(monkeypatch, [], pr_number=100)
    state = {"pipeline_id": "BUG-20260618-83F4"}
    result = p._check_pr_approver_provenance(state)
    result["ok"] = result.get("status") == "PASS"
    assert result["ok"] is False
    assert result.get("failure_code", "") == "pr_approver_missing"
