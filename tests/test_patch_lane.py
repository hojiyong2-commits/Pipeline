"""
tests/test_patch_lane.py
------------------------
IMP-20260513-4C0B: Patch Lane + Incident Cluster 단위 테스트
IMP-20260514-28A2: diff gate, verify evidence 필수화, cluster 환경변수 격리, acceptance 강화 테스트 추가

auto-escalation 임계값: 15줄 (Q-PL-01 사용자 확정)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PIPELINE = str(_ROOT / "pipeline.py")


def _run(
    args: list,
    input_str: str = None,
    extra_env: Dict[str, str] = None,
) -> subprocess.CompletedProcess:
    """pipeline.py 서브커맨드 실행 헬퍼."""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        [sys.executable, _PIPELINE] + args,
        capture_output=True,
        cwd=str(_ROOT),
        input=input_str.encode("utf-8") if input_str else None,
        env=env,
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
# 공통 환경 격리 fixture
# PIPELINE_CLUSTER_DIR=tmpdir 으로 .pipeline/clusters/ 오염 방지
# PATCH_SKIP_DIFF=1 으로 diff gate 건너뜀 (테스트에서 git diff 의존 없음)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_cluster_and_diff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """모든 테스트에 PIPELINE_CLUSTER_DIR 격리와 PATCH_SKIP_DIFF=1 적용."""
    monkeypatch.setenv("PIPELINE_CLUSTER_DIR", str(tmp_path))
    monkeypatch.setenv("PATCH_SKIP_DIFF", "1")


# ---------------------------------------------------------------------------
# Cluster 테스트 (4개)
# ---------------------------------------------------------------------------

class TestClusterCommands:
    """cluster detect / init / status / close 기능 테스트."""

    def test_cluster_detect_returns_json(self, tmp_path: Path) -> None:
        """cluster detect는 JSON을 반환하고 match_found 필드를 포함해야 한다."""
        proc = _run(["cluster", "detect"], extra_env={"PIPELINE_CLUSTER_DIR": str(tmp_path)})
        assert proc.returncode == 0, (
            f"exit code: {proc.returncode}\n{proc.stderr.decode('utf-8', errors='replace')}"
        )
        data = _parse_json(proc)
        assert "match_found" in data, "match_found 필드 누락"
        assert isinstance(data.get("clusters"), list), "clusters 필드는 리스트여야 함"

    def test_cluster_init_creates_cluster(self, tmp_path: Path) -> None:
        """cluster init은 CL- 접두사를 가진 클러스터 ID를 생성해야 한다."""
        proc = _run(
            ["cluster", "init", "--desc", "pytest test cluster"],
            extra_env={"PIPELINE_CLUSTER_DIR": str(tmp_path)},
        )
        assert proc.returncode == 0, (
            f"exit code: {proc.returncode}\n{proc.stderr.decode('utf-8', errors='replace')}"
        )
        data = _parse_json(proc)
        cluster_id = data.get("id", "")
        assert cluster_id.startswith("CL-"), f"클러스터 ID 형식 오류: {cluster_id!r}"
        assert data.get("patch_lane_forbidden") is False
        assert data.get("patch_failures") == 0
        # 격리된 tmp_path에서만 파일 생성됐는지 확인
        assert (tmp_path / f"{cluster_id}.json").exists(), "클러스터 파일이 tmp_path에 없음"
        assert not (_ROOT / ".pipeline" / "clusters" / f"{cluster_id}.json").exists(), (
            ".pipeline/clusters/에 오염 파일 생성됨"
        )
        # 정리
        _run(
            ["cluster", "close", "--cluster-id", cluster_id],
            extra_env={"PIPELINE_CLUSTER_DIR": str(tmp_path)},
        )

    def test_cluster_status_shows_all(self, tmp_path: Path) -> None:
        """cluster status는 전체 클러스터 목록 JSON을 반환해야 한다."""
        proc_init = _run(
            ["cluster", "init", "--desc", "status test"],
            extra_env={"PIPELINE_CLUSTER_DIR": str(tmp_path)},
        )
        data_init = _parse_json(proc_init)
        cluster_id = data_init.get("id", "")

        proc = _run(["cluster", "status"], extra_env={"PIPELINE_CLUSTER_DIR": str(tmp_path)})
        assert proc.returncode == 0
        data = _parse_json(proc)
        assert "clusters" in data, "clusters 필드 누락"
        assert "total" in data, "total 필드 누락"

        if cluster_id:
            _run(
                ["cluster", "close", "--cluster-id", cluster_id],
                extra_env={"PIPELINE_CLUSTER_DIR": str(tmp_path)},
            )

    def test_cluster_close_sets_closed_at(self, tmp_path: Path) -> None:
        """cluster close는 closed_at 타임스탬프를 기록해야 한다."""
        proc_init = _run(
            ["cluster", "init", "--desc", "close test"],
            extra_env={"PIPELINE_CLUSTER_DIR": str(tmp_path)},
        )
        data_init = _parse_json(proc_init)
        cluster_id = data_init.get("id", "")
        assert cluster_id, "클러스터 생성 실패"

        proc = _run(
            ["cluster", "close", "--cluster-id", cluster_id],
            extra_env={"PIPELINE_CLUSTER_DIR": str(tmp_path)},
        )
        assert proc.returncode == 0
        data = _parse_json(proc)
        assert data.get("closed_at") is not None, "closed_at이 기록되지 않음"


# ---------------------------------------------------------------------------
# Patch Audit 테스트 (4개)
# ---------------------------------------------------------------------------

class TestPatchAudit:
    """patch plan / audit -- 진입 조건과 auto-escalation 테스트."""

    def test_patch_plan_pass_within_limit(self) -> None:
        """15줄 이하 단일 파일/함수 계획은 PASS (exit 0). --skip-diff-check 사용."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        try:
            proc = _run(["patch", "plan", "--plan", plan_path, "--skip-diff-check"])
            assert proc.returncode == 0, (
                f"exit code: {proc.returncode}\n"
                f"{proc.stderr.decode('utf-8', errors='replace')}"
            )
            data = _parse_json(proc)
            assert data.get("verdict") == "PASS", f"verdict: {data.get('verdict')}"
            assert data.get("lane") == "patch", f"lane: {data.get('lane')}"
        finally:
            os.unlink(plan_path)

    def test_patch_plan_fail_exceeds_15_lines(self) -> None:
        """16줄 이상 계획은 FAIL + lane=full (exit 1). auto-escalation 임계값=15."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=16))
            plan_path = f.name
        try:
            proc = _run(["patch", "plan", "--plan", plan_path, "--skip-diff-check"])
            assert proc.returncode == 1, f"16줄은 FAIL이어야 함. exit: {proc.returncode}"
            data = _parse_json(proc)
            assert data.get("lane") == "full", f"lane은 full이어야 함: {data.get('lane')}"
        finally:
            os.unlink(plan_path)

    def test_patch_plan_fail_trust_root(self) -> None:
        """trust_root_changes=true이면 FAIL + lane=full."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=5, trust_root=True))
            plan_path = f.name
        try:
            proc = _run(["patch", "plan", "--plan", plan_path, "--skip-diff-check"])
            assert proc.returncode == 1
            data = _parse_json(proc)
            assert data.get("lane") == "full"
        finally:
            os.unlink(plan_path)

    def test_patch_audit_pass(self) -> None:
        """patch audit은 유효한 계획에 대해 PASS를 반환해야 한다."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        try:
            proc = _run(["patch", "audit", "--plan", plan_path, "--skip-diff-check"])
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

    def test_patch_verify_pass(self) -> None:
        """patch verify --result PASS --test-command 제공 시 exit 0."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        try:
            proc = _run(
                [
                    "patch", "verify",
                    "--plan", plan_path,
                    "--result", "PASS",
                    "--test-command", "python --version",
                ]
            )
            assert proc.returncode == 0, (
                f"exit: {proc.returncode}\n"
                f"{proc.stderr.decode('utf-8', errors='replace')}"
            )
            data = _parse_json(proc)
            assert data.get("verdict") == "PASS"
        finally:
            os.unlink(plan_path)

    def test_patch_verify_fail(self) -> None:
        """patch verify --result FAIL은 exit 1을 반환해야 한다."""
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

    def test_exactly_15_lines_passes(self) -> None:
        """정확히 15줄은 PASS -- 임계값 이하."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=15))
            plan_path = f.name
        try:
            proc = _run(["patch", "plan", "--plan", plan_path, "--skip-diff-check"])
            assert proc.returncode == 0, f"15줄은 PASS여야 함. exit: {proc.returncode}"
            data = _parse_json(proc)
            assert data.get("verdict") == "PASS"
        finally:
            os.unlink(plan_path)

    def test_16_lines_fails(self) -> None:
        """16줄은 FAIL -- 임계값 초과."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=16))
            plan_path = f.name
        try:
            proc = _run(["patch", "plan", "--plan", plan_path, "--skip-diff-check"])
            assert proc.returncode == 1, f"16줄은 FAIL이어야 함. exit: {proc.returncode}"
        finally:
            os.unlink(plan_path)


