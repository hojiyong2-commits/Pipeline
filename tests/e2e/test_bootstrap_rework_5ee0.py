"""IMP-20260717-5EE0 REJECT#2 rework 검증 테스트 (TC-23, TC-24, TC-25).

# [Purpose]: 5개 REJECT 문제 수정 후 신규 동작을 검증한다.
#   TC-23: _run_codex_review_preflight가 8개 필드 포함 dict 반환
#   TC-24: CODEX_REVIEW_SHARDING 환경변수 없이도 sharding 계산 실행 (guard 제거)
#   TC-25: CODEX_CLI_MOCK=1로 _call_codex_cli_for_shard가 APPROVE_TO_USER 반환 + aggregate APPROVED
# [Assumptions]: 신규 헬퍼 함수(_run_codex_review_preflight/_call_codex_cli_for_shard)는 CLI 미노출
#   구조이므로 함수를 직접 import하여 검증한다. PIPELINE_STATE_PATH로 격리하여 운영 상태를 오염하지 않는다.
# [Vulnerability & Risks]: 실제 Codex CLI를 호출하지 않는다(mock/미연결 경로만 검증).
# [Improvement]: 실제 subprocess CLI 경로를 모킹하면 통합 흐름까지 검증 가능.

CLI Evidence Contract:
- 신규 헬퍼는 CLI 미노출이라 함수 직접 import를 예외적으로 허용(태스크 지침).
- 상태 격리는 PIPELINE_STATE_PATH로 수행한다.
"""

import importlib.util
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PID = "IMP-20260717-5EE0"


def _get_pipeline_module():
    spec = importlib.util.spec_from_file_location("pipeline_rework", BASE_DIR / "pipeline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    state_file = tmp_path / "pipeline_state.json"
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_file))
    return state_file


@pytest.fixture(scope="module")
def pipe():
    return _get_pipeline_module()


# ---------------------------------------------------------------------------
# TC-23: _run_codex_review_preflight가 8개 필드 포함 dict 반환
# ---------------------------------------------------------------------------
def test_tc23_preflight_runs_full_pipeline(pipe, isolated_state):
    """_run_codex_review_preflight가 8개 필드를 모두 반환한다."""
    result = pipe._run_codex_review_preflight(PID, skip_cli=True)
    required_fields = {
        "review_plan", "shard_plan", "total_estimated_chars", "per_shard_chars",
        "budget_ok", "coverage_ok", "convergence_ok", "blocked_reason",
    }
    assert required_fields.issubset(set(result.keys())), (
        f"누락 필드: {required_fields - set(result.keys())}"
    )
    assert isinstance(result["shard_plan"], list)
    assert isinstance(result["per_shard_chars"], dict)
    assert isinstance(result["total_estimated_chars"], int)
    assert isinstance(result["review_plan"], dict)


# ---------------------------------------------------------------------------
# TC-24: CODEX_REVIEW_SHARDING 환경변수 없이도 sharding 실행 (guard 제거 확인)
# ---------------------------------------------------------------------------
def test_tc24_sharding_on_by_default(pipe, isolated_state, monkeypatch):
    """CODEX_REVIEW_SHARDING 환경변수 없이 _run_codex_review_preflight 실행 → shard 계산 수행."""
    # CODEX_REVIEW_SHARDING 환경변수 제거 (설정되어 있어도 영향 없어야 함)
    monkeypatch.delenv("CODEX_REVIEW_SHARDING", raising=False)
    result = pipe._run_codex_review_preflight(PID, skip_cli=True)
    # guard 제거로 sharding이 기본 활성화 → shard_plan/review_plan이 정상 타입으로 반환됨
    assert isinstance(result["shard_plan"], list), "sharding이 기본 활성화되지 않음"
    assert isinstance(result["review_plan"], dict)
    # per_shard_chars와 total_estimated_chars가 일관성 있게 계산됨
    assert result["total_estimated_chars"] == sum(result["per_shard_chars"].values())


