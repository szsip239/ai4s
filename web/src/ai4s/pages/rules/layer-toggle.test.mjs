import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 page-tab-groups.test.mjs 同款：TS 源 transpile 后经 data URL 导入（layer-toggle 仅 import type，无运行时依赖）
const srcRoot = join(import.meta.dirname, '..', '..', '..');

const source = readFileSync(join(srcRoot, 'ai4s/pages/rules/layer-toggle.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { layerEnabled, buildSettingsWithLayerEnabled, responsePartiallyClosed } = mod;

const baseDoc = () => ({
  version: 1,
  judge: { enabled: true, model: 'm', threshold: 0.8 },
  edm: { enabled: true, min_hits: 3 },
  pg: { enabled: false, threshold: 0.7, normalize: true, block_enabled: true, block_threshold: 0.9 },
  rules: { enabled: true, block: false },
  l2: { enabled: true, opf: { enabled: true, timeout_ms: 800, max_chars: 4000 } },
});

test('读侧：缺段回退 true（与 shim 缺段默认语义对齐），出席段读真实值', () => {
  const doc = baseDoc();
  assert.equal(layerEnabled(doc, 'l1'), true); // 缺段 → true
  assert.equal(layerEnabled(doc, 'l2'), true);
  assert.equal(layerEnabled(doc, 'response'), true); // 缺段 → true
  assert.equal(layerEnabled(doc, 'l3'), true);
  assert.equal(layerEnabled(doc, 'judge'), true);
  assert.equal(layerEnabled(doc, 'rules'), true);
  assert.equal(layerEnabled(doc, 'pg'), false); // 出席 false 不臆造
});

test('写侧：翻转 l1 后三段齐全，其余段原样保留', () => {
  const out = buildSettingsWithLayerEnabled(baseDoc(), 'l1', false);
  assert.equal(out.l1.enabled, false);
  assert.equal(out.l2.enabled, true); // 缺段补齐
  assert.equal(out.response.enabled, true); // 缺段补齐
  assert.equal(out.judge.threshold, 0.8); // 子键不丢
  assert.equal(out.pg.block_threshold, 0.9);
});

test('写侧：翻转 l2 保留 opf 子节（issue #127 纪律）', () => {
  const out = buildSettingsWithLayerEnabled(baseDoc(), 'l2', false);
  assert.equal(out.l2.enabled, false);
  assert.deepEqual(out.l2.opf, { enabled: true, timeout_ms: 800, max_chars: 4000 });
});

test('写侧：翻转多键段只改 enabled（judge/rules/pg/edm 子键保留）', () => {
  const doc = baseDoc();
  assert.equal(buildSettingsWithLayerEnabled(doc, 'judge', false).judge.model, 'm');
  const pgOut = buildSettingsWithLayerEnabled(doc, 'pg', true);
  assert.equal(pgOut.pg.enabled, true);
  assert.equal(pgOut.pg.normalize, true);
  assert.equal(pgOut.pg.block_enabled, true);
  const rulesOut = buildSettingsWithLayerEnabled(doc, 'rules', false);
  assert.equal(rulesOut.rules.enabled, false);
  assert.equal(rulesOut.rules.block, false);
  const edmOut = buildSettingsWithLayerEnabled(doc, 'l3', false);
  assert.equal(edmOut.edm.enabled, false);
  assert.equal(edmOut.edm.min_hits, 3);
});

test('响应侧联动：response 开且 l1/l2 任一为关时部分关闭', () => {
  assert.equal(responsePartiallyClosed(baseDoc()), false);
  assert.equal(responsePartiallyClosed(buildSettingsWithLayerEnabled(baseDoc(), 'l1', false)), true);
  assert.equal(responsePartiallyClosed(buildSettingsWithLayerEnabled(baseDoc(), 'l2', false)), true);
  // response 整层关闭时不是「部分关闭」（节点显示已关闭徽标）
  const off = buildSettingsWithLayerEnabled(buildSettingsWithLayerEnabled(baseDoc(), 'l1', false), 'response', false);
  assert.equal(responsePartiallyClosed(off), false);
});
