# Pipeline — Self-Evolving Multi-Agent Protocol

A 9-phase autonomous app factory pipeline enforcer.

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
- Option A mode: PM/Dev/QA/Build phase requests produce `phase_attestation.json` in GitHub Actions
- Three-Gate mode: Technical + Oracle (waived: docs-only) + GitHub CI + User Acceptance
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

### CI Workflow

The `.github/workflows/ci.yml` workflow runs on every PR and push to `main`:

1. Runs `python -m pytest -q` on `windows-latest`
2. On success, creates `pipeline_attestation.json` with run metadata
3. If `.pipeline/phase_attestation_request.json` exists, validates the phase evidence and creates `phase_attestation.json`
4. Uploads the attestations as GitHub Actions artifacts named `pipeline-attestation` and, when requested, `pipeline-phase-attestation`

The `pipeline.py gates github-ci` command verifies the latest CI run against HEAD
and records the GitHub CI gate as PASS when the attestation is valid.
The `pipeline.py gates phase-ci --phase <pm|dev|qa|build>` command verifies the
per-phase GitHub attestation before the next role boundary may proceed.
