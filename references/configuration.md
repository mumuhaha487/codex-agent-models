# 子代理设置

本机页面只保存三个非认证设置：模型 ID、思考强度和识图能力。它不显示 API URL 或 API Key，也不会修改 Codex 父 Provider。

## 打开页面

将当前 `SKILL.md` 所在绝对目录记为 `SKILL_DIR`：

```bash
npm --prefix "$SKILL_DIR/scripts/credential-ui" ci --ignore-scripts
node "$SKILL_DIR/scripts/credential-ui/src/profile.ts" status default
node "$SKILL_DIR/scripts/credential-ui/src/profile.ts" setup default
```

页面只有一个“更新”按钮，三个字段同页提交：

| 字段 | 环境变量 | 可选值 |
| --- | --- | --- |
| 子代理模型 | `CUSTOM_AGENT_MODEL` | 精确模型 ID |
| 思考强度 | `CUSTOM_AGENT_REASONING_EFFORT` | `low`、`medium`、`high` |
| 支持识图 | `CUSTOM_AGENT_VISION` | `yes`、`no` |

设置保存在当前用户的系统凭据后端，只是为了避免普通配置文件被其他工具随意改写；这些值不是 API 凭据。已有项留空会保留，页面不会回填原值。

## 应用设置

```bash
node "$SKILL_DIR/scripts/credential-ui/src/profile.ts" run default -- python3 "$SKILL_DIR/scripts/codex_custom_agent.py" setup --model-env --effort-env --vision-env --json
```

管理程序验证三个值后只写入受管模型目录、`CustomAgent.toml`、角色注册和状态清单。它从当前 `config.toml` 只读获取父模型和父 Provider。

无法确认模型是否支持图片时选择 `no`。选择 `yes` 仅声明能力，不执行自动探测。
