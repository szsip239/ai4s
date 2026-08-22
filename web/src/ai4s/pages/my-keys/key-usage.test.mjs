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

const { formatTokenCount, formatCredits, quotaProgress, activeUsageEntry } = mod;

test('formatTokenCount: zh 万/亿口径（与 #82 指南一致）', () => {
  assert.equal(formatTokenCount(0, true), '0');
  assert.equal(formatTokenCount(9999, true), '9999');
  assert.equal(formatTokenCount(4300000, true), '430万');
  assert.equal(formatTokenCount(43000000, true), '4300万');
  assert.equal(formatTokenCount(215000000, true), '2.2亿'); // 2.15 亿 round-half 到 1 位小数
  assert.equal(formatTokenCount(125000, true), '12.5万');
});

test('formatTokenCount: en K/M 口径', () => {
  assert.equal(formatTokenCount(999, false), '999');
  assert.equal(formatTokenCount(1500, false), '1.5K');
  assert.equal(formatTokenCount(4300000, false), '4.3M');
  assert.equal(formatTokenCount(215000000, false), '215M');
});

test('formatCredits: 最多两位小数，浮点尾巴截断', () => {
  assert.equal(formatCredits(3), '3');
  assert.equal(formatCredits(0.042), '0.04');
  assert.equal(formatCredits(2.9999999), '3');
  assert.equal(formatCredits(1.5), '1.5');
});

test('quotaProgress: cost 优先（credit 帽主控），pct=已用/配额', () => {
  const e = {
    quota: { cost: 3, totalTokens: 4300000, requests: null },
    usage: { totalCost: '1.5', totalTokens: 100, requestCount: 2 },
  };
  const p = quotaProgress(e);
  assert.equal(p.kind, 'cost');
  assert.equal(p.used, 1.5);
  assert.equal(p.total, 3);
  assert.equal(Math.round(p.pct), 50);
});

test('quotaProgress: cost 空时落 token/requests 维度', () => {
  const t = quotaProgress({ quota: { cost: null, totalTokens: 4300000 }, usage: { totalTokens: 430000 } });
  assert.equal(t.kind, 'totalTokens');
  assert.equal(Math.round(t.pct), 10);
  const r = quotaProgress({ quota: { cost: null, totalTokens: null, requests: 100 }, usage: { requestCount: 25 } });
  assert.equal(r.kind, 'requests');
  assert.equal(r.pct, 25);
});

test('quotaProgress: 零用量显示 0；配额全空（不设限）返回 null；usage 缺省不炸', () => {
  const zero = quotaProgress({ quota: { cost: 3 }, usage: { totalCost: 0, totalTokens: 0, requestCount: 0 } });
  assert.equal(zero.used, 0);
  assert.equal(zero.pct, 0);
  assert.equal(quotaProgress({ quota: { cost: null, totalTokens: null, requests: null } }), null);
  assert.equal(quotaProgress({ quota: null, usage: null }), null);
  const noUsage = quotaProgress({ quota: { cost: 3 } });
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
