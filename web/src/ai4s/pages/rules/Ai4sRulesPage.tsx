/**
 * ai4s 脱敏规则页 = DLP 统一配置中心（issue #36 正式替换；#38 UX 修正；#39 导航精简；#40 分层总开关）。
 * 布局：顶部检测管线条（节点状态=真实 settings）→ master-detail（左层导航 + 右配置面板），无常驻区块。
 * 五个可配面板对接 shim /dlp-admin/*（React Query，写后 invalidate）：
 *   商密词表 / PII 规则 / 格式规则 / EDM 语料 / 开关与阈值（judge、PG 为独立面板，另含整体视图）。
 * L1/L2/响应侧主开关（#40）：三层面板头部各一个即改即存 Switch（panels/LayerSwitch 复用件），
 * 管线节点与左导航徽标显示 settings 真实开关态（关闭显示「已关闭」）。
 * 纵深层规则（只读）为普通导航选中项（#38：不再常驻页底）；管线点击与左导航选中联动同一 state；
 * 左导航只给已关闭的层显示「已关闭」徽标（#39：启用态与顶部管线重复不再展示；纵深层保留「只读」徽标）；
 * 离开有未保存修改的面板时 confirm 提示（dirty 按上报方分 key 记账，见 dirty-registry.ts，issue #69 P2-C）。
 */
import { useMemo, useState } from 'react';
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
import { createDirtyRegistry } from './dirty-registry';
import { Ai4sNodeBadges, Ai4sPipelineBar, type Ai4sPipelineNodeView } from './PipelineBar';
import { Ai4sDeepLayerRules } from './panels/DeepLayerRules';
import { Ai4sEdmCorpusPanel } from './panels/EdmCorpusPanel';
import { Ai4sFormatRulesPanel } from './panels/FormatRulesPanel';
import { Ai4sInjectRulesPanel } from './panels/InjectRulesPanel';
import { Ai4sJudgePanel } from './panels/JudgePanel';
import { Ai4sLayerSwitch } from './panels/LayerSwitch';
import { Ai4sPgPanel } from './panels/PgPanel';
import { Ai4sRecognizersPanel } from './panels/RecognizersPanel';
import { Ai4sSettingsPanel } from './panels/SettingsPanel';
import { Ai4sWordlistPanel } from './panels/WordlistPanel';

const ON: StatusBadge = { label: '启用', variant: 'default' };
const OFF: StatusBadge = { label: '已关闭', variant: 'outline' };
// judge/pg 的 shadow 徽标=契约现状（docs/contracts/dlp-webhook-shim.md：两层 shadow 只记不拦）
const SHADOW: StatusBadge = { label: 'shadow', variant: 'secondary' };
const UNKNOWN: StatusBadge = { label: '未知', variant: 'outline' };

/** 响应侧输出检查（issue #40 起有独立总开关）：复用请求侧检测族（归一化 secrets/词表/PII），命中即 451 拒绝 */
function Ai4sResponseSideCard() {
  return (
    <Card>
      <CardHeader>
        <div className='flex items-center justify-between gap-4'>
          <div>
            <CardTitle>响应侧输出检查</CardTitle>
            <CardDescription>
              模型应答返回前复用请求侧检测族（归一化 secrets/词表/PII），命中即 451 拒绝；头部为本层总开关
            </CardDescription>
          </div>
          <Ai4sLayerSwitch section='response' />
        </div>
      </CardHeader>
      <CardContent className='text-sm text-muted-foreground'>
        检测族规则集与「L1 格式规则」「L2 词表/PII」面板一致，l1/l2 总开关关闭时响应侧对应检测族同步跳过（流式经
        agentgateway 缓冲后评估，mask 对流式无效，故响应侧命中统一拒绝——契约 docs/contracts/dlp-webhook-shim.md）。
      </CardContent>
    </Card>
  );
}

