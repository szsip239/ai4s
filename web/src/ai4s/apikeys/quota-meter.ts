/**
 * 管理端 Key 管理「档位消耗」仪表的纯函数层——与渲染分离，便于 node --test 直测。
 * 数据口径同用户侧 my-keys（key-usage.ts）：用量/上限均来自上游 apiKeyQuotaUsages 聚合。
 */

/** 用量占比（0-100+，超 100 表示已超额）；上限为空或 ≤0 = 不设限，返回 null */
export function usagePct(used: number, limit: number | null | undefined): number | null {
  if (limit == null || limit <= 0) return null;
  return (used / limit) * 100;
}

export type MeterTone = 'normal' | 'warning' | 'over';

/** 进度条色调：<80 主色；80-99 琥珀（接近上限）；≥100 红（已超额）。不设限（null）归 normal */
export function meterTone(pct: number | null): MeterTone {
  if (pct == null) return 'normal';
  if (pct >= 100) return 'over';
  if (pct >= 80) return 'warning';
  return 'normal';
}

/** 百分比文本：0<pct<1 显示 <1%（避免「0%」掩盖已有消耗），其余四舍五入取整 */
export function formatPct(pct: number): string {
  if (pct > 0 && pct < 1) return '<1%';
  return `${Math.round(pct)}%`;
}