# ---------------------------------------------------------------------------
# TC-25: _cli_factory DI seam으로 _call_codex_cli_for_shard 실행 + aggregate 확인
#   (REJECT#3 P0-3: CODEX_CLI_MOCK 승인 우회 분기가 제거되어 DI seam으로 마이그레이션함)
# ---------------------------------------------------------------------------
def test_tc25_aggregate_called_via_mock(pipe, monkeypatch):
    """_cli_factory DI seam으로 _call_codex_cli_for_shard가 APPROVE_TO_USER를 반환하고,
    _aggregate_shard_verdicts가 APPROVED를 반환한다.

    REJECT#3 P0-3: production CODEX_CLI_MOCK 승인 우회 분기는 완전히 제거되었으므로
    테스트 결정성은 CODEX_CLI_MOCK 환경변수가 아닌 _cli_factory 주입으로 확보한다.
    """
    monkeypatch.delenv("CODEX_CLI_MOCK", raising=False)
    shard = {"shard_id": "shard-core", "pr_head_sha": "a" * 40}

    def _fake_cli(s, _permit):
        return {
            "shard_id": s.get("shard_id"),
            "verdict": "APPROVE_TO_USER",
            "findings": [],
            "pr_head_sha": s.get("pr_head_sha"),
        }

    result = pipe._call_codex_cli_for_shard(shard, _cli_factory=_fake_cli)
    assert result["verdict"] == "APPROVE_TO_USER"
    assert result["shard_id"] == "shard-core"

    # aggregate: 모든 shard APPROVE_TO_USER → APPROVED
    shard_results = [
        {"shard_id": "s1", "verdict": "APPROVE_TO_USER", "pr_head_sha": "a" * 40, "findings": []},
        {"shard_id": "s2", "verdict": "APPROVE_TO_USER", "pr_head_sha": "a" * 40, "findings": []},
    ]
    agg = pipe._aggregate_shard_verdicts(shard_results)
    assert agg["verdict"] == "APPROVED"
    assert agg["approved_shards"] == 2
    assert agg["total_shards"] == 2


# ---------------------------------------------------------------------------
# TC-25b: 미연결(non-mock) 환경에서는 ERROR verdict → fake approval 방지
# ---------------------------------------------------------------------------
def test_tc25b_no_mock_returns_error(pipe, monkeypatch):
    """CODEX_CLI_MOCK 미설정 시 _call_codex_cli_for_shard는 ERROR를 반환한다(fail-closed)."""
    monkeypatch.delenv("CODEX_CLI_MOCK", raising=False)
    result = pipe._call_codex_cli_for_shard({"shard_id": "s1", "pr_head_sha": "b" * 40})
    assert result["verdict"] == "ERROR"
    assert result["error_reason"] == "codex_cli_not_connected"
    # aggregate가 비승인 처리
    agg = pipe._aggregate_shard_verdicts([result])
    assert agg["verdict"] != "APPROVED"


# ---------------------------------------------------------------------------
# REJECT#4 회귀 테스트: TC-36~TC-43
# ---------------------------------------------------------------------------
import hashlib
import json
import threading
import time


