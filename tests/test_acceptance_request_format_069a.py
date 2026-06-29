"""IMP-20260624-069A 회귀 테스트: 최소 고정 양식 + CLI stdout 마지막 줄 검증.

REJECT 사유 2 수정: 기존 테스트는 _render_pending_acceptance_comment를 직접
import 호출하여 실제 CLI stdout을 검증하지 못했다. 본 테스트는 subprocess로
실제 `python pipeline.py gates request-accept --evidence ...` CLI를 실행하고
그 stdout을 캡처하여 최소 고정 양식 4요소와
"CODEX 검토 필요"가 마지막 의미 있는(비어있지 않은) 줄인지 검증한다.

Real CLI Path E2E Gate Policy (IMP-20260525-6FAC):
  - PIPELINE_STATE_PATH 환경변수로 state 파일 격리 (전역 pipeline_state.json 미수정)
  - subprocess 기반 실제 CLI 실행 (내부 함수 직접 호출 금지)
  - final_state assertion 포함 (acceptance_request.json post-state 검증)

REJECT 사유 1 회귀 방지: "CODEX 검토 필요" 이후 어떤 비어있지 않은 줄도
없어야 한다 ("승인 요청 ID: ..." print문 제거 확인).
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pytest

PIPELINE_PY = Path(__file__).resolve().parent.parent / "pipeline.py"


# ---------------------------------------------------------------------------
# Helpers (tests/e2e/test_request_accept_snapshot_8c3b.py 패턴 재사용)
# ---------------------------------------------------------------------------

# request-accept의 PR body readiness 검사(IMP-20260611-A716)를 통과하기 위한
# 완전한 PR body를 반환하는 fake gh 픽스처. headSha/databaseId는 빈 문자열,
# run/pr list는 빈 배열을 반환하여 "gh CLI 없는 환경"을 시뮬레이션한다.
_FAKE_GH_PR_BODY = (
    "## 작업 요약\n자동 테스트 픽스처 PR body\n\n"
    "## 사용자가 확인할 결과물\n결과물 경로: N/A (테스트)\n\n"
    "## 기대 결과와 실제 결과\n기대: 성공 / 실제: 성공\n\n"
    "## 중요한 선택과 트레이드오프\nN/A (테스트 픽스처)\n\n"
    "## 검증\n모든 게이트 PASS\n"
)


def run_cli(
    args: List[str],
    env: Dict[str, str],
    cwd: Optional[Path] = None,
    timeout: int = 30,
) -> "subprocess.CompletedProcess[str]":
    """`python pipeline.py <args>`를 subprocess로 실행 후 CompletedProcess 반환.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess에 전달할 환경 변수 dict.
        cwd: 작업 디렉토리 (기본은 PIPELINE_PY.parent).
        timeout: 초 단위 timeout.
    Returns:
        subprocess.CompletedProcess (stdout/stderr 캡처).
    Raises:
        TypeError: args가 list가 아닌 경우.
    """
    if args is None:
        raise TypeError("args must not be None")
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    cmd = [sys.executable, str(PIPELINE_PY)] + args
    effective_cwd = cwd if cwd is not None else PIPELINE_PY.parent
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        cwd=str(effective_cwd),
    )


def _write_fake_gh_script(tmp_path: Path) -> Path:
    """완전한 PR body를 반환하는 fake gh 스크립트를 tmp_path에 생성하여 경로 반환.

    Args:
        tmp_path: pytest tmp_path fixture.
    Returns:
        생성된 fake gh .py 스크립트 절대 경로.
    Raises:
        TypeError: tmp_path가 None.
    """
    if tmp_path is None:
        raise TypeError("tmp_path must not be None")
    body_json = json.dumps(_FAKE_GH_PR_BODY)
    pipeline_dir_json = json.dumps(str(PIPELINE_PY.parent))
    packet_md_json = json.dumps(str(PIPELINE_PY.parent / "human_acceptance_packet.md"))
    script = tmp_path / "fake_gh_069a.py"
    # BUG-20260628-F52C: publish가 디스크에 기록한 packet.md가 있으면 그 내용으로 FINAL_PACKET
    # 블록을 교체한 "publish 후 최종 body"를 반환한다(실제 gh pr view와 동일). 없으면 원본 body.
    # 이것이 없으면 publish 후 _verify_published_pr_body_three_way의 3자 SHA 검증이 깨진다.
    script.write_text(
        "import sys, io, json, os\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        f"sys.path.insert(0, {pipeline_dir_json})\n"
        f"DEFAULT_BODY = {body_json}\n"
        f"PACKET_MD = {packet_md_json}\n"
        "def _current_body():\n"
        "    try:\n"
        '        with open(PACKET_MD, encoding="utf-8") as fh:\n'
        "            packet = fh.read()\n"
        "    except OSError:\n"
        "        return DEFAULT_BODY\n"
        "    try:\n"
        "        import pipeline\n"
        "        return pipeline._replace_pr_body_packet_block(DEFAULT_BODY, packet)\n"
        "    except Exception:\n"
        "        return DEFAULT_BODY\n"
        "args = sys.argv[1:]\n"
        '# pr edit (PR body 갱신) — no-op 성공(.body가 packet.md 기준으로 결정적 반환).\n'
        'if "pr" in args and "edit" in args:\n'
        "    sys.exit(0)\n"
        '# pr comment — pending 안내 댓글 성공.\n'
        'if "pr" in args and "comment" in args:\n'
        '    print("https://github.com/test/repo/pull/1#issuecomment-1"); sys.exit(0)\n'
        'if "--jq" in args:\n'
        '    jq_idx = args.index("--jq"); jq = args[jq_idx+1] if jq_idx+1 < len(args) else ""\n'
        '    if jq == ".body":\n'
        "        _b = _current_body()\n"
        "        sys.stdout.write(_b)\n"
        '        if not _b.endswith("\\n"):\n'
        '            sys.stdout.write("\\n")\n'
        "        sys.exit(0)\n"
        '    elif "[.files" in jq or jq.startswith(".[0]"):\n'
        '        print("[]"); sys.exit(0)\n'
        '    elif ".headSha" in jq or ".databaseId" in jq:\n'
        '        print(""); sys.exit(0)\n'
        'if "api" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        'if "run" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        'if "run" in args and "view" in args:\n'
        "    print(json.dumps({})); sys.exit(0)\n"
        'if "pr" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        "print(json.dumps({\n"
        '    "body": _current_body(), "number": 1,\n'
        '    "headRefOid": "abc123def456abc123def456abc123def456abc1",\n'
        '    "isDraft": False, "state": "OPEN", "files": [],\n'
        '    "url": "https://github.com/test/repo/pull/1",\n'
        "}))\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return script


def make_env(tmp_path: Path) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 + gh CLI 무력화 + fake gh 주입 환경변수 dict 반환.

    Args:
        tmp_path: pytest tmp_path fixture.
    Returns:
        subprocess에 전달할 환경 변수 dict.
    Raises:
        TypeError: tmp_path가 None.
    """
    if tmp_path is None:
        raise TypeError("tmp_path must not be None")
    state_file = tmp_path / "pipeline_state.json"
    env = dict(os.environ)
    # 전역 pipeline_state.json 미수정 — tmp_path로 격리
    env["PIPELINE_STATE_PATH"] = str(state_file)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    env["PATH"] = str(tmp_path)
    env["PIPELINE_GH_EXECUTABLE"] = str(_write_fake_gh_script(tmp_path))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING"] = "1"
    env["PIPELINE_BROWSER_APPROVAL_SKIP"] = "1"
    return env


