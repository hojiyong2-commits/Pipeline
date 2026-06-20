"""
test_pr_diff_artifact_8ac8.py — BUG-20260619-8AC8 PR diff 오염 검사 E2E 테스트

# [Purpose]: gates request-accept 전 PR diff 오염 검사(_check_pr_diff_artifact_pollution)가
#            실행 산출물(step_plan_F41F.xml, pipeline_state.json 등)을 BLOCKED 처리하고,
#            git CLI 부재 시 fail-closed로 git_unavailable BLOCKED를 반환하는지
#            4개 oracle 케이스(normal/edge_step_plan/edge_pipeline_state/error_git)로 검증.
# [Assumptions]: tests/oracles/BUG-20260619-8AC8/<case>/given.json + expected.json이
#            사전 작성되어 있고, pipeline.PR_DIFF_ARTIFACT_PATTERNS / _check_pr_diff_artifact_pollution이
#            존재함. git 조작 없이 subprocess.run / shutil.which를 mock하여 diff 파일 목록을 주입.
# [Vulnerability & Risks]: _check_pr_diff_artifact_pollution이 내부적으로 git diff를
#            subprocess로 호출하므로, 실제 git 실행을 막기 위해 mock이 필수. mock 누락 시
#            로컬 git 상태에 따라 결과가 흔들릴 수 있어 모든 케이스에서 mock을 강제한다.
#            상태 변경 CLI가 아니므로 PIPELINE_STATE_PATH 격리는 불필요(순수 함수 검사).
# [Improvement]: 향후 실제 임시 git repo를 만들어 subprocess mock 없이 end-to-end로
#            git diff 경로까지 커버할 수 있음.

# CLI Evidence Contract (BUG-20260525-39DE):
# - 본 테스트는 상태를 변경하지 않는 순수 검사 함수(_check_pr_diff_artifact_pollution)를 검증한다.
# CLI_EVIDENCE_ALLOW_READ_ONLY: _check_pr_diff_artifact_pollution은 pipeline_state.json을
#   읽거나 쓰지 않는 read-only 검사 함수이므로 PIPELINE_STATE_PATH 격리/final_state assertion 불필요.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import pytest

# pipeline.py는 tests/e2e의 2단계 상위 디렉토리에 위치
PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
ORACLE_DIR = Path(__file__).resolve().parent.parent / "oracles" / "BUG-20260619-8AC8"

CASE_IDS: List[str] = [
    "normal_clean_diff",
    "edge_step_plan_in_diff",
    "edge_pipeline_state_in_diff",
    "error_git_unavailable",
]


def _load_pipeline_module():
    """pipeline.py를 'pipeline' 모듈로 import한다.

    Returns:
        import된 pipeline 모듈 객체.
    Raises:
        ImportError: spec 생성 실패 시.
    """
    spec = importlib.util.spec_from_file_location("pipeline", str(PIPELINE_PY))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load pipeline module from {PIPELINE_PY}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_json(path: Path) -> Dict[str, Any]:
    """utf-8 -> cp949 -> latin-1 순서로 JSON 파일을 읽는다.

    Args:
        path: 읽을 JSON 파일 경로.
    Returns:
        파싱된 dict.
    Raises:
        TypeError: path가 None인 경우.
        FileNotFoundError: 파일 부재 시.
    """
    if path is None:
        raise TypeError("path must not be None")
    if not path.exists():
        raise FileNotFoundError(f"oracle file not found: {path}")
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            return json.loads(path.read_text(encoding=enc))
        except (UnicodeDecodeError, LookupError):
            continue
    raise UnicodeDecodeError("utf-8", b"", 0, 1, f"cannot decode {path}")


def _run_check(pipeline_mod, given: Dict[str, Any]) -> Dict[str, Any]:
    """given.json 입력에 따라 _check_pr_diff_artifact_pollution을 mock 환경에서 실행한다.

    Args:
        pipeline_mod: import된 pipeline 모듈.
        given: oracle given.json 내용 (diff_files, git_available 포함).
    Returns:
        _check_pr_diff_artifact_pollution 반환 dict.
    Raises:
        TypeError: given이 dict가 아닌 경우.
    """
    if given is None:
        raise TypeError("given must not be None")
    if not isinstance(given, dict):
        raise TypeError(f"given must be dict, got {type(given).__name__}")
    git_available = given.get("git_available", True)
    if not git_available:
        # git CLI 부재 시뮬레이션: shutil.which가 None 반환 -> fail-closed BLOCKED
        with mock.patch.object(pipeline_mod.shutil, "which", return_value=None):
            return pipeline_mod._check_pr_diff_artifact_pollution()
    diff_files = given.get("diff_files") or []
    fake_completed = mock.Mock(returncode=0, stdout="\n".join(diff_files))
    with mock.patch.object(pipeline_mod.shutil, "which", return_value="git"), \
            mock.patch.object(pipeline_mod.subprocess, "run", return_value=fake_completed):
        return pipeline_mod._check_pr_diff_artifact_pollution()


@pytest.fixture(scope="module")
def pipeline_mod():
    """pipeline 모듈을 모듈 스코프로 1회 import한다."""
    return _load_pipeline_module()


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_pr_diff_artifact_pollution(pipeline_mod, case_id: str):
    """각 oracle 케이스의 given -> _check_pr_diff_artifact_pollution -> expected 비교."""
    given = _read_json(ORACLE_DIR / case_id / "given.json")
    expected = _read_json(ORACLE_DIR / case_id / "expected.json")

    result = _run_check(pipeline_mod, given)

    # status는 모든 케이스에서 정확히 일치해야 한다.
    assert result.get("status") == expected.get("status"), (
        f"[{case_id}] status mismatch: got {result.get('status')}, "
        f"expected {expected.get('status')}"
    )

    # failure_code가 expected에 있으면 일치해야 한다 (OK 케이스는 failure_code 없음).
    if "failure_code" in expected:
        assert result.get("failure_code") == expected.get("failure_code"), (
            f"[{case_id}] failure_code mismatch: got {result.get('failure_code')}, "
            f"expected {expected.get('failure_code')}"
        )

    # blocking_files가 expected에 명시된 경우 일치해야 한다.
    if "blocking_files" in expected:
        assert result.get("blocking_files") == expected.get("blocking_files"), (
            f"[{case_id}] blocking_files mismatch: got {result.get('blocking_files')}, "
            f"expected {expected.get('blocking_files')}"
        )


def test_patterns_constant_present(pipeline_mod):
    """PR_DIFF_ARTIFACT_PATTERNS SSoT 상수가 존재하고 핵심 패턴을 포함하는지 검증."""
    patterns = pipeline_mod.PR_DIFF_ARTIFACT_PATTERNS
    assert isinstance(patterns, list)
    assert len(patterns) >= 1
    for required in ("step_plan*.xml", "pipeline_state*.json", "manager_handoff*.xml"):
        assert required in patterns, f"missing required pattern: {required}"


if __name__ == "__main__":
    mod = _load_pipeline_module()
    for cid in CASE_IDS:
        g = _read_json(ORACLE_DIR / cid / "given.json")
        e = _read_json(ORACLE_DIR / cid / "expected.json")
        r = _run_check(mod, g)
        assert r.get("status") == e.get("status"), f"{cid}: status mismatch"
        if "failure_code" in e:
            assert r.get("failure_code") == e.get("failure_code"), f"{cid}: failure_code mismatch"
    print("[SELF-VERIFY] OK")
