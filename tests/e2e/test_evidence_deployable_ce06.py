"""IMP-20260612-CE06: gates request-accept evidence deployability E2E 테스트.

TC-1 ~ TC-9 — PIPELINE_STATE_PATH 격리 + 실제 CLI subprocess 호출 + final_state assertion.

검증 대상:
  request-accept 단계에서 내부 파이프라인 산출물(qa_report.xml 등)을 evidence로
  전달하면 nonce 발급 전에 evidence_not_deployable 코드로 BLOCKED 되는지 확인한다.
  배포 가능한 결과물(일반 파일/URL)은 정상적으로 nonce가 발급되는지 확인한다.

시나리오:
  TC-1 (normal): 배포 가능한 evidence(output.xlsx) → BLOCKED 없이 nonce 발급
  TC-2 (error):  qa_report.xml → evidence_not_deployable BLOCKED
  TC-3 (error):  build_report.xml → BLOCKED
  TC-4 (error):  human_acceptance_packet.md → BLOCKED
  TC-5 (edge):   https URL → BLOCKED 없음
  TC-6 (error):  acceptance_request.json → BLOCKED
  TC-7 (error):  pipeline_contracts/IMP-xxx/ 경로 → BLOCKED
  TC-8 (error):  .pipeline/ 절대 경로 → BLOCKED (AC-2)
  TC-9 (error):  outputs add 등록된 qa_report.xml → BLOCKED (AC-5/AC-9)

격리 전략:
  - PIPELINE_STATE_PATH 환경 변수로 state 파일을 tmp_path 안에 격리
  - subprocess cwd=tmp_path로 실행하여 acceptance_request.json(상대경로)도 격리
  - 전역 pipeline_state.json 및 acceptance_request.json을 수정하지 않음
  - PR body readiness gate 우회: 완전한 PR body를 반환하는 fake gh를
    PIPELINE_GH_EXECUTABLE로 주입하고 PATH를 tmp_path로 제한
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# ─── 헬퍼 ──────────────────────────────────────────────────────────────────────

PIPELINE_PY = str(Path(__file__).resolve().parents[2] / "pipeline.py")

DUMMY_PIPELINE_ID = "IMP-20260612-CE06"

# fake gh가 반환할 완전한 PR body (PR body readiness gate 통과용).
_CE06_FAKE_GH_PR_BODY = (
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
    body_json = json.dumps(_CE06_FAKE_GH_PR_BODY)
    script = tmp_path / "fake_gh_ce06.py"
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

    PATH를 tmp_path로 제한하여 실제 gh 탐색을 무력화하고(pr_head_sha=""/ci_run_id=""),
    PR body 조회만 fake gh로 라우팅한다.
    """
    return {
        "PIPELINE_GH_EXECUTABLE": str(_write_fake_gh_script(tmp_path)),
        "PATH": str(tmp_path),
        "PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING": "1",
    }


