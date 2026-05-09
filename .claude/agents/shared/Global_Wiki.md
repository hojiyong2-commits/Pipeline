# Global_Wiki.md — Shared Reference for All Agents

## Read First: Mandatory Pipeline

`/Task` always uses Three-Gate + Option A phase attestation + Incremental Module Gate. Classic mode, numeric Harness completion, BUILD+QA final scoring, and `pipeline.py harness --score ...` are not valid completion paths.

Completion requires phase attestations PASS, all `MT-N` module gates PASS, `module integrate` PASS, Technical PASS, Oracle PASS, GitHub CI PASS, and User Acceptance ACCEPT with real result evidence.

## Python Version (PM-Specified)

PM Agent가 분석하여 프로젝트에 최적인 Python 버전(3.9 또는 3.10)을 지정합니다.

**Python 3.9 모드 (PM 지정 시):**
- Mandatory: `from typing import Optional, Union, List, Dict, Tuple` — 소문자 제네릭 금지
- Forbidden (instant 0pt): `match/case`, `int|str` union, `list[int]`/`dict[str,int]` 소문자 제네릭, `from __future__ import annotations` (PyInstaller 위험), `tomllib`, `asyncio.TaskGroup`, `ExceptionGroup`

**Python 3.10 모드 (PM 지정 시):**
- Allowed: `list[int]`, `str | None` (PEP 604), `match/case` 사용 가능
- Mandatory: `from __future__ import annotations` 없이도 PEP 585 적용

## MVC Structure
```
project/
├── core/       # Business logic — zero UI imports
│   └── logic.py, models.py, utils.py
├── ui/
│   └── app.py  # UI only — imports core
├── main.py
└── requirements.txt
```
Forbidden: business logic in `app.py`; UI imports in `core/`; global state (use class/dataclass).

## File Encoding 4-Step Fallback
```python
for enc in ["utf-8", "cp949", "euc-kr", "latin-1"]:
    try:
        text = Path(filepath).read_text(encoding=enc); break
    except UnicodeDecodeError:
        continue
```
`encoding="utf-8"` alone is forbidden on any text file read. Use `safe_read_file()` or the loop above.

## sys._MEIPASS Path Helper
```python
import sys
from pathlib import Path
def get_base_path() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent
```

## WA Category — 4 Mandatory Items (SSoT)

> **이 섹션이 WA 4항목의 Single Source of Truth(SSoT)입니다.** dev-agent.md / qa-agent.md / test-harness-agent.md / pm-agent.md는 모두 이 섹션을 참조합니다.

1. `timeout=(connect_timeout, read_timeout)` tuple on every `requests` call (e.g. `timeout=(5, 30)`)
2. `urllib3.util.retry.Retry` with `total=3`, `backoff_factor`, `status_forcelist` OR manual 3-retry loop
3. Separate `except` blocks for: `Timeout`, `ConnectionError`, `HTTPError` (4xx/5xx), `RequestException`
4. JSON parse fallback (`except ValueError` around `response.json()`)

Missing any one → Robustness -4pt.

## `<handover>` XML Contract Template
```xml
<handover>
  <from>[source-agent]</from>
  <to>[target-agent]</to>
  <step_id>[pipeline_id]-S[N]</step_id>
  <evidence>
    <file>core/[module].py</file>
    <file>ui/app.py</file>
  </evidence>
  <status>READY_FOR_QA</status>
</handover>
```
QA rejects any handover missing `<evidence>` with real file paths.

