# [Purpose]: 룰북 탭 — 블록 조립식 조건 빌더, 룰북 저장/조회/삭제.
# [Assumptions]: rulebook 테이블 (name PK, rulebook_json, description, created_at, updated_at) 존재.
#               INDICATOR_TYPES 메타데이터는 rule_model.py에서 SSoT.
# [Vulnerability & Risks]:
#   - 사용자가 빈 룰북 이름이나 공백 이름을 입력하면 validate()가 거부하지만, UI 단에서도 1차 차단 필요.
#   - INDICATOR_TYPES의 params 목록에 "lookback" 등 추가 파라미터가 있는 경우 UI는 period만 노출 — v1 단순화.
# [Improvement]: 인디케이터별 파라미터 동적 폼, 그룹별 AND/OR, 조건별 설명 입력, 룰북 import/export.
"""룰북 탭 — 조건 빌더 UI."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def render_rulebook_tab() -> None:
    """룰북 탭을 렌더링한다.

    Returns:
        없음. streamlit이 페이지를 렌더링.
    """
    import streamlit as st

    from rule_watcher.config import DB_PATH

    st.subheader("📐 룰북 관리")
    st.caption(
        "조건을 조합한 룰북을 만들어 관심종목에 적용하세요. "
        "예: RSI(14) < 30 → 과매도, MACD_CROSS → 골든크로스."
    )

    col_list, col_edit = st.columns([1, 2])

    rulebooks = _load_rulebooks(DB_PATH)
    selected: Optional[Dict[str, Any]] = None

    with col_list:
        st.markdown("**저장된 룰북**")
        if not rulebooks:
            st.info("룰북이 없습니다.")
        else:
            rb_names = [rb["name"] for rb in rulebooks]
            selected_name = st.radio(
                "선택", rb_names, label_visibility="collapsed", key="rb_radio"
            )
            for rb in rulebooks:
                if rb["name"] == selected_name:
                    selected = rb
                    break

        if st.button("➕ 새 룰북", key="rb_new_btn"):
            st.session_state["rb_editing_new"] = True

    with col_edit:
        if st.session_state.get("rb_editing_new"):
            _render_new_rulebook_form(DB_PATH)
        elif selected is not None:
            _render_edit_rulebook(selected, DB_PATH)
        else:
            st.info("왼쪽에서 룰북을 선택하거나 '새 룰북' 버튼을 누르세요.")
            _show_sample_conditions()


def _render_new_rulebook_form(db_path: str) -> None:
    """새 룰북 생성 폼.

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

    import streamlit as st

    from rule_watcher.engine.rule_model import (
        INDICATOR_TYPES,
        RuleBook,
        RuleCondition,
        RuleGroup,
        VALID_OPERATORS,
    )

    indicator_options = list(INDICATOR_TYPES.keys())
    # bool 출력 인디케이터는 "is_true" 연산자만 의미 있음. 둘 다 사용 가능하도록 결합.
    operator_options = VALID_OPERATORS + ["is_true"]

    st.markdown("**새 룰북 만들기**")
    with st.form("rb_new_form"):
        rb_name = st.text_input("룰북 이름", max_chars=60, key="rb_new_name")
        rb_desc = st.text_input("설명 (선택)", max_chars=200, key="rb_new_desc")
        group_logic = st.selectbox("조건 결합 방식", ["AND", "OR"], key="rb_new_logic")

        st.markdown("**조건 추가** (최대 5개)")
        num_conds = st.number_input(
            "조건 수", min_value=1, max_value=5, value=1, step=1, key="rb_new_num"
        )

        conditions: List[Dict[str, Any]] = []
        for i in range(int(num_conds)):
            st.markdown(f"**조건 {i+1}**")
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                indicator = st.selectbox(
                    "인디케이터", indicator_options, key=f"rb_new_ind_{i}"
                )
            with c2:
                period = st.number_input(
                    "기간(period)",
                    min_value=1,
                    max_value=200,
                    value=14,
                    step=1,
                    key=f"rb_new_period_{i}",
                )
            with c3:
                operator = st.selectbox(
                    "연산자", operator_options, key=f"rb_new_op_{i}"
                )
            with c4:
                threshold = st.number_input(
                    "임계값", value=30.0, step=1.0, key=f"rb_new_thr_{i}"
                )
            conditions.append(
                {
                    "indicator": indicator,
                    "operator": operator,
                    "threshold": float(threshold),
                    "params": {"period": int(period)},
                }
            )

        submitted = st.form_submit_button("💾 저장")
        if submitted:
            name_clean = (rb_name or "").strip()
            if not name_clean:
                st.error("룰북 이름을 입력하세요.")
                return
            try:
                rb = RuleBook(
                    name=name_clean,
                    description=(rb_desc or "").strip(),
                    groups=[
                        RuleGroup(
                            name="그룹1",
                            logic=group_logic,
                            conditions=[
                                RuleCondition(
                                    indicator=c["indicator"],
                                    operator=c["operator"],
                                    threshold=c["threshold"],
                                    params=c["params"],
                                )
                                for c in conditions
                            ],
                        )
                    ],
                )
                rb.validate()
                _save_rulebook(rb, db_path)
                st.success(f"✅ '{name_clean}' 저장됨")
                st.session_state.pop("rb_editing_new", None)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                logger.error("룰북 저장 실패: %s", exc)
                st.error(f"룰북 저장 실패: {exc}")

    if st.button("❌ 취소", key="rb_new_cancel"):
        st.session_state.pop("rb_editing_new", None)
        st.rerun()


