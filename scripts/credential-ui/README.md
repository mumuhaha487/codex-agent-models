# 本机模型设置页

默认 profile 只包含模型、思考强度和识图能力，不包含 URL 或 API Key。

完整自动入口：

```text
node src/profile.ts apply default --confirmed -- <python3> ../codex_custom_agent.py setup --model-env --effort-env --vision-env --confirmed --replace-agent --json
```

`apply` 启动 30 分钟有效的 localhost 页面。用户保存后，它自动从当前用户系统凭据后端读取三项非认证设置，只注入目标管理进程，等待配置和验收结束，再以该进程的退出码结束。页面取消、过期或业务程序失败都会返回非零状态。

辅助入口：

```text
node src/profile.ts status default
node src/profile.ts setup default --confirmed
node src/profile.ts run default -- <program> <args...>
```

`status` 只返回每项是否存在，不返回值。`setup` 只打开页面，`run` 只注入已有值；日常配置应使用单一 `apply` 入口，避免保存后遗漏应用步骤。
