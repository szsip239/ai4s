/**
 * 管理员「智能路由」页（issue #120，顶栏一级入口，权限 read_channels 与 shim 读级对齐）。
 * 配置卡：读 GET /dlp-admin/settings（rules/api useSettings，judge/pg/rules 段已 normalize）→
 * 只改 routing 节 → 整体 PUT（buildSettingsWithRouting 补 l1/l2/response 缺段过服务端全量严校）。
 * 表单抄 rules 页 SettingsPanel 先例：受控草稿 edited??data、null=无改动=保存 disabled、
 * formError 行内红字、保存 toast 热生效；routing 节缺席=关态合法，normalizeRouting 补默认。
 * 两档模型映射 combobox（ModelCombobox）：下拉拉 axonhub /models 卡片（useQueryAllModels），
 * 允许手输；保存前 validateRouting 预检 + 服务端白名单兜底。
 * 决策日志卡：GET /dlp-admin/shadow-verdicts?layer=router&n=50 只读表格
 * （时间/档位/p_complex/原因/改写目标/延迟/会话），手动刷新不轮询。
 */
import { useMemo, useState } from 'react';
import { format } from 'date-fns';
import { useTranslation } from 'react-i18next';
import { IconArrowsShuffle, IconLoader2, IconRefresh } from '@tabler/icons-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { Header } from '@/components/layout/header';
import { Main } from '@/components/layout/main';
import { useQueryAllModels } from '@/features/models/data/models';
import { useSettings, type RoutingSettings } from '../rules/api';
import { Ai4sSettingsQueryState } from '../rules/panels/QueryState';
import {
  buildSettingsWithRouting,
  normalizeRouting,
  usePutRoutingSettings,
  useRouterVerdicts,
  type RouterVerdict,
} from './api';
import { ModelCombobox } from './ModelCombobox';
import { validateRouting } from './validation';

/** 决策行（router 层五决策字段非 None 才写，读侧全部可选；error 条=分类失败 fail-open） */
function VerdictRow({ r }: { r: RouterVerdict }) {
  return (
    <TableRow>
      <TableCell className='whitespace-nowrap text-muted-foreground'>
        {typeof r.ts === 'number' ? format(new Date(r.ts * 1000), 'MM-dd HH:mm:ss') : '—'}
      </TableCell>
      <TableCell>
        {r.tier ? <Badge variant={r.tier === 'complex' ? 'default' : 'secondary'}>{r.tier}</Badge> : '—'}
      </TableCell>
      <TableCell className='font-mono text-xs'>
        {typeof r.p_complex === 'number' ? r.p_complex.toFixed(3) : '—'}
      </TableCell>
      <TableCell className='max-w-56'>
        <span className={r.error ? 'text-destructive' : 'text-muted-foreground'} title={r.error ?? undefined}>
          {r.reason ?? '—'}
          {r.error ? `（${r.error}）` : ''}
        </span>
      </TableCell>
      <TableCell className='max-w-56 truncate font-mono text-xs' title={r.resolved_model ?? undefined}>
        {r.resolved_model ?? '—'}
      </TableCell>
      <TableCell className='whitespace-nowrap text-muted-foreground'>
        {typeof r.latency_ms === 'number' ? `${Math.round(r.latency_ms)} ms` : '—'}
      </TableCell>
      <TableCell>{r.session ? <Badge variant='outline'>✓</Badge> : '—'}</TableCell>
    </TableRow>
  );
}

