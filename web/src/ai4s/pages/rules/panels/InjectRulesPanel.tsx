/**
 * 注入规则层面板（issue #104，#100 路线② 生产落点；结构对齐 PgPanel 独立面板先例）。
 * 只编辑 rules 段；保存时与最新 useSettings 缓存合并（judge/edm/pg 段与 version/_comment 原样）
 * 整体 PUT，写后 invalidate——多面板读写同一份 settings.json 且不互相覆盖。
 * 两个开关：enabled（默认关，新层先进场 shadow 观察）与 block（命中即 451——规则命中是
 * 布尔无分数，故无阈值控件；阻断判定在应答前同步进行，µs 级延迟无感）。
 */
import { useEffect, useState } from 'react';
import { IconLoader2 } from '@tabler/icons-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { usePutSettings, useSettings, type InjectRulesSettings } from '../api';
import { Ai4sSettingsQueryState } from './QueryState';
import { validateInjectRules } from './settingsValidation';

export function Ai4sInjectRulesPanel({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const { data, isLoading, error } = useSettings();
  const putSettings = usePutSettings();
  const [edited, setEdited] = useState<InjectRulesSettings | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  // edited===null 即无本地改动（dirty 供离开提示）
  const dirty = edited !== null;
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  const rules = edited ?? data?.rules ?? null;

  const mutate = (next: InjectRulesSettings) => {
    setFormError(null);
    setEdited(next);
  };

  const save = () => {
    if (!data || !edited) return;
    const invalid = validateInjectRules(edited); // 共享预检（settingsValidation），服务端权威校验为准
    if (invalid) return setFormError(invalid);
    // 只换 rules 段：judge/edm/pg 与 version/_comment 取最新缓存原样（整体 PUT 语义，避免面板间互相覆盖）
    putSettings.mutate({ ...data, rules: edited }, { onSuccess: () => setEdited(null) });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>注入规则</CardTitle>
        <CardDescription>
          用语义模式组识别提示词注入（提取系统提示词、覆盖指令、虚假授权、情感操纵这类手法，中英日韩四语），
          并还原 base64 嵌套编码与不可见字符伪装。默认命中只记日志不拦截（shadow，日志标记
          [injection.rules]），打开「命中阻断」后命中即 451 并发飞书告警（告警不含原文）
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Ai4sSettingsQueryState isLoading={isLoading} error={error} rows={2}>
          {rules && (
            <div className='space-y-6'>
              <div className='flex items-center justify-between gap-4'>
                <div className='text-sm text-muted-foreground'>
                  启用后每个请求过一遍 16 个注入模式组（µs 级，不影响员工正常使用）；命中只写日志
                </div>
                <Switch checked={rules.enabled} onCheckedChange={(c) => mutate({ ...rules, enabled: c })} />
              </div>
              <div className='flex items-center justify-between gap-4'>
                <div className='text-sm text-muted-foreground'>
                  命中阻断：模式组命中即直接拒绝请求（451）并发飞书告警；规则层判定在应答前同步进行，
                  延迟 µs 级无感——关闭则维持「只记日志」现状。建议先开启用观察一段时间误报再开阻断
                </div>
                <Switch checked={rules.block} onCheckedChange={(c) => mutate({ ...rules, block: c })} />
              </div>
              <div className='flex items-center justify-end gap-3'>
                {formError && <span className='text-sm text-destructive'>{formError}</span>}
                {dirty && !formError && <span className='text-sm text-amber-600'>有未保存修改</span>}
                <Button onClick={save} disabled={!dirty || putSettings.isPending}>
                  {putSettings.isPending && <IconLoader2 className='animate-spin' />}
                  保存
                </Button>
              </div>
            </div>
          )}
        </Ai4sSettingsQueryState>
      </CardContent>
    </Card>
  );
}
