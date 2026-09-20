# 原生子 Agent 路由与凭据故障排查

本文记录安装成功但实际子任务仍使用父模型、专用 Key 直连成功但原生派发失败、或修改认证后主 Codex 无法访问时的判断方法。以下行为在 2026-09-20 的 Codex 桌面内置运行时 `0.155.0-alpha.9.2` 上完成了真实验证；后续运行时变化仍应以 `test --json` 和子线程数据库元数据为准。

## 两类凭据不是同一个概念

Codex Home 中可能同时存在多套认证来源：

- 主 Provider 使用 `requires_openai_auth = true` 时，凭据来自 Codex 登录存储。文件模式通常是 `$CODEX_HOME/auth.json`，也可能由宿主改用系统 Keyring。
- 本 Skill 的 `custom_agent` Provider 使用独立认证命令，从系统凭据库读取 `codex-custom-subagent-api-key`。
- 配置页保存的 API URL、API Key 和模型 ID 也位于系统凭据库，不写入仓库或普通配置文件。

因此 Codex 并非只能保存一个 Key。但“保存了独立 Key”不等于原生子 Agent 一定会使用该 Key，实际凭据取决于子线程最终记录的 Provider。

## `inherited_shared_gateway` 的真实含义

当前 v1 原生派发路径可能忽略 Agent 文件中的 `model_provider = "custom_agent"`，让子 Agent 继承父 Provider。只有父 Provider 与自定义 Provider 的规范化 Base URL 相同时，本 Skill 才接受该路由，并报告：

```json
{
  "route_mode": "inherited_shared_gateway",
  "credential_source": "parent_provider",
  "uses_dedicated_credential": false
}
```

此时：

- 直连测试使用专用系统凭据。
- 原生 `spawn_agent` 使用父 Provider 的凭据。
- 父 Provider 的凭据必须同时支持父模型和子 Agent 模型。
- 专用 Key 即使可以调用子模型，也不能证明原生派发可用。

如果父凭据不支持子模型，常见错误是网关返回 `404`，提示该模型不属于当前账号组。正确修复是让父账号组获得目标模型权限，或等待运行时能够可靠保留独立 Provider；不要把只支持子模型的 Key 覆盖到父 Provider。

## 禁止用子 Agent Key 替换父认证

不要为了绕过 Provider 继承而手工修改以下内容：

- 顶层 `model_provider`。
- 父 Provider 的 `requires_openai_auth`、`env_key` 或 `auth`。
- `$CODEX_HOME/auth.json`。

如果专用 Key 只支持子模型，用它替换父认证会导致父模型无法工作，表现为整个 Codex 无法继续访问。管理脚本本身不会修改顶层父 Provider 或 `auth.json`；排障时也必须保持这一边界。

## 注册角色不等于默认选择角色

安装 `[agents.CustomAgent]` 只让角色可被显式选择，不会把所有普通委派自动改成该角色。实际调用必须包含：

```text
spawn_agent(agent_type="CustomAgent", fork_turns="none", ...)
```

如果主 Agent 调用了通用 `worker`、省略 `agent_type`，或直接指定父模型，右侧子任务仍会运行父模型。需要默认路由时，应在全局或项目 `AGENTS.md` 中写明：

```markdown
# Default Custom Subagent Routing

- When the user explicitly requests a subagent or delegation, call `spawn_agent` with `agent_type = "CustomAgent"` and `fork_turns = "none"`.
- Do not select a model or reasoning effort directly; the `CustomAgent` role owns them.
- Do not fall back to `worker` or another standard agent unless the user explicitly authorizes it.
```

Codex 在新任务启动时读取 Agent 指令。更新 `AGENTS.md` 后必须打开新任务；旧任务不会动态采用新规则。

## `features.thread_tools` 配置警告

Codex 桌面内置运行时 `0.155.0-alpha.9.2` 的 `codex features list` 不再包含 `thread_tools`，因此以下提示表示旧功能标志已失效：

```text
session-flags: features.thread_tools is ignored
```

先检查 `$CODEX_HOME/config.toml`。如果 `[features]` 中仍有 `thread_tools`，运行本 Skill 的 `repair --json`，由管理程序在备份后移除；不要同时手工编辑受管配置。如果磁盘文件已经没有该字段，提示来自当前桌面进程缓存的启动参数，必须完全退出 Codex 并重新打开，再创建新任务。仅关闭当前任务或重复覆盖同一份 `config.toml` 不会清除旧进程状态。

`multi_agent_v2 = false` 是另一项仍被当前运行时识别的配置，不能因为名称相似而一并删除。它用于本 Skill 当前可验证的 v1 原生派发路径。

## 哪些证据可以判定成功

不能只依赖以下信息：

- 子 Agent 自述自己使用了什么模型。
- UI 面板中的名称或颜色。
- 直连请求返回成功。
- 配置文件中写有目标模型。

必须同时确认：

1. 直连口令为 `CUSTOM_AGENT_DIRECT_OK`。
2. 原生子 Agent 返回 `NATIVE_CUSTOM_AGENT_OK`。
3. `$CODEX_HOME/state_*.sqlite` 的 `threads` 行包含目标 `model`、`high`、`CustomAgent` 和符合路由规则的 Provider。

日常任务排障时也应检查子线程元数据。若记录为 `agent_role = "worker"` 或父模型，说明主 Agent 根本没有选择 `CustomAgent`，与 API Key 是否有效无关。

## 常见故障签名

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 直连通过，原生子线程返回模型不支持 | 原生路由继承父 Provider，父凭据没有目标模型权限 | 给父账号组增加目标模型权限；不要替换父认证 |
| 修改认证后父 Codex 无法使用 | 把只支持子模型的专用 Key 用到了父 Provider | 恢复 `config.toml` 和父登录配置，再重新运行 Skill setup |
| 右侧子任务仍显示父模型 | 主 Agent 调用了 `worker` 或显式父模型，没有指定 `CustomAgent` | 增加 `AGENTS.md` 路由规则，并在新任务中重试 |
| `502` / `503` 后重试通过 | 网关或上游模型短暂不可用 | 保留配置，间隔后重试 `test --json` |
| setup 返回成功但旧任务行为不变 | 旧任务缓存了启动时配置和指令 | 完全重启 Codex 并打开新任务 |
| 设置页提示 `features.thread_tools is ignored` | 旧配置或当前桌面进程仍携带已移除的功能标志 | 运行 `repair` 清除磁盘字段；若文件已无该字段，完全退出并重启 Codex |

## 安全恢复

1. 不要删除或覆盖用户项目文件；安装和排障只操作全局 Skill、Codex Home 或隔离临时目录。
2. 使用 `$CODEX_HOME/codex-custom-subagent/backups/` 中的备份恢复受管配置。
3. 不读取、回显或提交 `auth.json`、系统凭据或 API Key。
4. 如果 Key 曾进入聊天、日志或命令参数，立即在网关撤销并通过本机配置页替换。
5. 恢复主 Codex 后，先用父 Provider 验证父模型，再运行本 Skill 的完整 `setup` 或 `test`。
