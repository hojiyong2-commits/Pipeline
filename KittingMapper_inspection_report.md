# KittingMapper 정적 분석 보고서

**파이프라인 ID:** IMP-20260511-92A6  
**분석 일시:** 2026-05-11  
**분석 대상:** `FEAT-20260426-332E` (KittingMapper 소스 코드)  
**분석 범위:** `main.py`, `ui/app.py`, `core/excel_mapper.py`, `core/order_lines_reader.py`, `core/packing_detail_reader.py`, `config.json`

---

## S1. PM 매핑 테이블 현황 확인

사용자 답변(Q1 — A, 정확함)에 따라 `config.json`의 `pm_name_map` 및 `pm_location_overrides` 내용을 검증합니다.

| 항목 | config.json 기재값 | 검증 결과 |
|---|---|---|
| Ji Yong Ho | 호지용 | 정확 (사용자 확인) |
| Hye Ran Ko | 고혜란 | 정확 (사용자 확인) |
| In Kyoung Nam | 남인경 | 정확 (사용자 확인) |
| Yong Kyu Sung | 성용규 | 정확 (사용자 확인) |
| pm_location_overrides — Sang Yong Cho (AT → 호지용, _default → 성용규) | — | 정확 (사용자 확인) |

**결론:** PM 매핑 테이블 수정 불필요.

---

## S2. Dead Code (데드 코드)

### F-01. `write_date` 변수 미사용 — CLI와 GUI 공통

| 구분 | 위치 | 내용 |
|---|---|---|
| CLI | `main.py`, L107 | `write_date = get_next_business_day(today)` |
| GUI | `ui/app.py`, L425–426 | `write_date: date = get_next_business_day(today)` + `logger.info(...)` |

두 경로 모두 `write_date`를 계산한 뒤 이후 코드에서 전혀 사용하지 않습니다. GUI는 로그에 출력만 하므로 실행 흐름에는 영향이 없으나, `get_next_business_day()` 호출이 매번 발생하여 불필요한 연산이 추가됩니다.

**권고:** 두 파일 모두에서 해당 라인을 제거합니다.

---

### F-02. `import re as _re` 인라인 import — GUI 전용

| 위치 | 내용 |
|---|---|
| `ui/app.py`, L591 | `import re as _re` (pending 처리 루프 안에 인라인 배치) |

루프 내부에서 `import` 를 반복 호출하는 패턴입니다. Python은 `sys.modules` 캐시 덕분에 런타임 비용은 낮지만, 코드 가독성을 해치고 정적 분석 도구가 미사용 import로 오탐할 수 있습니다.

**권고:** `import re` 를 파일 상단 import 블록으로 이동합니다.

---

### F-03. `__main__` 테스트 블록 — `order_lines_reader.py`

| 위치 | 내용 |
|---|---|
| `order_lines_reader.py`, L300–323 | `if __name__ == "__main__":` 직접 실행 블록 |

파일 단독 실행 시 동작하는 테스트/디버그 코드가 프로덕션 모듈에 포함되어 있습니다. 동일 패턴이 `packing_detail_reader.py` (L198–345)에도 존재합니다. 단위 테스트를 `tests/` 폴더로 분리하지 않아 모듈 크기가 불필요하게 커집니다.

**권고:** `__main__` 블록을 `tests/` 하위 pytest 테스트 파일로 이관하거나 삭제합니다.

---

### F-04. `__main__` 테스트 블록 — `packing_detail_reader.py`

| 위치 | 내용 |
|---|---|
| `packing_detail_reader.py`, L198–345 | `if __name__ == "__main__":` 직접 실행 블록 (148라인) |

F-03과 동일한 패턴이며, 임시 파일을 직접 생성하는 통합 테스트 수준의 코드입니다. `packing_detail_reader.py` 전체 파일의 약 43%를 차지합니다.

**권고:** F-03과 동일하게 `tests/` 로 이관합니다.

---

## S3. 코드 중복

### D-01. `_compress_line_nos()` 함수 완전 중복

