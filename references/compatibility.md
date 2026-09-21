# 兼容性与安全边界

## 支持范围

- macOS、Windows、Python 3.11+，Codex 桌面应用至少启动过一次。
- 模型 ID 长度不超过 128，只含字母、数字、点、下划线、冒号、斜杠或连字符。
- 思考强度只允许 `low`、`medium`、`high`。
- 识图选项只允许 `yes` 或 `no`。
- 父 Provider 必须能通过当前认证访问目标模型，并支持 Codex 所需的 Responses 工具调用。

## Codex 配置边界

唯一可写文件：

```text
$CODEX_HOME/agents/CustomAgent.toml
```

只读保护文件：

```text
$CODEX_HOME/config.toml
```

脚本从顶层 `model_provider` 读取父 Provider，并把该字符串写入 Agent 文件。脚本不会：

- 修改、恢复、格式化或清理 `config.toml`。
- 读取或写入 `auth.json`。
- 创建独立 Provider 或替换父认证。
- 创建或选择模型目录。
- 写入状态清单、配置备份、角色注册块或功能标志。
- 保存、替换或删除 API URL 与 API Key。

文件白名单由代码强制检查。操作前后会比较 `config.toml` 哈希；检测到变化时，脚本不碰主配置，只撤销本次 Agent 文件变更。

## Agent 行为

`CustomAgent.toml` 包含目标模型、父 Provider、`model_reasoning_effort`、`sandbox_mode = "workspace-write"`、识图标记和隔离工作指令。Agent 文件由 Codex 自动发现，不需要在 `config.toml` 添加角色注册块。

识图选项不修改模型目录。`yes` 表示用户确认模型支持图片，并要求子智能体直接使用附带图片；`no` 表示只使用文本，由主 Agent 提供视觉观察。

## Git 隔离

日常调用显式使用 `spawn_agent(agent_type="CustomAgent", fork_turns="none")`。可写任务必须在 `task_worktree.py start` 创建的隔离 worktree 中运行；主工作区不干净时拒绝启动，不自动 stash、提交、reset 或清理用户修改。

任务分支仅用于该必要的隔离回退流程，完成后立即清理。普通仓库修改与上传直接使用现有主分支，不自行增加分支。
