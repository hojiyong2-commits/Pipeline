# Pipeline 릴리즈 노트

## IMP-20260605-58BF — Verification JSON SSoT

Protocol Consistency Guard D/F 검사를 PR 본문 텍스트 파싱에서 `human_acceptance_packet.json`
JSON 기반으로 전환합니다. `final-packet` 실행 시 MD와 JSON이 동시 생성되며, `gates accept`는
JSON SHA freshness를 자동 검증합니다. `hygiene cleanup-workspace` 서브커맨드도 추가됩니다.

### 주요 변경

- `_build_verification_json` / `_write_verification_json` / `_load_verification_json` 신규 함수
- `_check_protocol_consistency(verification_json=...)`: D/F 검사 JSON 기반 처리
- `_verify_verification_json_freshness`: `gates accept` 시 JSON freshness 검증
- `_write_acceptance_request`: `verification_json_path`/`sha256` 필드 추가
- `hygiene cleanup-workspace`: 파이프라인 완료 후 untracked 파일 정리
- `HYGIENE_SOURCE_LIKE_EXTENSIONS` SSoT 상수 추가

## IMP-20260603-2E3D — PR Packet SSoT

**머지 예정 / 작업 중:** 사용자 ACCEPT/REJECT 판단 자료(PR 본문의 "최종 확인 안내",
`human_acceptance_packet.md`)를 에이전트 자유서술 대신 `pipeline.py`가 실제 git/gh/state
자료로 자동 생성하도록 SSoT를 강제합니다.

### 신규 명령

- `python pipeline.py report final-packet` — `human_acceptance_packet.md` 자동 생성.
  PR head SHA, CI run ID, `git diff origin/main...HEAD --name-only` 결과(변경 파일 수
  자동 산출), 게이트 상태, AC 충족표, 승인 코드(`acceptance_request.json`에 있을 때)를
  포함합니다. 모든 줄 120자 이하, 승인 코드는 독립 줄로 출력합니다.
- `python pipeline.py report update-pr-body` — 현재 PR 본문의
  `<!-- PIPELINE_FINAL_PACKET_START -->` ~ `<!-- PIPELINE_FINAL_PACKET_END -->` 블록을
  최신 packet 내용으로 교체합니다. 블록이 없으면 PR 본문 끝에 추가합니다. gh CLI가
  없으면 graceful skip.

### gates request-accept 통합 흐름 (순환 구조 해결)

`gates request-accept`가 다음 단계를 한 흐름으로 묶어 final packet 부재로 인한 차단 순환을
제거합니다.

1. 실제 PR head SHA / CI run ID / git diff 변경 파일을 직접 조회.
2. `human_acceptance_packet.md`가 있고 실제 상태와 다르면 BLOCKED + 한국어 재실행 명령.
   packet 부재는 차단하지 않음.
3. 검증 통과 시 nonce 발급(또는 조건 동일 시 재사용) + packet 자동 생성 + gh CLI 있으면
   PR 본문 자동 업데이트.

기존 Nonce Gate(`gates accept` 단계의 `--user-confirmed` 단독 차단,
`--acceptance-code` 필수), Three-Gate, AC Tracking, Protocol Consistency Guard,
Final Acceptance Readiness Gate는 그대로 유지됩니다.

### 신규 SSoT 상수

- `PIPELINE_FINAL_PACKET_START_MARKER` / `PIPELINE_FINAL_PACKET_END_MARKER`
- `HUMAN_ACCEPTANCE_PACKET_FILE = "human_acceptance_packet.md"`
- `PACKET_LINE_MAX_WIDTH = 120`

### 사용자가 확인할 결과물

- `human_acceptance_packet.md` (PR head SHA, CI run ID, 변경 파일, AC 충족표, 승인 코드 포함)
- PR 본문의 PIPELINE_FINAL_PACKET 블록 (`gh pr view`로 확인 가능)

---

# Pipeline v1.0 릴리즈 노트

**릴리즈 날짜:** 2026-05-30
**기준 커밋:** PR #356 merge 후 최신 main
**태그:** v1.0 — PR #356 merge 후 main 최신화: `git pull origin main && git tag v1.0 && git push origin v1.0`

## v1.0 안정화 릴리즈

이 릴리즈는 지금까지 머지된 파이프라인 기능을 실사용 가능한 v1 기준선으로 고정합니다.
새 기능은 추가하지 않았으며, 문서와 최종 검증에 집중합니다.

## 포함된 핵심 IMP