## Pipeline Gate Commands (Quick Reference)
| Phase | Check | Record |
|---|---|---|
| pm | `python pipeline.py check --phase pm` | `python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --agent-run-id <run_id>` + `gates prepare-phase --phase pm` + `gates phase-ci --phase pm --repo hojiyong2-commits/Pipeline` |
| module | `python pipeline.py module status` | `module design -> module dev -> module qa` for each `MT-N`, then `module integrate --result PASS --report-file integration_report.xml` |
| dev | `python pipeline.py check --phase dev` | `python pipeline.py done --phase dev --files "..." --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <run_id>` + phase attestation |
| qa | `python pipeline.py check --phase qa` | `python pipeline.py qa --result PASS --numeric-score N --report-file qa_report.xml --agent-run-id <run_id>` or `--result FAIL --numeric-score N --failure-sig "[category]:[hash]" --report-file qa_report.xml` |
| sec | `python pipeline.py check --phase sec` | `python pipeline.py sec --result PASS --risk LOW` or `--skip` |
| build | `python pipeline.py check --phase build` | `python pipeline.py build --exe "dist/app.exe" --report-file dist/build_report.xml --agent-run-id <run_id>` or `--exe "N/A" --skip-reason "meta-task" --user-confirmed --agent-run-id <run_id>` + phase attestation |
| external gates | `python pipeline.py gates status` | `gates technical`, `gates oracle --user-confirmed`, `gates github-ci --repo hojiyong2-commits/Pipeline`, `gates accept --result ACCEPT --evidence <real-result> --user-confirmed` |

**Legacy harness diagnostic:** `<test_code>` CDATA/strict unittest evidence rules are retained only for old harness diagnostic regression tests. New `/Task` completion uses external gates and phase/module attestations.
| architect | `python pipeline.py check --phase architect` | `python pipeline.py architect --report-file architect_report.xml` |

## `/task` Command — Discovery Gate (코딩 전 필수)

PM Agent는 `/task` 수신 즉시 파이프라인(Phase 1 step_plan 포함)을 시작하지 않는다.
반드시 아래 **Discovery Report**를 먼저 출력하고 사용자의 명시적 승인을 받아야 한다.

### Discovery Report 필수 구성 (5항목)
1. **문제 정의** — 무엇을 해결하는가, 핵심 제약 조건
2. **해결 방향 2~3가지** — 각 방향의 접근법 + 트레이드오프 한 줄 요약
3. **추천 방향 및 근거** — 왜 이 방향이 최적인가
4. **예상 파이프라인** — Phase별 에이전트 배정 + 각 에이전트의 예상 결과물
5. **리스크/불명확 항목** — 사용자에게 사전 확인이 필요한 항목 (경로, 컬럼명, 시트명 등)

### Gate 조건
- 사용자가 "진행", "OK", "승인" 등 명시적 동의를 표현해야만 Phase 1(step_plan) 진입
- 사용자가 방향을 수정하면 Discovery Report를 재출력
- "간단한 태스크", "빠르게 해줘", "한 줄 fix" 등 어떠한 사유로도 Discovery 생략 금지
- step_plan 발행은 Discovery Report 승인 후에만 허용

---

## Async-First Pattern
모든 I/O 작업은 `asyncio` 기반 비동기 논블로킹으로 작성합니다.
```python
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def fetch_data(url: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            return await resp.json()
```

## Mandatory Logging
모든 실행 프로세스는 `app.log`에 실시간 기록하도록 설계합니다. (채점: dev-agent.md의 Golden Rules 및 QA numeric 인계 정책 참조)
```python
import logging
logging.basicConfig(
    filename="app.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)
```

## Mandatory Pipeline Loop (실제 실행 순서)
1. **[PM]** `<step_plan>` 발행, oracle/test ownership 확정, phase receipt 기록
2. **[Module Gate]** 각 `MT-N`에 대해 design → dev → qa 순차 PASS
3. **[Dev]** 최종 handover + `scope_manifest.json` 기록, Dev phase attestation PASS
4. **[QA]** 논리/범위/실행 증거 검증, QA phase attestation PASS
5. **[Security]** (DB/네트워크 포함 시) 보안 감사 및 remediation_code 제공
6. **[Build]** PyInstaller 단일 EXE 빌드 또는 N/A 기록, Build phase attestation PASS
7. **[External Gates]** Technical → Oracle → GitHub CI → User Acceptance
8. **[Architect]** 외부 게이트/RCA/프로토콜 결함 여부 진단 후 COMPLETE 또는 별도 IMP 권고

## Tier 2 Pattern Library

### Async+Scheduler Pattern
**When to use:** Tkinter/PyQt5 UI에서 asyncio 코루틴 호출 시 GUI 스레드 충돌 방지.

```python
import asyncio
import threading
from typing import Any, Coroutine

def run_async_in_thread(coro: Coroutine[Any, Any, Any]) -> None:
    """별도 스레드에서 asyncio 이벤트 루프를 실행합니다."""
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_until_complete, args=(coro,), daemon=True)
    t.start()
```

