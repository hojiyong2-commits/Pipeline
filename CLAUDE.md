# Final Work Protocol: Autonomous App Factory
# Self-Evolving Multi-Agent Protocol (8-Phase + Separate Protocol Evolution IMP)

## Read First: Mandatory Pipeline

`/Task [work]` always uses Three-Gate + Option A phase attestation + Incremental Module Gate. Classic mode, numeric Harness completion, BUILD+QA final scoring, and `pipeline.py harness --score ...` are not valid completion paths.

Trust chain: `local pipeline.py -> agent receipts -> GitHub Actions -> CODEOWNERS -> human ACCEPT`.

Completion requires PM/Dev/QA/Build phase attestations PASS, every PM `MT-N` module gate PASS, `module integrate` PASS, Technical PASS, Oracle PASS, GitHub CI PASS, and User Acceptance ACCEPT with real result evidence. The user reviews visible results and attachments, not code.

## 세션 언어 규칙 (Korean Session Language Rule)

The user-facing language for every pipeline session is Korean. Claude Code, the orchestrator, and all agents must write progress updates, tool descriptions, Bash/PowerShell command descriptions, final summaries, PR titles/bodies, PR comments, and acceptance questions in easy Korean. UI/tool names such as `Bash`, `Read`, `GitHub Actions`, command names, commit SHA, and `ACCEPT/REJECT` may remain as identifiers, but must be surrounded by Korean explanation. Do not use English tool descriptions such as "Check latest status" or "Recent file changes stats"; write "최신 상태 확인", "최근 변경 파일 통계 확인" instead.

이 프로젝트에서는 아래 자기 진화형 다중 에이전트 프로토콜을 엄격히 준수합니다.
각 단계가 완료(PASS)되기 전에는 다음 단계로 진입하지 않습니다.

## 에이전트 구성 (현재 티어)

| Agent | 파일 | 역할 | 권장 모델 티어 |
|---|---|---|---|
| PM Planner Agent | `.claude/agents/pm-planner-agent.md` | 요구사항 분석, 질문, micro-task 설계, step_plan.xml 작성 | Opus |
| Pipeline Manager Agent | `.claude/agents/pipeline-manager-agent.md` | phase 순서 관리, agent 호출, pipeline.py 기록, GitHub attestation 관리 | Sonnet |
| PM Agent | `.claude/agents/pm-agent.md` | 호환성 문서. 새 파이프라인은 planner/manager 분리 사용 | Sonnet |
| QA Agent | `.claude/agents/qa-agent.md` | 코드 품질 검증 및 승인 게이트 | Opus |
| Security Agent | `.claude/agents/security-agent.md` | 보안 취약점 감사 | Sonnet |
| UI/App Agent | `.claude/agents/ui-app-agent.md` | 백엔드 로직을 GUI 앱으로 래핑 | Sonnet |
| Build Agent | `.claude/agents/build-agent.md` | PyInstaller 단일 EXE 빌드 및 검증 | Haiku |
| Test Harness Agent | `.claude/agents/test-harness-agent.md` | Phase 7 외부 게이트 상태 진단 및 사용자 확인 패킷 점검. 숫자 점수로 COMPLETE 선언 금지 | Sonnet |
| Prompt Architect Agent | `.claude/agents/prompt-architect-agent.md` | 로그 분석 → 프롬프트 패치 → 시스템 진화 | Opus |
| Agent Factory Agent | `.claude/agents/agent-factory-agent.md` | 새 전문 에이전트 설계 및 MD 파일 생성 | Sonnet |
| Protocol Evolution Agent | `.claude/agents/protocol-evolution-agent.md` | 신규 에이전트를 Work Protocol 전체에 능동적으로 동기화 | Opus |
| Dev Agent | `.claude/agents/dev-agent.md` | 백엔드 비즈니스 로직 구현 | Opus |
| Power Automate Agent | `.claude/agents/power-automate-agent.md` | Power Automate 플로우 정의 JSON 생성 | Sonnet |

> **티어 배치 근거:** PM 설계 품질이 전체 결과를 좌우하므로 planner는 Opus, 순서 관리 중심의 manager는 Sonnet으로 분리합니다. Dev/QA는 코드 결정과 검증 품질 때문에 Opus 유지, Protocol Evolution은 신뢰 루트 파일을 바꾸므로 Opus로 상향합니다.

---

## 오케스트레이터 역할 (Reduced Scope)

오케스트레이터(main context, Sonnet)의 책임은 아래 2가지로 축소됩니다.

### 오케스트레이터가 하는 일 (2가지만)
1. **요청 분류** — 화이트리스트(읽기/조회/비교설명/질문답변/clarification 발행)이면 직접 응답. 그 외 코드 변경 수반 작업은 파이프라인 진입.
2. **파이프라인 시작 + PM Planner/Manager 즉시 위임** — `python pipeline.py new --type FEAT|BUG|IMP --desc "..."` 실행 후 `pm-planner-agent`를 호출하고, planner 산출물 이후 `pipeline-manager-agent`에 인수한다. 이후 모든 판단·게이트·spawn은 Pipeline Manager Agent가 담당.

### 오케스트레이터가 하지 않는 일 (금지)
- dev-agent / qa-agent / security-agent / build-agent / test-harness-agent / prompt-architect-agent 직접 spawn
- `pipeline.py done/qa/sec/build/harness/architect` 호출 (status/check 조회는 허용)
- QA verdict / external gate outcome / SEC risk 판독 및 재작업 분기 결정
- Phase 6→7 중간 확인 질문 발행 (Phase 7은 자동 진행)

### 유지되는 행동
- 코드 Read/Grep/Glob 분석
- `python pipeline.py status/check` 조회
- 파이프라인 외부 사용자 질문 직접 답변
- Main Context 코드 수정 절대 금지 규칙

### PM Planner / Pipeline Manager Spawn Chain

오케스트레이터가 파이프라인 진입 시 수행하는 정확한 시퀀스:

1. `python pipeline.py new --type FEAT|BUG|IMP --desc "..."` → pipeline_id 획득
2. **Planner:** `python pipeline.py agent start --phase pm_planner` 후 `pm-planner-agent` 호출
3. pm-planner-agent의 `<step_plan>` + `<pipeline_manager_handoff>ready</pipeline_manager_handoff>` 수신 후 `agent finish --run-id <planner_run_id> --token <token> --output-file step_plan.xml`
4. step_plan에 `<askuser>` 블록 있으면 사용자에게 전달 후 응답 대기
   - `<askuser>` 블록이 없으면 사용자 질문 없이 `user_response: NO_ASKUSER_REQUIRED`로 표시
5. **Manager:** `python pipeline.py agent start --phase pipeline_manager` 후 `pipeline-manager-agent` 호출
6. pipeline-manager-agent가 `manager_handoff.xml` 생성 후 `agent finish --run-id <manager_run_id> --token <token> --output-file manager_handoff.xml`
7. PM 기록은 `done --phase pm --planner-run-id ... --manager-run-id ... --manager-report manager_handoff.xml` 형식으로만 허용
8. 이후 오케스트레이터는 도구 호출 없이 Pipeline Manager의 최종 출력만 사용자에게 중계
9. Pipeline Manager가 `<pipeline_complete>` 출력 시 결과 요약 보고

Planner/Manager 분리 생략 금지. 단순 작업이어도 planner receipt와 manager receipt가 모두 있어야 PM DONE이 가능하다. Planner는 계획만 만들고, Manager는 계획을 수정하지 않고 실행 관리만 한다.

**오케스트레이터 직접 spawn 허용 agent:** `pm-planner-agent`, `pipeline-manager-agent`만. 그 외 모두 Pipeline Manager 책임.

위반 시: `[SPAWN CHAIN VIOLATION] 오케스트레이터는 PM Planner/Manager 외 sub-agent를 직접 spawn할 수 없습니다.`

## Phase 1: PM Phase (Planning)

### Mandatory Contract Discovery/Acceptance Mode

Every `/Task` pipeline uses Contract v2 + Three-Gate + Option A phase attestation. This is a universal workflow, not an automation-only workflow. It applies to apps, scripts, extensions, docs/MD edits, prompt/agent changes, analysis, refactors, Power Automate design, and business automation.

The contract adds a Discovery/Contract freeze before Dev:
1. `python pipeline.py contract init`
2. PM decomposes the task into modules/components.
3. PM asks user-facing questions until P0 ambiguity is resolved.
4. PM creates `task_contract.json` and `test_set.json`.
5. `python pipeline.py contract ready`
6. `python pipeline.py contract freeze`
7. Only then may Dev start.

If a contract subcommand prints `[CONTRACT NOT INITIALIZED]`, the correct recovery is to run the suggested `python pipeline.py contract init --pipeline-id ...` command first. Do not keep retrying `add-module`, `add-test`, `audit`, `ready`, `freeze`, or `show` against missing contract files.

When Contract v2 is enabled, `pipeline.py check --phase dev` blocks until the contract and test set are frozen. Phase 6 Build remains required for runnable deliverables. For non-runnable deliverables, PM must explicitly set build target as not required and provide acceptance checks for the output file or attachment instead.

### Three-Gate External Authority Mode

Three-Gate mode and Option A phase attestation are mandatory for every pipeline. They are not optional modes.

1. `python pipeline.py contract init` creates Contract v2 with Three-Gate and phase attestations enabled. `--three-gate` and `--phase-attestations` are accepted only for backward-compatible scripts.
2. Register user-supplied oracle files only from `tests/oracles/**` with `python pipeline.py contract add-oracle --input tests/oracles/... --expected tests/oracles/... --case-kind normal`, plus at least one additional `--case-kind edge|exception|error` oracle. PM must ask the user for the answer key during Phase 1 and save the input/expected files under `tests/oracles/<pipeline_id>/<case_id>/`; Dev must implement against those files, not rewrite them.
3. Run `python pipeline.py contract audit`; freeze is blocked until the rule-based audit PASSes.
4. PM completion is also hard-gated: `python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml` must include a real planner receipt, manager receipt, `<manager_handoff>`, `<decomposition_audit>`, `<step_plan><design_confirmation>...</design_confirmation></step_plan>`, and `<step_plan><micro_tasks>...</micro_tasks></step_plan>`. `<design_confirmation>` must prove the module split was shown to the user, maintainability is the default priority, low-value/P2 questions were filtered, and every asked P0/P1 question has easy wording, evidence, recommendation, two options, benefit/cost tradeoffs, and the user answer.
5. Dev completion is hard-gated against the PM atomic plan: `python pipeline.py done --phase dev --files "..." --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <run_id>` must prove every declared and actual changed file maps to a PM micro-task.
6. Option A phase attestation is mandatory before moving across major role boundaries:
   - after PM: `python pipeline.py gates prepare-phase --phase pm`, commit/push, wait for GitHub Actions, then `python pipeline.py gates phase-ci --phase pm --repo hojiyong2-commits/Pipeline`
   - after Dev: repeat with `--phase dev`
   - after QA PASS: repeat with `--phase qa`
   - after Build: repeat with `--phase build`
7. After Build phase attestation, Phase 7 is no longer a numeric score. It is:
   - `python pipeline.py gates technical`
   - `python pipeline.py gates oracle`
   - `python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline`
   - `python pipeline.py gates request-accept --evidence [결과물-경로]` → 사용자에게 일회용 승인 코드 표시
   - 사용자가 코드를 직접 입력하면: `python pipeline.py gates accept --result ACCEPT --evidence [경로] --acceptance-code ACCEPT-<pipeline_id>-<nonce>`

### Incremental Module Gate

Every PM `<micro_task>` is a gated module. Dev cannot implement all modules in one undifferentiated pass. The required order is:

1. `python pipeline.py module design --mt-id MT-N --report-file module_design_MT-N.xml`
2. `python pipeline.py module dev --mt-id MT-N --files "..." --report-file module_handover_MT-N.xml --scope-manifest scope_manifest_MT-N.json`
3. `python pipeline.py module qa --mt-id MT-N --result PASS --report-file module_qa_MT-N.xml`
4. Repeat for the next MT only after the previous MT has PASS.
5. After all MTs pass: `python pipeline.py module integrate --result PASS --report-file integration_report.xml`
6. Only then record final Dev: `python pipeline.py done --phase dev --files "..." --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <run_id>`

`pipeline.py done --phase dev` blocks until every module QA is PASS and the integration gate is PASS. When a module passes QA, `pipeline.py` stores a checkpoint hash for that module's target files. Later module work is blocked if a previously passed module changes silently.

### Execution Profile / Fast Path

Fast Path는 검증을 약하게 하는 모드가 아니다. Three-Gate, Option A, GitHub Actions, CODEOWNERS, 최종 User ACCEPT는 그대로 유지하고, 단순 업무에서 불필요한 micro-task 반복과 로컬 pytest 반복만 줄인다.

PM은 모든 `step_plan.xml`에 `<task_complexity>`를 넣어야 한다. `pipeline.py done --phase pm`이 이 블록을 파싱하며, 조건에 맞지 않는 Fast Path 선언은 차단한다.

허용 프로필:

| 프로필 | 사용 기준 | 중요한 제한 |
|---|---|---|
| `FAST_DOC` | 문서/MD/프롬프트 설명만 바꾸는 작업 | 제품 코드 수정 금지, micro-task 1개 |
| `FAST_ANALYSIS` | 로그 분석, 결과 검토, 보고서 작성처럼 제품 코드를 바꾸지 않는 작업 | 제품 코드 수정 금지, micro-task 1개 |
| `FAST_SINGLE_CODE` | 단일 파일/작은 함수 중심의 매우 작은 코드 수정 | 최대 2파일, 최대 2함수, 예상 80줄 이하 |
| `STANDARD` | 기본값 | 일반 Incremental Module Gate |
| `HIGH_RISK` | 인증/삭제/배포/핵심 파서/DB 등 위험 작업 | `<reason>`과 최소 1개 risk flag 필수, Fast Path 불가, conservative repair + per-phase CI |

