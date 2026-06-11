# Legacy Cleanup Execution Report — IMP-20260611-3F91

**파이프라인:** IMP-20260611-3F91
**날짜:** 2026-06-11
**목적:** harness CLI 차단 명확화 + AGENTS.md 경로 정정 + 레거시 정리 결과 기록

---

## REMOVED

| 항목 | 파일 | 함수/인자명 | 라인 번호 | 변경 전/후 |
|---|---|---|---|---|
| `--score` required=True 제거 | pipeline.py | `build_parser()` 내 `p_harness.add_argument("--score", ...)` | 18399 | `required=True` → `required=False, default=None` |
| `--verdict` required=True 제거 | pipeline.py | `build_parser()` 내 `p_harness.add_argument("--verdict", ...)` | 18400 | `required=True` → `required=False, default=None` |

---

## MODIFIED

| 항목 | 파일 | 함수/인자명 | 라인 번호 | 변경 내용 |
|---|---|---|---|---|
| harness 서브파서 `--score` 인자 | pipeline.py | `build_parser()` → `p_harness.add_argument("--score", ...)` | 18399 | `required=True` → `required=False, default=None`. help 문자열: "Legacy diagnostic percentage only; not a completion score (ignored — command always blocked)" |
| harness 서브파서 `--verdict` 인자 | pipeline.py | `build_parser()` → `p_harness.add_argument("--verdict", ...)` | 18400 | `required=True` → `required=False, default=None`. `choices=["PASS","FAIL","pass","fail"]` 유지. |
| 에이전트 경로 참조 수정 | AGENTS.md | 에이전트 테이블(lines 62-74), 금지 목록, cleanup 섹션, 보호 파일 목록 | 전체 20건 | `.Codex/agents` → `.claude/agents` 경로 20건 전체 교체 |

---

## PRESERVED

| 항목 | 파일 | 함수/인자명 | 라인 번호 | 이유 |
|---|---|---|---|---|
| `_parse_harness_report_et` 함수 | pipeline.py | `def _parse_harness_report_et(clean_text: str)` | 114 | `_extract_test_code()` 함수(line 145)에서 호출됨 확인. 레거시 테스트 증거 검증 체인 일부로 안전하게 삭제 불가. |
| `_extract_test_code` 함수 | pipeline.py | `def _extract_test_code(agent_output: str)` | 145 | `validate_test_evidence()` 함수(line 464) 호출 체인 하위. `cmd_harness` 독립 차단 후에도 unit test/diagnostic용으로 유지. |
| `cmd_harness` 함수 본문 | pipeline.py | `def cmd_harness(args: argparse.Namespace)` | 7216 | `_die("[THREE GATE BLOCKED] ...")` 호출로 항상 차단. 함수 자체는 유지하되 THREE GATE BLOCKED 메시지 + `sys.exit(1)` 보장. |
| 기존 E2E 테스트 | tests/e2e/ | (기존 파일 전체) | — | 기존 테스트 파일 그대로 보존. MT-2에서 `test_harness_block_3f91.py`만 신규 추가. |
| `tests/test_legacy_cleanup_b0ab.py` | tests/ | `test_harness_score_blocked()` | — | `test_harness_score_blocked()`가 `harness --score 100 --verdict PASS` 호출 시 THREE GATE BLOCKED + exit!=0 확인. 계속 PASS 유지됨. |

---

## DEFERRED

| 항목 | 파일 | 함수/인자명 | 라인 번호 | 이유 |
|---|---|---|---|---|
| `_parse_harness_report_et` 삭제 | pipeline.py | `def _parse_harness_report_et(clean_text: str)` | 114 | `_extract_test_code()`(line 145) → `_parse_harness_report_et()`(line 155) 호출 관계 확인됨. call graph 전체 분석 없이 즉시 삭제 불가. 별도 파이프라인에서 결정 필요. |
| `pm-planner-agent.md` 생성 | .claude/agents/ | — | — | 해당 파일이 현재 존재하지 않음 (`.claude/agents/pm-planner-agent.md` 없음). 경로 참조만 수정. 파일 생성은 별도 IMP 파이프라인에서 진행. |
| `pipeline-manager-agent.md` 생성 | .claude/agents/ | — | — | 동일 사유. `.claude/agents/pipeline-manager-agent.md` 없음. 파일 생성은 별도 IMP 파이프라인에서 진행. |
| `tests/oracles/` orphaned 파일 정리 | tests/oracles/ | — | — | 현재 파이프라인 scope 외. 별도 hygiene 파이프라인에서 처리. |

---

## 검증 결과

| AC | 설명 | 결과 |
|---|---|---|
| AC-1 | `python pipeline.py harness` → THREE GATE BLOCKED + exit 1 (argparse 오류 없음) | PASS |
| AC-2 | `python pipeline.py harness --score 100 --verdict PASS` → THREE GATE BLOCKED + exit 1 | PASS |
| AC-3 | `cmd_harness()` 함수(line 7216)가 항상 `_die("[THREE GATE BLOCKED]")` 반환 | PASS |
| AC-4 | `_parse_harness_report_et`(line 114) call graph 분석 | DEFERRED — 삭제 위험으로 보류 |
| AC-5 | AGENTS.md `.Codex/agents` → `.claude/agents` 20건 수정. `grep ".Codex/agents" AGENTS.md` = 0 | PASS |
| AC-6 | `pm-planner-agent.md`, `pipeline-manager-agent.md` 파일 미생성 확인 | PASS (DEFERRED — 파일 부재) |
| AC-7 | 본 파일 존재 + REMOVED/MODIFIED/PRESERVED/DEFERRED 4개 섹션 포함 | PASS |
| AC-8 | 변경 파일 4개 범위 내 (pipeline.py, tests/e2e/test_harness_block_3f91.py, AGENTS.md, legacy_cleanup_execution_report.md) | PASS |
| AC-9 | Technical/Oracle/GitHub CI gate PASS + request-accept 승인 코드 발급 후 사용자 ACCEPT | PENDING (Phase 7 완료 후 확인) |
