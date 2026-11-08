"""Static regression tests for Codex Plus model pins and safety defaults."""
import pathlib
import tomllib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUS = ROOT / "profiles" / "plus" / "codex"

ROLES = {
    "explorer": ("gpt-6-luna", "medium", "read-only"),
    "researcher": ("gpt-6.1-sol", "medium", "read-only"),
    "worker": ("gpt-6-luna", "medium", "workspace-write"),
    "tester": ("gpt-6-luna", "high", "workspace-write"),
    "reviewer": ("gpt-6-astra", "medium", "read-only"),
    "visual-inspector": ("gpt-6.1-terra", "medium", "read-only"),
}

class PlusProfileTests(unittest.TestCase):
    def test_root_security_and_model(self):
        config = tomllib.loads((PLUS / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["model"], "gpt-6.1-sol")
        self.assertEqual(config["model_reasoning_effort"], "high")
        self.assertEqual(config["approval_policy"], "on-request")
        self.assertEqual(config["sandbox_mode"], "workspace-write")
        self.assertEqual(config["agents"]["max_concurrent_threads_per_session"], 4)
        self.assertEqual(config["agents"]["default_subagent_model"], "gpt-6-luna")
        self.assertEqual({key for key, value in config["agents"].items() if isinstance(value, dict)}, set(ROLES))
        for name in ROLES:
            self.assertEqual(config["agents"][name]["config_file"], f"agents/{name}.toml")
            self.assertTrue(config["agents"][name]["description"])

    def test_six_role_pins_and_permissions(self):
        for name, (model, effort, sandbox) in ROLES.items():
            with self.subTest(name=name):
                path = PLUS / "agents" / (name + ".toml")
                role = tomllib.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(role["name"], name)
                self.assertEqual(role["model"], model)
                self.assertEqual(role["model_reasoning_effort"], effort)
                self.assertEqual(role["sandbox_mode"], sandbox)
                self.assertTrue(role["developer_instructions"].strip())

    def test_installer_has_preflight_and_backup(self):
        script = (ROOT / "setup.ps1").read_text(encoding="utf-8")
        self.assertIn("Test-CodexCli", script)
        self.assertIn("0.153.0", script)
        self.assertIn("0.159.0", script)
        self.assertIn("Backup: $backupPath", script)
        self.assertNotIn("if (Install-Component -Name $component -TargetDirectory $targetDirectory) {", script)

    def test_manual_approval_guardrails(self):
        policy = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Never merge, deploy", policy)
        self.assertIn("NOT CHECKED", policy)

if __name__ == "__main__":
    unittest.main()
