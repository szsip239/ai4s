import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 ai4s/apikeys/batch-tier.test.mjs 同款：TS 源 transpile 后经 data URL 导入
const srcRoot = join(import.meta.dirname, '..', '..', '..');

const source = readFileSync(join(srcRoot, 'ai4s/pages/rules/dirty-registry.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { createDirtyRegistry } = mod;

test('dirty-registry: 单上报者 dirty/clean 往返', () => {
  const reg = createDirtyRegistry();
  const report = reg.reporter('l1');
  assert.equal(reg.any(), false);
  report(true);
  assert.equal(reg.any(), true);
  report(false);
  assert.equal(reg.any(), false);
});

test('dirty-registry: 后到上报者的 false 不覆盖先到者的 dirty（issue #69 P2-C 复现链）', () => {
  const reg = createDirtyRegistry();
  const wordlist = reg.reporter('l2.wordlist');
  const recognizersDialog = reg.reporter('l2.recognizers');
  // 词表弄脏
  wordlist(true);
  assert.equal(reg.any(), true);
  // 打开 PII 新增对话框：挂载 effect 上报自身 dirty=false（不得冲掉词表）
  recognizersDialog(false);
  assert.equal(reg.any(), true);
  // 关闭对话框：卸载 cleanup 再报 false
  recognizersDialog(false);
  assert.equal(reg.any(), true);
  // 词表保存后自行复位
  wordlist(false);
  assert.equal(reg.any(), false);
});

test('dirty-registry: 多方 dirty 时各自独立复位', () => {
  const reg = createDirtyRegistry();
  const a = reg.reporter('l2.wordlist');
  const b = reg.reporter('l2.recognizers');
  a(true);
  b(true);
  a(false);
  assert.equal(reg.any(), true);
  b(false);
  assert.equal(reg.any(), false);
});

test('dirty-registry: 同 key 返回同一函数引用（面板 useEffect 依赖稳定）', () => {
  const reg = createDirtyRegistry();
  assert.equal(reg.reporter('l1'), reg.reporter('l1'));
  assert.notEqual(reg.reporter('l1'), reg.reporter('l2'));
});

test('dirty-registry: clear 全部复位（确认丢弃后切层）', () => {
  const reg = createDirtyRegistry();
  reg.reporter('l2.wordlist')(true);
  reg.reporter('l2.recognizers')(true);
  assert.equal(reg.any(), true);
  reg.clear();
  assert.equal(reg.any(), false);
});
