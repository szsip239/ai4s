/**
 * 检测链层定义（issue #36）：顶部管线节点与左侧导航同构共用。
 * key 即面板选中态（Ai4sRulesPage 的 selected），管线点击与导航选中联动同一 state。
 */

export type PanelKey = 'l1' | 'l15' | 'l2' | 'l3' | 'judge' | 'pg' | 'response' | 'toggles' | 'deep';

export interface LayerDef {
  key: PanelKey;
  label: string;
}

/** 管线节点（请求侧 6 层 + 响应侧），顺序即检测链评估顺序 */
export const PIPELINE_LAYERS: LayerDef[] = [
  { key: 'l1', label: 'L1 Secrets' },
  { key: 'l15', label: 'L1.5 PII 格式' },
  { key: 'l2', label: 'L2 词表/PII' },
  { key: 'l3', label: 'L3 EDM' },
  { key: 'judge', label: '语义 judge' },
  { key: 'pg', label: '注入 PG' },
  { key: 'response', label: '响应侧' },
];

/** 导航附加项：开关与阈值（judge/edm/pg 总配置面板）、纵深层（只读，锚点滚动到底部） */
export const EXTRA_NAV: LayerDef[] = [
  { key: 'toggles', label: '开关与阈值' },
  { key: 'deep', label: '纵深层规则' },
];

export const LAYER_LABEL: Record<PanelKey, string> = Object.fromEntries(
  [...PIPELINE_LAYERS, ...EXTRA_NAV].map((l) => [l.key, l.label])
) as Record<PanelKey, string>;

/** 徽标（管线节点与导航项共用）：label + shadcn Badge variant */
export interface StatusBadge {
  label: string;
  variant: 'default' | 'secondary' | 'outline' | 'destructive';
}
