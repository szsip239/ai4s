/**
 * 档位秩次（issue #85）：体验档 < 标准档 < 高档。
 * 与 shim alert_poller.TIER_RANK 双向同源，改动需两侧同步。
 * 纯函数层：「我的 Key」页提额按钮门态与弹窗选项过滤由这里推导，组件只做接线。
 */

/** 三档按秩次升序 */
export const TIER_ORDER = ['体验档', '标准档', '高档'] as const;
export type TierName = (typeof TIER_ORDER)[number];

/** 档名 → 秩次；未挂档/未知档 → -1 */
export function tierRank(name?: string | null): number {
  return (TIER_ORDER as readonly string[]).indexOf(name ?? '');
}

type KeyLike = { status: string; profiles?: { activeProfile?: string | null } | null };

/** 当前最高档：只数 enabled key 的 activeProfile；无 enabled / 均未挂档（或档名未知）→ null */
export function currentHighestTier(keys: KeyLike[]): TierName | null {
  let best: TierName | null = null;
  for (const k of keys) {
    if (k.status !== 'enabled') continue;
    const name = k.profiles?.activeProfile;
    if (name && tierRank(name) > tierRank(best)) best = name as TierName;
  }
  return best;
}

/**
 * 「申请提额」按钮门态（issue #85）：
 * 'no-enabled-key' = 无 enabled key（含全 disabled），提示先新建；
 * 'maxed' = 已是最高档；null = 可发起提额。
 */
export function upgradeButtonBlock(keys: KeyLike[]): 'no-enabled-key' | 'maxed' | null {
  if (!keys.some((k) => k.status === 'enabled')) return 'no-enabled-key';
  if (currentHighestTier(keys) === '高档') return 'maxed';
  return null;
}

/**
 * 提额弹窗可选档：秩次 > 当前档。shim 白名单 TIERS 只含标准/高档，体验档恒不在列——
 * 当前档为 null（未挂档/无 key）时列标准+高档；shim 端无 enabled key 会 400 拦，双保险。
 */
export function upgradeOptions(current: TierName | null): TierName[] {
  return TIER_ORDER.filter((t) => t !== '体验档' && tierRank(t) > tierRank(current));
}
