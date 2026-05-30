# [Purpose]: Streamlit 앱 진입 — 4탭(관심종목/룰북/선별결과/알림) 레이아웃 구성 및 provider 상태 표시.
# [Assumptions]: streamlit 1.20+ (st.tabs, st.rerun 지원). 본 앱은 룰 기반 감시 전용이며 매매 추천이 아님.
# [Vulnerability & Risks]:
#   - Streamlit session_state 충돌: 다중 사용자 환경(서버 배포)에서는 세션 격리 필요.
#   - tab import는 함수 내부에서 수행하여 streamlit 미설치 환경에서도 패키지 import는 가능하게 함.
# [Improvement]: 다국어 지원, 사용자별 DB 분리, OAuth 기반 인증.
"""Streamlit 앱 메인 — 4탭 레이아웃 (관심종목/룰북/선별결과/알림)."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def run_app() -> None:
    """Streamlit 앱 메인 함수. main.py에서 호출됨.

    Returns:
        없음. streamlit이 페이지를 렌더링한 뒤 반환.
    Raises:
        ImportError: streamlit이 설치되어 있지 않은 경우.
    """
    # streamlit / 내부 모듈은 함수 내부에서 import — 패키지 import 시 부수 효과 방지
    import streamlit as st

    from rule_watcher.config import get_provider_name

    st.set_page_config(
        page_title="한국주식 Rule Watcher",
        page_icon="📊",
        layout="wide",
    )

    st.title("📊 한국주식 Rule Watcher")
    st.caption(
        "룰 기반 종목 감시 앱 — 조건 충족 시 알림만 제공합니다. "
        "이 앱은 매매 추천을 하지 않으며, 자동 주문 기능도 없습니다."
    )

    # provider 상태 표시
    try:
        provider_name = get_provider_name()
    except Exception as exc:  # noqa: BLE001 - UI 레벨에서 모든 실패는 안내로 표시
        logger.error("provider 상태 조회 실패: %s", exc)
        provider_name = "mock"

    if provider_name == "mock":
        st.info(
            "🔧 Mock 데이터 모드: API 키가 설정되지 않아 가상 시세로 동작합니다. "
            "실제 시세를 사용하려면 `.env` 파일에 `KIS_APP_KEY` / `KIS_APP_SECRET`을 설정하세요."
        )
    else:
        st.success("✅ KIS Open API 연결 준비됨")

    tab_watch, tab_rule, tab_result, tab_alert = st.tabs(
        ["📋 관심종목", "📐 룰북", "🔍 선별 결과", "🔔 알림"]
    )

    with tab_watch:
        from rule_watcher.ui.watchlist_tab import render_watchlist_tab
        render_watchlist_tab()

    with tab_rule:
        from rule_watcher.ui.rulebook_tab import render_rulebook_tab
        render_rulebook_tab()

    with tab_result:
        from rule_watcher.ui.results_tab import render_results_tab
        render_results_tab()

    with tab_alert:
        from rule_watcher.ui.alert_tab import render_alert_tab
        render_alert_tab()
