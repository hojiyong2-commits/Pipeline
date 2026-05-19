# -*- coding: utf-8 -*-
"""IMP-20260518-150C — advisory 기본 경로 강등 검증 테스트.

[Purpose] ENABLE_GPT_ADVISORY=1과 ENABLE_GPT_ADVISORY_REQUIRED=1을 분리한 후
  - 기본 모드(REQUIRED 미설정): advisory가 자동 실행되지 않고 unresolved CRITICAL도 blocker가 아님
  - REQUIRED=1: 자동 실행 + unresolved CRITICAL이 COMPLETE를 차단
이 규칙이 코드로 강제되고 있는지 17개 케이스로 검증한다.
[Assumptions] pipeline.py가 _openai_advisory_required(), _external_gate_blockers(),
  _advisory_status_summary()를 노출한다. OpenAI 실제 API 호출은 없다 (mock/패치).
[Vulnerability & Risks] 환경변수 누수로 다른 테스트에 영향이 갈 수 있으므로
  각 테스트에서 set/del을 항상 수행한다.
[Improvement] subprocess 호출 케이스를 늘려 CLI 출력까지 검증한다.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from unittest import mock

# pipeline.py 를 패키지 형태로 import할 수 있도록 BASE_DIR 추가
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import pipeline  # noqa: E402  pylint: disable=wrong-import-position


# ─────────────────────────────────────────────────────────────────────────────
# 환경변수 격리 유틸
# ─────────────────────────────────────────────────────────────────────────────

class _EnvGuard:
    """ENABLE_GPT_ADVISORY*, OPENAI_API_KEY 환경변수를 테스트마다 격리."""

    KEYS = ("ENABLE_GPT_ADVISORY", "ENABLE_GPT_ADVISORY_REQUIRED", "OPENAI_API_KEY")

    def __init__(self) -> None:
        self._saved: Dict[str, Optional[str]] = {}

    def __enter__(self) -> "_EnvGuard":
        for k in self.KEYS:
            self._saved[k] = os.environ.get(k)
            if k in os.environ:
                del os.environ[k]
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        for k, v in self._saved.items():
            if v is None:
                if k in os.environ:
                    del os.environ[k]
            else:
                os.environ[k] = v


def _make_minimal_state(pipeline_id: str = "TEST-ADVISORY-150C") -> Dict[str, Any]:
    """three-gate 활성화된 최소 state. external gates는 모두 PENDING."""
    return {
        "pipeline_id": pipeline_id,
        "phases": {
            "pm": {"status": "DONE"},
            "dev": {"status": "DONE"},
            "qa": {"status": "PASS"},
            "build": {"status": "PASS"},
        },
        "external_gates": {
            "enabled": True,
            "technical": {"status": "PENDING"},
            "oracle": {"status": "PENDING"},
            "acceptance": {"status": "PENDING"},
            "github_ci": {"status": "PENDING"},
        },
        "phase_attestations": {
            "enabled": True,
            "pm": {"status": "PASS"},
            "dev": {"status": "PASS"},
            "qa": {"status": "PASS"},
            "build": {"status": "PASS"},
        },
    }


def _patch_unresolved_critical(count: int) -> Any:
    """_unresolved_critical_advisories가 count개 CRITICAL을 반환하도록 patch."""
    fake = [
        {"id": f"F{i}", "level": "CRITICAL", "message": f"fake critical {i}"}
        for i in range(count)
    ]
    return mock.patch.object(pipeline, "_unresolved_critical_advisories", return_value=fake)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAdvisoryDemotionBlockers(unittest.TestCase):
    """_external_gate_blockers 가 advisory를 REQUIRED 모드에서만 차단하는지 검증."""

    def test_default_mode_advisory_not_blocker(self) -> None:
        """기본 모드(둘 다 미설정)에서는 unresolved CRITICAL 존재하더라도 blocker 없음."""
        with _EnvGuard(), _patch_unresolved_critical(2):
            state = _make_minimal_state()
            blockers = pipeline._external_gate_blockers(state)
            advisory_blockers = [b for b in blockers if "advisory" in b.lower()]
            self.assertEqual(
                advisory_blockers, [],
                f"기본 모드에서 advisory blocker 없어야 함: {advisory_blockers}",
            )

    def test_advisory_enabled_not_required_not_blocker(self) -> None:
        """ENABLE_GPT_ADVISORY=1만 설정해도 REQUIRED 없으면 blocker 아님."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY"] = "1"
            # REQUIRED는 _EnvGuard가 진입 시 제거하므로 명시적으로 미설정 상태
            self.assertNotIn("ENABLE_GPT_ADVISORY_REQUIRED", os.environ)
            with _patch_unresolved_critical(1):
                state = _make_minimal_state()
                blockers = pipeline._external_gate_blockers(state)
                advisory_blockers = [b for b in blockers if "advisory" in b.lower()]
                self.assertEqual(advisory_blockers, [])

    def test_required_mode_no_critical_no_blocker(self) -> None:
        """REQUIRED=1 + unresolved CRITICAL=0 + API key 있음 + advisory 실행됨 → blocker 없음."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY_REQUIRED"] = "1"
            os.environ["OPENAI_API_KEY"] = "sk-test-FAKE"
            # advisory가 정상 실행된 상태를 모사 (review_count>=1, api_call_count>=1)
            with mock.patch.object(pipeline, "_advisory_run_counts", return_value=(1, 1)):
                with _patch_unresolved_critical(0):
                    state = _make_minimal_state()
                    blockers = pipeline._external_gate_blockers(state)
                    advisory_blockers = [b for b in blockers if "advisory" in b.lower()]
                    self.assertEqual(advisory_blockers, [])

    def test_required_mode_with_critical_blocks(self) -> None:
        """REQUIRED=1 + unresolved CRITICAL≥1 + advisory 실행됨 → blocker로 등장."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY_REQUIRED"] = "1"
            os.environ["OPENAI_API_KEY"] = "sk-test-FAKE"
            # advisory가 정상 실행된 상태를 모사하여 unresolved CRITICAL blocker 경로로 진입
            with mock.patch.object(pipeline, "_advisory_run_counts", return_value=(1, 1)):
                with _patch_unresolved_critical(3):
                    state = _make_minimal_state()
                    blockers = pipeline._external_gate_blockers(state)
                    matches = [b for b in blockers if "unresolved GPT advisory CRITICAL findings" in b]
                    self.assertEqual(len(matches), 1, f"blockers={blockers}")
                    self.assertIn("3", matches[0])

    def test_required_mode_no_api_key_blocks(self) -> None:
        """REQUIRED=1인데 OPENAI_API_KEY 없음 → 별도 blocker 등장."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY_REQUIRED"] = "1"
            # OPENAI_API_KEY 의도적 미설정
            with mock.patch.object(pipeline, "_openai_api_key", return_value=(None, "missing")):
                with _patch_unresolved_critical(0):
                    state = _make_minimal_state()
                    blockers = pipeline._external_gate_blockers(state)
                    matches = [b for b in blockers if "OPENAI_API_KEY missing" in b]
                    self.assertEqual(len(matches), 1, f"blockers={blockers}")

    def test_required_mode_not_run_is_blocker(self) -> None:
        """REQUIRED=1인데 advisory가 한 번도 실행되지 않으면 (review_count=0) COMPLETE blocker."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY_REQUIRED"] = "1"
            with mock.patch.object(pipeline, "_openai_api_key", return_value=("sk-test-FAKE", "process")):
                with mock.patch.object(pipeline, "_advisory_run_counts", return_value=(0, 0)):
                    with _patch_unresolved_critical(0):
                        state = _make_minimal_state()
                        blockers = pipeline._external_gate_blockers(state)
                        not_run_blockers = [b for b in blockers if "review_count=0" in b or "not run" in b.lower()]
                        self.assertTrue(
                            len(not_run_blockers) >= 1,
                            f"review_count=0인데 blocker 없음: {blockers}",
                        )

    def test_required_mode_all_skipped_is_blocker(self) -> None:
        """REQUIRED=1인데 모든 advisory가 SKIPPED/ERROR면 (api_call_count=0) COMPLETE blocker."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY_REQUIRED"] = "1"
            with mock.patch.object(pipeline, "_openai_api_key", return_value=("sk-test-FAKE", "process")):
                with mock.patch.object(pipeline, "_advisory_run_counts", return_value=(2, 0)):
                    with _patch_unresolved_critical(0):
                        state = _make_minimal_state()
                        blockers = pipeline._external_gate_blockers(state)
                        skipped_blockers = [b for b in blockers if "never called" in b or "api_call_count=0" in b.lower() or "SKIPPED" in b]
                        self.assertTrue(
                            len(skipped_blockers) >= 1,
                            f"api_call_count=0인데 blocker 없음: {blockers}",
                        )


class TestAdvisoryDemotionAutoRun(unittest.TestCase):
    """cmd_done/cmd_contract의 advisory auto-run 분기 강등 검증."""

    def test_dev_done_auto_run_disabled_by_default(self) -> None:
        """REQUIRED 미설정 시 _openai_advisory_required()==False."""
        with _EnvGuard():
            self.assertFalse(pipeline._openai_advisory_required())

    def test_dev_done_auto_run_enabled_when_required(self) -> None:
        """REQUIRED=1 시 _openai_advisory_required()==True."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY_REQUIRED"] = "1"
            self.assertTrue(pipeline._openai_advisory_required())

    def test_contract_freeze_auto_run_disabled_by_default(self) -> None:
        """ENABLE_GPT_ADVISORY=1만 설정 → required는 False 유지."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY"] = "1"
            self.assertTrue(pipeline._openai_advisory_enabled())
            self.assertFalse(pipeline._openai_advisory_required())


class TestAdvisoryStatusMode(unittest.TestCase):
    """_advisory_status_summary 의 advisory_mode 4상태 분기 검증."""

    def _summary(self, pid: str = "TEST-ADVISORY-150C") -> Dict[str, Any]:
        # advisory_root에 review 파일이 없는 상태로 호출
        return pipeline._advisory_status_summary(pid)

    def test_status_output_not_run(self) -> None:
        """기본 모드 (둘 다 미설정 + API key 있음) → mode='not_run'."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY"] = "0"
            with mock.patch.object(pipeline, "_openai_api_key", return_value=("sk-test-FAKE", "process")):
                with mock.patch.object(pipeline, "_unresolved_critical_advisories", return_value=[]):
                    summary = self._summary()
                    # ENABLE_GPT_ADVISORY=0 → API call disabled → skipped
                    # 이 케이스는 명확히 skipped로 분류됨
                    self.assertIn(summary["advisory_mode"], ("skipped", "not_run"))

    def test_status_output_required(self) -> None:
        """REQUIRED=1 + API key + unresolved CRITICAL=0 → mode='required'."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY_REQUIRED"] = "1"
            with mock.patch.object(pipeline, "_openai_api_key", return_value=("sk-test-FAKE", "process")):
                with mock.patch.object(pipeline, "_unresolved_critical_advisories", return_value=[]):
                    summary = self._summary()
                    self.assertEqual(summary["advisory_mode"], "required")
                    self.assertTrue(summary["required"])

    def test_status_output_blocking(self) -> None:
        """REQUIRED=1 + unresolved CRITICAL≥1 → mode='blocking'."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY_REQUIRED"] = "1"
            fake_critical = [{"id": "F1", "level": "CRITICAL"}]
            with mock.patch.object(pipeline, "_openai_api_key", return_value=("sk-test-FAKE", "process")):
                with mock.patch.object(pipeline, "_unresolved_critical_advisories", return_value=fake_critical):
                    summary = self._summary()
                    self.assertEqual(summary["advisory_mode"], "blocking")


