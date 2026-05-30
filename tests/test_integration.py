# [Purpose]: mock provider + 룰북 + 알림 엔진의 end-to-end 통합 동작을 검증한다.
# [Assumptions]: rule_watcher 패키지는 import 가능. 외부 API 호출은 mock provider로 차단.
# [Vulnerability & Risks]:
#   - 테스트 시 :memory: SQLite 사용으로 디스크 영향 없음.
#   - 환경변수 KIS_APP_KEY/SECRET를 임시로 pop/restore하므로 동시 실행 환경에서는 격리에 주의.
# [Improvement]: pytest fixture로 환경변수 격리 자동화, 더 다양한 룰북 시나리오 추가.
"""통합 테스트 — mock provider + 룰북 + 알림 엔진 end-to-end."""
from __future__ import annotations

import datetime
import json
import pathlib

# 외부 의존성: pytest는 테스트 실행 도구이며 import-time side-effect 없음
import pytest  # noqa: F401 — pytest collection 유지 목적


def test_full_pipeline_rsi_oversold() -> None:
    """전체 파이프라인: mock OHLCV → RSI 조건 → ScreeningEngine."""
    from rule_watcher.providers.mock_provider import MockProvider
    from rule_watcher.engine.rule_model import RuleBook, RuleGroup, RuleCondition
    from rule_watcher.engine.screening_engine import ScreeningEngine

    provider = MockProvider()
    df = provider.get_ohlcv("005930", period=60)
    assert not df.empty, "MockProvider가 빈 DataFrame을 반환했습니다"

    # RSI < 100 (항상 통과 가능한 조건) — 통합 흐름이 끝까지 도는지만 확인
    rulebook = RuleBook(
        name="통합테스트",
        groups=[
            RuleGroup(
                name="그룹1",
                conditions=[
                    RuleCondition(
                        indicator="RSI",
                        operator="<",
                        threshold=100.0,
                        params={"period": 14},
                    )
                ],
            )
        ],
    )

    engine = ScreeningEngine(rulebook)
    results = engine.screen_watchlist(
        [{"ticker": "005930", "name": "삼성전자", "df": df}]
    )
    matched = [r for r in results if r.matched]
    assert len(matched) == 1, f"matched 1개여야 함, got {len(matched)}"
    assert matched[0].ticker == "005930"
    assert matched[0].reason, "한국어 reason이 비어 있습니다"


def test_alert_engine_integration(tmp_path) -> None:
    """AlertEngine → DB → 알림 로그 기록 통합 흐름.

    NOTE: SQLite ':memory:' 는 connection 마다 독립된 DB이므로 통합 테스트에는
    파일 기반 임시 DB를 사용해야 한다. AlertEngine 의 db_conn() 컨텍스트 매니저가
    매 호출 시 새 connection 을 열기 때문이다.
    """
    from rule_watcher.engine.screening_engine import ScreenResult
    from rule_watcher.alert.alert_engine import AlertEngine

    db_path = str(tmp_path / "alert_integration.db")
    # auto_init_db=True (기본값) — AlertEngine 이 알아서 스키마 생성
    engine = AlertEngine(db_path=db_path, cooldown_minutes=0)

    result = ScreenResult(
        ticker="005930",
        name="삼성전자",
        matched=True,
        reason="RSI 과매도",
        matched_at=datetime.datetime.now().isoformat(),
    )
    ok = engine.trigger(result, "rsi_test", "RSI 테스트")
    assert ok is True, f"trigger 결과는 True여야 함, got {ok}"

    logs = engine.get_alert_log(limit=10)
    assert len(logs) >= 1, f"DB 로그 최소 1건 필요, got {len(logs)}"
    assert logs[0]["ticker"] == "005930"


def test_sample_rulebooks_loadable() -> None:
    """샘플 룰북 3개 모두 JSON 파싱 + validate 통과."""
    from rule_watcher.engine.rule_model import RuleBook

    samples_dir = pathlib.Path(__file__).parent.parent / "sample_rulebooks"
    expected = ["oversold_rebound.json", "trend_breakout.json", "volume_surge.json"]
    for fname in expected:
        path = samples_dir / fname
        assert path.exists(), f"샘플 룰북 파일 없음: {path}"
        data = json.loads(path.read_text(encoding="utf-8"))
        rb = RuleBook.from_dict(data)
        rb.validate()
        assert rb.name, "룰북 이름이 비어 있음"
        assert len(rb.groups) > 0, "그룹이 비어 있음"
        for grp in rb.groups:
            assert len(grp.conditions) > 0, f"그룹 {grp.name} 조건 비어 있음"


