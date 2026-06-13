"""tests/test_codex_removal_8104.py

IMP-20260612-8104: Codex Review Gate 제거 회귀 테스트

검증 대상:
  - pipeline.py에서 Codex Review CLI 서브커맨드가 완전히 제거됨
  - check --phase dev가 codex gate 없이 동작함 (다른 gate에 의해서만 차단)
  - check --phase qa가 codex gate 없이 동작함
  - --codex-review-waiver 옵션이 제거됨
  - cmd_codex doctor는 여전히 동작함 (보존)
  - GPT advisory 서브커맨드는 여전히 동작함 (보존)

IMP-20260525-6FAC 요건 준수:
  - subprocess 기반 실제 CLI 실행 (내부 함수 직접 import 금지)
  - PIPELINE_STATE_PATH 환경 변수로 격리된 state 파일 사용
  - final_state assertion 포함 (stdout-only 검증 금지)

주의 (argparse 종료 코드):
  argparse는 잘못된 서브커맨드("invalid choice")나 알 수 없는 옵션
  ("unrecognized arguments")에 대해 exit code 2를 반환합니다. 따라서
  "제거됨" 검증은 exit code != 0 으로 단정하며, 오류 메시지 문자열
  ("invalid choice" / "unrecognized")로 제거 의도를 추가 확인합니다.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 경로 설정
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_PY = PROJECT_ROOT / "pipeline.py"


def _run(
    args: List[str],
    state_path: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 60,
) -> "subprocess.CompletedProcess[str]":
    """python pipeline.py <args> 실제 CLI 실행.

    Args:
        args: pipeline.py에 전달할 인자 리스트.
        state_path: 격리된 임시 state 파일 경로 (PIPELINE_STATE_PATH로 주입).
        env: 추가 환경 변수.
        timeout: 초 단위 타임아웃.

    Returns:
        subprocess.CompletedProcess 인스턴스 (utf-8 디코딩).

    Raises:
        TypeError: args가 리스트가 아닐 때.
    """
    if args is None:
        raise TypeError("args must not be None")
    if not isinstance(args, list):
        raise TypeError(f"args must be list, got {type(args).__name__}")
    full_env = os.environ.copy()
    if state_path is not None:
        full_env["PIPELINE_STATE_PATH"] = str(state_path)
    full_env["PIPELINE_NO_DASHBOARD"] = "1"
    if env:
        full_env.update(env)
    cmd = [sys.executable, str(PIPELINE_PY)] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",      # Windows cp949 디코드 오류 방지 (필수)
        errors="replace",
        timeout=timeout,
        env=full_env,
    )


def _make_minimal_dev_state(
    pipeline_id: str = "IMP-20260612-8104",
) -> Dict[str, Any]:
    """check --phase dev 테스트용 최소 유효 state.

    PM 완료(DONE) + Dev PENDING 상태로, codex gate가 제거되었으므로
    이 state로 check --phase dev는 GATE OK(exit 0)여야 한다.

    Args:
        pipeline_id: 파이프라인 ID.

    Returns:
        최소 state 딕셔너리.
    """
    return {
        "version": "1.2.0",
        "pipeline_id": pipeline_id,
        "type": "IMP",
        "description": "Codex 제거 회귀 테스트용",
        "current_phase": "dev",
        "terminal_state": None,
        "blocked": False,
        "blocked_reason": None,
        "phases": {
            "pm": {"status": "DONE"},
            "dev": {"status": "PENDING"},
            "qa": {"status": "PENDING"},
            "sec": {"status": "PENDING"},
            "build": {"status": "PENDING"},
            "harness": {"status": "PENDING"},
            "architect": {"status": "PENDING"},
        },
        "external_gates": {"enabled": True, "mode": "three_gate"},
        "codex_review_gates": {},
        "phase_attestations": {
            "enabled": True,
            "mode": "github_actions_per_phase",
            "required_phases": ["pm", "dev", "qa", "build"],
            "phases": {
                "pm": {"status": "PASS"},
                "dev": {"status": "PENDING"},
                "qa": {"status": "PENDING"},
                "build": {"status": "PENDING"},
            },
        },
    }


def _write_state(state_path: Path, state: Dict[str, Any]) -> None:
    """state dict를 JSON으로 직렬화하여 격리 state 파일에 기록.

    Args:
        state_path: 기록할 파일 경로.
        state: 기록할 state 딕셔너리.

    Raises:
        TypeError: state_path가 None이거나 state가 dict가 아닐 때.
    """
    if state_path is None:
        raise TypeError("state_path must not be None")
    if not isinstance(state, dict):
        raise TypeError(f"state must be dict, got {type(state).__name__}")
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class TestCodexRemoval(unittest.TestCase):
    """IMP-20260612-8104: Codex Review Gate CLI 제거 회귀 검증."""

    # ── 1. review codex-run 서브커맨드 제거 ─────────────────────────────

    def test_review_subcommand_removed(self) -> None:
        """`pipeline.py review codex-run` 실행 시 exit != 0 + invalid choice.

        review 서브커맨드 자체가 argparse에서 제거되었으므로
        "invalid choice: 'review'" 오류가 출력되어야 한다.
        """
        proc = _run(["review", "codex-run", "--stage", "code"])  # CLI_EVIDENCE_ALLOW_READ_ONLY: review 서브커맨드 제거 확인 — 오류 출력만, 상태 변경 없음
        self.assertNotEqual(
            proc.returncode, 0,
            f"review codex-run은 제거되어 실패해야 함. rc={proc.returncode}",
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn(
            "invalid choice", combined,
            f"review 서브커맨드 제거 메시지가 없음. 출력: {combined[:400]}",
        )
        self.assertIn(
            "'review'", combined,
            "오류 메시지에 'review' 서브커맨드 언급이 있어야 함",
        )

    # ── 2. review codex-record 서브커맨드 제거 ──────────────────────────

    def test_review_codex_record_removed(self) -> None:
        """`pipeline.py review codex-record` 실행 시 exit != 0.

        review codex-record는 codex review gate 기록 명령으로 제거됨.
        """
        proc = _run(
            ["review", "codex-record", "--stage", "code", "--result", "ACCEPT"]  # CLI_EVIDENCE_ALLOW_READ_ONLY: review codex-record 제거 확인 — 오류 출력만, 상태 변경 없음
        )
        self.assertNotEqual(
            proc.returncode, 0,
            f"review codex-record는 제거되어 실패해야 함. rc={proc.returncode}",
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn(
            "invalid choice", combined,
            f"review codex-record 제거 메시지가 없음. 출력: {combined[:400]}",
        )

    # ── 3. --codex-review-waiver 옵션 제거 ──────────────────────────────

    def test_codex_waiver_option_removed(self) -> None:
        """`check --phase dev --codex-review-waiver` 실행 시 exit != 0 + unrecognized.

        codex review gate가 제거되었으므로 waiver 옵션도 함께 제거되어야 한다.
        argparse는 알 수 없는 옵션에 "unrecognized arguments"를 출력한다.
        """
        proc = _run(
            ["check", "--phase", "dev", "--codex-review-waiver", "legacy-bootstrap"]
        )
        self.assertNotEqual(
            proc.returncode, 0,
            f"--codex-review-waiver는 제거되어 실패해야 함. rc={proc.returncode}",
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertIn(
            "unrecognized", combined,
            f"--codex-review-waiver 제거 메시지가 없음. 출력: {combined[:400]}",
        )

    # ── 4. check --phase dev 출력에 codex 부재 (격리 state) ─────────────

    def test_check_phase_dev_no_codex_output(self) -> None:
        """격리 state로 check --phase dev 실행 시 출력에 'codex' 문자열이 없다.

        PIPELINE_STATE_PATH 격리 + PM DONE state 사용.
        codex review gate가 제거되었으므로:
          - exit code 0 (GATE OK) — 다른 선행 gate가 모두 충족되면 통과
          - 출력 어디에도 codex 관련 메시지가 없어야 함
        final_state assertion: state 파일이 codex gate 호출로 변형되지 않아야 함.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "pipeline_state.json"
            _write_state(state_path, _make_minimal_dev_state())

            proc = _run(["check", "--phase", "dev"], state_path=state_path)

            combined = (proc.stdout or "") + (proc.stderr or "")
            # codex 관련 메시지(특히 [CODEX REVIEW REQUIRED])가 없어야 함
            self.assertNotIn(
                "codex", combined.lower(),
                f"check --phase dev 출력에 codex 언급이 있으면 안 됨. 출력: {combined[:500]}",
            )
            # codex gate 제거 후 PM DONE state는 GATE OK(exit 0)여야 함
            self.assertEqual(
                proc.returncode, 0,
                f"codex gate 제거 후 check --phase dev는 통과(0)여야 함. "
                f"rc={proc.returncode}, 출력: {combined[:500]}",
            )
            # final_state assertion: 격리 state 파일은 여전히 유효 JSON이고
            # current_phase가 보존되어야 한다 (check는 read-only).
            self.assertTrue(state_path.exists(), "격리 state 파일이 유지되어야 함")
            final_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                final_state.get("current_phase"), "dev",
                "check --phase dev는 read-only이므로 current_phase가 보존되어야 함",
            )
            self.assertEqual(
                final_state.get("pipeline_id"), "IMP-20260612-8104",
                "격리 state의 pipeline_id가 보존되어야 함",
            )

    # ── 5. codex doctor 서브커맨드 보존 ─────────────────────────────────

    def test_cmd_codex_doctor_preserved(self) -> None:
        """`pipeline.py codex doctor --json` 서브커맨드는 보존되어 실행 가능하다.

        codex doctor는 Codex CLI 환경 진단 기능으로, codex *review* gate와
        별개의 기능이므로 제거 대상이 아니다.

        주의: codex doctor의 exit code는 환경 파일 존재 여부 등 진단 결과에
        따라 0(통과) 또는 1(일부 점검 실패)로 달라진다. 따라서 exit code를
        고정값으로 단정하지 않고, 서브커맨드가 argparse에서 거부되지 않음
        (invalid choice 없음) + JSON 형식 출력 생성 여부로 보존을 검증한다.
        """
        proc = _run(["codex", "doctor", "--json"])
        combined = (proc.stdout or "") + (proc.stderr or "")
        # argparse가 codex 서브커맨드를 거부하면 안 됨 (보존 확인)
        self.assertNotIn(
            "invalid choice", combined,
            f"codex doctor 서브커맨드가 제거되면 안 됨. 출력: {combined[:400]}",
        )
        # JSON 출력이 파싱 가능해야 함 (doctor 진단 결과)
        stdout = (proc.stdout or "").strip()
        self.assertTrue(stdout, "codex doctor --json은 stdout 출력이 있어야 함")
        try:
            doctor_result = json.loads(stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"codex doctor --json 출력이 유효한 JSON이 아님: {exc}\n{stdout[:400]}")
        self.assertIn(
            "status", doctor_result,
            "codex doctor --json 결과에 status 필드가 있어야 함",
        )

    # ── 6. GPT advisory 서브커맨드 보존 ─────────────────────────────────

    def test_gpt_advisory_preserved(self) -> None:
        """`pipeline.py advisory status` 서브커맨드는 보존되어 exit 0으로 동작한다.

        advisory(GPT 자문)는 codex review gate와 별개의 기능이므로
        제거 대상이 아니며 정상 동작해야 한다.

        advisory 서브커맨드는 상태 변경 CLI이므로 PIPELINE_STATE_PATH 격리 사용.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "advisory_status_state.json"
            state = _make_minimal_dev_state()
            # advisory status 실행을 위해 harness 단계로 진행된 상태
            state["current_phase"] = "harness"
            state["phases"]["dev"]["status"] = "DONE"
            state["phases"]["qa"]["status"] = "DONE"
            state["phases"]["sec"]["status"] = "DONE"
            state["phases"]["build"]["status"] = "DONE"
            _write_state(state_path, state)
            proc = _run(["advisory", "status"], state_path=state_path)
            combined = (proc.stdout or "") + (proc.stderr or "")
            self.assertNotIn(
                "invalid choice", combined,
                f"advisory 서브커맨드가 제거되면 안 됨. 출력: {combined[:400]}",
            )
            self.assertEqual(
                proc.returncode, 0,
                f"advisory status는 exit 0이어야 함. rc={proc.returncode}, 출력: {combined[:400]}",
            )
            # evidence assertion: advisory_mode 필드가 응답에 포함되는지 확인
            self.assertTrue(
                "advisory_mode" in combined or "not_run" in combined or "skipped" in combined,
                f"advisory status 출력에 모드 정보가 없음: {combined[:400]}",
            )
            # final_state assertion: advisory status는 read-only 조회이므로
            # current_phase가 변경되면 안 된다 (harness 유지).
            self.assertTrue(state_path.exists(), "격리 state 파일이 유지되어야 함")
            final_state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(
                final_state.get("current_phase"), "harness",
                "advisory status는 read-only이므로 current_phase가 변경되면 안 됨",
            )


if __name__ == "__main__":
    # tests/ 폴더 기반 실행과 별개로 직접 실행 시 자가 검증
    unittest.main(verbosity=2)