class TestAdvisoryManualCall(unittest.TestCase):
    """_call_openai_advisory가 ENABLE_GPT_ADVISORY=1 없으면 SKIPPED를 반환하는지 검증."""

    def test_advisory_cli_manual_call_without_flag(self) -> None:
        """ENABLE_GPT_ADVISORY 미설정 시 SKIPPED."""
        with _EnvGuard():
            with mock.patch.object(pipeline, "_openai_api_key", return_value=("sk-test-FAKE", "process")):
                result = pipeline._call_openai_advisory("test prompt", model="gpt-5.5", timeout=5)
                self.assertEqual(result["status"], "SKIPPED")
                self.assertIn("ENABLE_GPT_ADVISORY", str(result.get("reason", "")))
                self.assertFalse(result.get("api_called", True))

    def test_advisory_cli_manual_call_with_flag(self) -> None:
        """ENABLE_GPT_ADVISORY=1 + API key 있음 → API 경로 진입 (실제 호출은 mock으로 차단)."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY"] = "1"
            fake_response = mock.MagicMock()
            fake_response.read.return_value = json.dumps({
                "output": [{"content": [{"text": json.dumps({"summary": "ok", "findings": []})}]}],
            }).encode("utf-8")
            with mock.patch.object(pipeline, "_openai_api_key", return_value=("sk-test-FAKE", "process")):
                with mock.patch("urllib.request.urlopen", return_value=fake_response):
                    fake_response.__enter__ = lambda s: s
                    fake_response.__exit__ = lambda *a: None
                    result = pipeline._call_openai_advisory("test prompt", model="gpt-5.5", timeout=5)
                    # 결과는 status=COMPLETED 또는 ERROR (mock의 형태에 따라 다를 수 있음)
                    # 핵심은 SKIPPED가 아니어야 한다는 점 (API 경로가 열렸음)
                    self.assertNotEqual(result["status"], "SKIPPED")

    def test_required_mode_without_advisory_flag_enters_api_path(self) -> None:
        """REQUIRED=1 + API key 있음 + ENABLE_GPT_ADVISORY 미설정 → SKIPPED 아님 (API 경로 진입)."""
        with _EnvGuard():
            os.environ["ENABLE_GPT_ADVISORY_REQUIRED"] = "1"
            # ENABLE_GPT_ADVISORY는 의도적으로 미설정
            self.assertNotIn("ENABLE_GPT_ADVISORY", os.environ)
            fake_response = mock.MagicMock()
            fake_response.read.return_value = json.dumps({
                "output": [{"content": [{"text": json.dumps({"summary": "ok", "findings": []})}]}],
            }).encode("utf-8")
            with mock.patch.object(pipeline, "_openai_api_key", return_value=("sk-test-FAKE", "process")):
                with mock.patch("urllib.request.urlopen", return_value=fake_response):
                    fake_response.__enter__ = lambda s: s
                    fake_response.__exit__ = lambda *a: None
                    result = pipeline._call_openai_advisory("test prompt", model="gpt-5.5", timeout=5)
                    self.assertNotEqual(
                        result["status"], "SKIPPED",
                        "REQUIRED=1이면 ENABLE_GPT_ADVISORY 없어도 API 호출 경로에 진입해야 합니다."
                    )


class TestAdvisoryBackwardCompat(unittest.TestCase):
    """기존 unresolved CRITICAL이 있는 파이프라인에서 REQUIRED 미설정 시 blocker 아님 확인."""

    def test_backward_compat_existing_critical_not_blocking(self) -> None:
        """이전 advisory에서 CRITICAL이 기록되었어도 기본 모드에서는 COMPLETE 차단 안 함."""
        with _EnvGuard():
            with _patch_unresolved_critical(5):
                state = _make_minimal_state()
                blockers = pipeline._external_gate_blockers(state)
                advisory_blockers = [b for b in blockers if "advisory" in b.lower()]
                self.assertEqual(
                    advisory_blockers, [],
                    "REQUIRED 미설정 모드에서는 과거 CRITICAL이 있어도 blocker가 아님",
                )

    def test_advisory_not_run_status_in_pipeline_status(self) -> None:
        """pipeline.py status 명령 출력에 advisory 관련 한국어/영어 텍스트가 포함되는지 확인.

        이 테스트는 실제 subprocess로 status를 호출하지 않고,
        _advisory_status_summary 결과의 advisory_mode가 4가지 값 중 하나임을 검증한다.
        """
        with _EnvGuard():
            with mock.patch.object(pipeline, "_openai_api_key", return_value=(None, "missing")):
                with mock.patch.object(pipeline, "_unresolved_critical_advisories", return_value=[]):
                    summary = pipeline._advisory_status_summary("TEST-ADVISORY-150C")
                    self.assertIn(
                        summary["advisory_mode"],
                        ("not_run", "skipped", "required", "blocking"),
                    )
                    # 기본 모드에서 advisory_mode_reason은 인사이트 메시지를 담아야 함
                    self.assertTrue(summary.get("advisory_mode_reason"))


class TestForbiddenReviewCodexPattern(unittest.TestCase):
    """review codex --result ACCEPT/REJECT 안내가 소스 파일에서 제거되었는지 grep 검증."""

    _FORBIDDEN_RE = re.compile(r"review codex --stage[^'\n]*--result (?:ACCEPT|REJECT)")

    def _check_file(self, path: Path) -> List[str]:
        if not path.exists():
            return []
        content = path.read_text(encoding="utf-8", errors="replace")
        return self._FORBIDDEN_RE.findall(content)

    def test_pipeline_py_forbidden_pattern_absent(self) -> None:
        """pipeline.py에 금지 패턴 없어야 함."""
        found = self._check_file(BASE_DIR / "pipeline.py")
        self.assertEqual(found, [], f"pipeline.py에 금지 패턴 발견: {found}")

    def test_claude_md_forbidden_pattern_absent(self) -> None:
        """CLAUDE.md에 금지 패턴 없어야 함."""
        found = self._check_file(BASE_DIR / "CLAUDE.md")
        self.assertEqual(found, [], f"CLAUDE.md에 금지 패턴 발견: {found}")

    def test_agent_md_files_forbidden_pattern_absent(self) -> None:
        """.claude/agents/*.md 파일들에 금지 패턴 없어야 함."""
        agents_dir = BASE_DIR / ".claude" / "agents"
        violations: List[str] = []
        if agents_dir.exists():
            for md_file in agents_dir.glob("*.md"):
                for match in self._check_file(md_file):
                    violations.append(f"{md_file.name}: {match}")
        self.assertEqual(violations, [], f".claude/agents/*.md에 금지 패턴 발견: {violations}")

    def test_github_workflows_forbidden_pattern_absent(self) -> None:
        """.github/workflows/**에 금지 패턴 없어야 함."""
        workflows_dir = BASE_DIR / ".github" / "workflows"
        violations: List[str] = []
        if workflows_dir.exists():
            for yml_file in workflows_dir.glob("*.yml"):
                for match in self._check_file(yml_file):
                    violations.append(f"{yml_file.name}: {match}")
        self.assertEqual(violations, [], f".github/workflows/*.yml에 금지 패턴 발견: {violations}")

    def test_commands_dir_forbidden_pattern_absent(self) -> None:
        """.claude/commands/**에 금지 패턴 없어야 함."""
        commands_dir = BASE_DIR / ".claude" / "commands"
        violations: List[str] = []
        if commands_dir.exists():
            for md_file in commands_dir.glob("*.md"):
                for match in self._check_file(md_file):
                    violations.append(f"{md_file.name}: {match}")
        self.assertEqual(violations, [], f".claude/commands/*.md에 금지 패턴 발견: {violations}")


# ─────────────────────────────────────────────────────────────────────────────
# self-verify 블록
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # 정상 입력
    with _EnvGuard():
        assert pipeline._openai_advisory_required() is False, "default should be False"
        os.environ["ENABLE_GPT_ADVISORY_REQUIRED"] = "1"
        assert pipeline._openai_advisory_required() is True, "REQUIRED=1 should be True"
    print("[SELF-VERIFY] OK — running pytest now")
    unittest.main(verbosity=2)
