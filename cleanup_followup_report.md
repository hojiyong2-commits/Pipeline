# IMP-20260611-508F Cleanup Followup Report

**파이프라인:** IMP-20260611-508F
**날짜:** 2026-06-11
**목적:** Protocol Evolution `<recommended_pipeline_type>` 오류 메시지 개선 + 문서 보강 + 회귀 테스트 추가, 그리고 직전 파이프라인 IMP-20260611-3F91의 DEFERRED 항목 재audit.

이 보고서는 코드/oracle을 삭제하지 않는 **분석/audit 산출물**입니다. 모든 식별자는 backtick으로 표기합니다.

---

## REMOVED

| 파일 | 함수/섹션 | 라인 | 처리 | 이유 | 안전 근거 |
|---|---|---|---|---|---|
| 해당 없음 | — | — | — | 본 파이프라인은 삭제(REMOVE) 작업을 포함하지 않음 | Frozen Codebase 원칙 — 불확실한 dead code 삭제 금지 |

---

## MODIFIED

| 파일 | 함수/섹션 | 라인 | 처리 | 이유 |
|---|---|---|---|---|
| `pipeline.py` | `_parse_protocol_evolution_decision` | ~1178-1192 | 오류 메시지 개선 | AC-1: `recommended_type != "IMP"` 분기의 `_die()` 메시지를 단일 줄에서 다중 줄로 확장. `actual : {recommended_type}`, `expected: IMP`, 복구용 `<protocol_evolution_decision>` XML 예시 포함. 차단 조건과 `_child_text(..., "IMP")` 기본값 로직은 불변. |
| `.claude/agents/prompt-architect-agent.md` | `## Protocol Evolution Decision` 섹션 | ~102 | 지침 보강 | AC-3: `required=false`이더라도 `<recommended_pipeline_type>`은 항상 `IMP`여야 하며 다른 값은 `pipeline.py architect`에서 즉시 BLOCKED, 자동 보정 없음을 명시한 blockquote 4줄 추가. |
| `AGENTS.md` | `## Phase 9 성격 작업: Protocol Evolution via Separate IMP` 섹션 | ~535 | 문서 보강 | AC-4: `protocol_evolution_decision` XML 예시 직후에 `recommended_pipeline_type=IMP` 필수 규칙 blockquote 4줄 추가. |
| `tests/e2e/test_architect_evo_type_508f.py` | `TestArchitectEvoType` (신규) | 신규 파일 | 회귀 테스트 추가 | AC-5/AC-6/AC-9/AC-10: subprocess 기반 실제 `pipeline.py architect` CLI 호출로 잘못된 `recommended_pipeline_type`(`T`, `FEAT`) 차단 메시지·exit code·복구 XML·traceback 부재를 검증하고, 정상 `IMP`는 type 차단 없이 통과함을 final_state로 검증. `PIPELINE_STATE_PATH` 격리 적용. |
| `cleanup_followup_report.md` | (본 파일) | 신규 파일 | 분석 보고서 생성 | AC-7/AC-8: 3F91 DEFERRED 항목 재audit + REMOVED/MODIFIED/PRESERVED/DEFERRED 4개 섹션 포함. |

---

## PRESERVED

| 파일 | 함수/섹션 | 라인 | 처리 이유 |
|---|---|---|---|
| `pipeline.py` | `_parse_harness_report_et` | 114 | `_extract_test_code()`(line 145)의 본문(line 155)에서 `root = _parse_harness_report_et(clean)`로 호출됨. call graph 상 활성 사용처가 존재하므로 안전하게 삭제 불가 → PRESERVED. |
| `pipeline.py` | `_extract_test_code` | 145 | `validate_test_evidence()`(line 464) 본문(line 511)에서 `code = _extract_test_code(agent_output)`로 호출됨. legacy harness 진단/unit test 증거 검증 경로에 사용되어 PRESERVED. |
| `pipeline.py` | `validate_test_evidence` | 464 | `_extract_test_code`의 상위 호출자. 레거시 진단용으로 유지. `# noqa: ARG001` 주석으로 향후 audit 예약 표시됨. |
| `pipeline.py` | `cmd_harness` | 7229 | IMP-20260611-3F91에서 PRESERVED. 현재 상태 확인 결과 함수 본문이 `_load_branch_state(args)` 직후 `_die("[THREE GATE BLOCKED] ...")`로 항상 차단됨. `--score`/`--verdict` 인자는 `required=False, default=None`(line 18429-18430)로 남아 argparse 오류(exit 2) 대신 THREE GATE BLOCKED(exit 1)를 반환. 상태 변경 없음. 정상 동작 유지되어 PRESERVED. |
| `pipeline.py` | `build_parser` 내 `--score`/`--verdict` 인자 | 18429-18430 | 3F91에서 `required=False`로 변경 완료. 제거하지 않고 유지하여 기존 `harness --score 100 --verdict PASS` 호출이 argparse 오류 없이 THREE GATE BLOCKED 경로로 흐르도록 함. 회귀 테스트(`test_harness_block_3f91.py`)가 이 동작을 보장하므로 PRESERVED. |
| `tests/e2e/test_harness_block_3f91.py` | 기존 harness 차단 테스트 | — | AC-9(기존 테스트 무손상) 대상. 본 파이프라인에서 미수정. `python -m pytest tests/e2e/test_harness_block_3f91.py -q` → 2 passed 확인. |

