/**
 * 开关与阈值整体面板（issue #36；issue #38 起为整体视图，judge/PG 单项维护走各自独立面板；issue #40 扩六段；issue #104 加注入规则段；
 * issue #133 方案 A：分层总开关收归顶部管线节点（唯一控制入口），本面板只留阈值/参数类配置）。
 * GET/PUT /dlp-admin/settings（judge/edm/rules/pg 开关阈值 + judge prompt；分层总开关不在此重复）。
 * PUT 整体替换（服务端校验七段必填且字段齐全）；本地草稿 edited===null 即无改动（dirty 供离开提示）。
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
import { validateInjectRules, validateJudge, validatePg } from './settingsValidation';

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
  // 保证 PUT 整体替换始终七段齐全（rules 段由 api selectSettings 的 normalizeInjectRules 补默认；否则只改其他段保存也会被服务端 400）
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
    // 共享预检（settingsValidation，judge→edm→pg→rules 顺序同原逐字面板的报错优先级；服务端权威校验为准）
    const invalidJudge = validateJudge(settingsDoc.judge);
    if (invalidJudge) return setFormError(invalidJudge);
    if (!Number.isInteger(settingsDoc.edm.min_hits) || settingsDoc.edm.min_hits < 1)
      return setFormError('edm min_hits 须为 ≥1 整数');
    const invalidPg = validatePg(settingsDoc.pg);
    if (invalidPg) return setFormError(invalidPg);
    const invalidRules = validateInjectRules(settingsDoc.rules); // issue #104 注入规则层（两布尔开关）
    if (invalidRules) return setFormError(invalidRules);
    putSettings.mutate(settingsDoc, { onSuccess: () => setEdited(null) });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>开关与阈值</CardTitle>
        <CardDescription>
          judge / EDM / 注入规则 / PG 的阈值与参数整体视图，与左侧各层面板读写同一份
          settings.json；分层总开关在顶部管线节点上（唯一控制入口，即改即存）。单项维护走对应面板，这里适合整体核对；保存即热生效
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Ai4sSettingsQueryState isLoading={isLoading} error={error}>
          {settingsDoc && (
            <div className='space-y-8'>
              {/* ---- 语义 judge ---- */}
              <section className='space-y-4'>
                <div>
                  <div className='font-medium'>语义 judge（LLM 判定商密语义指代）</div>
                  <div className='text-sm text-muted-foreground'>shadow 只记不拦；判定失败自动降级纯词表；总开关在顶部管线节点</div>
                </div>
                <Alert className='border-amber-500/60 bg-amber-50 text-amber-900 dark:border-amber-400/40 dark:bg-amber-950/40 dark:text-amber-100'>
                  <IconAlertTriangle className='size-4' />
                  <AlertTitle>外发警示</AlertTitle>
                  <AlertDescription>
                    真实员工流量启用前必须换内网模型（当前测试期经内部网关外发 API 判定，judge 会扩大暴露面，生产不可外发——契约部署
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
                    <div className='text-sm text-muted-foreground'>
                      指纹命中数达阈值即 451（edm.doc_match）；总开关在顶部管线节点
                    </div>
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
                  </div>
                </div>
              </section>

              {/* ---- 注入规则（issue #104；管线顺序在 PG 之前——确定性模式命中先于模型打分） ---- */}
              <section className='space-y-4'>
                <div className='flex items-center justify-between gap-4'>
                  <div>
                    <div className='font-medium'>注入规则（16 模式组）</div>
                    <div className='text-sm text-muted-foreground'>
                      命中记 [injection.rules] 日志（shadow 不拦）；开阻断后命中即 451；总开关在顶部管线节点
                    </div>
                  </div>
                  <div className='flex items-center gap-3'>
                    <span className='text-sm text-muted-foreground'>阻断</span>
                    <Switch
                      checked={settingsDoc.rules.block}
                      onCheckedChange={(c) => mutate({ ...settingsDoc, rules: { ...settingsDoc.rules, block: c } })}
                    />
                  </div>
                </div>
              </section>

              {/* ---- 注入 PG ---- */}
              <section className='space-y-4'>
                <div className='flex items-center justify-between gap-4'>
                  <div>
                    <div className='font-medium'>注入 PG（PromptGuard 2）</div>
                    <div className='text-sm text-muted-foreground'>
                      shadow 只记不拦；分数 ≥ 阈值记 [injection.shadow] 日志；总开关在顶部管线节点
                    </div>
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
        </Ai4sSettingsQueryState>
      </CardContent>
    </Card>
  );
}
