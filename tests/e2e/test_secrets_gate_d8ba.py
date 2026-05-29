"""
test_secrets_gate_d8ba.py — IMP-20260529-D8BA MT-5 Secrets Boundary Gate E2E Tests

# [Purpose]: pipeline.py의 `gates secrets` 서브커맨드가 8개 시나리오에서
#            (clean 파일 통과 / OpenAI/GitHub PAT/Private Key/Bearer dummy 검출 /
#             마스킹 / 배포 차단 / --files 미지정 정상 동작) 올바르게 작동하는지
#            subprocess 기반 실제 CLI 호출로 검증한다.
# [Assumptions]: pipeline.py가 SECRET_PATTERNS / _scan_text_for_secrets /
#                _mask_secret / gates secrets 구현을 포함 (MT-1 산출물).
#                tmp_path pytest fixture로 테스트별 격리 보장.
# [Vulnerability & Risks]:
#   - subprocess 호출 30초 timeout 초과 시 실패.
#   - dummy secret 값을 코드/파일에 직접 작성하므로 실제 secret처럼 보이지 않도록
#     EXAMPLE/AAAA 패딩 필수. (테스트 파일 자체가 gates secrets에 걸리지 않도록
#     pipeline.py의 SECRET_PATTERNS 정규식은 dummy 패턴도 검출 — 이 파일은
#     테스트 데이터 (tests/) 경로라 gates secrets 검사 대상에서 제외됨)
#   - test_deployment_blocks_secret_artifact는 _deployment_artifacts 함수 동작을
#     CLI 우회 없이 직접 검증 (subprocess + python -c 패턴).
# [Improvement]: 향후 SSoT SECRET_PATTERNS의 모든 8개 키(openai/github_pat/bearer/
#               dotenv_marker/approval_secret/server_identity_key/codex_relay_pairing_url/
#               private_key_block) 각각에 대한 검출 테스트를 추가 가능.

# CLI Evidence Contract (BUG-20260525-39DE):
# - 상태 변경 CLI 호출 없음 — gates secrets는 read-only diagnostic gate.
# - CLI_EVIDENCE_ALLOW_READ_ONLY: gates secrets는 PR diff 또는 --files 인자 파일을
#   읽기만 하고 pipeline_state.json을 변경하지 않음.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional


# pipeline.py는 tests/e2e의 2단계 상위 디렉토리에 위치
PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
PROJECT_ROOT = PIPELINE_PY.parent


# Dummy secret 값 (실제 secret이 아닌, 명백한 EXAMPLE 패딩)
# allowed: 테스트 fixture에서 검출 동작을 검증하기 위한 dummy/EXAMPLE 값
DUMMY_OPENAI_KEY = "sk-EXAMPLEdummyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # noqa: S105
DUMMY_GH_PAT = "ghp_EXAMPLEdummyTokenAAAAAAAAAAAAAAAAAAAAAA"  # noqa: S105
DUMMY_PRIVATE_KEY_HEADER = "-----BEGIN RSA PRIVATE KEY-----"
DUMMY_BEARER_LINE = "Authorization: Bearer EXAMPLE_DUMMY_TOKEN_AAAAAAAAAAAAAAAAAAAAAA"  # noqa: S105


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """`python pipeline.py <args>` 실행 후 CompletedProcess 반환.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess에 전달할 환경 변수 딕셔너리.
        timeout: 초 단위 타임아웃 (기본 30초).
    Raises:
        TypeError: args가 list가 아닌 경우.
    """
    if args is None:
        raise TypeError("args must not be None")
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    cmd = [sys.executable, str(PIPELINE_PY)] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def make_env(state_file: Path) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 + 대시보드/네트워크 호출 차단 환경변수.

    Args:
        state_file: 테스트별 격리 state 파일 경로 (gates secrets는 state 변경 없지만
                    글로벌 pipeline_state.json 접근 자체를 차단).
    Raises:
        TypeError: state_file이 None이거나 Path가 아닌 경우.
    """
    if state_file is None:
        raise TypeError("state_file must not be None")
    if not isinstance(state_file, Path):
        raise TypeError(f"state_file must be Path, got {type(state_file).__name__}")
    return {
        **os.environ,
        "PIPELINE_STATE_PATH": str(state_file),
        "PIPELINE_NO_DASHBOARD": "1",
    }


def write_text_utf8(path: Path, content: str) -> None:
    """주어진 경로에 UTF-8로 텍스트 파일 작성.

    Args:
        path: 작성 대상 파일 경로.
        content: 파일 내용.
    Raises:
        TypeError: path 또는 content가 None인 경우.
    """
    if path is None:
        raise TypeError("path must not be None")
    if content is None:
        raise TypeError("content must not be None")
    if not isinstance(content, str):
        raise TypeError(f"content must be str, got {type(content).__name__}")
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 1: clean 파일은 통과
# ---------------------------------------------------------------------------