| 위치 | 라인 |
|---|---|
| `main.py` | L34–63 |
| `ui/app.py` | L30–59 |

두 함수는 docstring·인자명·로직이 완전히 동일합니다. 향후 압축 알고리즘을 변경하면 두 파일을 동시에 수정해야 하며, 한 파일만 수정할 경우 CLI와 GUI의 출력이 달라지는 드리프트가 발생합니다.

**권고:** `core/utils.py`(또는 기존 유틸리티 모듈) 에 `_compress_line_nos()` 를 단일 정의하고, `main.py`와 `ui/app.py` 양쪽에서 `from core.utils import _compress_line_nos`로 import합니다.

---

## S4. CLI-GUI 기능 격차

### G-01. `pending_store` / `pm_location_overrides` CLI 미지원

| 기능 | CLI (`main.py`) | GUI (`ui/app.py`) |
|---|---|---|
| pending_store (포장반 재시도) | 없음 | L560–621 에 구현 |
| pm_location_overrides (고객별 PM 재배치) | 없음 | L406–408, L525–530 에 구현 |

CLI로 실행하면 포장반 치수 미확보 건이 재시도되지 않고, 고객별 PM 재배치(Sang Yong Cho AT → 호지용 등)도 적용되지 않습니다. CLI와 GUI가 서로 다른 결과를 생성합니다.

**권고:** `main.py`에도 GUI와 동일하게 `pending_store` 처리 블록과 `pm_location_overrides` 분기를 추가합니다. 단, 사용 환경이 GUI 전용으로 확정된 경우에는 CLI 진입점(`main.py`)에 `# CLI is GUI-only fallback; pending/override not supported` 주석을 명시하여 의도를 문서화합니다.

---

### G-02. 포장반 Note 기재 방식 차이

| 경로 | 조건 | 기재값 |
|---|---|---|
| CLI `main.py` | 치수 조회 성공 시 | `"포장반 ({dims_result})"` |
| CLI `main.py` | 치수 조회 실패 시 | `"포장반"` (하드코딩 문자열) |
| GUI `ui/app.py` | 치수 조회 실패 시 | `kit_place` (이메일 원문 값 그대로) |

이메일 원문 `kit_place` 값이 "포장반" 이외 변형(예: "포장반 (조기)")인 경우, CLI는 항상 "포장반"을 기재하고 GUI는 원문을 그대로 기재합니다. 동일 입력에 대해 두 경로가 다른 Excel 출력을 생성합니다.

**권고:** `main.py` L의 하드코딩 `"포장반"` 를 GUI와 동일하게 `kit_place` 변수로 교체합니다.

---

## S5. 잠재적 버그

### B-01. `jm` 컬럼 기본값 불일치 — `main.py`

| 항목 | 값 |
|---|---|
| `main.py` L181 기본값 | `excel_b_columns.get("jm", "G")` → 폴백: `"G"` |
| `config.json` 및 `ui/app.py` `_FIXED_EXCEL_B_COLUMNS["jm"]` | `"KJ"` |
| `order_lines_reader.py` `_DEFAULT_JM_COL` | `"KJ"` |

`config.json`이 누락되거나 `excel_b_columns` 키가 없을 때 CLI는 JM 컬럼을 `"G"` (7번째 열)로 읽지만, 실제 데이터는 `"KJ"` (296번째 열)에 있습니다. 이 경우 PM 매핑이 전혀 이루어지지 않고 모든 행이 스킵됩니다.

**권고:** `main.py`의 폴백값을 `"G"` → `"KJ"` 로 수정합니다.

```python
# 수정 전
jm_col_letter=excel_b_columns.get("jm", "G"),
# 수정 후
jm_col_letter=excel_b_columns.get("jm", "KJ"),
```

---

### B-02. UNC 경로 차단 — `order_lines_reader._safe_resolve()`

| 항목 | 내용 |
|---|---|
| `_ALLOWED_ROOT` | `Path.home()` — 예: `C:\Users\hojiy` |
| `config.json packing_detail_path` | `\\\\ccikrnf\\wrkgroup\\...` (UNC 네트워크 경로) |
| `config.json order_lines_path` | `\\\\ccikrnf\\wrkgroup\\...` (UNC 네트워크 경로) |

