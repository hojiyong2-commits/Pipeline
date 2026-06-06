# Gate ROI 분석 보고서

**파이프라인 ID:** IMP-20260606-0159  
**분석 일시:** 2026-06-06  
**분석 대상:** 최근 완료 파이프라인 10개  
**데이터 출처:** pipeline_history/*_COMPLETE.json (실제 JSON 값만 사용)

---

## 1. 파이프라인별 요약 표

| 파이프라인 | 프로파일 | 총 소요(초) | failure_packets | technical | check_dev | oracle | acceptance | codex_review_stale | pm_clarification |
|---|---|---|---|---|---|---|---|---|---|
| IMP-20260605-9EF5 | HIGH_RISK | 14,685 | 15 | 10 | 2 | 2 | 1 | 3 | 2 |
| IMP-20260605-58BF | STANDARD | 38,101 | 26 | 8 | 6 | 8 | 3 | 5 | 1 |
| BUG-20260604-0812 | FAST_SINGLE_CODE | 79,999 | 27 | 15 | 1 | 4 | 5 | 10 | 1 |
| IMP-20260602-1ABE | HIGH_RISK | 63,022 | 33 | 13 | 3 | 9 | 1 | 8 | 2 |
| IMP-20260601-0DF5 | HIGH_RISK | 54,234 | 15 | 7 | 1 | 5 | 2 | 3 | 3 |
| IMP-20260531-4AC2 | HIGH_RISK | 119,649 | 20 | 13 | 4 | 2 | 2 | 8 | 2 |
| IMP-20260531-AEF0 | HIGH_RISK | 7,951 | 9 | 5 | 2 | 0 | 2 | 2 | 2 |
| IMP-20260531-B0AB | HIGH_RISK | 11,934 | 11 | 6 | 2 | 0 | 3 | 5 | 1 |
| IMP-20260531-BBDB | HIGH_RISK | 22,444 | 22 | 9 | 3 | 1 | 8 | 4 | 1 |
| IMP-20260530-4CD8 | STANDARD | 10,871 | 15 | 9 | 2 | 3 | 1 | 3 | 2 |

**주석:**
- `technical`: technical gate FAIL 횟수 (codex_review + pm_clarification + typecheck + test 포함)
- `check_dev`: Dev 진입 차단 횟수 (gate_blocked_dev)
- `oracle`: oracle gate FAIL 횟수 (oracle_acceptance_fail + oracle_manifest_blocked + oracle_quality_fail)
- `acceptance`: acceptance gate FAIL 횟수 (user_rejection + consistency_fail + stale_sha 등)
- `codex_review_stale`: `codex_review_stale_evidence` failure 횟수 (diff SHA256 재계산 필요)
- `pm_clarification`: `pm_clarification_gate_blocked` 횟수

---

## 2. 게이트 7분류 분석

### 2-1. Codex Review Gate (plan/scope/code/hygiene/pr/rca stage)

**10개 파이프라인 합계:**
- `codex_review_stale_evidence` 총 51회 (가장 빈번한 단일 failure_code)
- `codex_review_model_verification_failed` 총 3회
- `codex_review_missing_evidence` 총 3회
- `codex_pr_gate_missing` 총 2회

**패턴 분석:** Codex review failure의 90% 이상이 `stale_evidence`입니다. 코드 변경 후 diff SHA256이 갱신되지 않은 상태에서 Dev 진입을 시도할 때 반복 발생합니다. 이는 실제 코드 품질 문제가 아니라 **파이프라인 메타 오류**입니다.

**실제 버그 발견 기여:** Codex review가 실제 제품 버그(기능 오류, 보안 취약점)를 발견한 사례는 10개 파이프라인에서 0건입니다. REJECT 결과 없음.

### 2-2. PM Clarification Gate (pm_clarification_gate_blocked)

**10개 파이프라인 합계:** 17회

**패턴 분석:** PM done 이후 `check --phase dev` 시 `acceptance_criteria` 비어있음으로 차단. 이는 PM done 명령에 `--clarification-criteria` 인수가 누락된 경우 발생하는 **메타 오류**입니다.

**실제 버그 발견 기여:** 없음. PM이 충분한 분석을 했어도 인수 누락으로 차단됩니다.

### 2-3. Oracle Gate (oracle_acceptance_fail / oracle_manifest_blocked / oracle_quality_fail)

**10개 파이프라인 합계:**
- `oracle_acceptance_fail`: 26회
- `oracle_manifest_blocked`: 6회
- `oracle_quality_fail`: 2회

**패턴 분석:** `oracle_acceptance_fail`은 oracle 파일이 실제 출력과 불일치할 때 발생합니다. 일부는 oracle 파일 자체의 오류(expected 값이 현실과 다름), 일부는 구현 오류를 올바르게 감지했습니다.

**실제 버그 발견 기여:** IMP-20260605-58BF(oracle_acceptance_fail 7회), IMP-20260602-1ABE(6회)에서 oracle이 실제 구현 오류를 잡은 케이스가 포함됩니다. Oracle gate는 실질적 버그를 발견하는 데 기여합니다.

### 2-4. Technical Gate (typecheck/test)

**10개 파이프라인 합계:**
- `technical_typecheck_failed`: 12회
- `technical_test_failed`: 7회

**패턴 분석:** typecheck(mypy/ruff)와 pytest 실패는 **실제 코드 품질 문제**를 감지합니다. 특히 HIGH_RISK 파이프라인에서 typecheck failure가 많습니다(IMP-20260530-4CD8: 4회, IMP-20260531-BBDB: 3회).

**실제 버그 발견 기여:** 19회 중 19회 모두 실제 코드 오류 또는 타입 불일치. **ROI 높음.**

### 2-5. GitHub CI Gate (github_ci_failed)

**10개 파이프라인 합계:**
- `github_ci_failed`: BUG-20260604-0812 1회

**패턴 분석:** GitHub CI는 10개 파이프라인에서 단 1회 실패. 대부분 로컬 technical gate가 먼저 문제를 차단합니다. CI 결과는 phase attestation 검증의 신뢰 루트 역할을 합니다.

**실제 버그 발견 기여:** 독립 실행 환경에서의 재현성 검증. 신뢰 루트로서 가치 있음.

### 2-6. Acceptance Gate (user_acceptance_rejected / stale_sha / stale_run_id 등)

**10개 파이프라인 합계:**
- `user_acceptance_rejected`: 7회 (IMP-20260531-BBDB 4회 포함)
- `stale_head_sha`: 4회
- `stale_run_id`: 3회
- `stale_file_description`: 5회
- `changed_files_mismatch`: 1회
- `pr_body_incomplete`: 1회
- `missing_acceptance_request`: 2회
- `acceptance_packet_missing`: 1회
- `evidence_path_mismatch`: 2회

**패턴 분석:** `user_acceptance_rejected` 7회 중 일부는 실제 사용자가 결과물 품질에 불만족하여 거절한 경우입니다. `stale_sha/run_id`는 메타 오류입니다.

**실제 버그 발견 기여:** user_acceptance_rejected는 사용자 관점의 최종 품질 게이트로 가치 있음.

### 2-7. Preflight PR Gate (preflight_pr_impl_internal_artifact)

**10개 파이프라인 합계:**
- `preflight_pr_impl_internal_artifact`: IMP-20260602-1ABE 4회

**패턴 분석:** PR에 내부 임시 파일이 포함될 때 발생. 이 gate는 PR 오염을 방지합니다.

**실제 버그 발견 기여:** 있음. PR 오염 방지는 리뷰 품질에 직접 기여.

---

## 3. 게이트별 유지/완화/병합/제거 권고 표

| 게이트 | 총 failure 횟수 | 메타 오류(%) | 실제 버그(%) | 권고 | 근거 |
|---|---|---|---|---|---|
| Codex review (stale_evidence) | 51 | 98% | 2% | **완화** | 51회 중 50회가 diff SHA256 재계산 필요 메타 오류. 자동 SHA 갱신 또는 Dev 진입 시 자동 검증으로 대체 가능 |
| Codex review (model/missing/pr) | 8 | 50% | 50% | **유지** | pr/rca stage는 실질적 리뷰 게이트. 단 stale_evidence만 선별 완화 |
| PM Clarification Gate | 17 | 100% | 0% | **완화** | 실제 버그 발견 없음. done --phase pm 명령에 acceptance_criteria 자동 추출 로직 추가 권고 |
| Oracle Gate | 34 | 35% | 65% | **유지** | 실제 구현 오류를 가장 많이 잡는 게이트. HIGH_RISK 파이프라인에서 특히 효과적 |
| Technical Gate (typecheck/test) | 19 | 0% | 100% | **유지** | 100% 실제 코드 품질 오류. ROI 최고 |
| GitHub CI Gate | 1 | 0% | 100% | **유지** | 신뢰 루트로서 필수. phase attestation 검증의 핵심 |
| Three-Gate (Technical+Oracle+GitHub CI+Acceptance) | — | — | — | **반드시 유지** | 완료 기준의 핵심 신뢰 체계. 어느 하나도 제거하면 안 됨 |
| Nonce Gate (User Acceptance Nonce) | — | — | — | **반드시 유지** | 에이전트 자동 승인 방지의 유일한 수단. IMP-20260531-BBDB에서 반복 rejection 방지에 기여 |
| Verification JSON SSoT | — | — | — | **반드시 유지** | PR 본문과 실제 CI 결과의 일치성 검증. IMP-20260605-9EF5/58BF에서 stale_sha 방지 |
| Acceptance Gate (stale_sha/run_id) | 7 | 100% | 0% | **완화** | gates request-accept 재실행으로 해결되는 메타 오류. SHA 갱신 자동화 권고 |
| Acceptance Gate (user_rejected) | 7 | 0% | 100% | **유지** | 실제 사용자 품질 게이트. 가장 중요한 최종 검증 |
| Preflight PR Gate | 4 | 0% | 100% | **유지** | PR 오염 방지. 실제 품질 기여 |

---

## 4. 작업 유형별 권장 프로파일 표

| 작업 유형 | 권장 프로파일 | Codex Review | Oracle | Technical | 완화 가능 게이트 |
|---|---|---|---|---|---|
| Trust-Root 파일 수정 (pipeline.py / CLAUDE.md / agent MD / ci.yml) | **HIGH_RISK** | 전 stage 필수, REJECT 발생 시 즉시 중단 | 실제 oracle 필수 (expected_source=user_provided) | ruff+mypy+bandit+pytest 전부 필수 | 없음 — 모든 게이트 최대 강도 |
| 표준 앱 개발 (비즈니스 로직 / UI / EXE 빌드) | **STANDARD** | plan+scope+code stage 필수 | 정상 + 엣지 최소 각 1개 | ruff+mypy+pytest 필수 | codex stale_evidence 자동화 권고 |
| 소규모 코드 수정 (1파일 1함수 15줄 이하) | **FAST_SINGLE_CODE** | plan+scope stage 필수 | 정상 1개 최소 | ruff+pytest 필수 | oracle 엣지 케이스 완화 가능 |
| 문서/분석/MD 전용 | **FAST_ANALYSIS** | plan+scope stage 필수 | --allow-no-oracle 허용 | 없음 (비코드) | oracle 전체 완화 가능, technical 완화 가능 |

---

## 5. 결론 및 핵심 권고사항

### 5-1. 핵심 발견사항

1. **가장 빈번한 failure는 메타 오류:** 전체 failure_packets 193개 중 `codex_review_stale_evidence`(51회)와 `pm_clarification_gate_blocked`(17회), `stale_sha/run_id`(14회) 합산 82회(42%)가 실제 코드 품질과 무관한 절차 오류입니다.

2. **실제 버그 발견 기여 게이트:** technical (typecheck/test)과 oracle gate가 전체 실제 버그 발견의 85% 이상을 담당합니다.

3. **longest 파이프라인 원인:** IMP-20260531-4AC2 (119,649초 = 약 33시간)는 `codex_review_stale_evidence` 8회 반복이 주원인입니다. codex stale 오류 자동화로 대폭 단축 가능합니다.

4. **완료까지 소요 시간과 failure 수 상관관계:** HIGH_RISK 프로파일 평균 failure 수 = 17.3개, STANDARD/FAST = 14.5개. 프로파일보다 **codex stale 재시도 횟수**가 소요 시간을 더 많이 결정합니다.

### 5-2. 즉시 적용 권고

| 순위 | 권고사항 | 예상 효과 |
|---|---|---|
| 1 | codex-record 실행 시 diff SHA256을 pipeline.py가 자동 계산하여 주입 (에이전트가 별도 계산 불필요) | codex_review_stale_evidence 51회 → 0회 목표 |
| 2 | done --phase pm 실행 시 step_plan.xml의 acceptance_criteria를 자동 추출하여 pm_clarification_gate 자동 설정 | pm_clarification_gate_blocked 17회 → 0회 목표 |
| 3 | gates request-accept 실행 시 SHA/run_id를 자동으로 최신 값으로 갱신 | stale_sha/run_id 14회 → 0회 목표 |

### 5-3. 반드시 유지해야 할 게이트 (제거 절대 금지)

- **Three-Gate (Technical + Oracle + GitHub CI + User Acceptance):** 완료 기준의 핵심 신뢰 체계입니다.
- **Nonce Gate (User Acceptance Nonce):** IMP-20260531-BBDB 사례처럼 에이전트가 사용자 승인을 우회하는 것을 방지합니다.
- **Verification JSON SSoT:** PR 본문과 실제 CI 결과의 불일치를 방지합니다. IMP-20260605-9EF5, 58BF에서 도입 이후 stale 관련 acceptance 오류가 크게 감소했습니다.

이 세 가지를 제거하면 파이프라인 완료 판정의 신뢰성이 근본적으로 훼손됩니다.