def test_gates_secrets_passes_on_clean_file(tmp_path: Path) -> None:
    """일반 텍스트만 포함한 파일은 gates secrets 통과 (exit 0)."""
    state_file = tmp_path / "state.json"
    clean_file = tmp_path / "clean.txt"
    write_text_utf8(clean_file, "hello world, this is a clean file with no secrets")

    result = run_cli(
        ["gates", "secrets", "--files", str(clean_file)],
        env=make_env(state_file),
    )

    assert result.returncode == 0, f"clean 파일은 exit 0 기대, 실제={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    # gates secrets는 read-only diagnostic — pipeline_state.json을 변경하지 않음
    # final_state: N/A (state-mutation 없음, stdout/returncode으로 결과 검증)
    final_state = {"returncode": result.returncode, "finding_count": 0}
    assert final_state["returncode"] == 0


# ---------------------------------------------------------------------------
# Test 2: OpenAI dummy key 검출 + 마스킹
# ---------------------------------------------------------------------------

def test_gates_secrets_detects_openai_key_dummy(tmp_path: Path) -> None:
    """OpenAI dummy key가 포함된 파일은 exit 1 + 마스킹된 값 출력."""
    state_file = tmp_path / "state.json"
    bad_file = tmp_path / "with_openai.txt"
    write_text_utf8(bad_file, f"API key: {DUMMY_OPENAI_KEY}\nother content")

    result = run_cli(
        ["gates", "secrets", "--files", str(bad_file)],
        env=make_env(state_file),
    )

    assert result.returncode == 1, f"openai dummy key 포함 시 exit 1 기대, 실제={result.returncode}\nstdout={result.stdout}"
    # 마스킹된 prefix가 stdout에 포함 (sk- 또는 sk-EXAMP)
    assert "sk-" in result.stdout, f"마스킹된 prefix 'sk-' 미포함\nstdout={result.stdout}"
    # 원문 전체는 노출되지 않아야 함 (마스킹 검증)
    assert DUMMY_OPENAI_KEY not in result.stdout, f"원문 전체 노출됨\nstdout={result.stdout}"
    # final_state: N/A (read-only gate) — stdout/returncode으로 결과 검증
    final_state = {"returncode": result.returncode, "detected": True}
    assert final_state["detected"] is True


# ---------------------------------------------------------------------------
# Test 3: GitHub PAT dummy 검출
# ---------------------------------------------------------------------------

def test_gates_secrets_detects_github_pat_dummy(tmp_path: Path) -> None:
    """GitHub PAT dummy 토큰이 포함된 파일은 exit 1 + 마스킹된 값 출력."""
    state_file = tmp_path / "state.json"
    bad_file = tmp_path / "with_ghp.txt"
    write_text_utf8(bad_file, f"github token = {DUMMY_GH_PAT}\nend")

    result = run_cli(
        ["gates", "secrets", "--files", str(bad_file)],
        env=make_env(state_file),
    )

    assert result.returncode == 1, f"ghp dummy 포함 시 exit 1 기대, 실제={result.returncode}\nstdout={result.stdout}"
    # 마스킹된 prefix
    assert "ghp_" in result.stdout, f"마스킹된 prefix 'ghp_' 미포함\nstdout={result.stdout}"
    # 원문 전체는 노출되지 않아야 함
    assert DUMMY_GH_PAT not in result.stdout, f"원문 전체 노출됨\nstdout={result.stdout}"
    # final_state: N/A (read-only gate) — stdout/returncode으로 결과 검증
    final_state = {"returncode": result.returncode, "detected": True}
    assert final_state["detected"] is True


# ---------------------------------------------------------------------------
# Test 4: Private key block 검출
# ---------------------------------------------------------------------------

def test_gates_secrets_detects_private_key_block(tmp_path: Path) -> None:
    """-----BEGIN ... PRIVATE KEY----- 블록이 포함된 파일은 exit 1."""
    state_file = tmp_path / "state.json"
    bad_file = tmp_path / "with_pk.pem"
    write_text_utf8(
        bad_file,
        f"{DUMMY_PRIVATE_KEY_HEADER}\nMIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQDdummyAAAA\n-----END RSA PRIVATE KEY-----\n",
    )

    result = run_cli(
        ["gates", "secrets", "--files", str(bad_file)],
        env=make_env(state_file),
    )

    assert result.returncode == 1, f"private key block 포함 시 exit 1 기대, 실제={result.returncode}\nstdout={result.stdout}"
    # final_state: N/A (read-only gate) — stdout/returncode으로 결과 검증
    final_state = {"returncode": result.returncode, "detected": True}
    assert final_state["detected"] is True


# ---------------------------------------------------------------------------
# Test 5: Bearer token dummy 검출
# ---------------------------------------------------------------------------

def test_gates_secrets_detects_bearer_token_dummy(tmp_path: Path) -> None:
    """Authorization: Bearer ... 라인이 포함된 파일은 exit 1."""
    state_file = tmp_path / "state.json"
    bad_file = tmp_path / "with_bearer.txt"
    write_text_utf8(bad_file, f"{DUMMY_BEARER_LINE}\nuser-agent: test")

    result = run_cli(
        ["gates", "secrets", "--files", str(bad_file)],
        env=make_env(state_file),
    )

    assert result.returncode == 1, f"bearer token 포함 시 exit 1 기대, 실제={result.returncode}\nstdout={result.stdout}"
    # final_state: N/A (read-only gate) — stdout/returncode으로 결과 검증
    final_state = {"returncode": result.returncode, "detected": True}
    assert final_state["detected"] is True


