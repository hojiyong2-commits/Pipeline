# Final Work Protocol: Autonomous App Factory
# Self-Evolving Multi-Agent Protocol (8-Phase + Separate Protocol Evolution IMP)

이 프로젝트에서는 아래 자기 진화형 다중 에이전트 프로토콜을 엄격히 준수합니다.
각 단계가 완료(PASS)되기 전에는 다음 단계로 진입하지 않습니다.

## 에이전트 구성 (현재 티어)

| Agent | 파일 | 역할 | 권장 모델 티어 |
|---|---|---|---|
| PM Agent | `.claude/agents/pm-agent.md` | 요구사항 분석 및 스텝 정의 | Sonnet |
| QA Agent | `.claude/agents/qa-agent.md` | 코드 품질 검증 및 승인 게이트 | Opus |
| Security Agent | `.claude/agents/security-agent.md` | 보안 취약점 감사 | Sonnet |
| UI/App Agent | `.claude/agents/ui-app-agent.md` | 백엔드 로직을 GUI 앱으로 래핑 | Sonnet |
| Build Agent | `.claude/agents/build-agent.md` | PyInstaller 단일 EXE 빌드 및 검증 | Haiku |
| Test Harness Agent | `.claude/agents/test-harness-agent.md` | BUILD 채점(20pt) + QA 수치 채점 인계(120pt) 합산 140점 검증 | Sonnet |
| Prompt Architect Agent | `.claude/agents/prompt-architect-agent.md` | 로그 분석 → 프롬프트 패치 → 시스템 진화 | Opus |
| Agent Factory Agent | `.claude/agents/agent-factory-agent.md` | 새 전문 에이전트 설계 및 MD 파일 생성 | Sonnet |
| Protocol Evolution Agent | `.claude/agents/protocol-evolution-agent.md` | 신규 에이전트를 Work Protocol 전체에 능동적으로 동기화 | Sonnet |
| Dev Agent | `.claude/agents/dev-agent.md` | 백엔드 비즈니스 로직 구현 | Opus |
| Power Automate Agent | `.claude/agents/power-automate-agent.md` | Power Automate 플로우 정의 JSON 생성 | Sonnet |

> **티어 배치 근거:** Dev/QA가 코드 결정의 품질을 좌우하므로 Opus로 상향. PM은 조정·라우팅 중심이라 Sonnet으로 충분. UI는 Tkinter 위젯 네이밍/Threading 등 정확성 요구로 Sonnet 적용. Architect는 RCA 정밀도 유지를 위해 Opus 유지.

---

## 오케스트레이터 역할 (Reduced Scope)

오케스트레이터(main context, Sonnet)의 책임은 아래 2가지로 축소됩니다.

### 오케스트레이터가 하는 일 (2가지만)
1. **요청 분류** — 화이트리스트(읽기/조회/비교설명/질문답변/clarification 발행)이면 직접 응답. 그 외 코드 변경 수반 작업은 파이프라인 진입.
2. **파이프라인 시작 + PM Agent 즉시 위임** — `python pipeline.py new --type FEAT|BUG|IMP --desc "..."` 실행 후 pm-agent를 spawn하고 종료. 이후 모든 판단·게이트·spawn은 PM Agent가 담당.

### 오케스트레이터가 하지 않는 일 (금지)
- dev-agent / qa-agent / security-agent / build-agent / test-harness-agent / prompt-architect-agent 직접 spawn
- `pipeline.py done/qa/sec/build/harness/architect` 호출 (status/check 조회는 허용)
- QA verdict / harness score / SEC risk 판독 및 재작업 분기 결정
- Phase 6→7 사용자 확인 질문 직접 발행

### 유지되는 행동
- 코드 Read/Grep/Glob 분석
- `python pipeline.py status/check` 조회
- 파이프라인 외부 사용자 질문 직접 답변
- Main Context 코드 수정 절대 금지 규칙

### PM Pipeline Manager Spawn Chain

오케스트레이터가 파이프라인 진입 시 수행하는 정확한 시퀀스:

1. `python pipeline.py new --type FEAT|BUG|IMP --desc "..."` → pipeline_id 획득
2. **Round 1:** pm-agent spawn (prompt에 pipeline_id, 사용자 요청 원문, `mode: pipeline_manager_round1` 포함)
3. pm-agent의 `<step_plan>` + `<pipeline_manager_handoff>ready</pipeline_manager_handoff>` 수신
4. step_plan에 `<askuser>` 블록 있으면 사용자에게 전달 후 응답 대기
5. **Round 2:** pm-agent 재spawn (prompt에 `mode: pipeline_manager_round2`, Round 1 step_plan 전문, 사용자 응답 포함)
6. 이후 오케스트레이터는 도구 호출 없이 PM의 최종 출력만 사용자에게 중계
7. PM이 `<pipeline_complete>` 출력 시 결과 요약 보고

**오케스트레이터 직접 spawn 허용 agent:** pm-agent (Round 1, Round 2)만. 그 외 모두 pm-agent 책임.

위반 시: `[SPAWN CHAIN VIOLATION] 오케스트레이터는 pm-agent 외 sub-agent를 직접 spawn할 수 없습니다.`

## Phase 1: PM Phase (Planning)

### Contract v2 Discovery/Acceptance Mode

