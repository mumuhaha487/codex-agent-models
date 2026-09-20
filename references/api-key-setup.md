# API URL、Key 与模型配置

首次填写或更换 API URL、API Key、模型 ID 时，使用随附统一页面。三项都通过系统凭据后端保存并由包装器按用途注入；不要在聊天、命令参数或普通文件中收集这些值。

## 本机页面

将当前 `SKILL.md` 所在绝对目录记为 `SKILL_DIR`。页面需要 Node.js 22.18+：

```bash
npm --prefix "$SKILL_DIR/scripts/credential-ui" ci --ignore-scripts
node "$SKILL_DIR/scripts/credential-ui/src/profile.ts" status default
node "$SKILL_DIR/scripts/credential-ui/src/profile.ts" setup default
```

`status` 退出码 0 表示三项均可读取，2 表示至少一项缺失，1 表示配置或系统后端失败。缺失或用户要求更换时才启动 `setup`，把返回的 localhost 链接展示给用户，由用户亲自填写保存。不要自动操作真实配置页面。

页面中 API URL 与模型 ID 使用明文输入框，API Key 使用密码框。输入类型不改变存储边界：三项都不回填原值；已有项留空会保留，替换需要用户确认。只有 `saved` 表示全部成功；`partial`、超时或中断后先重新运行 `status`，再补缺失项。

API URL 必须按用户或网关文档给出的精确 Base URL 填写。不要自行追加 `/v1` 或其他路径；如果给出的 Base URL 本身包含 `/v1` 等合法路径，则保留该路径。管理程序只会统一末尾斜杠，不会删除或补写 API 路径。

| 配置项 | 业务环境变量 | 系统凭据引用 |
| --- | --- | --- |
| API URL | `CUSTOM_AGENT_BASE_URL` | `codex-custom-subagent/setup/api-url` |
| API Key | `CUSTOM_AGENT_API_KEY` | `codex-custom-subagent/setup/api-key` |
| 模型 ID | `CUSTOM_AGENT_MODEL` | `codex-custom-subagent/setup/model` |

系统凭据引用沿用旧版 `codex-custom-subagent` 标识，以便升级为 `$deepseek` 后继续使用现有秘密；这是内部兼容标识，不是 Skill 的公开名称。

切换服务时应在同一页面核对三项。保存成功只证明值可安全读取；实际端点和模型能力以业务测试为准。

## 运行业务

```bash
node "$SKILL_DIR/scripts/credential-ui/src/profile.ts" run default -- python3 "$SKILL_DIR/scripts/codex_custom_agent.py" setup --base-url-env --api-key-env --model-env --json
```

包装器只把三项注入该业务子进程。管理程序只会将验证后的 URL 和模型 ID 写入受管 Codex 配置；显式传入 `--api-key-env` 或 `--api-key-stdin` 时会更新专用运行时 Key，即使旧 Key 已存在。未显式提供 Key 时复用旧值。后续安装或测试失败会恢复旧 Key；首次创建则删除新 Key。任何路径都不会回显 Key。

系统后端为 macOS Keychain、Windows Credential Manager 或组件支持的 Linux Secret Service。缺少后端时停止，不自动安装、解锁或降级为明文。正常流程不把秘密传入对话，但系统凭据不构成对同一用户任意代码执行的隔离。

## 组件验证

```bash
npm --prefix "$SKILL_DIR/scripts/credential-ui" run check
npm --prefix "$SKILL_DIR/scripts/credential-ui" test
npm --prefix "$SKILL_DIR/scripts/credential-ui" run build
```

测试使用假后端，不接触用户真实配置。卸载原生配置不会自动删除页面配置源；只有用户明确要求时才删除运行时 Key。
