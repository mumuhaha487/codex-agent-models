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
            f'model_catalog_json = {MANAGER.toml_string(str(self.paths.catalog))}\n'
            '[model_providers.parent-provider]\n'
            'base_url = "https://gateway.example/v1"\n'
            'wire_api = "responses"\n'
            '[model_providers.parent-provider.auth]\n'
            'env_key = "PARENT_KEY"\n',
            encoding="utf-8",
        )
        self.paths.catalog.write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "auto",
                            "display_name": "auto",
                            "description": "Template",
                            "default_reasoning_level": "none",
                            "supported_reasoning_levels": [{"effort": "none", "description": "Default"}],
                            "shell_type": "unified_exec",
                            "visibility": "list",
                            "supported_in_api": True,
                            "input_modalities": ["text"],
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


class AgentTextTests(ManagerTestCase):
    def test_writable_agent_with_none_reasoning_effort(self) -> None:
        text = MANAGER.expected_agent_text("child-model", "parent-provider", "none", False)
        self.assertIn('model = "child-model"', text)
        self.assertIn('model_provider = "parent-provider"', text)
        self.assertIn('model_reasoning_effort = "none"', text)
        self.assertIn('sandbox_mode = "workspace-write"', text)

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
    def test_install_with_none_reasoning_effort(self) -> None:
        outcome = MANAGER.install(self.paths, "", "child-model", "none", False)
        self.assertTrue(outcome["protected_config_fields_unchanged"])
        self.assertTrue(self.paths.agent.is_file())
        after_config = MANAGER.parse_toml_text(self.paths.config.read_text(encoding="utf-8"))
        self.assertEqual(after_config["agents"]["default_subagent_model"], "child-model")
        self.assertEqual(after_config["agents"]["default_subagent_reasoning_effort"], "none")
        agent_settings = MANAGER.read_agent_settings(self.paths)
        self.assertEqual(agent_settings["reasoning_effort"], "none")
        catalog_entry = MANAGER.catalog_model_entry(
            json.loads(self.paths.catalog.read_text(encoding="utf-8")), "child-model"
        )
        self.assertIsNotNone(catalog_entry)
        self.assertEqual(catalog_entry["default_reasoning_level"], "none")
        efforts = {item["effort"] for item in catalog_entry["supported_reasoning_levels"]}
        self.assertIn("none", efforts)

    def snapshot_unmanaged_files(self) -> dict[str, str]:
        return {
            str(path.relative_to(self.paths.home)): MANAGER.sha256_bytes(path.read_bytes())
            for path in self.paths.home.rglob("*")
            if path.is_file() and path not in {self.paths.agent, self.paths.config, self.paths.catalog}
        }

    def test_install_writes_agent_default_settings_and_catalog_entry(self) -> None:
        auth = self.paths.home / "auth.json"
        catalog = self.paths.home / "models-with-custom-agent.json"
        manifest = self.paths.home / "codex-custom-subagent" / "manifest.json"
        auth.write_text('{"OPENAI_API_KEY":"secret"}\n', encoding="utf-8")
        catalog.write_text('{"models":[]}\n', encoding="utf-8")
        manifest.parent.mkdir()
        manifest.write_text('{"legacy":true}\n', encoding="utf-8")
        before = self.snapshot_unmanaged_files()
        before_config = MANAGER.parse_toml_text(self.paths.config.read_text(encoding="utf-8"))

        outcome = MANAGER.install(self.paths, "", "child-model", "high", True)

        self.assertEqual(before, self.snapshot_unmanaged_files())
        self.assertEqual(
            outcome["write_allowlist"],
            [str(self.paths.agent), str(self.paths.config), str(self.paths.catalog)],
        )
        self.assertTrue(outcome["protected_config_fields_unchanged"])
        self.assertTrue(outcome["provider_url_unchanged"])
        self.assertTrue(self.paths.agent.is_file())
        after_config = MANAGER.parse_toml_text(self.paths.config.read_text(encoding="utf-8"))
        self.assertEqual(after_config["agents"]["default_subagent_model"], "child-model")
        self.assertEqual(after_config["agents"]["default_subagent_reasoning_effort"], "high")
        self.assertEqual(before_config["model_providers"], after_config["model_providers"])
        self.assertEqual(
            MANAGER.config_without_managed_subagent_settings(before_config),
            MANAGER.config_without_managed_subagent_settings(after_config),
        )
        catalog = MANAGER.parse_catalog_bytes(self.paths.catalog.read_bytes())
        self.assertTrue(MANAGER.catalog_model_matches(catalog, "child-model", "high", True))
        self.assertEqual(MANAGER.catalog_model_entry(catalog, "child-model")["description"], MANAGER.MANAGED_CATALOG_DESCRIPTION)

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

    def test_external_config_change_is_not_overwritten_during_rollback(self) -> None:
        self.paths.agent.parent.mkdir()
        self.paths.agent.write_text("original agent\n", encoding="utf-8")
        original_writer = MANAGER.atomic_write_agent

        def mutate_config(paths, data):
            original_writer(paths, data)
            paths.config.write_text(paths.config.read_text(encoding="utf-8") + "# external change\n", encoding="utf-8")

        with patch.object(MANAGER, "atomic_write_agent", side_effect=mutate_config):
            with self.assertRaises(MANAGER.ManagerError) as caught:
                MANAGER.install(self.paths, "", "child", "medium", False, replace_agent=True)

        self.assertEqual(caught.exception.code, "rollback_incomplete")
        self.assertEqual(self.paths.agent.read_text(encoding="utf-8"), "original agent\n")
        self.assertIn("external change", self.paths.config.read_text(encoding="utf-8"))

    def test_disable_deletes_only_managed_agent(self) -> None:
        original_config = MANAGER.parse_toml_text(self.paths.config.read_text(encoding="utf-8"))
        MANAGER.install(self.paths, "", "child", "medium", False)
        outcome = MANAGER.disable(self.paths)
        self.assertTrue(outcome["changed"])
        self.assertTrue(outcome["config_changed"])
        self.assertFalse(self.paths.agent.exists())
        current_config = MANAGER.parse_toml_text(self.paths.config.read_text(encoding="utf-8"))
        self.assertIsNone(MANAGER.configured_default_subagent_model(current_config))
        self.assertIsNone(MANAGER.configured_default_subagent_effort(current_config))
        self.assertEqual(
            MANAGER.config_without_managed_subagent_settings(original_config),
            MANAGER.config_without_managed_subagent_settings(current_config),
        )
        catalog = MANAGER.parse_catalog_bytes(self.paths.catalog.read_bytes())
        self.assertIsNone(MANAGER.catalog_model_entry(catalog, "child"))

    def test_write_guard_rejects_any_other_target(self) -> None:
        with self.assertRaises(MANAGER.ManagerError) as caught:
            MANAGER.assert_agent_target(self.paths, self.paths.config)
        self.assertEqual(caught.exception.code, "write_scope_violation")
        with self.assertRaises(MANAGER.ManagerError) as caught:
            MANAGER.assert_catalog_target(self.paths, self.paths.config)
        self.assertEqual(caught.exception.code, "write_scope_violation")


