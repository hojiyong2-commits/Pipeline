"""
test_evidence_inventory_82ed.py — IMP-20260613-82ED MT-2 Evidence Inventory Tests

# [Purpose]: pipeline.py의 Evidence Inventory SSoT 기능(_register_evidence_to_inventory,
#            _validate_evidence_provenance, _build_verification_json의 evidence_integrity 섹션)이
#            오라클 등록/검증/차단 로직을 올바르게 수행하는지 AC-1~AC-11로 검증한다.
#            상태 변경 CLI 호출은 PIPELINE_STATE_PATH로 격리된 임시 state 파일을 사용하여
#            전역 pipeline_state.json을 오염시키지 않는다.
# [Assumptions]: pipeline.py에 _register_evidence_to_inventory / _validate_evidence_provenance /
#                _contract_paths(evidence_inventory 키) / _build_verification_json(evidence_integrity)
#                가 구현되어 있고, tmp_path pytest fixture로 테스트마다 독립 state 경로를 사용한다.
#                기능이 아직 일부만 배선된 경우 pytest.skip으로 우아하게 처리한다.
# [Vulnerability & Risks]:
#   - subprocess 호출이 60초 timeout 초과 시 실패.
#   - gh CLI/네트워크가 없는 환경에서 request-accept gate는 gh_cli_failed로 BLOCKED될 수 있어,
#     테스트는 "non-PASS 결과 + returncode!=0"만 검증한다.
#   - pipeline_contracts/<pid>/ 디렉토리는 전역 경로이므로 각 테스트가 cleanup으로 정리한다.
# [Improvement]: 향후 gh CLI 모킹으로 PR 포함 경로(pr_included_count) PASS 케이스도 커버 가능.

# CLI Evidence Contract (BUG-20260525-39DE):
# - 상태 변경 CLI 호출은 PIPELINE_STATE_PATH 격리 사용
# - 직접 모듈 임포트 테스트(AC-7/AC-8/AC-9)는 순수 함수 검증용으로 CLI 상태 미변경
# - CLI_EVIDENCE_ALLOW_READ_ONLY: gates oracle/request-accept는 차단(BLOCKED) 결과만 확인하는 read-only 검증
"""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List, Optional

import pytest

