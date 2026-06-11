# Legacy Cleanup Execution Report — IMP-20260611-3F91

**파이프라인:** IMP-20260611-3F91
**날짜:** 2026-06-11
**목적:** harness CLI 차단 명확화 + AGENTS.md 경로 정정 + 레거시 정리 결과 기록

---

## REMOVED

| 항목 | 파일 | 설명 |
|---|---|---|
| harness --score required=True | pipeline.py | ,  인자의 를 로 변경하여 argparse exit 2 제거. 빈 harness 호출도 THREE GATE BLOCKED + exit 1 반환. |

---

## MODIFIED

| 항목 | 파일 | 변경 내용 |
|---|---|---|
| harness 서브파서 인자 | pipeline.py |  → .  → . help 문자열에 legacy/blocked 명시. |
| 에이전트 경로 참조 | AGENTS.md |  →  경로 20건 전체 교체. 에이전트 테이블(lines 62-74), 금지 목록, cleanup 섹션, 보호 파일 목록 모두 포함. |

---

## PRESERVED

| 항목 | 파일 | 이유 |
|---|---|---|
|  함수 | pipeline.py | 에서 호출됨 확인. 레거시 테스트 증거 검증 체인 일부로 안전하게 삭제 불가. |
|  함수 | pipeline.py |  호출 체인 하위에 위치. 사용처 존재 확인됨. |
| 기존 E2E 테스트 | tests/e2e/ | 기존 테스트 파일 그대로 보존. MT-2에서 신규 test_harness_block_3f91.py만 추가. |
| tests/test_legacy_cleanup_b0ab.py | tests/ | test_harness_score_blocked() 계속 PASS 확인 필요. |

---

## DEFERRED

| 항목 | 파일 | 이유 |
|---|---|---|
|  삭제 | pipeline.py | 에서 호출되므로 즉시 삭제 불가. 별도 파이프라인에서 call graph 전체 분석 후 결정 필요. |
| pm-planner-agent.md 생성 | .claude/agents/ | 해당 파일이 현재 존재하지 않음. 경로 참조만 수정. 파일 생성은 별도 IMP 파이프라인에서 진행. |
| pipeline-manager-agent.md 생성 | .claude/agents/ | 동일 사유. 파일 생성은 별도 IMP 파이프라인에서 진행. |
| tests/oracles/ orphaned 파일 정리 | tests/oracles/ | 현재 파이프라인 scope 외. 별도 hygiene 파이프라인에서 처리. |

---

## 검증 결과

- AC-1:  → THREE GATE BLOCKED + exit 1 (argparse 오류 없음): PASS
- AC-2:  → THREE GATE BLOCKED + exit 1: PASS
- AC-3: cmd_harness 항상 _die([THREE GATE BLOCKED]) 반환: PASS
- AC-4: _parse_harness_report_et call graph 분석: DEFERRED (삭제 위험)
- AC-5: AGENTS.md .Codex/agents → .claude/agents 20건: PASS
- AC-6: pm-planner-agent.md, pipeline-manager-agent.md 생성 없음: PASS (DEFERRED)
- AC-7: 본 파일 존재 + 4개 섹션 포함: PASS
- AC-8: 변경 파일 4개 범위 내 (pipeline.py, tests/e2e/test_harness_block_3f91.py, AGENTS.md, legacy_cleanup_execution_report.md): PASS
