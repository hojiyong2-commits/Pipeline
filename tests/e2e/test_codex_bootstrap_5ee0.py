"""IMP-20260717-5EE0 Bootstrap: Codex Review 입력 크기 초과 구조적 해결 E2E 테스트 (TC-1~TC-22).

# [Purpose]: Codex Review 입력이 CLI 한도(1,048,576자)를 초과하는 문제를 해결하는 7개 신규 함수
#            (_build_codex_review_plan / _extract_evidence_ast(외 4) / _calculate_shard_budget /
#            _build_shard_plan / _aggregate_shard_verdicts / _invalidate_codex_cache /
#            _check_pr_size_budget / _check_convergence_guard)의 구조/차단 조건을 검증한다.
# [Assumptions]: 신규 함수들이 pipeline.py에 아직 CLI로 노출되지 않았으므로(구조만 구현), 본 태스크
#            지침에 따라 예외적으로 함수를 직접 import하여 테스트한다. PIPELINE_STATE_PATH로 격리하여
#            운영 .pipeline/ / pipeline_state.json 을 오염시키지 않는다.
# [Vulnerability & Risks]: 실제 Codex CLI를 호출하지 않는다. git 의존 함수는 _inject/_metrics seam으로
#            결정적으로 검증한다(프로덕션 우회 불가, 추가 제약만 가능).
# [Improvement]: 실제 subprocess CLI 경로(gates codex-review --...)를 모킹하면 통합 흐름까지 검증 가능.

CLI Evidence Contract:
- 상태 변경 함수는 PIPELINE_STATE_PATH 격리 + final_state(codex_review_plan.json) assertion 포함.
- 본 파이프라인은 신규 함수가 CLI 미노출이라 함수 직접 import를 예외적으로 허용(태스크 지침).
"""

import importlib.util
import json
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ORACLE_DIR = BASE_DIR / "tests" / "oracles" / "IMP-20260717-5EE0"
PID = "IMP-20260717-5EE0"