# ---------------------------------------------------------------------------
# Patch Lane Forbidden 테스트 (1개)
# ---------------------------------------------------------------------------

class TestPatchLaneForbidden:
    """patch_lane_forbidden 자동 설정 테스트 (_CLUSTER_MAX_PATCH_FAILURES=2)."""

    def test_cluster_init_has_forbidden_false(self, tmp_path: Path) -> None:
        """새로 생성한 클러스터는 patch_lane_forbidden=false여야 한다."""
        proc = _run(
            ["cluster", "init", "--desc", "forbidden test"],
            extra_env={"PIPELINE_CLUSTER_DIR": str(tmp_path)},
        )
        assert proc.returncode == 0
        data = _parse_json(proc)
        cluster_id = data.get("id", "")
        assert data.get("patch_lane_forbidden") is False, "초기 patch_lane_forbidden은 false여야 함"
        assert data.get("patch_failures") == 0
        if cluster_id:
            _run(
                ["cluster", "close", "--cluster-id", cluster_id],
                extra_env={"PIPELINE_CLUSTER_DIR": str(tmp_path)},
            )


# ---------------------------------------------------------------------------
# Patch Plan 스키마 검증 (1개)
# ---------------------------------------------------------------------------

class TestPatchPlanSchema:
    """patch_plan.json 스키마 검증 테스트."""

    def test_missing_scope_file_fails(self) -> None:
        """patch_scope.file 미지정 시 FAIL."""
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
            proc = _run(["patch", "plan", "--plan", plan_path, "--skip-diff-check"])
            assert proc.returncode == 1, "patch_scope.file 누락 시 FAIL이어야 함"
        finally:
            os.unlink(plan_path)