def _run_pipeline(
    args: list,
    state_path: Path,
    cwd: Path,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """PIPELINE_STATE_PATH 격리 + cwd 격리 환경에서 pipeline.py 실행."""
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

    requirements_tracking을 설정하지 않아 legacy 파이프라인으로 취급 →
    AC table 검증을 생략한다. event_log/events 필드로 _log_event() KeyError 방지.
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


def _assert_blocked_not_deployable(
    result: subprocess.CompletedProcess, req_path: Path
) -> None:
    """BLOCKED(evidence_not_deployable) 공통 검증.

    - exit code 1
    - stdout/stderr에 failure_code=evidence_not_deployable 포함
    - acceptance_request.json이 생성되지 않음 (nonce 미발급)
    """
    combined = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 1, (
        f"BLOCKED 기대(exit 1)인데 returncode={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "evidence_not_deployable" in combined, (
        f"evidence_not_deployable 코드 누락\n{combined}"
    )
    # nonce가 발급되지 않아야 함 (acceptance_request.json 미생성)
    assert not req_path.exists(), (
        f"BLOCKED인데 acceptance_request.json이 생성됨: {req_path}"
    )


# ─── TC-1: 배포 가능한 evidence → nonce 발급 ─────────────────────────────────────


def test_tc1_deployable_evidence_allowed(tmp_path: Path) -> None:
    """TC-1 (normal): 배포 가능한 결과물(output.xlsx)은 BLOCKED되지 않고 nonce가 발급된다.

    격리: PIPELINE_STATE_PATH로 state를 tmp_path에 격리.
    CLI Evidence Contract: PIPELINE_STATE_PATH isolation + final_state assertion.
    """
    # PIPELINE_STATE_PATH isolation via _run_pipeline helper
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "output.xlsx"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_path.write_text("deployable user-facing result", encoding="utf-8")

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )

    # evidence_not_deployable으로 차단되지 않아야 함
    combined = (result.stdout or "") + (result.stderr or "")
    assert "evidence_not_deployable" not in combined, (
        f"배포 가능한 evidence가 차단됨\n{combined}"
    )
    # 정상 종료 + nonce 발급
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert req_path.exists(), (
        f"nonce 발급 실패: acceptance_request.json 미생성\n{combined}"
    )
    # final_state: acceptance_request.json에 nonce와 status 확인
    final_state = json.loads(req_path.read_text(encoding="utf-8"))
    assert final_state.get("nonce"), f"nonce 필드 누락: {final_state}"
    assert final_state.get("status") == "PENDING", (
        f"status가 PENDING이 아님: {final_state.get('status')}"
    )


# ─── TC-2: qa_report.xml → BLOCKED ────────────────────────────────────────────


def test_tc2_qa_report_blocked(tmp_path: Path) -> None:
    """TC-2 (error): qa_report.xml은 내부 산출물 → evidence_not_deployable BLOCKED.

    격리: PIPELINE_STATE_PATH로 state를 tmp_path에 격리.
    CLI Evidence Contract: PIPELINE_STATE_PATH isolation + final_state assertion.
    """
    # PIPELINE_STATE_PATH isolation via _run_pipeline helper
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "qa_report.xml"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_path.write_text("<qa_report/>", encoding="utf-8")

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )
    _assert_blocked_not_deployable(result, req_path)
    # final_state: BLOCKED이므로 acceptance_request.json 미생성 확인
    final_state = {} if not req_path.exists() else json.loads(req_path.read_text(encoding="utf-8"))
    assert not final_state, f"BLOCKED인데 acceptance_request.json 생성됨: {final_state}"


# ─── TC-3: build_report.xml → BLOCKED ─────────────────────────────────────────


def test_tc3_build_report_blocked(tmp_path: Path) -> None:
    """TC-3 (error): build_report.xml은 내부 산출물 → BLOCKED.

    격리: PIPELINE_STATE_PATH로 state를 tmp_path에 격리.
    CLI Evidence Contract: PIPELINE_STATE_PATH isolation + final_state assertion.
    """
    # PIPELINE_STATE_PATH isolation via _run_pipeline helper
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "build_report.xml"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_path.write_text("<build_report/>", encoding="utf-8")

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )
    _assert_blocked_not_deployable(result, req_path)
    # final_state: BLOCKED이므로 acceptance_request.json 미생성 확인
    final_state = {} if not req_path.exists() else json.loads(req_path.read_text(encoding="utf-8"))
    assert not final_state, f"BLOCKED인데 acceptance_request.json 생성됨: {final_state}"


# ─── TC-4: human_acceptance_packet.md → BLOCKED ───────────────────────────────


def test_tc4_acceptance_packet_blocked(tmp_path: Path) -> None:
    """TC-4 (error): human_acceptance_packet.md은 내부 산출물 → BLOCKED.

    격리: PIPELINE_STATE_PATH로 state를 tmp_path에 격리.
    CLI Evidence Contract: PIPELINE_STATE_PATH isolation + final_state assertion.
    """
    # PIPELINE_STATE_PATH isolation via _run_pipeline helper
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_path = tmp_path / "human_acceptance_packet.md"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_path.write_text("# packet\n내부 산출물", encoding="utf-8")

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )
    _assert_blocked_not_deployable(result, req_path)
    # final_state: BLOCKED이므로 acceptance_request.json 미생성 확인
    final_state = {} if not req_path.exists() else json.loads(req_path.read_text(encoding="utf-8"))
    assert not final_state, f"BLOCKED인데 acceptance_request.json 생성됨: {final_state}"


