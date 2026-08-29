/**
 * 管理员「智能路由」页（issue #120；本轮按脱敏规则页版式重构：顶部决策路由图 + 左侧标签导航）。
 * 布局：顶部决策路由图（节点状态=真实 settings，链路与 shim route_resolve 决策流同序，
 * 点击节点=选中对应标签）→ master-detail（左阶段导航 + 右配置面板）；决策日志为标签项不再首页直出。
 * 总开关 enabled 在标题行即改即存（独占该键，先例：rules/panels/LayerSwitch）；
 * 四配置面板各持自己负责的键（useRoutingDraft patch 语义，键集互不相交互不覆盖），
 * 保存整体 PUT 热生效；离开有未保存修改的面板时 confirm 提示（rules 页 dirty-registry 先例）。
 */
import { useMemo, useState } from 'react';
import { IconArrowsShuffle } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Header } from '@/components/layout/header';
import { Main } from '@/components/layout/main';
import { Ai4sNodeBadges } from '../rules/PipelineBar';
import { useSettings } from '../rules/api';
import { createDirtyRegistry } from '../rules/dirty-registry';
import type { StatusBadge } from '../rules/layers';
import { Ai4sRoutingPipelineBar, type RoutingStageNodeView } from './RoutingPipelineBar';
import { buildSettingsWithRouting, normalizeRouting, usePutRoutingSettings } from './api';
import { Ai4sRoutingClassifyPanel } from './panels/ClassifyPanel';
import { Ai4sRoutingDecisionPanel } from './panels/DecisionPanel';
import { Ai4sRoutingLogPanel } from './panels/LogPanel';
import { Ai4sRoutingSessionPanel } from './panels/SessionPanel';
import { Ai4sRoutingTiersPanel } from './panels/TiersPanel';
import { ROUTER_EXTRA_NAV, ROUTER_STAGES, routingEnabledState, type SmartRoutingNavKey, type StageKey } from './stages';

