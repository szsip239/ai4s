/**
 * 批量配额换档纯逻辑（issue #64）：命中筛选与模板→profile 入参转换。
 * 换档语义复用 issue #19 审批路径（shim/alert_poller.py apply_tier）：档位是 key 创建时
 * 从模板拷贝的快照而非引用，改模板不回溯存量 key——本模块服务存量调档。
 * profile 入参只带 name+quota：限额档=配额模板，不含渠道/模型约束（与 alert_poller 一致）。
 */

/** 参与换档的 key 最小形状（GraphQL 返回子集） */
export interface BatchTierKey {
  id: string;
  name: string;
  projectID: string;
  userID: string;
  profiles?: { activeProfile?: string | null } | null;
}

/** 限额档模板最小形状（apiKeyProfileTemplates 返回子集） */
export interface BatchTierTemplate {
  id: string;
  name: string;
  profile?: {
    quota?: {
      requests?: number | null;
      totalTokens?: number | null;
      cost?: number | string | null;
      period?: {
        type?: string | null;
        pastDuration?: { value: number; unit: string } | null;
        calendarDuration?: { unit: string } | null;
      } | null;
    } | null;
  } | null;
}

export interface BatchTierFilter {
  projectId?: string;
  userId?: string;
  /** 当前档名；NO_PROFILE 哨兵代表「未设档」（activeProfile 为空） */
  activeProfile?: string;
}

/** 「未设档」哨兵：空串是合法 activeProfile，不便直接做下拉 option value */
export const NO_PROFILE = '__no_profile__';

/** 筛中目标 key 集合（项目/员工维度服务端 where 已收窄时客户端再过滤亦幂等） */
export function filterBatchTierKeys(keys: BatchTierKey[], filter: BatchTierFilter): BatchTierKey[] {
  return keys.filter((k) => {
    if (filter.projectId && k.projectID !== filter.projectId) return false;
    if (filter.userId && k.userID !== filter.userId) return false;
    if (filter.activeProfile) {
      const current = k.profiles?.activeProfile || '';
      const want = filter.activeProfile === NO_PROFILE ? '' : filter.activeProfile;
      if (current !== want) return false;
    }
    return true;
  });
}

/** 从 key 集合收集出现过的当前档名（未设档归并为哨兵值），供筛选下拉 */
export function collectActiveProfiles(keys: BatchTierKey[]): string[] {
  const seen = new Set<string>();
  for (const k of keys) {
    seen.add(k.profiles?.activeProfile || NO_PROFILE);
  }
  return [...seen].sort((a, b) => a.localeCompare(b, 'zh-CN'));
}

/**
 * 模板 → UpdateAPIKeyProfilesInput.profiles 的单条入参。
 * 对齐 alert_poller.apply_tier：quota.cost 转字符串（GraphQL DecimalInput）；
 * period 缺省补 calendar_duration/month；模板用 past_duration 时带上 pastDuration。
 */
export function templateToProfileInput(template: BatchTierTemplate) {
  const quota = template.profile?.quota;
  if (!quota) {
    return { name: template.name, quota: null };
  }
  const period = quota.period;
  return {
    name: template.name,
    quota: {
      requests: quota.requests ?? null,
      totalTokens: quota.totalTokens ?? null,
      cost: quota.cost != null ? String(quota.cost) : null,
      period: {
        type: period?.type ?? 'calendar_duration',
        calendarDuration: period?.calendarDuration ?? { unit: 'month' },
        ...(period?.pastDuration ? { pastDuration: period.pastDuration } : {}),
      },
    },
  };
}
