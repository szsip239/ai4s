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

/**
 * 「申请提额」按钮门态（issue #85）：
 * 'no-enabled-key' = 无 enabled key（含全 disabled），提示先新建；
 * 'maxed' = 全部 enabled key 均已是最高档；null = 可发起提额。
 * issue #86 评审 P1-1：maxed 从「最高档=高档」收窄为「全部 enabled 均为高档」——
 * 混档用户（1 把高档+1 把体验档）必须能开弹窗只勾低档 key 提档（#86 核心场景）；
 * 弹窗内 keysMaxed 空态兜底不变。
 */
export function upgradeButtonBlock(keys: KeyLike[]): 'no-enabled-key' | 'maxed' | null {
  const enabled = keys.filter((k) => k.status === 'enabled');
  if (enabled.length === 0) return 'no-enabled-key';
  if (enabled.every((k) => k.profiles?.activeProfile === '高档')) return 'maxed';
  return null;
}

/**
 * 提额弹窗可选档（issue #86）：按所选 Key 的最低档（floor）过滤，只列秩次 > floor 的档。
 * shim 白名单 TIERS 只含标准/高档，体验档恒不在列——
 * 所选均未挂档（floor=-1）或空选时列标准+高档；全高档时空列（shim 端同样 400 拦，双保险）。
 */
export function upgradeOptions(keys: KeyLike[]): TierName[] {
  const floor = keys.length ? Math.min(...keys.map((k) => tierRank(k.profiles?.activeProfile))) : -1;
  return TIER_ORDER.filter((t) => t !== '体验档' && tierRank(t) > floor);
}
