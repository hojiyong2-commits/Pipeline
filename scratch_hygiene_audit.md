# Scratch Hygiene Audit Report — IMP-20260622-79BA

## 현황
- 분석 일시: 2026-06-22
- 대상: pipeline.py 내부 임시 파일 생성 지점 + repo root 임시 산출물 오염원

## Repo Root 임시 파일 생성 지점 목록 (Grep 기반)

pipeline.py 내부에서 파일을 쓰는 모든 지점(`.write_text`, `open(... "w")`, `json.dump`,
`Path(os.getcwd())/...`, `Path("...")`)을 전수 조사한 결과는 다음과 같다.

| 분류 | 생성 위치(라인 근사) | 대상 경로 | 판정 |
|---|---|---|---|
| Runtime state 원자적 쓰기 | `_save_state_to` (≈2775) | `.pipeline/runs/<id>/state.json` (tempfile.NamedTemporaryFile in `path.parent` → os.replace) | 격리됨 (repo root 아님) |
| Contract 원자적 쓰기 | (≈3452) | contract 디렉터리 내부 tempfile | 격리됨 |
| Evidence inventory 등 | (≈4789, 4820) | 각 산출물 디렉터리 내부 tempfile | 격리됨 |
| active_run pointer 쓰기 | (≈7575) | `.pipeline/active_run.json` tempfile | 격리됨 |
| human_acceptance_packet | `_packet_output_path` / `_packet_json_output_path` (≈12374, 12411) | `cwd/human_acceptance_packet.md(.json)` | 등록 산출물 (HYGIENE_ARCHIVE_PATTERNS) |
| golden failure packet | `cmd_golden_*` (≈22559) | `cwd/golden_failure_packet.json` | 등록 산출물 (HYGIENE_ARCHIVE_PATTERNS) |
| acceptance_result | `cmd_acceptance` (≈10280) | `paths["result"]` 또는 `--output` | CLI 명시 경로 존중 |

### 핵심 발견
- pipeline.py 프로덕션 코드는 **`tmp_tc*.json` / `oracle_result_dump.txt` 를 repo root에
  직접 쓰지 않는다.** Grep 결과 이 두 패턴은 모두 cleanup 패턴 목록
  (`_WORKSPACE_HYGIENE_EXTRA_CLEANUP_PATTERNS`, `_ROOT_CLEANUP_PATTERNS`)에만 등장한다.
- repo root에 남은 `tmp_tc1_given.json` / `tmp_tc1_when.json` / `tmp_tc1_then.json` /
  `build_report.xml` 등은 과거 QA/oracle 진단 dump 또는 에이전트 수동 산출물의 잔재이며,
  pipeline.py 내부 생산자가 흘린 것이 아니다.
- `.pytest_tmp_*` 디렉터리는 테스트 실행 중 생성되며, Permission denied로 정리되지 않는 잔재가 남는다.
- 모든 state/contract/inventory 쓰기는 이미 `tempfile.NamedTemporaryFile(dir=path.parent)` +
  `os.replace` 원자적 패턴으로 `.pipeline/` 또는 산출물 디렉터리 내부에 격리되어 있다.

## AC 매핑

| AC | 설명 | 구현 MT |
|---|---|---|
| AC-1 | scratch helper 6개 함수 (.pipeline/runs/<id>/scratch/) | MT-1 |
| AC-2 | 내부 생산자 audit + scratch 리다이렉트 | MT-2, MT-3 |
| AC-3 | workspace hygiene cleanup_only 분류 강화 | MT-5 |
| AC-4 | Permission denied .pytest_tmp_* 정리 fixture | MT-4 |
| AC-5 | hygiene cleanup CLI (dry-run 기본 + --apply) | MT-6 |
| AC-6 | tmp_*.json/*_dump.txt/build_report.xml cleanup_only 보장 | MT-5 |
| AC-7 | final packet hygiene 요약 섹션 | MT-7 |
| AC-8 | .pipeline/runs/** PR diff 포함 시 BLOCKING | MT-5 |
| AC-9 | pipeline_state.json protected 분류 (PR/packet 미포함) | MT-5 |
| AC-11 | 15개 E2E 테스트 + oracle | MT-8 |

## 분류 규칙 (cleanup_only vs blocking vs protected)

- **cleanup_only (차단 안 함, WARN + 정리 대상)**: 진단/임시 산출물.
  `tmp_*.json`, `tmp_tc*.json`, `*_dump.txt`, `oracle_result_dump.txt`, `build_report.xml`,
  `pr_body_*.txt`, `.pytest_tmp_*` (파일/디렉터리), `.claude/worktrees/`.
- **blocking (BLOCKED, 승인 차단)**: evidence 변조 신호.
  oracle 증거 untracked, protected evidence missing/untracked/sha_mismatch,
  `.pipeline/runs/**` 가 PR diff에 포함됨(runtime state 누출).
- **protected (절대 PR/packet 미포함, 삭제/이동 금지)**: 신뢰 루트 + 상태 파일.
  `pipeline.py`, `CLAUDE.md`, `pipeline_state.json`, `.github/`, `.pipeline/`, `tests/oracles/` 등
  (`HYGIENE_PROTECTED_PATHS` / `HYGIENE_PROTECTED_PREFIXES`).

## MT-3 리다이렉트 결론
audit 결과 pipeline.py 내부에 repo-root tmp_tc/oracle dump 생산자가 없으므로 기존 코드의
경로 변경은 불필요하다(Frozen Codebase 원칙: 무관 리팩토링 금지). 대신 MT-1의
`_scratch_path`/`_scratch_file` helper를 SSoT 진입점으로 제공하여, 향후 진단/임시 dump가
필요한 내부 생산자는 repo root 대신 `.pipeline/runs/<id>/scratch/` 를 사용하도록 표준화한다.
이 방침은 `_scratch_path`/`_scratch_file` docstring과 본 audit에 명문화한다.
