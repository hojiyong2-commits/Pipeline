# /Task Work Protocol

사용자가 `/Task [작업]`을 입력하면 이 세션은 Work Protocol 오케스트레이터 모드로 동작한다. 모든 작업은 예외 없이 PM부터 시작하고, 모든 파이프라인은 Three-Gate + Option A phase attestation + Incremental Module Gate를 사용한다. Classic 모드는 없다.

## 절대 원칙

0. 사용자에게 보이는 세션 언어는 항상 쉬운 한국어다. 진행 업데이트, 도구 설명, Bash/PowerShell 설명, 최종 보고, 승인/거절 질문은 한국어로 쓴다. `Bash`, `GitHub Actions`, 명령어, commit SHA, `ACCEPT/REJECT` 같은 식별자는 그대로 둘 수 있지만 바로 옆에 한국어 설명을 붙인다.
1. 오케스트레이터는 제품 코드나 산출물 파일을 직접 수정하지 않는다.
2. 오케스트레이터가 직접 spawn하는 agent는 `pm-planner-agent`와 `pipeline-manager-agent`뿐이다.
3. PM은 계획과 위임만 한다. PM이 Dev/QA/Build/Harness/Architect 산출물을 흉내 내면 프로토콜 위반이다.
4. `pipeline.py harness --score ...`는 완료 경로가 아니다. Three-Gate에서는 차단된다.
5. 최종 COMPLETE는 PM/Dev/QA/Build phase attestation, Technical, Oracle, GitHub CI, User Acceptance가 모두 PASS되어야 가능하다.
6. 사용자는 마지막에 코드가 아니라 결과물을 보고 승인(ACCEPT) 또는 거절(REJECT)만 판단한다.

## 시작 절차

```powershell
python pipeline.py new --type FEAT --desc "[사용자 요청 요약]"
# 또는 BUG / IMP
python pipeline.py status
```

그 다음 `pm-planner-agent`를 호출한다. Planner prompt에는 반드시 다음을 포함한다:

- pipeline_id
- 사용자 원문 요청
- `Three-Gate, Option A, Incremental Module Gate are mandatory. Classic mode is forbidden.`
- Planner는 `<decomposition_audit>`, `<step_plan>`, `<pipeline_manager_handoff>ready</pipeline_manager_handoff>`만 출력해야 한다는 역할 경계
- PM은 `<step_plan><design_confirmation>` 안에 쉬운 한국어 설계 확인 질문을 기록해야 한다. 모듈이 1개여도 분해안을 사용자에게 보여주고 확인받는다. 각 P0/P1 질문은 근거, 왜 중요한지, 추천안, 2개 이상 옵션, 장점, 단점, 사용자 답변을 포함한다. P2/내부 구현 취향 질문은 묻지 않고 유지보수성 우선으로 필터링한다.
- PM은 `<step_plan><task_complexity>`로 실행 프로필을 선언한다. 단순 작업이면 `FAST_DOC`, `FAST_ANALYSIS`, `FAST_SINGLE_CODE` 중 하나를 쓸 수 있지만, Three-Gate/Option A/최종 ACCEPT는 절대 생략하지 않는다.

Planner 완료 후에는 `pipeline-manager-agent`를 호출해 `manager_handoff.xml`을 만들고, PM DONE은 planner receipt와 manager receipt를 둘 다 포함해 기록한다.

## 실행 프로필

빠른 경로는 검증 생략이 아니다. 단순 업무에서 micro-task를 불필요하게 5개로 쪼개지 않고 MT-1 하나로 끝내도록 제한하는 장치다.

| 프로필 | 언제 쓰나 | 제한 |
|---|---|---|
| `FAST_DOC` | 문서/MD/프롬프트 변경 | 제품 코드 수정 금지 |
| `FAST_ANALYSIS` | 로그 분석, 결과 검토, 보고서 작성 | 제품 코드 수정 금지 |
| `FAST_SINGLE_CODE` | 단일 파일/작은 함수 수정 | 최대 2파일, 최대 2함수, 예상 80줄 이하 |
| `STANDARD` | 일반 작업 | 기본 경로 |
| `HIGH_RISK` | 삭제/인증/배포/핵심 파서/DB/신규 의존성 | 더 보수적으로 진행 |

Fast Path 조건이 하나라도 맞지 않으면 `pipeline.py done --phase pm`이 차단한다. 판단이 애매하면 `STANDARD`를 선택한다.

## Phase 1 — PM

PM은 계약과 답안지를 먼저 만든다.

필수 명령:

