/**
 * 批量配额换档纯逻辑（issue #64）：命中筛选与模板→profile 入参转换。
 * 换档语义复用 issue #19 审批路径（shim/alert_poller.py apply_tier）：档位是 key 创建时
 * 从模板拷贝的快照而非引用，改模板不回溯存量 key——本模块服务存量调档。
 * issue #138：profile 入参全字段透传（channelIDs/channelTags/channelTagsMatchMode/modelIDs/
 * loadBalanceStrategy/modelMappings 一个不落）——体验档等带渠道/模型约束，updateAPIKeyProfiles
 * 全量替换语义下丢字段=换档静默丢限制；形状与兜底细节照抄 shim alert_poller.load_tier_profile。
 */

/** 参与换档的 key 最小形状（GraphQL 返回子集） */
export interface BatchTierKey {
  id: string;
  name: string;
  projectID: string;
  userID: string;
  profiles?: { activeProfile?: string | null } | null;
}

/** 限额档模板形状（apiKeyProfileTemplates 返回子集；profile 全字段，对齐 shim PROFILE_TEMPLATES_QUERY） */
export interface BatchTierTemplate {
  id: string;
  name: string;
  profile?: {
    modelMappings?: { from: string; to: string }[] | null;
    channelIDs?: number[] | null;
    channelTags?: string[] | null;
    channelTagsMatchMode?: string | null;
    modelIDs?: string[] | null;
    loadBalanceStrategy?: string | null;
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
 * 模板 → UpdateAPIKeyProfilesInput.profiles 的单条入参（issue #138 起与 shim
 * alert_poller.load_tier_profile 同形状同兜底）：
 * - modelMappings 空兜底 []（落库 null 会让前端 zod 必填数组解析崩，shim issue #81 教训）；
 * - channelTagsMatchMode 空/空串兜底 'any'（对齐前端 zod 缺省语义，shim issue #83）；
 * - quota.cost 转字符串（GraphQL DecimalInput）；period.type 缺省 calendar_duration；
 * - calendarDuration 缺省兜底 {unit:'month'} 只对 calendar 类模板生效，past_duration 不臆造 calendar 值；
 * - 模板无 quota 时仍落全空 quota 骨架（shim 同款，服务端已验证接受）。
 */
export function templateToProfileInput(template: BatchTierTemplate) {
  const prof = template.profile ?? {};
  const quota = prof.quota ?? {};
  const period = quota.period ?? {};
  const periodType = period.type ?? 'calendar_duration';
  const calendarDuration = period.calendarDuration ?? (periodType === 'calendar_duration' ? { unit: 'month' } : null);
  return {
    name: template.name,
    modelMappings: prof.modelMappings ?? [],
    channelIDs: prof.channelIDs ?? null,
    channelTags: prof.channelTags ?? null,
    channelTagsMatchMode: prof.channelTagsMatchMode || 'any',
    modelIDs: prof.modelIDs ?? null,
    loadBalanceStrategy: prof.loadBalanceStrategy ?? null,
    quota: {
      requests: quota.requests ?? null,
      totalTokens: quota.totalTokens ?? null,
      cost: quota.cost != null ? String(quota.cost) : null,
      period: {
        type: periodType,
        pastDuration: period.pastDuration ?? null,
        calendarDuration,
      },
    },
  };
}
