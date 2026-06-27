#!/usr/bin/env python3
# [Purpose]: User Acceptance 직전 승인 요청문과 Codex Review Contract 로드를 단일 SSoT로
#   제공한다 (IMP-20260627-3907). pipeline.py(gates request-accept)와 hook
#   (codex_user_acceptance_review.py)이 각자 승인 요청문을 자유서술/중복 정의하던 문제를
#   제거하고, 두 운영 경로가 모두 이 모듈의 render_user_acceptance_request()만 호출하도록 한다.
# [Assumptions]: 호출자는 importlib로 이 파일을 직접 로드한다(pipeline.py 전체 import 시
#   side effect 발생 방지). 이 모듈 자체는 import 시 어떤 부수효과도 발생시키지 않는다.
#   pr_url/pipeline_id는 호출자가 이미 검증한 값을 전달한다.
# [Vulnerability & Risks]: mode 문자열 오탈자 시 ValueError로 fail-closed 차단된다.
#   load_contract는 파일 부재/읽기 실패를 RuntimeError로 전파하여(fail-closed) hook이
#   contract 없이 검토를 진행하는 것을 막는다. 출력 양식은 oracle expected.txt와 바이트
#   단위로 일치해야 하므로 빈 줄/줄바꿈 수정 시 oracle 회귀를 유발할 수 있다.
# [Improvement]: 시간이 더 있다면 양식을 데이터 클래스/템플릿 파일로 분리하고, mode를
#   Enum으로 강제하며, 다국어 양식을 지원하도록 확장할 것이다.
"""User Acceptance 승인 요청문 renderer + Codex Review Contract 로더 (단일 SSoT).

운영 코드(pipeline.py, hook)는 자체적으로 승인 요청문을 작성하지 않고 이 모듈의
render_user_acceptance_request()만 호출한다.

양식:
  - A형 (mode='codex_review_required'): 마지막에 'CODEX 검토 필요' 포함
  - B형 (mode='user_final'): 'CODEX 검토 필요' 미포함

반환 문자열에는 trailing newline을 포함하지 않는다. 콘솔 출력 시 print()가
줄바꿈을 추가하므로, oracle expected.txt(끝에 단일 newline)와 비교할 때는
호출자/테스트가 '\n' 차이를 보정한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

# 허용되는 mode 값 (화이트리스트). 그 외는 ValueError.
_VALID_MODES: Tuple[str, str] = ("codex_review_required", "user_final")

# 인코딩 fallback 순서 (FS.encoding: utf-8 단독 금지)
_ENCODINGS: Tuple[str, ...] = ("utf-8", "utf-8-sig", "cp949", "latin-1")


def render_user_acceptance_request(mode: str, pr_url: str, pipeline_id: str) -> str:
    """User Acceptance 승인 요청문을 mode에 따라 렌더링한다 (단일 SSoT).

    Args:
        mode: 'codex_review_required'(A형) 또는 'user_final'(B형).
        pr_url: PR 링크 (그대로 출력에 포함).
        pipeline_id: 파이프라인 ID. 승인 코드는 'ACCEPT-{pipeline_id}'로 구성.
    Returns:
        렌더링된 승인 요청문 문자열 (trailing newline 없음).
    Raises:
        TypeError: 인자가 None이거나 str가 아닌 경우.
        ValueError: 빈 문자열이거나 mode가 허용 값이 아닌 경우.
    """
    for name, val in (
        ("mode", mode),
        ("pr_url", pr_url),
        ("pipeline_id", pipeline_id),
    ):
        if val is None:
            raise TypeError(f"{name} must not be None")
        if not isinstance(val, str):
            raise TypeError(f"{name} must be str, got {type(val).__name__}")

    # pr_url과 pipeline_id는 의미 있는 값이어야 함 (빈 문자열 차단)
    if len(pr_url.strip()) == 0:
        raise ValueError("pr_url must not be empty")
    if len(pipeline_id.strip()) == 0:
        raise ValueError("pipeline_id must not be empty")

    # mode 화이트리스트 검증 (fail-closed)
    if mode not in _VALID_MODES:
        raise ValueError(
            f"mode must be one of {_VALID_MODES}, got {mode!r}"
        )

    lines = [
        "사용자 승인 요청",
        "",
        f"PR: {pr_url}",
        "",
        "승인 코드:",
        f"ACCEPT-{pipeline_id}",
    ]
    if mode == "codex_review_required":
        lines.append("")
        lines.append("CODEX 검토 필요")

    return "\n".join(lines)


def load_contract(contract_path: str) -> str:
    """Codex Review Contract 파일을 읽어 내용을 반환한다 (fail-closed).

    Args:
        contract_path: contract 파일 경로 (str 또는 Path).
    Returns:
        contract 파일 텍스트 내용.
    Raises:
        TypeError: contract_path가 None이거나 str/Path가 아닌 경우.
        ValueError: contract_path가 빈 문자열인 경우.
        RuntimeError: 파일이 없거나 읽기 실패한 경우 (fail-closed).
    """
    if contract_path is None:
        raise TypeError("contract_path must not be None")
    if not isinstance(contract_path, (str, Path)):
        raise TypeError(
            f"contract_path must be str or Path, got {type(contract_path).__name__}"
        )
    if isinstance(contract_path, str) and len(contract_path.strip()) == 0:
        raise ValueError("contract_path must not be empty")

    path = Path(contract_path)
    if not path.exists():
        raise RuntimeError(
            f"Codex Review Contract를 로드할 수 없습니다: {contract_path}"
        )
    # 4-encoding fallback 읽기 (FS.encoding)
    for enc in _ENCODINGS:
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
        except OSError as e:
            raise RuntimeError(
                f"Codex Review Contract를 로드할 수 없습니다: {contract_path}"
            ) from e
    raise RuntimeError(
        f"Codex Review Contract를 로드할 수 없습니다: {contract_path}"
    )


if __name__ == "__main__":
    # 정상 A형
    _a = render_user_acceptance_request(
        "codex_review_required",
        "https://github.com/x/y/pull/1",
        "IMP-20260627-3907",
    )
    assert "CODEX 검토 필요" in _a, "A형에 CODEX 검토 필요 누락"
    assert _a.startswith("사용자 승인 요청"), "A형 헤더 불일치"
    assert "ACCEPT-IMP-20260627-3907" in _a, "A형 승인 코드 불일치"
    # 정상 B형
    _b = render_user_acceptance_request(
        "user_final",
        "https://github.com/x/y/pull/1",
        "IMP-20260627-3907",
    )
    assert "CODEX 검토 필요" not in _b, "B형에 CODEX 검토 필요 포함됨"
    # mode 오류
    try:
        render_user_acceptance_request("bad", "u", "p")
        assert False, "잘못된 mode에 예외 미발생"
    except ValueError:
        pass
    # None 방어
    try:
        render_user_acceptance_request(None, "u", "p")  # type: ignore[arg-type]
        assert False, "None mode에 예외 미발생"
    except TypeError:
        pass
    # contract 부재 → RuntimeError
    try:
        load_contract("/nonexistent/path/codex_review_contract.md")
        assert False, "없는 contract에 예외 미발생"
    except RuntimeError as e:
        assert "contract" in str(e).lower(), "RuntimeError 메시지에 contract 없음"
    print("[SELF-VERIFY] acceptance_renderer OK")