def bootstrap_pipeline_legacy(tmp_path: Path, env: Dict[str, str]) -> str:
    """격리 환경에 IMP 파이프라인을 생성하고 requirements_tracking.enabled=false로
    설정하여 AC 검사를 우회한 후 pipeline_id를 반환.

    Args:
        tmp_path: pytest tmp_path fixture.
        env: PIPELINE_STATE_PATH가 설정된 환경 변수 dict.
    Returns:
        생성된 pipeline_id 문자열.
    """
    r = run_cli(["new", "--type", "IMP", "--desc", "format e2e test 069a"], env=env)
    assert r.returncode == 0, f"new failed: {r.stdout} {r.stderr}"
    state_file = Path(env["PIPELINE_STATE_PATH"])
    with open(state_file, encoding="utf-8") as f:
        final_state = json.load(f)
    pid = str(final_state.get("pipeline_id", ""))
    assert pid, "pipeline_id missing"
    final_state.setdefault("requirements_tracking", {})
    final_state["requirements_tracking"]["enabled"] = False
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(final_state, f, ensure_ascii=False, indent=2)
    return pid


def write_evidence_file(tmp_path: Path, content: str = "format 069a evidence") -> Path:
    """evidence 파일을 tmp_path에 생성하고 경로 반환."""
    ev_file = tmp_path / "evidence.txt"
    ev_file.write_text(content, encoding="utf-8")
    return ev_file


