# 기술 선택 의사결정 트리 (pm-agent 참조 전용)

```
요구사항 수신
│
├─ [Python 단독 시그널 존재?]
│   - 웹 스크래핑 / 동적 크롤링
│   - 복잡한 데이터 변환 · 집계 (pandas, numpy 등)
│   - 비Microsoft 외부 API 연동
│   - 파일 고급 처리 (PDF, Excel 파싱/생성)
│   - ML / 통계 / 수치 연산
│   - 로컬 파일시스템 직접 접근
│   │
│   └─ YES → [Microsoft 서비스 연동 또는 PA 시그널도 존재?]
│               │
│               ├─ YES → [HYBRID] dev-agent + power-automate-agent 모두 사용
│               │         (복잡 로직은 Python, 연동/알림/저장은 PA 담당. 각 작업은 PM micro-task 순서대로 실행)
│               │
│               └─ NO  → [PYTHON 단독] dev-agent만 spawn
│
└─ [Python 단독 시그널 없음]
    │
    └─ [Power Automate 단독 시그널 존재?]
        - Microsoft 365 서비스 간 단순 연동
          (Outlook ↔ Teams ↔ SharePoint)
        - 승인 워크플로우 (Approval 커넥터)
        - 단순 트리거-액션 플로우
        - 비개발자 유지보수 대상 플로우
        - 실시간 이벤트 기반 자동화 (HTTP 트리거 등)
        │
        ├─ YES → [PA 단독] power-automate-agent만 spawn
        │         EXE 빌드 불필요 → `--exe "N/A" --skip-reason "power-automate"`
        │         외부 HTTP 커넥터 포함 시 SEC 필수
        │
        └─ NO  → 시그널 불명확 → Clarification 요청
                  (Output Format 1: clarification_request 발행)
```

### 케이스별 spawn 요약

| 판정 결과 | spawn 에이전트 | category_tags | EXE 빌드 |
|---|---|---|---|
| Python 단독 | `dev-agent` | WA / FS / AL / PD (해당 태그) | 해당 시 Phase 6 진행 |
| PA 단독 | `power-automate-agent` | PA | `--exe "N/A" --skip-reason "power-automate"` |
| 하이브리드 | `dev-agent` + `power-automate-agent` | PA + (WA/FS/AL 등 복합) | Python 산출물만 Phase 6 대상 |

### 하이브리드 판정 우선 규칙

- Python 단독 시그널과 PA 단독 시그널이 **동시에 존재**하면 하이브리드로 확정. 추가 Clarification 금지.
- Microsoft 서비스가 포함되더라도 복잡한 로직(집계/변환/ML 등)이 **하나라도** 포함되면 하이브리드 우선.
- 하이브리드 시 dev-agent는 Python 로직 산출물을 `<handover>`로 QA에 인계하고, power-automate-agent는 플로우 JSON을 별도 산출물로 제출한다. 둘은 병렬 실행하지 않고 PM이 정한 `MT-N` 순서를 따른다.

### 시그널 불명확(Ambiguous) 처리

요구사항에서 어느 케이스에도 해당하는 명확한 시그널이 없을 경우:
1. Output Format 1(`<clarification_request>`)을 발행하여 사용자에게 기술 방향 선택 요청.
2. 선택지에는 반드시 "Python 단독 / PA 단독 / 하이브리드" 3가지 옵션을 포함.
3. 사용자 확인 전 step_plan 발행 금지.

<!-- pm-agent.md에서 분리됨 — IMP-20260418-6AE3 -->