For new non-trivial work, prefer Contract v2. This is a universal workflow, not an automation-only workflow. It applies to apps, scripts, extensions, docs/MD edits, prompt/agent changes, analysis, refactors, Power Automate design, and business automation.

Contract v2 adds a Discovery/Contract freeze before Dev:
1. `python pipeline.py contract init`
2. PM decomposes the task into modules/components.
3. PM asks user-facing questions until P0 ambiguity is resolved.
4. PM creates `task_contract.json` and `test_set.json`.
5. `python pipeline.py contract ready`
6. `python pipeline.py contract freeze`
7. Only then may Dev start.

If a contract subcommand prints `[CONTRACT NOT INITIALIZED]`, the correct recovery is to run the suggested `python pipeline.py contract init --pipeline-id ...` command first. Do not keep retrying `add-module`, `add-test`, `audit`, `ready`, `freeze`, or `show` against missing contract files.

When Contract v2 is enabled, `pipeline.py check --phase dev` blocks until the contract and test set are frozen. Phase 6 Build remains required for runnable deliverables. For non-runnable deliverables, PM must explicitly set build target as not required and provide acceptance checks for the artifact instead.

### Three-Gate External Authority Mode

Three-Gate mode and Option A phase attestation are mandatory for every pipeline. They are not optional modes.

1. `python pipeline.py contract init` creates Contract v2 with Three-Gate and phase attestations enabled. `--three-gate` and `--phase-attestations` are accepted only for backward-compatible scripts.
2. Register user-supplied oracle files only from `tests/oracles/**` with `python pipeline.py contract add-oracle --input tests/oracles/... --expected tests/oracles/... --case-kind normal`, plus at least one additional `--case-kind edge|exception|error` oracle. PM must ask the user for the answer key during Phase 1 and save the input/expected files under `tests/oracles/<pipeline_id>/<case_id>/`; Dev must implement against those files, not rewrite them.
3. Run `python pipeline.py contract audit`; freeze is blocked until the rule-based audit PASSes.
4. PM completion is also hard-gated: `python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification ...` must include a real `<decomposition_audit>` and `<step_plan><micro_tasks>...</micro_tasks></step_plan>`.
5. Dev completion is hard-gated against the PM atomic plan: `python pipeline.py done --phase dev --files "..." --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json` must prove every declared and actual changed file maps to a PM micro-task.
6. Option A phase attestation is mandatory before moving across major role boundaries:
   - after PM: `python pipeline.py gates prepare-phase --phase pm`, commit/push, wait for GitHub Actions, then `python pipeline.py gates phase-ci --phase pm --repo hojiyong2-commits/Pipeline`
   - after Dev: repeat with `--phase dev`
   - after QA PASS: repeat with `--phase qa`
   - after Build: repeat with `--phase build`
7. After Build phase attestation, Phase 7 is no longer a numeric score. It is:
   - `python pipeline.py gates technical`
   - `python pipeline.py gates oracle --user-confirmed`
   - `python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline`
   - `python pipeline.py gates accept --result ACCEPT --evidence [real-result-path] --user-confirmed`

### Incremental Module Gate

Every PM `<micro_task>` is a gated module. Dev cannot implement all modules in one undifferentiated pass. The required order is:

1. `python pipeline.py module design --mt-id MT-N --report-file module_design_MT-N.xml`
2. `python pipeline.py module dev --mt-id MT-N --files "..." --report-file module_handover_MT-N.xml --scope-manifest scope_manifest_MT-N.json`
3. `python pipeline.py module qa --mt-id MT-N --result PASS --report-file module_qa_MT-N.xml`
4. Repeat for the next MT only after the previous MT has PASS.
5. After all MTs pass: `python pipeline.py module integrate --result PASS --report-file integration_report.xml`
6. Only then record final Dev: `python pipeline.py done --phase dev --files "..." --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <run_id>`

`pipeline.py done --phase dev` blocks until every module QA is PASS and the integration gate is PASS. When a module passes QA, `pipeline.py` stores a checkpoint hash for that module's target files. Later module work is blocked if a previously passed module changes silently.

### Option A Agent Receipt Gate

When `phase_attestations.enabled=true`, PM/Dev/QA/Build phase records must be
bound to a completed agent receipt. The orchestrator starts the receipt, passes
the one-time token only to the assigned agent, and records the phase only with
the returned `run_id`:

```powershell
python pipeline.py agent start --phase pm
# give the printed token only to pm-agent
python pipeline.py agent finish --run-id <run_id> --token <token> --output-file step_plan.xml
python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --agent-run-id <run_id>
```

Use the same pattern for `dev`, `qa`, and `build`. Expected ids are fixed:
`pm-agent`, `dev-agent`, `qa-agent`, `build-agent`. `pipeline.py` rejects a
phase submission without a matching completed receipt, and `gates prepare-phase`
copies that receipt into `.pipeline/phase_evidence` so GitHub Actions can verify
it. Agents must not claim another agent's phase, reuse a run id, or submit a
phase without the receipt gate.

In Option A mode this overrides the older PM Pipeline Manager spawn chain. PM may
plan and hand off, but PM must not own downstream tokens, must not finish Dev/QA/
Build receipts, and must not submit phase CI requests for another role. The main
orchestrator acts only as the receipt clerk: it may run `agent start`, pass the
one-time token only inside the assigned agent's prompt, collect that agent's
output file, run `agent finish`, and then record the matching phase with
`--agent-run-id`. The orchestrator still must not edit product files or author
the agent reports.