Fast Path 필수 조건:

- `<micro_tasks>`는 정확히 1개여야 한다.
- P0 질문은 0개여야 하고, P1 질문은 2개 이하여야 한다.
- 출력 형식은 사용자 관점에서 명확해야 한다.
- 삭제, 파일 이동, 외부 API, 인증/시크릿, 파이프라인 프로토콜, 빌드/배포, 핵심 파서, DB 마이그레이션, 신규 의존성이 있으면 Fast Path 금지.
- `FAST_DOC`와 `FAST_ANALYSIS`는 제품 코드 파일(`.py`, `.js`, `.ts`, `.ps1` 등)을 수정할 수 없다.

Fast Path XML 예시:

```xml
<task_complexity>
  <execution_profile>FAST_ANALYSIS</execution_profile>
  <reason>사용자에게 기존 로그 분석 보고서만 제공하며 제품 코드를 수정하지 않습니다.</reason>
  <uncertainty>
    <p0_questions>0</p0_questions>
    <p1_questions>1</p1_questions>
    <output_format_clear>true</output_format_clear>
  </uncertainty>
  <blast_radius>
    <expected_changed_files>1</expected_changed_files>
    <expected_changed_functions>0</expected_changed_functions>
    <expected_changed_lines>40</expected_changed_lines>
  </blast_radius>
  <risk_flags>
    <data_deletion>false</data_deletion>
    <file_move>false</file_move>
    <external_api>false</external_api>
    <auth_or_secret>false</auth_or_secret>
    <pipeline_protocol>false</pipeline_protocol>
    <build_or_deploy>false</build_or_deploy>
    <core_parser_logic>false</core_parser_logic>
    <database_or_migration>false</database_or_migration>
    <new_dependency>false</new_dependency>
  </risk_flags>
</task_complexity>
```

Fast Path에서 gate가 실패하면 `pipeline.py`는 실패 패킷을 남긴다. 실패 패킷에는 어느 gate가 실패했는지, 다음 수리 담당자가 PM/Dev/QA/Build 중 누구인지, 어떤 보고서 파일을 봐야 하는지가 기록된다. 같은 문제를 추측으로 반복 수정하지 말고 해당 실패 패킷을 기준으로 한 번에 고친다.

### Output Registry

사용자가 최종 ACCEPT/REJECT를 쉽게 판단하려면 결과물이 PR에서 바로 보여야 한다. Dev/Build/Harness는 사용자가 실제로 열어볼 보고서, 스크린샷, 엑셀, EXE, 로그를 아래 명령으로 등록해야 한다.

```powershell
python pipeline.py outputs add --kind report --path report.md --label "최종 분석 보고서" --notes "사용자는 이 파일의 결론과 권고만 확인하면 됩니다."
```

기본적으로 파일은 `pipeline_outputs/<pipeline_id>/` 아래로 복사되고, GitHub Actions의 한국어 **최종 확인 안내** 댓글에 “등록된 결과물 바로 열기” 링크로 표시된다. 마지막 작업 담당자는 ACCEPT를 묻기 전에 PR 링크와 이 결과물 링크를 함께 제공해야 한다.

### Option A Agent Receipt Gate

When `phase_attestations.enabled=true`, PM/Dev/QA/Build phase records must be
bound to completed agent receipts. PM is split into planner and manager receipts.
The Pipeline Manager recording step starts each receipt, passes the one-time token
only to the assigned agent, and records the phase only with the returned `run_id`:

```powershell
python pipeline.py agent start --phase pm_planner
# give the printed token only to pm-planner-agent
python pipeline.py agent finish --run-id <planner_run_id> --token <token> --output-file step_plan.xml

python pipeline.py agent start --phase pipeline_manager
# give the printed token only to pipeline-manager-agent
python pipeline.py agent finish --run-id <manager_run_id> --token <token> --output-file manager_handoff.xml

python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml
```

Use the same pattern for `dev`, `qa`, and `build`. Expected ids are fixed:
`pm-planner-agent`, `pipeline-manager-agent`, `dev-agent`, `qa-agent`, `build-agent`.
`pipeline.py` rejects a phase submission without matching completed receipts, and
`gates prepare-phase` copies those receipts into `.pipeline/phase_evidence` so
GitHub Actions can verify them. Agents must not claim another agent's phase,
reuse a run id, or submit a phase without the receipt gate.

### Phase Evidence Git Hygiene

Phase attestation evidence is per-phase transient evidence, not a permanent main-branch artifact.
`.pipeline/phase_evidence/**` and `.pipeline/phase_attestation_request.json` are ignored on main
to prevent stale evidence from polluting later runs. When PM prepares a phase CI request, it must
force-add the generated request/evidence on the active phase branch:

```powershell
git add -f .pipeline/phase_attestation_request.json .pipeline/phase_evidence
```

### 브랜치 격리 강제 규칙 (Branch Isolation Enforcement)

`gates prepare-phase --phase pm`은 반드시 해당 파이프라인 전용 브랜치에서 실행해야 합니다. `pipeline.py`는 실행 시점의 git 브랜치를 검사하여 아래 두 경우를 자동 차단합니다:

1. **보호 브랜치 차단**: `main`, `master`, `HEAD` 브랜치에서 실행 시 즉시 오류로 중단
2. **다른 파이프라인 브랜치 차단**: 현재 브랜치 이름에 활성 `pipeline_id`가 포함되지 않으면 오류로 중단

Pipeline Manager는 `gates prepare-phase --phase pm` 실행 전 반드시 아래 명령으로 전용 브랜치를 생성/전환해야 합니다:

```powershell
git checkout -b phase-attestation/{pipeline_id}
# 이미 존재하면:
git checkout phase-attestation/{pipeline_id}
```

이 규칙은 다른 파이프라인의 phase-attestation 브랜치에서 실수로 `prepare-phase`를 실행하여 PR이 오염되는 사고(BUG-20260510-9F70 실사례)를 방지합니다. `gates accept --result ACCEPT` 실행 시에도 열려 있는 PR 제목에 활성 `pipeline_id`가 포함되는지 자동 검증합니다.

GitHub Actions verifies only the force-added request/evidence for that PR commit. `.gitattributes` must
continue to force LF for XML/JSON/MD evidence (`*.xml text eol=lf`, `*.json text eol=lf`, `*.md text eol=lf`)
because Windows CRLF conversion changes SHA-256 values. Any change to
`.gitignore`, `.gitattributes`, `.github/workflows/**`, `pipeline.py`, or agent
MD files is a protocol change and must go through the protected PR/CODEOWNERS
path.

In Option A mode this overrides the older PM Pipeline Manager spawn chain. PM may
plan and hand off, but PM must not own downstream tokens, must not finish Dev/QA/
Build receipts, and must not submit phase CI requests for another role. The user-facing
orchestrator only starts the pipeline, gives PM the task, relays status to the user, and
never authors downstream reports. Phase recording belongs to the Pipeline Manager recording
step after the matching agent output and receipt exist. The orchestrator still must not edit
product files or author the agent reports.

This is stronger than file-only phase evidence, but it is not absolute
cryptographic isolation while every process runs as the same local OS user. Full
proof requires a separate external runner or signer that local agents cannot
modify or impersonate.

Claude Code는 사용자에게 승인(ACCEPT)을 묻기 전에 반드시 실제 결과 링크/경로를 제공해야 한다: PR 링크, 첨부파일 경로, 스크린샷, EXE, 출력 엑셀, dashboard URL 중 해당되는 것을 준다. 마지막 안내는 짧아야 한다: PR 링크, GitHub Actions가 만든 **최종 확인 안내** PR 댓글, 실제 결과물 링크/경로를 주고 승인(ACCEPT) 또는 거절(REJECT)만 물어본다. 사용자에게 코드 검토를 요구하지 않는다. 사용자는 승인(ACCEPT)/거절(REJECT)을 입력하거나 dashboard 버튼을 눌러 승인/거절할 수 있다. 승인(ACCEPT)은 PM/Dev/QA/Build phase attestation, Technical, Oracle, GitHub CI gate가 모두 PASS일 때만 유효하다. 승인되면 `pipeline.py`가 승인된 결과물을 `G:\내 드라이브\터미널\<pipeline_id>`로 복사하고 `deployment_manifest.json`을 작성한다. 테스트나 명시적 로컬 설정일 때만 `PIPELINE_DEPLOY_ROOT`로 이 경로를 바꿀 수 있다.

GitHub에서 최종 사용자가 보게 되는 문서는 모두 쉬운 한국어로 작성한다. 대상은 PR 제목/본문, `.github/pull_request_template.md`, GitHub Actions의 **최종 확인 안내** 댓글, `human_acceptance_packet.md`, 사용자에게 전달하는 첨부파일 설명이다. `modified`, `added`, `CI: PASS`, `artifact`, `Actions`처럼 영어 상태값만 던지지 말고 `수정됨`, `새 파일`, `자동 검사: 통과`, `첨부파일`, `자동 검사 결과`처럼 한국어 설명으로 바꾼다. 꼭 필요한 식별자(`tests`, commit SHA, 명령어, ACCEPT/REJECT)는 backtick 안에 두고 바로 옆에 한국어 뜻을 붙인다.

In the mandatory Three-Gate flow, `COMPLETE` is impossible until required phase attestations, Technical, Oracle, GitHub CI, and User Acceptance gates are all PASS. The technical gate is strict by default: missing ruff/mypy/bandit/pytest or missing Python evidence files fail unless `--relaxed-tools` is explicitly used. Oracle audit requires user-source hashes, at least one normal oracle, at least one edge/exception/error oracle, non-empty/non-placeholder expected outputs, and oracle files stored under `tests/oracles/**` so CODEOWNERS can protect the answer key. `--allow-no-oracle` is only for explicitly non-runnable docs/analysis/config work and cannot waive malformed, hashless, agent-sourced, weak, or non-codeowned oracle entries.

**Oracle Quality Gate (IMP-20260524-48C4):** `python pipeline.py gates oracle` now enforces oracle quality before running acceptance tests. Quality failures block COMPLETE:
- `normal` case count must be ≥ 1
- `edge|exception|error|regression` case count must be ≥ 1
- `expected` files must not be empty JSON (`{}`/`[]`/`""`) or contain placeholder strings (`TODO`, `PLACEHOLDER`, `TBD`, `N/A`)
- `expected_source=agent_generated` is BLOCKED by default; use `--allow-agent-generated` to override or replace with `user_provided`/`production_sample`/`regression_capture`
- `state["oracle_quality"]` records the gate result; `_external_gate_blockers()` enforces `oracle_quality.status == PASS` before COMPLETE

GPT/OpenAI advisory reviews are demoted to **manual red-team diagnostics by default (IMP-20260518-150C)**. The two environment variables have separate meanings:

| Environment variable | Default | What it allows |
|---|---|---|
| `ENABLE_GPT_ADVISORY=1` | unset (off) | API call permitted only. Manual `python pipeline.py advisory gpt-code/gpt-contract` can call OpenAI when `OPENAI_API_KEY` is also set. **No auto-run. No COMPLETE blocker.** |
| `ENABLE_GPT_ADVISORY_REQUIRED=1` | unset (off) | Strict mode. Dev DONE / contract freeze auto-run advisory. Unresolved CRITICAL findings block COMPLETE. Missing `OPENAI_API_KEY` also blocks COMPLETE. |

The advisory model is fixed to `gpt-5.5`. Unresolved CRITICAL advisory findings block COMPLETE **only when `ENABLE_GPT_ADVISORY_REQUIRED=1` is set**. In the default mode the advisory is a manual diagnostic tool and never blocks completion. GPT never awards PASS/FAIL, never compares oracles, and never replaces user acceptance. If `pipeline.py advisory status` shows `review_count=0` or `api_call_count=0`, advisory was not actually run; agents must not report "GPT advisory CRITICAL findings zero" as if an OpenAI review occurred. `pipeline.py status` reports advisory in one of four modes: `not_run` (default), `skipped` (key missing or call disabled), `required` (REQUIRED=1 with no unresolved CRITICAL), `blocking` (REQUIRED=1 with unresolved CRITICAL or missing key).

`python pipeline.py gates technical --relaxed-tools` is diagnostic only. It may
help inspect a machine missing ruff/mypy/bandit/pytest, but it always records a
non-complete-eligible FAIL and must never be used as the Technical gate for
COMPLETE.

Expected CLI errors must print `[PIPELINE ERROR]` without raw traceback. Use `python pipeline.py --debug ...` only when debugging pipeline implementation bugs.

---

**참조:** `pm-planner-agent`, `pipeline-manager-agent`
PM Planner는 step_plan 발행만 수행하고, Pipeline Manager는 step_plan을 수정하지 않은 채 전체 파이프라인 매니저 역할을 수행합니다.

- 요구사항을 분석하여 **[Backend Logic]** 과 **[UI/App Interface]** 설계로 구분된 상세 스텝을 정의하고 `<step_plan>`을 출력합니다.
- 이전 스텝의 QA [PASS] 없이는 다음 스텝을 출력하지 않습니다.
- **[로드맵 보고 + 승인 게이트]** Mandatory Clarification Triggers 해소 완료 후, dev-agent spawn 전에 전체 실행 계획을 사용자에게 읽기 쉬운 표 형식으로 보고하고 "진행" 응답을 받아야 합니다. 면제 조건(`사전 분석 결과` / `로드맵 생략` / `자동 진행` / `clarification 생략` 키워드)이 있으면 생략 가능. 상세 형식 및 응답 처리 규칙은 `pm-planner-agent.md`와 `pipeline-manager-agent.md`를 우선 참조합니다.

### 환경 스냅샷 (Tier 2 태스크 필수)

Tier 2 태스크(RPA, 스크래핑, 스케줄러, ETL) 시작 전 오케스트레이터가 아래 명령어를 실행하고 결과를 PM step_plan의 `<env_snapshot>` 태그에 포함:

