# 兼容性与安全边界

## 支持范围

- macOS、Windows、Python 3.11+，且 Codex 桌面应用至少启动过一次。
- 用户指定的端点必须兼容 OpenAI Responses API，并支持 Codex 需要的工具调用和所选 `high` reasoning effort。
- 模型 ID 必须是网关公布的精确 ID，长度不超过 128，只含字母、数字、点、下划线、冒号、斜杠或连字符。
- 子 Agent 固定为文本、只读工作者；它不直接写共享工作区，只返回完整候选补丁。

## 受管位置

默认 `CODEX_HOME` 为 `~/.codex`：

- Codex 配置：`$CODEX_HOME/config.toml`
- 合并模型目录：`$CODEX_HOME/models-with-custom-agent.json`
- Agent 文件：`$CODEX_HOME/agents/CustomAgent.toml`
- 状态与备份：`$CODEX_HOME/codex-custom-subagent/`
- 运行时 Key：系统凭据目标 `codex-custom-subagent-api-key`

程序不修改顶层 `model` 或 `model_provider`。自定义模型元数据从当前父模型条目深拷贝，再只替换自定义模型标识、显示名、说明、文本输入能力、`high` 默认思考程度和 `v1` 多 Agent 版本。

## 原生派发验收

父模型从当前 Codex 配置动态读取。管理程序将 `features.multi_agent_v2` 设为 `false`，并把父模型 `multi_agent_version` 设为 `v1`，以使用当前可验证的明文跨 Agent 派发路径。父模型变化后运行 `repair`。

日常任务由主 Agent 直接调用：

```text
spawn_agent(agent_type="CustomAgent", fork_turns="none", ...)
```

实时验收必须同时确认子 Agent 返回 `NATIVE_CUSTOM_AGENT_OK`，以及 `$CODEX_HOME/state_*.sqlite` 的 `threads` 元数据：精确模型 ID、`high`、`CustomAgent` 和实际 Provider。

当前运行时可能让自定义 Agent 继承父 Provider。只有父 Provider 的规范化 Base URL 与用户配置 URL 完全一致时，才接受该实际 Provider，并报告 `route_mode = inherited_shared_gateway`；否则数据库记录必须是 `custom_agent`。子 Agent 自述不能替代数据库证据。

## URL 与凭据

URL 只允许 HTTPS，或 localhost、回环、私有网络 IP 上的 HTTP；禁止 URL 内嵌凭据、查询参数和片段。使用用户或网关给出的精确 Base URL，不自行追加 `/v1` 或任何其他路径；用户提供的合法路径会保留，仅末尾斜杠被规范化。Key 可采用任意服务格式，但必须为非空、长度不超过 2500 的单行值。

URL、Key 和模型 ID 由用户在本机页面填写，并分别以 `CUSTOM_AGENT_BASE_URL`、`CUSTOM_AGENT_API_KEY`、`CUSTOM_AGENT_MODEL` 注入。URL 和模型字段可见，Key 字段遮蔽；任何字段都不从后端回填。不要在回复、日志摘要、异常或测试夹具中重复 Key。

## 配置事务

写入前创建时间戳备份；配置使用进程锁、候选解析验证和原子替换。写入、卸载或实时测试失败时恢复本次事务开始前的文件。已有但不属于本 Skill 的冲突配置不会被静默覆盖；兼容配置可以采用并报告 `adopted_existing`。
