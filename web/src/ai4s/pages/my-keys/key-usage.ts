/**
 * 「我的 Key」页用量展示的纯函数层（issue #83）——与渲染分离，便于 node --test 直测。
 * 数据来自 shim /self/keys 内嵌的 usage（apiKeyQuotaUsages 代查透传，与管理员侧 profiles
 * 对话框同一上游聚合）。数值口径与 #82 配置指南一致：zh 用 万/亿，en 用 K/M；credit 显示
 * 「点/credits」，最多两位小数。
 */

export interface UsagePeriod {
  type?: string;
  pastDuration?: { value?: number; unit?: string } | null;
  calendarDuration?: { unit?: string } | null;
}

export interface UsageEntry {
  profileName: string;
  quota?: {
    requests?: number | null;
    totalTokens?: number | null;
    cost?: number | string | null;
    period?: UsagePeriod | null;
  } | null;
  window?: { start?: string | null; end?: string | null } | null;
  usage?: { requestCount?: number; totalTokens?: number; totalCost?: number | string } | null;
}

/** token 计数本地化：zh ≥1亿→x.x亿 / ≥1万→x.x万；en ≥1B→x.xB / ≥1M→x.xM / ≥1K→x.xK（与 #82 指南口径一致） */
export function formatTokenCount(n: number, zh: boolean): string {
  const trim = (v: number) => `${Math.round(v * 10) / 10}`;
  if (zh) {
    if (n >= 1e8) return `${trim(n / 1e8)}亿`;
    if (n >= 1e4) return `${trim(n / 1e4)}万`;
    return `${n}`;
  }
  if (n >= 1e9) return `${trim(n / 1e9)}B`; // issue #84：高档 30 亿 → 3B（避免 3000M）
  if (n >= 1e6) return `${trim(n / 1e6)}M`;
  if (n >= 1e3) return `${trim(n / 1e3)}K`;
  return `${n}`;
}

/** credit 点数：最多两位小数（上游 cost 为 decimal，usage 累积常有浮点尾巴） */
export function formatCredits(cost: number): string {
  return `${Math.round(cost * 100) / 100}`;
}

export type QuotaKind = 'cost' | 'totalTokens' | 'requests';

export interface QuotaProgress {
  kind: QuotaKind;
  used: number;
  total: number;
  /** 0-100+（超 100 表示已超额，展示层截断进度条即可） */
  pct: number;
}

/**
 * 取进度条主控维度：cost（点）→ totalTokens → requests，取配额非空的第一个
 * （与 quota-tiers.md「credit 帽主控」口径一致）。全空 = 不设限，返回 null。
 */
export function quotaProgress(entry: UsageEntry): QuotaProgress | null {
  const q = entry.quota ?? {};
  const u = entry.usage ?? {};
  if (q.cost != null) {
    const used = Number(u.totalCost ?? 0);
    const total = Number(q.cost);
    return { kind: 'cost', used, total, pct: total > 0 ? (used / total) * 100 : 0 };
  }
  if (q.totalTokens != null) {
    const used = Number(u.totalTokens ?? 0);
    const total = Number(q.totalTokens);
    return { kind: 'totalTokens', used, total, pct: total > 0 ? (used / total) * 100 : 0 };
  }
  if (q.requests != null) {
    const used = Number(u.requestCount ?? 0);
    const total = Number(q.requests);
    return { kind: 'requests', used, total, pct: total > 0 ? (used / total) * 100 : 0 };
  }
  return null;
}

/** 当前生效档的用量条目：按 activeProfile 名匹配；无档位/无条目返回 undefined */
export function activeUsageEntry(
  entries: UsageEntry[] | null | undefined,
  activeProfile: string | null | undefined
): UsageEntry | undefined {
  if (!entries || !activeProfile) return undefined;
  return entries.find((e) => e.profileName === activeProfile);
}