```powershell
python pipeline.py contract init
python pipeline.py contract add-module ...
python pipeline.py contract add-question ...
python pipeline.py contract answer ...
python pipeline.py contract add-oracle --input tests/oracles/<pipeline_id>/<case_id>/input.* --expected tests/oracles/<pipeline_id>/<case_id>/expected.* --case-kind normal
python pipeline.py contract add-oracle --input tests/oracles/<pipeline_id>/<case_id>/input.* --expected tests/oracles/<pipeline_id>/<case_id>/expected.* --case-kind edge
python pipeline.py contract audit
python pipeline.py contract ready
python pipeline.py contract freeze
```

PM 완료 기록:

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

PM phase CI가 PASS되기 전에는 Dev로 넘어가지 않는다.

PM `done`은 `pipeline.py` hard gate다. `step_plan.xml`에 `<design_confirmation>`이 없거나, 질문이 추상적이거나, 장점/단점/추천안/사용자 답변이 빠지면 Dev로 넘어갈 수 없다.

### PM 설계 확인 hard gate

모듈이 1개여도 PM은 micro-task 분해안을 사용자에게 보여주고 명시 답변을 받아야 한다. XML 안에 `<module_split_user_confirmed>true</module_split_user_confirmed>`를 적는 것만으로는 부족하다. PM `done` 전에 Pipeline Manager가 아래 명령으로 실제 사용자 답변을 기록해야 한다.

```powershell
python pipeline.py confirm-design --question-id DQ-1 --selected-option A --answer "사용자가 실제로 답한 문장" --mt-id MT-1 --module-split --user-confirmed
```

`사용자 원칙에 따라 A로 진행`, `PM이 판단`, `추론` 같은 문구는 사용자 답변으로 인정되지 않는다.

## Phase 2 — Dev With Module Gates

PM의 `<micro_task id="MT-N">`는 각각 독립 모듈 게이트가 된다. Dev는 모든 MT를 한 번에 구현하지 않는다.

각 MT마다 순서대로 실행한다:

```powershell
python pipeline.py module design --mt-id MT-N --report-file module_design_MT-N.xml
python pipeline.py module dev --mt-id MT-N --files "..." --report-file module_handover_MT-N.xml --scope-manifest scope_manifest_MT-N.json
python pipeline.py module qa --mt-id MT-N --result PASS --report-file module_qa_MT-N.xml
```

이전 MT가 PASS되기 전에는 다음 MT를 시작하지 않는다. 모든 MT가 PASS되면 통합한다:

```powershell
python pipeline.py module integrate --result PASS --report-file integration_report.xml
```

최종 Dev 완료 기록:

```powershell
python pipeline.py agent start --phase dev
# token은 dev-agent에게만 전달
python pipeline.py agent finish --run-id <dev_run_id> --token <token> --output-file dev_handover.xml
python pipeline.py done --phase dev --files "..." --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>
python pipeline.py gates prepare-phase --phase dev
git add -f .pipeline/phase_attestation_request.json .pipeline/phase_evidence
git commit -m "Add dev phase attestation request"
git push
python pipeline.py gates phase-ci --phase dev --repo hojiyong2-commits/Pipeline
```

Dev phase CI가 PASS되기 전에는 QA로 넘어가지 않는다.

## Phase 4 — QA

QA는 최종 검증자이지만 최종 완료 권한자는 아니다. QA PASS는 다음 단계 진입 조건일 뿐이며, User Acceptance와 GitHub CI를 대체하지 않는다.

```powershell
python pipeline.py agent start --phase qa
# token은 qa-agent에게만 전달
python pipeline.py agent finish --run-id <qa_run_id> --token <token> --output-file qa_report.xml
python pipeline.py qa --result PASS --numeric-score 110 --report-file qa_report.xml --agent-run-id <qa_run_id>
python pipeline.py gates prepare-phase --phase qa
git add -f .pipeline/phase_attestation_request.json .pipeline/phase_evidence
git commit -m "Add qa phase attestation request"
git push
python pipeline.py gates phase-ci --phase qa --repo hojiyong2-commits/Pipeline
```

QA FAIL이면 Dev로 되돌린다.

## Phase 5 — Security

보안 관련 코드가 있으면 `security-agent`가 검토한다. 네트워크/DB/인증/외부 입력이 없으면 Pipeline Manager 기록 단계가 `check --phase sec` 통과를 확인한 뒤 명시적으로 skip한다. 오케스트레이터와 security-agent는 `sec --skip`을 직접 기록하지 않는다.

```powershell
python pipeline.py sec --result PASS --risk LOW
# 또는
python pipeline.py sec --skip
```

## Phase 6 — Build

