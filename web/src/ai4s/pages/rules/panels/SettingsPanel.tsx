/**
 * 开关与阈值面板（issue #36）：GET/PUT /dlp-admin/settings（judge/edm/pg 开关阈值 + judge prompt）。
 * PUT 整体替换（服务端校验三段必填且字段齐全）；本地草稿 edited===null 即无改动（dirty 供离开提示）。
 * 404（settings.json 缺失=env 兜底态）按 DlpApiError.status 判定（review #3，不耦合文案）；
 * 非 404 故障仅报错，不追加兜底指引。judge 区常驻警示：真实员工流量启用前必须换内网模型。
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
import { DlpApiError, usePutSettings, useSettings, type DlpSettings } from '../api';
import { Ai4sQueryState } from './QueryState';

export type Ai4sSettingsFocus = 'judge' | 'edm' | 'pg' | null;

export function Ai4sSettingsPanel({
  focus = null,
  onDirtyChange,
}: {
  focus?: Ai4sSettingsFocus;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const { data, isLoading, error } = useSettings();
  const putSettings = usePutSettings();
  const [edited, setEdited] = useState<DlpSettings | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const dirty = edited !== null;
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  // 管线 judge/pg 节点点入时滚动定位到对应区段
  useEffect(() => {
    if (!focus || !data) return;
    document.getElementById(`settings-section-${focus}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [focus, data]);

  const settingsDoc = edited ?? data ?? null;

  const mutate = (next: DlpSettings) => {
    setFormError(null);
    setEdited(next);
  };

  const save = () => {
    if (!settingsDoc) return;
    // 客户端预检（服务端权威校验同款，省一次往返）
    if (!settingsDoc.judge.model.trim() || !settingsDoc.judge.base_url.trim())
      return setFormError('judge model/base_url 不能为空');
    if (!(settingsDoc.judge.timeout > 0)) return setFormError('judge timeout 须 > 0');
    if (!settingsDoc.judge.prompt_system.trim() || !settingsDoc.judge.prompt_fewshot.trim())
      return setFormError('judge prompt_system/prompt_fewshot 不能为空');
    if (!Number.isInteger(settingsDoc.edm.min_hits) || settingsDoc.edm.min_hits < 1)
      return setFormError('edm min_hits 须为 ≥1 整数');
    if (!(settingsDoc.pg.threshold >= 0 && settingsDoc.pg.threshold <= 1))
      return setFormError('pg threshold 须在 0~1');
    putSettings.mutate(settingsDoc, { onSuccess: () => setEdited(null) });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>开关与阈值</CardTitle>
        <CardDescription>judge / EDM / PG 总开关与判定参数（settings.json）；保存即热生效，无需重启</CardDescription>
      </CardHeader>
      <CardContent>
        <Ai4sQueryState
          isLoading={isLoading}
          error={error}
          errorTitle='settings 加载失败'
          rows={4}
          renderError={(err) =>
            err instanceof DlpApiError && err.status === 404 ? (
              // settings.json 缺失（env 兜底态，合法）：给恢复指引；非 404 故障走缺省错误样式、不加指引
              <Alert>
                <IconAlertTriangle className='size-4' />
                <AlertTitle>settings.json 不存在（env 兜底态）</AlertTitle>
                <AlertDescription>
                  当前 shim 以 env/内置默认运行；请在部署侧恢复 deploy/dlp/settings.json 后再于本页维护。
                </AlertDescription>
              </Alert>
            ) : (
              <Alert variant='destructive'>
                <AlertTitle>settings 加载失败</AlertTitle>
                <AlertDescription>{err instanceof Error ? err.message : String(err)}</AlertDescription>
              </Alert>
            )
          }
        >
          {settingsDoc && (
            <div className='space-y-8'>
              {/* ---- 语义 judge ---- */}
              <section id='settings-section-judge' className='space-y-4 scroll-mt-4'>
                <div className='flex items-center justify-between gap-4'>
                  <div>
                    <div className='font-medium'>语义 judge（LLM 判定商密语义指代）</div>
                    <div className='text-sm text-muted-foreground'>shadow 只记不拦；判定失败自动降级纯词表</div>
                  </div>
                  <Switch
                    checked={settingsDoc.judge.enabled}
                    onCheckedChange={(c) => mutate({ ...settingsDoc, judge: { ...settingsDoc.judge, enabled: c } })}
                  />
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
                    <Input
                      value={settingsDoc.judge.model}
                      onChange={(e) => mutate({ ...settingsDoc, judge: { ...settingsDoc.judge, model: e.target.value } })}
                    />
                  </div>
                  <div className='space-y-1.5'>
                    <Label>base_url</Label>
                    <Input
                      value={settingsDoc.judge.base_url}
                      onChange={(e) =>
                        mutate({ ...settingsDoc, judge: { ...settingsDoc.judge, base_url: e.target.value } })
                      }
                    />
                  </div>
                  <div className='space-y-1.5'>
                    <Label>timeout（秒）</Label>
                    <Input
                      type='number'
                      min='1'
                      value={settingsDoc.judge.timeout}
                      onChange={(e) =>
                        mutate({ ...settingsDoc, judge: { ...settingsDoc.judge, timeout: Number(e.target.value) } })
                      }
                    />
                  </div>
                </div>
                <div className='space-y-1.5'>
                  <Label>prompt_system（{'{terms}'} 为词表占位；{'{{ }}'} 为 .format 转义，须保留）</Label>
                  <Textarea
                    rows={5}
                    className='font-mono text-xs'
                    value={settingsDoc.judge.prompt_system}
                    onChange={(e) =>
                      mutate({ ...settingsDoc, judge: { ...settingsDoc.judge, prompt_system: e.target.value } })
                    }
                  />
                </div>
                <div className='space-y-1.5'>
                  <Label>prompt_fewshot（few-shot 示例，同上转义纪律）</Label>
                  <Textarea
                    rows={6}
                    className='font-mono text-xs'
                    value={settingsDoc.judge.prompt_fewshot}
                    onChange={(e) =>
                      mutate({ ...settingsDoc, judge: { ...settingsDoc.judge, prompt_fewshot: e.target.value } })
                    }
                  />
                </div>
              </section>

              {/* ---- EDM ---- */}
              <section id='settings-section-edm' className='space-y-4 scroll-mt-4'>
                <div className='flex items-center justify-between gap-4'>
                  <div>
                    <div className='font-medium'>L3 EDM 文档指纹</div>
                    <div className='text-sm text-muted-foreground'>指纹命中数达阈值即 451（edm.doc_match）</div>
                  </div>
                  <div className='flex items-center gap-3'>
                    <span className='text-sm text-muted-foreground'>min_hits</span>
                    <Input
                      type='number'
                      min='1'
                      step='1'
                      className='w-20'
                      value={settingsDoc.edm.min_hits}
                      onChange={(e) =>
                        mutate({ ...settingsDoc, edm: { ...settingsDoc.edm, min_hits: Number(e.target.value) } })
                      }
                    />
                    <Switch
                      checked={settingsDoc.edm.enabled}
                      onCheckedChange={(c) => mutate({ ...settingsDoc, edm: { ...settingsDoc.edm, enabled: c } })}
                    />
                  </div>
                </div>
              </section>

              {/* ---- 注入 PG ---- */}
              <section id='settings-section-pg' className='space-y-4 scroll-mt-4'>
                <div className='flex items-center justify-between gap-4'>
                  <div>
                    <div className='font-medium'>注入 PG（PromptGuard 2）</div>
                    <div className='text-sm text-muted-foreground'>shadow 只记不拦；分数 ≥ 阈值记 [injection.shadow] 日志</div>
                  </div>
                  <div className='flex items-center gap-3'>
                    <span className='text-sm text-muted-foreground'>threshold（0~1）</span>
                    <Input
                      type='number'
                      step='0.05'
                      min='0'
                      max='1'
                      className='w-24'
                      value={settingsDoc.pg.threshold}
                      onChange={(e) =>
                        mutate({ ...settingsDoc, pg: { ...settingsDoc.pg, threshold: Number(e.target.value) } })
                      }
                    />
                    <Switch
                      checked={settingsDoc.pg.enabled}
                      onCheckedChange={(c) => mutate({ ...settingsDoc, pg: { ...settingsDoc.pg, enabled: c } })}
                    />
                  </div>
                </div>
              </section>

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
        </Ai4sQueryState>
      </CardContent>
    </Card>
  );
}
