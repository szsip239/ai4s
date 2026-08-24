import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 ai4s/pages/rules/dirty-registry.test.mjs 同款：TS 源 transpile 后经 data URL 导入
const srcRoot = join(import.meta.dirname, '..', '..', '..', '..');

const source = readFileSync(join(srcRoot, 'ai4s/pages/rules/panels/settingsValidation.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { validateJudge, validatePg } = mod;

const validJudge = {
  enabled: true,
  model: 'm0',
  base_url: 'http://judge/v1',
  timeout: 8,
  prompt_system: '系统提示 {terms}',
  prompt_fewshot: '示例',
  threshold: 0.8,
  action: 'shadow',
};

test('validateJudge: 合法配置（含 threshold/action，issue #94）通过', () => {
  assert.equal(validateJudge(validJudge), null);
  for (const action of ['off', 'shadow', 'warn', 'reject']) {
    assert.equal(validateJudge({ ...validJudge, action }), null, `action=${action} 应合法`);
  }
});

test('validateJudge: threshold 越界/非数拒绝（issue #94）', () => {
  assert.match(validateJudge({ ...validJudge, threshold: 1.5 }), /threshold/);
  assert.match(validateJudge({ ...validJudge, threshold: -0.1 }), /threshold/);
  assert.match(validateJudge({ ...validJudge, threshold: Number.NaN }), /threshold/);
});

test('validateJudge: action 非法档位拒绝（issue #94）', () => {
  assert.match(validateJudge({ ...validJudge, action: 'block' }), /action/);
  assert.match(validateJudge({ ...validJudge, action: '' }), /action/);
});

test('validateJudge: 既有规则不回归（空 model/timeout/prompt 仍拒绝）', () => {
  assert.match(validateJudge({ ...validJudge, model: ' ' }), /model/);
  assert.match(validateJudge({ ...validJudge, timeout: 0 }), /timeout/);
  assert.match(validateJudge({ ...validJudge, prompt_fewshot: '' }), /prompt/);
});

test('validatePg: threshold 0~1（既有行为回归）', () => {
  assert.equal(validatePg({ enabled: true, threshold: 0.7, normalize: false }), null);
  assert.match(validatePg({ enabled: true, threshold: 2, normalize: false }), /threshold/);
});
