# IMP-20260711-86DD 출력 권한 Inventory — 분류 보고서

이 문서는 사용자 승인 요청(approval request) 출력 경로의 SSoT(단일 진실 원천)를 정리하고, 이중 출력을 유발하는 dead code를 식별·제거하기 위한 인벤토리 분류 보고서입니다.

## 1. 현황

승인 요청 출력에 관여하는 함수·필드 목록입니다.

| 이름 | 종류 | 역할 |
|---|---|---|
| `_build_approval_request_output()` | 함수 | **canonical source** — 사용자 승인 요청 메시지를 생성하는 단일 진실 원천 |
| `approval_request_message` | 필드 | JSON machine-readable 모드에서 반환되는 승인 요청 메시지 필드. Pipeline Manager가 추출하여 사용자에게 중계 |
| `approval_display` | 필드 | **DEPRECATED 예정** — 구버전 하위 호환용 필드. 신규 코드는 `approval_request_message`를 사용 |
| `_cmd_gates_request_accept()` | 함수 | CLI 진입점 — `gates request-accept` 명령을 처리하며 `--machine-readable` 분기를 포함 |

## 2. 문제

이중 출력(같은 승인 메시지가 두 번 나오는 현상)이 발생하는 원인입니다.

- **machine-readable 이중 출력 경로:** `--machine-readable` 모드에서 JSON을 stdout으로 출력하는 동시에, JSON 외부로도 approval 블록(사용자 승인 요청 텍스트)을 별도로 출력하는 경로가 존재합니다. 이 때문에 Pipeline Manager가 JSON을 파싱하려 할 때 stdout에 JSON이 아닌 텍스트가 섞여 파싱이 깨지거나, 승인 메시지가 두 번 노출됩니다.
- **relay 섹션 복수 존재:** `pipeline-manager-agent.md`에 승인 요청 중계(relay) 규칙을 담은 섹션이 여러 개 존재하여, Pipeline Manager가 어느 규칙을 따라야 하는지 혼란을 유발합니다.

## 3. 이번 정리 대상

- **`pipeline.py`:** `approval_display` 필드에 DEPRECATED 주석 추가, machine-readable 모드의 이중 출력 경로 제거 (`approval_request_message` 필드는 JSON 내부에 유지).
- **`pipeline-manager-agent.md`:** relay 관련 섹션을 단일 섹션 `## 사용자 승인 요청 중계 규칙 (Output Authority SSoT)`으로 통합.
- **`tests/test_codex_review_hook_cd3c.py`:** 삭제 (dead code).
- **`tests/test_codex_review_loop_4121.py`:** 삭제 (dead code).
- **`tests/e2e/test_approval_authority_86dd.py`:** 신규 작성 (TC-1~TC-7).

## 4. 완료 후 기대 상태

- `gates request-accept --machine-readable`의 stdout이 **JSON 전용**이 되어 Pipeline Manager가 안정적으로 파싱 가능.
- `pipeline-manager-agent.md`의 relay 섹션이 **정확히 1개**로 단일화됨.
- 위 테스트 파일 2개가 **삭제됨**.
- 신규 E2E 테스트의 **TC-1~TC-7 전부 PASS**.
