# 파이프라인 실사(dry run) 검증 기록 — 2026-06-17

## 목적

이 문서는 자기 진화형 다중 에이전트 파이프라인의 전체 흐름이 end-to-end로 실제 동작하는지 검증하기 위한 실사(dry run) 기록입니다. 검증 대상 흐름은 PM → Dev → QA → SEC → Build → Phase Attestation → External Gates → Architect 전 구간입니다. 실제 제품 코드를 바꾸지 않고, docs 파일 1개(`docs/pipeline_real_dry_run_20260617.md`) 생성만으로 파이프라인의 모든 게이트와 신뢰 체인이 끊김 없이 동작하는지 확인합니다. FAST_DOC 실행 프로필을 사용하므로 제품 코드(`.py`), `pipeline.py`, `CLAUDE.md`, 에이전트 정의, 워크플로 파일은 일절 수정하지 않습니다.

## 현재 trust chain 요약

신뢰 체인은 아래 순서로 구성됩니다.

1. 로컬 `pipeline.py` — 상태 기록과 게이트 차단(exit code 1)
2. agent receipts — planner / manager / dev / qa / build 영수증(`agent start` → `agent finish`)
3. `phase_attestation_request.json` — phase별 증거 묶음(`gates prepare-phase`)
4. GitHub Actions CI — 푸시된 커밋에 대한 자동 검사(`gates phase-ci`, `gates github-ci`)
5. CODEOWNERS — 보호 파일과 오라클 답안 키에 대한 리뷰 권한
6. human ACCEPT — 사용자가 일회용 승인 코드를 직접 입력하는 최종 단계

각 단계는 앞 단계의 증거가 실제로 존재할 때만 다음 단계로 넘어갑니다.

## 이번 dry run에서 실제로 통과한 단계

- PM phase: planner receipt + manager receipt + contract freeze + clarification gate 통과 (완료)
- PM phase attestation CI: GitHub Actions PASS (완료)
- Dev phase: 이 문서 파일(`docs/pipeline_real_dry_run_20260617.md`) 생성 (완료)
- QA phase: 진행 중 (문서 5개 섹션 존재 및 scope 일치 검증 예정)
- SEC phase: 예정 (네트워크/DB 접근 없는 docs 전용 작업 → skip 처리 예정)
- Build phase: 예정 (EXE 빌드 대상 아님 → N/A 기록 예정)
- External Gates(Technical / Oracle / GitHub CI / User Acceptance): 예정
- Architect phase: 예정

## 사용자 ACCEPT 전 확인 항목

- PR diff에 `docs/pipeline_real_dry_run_20260617.md` 파일만 포함되어 있는지 확인
- 위 5개 섹션(목적, 현재 trust chain 요약, 이번 dry run에서 실제로 통과한 단계, 사용자 ACCEPT 전 확인 항목, 실사용 전 남은 주의사항)이 모두 존재하는지 확인
- Technical / Oracle / GitHub CI 게이트가 모두 PASS인지 확인
- `acceptance_request.json`의 `browser_approval_skip` 필드 값 확인

## 실사용 전 남은 주의사항

- `PIPELINE_BROWSER_APPROVAL_SKIP=1` 환경변수는 실제 사용자 ACCEPT 경로에서 사용 금지 (테스트/진단 전용)
- 이번 dry run 중 파이프라인 결함이 발견되더라도 같은 PR에서 수정 금지 — 별도 BUG 파이프라인을 시작해야 함
- `gates accept`는 반드시 사용자가 이번 대화에서 직접 acceptance code를 입력한 후에만 실행 (에이전트가 코드를 추측·생성·자동 입력하는 행위 금지)
