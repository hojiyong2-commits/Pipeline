"""test_codex_defects_dae1.py — IMP-20260712-DAE1 rework: Codex Review 7개 결함 수정 E2E 테스트.

# [Purpose]: Codex Review gate의 7개 결함 수정을 실제 CLI 경로(subprocess) 및 내부 함수로 검증한다.
#   결함1: NON_CONVERGING을 codex_review_result.json에 영속 기록
#   결함2: reject_count SSoT를 append-only history로 전환(result 파일 삭제로 우회 불가)
#   결함3: circuit breaker를 Codex CLI 호출 전에 실행(result 파일 삭제해도 history가 차단)
#   결함4: REJECT/BLOCKED verdict에 findings 필드 필수(없으면 invalid_verdict_schema)
#   결함5: finding 7개 필드 실제 검증(누락/빈값 시 invalid_verdict_schema)
#   결함6: contract_sha256을 구조화된 계약 상수(CODEX_REVIEW_CONTRACT_STRUCT) SHA로 계산
#   결함7: --start-epoch 자동 실행 금지 guard(CODEX_START_EPOCH_USER_CONFIRMED=1 필요)
# [Assumptions]: PIPELINE_STATE_PATH로 state/.pipeline 격리. subprocess로 실제 CLI를 실행하며,
#   내부 함수 직접 호출은 파싱/계약 검증 보조로만 사용한다.
# [Vulnerability & Risks]: fake codex marker 파일 부재로 CLI 미호출을 증명한다. 실제 OpenAI/Codex
#   CLI는 호출하지 않는다(모든 경로가 CLI 호출 전에 fail-closed BLOCKED되거나 순수 파싱 함수).
# [Improvement]: 승인(APPROVED) 흐름까지 포함한 full-flow reject_count 누적 회귀를 추가할 수 있다.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pipeline  # noqa: E402

_PID = "IMP-20260712-DAE1"


# ------------------------------------------------------------------ #
# 공용 헬퍼
# ------------------------------------------------------------------ #
def _setup_state(tmp_path: Path, state: Dict[str, object]) -> Tuple[Path, Path]:
    """격리된 state 파일과 .pipeline 디렉토리를 만든다.

    Returns:
        (state_path, pipeline_dir) — pipeline_dir는 history/result JSON이 놓이는 위치.
    """
    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    pipeline_dir = tmp_path / ".pipeline"
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    return state_path, pipeline_dir


def _seed_history(pipeline_dir: Path, entries: List[Dict[str, object]]) -> Path:
    """codex_review_history.jsonl에 append-only 항목을 seed한다."""
    hist_path = pipeline_dir / "codex_review_history.jsonl"
    with open(hist_path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    return hist_path


def _make_fake_codex(bin_dir: Path, marker: Path) -> None:
    """PATH에 올릴 fake codex 실행 파일을 만든다. 실행되면 marker 파일을 생성한다.

    marker 파일의 부재는 Codex CLI가 호출되지 않았음을 증명한다(CLI 미호출 검증용).
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        # Windows: codex.cmd (shutil.which가 PATHEXT로 탐색).
        (bin_dir / "codex.cmd").write_text(
            "@echo off\r\necho called> \"%s\"\r\n" % str(marker),
            encoding="utf-8",
        )
    else:
        script = bin_dir / "codex"
        script.write_text(
            "#!/bin/sh\necho called > \"%s\"\n" % str(marker),
            encoding="utf-8",
        )
        script.chmod(0o755)