| IMP ID | 기능 | 머지 커밋 |
|---|---|---|
| IMP-20260529-D8BA | Security & Secrets Boundary | 8b75482 |
| IMP-20260528-0A9E | Golden Task Regression Suite | 7125790 |
| IMP-20260528-3898 | Workspace Hygiene & Artifact Isolation | 23bb5ec |
| IMP-20260527-075A | Cost/Attempt Budget Gate | 8c20102 |
| IMP-20260526-82E3 | Observability Metrics Gate | c4a0f41 |
| IMP-20260524-48C4 | Oracle Quality Gate 강화 | 7becd26 |
| IMP-20260525-6FAC | Real CLI Path E2E Gate Policy | 94f8900 |

## 포함된 핵심 게이트

| 게이트 | 명령어 |
|---|---|
| Security & Secrets Boundary | `python pipeline.py gates secrets` |
| Golden Task | `python pipeline.py golden run --smoke` |
| Workspace Hygiene | `python pipeline.py gates preflight-pr-impl` |
| Cost/Attempt Budget | `python pipeline.py budget status` |
| Observability Metrics | `python pipeline.py metrics report` |
| Oracle Quality Gate | `python pipeline.py gates oracle` |
| Real CLI Path E2E | `pytest tests/e2e/ -v` |
| Phase Attestation (Option A) | `python pipeline.py gates phase-ci --phase pm` |

## 실제 업무 사용 가능 여부

**사용 가능합니다** — 아래 조건이 모두 충족됩니다:
- 전체 테스트 PASS: `pytest tests/ -q`
- GitHub Actions CI PASS (main 기준 최신 PR)
- Security & Secrets Boundary 활성화 (IMP-20260529-D8BA)
- Phase Attestation Option A 적용
- Oracle Quality Gate 적용

주의사항: 알려진 제한사항 5가지를 확인하고 사용하십시오 (README 참조).

## 알려진 제한사항

1. **Codex Review (GPT-5.5)**: OpenAI API 쿼터 소진 시 `review codex-record`로 수동 기록 필요
2. **Google Drive 배포**: `G:\내 드라이브\터미널\` 경로가 마운트되지 않으면 배포 단계 SKIP
3. **Windows 전용**: `pipeline.py`는 Windows PowerShell 환경에서 개발됨. Linux/macOS 경로 구분자 조정 필요
4. **Phase attestation**: GitHub Actions 미실행 환경(오프라인)에서는 phase-ci gate 수동 처리 필요
5. **git tag**: v1.0 태그 자동 생성 없음 — ACCEPT 후 사용자가 직접 실행

## 다음 단계

- v1.0 태그 생성: PR #356 merge 후 main 최신화 → `git pull origin main && git tag v1.0 && git push origin v1.0`
- v1.1 로드맵: 추후 별도 IMP 파이프라인으로 계획

## 부록: 게이트별 검증 명령

### Weekly Cleanup Archive (Hygiene)

7일 이상 된 임시 산출물을 스캔하거나 Google Drive 찌꺼기 폴더로 이동합니다.

```powershell
# 후보 파일 목록 확인 (파일 이동 없음)
python pipeline.py hygiene scan --older-than 7d --json

# 실제 이동 전 dry-run 확인
python pipeline.py hygiene archive --older-than 7d --json --dry-run

# 실제 이동 (PIPELINE_DEPLOY_ROOT 아래 찌꺼기/YYYY-MM-DD/ 폴더로 이동)
python pipeline.py hygiene archive --older-than 7d

# Windows 작업 스케줄러에 주간 자동 실행 등록 (dry-run)
python pipeline.py hygiene schedule install --dry-run

# Windows 작업 스케줄러에 실제 등록 (관리자 권한 필요)
python pipeline.py hygiene schedule install

