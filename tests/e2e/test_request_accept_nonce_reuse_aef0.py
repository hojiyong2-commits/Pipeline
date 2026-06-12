"""IMP-20260531-AEF0: gates request-accept nonce 재사용 E2E 테스트.

TC-R1 ~ TC-R6 — PIPELINE_STATE_PATH 격리 + final_state assertion 필수.

아래 6개 시나리오를 테스트합니다:
  TC-R1 (normal): 동일 조건 재실행 → 기존 nonce 재사용
  TC-R2 (edge):   --force-new-code → 새 nonce 강제 발급
  TC-R3 (edge):   evidence SHA-256 변경 → 새 nonce 발급
  TC-R4 (edge):   PR head SHA 변경 → 새 nonce 발급
  TC-R5 (edge):   CI run ID 변경 → 새 nonce 발급
  TC-R6 (edge):   기존 코드 status=CONSUMED → 새 nonce 발급

격리 전략:
  - PIPELINE_STATE_PATH 환경 변수로 state 파일을 tmp_path 안에 격리
  - subprocess cwd=tmp_path로 실행하여 acceptance_request.json(상대경로)도 격리
  - 전역 pipeline_state.json 및 acceptance_request.json을 수정하지 않음
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# ─── 헬퍼 ──────────────────────────────────────────────────────────────────────

PIPELINE_PY = str(Path(__file__).resolve().parents[2] / "pipeline.py")

DUMMY_PIPELINE_ID = "IMP-20260531-AEF0"
DUMMY_NONCE = "AAAABBBB"
DUMMY_REQUEST_ID = "aef0test"


def _sha256_of(text: str) -> str:
    """텍스트의 SHA-256 hex digest."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# IMP-20260612-E12D MT-2: conftest의 autouse fake gh fixture가 제거되어
# request-accept의 PR body readiness 검사를 통과하려면 fake gh를 명시적으로 주입해야 한다.
# 본 파일의 TC-R1~TC-R6은 모두 request-accept 성공(새 nonce 발급 또는 재사용)을 기대하므로
# 완전한 PR body를 반환하는 fake gh 스크립트를 PIPELINE_GH_EXECUTABLE로 전달한다.
# 단, 기존 테스트 의도(gh CLI 없는 환경 → pr_head_sha/ci_run_id 빈 문자열)를 유지하기 위해
# fake gh는 headSha/databaseId에 빈 문자열을, run/pr list에 빈 배열을 반환한다.

_AEF0_FAKE_GH_PR_BODY = (
    "## 작업 요약\n자동 테스트 픽스처 PR body\n\n"
    "## 사용자가 확인할 결과물\n결과물 경로: N/A (테스트)\n\n"
    "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 성공\n\n"
    "## 중요한 선택과 트레이드오프\nN/A (테스트 픽스처)\n\n"
    "## 검증\n모든 게이트 PASS\n"
)


