import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 src/gql/graphql.test.mjs 同款：TS 源 transpile 后运行期依赖替换为桩，经 data URL 导入。
// 只测 normalizePg / normalizeInjectRules 纯函数（settings pg/rules 段读侧缺键补默认），hooks/apiRequest 桩不触发。
const source = readFileSync(join(import.meta.dirname, 'api.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText
  .replaceAll(
    "import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';",
    // useQuery 捕获配置（queryFn/enabled/queryKey 可直接断言与调用）；useMutation/useQueryClient 桩不触发
    'const useMutation = () => ({}); const useQuery = (cfg) => cfg; const useQueryClient = () => ({});'
  )
  .replaceAll("import { toast } from 'sonner';", 'const toast = { error() {} };')
  .replaceAll(
    "import { apiRequest } from '@/lib/api-client';",
    // 捕获式请求桩：记录 endpoint/options；__apiError 出席时按其拒绝（测错误归一）
    'const apiRequest = (endpoint, options) => { (globalThis.__apiCalls ??= []).push({ endpoint, options }); return globalThis.__apiError ? Promise.reject(globalThis.__apiError) : Promise.resolve(globalThis.__apiResponse); };'
  )
  .replaceAll("import { getTokenFromStorage } from '@/stores/authStore';", 'const getTokenFromStorage = () => "";');

const { normalizePg, normalizeInjectRules, normalizeJudge, useBypassKeyMatch, matchedKeyIdSet } = await import(
  `data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`
);

test('normalizePg: 旧 settings.json 缺键补默认（issue #44 normalize / issue #103 阻断两键）', () => {
  assert.deepEqual(normalizePg({ enabled: true, threshold: 0.8 }), {
    enabled: true,
    threshold: 0.8,
    normalize: false,
    block_enabled: false, // issue #103 缺省关=维持 shadow 现状
    block_threshold: 0.9, // issue #103 缺省 0.9（高分档试点水位，与 shim setting_value 缺省对齐）
  });
  assert.deepEqual(normalizePg(undefined), {
    enabled: false,
    threshold: 0.7,
    normalize: false,
    block_enabled: false,
    block_threshold: 0.9,
  });
});

test('normalizePg: normalize 显式两档原样保留（issue #97 PgPanel 开关读写语义）', () => {
  // 面板整体 PUT 时 normalize 必填（shim _SETTINGS_PG_KEYS）——读侧归一不得吞掉用户已保存的档位
  assert.equal(normalizePg({ enabled: true, threshold: 0.7, normalize: true }).normalize, true);
  assert.equal(normalizePg({ enabled: true, threshold: 0.7, normalize: false }).normalize, false);
});

test('normalizePg: 阻断两键显式值原样保留（issue #103 面板整体 PUT 必填，读侧不得吞档）', () => {
  const on = normalizePg({ enabled: true, threshold: 0.7, block_enabled: true, block_threshold: 0.95 });
  assert.equal(on.block_enabled, true);
  assert.equal(on.block_threshold, 0.95);
  const off = normalizePg({ enabled: true, threshold: 0.7, block_enabled: false, block_threshold: 0.8 });
  assert.equal(off.block_enabled, false);
  assert.equal(off.block_threshold, 0.8);
});

test('normalizeInjectRules: 旧 settings.json 缺 rules 段/缺键补默认（issue #104 双键默认关）', () => {
  // 缺省与 shim setting_value 缺省对齐：enabled=false（新层先进场 shadow 观察）、block=false
  assert.deepEqual(normalizeInjectRules(undefined), { enabled: false, block: false });
  assert.deepEqual(normalizeInjectRules({}), { enabled: false, block: false });
  assert.deepEqual(normalizeInjectRules({ enabled: true }), { enabled: true, block: false });
});

test('normalizeInjectRules: 显式两档原样保留（面板整体 PUT 必填，读侧不得吞档）', () => {
  const on = normalizeInjectRules({ enabled: true, block: true });
  assert.equal(on.enabled, true);
  assert.equal(on.block, true);
  const off = normalizeInjectRules({ enabled: false, block: false });
  assert.equal(off.enabled, false);
  assert.equal(off.block, false);
});

test('normalizeJudge: 旧 settings.json 缺注入三键补默认（issue #105 judge 注入第二职责）', () => {
  // 缺省与 shim setting_value 缺省对齐：inject_enabled=false（新职责先进场 shadow 观察）；
  // prompt 补空串占位（单一源=settings.json，web 不内置 prompt 文本——开态保存由 validateJudge 拦）
  const n = normalizeJudge({
    enabled: true, model: 'm0', base_url: 'http://judge/v1', timeout: 8,
    prompt_system: 'ps', prompt_fewshot: 'pf', threshold: 0.8, action: 'shadow',
    sample_rate: 1.0, max_concurrency: 2,
  });
  assert.equal(n.inject_enabled, false);
  assert.equal(n.inject_prompt_system, '');
  assert.equal(n.inject_prompt_fewshot, '');
  assert.deepEqual(normalizeJudge(undefined).inject_enabled, false);
});

test('normalizeJudge: 注入三键显式值原样保留（面板整体 PUT 必填，读侧不得吞档/吞 prompt）', () => {
  const on = normalizeJudge({
    inject_enabled: true, inject_prompt_system: '注入系统提示', inject_prompt_fewshot: '注入示例',
  });
  assert.equal(on.inject_enabled, true);
  assert.equal(on.inject_prompt_system, '注入系统提示');
  assert.equal(on.inject_prompt_fewshot, '注入示例');
  const off = normalizeJudge({ inject_enabled: false, inject_prompt_system: '', inject_prompt_fewshot: '' });
  assert.equal(off.inject_enabled, false);
  assert.equal(off.inject_prompt_system, '');
});

// ---- issue #138：白名单 Key 服务端匹配（/dlp-admin/bypass-keys/match），前端不再拉明文算哈希 ----

test('useBypassKeyMatch: POST /dlp-admin/bypass-keys/match，body 带 keyIds 且要求鉴权', async () => {
  globalThis.__apiCalls = [];
  globalThis.__apiError = null;
  globalThis.__apiResponse = {
    matches: [{ keyId: 'gid://axonhub/APIKey/9', entryId: 'ab12', label: 'ci 管道', scope: 'layers', enabled: true }],
  };
  const cfg = useBypassKeyMatch(['gid://axonhub/APIKey/9']);
  assert.equal(cfg.enabled, true);
  const out = await cfg.queryFn();
  assert.equal(globalThis.__apiCalls.length, 1);
  const call = globalThis.__apiCalls[0];
  assert.equal(call.endpoint, '/dlp-admin/bypass-keys/match');
  assert.equal(call.options.method, 'POST');
  assert.deepEqual(call.options.body, { keyIds: ['gid://axonhub/APIKey/9'] });
  assert.equal(call.options.requireAuth, true);
  assert.deepEqual(out, globalThis.__apiResponse);
  // queryKey 挂在 ['dlp-admin','bypass-keys'] 前缀下：名单增删 invalidate 后匹配结果同步重取
  assert.deepEqual(cfg.queryKey.slice(0, 2), ['dlp-admin', 'bypass-keys']);
});

test('useBypassKeyMatch: 空候选不发起请求（enabled=false）', () => {
  assert.equal(useBypassKeyMatch([]).enabled, false);
});

test('useBypassKeyMatch: 无 read_api_keys 被 shim 拒 403 时归一为 DlpApiError 并带 status', async () => {
  globalThis.__apiCalls = [];
  globalThis.__apiResponse = undefined;
  globalThis.__apiError = Object.assign(new Error('forbidden: 需要 read_api_keys'), { status: 403 });
  const cfg = useBypassKeyMatch(['gid://axonhub/APIKey/9']);
  await assert.rejects(cfg.queryFn(), (e) => {
    assert.equal(e.name, 'DlpApiError');
    assert.equal(e.status, 403);
    return true;
  });
  globalThis.__apiError = null;
});

test('matchedKeyIdSet: matches → keyId 集合（候选「已登记」置灰依据）', () => {
  const s = matchedKeyIdSet([
    { keyId: 'gid://axonhub/APIKey/9', entryId: 'ab12', label: 'ci', scope: 'layers', enabled: true },
    { keyId: 'gid://axonhub/APIKey/11', entryId: 'cd34', label: '', scope: 'all', enabled: false },
  ]);
  assert.ok(s.has('gid://axonhub/APIKey/9'));
  assert.ok(s.has('gid://axonhub/APIKey/11'));
  assert.ok(!s.has('gid://axonhub/APIKey/13'));
  assert.equal(matchedKeyIdSet([]).size, 0);
});
