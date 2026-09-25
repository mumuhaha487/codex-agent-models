---
name: deepseek
description: "配置 Codex 原生 CustomAgent，并由主模型派发子任务完成编码、测试与独立审查；沿用现有 Provider、URL 与认证，在隔离 Git worktree 中修改代码。"
---

# deepseek

本 Skill 管理 Codex 原生 `CustomAgent`，并保证用户没有显式引用 Skill 或角色时，普通子智能体仍使用页面选择的模型。网页只收集模型、思考强度和是否支持识图；API URL、API Key、父 Provider 与认证始终沿用当前 Codex 配置。

## 一句话配置

当用户发送“阅读 https://github.com/mumuhaha487/codex-agent-models 配置skill”、要求阅读本仓库并配置，或表达同等明确意图时：

1. 安装时删除旧的 `codex-custom-subagent` 与 `deepseek`，只保留一个名为 `deepseek` 的 Skill。
2. 记录 `config.toml`、`auth.json`、`codex-models.json` 和现有 `CustomAgent.toml` 的哈希，不输出其中的 URL 或 Key。
3. 运行 [子代理设置](references/configuration.md) 中的单一 `apply` 入口并打开 localhost 页面。
4. 用户只填写模型 ID、思考强度（`none`、`low`、`medium`、`high`）和识图选项。点击保存即确认应用，不再要求额外聊天回复。
5. 页面保存后自动生成 `CustomAgent.toml`、把所选模型登记到现有 `codex-models.json`、设置默认子智能体模型与思考强度、运行完整验收并返回结果。

不要让用户复制配置、重复输入模型信息、手动执行后续命令或再次确认。

## 写入边界

- `$CODEX_HOME/agents/CustomAgent.toml`：允许完整生成或覆盖，用于显式 `CustomAgent`。
- `$CODEX_HOME/codex-models.json`：只允许新增或更新页面所选模型的单个条目；其他模型条目必须保持不变。
- `$CODEX_HOME/config.toml`：只允许新增或更新 `[agents].default_subagent_model` 与 `[agents].default_subagent_reasoning_effort`，两者必须与 `CustomAgent.toml` 一致（支持 `none`、`low`、`medium`、`high`）。
- `$CODEX_HOME/auth.json`：禁止写入、删除或替换。
- `config.toml` 中的主模型、Provider、API URL、`model_catalog_json` 路径及其他字段禁止改变。
- 页面和仓库都不得提供默认模型值；模型必须由用户填写。

管理脚本使用 TOML 与 JSON 解析验证修改前后语义，只允许两个默认子智能体字段和所选模型条目发生变化。写入失败或验收失败时回滚本次 Agent 文件、默认设置和模型目录条目；检测到外部并发修改时停止，不覆盖外部内容。

## 验收

成功必须同时证明：

- 父 Provider 可以直接调用页面选择的模型。
- 显式 `spawn_agent(agent_type="CustomAgent", fork_context=false)` 使用页面模型、所选思考强度（包括 `none`）和 `workspace-write`。
- 未指定 `agent_type` 的子智能体实际使用页面模型和 `agents.default_subagent_reasoning_effort`。
- Provider URL 与 `auth.json` 哈希未变，`config.toml` 除允许字段外语义完全相同，模型目录除所选模型条目外完全相同。

配置完成后若返回 `restart_required` 或 `new_task_required`，完全退出 Codex 并打开新任务。主窗口显示的是主 Agent 模型，不能用它判断子线程模型；以子线程元数据验收为准。

## 主模型派发、编码与独立审查

先由主模型拆分并派发子任务，再由子智能体完成相应的编码、测试、审核与审查。主模型负责定义交付合同、协调依赖、处理阻碍、核对验收证据和最终集成，不代替实现者编写业务代码，也不代替独立审查者逐文件审查实现。

- 实现子任务：由 `CustomAgent` 在主模型管理的隔离 worktree 中自主读取代码、选择做法、修改并完成测试。
- 审查子任务：由另一个 `CustomAgent` 在独立上下文中只读检查实现结果，对照原始需求报告问题、影响、文件位置与验证缺口。实现者自己的测试不能代替独立审查。
- 主模型：把审查发现交回原实现子任务修订，协调复核，确认要求、测试和审查证据齐备后集成。

