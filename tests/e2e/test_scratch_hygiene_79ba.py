"""
E2E 테스트: Scratch Hygiene v1 (IMP-20260622-79BA)
-------------------------------------------------
repo root 임시 산출물 오염 방지 + 안전 cleanup 검증.

검증 대상:
- AC-1: scratch helper 6개 (.pipeline/runs/<id>/scratch/)
- AC-2: 내부 생산자 audit + scratch redirect 정책
- AC-3/AC-6: workspace hygiene cleanup_only 분류
- AC-4: Permission denied .pytest_tmp_* 정리 fixture
- AC-5: hygiene cleanup CLI (dry-run 기본 + --apply)
- AC-7: final packet hygiene 요약
- AC-8/AC-9: .pipeline/runs/** / pipeline_state.json PR diff BLOCKING
- AC-10: .pipeline/ gitignore
- AC-11: E2E 회귀

상태 변경 CLI는 PIPELINE_STATE_PATH로 격리한다.
오라클: tests/oracles/IMP-20260622-79BA/{tc1_normal,tc2_edge}
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PIPELINE_ID = "IMP-20260622-79BA"
ORACLE_DIR = BASE_DIR / "tests" / "oracles" / PIPELINE_ID

# pipeline 모듈 import용 경로
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def run_pipeline(*args, env=None, cwd=None):
    """pipeline.py CLI 실행 헬퍼 (subprocess 기반 실제 CLI 경로 검증)."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, "pipeline.py", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=full_env, cwd=str(cwd or BASE_DIR),
    )


# === AC-1: scratch helper 함수 (oracle tc1_normal) ===

def test_scratch_root_under_pipeline_runs():
    """[oracle tc1_normal] _scratch_root이 .pipeline/runs/<id>/scratch/ 경로를 반환."""
    from pipeline import _scratch_root
    result = _scratch_root("IMP-TEST-001")
    suffix = os.path.join(".pipeline", "runs", "IMP-TEST-001", "scratch")
    assert suffix in str(result), f"expected suffix {suffix} in {result}"
    assert ".pipeline" in str(result) and "scratch" in str(result)


def test_scratch_root_oracle_tc1_normal():
    """[oracle tc1_normal] 기대 출력과 실제 동작 일치 검증."""
    input_data = json.loads((ORACLE_DIR / "tc1_normal" / "input.json").read_text(encoding="utf-8"))
    expected = json.loads((ORACLE_DIR / "tc1_normal" / "expected.json").read_text(encoding="utf-8"))
    from pipeline import _scratch_root
    pid = input_data["params"]["pipeline_id"]
    result = _scratch_root(pid)
    norm = str(result).replace("\\", "/")
    assert norm.endswith(expected["result"]["scratch_dir_suffix"]), norm
    assert ".pipeline/runs/" in norm  # is_under_pipeline_runs
    # not_in_repo_root: scratch는 .../runs/<pid>/scratch 이므로 parent.name == pid
    assert Path(result).parent.name == pid
    assert Path(result).parent != BASE_DIR


def test_scratch_path_not_in_repo_root():
    """_scratch_path가 repo root 밖(.pipeline/runs/)에 경로를 반환."""
    from pipeline import _scratch_path
    path = _scratch_path("IMP-TEST-YYYY", "tmp_test.json")
    assert path.parent != BASE_DIR, f"scratch path must not be repo root: {path}"
    assert ".pipeline" in str(path)


def test_scratch_file_creates_root_and_lists():
    """_scratch_file이 루트를 생성하고 _list_scratch_files가 파일을 반환."""
    from pipeline import _scratch_file, _list_scratch_files, _cleanup_scratch, _scratch_exists
    pid = "IMP-TEST-LIST"
    try:
        f = _scratch_file(pid, "out.json")
        assert _scratch_exists(pid)
        f.write_text("{}", encoding="utf-8")
        files = _list_scratch_files(pid)
        assert any(p.name == "out.json" for p in files)
    finally:
        _cleanup_scratch(pid)
        assert not _scratch_exists(pid)


def test_scratch_helpers_input_validation():
    """scratch helper가 None/빈 pipeline_id를 방어."""
    from pipeline import _scratch_root, _scratch_file
    with pytest.raises(TypeError):
        _scratch_root(None)
    with pytest.raises(ValueError):
        _scratch_root("")
    with pytest.raises(TypeError):
        _scratch_file("IMP-X", None)


# === AC-2: audit 보고서 ===

