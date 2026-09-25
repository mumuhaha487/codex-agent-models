#!/usr/bin/env python3
"""配置并验证用户指定模型作为 Codex 原生子 Agent。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from contextlib import contextmanager
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
REASONING_EFFORTS = {"none", "low", "medium", "high"}
VISION_VALUES = {"yes", "no"}
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
MANAGED_MARKER = "# managed-by = deepseek"
MANAGED_CATALOG_DESCRIPTION = "Custom subagent model managed by deepseek."
VISION_MARKER = re.compile(r"^# supports-vision = (yes|no)$", re.MULTILINE)
DEFAULT_SUBAGENT_MODEL_KEY = "default_subagent_model"
DEFAULT_SUBAGENT_EFFORT_KEY = "default_subagent_reasoning_effort"
AGENTS_HEADER = re.compile(r"^[ \t]*\[agents\][ \t]*(?:#.*)?(?:\r?\n)?$")
TABLE_HEADER = re.compile(r"^[ \t]*\[[^\r\n]+\][ \t]*(?:#.*)?(?:\r?\n)?$")
DEFAULT_SUBAGENT_LINES = {
    DEFAULT_SUBAGENT_MODEL_KEY: re.compile(r"^[ \t]*default_subagent_model[ \t]*="),
    DEFAULT_SUBAGENT_EFFORT_KEY: re.compile(r"^[ \t]*default_subagent_reasoning_effort[ \t]*="),
}
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
    catalog: Path


def resolve_paths(codex_home: str | None) -> Paths:
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    return Paths(
        home=home,
        config=home / "config.toml",
        agent=home / "agents" / f"{ROLE}.toml",
        catalog=home / "codex-models.json",
    )


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


def read_bytes_with_retry(path: Path, attempts: int = 20, delay_seconds: float = 0.1) -> bytes:
    last_error: PermissionError | None = None
    for attempt in range(attempts):
        try:
            return path.read_bytes()
        except PermissionError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay_seconds)
    assert last_error is not None
    raise last_error


@contextmanager
def inherited_temp_directory(prefix: str):
    root = Path(tempfile.gettempdir()) / f"{prefix}{os.getpid()}-{time.time_ns()}"
    root.mkdir()
    try:
        yield root
    finally:
        def remove_readonly(function, target, _error):
            os.chmod(target, stat.S_IWRITE)
            function(target)

        shutil.rmtree(root, onerror=remove_readonly)


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


def read_config_source(paths: Paths) -> tuple[bytes, str, str, dict[str, Any]]:
    if not paths.config.is_file():
        raise ManagerError("parent_config_missing", f"父配置不存在：{paths.config}")
    data = paths.config.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManagerError("invalid_config", "config.toml 不是 UTF-8。") from exc
    return data, sha256_bytes(data), text, parse_toml_text(text, "config.toml")


def assert_config_unchanged(paths: Paths, expected_digest: str) -> None:
    if file_digest(paths.config) != expected_digest:
        raise ManagerError(
            "protected_config_changed",
            "检测到 config.toml 在操作期间发生变化；已停止，管理器不会修改或恢复该文件。",
            {"path": str(paths.config)},
        )


def assert_catalog_unchanged(paths: Paths, expected_digest: str) -> None:
    if file_digest(paths.catalog) != expected_digest:
        raise ManagerError(
            "protected_model_catalog_changed",
            "检测到 codex-models.json 在操作期间发生变化；已停止，管理器不会修改或恢复该文件。",
            {"path": str(paths.catalog)},
        )


def configured_parent_model(config: dict[str, Any]) -> str | None:
    value = config.get("model")
    return value if isinstance(value, str) and value else None


def configured_parent_provider(config: dict[str, Any]) -> str | None:
    value = config.get("model_provider")
    return value if isinstance(value, str) and value else None


def configured_default_subagent_model(config: dict[str, Any]) -> str | None:
    agents = config.get("agents")
    if not isinstance(agents, dict):
        return None
    value = agents.get(DEFAULT_SUBAGENT_MODEL_KEY)
    return value if isinstance(value, str) and value else None


def configured_default_subagent_effort(config: dict[str, Any]) -> str | None:
    agents = config.get("agents")
    if not isinstance(agents, dict):
        return None
    value = agents.get(DEFAULT_SUBAGENT_EFFORT_KEY)
    return value if isinstance(value, str) and value in REASONING_EFFORTS else None


def config_without_managed_subagent_settings(config: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(config)
    agents = normalized.get("agents")
    if isinstance(agents, dict):
        agents.pop(DEFAULT_SUBAGENT_MODEL_KEY, None)
        agents.pop(DEFAULT_SUBAGENT_EFFORT_KEY, None)
        if not agents:
            normalized.pop("agents", None)
    return normalized


def _replace_agents_setting(lines: list[str], agents_index: int, key: str, value: str, newline: str) -> None:
    section_end = next(
        (index for index in range(agents_index + 1, len(lines)) if TABLE_HEADER.fullmatch(lines[index])),
        len(lines),
    )
    key_index = next(
        (index for index in range(agents_index + 1, section_end) if DEFAULT_SUBAGENT_LINES[key].match(lines[index])),
        None,
    )
    replacement = f"{key} = {toml_string(value)}{newline}"
    if key_index is None:
        lines.insert(section_end, replacement)
        return
    comment_match = re.search(r"[ \t]+#.*?(?=\r?\n?$)", lines[key_index])
    comment = comment_match.group(0) if comment_match else ""
    line_ending = "\r\n" if lines[key_index].endswith("\r\n") else "\n" if lines[key_index].endswith("\n") else ""
    lines[key_index] = f"{key} = {toml_string(value)}{comment}{line_ending}"


def config_with_default_subagent_settings(text: str, model: str, effort: str) -> str:
    model = validate_model_id(model)
    effort = validate_reasoning_effort(effort)
    before = parse_toml_text(text, "config.toml")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    agents_index = next((index for index, line in enumerate(lines) if AGENTS_HEADER.fullmatch(line)), None)

    if agents_index is None:
        nested_agents_index = next(
            (
                index
                for index, line in enumerate(lines)
                if re.match(r"^[ \t]*\[agents\.", line)
            ),
            None,
        )
        section = [
            f"[agents]{newline}",
            f"{DEFAULT_SUBAGENT_MODEL_KEY} = {toml_string(model)}{newline}",
            f"{DEFAULT_SUBAGENT_EFFORT_KEY} = {toml_string(effort)}{newline}",
        ]
        if nested_agents_index is None:
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            if lines and lines[-1].strip():
                lines.append(newline)
            lines.extend(section)
        else:
            lines[nested_agents_index:nested_agents_index] = section + [newline]
    else:
        _replace_agents_setting(lines, agents_index, DEFAULT_SUBAGENT_MODEL_KEY, model, newline)
        _replace_agents_setting(lines, agents_index, DEFAULT_SUBAGENT_EFFORT_KEY, effort, newline)

    updated = "".join(lines)
    after = parse_toml_text(updated, "config.toml")
    if configured_default_subagent_model(after) != model:
        raise ManagerError("default_subagent_model_write_failed", "默认子智能体模型没有正确写入 config.toml。")
    if configured_default_subagent_effort(after) != effort:
        raise ManagerError("default_subagent_effort_write_failed", "默认子智能体思考强度没有正确写入 config.toml。")
    if config_without_managed_subagent_settings(before) != config_without_managed_subagent_settings(after):
        raise ManagerError("protected_config_changed", "拒绝修改默认子智能体模型和思考强度之外的 config.toml 字段。")
    return updated


def config_without_managed_subagent_settings_text(text: str, expected_model: str, expected_effort: str) -> str:
    before = parse_toml_text(text, "config.toml")
    expected_model = validate_model_id(expected_model)
    expected_effort = validate_reasoning_effort(expected_effort)
    current_model = configured_default_subagent_model(before)
    current_effort = configured_default_subagent_effort(before)
    remove_keys = {
        key
        for key, current, expected in (
            (DEFAULT_SUBAGENT_MODEL_KEY, current_model, expected_model),
            (DEFAULT_SUBAGENT_EFFORT_KEY, current_effort, expected_effort),
        )
        if current == expected
    }
    if not remove_keys:
        return text
    lines = text.splitlines(keepends=True)
    agents_index = next((index for index, line in enumerate(lines) if AGENTS_HEADER.fullmatch(line)), None)
    if agents_index is None:
        raise ManagerError("unsupported_agents_layout", "无法安全定位 config.toml 中的 [agents] 表。")
    section_end = next(
        (index for index in range(agents_index + 1, len(lines)) if TABLE_HEADER.fullmatch(lines[index])),
        len(lines),
    )
    key_indexes = [
        index
        for index in range(agents_index + 1, section_end)
        if any(DEFAULT_SUBAGENT_LINES[key].match(lines[index]) for key in remove_keys)
    ]
    if len(key_indexes) != len(remove_keys):
        raise ManagerError("unsupported_agents_layout", "无法安全定位 config.toml 中的受管默认子智能体字段。")
    for index in reversed(key_indexes):
        del lines[index]
    updated = "".join(lines)
    after = parse_toml_text(updated, "config.toml")
    if DEFAULT_SUBAGENT_MODEL_KEY in remove_keys and configured_default_subagent_model(after) is not None:
        raise ManagerError("default_subagent_model_remove_failed", "默认子智能体模型删除后验收失败。")
    if DEFAULT_SUBAGENT_EFFORT_KEY in remove_keys and configured_default_subagent_effort(after) is not None:
        raise ManagerError("default_subagent_effort_remove_failed", "默认子智能体思考强度删除后验收失败。")
    if config_without_managed_subagent_settings(before) != config_without_managed_subagent_settings(after):
        raise ManagerError("protected_config_changed", "停用操作修改了受管默认子智能体字段之外的配置。")
    return updated


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


def configured_catalog_path(paths: Paths, config: dict[str, Any]) -> Path:
    value = config.get("model_catalog_json")
    if not isinstance(value, str) or not value.strip():
        raise ManagerError("model_catalog_unconfigured", "config.toml 没有配置 model_catalog_json。")
    expanded = Path(os.path.expandvars(os.path.expanduser(value.strip())))
    resolved = (paths.home / expanded).resolve() if not expanded.is_absolute() else expanded.resolve()
    if resolved != paths.catalog.resolve():
        raise ManagerError(
            "unsupported_model_catalog_path",
            "model_catalog_json 必须指向当前 CODEX_HOME 下的 codex-models.json，管理器不会写入其他路径。",
            {"configured_path": str(resolved), "required_path": str(paths.catalog.resolve())},
        )
    return resolved


def parse_catalog_bytes(data: bytes, source: str = "codex-models.json") -> dict[str, Any]:
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagerError("invalid_model_catalog", f"{source} 无法解析：{exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("models"), list):
        raise ManagerError("invalid_model_catalog", f"{source} 必须包含 models 数组。")
    if not all(isinstance(item, dict) for item in parsed["models"]):
        raise ManagerError("invalid_model_catalog", f"{source} 的 models 数组包含无效条目。")
    return parsed


def read_catalog_source(paths: Paths, config: dict[str, Any]) -> tuple[bytes, str, dict[str, Any]]:
    target = configured_catalog_path(paths, config)
    if not target.is_file():
        raise ManagerError("model_catalog_missing", f"模型目录不存在：{target}")
    data = target.read_bytes()
    return data, sha256_bytes(data), parse_catalog_bytes(data)


def catalog_without_model(catalog: dict[str, Any], model: str) -> dict[str, Any]:
    normalized = copy.deepcopy(catalog)
    normalized["models"] = [item for item in normalized["models"] if item.get("slug") != model]
    return normalized


def catalog_model_entry(catalog: dict[str, Any], model: str) -> dict[str, Any] | None:
    matches = [item for item in catalog["models"] if item.get("slug") == model]
    if len(matches) > 1:
        raise ManagerError("duplicate_model_catalog_entry", f"模型目录中存在多个 {model} 条目。")
    return matches[0] if matches else None


def supported_reasoning_levels() -> list[dict[str, str]]:
    return [
        {"effort": "none", "description": "Direct model response with reasoning disabled"},
        {"effort": "low", "description": "Fast responses with lighter reasoning"},
        {"effort": "medium", "description": "Balanced reasoning for general coding tasks"},
        {"effort": "high", "description": "Greater reasoning depth for coding and agent tasks"},
    ]


def catalog_with_model(data: bytes, model: str, effort: str, supports_vision: bool) -> tuple[bytes, bool]:
    model = validate_model_id(model)
    effort = validate_reasoning_effort(effort)
    if not isinstance(supports_vision, bool):
        raise ManagerError("invalid_vision", "识图能力必须由用户明确选择 yes 或 no。")
    before = parse_catalog_bytes(data)
    existing = catalog_model_entry(before, model)
    created = existing is None
    if existing is None:
        template = catalog_model_entry(before, "auto")
        if template is None:
            raise ManagerError("model_catalog_template_missing", "模型目录缺少 auto 模板，无法安全创建自定义模型条目。")
        target = copy.deepcopy(template)
        target["slug"] = model
        target["display_name"] = model
        target["description"] = MANAGED_CATALOG_DESCRIPTION
    else:
        target = copy.deepcopy(existing)
        target.setdefault("display_name", model)
    target["default_reasoning_level"] = effort
    target["supported_reasoning_levels"] = supported_reasoning_levels()
    target["input_modalities"] = ["text", "image"] if supports_vision else ["text"]
    target["visibility"] = "list"
    target["supported_in_api"] = True

    after = copy.deepcopy(before)
    if existing is None:
        after["models"].append(target)
    else:
        index = after["models"].index(existing)
        after["models"][index] = target
    if catalog_without_model(before, model) != catalog_without_model(after, model):
        raise ManagerError("protected_model_catalog_changed", "拒绝修改所选模型条目之外的模型目录内容。")
    output = json.dumps(after, ensure_ascii=False, indent=2).encode("utf-8")
    parse_catalog_bytes(output)
    return output, created


def catalog_model_matches(catalog: dict[str, Any], model: str, effort: str, supports_vision: bool) -> bool:
    entry = catalog_model_entry(catalog, model)
    if entry is None:
        return False
    efforts = {
        item.get("effort")
        for item in entry.get("supported_reasoning_levels", [])
        if isinstance(item, dict)
    }
    modalities = entry.get("input_modalities")
    expected_modalities = ["text", "image"] if supports_vision else ["text"]
    return (
        entry.get("default_reasoning_level") == effort
        and effort in efforts
        and modalities == expected_modalities
        and entry.get("supported_in_api") is True
    )


def catalog_without_managed_model(data: bytes, model: str) -> bytes:
    before = parse_catalog_bytes(data)
    existing = catalog_model_entry(before, model)
    if existing is None or existing.get("description") != MANAGED_CATALOG_DESCRIPTION:
        return data
    after = copy.deepcopy(before)
    after["models"] = [item for item in after["models"] if item.get("slug") != model]
    if catalog_without_model(before, model) != after:
        raise ManagerError("protected_model_catalog_changed", "停用操作修改了受管模型条目之外的模型目录内容。")
    output = json.dumps(after, ensure_ascii=False, indent=2).encode("utf-8")
    parse_catalog_bytes(output)
    return output


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
        raise ManagerError("write_scope_violation", "拒绝写入允许清单之外的 Codex 配置文件。")


def assert_config_target(paths: Paths, target: Path) -> None:
    if target.resolve() != paths.config.resolve():
        raise ManagerError("write_scope_violation", "拒绝写入允许清单之外的 Codex 配置文件。")


def assert_catalog_target(paths: Paths, target: Path) -> None:
    if target.resolve() != paths.catalog.resolve():
        raise ManagerError("write_scope_violation", "拒绝写入允许清单之外的模型目录文件。")


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


def atomic_write_config(paths: Paths, data: bytes, expected_digest: str) -> None:
    target = paths.config
    assert_config_target(paths, target)
    if file_digest(target) != expected_digest:
        raise ManagerError(
            "protected_config_changed",
            "检测到 config.toml 在操作期间被其他进程修改；已停止且不会覆盖外部变更。",
            {"path": str(target)},
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManagerError("invalid_config", "准备写入的 config.toml 不是 UTF-8。") from exc
    parse_toml_text(text, "config.toml")
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, target.stat().st_mode & 0o777)
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def atomic_write_catalog(paths: Paths, data: bytes, expected_digest: str) -> None:
    target = paths.catalog
    assert_catalog_target(paths, target)
    if file_digest(target) != expected_digest:
        raise ManagerError(
            "protected_model_catalog_changed",
            "检测到 codex-models.json 在操作期间被其他进程修改；已停止且不会覆盖外部变更。",
            {"path": str(target)},
        )
    parse_catalog_bytes(data)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, target.stat().st_mode & 0o777)
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


def restore_config(paths: Paths, previous: bytes, expected_current_digest: str) -> None:
    atomic_write_config(paths, previous, expected_current_digest)


def restore_catalog(paths: Paths, previous: bytes, expected_current_digest: str) -> None:
    atomic_write_catalog(paths, previous, expected_current_digest)


def install(paths: Paths, _codex_bin: str, model: str, effort: str, vision: bool, replace_agent: bool = False) -> dict[str, Any]:
    model = validate_model_id(model)
    effort = validate_reasoning_effort(effort)
    if not isinstance(vision, bool):
        raise ManagerError("invalid_vision", "识图能力必须由用户明确选择 yes 或 no。")
    config_bytes, config_digest, config_text, config = read_config_source(paths)
    provider = configured_parent_provider(config)
    if not provider:
        raise ManagerError("parent_provider_unconfigured", "桌面配置中没有明确的父 model_provider。")
    catalog_bytes, catalog_digest, catalog = read_catalog_source(paths, config)
    target = expected_agent_text(model, provider, effort, vision).encode("utf-8")
    target_config = config_with_default_subagent_settings(config_text, model, effort).encode("utf-8")
    target_config_digest = sha256_bytes(target_config)
    target_catalog, catalog_created = catalog_with_model(catalog_bytes, model, effort, vision)
    target_catalog_digest = sha256_bytes(target_catalog)
    previous = paths.agent.read_bytes() if paths.agent.is_file() else None
    previous_managed = previous is not None and MANAGED_MARKER.encode("utf-8") in previous
    if previous is not None and previous != target and not previous_managed and not replace_agent:
        raise ManagerError(
            "conflict",
            "现有 CustomAgent.toml 与目标配置不同。",
            {"path": str(paths.agent), "resolution": "确认完整覆盖范围后，以 --confirmed --replace-agent 重试。"},
        )
    changed = previous != target
    config_changed = config_bytes != target_config
    catalog_changed = catalog_bytes != target_catalog
    config_written = False
    catalog_written = False
    agent_written = False
    try:
        if catalog_changed:
            atomic_write_catalog(paths, target_catalog, catalog_digest)
            catalog_written = True
        if config_changed:
            atomic_write_config(paths, target_config, config_digest)
            config_written = True
        if changed:
            atomic_write_agent(paths, target)
            agent_written = True
        current_digest, current_config = read_config_snapshot(paths)
        if current_digest != target_config_digest or configured_default_subagent_model(current_config) != model:
            raise ManagerError("default_subagent_model_write_failed", "默认子智能体模型写入后验收失败。")
        if configured_default_subagent_effort(current_config) != effort:
            raise ManagerError("default_subagent_effort_write_failed", "默认子智能体思考强度写入后验收失败。")
        if config_without_managed_subagent_settings(config) != config_without_managed_subagent_settings(current_config):
            raise ManagerError("protected_config_changed", "config.toml 中出现了允许字段之外的变化。")
        current_catalog_bytes = paths.catalog.read_bytes()
        if sha256_bytes(current_catalog_bytes) != target_catalog_digest:
            raise ManagerError("protected_model_catalog_changed", "模型目录写入后被其他进程修改。")
        current_catalog = parse_catalog_bytes(current_catalog_bytes)
        if not catalog_model_matches(current_catalog, model, effort, vision):
            raise ManagerError("model_catalog_write_failed", "模型目录中的自定义子智能体模型没有正确写入。")
        if catalog_without_model(catalog, model) != catalog_without_model(current_catalog, model):
            raise ManagerError("protected_model_catalog_changed", "模型目录中出现了所选模型条目之外的变化。")
    except Exception as error:
        rollback_errors: list[str] = []
        if agent_written:
            try:
                if paths.agent.is_file() and paths.agent.read_bytes() == target:
                    restore_agent(paths, previous)
                else:
                    rollback_errors.append("CustomAgent.toml 已被外部修改，未覆盖该外部变化。")
            except Exception as exc:
                rollback_errors.append(f"CustomAgent.toml 回滚失败：{exc}")
        if config_written:
            try:
                restore_config(paths, config_bytes, target_config_digest)
            except Exception as exc:
                rollback_errors.append(f"config.toml 回滚失败：{exc}")
        if catalog_written:
            try:
                restore_catalog(paths, catalog_bytes, target_catalog_digest)
            except Exception as exc:
                rollback_errors.append(f"codex-models.json 回滚失败：{exc}")
        if rollback_errors:
            raise ManagerError("rollback_incomplete", "配置失败且自动回滚不完整。", {"errors": rollback_errors}) from error
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
        "provider_url_unchanged": True,
        "default_subagent_model": model,
        "default_subagent_reasoning_effort": effort,
        "model_catalog": str(paths.catalog),
        "model_catalog_registered": True,
        "protected_config_fields_unchanged": True,
        "write_allowlist": [str(paths.agent), str(paths.config), str(paths.catalog)],
        "changed": changed,
        "config_changed": config_changed,
        "catalog_changed": catalog_changed,
        "catalog_entry_created": catalog_created,
        "replaced_conflicting_agent": bool(previous is not None and changed and not previous_managed and replace_agent),
    }


def static_status(paths: Paths, codex_bin: str | None = None) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "config_exists": paths.config.is_file(),
        "agent_exists": paths.agent.is_file(),
        "model_catalog_exists": paths.catalog.is_file(),
        "write_allowlist_exact": [str(paths.agent), str(paths.config), str(paths.catalog)],
        "protected_config_fields_only": True,
        "desktop_codex_detected": bool(codex_bin),
    }
    errors: list[str] = []
    provider: str | None = None
    default_model: str | None = None
    default_effort: str | None = None
    config: dict[str, Any] | None = None
    settings: dict[str, Any] = {}
    try:
        _, config = read_config_snapshot(paths)
        provider = configured_parent_provider(config)
        default_model = configured_default_subagent_model(config)
        default_effort = configured_default_subagent_effort(config)
        configured_catalog_path(paths, config)
        checks["config_valid"] = True
        checks["parent_provider_configured"] = bool(provider)
        checks["default_subagent_model_configured"] = bool(default_model)
        checks["default_subagent_effort_configured"] = bool(default_effort)
        checks["model_catalog_configured"] = True
    except ManagerError as exc:
        checks["config_valid"] = False
        checks["parent_provider_configured"] = False
        checks["default_subagent_model_configured"] = False
        checks["default_subagent_effort_configured"] = False
        checks["model_catalog_configured"] = False
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
    checks["default_model_matches_agent"] = bool(default_model) and default_model == settings.get("model")
    checks["default_effort_matches_agent"] = bool(default_effort) and default_effort == settings.get("reasoning_effort")
    try:
        if config is None:
            raise ManagerError("invalid_config", "config.toml 尚未通过解析。")
        _, _, catalog = read_catalog_source(paths, config)
        checks["model_catalog_valid"] = True
        checks["model_catalog_matches_agent"] = bool(settings) and catalog_model_matches(
            catalog,
            settings.get("model"),
            settings.get("reasoning_effort"),
            settings.get("supports_vision"),
        )
    except (ManagerError, OSError) as exc:
        checks["model_catalog_valid"] = False
        checks["model_catalog_matches_agent"] = False
        errors.append(str(exc))
    required = (
        "config_valid",
        "parent_provider_configured",
        "default_subagent_model_configured",
        "default_subagent_effort_configured",
        "model_catalog_configured",
        "model_catalog_exists",
        "model_catalog_valid",
        "model_catalog_matches_agent",
        "agent_exists",
        "agent_valid",
        "agent_managed",
        "model_selected",
        "reasoning_effort_valid",
        "vision_setting_present",
        "workspace_write",
        "provider_inherited",
        "default_model_matches_agent",
        "default_effort_matches_agent",
    )
    ready = all(checks.get(key) is True for key in required)
    status = "configured" if ready else "configuration_missing" if not paths.agent.is_file() else "partial"
    return result(
        status,
        skill_name=SKILL_NAME,
        selected_model=settings.get("model"),
        default_subagent_model=default_model,
        default_subagent_reasoning_effort=default_effort,
        reasoning_effort=settings.get("reasoning_effort"),
        supports_vision=settings.get("supports_vision"),
        sandbox_mode=settings.get("sandbox_mode"),
        execution_mode=AGENT_EXECUTION_MODE,
        parent_provider=provider,
        parent_credentials_untouched=True,
        model_catalog=str(paths.catalog),
        write_allowlist=[str(paths.agent), str(paths.config), str(paths.catalog)],
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
    with inherited_temp_directory("codex-custom-agent-write-test-") as directory:
        repository = directory / "repo"
        repository.mkdir()
        subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.name", "Codex Test"], check=True)
        subprocess.run(["git", "-C", str(repository), "config", "user.email", "codex-test@localhost"], check=True)
        (repository / "baseline.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "baseline.txt"], check=True)
        subprocess.run(["git", "-C", str(repository), "commit", "-q", "-m", "baseline"], check=True)
        prompt = (
            "Use the native spawn_agent tool exactly once with agent_type CustomAgent and fork_context false. "
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
        content_ok = marker.is_file() and read_bytes_with_retry(marker) in {
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


def default_native_test(paths: Paths, codex_bin: str, model: str, effort: str) -> dict[str, Any]:
    _, config = read_config_snapshot(paths)
    parent_model = configured_parent_model(config)
    provider = configured_parent_provider(config)
    if not parent_model or not provider:
        raise ManagerError("parent_route_unconfigured", "桌面配置中没有明确的父模型或父 Provider。")
    if configured_default_subagent_model(config) != model:
        raise ManagerError("default_subagent_model_mismatch", "config.toml 的默认子智能体模型与 CustomAgent 不一致。")
    if configured_default_subagent_effort(config) != effort:
        raise ManagerError("default_subagent_effort_mismatch", "config.toml 的默认子智能体思考强度与 CustomAgent 不一致。")
    env = dict(os.environ)
    env["CODEX_HOME"] = str(paths.home)
    with inherited_temp_directory("codex-default-subagent-test-") as directory:
        repository = directory / "repo"
        repository.mkdir()
        subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
        prompt = (
            "This is a configuration diagnostic. Use the native spawn_agent tool exactly once without specifying "
            "agent_type, model, or reasoning effort. Ask the child to reply exactly DEFAULT_SUBAGENT_MODEL_OK, "
            "wait for it, and return only its final response. Do not modify any file."
        )
        proc = subprocess.run(
            [
                codex_bin,
                "exec",
                "--json",
                "-s",
                "read-only",
                "-C",
                str(repository),
                "-m",
                parent_model,
                "-c",
                "project_doc_max_bytes=0",
                prompt,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
        )
    if proc.returncode != 0:
        raise ManagerError("default_native_test_failed", "未指定角色的原生子智能体测试失败。", {"stderr": proc.stderr[-1200:]})
    child_ids, states = parse_native_events(proc.stdout)
    child_id = child_ids[0] if len(child_ids) == 1 else None
    child_state = states.get(child_id) if child_id else None
    metadata = wait_for_child_metadata(paths, child_id) if child_id else None
    expected_route = {"model_provider": provider, "model": model, "reasoning_effort": effort}
    marker_ok = "DEFAULT_SUBAGENT_MODEL_OK" in proc.stdout
    metadata_ok = bool(metadata) and all(metadata.get(key) == value for key, value in expected_route.items())
    if child_state and child_state.get("status") not in {None, "completed"}:
        raise ManagerError("default_native_child_failed", "默认子智能体启动或执行失败。", {"child_state": child_state})
    if len(child_ids) != 1 or not metadata_ok or not marker_ok:
        raise ManagerError(
            "default_native_route_mismatch",
            "未指定 agent_type 的子智能体没有使用配置的默认模型。",
            {"child_ids": child_ids, "metadata": metadata, "expected": expected_route, "marker_verified": marker_ok},
        )
    return {
        "default_subagent_native": True,
        "default_child_id": child_id,
        "default_child_role": metadata.get("agent_role") if metadata else None,
        "default_child_model": model,
        "default_child_reasoning_effort": effort,
        "default_child_provider": provider,
    }


def run_tests(paths: Paths, codex_bin: str) -> dict[str, Any]:
    config_digest, initial_config = read_config_snapshot(paths)
    _, catalog_digest, initial_catalog = read_catalog_source(paths, initial_config)
    status = static_status(paths, codex_bin)
    if status["status"] != "configured":
        raise ManagerError("not_configured", "静态配置尚未完整，不能运行实时测试。", status)
    direct = direct_test(paths, codex_bin, status["selected_model"], status["reasoning_effort"])
    native = native_test(paths, codex_bin, status["selected_model"], status["reasoning_effort"])
    default_native = default_native_test(paths, codex_bin, status["selected_model"], status["reasoning_effort"])
    assert_config_unchanged(paths, config_digest)
    assert_catalog_unchanged(paths, catalog_digest)
    _, final_config = read_config_snapshot(paths)
    _, _, final_catalog = read_catalog_source(paths, final_config)
    if initial_config != final_config:
        raise ManagerError("protected_config_changed", "实时验收期间 config.toml 的配置语义发生变化。")
    if initial_catalog != final_catalog:
        raise ManagerError("protected_model_catalog_changed", "实时验收期间 codex-models.json 的配置语义发生变化。")
    return result(
        "ready",
        **direct,
        **native,
        **default_native,
        supports_vision=status["supports_vision"],
        protected_config_fields_unchanged=True,
        write_allowlist=[str(paths.agent), str(paths.config), str(paths.catalog)],
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
    previous_config = paths.config.read_bytes()
    previous_catalog = paths.catalog.read_bytes()
    previous = paths.agent.read_bytes() if paths.agent.is_file() else None
    installed = install(paths, codex_bin, model, effort, vision, replace_agent=replace_agent)
    installed_config_digest = file_digest(paths.config)
    installed_catalog_digest = file_digest(paths.catalog)
    installed_agent_digest = file_digest(paths.agent)
    if skip_live_test:
        return result("configured", **installed, new_task_required=True, restart_required=True)
    try:
        tested = run_tests(paths, codex_bin)
    except Exception as error:
        rollback_errors: list[str] = []
        try:
            if file_digest(paths.agent) == installed_agent_digest:
                restore_agent(paths, previous)
            else:
                rollback_errors.append("CustomAgent.toml 已被外部修改，未覆盖该外部变化。")
        except Exception as exc:
            rollback_errors.append(f"CustomAgent.toml 回滚失败：{exc}")
        try:
            if installed_config_digest is None:
                rollback_errors.append("无法确定已安装 config.toml 的摘要，未自动覆盖。")
            else:
                restore_config(paths, previous_config, installed_config_digest)
        except Exception as exc:
            rollback_errors.append(f"config.toml 回滚失败：{exc}")
        try:
            if installed_catalog_digest is None:
                rollback_errors.append("无法确定已安装 codex-models.json 的摘要，未自动覆盖。")
            else:
                restore_catalog(paths, previous_catalog, installed_catalog_digest)
        except Exception as exc:
            rollback_errors.append(f"codex-models.json 回滚失败：{exc}")
        if rollback_errors:
            raise ManagerError(
                "rollback_incomplete",
                "实时验收失败且自动回滚不完整。",
                {
                    "cause": error.code if isinstance(error, ManagerError) else type(error).__name__,
                    "cause_message": str(error),
                    "errors": rollback_errors,
                },
            ) from error
        raise
    return {**tested, **installed}


def disable(paths: Paths) -> dict[str, Any]:
    if not paths.agent.is_file():
        return result(
            "disabled",
            changed=False,
            config_changed=False,
            catalog_changed=False,
            write_allowlist=[str(paths.agent), str(paths.config), str(paths.catalog)],
        )
    settings = read_agent_settings(paths)
    if settings.get("managed") is not True:
        raise ManagerError("not_managed", "CustomAgent.toml 不是本 Skill 生成的文件，拒绝删除。")
    model = settings.get("model")
    effort = settings.get("reasoning_effort")
    if not isinstance(model, str) or effort not in REASONING_EFFORTS:
        raise ManagerError("invalid_agent", "CustomAgent.toml 没有有效模型或思考强度，拒绝自动停用。")
    config_bytes, config_digest, config_text, config = read_config_source(paths)
    catalog_bytes, catalog_digest, _ = read_catalog_source(paths, config)
    target_config = config_without_managed_subagent_settings_text(config_text, model, effort).encode("utf-8")
    target_catalog = catalog_without_managed_model(catalog_bytes, model)
    target_config_digest = sha256_bytes(target_config)
    target_catalog_digest = sha256_bytes(target_catalog)
    previous = paths.agent.read_bytes()
    config_changed = target_config != config_bytes
    catalog_changed = target_catalog != catalog_bytes
    try:
        if catalog_changed:
            atomic_write_catalog(paths, target_catalog, catalog_digest)
        if config_changed:
            atomic_write_config(paths, target_config, config_digest)
        delete_agent(paths)
        if file_digest(paths.config) != target_config_digest:
            raise ManagerError("protected_config_changed", "停用期间 config.toml 被其他进程修改。")
        if file_digest(paths.catalog) != target_catalog_digest:
            raise ManagerError("protected_model_catalog_changed", "停用期间 codex-models.json 被其他进程修改。")
    except Exception as error:
        rollback_errors: list[str] = []
        try:
            if not paths.agent.exists():
                restore_agent(paths, previous)
        except Exception as exc:
            rollback_errors.append(f"CustomAgent.toml 回滚失败：{exc}")
        if config_changed:
            try:
                restore_config(paths, config_bytes, target_config_digest)
            except Exception as exc:
                rollback_errors.append(f"config.toml 回滚失败：{exc}")
        if catalog_changed:
            try:
                restore_catalog(paths, catalog_bytes, target_catalog_digest)
            except Exception as exc:
                rollback_errors.append(f"codex-models.json 回滚失败：{exc}")
        if rollback_errors:
            raise ManagerError("rollback_incomplete", "停用失败且自动回滚不完整。", {"errors": rollback_errors}) from error
        raise
    return result(
        "disabled",
        changed=True,
        config_changed=config_changed,
        catalog_changed=catalog_changed,
        parent_credentials_untouched=True,
        protected_config_fields_unchanged=True,
        write_allowlist=[str(paths.agent), str(paths.config), str(paths.catalog)],
    )


def uninstall(paths: Paths) -> dict[str, Any]:
    disabled = disable(paths)
    return result(
        "uninstalled",
        disabled=disabled,
        write_allowlist=[str(paths.agent), str(paths.config), str(paths.catalog)],
    )


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
                "拒绝执行持久化子智能体配置变更。只有用户明确要求配置，或亲自在本机设置页保存模型选项后，"
                "才能传入 --confirmed。",
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
