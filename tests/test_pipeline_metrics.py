"""IMP-20260522-0C83: Pipeline Observability & Cycle Time Metrics 테스트.

oracle 파일: tests/oracles/IMP-20260522-0C83/T-001~T-005
12개 이상 pytest 테스트
"""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# pipeline.py를 import할 수 있도록 프로젝트 루트를 경로에 추가
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pipeline  # noqa: E402


ORACLE_ROOT = PROJECT_ROOT / "tests" / "oracles" / "IMP-20260522-0C83"


def load_oracle(case_id: str):
    input_path = ORACLE_ROOT / case_id / "input.json"
    expected_path = ORACLE_ROOT / case_id / "expected.json"
    return (
        json.loads(input_path.read_text(encoding="utf-8")),
        json.loads(expected_path.read_text(encoding="utf-8")),
    )


# ---------------------------------------------------------------------------
# TC-01: T-001 — 정상 phase elapsed 계산 (normal oracle)
# ---------------------------------------------------------------------------
class TestPhaseElapsedSummaryNormal(unittest.TestCase):
    def test_tc01_phase_elapsed_pm_dev_correct(self):
        inp, exp = load_oracle("T-001")
        state = {"phases": inp["phases"]}
        result = pipeline._phase_elapsed_summary(state)
        # pm: 15분 0초 = 900초
        self.assertEqual(result["pm"]["elapsed_seconds"], exp["phase_elapsed"]["pm"]["elapsed_seconds"])
        self.assertEqual(result["pm"]["elapsed_human"], exp["phase_elapsed"]["pm"]["elapsed_human"])
        # dev: 1시간 15분 0초 = 4500초
        self.assertEqual(result["dev"]["elapsed_seconds"], exp["phase_elapsed"]["dev"]["elapsed_seconds"])
        self.assertEqual(result["dev"]["elapsed_human"], exp["phase_elapsed"]["dev"]["elapsed_human"])

    def test_tc01_timestamps_preserved(self):
        inp, exp = load_oracle("T-001")
        state = {"phases": inp["phases"]}
        result = pipeline._phase_elapsed_summary(state)
        self.assertEqual(result["pm"]["started_at"], exp["phase_elapsed"]["pm"]["started_at"])
        self.assertEqual(result["pm"]["completed_at"], exp["phase_elapsed"]["pm"]["completed_at"])


# ---------------------------------------------------------------------------
# TC-02: T-002 — 타임스탬프 누락 시 확인 불가 (edge oracle)
# ---------------------------------------------------------------------------
class TestPhaseElapsedSummaryEdge(unittest.TestCase):
    def test_tc02_missing_started_at_returns_unavailable(self):
        inp, exp = load_oracle("T-002")
        state = {"phases": inp["phases"]}
        result = pipeline._phase_elapsed_summary(state)
        self.assertEqual(result["pm"]["elapsed_seconds"], "확인 불가")
        self.assertEqual(result["pm"]["elapsed_human"], "확인 불가")
        self.assertEqual(result["pm"]["reason"], exp["phase_elapsed"]["pm"]["reason"])

    def test_tc02_missing_completed_at_returns_unavailable(self):
        inp, exp = load_oracle("T-002")
        state = {"phases": inp["phases"]}
        result = pipeline._phase_elapsed_summary(state)
        self.assertEqual(result["dev"]["elapsed_seconds"], "확인 불가")
        self.assertEqual(result["dev"]["reason"], exp["phase_elapsed"]["dev"]["reason"])

    def test_tc02_both_missing_reason_combined(self):
        inp, exp = load_oracle("T-002")
        state = {"phases": inp["phases"]}
        result = pipeline._phase_elapsed_summary(state)
        self.assertIn("qa", result)
        self.assertEqual(result["qa"]["reason"], exp["phase_elapsed"]["qa"]["reason"])

    def test_tc02_no_estimation(self):
        """elapsed_seconds가 정수 추정값이 아니라 '확인 불가' 문자열이어야 함."""
        state = {"phases": {"pm": {"status": "DONE", "started_at": None, "completed_at": None}}}
        result = pipeline._phase_elapsed_summary(state)
        self.assertIsInstance(result["pm"]["elapsed_seconds"], str)
        self.assertEqual(result["pm"]["elapsed_seconds"], "확인 불가")