class ConfigEditingTests(ManagerTestCase):
    def test_reasoning_effort_validation(self) -> None:
        for valid in ("none", "low", "medium", "high"):
            self.assertEqual(MANAGER.validate_reasoning_effort(valid), valid)
        for invalid in ("ultra", "", "None", "off", "max"):
            with self.assertRaises(MANAGER.ManagerError) as caught:
                MANAGER.validate_reasoning_effort(invalid)
            self.assertEqual(caught.exception.code, "invalid_reasoning_effort")

    def test_config_with_none_reasoning_effort(self) -> None:
        initial = 'model = "parent"\n'
        updated = MANAGER.config_with_default_subagent_settings(initial, "child", "none")
        self.assertIn('default_subagent_reasoning_effort = "none"', updated)
        parsed = MANAGER.parse_toml_text(updated)
        self.assertEqual(parsed["agents"]["default_subagent_model"], "child")
        self.assertEqual(parsed["agents"]["default_subagent_reasoning_effort"], "none")
        cleaned_text = MANAGER.config_without_managed_subagent_settings_text(updated, "child", "none")
        self.assertNotIn("default_subagent_reasoning_effort", cleaned_text)
        cleaned_parsed = MANAGER.parse_toml_text(cleaned_text)
        self.assertEqual(
            MANAGER.config_without_managed_subagent_settings(MANAGER.parse_toml_text(initial)),
            MANAGER.config_without_managed_subagent_settings(cleaned_parsed),
        )
    def test_adds_agents_table_without_changing_provider_url(self) -> None:
        before = self.paths.config.read_text(encoding="utf-8")
        updated = MANAGER.config_with_default_subagent_settings(before, "child-model", "high")
        parsed = MANAGER.parse_toml_text(updated)
        self.assertEqual(parsed["agents"]["default_subagent_model"], "child-model")
        self.assertEqual(parsed["agents"]["default_subagent_reasoning_effort"], "high")
        self.assertEqual(parsed["model_providers"]["parent-provider"]["base_url"], "https://gateway.example/v1")
        self.assertEqual(
            MANAGER.config_without_managed_subagent_settings(MANAGER.parse_toml_text(before)),
            MANAGER.config_without_managed_subagent_settings(parsed),
        )

    def test_updates_existing_agents_value_and_preserves_other_fields(self) -> None:
        source = (
            'model = "parent-model"\r\n'
            'model_provider = "parent-provider"\r\n'
            '[agents]\r\n'
            'max_threads = 4\r\n'
            'default_subagent_model = "old-model" # keep-comment\r\n'
            'default_subagent_reasoning_effort = "low" # effort-comment\r\n'
            '[model_providers.parent-provider]\r\n'
            'base_url = "https://gateway.example/v1"\r\n'
        )
        updated = MANAGER.config_with_default_subagent_settings(source, "new-model", "high")
        self.assertIn('default_subagent_model = "new-model" # keep-comment\r\n', updated)
        self.assertIn('default_subagent_reasoning_effort = "high" # effort-comment\r\n', updated)
        parsed = MANAGER.parse_toml_text(updated)
        self.assertEqual(parsed["agents"]["max_threads"], 4)
        self.assertEqual(parsed["model_providers"]["parent-provider"]["base_url"], "https://gateway.example/v1")

    def test_repeated_update_is_idempotent(self) -> None:
        source = self.paths.config.read_text(encoding="utf-8")
        once = MANAGER.config_with_default_subagent_settings(source, "child-model", "medium")
        self.assertEqual(MANAGER.config_with_default_subagent_settings(once, "child-model", "medium"), once)

    def test_removes_only_matching_managed_default_settings(self) -> None:
        source = MANAGER.config_with_default_subagent_settings(
            self.paths.config.read_text(encoding="utf-8"), "child-model", "medium"
        )
        updated = MANAGER.config_without_managed_subagent_settings_text(source, "child-model", "medium")
        self.assertIsNone(MANAGER.configured_default_subagent_model(MANAGER.parse_toml_text(updated)))
        self.assertIsNone(MANAGER.configured_default_subagent_effort(MANAGER.parse_toml_text(updated)))

    def test_preserves_externally_changed_default_settings_during_disable(self) -> None:
        source = MANAGER.config_with_default_subagent_settings(
            self.paths.config.read_text(encoding="utf-8"), "child-model", "medium"
        )
        source = source.replace('default_subagent_model = "child-model"', 'default_subagent_model = "other-model"')
        updated = MANAGER.config_without_managed_subagent_settings_text(source, "child-model", "medium")
        parsed = MANAGER.parse_toml_text(updated)
        self.assertEqual(parsed["agents"]["default_subagent_model"], "other-model")
        self.assertIsNone(MANAGER.configured_default_subagent_effort(parsed))


