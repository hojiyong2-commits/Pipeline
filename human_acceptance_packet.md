[검증용 메타데이터]
pipeline_id: IMP-20260703-B985
pr_url: https://github.com/hojiyong2-commits/Pipeline/pull/835
pr_head_sha: e654df4a4627564e7abd157c9dd9ecb6f034ed7a
ci_run_id: 28668999818
ci_head_sha: d9aa45a0067b9bdbbc530a80917229887e9985e4
changed_files_count: 2
changed_files: pipeline.py, tests/test_canonical_pr_body_sha256_b985.py
verification_json_sha256: 0f199ad6ec7ea393fbda25d5afdb9f0577db3eee7cf1d8f38d67c14c327e6b65
packet_md_sha256: 237a630b97e09b64437b77a733945612b0dd0dbb433d2bf47336a1ab3c27faa1
technical: PASS
oracle: PASS
github_ci: FAIL
acceptance: FAIL
acceptance_display: FAIL
requirements_summary: 10/10 PASS
oracle_summary: PASS (4개 케이스, 4개 통과)
known_failures: 없음
evidence_integrity: PASS (protected:8, tracked:8, pr_included:8)
workspace_hygiene: WARN (blocking:0, cleanup_only:1)
verification_json: human_acceptance_packet.json

[최종 확인 안내]

파이프라인: IMP-20260703-B985
PR: https://github.com/hojiyong2-commits/Pipeline/pull/835
GitHub Actions: https://github.com/hojiyong2-commits/Pipeline/actions/runs/28668999818
승인 표시 상태: FAIL

게이트 상태:
Technical: PASS
Oracle: PASS
GitHub CI: FAIL
User Acceptance: FAIL

변경 파일: 총 2건
  pipeline.py
  tests/test_canonical_pr_body_sha256_b985.py

요구사항 충족 요약: 10/10 충족

작업공간 정리 상태: WARN (blocking:0, cleanup_only:1)

워크스페이스 정리 요약:
Workspace Readiness: unknown
삭제 완료: 0개 파일
보류(deferred): 0개 파일
Unknown(보존): 0개 파일

요구사항 충족표:

[요구사항 충족표]

AC-1:
요구사항:
  TC-1: 동일 PR body 문자열을 canonical helper로 계산하면 CRLF/LF 차이에도 같은
   SHA가 나온다
구현 작업: MT-1, MT-8
구현 근거: MT-1: _canonical_pr_body_sha256(text: str) -> str 신규 함수를 _sha256_file 직후에 추가. CRLF(
)와 lone CR()을 LF(
)로 정규화 후 UTF-8 인코딩하여 hashlib.sha256().hexdigest() 반환 (AC-1). / MT-1: trailing newline을 strip하지 않아 no/single/double
trailing newline이 서로 다른 SHA를 생성 — GitHub API body 저장값 재현성 보장 (AC-2).
검증: MT-1: PASS — _canonical_pr_body_sha256가 CRLF/LF/mixed 3개 test_vector에서 동일 SHA(66f34ed4c2bd) 반환 — 실행 확인 / MT-8: PASS
— MT-8 AC-1 — 해당 TC 테스트 통과 (25 passed)
결과: PASS

AC-2:
요구사항:
  TC-2: trailing newline 유무가 정의된 canonical 규칙대로 처리된다 (strip 금지
  , 원문 유지)
구현 작업: MT-1, MT-8
구현 근거: MT-1: _canonical_pr_body_sha256(text: str) -> str 신규 함수를 _sha256_file 직후에 추가. CRLF(
)와 lone CR()을 LF(
)로 정규화 후 UTF-8 인코딩하여 hashlib.sha256().hexdigest() 반환 (AC-1). / MT-1: trailing newline을 strip하지 않아 no/single/double
trailing newline이 서로 다른 SHA를 생성 — GitHub API body 저장값 재현성 보장 (AC-2).
검증: MT-1: PASS — no/single/double trailing newline이 서로 다른 SHA 생성, strip 미적용 — 실행 확인 / MT-8: PASS — MT-8 AC-2 — 해당 TC 테스트
통과 (25 passed)
결과: PASS

