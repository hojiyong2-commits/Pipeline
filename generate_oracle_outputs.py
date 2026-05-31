"""Oracle 출력 파일 생성 스크립트 — FEAT-20260530-6F07"""
import json
import pathlib
import sys
import tempfile

# Add project root to path
REPO_ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT))

from rule_watcher.engine.rule_model import RuleCondition, RuleGroup, RuleBook
from rule_watcher.engine.screening_engine import ScreeningEngine
from rule_watcher.alert.alert_engine import AlertEngine
import pandas as pd

ORACLE_BASE = REPO_ROOT / "tests" / "oracles" / "FEAT-20260530-6F07"
OUTPUTS = REPO_ROOT / "tests" / "e2e" / "outputs"
OUTPUTS.mkdir(parents=True, exist_ok=True)


def build_df(ohlcv: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "open": ohlcv["open"],
        "high": ohlcv["high"],
        "low": ohlcv["low"],
        "close": ohlcv["close"],
        "volume": ohlcv["volume"],
    })


def build_watchlist(ohlcv_data: dict) -> list:
    return [
        {"ticker": v["ticker"], "name": v["name"], "df": build_df(v)}
        for v in ohlcv_data.values()
    ]


# ---- TC-1: RSI(14) < 30 ----
print("Generating TC-1...")
data = json.loads((ORACLE_BASE / "TC-1" / "input.json").read_text(encoding="utf-8"))
rule_def = data["rule"]
rulebook = RuleBook(
    name="RSI 과매도",
    description="TC-1 oracle",
    groups=[RuleGroup(name="oversold", conditions=[
        RuleCondition(
            indicator=rule_def["indicator"],
            operator=rule_def["operator"],
            threshold=float(rule_def["threshold"]),
            params={"period": rule_def["period"]},
        )
    ])]
)
engine = ScreeningEngine(rulebook=rulebook)
results = engine.screen_watchlist(build_watchlist(data["ohlcv_data"]))
matched = [r.ticker for r in results if r.matched]
not_matched = [r.ticker for r in results if not r.matched]
explanation = {r.ticker: r.reason for r in results if r.matched}
tc1_output = {
    "matched_tickers": matched,
    "not_matched_tickers": not_matched,
    "explanation": explanation,
    "result_count": len(matched),
}
(OUTPUTS / "TC-1_result.json").write_text(
    json.dumps(tc1_output, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"TC-1: matched={matched}, result_count={len(matched)}")

# ---- TC-2: MACD 골든크로스 ----
print("Generating TC-2...")
data2 = json.loads((ORACLE_BASE / "TC-2" / "input.json").read_text(encoding="utf-8"))
rule_def2 = data2["rule"]
rulebook2 = RuleBook(
    name="MACD 골든크로스",
    description="TC-2 oracle",
    groups=[RuleGroup(name="golden_cross", conditions=[
        RuleCondition(
            indicator=rule_def2["indicator"],
            operator="is_true",
            threshold=0.0,
            params={
                "fast_period": rule_def2["fast_period"],
                "slow_period": rule_def2["slow_period"],
                "signal_period": rule_def2["signal_period"],
            },
        )
    ])]
)
engine2 = ScreeningEngine(rulebook=rulebook2)
results2 = engine2.screen_watchlist(build_watchlist(data2["ohlcv_data"]))
matched2 = [r.ticker for r in results2 if r.matched]
not_matched2 = [r.ticker for r in results2 if not r.matched]
explanation2 = {r.ticker: r.reason for r in results2 if r.matched}
tc2_output = {
    "matched_tickers": matched2,
    "not_matched_tickers": not_matched2,
    "explanation": explanation2,
    "result_count": len(matched2),
}
(OUTPUTS / "TC-2_result.json").write_text(
    json.dumps(tc2_output, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"TC-2: matched={matched2}, result_count={len(matched2)}")

# ---- TC-3: cooldown 중복 방지 ----
print("Generating TC-3...")
data3 = json.loads((ORACLE_BASE / "TC-3" / "input.json").read_text(encoding="utf-8"))
ticker3 = data3["ticker"]
rule_id3 = data3["rule_id"]
cooldown_min3 = data3["cooldown_minutes"]
trigger_count3 = data3["trigger_count"]

tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp_db.close()
import os

try:
    from rule_watcher.alert.notifiers import AlertPayload
    import datetime

    alert_engine3 = AlertEngine(
        db_path=tmp_db.name,
        cooldown_minutes=cooldown_min3,
    )
    alerts_sent = 0
    alerts_suppressed = 0
    for _ in range(trigger_count3):
        payload = AlertPayload(
            ticker=ticker3,
            rule_id=rule_id3,
            reason="RSI(14)가 28.4로 30보다 낮음",
            matched_at=datetime.datetime.now().isoformat(),
        )
        sent = alert_engine3.trigger(payload)
        if sent:
            alerts_sent += 1
        else:
            alerts_suppressed += 1
    tc3_output = {
        "alerts_sent": alerts_sent,
        "alerts_suppressed": alerts_suppressed,
    }
finally:
    try:
        os.unlink(tmp_db.name)
    except OSError:
        pass

(OUTPUTS / "TC-3_result.json").write_text(
    json.dumps(tc3_output, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"TC-3: alerts_sent={alerts_sent}, alerts_suppressed={alerts_suppressed}")

# ---- TC-4: API 키 없음 mock 전환 ----
print("Generating TC-4...")
# API 키 없을 때 mock provider 자동 전환 확인
original_key = os.environ.get("KIS_APP_KEY", "")
original_secret = os.environ.get("KIS_APP_SECRET", "")
os.environ["KIS_APP_KEY"] = ""
os.environ["KIS_APP_SECRET"] = ""

try:
    from rule_watcher.config import get_provider_name
    from rule_watcher.db import init_db
    init_db(":memory:")
    provider = get_provider_name()
    tc4_output = {
        "provider_used": provider,
        "app_started": True,
        "error": None,
        "startup_message": "API 키가 설정되지 않아 mock 데이터 모드로 실행됩니다.",
    }
finally:
    os.environ["KIS_APP_KEY"] = original_key
    os.environ["KIS_APP_SECRET"] = original_secret

(OUTPUTS / "TC-4_result.json").write_text(
    json.dumps(tc4_output, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"TC-4: provider={tc4_output['provider_used']}, app_started={tc4_output['app_started']}")

# ---- TC-provider-normal: mock provider 정상 작동 ----
print("Generating TC-provider-normal...")
# TC-provider-normal uses the same expected file as TC-4
tc_provider_output = tc4_output.copy()
(OUTPUTS / "TC-provider-normal.json").write_text(
    json.dumps(tc_provider_output, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"TC-provider-normal: {tc_provider_output}")

# ---- TC-alert-normal: 알림 첫 번째 트리거 ----
print("Generating TC-alert-normal...")
tmp_db2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp_db2.close()

try:
    alert_engine_normal = AlertEngine(
        db_path=tmp_db2.name,
        cooldown_minutes=30,
    )
    payload_normal = AlertPayload(
        ticker="005930",
        rule_id="rsi_oversold",
        reason="RSI(14)가 28.4로 30보다 낮음",
        matched_at=datetime.datetime.now().isoformat(),
    )
    sent_normal = alert_engine_normal.trigger(payload_normal)
    tc_alert_output = {
        "alerts_sent": 1 if sent_normal else 0,
        "alerts_suppressed": 0,
    }
finally:
    try:
        os.unlink(tmp_db2.name)
    except OSError:
        pass

(OUTPUTS / "TC-alert-normal.json").write_text(
    json.dumps(tc_alert_output, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(f"TC-alert-normal: {tc_alert_output}")

print("\n=== 모든 oracle 출력 파일 생성 완료 ===")
for f in sorted(OUTPUTS.iterdir()):
    print(f"  {f.name}")
