import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 page-tab-groups.test.mjs 同款：TS 源 transpile 后经 data URL 导入（route-permission 无运行时依赖）
const srcRoot = join(import.meta.dirname, '..');

const source = readFileSync(join(srcRoot, 'config/route-permission.ts'), 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
}).outputText;
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { routeConfigs, getRouteConfig, hasRouteAccess, resolveLandingPath } = mod;

/** 收集 routes/ 下所有文件路由对应的 URL 路径（TanStack 文件路由约定，够 routeConfigs 对账用） */
function collectRoutePaths() {
  const routesDir = join(srcRoot, 'routes');
  const files = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (entry.endsWith('.tsx')) files.push(full.slice(routesDir.length + 1));
    }
  };
  walk(routesDir);
  return new Set(
    files.map((rel) => {
      const segs = rel.replace(/\.tsx$/, '').split('/');
      const out = [];
      for (const seg of segs) {
        if (seg.startsWith('(') && seg.endsWith(')')) continue; // 路径无关分组 (auth)/(errors)
        if (seg.startsWith('_')) continue; // 布局前缀 _authenticated
        if (seg === 'index' || seg === 'route') continue;
        out.push(seg);
      }
      return '/' + out.join('/');
    })
  );
}

const routePaths = collectRoutePaths();
const allConfigPaths = routeConfigs.flatMap((g) => g.routes.map((r) => [g.key, r]));

test('issue #139：已下线的路由不再留在 routeConfigs（/api-keys、/permission-demo、死配置 /project/usage-logs）', () => {
  const paths = allConfigPaths.map(([, r]) => r.path);
  for (const removed of ['/api-keys', '/permission-demo', '/project/usage-logs']) {
    assert.ok(!paths.includes(removed), `${removed} 不应再登记在 routeConfigs`);
  }
});

test('issue #139：routeConfigs 每个条目都有真实路由文件（死配置防回归）', () => {
  for (const [groupKey, route] of allConfigPaths) {
    assert.ok(routePaths.has(route.path), `routeConfigs[${groupKey}] ${route.path} 无对应路由文件`);
  }
});

test('issue #139：/project/blocks 登记为 system 级 read_channels + hidden（与路由 RouteGuard 对齐）', () => {
  const cfg = getRouteConfig('/project/blocks');
  assert.ok(cfg, '/project/blocks 须登记');
  assert.deepEqual(cfg.requiredScopes, ['read_channels']);
  assert.equal(cfg.scopeLevel, 'system');
  assert.equal(cfg.mode, 'hidden');
  // 无权限用户不可见（hidden 由 Ai4sPageTabs/filterNavItems 消费）；持 system read_channels 可见
  assert.equal(hasRouteAccess([], cfg), false);
  assert.equal(hasRouteAccess(['read_channels'], cfg), true);
});

test('登录落点解析不受本批改动影响：owner 落 /，零 scope 员工落 /project/my-keys', () => {
  assert.equal(resolveLandingPath({ isOwner: true }), '/');
  assert.equal(resolveLandingPath({}), '/project/my-keys');
  assert.equal(resolveLandingPath({ systemScopes: ['read_requests'] }), '/project/requests');
});
