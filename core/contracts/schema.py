"""Schema and file helpers for pipeline v2 task contracts.

The schema is intentionally simple JSON so PM/user decisions can be audited
without needing a database or an external service.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

CONTRACT_SCHEMA_VERSION = 1
TEST_SET_SCHEMA_VERSION = 1

QUESTION_SEVERITIES = {"P0", "P1", "P2"}
TEST_PRIORITIES = {"P0", "P1", "P2"}
SUPPORTED_TEST_TYPES = {
    "command_check",
    "csv_row_match",
    "email_parse_check",
    "excel_row_match",
    "excel_schema_check",
    "exe_launch_check",
    "file_exists_check",
    "file_output_check",
    "json_exact_match",
    "mapping_rule_check",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def contract_dir(base_dir: Path, pipeline_id: str) -> Path:
    if not pipeline_id or any(part in pipeline_id for part in ("..", "/", "\\")):
        raise ValueError(f"invalid pipeline_id: {pipeline_id!r}")
    return base_dir / "pipeline_contracts" / pipeline_id


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def save_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
        ) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        os.replace(str(tmp_path), str(path))
    except Exception:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _canonical_for_hash(data: Mapping[str, Any]) -> dict[str, Any]:
    cloned = copy.deepcopy(dict(data))
    cloned.pop("contract_hash", None)
    cloned.pop("test_set_hash", None)
    cloned.pop("frozen_at", None)
    cloned.pop("updated_at", None)
    return cloned


def stable_hash(data: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _canonical_for_hash(data),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_initial_contract(pipeline_id: str, description: str, pipeline_type: str = "FEAT") -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "pipeline_id": pipeline_id,
        "pipeline_type": pipeline_type.upper(),
        "created_at": now,
        "updated_at": now,
        "status": "draft",
        "frozen": False,
        "goal": description,
        "task_profile": {
            "domain": "general",
            "task_kind": None,
            "deliverable_kind": None,
            "success_signal": [],
        },
        "environment": {
            "target_os": "Windows",
            "target_python": None,
            "constraints": [],
            "external_connectors_allowed": None,
        },
        "deliverables": [],
        "build_target": {
            "required": None,
            "type": None,
            "notes": "Set required=true for runnable apps/packages; use false for docs, prompts, analysis, or MD-only work.",
        },
        "execution": {
            "mode": None,
            "entrypoint": None,
            "user_trigger": None,
        },
        "modules": [],
        "questions": [],
        "assumptions": [],
        "acceptance_threshold": 80,
        "definition_of_ready": {
            "min_normal_tests_per_module": 1,
            "min_edge_cases_total": 1,
            "allow_p1_defaults": True,
        },
    }


def build_initial_test_set(pipeline_id: str) -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": TEST_SET_SCHEMA_VERSION,
        "pipeline_id": pipeline_id,
        "created_at": now,
        "updated_at": now,
        "status": "draft",
        "frozen": False,
        "tests": [],
    }


def validate_contract_shape(contract: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if contract.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {CONTRACT_SCHEMA_VERSION}")
    if not str(contract.get("pipeline_id", "")).strip():
        errors.append("pipeline_id is required")
    if not str(contract.get("goal", "")).strip():
        errors.append("goal is required")
    if not isinstance(contract.get("modules"), list):
        errors.append("modules must be a list")
    if not isinstance(contract.get("questions"), list):
        errors.append("questions must be a list")
    if not isinstance(contract.get("task_profile"), dict):
        errors.append("task_profile must be an object")
    if not isinstance(contract.get("environment"), dict):
        errors.append("environment must be an object")
    if not isinstance(contract.get("deliverables"), list):
        errors.append("deliverables must be a list")
    if not isinstance(contract.get("build_target"), dict):
        errors.append("build_target must be an object")
    if not isinstance(contract.get("execution"), dict):
        errors.append("execution must be an object")

    for index, question in enumerate(contract.get("questions", [])):
        if not isinstance(question, dict):
            errors.append(f"questions[{index}] must be an object")
            continue
        severity = str(question.get("severity", "")).upper()
        if severity not in QUESTION_SEVERITIES:
            errors.append(f"questions[{index}].severity must be one of {sorted(QUESTION_SEVERITIES)}")
        if not str(question.get("question", "")).strip():
            errors.append(f"questions[{index}].question is required")

    for index, module in enumerate(contract.get("modules", [])):
        if not isinstance(module, dict):
            errors.append(f"modules[{index}] must be an object")
            continue
        if not str(module.get("id", "")).strip():
            errors.append(f"modules[{index}].id is required")
        if not str(module.get("name", "")).strip():
            errors.append(f"modules[{index}].name is required")
        for key in ("inputs", "outputs", "acceptance_rules", "exceptions"):
            if key not in module or not isinstance(module.get(key), list):
                errors.append(f"modules[{index}].{key} must be a list")
        if "business_rules" in module and "acceptance_rules" not in module:
            errors.append(f"modules[{index}] uses legacy business_rules; rename to acceptance_rules")
    return errors


def validate_test_set_shape(test_set: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if test_set.get("schema_version") != TEST_SET_SCHEMA_VERSION:
        errors.append(f"schema_version must be {TEST_SET_SCHEMA_VERSION}")
    if not str(test_set.get("pipeline_id", "")).strip():
        errors.append("pipeline_id is required")
    tests = test_set.get("tests")
    if not isinstance(tests, list):
        errors.append("tests must be a list")
        return errors

    seen_ids: set[str] = set()
    for index, test in enumerate(tests):
        if not isinstance(test, dict):
            errors.append(f"tests[{index}] must be an object")
            continue
        test_id = str(test.get("id", "")).strip()
        if not test_id:
            errors.append(f"tests[{index}].id is required")
        elif test_id in seen_ids:
            errors.append(f"duplicate test id: {test_id}")
        seen_ids.add(test_id)
        test_type = str(test.get("type", "")).strip()
        if test_type not in SUPPORTED_TEST_TYPES:
            errors.append(f"tests[{index}].type unsupported: {test_type}")
        priority = str(test.get("priority", "P1")).upper()
        if priority not in TEST_PRIORITIES:
            errors.append(f"tests[{index}].priority must be one of {sorted(TEST_PRIORITIES)}")
        points = test.get("points", 1)
        if not isinstance(points, int) or points < 0:
            errors.append(f"tests[{index}].points must be a non-negative integer")
    return errors
