# Codex Review 비용 최적화 리포트 (IMP-20260710-DB54)

## 요약

Codex CLI 리뷰는 호출당 비용이 발생합니다. 이 파이프라인은 **비싼 Codex CLI 호출 전에**
결정적(deterministic) 검사로 실패를 조기에 차단하고, 리뷰에 보내는 payload를 최소화하며,
동일한 검토 대상에 대한 반복 호출을 안전한 캐시로 건너뛰어 리뷰 비용을 줄입니다.

핵심 3축:

1. **Deterministic Preflight** — gate 상태/계약 동결/bundle 내용 안전성을 코드로 먼저 검증.
2. **Minimal Review Bundle** — 대형 diff 원문과 oracle 원문을 제외하고 개수/SHA만 전송.
3. **Safe Cache** — contract+bundle SHA 조합으로 이전 verdict를 재사용, critical file 변경 시 무효화.

기존 F52C/B985/E69E의 SHA·provenance·stale·snapshot 검증 구조와 User Acceptance 승인 형식,
acceptance snapshot 구조는 **변경하지 않았습니다**. OpenAI advisory와 Codex CLI review도 혼합하지
않았습니다. 모든 변경은 기존 흐름 앞단에 추가되는 하위 호환 방식입니다.

## MT별 변경 내역

| MT | 대상 | 내용 |
|---|---|---|
| MT-1 | `pipeline.py :: _run_codex_preflight_checks` | Codex CLI 호출 전 10개 결정적 preflight 검사 (fail-closed) |
| MT-2 | `pipeline.py :: _cmd_gates_codex_preflight` + argparse/dispatcher | `python pipeline.py gates codex-preflight [--dry-run]` 명령 추가 |
| MT-3 | `pipeline.py :: _build_codex_review_bundle` | `review_bundle_sha256`/`changed_files_count`/`oracle_count` 필드 추가, 대형 diff·oracle 원문 제외 |
| MT-4 | `pipeline.py :: _codex_cache_key`, `_codex_review_cache_path`, `_check_codex_cache` | 결정적 캐시 키 + 격리 경로 + critical-file 무효화 캐시 조회 |
| MT-5 | `pipeline.py :: _cmd_gates_codex_review` | 앞단에 preflight(내용 안전성 hard-block) + cache 조회 + verdict 기록 후 cache write 추가 |
| MT-6 | `tests/test_codex_preflight_db54.py` | preflight 회귀 테스트 16개 |
| MT-7 | `tests/test_codex_bundle_db54.py` | bundle 최소화 검증 테스트 9개 |
| MT-8 | `tests/test_codex_cache_db54.py` | 캐시 hit/miss/무효화 테스트 10개 |
| MT-9 | `tests/test_codex_rate_limit_db54.py` | ERROR/REJECT 분리 + rate-limit 테스트 17개 |
| MT-10 | `codex_review_optimization_report.md` | 본 리포트 |

## 1. Deterministic Preflight (MT-1, MT-2)

`_run_codex_preflight_checks(bundle, state, pipeline_id)`는 아래 10개 검사를 순서대로 수행하고
`{result, preflight_checks_passed, preflight_checks_failed, blocked, failure_codes}`를 반환합니다.

| # | 검사 | 실패 코드 |
|---|---|---|
| 1 | technical gate PASS | `technical_gate_not_pass` |
| 2 | oracle gate PASS | `oracle_gate_not_pass` |
| 3 | github_ci gate PASS | `github_ci_not_pass` |
| 4 | contract frozen | `contract_not_frozen` |
| 5 | bundle에 raw ACCEPT 코드 없음 | `bundle_contains_raw_accept_code` |
| 6 | reject_count < 3 | `reject_count_below_limit` |
| 7 | nonce 노출 없음 (8자 base32) | `bundle_contains_nonce` |
| 8 | 제거된 파일 참조 없음 | `bundle_references_deleted_file` |
| 9 | NOT_CONFIGURED fallback 없음 | `no_not_configured_fallback` |
| 10 | pending comment 우회 경로 없음 | `no_pending_comment_bypass` |

- 모든 검사는 try/except로 감싸 예외 시 실패(fail-closed)로 집계합니다.
- nonce 검사는 base32(A-Z2-7) + 알파벳 포함 조건으로, `pipeline_id` 날짜(0/1 포함)나
  소문자 SHA hex가 nonce로 오탐되지 않습니다.
