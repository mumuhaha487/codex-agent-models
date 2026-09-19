from __future__ import annotations

import importlib.util
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


class SetupCredentialTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = MANAGER.resolve_paths(self.temporary.name)
        self.base_args = (
            self.paths,
            "unused-codex",
            False,
            True,
            "vendor-model",
            "https://gateway.example/v1",
        )
        self.install_result = {
            "backup": str(Path(self.temporary.name) / "backup"),
            "selected_model": "vendor-model",
            "base_url": "https://gateway.example/v1/",
        }

    def test_existing_key_is_replaced_when_env_is_explicit(self) -> None:
        writes: list[str] = []
        with (
            patch.dict(os.environ, {"CUSTOM_AGENT_API_KEY": "replacement-key"}),
            patch.object(MANAGER, "credential_available", return_value=True),
            patch.object(MANAGER, "credential_has_key", return_value=True),
            patch.object(MANAGER, "read_credential_key", return_value="old-key"),
            patch.object(MANAGER, "store_credential_key", side_effect=writes.append),
            patch.object(MANAGER, "install", return_value=self.install_result),
        ):
            result = MANAGER.setup(*self.base_args, api_key_env=True)

        self.assertEqual(result["status"], "configured")
        self.assertEqual(writes, ["replacement-key"])

    def test_failed_install_restores_replaced_key(self) -> None:
        writes: list[str] = []
        with (
            patch.dict(os.environ, {"CUSTOM_AGENT_API_KEY": "replacement-key"}),
            patch.object(MANAGER, "credential_available", return_value=True),
            patch.object(MANAGER, "credential_has_key", return_value=True),
            patch.object(MANAGER, "read_credential_key", return_value="old-key"),
            patch.object(MANAGER, "store_credential_key", side_effect=writes.append),
            patch.object(MANAGER, "install", side_effect=RuntimeError("install failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "install failed"):
                MANAGER.setup(*self.base_args, api_key_env=True)

        self.assertEqual(writes, ["replacement-key", "old-key"])

    def test_failed_first_install_removes_new_key(self) -> None:
        writes: list[str] = []
        removals: list[bool] = []
        with (
            patch.dict(os.environ, {"CUSTOM_AGENT_API_KEY": "first-key"}),
            patch.object(MANAGER, "credential_available", return_value=True),
            patch.object(MANAGER, "credential_has_key", return_value=False),
            patch.object(MANAGER, "store_credential_key", side_effect=writes.append),
            patch.object(MANAGER, "remove_credential_key", side_effect=lambda: removals.append(True)),
            patch.object(MANAGER, "install", side_effect=RuntimeError("install failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "install failed"):
                MANAGER.setup(*self.base_args, api_key_env=True)

        self.assertEqual(writes, ["first-key"])
        self.assertEqual(removals, [True])

    def test_existing_key_is_preserved_without_explicit_input(self) -> None:
        with (
            patch.object(MANAGER, "credential_available", return_value=True),
            patch.object(MANAGER, "credential_has_key", return_value=True),
            patch.object(MANAGER, "read_credential_key", side_effect=AssertionError("unexpected read")),
            patch.object(MANAGER, "store_credential_key", side_effect=AssertionError("unexpected write")),
            patch.object(MANAGER, "install", return_value=self.install_result),
        ):
            result = MANAGER.setup(*self.base_args)

        self.assertEqual(result["status"], "configured")

    def test_explicit_empty_env_does_not_fall_back_to_existing_key(self) -> None:
        with (
            patch.dict(os.environ, {"CUSTOM_AGENT_API_KEY": ""}),
            patch.object(MANAGER, "credential_available", return_value=True),
            patch.object(MANAGER, "credential_has_key", return_value=True),
            patch.object(MANAGER, "read_credential_key", side_effect=AssertionError("unexpected read")),
            patch.object(MANAGER, "store_credential_key", side_effect=AssertionError("unexpected write")),
        ):
            with self.assertRaises(MANAGER.ManagerError) as caught:
                MANAGER.setup(*self.base_args, api_key_env=True)

        self.assertEqual(caught.exception.code, "credential_missing")


class StdioEncodingTests(unittest.TestCase):
    def test_help_is_utf8_without_python_encoding_environment(self) -> None:
        environment = dict(os.environ)
        environment.pop("PYTHONUTF8", None)
        environment.pop("PYTHONIOENCODING", None)

        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=True,
        )

        output = completed.stdout.decode("utf-8")
        error = completed.stderr.decode("utf-8")
        self.assertIn("配置并验证用户指定模型作为 Codex 原生子 Agent", output)
        self.assertEqual(error, "")


if __name__ == "__main__":
    unittest.main()