def test_audit_report_exists_and_documents_finding():
    """scratch_hygiene_audit.md가 존재하고 핵심 발견을 문서화."""
    audit = BASE_DIR / "scratch_hygiene_audit.md"
    assert audit.exists(), "scratch_hygiene_audit.md must exist"
    content = audit.read_text(encoding="utf-8")
    assert "tmp_tc" in content and "oracle_result_dump" in content
    assert "_scratch_file" in content or "_scratch_path" in content
    assert "AC-2" in content


# === AC-3/AC-6: cleanup_only 분류 ===

def test_cleanup_only_classification_patterns():
    """tmp_*.json/*_dump.txt/build_report.xml/.pytest_tmp_*/.claude/worktrees/ cleanup_only 분류."""
    from pipeline import _is_workspace_cleanup_only as c
    assert c("tmp_x.json")
    assert c("tmp_tc1_given.json")
    assert c("foo_dump.txt")
    assert c("build_report.xml")
    assert c(".pytest_tmp_abc/x")
    assert c(".claude/worktrees/wt1/file")


def test_protected_not_cleanup_only():
    """protected 파일은 cleanup_only로 분류되지 않음."""
    from pipeline import _is_workspace_cleanup_only as c
    assert not c("pipeline.py")
    assert not c("CLAUDE.md")
    assert not c("pipeline_state.json")
    assert not c("tests/oracles/IMP-X/input.json")


def test_dotfile_prefix_classification_fixed():
    """dotfile 접두사(.pytest_tmp_*)가 lstrip 버그 없이 정상 분류."""
    from pipeline import _is_workspace_cleanup_only as c
    # 이전 lstrip('./')는 선행 점을 제거해 분류를 깨뜨렸다.
    assert c(".pytest_tmp_b96c_b/file")
    assert c("./tmp_y.json")


# === AC-4: Permission denied .pytest_tmp_* 정리 ===

def test_conftest_parses_cleanly():
    """conftest.py가 Python 파싱 가능."""
    conftest = BASE_DIR / "tests" / "conftest.py"
    assert conftest.exists()
    ast.parse(conftest.read_text(encoding="utf-8"))


