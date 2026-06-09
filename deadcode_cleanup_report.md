# Dead Code 분류 보고서 — IMP-20260609-119C

**파이프라인:** IMP-20260609-119C  
**대상 파일:** pipeline.py  
**작성 일시:** 2026-06-09  
**담당:** dev-agent (MT-1)

---

## 요약 표

| 분류 | 항목 수 | 설명 |
|---|---|---|
| A. 삭제 대상 dead code | 0 | 실제 호출 없고 현재 SSoT와 충돌하는 분기 없음 |
| B. SSoT 인용 보강 (텍스트 변경) | 6 | 헬프/docstring 6개 위치에 SSoT IMP 번호 추가 |
| C. 보존 — 진단/테스트 전용 코드 | 다수 | validate_test_evidence() 등 내부 함수 — 현재 SSoT와 충돌 없음 |
| D. 보존 — 하위 호환성 코드 | 3 | --user-confirmed argparse 정의 등 — 제거 시 기존 스크립트 파괴 위험 |

---

## A. 삭제 항목 (Dead Code 확정) — 0개

> 삭제 항목 없음. Grep 10회 실행 결과 현재 SSoT(Three-Gate / Nonce Gate / Verification JSON SSoT)와
> 충돌하는 실행 가능 분기가 발견되지 않았습니다.

---

## B. SSoT 인용 보강 — 6개 위치 (변경 전/후 diff)

### B-1. 파일 상단 module docstring

**변경 전:**
```
현재 `pipeline.py harness --score ...`는 완료 경로가 아니며 CLI에서 차단됩니다.
```

**변경 후:**
```
현재 `pipeline.py harness --score ...`는 완료 경로가 아니며 CLI에서 차단됩니다.
  (SSoT: Three-Gate External Authority — CLAUDE.md "Three-Gate External Authority Mode" 섹션)
```

---

### B-2. cmd_harness() docstring

**변경 전:**
```
"""Reject the removed legacy harness score path.

    Harness helpers such as validate_test_evidence() remain available for unit tests and
    diagnostics, but the CLI command no longer mutates pipeline_state.json. Completion is
    owned by external gates only.
    """
```

**변경 후 (SSoT 인용 2줄 추가):**
```
    SSoT: Three-Gate External Authority (CLAUDE.md "Three-Gate External Authority Mode").
    COMPLETE 조건: Technical / Oracle / GitHub CI / User Acceptance 4개 gate 모두 PASS 필수.
```

---

### B-3. cmd_harness() BLOCKED 메시지

**변경 전:**
```
[THREE GATE BLOCKED] `pipeline.py harness --score`는 현재 필수 파이프라인의 완료 경로가 아닙니다 (not a completion path).
  대신 아래 외부 게이트를 순서대로 사용하세요:
```

**변경 후 (SSoT 인용 1줄 추가):**
```
[THREE GATE BLOCKED] `pipeline.py harness --score`는 현재 필수 파이프라인의 완료 경로가 아닙니다 (not a completion path).
  SSoT: Three-Gate External Authority (CLAUDE.md) — harness 숫자 점수는 COMPLETE 조건이 아닙니다.
  대신 아래 외부 게이트를 순서대로 사용하세요:
```

---

### B-4. gates accept — --user-confirmed 단독 BLOCKED 메시지

**변경 후 (SSoT 인용 1줄 추가):**
```
  SSoT: User Acceptance Nonce Gate (IMP-20260531-BBDB)
```

---

### B-5. gates accept — acceptance_code 없음 BLOCKED 메시지

**변경 후 (SSoT 인용 1줄 추가):**
```
  SSoT: User Acceptance Nonce Gate (IMP-20260531-BBDB)
```

---

### B-6. cmd_architect() — THREE GATE BLOCKED COMPLETE 메시지

**변경 후 (SSoT 인용 1줄 추가):**
```
  SSoT: Three-Gate External Authority (CLAUDE.md) / Verification JSON SSoT (IMP-20260605-58BF)
```

---

## C. 보존 항목 — 진단/테스트 전용

다음 함수들은 직접 CLI 완료 경로에 사용되지 않지만 단위 테스트와 진단 목적으로 보존됩니다.
현재 SSoT와 충돌 없음 확인.

- `validate_test_evidence()` — 하네스 증거 검증 헬퍼 (QA 진단 전용)
- `_parse_harness_report_et()` — harness_report XML 파싱 (내부 유틸)
- `_extract_test_code()` — test_code 추출 헬퍼 (내부 유틸)

---

## D. 보존 항목 — 하위 호환성

다음 코드는 하위 호환성을 위해 보존됩니다. 제거 시 기존 스크립트가 파괴될 위험이 있습니다.

- `--user-confirmed` argparse 정의 (4개 위치) — BLOCKED 처리로 SSoT 준수, argparse 정의 자체는 보존
- `--acceptance-code` 없는 호출에 대한 backward-compatible BLOCKED 경로 — 제거 금지 (CLAUDE.md 명시)

---

## 실행한 테스트 (회귀 없음 확인)

### T-1: harness --score BLOCKED 동작 확인 (AC-3)

```
python pipeline.py harness --score 100 --verdict PASS --test-output-file dummy.xml
```

결과:
- returncode: 1 (PASS)
- stdout: "[THREE GATE BLOCKED]..." 포함 (PASS)
- "SSoT: Three-Gate External Authority" 포함 (신규 인용 확인)

### T-2: --user-confirmed 단독 BLOCKED 동작 확인 (AC-3)

```
python pipeline.py gates accept --result ACCEPT --user-confirmed
```

결과:
- returncode: 1 (PASS)
- stdout: "acceptance_code_required" 포함 (PASS)
- "SSoT: User Acceptance Nonce Gate (IMP-20260531-BBDB)" 포함 (신규 인용 확인)

### T-3: import 검증

```
python -c "import pipeline; print('import OK')"
```

결과: import OK (PASS) — 구문 오류 없음

---

## 남은 위험

- 없음. 함수 시그니처/제어 흐름 변경 없음. 텍스트 보강만 수행.
- CLAUDE.md / agent MD는 본 파이프라인에서 수정하지 않음 (DQ-1 옵션 A 준수).