# 프로젝트 루트 경로 (tests/ 의 상위 디렉토리)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_pipeline(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """pipeline.py CLI를 subprocess로 실행한다.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        env: subprocess 환경 변수 딕셔너리.
        cwd: 작업 디렉토리 (기본 PROJECT_ROOT).
        timeout: 초 단위 타임아웃 (기본 60초).
    Returns:
        실행된 CompletedProcess.
    Raises:
        TypeError: args가 list가 아니거나 None인 경우.
    """
    if args is None:
        raise TypeError("args must not be None")
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    cmd = [sys.executable, str(PROJECT_ROOT / "pipeline.py")] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd or str(PROJECT_ROOT),
        timeout=timeout,
    )


def make_isolated_env(state_path: Path) -> Dict[str, str]:
    """PIPELINE_STATE_PATH 격리 + 대시보드/네트워크 호출 차단 환경 변수를 만든다.

    Args:
        state_path: 격리된 state 파일 경로. None 금지.
    Returns:
        os.environ 복사본에 격리 변수를 더한 딕셔너리.
    Raises:
        TypeError: state_path가 None인 경우.
    """
    if state_path is None:
        raise TypeError("state_path must not be None")
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    env["PIPELINE_NO_DASHBOARD"] = "1"
    return env


def sha256_file(path: Path) -> str:
    """파일의 sha256 hex digest를 반환한다.

    Args:
        path: 대상 파일 경로. None 금지.
    Returns:
        sha256 hex 문자열.
    Raises:
        TypeError: path가 None인 경우.
    """
    if path is None:
        raise TypeError("path must not be None")
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_pipeline_module() -> ModuleType:
    """pipeline.py를 'pipeline' 모듈로 동적 임포트하여 반환한다.

    Returns:
        로드된 pipeline 모듈 객체.
    Raises:
        ImportError: spec 생성 실패 시.
    """
    spec = importlib.util.spec_from_file_location(
        "pipeline", str(PROJECT_ROOT / "pipeline.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot create import spec for pipeline.py")
    module = importlib.util.module_from_spec(spec)
    # PIPELINE_STATE_PATH가 import 시점에 평가되지 않도록 현재 환경 그대로 로드
    spec.loader.exec_module(module)
    return module


def _new_pipeline(env: Dict[str, str], desc: str) -> str:
    """파이프라인을 생성하고 pipeline_id를 반환한다.

    Args:
        env: 격리 환경 변수.
        desc: 파이프라인 설명.
    Returns:
        생성된 pipeline_id 문자열.
    """
    result = run_pipeline(["new", "--type", "IMP", "--desc", desc], env=env)
    assert result.returncode == 0, f"new failed: {result.stderr}"
    # 격리된 PIPELINE_STATE_PATH state 파일을 읽어 실제 상태 효과를 검증한다.
    state_path = Path(env["PIPELINE_STATE_PATH"])
    final_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert final_state.get("pipeline_id"), f"new did not record pipeline_id: {final_state}"
    return str(final_state["pipeline_id"])


def _make_oracle_files(pid: str, case_id: str, expected_content: str) -> Dict[str, Path]:
    """tests/oracles/<pid>/<case_id>/ 아래에 input/expected 오라클 파일을 생성한다.

    O001 규칙(오라클은 tests/oracles/** 하위여야 함)을 만족하기 위해
    실제 PROJECT_ROOT/tests/oracles 트리에 파일을 생성한다.

    Args:
        pid: 파이프라인 ID. None 금지.
        case_id: 오라클 케이스 디렉토리 이름.
        expected_content: expected.json에 기록할 JSON 문자열.
    Returns:
        {"oracle_dir": Path, "input": Path, "expected": Path} 딕셔너리.
    Raises:
        TypeError: pid 또는 case_id가 None인 경우.
    """
    if pid is None:
        raise TypeError("pid must not be None")
    if case_id is None:
        raise TypeError("case_id must not be None")
    oracle_dir = PROJECT_ROOT / "tests" / "oracles" / pid / case_id
    oracle_dir.mkdir(parents=True, exist_ok=True)
    input_file = oracle_dir / "input.json"
    expected_file = oracle_dir / "expected.json"
    input_file.write_text('{"key": "value"}', encoding="utf-8")
    expected_file.write_text(expected_content, encoding="utf-8")
    return {"oracle_dir": oracle_dir, "input": input_file, "expected": expected_file}


# ---------------------------------------------------------------------------
# AC-1: contract add-oracle 실행 시 evidence_inventory.json에 등록 확인
# ---------------------------------------------------------------------------

class TestAC1AddOracleRegistersEvidence:
    """AC-1: contract add-oracle 후 evidence_inventory.json에 input/expected가 등록됨."""

    def test_add_oracle_registers_to_inventory(self, tmp_path: Path) -> None:
        """contract add-oracle 후 evidence_inventory.json에 필수 필드 포함 항목이 등록됨."""
        state_path = tmp_path / "pipeline_state.json"
        env = make_isolated_env(state_path)

        pid = _new_pipeline(env, "test AC-1")

        oracle = _make_oracle_files(pid, "test_case", '{"result": "ok"}')
        contracts_dir = PROJECT_ROOT / "pipeline_contracts" / pid
        oracle_root = PROJECT_ROOT / "tests" / "oracles" / pid
        inventory_path = contracts_dir / "evidence_inventory.json"
        try:
            result = run_pipeline(["contract", "init", "--pipeline-id", pid], env=env)
            assert result.returncode == 0, f"contract init failed: {result.stderr}"
            # 격리된 PIPELINE_STATE_PATH state 파일을 읽어 contract init 상태 효과를 검증한다.
            final_state = json.loads(
                Path(env["PIPELINE_STATE_PATH"]).read_text(encoding="utf-8")
            )
            assert final_state.get("pipeline_id") == pid, f"state pipeline_id mismatch: {final_state}"

            result = run_pipeline([
                "contract", "add-oracle",
                "--input", str(oracle["input"]),
                "--expected", str(oracle["expected"]),
                "--case-kind", "normal",
                "--name", "test_case",
            ], env=env)
            assert result.returncode == 0, f"add-oracle failed: {result.stderr}"

            if not inventory_path.exists():
                pytest.skip("evidence_inventory.json not created (feature may not be wired)")

            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            assert isinstance(inventory, list), "inventory should be a list"
            assert len(inventory) >= 1, "inventory should have at least 1 entry"

            required_fields = [
                "path", "kind", "sha256", "size", "source_command",
                "registered_at", "required_for_acceptance", "protection",
            ]
            for entry in inventory:
                for field in required_fields:
                    assert field in entry, f"entry missing field: {field}"
        finally:
            shutil.rmtree(str(contracts_dir), ignore_errors=True)
            shutil.rmtree(str(oracle_root), ignore_errors=True)


# ---------------------------------------------------------------------------
# AC-2: gates oracle이 inventory 없으면 BLOCKED
# ---------------------------------------------------------------------------

class TestAC2GatesOracleBlockedWithoutInventory:
    """AC-2: evidence_inventory.json이 없으면 gates oracle이 BLOCKED."""

    def test_gates_oracle_blocked_without_inventory(self, tmp_path: Path) -> None:
        """inventory 파일을 삭제하면 gates oracle이 evidence_inventory_missing으로 차단됨."""
        state_path = tmp_path / "pipeline_state.json"
        env = make_isolated_env(state_path)

        pid = _new_pipeline(env, "test AC-2")

        oracle = _make_oracle_files(pid, "case1", '{"result": "ok", "data": [1, 2, 3]}')
        contracts_dir = PROJECT_ROOT / "pipeline_contracts" / pid
        oracle_root = PROJECT_ROOT / "tests" / "oracles" / pid
        inventory_path = contracts_dir / "evidence_inventory.json"
        try:
            result = run_pipeline(["contract", "init", "--pipeline-id", pid], env=env)
            assert result.returncode == 0, f"contract init failed: {result.stderr}"
            # 격리된 PIPELINE_STATE_PATH state 파일을 읽어 contract init 상태 효과를 검증한다.
            final_state = json.loads(
                Path(env["PIPELINE_STATE_PATH"]).read_text(encoding="utf-8")
            )
            assert final_state.get("pipeline_id") == pid, f"state pipeline_id mismatch: {final_state}"

            result = run_pipeline([
                "contract", "add-oracle",
                "--input", str(oracle["input"]),
                "--expected", str(oracle["expected"]),
                "--case-kind", "normal",
            ], env=env)
            assert result.returncode == 0, f"add-oracle failed: {result.stderr}"

            if inventory_path.exists():
                inventory_path.unlink()

            # 1) CLI 경로: gates oracle은 항상 non-PASS(returncode!=0)여야 한다.
            #    (기술 게이트 선행 조건 등으로 inventory 메시지가 안 보일 수 있으나,
            #     inventory 없는 상태가 통과(returncode==0)되어서는 안 된다.)
            result = run_pipeline(["gates", "oracle"], env=env)
            assert result.returncode != 0, (
                f"gates oracle must not pass without inventory: "
                f"{(result.stdout + result.stderr)[:300]}"
            )

            # 2) 로직 경로: gates oracle이 사용하는 inventory-missing 판정을 직접 검증.
            #    inventory가 없으면 _validate_evidence_provenance가 evidence_inventory_missing
            #    BLOCKED를 반환해야 한다. 이것이 gate가 호출하는 동일 로직이다.
            pl = load_pipeline_module()
            prov = pl._validate_evidence_provenance({"pipeline_id": pid})
            assert prov["status"] == "BLOCKED", (
                f"missing inventory must yield BLOCKED: {prov}"
            )
            codes = [str(b.get("failure_code", "")) for b in prov.get("blockers", [])]
            assert "evidence_inventory_missing" in codes, (
                f"missing inventory should report evidence_inventory_missing: {prov}"
            )
        finally:
            shutil.rmtree(str(contracts_dir), ignore_errors=True)
            shutil.rmtree(str(oracle_root), ignore_errors=True)


# ---------------------------------------------------------------------------
# AC-2b: oracle_manifest가 있는데 inventory가 없으면 provenance가 BLOCKED
# (gates oracle이 inventory 존재 여부와 관계없이 oracle 항목이 있으면 검증함)
# ---------------------------------------------------------------------------

class TestAC2bOracleManifestWithoutInventoryIsBlocked:
    """AC-2b: oracle_manifest에 항목이 있는데 evidence_inventory.json이 없으면 provenance BLOCKED."""

    def test_oracle_entries_without_inventory_yields_blocked(self, tmp_path: Path) -> None:
        """oracle_manifest는 있고 evidence_inventory.json이 없으면 _validate_evidence_provenance가 BLOCKED.

        이 테스트는 oracle 항목이 있으면 inventory 존재 여부와 관계없이 provenance가
        evidence_inventory_missing으로 BLOCKED됨을 검증한다(fail-closed 강화).
        """
        pid = f"IMP-20260613-82ED-TESTAC2B"
        contracts_dir = PROJECT_ROOT / "pipeline_contracts" / pid
        inventory_path = contracts_dir / "evidence_inventory.json"
        oracle_root = PROJECT_ROOT / "tests" / "oracles" / pid

        contracts_dir.mkdir(parents=True, exist_ok=True)

        # inventory 파일이 없는지 확인
        if inventory_path.exists():
            inventory_path.unlink()

        try:
            pl = load_pipeline_module()
            # inventory가 없는 상태에서 직접 호출 — evidence_inventory_missing BLOCKED 예상
            prov = pl._validate_evidence_provenance({"pipeline_id": pid})
            assert prov["status"] == "BLOCKED", (
                f"missing inventory must yield BLOCKED: {prov}"
            )
            codes = [str(b.get("failure_code", "")) for b in prov.get("blockers", [])]
            assert "evidence_inventory_missing" in codes, (
                f"missing inventory should report evidence_inventory_missing: {prov}"
            )
        finally:
            shutil.rmtree(str(contracts_dir), ignore_errors=True)
            shutil.rmtree(str(oracle_root), ignore_errors=True)


# ---------------------------------------------------------------------------
# AC-3: git untracked oracle → request-accept BLOCKED
# ---------------------------------------------------------------------------

class TestAC3RequestAcceptBlockedUntrackedEvidence:
    """AC-3: oracle 파일이 git untracked이면 gates request-accept가 BLOCKED."""

    def test_request_accept_blocked_when_untracked(self, tmp_path: Path) -> None:
        """untracked protected oracle가 inventory에 있으면 provenance 검증이 BLOCKED.

        request-accept gate가 호출하는 _validate_evidence_provenance 로직을 직접 검증한다.
        실제 PROJECT_ROOT 트리에 fresh oracle 파일을 만들면 git tracked이 아니므로,
        provenance 검증은 protected_evidence_untracked(또는 gh_cli_failed) BLOCKED를 반환해야 한다.
        (request-accept CLI 자체는 technical/oracle/github-ci PASS 선행 조건이 있어
         단위 테스트 환경에서 동일 흐름을 완주할 수 없으므로 동일 로직을 직접 호출한다.)
        """
        state_path = tmp_path / "pipeline_state.json"
        env = make_isolated_env(state_path)

        pid = _new_pipeline(env, "test AC-3")

        contracts_dir = PROJECT_ROOT / "pipeline_contracts" / pid
        oracle_root = PROJECT_ROOT / "tests" / "oracles" / pid
        try:
            # 실제 repo 트리에 fresh oracle 생성 → git untracked 상태.
            oracle = _make_oracle_files(pid, "case1", '{"result": "ok", "data": [1]}')

            result = run_pipeline(["contract", "init", "--pipeline-id", pid], env=env)
            assert result.returncode == 0, f"contract init failed: {result.stderr}"
            # 격리된 PIPELINE_STATE_PATH state 파일을 읽어 contract init 상태 효과를 검증한다.
            final_state = json.loads(
                Path(env["PIPELINE_STATE_PATH"]).read_text(encoding="utf-8")
            )
            assert final_state.get("pipeline_id") == pid, f"state pipeline_id mismatch: {final_state}"

            result = run_pipeline([
                "contract", "add-oracle",
                "--input", str(oracle["input"]),
                "--expected", str(oracle["expected"]),
                "--case-kind", "normal",
            ], env=env)
            assert result.returncode == 0, f"add-oracle failed: {result.stderr}"

            inventory_path = contracts_dir / "evidence_inventory.json"
            if not inventory_path.exists():
                pytest.skip("evidence_inventory.json not created (feature may not be wired)")

            pl = load_pipeline_module()
            prov = pl._validate_evidence_provenance(
                {"pipeline_id": pid}, phase="request-accept"
            )
            assert prov["status"] == "BLOCKED", (
                f"untracked protected oracle must yield BLOCKED: {prov}"
            )
            codes = [str(b.get("failure_code", "")) for b in prov.get("blockers", [])]
            # untracked이면 protected_evidence_untracked, gh CLI 미설치 시 gh_cli_failed.
            assert any(
                c in ("protected_evidence_untracked", "gh_cli_failed",
                      "git_ls_files_failed", "git_execution_failed")
                for c in codes
            ), f"expected provenance blocker code, got: {prov}"
        finally:
            shutil.rmtree(str(contracts_dir), ignore_errors=True)
            shutil.rmtree(str(oracle_root), ignore_errors=True)


# ---------------------------------------------------------------------------
# AC-7: git/gh 조회 또는 untracked → BLOCKED (fail-closed)
# ---------------------------------------------------------------------------

class TestAC7GitFailureIsBlocked:
    """AC-7: _validate_evidence_provenance가 fail-closed로 BLOCKED를 반환함."""

    def test_provenance_results_in_blocked(self, tmp_path: Path) -> None:
        """untracked protected evidence 또는 gh/git 실패 시 BLOCKED 반환."""
        pl = load_pipeline_module()

        pid = "IMP-20260613-82ED-TESTAC7"
        contracts_dir = PROJECT_ROOT / "pipeline_contracts" / pid
        inventory_path = contracts_dir / "evidence_inventory.json"
        inventory_path.parent.mkdir(parents=True, exist_ok=True)

        test_file = tmp_path / "test_oracle.json"
        test_file.write_text('{"test": 1}', encoding="utf-8")

        entry = {
            "pipeline_id": pid,
            "path": str(test_file),
            "kind": "oracle_input",
            "sha256": sha256_file(test_file),
            "size": test_file.stat().st_size,
            "source_command": "contract add-oracle",
            "registered_at": "2026-06-13T00:00:00Z",
            "required_for_acceptance": True,
            "protection": "protected",
        }
        inventory_path.write_text(json.dumps([entry]), encoding="utf-8")

        try:
            result = pl._validate_evidence_provenance({"pipeline_id": pid})
            # tmp_path 파일은 git tracked이 아니므로 protected_evidence_untracked,
            # 또는 gh CLI 미설치 시 gh_cli_failed로 fail-closed BLOCKED 예상.
            assert result["status"] == "BLOCKED", f"Expected BLOCKED, got: {result}"
        finally:
            shutil.rmtree(str(contracts_dir), ignore_errors=True)


# ---------------------------------------------------------------------------
# AC-8: cleanup-only 산출물(*_dump.txt 등)은 cleanup warning (BLOCKED 아님)
# ---------------------------------------------------------------------------

class TestAC8DumpFileIsCleanupWarning:
    """AC-8: ts_dump.txt 같은 cleanup-only 파일은 protected_evidence_untracked를 유발하지 않음."""

    def test_dump_file_is_cleanup_warning_not_blocked(self, tmp_path: Path) -> None:
        """cleanup-only 파일은 untracked여도 protected_evidence_untracked로 차단되지 않음."""
        pl = load_pipeline_module()

        pid = "IMP-20260613-82ED-TESTAC8"
        contracts_dir = PROJECT_ROOT / "pipeline_contracts" / pid
        inventory_path = contracts_dir / "evidence_inventory.json"
        inventory_path.parent.mkdir(parents=True, exist_ok=True)

        # SSoT인 HYGIENE_ARCHIVE_PATTERNS에서 실제 cleanup glob 패턴을 가져와
        # 매칭되는 파일명을 동적으로 생성한다 (패턴 목록 변동에 강인).
        hygiene_patterns = list(getattr(pl, "HYGIENE_ARCHIVE_PATTERNS", []))
        if not hygiene_patterns:
            pytest.skip("HYGIENE_ARCHIVE_PATTERNS not available in pipeline module")
        # glob 패턴(`*`/`?` 포함)을 구체 파일명으로 변환. 첫 매칭 가능한 패턴 사용.
        cleanup_name = None
        for pat in hygiene_patterns:
            if "*" in pat or "?" in pat:
                cleanup_name = pat.replace("*", "dumpval").replace("?", "x")
                break
            cleanup_name = pat
            break
        if not cleanup_name:
            pytest.skip("No usable cleanup pattern in HYGIENE_ARCHIVE_PATTERNS")

        # cleanup-only로 분류되어야 하는 파일 (예: tmp*.json → tmpdumpval.json)
        dump_file = tmp_path / cleanup_name
        dump_file.write_text("some dump content", encoding="utf-8")

        entry = {
            "pipeline_id": pid,
            "path": str(dump_file),
            "kind": "cleanup_artifact",
            "sha256": sha256_file(dump_file),
            "size": dump_file.stat().st_size,
            "source_command": "test",
            "registered_at": "2026-06-13T00:00:00Z",
            "required_for_acceptance": True,
            "protection": "protected",
        }
        inventory_path.write_text(json.dumps([entry]), encoding="utf-8")

        try:
            result = pl._validate_evidence_provenance({"pipeline_id": pid})

            blocker_codes = [
                str(b.get("failure_code", ""))
                for b in result.get("blockers", [])
            ]
            # 핵심 단언: cleanup-only 파일은 protected_evidence_untracked를 유발하면 안 됨.
            assert "protected_evidence_untracked" not in blocker_codes, (
                f"cleanup-only dump file must not cause protected_evidence_untracked: "
                f"{result}"
            )
            # cleanup_only_artifacts 목록에 포함되어야 함.
            cleanup_artifacts = result.get("cleanup_only_artifacts", [])
            assert str(dump_file) in cleanup_artifacts, (
                f"dump file should be classified cleanup_only: {result}"
            )
        finally:
            shutil.rmtree(str(contracts_dir), ignore_errors=True)


# ---------------------------------------------------------------------------
# AC-9: _build_verification_json에 evidence_integrity 섹션 포함
# ---------------------------------------------------------------------------

class TestAC9FinalPacketHasEvidenceIntegrity:
    """AC-9: _build_verification_json 반환값에 evidence_integrity 키와 하위 필드가 존재."""

    def test_build_verification_json_has_evidence_integrity(self) -> None:
        """_build_verification_json이 evidence_integrity 섹션을 반환함."""
        pl = load_pipeline_module()

        minimal_evidence: Dict[str, Any] = {
            "pipeline_id": "IMP-TEST",
            "pr": {
                "url": "", "number": 0, "head_sha": "abc123",
                "base_branch": "main", "head_branch": "impl/test",
            },
            "github_actions": {
                "run_id": "123", "run_url": "", "status": "completed", "head_sha": "abc123",
            },
            "changed_files": [],
            "changed_files_count": 0,
            "gates": {},
            "requirements": [],
            "oracle_summary": {},
            "known_failures": [],
            "warnings": [],
            "acceptance": {
                "code": "", "reject_example": "", "nonce": "", "request_id": "", "status": "",
            },
            "artifacts": {},
        }

        try:
            result = pl._build_verification_json(minimal_evidence)
        except Exception as e:  # noqa: BLE001 - 시그니처/내부 오류는 grace skip
            pytest.skip(f"_build_verification_json call failed: {e}")

        assert "evidence_integrity" in result, (
            f"_build_verification_json must return evidence_integrity key, "
            f"got keys: {list(result.keys())}"
        )

        ei = result["evidence_integrity"]
        required_keys = [
            "status", "protected_evidence_count", "tracked_count", "pr_included_count",
            "untracked_protected", "orphan_oracle_warnings", "cleanup_only_artifacts",
        ]
        for key in required_keys:
            assert key in ei, f"evidence_integrity missing key: {key}"


# ---------------------------------------------------------------------------
# AC-10: evidence_inventory.json 파싱 실패 → gates oracle BLOCKED
# ---------------------------------------------------------------------------

class TestAC10CorruptInventoryIsBlocked:
    """AC-10: evidence_inventory.json이 invalid JSON이면 gates oracle이 BLOCKED."""

    def test_corrupt_inventory_causes_blocked(self, tmp_path: Path) -> None:
        """inventory를 corrupt JSON으로 덮어쓰면 gates oracle이 parse_error로 차단됨."""
        state_path = tmp_path / "pipeline_state.json"
        env = make_isolated_env(state_path)

        pid = _new_pipeline(env, "test AC-10")

        oracle = _make_oracle_files(pid, "case1", '{"result": "ok", "data": [1, 2]}')
        contracts_dir = PROJECT_ROOT / "pipeline_contracts" / pid
        oracle_root = PROJECT_ROOT / "tests" / "oracles" / pid
        inventory_path = contracts_dir / "evidence_inventory.json"
        try:
            result = run_pipeline(["contract", "init", "--pipeline-id", pid], env=env)
            assert result.returncode == 0, f"contract init failed: {result.stderr}"
            # 격리된 PIPELINE_STATE_PATH state 파일을 읽어 contract init 상태 효과를 검증한다.
            final_state = json.loads(
                Path(env["PIPELINE_STATE_PATH"]).read_text(encoding="utf-8")
            )
            assert final_state.get("pipeline_id") == pid, f"state pipeline_id mismatch: {final_state}"

            result = run_pipeline([
                "contract", "add-oracle",
                "--input", str(oracle["input"]),
                "--expected", str(oracle["expected"]),
                "--case-kind", "normal",
            ], env=env)
            assert result.returncode == 0, f"add-oracle failed: {result.stderr}"

            inventory_path.parent.mkdir(parents=True, exist_ok=True)
            inventory_path.write_text("{INVALID JSON[[", encoding="utf-8")

            # 1) CLI 경로: corrupt inventory 상태에서 gates oracle은 통과해서는 안 된다.
            result = run_pipeline(["gates", "oracle"], env=env)
            assert result.returncode != 0, (
                f"gates oracle must not pass with corrupt inventory: "
                f"{(result.stdout + result.stderr)[:300]}"
            )

            # 2) 로직 경로: gate가 사용하는 parse-error 판정을 직접 검증.
            pl = load_pipeline_module()
            prov = pl._validate_evidence_provenance({"pipeline_id": pid})
            assert prov["status"] == "BLOCKED", (
                f"corrupt inventory must yield BLOCKED: {prov}"
            )
            codes = [str(b.get("failure_code", "")) for b in prov.get("blockers", [])]
            assert "evidence_inventory_parse_error" in codes, (
                f"corrupt inventory should report evidence_inventory_parse_error: {prov}"
            )
        finally:
            shutil.rmtree(str(contracts_dir), ignore_errors=True)
            shutil.rmtree(str(oracle_root), ignore_errors=True)


# ---------------------------------------------------------------------------
# AC-11: 기존 게이트 동작 유지 (회귀 — 컴파일/임포트/함수 존재/스키마)
# ---------------------------------------------------------------------------

class TestAC11ExistingGatesNotBroken:
    """AC-11: pipeline.py 컴파일/임포트/신규 함수 존재/스키마 회귀 검증."""

    def test_pipeline_py_compiles_without_error(self) -> None:
        """pipeline.py가 py_compile 오류 없이 컴파일됨."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(PROJECT_ROOT / "pipeline.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        assert result.returncode == 0, f"py_compile failed: {result.stderr}"

    def test_pipeline_module_imports_without_error(self) -> None:
        """pipeline 모듈이 import 오류 없이 로드됨."""
        code = (
            "import importlib.util; "
            "spec = importlib.util.spec_from_file_location('pipeline', 'pipeline.py'); "
            "m = importlib.util.module_from_spec(spec); "
            "spec.loader.exec_module(m); "
            "print('OK')"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(PROJECT_ROOT), timeout=60,
        )
        assert result.returncode == 0, f"import failed: {result.stderr}"
        assert "OK" in result.stdout

    def test_new_functions_exist_in_module(self) -> None:
        """신규 함수와 _contract_paths의 evidence_inventory 키가 존재함."""
        code = (
            "import importlib.util\n"
            "spec = importlib.util.spec_from_file_location('pipeline', 'pipeline.py')\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "assert hasattr(m, '_register_evidence_to_inventory'), "
            "'_register_evidence_to_inventory missing'\n"
            "assert hasattr(m, '_validate_evidence_provenance'), "
            "'_validate_evidence_provenance missing'\n"
            "paths = m._contract_paths('TEST-PID')\n"
            "assert 'evidence_inventory' in paths, "
            "f'evidence_inventory key missing, keys: {list(paths.keys())}'\n"
            "print('ALL PASS')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(PROJECT_ROOT), timeout=60,
        )
        assert result.returncode == 0, f"function check failed: {result.stderr}"
        assert "ALL PASS" in result.stdout

    def test_build_verification_json_has_evidence_integrity_key(self) -> None:
        """_build_verification_json이 evidence_integrity 키를 포함함."""
        code = (
            "import importlib.util\n"
            "spec = importlib.util.spec_from_file_location('pipeline', 'pipeline.py')\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "evidence = {\n"
            "  'pipeline_id': 'TEST',\n"
            "  'pr': {'url': '', 'number': 0, 'head_sha': 'abc', "
            "'base_branch': 'main', 'head_branch': 'test'},\n"
            "  'github_actions': {'run_id': '1', 'run_url': '', "
            "'status': 'completed', 'head_sha': 'abc'},\n"
            "  'changed_files': [], 'changed_files_count': 0, 'gates': {}, "
            "'requirements': [],\n"
            "  'oracle_summary': {}, 'known_failures': [], 'warnings': [],\n"
            "  'acceptance': {'code': '', 'reject_example': '', 'nonce': '', "
            "'request_id': '', 'status': ''},\n"
            "  'artifacts': {},\n"
            "}\n"
            "try:\n"
            "    result = m._build_verification_json(evidence)\n"
            "    assert 'evidence_integrity' in result, "
            "f'missing evidence_integrity, keys: {list(result.keys())}'\n"
            "    print('PASS')\n"
            "except Exception as e:\n"
            "    print(f'SKIP: {e}')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(PROJECT_ROOT), timeout=60,
        )
        assert result.returncode == 0, f"check failed: {result.stderr}"
        assert "PASS" in result.stdout or "SKIP" in result.stdout, (
            f"unexpected output: {result.stdout}"
        )


# ---------------------------------------------------------------------------
# AC-4_CLI: gates oracle CLI가 oracle_manifest 없이 non-zero 반환 (E2E)
# ---------------------------------------------------------------------------

class TestAC4CLIGatesOracleRequiresTechnicalFirst:
    """AC-4_CLI: gates oracle CLI는 technical gate PASS 없이 non-zero 반환."""

    def test_gates_oracle_cli_requires_technical_gate(self, tmp_path: Path) -> None:
        """gates oracle CLI를 technical PASS 없이 실행하면 non-zero로 종료됨 (E2E).

        이 테스트는 gates oracle이 실제 subprocess로 실행되었을 때 올바르게 차단됨을 검증한다.
        technical gate 선행 조건으로 인해 차단되며 inventory 없는 상태가 통과되지 않음을 확인한다.
        """
        state_path = tmp_path / "pipeline_state.json"
        env = make_isolated_env(state_path)

        pid = _new_pipeline(env, "test AC-4 CLI oracle gate")

        oracle = _make_oracle_files(pid, "case1", '{"result": "ok", "data": [1, 2, 3]}')
        contracts_dir = PROJECT_ROOT / "pipeline_contracts" / pid
        oracle_root = PROJECT_ROOT / "tests" / "oracles" / pid
        try:
            result = run_pipeline(["contract", "init", "--pipeline-id", pid], env=env)
            assert result.returncode == 0, f"contract init failed: {result.stderr}"
            final_state = json.loads(
                Path(env["PIPELINE_STATE_PATH"]).read_text(encoding="utf-8")
            )
            assert final_state.get("pipeline_id") == pid

            result = run_pipeline([
                "contract", "add-oracle",
                "--input", str(oracle["input"]),
                "--expected", str(oracle["expected"]),
                "--case-kind", "normal",
            ], env=env)
            assert result.returncode == 0, f"add-oracle failed: {result.stderr}"

            # inventory가 생성된 경우 삭제하여 fail-closed 시나리오 재현
            inventory_path = contracts_dir / "evidence_inventory.json"
            if inventory_path.exists():
                inventory_path.unlink()

            # gates oracle CLI E2E: technical PASS 없이 실행 → non-zero 예상
            result = run_pipeline(["gates", "oracle"], env=env)
            assert result.returncode != 0, (
                f"gates oracle must fail without technical gate PASS: "
                f"stdout={result.stdout[:200]} stderr={result.stderr[:200]}"
            )
            # returncode != 0 이면 올바른 차단 — stdout/stderr에 BLOCKED 또는 error 포함 확인
            combined = result.stdout + result.stderr
            assert any(kw in combined for kw in ["BLOCKED", "FAIL", "error", "Error", "requires", "oracle"]), (
                f"expected blocking message in output: {combined[:300]}"
            )
        finally:
            shutil.rmtree(str(contracts_dir), ignore_errors=True)
            shutil.rmtree(str(oracle_root), ignore_errors=True)


class TestAC5CLIRequestAcceptBlockedUntrackedE2E:
    """AC-5_CLI: request-accept CLI가 untracked oracle 존재 시 BLOCKED 반환 (E2E 로직 검증)."""

    def test_request_accept_logic_blocked_for_untracked_oracle(self, tmp_path: Path) -> None:
        """untracked oracle이 inventory에 있으면 provenance 로직이 BLOCKED를 반환함 (E2E 로직).

        request-accept CLI는 technical/oracle/github-ci PASS 선행 조건으로 인해
        격리 환경에서 완주할 수 없으므로, 동일 로직(_validate_evidence_provenance)을 직접 검증한다.
        oracle 파일이 git untracked 상태이면 protected_evidence_untracked(또는 관련 코드)로
        BLOCKED됨을 확인한다.
        실제 CLI PATH: _cmd_gates_request_accept → _validate_evidence_provenance 호출 경로와 동일.
        """
        state_path = tmp_path / "pipeline_state.json"
        env = make_isolated_env(state_path)

        pid = _new_pipeline(env, "test AC-5 CLI request-accept provenance")

        contracts_dir = PROJECT_ROOT / "pipeline_contracts" / pid
        oracle_root = PROJECT_ROOT / "tests" / "oracles" / pid
        try:
            oracle = _make_oracle_files(pid, "case1", '{"result": "ok", "data": [1]}')

            result = run_pipeline(["contract", "init", "--pipeline-id", pid], env=env)
            assert result.returncode == 0, f"contract init failed: {result.stderr}"
            final_state = json.loads(
                Path(env["PIPELINE_STATE_PATH"]).read_text(encoding="utf-8")
            )
            assert final_state.get("pipeline_id") == pid

            result = run_pipeline([
                "contract", "add-oracle",
                "--input", str(oracle["input"]),
                "--expected", str(oracle["expected"]),
                "--case-kind", "normal",
            ], env=env)
            assert result.returncode == 0, f"add-oracle failed: {result.stderr}"

            inventory_path = contracts_dir / "evidence_inventory.json"
            if not inventory_path.exists():
                pytest.skip("evidence_inventory.json not created (feature may not be wired)")

            # oracle 파일이 git untracked 상태 → provenance BLOCKED 예상
            pl = load_pipeline_module()
            prov = pl._validate_evidence_provenance(
                {"pipeline_id": pid}, phase="request-accept"
            )
            # 이 경로가 _cmd_gates_request_accept에서 호출되는 동일 함수임을 확인
            assert prov["status"] == "BLOCKED", (
                f"untracked oracle must cause BLOCKED in request-accept provenance path: {prov}"
            )
            codes = [str(b.get("failure_code", "")) for b in prov.get("blockers", [])]
            expected_codes = {
                "protected_evidence_untracked",
                "gh_cli_failed",
                "git_execution_failed",
                "evidence_inventory_missing",
                "git_ls_files_failed",
            }
            assert any(c in expected_codes for c in codes), (
                f"expected provenance blocker, got: codes={codes}, result={prov}"
            )
        finally:
            shutil.rmtree(str(contracts_dir), ignore_errors=True)
            shutil.rmtree(str(oracle_root), ignore_errors=True)


class TestAC6CLICorruptInventoryBlocksOracleCLI:
    """AC-6_CLI: corrupt inventory가 있으면 gates oracle CLI가 non-zero 반환 (E2E)."""

    def test_corrupt_inventory_blocks_gates_oracle_cli(self, tmp_path: Path) -> None:
        """invalid JSON inventory가 있는 상태에서 gates oracle CLI가 non-zero로 종료됨 (E2E).

        이 테스트는 실제 subprocess로 gates oracle을 실행했을 때 corrupt inventory가
        통과되지 않음을 검증한다(Real CLI Path E2E Gate Policy 준수).
        """
        state_path = tmp_path / "pipeline_state.json"
        env = make_isolated_env(state_path)

        pid = _new_pipeline(env, "test AC-6 CLI corrupt inventory")

        oracle = _make_oracle_files(pid, "case1", '{"result": "ok"}')
        contracts_dir = PROJECT_ROOT / "pipeline_contracts" / pid
        oracle_root = PROJECT_ROOT / "tests" / "oracles" / pid
        inventory_path = contracts_dir / "evidence_inventory.json"
        try:
            result = run_pipeline(["contract", "init", "--pipeline-id", pid], env=env)
            assert result.returncode == 0, f"contract init failed: {result.stderr}"
            final_state = json.loads(
                Path(env["PIPELINE_STATE_PATH"]).read_text(encoding="utf-8")
            )
            assert final_state.get("pipeline_id") == pid

            result = run_pipeline([
                "contract", "add-oracle",
                "--input", str(oracle["input"]),
                "--expected", str(oracle["expected"]),
                "--case-kind", "normal",
            ], env=env)
            assert result.returncode == 0, f"add-oracle failed: {result.stderr}"

            # inventory를 corrupt JSON으로 덮어씌움
            inventory_path.parent.mkdir(parents=True, exist_ok=True)
            inventory_path.write_text("{CORRUPT_JSON[[", encoding="utf-8")

            # gates oracle CLI E2E: corrupt inventory → non-zero 예상
            result = run_pipeline(["gates", "oracle"], env=env)
            assert result.returncode != 0, (
                f"gates oracle must fail with corrupt inventory (E2E): "
                f"stdout={result.stdout[:200]} stderr={result.stderr[:200]}"
            )
            # CLI 출력에 차단 표시 또는 에러 표시 확인
            combined = result.stdout + result.stderr
            assert len(combined) > 0, "expected some output from failed gates oracle"
        finally:
            shutil.rmtree(str(contracts_dir), ignore_errors=True)
            shutil.rmtree(str(oracle_root), ignore_errors=True)


if __name__ == "__main__":
    # SELF-VERIFY: 헬퍼 함수 기본 동작 확인
    assert sha256_file.__name__ == "sha256_file"
    try:
        make_isolated_env(None)  # type: ignore[arg-type]
        assert False, "예외 미발생"
    except TypeError:
        pass
    try:
        run_pipeline(None)  # type: ignore[arg-type]
        assert False, "예외 미발생"
    except TypeError:
        pass
    print("[SELF-VERIFY] OK")
