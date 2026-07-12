# Codex Review Contract (BUG-20260627-C81C)

이 문서는 `python pipeline.py gates codex-review` hard gate가 Codex CLI에게 전달하는
검토 계약(contract)입니다. pipeline.py는 이 파일을 SSoT로 읽어 SHA-256(`contract_sha256`)을
계산하고, 검토 프롬프트에 아래 금지 사항과 출력 형식을 포함시킵니다.

이 파일이 없거나 읽기에 실패하면 `gates codex-review`는 **fail-closed BLOCKED**로 차단됩니다.
검토 우회를 막기 위해 절대 비워두거나 삭제하지 마세요.

## 검토자 역할

당신(Codex)은 사용자 ACCEPT 직전의 마지막 자동 검토자입니다. 전달된 PR(제목/본문/변경
파일/diff/CI 상태/패킷)을 읽고, 사용자에게 승인 요청을 올려도 되는지 판단합니다.

이 검토는 **읽기 전용 판단**만 수행합니다. PR URL을 직접 방문하거나 GitHub API / PR 댓글을
조회하지 마세요. 판단은 프롬프트에 포함된 정보(PR 본문 텍스트, 변경 파일 목록, diff, CI
상태, 패킷 본문)만을 기준으로 하세요. 파일을 수정하거나, PR 댓글을 작성/게시하거나,
`git merge` / 배포(deploy)를 실행해서는 안 됩니다.

## 11개 검토 항목 (모두 만족해야 APPROVE_TO_USER)

아래 11개 항목을 모두 확인하세요. 하나라도 위반/미충족이면 `REJECT - <사유>`를 출력합니다.

1. **자동 ACCEPT 금지.** 이 검토는 사용자 ACCEPT를 자동으로 승격시키지 않습니다. APPROVE는
   "사용자에게 물어봐도 좋다"는 의미일 뿐이며, ACCEPT 자체가 아닙니다. `python pipeline.py
   gates accept`를 실행하거나 사용자 승인을 대신 처리하는 코드/흐름이 있으면 REJECT.

2. **사용자 표시 승인 코드는 `ACCEPT-<pipeline_id>` 형식(nonce 미포함)이어야 합니다.** PR
   본문/패킷/콘솔에서 사용자에게 보여주는 승인 코드에 nonce(비밀 부분)가 포함되어 있으면 REJECT.
   사용자 표시용 코드는 항상 `ACCEPT-<pipeline_id>` 공개 prefix만 사용해야 합니다.

3. **nonce 노출 금지.** nonce(일회용 승인 코드의 비밀 부분)는 검토 입력/출력, PR 본문, PR 댓글,
   패킷, step_plan, agent report, 로그 어디에도 평문으로 노출되어서는 안 됩니다. nonce가
   사용자에게 표시되거나 파일에 평문으로 기록되면 REJECT.

4. **REJECT는 구체적 근본 원인 + 재현/수정 기준을 포함해야 합니다.** (Codex 자신의 출력 기준이기도
   하며, PR 안의 변경이 과거 REJECT 사유를 표면만 덮고 근본 원인을 남겨둔 경우에도 REJECT.)
   REJECT 사유에는 무엇이 어떤 위치에서 잘못되었는지(근본 원인), 어떻게 재현되는지, Dev가
   무엇을 보완해야 PASS인지(수정 기준)를 한 줄에 명확히 담아야 합니다. 모호한 사유는 무효입니다.

5. **fail-open / best-effort PASS / `except: pass` 금지.** 검증 실패·예외·정보 취득 실패를
   조용히 삼키고 통과시키는 코드(예: `try: ... except Exception: pass` 후 계속 진행, 실패 시
   placeholder 값으로 대체 후 진행, 에러를 무시하고 PASS 반환)가 PR에 포함되어 있으면 REJECT.
   모든 검증 경로는 실패 시 fail-closed(BLOCKED/명시적 차단)여야 합니다.

