# Repository Instructions

当用户要求“阅读这个项目仓库，帮我配置这个技能”或表达同等意图时：

1. 以远端默认分支 `main` 的内容为准。不得假设会自动切换到其他分支；除非用户明确要求、隔离回退确有必要或工具强制要求，不得自行创建分支。
2. 阅读根目录 `SKILL.md`、`references/configuration.md`、`references/compatibility.md`、`references/worktree-workflow.md` 和 `references/troubleshooting.md`。
3. 安装和排障不得修改用户当前业务项目。将仓库安装到当前 `CODEX_HOME/skills/deepseek`，复制时排除 `.git`、`node_modules`、`__pycache__` 和 `*.pyc`，不得覆盖其他 Skill。
4. 在安装目录运行 `npm --prefix <skill-dir>/scripts/credential-ui ci --ignore-scripts`。
5. 运行 `python3 <skill-dir>/scripts/codex_custom_agent.py status --json`。第一次收到创建或变更请求时只做只读检查，并说明当前值、目标值、唯一写入文件、持久影响及能力/费用风险。
6. 必须等待用户在下一条独立消息中只回复 `已确认`。目标或范围变化后重新确认。
7. 确认后运行 `node <skill-dir>/scripts/credential-ui/src/profile.ts setup default --confirmed`。页面只允许填写模型、思考强度和是否支持识图。
8. 保存后运行：

```text
node <skill-dir>/scripts/credential-ui/src/profile.ts run default -- <python3> <skill-dir>/scripts/codex_custom_agent.py setup --model-env --effort-env --vision-env --confirmed --json
```

9. 唯一允许写入、覆盖或删除的 Codex 配置文件是 `$CODEX_HOME/agents/CustomAgent.toml`。`config.toml` 只能读取父 Provider 和进行前后哈希校验；不得修改、恢复、格式化或清理。不得读写 `auth.json`、URL、API Key、模型目录、状态清单、角色注册或功能标志。
10. 检查模型、思考强度、识图标记、`workspace-write` 沙箱、父 Provider、单文件白名单、原生路由、数据库元数据和临时 Git 仓库写入结果。
11. 可写任务必须使用 `scripts/task_worktree.py start` 创建隔离 worktree。每轮 `checkpoint`；通过后 `integrate` 并复测，再 `finalize`；失败用 `rollback-integrated` 或 `abort`。
12. 任一任务级失败计数达到 `5` 时，不发起第 6 次子智能体调用，先丢弃隔离修改并清理，再由主 Agent 直接完成。只有真正的新任务才重置计数。

任何时候都不得把认证内容写入仓库、命令参数、日志或最终回复。
