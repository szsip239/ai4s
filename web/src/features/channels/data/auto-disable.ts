import type { APIKeyAutoDisableRule } from './schema';

// beta11 vendored subset of upstream frontend/src/features/channels/data/auto-disable.ts:
// 仅保留系统设置重试页（retry-settings）与规则编辑器所需的表单值类型与序列化助手；
// 上游的 mode/bulk 相关助手依赖 b11 新增的渠道级 schema 类型（APIKeyAutoDisableMode、
// BulkUpdateChannelAutoDisableInput 等），本次适配未引入，待渠道功能整体升级时再补齐。

export type ApiKeyAutoDisableRuleFormValue = {
  statusCodes?: number[] | null;
  keywordPatterns?: string[] | null;
  times: number;
  action: APIKeyAutoDisableRule['action'];
  disableDurationMinutes?: number | null;
  disableUntilCron?: string | null;
  disableUntilTimezone?: string | null;
};

export const RECOMMENDED_GLOBAL_AUTO_DISABLE_RULES: APIKeyAutoDisableRule[] = [
  { statusCodes: [401], times: 3, action: 'permanent_disable' },
  { statusCodes: [429], times: 3, action: 'temporary_disable', disableDurationMinutes: 30 },
];

export function toApiKeyAutoDisableRuleFormValues(rules?: APIKeyAutoDisableRule[] | null): ApiKeyAutoDisableRuleFormValue[] {
  return (
    rules?.map((rule) => ({
      statusCodes: rule.statusCodes ?? [],
      keywordPatterns: rule.keywordPatterns ?? [],
      times: rule.times,
      action: rule.action,
      disableDurationMinutes: rule.action === 'temporary_disable' ? (rule.disableDurationMinutes ?? 30) : null,
      disableUntilCron: rule.action === 'disable_until_cron' ? (rule.disableUntilCron ?? '0 0 * * *') : null,
      disableUntilTimezone: rule.action === 'disable_until_cron' ? rule.disableUntilTimezone || 'UTC' : null,
    })) ?? []
  );
}

export function serializeApiKeyAutoDisableRules(rules: ApiKeyAutoDisableRuleFormValue[]): APIKeyAutoDisableRule[] {
  return rules.map((rule) => ({
    statusCodes: rule.statusCodes?.filter((code): code is number => code != null) ?? [],
    keywordPatterns: rule.keywordPatterns?.map((pattern) => pattern.trim()).filter(Boolean) ?? [],
    times: rule.times,
    action: rule.action,
    disableDurationMinutes: rule.action === 'temporary_disable' ? (rule.disableDurationMinutes ?? null) : null,
    disableUntilCron: rule.action === 'disable_until_cron' ? rule.disableUntilCron?.trim() || null : null,
    disableUntilTimezone: rule.action === 'disable_until_cron' ? rule.disableUntilTimezone?.trim() || null : null,
  }));
}
