# 파이프라인 실사용 안내 (Dry-Run Guide) — 2026-06-15

이 문서는 자기 진화형 다중 에이전트 파이프라인을 처음 또는 오랜만에 사용하는 사람이
실제 작업을 맡기기 전에 전체 흐름과 확인 항목을 한눈에 파악하도록 정리한 안내서입니다.
사용자는 코드를 검토할 필요가 없으며, 보이는 결과물과 자동 검사 결과만 확인하면 됩니다.

---

## 1. 파이프라인 신뢰 사슬(Trust Chain) 요약

모든 작업은 아래 신뢰 사슬을 따라 단계적으로 검증됩니다. 한 단계가 통과(PASS)되기
전에는 다음 단계로 넘어가지 않습니다.

```
local pipeline.py → agent receipts → GitHub Actions → CODEOWNERS → human ACCEPT
```

- **local pipeline.py**: 로컬에서 파이프라인 상태(`pipeline_state.json`)를 기록하고
  각 단계 진입을 물리적으로 차단하는 엔진입니다.
- **agent receipts**: 각 에이전트(PM/Dev/QA/Build 등)가 실제로 일을 수행했음을
  증명하는 일회용 토큰 기반 영수증입니다. 영수증 없이는 단계 기록이 거부됩니다.
- **GitHub Actions**: 푸시된 변경에 대해 클라우드에서 자동 검사(테스트, 보안 검사 등)를
  수행합니다.
- **CODEOWNERS**: 신뢰 루트 파일(파이프라인 엔진, 워크플로우, 에이전트 정의 등) 변경을
  보호합니다.
- **human ACCEPT**: 마지막에 사람이 결과물을 직접 확인하고 승인(ACCEPT)하거나
  거절(REJECT)합니다.

### Phase 1~8 흐름

| 단계 | 이름 | 핵심 역할 |
|---|---|---|
| Phase 1 | PM (계획) | 요구사항 분석, 질문, micro-task 분해, 오라클(정답표) 설계 |
| Phase 2 | Dev (백엔드 구현) | PM 계획대로 비즈니스 로직 구현 |
| Phase 3 | UI/App (선택) | 백엔드 로직을 GUI 앱으로 결합 (UI 태그가 있을 때만) |
| Phase 4 | QA (검증) | 7개 카테고리 품질 검사 및 승인 게이트 |
| Phase 5 | Security (선택) | 외부 통신/DB 접근이 있을 때만 보안 감사 |
| Phase 6 | Build (패키징) | PyInstaller 단일 EXE 빌드 (또는 N/A 사유 기록) |
| Phase 7 | External Gate | Technical / Oracle / GitHub CI / User Acceptance 외부 게이트 |
| Phase 8 | Architect (진단) | 반복 실패 패턴 분석 및 개선 진단 |

### 핵심 강제 장치

- **Option A phase attestation**: PM/Dev/QA/Build 각 역할 경계를 넘을 때마다
  전용 브랜치에서 `gates prepare-phase` → 커밋/푸시 → GitHub Actions → `gates phase-ci`로
  단계 증거를 클라우드에서 검증합니다.
- **Three-Gate**: 완료(COMPLETE)는 Technical, Oracle, GitHub CI 세 외부 게이트와
  User Acceptance가 모두 통과될 때만 가능합니다. 숫자 점수만으로는 완료가 불가능합니다.
- **Incremental Module Gate**: PM이 나눈 모든 `MT-N`(micro-task)은 각각 설계 →
  구현 → QA 검증을 거친 뒤에야 다음 모듈로 넘어갑니다. 모든 모듈 통과 후에 통합
  검증(`module integrate`)을 한 번 수행합니다.

---

## 2. 사용자가 ACCEPT 전에 확인해야 하는 항목

승인(ACCEPT)을 누르기 전에 아래 항목을 차례대로 확인합니다. 코드를 읽을 필요는 없으며,
결과가 요청과 맞는지만 확인하면 됩니다.

