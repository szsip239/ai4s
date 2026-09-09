import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与同目录 batch-tier.test.mjs 同款：TS 源 transpile 后经 data URL 导入
const srcRoot = join(import.meta.dirname, '..', '..');

const source = readFileSync(join(srcRoot, 'ai4s/apikeys/quota-meter.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { usagePct, meterTone, formatPct } = mod;

test('usagePct：上限为空或 ≤0 视为不设限', () => {
  assert.equal(usagePct(50, null), null);
  assert.equal(usagePct(50, undefined), null);
  assert.equal(usagePct(50, 0), null);
  assert.equal(usagePct(50, -3), null);
});

test('usagePct：正常占比与超额', () => {
  assert.equal(usagePct(1, 4), 25);
  assert.equal(usagePct(0, 100), 0);
  assert.equal(usagePct(150, 100), 150);
});

test('meterTone：80/100 阈值分档', () => {
  assert.equal(meterTone(null), 'normal');
  assert.equal(meterTone(0), 'normal');
  assert.equal(meterTone(79.9), 'normal');
  assert.equal(meterTone(80), 'warning');
  assert.equal(meterTone(99.9), 'warning');
  assert.equal(meterTone(100), 'over');
  assert.equal(meterTone(137), 'over');
});

test('formatPct：小用量不显示 0%，超额保留真实值', () => {
  assert.equal(formatPct(0), '0%');
  assert.equal(formatPct(0.4), '<1%');
  assert.equal(formatPct(45.2), '45%');
  assert.equal(formatPct(137.4), '137%');
});