# ---------------------------------------------------------------------------
# IMP-20260514-28A2 신규 테스트 (8개)
# diff gate, verify evidence, attest blocking, cluster 격리, acceptance 플래그
# ---------------------------------------------------------------------------

class TestDiffGate:
    """diff gate 동작 테스트 (IMP-20260514-28A2)."""

    def test_diff_gate_skip_via_flag(self) -> None:
        """--skip-diff-check 플래그로 diff gate 건너뜀 시 PASS."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        try:
            proc = _run(["patch", "plan", "--plan", plan_path, "--skip-diff-check"])
            assert proc.returncode == 0, (
                f"--skip-diff-check 시 exit 0이어야 함. exit: {proc.returncode}\n"
                f"{proc.stderr.decode('utf-8', errors='replace')}"
            )
            data = _parse_json(proc)
            assert data.get("verdict") == "PASS"
            # diff_scope가 비어있어야 함 (skip됨)
            assert data.get("diff_scope") == {} or data.get("diff_scope", {}).get("skipped") is True
        finally:
            os.unlink(plan_path)

    def test_diff_gate_skip_via_env_var(self) -> None:
        """PATCH_SKIP_DIFF=1 환경변수로 diff gate 건너뜀 시 PASS."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        try:
            # autouse fixture가 이미 PATCH_SKIP_DIFF=1을 설정하므로 그냥 _run
            proc = _run(["patch", "plan", "--plan", plan_path])
            assert proc.returncode == 0, (
                f"PATCH_SKIP_DIFF=1 시 exit 0이어야 함. exit: {proc.returncode}"
            )
            data = _parse_json(proc)
            assert data.get("verdict") == "PASS"
        finally:
            os.unlink(plan_path)

    def test_diff_gate_trust_root_detected_without_skip(self) -> None:
        """trust_root_changes=true 계획은 --skip-diff-check 없어도 자기신고 검사에서 FAIL."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=5, trust_root=True))
            plan_path = f.name
        try:
            # PATCH_SKIP_DIFF=1 환경변수가 autouse fixture에서 설정되어 있지만
            # trust_root_changes=true 는 자기신고 검사 (diff gate 아님) 에서 잡힘
            proc = _run(["patch", "plan", "--plan", plan_path])
            assert proc.returncode == 1, "trust_root_changes=true 시 FAIL이어야 함"
            data = _parse_json(proc)
            assert data.get("lane") == "full"
        finally:
            os.unlink(plan_path)


class TestVerifyEvidenceRequired:
    """patch verify PASS 시 evidence 필수화 테스트 (IMP-20260514-28A2)."""

    def test_verify_pass_without_evidence_blocked(self) -> None:
        """--test-command / --evidence-file 없이 PASS 호출 시 exit 1 + BLOCKED."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        try:
            proc = _run(["patch", "verify", "--plan", plan_path, "--result", "PASS"])
            assert proc.returncode == 1, (
                "evidence 없이 PASS 시 exit 1이어야 함"
            )
            stderr = proc.stderr.decode("utf-8", errors="replace")
            assert "BLOCKED" in stderr or "PATCH VERIFY BLOCKED" in stderr, (
                f"BLOCKED 메시지 없음: {stderr[:300]}"
            )
        finally:
            os.unlink(plan_path)

    def test_verify_pass_with_test_command_succeeds(self) -> None:
        """--test-command 제공 시 PASS exit 0."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        try:
            proc = _run(
                [
                    "patch", "verify",
                    "--plan", plan_path,
                    "--result", "PASS",
                    "--test-command", "python --version",
                ]
            )
            assert proc.returncode == 0, (
                f"--test-command 제공 시 exit 0이어야 함. exit: {proc.returncode}\n"
                f"{proc.stderr.decode('utf-8', errors='replace')}"
            )
            data = _parse_json(proc)
            assert data.get("verdict") == "PASS"
        finally:
            os.unlink(plan_path)

    def test_verify_pass_with_evidence_file_succeeds(self) -> None:
        """--evidence-file 제공 시 PASS exit 0."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        # 증거 파일 생성
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as ef:
            ef.write("test evidence content")
            evidence_path = ef.name
        try:
            proc = _run(
                [
                    "patch", "verify",
                    "--plan", plan_path,
                    "--result", "PASS",
                    "--evidence-file", evidence_path,
                ]
            )
            assert proc.returncode == 0, (
                f"--evidence-file 제공 시 exit 0이어야 함. exit: {proc.returncode}\n"
                f"{proc.stderr.decode('utf-8', errors='replace')}"
            )
            data = _parse_json(proc)
            assert data.get("verdict") == "PASS"
        finally:
            os.unlink(plan_path)
            os.unlink(evidence_path)


