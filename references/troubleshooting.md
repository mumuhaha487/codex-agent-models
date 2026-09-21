# 原生子 Agent 路由故障排查

以下行为以 2026-09-21 的 Codex 桌面环境为基准；后续版本仍以实际配置、实时子线程和数据库元数据为准。

## URL 与 Key

原生子 Agent 继承父任务的 Provider 路由。本 Skill 只登记页面所选模型、设置思考强度与识图元数据，并配置默认子智能体，不修改 URL、Key、父 Provider 或认证。父凭据必须同时允许访问父模型和子模型。

配置前后比较 `auth.json` 哈希；比较时不得输出内容。`config.toml` 去除两个受管 `agents` 字段后必须与修改前相同，`codex-models.json` 去除页面所选模型条目后也必须与修改前相同。

## 默认子智能体仍显示主模型

1. 运行 `status --json`，确认默认模型和默认思考强度都与 Agent 一致，且 `model_catalog_matches_agent = true`。
2. 运行 `test --json`。验收会创建一个不指定 `agent_type`、模型或思考强度的真实子线程，并检查线程数据库中的实际模型。
3. 完全退出 Codex 后重新打开并创建新任务。旧任务可能继续使用启动时加载的配置。
4. 主窗口输入框旁显示的是主 Agent 模型，不是子线程模型。不要仅凭主窗口的 `5.6 Sol` 标签判断子智能体失败。

## 显式 CustomAgent 无法创建

1. 确认只保留 `$CODEX_HOME/skills/deepseek`，旧的 `codex-custom-subagent` 已删除。
2. 检查 `$CODEX_HOME/agents/CustomAgent.toml` 能被 TOML 解析，模型、父 Provider、思考强度和沙箱字段完整。
3. 检查 `$CODEX_HOME/codex-models.json` 存在该模型，且思考强度和输入模态与页面选择一致。
4. 运行 `test --json`，检查显式角色的线程元数据和临时 Git 仓库写入结果。显式调用必须使用 `fork_context=false`。
5. 若工具 schema 不认识 `CustomAgent`，完全重启 Codex 并打开新任务。

## 识图没有生效

1. 运行 `status --json`，确认 `supports_vision = true`。
2. 确认上游模型和父 Provider 实际支持图片。本 Skill 不根据模型名猜测能力。
3. 委派时把图片作为 `image` 或 `local_image` 输入传给 `CustomAgent`。
4. 完全重启 Codex并创建新任务。

## 成功证据

1. `CustomAgent.toml` 的模型、Provider、思考强度、识图选项和 `workspace-write` 正确。
2. `codex-models.json` 已登记页面模型、所选思考强度和输入模态，其他模型条目不变。
3. `config.toml` 的默认子智能体模型与思考强度和 Agent 一致，其他配置语义不变。
4. `auth.json` 和 API Key 未变，Provider URL 未变。
5. 父 Provider 直连返回 `CUSTOM_AGENT_DIRECT_OK`。
6. 显式 `CustomAgent` 在临时 Git 仓库成功写入验收文件，线程元数据与配置一致。
7. 未指定角色的子线程返回 `DEFAULT_SUBAGENT_MODEL_OK`，数据库中的实际模型和思考强度为页面设置。

## 常见故障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 子 Agent 报模型无权限 | 父凭据不能访问目标模型 | 给现有父账号增加目标模型权限，不替换认证 |
| 子 Agent 报 `Unknown model` | 页面模型尚未登记到 `codex-models.json` | 重新运行单一 `apply` 入口；不要只手工修改 Agent 文件 |
| 默认子任务仍是父模型 | 缺少或未重新加载 `agents.default_subagent_model` | 运行单一 `apply` 入口并完全重启 Codex |
| 显式角色仍是旧模型 | 旧 Agent 文件或旧任务缓存 | 重新打开页面保存并在新任务测试 |
| 引用 skill 后要求 URL/Key | 调用了旧 `codex-custom-subagent` | 删除旧目录，只安装 `deepseek` |
| 思考强度仍是旧值 | 当前任务缓存旧 Agent 文件 | 完全重启并运行 `test --json` |
| 识图开启但收不到图片 | 委派未附带图片或上游不支持 | 附带图片并核实上游能力 |
| 子 Agent 只返回补丁 | Agent 文件仍是旧只读版本 | 重新运行页面配置，覆盖为可写版本 |
| 无法创建隔离任务 | 主工作区不干净或任务冲突 | 保留用户修改；先处理冲突，不自动 stash/reset |
| `protected_config_changed` | 操作期间主配置被其他进程改变 | 停止操作并检查外部变更；不要覆盖 |
| `protected_model_catalog_changed` | 操作期间模型目录被其他进程改变 | 停止操作并检查外部变更；不要覆盖 |
| `rollback_incomplete` | 失败后文件又被外部修改 | 保留外部变化，人工核对精确文件，不盲目恢复 |
