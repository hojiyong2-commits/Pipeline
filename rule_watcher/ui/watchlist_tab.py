# [Purpose]: 관심종목 탭 — 종목 추가/삭제, 현재가/등락률/거래량 표시.
# [Assumptions]: SQLite watchlist 테이블 (ticker PK, name, market, added_at) 존재. provider 호출은 ticker 단위.
# [Vulnerability & Risks]:
#   - ticker 입력 sanitization은 strip()만 수행. SQLite parameter binding으로 SQL injection은 방지되지만
#     6자리 숫자 검증 등 도메인 검증은 누락 (v1 단순화).
#   - provider 호출 실패 시 "조회 실패" 행으로 표시하여 전체 테이블 무력화 방지.
# [Improvement]: 종목 코드 자동완성, 검색, 정렬, 다중 선택 삭제.
"""관심종목 탭 — SQLite watchlist 테이블 CRUD + 시세 조회."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def render_watchlist_tab() -> None:
    """관심종목 탭을 렌더링한다.

    Returns:
        없음. streamlit이 페이지를 렌더링.
    """
    import streamlit as st
    import pandas as pd

    from rule_watcher.config import DB_PATH
    from rule_watcher.db import db_conn

    st.subheader("📋 관심종목 관리")

    # 종목 추가 폼
    with st.expander("➕ 종목 추가", expanded=False):
        with st.form("add_watchlist", clear_on_submit=True):
            col1, col2 = st.columns(2)
            with col1:
                ticker_input = st.text_input(
                    "종목 코드 (예: 005930)", max_chars=10, key="wl_add_ticker"
                )
            with col2:
                name_input = st.text_input(
                    "종목명 (예: 삼성전자)", max_chars=40, key="wl_add_name"
                )
            market_input = st.selectbox("시장", ["KOSPI", "KOSDAQ"], key="wl_add_market")
            submitted = st.form_submit_button("추가")
            if submitted:
                ticker_clean = (ticker_input or "").strip()
                name_clean = (name_input or "").strip()
                if not ticker_clean or not name_clean:
                    st.error("종목 코드와 종목명을 모두 입력하세요.")
                else:
                    try:
                        with db_conn(DB_PATH) as conn:
                            conn.execute(
                                "INSERT OR REPLACE INTO watchlist (ticker, name, market) VALUES (?,?,?)",
                                (ticker_clean, name_clean, market_input),
                            )
                        st.success(f"✅ {name_clean}({ticker_clean}) 추가됨")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        logger.error("관심종목 추가 실패: %s", exc)
                        st.error(f"추가 실패: {exc}")

    # 관심종목 목록
    watchlist = _load_watchlist(DB_PATH)
    if not watchlist:
        st.info("관심종목이 없습니다. 위에서 종목을 추가하세요.")
        return

    # 현재 시세 갱신 버튼
    refresh_col, _ = st.columns([1, 5])
    with refresh_col:
        if st.button("🔄 시세 새로고침"):
            st.rerun()

    rows = _build_display_rows(watchlist)
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # 삭제 UI
    st.divider()
    st.markdown("**종목 삭제**")
    tickers = [item["ticker"] for item in watchlist]
    del_options = ["선택하세요"] + tickers
    del_ticker = st.selectbox(
        "삭제할 종목", del_options, key="wl_delete_select"
    )
    if st.button("🗑️ 선택 종목 삭제"):
        if del_ticker == "선택하세요":
            st.warning("삭제할 종목을 선택하세요.")
        else:
            try:
                with db_conn(DB_PATH) as conn:
                    conn.execute("DELETE FROM watchlist WHERE ticker=?", (del_ticker,))
                st.success(f"✅ {del_ticker} 삭제됨")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                logger.error("관심종목 삭제 실패: %s", exc)
                st.error(f"삭제 실패: {exc}")


def _load_watchlist(db_path: str) -> List[Dict[str, Any]]:
    """SQLite watchlist 테이블에서 모든 종목을 읽는다.

    Args:
        db_path: SQLite DB 경로.
    Returns:
        ticker / name / market 키를 가진 dict 리스트. 최신 추가 순.
    Raises:
        TypeError: db_path가 None이거나 str이 아닌 경우.
        ValueError: db_path가 빈 문자열인 경우.
    """
    if db_path is None:
        raise TypeError("db_path must not be None")
    if not isinstance(db_path, str):
        raise TypeError(f"db_path must be str, got {type(db_path).__name__}")
    if not db_path.strip():
        # negative not allowed (empty path): 빈 경로는 DB 접근 불가
        raise ValueError("db_path must not be empty")

    from rule_watcher.db import db_conn

    try:
        with db_conn(db_path) as conn:
            rows = conn.execute(
                "SELECT ticker, name, market FROM watchlist ORDER BY rowid DESC"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.error("watchlist 로드 실패: %s", exc)
        return []

    result: List[Dict[str, Any]] = []
    for r in rows:
        result.append({"ticker": r[0], "name": r[1], "market": r[2]})
    return result


def _build_display_rows(watchlist: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """watchlist 각 종목에 대해 provider에서 시세를 조회하여 표시용 dict 목록 생성.

    Args:
        watchlist: ticker/name/market 키를 가진 dict 리스트.
    Returns:
        UI 표시용 행 목록.
    Raises:
        TypeError: watchlist가 None이거나 list가 아닌 경우.
    """
    if watchlist is None:
        raise TypeError("watchlist must not be None")
    if not isinstance(watchlist, list):
        raise TypeError(f"watchlist must be list, got {type(watchlist).__name__}")

    from rule_watcher.providers.data_service import get_provider

    try:
        provider = get_provider()
    except Exception as exc:  # noqa: BLE001
        logger.error("provider 로드 실패: %s", exc)
        provider = None

    rows: List[Dict[str, str]] = []
    for item in watchlist:
        if not isinstance(item, dict):
            # 비정상 타입은 건너뜀
            continue
        ticker = str(item.get("ticker", ""))  # allowed: SQLite row 값은 str/None이므로 str() 안전
        name = str(item.get("name", ""))
        if provider is None:
            rows.append(_failed_row(ticker, name))
            continue
        try:
            info = provider.get_current_price(ticker)
            rows.append(
                {
                    "종목코드": info.ticker,
                    "종목명": info.name or name,
                    "현재가": f"{info.current_price:,.0f}원",
                    "등락률": f"{info.change_rate:+.2f}%",
                    "거래량": f"{int(info.volume):,}",
                    "업데이트": info.updated_at,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_current_price 실패 ticker=%s: %s", ticker, exc)
            rows.append(_failed_row(ticker, name))
    return rows


def _failed_row(ticker: str, name: str) -> Dict[str, str]:
    """시세 조회 실패 시 표시할 placeholder 행."""
    return {
        "종목코드": ticker,
        "종목명": name,
        "현재가": "조회 실패",
        "등락률": "-",
        "거래량": "-",
        "업데이트": "-",
    }
