---
name: deepseek
description: "配置、维护并使用用户指定模型作为 Codex 原生只读 CustomAgent；模型复用当前父 Provider 与认证，可设置 low/medium/high 思考强度和是否支持识图。适用于主 Agent 派发候选补丁并闭环验收的工作流。普通 API 咨询或不需要子 Agent 的任务不触发。"
---

# deepseek

本 Skill 管理 Codex 原生 `CustomAgent`。它只设置子代理模型、思考强度和输入模态；始终继承当前 `config.toml` 顶层 `model_provider` 及其认证。

## 安全边界

- 不收集、不保存、不修改 API URL 或 API Key。
- 不修改顶层 `model_provider`、父 Provider 表、父 Provider 认证或 `auth.json`。
- 不注册独立子代理 Provider。升级时只移除旧版 Skill 自己用标记包围的 Provider 块。
- 模型必须能通过父 Provider 的现有凭据访问；否则原生子代理不能工作。
- 安装和排障只操作全局 Skill、`CODEX_HOME` 或隔离临时目录，不在用户业务项目中创建调试文件。

原因和恢复方法见 [原生路由故障排查](references/troubleshooting.md)。配置字段说明见 [子代理设置](references/configuration.md)，模型目录规则见 [兼容性与安全边界](references/compatibility.md)。

## 配置流程

1. 运行 `status --json`。
2. 首次安装、字段缺失或用户要求更换时，运行 `node <skill-dir>/scripts/credential-ui/src/profile.ts setup default`，展示 localhost 页面并让用户亲自填写：
   - 子代理模型：精确模型 ID。
   - 思考强度：`low`、`medium` 或 `high`。
   - 支持识图：`yes` 或 `no`。
3. 页面保存后，经包装器运行：

```text
node <skill-dir>/scripts/credential-ui/src/profile.ts run default -- python3 <skill-dir>/scripts/codex_custom_agent.py setup --model-env --effort-env --vision-env --json
```

4. `setup`、`repair` 和 `test` 使用桌面应用内置 Codex。若返回 `new_task_required` 或 `restart_required`，完全重启 Codex 并打开新任务。
5. 验收必须同时确认父 Provider 直连口令 `CUSTOM_AGENT_DIRECT_OK`、原生口令 `NATIVE_CUSTOM_AGENT_OK`，以及子线程数据库中的父 Provider、精确模型、所选思考强度和 `CustomAgent` 角色。
6. 最终只汇报模型、思考强度、识图能力、父 Provider、角色和备份位置。不要读取或输出认证内容。

入口命令：

```text
python3 <skill-dir>/scripts/codex_custom_agent.py <command> --json
```

- `status`：只读检查角色、模型目录、父 Provider 和桌面运行时。
- `setup`：写入所选模型、思考强度和识图能力，并执行验收。
- `test`：执行父 Provider 直连与原生 `spawn_agent` 验收。
- `repair`：按已保存设置和当前父模型/Provider 重建配置。
- `disable`：停用角色，保留模型目录和父认证。
- `uninstall`：移除本 Skill 管理的角色和模型目录；不删除任何认证。

## 识图行为

- `supports_vision = true` 时，模型目录写入 `input_modalities = ["text", "image"]`。有图片的委派应把图片作为 `image` 或 `local_image` 输入直接交给 `CustomAgent`；不要先转交主 Agent 做视觉解读。子 Agent 的开发指令也会要求它直接检查收到的图片。
- `supports_vision = false` 时，模型目录只声明 `input_modalities = ["text"]`。主 Agent 负责查看图片，并把与实现相关的视觉观察作为文本交给子 Agent；子 Agent 不得声称看过图片。
- 此开关声明用户已确认的模型能力，不会根据模型名称猜测，也不能让本来不支持图片的端点获得视觉能力。

## 主 Agent 工作流

主 Agent 负责计划、派发、验收和最终写入；`CustomAgent` 只读分析并返回候选补丁。

1. 阅读项目约束，把需求拆成有依赖顺序的计划点，并为每一点写明范围、验收标准和测试。
2. 调用 `spawn_agent(agent_type="CustomAgent", fork_turns="none")`。一次只派发一个边界明确的计划点，要求完整 unified diff、测试命令和假设。
3. 不显式指定模型或 reasoning effort；`CustomAgent` 角色配置拥有这些字段。
4. 不静默回退到 `worker`、`default` 或父模型。失败时报告原始结构化错误，只有用户明确授权后才使用其他 Agent。
5. 主 Agent 在隔离副本或临时 worktree 中应用候选补丁并测试。失败时把具体文件位置、命令证据、预期行为和修改方向发回同一个子 Agent，要求完整替换补丁。
6. 候选补丁通过该计划点全部验收后，才应用到真实工作区并复测。

注册角色不等于默认选择角色。用户要求默认使用 `CustomAgent` 时，按 [故障排查文档](references/troubleshooting.md#注册角色不等于默认选择角色) 配置 `AGENTS.md`，然后在新任务中验证数据库元数据。

## 状态处理

- `ready`：静态配置、父 Provider 直连、原生路由和数据库元数据均通过。
- `configured`：静态配置完整，尚未完成实时验收。
- `partial`：检查 `checks`；常见原因是旧功能标志、模型目录或 Agent 文件不一致。
- `configuration_missing`、`model_selection_required`：回到本机设置页补齐三项。
- `operation_in_progress`：等待当前操作结束，不并发写配置。
- `conflict`：报告冲突文件和字段，等待用户决定。
- `native_child_failed`：检查父 Provider 是否允许访问目标子模型。

默认使用当前 `CODEX_HOME`；只有用户明确指定其他 Codex Home 时才传 `--codex-home`。