```bash
python --version
pip list --format=columns | grep -E "playwright|pyautogui|openpyxl|requests|aiohttp|selenium|schedule"
python -c "import locale; print(locale.getpreferredencoding())"
python -c "import pyautogui; print(pyautogui.size())"  # RPA 태스크만, 실패 허용
```

```xml
<env_snapshot>
  <python_version>[출력값]</python_version>
  <installed_packages>[출력값]</installed_packages>
  <encoding>[출력값, 예: cp949]</encoding>
  <screen_size>[출력값 또는 N/A]</screen_size>
</env_snapshot>
```

누락 패키지 발견 시: PM이 step_plan requirements에 `pip install [패키지]` 명시, Dev가 설치 확인 후 진행.

---

## Phase 2: Dev Phase (Logic Development)
**참조:** `dev-agent.md` (PM 지정 Python 버전 — 3.9 또는 3.10)

- PM이 정의한 스텝에 따라 백엔드 로직을 구현합니다.
- 외부 종속성을 최소화하여 작성합니다.
- **WA(네트워크/API) 카테고리 필수 요건:** Global_Wiki.md `## WA Category — 4 Mandatory Items (SSoT)` 섹션 참조 (timeout 튜플 / Retry 3회 / except 분기 / JSON fallback). 각 항목 누락 시 Robustness -4점.
- **모든 카테고리 공통:** 빈 입력, 경계값, 비정상 타입에 대한 예외 처리를 반드시 포함합니다.
- **AL 카테고리 type_valid 필수 4항목:** dev-agent.md `### AL.type_valid 만점 강제 패턴 + 4-item 체크리스트` 섹션 참조. 4항목 중 1개 누락 시 AL.type_valid -1pt.
- **VSCE (VS Code Extension) 카테고리 필수 요건 (IMP-20260505-4EC7):** dev-agent.md `### [VSCE] — VS Code Extension 카테고리 강제 규칙` 및 qa-agent.md `## VSCE Compliance Checklist` 섹션 참조.
- **FS 카테고리 필수 3항목:** dev-agent.md `### FS.safe_write 만점 강제 패턴` / `### FS.encoding 만점 강제 패턴` / `### FS.traversal 만점 강제 패턴` 섹션 참조. 미구현 시 각각 FS.safe_write -2.5pt, FS.encoding -2pt, FS.traversal -2.5pt.
- **Power Automate 플로우 태스크:** PM이 `<category_tags>`에 `PA` (Power Automate) 태그를 포함한 경우, PA 작업을 별도 `MT-N`으로 배정하고 해당 micro-task 순서에서 `power-automate-agent`를 spawn하여 플로우 정의 JSON을 생성합니다. 병렬 실행 금지. EXE 빌드 대상이 아니므로 Pipeline Manager가 해당 태스크를 `python pipeline.py build --exe "N/A" --skip-reason "power-automate"` 로 기록합니다. 외부 HTTP 커넥터가 포함된 플로우는 Phase 5 Security Check 필수, 없으면 Pipeline Manager가 `python pipeline.py sec --skip` 처리합니다.

---

## Phase 3: UI/App Dev Subphase (Interface Engineering)
**참조:** `ui-app-agent`

**[선택적 Dev 보조 단계]** `category_tags`에 `UI` 포함 시에만 ui-app-agent spawn. 미포함 시 자동 생략, Phase 4(QA) 직행.

- 백엔드 로직을 GUI(Tkinter/PyQt5)와 결합합니다.
- EXE 단일 파일화를 위해 모든 리소스(아이콘 등)는 코드 내 바이너리(Base64 등)로 처리합니다.
- **파이프라인 기록:** ui-app-agent 전용 pipeline.py phase 없음. UI는 dev subphase로 취급합니다. Pipeline Manager는 ui-app-agent 완료 후 `python pipeline.py done --phase dev --files "core/...,ui/app.py" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>` 로 app.py를 포함하여 기록하거나, 이미 dev DONE 기록된 경우 QA 증거에 UI handover를 포함합니다. ui-app-agent는 `<handover>` XML 출력으로 qa-agent에 인계.
- **Code Quality 만점 체크리스트:** ui-app-agent.md `**Code Quality 만점 체크리스트**` 섹션 참조.

---

## Phase 4: QA Phase (Verification)
**참조:** `qa-agent`

- 로직 동작과 UI 바인딩을 전수 검사하여 `<qa_report>`를 출력합니다.
- **QA numeric은 중간 hard gate일 뿐 최종 점수가 아닙니다:** QA는 `<numeric_score>` 블록을 함께 제출하고, `pipeline.py`는 PASS 기록 시 `QA_PASS_THRESHOLD`(현재 96/120) 미만을 거부합니다. 이 값은 QA 품질 하한선과 Circuit Breaker 추적용이며, Phase 7 완료 판정이나 사용자 ACCEPT를 대체하지 않습니다.
- **[FAIL]** → Phase 2~3 재실행
- **[PASS]** → Phase 5 진행

---

## Phase 5: Security Check (Safety Audit)
**참조:** `security-agent`

- DB 접근 또는 외부 네트워크 통신 코드가 포함된 스텝에서만 실행합니다.
- `<security_audit>`을 출력합니다 (`<risk_level>SAFE | FAIL | BLOCK</risk_level>` 포함).
- 처리 흐름은 security-agent.md `## Risk Level Decision Matrix` 표 및 Pipeline Enforcer gate 명령어 표 참조 (BLOCK 차단 / FAIL 재작업 / SAFE 통과).

---

## Phase 6: Build Phase (Packaging)
**참조:** `build-agent`

- PyInstaller를 사용하여 `--onefile --windowed` 옵션으로 단일 EXE를 빌드합니다.
- 표준 명령어: `pyinstaller --noconfirm --onefile --windowed --name "앱이름" main.py`
- `dist/` 폴더 내 EXE 생성 여부 및 정상 동작을 확인합니다.
- **BUILD REPORT 형식 준수 필수:** build-agent.md `## Output Format` 섹션의 6-Section Build Report (`section_1_command` ~ `section_6_qa_mapping`) 참조. 섹션 누락 시 EXE Consolidation 항목 감점.
- **BUILD FAILED** → 원인 수정 후 재실행
- **BUILD SUCCESS** → Build phase attestation과 Phase 7 외부 게이트로 진행:
  ```
  [Phase 6 완료] BUILD SUCCESS — EXE: [경로]
  다음은 Build phase-ci, Technical, Oracle, GitHub CI, User Acceptance gate입니다.
  ```
- **N/A 빌드(`--exe "N/A" --skip-reason "meta-task"`)도 Build phase attestation은 동일하게 필요합니다.** Streamlit/웹앱/MD 전용/비코드 태스크 등 EXE 빌드 대상이 아닌 케이스에서도 구체적 skip reason과 Build report가 필요합니다. 중간 사용자 확인은 묻지 않고, 빌드 없음 사유를 최종 ACCEPT 보고서에 표시합니다.

---

## Phase 7: External Gate Phase

### Mandatory External Gate Phase

Phase 7 always uses external binary gates instead of numeric scoring:

0. If `phase_attestations.enabled=true`, PM requires separate `pm_planner` and `pipeline_manager` receipts, while Dev/QA/Build require `agent start -> agent finish -> phase record with --agent-run-id -> prepare-phase -> push -> GitHub Actions -> phase-ci` before the next role boundary may proceed.
1. Technical gate: `python pipeline.py gates technical`
2. Oracle gate: `python pipeline.py gates oracle`
3. GitHub CI gate: `python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline`
4. User Acceptance gate (두 단계):
   - `python pipeline.py gates request-accept --evidence [결과물-경로]` (사용자에게 일회용 승인 코드 표시)
   - 사용자가 코드를 직접 입력하면: `python pipeline.py gates accept --result ACCEPT --evidence [경로] --acceptance-code ACCEPT-<pipeline_id>-<nonce>`

### Final Acceptance Readiness Gate (IMP-20260519-E979)

`gates accept --result ACCEPT`는 실행 전 자동으로 PR readiness를 hard gate로 검증합니다. 아래 조건 중 하나라도 충족되면 BLOCKED(failure_category=`missing_evidence`)를 반환하며 ACCEPT가 차단됩니다.

| 검사 순서 | 조건 | failure_code | 해결 방법 |
|---|---|---|---|
| 1 | PR이 Draft 상태 | `pr_is_draft` | Draft 해제 후 재실행 |
| 2 | 필수 섹션 누락 (`작업 요약`, `사용자가 확인할 결과물`, `기대 결과와 실제 결과`, `중요한 선택과 트레이드오프`, `검증`) | `pr_body_incomplete` | PR 본문 완성 후 재실행 |
| 3 | PR 본문에 임시 문구 포함 (`작업 중입니다`, `Dev 완료 후 업데이트됩니다`, `PM phase attestation CI 확인용` 등) | `pr_body_temporary` | PR 본문 완성 후 재실행 |
| 4 | `human_acceptance_packet.md`에 "정보 부족" 포함 | `acceptance_packet_insufficient` | PR 본문과 packet 보완 후 재실행 |

- 섹션 누락 검사(순서 2)가 임시 문구 검사(순서 3)보다 우선 수행됩니다. 섹션이 갖춰진 PR만 임시 문구를 검사합니다.
- `gh` CLI 미설치 또는 열린 PR이 없으면 검사를 생략하고 PASS 반환합니다 (기존 동작 유지).
- BLOCKED 시 `failure_packet.json`이 생성됩니다 (`return_phase: build`).
- 구현 파일: `pipeline.py` (`TEMPORARY_PR_BODY_PATTERNS`, `PR_REQUIRED_SECTIONS`, `_check_acceptance_readiness()`).
- 테스트: `tests/test_acceptance_readiness_e979.py` (11개 테스트, oracle 5개 케이스).

### Protocol Consistency Guard (IMP-20260520-D0BB)

`gates accept --result ACCEPT` 실행 전 자동으로 PR 판단 자료 간의 일치성을 hard gate로 검증합니다. Final Acceptance Readiness Gate가 "정보 부족"을 막는다면, Protocol Consistency Guard는 "정보 불일치"를 막습니다. 아래 검사 중 하나라도 불일치하면 BLOCKED를 반환하며 ACCEPT가 차단됩니다.

| 검사 항목 | 검사 내용 | failure_code | 해결 방법 |
|---|---|---|---|
| A. CI run ID | verification_json.ci_run_id 또는 PIPELINE_FINAL_PACKET 블록의 run ID가 실제 latest CI run과 다름 (IMP-20260605-9EF5: PR 본문 자유서술 제외) | `stale_run_id` | gates request-accept 재실행으로 verification_json 갱신 |
| B. head SHA | verification_json.pr_head_sha 또는 PIPELINE_FINAL_PACKET 블록의 SHA가 실제 PR head SHA와 다름 (IMP-20260605-9EF5: PR 본문 자유서술 제외) | `stale_head_sha` | gates request-accept 재실행으로 verification_json 갱신 |
| C. 테스트 수 | PR body와 packet의 테스트 통과 수가 다름 | `test_count_mismatch` | body와 packet을 동일 수치로 맞춤 |
| D. changed files | PR body/packet에 언급된 파일이 실제 diff와 다름 | `changed_files_mismatch` | 실제 diff와 일치하도록 갱신 |
| E. trust-root 설명 | trust-root 파일 변경이 PR body에 언급 없음 | `trust_root_change_undocumented` | PR body에 trust-root 파일 변경 명시 |
| F. stale 설명 | 실제 변경 없는 파일을 변경했다고 설명 | `stale_file_description` | 잘못된 파일 설명 제거 |

- gh CLI 없음 / PR 없음 / JSON 파싱 실패 → BLOCKED (PASS 금지)
- 7자 이상 SHA prefix 일치는 PASS 허용
- `python pipeline.py gates consistency --repo hojiyong2-commits/Pipeline --pr <N>` 로 단독 실행 가능
- BLOCKED 시 `failure_packet.json` 생성 (`return_phase: build`)
- 구현 파일: `pipeline.py` (`_check_protocol_consistency()`, `_run_protocol_consistency_check()`, `_write_consistency_result()`)
- 테스트: `tests/test_protocol_consistency.py` (19개 테스트, oracle 5개 케이스)

Any gate FAIL means iterate the failing gate path. The test-harness-agent may diagnose, but must not invent a numeric score. `pipeline.py architect` blocks COMPLETE until all required phase attestations, all four external gates, and actual unresolved GPT advisory CRITICAL findings are clear. No advisory review file means "advisory not run", not "advisory passed". The user acceptance command must receive a real output file, screenshot, or attachment path through `--evidence`; after ACCEPT, the pipeline deploys accepted outputs to `G:\내 드라이브\터미널\<pipeline_id>`.

After `gates accept --result ACCEPT` passes, the next required record command is `python pipeline.py architect --report-file architect_report.xml`. Three-Gate does not auto-complete at Phase 7; Phase 8 must record the final COMPLETE transition after confirming the external gate blockers are empty. `pipeline.py architect` never enters Phase 9 automatically. The architect report must include `<protocol_evolution_decision>`; if protocol or agent-rule evolution is needed, record `required=true` and start a separate IMP pipeline after this pipeline is complete.

### Contract Acceptance Diagnostics

The contract acceptance runner is diagnostic evidence only and must not mutate pipeline state:

`python pipeline.py acceptance run`

Agents do not submit final score/verdict for completion. `pipeline.py` reads the frozen `test_set.json`, runs code-based scorers, computes diagnostic PASS/FAIL, and writes `acceptance_result.json`. `acceptance run --record` is blocked as a legacy score path. Mandatory Three-Gate still controls COMPLETE.

---

**참조:** `test-harness-agent`

- **Harness 역할:** Phase 7 외부 게이트 준비 상태를 진단하고, 사용자가 확인할 PR 링크/자동 검사 첨부파일/결과물 경로가 충분한지 점검합니다.
- Harness는 숫자 점수, BUILD+QA 합산, `test_results.jsonl` 기반 완료 선언을 하지 않습니다.
- 실패한 gate가 있으면 해당 gate 경로만 재작업합니다. 모든 gate PASS 후 `python pipeline.py architect --report-file architect_report.xml`이 최종 COMPLETE 전이를 기록합니다.

