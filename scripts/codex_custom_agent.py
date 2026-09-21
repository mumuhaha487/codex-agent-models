#!/usr/bin/env python3
"""配置并验证用户指定模型作为 Codex 原生子 Agent。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except (AttributeError, OSError, ValueError):
                pass


configure_utf8_stdio()

SKILL_NAME = "deepseek"
ROLE = "CustomAgent"
AGENT_SANDBOX_MODE = "workspace-write"
AGENT_EXECUTION_MODE = "isolated_git_worktree"
REASONING_EFFORTS = {"low", "medium", "high"}
VISION_VALUES = {"yes", "no"}
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
MANAGED_MARKER = "# managed-by = deepseek"
VISION_MARKER = re.compile(r"^# supports-vision = (yes|no)$", re.MULTILINE)
MAX_STATE_DATABASES = 32
METADATA_WAIT_SECONDS = 5.0
DESKTOP_CODEX_CANDIDATES = (
    Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
    Path("/Applications/Codex.app/Contents/Resources/codex"),
)
WINDOWS_CODEX_RELATIVE_CANDIDATES = (
    Path("Programs") / "Codex" / "resources" / "codex.exe",
    Path("Programs") / "OpenAI" / "Codex" / "resources" / "codex.exe",
    Path("Codex") / "resources" / "codex.exe",
)


class ManagerError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class Paths:
    home: Path
    config: Path
    agent: Path


def resolve_paths(codex_home: str | None) -> Paths:
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    return Paths(home=home, config=home / "config.toml", agent=home / "agents" / f"{ROLE}.toml")


def result(status: str, **kwargs: Any) -> dict[str, Any]:
    return {"status": status, **kwargs}


def emit(payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(payload.get("status", "unknown"))
    for key, value in payload.items():
        if key != "status":
            print(f"{key}: {value}")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str | None:
    return sha256_bytes(path.read_bytes()) if path.is_file() else None


def find_desktop_codex() -> str:
    configured = os.environ.get("CODEX_DESKTOP_BIN")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        raise ManagerError("desktop_codex_missing", f"CODEX_DESKTOP_BIN 指向的文件不存在：{candidate}")
    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.extend(DESKTOP_CODEX_CANDIDATES)
    elif os.name == "nt" or sys.platform == "win32":
        for variable in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            root = os.environ.get(variable)
            if root:
                candidates.extend(Path(root) / relative for relative in WINDOWS_CODEX_RELATIVE_CANDIDATES)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    discovered = shutil.which("codex.exe") or shutil.which("codex")
    if discovered:
        return discovered
    raise ManagerError("desktop_codex_missing", "没有找到 Codex 桌面应用内置运行时。")


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def parse_toml_text(text: str, source: str = "TOML") -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManagerError("invalid_config", f"{source} 无法解析：{exc}") from exc


def read_config_snapshot(paths: Paths) -> tuple[str, dict[str, Any]]:
    if not paths.config.is_file():
        raise ManagerError("parent_config_missing", f"父配置不存在：{paths.config}")
    data = paths.config.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManagerError("invalid_config", "config.toml 不是 UTF-8。") from exc
    return sha256_bytes(data), parse_toml_text(text, "config.toml")


def assert_config_unchanged(paths: Paths, expected_digest: str) -> None:
    if file_digest(paths.config) != expected_digest:
        raise ManagerError(
            "protected_config_changed",
            "检测到 config.toml 在操作期间发生变化；已停止，管理器不会修改或恢复该文件。",
            {"path": str(paths.config)},
        )


def configured_parent_model(config: dict[str, Any]) -> str | None:
    value = config.get("model")
    return value if isinstance(value, str) and value else None


def configured_parent_provider(config: dict[str, Any]) -> str | None:
    value = config.get("model_provider")
    return value if isinstance(value, str) and value else None


def validate_model_id(model: str) -> str:
    if not MODEL_ID_PATTERN.fullmatch(model):
        raise ManagerError("invalid_model", "模型 ID 包含不支持的字符或长度超过 128。")
    return model


def validate_reasoning_effort(effort: str) -> str:
    if effort not in REASONING_EFFORTS:
        raise ManagerError("invalid_reasoning_effort", "思考强度必须是 low、medium 或 high。")
    return effort


def validate_vision(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in VISION_VALUES:
        raise ManagerError("invalid_vision", "识图能力必须是 yes 或 no。")
    return normalized == "yes"


def expected_agent_text(model: str, provider: str, effort: str, supports_vision: bool) -> str:
    vision_value = "yes" if supports_vision else "no"
    vision_instruction = (
        "When image inputs are included in your task context, inspect them directly and use the visual evidence in your implementation; do not ask the parent agent to pre-analyze them."
        if supports_vision
        else "You are configured for text-only input. Do not claim to inspect images; use visual observations supplied by the parent agent."
    )
    return f'''{MANAGED_MARKER}
# supports-vision = {vision_value}
# execution-mode = {AGENT_EXECUTION_MODE}
name = {toml_string(ROLE)}
description = "Writable implementation subagent restricted to a parent-managed isolated Git worktree."
model = {toml_string(model)}
model_provider = {toml_string(provider)}
model_reasoning_effort = {toml_string(effort)}
sandbox_mode = {toml_string(AGENT_SANDBOX_MODE)}
developer_instructions = """
You are the writable implementation subagent running inside Codex.