This is stronger than file-only phase evidence, but it is not absolute
cryptographic isolation while every process runs as the same local OS user. Full
proof requires a separate external runner or signer that local agents cannot
modify or impersonate.

Claude Code는 사용자에게 ACCEPT를 묻기 전에 반드시 실제 결과 링크/경로를 제공해야 한다: PR 링크, artifact 경로, 스크린샷, EXE, 출력 엑셀, dashboard URL 중 해당되는 것을 준다. 마지막 안내는 짧아야 한다: PR 링크, GitHub Actions가 만든 **최종 확인 안내** PR 댓글, 실제 결과물 링크/경로를 주고 ACCEPT 또는 REJECT만 물어본다. 사용자에게 코드 검토를 요구하지 않는다. 사용자는 ACCEPT/REJECT를 입력하거나 dashboard 버튼을 눌러 승인/거절할 수 있다. ACCEPT는 PM/Dev/QA/Build phase attestation, Technical, Oracle, GitHub CI gate가 모두 PASS일 때만 유효하다. ACCEPT되면 `pipeline.py`가 승인된 결과물을 `G:\내 드라이브\터미널\<pipeline_id>`로 복사하고 `deployment_manifest.json`을 작성한다. 테스트나 명시적 로컬 설정일 때만 `PIPELINE_DEPLOY_ROOT`로 이 경로를 바꿀 수 있다.

In Three-Gate mode, `COMPLETE` is impossible until required phase attestations, Technical, Oracle, GitHub CI, and User Acceptance gates are all PASS. The technical gate is strict by default: missing ruff/mypy/bandit/pytest or missing Python evidence files fail unless `--relaxed-tools` is explicitly used. Oracle audit requires user-source hashes, at least one normal oracle, at least one edge/exception/error oracle, non-empty/non-placeholder expected outputs, and oracle files stored under `tests/oracles/**` so CODEOWNERS can protect the answer key. `--allow-no-oracle` is only for explicitly non-runnable docs/analysis/config work and cannot waive malformed, hashless, agent-sourced, weak, or non-codeowned oracle entries. Legacy `pipeline.py harness --score ...` is blocked in this mode. GPT/OpenAI advisory reviews are optional, non-binding red-team reviews fixed to `gpt-5.5`; API calls happen only when `OPENAI_API_KEY` is present and `ENABLE_GPT_ADVISORY=1`. Unresolved CRITICAL advisory findings block COMPLETE, but GPT never awards PASS/FAIL, never compares oracles, and never replaces user acceptance. If `pipeline.py advisory status` shows `review_count=0` or `api_call_count=0`, advisory was not actually run; agents must not report "GPT advisory CRITICAL findings zero" as if an OpenAI review occurred.

Expected CLI errors must print `[PIPELINE ERROR]` without raw traceback. Use `python pipeline.py --debug ...` only when debugging pipeline implementation bugs.

---

**참조:** `pm-agent`
PM Agent는 step_plan 발행 외에 **전체 파이프라인 매니저 역할**도 수행합니다. 상세는 `.claude/agents/pm-agent.md`의 'Pipeline Manager Mode' 섹션 참조.

- 요구사항을 분석하여 **[Backend Logic]** 과 **[UI/App Interface]** 설계로 구분된 상세 스텝을 정의하고 `<step_plan>`을 출력합니다.
- 이전 스텝의 QA [PASS] 없이는 다음 스텝을 출력하지 않습니다.
- **[로드맵 보고 + 승인 게이트]** Mandatory Clarification Triggers 해소 완료 후, dev-agent spawn 전에 전체 실행 계획을 사용자에게 읽기 쉬운 표 형식으로 보고하고 "진행" 응답을 받아야 합니다. 면제 조건(`사전 분석 결과` / `로드맵 생략` / `자동 진행` / `clarification 생략` 키워드)이 있으면 생략 가능. 상세 형식 및 응답 처리 규칙은 `pm-agent.md`의 `## User Roadmap Presentation Gate` 섹션 참조.

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
- **Power Automate 플로우 태스크:** PM이 `<category_tags>`에 `PA` (Power Automate) 태그를 포함한 경우, dev-agent와 병렬 또는 직후에 `power-automate-agent`를 spawn하여 플로우 정의 JSON을 생성합니다. EXE 빌드 대상이 아니므로 해당 태스크는 `python pipeline.py build --exe "N/A" --skip-reason "power-automate" --user-confirmed` 로 기록합니다. 외부 HTTP 커넥터가 포함된 플로우는 Phase 5 Security Check 필수, 없으면 `python pipeline.py sec --skip` 처리합니다.

---

## Phase 3: UI/App Dev Subphase (Interface Engineering)
**참조:** `ui-app-agent`

**[선택적 Dev 보조 단계]** `category_tags`에 `UI` 포함 시에만 ui-app-agent spawn. 미포함 시 자동 생략, Phase 4(QA) 직행.

