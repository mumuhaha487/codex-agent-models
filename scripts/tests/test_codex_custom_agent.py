from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "codex_custom_agent.py"
SPEC = importlib.util.spec_from_file_location("codex_custom_agent_under_test", SCRIPT)
assert SPEC and SPEC.loader
MANAGER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MANAGER
SPEC.loader.exec_module(MANAGER)


class ManagerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = MANAGER.resolve_paths(self.temporary.name)
        self.paths.home.mkdir(parents=True, exist_ok=True)
        self.paths.config.write_text(
            'model = "parent-model"\n'
            'model_provider = "parent-provider"\n'
            '[model_providers.parent-provider]\n'
            'base_url = "https://gateway.example/v1"\n'
            'wire_api = "responses"\n'
            '[model_providers.parent-provider.auth]\n'
            'env_key = "PARENT_KEY"\n',
            encoding="utf-8",
        )


class AgentTextTests(ManagerTestCase):
    def test_writable_agent_inherits_provider_effort_and_vision(self) -> None:
        text = MANAGER.expected_agent_text("child-model", "parent-provider", "high", True)
        self.assertIn('model = "child-model"', text)
        self.assertIn('model_provider = "parent-provider"', text)
        self.assertIn('model_reasoning_effort = "high"', text)
        self.assertIn('sandbox_mode = "workspace-write"', text)
        self.assertIn("# supports-vision = yes", text)
        self.assertIn("edit code directly", text)

    def test_text_only_agent_does_not_claim_image_access(self) -> None:
        text = MANAGER.expected_agent_text("child", "parent-provider", "low", False)
        self.assertIn("# supports-vision = no", text)
        self.assertIn("configured for text-only input", text)


class WriteScopeTests(ManagerTestCase):
    def snapshot_non_agent_files(self) -> dict[str, str]:
        return {
            str(path.relative_to(self.paths.home)): MANAGER.sha256_bytes(path.read_bytes())
            for path in self.paths.home.rglob("*")
            if path.is_file() and path != self.paths.agent
        }

    def test_install_only_writes_custom_agent(self) -> None:
        auth = self.paths.home / "auth.json"
        catalog = self.paths.home / "models-with-custom-agent.json"
        manifest = self.paths.home / "codex-custom-subagent" / "manifest.json"
        auth.write_text('{"OPENAI_API_KEY":"secret"}\n', encoding="utf-8")
        catalog.write_text('{"models":[]}\n', encoding="utf-8")
        manifest.parent.mkdir()
        manifest.write_text('{"legacy":true}\n', encoding="utf-8")
        before = self.snapshot_non_agent_files()

        outcome = MANAGER.install(self.paths, "", "child-model", "high", True)

        self.assertEqual(before, self.snapshot_non_agent_files())
        self.assertEqual(outcome["write_allowlist"], [str(self.paths.agent)])
        self.assertTrue(outcome["protected_config_unchanged"])
        self.assertTrue(self.paths.agent.is_file())

    def test_conflicting_agent_requires_explicit_replacement(self) -> None:
        self.paths.agent.parent.mkdir()
        self.paths.agent.write_text("user managed agent\n", encoding="utf-8")
        with self.assertRaises(MANAGER.ManagerError) as caught:
            MANAGER.install(self.paths, "", "child", "high", False)
        self.assertEqual(caught.exception.code, "conflict")

        outcome = MANAGER.install(self.paths, "", "child", "high", False, replace_agent=True)
        self.assertTrue(outcome["replaced_conflicting_agent"])
        self.assertIn(MANAGER.MANAGED_MARKER, self.paths.agent.read_text(encoding="utf-8"))

    def test_managed_agent_can_be_updated_without_replace_flag(self) -> None:
        MANAGER.install(self.paths, "", "first-model", "low", False)
        outcome = MANAGER.install(self.paths, "", "second-model", "high", True)
        self.assertTrue(outcome["changed"])
        self.assertFalse(outcome["replaced_conflicting_agent"])
        self.assertIn('model = "second-model"', self.paths.agent.read_text(encoding="utf-8"))

    def test_config_change_during_install_rolls_back_agent_only(self) -> None:
        self.paths.agent.parent.mkdir()
        self.paths.agent.write_text("original agent\n", encoding="utf-8")
        original_writer = MANAGER.atomic_write_agent

        def mutate_config(paths, data):
            original_writer(paths, data)
            paths.config.write_text(paths.config.read_text(encoding="utf-8") + "# external change\n", encoding="utf-8")

        with patch.object(MANAGER, "atomic_write_agent", side_effect=mutate_config):
            with self.assertRaises(MANAGER.ManagerError) as caught:
                MANAGER.install(self.paths, "", "child", "medium", False, replace_agent=True)

        self.assertEqual(caught.exception.code, "protected_config_changed")
        self.assertEqual(self.paths.agent.read_text(encoding="utf-8"), "original agent\n")
        self.assertIn("external change", self.paths.config.read_text(encoding="utf-8"))

    def test_disable_deletes_only_managed_agent(self) -> None:
        MANAGER.install(self.paths, "", "child", "medium", False)
        before_config = self.paths.config.read_bytes()
        outcome = MANAGER.disable(self.paths)
        self.assertTrue(outcome["changed"])
        self.assertFalse(self.paths.agent.exists())
        self.assertEqual(self.paths.config.read_bytes(), before_config)

    def test_write_guard_rejects_any_other_target(self) -> None:
        with self.assertRaises(MANAGER.ManagerError) as caught:
            MANAGER.assert_agent_target(self.paths, self.paths.config)
        self.assertEqual(caught.exception.code, "write_scope_violation")