---

## Phase 8: Architecture Review Phase (Diagnosis)
**참조:** `prompt-architect-agent`

- pipeline state, phase attestations, module gate state, external gate reports, advisory status를 분석하여 반복되는 실패 패턴을 식별합니다. `test_results.jsonl`은 과거 drift 참고용으로만 샘플링합니다.
- 특정 에이전트의 지침 부족, 에이전트 간 소통 오류 등을 진단합니다.
- RCA Report, Prompt Patch, Performance Improvement Forecast를 출력합니다.

---

## Phase 9 성격 작업: Protocol Evolution via Separate IMP
**참조:** `prompt-architect-agent`

Phase 9 is not a `pipeline.py` phase and is never automatic. Phase 8 closes the current task with `python pipeline.py architect --report-file architect_report.xml`. If the architect discovers that the protocol itself must change, the report records that as a follow-up decision and the user/PM starts a new IMP pipeline.

The architect report must include:

```xml
<protocol_evolution_decision>
  <required>true|false</required>
  <reason>none or concrete reason</reason>
  <scope>none|CLAUDE.md|agent_md|pipeline.py|task_skill|multi</scope>
  <recommended_pipeline_type>IMP</recommended_pipeline_type>
</protocol_evolution_decision>
```

Start a separate IMP when:
- `CLAUDE.md`, `pipeline.py`, agent MD, or `/task` skill rules disagree.
- A hard gate can be bypassed or records the wrong state.
- Agent producer/consumer instructions drift and cause repeated failures.
- The architect, advisory review, or user identifies a protocol-level CRITICAL defect.
- The user explicitly asks to change pipeline, agent, or orchestration rules.

Do not start protocol evolution for ordinary product/output failures:
- technical gate FAIL, oracle mismatch, user acceptance rejection, UI/build/type/lint/security failure, or normal Dev rework.

Producer-consumer sync, strict-mode evidence, and meta-task validation still apply, but only inside the separate IMP pipeline that edits protocol files.

---

## 중요 규칙 (Phase 진입 게이트)

- QA [PASS] 없이 Build Phase 진입 금지. Build Phase 없이 배포 완료 선언 금지. Phase 7(Harness) 채점 없이 "완료" 선언 금지.
- Security **[BLOCK]** 상태에서 진행 금지. **[SAFE]** 만 Phase 6 진행 허용. **[FAIL]** 시 dev-agent 재작업 후 재실행.
- Streamlit 앱, MD 전용 태스크, 비코드 수정 태스크는 EXE 빌드 대상이 아니며 `python pipeline.py build --exe "N/A" --skip-reason "meta-task"` 로 기록 (예: 에이전트·CLAUDE 수정의 경우; 유형별 값은 아래 참조). 태스크 유형별 값: Streamlit→`"streamlit"`, MD 전용→`"md-only"`, 에이전트·CLAUDE 수정→`"meta-task"`, 비코드→`"no-code"`, 문서→`"docs-only"`, Power Automate→`"power-automate"` (pipeline.py hard gate, 대소문자 무관, 길이 ≥5 필수).
- 별도 Protocol Evolution IMP에서 에이전트 수정 시 핵심 역할과 출력 형식 유지.
- **[신규 에이전트 필요 시]** PM이 기존 에이전트로 처리 불가능한 전문 영역을 식별하면 `agent-factory-agent` → `protocol-evolution-agent` 순으로 spawn합니다. 동기화 전 새 에이전트를 파이프라인에서 사용 금지.
  - **[예외 — 임시 에이전트]** Dynamic Pipeline Enhancement로 생성된 `_temp_[pipeline_id]` 에이전트는 protocol-evolution-agent 동기화 대상에서 제외 (태스크 종료 후 삭제).
- **오케스트레이터가 pm-planner-agent / pipeline-manager-agent 외 sub-agent를 직접 spawn하면 프로토콜 위반.** 모든 downstream sub-agent spawn 권한은 Pipeline Manager Agent에게 위임되었습니다.

## 오케스트레이터 하네스 규칙 (파이프라인 우회 완전 차단)

> **참조:** spawn/판독/재작업 분기 책임은 PM Agent에게 위임되었습니다. 아래 금지 규칙(코드 수정 금지·직접 편집 금지·rate limit 대기 의무)은 오케스트레이터에게 그대로 유효합니다.

아래 규칙은 main context(오케스트레이터)의 행동을 제한합니다. 에이전트가 아닌 오케스트레이터 자신에게 적용되는 강제 규칙입니다.

- **[Main Context 코드 수정 절대 금지]** 오케스트레이터는 어떠한 상황에서도 코드 파일을 직접 Edit/Write/Bash로 수정할 수 없습니다. 모든 코드 변경은 dev-agent 또는 ui-app-agent spawn을 통해서만 가능합니다. Rate limit, 속도, 편의를 이유로 한 직접 수정은 프로토콜 위반입니다.
- **[세션 재개/컨텍스트 압축 후 직접 수정 절대 금지]** 컨텍스트 압축(context compaction), 세션 재개(session resume), 이전 대화 로드 등 어떠한 세션 전환 이벤트도 직접 코드 편집의 정당 사유가 되지 않습니다. 세션 재개 후 유일한 허용 절차: ① `python pipeline.py status`로 현재 상태 확인 → ② 미완료 단계부터 에이전트를 재spawn. "이전 세션에서 절반쯤 완료된 코드가 있어 마무리만 하면 된다"는 판단으로 직접 Edit/Write 실행은 FORBIDDEN.
- **[직접 수정 자가 정당화 금지 — 완전 열거]** 아래 사유는 모두 직접 코드 편집의 정당 사유로 인정되지 않습니다: Rate limit / API 과부하 / 세션 재개·컨텍스트 압축·이전 세션 미완료 / "작은 수정"·"한 줄 fix"·"명백한 오타 교정" / 에이전트 spawn 비용 절감 / 사용자 압박감 / 에이전트 응답 지연. 유일한 허용 경로: pm-planner-agent → pipeline-manager-agent → dev-agent(또는 ui-app-agent) spawn.
- **[버그 수정도 전체 파이프라인 필수]** 사용자의 버그 수정 또는 개선 요청은 신규 기능과 동일하게 PM → Dev → QA → (SEC) → Build → Harness 전 단계를 통과해야 합니다. "작은 수정"이라도 예외 없습니다.
- **[Rate Limit 대기 의무]** 에이전트가 rate limit으로 실패하면 main context가 대신 수행하는 것은 절대 금지입니다. 사용자에게 상황을 보고하고 rate limit 해제 후 재시도합니다.
- **[Phase 6→7 자동 진행]** Build 완료와 Build phase attestation 후 Phase 7 외부 게이트는 중간 사용자 확인 없이 자동 진행합니다. Phase 7은 배포 결정이 아니라 Technical/Oracle/GitHub CI/Acceptance 준비 검증입니다. 사용자에게 묻는 지점은 마지막 `gates accept`의 ACCEPT/REJECT 한 번뿐입니다.
- **[Phase 7 생략 절대 금지]** Build SUCCESS 후 외부 게이트 없이 "완료"를 선언할 수 없습니다. 외부 게이트 결과가 없으면 파이프라인은 미완성입니다.
- **[Phase 8 생략 절대 금지]** Phase 7 완료 후 prompt-architect-agent RCA 없이 다음 사용자 요청으로 넘어갈 수 없습니다.
- **[분석은 main context 허용]** 코드 읽기(Read), 검색(Grep/Glob), 원인 조사는 main context가 직접 수행할 수 있습니다. 수정만 금지입니다.

### 요청 분류 — 파이프라인 우회 가능 동작 화이트리스트 (Strict)

**화이트리스트 (파이프라인 우회 허용 — 아래 5개만):**
1. 파일/코드 읽기 (Read, Grep, Glob) — 단, **로그/런타임 출력/test_results.jsonl 분석은** pm-agent.md `## Log/Result Analysis 5-Layer Checklist` (BUG-20260506-B6A2) 5-Layer 전부 적용 의무. ERROR-only 분석은 부분 분석으로 간주, Layer 3(성공 처리 패턴, overwrite 검증)·Layer 5(소급) 누락 시 분석 결과 무효.
2. 파이프라인 상태 조회 (`python pipeline.py status`, `check --phase ...`)
3. 방법/접근법 비교·설명 (코드 변경 없는 순수 텍스트 응답)
4. 사용자 질문에 대한 답변 (코드 미변경)
5. clarification_request / script_request 발행

**블랙리스트 (파이프라인 필수):**
- 모든 코드 파일(.py/.js/.ts/.json 등) Edit/Write/Bash 수정
- CLAUDE.md 및 `.claude/agents/*.md` 수정 (별도 IMP 파이프라인 필수; Phase 9 성격 작업)
- `pipeline.py` 수정 (별도 파이프라인 필수)
- 설정 파일(`settings.json`, `requirements.txt` 등) 수정
- 파일 생성·삭제·이동 (read-only 분석은 제외)

**회색지대 처리 원칙:** 화이트리스트에 명시되지 않은 동작은 무조건 블랙리스트로 분류. 의심 시 사용자에게 확인 후 진행.

**위반 시 자동 차단 문구:**
`[PIPELINE GATE] 이 작업은 화이트리스트 외 동작입니다. PM → Dev 파이프라인을 시작합니다.`

### 분석(허용) vs 수정(금지) 경계 — 케이스별 판정표

| 동작 | 분류 | 허용 여부 | 비고 |
|---|---|---|---|
| 코드 파일 Read | 분석 | 허용 | 라인 수 제한 없음 |
| 코드 파일 Grep/Glob | 분석 | 허용 | 패턴 검색 |
| "이 함수는 어떻게 동작하나요?" 답변 | 분석 | 허용 | 텍스트 응답만 |
| "X와 Y 중 어느 방법이 좋나요?" 비교 | 분석 | 허용 | 권고만, 적용 금지 |
| `python pipeline.py status/check` | 상태조회 | 허용 | 읽기 전용 명령 |
| `python pipeline.py new` | 상태기록 | 허용 (오케스트레이터) | 새 파이프라인 시작 전용 |
| `python pipeline.py done/qa/sec/build/harness/architect` | 상태기록 | **금지** (오케스트레이터) | **Pipeline Manager 기록 단계 전용** — 오케스트레이터가 직접 호출 시 이중 기록 발생 |
| `python pipeline.py contract add-module/add-test/add-oracle/audit/ready/freeze` | 계약/상태기록 | **금지** (오케스트레이터) | Pipeline Manager 전용. 오케스트레이터는 `contract init`도 PM에게 위임 |
| `python pipeline.py acceptance/gates/advisory` | 외부 게이트/리뷰 기록 | **금지** (오케스트레이터) | Pipeline Manager/test-harness/prompt-architect 책임 경로. 오케스트레이터 직접 호출 금지 |
| 코드 파일 Edit/Write | 수정 | 금지 | dev-agent 필수 |
| CLAUDE.md / `.claude/agents/*.md` Edit | 수정 | 금지 | 별도 Protocol Evolution IMP 파이프라인 |
| `pipeline.py` Edit | 수정 | 금지 | 별도 파이프라인 |
| Bash로 파일 생성/삭제/이동 (코드/설정) | 수정 | 금지 | dev-agent 필수 |
| 임시 에이전트 MD 파일 삭제 (`rm .claude/agents/*_temp_*.md`) | 수정(예외) | 허용 | 임시 에이전트 Cleanup 규칙 명시 예외 |
| `python pipeline.py status` 결과 파싱 | 분석 | 허용 | 읽기 |

- 분석은 무제한 허용되며 토큰/시간 제약을 이유로 분석을 생략하고 수정으로 직행하는 것은 금지된다.
- 회색지대 발견 시 사용자에게 1회 확인 요청 후 명시적 승인이 있을 때만 진행.

---

## Pipeline Enforcer 연동 (pipeline.py 기술적 게이트)

`pipeline.py`는 파이프라인 상태를 `pipeline_state.json`에 기록하고 exit code 1로 진입을 물리적으로 차단합니다.
**텍스트 규칙만으로는 우회 가능하므로, 아래 명령어 실행이 각 Phase 진입의 필수 조건입니다.**

### Pipeline Manager 필수 절차 (Phase별 gate 명령어) — Phase 1 진입 명령(`pipeline.py new`)만 오케스트레이터가 호출, 나머지는 Pipeline Manager 기록 단계가 호출

