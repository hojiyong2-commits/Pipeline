import tempfile
import unittest
from pathlib import Path

from core.acceptance import run_acceptance
from core.contracts import (
    build_initial_contract,
    build_initial_test_set,
    freeze_bundle,
    readiness_report,
    save_json_atomic,
)


class ContractAcceptanceV2Tests(unittest.TestCase):
    def test_readiness_blocks_until_module_and_tests_exist(self) -> None:
        contract = build_initial_contract("TMP-READY", "generic task")
        test_set = build_initial_test_set("TMP-READY")

        report = readiness_report(contract, test_set)
        self.assertFalse(report["ready"])
        self.assertTrue(any("module" in item for item in report["blockers"]))

        contract["modules"].append({
            "id": "M1",
            "name": "Generic module",
            "inputs": [],
            "outputs": [],
            "acceptance_rules": [],
            "exceptions": [],
        })
        report = readiness_report(contract, test_set)
        self.assertFalse(report["ready"])
        self.assertTrue(any("normal acceptance test" in item for item in report["blockers"]))

    def test_freeze_and_acceptance_run_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path = root / "task_contract.json"
            test_set_path = root / "test_set.json"
            result_path = root / "acceptance_result.json"

            contract = build_initial_contract("TMP-ACCEPT", "generic task")
            contract["modules"].append({
                "id": "M1",
                "name": "Generic module",
                "inputs": [],
                "outputs": [],
                "acceptance_rules": [],
                "exceptions": [],
            })
            test_set = build_initial_test_set("TMP-ACCEPT")
            test_set["tests"].extend([
                {
                    "id": "T1",
                    "module": "M1",
                    "type": "json_exact_match",
                    "priority": "P0",
                    "case_kind": "normal",
                    "points": 100,
                    "given": {"actual": {"ok": True}},
                    "when": {},
                    "then": {"expected": {"ok": True}},
                },
                {
                    "id": "T2",
                    "module": "M1",
                    "type": "json_exact_match",
                    "priority": "P1",
                    "case_kind": "edge",
                    "points": 0,
                    "given": {"actual": {"edge": True}},
                    "when": {},
                    "then": {"expected": {"edge": True}},
                },
            ])

            frozen_contract, frozen_test_set, report = freeze_bundle(contract, test_set)
            self.assertTrue(report["ready"])
            save_json_atomic(contract_path, frozen_contract)
            save_json_atomic(test_set_path, frozen_test_set)

            acceptance = run_acceptance(
                contract_path=contract_path,
                test_set_path=test_set_path,
                project_dir=root,
                output_path=result_path,
            )
            self.assertEqual(acceptance["summary"]["verdict"], "PASS")
            self.assertEqual(acceptance["summary"]["score"], 100.0)
            self.assertTrue(result_path.exists())

    def test_p0_failure_forces_fail_even_when_score_threshold_met(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path = root / "task_contract.json"
            test_set_path = root / "test_set.json"

            contract = build_initial_contract("TMP-P0", "generic task")
            contract["modules"].append({
                "id": "M1",
                "name": "Generic module",
                "inputs": [],
                "outputs": [],
                "acceptance_rules": [],
                "exceptions": [],
            })
            contract["acceptance_threshold"] = 50
            test_set = build_initial_test_set("TMP-P0")
            test_set["tests"].extend([
                {
                    "id": "T1",
                    "module": "M1",
                    "type": "json_exact_match",
                    "priority": "P0",
                    "case_kind": "normal",
                    "points": 1,
                    "given": {"actual": {"ok": False}},
                    "when": {},
                    "then": {"expected": {"ok": True}},
                },
                {
                    "id": "T2",
                    "module": "M1",
                    "type": "json_exact_match",
                    "priority": "P1",
                    "case_kind": "edge",
                    "points": 99,
                    "given": {"actual": {"edge": True}},
                    "when": {},
                    "then": {"expected": {"edge": True}},
                },
            ])

            frozen_contract, frozen_test_set, report = freeze_bundle(contract, test_set)
            self.assertTrue(report["ready"])
            save_json_atomic(contract_path, frozen_contract)
            save_json_atomic(test_set_path, frozen_test_set)

            acceptance = run_acceptance(
                contract_path=contract_path,
                test_set_path=test_set_path,
                project_dir=root,
            )
            self.assertEqual(acceptance["summary"]["score"], 99.0)
            self.assertEqual(acceptance["summary"]["verdict"], "FAIL")

    def test_hash_mismatch_forces_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path = root / "task_contract.json"
            test_set_path = root / "test_set.json"

            contract = build_initial_contract("TMP-HASH", "generic task")
            contract["definition_of_ready"]["min_edge_cases_total"] = 0
            contract["modules"].append({
                "id": "M1",
                "name": "Generic module",
                "inputs": [],
                "outputs": [],
                "acceptance_rules": [],
                "exceptions": [],
            })
            test_set = build_initial_test_set("TMP-HASH")
            test_set["tests"].append({
                "id": "T1",
                "module": "M1",
                "type": "json_exact_match",
                "priority": "P0",
                "case_kind": "normal",
                "points": 100,
                "given": {"actual": {"ok": True}},
                "when": {},
                "then": {"expected": {"ok": True}},
            })

            frozen_contract, frozen_test_set, report = freeze_bundle(contract, test_set)
            self.assertTrue(report["ready"])
            frozen_test_set["tests"][0]["then"]["expected"] = {"ok": False}
            save_json_atomic(contract_path, frozen_contract)
            save_json_atomic(test_set_path, frozen_test_set)

            acceptance = run_acceptance(
                contract_path=contract_path,
                test_set_path=test_set_path,
                project_dir=root,
            )
            self.assertEqual(acceptance["summary"]["verdict"], "FAIL")
            self.assertIn("test_set_hash mismatch", acceptance["integrity_errors"])


if __name__ == "__main__":
    unittest.main()