export default function Ai4sSmartRoutingPage() {
  const { t } = useTranslation();
  const settings = useSettings();
  const putSettings = usePutRoutingSettings();
  const verdicts = useRouterVerdicts();
  const models = useQueryAllModels({});
  const [edited, setEdited] = useState<RoutingSettings | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const dirty = edited !== null;
  // routing 缺席=关态合法（shim #117）：草稿基线经 normalizeRouting 补默认，十键齐全
  const routing = edited ?? (settings.data ? normalizeRouting(settings.data.routing) : null);

  // combobox 建议=axonhub /models 卡片（modelID 去重排序）；加载失败/为空不挡手输
  const modelOptions = useMemo(() => {
    const ids = [...new Set((models.data?.edges ?? []).map((e) => e.node.modelID))].sort();
    return ids.map((id) => ({ value: id, label: id }));
  }, [models.data]);

  const mutate = (next: RoutingSettings) => {
    setFormError(null);
    setEdited(next);
  };

  const save = () => {
    if (!settings.data || !routing) return;
    // 客户端预检（与服务端权威校验同款规则）；失败原因行内展示，不发请求
    const invalid = validateRouting(routing);
    if (invalid) return setFormError(invalid);
    putSettings.mutate(buildSettingsWithRouting(settings.data, routing), { onSuccess: () => setEdited(null) });
  };

  const records = verdicts.data?.records ?? [];

  return (
    <>
      <Header />
      <Main>
        <div className='mb-6'>
          <h2 className='flex items-center gap-2 text-xl font-semibold tracking-tight'>
            <IconArrowsShuffle className='size-5' />
            {t('ai4s.smartRouting.title')}
          </h2>
          <p className='text-sm text-muted-foreground'>{t('ai4s.smartRouting.subtitle')}</p>
        </div>

        <div className='space-y-6'>
          {/* ---- 路由配置（settings.json routing 节） ---- */}
          <Card>
            <CardHeader>
              <CardTitle>{t('ai4s.smartRouting.config.title')}</CardTitle>
              <CardDescription>{t('ai4s.smartRouting.config.description')}</CardDescription>
            </CardHeader>
            <CardContent>
              <Ai4sSettingsQueryState isLoading={settings.isLoading} error={settings.error}>
                {routing && (
                  <div className='space-y-8'>
                    <section className='space-y-4'>
                      <div className='flex items-center justify-between gap-4'>
                        <div>
                          <div className='font-medium'>{t('ai4s.smartRouting.fields.enabled')}</div>
                          <div className='text-sm text-muted-foreground'>
                            {t('ai4s.smartRouting.fields.enabledHint')}
                          </div>
                        </div>
                        <Switch
                          checked={routing.enabled}
                          onCheckedChange={(c) => mutate({ ...routing, enabled: c })}
                        />
                      </div>
                    </section>

                    <section className='grid grid-cols-2 gap-4 md:grid-cols-3'>
                      <div className='space-y-1.5'>
                        <Label>{t('ai4s.smartRouting.fields.threshold')}</Label>
                        <Input
                          type='number'
                          step='0.05'
                          min='0'
                          max='1'
                          value={routing.threshold}
                          onChange={(e) => mutate({ ...routing, threshold: Number(e.target.value) })}
                        />
                      </div>
                      <div className='space-y-1.5'>
                        <Label>{t('ai4s.smartRouting.fields.escalateConf')}</Label>
                        <Input
                          type='number'
                          step='0.05'
                          min='0'
                          max='1'
                          value={routing.escalate_conf}
                          onChange={(e) => mutate({ ...routing, escalate_conf: Number(e.target.value) })}
                        />
                      </div>
                      <div className='space-y-1.5'>
                        <Label>{t('ai4s.smartRouting.fields.timeout')}</Label>
                        <Input
                          type='number'
                          min='0'
                          step='0.5'
                          value={routing.timeout}
                          onChange={(e) => mutate({ ...routing, timeout: Number(e.target.value) })}
                        />
                      </div>
                      <div className='space-y-1.5'>
                        <Label>{t('ai4s.smartRouting.fields.maxConcurrency')}</Label>
                        <Input
                          type='number'
                          min='1'
                          step='1'
                          value={routing.max_concurrency}
                          onChange={(e) => mutate({ ...routing, max_concurrency: Number(e.target.value) })}
                        />
                      </div>
                      <div className='space-y-1.5'>
                        <Label>{t('ai4s.smartRouting.fields.sessionTtl')}</Label>
                        <Input
                          type='number'
                          min='1'
                          step='1'
                          value={routing.session_ttl}
                          onChange={(e) => mutate({ ...routing, session_ttl: Number(e.target.value) })}
                        />
                      </div>
                    </section>

                    <section className='grid grid-cols-1 gap-4 md:grid-cols-2'>
                      <div className='space-y-1.5'>
                        <Label>{t('ai4s.smartRouting.fields.tierSimple')}</Label>
                        <ModelCombobox
                          value={routing.tiers.simple}
                          onChange={(v) => mutate({ ...routing, tiers: { ...routing.tiers, simple: v } })}
                          modelOptions={modelOptions}
                          isLoading={models.isLoading}
                          placeholder={t('ai4s.smartRouting.fields.modelPlaceholder')}
                          emptyText={t('ai4s.smartRouting.fields.modelListEmpty')}
                        />
                      </div>
                      <div className='space-y-1.5'>
                        <Label>{t('ai4s.smartRouting.fields.tierComplex')}</Label>
                        <ModelCombobox
                          value={routing.tiers.complex}
                          onChange={(v) => mutate({ ...routing, tiers: { ...routing.tiers, complex: v } })}
                          modelOptions={modelOptions}
                          isLoading={models.isLoading}
                          placeholder={t('ai4s.smartRouting.fields.modelPlaceholder')}
                          emptyText={t('ai4s.smartRouting.fields.modelListEmpty')}
                        />
                      </div>
                    </section>

                    <section className='space-y-4'>
                      <div className='flex items-center justify-between gap-4'>
                        <div>
                          <div className='font-medium'>{t('ai4s.smartRouting.fields.toolLoopLock')}</div>
                          <div className='text-sm text-muted-foreground'>
                            {t('ai4s.smartRouting.fields.toolLoopLockHint')}
                          </div>
                        </div>
                        <Switch
                          checked={routing.tool_loop_lock}
                          onCheckedChange={(c) => mutate({ ...routing, tool_loop_lock: c })}
                        />
                      </div>
                      <div className='flex items-center justify-between gap-4'>
                        <div>
                          <div className='font-medium'>{t('ai4s.smartRouting.fields.thinkingLock')}</div>
                          <div className='text-sm text-muted-foreground'>
                            {t('ai4s.smartRouting.fields.thinkingLockHint')}
                          </div>
                        </div>
                        <Switch
                          checked={routing.thinking_lock}
                          onCheckedChange={(c) => mutate({ ...routing, thinking_lock: c })}
                        />
                      </div>
                    </section>

                    <section className='space-y-1.5'>
                      <Label>{t('ai4s.smartRouting.fields.prompt')}</Label>
                      <Textarea
                        rows={7}
                        className='font-mono text-xs'
                        value={routing.prompt}
                        onChange={(e) => mutate({ ...routing, prompt: e.target.value })}
                      />
                    </section>

                    <div className='flex items-center justify-end gap-3'>
                      {formError && <span className='text-sm text-destructive'>{formError}</span>}
                      {dirty && !formError && (
                        <span className='text-sm text-amber-600'>{t('ai4s.smartRouting.unsaved')}</span>
                      )}
                      <Button onClick={save} disabled={!dirty || putSettings.isPending}>
                        {putSettings.isPending && <IconLoader2 className='animate-spin' />}
                        {t('ai4s.smartRouting.save')}
                      </Button>
                    </div>
                  </div>
                )}
              </Ai4sSettingsQueryState>
            </CardContent>
          </Card>

          {/* ---- 决策日志（shadow-verdicts 出口首个前端消费；手动刷新不轮询） ---- */}
          <Card>
            <CardHeader>
              <div className='flex items-center justify-between gap-4'>
                <div>
                  <CardTitle>{t('ai4s.smartRouting.log.title')}</CardTitle>
                  <CardDescription>{t('ai4s.smartRouting.log.description')}</CardDescription>
                </div>
                <Button
                  variant='outline'
                  size='sm'
                  disabled={verdicts.isFetching}
                  onClick={() => verdicts.refetch()}
                >
                  <IconRefresh className={verdicts.isFetching ? 'animate-spin' : undefined} />
                  {t('ai4s.smartRouting.log.refresh')}
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {verdicts.isLoading ? (
                <p className='py-6 text-center text-sm text-muted-foreground'>{t('common.loading')}</p>
              ) : verdicts.isError ? (
                <Alert variant='destructive'>
                  <AlertTitle>{t('ai4s.smartRouting.log.loadError')}</AlertTitle>
                  <AlertDescription>
                    {verdicts.error instanceof Error ? verdicts.error.message : String(verdicts.error)}
                  </AlertDescription>
                </Alert>
              ) : records.length === 0 ? (
                <p className='py-6 text-center text-sm text-muted-foreground'>
                  {t('ai4s.smartRouting.log.empty')}
                </p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t('ai4s.smartRouting.log.columns.time')}</TableHead>
                      <TableHead>{t('ai4s.smartRouting.log.columns.tier')}</TableHead>
                      <TableHead>{t('ai4s.smartRouting.log.columns.pComplex')}</TableHead>
                      <TableHead>{t('ai4s.smartRouting.log.columns.reason')}</TableHead>
                      <TableHead>{t('ai4s.smartRouting.log.columns.resolvedModel')}</TableHead>
                      <TableHead>{t('ai4s.smartRouting.log.columns.latency')}</TableHead>
                      <TableHead>{t('ai4s.smartRouting.log.columns.session')}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {records.map((r, i) => (
                      <VerdictRow key={`${r.ts}-${i}`} r={r} />
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </div>
      </Main>
    </>
  );
}