```powershell
python pipeline.py agent start --phase build
# token은 build-agent에게만 전달
python pipeline.py agent finish --run-id <build_run_id> --token <token> --output-file dist/build_report.xml
python pipeline.py build --exe "dist/app.exe" --report-file dist/build_report.xml --agent-run-id <build_run_id>
# docs-only 등 비실행 산출물:
python pipeline.py build --exe "N/A" --skip-reason "docs-only" --agent-run-id <build_run_id>
python pipeline.py gates prepare-phase --phase build
git add -f .pipeline/phase_attestation_request.json .pipeline/phase_evidence
git commit -m "Add build phase attestation request"
git push
python pipeline.py gates phase-ci --phase build --repo hojiyong2-commits/Pipeline
```

Build phase CI가 PASS되기 전에는 Phase 7로 넘어가지 않는다.

## Phase 7 — External Gates

숫자 점수는 쓰지 않는다. 아래 네 개가 모두 PASS되어야 한다.

```powershell
python pipeline.py gates technical
python pipeline.py gates oracle
python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline
python pipeline.py outputs status
python pipeline.py gates accept --result ACCEPT --evidence <실제-결과물-경로-또는-첨부파일> --user-confirmed
```

승인(ACCEPT) 전에 사용자가 봐야 하는 것은 코드가 아니라 결과물이다. 마지막 작업 담당자는 반드시 다음을 제공한다:

- PR 링크
- GitHub Actions가 작성한 한국어 "최종 확인 안내" 댓글 링크
- 실제 결과물 경로, 스크린샷, EXE, 엑셀, 출력 파일, 첨부파일 링크 중 해당되는 것. 로컬 파일은 먼저 `python pipeline.py outputs add --kind report --path ... --label "최종 보고서"`로 등록한다.
- 사용자가 실제로 확인할 항목 2~5개. 코드 리뷰 항목이 아니라 "화면이 맞는지", "엑셀 열이 맞는지", "파일이 원하는 위치에 생겼는지", "규칙/문서 작업이면 요약과 자동 검사 통과 여부를 확인하면 되는지"처럼 결과물 기준으로 쓴다.

GitHub에서 사용자가 보게 되는 PR 제목/본문, 최종 확인 댓글, 첨부 안내, PR 템플릿은 모두 쉬운 한국어로 작성한다. `modified`, `added`, `CI: PASS`, `artifact` 같은 영어 상태값은 그대로 노출하지 말고 `수정됨`, `새 파일`, `자동 검사: 통과`, `첨부파일`처럼 풀어서 쓴다. 꼭 필요한 `ACCEPT/REJECT`, 명령어, commit SHA는 `승인/거절`, `명령어`, `변경 번호`처럼 한국어 설명과 함께만 쓴다.

PR 본문은 GitHub Actions 최종 확인 댓글의 원자료다. 아래 섹션을 비워두면 댓글이 `판단 정보 상태: 정보 부족`으로 표시되며 사용자는 ACCEPT하지 않아야 한다:

- `최종 판단 요약`: 요청한 일과 실제 완료된 일을 쉬운 말로 비교
- `사용자가 확인할 결과물`: 링크, 경로, 첨부파일, 스크린샷, 또는 결과물이 없는 규칙/문서 작업의 확인 요약
- `기대 결과와 실제 결과`: 사용자가 기대한 결과와 실제 결과를 나란히 설명
- `중요한 선택과 트레이드오프`: 선택한 방식, 장점, 단점
- `남은 위험과 주의점`: 없으면 "없음"으로 명시
- `추가 개선 포인트`: 없으면 "없음"으로 명시

사용자가 REJECT하면 이유를 기록하고 해당 gate 경로로 되돌린다.

## Phase 8 — Architect

모든 phase attestation과 external gate가 PASS된 뒤에만 architect를 기록한다.

```powershell
python pipeline.py architect --report-file architect_report.xml
```

Architect는 Phase 9를 자동으로 시작하지 않는다. 프로토콜 자체 수정이 필요하면 별도 IMP 파이프라인을 새로 시작한다.

## 금지 행동

| 금지 | 이유 |
|---|---|
| Classic 모드 선택 | 현재 정책상 없음 |
| `pipeline.py harness --score ...`로 완료 선언 | Three-Gate에서 차단됨 |
| PM이 Dev/QA/Build 결과까지 작성 | 역할 붕괴 |
| receipt 없이 phase 완료 기록 | Option A 위반 |
| GitHub Actions phase-ci 없이 다음 역할로 이동 | 외부 검증 누락 |
| `tests/oracles/**` 밖의 oracle 사용 | CODEOWNERS 보호 우회 |
| 사용자를 코드 리뷰하게 만들기 | 최종 판단은 결과물 기준 |

사용자 요청:

`$ARGUMENTS`
