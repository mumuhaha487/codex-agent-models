# Repository Instructions

当用户要求“阅读这个项目仓库，帮我配置这个技能”或表达同等意图时，执行以下流程，不要求用户自行复制文件或把凭据发到聊天。

1. 完整阅读根目录 `SKILL.md`、`references/api-key-setup.md` 和 `references/compatibility.md`。
2. 将本仓库作为 `codex-custom-subagent` 安装到全局 Codex skills 目录：优先使用当前 `CODEX_HOME/skills/codex-custom-subagent`，未设置时使用 `~/.codex/skills/codex-custom-subagent`。只复制本仓库内容，排除 `.git`、`node_modules`、`__pycache__` 和 `*.pyc`；不得删除或覆盖全局 skills 下其他技能或非本技能文件。若同名目标已存在且来源不明或有冲突，先报告差异并征求用户决定。
3. 在安装后的 Skill 目录安装锁定的配置页依赖：`npm --prefix <skill-dir>/scripts/credential-ui ci --ignore-scripts`。
4. 用 `scripts/codex_custom_agent.py status --json` 只读检查。缺少任一配置时，运行 `node <skill-dir>/scripts/credential-ui/src/profile.ts setup default`，把返回的 localhost 链接展示给用户并等待用户亲自保存 URL、Key、模型三项；不要代填、读取或回显值。
5. 保存后通过包装器运行：`node <skill-dir>/scripts/credential-ui/src/profile.ts run default -- <python3> <skill-dir>/scripts/codex_custom_agent.py setup --base-url-env --api-key-env --model-env --json`。
6. 若 setup 未完成实时验收，再运行 `test --json`。按结构化错误修复；不要手工改受管 TOML、JSON、Agent 文件或凭据。
7. 只在直连口令、原生口令和数据库实际路由元数据均通过后报告成功。若结果要求重启或新任务，明确告知用户重启 Codex 并打开新任务。

任何时候都不得将 API Key 写入仓库、普通文件、命令参数、日志或最终回复。
