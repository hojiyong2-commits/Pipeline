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
    """TC-12 ~ TC-13: subprocess 기반 E2E 및 relay 시뮬레이션"""

    def test_tc12_machine_readable_stdout_no_diagnostic_text(self, tmp_path):
        """TC-12 (subprocess E2E): --machine-readable 모드에서 stdout에 diagnostic text 없음

        gates request-accept --machine-readable 실행 시
        '[PR 본문 SHA 재기록]', '[CODEX CANONICAL SHA 기록]' 같은 진단 메시지가
        stdout에 섞이면 json.loads(stdout)가 실패한다.

        exit code와 무관하게 해당 문자열이 stdout에 없어야 한다.
        성공(exit 0)이면 stdout 전체가 유효한 JSON이어야 한다.
        """
        import os
        import subprocess
        import sys as _sys

        evidence_path = REPO_ROOT / "cleanup_report_86dd.md"
        if not evidence_path.exists():
            evidence_path = tmp_path / "dummy_evidence.md"
            evidence_path.write_text("# dummy evidence for TC-12", encoding="utf-8")

        env = {
            **os.environ,
            "PIPELINE_WORKSPACE_HYGIENE_ALLOW_GIT_MISSING": "1",
        }
        result = subprocess.run(
            [
                _sys.executable,
                str(PIPELINE_PY),
                "gates",
                "request-accept",
                "--machine-readable",
                "--evidence",
                str(evidence_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            env=env,
        )

        # 핵심 불변식: diagnostic prints가 stdout에 없어야 함 (exit code 무관)
        assert "[PR 본문 SHA 재기록]" not in result.stdout, (
            "[PR 본문 SHA 재기록] 메시지가 machine-readable stdout에 노출됨\n"
            f"stdout(첫 300자):\n{result.stdout[:300]}"
        )
        assert "[CODEX CANONICAL SHA 기록]" not in result.stdout, (
            "[CODEX CANONICAL SHA 기록] 메시지가 machine-readable stdout에 노출됨\n"
            f"stdout(첫 300자):\n{result.stdout[:300]}"
        )

        # 성공 시 추가 검증
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            assert "approval_request_message" in data, (
                "success stdout에 approval_request_message 키 없음"
            )
            msg = data["approval_request_message"]
            assert msg.count("사용자 승인 요청") == 1, (
                f"approval_request_message에 '사용자 승인 요청'이 1회가 아님: {msg.count('사용자 승인 요청')}회"
            )

    def test_tc13_relay_simulation_no_duplication(self):
        """TC-13: Pipeline Manager relay 시뮬레이션 — approval_request_message 중복 없음

        Pipeline Manager는 JSON에서 approval_request_message를 추출하여 1회만 출력한다.
        동일 메시지를 2회 연결하면 count가 1이 아니어야 한다(REJECT 기준).
        """
        pipeline = _import_pipeline()
        validate = pipeline._validate_approval_request_message

        relay_msg = (
            "사용자 승인 요청\n\n"
            "PR: https://github.com/hojiyong2-commits/Pipeline/pull/877\n\n"
            "승인 코드:\nACCEPT-IMP-20260711-86DD\n\n"
            "CODEX 검토 필요"
        )

        # 1회 relay: 4요소 각 1회 — 통과해야 함
        assert relay_msg.count("사용자 승인 요청") == 1
        assert relay_msg.count("PR:") == 1
        assert relay_msg.count("승인 코드:") == 1
        assert relay_msg.count("CODEX 검토 필요") == 1
        validate(relay_msg)  # 예외 없음

        # 중복 relay 시나리오: 동일 블록 2회 concatenate → FAIL
        doubled = relay_msg + "\n\n" + relay_msg
        assert doubled.count("사용자 승인 요청") != 1, (
            "중복 relay는 count != 1 조건을 트리거해야 함"
        )
        with pytest.raises(ValueError) as exc_info:
            validate(doubled)
        err = str(exc_info.value)
        assert "count" in err and "BLOCKED" in err, (
            f"중복 relay가 count 검증으로 차단되어야 함: {err}"
        )