def _get_pipeline_module():
    """pipeline.py를 동적으로 로드하여 신규 함수를 제공한다."""
    spec = importlib.util.spec_from_file_location("pipeline_5ee0", BASE_DIR / "pipeline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_oracle(case_id):
    """oracle input.json + expected.json 을 읽어 (input, expected) 튜플로 반환한다."""
    case_dir = ORACLE_DIR / case_id
    inp = json.loads((case_dir / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    return inp, exp


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    """PIPELINE_STATE_PATH를 임시 경로로 격리한다(운영 상태 파일 오염 방지)."""
    state_file = tmp_path / "pipeline_state.json"
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
    return state_file


@pytest.fixture(scope="module")
def pipe():
    """pipeline 모듈을 1회 로드한다."""
    return _get_pipeline_module()


# ---------------------------------------------------------------------------
# TC-1: 대형 PR — 전체 diff를 prompt에 넣지 않음
# ---------------------------------------------------------------------------
def test_tc1_no_full_diff_in_prompt(pipe, isolated_state):
    inp, exp = _load_oracle("TC-1")
    big_source = "\n".join(f"def fn_{i}():\n    return {i}" for i in range(2000)) + "\n"
    plan = pipe._build_codex_review_plan(
        PID,
        _inject={
            "changed_files": inp["changed_files"],
            "head_sha": "a" * 40,
            "base_sha": "b" * 40,
            "file_sources": {"pipeline.py": big_source},
        },
    )
    assert "full_diff" not in plan
    assert plan["evidence_mode"] == "ast_extract" == exp["evidence_mode"]
    assert plan["coverage_status"] == "FULL"
    # final_state: plan 파일이 격리 경로에 기록됨
    assert pipe._codex_review_plan_path().exists()
    budgets = pipe._calculate_shard_budget(plan)
    shards = pipe._build_shard_plan(plan, budgets)
    assert len(shards) >= exp["shard_count_min"]
    assert all(s["prompt_chars"] <= pipe.SHARD_TARGET_BUDGET for s in shards)


# ---------------------------------------------------------------------------
# TC-2: 테스트 파일 요약 (전체 원문 금지)
# ---------------------------------------------------------------------------
def test_tc2_test_file_summary(pipe, isolated_state, tmp_path):
    inp, exp = _load_oracle("TC-2")
    tf = tmp_path / "test_large_sample.py"
    body = "import subprocess\nimport pytest\n\n\n@pytest.fixture\ndef fx():\n    return 1\n\n\n"
    body += "\n\n".join(f"def test_case_{i}():\n    assert {i} == {i}" for i in range(300)) + "\n"
    tf.write_text(body, encoding="utf-8")
    ev = pipe._extract_test_evidence_ast(str(tf))
    assert len(ev["test_functions"]) > 0 and exp["contains_test_names"]
    assert len(ev["fixtures"]) > 0 and exp["contains_fixture_sha"]
    assert ev["has_subprocess_calls"] is True
    # 원문 전체가 증거에 실리지 않음: 요약 SHA만 존재, source 원문 키 없음
    assert "source" not in ev
    assert exp["full_file_in_evidence"] is False


# ---------------------------------------------------------------------------
# TC-3: 여러 함수에 동일 라인 — 전역 dedup 없이 각 함수에서 보존
# ---------------------------------------------------------------------------
def test_tc3_no_global_dedup(pipe, tmp_path):
    inp, exp = _load_oracle("TC-3")
    py = tmp_path / "dup.py"
    py.write_text(
        "def alpha():\n    x = 1\n    return x\n\n\ndef beta():\n    y = 2\n    return x\n",
        encoding="utf-8",
    )
    ev = pipe._extract_evidence_ast(str(py))
    names = [e["name"] for e in ev]
    assert "alpha" in names and "beta" in names
    assert len(names) >= exp["min_symbols"]
    # 동일한 "    return x" 라인이 두 함수 source 모두에 보존됨(전역 dedup 없음)
    alpha_src = next(e["source"] for e in ev if e["name"] == "alpha")
    beta_src = next(e["source"] for e in ev if e["name"] == "beta")
    assert "return x" in alpha_src and "return x" in beta_src


# ---------------------------------------------------------------------------
# TC-4: AST boundary 정확성
# ---------------------------------------------------------------------------
def test_tc4_ast_boundary_exact(pipe, tmp_path):
    inp, exp = _load_oracle("TC-4")
    py = tmp_path / "sample.py"
    py.write_text(
        "def alpha():\n    a = 1\n    return a\n\n\nclass Beta:\n    def method(self):\n        return 2\n",
        encoding="utf-8",
    )
    ev = pipe._extract_evidence_ast(str(py))
    alpha = next(e for e in ev if e["name"] == "alpha")
    assert alpha["lineno"] == 1 and alpha["end_lineno"] == 3
    beta = next(e for e in ev if e["name"] == "Beta")
    assert beta["kind"] == "class" and beta["lineno"] == 6 and beta["end_lineno"] == 8
    assert exp["uses_ast"] and exp["no_regex_hunk"]


# ---------------------------------------------------------------------------
# TC-5: unknown binary file — evidence_incomplete BLOCKED (oracle 사용)
# ---------------------------------------------------------------------------
def test_tc5_unknown_binary_blocked(pipe, isolated_state):
    inp, exp = _load_oracle("TC-5")
    with pytest.raises(SystemExit):
        pipe._build_codex_review_plan(
            PID,
            _inject={
                "changed_files": inp["changed_files"],
                "head_sha": "a" * 40,
                "base_sha": "b" * 40,
                "file_sources": {"pipeline.py": "x = 1\n"},
                "unknown_files": [inp["unknown_file"]],
            },
        )
    assert exp["status"] == "BLOCKED" and exp["failure_code"] == "evidence_incomplete"


# ---------------------------------------------------------------------------
# TC-6: critical symbol excluded/missing — BLOCKED (oracle 사용)
# ---------------------------------------------------------------------------
def test_tc6_critical_symbol_missing_blocked(pipe, isolated_state, capsys):
    inp, exp = _load_oracle("TC-6")
    with pytest.raises(SystemExit):
        pipe._build_codex_review_plan(
            PID,
            _inject={
                "changed_files": inp["changed_files"],
                "head_sha": "a" * 40,
                "base_sha": "b" * 40,
                "file_sources": {"pipeline.py": "x = 1\n"},
                "changed_symbols": inp["critical_symbols"],
                "covered_symbols": inp["evidence_covers_symbols"],
            },
        )
    err = capsys.readouterr().err
    assert inp["missing_symbol"] in err
    assert exp["failure_code"] == "critical_symbol_missing"


# ---------------------------------------------------------------------------
# TC-7: shard budget — 모든 shard hard budget 이하
# ---------------------------------------------------------------------------
def test_tc7_shard_budget_under_hard(pipe):
    inp, exp = _load_oracle("TC-7")
    budgets = pipe._calculate_shard_budget({"included_items": []})
    assert set(budgets.keys()) == set(pipe._DEFAULT_SHARD_IDS)
    for sid, b in budgets.items():
        assert b["budget"] <= exp["hard_budget"] == pipe.SHARD_HARD_BUDGET
        assert b["budget"] <= pipe.SHARD_TARGET_BUDGET


# ---------------------------------------------------------------------------
# TC-8: no arbitrary truncation — 항목 분할 없음
# ---------------------------------------------------------------------------
def test_tc8_no_arbitrary_truncation(pipe):
    _inp, exp = _load_oracle("TC-8")
    included = [
        {"id": f"f{i}.py", "file": f"f{i}.py", "evidence_mode": "full_ast", "char_count": 40_000}
        for i in range(6)
    ]
    plan = {"pr_head_sha": "h", "contract_sha256": "c", "included_items": included}
    budgets = pipe._calculate_shard_budget(plan)
    shards = pipe._build_shard_plan(plan, budgets)
    all_ids = [eid for s in shards for eid in s["included_evidence_ids"]]
    # 항목 손실/중복 없음: 모든 item id가 정확히 한 번씩 존재
    assert sorted(all_ids) == sorted(it["id"] for it in included)
    assert len(all_ids) == len(set(all_ids))
    assert exp["items_not_split"]


# ---------------------------------------------------------------------------
# TC-9: 일부 shard만 APPROVE — aggregate APPROVED 금지 (oracle 사용)
# ---------------------------------------------------------------------------
def test_tc9_aggregate_not_all_approved(pipe):
    inp, exp = _load_oracle("TC-9")
    shard_results = [
        {"shard_id": s["shard_id"], "verdict": s["verdict"], "pr_head_sha": "same", "findings": []}
        for s in inp["shards"]
    ]
    agg = pipe._aggregate_shard_verdicts(shard_results)
    assert agg["verdict"] != "APPROVED"
    assert exp["aggregate_approved"] is False


# ---------------------------------------------------------------------------
# TC-10: stale shard — head_sha 불일치 시 비승인
# ---------------------------------------------------------------------------
def test_tc10_stale_shard_blocked(pipe):
    inp, exp = _load_oracle("TC-10")
    shard_results = [
        {"shard_id": s["shard_id"], "verdict": s["verdict"], "pr_head_sha": s["pr_head_sha"], "findings": []}
        for s in inp["shards"]
    ]
    agg = pipe._aggregate_shard_verdicts(shard_results)
    assert agg["stale_shards"] > 0
    assert agg["verdict"] == exp["verdict"] == "ERROR"


# ---------------------------------------------------------------------------
# TC-11: head 변경 시 캐시 무효화 (oracle 사용)
# ---------------------------------------------------------------------------
def test_tc11_head_change_cache_miss(pipe, isolated_state):
    inp, exp = _load_oracle("TC-11")
    cache = pipe._codex_review_plan_path().parent / "codex_review_cache.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{}", encoding="utf-8")
    r = pipe._invalidate_codex_cache(inp["trigger_reason"], PID)
    assert r["invalidated"] is True == exp["invalidated"]
    assert not cache.exists()


# ---------------------------------------------------------------------------
# TC-12: contract/policy 변경 시 캐시 무효화
# ---------------------------------------------------------------------------
def test_tc12_contract_change_cache_miss(pipe, isolated_state):
    inp, exp = _load_oracle("TC-12")
    cache = pipe._codex_review_plan_path().parent / "codex_review_cache.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{}", encoding="utf-8")
    r = pipe._invalidate_codex_cache(inp["trigger_reason"], PID)
    assert r["invalidated"] is True == exp["invalidated"]


# ---------------------------------------------------------------------------
# TC-13: unchanged shard — 캐시 재사용(무효화 안 함)
# ---------------------------------------------------------------------------
def test_tc13_unchanged_cache_reuse(pipe, isolated_state):
    inp, exp = _load_oracle("TC-13")
    cache = pipe._codex_review_plan_path().parent / "codex_review_cache.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{}", encoding="utf-8")
    r = pipe._invalidate_codex_cache(inp["trigger_reason"], PID)
    assert r["invalidated"] is False == exp["invalidated"]
    assert cache.exists() == exp["cache_preserved"]


