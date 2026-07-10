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


if __name__ == "__main__":
    import subprocess

    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
