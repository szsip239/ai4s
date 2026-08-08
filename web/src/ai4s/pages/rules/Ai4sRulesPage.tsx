/**
 * ai4s 脱敏规则页 = DLP 统一配置中心（issue #36 正式替换）。
 * 布局：顶部检测管线条（节点状态=真实 settings）→ master-detail（左层导航 + 右配置面板）→ 底部纵深层只读表。
 * 五个可配面板对接 shim /dlp-admin/*（React Query，写后 invalidate）：
 *   商密词表 / PII 规则 / 格式规则 L1·L1.5 / EDM 语料 / 开关与阈值（含 judge prompt）。
 * 管线点击与左导航选中联动同一 state；离开有未保存修改的面板时 confirm 提示（dirty 标记由面板上报）。
 */
import { useCallback, useMemo, useRef, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Header } from '@/components/layout/header';
import { Main } from '@/components/layout/main';
import { cn } from '@/lib/utils';
import {
  useEdmCorpus,
  useFormatRules,
  useRecognizers,
  useSettings,
  useWordlist,
} from './api';
import { EXTRA_NAV, LAYER_LABEL, PIPELINE_LAYERS, type PanelKey, type StatusBadge } from './layers';
import { Ai4sNodeBadges, Ai4sPipelineBar, type Ai4sPipelineNodeView } from './PipelineBar';
import { Ai4sDeepLayerRules } from './panels/DeepLayerRules';
import { Ai4sEdmCorpusPanel } from './panels/EdmCorpusPanel';
import { Ai4sFormatRulesPanel } from './panels/FormatRulesPanel';
import { Ai4sRecognizersPanel } from './panels/RecognizersPanel';
import { Ai4sSettingsPanel } from './panels/SettingsPanel';
import { Ai4sWordlistPanel } from './panels/WordlistPanel';

const ON: StatusBadge = { label: '启用', variant: 'default' };
const OFF: StatusBadge = { label: '关闭', variant: 'outline' };
// judge/pg 的 shadow 徽标=契约现状（docs/contracts/dlp-webhook-shim.md：两层 shadow 只记不拦）
const SHADOW: StatusBadge = { label: 'shadow', variant: 'secondary' };
const UNKNOWN: StatusBadge = { label: '未知', variant: 'outline' };

/** 响应侧无独立配置项（复用 L2 规则集做输出侧检查），只读说明卡 */
function Ai4sResponseSideCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>响应侧输出检查</CardTitle>
        <CardDescription>模型应答返回前复用请求侧检测族（归一化 secrets/词表/PII），命中即 451 拒绝</CardDescription>
      </CardHeader>
      <CardContent className='text-sm text-muted-foreground'>
        恒启用，无独立配置项；规则集与「L2 词表/PII」面板一致（流式经 agentgateway 缓冲后评估，mask 对流式无效，
        故响应侧命中统一拒绝——契约 docs/contracts/dlp-webhook-shim.md）。
      </CardContent>
    </Card>
  );
}