- `gates codex-preflight`는 전체 10개 검사가 하나라도 실패하면 BLOCKED로 die합니다
  (`--dry-run`은 진단 출력만).

### oracle 대조

- `TC-preflight-normal`: 10/10 PASS, `failure_codes=[]` — 일치.
- `TC-preflight-edge`: raw ACCEPT 코드 → 9 pass / 1 fail, `["bundle_contains_raw_accept_code"]` — 일치.

## 2. Minimal Review Bundle (MT-3)

`_build_codex_review_bundle`은 이제 다음을 만족합니다.

- 추가 필드: `review_bundle_sha256`(자기참조 placeholder), `changed_files_count`, `oracle_count`.
- 대형 diff 원문(@@ hunk)과 oracle 원문(expected.json 내용)은 bundle에 포함하지 않고,
  개수/SHA만 담아 Codex CLI payload 크기를 줄입니다.
- **E69E 불변식 보존**: bundle의 신뢰 루트 SHA는 여전히 `_sha256_file(bundle_path)`이며,
  `_codex_snapshot_identity`도 동일 함수로 재계산합니다. embedded `review_bundle_sha256`은
  빈 문자열로 고정하여 파일 SHA 결정성을 유지하고, 파일을 SHA로 재기록하지 않습니다.

### oracle 대조

- `TC-bundle-normal`: 필수 필드 5종 존재, `full_diff_excluded=true`, `oracle_raw_excluded=true` — 일치.

## 3. Safe Cache (MT-4, MT-5)

- `_codex_cache_key(contract_sha256, review_bundle_sha256)` — `sha256(a+":"+b)[:16]` 결정적 키.
- `_codex_review_cache_path()` — `PIPELINE_STATE_PATH` 격리를 지원하는 캐시 파일 경로.
- `_check_codex_cache(...)` — 키/SHA 일치 + critical file SHA 재검증. critical file(pipeline.py 등)이
  바뀌면 캐시를 무효화(`hit=False`)하여 stale verdict 재사용을 막습니다(safe cache).
- `_cmd_gates_codex_review`는 verdict 기록 후 critical file SHA와 함께 캐시를 기록합니다.

### oracle 대조

- `TC-cache-hit`: 동일 contract+bundle SHA → `hit=True`, `cached_verdict="APPROVE"`, `cache_key` 일치 — 일치.

## 4. ERROR/REJECT 분리 + Rate-limit (MT-9 검증)

기존 `_run_codex_cli_review` / `_check_codex_rate_limit`의 동작을 회귀 테스트로 고정합니다.

- CLI 실행 실패(non-zero exit, timeout, parse 실패)는 `ERROR`이며 `reject_count`를 증가시키지 않습니다.
- 명시적 `REJECT - <사유>`만 `REJECTED`로 집계됩니다.
- rate-limit은 `reject_count`에만 기반(기본 임계값 2)하며 `cli_error_count`는 트리거하지 않습니다.

### oracle 대조

- `TC-error-split`: CLI ERROR → `reject_count` 불변 — 일치.
- `TC-rate-limit-normal`: rate-limit은 reject_count에만 적용, CLI ERROR는 reject로 집계 안 됨 — 일치.

## 안전성/하위 호환

- MT-5의 codex-review 흐름은 **내용 안전성 실패**(승인코드/nonce/NOT_CONFIGURED/삭제파일 참조)에만
  hard-block하고, gate 상태/계약 동결/reject_count는 경고만 출력합니다. 이로써 정상 bundle에서는
  절대 차단되지 않으며 기존 codex-review E2E 흐름을 보존합니다.
- 전체 10개 검사의 hard gate는 별도 진단 명령 `gates codex-preflight`에서 제공합니다.
- 변경 파일: `pipeline.py` 1개(제품 코드) + 신규 테스트 4개 + 본 리포트. Trust-Root 위반 없음.

## 테스트 결과

| 테스트 파일 | 테스트 수 | 결과 |
|---|---|---|
| `tests/test_codex_preflight_db54.py` | 16 | PASS |
| `tests/test_codex_bundle_db54.py` | 9 | PASS |
| `tests/test_codex_cache_db54.py` | 10 | PASS |
| `tests/test_codex_rate_limit_db54.py` | 17 | PASS |
| 합계 | 52 | 52 passed |

기존 `tests/e2e/test_codex_cost_optimization_9f5e.py`(25개)도 전부 PASS로 유지됩니다.