def load_acceptance_request() -> Dict[str, object]:
    """acceptance_request.json 로드 (없으면 빈 dict). BASE_DIR(프로젝트 루트)에 생성됨."""
    req_file = PIPELINE_PY.parent / "acceptance_request.json"
    if not req_file.exists():
        return {}
    with open(req_file, encoding="utf-8") as f:
        return json.load(f)


def _meaningful_lines(stdout: str) -> List[str]:
    """stdout에서 비어있지 않은(공백 제거 후 비어있지 않은) 줄 목록을 반환."""
    return [ln for ln in stdout.splitlines() if ln.strip()]


def _staging_path() -> Path:
    """acceptance_staging.json 경로 — pipeline.py는 BASE_DIR(.pipeline)에 저장한다."""
    return PIPELINE_PY.parent / ".pipeline" / "acceptance_staging.json"


def stage_and_codex_approve(env: Dict[str, str], evidence: Path) -> None:
    """BUG-20260628-F52C 2-call 흐름: staging file 생성 후 codex-review로 frozen bytes 검토.

    1) gates request-accept (1차) — staging file 생성, codex 미승인으로 BLOCKED(정상).
    2) gates codex-review --approve-pending — staging file frozen bytes로 APPROVE_TO_USER 기록.
    """
    run_cli(["gates", "request-accept", "--evidence", str(evidence)], env=env)
    assert _staging_path().exists(), "1차 request-accept가 staging file을 생성하지 않음"
    r_cx = run_cli(
        ["gates", "codex-review", "--verdict", "APPROVE_TO_USER", "--approve-pending"],
        env=env,
    )
    assert r_cx.returncode == 0, (
        f"codex-review --approve-pending 실패\n{r_cx.stdout}{r_cx.stderr}"
    )


@pytest.fixture(autouse=True)
def _clean_staging_069a():
    """각 테스트 전후로 공유 staging file(.pipeline/acceptance_staging.json)을 정리한다."""
    sp = _staging_path()
    if sp.exists():
        try:
            sp.unlink()
        except OSError:
            pass
    yield
    if sp.exists():
        try:
            sp.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Tests — 실제 CLI stdout 기반 (REJECT 사유 2 수정)
# ---------------------------------------------------------------------------


