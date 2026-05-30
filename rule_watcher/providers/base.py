# [Purpose]: 데이터 provider의 공통 추상 인터페이스 정의. KIS/Mock 양쪽이 구현.
# [Assumptions]: pandas DataFrame을 OHLCV의 표준 자료형으로 사용. ticker는 6자리 한국 종목코드 문자열.
# [Vulnerability & Risks]: ABC 메서드 미구현 클래스 인스턴스화 시 TypeError 자연 발생. 인터페이스 외 추가 메서드는 구체 클래스 책임.
# [Improvement]: typing.Protocol로 전환하여 duck typing 허용.
"""데이터 provider 추상 인터페이스."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass
class StockInfo:
    """종목 기본 정보 + 현재가 스냅샷."""

    ticker: str
    name: str
    market: str = "KOSPI"
    current_price: float = 0.0
    change_rate: float = 0.0
    volume: int = 0
    updated_at: str = ""


class BaseProvider(ABC):
    """모든 데이터 provider의 공통 인터페이스."""

    @property
    @abstractmethod
    def name(self) -> str:
        """provider 이름 (예: 'kis', 'mock')."""

    @abstractmethod
    def get_ohlcv(self, ticker: str, period: int = 120) -> pd.DataFrame:
        """OHLCV 일봉 데이터 반환.

        Args:
            ticker: 6자리 종목코드 문자열.
            period: 조회할 봉 개수 (양수).
        Returns:
            columns=['open','high','low','close','volume'], index=DatetimeIndex (오름차순).
        """

    @abstractmethod
    def get_current_price(self, ticker: str) -> StockInfo:
        """현재가 정보 반환."""

    @abstractmethod
    def get_stock_list(self, market: str = "KOSPI") -> list[StockInfo]:
        """종목 목록 반환.

        Args:
            market: "KOSPI" | "KOSDAQ" | "ALL".
        """

    def is_available(self) -> bool:
        """provider 사용 가능 여부 (API 키 등 설정 확인). 기본값: 항상 True."""
        return True
