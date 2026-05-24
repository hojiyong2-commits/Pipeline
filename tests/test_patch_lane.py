"""
tests/test_patch_lane.py
------------------------
IMP-20260513-4C0B: Patch Lane + Incident Cluster 단위 테스트

auto-escalation 임계값: 15줄 (Q-PL-01 사용자 확정)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parent.parent
_PIPELINE = str(_ROOT / "pipeline.py")


def _run(args: list, input_str: Optional[str] = None) -> subprocess.CompletedProcess:
    """pipeline.py 서브커맨드 실행 헬퍼."""
    result = subprocess.run(
        [sys.executable, _PIPELINE] + args,
        capture_output=True,
        cwd=str(_ROOT),
        input=input_str.encode("utf-8") if input_str else None,
    )
    return result


def _parse_json(proc: subprocess.CompletedProcess) -> Dict[str, Any]:
    """표준 출력에서 JSON 파싱."""
    text = proc.stdout.decode("utf-8", errors="replace").strip()
    idx = text.find("{")
    if idx >= 0:
        try:
            return json.loads(text[idx:])
        except json.JSONDecodeError:
            pass
    return {}


def _make_patch_plan(
    file: str = "pipeline.py",
    function: str = "cmd_cluster",
    lines: int = 10,
    trust_root: bool = False,
    new_deps: bool = False,
    file_move: bool = False,
) -> str:
    """임시 patch_plan.json 내용 생성."""
    plan = {
        "schema_version": 1,
        "pipeline_id": "IMP-TEST-0000-0001",
        "patch_scope": {
            "file": file,
            "function": function,
            "expected_lines_changed_max": lines,
        },
        "forbidden": {
            "trust_root_changes": trust_root,
            "new_dependencies": new_deps,
            "file_move_or_delete": file_move,
            "packaging_changes": False,
        },
    }
    return json.dumps(plan, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Cluster 테스트 (4개)
# ---------------------------------------------------------------------------

class TestClusterCommands:
    """cluster detect / init / status / close 기능 테스트."""

    def test_cluster_detect_returns_json(self):
        """cluster detect는 JSON을 반환하고 match_found 필드를 포함해야 한다."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: cluster 명령은 pipeline_state.json이 아닌 별도 cluster 파일만 수정합니다
        proc = _run(["cluster", "detect"])
        assert proc.returncode == 0, (
            f"exit code: {proc.returncode}\n{proc.stderr.decode('utf-8', errors='replace')}"
        )
        data = _parse_json(proc)
        assert "match_found" in data, "match_found 필드 누락"
        assert isinstance(data.get("clusters"), list), "clusters 필드는 리스트여야 함"

    def test_cluster_init_creates_cluster(self):
        """cluster init은 CL- 접두사를 가진 클러스터 ID를 생성해야 한다."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: cluster 명령은 pipeline_state.json이 아닌 별도 cluster 파일만 수정합니다
        proc = _run(["cluster", "init", "--desc", "pytest test cluster"])
        assert proc.returncode == 0, (
            f"exit code: {proc.returncode}\n{proc.stderr.decode('utf-8', errors='replace')}"
        )
        data = _parse_json(proc)
        cluster_id = data.get("id", "")
        assert cluster_id.startswith("CL-"), f"클러스터 ID 형식 오류: {cluster_id!r}"
        assert data.get("patch_lane_forbidden") is False
        assert data.get("patch_failures") == 0
        # 정리
        _run(["cluster", "close", "--cluster-id", cluster_id])

    def test_cluster_status_shows_all(self):
        """cluster status는 전체 클러스터 목록 JSON을 반환해야 한다."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: cluster 명령은 pipeline_state.json이 아닌 별도 cluster 파일만 수정합니다
        proc_init = _run(["cluster", "init", "--desc", "status test"])
        data_init = _parse_json(proc_init)
        cluster_id = data_init.get("id", "")

        proc = _run(["cluster", "status"])
        assert proc.returncode == 0
        data = _parse_json(proc)
        assert "clusters" in data, "clusters 필드 누락"
        assert "total" in data, "total 필드 누락"

        if cluster_id:
            _run(["cluster", "close", "--cluster-id", cluster_id])

    def test_cluster_close_sets_closed_at(self):
        """cluster close는 closed_at 타임스탬프를 기록해야 한다."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: cluster 명령은 pipeline_state.json이 아닌 별도 cluster 파일만 수정합니다
        proc_init = _run(["cluster", "init", "--desc", "close test"])
        data_init = _parse_json(proc_init)
        cluster_id = data_init.get("id", "")
        assert cluster_id, "클러스터 생성 실패"

        proc = _run(["cluster", "close", "--cluster-id", cluster_id])
        assert proc.returncode == 0
        data = _parse_json(proc)
        assert data.get("closed_at") is not None, "closed_at이 기록되지 않음"


# ---------------------------------------------------------------------------
# Patch Audit 테스트 (4개)
# ---------------------------------------------------------------------------

class TestPatchAudit:
    """patch plan / audit -- 진입 조건과 auto-escalation 테스트."""

    def test_patch_plan_pass_within_limit(self):
        """15줄 이하 단일 파일/함수 계획은 PASS (exit 0)."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: patch plan/audit은 임시 파일만 처리하며 pipeline_state.json을 수정하지 않습니다
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        try:
            proc = _run(["patch", "plan", "--plan", plan_path])
            assert proc.returncode == 0, (
                f"exit code: {proc.returncode}\n"
                f"{proc.stderr.decode('utf-8', errors='replace')}"
            )
            data = _parse_json(proc)
            assert data.get("verdict") == "PASS", f"verdict: {data.get('verdict')}"
            assert data.get("lane") == "patch", f"lane: {data.get('lane')}"
        finally:
            os.unlink(plan_path)

    def test_patch_plan_fail_exceeds_15_lines(self):
        """16줄 이상 계획은 FAIL + lane=full (exit 1). auto-escalation 임계값=15."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: patch plan은 임시 파일만 처리하며 pipeline_state.json을 수정하지 않습니다
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=16))
            plan_path = f.name
        try:
            proc = _run(["patch", "plan", "--plan", plan_path])
            assert proc.returncode == 1, f"16줄은 FAIL이어야 함. exit: {proc.returncode}"
            data = _parse_json(proc)
            assert data.get("lane") == "full", f"lane은 full이어야 함: {data.get('lane')}"
        finally:
            os.unlink(plan_path)

    def test_patch_plan_fail_trust_root(self):
        """trust_root_changes=true이면 FAIL + lane=full."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: patch plan은 임시 파일만 처리하며 pipeline_state.json을 수정하지 않습니다
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=5, trust_root=True))
            plan_path = f.name
        try:
            proc = _run(["patch", "plan", "--plan", plan_path])
            assert proc.returncode == 1
            data = _parse_json(proc)
            assert data.get("lane") == "full"
        finally:
            os.unlink(plan_path)

    def test_patch_audit_pass(self):
        """patch audit은 유효한 계획에 대해 PASS를 반환해야 한다."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: patch audit은 임시 파일만 처리하며 pipeline_state.json을 수정하지 않습니다
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        try:
            proc = _run(["patch", "audit", "--plan", plan_path])
            assert proc.returncode == 0
            data = _parse_json(proc)
            assert data.get("verdict") == "PASS"
        finally:
            os.unlink(plan_path)


# ---------------------------------------------------------------------------
# Patch Verify 테스트 (2개)
# ---------------------------------------------------------------------------

class TestPatchVerify:
    """patch verify 결과 기록 테스트."""

    def test_patch_verify_pass(self):
        """patch verify --result PASS는 --test-command와 함께 exit 0을 반환해야 한다."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: patch verify는 임시 파일만 처리하며 pipeline_state.json을 수정하지 않습니다
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        try:
            proc = _run([
                "patch", "verify",
                "--plan", plan_path,
                "--result", "PASS",
                "--test-command", "python -m pytest -q",
            ])
            assert proc.returncode == 0, (
                f"exit: {proc.returncode}\n"
                f"{proc.stderr.decode('utf-8', errors='replace')}"
            )
            data = _parse_json(proc)
            assert data.get("verdict") == "PASS"
        finally:
            os.unlink(plan_path)

    def test_patch_verify_fail(self):
        """patch verify --result FAIL은 exit 1을 반환해야 한다."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: patch verify는 임시 파일만 처리하며 pipeline_state.json을 수정하지 않습니다
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        try:
            proc = _run(["patch", "verify", "--plan", plan_path, "--result", "FAIL"])
            assert proc.returncode == 1, (
                f"FAIL 결과 시 exit code 1이어야 함: {proc.returncode}"
            )
        finally:
            os.unlink(plan_path)


# ---------------------------------------------------------------------------
# Auto-escalation 경계값 테스트 (2개)
# ---------------------------------------------------------------------------

class TestAutoEscalation:
    """auto-escalation 임계값 15줄 경계값 테스트 (Q-PL-01)."""

    def test_exactly_15_lines_passes(self):
        """정확히 15줄은 PASS -- 임계값 이하."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: patch plan은 임시 파일만 처리하며 pipeline_state.json을 수정하지 않습니다
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=15))
            plan_path = f.name
        try:
            proc = _run(["patch", "plan", "--plan", plan_path])
            assert proc.returncode == 0, f"15줄은 PASS여야 함. exit: {proc.returncode}"
            data = _parse_json(proc)
            assert data.get("verdict") == "PASS"
        finally:
            os.unlink(plan_path)

    def test_16_lines_fails(self):
        """16줄은 FAIL -- 임계값 초과."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: patch plan은 임시 파일만 처리하며 pipeline_state.json을 수정하지 않습니다
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=16))
            plan_path = f.name
        try:
            proc = _run(["patch", "plan", "--plan", plan_path])
            assert proc.returncode == 1, f"16줄은 FAIL이어야 함. exit: {proc.returncode}"
        finally:
            os.unlink(plan_path)


# ---------------------------------------------------------------------------
# Patch Lane Forbidden 테스트 (1개)
# ---------------------------------------------------------------------------

class TestPatchLaneForbidden:
    """patch_lane_forbidden 자동 설정 테스트 (_CLUSTER_MAX_PATCH_FAILURES=2)."""

    def test_cluster_init_has_forbidden_false(self):
        """새로 생성한 클러스터는 patch_lane_forbidden=false여야 한다."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: cluster 명령은 pipeline_state.json이 아닌 별도 cluster 파일만 수정합니다
        proc = _run(["cluster", "init", "--desc", "forbidden test"])
        assert proc.returncode == 0
        data = _parse_json(proc)
        cluster_id = data.get("id", "")
        assert data.get("patch_lane_forbidden") is False, "초기 patch_lane_forbidden은 false여야 함"
        assert data.get("patch_failures") == 0
        if cluster_id:
            _run(["cluster", "close", "--cluster-id", cluster_id])


# ---------------------------------------------------------------------------
# Patch Plan 스키마 검증 (1개)
# ---------------------------------------------------------------------------

class TestPatchPlanSchema:
    """patch_plan.json 스키마 검증 테스트."""

    def test_missing_scope_file_fails(self):
        """patch_scope.file 미지정 시 FAIL."""
        # CLI_EVIDENCE_ALLOW_READ_ONLY: patch plan은 임시 파일만 처리하며 pipeline_state.json을 수정하지 않습니다
        plan = {
            "schema_version": 1,
            "pipeline_id": "IMP-TEST-0000-0001",
            "patch_scope": {
                "function": "cmd_cluster",
                "expected_lines_changed_max": 10,
            },
            "forbidden": {
                "trust_root_changes": False,
                "new_dependencies": False,
                "file_move_or_delete": False,
                "packaging_changes": False,
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            json.dump(plan, f, ensure_ascii=False)
            plan_path = f.name
        try:
            proc = _run(["patch", "plan", "--plan", plan_path])
            assert proc.returncode == 1, "patch_scope.file 누락 시 FAIL이어야 함"
        finally:
            os.unlink(plan_path)