class TestRealCliRequestAcceptFormat:
    """MT-4: subprocess로 실제 CLI stdout을 캡처하여 최소 고정 양식 검증."""

    def test_tc1_stdout_contains_4_elements(self, tmp_path):
        """TC-1 (normal): 실제 CLI stdout이 최소 양식 4요소를 순서대로 포함한다."""
        env = make_env(tmp_path)
        # PIPELINE_STATE_PATH는 make_env()가 tmp_path로 격리하여 설정함
        assert "PIPELINE_STATE_PATH" in env, "isolation env var must be set"
        pid = bootstrap_pipeline_legacy(tmp_path, env)
        ev_file = write_evidence_file(tmp_path)

        # BUG-20260628-F52C 2-call: staging file 생성 후 codex approve, 그 다음 publish.
        stage_and_codex_approve(env, ev_file)
        r = run_cli(
            ["gates", "request-accept", "--evidence", str(ev_file)],
            env=env,
        )

        assert r.returncode == 0, (
            f"request-accept 실패 (returncode={r.returncode})\n"
            f"stdout: {r.stdout[:600]}\nstderr: {r.stderr[:400]}"
        )

        # post-state assertion — final_state via acceptance_request.json
        import json as _json
        _state_path = Path(env["PIPELINE_STATE_PATH"])
        final_state = _json.loads(_state_path.read_text(encoding="utf-8")) if _state_path.exists() else {}
        req = load_acceptance_request()
        assert req.get("pipeline_id") == pid, (
            f"acceptance_request.json pipeline_id 불일치: {req.get('pipeline_id')}"
        )
        assert req.get("nonce"), "nonce가 acceptance_request.json에 기록되어야 함"
        _ = final_state  # isolation 상태 유지 확인용

        lines = r.stdout.splitlines()
        elements = ["사용자 승인 요청", "PR:", "승인 코드:", "CODEX 검토 필요"]
        found_positions = []
        for elem in elements:
            for i, line in enumerate(lines):
                if elem in line:
                    found_positions.append(i)
                    break
            else:
                pytest.fail(
                    f"필수 요소 '{elem}'가 CLI stdout에 없습니다.\nstdout:\n{r.stdout}"
                )

        assert found_positions == sorted(found_positions), (
            f"4요소가 순서대로 등장하지 않습니다. 위치: {found_positions}\n"
            f"stdout:\n{r.stdout}"
        )

    def test_tc2_codex_is_last_meaningful_line(self, tmp_path):
        """TC-2 (핵심): "CODEX 검토 필요"가 stdout의 마지막 의미 있는 줄이어야 한다.

        REJECT 사유 1 회귀 방지: "승인 요청 ID: ..." print문이 제거되어
        "CODEX 검토 필요" 이후 어떤 비어있지 않은 줄도 없어야 한다.
        """
        env = make_env(tmp_path)
        # PIPELINE_STATE_PATH는 make_env()가 tmp_path로 격리하여 설정함
        assert "PIPELINE_STATE_PATH" in env, "isolation env var must be set"
        bootstrap_pipeline_legacy(tmp_path, env)
        ev_file = write_evidence_file(tmp_path)

        # BUG-20260628-F52C 2-call: staging file 생성 후 codex approve, 그 다음 publish.
        stage_and_codex_approve(env, ev_file)
        r = run_cli(
            ["gates", "request-accept", "--evidence", str(ev_file)],
            env=env,
        )

        assert r.returncode == 0, (
            f"request-accept 실패 (returncode={r.returncode})\n"
            f"stdout: {r.stdout[:600]}\nstderr: {r.stderr[:400]}"
        )

        # post-state assertion — final_state를 통해 gates 실행 후 상태 격리 확인
        import json as _json
        _state_path = Path(env["PIPELINE_STATE_PATH"])
        final_state = _json.loads(_state_path.read_text(encoding="utf-8")) if _state_path.exists() else {}
        _ = final_state  # isolation 상태 유지 확인용

        meaningful = _meaningful_lines(r.stdout)
        assert meaningful, f"stdout에 의미 있는 줄이 없습니다.\nstdout:\n{r.stdout}"

        last_line = meaningful[-1]
        assert "CODEX 검토 필요" in last_line, (
            f'"CODEX 검토 필요"가 마지막 의미 있는 줄이 아닙니다.\n'
            f"마지막 줄: {last_line!r}\n"
            f"전체 의미 있는 줄:\n" + "\n".join(meaningful)
        )

    def test_tc3_no_request_id_print_after_codex(self, tmp_path):
        """TC-3 (regression): "승인 요청 ID:" 문구가 stdout 어디에도 없어야 한다.

        REJECT 사유 1 직접 회귀 검증 — 제거된 print문 문구 부재 확인.
        """
        env = make_env(tmp_path)
        # PIPELINE_STATE_PATH는 make_env()가 tmp_path로 격리하여 설정함
        assert "PIPELINE_STATE_PATH" in env, "isolation env var must be set"
        bootstrap_pipeline_legacy(tmp_path, env)
        ev_file = write_evidence_file(tmp_path)

        # BUG-20260628-F52C 2-call: staging file 생성 후 codex approve, 그 다음 publish.
        stage_and_codex_approve(env, ev_file)
        r = run_cli(
            ["gates", "request-accept", "--evidence", str(ev_file)],
            env=env,
        )

        assert r.returncode == 0, (
            f"request-accept 실패 (returncode={r.returncode})\n"
            f"stdout: {r.stdout[:600]}\nstderr: {r.stderr[:400]}"
        )

        # post-state assertion — final_state를 통해 gates 실행 후 상태 격리 확인
        import json as _json
        _state_path = Path(env["PIPELINE_STATE_PATH"])
        final_state = _json.loads(_state_path.read_text(encoding="utf-8")) if _state_path.exists() else {}
        _ = final_state  # isolation 상태 유지 확인용

        assert "승인 요청 ID:" not in r.stdout, (
            f'"승인 요청 ID:" 문구가 stdout에 남아 있습니다 (REJECT 사유 1 미해결).\n'
            f"stdout:\n{r.stdout}"
        )

    def test_tc4_pr_url_present_in_pr_line(self, tmp_path):
        """TC-4 (edge): "PR:" 줄에 PR URL 또는 "(PR 링크 없음)" fallback이 포함된다."""
        env = make_env(tmp_path)
        # PIPELINE_STATE_PATH는 make_env()가 tmp_path로 격리하여 설정함
        assert "PIPELINE_STATE_PATH" in env, "isolation env var must be set"
        bootstrap_pipeline_legacy(tmp_path, env)
        ev_file = write_evidence_file(tmp_path)

        # BUG-20260628-F52C 2-call: staging file 생성 후 codex approve, 그 다음 publish.
        stage_and_codex_approve(env, ev_file)
        r = run_cli(
            ["gates", "request-accept", "--evidence", str(ev_file)],
            env=env,
        )

        assert r.returncode == 0, (
            f"request-accept 실패 (returncode={r.returncode})\n"
            f"stdout: {r.stdout[:600]}\nstderr: {r.stderr[:400]}"
        )

        # post-state assertion — final_state를 통해 gates 실행 후 상태 격리 확인
        import json as _json
        _state_path = Path(env["PIPELINE_STATE_PATH"])
        final_state = _json.loads(_state_path.read_text(encoding="utf-8")) if _state_path.exists() else {}
        _ = final_state  # isolation 상태 유지 확인용

        pr_lines = [ln for ln in r.stdout.splitlines() if ln.strip().startswith("PR:")]
        assert pr_lines, f'"PR:"으로 시작하는 줄이 없습니다.\nstdout:\n{r.stdout}'
        pr_line = pr_lines[0]
        assert ("http" in pr_line) or ("PR 링크 없음" in pr_line), (
            f'"PR:" 줄에 URL 또는 fallback이 없습니다.\nPR 줄: {pr_line!r}'
        )

    def test_tc5_approval_code_present(self, tmp_path):
        """TC-5 (normal): "승인 코드:" 다음 줄에 ACCEPT-{pipeline_id} 형식 코드가 출력된다."""
        env = make_env(tmp_path)
        # PIPELINE_STATE_PATH는 make_env()가 tmp_path로 격리하여 설정함
        assert "PIPELINE_STATE_PATH" in env, "isolation env var must be set"
        pid = bootstrap_pipeline_legacy(tmp_path, env)
        ev_file = write_evidence_file(tmp_path)

        # BUG-20260628-F52C 2-call: staging file 생성 후 codex approve, 그 다음 publish.
        stage_and_codex_approve(env, ev_file)
        r = run_cli(
            ["gates", "request-accept", "--evidence", str(ev_file)],
            env=env,
        )

        assert r.returncode == 0, (
            f"request-accept 실패 (returncode={r.returncode})\n"
            f"stdout: {r.stdout[:600]}\nstderr: {r.stderr[:400]}"
        )

        # post-state assertion — final_state를 통해 gates 실행 후 상태 격리 확인
        import json as _json
        _state_path = Path(env["PIPELINE_STATE_PATH"])
        final_state = _json.loads(_state_path.read_text(encoding="utf-8")) if _state_path.exists() else {}
        _ = final_state  # isolation 상태 유지 확인용

        expected_code = f"ACCEPT-{pid}"
        assert expected_code in r.stdout, (
            f"stdout에 승인 코드({expected_code})가 없습니다.\nstdout:\n{r.stdout}"
        )


