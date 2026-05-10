import json
import subprocess
import sys
import unittest
from pathlib import Path

import pipeline


ROOT = Path(__file__).resolve().parent


class CodexTaskHookTests(unittest.TestCase):
    def test_codex_skill_documents_mandatory_pipeline(self) -> None:
        skill = ROOT / ".codex" / "skills" / "pipeline-task" / "SKILL.md"
        text = skill.read_text(encoding="utf-8")

        self.assertIn("Three-Gate", text)
        self.assertIn("Option A phase attestation", text)
        self.assertIn("Incremental Module Gate", text)
        self.assertIn("pm_planner", text)
        self.assertIn("pipeline_manager", text)
        self.assertIn("manager_handoff.xml", text)
        self.assertIn("anti_gaming_read", text)
        self.assertIn("ACCEPT", text)
        self.assertIn("REJECT", text)

    def test_codex_doctor_reports_ready_json(self) -> None:
        proc = subprocess.run(
            [sys.executable, "pipeline.py", "codex", "doctor", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "PASS")
        self.assertIn("pm_planner", "\n".join(payload["quick_start"]))
        self.assertIn("pipeline_manager", "\n".join(payload["quick_start"]))

    def test_codex_command_is_registered(self) -> None:
        parser = pipeline.build_parser()
        args = parser.parse_args(["codex", "doctor", "--json"])

        self.assertEqual(args.command, "codex")
        self.assertEqual(args.codex_action, "doctor")
        self.assertTrue(args.json)
        self.assertIs(pipeline.COMMAND_MAP["codex"], pipeline.cmd_codex)


if __name__ == "__main__":
    unittest.main()
