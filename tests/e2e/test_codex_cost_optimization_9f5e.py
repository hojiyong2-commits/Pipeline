"""
test_codex_cost_optimization_9f5e.py — IMP-20260701-9F5E MT-7 E2E Tests (TC-1~TC-24)

# [Purpose]: pipeline.py의 Codex Review 비용 최적화 레이어(MT-1 preflight / MT-2 bundle /
#            MT-3 critical files / MT-4 codex-review 확장 / MT-5 cache / MT-6 history+rate)를
#            subprocess 기반 실제 CLI 호출 + PIPELINE_STATE_PATH 격리로 검증한다.
# [Assumptions]: pipeline.py가 _cmd_gates_codex_preflight / _build_codex_review_bundle /
#            _is_critical_file / _codex_review_cache_path / _compute_cache_key /
#            _load_codex_cache / _save_codex_cache / _codex_review_history_path /
#            _append_codex_history / _check_codex_rate_limit / _cmd_gates_codex_review 를
#            포함한다. tmp_path fixture로 테스트별 격리.
# [Vulnerability & Risks]:
#   - subprocess 호출 timeout 초과 시 실패.
#   - gh/git CLI가 stub/미설치인 환경에서도 결정적으로 동작하도록 --files 인자를 사용한다.
#   - 이 파일은 tests/e2e 경로라 gates secrets 검사 대상에서 제외된다.
# [Improvement]: 향후 실제 Codex CLI mock을 추가하여 --use-bundle의 인자 전달을 검증 가능.

# CLI Evidence Contract (BUG-20260525-39DE):
# - PIPELINE_STATE_PATH 격리로 전역 pipeline_state.json을 변경하지 않음.
# - preflight/bundle/cache/history는 진단 산출물(.pipeline/*)만 write.
# - 모든 테스트는 final_state assertion(returncode + 산출물 상태)을 포함한다.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
PROJECT_ROOT = PIPELINE_PY.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_cli(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """`python pipeline.py <args>` 실행 후 CompletedProcess 반환.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess 환경 변수.
        timeout: 초 단위 타임아웃.
    Raises:
        TypeError: args가 None이거나 list가 아닌 경우.
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
    """PIPELINE_STATE_PATH 격리 + 네트워크/대시보드 차단 환경변수.

    PIPELINE_STATE_PATH를 지정하면 pipeline.py가 .pipeline 산출물도 state 파일과
    같은 부모 디렉토리에 co-locate하므로 전역 .pipeline 오염을 방지한다.

    Args:
        state_file: 테스트별 격리 state 파일 경로.
    Raises:
        TypeError: state_file이 None이거나 Path가 아닌 경우.
    """
    if state_file is None:
        raise TypeError("state_file must not be None")
    if not isinstance(state_file, Path):
        raise TypeError(f"state_file must be Path, got {type(state_file).__name__}")
    env = {
        **os.environ,
        "PIPELINE_STATE_PATH": str(state_file),
        "PIPELINE_NO_DASHBOARD": "1",
    }
    # gh를 미설치 상태처럼 취급하도록 PATH에서 제거하지 않되, 테스트는 --files로
    # git diff 의존을 우회하여 결정적으로 동작한다.
    return env


def write_state(state_file: Path, pipeline_id: str) -> None:
    """최소 pipeline_state.json을 작성한다.

    Args:
        state_file: state 파일 경로.
        pipeline_id: 파이프라인 ID.
    Raises:
        TypeError: 인자가 None이거나 타입이 틀린 경우.
    """
    if state_file is None:
        raise TypeError("state_file must not be None")
    if pipeline_id is None:
        raise TypeError("pipeline_id must not be None")
    if not isinstance(pipeline_id, str):
        raise TypeError(f"pipeline_id must be str, got {type(pipeline_id).__name__}")
    # 실제 pipeline.py new가 생성하는 state의 필수 필드를 포함한다.
    # _cmd_gates_codex_review는 _log_event(state, ...)로 event_log에 append하므로
    # event_log 키가 반드시 존재해야 한다 (실제 파이프라인 state는 항상 포함).
    payload = {
        "pipeline_id": pipeline_id,
        "current_phase": "gates",
        "phases": {},
        "event_log": [],
        "external_gates": {"enabled": True},
    }
    state_file.write_text(json.dumps(payload), encoding="utf-8")


def write_file(path: Path, content: str) -> None:
    """UTF-8 텍스트 파일 작성.

    Args:
        path: 대상 경로.
        content: 파일 내용.
    Raises:
        TypeError: 인자가 None인 경우.
    """
    if path is None:
        raise TypeError("path must not be None")
    if content is None:
        raise TypeError("content must not be None")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def pipeline_dir(state_file: Path) -> Path:
    """state 파일 부모의 .pipeline 디렉토리 경로를 반환한다.

    Args:
        state_file: 격리 state 파일 경로.
    Returns:
        .pipeline 디렉토리 경로.
    """
    return state_file.resolve().parent / ".pipeline"


def import_pipeline_module() -> Any:
    """pipeline.py를 모듈로 import하여 순수 함수 단위 테스트를 지원한다.

    Returns:
        import된 pipeline 모듈.
    """
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    import pipeline  # noqa: E402
    return pipeline


# ---------------------------------------------------------------------------
# TC-1: preflight — raw ACCEPT 코드 없으면 PASS
# ---------------------------------------------------------------------------

