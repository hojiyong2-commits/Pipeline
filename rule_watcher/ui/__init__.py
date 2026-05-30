# [Purpose]: rule_watcher.ui 패키지 — Streamlit 4탭 UI 및 백그라운드 스케줄러 모듈을 묶는다.
# [Assumptions]: 본 패키지는 streamlit 런타임에서 import되어 사용됨. import 시 Streamlit 초기화는 수행하지 않음.
# [Vulnerability & Risks]: 패키지 import 시 부수 효과가 없도록 유지. 부수 효과 발생 시 streamlit 캐싱/세션 충돌 가능.
# [Improvement]: 추후 ui 컴포넌트 자동 등록 메커니즘(Plug-in 방식) 도입.
"""Streamlit 기반 UI 패키지."""
from __future__ import annotations

__all__ = [
    "run_app",
]


def __getattr__(name: str):
    """Lazy attribute access to avoid importing streamlit at package import time.

    Args:
        name: 접근하려는 속성 이름.
    Returns:
        run_app 함수 (rule_watcher.ui.app.run_app).
    Raises:
        AttributeError: 등록되지 않은 속성을 요청한 경우.
    """
    if name == "run_app":
        from rule_watcher.ui.app import run_app
        return run_app
    raise AttributeError(f"module 'rule_watcher.ui' has no attribute {name!r}")
