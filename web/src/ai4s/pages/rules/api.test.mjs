import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 src/gql/graphql.test.mjs 同款：TS 源 transpile 后运行期依赖替换为桩，经 data URL 导入。
// 只测 normalizePg 纯函数（settings pg 段读侧缺键补默认），hooks/apiRequest 桩不触发。
const source = readFileSync(join(import.meta.dirname, 'api.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText
  .replaceAll(
    "import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';",
    'const useMutation = () => ({}); const useQuery = () => ({}); const useQueryClient = () => ({});'
  )
  .replaceAll("import { toast } from 'sonner';", 'const toast = { error() {} };')
  .replaceAll("import { apiRequest } from '@/lib/api-client';", 'const apiRequest = () => Promise.resolve({});')
  .replaceAll("import { getTokenFromStorage } from '@/stores/authStore';", 'const getTokenFromStorage = () => "";');

const { normalizePg } = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

test('normalizePg: 旧 settings.json 缺 normalize 键补默认 false（issue #44 读侧兼容）', () => {
  assert.deepEqual(normalizePg({ enabled: true, threshold: 0.8 }), {
    enabled: true,
    threshold: 0.8,
    normalize: false,
  });
  assert.deepEqual(normalizePg(undefined), { enabled: false, threshold: 0.7, normalize: false });
});

test('normalizePg: normalize 显式两档原样保留（issue #97 PgPanel 开关读写语义）', () => {
  // 面板整体 PUT 时 normalize 必填（shim _SETTINGS_PG_KEYS）——读侧归一不得吞掉用户已保存的档位
  assert.equal(normalizePg({ enabled: true, threshold: 0.7, normalize: true }).normalize, true);
  assert.equal(normalizePg({ enabled: true, threshold: 0.7, normalize: false }).normalize, false);
});
