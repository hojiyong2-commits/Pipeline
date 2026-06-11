# 레포 전체 레거시/데드코드 감사 보고서

**pipeline_id:** IMP-20260611-6574

---

## 1. 감사 목적

이 보고서는 현재 SSoT(Single Source of Truth) 기준선과 비교하여 레포 전체에 남아 있는 레거시 패턴, 데드코드, 잘못된 경로 참조를 식별하고 5가지 분류 기준으로 정리합니다.

코드를 직접 수정하지 않으며, 이후 별도 IMP 파이프라인에서 삭제/수정 작업의 근거 자료로 활용됩니다.

---

## 2. 감사 일자 및 현재 SSoT 기준선

- **감사 일자:** 2026-06-11
- **기준 커밋:** `17ca1bdeb9b501a4581193c4c82ffa9abf517b75` (impl/IMP-20260611-6574)

### 현재 SSoT 기준선 (IMP-20260610-8C3B 및 IMP-20260610-982B 이후)

| 항목 | 현재 기준 |
|---|---|
| 완료 판정 방식 | Three-Gate + Option A phase attestation (Technical / Oracle / GitHub CI / Human ACCEPT) |
| PM 에이전트 구조 | pm-planner-agent + pipeline-manager-agent 분리 (planner receipt + manager receipt 별도 필수) |
| Verification JSON | `human_acceptance_packet.json` SSoT 기반 Protocol Consistency Guard D/F 검사 |
| Nonce Gate | `gates request-accept` 일회용 코드 -> `gates accept --acceptance-code` 필수 |
| AC Tracking | `step_plan.xml`의 `<acceptance_criteria>` + MT `covers_ac` 연결 강제 |
| Module Gate | `module integrate PASS` + ac_completeness cache 필수 |
| request-accept 스냅샷 | acceptance snapshot atomic creation (IMP-20260610-8C3B) |
| Codex Review Gate | GPT-5.5 고정, plan/scope/code/hygiene/pr/rca 6단계 |

### 폐기된 경로 (레거시)

- `pipeline.py harness --score ...` -- 현재 [THREE GATE BLOCKED]로 차단
- numeric final score (BUILD+QA 합산) 기반 COMPLETE
- `test_results.jsonl` 기반 COMPLETE 선언
- 단일 PM 에이전트 흐름 (`pm-agent` 단독, planner/manager 분리 없음)
- Phase 6->7 중간 사용자 확인 질문 패턴

---

## 3. 감사 범위

### 포함 대상

- `pipeline.py` (레거시 함수 및 호출 경로)
- `.claude/agents/*.md` (에이전트 규칙 문서)
- `AGENTS.md` (에이전트 구성표 및 경로 참조)
- `.github/workflows/ci.yml` (CI 단계 정의)
- `.github/pull_request_template.md` (PR 템플릿)
- `tests/**` (테스트 파일)
- `tests/oracles/**` (오라클 파일)

### 제외 대상

- `.pipeline/**`, `pipeline_outputs/**`, `dist/`, `build/`, `cache/`, `temp/` 출력 디렉토리
- 임시 에이전트 (`_temp_` 접미사 파일)
- 워크트리 디렉토리 (`.claude/worktrees/`)

---

## 4. 분류 기준

| 분류 | 정의 |
|---|---|
| **DELETE_CANDIDATE_HIGH** | 현재 SSoT 기준선과 직접 충돌하거나 이미 CLI에서 차단된 레거시 완료 경로. 그대로 두면 혼란이나 오작동 위험 |
| **DELETE_CANDIDATE_LOW** | 사용되지 않는 orphaned 테스트 fixture, 더 이상 참조되지 않는 actual.json, 실질 기능이 없는 코드 경로. 즉시 위험하지 않지만 불필요한 노이즈 |
| **MODIFY_CANDIDATE** | 현재 SSoT 기준과 불일치하는 경로 참조, 구식 설명, 또는 업데이트가 필요한 레거시 표현. 기능은 동작하나 혼동 유발 가능 |
| **PRESERVE** | 현재 SSoT 기준선에 부합하며 정상 동작하는 코드/문서. 변경 불필요 |
| **NEEDS_INVESTIGATION** | 레거시 여부를 단독으로 판단하기 어려운 패턴. 사용 빈도, 호출 경로 추적이 필요하거나 사용자 결정이 요구됨 |

