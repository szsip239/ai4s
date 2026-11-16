import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

process.env.TZ = 'Asia/Shanghai';

const srcRoot = join(import.meta.dirname, '..', '..');
const componentsDir = join(srcRoot, 'features/analytics/components');
const source = readFileSync(join(componentsDir, 'analytics-filter-bar.tsx'), 'utf8');

function extractFunction(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} not found`);
  const bodyStart = source.indexOf('{', start);
  let depth = 0;
  for (let i = bodyStart; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`${name} is unterminated`);
}

const tempDir = mkdtempSync(join(tmpdir(), 'ai4s-analytics-range-'));
const modulePath = join(tempDir, 'range.mjs');
const helpers = ['formatDate', 'formatTimestamp', 'parseDate'].map(extractFunction).join('\n');
const transpiled = ts.transpileModule(`${helpers}\nexport { formatDate, formatTimestamp, parseDate };`, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
writeFileSync(modulePath, transpiled.outputText);
const { formatDate, formatTimestamp, parseDate } = await import(modulePath);

test('start and end dates preserve the inclusive local-day contract as RFC3339', () => {
  const day = new Date(2026, 10, 5, 12);
  assert.equal(formatTimestamp(day), '2026-11-04T16:00:00.000Z');
  assert.equal(formatTimestamp(day, true), '2026-11-05T15:59:59.999Z');
});

test('calendar labels remain local dates after timestamps are stored', () => {
  assert.equal(formatDate(parseDate('2026-11-04T16:00:00.000Z')), '2026-11-05');
  assert.equal(formatDate(parseDate('2026-11-05T15:59:59.999Z')), '2026-11-05');
});

test('metadata and page prop use the beta12 earliestTime field', () => {
  const data = readFileSync(join(srcRoot, 'features/analytics/data/analytics.ts'), 'utf8');
  const page = readFileSync(join(srcRoot, 'features/analytics/index.tsx'), 'utf8');
  assert.doesNotMatch(data + source + page, /earliestDate/);
  assert.match(data, /analyticsMetadata\s*\{\s*earliestTime\s*\}/);
  assert.match(page, /AnalyticsFilterBar earliestTime=\{metadata\?\.earliestTime\}/);
});

test.after(() => rmSync(tempDir, { recursive: true, force: true }));
