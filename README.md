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
| Phase 7 — Three-Gate | Technical gate + Oracle gate + GitHub CI gate + User acceptance gate | `pipeline.py gates accept --result ACCEPT --evidence [real-result-path] --user-confirmed` |
| Phase 8 — Architect | RCA and optimization report | `pipeline.py architect --report-file architect_report.xml` |

### Constraints verified

- No direct push to `main` — all changes via PR from `smoke-test/IMP-20260509-A4DB`
- GitHub Actions CI (pytest + `pipeline_attestation.json` upload) must PASS before gate acceptance
- Three-Gate mode: Technical + Oracle (waived: docs-only) + GitHub CI + User Acceptance
- User acceptance deploys accepted artifacts to `G:\내 드라이브\터미널\<pipeline_id>` and writes `deployment_manifest.json`

### CI Workflow

The `.github/workflows/ci.yml` workflow runs on every PR and push to `main`:

1. Runs `python -m pytest -q` on `windows-latest`
2. On success, creates `pipeline_attestation.json` with run metadata
3. Uploads the attestation as a GitHub Actions artifact named `pipeline-attestation`

The `pipeline.py gates github-ci` command verifies the latest CI run against HEAD
and records the GitHub CI gate as PASS when the attestation is valid.
