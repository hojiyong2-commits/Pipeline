"""Freeze task contracts once Discovery has produced testable requirements."""

from __future__ import annotations

from typing import Any, Mapping

from .readiness import readiness_report
from .schema import stable_hash, utc_now


def freeze_bundle(
    contract: Mapping[str, Any],
    test_set: Mapping[str, Any],
    *,
    force: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    report = readiness_report(contract, test_set)
    if not report["ready"] and not force:
        raise ValueError("contract is not ready: " + "; ".join(report["blockers"]))

    frozen_contract = dict(contract)
    frozen_test_set = dict(test_set)
    now = utc_now()

    frozen_contract["status"] = "frozen"
    frozen_contract["frozen"] = True
    frozen_contract["frozen_at"] = now
    frozen_contract["updated_at"] = now

    frozen_test_set["status"] = "frozen"
    frozen_test_set["frozen"] = True
    frozen_test_set["frozen_at"] = now
    frozen_test_set["updated_at"] = now

    frozen_contract["contract_hash"] = stable_hash(frozen_contract)
    frozen_test_set["test_set_hash"] = stable_hash(frozen_test_set)
    frozen_contract["test_set_hash"] = frozen_test_set["test_set_hash"]
    frozen_test_set["contract_hash"] = frozen_contract["contract_hash"]

    return frozen_contract, frozen_test_set, report
