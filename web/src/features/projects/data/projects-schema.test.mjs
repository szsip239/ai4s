import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 apikeys-query.test.mjs 同款：TS 源 transpile 后经 data URL 导入，'@/gql/pagination' 打桩；
// zod 用真实包（校验逻辑本身就是要测的行为），经 import.meta.resolve 转 file URL 供 data 模块导入。
const zodUrl = import.meta.resolve('zod');
const source = readFileSync(join(import.meta.dirname, 'schema.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText
  .replaceAll("import { z } from 'zod';", `import { z } from '${zodUrl}';`)
  .replaceAll(
    "import { pageInfoSchema } from '@/gql/pagination';",
    'const pageInfoSchema = z.object({ hasNextPage: z.boolean(), hasPreviousPage: z.boolean(), startCursor: z.string().optional().nullable(), endCursor: z.string().optional().nullable() });'
  );

const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);
const { projectConnectionSchema } = mod;

function connectionWith(mode) {
  return {
    edges: [
      {
        node: {
          id: 'gid://axonhub/Project/7',
          createdAt: '2026-10-14T10:00:00Z',
          updatedAt: '2026-10-14T10:00:00Z',
          name: 'Local Mac',
          description: '',
          status: 'archived',
          profiles: {
            activeProfile: 'Local Mac',
            profiles: [{ name: 'Local Mac', channelIDs: [14], channelTags: null, channelTagsMatchMode: mode }],
          },
        },
        cursor: 'c1',
      },
    ],
    pageInfo: { hasNextPage: false, hasPreviousPage: false, startCursor: 'c1', endCursor: 'c1' },
    totalCount: 1,
  };
}

test('projectConnectionSchema: 活栈空串 channelTagsMatchMode 归一为未设置（2026-10-16 线上校验失败回归）', () => {
  // beta10 后端对缺省 channelTagsMatchMode 回落空串（同 apikeys issue #83），
  // 此前 z.enum().optional().nullable() 直挂导致项目列表整体 ZodError。
  const result = projectConnectionSchema.safeParse(connectionWith(''));
  assert.ok(result.success, JSON.stringify(result.error?.issues));
  assert.equal(result.data.edges[0].node.profiles.profiles[0].channelTagsMatchMode, undefined);
});

test('projectConnectionSchema: 合法枚举值与 null 原样保留', () => {
  for (const mode of ['any', 'all', 'none', null]) {
    const result = projectConnectionSchema.safeParse(connectionWith(mode));
    assert.ok(result.success, `${mode}: ${JSON.stringify(result.error?.issues)}`);
    assert.equal(result.data.edges[0].node.profiles.profiles[0].channelTagsMatchMode, mode);
  }
});

test('projectConnectionSchema: 非法非空值仍拒绝（不放宽语义）', () => {
  const result = projectConnectionSchema.safeParse(connectionWith('match_all'));
  assert.equal(result.success, false);
});
