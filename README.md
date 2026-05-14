# Pipeline — 자체 개선형 다중 에이전트 파이프라인

작업 순서, 외부 검증, 사용자 최종 승인을 강제하는 파이프라인입니다.

## 현재 기본 구조

모든 파이프라인은 이제 예외 없이 Three-Gate + Option A + Incremental Module Gate로 실행합니다. 이 셋은 선택 모드가 아닙니다.

쉽게 말하면:

1. PM이 요구사항을 작은 `MT-N` 단위로 쪼갭니다.
2. 각 `MT-N`은 `module design -> module dev -> module qa`를 통과해야 다음 모듈로 넘어갑니다.
3. 모든 모듈이 PASS되면 `module integrate`로 합칩니다.
4. PM/Dev/QA/Build phase는 각각 GitHub Actions phase attestation을 통과해야 다음 역할 경계로 넘어갑니다.
5. 최종 완료는 Technical, Oracle, GitHub CI, User Acceptance가 모두 PASS된 뒤에만 가능합니다.

사용자가 마지막에 해야 할 일은 코드 리뷰가 아니라 결과물 확인입니다. 마지막 작업 담당자가 PR 링크와 GitHub Actions의 한국어 **최종 확인 안내**, 실제 결과물 경로나 첨부파일 링크를 줍니다. 사용자는 그 결과물이 요청과 맞는지만 보고 승인(ACCEPT) 또는 거절(REJECT)을 선택합니다.

### 단순 업무 빠른 경로

단순 업무도 Three-Gate와 Option A는 그대로 씁니다. 다만 PM이 `step_plan.xml`의 `<task_complexity>`로 아래 프로필을 선언하면, 불필요하게 MT를 여러 개로 쪼개지 않고 MT-1 하나로 진행할 수 있습니다.

| 프로필 | 의미 |
|---|---|
| `FAST_DOC` | 문서/MD/프롬프트 변경. 제품 코드 수정 금지 |
| `FAST_ANALYSIS` | 로그 분석, 결과 검토, 보고서 작성. 제품 코드 수정 금지 |
| `FAST_SINGLE_CODE` | 최대 2파일/2함수/예상 80줄 이하의 작은 코드 수정 |
| `STANDARD` | 일반 작업 |
| `HIGH_RISK` | 삭제, 인증, 배포, 핵심 파서, DB, 신규 의존성 등 위험 작업. 이유와 risk flag가 필요하고 보수 모드로 진행 |

Fast Path는 검증 생략이 아닙니다. GitHub Actions, phase attestation, Technical/Oracle/GitHub/User Acceptance gate는 그대로 필요합니다.

### 결과물 등록

사용자가 PR에서 바로 열어볼 파일은 아래처럼 등록합니다.

```powershell
python pipeline.py outputs add --kind report --path report.md --label "최종 보고서" --notes "사용자는 결론과 확인 항목만 보면 됩니다."
```

등록된 파일은 `pipeline_outputs/<pipeline_id>/`로 복사되고, GitHub Actions가 만드는 **최종 확인 안내** 댓글의 “등록된 결과물 바로 열기”에 링크로 표시됩니다.

## 필수 명령 흐름

