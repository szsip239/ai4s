import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 rules/api.test.mjs 同款：TS 源 transpile 后运行期依赖替换为桩，经 data URL 导入。
// 只测 normalizeRouting / buildSettingsWithRouting / ROUTER_DEFAULT_PROMPT 纯函数与常量，hooks 桩不触发。
const srcRoot = join(import.meta.dirname, '..', '..', '..');
const repoRoot = join(srcRoot, '..', '..');

const source = readFileSync(join(srcRoot, 'ai4s/pages/smart-routing/api.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText
  .replaceAll(
    "import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';",
    'const useMutation = () => ({}); const useQuery = () => ({}); const useQueryClient = () => ({});'
  )
  .replaceAll("import { useTranslation } from 'react-i18next';", 'const useTranslation = () => ({ t: (k) => k });')
  .replaceAll("import { toast } from 'sonner';", 'const toast = { success() {}, error() {} };')
  .replaceAll("import { apiRequest } from '@/lib/api-client';", 'const apiRequest = () => Promise.resolve({});');

const { normalizeRouting, buildSettingsWithRouting, ROUTER_DEFAULT_PROMPT } = await import(
  `data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`
);

test('normalizeRouting: routing 节缺席/空对象补全量默认（issue #120；缺省与 shim 运行侧逐点对齐）', () => {
  const expected = {
    enabled: false, // 缺席=关态合法（shim #117）
    threshold: 0.5, // #114 评测推荐默认工作点
    tiers: { simple: 'deepseek-v4-flash', complex: 'gpt-5.6-luna' },
    timeout: 4,
    max_concurrency: 2,
    prompt: ROUTER_DEFAULT_PROMPT, // #119 缺省=app.py ROUTER_PROMPT_SYSTEM 常量
    escalate_conf: 0.85,
    session_ttl: 3600,
    tool_loop_lock: true,
    thinking_lock: true,
  };
  assert.deepEqual(normalizeRouting(undefined), expected);
  assert.deepEqual(normalizeRouting({}), expected);
});

test('normalizeRouting: #117 旧五键节（无 #119 扩展键）补扩展默认、显式五键原样保留', () => {
  const n = normalizeRouting({
    enabled: true,
    threshold: 0.6,
    tiers: { simple: 'm-simple', complex: 'm-complex' },
    timeout: 8,
    max_concurrency: 4,
  });
  assert.equal(n.enabled, true);
  assert.equal(n.threshold, 0.6);
  assert.deepEqual(n.tiers, { simple: 'm-simple', complex: 'm-complex' });
  assert.equal(n.timeout, 8);
  assert.equal(n.max_concurrency, 4);
  // 扩展键补默认（缺席=运行侧内置默认，normalize 后面板十键齐全、整体 PUT 出席即严校）
  assert.equal(n.prompt, ROUTER_DEFAULT_PROMPT);
  assert.equal(n.escalate_conf, 0.85);
  assert.equal(n.session_ttl, 3600);
  assert.equal(n.tool_loop_lock, true);
  assert.equal(n.thinking_lock, true);
});

test('normalizeRouting: 扩展键显式值原样保留（读侧不得吞档——含锁 false 与自定义 prompt）', () => {
  const n = normalizeRouting({
    enabled: true,
    threshold: 0.5,
    tiers: { simple: 'a', complex: 'b' },
    timeout: 2,
    max_concurrency: 1,
    prompt: '自定义分类提示',
    escalate_conf: 0.9,
    session_ttl: 60,
    tool_loop_lock: false,
    thinking_lock: false,
  });
  assert.equal(n.prompt, '自定义分类提示');
  assert.equal(n.escalate_conf, 0.9);
  assert.equal(n.session_ttl, 60);
  assert.equal(n.tool_loop_lock, false);
  assert.equal(n.thinking_lock, false);
});

test('ROUTER_DEFAULT_PROMPT: 与 shim/app.py ROUTER_PROMPT_SYSTEM 逐字一致（#114 获胜版单一事实源）', () => {
  const appPy = readFileSync(join(repoRoot, 'shim/app.py'), 'utf8');
  const m = appPy.match(/ROUTER_PROMPT_SYSTEM = """([\s\S]*?)"""/);
  assert.ok(m, 'shim/app.py 须含 ROUTER_PROMPT_SYSTEM 常量');
  assert.equal(ROUTER_DEFAULT_PROMPT, m[1]);
});

test('buildSettingsWithRouting: 只改 routing 节，l1/l2/response 缺段补默认（整体 PUT 过服务端必填严校）', () => {
  const doc = {
    version: 1,
    judge: { enabled: false },
    edm: { enabled: false, min_hits: 1 },
    pg: { enabled: false, threshold: 0.7 },
    rules: { enabled: false, block: false },
    // 旧文件缺 l1/l2/response 段
  };
  const routing = normalizeRouting(undefined);
  const out = buildSettingsWithRouting(doc, { ...routing, enabled: true });
  assert.deepEqual(out.l1, { enabled: true }); // 缺段补默认（shim 缺段默认 true，SettingsPanel 同处置）
  assert.deepEqual(out.l2, { enabled: true });
  assert.deepEqual(out.response, { enabled: true });
  assert.equal(out.routing.enabled, true);
  // 其他段原样透传（引用不变=不被改写）
  assert.equal(out.judge, doc.judge);
  assert.equal(out.pg, doc.pg);
});

test('buildSettingsWithRouting: 已有 l1/l2/response/routing 不被默认覆盖', () => {
  const doc = {
    version: 1,
    judge: { enabled: true },
    edm: { enabled: false, min_hits: 1 },
    pg: { enabled: false, threshold: 0.7 },
    rules: { enabled: false, block: false },
    l1: { enabled: false },
    l2: { enabled: false },
    response: { enabled: false },
    routing: normalizeRouting({ enabled: true, threshold: 0.7, tiers: { simple: 'a', complex: 'b' }, timeout: 2, max_concurrency: 1 }),
  };
  const next = normalizeRouting({ enabled: false, threshold: 0.4, tiers: { simple: 'c', complex: 'd' }, timeout: 3, max_concurrency: 2 });
  const out = buildSettingsWithRouting(doc, next);
  assert.equal(out.l1.enabled, false);
  assert.equal(out.l2.enabled, false);
  assert.equal(out.response.enabled, false);
  assert.equal(out.routing, next); // 新 routing 节整体替换
});

test('路由接线：routes/_authenticated/smart-routing/index.tsx 挂 RouteGuard（read_channels/system，issue #120）', () => {
  const route = readFileSync(join(srcRoot, 'routes/_authenticated/smart-routing/index.tsx'), 'utf8');
  assert.match(route, /requiredScopes=\{?\['read_channels'\]/, 'RouteGuard 须要求 read_channels');
  assert.match(route, /scopeLevel=["']system["']/, 'scopeLevel 须为 system（Admin 组）');
  assert.match(route, /createFileRoute\('\/_authenticated\/smart-routing\/'\)/, '路由路径须为 /smart-routing');
});
