# Codex Review Contract (SSoT)

이 문서는 User Acceptance 직전 Codex Review Loop의 단일 검토 계약(SSoT)입니다.
`.claude/hooks/codex_user_acceptance_review.py`가 `acceptance_renderer.load_contract()`로
이 파일을 로드하여 Codex 검토 프롬프트에 삽입합니다. 이 파일이 없거나 읽기에 실패하면
hook은 fail-closed로 검토를 중단합니다 (검토 없이 통과 금지).

검토자는 아래 11개 항목을 모두 강제 준수합니다. 하나라도 위반이 발견되면
`REJECT - <근본 원인>` 형식으로 반려해야 하며, 모든 항목이 충족될 때만
`APPROVE_TO_USER`를 출력합니다.

## 1. 자동 ACCEPT 금지

검토자는 어떤 경우에도 자동 ACCEPT를 수행하지 않습니다. `pipeline.py gates accept`를
실행하거나, 사용자를 대신해 승인을 확정하거나, 승인 흐름을 우회하는 행위를 금지합니다.
최종 ACCEPT 권한은 오직 사용자에게만 있습니다.

## 2. nonce 노출 금지

검토자는 일회용 승인 코드(nonce)를 어디에도 노출하지 않습니다.
`acceptance_request.json`의 nonce 값을 읽어 출력하거나, PR 댓글/로그/출력에
`ACCEPT-<pipeline_id>-<nonce>` 형식의 전체 코드를 게시하는 것을 금지합니다.
사용자에게 표시하는 코드는 nonce가 없는 `ACCEPT-<pipeline_id>` 형식만 허용합니다.

## 3. REJECT 시 근본 원인 포함 필수

반려할 때는 `REJECT - <근본 원인>` 형식으로, 표면 증상이 아니라 근본 원인(root cause)을
반드시 한 줄로 명시합니다. "테스트 실패", "문제 있음" 같은 추상 문구만으로는 반려할 수
없으며, 무엇이 왜 잘못되었는지 구체적으로 적어야 합니다.

## 4. fail-open / best-effort PASS / except pass 금지

fail-open(실패 시 통과), best-effort PASS(최선만 하고 통과 처리),
`except: pass`(예외를 삼키고 진행)로 검증을 무력화하는 패턴을 금지합니다.
검증 경로의 모든 예외는 fail-closed로 차단되어야 하며, 검증 불가 상태를
PASS로 처리하는 코드는 즉시 REJECT 대상입니다.

## 5. SHA 동기화 약화 금지

PR head SHA, packet SHA-256, verification_json SHA 등 동기화(SHA synchronization)
검증을 약화시키거나 우회하는 변경을 금지합니다. prefix-only 비교로 완화하거나,
SHA 비교 자체를 생략하거나, stale 검증을 끄는 변경은 REJECT 대상입니다.

## 6. state / scratch PR diff 포함 금지

`pipeline_state.json`, `.pipeline/` 런타임 state, scratch/임시 산출물 파일이
PR diff에 포함되는 것을 금지합니다. 검토자는 변경 파일 목록(changed_files diff)에
state/scratch 파일이 섞여 있으면 REJECT합니다.

## 7. dummy / TODO / placeholder 금지

실제 동작 대신 dummy 값, TODO 주석, placeholder 문자열(TBD, N/A 등)로
구현을 흉내내는 것을 금지합니다. oracle expected 출력이나 핵심 로직에
placeholder가 남아 있으면 REJECT 대상입니다.

## 8. 근본 개선 되돌리기 금지

이전 파이프라인에서 적용된 근본 개선(root-cause fix)을 되돌리거나(revert),
회귀(regression)를 유발하는 변경을 금지합니다. 기존 fail-closed 게이트를
다시 fail-open으로 바꾸는 등의 퇴행은 REJECT 대상입니다.

## 9. 원인 제거 확인 의무

검토자는 보고된 문제의 근본 원인이 실제로 제거(원인 제거 확인, root-cause removal
verification)되었는지 확인할 의무가 있습니다. 증상만 가리고 원인이 남아 있으면
REJECT합니다. 원인 제거를 확인하지 못하면 APPROVE할 수 없습니다.

## 10. APPROVE_TO_USER 조건부 출력

`APPROVE_TO_USER`는 위 1~9 항목을 모두 충족하고, 모든 AC(acceptance criteria)가
PASS이며, 코드 품질 기준을 충족할 때에만 출력합니다. 조건이 하나라도 미충족이면
APPROVE 대신 `REJECT - <근본 원인>`을 출력합니다.

## 11. REJECT 횟수 제한 초과 시 사용자 에스컬레이션

동일 PR에 대한 REJECT 누적 횟수가 상한(최대 5회)을 초과하면, 루프를 자동 중단하고
사용자에게 직접 검토를 요청하는 에스컬레이션(escalation)으로 전환합니다.
검토자는 무한 반려 루프를 만들지 않고, 상한 초과 시 사용자 에스컬레이션을 따릅니다.
