# Codex Model Router 보고서 (IMP-20260712-DAE1)

## 1. 개요

Codex Review 모델/사고레벨 자동 라우터입니다. `gates codex-review` 실행 시 이번 PR에서 변경된 파일과 함수를 trust-chain risk로 분류하고, risk level에 맞는 Codex 모델·사고레벨·검토 모드·캐시 정책을 SSoT 상수(`CODEX_MODEL_POLICIES`)에서 자동 선택합니다. 전역 on/off 스위치 없이 변경 대상의 위험도에 따라 계층형으로 동작합니다.

핵심 설계 결정:
- **DQ-1**: LOW/MEDIUM은 observe 모드 기본, HIGH/CRITICAL은 enforce 모드 기본 (계층형, 전역 전환 없음).
- **DQ-2**: HIGH/CRITICAL은 최소 `invocation_verified` 이상이어야 통과 (fail-closed).
- **DQ-3 (REJECT#3)**: Codex Review는 GPT-5.6 계열 모델을 사용하며(claude-* 아님), cache miss 시 실제 `codex exec`를 자동 실행합니다. 수동 verdict/CLI 주입은 운영 승인 자격이 없습니다(acceptance_eligible=false).

라우터 버전: `CODEX_MODEL_ROUTER_VERSION = "2.0.0"`.

## REJECT#3 재구현 요약 (GPT-5.6 라우터)

### 모델 정책 SSoT (`CODEX_MODEL_POLICIES`)

| Risk | selected_model | selected_effort | mode | cache_allowed | force_review |
|---|---|---|---|---|---|
| LOW | gpt-5.6-luna | low | observe | true | false |
| MEDIUM | gpt-5.6-terra | high | observe | true | false |
| HIGH | gpt-5.6-sol | high | enforce | limited | false |
| CRITICAL | gpt-5.6-sol | max | enforce | false | true |

### 실제 실행 (요구2/3)

```
codex exec --model <selected_model> -c model_reasoning_effort=<selected_effort> \
  --sandbox read-only --ephemeral --json -C <repo-root> -
```

- 실행 전 `codex login status`로 ChatGPT Plus 인증 강제 (정확히 `Logged in using ChatGPT`).
- Codex subprocess 환경에서 `OPENAI_API_KEY` 제거.
- timeout=600초. review bundle을 stdin으로 전달.

### 모델 증거 필드 분리 (요구4)

- `selected_model/effort`: 정책 선택값.
- `invoked_model/effort`: `--model`에 실제 전달한 값(= selected).
- `actual_model/effort`: CLI가 `--json`으로 명시 보고한 경우만(아니면 `unknown`).
- `model_verification_level`: `actual_verified` | `invocation_verified` | `unverified`.
  - HIGH/CRITICAL 최소: `invocation_verified`.

### verdict 스키마 (요구6)

- 승인: `{"verdict": "APPROVE_TO_USER"}`
- 거절: `{"verdict": "REJECT", "root_cause": "...", "reproduction": "...", "required_fix": "...", "acceptance_criteria": ["..."]}`
- REJECT 4필드 중 하나라도 누락 → parse_failure ERROR.

### 승인 자격/테스트 seam (요구5)

- `--verdict` / `--codex-cli-*` (external injection) → 어떤 환경에서도 `acceptance_eligible=false`.
- 테스트 주입 seam은 fake executable(`CODEX_REVIEW_FAKE_BIN`) + `PIPELINE_STATE_PATH` 격리 + `environment=test` 기록 + `acceptance_eligible=false` 강제. `PIPELINE_TEST_MODE` 우회 제거.

### cache 정책 (요구11)

- CRITICAL은 cache 절대 금지. environment=test / external / non-eligible 결과도 cache 저장 금지.
- cache key는 contract+bundle+router_version+risk+selected_model+selected_effort+model_policy_signature 조합.

## 2. Risk 분류 규칙

우선순위: **CRITICAL > HIGH > MEDIUM > LOW**. 위쪽 규칙에 먼저 매칭되면 그 risk level로 확정됩니다.

### CRITICAL — acceptance/SHA/nonce writer 함수 (`CODEX_CRITICAL_FUNCTIONS`)

| 함수명 |
|---|
| `_cmd_gates_request_accept` |
| `_cmd_gates_accept` |
| `_publish_acceptance_request` |
| `_write_acceptance_request` |
| `_consume_acceptance_request` |
| `_validate_pr_body_readiness` |
| `_build_acceptance_request` |
| `_finalize_acceptance` |

이 함수 중 하나라도 변경되면 CRITICAL로 분류됩니다. 승인 코드/nonce/SHA를 다루는 신뢰 루트 코드이기 때문입니다.

### HIGH — trust-chain 파일 경로 (`CODEX_HIGH_RISK_PATHS`)

| 경로 패턴 |
|---|
| `pipeline.py` |
| `.github/workflows/` |
| `CLAUDE.md` |
| `.claude/agents/` |
| `tests/` |

변경 파일 경로가 위 패턴과 정확히 일치하거나 접두사로 시작하면 HIGH로 분류됩니다 (CRITICAL 함수 변경이 없을 때).

### MEDIUM — 일반 코드 파일

확장자가 `.py`, `.ts`, `.js`, `.yaml`, `.yml`, `.sh`, `.ps1`, `.json` 중 하나인 파일만 변경된 경우.

### LOW — 문서/보고서

위 어느 규칙에도 해당하지 않는 경우 (예: `docs/`, `*.md` 보고서).

## 3. 모델 라우팅 표

| Risk Level | selected_model | reasoning_effort | mode | cache_allowed | force_review_required | downgrade_blocked |
|---|---|---|---|---|---|---|
| LOW | claude-sonnet | low | observe | true | false | false |
| MEDIUM | claude-sonnet | medium | observe | true | false | false |
| HIGH | claude-sonnet | high | enforce | limited | false | true |
| CRITICAL | claude-opus | high | enforce | false | true | true |

## 4. 계층형 observe/enforce 동작

- **observe 모드** (LOW/MEDIUM): 정책 위반 시 WARN만 출력하고 진행합니다. 캐시를 허용하여 사용량을 절약합니다. 전역 전환 스위치가 아니라 risk level에서 자동 파생됩니다.
- **enforce 모드** (HIGH/CRITICAL): 정책 위반 시 BLOCKED 처리합니다.
  - HIGH: critical 파일 변경이 없으면 `limited` 캐시를 허용합니다.
  - CRITICAL: 캐시를 항상 금지합니다 (`cache_allowed=False`). 매번 새 검토를 강제합니다.

## 5. Plus 사용량 보호 전략

라우터는 위험도에 비례해 모델 사용량을 배분하여 Plus 한도를 보호합니다.

- **CRITICAL**: 항상 `claude-opus` + `force_review` + 캐시 금지 → 검토 품질 최대화. 승인/nonce 코드 변경은 반드시 최고 사양으로 재검토.
- **HIGH**: `claude-sonnet` + enforce + limited cache → 신중하게 사용하되 반복 실행 시 제한적 캐시로 낭비 방지.
- **LOW/MEDIUM**: `claude-sonnet` + observe + 캐시 허용 → 문서/일반 코드는 사용량 절약.

## 6. actual_model=unknown 처리 (fail-closed)

`_detect_codex_cli_capability()`는 `codex --version`으로 CLI 존재를 확인하지만, 모델명을 결정적으로 확인할 수 없으면 `actual_model=unknown`으로 남깁니다 (확인하지 않은 모델명을 허위로 기록하지 않음).

| actual_model | risk_level | 결과 |
|---|---|---|
| unknown | HIGH | **BLOCKED** (`unknown_model_critical_blocked`) |
| unknown | CRITICAL | **BLOCKED** (`unknown_model_critical_blocked`) |
| unknown | LOW/MEDIUM | WARN 후 계속 진행 |
| known (예: claude-sonnet) | any | OK |

또한 `_check_codex_cache`는 actual_model=unknown + HIGH/CRITICAL 조건에서 캐시 사용도 금지합니다 (fail-closed).

## 7. 다운그레이드 차단

HIGH/CRITICAL risk에서는 `downgrade_blocked=True`이므로, 더 낮은 모델 티어로 다운그레이드 요청 시 항상 BLOCKED 됩니다.

- 트리거: `downgrade_requested=True`, 또는 `requested_model_tier`가 현재 risk보다 낮은 티어일 때.
- 반환: `{"result": "BLOCKED", "downgrade_blocked": True, "failure_code": "downgrade_blocked"}`.

이는 trust-chain 변경에 대한 검토 품질을 강제로 보장합니다.

## 8. 구현 위치 (SSoT)

| 항목 | 위치 |
|---|---|
| SSoT 상수 | `pipeline.py` — `CODEX_MODEL_ROUTER_VERSION`, `CODEX_CRITICAL_FUNCTIONS`, `CODEX_HIGH_RISK_PATHS`, `CODEX_MODEL_POLICIES` |
| risk 분류 | `pipeline.py::_classify_codex_review_risk` |
| 모델 정책 | `pipeline.py::_build_codex_model_policy` |
| capability 감지/게이트 | `pipeline.py::_detect_codex_cli_capability`, `_check_codex_capability_gate` |
| 캐시 정책 통합 | `pipeline.py::_check_codex_cache` (model_policy/actual_model/risk_level 매개변수) |
| 실행 통합 | `pipeline.py::_cmd_gates_codex_review` |
| bundle 정책 섹션 | `pipeline.py::_build_codex_review_bundle` (model_policy + raw ACCEPT/nonce 금지 가드) |
| E2E 테스트 | `tests/e2e/test_codex_model_router_dae1.py` (TC-1~TC-18) |
