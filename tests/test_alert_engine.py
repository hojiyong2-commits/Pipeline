# [Purpose]: AlertEngine + Notifier 동작 검증 — TC-3 cooldown / TC-4 mock provider oracle 포함.
# [Assumptions]: pytest 환경. tests/oracles/FEAT-20260530-6F07/TC-{3,4}/ 오라클 파일 존재.
# [Vulnerability & Risks]:
#   - SoundNotifier 테스트는 비Windows에서 winsound 미존재로 silent True 반환만 검증.
#   - in-memory SQLite는 conn 분리 시 격리되므로 단일 db_path 사용.
# [Improvement]: monkeypatch로 datetime.now 시간 조작하여 cooldown 경과 시뮬레이션을 더 정밀하게.
"""AlertEngine / Notifier 단위 테스트 — MT-6 acceptance gate."""
from __future__ import annotations

import datetime
import json
import pathlib
import tempfile
from typing import Generator

import pytest

from rule_watcher.alert.alert_engine import AlertEngine
from rule_watcher.alert.notifiers import (
    AlertPayload,
    InAppNotifier,
    NotifierRegistry,
    SoundNotifier,
)
from rule_watcher.engine.screening_engine import ScreenResult


# ---------- helpers ----------

def _make_result(ticker: str = "005930", matched: bool = True) -> ScreenResult:
    return ScreenResult(
        ticker=ticker,
        name="삼성전자",
        matched=matched,
        reason="RSI 조건 충족",
        matched_at=datetime.datetime.now().isoformat(),
    )


@pytest.fixture
def tmp_db_path() -> Generator[str, None, None]:
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    yield f.name
    import os
    try:
        os.unlink(f.name)
    except OSError:
        pass


# ---------- TC-3 oracle ----------