| Phase | 호출 주체 | 진입 전 (check) | 완료 후 (record) |
|---|---|---|---|
| Phase 1 — PM | 오케스트레이터는 `new`와 PM Planner/Manager 호출까지만 수행, PM 완료 기록은 Pipeline Manager 기록 단계가 호출 | `python pipeline.py new --type FEAT --desc "..."` | `python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --planner-run-id <planner_run_id> --manager-run-id <manager_run_id> --manager-report manager_handoff.xml` + phase attestation |
| Phase 2 — Dev | Pipeline Manager 기록 단계 | `python pipeline.py check --phase dev` (exit 0 확인) | `module design/dev/qa` per `MT-N` + `module integrate` 후 `python pipeline.py done --phase dev --files "파일목록" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>` + phase attestation |
| Phase 3 — UI/dev subphase | Pipeline Manager 기록 단계 | pipeline.py phase 없음 (ui-app-agent 자체 gate) | ui-app-agent의 app.py를 Phase 2 done --files에 포함하거나 QA 증거로 처리 |
| Phase 4 — QA | Pipeline Manager 기록 단계 | `python pipeline.py check --phase qa` (exit 0 확인) | `python pipeline.py qa --result PASS --numeric-score N --report-file qa_report.xml --agent-run-id <qa_run_id>` 또는 `--result FAIL --numeric-score N --failure-sig "[category]:[hash]" --report-file qa_report.xml --agent-run-id <qa_run_id>` + phase attestation |
| Phase 5 — SEC | Pipeline Manager 기록 단계 | `python pipeline.py check --phase sec` (exit 0 확인) | SAFE: `python pipeline.py sec --result PASS --risk LOW` / BLOCK: `--result BLOCK --risk HIGH` / FAIL: `python pipeline.py sec --result FAIL --risk MEDIUM` (dev PENDING 리셋, 스냅샷 미기록 — 의도적 설계) / 미해당: `python pipeline.py sec --skip` |
| Phase 6 — Build | Pipeline Manager 기록 단계 | `python pipeline.py check --phase build` (exit 0 확인) | `python pipeline.py build --exe "dist/앱이름.exe" --report-file dist/build_report.xml --agent-run-id <build_run_id>` 또는 `--exe "N/A" --skip-reason "meta-task" --agent-run-id <build_run_id>` + phase attestation |
| Phase 7 — External Gates | Pipeline Manager 기록 단계, 마지막 ACCEPT/REJECT만 사용자 확인 후 기록 | `python pipeline.py gates status` | `python pipeline.py gates technical`; `python pipeline.py gates oracle`; `python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline`; `python pipeline.py gates request-accept --evidence [경로]` → 사용자 승인 코드 수신 후 → `python pipeline.py gates accept --result ACCEPT --evidence [경로] --acceptance-code ACCEPT-<pid>-<nonce>` |
| Phase 8 — Architect | Pipeline Manager 기록 단계 | `python pipeline.py check --phase architect` (exit 0 확인) | `python pipeline.py architect --report-file architect_report.xml` |

Phase 5가 보안 범위가 아니면 Pipeline Manager 기록 단계가 `python pipeline.py check --phase sec` exit 0을 확인한 뒤 `python pipeline.py sec --skip`을 실행한다. 오케스트레이터와 security-agent는 `sec --skip`을 직접 기록하지 않는다.

**exit code 1 = BLOCKED** → 해당 에이전트를 spawn하지 않고 `python pipeline.py status`로 원인 확인 후 선행 단계를 먼저 완료합니다.

**새 파이프라인 시작 시 반드시:**
```
python pipeline.py new --type FEAT|BUG|IMP --desc "태스크 설명"
```

**현재 상태 확인:**
```
python pipeline.py status
```

---

## 컨텍스트 주입 규칙 (PM Agent 주관, 토큰 절감)

PM Agent가 sub-agent를 spawn할 때 반드시 아래 컨텍스트를 prompt에 포함하여, 에이전트가 탐색적 Read 호출을 최소화하도록 합니다.

### 필수 포함 항목
1. **대상 파일 절대 경로 목록** — 에이전트가 Read해야 할 파일을 명시. 목록 외 파일은 Read 금지.
2. **해당 Phase의 규칙 발췌** — CLAUDE.md 전체를 Read하는 대신, 오케스트레이터가 해당 Phase에 관련된 조항만 발췌하여 전달.
3. **이전 Phase 결과 요약** — QA verdict, external gate 상태, module gate 상태, SEC risk 등을 한 줄 요약으로 전달.

### 금지 사항
- 에이전트에게 "CLAUDE.md를 읽어라"고 지시하지 않음 — 필요한 내용은 오케스트레이터가 발췌하여 주입.
- 에이전트에게 다른 에이전트의 MD 파일을 Read하도록 지시하지 않음 — 필요 정보는 오케스트레이터가 요약하여 전달.
- prompt-architect-agent를 Phase 8에서 호출할 때도 대상 파일 목록을 명시적으로 제공. 탐색적 Glob 호출 최소화.

### Phase 8/9 재검증 의무
Phase 8(Architect)에서 에이전트 MD, `CLAUDE.md`, `pipeline.py`, workflow, oracle/test surface를 수정한 경우, **반드시 영향을 받는 phase attestation과 Phase 7 외부 게이트를 다시 실행**하여 개선 효과를 검증해야 합니다.
- 외부 게이트 재검증 없이 `pipeline.py architect`로 COMPLETE 선언 금지.
- 문구-only 변경처럼 실행 산출물 영향이 없는 경우에도 GitHub CI gate와 User Acceptance evidence는 유지되어야 합니다.

---

## 핵심 운영 규칙
- **[배치 처리 절대 금지]** 한 번의 파이프라인 사이클(Phase 1→7)은 정확히 **1개 태스크**만 처리합니다. PM이 여러 스텝을 동시에 발행하거나, Dev/QA/Harness가 여러 태스크를 한꺼번에 처리하는 것은 게이트키핑을 무효화하는 시스템 위반입니다.
- **[증거 없는 검증 금지]** QA, Harness diagnostic, Architect는 실제 산출물 또는 실제 변경 파일이 제출된 이후에만 검증합니다. `<handover><evidence>` 태그와 phase receipt에 실제 파일명이 명시되어 있어야 합니다. (단, .py 파일 없는 meta-task는 MD 파일과 GitHub CI 첨부파일을 증거로 인정)
- **[인계 계약 필수]** Dev 에이전트는 작업 완료 후 반드시 `<handover>` XML로 QA에게 인계합니다. 인계 없이 QA가 검증을 시작하면 QA는 거부 메시지를 출력하고 중단합니다.
- **[remediation_code 의무]** Security 에이전트의 모든 `<finding>`에는 `<remediation_code>` CDATA 블록이 포함되어야 합니다. 서술형 수정 지시는 허용하지 않습니다.
- **[Legacy harness diagnostic only]** 예전 `<test_code>` 기반 harness evidence는 오래된 회귀 테스트와 진단용으로만 유지됩니다. 신규 `/Task` 완료 판정은 Technical, Oracle, GitHub CI, User Acceptance gate와 phase/module attestation으로만 이루어집니다.
- **[BUILD REPORT XML 전용]** Build 에이전트의 출력은 반드시 `<build_report>` XML 형식(section_1~section_6 포함)이어야 합니다. 구 텍스트 형식은 폐기되었습니다.
- **[임시 에이전트 Cleanup 의무]** Dynamic Pipeline Enhancement로 생성된 임시 에이전트는 반드시 아래 절차로 삭제합니다:
  1. **삭제 트리거**: `pipeline.py architect`가 terminal COMPLETE를 기록한 직후 또는 파이프라인 FAIL 확정 직후 (먼저 발생하는 쪽)
  2. **삭제 명령** (오케스트레이터 Bash 직접 실행 허용 — MD 파일은 코드가 아님):
     ```bash
     rm ".claude/agents/[agent_filename]"
     ls .claude/agents/ | grep _temp_   # 공백 출력이어야 함
     ```
  3. **실패 처리**: 파일 미존재 → 정상으로 간주. 권한 오류 → 사용자에게 수동 삭제 요청 후 계속 진행.
  4. **보존 원칙**: CLAUDE.md 및 기존 에이전트 MD 파일은 절대 수정하지 않음.

## Dynamic Pipeline Enhancement

PM agent는 사용자 업무 수신 시 3-AND 복잡도 조건(도메인 공백 + 최신성 요구 + 결과물 차이 10점+)을 판정합니다. 세 조건 모두 충족 시:

1. **WebSearch 리서치** (최대 3회): Apache-2.0/MIT 라이선스 + PyPI 등록 라이브러리 대상
2. **임시 전문 에이전트 생성**: `agent-factory-agent`를 통해 `.claude/agents/[기능명]_specialist_temp_[pipeline_id].md` 생성. protocol-evolution-agent 비호출.
3. **파이프라인 삽입**: Phase 2~5 사이 임의 위치에 삽입. Phase 1 직후(insert_after_phase=0)와 Phase 6 이후 삽입 금지.
4. **Cleanup**: 파이프라인 종료 시 임시 에이전트 MD 파일 삭제. 다음 파이프라인은 기존 구조 그대로 사용.

조건 불충족 시: 필수 파이프라인(PM→Module-gated Dev→QA→SEC→Build→External Gates→Architect)을 그대로 실행.

## Meta-Task Two-Round 방지 규칙

별도 Protocol Evolution IMP에서 에이전트 MD 파일 편집(meta-task) 수행 시:

1. **Single-Round 원칙:** 한 번의 Protocol Evolution IMP는 1라운드만 허용. 2라운드 필요 시 architect MD의 contract validation 누락이 근본 원인 → architect MD 재패치로 다음 사이클부터 차단.
2. **Producer-Consumer 묶음 패치 의무:** producer(dev/ui/qa/security/build/harness)에 신규 규칙 추가 시, consumer 문서(`CLAUDE.md`, command skill, QA/Architect 검증 규칙, `pipeline.py` gate)도 동일 사이클에서 함께 패치. 분리 패치 금지.
3. **완료 판정 통일:** Protocol Evolution IMP도 숫자 점수가 아니라 Three-Gate + Option A + module gate로 완료됩니다. `test_results.jsonl`은 과거 로그 분석용일 뿐 신규 COMPLETE 조건이 아닙니다.
4. **contract_audit 블록 게이트:** Protocol Evolution IMP의 architect 출력에 `<contract_audit>` 블록이 누락되어 있으면 기록을 차단하고 architect를 재spawn.
5. **strict 모드 evidence 스키마 의무:** strict 모드 진입 시(직전 3개 META-FS-PD 사이클 중 ≥2개 100점) architect는 `<optimization_report>` 출력에 `evidence_lines` 객체를 4개 필드(section_completeness/producer_consumer_sync/backward_compatibility/role_transition_clarity) 모두 채워서 포함해야 한다. Pipeline Manager는 architect 출력의 `<contract_audit>` 검증 시 `evidence_lines` 필드도 함께 확인. 4개 필드 중 1개라도 누락 시 별도 IMP 기록 차단 + architect 재spawn.
6. **레거시 로그 인코딩 의무:** 과거 `test_results.jsonl`을 읽거나 보존할 때는 utf-8 + `ensure_ascii=False`를 유지합니다. 신규 완료 판정에는 사용하지 않습니다.

## Tournament Mode (Multi-Branch Architecture Comparison)

여러 구현 방식을 병렬로 실행하여 외부 게이트 통과 여부와 사용자에게 보이는 결과물 기준으로 최종 선택하는 모드입니다. 토너먼트도 Three-Gate + Option A + module gate를 생략할 수 없습니다.

### 트리거 조건
PM Agent가 아래 조건 **모두** 충족 시 AskUserQuestion으로 토너먼트 모드를 제안합니다:
1. 사용자 요청에 진정한 아키텍처 분기점 존재 (예: GUI 프레임워크, DB 선택, 알고리즘 선택)
2. 각 옵션의 구현 비용이 유사함 (한 옵션이 명백히 더 복잡하면 단일 추천)
3. 사용자가 복수 옵션 선택 (AskUserQuestion 응답에 "+" 포함)

### 실행 절차

1. **PM이 옵션 제시:** AskUserQuestion으로 복수 선택 가능한 옵션 제시
   - options 예시: `["A만 (tkinter)", "B만 (PyQt5)", "A+B 병렬 비교", "토너먼트 사용 안 함"]`
2. **토너먼트 초기화:** `python pipeline.py tournament-start --pipeline-id [ID] --branches A,B`
3. **병렬 브랜치 실행:** PM이 각 브랜치에 대해 dev-agent를 `run_in_background=True`로 spawn
   - 각 브랜치는 독립적으로 Module-gated Dev→QA→SEC→Build→External Gates 수행
   - 모든 pipeline.py 명령에 `--branch [A|B|...]` 옵션 추가
4. **결과 비교:** 모든 브랜치의 external gate 상태와 첨부파일/결과물 요약을 `python pipeline.py tournament-rank --pipeline-id [ID]`로 확인
5. **승자 확정:** `python pipeline.py tournament-finalize --pipeline-id [ID] --winner [브랜치]`
6. **결과 보고:** 승자 브랜치의 결과물 경로, PR/Actions 링크, 변경 요약, 주요 특징을 사용자에게 제시

### 승자 결정 규칙
- Build FAIL 브랜치는 자동 탈락 (ELIMINATED)
- 남은 브랜치 중 모든 external gate가 PASS이고 사용자 요구에 가장 가까운 결과물이 승자 후보
- 복수 후보가 있으면 PM이 결과물 비교표를 제시하고 사용자에게 최종 선택 위임

### 패자 처리
모든 브랜치 결과는 `pipeline_history/`에 보관됩니다. 승자 발표 후 패자 브랜치도 참조 가능합니다.

### pipeline.py 토너먼트 명령어

| 명령어 | 용도 |
|---|---|
| `tournament-start --pipeline-id ID --branches A,B` | 토너먼트 초기화, 브랜치 state 파일 생성 |
| `tournament-status --pipeline-id ID` | 전체 브랜치 진행 현황 |
| `tournament-rank --pipeline-id ID` | 브랜치별 external gate와 첨부파일/결과물 비교 표 |
| `tournament-finalize --pipeline-id ID --winner A` | 승자 확정 + 모든 브랜치 history 보관 |

---

## Auto-Resume on Session Start (세션 시작 시 자동 복구)

신규 명령어 추가 없이 CLAUDE.md 규칙으로만 동작하는 자동 파이프라인 복구 메커니즘입니다. 세션 압축, 세션 재개, 새 prompt 시작 등 모든 세션 전환 이벤트 직후에 활성화됩니다.

### 자동 실행 절차 (모든 새 세션의 첫 사용자 턴에서 의무)

1. **첫 액션 = status 자동 조회**
   오케스트레이터(main context)는 모든 새 세션의 첫 사용자 턴에서 다른 어떤 분석/응답보다 먼저 다음 명령을 실행합니다:
   ```bash
   python pipeline.py status
   ```
   사용자가 명시적으로 호출하지 않아도 자동 실행. 이 자동 호출은 v2.2 Main Context 코드 수정 금지 규칙과 무관합니다 (status는 read-only).

