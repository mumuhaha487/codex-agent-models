# 兼容性与安全边界

## 支持范围

- macOS、Windows、Python 3.11+，Codex 桌面应用至少启动过一次。
- 模型 ID 长度不超过 128，只含字母、数字、点、下划线、冒号、斜杠或连字符。
- 思考强度只允许 `none`、`low`、`medium`、`high`。
- 识图选项只允许 `yes` 或 `no`。
- 当前父 Provider 必须能通过现有认证访问目标模型，并支持 Codex 所需的 Responses 工具调用。

## Codex 配置边界

允许写入：

```text
$CODEX_HOME/agents/CustomAgent.toml
$CODEX_HOME/codex-models.json -> selected model entry only
$CODEX_HOME/config.toml -> [agents].default_subagent_model and default_subagent_reasoning_effort only
```

禁止写入：

```text
$CODEX_HOME/auth.json
config.toml 中除两个受管 agents 字段外的任何字段
codex-models.json 中除页面所选模型条目外的任何条目
```

管理脚本不会创建独立 Provider、替换父认证、修改 API URL、修改主模型、改变 `model_catalog_json` 路径或写入仓库默认模型值。页面模型必须由用户明确填写；仅在现有模型目录中动态登记该模型。

配置编辑器保留现有文本，只新增或替换两个默认子智能体字段。模型目录编辑器保留其他条目，仅新增或更新页面所选模型。写入前后均解析验证并检查 SHA-256；脚本不会覆盖未知外部变化。

## Agent 行为

`CustomAgent.toml` 包含目标模型、父 Provider、`model_reasoning_effort`（`none`、`low`、`medium` 或 `high`）、`sandbox_mode = "workspace-write"`、识图标记和隔离工作指令。

`agents.default_subagent_model` 与 `agents.default_subagent_reasoning_effort` 使未指定角色、模型或思考强度的普通子智能体使用同一设置（包括 `none`，不会被强制转为 `high`）。显式 `CustomAgent` 仍从 Agent 文件读取 Provider、思考强度、沙箱和工作指令。

识图选项只修改所选模型条目的 `input_modalities`。`yes` 表示用户确认模型支持图片；`no` 表示只使用文本。

## Git 隔离

可写任务必须在 `task_worktree.py start` 创建的隔离 worktree 中运行；主工作区不干净时拒绝启动，不自动 stash、提交、reset 或清理用户修改。

任务分支只用于必要的隔离回退流程，完成后立即清理。普通仓库修改与上传直接使用现有远程默认 `main`，不自行增加分支。
