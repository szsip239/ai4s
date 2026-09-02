/**
 * 分层总开关的读写纯函数（issue #133 方案 A：管线节点即唯一开关入口）。
 * 从 panels/LayerSwitch 的合并逻辑提纯为可测纯函数，并扩展到全部七个可控层：
 * l1/l2/响应侧为单键段（旧 settings.json 可能缺段，读侧缺省回退 true 与服务端语义对齐）；
 * l3=edm / judge / rules=注入规则 / pg 为多键段，翻转只改 enabled，其余键展开保留
 * （l2 展开保留 opf 子节，issue #127；judge/pg/rules 保留阈值等子键）。
 * 输出文档始终三段齐全（l1/l2/response 缺段补 {enabled:true}），避免 PUT 缺段被服务端 400。
 */
import type { DlpSettings } from './api';

export type ToggleableLayerKey = 'l1' | 'l2' | 'l3' | 'judge' | 'rules' | 'pg' | 'response';

export const TOGGLEABLE_LAYER_KEYS: readonly ToggleableLayerKey[] = ['l1', 'l2', 'l3', 'judge', 'rules', 'pg', 'response'];

/** 读侧：层开关真实态（缺段回退 true，与 shim 缺段默认语义对齐） */
export function layerEnabled(doc: DlpSettings, key: ToggleableLayerKey): boolean {
  switch (key) {
    case 'l1':
      return doc.l1?.enabled ?? true;
    case 'l2':
      return doc.l2?.enabled ?? true;
    case 'l3':
      return doc.edm.enabled;
    case 'judge':
      return doc.judge.enabled;
    case 'rules':
      return doc.rules.enabled;
    case 'pg':
      return doc.pg.enabled;
    case 'response':
      return doc.response?.enabled ?? true;
  }
}

/** 写侧：翻转目标层 enabled，其余段/子键原样保留；l1/l2/response 缺段按缺省 true 补齐 */
export function buildSettingsWithLayerEnabled(
  doc: DlpSettings,
  key: ToggleableLayerKey,
  next: boolean
): DlpSettings {
  const out: DlpSettings = {
    ...doc,
    l1: doc.l1 ?? { enabled: true },
    l2: doc.l2 ?? { enabled: true },
    response: doc.response ?? { enabled: true },
  };
  switch (key) {
    case 'l1':
      out.l1 = { ...out.l1, enabled: next };
      break;
    case 'l2':
      out.l2 = { ...out.l2, enabled: next };
      break;
    case 'l3':
      out.edm = { ...out.edm, enabled: next };
      break;
    case 'judge':
      out.judge = { ...out.judge, enabled: next };
      break;
    case 'rules':
      out.rules = { ...out.rules, enabled: next };
      break;
    case 'pg':
      out.pg = { ...out.pg, enabled: next };
      break;
    case 'response':
      out.response = { ...out.response, enabled: next };
      break;
  }
  return out;
}

/** 响应侧联动（issue #40「模块关=处处关」语义）：l1/l2 关闭时响应侧对应检测族同步跳过。
 * UI 显式表达用：响应侧层开且 l1/l2 任一为关时返回 true（节点显示「随 L1/L2 部分关闭」） */
export function responsePartiallyClosed(doc: DlpSettings): boolean {
  return layerEnabled(doc, 'response') && (!layerEnabled(doc, 'l1') || !layerEnabled(doc, 'l2'));
}