# 스케줄 등록 상태 확인
python pipeline.py hygiene schedule status
```

**보호 파일 목록** (이동 대상에서 항상 제외):
- `pipeline.py`, `CLAUDE.md`, `README.md`, `RELEASE_NOTES.md`, `pyproject.toml`, `.gitignore`, `.gitattributes`
- `.github/`, `.claude/`, `.codex/`, `.pipeline/`, `tests/`, `pipeline_contracts/`, `pipeline_outputs/` 디렉토리

**이동 대상 패턴** (`HYGIENE_ARCHIVE_PATTERNS` SSoT 상수):
- `build_report*.xml`, `qa_report*.xml`, `dev_handover*.xml`, `architect_report*.xml`
- `failure_packet.json`, `acceptance_comment.json`, `bandit_e2e_result*.json`, `codex_review_result*.json` 등

**환경 변수**:
- `PIPELINE_DEPLOY_ROOT`: 찌꺼기 이동 대상 루트 (기본값: `G:\내 드라이브\터미널`)
- `PIPELINE_STATE_PATH`: 격리 테스트 시 state 파일 경로 지정

### Security & Secrets Boundary
```powershell
python pipeline.py gates secrets
# PR diff 포함 전체 스캔
python pipeline.py gates secrets --base-ref main
```

### Golden Task (smoke 테스트)
```powershell
python pipeline.py golden run --smoke
# 전체 테스트
python pipeline.py golden run --all
```

### Observability Metrics
```powershell
python pipeline.py metrics report
```

### Cost/Attempt Budget
```powershell
python pipeline.py budget status
```

### 전체 E2E 테스트
```powershell
pytest tests/e2e/ -v
```

### 전체 테스트 (빠른 확인)
```powershell
pytest tests/ -q
```

### Phase Attestation 상태 확인
```powershell
python pipeline.py gates status
python pipeline.py status
```

---

## IMP-20260602-1ABE — 요구사항 추적 구조 개선

사용자 요구사항(AC)이 PM → Dev → Codex Review → QA → Oracle → request-accept까지
끊기지 않고 추적되도록 기존 게이트를 확장했습니다. 새 대형 게이트 추가 없이 기존
PM/Dev/Module-QA/Oracle/Codex Review 경로에 AC 연결을 강제합니다.

### 변경 내용

- **PM** `step_plan.xml`에 structured AC 블록 필수화 (AC-N 형태, 6개 필드: `ac_id`, `requirement`, `must_verify`, `source`, `user_visible`, `expected_evidence`)
- **PM** 8개 검증 규칙 (`pipeline._validate_structured_ac_block`):
  빈 AC, AC id 형식, 중복 id, 단독 추상 문구 (`ABSTRACT_AC_PATTERNS` SSoT 15개), MT covers_ac 누락, 알 수 없는 AC id, must_verify=true 미연결, covers_iqr 허용
- **state** `requirements_tracking` 플래그 (enabled, schema_version, recorded_at) + `structured_acceptance_criteria` 최상위 키 저장
- **Module QA** report XML에 `<ac_verification><criterion ac_id="AC-N"/></ac_verification>` 블록 필수화 (covers_ac 있는 MT만, 새 파이프라인 한정)
- **Dev** `scope_manifest.json`의 micro_tasks에 `implemented_tasks` 필수화 (mt_id, implemented_ac, implementation_evidence; 추상 evidence 단독 차단)
- **Oracle** `oracle_manifest.json` entry에 `ac_ids` 필드 필수화 (새 파이프라인 한정)
- **Codex Review** `coverage_checks` 7개 필드 hard gate (QA 진입 차단):
  `all_ac_reviewed`, `diff_values_match_ac`, `tests_assert_core_values`, `no_dry_run_substitution`, `no_stale_sha`, `no_stale_ci_run`, `user_facing_korean_ok`
- **Codex Review** `criteria_review` blocking=true + FAIL/UNCLEAR 항목 차단
- **request-accept** AC별 충족표 자동 조립 + 모바일 친화적 출력 + `acceptance_request.json`에 `ac_fulfillment_table` 저장
- **CLAUDE.md** "Structured AC Tracking" 섹션 추가
- **agent MD 4개** (pm-agent, dev-agent, qa-agent, test-harness-agent) AC Tracking 역할별 섹션 추가

### legacy 호환 정책

- `requirements_tracking` 필드 없는 state → legacy로 자동 판정, 모든 AC 검증 skip
- `requirements_tracking.enabled=true`인데 `structured_acceptance_criteria` 비어있음 → FAIL (legacy 취급 금지)
- `covers_ac` 없는 MT → ac_verification 면제 (legacy MT 또는 `covers_iqr`만 있는 문서 MT)
- legacy codex_review_result.json (`coverage_checks` 키 자체 없음) → 기존 동작 유지

### E2E 테스트

`tests/e2e/test_ac_tracking_1abe.py` — 46개 테스트 (회귀 2개 포함):
- 회귀 1: 사용자 AC=MON/09:00, diff=SUN/02:00 → `diff_values_match_ac=false` 차단
- 회귀 2: 사용자 AC=실제 파일 이동, 테스트=dry-run만 → `no_dry_run_substitution=false` 차단

### Oracle 케이스 (`tests/oracles/IMP-20260602-1ABE/`)

- `case_normal_01` — structured AC 정상 파싱
- `case_edge_01` — legacy state 하위 호환
- `case_error_01` — 단독 추상 AC 차단

