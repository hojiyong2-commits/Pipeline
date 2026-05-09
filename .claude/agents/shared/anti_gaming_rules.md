# Anti-Gaming Rules Log

에이전트 우회 패턴 발견 기록. PM은 파이프라인 시작 시 이 파일을 Read하여 step_plan에 반영한다.

## [IMP-20260505-A4C0] 2026-05-05 초기 수립

### 발견된 우회 패턴

1. **Harness 점수 위조:** test-harness-agent가 실제 코드 실행 없이 정적 패턴 매칭만으로 점수를 부여하는 패턴.
   - 대응: `validate_test_evidence()` 완전 프로세스 격리 게이트 (pipeline.py BUG-20260509-ED9C).
   - 탐지 기준: `<test_code>` 블록 없거나 `_ast_assert_count() == 0` (test_* 메서드 내 assert* 없음) 또는 독립 subprocess returncode!=0 또는 testsRun=0이면 자동 반려. `unittest.TestCase` 없는 코드 또한 차단.

2. **QA Gaming:** QA가 `<execution_check>` 없이 코드 존재만으로 PASS를 선언하는 패턴.
   - 대응: qa-agent.md 실행 증거 의무화 규칙 기존 수립 완료.
   - 탐지 기준: handover에 `<execution_check>` 결과 누락 시 QA가 FAIL 처리.

3. **MD 수정 위조:** prompt-architect-agent가 MD 파일을 수정했다고 선언하나 실제 파일 내용과 불일치하는 패턴.
   - 대응: `<context_cleanup_report>` 의무 출력 + PM Grep 대조 의무 (prompt-architect-agent.md).
   - 탐지 기준: PM이 Grep으로 선언된 문구 변경이 실제 파일에 반영되었는지 라인 단위 확인.

### 적용 규칙

- step_plan `<forbidden>` 섹션에 발견된 패턴 유형 명시 의무.
- `validate_test_evidence()` 함수가 pipeline.py에 존재하는지 Phase 7 전 확인. BUG-20260509-4D25 이후 신규 모델: `python -m unittest` runner 기반 (returncode + testsRun + FAILED 체크). 구 nonce/ASSERTION PASSED 모델로 회귀 금지.
- META-FS-PD (MD-only) 태스크도 예외 없음 — meta-task도 `<test_code>` assert 필수 (pipeline.py hard gate). "MD-only 면제" 규칙은 IMP-20260507-FC80에서 폐기됨.

## [IMP-20260505-A4C0 Phase 9] 2026-05-05 Phase 8 정리 후 추가

### Phase 8 Cleanup에서 발견된 추가 패턴

4. **Stale Floor Example Gaming:** test-harness-agent.md의 `<floor_violation>` 예시가 이관된 카테고리(Robustness)를 사용하여 에이전트가 잘못된 카테고리로 floor_violation을 기록하는 패턴.
   - 대응: test-harness-agent.md 예시를 활성 카테고리(Code Quality)로 교체.
   - 탐지 기준: `<floor_violation><category>Robustness</category>` 또는 `<category>Security</category>` 발견 시 `critical_flaw: "STALE_FLOOR_VIOLATION"` 기록.

5. **Dead Sub-Rubric 점수 혼동:** test-harness-agent.md의 BUILD documentation Sub-Rubric이 25pt로 표기되어 BUILD 20pt 총합을 초과한다고 오인하는 패턴.
   - 대응: Sub-Rubric을 BUILD 20pt 내 "6-Section Report 5pt 세분화"로 명확히 재정의.
   - 탐지 기준: Harness가 BUILD를 20pt 초과로 채점하면 `critical_flaw: "BUILD_OVERCOUNT"` 기록.

6. **asyncio 강제 적용 오용:** dev-agent의 "Async-Driven" 규칙을 Tkinter GUI 태스크에도 적용하여 event loop 충돌을 유발하는 패턴.
   - 대응: dev-agent.md Async-Driven 규칙에 Tkinter/FS 단독 태스크 면제 명시.
   - 탐지 기준: Tkinter 앱에 asyncio 이벤트 루프를 직접 실행하는 코드 발견 시 QA PD FAIL.

## [IMP-20260507-B77A] 2026-05-07 Phase 8 이후 추가

### 발견된 패턴 (모니터링 대상 — 3회 미충족, 패치 미발행)

7. **strict mode 진입 시 [유형] placeholder 명시성 부족:** meta-task에서 MD 파일 내 `[유형]` 같은 placeholder 삽입 시 pipeline.py enum 값과의 cross-reference 주석이 없으면 strict mode QA가 PD.interface_contract 항목에서 부분 감점 처리하는 패턴.
   - 발생 사례: IMP-20260507-B77A — QA 110/120 (10pt 감점), strict mode 첫 진입 사이클.
   - 현재 상태: 1회 발생 — 3회 반복 기준 미충족으로 패치 미발행.
   - 모니터링: 다음 META-FS-PD strict mode 사이클에서 동일 패턴 재발 시 build-agent.md 주석 보강 대상.

## [IMP-20260507-B455] 2026-05-07 Phase 8 이후 추가

### 발견된 패턴

8. **Multi-location whitelist 동기화 누락:** 동일 whitelist/상수 값이 2개 이상 위치(예: 주석 + 본문)에 분산 존재할 때, Dev가 한 위치 수정 후 다른 위치 확인을 생략하는 패턴.
   - 발생 사례: IMP-20260507-B455 — build-agent.md L34(주석)는 수정, L83(본문) `power-automate` 누락 → QA Round 1 FAIL (60/120).
   - 대응: dev-agent.md `frozen_codebase_check` 항목 6 추가 (Multi-location Sync Audit) + `### 6. Multi-location Sync Audit` 섹션 신설.
   - 탐지 기준: `<sync_verification><sync_status>PARTIAL` 상태 handover는 QA가 PD FAIL 처리.