def _render_edit_rulebook(selected: Dict[str, Any], db_path: str) -> None:
    """기존 룰북 상세 표시 및 삭제.

    Args:
        selected: name/rulebook_json/description 키를 가진 dict.
        db_path: SQLite DB 경로.
    Raises:
        TypeError: selected가 None이거나 dict가 아닌 경우, db_path가 None이거나 str이 아닌 경우.
        ValueError: db_path가 빈 문자열인 경우.
    """
    if selected is None:
        raise TypeError("selected must not be None")
    if not isinstance(selected, dict):
        raise TypeError(f"selected must be dict, got {type(selected).__name__}")
    if db_path is None:
        raise TypeError("db_path must not be None")
    if not isinstance(db_path, str):
        raise TypeError(f"db_path must be str, got {type(db_path).__name__}")
    if not db_path.strip():
        # negative not allowed (empty path): 빈 경로는 DB 접근 불가
        raise ValueError("db_path must not be empty")

    import streamlit as st

    from rule_watcher.db import db_conn
    from rule_watcher.engine.rule_model import RuleBook

    name = str(selected.get("name", ""))  # allowed: SQLite row 값은 str/None이므로 str() 안전
    desc = str(selected.get("description", ""))
    json_str = selected.get("rulebook_json", "")

    st.markdown(f"**룰북: {name}**")
    if desc:
        st.caption(desc)

    try:
        rb = RuleBook.deserialize(json_str)
        for group in rb.groups:
            st.markdown(f"**조건 그룹: {group.name}** (`{group.logic}`)")
            for cond in group.conditions:
                params_str = ", ".join(f"{k}={v}" for k, v in cond.params.items())
                if cond.operator == "is_true":
                    st.markdown(f"  • `{cond.indicator}({params_str})` (참)")
                else:
                    st.markdown(
                        f"  • `{cond.indicator}({params_str})` "
                        f"{cond.operator} `{cond.threshold}`"
                    )
    except Exception as exc:  # noqa: BLE001
        logger.error("룰북 파싱 실패 name=%s: %s", name, exc)
        st.error(f"룰북 파싱 오류: {exc}")

    st.divider()
    if st.button(f"🗑️ '{name}' 삭제", key=f"rb_del_{name}"):
        try:
            with db_conn(db_path) as conn:
                conn.execute("DELETE FROM rulebook WHERE name=?", (name,))
            st.success(f"✅ '{name}' 삭제됨")
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            logger.error("룰북 삭제 실패: %s", exc)
            st.error(f"삭제 실패: {exc}")


def _show_sample_conditions() -> None:
    """예시 조건 표시 — 사용자 가이드용."""
    import streamlit as st

    st.markdown("**예시 조건**")
    st.code("RSI(14) < 30        → 과매도 신호")
    st.code("MACD_CROSS is_true  → 골든크로스 발생")
    st.code("VOLUME_MA_RATIO(20) > 2  → 거래량 급증")
    st.code("NEAR_HIGH(20) is_true   → 20일 신고가 근접")


def _load_rulebooks(db_path: str) -> List[Dict[str, Any]]:
    """rulebook 테이블에서 모든 룰북을 읽는다.

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
                "SELECT name, rulebook_json, description FROM rulebook ORDER BY updated_at DESC"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.error("rulebook 로드 실패: %s", exc)
        return []

    result: List[Dict[str, Any]] = []
    for r in rows:
        result.append(
            {
                "name": r[0],
                "rulebook_json": r[1],
                "description": r[2] or "",
            }
        )
    return result


def _save_rulebook(rb: Any, db_path: str) -> None:
    """RuleBook을 직렬화하여 SQLite에 저장.

    Args:
        rb: RuleBook 인스턴스.
        db_path: SQLite DB 경로.
    Raises:
        TypeError: rb 또는 db_path가 None인 경우.
        ValueError: db_path가 빈 문자열인 경우.
    """
    if rb is None:
        raise TypeError("rb must not be None")
    if db_path is None:
        raise TypeError("db_path must not be None")
    if not isinstance(db_path, str):
        raise TypeError(f"db_path must be str, got {type(db_path).__name__}")
    if not db_path.strip():
        # negative not allowed (empty path): 빈 경로는 DB 접근 불가
        raise ValueError("db_path must not be empty")

    from rule_watcher.db import db_conn

    with db_conn(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO rulebook (name, rulebook_json, description) VALUES (?,?,?)",
            (rb.name, rb.serialize(), rb.description),
        )