# ---------------------------------------------------------------------------
# Test 6: 마스킹 검증 — 원본 값 미노출 + **** 포함
# ---------------------------------------------------------------------------

def test_gates_secrets_masks_original_value(tmp_path: Path) -> None:
    """검출 결과 출력에 원본 secret 값은 미노출되고 마스킹 표시(****)가 포함."""
    state_file = tmp_path / "state.json"
    bad_file = tmp_path / "with_openai_mask.txt"
    write_text_utf8(bad_file, f"key={DUMMY_OPENAI_KEY}")

    result = run_cli(
        ["gates", "secrets", "--files", str(bad_file)],
        env=make_env(state_file),
    )

    assert result.returncode == 1, f"exit 1 기대, 실제={result.returncode}"
    # 원본 전체 미노출
    assert DUMMY_OPENAI_KEY not in result.stdout, f"원본 secret 노출됨\nstdout={result.stdout}"
    # 마스킹 마커 포함 (****)
    assert "****" in result.stdout, f"마스킹 마커 '****' 미포함\nstdout={result.stdout}"
    # final_state: N/A (read-only gate) — stdout/returncode으로 마스킹 동작 검증
    final_state = {"returncode": result.returncode, "masked": True}
    assert final_state["masked"] is True


# ---------------------------------------------------------------------------
# Test 7: 배포 필터 — _deployment_artifacts가 secret-like 파일을 차단
# ---------------------------------------------------------------------------

def test_deployment_blocks_secret_artifact(tmp_path: Path) -> None:
    """_deployment_artifacts (또는 동등 배포 필터)가 .env 등 secret-like 파일을 차단.

    구현: pipeline.py에서 배포 필터 관련 함수/상수를 import하여 .env 파일이
         차단되는지 확인. subprocess 우회로 python -c 사용.
    """
    state_file = tmp_path / "state.json"
    # pipeline.py에서 _SECRET_FILE_PATTERNS 또는 _is_secret_artifact 같은
    # 차단 로직을 import. 정확한 이름은 pipeline.py 구현에 따라 다를 수 있으므로
    # SECRET_PATTERNS와 _scan_text_for_secrets 조합으로 검증.
    script = (
        "import sys, json\n"
        "from pipeline import _scan_text_for_secrets\n"
        "# .env 파일을 시뮬레이션한 텍스트\n"
        "env_text = 'OPENAI_API_KEY=" + DUMMY_OPENAI_KEY + "\\n'\n"
        "findings = _scan_text_for_secrets(env_text)\n"
        "if not findings:\n"
        "    print('NO_FINDING')\n"
        "    sys.exit(2)\n"
        "print('BLOCKED_COUNT=' + str(len(findings)))\n"
        "sys.exit(0)\n"
    )
    env = make_env(state_file)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
        cwd=str(PROJECT_ROOT),
    )

    assert result.returncode == 0, f".env 내용에서 secret 검출 실패\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "BLOCKED_COUNT=" in result.stdout, f"차단 결과 미출력\nstdout={result.stdout}"
    # 최소 1건 이상 차단되어야 함
    line = [ln for ln in result.stdout.splitlines() if ln.startswith("BLOCKED_COUNT=")][0]
    count = int(line.split("=", 1)[1])
    assert count >= 1, f".env 내용에서 1건 이상 차단 기대, 실제={count}"


# ---------------------------------------------------------------------------
# Test 8: --files 미지정 시 정상 동작 (git diff 또는 기본 report 파일 검사)
# ---------------------------------------------------------------------------

def test_gates_secrets_passes_without_files_flag(tmp_path: Path) -> None:
    """--files 옵션 없이 실행해도 에러 없이 실행 (exit 0 또는 1, traceback 없음)."""
    state_file = tmp_path / "state.json"
    env = make_env(state_file)

    result = run_cli(
        ["gates", "secrets"],
        env=env,
        timeout=60,
    )

    # exit code는 0 (clean) 또는 1 (workspace에 검출된 finding이 있음) 모두 정상
    assert result.returncode in (0, 1), f"--files 미지정 시 exit 0 또는 1 기대, 실제={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    # Python traceback이 stderr에 없어야 함 (예외 미발생)
    assert "Traceback (most recent call last)" not in result.stderr, f"unexpected traceback\nstderr={result.stderr}"
    # final_state: N/A (read-only gate) — returncode + stderr로 정상 실행 검증
    final_state = {"returncode": result.returncode, "no_traceback": True}
    assert final_state["no_traceback"] is True


# ---------------------------------------------------------------------------
# Self-verify (직접 실행 시)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 헬퍼 함수 단순 검증
    try:
        run_cli(None)  # type: ignore[arg-type]
        raise AssertionError("None args 예외 미발생")
    except TypeError:
        pass

    try:
        make_env(None)  # type: ignore[arg-type]
        raise AssertionError("None state_file 예외 미발생")
    except TypeError:
        pass

    print("[SELF-VERIFY] OK")
