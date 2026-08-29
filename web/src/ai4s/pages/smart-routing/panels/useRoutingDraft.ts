/**
 * 智能路由面板草稿共享钩子（先例见 rules/panels/PgPanel.tsx 的 edited??data 模式）。
 * 与原单卡版本的差异：本页拆四面板后各面板只持**自己负责的键**（patch 语义），
 * 视图与保存均与最新缓存基线（normalizeRouting）合并——各面板键集互不相交
 * （enabled 归标题行总开关即改即存独占），并发编辑互不覆盖；
 * 保存仍整体 PUT（shim 全量严校），写后 invalidate 热生效。
 */
import { useEffect, useState } from 'react';
import { useSettings, type RoutingSettings } from '../../rules/api';
import { buildSettingsWithRouting, normalizeRouting, usePutRoutingSettings } from '../api';
import { validateRouting } from '../validation';

export function useRoutingDraft(onDirtyChange?: (dirty: boolean) => void) {
  const settings = useSettings();
  const putSettings = usePutRoutingSettings();
  // patch 只装本面板改过的键（null=无改动=保存 disabled；离开提示经 dirty 上报）
  const [patch, setPatch] = useState<Partial<RoutingSettings> | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const dirty = patch !== null;
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  const base = settings.data ? normalizeRouting(settings.data.routing) : null;
  // 视图 = 最新基线 + 本面板改动（tiers 嵌套对象整体替换，面板 mutate 时构造完整值）
  const routing = base ? { ...base, ...(patch ?? {}) } : null;

  const mutate = (p: Partial<RoutingSettings>) => {
    setFormError(null);
    setPatch((prev) => ({ ...(prev ?? {}), ...p }));
  };

  const save = () => {
    if (!settings.data || !routing || !patch) return;
    // 客户端预检（与服务端权威校验同款规则）；失败原因行内展示，不发请求
    const invalid = validateRouting(routing);
    if (invalid) return setFormError(invalid);
    putSettings.mutate(buildSettingsWithRouting(settings.data, routing), { onSuccess: () => setPatch(null) });
  };

  return { settings, putSettings, routing, dirty, formError, mutate, save };
}
