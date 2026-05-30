# 한국주식 Rule Watcher v1.0

룰 기반 종목 감시 앱입니다. 매수/매도 추천이 아닙니다.
내가 정한 투자 규칙에 맞는 종목을 찾아 알림을 줍니다.

## 기능

- 관심종목 등록/관리
- 룰북 조건 설정 (블록 조립식)
- 조건 충족 종목 자동 선별
- 앱 내부 알림 + 소리 알림
- SQLite 기반 데이터 저장
- API 키 없이 mock 데이터로 테스트 가능

## 설치

```
pip install -r requirements.txt
```

## 실행

```
streamlit run main.py
```

(API 키 없으면 mock 모드 자동 실행)

## KIS Open API 연결 (선택)

1. `.env.example`을 `.env`로 복사
2. KIS Open API에서 발급받은 키 입력

```
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_ACCOUNT_NO=...
```

키가 비어 있으면 자동으로 mock 데이터 모드로 전환됩니다.

## 샘플 룰북

`sample_rulebooks/` 폴더에 3개 제공:

| 파일 | 설명 |
|---|---|
| `oversold_rebound.json` | RSI < 30 + ADX > 20 (과매도 반등 후보) |
| `trend_breakout.json` | MACD 골든크로스 + RSI > 40 (추세 돌파 후보) |
| `volume_surge.json` | 거래량 이동평균 비율 > 2 + RSI > 50 (거래량 급증 후보) |

앱에서 룰북 탭 → "JSON 가져오기"로 로드할 수 있습니다.

## 지원 인디케이터 (v1.0)

RSI, MACD_CROSS, SMA, EMA, BOLLINGER_UPPER/LOWER, ATR, ADX, STOCH_K,
STOCH_RSI_K, CCI, ROC, VOLUME_MA_RATIO, OBV, MFI, VWAP, PRICE,
CHANGE_RATE, PREV_HIGH_BREAK, NEAR_HIGH, MA_CROSS_UP/DOWN, GAP_UP/DOWN.

## 데이터 저장 위치

- SQLite DB: `~/rule_watcher.db` (환경변수 `RULE_WATCHER_DB`로 변경 가능)
- 로그 파일: `rule_watcher.log` (실행 폴더)

## 테스트

```
pytest tests/test_integration.py -v
pytest tests/e2e/test_rule_watcher_e2e.py -v
```

## 주의사항

이 앱은 종목 분석 도구입니다.
자동매매, 매수/매도 추천, 미래 수익률 예측 기능이 없습니다.
투자 결정은 본인의 판단으로 하세요.

## 라이선스

내부 사용 전용.
