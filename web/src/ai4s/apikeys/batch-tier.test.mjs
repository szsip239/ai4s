import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 features/channels-override-merge.test.mjs 同款：TS 源 transpile 后经 data URL 导入
const srcRoot = join(import.meta.dirname, '..', '..');

const source = readFileSync(join(srcRoot, 'ai4s/apikeys/batch-tier.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { NO_PROFILE, filterBatchTierKeys, collectActiveProfiles, templateToProfileInput } = mod;

const KEYS = [
  { id: 'k1', name: 'a', projectID: 'p1', userID: 'u1', profiles: { activeProfile: '体验档' } },
  { id: 'k2', name: 'b', projectID: 'p1', userID: 'u2', profiles: { activeProfile: '标准档' } },
  { id: 'k3', name: 'c', projectID: 'p2', userID: 'u1', profiles: { activeProfile: '' } },
  { id: 'k4', name: 'd', projectID: 'p1', userID: 'u1', profiles: null },
];

test('filterBatchTierKeys: 无筛选条件全量命中', () => {
  assert.equal(filterBatchTierKeys(KEYS, {}).length, 4);
});

test('filterBatchTierKeys: 项目/员工维度收窄', () => {
  assert.deepEqual(filterBatchTierKeys(KEYS, { projectId: 'p2' }).map((k) => k.id), ['k3']);
  assert.deepEqual(filterBatchTierKeys(KEYS, { userId: 'u2' }).map((k) => k.id), ['k2']);
  assert.deepEqual(filterBatchTierKeys(KEYS, { projectId: 'p1', userId: 'u1' }).map((k) => k.id), ['k1', 'k4']);
});

test('filterBatchTierKeys: 当前档含未设档哨兵语义', () => {
  assert.deepEqual(filterBatchTierKeys(KEYS, { activeProfile: '体验档' }).map((k) => k.id), ['k1']);
  // NO_PROFILE 命中 activeProfile 为空与 profiles 为 null 两种形态
  assert.deepEqual(filterBatchTierKeys(KEYS, { activeProfile: NO_PROFILE }).map((k) => k.id), ['k3', 'k4']);
});

test('collectActiveProfiles: 去重含未设档', () => {
  const tiers = collectActiveProfiles(KEYS);
  assert.equal(tiers.length, 3);
  assert.ok(tiers.includes('体验档') && tiers.includes('标准档') && tiers.includes(NO_PROFILE));
});

test('templateToProfileInput: quota 拷贝 + cost 字符串化 + period 缺省补齐', () => {
  const out = templateToProfileInput({
    id: 't1',
    name: '高档',
    profile: { quota: { requests: 100000, totalTokens: null, cost: 500, period: { type: 'calendar_duration', calendarDuration: { unit: 'month' } } } },
  });
  assert.deepEqual(out, {
    name: '高档',
    quota: {
      requests: 100000,
      totalTokens: null,
      cost: '500',
      period: { type: 'calendar_duration', calendarDuration: { unit: 'month' } },
    },
  });
});

test('templateToProfileInput: past_duration 模板带 pastDuration；无 quota 退化为 null', () => {
  const withPast = templateToProfileInput({
    id: 't2',
    name: '计时档',
    profile: { quota: { requests: 60, period: { type: 'past_duration', pastDuration: { value: 1, unit: 'hour' } } } },
  });
  assert.equal(withPast.quota.period.type, 'past_duration');
  assert.deepEqual(withPast.quota.period.pastDuration, { value: 1, unit: 'hour' });

  const noQuota = templateToProfileInput({ id: 't3', name: '空档', profile: null });
  assert.deepEqual(noQuota, { name: '空档', quota: null });
});
