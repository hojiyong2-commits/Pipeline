# User Acceptance Nonce Gate — IMP-20260531-BBDB

## 변경 요약

User Acceptance gate(`pipeline.py gates accept`)에 **일회용 승인 코드(nonce) 검증**을 추가하여,
에이전트가 세션 재개 요약이나 컨텍스트만으로 사용자 승인을 임의로 처리하는 것을 방지합니다.

## 근본 원인 (RCA)

기존에는 `--user-confirmed` 플래그만 있으면 `gates accept`가 통과했습니다.
이 플래그는 에이전트가 임의로 붙일 수 있어 실제 사용자가 승인했는지 증명하지 못했고,
세션 재개 시 컨텍스트 요약의 "다음 단계: gates accept 실행" 문구만으로 에이전트가
자동 실행하는 사고가 반복 발생했습니다.

해결: 사용자가 결과물을 직접 확인하고 입력해야 하는 **일회용 nonce 기반 인증 코드**를 도입.

## 새 흐름

### 1단계: 승인 코드 발급

```powershell
python pipeline.py gates request-accept --evidence <결과물-경로>
```

→ `acceptance_request.json` 생성 + 사용자에게 일회용 코드 표시:

```
================================================================
  사용자 최종 확인 요청
================================================================
  PR: https://github.com/hojiyong2-commits/Pipeline/pull/999
  GitHub Actions: https://github.com/.../actions/runs/12345
  결과물: dist/MyApp.exe

  위 결과물을 확인하신 후 아래 코드를 입력해 주세요.

  [O] 승인하시려면 정확히 아래 코드를 입력하세요:
     ACCEPT-IMP-20260531-BBDB-A2B3C4D5

  [X] 거절하시려면 아래 형식으로 입력하세요:
     REJECT-IMP-20260531-BBDB-A2B3C4D5: 거절 이유
================================================================
```

### 2단계: 사용자가 결과물 확인 후 코드 입력

```
ACCEPT-IMP-20260531-BBDB-A2B3C4D5
```

### 3단계: gates accept 실행 (Pipeline Manager)

```powershell
python pipeline.py gates accept --result ACCEPT --evidence <경로> \
    --acceptance-code ACCEPT-IMP-20260531-BBDB-A2B3C4D5
```

## 검증 조건 (11단계 hard gate)

`gates accept` 실행 시 아래 11단계가 순차적으로 검증됩니다:

| 순번 | 조건 | failure_code |
|---|---|---|
| 1 | `--user-confirmed` 단독 | `acceptance_code_required` |
| 2 | `--acceptance-code` 누락 | `acceptance_code_required` |
| 3 | `acceptance_request.json` 없음 | `missing_acceptance_request` |
| 4 | `status` != `PENDING` | `consumed_or_expired` |
| 5 | `pipeline_id` 불일치 | `pipeline_id_mismatch` |
| 6 | 코드 형식 오류 | `acceptance_code_mismatch` |
| 7 | nonce 불일치 | `acceptance_code_mismatch` |
| 8 | PR head SHA 변경 | `stale_head_sha` |
| 9 | CI run ID 변경 | `stale_run_id` |
| 10 | evidence 파일 변경 | `evidence_changed` |
| 11 | 모두 통과 | CONSUMED 처리 후 기존 accept 흐름 진행 |

각 실패는 `failure_packet.json`을 생성하며 `return_phase: build`로 기록됩니다.

## 보안 강도

- nonce: 8자 base32 uppercase (`A-Z2-7`) → 40bit 엔트로피.
- 단일 PR 라이프사이클 동안 충돌 가능성은 무시 가능 (40비트 = 약 1.1조 조합).
- `secrets.token_bytes(5)` 기반 암호학적 안전성 보장.

## 하위 호환성

- 기존 `--user-confirmed` 플래그는 보존되지만 더 이상 ACCEPT를 통과시키지 않습니다.
- 경고 메시지 출력 후 BLOCKED 처리됩니다.
- 모든 호출 예제(`gates accept --user-confirmed`)는 `request-accept` + `--acceptance-code` 흐름으로 교체됩니다.

## 변경 파일

- `pipeline.py`:
  - MT-1: nonce I/O 헬퍼 (5개 함수 + 3개 상수 + 2개 import)
  - MT-2: `gates request-accept` 서브커맨드 + 핸들러
  - MT-3: `gates accept` 11단계 nonce 검증
  - MT-4: GitHub PR/CI 조회 + 댓글 갱신 헬퍼 5종
- `CLAUDE.md`: "User Acceptance Nonce Gate (IMP-20260531-BBDB)" 섹션 추가
- `.claude/agents/pm-agent.md`: 세션 요약 오염 방지 규칙 추가
- `.claude/agents/test-harness-agent.md`: ACCEPT 코드 안내 4단계 절차 추가
- `tests/e2e/test_user_acceptance_nonce_bbdb.py`: E2E 14 케이스 (TC-1 ~ TC-14)
- `tests/oracles/IMP-20260531-BBDB/`: normal + edge 2종 oracle (PM 제공)
- `docs/RELEASE_NOTES_IMP-20260531-BBDB.md`: 본 문서

## 검증

- `python -m py_compile pipeline.py` 통과
- `python -c "import pipeline; pipeline._issue_acceptance_nonce()"` 정상 (8자 base32)
- `pytest tests/e2e/test_user_acceptance_nonce_bbdb.py -q` 14 케이스 통과
