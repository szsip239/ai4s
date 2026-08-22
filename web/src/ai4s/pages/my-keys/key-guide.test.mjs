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

// ---- issue #84：指南档位数字守卫——locale 写死的档位数值（指南 guide.tiers.* + 申请弹窗
// dialog.tierStandard/tierPremium）必须与契约矩阵一致（zh/en 双侧）。
// 与 docs/contracts/quota-tiers.md 矩阵表格格式耦合（`| 名称（key） | N 点 | N 亿 | …` 行结构）——
// 可接受，对齐上方 pricing 守卫惯例：矩阵改数值或改表格格式时此测试先红，逼着同步。----

const repoRoot = join(srcRoot, '..', '..');

/** zh「1.5 亿 / 5000 万」→ 原始数 */
function zhTokenNum(s) {
  const m = s.match(/([\d.]+)\s*(亿|万)\s*Token/);
  assert.ok(m, `zh token 段解析失败: ${s}`);
  return Number(m[1]) * (m[2] === '亿' ? 1e8 : 1e4);
}

/** en「150M / 3B / 750M」→ 原始数 */
function enTokenNum(s) {
  const m = s.match(/([\d.]+)\s*([KMB])\s*tokens/i);
  assert.ok(m, `en token 段解析失败: ${s}`);
  const mult = { K: 1e3, M: 1e6, B: 1e9 }[m[2].toUpperCase()];
  return Number(m[1]) * mult;
}

/** 契约矩阵行（| 体验档（trial） | 100 点 | 1.5 亿 | … |）→ {cost, tokens} */
function contractRow(contract, tierName) {
  const line = contract.split('\n').find((l) => l.startsWith(`| ${tierName}（`));
  assert.ok(line, `契约矩阵缺 ${tierName} 行`);
  const cells = line.split('|').map((c) => c.trim());
  const cost = Number(cells[2].match(/([\d.]+)\s*点/)?.[1]);
  const tokens = zhTokenNum(`${cells[3]} Token`); // 复用 zh 解析（补 Token 后缀）
  assert.ok(cost > 0 && tokens > 0, `契约行解析失败: ${line}`);
  return { cost, tokens };
}

test('指南档位数字 = 契约矩阵（zh/en 双侧，issue #84 守卫）', () => {
  const contract = readFileSync(join(repoRoot, 'docs', 'contracts', 'quota-tiers.md'), 'utf8');
  const zh = JSON.parse(readFileSync(join(srcRoot, 'ai4s', 'locales', 'zh-CN', 'ai4s-patch.json'), 'utf8'));
  const en = JSON.parse(readFileSync(join(srcRoot, 'ai4s', 'locales', 'en', 'ai4s-patch.json'), 'utf8'));
  for (const [key, tierName] of [
    ['trial', '体验档'],
    ['standard', '标准档'],
    ['premium', '高档'],
  ]) {
    const want = contractRow(contract, tierName);
    const zhLine = zh[`ai4s.myKeys.guide.tiers.${key}`];
    const enLine = en[`ai4s.myKeys.guide.tiers.${key}`];
    assert.ok(zhLine && enLine, `locale 缺 guide.tiers.${key}`);
    const zhCost = Number(zhLine.match(/([\d.]+)\s*点/)?.[1]);
    const enCost = Number(enLine.match(/([\d.]+)\s*credits/i)?.[1]);
    assert.equal(zhCost, want.cost, `zh ${tierName} cost`);
    assert.equal(enCost, want.cost, `en ${tierName} cost`);
    assert.equal(zhTokenNum(zhLine), want.tokens, `zh ${tierName} tokens`);
    assert.equal(enTokenNum(enLine), want.tokens, `en ${tierName} tokens`);
    // 申请弹窗档位标签（仅 standard/premium）只写 cost，同矩阵比对
    const dialogKey = { standard: 'dialog.tierStandard', premium: 'dialog.tierPremium' }[key];
    if (dialogKey) {
      const zhDialog = zh[`ai4s.myKeys.${dialogKey}`];
      const enDialog = en[`ai4s.myKeys.${dialogKey}`];
      assert.ok(zhDialog && enDialog, `locale 缺 ${dialogKey}`);
      assert.equal(Number(zhDialog.match(/([\d.]+)\s*点/)?.[1]), want.cost, `zh ${dialogKey} cost`);
      assert.equal(Number(enDialog.match(/([\d.]+)\s*credits/i)?.[1]), want.cost, `en ${dialogKey} cost`);
    }
  }
});