- 백엔드 로직을 GUI(Tkinter/PyQt5)와 결합합니다.
- EXE 단일 파일화를 위해 모든 리소스(아이콘 등)는 코드 내 바이너리(Base64 등)로 처리합니다.
- **파이프라인 기록:** ui-app-agent 전용 pipeline.py phase 없음. UI는 dev subphase로 취급합니다. 오케스트레이터는 ui-app-agent 완료 후 `python pipeline.py done --phase dev --files "core/...,ui/app.py" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json` 로 app.py를 포함하여 기록하거나, 이미 dev DONE 기록된 경우 QA 증거에 UI handover를 포함합니다. ui-app-agent는 `<handover>` XML 출력으로 qa-agent에 인계.
- **Code Quality 만점 체크리스트:** ui-app-agent.md `**Code Quality 만점 체크리스트**` 섹션 참조.

---

## Phase 4: QA Phase (Verification)
**참조:** `qa-agent`

- 로직 동작과 UI 바인딩을 전수 검사하여 `<qa_report>`를 출력합니다.
- **수치 채점(120점 만점) 동시 수행:** QA는 이진 PASS/FAIL 게이트와 동시에 수치 채점(`<numeric_score>` 블록)을 수행합니다. 수치 채점 결과 < 80%이면 [FAIL] 강제 처리. SEC 카테고리는 N/A (Phase 5 security-agent 전담). PD는 모듈 분리 + 인터페이스 계약 2항목만 채점(10pt). 수치 채점 결과는 Phase 7 Harness에서 BUILD 점수와 합산됩니다.
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
- **BUILD SUCCESS** → 사용자에게 아래 메시지로 확인 후 Phase 7 진행:
  ```
  [Phase 6 완료] BUILD SUCCESS — EXE: [경로]
  Phase 7 (Harness 채점)을 진행하시겠습니까? (진행 / 중단)
  ```
- **N/A 빌드(`--exe "N/A" --skip-reason "meta-task" --user-confirmed`)인 경우에도 동일한 AskUserQuestion 절차 필수.** Streamlit/웹앱/MD 전용/비코드 태스크 등 EXE 빌드 대상이 아닌 모든 케이스에서도 구체적 skip reason과 사용자 명시적 '진행' 응답 없이 Phase 7 진입 금지.
  사용자 확인 없이 Phase 7 자동 진행 금지. (N/A 빌드 포함 예외 없음)

---

## Phase 7: Benchmarking Phase (Performance Testing)

### Three-Gate Phase 7

If `external_gates.enabled=true`, Phase 7 uses external binary gates instead of numeric scoring:

0. If `phase_attestations.enabled=true`, PM/Dev/QA/Build each require `agent start -> agent finish -> phase record with --agent-run-id -> prepare-phase -> push -> GitHub Actions -> phase-ci` before the next role boundary may proceed.
1. Technical gate: `python pipeline.py gates technical`
2. Oracle gate: `python pipeline.py gates oracle --user-confirmed`
3. GitHub CI gate: `python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline`
4. User Acceptance gate: `python pipeline.py gates accept --result ACCEPT --evidence [file-or-screenshot] --user-confirmed`

Any gate FAIL means iterate the failing gate path. The test-harness-agent may diagnose, but must not invent a numeric score. `pipeline.py architect` blocks COMPLETE until all required phase attestations, all four external gates, and actual unresolved GPT advisory CRITICAL findings are clear. No advisory review file means "advisory not run", not "advisory passed". The user acceptance command must receive a real output artifact path through `--evidence`; after ACCEPT, the pipeline deploys accepted outputs to `G:\내 드라이브\터미널\<pipeline_id>`.

After `gates accept --result ACCEPT` passes, the next required record command is `python pipeline.py architect --report-file architect_report.xml`. Three-Gate does not auto-complete at Phase 7; Phase 8 must record the final COMPLETE transition after confirming the external gate blockers are empty. `pipeline.py architect` never enters Phase 9 automatically. The architect report must include `<protocol_evolution_decision>`; if protocol or agent-rule evolution is needed, record `required=true` and start a separate IMP pipeline after this pipeline is complete.

### Contract v2 Acceptance Harness

If `contract_v2.enabled=true`, Phase 7 uses:

`python pipeline.py acceptance run --record --user-confirmed`

In this mode, agents do not submit final score/verdict. `pipeline.py` reads the frozen `test_set.json`, runs code-based scorers, computes the score, records PASS/FAIL, and writes `acceptance_result.json`. Legacy `pipeline.py harness --score ...` is only for non-v2 pipelines.

---

**참조:** `test-harness-agent`

- **Harness 역할:** BUILD 카테고리(20점) 채점을 전담합니다. WA/UI/FS/PD/AL 채점은 Phase 4 QA가 담당하며, SEC는 Phase 5 security-agent가 전담합니다. Harness는 QA의 수치 채점(120점)과 BUILD 점수(20점)를 합산하여 140점 만점으로 `test_results.jsonl`에 기록합니다.
- PM step_plan의 `<category_tags>`를 기준으로 평가 프레임워크를 자동 선택합니다.
  - BUILD 포함 → BUILD 채점(20pt) + QA numeric 인계(120pt) = 합산 140pt
  - BUILD 미포함 → QA numeric 인계(최대 120pt) 기준으로 percentage 산출
  - meta-task (`.claude/agents/*.md` / `CLAUDE.md` 편집) → Framework D (META-FS-PD)