export default function Ai4sRulesPage() {
  const [selected, setSelected] = useState<PanelKey>('l2');
  const deepRef = useRef<HTMLDivElement>(null);
  // 当前面板的 dirty 标记（一次只渲染一个面板，面板经 onDirtyChange 上报；卸载时自动复位）
  const dirtyRef = useRef(false);
  const setDirty = useCallback((d: boolean) => {
    dirtyRef.current = d;
  }, []);

  // 管线/导航状态与规模摘要所需的全量查询（与面板共享 React Query 缓存，不额外发请求）
  const settings = useSettings();
  const wordlist = useWordlist();
  const recognizers = useRecognizers();
  const formatRules = useFormatRules();
  const edmCorpus = useEdmCorpus();
  const settingsDoc = settings.data ?? null;

  const nodes = useMemo<Record<PanelKey, Ai4sPipelineNodeView>>(() => {
    const rules = formatRules.data?.rules ?? [];
    const l1Count = rules.filter((r) => r.layer === 'L1' && r.enabled).length;
    const l15Count = rules.filter((r) => r.layer === 'L1.5' && r.enabled).length;
    const cfgBadges = (enabled: boolean | null, shadow: boolean): StatusBadge[] => {
      // enabled=null：settings 查询失败（401/404/故障）→ 状态未知，不臆造
      if (enabled === null) return [UNKNOWN];
      return [enabled ? ON : OFF, ...(shadow && enabled ? [SHADOW] : [])];
    };
    const cfgEnabled = (get: (x: NonNullable<typeof settingsDoc>) => boolean) =>
      settings.isError ? null : settingsDoc ? get(settingsDoc) : null;
    // 分区查询失败时对应徽标降级为“未知”，不显示硬编码“启用”误导（review #8）
    const l1Badges = formatRules.isError ? [UNKNOWN] : [ON];
    // l2 与响应侧同数据源（响应侧复用 L2 规则集）：词表/识别器任一失败即未知
    const l2Badges = wordlist.isError || recognizers.isError ? [UNKNOWN] : [ON];
    return {
      l1: { key: 'l1', name: LAYER_LABEL.l1, badges: l1Badges, count: `${l1Count} 条 reject` },
      l15: { key: 'l15', name: LAYER_LABEL.l15, badges: l1Badges, count: `${l15Count} 条 mask` },
      l2: {
        key: 'l2',
        name: LAYER_LABEL.l2,
        badges: l2Badges,
        count: `${wordlist.data?.terms.length ?? '—'} 词 · ${recognizers.data?.recognizers.length ?? '—'} 规则`,
      },
      l3: {
        key: 'l3',
        name: LAYER_LABEL.l3,
        badges: cfgBadges(cfgEnabled((x) => x.edm.enabled), false),
        count: `${edmCorpus.data?.length ?? '—'} 语料`,
      },
      judge: {
        key: 'judge',
        name: LAYER_LABEL.judge,
        // judge 契约现状同为 shadow（只记不拦），与 PG 并列标注
        badges: cfgBadges(cfgEnabled((x) => x.judge.enabled), true),
        count: settingsDoc?.judge.model ?? '—',
      },
      pg: {
        key: 'pg',
        name: LAYER_LABEL.pg,
        badges: cfgBadges(cfgEnabled((x) => x.pg.enabled), true),
        count: settingsDoc ? `阈值 ${settingsDoc.pg.threshold}` : '—',
      },
      response: { key: 'response', name: LAYER_LABEL.response, badges: l2Badges, count: '复用 L2 规则' },
      toggles: { key: 'toggles', name: LAYER_LABEL.toggles, badges: [], count: '' },
      deep: { key: 'deep', name: LAYER_LABEL.deep, badges: [], count: '' },
    };
  }, [
    formatRules.data,
    formatRules.isError,
    wordlist.data,
    wordlist.isError,
    recognizers.data,
    recognizers.isError,
    edmCorpus.data,
    settings.isError,
    settingsDoc,
  ]);

  /** 切换选中层（管线点击与左导航联动同一 state）；有未保存修改先 confirm */
  const handleSelect = (key: PanelKey) => {
    if (key === 'deep') {
      // 纵深层无面板：锚点滚动到底部只读表，不改选中态
      deepRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      return;
    }
    if (key !== selected && dirtyRef.current) {
      if (!window.confirm('当前面板有未保存的修改，离开将丢弃。确定离开？')) return;
    }
    dirtyRef.current = false;
    setSelected(key);
  };

  // 左导航徽标：管线层与节点同构；toggles 汇总三个开关的启用数；deep 只读
  const navBadges = (key: PanelKey): StatusBadge[] => {
    if (key === 'toggles') {
      if (!settingsDoc) return [];
      const n = [settingsDoc.judge.enabled, settingsDoc.edm.enabled, settingsDoc.pg.enabled].filter(Boolean).length;
      return [{ label: `${n}/3 启用`, variant: 'outline' }];
    }
    if (key === 'deep') return [{ label: '只读', variant: 'outline' }];
    return nodes[key].badges;
  };

  return (
    <>
      <Header title='脱敏规则' />
      <Main>
        <div className='mb-6'>
          <h2 className='text-xl font-semibold tracking-tight'>脱敏规则 · DLP 统一配置中心</h2>
          <p className='text-sm text-muted-foreground'>请求链各层的词表、规则、语料与开关在此集中维护，保存即热生效</p>
        </div>

        <Ai4sPipelineBar
          requestNodes={PIPELINE_LAYERS.filter((l) => l.key !== 'response').map((l) => nodes[l.key])}
          responseNode={nodes.response}
          selected={selected}
          onSelect={(k) => handleSelect(k as PanelKey)}
        />

        <div className='mb-6 flex flex-col gap-6 md:flex-row'>
          <aside className='shrink-0 md:w-60'>
            <nav className='space-y-1'>
              {[...PIPELINE_LAYERS, ...EXTRA_NAV].map((l) => (
                <button
                  key={l.key}
                  type='button'
                  onClick={() => handleSelect(l.key)}
                  className={cn(
                    'flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors',
                    l.key === selected ? 'bg-primary text-primary-foreground' : 'hover:bg-accent'
                  )}
                >
                  <span className='font-medium'>{l.label}</span>
                  <Ai4sNodeBadges badges={navBadges(l.key)} />
                </button>
              ))}
            </nav>
          </aside>
          <div className='min-w-0 flex-1'>
            {(selected === 'l1' || selected === 'l15') && <Ai4sFormatRulesPanel onDirtyChange={setDirty} />}
            {selected === 'l2' && (
              <div className='space-y-6'>
                <Ai4sWordlistPanel onDirtyChange={setDirty} />
                <Ai4sRecognizersPanel onDirtyChange={setDirty} />
              </div>
            )}
            {selected === 'l3' && <Ai4sEdmCorpusPanel onDirtyChange={setDirty} />}
            {selected === 'judge' && <Ai4sSettingsPanel focus='judge' onDirtyChange={setDirty} />}
            {selected === 'pg' && <Ai4sSettingsPanel focus='pg' onDirtyChange={setDirty} />}
            {selected === 'toggles' && <Ai4sSettingsPanel onDirtyChange={setDirty} />}
            {selected === 'response' && <Ai4sResponseSideCard />}
          </div>
        </div>

        {/* 底部：纵深层只读规则表（原 Ai4sRulesPage Alert+Card+Table 段，原样保留） */}
        <div ref={deepRef} className='scroll-mt-4'>
          <Ai4sDeepLayerRules />
        </div>
      </Main>
    </>
  );
}
