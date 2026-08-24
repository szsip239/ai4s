/**
 * settings 段校验共享模块（issue #38 review #3）：judge/pg 段客户端预检原本在
 * JudgePanel/PgPanel/SettingsPanel 逐字重复，收敛于此三处共用。
 * 规则与服务端权威校验（shim/admin_api.py _validate_settings）同款——预检只为省一次往返，
 * 失败原因仍以 API 为准。合法返回 null，非法返回中文原因。
 */
import type { JudgeSettings, PgSettings } from '../api';

/** judge 段预检：model/base_url 非空、timeout > 0、两段 prompt 非空、threshold 0~1、action 四档（issue #94） */
export function validateJudge(judge: JudgeSettings): string | null {
  if (!judge.model.trim() || !judge.base_url.trim()) return 'judge model/base_url 不能为空';
  if (!(judge.timeout > 0)) return 'judge timeout 须 > 0';
  if (!judge.prompt_system.trim() || !judge.prompt_fewshot.trim())
    return 'judge prompt_system/prompt_fewshot 不能为空';
  if (!(judge.threshold >= 0 && judge.threshold <= 1)) return 'judge threshold 须在 0~1';
  if (!['off', 'shadow', 'warn', 'reject'].includes(judge.action)) return 'judge action 须为 关/仅记录/告警/拦截 之一';
  return null;
}

/** pg 段预检：threshold/block_threshold（issue #103 阻断阈值）均须在 0~1 */
export function validatePg(pg: PgSettings): string | null {
  if (!(pg.threshold >= 0 && pg.threshold <= 1)) return 'pg threshold 须在 0~1';
  if (!(pg.block_threshold >= 0 && pg.block_threshold <= 1)) return 'pg block_threshold 须在 0~1';
  return null;
}
