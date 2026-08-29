/**
 * 智能路由决策链阶段定义（页面管线图 + 左侧标签导航同构共用，先例见 rules/layers.ts）。
 * key 即面板选中态（Ai4sSmartRoutingPage 的 selected），管线点击与导航选中联动同一 state。
 * 阶段顺序 = shim route_resolve 决策流真实顺序（issue #117/#119）：
 *   会话检查（继承/锁）→ 复杂度分类（judge 通道 LLM 打 p_complex）→ 档位判定（阈值/升档）→ 模型改写。
 */
import type { DlpSettings } from '../rules/api';

export type StageKey = 'session' | 'classify' | 'decision' | 'tiers';
export type SmartRoutingNavKey = StageKey | 'log';

export interface StageDef {
  key: StageKey;
  /** i18n 键（页面经 t() 解出标签；zh/en 在 ai4s-patch.json） */
  labelKey: string;
}

export const ROUTER_STAGES: readonly StageDef[] = [
  { key: 'session', labelKey: 'ai4s.smartRouting.nav.session' },
  { key: 'classify', labelKey: 'ai4s.smartRouting.nav.classify' },
  { key: 'decision', labelKey: 'ai4s.smartRouting.nav.decision' },
  { key: 'tiers', labelKey: 'ai4s.smartRouting.nav.tiers' },
];

/** 导航附加项：决策日志（只读；不放首页直出，作为标签页） */
export const ROUTER_EXTRA_NAV = [{ key: 'log', labelKey: 'ai4s.smartRouting.nav.log' }] as const;

/** routing 启用态三值（先例：rules 页 cfgEnabled——查询失败/文档缺席不臆造，显示「未知」）：
 * isError 或 doc 缺席 → null（未知）；routing 节缺席 → false（shim #117：缺席=关态合法） */
export function routingEnabledState(doc: Pick<DlpSettings, 'routing'> | null | undefined, isError: boolean): boolean | null {
  if (isError || !doc) return null;
  return doc.routing?.enabled ?? false;
}