**Known pitfalls:**
- GUI 이벤트 루프와 asyncio 루프를 동일 스레드에서 실행하면 블로킹 발생
- `asyncio.run()` 직접 호출 금지 — 실행 중 루프와 충돌
- 결과값 반환 필요 시 `loop.run_until_complete()` 반환값 캡처

---

### Dynamic Page Scraping Pattern (Playwright)
**When to use:** JavaScript 렌더링 후 DOM이 변경되는 동적 페이지 스크래핑.
**사전 실행 필수:** `playwright install chromium`

```python
import asyncio
from typing import Optional
from playwright.async_api import async_playwright

async def scrape_dynamic(url: str, selector: Optional[str] = None, timeout: int = 30000) -> str:
    """동적 페이지 렌더링 후 HTML 또는 특정 요소 텍스트를 반환합니다."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=timeout)
        if selector:
            await page.wait_for_selector(selector, timeout=timeout)
            content = await page.inner_text(selector)
        else:
            content = await page.content()
        await browser.close()
        return content
```

**Known pitfalls:**
- CSS 셀렉터 대신 `data-testid` 속성 기반 셀렉터 사용 권장
- `wait_until="networkidle"` 대신 `wait_for_selector` 사용이 더 정밀
- 로그인 세션 필요 시 `browser.new_context(storage_state="auth.json")` 활용

---

### RPA Image-Based Click Pattern
**When to use:** IFS/ERP 등 Win32 앱 화면 자동화. 좌표 하드코딩 절대 금지.

```python
import time
import logging
from pathlib import Path
from typing import Optional
import pyautogui

logger = logging.getLogger(__name__)

def click_image(
    template_path: str,
    confidence: float = 0.85,
    timeout: int = 10,
    offset_x: int = 0,
    offset_y: int = 0
) -> bool:
    """이미지를 화면에서 찾아 클릭합니다. 좌표 하드코딩 없음."""
    if not Path(template_path).exists():
        raise FileNotFoundError(f"템플릿 이미지 없음: {template_path}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        loc = pyautogui.locateOnScreen(template_path, confidence=confidence)
        if loc:
            center = pyautogui.center(loc)
            pyautogui.click(center.x + offset_x, center.y + offset_y)
            logger.info(f"클릭 성공: {template_path} @ {center}")
            return True
        time.sleep(0.5)
    logger.warning(f"클릭 실패 (타임아웃): {template_path}")
    return False
```

**Known pitfalls:**
- `confidence`는 OS/해상도/DPI에 따라 0.70~0.95 조정 필요
- `pyautogui.FAILSAFE = True` 설정 권장 (마우스 좌상단 이동 시 긴급 중단)
- 고DPI(4K) 환경 좌표 스케일 불일치 → `pyautogui.screenshot()`으로 먼저 확인

---

### ETL Schema Validation Pattern
**When to use:** 엑셀/CSV 데이터 읽어 DB/API 전송 전 스키마 검증.

```python
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

@dataclass
class RowSchema:
    date: str
    value: float
    memo: Optional[str] = None

def validate_row(row: Dict[str, Any], required_cols: Optional[List[str]] = None) -> RowSchema:
    """행 데이터의 필수 컬럼 존재 및 타입을 검증합니다."""
    if required_cols is None:
        required_cols = ["date", "value"]
    missing = set(required_cols) - set(row.keys())
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")
    try:
        return RowSchema(
            date=str(row["date"]).strip(),
            value=float(row["value"]),
            memo=str(row["memo"]).strip() if row.get("memo") else None
        )
    except (TypeError, ValueError) as e:
        raise ValueError(f"타입 변환 실패: {e}") from e
```

**Known pitfalls:**
- 헤더 없는 엑셀: `pd.read_excel(header=None)` 후 컬럼 직접 명명
- 날짜 컬럼이 datetime 객체일 경우 `.strftime("%Y-%m-%d")` 변환
- 빈 행 필터링: `df.dropna(how="all")` 먼저 적용

## Tier 2 High-Reliability Patterns

### qasync PyQt Bridge (Race Condition 제거)
**When to use:** PyQt5/PySide2에서 asyncio 코루틴을 UI와 통합할 때. `threading` 브릿지보다 안전.

