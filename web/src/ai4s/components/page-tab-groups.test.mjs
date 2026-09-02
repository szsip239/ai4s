import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 tier-rank.test.mjs 同款：TS 源 transpile 后经 data URL 导入
// （page-tab-groups 仅含 import type，transpile 后无运行时依赖）
const srcRoot = join(import.meta.dirname, '..', '..');

const source = readFileSync(join(srcRoot, 'ai4s/components/page-tab-groups.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { pageTabGroups } = mod;

// t 恒等 mock：断言 label 实际使用的 locale 键（与 sidebar 同源，不产生新翻译负担）
const t = (key) => key;

test('access 组含 Playground Tab（issue #113）：渠道 | 模型 | Playground，url 与 routeConfigs 对齐', () => {
  assert.deepEqual(pageTabGroups.access(t), [
    { label: 'sidebar.items.channels', url: '/channels' },
    { label: 'sidebar.items.models', url: '/models' },
    { label: 'sidebar.items.playground', url: '/project/playground' },
  ]);
});

test('people/observability 组不受 issue #113 影响', () => {
  assert.deepEqual(
    pageTabGroups.people(t).map((tab) => tab.url),
    ['/users', '/project/roles']
  );
  assert.deepEqual(
    pageTabGroups.observability(t).map((tab) => tab.url),
    ['/project/requests', '/project/usage-stats', '/project/blocks']
  );
});
