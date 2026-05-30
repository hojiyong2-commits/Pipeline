# [Purpose]: 다수 종목의 OHLCV DataFrame에 RuleBook을 적용해 (matched, reason) 결과 목록을 반환.
# [Assumptions]:
#   - watchlist 항목은 dict {"ticker": str, "name": str, "df": pd.DataFrame} 형태.
#   - 단일 RuleBook 기준 평가. 다중 RuleBook은 호출자가 ScreeningEngine 인스턴스를 여러 개 생성.
#   - 시장 전체 스크리닝(screen_market)도 watchlist와 동일 처리 — 확장 지점은 정렬/필터.
# [Vulnerability & Risks]:
#   - 종목 하나에서 예외 발생 시 전체 실패하지 않도록 종목별 try-except로 격리, 실패 항목은 matched=False.
#   - watchlist 가 매우 클 경우 단일 스레드 평가 — UI 응답성을 위해 호출자가 threading/asyncio 적용 권장.
# [Improvement]:
#   - 병렬 평가(ProcessPoolExecutor), 종목별 캐싱, 결과 페이지네이션.
"""스크리닝 엔진 — RuleBook을 다수 종목에 적용."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

import pandas as pd

from rule_watcher.engine.rule_model import RuleBook
from rule_watcher.engine.rulebook_engine import RuleEvaluator


@dataclass
class ScreenResult:
    """단일 종목 스크리닝 결과."""

    ticker: str
    name: str
    matched: bool
    reason: str  # 한국어 설명 (충족 시 조건 요약, 미충족 시 사유 또는 "데이터 부족...")
    matched_at: str  # ISO 8601 datetime (UTC)
    error: str = ""  # 평가 중 예외가 발생한 경우 메시지 (비어 있으면 정상)

    def to_dict(self) -> Dict[str, Any]:
        """JSON 호환 dict 변환."""
        return {
            "ticker": self.ticker,
            "name": self.name,
            "matched": self.matched,
            "reason": self.reason,
            "matched_at": self.matched_at,
            "error": self.error,
        }


@dataclass
class ScreeningEngine:
    """관심종목/시장 전체에 단일 RuleBook을 적용하는 엔진.

    Args:
        rulebook: 평가할 RuleBook.
    """

    rulebook: RuleBook
    evaluator: RuleEvaluator = field(default_factory=RuleEvaluator)

    def __post_init__(self) -> None:
        if self.rulebook is None:
            raise TypeError("ScreeningEngine: rulebook must not be None")
        if not isinstance(self.rulebook, RuleBook):
            raise TypeError(
                f"ScreeningEngine: rulebook must be RuleBook, "
                f"got {type(self.rulebook).__name__}"
            )
        # 룰북 무결성 사전 검증 — 잘못된 룰북으로 스크리닝 시작하지 않음
        self.rulebook.validate()

    def screen_ticker(
        self, ticker: str, name: str, df: pd.DataFrame
    ) -> ScreenResult:
        """단일 종목 평가.

        Args:
            ticker: 종목 코드 (1~20자).
            name: 종목명 (1~64자).
            df: OHLCV DataFrame.
        Returns:
            ScreenResult.
        Raises:
            TypeError: ticker/name/df 가 None 또는 잘못된 타입.
            ValueError: ticker 또는 name 이 빈 문자열 / 길이 초과.
        """
        if ticker is None:
            raise TypeError("screen_ticker: ticker must not be None")
        if not isinstance(ticker, str):
            raise TypeError(
                f"screen_ticker: ticker must be str, got {type(ticker).__name__}"
            )
        if not ticker.strip():
            # negative not allowed: 빈 ticker는 식별 불가
            raise ValueError("screen_ticker: ticker가 빈 문자열입니다")
        if len(ticker) > 20:
            raise ValueError(
                f"screen_ticker: ticker 길이는 1~20자, got {len(ticker)}"
            )

        if name is None:
            raise TypeError("screen_ticker: name must not be None")
        if not isinstance(name, str):
            raise TypeError(
                f"screen_ticker: name must be str, got {type(name).__name__}"
            )
        if not name.strip():
            raise ValueError("screen_ticker: name이 빈 문자열입니다")
        if len(name) > 64:
            raise ValueError(
                f"screen_ticker: name 길이는 1~64자, got {len(name)}"
            )

        if df is None:
            raise TypeError("screen_ticker: df must not be None")
        if not isinstance(df, pd.DataFrame):
            raise TypeError(
                f"screen_ticker: df must be pd.DataFrame, got {type(df).__name__}"
            )

        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            matched, reason = self.evaluator.evaluate_rulebook(df, self.rulebook)
            return ScreenResult(
                ticker=ticker,
                name=name,
                matched=matched,
                reason=reason,
                matched_at=now_iso,
                error="",
            )
        except Exception as exc:  # noqa: BLE001 — 개별 종목 실패는 전체 스크리닝 중단 안 됨
            return ScreenResult(
                ticker=ticker,
                name=name,
                matched=False,
                reason=f"평가 중 오류: {exc}",
                matched_at=now_iso,
                error=str(exc),
            )

    def screen_watchlist(
        self, watchlist: List[Dict[str, Any]]
    ) -> List[ScreenResult]:
        """관심종목 전체를 평가하고 matched=True 항목을 상단으로 정렬.

        Args:
            watchlist: [{"ticker": "005930", "name": "삼성전자", "df": DataFrame}, ...]
        Returns:
            ScreenResult 리스트 (matched=True 먼저, 그 뒤 matched=False; 입력 순서 안정).
        Raises:
            TypeError: watchlist가 None 또는 list가 아님 / 항목 dict 아님.
            ValueError: 필수 키 누락.
        """
        if watchlist is None:
            raise TypeError("screen_watchlist: watchlist must not be None")
        if not isinstance(watchlist, list):
            raise TypeError(
                f"screen_watchlist: watchlist must be list, "
                f"got {type(watchlist).__name__}"
            )
        # 빈 watchlist는 ValueError가 아니라 정상 — 결과 빈 리스트
        if len(watchlist) == 0:
            return []

        results: List[ScreenResult] = []
        for idx, item in enumerate(watchlist):
            if not isinstance(item, dict):
                raise TypeError(
                    f"screen_watchlist: watchlist[{idx}]는 dict여야 합니다, "
                    f"got {type(item).__name__}"
                )
            ticker = item.get("ticker")
            name = item.get("name")
            df = item.get("df")
            if ticker is None:
                raise ValueError(
                    f"screen_watchlist: watchlist[{idx}]에 'ticker' 키가 필요합니다"
                )
            if name is None:
                raise ValueError(
                    f"screen_watchlist: watchlist[{idx}]에 'name' 키가 필요합니다"
                )
            if df is None:
                raise ValueError(
                    f"screen_watchlist: watchlist[{idx}]에 'df' 키가 필요합니다"
                )
            results.append(self.screen_ticker(ticker, name, df))

        # matched=True 우선, 동일 그룹 내에서는 원본 순서 유지(stable sort)
        results.sort(key=lambda r: 0 if r.matched else 1)
        return results

    def screen_market(
        self, stocks: List[Dict[str, Any]]
    ) -> List[ScreenResult]:
        """시장 전체 스크리닝. v1은 screen_watchlist와 동일 로직 (확장 지점).

        Args:
            stocks: [{"ticker", "name", "df"}] 리스트.
        Returns:
            ScreenResult 리스트.
        """
        # 시장 전체에서 결과가 너무 많을 경우 호출자가 .matched=True만 필터링하면 됨
        return self.screen_watchlist(stocks)


if __name__ == "__main__":
    # 자가 검증 — 강한 하락 종목(matched) + 강한 상승 종목(not matched)
    import pandas as pd
    from rule_watcher.engine.rule_model import RuleBook, RuleCondition, RuleGroup

    falling = [55000 - i * 500 for i in range(20)]
    rising = [60000 + i * 1000 for i in range(20)]

    def make_df(close_list):
        return pd.DataFrame(
            {
                "open": [c + 200 for c in close_list],
                "high": [c + 500 for c in close_list],
                "low": [c - 200 for c in close_list],
                "close": close_list,
                "volume": [1_000_000] * len(close_list),
            }
        )

    rulebook = RuleBook(
        name="과매도 룰",
        groups=[
            RuleGroup(
                name="oversold",
                conditions=[
                    RuleCondition(
                        indicator="RSI",
                        operator="<",
                        threshold=30.0,
                        params={"period": 14},
                    )
                ],
            )
        ],
    )

    engine = ScreeningEngine(rulebook=rulebook)
    results = engine.screen_watchlist(
        [
            {"ticker": "005930", "name": "삼성전자", "df": make_df(falling)},
            {"ticker": "035720", "name": "카카오", "df": make_df(rising)},
        ]
    )
    assert len(results) == 2, "결과 개수는 2여야 함"
    matched = [r for r in results if r.matched]
    assert len(matched) == 1, f"matched 종목 1개여야 함, got {len(matched)}"
    assert matched[0].ticker == "005930", f"매칭 종목 005930여야 함, got {matched[0].ticker}"

    # 빈 watchlist는 ValueError가 아니라 빈 리스트 반환
    empty = engine.screen_watchlist([])
    assert empty == [], "빈 watchlist는 빈 결과여야 함"

    print("[SELF-VERIFY] screening_engine OK")