def _run_cli(
    state_path: Path,
    args: List[str],
    fake_bin_dir: Optional[Path] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    """격리 state로 pipeline.py CLI를 subprocess 실행한다."""
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    # 실제 Codex/OpenAI 호출을 유발할 수 있는 자동 실행 확인 변수는 제거한다.
    env.pop("CODEX_START_EPOCH_USER_CONFIRMED", None)
    if fake_bin_dir is not None:
        env["PATH"] = str(fake_bin_dir) + os.pathsep + env.get("PATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_ROOT / "pipeline.py"), *args],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(_ROOT),
        timeout=90,
    )


def _full_finding() -> Dict[str, object]:
    """7개 필수 필드를 모두 갖춘 유효한 finding."""
    return {
        "scope": "IN_SCOPE",
        "severity": "P0",
        "root_cause_category": "fake_codex_exec",
        "evidence": "관측된 증거 문자열",
        "reproduction": "재현 절차",
        "required_fix": "요구되는 수정",
        "acceptance_criteria": ["검증 기준 1"],
    }


# ================================================================== #
# 테스트 1: epoch 누락 시 fake Codex marker 미생성(CLI 미호출) + epoch_missing BLOCKED
# ================================================================== #
def test_1_epoch_missing_blocks_before_cli(tmp_path: Path) -> None:
    """review_epoch 없음 → codex_review_epoch_missing BLOCKED, fake codex marker 미생성."""
    # isolation: PIPELINE_STATE_PATH set by _run_cli helper; final_state asserted via marker/output.
    state_path, _ = _setup_state(tmp_path, {"pipeline_id": _PID, "current_phase": 7})
    bin_dir = tmp_path / "bin"
    marker = tmp_path / "codex_called.marker"
    _make_fake_codex(bin_dir, marker)

    r = _run_cli(state_path, ["gates", "codex-review"], fake_bin_dir=bin_dir)
    combined = r.stdout + r.stderr
    assert r.returncode != 0, f"epoch 없으면 BLOCKED여야 함: {combined[:400]}"
    assert "codex_review_epoch_missing" in combined, combined[:400]
    # 핵심: Codex CLI가 호출되지 않았음을 marker 부재로 증명.
    assert not marker.exists(), "epoch 누락 시 Codex CLI가 호출되면 안 됨(marker 생성됨)"


# ================================================================== #
# 테스트 2: legacy 22회 REJECT 이력 → NON_CONVERGING이 codex_review_result.json에 영속(결함1)
# ================================================================== #
def test_2_legacy_history_persists_non_converging(tmp_path: Path) -> None:
    """epoch 없는 22회 REJECT 이력 → result 파일에 NON_CONVERGING 영속(history는 보존)."""
    # isolation: PIPELINE_STATE_PATH set by _run_cli helper; final_state asserted via result file.
    state_path, pdir = _setup_state(tmp_path, {"pipeline_id": _PID, "current_phase": 7})
    entries = [
        {
            "status": "REJECTED",
            "verdict_scope": "IN_SCOPE",
            "root_cause_category": "legacy_cat_%d" % (i % 7),
            "counts_toward_reject_rate_limit": True,
        }
        for i in range(22)
    ]
    _seed_history(pdir, entries)

    r = _run_cli(state_path, ["gates", "codex-review"])
    assert r.returncode != 0
    assert "codex_review_epoch_missing" in (r.stdout + r.stderr)

    result_path = pdir / "codex_review_result.json"
    assert result_path.exists(), "NON_CONVERGING이 result 파일에 기록돼야 함"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "NON_CONVERGING", result
    assert result["review_epoch"] == "epoch_legacy", result
    assert result["acceptance_eligible"] is False
    assert int(result["effective_rejects"]) == 22
    # history는 삭제/초기화되지 않아야 한다(append-only 보존).
    hist_lines = (pdir / "codex_review_history.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len([ln for ln in hist_lines if ln.strip()]) == 22


# ================================================================== #
# 테스트 3: result 파일 삭제 후에도 history가 CLI 호출 전 차단(결함2+결함3)
# ================================================================== #
def test_3_result_deleted_history_blocks_pre_cli(tmp_path: Path) -> None:
    """named epoch + 5 REJECT history + result 파일 없음 → pre-CLI NON_CONVERGING BLOCKED."""
    # isolation: PIPELINE_STATE_PATH set by _run_cli helper; final_state asserted via result file.
    epoch = "epoch_20260712_001"
    state = {
        "pipeline_id": _PID,
        "current_phase": 7,
        "codex_review_contract_migration": {
            "review_epoch": epoch,
            "new_contract_sha256": "deadbeef",
        },
    }
    state_path, pdir = _setup_state(tmp_path, state)
    entries = [
        {
            "status": "REJECTED",
            "review_epoch": epoch,
            "verdict_scope": "IN_SCOPE",
            "root_cause_category": "cat_%d" % i,
            "counts_toward_reject_rate_limit": True,
        }
        for i in range(5)
    ]
    _seed_history(pdir, entries)
    # result 파일은 존재하지 않음(삭제된 상태 시뮬레이션).
    assert not (pdir / "codex_review_result.json").exists()

    bin_dir = tmp_path / "bin"
    marker = tmp_path / "codex_called.marker"
    _make_fake_codex(bin_dir, marker)

    r = _run_cli(state_path, ["gates", "codex-review"], fake_bin_dir=bin_dir)
    combined = r.stdout + r.stderr
    assert r.returncode != 0, combined[:400]
    assert "codex_review_non_converging_pre_cli" in combined, combined[:400]
    # CLI 미호출 증명.
    assert not marker.exists(), "history가 차단해야 하며 Codex CLI가 호출되면 안 됨"
    # NON_CONVERGING이 result 파일에 다시 영속됨.
    result = json.loads((pdir / "codex_review_result.json").read_text(encoding="utf-8"))
    assert result["status"] == "NON_CONVERGING"
    assert result["review_epoch"] == epoch
    assert int(result["effective_rejects"]) == 5


# ================================================================== #
# 테스트 4: findings 누락 시 → invalid_verdict_schema (parse_failure / None) (결함4)
# ================================================================== #
def test_4_reject_missing_findings_is_parse_failure() -> None:
    """legacy 4-필드 REJECT(findings 없음) → _parse_json_verdict None(invalid_verdict_schema)."""
    legacy_reject = json.dumps({
        "verdict": "REJECT",
        "root_cause": "x",
        "reproduction": "y",
        "required_fix": "z",
        "acceptance_criteria": ["a"],
    })
    assert pipeline._parse_json_verdict(legacy_reject) is None
    # BLOCKED도 findings 필수.
    blocked_no_findings = json.dumps({"verdict": "BLOCKED", "reason": "x"})
    assert pipeline._parse_json_verdict(blocked_no_findings) is None
    # findings가 list가 아님 → None.
    bad_type = json.dumps({"verdict": "REJECT", "findings": {"scope": "IN_SCOPE"}})
    assert pipeline._parse_json_verdict(bad_type) is None


# ================================================================== #
# 테스트 5: findings=[] 시 → invalid_verdict_schema (None) (결함4)
# ================================================================== #
def test_5_reject_empty_findings_is_parse_failure() -> None:
    """findings=[] + REJECT/BLOCKED → _parse_json_verdict None."""
    assert pipeline._parse_json_verdict(
        json.dumps({"verdict": "REJECT", "findings": []})
    ) is None
    assert pipeline._parse_json_verdict(
        json.dumps({"verdict": "BLOCKED", "findings": []})
    ) is None


# ================================================================== #
# 테스트 6: finding 7개 필드 각각 누락/빈값 시 → invalid_verdict_schema (None) (결함5)
# ================================================================== #
def test_6_finding_each_field_required() -> None:
    """finding 7개 필수 필드 중 하나라도 누락/빈값이면 None. 완전하면 REJECTED."""
    required = [
        "scope", "severity", "root_cause_category", "evidence",
        "reproduction", "required_fix", "acceptance_criteria",
    ]
    # 완전한 finding → REJECTED.
    ok = pipeline._parse_json_verdict(
        json.dumps({"verdict": "REJECT", "findings": [_full_finding()]})
    )
    assert ok is not None and ok["verdict"] == "REJECTED"
    assert ok["in_scope_count"] >= 1

    # 각 필드를 하나씩 제거 → None.
    for field in required:
        f = _full_finding()
        del f[field]
        got = pipeline._parse_json_verdict(
            json.dumps({"verdict": "REJECT", "findings": [f]})
        )
        assert got is None, f"필드 '{field}' 누락 시 None이어야 함, got {got!r}"

    # 각 str 필드를 빈 문자열로 → None.
    for field in required:
        if field == "acceptance_criteria":
            continue
        f = _full_finding()
        f[field] = "   "
        assert pipeline._parse_json_verdict(
            json.dumps({"verdict": "REJECT", "findings": [f]})
        ) is None, f"필드 '{field}' 빈 문자열 시 None이어야 함"

    # acceptance_criteria 빈 리스트/빈 원소 → None.
    f = _full_finding()
    f["acceptance_criteria"] = []
    assert pipeline._parse_json_verdict(
        json.dumps({"verdict": "REJECT", "findings": [f]})
    ) is None
    f["acceptance_criteria"] = ["  "]
    assert pipeline._parse_json_verdict(
        json.dumps({"verdict": "REJECT", "findings": [f]})
    ) is None


# ================================================================== #
# 테스트 7: schema ERROR(parse_failure)는 reject_count를 증가시키지 않음 (결함2/결함5)
# ================================================================== #
def test_7_schema_error_does_not_increase_reject_count() -> None:
    """불완전 REJECT는 status=ERROR로 분류되어 effective reject로 계수되지 않는다.

    reject_count SSoT는 append-only history의 effective(IN_SCOPE) REJECT 수이며,
    ERROR/parse_failure 항목은 counts_toward_reject_rate_limit=False로 미계수된다.
    """
    # 불완전 REJECT stdout → CLI 결과 status=ERROR (REJECTED로 승격되지 않음).
    invalid_reject = json.dumps({"verdict": "REJECT"})  # findings 없음
    res = pipeline._run_codex_cli_review(0, invalid_reject, "")
    assert res["status"] == "ERROR", res
    assert res.get("error_type") == "parse_failure", res

    # ERROR 항목만 있는 history → effective_rejects=0 (reject_count 미증가).
    err_hist = [
        {
            "status": "ERROR",
            "review_epoch": "epoch_x",
            "counts_toward_reject_rate_limit": False,
        }
        for _ in range(4)
    ]
    cb = pipeline._check_codex_circuit_breaker(err_hist, "epoch_x")
    assert cb["effective_rejects"] == 0, cb
    assert cb["triggered"] is False


# ================================================================== #
# 테스트 8: contract_sha256이 구조화된 계약 상수 SHA와 일치 (결함6)
# ================================================================== #
def test_8_contract_sha256_matches_struct() -> None:
    """_compute_codex_contract_sha256 == CODEX_REVIEW_CONTRACT_STRUCT canonical SHA256."""
    expected = hashlib.sha256(
        json.dumps(
            pipeline.CODEX_REVIEW_CONTRACT_STRUCT, sort_keys=True, ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()
    actual = pipeline._compute_codex_contract_sha256()
    assert actual == expected
    assert len(actual) == 64
    # 결정적: 두 번 호출해도 동일.
    assert pipeline._compute_codex_contract_sha256() == actual


# ================================================================== #
# 테스트 9: 계약 struct 외부의 값 변경은 contract_sha256을 바꾸지 않음 (결함6)
# ================================================================== #
def test_9_noncontract_value_does_not_affect_sha() -> None:
    """SHA 입력은 CODEX_REVIEW_CONTRACT_STRUCT 뿐 — struct에 없는 상수는 영향 없음."""
    canonical = json.dumps(
        pipeline.CODEX_REVIEW_CONTRACT_STRUCT, sort_keys=True, ensure_ascii=True
    )
    # 계약과 무관한 예산 상수 값은 canonical 입력에 포함되지 않는다.
    assert str(pipeline.CODEX_REVIEW_BUNDLE_BUDGET_CHARS) not in canonical
    # struct 내용이 실제로 바뀌면 SHA가 바뀐다(민감도 검증) — 원본은 훼손하지 않음.
    mutated = dict(pipeline.CODEX_REVIEW_CONTRACT_STRUCT)
    mutated["schema_version"] = mutated["schema_version"] + 1
    mutated_sha = hashlib.sha256(
        json.dumps(mutated, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    assert mutated_sha != pipeline._compute_codex_contract_sha256()


# ================================================================== #
# 테스트 10: 기존 supply-chain finding(bounded trust SSoT)이 변경되지 않음
# ================================================================== #
def test_10_supply_chain_findings_unchanged() -> None:
    """3개 bounded trust SSoT 목록의 항목 수와 핵심 supply-chain 항목이 그대로 보존됨."""
    assert len(pipeline.CODEX_BOUNDED_TRUST_IN_SCOPE) == 8
    assert len(pipeline.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC) == 6
    assert len(pipeline.CODEX_BOUNDED_TRUST_ENVIRONMENT_UNTRUSTED) == 6
    # 공급망 관련 진단 항목은 OUT_OF_SCOPE_DIAGNOSTIC에 그대로 존재.
    for entry in (
        "openai_registry_compromise",
        "npm_tarball_supply_chain_proof",
        "native_binary_origin_proof",
        "authenticode_ca_trust_store",
        "same_os_user_privilege_attack",
        "external_signing_unverifiable",
    ):
        assert entry in pipeline.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC
    # 세 목록은 상호 배타적(disjoint)이어야 한다.
    _in = set(pipeline.CODEX_BOUNDED_TRUST_IN_SCOPE)
    _out = set(pipeline.CODEX_BOUNDED_TRUST_OUT_OF_SCOPE_DIAGNOSTIC)
    _env = set(pipeline.CODEX_BOUNDED_TRUST_ENVIRONMENT_UNTRUSTED)
    assert not (_in & _out) and not (_in & _env) and not (_out & _env)


# ================================================================== #
# IMP-20260712-DAE1 REJECT#LATEST rework: _check_codex_review_gate 강화 회귀 테스트 (15개)
#   결함1(acceptance_eligible 필수) / 결함2(pr_body_sha256 필수) / 결함3(schema·status·verdict·
#   source 동시 요구) / 결함4(pipeline_id·review_epoch·contract_sha256·acceptance PENDING 불변식) /
#   결함5(start-epoch가 struct SHA 사용)를 직접 함수 호출 + subprocess로 검증한다.
# ================================================================== #
_GATE_EPOCH = "epoch_20260712_gate"
_HEAD_SHA = "a" * 40
_PACKET_SHA = "packet" + "0" * 58
_BODY_SHA = "body0" + "0" * 59


def _gate_state(epoch: str = _GATE_EPOCH) -> Dict[str, object]:
    """review_epoch를 담은 최소 state dict."""
    return {
        "pipeline_id": _PID,
        "current_phase": 7,
        "codex_review_contract_migration": {"review_epoch": epoch},
    }


def _valid_result(drop: Tuple[str, ...] = (), **overrides: object) -> Dict[str, object]:
    """모든 불변식을 통과해 head SHA 검증 직전까지 도달하는 완전한 result dict.

    drop에 나열된 키는 제거하여 '필드 누락' 상황을 시뮬레이션한다.
    """
    r: Dict[str, object] = {
        "schema_version": pipeline.CODEX_REVIEW_RESULT_SCHEMA_VERSION,
        "pipeline_id": _PID,
        "status": "APPROVED",
        "verdict": "APPROVE_TO_USER",
        "acceptance_eligible": True,
        "verdict_source": "codex_cli",
        "review_epoch": _GATE_EPOCH,
        "contract_sha256": pipeline._compute_codex_contract_sha256(),
        "pr_head_sha": _HEAD_SHA,
        "packet_sha256": _PACKET_SHA,
        "pr_body_sha256": _BODY_SHA,
    }
    r.update(overrides)
    for k in drop:
        r.pop(k, None)
    return r


def _valid_request(drop: Tuple[str, ...] = (), **overrides: object) -> Dict[str, object]:
    """result와 일치하는 PENDING acceptance_request."""
    req: Dict[str, object] = {
        "pipeline_id": _PID,
        "status": "PENDING",
        "packet_sha256": _PACKET_SHA,
        "pr_body_sha256": _BODY_SHA,
    }
    req.update(overrides)
    for k in drop:
        req.pop(k, None)
    return req


def _call_gate(
    tmp_path: Path,
    monkeypatch,
    result: Dict[str, object],
    request: Optional[Dict[str, object]] = None,
    state: Optional[Dict[str, object]] = None,
    head_sha: Optional[str] = None,
) -> Dict[str, object]:
    """격리 환경에서 _check_codex_review_gate를 직접 호출한다.

    PIPELINE_STATE_PATH로 result 경로를 격리하고, cwd를 tmp_path로 바꿔
    acceptance_request.json(상대경로 SSoT)을 격리한다. head SHA는 monkeypatch로 결정적으로 만든다.
    """
    st = state if state is not None else _gate_state()
    state_path = tmp_path / "pipeline_state.json"
    state_path.write_text(json.dumps(st), encoding="utf-8")
    pdir = tmp_path / ".pipeline"
    pdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PIPELINE_STATE_PATH", str(state_path))
    monkeypatch.chdir(tmp_path)
    (pdir / "codex_review_result.json").write_text(json.dumps(result), encoding="utf-8")
    if request is not None:
        (tmp_path / "acceptance_request.json").write_text(
            json.dumps(request), encoding="utf-8"
        )
    monkeypatch.setattr(pipeline, "_get_current_pr_head_sha", lambda: head_sha)
    return pipeline._check_codex_review_gate(_PID, st)


# --- 결함1: acceptance_eligible 필수화 ------------------------------- #
def test_acceptance_eligible_missing_blocked(tmp_path: Path, monkeypatch) -> None:
    """acceptance_eligible 필드 없음 → codex_review_not_eligible BLOCKED."""
    res = _call_gate(tmp_path, monkeypatch, _valid_result(drop=("acceptance_eligible",)))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_not_eligible", res


def test_acceptance_eligible_false_blocked(tmp_path: Path, monkeypatch) -> None:
    """acceptance_eligible=False → codex_review_not_eligible BLOCKED."""
    res = _call_gate(tmp_path, monkeypatch, _valid_result(acceptance_eligible=False))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_not_eligible", res


def test_acceptance_eligible_string_true_blocked(tmp_path: Path, monkeypatch) -> None:
    """acceptance_eligible='true'(문자열) → bool True 아님 → BLOCKED."""
    res = _call_gate(tmp_path, monkeypatch, _valid_result(acceptance_eligible="true"))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_not_eligible", res


# --- 결함3: status/verdict/source/schema 동시 요구 ------------------- #
def test_status_approved_no_verdict_blocked(tmp_path: Path, monkeypatch) -> None:
    """status=APPROVED이지만 verdict 누락 → codex_review_verdict_mismatch BLOCKED."""
    res = _call_gate(tmp_path, monkeypatch, _valid_result(drop=("verdict",)))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_verdict_mismatch", res


def test_verdict_approve_to_user_no_status_blocked(tmp_path: Path, monkeypatch) -> None:
    """verdict=APPROVE_TO_USER이지만 status 누락 → codex_review_not_approved BLOCKED."""
    res = _call_gate(tmp_path, monkeypatch, _valid_result(drop=("status",)))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_not_approved", res


def test_verdict_source_external_blocked(tmp_path: Path, monkeypatch) -> None:
    """verdict_source='external' → codex_review_untrusted_source BLOCKED."""
    res = _call_gate(tmp_path, monkeypatch, _valid_result(verdict_source="external"))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_untrusted_source", res


def test_verdict_source_manual_blocked(tmp_path: Path, monkeypatch) -> None:
    """verdict_source='manual' → codex_review_untrusted_source BLOCKED."""
    res = _call_gate(tmp_path, monkeypatch, _valid_result(verdict_source="manual"))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_untrusted_source", res


def test_schema_version_mismatch_blocked(tmp_path: Path, monkeypatch) -> None:
    """schema_version 불일치 → codex_review_schema_mismatch BLOCKED (결함3 보강)."""
    bad = pipeline.CODEX_REVIEW_RESULT_SCHEMA_VERSION + 99
    res = _call_gate(tmp_path, monkeypatch, _valid_result(schema_version=bad))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_schema_mismatch", res


# --- 결함2: pr_body_sha256 필수화 ----------------------------------- #
def test_body_sha_both_absent_blocked(tmp_path: Path, monkeypatch) -> None:
    """result/request 양쪽 body SHA 없음 → pr_body_sha256_required_but_absent BLOCKED."""
    res = _call_gate(
        tmp_path, monkeypatch,
        _valid_result(drop=("pr_body_sha256",)),
        request=_valid_request(drop=("pr_body_sha256",)),
    )
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "pr_body_sha256_required_but_absent", res


def test_body_sha_mismatch_blocked(tmp_path: Path, monkeypatch) -> None:
    """result/request body SHA 불일치 → pr_body_sha256_mismatch BLOCKED."""
    res = _call_gate(
        tmp_path, monkeypatch,
        _valid_result(),  # pr_body_sha256 = _BODY_SHA
        request=_valid_request(pr_body_sha256="deadbeef" * 8),
    )
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "pr_body_sha256_mismatch", res


# --- 결함4: review_epoch / contract_sha256 불변식 ------------------- #
def test_review_epoch_mismatch_blocked(tmp_path: Path, monkeypatch) -> None:
    """result.review_epoch != state epoch → codex_review_stale_epoch BLOCKED."""
    res = _call_gate(
        tmp_path, monkeypatch, _valid_result(review_epoch="epoch_other_999")
    )
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_stale_epoch", res


def test_contract_sha_mismatch_blocked(tmp_path: Path, monkeypatch) -> None:
    """result.contract_sha256 != struct SHA → codex_review_stale_contract BLOCKED."""
    res = _call_gate(
        tmp_path, monkeypatch, _valid_result(contract_sha256="0" * 64)
    )
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_stale_contract", res


# --- 결함6: struct SHA는 계약 struct에만 의존(비계약 코드 변경 무관) --- #
def test_pipeline_py_change_no_contract_sha_change() -> None:
    """_compute_codex_contract_sha256는 CODEX_REVIEW_CONTRACT_STRUCT만 해시한다.

    struct에 포함되지 않은 상수(예: 번들 예산 문자수)는 SHA 입력(canonical JSON)에 없으므로,
    그런 비계약 값이 바뀌어도 contract SHA는 변하지 않는다.
    """
    canonical = json.dumps(
        pipeline.CODEX_REVIEW_CONTRACT_STRUCT, sort_keys=True, ensure_ascii=True
    )
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert pipeline._compute_codex_contract_sha256() == expected
    # 비계약 상수 값은 canonical 입력에 포함되지 않는다(그 값을 바꿔도 SHA 불변).
    assert str(pipeline.CODEX_REVIEW_BUNDLE_BUDGET_CHARS) not in canonical
    # 결정적: 반복 호출해도 동일.
    assert pipeline._compute_codex_contract_sha256() == expected


# --- 결함5: start-epoch가 struct SHA를 기록 ------------------------- #
def test_start_epoch_uses_contract_sha(tmp_path: Path) -> None:
    """--start-epoch가 migration.new_contract_sha256을 struct SHA로 기록한다(subprocess)."""
    # isolation: PIPELINE_STATE_PATH set by _run_cli; final_state asserted via state file.
    state_path, _ = _setup_state(tmp_path, {"pipeline_id": _PID, "current_phase": 7})
    r = _run_cli(
        state_path,
        ["gates", "codex-review", "--start-epoch", "회귀 테스트: struct SHA 검증"],
        extra_env={"CODEX_START_EPOCH_USER_CONFIRMED": "1"},
    )
    combined = r.stdout + r.stderr
    assert r.returncode == 0, combined[:600]
    assert "contract migration 기록 완료" in combined, combined[:600]
    new_state = json.loads(state_path.read_text(encoding="utf-8"))
    migration = new_state.get("codex_review_contract_migration") or {}
    assert migration.get("new_contract_sha256") == pipeline._compute_codex_contract_sha256(), (
        migration
    )
    # pipeline.py 전체 파일 SHA와는 다르다(비계약 코드 변경에 흔들리지 않음).
    assert migration.get("new_contract_sha256") != pipeline._sha256_file(
        pipeline.BASE_DIR / "pipeline.py"
    )


# --- 기존 검증 유지: head SHA / packet SHA -------------------------- #
def test_existing_head_sha_check_maintained(tmp_path: Path, monkeypatch) -> None:
    """모든 불변식 통과 후 head SHA 불일치 → codex_review_stale BLOCKED (head 검증 유지)."""
    res = _call_gate(
        tmp_path, monkeypatch,
        _valid_result(),
        request=_valid_request(),
        head_sha="b" * 40,  # result.pr_head_sha(_HEAD_SHA='a'*40)와 다름
    )
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_stale", res
    assert "pr_head_sha" in str(res["message"]), res


def test_existing_packet_sha_check_maintained(tmp_path: Path, monkeypatch) -> None:
    """packet SHA 불일치 → codex_review_stale BLOCKED (packet 검증 유지)."""
    res = _call_gate(
        tmp_path, monkeypatch,
        _valid_result(),  # packet_sha256 = _PACKET_SHA
        request=_valid_request(packet_sha256="f" * 64),
    )
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_stale", res
    assert "packet_sha256" in str(res["message"]), res


# ================================================================== #
# IMP-20260712-DAE1 REJECT#14 rework: verdict_source 단일 SSoT 회귀 테스트 (8개)
#   결함1: CODEX_REVIEW_TRUSTED_VERDICT_SOURCES를 2개(codex_cli/verified_cache)로 축소.
#   결함2: _check_codex_review_gate alias → legacy_untrusted_source.
#   결함3: _check_codex_review_operational_trust가 동일 SSoT 상수 사용 + alias 구분.
#   결함4: --start-epoch guard 주석은 운영 정책 서술로 교정(코드 검증 대상 아님).
#
#   두 소비자(_check_codex_review_gate, _check_codex_review_operational_trust)가 동일한
#   verdict_source 판정을 내리는지 직접 함수 호출로 검증한다. 실제 Codex CLI는 호출하지 않는다
#   (순수 판정 함수 + 파일 IO 없는 operational_trust).
# ================================================================== #

# verdict_source 계층에서 반환되는 BLOCKED failure_code 집합.
#   trusted 값이면 이 계층을 통과하여 이후 다른 검사에서 다른 failure_code로만 차단된다.
_GATE_VS_FAILURE_CODES = {"codex_review_untrusted_source", "legacy_untrusted_source"}
_OT_VS_FAILURE_CODES = {"codex_review_untrusted_verdict_source", "legacy_untrusted_source"}


def _ot_verdict_source_block(verdict_source: str) -> Optional[str]:
    """operational_trust에서 verdict_source 계층이 차단하는지 판정한다.

    최소 result(verdict_source만 지정)로 호출한다. trusted 값이면 verdict_source 검사를
    통과하고 그 다음 검사(acceptance_eligible)에서 다른 failure_code로 차단되므로,
    verdict_source 계층 failure_code가 아니면 None(계층 통과)을 반환한다.

    Returns:
        verdict_source 계층에서 차단됐으면 그 failure_code, 아니면 None.
    """
    res = pipeline._check_codex_review_operational_trust({"verdict_source": verdict_source})
    fc = str(res.get("failure_code", "") or "")
    if res.get("status") == "BLOCKED" and fc in _OT_VS_FAILURE_CODES:
        return fc
    return None


def test_verdict_source_ssot_constant_is_exactly_two() -> None:
    """CODEX_REVIEW_TRUSTED_VERDICT_SOURCES는 정확히 2개(codex_cli/verified_cache)만 포함(결함1)."""
    assert pipeline.CODEX_REVIEW_TRUSTED_VERDICT_SOURCES == frozenset(
        {"codex_cli", "verified_cache"}
    )
    # 제거된 alias는 SSoT 집합에 없어야 한다.
    assert "codex_cli_cached" not in pipeline.CODEX_REVIEW_TRUSTED_VERDICT_SOURCES
    assert "cache_hit" not in pipeline.CODEX_REVIEW_TRUSTED_VERDICT_SOURCES


def test_vs_codex_cli_passes_both(tmp_path: Path, monkeypatch) -> None:
    """verdict_source='codex_cli' → gate 완전 PASS + operational_trust verdict_source 계층 통과."""
    # gate: 모든 불변식 통과 + head SHA 일치 → PASS.
    res = _call_gate(
        tmp_path, monkeypatch,
        _valid_result(verdict_source="codex_cli"),
        request=_valid_request(),
        head_sha=_HEAD_SHA,
    )
    assert res["status"] == "PASS", res
    # operational_trust: verdict_source 계층에서 차단되지 않음.
    assert _ot_verdict_source_block("codex_cli") is None


def test_vs_verified_cache_passes_both(tmp_path: Path, monkeypatch) -> None:
    """verdict_source='verified_cache' → gate 완전 PASS + operational_trust verdict_source 계층 통과."""
    res = _call_gate(
        tmp_path, monkeypatch,
        _valid_result(verdict_source="verified_cache"),
        request=_valid_request(),
        head_sha=_HEAD_SHA,
    )
    assert res["status"] == "PASS", res
    assert _ot_verdict_source_block("verified_cache") is None


def test_vs_alias_codex_cli_cached_legacy_blocked(tmp_path: Path, monkeypatch) -> None:
    """verdict_source='codex_cli_cached'(제거된 alias) → 양쪽 legacy_untrusted_source BLOCKED(결함2/3)."""
    res = _call_gate(tmp_path, monkeypatch, _valid_result(verdict_source="codex_cli_cached"))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "legacy_untrusted_source", res
    assert _ot_verdict_source_block("codex_cli_cached") == "legacy_untrusted_source"


def test_vs_alias_cache_hit_legacy_blocked(tmp_path: Path, monkeypatch) -> None:
    """verdict_source='cache_hit'(제거된 alias) → 양쪽 legacy_untrusted_source BLOCKED(결함2/3)."""
    res = _call_gate(tmp_path, monkeypatch, _valid_result(verdict_source="cache_hit"))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "legacy_untrusted_source", res
    assert _ot_verdict_source_block("cache_hit") == "legacy_untrusted_source"


def test_vs_external_blocked_both(tmp_path: Path, monkeypatch) -> None:
    """verdict_source='external' → gate=codex_review_untrusted_source, ot=codex_review_untrusted_verdict_source."""
    res = _call_gate(tmp_path, monkeypatch, _valid_result(verdict_source="external"))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_untrusted_source", res
    assert _ot_verdict_source_block("external") == "codex_review_untrusted_verdict_source"


def test_vs_manual_blocked_both(tmp_path: Path, monkeypatch) -> None:
    """verdict_source='manual' → 양쪽 verdict_source 계층에서 BLOCKED(비-alias failure_code)."""
    res = _call_gate(tmp_path, monkeypatch, _valid_result(verdict_source="manual"))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_untrusted_source", res
    assert _ot_verdict_source_block("manual") == "codex_review_untrusted_verdict_source"


def test_vs_missing_blocked_both(tmp_path: Path, monkeypatch) -> None:
    """verdict_source 누락(빈 문자열) → 양쪽 verdict_source 계층에서 BLOCKED."""
    res = _call_gate(tmp_path, monkeypatch, _valid_result(drop=("verdict_source",)))
    assert res["status"] == "BLOCKED", res
    assert res["failure_code"] == "codex_review_untrusted_source", res
    # operational_trust: 빈 문자열도 verdict_source 계층에서 차단.
    assert _ot_verdict_source_block("") == "codex_review_untrusted_verdict_source"


def test_gate_and_operational_trust_verdict_source_agreement(
    tmp_path: Path, monkeypatch
) -> None:
    """두 소비자가 동일 verdict_source 판정을 내린다: trusted 둘 다 허용, untrusted 둘 다 BLOCKED(통합).

    - gate: _call_gate로 전체 fixture를 구성한다. trusted 값은 완전 PASS(request/head SHA 일치),
      untrusted 값은 verdict_source 계층 failure_code로 BLOCKED된다(verdict_source 검사가
      epoch/head 검사보다 먼저 실행되므로 fixture 미완성이어도 계층에서 차단됨).
    - operational_trust: _ot_verdict_source_block으로 verdict_source 계층 판정을 격리한다.
    두 함수 모두 CODEX_REVIEW_TRUSTED_VERDICT_SOURCES를 SSoT로 사용하므로 판정이 일치해야 한다.
    """
    trusted = ["codex_cli", "verified_cache"]
    untrusted = ["codex_cli_cached", "cache_hit", "external", "manual", "agent_generated", ""]

    for vs in trusted:
        # trusted 값은 항상 non-empty이므로 verdict_source만 교체한 완전 fixture로 PASS를 검증한다.
        gate_res = _call_gate(
            tmp_path, monkeypatch,
            _valid_result(verdict_source=vs),
            request=_valid_request(),
            head_sha=_HEAD_SHA,
        )
        assert gate_res["status"] == "PASS", f"gate: {vs} trusted → PASS 기대, got {gate_res}"
        assert _ot_verdict_source_block(vs) is None, f"ot: {vs} trusted여야 함"

    for vs in untrusted:
        drop = ("verdict_source",) if vs == "" else ()
        gate_res = _call_gate(
            tmp_path, monkeypatch,
            _valid_result(drop=drop) if vs == "" else _valid_result(verdict_source=vs),
        )
        assert gate_res["status"] == "BLOCKED", f"gate: {vs} untrusted → BLOCKED 기대"
        assert gate_res["failure_code"] in _GATE_VS_FAILURE_CODES, (
            f"gate: {vs} → verdict_source 계층 failure_code 기대, got {gate_res['failure_code']}"
        )
        assert _ot_verdict_source_block(vs) is not None, f"ot: {vs} untrusted여야 함"


# ================================================================== #
# REJECT#15 회귀 테스트: P0 findings 타입 검증 + P1 프롬프트/파서 일관성
# ================================================================== #

class TestReject15FindingsTypeValidation:
    """[P0] APPROVE_TO_USER에서 findings가 list 아닌 경우 parse_failure."""

    def test_approve_to_user_findings_dict_is_parse_failure(self) -> None:
        """findings=dict → APPROVE_TO_USER라도 parse_failure(None)."""
        payload = json.dumps({
            "verdict": "APPROVE_TO_USER",
            "findings": {
                "scope": "IN_SCOPE",
                "severity": "P0",
                "root_cause_category": "fake_codex_exec",
            },
        })
        assert pipeline._parse_json_verdict(payload) is None

    def test_approve_to_user_findings_string_is_parse_failure(self) -> None:
        """findings=string → parse_failure(None)."""
        payload = json.dumps({
            "verdict": "APPROVE_TO_USER",
            "findings": "IN_SCOPE",
        })
        assert pipeline._parse_json_verdict(payload) is None

    def test_approve_to_user_findings_null_is_parse_failure(self) -> None:
        """findings=null → parse_failure(None)."""
        payload = json.dumps({
            "verdict": "APPROVE_TO_USER",
            "findings": None,
        })
        assert pipeline._parse_json_verdict(payload) is None

    def test_approve_to_user_findings_int_is_parse_failure(self) -> None:
        """findings=int → parse_failure(None)."""
        payload = json.dumps({
            "verdict": "APPROVE_TO_USER",
            "findings": 42,
        })
        assert pipeline._parse_json_verdict(payload) is None

    def test_approve_to_user_no_findings_key_is_approved(self) -> None:
        """findings 키 자체가 없는 APPROVE_TO_USER → 기존처럼 APPROVED."""
        payload = json.dumps({"verdict": "APPROVE_TO_USER"})
        result = pipeline._parse_json_verdict(payload)
        assert result is not None
        assert result["verdict"] == "APPROVED"

    def test_approve_to_user_with_valid_in_scope_finding_returns_approved_with_count(self) -> None:
        """유효한 IN_SCOPE finding 포함 APPROVE_TO_USER → APPROVED + in_scope_count/reject_count_delta 보존."""
        finding = {
            "scope": "IN_SCOPE",
            "severity": "P0",
            "root_cause_category": "error_misclassified_as_approved",
            "evidence": "line 123",
            "reproduction": "repro steps",
            "required_fix": "fix here",
            "acceptance_criteria": ["criterion 1"],
        }
        payload = json.dumps({"verdict": "APPROVE_TO_USER", "findings": [finding]})
        result = pipeline._parse_json_verdict(payload)
        assert result is not None
        assert result["verdict"] == "APPROVED"
        assert result.get("in_scope_count", 0) >= 1
        assert result.get("reject_count_delta", 0) >= 0


class TestReject15PromptParserConsistency:
    """[P1] 프롬프트 REJECT 예시 → 파서가 REJECTED로 분류."""

    def test_reject_with_findings_is_rejected_not_parse_failure(self) -> None:
        """findings 포함 유효 REJECT → REJECTED (parse_failure 아님)."""
        finding = {
            "scope": "IN_SCOPE",
            "severity": "P1",
            "root_cause_category": "verdict_parse_failure",
            "evidence": "evidence here",
            "reproduction": "reproduction steps",
            "required_fix": "fix direction",
            "acceptance_criteria": ["criterion"],
        }
        payload = json.dumps({
            "verdict": "REJECT",
            "findings": [finding],
        })
        result = pipeline._parse_json_verdict(payload)
        assert result is not None, "findings 포함 유효 REJECT가 parse_failure여서는 안 됨"
        assert result["verdict"] == "REJECTED"

    def test_reject_without_findings_is_parse_failure_option_a(self) -> None:
        """Option A: findings 없는 REJECT → parse_failure(None) — 프롬프트와 파서가 일관."""
        payload = json.dumps({
            "verdict": "REJECT",
            "root_cause": "x",
            "reproduction": "y",
            "required_fix": "z",
            "acceptance_criteria": ["a"],
        })
        result = pipeline._parse_json_verdict(payload)
        assert result is None, "Option A: findings 없는 REJECT는 parse_failure여야 함"

    def test_prompt_schema_contains_all_7_required_fields(self) -> None:
        """_build_codex_prompt_for_review의 finding 스키마에 7개 필수 필드 포함."""
        bundle = {
            "changed_files": ["pipeline.py"],
            "changed_files_count": 1,
            "evidence_complete": True,
            "diff_hunks": [],
        }
        prompt = pipeline._build_codex_prompt_for_review(bundle, "IMP-20260712-DAE1")
        required_fields = [
            "scope", "severity", "root_cause_category", "evidence",
            "reproduction", "required_fix", "acceptance_criteria",
        ]
        for field in required_fields:
            assert field in prompt, f"프롬프트에 '{field}' 필드 설명이 없음"


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
