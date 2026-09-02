/**
 * 注入 PG 面板（issue #38）：独立面板消灭焦点移动（原 SettingsPanel focus 滚动定位）。
 * 只编辑 pg 段；保存时与最新 useSettings 缓存合并（judge/edm 段与 version/_comment 原样）整体 PUT，
 * 写后 invalidate——三面板读写同一份 settings.json 且不互相覆盖。
 * issue #97：补 normalize 打分前置归一化开关（键 issue #44 起即有、后端校验已有，面板此前只有 threshold）。
 * issue #103：补 block_enabled/block_threshold 高分阻断试点两控件（≥阻断阈值 451 + 飞书告警，
 * 阻断判定在应答前同步进行、延迟约 +50~140ms；默认关=维持 shadow 现状）。
 */
import { useEffect, useState } from 'react';
import { IconLoader2 } from '@tabler/icons-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { usePutSettings, useSettings, type PgSettings } from '../api';
import { Ai4sSettingsQueryState } from './QueryState';
import { validatePg } from './settingsValidation';

export function Ai4sPgPanel({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const { data, isLoading, error } = useSettings();
  const putSettings = usePutSettings();
  const [edited, setEdited] = useState<PgSettings | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  // edited===null 即无本地改动（dirty 供离开提示）
  const dirty = edited !== null;
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  const pg = edited ?? data?.pg ?? null;

  const mutate = (next: PgSettings) => {
    setFormError(null);
    setEdited(next);
  };

  const save = () => {
    if (!data || !edited) return;
    const invalid = validatePg(edited); // 共享预检（settingsValidation），服务端权威校验为准
    if (invalid) return setFormError(invalid);
    // 只换 pg 段：judge/edm 与 version/_comment 取最新缓存原样（整体 PUT 语义，避免面板间互相覆盖）
    putSettings.mutate({ ...data, pg: edited }, { onSuccess: () => setEdited(null) });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>注入 PG</CardTitle>
        <CardDescription>
          识别提示词注入攻击（套取系统提示词、覆盖指令、虚假授权这类手法）。默认命中只记日志不拦截（shadow，日志标记
          [injection.shadow]），打开「高分阻断」后 ≥ 阻断阈值的请求直接 451；两个阈值均 0~1，调低抓得更多、误报也更多
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Ai4sSettingsQueryState isLoading={isLoading} error={error} rows={2}>
          {pg && (
            <div className='space-y-6'>
              <p className='text-sm text-muted-foreground'>
                启用后每个请求过一遍 PromptGuard 2 打分；分数 ≥ 阈值只写日志，不影响员工正常使用；本层总开关在顶部管线「注入 PG」节点上
              </p>
              <div className='flex items-center gap-3'>
                <Label className='shrink-0'>threshold（0~1）</Label>
                <Input
                  type='number'
                  step='0.05'
                  min='0'
                  max='1'
                  className='w-28'
                  value={pg.threshold}
                  onChange={(e) => mutate({ ...pg, threshold: Number(e.target.value) })}
                />
              </div>
              <div className='flex items-center justify-between gap-4'>
                <div className='text-sm text-muted-foreground'>
                  归一化识别：评分前先还原 base64 伪装内容、清除不可见字符、全角转半角——只改打分输入，
                  员工原文照常转发；打开后对绕过伪装的注入更敏感，误报略增
                </div>
                <Switch checked={pg.normalize} onCheckedChange={(c) => mutate({ ...pg, normalize: c })} />
              </div>
              <div className='flex items-center justify-between gap-4'>
                <div className='text-sm text-muted-foreground'>
                  高分阻断试点：分数 ≥ 阻断阈值时直接拒绝请求（451）并发飞书告警（告警不含原文）；阻断判定在
                  应答前同步进行，每个请求约多花 50~140ms——关闭则维持「只记日志」现状
                </div>
                <Switch
                  checked={pg.block_enabled}
                  onCheckedChange={(c) => mutate({ ...pg, block_enabled: c })}
                />
              </div>
              <div className='flex items-center gap-3'>
                <Label className='shrink-0'>block_threshold（0~1）</Label>
                <Input
                  type='number'
                  step='0.05'
                  min='0'
                  max='1'
                  className='w-28'
                  value={pg.block_threshold}
                  onChange={(e) => mutate({ ...pg, block_threshold: Number(e.target.value) })}
                />
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
