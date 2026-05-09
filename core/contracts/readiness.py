"""Definition-of-ready checks for task contracts."""

from __future__ import annotations

from typing import Any, Mapping

from .schema import validate_contract_shape, validate_test_set_shape


def _resolved(question: Mapping[str, Any]) -> bool:
    if bool(question.get("resolved")):
        return True
    answer = str(question.get("answer", "")).strip()
    default = str(question.get("approved_default", "")).strip()
    return bool(answer or default)


def readiness_report(
    contract: Mapping[str, Any],
    test_set: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {}

    blockers.extend(validate_contract_shape(contract))
    if test_set is not None:
        blockers.extend(validate_test_set_shape(test_set))
        if contract.get("pipeline_id") != test_set.get("pipeline_id"):
            blockers.append("contract/test_set pipeline_id mismatch")

    modules = contract.get("modules", [])
    module_ids = {
        str(module.get("id", "")).strip()
        for module in modules
        if isinstance(module, dict) and str(module.get("id", "")).strip()
    }
    metrics["module_count"] = len(module_ids)
    if not module_ids:
        blockers.append("at least one module/component is required")

    task_profile = contract.get("task_profile", {})
    deliverable_kind = ""
    if isinstance(task_profile, dict):
        deliverable_kind = str(task_profile.get("deliverable_kind") or "").lower()
    runnable_kinds = {"app", "automation", "script", "tool", "webapp", "package", "cli", "extension"}

    execution = contract.get("execution", {})
    build_target = contract.get("build_target", {})
    build_required = isinstance(build_target, dict) and build_target.get("required") is True
    runnable_deliverable = build_required or deliverable_kind in runnable_kinds
    if isinstance(execution, dict):
        if runnable_deliverable and not str(execution.get("mode") or "").strip():
            blockers.append("execution.mode is required")
        if runnable_deliverable and not str(execution.get("entrypoint") or "").strip():
            warnings.append("execution.entrypoint is not set yet")

    if isinstance(build_target, dict) and build_target.get("required") is True:
        if not str(build_target.get("type") or "").strip():
            blockers.append("build_target.type is required when build is required")
    if isinstance(build_target, dict) and build_target.get("required") is None and runnable_deliverable:
        warnings.append("build_target.required is not decided for a runnable deliverable")

    unresolved_p0 = []
    unresolved_p1 = []
    for question in contract.get("questions", []):
        if not isinstance(question, dict):
            continue
        severity = str(question.get("severity", "")).upper()
        if severity == "P0" and not _resolved(question):
            unresolved_p0.append(question.get("id") or question.get("question"))
        if severity == "P1" and not _resolved(question):
            unresolved_p1.append(question.get("id") or question.get("question"))
    metrics["unresolved_p0_questions"] = len(unresolved_p0)
    metrics["unresolved_p1_questions"] = len(unresolved_p1)
    if unresolved_p0:
        blockers.append(f"unresolved P0 questions: {len(unresolved_p0)}")

    dor = contract.get("definition_of_ready", {})
    if not isinstance(dor, dict):
        dor = {}
    min_normal = int(dor.get("min_normal_tests_per_module", 1))
    min_edge = int(dor.get("min_edge_cases_total", 3))
    allow_p1_defaults = bool(dor.get("allow_p1_defaults", True))
    if unresolved_p1 and not allow_p1_defaults:
        blockers.append(f"unresolved P1 questions without approved defaults: {len(unresolved_p1)}")
    elif unresolved_p1:
        warnings.append(f"unresolved P1 questions remain: {len(unresolved_p1)}")

    tests = test_set.get("tests", []) if isinstance(test_set, dict) else []
    normal_by_module = {module_id: 0 for module_id in module_ids}
    edge_count = 0
    p0_test_count = 0
    if isinstance(tests, list):
        for test in tests:
            if not isinstance(test, dict):
                continue
            module_id = str(test.get("module", "")).strip()
            case_kind = str(test.get("case_kind", "normal")).lower()
            priority = str(test.get("priority", "P1")).upper()
            if module_id in normal_by_module and case_kind == "normal":
                normal_by_module[module_id] += 1
            if case_kind in {"edge", "exception", "error"}:
                edge_count += 1
            if priority == "P0":
                p0_test_count += 1

    metrics["normal_tests_by_module"] = normal_by_module
    metrics["edge_case_tests"] = edge_count
    metrics["p0_tests"] = p0_test_count

    if test_set is None:
        blockers.append("test_set.json is required before Dev")
    else:
        for module_id, count in normal_by_module.items():
            if count < min_normal:
                blockers.append(
                    f"module {module_id} needs at least {min_normal} normal acceptance test(s)"
                )
        if edge_count < min_edge:
            blockers.append(f"at least {min_edge} edge/exception tests are required")
        if p0_test_count == 0:
            blockers.append("at least one P0 acceptance test is required")

    return {
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "metrics": metrics,
    }
