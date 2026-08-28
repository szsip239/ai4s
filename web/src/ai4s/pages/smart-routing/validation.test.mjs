import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 rules/panels/settingsValidation.test.mjs 同款：TS 源 transpile 后经 data URL 导入
const srcRoot = join(import.meta.dirname, '..', '..', '..');

const source = readFileSync(join(srcRoot, 'ai4s/pages/smart-routing/validation.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { validateRouting } = mod;

/** 合法基准：normalizeRouting 输出形状（十键齐全） */
const validRouting = {
  enabled: true,
  threshold: 0.5,
  tiers: { simple: 'deepseek-v4-flash', complex: 'gpt-5.6-luna' },
  timeout: 4,
  max_concurrency: 2,
  prompt: '分类系统提示',
  escalate_conf: 0.85,
  session_ttl: 3600,
  tool_loop_lock: true,
  thinking_lock: false,
};

test('validateRouting: 合法配置通过（含关态与锁两档组合）', () => {
  assert.equal(validateRouting(validRouting), null);
  assert.equal(validateRouting({ ...validRouting, enabled: false }), null);
  for (const tool_loop_lock of [true, false]) {
    for (const thinking_lock of [true, false]) {
      assert.equal(validateRouting({ ...validRouting, tool_loop_lock, thinking_lock }), null);
    }
  }
  // 边界值合法（shim 含边界：0<=x<=1；session_ttl/timeout 任意 >0 数值）
  assert.equal(validateRouting({ ...validRouting, threshold: 0, escalate_conf: 1, session_ttl: 0.5, timeout: 0.1 }), null);
});

test('validateRouting: threshold 越界/非数拒绝（shim #117 0~1）', () => {
  assert.match(validateRouting({ ...validRouting, threshold: 1.5 }), /threshold/);
  assert.match(validateRouting({ ...validRouting, threshold: -0.1 }), /threshold/);
  assert.match(validateRouting({ ...validRouting, threshold: Number.NaN }), /threshold/);
});

test('validateRouting: tiers 两档非空 + 字符白名单（shim _SETTINGS_MODEL_SAFE 同款：进响应头防拆分）', () => {
  assert.match(validateRouting({ ...validRouting, tiers: { simple: '', complex: 'm' } }), /tiers\.simple/);
  assert.match(validateRouting({ ...validRouting, tiers: { simple: '  ', complex: 'm' } }), /tiers\.simple/);
  assert.match(validateRouting({ ...validRouting, tiers: { simple: 'm', complex: '' } }), /tiers\.complex/);
  // 非法字符（空格/斜杠/中文）拒绝——值进 x-resolved-model 响应头
  assert.match(validateRouting({ ...validRouting, tiers: { simple: 'a b', complex: 'm' } }), /tiers\.simple/);
  assert.match(validateRouting({ ...validRouting, tiers: { simple: 'm', complex: 'a/b' } }), /tiers\.complex/);
  assert.match(validateRouting({ ...validRouting, tiers: { simple: '模型', complex: 'm' } }), /tiers\.simple/);
  // 超长（>128）拒绝
  assert.match(validateRouting({ ...validRouting, tiers: { simple: 'a'.repeat(129), complex: 'm' } }), /tiers\.simple/);
  // 白名单内字符全放行（字母数字 . _ : -）
  assert.equal(validateRouting({ ...validRouting, tiers: { simple: 'a.B_c:d-1', complex: 'x' } }), null);
});

test('validateRouting: timeout > 0（shim #117）', () => {
  assert.match(validateRouting({ ...validRouting, timeout: 0 }), /timeout/);
  assert.match(validateRouting({ ...validRouting, timeout: -1 }), /timeout/);
  assert.match(validateRouting({ ...validRouting, timeout: Number.NaN }), /timeout/);
});

test('validateRouting: max_concurrency ≥1 整数（shim #117；布尔不放行）', () => {
  assert.match(validateRouting({ ...validRouting, max_concurrency: 0 }), /max_concurrency/);
  assert.match(validateRouting({ ...validRouting, max_concurrency: 1.5 }), /max_concurrency/);
  assert.match(validateRouting({ ...validRouting, max_concurrency: true }), /max_concurrency/);
});

test('validateRouting: escalate_conf 0~1（shim #119）', () => {
  assert.match(validateRouting({ ...validRouting, escalate_conf: 1.01 }), /escalate_conf/);
  assert.match(validateRouting({ ...validRouting, escalate_conf: -0.1 }), /escalate_conf/);
});

test('validateRouting: session_ttl > 0（shim #119）', () => {
  assert.match(validateRouting({ ...validRouting, session_ttl: 0 }), /session_ttl/);
  assert.match(validateRouting({ ...validRouting, session_ttl: -60 }), /session_ttl/);
});

test('validateRouting: prompt 非空（shim #119：出席即非空字符串）', () => {
  assert.match(validateRouting({ ...validRouting, prompt: '' }), /prompt/);
  assert.match(validateRouting({ ...validRouting, prompt: '   ' }), /prompt/);
});

test('validateRouting: 布尔键运行时类型防御（手改 JSON/异常数据，与后端布尔校验同款）', () => {
  assert.match(validateRouting({ ...validRouting, enabled: 1 }), /enabled/);
  assert.match(validateRouting({ ...validRouting, tool_loop_lock: 'yes' }), /tool_loop_lock/);
  assert.match(validateRouting({ ...validRouting, thinking_lock: 0 }), /thinking_lock/);
});
