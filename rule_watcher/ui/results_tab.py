# [Purpose]: 선별결과 탭 — 룰북을 관심종목 OHLCV에 적용하여 조건 충족 종목을 표시.
# [Assumptions]: ScreeningEngine은 watchlist item을 {ticker, name, df} dict로 받음. df는 OHLCV pandas.DataFrame.
# [Vulnerability & Risks]:
#   - OHLCV 조회 실패 종목은 조용히 누락 — UI에 누락 카운트 표시하여 사용자가 인지할 수 있게 함.
#   - 동기 호출이므로 N개 종목 × HTTP latency 만큼 블로킹 — provider 캐싱은 data_service.py 책임.
# [Improvement]: 비동기 OHLCV 일괄 조회, 진행률 표시, 결과 캐싱, CSV 내보내기.
"""선별 결과 탭 — 룰북 적용 + 결과 표시."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def render_results_tab() -> None:
    """선별결과 탭을 렌더링한다.

    Returns:
        없음. streamlit이 페이지를 렌더링.
    """
    import streamlit as st
    import pandas as pd

    from rule_watcher.config import DB_PATH

    st.subheader("🔍 선별 결과")
    st.caption(
        "선택한 룰북을 관심종목에 적용하여 조건을 충족한 종목을 보여줍니다. "
        "이 결과는 매수/매도 추천이 아닌 단순 조건 매칭 안내입니다."
    )

    rulebooks = _load_rulebooks(DB_PATH)
    if not rulebooks:
        st.info("룰북이 없습니다. '룰북' 탭에서 먼저 조건을 만드세요.")
        return

    rb_names = [rb["name"] for rb in rulebooks]
    selected_rb_name = st.selectbox(
        "적용할 룰북", rb_names, key="rs_select_rb"
    )
    sort_by = st.selectbox(
        "정렬 기준", ["조건 충족 시각순", "종목코드순"], key="rs_sort"
    )

    if not st.button("🔍 선별 실행", key="rs_run_btn"):
        return

    selected_rb: Dict[str, Any] = next(
        (rb for rb in rulebooks if rb["name"] == selected_rb_name), {}
    )
    if not selected_rb:
        st.error("선택된 룰북을 찾을 수 없습니다.")
        return

    # 룰북 로드
    from rule_watcher.engine.rule_model import RuleBook
    try:
        rulebook = RuleBook.deserialize(selected_rb.get("rulebook_json", ""))
    except Exception as exc:  # noqa: BLE001
        logger.error("룰북 로드 실패: %s", exc)
        st.error(f"룰북 로드 실패: {exc}")
        return

    # 관심종목 로드
    watchlist = _load_watchlist(DB_PATH)
    if not watchlist:
        st.warning("관심종목이 없습니다. '관심종목' 탭에서 종목을 추가하세요.")
        return

    # OHLCV 일괄 조회
    stocks, failed_count = _fetch_ohlcv_for_all(watchlist)
    if not stocks:
        st.error("OHLCV 데이터를 가져온 종목이 없습니다.")
        return

    # 선별 실행
    from rule_watcher.engine.screening_engine import ScreeningEngine
    try:
        engine = ScreeningEngine(rulebook)
        results = engine.screen_watchlist(stocks)
    except Exception as exc:  # noqa: BLE001
        logger.error("선별 엔진 실패: %s", exc)
        st.error(f"선별 실행 실패: {exc}")
        return

    matched = [r for r in results if getattr(r, "matched", False)]

    # 카운트 요약
    summary_col1, summary_col2, summary_col3 = st.columns(3)
    with summary_col1:
        st.metric("선별 대상", f"{len(stocks)}개")
    with summary_col2:
        st.metric("조건 충족", f"{len(matched)}개")
    with summary_col3:
        st.metric("조회 실패", f"{failed_count}개")

    if not matched:
        st.info("조건을 충족한 종목이 없습니다.")
        return

    st.success(f"✅ {len(matched)}개 종목이 조건을 충족했습니다.")

    rows: List[Dict[str, str]] = []
    for r in matched:
        rows.append(
            {
                "종목코드": str(getattr(r, "ticker", "")),  # allowed: dataclass attr는 str 가정
                "종목명": str(getattr(r, "name", "")),
                "선별 이유": str(getattr(r, "reason", "")),
                "선별 시각": str(getattr(r, "matched_at", "")),
            }
        )

    if sort_by == "종목코드순":
        rows.sort(key=lambda x: x["종목코드"])
    else:
        # 시각 내림차순 (최신순)
        rows.sort(key=lambda x: x["선별 시각"], reverse=True)

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


def _load_watchlist(db_path: str) -> List[Dict[str, Any]]:
    """관심종목 로드 (results_tab 전용 — watchlist_tab과 분리하여 의존 단순화).

    Args:
        db_path: SQLite DB 경로.
    Returns:
        ticker / name / market 키를 가진 dict 리스트.
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
                "SELECT ticker, name, market FROM watchlist"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.error("watchlist 로드 실패: %s", exc)
        return []

    return [{"ticker": r[0], "name": r[1], "market": r[2]} for r in rows]


def _load_rulebooks(db_path: str) -> List[Dict[str, Any]]:
    """룰북 로드.

    Args:
        db_path: SQLite DB 경로.
    Returns:
        name / rulebook_json / description 키를 가진 dict 리스트.
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
                "SELECT name, rulebook_json, description FROM rulebook"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.error("rulebook 로드 실패: %s", exc)
        return []

    return [
        {"name": r[0], "rulebook_json": r[1], "description": r[2] or ""}
        for r in rows
    ]


def _fetch_ohlcv_for_all(
    watchlist: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], int]:
    """관심종목 각각의 OHLCV를 조회하여 ScreeningEngine 입력 형태로 만든다.

    Args:
        watchlist: ticker/name/market 키를 가진 dict 리스트.
    Returns:
        (stocks, failed_count). stocks는 {ticker, name, df} dict 리스트.
    Raises:
        TypeError: watchlist가 None이거나 list가 아닌 경우.
    """
    if watchlist is None:
        raise TypeError("watchlist must not be None")
    if not isinstance(watchlist, list):
        raise TypeError(f"watchlist must be list, got {type(watchlist).__name__}")

    import streamlit as st
    from rule_watcher.providers.data_service import get_provider

    try:
        provider = get_provider()
    except Exception as exc:  # noqa: BLE001
        logger.error("provider 로드 실패: %s", exc)
        return [], len(watchlist)

    stocks: List[Dict[str, Any]] = []
    failed = 0

    progress = st.progress(0.0, text="데이터 로딩 중...")
    total = max(1, len(watchlist))
    for idx, item in enumerate(watchlist):
        if not isinstance(item, dict):
            failed += 1
            continue
        ticker = str(item.get("ticker", ""))  # allowed: SQLite row 값은 str/None
        name = str(item.get("name", ""))
        try:
            df = provider.get_ohlcv(ticker, period=120)
            stocks.append({"ticker": ticker, "name": name, "df": df})
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_ohlcv 실패 ticker=%s: %s", ticker, exc)
            failed += 1
        progress.progress((idx + 1) / total, text=f"데이터 로딩 중... ({idx+1}/{total})")

    progress.empty()
    return stocks, failed
