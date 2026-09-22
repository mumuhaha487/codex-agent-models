# CustomAgent 模型配置技能

把当前父 Provider 已能访问的模型配置成 Codex 原生可写 `CustomAgent`，并把同一模型设置为普通子智能体的默认模型。设置页只有三个字段：

- 子代理模型：必须填写精确模型 ID，仓库不提供默认值
- 思考强度：`low`、`medium`、`high`
- 支持识图：`yes`、`no`

本 Skill 不收集或修改 API URL、API Key、父 Provider 与认证。它生成 `$CODEX_HOME/agents/CustomAgent.toml`，只更新 `$CODEX_HOME/codex-models.json` 中页面所选模型的条目，并只修改 `$CODEX_HOME/config.toml` 的默认子智能体模型与思考强度。

用户可以把本仓库交给 Codex，然后只说：

> 阅读 https://github.com/mumuhaha487/codex-agent-models 配置skill

Codex 会使用远程默认 `main`，删除旧的同类 Skill，只安装一个名为 `deepseek` 的 Skill，打开本机三字段页面，并在用户保存后自动应用和验收。无需再次回复“已确认”、复制命令或粘贴配置。

验收同时覆盖显式 `CustomAgent` 和未指定角色的默认子智能体。子智能体在隔离 Git worktree 中自主修改、运行命令和测试；主 Agent 只负责完整任务合同、协调、阻碍处理和最终集成，并直接采用子智能体的测试证据，不重复检查。相同任务复用原子智能体，独立审查使用新子智能体；等待按 `1 -> 2 -> 4 -> 8 -> 16` 分钟退避。任一失败计数达到 5 时恢复基线并重新拆分或改派，不由主 Agent 接手实现。

仓库的真实功能只放在默认 `main` 分支。普通修改和上传不创建额外分支。
