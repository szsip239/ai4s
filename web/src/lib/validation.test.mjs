import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// 与 graphql.test.mjs 同款：TS 源 transpile + data URL 导入；zod 为真实依赖，改写为 file URL 引入
const srcRoot = join(import.meta.dirname, '..');

const source = readFileSync(join(srcRoot, 'lib/validation.ts'), 'utf8');
const transpiled = ts
  .transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2023 },
  })
  .outputText.replaceAll("from 'zod'", `from 'file://${join(srcRoot, '..', 'node_modules', 'zod', 'index.js')}'`);
const mod = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString('base64')}`);

const { passwordValidation, passwordSchema, passwordConfirmationSchema } = mod;
const t = (key) => key;

test('共享 passwordSchema 最小长度为 8（issue #139：注册/登录全站统一）', () => {
  assert.equal(passwordValidation.minLength, 8);
  const schema = passwordSchema(t);
  assert.equal(schema.safeParse('').success, false); // 必填
  assert.equal(schema.safeParse('1234567').success, false); // 7 位拒绝（旧 sign-up min(7) 已并轨）
  assert.equal(schema.safeParse('12345678').success, true); // 8 位放行（pattern 校验刻意未启用，见源码注释）
});

test('passwordSchema 报错文案走 i18n 键', () => {
  const schema = passwordSchema(t);
  const empty = schema.safeParse('');
  assert.equal(empty.error.issues[0].message, 'auth.signIn.validation.passwordRequired');
  const short = schema.safeParse('1234567');
  assert.equal(short.error.issues[0].message, 'auth.signIn.validation.passwordMinLength');
});

test('passwordConfirmationSchema 密码不一致报 confirmPassword 路径', () => {
  const schema = passwordConfirmationSchema(t);
  assert.equal(schema.safeParse({ password: '12345678', confirmPassword: '12345678' }).success, true);
  const mismatch = schema.safeParse({ password: '12345678', confirmPassword: '87654321' });
  assert.equal(mismatch.success, false);
  assert.equal(mismatch.error.issues[0].path[0], 'confirmPassword');
});

test('issue #139：sign-up 表单接线共享 passwordSchema，不再私用 min(7)', () => {
  const signUpSource = readFileSync(join(srcRoot, 'features/auth/sign-up/components/sign-up-form.tsx'), 'utf8');
  assert.match(signUpSource, /passwordSchema\(t\)/);
  assert.ok(!/\.min\(7\)/.test(signUpSource), 'sign-up-form 不得再出现 min(7)');
});