- 결과를 `test_results.jsonl`에 JSON 한 줄로 기록합니다 (`"id"` 필드 = pipeline_id).
- 오케스트레이터: `python pipeline.py harness --score [percentage 정수] --verdict [PASS|FAIL] --test-output-file [harness_output.xml] --user-confirmed` (score는 0~100 백분율, total_score(0~140) 아님). `--test-output-file`은 PASS/FAIL 공통 필수. `--user-confirmed`는 사용자 "진행" 응답 확인 후 필수.
- **verdict: FAIL** (score < 80) → Phase 8 RCA (prompt-architect-agent) → architect 완료 후 Phase 2 dev 재작업
- **verdict: PASS** (score ≥ 80) → Phase 8 진행

---

## Phase 8: Architecture Review Phase (Diagnosis)
**참조:** `prompt-architect-agent`

- `test_results.jsonl` 로그를 분석하여 반복되는 실패 패턴을 식별합니다.
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
- Streamlit 앱, MD 전용 태스크, 비코드 수정 태스크는 EXE 빌드 대상이 아니며 `python pipeline.py build --exe "N/A" --skip-reason "meta-task" --user-confirmed` 로 기록 (예: 에이전트·CLAUDE 수정의 경우; 유형별 값은 아래 참조). 태스크 유형별 값: Streamlit→`"streamlit"`, MD 전용→`"md-only"`, 에이전트·CLAUDE 수정→`"meta-task"`, 비코드→`"no-code"`, 문서→`"docs-only"`, Power Automate→`"power-automate"` (pipeline.py hard gate, 대소문자 무관, 길이 ≥5 필수).
- 별도 Protocol Evolution IMP에서 에이전트 수정 시 핵심 역할과 출력 형식 유지.
- **[신규 에이전트 필요 시]** PM이 기존 에이전트로 처리 불가능한 전문 영역을 식별하면 `agent-factory-agent` → `protocol-evolution-agent` 순으로 spawn합니다. 동기화 전 새 에이전트를 파이프라인에서 사용 금지.
  - **[예외 — 임시 에이전트]** Dynamic Pipeline Enhancement로 생성된 `_temp_[pipeline_id]` 에이전트는 protocol-evolution-agent 동기화 대상에서 제외 (태스크 종료 후 삭제).
- **오케스트레이터가 pm-agent 외 sub-agent를 직접 spawn하면 프로토콜 위반.** 모든 sub-agent spawn 권한은 PM Agent에게 위임되었습니다.

## 오케스트레이터 하네스 규칙 (파이프라인 우회 완전 차단)

> **참조:** spawn/판독/재작업 분기 책임은 PM Agent에게 위임되었습니다. 아래 금지 규칙(코드 수정 금지·직접 편집 금지·rate limit 대기 의무)은 오케스트레이터에게 그대로 유효합니다.

아래 규칙은 main context(오케스트레이터)의 행동을 제한합니다. 에이전트가 아닌 오케스트레이터 자신에게 적용되는 강제 규칙입니다.

- **[Main Context 코드 수정 절대 금지]** 오케스트레이터는 어떠한 상황에서도 코드 파일을 직접 Edit/Write/Bash로 수정할 수 없습니다. 모든 코드 변경은 dev-agent 또는 ui-app-agent spawn을 통해서만 가능합니다. Rate limit, 속도, 편의를 이유로 한 직접 수정은 프로토콜 위반입니다.
- **[세션 재개/컨텍스트 압축 후 직접 수정 절대 금지]** 컨텍스트 압축(context compaction), 세션 재개(session resume), 이전 대화 로드 등 어떠한 세션 전환 이벤트도 직접 코드 편집의 정당 사유가 되지 않습니다. 세션 재개 후 유일한 허용 절차: ① `python pipeline.py status`로 현재 상태 확인 → ② 미완료 단계부터 에이전트를 재spawn. "이전 세션에서 절반쯤 완료된 코드가 있어 마무리만 하면 된다"는 판단으로 직접 Edit/Write 실행은 FORBIDDEN.
- **[직접 수정 자가 정당화 금지 — 완전 열거]** 아래 사유는 모두 직접 코드 편집의 정당 사유로 인정되지 않습니다: Rate limit / API 과부하 / 세션 재개·컨텍스트 압축·이전 세션 미완료 / "작은 수정"·"한 줄 fix"·"명백한 오타 교정" / 에이전트 spawn 비용 절감 / 사용자 압박감 / 에이전트 응답 지연. 유일한 허용 경로: pm-agent → dev-agent(또는 ui-app-agent) spawn.
- **[버그 수정도 전체 파이프라인 필수]** 사용자의 버그 수정 또는 개선 요청은 신규 기능과 동일하게 PM → Dev → QA → (SEC) → Build → Harness 전 단계를 통과해야 합니다. "작은 수정"이라도 예외 없습니다.
- **[Rate Limit 대기 의무]** 에이전트가 rate limit으로 실패하면 main context가 대신 수행하는 것은 절대 금지입니다. 사용자에게 상황을 보고하고 rate limit 해제 후 재시도합니다.
- **[Phase 6→7 사용자 확인 의무]** Build 완료(pipeline.py build 기록) 후 Phase 7(Harness) 진입 전, 반드시 사용자에게 확인을 받아야 합니다. 사용자가 "진행"을 명시하지 않으면 Phase 7 spawn 금지. **N/A 빌드(`--exe N/A`, Streamlit/MD 전용/메타-태스크 등)도 동일 규칙 적용 — 예외 없음.**
- **[Phase 7 생략 절대 금지]** Build SUCCESS 후 test-harness-agent 채점 없이 "완료"를 선언할 수 없습니다. 채점 결과가 없으면 파이프라인은 미완성입니다.
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
| `python pipeline.py done/qa/sec/build/harness/architect` | 상태기록 | **금지** (오케스트레이터) | **PM Agent 전용** — 오케스트레이터가 직접 호출 시 이중 기록 발생 |
| `python pipeline.py contract add-module/add-test/add-oracle/audit/ready/freeze` | 계약/상태기록 | **금지** (오케스트레이터) | PM Agent 전용. 오케스트레이터는 `contract init`도 PM에게 위임 |
| `python pipeline.py acceptance/gates/advisory` | 외부 게이트/리뷰 기록 | **금지** (오케스트레이터) | PM/test-harness/prompt-architect 책임 경로. 오케스트레이터 직접 호출 금지 |
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

