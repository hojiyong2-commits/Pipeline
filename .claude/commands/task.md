# /Task Work Protocol

사용자가 `/Task [작업]`을 입력하면 이 세션은 Work Protocol 오케스트레이터 모드로 동작한다. 모든 작업은 예외 없이 PM부터 시작하고, 모든 파이프라인은 Three-Gate + Option A phase attestation + Incremental Module Gate를 사용한다. Classic 모드는 없다.

## 절대 원칙

1. 오케스트레이터는 제품 코드나 산출물 파일을 직접 수정하지 않는다.
2. 오케스트레이터가 직접 spawn하는 agent는 `pm-agent`뿐이다.
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

그 다음 `pm-agent`를 `mode: pipeline_manager_round1`로 호출한다. PM prompt에는 반드시 다음을 포함한다:

- pipeline_id
- 사용자 원문 요청
- `Three-Gate, Option A, Incremental Module Gate are mandatory. Classic mode is forbidden.`
- PM은 `<decomposition_audit>`, `<step_plan>`, `<pipeline_manager_handoff>ready</pipeline_manager_handoff>`만 출력해야 한다는 역할 경계

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
python pipeline.py agent start --phase pm
# 출력된 token은 pm-agent에게만 전달
python pipeline.py agent finish --run-id <pm_run_id> --token <token> --output-file step_plan.xml
python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --agent-run-id <pm_run_id>
python pipeline.py gates prepare-phase --phase pm
git add .pipeline/phase_attestation_request.json .pipeline/phase_evidence
git commit -m "Add pm phase attestation request"
git push
python pipeline.py gates phase-ci --phase pm --repo hojiyong2-commits/Pipeline
```

PM phase CI가 PASS되기 전에는 Dev로 넘어가지 않는다.

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
git add .pipeline/phase_attestation_request.json .pipeline/phase_evidence
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
git add .pipeline/phase_attestation_request.json .pipeline/phase_evidence
git commit -m "Add qa phase attestation request"
git push
python pipeline.py gates phase-ci --phase qa --repo hojiyong2-commits/Pipeline
```

QA FAIL이면 Dev로 되돌린다.

## Phase 5 — Security

보안 관련 코드가 있으면 `security-agent`가 검토한다. 네트워크/DB/인증/외부 입력이 없으면 명시적으로 skip한다.

```powershell
python pipeline.py sec --result PASS --risk LOW
# 또는
python pipeline.py sec --skip
```

## Phase 6 — Build

```powershell
python pipeline.py agent start --phase build
# token은 build-agent에게만 전달
python pipeline.py agent finish --run-id <build_run_id> --token <token> --output-file build_report.xml
python pipeline.py build --exe "dist/app.exe" --report-file build_report.xml --agent-run-id <build_run_id>
# docs-only 등 비실행 산출물:
python pipeline.py build --exe "N/A" --skip-reason "docs-only" --user-confirmed --agent-run-id <build_run_id>
python pipeline.py gates prepare-phase --phase build
git add .pipeline/phase_attestation_request.json .pipeline/phase_evidence
git commit -m "Add build phase attestation request"
git push
python pipeline.py gates phase-ci --phase build --repo hojiyong2-commits/Pipeline
```

Build phase CI가 PASS되기 전에는 Phase 7로 넘어가지 않는다.

## Phase 7 — External Gates

숫자 점수는 쓰지 않는다. 아래 네 개가 모두 PASS되어야 한다.

```powershell
python pipeline.py gates technical
python pipeline.py gates oracle --user-confirmed
python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline
python pipeline.py gates accept --result ACCEPT --evidence <실제-결과물-경로-또는-첨부파일> --user-confirmed
```

승인(ACCEPT) 전에 사용자가 봐야 하는 것은 코드가 아니라 결과물이다. 마지막 작업 담당자는 반드시 다음을 제공한다:

- PR 링크
- GitHub Actions가 작성한 한국어 "최종 확인 안내" 댓글 링크
- 실제 결과물 경로, 스크린샷, EXE, 엑셀, 출력 파일, 첨부파일 링크 중 해당되는 것

GitHub에서 사용자가 보게 되는 PR 제목/본문, 최종 확인 댓글, 첨부 안내, PR 템플릿은 모두 쉬운 한국어로 작성한다. `modified`, `added`, `CI: PASS`, `artifact` 같은 영어 상태값은 그대로 노출하지 말고 `수정됨`, `새 파일`, `자동 검사: 통과`, `첨부파일`처럼 풀어서 쓴다. 꼭 필요한 `ACCEPT/REJECT`, 명령어, commit SHA는 `승인/거절`, `명령어`, `변경 번호`처럼 한국어 설명과 함께만 쓴다.

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