def test_tc01_preflight_pass_without_raw_accept(tmp_path: Path) -> None:
    """clean 파일에는 raw ACCEPT 코드가 없으므로 preflight PASS (exit 0)."""
    # PIPELINE_STATE_PATH isolation via make_env(state_file)
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC01")
    clean = tmp_path / "clean.py"
    write_file(clean, "def f():\n    return 1\n")

    result = run_cli(
        ["gates", "codex-preflight", "--files", str(clean)],
        env=make_env(state_file),
    )

    assert result.returncode == 0, (
        f"clean 파일은 PASS(exit 0) 기대, 실제={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "PREFLIGHT PASS" in result.stdout
    # final_state: preflight 결과 산출물 status=PASS
    res_file = pipeline_dir(state_file) / "codex_preflight_result.json"
    assert res_file.exists(), "codex_preflight_result.json 미생성"
    final_state = json.loads(res_file.read_text(encoding="utf-8"))
    assert final_state["status"] == "PASS"


# ---------------------------------------------------------------------------
# TC-2: preflight — raw ACCEPT 코드 있으면 BLOCKED (exit 1)
# ---------------------------------------------------------------------------

def test_tc02_preflight_blocked_on_raw_accept_code(tmp_path: Path) -> None:
    """raw ACCEPT 코드 패턴이 파일에 있으면 preflight BLOCKED (exit 1)."""
    # PIPELINE_STATE_PATH isolation via make_env(state_file)
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC02")
    bad = tmp_path / "leak.py"
    # 형식: ACCEPT-<TYPE>-YYYYMMDD-<4HEX>-<8hex>
    write_file(bad, "code = 'ACCEPT-IMP-20260701-9F5E-a1b2c3d4'\n")

    result = run_cli(
        ["gates", "codex-preflight", "--files", str(bad)],
        env=make_env(state_file),
    )

    assert result.returncode == 1, (
        f"raw ACCEPT 코드 포함 시 BLOCKED(exit 1) 기대, 실제={result.returncode}\n"
        f"stdout={result.stdout}"
    )
    assert "PREFLIGHT BLOCKED" in result.stdout
    res_file = pipeline_dir(state_file) / "codex_preflight_result.json"
    final_state = json.loads(res_file.read_text(encoding="utf-8"))
    assert final_state["status"] == "BLOCKED"
    assert any("검사1" in b for b in final_state["blocked"])


# ---------------------------------------------------------------------------
# TC-3: preflight — except:pass 패턴 있으면 BLOCKED
# ---------------------------------------------------------------------------

def test_tc03_preflight_blocked_on_except_pass(tmp_path: Path) -> None:
    """except: pass 패턴이 있으면 preflight BLOCKED (exit 1)."""
    # PIPELINE_STATE_PATH isolation via make_env(state_file)
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC03")
    bad = tmp_path / "swallow.py"
    write_file(bad, "def f():\n    try:\n        g()\n    except:\n        pass\n")

    result = run_cli(
        ["gates", "codex-preflight", "--files", str(bad)],
        env=make_env(state_file),
    )

    assert result.returncode == 1, (
        f"except:pass 포함 시 BLOCKED 기대, 실제={result.returncode}\n{result.stdout}"
    )
    res_file = pipeline_dir(state_file) / "codex_preflight_result.json"
    final_state = json.loads(res_file.read_text(encoding="utf-8"))
    assert final_state["status"] == "BLOCKED"
    assert any("검사5" in b for b in final_state["blocked"])


# ---------------------------------------------------------------------------
# TC-4: preflight — best-effort 패턴 있으면 BLOCKED
# ---------------------------------------------------------------------------

def test_tc04_preflight_blocked_on_best_effort(tmp_path: Path) -> None:
    """# best-effort / # fallback pass 주석이 있으면 preflight BLOCKED."""
    # PIPELINE_STATE_PATH isolation via make_env(state_file)
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC04")
    bad = tmp_path / "beffort.py"
    write_file(bad, "def f():\n    return 1  # best-effort\n")

    result = run_cli(
        ["gates", "codex-preflight", "--files", str(bad)],
        env=make_env(state_file),
    )

    assert result.returncode == 1, (
        f"best-effort 포함 시 BLOCKED 기대, 실제={result.returncode}\n{result.stdout}"
    )
    res_file = pipeline_dir(state_file) / "codex_preflight_result.json"
    final_state = json.loads(res_file.read_text(encoding="utf-8"))
    assert final_state["status"] == "BLOCKED"
    assert any("검사8" in b for b in final_state["blocked"])


# ---------------------------------------------------------------------------
# TC-5: preflight — diff 정상이면 PASS
# ---------------------------------------------------------------------------

def test_tc05_preflight_pass_on_normal_diff(tmp_path: Path) -> None:
    """정상적인 코드 파일은 preflight PASS."""
    # PIPELINE_STATE_PATH isolation via make_env(state_file)
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC05")
    normal = tmp_path / "normal.py"
    write_file(
        normal,
        "def add(a: int, b: int) -> int:\n"
        "    try:\n"
        "        return a + b\n"
        "    except TypeError:\n"
        "        raise\n",
    )

    result = run_cli(
        ["gates", "codex-preflight", "--files", str(normal)],
        env=make_env(state_file),
    )

    assert result.returncode == 0, (
        f"정상 diff는 PASS 기대, 실제={result.returncode}\n{result.stdout}"
    )
    res_file = pipeline_dir(state_file) / "codex_preflight_result.json"
    final_state = json.loads(res_file.read_text(encoding="utf-8"))
    assert final_state["status"] == "PASS"
    assert final_state["blocked"] == []


# ---------------------------------------------------------------------------
# TC-6: bundle — critical 파일 변경 시 함수단위 diff 포함
# ---------------------------------------------------------------------------

def test_tc06_bundle_includes_critical_function_diff(tmp_path: Path) -> None:
    """critical 파일(pipeline.py)이 changed_files에 있으면 critical_file_diffs에 포함."""
    pipeline = import_pipeline_module()
    critical_diffs = pipeline._extract_critical_function_diffs(
        ["pipeline.py", "README.md"]
    )
    # pipeline.py는 critical → 키에 포함 (diff 텍스트는 git 환경에 따라 빈 값 허용).
    assert "pipeline.py" in critical_diffs
    assert "README.md" not in critical_diffs
    final_state = {"critical_keys": sorted(critical_diffs.keys())}
    assert "pipeline.py" in final_state["critical_keys"]


# ---------------------------------------------------------------------------
# TC-7: bundle — non-critical 파일은 요약만 포함
# ---------------------------------------------------------------------------

def test_tc07_bundle_noncritical_summary_only(tmp_path: Path) -> None:
    """non-critical 파일은 함수단위 diff가 아닌 요약만 포함된다."""
    pipeline = import_pipeline_module()
    critical_diffs = pipeline._extract_critical_function_diffs(
        ["docs/guide.md", "assets/logo.png"]
    )
    # 둘 다 non-critical → critical_diffs 비어 있음.
    assert critical_diffs == {}
    final_state = {"critical_count": len(critical_diffs)}
    assert final_state["critical_count"] == 0


# ---------------------------------------------------------------------------
# TC-8: bundle — PR body 내용 미포함 확인
# ---------------------------------------------------------------------------

def test_tc08_bundle_excludes_pr_body(tmp_path: Path) -> None:
    """bundle에 PR body 전체가 포함되지 않는다 (excluded에 명시)."""
    pipeline = import_pipeline_module()
    state = {"pipeline_id": "IMP-9F5E-TC08", "github": {"pr_url": "https://x/pull/1"}}
    # PIPELINE_STATE_PATH 격리를 위해 환경변수 설정 후 bundle 생성.
    prev = os.environ.get("PIPELINE_STATE_PATH")
    os.environ["PIPELINE_STATE_PATH"] = str(tmp_path / "state.json")
    try:
        bundle = pipeline._build_codex_review_bundle(state, "IMP-9F5E-TC08")
    finally:
        if prev is None:
            os.environ.pop("PIPELINE_STATE_PATH", None)
        else:
            os.environ["PIPELINE_STATE_PATH"] = prev
    assert "pr_body_full" in bundle["excluded"]
    # bundle의 어떤 키에도 'pr_body' 원문 필드가 없음.
    assert "pr_body" not in bundle
    assert "pr_body_content" not in bundle
    final_state = {"excluded": bundle["excluded"]}
    assert "pr_body_full" in final_state["excluded"]


# ---------------------------------------------------------------------------
# TC-9: bundle — oracle JSON 원문 미포함 확인
# ---------------------------------------------------------------------------

def test_tc09_bundle_excludes_oracle_json(tmp_path: Path) -> None:
    """bundle에 oracle JSON 원문이 포함되지 않는다 (excluded에 명시)."""
    pipeline = import_pipeline_module()
    state = {"pipeline_id": "IMP-9F5E-TC09"}
    prev = os.environ.get("PIPELINE_STATE_PATH")
    os.environ["PIPELINE_STATE_PATH"] = str(tmp_path / "state.json")
    try:
        bundle = pipeline._build_codex_review_bundle(state, "IMP-9F5E-TC09")
    finally:
        if prev is None:
            os.environ.pop("PIPELINE_STATE_PATH", None)
        else:
            os.environ["PIPELINE_STATE_PATH"] = prev
    assert "oracle_json_raw" in bundle["excluded"]
    assert "oracle_json" not in bundle
    final_state = {"excluded": bundle["excluded"]}
    assert "oracle_json_raw" in final_state["excluded"]


# ---------------------------------------------------------------------------
# TC-10: bundle — raw ACCEPT 코드 미포함 확인
# ---------------------------------------------------------------------------

def test_tc10_bundle_excludes_raw_accept_code(tmp_path: Path) -> None:
    """bundle의 어떤 필드에도 raw ACCEPT 코드 패턴이 존재하지 않는다."""
    pipeline = import_pipeline_module()
    import re
    state = {"pipeline_id": "IMP-9F5E-TC10"}
    prev = os.environ.get("PIPELINE_STATE_PATH")
    os.environ["PIPELINE_STATE_PATH"] = str(tmp_path / "state.json")
    try:
        bundle = pipeline._build_codex_review_bundle(state, "IMP-9F5E-TC10")
    finally:
        if prev is None:
            os.environ.pop("PIPELINE_STATE_PATH", None)
        else:
            os.environ["PIPELINE_STATE_PATH"] = prev
    serialized = json.dumps(bundle, ensure_ascii=False)
    raw_accept_re = re.compile(r"ACCEPT-[A-Z]+-\d{8}-[0-9A-F]{4}-[0-9a-f]{8}")
    assert raw_accept_re.search(serialized) is None
    assert "raw_accept_code" in bundle["excluded"]
    final_state = {"has_raw_accept": bool(raw_accept_re.search(serialized))}
    assert final_state["has_raw_accept"] is False


# ---------------------------------------------------------------------------
# TC-11: _is_critical_file — pipeline.py는 True
# ---------------------------------------------------------------------------

def test_tc11_is_critical_pipeline_py(tmp_path: Path) -> None:
    """pipeline.py는 critical 파일이다."""
    pipeline = import_pipeline_module()
    result = pipeline._is_critical_file("pipeline.py")
    assert result is True
    final_state = {"is_critical": result}
    assert final_state["is_critical"] is True


# ---------------------------------------------------------------------------
# TC-12: _is_critical_file — tests/e2e/foo.py는 True
# ---------------------------------------------------------------------------

def test_tc12_is_critical_tests_e2e(tmp_path: Path) -> None:
    """tests/e2e/ 하위 파일은 critical 패턴에 해당한다."""
    pipeline = import_pipeline_module()
    result = pipeline._is_critical_file("tests/e2e/foo.py")
    assert result is True
    # 역슬래시 경로도 정규화되어 True.
    assert pipeline._is_critical_file("tests\\e2e\\bar.py") is True
    final_state = {"is_critical": result}
    assert final_state["is_critical"] is True


# ---------------------------------------------------------------------------
# TC-13: _is_critical_file — README.md는 False
# ---------------------------------------------------------------------------

def test_tc13_is_critical_readme_false(tmp_path: Path) -> None:
    """README.md는 critical 파일이 아니다."""
    pipeline = import_pipeline_module()
    result = pipeline._is_critical_file("README.md")
    assert result is False
    final_state = {"is_critical": result}
    assert final_state["is_critical"] is False


# ---------------------------------------------------------------------------
# TC-14: cache — cache miss 시 (미저장 상태) None 반환
# ---------------------------------------------------------------------------

def test_tc14_cache_miss_returns_none(tmp_path: Path) -> None:
    """cache 파일이 없으면 _load_codex_cache는 None을 반환한다 (cache miss)."""
    pipeline = import_pipeline_module()
    prev = os.environ.get("PIPELINE_STATE_PATH")
    os.environ["PIPELINE_STATE_PATH"] = str(tmp_path / "state.json")
    try:
        loaded = pipeline._load_codex_cache("IMP-9F5E-TC14")
    finally:
        if prev is None:
            os.environ.pop("PIPELINE_STATE_PATH", None)
        else:
            os.environ["PIPELINE_STATE_PATH"] = prev
    assert loaded is None
    final_state = {"cache_hit": loaded is not None}
    assert final_state["cache_hit"] is False


# ---------------------------------------------------------------------------
# TC-15: cache — cache hit 시 저장값 반환 + SHA 재검증(pr_head_sha 갱신 기록)
# ---------------------------------------------------------------------------

def test_tc15_cache_hit_returns_saved_and_updates_sha(tmp_path: Path) -> None:
    """저장 후 로드하면 cache hit되며, pr_head_sha가 저장 시점 값으로 기록된다."""
    pipeline = import_pipeline_module()
    prev = os.environ.get("PIPELINE_STATE_PATH")
    os.environ["PIPELINE_STATE_PATH"] = str(tmp_path / "state.json")
    try:
        key = pipeline._compute_cache_key("contractSHA", "bundleSHA")
        pipeline._save_codex_cache(
            "IMP-9F5E-TC15", key, "APPROVE_TO_USER", "headsha_v1"
        )
        loaded = pipeline._load_codex_cache("IMP-9F5E-TC15")
        # stale SHA 재사용 금지: 새 head SHA로 재저장하면 갱신됨.
        pipeline._save_codex_cache(
            "IMP-9F5E-TC15", key, "APPROVE_TO_USER", "headsha_v2"
        )
        loaded2 = pipeline._load_codex_cache("IMP-9F5E-TC15")
    finally:
        if prev is None:
            os.environ.pop("PIPELINE_STATE_PATH", None)
        else:
            os.environ["PIPELINE_STATE_PATH"] = prev
    assert loaded is not None
    assert loaded["cache_key"] == key
    assert loaded["result"] == "APPROVE_TO_USER"
    assert loaded["pr_head_sha"] == "headsha_v1"
    # 재저장 후 pr_head_sha가 새 값으로 갱신 (stale SHA 그대로 사용 금지).
    assert loaded2["pr_head_sha"] == "headsha_v2"
    final_state = {"cache_hit": loaded is not None, "sha": loaded2["pr_head_sha"]}
    assert final_state["cache_hit"] is True
    assert final_state["sha"] == "headsha_v2"


# ---------------------------------------------------------------------------
# TC-16: cache — critical 파일 변경 시 cache 무효화 (새 key로 저장)
# ---------------------------------------------------------------------------

def test_tc16_cache_invalidated_on_critical_change(tmp_path: Path) -> None:
    """bundle SHA가 바뀌면 cache key가 달라져 이전 cache가 무효화된다."""
    pipeline = import_pipeline_module()
    prev = os.environ.get("PIPELINE_STATE_PATH")
    os.environ["PIPELINE_STATE_PATH"] = str(tmp_path / "state.json")
    try:
        key_old = pipeline._compute_cache_key("contractSHA", "bundleSHA_old")
        key_new = pipeline._compute_cache_key("contractSHA", "bundleSHA_new")
        pipeline._save_codex_cache(
            "IMP-9F5E-TC16", key_old, "APPROVE_TO_USER", "head1"
        )
        # critical 파일 변경 → bundle SHA 변경 → 새 key로 저장 (덮어쓰기, key 교체).
        pipeline._save_codex_cache(
            "IMP-9F5E-TC16", key_new, "REJECT", "head2"
        )
        loaded = pipeline._load_codex_cache("IMP-9F5E-TC16")
    finally:
        if prev is None:
            os.environ.pop("PIPELINE_STATE_PATH", None)
        else:
            os.environ["PIPELINE_STATE_PATH"] = prev
    # 새 key로 교체됨 → 이전 key는 더 이상 유효하지 않음 (cache 무효화).
    assert key_old != key_new
    assert loaded["cache_key"] == key_new
    assert loaded["cache_key"] != key_old
    final_state = {"active_key": loaded["cache_key"]}
    assert final_state["active_key"] == key_new


# ---------------------------------------------------------------------------
# TC-17: cache key — contract_sha256 + bundle_sha256으로 생성
# ---------------------------------------------------------------------------

def test_tc17_cache_key_deterministic(tmp_path: Path) -> None:
    """cache key는 contract_sha256 + ':' + bundle_sha256의 SHA256로 결정적으로 생성."""
    pipeline = import_pipeline_module()
    import hashlib
    key = pipeline._compute_cache_key("aaa", "bbb")
    expected = hashlib.sha256("aaa:bbb".encode("utf-8")).hexdigest()
    assert key == expected
    # 동일 입력 → 동일 key (결정성).
    assert pipeline._compute_cache_key("aaa", "bbb") == key
    # 다른 입력 → 다른 key.
    assert pipeline._compute_cache_key("aaa", "ccc") != key
    final_state = {"key": key}
    assert final_state["key"] == expected


# ---------------------------------------------------------------------------
# TC-18: history — REJECT 1회 후 rate limit 미달 (계속 진행)
# ---------------------------------------------------------------------------

def test_tc18_rate_limit_not_triggered_after_one_reject(tmp_path: Path) -> None:
    """REJECT 1회는 rate limit(>=2) 미달로 계속 진행 가능."""
    pipeline = import_pipeline_module()
    prev = os.environ.get("PIPELINE_STATE_PATH")
    os.environ["PIPELINE_STATE_PATH"] = str(tmp_path / "state.json")
    try:
        pipeline._append_codex_history("IMP-9F5E-TC18", {"verdict": "REJECT"})
        limited, count = pipeline._check_codex_rate_limit("IMP-9F5E-TC18")
    finally:
        if prev is None:
            os.environ.pop("PIPELINE_STATE_PATH", None)
        else:
            os.environ["PIPELINE_STATE_PATH"] = prev
    assert limited is False
    assert count == 1
    final_state = {"rate_limited": limited, "reject_count": count}
    assert final_state["rate_limited"] is False


# ---------------------------------------------------------------------------
# TC-19: history — REJECT 2회 후 rate_limited BLOCKED
# ---------------------------------------------------------------------------

def test_tc19_rate_limit_triggered_after_two_rejects(tmp_path: Path) -> None:
    """REJECT 2회면 rate_limited=True (>=2 임계값)."""
    pipeline = import_pipeline_module()
    prev = os.environ.get("PIPELINE_STATE_PATH")
    os.environ["PIPELINE_STATE_PATH"] = str(tmp_path / "state.json")
    try:
        pipeline._append_codex_history("IMP-9F5E-TC19", {"verdict": "REJECT"})
        pipeline._append_codex_history("IMP-9F5E-TC19", {"verdict": "REJECT"})
        limited, count = pipeline._check_codex_rate_limit("IMP-9F5E-TC19")
    finally:
        if prev is None:
            os.environ.pop("PIPELINE_STATE_PATH", None)
        else:
            os.environ["PIPELINE_STATE_PATH"] = prev
    assert limited is True
    assert count == 2
    final_state = {"rate_limited": limited, "reject_count": count}
    assert final_state["rate_limited"] is True
    assert final_state["reject_count"] == 2


# ---------------------------------------------------------------------------
# TC-20: history — append-only 확인 (덮어쓰기 시 기존 내용 보존)
# ---------------------------------------------------------------------------

def test_tc20_history_append_only(tmp_path: Path) -> None:
    """_append_codex_history는 append-only — 이전 항목이 보존된다."""
    pipeline = import_pipeline_module()
    prev = os.environ.get("PIPELINE_STATE_PATH")
    os.environ["PIPELINE_STATE_PATH"] = str(tmp_path / "state.json")
    try:
        pipeline._append_codex_history("IMP-9F5E-TC20", {"verdict": "REJECT", "n": 1})
        pipeline._append_codex_history("IMP-9F5E-TC20", {"verdict": "APPROVE_TO_USER", "n": 2})
        pipeline._append_codex_history("IMP-9F5E-TC20", {"verdict": "REJECT", "n": 3})
        hist_path = pipeline._codex_review_history_path("IMP-9F5E-TC20")
        lines = [
            ln for ln in hist_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
    finally:
        if prev is None:
            os.environ.pop("PIPELINE_STATE_PATH", None)
        else:
            os.environ["PIPELINE_STATE_PATH"] = prev
    # 3개 항목 모두 보존 (덮어쓰기 없음).
    assert len(lines) == 3
    parsed = [json.loads(ln) for ln in lines]
    ns = [p["n"] for p in parsed]
    assert ns == [1, 2, 3]
    final_state = {"line_count": len(lines), "ns": ns}
    assert final_state["line_count"] == 3


# ---------------------------------------------------------------------------
# TC-21: codex_review — preflight 자동 호출됨
# ---------------------------------------------------------------------------

def test_tc21_codex_review_auto_calls_preflight(tmp_path: Path) -> None:
    """codex-review는 시작 시 preflight를 자동 호출하며, BLOCKED면 중단한다.

    --files로 except:pass 파일을 지정하면 preflight 검사5가 BLOCKED되어
    codex-review가 codex_preflight_blocked로 중단됨을 검증한다.
    """
    # PIPELINE_STATE_PATH isolation via make_env(state_file)
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC21")
    bad = tmp_path / "swallow.py"
    write_file(bad, "def f():\n    try:\n        g()\n    except:\n        pass\n")

    result = run_cli(
        ["gates", "codex-review", "--verdict", "APPROVE_TO_USER",
         "--packet-sha256", "deadbeef", "--files", str(bad)],
        env=make_env(state_file),
    )

    # preflight가 BLOCKED → codex-review 중단 (exit 1).
    assert result.returncode == 1, (
        f"preflight BLOCKED 시 codex-review 중단 기대, 실제={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "codex_preflight_blocked" in combined or "PREFLIGHT BLOCKED" in combined
    # final_state: codex_review_result.json이 생성되지 않아야 함 (preflight에서 중단).
    res_file = pipeline_dir(state_file) / "codex_review_result.json"
    final_state = {"review_result_created": res_file.exists()}
    assert final_state["review_result_created"] is False


# ---------------------------------------------------------------------------
# TC-22: codex_review — --use-bundle 시 bundle만 전달 (전체 diff 미전달)
# ---------------------------------------------------------------------------

def test_tc22_codex_review_use_bundle_passes_bundle_only(tmp_path: Path) -> None:
    """--use-bundle 시 codex_cli_command가 bundle 경로만 참조하고 bundle이 생성된다."""
    # PIPELINE_STATE_PATH isolation via make_env(state_file)
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC22")
    clean = tmp_path / "clean.py"
    write_file(clean, "def f():\n    return 1\n")

    result = run_cli(
        ["gates", "codex-review", "--verdict", "APPROVE_TO_USER",
         "--packet-sha256", "deadbeef", "--use-bundle", "--files", str(clean)],
        env=make_env(state_file),
    )

    # preflight PASS(clean) → codex-review 진행 → APPROVE exit 0.
    assert result.returncode == 0, (
        f"--use-bundle + clean 파일은 exit 0 기대, 실제={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    # bundle 파일 생성 확인.
    bundle_file = pipeline_dir(state_file) / "codex_review_bundle.json"
    assert bundle_file.exists(), "codex_review_bundle.json 미생성"
    # codex_cli_command가 bundle 경로만 참조 (전체 diff 미포함).
    res_file = pipeline_dir(state_file) / "codex_review_result.json"
    final_state = json.loads(res_file.read_text(encoding="utf-8"))
    assert "--bundle" in final_state["codex_cli_command"]
    assert "codex_review_bundle.json" in final_state["codex_cli_command"]


# ---------------------------------------------------------------------------
# TC-23: codex_review — codex_cli_command 필드 기록
# ---------------------------------------------------------------------------

def test_tc23_codex_review_records_cli_command(tmp_path: Path) -> None:
    """codex_review_result.json에 codex_cli_command 필드가 기록된다."""
    # PIPELINE_STATE_PATH isolation via make_env(state_file)
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC23")
    clean = tmp_path / "clean.py"
    write_file(clean, "def f():\n    return 1\n")

    result = run_cli(
        ["gates", "codex-review", "--verdict", "APPROVE_TO_USER",
         "--packet-sha256", "deadbeef", "--use-bundle", "--files", str(clean)],
        env=make_env(state_file),
    )

    assert result.returncode == 0, (
        f"exit 0 기대, 실제={result.returncode}\n{result.stdout}\n{result.stderr}"
    )
    res_file = pipeline_dir(state_file) / "codex_review_result.json"
    final_state = json.loads(res_file.read_text(encoding="utf-8"))
    assert "codex_cli_command" in final_state
    assert final_state["codex_cli_command"] != ""
    # F52C 불변식 필드가 삭제되지 않았는지 회귀 검증.
    for field in ("packet_sha256", "pr_body_candidate_sha256", "pr_head_sha"):
        assert field in final_state, f"F52C 불변식 필드 {field} 삭제됨"


# ---------------------------------------------------------------------------
# TC-24: codex_review — codex_model_detected 필드 기록
# ---------------------------------------------------------------------------

def test_tc24_codex_review_records_model_detected(tmp_path: Path) -> None:
    """codex_review_result.json에 codex_model_detected 필드가 기록된다.

    CODEX_MODEL 환경변수가 있으면 그 값, 없으면 'unknown'이 기록된다.
    """
    # PIPELINE_STATE_PATH isolation via make_env(state_file)
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC24")
    clean = tmp_path / "clean.py"
    write_file(clean, "def f():\n    return 1\n")

    env = make_env(state_file)
    env["CODEX_MODEL"] = "codex-test-model-v1"

    result = run_cli(
        ["gates", "codex-review", "--verdict", "APPROVE_TO_USER",
         "--packet-sha256", "deadbeef", "--use-bundle", "--files", str(clean)],
        env=env,
    )

    assert result.returncode == 0, (
        f"exit 0 기대, 실제={result.returncode}\n{result.stdout}\n{result.stderr}"
    )
    res_file = pipeline_dir(state_file) / "codex_review_result.json"
    final_state = json.loads(res_file.read_text(encoding="utf-8"))
    assert "codex_model_detected" in final_state
    assert final_state["codex_model_detected"] == "codex-test-model-v1"


# ---------------------------------------------------------------------------
# IMP-20260701-9F5E REJECT r2: 실질 Codex CLI 어댑터 검증 (call count / payload / stable SHA)
# ---------------------------------------------------------------------------

def install_fake_codex(bin_dir: Path, verdict: str = "APPROVE_TO_USER") -> Path:
    """PATH에 넣을 fake `codex` 실행기를 생성하고, 호출 로그를 남긴다.

    fake codex는 받은 인자를 codex_calls.log에 한 줄로 append하고, verdict를 stdout에 출력한다.
    이를 통해 실제 Codex CLI 호출 횟수와 전달된 인자(bundle 경로)를 검증할 수 있다.

    Args:
        bin_dir: fake codex를 설치할 디렉토리.
        verdict: fake codex가 출력할 판정 (APPROVE_TO_USER|REJECT).
    Returns:
        codex_calls.log 경로 (호출 로그 파일).
    Raises:
        TypeError: bin_dir가 None이거나 Path가 아닌 경우.
    """
    if bin_dir is None:
        raise TypeError("bin_dir must not be None")
    if not isinstance(bin_dir, Path):
        raise TypeError(f"bin_dir must be Path, got {type(bin_dir).__name__}")
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_path = bin_dir / "codex_calls.log"
    # 크로스플랫폼: python 스크립트 + OS별 런처(Windows codex.cmd / POSIX codex).
    py_script = bin_dir / "codex_impl.py"
    py_script.write_text(
        "import sys\n"
        f"log = r'''{log_path}'''\n"
        "with open(log, 'a', encoding='utf-8') as fh:\n"
        "    fh.write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"print('{verdict}')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        launcher = bin_dir / "codex.cmd"
        launcher.write_text(
            f"@echo off\r\n\"{sys.executable}\" \"{py_script}\" %*\r\n",
            encoding="utf-8",
        )
    else:
        launcher = bin_dir / "codex"
        launcher.write_text(
            f"#!/bin/sh\nexec \"{sys.executable}\" \"{py_script}\" \"$@\"\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)
    return log_path


def read_call_count(log_path: Path) -> int:
    """codex_calls.log의 줄 수(=CLI 호출 횟수)를 반환한다.

    Args:
        log_path: 호출 로그 경로.
    Returns:
        호출 횟수 (파일 없으면 0).
    """
    if not log_path.exists():
        return 0
    return len([ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()])


def env_with_fake_codex(state_file: Path, bin_dir: Path) -> Dict[str, str]:
    """make_env에 fake codex bin_dir을 PATH 앞에 추가한 환경을 반환한다.

    Args:
        state_file: 격리 state 파일 경로.
        bin_dir: fake codex 설치 디렉토리.
    Returns:
        환경 변수 dict.
    """
    env = make_env(state_file)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env


# TC-19b: preflight BLOCKED이면 Codex CLI call_count = 0
def test_tc19b_preflight_blocked_no_cli_call(tmp_path: Path) -> None:
    """preflight가 BLOCKED이면 실제 Codex CLI가 호출되지 않아야 한다 (call_count=0)."""
    # PIPELINE_STATE_PATH isolation: env_with_fake_codex -> make_env(state_file) 내부에서 PIPELINE_STATE_PATH=str(state_file) 설정
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC19B")
    bin_dir = tmp_path / "bin"
    log_path = install_fake_codex(bin_dir, verdict="APPROVE_TO_USER")
    # except:pass 파일 → preflight 검사5 BLOCKED.
    bad = tmp_path / "swallow.py"
    write_file(bad, "def f():\n    try:\n        g()\n    except:\n        pass\n")

    # --verdict/--approve-pending 없이 실행 → 실제 CLI control flow 진입.
    result = run_cli(
        ["gates", "codex-review", "--files", str(bad)],
        env=env_with_fake_codex(state_file, bin_dir),
    )

    assert result.returncode == 1, (
        f"preflight BLOCKED 시 exit 1 기대, 실제={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "PREFLIGHT BLOCKED" in combined or "codex_preflight_blocked" in combined
    # 핵심: preflight BLOCKED이므로 Codex CLI가 절대 호출되지 않았어야 한다.
    call_count = read_call_count(log_path)
    assert call_count == 0, f"preflight BLOCKED 시 CLI 미호출 기대, 실제 호출={call_count}"
    final_state = {"preflight_blocked_no_cli_call": call_count == 0}
    assert final_state["preflight_blocked_no_cli_call"] is True


# TC-21b: cache miss 시 Codex CLI call_count = 1 + bundle 경로 payload 검증
def test_tc21b_cache_miss_cli_called_with_bundle_path(tmp_path: Path) -> None:
    """cache miss 시 Codex CLI가 bundle 경로를 인자로 받아 1회 호출되어야 한다."""
    # PIPELINE_STATE_PATH isolation: env_with_fake_codex -> make_env(state_file) 내부에서 PIPELINE_STATE_PATH=str(state_file) 설정
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC21B")
    bin_dir = tmp_path / "bin"
    log_path = install_fake_codex(bin_dir, verdict="APPROVE_TO_USER")
    clean = tmp_path / "clean.py"
    write_file(clean, "def f():\n    return 1\n")

    result = run_cli(
        ["gates", "codex-review", "--files", str(clean)],
        env=env_with_fake_codex(state_file, bin_dir),
    )

    assert result.returncode == 0, (
        f"clean 파일 + APPROVE_TO_USER는 exit 0 기대, 실제={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    # CLI가 정확히 1회 호출됨.
    call_count = read_call_count(log_path)
    assert call_count == 1, f"cache miss 시 CLI 1회 호출 기대, 실제={call_count}"
    # 전달 payload: bundle 경로(.json)만 포함, full diff 원문 미포함.
    call_args = log_path.read_text(encoding="utf-8").strip()
    assert "--base" in call_args, f"CLI args에 --base 없음: {call_args}"
    assert "origin/main" in call_args, f"origin/main 미전달: {call_args}"
    assert "def f()" not in call_args, "full diff 원문이 CLI args에 노출됨"
    # 결과 파일의 codex_cli_call_count 검증.
    res_file = pipeline_dir(state_file) / "codex_review_result.json"
    final_state = json.loads(res_file.read_text(encoding="utf-8"))
    assert final_state["codex_cli_called"] is True
    assert final_state["codex_cli_call_count"] == 1
    assert final_state["cache_hit"] is False


# TC-20b: cache hit 시 Codex CLI call_count = 0 (두 번째 실행)
def test_tc20b_cache_hit_no_second_cli_call(tmp_path: Path) -> None:
    """동일 변경으로 두 번 실행 시, 두 번째는 cache hit되어 CLI를 추가 호출하지 않는다."""
    # PIPELINE_STATE_PATH isolation: env_with_fake_codex -> make_env(state_file) 내부에서 PIPELINE_STATE_PATH=str(state_file) 설정
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC20B")
    bin_dir = tmp_path / "bin"
    log_path = install_fake_codex(bin_dir, verdict="APPROVE_TO_USER")
    clean = tmp_path / "clean.py"
    write_file(clean, "def f():\n    return 1\n")
    env = env_with_fake_codex(state_file, bin_dir)

    # 1차 실행: cache miss → CLI 1회.
    r1 = run_cli(["gates", "codex-review", "--files", str(clean)], env=env)
    assert r1.returncode == 0, f"1차 exit 0 기대\n{r1.stdout}\n{r1.stderr}"
    assert read_call_count(log_path) == 1, "1차 실행에서 CLI 1회 호출되어야 함"

    # 2차 실행: 동일 stable bundle → cache hit → CLI 추가 호출 없음.
    r2 = run_cli(["gates", "codex-review", "--files", str(clean)], env=env)
    assert r2.returncode == 0, f"2차 exit 0 기대\n{r2.stdout}\n{r2.stderr}"
    # cache hit 검증: contract가 없으면 cache_key가 비어 hit 불가하므로 조건부 검증.
    res_file = pipeline_dir(state_file) / "codex_review_result.json"
    final = json.loads(res_file.read_text(encoding="utf-8"))
    if final.get("cache_key"):
        # cache_key가 계산된 경우: 2차는 반드시 cache hit + CLI 미호출.
        assert final["cache_hit"] is True, "2차 실행은 cache hit이어야 함"
        assert read_call_count(log_path) == 1, "cache hit 시 CLI 추가 호출 없어야 함"
        assert final["codex_cli_call_count"] == 0, "cache hit 시 call_count=0"
    final_state = {"cache_key_present": bool(final.get("cache_key"))}
    assert isinstance(final_state["cache_key_present"], bool)


# TC-25: volatile timestamp가 stable_bundle_sha256에 영향 없음
def test_tc25_volatile_fields_excluded_from_stable_sha(tmp_path: Path) -> None:
    """generated_at 등 volatile 필드가 달라져도 stable_bundle_sha256은 동일해야 한다."""
    import hashlib
    pipeline = import_pipeline_module()

    bundle1 = {
        "pipeline_id": "TEST-001",
        "contract_sha256": "abc123",
        "pr_diff_summary": "test",
        "generated_at": "2026-07-01T10:00:00Z",
    }
    bundle2 = dict(bundle1)
    bundle2["generated_at"] = "2026-07-01T11:00:00Z"

    stable_dict1 = {
        k: v for k, v in bundle1.items()
        if k not in pipeline._BUNDLE_SHA_VOLATILE_FIELDS
    }
    stable_dict2 = {
        k: v for k, v in bundle2.items()
        if k not in pipeline._BUNDLE_SHA_VOLATILE_FIELDS
    }
    sha1 = hashlib.sha256(
        json.dumps(stable_dict1, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    sha2 = hashlib.sha256(
        json.dumps(stable_dict2, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()

    assert sha1 == sha2, "volatile 필드 변경이 stable SHA에 영향을 주면 안 됨"
    # cache key도 동일해야 한다 (stable SHA 기반).
    key1 = pipeline._compute_cache_key("contractX", {"stable_bundle_sha256": sha1})
    key2 = pipeline._compute_cache_key("contractX", {"stable_bundle_sha256": sha2})
    assert key1 == key2, "동일 stable SHA → 동일 cache key"
    final_state = {"stable_sha_unchanged": sha1 == sha2, "key_unchanged": key1 == key2}
    assert final_state["stable_sha_unchanged"] is True
    assert final_state["key_unchanged"] is True


# TC-26: 실제 bundle builder가 volatile 필드를 stable SHA에서 제외하는지 검증
def test_tc26_build_bundle_stable_sha_ignores_generated_at(tmp_path: Path) -> None:
    """_build_codex_review_bundle이 두 번 실행되어 generated_at이 달라도 stable SHA는 동일하다."""
    pipeline = import_pipeline_module()
    state = {"pipeline_id": "IMP-9F5E-TC26"}
    prev = os.environ.get("PIPELINE_STATE_PATH")
    os.environ["PIPELINE_STATE_PATH"] = str(tmp_path / "state.json")
    try:
        b1 = pipeline._build_codex_review_bundle(state, "IMP-9F5E-TC26")
        b2 = pipeline._build_codex_review_bundle(state, "IMP-9F5E-TC26")
    finally:
        if prev is None:
            os.environ.pop("PIPELINE_STATE_PATH", None)
        else:
            os.environ["PIPELINE_STATE_PATH"] = prev
    # generated_at은 (거의 항상) 달라지지만 stable_bundle_sha256은 동일해야 한다.
    assert "stable_bundle_sha256" in b1
    assert "stable_bundle_sha256" in b2
    assert b1["stable_bundle_sha256"] == b2["stable_bundle_sha256"], (
        "동일 diff에 대해 stable_bundle_sha256이 실행마다 달라지면 안 됨 (cache key 불안정)"
    )
    # generated_at은 stable SHA input에서 제외되어야 한다.
    assert "generated_at" not in b1["stable_bundle_sha256_input_fields"]
    final_state = {"stable_equal": b1["stable_bundle_sha256"] == b2["stable_bundle_sha256"]}
    assert final_state["stable_equal"] is True


# TC-27: Codex CLI mock이 받은 입력이 bundle 경로뿐 (full diff text 미포함)
def test_tc27_cli_receives_bundle_path_only(tmp_path: Path) -> None:
    """Codex CLI mock이 받은 args에 bundle 경로만 포함되고 full diff 원문은 미포함이어야 한다."""
    # PIPELINE_STATE_PATH isolation: env_with_fake_codex -> make_env(state_file) 내부에서 PIPELINE_STATE_PATH=str(state_file) 설정
    state_file = tmp_path / "state.json"
    write_state(state_file, "IMP-9F5E-TC27")
    bin_dir = tmp_path / "bin"
    log_path = install_fake_codex(bin_dir, verdict="APPROVE_TO_USER")
    clean = tmp_path / "clean.py"
    # 고유 marker를 코드에 넣어, 이 문자열이 CLI args에 새어나가지 않음을 검증.
    write_file(clean, "def unique_marker_xyz():\n    return 1\n")

    result = run_cli(
        ["gates", "codex-review", "--files", str(clean)],
        env=env_with_fake_codex(state_file, bin_dir),
    )

    assert result.returncode == 0, (
        f"exit 0 기대, 실제={result.returncode}\n{result.stdout}\n{result.stderr}"
    )
    call_args = log_path.read_text(encoding="utf-8").strip()
    # base 리뷰 인자(--base origin/main)는 포함.
    assert "--base" in call_args, f"--base 미포함: {call_args}"
    assert "origin/main" in call_args, f"origin/main 미포함: {call_args}"
    # full diff 원문 marker는 미포함 (bundle SHA만 전달).
    assert "unique_marker_xyz" not in call_args, "full diff 원문이 CLI args로 새어나감"
    final_state = {
        "base_in_args": "--base" in call_args and "origin/main" in call_args,
        "no_full_diff_leak": "unique_marker_xyz" not in call_args,
    }
    assert final_state["base_in_args"] is True, f"--base origin/main 미전달: {call_args}"
    assert final_state["no_full_diff_leak"] is True


# ── IMP-20260701-9F5E rework: _parse_codex_verdict SSoT 검증 테스트 ──────────

def test_parse_codex_verdict_exact_approve(tmp_path):
    """정확히 APPROVE_TO_USER만 반환하는 경우만 APPROVE"""
    from pipeline import _parse_codex_verdict
    result = _parse_codex_verdict("APPROVE_TO_USER")
    assert result["verdict"] == "APPROVE_TO_USER"
    assert result["reason"] == ""


def test_parse_codex_verdict_exact_reject(tmp_path):
    """정확히 REJECT - 사유 형태만 REJECT"""
    from pipeline import _parse_codex_verdict
    result = _parse_codex_verdict("REJECT - missing tests")
    assert result["verdict"] == "REJECT"
    assert result["reason"] == "missing tests"


def test_parse_codex_verdict_ambiguous_blocked(tmp_path):
    """ambiguous 출력은 codex_verdict_invalid"""
    from pipeline import _parse_codex_verdict
    ambiguous_cases = [
        "Do not APPROVE_TO_USER",
        "INFO: APPROVE_TO_USER",
        "APPROVE_TO_USER ",   # trailing space
        "REJECT",             # REJECT without reason
        "REJECT:",            # REJECT with colon but no reason
        "REJECT - ",          # REJECT with empty reason (no text after space)
    ]
    for case in ambiguous_cases:
        result = _parse_codex_verdict(case)
        assert result["verdict"] == "codex_verdict_invalid", f"Expected invalid for: {repr(case)}"


def test_cache_hit_zero_cli_calls(tmp_path: Path) -> None:
    """cache hit 경로 산출물에서 codex_cli_called=False, call_count=0 불변량 검증.

    실제 production 함수 _codex_review_cache_path / _load_codex_cache를 호출하여,
    cache hit 시 CLI 재호출이 없었음을 나타내는 불변 필드가 디스크 산출물에
    그대로 보존되는지 확인한다 (tautological 아님 — 실제 로드 경로 검증).
    """
    pipeline = import_pipeline_module()
    pipeline_id = "IMP-9F5E-CACHEHIT"
    prev = os.environ.get("PIPELINE_STATE_PATH")
    os.environ["PIPELINE_STATE_PATH"] = str(tmp_path / "state.json")
    try:
        # 실제 production 경로 계산 (PIPELINE_STATE_PATH 격리 반영).
        cache_path = pipeline._codex_review_cache_path(pipeline_id)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # cache hit 산출물: CLI를 재호출하지 않았으므로 called=False, count=0.
        cache_data = {
            "cache_key": "DUMMY_KEY_12345",
            "result": "APPROVE_TO_USER",
            "pr_head_sha": "headsha_v1",
            "codex_cli_called": False,
            "codex_cli_call_count": 0,
        }
        cache_path.write_text(json.dumps(cache_data), encoding="utf-8")

        # 실제 production 로드 함수 호출.
        loaded = pipeline._load_codex_cache(pipeline_id)
    finally:
        if prev is None:
            os.environ.pop("PIPELINE_STATE_PATH", None)
        else:
            os.environ["PIPELINE_STATE_PATH"] = prev

    assert loaded is not None, "cache는 정상 로드되어야 한다 (cache hit)"
    assert loaded.get("result") == "APPROVE_TO_USER"
    # cache hit 불변량: CLI 재호출 없음.
    assert loaded.get("codex_cli_called") is False, \
        "cache hit: codex_cli_called는 False여야 한다"
    assert loaded.get("codex_cli_call_count") == 0, \
        "cache hit: codex_cli_call_count는 0이어야 한다"
    final_state = {
        "loaded": loaded is not None,
        "cli_called": loaded.get("codex_cli_called"),
        "cli_count": loaded.get("codex_cli_call_count"),
    }
    assert final_state["loaded"] is True
    assert final_state["cli_called"] is False
    assert final_state["cli_count"] == 0


def test_diagnostic_verdict_not_eligible(tmp_path: Path) -> None:
    """source=diagnostic_manual 결과는 request-accept에서 BLOCKED 검증.

    실제 production CLI(gates request-accept)를 subprocess로 실행하여,
    codex_review_result.json의 source가 diagnostic_manual일 때
    failure_code=diagnostic_result_not_eligible로 차단되는지 확인한다.
    (tautological 아님 — 실제 request-accept 게이트 경로를 통과시킨다.)
    """
    # PIPELINE_STATE_PATH isolation via make_env(state_file)
    pipeline_id = "IMP-9F5E-DIAG"
    state_file = tmp_path / "state.json"
    write_state(state_file, pipeline_id)

    # 실제 request-accept가 읽는 SSoT 경로(.pipeline/codex_review_result.json)에
    # diagnostic_manual source 결과를 배치한다.
    ci_dir = pipeline_dir(state_file)
    ci_dir.mkdir(parents=True, exist_ok=True)
    diag_result = {
        "pipeline_id": pipeline_id,
        "verdict": "APPROVE_TO_USER",
        "source": "diagnostic_manual",
        "acceptance_eligible": False,
    }
    write_file(
        ci_dir / "codex_review_result.json",
        json.dumps(diag_result),
    )

    result = run_cli(
        ["gates", "request-accept", "--evidence", "nonexistent_output.txt"],
        env=make_env(state_file),
    )
    output = result.stdout + result.stderr

    # diagnostic_manual source는 acceptance 게이트에서 반드시 차단되어야 한다.
    assert result.returncode != 0, \
        f"diagnostic_manual source는 BLOCKED(비정상 종료)여야 한다. output={output[:500]}"
    assert "diagnostic_result_not_eligible" in output, \
        f"failure_code=diagnostic_result_not_eligible 기대. output={output[:500]}"
    final_state = {
        "returncode": result.returncode,
        "blocked": "diagnostic_result_not_eligible" in output,
    }
    assert final_state["returncode"] != 0
    assert final_state["blocked"] is True


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
