---
name: deepseek
description: "配置、维护并使用用户指定模型作为 Codex 原生可写 CustomAgent；只允许写入 agents/CustomAgent.toml，继承父 Provider 与认证，并在隔离 Git worktree 中直接修改代码。任何持久化子智能体配置变更都必须经过独立第二轮“已确认”。"
---

# deepseek

本 Skill 管理 Codex 原生 `CustomAgent`。网页只收集模型、思考强度和是否支持识图；API URL、API Key、父 Provider 与认证始终沿用当前 Codex 配置。

## 配置写入边界

- 唯一允许写入、覆盖或删除的 Codex 配置文件是 `$CODEX_HOME/agents/CustomAgent.toml`。
- `$CODEX_HOME/config.toml` 只能只读解析父 `model_provider`，并在操作前后校验 SHA-256；绝不修改、恢复、格式化或清理它。
- 不读写 `auth.json`，不收集 URL 或 API Key，不创建独立 Provider。
- 不创建或修改模型目录、状态清单、角色注册块、功能标志或配置备份文件。
- 设置页只把三个非认证选项保存在当前用户的系统凭据后端，用于一次性注入管理脚本。
- `supports_vision` 只控制 `CustomAgent.toml` 中的能力标记和行为指令；目标模型及父 Provider 本身必须真实支持图片。

任何代码路径若尝试写入白名单之外的位置必须立即失败。`config.toml` 哈希发生变化时，只回滚本次 `CustomAgent.toml` 写入，不得动主配置。

## 持久化配置二次确认

创建、修改、覆盖、修复、停用或删除 `CustomAgent.toml` 前必须二次确认：

1. 第一次收到请求时只运行 `status --json`，说明当前值、目标值、唯一写入文件、后续持久影响，以及模型能力、费用和行为变化风险。
2. 要求用户在下一条独立消息中只回复 `已确认`。首次请求里附带的确认无效。
3. 仅在收到精确回复后运行带 `--confirmed` 的命令。
4. 模型、思考强度、识图选项或文件范围变化后，原确认失效，必须重新确认。

只调用已配置的 `CustomAgent`、运行 `status` 或不写配置的 `test` 不需要确认。

## 配置流程

1. 运行 `python3 <skill-dir>/scripts/codex_custom_agent.py status --json`。
2. 有效确认后运行：

```text
node <skill-dir>/scripts/credential-ui/src/profile.ts setup default --confirmed
```

3. 用户在 localhost 页面只填写：
   - 子代理模型：必须填写精确模型 ID，仓库不提供默认值。
   - 思考强度：`low`、`medium` 或 `high`。
   - 支持识图：`yes` 或 `no`。
4. 保存后运行：

```text
node <skill-dir>/scripts/credential-ui/src/profile.ts run default -- python3 <skill-dir>/scripts/codex_custom_agent.py setup --model-env --effort-env --vision-env --confirmed --json
```

5. `setup` 写入 `CustomAgent.toml` 后执行父 Provider 直连与原生可写子智能体验收。若返回需要重启或新任务，完全退出 Codex 后重新打开。
6. 最终只汇报模型、思考强度、识图选项、`workspace-write` 沙箱、父 Provider、单文件白名单和验收结果；不要输出认证内容。

现有 `CustomAgent.toml` 与目标不同且不带本 Skill 标记时，必须在确认范围明确包含完整覆盖后额外传入 `--replace-agent`。

## 可写子智能体工作流

主 Agent 负责计划、隔离、验收、整合和回退；`CustomAgent` 只在主 Agent 管理的隔离 worktree 中直接修改代码。

1. 每个计划点写明任务 ID、允许写入路径、验收标准和测试。只对 Git 仓库派发可写任务，且不得擅自提交、stash、reset 或清理用户已有修改。
2. 派发前运行 `scripts/task_worktree.py start` 创建隔离 worktree、基线和任务记录。创建任务分支是该回退流程的必要步骤；其他写项目或上传操作不得自行建立额外分支。
3. 调用 `spawn_agent(agent_type="CustomAgent", fork_turns="none")`，传入绝对 worktree 路径、允许路径、验收标准和测试命令。不要显式覆盖模型或思考强度。
4. 子智能体直接修改并测试。主 Agent 检查写入范围并进行功能验收；认证、删除、迁移或安全边界变更仍需针对性代码审核。
5. 每轮运行 `checkpoint --attempt <n>`。验收失败时把具体证据发回同一个子智能体继续修改。
6. 通过后运行 `integrate`，在主工作区复测；成功运行 `finalize` 清理 worktree、任务分支、检查点和临时记录，失败运行 `rollback-integrated` 回退整合并清理。

### 同一任务失败预算

每个任务分别从 `0` 记录 `attempt_failures`、`parent_redirects` 和 `review_rejections`。任一计数达到 `5` 时，不发起第 6 次调用；运行 `abort` 丢弃隔离修改并清理，然后由主 Agent 自己实现和验证该任务。改写提示词或重复派发同一验收目标不能重置计数；只有开始范围和目标不同的新任务才重新计数，并再次优先使用 `CustomAgent`。

## Git 分支规则

- 阅读仓库时以远端默认分支为准，不能假设智能体会自动切换到其他分支。
- 修改和上传仓库时默认直接使用现有主分支，不得为了普通工作自行创建分支。
- 只有隔离回退流程、用户明确指定或工具强制要求时才创建临时分支，并在完成后清理。

详细配置见 [子代理设置](references/configuration.md)，安全边界见 [兼容性](references/compatibility.md)，隔离写入见 [worktree 工作流](references/worktree-workflow.md)，故障处理见 [排障](references/troubleshooting.md)。
