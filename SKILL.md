---
name: deepseek
description: "通过本机三字段页面配置并使用 Codex 原生可写 CustomAgent，同时把所选模型设为默认子智能体模型；继承现有 Provider、URL 与认证，在隔离 Git worktree 中修改代码。"
---

# deepseek

本 Skill 管理 Codex 原生 `CustomAgent`，并保证用户没有显式引用 Skill 或角色时，普通子智能体仍使用页面选择的模型。网页只收集模型、思考强度和是否支持识图；API URL、API Key、父 Provider 与认证始终沿用当前 Codex 配置。

## 一句话配置

当用户发送“阅读 https://github.com/mumuhaha487/codex-agent-models 配置skill”、要求阅读本仓库并配置，或表达同等明确意图时：

1. 安装时删除旧的 `codex-custom-subagent` 与 `deepseek`，只保留一个名为 `deepseek` 的 Skill。
2. 记录 `config.toml`、`auth.json`、`codex-models.json` 和现有 `CustomAgent.toml` 的哈希，不输出其中的 URL 或 Key。
3. 运行 [子代理设置](references/configuration.md) 中的单一 `apply` 入口并打开 localhost 页面。
4. 用户只填写模型 ID、思考强度和识图选项。点击保存即确认应用，不再要求额外聊天回复。
5. 页面保存后自动生成 `CustomAgent.toml`、把所选模型登记到现有 `codex-models.json`、设置默认子智能体模型与思考强度、运行完整验收并返回结果。

不要让用户复制配置、重复输入模型信息、手动执行后续命令或再次确认。

## 写入边界

- `$CODEX_HOME/agents/CustomAgent.toml`：允许完整生成或覆盖，用于显式 `CustomAgent`。
- `$CODEX_HOME/codex-models.json`：只允许新增或更新页面所选模型的单个条目；其他模型条目必须保持不变。
- `$CODEX_HOME/config.toml`：只允许新增或更新 `[agents].default_subagent_model` 与 `[agents].default_subagent_reasoning_effort`，两者必须与 `CustomAgent.toml` 一致。
- `$CODEX_HOME/auth.json`：禁止写入、删除或替换。
- `config.toml` 中的主模型、Provider、API URL、`model_catalog_json` 路径及其他字段禁止改变。
- 页面和仓库都不得提供默认模型值；模型必须由用户填写。

管理脚本使用 TOML 与 JSON 解析验证修改前后语义，只允许两个默认子智能体字段和所选模型条目发生变化。写入失败或验收失败时回滚本次 Agent 文件、默认设置和模型目录条目；检测到外部并发修改时停止，不覆盖外部内容。

## 验收

成功必须同时证明：

- 父 Provider 可以直接调用页面选择的模型。
- 显式 `spawn_agent(agent_type="CustomAgent", fork_context=false)` 使用页面模型、所选思考强度和 `workspace-write`。
- 未指定 `agent_type` 的子智能体实际使用页面模型和 `agents.default_subagent_reasoning_effort`。
- Provider URL 与 `auth.json` 哈希未变，`config.toml` 除允许字段外语义完全相同，模型目录除所选模型条目外完全相同。

配置完成后若返回 `restart_required` 或 `new_task_required`，完全退出 Codex 并打开新任务。主窗口显示的是主 Agent 模型，不能用它判断子线程模型；以子线程元数据验收为准。

## 可写子智能体工作流

主 Agent 只担任项目经理，负责拆分任务、定义交付合同、协调依赖、处理阻碍和最终集成，不直接编写、修改或逐文件审查实现。`CustomAgent` 在主 Agent 管理的隔离 worktree 中自主读取代码、选择做法、修改、运行命令、等待结果并完成测试。

1. 每次派发必须一次性写明最终交付物、代码与输入材料位置、允许修改范围、其他代理占用的文件或并行边界、必要检查与验收标准。只对 Git 仓库派发可写任务，不擅自处理用户已有修改。
2. 使用 `scripts/task_worktree.py start` 创建隔离 worktree、基线和任务记录。
3. 调用 `spawn_agent(agent_type="CustomAgent", fork_context=false)`，传入绝对 worktree 路径和完整任务合同；不要覆盖模型或思考强度。明确要求子智能体中途保持静默，仅在实际阻塞、必须由主 Agent 决定或会影响其他任务时汇报，不回复无信息量的“收到”或普通进度。
4. 主 Agent 不连续轮询。没有可并行的协调工作时，按 `1 -> 2 -> 4 -> 8 -> 16` 分钟等待，之后每次等待 16 分钟；只在子智能体完成、阻塞或到达等待点时读取结果。
5. 子智能体交付后，主 Agent 信任其测试证据，不重复打开已读文件、逐文件计算哈希或重跑相同命令。主 Agent 只检查交付摘要、测试结果、范围合规与集成状态；只有整合产生冲突、环境变化或出现新的失败时才运行针对性检查。
6. 同一任务的修订、排障和集成问题优先继续使用已读过相关代码的原子智能体。需要独立审查时必须新建子智能体，不能让实现者自审，也不能把独立审查混入原上下文。
7. 验收通过后运行 `integrate`，使用子智能体已有测试证据完成整合；无新增问题时直接 `finalize`。整合带来的新问题交回相关子智能体处理，必要时才 `rollback-integrated`。
8. `attempt_failures`、`parent_redirects` 或 `review_rejections` 任一达到 `5` 时，运行 `abort` 恢复任务基线，不发起第 6 次相同尝试。主 Agent 重新拆分、补充任务合同或改派子智能体，但仍不亲自编写实现；真正的新任务重新计数。

仓库读取和上传始终使用远程默认 `main`。普通工作不创建额外分支；隔离回退产生的临时任务分支完成后必须清理。

详细边界见 [兼容性](references/compatibility.md)，隔离流程见 [worktree 工作流](references/worktree-workflow.md)，故障处理见 [排障](references/troubleshooting.md)。