def _write_fake_gh_script(tmp_path: Path) -> Path:
    """완전한 PR body를 반환하는 fake gh 스크립트를 tmp_path에 생성하여 경로 반환.

    headSha/databaseId는 빈 문자열, run/pr list는 빈 배열을 반환하여
    gh CLI 없는 환경(pr_head_sha=""/ci_run_id="")을 시뮬레이션한다.
    """
    body_json = json.dumps(_AEF0_FAKE_GH_PR_BODY)
    script = tmp_path / "fake_gh_aef0.py"
    script.write_text(
        "import sys, io, json\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        f"BODY = {body_json}\n"
        "args = sys.argv[1:]\n"
        'if "--jq" in args:\n'
        '    jq_idx = args.index("--jq"); jq = args[jq_idx+1] if jq_idx+1 < len(args) else ""\n'
        '    if jq == ".body":\n'
        '        sys.stdout.write(BODY)\n'
        '        if not BODY.endswith("\\n"):\n'
        '            sys.stdout.write("\\n")\n'
        "        sys.exit(0)\n"
        '    elif "[.files" in jq or jq.startswith(".[0]"):\n'
        '        print("[]"); sys.exit(0)\n'
        '    elif ".headSha" in jq or ".databaseId" in jq:\n'
        '        print(""); sys.exit(0)\n'
        'if "run" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        'if "run" in args and "view" in args:\n'
        "    print(json.dumps({})); sys.exit(0)\n"
        'if "pr" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        "print(json.dumps({\n"
        '    "body": BODY, "number": 1,\n'
        '    "headRefOid": "abc123def456abc123def456abc123def456abc1",\n'
        '    "isDraft": False, "state": "OPEN", "files": [],\n'
        '    "url": "https://github.com/test/repo/pull/1",\n'
        "}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return script


def _fake_gh_env(tmp_path: Path) -> Dict[str, str]:
    """PIPELINE_GH_EXECUTABLE로 fake gh(PR body)를 가리키고, PATH를 제한하는 extra_env dict.

    IMP-20260612-E12D MT-2: TC-R1~TC-R6은 "gh CLI 없는 환경(pr_head_sha=''/ci_run_id='')"을
    전제로 한다. 그러나 _get_current_pr_head_sha()/_get_pr_branch_ci_run_id()는
    bare ["gh"] 또는 shutil.which("gh")를 사용하므로, 실제 gh가 설치되고 열린 PR이 있으면
    실제 head SHA/run ID를 반환해 재사용 판단이 깨진다.
    이를 막기 위해 PATH를 tmp_path로 제한하여 실제 gh 탐색을 무력화한다.
    PR body 조회만 PIPELINE_GH_EXECUTABLE(Python fake gh)로 라우팅되며, 이는 sys.executable
    절대 경로로 실행되므로 PATH 제한과 무관하게 동작한다.

    Args:
        tmp_path: pytest tmp_path fixture.
    Returns:
        extra_env dict (PIPELINE_GH_EXECUTABLE + 제한된 PATH).
    """
    return {
        "PIPELINE_GH_EXECUTABLE": str(_write_fake_gh_script(tmp_path)),
        "PATH": str(tmp_path),
    }


def _run_pipeline(
    args: list,
    state_path: Path,
    cwd: Path,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """PIPELINE_STATE_PATH 격리 + cwd 격리 환경에서 pipeline.py 실행.

    acceptance_request.json은 상대 경로("acceptance_request.json")로 저장되므로
    cwd를 tmp_path로 설정하여 격리합니다.
    """
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    # Windows cp949 인코딩 문제 방지: PYTHONIOENCODING=utf-8 강제 설정
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, PIPELINE_PY] + args,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(cwd),
    )


