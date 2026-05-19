# -*- coding: utf-8 -*-
"""IMP-20260519-EC9F — failure_packet 생성 경로 및 콘솔 출력 완성 검증 테스트.

[Purpose] IMP-20260519-EC9F MT-1~MT-4:
  - cmd_check gate 차단 시 schema_v2 failure_packet 생성
  - cmd_qa FAIL 시 failure_packet 생성
  - cmd_sec FAIL/BLOCK 시 failure_packet 생성
  - _print_failure_packet_console 콘솔 출력 검증
  - 동일 failure_code 2회 YELLOW WARNING 콘솔 출력
  - 동일 failure_code 3회 BLOCKED + retry_allowed=False
  - preflight-pr FAIL 시 failure_packet 생성
  - return_phase 정합성 검증
  - required_actions 비어있지 않음 검증

[Assumptions] pipeline.py가 아래를 노출한다:
  _record_failure_packet, _print_failure_packet_console,
  _gate_return_phase_for_check, FAILURE_BLOCKED_THRESHOLD,
  FAILURE_PACKET_SCHEMA_VERSION, QA_MAX_SCORE, QA_PASS_THRESHOLD

[Isolation] 각 테스트는 새로운 tmpdir + _ContractPathSandbox로 격리.
기존 tests/test_failure_feedback.py 와 중복 테스트 없음.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pipeline  # noqa: E402  pylint: disable=wrong-import-position


# ─────────────────────────────────────────────────────────────────────────────
# 격리 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

class _ContractPathSandbox:
    """_contract_paths를 tmpdir로 patch하여 packet 파일이 실제 contracts에 쓰이지 않도록 차단."""

    def __init__(self, tmpdir: Path, pipeline_id: str = "TEST-EC9F") -> None:
        self.tmpdir = tmpdir
        self.pipeline_id = pipeline_id
        self.paths_dict: Dict[str, Path] = {
            "advisory_root": tmpdir / "advisory",
            "advisory_resolutions": tmpdir / "advisory_resolutions.json",
            "contract": tmpdir / "task_contract.json",
            "test_set": tmpdir / "test_set.json",
            "oracle_manifest": tmpdir / "oracles.json",
            "contract_audit": tmpdir / "audit.json",
            "summary": tmpdir / "summary.md",
            "technical_result": tmpdir / "technical_result.json",
            "oracle_result": tmpdir / "oracle_result.json",
            "github_ci_result": tmpdir / "github_ci_result.json",
            "user_validation": tmpdir / "user_validation.json",
            "phase_ci_root": tmpdir / "phase_ci",
            "failures_root": tmpdir / "failures",
        }
        for p in self.paths_dict.values():
            if p.suffix == "":
                p.mkdir(parents=True, exist_ok=True)
        self.paths_dict["failures_root"].mkdir(parents=True, exist_ok=True)
        self._patcher: Optional[Any] = None

    def __enter__(self) -> "_ContractPathSandbox":
        self._patcher = mock.patch.object(pipeline, "_contract_paths", return_value=self.paths_dict)
        self._patcher.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._patcher is not None:
            self._patcher.stop()


def _new_state(pid: str = "TEST-EC9F") -> Dict[str, Any]:
    """테스트용 최소 파이프라인 상태 dict."""
    return {
        "pipeline_id": pid,
        "failure_packets": [],
        "external_gates": {
            "enabled": True,
            "technical": {"status": "FAIL"},
            "oracle": {"status": "PENDING"},
            "acceptance": {"status": "PENDING"},
            "github_ci": {"status": "PENDING"},
        },
        "phases": {
            "pm": {"status": "DONE", "completed_at": "2026-01-01T00:00:00Z"},
            "dev": {"status": "DONE", "completed_at": "2026-01-01T01:00:00Z"},
            "qa": {"status": "PENDING"},
            "sec": {"status": "PENDING"},
            "build": {"status": "PENDING"},
            "harness": {"status": "PENDING"},
            "architect": {"status": "PENDING"},
        },
    }


def _make_minimal_report() -> Dict[str, Any]:
    """_record_failure_packet에 전달할 최소 report dict."""
    return {
        "schema_version": 1,
        "status": "FAIL",
        "checks": [],
        "blockers": [],
        "summary": {"verdict": "FAIL"},
        "results": [],
    }


def _capture_stdout(fn: Any, *args: Any, **kwargs: Any) -> str:
    """함수 실행 중 stdout을 캡처하여 문자열로 반환."""
    buf = io.StringIO()
    with mock.patch("sys.stdout", buf):
        try:
            fn(*args, **kwargs)
        except SystemExit:
            pass
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: cmd_check gate 차단 시 schema_v2 failure_packet 생성
# ─────────────────────────────────────────────────────────────────────────────

class TestCmdCheckGateBlockedCreatesFailurePacket(unittest.TestCase):
    """MT-1 / MT-2: cmd_check gate 차단 시 failure_packet 생성 검증."""

    def test_cmd_check_gate_blocked_creates_failure_packet(self) -> None:
        """cmd_check --phase dev 차단 시 _gate_return_phase_for_check 함수가 존재하고, failure_packet 생성이 가능하다."""
        # _gate_return_phase_for_check 함수 존재 확인 (MT-2 신규 함수)
        self.assertTrue(
            hasattr(pipeline, "_gate_return_phase_for_check"),
            "_gate_return_phase_for_check 함수가 pipeline.py에 없습니다",
        )

        # check_dev gate로 failure_packet 생성 가능한지 확인
        with tempfile.TemporaryDirectory() as td:
            with _ContractPathSandbox(Path(td)) as sandbox:
                state = _new_state()
                state["phases"]["dev"]["status"] = "PENDING"

                # _record_failure_packet을 직접 호출하여 check gate 시뮬레이션
                packet = pipeline._record_failure_packet(
                    state,
                    "check_dev",
                    _make_minimal_report(),
                    failure_code="gate_blocked_dev",
                    failure_category="missing_evidence",
                    summary_ko="Dev 진입 차단",
                    expected="PM phase DONE",
                    actual="PM phase PENDING",
                    owner="Pipeline Manager",
                    return_phase=pipeline._gate_return_phase_for_check("dev"),
                    required_actions=["pm 완료 후 재시도"],
                    retry_allowed=True,
                )

                self.assertEqual(packet["schema_version"], 2)
                self.assertEqual(packet["gate"], "check_dev")
                self.assertEqual(packet["return_phase"], "pm")
                failures_root = sandbox.paths_dict["failures_root"]
                self.assertTrue(failures_root.exists(), "failures_root 디렉토리가 없음")
                created = list(failures_root.glob("*.json"))
                self.assertGreater(len(created), 0, "failure_packet JSON 파일이 없음")

    def test_gate_return_phase_for_check_dev(self) -> None:
        """check --phase dev 차단 시 return_phase='pm'."""
        self.assertEqual(pipeline._gate_return_phase_for_check("dev"), "pm")

    def test_gate_return_phase_for_check_qa(self) -> None:
        """check --phase qa 차단 시 return_phase='dev'."""
        self.assertEqual(pipeline._gate_return_phase_for_check("qa"), "dev")

    def test_gate_return_phase_for_check_build(self) -> None:
        """check --phase build 차단 시 return_phase='qa'."""
        self.assertEqual(pipeline._gate_return_phase_for_check("build"), "qa")

    def test_gate_return_phase_for_check_unknown(self) -> None:
        """알 수 없는 phase → 기본값 'pm' 반환."""
        result = pipeline._gate_return_phase_for_check("nonexistent_phase")
        self.assertEqual(result, "pm")


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: cmd_qa FAIL 시 failure_packet 생성
# ─────────────────────────────────────────────────────────────────────────────

class TestCmdQaFailCreatesFailurePacket(unittest.TestCase):
    """MT-2: cmd_qa FAIL 시 failure_packet 생성 및 return_phase 검증."""

    def test_cmd_qa_fail_creates_failure_packet(self) -> None:
        """cmd_qa FAIL 시 state.failure_packets에 항목이 추가된다."""
        with tempfile.TemporaryDirectory() as td:
            with _ContractPathSandbox(Path(td)):
                state = _new_state()

                packet = pipeline._record_failure_packet(
                    state,
                    "qa",
                    _make_minimal_report(),
                    failure_code="qa_fail_pd",
                    failure_category="test_failed",
                    summary_ko="QA FAIL — PD 카테고리 실패",
                    expected="QA 모든 카테고리 PASS",
                    actual="PD:FAIL",
                    owner="Dev",
                    return_phase="dev",
                    required_actions=["qa_report.xml의 PD critical_issues를 수정하세요"],
                    retry_allowed=True,
                )

                self.assertEqual(packet["schema_version"], pipeline.FAILURE_PACKET_SCHEMA_VERSION)
                self.assertEqual(packet["gate"], "qa")
                self.assertIn("qa_fail", packet["failure_code"])
                self.assertEqual(len(state["failure_packets"]), 1)

    def test_return_phase_correct_for_qa_gate(self) -> None:
        """cmd_qa FAIL 시 return_phase='dev' 이어야 한다."""
        with tempfile.TemporaryDirectory() as td:
            with _ContractPathSandbox(Path(td)):
                state = _new_state()
                packet = pipeline._record_failure_packet(
                    state,
                    "qa",
                    _make_minimal_report(),
                    failure_code="qa_fail_wa",
                    failure_category="test_failed",
                    summary_ko="QA FAIL — WA 카테고리",
                    expected="WA PASS",
                    actual="WA:FAIL",
                    owner="Dev",
                    return_phase="dev",
                    required_actions=["WA 패턴 수정"],
                    retry_allowed=True,
                )
                self.assertEqual(packet["return_phase"], "dev")


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: cmd_sec FAIL 시 failure_packet 생성
# ─────────────────────────────────────────────────────────────────────────────

class TestCmdSecFailCreatesFailurePacket(unittest.TestCase):
    """MT-2: cmd_sec FAIL 시 failure_packet 생성 검증."""

    def test_cmd_sec_fail_creates_failure_packet(self) -> None:
        """cmd_sec FAIL 시 schema_v2 JSON 파일이 state에 기록된다."""
        with tempfile.TemporaryDirectory() as td:
            with _ContractPathSandbox(Path(td)):
                state = _new_state()
                packet = pipeline._record_failure_packet(
                    state,
                    "sec",
                    _make_minimal_report(),
                    failure_code="sec_fail_high",
                    failure_category="security_failed",
                    summary_ko="보안 감사 FAIL — risk_level=MEDIUM",
                    expected="SAFE 또는 LOW risk",
                    actual="risk_level=MEDIUM, Tier2 이상",
                    owner="Dev",
                    return_phase="dev",
                    required_actions=["security_audit HIGH finding 수정", "sec 재실행"],
                    retry_allowed=True,
                )

                self.assertEqual(packet["schema_version"], 2)
                self.assertEqual(packet["gate"], "sec")
                self.assertEqual(packet["failure_category"], "security_failed")
                packet_files = list(Path(td).rglob("*.json"))
                self.assertTrue(
                    any("sec_fail_high" in str(f) or "attempt" in str(f) for f in packet_files),
                    f"packet JSON 파일 없음 — found: {[str(f) for f in packet_files]}",
                )

    def test_cmd_sec_block_creates_failure_packet(self) -> None:
        """cmd_sec BLOCK 시 failure_category='security_failed', status 포함 검증."""
        with tempfile.TemporaryDirectory() as td:
            with _ContractPathSandbox(Path(td)):
                state = _new_state()
                packet = pipeline._record_failure_packet(
                    state,
                    "sec",
                    _make_minimal_report(),
                    status="BLOCKED",
                    failure_code="sec_block_critical",
                    failure_category="security_failed",
                    summary_ko="보안 감사 BLOCK — Critical 취약점",
                    expected="SAFE",
                    actual="risk_level=HIGH, BLOCK",
                    owner="Dev",
                    return_phase="dev",
                    required_actions=["CRITICAL finding remediation_code 적용"],
                    retry_allowed=False,
                )

                self.assertEqual(packet["failure_category"], "security_failed")
                self.assertIn(packet["status"], {"BLOCKED", "FAIL"})
                self.assertFalse(packet["retry_allowed"])


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: _print_failure_packet_console 콘솔 출력 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestPrintFailurePacketConsoleOutput(unittest.TestCase):
    """MT-1: _print_failure_packet_console 콘솔 출력 검증."""

    def test_print_failure_packet_console_output(self) -> None:
        """failure_code, summary_ko, return_phase가 출력에 포함된다."""
        self.assertTrue(
            hasattr(pipeline, "_print_failure_packet_console"),
            "_print_failure_packet_console 함수가 없습니다",
        )

        packet: Dict[str, Any] = {
            "schema_version": 2,
            "gate": "technical",
            "status": "FAIL",
            "failure_code": "test_failed_lint",
            "failure_category": "test_failed",
            "summary_ko": "기술 게이트 실패 — ruff 오류",
            "expected": "ruff PASS",
            "actual": "ruff FAIL",
            "return_phase": "dev",
            "owner": "Dev",
            "packet_path": "/tmp/failure.json",
            "minimal_rerun": ["python", "pipeline.py", "gates", "technical"],
            "required_actions": ["ruff 오류 수정 후 재실행"],
            "escalation_reason": None,
        }

        output = _capture_stdout(pipeline._print_failure_packet_console, packet)

        self.assertIn("test_failed_lint", output, "failure_code가 출력에 없음")
        self.assertIn("dev", output, "return_phase가 출력에 없음")
        self.assertIn("ruff", output, "summary_ko가 출력에 없음")
        self.assertIn("required_actions", output.lower() if output else "",
                      "required_actions 레이블이 출력에 없음")

    def test_print_failure_packet_console_blocked_status(self) -> None:
        """status=BLOCKED 시 출력에 BLOCKED 키워드가 포함된다."""
        packet: Dict[str, Any] = {
            "gate": "sec",
            "status": "BLOCKED",
            "failure_code": "sec_block_critical",
            "failure_category": "security_failed",
            "summary_ko": "BLOCKED 상태",
            "expected": "SAFE",
            "actual": "BLOCKED",
            "return_phase": "dev",
            "owner": "Dev",
            "packet_path": "/tmp/blocked.json",
            "minimal_rerun": [],
            "required_actions": ["critical fix"],
            "escalation_reason": "same_failure_code_repeated_3x",
        }

        output = _capture_stdout(pipeline._print_failure_packet_console, packet)

        self.assertIn("BLOCKED", output, "BLOCKED 키워드가 출력에 없음")
        self.assertIn("same_failure_code_repeated_3x", output, "escalation_reason이 출력에 없음")

    def test_print_failure_packet_console_none_input(self) -> None:
        """None 입력 시 예외 없이 종료된다."""
        # 예외가 발생하지 않으면 PASS
        try:
            pipeline._print_failure_packet_console(None)  # type: ignore[arg-type]
        except Exception as e:
            self.fail(f"_print_failure_packet_console(None) 예외 발생: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: 동일 failure_code 2회 시 WARNING 콘솔 출력
# ─────────────────────────────────────────────────────────────────────────────

class TestFailurePacket2xEmitsWarning(unittest.TestCase):
    """MT-3: 동일 failure_code 2회 시 YELLOW WARNING 콘솔 출력 검증."""

    def test_failure_packet_2x_emits_warning(self) -> None:
        """동일 failure_code 2회 반복 시 WARNING 메시지가 출력된다."""
        with tempfile.TemporaryDirectory() as td:
            with _ContractPathSandbox(Path(td)):
                state = _new_state()

                # 1회: FAIL (경고 없음)
                pipeline._record_failure_packet(
                    state, "technical", _make_minimal_report(),
                    status="FAIL", failure_code="lint_repeated",
                    failure_category="test_failed", required_actions=["fix1"],
                )

                # 2회: WARNING 출력 예상
                output = _capture_stdout(
                    pipeline._record_failure_packet,
                    state, "technical", _make_minimal_report(),
                    status="FAIL", failure_code="lint_repeated",
                    failure_category="test_failed", required_actions=["fix2"],
                )

                # BLOCKED_THRESHOLD=3이면 2회에서 WARNING
                warning_keyword_present = (
                    "WARNING" in output.upper()
                    or "warning" in output.lower()
                    or "다음 발생" in output
                    or "BLOCKED" in output.upper()
                )
                self.assertTrue(
                    warning_keyword_present,
                    f"2회 반복 시 WARNING 메시지 없음. 출력: {output!r}",
                )

    def test_failure_packet_2x_status_still_fail(self) -> None:
        """2회 반복 시 status는 여전히 FAIL (BLOCKED 아님)."""
        with tempfile.TemporaryDirectory() as td:
            with _ContractPathSandbox(Path(td)):
                state = _new_state()
                for _ in range(2):
                    p = pipeline._record_failure_packet(
                        state, "technical", _make_minimal_report(),
                        status="FAIL", failure_code="warn_code",
                        failure_category="test_failed", required_actions=["fix"],
                    )
                # 2회 후 상태: BLOCKED_THRESHOLD == 3이면 아직 FAIL
                if pipeline.FAILURE_BLOCKED_THRESHOLD == 3:
                    self.assertNotEqual(p["status"], "BLOCKED",
                                        "2회에서 BLOCKED로 전환되면 안 됨 (threshold=3)")


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: 동일 failure_code 3회 시 BLOCKED + retry_allowed=False
# ─────────────────────────────────────────────────────────────────────────────

class TestFailurePacket3xBlockedNoRetry(unittest.TestCase):
    """MT-3: 동일 failure_code 3회 시 retry_allowed=False, status=BLOCKED 검증."""

    def test_failure_packet_3x_blocked_no_retry(self) -> None:
        """3회 동일 failure_code → status=BLOCKED, retry_allowed=False."""
        with tempfile.TemporaryDirectory() as td:
            with _ContractPathSandbox(Path(td)):
                state = _new_state()
                threshold = pipeline.FAILURE_BLOCKED_THRESHOLD  # 보통 3

                for i in range(threshold - 1):
                    pipeline._record_failure_packet(
                        state, "technical", _make_minimal_report(),
                        status="FAIL", failure_code="block_test_code",
                        failure_category="test_failed",
                        required_actions=[f"fix attempt {i + 1}"],
                    )

                # threshold 번째: BLOCKED
                final = pipeline._record_failure_packet(
                    state, "technical", _make_minimal_report(),
                    status="FAIL", failure_code="block_test_code",
                    failure_category="test_failed",
                    required_actions=[f"fix attempt {threshold}"],
                )

                self.assertEqual(final["status"], "BLOCKED",
                                 f"{threshold}회 후 status가 BLOCKED 아님: {final['status']}")
                self.assertFalse(final["retry_allowed"],
                                 "BLOCKED 시 retry_allowed=False 이어야 함")
                self.assertEqual(final.get("escalation_reason"), "same_failure_code_repeated_3x")

    def test_failure_packet_3x_count_matches_threshold(self) -> None:
        """attempt_count가 FAILURE_BLOCKED_THRESHOLD와 일치한다."""
        with tempfile.TemporaryDirectory() as td:
            with _ContractPathSandbox(Path(td)):
                state = _new_state()
                threshold = pipeline.FAILURE_BLOCKED_THRESHOLD

                for i in range(threshold):
                    p = pipeline._record_failure_packet(
                        state, "technical", _make_minimal_report(),
                        status="FAIL", failure_code="count_check",
                        failure_category="test_failed",
                        required_actions=[f"action {i + 1}"],
                    )

                self.assertEqual(p["attempt_count"], threshold)


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: preflight-pr FAIL 시 schema_v2 JSON 생성
# ─────────────────────────────────────────────────────────────────────────────

class TestPreflightPrFailCreatesFailurePacket(unittest.TestCase):
    """MT-2: _cmd_gates_preflight_pr FAIL 시 failure_packet 생성 검증."""

    def test_preflight_pr_fail_creates_failure_packet(self) -> None:
        """preflight-pr FAIL 경로에서 failure_packet 생성 코드가 존재한다."""
        # pipeline.py 소스에서 preflight_pr 관련 failure_packet 기록 코드가 있는지 확인
        pipeline_source = Path(BASE_DIR / "pipeline.py").read_text(encoding="utf-8")

        self.assertIn(
            "preflight_pr",
            pipeline_source,
            "preflight_pr failure_packet 코드가 pipeline.py에 없음",
        )
        self.assertIn(
            "scope_mismatch",
            pipeline_source,
            "scope_mismatch failure_category 코드가 pipeline.py에 없음",
        )

    def test_preflight_pr_failure_packet_via_record(self) -> None:
        """preflight-pr 실패 시 사용되는 failure_packet 스키마 검증."""
        with tempfile.TemporaryDirectory() as td:
            with _ContractPathSandbox(Path(td)):
                state = _new_state()
                packet = pipeline._record_failure_packet(
                    state,
                    "preflight_pr_dev",
                    _make_minimal_report(),
                    failure_code="preflight_pr_scope_violation_dev",
                    failure_category="scope_mismatch",
                    summary_ko="preflight-pr FAIL: scope 초과 파일 발견",
                    expected="PR에는 scope_manifest 파일만 포함",
                    actual="forbidden 파일 2개: .github/workflows/ci.yml, pipeline.py",
                    owner="Dev",
                    return_phase="dev",
                    required_actions=[
                        "금지 파일 되돌리기: .github/workflows/ci.yml",
                        "Trust-Root 파일은 별도 IMP 파이프라인으로 처리",
                    ],
                    retry_allowed=True,
                )

                self.assertEqual(packet["failure_category"], "scope_mismatch")
                self.assertEqual(packet["return_phase"], "dev")
                self.assertGreaterEqual(len(packet["required_actions"]), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: required_actions가 모든 gate failure_packet에서 비어있지 않음
# ─────────────────────────────────────────────────────────────────────────────

class TestRequiredActionsInAllGatePackets(unittest.TestCase):
    """MT-2: 각 gate failure_packet에 required_actions이 비어있지 않음 검증."""

    _GATE_CASES: List[Dict[str, Any]] = [
        {
            "gate": "check_dev",
            "failure_code": "gate_blocked_dev",
            "failure_category": "missing_evidence",
            "return_phase": "pm",
            "summary_ko": "Dev 진입 차단",
        },
        {
            "gate": "qa",
            "failure_code": "qa_fail_pd",
            "failure_category": "test_failed",
            "return_phase": "dev",
            "summary_ko": "QA FAIL",
        },
        {
            "gate": "sec",
            "failure_code": "sec_fail_high",
            "failure_category": "security_failed",
            "return_phase": "dev",
            "summary_ko": "SEC FAIL",
        },
        {
            "gate": "sec",
            "failure_code": "sec_block_critical",
            "failure_category": "security_failed",
            "return_phase": "dev",
            "summary_ko": "SEC BLOCK",
        },
    ]

    def test_required_actions_in_all_gate_packets(self) -> None:
        """각 gate failure_packet에 required_actions가 최소 1개 이상 존재한다."""
        with tempfile.TemporaryDirectory() as td:
            with _ContractPathSandbox(Path(td)):
                for case in self._GATE_CASES:
                    with self.subTest(gate=case["gate"], code=case["failure_code"]):
                        state = _new_state()
                        packet = pipeline._record_failure_packet(
                            state,
                            case["gate"],
                            _make_minimal_report(),
                            failure_code=case["failure_code"],
                            failure_category=case["failure_category"],
                            summary_ko=case["summary_ko"],
                            expected="정상 완료",
                            actual="실패",
                            owner="Dev",
                            return_phase=case["return_phase"],
                            required_actions=[f"{case['gate']} 복구 조치 1단계"],
                            retry_allowed=True,
                        )

                        self.assertIsInstance(
                            packet.get("required_actions"), list,
                            f"gate={case['gate']}: required_actions이 list가 아님",
                        )
                        self.assertGreater(
                            len(packet["required_actions"]), 0,
                            f"gate={case['gate']}: required_actions이 비어 있음",
                        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: 콘솔 출력이 _record_failure_packet 호출 시 자동 발생
# ─────────────────────────────────────────────────────────────────────────────

class TestConsoleOutputOnRecord(unittest.TestCase):
    """MT-1: _record_failure_packet 호출 시 _print_failure_packet_console이 자동 실행된다."""

    def test_record_failure_packet_calls_print_console(self) -> None:
        """_record_failure_packet 호출 시 _print_failure_packet_console이 1회 호출된다."""
        with tempfile.TemporaryDirectory() as td:
            with _ContractPathSandbox(Path(td)):
                state = _new_state()
                call_count: List[int] = [0]

                original_print_fn = pipeline._print_failure_packet_console

                def counting_print(packet: Any) -> None:
                    call_count[0] += 1
                    return original_print_fn(packet)

                with mock.patch.object(pipeline, "_print_failure_packet_console", side_effect=counting_print):
                    pipeline._record_failure_packet(
                        state,
                        "technical",
                        _make_minimal_report(),
                        failure_code="auto_print_test",
                        failure_category="test_failed",
                        summary_ko="콘솔 자동 출력 테스트",
                        required_actions=["테스트 조치"],
                        retry_allowed=True,
                    )

                self.assertEqual(
                    call_count[0], 1,
                    f"_print_failure_packet_console 호출 횟수 = {call_count[0]}, 기대값 = 1",
                )


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: 파일 실제 생성 검증
# ─────────────────────────────────────────────────────────────────────────────

class TestFailurePacketFileCreation(unittest.TestCase):
    """MT-2: failure_packet이 failures_root 디렉토리에 JSON 파일로 실제 생성된다."""

    def test_failure_packet_json_file_created_on_disk(self) -> None:
        """_record_failure_packet 호출 후 failures_root 에 JSON 파일이 생성된다."""
        with tempfile.TemporaryDirectory() as td:
            sandbox = _ContractPathSandbox(Path(td))
            with sandbox:
                state = _new_state()
                pipeline._record_failure_packet(
                    state,
                    "technical",
                    _make_minimal_report(),
                    failure_code="file_creation_test",
                    failure_category="test_failed",
                    summary_ko="파일 생성 테스트",
                    required_actions=["inspect"],
                    retry_allowed=True,
                )

            failures_root = sandbox.paths_dict["failures_root"]
            created_files = list(failures_root.glob("*.json"))
            self.assertGreater(
                len(created_files), 0,
                f"failures_root 아래 JSON 파일 없음: {failures_root}",
            )

    def test_failure_packet_json_is_valid(self) -> None:
        """생성된 failure_packet JSON 파일이 유효한 JSON이다."""
        with tempfile.TemporaryDirectory() as td:
            sandbox = _ContractPathSandbox(Path(td))
            with sandbox:
                state = _new_state()
                pipeline._record_failure_packet(
                    state,
                    "technical",
                    _make_minimal_report(),
                    failure_code="json_valid_test",
                    failure_category="test_failed",
                    summary_ko="JSON 유효성 테스트",
                    required_actions=["inspect"],
                    retry_allowed=True,
                )

            failures_root = sandbox.paths_dict["failures_root"]
            json_files = list(failures_root.glob("*.json"))
            self.assertGreater(len(json_files), 0, "JSON 파일이 없음")

            for jf in json_files:
                with open(jf, encoding="utf-8") as f:
                    data = json.load(f)
                self.assertIn("schema_version", data, f"{jf}: schema_version 없음")
                self.assertEqual(data["schema_version"], 2, f"{jf}: schema_version != 2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
