/**
 * 语义 judge 面板（issue #38）：独立面板消灭焦点移动（原 SettingsPanel focus 滚动定位）。
 * 只编辑 judge 段；保存时与最新 useSettings 缓存合并（edm/pg 段与 version/_comment 原样）整体 PUT，
 * 写后 invalidate——三面板读写同一份 settings.json 且不互相覆盖。
 * 外发警示常驻：真实员工流量启用前必须换内网模型（契约部署 checklist 硬性项）。
 */
import { useEffect, useState } from 'react';
import { IconAlertTriangle, IconLoader2 } from '@tabler/icons-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { usePutSettings, useSettings, type JudgeSettings } from '../api';
import { Ai4sSettingsQueryState } from './QueryState';
import { validateJudge } from './settingsValidation';

export function Ai4sJudgePanel({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const { data, isLoading, error } = useSettings();
  const putSettings = usePutSettings();
  const [edited, setEdited] = useState<JudgeSettings | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  // edited===null 即无本地改动（dirty 供离开提示）
  const dirty = edited !== null;
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  const judge = edited ?? data?.judge ?? null;

  const mutate = (next: JudgeSettings) => {
    setFormError(null);
    setEdited(next);
  };

  const save = () => {
    if (!data || !edited) return;
    const invalid = validateJudge(edited); // 共享预检（settingsValidation），服务端权威校验为准
    if (invalid) return setFormError(invalid);
    // 只换 judge 段：edm/pg 与 version/_comment 取最新缓存原样（整体 PUT 语义，避免面板间互相覆盖）
    putSettings.mutate({ ...data, judge: edited }, { onSuccess: () => setEdited(null) });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>语义 judge</CardTitle>
        <CardDescription>
          用大模型判断一段话是否在变相提及公司商密（谐音、暗示这类精确词表抓不住的变形）。当前只记判定结果不拦截（shadow）；judge
          服务不可用时自动退回纯词表检测，不影响正常请求
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Ai4sSettingsQueryState isLoading={isLoading} error={error}>
          {judge && (
            <div className='space-y-6'>
              <div className='flex items-center justify-between gap-4'>
                <div className='text-sm text-muted-foreground'>启用后每个请求都会异步送 judge 判定，不阻塞正常应答</div>
                <Switch checked={judge.enabled} onCheckedChange={(c) => mutate({ ...judge, enabled: c })} />
              </div>
              <Alert className='border-amber-500/60 bg-amber-50 text-amber-900 dark:border-amber-400/40 dark:bg-amber-950/40 dark:text-amber-100'>
                <IconAlertTriangle className='size-4' />
                <AlertTitle>外发警示</AlertTitle>
                <AlertDescription>
                  真实员工流量启用前必须换内网模型（当前测试期外发 deepseek，judge 会扩大暴露面，生产不可外发——契约部署
                  checklist 硬性项）。
                </AlertDescription>
              </Alert>
              <div className='grid grid-cols-3 gap-4'>
                <div className='space-y-1.5'>
                  <Label>model</Label>
                  <Input value={judge.model} onChange={(e) => mutate({ ...judge, model: e.target.value })} />
                </div>
                <div className='space-y-1.5'>
                  <Label>base_url</Label>
                  <Input value={judge.base_url} onChange={(e) => mutate({ ...judge, base_url: e.target.value })} />
                </div>
                <div className='space-y-1.5'>
                  <Label>timeout（秒）</Label>
                  <Input
                    type='number'
                    min='1'
                    value={judge.timeout}
                    onChange={(e) => mutate({ ...judge, timeout: Number(e.target.value) })}
                  />
                </div>
              </div>
              <div className='space-y-1.5'>
                <Label>prompt_system（{'{terms}'} 为词表占位；{'{{ }}'} 为 .format 转义，须保留）</Label>
                <Textarea
                  rows={5}
                  className='font-mono text-xs'
                  value={judge.prompt_system}
                  onChange={(e) => mutate({ ...judge, prompt_system: e.target.value })}
                />
              </div>
              <div className='space-y-1.5'>
                <Label>prompt_fewshot（few-shot 示例，同上转义纪律）</Label>
                <Textarea
                  rows={6}
                  className='font-mono text-xs'
                  value={judge.prompt_fewshot}
                  onChange={(e) => mutate({ ...judge, prompt_fewshot: e.target.value })}
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