# ---------------------------------------------------------------------------
# TC-03: T-003 — failure_packet 집계 (normal oracle)
# ---------------------------------------------------------------------------
class TestFailureRetrySummary(unittest.TestCase):
    def test_tc03_failure_code_counts(self):
        inp, exp = load_oracle("T-003")
        exp_summary = exp["failure_retry_summary"]

        # 임시 디렉토리에 failure packet 파일들을 만들어 테스트
        with tempfile.TemporaryDirectory() as tmpdir:
            # pipeline_state.json과 contract/failures 구조 모킹
            failures_dir = pathlib.Path(tmpdir) / "pipeline_contracts" / "TEST-123" / "failures"
            failures_dir.mkdir(parents=True)
            for i, pkt in enumerate(inp["failure_packets"]):
                (failures_dir / f"gate_attempt_{i+1}.json").write_text(
                    json.dumps(pkt, ensure_ascii=False), encoding="utf-8"
                )
            state = {"pipeline_id": "TEST-123"}
            with patch.object(pipeline, "_contract_paths") as mock_paths, \
                 patch.object(pipeline, "_failure_root_from_paths") as mock_root:
                mock_paths.return_value = MagicMock()
                mock_root.return_value = failures_dir
                result = pipeline._failure_retry_summary(state)

        self.assertEqual(result["total_failure_packets"], exp_summary["total_failure_packets"])
        self.assertEqual(result["failure_code_counts"]["ruff_error"], exp_summary["failure_code_counts"]["ruff_error"])
        self.assertEqual(result["failure_code_counts"]["oracle_hash_mismatch"], exp_summary["failure_code_counts"]["oracle_hash_mismatch"])

    def test_tc03_most_repeated_failure_code(self):
        inp, exp = load_oracle("T-003")
        exp_summary = exp["failure_retry_summary"]
        with tempfile.TemporaryDirectory() as tmpdir:
            failures_dir = pathlib.Path(tmpdir) / "pipeline_contracts" / "TEST-123" / "failures"
            failures_dir.mkdir(parents=True)
            for i, pkt in enumerate(inp["failure_packets"]):
                (failures_dir / f"gate_attempt_{i+1}.json").write_text(
                    json.dumps(pkt, ensure_ascii=False), encoding="utf-8"
                )
            state = {"pipeline_id": "TEST-123"}
            with patch.object(pipeline, "_contract_paths") as mock_paths, \
                 patch.object(pipeline, "_failure_root_from_paths") as mock_root:
                mock_paths.return_value = MagicMock()
                mock_root.return_value = failures_dir
                result = pipeline._failure_retry_summary(state)
        self.assertEqual(result.get("most_repeated_failure_code"), exp_summary["most_repeated_failure_code"])
        self.assertEqual(result.get("most_repeated_failure_code_count"), exp_summary["most_repeated_failure_code_count"])

    def test_tc03_return_phase_distribution(self):
        inp, exp = load_oracle("T-003")
        exp_summary = exp["failure_retry_summary"]
        with tempfile.TemporaryDirectory() as tmpdir:
            failures_dir = pathlib.Path(tmpdir) / "pipeline_contracts" / "TEST-123" / "failures"
            failures_dir.mkdir(parents=True)
            for i, pkt in enumerate(inp["failure_packets"]):
                (failures_dir / f"gate_attempt_{i+1}.json").write_text(
                    json.dumps(pkt, ensure_ascii=False), encoding="utf-8"
                )
            state = {"pipeline_id": "TEST-123"}
            with patch.object(pipeline, "_contract_paths") as mock_paths, \
                 patch.object(pipeline, "_failure_root_from_paths") as mock_root:
                mock_paths.return_value = MagicMock()
                mock_root.return_value = failures_dir
                result = pipeline._failure_retry_summary(state)
        self.assertEqual(result["return_phase_distribution"]["dev"], exp_summary["return_phase_distribution"]["dev"])
        self.assertEqual(result["return_phase_distribution"]["build"], exp_summary["return_phase_distribution"]["build"])