### PM Agent 필수 절차 (Phase별 gate 명령어) — Phase 1 진입 명령(`pipeline.py new`)만 오케스트레이터가 호출, 나머지는 PM Agent가 호출

| Phase | 진입 전 (check) | 완료 후 (record) |
|---|---|---|
| Phase 1 — PM | `python pipeline.py new --type FEAT --desc "..."` | `python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap` |
| Phase 2 — Dev | `python pipeline.py check --phase dev` (exit 0 확인) | `python pipeline.py done --phase dev --files "파일목록" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json` |
| Phase 3 — UI/dev subphase | pipeline.py phase 없음 (ui-app-agent 자체 gate) | ui-app-agent의 app.py를 Phase 2 done --files에 포함하거나 QA 증거로 처리 |
| Phase 4 — QA | `python pipeline.py check --phase qa` (exit 0 확인) | `python pipeline.py qa --result PASS --numeric-score N --report-file qa_report.xml` 또는 `--result FAIL --numeric-score N --failure-sig "[category]:[hash]" --report-file qa_report.xml` |
| Phase 5 — SEC | `python pipeline.py check --phase sec` (exit 0 확인) | SAFE: `python pipeline.py sec --result PASS --risk LOW` / BLOCK: `--result BLOCK --risk HIGH` / FAIL: `python pipeline.py sec --result FAIL --risk MEDIUM` (dev PENDING 리셋, 스냅샷 미기록 — 의도적 설계) / 미해당: `python pipeline.py sec --skip` |
| Phase 6 — Build | `python pipeline.py check --phase build` (exit 0 확인) | `python pipeline.py build --exe "dist/앱이름.exe" --report-file dist/build_report.xml` 또는 `--exe "N/A" --skip-reason "meta-task" --user-confirmed` |
| Phase 7 — Harness | `python pipeline.py check --phase harness --user-confirmed` (exit 0 확인) | `python pipeline.py harness --score [percentage 0~100] --verdict PASS\|FAIL --test-output-file [harness_output.xml] --user-confirmed` (PASS/FAIL 공통 필수) |
| Phase 8 — Architect | `python pipeline.py check --phase architect` (exit 0 확인) | `python pipeline.py architect --report-file architect_report.xml` |

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
3. **이전 Phase 결과 요약** — QA verdict, harness score, SEC risk 등을 한 줄 요약으로 전달.

### 금지 사항
- 에이전트에게 "CLAUDE.md를 읽어라"고 지시하지 않음 — 필요한 내용은 오케스트레이터가 발췌하여 주입.
- 에이전트에게 다른 에이전트의 MD 파일을 Read하도록 지시하지 않음 — 필요 정보는 오케스트레이터가 요약하여 전달.
- prompt-architect-agent를 Phase 8에서 호출할 때도 대상 파일 목록을 명시적으로 제공. 탐색적 Glob 호출 최소화.

### Phase 8/9 재채점 의무
Phase 8(Architect)에서 에이전트 MD 파일을 수정한 경우, **반드시 Phase 7(Harness)를 재실행**하여 개선 효과를 검증해야 합니다.
- 재채점 없이 pipeline.py architect → pipeline complete 선언 금지.
- 단, 이미 100점인 경우 또는 수정이 출력 형식/채점 기준과 무관한 경우 오케스트레이터 판단으로 생략 가능하며, 이를 pipeline.py 기록에 명시해야 합니다.

---