### 적용 규칙 추가

- step_plan `<forbidden>` 섹션에 "동일 whitelist가 2개 이상 위치에 존재할 경우 단일 위치 수정 후 handover 금지" 명시 의무 (해당 태스크).
- PM은 multi-location 태스크 식별 시 `<micro_tasks>`의 각 `<micro_task>`에 `<affected_call_sites>`를 통해 모든 위치를 열거.

## [IMP-20260507-EC22] 2026-05-07 Phase 8 이후 추가

### 발견된 패턴 (모니터링 대상)

9. **CLAUDE.md 문서 동기화 연쇄 누락:** 동일 값(예: skip-reason 유형)이 build-agent.md + pm-agent.md + CLAUDE.md 3개 이상 위치에 분산될 때, 일부 위치 수정 후 CLAUDE.md 본문을 누락하는 패턴.
   - 발생 사례: B77A/B455에서 build-agent.md 수정 완료 → CLAUDE.md line 205의 `"[유형]"` placeholder는 EC22에서 후행 처리.
   - 현재 상태: EC22에서 완결됨 (placeholder 전체 제거 + 열거표 추가).
   - 예방 규칙: PM이 whitelist/enum 값 관련 태스크 식별 시 Grep으로 CLAUDE.md + 모든 agent MD를 동시 조사하여 누락 위치 없는지 `<micro_tasks>`에 명시 의무.

## [IMP-20260507-0BF1] 2026-05-07 Phase 9 완료 후 추가

### 발견된 패턴

10. **meta-task FS.atomic_write tmp→rename 오감점:** META-FS-PD 태스크에서 test-harness가 md 파일 편집 시 물리적 tmp→rename 패턴 부재를 이유로 FS.atomic_write를 감점하는 패턴.
    - 대응: agents.md HARNESS 섹션에 `META-FS-PD 전용 보조 채점 기준` 추가 — Edit 단일 블록 = 원자적 쓰기 인정, tmp→rename 부재 감점 금지 명시.
    - 탐지 기준: harness가 meta-task에서 `FS.atomic_write` 감점 사유로 "tmp→rename 없음"을 기재한 경우 `critical_flaw: "META_TASK_ATOMIC_WRITE_MISRULE"` 기록.

11. **contract_audit 4필드 중 backward_compatibility / role_transition_clarity 누락:** architect가 `<contract_audit>`을 출력하나 4개 필수 필드 중 일부를 생략하여 CLAUDE.md Phase 9 Sync Gate 검증에서 차단되는 패턴.
    - 대응: prompt-architect-agent.md의 `<contract_audit>` 스키마에 4개 필드 전부 명시 + `IMP-20260507-0BF1` 강제 게이트 문구 추가.
    - 탐지 기준: `<contract_audit>` 블록에서 `<section_completeness>`, `<backward_compatibility>`, `<role_transition_clarity>` 중 하나라도 누락 시 PM이 Phase 9 진입 차단.

12. **strict_mode 판정 정보 harness 미전달:** architect가 strict_mode 활성화 여부를 판정했으나 PM step_plan `<context_injection>`에 포함하지 않아 harness가 strict_mode 추가 패널티를 적용하지 못하는 패턴.
    - 대응: CLAUDE.md Phase 9 Sync Gate에 strict_mode 판정 의무 조항 추가 (항목 4), prompt-architect-agent.md에 `<strict_mode_notification>` 블록 추가.
    - 탐지 기준: strict_mode가 활성이어야 함에도 test_results.jsonl에 `"strict_mode": {"triggered": false}`로 기록된 경우 architect를 재spawn하여 소급 판정.

### 적용 규칙 추가

- PM은 META-FS-PD 파이프라인 시작 시 test_results.jsonl에서 최신 META-FS-PD 레코드 3개를 Read하여 strict_mode 활성 여부를 step_plan에 명시 의무.
- harness는 META-FS-PD 프레임워크 채점 시 FS.atomic_write 감점 전 "meta-task 여부" 판정 후 단일 Edit 블록 기준 적용 의무.

## [IMP-20260507-0EDC] 2026-05-07 Phase 9 이후 추가

### 발견된 패턴

10. **Documentation-Code Drift (코드 변경 시 문서 비동기 업데이트):** pipeline.py 또는 CLAUDE.md의 수치값·플래그·enum 목록을 변경하는 IMP 태스크에서 enforcement 코드만 갱신하고 대응하는 prose 주석 및 CLAUDE.md 예시를 동일 사이클에서 업데이트하지 않는 패턴.
    - 발생 사례: IMP-20260507-0EDC — pipeline.py line 896 주석이 80pt를 가리키고 있었으나 실제 hard gate는 96pt (IMP-20260506-A064에서 변경된 값). CLAUDE.md line 205 예시에 `--user-confirmed` 플래그 누락.
    - 근본 원인: enforcement 코드 변경 파이프라인(IMP-20260506-A064)에서 대응 문서 업데이트를 별도 micro_task로 분리하지 않고 생략.
    - 대응: pm-agent.md `## Technical Rules` 섹션에 "Documentation-Code Drift 방지" 규칙 추가 (IMP-20260507-0EDC).
    - 탐지 기준: pipeline.py에서 수치 상수(예: 96, 80, 120) 또는 플래그명(예: `--user-confirmed`, `--skip-reason`)을 변경하는 micro_task가 대응 CLAUDE.md prose 업데이트를 `<affected_call_sites>`에 포함하지 않으면 PD FAIL 처리.

### 적용 규칙 추가