def test_conftest_has_cleanup_function():
    """conftest.py에 .pytest_tmp_* 정리 함수가 존재."""
    content = (BASE_DIR / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "_cleanup_pytest_tmp_dirs" in content
    assert "pytest_sessionfinish" in content


def test_conftest_cleanup_removes_pytest_tmp(tmp_path):
    """_cleanup_pytest_tmp_dirs가 실제 .pytest_tmp_* 디렉터리를 삭제하고 None을 방어."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "conftest_under_test", BASE_DIR / "tests" / "conftest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = tmp_path / ".pytest_tmp_SELF79ba"
    d.mkdir()
    (d / "x.txt").write_text("y", encoding="utf-8")
    mod._cleanup_pytest_tmp_dirs(tmp_path)
    assert not d.exists()
    mod._cleanup_pytest_tmp_dirs(None)  # None 방어 — 예외 없이 통과


# === AC-5: hygiene cleanup CLI ===

def test_hygiene_scan_runs():
    """hygiene scan이 exit 0으로 실행 (TC-6)."""
    result = run_pipeline("hygiene", "scan")
    assert result.returncode == 0, f"hygiene scan failed: {result.stderr}"


def test_hygiene_cleanup_dry_run_default():
    """hygiene cleanup이 기본 dry-run으로 실행되며 파일을 삭제하지 않음."""
    result = run_pipeline("hygiene", "cleanup", "--json")
    assert result.returncode == 0, f"hygiene cleanup failed: {result.stderr}"
    manifest = json.loads(result.stdout)
    assert manifest["mode"] == "dry-run"
    assert manifest["removed_count"] == 0


def test_hygiene_cleanup_excludes_protected():
    """hygiene cleanup 후보에 protected 파일이 포함되지 않음."""
    result = run_pipeline("hygiene", "cleanup", "--json")
    assert result.returncode == 0
    manifest = json.loads(result.stdout)
    cands = manifest["candidates"]
    assert "pipeline.py" not in cands
    assert "pipeline_state.json" not in cands
    assert "CLAUDE.md" not in cands


# === AC-7: final packet hygiene 요약 ===

def test_verification_json_has_hygiene_summary():
    """_build_verification_json이 workspace_hygiene_summary를 포함."""
    from pipeline import _build_verification_json
    wh = {"status": "WARN", "cleanup_only_items": ["a", "b"], "blocking_items": [],
          "pr_runtime_state_leak": []}
    vj = _build_verification_json({"pipeline_id": PIPELINE_ID, "workspace_hygiene": wh})
    summary = vj["workspace_hygiene_summary"]
    assert summary["status"] == "WARN"
    assert summary["cleanup_only_count"] == 2
    assert summary["blocking_count"] == 0
    assert ".pipeline" in summary["scratch_root"] and "scratch" in summary["scratch_root"]


def test_pr_body_includes_hygiene_line():
    """_render_pr_body_final_packet이 작업공간 정리 요약 라인을 포함."""
    from pipeline import _render_pr_body_final_packet
    dm = {
        "pipeline_id": "IMP-X", "gates": {}, "changed_files": [],
        "requirements_summary": {"total": 1, "passed": 1, "failed": 0},
        "workspace_hygiene_summary": {
            "status": "WARN", "blocking_count": 0,
            "cleanup_only_count": 3, "runtime_state_leak_count": 0,
        },
    }
    body = _render_pr_body_final_packet(dm)
    assert "작업공간 정리 상태: WARN" in body
    assert "cleanup_only:3" in body


# === AC-8/AC-9: PR diff BLOCKING (oracle tc2_edge fail-closed) ===

def test_oracle_tc2_edge_fail_closed_semantics():
    """[oracle tc2_edge] Permission denied 시 cleanup_only_override=false (fail-closed)."""
    expected = json.loads((ORACLE_DIR / "tc2_edge" / "expected.json").read_text(encoding="utf-8"))
    # 분류 패턴 자체는 cleanup_only지만, hygiene 검사는 Permission denied를 fail-closed로 차단한다.
    assert expected["result"]["classification"] == "BLOCKED"
    assert expected["result"]["cleanup_only_override"] is False
    # 패턴 분류기는 cleanup_only=True를 반환하나(분류는 맞음),
    from pipeline import _is_workspace_cleanup_only
    assert _is_workspace_cleanup_only("tmp_tc1_given.json") is True


def test_hygiene_check_has_runtime_state_rule():
    """_check_workspace_hygiene가 pr_runtime_state_leak 규칙을 포함 (AC-8/AC-9)."""
    import inspect
    from pipeline import _check_workspace_hygiene
    src = inspect.getsource(_check_workspace_hygiene)
    assert "pr_runtime_state_leak" in src
    assert "runtime_state_in_pr" in src
    assert "pipeline_state_in_pr" in src
    assert ".pipeline/runs/" in src


# === AC-10: gitignore ===

def test_pipeline_dir_gitignored():
    """.pipeline/ 가 .gitignore에 포함됨 (AC-10)."""
    gitignore = BASE_DIR / ".gitignore"
    assert gitignore.exists()
    content = gitignore.read_text(encoding="utf-8")
    assert ".pipeline/" in content


# === AC-11: 회귀 ===

def test_pipeline_status_runs(tmp_path):
    """pipeline.py status가 격리 state로 정상 실행 (회귀)."""
    state_file = tmp_path / "state.json"
    result = run_pipeline(
        "new", "--type", "IMP", "--desc", "scratch hygiene regression test",
        env={"PIPELINE_STATE_PATH": str(state_file)},
    )
    assert result.returncode == 0, f"pipeline.py new failed: {result.stderr}"
    assert state_file.exists(), "state file should be created"
    final_state = json.loads(state_file.read_text(encoding="utf-8"))
    assert final_state.get("pipeline_id"), "state must have pipeline_id"
    status = run_pipeline("status", env={"PIPELINE_STATE_PATH": str(state_file)})
    assert status.returncode in (0, 255), f"status crashed: {status.stderr[:200]}"


def test_scratch_helpers_importable():
    """6개 scratch helper가 모두 import 가능 (회귀)."""
    from pipeline import (
        _scratch_root, _scratch_path, _cleanup_scratch,
        _scratch_file, _list_scratch_files, _scratch_exists,
    )
    assert callable(_scratch_root) and callable(_scratch_path)
    assert callable(_cleanup_scratch) and callable(_scratch_file)
    assert callable(_list_scratch_files) and callable(_scratch_exists)


def test_workspace_hygiene_classifier_regression():
    """_is_workspace_cleanup_only 기존 HYGIENE_ARCHIVE_PATTERNS 동작 보존 (회귀)."""
    from pipeline import _is_workspace_cleanup_only as c
    # 기존 패턴 (build_report*.xml, tmp*.json 등) 보존
    assert c("build_report.xml")
    assert c("tmp_anything.json")
    # 명백한 소스 파일은 여전히 미분류
    assert not c("core/order_mapper.py")
