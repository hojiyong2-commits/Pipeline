# Anti-Gaming Rules

PM, QA, Harness, Architect는 파이프라인 시작 시 이 파일을 읽고 step_plan, 검증 기준, RCA에 반영한다.

## Active Trust Chain

`local pipeline.py -> agent receipts -> GitHub Actions -> CODEOWNERS -> human ACCEPT`

완료 판정은 숫자 점수가 아니라 아래 조건의 동시 충족이다.

1. PM/Dev/QA/Build phase attestation PASS
2. 모든 `MT-N` module gate PASS
3. `module integrate` PASS
4. Technical gate PASS
5. Oracle gate PASS
6. GitHub CI gate PASS
7. User Acceptance ACCEPT with real result evidence
8. `pipeline.py architect --report-file architect_report.xml` 기록

## Hard Forbidden Patterns

1. **Role absorption**
   - PM이 `<dev_output>`, `<qa_report>`, `<build_report>`, `<harness_report>`, `<optimization_report>`를 직접 출력하면 실패.
   - 각 phase는 `pipeline.py agent start/finish` receipt와 `--agent-run-id`가 있어야 한다.

2. **Score completion**
   - `pipeline.py harness --score ...`, BUILD+QA 합산, 100점, 80점 이상 PASS 같은 표현으로 COMPLETE 선언 금지.
   - QA numeric은 중간 hard gate와 Circuit Breaker 추적용이다. 사용자 ACCEPT나 external gate를 대체하지 않는다.

3. **Weak oracle**
   - P0가 `file_exists_check` 또는 `exe_launch_check`만으로 구성되면 freeze 차단.
   - oracle은 `tests/oracles/<pipeline_id>/<case_id>/` 아래에 저장하고, input/expected SHA-256과 `source=user`가 있어야 한다.
   - 최소 normal 1개 + edge/exception/error 1개 필요. 빈 expected output, TODO/TBD/placeholder/sample은 차단.

4. **Answer-key mutation**
   - agent가 `tests/**`, `tests/oracles/**`, `.github/CODEOWNERS`, `.github/workflows/**`, `pipeline.py`, `CLAUDE.md`, `.claude/agents/**`를 몰래 바꾸면 CODEOWNERS/user review 대상이다.
   - PM은 oracle/test 변경이 필요한 경우 사용자에게 명확히 물어보고 PR에서 드러나게 해야 한다.

5. **Scope widening**
   - PM micro_task에 없는 파일/함수 변경 금지.
   - Dev는 모듈별 `scope_manifest_MT-N.json`과 최종 `scope_manifest.json`을 제출해야 한다.
   - QA는 실제 diff가 scope manifest와 맞는지 확인한다.

6. **Phase attestation bypass**
   - PM/Dev/QA/Build 완료 후 `gates prepare-phase`, commit/push, GitHub Actions, `gates phase-ci`가 없으면 다음 주요 role boundary로 넘어갈 수 없다.
   - 로컬 report 파일만으로 “진짜 agent가 했다”고 주장하지 않는다.

7. **Fake advisory claim**
   - `pipeline.py advisory status`에서 `review_count=0` 또는 `api_call_count=0`이면 GPT/OpenAI advisory는 실행되지 않은 것이다.
   - advisory는 선택적 red-team이다. PASS/FAIL 채점자나 oracle 비교자가 아니다.

8. **User acceptance without visible result**
   - 사용자에게 코드를 읽으라고 요구하지 않는다.
   - 승인(ACCEPT) 요청 전 PR 링크, GitHub Actions 첨부파일 링크, 실제 결과물 경로/스크린샷/출력 파일, 한국어 최종 확인 안내를 제공한다.
   - GitHub에서 최종 사용자가 보는 문서는 쉬운 한국어여야 한다. `modified`, `added`, `CI: PASS`, `artifact` 같은 영어 상태값만 노출하면 위반이다. `수정됨`, `새 파일`, `자동 검사: 통과`, `첨부파일`처럼 풀어서 쓴다.

## PM Step Plan Requirements

`<step_plan>`에는 반드시 포함한다.

- `<decomposition_audit>`
- `<micro_tasks>` with `MT-N`
- 각 `MT-N`의 target files/functions
- 실제 Grep 근거 (`<grep_evidence><executed>true</executed>`)
- oracle/test ownership plan
- phase attestation plan
- user acceptance result/attachment plan

## QA Requirements

QA는 아래를 FAIL로 처리한다.

- Dev handover 없이 QA 요청
- `scope_manifest.json` 없음
- 실제 diff가 PM micro_task 밖으로 나감
- oracle expected output을 Dev가 수정함
- 실행 증거 없이 PASS 선언
- PM/Dev/QA/Build phase receipt 누락

## Architect Requirements

Architect는 Phase 8에서 다음을 구분한다.

- **ordinary task failure:** Dev/QA/Build/oracle/user rejection 경로로 재작업
- **protocol defect:** 별도 IMP로 `CLAUDE.md`, agent MD, `pipeline.py`, workflow, tests를 함께 수정

Architect는 숫자 점수 로그를 final proof로 사용하지 않는다. 오래된 `test_results.jsonl`은 historical drift 참고용으로만 샘플링한다.
