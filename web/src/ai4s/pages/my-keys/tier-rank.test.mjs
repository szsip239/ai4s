import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 key-usage.test.mjs 同款：TS 源 transpile 后经 data URL 导入
const srcRoot = join(import.meta.dirname, '..', '..', '..');

const source = readFileSync(join(srcRoot, 'ai4s/pages/my-keys/tier-rank.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { TIER_ORDER, tierRank, currentHighestTier, upgradeButtonBlock, upgradeOptions } = mod;

const key = (status, tier) => ({ status, profiles: { activeProfile: tier } });

test('tierRank: 体验<标准<高，未挂档/未知档 -1（与 shim TIER_RANK 同源）', () => {
  assert.equal(tierRank('体验档'), 0);
  assert.equal(tierRank('标准档'), 1);
  assert.equal(tierRank('高档'), 2);
  assert.ok(tierRank('体验档') < tierRank('标准档') && tierRank('标准档') < tierRank('高档'));
  assert.equal(tierRank(null), -1);
  assert.equal(tierRank(undefined), -1);
  assert.equal(tierRank('无敌档'), -1);
  assert.deepEqual([...TIER_ORDER], ['体验档', '标准档', '高档']);
});

test('currentHighestTier: 只数 enabled，取秩次最高档；disabled/archived 不参与', () => {
  assert.equal(currentHighestTier([key('enabled', '体验档'), key('enabled', '高档')]), '高档');
  assert.equal(currentHighestTier([key('disabled', '高档'), key('enabled', '标准档')]), '标准档');
  assert.equal(currentHighestTier([key('archived', '高档')]), null); // 归档 key 保留旧快照但不计当前档
  assert.equal(currentHighestTier([]), null);
  assert.equal(currentHighestTier([key('enabled', null)]), null); // 未挂档
  assert.equal(currentHighestTier([key('enabled', '无敌档')]), null); // 未知档不参与秩次
});

test('upgradeButtonBlock: 无 enabled key（含全 disabled/空列表）→ no-enabled-key；全高档 → maxed', () => {
  assert.equal(upgradeButtonBlock([]), 'no-enabled-key');
  assert.equal(upgradeButtonBlock([key('disabled', '高档')]), 'no-enabled-key');
  assert.equal(upgradeButtonBlock([key('enabled', '高档'), key('enabled', '体验档')]), 'maxed');
  assert.equal(upgradeButtonBlock([key('enabled', '标准档')]), null);
  assert.equal(upgradeButtonBlock([key('enabled', '体验档')]), null);
  assert.equal(upgradeButtonBlock([key('enabled', null)]), null); // 未挂档可发起（shim 端再校验）
});

test('upgradeOptions: 只列秩次 > 当前档；体验档恒不在列（对齐 shim TIERS 白名单）', () => {
  assert.deepEqual(upgradeOptions('标准档'), ['高档']); // 标准档用户只见高档
  assert.deepEqual(upgradeOptions('体验档'), ['标准档', '高档']);
  assert.deepEqual(upgradeOptions(null), ['标准档', '高档']); // 未挂档同体验档视野
  assert.deepEqual(upgradeOptions('高档'), []); // 最高档无可选项（按钮已禁用，双保险）
  assert.ok(!upgradeOptions(null).includes('体验档'));
});