export default function Ai4sSmartRoutingPage() {
  const { t } = useTranslation();
  const settings = useSettings();
  const putSettings = usePutRoutingSettings();
  const [selected, setSelected] = useState<SmartRoutingNavKey>('classify');
  const dirtyRegistry = useMemo(createDirtyRegistry, []);

  const enabled = routingEnabledState(settings.data ?? null, settings.isError);
  // routing 缺席=关态合法（shim #117）：展示基线经 normalizeRouting 补默认（仅展示/徽标用，不落盘）
  const routing = settings.data ? normalizeRouting(settings.data.routing) : null;

  /** 总开关即改即存：独占 enabled 键，与面板草稿键集不相交（不覆盖未保存编辑） */
  const toggleEnabled = (c: boolean) => {
    if (!settings.data) return;
    putSettings.mutate(buildSettingsWithRouting(settings.data, { ...normalizeRouting(settings.data.routing), enabled: c }));
  };

  // 节点徽标：启用/已关闭/未知（settings 查询失败不臆造）；关态只显示「已关闭」（阶段参数无意义）
  const stageBadges = (key: StageKey): StatusBadge[] => {
    if (enabled === null) return [{ label: t('ai4s.smartRouting.badge.unknown'), variant: 'outline' }];
    const onOff: StatusBadge = enabled
      ? { label: t('ai4s.smartRouting.badge.on'), variant: 'default' }
      : { label: t('ai4s.smartRouting.badge.off'), variant: 'outline' };
    if (!enabled || !routing) return [onOff];
    const extra: Partial<Record<StageKey, StatusBadge[]>> = {
      session: [
        { label: t('ai4s.smartRouting.badge.toolLock'), variant: routing.tool_loop_lock ? 'secondary' : 'outline' },
        { label: t('ai4s.smartRouting.badge.thinkingLock'), variant: routing.thinking_lock ? 'secondary' : 'outline' },
      ],
      // 分类通道=judge 段模型（shim router_classify 沿用 settings judge.*）
      classify: [{ label: settings.data?.judge.model ?? '—', variant: 'secondary' }],
    };
    return [onOff, ...(extra[key] ?? [])];
  };

  // 节点参数摘要：settings 查询失败/加载中显示「—」（不臆造规模，rules 页先例）
  const stageCount = (key: StageKey): string => {
    if (!routing) return '—';
    switch (key) {
      case 'session':
        return t('ai4s.smartRouting.stage.session.count', { ttl: routing.session_ttl });
      case 'classify':
        return t('ai4s.smartRouting.stage.classify.count', {
          timeout: routing.timeout,
          conc: routing.max_concurrency,
        });
      case 'decision':
        return t('ai4s.smartRouting.stage.decision.count', {
          thr: routing.threshold,
          esc: routing.escalate_conf,
        });
      case 'tiers':
        return t('ai4s.smartRouting.stage.tiers.count', {
          simple: routing.tiers.simple,
          complex: routing.tiers.complex,
        });
    }
  };

  const nodes: RoutingStageNodeView[] = ROUTER_STAGES.map((s) => ({
    key: s.key,
    name: t(s.labelKey),
    badges: stageBadges(s.key),
    count: stageCount(s.key),
  }));

  /** 切换选中标签（管线点击与左导航联动同一 state）；任一上报方有未保存修改先 confirm */
  const handleSelect = (key: SmartRoutingNavKey) => {
    if (key !== selected && dirtyRegistry.any()) {
      if (!window.confirm(t('ai4s.dirtyConfirm'))) return;
    }
    dirtyRegistry.clear();
    setSelected(key);
  };

  // 左导航徽标（rules 页 #39 先例：只给已关闭的层显示徽标；查询失败/未知不臆造）
  const navBadges = (key: SmartRoutingNavKey): StatusBadge[] => {
    if (key === 'log' || enabled !== false) return [];
    return [{ label: t('ai4s.smartRouting.badge.off'), variant: 'outline' }];
  };

  return (
    <>
      <Header />
      <Main>
        <div className='mb-6 flex items-start justify-between gap-4'>
          <div>
            <h2 className='flex items-center gap-2 text-xl font-semibold tracking-tight'>
              <IconArrowsShuffle className='size-5' />
              {t('ai4s.smartRouting.title')}
            </h2>
            <p className='text-muted-foreground text-sm'>{t('ai4s.smartRouting.subtitle')}</p>
          </div>
          <div className='flex shrink-0 items-center gap-2'>
            <Label htmlFor='smart-routing-enabled'>{t('ai4s.smartRouting.fields.enabled')}</Label>
            <Switch
              id='smart-routing-enabled'
              checked={enabled ?? false}
              disabled={!settings.data || putSettings.isPending}
              onCheckedChange={toggleEnabled}
            />
          </div>
        </div>

        <Ai4sRoutingPipelineBar
          nodes={nodes}
          selected={selected}
          onSelect={(k) => handleSelect(k as SmartRoutingNavKey)}
          requestLabel={t('ai4s.smartRouting.pipeline.request')}
          upstreamLabel={t('ai4s.smartRouting.pipeline.upstream')}
          failOpenNote={t('ai4s.smartRouting.pipeline.failOpen')}
        />

        <div className='mb-6 flex flex-col gap-6 md:flex-row'>
          <aside className='shrink-0 md:w-60'>
            <nav className='space-y-1'>
              {[...ROUTER_STAGES, ...ROUTER_EXTRA_NAV].map((s) => (
                <button
                  key={s.key}
                  type='button'
                  onClick={() => handleSelect(s.key)}
                  className={cn(
                    'flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors',
                    s.key === selected ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'
                  )}
                >
                  <span className='font-medium'>{t(s.labelKey)}</span>
                  <Ai4sNodeBadges badges={navBadges(s.key)} />
                </button>
              ))}
            </nav>
          </aside>
          <div className='min-w-0 flex-1'>
            {selected === 'session' && <Ai4sRoutingSessionPanel onDirtyChange={dirtyRegistry.reporter('session')} />}
            {selected === 'classify' && <Ai4sRoutingClassifyPanel onDirtyChange={dirtyRegistry.reporter('classify')} />}
            {selected === 'decision' && <Ai4sRoutingDecisionPanel onDirtyChange={dirtyRegistry.reporter('decision')} />}
            {selected === 'tiers' && <Ai4sRoutingTiersPanel onDirtyChange={dirtyRegistry.reporter('tiers')} />}
            {selected === 'log' && <Ai4sRoutingLogPanel />}
          </div>
        </div>
      </Main>
    </>
  );
}
