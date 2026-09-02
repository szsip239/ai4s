import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 key-guide.test.mjs 同款：TS 源 transpile 后经 data URL 导入
const srcRoot = join(import.meta.dirname, '..', '..', '..');

const source = readFileSync(join(srcRoot, 'ai4s/pages/my-keys/key-usage.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { formatTokenCount, formatCredits, quotaProgress, activeUsageEntry, windowTotalTokens, modelTotalTokens } = mod;

test('formatTokenCount: zh 万/亿口径（与 #82 指南一致；档位实值为 issue #84 放大后）', () => {
  assert.equal(formatTokenCount(0, true), '0');
  assert.equal(formatTokenCount(9999, true), '9999');
  assert.equal(formatTokenCount(150000000, true), '1.5亿'); // 体验档
  assert.equal(formatTokenCount(750000000, true), '7.5亿'); // 标准档
  assert.equal(formatTokenCount(3000000000, true), '30亿'); // 高档
  assert.equal(formatTokenCount(125000, true), '12.5万');
});

test('formatTokenCount: en K/M/B 口径（issue #84 起 ≥1e9 用 B）', () => {
  assert.equal(formatTokenCount(999, false), '999');
  assert.equal(formatTokenCount(1500, false), '1.5K');
  assert.equal(formatTokenCount(150000000, false), '150M'); // 体验档
  assert.equal(formatTokenCount(750000000, false), '750M'); // 标准档
  assert.equal(formatTokenCount(999000000, false), '999M'); // 边界：未达 1e9 仍走 M
  assert.equal(formatTokenCount(1000000000, false), '1B'); // 边界：恰 1e9 起走 B
  assert.equal(formatTokenCount(3000000000, false), '3B'); // 高档
});

test('formatCredits: 最多两位小数，浮点尾巴截断', () => {
  assert.equal(formatCredits(3), '3');
  assert.equal(formatCredits(0.042), '0.04');
  assert.equal(formatCredits(2.9999999), '3');
  assert.equal(formatCredits(1.5), '1.5');
});

test('quotaProgress: cost 优先（credit 帽主控），pct=已用/配额', () => {
  const e = {
    quota: { cost: 100, totalTokens: 150000000, requests: null }, // 体验档（issue #84 数值）
    usage: { totalCost: '50', totalTokens: 100, requestCount: 2 },
  };
  const p = quotaProgress(e);
  assert.equal(p.kind, 'cost');
  assert.equal(p.used, 50);
  assert.equal(p.total, 100);
  assert.equal(Math.round(p.pct), 50);
});

test('quotaProgress: cost 空时落 token/requests 维度', () => {
  const t = quotaProgress({ quota: { cost: null, totalTokens: 150000000 }, usage: { totalTokens: 15000000 } });
  assert.equal(t.kind, 'totalTokens');
  assert.equal(Math.round(t.pct), 10);
  const r = quotaProgress({ quota: { cost: null, totalTokens: null, requests: 100 }, usage: { requestCount: 25 } });
  assert.equal(r.kind, 'requests');
  assert.equal(r.pct, 25);
});

test('quotaProgress: 零用量显示 0；配额全空（不设限）返回 null；usage 缺省不炸', () => {
  const zero = quotaProgress({ quota: { cost: 100 }, usage: { totalCost: 0, totalTokens: 0, requestCount: 0 } });
  assert.equal(zero.used, 0);
  assert.equal(zero.pct, 0);
  assert.equal(quotaProgress({ quota: { cost: null, totalTokens: null, requests: null } }), null);
  assert.equal(quotaProgress({ quota: null, usage: null }), null);
  const noUsage = quotaProgress({ quota: { cost: 100 } });
  assert.equal(noUsage.used, 0); // usage 字段缺省按 0 计，不报错
});

test('activeUsageEntry: 按 activeProfile 名匹配；无档/无条目/空数组均 undefined', () => {
  const entries = [{ profileName: '体验档' }, { profileName: '标准档' }];
  assert.equal(activeUsageEntry(entries, '标准档').profileName, '标准档');
  assert.equal(activeUsageEntry(entries, '体验档').profileName, '体验档');
  assert.equal(activeUsageEntry(entries, '高档'), undefined);
  assert.equal(activeUsageEntry(entries, null), undefined);
  assert.equal(activeUsageEntry([], '体验档'), undefined);
  assert.equal(activeUsageEntry(null, '体验档'), undefined);
});

test('windowTotalTokens: 输入+输出合计；空/缺省按 0 不炸', () => {
  assert.equal(windowTotalTokens({ inputTokens: 1200, outputTokens: 3400 }), 4600);
  assert.equal(windowTotalTokens({ inputTokens: 0, outputTokens: 0 }), 0);
  assert.equal(windowTotalTokens({}), 0);
  assert.equal(windowTotalTokens(null), 0);
  assert.equal(windowTotalTokens(undefined), 0);
  // cached/reasoning 是子类分解，不重复计入合计（与管理员侧 token chart 同口径）
  assert.equal(windowTotalTokens({ inputTokens: 100, outputTokens: 50, cachedTokens: 40, reasoningTokens: 10 }), 150);
});

test('modelTotalTokens: 单模型同口径合计', () => {
  assert.equal(modelTotalTokens({ modelId: 'gpt-x', inputTokens: 10, outputTokens: 5 }), 15);
  assert.equal(modelTotalTokens({}), 0);
});
