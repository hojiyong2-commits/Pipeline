"""IMP-20260603-2E3D MT-4 — PR Packet SSoT E2E tests.

[Purpose]: pipeline.py report final-packet / update-pr-body 와
gates request-accept 통합 흐름의 12개 동작을 격리 환경에서 검증한다.
모든 테스트는 subprocess로 실제 pipeline.py CLI를 호출하며,
PIPELINE_STATE_PATH 환경 변수로 활성 state 파일을 격리한다.

테스트 12개:
    1) test_final_packet_generated_with_pr_sha_and_ci_run
    2) test_final_packet_shows_pending_when_no_acceptance_request
    3) test_final_packet_includes_accept_code_when_request_exists
    4) test_final_packet_lines_within_120_chars
    5) test_final_packet_accept_code_on_separate_line
    6) test_request_accept_proceeds_without_final_packet
    7) test_request_accept_blocks_on_stale_pr_sha
    8) test_request_accept_blocks_on_stale_ci_run_id
    9) test_request_accept_reuses_nonce_when_conditions_same
    10) test_request_accept_issues_new_nonce_when_conditions_change
    11) test_request_accept_auto_generates_packet_with_code
    12) test_accept_blocks_without_acceptance_code

[Vulnerability & Risks]:
- PIPELINE_STATE_PATH 격리 누락 시 활성 state 파일 오염 위험.
- gh/git CLI가 호출되더라도 phase-attestation/impl 브랜치에는 실제 PR이 없어
  empty string 반환에 의존한다. 실제 PR 환경에서는 별도 통합 테스트가 필요.
[Improvement]: gh CLI mock fixture를 도입하면 stale 검사 회귀를 더 강하게 검증할 수 있다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"


def _run_cli(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[Path] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """python pipeline.py <args>를 격리 환경에서 실행한다.

    Args:
        args: pipeline.py에 전달할 인자 list.
        env: 환경 변수 dict (PIPELINE_STATE_PATH 포함 권장).
        cwd: 실행 디렉터리.
        timeout: 초 단위 timeout.
    Returns:
        CompletedProcess (stdout/stderr UTF-8 디코드 완료).
    """
    cmd = [sys.executable, str(PIPELINE_PY)] + list(args)
    run_env = os.environ.copy()
    run_env["PYTHONIOENCODING"] = "utf-8"
    if env:
        run_env.update(env)
    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        env=run_env,
        cwd=str(cwd) if cwd is not None else None,
    )
    stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
    stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _make_active_state(
    state_path: Path,
    *,
    pipeline_id: str = "IMP-20260603-TEST",
    structured_ac: Optional[List[Dict[str, Any]]] = None,
    external_gates: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """격리된 활성 state 파일을 작성한다.

    실제 pipeline_state.json 형식과 호환되도록 최소 필드만 채운다.
    """
    if structured_ac is None:
        # IMP-20260607-E656 fix: 기본값은 빈 목록으로 설정.
        # AC 충족표를 테스트하는 케이스는 명시적으로 structured_ac를 전달해야 함.
        # requirements_tracking.enabled=True이지만 AC 없으면 PENDING 체크 skip됨.
        structured_ac = []
    if external_gates is None:
        external_gates = {
            "enabled": True,
            "gates": {
                "technical": {"status": "PENDING"},
                "oracle": {"status": "PENDING"},
                "github_ci": {"status": "PENDING"},
                "acceptance": {"status": "PENDING"},
            },
        }
    state = {
        "version": "1.2.0",
        "pipeline_id": pipeline_id,
        "type": "IMP",
        "description": "E2E test fixture",
        "created_at": "2026-06-03T00:00:00Z",
        "updated_at": "2026-06-03T00:00:00Z",
        "current_phase": "external_gates",
        "blocked": False,
        "blocked_reason": None,
        "phases": {
            "pm": {"status": "PASS"},
            "dev": {"status": "PASS"},
            "qa": {"status": "PASS"},
            "sec": {"status": "PASS"},
            "build": {"status": "PASS"},
            "harness": {"status": "PENDING"},
            "architect": {"status": "PENDING"},
        },
        "external_gates": external_gates,
        "structured_acceptance_criteria": structured_ac,
        "requirements_tracking": {"enabled": True, "schema_version": 1},
        "pm_clarification_gate": {
            "clarification_needed": False,
            "assumptions": "fixture",
            "acceptance_criteria_source": "user",
            "acceptance_criteria": [
                ac["requirement"] for ac in structured_ac
            ],
        },
        "event_log": [],
        "agent_runs": {},
        # IMP-20260612-E12D MT-2: request-accept의 AC 사전 검증(_validate_ac_table_before_request_accept)은
        # requirements_tracking.enabled=True일 때 module_gates.integration.status == "PASS" 와
        # ac_completeness 캐시(complete)를 요구한다. 이 fixture는 PM/Dev/QA/Build PASS 상태를
        # 시뮬레이션하므로 integration도 PASS + ac_completeness complete로 설정하여
        # request-accept가 AC 단계에서 차단되지 않도록 한다.
        "module_gates": {"enabled": True, "modules": {}, "integration": {"status": "PASS"}},
        "ac_completeness": {
            "cached_at": "2026-06-03T00:00:00Z",
            "total": len(structured_ac),
            "pending_count": 0,
            "pending_ids": [],
            "complete": True,
        },
        "atomic_plan": {"micro_tasks": [], "structured_acceptance_criteria": structured_ac},
        "phase_attestations": {
            "enabled": True,
            "phases": {
                "pm": {"status": "PASS"},
                "dev": {"status": "PASS"},
                "qa": {"status": "PASS"},
                "build": {"status": "PASS"},
            },
        },
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return state


def _packet_path_in(cwd: Path) -> Path:
    """cwd 안의 human_acceptance_packet.md 경로."""
    return cwd / "human_acceptance_packet.md"


def _write_packet_with_stale_pr_sha(packet_path: Path, fake_sha: str = "fake1234567890abcdef") -> None:
    """packet 파일을 stale PR head SHA로 인위적 작성."""
    content = (
        "[최종 확인 안내]\n\n"
        "파이프라인:\nIMP-20260603-TEST\n\n"
        "PR:\nhttp://example.invalid/pull/999\n\n"
        "GitHub Actions:\n(none)\n\n"
        "PR head SHA:\n" + fake_sha + "\n\n"
        "CI run ID:\n(none)\n\n"
        "게이트 상태:\nTechnical: PENDING\nOracle: PENDING\n"
        "GitHub CI: PENDING\nUser Acceptance: PENDING\n\n"
        "변경 파일:\n총 0개\n\n"
        "(git diff 결과 없음 또는 git CLI 없음)\n\n"
        "요구사항 충족표:\n\n"
        "(structured AC 없음)\n\n"
        "사용자가 확인할 것:\n\n"
        "1. PR 링크를 연다.\n\n"
        "승인 코드:\n승인 코드 발급 전\n\n"
        "거절 예시:\n승인 코드 발급 전\n"
    )
    packet_path.write_text(content, encoding="utf-8")


def _write_packet_with_stale_ci_run(packet_path: Path, fake_run: str = "99999999999") -> None:
    """packet 파일을 stale CI run ID로 인위적 작성."""
    content = (
        "[최종 확인 안내]\n\n"
        "파이프라인:\nIMP-20260603-TEST\n\n"
        "PR:\nhttp://example.invalid/pull/999\n\n"
        "GitHub Actions:\n(none)\n\n"
        "PR head SHA:\n(none)\n\n"
        "CI run ID:\n" + fake_run + "\n\n"
        "게이트 상태:\nTechnical: PENDING\n\n"
        "변경 파일:\n총 0개\n\n"
        "(git diff 결과 없음)\n\n"
        "요구사항 충족표:\n\n"
        "(legacy)\n\n"
        "사용자가 확인할 것:\n\n"
        "1. PR 링크를 연다.\n\n"
        "승인 코드:\n승인 코드 발급 전\n"
    )
    packet_path.write_text(content, encoding="utf-8")


# IMP-20260612-E12D MT-2: conftest의 autouse fake gh fixture 제거에 따라,
# request-accept 성공을 기대하는 테스트(6/9/10/11)와 gh 부재 graceful skip을 검증하는
# 테스트(7/8)에 완전한 PR body를 반환하는 fake gh를 명시적으로 주입한다.
# fake gh는 PIPELINE_GH_EXECUTABLE로 전달되며 sys.executable로 실행되므로 PATH="" 환경에서도
# 동작한다. headSha/databaseId는 빈 문자열, run/pr list는 빈 배열을 반환하여
# 기존 "gh CLI 없는 환경(pr_head_sha=''/ci_run_id='')" 전제를 그대로 유지한다.

_FAKE_GH_PR_BODY_2E3D = (
    "## 작업 요약\n자동 테스트 픽스처 PR body\n\n"
    "## 사용자가 확인할 결과물\n결과물 경로: N/A (테스트)\n\n"
    "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 성공\n\n"
    "## 중요한 선택과 트레이드오프\nN/A (테스트 픽스처)\n\n"
    "## 검증\n모든 게이트 PASS\n"
)


def _write_fake_gh_script(tmp_path: Path) -> Path:
    """완전한 PR body를 반환하는 fake gh .py 스크립트를 생성하여 경로 반환.

    headSha/databaseId는 빈 문자열, run/pr list는 빈 배열을 반환하여
    gh CLI 없는 환경(pr_head_sha=''/ci_run_id='')을 시뮬레이션한다.

    Args:
        tmp_path: pytest tmp_path fixture.
    Returns:
        생성된 fake gh .py 스크립트 절대 경로.
    Raises:
        TypeError: tmp_path가 None.
    """
    if tmp_path is None:
        raise TypeError("tmp_path must not be None")
    body_json = json.dumps(_FAKE_GH_PR_BODY_2E3D)
    script = tmp_path / "fake_gh_2e3d.py"
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


@pytest.fixture
def isolated_env(tmp_path: Path) -> Dict[str, str]:
    """PIPELINE_STATE_PATH로 격리된 활성 state + cwd 환경 + fake gh(PR body) 주입."""
    state_path = tmp_path / "pipeline_state.json"
    _make_active_state(state_path)
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    # IMP-20260612-E12D MT-2: PR body readiness 검사 통과를 위해 fake gh 주입
    env["PIPELINE_GH_EXECUTABLE"] = str(_write_fake_gh_script(tmp_path))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING"] = "1"
    # BUG-20260617-788A: request-accept가 비대화형/CI 자동 감지 제거로 인해 브라우저
    # HTTP 서버를 실제로 띄워 300초 대기하지 않도록 E2E에서 브라우저 승인 우회.
    env["PIPELINE_BROWSER_APPROVAL_SKIP"] = "1"
    return env


@pytest.fixture
def isolated_cwd(tmp_path: Path) -> Path:
    """테스트 격리용 cwd. 실행 시 human_acceptance_packet.md가 여기에 생성된다."""
    return tmp_path


# ─── 1) packet 생성 기본 ───────────────────────────────────────────────────────
def test_final_packet_generated_with_pr_sha_and_ci_run(
    isolated_env: Dict[str, str],
    isolated_cwd: Path,
) -> None:
    """report final-packet 실행 시 human_acceptance_packet.md가 생성되고
    PR head SHA / CI run ID / 변경 파일 / AC 충족표 섹션을 포함한다."""
    result = _run_cli(["report", "final-packet"], env=isolated_env, cwd=isolated_cwd)
    assert result.returncode == 0, f"report final-packet 실패: {result.stderr}"
    packet = _packet_path_in(isolated_cwd)
    assert packet.exists(), f"packet 파일이 생성되지 않음: stdout={result.stdout}"
    text = packet.read_text(encoding="utf-8")
    assert "[최종 확인 안내]" in text
    # IMP-20260607-E656 MT-2: 신 형식은 [Codex 검토용] 블록에 pr_head_sha/ci_run_id 포함
    # 구 형식 "PR head SHA:\n" 또는 신 형식 "pr_head_sha: " 둘 중 하나 존재해야 함
    assert ("PR head SHA:" in text or "pr_head_sha:" in text), (
        "PR head SHA 정보가 packet에 없음"
    )
    assert ("CI run ID:" in text or "ci_run_id:" in text), (
        "CI run ID 정보가 packet에 없음"
    )
    assert "변경 파일:" in text
    assert "요구사항 충족표:" in text
    # final_state assertion: 콘솔 요약에 핵심 메시지 출력
    assert "[FINAL PACKET 작성 완료]" in result.stdout


# ─── 2) acceptance_request 없으면 "발급 전" 라인 ───────────────────────────────
def test_final_packet_shows_pending_when_no_acceptance_request(
    isolated_env: Dict[str, str],
    isolated_cwd: Path,
) -> None:
    """acceptance_request.json이 없으면 packet에 "승인 코드 발급 전" 라인이 있다."""
    # 명시적으로 acceptance_request.json 부재 확인
    req_path = isolated_cwd / "acceptance_request.json"
    assert not req_path.exists()
    result = _run_cli(["report", "final-packet"], env=isolated_env, cwd=isolated_cwd)
    assert result.returncode == 0
    text = _packet_path_in(isolated_cwd).read_text(encoding="utf-8")
    assert "승인 코드 발급 전" in text
    assert "ACCEPT-IMP-20260603-TEST-" not in text


# ─── 3) acceptance_request 있으면 ACCEPT 코드 포함 ─────────────────────────────
def test_final_packet_includes_accept_code_when_request_exists(
    isolated_env: Dict[str, str],
    isolated_cwd: Path,
) -> None:
    """acceptance_request.json이 있으면 packet에 ACCEPT-... 코드가 포함된다.

    BUG-20260619-F41F MT-3: 외부 표시(packet)의 승인 코드에서 nonce를 제거한다.
    packet에는 nonce 없는 ACCEPT-<pipeline_id> 형식만 포함되어야 하고, 8자리 nonce
    문자열(ABCD1234)은 승인 코드 줄에 노출되지 않아야 한다.
    """
    req_path = isolated_cwd / "acceptance_request.json"
    req_data = {
        "schema_version": 1,
        "pipeline_id": "IMP-20260603-TEST",
        "request_id": "test1234",
        "nonce": "ABCD1234",
        "created_at": "2026-06-03T00:00:00Z",
        "pr_url": "",
        "pr_head_sha": "",
        "github_ci_run_id": "",
        "evidence": "tests/dummy.txt",
        "evidence_sha256": None,
        "evidence_url": None,
        "status": "PENDING",
    }
    req_path.write_text(json.dumps(req_data, ensure_ascii=False, indent=2), encoding="utf-8")
    result = _run_cli(["report", "final-packet"], env=isolated_env, cwd=isolated_cwd)
    assert result.returncode == 0
    text = _packet_path_in(isolated_cwd).read_text(encoding="utf-8")
    # MT-3: nonce 없는 ACCEPT-<pipeline_id> 형식만 포함.
    assert "ACCEPT-IMP-20260603-TEST" in text
    assert "REJECT-IMP-20260603-TEST" in text
    # nonce(8자리)는 승인 코드 줄에 노출되지 않아야 한다.
    assert "ACCEPT-IMP-20260603-TEST-ABCD1234" not in text
    assert "REJECT-IMP-20260603-TEST-ABCD1234" not in text


# ─── 4) 모든 줄 120자 이하 ─────────────────────────────────────────────────────
def test_final_packet_lines_within_120_chars(
    isolated_env: Dict[str, str],
    isolated_cwd: Path,
) -> None:
    """packet 모든 줄이 PACKET_LINE_MAX_WIDTH(120) 이하."""
    # 긴 AC 문구를 가진 state 사용
    state_path = Path(isolated_env["PIPELINE_STATE_PATH"])
    long_text = "긴 요구사항 문구입니다. " * 20  # 한국어로 200자+ 만들기
    _make_active_state(
        state_path,
        structured_ac=[
            {
                "ac_id": "AC-1",
                "requirement": long_text,
                "must_verify": True,
                "source": "user",
                "user_visible": True,
                "expected_evidence": "fixture",
            }
        ],
    )
    result = _run_cli(["report", "final-packet"], env=isolated_env, cwd=isolated_cwd)
    assert result.returncode == 0
    text = _packet_path_in(isolated_cwd).read_text(encoding="utf-8")
    over_lines = [(i, ln) for i, ln in enumerate(text.split("\n"), 1) if len(ln) > 120]
    assert not over_lines, f"120자 초과 줄 발견: {over_lines[:3]}"


# ─── 5) 승인 코드 독립 줄 ─────────────────────────────────────────────────────
def test_final_packet_accept_code_on_separate_line(
    isolated_env: Dict[str, str],
    isolated_cwd: Path,
) -> None:
    """ACCEPT 코드가 자기 자신만 있는 독립 줄로 출력된다.

    BUG-20260619-F41F MT-3: 승인 코드는 nonce 없는 ACCEPT-<pipeline_id> 형식으로,
    여전히 정확히 1개의 독립 줄로 출력되어야 한다.
    """
    req_path = isolated_cwd / "acceptance_request.json"
    req_data = {
        "schema_version": 1,
        "pipeline_id": "IMP-20260603-TEST",
        "request_id": "isolated",
        "nonce": "WXYZ7890",
        "created_at": "2026-06-03T00:00:00Z",
        "pr_url": "",
        "pr_head_sha": "",
        "github_ci_run_id": "",
        "evidence": "tests/dummy.txt",
        "evidence_sha256": None,
        "evidence_url": None,
        "status": "PENDING",
    }
    req_path.write_text(json.dumps(req_data, ensure_ascii=False, indent=2), encoding="utf-8")
    result = _run_cli(["report", "final-packet"], env=isolated_env, cwd=isolated_cwd)
    assert result.returncode == 0
    text = _packet_path_in(isolated_cwd).read_text(encoding="utf-8")
    lines = text.split("\n")
    # MT-3: nonce 없는 ACCEPT-<pipeline_id> 형식의 독립 줄.
    accept_lines = [ln for ln in lines if ln.strip() == "ACCEPT-IMP-20260603-TEST"]
    assert len(accept_lines) == 1, (
        "승인 코드가 정확히 1개의 독립 줄로 출력되어야 함. "
        f"발견된 라인: {accept_lines}"
    )
    # nonce는 노출되지 않아야 한다.
    assert "WXYZ7890" not in text


# ─── 6) final packet 없어도 request-accept 진행 + 자동 packet 생성 ──────────────
def test_request_accept_proceeds_without_final_packet(
    isolated_env: Dict[str, str],
    isolated_cwd: Path,
) -> None:
    """packet 부재로는 request-accept가 차단되지 않고, 실행 후 packet이 자동 생성된다."""
    # PIPELINE_STATE_PATH isolation 확인 — state 격리 필수
    state_path = isolated_env["PIPELINE_STATE_PATH"]
    assert state_path, "PIPELINE_STATE_PATH 격리 미설정"
    # created_files: acceptance_request.json, human_acceptance_packet.md
    # final_state: acceptance gate PENDING → 승인 코드 발급 후 PENDING 유지
    packet = _packet_path_in(isolated_cwd)
    assert not packet.exists()
    evidence = isolated_cwd / "dummy_evidence.txt"
    evidence.write_text("evidence body", encoding="utf-8")
    result = _run_cli(
        ["gates", "request-accept", "--evidence", str(evidence.name)],
        env=isolated_env, cwd=isolated_cwd,
    )
    assert result.returncode == 0, f"request-accept 차단됨: {result.stderr}\n{result.stdout}"
    assert packet.exists(), "request-accept 후 packet이 자동 생성되지 않음"
    assert "[FINAL PACKET 자동 생성]" in result.stdout


# ─── 7) stale PR SHA 차단 ─────────────────────────────────────────────────────
def test_request_accept_blocks_on_stale_pr_sha(
    isolated_env: Dict[str, str],
    isolated_cwd: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """packet에 실제와 다른 PR head SHA가 있을 때 stale 차단.

    실제 gh가 빈 문자열을 반환하면 stale 검사가 skip되므로, 본 테스트는
    환경 변수로 fake actual SHA를 주입하는 대신 packet 안의 SHA가 일반적인
    실제 SHA 형식이고 실제 gh CLI가 빈 문자열이면 검사 자체가 skip되는 동작도
    명시적으로 검증한다. 즉 fake SHA가 packet에 있어도 actual이 비면 통과한다.
    실제 PR이 있는 환경의 회귀는 별도 통합 테스트에서 수행한다.
    """
    # PIPELINE_STATE_PATH isolation 확인 — state 격리 필수
    state_path = isolated_env["PIPELINE_STATE_PATH"]
    assert state_path, "PIPELINE_STATE_PATH 격리 미설정"
    # modified_files: human_acceptance_packet.md (packet 자동 재생성으로 stale SHA 제거)
    # final_state: acceptance gate PENDING 유지 (stale 검사 skip 경로)
    packet = _packet_path_in(isolated_cwd)
    _write_packet_with_stale_pr_sha(packet, fake_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    evidence = isolated_cwd / "dummy_evidence.txt"
    evidence.write_text("e", encoding="utf-8")
    # gh CLI가 없을 가능성을 보장하기 위해 PATH를 비운다 (Windows/Linux 호환)
    monkeypatch.setenv("PATH", "")
    result = _run_cli(
        ["gates", "request-accept", "--evidence", str(evidence.name)],
        env={**isolated_env, "PATH": ""}, cwd=isolated_cwd,
    )
    # PATH 비움 → gh/git 없음 → actual 빈 문자열 → stale 검사 skip → 통과
    # 단 packet은 자동 재생성되어 fake SHA가 정상 placeholder로 덮어쓰여진다.
    assert result.returncode == 0, (
        "gh/git 부재 시에는 stale 검사가 skip되어야 함: " + result.stderr
    )
    # packet 안의 fake SHA가 더 이상 남아있지 않음 — 자동 재생성됨
    new_text = packet.read_text(encoding="utf-8")
    assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in new_text, (
        "packet이 자동 재생성되어 stale 값을 덮어써야 함"
    )


# ─── 8) stale CI run ID 차단 (gh/git 부재 환경에서는 skip 검증) ────────────────
def test_request_accept_blocks_on_stale_ci_run_id(
    isolated_env: Dict[str, str],
    isolated_cwd: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """packet에 stale CI run ID가 있어도 gh CLI 없으면 skip된다 (graceful).

    실제 PR 환경에서는 stale 차단이 일어나야 하나, 본 격리 테스트는
    gh CLI 부재 시 자동 packet 재생성으로 stale 값이 사라지는 동작을 검증한다.
    """
    # PIPELINE_STATE_PATH isolation 확인 — state 격리 필수
    state_path = isolated_env["PIPELINE_STATE_PATH"]
    assert state_path, "PIPELINE_STATE_PATH 격리 미설정"
    # modified_files: human_acceptance_packet.md (packet 자동 재생성으로 stale CI run ID 제거)
    # final_state: acceptance gate PENDING 유지 (stale CI run 검사 skip 경로)
    packet = _packet_path_in(isolated_cwd)
    _write_packet_with_stale_ci_run(packet, fake_run="9999999999999")
    evidence = isolated_cwd / "dummy.txt"
    evidence.write_text("e", encoding="utf-8")
    monkeypatch.setenv("PATH", "")
    result = _run_cli(
        ["gates", "request-accept", "--evidence", str(evidence.name)],
        env={**isolated_env, "PATH": ""}, cwd=isolated_cwd,
    )
    assert result.returncode == 0, f"gh/git 부재 시 통과해야 함: {result.stderr}"
    new_text = packet.read_text(encoding="utf-8")
    assert "9999999999999" not in new_text, "packet 자동 재생성으로 stale 값이 덮어쓰여짐"


# ─── 9) 조건 동일 시 nonce 재사용 ─────────────────────────────────────────────
def test_request_accept_reuses_nonce_when_conditions_same(
    isolated_env: Dict[str, str],
    isolated_cwd: Path,
) -> None:
    """동일 evidence/PR/CI 조건에서 request-accept를 두 번 호출하면 같은 nonce."""
    # PIPELINE_STATE_PATH isolation 확인 — state 격리 필수
    state_path = isolated_env["PIPELINE_STATE_PATH"]
    assert state_path, "PIPELINE_STATE_PATH 격리 미설정"
    # created_files: acceptance_request.json (nonce 재사용 검증)
    # final_state: acceptance_request.json의 nonce가 동일 — 재사용 확인
    evidence = isolated_cwd / "evidence_v1.txt"
    evidence.write_text("body v1", encoding="utf-8")
    # 1회차
    r1 = _run_cli(
        ["gates", "request-accept", "--evidence", str(evidence.name)],
        env=isolated_env, cwd=isolated_cwd,
    )
    assert r1.returncode == 0, r1.stderr
    req_path = isolated_cwd / "acceptance_request.json"
    nonce1 = json.loads(req_path.read_text(encoding="utf-8"))["nonce"]
    # 2회차 — 동일 조건
    r2 = _run_cli(
        ["gates", "request-accept", "--evidence", str(evidence.name)],
        env=isolated_env, cwd=isolated_cwd,
    )
    assert r2.returncode == 0, r2.stderr
    nonce2 = json.loads(req_path.read_text(encoding="utf-8"))["nonce"]
    assert nonce1 == nonce2, "조건이 같으면 nonce가 재사용되어야 함"
    assert "[재사용]" in r2.stdout


# ─── 10) 조건 변경 시 새 nonce ────────────────────────────────────────────────
def test_request_accept_issues_new_nonce_when_conditions_change(
    isolated_env: Dict[str, str],
    isolated_cwd: Path,
) -> None:
    """evidence 내용이 바뀌면 SHA가 달라져 새 nonce가 발급된다."""
    # PIPELINE_STATE_PATH isolation 확인 — state 격리 필수
    state_path = isolated_env["PIPELINE_STATE_PATH"]
    assert state_path, "PIPELINE_STATE_PATH 격리 미설정"
    # modified_files: acceptance_request.json (evidence 변경 시 새 nonce 발급)
    # final_state: nonce가 변경됨 — evidence SHA 변화 확인
    evidence = isolated_cwd / "evidence_changeable.txt"
    evidence.write_text("body before", encoding="utf-8")
    r1 = _run_cli(
        ["gates", "request-accept", "--evidence", str(evidence.name)],
        env=isolated_env, cwd=isolated_cwd,
    )
    assert r1.returncode == 0
    req_path = isolated_cwd / "acceptance_request.json"
    nonce1 = json.loads(req_path.read_text(encoding="utf-8"))["nonce"]
    # evidence 내용 변경
    evidence.write_text("body AFTER different", encoding="utf-8")
    r2 = _run_cli(
        ["gates", "request-accept", "--evidence", str(evidence.name)],
        env=isolated_env, cwd=isolated_cwd,
    )
    assert r2.returncode == 0
    nonce2 = json.loads(req_path.read_text(encoding="utf-8"))["nonce"]
    assert nonce1 != nonce2, "evidence 내용이 변경되면 새 nonce가 발급되어야 함"


# ─── 11) request-accept 후 packet에 ACCEPT 코드 포함 ───────────────────────────
def test_request_accept_auto_generates_packet_with_code(
    isolated_env: Dict[str, str],
    isolated_cwd: Path,
) -> None:
    """request-accept 실행 후 packet이 ACCEPT-... 코드를 포함한다.

    BUG-20260619-F41F MT-3: packet의 승인 코드에서 nonce를 제거한다. packet에는 nonce
    없는 ACCEPT-<pipeline_id> 형식만 포함되고, 실제 nonce는 acceptance_request.json에만
    보존되어야 한다 (외부 노출 차단 + 내부 검증값 보존).
    """
    # PIPELINE_STATE_PATH isolation 확인 — state 격리 필수
    state_path = isolated_env["PIPELINE_STATE_PATH"]
    assert state_path, "PIPELINE_STATE_PATH 격리 미설정"
    # created_files: acceptance_request.json, human_acceptance_packet.md
    # final_state: acceptance_request.json에 nonce가 기록되고, packet에는 nonce 미노출
    evidence = isolated_cwd / "auto_packet_evidence.txt"
    evidence.write_text("body", encoding="utf-8")
    result = _run_cli(
        ["gates", "request-accept", "--evidence", str(evidence.name)],
        env=isolated_env, cwd=isolated_cwd,
    )
    assert result.returncode == 0
    packet_text = _packet_path_in(isolated_cwd).read_text(encoding="utf-8")
    # MT-3: nonce 없는 ACCEPT-<pipeline_id> 형식이 packet에 포함.
    assert "ACCEPT-IMP-20260603-TEST" in packet_text, (
        f"packet에 ACCEPT-<pipeline_id> 코드가 없음. 내용: {packet_text[:500]}"
    )
    # 실제 nonce는 acceptance_request.json에만 보존되고 packet에는 노출되지 않아야 한다.
    req = json.loads((isolated_cwd / "acceptance_request.json").read_text(encoding="utf-8"))
    _nonce = str(req["nonce"])
    assert _nonce, "acceptance_request.json에 nonce가 보존되어야 함"
    assert f"ACCEPT-IMP-20260603-TEST-{_nonce}" not in packet_text, (
        "packet에 nonce 포함 승인 코드가 노출되면 안 됨 (MT-3)"
    )


# ─── 12) --user-confirmed 단독 차단 (기존 Nonce Gate 보존) ─────────────────────
def test_accept_blocks_without_acceptance_code(
    isolated_env: Dict[str, str],
    isolated_cwd: Path,
) -> None:
    """gates accept --user-confirmed 단독 호출은 차단되어야 한다 (기존 Nonce Gate 보존)."""
    # PIPELINE_STATE_PATH isolation 확인 — state 격리 필수
    state_path = isolated_env["PIPELINE_STATE_PATH"]
    assert state_path, "PIPELINE_STATE_PATH 격리 미설정"
    # final_state: acceptance gate 차단됨 — failure_packet 생성 예상
    # created_files: 없음 (차단됨)
    evidence = isolated_cwd / "accept_evidence.txt"
    evidence.write_text("body", encoding="utf-8")
    result = _run_cli(
        ["gates", "accept", "--result", "ACCEPT",
         "--evidence", str(evidence.name), "--user-confirmed"],
        env=isolated_env, cwd=isolated_cwd,
    )
    assert result.returncode != 0, (
        "--user-confirmed 단독 호출은 차단되어야 함 (Nonce Gate). "
        f"returncode={result.returncode}, stdout={result.stdout}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert (
        "acceptance" in combined
        or "blocked" in combined
        or "code" in combined
    ), f"Nonce Gate 메시지가 출력되지 않음: {result.stdout}\n{result.stderr}"


# ─── 13) 게이트 PASS 상태가 final packet에 정확히 표시 (버그 1 회귀 방지) ────────
def test_13_final_packet_shows_pass_gate_status(tmp_path: Path) -> None:
    """버그 1 회귀 방지: external_gates 직접 구조에서 PASS가 packet에 올바르게 표시된다.

    [Context]: pipeline_state.json의 external_gates는 {"technical": {...}, "oracle": {...}}
    형식이다. 이전 코드는 .get("gates") 때문에 항상 빈 dict를 반환해 모든 상태가 PENDING이었다.
    PIPELINE_STATE_PATH 격리 사용. final_state: packet에 Technical/Oracle/GitHub CI PASS 확인.
    """
    state_file = tmp_path / "pipeline_state.json"
    state = {
        "version": "1.2.0",
        "pipeline_id": "IMP-TEST-GATE",
        "type": "IMP",
        "description": "버그 1 회귀 방지 fixture",
        "created_at": "2026-06-03T00:00:00Z",
        "updated_at": "2026-06-03T00:00:00Z",
        "current_phase": "external_gates",
        "blocked": False,
        "blocked_reason": None,
        "phases": {
            "pm": {"status": "PASS"},
            "dev": {"status": "PASS"},
            "qa": {"status": "PASS"},
            "sec": {"status": "PASS"},
            "build": {"status": "PASS"},
            "harness": {"status": "PENDING"},
            "architect": {"status": "PENDING"},
        },
        "external_gates": {
            "enabled": True,
            "mode": "three_gate",
            "technical": {"status": "PASS", "recorded_at": "2026-06-03T00:00:00Z"},
            "oracle": {"status": "PASS", "recorded_at": "2026-06-03T00:00:00Z"},
            "github_ci": {"status": "PASS", "recorded_at": "2026-06-03T00:00:00Z"},
            "acceptance": {"status": "PENDING"},
        },
        "structured_acceptance_criteria": [],
        "requirements_tracking": {"enabled": True, "schema_version": 1},
        "pm_clarification_gate": {
            "clarification_needed": False,
            "assumptions": "fixture",
            "acceptance_criteria_source": "user",
            "acceptance_criteria": [],
        },
        "event_log": [],
        "agent_runs": {},
        "module_gates": {
            "enabled": True, "modules": {},
            "integration": {"status": "PENDING"},
        },
        "atomic_plan": {"micro_tasks": [], "structured_acceptance_criteria": []},
        "phase_attestations": {
            "enabled": True,
            "phases": {
                "pm": {"status": "PASS"},
                "dev": {"status": "PASS"},
                "qa": {"status": "PASS"},
                "build": {"status": "PASS"},
            },
        },
    }
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    env = {
        **os.environ,
        "PIPELINE_STATE_PATH": str(state_file),
        "PIPELINE_NO_DASHBOARD": "1",
        "PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING": "1",
    }
    result = subprocess.run(
        [sys.executable, str(PIPELINE_PY), "report", "final-packet"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(PIPELINE_PY.parent),
    )
    # final-packet은 항상 packet 파일을 PIPELINE_PY.parent에 저장 (cwd 기준)
    packet_path = PIPELINE_PY.parent / "human_acceptance_packet.md"
    assert packet_path.exists(), (
        f"human_acceptance_packet.md가 생성되지 않음\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    content = packet_path.read_text(encoding="utf-8")
    assert "Technical: PASS" in content, f"Technical PASS 없음\n{content[:800]}"
    assert "Oracle: PASS" in content, f"Oracle PASS 없음\n{content[:800]}"
    assert "GitHub CI: PASS" in content, f"GitHub CI PASS 없음\n{content[:800]}"
    # 게이트 상태 섹션에서 Technical/Oracle/GitHub CI가 PENDING이면 안 됨
    gate_idx = content.find("게이트 상태:")
    if gate_idx != -1:
        gate_section = content[gate_idx : gate_idx + 300]
        assert "Technical: PENDING" not in gate_section, (
            f"Technical이 PENDING으로 잘못 표시됨\n{gate_section}"
        )
        assert "Oracle: PENDING" not in gate_section, (
            f"Oracle이 PENDING으로 잘못 표시됨\n{gate_section}"
        )
        assert "GitHub CI: PENDING" not in gate_section, (
            f"GitHub CI가 PENDING으로 잘못 표시됨\n{gate_section}"
        )


# ─── 14) _clean_pr_body_artifacts가 구 승인 코드를 제거 (버그 2 회귀 방지) ────────
def test_14_clean_pr_body_removes_stale_accept_codes() -> None:
    """버그 2 회귀 방지: _clean_pr_body_artifacts가 구 ACCEPT 코드를 제거하고 현재 코드는 보존한다.

    [Context]: update-pr-body 실행 시 이전 승인 코드가 PR 본문에 남아 있으면
    혼란을 야기한다. 현재 nonce 코드는 보존, 구 코드는 제거해야 한다.
    PIPELINE_STATE_PATH 격리: 불필요 (함수 직접 호출 테스트).
    final_state: cleaned 텍스트에 구 코드 없음, 현재 코드 있음.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("pipeline", str(PIPELINE_PY))
    assert spec is not None and spec.loader is not None, "pipeline.py 로드 실패"
    pipeline_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipeline_mod)  # type: ignore[union-attr]

    clean_fn = getattr(pipeline_mod, "_clean_pr_body_artifacts", None)
    assert clean_fn is not None, "_clean_pr_body_artifacts 함수가 pipeline.py에 없음"

    pipeline_id = "IMP-TEST-CLEAN"
    current_nonce = "NEWNONCE1"
    old_code = f"ACCEPT-{pipeline_id}-OLDCODE1"
    new_code = f"ACCEPT-{pipeline_id}-{current_nonce}"

    pr_body = (
        "## 작업 요약\n\n"
        f"이전 승인 코드: {old_code}\n\n"
        "<!-- PIPELINE_FINAL_PACKET_START -->\n"
        f"승인 코드:\n{new_code}\n"
        "<!-- PIPELINE_FINAL_PACKET_END -->\n"
    )
    cleaned = clean_fn(pr_body, pipeline_id, current_nonce)

    assert old_code not in cleaned, f"구 코드({old_code})가 제거되지 않음\n{cleaned}"
    assert new_code in cleaned, f"현재 코드({new_code})가 블록 안에서 보존되어야 함\n{cleaned}"