# ─── TC-5: https URL → BLOCKED 없음 ───────────────────────────────────────────


def test_tc5_url_evidence_allowed(tmp_path: Path) -> None:
    """TC-5 (edge): https URL은 항상 배포 가능 → evidence_not_deployable 차단 없음.

    격리: PIPELINE_STATE_PATH로 state를 tmp_path에 격리.
    CLI Evidence Contract: PIPELINE_STATE_PATH isolation + final_state assertion.
    """
    # PIPELINE_STATE_PATH isolation via _run_pipeline helper
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"

    _write_state(state_path, DUMMY_PIPELINE_ID)

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", "https://example.com/result.html"],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )

    combined = (result.stdout or "") + (result.stderr or "")
    assert "evidence_not_deployable" not in combined, (
        f"URL evidence가 차단됨\n{combined}"
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert req_path.exists(), (
        f"URL evidence로 nonce 발급 실패\n{combined}"
    )
    # final_state: acceptance_request.json에 nonce 확인
    final_state = json.loads(req_path.read_text(encoding="utf-8"))
    assert final_state.get("nonce"), f"nonce 필드 누락: {final_state}"


# ─── TC-6: acceptance_request.json → BLOCKED ──────────────────────────────────


def test_tc6_acceptance_request_blocked(tmp_path: Path) -> None:
    """TC-6 (error): acceptance_request.json은 내부 산출물 → BLOCKED.

    격리: PIPELINE_STATE_PATH로 state를 tmp_path에 격리.
    CLI Evidence Contract: PIPELINE_STATE_PATH isolation + final_state assertion.
    evidence 파일명을 다른 이름으로 두면 격리된 req_path와 충돌하지 않으므로,
    내부 산출물 판정 대상 파일은 별도 하위 폴더에 둔다.
    """
    # PIPELINE_STATE_PATH isolation via _run_pipeline helper
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    evidence_dir = tmp_path / "evid"
    evidence_dir.mkdir()
    evidence_path = evidence_dir / "acceptance_request.json"

    _write_state(state_path, DUMMY_PIPELINE_ID)
    evidence_path.write_text("{}", encoding="utf-8")

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )
    _assert_blocked_not_deployable(result, req_path)
    # final_state: BLOCKED이므로 acceptance_request.json 미생성 확인
    final_state = {} if not req_path.exists() else json.loads(req_path.read_text(encoding="utf-8"))
    assert not final_state, f"BLOCKED인데 acceptance_request.json 생성됨: {final_state}"


# ─── TC-7: pipeline_contracts/ 경로 → BLOCKED ─────────────────────────────────


def test_tc7_pipeline_contracts_blocked(tmp_path: Path) -> None:
    """TC-7 (error): pipeline_contracts/ 디렉터리 경로는 내부 산출물 → BLOCKED.

    격리: PIPELINE_STATE_PATH로 state를 tmp_path에 격리.
    CLI Evidence Contract: PIPELINE_STATE_PATH isolation + final_state assertion.
    _is_internal_artifact는 상대 경로의 디렉터리 접두사로 판정하므로,
    cwd 기준 상대 경로 'pipeline_contracts/IMP-20260612-CE06/foo.txt'를 evidence로 전달한다.
    """
    # PIPELINE_STATE_PATH isolation via _run_pipeline helper
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"
    contract_dir = tmp_path / "pipeline_contracts" / DUMMY_PIPELINE_ID
    contract_dir.mkdir(parents=True)
    (contract_dir / "foo.txt").write_text("internal", encoding="utf-8")

    _write_state(state_path, DUMMY_PIPELINE_ID)

    # cwd=tmp_path이므로 상대 경로로 전달하여 디렉터리 접두사 매칭을 유발
    rel_evidence = f"pipeline_contracts/{DUMMY_PIPELINE_ID}/foo.txt"

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", rel_evidence],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )
    _assert_blocked_not_deployable(result, req_path)
    # final_state: BLOCKED이므로 acceptance_request.json 미생성 확인
    final_state = {} if not req_path.exists() else json.loads(req_path.read_text(encoding="utf-8"))
    assert not final_state, f"BLOCKED인데 acceptance_request.json 생성됨: {final_state}"