def _write_state(state_path: Path, pipeline_id: str) -> None:
    """최소 pipeline_state.json 작성 (request-accept 실행에 필요한 필드만).

    event_log 필드를 포함하여 _log_event() KeyError를 방지합니다.
    """
    state: Dict[str, Any] = {
        "schema_version": 2,
        "pipeline_id": pipeline_id,
        "current_phase": "Phase 2 - Dev (Implementation)",
        "phases": {
            "pm": {"status": "DONE"},
            "dev": {"status": "PENDING"},
        },
        "external_gates": {
            "technical": {"status": "PENDING"},
            "oracle": {"status": "PENDING"},
            "acceptance": {"status": "PENDING"},
            "github_ci": {"status": "PENDING"},
        },
        "events": [],
        "event_log": [],
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# IMP-20260612-E12D MT-2: IMP-20260611-A716 도입 후 _should_reuse_acceptance_nonce는
# 기존 요청에 PR 본문 스냅샷 필드(pr_body_sha256/pr_body_readiness/required_sections_present/
# temporary_phrases_absent)가 없으면 재사용을 거부한다. fake gh가 완전한 PR body를 반환하므로
# (new_pr_body_sha256 != None) 재사용을 기대하는 TC-R1은 fake gh PR body와 동일한 스냅샷을
# 기존 요청에 미리 기록해야 한다. 이 SHA는 _AEF0_FAKE_GH_PR_BODY와 정확히 일치해야 한다.
_AEF0_FAKE_GH_PR_BODY_SHA256 = hashlib.sha256(
    _AEF0_FAKE_GH_PR_BODY.encode("utf-8")
).hexdigest()


def _write_acceptance_request(
    req_path: Path,
    *,
    pipeline_id: str = DUMMY_PIPELINE_ID,
    nonce: str = DUMMY_NONCE,
    evidence: str,
    evidence_sha256: Optional[str],
    pr_head_sha: str = "abc1234",
    ci_run_id: str = "99999999",
    status: str = "PENDING",
) -> None:
    """acceptance_request.json 초기 상태를 직접 기록 (기존 코드가 있는 상황 시뮬레이션).

    PR 본문 스냅샷 필드를 fake gh PR body 기준으로 채워, IMP-20260611-A716의
    재사용 거부 조건(PR 본문 SHA 없음/불일치)을 회피한다.
    """
    data: Dict[str, Any] = {
        "schema_version": 1,
        "pipeline_id": pipeline_id,
        "request_id": DUMMY_REQUEST_ID,
        "nonce": nonce,
        "created_at": "2026-05-31T10:00:00Z",
        "pr_url": "",
        "pr_head_sha": pr_head_sha,
        "github_ci_run_id": ci_run_id,
        "evidence": evidence,
        "evidence_sha256": evidence_sha256,
        "evidence_url": None,
        "status": status,
        # IMP-20260611-A716 PR 본문 스냅샷 (fake gh PR body 기준)
        "pr_body_sha256": _AEF0_FAKE_GH_PR_BODY_SHA256,
        "pr_body_readiness": "PASS",
        "required_sections_present": True,
        "temporary_phrases_absent": True,
    }
    req_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_acceptance_request(req_path: Path) -> Dict[str, Any]:
    """acceptance_request.json 읽기."""
    return json.loads(req_path.read_text(encoding="utf-8"))


def _write_evidence_file(path: Path, content: str = "test evidence content") -> str:
    """더미 evidence 파일 작성 후 SHA-256 반환."""
    path.write_text(content, encoding="utf-8")
    return _sha256_of(content)


# ─── TC-R1: 동일 조건 재실행 → nonce 재사용 ────────────────────────────────────────


def test_nonce_reused_on_same_conditions(tmp_path: Path) -> None:
    """TC-R1 (normal): 5-field 조건이 모두 같으면 기존 nonce를 재사용한다.

    Oracle: tests/oracles/IMP-20260531-AEF0/normal_nonce_reuse/expected.json
      nonce_reused=true, reason_contains="모두 같습니다"
    격리: PIPELINE_STATE_PATH 환경 변수로 state 파일을 tmp_path 안에 격리
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "evidence.txt"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_sha256 = _write_evidence_file(evidence_path)
    # gh CLI 없는 환경에서 pr_head_sha=""  ci_run_id="" 반환되므로 이에 맞춤
    _write_acceptance_request(
        req_path,
        evidence=str(evidence_path),
        evidence_sha256=evidence_sha256,
        pr_head_sha="",
        ci_run_id="",
        status="PENDING",
    )

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )

    # 프로세스가 정상 종료되어야 함
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    # stdout에 재사용 안내 메시지 포함
    assert "모두 같습니다" in result.stdout, (
        f"재사용 이유 메시지 누락\nstdout={result.stdout}"
    )
    assert "재사용" in result.stdout, (
        f"'재사용' 키워드 누락\nstdout={result.stdout}"
    )

    # final_state: acceptance_request.json의 nonce가 원래 값과 동일해야 함
    final_req = _read_acceptance_request(req_path)
    assert final_req["nonce"] == DUMMY_NONCE, (
        f"nonce가 변경됨 (재사용 실패): 기대={DUMMY_NONCE}, 실제={final_req['nonce']}"
    )
    assert final_req["status"] == "PENDING", (
        f"status가 변경됨: {final_req['status']}"
    )


# ─── TC-R2: --force-new-code → 새 nonce 강제 발급 ─────────────────────────────────


def test_force_new_code_always_new_nonce(tmp_path: Path) -> None:
    """TC-R2 (edge): --force-new-code 플래그가 있으면 조건과 무관하게 새 nonce를 발급한다.

    Oracle: tests/oracles/IMP-20260531-AEF0/edge_force_new_code/expected.json
      nonce_reused=false, reason_contains="--force-new-code 옵션이 지정되어"
    격리: PIPELINE_STATE_PATH 환경 변수로 state 파일을 tmp_path 안에 격리
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "evidence.txt"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_sha256 = _write_evidence_file(evidence_path)
    _write_acceptance_request(
        req_path,
        evidence=str(evidence_path),
        evidence_sha256=evidence_sha256,
        pr_head_sha="",
        ci_run_id="",
        status="PENDING",
    )

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path), "--force-new-code"],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

    assert "--force-new-code 옵션이 지정되어" in result.stdout, (
        f"force-new-code 안내 메시지 누락\nstdout={result.stdout}"
    )

    # final_state: nonce가 새로 발급되어야 함
    final_req = _read_acceptance_request(req_path)
    assert final_req["nonce"] != DUMMY_NONCE, (
        f"nonce가 재사용됨 (force-new-code 실패): {final_req['nonce']}"
    )


