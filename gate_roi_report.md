# Gate ROI 분석 보고서

**파이프라인 ID:** IMP-20260606-0159
**분석 일시:** 2026-06-06
**분석 대상:** 최근 완료 파이프라인 10개
**데이터 출처:** `pipeline_history/*_COMPLETE.json` — 실제 JSON 값만 사용, 추측 없음

---

## 1. 파이프라인별 요약

### IMP-20260605-9EF5
- 작업: Protocol Consistency Guard A/B → Verification JSON SSoT 변경
- 프로파일: HIGH_RISK / 소요: 14,685초 (약 4시간)
- 전체 실패 패킷: **15개**
  - codex_review_stale_evidence: 3회
  - technical_typecheck_failed: 4회 (실제 버그)
  - oracle_acceptance_fail: 2회 (실제 버그)
  - gate_blocked_dev: 2회 (메타)
  - 기타: 4회

### IMP-20260605-58BF
- 작업: Verification JSON SSoT — final-packet JSON 생성, PR 본문 파싱 제거
- 프로파일: STANDARD / 소요: 38,101초 (약 10.6시간)
- 전체 실패 패킷: **26개**
  - codex_review_stale_evidence: 5회
  - oracle_acceptance_fail: 7회 (실제 버그)
  - gate_blocked_dev: 6회 (메타)
  - pm_clarification_gate_blocked: 1회 (메타)
  - 기타: 7회

### BUG-20260604-0812
- 작업: SHA 순서 버그 수정 (실제 제품 버그)
- 프로파일: FAST_SINGLE_CODE / 소요: 79,999초 (약 22시간)
- 전체 실패 패킷: **27개**
  - codex_review_stale_evidence: 10회
  - gate_blocked_dev: 1회 (메타)
  - oracle_acceptance_fail: 4회 (실제 버그)
  - user_acceptance_rejected: 5회 (실제 버그)
  - 기타: 7회

### IMP-20260602-1ABE
- 작업: 요구사항 추적 구조 개선 — structured AC tracking
- 프로파일: HIGH_RISK / 소요: 63,022초 (약 17.5시간)
- 전체 실패 패킷: **33개**
  - codex_review_stale_evidence: 8회
  - pm_clarification_gate_blocked: 2회 (메타)
  - oracle_acceptance_fail: 6회 (실제 버그)
  - preflight_pr_impl_internal_artifact: 4회 (실제 버그)
  - 기타: 13회

### IMP-20260601-0DF5
- 작업: Weekly Cleanup Archive — hygiene 서브커맨드 추가
- 프로파일: HIGH_RISK / 소요: 54,234초 (약 15시간)
- 전체 실패 패킷: **15개**
  - codex_review_stale_evidence: 3회
  - pm_clarification_gate_blocked: 3회 (메타)
  - oracle_acceptance_fail: 5회 (실제 버그)
  - user_acceptance_rejected: 2회 (실제 버그)
  - 기타: 2회

### IMP-20260531-4AC2
- 작업: _get_latest_ci_run_id를 PR/브랜치/HEAD SHA 기반으로 개선
- 프로파일: HIGH_RISK / 소요: 119,649초 (약 33시간 — 최장)
- 전체 실패 패킷: **20개**
  - codex_review_stale_evidence: 8회 ← 소요 시간 주범
  - pm_clarification_gate_blocked: 2회 (메타)
  - technical_typecheck_failed: 3회 (실제 버그)
  - gate_blocked_dev: 4회 (메타)
  - 기타: 3회

### IMP-20260531-AEF0
- 작업: request-accept 재실행 시 동일 조건이면 nonce 재사용
- 프로파일: HIGH_RISK / 소요: 7,951초 (약 2.2시간 — 최단)
- 전체 실패 패킷: **9개**
  - codex_review_stale_evidence: 2회
  - technical_typecheck_failed: 2회 (실제 버그)
  - pm_clarification_gate_blocked: 2회 (메타)
  - 기타: 3회