class SetupTests(ManagerTestCase):
    def test_page_values_are_forwarded(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "CUSTOM_AGENT_MODEL": "child-model",
                    "CUSTOM_AGENT_REASONING_EFFORT": "high",
                    "CUSTOM_AGENT_VISION": "yes",
                },
            ),
            patch.object(MANAGER, "install", return_value={"changed": True}) as install,
        ):
            outcome = MANAGER.setup(
                self.paths,
                "",
                True,
                None,
                None,
                None,
                model_env=True,
                effort_env=True,
                vision_env=True,
            )
        self.assertEqual(outcome["status"], "configured")
        install.assert_called_once_with(
            self.paths,
            "",
            "child-model",
            "high",
            True,
            replace_agent=False,
        )

    def test_empty_vision_environment_is_rejected(self) -> None:
        with patch.dict(os.environ, {"CUSTOM_AGENT_VISION": ""}):
            with self.assertRaises(MANAGER.ManagerError) as caught:
                MANAGER.setup(self.paths, "", True, "child", "high", None, vision_env=True)
        self.assertEqual(caught.exception.code, "configuration_missing")

    def test_first_setup_has_no_fallback_defaults(self) -> None:
        outcome = MANAGER.setup(self.paths, "", True, None, None, None)
        self.assertEqual(outcome["status"], "configuration_missing")
        self.assertEqual(outcome["missing"], ["model", "reasoning_effort", "supports_vision"])


class StatusTests(ManagerTestCase):
    def test_status_reads_agent_without_writing_config(self) -> None:
        MANAGER.install(self.paths, "", "child", "low", True)
        before = self.paths.config.read_bytes()
        status = MANAGER.static_status(self.paths)
        self.assertEqual(status["status"], "configured")
        self.assertEqual(status["selected_model"], "child")
        self.assertEqual(status["reasoning_effort"], "low")
        self.assertTrue(status["supports_vision"])
        self.assertEqual(self.paths.config.read_bytes(), before)


class CliTests(unittest.TestCase):
    def test_mutating_command_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "disable", "--codex-home", directory, "--json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        payload = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(payload["status"], "confirmation_required")
        self.assertIn("已确认", payload["message"])

    def test_help_is_utf8(self) -> None:
        environment = dict(os.environ)
        environment.pop("PYTHONUTF8", None)
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            env=environment,
            check=True,
        )
        self.assertIn("配置并验证用户指定模型", completed.stdout.decode("utf-8"))


class NativeEventTests(unittest.TestCase):
    def test_failed_child_state_is_preserved(self) -> None:
        output = "\n".join(
            [
                json.dumps({"type": "item.completed", "item": {"type": "collab_tool_call", "tool": "spawn_agent", "receiver_thread_ids": ["child-1"]}}),
                json.dumps({"type": "item.completed", "item": {"type": "collab_tool_call", "tool": "wait", "agents_states": {"child-1": {"status": "failed", "message": "unsupported"}}}}),
            ]
        )
        child_ids, states = MANAGER.parse_native_events(output)
        self.assertEqual(child_ids, ["child-1"])
        self.assertEqual(states["child-1"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
