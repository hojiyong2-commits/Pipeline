"""IMP-20260710-DB54 rework MT-TC: Codex Review 비용 최적화 제어 흐름 E2E 테스트 (TC-A~TC-J).

# [Purpose]: 단위 함수 테스트만으로는 검증되지 않는 `gates codex-review` / `gates codex-preflight`
#            실제 CLI 제어 흐름(preflight fail-closed, cache hit 재사용, live SHA 재검증, bundle
#            필수 필드, oracle gate 상태, critical file SHA, 비용 증거 필드, 원문/nonce 제외)을
#            subprocess 기반으로 검증한다.
# [Assumptions]: PIPELINE_STATE_PATH로 격리된 임시 state + 가짜 gh(빈 응답)를 PIPELINE_GH_EXECUTABLE로
#            주입한다. bundle의 git diff는 실제 저장소에서 실행되므로 pipeline.py/oracle 파일이
#            changed_files에 포함된다(critical file 판정 자연 발생).
# [Vulnerability & Risks]: gh 유무에 따라 pr_head_sha가 달라질 수 있어 가짜 gh로 빈 값을 강제한다.
#            테스트 전용 block-only seam(PIPELINE_CODEX_TEST_*)은 프로덕션 우회에 사용할 수 없다
#            (필드 누락/무력화로 추가 BLOCKED만 유발).
# [Improvement]: 실제 codex CLI 모킹을 추가하면 CLI 실행 verdict 경로까지 완전 격리 가능.

CLI Evidence Contract:
- 상태 변경 CLI 호출은 PIPELINE_STATE_PATH 격리 + final_state(codex_review_result.json / bundle) assertion 포함.
- subprocess 기반 실제 CLI 실행 (내부 함수 직접 임포트 금지).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"
PID = "IMP-20260710-DB54"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_fake_gh(target_dir: Path) -> Path:
    """모든 조회에 빈/최소 응답을 주는 가짜 gh 스크립트를 만들고 경로를 반환한다."""
    script = target_dir / "fake_gh_db54.py"
    script.write_text(
        "import sys, io, json\n"
        'sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")\n'
        "args = sys.argv[1:]\n"
        'if "--jq" in args:\n'
        '    print(""); sys.exit(0)\n'
        'if "run" in args and ("list" in args or "view" in args):\n'
        '    print("[]"); sys.exit(0)\n'
        'if "pr" in args and "list" in args:\n'
        '    print("[]"); sys.exit(0)\n'
        'print(json.dumps({"body": "", "number": 1, "headRefOid": "",\n'
        '    "isDraft": False, "state": "OPEN", "files": [],\n'
        '    "url": "https://github.com/test/repo/pull/1"}))\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    return script


def make_env(state_file: Path, **extra: str) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 + 가짜 gh 주입 env 생성."""
    if state_file is None:
        raise TypeError("state_file must not be None")
    gh_dir = state_file.parent / "_gh_shim"
    gh_dir.mkdir(parents=True, exist_ok=True)
    fake_gh = _write_fake_gh(gh_dir)
    env = {
        **os.environ,
        "PIPELINE_STATE_PATH": str(state_file),
        "PIPELINE_NO_DASHBOARD": "1",
        "PIPELINE_GH_EXECUTABLE": str(fake_gh),
        "PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    env.update(extra)
    return env


def setup_workspace(tmp_path: Path, gates_ok: bool = True) -> Path:
    """격리 state + packet(.md/.json)을 tmp_path에 생성하고 state 파일 경로를 반환한다."""
    state_file = tmp_path / "state.json"
    (tmp_path / ".pipeline").mkdir(parents=True, exist_ok=True)
    if gates_ok:
        eg = {
            "technical": {"status": "PASS"},
            "oracle": {"status": "PASS"},
            "github_ci": {"status": "PASS"},
        }
    else:
        eg = {
            "technical": {"status": "FAIL"},
            "oracle": {"status": "PASS"},
            "github_ci": {"status": "PASS"},
        }
    state = {
        "version": "1.2.0",
        "pipeline_id": PID,
        "type": "IMP",
        "description": "codex control-flow e2e",
        "current_phase": "harness",
        "terminal_state": None,
        "event_log": [],
        "external_gates": eg,
        "contract": {"frozen": True},
        "codex_review_loop_state": {"reject_count": 0},
    }
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    (tmp_path / "human_acceptance_packet.md").write_text("packet md content", encoding="utf-8")
    (tmp_path / "human_acceptance_packet.json").write_text(
        json.dumps({"schema_version": 1}), encoding="utf-8"
    )
    return state_file


def run_cli(args: List[str], env: Dict[str, str], cwd: Path,
            timeout: int = 120) -> subprocess.CompletedProcess:
    """`python pipeline.py <args>` 실행."""
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    return subprocess.run(
        [sys.executable, str(PIPELINE_PY)] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env, cwd=str(cwd),
    )


def result_path(state_file: Path) -> Path:
    return state_file.resolve().parent / ".pipeline" / "codex_review_result.json"


def bundle_path(state_file: Path) -> Path:
    return state_file.resolve().parent / ".pipeline" / "codex_review_bundle.json"


def load_result(state_file: Path) -> Dict[str, Any]:
    return json.loads(result_path(state_file).read_text(encoding="utf-8"))


def _combined(r: subprocess.CompletedProcess) -> str:
    return (r.stdout or "") + "\n" + (r.stderr or "")


# ---------------------------------------------------------------------------
# TC-A: preflight 실패 → 즉시 BLOCKED, result.json 미기록 (CLI 미실행)
# ---------------------------------------------------------------------------

def test_tc_a_preflight_fail_blocks_before_cli(tmp_path: Path) -> None:
    state_file = setup_workspace(tmp_path, gates_ok=False)  # technical FAIL
    env = make_env(state_file)
    r = run_cli(["gates", "codex-review", "--verdict", "APPROVE_TO_USER"], env, tmp_path)
    assert r.returncode != 0, f"preflight 실패는 BLOCKED여야 함\n{_combined(r)}"
    assert "codex_preflight_failed" in _combined(r)
    # final_state: 결과 파일이 기록되지 않았다(리뷰가 진행되지 않음).
    assert not result_path(state_file).exists(), "preflight 차단 시 result.json이 없어야 함"


# ---------------------------------------------------------------------------
# TC-B: cache hit → cached verdict 재사용, CLI 미호출
# ---------------------------------------------------------------------------

def test_tc_b_cache_hit_reuses_verdict(tmp_path: Path) -> None:
    state_file = setup_workspace(tmp_path)
    env = make_env(state_file)
    # run1: 명시적 verdict로 fresh review → 캐시 채움.
    r1 = run_cli(["gates", "codex-review", "--verdict", "APPROVE_TO_USER"], env, tmp_path)
    assert r1.returncode == 0, f"run1 실패\n{_combined(r1)}"
    res1 = load_result(state_file)
    assert res1["cache_hit"] is False
    # run2: verdict 없이 실행 → cache hit 재사용.
    r2 = run_cli(["gates", "codex-review"], env, tmp_path)
    assert r2.returncode == 0, f"run2(cache hit) 실패\n{_combined(r2)}"
    res2 = load_result(state_file)
    assert res2["cache_hit"] is True, "run2는 cache hit이어야 함"
    assert res2["status"] == "APPROVED"
    assert res2["verdict"] == "APPROVE_TO_USER"
    assert res2["codex_cli_command"] == "N/A (cache hit)", "cache hit 시 CLI 미호출 명시"


# ---------------------------------------------------------------------------
# TC-C: cache hit이어도 live SHA 재검증 실패 시 BLOCKED
# ---------------------------------------------------------------------------

def test_tc_c_cache_hit_live_sha_mismatch_blocks(tmp_path: Path) -> None:
    state_file = setup_workspace(tmp_path)
    env = make_env(state_file)
    r1 = run_cli(["gates", "codex-review", "--verdict", "APPROVE_TO_USER"], env, tmp_path)
    assert r1.returncode == 0, f"run1 실패\n{_combined(r1)}"
    # verification json(human_acceptance_packet.json)을 변조 → bundle SHA는 그대로,
    # 그러나 live SHA(verification_json_sha256)가 캐시 시점과 달라진다.
    (tmp_path / "human_acceptance_packet.json").write_text(
        json.dumps({"schema_version": 999, "changed": True}), encoding="utf-8"
    )
    r2 = run_cli(["gates", "codex-review"], env, tmp_path)
    assert r2.returncode != 0, f"live SHA 불일치는 BLOCKED여야 함\n{_combined(r2)}"
    assert "cache_live_sha_mismatch" in _combined(r2)
    # final_state: result.json은 여전히 run1의 fresh(cache_hit=false) 결과여야 한다(덮어쓰기 없음).
    res = load_result(state_file)
    assert res["cache_hit"] is False


# ---------------------------------------------------------------------------
# TC-D: oracle gate 상태(state SSoT)가 bundle에 PASS로 전파된다 (UNKNOWN 버그 회귀 방지)
# ---------------------------------------------------------------------------

def test_tc_d_oracle_gate_status_pass_in_bundle(tmp_path: Path) -> None:
    state_file = setup_workspace(tmp_path)  # external_gates.oracle.status = PASS
    env = make_env(state_file)
    # preflight --dry-run은 bundle을 materialize하고 die하지 않는다.
    r = run_cli(["gates", "codex-preflight", "--dry-run"], env, tmp_path)
    assert bundle_path(state_file).exists(), f"bundle이 생성돼야 함\n{_combined(r)}"
    bundle = json.loads(bundle_path(state_file).read_text(encoding="utf-8"))
    assert bundle["oracle_gate_status"] == "PASS"
    assert bundle["oracle_gate_status"] != "UNKNOWN", "oracle이 UNKNOWN이면 기존 버그 회귀"
    assert bundle["technical_gate_status"] == "PASS"


# ---------------------------------------------------------------------------
# TC-E: bundle 필수 필드 누락 시 gates codex-review BLOCKED
# ---------------------------------------------------------------------------

def test_tc_e_missing_required_field_blocks(tmp_path: Path) -> None:
    state_file = setup_workspace(tmp_path)
    env = make_env(state_file, PIPELINE_CODEX_TEST_DROP_BUNDLE_FIELD="included_functions")
    r = run_cli(["gates", "codex-review", "--verdict", "APPROVE_TO_USER"], env, tmp_path)
    assert r.returncode != 0, f"필수 필드 누락은 BLOCKED여야 함\n{_combined(r)}"
    assert "codex_bundle_missing_required_field" in _combined(r)
    assert "included_functions" in _combined(r)
    assert not result_path(state_file).exists(), "필드 누락 차단 시 result.json 없어야 함"


# ---------------------------------------------------------------------------
# TC-F: critical file 변경 + bundle critical_file_shas 없음 → cache miss
# ---------------------------------------------------------------------------

def test_tc_f_critical_changed_no_shas_cache_miss(tmp_path: Path) -> None:
    state_file = setup_workspace(tmp_path)
    env = make_env(state_file, PIPELINE_CODEX_TEST_EMPTY_CRITICAL_SHAS="1")
    # run1: 캐시 채움(critical_file_shas 비어 있지만 changed_critical_files 존재).
    r1 = run_cli(["gates", "codex-review", "--verdict", "APPROVE_TO_USER"], env, tmp_path)
    assert r1.returncode == 0, f"run1 실패\n{_combined(r1)}"
    # run2: verdict 없이 → cache miss(critical 변경인데 shas 없음) → 재사용 근거 없음 BLOCKED.
    r2 = run_cli(["gates", "codex-review"], env, tmp_path)
    assert r2.returncode != 0, f"cache miss 후 재사용 불가는 BLOCKED여야 함\n{_combined(r2)}"
    out = _combined(r2)
    assert "codex_verdict_required" in out
    assert "critical" in out, "cache miss 사유에 critical_file_shas 부재가 표시돼야 함"
    # final_state: run1 결과 유지(cache_hit=false).
    assert load_result(state_file)["cache_hit"] is False


# ---------------------------------------------------------------------------
# TC-G: excluded_files에 critical file 있으면 cache 사용 금지 BLOCKED
# ---------------------------------------------------------------------------

def test_tc_g_excluded_critical_blocks_cache(tmp_path: Path) -> None:
    state_file = setup_workspace(tmp_path)
    env = make_env(state_file, PIPELINE_CODEX_TEST_EXCLUDE_CRITICAL="1")
    # excluded_files에 critical(pipeline.py)이 있으면 캐시 신뢰 불가 → fail-closed BLOCKED.
    # 명시적 --verdict 여부와 무관하게 차단한다(불완전한 리뷰 컨텍스트 방지).
    r = run_cli(["gates", "codex-review", "--verdict", "APPROVE_TO_USER"], env, tmp_path)
    assert r.returncode != 0, f"excluded critical은 BLOCKED여야 함\n{_combined(r)}"
    assert "cache_excluded_critical_file" in _combined(r)
    assert not result_path(state_file).exists(), "excluded critical 차단 시 result.json 없어야 함"


# ---------------------------------------------------------------------------
# TC-H: codex_review_result.json에 비용 최적화 증거 필드가 기록된다
# ---------------------------------------------------------------------------

def test_tc_h_result_has_cost_evidence_fields(tmp_path: Path) -> None:
    state_file = setup_workspace(tmp_path)
    env = make_env(state_file)
    r = run_cli(["gates", "codex-review", "--verdict", "APPROVE_TO_USER"], env, tmp_path)
    assert r.returncode == 0, f"fresh review 실패\n{_combined(r)}"
    res = load_result(state_file)
    for field in (
        "cache_hit", "cache_key", "cache_reason", "review_bundle_sha256",
        "contract_sha256", "verification_json_sha256", "included_functions",
        "excluded_files", "codex_cli_command", "codex_cli_version",
        "codex_model", "model_source",
    ):
        assert field in res, f"result.json 누락 필드: {field}"
    # 모델/CLI 버전을 확인할 수 없으면 gpt-5.5 등 특정값을 허위로 기록하지 않는다.
    assert res["codex_model"] == "unknown"
    assert res["model_source"] == "unknown"
    assert res["codex_cli_version"] == "unknown"
    assert isinstance(res["included_functions"], list)
    assert isinstance(res["excluded_files"], list)


# ---------------------------------------------------------------------------
# TC-I: full PR diff 원문이 bundle에 직접 포함되지 않는다
# ---------------------------------------------------------------------------

def test_tc_i_full_diff_not_in_bundle(tmp_path: Path) -> None:
    state_file = setup_workspace(tmp_path)
    env = make_env(state_file)
    run_cli(["gates", "codex-preflight", "--dry-run"], env, tmp_path)
    assert bundle_path(state_file).exists()
    raw = bundle_path(state_file).read_text(encoding="utf-8")
    # diff hunk 마커가 bundle 어디에도 없어야 한다(개수/SHA/식별자만 포함).
    assert "@@ " not in raw, "bundle에 diff hunk 원문이 포함되면 안 됨"
    assert "diff --git" not in raw, "bundle에 raw git diff가 포함되면 안 됨"


# ---------------------------------------------------------------------------
# TC-J: raw ACCEPT 코드/nonce가 bundle 어디에도 포함되지 않는다
# ---------------------------------------------------------------------------

def test_tc_j_no_accept_code_or_nonce_in_bundle(tmp_path: Path) -> None:
    import re
    state_file = setup_workspace(tmp_path)
    env = make_env(state_file)
    run_cli(["gates", "codex-preflight", "--dry-run"], env, tmp_path)
    assert bundle_path(state_file).exists()
    raw = bundle_path(state_file).read_text(encoding="utf-8")
    assert "ACCEPT-" not in raw, "bundle에 raw ACCEPT 코드가 포함되면 안 됨"
    # 8자 base32(A-Z2-7) + 알파벳 포함 nonce 패턴이 없어야 한다.
    for m in re.findall(r"(?<![A-Za-z0-9])[A-Z2-7]{8}(?![A-Za-z0-9=])", raw):
        assert not any(c.isalpha() for c in m), f"bundle에 nonce 유사 토큰 포함: {m}"


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
