import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 同 apikeys-query.test.mjs：TS 源 transpile 后经 data URL 导入；zod 真实包，别名依赖打桩。
const zodUrl = import.meta.resolve('zod');
const source = readFileSync(join(import.meta.dirname, 'schema.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText
  .replaceAll("import { z } from 'zod';", `import { z } from '${zodUrl}';`)
  .replaceAll(
    "import { pageInfoSchema } from '@/gql/pagination';",
    'const pageInfoSchema = z.object({ hasNextPage: z.boolean(), hasPreviousPage: z.boolean(), startCursor: z.string().optional().nullable(), endCursor: z.string().optional().nullable() });'
  )
  .replaceAll("import { userSchema } from '@/features/users/data/schema';", 'const userSchema = z.object({});');

const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);
const { apiKeySchema, apiKeyProfileSchema } = mod;

function apiKeyWith(modelMappings) {
  return {
    id: 'gid://axonhub/APIKey/32',
    createdAt: '2026-10-14T10:00:00Z',
    updatedAt: '2026-10-14T10:00:00Z',
    name: 'macllm-daily',
    type: 'user',
    status: 'enabled',
    scopes: null,
    allowedIps: null,
    profiles: {
      activeProfile: 'daily',
      profiles: [
        {
          name: 'daily',
          modelMappings,
          channelIDs: [14],
          channelTags: null,
          channelTagsMatchMode: '',
          modelIDs: ['qwen3.8-27b-local'],
          loadBalanceStrategy: 'default',
          traceStickyMode: 'default',
        },
      ],
    },
  };
}

test('apiKeySchema: 直连 GraphQL 写入的 modelMappings null 归一为 []（issue #81 语义，macllm  key 实证）', () => {
  const result = apiKeySchema.safeParse(apiKeyWith(null));
  assert.ok(result.success, JSON.stringify(result.error?.issues));
  assert.deepEqual(result.data.profiles.profiles[0].modelMappings, []);
  // 同 payload 的 channelTagsMatchMode 空串继续落 'any'（issue #83 行为锁定）
  assert.equal(result.data.profiles.profiles[0].channelTagsMatchMode, 'any');
});

test('apiKeySchema: modelMappings 缺省与正常数组均可用', () => {
  const withoutField = apiKeyWith(undefined);
  delete withoutField.profiles.profiles[0].modelMappings;
  const r1 = apiKeySchema.safeParse(withoutField);
  assert.ok(r1.success, JSON.stringify(r1.error?.issues));
  assert.deepEqual(r1.data.profiles.profiles[0].modelMappings, []);

  const r2 = apiKeySchema.safeParse(apiKeyWith([{ from: 'a', to: 'b' }]));
  assert.ok(r2.success, JSON.stringify(r2.error?.issues));
  assert.deepEqual(r2.data.profiles.profiles[0].modelMappings, [{ from: 'a', to: 'b' }]);
});

test('apiKeyProfileSchema: 模板 profile 的 modelMappings null 同样归一为 []', () => {
  const result = apiKeyProfileSchema.safeParse({ name: 't', modelMappings: null });
  assert.ok(result.success, JSON.stringify(result.error?.issues));
  assert.deepEqual(result.data.modelMappings, []);
});