class ModelCatalogTests(ManagerTestCase):
    def test_adds_selected_model_without_changing_other_entries(self) -> None:
        before = MANAGER.parse_catalog_bytes(self.paths.catalog.read_bytes())
        updated, created = MANAGER.catalog_with_model(self.paths.catalog.read_bytes(), "child-model", "high", True)
        after = MANAGER.parse_catalog_bytes(updated)
        self.assertTrue(created)
        self.assertTrue(MANAGER.catalog_model_matches(after, "child-model", "high", True))
        self.assertEqual(MANAGER.catalog_without_model(before, "child-model"), MANAGER.catalog_without_model(after, "child-model"))

    def test_updates_existing_model_in_place(self) -> None:
        first, _ = MANAGER.catalog_with_model(self.paths.catalog.read_bytes(), "child-model", "low", False)
        second, created = MANAGER.catalog_with_model(first, "child-model", "high", True)
        catalog = MANAGER.parse_catalog_bytes(second)
        self.assertFalse(created)
        self.assertTrue(MANAGER.catalog_model_matches(catalog, "child-model", "high", True))
        self.assertEqual(len([item for item in catalog["models"] if item.get("slug") == "child-model"]), 1)

    def test_uninstall_removes_only_skill_created_entry(self) -> None:
        created, _ = MANAGER.catalog_with_model(self.paths.catalog.read_bytes(), "child-model", "medium", False)
        removed = MANAGER.catalog_without_managed_model(created, "child-model")
        self.assertIsNone(MANAGER.catalog_model_entry(MANAGER.parse_catalog_bytes(removed), "child-model"))

        catalog = MANAGER.parse_catalog_bytes(created)
        MANAGER.catalog_model_entry(catalog, "child-model")["description"] = "User managed"
        user_managed = json.dumps(catalog, ensure_ascii=False, indent=2).encode("utf-8")
        self.assertEqual(MANAGER.catalog_without_managed_model(user_managed, "child-model"), user_managed)