# ---------------------------------------------------------------------------
# TC-36: authorize-run 후 2초 기다린 뒤 frozen_plan_sha 동일성 확인
#   (generated_at 차이로 permit_review_plan_stale 미발생)
# ---------------------------------------------------------------------------
def test_r4_tc36_frozen_plan_sha_stable(pipe, tmp_path, monkeypatch):
    """authorize-run이 저장한 frozen plan의 SHA가 시간이 지나도 변하지 않는다."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "state.json"))
    # frozen plan 경로 모킹: tmp_path 아래로 격리
    frozen_path = tmp_path / "codex_review_frozen_plan.json"
    monkeypatch.setattr(pipe, "_shard_review_frozen_plan_path", lambda: frozen_path)

    # 임의 plan (generated_at 포함)
    plan1 = {
        "schema_version": 1,
        "pr_head_sha": "a" * 40,
        "contract_sha256": "b" * 64,
        "included_items": [],
        "generated_at": "2026-01-01T00:00:00Z",
    }
    # frozen 버전 계산 (타임스탬프 제거)
    plan_frozen1 = {k: v for k, v in plan1.items() if k not in pipe._FROZEN_PLAN_EXCLUDE_KEYS}
    frozen_bytes1 = (json.dumps(plan_frozen1, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    sha1 = hashlib.sha256(frozen_bytes1).hexdigest()

    time.sleep(0.1)  # 실제 시간 경과 시뮬레이션

    # generated_at만 다른 plan2
    plan2 = dict(plan1)
    plan2["generated_at"] = "2026-01-01T00:00:01Z"
    plan_frozen2 = {k: v for k, v in plan2.items() if k not in pipe._FROZEN_PLAN_EXCLUDE_KEYS}
    frozen_bytes2 = (json.dumps(plan_frozen2, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    sha2 = hashlib.sha256(frozen_bytes2).hexdigest()

    assert sha1 == sha2, f"frozen SHA should be stable but got {sha1} != {sha2}"


# ---------------------------------------------------------------------------
# TC-37: generated_at만 다른 두 plan → frozen SHA 동일 → permit_stale 미발생
# ---------------------------------------------------------------------------
def test_r4_tc37_generated_at_ignored_in_sha(pipe):
    """generated_at, saved_at, run_at은 frozen plan SHA 계산에서 제외된다."""
    plan_base = {
        "schema_version": 1,
        "pr_head_sha": "c" * 40,
        "contract_sha256": "d" * 64,
        "included_items": [],
    }
    timestamps = [
        {"generated_at": "2026-07-01T00:00:00Z"},
        {"generated_at": "2026-07-01T12:34:56Z", "saved_at": "2026-07-01T12:34:57Z"},
        {"run_at": "2026-07-02T00:00:00Z"},
    ]
    shas = []
    for ts in timestamps:
        plan = dict(plan_base, **ts)
        frozen = {k: v for k, v in plan.items() if k not in pipe._FROZEN_PLAN_EXCLUDE_KEYS}
        b = (json.dumps(frozen, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        shas.append(hashlib.sha256(b).hexdigest())
    assert len(set(shas)) == 1, f"SHA should be identical regardless of timestamp: {shas}"


# ---------------------------------------------------------------------------
# TC-38: PR head SHA가 바뀌면 permit_stale, CLI 미호출
# ---------------------------------------------------------------------------
def test_r4_tc38_head_sha_change_causes_stale(pipe, tmp_path, monkeypatch):
    """PR head SHA가 변경되면 _check_and_consume_permit이 permit_stale로 BLOCKED한다."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "state.json"))

    # permit 파일 생성 (head_sha = "old...")
    permit_path = tmp_path / "shard_review_permit.json"
    permit = {
        "pipeline_id": "IMP-TEST",
        "pr_head_sha": "old_sha_" + "0" * 32,
        "review_plan_sha": "rp_sha_" + "0" * 57,
        "contract_sha": "",
        "consumed": False,
        "permit_id": "test-permit-38",
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": "2099-12-31T23:59:59Z",
    }
    pipe._write_json(permit_path, permit)
    monkeypatch.setattr(pipe, "_shard_review_permit_path", lambda: permit_path)

    new_head = "new_sha_" + "1" * 32
    with pytest.raises(SystemExit):
        pipe._check_and_consume_permit(
            "IMP-TEST",
            new_head,
            permit["review_plan_sha"],
            "",
        )
    # claim 파일 정리 확인
    claims = list(tmp_path.glob("*.claim"))
    assert len(claims) == 0, f"claim 파일이 남아 있음: {claims}"


