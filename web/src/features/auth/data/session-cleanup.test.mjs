import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 graphql.test.mjs 同款：TS 源 transpile 后替换运行时依赖为内存 stub，经 data URL 导入
const source = readFileSync(join(import.meta.dirname, 'session-cleanup.ts'), 'utf8');
const transpiled = ts
  .transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
  })
  .outputText.replaceAll(
    "import { removeTokenFromStorage, useAuthStore } from '@/stores/authStore';",
    `const removeTokenFromStorage = () => { globalThis.__calls.token++; };
     const useAuthStore = { getState: () => ({ auth: { reset: () => { globalThis.__calls.authReset++; } } }) };`
  )
  .replaceAll(
    "import { useProjectStore } from '@/stores/projectStore';",
    `const useProjectStore = { getState: () => ({ clearSelectedProjectId: () => { globalThis.__calls.projectClear++; } }) };`
  );

globalThis.__calls = { token: 0, authReset: 0, projectClear: 0, queryClear: 0 };
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { clearSessionState } = mod;

test('issue #139：登出清理覆盖 token/auth store/选中项目/React Query 缓存四处', () => {
  clearSessionState({ clear: () => globalThis.__calls.queryClear++ });
  assert.deepEqual(globalThis.__calls, { token: 1, authReset: 1, projectClear: 1, queryClear: 1 });
});

test('useSignOut 走 clearSessionState（接线防回归）', () => {
  const authSource = readFileSync(join(import.meta.dirname, 'auth.ts'), 'utf8');
  assert.match(authSource, /clearSessionState\(queryClient\)/);
  assert.match(authSource, /useQueryClient/);
});
