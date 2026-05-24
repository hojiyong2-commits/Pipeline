"""CLI Evidence Contract linter.

Scans test files under tests/ for pipeline CLI invocations that change
pipeline state (e.g. ``pipeline.py done``, ``pipeline.py qa``) and verifies
that each test either:
  1. Uses the new helper-based isolation (``run_cli_with_temp_state`` or
     ``PIPELINE_STATE_PATH`` environment injection), AND includes a
     post-state/file-effects assertion (``assert_post_state``,
     ``assert_file_effects``, or one of the recognised evidence-assertion
     keywords), OR
  2. Has an explicit read-only annotation:
     ``# CLI_EVIDENCE_ALLOW_READ_ONLY: <reason>``

Exit codes:
  0 -no violations
  1 -one or more violations found

Usage::

    python tools/check_cli_evidence_contract.py [--path tests/] [--verbose]
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sub-commands that change pipeline state and therefore require evidence.
STATE_CHANGING_SUBCOMMANDS: frozenset[str] = frozenset(
    [
        "new",
        "done",
        "qa",
        "sec",
        "build",
        "architect",
        "contract",
        "module",
        "gates",
        "advisory",
        "acceptance",
        "outputs",
        "review",
        "agent",
        "patch",
        "cluster",
        "tournament",
    ]
)

# Sub-commands that are read-only and do not require evidence.
READ_ONLY_SUBCOMMANDS: frozenset[str] = frozenset(
    [
        "status",
        "check",
        "list",
        "help",
        "--help",
        "-h",
        "interface",
        "metrics",
        "dry-run",
    ]
)

# Keywords that indicate isolation/helper setup — NOT evidence assertions.
# A function with only these markers (and no ACTUAL_ASSERTION_KEYWORDS) is a violation.
ISOLATION_MARKERS: tuple[str, ...] = (
    "run_cli_with_temp_state",
    "PIPELINE_STATE_PATH",
)

# Keywords in source text that satisfy the evidence-assertion requirement.
# "pipeline_state" and "post_state" removed — too generic, causes false negatives.
ACTUAL_ASSERTION_KEYWORDS: tuple[str, ...] = (
    "assert_post_state",
    "assert_file_effects",
    "final_state",
    "event_log",
    "phase_attempt_history",
    "failure_packets",
    "failure_packet",
    "artifact_manifest",
    "created_files",
    "modified_files",
)

# Annotation that marks a test as intentionally read-only.
ALLOW_READ_ONLY_PATTERN = re.compile(
    r"#\s*CLI_EVIDENCE_ALLOW_READ_ONLY\s*:", re.IGNORECASE
)

# Regex to detect ``pipeline.py`` CLI calls in string literals or subprocess
# arguments.  We match strings that contain "pipeline.py" followed by a
# subcommand token.
PIPELINE_CALL_RE = re.compile(r'pipeline\.py["\s,]+(\w[\w-]*)')

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class Violation(NamedTuple):
    file: Path
    line: int
    test_name: str
    subcommand: str
    message: str


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _enclosing_function(node: ast.AST, ancestors: list[ast.AST]) -> str:
    """Return the name of the innermost function/class enclosing *node*."""
    for parent in reversed(ancestors):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent.name
        if isinstance(parent, ast.ClassDef):
            return parent.name
    return "<module>"


def _string_value(node: ast.expr) -> str | None:
    """Extract a string value from a Constant or JoinedStr node (best-effort)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _collect_string_literals(tree: ast.AST) -> list[str]:
    """Collect all string literals in the AST."""
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------


def classify_command(subcommand: str) -> str:
    """Classify a pipeline.py subcommand.

    Returns:
        ``"state_changing"`` | ``"read_only"`` | ``"unknown"``
    """
    sub = subcommand.strip().lower()
    if sub in STATE_CHANGING_SUBCOMMANDS:
        return "state_changing"
    if sub in READ_ONLY_SUBCOMMANDS:
        return "read_only"
    return "unknown"


def check_evidence_assertions(source: str) -> bool:
    """Return True if the source text has BOTH isolation markers AND evidence assertions.

    A function that only sets up isolation (run_cli_with_temp_state /
    PIPELINE_STATE_PATH) but never asserts on post-state is considered a
    violation — isolation alone is NOT evidence.
    """
    has_isolation = any(kw in source for kw in ISOLATION_MARKERS)
    has_assertion = any(kw in source for kw in ACTUAL_ASSERTION_KEYWORDS)
    return has_isolation and has_assertion