class TestAttestBlocking:
    """patch attest verify_passed 블로킹 테스트 (IMP-20260514-28A2)."""

    def test_attest_blocks_when_no_prior_verify_pass(self) -> None:
        """patch verify PASS 없이 attest 호출 시 exit 1 + BLOCKED.

        pipeline_state.json의 patch_lane.verify_passed를 테스트 전후 임시 저장/복원하여
        다른 테스트의 부수 효과를 격리한다.
        """
        state_path = _ROOT / "pipeline_state.json"
        original_patch_lane: Any = None
        state_backup: Dict[str, Any] = {}

        # 현재 state의 patch_lane 저장
        if state_path.is_file():
            try:
                with open(str(state_path), encoding="utf-8") as sf:
                    state_backup = json.load(sf)
                original_patch_lane = state_backup.get("patch_lane")
            except (json.JSONDecodeError, OSError):
                pass

        # patch_lane.verify_passed를 False로 강제 설정
        if state_path.is_file():
            try:
                modified_state = dict(state_backup)
                pl = dict(modified_state.get("patch_lane") or {})
                pl["verify_passed"] = False
                modified_state["patch_lane"] = pl
                with open(str(state_path), "w", encoding="utf-8", newline="\n") as sf:
                    json.dump(modified_state, sf, indent=2, ensure_ascii=False)
                    sf.write("\n")
            except (OSError, TypeError):
                pass

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8", dir=str(_ROOT)
        ) as f:
            f.write(_make_patch_plan(lines=10))
            plan_path = f.name
        try:
            # pipeline_state.json에 verify_passed=False 상태에서 attest 호출
            proc = _run(["patch", "attest", "--plan", plan_path])
            assert proc.returncode == 1, (
                "verify PASS 없이 attest 시 exit 1이어야 함"
            )
            stderr = proc.stderr.decode("utf-8", errors="replace")
            assert "BLOCKED" in stderr or "ATTEST BLOCKED" in stderr, (
                f"BLOCKED 메시지 없음: {stderr[:300]}"
            )
        finally:
            os.unlink(plan_path)
            # patch_lane을 원래 값으로 복원
            if state_path.is_file() and state_backup:
                try:
                    restore_state = dict(state_backup)
                    restore_state["patch_lane"] = original_patch_lane
                    with open(str(state_path), "w", encoding="utf-8", newline="\n") as sf:
                        json.dump(restore_state, sf, indent=2, ensure_ascii=False)
                        sf.write("\n")
                except (OSError, TypeError):
                    pass