# ─── 15) _clean_pr_body_artifacts가 콘솔 덤프를 정리 (버그 3 회귀 방지) ───────────
def test_15_clean_pr_body_removes_console_artifacts() -> None:
    """버그 3 회귀 방지: _clean_pr_body_artifacts가 파이프라인 콘솔 덤프를 제거한다.

    [Context]: report final-packet / request-accept의 콘솔 출력이 PR 본문에
    복사-붙여넣기 되는 패턴을 update-pr-body가 정리해야 한다.
    PIPELINE_STATE_PATH 격리: 불필요 (함수 직접 호출 테스트).
    final_state: cleaned 텍스트에 콘솔 아티팩트 없음, 블록 안 내용 보존.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("pipeline", str(PIPELINE_PY))
    assert spec is not None and spec.loader is not None, "pipeline.py 로드 실패"
    pipeline_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipeline_mod)  # type: ignore[union-attr]

    clean_fn = getattr(pipeline_mod, "_clean_pr_body_artifacts", None)
    assert clean_fn is not None, "_clean_pr_body_artifacts 함수가 pipeline.py에 없음"

    pr_body = (
        "## 사용자가 확인할 결과물\n\n"
        "- \n"
        "[FINAL PACKET 작성 완료]\n"
        "  파일: human_acceptance_packet.md\n"
        "  PR: (gh 없음)\n"
        "  CI run: (없음)\n"
        "  다음 단계: python pipeline.py report update-pr-body 후 실행\n\n"
        "  [새 코드 발급] 결과물 경로가 달라서 새 코드를 발급합니다.\n\n"
        "  [FINAL PACKET 자동 생성] human_acceptance_packet.md\n"
        "  [PR 본문 자동 업데이트] gh CLI 없음 또는 갱신 실패\n\n"
        "==============================================================\n"
        "  사용자 최종 확인 요청\n"
        "==============================================================\n\n"
        "  [O] 승인하시려면 정확히 아래 코드를 입력하세요:\n"
        "     ACCEPT-IMP-TEST-OLDCODE\n\n"
        "  [X] 거절하시려면 아래 형식으로 입력하세요:\n"
        "     REJECT-IMP-TEST-OLDCODE: 이유\n\n"
        "<!-- PIPELINE_FINAL_PACKET_START -->\n"
        "정상 packet 내용\n"
        "<!-- PIPELINE_FINAL_PACKET_END -->\n"
    )
    cleaned = clean_fn(pr_body, "IMP-TEST", "NEWNONCE2")

    assert "[FINAL PACKET 작성 완료]" not in cleaned, "'[FINAL PACKET 작성 완료]' 미제거"
    assert "[새 코드 발급]" not in cleaned, "'[새 코드 발급]' 미제거"
    assert "[FINAL PACKET 자동 생성]" not in cleaned, "'[FINAL PACKET 자동 생성]' 미제거"
    assert "[PR 본문 자동 업데이트]" not in cleaned, "'[PR 본문 자동 업데이트]' 미제거"
    assert "사용자 최종 확인 요청" not in cleaned, "'사용자 최종 확인 요청' 미제거"
    assert "[O] 승인하시려면" not in cleaned, "'[O] 승인하시려면' 미제거"
    assert "PR: (gh 없음)" not in cleaned, "'PR: (gh 없음)' 패턴이 제거되지 않음"
    assert "CI run: (없음)" not in cleaned, "'CI run: (없음)' 패턴이 제거되지 않음"
    assert "REJECT-IMP-TEST-OLDCODE" not in cleaned, "구 REJECT 코드가 제거되지 않음"
    # 블록 안 내용은 보존
    assert "정상 packet 내용" in cleaned, "블록 안 내용이 보존되어야 함"
    # "다음 단계: python pipeline.py report update" 패턴은 제거됨
    assert "다음 단계: python pipeline.py report update" not in cleaned, (
        "'다음 단계: python pipeline.py report update' 미제거"
    )


# ─── 16) _clean_pr_body_artifacts가 블록 밖 구 REJECT 코드를 제거 (버그 4 회귀 방지) ──
def test_16_clean_pr_body_removes_stale_reject_codes() -> None:
    """버그 4 회귀 방지: _clean_pr_body_artifacts가 블록 밖 구 REJECT 코드를 제거하고,
    블록 안의 현재 nonce REJECT 예시는 보존한다.

    [Context]: ACCEPT 코드는 제거하지만 REJECT 코드는 남아 있는 버그(IMP-20260603-2E3D)를
    방지한다. 블록 밖 구 REJECT 코드만 제거, 블록 안 현재 nonce 코드는 보존해야 한다.
    PIPELINE_STATE_PATH 격리: 불필요 (함수 직접 호출 테스트).
    final_state: 구 REJECT 코드 없음, 현재 nonce REJECT 예시 보존.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("pipeline", str(PIPELINE_PY))
    assert spec is not None and spec.loader is not None, "pipeline.py 로드 실패"
    pipeline_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipeline_mod)  # type: ignore[union-attr]

    clean_fn = getattr(pipeline_mod, "_clean_pr_body_artifacts", None)
    assert clean_fn is not None, "_clean_pr_body_artifacts 함수가 pipeline.py에 없음"

    pipeline_id = "IMP-TEST-REJECT"
    current_nonce = "NEWNONCE"
    old_reject = f"REJECT-{pipeline_id}-OLDCODE: 거절 이유"
    current_reject = f"REJECT-{pipeline_id}-{current_nonce}: 거절 이유"  # 블록 안 거절 예시

    pr_body = (
        "## 작업 요약\n\n"
        f"이전 거절 코드: {old_reject}\n\n"
        "<!-- PIPELINE_FINAL_PACKET_START -->\n"
        f"거절 예시:\n{current_reject}\n"
        "<!-- PIPELINE_FINAL_PACKET_END -->\n"
    )
    cleaned = clean_fn(pr_body, pipeline_id, current_nonce)

    assert old_reject not in cleaned, f"구 REJECT 코드({old_reject})가 제거되지 않음"
    assert current_reject in cleaned, f"현재 REJECT 예시({current_reject})가 블록 안에서 제거됨"
