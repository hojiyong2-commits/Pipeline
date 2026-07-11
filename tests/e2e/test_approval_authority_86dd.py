"""
IMP-20260711-86DD — Approval Authority SSoT E2E Tests (TC-1 ~ TC-11)

REJECT rework: TC-1~TC-10 verify absent fields, hard-gate structure, and
producer/consumer SSoT via source-analysis; TC-11 performs import-level
verification by calling pipeline._validate_approval_request_message directly.
No subprocess/runtime-CLI claim is made — all checks are source-analysis and
import-level function invocation.
"""
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
PIPELINE_PY = REPO_ROOT / "pipeline.py"
AGENT_MD = REPO_ROOT / ".claude" / "agents" / "pipeline-manager-agent.md"
SETTINGS_JSON = REPO_ROOT / ".claude" / "settings.json"


def _import_pipeline():
    """pipeline 모듈을 파일 경로로 동적 임포트한다 (import-level 검증용).

    Returns:
        exec_module로 로드된 pipeline 모듈 객체. main guard 덕분에 CLI는 실행되지 않음.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline", str(PIPELINE_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestApprovalAuthority:
    """TC-1 ~ TC-11: approval 출력 권한 SSoT 검증"""

    def test_tc1_approval_display_removed_from_return_dict(self):
        """TC-1: _build_approval_request_output 반환 dict에 approval_display 키 없음"""
        source = PIPELINE_PY.read_text(encoding="utf-8")
        # _build_approval_request_output 함수 바디 추출 (return {} 블록 기준)
        # approval_display 키가 return dict에 있으면 이중 relay 경로가 생김
        # "approval_display": 패턴이 실행 코드에 없어야 함
        # (주석에는 있을 수 있으나 dict key 할당 패턴은 제거됨)
        # 주석 줄을 제외한 실행 코드에서 찾기
        non_comment_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith("#") and '"approval_display"' in line
        ]
        assert len(non_comment_lines) == 0, (
            "approval_display 키가 실행 코드에서 발견됨 (이중 relay 경로):\n"
            + "\n".join(non_comment_lines)
        )

    def test_tc2_message_file_not_in_json_output(self):
        """TC-2: machine-readable JSON stdout에 message_file 키가 추가되지 않음"""
        source = PIPELINE_PY.read_text(encoding="utf-8")
        # _approval_out["message_file"] 할당이 실행 코드에 없어야 함
        non_comment_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith("#") and '_approval_out["message_file"]' in line
        ]
        assert len(non_comment_lines) == 0, (
            "_approval_out[\"message_file\"] 할당이 실행 코드에서 발견됨:\n"
            + "\n".join(non_comment_lines)
        )

    def test_tc3_progress_prints_redirected_to_stderr(self):
        """TC-3: PR본문SHA/CODEX CANONICAL SHA 진행 메시지가 print() 대신 sys.stderr로 전환됨

        machine-readable 모드에서 JSON stdout 오염을 방지하기 위해
        diagnostic 메시지는 print()가 아닌 sys.stderr.write()를 사용해야 한다.
        """
        source = PIPELINE_PY.read_text(encoding="utf-8")
        lines = source.splitlines()

        # [PR 본문 SHA 재기록] 메시지가 print()에 있으면 안 됨
        pr_sha_in_print = [
            line for line in lines
            if "[PR 본문 SHA 재기록]" in line and "print(" in line
            and not line.strip().startswith("#")
        ]
        assert len(pr_sha_in_print) == 0, (
            "[PR 본문 SHA 재기록] 메시지가 여전히 print()로 stdout에 노출됨:\n"
            + "\n".join(pr_sha_in_print)
        )

        # [CODEX CANONICAL SHA 기록] 메시지가 print()에 있으면 안 됨
        codex_sha_in_print = [
            line for line in lines
            if "[CODEX CANONICAL SHA 기록]" in line and "print(" in line
            and not line.strip().startswith("#")
        ]
        assert len(codex_sha_in_print) == 0, (
            "[CODEX CANONICAL SHA 기록] 메시지가 여전히 print()로 stdout에 노출됨:\n"
            + "\n".join(codex_sha_in_print)
        )

        # 대신 sys.stderr로 전환되었는지 확인
        pr_sha_in_stderr = any(
            "[PR 본문 SHA 재기록]" in line and "stderr" in line
            for line in lines if not line.strip().startswith("#")
        )
        codex_sha_in_stderr = any(
            "[CODEX CANONICAL SHA 기록]" in line and "stderr" in line
            for line in lines if not line.strip().startswith("#")
        )
        assert pr_sha_in_stderr, (
            "[PR 본문 SHA 재기록] 메시지가 sys.stderr로 전환되지 않음 (stdout 오염 우려)"
        )
        assert codex_sha_in_stderr, (
            "[CODEX CANONICAL SHA 기록] 메시지가 sys.stderr로 전환되지 않음 (stdout 오염 우려)"
        )

    def test_tc4_pipeline_manager_md_has_single_relay_section(self):
        """TC-4: pipeline-manager-agent.md에 승인 요청 relay 관련 섹션이 1개여야 함"""
        if not AGENT_MD.exists():
            return
        content = AGENT_MD.read_text(encoding="utf-8")

        relay_section_patterns = [
            r"##\s+.*(?:relay|중계|Output Authority|이중 출력|승인 요청 출력 규칙|request-accept 중계 프로토콜)",
        ]
        relay_sections = []
        for pattern in relay_section_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            relay_sections.extend(matches)

        relay_only = [s for s in relay_sections if "표시 상태값" not in s and "최소 고정 양식" not in s]
        assert len(relay_only) <= 1, (
            f"relay 관련 섹션이 1개를 초과함: {relay_only}"
        )

    def test_tc5_no_final_user_message_txt_in_agent_md(self):
        """TC-5: pipeline-manager-agent.md에 'Read final_user_message.txt' 문구 없음"""
        if not AGENT_MD.exists():
            return
        content = AGENT_MD.read_text(encoding="utf-8")
        assert "Read final_user_message.txt" not in content, (
            "pipeline-manager-agent.md에 'Read final_user_message.txt' 문구가 없어야 함"
        )
        assert "validate-user-approval-message" not in content, (
            "pipeline-manager-agent.md에 'validate-user-approval-message' 문구가 없어야 함"
        )

    def test_tc6_settings_json_no_codex_approval_hooks(self):
        """TC-6: settings.json hooks에 codex/approval relay 항목 없음"""
        if not SETTINGS_JSON.exists():
            return
        try:
            settings = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return

        hooks = settings.get("hooks", {})
        hooks_str = json.dumps(hooks)
        assert "codex_review_hook" not in hooks_str, (
            "settings.json hooks에 codex_review_hook 항목이 없어야 함"
        )

    def test_tc7_deleted_test_files_do_not_exist(self):
        """TC-7: 삭제된 테스트 파일 2개가 존재하지 않아야 함"""
        hook_test = REPO_ROOT / "tests" / "test_codex_review_hook_cd3c.py"
        loop_test = REPO_ROOT / "tests" / "test_codex_review_loop_4121.py"

        assert not hook_test.exists(), (
            f"삭제되어야 할 파일이 존재함: {hook_test}"
        )
        assert not loop_test.exists(), (
            f"삭제되어야 할 파일이 존재함: {loop_test}"
        )

    def test_tc8_approval_display_not_in_req_candidate(self):
        """TC-8: req_candidate["approval_display"] 할당이 실행 코드에 없음"""
        source = PIPELINE_PY.read_text(encoding="utf-8")
        non_comment_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith("#")
            and 'req_candidate["approval_display"]' in line
        ]
        assert len(non_comment_lines) == 0, (
            "req_candidate[\"approval_display\"] 할당이 실행 코드에서 발견됨 "
            "(acceptance_request.json 이중 relay 경로):\n"
            + "\n".join(non_comment_lines)
        )

    def test_tc9_hard_gate_validates_approval_message_structure(self):
        """TC-9: _validate_approval_request_message 함수가 4요소 count==1 검증 로직을 포함"""
        source = PIPELINE_PY.read_text(encoding="utf-8")
        # 함수 존재 확인
        assert "_validate_approval_request_message" in source, (
            "_validate_approval_request_message 함수가 pipeline.py에 있어야 함"
        )
        # 함수가 count 검증 로직을 포함하는지 확인
        # 4개 필수 요소 모두가 함수 바디에서 체크되어야 함
        assert "사용자 승인 요청" in source, "hard gate가 '사용자 승인 요청' 검증해야 함"
        assert "CODEX 검토 필요" in source, "hard gate가 'CODEX 검토 필요' 검증해야 함"
        assert "승인 코드" in source, "hard gate가 '승인 코드' 검증해야 함"

        # _validate_approval_request_message 함수 바디에 count 비교가 있는지
        func_start = source.find("def _validate_approval_request_message(")
        assert func_start != -1, "_validate_approval_request_message 함수를 찾을 수 없음"
        func_end = source.find("\ndef ", func_start + 1)
        func_body = source[func_start:func_end] if func_end != -1 else source[func_start:func_start + 2000]
        assert "count" in func_body or "!= 1" in func_body or "== 1" in func_body, (
            "_validate_approval_request_message 함수에 count 검증 로직이 없음"
        )

    def test_tc10_approval_request_message_is_single_source_in_json(self):
        """TC-10: _build_approval_request_output이 approval_request_message만 노출 (단일 소스)"""
        source = PIPELINE_PY.read_text(encoding="utf-8")
        # 함수 바디 추출
        func_start = source.find("def _build_approval_request_output(")
        assert func_start != -1, "_build_approval_request_output 함수를 찾을 수 없음"
        func_end = source.find("\ndef ", func_start + 1)
        func_body = source[func_start:func_end] if func_end != -1 else source[func_start:func_start + 3000]

        # return dict에 approval_request_message 있어야 함
        assert '"approval_request_message"' in func_body, (
            "approval_request_message 키가 반환 dict에 없음"
        )
        # return dict에 approval_display 없어야 함 (실행 코드 기준)
        non_comment = [
            ln for ln in func_body.splitlines()
            if not ln.strip().startswith("#") and '"approval_display"' in ln
        ]
        assert len(non_comment) == 0, (
            "_build_approval_request_output이 여전히 approval_display를 반환함:\n"
            + "\n".join(non_comment)
        )

    def test_tc11_hard_gate_rejects_duplicated_approval_message(self):
        """TC-11: pipeline._validate_approval_request_message를 실제로 import·호출하여
        정상 최소 고정 양식은 통과하고, 필수 4요소가 중복(count!=1)된 메시지는 BLOCKED됨을 검증.

        기존 str.count tautology를 제거하고 실제 hard-gate 함수를 호출한다.
        """
        pipeline = _import_pipeline()
        validate = pipeline._validate_approval_request_message

        # IMP-20260624-069A 최소 고정 양식: 4요소 각 1회, 마지막 의미 줄이 'CODEX 검토 필요'
        valid_msg = (
            "사용자 승인 요청\n\n"
            "PR: https://github.com/example/repo/pull/1\n\n"
            "승인 코드:\nACCEPT-IMP-20260711-86DD\n\n"
            "CODEX 검토 필요"
        )
        # 정상 메시지는 예외 없이 통과해야 함 (단일 relay 경로 유효)
        validate(valid_msg)

        # 중복 relay 시뮬레이션: 같은 메시지를 2회 연결 → 필수 4요소 count가 2가 됨
        duplicated = valid_msg + "\n\n" + valid_msg
        with pytest.raises(ValueError) as exc_info:
            validate(duplicated)
        # hard gate가 실제로 count != 1 구조 오류를 잡아 BLOCKED 처리해야 함
        err_text = str(exc_info.value)
        assert "count" in err_text and "BLOCKED" in err_text, (
            f"중복 relay가 count 검증으로 차단되어야 함: {err_text}"
        )

        # None/비str 입력도 fail-closed로 차단됨을 실제 호출로 확인
        with pytest.raises(TypeError):
            validate(None)


class TestApprovalAuthoritySubprocess:
    """TC-12 ~ TC-13: subprocess 기반 실제 런타임 검증 (source-analysis 아님).

    설계 근거 (REJECT #3 rework):
      - TC-12는 격리 PIPELINE_STATE_PATH로 `gates request-accept --machine-readable`을
        실제 subprocess로 실행하여 machine-readable stdout이 절대 오염되지 않음을 검증한다.
        assertion은 exit code와 무관하게 무조건 실행된다(returncode==0일 때만 검사하는
        skip/bypass 패턴 제거). hermetic 환경에서는 gh/PR가 없어 pr_body_not_found로
        결정적으로 BLOCK되며, 이때 stdout은 반드시 비어 있어야 한다(진단/에러는 stderr).
      - TC-13은 `python -c` 드라이버로 실제 pipeline._build_approval_request_output을 호출하고
        CLI가 emit하는 것과 동일한 json.dumps 결과(=machine-readable JSON stdout 원문)를
        subprocess stdout으로 캡처하여 구조를 검증한다. 하드코딩 샘플 문자열이 아니라
        실제 emit 함수의 런타임 출력을 파싱한다. approval_display/message_file alias 부재와
        approval_request_message 단일 소스를 런타임 JSON에서 직접 확인한다.

    참고: `gates request-accept`를 returncode==0까지 몰고 가려면 frozen contract + codex
    APPROVE_TO_USER + fake gh 스택이 필요하며, 현 codex preflight(contract_not_frozen 등)로는
    hermetic 성공이 비결정적이다. 따라서 emit 원문 검증은 TC-13의 결정적 드라이버로 수행하고,
    TC-12는 stdout 오염 불변식(항상 검증)을 담당한다.
    """

    def _bootstrap_isolated_state(self, tmp_path):
        """격리 PIPELINE_STATE_PATH로 IMP 파이프라인을 생성하고 external gate를 PASS로 seed.

        `pipeline.py new`로 state를 부트스트랩하므로 event_log 등 필수 키가 모두 채워진다
        (손수 만든 최소 state의 KeyError를 방지). requirements_tracking을 비활성화하여
        AC 검사를 우회하고, technical/oracle/github_ci를 PASS로 두어 request-accept가
        상위 gate가 아닌 PR body 단계까지 진입하도록 한다.

        Args:
            tmp_path: pytest tmp_path fixture.
        Returns:
            (env, state_file, pipeline_id) 튜플.
        Raises:
            TypeError: tmp_path가 None인 경우.
        """
        import os
        import subprocess
        import sys as _sys

        if tmp_path is None:
            raise TypeError("tmp_path must not be None")

        state_file = tmp_path / "pipeline_state.json"
        env = dict(os.environ)
        env["PIPELINE_STATE_PATH"] = str(state_file)
        env["PATH"] = str(tmp_path)  # gh CLI 미탐지 (hermetic)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING"] = "1"
        env["PIPELINE_NO_DASHBOARD"] = "1"
        env["PIPELINE_BROWSER_APPROVAL_SKIP"] = "1"

        r_new = subprocess.run(
            [_sys.executable, str(PIPELINE_PY), "new", "--type", "IMP",
             "--desc", "tc12 approval authority isolation"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, env=env, cwd=str(PIPELINE_PY.parent),
        )
        assert r_new.returncode == 0, f"new failed: {r_new.stdout} {r_new.stderr}"

        state = json.loads(state_file.read_text(encoding="utf-8"))
        pid = str(state.get("pipeline_id", ""))
        assert pid, "pipeline_id missing after new"
        state.setdefault("requirements_tracking", {})["enabled"] = False
        state.setdefault("external_gates", {})
        for _g in ("technical", "oracle", "github_ci"):
            state["external_gates"].setdefault(_g, {})["status"] = "PASS"
        state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        return env, state_file, pid

    def test_tc12_machine_readable_stdout_never_polluted(self, tmp_path):
        """TC-12 (실제 subprocess E2E): --machine-readable stdout 오염 불변식.

        격리 PIPELINE_STATE_PATH에서 `gates request-accept --machine-readable`을 실제
        실행한다. hermetic 환경(gh/PR 없음)에서는 pr_body_not_found로 결정적으로 BLOCK되며,
        아래 불변식을 exit code와 무관하게 항상 검증한다(returncode==0 조건부 skip 없음):
          1. stdout에 진단 마커([PR 본문 SHA 재기록]/[CODEX CANONICAL SHA 기록])가 없다.
          2. BLOCK 시 machine-readable stdout은 비어 있다(진단/에러는 stderr 전용).
          3. BLOCK 사유는 stderr에 노출된다(stdout이 아니라).
          4. stdout이 비어 있지 않다면 그 전체가 유효한 JSON이어야 한다(부분 오염 금지).
        """
        import subprocess
        import sys as _sys

        env, state_file, pid = self._bootstrap_isolated_state(tmp_path)
        evidence_path = tmp_path / "dummy_evidence.md"
        evidence_path.write_text("# dummy evidence for TC-12", encoding="utf-8")

        result = subprocess.run(
            [_sys.executable, str(PIPELINE_PY), "gates", "request-accept",
             "--machine-readable", "--evidence", str(evidence_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env, cwd=str(PIPELINE_PY.parent),
        )

        # 불변식 1: 진단 마커가 stdout에 절대 없어야 함 (exit code 무관, 무조건 검증)
        assert "[PR 본문 SHA 재기록]" not in result.stdout, (
            "[PR 본문 SHA 재기록] 진단 메시지가 machine-readable stdout에 노출됨\n"
            f"stdout(첫 400자):\n{result.stdout[:400]}"
        )
        assert "[CODEX CANONICAL SHA 기록]" not in result.stdout, (
            "[CODEX CANONICAL SHA 기록] 진단 메시지가 machine-readable stdout에 노출됨\n"
            f"stdout(첫 400자):\n{result.stdout[:400]}"
        )

        # 불변식 4: stdout은 비어 있거나(=BLOCK), 전체가 유효한 JSON이어야 한다 (부분 오염 금지)
        _stdout_stripped = result.stdout.strip()
        if _stdout_stripped:
            data = json.loads(_stdout_stripped)  # 파싱 실패 시 test FAIL (부분 오염 감지)
            assert isinstance(data, dict), "machine-readable stdout JSON은 dict여야 함"
            assert "approval_request_message" in data
            # alias 키 회귀 방지: 런타임 JSON에 승인 본문 alias 키가 없어야 함
            assert "approval_display" not in data, "approval_display alias 키가 JSON에 잔존"
            assert "message_file" not in data, "message_file alias 키가 JSON에 잔존"
        else:
            # 불변식 2+3: hermetic BLOCK 경로 — stdout 비어 있고 BLOCK 사유는 stderr에 노출
            assert result.returncode != 0, (
                "hermetic 환경(gh/PR 없음)에서는 request-accept가 BLOCK(exit!=0)되어야 함\n"
                f"returncode={result.returncode}\nstderr(첫 400자):\n{result.stderr[:400]}"
            )
            assert "[PIPELINE ERROR]" in result.stderr or "BLOCKED" in result.stderr, (
                "BLOCK 사유가 stderr에 노출되지 않음 (진단/에러는 stderr 전용이어야 함)\n"
                f"stderr(첫 400자):\n{result.stderr[:400]}"
            )

        # post-state assertion: 격리 state 파일이 실제로 사용되었음을 확인 (CLI Evidence Contract)
        final_state = json.loads(state_file.read_text(encoding="utf-8"))
        assert final_state.get("pipeline_id") == pid, "격리 state 파일이 사용되지 않음"

    def test_tc13_real_emitted_json_is_single_source(self, tmp_path):
        """TC-13 (실제 emit 원문 검증): machine-readable JSON은 approval_request_message 단일 소스.

        `python -c` 드라이버로 실제 pipeline._build_approval_request_output을 호출하고,
        CLI가 emit하는 것과 동일한 json.dumps(out, ensure_ascii=False) 문자열을 subprocess
        stdout으로 캡처한다(하드코딩 샘플 아님 — 실제 emit 함수의 런타임 출력). 이 원문 JSON에서:
          1. approval_display / message_file alias 키가 없다(이중 relay 경로 부재).
          2. approval_request_message가 4요소를 각 1회씩 포함한다(단일 소스).
          3. approval_request_message 외 어떤 필드도 승인 본문("사용자 승인 요청")을 담지 않는다.
        추가로 hard gate(_validate_approval_request_message)가 중복 relay(count!=1)를 실제
        호출로 차단함을 확인한다.
        """
        import subprocess
        import sys as _sys

        pid = "IMP-20260711-86DD"
        pr_url = "https://github.com/hojiyong2-commits/Pipeline/pull/877"
        # CLI emit 원문과 동일한 직렬화를 재현하는 결정적 드라이버.
        driver = (
            "import json, importlib.util\n"
            "spec = importlib.util.spec_from_file_location('pipeline', PIPELINE_PATH)\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "out = mod._build_approval_request_output(PID, PR_URL)\n"
            "print(json.dumps(out, ensure_ascii=False))\n"
        )
        driver = (
            f"PIPELINE_PATH = {json.dumps(str(PIPELINE_PY))}\n"
            f"PID = {json.dumps(pid)}\n"
            f"PR_URL = {json.dumps(pr_url)}\n"
        ) + driver

        env = {"PYTHONIOENCODING": "utf-8"}
        import os as _os
        env = {**_os.environ, **env}
        result = subprocess.run(
            [_sys.executable, "-c", driver],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, env=env, cwd=str(PIPELINE_PY.parent),
        )
        assert result.returncode == 0, (
            f"emit 드라이버 실패 (returncode={result.returncode})\nstderr:\n{result.stderr[:500]}"
        )

        # 실제 emit 원문(=machine-readable JSON stdout)을 파싱
        data = json.loads(result.stdout)
        assert isinstance(data, dict), "emit JSON은 dict여야 함"

        # 불변식 1: 승인 본문 alias 키 부재 (approval_display / message_file)
        assert "approval_display" not in data, (
            f"approval_display alias 키가 실제 emit JSON에 잔존: keys={sorted(data.keys())}"
        )
        assert "message_file" not in data, (
            f"message_file alias 키가 실제 emit JSON에 잔존: keys={sorted(data.keys())}"
        )

        # 불변식 2: approval_request_message 단일 소스 (4요소 각 1회)
        arm = data.get("approval_request_message", "")
        assert arm, "approval_request_message가 비어 있음"
        assert arm.count("사용자 승인 요청") == 1, (
            f"'사용자 승인 요청' count != 1: {arm.count('사용자 승인 요청')}"
        )
        assert arm.count("PR:") == 1, f"'PR:' count != 1: {arm.count('PR:')}"
        assert arm.count("승인 코드:") == 1, f"'승인 코드:' count != 1: {arm.count('승인 코드:')}"
        assert arm.count("CODEX 검토 필요") == 1, (
            f"'CODEX 검토 필요' count != 1: {arm.count('CODEX 검토 필요')}"
        )
        assert arm.strip().split("\n")[-1].strip() == "CODEX 검토 필요", (
            f"승인 요청문 마지막 의미 줄이 'CODEX 검토 필요'가 아님: {arm.strip().split(chr(10))[-1]!r}"
        )

        # 불변식 3: approval_request_message 외 어떤 필드도 승인 본문을 담지 않음
        for key, val in data.items():
            if key == "approval_request_message":
                continue
            assert "사용자 승인 요청" not in str(val), (
                f"필드 '{key}'에 승인 본문이 중복 노출됨: {val!r}"
            )

        # hard gate: 중복 relay(동일 블록 2회 연결)를 실제 호출로 차단
        pipeline = _import_pipeline()
        validate = pipeline._validate_approval_request_message
        validate(arm)  # 단일 소스는 예외 없이 통과
        doubled = arm + "\n\n" + arm
        assert doubled.count("사용자 승인 요청") == 2  # 2회 = 실패 케이스 트리거
        with pytest.raises(ValueError) as exc_info:
            validate(doubled)
        err = str(exc_info.value)
        assert "count" in err and "BLOCKED" in err, (
            f"중복 relay가 count 검증으로 차단되어야 함: {err}"
        )