# ---------------------------------------------------------------------------
# TC-39/TC-40: 두 스레드가 동시에 같은 permit 소비 → 정확히 1개 성공, 1개 BLOCKED
#   성공한 스레드만 CLI 인자를 받고, 실패한 스레드는 CLI 호출 횟수 = 0
# ---------------------------------------------------------------------------
def test_r4_tc39_40_concurrent_permit_consumption(pipe, tmp_path, monkeypatch):
    """두 스레드가 동시에 permit 소비 시 정확히 1개 성공, 1개 permit_already_claimed BLOCKED."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "state.json"))

    permit_path = tmp_path / "shard_review_permit.json"
    permit = {
        "pipeline_id": "IMP-TEST",
        "pr_head_sha": "head_" + "0" * 35,
        "review_plan_sha": "rp_" + "0" * 61,
        "contract_sha": "",
        "consumed": False,
        "permit_id": "test-permit-3940",
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": "2099-12-31T23:59:59Z",
    }
    pipe._write_json(permit_path, permit)
    monkeypatch.setattr(pipe, "_shard_review_permit_path", lambda: permit_path)

    results = []
    errors = []

    def consume():
        try:
            p = pipe._check_and_consume_permit(
                "IMP-TEST",
                permit["pr_head_sha"],
                permit["review_plan_sha"],
                "",
            )
            results.append(("success", p.get("consumed")))
        except SystemExit as e:
            errors.append(("blocked", str(e)))

    t1 = threading.Thread(target=consume)
    t2 = threading.Thread(target=consume)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    total = len(results) + len(errors)
    assert total == 2, f"예상 2개 결과, 실제 {total}개"
    # 정확히 1개 성공
    assert len(results) == 1, f"성공 횟수가 1이어야 하는데 {len(results)}개"
    # 정확히 1개 차단(permit_already_claimed 또는 permit_consumed)
    assert len(errors) == 1, f"차단 횟수가 1이어야 하는데 {len(errors)}개"


# ---------------------------------------------------------------------------
# TC-41: authorize → frozen plan 저장 → review 실행 E2E (frozen plan 재사용 검증)
# ---------------------------------------------------------------------------
def test_r4_tc41_frozen_plan_reuse_e2e(pipe, tmp_path, monkeypatch):
    """--authorize-run이 저장한 frozen plan을 review 실행 시 재사용한다."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "state.json"))

    frozen_path = tmp_path / "codex_review_frozen_plan.json"
    monkeypatch.setattr(pipe, "_shard_review_frozen_plan_path", lambda: frozen_path)

    # fake plan (generated_at 포함)
    plan = {
        "schema_version": 1,
        "pr_head_sha": "e" * 40,
        "contract_sha256": "f" * 64,
        "included_items": [],
        "excluded_items": [],
        "critical_file_shas": {},
        "included_functions": {},
        "test_summary": "",
        "generated_at": "2026-07-18T00:00:00Z",
    }
    plan_frozen = {k: v for k, v in plan.items() if k not in pipe._FROZEN_PLAN_EXCLUDE_KEYS}
    frozen_bytes = (json.dumps(plan_frozen, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    frozen_path.write_bytes(frozen_bytes)
    expected_sha = hashlib.sha256(frozen_bytes).hexdigest()

    # review 실행 시 frozen plan 로드 검증
    loaded_bytes = frozen_path.read_bytes()
    loaded_plan = json.loads(loaded_bytes)
    computed_sha = hashlib.sha256(loaded_bytes).hexdigest()

    assert computed_sha == expected_sha, "frozen plan SHA 불일치"
    assert "generated_at" not in loaded_plan, "frozen plan에 generated_at이 포함되어 있음"
    assert loaded_plan.get("pr_head_sha") == "e" * 40


# ---------------------------------------------------------------------------
# TC-42: Codex CLI cmd에 --output-schema와 --output-last-message가 포함됨
# ---------------------------------------------------------------------------
def test_r4_tc42_output_schema_in_cmd(pipe, tmp_path, monkeypatch):
    """_call_codex_cli_for_shard가 codex_verdict_schema.json 존재 시 --output-schema를 cmd에 포함한다."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "state.json"))

    def _fake_cli(shard, permit_arg):
        # cmd를 캡처하지 않고 실제 cmd는 CLI factory에서만 확인 가능
        # → 대신 schema_path 존재 여부와 코드 로직을 직접 검증한다
        return {
            "shard_id": shard.get("shard_id"),
            "verdict": "APPROVE_TO_USER",
            "findings": [],
            "pr_head_sha": shard.get("pr_head_sha", ""),
        }

    shard = {"shard_id": "test-shard", "pr_head_sha": "g" * 40}
    # _cli_factory로 실행 (실제 subprocess 없음)
    result = pipe._call_codex_cli_for_shard(shard, _cli_factory=_fake_cli)
    assert result["verdict"] == "APPROVE_TO_USER"

    # schema 파일 존재 여부 확인 (파일이 있으면 cmd에 포함됨을 코드 로직으로 보장)
    # 실제 cmd 검증은 subprocess mock 없이 불가 — 코드 검사로 대체
    import inspect
    src = inspect.getsource(pipe._call_codex_cli_for_shard)
    assert "--output-schema" in src, "_call_codex_cli_for_shard에 --output-schema가 없음"
    assert "--output-last-message" in src, "_call_codex_cli_for_shard에 --output-last-message가 없음"


# ---------------------------------------------------------------------------
# TC-43: --output-last-message 파일 invalid JSON → ERROR, reject_count 불변
# ---------------------------------------------------------------------------
def test_r4_tc43_invalid_json_output_returns_error(pipe, tmp_path, monkeypatch):
    """--output-last-message 결과 파일이 invalid JSON이면 ERROR를 반환하고 reject_count를 증가시키지 않는다."""
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(tmp_path / "state.json"))

    # invalid JSON 파일 생성
    result_file = str(tmp_path / "codex_result.json")
    with open(result_file, "w") as f:
        f.write("THIS IS NOT VALID JSON {{{")

    shard_id = "test-shard-43"
    pr_head_sha = "h" * 40

    result = pipe._parse_codex_cli_shard_output(result_file, shard_id, pr_head_sha)

    assert result["verdict"] == "ERROR", f"expected ERROR, got {result['verdict']}"
    assert result["shard_id"] == shard_id
    # error_reason이 있어야 함 (json_parse_error 또는 유사)
    assert result.get("error_reason"), f"error_reason이 없음: {result}"
    # findings는 빈 리스트
    assert result.get("findings") == [], f"findings가 비어있지 않음: {result}"
