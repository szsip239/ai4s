/**
 * settings 段校验共享模块（issue #38 review #3）：judge/pg 段客户端预检原本在
 * JudgePanel/PgPanel/SettingsPanel 逐字重复，收敛于此三处共用。
 * issue #104 起 rules 段（注入规则层）同址。
 * 规则与服务端权威校验（shim/admin_api.py _validate_settings）同款——预检只为省一次往返，
 * 失败原因仍以 API 为准。合法返回 null，非法返回中文原因。
 */
import type { InjectRulesSettings, JudgeSettings, PgSettings } from '../api';

/** judge 段预检：model/base_url 非空、timeout > 0、两段 prompt 非空、threshold 0~1、action 档位（issue #94）。
 * issue #101 契约纪律：reject 档 schema 存在但「语义层永不阻断」不支持消费——面板拒绝保存，
 * 用户须选 off/shadow/warn（后端 schema 仍兼容存量 reject 值，按 shadow 处理）。
 * issue #105 注入第二职责：inject_enabled 开态时两段注入 prompt 必须非空（关态允许空串占位——
 * prompt 单一源=settings.json，web 不内置文本；「开+空」运行侧必 error 条，不给保存） */
export function validateJudge(judge: JudgeSettings): string | null {
  if (!judge.model.trim() || !judge.base_url.trim()) return 'judge model/base_url 不能为空';
  if (!(judge.timeout > 0)) return 'judge timeout 须 > 0';
  if (!judge.prompt_system.trim() || !judge.prompt_fewshot.trim())
    return 'judge prompt_system/prompt_fewshot 不能为空';
  if (!(judge.threshold >= 0 && judge.threshold <= 1)) return 'judge threshold 须在 0~1';
  if (judge.action === 'reject') return 'judge action 不可选拦截：契约约定语义层永不阻断（issue #101）';
  if (!['off', 'shadow', 'warn'].includes(judge.action)) return 'judge action 须为 关/仅记录/告警 之一';
  // 注入第二职责（issue #105）：undefined 按关态放行（旧数据防御；normalizeJudge 保存前已补默认）
  if (judge.inject_enabled !== undefined && typeof judge.inject_enabled !== 'boolean')
    return 'judge inject_enabled 须为布尔开关';
  if (judge.inject_enabled && (!judge.inject_prompt_system.trim() || !judge.inject_prompt_fewshot.trim()))
    return 'judge 注入判定开启时 inject_prompt_system/inject_prompt_fewshot 不能为空';
  return null;
}

/** pg 段预检：threshold/block_threshold（issue #103 阻断阈值）均须在 0~1 */
export function validatePg(pg: PgSettings): string | null {
  if (!(pg.threshold >= 0 && pg.threshold <= 1)) return 'pg threshold 须在 0~1';
  if (!(pg.block_threshold >= 0 && pg.block_threshold <= 1)) return 'pg block_threshold 须在 0~1';
  return null;
}

/** rules 段预检（issue #104）：enabled/block 两布尔开关——取值两档均合法（语义由后端权威校验），
 * 预检只挡运行时非布尔类型错误（手改 JSON/异常数据进面板的防御，与后端布尔校验同款） */
export function validateInjectRules(rules: InjectRulesSettings): string | null {
  if (typeof rules.enabled !== 'boolean') return 'rules enabled 须为布尔开关';
  if (typeof rules.block !== 'boolean') return 'rules block 须为布尔开关';
  return null;
}