```powershell
python pipeline.py contract init
python pipeline.py agent start --phase pm_planner
python pipeline.py agent finish --run-id <planner_run_id> --token <token> --output-file step_plan.xml
python pipeline.py agent start --phase pipeline_manager
python pipeline.py agent finish --run-id <manager_run_id> --token <token> --output-file manager_handoff.xml
python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml
python pipeline.py gates prepare-phase --phase pm
python pipeline.py gates phase-ci --phase pm --repo hojiyong2-commits/Pipeline

python pipeline.py module design --mt-id MT-1 --report-file module_design_MT-1.xml
python pipeline.py module dev --mt-id MT-1 --files "path/to/file.py" --report-file module_handover_MT-1.xml --scope-manifest scope_manifest_MT-1.json
python pipeline.py module qa --mt-id MT-1 --result PASS --report-file module_qa_MT-1.xml

# 모든 MT-N에 module design/dev/qa를 반복한 뒤:
python pipeline.py module integrate --result PASS --report-file integration_report.xml

python pipeline.py done --phase dev --files "path/to/file.py" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>
python pipeline.py gates prepare-phase --phase dev
python pipeline.py gates phase-ci --phase dev --repo hojiyong2-commits/Pipeline

python pipeline.py qa --result PASS --numeric-score 110 --report-file qa_report.xml --agent-run-id <qa_run_id>
python pipeline.py gates prepare-phase --phase qa
python pipeline.py gates phase-ci --phase qa --repo hojiyong2-commits/Pipeline

python pipeline.py build --exe "dist/app.exe" --report-file dist/build_report.xml --agent-run-id <build_run_id>
python pipeline.py gates prepare-phase --phase build
python pipeline.py gates phase-ci --phase build --repo hojiyong2-commits/Pipeline

python pipeline.py gates technical
python pipeline.py gates oracle
python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline
python pipeline.py gates accept --result ACCEPT --evidence <실제-결과물-경로-또는-첨부파일> --user-confirmed
python pipeline.py architect --report-file architect_report.xml
```

## 파이프라인 스모크 테스트

**Pipeline ID:** IMP-20260509-A4DB

이 섹션은 전체 파이프라인이 끝까지 정상 동작하는지 확인한 기록입니다.

### 이 테스트가 확인한 것

| 단계 | 확인한 내용 | 사용한 게이트 |
|---|---|---|
| Phase 1 — PM | 계약 초기화, 작업 분해, `step_plan.xml` 작성 | `pipeline.py done --phase pm --decomp --clarification --roadmap` |
| Phase 2 — Dev | 브랜치에서 README 수정, PR 흐름 확인 | `pipeline.py done --phase dev --files "README.md" --scope-declared` |
| Phase 4 — QA | 내용 검증, 브랜치/PR 검증 | `pipeline.py qa --result PASS --numeric-score N` |
| Phase 5 — SEC | 문서 작업이라 보안 검사는 생략 | `pipeline.py sec --skip` |
| Phase 6 — Build | 문서 작업이라 EXE 빌드는 해당 없음 | `pipeline.py build --exe "N/A" --skip-reason "docs-only"` |
| Phase A — 단계별 CI | PM/Dev/QA/Build마다 GitHub 자동 검증 통과 | `pipeline.py gates prepare-phase --phase dev` 후 `pipeline.py gates phase-ci --phase dev` |
| Phase 7 — Three-Gate | 기술 검사, Oracle 검사, GitHub 자동 검사, 사용자 승인 | `pipeline.py gates accept --result ACCEPT --evidence [실제-결과물-경로-또는-첨부파일] --user-confirmed` |
| Phase 8 — Architect | 원인 분석과 후속 개선 여부 판단 | `pipeline.py architect --report-file architect_report.xml` |

### 확인된 제약

- `main`에 직접 push하지 않고 PR을 거칩니다.
- GitHub 자동 검사(`pytest` + `pipeline_attestation.json`)가 통과해야 합니다.
- PM/Dev/QA/Build 단계마다 `phase_attestation.json` 검증이 필요합니다.
- Technical, Oracle, GitHub CI, User Acceptance 게이트는 필수입니다.
- 사용자가 승인한 결과물은 `G:\내 드라이브\터미널\<pipeline_id>`에 복사되고 `deployment_manifest.json`이 기록됩니다.

### Option A: 에이전트별 receipt + 단계별 CI

각 주요 단계는 한 번만 쓰는 agent receipt와 연결되어야 기록할 수 있습니다.

```powershell
python pipeline.py agent start --phase pm_planner
# 출력된 token은 pm-planner-agent에게만 전달
python pipeline.py agent finish --run-id <planner_run_id> --token <token> --output-file step_plan.xml
python pipeline.py agent start --phase pipeline_manager
# 출력된 token은 pipeline-manager-agent에게만 전달
python pipeline.py agent finish --run-id <manager_run_id> --token <token> --output-file manager_handoff.xml
python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml
python pipeline.py gates prepare-phase --phase pm
git add -f .pipeline/phase_attestation_request.json .pipeline/phase_evidence
git commit -m "Add pm phase attestation request"
git push
python pipeline.py gates phase-ci --phase pm --repo hojiyong2-commits/Pipeline
```

