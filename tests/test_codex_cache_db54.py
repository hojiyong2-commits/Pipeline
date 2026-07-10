"""IMP-20260710-DB54 MT-8: Codex Cache Hit/Miss/무효화 테스트.

_check_codex_cache / _codex_cache_key / _codex_review_cache_path 를 검증한다.
oracle: TC-cache-hit.
"""
import sys
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
import pipeline


class TestCacheKey:
    """_codex_cache_key: 결정적 16자 키."""

    def test_deterministic(self):
        assert pipeline._codex_cache_key("abc123", "bun456") == pipeline._codex_cache_key(
            "abc123", "bun456"
        )

    def test_length_16(self):
        assert len(pipeline._codex_cache_key("abc123", "bun456")) == 16

    def test_different_inputs_differ(self):
        assert pipeline._codex_cache_key("abc", "x") != pipeline._codex_cache_key("abc", "y")

    def test_none_raises(self):
        try:
            pipeline._codex_cache_key(None, "x")
            assert False, "None should raise"
        except TypeError:
            pass


class TestCache_TC11_Hit:
    """TC-cache-hit: 같은 contract+bundle SHA → cache hit, CLI 미호출."""

    def test_cache_hit_returns_cached_verdict(self):
        state = {"pipeline_id": "IMP-20260710-DB54"}
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"PIPELINE_STATE_PATH": os.path.join(tmpdir, "state.json")}
            with patch.dict(os.environ, env):
                pipeline_dir = Path(tmpdir) / ".pipeline"
                pipeline_dir.mkdir(exist_ok=True)
                cache_data = {
                    "cache_key": pipeline._codex_cache_key("abc123", "bun456"),
                    "contract_sha256": "abc123",
                    "review_bundle_sha256": "bun456",
                    "verdict": "APPROVE",
                    "critical_file_shas": {},
                }
                (pipeline_dir / "codex_review_cache.json").write_text(
                    json.dumps(cache_data), encoding="utf-8"
                )
                result = pipeline._check_codex_cache(
                    "abc123", "bun456", state, "IMP-20260710-DB54"
                )
                assert result["hit"] is True
                assert result["cached_verdict"] == "APPROVE"
                assert result["cache_key"] == cache_data["cache_key"]

    def test_cache_miss_on_different_sha(self):
        state = {"pipeline_id": "IMP-20260710-DB54"}
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"PIPELINE_STATE_PATH": os.path.join(tmpdir, "state.json")}
            with patch.dict(os.environ, env):
                pipeline_dir = Path(tmpdir) / ".pipeline"
                pipeline_dir.mkdir(exist_ok=True)
                cache_data = {
                    "cache_key": pipeline._codex_cache_key("abc123", "bun456"),
                    "contract_sha256": "abc123",
                    "review_bundle_sha256": "bun456",
                    "verdict": "APPROVE",
                    "critical_file_shas": {},
                }
                (pipeline_dir / "codex_review_cache.json").write_text(
                    json.dumps(cache_data), encoding="utf-8"
                )
                result = pipeline._check_codex_cache(
                    "new_sha", "new_bun", state, "IMP-20260710-DB54"
                )
                assert result["hit"] is False

    def test_cache_miss_on_no_cache_file(self):
        state = {"pipeline_id": "IMP-20260710-DB54"}
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"PIPELINE_STATE_PATH": os.path.join(tmpdir, "state.json")}
            with patch.dict(os.environ, env):
                (Path(tmpdir) / ".pipeline").mkdir(exist_ok=True)
                result = pipeline._check_codex_cache(
                    "abc", "def", state, "IMP-20260710-DB54"
                )
                assert result["hit"] is False


class TestCache_TC12_Invalidation:
    """TC-cache-invalidation: critical file 변경 시 cache 무효화."""

    def test_cache_invalidated_on_critical_file_change(self):
        state = {"pipeline_id": "IMP-20260710-DB54"}
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"PIPELINE_STATE_PATH": os.path.join(tmpdir, "state.json")}
            with patch.dict(os.environ, env):
                pipeline_dir = Path(tmpdir) / ".pipeline"
                pipeline_dir.mkdir(exist_ok=True)
                cache_data = {
                    "cache_key": pipeline._codex_cache_key("abc123", "bun456"),
                    "contract_sha256": "abc123",
                    "review_bundle_sha256": "bun456",
                    "verdict": "APPROVE",
                    "critical_file_shas": {"pipeline.py": "OLD_SHA_12345"},
                }
                (pipeline_dir / "codex_review_cache.json").write_text(
                    json.dumps(cache_data), encoding="utf-8"
                )
                # pipeline.py의 실제 SHA != "OLD_SHA_12345" → 무효화되어야 함.
                result = pipeline._check_codex_cache(
                    "abc123", "bun456", state, "IMP-20260710-DB54"
                )
                assert result["hit"] is False
                assert "pipeline.py" in result["reason"]

    def test_cache_hit_when_critical_sha_matches(self):
        state = {"pipeline_id": "IMP-20260710-DB54"}
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"PIPELINE_STATE_PATH": os.path.join(tmpdir, "state.json")}
            with patch.dict(os.environ, env):
                pipeline_dir = Path(tmpdir) / ".pipeline"
                pipeline_dir.mkdir(exist_ok=True)
                # 현재 pipeline.py의 실제 SHA를 계산하여 캐시에 저장 → 일치 → hit.
                real_sha = pipeline._sha256_file(pipeline.BASE_DIR / "pipeline.py")
                cache_data = {
                    "cache_key": pipeline._codex_cache_key("abc123", "bun456"),
                    "contract_sha256": "abc123",
                    "review_bundle_sha256": "bun456",
                    "verdict": "APPROVE",
                    "critical_file_shas": {"pipeline.py": real_sha},
                }
                (pipeline_dir / "codex_review_cache.json").write_text(
                    json.dumps(cache_data), encoding="utf-8"
                )
                result = pipeline._check_codex_cache(
                    "abc123", "bun456", state, "IMP-20260710-DB54"
                )
                assert result["hit"] is True


class TestCachePath:
    """_codex_review_cache_path: PIPELINE_STATE_PATH 격리."""

    def test_isolated_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {"PIPELINE_STATE_PATH": os.path.join(tmpdir, "state.json")}
            with patch.dict(os.environ, env):
                p = pipeline._codex_review_cache_path()
                assert p.name == "codex_review_cache.json"
                assert ".pipeline" in str(p)
                assert str(Path(tmpdir).resolve()) in str(p.resolve())


if __name__ == "__main__":
    import subprocess

    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