# ---------------------------------------------------------------------------
# TC-14: plan coverage 100%
# ---------------------------------------------------------------------------
def test_tc14_plan_coverage_full(pipe, isolated_state):
    inp, exp = _load_oracle("TC-14")
    plan = pipe._build_codex_review_plan(
        PID,
        _inject={
            "changed_files": inp["changed_files"],
            "head_sha": "a" * 40,
            "base_sha": "b" * 40,
            "file_sources": {
                "pipeline.py": "def a():\n    return 1\n",
                "tests/e2e/test_x.py": "def test_a():\n    assert 1\n",
            },
        },
    )
    assert plan["coverage_status"] == exp["coverage_status"] == "FULL"
    covered = set(plan["risk_classification"].keys())
    assert covered == set(inp["changed_files"])


# ---------------------------------------------------------------------------
# TC-15: preflight blocks CLI — pr_split_required 시 CLI 미호출
# ---------------------------------------------------------------------------
def test_tc15_preflight_blocks_cli(pipe):
    inp, exp = _load_oracle("TC-15")
    r = pipe._check_pr_size_budget(PID, _metrics=inp["metrics"])
    assert r["pr_split_required"] is True == exp["pr_split_required"]
    assert r["blocked"] is True  # blocked=True → CLI 진입 전 차단 신호