1. **PR 링크 열기** — 안내에 포함된 GitHub Pull Request 링크를 엽니다.
2. **GitHub Actions 자동 검사 통과 여부 확인** — PR 안의 자동 검사(체크) 결과가
   모두 통과(초록색)인지 확인합니다.
3. **요구사항 충족표(AC 충족 여부) 확인** — PR 본문/댓글의 "요구사항 충족표"에서
   요청한 조건(AC)들이 모두 충족(PASS)으로 표시되는지 확인합니다.
4. **결과물이 요청과 맞는지 확인** — 첨부된 결과물(보고서, 엑셀, EXE, 스크린샷 등)이
   실제 요청과 일치하는지 확인합니다. 이것은 코드 리뷰가 아니라 결과 확인입니다.
5. **승인 코드 입력** — 확인이 끝나면 발급된 일회용 승인 코드를 PR 댓글 또는
   이번 대화에 직접 입력합니다. (에이전트가 코드를 추측하거나 대신 입력하지 않습니다.)

---

## 3. ACCEPT 후 배포 산출물 위치

승인(ACCEPT)이 처리되면 결과물이 자동으로 아래 위치에 복사됩니다.

- **배포 폴더**: `G:\내 드라이브\터미널\<pipeline_id>`
  (Google Drive의 터미널 폴더 아래 파이프라인 ID별 하위 폴더)
- **deployment_manifest.json 생성**: 배포된 파일 목록과 메타데이터가 기록된
  매니페스트 파일이 함께 생성됩니다. 어떤 파일이 어디로 복사되었는지 이 파일로
  추적할 수 있습니다.

> 참고: 보안 위험이 있는 파일(자격 증명 파일, `*.key`, `*.pem` 등)은 배포 단계에서
> 자동 차단되며 `deployment_manifest.json`의 `blocked_secret_artifacts` 항목에
> 기록됩니다.

---

## 4. 최근 닫힌 주요 재발 문제 요약

아래는 최근 해결된 반복성 문제들입니다. 비슷한 증상이 다시 보이면 먼저 이 항목들을
참고하세요.

- **BUG-20260614-B32A** — pending 댓글의 evidence_integrity / workspace_hygiene
  표시가 SSoT(단일 진실 원천)와 불일치하던 문제. `_build_acceptance_display_model`에
  `packet_evidence` 파라미터를 추가하여 표시 값을 단일 원천에서 가져오도록 해결.
  (PR #602)

- **IMP-20260614-2821** — workspace / evidence hygiene 게이트 도입. 추적되지 않은
  (untracked) 오라클 파일로 승인 코드 발급을 우회하던 경로를 차단. git 조회에
  실패하면 안전하게 차단(fail-closed)하도록 설계.

- **BUG-20260614-32D7** — CI의 final-check / pending / accepted 댓글 마커 분리.
  `pipeline-final-check-packet` 마커를 도입하여, CI 업데이트가 pending/accepted
  댓글을 덮어쓰던 문제를 해결. (PR #608)

- **IMP-20260612-8104** — Codex Review를 레거시 hard gate에서 해제하고 수동
  red-team 진단 도구(manual red-team diagnostic)로 전환. 더 이상 자동으로 완료를
  차단하지 않으며, 필요 시 수동으로만 실행합니다.

---

## 5. 실사용 전 체크리스트

작업을 맡기기 전에 아래 항목을 확인합니다.

- [ ] `pipeline.py status`로 이전 미완료 파이프라인이 없는지 확인
- [ ] main 브랜치 최신화 (`git pull`)
- [ ] phase-attestation 브랜치는 반드시 main 기반으로 생성
- [ ] `pipeline_state.json`을 절대 직접 수정하지 말 것
- [ ] 승인 코드는 사용자가 직접 PR 댓글 또는 대화에 입력
- [ ] ACCEPT 후 `G:\내 드라이브\터미널\`에 배포되었는지 확인
