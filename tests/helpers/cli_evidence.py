"""CLI Evidence Contract helpers for pipeline CLI integration tests.

These helpers provide isolated subprocess execution of pipeline.py commands
by routing PIPELINE_STATE_PATH to a temporary directory, capturing the
resulting state changes, and exposing assertion utilities.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


@dataclasses.dataclass
class CliResult:
    """Result of a pipeline CLI invocation in an isolated temp directory."""

    returncode: int
    output: str
    final_state: dict
    temp_dir: Path


def run_cli_with_temp_state(
    initial_state: dict,
    command: list[str],
    extra_files: dict[str, str] | None = None,
) -> CliResult:
    """Run a pipeline CLI command with an isolated pipeline_state.json.

    Creates a temporary directory, writes ``initial_state`` as
    ``pipeline_state.json``, executes ``command`` with
    ``PIPELINE_STATE_PATH`` pointing at that file, and returns a
    :class:`CliResult` containing the exit code, combined stdout/stderr
    output, the final (post-command) state, and the temp directory path.

    The caller is responsible for cleaning up the temp directory when
    finished (or passing the result through a context manager).

    Args:
        initial_state: Dict written verbatim to ``pipeline_state.json``
            before the command runs.
        command: Argument list forwarded to :func:`subprocess.run` (e.g.
            ``["python", "pipeline.py", "revert"]``).
        extra_files: Optional mapping of relative path → file content
            (str) for additional files that should exist in the temp
            directory before the command runs.

    Returns:
        :class:`CliResult` with ``returncode``, ``output``,
        ``final_state``, and ``temp_dir``.
    """
    tmp = tempfile.mkdtemp(prefix="pipeline_cli_test_")
    tmp_path = Path(tmp)
    state_file = tmp_path / "pipeline_state.json"

    # Write initial state
    state_file.write_text(json.dumps(initial_state, ensure_ascii=False, indent=2), encoding="utf-8")

    # Write any extra files
    if extra_files:
        for rel_path, content in extra_files.items():
            target = tmp_path / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    # Build environment: inherit current env, override PIPELINE_STATE_PATH
    env = os.environ.copy()
    env["PIPELINE_STATE_PATH"] = str(state_file)

    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    combined_output = (proc.stdout or "") + (proc.stderr or "")

    # Read final state (may have been modified by the command)
    try:
        final_state = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        final_state = {}

    return CliResult(
        returncode=proc.returncode,
        output=combined_output,
        final_state=final_state,
        temp_dir=tmp_path,
    )


def _deep_match(actual: Any, expected: Any, path: str = "") -> list[str]:
    """Recursively compare actual vs expected and collect mismatch messages."""
    mismatches: list[str] = []

    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            mismatches.append(f"{path}: expected dict, got {type(actual).__name__!r}")
            return mismatches
        for key, exp_val in expected.items():
            key_path = f"{path}.{key}" if path else key
            if key not in actual:
                mismatches.append(f"{key_path}: key missing in actual state")
            else:
                mismatches.extend(_deep_match(actual[key], exp_val, key_path))
    else:
        if actual != expected:
            mismatches.append(f"{path}: expected {expected!r}, got {actual!r}")

    return mismatches


def assert_post_state(result: CliResult, expected: dict) -> None:
    """Assert that ``result.final_state`` contains all key/values in ``expected``.

    Performs a deep (nested) partial match: every key/value in ``expected``
    must exist and match in ``result.final_state``, but ``final_state`` may
    contain additional keys not present in ``expected``.

    Args:
        result: A :class:`CliResult` returned by :func:`run_cli_with_temp_state`.
        expected: Dict of expected key/value pairs (supports nested dicts).

    Raises:
        AssertionError: If any expected key is missing or its value does not
            match, including a human-readable diff of all mismatches.
    """
    mismatches = _deep_match(result.final_state, expected)
    if mismatches:
        diff_lines = "\n  ".join(mismatches)
        raise AssertionError(
            f"post-state mismatch ({len(mismatches)} difference(s)):\n  {diff_lines}\n"
            f"full final_state:\n{json.dumps(result.final_state, indent=2, ensure_ascii=False)}"
        )


def assert_file_effects(
    result: CliResult,
    expected_files: dict[str, dict],
) -> None:
    """Assert file existence and content within ``result.temp_dir``.

    Args:
        result: A :class:`CliResult` returned by :func:`run_cli_with_temp_state`.
        expected_files: Mapping of relative file path → assertion spec dict.
            Supported spec keys:

            - ``"exists"`` (bool): Whether the file should exist.
            - ``"contains"`` (list[str]): Substrings that must appear in the
              file content (all must match).
            - ``"not_contains"`` (list[str]): Substrings that must NOT appear.

    Raises:
        AssertionError: If any file assertion fails.
    """
    errors: list[str] = []

    for rel_path, spec in expected_files.items():
        file_path = result.temp_dir / rel_path
        should_exist = spec.get("exists", True)

        if should_exist and not file_path.exists():
            errors.append(f"{rel_path}: expected to exist but was not found")
            continue

        if not should_exist and file_path.exists():
            errors.append(f"{rel_path}: expected NOT to exist but was found")
            continue

        if not should_exist:
            # File correctly absent; no further checks needed.
            continue

        # File exists and is expected; check content assertions.
        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{rel_path}: could not read file: {exc}")
            continue

        for needle in spec.get("contains", []):
            if needle not in content:
                errors.append(f"{rel_path}: expected to contain {needle!r} but did not")

        for needle in spec.get("not_contains", []):
            if needle in content:
                errors.append(f"{rel_path}: expected NOT to contain {needle!r} but did")

    if errors:
        error_lines = "\n  ".join(errors)
        raise AssertionError(
            f"file effects mismatch ({len(errors)} issue(s)):\n  {error_lines}"
        )