- PM은 수치 상수·플래그·enum 변경 micro_task에서 `<requirements>`에 "대응 prose 문서(CLAUDE.md 섹션 및 pipeline.py 인라인 주석) 동일 micro_task 업데이트" 명시 의무.
- step_plan `<forbidden>` 섹션에 "enforcement 코드 변경과 prose 문서 업데이트를 별도 파이프라인으로 분리 금지" 추가 의무.

## [IMP-20260507-759D] 2026-05-07 Phase 8 이후 추가

### 발견된 패턴 (신규 — 이번 사이클에서 최초 식별)

이번 사이클(계약 충돌 5건 수정)에서 새로운 우회/오류 패턴은 식별되지 않음.
단, 아래 구조적 취약점이 이번 수정 대상으로 식별되어 기록:

11. **Framework whitelist 불일치 (Dead Constant):** CLAUDE.md SSoT의 framework 허용값 목록이 test-harness-agent.md의 실제 사용값과 달라 Architect의 `<contract_audit>`에서 TIER_MISMATCH 기록이 발생할 수 있는 패턴.
    - 발생 사례: IMP-20260507-759D 수정 전 — CLAUDE.md `FRAMEWORK_A/B/C` vs TH 실제 사용 `BUILD+QA_NUMERIC/QA_NUMERIC_ONLY`.
    - 대응: CLAUDE.md framework whitelist를 실제 사용값 3종으로 교체 (이번 사이클 수정).
    - 탐지 기준: CLAUDE.md whitelist에 없는 framework 값이 test_results.jsonl에 기록되면 `critical_flaw: "FRAMEWORK_WHITELIST_MISMATCH"` 기록.

12. **Phase 9 재채점 Framework 분기 누락:** test-harness-agent.md Framework 선택 로직에 Phase 9 재채점(`phase9_rescore: true`)과 일반 meta-task를 구분하는 분기가 없어, 재채점 시 QA numeric을 재채점하는 오동작 가능성.
    - 발생 사례: IMP-20260507-759D 수정 전 — pm-agent.md:148과 test-harness-agent.md:113 계약 충돌.
    - 대응: Framework 선택 우선순위 0번에 phase9_rescore 분기 추가, pm-agent Phase 9 행에 태그 지시 추가.
    - 탐지 기준: Phase 9 재채점 시 QA numeric이 재계산된 흔적(이전 기록값과 다른 값)이 있으면 `critical_flaw: "PHASE9_QA_RECALCULATION"` 기록.

### 적용 규칙 추가

- PM은 Phase 9 재채점 시 step_plan에 `<phase9_rescore>true</phase9_rescore>` 태그 포함 의무.
- test-harness-agent는 framework whitelist에 없는 값 기록 시 즉시 오케스트레이터에 보고.

## [IMP-20260507-7E0F] 2026-05-07 Phase 8 이후 추가

### 발견된 패턴 (해결 완료)

13. **Schema Gate Required Field 불완전:** test-harness-agent.md Schema Gate 스크립트의 required list에 `framework` 필드가 누락되어, framework 값이 없는 레코드가 게이트를 통과할 수 있는 패턴.
    - 발생 사례: IMP-20260507-7E0F 수정 전 — `validate_test_evidence()`가 framework whitelist를 검증하지 않아 임의 framework 값 기록 허용.
    - 대응: test-harness-agent.md Schema Gate required list에 `'framework'` 추가 + `FRAMEWORK_WHITELIST` 검증 블록 추가 (항목 N).
    - 탐지 기준: `framework` 값이 `META-FS-PD/BUILD+QA_NUMERIC/QA_NUMERIC_ONLY` 외 값이면 `FRAMEWORK_INVALID` 오류.

14. **score/percentage float 허용 버그:** test-harness-agent.md Schema Gate가 int 타입 강제 없이 float 기록을 허용하고, 문서 예시에도 float이 포함되어 있어 에이전트가 float 기록을 합법으로 오인하는 패턴.
    - 발생 사례: IMP-20260507-7E0F 수정 전 — jsonl에 `pct=100.0` (float) 레코드 7건 + `score=None` 레코드 4건 존재.
    - 대응: test-harness-agent.md `## score/percentage 필드 null 절대 금지 (RCA-20260507-001)` 섹션 신설 + Schema Gate int 타입 강제 검증(항목 6/7) 추가.
    - 탐지 기준: `SCORE_TYPE_VIOLATION` / `PERCENTAGE_TYPE_VIOLATION` / `SCORE_NULL_VIOLATION` / `PERCENTAGE_NULL_VIOLATION` 오류로 차단.

### 적용 규칙 추가

- PM은 score/percentage 관련 meta-task 시 `<acceptance_criteria>`에 "schema gate 스크립트의 int 강제 검증 항목 포함 확인" 명시 의무.
- test-harness-agent는 jsonl 기록 후 Schema Gate 자가검증 스크립트를 반드시 실행하고 `[SCHEMA GATE PASS]` 확인 후에만 `[HARNESS 완료]` 출력 허용.

## [IMP-20260507-E10F] 2026-05-07 Phase 9 이후 추가

### 발견된 패턴 (해결 완료)

15. **agents.md [HARNESS] Output Format 섹션 score/percentage 강제 규칙 부재:** agents.md의 [HARNESS] 섹션 Output Format에 score/percentage int 강제 규칙(RCA-20260507-001)이 명시되지 않아 harness 에이전트가 null/float 기록을 합법으로 오인하는 패턴.
    - 발생 사례: IMP-20260507-E10F — agents.md Output Format에 금지 패턴 미기재 → test_results.jsonl에 score=null(4건), percentage=100.0 float(1건) 누적.
    - 근본 원인: test-harness-agent.md에는 RCA-20260507-001 섹션이 존재했으나, agents.md [HARNESS] 섹션의 Output Format에는 동일 규칙이 동기화되지 않은 Documentation-Code Drift.
    - 대응: agents.md [HARNESS] `## Output Format` 섹션에 금지 패턴 3항목 + 3개 프레임워크 JSON 예시(int 값 명시) 추가.
    - 탐지 기준: agents.md [HARNESS] Output Format에 `RCA-20260507-001` 문자열 없으면 `critical_flaw: "HARNESS_FORMAT_DRIFT"` 기록.