## 핵심 운영 규칙
- **[배치 처리 절대 금지]** 한 번의 파이프라인 사이클(Phase 1→7)은 정확히 **1개 태스크**만 처리합니다. PM이 여러 스텝을 동시에 발행하거나, Dev/QA/Harness가 여러 태스크를 한꺼번에 처리하는 것은 게이트키핑을 무효화하는 시스템 위반입니다.
- **[코드 없는 채점 금지]** Harness와 QA는 실제 코드 파일이 제출된 이후에만 채점합니다. `<handover><evidence>` 태그에 실제 파일명이 명시되어 있어야 합니다. (단, category_tags=META-FS-PD이거나 .py 파일 없는 meta-task는 MD 파일을 증거로 인정)
- **[인계 계약 필수]** Dev 에이전트는 작업 완료 후 반드시 `<handover>` XML로 QA에게 인계합니다. 인계 없이 QA가 검증을 시작하면 QA는 거부 메시지를 출력하고 중단합니다.
- **[remediation_code 의무]** Security 에이전트의 모든 `<finding>`에는 `<remediation_code>` CDATA 블록이 포함되어야 합니다. 서술형 수정 지시는 허용하지 않습니다.
- **[`<test_code>` CDATA 권장 (BUG-20260508-F7A8 / BUG-20260509-05AD / BUG-20260509-4D25 / BUG-20260509-FA5E / BUG-20260509-6A4F / BUG-20260509-ED9C / BUG-20260509-894D)]** test-harness-agent가 제출하는 `<test_code>` 블록에 `<`, `>`, `&` 등 XML 특수문자가 포함될 경우 `<![CDATA[...]]>` 섹션 또는 XML entity escape(`&lt;` 등)를 사용해야 합니다. `validate_test_evidence()`는 strict unittest evidence gate를 사용합니다: (1) `_ast_assert_count()`로 test_* 메서드 내 직접 `self.assert*`/`cls.assert*` 호출을 AST 계수, (2) `_ast_forbidden_check()`로 runner/result-channel 접근 패턴을 hard-reject(`__main__`, `atexit`, `inspect`, `os`, `sys.argv`, `sys.modules`, `getattr`, `setattr`, monkeypatch, test_* 내부 `unittest.main`, return 이후 assert 등), (3) runner subprocess에 nonce와 test_code를 stdin으로 전달하고 runner가 `executed_assertions`를 집계한 nonce JSON line만 신뢰합니다. 통과 기준: `astAsserts >= 1` AND 금지 패턴 없음 AND runner nonce 일치 AND `executed_assertions >= 1` AND `testsRun >= 1` AND `failures/errors/skipped/expectedFailures/unexpectedSuccesses == 0`.
- **[BUILD REPORT XML 전용]** Build 에이전트의 출력은 반드시 `<build_report>` XML 형식(section_1~section_6 포함)이어야 합니다. 구 텍스트 형식은 폐기되었습니다.
- **[임시 에이전트 Cleanup 의무]** Dynamic Pipeline Enhancement로 생성된 임시 에이전트는 반드시 아래 절차로 삭제합니다:
  1. **삭제 트리거**: harness PASS 기록 완료 직후(pipeline.py harness 명령이 완료된 시점) 또는 파이프라인 FAIL 확정 직후 (먼저 발생하는 쪽)
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

조건 불충족 시: 기존 8단계 파이프라인(PM→Dev→QA→SEC→Build→Harness→Architect) 그대로 실행.

## Meta-Task Two-Round 방지 규칙

별도 Protocol Evolution IMP에서 에이전트 MD 파일 편집(meta-task) 수행 시:

1. **Single-Round 원칙:** 한 번의 Protocol Evolution IMP는 1라운드만 허용. 2라운드 필요 시 architect MD의 contract validation 누락이 근본 원인 → architect MD 재패치로 다음 사이클부터 차단.
2. **Producer-Consumer 묶음 패치 의무:** producer(dev/ui/qa/security)에 신규 규칙 추가 시, test-harness rubric과 CLAUDE.md Phase 규칙도 동일 사이클에서 함께 패치. 분리 패치 금지.
3. **Framework 통일:** test-harness-agent 채점 시 jsonl `framework` 필드는 `META-FS-PD` / `BUILD+QA_NUMERIC` / `QA_NUMERIC_ONLY` 3종만 허용 (FRAMEWORK_A/B/C는 폐기). 임의 명명 금지.
4. **contract_audit 블록 게이트:** Protocol Evolution IMP의 architect 출력에 `<contract_audit>` 블록이 누락되어 있으면 기록을 차단하고 architect를 재spawn.
5. **strict 모드 evidence 스키마 의무:** strict 모드 진입 시(직전 3개 META-FS-PD 사이클 중 ≥2개 100점) architect는 `<optimization_report>` 출력에 `evidence_lines` 객체를 4개 필드(section_completeness/producer_consumer_sync/backward_compatibility/role_transition_clarity) 모두 채워서 포함해야 한다. 오케스트레이터는 architect 출력의 `<contract_audit>` 검증 시 `evidence_lines` 필드도 함께 확인. 4개 필드 중 1개라도 누락 시 별도 IMP 기록 차단 + architect 재spawn.
6. **JSONL 인코딩·메타데이터 의무:** test-harness-agent가 작성하는 `test_results.jsonl`은 utf-8 + `ensure_ascii=False` 필수. N/A 카테고리는 `score: "N/A"` 문자열 형식 통일. META-FS-PD 레코드는 `strict_mode` 메타데이터 블록 필수 포함. 위반 시 prompt-architect-agent가 `<contract_audit>`에 기록 후 별도 Protocol Evolution IMP 패치 대상 포함.

## Tournament Mode (Multi-Branch Architecture Comparison)

여러 구현 방식을 병렬로 실행하여 Harness 점수 기준 최고 결과를 선택하는 모드입니다.

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
   - 각 브랜치는 독립적으로 Phase 2(Dev)→Phase 4(QA)→Phase 5(SEC)→Phase 6(Build)→Phase 7(Harness) 수행
   - 모든 pipeline.py 명령에 `--branch [A|B|...]` 옵션 추가
4. **결과 비교:** 모든 브랜치 완료 후 `python pipeline.py tournament-rank --pipeline-id [ID]`
5. **승자 확정:** `python pipeline.py tournament-finalize --pipeline-id [ID] --winner [브랜치]`
6. **결과 보고:** 승자 브랜치의 결과물(EXE 경로, 점수, 주요 특징)을 사용자에게 제시

