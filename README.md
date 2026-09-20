# deepseek

把 DeepSeek Responses 兼容端点配置成 Codex 的原生 `CustomAgent`。用户只需把本仓库交给 Codex，然后说：

> 阅读这个项目仓库，帮我配置这个技能

仓库中的 [AGENTS.md](AGENTS.md) 会引导 Codex 安装全局 Skill、检查状态、打开本机安全页面收集 API URL、API Key 和模型 ID，并完成直连与原生路由测试。

安装与排障只应操作全局 Codex Skill、`CODEX_HOME` 或隔离临时目录；不得在用户交给 Codex 的业务项目中复制测试目录、创建调试文件或修改项目源码。

旧版 Skill 名为 `codex-custom-subagent`。升级时先安装并验证全局 `deepseek` 目录，再删除旧目录；运行时状态、备份和系统凭据标识保持兼容，因此不需要重新粘贴 API Key。

## 要求

- Codex 桌面应用和 Python 3.11+
- Node.js 22.18+
- 兼容 OpenAI Responses API 且支持 Codex 工具调用的端点
- HTTPS URL；只有 localhost、回环或私有网络 IP 可以使用 HTTP

API Key 不应粘贴到聊天、命令行或提交到 Git。配置页只绑定 localhost，包装器只向配置进程注入所需值。

API URL 必须填写用户或网关给出的精确 Base URL。安装器不会自行追加 `/v1` 或其他路径；如果服务明确给出的 URL 本身包含 `/v1`，则应原样保留。配置页中 URL 和模型 ID 使用明文输入框，API Key 仍使用密码框；三项都不会回填已保存值。

## 重要的运行时限制

- 注册 `CustomAgent` 只让角色可以被显式选择，不会自动替换普通 `worker`。日常委派必须使用 `spawn_agent(agent_type="CustomAgent", fork_turns="none")`；需要默认路由时还要配置全局或项目 `AGENTS.md`。
- 当前原生 v1 派发可能继承父 Provider。此时直连测试使用专用 Key，但原生子 Agent 使用父 Provider 的凭据；父账号必须同时支持父模型和子模型。
- 不得用只支持子模型的 Key 覆盖父 Provider 或 `auth.json`，否则主 Codex 会失去父模型访问能力。
- 只有直连口令、原生口令和子线程数据库元数据同时通过，才能报告 `ready`。
- `features.thread_tools` 已不是当前 Codex 功能标志。若设置页仍显示 `session-flags` 警告，但 `config.toml` 已无该字段，需要完全退出并重启 Codex 以清除旧会话参数。

完整原因、故障签名与恢复步骤见 [原生子 Agent 路由与凭据故障排查](references/troubleshooting.md)。

配置完成后，主 Agent 把需求拆成计划点，调用只读 `CustomAgent` 取得候选补丁，在隔离副本中验收；只有通过后才写入真实工作区。详见 [SKILL.md](SKILL.md)。
