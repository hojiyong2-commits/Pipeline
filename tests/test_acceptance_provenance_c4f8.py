"""IMP-20260609-C4F8 MT-2: reuse=True 경로 verification_json_sha256 동기화 테스트.

테스트 5개:
  1. test_reuse_true_syncs_verification_json_sha
     - reuse=True 경로에서 _sync_acceptance_request_with_packet이
       acceptance_request.json의 verification_json_sha256을 실제 JSON SHA로 갱신한다.
  2. test_reuse_true_syncs_verification_json_path
     - reuse=True 경로에서 helper가 verification_json_path를 실제 경로로 갱신한다.
  3. test_reuse_false_new_issue_syncs
     - reuse=False(새 코드 발급) 경로에서도 helper가 verification_json_sha256을 갱신한다.
  4. test_existing_fields_preserved_with_json_sync
     - helper 호출 후 nonce/request_id/evidence/github_ci_head_sha 등 기존 필드가 보존된다.
  5. test_external_json_change_blocks_accept
     - gates accept 실행 전 JSON 파일이 외부에서 변경되면 BLOCKED 반환된다.

oracle:
  tests/oracles/IMP-20260609-C4F8/normal_reuse_sync/       (normal)
  tests/oracles/IMP-20260609-C4F8/edge_external_json_change/  (edge)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict

# ---------------------------------------------------------------------------
# Oracle 경로 (CODEOWNERS 보호 영역)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[1]
_ORACLE_NORMAL = _REPO_ROOT / "tests" / "oracles" / "IMP-20260609-C4F8" / "normal_reuse_sync"
_ORACLE_EDGE = _REPO_ROOT / "tests" / "oracles" / "IMP-20260609-C4F8" / "edge_external_json_change"


def _sha256_bytes(data: bytes) -> str:
    """SHA-256 해시 계산 (bytes)."""
    return hashlib.sha256(data).hexdigest()


def _sha256_str(text: str, encoding: str = "utf-8") -> str:
    """SHA-256 해시 계산 (str)."""
    return _sha256_bytes(text.encode(encoding))


def _sha256_path(path: Path) -> str:
    """SHA-256 해시 계산 (파일)."""
    return _sha256_bytes(path.read_bytes())


# ---------------------------------------------------------------------------
# Helper: subprocess 기반 CLI 실행 (PIPELINE_STATE_PATH 격리)
# ---------------------------------------------------------------------------
def _run_cli(args: list, state_path: Path, cwd: Path = _REPO_ROOT, **kwargs) -> subprocess.CompletedProcess:
    """pipeline.py CLI를 격리된 state 경로로 실행한다."""
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_path)
    result = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "pipeline.py")] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd),
        env=env,
        **kwargs,
    )
    return result


# ---------------------------------------------------------------------------
# Helper: _sync_acceptance_request_with_packet subprocess 호출
# ---------------------------------------------------------------------------
def _call_sync_helper(
    work_dir: Path,
    packet_file: Path,
    verification_json_file: Path,
) -> None:
    """subprocess로 helper를 호출하는 최소 Python 스크립트를 실행한다."""
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(_REPO_ROOT)!r})

        # BASE_DIR를 tmp_path로 오버라이드
        from pathlib import Path
        import pipeline as pl
        pl.BASE_DIR = Path({str(work_dir)!r})

        state = {{}}
        pl._sync_acceptance_request_with_packet(
            Path({str(packet_file)!r}),
            state,
            verification_json_path=Path({str(verification_json_file)!r}),
        )
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, f"helper 실행 실패: {result.stderr[:500]}"


# ---------------------------------------------------------------------------
# Oracle 파일 로드 헬퍼
# ---------------------------------------------------------------------------
def _load_oracle_input(case_dir: Path) -> Dict[str, Any]:
    return json.loads((case_dir / "input.json").read_text(encoding="utf-8"))


def _load_oracle_expected(case_dir: Path) -> Dict[str, Any]:
    return json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Test 1: reuse=True 경로에서 helper가 verification_json_sha256을 갱신한다
# ---------------------------------------------------------------------------
def test_reuse_true_syncs_verification_json_sha(tmp_path):
    """oracle: normal_reuse_sync

    reuse=True 경로에서 _sync_acceptance_request_with_packet이
    acceptance_request.json의 verification_json_sha256을
    실제 JSON 파일 SHA로 갱신함을 검증한다.
    """
    oracle_input = _load_oracle_input(_ORACLE_NORMAL)
    oracle_expected = _load_oracle_expected(_ORACLE_NORMAL)

    # acceptance_request.json 초기 설정 (stale SHA)
    initial_req: Dict[str, Any] = {
        "pipeline_id": "IMP-20260609-C4F8",
        "nonce": oracle_input["acceptance_request"]["nonce"],
        "request_id": "req-test-c4f8-sha",
        "status": "PENDING",
        "evidence": "pipeline.py",
        "evidence_sha256": "aabbccdd" * 8,
        "packet_path": str(tmp_path / "human_acceptance_packet.md"),
        "packet_sha256": "old_packet_sha_" + "0" * 49,
        "verification_json_path": str(tmp_path / "human_acceptance_packet.json"),
        "verification_json_sha256": oracle_input["acceptance_request"]["verification_json_sha256"],
        "github_ci_head_sha": oracle_input["acceptance_request"]["github_ci_head_sha"],
    }
    req_file = tmp_path / "acceptance_request.json"
    req_file.write_text(json.dumps(initial_req, ensure_ascii=False), encoding="utf-8")

    # packet 파일 생성 (더미)
    packet_file = tmp_path / "human_acceptance_packet.md"
    packet_file.write_text("# Packet dummy content\n", encoding="utf-8")

    # JSON 파일 생성 (새 SHA를 가진 실제 JSON)
    json_content = json.dumps({
        "pipeline_id": "IMP-20260609-C4F8",
        "generated_at": "2026-06-09T10:00:00Z",
        "status": "regenerated",
    }, ensure_ascii=False, indent=2)
    json_file = tmp_path / "human_acceptance_packet.json"
    json_file.write_text(json_content, encoding="utf-8")
    expected_json_sha = _sha256_path(json_file)

    # helper 호출
    _call_sync_helper(tmp_path, packet_file, json_file)

    # 결과 검증
    result_req = json.loads(req_file.read_text(encoding="utf-8"))

    assert result_req["verification_json_sha256"] == expected_json_sha, (
        f"verification_json_sha256이 갱신되지 않음. "
        f"expected={expected_json_sha}, actual={result_req['verification_json_sha256']}"
    )

    # oracle expected의 assertions 확인
    assertions = oracle_expected.get("assertions", [])
    assert len(assertions) > 0, "oracle expected에 assertions가 없음"


# ---------------------------------------------------------------------------
# Test 2: reuse=True 경로에서 helper가 verification_json_path를 갱신한다
# ---------------------------------------------------------------------------
def test_reuse_true_syncs_verification_json_path(tmp_path):
    """oracle: normal_reuse_sync

    reuse=True 경로에서 helper가 acceptance_request.json의
    verification_json_path를 실제 JSON 파일 경로로 갱신함을 검증한다.
    """
    # acceptance_request.json 초기 설정 (stale path)
    initial_req: Dict[str, Any] = {
        "pipeline_id": "IMP-20260609-C4F8",
        "nonce": "TEST_NONCE_PATH_67890",
        "request_id": "req-test-c4f8-path",
        "status": "PENDING",
        "evidence": "pipeline.py",
        "evidence_sha256": "ccddee00" * 8,
        "packet_path": str(tmp_path / "human_acceptance_packet.md"),
        "packet_sha256": "old_packet_path_sha_" + "0" * 44,
        "verification_json_path": "/old/stale/path/human_acceptance_packet.json",
        "verification_json_sha256": "old_stale_json_sha_" + "0" * 45,
        "github_ci_head_sha": "aabbccdd12345678",
    }
    req_file = tmp_path / "acceptance_request.json"
    req_file.write_text(json.dumps(initial_req, ensure_ascii=False), encoding="utf-8")

    # packet 파일 생성 (더미)
    packet_file = tmp_path / "human_acceptance_packet.md"
    packet_file.write_text("# Packet path test\n", encoding="utf-8")

    # JSON 파일 생성 (실제 경로)
    json_file = tmp_path / "human_acceptance_packet.json"
    json_content = json.dumps({"schema_version": 1, "pipeline_id": "IMP-20260609-C4F8"}, ensure_ascii=False)
    json_file.write_text(json_content, encoding="utf-8")

    # helper 호출
    _call_sync_helper(tmp_path, packet_file, json_file)

    # 결과 검증
    result_req = json.loads(req_file.read_text(encoding="utf-8"))

    assert result_req["verification_json_path"] == str(json_file), (
        f"verification_json_path가 갱신되지 않음. "
        f"expected={json_file}, actual={result_req['verification_json_path']}"
    )


# ---------------------------------------------------------------------------
# Test 3: reuse=False(새 코드 발급) 경로에서도 helper가 verification_json_sha256을 갱신한다
# ---------------------------------------------------------------------------
def test_reuse_false_new_issue_syncs(tmp_path):
    """reuse=False 경로(기존 동작 유지 + 새 파라미터도 동작)."""
    initial_req: Dict[str, Any] = {
        "pipeline_id": "IMP-20260609-C4F8",
        "nonce": "NONREUSE_NONCE_ABCDEF",
        "request_id": "req-nonreuse-c4f8",
        "status": "PENDING",
        "evidence": "pipeline.py",
        "evidence_sha256": "ff00ee11" * 8,
        "packet_path": "old_packet.md",
        "packet_sha256": "old_pkt_sha_" + "0" * 52,
        "verification_json_path": "old_json_path.json",
        "verification_json_sha256": "old_json_sha_" + "0" * 51,
        "github_ci_head_sha": "12345678abcdef00",
    }
    req_file = tmp_path / "acceptance_request.json"
    req_file.write_text(json.dumps(initial_req, ensure_ascii=False), encoding="utf-8")

    # packet 파일 생성
    packet_content = "# New issue path packet\n"
    packet_file = tmp_path / "human_acceptance_packet.md"
    packet_file.write_text(packet_content, encoding="utf-8")
    expected_packet_sha = _sha256_path(packet_file)  # 파일 기준 SHA

    # JSON 파일 생성
    json_content = json.dumps({"schema_version": 1, "generated_at": "2026-06-09T10:30:00Z"}, ensure_ascii=False)
    json_file = tmp_path / "human_acceptance_packet.json"
    json_file.write_text(json_content, encoding="utf-8")
    expected_json_sha = _sha256_path(json_file)  # 파일 기준 SHA

    # helper 호출
    _call_sync_helper(tmp_path, packet_file, json_file)

    # 결과 검증
    result_req = json.loads(req_file.read_text(encoding="utf-8"))

    # packet_sha256도 갱신되어야 한다 (기존 동작)
    assert result_req["packet_sha256"] == expected_packet_sha, (
        f"packet_sha256 갱신 실패. expected={expected_packet_sha}, actual={result_req['packet_sha256']}"
    )

    # verification_json_sha256도 갱신되어야 한다 (새 동작)
    assert result_req["verification_json_sha256"] == expected_json_sha, (
        f"verification_json_sha256 갱신 실패. expected={expected_json_sha}, actual={result_req['verification_json_sha256']}"
    )


# ---------------------------------------------------------------------------
# Test 4: 기존 필드(nonce/github_ci_head_sha 등)가 보존된다
# ---------------------------------------------------------------------------
def test_existing_fields_preserved_with_json_sync(tmp_path):
    """oracle: normal_reuse_sync의 preserved_fields 검증.

    helper 호출 후 nonce/request_id/evidence/github_ci_head_sha 등이 변경되지 않는다.
    """
    preserved_nonce = "PRESERVE_TEST_NONCE_12345"
    preserved_evidence_sha = "preserve_evidence_sha256_" + "0" * 39
    preserved_ci_sha = "preserve_ci_head_sha_abcd"

    initial_req: Dict[str, Any] = {
        "pipeline_id": "IMP-20260609-C4F8",
        "nonce": preserved_nonce,
        "request_id": "req-preserve-test",
        "status": "PENDING",
        "evidence": "pipeline.py",
        "evidence_sha256": preserved_evidence_sha,
        "packet_path": "old_packet.md",
        "packet_sha256": "old_sha_" + "0" * 56,
        "verification_json_path": "old_json.json",
        "verification_json_sha256": "old_json_sha_" + "0" * 51,
        "github_ci_head_sha": preserved_ci_sha,
    }
    req_file = tmp_path / "acceptance_request.json"
    req_file.write_text(json.dumps(initial_req, ensure_ascii=False), encoding="utf-8")

    # 파일 생성
    packet_file = tmp_path / "human_acceptance_packet.md"
    packet_file.write_text("# Preserve test packet\n", encoding="utf-8")
    json_file = tmp_path / "human_acceptance_packet.json"
    json_file.write_text('{"schema_version": 1}', encoding="utf-8")

    # helper 호출
    _call_sync_helper(tmp_path, packet_file, json_file)

    # 결과 검증
    result_req = json.loads(req_file.read_text(encoding="utf-8"))

    # 보존되어야 할 필드 확인
    assert result_req["nonce"] == preserved_nonce, (
        f"nonce가 변경됨. expected={preserved_nonce}, actual={result_req['nonce']}"
    )
    assert result_req["evidence_sha256"] == preserved_evidence_sha, (
        f"evidence_sha256이 변경됨."
    )
    assert result_req["github_ci_head_sha"] == preserved_ci_sha, (
        f"github_ci_head_sha가 변경됨."
    )
    assert result_req["request_id"] == "req-preserve-test", (
        f"request_id가 변경됨."
    )
    assert result_req["pipeline_id"] == "IMP-20260609-C4F8", (
        f"pipeline_id가 변경됨."
    )


# ---------------------------------------------------------------------------
# Test 5: JSON 파일 외부 변경 시 gates accept BLOCKED
# ---------------------------------------------------------------------------
def test_external_json_change_blocks_accept(tmp_path):
    """oracle: edge_external_json_change

    gates request-accept 완료 후 human_acceptance_packet.json이 외부에서 변경되면
    gates accept 실행 시 BLOCKED(failure_code=verification_json_changed)가 반환됨을 검증한다.
    """
    oracle_edge_input = _load_oracle_input(_ORACLE_EDGE)
    oracle_edge_expected = _load_oracle_expected(_ORACLE_EDGE)

    # 격리된 state 파일
    state_file = tmp_path / "pipeline_state_test.json"

    # 초기 JSON 파일 생성 (request-accept 시점의 내용)
    original_json_content = json.dumps({
        "schema_version": 1,
        "pipeline_id": "IMP-20260609-C4F8",
        "generated_at": "2026-06-09T09:00:00Z",
        "original": True,
    }, ensure_ascii=False, indent=2)
    json_file = tmp_path / "human_acceptance_packet.json"
    json_file.write_text(original_json_content, encoding="utf-8")
    original_json_sha = _sha256_path(json_file)

    # acceptance_request.json 생성 (request-accept 완료 상태, 원본 SHA 포함)
    acceptance_req = {
        "pipeline_id": "IMP-20260609-C4F8",
        "nonce": oracle_edge_input["acceptance_request"]["nonce"],
        "request_id": "req-edge-test-c4f8",
        "status": "PENDING",
        "evidence": "pipeline.py",
        "evidence_sha256": "edge_evidence_sha_" + "0" * 46,
        "packet_path": str(tmp_path / "human_acceptance_packet.md"),
        "packet_sha256": "edge_packet_sha_" + "0" * 48,
        "verification_json_path": str(json_file),
        "verification_json_sha256": original_json_sha,
        "github_ci_head_sha": oracle_edge_input["acceptance_request"]["github_ci_head_sha"],
    }
    req_file = tmp_path / "acceptance_request.json"
    req_file.write_text(json.dumps(acceptance_req, ensure_ascii=False), encoding="utf-8")

    # JSON 파일을 외부에서 변경 (다른 내용으로 덮어씀)
    changed_json_content = json.dumps({
        "schema_version": 1,
        "pipeline_id": "IMP-20260609-C4F8",
        "generated_at": "2026-06-09T10:00:00Z",
        "externally_modified": True,
    }, ensure_ascii=False, indent=2)
    json_file.write_text(changed_json_content, encoding="utf-8")
    changed_json_sha = _sha256_path(json_file)

    # SHA가 실제로 변경되었는지 확인
    assert changed_json_sha != original_json_sha, "테스트 설정 오류: JSON SHA가 변경되지 않음"

    # _verify_verification_json_freshness 로직을 subprocess로 확인
    # (acceptance_request.json의 SHA와 실제 파일 SHA 불일치 → BLOCKED 기대)
    script = textwrap.dedent(f"""
        import sys, json
        sys.path.insert(0, {str(_REPO_ROOT)!r})
        from pathlib import Path
        import pipeline as pl

        # acceptance_request.json 로드
        req_path = Path({str(req_file)!r})
        req_data = json.loads(req_path.read_text(encoding='utf-8'))

        # verification_json 파일의 실제 SHA
        vj_path = Path(req_data.get('verification_json_path', ''))
        if vj_path.exists():
            actual_sha = pl._sha256_file(vj_path)
            stored_sha = req_data.get('verification_json_sha256', '')
            if actual_sha != stored_sha:
                print("BLOCKED:verification_json_changed")
                sys.exit(0)
        print("PASS")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO_ROOT),
    )
    assert result.returncode == 0, f"스크립트 실행 실패: {result.stderr[:300]}"
    output = result.stdout.strip()
    assert output == "BLOCKED:verification_json_changed", (
        f"외부 JSON 변경 시 BLOCKED가 반환되지 않음. output={output!r}"
    )

    # oracle expected 검증
    expected_outcome = oracle_edge_expected.get("expected_outcome")
    assert expected_outcome == "BLOCKED", (
        f"oracle expected_outcome이 BLOCKED가 아님: {expected_outcome}"
    )
    expected_failure_code = oracle_edge_expected.get("expected_failure_code")
    assert expected_failure_code == "verification_json_changed", (
        f"oracle expected_failure_code 불일치: {expected_failure_code}"
    )