# ---------------------------------------------------------------------------
# TC-16: run permit single use — REJECT 2회 후 자동 재호출 차단
# ---------------------------------------------------------------------------
def test_tc16_run_permit_single_use(pipe, isolated_state):
    inp, exp = _load_oracle("TC-16")
    rp = pipe._codex_review_result_path()
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps({"epoch": inp["epoch"], "reject_count": inp["reject_count"]}), encoding="utf-8")
    cg = pipe._check_convergence_guard(PID)
    assert cg["convergence_ok"] is False == exp["convergence_ok"]
    assert cg["blocked_reason"] == exp["blocked_reason"]


# ---------------------------------------------------------------------------
# TC-17: invalid findings schema — BLOCKED 처리
# ---------------------------------------------------------------------------
def test_tc17_invalid_findings_schema(pipe):
    inp, exp = _load_oracle("TC-17")
    agg = pipe._aggregate_shard_verdicts(inp["shards"])
    assert agg["verdict"] == exp["verdict"] == "ERROR"
    assert agg["error_shards"] > 0


# ---------------------------------------------------------------------------
# TC-18: raw ACCEPT not in bundle — excluded_items 포함
# ---------------------------------------------------------------------------
def test_tc18_raw_accept_excluded(pipe, isolated_state):
    inp, exp = _load_oracle("TC-18")
    plan = pipe._build_codex_review_plan(
        PID,
        _inject={
            "changed_files": ["pipeline.py"],
            "head_sha": "a" * 40,
            "base_sha": "b" * 40,
            "file_sources": {"pipeline.py": "x = 1\n"},
            "raw_accept_values": [inp["raw_accept"]],
        },
    )
    excluded = [e for e in plan["excluded_items"] if e["reason"] == exp["reason"]]
    assert excluded and excluded[0]["item"] == inp["raw_accept"]
    assert exp["raw_accept_excluded"]


# ---------------------------------------------------------------------------
# TC-19: existing verifications intact — packet/head 검증 유지
# ---------------------------------------------------------------------------
def test_tc19_existing_verifications_intact(pipe, isolated_state):
    _inp, exp = _load_oracle("TC-19")
    plan = pipe._build_codex_review_plan(
        PID,
        _inject={
            "changed_files": ["pipeline.py"],
            "head_sha": "c" * 40,
            "base_sha": "d" * 40,
            "file_sources": {"pipeline.py": "x = 1\n"},
        },
    )
    assert plan["pr_head_sha"] == "c" * 40 and exp["pr_head_sha_present"]
    assert "contract_sha256" in plan and exp["contract_sha_field_present"]


# ---------------------------------------------------------------------------
# TC-20: PR891 scale — 여러 bounded shard 생성
# ---------------------------------------------------------------------------
def test_tc20_pr891_scale_multiple_shards(pipe):
    inp, exp = _load_oracle("TC-20")
    included = [
        {"id": f"core_{i}.py", "file": f"core_{i}.py", "evidence_mode": "full_ast",
         "char_count": inp["per_item_chars"]}
        for i in range(inp["item_count"])
    ]
    plan = {"pr_head_sha": "h", "contract_sha256": "c", "included_items": included}
    budgets = pipe._calculate_shard_budget(plan)
    shards = pipe._build_shard_plan(plan, budgets)
    assert len(shards) > 1 and exp["multiple_shards"]
    assert all(s["prompt_chars"] <= exp["target_budget"] for s in shards)


# ---------------------------------------------------------------------------
# TC-21: 대량 코드+테스트 — pr_split_required (oracle 사용)
# ---------------------------------------------------------------------------
def test_tc21_large_code_and_tests_split(pipe):
    inp, exp = _load_oracle("TC-21")
    r = pipe._check_pr_size_budget(
        PID,
        _metrics={
            "changed_product_lines": inp["product_code_lines"],
            "max_test_file_lines": inp["test_file_lines"],
            "critical_symbol_count": inp["critical_symbols_count"],
        },
    )
    assert r["pr_split_required"] is True
    assert set(exp["split_reasons"]).issubset(set(r["reasons"]))


# ---------------------------------------------------------------------------
# TC-22: self-referential — review engine + 대형 기능 변경 감지 (oracle 사용)
# ---------------------------------------------------------------------------
def test_tc22_self_referential_split(pipe):
    inp, exp = _load_oracle("TC-22")
    r = pipe._check_pr_size_budget(
        PID,
        _metrics={
            "changed_symbols": inp["changed_symbols"],
            "review_engine_changed": inp["review_engine_changed"],
            "large_feature_lines": inp["model_router_lines_changed"],
        },
    )
    assert r["self_referential"] is True == inp["self_referential"]
    assert "self_referential" in r["reasons"]
    assert exp["failure_code"] == "pr_split_required"