# ─── TC-R3: evidence SHA-256 변경 → 새 nonce ──────────────────────────────────────


def test_new_nonce_when_evidence_sha_changed(tmp_path: Path) -> None:
    """TC-R3 (edge): evidence 파일 내용이 바뀌면(SHA-256 변경) 새 nonce를 발급한다.

    Oracle: tests/oracles/IMP-20260531-AEF0/edge_evidence_sha_changed/expected.json
      nonce_reused=false, reason_contains="결과물 파일 내용이 달라서"
    격리: PIPELINE_STATE_PATH 환경 변수로 state 파일을 tmp_path 안에 격리
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "evidence.txt"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    # 기존 요청: SHA-256은 구 내용 기준
    old_sha = _sha256_of("old evidence content")
    _write_acceptance_request(
        req_path,
        evidence=str(evidence_path),
        evidence_sha256=old_sha,
        pr_head_sha="",
        ci_run_id="",
        status="PENDING",
    )
    # 현재 파일: 내용이 달라져 SHA-256이 다름
    _write_evidence_file(evidence_path, content="new different evidence content")

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "결과물 파일 내용이 달라서" in result.stdout, (
        f"SHA 변경 안내 메시지 누락\nstdout={result.stdout}"
    )

    # final_state: 새 nonce 발급
    final_req = _read_acceptance_request(req_path)
    assert final_req["nonce"] != DUMMY_NONCE, (
        f"nonce가 재사용됨 (SHA 변경 미감지): {final_req['nonce']}"
    )


# ─── TC-R4: PR head SHA 변경 → 새 nonce ────────────────────────────────────────────


def test_new_nonce_when_pr_sha_changed(tmp_path: Path) -> None:
    """TC-R4 (edge): PR head SHA가 달라지면(새 커밋 push) 새 nonce를 발급한다.

    Oracle: tests/oracles/IMP-20260531-AEF0/edge_pr_sha_changed/expected.json
      nonce_reused=false, reason_contains="PR head SHA가 달라서"
    격리: PIPELINE_STATE_PATH 환경 변수로 state 파일을 tmp_path 안에 격리

    gh CLI 없는 환경에서는 pr_head_sha=""를 반환하므로,
    기존 요청의 pr_head_sha를 "old_sha_value"(비어 있지 않음)로 설정하여 불일치를 유발합니다.
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "evidence.txt"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_sha256 = _write_evidence_file(evidence_path)
    # 기존 요청: pr_head_sha="old_sha_value" → CLI 없는 환경에서 new는 ""이 되어 불일치
    _write_acceptance_request(
        req_path,
        evidence=str(evidence_path),
        evidence_sha256=evidence_sha256,
        pr_head_sha="old_sha_value",
        ci_run_id="",
        status="PENDING",
    )

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "PR head SHA가 달라서" in result.stdout, (
        f"PR SHA 변경 안내 메시지 누락\nstdout={result.stdout}"
    )

    # final_state: 새 nonce 발급
    final_req = _read_acceptance_request(req_path)
    assert final_req["nonce"] != DUMMY_NONCE, (
        f"nonce가 재사용됨 (PR SHA 변경 미감지): {final_req['nonce']}"
    )


