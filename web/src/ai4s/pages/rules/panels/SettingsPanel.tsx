/**
 * 开关与阈值整体面板（issue #36；issue #38 起为整体视图，judge/PG 单项维护走各自独立面板；issue #40 扩六段）。
 * GET/PUT /dlp-admin/settings（L1/L2/响应侧分层总开关 + judge/edm/pg 开关阈值 + judge prompt）。
 * PUT 整体替换（服务端校验六段必填且字段齐全）；本地草稿 edited===null 即无改动（dirty 供离开提示）。
 * 404/故障展示与 judge/PG 面板同款（Ai4sSettingsQueryState）。judge 区常驻警示：真实员工流量启用前必须换内网模型。
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
import { usePutSettings, useSettings, type DlpSettings } from '../api';
import { Ai4sSettingsQueryState } from './QueryState';
import { validateJudge, validatePg } from './settingsValidation';

export function Ai4sSettingsPanel({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const { data, isLoading, error } = useSettings();
  const putSettings = usePutSettings();
  const [edited, setEdited] = useState<DlpSettings | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const dirty = edited !== null;
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  // 旧 settings.json 可能缺 l1/l2/response 段（shim 侧缺段默认 true）：草稿基线按缺省 true 补齐三段，
  // 保证 PUT 整体替换始终六段齐全（否则只改其他段保存也会被服务端 400）
  const settingsDoc =
    edited ??
    (data
      ? {
          ...data,
          l1: data.l1 ?? { enabled: true },
          l2: data.l2 ?? { enabled: true },
          response: data.response ?? { enabled: true },
        }
      : null);

  const mutate = (next: DlpSettings) => {
    setFormError(null);
    setEdited(next);
  };

  const save = () => {
    if (!settingsDoc) return;
    // 共享预检（settingsValidation，judge→edm→pg 顺序同原逐字面板的报错优先级；服务端权威校验为准）
    const invalidJudge = validateJudge(settingsDoc.judge);
    if (invalidJudge) return setFormError(invalidJudge);
    if (!Number.isInteger(settingsDoc.edm.min_hits) || settingsDoc.edm.min_hits < 1)
      return setFormError('edm min_hits 须为 ≥1 整数');
    const invalidPg = validatePg(settingsDoc.pg);
    if (invalidPg) return setFormError(invalidPg);
    putSettings.mutate(settingsDoc, { onSuccess: () => setEdited(null) });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>开关与阈值</CardTitle>
        <CardDescription>
          L1/L2/响应侧分层总开关与 judge / EDM / PG 开关阈值的整体视图，与左侧各层面板读写同一份
          settings.json。单项维护走对应面板，这里适合整体核对；保存即热生效
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Ai4sSettingsQueryState isLoading={isLoading} error={error}>
          {settingsDoc && (
            <div className='space-y-8'>
              {/* ---- L1 格式规则总开关（issue #40） ---- */}
              <section className='space-y-4'>
                <div className='flex items-center justify-between gap-4'>
                  <div>
                    <div className='font-medium'>L1 格式规则（密钥/私钥/PII 格式）</div>
                    <div className='text-sm text-muted-foreground'>
                      关闭后整层撤防（密钥拦截全敞口）；翻转会联动重渲染网关规则，网关侧同步撤下/恢复
                    </div>
                  </div>
                  <Switch
                    checked={settingsDoc.l1?.enabled ?? true}
                    onCheckedChange={(c) => mutate({ ...settingsDoc, l1: { enabled: c } })}
                  />
                </div>
              </section>

              {/* ---- L2 词表/PII 总开关（issue #40） ---- */}
              <section className='space-y-4'>
                <div className='flex items-center justify-between gap-4'>
                  <div>
                    <div className='font-medium'>L2 词表/PII（商密词表 + PII 识别）</div>
                    <div className='text-sm text-muted-foreground'>关闭后词表命中拦截与 PII 脱敏整体跳过</div>
                  </div>
                  <Switch
                    checked={settingsDoc.l2?.enabled ?? true}
                    onCheckedChange={(c) => mutate({ ...settingsDoc, l2: { enabled: c } })}
                  />
                </div>
              </section>

              {/* ---- 语义 judge ---- */}
              <section className='space-y-4'>
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
              <section className='space-y-4'>
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
              <section className='space-y-4'>
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

              {/* ---- 响应侧输出检查总开关（issue #40） ---- */}
              <section className='space-y-4'>
                <div className='flex items-center justify-between gap-4'>
                  <div>
                    <div className='font-medium'>响应侧输出检查</div>
                    <div className='text-sm text-muted-foreground'>
                      模型应答命中 secrets/词表/PII 即 451 拒绝；关闭后响应侧整段放行不检测
                      （l1/l2 总开关关闭时对应检测族在响应侧同样跳过）
                    </div>
                  </div>
                  <Switch
                    checked={settingsDoc.response?.enabled ?? true}
                    onCheckedChange={(c) => mutate({ ...settingsDoc, response: { enabled: c } })}
                  />
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
        </Ai4sSettingsQueryState>
      </CardContent>
    </Card>
  );
}
