# 原生子 Agent 路由故障排查

以下行为以 `2026-09-21` 的 Codex 桌面环境为基准，后续版本仍以 `status --json`、`test --json`、实际写入和子线程数据库元数据为准。

## URL 与 Key

原生子 Agent 继承父任务的 Provider 路由。本 Skill 因此只设置子模型、思考强度和识图选项，不读取或修改 URL、Key、父 Provider 认证或 `auth.json`。父凭据必须同时允许访问父模型和子模型。

`route_mode = inherited_parent_provider`、`credential_source = parent_provider` 和 `uses_dedicated_credential = false` 是预期结果。

## 默认选择角色

`$CODEX_HOME/agents/CustomAgent.toml` 让角色可被显式选择，但不会自动替换标准 worker。需要默认使用时，在全局或项目 `AGENTS.md` 写明：

```markdown
- 用户明确要求子智能体或委派时，使用 `agent_type = "CustomAgent"` 和 `fork_turns = "none"`。
- 不直接覆盖模型或思考强度，不静默回退到其他子 Agent。
- 派发前创建受管隔离 worktree；每轮 checkpoint，验收后 integrate 并 finalize。
- 任一任务级失败计数达到 5 时 abort，并由主 Agent 完成，不调用第 6 次。
```

更新后完全重启 Codex 并打开新任务。

## 识图没有生效

1. 运行 `status --json`，确认 `supports_vision = true`。
2. 确认上游模型和父 Provider 实际支持图片。本 Skill 不根据模型名探测能力。
3. 委派时把图片作为 `image` 或 `local_image` 输入传给 `CustomAgent`。
4. 完全重启 Codex 并创建新任务，避免旧会话缓存 Agent 配置。

## 配置冲突

现有 `CustomAgent.toml` 与目标不同且不带本 Skill 标记时，脚本会拒绝覆盖。重新说明完整覆盖后的模型、思考强度、识图选项和唯一文件范围，取得新的独立 `已确认`，再使用 `--confirmed --replace-agent`。

本 Skill 不会清理旧 `config.toml` 字段、模型目录或旧状态文件。发现这些历史内容时只报告，不得顺手修改。

## 成功证据

1. `config.toml` 操作前后 SHA-256 相同。
2. 写入白名单只有 `agents/CustomAgent.toml`。
3. 父 Provider 直连返回 `CUSTOM_AGENT_DIRECT_OK`。
4. 原生子 Agent 在临时 Git 仓库成功写入验收文件。
5. 子线程数据库记录为父 Provider、目标模型、所选思考强度和 `CustomAgent`。

## 常见故障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 子 Agent 报模型无权限 | 父凭据不能访问目标模型 | 给父账号组增加目标模型权限，不替换父认证 |
| 子任务仍是父模型或 worker | 调用没有指定 `CustomAgent` | 增加默认路由规则并在新任务重试 |
| 思考强度仍是旧值 | 旧 Agent 文件或旧任务缓存 | 二次确认后运行 `repair --confirmed --replace-agent`，完全重启 |
| 识图开启但收不到图片 | 委派未附带图片或上游不支持 | 附带图片输入并核实能力 |
| 子 Agent 只返回补丁 | Agent 文件仍是旧只读版本 | 二次确认后完整覆盖 Agent 文件 |
| 无法创建隔离任务 | 主工作区不干净或任务冲突 | 保留用户修改，不自动 stash/reset；先处理冲突 |
| `protected_config_changed` | 操作期间主配置被其他进程改变 | 停止操作并检查外部变更；Skill 不会恢复主配置 |