# ---------------------------------------------------------------------------
# TC-04: T-004 — GitHub Actions API 조회 실패 시 확인 불가 (edge oracle)
# ---------------------------------------------------------------------------
class TestGithubActionsDurationSummary(unittest.TestCase):
    def test_tc04_api_unreachable_returns_unavailable(self):
        inp, exp = load_oracle("T-004")
        exp_gh = exp["github_actions_summary"]
        # repo/run_id=None → 즉시 unavailable
        result = pipeline._github_actions_duration_summary(None, None)
        self.assertEqual(result["status"], exp_gh["status"])
        self.assertEqual(result["run_id"], exp_gh["run_id"])
        self.assertEqual(result["conclusion"], exp_gh["conclusion"])

    def test_tc04_no_estimation_on_failure(self):
        """API 실패 시 duration_seconds가 숫자 추정값이 아니어야 함."""
        result = pipeline._github_actions_duration_summary(None, None)
        self.assertIsInstance(result["duration_seconds"], str)
        self.assertEqual(result["duration_seconds"], "확인 불가")

    def test_tc04_unavailable_reason_present(self):
        result = pipeline._github_actions_duration_summary(None, None)
        self.assertIn("unavailable_reason", result)

    def test_tc04_all_required_fields_present(self):
        inp, exp = load_oracle("T-004")
        result = pipeline._github_actions_duration_summary(None, None)
        required_keys = list(exp["github_actions_summary"].keys())
        for key in required_keys:
            self.assertIn(key, result, f"필드 '{key}' 누락")


# ---------------------------------------------------------------------------
# TC-05: T-005 — metrics summary 6개 필수 섹션 포함 (normal oracle)
# ---------------------------------------------------------------------------
class TestFormatMetricsSummaryKo(unittest.TestCase):
    def test_tc05_all_six_sections_present(self):
        inp, exp = load_oracle("T-005")
        metrics = inp["pipeline_metrics"]
        output = pipeline._format_metrics_summary_ko(metrics)
        required_sections = exp["required_sections_present"]
        for section in required_sections:
            self.assertIn(section, output, f"필수 섹션 '{section}' 누락")

    def test_tc05_language_is_korean(self):
        inp, _ = load_oracle("T-005")
        metrics = inp["pipeline_metrics"]
        output = pipeline._format_metrics_summary_ko(metrics)
        # 한국어 키워드 포함 확인
        korean_keywords = ["소요", "시간", "요약", "실패", "Gate"]
        for kw in korean_keywords:
            self.assertIn(kw, output, f"한국어 키워드 '{kw}' 누락")

    def test_tc05_no_estimated_values(self):
        """실제 값이 없을 때 숫자 추정값 대신 '확인 불가'를 출력해야 함."""
        minimal_metrics = {"pipeline_id": "TEST-NO-DATA"}
        output = pipeline._format_metrics_summary_ko(minimal_metrics)
        # 임의 숫자 대신 확인 불가 포함 확인
        self.assertIn("확인 불가", output)