class TestClusterDirIsolation:
    """PIPELINE_CLUSTER_DIR 환경변수 격리 테스트 (IMP-20260514-28A2)."""

    def test_cluster_dir_isolation_via_env(self, tmp_path: Path) -> None:
        """PIPELINE_CLUSTER_DIR 설정 시 클러스터 파일이 해당 경로에만 생성된다."""
        custom_dir = tmp_path / "custom_clusters"
        custom_dir.mkdir()
        proc = _run(
            ["cluster", "init", "--desc", "isolation test"],
            extra_env={"PIPELINE_CLUSTER_DIR": str(custom_dir)},
        )
        assert proc.returncode == 0
        data = _parse_json(proc)
        cluster_id = data.get("id", "")
        assert cluster_id, "클러스터 ID 없음"
        # custom_dir에 파일이 있어야 함
        assert (custom_dir / f"{cluster_id}.json").exists(), (
            f"custom_dir에 클러스터 파일 없음: {cluster_id}"
        )
        # 기본 .pipeline/clusters에는 없어야 함
        default_path = _ROOT / ".pipeline" / "clusters" / f"{cluster_id}.json"
        assert not default_path.exists(), (
            f".pipeline/clusters에 오염 파일 생성됨: {cluster_id}"
        )
        # 정리
        _run(
            ["cluster", "close", "--cluster-id", cluster_id],
            extra_env={"PIPELINE_CLUSTER_DIR": str(custom_dir)},
        )


class TestAcceptanceEvidenceFlag:
    """gates accept --allow-unregistered-evidence 플래그 테스트 (IMP-20260514-28A2)."""

    def test_acceptance_unregistered_evidence_flag_in_help(self) -> None:
        """gates accept --help에 --allow-unregistered-evidence 옵션이 있어야 한다."""
        proc = _run(["gates", "accept", "--help"])
        # --help는 exit 0이 아닐 수 있음 (argparse는 SystemExit(0) 발생)
        output = proc.stdout.decode("utf-8", errors="replace") + proc.stderr.decode("utf-8", errors="replace")
        assert "allow-unregistered-evidence" in output, (
            f"--allow-unregistered-evidence 플래그가 --help에 없음: {output[:500]}"
        )