调用本 Skill 完成编码或审查任务，不等于要求重新配置子智能体。沿用现有 `CustomAgent` 设置，不修改模型、Provider、思考强度、认证或其他持久配置。

1. 主模型先定义交付合同，再派发任务。合同必须写明任务类型（实现或只读审查）、原始需求、最终交付物、代码与输入材料位置、精确文件白名单、并行边界、必要检查和验收标准。涉及界面时列出实际需要覆盖的桌面、移动端视口及关键操作。只对 Git 仓库派发可写任务，不擅自处理用户已有修改。
2. 对实现任务使用 `scripts/task_worktree.py start` 创建隔离 worktree、基线和任务记录。审查任务针对实现者交付的确定检查点，不在审查期间并行修改同一份实现。
3. 实现和审查均调用 `spawn_agent(agent_type="CustomAgent", fork_context=false)`，传入绝对 worktree 路径和完整任务合同；默认不传模型或思考强度覆盖参数（沿用已配置的模型与思考强度，包括已配置的 `none`）。普通子智能体请求路由到 `CustomAgent`，仅授权调用，不修改相邻配置。审查者不得修改文件，不得用 `default`、`worker` 或 `explorer` 替代指定角色。要求子智能体中途保持静默，仅在实际阻塞、必须由主模型决定或会影响其他任务时汇报，不回复无信息量的“收到”或普通进度。
4. 主模型不连续轮询。没有可并行的协调工作时，按 `1 -> 2 -> 4 -> 8 -> 16` 分钟等待，之后每次等待 16 分钟；只在子智能体完成、阻塞或到达等待点时读取结果。派发成功后才按返回的实际任务句柄等待，派发失败不能当作任务已启动。
5. 实现子任务交付修改摘要、文件清单、实际测试命令及结果、必要的界面验证证据（桌面端与移动端视口真实验收证据）和未覆盖项。主模型检查范围与证据是否满足合同，证据缺失时交回补齐，不把“已实现”或“应该通过”当作验收通过。对已经通过且环境未变化的测试，不重复执行同一命令。
6. 编码交付后，主模型必须另行派发独立审查子任务。给审查者原始需求、基线、交付检查点、允许读取范围和测试证据，不预设“通过”结论。审查者检查行为正确性、回归风险、接口完整性及测试覆盖；无问题时明确说明已检查范围与剩余验证限制。不能让实现者自审，也不能把独立审查混入实现者的原上下文。
7. 有审查问题时，主模型把具体发现交回原实现子智能体修订，再交原审查子智能体复核相关改动。同一任务的排障和集成问题也优先继续使用已读过相关代码的原子智能体。以修订后的检查点和补充证据验收，不沿用旧版本的通过结论。
8. 原始要求、测试和独立审查均满足交付合同时运行 `integrate`；无新增问题时运行 `finalize`。主模型检查集成状态，不重复逐文件审查实现。整合出现冲突、环境变化或新的失败时，把问题交回相关子智能体处理并补充针对性验证，必要时才 `rollback-integrated`。
9. `attempt_failures`、`parent_redirects` 或 `review_rejections` 任一达到 `5` 时，运行 `abort` 恢复任务基线，不发起第 6 次相同尝试。主模型重新拆分、补充任务合同或改派子智能体，不亲自接管业务实现；真正的新任务重新计数。

`CustomAgent` 无法启动时，主模型记录工具实际错误，核对角色可用性和当前配置，说明哪些任务尚未执行。不得擅自换角色、覆盖思考强度或修改持久配置来绕过失败；没有新证据时不重复相同调用。仅凭启动失败不能断言重启会解决问题，重启建议须说明依据与尚未证实的部分。

仓库读取和上传始终使用远程默认 `main`。普通工作不创建额外分支；隔离回退产生的临时任务分支完成后必须清理。

编码与独立审查的分工、验收顺序以本节为准。配置边界见 [兼容性](references/compatibility.md)，隔离命令与恢复操作见 [worktree 工作流](references/worktree-workflow.md)，故障处理见 [排障](references/troubleshooting.md)。
