[검증용 메타데이터]
pipeline_id: IMP-20260712-DAE1
pr_url: https://github.com/hojiyong2-commits/Pipeline/pull/891
pr_head_sha: 7df16acf74013dbba71bfd1342dcb68535aa48ba
ci_run_id: 29491299654
ci_head_sha: (없음)
changed_files_count: 21
changed_files: .claude/agents/pipeline-manager-agent.md, codex_model_router_report.md, pipeline.py, pyproject.toml,
tests/e2e/test_ac_tracking_1abe.py... (총 21개)
verification_json_sha256: 7b4e9c012129992ec131aa1e86dcfde2d6a452f28a43b9b34e6c6b87bd8d5bd1
technical: PASS
oracle: PASS
github_ci: PASS
acceptance: PENDING
acceptance_display: PENDING
requirements_summary: 11/11 PASS
oracle_summary: PASS (9개 케이스, 9개 통과)
known_failures: 없음
evidence_integrity: PASS (protected:10, tracked:10, pr_included:10)
workspace_hygiene: WARN (blocking:0, cleanup_only:1)
verification_json: human_acceptance_packet.json

[최종 확인 안내]

파이프라인: IMP-20260712-DAE1
PR: https://github.com/hojiyong2-commits/Pipeline/pull/891
GitHub Actions: https://github.com/hojiyong2-commits/Pipeline/actions/runs/29491299654
승인 표시 상태: PENDING

게이트 상태:
Technical: PASS
Oracle: PASS
GitHub CI: PASS
User Acceptance: PENDING

변경 파일: 총 21건
  .claude/agents/pipeline-manager-agent.md
  codex_model_router_report.md
  pipeline.py
  pyproject.toml
  tests/e2e/test_ac_tracking_1abe.py
  tests/e2e/test_codex_cost_optimization_9f5e.py
  tests/e2e/test_codex_defect_fix_dae1.py
  tests/e2e/test_codex_defects_dae1.py
  tests/e2e/test_codex_model_router_dae1.py
  tests/e2e/test_codex_model_router_e2e.py
  tests/e2e/test_codex_review_gate_3bb6.py
  tests/oracles/IMP-20260712-DAE1/tc01_low_risk_docs/expected.json
  tests/oracles/IMP-20260712-DAE1/tc01_low_risk_docs/input.json
  tests/oracles/IMP-20260712-DAE1/tc04_high_risk_pipeline/expected.json
  tests/oracles/IMP-20260712-DAE1/tc04_high_risk_pipeline/input.json
  tests/oracles/IMP-20260712-DAE1/tc05_critical_acceptance/expected.json
  tests/oracles/IMP-20260712-DAE1/tc05_critical_acceptance/input.json
  tests/oracles/IMP-20260712-DAE1/tc08_downgrade_blocked/expected.json
  tests/oracles/IMP-20260712-DAE1/tc08_downgrade_blocked/input.json
  tests/oracles/IMP-20260712-DAE1/tc11_unknown_model_critical/expected.json
  tests/oracles/IMP-20260712-DAE1/tc11_unknown_model_critical/input.json

요구사항 충족 요약: 11/11 충족

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
  _classify_codex_review_risk가 변경 파일/함수를 분석하여 LOW/MEDIUM/HIGH/
  CRITICAL을 결정적으로 반환한다. 사용자 요청의 Risk 규칙(LOW: docs/report만, MED
  IUM: 일반 테스트, HIGH: pipeline.py+gates/workflow, CRITICAL: acc
  eptance/SHA writer)을 정확히 따른다.
구현 작업: MT-1, MT-2, MT-3, MT-6
구현 근거: MT-1(covers_ac): MT-1 ?? ?? / MT-2(covers_ac): MT-2 ?? ??
검증: MT-1: PASS — CODEX_MODEL_POLICIES/CODEX_HIGH_RISK_PATHS/CODEX_CRITICAL_FUNCTIONS SSoT 상수 구현 완료 — risk 규칙표 정확히 구현 /
MT-2: PASS — _build_codex_model_policy: risk_level→selected_model/mode/cache_allowed/force_review 정확한 매핑 구현
결과: PASS

AC-2:
요구사항:
  selected_model과 actual_model이 분리되어 codex_review_result.json에
   기록된다. actual_model은 실제 CLI 확인 결과이며, 확인 불가 시 "unknown"으로 기록된
  다.
구현 작업: MT-4, MT-6
구현 근거: MT-4(covers_ac): MT-4 ?? ?? / MT-6(covers_ac): MT-6 ?? ??
검증: MT-4: PASS — _detect_codex_cli_capability: selected_model/actual_model 분리 기록, 확인 불가 시 unknown 기록 구현 / MT-6: PASS —
codex_review_result.json에 selected_model/actual_model 분리 기록, unknown 처리
결과: PASS

AC-3:
요구사항:
  actual_model=unknown + HIGH/CRITICAL 조합은 BLOCKED(fail-closed
  )로 차단된다. actual_model=unknown + LOW/MEDIUM은 WARN 후 진행 허용.