def _has_pipeline_wrapper(tree: ast.AST) -> bool:
    """Return True if the module defines a helper function that wraps pipeline.py.

    We look for module-level functions whose body contains a subprocess call
    that concatenates/appends to a ``pipeline.py`` variable.  This covers
    the ``def _run(args) -> subprocess.run([sys.executable, _PIPELINE] + args)``
    pattern commonly used in test files.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Only consider short module-level helpers (not test functions).
        if node.name.startswith("test_"):
            continue
        func_src = ast.unparse(node)
        if "pipeline.py" in func_src or "_PIPELINE" in func_src or "PIPELINE" in func_src:
            if "subprocess" in func_src or "run(" in func_src or "Popen" in func_src:
                return True
    return False


def _find_pipeline_calls_in_function(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
    has_wrapper: bool = False,
) -> list[tuple[int, str]]:
    """Return (lineno, subcommand) for each state-changing pipeline.py call.

    We detect two common patterns:

    1. Direct pipeline.py invocation in a list literal:
       ``subprocess.run(["python", "pipeline.py", "done", ...])``

    2. Helper function calls where the first list element is the subcommand
       (only when ``has_wrapper=True``):
       ``_run(["cluster", "init", ...])`` or ``_run(["patch", "plan", ...])``.
       This covers files that wrap pipeline.py calls in a ``_run``/``_cli``
       helper and pass only the subcommand args.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(func_node):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        elems = [_string_value(elt) for elt in node.elts]
        if not elems:
            continue

        handled = False
        # Pattern 1: explicit "pipeline.py" string in the list.
        for i, val in enumerate(elems):
            if val is None:
                continue
            if "pipeline.py" in val:
                sub = None
                for j in range(i + 1, len(elems)):
                    if elems[j] is not None:
                        sub = elems[j]
                        break
                if sub and classify_command(sub) == "state_changing":
                    found.append((node.lineno, sub))
                handled = True
                break

        # Pattern 2: first element is a known state-changing subcommand.
        # Only apply when the file has a known pipeline.py wrapper helper.
        if not handled and has_wrapper and elems[0] is not None:
            if classify_command(elems[0]) == "state_changing":
                found.append((node.lineno, elems[0]))

    return found


def _source_for_function(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    source_lines: list[str],
) -> str:
    """Return the raw source text for a function node."""
    start = func_node.lineno - 1
    end = func_node.end_lineno or len(source_lines)
    return "\n".join(source_lines[start:end])


def scan_test_files(test_dir: Path, verbose: bool = False) -> list[Violation]:
    """Scan all test_*.py files under *test_dir* and return violations."""
    violations: list[Violation] = []

    for py_file in sorted(test_dir.rglob("test_*.py")):
        source = py_file.read_text(encoding="utf-8", errors="replace")
        source_lines = source.splitlines()

        # Fast path: if the file contains no pipeline.py references at all, skip.
        if "pipeline.py" not in source:
            continue

        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            if verbose:
                print(f"[WARN] syntax error in {py_file}, skipping")
            continue

        # Detect whether the file has a pipeline.py wrapper helper (_run, etc.).
        has_wrapper = _has_pipeline_wrapper(tree)

        # Collect all top-level function definitions (including class methods).
        func_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_nodes.append(node)

        for func_node in func_nodes:
            calls = _find_pipeline_calls_in_function(func_node, source_lines, has_wrapper=has_wrapper)
            if not calls:
                continue

            func_source = _source_for_function(func_node, source_lines)

            # Check if the function (or its file) has a read-only annotation.
            has_allowlist = ALLOW_READ_ONLY_PATTERN.search(func_source) is not None

            # Check if evidence assertions are present in the function body.
            has_evidence = check_evidence_assertions(func_source)

            for lineno, subcommand in calls:
                if has_allowlist:
                    # Allowed read-only: no violation.
                    if verbose:
                        print(
                            f"  SKIP  {py_file.name}:{lineno} "
                            f"{func_node.name!r} ({subcommand}) - CLI_EVIDENCE_ALLOW_READ_ONLY"
                        )
                    continue

                if has_evidence:
                    # Evidence assertions present: no violation.
                    if verbose:
                        print(
                            f"  OK    {py_file.name}:{lineno} "
                            f"{func_node.name!r} ({subcommand}) - evidence found"
                        )
                    continue

                # Violation: state-changing CLI call without evidence assertion.
                violations.append(
                    Violation(
                        file=py_file,
                        line=lineno,
                        test_name=func_node.name,
                        subcommand=subcommand,
                        message=(
                            f"isolation marker (run_cli_with_temp_state / PIPELINE_STATE_PATH) "
                            f"is present but no actual assertion keyword was found "
                            f"(assert_post_state / assert_file_effects / final_state / "
                            f"event_log / ...) for 'pipeline.py {subcommand}'. "
                            "Add a post-state assertion or annotate with "
                            "# CLI_EVIDENCE_ALLOW_READ_ONLY: <reason>"
                        ),
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point.  Returns exit code (0 = clean, 1 = violations)."""
    parser = argparse.ArgumentParser(
        description="CLI Evidence Contract linter for pipeline test files."
    )
    parser.add_argument(
        "--path",
        default="tests",
        help="Root directory to scan (default: tests/)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-function status for all scanned functions.",
    )
    args = parser.parse_args(argv)

    test_dir = Path(args.path)
    if not test_dir.exists():
        print(f"[ERROR] path not found: {test_dir}", file=sys.stderr)
        return 1

    violations = scan_test_files(test_dir, verbose=args.verbose)

    if not violations:
        print("[CLI EVIDENCE CONTRACT] PASS -no violations found")
        return 0

    print(
        f"[CLI EVIDENCE CONTRACT] FAIL -{len(violations)} violation(s) found:\n",
        file=sys.stderr,
    )
    for v in violations:
        rel = v.file.relative_to(Path.cwd()) if v.file.is_relative_to(Path.cwd()) else v.file
        print(
            f"  {rel}:{v.line}  {v.test_name!r}  subcommand={v.subcommand!r}\n"
            f"    {v.message}\n",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