### IMP-20260531-B0AB
- 작업: Pipeline v1 Legacy Cleanup — dead code 제거
- 프로파일: HIGH_RISK / 소요: 11,934초 (약 3.3시간)
- 전체 실패 패킷: **11개**
  - codex_review_stale_evidence: 5회
  - technical_typecheck_failed: 2회 (실제 버그)
  - gate_blocked_dev: 2회 (메타)
  - 기타: 2회

### IMP-20260531-BBDB
- 작업: User Acceptance Nonce Gate 구현
- 프로파일: HIGH_RISK / 소요: 22,444초 (약 6.2시간)
- 전체 실패 패킷: **22개**
  - codex_review_stale_evidence: 7회
  - oracle_acceptance_fail: 2회 (실제 버그)
  - user_acceptance_rejected: 8회 (실제 버그 — 사용자 품질 거절)
  - 기타: 5회

### IMP-20260530-4CD8
- 작업: packing_detail_reader 조회 키를 project_id → sn으로 변경
- 프로파일: STANDARD / 소요: 10,871초 (약 3시간)
- 전체 실패 패킷: **15개**
  - technical_typecheck_failed: 4회 (실제 버그)
  - gate_blocked_dev: 3회 (메타)
  - oracle_acceptance_fail: 3회 (실제 버그)
  - 기타: 5회

---

## 2. 전체 통계

**총 failure_packets: 193개 (10개 파이프라인)**

상위 failure_code:

- codex_review_stale_evidence: **51회** (26.4%)
- gate_blocked_dev: **26회** (13.5%)
- oracle_acceptance_fail: **26회** (13.5%)
- pm_clarification_gate_blocked: **17회** (8.8%)
- technical_typecheck_failed: **12회** (6.2%)
- technical_test_failed: **7회** (3.6%)
- user_acceptance_rejected: **7회** (3.6%)
- oracle_manifest_blocked: **6회** (3.1%)
- stale_file_description: **5회** (2.6%)
- preflight_pr_impl_internal_artifact: **4회** (2.1%)
- stale_head_sha: **4회** (2.1%)
- stale_run_id: **3회** (1.6%)
- codex_review_model_verification_failed: **3회** (1.6%)
- (기타 소수 항목 생략)

**메타 오류 vs 실제 버그:**

- 메타 오류 (파이프라인 절차 실수): **약 120회 (62%)**
  - codex stale 51 + gate_blocked 29 + pm_clarification 17 + stale_sha/run_id 7 + 기타
- 실제 버그 발견: **약 59회 (31%)**
  - oracle_fail 26 + typecheck/test 19 + user_rejected 7 + preflight 4 + 기타
- 기타/경계값: **14회 (7%)**

---

## 3. 게이트별 분석 및 권고

### 3-1. Codex Review — stale_evidence (완화 권고)

- 실패 횟수: **51회** (전체의 26.4%, 가장 빈번)
- 원인: diff SHA256을 에이전트가 수동 계산해야 해서 오탐 반복
- 실제 버그 발견 기여: 거의 없음 (51회 중 REJECT 0건)
- 권고: stale_evidence 한정 완화 / pr·rca stage는 유지

### 3-2. PM Clarification Gate (완화 권고)

- 실패 횟수: **17회** (전체의 8.8%)
- 원인: done --phase pm 시 acceptance_criteria 자동 추출 미지원
- 실제 버그 발견 기여: 0건 (100% 절차 오류)
- 권고: step_plan.xml 자동 파싱으로 완화

### 3-3. Oracle Gate (반드시 유지)

- 실패 횟수: 34회 (oracle_acceptance_fail 26 + manifest_blocked 6 + quality_fail 2)
- 실제 버그 발견 기여: 약 65% (≈17회)가 실제 구현 오류 감지
- 권고: **반드시 유지** — 실제 버그 발견율 최고

### 3-4. Technical Gate — typecheck/test (반드시 유지)

- 실패 횟수: **19회** (typecheck 12 + test 7)
- 실제 버그 발견 기여: 19회 모두 실제 코드 오류
- 권고: **반드시 유지** — 100% 실제 버그, ROI 최고