---

## 5. DELETE_CANDIDATE_HIGH -- 고위험 삭제 후보

현재 SSoT 기준선과 직접 충돌하거나, 이미 차단된 레거시 완료 경로입니다.

### DH-1: `pipeline.py` -- `cmd_harness` 함수 (레거시 harness CLI 진입점)

- **파일:** `pipeline.py`
- **위치:** 줄 7216 -- `def cmd_harness(args: argparse.Namespace) -> None:`
- **레거시 판정 근거:** 현재 이 함수는 `--score` 인자를 받으면 즉시 `[THREE GATE BLOCKED]` 오류를 발생시킵니다. 함수 자체는 이미 "레거시 완료 경로 거부"를 구현하고 있으나, CLI에 `harness` 서브파서(`p_harness`)가 여전히 등록되어 있습니다 (줄 18399: `p_harness.add_argument("--score", ...)`). 에이전트나 자동화 스크립트가 `harness --score`를 호출할 때 BLOCKED 대신 `--score` 인자가 없다는 파서 에러로 먼저 실패할 수 있어 혼란을 줄 수 있습니다.
- **현재 SSoT 기준:** Three-Gate (`gates technical` / `gates oracle` / `gates github-ci` / `gates accept`)가 유일한 완료 경로

### DH-2: `pipeline.py` -- `_parse_harness_report_et` 함수

- **파일:** `pipeline.py`
- **위치:** 줄 114 -- `def _parse_harness_report_et(clean_text: str) -> "Optional[Any]":`
- **레거시 판정 근거:** 구 `<harness_report>` XML 파싱 함수입니다. 현재 test-harness-agent는 `<harness_diagnostic>` 형식을 사용하고, 숫자 점수 기반 완료 판정 자체가 폐기되었습니다. `_parse_harness_report_et`가 실제로 호출되는지 grep 결과 13577줄(TMP-HARNESS-AUTO 테스트 전용 코드)에서만 간접 참조됩니다.
- **현재 SSoT 기준:** `<harness_diagnostic>` 형식이 현행 표준. `<harness_report>` 파싱은 불필요

### DH-3: `pipeline.py` -- `p_harness` 서브파서 등록 (줄 18399)

- **파일:** `pipeline.py`
- **위치:** 줄 18399 -- `p_harness.add_argument("--score", required=True, ...)`
- **레거시 판정 근거:** `harness --score` 호출 시 `[THREE GATE BLOCKED]`로 차단하는 것이 현재 동작이지만, 파서에 `required=True`로 `--score`가 등록되어 있어 `harness` 없이 호출 시 파서 에러가 먼저 발생합니다. 이는 혼란스러운 에러 메시지를 생성합니다. `harness` 서브파서 자체를 제거하거나 더 명확한 에러로 대체하는 것이 바람직합니다.

### DH-4: `AGENTS.md` -- pm-planner-agent, pipeline-manager-agent 경로가 `.Codex/agents/` 로 잘못 기재됨

- **파일:** `AGENTS.md`
- **위치:** 줄 62-63 -- `| PM Planner Agent | .Codex/agents/pm-planner-agent.md |` 및 `| Pipeline Manager Agent | .Codex/agents/pipeline-manager-agent.md |`
- **레거시 판정 근거:** 실제 파일 위치는 `.claude/agents/`이지만 `.Codex/agents/`로 잘못 기재되어 있습니다. 또한 `pm-planner-agent.md`와 `pipeline-manager-agent.md` 파일 자체가 `.claude/agents/`에 존재하지 않습니다. AGENTS.md는 존재하지 않는 에이전트를 참조합니다.
- **현재 SSoT 기준:** CLAUDE.md "PM Planner / Pipeline Manager Spawn Chain" 섹션에 pm-planner-agent와 pipeline-manager-agent가 명시적으로 요구되며, 모든 파이프라인에서 두 receipt가 필수

---

## 6. DELETE_CANDIDATE_LOW -- 저위험 삭제 후보

사용되지 않거나 orphaned 상태인 산출물입니다.

### DL-1: `tests/oracles/BUG-20260521-C675/` -- `expected.json` 없는 actual-only 케이스 5개

