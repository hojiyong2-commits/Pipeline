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

PIPELINE_PY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline.py"
)

VALID_MSG = """사용자 승인 요청

PR: https://github.com/hojiyong2-commits/Pipeline/pull/835

승인 코드:
ACCEPT-IMP-20260703-B985

CODEX 검토 필요"""


def run_validator(content: str) -> tuple:
    """파일에 content를 쓰고 validate-user-approval-message CLI를 실행한다.

    MT-31: BOM 없이 LF 강제 저장 (binary 모드).
    """
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".txt", delete=False
    ) as f:
        # BOM 없이 LF 강제: Windows tempfile의 text 모드 CRLF 변환 방지
        f.write(content.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))
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


def _run_validator_path(file_path: str):
    """파일 경로를 받아 validator 실행 (MT-31 헬퍼)."""
    result = subprocess.run(
        [
            sys.executable,
            PIPELINE_PY,
            "gates",
            "validate-user-approval-message",
            "--file",
            file_path,
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


def test_bom_causes_fail(tmp_path):
    """BOM 있으면 FAIL"""
    bom_msg = b"\xef\xbb\xbf" + VALID_MSG.encode("utf-8")
    p = tmp_path / "bom_msg.txt"
    p.write_bytes(bom_msg)
    code, data = _run_validator_path(str(p))
    assert code == 1
    assert data["status"] == "FAIL"
    assert any("BOM" in e for e in data["errors"])


def test_ui_task_notification_phrase_forbidden(tmp_path):
    """'위 태스크 알림' 포함 → FAIL"""
    msg = VALID_MSG + "\n\n위 태스크 알림에 표시된 내용을 확인하세요"
    p = tmp_path / "msg.txt"
    p.write_text(msg, encoding="utf-8")
    code, data = _run_validator_path(str(p))
    assert code == 1
    assert data["status"] == "FAIL"
    assert any("위 태스크 알림" in e for e in data["errors"])


def test_accept_reject_input_phrase_forbidden(tmp_path):
    """'ACCEPT 또는 REJECT를 입력' 포함 → FAIL"""
    msg = VALID_MSG + "\n\nACCEPT 또는 REJECT를 입력해 주세요"
    p = tmp_path / "msg.txt"
    p.write_text(msg, encoding="utf-8")
    code, data = _run_validator_path(str(p))
    assert code == 1
    assert data["status"] == "FAIL"


def test_scratch_not_root():
    """root에 final_user_message.txt 없어야 함"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root_file = os.path.join(root, "final_user_message.txt")
    assert not os.path.exists(root_file), (
        f"root에 final_user_message.txt가 있으면 안 됩니다: {root_file}"
    )

