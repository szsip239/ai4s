/**
 * 检测链层定义（issue #36；#39 合并 L1/L1.5 为单节点）：顶部管线节点与左侧导航同构共用。
 * key 即面板选中态（Ai4sRulesPage 的 selected），管线点击与导航选中联动同一 state。
 */

export type PanelKey = 'l1' | 'l2' | 'l3' | 'judge' | 'rules' | 'pg' | 'response' | 'toggles' | 'deep';

export interface LayerDef {
  key: PanelKey;
  label: string;
}

/** 管线节点（请求侧 5 节点 + 响应侧），顺序即检测链评估顺序。
 * L1/L1.5 同一 format-rules.json、同一 FormatRulesPanel（action 列区分 reject/mask），#39 起合并为「L1 格式规则」；
 * issue #104 起「注入规则」节点与「注入 PG」相邻（规则层在 PG 段之前评估——确定性模式命中先于模型打分） */
export const PIPELINE_LAYERS: LayerDef[] = [
  { key: 'l1', label: 'L1 格式规则' },
  { key: 'l2', label: 'L2 词表/PII' },
  { key: 'l3', label: 'L3 EDM' },
  { key: 'judge', label: '语义 judge' },
  { key: 'rules', label: '注入规则' },
  { key: 'pg', label: '注入 PG' },
  { key: 'response', label: '响应侧' },
];

/** 导航附加项：白名单 Key（原「开关与阈值」tab 收口——分层开关归管线节点、阈值归 per-layer 面板，
 *  整体视图 SettingsPanel 删除后本 tab 只挂白名单 Key 面板）、纵深层（只读；issue #38 起为普通选中项） */
export const EXTRA_NAV: LayerDef[] = [
  { key: 'toggles', label: '白名单 Key' },
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