### 적용 규칙 추가

- PM은 test-harness-agent.md 수정 시 agents.md [HARNESS] 섹션도 동일 사이클에서 동기화 의무 (`<affected_call_sites>`에 포함).
- dev-agent가 agents.md 수정 시 [HARNESS] Output Format 섹션의 score/percentage 금지 패턴 존재 여부를 `<self_check>` 단계에서 확인.

## [IMP-20260507-35C5] 2026-05-07 Phase 8 이후 추가

### 발견된 패턴 (기존 패턴 10 재발 — 모니터링 카운트 증가)

재발 케이스: pipeline.py에 `--judgment-confirmed` flag 추가 시 pm-agent.md Phase 1 table 미동기화.
- 기존 패턴 10 (Documentation-Code Drift) 2회 발생 — 3회 반복 기준 미충족, 구조적 패치 미발행.
- 해결: Phase 9에서 Phase 1 table에 `[--judgment-confirmed]` 추가 완료.
- 모니터링: pipeline.py flag 추가 시 pm-agent.md Phase 1 table 동시 업데이트 의무화 — dev-agent.md 규칙 강화 대상 (3회 도달 시).

### 적용 규칙 보강 (패턴 10 enforcement)

- PM은 pipeline.py `--[flag]` 추가 micro_task에서 `<affected_call_sites>`에 pm-agent.md Phase 1 table 행을 반드시 포함. 미포함 시 QA PD.functional_completeness -1pt.

## [IMP-20260507-4D27] 2026-05-07 Phase 8 이후 추가

### 모니터링 패턴 (1회 발생 — 3회 반복 시 패치 대상)

16. **test_code subprocess 상대경로 실패 (IMP-20260507-4D27):** test-harness-agent가 test_code 내에서 `pathlib.Path(__file__).parent / 'pipeline.py'`를 사용하면 `validate_test_evidence()`가 tmp 파일로 실행 시 `__file__`이 tmp 디렉토리를 가리켜 pipeline.py를 찾지 못하고 exit code 2 반환 → ASSERTION PASSED 미검출 → [HARNESS GATE BLOCKED].
    - 발생 사례: IMP-20260507-4D27(1회), IMP-20260507-46AB(2회), BUG-20260509-FDBC(3회) — 3회 임계값 도달, 패치 발행.
    - 해결 패턴: test_code에 `PROJECT_DIR = r"[절대경로]"` 상수를 정의하고 subprocess 호출 시 `cwd=PROJECT_DIR` 인자를 명시적으로 포함.
    - 현재 상태: **3회 발생 — 3회 임계값 도달. test-harness-agent.md Anti-Gaming Gate 섹션에 "subprocess 호출 시 절대 경로 필수" 규칙 패치 완료 (BUG-20260509-FDBC Phase 8).**
    - 대응: test-harness-agent.md `**금지 패턴 (Schema Gate가 ET_PARSE_ERROR로 차단):**` 섹션 직후에 절대경로 필수 규칙 + 코드 예시 + FORBIDDEN 패턴 명시 추가.
    - 탐지 기준: test_code에 `__file__` 기반 경로가 subprocess cwd로 사용되거나 cwd 인자가 없으면 `critical_flaw: "SUBPROCESS_RELATIVE_PATH"` 기록.

## [IMP-20260507-46AB] 2026-05-07 Phase 8 이후 추가

### 신규 패턴: 없음 (모니터링 카운트 업데이트)

이번 사이클(IMP-20260507-46AB — qa-agent.md Step 0-C gate + build-agent.md power-automate skip-reason)에서 새로운 우회 패턴 식별 없음. 100/100 달성.

- 패턴 16 (test_code 상대경로 실패): 이번 사이클 PM이 절대경로 사용으로 BLOCKED 없이 통과. 발생 2회 — 3회 임계값 미충족.
- 모니터링 유지: 다음 pipeline.py 대상 하네스 사이클에서 패턴 16 재발 시 test-harness-agent.md Anti-Gaming Gate에 규칙 추가 의무.

## [IMP-20260507-EEC9] 2026-05-08 Phase 8 이후 추가

### 신규 패턴: 없음 (모니터링 카운트 업데이트)

이번 사이클(IMP-20260507-EEC9 — task.md harness gate --user-confirmed, dev done --scope-declared, pipeline.py --decomp/--clarification/--failure-sig hard gates, PHASE_INTERFACE next_cmd 업데이트, agents.md 11개 에이전트 동기화)에서 새로운 우회 패턴 식별 없음. strict mode 활성 상태에서 100/100 달성.

- 패턴 16 (test_code 상대경로 실패): 이번 사이클 BLOCKED 없이 통과. 발생 2회 유지 (3회 임계값 미충족).
- 모니터링 유지: 다음 pipeline.py 대상 하네스 사이클에서 재발 시 test-harness-agent.md Anti-Gaming Gate 규칙 추가 의무.

## [BUG-20260507-559E / IMP-20260507-1E8D] 2026-05-08 Phase 9 이후 추가

### 식별된 반복 감점 패턴 (RCA 기반)

이번 사이클(BUG-20260507-559E Harness 100pt, Phase 8 RCA 수행, Phase 9 dev-agent.md 패치)에서 우회 패턴이 아닌 **규칙 부재로 인한 반복 감점 구조**를 확인 및 해소:

