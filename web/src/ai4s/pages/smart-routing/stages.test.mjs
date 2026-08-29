import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 validation.test.mjs 同款：TS 源 transpile 后经 data URL 导入
const srcRoot = join(import.meta.dirname, '..', '..', '..');

const source = readFileSync(join(srcRoot, 'ai4s/pages/smart-routing/stages.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { ROUTER_STAGES, ROUTER_EXTRA_NAV, routingEnabledState } = mod;

test('ROUTER_STAGES：四阶段按 shim route_resolve 决策流顺序（session→classify→decision→tiers）', () => {
  assert.deepEqual(
    ROUTER_STAGES.map((s) => s.key),
    ['session', 'classify', 'decision', 'tiers']
  );
  // 每阶段带 i18n 键；日志为唯一附加导航项
  for (const s of ROUTER_STAGES) assert.ok(s.labelKey.startsWith('ai4s.smartRouting.nav.'));
  assert.deepEqual(
    ROUTER_EXTRA_NAV.map((s) => s.key),
    ['log']
  );
});

test('ROUTER_STAGES/EXTRA_NAV 的 i18n 键在 zh-CN/en 补丁包中齐全（防漏键白屏）', () => {
  for (const loc of ['zh-CN', 'en']) {
    const patch = JSON.parse(readFileSync(join(srcRoot, `ai4s/locales/${loc}/ai4s-patch.json`), 'utf8'));
    for (const s of [...ROUTER_STAGES, ...ROUTER_EXTRA_NAV]) {
      assert.ok(typeof patch[s.labelKey] === 'string', `${loc} 缺 ${s.labelKey}`);
    }
  }
});

test('routingEnabledState：查询失败/文档缺席 → null（未知，不臆造）', () => {
  assert.equal(routingEnabledState(null, false), null);
  assert.equal(routingEnabledState(undefined, false), null);
  assert.equal(routingEnabledState({ routing: { enabled: true } }, true), null);
  assert.equal(routingEnabledState(null, true), null);
});

test('routingEnabledState：routing 节缺席 → false（shim #117 缺席=关态合法）', () => {
  assert.equal(routingEnabledState({}, false), false);
  assert.equal(routingEnabledState({ routing: undefined }, false), false);
});

test('routingEnabledState：routing.enabled 真实值透传', () => {
  assert.equal(routingEnabledState({ routing: { enabled: true } }, false), true);
  assert.equal(routingEnabledState({ routing: { enabled: false } }, false), false);
});