# ─── TC-R5: CI run ID 변경 → 새 nonce ─────────────────────────────────────────────


def test_new_nonce_when_ci_run_changed(tmp_path: Path) -> None:
    """TC-R5 (edge): GitHub Actions run ID가 달라지면 새 nonce를 발급한다.

    Oracle: tests/oracles/IMP-20260531-AEF0/edge_ci_run_changed/expected.json
      nonce_reused=false, reason_contains="GitHub Actions run ID가 달라서"
    격리: PIPELINE_STATE_PATH 환경 변수로 state 파일을 tmp_path 안에 격리

    gh CLI 없는 환경: ci_run_id=""가 반환되므로,
    기존 요청의 ci_run_id를 "99999999"(비어 있지 않음)로 설정하여 불일치를 유발합니다.
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "evidence.txt"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_sha256 = _write_evidence_file(evidence_path)
    # 기존 요청: ci_run_id="99999999", 현재 환경: gh CLI 없어 "" 반환 → 불일치
    _write_acceptance_request(
        req_path,
        evidence=str(evidence_path),
        evidence_sha256=evidence_sha256,
        pr_head_sha="",
        ci_run_id="99999999",
        status="PENDING",
    )

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "GitHub Actions run ID가 달라서" in result.stdout, (
        f"CI run ID 변경 안내 메시지 누락\nstdout={result.stdout}"
    )

    # final_state: 새 nonce 발급
    final_req = _read_acceptance_request(req_path)
    assert final_req["nonce"] != DUMMY_NONCE, (
        f"nonce가 재사용됨 (CI run ID 변경 미감지): {final_req['nonce']}"
    )


# ─── TC-R6: status=CONSUMED → 새 nonce ────────────────────────────────────────────


def test_new_nonce_when_status_not_pending(tmp_path: Path) -> None:
    """TC-R6 (edge): 기존 코드 status=CONSUMED면 새 nonce를 발급한다.

    Oracle: tests/oracles/IMP-20260531-AEF0/edge_status_not_pending/expected.json
      nonce_reused=false, reason_contains="새 코드를 발급합니다"
    격리: PIPELINE_STATE_PATH 환경 변수로 state 파일을 tmp_path 안에 격리
    """
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "evidence.txt"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_sha256 = _write_evidence_file(evidence_path)
    _write_acceptance_request(
        req_path,
        evidence=str(evidence_path),
        evidence_sha256=evidence_sha256,
        pr_head_sha="",
        ci_run_id="",
        status="CONSUMED",  # 이미 소비된 상태
    )

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "새 코드를 발급합니다" in result.stdout, (
        f"새 코드 발급 안내 메시지 누락\nstdout={result.stdout}"
    )

    # final_state: 새 nonce 발급 (CONSUMED 상태였으므로 새 파일이 써져야 함)
    final_req = _read_acceptance_request(req_path)
    assert final_req["nonce"] != DUMMY_NONCE, (
        f"nonce가 재사용됨 (CONSUMED 상태 미감지): {final_req['nonce']}"
    )
    assert final_req["status"] == "PENDING", (
        f"새 요청의 status가 PENDING이 아님: {final_req['status']}"
    )
