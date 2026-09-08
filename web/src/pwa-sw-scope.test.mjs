import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

// PWA SW 作用域对账（issue #139）：API 前缀必须同时进 navigateFallbackDenylist 与 runtimeCaching
// NetworkOnly，否则 SW 会缓存/接管管理面与绕行入口请求。直接读 vite.config.ts 提取正则字面量求值。
const viteConfig = readFileSync(join(import.meta.dirname, '..', 'vite.config.ts'), 'utf8');

const denylistMatch = viteConfig.match(/navigateFallbackDenylist:\s*\[(\/\^[^\]]+\/)\]/);
const networkOnlyMatch = viteConfig.match(/urlPattern:\s*(\/.*\/),\s*\n\s*handler:\s*'NetworkOnly'/);

assert.ok(denylistMatch, 'vite.config.ts 缺少 navigateFallbackDenylist 配置');
assert.ok(networkOnlyMatch, 'vite.config.ts 缺少 runtimeCaching NetworkOnly 配置');

// eslint 配置外的测试文件，对仓库自有配置求值是刻意的
const denylist = eval(denylistMatch[1]);
const networkOnly = eval(networkOnlyMatch[1]);

const API_PREFIXES = ['/admin/', '/v1/', '/bv1/', '/self/', '/dlp-admin/', '/oauth/'];

test('issue #139：/bv1（Key 全量绕行入口）与其余 API 前缀均不入 SW 导航回退', () => {
  for (const prefix of API_PREFIXES) {
    assert.ok(denylist.test(`${prefix}chat/completions`), `${prefix} 应在 navigateFallbackDenylist`);
  }
  assert.ok(!denylist.test('/index.html'));
  assert.ok(!denylist.test('/settings/profile'));
});

test('issue #139：/bv1 与其余 API 前缀运行时缓存策略为 NetworkOnly', () => {
  for (const prefix of API_PREFIXES) {
    assert.ok(networkOnly.test(`https://ai4s.example.cn${prefix}models`), `${prefix} 应走 NetworkOnly`);
  }
  assert.ok(!networkOnly.test('https://ai4s.example.cn/assets/index.js'));
});