---

## DEFERRED

| 파일 | 함수/섹션 | 경로 | 이유 |
|---|---|---|---|
| `pipeline.py` | `_parse_harness_report_et` 삭제 | line 114 | 3F91에서 DEFERRED로 기록된 항목. 재audit 결과 `_extract_test_code()`(line 145→155)에서 여전히 호출되며, 그 상위로 `validate_test_evidence()`(line 464→511)까지 활성 call graph가 이어짐. 즉시 삭제는 회귀 위험이 있어 별도 정리 IMP 파이프라인에서 전체 call graph 분석 후 결정 필요. **현 상태: 활성 사용처 존재 → 삭제 보류.** |
| `pipeline.py` | `_extract_test_code` / `validate_test_evidence` 삭제 | line 145 / 464 | `cmd_harness`가 CLI에서 차단되어 더 이상 상태를 변경하지 않으나, 이 두 헬퍼는 legacy 진단/unit test 경로에서 호출 가능성이 남아 있음. dead code 여부 단정 불가 → 별도 IMP에서 결정. |
| `.claude/agents/pm-planner-agent.md` | 파일 생성 | `.claude/agents/` | `CLAUDE.md`/`AGENTS.md`는 `pm-planner-agent`를 참조하나 `.claude/agents/pm-planner-agent.md` 파일은 현재 **존재하지 않음**(ls 확인: `pm-agent.md`만 존재). 본 IMP는 "새 agent MD 파일 생성 금지" 제약이 있으므로 생성하지 않음. 참조 정합성 해소는 별도 Protocol Evolution IMP 필요. |
| `.claude/agents/pipeline-manager-agent.md` | 파일 생성 | `.claude/agents/` | 동일 사유. `pipeline-manager-agent` 참조는 존재하나 `.claude/agents/pipeline-manager-agent.md` 파일 부재. 본 IMP는 생성 금지 제약. 별도 IMP 필요. |
| `tests/oracles/**` | orphaned oracle 정리 | `tests/oracles/` | 현재 `tests/oracles/`에 40개 파이프라인별 디렉터리 존재(`BUG-*`, `FEAT-*`, `IMP-*`). 이들은 완료된 과거 파이프라인의 oracle answer key로, CODEOWNERS 보호 대상이며 회귀 capture로 재사용될 수 있음. 활성 파이프라인이 직접 참조하지 않더라도 임의 삭제는 oracle 무결성/회귀 추적을 훼손할 수 있음. 본 IMP 제약("tests/oracles/ 파일 삭제 금지")에 따라 삭제하지 않음. 정리가 필요하면 `pipeline.py hygiene` 경로 또는 별도 hygiene IMP에서 처리. **현 상태: orphaned 판정 단정 불가 → 보류.** |

---

## 재audit 요약 (3F91 DEFERRED 대비 변화)

| 3F91 DEFERRED 항목 | 본 파이프라인 재audit 결과 |
|---|---|
| `_parse_harness_report_et` 삭제 | 변화 없음 — `_extract_test_code`(line 155)에서 여전히 호출됨. 계속 DEFERRED. |
| `pm-planner-agent.md` 생성 | 변화 없음 — 파일 부재 유지. 생성 금지 제약으로 DEFERRED. |
| `pipeline-manager-agent.md` 생성 | 변화 없음 — 파일 부재 유지. 생성 금지 제약으로 DEFERRED. |
| `tests/oracles/` orphaned 정리 | 변화 없음 — 40개 디렉터리 보존. 삭제 금지 제약으로 DEFERRED. |

---

## 검증 결과

| AC | 설명 | 결과 |
|---|---|---|
| AC-1 | `pipeline.py` `_parse_protocol_evolution_decision`의 잘못된 type 오류 메시지에 actual/expected/복구 XML 포함 | PASS |
| AC-2 | 차단 조건(`recommended_type != "IMP"`) 및 `_child_text(..., "IMP")` 기본값 로직 불변 | PASS |
| AC-3 | `prompt-architect-agent.md`에 `recommended_pipeline_type=IMP` 규칙 추가 | PASS |
| AC-4 | `AGENTS.md` Phase 9 섹션에 `recommended_pipeline_type=IMP` 규칙 추가 | PASS |
| AC-5 | 회귀 테스트가 잘못된 type(`T`, `FEAT`)에서 actual/expected/복구 XML 검증 | PASS (3 passed) |
| AC-6 | 회귀 테스트가 exit!=0 + traceback 부재 검증 | PASS |
| AC-7 | 본 파일 존재 + REMOVED/MODIFIED/PRESERVED/DEFERRED 4개 섹션 포함 | PASS |
| AC-8 | 변경 파일이 5개 PM target_files 범위 내 (`pipeline.py`, `.claude/agents/prompt-architect-agent.md`, `AGENTS.md`, `tests/e2e/test_architect_evo_type_508f.py`, `cleanup_followup_report.md`) | PASS |
| AC-9 | 기존 harness 테스트 무손상 (`test_harness_block_3f91.py` 2 passed) | PASS |
| AC-10 | 정상 type(`IMP`)은 type 차단 없이 진행 | PASS |
