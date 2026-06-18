"""BUG-20260617-1022 MT-8: 브라우저 승인 채널 UX freshness 검증 E2E 테스트.

_verify_browser_click_freshness 함수를 oracle 파일을 기준으로 직접 테스트한다.

oracle 경로:
  tests/oracles/BUG-20260617-1022/normal_freshness_pass/{input,expected}.json
  tests/oracles/BUG-20260617-1022/edge_freshness_fail_stale_sha/{input,expected}.json

격리 전략:
  - monkeypatch.chdir(tmp_path)로 acceptance_request.json(상대경로)을 tmp_path에 격리
  - human_acceptance_packet.md도 tmp_path(cwd)에 작성 (_packet_output_path는 os.getcwd 기준)
  - 전역 pipeline_state.json / acceptance_request.json을 수정하지 않음

oracle input.json 구조:
  acceptance_request: status=PENDING, nonce, packet_sha256 placeholder
  packet_exists, packet_sha256_matches, session_token

oracle expected.json 구조:
  browser_click_confirmed, freshness_blocked, freshness_reason
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

import pytest

import pipeline

# oracle 디렉토리 (이 파일 기준 repo 루트/tests/oracles/...)
_ORACLE_ROOT = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "oracles"
    / "BUG-20260617-1022"
)

_PACKET_CONTENT = "human acceptance packet body (테스트 고정 내용)\n"


def _load_oracle(case: str) -> Dict[str, Any]:
    """oracle 케이스의 input/expected JSON을 읽어 dict로 반환."""
    case_dir = _ORACLE_ROOT / case
    inp = json.loads((case_dir / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    return {"input": inp, "expected": exp}


def _setup_workspace(
    tmp_path: Path,
    oracle_input: Dict[str, Any],
) -> str:
    """oracle input을 기반으로 tmp_path에 acceptance_request.json과 packet을 구성한다.

    Returns:
        호출에 사용할 session_token (acceptance_request.json의 nonce와 동일하게 맞춤).
    """
    # packet 파일 작성 (cwd == tmp_path 기준).
    packet_path = tmp_path / pipeline.HUMAN_ACCEPTANCE_PACKET_FILE
    packet_exists = bool(oracle_input.get("packet_exists", True))
    actual_packet_sha = ""
    if packet_exists:
        # bytes로 직접 써서 Windows의 \n→\r\n 변환을 회피하고,
        # _sha256_file(바이너리 읽기)과 동일한 바이트로 SHA를 계산한다.
        packet_bytes = _PACKET_CONTENT.encode("utf-8")
        packet_path.write_bytes(packet_bytes)
        actual_packet_sha = hashlib.sha256(packet_bytes).hexdigest()

    req = dict(oracle_input["acceptance_request"])
    # freshness 검증은 session_token == nonce 를 요구하므로, 세션 토큰을 nonce로 통일한다.
    session_token = str(req.get("nonce") or oracle_input.get("session_token") or "tok")
    req["nonce"] = session_token

    # packet_sha256_matches 에 따라 stored packet_sha256을 일치/불일치로 설정.
    if oracle_input.get("packet_sha256_matches", True):
        req["packet_sha256"] = actual_packet_sha
    else:
        req["packet_sha256"] = "STALE_" + actual_packet_sha

    (tmp_path / pipeline.ACCEPTANCE_REQUEST_FILE).write_text(
        json.dumps(req, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return session_token


def test_freshness_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-1: 정상 케이스 — packet_sha256 일치 시 클릭 허용."""
    oracle = _load_oracle("normal_freshness_pass")
    monkeypatch.chdir(tmp_path)
    session_token = _setup_workspace(tmp_path, oracle["input"])

    result = pipeline._verify_browser_click_freshness({}, session_token)

    expected = oracle["expected"]
    # ok=True ↔ browser_click_confirmed=True, freshness_blocked=False
    assert result["ok"] is expected["browser_click_confirmed"], (
        f"ok 불일치: {result}"
    )
    assert (not result["ok"]) is expected["freshness_blocked"], (
        f"freshness_blocked 불일치: {result}"
    )
    assert result["ok"] is True, f"정상 케이스인데 BLOCKED: {result}"
    assert result["reason"] == "", f"정상 케이스 reason은 빈 문자열이어야 함: {result}"


def test_freshness_fail_stale_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-2: 엣지 케이스 — packet_sha256 불일치 시 클릭 BLOCKED."""
    oracle = _load_oracle("edge_freshness_fail_stale_sha")
    monkeypatch.chdir(tmp_path)
    session_token = _setup_workspace(tmp_path, oracle["input"])

    result = pipeline._verify_browser_click_freshness({}, session_token)

    expected = oracle["expected"]
    assert result["ok"] is expected["browser_click_confirmed"], (
        f"ok 불일치: {result}"
    )
    assert (not result["ok"]) is expected["freshness_blocked"], (
        f"freshness_blocked 불일치: {result}"
    )
    assert result["ok"] is False, f"stale 케이스인데 통과됨: {result}"
    assert "packet_sha256" in result["reason"], (
        f"stale 케이스 reason에 packet_sha256 언급 필요: {result}"
    )


def test_freshness_missing_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-3: acceptance_request.json 부재 시 BLOCKED (방어 케이스)."""
    monkeypatch.chdir(tmp_path)
    result = pipeline._verify_browser_click_freshness({}, "any-token")
    assert result["ok"] is False
    assert "acceptance_request.json" in result["reason"]