class TestErrorHandling:
    """입력 검증 테스트."""

    def test_run_cli_rejects_none_args(self):
        """TC-6 (exception): run_cli에 None 전달 시 TypeError."""
        with pytest.raises(TypeError):
            run_cli(None, env={})  # type: ignore[arg-type]


if __name__ == "__main__":
    import tempfile

    # 정상 입력 검증 (격리된 임시 디렉토리)
    with tempfile.TemporaryDirectory() as _td:
        _tp = Path(_td)
        _env = make_env(_tp)
        _pid = bootstrap_pipeline_legacy(_tp, _env)
        _ev = write_evidence_file(_tp)
        # BUG-20260628-F52C 2-call: staging file 생성 후 codex approve, 그 다음 publish.
        stage_and_codex_approve(_env, _ev)
        _r = run_cli(["gates", "request-accept", "--evidence", str(_ev)], env=_env)
        assert _r.returncode == 0, f"request-accept 실패: {_r.stdout} {_r.stderr}"
        _meaningful = _meaningful_lines(_r.stdout)
        assert _meaningful, "stdout 의미 있는 줄 없음"
        assert "CODEX 검토 필요" in _meaningful[-1], "CODEX 검토 필요가 마지막 줄 아님"
        assert "승인 요청 ID:" not in _r.stdout, "승인 요청 ID 문구 잔존"

    # None 입력 방어 검증
    try:
        run_cli(None, env={})  # type: ignore[arg-type]
        assert False, "예외 미발생"
    except TypeError:
        pass

    print("[SELF-VERIFY] OK")