- **파일:** `tests/oracles/BUG-20260521-C675/test_t001_ab1_blocked/actual.json` 외 4개
- **위치:** `tests/oracles/BUG-20260521-C675/` 하위 5개 케이스 디렉토리
- **레거시 판정 근거:** 각 케이스 디렉토리에 `actual.json`만 있고 `expected.json` 및 `input.json`이 없습니다. oracle gate가 이 케이스를 검증할 수 없으며 단순 스냅샷 로그로만 존재합니다.
- **위험도:** 낮음 -- 검증 대상이 아닌 단순 로그로 취급됨

### DL-2: `tests/oracles/IMP-20260521-90F4/` -- actual_output.json만 있는 케이스 4개

- **파일:** `tests/oracles/IMP-20260521-90F4/error_packet_files_extra_file/actual_output.json` 외 3개
- **위치:** `tests/oracles/IMP-20260521-90F4/` 내 `error_packet_files_extra_file`, `normal_packet_files_exact_match`, `normal_phase_attestation_run_id_not_confused`, `edge_total_count_distinguished` 케이스
- **레거시 판정 근거:** `actual_output.json`만 있고 `expected.json`이 없는 케이스가 혼재합니다. expected 없는 케이스는 oracle 검증에 활용되지 않습니다.
- **위험도:** 낮음 -- 단순 노이즈, 오라클 오작동 없음

### DL-3: 프로젝트 루트 `codex_review_result.json` -- 파이프라인 간 잔류 임시 파일

- **파일:** `codex_review_result.json`
- **위치:** 프로젝트 루트
- **레거시 판정 근거:** 파이프라인별로 재생성되는 임시 artifact입니다. `.gitignore`에 명시적으로 제외되어 있지 않으면 이전 파이프라인의 결과가 잔류하여 새 파이프라인의 Codex gate를 오염시킬 수 있습니다.
- **위험도:** 낮음 -- pipeline.py가 매 파이프라인 실행 시 덮어씀

---

## 7. MODIFY_CANDIDATE -- 수정 필요 후보

현재 SSoT와 불일치하거나 혼동을 유발하는 패턴입니다.

### MC-1: `AGENTS.md` -- 에이전트 파일 경로 `.Codex/agents/` 오기재 (모든 에이전트 항목)

- **파일:** `AGENTS.md`
- **위치:** 줄 62-75 (에이전트 구성 표 전체)
- **수정 근거:** 표의 모든 에이전트 파일 경로가 `.Codex/agents/`로 기재되어 있으나 실제 경로는 `.claude/agents/`입니다. 에이전트 경로 불일치는 Claude Code가 agents를 참조할 때 올바른 파일을 찾지 못하는 원인이 됩니다.
- **현재 SSoT:** `.claude/agents/` (CLAUDE.md "에이전트 구성" 표)

### MC-2: `AGENTS.md` -- pm-planner-agent.md, pipeline-manager-agent.md 파일 미존재

- **파일:** `AGENTS.md` (참조 문서), `.claude/agents/` (파일 시스템)
- **위치:** `.claude/agents/pm-planner-agent.md` 및 `.claude/agents/pipeline-manager-agent.md` 부재
- **수정 근거:** AGENTS.md와 CLAUDE.md 모두 `pm-planner-agent`와 `pipeline-manager-agent`를 별도 에이전트로 정의하지만 실제 `.claude/agents/` 디렉토리에는 이 두 파일이 없습니다. 현재 `pm-agent.md`가 호환성 문서로 기능하고 있으나, 신규 파이프라인은 분리된 두 에이전트 파일이 필요합니다.
- **현재 SSoT:** CLAUDE.md "PM Planner / Pipeline Manager Spawn Chain" -- 두 에이전트 파일이 모두 존재해야 함

### MC-3: `.claude/agents/pm-agent.md` -- "단일 PM 에이전트 흐름" 설명이 현재 분리 흐름과 혼재

