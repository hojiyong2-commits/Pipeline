# [Purpose]: 알림 탭 — 알림 방식 토글 + 알림 로그 조회/삭제.
# [Assumptions]: alerts_log 테이블 (ticker, rule_id, rule_name, reason, triggered_at, provider) 존재.
#               알림 방식 설정은 v1에서 UI 상태만 유지 (DB persistence는 추후 작업).
# [Vulnerability & Risks]:
#   - 로그 삭제는 되돌릴 수 없음 — 사용자에게 명시적 확인 절차 필요 (v1은 단순 버튼).
#   - 알림 방식 설정값은 NotifierRegistry로 전파되지 않음 — v1에서는 표시만 (스케줄러 통합은 v2).
# [Improvement]: 알림 방식 DB persistence, 룰북별 알림 on/off, 알림 채널별 통계, 슬랙/이메일 연동.
"""알림 탭 — 알림 로그 표시 + 설정 토글."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def render_alert_tab() -> None:
    """알림 탭을 렌더링한다.

    Returns:
        없음. streamlit이 페이지를 렌더링.
    """
    import streamlit as st
    import pandas as pd

    from rule_watcher.config import ALERT_COOLDOWN_MIN, DB_PATH

    st.subheader("🔔 알림 설정 및 로그")
    st.caption(
        f"룰북 조건을 충족한 종목에 대해 알림을 발생시킵니다. "
        f"같은 종목 + 같은 룰은 {ALERT_COOLDOWN_MIN}분 쿨다운 동안 중복 알림이 억제됩니다."
    )

    # 알림 방식 설정
    st.markdown("**알림 방식**")
    col1, col2 = st.columns(2)
    with col1:
        in_app_enabled = st.checkbox(
            "앱 내부 알림", value=True, key="al_in_app"
        )
    with col2:
        sound_enabled = st.checkbox(
            "소리 알림", value=False, key="al_sound"
        )

    # 설정 상태 표시 (v1은 안내만; 실제 NotifierRegistry 연동은 v2)
    enabled_labels: List[str] = []
    if in_app_enabled:
        enabled_labels.append("앱 내부")
    if sound_enabled:
        enabled_labels.append("소리")
    if enabled_labels:
        st.caption(f"활성화된 알림: {', '.join(enabled_labels)}")
    else:
        st.warning("알림 방식이 모두 꺼져 있습니다. 최소 한 가지를 켜야 알림이 전달됩니다.")

    st.divider()

    # 알림 로그
    st.markdown("**알림 로그 (최근 100건)**")

    logs = _load_alert_log(DB_PATH)

    summary_col1, summary_col2 = st.columns(2)
    with summary_col1:
        st.metric("표시 중 로그", f"{len(logs)}건")
    with summary_col2:
        if st.button("🔄 새로고침", key="al_refresh"):
            st.rerun()

    if not logs:
        st.info("알림 로그가 없습니다.")
    else:
        df = pd.DataFrame(logs)
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("**로그 관리**")
    confirm = st.checkbox("로그 삭제 동의", key="al_confirm_del")
    if st.button("🗑️ 알림 로그 전체 삭제", key="al_del_btn"):
        if not confirm:
            st.warning("로그 삭제 동의에 체크한 뒤 다시 누르세요.")
        else:
            try:
                _clear_alert_log(DB_PATH)
                st.success("✅ 알림 로그가 초기화되었습니다.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                logger.error("알림 로그 삭제 실패: %s", exc)
                st.error(f"삭제 실패: {exc}")


def _load_alert_log(db_path: str) -> List[Dict[str, Any]]:
    """alerts_log 테이블에서 최근 100건을 읽는다.

    Args:
        db_path: SQLite DB 경로.
    Returns:
        UI 표시용 dict 리스트.
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
                "SELECT ticker, rule_id, rule_name, reason, triggered_at, provider "
                "FROM alerts_log ORDER BY triggered_at DESC LIMIT 100"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("alerts_log 조회 실패 (테이블 미존재 가능): %s", exc)
        return []

    result: List[Dict[str, Any]] = []
    for r in rows:
        result.append(
            {
                "종목코드": r[0],
                "룰ID": r[1],
                "룰이름": r[2],
                "사유": r[3],
                "발생시각": r[4],
                "데이터소스": r[5],
            }
        )
    return result


def _clear_alert_log(db_path: str) -> None:
    """alerts_log 테이블 전체 삭제.

    Args:
        db_path: SQLite DB 경로.
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

    with db_conn(db_path) as conn:
        conn.execute("DELETE FROM alerts_log")