# ─── TC-8: .pipeline/ 경로 직접 차단 (AC-2) ────────────────────────────────────


def test_tc8_pipeline_dir_blocked(tmp_path: Path) -> None:
    """TC-8 (error): .pipeline/ 경로를 evidence로 사용하면 evidence_not_deployable로 BLOCKED된다.

    격리: PIPELINE_STATE_PATH로 state를 tmp_path에 격리.
    CLI Evidence Contract: PIPELINE_STATE_PATH isolation + final_state assertion.
    절대 경로로 전달되더라도 경로 중간의 `/.pipeline/` 세그먼트를 내부 산출물로 감지한다.
    """
    # PIPELINE_STATE_PATH isolation via _run_pipeline helper
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"

    _write_state(state_path, DUMMY_PIPELINE_ID)

    # .pipeline/ 아래 파일 생성
    pipeline_dir = tmp_path / ".pipeline" / "phase_evidence"
    pipeline_dir.mkdir(parents=True)
    evidence_path = pipeline_dir / "something.json"
    evidence_path.write_text("{}", encoding="utf-8")

    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(evidence_path)],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )
    _assert_blocked_not_deployable(result, req_path)
    # final_state: BLOCKED이므로 acceptance_request.json 미생성 확인
    final_state = {} if not req_path.exists() else json.loads(req_path.read_text(encoding="utf-8"))
    assert not final_state, f"BLOCKED인데 acceptance_request.json 생성됨: {final_state}"


# ─── TC-9: outputs add 후에도 내부 산출물 차단 (AC-5, AC-9) ──────────────────────


def test_tc9_outputs_add_no_bypass(tmp_path: Path) -> None:
    """TC-9 (error): outputs add로 등록된 qa_report.xml도 request-accept 단계에서 BLOCKED된다.

    격리: PIPELINE_STATE_PATH로 state를 tmp_path에 격리.
    CLI Evidence Contract: PIPELINE_STATE_PATH isolation + final_state assertion.
    내부 산출물은 outputs add로 등록하더라도 evidence로 사용 시 차단되어야 한다.
    """
    # PIPELINE_STATE_PATH isolation via _run_pipeline helper
    state_path = tmp_path / "pipeline_state.json"
    req_path = tmp_path / "acceptance_request.json"

    _write_state(state_path, DUMMY_PIPELINE_ID)

    # qa_report.xml 생성
    qa_report = tmp_path / "qa_report.xml"
    qa_report.write_text("<qa_report></qa_report>", encoding="utf-8")

    # outputs add로 등록 (이 자체는 성공할 것임)
    _run_pipeline(
        ["outputs", "add", "--kind", "report", "--path", "qa_report.xml", "--label", "test"],
        state_path,
        cwd=tmp_path,
    )

    # 등록 후에도 request-accept에서 차단되어야 함
    result = _run_pipeline(
        ["gates", "request-accept", "--evidence", str(qa_report)],
        state_path,
        cwd=tmp_path,
        extra_env=_fake_gh_env(tmp_path),
    )
    _assert_blocked_not_deployable(result, req_path)
    # final_state: BLOCKED이므로 acceptance_request.json 미생성 확인
    final_state = {} if not req_path.exists() else json.loads(req_path.read_text(encoding="utf-8"))
    assert not final_state, f"BLOCKED인데 acceptance_request.json 생성됨: {final_state}"
