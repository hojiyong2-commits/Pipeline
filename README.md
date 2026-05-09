# Pipeline — Self-Evolving Multi-Agent Protocol

A 9-phase autonomous app factory pipeline enforcer.

## 현재 기본 구조

모든 파이프라인은 이제 예외 없이 Three-Gate + Option A + Incremental Module Gate로 실행합니다. 이 셋은 선택 모드가 아닙니다.

쉽게 말하면:

1. PM이 요구사항을 작은 `MT-N` 단위로 쪼갭니다.
2. 각 `MT-N`은 `module design -> module dev -> module qa`를 통과해야 다음 모듈로 넘어갑니다.
3. 모든 모듈이 PASS되면 `module integrate`로 합칩니다.
4. PM/Dev/QA/Build phase는 각각 GitHub Actions phase attestation을 통과해야 다음 역할 경계로 넘어갑니다.
5. 최종 완료는 Technical, Oracle, GitHub CI, User Acceptance가 모두 PASS된 뒤에만 가능합니다.

사용자가 마지막에 해야 할 일은 코드 리뷰가 아니라 결과물 확인입니다. 마지막 agent가 PR 링크와 GitHub Actions의 한국어 최종 확인 안내, 실제 결과물 경로나 artifact 링크를 줍니다. 사용자는 그 결과물이 요청과 맞는지만 보고 ACCEPT 또는 REJECT를 선택합니다.

## 필수 명령 흐름

```powershell
python pipeline.py contract init
python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --agent-run-id <pm_run_id>
python pipeline.py gates prepare-phase --phase pm
python pipeline.py gates phase-ci --phase pm --repo hojiyong2-commits/Pipeline

python pipeline.py module design --mt-id MT-1 --report-file module_design_MT-1.xml
python pipeline.py module dev --mt-id MT-1 --files "path/to/file.py" --report-file module_handover_MT-1.xml --scope-manifest scope_manifest_MT-1.json
python pipeline.py module qa --mt-id MT-1 --result PASS --report-file module_qa_MT-1.xml

# Repeat module design/dev/qa for every MT-N, then:
python pipeline.py module integrate --result PASS --report-file integration_report.xml

python pipeline.py done --phase dev --files "path/to/file.py" --report-file dev_handover.xml --scope-declared --scope-manifest scope_manifest.json --agent-run-id <dev_run_id>
python pipeline.py gates prepare-phase --phase dev
python pipeline.py gates phase-ci --phase dev --repo hojiyong2-commits/Pipeline

python pipeline.py qa --result PASS --numeric-score 110 --report-file qa_report.xml --agent-run-id <qa_run_id>
python pipeline.py gates prepare-phase --phase qa
python pipeline.py gates phase-ci --phase qa --repo hojiyong2-commits/Pipeline

python pipeline.py build --exe "dist/app.exe" --report-file build_report.xml --agent-run-id <build_run_id>
python pipeline.py gates prepare-phase --phase build
python pipeline.py gates phase-ci --phase build --repo hojiyong2-commits/Pipeline

python pipeline.py gates technical
python pipeline.py gates oracle --user-confirmed
python pipeline.py gates github-ci --repo hojiyong2-commits/Pipeline
python pipeline.py gates accept --result ACCEPT --evidence <real-result-path-or-artifact> --user-confirmed
python pipeline.py architect --report-file architect_report.xml
```

## Pipeline smoke test

**Pipeline ID:** IMP-20260509-A4DB

This section documents the end-to-end pipeline smoke test that verifies the full
9-phase self-evolving multi-agent workflow operates correctly.

### What this smoke test validates

| Phase | Description | Gate |
|---|---|---|
| Phase 1 — PM | Contract v2 Three-Gate initialization, task decomposition, step_plan.xml | `pipeline.py done --phase pm --decomp --clarification --roadmap` |
| Phase 2 — Dev | README.md creation on feature branch, PR workflow | `pipeline.py done --phase dev --files "README.md" --scope-declared` |
| Phase 4 — QA | Content verification, branch/PR validation | `pipeline.py qa --result PASS --numeric-score N` |
| Phase 5 — SEC | Skip (docs-only, no network or DB code) | `pipeline.py sec --skip` |
| Phase 6 — Build | N/A (docs-only deliverable) | `pipeline.py build --exe "N/A" --skip-reason "docs-only" --user-confirmed` |
| Phase A — Phase CI | PM/Dev/QA/Build each get GitHub Actions attestation before the next role boundary | `pipeline.py gates prepare-phase --phase dev` then `pipeline.py gates phase-ci --phase dev` |
| Phase 7 — Three-Gate | Technical gate + Oracle gate + GitHub CI gate + User acceptance gate | `pipeline.py gates accept --result ACCEPT --evidence [real-result-path] --user-confirmed` |
| Phase 8 — Architect | RCA and optimization report | `pipeline.py architect --report-file architect_report.xml` |

### Constraints verified