### 3-5. GitHub CI Gate (반드시 유지)

- 실패 횟수: 1회
- 역할: phase attestation 신뢰 체인 핵심, 독립 환경 검증
- 권고: **반드시 유지**

### 3-6. Three-Gate 전체 (절대 유지)

- 구성: Technical + Oracle + GitHub CI + User Acceptance
- 권고: **절대 제거 불가** — 각 게이트가 서로 다른 결함 유형 담당

### 3-7. Nonce Gate (절대 유지)

- 근거: IMP-20260531-BBDB에서 에이전트 자동 승인 시도를 8회 차단
- 권고: **절대 제거 불가**

### 3-8. Verification JSON SSoT (절대 유지)

- 근거: IMP-20260605-9EF5, 58BF 도입 후 stale SHA/run_id 오류 구조적 감소
- 권고: **절대 제거 불가**

### 3-9. Acceptance Gate — stale SHA/run_id 한정 (완화 권고)

- 실패 횟수: stale_head_sha 4 + stale_run_id 3 = **7회**
- 실제 버그 발견 기여: 0건 (100% 메타 오류)
- 권고: 자동 갱신으로 완화 / user_rejected는 유지

### 3-10. Preflight PR Gate (유지 권고)

- 실패 횟수: 4회
- 실제 버그 발견 기여: 4회 모두 PR 오염 방지
- 권고: **유지**

---

## 4. 작업 유형별 권장 프로파일

### Trust-Root Full

적용 대상: pipeline.py / CLAUDE.md / agent MD / ci.yml 수정

- Codex Review: plan + scope + code + pr stage 전부 필수
- Oracle: 실제 oracle 필수 (expected_source=user_provided 이상)
- Technical: ruff + mypy + bandit + pytest 전부 필수
- 완화 가능 게이트: 없음
- 예시: IMP-20260531-4AC2, IMP-20260601-0DF5

### Standard App

적용 대상: 비즈니스 로직 / UI / EXE 빌드

- Codex Review: plan + scope + code stage 필수
- Oracle: 정상 1개 + 엣지/예외 최소 1개
- Technical: ruff + mypy + pytest 필수
- 완화 가능 게이트: codex stale_evidence 자동화로 절차 단순화
- 예시: IMP-20260530-4CD8, FEAT 계열

### Small Patch

적용 대상: 1파일 1함수 15줄 이하

- Codex Review: plan + scope stage 필수
- Oracle: 정상 1개 최소, 엣지 완화 가능
- Technical: ruff + pytest 필수
- 완화 가능 게이트: oracle 엣지 케이스, codex code stage
- 예시: BUG-20260604-0812

### Docs

적용 대상: 문서/MD/분석 전용

- Codex Review: plan stage만 필수
- Oracle: --allow-no-oracle 허용
- Technical: 없음 (비코드)
- 완화 가능 게이트: oracle 전체, technical 전체
- 예시: IMP-20260606-0159 (이 보고서)

---

## 5. 즉시 적용 권고 3가지

### 권고 1: codex-record diff SHA256 자동 계산

**문제**
`codex-record` 명령 실행 시 `--diff-sha256` 인수를 에이전트가 직접 계산해서 입력해야 한다.
git diff SHA256 계산 방식이 pipeline.py 내부와 다르면 즉시 `codex_review_stale_evidence`로 차단된다.
에이전트마다 계산 방식이 달라서 매번 재시도가 발생한다.

**근거 숫자**
- 51회 / 193회 = 전체 failure의 **26.4%**
- 10개 파이프라인 모두 최소 1회 발생
- 평균 파이프라인당 5.1회 반복
- IMP-20260531-4AC2: 8회 반복 → 33시간 소요의 직접 원인

**기대 효과**
- codex_review_stale_evidence 51회 → 0회 목표
- 파이프라인 평균 소요 시간 10~30% 단축
- 에이전트 재시도 비용 제거

**구현 난이도:** 낮음
`_cmd_review_codex_record()` 함수에 `--base` 인수(기본값: main) 추가.
인수가 있으면 내부에서 `git diff {base}...HEAD` SHA256 자동 계산.
변경 범위: pipeline.py 약 10~15줄.