### 승자 결정 규칙
- Build FAIL 브랜치는 자동 탈락 (ELIMINATED)
- 남은 브랜치 중 Harness 점수 최고 브랜치가 승자
- 동점 시 PM이 AskUserQuestion으로 사용자에게 최종 선택 위임

### 패자 처리
모든 브랜치 결과는 `pipeline_history/`에 보관됩니다. 승자 발표 후 패자 브랜치도 참조 가능합니다.

### pipeline.py 토너먼트 명령어

| 명령어 | 용도 |
|---|---|
| `tournament-start --pipeline-id ID --branches A,B` | 토너먼트 초기화, 브랜치 state 파일 생성 |
| `tournament-status --pipeline-id ID` | 전체 브랜치 진행 현황 |
| `tournament-rank --pipeline-id ID` | Harness 점수 비교 표 |
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
   - 해당 Phase의 에이전트를 PM Agent가 직접 spawn (오케스트레이터는 spawn 권한 없음 — pm-agent를 Round 2 모드로 재spawn하여 위임). **재spawn 시 prompt에 `mode: pipeline_manager_round2`, `pipeline_id: [ID]`, `current_phase: [N]`, `resume: true` 포함 필수 — Round 1 step_plan 생략 허용.**
   - 재개 시 사용자에게 한 줄 통지: `[Auto-Resume] 미완료 파이프라인 [pipeline_id] 감지 — Phase N부터 재개합니다.`

4. **미완료 파이프라인 없음 → 정상 처리**
   status 결과가 `활성 파이프라인 없음` 이거나 `current_phase=COMPLETE` 이면 status 결과를 무시하고 사용자 요청을 평소대로 처리.

5. **v2.2 보강 — 직접 편집 금지 그대로 유지**
   세션 재개 후에도 오케스트레이터의 코드 직접 편집은 금지입니다. Auto-Resume 은 "에이전트 spawn으로 재개" 만 자동화하며, "Main Context가 마무리한다"는 우회 경로를 열지 않습니다.

### 사용자 통지 양식

```
[Auto-Resume] 미완료 파이프라인 [IMP-YYYYMMDD-XXXX] 감지 — Phase [N] ([Phase 이름])부터 재개합니다.
```

추가 설명/확인 질문 금지 — 한 줄 통지 후 바로 PM Agent Round 2 spawn.

---

## Tier Realignment (모델 티어 재배치)

코드 결정 품질을 좌우하는 Dev/QA를 Opus로 상향, 조정·라우팅 중심인 PM은 Sonnet으로 유지. UI는 위젯 네이밍/Threading 정확성 요구로 Sonnet 적용.

### 현재 티어 매핑 (frontmatter `model:` 필드와 일치)

| Agent | 현재 티어 | 근거 |
|---|---|---|
| pm-agent | **sonnet** | 조정·라우팅·게이트 관리 중심으로 충분 |
| dev-agent | **opus** | 코드 결정 품질이 시스템 전체 점수 좌우 |
| qa-agent | **opus** | 7개 카테고리 자동 FAIL 정확도 핵심 |
| ui-app-agent | **sonnet** | 위젯 네이밍/Threading 정확성 요구 |
| prompt-architect-agent | **opus** | RCA 정밀도 유지 |
| security-agent | **sonnet** | 보안 감사 전담 |
| build-agent | **haiku** | PyInstaller 패키징 전용 |
| test-harness-agent | **sonnet** | BUILD 전담 + QA numeric 합산 로직 정확성 요구 |
| agent-factory-agent | **sonnet** | 에이전트 설계 전용 |
| protocol-evolution-agent | **sonnet** | 프로토콜 동기화 전용 |

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
| pm-agent | qa_fail_history 추적 + RECURRING 판정 + Architect 이관 (pm-agent.md `## Circuit Breaker — Same-Error 2x FAIL Escalation`) |
| prompt-architect-agent | circuit_breaker_handoff 수신 시 Strategic Restructuring Report 출력 (prompt-architect-agent.md `## Circuit Breaker Intake Mode`) |
| 사용자 | 3개 옵션 중 선택 또는 중단 |

### Protocol Evolution IMP 비자동화

Circuit Breaker로 진입한 Phase 8은 일반 RCA가 아닌 Strategic Restructuring 모드이며, **Protocol Evolution IMP를 자동 시작하지 않습니다**. 이 사이클의 결과는 `pipeline.py architect --report-file architect_report.xml` 미기록 상태로 PAUSED 또는 새 파이프라인으로 전환됩니다.

---

## Micro-task Decomposition (단일 함수/위젯 단위 변경 격리)

PM step_plan은 변경 범위를 단일 함수 또는 단일 UI 위젯 단위로 쪼개야 하며, dev-agent와 ui-app-agent는 코드 작성 전 `<impact_analysis>` XML을 필수 출력합니다. QA는 실제 diff와 `<impact_analysis>` 의 일치 여부를 검증합니다.

이 규칙은 문서 지침이 아니라 `pipeline.py` hard gate입니다. PM `done`은 모든 파이프라인에서 `step_plan.xml` 안의 `<decomposition_audit>`/`<micro_tasks>`를 파싱해 원자 단위 계획을 `pipeline_state.json`에 기록합니다. Dev `done`도 모든 파이프라인에서 `dev_handover.xml`과 `scope_manifest.json`이 그 계획의 id, 파일, 함수 범위를 정확히 따를 때만 통과합니다.

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
