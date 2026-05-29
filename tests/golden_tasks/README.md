# Golden Task Regression Suite

이 디렉터리는 파이프라인 시스템의 핵심 동작을 검증하는 **Golden Task** 파일을 포함합니다.
파이프라인 코드 변경 시 이 golden task들을 실행하여 회귀(regression)가 없음을 확인합니다.

## Golden Task 구조

각 golden task는 다음 구조를 가집니다:

```
GT-XXX-<설명>/
  golden_task.json          # 태스크 정의 (id, command, smoke, allowed_files, forbidden_files 등)
  input/                    # 태스크 실행에 필요한 입력 데이터
  expected/                 # 기대 결과 (stdout_contains.json, result.json, audit_result.json 등)
```

## Golden Task 스키마

`golden_task.json`의 필수 필드:

| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | string | 고유 식별자 (디렉터리명과 일치) |
| `description` | string | 태스크 설명 |
| `command` | string | 실행할 CLI 명령어 |
| `smoke` | boolean | CI smoke 테스트 포함 여부 |
| `allowed_files` | array | PR에 포함 허용된 파일 패턴 |
| `forbidden_files` | array | PR에 포함 금지된 파일 패턴 (내부 아티팩트) |
| `acceptance_criteria` | array | 합격 기준 목록 |
| `return_phase` | string | 실패 시 돌아갈 phase |

## 등록된 Golden Tasks

| ID | 설명 | Smoke |
|----|------|-------|
| `GT-001-status-complete-exit0` | `pipeline.py status` exit code 0 검증 | true |
| `GT-002-internal-artifact-blocked` | forbidden 파일(내부 아티팩트) 차단 검증 | true |
| `GT-003-oracle-quality-normal-edge` | oracle quality gate PASS 검증 (normal + edge) | true |

## CLI 사용법

```bash
# 모든 golden task 목록 조회
python pipeline.py golden list

# 특정 태스크 실행
python pipeline.py golden run --task GT-001-status-complete-exit0

# 모든 태스크 실행
python pipeline.py golden run --all

# smoke=true 태스크만 실행 (CI 용)
python pipeline.py golden run --smoke
```

## CI 통합

`.github/workflows/ci.yml`의 **Golden Task Smoke** 단계에서 `smoke=true`인 태스크를 자동 실행합니다.
하나라도 실패하면 CI가 FAIL됩니다.

## 실패 시 대응

golden task 실패 시 `golden_failure_packet.json`이 생성됩니다.
`return_phase` 필드에 명시된 phase부터 수정 작업을 시작합니다.

## 새 Golden Task 추가 방법

1. `tests/golden_tasks/GT-XXX-<설명>/` 디렉터리 생성
2. `golden_task.json` 작성 (스키마 준수)
3. `input/` 하위에 입력 데이터 파일 작성
4. `expected/` 하위에 기대 결과 파일 작성
5. `python pipeline.py golden list`로 인식 확인
6. `python pipeline.py golden run --task GT-XXX-<설명>`으로 동작 검증