17. **AL.type_valid 트리거 범위 명시 부재 (7회 누적 감점 패턴):** dev-agent.md에 AL.type_valid 4-item 체크리스트 코드 패턴은 존재했으나, "어떤 함수/지점에 적용되는가"의 트리거 조건 표가 없어 에이전트가 공개 함수에만 적용하고 UI 핸들러/내부 클로저를 빠뜨리는 패턴.
    - 발생 사례: BUG-20260507-559E — AL.type_valid 7회 누적 감점.
    - 대응: `AL.type_valid 트리거 조건 표` (5행 테이블) + `AL-V1~V5 자가 검증 체크리스트` 추가 (IMP-20260507-1E8D).
    - 탐지 기준: dev-agent.md에 `AL-V1`~`AL-V5` 검증 항목이 모두 존재하는지 Grep으로 확인. 누락 시 `critical_flaw: "AL_CHECKLIST_MISSING"` 기록.

18. **FS.traversal 쓰기 경로 방어 누락 (5회 누적 감점 패턴):** dev-agent.md에 `_safe_resolve()` 읽기 배선 규칙은 존재했으나 쓰기 경로(safe_write_file)에 `allowed_root` 파라미터가 없어 사용자 입력 파생 경로의 쓰기 traversal 방어가 누락되는 패턴.
    - 발생 사례: BUG-20260507-559E — FS.traversal 5회 누적 감점.
    - 대응: `safe_write_file(path, content, allowed_root, encoding)` 함수 정의 추가 + FS.traversal 판별 표에 쓰기 경로 열 추가 (IMP-20260507-1E8D).
    - 탐지 기준: dev-agent.md에 `safe_write_file` + `allowed_root: Path` + `Path traversal detected on write`가 모두 존재하는지 Grep. 하나라도 없으면 `critical_flaw: "FS_WRITE_TRAVERSAL_UNPATCHED"` 기록.

19. **PD.edge_case 5개 케이스 중 EC-3/EC-4/EC-5 명시 부재 (4회 누적 감점 패턴):** dev-agent.md에 PD.edge_case 일반 설계 기준 표는 존재했으나, "어떤 5개 케이스가 있고 각각 어떻게 검증하는가"의 구조화된 체크리스트가 없어 에이전트가 EC-1/EC-2만 처리하고 EC-3(상태 재진입)/EC-4(0 크기)/EC-5(while True 상한) 검증을 누락하는 패턴.
    - 발생 사례: BUG-20260507-559E — PD.edge_case 4회 누적 감점.
    - 대응: `PD.edge_case 만점 강제 패턴 + 5-item 체크리스트` 섹션 신설 — EC-1~EC-5 트리거 표 + PD-EC-1~PD-EC-5 자가 검증 체크리스트 (IMP-20260507-1E8D).
    - 탐지 기준: dev-agent.md에 `PD-EC-5` + `EC-5` + `PD.edge_case 5-item` 텍스트가 모두 존재하는지 Grep. 누락 시 `critical_flaw: "PD_EDGE_CASE_CHECKLIST_MISSING"` 기록.

### 적용 규칙 추가

- PM은 AL/FS/PD 카테고리 포함 태스크에서 step_plan `<forbidden>`에 "AL-V1~V5 자가 검증 체크리스트 누락 금지", "FS 쓰기 경로 safe_write_file(allowed_root) 사용 의무", "PD-EC-1~EC-5 5항목 자가 검증 완료 의무" 명시 의무.

## [BUG-20260509-FDBC] 2026-05-09 Phase 8 이후 추가

### 패턴 16 임계값 도달 — 패치 발행

패턴 16 (test_code subprocess 상대경로 실패) 3회 발생으로 임계값 도달. 이번 사이클(BUG-20260509-FDBC)에서 패치 발행 완료.

- **패치 대상:** test-harness-agent.md `금지 패턴` 섹션 직후에 `test_code subprocess 절대경로 필수 규칙` 추가.
- **패치 내용:** `PROJECT_DIR = r"[절대경로]"` 상수 정의 + `cwd=PROJECT_DIR` 인자 명시 의무 + 금지 패턴 3종(`__file__` 기반 cwd, cwd 미지정, 상대경로 pipeline.py) 명시.
- **탐지 기준:** test_code에 `__file__` 기반 경로가 subprocess cwd로 사용되거나 cwd 인자가 없으면 `critical_flaw: "SUBPROCESS_RELATIVE_PATH"` 기록.

### 신규 패턴: 없음

이번 사이클(BUG-20260509-FDBC — PASS/FAIL harness evidence gate 비대칭 해소, `_validate_harness_evidence_gate()` helper 신설, test 17~19 추가)에서 새로운 우회 패턴 식별 없음. strict mode 활성 상태에서 100/100 달성.

### 적용 규칙 추가

- test-harness-agent는 test_code 내 subprocess 호출 시 `PROJECT_DIR = r"[절대경로]"` 상수를 정의하고 `cwd=PROJECT_DIR`을 명시적으로 전달해야 한다. 위반 시 `critical_flaw: "SUBPROCESS_RELATIVE_PATH"` 기록 의무.
- dev-agent 다음 파이프라인 시작 전 이 파일의 패턴 17~19를 확인하여 step_plan `<acceptance_criteria>`에 반영.

## [BUG-20260509-FA5E / BUG-20260509-B208] 2026-05-09 Phase 1 PM 분석 후 추가

### 신규 패턴

