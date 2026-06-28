[검증용 메타데이터]
pipeline_id: BUG-20260628-1AAC
pr_url: https://github.com/hojiyong2-commits/Pipeline/pull/780
pr_head_sha: 7c86b730e1b051a913266b7cbbe4d115ff370763
ci_run_id: 28315085388
ci_head_sha: 7c86b730e1b051a913266b7cbbe4d115ff370763
changed_files_count: 35
changed_files: .claude/agents/pipeline-manager-agent.md, .claude/codex_review_contract.md, .claude/commands/task.md,
.claude/hooks/codex-user-acceptance-review.ps1, .claude/hooks/codex_user_acceptance_review.py... (총 35개)
verification_json_sha256: 4f0ec434f8b735d4119c2f0aaf7322871fd59f70e61db53769120d2b77a0538f
packet_md_sha256: c380613f750e25e99ce11ba2dca159a498f667532ed7195afe02faeba7957c18
technical: PASS
oracle: PASS
github_ci: PASS
acceptance: PENDING
acceptance_display: PENDING
requirements_summary: 3/3 PASS
oracle_summary: PASS (5개 케이스, 5개 통과)
known_failures: 없음
evidence_integrity: PASS (protected:6, tracked:6, pr_included:6)
workspace_hygiene: WARN (blocking:0, cleanup_only:2)
verification_json: human_acceptance_packet.json

[최종 확인 안내]

파이프라인: BUG-20260628-1AAC
PR: https://github.com/hojiyong2-commits/Pipeline/pull/780
GitHub Actions: https://github.com/hojiyong2-commits/Pipeline/actions/runs/28315085388
승인 표시 상태: PENDING

게이트 상태:
Technical: PASS
Oracle: PASS
GitHub CI: PASS
User Acceptance: PENDING

변경 파일: 총 35건
  .claude/agents/pipeline-manager-agent.md
  .claude/codex_review_contract.md
  .claude/commands/task.md
  .claude/hooks/codex-user-acceptance-review.ps1
  .claude/hooks/codex_user_acceptance_review.py
  .claude/settings.json
  pipeline.py
  pyproject.toml
  tests/e2e/test_codex_review_gate_3bb6.py
  tests/e2e/test_real_cli_paths.py
  tests/oracles/BUG-20260628-9B39/edge_info_prefix_invalid/expected.json
  tests/oracles/BUG-20260628-9B39/edge_info_prefix_invalid/input.json
  tests/oracles/BUG-20260628-9B39/exception_trustroot_excluded_blocked/expected.json
  tests/oracles/BUG-20260628-9B39/exception_trustroot_excluded_blocked/input.json
  tests/oracles/BUG-20260628-9B39/normal_prbody_masked/expected.json
  tests/oracles/BUG-20260628-9B39/normal_prbody_masked/input.json
  tests/oracles/IMP-20260625-CD3C/edge_01/actual.json
  tests/oracles/IMP-20260625-CD3C/edge_01/expected.json
  tests/oracles/IMP-20260625-CD3C/edge_01/input.json
  tests/oracles/IMP-20260625-CD3C/normal_01/actual.json
  tests/oracles/IMP-20260625-CD3C/normal_01/expected.json
  tests/oracles/IMP-20260625-CD3C/normal_01/input.json
  tests/oracles/IMP-20260625-CD3C/settings_01/actual.json
  tests/oracles/IMP-20260625-CD3C/settings_01/expected.json
  tests/oracles/IMP-20260627-3BB6/edge_codex_reject/expected.json
  tests/oracles/IMP-20260627-3BB6/edge_codex_reject/input.json
  tests/oracles/IMP-20260627-3BB6/edge_stale_sha/expected.json
  tests/oracles/IMP-20260627-3BB6/edge_stale_sha/input.json
  tests/oracles/IMP-20260627-3BB6/exception_missing_review/expected.json
  tests/oracles/IMP-20260627-3BB6/exception_missing_review/input.json
  tests/oracles/IMP-20260627-3BB6/normal_approve/expected.json
  tests/oracles/IMP-20260627-3BB6/normal_approve/input.json
  tests/test_acceptance_request_format_069a.py
  tests/test_codex_review_hook_cd3c.py
  tests/test_codex_review_loop_4121.py