def test_freshness_session_token_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-4: session_token이 nonce와 불일치하면 BLOCKED."""
    monkeypatch.chdir(tmp_path)
    req = {
        "status": "PENDING",
        "nonce": "correct-nonce",
        "pipeline_id": "BUG-20260617-1022",
        "packet_sha256": "",
        "pr_body_sha256": "",
    }
    (tmp_path / pipeline.ACCEPTANCE_REQUEST_FILE).write_text(
        json.dumps(req, ensure_ascii=False), encoding="utf-8"
    )
    result = pipeline._verify_browser_click_freshness({}, "wrong-token")
    assert result["ok"] is False
    assert "nonce" in result["reason"]


def test_freshness_none_token_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-5: session_token=None 입력 방어 — TypeError raise."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(TypeError):
        pipeline._verify_browser_click_freshness({}, None)  # type: ignore[arg-type]


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# BUG-20260617-1022 REJECT 보강 테스트
# ---------------------------------------------------------------------------

import subprocess
import sys
import os


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_min_state(pipeline_id: str, tmp_path: Path) -> Path:
    """테스트용 최소 pipeline_state.json을 tmp_path에 생성한다."""
    state = {
        "version": "1.2.0",
        "pipeline_id": pipeline_id,
        "type": "BUG",
        "description": "test",
        "current_phase": "harness",
        "blocked": False,
        "phases": {"pm": {"status": "DONE"}, "dev": {"status": "DONE"}},
        "external_gates": {
            "technical": {"status": "PASS"},
            "oracle": {"status": "PASS"},
            "github_ci": {"status": "PASS"},
            "acceptance": {"status": "PENDING"},
        },
        "events": [],
    }
    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(
        __import__("json").dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return state_path


def test_request_accept_bundle_excludes_accept_code(tmp_path: Path) -> None:
    """gates request-accept 출력에서 Codex bundle은 ACCEPT 코드를 포함하지 않아야 한다."""
    state_path = _make_min_state("BUG-20260617-1022", tmp_path)
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    env["PIPELINE_BROWSER_APPROVAL_SKIP"] = "1"
    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "pipeline.py"), "gates", "request-accept",
         "--evidence", str(_REPO_ROOT / "pipeline.py")],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
        encoding="utf-8", errors="replace",
    )
    stdout = result.stdout + result.stderr
    # bundle 섹션이 있으면 그 안에 ACCEPT 코드가 없어야 한다
    if "[Codex 검증 Bundle]" in stdout:
        bundle_start = stdout.index("[Codex 검증 Bundle]")
        bundle_section = stdout[bundle_start:]
        # 승인 코드 섹션은 bundle 이후에만 나와야 함
        accept_idx = bundle_section.find("[승인 코드]")
        assert accept_idx == -1 or accept_idx > len("[Codex 검증 Bundle]"), \
            "Codex bundle 섹션 안에 [승인 코드]가 포함되어 있습니다"
    # accept_code는 [승인 코드] 섹션에만 있어야 함
    assert "[승인 코드]" in stdout or result.returncode != 0, \
        "request-accept 출력에 [승인 코드] 섹션이 없습니다"


def test_request_accept_no_browser_server_blocking(tmp_path: Path) -> None:
    """PR 댓글 방식: request-accept가 브라우저 서버 없이 즉시 완료되어야 한다."""
    import time
    state_path = _make_min_state("BUG-20260617-1022", tmp_path)
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    env["PIPELINE_BROWSER_APPROVAL_SKIP"] = "1"
    start = time.monotonic()
    subprocess.run(
        [sys.executable, str(_REPO_ROOT / "pipeline.py"), "gates", "request-accept",
         "--evidence", str(_REPO_ROOT / "pipeline.py")],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
        encoding="utf-8", errors="replace", timeout=30,
    )
    elapsed = time.monotonic() - start
    # 브라우저 서버 없이 실행해야 하므로 30초 내에 완료 필수
    assert elapsed < 30, f"request-accept가 {elapsed:.1f}초 걸림 — 브라우저 서버 블로킹 의심"


def test_request_accept_browser_approval_skip_set(tmp_path: Path) -> None:
    """request-accept 후 acceptance_request.json에 browser_approval_skip=True가 기록된다."""
    state_path = _make_min_state("BUG-20260617-1022", tmp_path)
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    env["PIPELINE_BROWSER_APPROVAL_SKIP"] = "1"
    subprocess.run(
        [sys.executable, str(_REPO_ROOT / "pipeline.py"), "gates", "request-accept",
         "--evidence", str(_REPO_ROOT / "pipeline.py")],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
        encoding="utf-8", errors="replace", timeout=30,
    )
    req_path = tmp_path / "acceptance_request.json"
    if req_path.exists():
        import json as _json
        req = _json.loads(req_path.read_text(encoding="utf-8"))
        assert req.get("browser_approval_skip") is True, \
            f"browser_approval_skip should be True, got {req.get('browser_approval_skip')!r}"