- **파일:** `.claude/agents/pm-agent.md`
- **위치:** 줄 1-5 (frontmatter 및 첫 설명), 줄 285-299 (Phase별 책임 행동 표)
- **수정 근거:** `pm-agent.md`는 `mode: pipeline_manager_round1/round2` 방식을 모두 서술합니다. CLAUDE.md는 이 파일을 "호환성 문서"로 명시하지만, 에이전트가 실제로 pm-agent.md를 플래너/매니저 양쪽으로 사용하는 경우 혼동이 발생할 수 있습니다.
- **현재 SSoT:** pm-planner-agent(계획 전용) + pipeline-manager-agent(실행 관리 전용) 분리

### MC-4: `pipeline.py` 줄 2277 -- "pm" 레거시 backward compat 주석

- **파일:** `pipeline.py`
- **위치:** 줄 2277 -- `# "pm" is kept for legacy backward compat; new pipelines use "pm_planner" + "pipeline_manager"`
- **수정 근거:** 현재 모든 파이프라인에서 planner + manager 분리가 필수이므로, "new pipelines use" 문구를 "all pipelines must use"로 강화하는 것이 더 정확한 SSoT 반영입니다.

### MC-5: `tests/test_legacy_cleanup_b0ab.py` -- harness --score 차단 테스트 파서 에러 가능성

- **파일:** `tests/test_legacy_cleanup_b0ab.py`
- **위치:** 줄 82-95 -- `test_harness_score_blocked`
- **수정 근거:** `harness --score` 차단 자체는 올바른 동작이나, 현재 파서가 `--score required=True`로 등록되어 있어 실제 테스트에서 파서 에러(argparse)가 먼저 발생하고 `[THREE GATE BLOCKED]` 메시지가 보이지 않을 수 있습니다. 테스트가 올바른 오류 메시지를 검증하는지 확인 후 수정 고려.

---

## 8. PRESERVE -- 보존

현재 SSoT 기준선에 부합하며 정상 동작하는 항목입니다.

### PR-1: `.github/workflows/ci.yml` -- Three-Gate 및 phase attestation CI 단계

- **위치:** 줄 40-165 (구현 PR 위생 검사, Phase attestation 검사, Secrets Boundary 검사 등)
- **보존 근거:** 현재 SSoT 기준선에 완전히 부합합니다. preflight-pr-impl, preflight-pr, secrets 검사가 hard gate로 올바르게 구성되어 있습니다.

### PR-2: `.github/workflows/ci.yml` -- pm-planner-agent / pm-agent 양쪽 수용 (줄 454-456)

- **위치:** 줄 454-456
- **보존 근거:** PM split에서 `pm-planner-agent` 및 구형 `pm-agent` 모두를 허용하는 하위 호환 로직입니다. 이 유연성은 현재 과도기 상태에서 필요하며 의도적인 설계입니다.

### PR-3: `pipeline.py` -- `cmd_harness` 내 [THREE GATE BLOCKED] 차단 로직 (줄 7227-7235)

- **위치:** 줄 7227-7235
- **보존 근거:** 현재 SSoT 기준에 맞게 레거시 경로를 올바르게 차단합니다. 함수의 차단 내용 자체는 보존되어야 합니다.

### PR-4: `tests/test_legacy_cleanup_b0ab.py` -- harness --score 차단 검증 테스트

- **위치:** 줄 82-95
- **보존 근거:** 레거시 harness 경로가 차단됨을 회귀 테스트로 보장합니다. 삭제하면 미래에 레거시 경로가 우발적으로 복구될 위험이 있습니다.

### PR-5: `.github/pull_request_template.md` -- 현재 구조 전체

- **위치:** 줄 1-52
- **보존 근거:** 작업 요약, 사용자가 확인할 결과물, 기대 결과와 실제 결과, 중요한 선택과 트레이드오프, 검증 섹션을 포함한 현재 구조가 Final Acceptance Readiness Gate (IMP-20260519-E979) 요구사항과 완전히 일치합니다.

### PR-6: `tests/oracles/IMP-20260611-6574/` -- 현재 파이프라인 오라클

- **위치:** `tests/oracles/IMP-20260611-6574/normal_report_structure/`, `edge_delete_high_present/`
- **보존 근거:** 현재 파이프라인 oracle이며 삭제/수정 금지 대상입니다.

---

## 9. NEEDS_INVESTIGATION -- 조사 필요

레거시 여부를 단독으로 판단하기 어렵거나 사용자 결정이 필요한 항목입니다.