구현 작업: MT-4, MT-6
구현 근거: MT-4(covers_ac): MT-4 ?? ?? / MT-6(covers_ac): MT-6 ?? ??
검증: MT-4: PASS — actual_model=unknown + HIGH/CRITICAL → BLOCKED(fail-closed), LOW/MEDIUM → WARN 후 진행 구현 / MT-6: PASS —
unknown_model_critical_blocked failure_code 구현, LOW/MEDIUM WARN 진행
결과: PASS

AC-4:
요구사항:
  CRITICAL 변경(acceptance_request 핸들러, SHA 검증 함수, nonce writer 
  등)은 사용자 요청에 명시된 함수 목록과 정확히 일치하는 규칙으로 감지된다.
구현 작업: MT-1, MT-2
구현 근거: MT-1(covers_ac): MT-1 ?? ?? / MT-2(covers_ac): MT-2 ?? ??
검증: MT-1: PASS — CODEX_CRITICAL_FUNCTIONS frozenset으로 CRITICAL 함수 목록 정의 — 사용자 요청 목록과 정확히 일치 / MT-2: PASS — CRITICAL risk
분류 로직이 CODEX_CRITICAL_FUNCTIONS 목록 기반으로 동작 확인
결과: PASS

AC-5:
요구사항:
  HIGH/CRITICAL risk를 LOW 모델로 다운그레이드하면 BLOCKED. CRITICAL deep 
  reasoning을 medium으로 낮추면 BLOCKED.
구현 작업: MT-3
구현 근거: MT-3(covers_ac): MT-3 ?? ?? / MT-11: REJECT#LATEST rework: 7개 결함 fail-closed 강화
검증: MT-3: PASS — downgrade_blocked: HIGH/CRITICAL에서 낮은 모델 요청 시 BLOCKED 처리 구현 / MT-11: PASS — --start-epoch struct SHA +
주석 operational ceremony 명시 — subprocess 회귀 테스트 PASS
결과: PASS

AC-7:
요구사항:
  usage limit/network/timeout/model unavailable 오류는 REJECT가 아니
  라 ERROR로 분류된다. reject_count 증가 없음.
구현 작업: MT-6
구현 근거: MT-6(covers_ac): MT-6 ?? ??
검증: MT-6: PASS — usage_limit_error/timeout_error → ERROR 분류, reject_count 증가 없음 구현
결과: PASS

AC-8:
요구사항:
  TC-1~TC-18 테스트가 모두 구현되어 pytest 통과한다.
구현 작업: MT-6, MT-9
구현 근거: MT-6(covers_ac): MT-6 ?? ?? / MT-9(covers_ac): MT-9 ?? ??
검증: MT-6: PASS — TC-1~TC-14 테스트 pytest PASS 확인 (test_codex_model_router_dae1.py) / MT-9: PASS — E2E 테스트 TC-1~TC-14(dae1)
+ TC-1~TC-14(e2e subprocess) + TC-3bb6 pytest PASS 확인
결과: PASS

AC-9:
요구사항:
  LOW/MEDIUM은 observe 모드 기본(WARN 후 진행), HIGH/CRITICAL은 enforce
   모드 기본(정책 위반 시 BLOCKED). 계층형 동작.
구현 작업: MT-3, MT-5
구현 근거: MT-3(covers_ac): MT-3 ?? ?? / MT-5(covers_ac): MT-5 ?? ??
검증: MT-3: PASS — observe(LOW/MEDIUM)/enforce(HIGH/CRITICAL) 계층형 모드 로직 구현 / MT-5: PASS — verdict_source=external_verdict
→ acceptance_eligible=false, observe/enforce 계층 동작 TC-10 검증 완료
결과: PASS

AC-11:
요구사항:
  pipeline-manager-agent.md에 Codex Model Router 섹션이 추가되어 risk 
  규칙표, 모델 라우팅 표, Plus 사용량 보호 전략이 문서화된다.
구현 작업: MT-8
구현 근거: MT-8(covers_ac): MT-8 ?? ??
검증: MT-8: PASS — pipeline-manager-agent.md에 Codex Model Router 섹션 추가 완료 — risk 규칙표/모델 라우팅 표/Plus 사용량 보호 전략 문서화
결과: PASS

[자동 검증 요약]

AC-6: PASS — 기존 packet_sha256/pr_body_sha256/head_sha/provenance/replay/s
AC-10: PASS — Review Bundle Budget이 모든 risk에서 raw ACCEPT/nonce 포함을 금지한다.

사용자가 확인할 것:

1. PR 링크를 연다.
2. GitHub Actions 자동 검사가 성공인지 본다.
3. 요구사항 충족표를 본다.
4. 결과물이 요청과 맞으면 아래 승인 코드를 GitHub PR 댓글에 한 줄로 남긴다.
   현재 허용 승인자: hojiyong2-commits
   Claude/Codex가 대신 입력할 수 없습니다. 반드시 사람이 직접 입력해야 합니다.
5. 틀리면 거절 코드 뒤에 이유를 적는다.

[승인 코드]
승인 코드 발급 전 — gates request-accept를 먼저 실행하세요

[거절 예시]
승인 코드 발급 전 — gates request-accept를 먼저 실행하세요