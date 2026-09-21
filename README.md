# CustomAgent 模型配置技能

把父 Provider 已经可以访问的模型配置成 Codex 原生可写 `CustomAgent`。设置页只有三个字段：

- 子代理模型：首次配置必须填写，仓库不提供默认值
- 思考强度：`low`、`medium`、`high`
- 支持识图：`yes`、`no`

本 Skill 不收集或修改 API URL、API Key、父 Provider 与认证。唯一允许修改的 Codex 配置文件是 `$CODEX_HOME/agents/CustomAgent.toml`；`config.toml` 只读并进行前后哈希保护。

用户可以把本仓库交给 Codex，然后说：

> 阅读这个项目仓库，帮我配置这个技能

持久化配置受独立第二轮 `已确认` 保护。确认后，Codex 安装全局 Skill、打开只含三个字段的本机页面、生成 `CustomAgent.toml`，并执行原生可写子智能体验收。

子智能体只在主 Agent 管理的隔离 Git worktree 中直接修改代码。主 Agent 负责验收、整合、回退和清理；任一失败计数达到 5 时由主 Agent 接管，不调用第 6 次。

仓库的真实功能必须存在于默认 `main` 分支。普通修改和上传不得自行创建额外分支；隔离回退所需的临时任务分支在完成后立即清理。
