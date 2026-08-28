/**
 * 智能路由 routing 节客户端预检（issue #120，先例见 rules/panels/settingsValidation.ts）：
 * 规则与服务端权威校验（shim/admin_api.py routing 节，#117 必填五键 + #119 可选五键出席才校验）
 * 同款——预检只为省一次往返，失败原因仍以 API 为准。合法返回 null，非法返回中文原因。
 * 输入为 normalizeRouting 后的十键齐全形状。
 */
import type { RoutingSettings } from '../../rules/api';

/** 模型名白名单字符形态：与 shim _SETTINGS_MODEL_SAFE 同款（值进 extAuthz 响应头
 * x-resolved-model，防响应拆分）；combobox 下拉只是建议，手输值由本校验 + 服务端兜底 */
const MODEL_SAFE = /^[A-Za-z0-9._:-]{1,128}$/;

export function validateRouting(r: RoutingSettings): string | null {
  if (typeof r.enabled !== 'boolean') return 'enabled 须为布尔开关';
  if (!(typeof r.threshold === 'number' && Number.isFinite(r.threshold) && r.threshold >= 0 && r.threshold <= 1))
    return 'threshold 须在 0~1';
  if (!r.tiers.simple.trim() || !MODEL_SAFE.test(r.tiers.simple))
    return 'tiers.simple 模型名非法（须匹配 [A-Za-z0-9._:-]，≤128 字符）';
  if (!r.tiers.complex.trim() || !MODEL_SAFE.test(r.tiers.complex))
    return 'tiers.complex 模型名非法（须匹配 [A-Za-z0-9._:-]，≤128 字符）';
  if (!(typeof r.timeout === 'number' && Number.isFinite(r.timeout) && r.timeout > 0)) return 'timeout 须 > 0';
  if (!Number.isInteger(r.max_concurrency) || r.max_concurrency < 1) return 'max_concurrency 须为 ≥1 整数';
  if (!(typeof r.escalate_conf === 'number' && Number.isFinite(r.escalate_conf) && r.escalate_conf >= 0 && r.escalate_conf <= 1))
    return 'escalate_conf 须在 0~1';
  if (!(typeof r.session_ttl === 'number' && Number.isFinite(r.session_ttl) && r.session_ttl > 0))
    return 'session_ttl 须 > 0';
  if (!r.prompt.trim()) return 'prompt 不能为空';
  if (typeof r.tool_loop_lock !== 'boolean') return 'tool_loop_lock 须为布尔开关';
  if (typeof r.thinking_lock !== 'boolean') return 'thinking_lock 须为布尔开关';
  return null;
}