2. **미완료 파이프라인 감지 조건**
   status 출력에서 아래 모두 충족 시 미완료로 판정:
   - 활성 파이프라인 존재 (`활성 파이프라인 없음` 메시지가 아님)
   - `current_phase` 가 `COMPLETE` 가 아님
   - `terminal_state` 필드가 `null` (즉, `COMPLETE`/`FAILED`/`TERMINATED` 가 아님)

3. **자동 재개 (사용자 입력 불필요)**
   미완료 파이프라인 감지 시 사용자에게 추가 질문 없이 즉시 해당 Phase 재개:
   - 다음 진입 Phase = `current_phase`
   - 해당 Phase의 에이전트를 Pipeline Manager Agent가 직접 spawn (오케스트레이터는 downstream spawn 권한 없음). **재개 시 prompt에 `pipeline_id: [ID]`, `current_phase: [N]`, `resume: true`, 기존 `step_plan_sha256` 포함 필수 — Planner 재실행은 금지.**
   - 재개 시 사용자에게 한 줄 통지: `[Auto-Resume] 미완료 파이프라인 [pipeline_id] 감지 — Phase N부터 재개합니다.`

4. **미완료 파이프라인 없음 → 정상 처리**
   status 결과가 `활성 파이프라인 없음` 이거나 `current_phase=COMPLETE` 이면 status 결과를 무시하고 사용자 요청을 평소대로 처리.

5. **v2.2 보강 — 직접 편집 금지 그대로 유지**
   세션 재개 후에도 오케스트레이터의 코드 직접 편집은 금지입니다. Auto-Resume 은 "에이전트 spawn으로 재개" 만 자동화하며, "Main Context가 마무리한다"는 우회 경로를 열지 않습니다.

### 사용자 통지 양식

```
[Auto-Resume] 미완료 파이프라인 [IMP-YYYYMMDD-XXXX] 감지 — Phase [N] ([Phase 이름])부터 재개합니다.
```

추가 설명/확인 질문 금지 — 한 줄 통지 후 바로 Pipeline Manager Agent resume/spawn.

---

## Tier Realignment (모델 티어 재배치)

설계 품질을 좌우하는 PM Planner와 코드 결정 품질을 좌우하는 Dev/QA는 Opus로 둔다. 순서 관리 중심인 Pipeline Manager는 Sonnet으로 충분하다. Protocol Evolution은 신뢰 루트 파일을 수정하므로 Opus로 상향한다.

### 현재 티어 매핑 (frontmatter `model:` 필드와 일치)

| Agent | 현재 티어 | 근거 |
|---|---|---|
| pm-planner-agent | **opus** | 질문, oracle, micro-task 설계 품질이 전체 결과를 좌우 |
| pipeline-manager-agent | **sonnet** | 순서 관리, receipt 확인, gate 기록 중심 |
| pm-agent | **sonnet** | 호환성 문서. 새 작업은 planner/manager 분리 사용 |
| dev-agent | **opus** | 코드 결정 품질과 oracle 통과 가능성을 좌우 |
| qa-agent | **opus** | 7개 카테고리 자동 FAIL 정확도 핵심 |
| ui-app-agent | **sonnet** | 위젯 네이밍/Threading 정확성 요구 |
| prompt-architect-agent | **opus** | RCA 정밀도 유지 |
| security-agent | **sonnet** | 보안 감사 전담 |
| build-agent | **haiku** | PyInstaller 패키징 전용 |
| test-harness-agent | **sonnet** | Phase 7 외부 게이트 readiness와 사용자 확인 패킷 진단 |
| agent-factory-agent | **sonnet** | 에이전트 설계 전용 |
| protocol-evolution-agent | **opus** | pipeline.py/CLAUDE.md/agent MD 같은 신뢰 루트 동기화 |

각 agent MD의 frontmatter `model:` 필드와 본문 `**Tier:** ...` 헤더는 위 표와 정확히 일치해야 합니다. 불일치 발견 시 prompt-architect-agent가 `<contract_audit>`에 `TIER_MISMATCH`로 기록하고 별도 Protocol Evolution IMP의 패치 대상에 포함합니다.

---

## Circuit Breaker Protocol (동일 오류 2회 FAIL 시 Architect 즉시 이관)

QA가 같은 실패 시그니처로 2회 연속 FAIL을 반환하면, PM은 dev-agent를 3회째 재spawn하지 않고 prompt-architect-agent로 즉시 이관합니다. dev 추가 시도가 실패를 해결하지 못하는 상황은 **구조적 문제**이며 코드 수정이 아닌 **전략적 재설계**가 필요합니다.

### 발동 조건 (3-AND)

1. Phase 4 QA Round n=1 verdict = FAIL
2. Phase 4 QA Round n=2 verdict = FAIL
3. Round 1과 Round 2의 `<failure_signature>` 가 동일 (category 일치 AND hash 일치)

### 발동 시 즉시 절차

1. **dev-agent 3회 재spawn 금지** — 시도 시 프로토콜 위반
2. **`python pipeline.py qa --result FAIL` 마지막 기록 그대로 유지** (재기록 없음)
3. **Phase 8 즉시 진입** — prompt-architect-agent를 `circuit_breaker_handoff` 컨텍스트와 함께 spawn
4. **Architect는 Strategic Restructuring Report 모드로 응답** (`<strategic_restructuring_report>` XML 출력)
5. **PM이 사용자에게 AskUserQuestion** — 3개 pivot 옵션 제시 후 선택
6. **사용자 선택 후 Phase 1부터 새 step_plan으로 재시작** 또는 PAUSED

### 책임자별 역할

| 책임자 | 역할 |
|---|---|
| qa-agent | FAIL 시 `<failure_signature>` + `<repeat_indicator>FIRST</repeat_indicator>` 출력 (qa-agent.md `## Circuit Breaker Signal Output`) |
| pipeline.py + Pipeline Manager | `qa_fail_history` 추적 + RECURRING 판정 확인 + Architect 이관. `pm-agent.md`의 Circuit Breaker 섹션은 호환성 참고문서로만 사용 |
| prompt-architect-agent | circuit_breaker_handoff 수신 시 Strategic Restructuring Report 출력 (prompt-architect-agent.md `## Circuit Breaker Intake Mode`) |
| 사용자 | 3개 옵션 중 선택 또는 중단 |

### Protocol Evolution IMP 비자동화

Circuit Breaker로 진입한 Phase 8은 일반 RCA가 아닌 Strategic Restructuring 모드이며, **Protocol Evolution IMP를 자동 시작하지 않습니다**. 이 사이클의 결과는 `pipeline.py architect --report-file architect_report.xml` 미기록 상태로 PAUSED 또는 새 파이프라인으로 전환됩니다.

---

## Micro-task Decomposition (단일 함수/위젯 단위 변경 격리)

PM step_plan은 변경 범위를 단일 함수 또는 단일 UI 위젯 단위로 쪼개야 하며, dev-agent와 ui-app-agent는 코드 작성 전 `<impact_analysis>` XML을 필수 출력합니다. QA는 실제 diff와 `<impact_analysis>` 의 일치 여부를 검증합니다.

이 규칙은 문서 지침이 아니라 `pipeline.py` hard gate입니다. PM `done`은 모든 파이프라인에서 `step_plan.xml` 안의 `<decomposition_audit>`/`<design_confirmation>`/`<micro_tasks>`를 파싱해 원자 단위 계획과 사용자 설계 확인 기록을 `pipeline_state.json`에 기록합니다. Dev `done`도 모든 파이프라인에서 `dev_handover.xml`과 `scope_manifest.json`이 그 계획의 id, 파일, 함수 범위를 정확히 따를 때만 통과합니다.

### PM 설계 확인 질문 품질

PM은 모듈 분해 방식이 1개뿐이어도 Dev 진입 전에 사용자에게 보여주고 확인받습니다. 질문은 코드 용어가 아니라 사용자가 이해할 수 있는 쉬운 한국어로 작성하며, 각 질문은 아래를 포함해야 합니다:

- 무엇을 선택하는지
- 왜 중요한지
- PM 추천안
- 최소 2개 옵션
- 각 옵션의 장점과 단점
- 사용자 답변

P2 수준의 내부 구현 취향 질문(변수명, 코드 스타일, 사소한 함수명 등)은 사용자에게 묻지 않습니다. PM은 이를 `low_value_questions_filtered=true`와 `filter_summary`로 기록하고, 유지보수성 우선 원칙으로 처리합니다. 유지보수성은 항상 빠른 수정이나 최소 수정보다 우선합니다.

### 계층 구조 (Frozen Codebase와 상호 보완)

| 원칙 | 경계 | 적용 대상 | 위반 시 |
|---|---|---|---|
| Frozen Codebase Principle (IMP-C0FC) | **파일 수준** | scope_declaration `<files_to_modify>` | EXCEEDS_SCOPE FAIL |
| **Micro-task Surgical Edit** | **함수/위젯 수준** | impact_analysis `<target_function>` | EXCEEDS_SCOPE FAIL |

두 원칙은 외부(파일) / 내부(함수) 경계로 상호 보완되며 동시에 적용됩니다.

### PM step_plan 의무 필드

```xml
<micro_tasks>
  <micro_task id="MT-1">
    <affected_function>module.path.function_name</affected_function>
    <target_files>
      <file>core/module.py</file>
    </target_files>
    <affected_call_sites>
      <site>module.path.caller1</site>
    </affected_call_sites>
    <grep_evidence>
      <pattern>[실제 Grep 실행 패턴]</pattern>
      <match_count>[숫자]</match_count>
      <executed>true</executed>
    </grep_evidence>
    <change_summary>[1줄]</change_summary>
    <line_estimate>[N]</line_estimate>
  </micro_task>
</micro_tasks>
```

PM은 `<affected_call_sites>` 를 Grep으로 사전 조사한 결과로 채웁니다. 빈 목록은 `<site>none</site>` 으로 명시.

PM은 `<grep_evidence>` 서브태그에 실행 패턴과 match count를 반드시 포함합니다. `<executed>true</executed>` 없이 `<affected_call_sites>`를 제출하면 QA가 PD FAIL 처리합니다.

PM은 step_plan 발행 전 `<decomposition_audit>` 블록을 반드시 출력합니다. 누락 시 QA가 `PD:decomposition_audit_missing` failure_signature로 자동 FAIL 처리합니다.

### Dev/UI agent 의무 출력

dev-agent / ui-app-agent 는 `<scope_declaration>` 직후 `<impact_analysis>` 블록을 출력합니다 (각 agent MD `## Micro-task Surgical Edit` 섹션 참조). 누락 시 QA가 `<failure_signature>PD:impact_analysis_missing</failure_signature>` 또는 `UI:impact_analysis_missing` 으로 자동 FAIL.

모든 파이프라인에서 Dev는 handover와 별도로 아래 `scope_manifest.json`을 생성해야 합니다. `files`는 `pipeline.py done --phase dev --files ...`와 일치해야 하며, 모든 값은 PM `<micro_tasks>`의 `id`, `<target_files>`, `<affected_function>` 범위 안에 있어야 합니다.

```json
{
  "pipeline_id": "FEAT-YYYYMMDD-XXXX",
  "micro_tasks": [
    {
      "id": "MT-1",
      "files": ["core/module.py"],
      "affected_functions": ["module.path.function_name"]
    }
  ]
}
```

### QA Boundary Verification

qa-agent 는 `<scope_declaration>` + `<impact_analysis>` 와 실제 diff를 Grep으로 비교하여 `<micro_task_boundary>` 블록을 출력합니다 (qa-agent.md `## Micro-task Boundary Verification`). 범위 초과 발견 시 `<verdict>EXCEEDS_SCOPE</verdict>` + `<blast_radius>EXCEEDS_SCOPE</blast_radius>` 로 자동 FAIL.

### 분할 의무 케이스

PM은 아래 케이스에서 step_plan을 micro_task 단위로 분할합니다:

- 변경 함수 ≥3개 → 함수별 step_plan 분할
- 변경 파일 ≥3개 (사용자가 명시한 동기화 묶음 또는 project profile로 검증된 예외 제외) → 파일별 분할
- 단일 함수 내 변경 라인 >100 → 책임 단위로 분할

### 기존 규칙과의 호환성

본 Micro-task Decomposition 섹션은 기존 규칙(Frozen Codebase, Mandatory Clarification, Compliance Checklist, Robustness Sub-Rubric, Pre-Output Self-Check, Producer-Consumer Bundle Patch Verification 등)을 무손상으로 보존합니다. 신규 규칙은 모두 별도 헤더로 추가되며, 기존 섹션 텍스트는 변경하지 않습니다.

---

## Patch Lane + Incident Cluster (IMP-20260513-4C0B)

파이프라인 규모가 작고 위험 요소가 없는 경우 Full Lane 대신 경량 Patch Lane을 사용합니다.
Incident Cluster는 관련 파이프라인들을 그룹화하여 반복 실패 패턴을 추적합니다.

### Patch Lane 진입 조건 (5-AND)

아래 **모두** 충족 시에만 Patch Lane 사용 가능합니다:

1. 변경 파일 수 = 1개
2. 변경 함수 수 = 1개
3. 예상 변경 줄 수 ≤ **15줄** (auto-escalation 임계값)
4. 신뢰 루트 파일(CLAUDE.md/pipeline.py/agent MD/ci.yml) 수정 없음 (`trust_root_changes=false`)
5. 새 의존성 추가 없음, 파일 이동/삭제 없음

하나라도 위반 시 → 자동으로 Full Lane으로 에스컬레이션됩니다 (`exit code 1`, `lane=full`).

### Patch Lane 운영 절차

```powershell
# 1. patch_plan.json 작성 후 진입 조건 검사
python pipeline.py patch plan --plan patch_plan.json

# 2. 패치 감사 (auto-escalation 검사)
python pipeline.py patch audit --plan patch_plan.json

# 3. 패치 적용 후 검증
python pipeline.py patch verify --plan patch_plan.json --result PASS --test-command "python -m pytest tests/test_patch_lane.py -q"
# 또는
python pipeline.py patch verify --plan patch_plan.json --result PASS --evidence-file verify_result.txt

# 4. 완료 증거 기록
python pipeline.py patch attest --plan patch_plan.json
```

