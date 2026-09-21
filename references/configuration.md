# 子代理设置

本机页面只收集三个非认证设置：模型 ID、思考强度和是否支持识图。页面不显示 API URL 或 API Key，也不会修改 Codex 父 Provider。

## 二次确认

首次收到创建或修改请求时，只运行 `status` 并展示当前值、目标值、唯一写入文件、后续持久影响，以及模型能力和费用风险。必须等用户在下一条独立消息中只回复 `已确认` 后，才能打开设置页或运行写命令。

## 打开页面

```text
npm --prefix <skill-dir>/scripts/credential-ui ci --ignore-scripts
node <skill-dir>/scripts/credential-ui/src/profile.ts setup default --confirmed
```

页面同页提交三个字段：

| 字段 | 环境变量 | 可选值 |
| --- | --- | --- |
| 子代理模型 | `CUSTOM_AGENT_MODEL` | 用户明确填写的精确模型 ID，无默认值 |
| 思考强度 | `CUSTOM_AGENT_REASONING_EFFORT` | `low`、`medium`、`high` |
| 支持识图 | `CUSTOM_AGENT_VISION` | `yes`、`no` |

这些值保存在当前用户的系统凭据后端，并由包装器注入管理脚本；它们不是 API 凭据。已有项留空会保留，页面不会回填原值。

## 应用设置

```text
node <skill-dir>/scripts/credential-ui/src/profile.ts run default -- python3 <skill-dir>/scripts/codex_custom_agent.py setup --model-env --effort-env --vision-env --confirmed --json
```

管理脚本只写 `$CODEX_HOME/agents/CustomAgent.toml`。它只读获取父 `model_provider`，并在写入前后比较 `config.toml` 的 SHA-256。哈希变化会使操作失败，并只回滚本次 Agent 文件写入。

现有 Agent 文件冲突时，只有确认范围明确包含完整覆盖，才可额外传入 `--replace-agent`。不会创建持久备份文件。

选择识图 `yes` 只把能力标记和相应指令写入 Agent 文件；模型和上游 Provider 必须真实支持图片。无法确认时选择 `no`。
