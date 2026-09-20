# Codex Custom Subagent

把任意 OpenAI Responses 兼容端点配置成 Codex 的原生 `CustomAgent`。用户只需把本仓库交给 Codex，然后说：

> 阅读这个项目仓库，帮我配置这个技能

仓库中的 [AGENTS.md](AGENTS.md) 会引导 Codex 安装全局 Skill、检查状态、打开本机安全页面收集 API URL、API Key 和模型 ID，并完成直连与原生路由测试。

## 要求

- Codex 桌面应用和 Python 3.11+
- Node.js 22.18+
- 兼容 OpenAI Responses API 且支持 Codex 工具调用的端点
- HTTPS URL；只有 localhost、回环或私有网络 IP 可以使用 HTTP

API Key 不应粘贴到聊天、命令行或提交到 Git。配置页只绑定 localhost，包装器只向配置进程注入所需值。

API URL 必须填写用户或网关给出的精确 Base URL。安装器不会自行追加 `/v1` 或其他路径；如果服务明确给出的 URL 本身包含 `/v1`，则应原样保留。配置页中 URL 和模型 ID 使用明文输入框，API Key 仍使用密码框；三项都不会回填已保存值。

配置完成后，主 Agent 把需求拆成计划点，调用只读 `CustomAgent` 取得候选补丁，在隔离副本中验收；只有通过后才写入真实工作区。详见 [SKILL.md](SKILL.md)。