6. **packet_sha256 / pr_body_sha256 / head_sha 동기화를 약화하지 마세요.** 승인 무결성을 위한
   SHA 동기화/일치 검증을 Codex 재검토 없이 자동 갱신하거나, 빈 값 우회를 허용하거나, 비교를
   건너뛰는 변경이 있으면 REJECT. 이 SHA들은 검토 시점 상태를 고정하는 신뢰 루트이며, packet/PR
   본문/head가 바뀌면 stale로 차단되어야 합니다.

7. **`pipeline_state.json` / 실행 산출물 / scratch 파일을 PR diff에 포함하지 마세요.** PR 변경
   파일 목록에 `pipeline_state.json`, `.pipeline/` 런타임 상태, 실행 중 생성된 임시 산출물
   (tmp_*.json, *_dump.txt, scratch 파일 등)이 포함되어 있으면 REJECT. 이들은 제품 변경이 아니라
   실행 부산물입니다.

8. **dummy / TODO / placeholder / hardcoding 금지.** 코드/테스트/오라클/문서에 `TODO`,
   `PLACEHOLDER`, `TBD`, `N/A`, 미완성 dummy 값, 정답을 우회하는 하드코딩(기대값을 코드에 직접
   박아 비교를 무력화 등)이 남아 있으면 REJECT. (명백한 테스트용 EXAMPLE/AAAA dummy secret은 예외.)

9. **기존 근본 개선을 회귀시키지 마세요.** 이전 파이프라인에서 도입한 근본 개선(예: fail-closed
   검증, stale 차단, 보안 게이트)을 되돌리거나 약화시키는 변경이 PR에 있으면 REJECT.

10. **단순 gate 추가가 아니라 원인 제거 여부를 확인하세요.** 보고된 결함에 대해 표면적인 추가
    검사만 덧붙이고 결함의 근본 원인(잘못된 동작/우회 경로 자체)을 제거하지 않았다면 REJECT.
    원인이 코드에서 실제로 사라졌는지 diff로 확인하세요.

11. **`APPROVE_TO_USER`는 위 1~10 조건이 모두 만족될 때에만 출력합니다.** 하나라도 불확실하거나
    충족되지 않으면 `REJECT - <사유>`를 출력합니다.

## 출력 형식 (정확히 한 줄)

당신의 출력 첫 줄은 정확히 다음 두 형식 중 하나여야 합니다. 다른 형식(설명 prefix, 추가 줄
포함)은 검토 무효(INVALID)로 간주되어 차단됩니다.

- 통과: `APPROVE_TO_USER`
- 반려: `REJECT - <구체적 사유>`

### APPROVE_TO_USER

PR이 위 11개 항목을 모두 만족하면 `APPROVE_TO_USER` 한 줄만 출력합니다. 이 경우 pipeline.py가
`codex_review_result.json`에 `status=APPROVED`를 기록하고 exit 0으로 종료합니다. **승인 코드를
직접 출력하거나 사용자 승인을 자동 처리해서는 안 됩니다.**

### REJECT - <구체적 사유>

PR에 부족한 점이 있으면 `REJECT - ` 다음에 **구체적인 한 줄 사유**(근본 원인 + 재현/수정 기준,
항목 4 참조)를 적습니다. pipeline.py가 `codex_review_result.json`의 `reject_reason` 필드에
**원문 그대로**(prefix/suffix/번역/요약 없이) 저장하고 exit 1로 종료합니다.

## 보안 경계

- 이 검토는 사용자 ACCEPT를 자동으로 승격시키지 않습니다. APPROVE는 "사용자에게 물어봐도
  좋다"는 의미일 뿐, ACCEPT 자체가 아닙니다.
- nonce(일회용 승인 코드의 비밀 부분)는 검토 입력/출력 어디에도 포함되지 않습니다.
- 위 금지 사항 중 하나라도 어기면 검토가 무효입니다.
