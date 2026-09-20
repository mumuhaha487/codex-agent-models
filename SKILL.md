---
name: codex-custom-subagent
description: "配置、维护并使用一个由用户指定 Responses API URL、API Key 和模型 ID 的 Codex 原生只读子 Agent；适用于主 Agent 拆解任务、派发候选补丁、验收并迭代修订的工作流。普通 API 咨询或不需要子 Agent 的任务不触发。"
---

# Codex Custom Subagent

本 Skill 管理 `CustomAgent` 原生子 Agent，并规定主 Agent 的派发和验收流程。确定性的配置、模型目录、凭据与测试操作交给 `scripts/codex_custom_agent.py`；不要手动修改受管 TOML、JSON、Agent 文件或系统凭据。

## 配置入口

首次配置或更换服务时，先读 [API URL、Key 与模型配置](references/api-key-setup.md)。API URL、API Key 和精确模型 ID 都由用户在随附本机安全页面中填写，再由包装器注入业务进程；不要让用户把这些值贴进聊天，也不要把 Key 放进命令参数、日志或普通文件。

网关必须兼容 OpenAI Responses API，并支持 Codex 所需的工具调用。API URL 必须是用户或网关给出的精确 Base URL；不得猜测或自行追加 `/v1` 或其他路径，但必须保留用户明确提供的合法路径。HTTP 只允许 localhost、回环或私有网络 IP，公网端点必须使用 HTTPS。兼容性和 Provider 继承规则见 [references/compatibility.md](references/compatibility.md)。

## 配置流程

1. 运行 `status --json`，根据结构化状态继续。
2. 缺少 URL、Key 或模型时，启动随附本机页面让用户填写三项；URL 和模型使用明文输入框，Key 使用密码框，但三项都不回填已保存值。不要自动操作页面。
3. 经 `profile.ts run default` 包装入口运行 `setup --base-url-env --api-key-env --model-env --json`。
4. `setup`、`repair` 和 `test` 使用桌面内置 Codex 运行时。若返回 `new_task_required` 或 `restart_required`，提示用户重启 Codex 并打开新任务。
5. 验收必须同时通过直连口令 `CUSTOM_AGENT_DIRECT_OK`、原生口令 `NATIVE_CUSTOM_AGENT_OK`，以及子线程数据库中的实际 Provider、精确模型、`high` 思考程度和 `CustomAgent` 角色。
6. 最终只汇报状态、Provider、Base URL、模型、思考程度、角色和备份位置；不要输出 Key 或原始事件日志。

入口命令（macOS 使用 `python3`，Windows 使用可用的 Python 3 启动器）：

```text
python3 <skill-dir>/scripts/codex_custom_agent.py <command> --json
```

- `status`：只读检查配置、模型目录、凭据和桌面运行时。
- `setup`：首次写入配置并执行验收；安全页面流程必须传三个 `--*-env` 参数。
- `test`：执行直连和原生 `spawn_agent(agent_type="CustomAgent", fork_turns="none")` 验收。
- `repair`：按当前父模型重建目录并验收；省略三项输入时保留现有受管值。
- `disable`：停用角色，保留 Provider、模型目录和凭据。
- `uninstall`：移除本 Skill 管理的配置；只有用户明确要求删除运行时凭据时才传 `--remove-credential`。

## 主 Agent 工作流

主 Agent 负责计划、派发、验收和最终写入；`CustomAgent` 只读分析并返回候选补丁。

1. 先阅读项目约束，把需求拆成有依赖顺序的计划点；每一点写明范围、可验证验收标准和应运行的测试。
2. 按依赖逐点调用 `spawn_agent(agent_type="CustomAgent", fork_turns="none")`。一次只派发一个边界明确的计划点，要求返回完整 unified diff/patch、测试命令和假设。
3. 不直接信任候选结果。主 Agent 在隔离副本或临时 worktree 中应用补丁，检查范围并运行对应测试。
4. 验收失败时，把具体文件/位置、失败命令或证据、预期行为和修改方向发回同一个子 Agent，要求完整修订补丁。继续用新证据迭代，不能只回复“验收不通过”。
5. 只有候选补丁通过该计划点的全部验收标准后，主 Agent 才把它应用到真实工作区，并在真实工作区复测。然后再进入下一个计划点。
6. 只有客观阻塞（缺少必要能力或外部状态）或需要用户作出重大选择时才停止；普通实现或测试失败必须继续反馈、修订和验收。

如果当前工具 schema 不认识 `CustomAgent`，提示用户重启 Codex 并打开新任务；不要用管理脚本或 `codex exec` 代替日常编码子任务。

## 状态处理

- `ready`：静态配置、直连、原生路由、数据库元数据和口令均通过。
- `configured`：静态配置完整，尚未完成实时验收。
- `credential_missing`、`base_url_required`、`model_selection_required`：回到安全页面补齐三项，再继续原流程。
- `operation_in_progress`：等待当前配置操作结束，不并发写配置。
- `conflict`：报告冲突文件和字段，等待用户决定是否替换。
- `unsupported`：报告缺少的系统能力，不手工绕过。
- `failed`：读取结构化 `errors`；如果已回滚，不再手改受管文件。

默认使用当前 `CODEX_HOME`；只有用户明确指定其他 Codex Home 时才传 `--codex-home`。
