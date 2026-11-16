import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');

test('catalog requests use the rc.4 on-demand API', () => {
  const settings = source('features/system/data/system.ts');
  const providers = source('features/models/data/providers.ts');
  assert.doesNotMatch(settings.match(/const CATALOG_SETTINGS_QUERY = `[\s\S]*?`;/)[0], /refreshSeconds/);
  assert.doesNotMatch(settings.match(/export interface UpdateCatalogSettingsInput \{[^}]*\}/)[0], /refreshSeconds/);
  assert.doesNotMatch(providers, /refreshProvidersCatalog/);
});

test('catalog pages do not offer removed server refresh controls', () => {
  const settings = source('features/system/components/catalog-settings.tsx');
  const status = source('features/models/components/models-catalog-status.tsx');
  assert.doesNotMatch(settings, /refreshSeconds|useRefreshProvidersCatalog/);
  assert.doesNotMatch(status, /useRefreshProvidersCatalog/);
});
