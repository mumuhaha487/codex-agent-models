# Repository Instructions

当用户要求“阅读这个项目仓库，帮我配置这个技能”、发送“阅读 https://github.com/mumuhaha487/codex-agent-models 配置skill”或表达同等意图时，完整执行以下流程，不要求用户再发送第二条确认消息：

1. 以远端默认分支 `main` 为准。普通修改、安装或上传不得创建新分支。
2. 阅读根目录 `SKILL.md` 及 `references` 下的配置、兼容性、worktree 和排障说明。
3. 配置前记录 `$CODEX_HOME/config.toml`、`$CODEX_HOME/auth.json`、`$CODEX_HOME/codex-models.json` 和 `$CODEX_HOME/agents/CustomAgent.toml`（若存在）的 SHA-256。不得输出认证内容或 URL。
4. 删除旧的 `$CODEX_HOME/skills/codex-custom-subagent` 和 `$CODEX_HOME/skills/deepseek` 后，只把本仓库安装为 `$CODEX_HOME/skills/deepseek`。复制时排除 `.git`、`node_modules`、`__pycache__` 和 `*.pyc`。
5. 在安装目录运行 `npm --prefix <skill-dir>/scripts/credential-ui ci --ignore-scripts`，再运行管理脚本的 `status --json`。
6. 运行下面的单一入口，并把输出的 localhost 页面打开给用户。用户只填写模型、思考强度和是否支持识图；保存页面即确认应用，不再要求聊天中回复“已确认”。

```text
node <skill-dir>/scripts/credential-ui/src/profile.ts apply default --confirmed -- <python3> <skill-dir>/scripts/codex_custom_agent.py setup --model-env --effort-env --vision-env --confirmed --replace-agent --json
```

7. 等待命令完成。它必须自动写入、测试并返回状态，不能让用户再复制命令、粘贴配置或重复提供模型信息。
8. 写入清单只有 `$CODEX_HOME/agents/CustomAgent.toml`、`$CODEX_HOME/codex-models.json` 的页面所选模型条目，以及 `$CODEX_HOME/config.toml` 的 `[agents].default_subagent_model` 和 `[agents].default_subagent_reasoning_effort`。
9. `config.toml` 的主模型、Provider、API URL、`model_catalog_json` 路径及所有其他字段必须保持不变；`codex-models.json` 的其他模型条目必须保持不变；不得写入或删除 `auth.json`，API Key 必须保持不变。完成后复核配置语义和认证文件哈希。
10. 验收必须覆盖父 Provider 直连、显式 `spawn_agent(agent_type="CustomAgent", fork_context=false)` 可写子线程，以及未指定 `agent_type` 时实际使用页面选择的默认模型和思考强度。
11. 若结果要求重启或新任务，提示用户完全退出 Codex 后重新打开。
12. 可写任务使用 `scripts/task_worktree.py` 管理隔离、检查点、整合、回退和清理。主 Agent 只负责完整任务合同、协调、阻碍处理与集成，具体实现和测试由子智能体完成；同一任务优先复用原子智能体，独立审查使用新子智能体。禁止连续轮询和重复执行子智能体已成功运行的检查，等待按 `1 -> 2 -> 4 -> 8 -> 16` 分钟退避后维持 16 分钟。任一任务级失败计数达到 `5` 时，恢复基线并重新拆分或改派，不发起第 6 次相同尝试，也不由主 Agent 接手实现。

任何时候都不得把认证内容、完整 URL 或用户密钥写入仓库、命令参数、日志或最终回复。