AC-3:
요구사항:
  TC-3: gh pr view --json body 결과를 Python JSON parser로 읽은 값과 내
  부 PR body 문자열이 helper 통과 후 같은 SHA이다
구현 작업: MT-2, MT-4, MT-8
구현 근거: MT-2: _prepare_acceptance_snapshot_candidate의 line ~20503 직접 hashlib.sha256(pr_body.encode)를
_canonical_pr_body_sha256(pr_body)로 교체 (AC-9). / MT-2: candidate body SHA 계산을 canonical SSoT helper로 통일 (AC-3).
검증: MT-2: PASS — MT-2 AC-3 — canonical helper 통일 후 py_compile/import 및 회귀 확인 / MT-4: PASS — MT-4 AC-3 — canonical helper
통일 후 py_compile/import 및 회귀 확인
결과: PASS

AC-4:
요구사항:
  TC-4: jq stdout 또는 PowerShell stdout에 추가 개행이 붙어도 SHA 기준이 흔들리
  지 않는다 (canonical helper가 내부 정규화 수행)
구현 작업: MT-6, MT-7, MT-8
구현 근거: MT-6: _cmd_gates_codex_review(플랜의 _cmd_codex_record)의 pr_body_sha final_body 계산을 _canonical_pr_body_sha256으로 통일
(AC-5). / MT-6: jq/stdout 정규화 차이 제거로 SHA 안정화 (AC-4). github_canonical_pr_body_sha256 필드는 codex_review_result에 이미 분리 존재
(AC-6).
검증: MT-6: PASS — MT-6 AC-4 — canonical helper 통일 후 py_compile/import 및 회귀 확인 / MT-7: PASS — MT-7 AC-4 — canonical helper
통일 후 py_compile/import 및 회귀 확인
결과: PASS

AC-5:
요구사항:
  TC-5: request-accept 직후 codex_review_result.pr_body_candidat
  e_sha256 == acceptance_request.pr_body_candidate_sha256
구현 작업: MT-5, MT-6, MT-7
구현 근거: MT-5: _cmd_gates_request_accept의 _new_pr_body_sha256 및 stale 조기검사 직접 hashlib를 canonical helper로 교체 (AC-9). /
MT-5: _get_staged_pr_body_sha의 final_body candidate SHA 2곳을 canonical helper로 통일 — codex/acceptance_request candidate 3자
일치 (AC-5).
검증: MT-5: PASS — MT-5 AC-5 — canonical helper 통일 후 py_compile/import 및 회귀 확인 / MT-6: PASS — MT-6 AC-5 — canonical helper
통일 후 py_compile/import 및 회귀 확인
결과: PASS

AC-6:
요구사항:
  TC-6: PR body publish 후 GitHub에서 fetch한 canonical SHA == acc
  eptance_request.github_canonical_pr_body_sha256
구현 작업: MT-4, MT-6, MT-7
구현 근거: MT-4: _fetch_canonical_pr_body_sha256 inline 정규화+hashlib를 _canonical_pr_body_sha256(body)로 교체 (AC-6). / MT-4:
_get_pr_body_text를 --jq .body에서 --json body + Python JSON parse로 변경 — jq stdout trailing newline 오염 제거 (AC-3).
검증: MT-4: PASS — MT-4 AC-6 — canonical helper 통일 후 py_compile/import 및 회귀 확인 / MT-6: PASS — MT-6 AC-6 — canonical helper
통일 후 py_compile/import 및 회귀 확인
결과: PASS

AC-7:
요구사항:
  TC-7: gates accept가 동일 canonical helper로 current PR body sta
  le 검증을 수행한다