# ---------------------------------------------------------------------------
# TC-06: record_failure_packet optional 필드 backward compatibility
# ---------------------------------------------------------------------------
class TestRecordFailurePacketMetricsFields(unittest.TestCase):
    def test_tc06_backward_compatible_call_still_works(self):
        """기존 호출 방식 (새 인자 없이)도 정상 동작해야 함."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state = {
                "pipeline_id": "TEST-COMPAT-001",
                "failure_packets": [],
                "phase_attestations": {},
            }
            with patch.object(pipeline, "_contract_paths") as mock_cp, \
                 patch.object(pipeline, "_failure_root_from_paths") as mock_fr, \
                 patch.object(pipeline, "_next_failure_attempt", return_value=1), \
                 patch.object(pipeline, "_write_json"), \
                 patch.object(pipeline, "_save_state"), \
                 patch.object(pipeline, "_now", return_value="2026-05-22T00:00:00Z"):
                mock_cp.return_value = MagicMock()
                mock_fr.return_value = pathlib.Path(tmpdir)
                pkt = pipeline._record_failure_packet(
                    state, "technical", {},
                    note="기존 방식 테스트",
                    failure_code="test_error",
                    required_actions=["테스트 조치 1"],
                )
            # optional metrics 필드가 없어야 함 (None이면 포함 안 됨)
            self.assertNotIn("elapsed_before_failure", pkt)
            self.assertNotIn("previous_attempt_count", pkt)

    def test_tc06_new_optional_fields_included_when_provided(self):
        """새 optional 필드를 제공하면 packet에 포함되어야 함."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state = {
                "pipeline_id": "TEST-COMPAT-002",
                "failure_packets": [],
                "phase_attestations": {},
            }
            with patch.object(pipeline, "_contract_paths") as mock_cp, \
                 patch.object(pipeline, "_failure_root_from_paths") as mock_fr, \
                 patch.object(pipeline, "_next_failure_attempt", return_value=1), \
                 patch.object(pipeline, "_write_json"), \
                 patch.object(pipeline, "_save_state"), \
                 patch.object(pipeline, "_now", return_value="2026-05-22T00:00:00Z"):
                mock_cp.return_value = MagicMock()
                mock_fr.return_value = pathlib.Path(tmpdir)
                pkt = pipeline._record_failure_packet(
                    state, "technical", {},
                    note="metrics 필드 테스트",
                    failure_code="test_error",
                    required_actions=["테스트 조치 1"],
                    elapsed_before_failure=3600,
                    previous_attempt_count=2,
                    repeated_failure_count=1,
                    last_same_failure_at="2026-05-21T12:00:00Z",
                    suggested_minimal_rerun_reason="ruff 오류 수정 후 재실행",
                )
            self.assertEqual(pkt["elapsed_before_failure"], 3600)
            self.assertEqual(pkt["previous_attempt_count"], 2)
            self.assertEqual(pkt["repeated_failure_count"], 1)
            self.assertEqual(pkt["last_same_failure_at"], "2026-05-21T12:00:00Z")
            self.assertEqual(pkt["suggested_minimal_rerun_reason"], "ruff 오류 수정 후 재실행")


# ---------------------------------------------------------------------------
# TC-07: _collect_pipeline_metrics 통합 테스트
# ---------------------------------------------------------------------------
class TestCollectPipelineMetrics(unittest.TestCase):
    def test_tc07_collect_returns_all_required_keys(self):
        """_collect_pipeline_metrics 반환값에 필수 키가 모두 포함되어야 함."""
        state = {
            "pipeline_id": "IMP-20260522-0C83",
            "created_at": "2026-05-22T00:10:00Z",
            "updated_at": "2026-05-22T03:45:00Z",
            "phases": {
                "pm": {
                    "status": "DONE",
                    "started_at": "2026-05-22T00:10:00Z",
                    "completed_at": "2026-05-22T00:25:00Z",
                }
            },
            "external_gates": {},
            "agent_runs": {},
        }
        with patch.object(pipeline, "_contract_paths") as mock_cp, \
             patch.object(pipeline, "_failure_root_from_paths") as mock_fr:
            mock_cp.return_value = MagicMock()
            mock_fr.return_value = pathlib.Path("/nonexistent_path_for_test")
            result = pipeline._collect_pipeline_metrics(state)
        required_keys = [
            "pipeline_id", "collected_at", "total_elapsed",
            "phase_elapsed", "gate_elapsed", "failure_retry",
            "github_actions", "agent_sessions", "bottleneck"
        ]
        for key in required_keys:
            self.assertIn(key, result, f"필수 키 '{key}' 누락")

    def test_tc07_pipeline_id_preserved(self):
        state = {
            "pipeline_id": "IMP-20260522-0C83",
            "phases": {},
            "external_gates": {},
            "agent_runs": {},
        }
        with patch.object(pipeline, "_contract_paths") as mock_cp, \
             patch.object(pipeline, "_failure_root_from_paths") as mock_fr:
            mock_cp.return_value = MagicMock()
            mock_fr.return_value = pathlib.Path("/nonexistent_path_for_test")
            result = pipeline._collect_pipeline_metrics(state)
        self.assertEqual(result["pipeline_id"], "IMP-20260522-0C83")


if __name__ == "__main__":
    unittest.main()