### patch_plan.json 스키마

```json
{
  "schema_version": 1,
  "pipeline_id": "FEAT|BUG|IMP-YYYYMMDD-XXXX",
  "patch_scope": {
    "file": "단일파일.py",
    "function": "단일함수명",
    "expected_lines_changed_max": 10
  },
  "forbidden": {
    "trust_root_changes": false,
    "new_dependencies": false,
    "file_move_or_delete": false,
    "packaging_changes": false
  }
}
```

### Incident Cluster 운영 절차

```powershell
# 유사 클러스터 탐색
python pipeline.py cluster detect --desc "키워드"

# 새 클러스터 생성
python pipeline.py cluster init --desc "클러스터 설명"

# 현재 파이프라인 연결
python pipeline.py cluster attach --cluster-id CL-XXXX

# 클러스터 상태 조회
python pipeline.py cluster status [--cluster-id CL-XXXX]

# 클러스터 종료
python pipeline.py cluster close --cluster-id CL-XXXX
```

### Cluster 자동 규칙

- `patch_failures >= 2` → `patch_lane_forbidden=true` 자동 설정 — 이후 Patch Lane 사용 불가
- `created_at` 으로부터 7일 경과 → `cluster status` / `cluster detect` 실행 시 자동 close
- `closed_at != null` 인 클러스터는 `detect` 결과에서 제외

### Pipeline Manager 책임

Pipeline Manager는 Patch Lane 작업에도 아래를 수행합니다:

- `patch plan` FAIL(exit code 1) 시 → Full Lane으로 전환하고 PM에게 새 step_plan 요청
- `patch audit` ESCALATE 시 → `python pipeline.py patch audit` exit code 1 → Full Lane 전환
- `patch verify --result FAIL` 시 → `cluster patch_failures` 증가, 임계값(2) 초과 시 `patch_lane_forbidden=true`
- Patch Lane 완료 증거는 `patch attest` 로 기록하고 GitHub CI에 포함

### QA — Patch Lane 검증 의무

QA는 Patch Lane 태스크에서 아래를 추가로 검증합니다:

1. `patch_plan.json`의 `schema_version=1` 확인
2. `patch_scope.file`이 실제 변경된 파일과 일치하는지 확인
3. `patch_scope.expected_lines_changed_max <= 15` 확인
4. `forbidden.*` 필드가 모두 `false`인지 확인
5. `patch attest` 출력 JSON에 `attested_at` 타임스탬프 존재 확인

---

## Scope Lock 강제 규칙 (IMP-20260516-77AA)

PM `step_plan.xml`의 `<target_files>` 목록과 Dev가 실제로 변경한 파일(git diff --name-only) 사이에 불일치가 있을 경우, **Dev done/PR 생성을 차단**합니다. 이 규칙은 IMP-20260516-E881의 사후 REJECT(ci.yml이 PM scope 외 파일로 PR에 포함된 사건)를 방지하기 위해 도입되었습니다.

### 보호 대상 파일 목록 (Trust-Root Files)

아래 파일/경로는 별도 IMP 파이프라인 + CODEOWNERS 승인 없이 어떠한 PR에도 포함될 수 없습니다:

- `.github/workflows/**` (GitHub Actions 워크플로우)
- `pipeline.py` (파이프라인 엔진)
- `CLAUDE.md` (최상위 프로토콜 문서)
- `tests/**` (테스트 및 오라클 파일)
- `.claude/agents/*.md` (에이전트 정의 파일)

> **예외:** 현재 파이프라인의 PM `<target_files>`에 명시적으로 포함된 Trust-Root 파일은 해당 파이프라인 PR 내에서 수정 가능합니다. Trust-Root 파일을 대상으로 하는 파이프라인은 `HIGH_RISK` 실행 프로필을 사용해야 합니다.

> **ci.yml fetch-depth 변경 정식 기록:** IMP-20260516-E881에서 `.github/workflows/ci.yml`의 `fetch-depth: 0` 변경은 phase attestation SHA 검증에 필요한 전체 git 이력을 확보하기 위한 것이었습니다. 이 변경은 별도 scope로 기록되지 않았으나, 기능적으로는 phase attestation 인프라의 일부입니다. 이후 ci.yml 변경은 반드시 별도 `IMP` 파이프라인을 통해 PM scope에 명시하고 진행해야 합니다.

### Pipeline Manager Scope Lock 의무

Pipeline Manager는 `module dev` 기록 직전에 반드시 아래를 수행합니다:

1. `git diff --name-only HEAD` 또는 `git status --short` 로 실제 변경 파일 목록을 확인합니다.
2. 변경 파일 목록을 현재 MT-N의 `scope_manifest_MT-N.json`의 `files` 배열과 비교합니다.
3. **scope_manifest에 없는 파일이 변경되어 있으면:**
   - `python pipeline.py module dev` 기록을 하지 않습니다.
   - Dev agent에게 초과 파일을 되돌리도록 지시합니다.
   - 초과 파일이 Trust-Root 파일인 경우, 별도 IMP를 시작하도록 사용자에게 안내합니다.
   - **dev-agent 재spawn 금지** — 같은 dev-agent가 이미 scope를 초과했으므로, 재spawn이 아니라 현재 변경을 되돌린 후 scope 내 변경만 재확인합니다.
4. 모든 변경 파일이 scope_manifest 안에 있으면 정상적으로 `module dev` 기록을 진행합니다.

### Dev Agent Scope Lock 의무

dev-agent는 코드 작성 완료 후 `<handover>` 출력 전에 반드시 스스로 scope 검증을 수행합니다:

```xml
<scope_lock_check>
  <git_diff_files>[git diff --name-only로 확인한 실제 변경 파일 목록]</git_diff_files>
  <manifest_files>[scope_manifest의 files 배열]</manifest_files>
  <extra_files>[manifest에 없는 파일 — 없으면 "없음"]</extra_files>
  <trust_root_violation>[Trust-Root 파일 포함 여부 — true/false]</trust_root_violation>
  <verdict>PASS | FAIL</verdict>
</scope_lock_check>
```

`<extra_files>`가 "없음"이고 `<trust_root_violation>`이 false일 때만 `<verdict>PASS</verdict>`이며, 이 때만 `<handover>` 출력과 Pipeline Manager의 `module dev` 기록이 허용됩니다.


## Real CLI Path E2E Gate Policy (IMP-20260525-6FAC)

내부 함수 테스트만으로 CLI 완료 판정 금지.
핵심 파이프라인 흐름(new / done --phase pm|dev / gates technical|oracle|accept / architect)은
`tests/e2e/test_real_cli_paths.py`의 Real CLI Path E2E 테스트로 검증 필수.
`PIPELINE_STATE_PATH` 격리 + `final_state` assertion 없는 테스트는 CLI 검증으로 인정하지 않는다.

Golden Task CLI(`golden list` / `golden run --task/--all/--smoke`)는
`tests/e2e/test_golden_tasks.py`의 E2E 테스트로 검증한다. (IMP-20260528-0A9E MT-4)

### 근거
- 내부 함수 직접 호출 테스트는 CLI 인자 파싱, 상태 전이, 파일 효과가 실제로 맞는지 보장하지 않는다.
- `subprocess` 기반 E2E 테스트만이 실제 사용자가 치는 CLI 흐름을 검증한다.
- `PIPELINE_STATE_PATH` 환경 변수로 격리된 state 파일을 사용하여 전역 `pipeline_state.json`을 수정하지 않는다.

### 위반 감지
QA/Harness는 `check_cli_evidence_contract.py` 또는 수동 검토로 아래를 확인한다:
- 상태 변경 CLI 호출마다 `PIPELINE_STATE_PATH` 격리 사용 여부
- `final_state` assertion 포함 여부 (stdout-only 검증 금지)
- `subprocess` 기반 실제 CLI 실행 여부 (내부 함수 직접 임포트 금지)


## Security & Secrets Boundary (IMP-20260529-D8BA)

`python pipeline.py gates secrets` — PR diff와 주요 report 파일에서 secret-like 문자열을 자동 검사합니다.

### SSoT Secret Pattern 목록
`SECRET_PATTERNS` 상수는 `pipeline.py`에 정의됩니다 (`WORKSPACE_INTERNAL_PATTERNS` 인근). 8개 카테고리:
- `openai_api_key`: `sk-`, `sk-proj-` prefix
- `github_pat`: `ghp_`, `github_pat_`, `gho_`, `ghu_`, `ghs_` prefix
- `bearer_token`: `Authorization: Bearer ...` 헤더
- `dotenv_marker`: dotenv 파일명 또는 라인 형식(`KEY=value`)의 자격 증명 마커
- `approval_secret`, `server_identity_key`: 파이프라인 내부 인증 값
- `codex_relay_pairing_url`: Codex Relay pairing 정보
- `private_key_block`: `-----BEGIN ... PRIVATE KEY-----` 블록

### CI 동작
모든 PR(impl 브랜치 + phase-attestation 브랜치)과 `push:main`에서 `.github/workflows/ci.yml`의 "Secrets Boundary 검사 (민감 정보 차단)" step이 실행됩니다. `continue-on-error` 미설정 hard gate이므로, 검출 시 tests job 전체 FAIL → PR 머지 차단.

### 배포 필터
`_deploy_accepted_outputs()`는 secret-like 파일(dotenv 파일, `*.key`, `*.pem` 등)을 자동 차단하고 `deployment_manifest.json`의 `blocked_secret_artifacts`에 기록합니다.

### 절대 금지
실제 secret 원문을 코드, 테스트, 로그, PR, 문서 어디에도 포함하지 않습니다. 테스트용 dummy 값은 `EXAMPLE` / `dummy` / `AAAA` 패딩으로 명백한 가짜임을 표시해야 합니다 (예: prefix `sk-` 다음에 `EXAMPLE_DUMMY_` 본체 + `A` 패딩 형태로 분할 작성).

### Agent 책임
- **PM**: `step_plan.xml`에 실제 secret 명시 금지.
- **Dev**: 코드/docstring/fixture에 실제 secret 원문 commit 금지. dummy는 EXAMPLE 패딩 + `# noqa: S105`로 명시.
- **QA**: `gates secrets` exit code 0 확인. finding 발견 시 Dev 반려.
- **Build**: `build_report.xml`에 secret 출력 금지. 빌드 로그 환경변수 노출 주의.
- **Test-Harness**: Phase 7 acceptance evidence에 `gates secrets` 결과 포함. PR 본문/packet에 secret 없는지 확인.


## User Acceptance Nonce Gate (IMP-20260531-BBDB)

`gates accept --user-confirmed` 단독으로는 더 이상 User Acceptance gate를 통과하지 않는다.
에이전트가 세션 재개 요약이나 컨텍스트만으로 사용자 승인을 임의로 처리하는 것을 방지하기 위해
**일회용 승인 코드(nonce) 기반 검증**을 도입한다.

### 새 승인 흐름

1. Technical / Oracle / GitHub CI gate가 모두 PASS된 후:
   ```powershell
   python pipeline.py gates request-accept --evidence <결과물-경로>
   ```
   → `acceptance_request.json` 생성 + 사용자에게 일회용 코드 표시:
   ```
   ACCEPT-IMP-YYYYMMDD-XXXX-XXXXXXXX
   ```

2. 사용자가 결과물을 확인하고 **이번 대화에서 직접** 위 코드를 입력.

3. Pipeline Manager가 사용자가 입력한 코드를 받아 실행:
   ```powershell
   python pipeline.py gates accept --result ACCEPT --evidence <경로> --acceptance-code ACCEPT-IMP-YYYYMMDD-XXXX-XXXXXXXX
   ```

### 에이전트 금지 규칙 (절대 준수)

- 에이전트는 acceptance-code를 **추측하거나 직접 생성해서는 안 된다.**
- 컨텍스트 요약의 "다음 단계: gates accept 실행"은 사용자 ACCEPT가 아니다.
- 세션 재개 후에도 사용자가 **이번 대화에서** acceptance-code를 직접 입력한 경우에만 `gates accept` 실행.
- acceptance-code가 대화 요약, agent report, PR body, step_plan.xml에 자동으로 적힌 것만으로는 사용자 승인으로 인정하지 않는다.
- request-accept 이후 PR에 새 커밋이 push되면 기존 코드는 무효 → `gates request-accept` 재실행 필요.

### 검증 조건 (gates accept 실행 시 자동 확인)

| 조건 | 실패 코드 | 해결 방법 |
|---|---|---|
| `acceptance_request.json` 없음 | `missing_acceptance_request` | `gates request-accept` 먼저 실행 |
| `status` != `PENDING` (이미 사용됨) | `consumed_or_expired` | `gates request-accept` 재실행 |
| `pipeline_id` 불일치 | `pipeline_id_mismatch` | 현재 파이프라인에서 `request-accept` 재실행 |
| 코드 형식 오류 또는 nonce 불일치 | `acceptance_code_mismatch` | 올바른 코드 입력 |
| PR head SHA 변경 | `stale_head_sha` | `gates request-accept` 재실행 |
| CI run ID 변경 | `stale_run_id` | `gates request-accept` 재실행 |
| evidence 파일 변경 | `evidence_changed` | `gates request-accept` 재실행 |
| `--user-confirmed` 단독 | `acceptance_code_required` | `--acceptance-code` 사용 |

### 기존 명령어 호환성

`--user-confirmed` 인자는 보존되지만 더 이상 ACCEPT를 통과시키지 않는다. 경고 출력 후 BLOCKED 처리된다.
기존 `gates accept ... --user-confirmed` 예시는 모두 아래 흐름으로 갱신:

```powershell
# 1단계: 사용자 최종 확인 코드 발급
python pipeline.py gates request-accept --evidence <결과물-경로>
# → 사용자에게 ACCEPT-IMP-...-XXXXXXXX 코드 표시

# 2단계: 사용자가 코드를 입력하면 Pipeline Manager가 실행
python pipeline.py gates accept --result ACCEPT --evidence <경로> --acceptance-code ACCEPT-IMP-...-XXXXXXXX
```