class SetupTests(ManagerTestCase):
    def test_setup_forwards_none_reasoning_effort(self) -> None:
        with patch.dict(os.environ, {
            "CUSTOM_AGENT_MODEL": "child-none",
            "CUSTOM_AGENT_REASONING_EFFORT": "none",
            "CUSTOM_AGENT_VISION": "no"
        }):
            outcome = MANAGER.setup(
                self.paths, "", True, None, None, None,
                model_env=True, effort_env=True, vision_env=True
            )
        self.assertEqual(outcome["status"], "configured")
        self.assertEqual(outcome["reasoning_effort"], "none")
        self.assertEqual(outcome["default_subagent_reasoning_effort"], "none")
        status = MANAGER.static_status(self.paths)
        self.assertEqual(status["reasoning_effort"], "none")
        self.assertEqual(status["default_subagent_reasoning_effort"], "none")

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

    def test_live_test_failure_rolls_back_agent_and_config(self) -> None:
        before_config = self.paths.config.read_bytes()
        before_catalog = self.paths.catalog.read_bytes()
        with patch.object(MANAGER, "run_tests", side_effect=MANAGER.ManagerError("test_failed", "failed")):
            with self.assertRaises(MANAGER.ManagerError) as caught:
                MANAGER.setup(self.paths, "codex", False, "child-model", "high", "yes")
        self.assertEqual(caught.exception.code, "test_failed")
        self.assertFalse(self.paths.agent.exists())
        self.assertEqual(self.paths.config.read_bytes(), before_config)
        self.assertEqual(self.paths.catalog.read_bytes(), before_catalog)


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
        self.assertIn("明确要求配置", payload["message"])

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
    def test_inherited_temp_directory_is_removed(self) -> None:
        with MANAGER.inherited_temp_directory("codex-test-") as directory:
            self.assertTrue(directory.is_dir())
            readonly = directory / "readonly.txt"
            readonly.write_text("locked", encoding="utf-8")
            readonly.chmod(MANAGER.stat.S_IREAD)
            created = directory
        self.assertFalse(created.exists())

    def test_marker_read_retries_transient_permission_error(self) -> None:
        class EventuallyReadable:
            def __init__(self) -> None:
                self.attempts = 0

            def read_bytes(self) -> bytes:
                self.attempts += 1
                if self.attempts == 1:
                    raise PermissionError("temporarily locked")
                return b"ok"

        path = EventuallyReadable()
        self.assertEqual(MANAGER.read_bytes_with_retry(path, attempts=2, delay_seconds=0), b"ok")
        self.assertEqual(path.attempts, 2)

    def test_native_prompt_uses_current_non_inheriting_fork_option(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("agent_type CustomAgent and fork_context false", source)
        self.assertNotIn("fork_turns", source)

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
