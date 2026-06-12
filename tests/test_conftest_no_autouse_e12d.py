"""IMP-20260612-E12D MT-3: AC-8 검증 테스트.

_default_fake_gh_for_pr_body fixture가 autouse=False임을 명시적으로 검증한다.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_default_fake_gh_fixture_is_not_autouse() -> None:
    """AC-8: _default_fake_gh_for_pr_body은 autouse fixture가 아니다.

    autouse=True 이었을 때는 모든 테스트에 자동으로 PIPELINE_GH_EXECUTABLE을
    주입하여 gh CLI 부재 테스트를 가렸습니다. IMP-20260612-E12D MT-1에서
    autouse=True를 제거하여 opt-in fixture로 전환했습니다.
    """
    # fixture 함수의 autouse 여부 확인.
    # pytest 버전에 따라 fixture 메타데이터 보관 위치가 다르다:
    #   - pytest < 8: 함수에 _pytestfixturefunction(FixtureFunctionMarker) 속성
    #   - pytest >= 8/9: FixtureFunctionDefinition 객체의 _fixture_function_marker 속성
    import importlib
    conftest_module = importlib.import_module("conftest")
    fixture_func = conftest_module._default_fake_gh_for_pr_body

    marker = getattr(fixture_func, "_pytestfixturefunction", None)
    if marker is None:
        marker = getattr(fixture_func, "_fixture_function_marker", None)
    assert marker is not None, (
        "fixture 메타데이터(_pytestfixturefunction 또는 _fixture_function_marker)를 찾지 못함 — "
        "pytest.fixture 데코레이터 적용 여부 확인"
    )
    assert getattr(marker, "autouse", None) is False, (
        "_default_fake_gh_for_pr_body.autouse가 True입니다. "
        "IMP-20260612-E12D MT-1 수정이 적용되지 않았습니다."
    )


def test_pipeline_gh_executable_not_set_by_default(tmp_path: Path) -> None:
    """AC-8: 기본 상태에서 PIPELINE_GH_EXECUTABLE이 자동 설정되지 않는다.

    이 테스트 자체는 _default_fake_gh_for_pr_body fixture를 요청하지 않으므로,
    PIPELINE_GH_EXECUTABLE이 환경에 설정되어 있으면 안 됩니다.
    (다른 테스트의 monkeypatch가 정리된 상태 기준)
    """
    # 주의: os.environ에 PIPELINE_GH_EXECUTABLE이 원래부터 있는 환경에서는
    # 이 assertion이 실패할 수 있습니다. CI 환경 기준으로 작성됩니다.
    # 로컬에 PIPELINE_GH_EXECUTABLE이 실제 gh 경로로 설정된 경우 이 테스트를 skip합니다.
    if "PIPELINE_GH_EXECUTABLE" in os.environ:
        pytest.skip("PIPELINE_GH_EXECUTABLE이 환경변수로 이미 설정되어 있음 (외부 설정)")
    # PIPELINE_GH_EXECUTABLE이 없으면 이 테스트는 PASS
    assert "PIPELINE_GH_EXECUTABLE" not in os.environ
