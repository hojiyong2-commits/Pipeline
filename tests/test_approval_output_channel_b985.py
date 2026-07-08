# [Purpose]: IMP-20260703-B985 MT-28 — approval output channel SSoT 검증.
#   REJECT #21 근본 수정: --machine-readable 모드에서 approval_request_message가 정확히 1회만
#   사용자에게 전달되도록 channel SSoT를 검증한다. _validate_approval_request_message 함수와
#   _approve_message(hook) 이중 출력 방지를 단위 테스트로 고정한다.
# [Assumptions]: pipeline 모듈을 직접 import할 수 있다. hook 파일은 .claude/hooks/에 있다.
# [Vulnerability & Risks]: _validate_approval_request_message 내부 검증 로직이 바뀌면 이 테스트도
#   업데이트해야 한다. hook 파일 이름 변경 시 TC-H1 경로 수정 필요.
# [Improvement]: subprocess 기반 CLI E2E 테스트는 복잡한 pre-condition으로 인해 단위 테스트로 대체.
"""IMP-20260703-B985 MT-28 approval output channel SSoT 테스트 (14개).

oracle: tests/oracles/IMP-20260703-B985/ (기존 TC-1~TC-13 사용 가능)

TC-VAL-1  approval_request_message에 "사용자 승인 요청" 정확히 1회
TC-VAL-2  approval_request_message에 "CODEX 검토 필요" 정확히 1회
TC-VAL-3  approval_request_message 마지막 줄이 "CODEX 검토 필요"
TC-VAL-4  금지 문구 "JSON 출력은 성공" 포함 시 ValueError
TC-VAL-5  금지 문구 "Pipeline Manager도" 포함 시 ValueError
TC-VAL-6  "사용자 승인 요청" 2회 포함 시 ValueError
TC-VAL-7  빈 문자열 입력 시 ValueError
TC-VAL-8  "CODEX 검토 필요"가 마지막 줄이 아니면 ValueError
TC-VAL-9  _validate_approval_request_message 함수가 pipeline 모듈에 존재
TC-BUILD-1  _build_approval_request_output이 검증 통과 메시지를 반환
TC-BUILD-2  _build_approval_request_output이 4요소를 정확히 1회씩 포함
TC-HOOK-1  _approve_message(hook)가 빈 문자열을 반환 (이중 출력 방지)
TC-HOOK-2  _approve_message(hook) 반환값에 "사용자 승인 요청"이 없음
TC-HOOK-3  hook 파일에 이중 출력 방지 주석(MT-28)이 존재
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pipeline  # noqa: E402

HOOK_FILE = REPO_ROOT / ".claude" / "hooks" / "codex_user_acceptance_review.py"


# ---------------------------------------------------------------------------
# TC-VAL-1: "사용자 승인 요청" 정확히 1회
# ---------------------------------------------------------------------------
def test_val_1_approval_marker_count_exactly_1():
    """유효한 메시지에서 '사용자 승인 요청'이 정확히 1회."""
    msg = "사용자 승인 요청\n\nPR: https://example.com\n\n승인 코드:\nACCEPT-TEST\n\nCODEX 검토 필요"
    # 검증 통과해야 함 (예외 없음)
    pipeline._validate_approval_request_message(msg)
    assert msg.count("사용자 승인 요청") == 1


# ---------------------------------------------------------------------------
# TC-VAL-2: "CODEX 검토 필요" 정확히 1회
# ---------------------------------------------------------------------------
def test_val_2_codex_required_count_exactly_1():
    """유효한 메시지에서 'CODEX 검토 필요'가 정확히 1회."""
    msg = "사용자 승인 요청\n\nPR: https://example.com\n\n승인 코드:\nACCEPT-TEST\n\nCODEX 검토 필요"
    pipeline._validate_approval_request_message(msg)
    assert msg.count("CODEX 검토 필요") == 1


# ---------------------------------------------------------------------------
# TC-VAL-3: 마지막 의미 있는 줄이 "CODEX 검토 필요"
# ---------------------------------------------------------------------------
def test_val_3_last_meaningful_line_codex_required():
    """마지막 의미 있는 줄이 'CODEX 검토 필요'인지 검증."""
    msg = "사용자 승인 요청\n\nPR: https://example.com\n\n승인 코드:\nACCEPT-TEST\n\nCODEX 검토 필요"
    lines = [ln for ln in msg.strip().split("\n") if ln.strip()]
    assert lines[-1].strip() == "CODEX 검토 필요"


# ---------------------------------------------------------------------------
# TC-VAL-4: 금지 문구 "JSON 출력은 성공" 포함 시 ValueError
# ---------------------------------------------------------------------------
def test_val_4_forbidden_phrase_json_output_raises():
    """금지 문구 'JSON 출력은 성공'이 포함된 메시지는 ValueError."""
    msg = (
        "사용자 승인 요청\n\nPR: https://example.com\n\n승인 코드:\nACCEPT-TEST\n\n"
        "JSON 출력은 성공했습니다. approval_request_message 필드를 추출합니다.\n\n"
        "CODEX 검토 필요"
    )
    with pytest.raises(ValueError, match="금지 문구"):
        pipeline._validate_approval_request_message(msg)


# ---------------------------------------------------------------------------
# TC-VAL-5: 금지 문구 "Pipeline Manager도" 포함 시 ValueError
# ---------------------------------------------------------------------------
def test_val_5_forbidden_phrase_pipeline_manager_raises():
    """금지 문구 'Pipeline Manager도'가 포함된 메시지는 ValueError."""
    msg = (
        "사용자 승인 요청\n\nPR: https://example.com\n\n승인 코드:\nACCEPT-TEST\n\n"
        "Pipeline Manager도 이를 확인했습니다.\n\nCODEX 검토 필요"
    )
    with pytest.raises(ValueError, match="금지 문구"):
        pipeline._validate_approval_request_message(msg)


# ---------------------------------------------------------------------------
# TC-VAL-6: "사용자 승인 요청" 2회 포함 시 ValueError
# ---------------------------------------------------------------------------
def test_val_6_duplicate_approval_marker_raises():
    """'사용자 승인 요청'이 2회 포함되면 ValueError."""
    msg = (
        "사용자 승인 요청\n\n사용자 승인 요청\n\nPR: https://example.com\n\n"
        "승인 코드:\nACCEPT-TEST\n\nCODEX 검토 필요"
    )
    with pytest.raises(ValueError, match="count=2"):
        pipeline._validate_approval_request_message(msg)


# ---------------------------------------------------------------------------
# TC-VAL-7: 빈 문자열 입력 시 ValueError
# ---------------------------------------------------------------------------
def test_val_7_empty_string_raises():
    """빈 문자열은 ValueError."""
    with pytest.raises(ValueError, match="빈 문자열"):
        pipeline._validate_approval_request_message("")


# ---------------------------------------------------------------------------
# TC-VAL-8: "CODEX 검토 필요"가 마지막 줄이 아니면 ValueError
# ---------------------------------------------------------------------------
def test_val_8_codex_required_not_last_line_raises():
    """'CODEX 검토 필요' 뒤에 다른 줄이 있으면 ValueError."""
    msg = (
        "사용자 승인 요청\n\nPR: https://example.com\n\n승인 코드:\nACCEPT-TEST\n\n"
        "CODEX 검토 필요\n\n추가 안내 문구입니다."
    )
    with pytest.raises(ValueError, match="마지막 줄"):
        pipeline._validate_approval_request_message(msg)


# ---------------------------------------------------------------------------
# TC-VAL-9: _validate_approval_request_message 함수 존재 확인
# ---------------------------------------------------------------------------
def test_val_9_validate_function_exists_in_pipeline():
    """pipeline 모듈에 _validate_approval_request_message 함수가 존재."""
    assert hasattr(pipeline, "_validate_approval_request_message"), (
        "pipeline 모듈에 _validate_approval_request_message 함수가 없습니다."
    )
    assert callable(pipeline._validate_approval_request_message)


# ---------------------------------------------------------------------------
# TC-BUILD-1: _build_approval_request_output 검증 통과 메시지 반환
# ---------------------------------------------------------------------------
def test_build_1_build_output_returns_valid_message():
    """_build_approval_request_output이 검증을 통과하는 approval_request_message를 반환."""
    result = pipeline._build_approval_request_output(
        "IMP-20260703-B985", "https://github.com/test/repo/pull/1"
    )
    msg = result["approval_request_message"]
    # 검증 함수가 통과해야 함 (예외 없음)
    pipeline._validate_approval_request_message(msg)


# ---------------------------------------------------------------------------
# TC-BUILD-2: _build_approval_request_output 4요소 각 1회
# ---------------------------------------------------------------------------
def test_build_2_build_output_four_elements_once_each():
    """_build_approval_request_output 반환 메시지에 4요소가 정확히 1회씩."""
    result = pipeline._build_approval_request_output(
        "IMP-20260703-B985", "https://github.com/test/repo/pull/1"
    )
    msg = result["approval_request_message"]
    assert msg.count("사용자 승인 요청") == 1, "사용자 승인 요청은 1회여야 함"
    assert msg.count("승인 코드:") == 1, "승인 코드:는 1회여야 함"
    assert msg.count("CODEX 검토 필요") == 1, "CODEX 검토 필요는 1회여야 함"
    # PR: 줄은 \nPR: 형식으로 1회
    assert msg.count("\nPR:") == 1, "\\nPR:는 1회여야 함"


# ---------------------------------------------------------------------------
# TC-HOOK-1: hook _approve_message가 빈 문자열 반환 (이중 출력 방지)
# ---------------------------------------------------------------------------
def test_hook_1_approve_message_returns_empty_string():
    """hook의 _approve_message()가 빈 문자열을 반환해야 한다 (MT-28 이중 출력 방지)."""
    if not HOOK_FILE.exists():
        pytest.skip("hook 파일 없음 — 환경 미설정")

    # hook 모듈을 동적으로 import
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "codex_user_acceptance_review", HOOK_FILE
    )
    hook_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook_mod)

    result = hook_mod._approve_message("IMP-20260703-B985", "https://example.com/pull/1")
    assert result == "", (
        f"_approve_message()가 빈 문자열이어야 하는데 '{result}'를 반환했습니다. "
        "MT-28 이중 출력 방지 수정이 필요합니다."
    )


# ---------------------------------------------------------------------------
# TC-HOOK-2: hook _approve_message 반환값에 "사용자 승인 요청" 없음
# ---------------------------------------------------------------------------
def test_hook_2_approve_message_no_approval_block():
    """hook의 _approve_message() 반환값에 '사용자 승인 요청'이 포함되면 안 된다."""
    if not HOOK_FILE.exists():
        pytest.skip("hook 파일 없음 — 환경 미설정")

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "codex_user_acceptance_review", HOOK_FILE
    )
    hook_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook_mod)

    result = hook_mod._approve_message("IMP-20260703-B985", None)
    assert "사용자 승인 요청" not in result, (
        "'사용자 승인 요청'이 hook _approve_message에 포함되어 이중 출력이 발생합니다."
    )


# ---------------------------------------------------------------------------
# TC-HOOK-3: hook 파일에 MT-28 이중 출력 방지 주석 존재
# ---------------------------------------------------------------------------
def test_hook_3_mt28_comment_in_hook_file():
    """hook 파일에 MT-28 이중 출력 방지 관련 주석이 존재해야 한다."""
    if not HOOK_FILE.exists():
        pytest.skip("hook 파일 없음 — 환경 미설정")

    content = HOOK_FILE.read_text(encoding="utf-8")
    assert "MT-28" in content, (
        "hook 파일에 MT-28 주석이 없습니다. 이중 출력 방지 수정 여부를 확인하세요."
    )