export default function Ai4sRulesPage() {
  const [selected, setSelected] = useState<PanelKey>('l2');
  // dirty 按上报方分 key 记账（issue #69 P2-C）：同层多面板/对话框（如 L2 词表+PII）共用单个 boolean 会互相覆盖，
  // 词表弄脏后开关一次 PII 对话框即丢 dirty；注册表互不复位，面板卸载时自行上报 false 注销
  const dirtyRegistry = useMemo(createDirtyRegistry, []);

  // 管线/导航状态与规模摘要所需的全量查询（与面板共享 React Query 缓存，不额外发请求）
  const settings = useSettings();
  const wordlist = useWordlist();
  const recognizers = useRecognizers();
  const formatRules = useFormatRules();
  const edmCorpus = useEdmCorpus();
  const settingsDoc = settings.data ?? null;

  const nodes = useMemo<Record<PanelKey, Ai4sPipelineNodeView>>(() => {
    const rules = formatRules.data?.rules ?? [];
    // L1/L1.5 合并节点（#39）：规则数为启用规则合计（action 列区分 reject/mask）
    const enabledCount = rules.filter((r) => r.enabled).length;
    const cfgBadges = (enabled: boolean | null, shadow: boolean): StatusBadge[] => {
      // enabled=null：settings 查询失败（401/404/故障）→ 状态未知，不臆造
      if (enabled === null) return [UNKNOWN];
      return [enabled ? ON : OFF, ...(shadow && enabled ? [SHADOW] : [])];
    };
    const cfgEnabled = (get: (x: NonNullable<typeof settingsDoc>) => boolean) =>
      settings.isError ? null : settingsDoc ? get(settingsDoc) : null;
    // l1/l2/response 徽标=分层总开关真实态（issue #40，settings 驱动；旧 settings.json 缺段按服务端语义回退 true）
    return {
      l1: {
        key: 'l1',
        name: LAYER_LABEL.l1,
        badges: cfgBadges(cfgEnabled((x) => x.l1?.enabled ?? true), false),
        // 分区查询失败时计数显示「—」而非 0 或旧缓存值（不臆造规模）
        count: formatRules.isError ? '—' : `${enabledCount} 条规则`,
      },
      l2: {
        key: 'l2',
        name: LAYER_LABEL.l2,
        badges: cfgBadges(cfgEnabled((x) => x.l2?.enabled ?? true), false),
        count:
          wordlist.isError || recognizers.isError
            ? '—'
            : `${wordlist.data?.terms.length ?? '—'} 词 · ${recognizers.data?.recognizers.length ?? '—'} 规则`,
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
      rules: {
        key: 'rules',
        name: LAYER_LABEL.rules,
        // 规则层 shadow 只记不拦（issue #104）；block 开=命中即 451 阻断，不再标 shadow
        badges: cfgBadges(cfgEnabled((x) => x.rules.enabled), !(settingsDoc?.rules.block ?? false)),
        count: settingsDoc ? `16 模式组${settingsDoc.rules.block ? ' · 阻断' : ''}` : '—',
      },
      pg: {
        key: 'pg',
        name: LAYER_LABEL.pg,
        badges: cfgBadges(cfgEnabled((x) => x.pg.enabled), true),
        count: settingsDoc ? `阈值 ${settingsDoc.pg.threshold}` : '—',
      },
      response: {
        key: 'response',
        name: LAYER_LABEL.response,
        badges: cfgBadges(cfgEnabled((x) => x.response?.enabled ?? true), false),
        count: '复用 L1/L2 规则',
      },
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

  /** 切换选中层（管线点击与左导航联动同一 state）；任一上报方有未保存修改先 confirm */
  const handleSelect = (key: PanelKey) => {
    if (key !== selected && dirtyRegistry.any()) {
      if (!window.confirm('当前面板有未保存的修改，离开将丢弃。确定离开？')) return;
    }
    dirtyRegistry.clear();
    setSelected(key);
  };

  // 左导航徽标（#39 起只给已关闭的层显示徽标，启用不重复展示；纵深层保留「只读」说明徽标。
  // #40：开关态取 settings 真实值——查询失败/缺失时不臆造，不显示徽标，管线节点已显示「未知」）
  const navBadges = (key: PanelKey): StatusBadge[] => {
    if (key === 'deep') return [{ label: '只读', variant: 'outline' }];
    if (settings.isError || !settingsDoc) return [];
    const closed: Partial<Record<PanelKey, boolean>> = {
      l1: !(settingsDoc.l1?.enabled ?? true),
      l2: !(settingsDoc.l2?.enabled ?? true),
      l3: !settingsDoc.edm.enabled,
      judge: !settingsDoc.judge.enabled,
      rules: !settingsDoc.rules.enabled,
      pg: !settingsDoc.pg.enabled,
      response: !(settingsDoc.response?.enabled ?? true),
    };
    return closed[key] ? [OFF] : [];
  };

  return (
    <>
      <Header title='脱敏规则' />
      <Main>
        <div className='mb-6'>
          <h2 className='text-xl font-semibold tracking-tight'>脱敏规则 · DLP 统一配置中心</h2>
          <p className='text-sm text-muted-foreground'>请求链各层的词表、规则、语料与开关在此集中维护，保存即热生效</p>
          <p className='mt-1 text-xs text-muted-foreground'>维护方法见 docs/guides/dlp-config-guide.md</p>
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
            {selected === 'l1' && <Ai4sFormatRulesPanel onDirtyChange={dirtyRegistry.reporter('l1')} />}
            {selected === 'l2' && (
              <div className='space-y-6'>
                <Ai4sWordlistPanel onDirtyChange={dirtyRegistry.reporter('l2:wordlist')} />
                <Ai4sRecognizersPanel onDirtyChange={dirtyRegistry.reporter('l2:recognizers')} />
              </div>
            )}
            {selected === 'l3' && <Ai4sEdmCorpusPanel onDirtyChange={dirtyRegistry.reporter('l3')} />}
            {selected === 'judge' && <Ai4sJudgePanel onDirtyChange={dirtyRegistry.reporter('judge')} />}
            {selected === 'rules' && <Ai4sInjectRulesPanel onDirtyChange={dirtyRegistry.reporter('rules')} />}
            {selected === 'pg' && <Ai4sPgPanel onDirtyChange={dirtyRegistry.reporter('pg')} />}
            {selected === 'toggles' && <Ai4sSettingsPanel onDirtyChange={dirtyRegistry.reporter('toggles')} />}
            {selected === 'response' && <Ai4sResponseSideCard />}
            {selected === 'deep' && <Ai4sDeepLayerRules />}
          </div>
        </div>
      </Main>
    </>
  );
}
