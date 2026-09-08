import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 ai4s/apikeys/batch-tier.test.mjs 同款：TS 源 transpile 后经 data URL 导入，hooks/client/schema 全部打桩，
// 只测查询构建器的字段选择（issue #138：列表查询不再下发 key 明文，单条查询保留刻意查看路径）。
const source = readFileSync(join(import.meta.dirname, 'apikeys.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText
  .replaceAll(
    "import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';",
    'const useMutation = () => ({}); const useQuery = () => ({}); const useQueryClient = () => ({}); const keepPreviousData = undefined;'
  )
  .replaceAll("import { graphqlRequest } from '@/gql/graphql';", 'const graphqlRequest = () => Promise.resolve({});')
  .replaceAll("import { useTranslation } from 'react-i18next';", "const useTranslation = () => ({ t: (k) => k });")
  .replaceAll("import { toast } from 'sonner';", 'const toast = { success() {}, error() {} };')
  .replaceAll("import { useSelectedProjectId } from '@/stores/projectStore';", 'const useSelectedProjectId = () => null;')
  .replaceAll("import { useErrorHandler } from '@/hooks/use-error-handler';", 'const useErrorHandler = () => ({ handleError: () => {} });')
  .replaceAll("import { useRequestPermissions } from '../../../hooks/useRequestPermissions';", 'const useRequestPermissions = () => ({ canViewUsers: false });')
  .replaceAll(
    "import { apiKeyConnectionSchema, apiKeyProfileQuotaUsageSchema, apiKeyProfileTemplateSchema, apiKeySchema, apiKeyTokenUsageStatsSchema } from './schema';",
    'const __mk = () => ({ parse: (x) => x, array: () => ({ parse: (x) => x }) });\n' +
      'const apiKeyConnectionSchema = __mk(); const apiKeyProfileQuotaUsageSchema = __mk(); const apiKeyProfileTemplateSchema = __mk(); const apiKeySchema = __mk(); const apiKeyTokenUsageStatsSchema = __mk();'
  );

const { buildApiKeysQuery, buildApiKeyQuery } = await import(
  `data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`
);

/** 行锚定字段名匹配（查询文本无第二处独立成行的同名字段） */
const hasFieldLine = (query, field) => new RegExp(`^\\s*${field}\\s*$`, 'm').test(query);

test('issue #138：列表查询 GetApiKeys 不再请求 key 明文字段（其余字段不动）', () => {
  const query = buildApiKeysQuery({ canViewUsers: false });
  assert.equal(hasFieldLine(query, 'key'), false, '列表 node 选择块不得含 key 字段');
  for (const f of ['id', 'createdAt', 'updatedAt', 'name', 'type', 'status', 'scopes', 'allowedIps']) {
    assert.ok(hasFieldLine(query, f), `列表仍应含 ${f}`);
  }
});

test('issue #138：单条查询 GetApiKey 保留 key（「查看 key」对话框的刻意单条明文路径）', () => {
  const query = buildApiKeyQuery({ canViewUsers: false });
  assert.equal(hasFieldLine(query, 'key'), true);
});
