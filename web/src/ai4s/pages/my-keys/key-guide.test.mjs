import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 ai4s/apikeys/batch-tier.test.mjs 同款：TS 源 transpile 后经 data URL 导入
const srcRoot = join(import.meta.dirname, '..', '..', '..');

const source = readFileSync(join(srcRoot, 'ai4s/pages/my-keys/key-guide.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { KEY_PLACEHOLDER, GUIDE_MODELS, buildGuideSnippets } = mod;

const ORIGIN = 'https://example-host.tail000000.ts.net:8445';

test('buildGuideSnippets: curl 指向 origin/v1/chat/completions 且带 Bearer 占位符', () => {
  const { base, curl } = buildGuideSnippets(ORIGIN);
  assert.equal(base, `${ORIGIN}/v1`); // base 由纯数据层组合，组件不再拼 URL（评审 P2）
  assert.ok(curl.includes(`${ORIGIN}/v1/chat/completions`));
  assert.ok(curl.includes(`Authorization: Bearer ${KEY_PLACEHOLDER}`));
  assert.ok(curl.includes('"model":"echo-test"'));
});

test('buildGuideSnippets: python base_url 到 /v1，api_key 用占位符', () => {
  const { python } = buildGuideSnippets(ORIGIN);
  assert.ok(python.includes(`base_url="${ORIGIN}/v1"`));
  assert.ok(python.includes(`api_key="${KEY_PLACEHOLDER}"`));
  assert.ok(python.includes('client.chat.completions.create('));
});

test('buildGuideSnippets: Claude Code 类 BASE_URL 只到入口根（客户端自拼 /v1/messages）', () => {
  const { claudeCode } = buildGuideSnippets(ORIGIN);
  assert.ok(claudeCode.includes(`ANTHROPIC_BASE_URL=${ORIGIN}\n`) || claudeCode.endsWith(`ANTHROPIC_BASE_URL=${ORIGIN}`));
  assert.ok(!claudeCode.split('\n')[0].includes('/v1'));
  assert.ok(claudeCode.includes(`ANTHROPIC_AUTH_TOKEN=${KEY_PLACEHOLDER}`));
});

test('GUIDE_MODELS: 名称唯一、均带 i18n noteKey，含回声联调模型 echo-test', () => {
  const names = GUIDE_MODELS.map((m) => m.name);
  assert.equal(new Set(names).size, names.length);
  for (const m of GUIDE_MODELS) assert.ok(m.noteKey.length > 0, m.name);
  assert.ok(names.includes('echo-test'));
});

// 守卫：指南所列模型必须落在 deploy/pricing.json 的渠道模型键内
// （pricing.json = 模型计价唯一事实源，见 docs/contracts/quota-tiers.md；路径耦合可接受——
//  指南列了渠道没有的模型时此测试先红，逼着同步）。
// echo-test 是 DLP 回声渠道（联调用，无计价），显式豁免。
test('GUIDE_MODELS ⊆ deploy/pricing.json 模型键（echo-test 豁免）', () => {
  const pricing = JSON.parse(readFileSync(join(srcRoot, '..', '..', 'deploy', 'pricing.json'), 'utf8'));
  const priced = new Set((pricing.channels || []).flatMap((ch) => Object.keys(ch.models || {})));
  for (const m of GUIDE_MODELS) {
    if (m.name === 'echo-test') continue;
    assert.ok(priced.has(m.name), `${m.name} 不在 deploy/pricing.json 任何渠道的 models 里`);
  }
});
