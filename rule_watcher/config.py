# [Purpose]: 환경변수 / .env 파일에서 KIS API 및 앱 설정을 안전하게 로드한다.
# [Assumptions]: python-dotenv 설치됨. 실제 secret은 .env 또는 OS 환경변수에만 존재.
# [Vulnerability & Risks]: getenv 빈 문자열 fallback으로 인해 is_kis_configured()가 False 반환 시 자동 mock fallback 작동. 환경변수 인젝션은 OS 레이어 책임.
# [Improvement]: pydantic-settings 도입으로 타입 검증과 .env 스키마 자동화.
"""앱 설정 로더 — .env 또는 환경변수에서 읽음. 코드에 secret 저장 금지."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# .env 파일이 존재하면 로드 (없으면 OS 환경변수만 사용)
load_dotenv()


def _read_int_env(key: str, default: int) -> int:
    """환경변수에서 int 값 안전하게 읽기 (잘못된 값이면 기본값 반환).

    Args:
        key: 환경변수 이름.
        default: 변환 실패 시 사용할 기본값.
    Returns:
        int 값.
    Raises:
        TypeError: key가 None이거나 str이 아닌 경우.
        ValueError: default가 음수인 경우 (시간/주기 설정은 음수 불허).
    """
    if key is None:
        raise TypeError("key must not be None")
    if not isinstance(key, str):
        raise TypeError(f"key must be str, got {type(key).__name__}")
    if not isinstance(default, int):
        raise TypeError(f"default must be int, got {type(default).__name__}")
    if default < 0:
        # negative not allowed: 시간/주기 설정은 0 이상이어야 함
        raise ValueError(f"default must be >= 0, got {default}")

    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return default
    if value < 0:
        # negative not allowed: 시간/주기 설정은 0 이상이어야 함
        return default
    return value


# KIS API 설정 (환경변수에서만 읽음, 코드에 저장 금지)
KIS_APP_KEY: str = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET: str = os.getenv("KIS_APP_SECRET", "")
KIS_ACCOUNT_NO: str = os.getenv("KIS_ACCOUNT_NO", "")
KIS_BASE_URL: str = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")

# 앱 설정
DB_PATH: str = os.getenv("RULE_WATCHER_DB", str(Path.home() / "rule_watcher.db"))
WATCHLIST_REFRESH_SEC: int = _read_int_env("WATCHLIST_REFRESH_SEC", 60)
SCREENING_INTERVAL_SEC: int = _read_int_env("SCREENING_INTERVAL_SEC", 300)
ALERT_COOLDOWN_MIN: int = _read_int_env("ALERT_COOLDOWN_MIN", 30)


def is_kis_configured() -> bool:
    """KIS API 키가 모두 설정됐는지 확인.

    Returns:
        True: KIS_APP_KEY와 KIS_APP_SECRET가 모두 비어있지 않음.
        False: 하나라도 비어있음 → MockProvider로 자동 fallback.
    """
    return bool(KIS_APP_KEY and KIS_APP_SECRET)


def get_provider_name() -> str:
    """현재 사용할 provider 이름 반환.

    Returns:
        "kis": KIS API 키 설정됨.
        "mock": 키 미설정 → mock fallback.
    """
    return "kis" if is_kis_configured() else "mock"
