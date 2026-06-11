# IMP-20260610-982B Dead Code Audit Report

**파이프라인:** IMP-20260610-982B  
**작성일:** 2026-06-11  
**작성자:** dev-agent (MT-1)  
**목적:** pipeline.py 및 agent MD의 구버전 잔재/dead code를 audit-first 방식으로 정리  
**External Gates 검증일:** 2026-06-11 (Technical PASS, Oracle PASS)

---

## 1. 제거 후보 (DELETE)

### 1-1. `_sync_acceptance_request_with_packet` (pipeline.py, line 15643–15692)

| 항목 | 내용 |
|---|---|
| 함수 위치 | `pipeline.py` line 15643–15692 |
| 호출 경로 | **Production 호출 0건** |
| SSoT 근거 | IMP-20260608-7FAC 도입 시 helper 목적으로 생성됐으나, IMP-20260610-8C3B에서 `_materialize_acceptance_snapshot`이 acceptance_request 동기화를 내부에서 직접 처리하도록 대체됨. line 15411의 언급은 docstring 주석 (실제 호출 아님) |
| 대체 경로 | `_materialize_acceptance_snapshot` (line 15403–) 가 동일 책임 수행 |
| 삭제 확인 | pipeline-manager-agent `delete_confirmed` 목록에 명시됨 |

### 1-2. `tests/test_acceptance_provenance_c4f8.py`

| 항목 | 내용 |
|---|---|
| 파일 위치 | `tests/test_acceptance_provenance_c4f8.py` |
| 테스트 대상 | `_sync_acceptance_request_with_packet` (dead function) |
| SSoT 근거 | dead function만 테스트하며 production 코드의 동작을 검증하지 않음 |
| 대체 경로 | 없음 (dead function이므로 테스트도 불필요) |

### 1-3. `tests/test_reuse_packet_sync_7fac.py`

| 항목 | 내용 |
|---|---|
| 파일 위치 | `tests/test_reuse_packet_sync_7fac.py` |
| 테스트 대상 | `_sync_acceptance_request_with_packet` (dead function) |
| SSoT 근거 | dead function만 테스트하며 production 코드의 동작을 검증하지 않음 |
| 대체 경로 | 없음 (dead function이므로 테스트도 불필요) |

---

## 2. 수정 후보 (MODIFY)

### 2-1. `.claude/agents/pm-agent.md` — 구버전 `done --phase pm` 명령 형식

| 항목 | 내용 |
|---|---|
| 파일 위치 | `.claude/agents/pm-agent.md` |
| 충돌 문구 | `pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --agent-run-id <pm_run_id>` (3곳) |
| SSoT 근거 | CLAUDE.md 현행 규칙: `done --phase pm`에는 `--planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml` 필수. 단일 `--agent-run-id` 방식은 구버전으로, PM Planner/Manager 분리 이전 시대 형식임 |
| 대체 경로 | CLAUDE.md의 현행 `done --phase pm` 명령 형식으로 교체 |
| 수정 내용 | `--agent-run-id <pm_run_id>` → `--planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml` |

---

## 3. 보존 필수 항목 (PRESERVE)

| 항목 | 위치 | 보존 이유 |
|---|---|---|
| `_build_ac_fulfillment_table` | pipeline.py (6 occurrences: 1 def + 5 calls) | AC 충족표 생성 — request-accept 흐름의 핵심 활성 함수. 호출 5건 존재 |
| `cmd_harness` 차단 블록 | pipeline.py (3 occurrences) | 구버전 harness 명령 차단 guard rail |
| `--user-confirmed` no-op 경고 블록 | pipeline.py (10 occurrences) | Nonce gate 이전 방식 명시적 차단 |
| `acceptance run --record` 차단 블록 | pipeline.py (1 occurrence) | legacy score path 차단 guard rail |
| `_extract_test_code` | pipeline.py (3 occurrences) | 테스트 코드 추출 진단용 helper — 활성 사용 중 |
| `validate_test_evidence` | pipeline.py (2 occurrences) | 테스트 증거 검증 함수 — 활성 사용 중 |
| `_parse_harness_report_et` | pipeline.py (3 occurrences) | harness 보고서 파싱 helper — 활성 사용 중 |

---

## 4. 감사 요약

| 구분 | 항목 수 | 세부 내용 |
|---|---|---|
| 제거 | 3 | pipeline.py 함수 1개, 테스트 파일 2개 |
| 수정 | 1 | pm-agent.md 구버전 명령 형식 3곳 |
| 보존 | 7 | guard rail 및 활성 함수 전체 유지 |

---

## 5. Scope Manifest 선언

```json
{
  "pipeline_id": "IMP-20260610-982B",
  "micro_tasks": [
    {
      "id": "MT-1",
      "files": ["cleanup_report.md"],
      "affected_functions": ["none — new file creation"]
    },
    {
      "id": "MT-2",
      "files": ["pipeline.py", "tests/test_acceptance_provenance_c4f8.py", "tests/test_reuse_packet_sync_7fac.py"],
      "affected_functions": ["_sync_acceptance_request_with_packet"]
    },
    {
      "id": "MT-3",
      "files": [".claude/agents/pm-agent.md"],
      "affected_functions": ["none — documentation fix"]
    }
  ]
}
```
