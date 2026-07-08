"""
IMP-20260703-B985 MT-30: final_user_message validator 회귀 테스트
gates validate-user-approval-message --file 검증 게이트

이중 출력 구조 차단: Pipeline Manager task result에 approval 블록 포함 금지를 강제하는
하드 게이트가 올바르게 동작하는지 검증한다.
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

PIPELINE_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline.py"
)

VALID_MSG = """사용자 승인 요청

PR: https://github.com/hojiyong2-commits/Pipeline/pull/835

승인 코드:
ACCEPT-IMP-20260703-B985

CODEX 검토 필요"""


def run_validator(content: str) -> tuple:
    """파일에 content를 쓰고 validate-user-approval-message CLI를 실행한다."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", encoding="utf-8", delete=False
    ) as f:
        f.write(content)
        fname = f.name
    try:
        result = subprocess.run(
            [
                sys.executable,
                PIPELINE_PY,
                "gates",
                "validate-user-approval-message",
                "--file",
                fname,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        try:
            data = json.loads(result.stdout.strip())
        except Exception:
            data = {"status": "PARSE_ERROR", "raw": result.stdout}
        return result.returncode, data
    finally:
        os.unlink(fname)


def test_valid_message_passes():
    """고정 양식 정상 메시지 → PASS (exit 0)"""
    code, data = run_validator(VALID_MSG)
    assert code == 0, f"Expected exit 0, got {code}: {data}"
    assert data["status"] == "PASS"


def test_prefix_causes_fail():
    """PM이 prefix(금지 문구) 붙이면 → FAIL"""
    msg = "gates request-accept가 성공했습니다\n\n승인 요청 메시지:\n\n" + VALID_MSG
    code, data = run_validator(msg)
    assert code == 1, f"Expected exit 1, got {code}: {data}"
    assert data["status"] == "FAIL"
    assert any("금지 문구" in e for e in data["errors"])


def test_double_approval_block_fails():
    """approval 블록 2회 포함 → FAIL (count=2 오류)"""
    msg = VALID_MSG + "\n\n" + VALID_MSG
    code, data = run_validator(msg)
    assert code == 1, f"Expected exit 1, got {code}: {data}"
    assert data["status"] == "FAIL"
    assert any("사용자 승인 요청" in e and "count=2" in e for e in data["errors"])


def test_approval_msg_prefix_forbidden():
    """'승인 요청 메시지:' prefix 포함 → FAIL"""
    msg = "승인 요청 메시지:\n\n" + VALID_MSG
    code, data = run_validator(msg)
    assert code == 1, f"Expected exit 1, got {code}: {data}"
    assert data["status"] == "FAIL"


def test_json_stdout_extract_phrase_forbidden():
    """'JSON stdout에서 approval_request_message를 추출' 포함 → FAIL"""
    msg = "JSON stdout에서 approval_request_message를 추출하여 전달합니다.\n\n" + VALID_MSG
    code, data = run_validator(msg)
    assert code == 1, f"Expected exit 1, got {code}: {data}"
    assert data["status"] == "FAIL"


def test_trailing_extra_line_fails():
    """VALID_MSG 뒤에 추가 줄이 있으면 → FAIL (마지막 줄이 CODEX 검토 필요가 아님)"""
    msg = VALID_MSG + "\n추가된 줄"
    code, data = run_validator(msg)
    assert code == 1, f"Expected exit 1, got {code}: {data}"
    assert data["status"] == "FAIL"
    assert any("마지막 의미있는 줄" in e for e in data["errors"])


def test_codex_required_count_1():
    """CODEX 검토 필요 count == 1이면 PASS"""
    code, data = run_validator(VALID_MSG)
    assert code == 0
    assert data["status"] == "PASS"


def test_codex_required_last_line():
    """CODEX 검토 필요가 마지막 의미있는 줄이면 PASS"""
    code, data = run_validator(VALID_MSG)
    assert code == 0
    assert data["status"] == "PASS"


def test_missing_pr_line_fails():
    """PR: 줄 없으면 → FAIL (count=0)"""
    msg = VALID_MSG.replace(
        "PR: https://github.com/hojiyong2-commits/Pipeline/pull/835\n", ""
    )
    code, data = run_validator(msg)
    assert code == 1, f"Expected exit 1, got {code}: {data}"
    assert data["status"] == "FAIL"
    assert any("PR:" in e and "count=0" in e for e in data["errors"])


def test_pipeline_manager_forbidden_suffix_fails():
    """'위 PR에 승인 코드를 입력해 주세요' suffix → FAIL"""
    msg = VALID_MSG + "\n\n위 PR에 승인 코드를 입력해 주세요"
    code, data = run_validator(msg)
    assert code == 1, f"Expected exit 1, got {code}: {data}"
    assert data["status"] == "FAIL"
    assert any("금지 문구" in e for e in data["errors"])