23. **noop/skip/expectedFailure 우회 (BUG-20260509-FA5E):** `validate_test_evidence()`의 구 unittest runner 모델(`returncode==0 + testsRun>=1 + FAILED 미포함`)이 아래 3가지 케이스를 잘못 통과시키는 패턴.
    - `test_noop` (assertion 없이 pass만): returncode=0, testsRun=1, no FAILED → 구 모델 통과 (실 검증 없음)
    - `@unittest.skip`: returncode=0, skipped=1, no FAILED → 구 모델 통과 (실 실행 없음)
    - `@unittest.expectedFailure` (실패하는 테스트): returncode=0, expectedFailures=1, no FAILED → 구 모델 통과 (실패를 기대값으로 위장)
    - 근본 원인: `unittest.TestResult`의 6개 카운터 중 `testsRun`과 returncode/FAILED만 검사하고 `skipped`, `expectedFailures`, `unexpectedSuccesses`, `assertionCount`를 검사하지 않음.
    - 대응 (현재 모델): strict unittest evidence gate. `_ast_assert_count()` + `_ast_forbidden_check()` + stdin nonce runner JSON line으로 `executed_assertions`, `testsRun`, failures/errors/skips 계열을 모두 검사한다.
    - 탐지 기준: `validate_test_evidence()` 내부에 `executed_assertions`, `skipped`, `expectedFailures`, nonce 검사가 없거나 stderr/stdout 텍스트 파싱에 의존하면 `critical_flaw: "VALIDATE_TEST_EVIDENCE_STALE_MODEL"` 기록.

### 적용 규칙 추가

- test-harness-agent는 `<test_code>`에 반드시 실 assertion(`self.assertX(...)`)을 포함해야 한다. `pass`만 있는 noop 테스트, `@unittest.skip`, `@unittest.expectedFailure`는 assertionCount==0 또는 skipped/expectedFailures>=1로 차단.
- PM은 `validate_test_evidence` 관련 micro_task에서 `<acceptance_criteria>`에 "noop/skip/expectedFailure 차단 확인 (Tests 30-32)" 명시 의무.
- dev-agent는 `validate_test_evidence()` 수정 시 `assertionCount` 검사 미포함 시 QA PD FAIL 처리.

## [BUG-20260509-7D6E] 2026-05-09 Phase 9 이후 추가

### 패턴 10 재발 (3회 임계값 도달) — 구조적 패치 발행

패턴 10 (Documentation-Code Drift) 3회 발생으로 임계값 도달. 이번 사이클(BUG-20260509-7D6E)에서 구조적 패치 발행.

**발생 이력:**
- 1회 (IMP-20260507-0EDC): pipeline.py prose drift — pipeline.py line 896 주석 값 불일치
- 2회 (IMP-20260507-35C5): pm-agent.md Phase 1 table에 `--judgment-confirmed` flag 누락
- 3회 (BUG-20260509-7D6E): task.md Step 7 CDATA rule에 `BUG-20260509-7D6E` reference 누락 — 동일 사이클에서 CLAUDE.md / test-harness-agent.md / Global_Wiki.md / agents.md는 모두 업데이트 완료, task.md만 누락 (-2pt strict_mode)

**구조적 패치 (3회 임계값 도달):**

20. **Documentation-Code Drift — consumer 파일 일부 누락:** dev-agent가 multi-consumer sync 태스크에서 dev evidence `<evidence>` 블록에 파일명을 나열했음에도 실제 파일 편집을 누락하는 패턴. 특히 task.md / pm-agent.md 같은 오케스트레이터 규칙 파일이 누락 대상이 됨.
    - 대응: PM step_plan `<micro_tasks>`의 MT-N에 task.md를 consumer로 명시할 때 `<affected_call_sites><site>.claude/commands/task.md</site></affected_call_sites>` 형태로 포함 의무화. dev-agent `<self_check>` 단계에서 `<evidence>` 파일 목록과 실제 Edit 완료 여부 1:1 대조 추가.
    - 탐지 기준: `<evidence>` 파일 목록에 있으나 실제 Edit/Write 호출 없는 파일 발견 시 QA가 `PD:evidence_file_not_edited` failure_signature로 자동 FAIL.

### 적용 규칙 추가

- PM은 CDATA rule / whitelist / enum 등 multi-consumer sync 태스크에서 `<micro_tasks>` `<affected_call_sites>`에 task.md를 반드시 포함하고 별도 micro_task로 분리 또는 동일 MT에 포함 의무.
- dev-agent는 handover `<evidence>` 블록 파일 목록 제출 전 각 파일에 대해 실제 Edit/Write 호출 완료 여부를 `<self_check>` 에서 확인. 미완료 파일 있으면 handover 금지.

## [BUG-20260509-5270] 2026-05-09 Phase 8 이후 추가 (구 nonce 모델 — 폐기됨)

### 발견된 패턴 (참고용 — BUG-20260509-4D25에서 근본 해결)