def test_no_api_key_mock_provider() -> None:
    """KIS API 키 없음 → get_provider()가 mock으로 fallback."""
    import os
    from rule_watcher.providers.data_service import get_provider, reset_provider

    # 환경변수 백업 후 제거
    original_key = os.environ.pop("KIS_APP_KEY", None)
    original_secret = os.environ.pop("KIS_APP_SECRET", None)
    # config 모듈 캐시된 값도 우회하려면 reset_provider만으로 충분
    # (is_kis_configured는 os.getenv를 직접 호출하지 않고 모듈 상수를 참조하므로
    #  실제 KIS 키가 모듈 import 시점에 비어 있었다면 mock fallback 자동 발생)
    reset_provider()

    try:
        # 모듈 상수가 이미 빈 문자열일 가능성이 높으므로 추가로 직접 검증
        from rule_watcher import config as _cfg

        # config 모듈 상수까지 빈 값으로 강제
        original_module_key = _cfg.KIS_APP_KEY
        original_module_secret = _cfg.KIS_APP_SECRET
        _cfg.KIS_APP_KEY = ""
        _cfg.KIS_APP_SECRET = ""
        try:
            provider = get_provider()
            assert provider.name == "mock", (
                f"provider.name은 'mock'이어야 함, got {provider.name}"
            )
            df = provider.get_ohlcv("005930", period=30)
            assert not df.empty, "mock provider OHLCV가 비어 있음"
        finally:
            _cfg.KIS_APP_KEY = original_module_key
            _cfg.KIS_APP_SECRET = original_module_secret
    finally:
        if original_key is not None:
            os.environ["KIS_APP_KEY"] = original_key
        if original_secret is not None:
            os.environ["KIS_APP_SECRET"] = original_secret
        reset_provider()


def test_rulebook_serialize_and_screen() -> None:
    """룰북 직렬화 → 역직렬화 → 스크리닝 실행 round-trip."""
    from rule_watcher.engine.rule_model import RuleBook, RuleGroup, RuleCondition
    from rule_watcher.engine.screening_engine import ScreeningEngine
    from rule_watcher.providers.mock_provider import MockProvider

    rb = RuleBook(
        name="직렬화테스트",
        groups=[
            RuleGroup(
                name="g1",
                conditions=[
                    RuleCondition(
                        indicator="RSI",
                        operator="<",
                        threshold=100.0,
                        params={"period": 14},
                    )
                ],
            )
        ],
    )
    serialized = rb.serialize()
    assert isinstance(serialized, str) and len(serialized) > 0

    restored = RuleBook.deserialize(serialized)
    assert restored.name == rb.name

    provider = MockProvider()
    df = provider.get_ohlcv("000660", 60)
    engine = ScreeningEngine(restored)
    results = engine.screen_watchlist(
        [{"ticker": "000660", "name": "SK하이닉스", "df": df}]
    )
    assert any(r.matched for r in results), "RSI<100 조건은 반드시 매칭되어야 함"


if __name__ == "__main__":
    # 자가 검증 — pytest 없이도 핵심 흐름 실행 (tmp_path는 수동 생성)
    import sys as _sys
    import tempfile as _tempfile

    # repo root를 sys.path에 추가 (직접 실행 시 rule_watcher import 가능하도록)
    _repo_root = str(pathlib.Path(__file__).parent.parent.resolve())
    if _repo_root not in _sys.path:
        _sys.path.insert(0, _repo_root)

    test_full_pipeline_rsi_oversold()
    with _tempfile.TemporaryDirectory() as _td:
        test_alert_engine_integration(pathlib.Path(_td))
    test_sample_rulebooks_loadable()
    test_no_api_key_mock_provider()
    test_rulebook_serialize_and_screen()
    print("[SELF-VERIFY] test_integration.py OK")
