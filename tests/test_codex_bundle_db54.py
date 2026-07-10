"""IMP-20260710-DB54 MT-7: Codex Review Bundle 최소화 검증.

_build_codex_review_bundle이 최소 필드(개수/SHA)만 포함하고 대형 diff 원문/oracle 원문을
제외하는지, raw ACCEPT 코드/nonce가 값으로 포함되지 않는지 검증한다.
oracle: TC-bundle-normal.
"""
import sys
import os
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import pipeline


def _build_in_isolation(tmpdir, pid="IMP-20260710-DB54", state=None):
    """PIPELINE_STATE_PATH 격리 환경에서 bundle을 생성하고 (sha, path, bundle) 반환."""
    state_path = os.path.join(tmpdir, "state.json")
    if state is None:
        state = {"pipeline_id": pid}
    Path(state_path).write_text(json.dumps(state), encoding="utf-8")
    os.makedirs(os.path.join(tmpdir, ".pipeline"), exist_ok=True)
    env = {"PIPELINE_STATE_PATH": state_path}
    with patch.dict(os.environ, env):
        sha, path = pipeline._build_codex_review_bundle(state, pid)
    bundle = None
    if path and Path(path).exists():
        bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    return sha, path, bundle


class TestBundle_TC7_Fields:
    """TC-bundle-normal: 필수 필드 존재 + 대형 diff/oracle 제외."""

    def test_bundle_has_required_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha, path, bundle = _build_in_isolation(tmpdir)
            assert sha, "bundle SHA가 비어 있으면 안 됨"
            assert bundle is not None
            for field in [
                "contract_sha256",
                "review_bundle_sha256",
                "pipeline_id",
                "changed_files_count",
                "oracle_count",
            ]:
                assert field in bundle, f"필드 누락: {field}"

    def test_changed_files_count_matches_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha, path, bundle = _build_in_isolation(tmpdir)
            assert bundle is not None
            assert bundle["changed_files_count"] == len(bundle.get("changed_files", []))

    def test_oracle_count_is_int(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha, path, bundle = _build_in_isolation(tmpdir)
            assert bundle is not None
            assert isinstance(bundle["oracle_count"], int)
            assert bundle["oracle_count"] >= 0

    def test_full_diff_excluded(self):
        """bundle에 대형 diff 원문 텍스트가 포함되지 않아야 한다 (파일명 목록만)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sha, path, bundle = _build_in_isolation(tmpdir)
            assert bundle is not None
            # diff 원문 hunk 마커(@@ ... @@)가 bundle 어디에도 없어야 함.
            raw = json.dumps(bundle, ensure_ascii=False)
            assert "@@ " not in raw, "bundle에 diff hunk 원문이 포함되면 안 됨"

    def test_oracle_raw_excluded(self):
        """oracle 원문(expected.json 내용)이 bundle에 포함되지 않아야 한다."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sha, path, bundle = _build_in_isolation(tmpdir)
            assert bundle is not None
            assert "expected" not in bundle
            assert "oracle_manifest" not in bundle


class TestBundle_TC8_NoRawAcceptCode:
    """TC-8: bundle 값에 raw ACCEPT 코드/nonce 미포함."""

    def test_bundle_excludes_accept_patterns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha, path, bundle = _build_in_isolation(tmpdir)
            assert bundle is not None
            for k, v in bundle.items():
                if isinstance(v, str):
                    assert not re.search(
                        r"ACCEPT-[A-Z0-9]+-\d{8}-[A-Z0-9]+-[A-Z2-7]{8}", v
                    ), f"bundle 값 {k}에 raw ACCEPT 코드 포함: {v}"

    def test_review_bundle_sha_field_is_placeholder(self):
        """embedded review_bundle_sha256은 파일 SHA 결정성을 위해 빈 placeholder여야 한다."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sha, path, bundle = _build_in_isolation(tmpdir)
            assert bundle is not None
            # E69E 불변식: 신뢰 루트 SHA는 _sha256_file(반환 sha)이고 embedded 필드는 "" 고정.
            assert bundle["review_bundle_sha256"] == ""
            assert sha != "", "반환 SHA(신뢰 루트)는 non-empty여야 함"


class TestBundle_TC9_TypeGuards:
    """None/비str pipeline_id 방어 → ("", "") 반환."""

    def test_none_pipeline_id_returns_empty(self):
        sha, path = pipeline._build_codex_review_bundle({}, None)
        assert sha == "" and path == ""

    def test_non_str_pipeline_id_returns_empty(self):
        sha, path = pipeline._build_codex_review_bundle({}, 123)
        assert sha == "" and path == ""


class TestBundle_Rework_RequiredFields:
    """rework MT-3: 신규 필수 필드 존재 + 누락 감지 헬퍼."""

    def test_all_new_fields_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sha, path, bundle = _build_in_isolation(tmpdir)
            assert bundle is not None
            for field in [
                "included_functions", "excluded_files", "excluded_files_reason",
                "critical_file_shas", "critical_function_shas", "changed_critical_files",
                "test_summary", "oracle_summary",
            ]:
                assert field in bundle, f"신규 필드 누락: {field}"
            assert isinstance(bundle["included_functions"], list)
            assert isinstance(bundle["excluded_files"], list)
            assert isinstance(bundle["critical_file_shas"], dict)
            assert isinstance(bundle["test_summary"], dict)

    def test_required_fields_missing_helper(self):
        # 4개 필수 필드가 모두 있으면 빈 리스트.
        good = {
            "included_functions": [], "excluded_files": [],
            "critical_file_shas": {}, "test_summary": {},
        }
        assert pipeline._codex_bundle_required_fields_missing(good) == []
        # 하나 빠지면 그 이름이 반환된다.
        bad = dict(good)
        del bad["critical_file_shas"]
        assert "critical_file_shas" in pipeline._codex_bundle_required_fields_missing(bad)

    def test_required_fields_missing_type_guard(self):
        try:
            pipeline._codex_bundle_required_fields_missing(None)
            assert False, "None should raise"
        except TypeError:
            pass

    def test_excluded_files_no_raw_originals(self):
        """excluded_files는 원문이 아니라 제외 항목 식별자만 담는다."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sha, path, bundle = _build_in_isolation(tmpdir)
            assert bundle is not None
            raw = json.dumps(bundle, ensure_ascii=False)
            # raw ACCEPT 코드/nonce가 bundle 값에 들어가면 안 된다.
            assert not re.search(r"ACCEPT-[A-Z0-9]+-\d{8}-[A-Z0-9]+-[A-Z2-7]{8}", raw)


class TestBundle_Rework_OracleGateStatus:
    """rework MT-3 문제4: oracle gate 상태를 state SSoT/summary.verdict에서 읽는다."""

    def test_oracle_status_from_state(self):
        state = {
            "pipeline_id": "IMP-20260710-DB54",
            "external_gates": {"oracle": {"status": "PASS"}},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            sha, path, bundle = _build_in_isolation(tmpdir, state=state)
            assert bundle is not None
            assert bundle["oracle_gate_status"] == "PASS"
            # 기존 버그: oracle이 UNKNOWN이 되면 안 된다.
            assert bundle["oracle_gate_status"] != "UNKNOWN"

    def test_oracle_status_from_summary_verdict_fallback(self, monkeypatch):
        """state에 oracle이 없으면 oracle_result.json의 summary.verdict를 읽는다."""
        pid = "IMP-20260710-DB54"
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_contracts = Path(tmpdir) / "contracts"
            gates_dir = fake_contracts / pid / "gates"
            gates_dir.mkdir(parents=True, exist_ok=True)
            (gates_dir / "oracle_result.json").write_text(
                json.dumps({"summary": {"verdict": "PASS"}}), encoding="utf-8"
            )
            monkeypatch.setattr(pipeline, "CONTRACTS_DIR", fake_contracts)
            state_path = os.path.join(tmpdir, "state.json")
            # external_gates에 oracle 없음 → 파일 fallback 유도.
            state = {"pipeline_id": pid, "external_gates": {}}
            Path(state_path).write_text(json.dumps(state), encoding="utf-8")
            os.makedirs(os.path.join(tmpdir, ".pipeline"), exist_ok=True)
            with patch.dict(os.environ, {"PIPELINE_STATE_PATH": state_path}):
                sha, path = pipeline._build_codex_review_bundle(state, pid)
                bundle = json.loads(Path(path).read_text(encoding="utf-8"))
            assert bundle["oracle_gate_status"] == "PASS"


class TestBundle_Rework_CriticalFile:
    """rework MT-4: critical file 판정 헬퍼."""

    def test_pipeline_py_is_critical(self):
        assert pipeline._is_codex_critical_file("pipeline.py") is True

    def test_workflow_is_critical(self):
        assert pipeline._is_codex_critical_file(".github/workflows/ci.yml") is True

    def test_codex_test_is_critical(self):
        assert pipeline._is_codex_critical_file("tests/test_codex_cache_db54.py") is True

    def test_regular_file_not_critical(self):
        assert pipeline._is_codex_critical_file("core/foo.py") is False

    def test_none_raises(self):
        try:
            pipeline._is_codex_critical_file(None)
            assert False, "None should raise"
        except TypeError:
            pass


if __name__ == "__main__":
    import subprocess

    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
