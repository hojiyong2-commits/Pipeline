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


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
