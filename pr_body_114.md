## 최종 판단 요약

**요청:** GPT/OpenAI advisory를 기본 완료 경로에서 제거하고 수동 red-team 진단 전용으로 강등.
FAIL/BLOCKED 발생 시 즉시 원인과 다음 조치를 알 수 있도록 피드백 계약(failure_packet schema_v2) 표준화.

**완료된 내용:**
- `ENABLE_GPT_ADVISORY=1` (API 호출 허용)과 `ENABLE_GPT_ADVISORY_REQUIRED=1` (자동 실행+COMPLETE 차단) 분리 완료
- `ENABLE_GPT_ADVISORY_REQUIRED=1`이면 `ENABLE_GPT_ADVISORY` 없이도 API 호출 가능하도록 수정
- REQUIRED 모드에서 advisory 미실행(review_count=0) 또는 API 미호출(api_call_count=0)이면 COMPLETE 차단
- 에러 메시지/문서의 낡은 `review codex --result ACCEPT` 안내를 `review codex-run`으로 전면 교체 (pipeline.py, CLAUDE.md, task.md, agent MD 파일 포함)
- Codex PR gate 실패 경로(`gates technical`, `gates accept`)에 failure_packet schema_v2 생성 연결
- `check --phase dev/qa` 실패 시에도 failure_packet 생성 (stale_evidence/model_verification_failed/missing_evidence 분류)
- failure_packet schema_v2: pipeline_id/phase/gate/status/failure_code/failure_category/summary_ko/blocking_condition/expected/actual/evidence_paths/command/exit_code/owner/return_phase/minimal_rerun/required_actions/retry_allowed/attempt_count/created_at 필드 포함

## 사용자가 확인할 결과물

- `pipeline.py` — `_call_openai_advisory`, `_advisory_run_counts`, `_external_gate_blockers`, `_check_codex_review_gate`, `cmd_check` 함수 변경
- `tests/test_advisory_demotion.py` — 신규 테스트 5개 추가 (advisory 강등 4 + commands 디렉토리 grep 1)
- `tests/test_failure_feedback.py` — `TestCodexGateFailurePacket` 클래스를 4개 실측 테스트로 전면 교체
- `CLAUDE.md`, `.claude/commands/task.md` — 금지 패턴 제거 완료
- 전체 pytest: **379 passed** (회귀 없음)
- Technical gate: **PASS** (ruff/mypy/bandit/pytest 모두 통과)
- GitHub CI: **PASS** (run 26078442528)

## 기대 결과와 실제 결과

| 항목 | 기대 | 실제 |
|---|---|---|
| REQUIRED=1 + API key + ENABLE_GPT_ADVISORY 없음 | API 호출 경로 진입 (SKIPPED 아님) | 완료: `_call_openai_advisory`가 SKIPPED 반환 안 함 |
| REQUIRED=1 + advisory 미실행 | COMPLETE blocker | 완료: `review_count=0` blocker 추가됨 |
| REQUIRED=1 + 모든 결과 SKIPPED/ERROR | COMPLETE blocker | 완료: `api_call_count=0` blocker 추가됨 |
| `review codex --result ACCEPT` 안내 제거 | 모든 소스 파일에 없어야 함 | 완료: pipeline.py·CLAUDE.md·task.md·agent MD 전체 교체, grep 테스트 5개 통과 |
| Codex PR gate 실패 시 failure_packet | schema_v2 파일 실제 생성 | 완료: 4개 케이스 JSON 파일 생성 검증 |
| check --phase dev/qa 실패 시 failure_packet | BLOCKED 상태 + 패킷 생성 | 완료: stale_evidence/model_verification_failed/missing_evidence 분류 |
| 기본 모드 (REQUIRED 미설정) | advisory가 COMPLETE blocker 아님 | 완료: 기존 테스트 15개 모두 통과 |

## 검증

- `python -m pytest -q`: **379 passed**, 실패 없음
- `python -m pytest tests/test_advisory_demotion.py::TestForbiddenReviewCodexPattern -v`: **5/5 PASS** (pipeline.py, CLAUDE.md, agents, workflows, commands 전체)
- `python -m pytest tests/test_failure_feedback.py::TestCodexGateFailurePacket -v`: **4/4 PASS** (실제 JSON 파일 생성 검증)
- Technical gate: ruff PASS / mypy PASS / bandit PASS / pytest PASS
- GitHub CI run 26078442528: COMPLETED / SUCCESS

## 중요한 선택과 트레이드오프

**`_advisory_run_counts()` 헬퍼 분리:**
- 장점: `_external_gate_blockers`에서 직접 advisory 파일을 스캔하는 로직이 테스트 가능해짐 (mock 주입 가능)
- 단점: 새 함수 추가로 인터페이스가 늘어남
- 선택 이유: 기존 `_unresolved_critical_advisories` 패턴을 따름 (일관성)

**REQUIRED=1이면 ENABLE_GPT_ADVISORY 없어도 API 호출 허용:**
- 장점: REQUIRED=1 하나만 설정해도 모든 필요 동작이 활성화됨 (사용자 편의)
- 단점: 두 플래그의 의미가 완전히 독립적이지 않음
- 선택 이유: CLAUDE.md 문서에 "REQUIRED=1이면 ENABLE_GPT_ADVISORY=1로 간주" 명시되어 있었으나 구현이 누락된 상태였음

## 남은 위험과 주의점

- `ENABLE_GPT_ADVISORY_REQUIRED=1` 모드에서 실제 OpenAI API를 사용하려면 `OPENAI_API_KEY` 환경변수 설정 필요 (설정 없으면 COMPLETE blocker로 기록됨)
- advisory 모델은 `gpt-5.5`로 고정 (다른 모델은 허용되지 않음)

## 추가 개선 포인트

- advisory `status_counts` 통계에서 SKIPPED/ERROR를 별도 집계하는 UI 개선 가능 (이번 범위 밖)