`dev`, `qa`, `build`도 같은 방식으로 진행합니다. GitHub 자동 검사는 단계 요청, 증거 파일, agent receipt, 변경 번호, 테스트 실행 결과를 확인합니다.

이 구조에서는 PM이 Dev/QA/Build 역할을 대신했다고 주장하기 어렵습니다. 오케스트레이터가 token을 발급하고, 해당 phase agent만 그 token으로 receipt를 완성합니다. 이후 `pipeline.py`와 GitHub 자동 검사가 그 receipt를 확인합니다.

이 방식은 강한 운영상 방어입니다. 완전한 암호학적 증명을 원하면 로컬 agent가 수정할 수 없는 외부 runner나 별도 signer가 필요합니다.

### 사용자가 소유하는 oracle 파일

Dev 시작 전, PM은 사용자에게 실제 입력 예시와 기대 출력 예시를 받아야 합니다. 정답 파일은 아래 위치에 저장합니다.

```text
tests/oracles/<pipeline_id>/<case_id>/
```

그 다음 oracle 파일을 등록합니다:

```powershell
python pipeline.py contract add-oracle --input tests/oracles/<pipeline_id>/<case_id>/input.json --expected tests/oracles/<pipeline_id>/<case_id>/expected.json --case-kind normal
```

`pipeline.py contract audit`는 `tests/oracles/**` 밖의 oracle 파일을 거부합니다. CODEOWNERS도 `tests/oracles/**`, `tests/**`, 루트의 `test_*.py`, `*_test.py`를 사용자 확인 대상으로 표시합니다. 이렇게 해야 agent가 정답지나 테스트를 몰래 바꾸기 어렵습니다.

### PM 설계 확인

Dev로 넘어가기 전, PM은 모듈 분해안을 사용자에게 쉬운 한국어로 보여주고 확인받아야 합니다. 모듈이 1개뿐이어도 확인합니다.

`pipeline.py done --phase pm`은 `step_plan.xml`의 `<design_confirmation>`을 검사합니다. 각 중요한 질문은 근거, 왜 중요한지, 추천안, 최소 2개 옵션, 장점, 단점, 사용자 답변을 포함해야 합니다. 변수명이나 코드 취향처럼 중요하지 않은 질문은 묻지 않고, 유지보수성 우선 원칙으로 PM이 정리합니다.

### GitHub 자동 검사 흐름

`.github/workflows/ci.yml`은 모든 PR과 `main` push에서 실행됩니다.

1. `python -m pytest -q`를 실행합니다.
2. 성공하면 `pipeline_attestation.json`을 만듭니다.
3. PR에서는 `human_acceptance_packet.md`를 만들고, **최종 확인 안내** 댓글을 쉬운 한국어로 남깁니다.
4. `.pipeline/phase_attestation_request.json`이 있으면 단계별 증거를 확인하고 `phase_attestation.json`을 만듭니다.
5. 필요한 증명 파일을 GitHub 첨부파일로 올립니다.

`pipeline.py gates github-ci`는 최신 GitHub 자동 검사 결과를 확인합니다. `pipeline.py gates phase-ci --phase <pm|dev|qa|build>`는 다음 역할로 넘어가기 전에 단계별 증명을 확인합니다.

### 최종 사용자 확인

사용자가 마지막에 할 일은 이것뿐입니다:

1. 마지막 작업 담당자가 준 PR 링크를 엽니다.
2. PR 댓글의 **최종 확인 안내**를 읽습니다.
3. 그 안내에 적힌 결과 링크/경로 또는 첨부파일을 엽니다.
4. 결과물이 요청과 맞으면 Claude Code/dashboard에서 승인(ACCEPT), 아니면 거절(REJECT)을 고릅니다.