Work only on the single bounded plan item assigned by the parent agent and edit code directly in the exact isolated Git worktree path it provides. Before editing, verify that your working directory is that worktree and inspect git status. Never edit the parent's active checkout or any path outside the assigned worktree.
{vision_instruction}
Implement the task in the worktree, run the relevant tests, and return changed file paths, test results, and explicit assumptions. Do not return a replacement patch unless the parent explicitly asks for one.
The parent owns checkpoints, integration, rollback, and cleanup. Do not run git reset, git clean, git checkout, git restore, git worktree remove, branch deletion, merge, rebase, cherry-pick, or revert. Do not commit unless the parent explicitly asks you to do so.
When the parent reports an acceptance failure, use its exact file locations, commands, evidence, expected behavior, and direction to revise the files in the same assigned worktree.
Do not spawn or delegate to any additional subagent.
"""
'''


def read_agent_settings(paths: Paths) -> dict[str, Any]:
    if not paths.agent.is_file():
        return {}
    text = paths.agent.read_text(encoding="utf-8")
    parsed = parse_toml_text(text, "CustomAgent.toml")
    vision = VISION_MARKER.search(text)
    return {
        "text": text,
        "managed": MANAGED_MARKER in text,
        "model": parsed.get("model"),
        "model_provider": parsed.get("model_provider"),
        "reasoning_effort": parsed.get("model_reasoning_effort"),
        "supports_vision": vision.group(1) == "yes" if vision else None,
        "sandbox_mode": parsed.get("sandbox_mode"),
    }


def configured_custom_model(paths: Paths) -> str | None:
    value = read_agent_settings(paths).get("model")
    return validate_model_id(value) if isinstance(value, str) else None


def configured_reasoning_effort(paths: Paths) -> str | None:
    value = read_agent_settings(paths).get("reasoning_effort")
    return validate_reasoning_effort(value) if isinstance(value, str) else None


def configured_supports_vision(paths: Paths) -> bool | None:
    value = read_agent_settings(paths).get("supports_vision")
    return value if isinstance(value, bool) else None


def assert_agent_target(paths: Paths, target: Path) -> None:
    if target.resolve() != paths.agent.resolve():
        raise ManagerError("write_scope_violation", "拒绝写入 CustomAgent.toml 之外的任何 Codex 配置文件。")


def atomic_write_agent(paths: Paths, data: bytes) -> None:
    target = paths.agent
    assert_agent_target(paths, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def delete_agent(paths: Paths) -> None:
    assert_agent_target(paths, paths.agent)
    paths.agent.unlink(missing_ok=True)


def restore_agent(paths: Paths, previous: bytes | None) -> None:
    delete_agent(paths) if previous is None else atomic_write_agent(paths, previous)


def install(paths: Paths, _codex_bin: str, model: str, effort: str, vision: bool, replace_agent: bool = False) -> dict[str, Any]:
    model = validate_model_id(model)
    effort = validate_reasoning_effort(effort)
    if not isinstance(vision, bool):
        raise ManagerError("invalid_vision", "识图能力必须由用户明确选择 yes 或 no。")
    config_digest, config = read_config_snapshot(paths)
    provider = configured_parent_provider(config)
    if not provider:
        raise ManagerError("parent_provider_unconfigured", "桌面配置中没有明确的父 model_provider。")
    target = expected_agent_text(model, provider, effort, vision).encode("utf-8")
    previous = paths.agent.read_bytes() if paths.agent.is_file() else None
    previous_managed = previous is not None and MANAGED_MARKER.encode("utf-8") in previous
    if previous is not None and previous != target and not previous_managed and not replace_agent:
        raise ManagerError(
            "conflict",
            "现有 CustomAgent.toml 与目标配置不同。",
            {"path": str(paths.agent), "resolution": "确认完整覆盖范围后，以 --confirmed --replace-agent 重试。"},
        )
    changed = previous != target
    try:
        if changed:
            atomic_write_agent(paths, target)
        assert_config_unchanged(paths, config_digest)
    except Exception:
        restore_agent(paths, previous)
        raise
    return {
        "skill_name": SKILL_NAME,
        "selected_model": model,
        "reasoning_effort": effort,
        "supports_vision": vision,
        "sandbox_mode": AGENT_SANDBOX_MODE,
        "execution_mode": AGENT_EXECUTION_MODE,
        "parent_provider": provider,
        "parent_credentials_untouched": True,
        "protected_config_unchanged": True,
        "write_allowlist": [str(paths.agent)],
        "changed": changed,
        "replaced_conflicting_agent": bool(previous is not None and changed and not previous_managed and replace_agent),
    }


def static_status(paths: Paths, codex_bin: str | None = None) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "config_exists": paths.config.is_file(),
        "agent_exists": paths.agent.is_file(),
        "write_allowlist_exact": [str(paths.agent)],
        "protected_config_read_only": True,
        "desktop_codex_detected": bool(codex_bin),
    }
    errors: list[str] = []
    provider: str | None = None
    settings: dict[str, Any] = {}
    try:
        _, config = read_config_snapshot(paths)
        provider = configured_parent_provider(config)
        checks["config_valid"] = True
        checks["parent_provider_configured"] = bool(provider)
    except ManagerError as exc:
        checks["config_valid"] = False
        checks["parent_provider_configured"] = False
        errors.append(str(exc))
    if paths.agent.is_file():
        try:
            settings = read_agent_settings(paths)
            checks.update(
                {
                    "agent_valid": True,
                    "agent_managed": settings.get("managed") is True,
                    "model_selected": isinstance(settings.get("model"), str),
                    "reasoning_effort_valid": settings.get("reasoning_effort") in REASONING_EFFORTS,
                    "vision_setting_present": isinstance(settings.get("supports_vision"), bool),
                    "workspace_write": settings.get("sandbox_mode") == AGENT_SANDBOX_MODE,
                    "provider_inherited": bool(provider) and settings.get("model_provider") == provider,
                }
            )
        except (ManagerError, OSError, UnicodeDecodeError) as exc:
            checks["agent_valid"] = False
            errors.append(str(exc))
    else:
        checks.update(
            {
                "agent_valid": False,
                "agent_managed": False,
                "model_selected": False,
                "reasoning_effort_valid": False,
                "vision_setting_present": False,
                "workspace_write": False,
                "provider_inherited": False,
            }
        )
    required = (
        "config_valid",
        "parent_provider_configured",
        "agent_exists",
        "agent_valid",
        "agent_managed",
        "model_selected",
        "reasoning_effort_valid",
        "vision_setting_present",
        "workspace_write",
        "provider_inherited",
    )
    ready = all(checks.get(key) is True for key in required)
    status = "configured" if ready else "configuration_missing" if not paths.agent.is_file() else "partial"
    return result(
        status,
        skill_name=SKILL_NAME,
        selected_model=settings.get("model"),
        reasoning_effort=settings.get("reasoning_effort"),
        supports_vision=settings.get("supports_vision"),
        sandbox_mode=settings.get("sandbox_mode"),
        execution_mode=AGENT_EXECUTION_MODE,
        parent_provider=provider,
        parent_credentials_untouched=True,
        write_allowlist=[str(paths.agent)],
        checks=checks,
        errors=errors,
    )


def direct_test(paths: Paths, codex_bin: str, model: str, effort: str) -> dict[str, Any]:
    _, config = read_config_snapshot(paths)
    provider = configured_parent_provider(config)
    if not provider:
        raise ManagerError("parent_provider_unconfigured", "桌面配置中没有明确的父 model_provider。")
    env = dict(os.environ)
    env["CODEX_HOME"] = str(paths.home)
    proc = subprocess.run(
        [
            codex_bin,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--json",
            "-s",
            "read-only",
            "-C",
            str(paths.home),
            "-m",
            model,
            "-c",
            f"model_provider={toml_string(provider)}",
            "-c",
            f"model_reasoning_effort={toml_string(effort)}",
            "Reply exactly CUSTOM_AGENT_DIRECT_OK and nothing else.",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )
    if proc.returncode != 0 or "CUSTOM_AGENT_DIRECT_OK" not in proc.stdout:
        raise ManagerError("direct_test_failed", "自定义子 Agent 直连测试失败。", {"stderr": proc.stderr[-1000:]})
    return {
        "direct": True,
        "selected_model": model,
        "model_provider": provider,
        "reasoning_effort": effort,
        "credential_source": "parent_provider",
    }


def query_child_metadata(paths: Paths, child_id: str, deadline: float | None = None) -> dict[str, Any] | None:
    candidates: list[tuple[float, Path]] = []
    for state_db in paths.home.glob("state_*.sqlite"):
        try:
            candidates.append((state_db.stat().st_mtime, state_db))
        except OSError:
            continue
    for _, state_db in sorted(candidates, reverse=True)[:MAX_STATE_DATABASES]:
        if deadline is not None and time.monotonic() >= deadline:
            return None
        try:
            with sqlite3.connect(f"{state_db.resolve().as_uri()}?mode=ro", uri=True, timeout=0.05) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)").fetchall()}
                required = {"id", "model_provider", "model", "reasoning_effort", "agent_role"}
                if not required.issubset(columns):
                    continue
                row = connection.execute(
                    "SELECT model_provider, model, reasoning_effort, agent_role FROM threads WHERE id = ?",
                    (child_id,),
                ).fetchone()
        except (OSError, sqlite3.Error):
            continue
        if row:
            return {
                "model_provider": row[0],
                "model": row[1],
                "reasoning_effort": row[2],
                "agent_role": row[3],
            }
    return None


def wait_for_child_metadata(paths: Paths, child_id: str) -> dict[str, Any] | None:
    deadline = time.monotonic() + METADATA_WAIT_SECONDS
    while True:
        metadata = query_child_metadata(paths, child_id, deadline)
        if metadata is not None:
            return metadata
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.2, remaining))


def native_route_details(paths: Paths, parsed: dict[str, Any] | None = None) -> dict[str, Any]:
    if parsed is None:
        _, parsed = read_config_snapshot(paths)
    provider = configured_parent_provider(parsed)
    return {
        "route_mode": "inherited_parent_provider",
        "expected_provider": provider,
        "parent_provider": provider,
        "credential_source": "parent_provider",
        "uses_dedicated_credential": False,
    }


def parse_native_events(stdout: str) -> tuple[list[str], dict[str, dict[str, Any]]]:
    child_ids: list[str] = []
    child_states: dict[str, dict[str, Any]] = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if event.get("type") != "item.completed" or item.get("type") != "collab_tool_call":
            continue
        if item.get("tool") == "spawn_agent":
            child_ids.extend(item.get("receiver_thread_ids") or [])
        elif item.get("tool") == "wait":
            for receiver_id, state in (item.get("agents_states") or {}).items():
                if isinstance(state, dict):
                    child_states[receiver_id] = {"status": state.get("status"), "message": state.get("message")}
    return child_ids, child_states


def native_test(paths: Paths, codex_bin: str, model: str, effort: str) -> dict[str, Any]:
    _, config = read_config_snapshot(paths)
    parent_model = configured_parent_model(config)
    if not parent_model:
        raise ManagerError("parent_model_unconfigured", "桌面配置中没有明确的父模型。")
    route = native_route_details(paths, config)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(paths.home)
    with tempfile.TemporaryDirectory(prefix="codex-custom-agent-write-test-") as directory:
        repository = Path(directory) / "repo"
        repository.mkdir()
        subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Codex Test"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "codex-test@localhost"], check=True)
        (repository / "baseline.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "baseline.txt"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-q", "-m", "baseline"], check=True)
        prompt = (
            "Use the native spawn_agent tool exactly once with agent_type CustomAgent and fork_turns none. "
            "Tell it that the current directory is its assigned isolated Git worktree. Ask it to create "
            "native-custom-agent-write.txt containing exactly NATIVE_CUSTOM_AGENT_WRITE_OK followed by a newline, "
            "modify no other file, and not commit. Then wait and return only its final response."
        )
        proc = subprocess.run(
            [codex_bin, "exec", "--json", "-s", AGENT_SANDBOX_MODE, "-C", str(repository), "-m", parent_model, prompt],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
        )
        marker = repository / "native-custom-agent-write.txt"
        content_ok = marker.is_file() and marker.read_bytes() in {
            b"NATIVE_CUSTOM_AGENT_WRITE_OK\n",
            b"NATIVE_CUSTOM_AGENT_WRITE_OK\r\n",
        }
        status = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain=v1", "--untracked-files=all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        write_ok = content_ok and status.returncode == 0 and status.stdout.strip() == "?? native-custom-agent-write.txt"
    if proc.returncode != 0:
        raise ManagerError("native_test_failed", "新 Codex 任务中的原生 spawn_agent 测试失败。", {"stderr": proc.stderr[-1200:], **route})
    child_ids, states = parse_native_events(proc.stdout)
    child_id = child_ids[0] if len(child_ids) == 1 else None
    child_state = states.get(child_id) if child_id else None
    metadata = wait_for_child_metadata(paths, child_id) if child_id else None
    expected = {
        "model_provider": route["expected_provider"],
        "model": model,
        "reasoning_effort": effort,
        "agent_role": ROLE,
    }
    if child_state and child_state.get("status") not in {None, "completed"}:
        raise ManagerError("native_child_failed", "原生 CustomAgent 子线程启动或执行失败。", {"child_state": child_state, **route})
    if len(child_ids) != 1 or metadata != expected or not write_ok:
        raise ManagerError(
            "native_route_mismatch",
            "原生子 Agent 路由验收证据不完整或不符合自定义配置。",
            {"child_ids": child_ids, "metadata": metadata, "expected": expected, "write_verified": write_ok, **route},
        )
    return {
        "desktop_fresh_session_native": True,
        "child_id": child_id,
        "writable_child_verified": True,
        **route,
        **expected,
    }


def run_tests(paths: Paths, codex_bin: str) -> dict[str, Any]:
    config_digest, _ = read_config_snapshot(paths)
    status = static_status(paths, codex_bin)
    if status["status"] != "configured":
        raise ManagerError("not_configured", "静态配置尚未完整，不能运行实时测试。", status)
    direct = direct_test(paths, codex_bin, status["selected_model"], status["reasoning_effort"])
    native = native_test(paths, codex_bin, status["selected_model"], status["reasoning_effort"])
    assert_config_unchanged(paths, config_digest)
    return result(
        "ready",
        **direct,
        **native,
        supports_vision=status["supports_vision"],
        protected_config_unchanged=True,
        write_allowlist=[str(paths.agent)],
        new_task_required=True,
        restart_required=True,
    )


def setup(
    paths: Paths,
    codex_bin: str,
    skip_live_test: bool,
    requested_model: str | None,
    requested_effort: str | None,
    requested_vision: str | None,
    model_env: bool = False,
    effort_env: bool = False,
    vision_env: bool = False,
    replace_agent: bool = False,
) -> dict[str, Any]:
    if model_env:
        requested_model = os.environ.get("CUSTOM_AGENT_MODEL", "").strip()
        if not requested_model:
            raise ManagerError("configuration_missing", "设置页面没有注入模型 ID。")
    if effort_env:
        requested_effort = os.environ.get("CUSTOM_AGENT_REASONING_EFFORT", "").strip()
        if not requested_effort:
            raise ManagerError("configuration_missing", "设置页面没有注入思考强度。")
    if vision_env:
        requested_vision = os.environ.get("CUSTOM_AGENT_VISION", "").strip()
        if not requested_vision:
            raise ManagerError("configuration_missing", "设置页面没有注入识图能力。")
    model = validate_model_id(requested_model) if requested_model else configured_custom_model(paths)
    effort = validate_reasoning_effort(requested_effort) if requested_effort else configured_reasoning_effort(paths)
    vision = validate_vision(requested_vision) if requested_vision else configured_supports_vision(paths)
    missing = [
        name
        for name, value in (("model", model), ("reasoning_effort", effort), ("supports_vision", vision))
        if value is None
    ]
    if missing:
        return result(
            "configuration_missing",
            message="首次配置必须由用户明确填写模型、思考强度和是否支持识图；仓库不提供默认值。",
            missing=missing,
        )
    previous = paths.agent.read_bytes() if paths.agent.is_file() else None
    installed = install(paths, codex_bin, model, effort, vision, replace_agent=replace_agent)
    if skip_live_test:
        return result("configured", **installed, new_task_required=True, restart_required=True)
    try:
        tested = run_tests(paths, codex_bin)
    except Exception:
        restore_agent(paths, previous)
        raise
    return {**tested, **installed}


def disable(paths: Paths) -> dict[str, Any]:
    if not paths.agent.is_file():
        return result("disabled", changed=False, write_allowlist=[str(paths.agent)], protected_config_unchanged=True)
    if read_agent_settings(paths).get("managed") is not True:
        raise ManagerError("not_managed", "CustomAgent.toml 不是本 Skill 生成的文件，拒绝删除。")
    config_digest, _ = read_config_snapshot(paths)
    previous = paths.agent.read_bytes()
    try:
        delete_agent(paths)
        assert_config_unchanged(paths, config_digest)
    except Exception:
        restore_agent(paths, previous)
        raise
    return result("disabled", changed=True, write_allowlist=[str(paths.agent)], protected_config_unchanged=True)


def uninstall(paths: Paths) -> dict[str, Any]:
    disabled = disable(paths)
    return result("uninstalled", disabled=disabled, write_allowlist=[str(paths.agent)], protected_config_unchanged=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "setup", "test", "repair", "disable", "uninstall"))
    parser.add_argument("--codex-home")
    model_input = parser.add_mutually_exclusive_group()
    model_input.add_argument("--model")
    model_input.add_argument("--model-env", action="store_true")
    effort_input = parser.add_mutually_exclusive_group()
    effort_input.add_argument("--effort", choices=sorted(REASONING_EFFORTS))
    effort_input.add_argument("--effort-env", action="store_true")
    vision_input = parser.add_mutually_exclusive_group()
    vision_input.add_argument("--vision", choices=sorted(VISION_VALUES))
    vision_input.add_argument("--vision-env", action="store_true")
    parser.add_argument("--skip-live-test", action="store_true")
    parser.add_argument("--replace-agent", action="store_true")
    parser.add_argument("--confirmed", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    paths = resolve_paths(args.codex_home)
    try:
        if args.replace_agent and args.command not in {"setup", "repair"}:
            raise ManagerError("invalid_option", "--replace-agent 只能与 setup 或 repair 一起使用。")
        if args.command in {"setup", "repair", "disable", "uninstall"} and not args.confirmed:
            raise ManagerError(
                "confirmation_required",
                "拒绝执行持久化子智能体配置变更。必须先展示当前配置、目标配置、影响和唯一写入文件，"
                "并在后续独立用户消息中收到精确回复“已确认”；确认后重试并传入 --confirmed。",
            )
        codex_bin: str | None = None
        if args.command == "test" or (args.command in {"setup", "repair"} and not args.skip_live_test):
            codex_bin = find_desktop_codex()
        elif args.command == "status":
            try:
                codex_bin = find_desktop_codex()
            except ManagerError:
                codex_bin = None
        if args.command == "status":
            payload = static_status(paths, codex_bin)
        elif args.command in {"setup", "repair"}:
            payload = setup(
                paths,
                codex_bin or "",
                args.skip_live_test,
                args.model,
                args.effort,
                args.vision,
                model_env=args.model_env,
                effort_env=args.effort_env,
                vision_env=args.vision_env,
                replace_agent=args.replace_agent,
            )
        elif args.command == "test":
            payload = run_tests(paths, codex_bin or "")
        elif args.command == "disable":
            payload = disable(paths)
        else:
            payload = uninstall(paths)
        emit(payload, args.json)
        return 0 if payload["status"] not in {"partial", "configuration_missing", "model_selection_required"} else 2
    except ManagerError as exc:
        emit(result(exc.code, message=str(exc), **exc.details), args.json)
        return 2
    except subprocess.TimeoutExpired:
        emit(result("timeout", message="操作超时，未输出任何凭据。"), args.json)
        return 3
    except Exception as exc:
        emit(result("failed", message=f"{type(exc).__name__}: {exc}"), args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
