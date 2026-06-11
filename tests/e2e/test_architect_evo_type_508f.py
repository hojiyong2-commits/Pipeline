"""
test_architect_evo_type_508f.py — IMP-20260611-508F MT-4

목적:
  pipeline.py architect 명령이 <recommended_pipeline_type>이 IMP가 아닌
  잘못된 값(T, FEAT 등)을 받았을 때, 개선된 [PIPELINE ERROR] 메시지
  (actual/expected/복구 XML 예시)를 출력하고 BLOCKED(non-zero exit)되는지
  subprocess 기반 실제 CLI 호출로 검증한다.

CLI Evidence Contract (IMP-20260525-6FAC):
  - 상태 변경 CLI 호출: PIPELINE_STATE_PATH 격리 + final_state assertion 필수
  - stdout-only 검증 금지 (final_state 확인 포함)
  - subprocess 기반 실제 CLI 실행 (내부 함수 직접 임포트 금지)

설계 결정:
  cmd_architect는 check_gate(state, "architect") 통과 후에야
  _parse_protocol_evolution_decision()에 도달한다. 따라서 격리 state를
  architect phase로 직접 준비해야 한다:
    - pipeline.py new로 격리 state 생성 (PIPELINE_STATE_PATH)
    - state JSON을 직접 편집:
        current_phase=architect, phases.harness.status=PASS,
        phase_attestations.enabled=false (build attestation 요구 우회)
  이렇게 하면 architect 명령이 protocol_evolution_decision 파싱 경로에 도달한다.

AC 매핑:
  AC-5  : 잘못된 type(T, FEAT)에서 [PIPELINE ERROR] + actual/expected/복구 XML
  AC-6  : exit code != 0 (BLOCKED) + Python traceback 없음
  AC-9  : 기존 harness 테스트 무손상 (신규 파일만 추가, 기존 파일 미수정)
  AC-10 : 정상 type(IMP)은 type 차단 메시지로 막히지 않음
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PIPELINE_PY = Path(__file__).resolve().parent.parent.parent / "pipeline.py"


def run_cli(
    args: List[str],
    env: Optional[Dict[str, str]] = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess:
    """pipeline.py CLI를 subprocess로 실행."""
    cmd = [sys.executable, str(PIPELINE_PY)] + args
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=merged_env,
    )


def read_state(state_path: Path) -> Dict[str, Any]:
    """격리된 state 파일 읽기."""
    if not state_path.exists():
        return {}
    with open(state_path, encoding="utf-8") as f:
        return json.load(f)


def write_state(state_path: Path, state: Dict[str, Any]) -> None:
    """격리된 state 파일 쓰기."""
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def prepare_architect_state(tmp_path: Path, satisfy_all_gates: bool = False) -> Dict[str, str]:
    """tmp_path에 architect phase에 도달한 격리 state를 준비하고 env를 반환.

    1. pipeline.py new로 최소 state 생성
    2. state JSON을 직접 편집하여 architect gate를 통과 가능하게 함
       (current_phase=architect, harness=PASS, build attestation=PASS)

    satisfy_all_gates=True면 external gates + 전체 phase attestation + oracle_quality를
    PASS로 설정하여 architect가 type 검증 통과 후 COMPLETE까지 도달하게 한다.
    (정상 IMP 케이스에서 type 차단이 아닌 정상 완료를 final_state로 검증하기 위함)
    """
    state_file = tmp_path / "pipeline_state.json"
    env = {
        "PIPELINE_STATE_PATH": str(state_file),
        "PYTHONIOENCODING": "utf-8",
    }
    result = run_cli(
        ["new", "--type", "IMP", "--desc", "architect-evo-type-isolation"],
        env=env,
    )
    assert result.returncode == 0, (
        f"격리 state 초기화 실패(pipeline.py new).\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert state_file.exists(), "pipeline.py new 후 state 파일이 생성되지 않았습니다."

    state = read_state(state_file)
    # architect gate 통과 조건 설정
    state["current_phase"] = "architect"
    state.setdefault("phases", {})
    state["phases"].setdefault("harness", {})
    state["phases"]["harness"]["status"] = "PASS"
    state["phases"].setdefault("architect", {})
    state["phases"]["architect"]["status"] = "PENDING"
    # architect gate는 build phase attestation PASS를 요구한다.
    # _ensure_v210_fields가 phase_attestations.enabled를 강제로 True로 되돌리므로
    # 비활성화 대신 build attestation을 PASS로 직접 설정한다.
    pa = state.get("phase_attestations")
    if not isinstance(pa, dict):
        pa = {"enabled": True, "phases": {}}
        state["phase_attestations"] = pa
    pa.setdefault("phases", {})
    if not isinstance(pa["phases"].get("build"), dict):
        pa["phases"]["build"] = {}
    pa["phases"]["build"]["status"] = "PASS"

    if satisfy_all_gates:
        # 전체 phase attestation PASS (pm/dev/qa/build)
        for ph in ("pm", "dev", "qa", "build"):
            if not isinstance(pa["phases"].get(ph), dict):
                pa["phases"][ph] = {}
            pa["phases"][ph]["status"] = "PASS"
        # external gates PASS (technical/oracle/acceptance/github_ci)
        eg = state.get("external_gates")
        if not isinstance(eg, dict):
            eg = {"enabled": True, "gates": {}}
            state["external_gates"] = eg
        eg.setdefault("gates", {})
        for g in ("technical", "oracle", "acceptance", "github_ci"):
            # external_gates는 top-level 또는 gates 하위 모두를 방어적으로 채움
            eg[g] = {"status": "PASS"}
            eg["gates"][g] = {"status": "PASS"}
        # oracle_quality PASS
        state["oracle_quality"] = {"status": "PASS"}
    write_state(state_file, state)

    # 준비 상태 검증
    prepared = read_state(state_file)
    assert prepared.get("current_phase") == "architect"
    assert prepared.get("phases", {}).get("harness", {}).get("status") == "PASS"
    assert (
        prepared.get("phase_attestations", {}).get("phases", {}).get("build", {}).get("status")
        == "PASS"
    )
    return env


def make_report(tmp_path: Path, recommended_type: str) -> Path:
    """recommended_pipeline_type을 지정한 architect 리포트 XML을 작성하고 경로 반환."""
    report = tmp_path / f"architect_report_{recommended_type}.xml"
    report.write_text(
        "<optimization_report>\n"
        "  <protocol_evolution_decision>\n"
        "    <required>false</required>\n"
        "    <reason>none</reason>\n"
        "    <scope>none</scope>\n"
        f"    <recommended_pipeline_type>{recommended_type}</recommended_pipeline_type>\n"
        "  </protocol_evolution_decision>\n"
        "</optimization_report>\n",
        encoding="utf-8",
    )
    return report


class TestArchitectEvoType:
    """recommended_pipeline_type 검증 메시지 회귀 테스트."""

    @pytest.mark.parametrize("bad_type", ["T", "FEAT"])
    def test_bad_recommended_type_blocked(self, tmp_path: Path, bad_type: str) -> None:
        """AC-5, AC-6: 잘못된 type은 actual/expected/복구 XML 포함 메시지 + BLOCKED."""
        env = prepare_architect_state(tmp_path)
        state_file = Path(env["PIPELINE_STATE_PATH"])
        report = make_report(tmp_path, bad_type)

        result = run_cli(["architect", "--report-file", str(report)], env=env)
        combined = result.stdout + result.stderr

        # exit code != 0 (BLOCKED)
        assert result.returncode != 0, (
            f"잘못된 type({bad_type})인데 exit code가 0입니다.\n"
            f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )

        # [PIPELINE ERROR] 포함
        assert "[PIPELINE ERROR]" in combined, (
            f"[PIPELINE ERROR] prefix가 없습니다.\nstdout: {result.stdout!r}\nstderr: {result.stderr!r}"
        )

        # actual 값 포함 (예: 'actual  : T')
        assert f"actual  : {bad_type}" in combined, (
            f"actual 값('actual  : {bad_type}')이 메시지에 없습니다.\n"
            f"combined: {combined!r}"
        )

        # expected: IMP / IMP 키워드 포함
        assert "expected: IMP" in combined, (
            f"'expected: IMP' 안내가 없습니다.\ncombined: {combined!r}"
        )
        assert "IMP" in combined

        # 복구 예시 XML 포함
        assert "<recommended_pipeline_type>IMP</recommended_pipeline_type>" in combined, (
            f"복구 예시 XML이 메시지에 없습니다.\ncombined: {combined!r}"
        )

        # Python traceback 없음
        assert "Traceback (most recent call last)" not in combined, (
            f"raw Python traceback이 노출되었습니다.\ncombined: {combined!r}"
        )

        # final_state assertion: 잘못된 type이므로 architect phase가 DONE이 되면 안 됨
        state = read_state(state_file)
        architect_status = state.get("phases", {}).get("architect", {}).get("status")
        assert architect_status != "DONE", (
            f"잘못된 type({bad_type})인데 architect phase가 DONE으로 기록되었습니다 — "
            f"type 차단이 우회되었습니다. status={architect_status!r}"
        )

    def test_valid_imp_type_not_blocked_by_type_check(self, tmp_path: Path) -> None:
        """AC-10: 정상 type(IMP)은 recommended_pipeline_type 차단 메시지로 막히지 않는다.

        IMP는 type 검증을 통과하므로, 설령 다른 사유로 실패하더라도
        'recommended_pipeline_type 잘못된 값' 메시지는 나타나지 않아야 한다.
        모든 external gate + attestation을 PASS로 준비한 state에서는 architect
        phase가 type 차단 없이 DONE으로 기록되고 파이프라인이 COMPLETE된다.
        """
        env = prepare_architect_state(tmp_path, satisfy_all_gates=True)
        state_file = Path(env["PIPELINE_STATE_PATH"])
        report = make_report(tmp_path, "IMP")

        result = run_cli(["architect", "--report-file", str(report)], env=env)
        combined = result.stdout + result.stderr

        # type 차단 메시지가 나타나면 안 됨
        assert "잘못된 값이 감지됐습니다" not in combined, (
            f"정상 type(IMP)인데 type 차단 메시지가 출력되었습니다.\ncombined: {combined!r}"
        )
        assert "actual  : IMP" not in combined, (
            f"정상 type(IMP)인데 actual/expected 차단 메시지가 출력되었습니다.\ncombined: {combined!r}"
        )

        # Python traceback 없음
        assert "Traceback (most recent call last)" not in combined, (
            f"raw Python traceback이 노출되었습니다.\ncombined: {combined!r}"
        )

        # final_state assertion: IMP는 type 차단 없이 architect phase가 DONE 기록
        state = read_state(state_file)
        architect_status = state.get("phases", {}).get("architect", {}).get("status")
        assert architect_status == "DONE", (
            f"정상 type(IMP)인데 architect phase가 DONE이 아닙니다.\n"
            f"status={architect_status!r}\nexit={result.returncode}\ncombined: {combined!r}"
        )
        decision = state.get("protocol_evolution_decision", {})
        assert decision.get("recommended_pipeline_type") == "IMP", (
            f"architect DONE 후 protocol_evolution_decision이 IMP로 기록되지 않았습니다: {decision!r}"
        )

        # cleanup: COMPLETE 도달 시 pipeline.py가 공유 pipeline_history/에
        # <pid>_COMPLETE.json archive를 남기므로, 테스트가 생성한 archive를 제거한다.
        pid = state.get("pipeline_id")
        if pid:
            archive = PIPELINE_PY.parent / "pipeline_history" / f"{pid}_COMPLETE.json"
            try:
                if archive.exists():
                    archive.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