이 안내는 GitHub Actions가 자동으로 만듭니다. CI 통과 여부, 변경 파일,
중요 보호 파일 변경 여부, 첨부파일 링크를 요약합니다. 사용자는 코드를
검토하지 않고 결과물만 보고 판단하면 됩니다.

최종 확인 댓글은 PR 본문에서 `최종 판단 요약`, `사용자가 확인할 결과물`,
`기대 결과와 실제 결과`, `중요한 선택과 트레이드오프`를 읽어옵니다.
이 정보가 비어 있으면 댓글 맨 위에 `판단 정보 상태: 정보 부족`이 표시됩니다.
그 경우에는 승인하지 말고 담당자에게 보완을 요청하거나 거절하면 됩니다.

### GitHub 신뢰 기준

`main` 브랜치는 로컬 agent 흐름 밖에서 보호됩니다.

- `main` 직접 push는 막습니다.
- PR은 GitHub 자동 검사 `tests`를 통과해야 merge할 수 있습니다.
- 관리자도 보호 규칙을 따릅니다.
- `.github/CODEOWNERS`, `.github/workflows/**`, `pipeline.py`, `CLAUDE.md`, `.claude/agents/**`, `tests/oracles/**`, `tests/**`, `test_*.py`, `*_test.py`는 CODEOWNERS에 들어 있습니다.

이렇게 하면 최종 merge 결정과 자동 검사가 로컬 Claude/Codex 주장 밖에서 이루어집니다. 단일 소유자 repo에서 “승인 1명 필수”를 켜면 본인이 만든 PR이 막힐 수 있으므로, 그 설정은 신뢰할 두 번째 GitHub 계정을 추가한 뒤 켜는 것이 좋습니다.

## 외부 플러그인 운영 설계 (개인 PC 한정)

> **핵심 원칙:** 외부 플러그인은 완료 판정자가 아니라 정보 탐색/실행/증거 생성 도구이며, 완료 판정은 Three-Gate + Option A + Incremental Module Gate + User Acceptance가 담당한다.

이 섹션은 개인 PC에서 개인 프로젝트를 관리할 때 외부 플러그인(GitHub, Local shell, Web/Browser, Playwright/Screenshot, Notion/Drive, Calendar, OpenAI advisory)을 파이프라인에 연결하는 운영 설계를 요약합니다.

### 플러그인 역할 분리

| 플러그인 | 허용 | 금지 |
|---|---|---|
| GitHub | 조회·열람·CI 결과 확인 | 직접 머지, 승인 결정 |
| Local shell | 빌드·테스트 실행, 로그 수집 | `pipeline.py done/qa/build` 직접 기록 |
| Web·Browser | 문서 열람, 검색 | 증거 날조, oracle 변조 |
| Playwright·Screenshot | UI 화면 캡처, 동작 증거 수집 | QA PASS 선언 |
| Notion·Drive | 컨텍스트·결정사항 기록 | step_plan 대체 |
| Calendar | 리마인더, 마감 추적 | 자동 완료 선언 |
| OpenAI advisory | 코드 리뷰 조언, red-team 리뷰 | PASS/FAIL 결정 |

### 도입 순서 (4단계 추천)

1. **1단계:** GitHub + Local shell + Web search (기본 작업 도구)
2. **2단계:** Playwright·Screenshot + outputs_manifest (UI 검증·결과물 제공)
3. **3단계:** Notion·Drive + Calendar (장기 컨텍스트 관리)
4. **4단계:** OpenAI API advisory + red-team review (선택적 품질 향상)

### 성능 기대치

| 업무 | 기대 효과 |
|---|---|
| 최신 문서 기반 구현 | 오래된 API 사용 실수 감소 |
| 코드 수정 + 테스트 | 빌드·테스트 즉시 확인, 반복 감소 |
| UI 동작 검증 | 스크린샷으로 시각적 증거 자동 수집 |
| GitHub PR·CI | PR/CI 상태 실시간 반영 |
| 장기 프로젝트 | 결정사항 보존, 컨텍스트 복원 비용 감소 |

상세 규칙은 `CLAUDE.md`의 “외부 플러그인 운영 설계” 섹션을 참조합니다.
