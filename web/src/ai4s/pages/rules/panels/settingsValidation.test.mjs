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
  for (const action of ['off', 'shadow', 'warn']) {
    assert.equal(validateJudge({ ...validJudge, action }), null, `action=${action} 应合法`);
  }
});

test('validateJudge: reject 档拒绝保存（issue #101 契约纪律——语义层永不阻断，schema 档位存在但面板不可选/不保存）', () => {
  assert.match(validateJudge({ ...validJudge, action: 'reject' }), /契约|阻断|永不/);
});

test('JudgePanel: reject 档 UI 灰置不可选（issue #101 契约纪律，源码级断言——无组件测试基建的最小锚点）', () => {
  const panel = readFileSync(join(srcRoot, 'ai4s/pages/rules/panels/JudgePanel.tsx'), 'utf8');
  assert.match(panel, /SelectItem value='reject' disabled/, 'reject 选项须 disabled 灰置');
  assert.match(panel, /语义层永不阻断/, '灰置选项须注明契约依据');
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
  const base = { enabled: true, threshold: 0.7, normalize: false, block_enabled: false, block_threshold: 0.9 };
  assert.equal(validatePg(base), null);
  assert.match(validatePg({ ...base, threshold: 2 }), /threshold/);
});

test('validatePg: normalize 开关两档均合法（issue #97 面板开关读写键位）', () => {
  // normalize 是布尔开关（后端 admin_api 类型校验兜底），预检不应按取值拒绝任何一档
  const base = { enabled: true, threshold: 0.7, block_enabled: false, block_threshold: 0.9 };
  assert.equal(validatePg({ ...base, normalize: true }), null);
  assert.equal(validatePg({ ...base, enabled: false, normalize: false }), null);
});

test('validatePg: block_threshold 0~1 预检（issue #103 阻断阈值；放行用例含两新键）', () => {
  const base = { enabled: true, threshold: 0.7, normalize: false, block_enabled: true, block_threshold: 0.9 };
  assert.equal(validatePg(base), null); // 阻断开放行用例
  assert.match(validatePg({ ...base, block_threshold: 1.5 }), /block_threshold/);
  assert.match(validatePg({ ...base, block_threshold: -0.1 }), /block_threshold/);
  assert.match(validatePg({ ...base, block_threshold: Number.NaN }), /block_threshold/);
  // block_enabled 是布尔开关（后端类型校验兜底），预检不按取值拒绝
  assert.equal(validatePg({ ...base, block_enabled: false }), null);
});