21. **VALIDATE_TEST_EVIDENCE_STALE_MODEL (BUG-20260509-4D25):** validate_test_evidence()의 구 구현(stdout 마커 검사 모델 — ASSERTION PASSED / __ASSERT_COUNTER__ / per-run nonce)은 근본적으로 "검증 대상 코드가 자기 검증 증거를 출력"하는 구조이므로 어떤 마커 방식이든 스푸핑 가능하다.
    - 구 구현의 한계: test_code 내에서 마커 문자열 출력(print 등) + 별도 경로로 실행하면 통과 가능. AST gate, nonce 모두 이 근본 취약점 해결 불가.
    - 근본 대응 (BUG-20260509-4D25): validate_test_evidence()를 `python -m unittest` runner 모델로 완전 교체. RUNNER(pipeline.py)가 결과를 보고하므로 test_code가 결과를 위조할 수 없음.
    - 현재 통과 기준: strict AST policy 통과 AND runner nonce 일치 AND `returncode == 0` AND `executed_assertions >= 1` AND `testsRun >= 1` AND failures/errors/skipped/expectedFailures/unexpectedSuccesses == 0.
    - 스푸핑 차단 원리: `print("ASSERTION PASSED")` only → testsRun=0 → 차단; `sys.exit(1)` → returncode!=0 → 차단; dead code assert → TestCase 없어서 testsRun=0 → 차단; `__file__` nonce 추출 → TestCase 없어서 testsRun=0 → 차단; `print("__ASSERT_COUNTER__")` only → testsRun=0 → 차단.
    - 탐지 기준: validate_test_evidence() 내부에서 `"ASSERTION PASSED" in output`, `"__ASSERT_COUNTER__" in output`, `re.search(r"Ran (\d+) tests?", output)`, `"FAILED" not in output` 같은 텍스트 신호에 의존하면 `critical_flaw: "VALIDATE_TEST_EVIDENCE_STALE_MODEL"` 기록. 현재 모델에서는 stdin nonce runner JSON line만 허용.

### 적용 규칙 추가

- dev-agent는 validate_test_evidence() 수정 시 stdout 검사에 `"ASSERTION PASSED"`, `"__ASSERT_COUNTER__"`, nonce 등 고정/가변 마커 문자열 검사를 사용하면 QA PD FAIL 처리. unittest runner 모델(returncode + testsRun + FAILED 체크)만 허용.
- PM은 pipeline.py validate_test_evidence 관련 micro_task에서 `<acceptance_criteria>`에 "스푸핑 차단 테스트: print 스푸핑 + TestCase 없음 → testsRun=0 → False" 및 "unittest TestCase + passing test_* → returncode=0 → True" 항목 명시 의무.

## [BUG-20260509-4D25] 2026-05-09 unittest runner 모델 전환

### 발견된 근본 패턴

22. **근본 취약점: 자기 검증 모델 (BUG-20260509-4D25):** validate_test_evidence()가 "검증 대상 코드(test_code)가 자기 검증 증거를 출력"하도록 설계되어 있어, 어떤 마커(ASSERTION PASSED / __ASSERT_COUNTER__ / per-run nonce)도 test_code 작성자가 해당 마커를 직접 출력하면 우회 가능하다.
    - 모든 stdout 마커 기반 모델의 공통 취약점: test_code → stdout 출력 → pipeline.py가 stdout 확인 → test_code가 결과를 직접 통제할 수 있음.
    - 근본 해결 (BUG-20260509-4D25): RUNNER가 결과를 보고하는 모델로 전환. `python -m unittest`는 test 실행 결과를 자체적으로 집계하므로 test_code가 결과를 위조할 수 없음.

### 적용 규칙

- `validate_test_evidence()` 미래 수정 시 반드시 RUNNER 보고 모델 유지. test_code의 stdout에 의존하는 구조로 회귀 금지.
- test-harness-agent는 `<test_code>`에 반드시 `unittest.TestCase` 서브클래스 + 최소 1개 `test_*` 메서드를 포함해야 한다. `print("ASSERTION PASSED")` 또는 bare `assert` 문만 있는 코드는 testsRun=0으로 거부됨.

## [BUG-20260509-6A4F] 2026-05-09 Phase 1 PM 분석 후 추가

### 신규 패턴

24. **Result-channel spoof via shared stdout (BUG-20260509-6A4F):** `__PIPELINE_RESULT__:` prefix가 MD 파일에 공개 문서화되어 있어 test_code가 동일 stdout 채널에 `print('__PIPELINE_RESULT__:...')` 출력으로 가짜 결과를 주입할 수 있는 패턴. pipeline.py 구 구현(line 329)에서 첫 번째 매칭 라인을 취하므로 스푸핑이 성공한다.
    - 반례: `print('__PIPELINE_RESULT__:{"testsRun":1,...}')` + TestCase 없음 → 구 모델 통과 (testsRun=0이어야 함)
    - 근본 원인: 신뢰할 수 없는 test_code stdout과 신뢰할 수 있는 runner 결과가 동일 채널을 공유.
    - 대응 (현재 모델): sidecar 파일 폐기. runner가 stdin으로 받은 nonce와 test_code를 사용하고, 최종 nonce JSON line만 출력한다. stderr/stdout의 unittest 텍스트와 sidecar 파일은 신뢰하지 않는다.
    - 탐지 기준: `validate_test_evidence()` 내부에서 stdout의 공개 prefix, stderr unittest 텍스트, sidecar `report_path` 파일을 신뢰하면 `critical_flaw: "RESULT_CHANNEL_SPOOF_VULNERABLE"` 기록. 현재 모델에서는 stdin nonce + runner JSON line nonce 일치만 허용.

### 적용 규칙 추가

- PM은 `validate_test_evidence` 관련 micro_task에서 `<forbidden>`에 "stdout `__PIPELINE_RESULT__:` 파싱 로직으로 회귀 금지" 명시 의무.
- dev-agent는 sidecar result file, stderr 텍스트 파싱, 공개 stdout marker, runner 전역 result path/counter 구조로 회귀하면 QA PD FAIL. strict AST policy + stdin nonce runner JSON line 유지 의무.
- test-harness-agent는 `<test_code>` 내에서 `print('__PIPELINE_RESULT__:...')` 패턴 사용 금지.

## [BUG-20260509-894D] 2026-05-09 Phase 9 이후 추가

### 신규 패턴

