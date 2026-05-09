"""Acceptance harness runner for frozen pipeline contracts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.contracts.readiness import readiness_report
from core.contracts.schema import load_json, stable_hash, validate_test_set_shape

from .scorers import SCORERS, ScoringError


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _status_for_test(test: dict[str, Any], contract_frozen: bool, test_set_frozen: bool) -> tuple[bool, str]:
    if not contract_frozen or not test_set_frozen:
        return False, "contract and test_set must be frozen before acceptance run"
    if not isinstance(test, dict):
        return False, "test must be an object"
    if test.get("disabled") is True:
        return False, "test is disabled"
    return True, ""


def run_acceptance(
    *,
    contract_path: Path,
    test_set_path: Path,
    project_dir: Path,
    output_path: Path | None = None,
) -> dict[str, Any]:
    contract = load_json(contract_path)
    test_set = load_json(test_set_path)
    contract_frozen = bool(contract.get("frozen"))
    test_set_frozen = bool(test_set.get("frozen"))

    readiness = readiness_report(contract, test_set)
    shape_errors = validate_test_set_shape(test_set)
    tests = test_set.get("tests", [])
    if not isinstance(tests, list):
        tests = []

    results: list[dict[str, Any]] = []
    total_points = 0
    earned_points = 0
    hard_fail = False

    base_dir = test_set_path.parent
    for index, test in enumerate(tests):
        if not isinstance(test, dict):
            results.append({
                "id": f"invalid-{index}",
                "status": "FAIL",
                "points": 0,
                "earned": 0,
                "message": "test entry must be an object",
            })
            hard_fail = True
            continue

        test_id = str(test.get("id") or f"test-{index}")
        points = int(test.get("points", 0))
        priority = str(test.get("priority", "P1")).upper()
        total_points += points

        runnable, reason = _status_for_test(test, contract_frozen, test_set_frozen)
        if not runnable:
            status = "SKIP" if test.get("disabled") is True else "FAIL"
            if status == "FAIL" and priority == "P0":
                hard_fail = True
            results.append({
                "id": test_id,
                "type": test.get("type"),
                "priority": priority,
                "status": status,
                "points": points,
                "earned": 0,
                "message": reason,
            })
            continue

        scorer = SCORERS.get(str(test.get("type", "")))
        if scorer is None:
            ok = False
            message = f"unsupported test type: {test.get('type')}"
            details: dict[str, Any] = {}
        else:
            try:
                ok, message, details = scorer(test, base_dir, project_dir)
            except (OSError, ValueError, TimeoutError, ScoringError) as exc:
                ok = False
                message = str(exc)
                details = {"error": type(exc).__name__}

        earned = points if ok else 0
        earned_points += earned
        if not ok and priority == "P0":
            hard_fail = True
        results.append({
            "id": test_id,
            "module": test.get("module"),
            "type": test.get("type"),
            "priority": priority,
            "case_kind": test.get("case_kind", "normal"),
            "status": "PASS" if ok else "FAIL",
            "points": points,
            "earned": earned,
            "message": message,
            "details": details,
        })

    observed_contract_hash = stable_hash(contract)
    observed_test_set_hash = stable_hash(test_set)
    integrity_errors: list[str] = []
    if contract.get("contract_hash") and contract.get("contract_hash") != observed_contract_hash:
        integrity_errors.append("contract_hash mismatch")
    if test_set.get("test_set_hash") and test_set.get("test_set_hash") != observed_test_set_hash:
        integrity_errors.append("test_set_hash mismatch")
    if integrity_errors:
        hard_fail = True

    score = round((earned_points / total_points) * 100, 2) if total_points else 0.0
    threshold = int(contract.get("acceptance_threshold", 80))
    verdict = "PASS" if score >= threshold and not hard_fail and not shape_errors and not integrity_errors else "FAIL"
    report = {
        "schema_version": 1,
        "generated_at": _now(),
        "pipeline_id": contract.get("pipeline_id"),
        "contract_hash": contract.get("contract_hash"),
        "test_set_hash": test_set.get("test_set_hash"),
        "observed_contract_hash": observed_contract_hash,
        "observed_test_set_hash": observed_test_set_hash,
        "readiness": readiness,
        "shape_errors": shape_errors,
        "integrity_errors": integrity_errors,
        "summary": {
            "verdict": verdict,
            "score": score,
            "threshold": threshold,
            "earned_points": earned_points,
            "total_points": total_points,
            "hard_fail": hard_fail,
            "passed": sum(1 for item in results if item["status"] == "PASS"),
            "failed": sum(1 for item in results if item["status"] == "FAIL"),
            "skipped": sum(1 for item in results if item["status"] == "SKIP"),
        },
        "results": results,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