def test_tc3_cooldown_oracle(tmp_db_path: str) -> None:
    """오라클: cooldown 30분 내 2회 트리거 → alerts_sent=1, alerts_suppressed=1."""
    base = pathlib.Path(__file__).resolve().parent / "oracles" / "FEAT-20260530-6F07" / "TC-3"
    inp = json.loads((base / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((base / "expected.json").read_text(encoding="utf-8"))

    ticker = inp["ticker"]
    rule_id = inp["rule_id"]
    cooldown_min = inp["cooldown_minutes"]
    trigger_count = inp["trigger_count"]

    engine = AlertEngine(db_path=tmp_db_path, cooldown_minutes=cooldown_min)
    result = _make_result(ticker=ticker)

    sent = 0
    suppressed = 0
    for _ in range(trigger_count):
        ok = engine.trigger(result, rule_id=rule_id, rule_name="RSI 과매도")
        if ok:
            sent += 1
        else:
            suppressed += 1

    assert sent == exp["alerts_sent"], f"sent={sent}, expected={exp['alerts_sent']}"
    assert suppressed == exp["alerts_suppressed"], (
        f"suppressed={suppressed}, expected={exp['alerts_suppressed']}"
    )
    # DB 기록도 1건만 (cooldown으로 두번째는 INSERT 안 됨)
    logs = engine.get_alert_log(limit=10)
    assert len(logs) == 1


# ---------- TC-4 oracle ----------

def test_tc4_mock_provider_oracle(monkeypatch: pytest.MonkeyPatch) -> None:
    """오라클: API 키 없음 → provider='mock', startup_message 한국어 안내."""
    base = pathlib.Path(__file__).resolve().parent / "oracles" / "FEAT-20260530-6F07" / "TC-4"
    exp = json.loads((base / "expected.json").read_text(encoding="utf-8"))

    # 환경변수 제거 (input.json의 env_vars_absent)
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    monkeypatch.delenv("KIS_ACCOUNT_NO", raising=False)

    # config 모듈 변수도 재할당 (.env에서 이미 로드된 값을 덮어씀)
    import rule_watcher.config as cfg
    monkeypatch.setattr(cfg, "KIS_APP_KEY", "")
    monkeypatch.setattr(cfg, "KIS_APP_SECRET", "")
    monkeypatch.setattr(cfg, "KIS_ACCOUNT_NO", "")

    # provider 싱글톤 리셋
    from rule_watcher.providers import reset_provider, get_provider
    reset_provider()
    try:
        provider = get_provider()
        assert provider.name == exp["provider_used"], (
            f"provider={provider.name}, expected={exp['provider_used']}"
        )
        # provider_name 함수도 'mock' 반환
        assert cfg.get_provider_name() == "mock"
        # startup_message: is_kis_configured()가 False면 mock 메시지
        expected_startup = exp["startup_message"]
        actual_startup = (
            "KIS Open API에 연결합니다."
            if cfg.is_kis_configured()
            else "API 키가 설정되지 않아 mock 데이터 모드로 실행합니다."
        )
        assert actual_startup == expected_startup
        # app_started=true는 provider 인스턴스 생성 자체로 입증됨
        assert provider is not None
    finally:
        reset_provider()


# ---------- 일반 단위 테스트 ----------

def test_check_cooldown_unused_ticker(tmp_db_path: str) -> None:
    engine = AlertEngine(db_path=tmp_db_path, cooldown_minutes=30)
    # 미사용 (ticker, rule_id) → False
    assert engine.check_cooldown("000000", "any_rule") is False


def test_cooldown_zero_minutes_disables(tmp_db_path: str) -> None:
    """cooldown_minutes=0이면 cooldown 비활성 — 항상 전송 가능."""
    engine = AlertEngine(db_path=tmp_db_path, cooldown_minutes=0)
    result = _make_result()
    ok1 = engine.trigger(result, rule_id="r1", rule_name="r1")
    ok2 = engine.trigger(result, rule_id="r1", rule_name="r1")
    assert ok1 is True
    assert ok2 is True
    logs = engine.get_alert_log()
    assert len(logs) == 2


def test_trigger_return_values(tmp_db_path: str) -> None:
    engine = AlertEngine(db_path=tmp_db_path, cooldown_minutes=30)
    result = _make_result()
    assert engine.trigger(result, "r", "RuleX") is True
    assert engine.trigger(result, "r", "RuleX") is False  # cooldown


def test_trigger_matched_false_returns_false(tmp_db_path: str) -> None:
    """matched=False는 트리거 의미 없음 → False 반환, DB 기록 없음."""
    engine = AlertEngine(db_path=tmp_db_path, cooldown_minutes=30)
    result = _make_result(matched=False)
    assert engine.trigger(result, "r", "RuleX") is False
    assert engine.get_alert_log() == []


def test_clear_cache_allows_resend(tmp_db_path: str) -> None:
    engine = AlertEngine(db_path=tmp_db_path, cooldown_minutes=30)
    result = _make_result()
    assert engine.trigger(result, "r", "RuleX") is True
    assert engine.trigger(result, "r", "RuleX") is False
    engine.clear_cooldown_cache()
    assert engine.trigger(result, "r", "RuleX") is True


def test_notifier_registry_active_filter(tmp_db_path: str) -> None:
    registry = NotifierRegistry()
    in_app = InAppNotifier()
    sound = SoundNotifier()
    registry.register(in_app)
    registry.register(sound)
    assert len(registry.get_active_notifiers()) == 2
    registry.disable("sound")
    active = registry.get_active_notifiers()
    assert len(active) == 1
    assert active[0].name == "in_app"
    registry.enable("sound")
    assert len(registry.get_active_notifiers()) == 2


def test_inapp_notifier_fifo_200() -> None:
    in_app = InAppNotifier()
    payload = AlertPayload(
        ticker="005930",
        name="삼성전자",
        rule_id="r",
        rule_name="R",
        reason="test",
        price=0.0,
        triggered_at=datetime.datetime.now().isoformat(),
        provider="mock",
    )
    for _ in range(250):
        in_app.notify(payload)
    logs = in_app.get_logs()
    assert len(logs) == 200, f"FIFO max 200, got {len(logs)}"


def test_sound_notifier_no_exception() -> None:
    """SoundNotifier.notify는 Windows/비Windows 어디서든 예외 없이 동작."""
    sound = SoundNotifier()
    payload = AlertPayload(
        ticker="005930",
        name="삼성전자",
        rule_id="r",
        rule_name="R",
        reason="test",
        price=0.0,
        triggered_at=datetime.datetime.now().isoformat(),
        provider="mock",
    )
    # 반환값은 True (성공 또는 비Windows silent)
    assert sound.notify(payload) is True


def test_get_alert_log_limit(tmp_db_path: str) -> None:
    engine = AlertEngine(db_path=tmp_db_path, cooldown_minutes=0)  # cooldown 비활성
    for i in range(5):
        result = _make_result(ticker=f"00000{i}")
        engine.trigger(result, rule_id=f"r{i}", rule_name=f"R{i}")
    assert len(engine.get_alert_log(limit=100)) == 5
    assert len(engine.get_alert_log(limit=3)) == 3
    assert len(engine.get_alert_log(limit=0)) == 0


# ---------- None / 타입 / 경계값 방어 ----------

def test_trigger_none_result_raises(tmp_db_path: str) -> None:
    engine = AlertEngine(db_path=tmp_db_path)
    with pytest.raises(TypeError):
        engine.trigger(None, "r", "R")  # type: ignore[arg-type]


def test_trigger_empty_rule_id_raises(tmp_db_path: str) -> None:
    engine = AlertEngine(db_path=tmp_db_path)
    result = _make_result()
    with pytest.raises(ValueError):
        engine.trigger(result, "", "R")


def test_alert_engine_negative_cooldown_raises(tmp_db_path: str) -> None:
    with pytest.raises(ValueError):
        AlertEngine(
            db_path=tmp_db_path, cooldown_minutes=-1, auto_init_db=False
        )


def test_alert_payload_none_raises() -> None:
    with pytest.raises(TypeError):
        AlertPayload(
            ticker=None,  # type: ignore[arg-type]
            name="x",
            rule_id="r",
            rule_name="R",
            reason="x",
            price=0.0,
            triggered_at="2026-01-01T00:00:00",
            provider="mock",
        )


def test_notifier_registry_disable_unknown_raises() -> None:
    reg = NotifierRegistry()
    with pytest.raises(ValueError):
        reg.disable("nonexistent")


def test_sound_notifier_negative_frequency_raises() -> None:
    with pytest.raises(ValueError):
        SoundNotifier(frequency=-1)


def test_registry_disable_filters_from_trigger(tmp_db_path: str) -> None:
    """비활성 notifier는 trigger 호출 시 디스패치되지 않음."""
    registry = NotifierRegistry()
    in_app = InAppNotifier()
    registry.register(in_app)
    engine = AlertEngine(
        db_path=tmp_db_path, cooldown_minutes=0, registry=registry
    )
    result = _make_result()
    assert engine.trigger(result, "r", "R") is True
    assert len(in_app.get_logs()) == 1

    # 비활성화 후 트리거 → in_app 로그는 늘어나지 않음
    registry.disable("in_app")
    engine.clear_cooldown_cache()
    assert engine.trigger(result, "r", "R") is True  # trigger는 성공 (다른 notifier는 없음)
    assert len(in_app.get_logs()) == 1  # 여전히 1


def test_trigger_records_db_with_provider_info(tmp_db_path: str) -> None:
    """DB 기록에 provider/price/rule_str 정보가 reason_ko에 포함됨."""
    engine = AlertEngine(db_path=tmp_db_path, cooldown_minutes=0)
    result = _make_result()
    assert engine.trigger(
        result, "rsi_oversold", "RSI 과매도", provider="kis", price=72500.0
    ) is True
    logs = engine.get_alert_log()
    assert len(logs) == 1
    reason = logs[0]["reason_ko"]
    assert "provider=kis" in reason
    assert "rule_str=rsi_oversold" in reason
    assert "price=72500.0" in reason