```python
import qasync
import asyncio
from typing import Callable, Any
from PyQt5.QtWidgets import QApplication

def run_app_with_async(setup_fn: Callable[[], Any]) -> None:
    """qasync로 PyQt5 + asyncio 통합 실행."""
    app = QApplication([])
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        loop.run_until_complete(setup_fn())
        loop.run_forever()
```

**Known pitfalls:**
- `pip install qasync` 사전 설치 필수
- `asyncio.run()` 와 혼용 금지 — qasync가 loop를 직접 관리
- Tkinter 환경에서는 qasync 사용 불가 → 기존 `run_async_in_thread()` 패턴 사용

---

### Playwright High-Reliability Wait Pattern (Flaky 방지)
**When to use:** JavaScript 동적 렌더링 페이지. auto-wait 단독 사용 시 CI 환경 46.5% flaky 위험.

```python
import asyncio
from typing import Optional
from playwright.async_api import Page

async def robust_get_text(
    page: Page,
    selector: str,
    retries: int = 2,
    timeout: int = 10000
) -> Optional[str]:
    """명시적 wait + 재시도 래퍼. flaky test 방지."""
    for attempt in range(retries):
        try:
            await page.wait_for_selector(selector, state="visible", timeout=timeout)
            await page.wait_for_load_state("networkidle")
            return await page.inner_text(selector)
        except Exception as e:
            if attempt == retries - 1:
                raise RuntimeError(f"셀렉터 실패 ({retries}회): {selector}") from e
            await asyncio.sleep(1)
    return None
```

**Known pitfalls:**
- `wait_until="networkidle"` 단독 사용 금지 — 동적 SPA에서 무한 대기 가능
- CI 환경(GitHub Actions)에서는 `--headed` 제거, `chromium.launch(headless=True)` 필수
- 셀렉터는 `data-testid` 우선 사용 (CSS 클래스는 배포마다 변경 가능)

---

### EasyOCR RPA Pattern (좌표 독립 텍스트 인식)
**When to use:** IFS/ERP 화면 텍스트 기반 클릭. 좌표 하드코딩 완전 대체.
**사전 설치:** `pip install easyocr`

```python
import pyautogui
import numpy as np
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

def find_and_click_text(
    target: str,
    lang: Optional[list] = None,
    confidence: float = 0.75
) -> bool:
    """EasyOCR로 화면에서 텍스트를 찾아 클릭합니다."""
    import easyocr
    if lang is None:
        lang = ["ko", "en"]
    reader = easyocr.Reader(lang, gpu=False)
    img = pyautogui.screenshot()
    results = reader.readtext(np.array(img))
    for (bbox, text, conf) in results:
        if target in text and conf >= confidence:
            x = int((bbox[0][0] + bbox[2][0]) / 2)
            y = int((bbox[0][1] + bbox[2][1]) / 2)
            pyautogui.click(x, y)
            logger.info(f"OCR 클릭 성공: '{text}' @ ({x}, {y}) conf={conf:.2f}")
            return True
    logger.warning(f"OCR 클릭 실패: '{target}' 미발견")
    return False
```

**Known pitfalls:**
- 최초 실행 시 모델 다운로드 (~200MB) — 오프라인 환경 주의
- 한국어 인식률 향상: 스크린샷 전 `PIL.Image` + 흑백 고대비 전처리 권장
- `gpu=False` 고정 — CPU 환경에서도 안정 동작 (GPU 있으면 True로 변경 가능)

---

## Tier 2 Reliability Roadmap

| 단계 | 적용 내용 | 현재→목표 신뢰도 |
|---|---|---|
| Phase A (완료) | Tier 2 Pattern Library + execution_check | 70% → 85% |
| Phase B (이번) | PM Tier 분류 + OAR Loop + qasync/Playwright/EasyOCR 고신뢰 패턴 | 85% → 90% |
| Phase C (다음) | QA에 Tier 2 전용 체크리스트 + async_bridge/playwright_wait 검증 강화 | 90% → 93% |
| Phase D (3번째) | Harness Framework D: 런타임 실행 점수 반영 + OAR 사이클 횟수 채점 | 93% → **95%** |