26. **dead code assert 우회 (BUG-20260509-894D):** `if False: self.assertEqual(1,1)` 같은 dead code에 assert*를 배치하여 AST 정적 카운트(ast_count >= 1)는 통과하지만 실제로는 실행되지 않는 패턴. 구 모델(returncode==0 + testsRun>=1)은 이 케이스를 통과시켰음.
    - 대응: stdin nonce runner JSON line의 `executed_assertions` 런타임 카운터. dead code 브랜치는 실행되지 않으므로 executed_assertions=0 → 차단.
    - 탐지 기준: executed_assertions < 1이면 `[EVIDENCE GATE] 검증 실패: executed_assertions=0` 출력 + False 반환.

27. **monkeypatch — assert* 메서드 재할당 (BUG-20260509-894D):** `unittest.TestCase.assertEqual = lambda *a, **k: None` 같이 assert* 메서드를 lambda/noop으로 교체하여 executed_assertions 카운터가 증가하지 않게 만드는 패턴.
    - 대응: `_ast_forbidden_check()` — AST에서 assert* 속성 재할당(Assign-to-attribute) 탐지 → 즉시 hard-reject.
    - 탐지 기준: `FORBIDDEN: monkeypatch detected — '...' attribute reassignment` 메시지 + False 반환.

28. **unittest.main() in test_* body (BUG-20260509-894D):** test_* 메서드 내부에서 unittest.main()을 직접 호출하여 runner 프로세스 내 재귀적 테스트 실행을 유발하려는 패턴.
    - 대응: `_ast_forbidden_check()` — test_* 메서드 body에서 unittest.main() Call 탐지 → 즉시 hard-reject.
    - 탐지 기준: `FORBIDDEN: unittest.main() call inside test_* method` 메시지 + False 반환.

29. **unreachable assert after return (BUG-20260509-894D):** test_* 메서드에서 return 문 이후에 assert*를 배치하여 AST 카운트는 통과하지만 실제로는 실행되지 않는 패턴.
    - 대응: `_ast_forbidden_check()` — test_* body를 순서대로 탐색하여 Return 이후 assert* 호출 탐지 → 즉시 hard-reject.
    - 탐지 기준: `FORBIDDEN: unreachable assert* call after return` 메시지 + False 반환.

30. **fake stderr injection (BUG-20260509-894D):** `sys.stderr.write('Ran 1 test\nOK')` 같이 가짜 unittest 출력을 stderr에 직접 써서 구 stderr-파싱 모델을 우회하려는 패턴.
    - 대응: stderr 텍스트 파싱 완전 폐기. stdin nonce runner JSON line만 신뢰. TestCase 없으면 ast_count=0 → AST gate 차단.
    - 탐지 기준: validate_test_evidence() 내부에서 `proc.stderr` 파싱이 없으면 이 패턴은 자동 무력화.

### 적용 규칙 추가

- `_ast_forbidden_check()` 함수가 pipeline.py에 존재하고 `validate_test_evidence()` Step 2에서 호출되는지 Phase 7 전 확인.
- test-harness-agent는 `<test_code>` 내에 dead code assert, monkeypatch, unittest.main() in test_* body, unreachable assert 패턴 사용 금지.
- PM은 `validate_test_evidence` 관련 micro_task에서 `<acceptance_criteria>`에 "Test 40-44 신규 패턴 차단 확인" 명시 의무.
- dev-agent는 `validate_test_evidence()` 수정 시 stderr 파싱 로직 재도입 금지. runner-owned JSON 채널 유지 의무.

## [BUG-20260509-ED9C] 2026-05-09 Phase 9 이후 추가

### 신규 패턴

25. **공유 프로세스 상태 조작 (BUG-20260509-ED9C):** runner와 test_code가 같은 Python 프로세스를 공유할 때 발생하는 신뢰 경계 침해 패턴.
    - `import __main__; __main__._assertion_count = 1` — test_code가 runner의 전역 카운터를 직접 조작하여 noop 테스트가 통과되는 패턴. 구 sidecar 모델(BUG-20260509-6A4F)에서 확인됨.
    - `atexit.register(lambda: open(sys.argv[2], "w").write(fake_json))` — test_code가 sys.argv[2](사이드카 경로)를 읽어 atexit 핸들러로 런너가 기록한 결과를 덮어쓰는 패턴.
    - 근본 원인: 출력 채널은 분리했으나 신뢰 경계(프로세스)가 분리되지 않음 — 동일 프로세스 내 globals, argv, atexit, module state 공유.
    - 대응 (현재 모델): runner/result-channel 접근 자체를 strict AST policy에서 차단한다. `__main__`, `atexit`, `inspect`, `os`, `sys.argv`, `sys.modules`, `getattr`, `setattr` 등은 runner 실행 전 hard-reject된다. nonce는 stdin으로만 runner에 전달되고 결과는 nonce JSON line으로 검증한다.
    - 탐지 기준: `validate_test_evidence()` 내부에 runner 전역 `_result_path`/`_exec_assert_count`, sidecar JSON, uuid4 `report_path`, stderr unittest 텍스트 파싱 구조가 존재하면 `critical_flaw: "SHARED_PROCESS_TRUST_BOUNDARY"` 기록.

### 적용 규칙 추가

- dev-agent는 `validate_test_evidence()` 수정 시 sidecar 파일(`report_path`, `uuid4`), runner 전역 result path/counter, stderr unittest 텍스트 파싱, 공개 stdout marker 구조를 재도입하면 QA PD FAIL 처리.
- PM은 `validate_test_evidence` 관련 micro_task에서 `<acceptance_criteria>`에 "import __main__ 조작 차단 (Test 37), atexit + failing 차단 (Test 38), atexit no TestCase 차단 (Test 39)" 명시 의무.
- test-harness-agent는 `<test_code>` 내에서 `import __main__` 및 `atexit.register()` 패턴으로 검증을 우회하려는 시도가 AST gate와 완전 프로세스 격리로 차단됨을 인지해야 한다.