요구사항 충족 요약: 3/3 충족

작업공간 정리 상태: WARN (blocking:0, cleanup_only:2)

워크스페이스 정리 요약:
Workspace Readiness: unknown
삭제 완료: 0개 파일
보류(deferred): 0개 파일
Unknown(보존): 0개 파일

요구사항 충족표:

[요구사항 충족표]

AC-1:
요구사항:
  _parse_codex_verdict이 INFO:/WARNING: prefix로 시작하는 줄을 first_a
  i_line으로 처리하여 APPROVE_TO_USER가 이후에 나와도 INVALID를 반환한다
구현 작업: MT-1
구현 근거: MT-1: _CODEX_CLI_SYSTEM_PREFIXES 튜플에서 "INFO: "와 "WARNING: " 항목 제거 (pipeline.py:6761) — 이제 INFO:/WARNING: prefix
줄도 AI 출력으로 취급되어 _parse_codex_verdict가 형식 불일치 INVALID 반환 / MT-1: test_tc14_info_prefix_invalid: 'INFO: 문제
없음\nAPPROVE_TO_USER' 입력 시 gates codex-review exit 1 + codex_verdict_invalid 검증
검증: MT-1: PASS — git diff 조회 실패(FileNotFoundError/TimeoutExpired/OSError/returncode!=0) 시
_die(codex_review_diff_unavailable) — placeholder '(diff 조회 실패)' 제거 확인
결과: PASS

AC-2:
요구사항:
  _cmd_gates_codex_review가 pr_body를 Codex 프롬프트에 포함하기 전에 ACCEPT
  -* 패턴을 [ACCEPT코드 마스킹]으로 치환한다
구현 작업: MT-2
구현 근거: MT-2: _cmd_gates_codex_review에서 pr_body[:5000]를 Codex 프롬프트에 삽입하기 전 re.sub(r'ACCEPT-[A-Z]+-\d{8}-[0-9A-F]{4}',
'[ACCEPT코드 마스킹]', ...)로 마스킹한 pr_body_masked 변수를 사용 (pipeline.py 프롬프트 구성부). pr_body_sha256은 마스킹 전 원본에서 계산되어 SHA 검증 무영향. /
MT-2: test_tc15_prbody_accept_code_masked: PR body에 ACCEPT-IMP-...-A1B2 코드를 포함한 fake gh 주입 후 stdin 캡처로 원문 미노출 +
'[ACCEPT코드 마스킹]' placeholder 포함 검증
검증: MT-2: PASS — pipeline._cmd_gates_codex_review에서 pr_body를 Codex 프롬프트에 포함하기 전 re.sub(ACCEPT-* 패턴, [ACCEPT코드 마스킹]) 치환
로직 구현 확인 — test_tc15_prbody_accept_code_masked P
결과: PASS

AC-3:
요구사항:
  CODEX_REVIEW_CRITICAL_FILES SSoT 상수가 정의되고, critical 파일이 excl
  uded_files에 포함되면 codex_review_diff_incomplete로 BLOCKED된다
구현 작업: MT-3
구현 근거: MT-3: CODEX_REVIEW_CRITICAL_FILES SSoT 상수 신규 정의 (.claude/codex_review_contract.md, .claude/agents/, pipeline.py,
CLAUDE.md, .github/workflows/) + _is_codex_critical_file() 판정 헬퍼 추가 (정확 경로/prefix/Windows 구분자 정규화 지원) / MT-3:
_cmd_gates_codex_review에서 included_files 검증 직후 _excluded_critical = [p for p in excluded_files if
_is_codex_critical_file(p)] 검사 추가 — trust-root 파일이 budget 초과로 제외되면 failure_code=codex_review_diff_incomplete로 _die(exit
1) BLOCKED
검증: MT-3: PASS — CODEX_REVIEW_CRITICAL_FILES SSoT 상수 정의 확인 + _is_codex_critical_file() 헬퍼 구현 + critical 파일이
excluded_files에 포함 시 codex_review_diff_incomplete로 BLOCKED 
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
ACCEPT-BUG-20260628-1AAC

[거절 예시]
REJECT-BUG-20260628-1AAC: 이유