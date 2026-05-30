# [Purpose]: 환경 설정에 따라 KIS / Mock provider를 자동 선택하는 팩토리 + 공통 데이터 접근 facade.
# [Assumptions]: provider는 프로세스 수명 동안 싱글톤. 테스트는 reset_provider()로 격리.
# [Vulnerability & Risks]: 싱글톤 전역 상태로 인해 스레드 간 race condition 가능 — 첫 생성 시점만 영향 (이후 read-only).
# [Improvement]: dependency injection 컨테이너 도입, thread-local 인스턴스.
"""데이터 서비스 — provider 팩토리 및 공통 데이터 접근 계층."""
from __future__ import annotations

import logging

import pandas as pd

from rule_watcher.providers.base import BaseProvider, StockInfo
from rule_watcher.providers.mock_provider import MockProvider

logger = logging.getLogger(__name__)

_provider: BaseProvider | None = None


def _create_provider() -> BaseProvider:
    """설정에 따라 적절한 provider 생성.

    Returns:
        KIS_APP_KEY/SECRET 설정 시: KISProvider 인스턴스 (실패하면 MockProvider).
        키 미설정 시: MockProvider 인스턴스.
    """
    from rule_watcher.config import is_kis_configured

    if is_kis_configured():
        try:
            from rule_watcher.providers.kis_provider import KISProvider

            provider = KISProvider()
            logger.info("KIS Open API provider 초기화 완료")
            return provider
        except Exception as e:
            logger.warning(f"KIS provider 초기화 실패, mock으로 대체: {e}")

    logger.info("API 키가 설정되지 않아 mock 데이터 모드로 실행합니다.")
    return MockProvider()


def get_provider() -> BaseProvider:
    """현재 provider 인스턴스 반환 (싱글톤)."""
    global _provider
    if _provider is None:
        _provider = _create_provider()
    return _provider


def reset_provider() -> None:
    """provider 재초기화 (테스트/설정 변경 시 사용)."""
    global _provider
    _provider = None


class DataService:
    """데이터 접근 공통 계층 — provider 위에 얇은 facade.

    UI / 룰 엔진 / 스크리너는 모두 이 DataService를 통해 데이터를 조회한다.
    내부 provider 교체 시 호출자 코드 변경 불필요.
    """

    def __init__(self, provider: BaseProvider | None = None) -> None:
        """DataService 생성.

        Args:
            provider: 명시적 provider 주입 (테스트용). None이면 싱글톤 사용.
        Raises:
            TypeError: provider가 BaseProvider 인스턴스가 아닌 경우.
        """
        if provider is not None and not isinstance(provider, BaseProvider):
            raise TypeError(
                f"provider must be BaseProvider, got {type(provider).__name__}"
            )
        self._provider: BaseProvider = provider or get_provider()

    @property
    def provider_name(self) -> str:
        """현재 사용 중인 provider 이름."""
        return self._provider.name

    def get_ohlcv(self, ticker: str, period: int = 120) -> pd.DataFrame:
        """OHLCV 데이터 조회. 예외 발생 시 빈 DataFrame 반환.

        Args:
            ticker: 종목코드 문자열.
            period: 조회할 봉 개수 (양수).
        Returns:
            OHLCV DataFrame (성공) 또는 빈 DataFrame (실패).
        Raises:
            TypeError: ticker가 None/str이 아니거나 period 타입 오류.
            ValueError: ticker 빈 문자열 또는 period <= 0.
        """
        if ticker is None:
            raise TypeError("ticker must not be None")
        if not isinstance(ticker, str):
            raise TypeError(f"ticker must be str, got {type(ticker).__name__}")
        if len(ticker) == 0:
            raise ValueError("ticker must not be empty")
        if not isinstance(period, int) or isinstance(period, bool):
            raise TypeError(f"period must be int, got {type(period).__name__}")
        if period <= 0:
            # negative not allowed: period는 양수여야 함
            raise ValueError(f"period must be > 0, got {period}")

        try:
            return self._provider.get_ohlcv(ticker, period)
        except (ValueError, TypeError):
            # 입력 오류는 그대로 전파 (조용히 빈 DataFrame 반환하면 디버깅 어려움)
            raise
        except Exception as e:
            logger.error(f"OHLCV 조회 실패 [{ticker}]: {e}")
            return pd.DataFrame()

    def get_current_price(self, ticker: str) -> StockInfo:
        """현재가 조회. 예외 발생 시 빈 StockInfo 반환.

        Args:
            ticker: 종목코드 문자열.
        Returns:
            StockInfo (성공) 또는 ticker만 채워진 빈 StockInfo (실패).
        Raises:
            TypeError: ticker가 None이거나 str이 아닌 경우.
            ValueError: ticker가 빈 문자열인 경우.
        """
        if ticker is None:
            raise TypeError("ticker must not be None")
        if not isinstance(ticker, str):
            raise TypeError(f"ticker must be str, got {type(ticker).__name__}")
        if len(ticker) == 0:
            raise ValueError("ticker must not be empty")

        try:
            return self._provider.get_current_price(ticker)
        except (ValueError, TypeError):
            raise
        except Exception as e:
            logger.error(f"현재가 조회 실패 [{ticker}]: {e}")
            return StockInfo(ticker=ticker, name=ticker)

    def get_stock_list(self, market: str = "KOSPI") -> list[StockInfo]:
        """종목 목록 조회. 예외 발생 시 빈 리스트 반환.

        Args:
            market: "KOSPI" | "KOSDAQ" | "ALL".
        Returns:
            StockInfo 리스트 (성공) 또는 빈 리스트 (실패).
        Raises:
            TypeError: market이 None이거나 str이 아닌 경우.
            ValueError: market이 빈 문자열인 경우.
        """
        if market is None:
            raise TypeError("market must not be None")
        if not isinstance(market, str):
            raise TypeError(f"market must be str, got {type(market).__name__}")
        if len(market) == 0:
            raise ValueError("market must not be empty")

        try:
            return self._provider.get_stock_list(market)
        except (ValueError, TypeError):
            raise
        except Exception as e:
            logger.error(f"종목 목록 조회 실패: {e}")
            return []