구현 작업: MT-3, MT-8
구현 근거: MT-3: _verify_published_canonical_pr_body의 recompute_sha 직접 hashlib를 _canonical_pr_body_sha256(current_pr_body)로
교체 (AC-9). / MT-3: gates accept 시 current PR body stale 검증이 canonical helper로 계산 (AC-7).
검증: MT-3: PASS — MT-3 AC-7 — canonical helper 통일 후 py_compile/import 및 회귀 확인 / MT-8: PASS — MT-8 AC-7 — 해당 TC 테스트 통과 (25
passed)
결과: PASS

AC-8:
요구사항:
  TC-8: candidate SHA와 github canonical SHA를 의도적으로 다르게 만든 경우 올
  바른 필드에서만 차이를 보고, 잘못된 stale로 오판하지 않는다
구현 작업: MT-7, MT-8
구현 근거: MT-7: acceptance_request.json에 pr_body_candidate_sha256(로컬 예정 body) + github_canonical_pr_body_sha256(publish 후
GitHub 실제 body) 필드 분리 추가 (AC-8). / MT-7: _publish_acceptance_request가 publish 직후 github_canonical_pr_body_sha256을
canonical fetch 값으로 기록 (AC-6). candidate/canonical 필드 혼용 없음 (AC-8).
검증: MT-7: PASS — MT-7 AC-8 — canonical helper 통일 후 py_compile/import 및 회귀 확인 / MT-8: PASS — MT-8 AC-8 — 해당 TC 테스트 통과 (25
passed)
결과: PASS

AC-9:
요구사항:
  TC-9: 직접 hashlib.sha256(pr_body.encode(...)) 호출이 PR body SHA
   경로에 남아 있지 않다 (canonical helper 외부)
구현 작업: MT-2, MT-3, MT-5, MT-8
구현 근거: MT-2: _prepare_acceptance_snapshot_candidate의 line ~20503 직접 hashlib.sha256(pr_body.encode)를
_canonical_pr_body_sha256(pr_body)로 교체 (AC-9). / MT-2: candidate body SHA 계산을 canonical SSoT helper로 통일 (AC-3).
검증: MT-2: PASS — MT-2 AC-9 — canonical helper 통일 후 py_compile/import 및 회귀 확인 / MT-3: PASS — MT-3 AC-9 — canonical helper
통일 후 py_compile/import 및 회귀 확인
결과: PASS

AC-10:
요구사항:
  TC-10/11: 기존 E69E Codex CLI ERROR/REJECT 상태 모델 테스트 및 F52C st
  aged snapshot/packet SHA 검증 테스트가 깨지지 않는다
구현 작업: MT-8
구현 근거: MT-8: tests/test_canonical_pr_body_sha256_b985.py 신규 작성 — 18개 테스트 (TC-1~TC-11 커버). / MT-8: TC-1 CRLF/LF/lone-CR
정규화 동일 SHA, TC-2 trailing newline 구분(strip 없음), TC-3 --json body JSON parse vs jq stdout, TC-9 canonical helper 외부 직접
hashlib 0건 회귀, TC-11 F52C/E69E 호환성 검증.
검증: MT-8: PASS — MT-8 AC-10 — 해당 TC 테스트 통과 (25 passed)
결과: PASS

사용자가 확인할 것:

1. PR 링크를 연다.
2. GitHub Actions 자동 검사가 성공인지 본다.
3. 요구사항 충족표를 본다.
4. 결과물이 요청과 맞으면 아래 승인 코드를 GitHub PR 댓글에 한 줄로 남긴다.
   현재 허용 승인자: hojiyong2-commits
   Claude/Codex가 대신 입력할 수 없습니다. 반드시 사람이 직접 입력해야 합니다.
5. 틀리면 거절 코드 뒤에 이유를 적는다.

[승인 코드]
ACCEPT-IMP-20260703-B985

[거절 예시]
REJECT-IMP-20260703-B985: 이유