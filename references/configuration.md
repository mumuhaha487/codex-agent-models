# 子代理设置

本机页面只收集三个非认证设置：模型 ID、思考强度和是否支持识图。页面不显示或修改 API URL、API Key、父 Provider 或主模型。

## 一次完成

收到明确的仓库配置请求后，直接运行：

```text
npm --prefix <skill-dir>/scripts/credential-ui ci --ignore-scripts
node <skill-dir>/scripts/credential-ui/src/profile.ts apply default --confirmed -- <python3> <skill-dir>/scripts/codex_custom_agent.py setup --model-env --effort-env --vision-env --confirmed --replace-agent --json
```

把命令输出的 localhost URL 打开给用户。用户只填写：

| 字段 | 环境变量 | 可选值 |
| --- | --- | --- |
| 子代理模型 | `CUSTOM_AGENT_MODEL` | 精确模型 ID，无默认值 |
| 思考强度 | `CUSTOM_AGENT_REASONING_EFFORT` | `low`、`medium`、`high` |
| 支持识图 | `CUSTOM_AGENT_VISION` | `yes`、`no` |

用户点击保存即确认应用所填值。`apply` 会自动等待保存、注入三个值、运行管理脚本并等待完整验收，不需要第二条聊天确认或后续手工命令。

## 精确写入范围

管理脚本允许写入：

1. `$CODEX_HOME/agents/CustomAgent.toml`。
2. `$CODEX_HOME/codex-models.json` 中页面所选模型的单个条目。
3. `$CODEX_HOME/config.toml` 中的 `[agents].default_subagent_model` 与 `[agents].default_subagent_reasoning_effort`。

脚本先解析原始 TOML 与模型目录 JSON，再进行最小范围更新，并在写入后重新解析比较。移除两个默认子智能体字段与页面所选模型条目后，修改前后的配置语义必须完全相同，因此主模型、Provider、API URL、模型目录路径、其他模型条目和其他配置无法随操作改变。

`auth.json` 不在写入清单中。配置前后应独立比较其 SHA-256，且不得输出文件内容或 Key。

现有 `CustomAgent.toml` 会在页面保存后按用户所填值完整覆盖。管理脚本不创建持久备份；实时验收失败时自动恢复本次操作前的 Agent 文件、默认子智能体设置和模型目录。检测到任一受管文件被外部并发修改时停止，不覆盖外部内容。

选择识图 `yes` 会把所选模型条目的输入模态登记为文本与图片，并写入 Agent 能力标记和行为指令；目标模型和父 Provider 必须真实支持图片。