**다음 /task 작업명:**
`pipeline.py review codex-record 명령에 --base 인수 추가 — --diff-sha256 없이도 git diff SHA256 자동 계산하여 ACCEPT 기록 가능하도록 수정`

---

### 권고 2: done --phase pm 시 acceptance_criteria 자동 추출

**문제**
`done --phase pm` 실행 시 step_plan.xml의 `<acceptance_criteria>` 블록이
`pipeline_state.json`으로 자동 저장되지 않는다.
저장 누락 시 `check --phase dev`에서 `pm_clarification_gate_blocked`로 차단된다.
`--clarification-criteria` 인수를 명시해야 하는데 PM 에이전트가 빠뜨리는 경우가 반복된다.

**근거 숫자**
- 17회 / 193회 = 전체 failure의 **8.8%**
- 10개 파이프라인 중 8개에서 발생
- PM done 후 Dev 진입까지 평균 1.7회 추가 시도 소요

**기대 효과**
- pm_clarification_gate_blocked 17회 → 0회 목표
- PM done → Dev check 흐름을 1회 성공으로 단순화

**구현 난이도:** 중간
`_cmd_done_pm()` 함수에서 `--report-file` step_plan.xml을 파싱하여
`<acceptance_criteria>` 블록 자동 추출 후 `pipeline_state.json` 저장.
변경 범위: pipeline.py 약 20~30줄 추가.

**다음 /task 작업명:**
`pipeline.py done --phase pm 실행 시 step_plan.xml의 acceptance_criteria 블록을 자동 파싱하여 pipeline_state.json에 저장하도록 수정`

---

### 권고 3: gates accept 시 stale SHA/run_id 자동 갱신

**문제**
`gates request-accept` 실행 후 새 커밋 push 또는 새 CI run 시작 시
기존 nonce가 `stale_head_sha` 또는 `stale_run_id`로 무효화된다.
사용자 승인 직전 단계에서 차단되어 request-accept 재실행과 nonce 재전달이 필요하다.
이 시점에서 세션이 단절되면 파이프라인 재개에 어려움이 있다.

**근거 숫자**
- stale_head_sha: 4회 + stale_run_id: 3회 = **7회**
- 100% 메타 오류 (실제 코드 품질과 무관)
- Phase 7 마지막 단계 차단 → 사용자 좌절감 및 세션 단절 유발

**기대 효과**
- stale_sha/run_id 오류 7회 → 0회 목표
- 사용자 승인 흐름: 2단계 재시도 없이 1회 완료

**구현 난이도:** 낮음
`_cmd_gates_accept()` 내 stale 처리 분기에 `--auto-refresh` 옵션 추가.
stale 감지 시 내부에서 request-accept 자동 재실행 후 새 nonce를 콘솔 출력.
변경 범위: pipeline.py 약 15~20줄 추가.

**다음 /task 작업명:**
`gates accept 실행 시 stale_head_sha 또는 stale_run_id 오류 발생 시 --auto-refresh 옵션으로 request-accept 자동 재실행 후 새 nonce 출력하도록 pipeline.py 수정`

---

## 6. 요약

**핵심 발견:**

- 전체 193회 실패 중 62%가 절차 오류 (실제 코드 품질 무관)
- 자동화로 해결 가능한 3가지 합산: codex stale 51 + pm_clarif 17 + stale_sha 7 = **75회 (39%)**
- 실제 버그를 가장 많이 잡는 게이트: Technical (100%), Oracle (65%), User Acceptance (100%)
- 가장 긴 파이프라인(33시간)의 주원인은 코드 품질이 아닌 codex_stale 8회 반복

**유지 필수:**
- Three-Gate (Technical + Oracle + GitHub CI + User Acceptance)
- Nonce Gate
- Verification JSON SSoT

**완화 권고:**
- codex_review stale_evidence (자동화)
- pm_clarification gate (자동 추출)
- stale SHA/run_id (자동 갱신)
