"""
회귀 테스트: pm-planner-agent.md Oracle Design Contract 섹션 문서 검증
pipeline_id: IMP-20260622-6976
AC 커버: AC-10 (회귀 테스트 PASS)

[Purpose]: pm-planner-agent.md의 Oracle Design Contract 섹션이 9개 핵심 원칙(AC-1~AC-9)을
           포함하고, SSoT 참조 원칙(AC-12, 80줄 이내)을 지키는지 회귀 검증한다.
[Assumptions]: .claude/agents/pm-planner-agent.md 파일이 UTF-8로 존재하며,
               '## Oracle Design Contract' 헤딩 다음 '## ' 헤딩 전까지가 해당 섹션이다.
[Vulnerability & Risks]: 문구 단순 substring 매칭이므로 동일 키워드가 섹션 밖에 있어도
                         전체 문서 기준으로 통과될 수 있다(섹션 격리 검증은 AC-12에서만 수행).
[Improvement]: 시간이 더 있다면 각 AC를 섹션 본문(start_idx~end_idx)으로 슬라이스하여
               섹션 내부에서만 키워드를 검증하도록 강화할 수 있다.
"""
import pathlib

import pytest

PM_PLANNER_MD = pathlib.Path(__file__).parent.parent / ".claude" / "agents" / "pm-planner-agent.md"


def _get_content() -> str:
    """pm-planner-agent.md 내용을 UTF-8 fallback 순서로 읽어 반환한다.

    Returns:
        파일 전체 텍스트 내용.
    Raises:
        FileNotFoundError: 대상 MD 파일이 존재하지 않는 경우.
        UnicodeDecodeError: 지원 인코딩으로 디코딩이 모두 실패한 경우.
    """
    if not PM_PLANNER_MD.exists():
        raise FileNotFoundError(f"검증 대상 파일이 없습니다: {PM_PLANNER_MD}")
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            return PM_PLANNER_MD.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise UnicodeDecodeError(
        "utf-8", b"", 0, 1, f"Cannot decode {PM_PLANNER_MD} with any supported encoding"
    )


def test_oracle_design_contract_section_exists() -> None:
    """AC-1: Oracle Design Contract 섹션 존재 확인"""
    content = _get_content()
    assert "## Oracle Design Contract" in content, (
        "pm-planner-agent.md에 '## Oracle Design Contract' 섹션이 없습니다 (AC-1)"
    )


def test_utf8_encoding_principle() -> None:
    """AC-2: UTF-8 인코딩 원칙 문구 존재 확인"""
    content = _get_content()
    assert any(term in content for term in ["UTF-8", "utf-8", "utf8"]), (
        "Oracle Design Contract 섹션에 UTF-8 인코딩 원칙 문구가 없습니다 (AC-2)"
    )


def test_ci_pytest_compatibility() -> None:
    """AC-3: CI/pytest 수집 호환성 명시 확인"""
    content = _get_content()
    assert "tests/oracles" in content, (
        "Oracle Design Contract 섹션에 tests/oracles 경로 명시가 없습니다 (AC-3)"
    )


def test_ac_ids_mapping_requirement() -> None:
    """AC-4: ac_ids 매핑 요구 명시 확인"""
    content = _get_content()
    assert "ac_ids" in content or "--ac-ids" in content, (
        "Oracle Design Contract 섹션에 ac_ids 매핑 요구 문구가 없습니다 (AC-4)"
    )


def test_minimum_oracle_composition() -> None:
    """AC-5: 최소 oracle 구성 원칙 (normal + edge/exception/error/regression) 명시 확인"""
    content = _get_content()
    has_normal = "normal" in content
    has_edge_variant = any(
        term in content for term in ["edge", "exception", "error", "regression"]
    )
    assert has_normal and has_edge_variant, (
        "Oracle Design Contract 섹션에 최소 oracle 구성 원칙"
        "(normal + edge/exception/error/regression)이 없습니다 (AC-5)"
    )


def test_expected_quality_principle() -> None:
    """AC-6: expected 품질 원칙 (agent_generated 금지) 명시 확인"""
    content = _get_content()
    has_agent_generated = "agent_generated" in content
    has_forbid_term = "forbidden" in content or "금지" in content
    assert has_agent_generated and has_forbid_term, (
        "Oracle Design Contract 섹션에 agent_generated 금지 원칙 문구가 없습니다 (AC-6)"
    )


def test_phase1_source_distinction() -> None:
    """AC-7: Phase 1 answer key 출처 구분 명시 확인"""
    content = _get_content()
    assert any(
        term in content for term in ["user_provided", "production_sample", "regression_capture"]
    ), (
        "Oracle Design Contract 섹션에 출처 구분 문구"
        "(user_provided/production_sample/regression_capture)가 없습니다 (AC-7)"
    )


def test_file_location_principle() -> None:
    """AC-8: 파일 위치 원칙 명시 확인"""
    content = _get_content()
    assert "tests/oracles" in content, (
        "Oracle Design Contract 섹션에 파일 위치 원칙(tests/oracles/) 문구가 없습니다 (AC-8)"
    )


def test_pipeline_py_terminology() -> None:
    """AC-9: pipeline.py 게이트 용어 일치 확인"""
    content = _get_content()
    assert "contract add-oracle" in content or "gates oracle" in content, (
        "Oracle Design Contract 섹션에 pipeline.py 용어"
        "(contract add-oracle 또는 gates oracle)가 없습니다 (AC-9)"
    )


def test_ssot_no_excessive_duplication() -> None:
    """AC-12: SSoT 참조 구조 확인 — Oracle Design Contract 섹션이 80줄 이내"""
    content = _get_content()
    lines = content.split("\n")
    # Oracle Design Contract 섹션의 시작과 끝 찾기
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if "## Oracle Design Contract" in line:
            start_idx = i
        elif start_idx is not None and line.startswith("## ") and i > start_idx:
            end_idx = i
            break
    if start_idx is None:
        pytest.fail("## Oracle Design Contract 섹션을 찾을 수 없습니다 (AC-12)")
    if end_idx is None:
        end_idx = len(lines)
    section_length = end_idx - start_idx
    assert section_length <= 80, (
        f"Oracle Design Contract 섹션이 {section_length}줄로 80줄 한도를 초과합니다 "
        f"(AC-12 SSoT 원칙 위반)"
    )


if __name__ == "__main__":
    # self-verify 블록: 핵심 함수와 대표 검증 동작 확인
    _md = _get_content()
    assert isinstance(_md, str) and len(_md) > 0, "MD 내용 로드 실패"
    assert "## Oracle Design Contract" in _md, "섹션 부재 — self-verify 실패"
    # 80줄 한도 정상 케이스 검증
    test_ssot_no_excessive_duplication()
    print("[SELF-VERIFY] OK")
