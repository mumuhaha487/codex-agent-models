import { readFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { loadManifest, nativeBackend, object, PublicError } from './config.ts';
import { validateFields } from './page.ts';
import { parseBindings, prepareCommand } from './run.ts';
import { startServer } from './server.ts';

const root = fileURLToPath(new URL('../', import.meta.url));
type Binding = { manifest: string; env: string };
type Completion = { status?: string };
export async function loadProfile(name: string, directory = root): Promise<Binding[]> {
  let value: unknown;
  try { value = JSON.parse(await readFile(path.join(directory, 'manifests/profiles.json'), 'utf8')); }
  catch { throw new PublicError('未找到业务凭据配置。'); }
  if (!/^[a-z0-9-]+$/.test(name) || !object(value) || value.version !== 1 || !object(value.profiles)
    || !Object.hasOwn(value.profiles, name)) throw new PublicError('请选择已声明的业务配置。');
  const items = value.profiles[name];
  if (!Array.isArray(items) || !items.length || items.length > 16) throw new PublicError('业务凭据配置不合法。');
  const bindings: Binding[] = items.map(item => {
    if (!object(item) || Object.keys(item).some(k => !['manifest', 'env'].includes(k))
      || typeof item.manifest !== 'string' || !/^[a-z0-9-]+\.json$/.test(item.manifest)
      || typeof item.env !== 'string') throw new PublicError('业务凭据绑定不合法。');
    return { manifest: path.join(directory, 'manifests', item.manifest), env: item.env };
  });
  parseBindings([...bindingArgs(bindings), '--', 'check']);
  validateFields(await Promise.all(bindings.map(b => loadManifest(b.manifest))));
  return bindings;
}
function bindingArgs(bindings: Binding[]) {
  return bindings.flatMap(b => ['--manifest', b.manifest, '--env', b.env]);
}
export async function profileStatus(bindings: Binding[], environment = process.env,
  read = async (ref: string) => (await nativeBackend(ref)).get()) {
  const fields = [];
  for (const b of bindings) {
    const m = await loadManifest(b.manifest);
    const fromEnv = Boolean(environment[b.env]?.trim());
    const configured = fromEnv || Boolean(await read(m.credential));
    fields.push({ credential: m.credential, configured, source: fromEnv ? 'environment' : 'system-store' });
  }
  return { configured: fields.every(f => f.configured), fields };
}
export async function prepareProfile(bindings: Binding[], command: string[], environment = process.env,
  read = async (ref: string) => (await nativeBackend(ref)).get()) {
  if (!command.length) throw new PublicError('请指定真实业务程序。');
  // 环境注入优先；本次只读取所选业务需要的凭据。
  const missing = bindings.filter(b => !environment[b.env]?.trim());
  if (!missing.length) return { command: command[0], args: command.slice(1), env: { ...environment } };
  return prepareCommand([...bindingArgs(missing), '--', ...command], read, environment);
}
async function runPreparedProfile(bindings: Binding[], command: string[]) {
  const plan = await prepareProfile(bindings, command);
  const child = spawn(plan.command, plan.args, { env: plan.env, stdio: 'inherit', shell: false });
  for (const b of bindings) delete plan.env[b.env];
  const code = await new Promise<number>((resolve, reject) => {
    child.once('error', () => reject(new PublicError('业务程序启动失败。')));
    child.once('exit', value => resolve(value ?? 1));
  });
  if (code !== 0) throw new PublicError('子智能体配置或验收失败。');
}
export async function openProfileAndApply(
  bindings: Binding[],
  command: string[],
  start = startServer,
  run = runPreparedProfile,
) {
  const manifests = await Promise.all(bindings.map(b => loadManifest(b.manifest)));
  let complete!: (value: Completion) => void;
  const completion = new Promise<Completion>(resolve => { complete = resolve; });
  const app = await start({ manifests, onComplete: result => {
    process.stdout.write(JSON.stringify(result) + '\n');
    const value = result as Completion;
    if (value.status !== 'partial') complete(value);
  } });
  process.stdout.write(`本机配置页面（由用户亲自填写，30 分钟内有效）：\n${app.url}\n`);
  const close = () => app.close();
  process.once('SIGINT', close); process.once('SIGTERM', close);
  const outcome = await completion.finally(() => {
    process.removeListener('SIGINT', close); process.removeListener('SIGTERM', close); app.close();
  });
  if (outcome.status !== 'saved') throw new PublicError('本机配置页面未保存，未修改 Codex 配置。');
  await run(bindings, command);
}
async function main() {
  const [action, name, ...args] = process.argv.slice(2);
  if (!['status', 'setup', 'run', 'apply'].includes(action) || !name
    || (action === 'status' && args.length)
    || (action === 'run' && (args[0] !== '--' || args.length < 2))
    || (action === 'apply' && (args[0] !== '--confirmed' || args[1] !== '--' || args.length < 4)))
    throw new PublicError('用法：node src/profile.ts status 配置名；node src/profile.ts setup 配置名 --confirmed；node src/profile.ts run 配置名 -- 程序 参数；node src/profile.ts apply 配置名 --confirmed -- 程序 参数');
  if (action === 'setup' && (args.length !== 1 || args[0] !== '--confirmed'))
    throw new PublicError('拒绝打开持久化子智能体设置页：只有用户明确要求配置时才能传入 --confirmed。');
  const bindings = await loadProfile(name);
  if (action === 'status') {
    const status = await profileStatus(bindings);
    process.stdout.write(JSON.stringify(status) + '\n');
    if (!status.configured) process.exitCode = 2;
  } else if (action === 'setup') {
    const manifests = await Promise.all(bindings.map(b => loadManifest(b.manifest)));
    const app = await startServer({ manifests, onComplete: result => process.stdout.write(JSON.stringify(result) + '\n') });
    process.stdout.write(`本机配置页面（由用户亲自填写，30 分钟内有效）：\n${app.url}\n`);
    process.once('SIGINT', app.close); process.once('SIGTERM', app.close);
  } else if (action === 'run') await runPreparedProfile(bindings, args.slice(1));
  else await openProfileAndApply(bindings, args.slice(2));
}
if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url)
  main().catch(error => {
    process.stderr.write((error instanceof PublicError ? error.message : '配置未完成，请检查依赖与系统凭据服务。') + '\n');
    process.exitCode = 1;
  });
