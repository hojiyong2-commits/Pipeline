import json
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent


def _copy_harness_fixture(tmp_path: Path) -> Path:
    """Create an isolated project root for the legacy harness gate smoke test."""
    work = tmp_path / "workspace"
    (work / ".claude" / "commands").mkdir(parents=True)
    (work / ".claude" / "agents").mkdir(parents=True)
    shutil.copy2(PROJECT_DIR / "pipeline.py", work / "pipeline.py")
    shutil.copy2(PROJECT_DIR / ".claude" / "commands" / "agents.md", work / ".claude" / "commands" / "agents.md")
    shutil.copy2(PROJECT_DIR / ".claude" / "agents" / "qa-agent.md", work / ".claude" / "agents" / "qa-agent.md")

    state = {
        "pipeline_id": "TEST-HARNESS-6198",
        "type": "BUG",
        "description": "legacy harness gate isolation test",
        "current_phase": "harness",
        "terminal_state": None,
        "blocked": False,
        "phases": {
            "pm": {"status": "DONE"},
            "dev": {"status": "DONE"},
            "qa": {"status": "PASS"},
            "sec": {"status": "SKIP"},
            "build": {"status": "DONE"},
            "harness": {"status": "PENDING"},
            "architect": {"status": "PENDING"},
        },
        "external_gates": {"enabled": True},
        "phase_attestations": {
            "enabled": True,
            "phases": {
                "build": {
                    "phase": "build",
                    "status": "PASS",
                    "run_id": "test-build-run",
                    "completed_at": "2026-05-10T00:00:00Z",
                }
            },
        },
        "module_gates": {
            "enabled": True,
            "sequence": [],
            "modules": {},
            "integration": {"status": "PASS"},
        },
        "event_log": [],
    }
    (work / "pipeline_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return work


def _run_pipeline(work: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "pipeline.py", *args],
        cwd=work,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_legacy_harness_phase7_user_confirmation_and_docs(tmp_path: Path) -> None:
    work = _copy_harness_fixture(tmp_path)

    r1 = _run_pipeline(work, "check", "--phase", "harness")
    assert r1.returncode != 0, r1.stdout + r1.stderr

    r2 = _run_pipeline(work, "check", "--phase", "harness", "--user-confirmed")
    assert r2.returncode == 0, r2.stdout + r2.stderr

    r3 = _run_pipeline(work, "harness", "--score", "100", "--verdict", "PASS", "--user-confirmed")
    assert r3.returncode != 0, r3.stdout + r3.stderr
    assert "external_gates.enabled=true" in (r3.stdout + r3.stderr)

    agents_md = (work / ".claude" / "commands" / "agents.md").read_text(encoding="utf-8")
    assert "<status>BUILD SUCCESS</status>" in agents_md

    qa_md = (work / ".claude" / "agents" / "qa-agent.md").read_text(encoding="utf-8")
    assert "PASS/FAIL" in qa_md and "numeric-score" in qa_md

    pipeline_py = (work / "pipeline.py").read_text(encoding="utf-8")
    assert '"<status>BUILD SUCCESS</status>"' in pipeline_py
