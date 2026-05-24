"""Self-tests for tools/check_cli_evidence_contract.py (MT-2, BUG-20260523-C48A).

These tests verify that the ISOLATION_MARKERS / ACTUAL_ASSERTION_KEYWORDS split
works correctly:
  - isolation marker only  -> violation (exit 1)
  - isolation + assertion  -> OK (exit 0)
  - CLI_EVIDENCE_ALLOW_READ_ONLY annotation -> OK (exit 0)
  - CLI_EVIDENCE_ALLOW_READ_ONLY on state-changing command -> violation (exit 1)
"""

from __future__ import annotations

import textwrap
from pathlib import Path

# Import the function under test from the tools package.
from tools.check_cli_evidence_contract import scan_test_files


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_test_file(tmp_path: Path, content: str) -> Path:
    """Write a test_*.py file with the given content into tmp_path."""
    py_file = tmp_path / "test_evidence_sample.py"
    py_file.write_text(textwrap.dedent(content), encoding="utf-8")
    return py_file


# ---------------------------------------------------------------------------
# Bad cases (should produce violations)
# ---------------------------------------------------------------------------


def test_bad_isolation_only_run_cli(tmp_path: Path) -> None:
    """Function that uses run_cli_with_temp_state but has no evidence assertion.

    Expected: scan_test_files returns >= 1 violation (exit 1 equivalent).
    """
    _write_test_file(
        tmp_path,
        """\
        import subprocess

        def run_cli_with_temp_state(args):
            return subprocess.run(["python", "pipeline.py"] + args)

        def test_bad_isolation_only(tmp_path):
            # Only sets up isolation, no post-state assertion follows.
            run_cli_with_temp_state(["done", "--phase", "dev"])
            assert result.returncode == 0
        """,
    )
    violations = scan_test_files(tmp_path)
    assert len(violations) >= 1, (
        f"Expected at least 1 violation for isolation-only function, got {len(violations)}"
    )


def test_bad_isolation_only_pipeline_state_path(tmp_path: Path) -> None:
    """Function that uses PIPELINE_STATE_PATH but has no evidence assertion.

    Expected: scan_test_files returns >= 1 violation.
    """
    _write_test_file(
        tmp_path,
        """\
        import os
        import subprocess

        def test_bad_env_isolation_only(tmp_path):
            # Sets PIPELINE_STATE_PATH for isolation but never asserts on it.
            env = {**os.environ, "PIPELINE_STATE_PATH": str(tmp_path / "state.json")}
            result = subprocess.run(
                ["python", "pipeline.py", "done", "--phase", "dev"],
                capture_output=True,
                env=env,
            )
            assert result.returncode == 0
        """,
    )
    violations = scan_test_files(tmp_path)
    assert len(violations) >= 1, (
        f"Expected at least 1 violation for PIPELINE_STATE_PATH-only function, got {len(violations)}"
    )


# ---------------------------------------------------------------------------
# Good cases (should produce NO violations)
# ---------------------------------------------------------------------------


def test_good_isolation_with_assertion(tmp_path: Path) -> None:
    """Function that uses run_cli_with_temp_state AND assert_post_state.

    Expected: scan_test_files returns 0 violations (exit 0 equivalent).
    """
    _write_test_file(
        tmp_path,
        """\
        import subprocess

        def run_cli_with_temp_state(args):
            return subprocess.run(["python", "pipeline.py"] + args)

        def assert_post_state(state_path, expected):
            import json
            actual = json.loads(state_path.read_text())
            assert actual == expected

        def test_good_isolation_with_assertion(tmp_path):
            # Has both isolation helper AND post-state assertion.
            run_cli_with_temp_state(["done", "--phase", "dev"])
            assert_post_state(tmp_path / "state.json", {"phase": "dev"})
        """,
    )
    violations = scan_test_files(tmp_path)
    assert violations == [], (
        f"Expected 0 violations for isolation+assertion function, got {violations}"
    )


def test_good_read_only_annotation(tmp_path: Path) -> None:
    """Function annotated with CLI_EVIDENCE_ALLOW_READ_ONLY with a genuinely
    read-only subcommand (status) should be skipped — no violation.

    Expected: scan_test_files returns 0 violations (exit 0 equivalent).
    """
    _write_test_file(
        tmp_path,
        """\
        import subprocess

        def test_good_read_only_annotation(tmp_path):
            # CLI_EVIDENCE_ALLOW_READ_ONLY: read-only status check, no state change needed
            result = subprocess.run(
                ["python", "pipeline.py", "status"],
                capture_output=True,
            )
            assert result.returncode == 0
        """,
    )
    violations = scan_test_files(tmp_path)
    assert violations == [], (
        f"Expected 0 violations for CLI_EVIDENCE_ALLOW_READ_ONLY function, got {violations}"
    )


def test_bad_state_changing_with_annotation(tmp_path: Path) -> None:
    """CLI_EVIDENCE_ALLOW_READ_ONLY annotation does NOT exempt state-changing commands.

    A test that calls ``pipeline.py done`` (state-changing) and uses the
    read-only annotation is still a violation — the annotation is only valid
    for genuinely read-only subcommands like ``status`` or ``check``.

    Expected: scan_test_files returns >= 1 violation.
    """
    _write_test_file(
        tmp_path,
        """\
        import subprocess

        def test_bad_state_changing_with_annotation(tmp_path):
            # CLI_EVIDENCE_ALLOW_READ_ONLY: 상태 변경 명령에는 적용되지 않음
            result = subprocess.run(
                ["python", "pipeline.py", "done", "--phase", "dev"],
                capture_output=True,
            )
            assert result.returncode == 0
        """,
    )
    violations = scan_test_files(tmp_path)
    assert len(violations) >= 1, (
        f"Expected at least 1 violation when CLI_EVIDENCE_ALLOW_READ_ONLY is used "
        f"with a state-changing command ('done'), got {len(violations)}"
    )