`Path("\\\\ccikrnf\\wrkgroup\\...").resolve()` 는 UNC 경로를 그대로 반환하며, `Path.home()` 하위에 있지 않으므로 `_safe_resolve()` 가 `ValueError: Path traversal detected` 를 발생시킵니다. 실 운용 환경에서 Config의 모든 경로가 차단됩니다.

**권고 (단기):** `_ALLOWED_ROOT` 를 `Path.home()` 단일 루트에서 허용 경로 목록(`List[Path]`) 으로 변경하고, UNC 경로를 별도 화이트리스트로 관리합니다.

```python
_ALLOWED_ROOTS: List[Path] = [
    Path.home(),
    Path("//ccikrnf/wrkgroup"),  # 사내 네트워크 공유 루트
]
```

또는 `order_lines_reader` 를 UNC 경로 허용 여부를 설정 파일에서 읽도록 리팩터링합니다.

---

## S6. 성능

### P-01. COM 인스턴스 매 쓰기 시 재생성 — `excel_mapper.py`

| 위치 | 내용 |
|---|---|
| `excel_mapper.py` `_apply_label_via_excel()` | `win32com.client.Dispatch("Excel.Application")` 를 함수 호출마다 실행 |

`write_to_excel_a()` 가 N개 행을 쓸 때 COM 인스턴스 생성-열기-닫기-소멸이 N번 반복됩니다. COM 인스턴스 생성은 약 1~2초의 오버헤드가 있으므로, 10건 처리 시 불필요한 대기가 10~20초 추가됩니다.

**권고:** `write_to_excel_a()` 호출 전에 COM 인스턴스를 1회 생성하여 `_apply_label_via_excel()` 에 주입(의존성 주입)하거나, `with` 컨텍스트 매니저 패턴으로 단일 인스턴스를 재사용합니다.

---

### P-02. `_cell_str()` 함수 루프 내부 재정의 — `packing_detail_reader.py`

| 위치 | 내용 |
|---|---|
| `packing_detail_reader.py`, L155–160 | `def _cell_str(col_1based):` 가 `for row in ws.iter_rows()` 루프 안에서 정의됨 |

Python에서 함수 정의(def)는 매 루프 반복마다 새 함수 객체를 생성하여 할당합니다. 행 수가 수백 개일 경우 불필요한 함수 객체 생성이 반복됩니다.

**권고:** `_cell_str()` 정의를 루프 바깥, 함수 상단으로 이동합니다.

---

## S7. 종합 개선 우선순위

| 우선순위 | 항목 ID | 제목 | 영향 범위 | 난이도 |
|---|---|---|---|---|
| P0 (즉시 수정) | B-02 | UNC 경로 차단 | 실 운용 불가 — 모든 Config 경로가 차단됨 | 중 |
| P0 (즉시 수정) | B-01 | `jm` 컬럼 기본값 불일치 | Config 누락 시 PM 매핑 전체 실패 | 소 |
| P1 | G-01 | pending_store / pm_location_overrides CLI 미지원 | CLI-GUI 결과 불일치 | 중 |
| P1 | G-02 | 포장반 Note 기재 방식 차이 | 동일 입력 시 다른 Excel 출력 | 소 |
| P1 | D-01 | `_compress_line_nos` 중복 | 유지보수 리스크 | 소 |
| P2 | F-01 | `write_date` 미사용 | 불필요한 연산 | 소 |
| P2 | P-01 | COM 인스턴스 매 쓰기 재생성 | 처리 속도 저하 | 중 |
| P2 | F-02 | `import re as _re` 인라인 | 가독성 저하 | 소 |
| P3 | F-03, F-04 | `__main__` 테스트 블록 | 모듈 크기 증가 | 소 |
| P3 | P-02 | `_cell_str` 루프 내 재정의 | 미미한 성능 저하 | 소 |

---

**보고서 끝 — IMP-20260511-92A6**
