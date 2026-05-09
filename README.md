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
3. On pull requests, creates `human_acceptance_packet.md`, uploads it as `human-acceptance-packet`, and posts/updates a PR comment with the same packet.
4. If `.pipeline/phase_attestation_request.json` exists, validates the phase evidence and creates `phase_attestation.json`
5. Uploads the attestations as GitHub Actions artifacts named `pipeline-attestation` and, when requested, `pipeline-phase-attestation`

The `pipeline.py gates github-ci` command verifies the latest CI run against HEAD
and records the GitHub CI gate as PASS when the attestation is valid.
The `pipeline.py gates phase-ci --phase <pm|dev|qa|build>` command verifies the
per-phase GitHub attestation before the next role boundary may proceed.

### Final user decision

The user's final job should be only this:

1. Open the PR link provided by the final agent.
2. Read the **Human Acceptance Packet** PR comment.
3. Open the result link/path or artifact named in that packet.
4. Choose ACCEPT or REJECT in Claude Code/dashboard.

The packet is generated by GitHub Actions and summarizes CI status, changed
files, trust-root/test/oracle files touched, and the Actions artifact link. This
keeps final approval focused on the visible result instead of code review.

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