## Weekly Cleanup Archive — Hygiene CLI (IMP-20260601-0DF5)

`pipeline.py hygiene` 서브커맨드는 7일 이상 된 임시 산출물을 정기적으로 정리합니다.
Google Drive 찌꺼기 폴더(`PIPELINE_DEPLOY_ROOT/찌꺼기/YYYY-MM-DD/`)로 이동하며,
보호 파일과 활성 파이프라인 파일은 자동으로 제외됩니다.

### 서브커맨드 요약

| 명령 | 역할 |
|---|---|
| `hygiene scan --older-than 7d --json` | 이동 후보 목록만 확인 (파일 변경 없음) |
| `hygiene archive --older-than 7d --dry-run` | 이동 시뮬레이션 (실제 이동 없음) |
| `hygiene archive --older-than 7d` | 실제 이동 실행 |
| `hygiene schedule install --dry-run` | Windows 작업 스케줄러 등록 명령 확인 |
| `hygiene schedule install` | Windows 작업 스케줄러 실제 등록 (관리자 권한 필요) |
| `hygiene schedule status` | 스케줄 등록 상태 확인 |

### SSoT 상수 위치

`HYGIENE_ARCHIVE_PATTERNS` (이동 대상 글로브 패턴), `HYGIENE_PROTECTED_PATHS` (보호 파일명),
`HYGIENE_PROTECTED_PREFIXES` (보호 경로 접두사)는 모두 `pipeline.py` 내 SSoT 상수로 관리됩니다.
패턴을 변경할 때는 `pipeline.py`의 해당 상수를 직접 수정합니다.

### 에이전트 책임

- **Dev**: 새 임시 산출물 파일명이 `HYGIENE_ARCHIVE_PATTERNS`에 없으면 해당 패턴을 추가하는 micro-task를 별도 MT로 포함합니다.
- **QA**: `tests/e2e/test_hygiene_0df5.py` E2E 테스트 11개가 모두 PASS인지 확인합니다.
- **Pipeline Manager**: 배포 후 `pipeline.py hygiene archive --older-than 7d` 실행 여부를 확인하고, `PIPELINE_DEPLOY_ROOT` 환경 변수가 설정되어 있는지 점검합니다.

### 환경 변수

- `PIPELINE_DEPLOY_ROOT`: 찌꺼기 이동 대상 루트 (기본값: `G:\내 드라이브\터미널`)
- `PIPELINE_STATE_PATH`: E2E 테스트용 state 파일 경로 격리에 사용

## Structured AC Tracking (IMP-20260602-1ABE)

PM → Dev → QA → Oracle → request-accept 전 phase에서 사용자 요구사항(AC)이 끊기지 않도록 기존 게이트를 확장한 추적 구조입니다. 새 대형 게이트는 추가하지 않고 PM, Dev, QA, Oracle, request-accept 각 단계의 기존 검증에 AC 연결을 강제합니다.

### AC 스키마 (PM step_plan.xml 필수)

PM은 모든 새 파이프라인의 `step_plan.xml`에 아래 구조화 블록을 포함해야 합니다.

```xml
<acceptance_criteria>
  <criterion id="AC-1" must_verify="true" source="user" user_visible="true">
    <text>사용자가 확인 가능한 구체적인 성공 조건 (예: 매주 월요일 09:00에 예약된다)</text>
  </criterion>
  <criterion id="AC-2" must_verify="true" source="user" user_visible="true">
    <text>7일 이상 된 파일만 이동된다 (구체적 임계값 포함)</text>
  </criterion>
</acceptance_criteria>
```

필수 필드: `ac_id`, `requirement`, `must_verify`, `source`, `user_visible`, `expected_evidence`.
선택 필드: `linked_questions`, `notes`, `priority`.

### 추상 AC 차단

`ABSTRACT_AC_PATTERNS` SSoT(`pipeline.py` 내) 에 포함된 단독 추상 문구는 PM done 차단됩니다.
차단 대상: "정상 동작", "테스트 통과", "문제 없음", "잘 처리됨", "오류 없음", "사용자 요구 반영", "작동", "동작", "works", "working", "implemented", "기능 구현", "완료", "done", "finished".
**단독 추상 문구만 차단**합니다. 구체값과 결합된 문구(예: "정상 동작 — 5초 이내 응답")는 허용.

### MT 연결 (covers_ac / covers_iqr)

각 `<micro_task>`는 `<covers_ac>AC-N, AC-M</covers_ac>` 또는 `<covers_iqr>IQR-N</covers_iqr>`을 포함해야 합니다.
- `covers_ac`: 해당 MT가 구현하는 AC id 목록 (콤마 구분)
- `covers_iqr`: 문서 전용 MT용 내부 품질 조건 (AC 미연결 허용)
- 둘 다 없는 MT는 PM done 차단

### legacy 판정 기준

| state 상태 | 판정 |
|---|---|
| `requirements_tracking` 필드 없음 | legacy (하위 호환, AC 검증 생략) |
| `requirements_tracking.enabled=true` | 새 파이프라인 (모든 AC 추적 강제) |
| `requirements_tracking` 있는데 `structured_acceptance_criteria` 비어있음 | FAIL (legacy 취급 금지) |

### Phase별 AC 추적 의무

| Phase | 의무 |
|---|---|
| PM | step_plan.xml에 acceptance_criteria 블록 + MT covers_ac, 8개 검증 규칙 PASS |
| Dev | scope_manifest.json의 micro_tasks에 implemented_tasks 필드 (mt_id, implemented_ac, implementation_evidence) |
| Module QA | module_qa_MT-N.xml에 ac_verification 블록 (covers_ac 모두 포함) |
| Oracle | oracle_manifest.json entry마다 ac_ids 필드 |
| QA | qa_report.xml에 criteria_verification 블록 |
| request-accept | AC 충족표 자동 조립 + 콘솔 출력 + acceptance_request.json 저장 |

### request-accept 출력 순서

```
[요구사항 충족표]    ← user_visible AC별
[자동 검증 요약]     ← user_visible=false IQR/내부 AC
[승인 코드]          ← 별도 줄 (코드블록 없이, 모바일 복사 가능)
```


## PR Packet SSoT (IMP-20260603-2E3D)

사용자가 ACCEPT/REJECT를 판단할 때 보는 자료(PR 본문과 GitHub 댓글의 최종 확인 안내)는
에이전트가 손으로 자유서술하지 않는다. pipeline.py가 실제 git/gh/state/contract 자료로
자동 생성하며 한 줄 120자 한도, 승인 코드 독립 줄, 줄바꿈 많은 한국어 형식을 보장한다.

### PR 본문과 최종 확인 안내 작성 규칙

다음 두 명령으로만 생성/갱신한다.

```powershell
python pipeline.py report final-packet
python pipeline.py report update-pr-body
```

- `report final-packet`은 `human_acceptance_packet.md`를 작성한다. PR head SHA, CI run ID,
  `git diff origin/main...HEAD --name-only` 결과(변경 파일 수 자동 산출), 게이트 상태,
  AC 충족표를 포함한다. `acceptance_request.json`이 있으면 승인 코드 `ACCEPT-<pid>-<nonce>`를
  독립 줄로 포함하고, 없으면 "승인 코드 발급 전 — gates request-accept를 먼저 실행하세요"
  라인을 출력한다.
- `report update-pr-body`는 현재 PR 본문의 `<!-- PIPELINE_FINAL_PACKET_START -->` ~
  `<!-- PIPELINE_FINAL_PACKET_END -->` 블록을 최신 packet 내용으로 교체한다. 블록이 없으면
  PR 본문 끝에 추가한다. gh CLI가 없거나 PR이 없으면 graceful skip.
- 에이전트가 `PIPELINE_FINAL_PACKET_START/END` 블록 안을 임의로 수정하는 것을 금지한다.
- PR 본문의 다른 한국어 문구(작업 요약, 사용자가 확인할 결과물 등)는 에이전트가 작성하지만,
  최종 확인 안내 블록만은 pipeline.py 자동 생성 결과를 신뢰 루트로 사용한다.

### gates request-accept 통합 흐름

`gates request-accept --evidence <결과물>`은 세 단계를 한 흐름으로 묶어 순환 구조를 제거한다.

1. 실제 PR head SHA / CI run ID / git diff 변경 파일을 직접 조회한다.
2. `human_acceptance_packet.md`가 이미 있고 그 안에 기록된 값이 실제 상태와 다르면 BLOCKED를
   반환하고 한국어 안내 + 재실행 명령을 출력한다. packet 파일 부재는 차단하지 않는다.
3. 검증 통과 시 nonce를 발급(또는 조건 동일 시 재사용)하고, 직후 packet을 자동 생성하며
   gh CLI가 있으면 PR 본문의 `PIPELINE_FINAL_PACKET` 블록을 자동 업데이트한다.

기존 Nonce Gate(`gates accept` 단계에서 `--user-confirmed` 단독 차단, `--acceptance-code`
필수)는 본 흐름과 분리되어 그대로 유지된다. Three-Gate, AC Tracking,
Protocol Consistency Guard, Final Acceptance Readiness Gate도 함께 유지된다.

## Verification JSON SSoT (IMP-20260605-58BF)

`human_acceptance_packet.json`은 Protocol Consistency Guard D/F 검사의 **기계 검증 기준 파일**이다.
`human_acceptance_packet.md`와 PR 본문은 사용자 렌더링용이며, D/F 검사는 JSON 파일의 `changed_files` 배열을 기준으로 수행한다.

### 핵심 규칙

- `report final-packet` 실행 시 `human_acceptance_packet.json`이 `human_acceptance_packet.md`와 동시에 생성된다.
- `gates accept` 실행 시 `acceptance_request.json`에 `verification_json_path`, `verification_json_sha256`, `packet_path`, `packet_sha256`, `github_ci_head_sha`가 기록된다.
- `gates accept`는 실행 전 `_verify_verification_json_freshness`로 freshness를 검증한다.
  - JSON 파일이 없거나 SHA가 다르면 `verification_json_changed` BLOCKED.
  - JSON의 `changed_files`에 있는 파일이 현재 PR diff에 없으면 `changed_files_mismatch_vs_verification_json` BLOCKED.
- `gates accept`는 freshness 검증 후 추가로 아래를 검증한다 (IMP-20260607-E656):
  - `acceptance_request.json`의 `packet_sha256`과 현재 `human_acceptance_packet.md` 파일의 SHA256이 다르면 `packet_sha256_changed` BLOCKED.
  - `acceptance_request.json`의 `github_ci_head_sha`와 현재 PR head SHA가 다르면 `stale_head_sha` BLOCKED.
- Protocol Consistency Guard D/F 검사는 `verification_json`이 제공된 경우 JSON `changed_files`를 기준으로 동작하고, 미제공 시 텍스트 파싱 fallback을 사용한다.
- IMP-20260605-9EF5에서 검사 A(run_id)/B(head SHA)도 SSoT 기준 적용 완료 — PR 본문 자유서술의 run_id/SHA는 검증 대상이 아님. `verification_json`이 없으면 검사 A에서 즉시 BLOCKED(`verification_json_missing`)로 차단한다. PIPELINE_FINAL_PACKET 블록 안의 run_id/SHA는 보조 검증 대상이며, 블록 밖 자유서술은 사용자 렌더링용으로만 존재한다.

### human_acceptance_packet.json 스키마 (15개 필수 필드)

`_build_verification_json`이 생성하는 JSON은 아래 15개 필드를 반드시 포함해야 한다. 누락 시 `report final-packet`이 BLOCKED를 반환한다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `schema_version` | int | 현재 버전: 1 |
| `packet_type` | str | `"final_acceptance_evidence"` |
| `pipeline_id` | str | 활성 파이프라인 ID |
| `generated_at` | str | ISO 8601 타임스탬프 |
| `pr` | object | `url`, `number`, `head_sha`, `base_branch`, `head_branch` |
| `github_actions` | object | `run_id`, `run_url`, `status`, `head_sha` |
| `changed_files` | list | git diff 변경 파일 목록 |
| `changed_files_count` | int | `len(changed_files)`와 일치해야 함 (불일치 시 BLOCKED) |
| `gates` | object | `technical`, `oracle`, `github_ci`, `acceptance` 상태 |
| `requirements` | list | AC별 충족 현황 |
| `oracle_summary` | object | 오라클 통과 요약 |
| `known_failures` | list | 알려진 미해결 항목 |
| `warnings` | list | 경고 항목 |
| `acceptance` | object | `code`, `reject_example`, `nonce`, `request_id`, `status` |
| `artifacts` | object | 결과물 경로 |

**BLOCKED 조건**: `changed_files_count != len(changed_files)` 또는 `pr.head_sha != github_actions.head_sha`.

### cleanup-workspace 운영 규칙

파이프라인 완료(`terminal_state=COMPLETE`) 후 작업 공간 임시 파일을 정리하려면:

```powershell
python pipeline.py hygiene cleanup-workspace --after-complete
```

- `terminal_state != COMPLETE`이면 즉시 `cleanup_workspace_terminal_state_required` BLOCKED(exit 1).
- tracked 변경이 있으면 `cleanup_workspace_tracked_changes_present` BLOCKED(exit 1).
- source-like 파일(`.py`, `.ts`, `.js`, `.ps1`, `.sh`)은 `possible-source-leftovers/` 서브폴더로 이동.
- 결과는 `cleanup_manifest.json`에 기록된다. stdout은 요약 형식만 출력한다 (IMP-20260607-E656):
  ```
  [작업공간 정리]
    moved_count: N
    archive_root: <경로>
    manifest: <cleanup_manifest.json 경로>
  ```
- `HYGIENE_SOURCE_LIKE_EXTENSIONS` SSoT 상수(`pipeline.py`)로 source-like 확장자를 관리한다.