- No direct push to `main` — all changes via PR from `smoke-test/IMP-20260509-A4DB`
- GitHub Actions CI (pytest + `pipeline_attestation.json` upload) must PASS before gate acceptance
- Option A is mandatory: PM/Dev/QA/Build phase requests produce `phase_attestation.json` in GitHub Actions
- Three-Gate is mandatory: Technical + Oracle (waived only by audited docs-only rules) + GitHub CI + User Acceptance
- User acceptance deploys accepted artifacts to `G:\내 드라이브\터미널\<pipeline_id>` and writes `deployment_manifest.json`

### Option A: per-agent receipt + phase CI

When phase attestations are enabled, every major phase is tied to a one-time
agent run receipt before it can be submitted:

```powershell
python pipeline.py agent start --phase pm
# pass the printed token only to pm-agent
python pipeline.py agent finish --run-id <run_id> --token <token> --output-file step_plan.xml
python pipeline.py done --phase pm --report-file step_plan.xml --decomp --clarification --roadmap --agent-run-id <run_id>
python pipeline.py gates prepare-phase --phase pm
git add .pipeline/phase_attestation_request.json .pipeline/phase_evidence
git commit -m "Add pm phase attestation request"
git push
python pipeline.py gates phase-ci --phase pm --repo hojiyong2-commits/Pipeline
```

Repeat the same pattern for `dev`, `qa`, and `build`, using the expected agent
ids `dev-agent`, `qa-agent`, and `build-agent`. GitHub Actions validates the
phase request, copied evidence, copied agent receipt, commit SHA, and test run
before the next role boundary can proceed.

In this mode, PM is not allowed to act as Dev/QA/Build. The orchestrator owns
receipt issuance and passes the one-time token only to the assigned phase agent;
`pipeline.py` and GitHub Actions then verify that the phase submission references
that completed receipt.

This is a strong local operating guard, not perfect cryptographic isolation.
Absolute proof that a different OS-level actor performed each role requires an
external runner or separate signer that local agents cannot modify.

### User-owned oracle files

Phase 1 PM must ask the user for concrete input and expected output examples
before Dev starts. Store those answer-key files under:

```text
tests/oracles/<pipeline_id>/<case_id>/
```

Then register them:

```powershell
python pipeline.py contract add-oracle --input tests/oracles/<pipeline_id>/<case_id>/input.json --expected tests/oracles/<pipeline_id>/<case_id>/expected.json --case-kind normal
```

`pipeline.py contract audit` rejects oracle files outside `tests/oracles/**`.
CODEOWNERS also marks `tests/oracles/**`, `tests/**`, root `test_*.py`, and
`*_test.py` as user-owned review surfaces. This keeps the answer key and tests
visible in PR review instead of letting the implementing agent silently rewrite
the judge.

### CI Workflow

The `.github/workflows/ci.yml` workflow runs on every PR and push to `main`:

1. Runs `python -m pytest -q` on `windows-latest`
2. On success, creates `pipeline_attestation.json` with run metadata
3. On pull requests, creates `human_acceptance_packet.md`, uploads it as `human-acceptance-packet`, and posts/updates a Korean **최종 확인 안내** PR comment with the same packet.
4. If `.pipeline/phase_attestation_request.json` exists, validates the phase evidence and creates `phase_attestation.json`
5. Uploads the attestations as GitHub Actions artifacts named `pipeline-attestation` and, when requested, `pipeline-phase-attestation`

The `pipeline.py gates github-ci` command verifies the latest CI run against HEAD
and records the GitHub CI gate as PASS when the attestation is valid.
The `pipeline.py gates phase-ci --phase <pm|dev|qa|build>` command verifies the
per-phase GitHub attestation before the next role boundary may proceed.

### 최종 사용자 확인

사용자가 마지막에 할 일은 이것뿐입니다:

1. 마지막 agent가 준 PR 링크를 엽니다.
2. PR 댓글의 **최종 확인 안내**를 읽습니다.
3. 그 안내에 적힌 결과 링크/경로 또는 artifact를 엽니다.
4. 결과물이 요청과 맞으면 Claude Code/dashboard에서 ACCEPT, 아니면 REJECT를 고릅니다.

이 안내는 GitHub Actions가 자동으로 만듭니다. CI 통과 여부, 변경 파일,
중요 보호 파일 변경 여부, artifact 링크를 요약합니다. 사용자는 코드를
검토하지 않고 결과물만 보고 판단하면 됩니다.

### GitHub trust root

The repository `main` branch is protected outside the local agent loop:

- direct pushes to `main` are blocked
- PRs must pass the GitHub Actions `tests` check before merge
- admins are also subject to the protection rule
- `.github/CODEOWNERS`, `.github/workflows/**`, `pipeline.py`, `CLAUDE.md`,
  `.claude/agents/**`, `tests/oracles/**`, `tests/**`, `test_*.py`, and
  `*_test.py` are listed in `.github/CODEOWNERS`

This keeps the final merge decision and CI check outside local Claude/Codex
claims. In a single-owner repo, mandatory one-person review can block self-authored
PRs; add a second trusted GitHub account before raising the required approval
count to `1`.