### NI-1: `pipeline.py` 줄 13577 -- TMP-HARNESS-AUTO 테스트 전용 코드

- **파일:** `pipeline.py`
- **위치:** 줄 13577 근처 -- TMP-HARNESS-AUTO 관련 STATE_FILE 덮어쓰기 로직
- **조사 필요 이유:** 이 코드가 현재 어떤 테스트에서 호출되는지, 삭제해도 테스트 suite가 통과하는지 확인 필요합니다. 테스트 격리를 위한 레거시 메커니즘일 수 있습니다.

### NI-2: `.claude/agents/shared/` 디렉토리 파일들

- **파일:** `.claude/agents/shared/anti_gaming_rules.md`, `self_evolving_log.md`, `tech_tree_examples.md`, `Global_Wiki.md`
- **위치:** `.claude/agents/shared/`
- **조사 필요 이유:** 이 파일들이 현재 어느 에이전트에서 실제로 참조되는지 확인이 필요합니다. 일부는 최신 프로토콜에서 참조가 줄었을 수 있습니다.

### NI-3: `tests/fixtures/` 디렉토리 -- 오래된 fixture 파일들

- **파일:** `tests/fixtures/` 전체
- **위치:** `tests/fixtures/`
- **조사 필요 이유:** 어느 테스트가 이 fixture를 참조하는지, 더 이상 사용되지 않는 fixture가 있는지 확인이 필요합니다.

### NI-4: `tests/oracles/IMP-20260519-E979/` -- actual.json 파일들

- **파일:** `tests/oracles/IMP-20260519-E979/` 내 여러 `actual.json`
- **위치:** `normal_body_incomplete_blocked/actual.json`, `normal_draft_pr_blocked/actual.json` 등
- **조사 필요 이유:** `actual.json`은 테스트 실행 산출물입니다. git에 커밋된 경우 의도적으로 포함된 것인지, 실수로 포함된 것인지 확인이 필요합니다.

---

## 10. 감사 요약 및 후속 권고

### 우선순위별 요약

| 우선순위 | 분류 | 항목 수 | 대표 항목 |
|---|---|---|---|
| 높음 | DELETE_CANDIDATE_HIGH | 4 | DH-1(harness cmd_harness), DH-2(_parse_harness_report_et), DH-3(p_harness parser), DH-4(AGENTS.md 경로 오기재) |
| 중간 | MODIFY_CANDIDATE | 5 | MC-1(AGENTS.md 경로), MC-2(pm-planner 파일 미존재), MC-3(pm-agent.md 혼재), MC-4(주석 강화), MC-5(테스트 검증) |
| 낮음 | DELETE_CANDIDATE_LOW | 3 | DL-1(C675 actual-only), DL-2(90F4 actual-only), DL-3(codex_review_result.json) |
| 조사 필요 | NEEDS_INVESTIGATION | 4 | NI-1(TMP-HARNESS-AUTO), NI-2(shared/ 파일), NI-3(fixtures/), NI-4(E979 actual.json) |
| 보존 | PRESERVE | 6 | PR-1~PR-6 |

### 후속 IMP 권고

1. **(긴급)** `AGENTS.md` 경로 수정 (`.Codex/agents/` -> `.claude/agents/`) 및 `pm-planner-agent.md`, `pipeline-manager-agent.md` 파일 생성 -- PM split이 실제로 작동하려면 두 파일이 필요합니다.

2. `pipeline.py`의 `harness` 서브파서 제거 또는 `--score` 없이도 즉시 BLOCKED를 반환하도록 수정 -- 현재 `required=True` 파서가 잘못된 에러 메시지를 생성합니다.

3. `tests/oracles/BUG-20260521-C675/`와 `IMP-20260521-90F4/`의 orphaned actual.json 케이스 정리 -- 현재 oracle gate에 영향을 주지 않지만 노이즈 제거를 위해 권고합니다.

4. `pm-agent.md` 정리 -- 현재 "호환성 문서"로 명시되어 있으나, planner/manager 분리가 안정화된 후 deprecation notice를 강화하는 것을 권고합니다.

---

*이 보고서는 FAST_ANALYSIS 프로파일 하에 코드를 수정하지 않고 생성되었습니다.*
*생성 에이전트: pipeline-manager-agent (IMP-20260611-6574 MT-1)*
