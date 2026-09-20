# Repository Instructions

当用户要求“阅读这个项目仓库，帮我配置这个技能”或表达同等意图时，执行以下流程，不要求用户自行复制文件或把凭据发到聊天。

1. 完整阅读根目录 `SKILL.md`、`references/api-key-setup.md` 和 `references/compatibility.md`。
2. 同时阅读 `references/troubleshooting.md`。安装和排障不得修改用户当前业务项目中的任何文件；需要检查、测试或修改本仓库时，使用系统临时目录的独立克隆或隔离 worktree。
3. 将本仓库作为 `deepseek` 安装到全局 Codex skills 目录：优先使用当前 `CODEX_HOME/skills/deepseek`，未设置时使用 `~/.codex/skills/deepseek`。只复制本仓库内容，排除 `.git`、`node_modules`、`__pycache__` 和 `*.pyc`；不得删除或覆盖全局 skills 下其他技能或非本技能文件。若旧版 `codex-custom-subagent` 目录存在，先安装并验证 `deepseek`，确认受管配置已指向新目录后再移除旧目录；不得提前删除。若同名目标已存在且来源不明或有冲突，先报告差异并征求用户决定。
4. 在安装后的 Skill 目录安装锁定的配置页依赖：`npm --prefix <skill-dir>/scripts/credential-ui ci --ignore-scripts`。
5. 用 `scripts/codex_custom_agent.py status --json` 只读检查。缺少任一配置时，运行 `node <skill-dir>/scripts/credential-ui/src/profile.ts setup default`，把返回的 localhost 链接展示给用户并等待用户亲自保存 URL、Key、模型三项；不要代填、读取或回显值。URL 必须使用用户或网关给出的精确 Base URL，不得自行追加 `/v1` 或其他路径；用户给出的合法路径必须保留。
6. 保存后通过包装器运行：`node <skill-dir>/scripts/credential-ui/src/profile.ts run default -- <python3> <skill-dir>/scripts/codex_custom_agent.py setup --base-url-env --api-key-env --model-env --json`。
7. 若 setup 未完成实时验收，再运行 `test --json`。按结构化错误修复；不要手工改受管 TOML、JSON、Agent 文件、父 Provider 认证或 `auth.json`。特别禁止把只支持子模型的 Key 改成父 Provider 的全局凭据。
8. 检查 `route_mode`、`credential_source`、`model`、`reasoning_effort` 和 `agent_role`。共享网关继承路由使用父 Provider 凭据，直连专用 Key 成功不能替代原生验收。
9. 只在直连口令、原生口令和数据库实际路由元数据均通过后报告成功。若结果要求重启或新任务，明确告知用户重启 Codex 并打开新任务。
10. 安装角色不会自动替换标准 worker。只有用户明确要求“默认使用 CustomAgent”时，才在备份后按 `references/troubleshooting.md` 添加全局或项目 `AGENTS.md` 路由规则；验证文件无控制字符，并在全新任务中检查子线程数据库。失败时不得静默回退到父模型或标准 Agent。

任何时候都不得将 API Key 写入仓库、普通文件、命令参数、日志或最终回复。
