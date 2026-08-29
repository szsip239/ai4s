/**
 * 顶部决策路由图（智能路由页；版式抄 rules/PipelineBar.tsx 先例，无响应侧节点）。
 * 链路与 shim route_resolve 决策流同序（issue #117/#119）：
 *   请求 → 会话检查 → 复杂度分类 → 档位判定 → 模型改写 → 上游；
 * 节点为可点卡片（点击=选中该阶段标签页，与左侧导航同一 state），状态徽标/规模摘要由调用方
 * 按真实 settings 组装；卡底一行 fail-open 说明（任一环节异常不改写、网关兜底旗舰）。
 */
import { IconArrowRight, IconCornerDownRight } from '@tabler/icons-react';
import { cn } from '@/lib/utils';
import { Card, CardContent } from '@/components/ui/card';
import { Ai4sNodeBadges } from '../rules/PipelineBar';
import type { StatusBadge } from '../rules/layers';

export interface RoutingStageNodeView {
  key: string;
  name: string;
  badges: StatusBadge[];
  /** 节点上的参数/规模摘要（如 "TTL 3600s"、"阈值 0.5 · 升档 0.85"）；含 \n 时折行展示 */
  count: string;
}

function EndpointChip({ label }: { label: string }) {
  return <div className='text-muted-foreground rounded-full border border-dashed px-4 py-2 text-sm whitespace-nowrap'>{label}</div>;
}

function NodeCard({ node, selected, onSelect }: { node: RoutingStageNodeView; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type='button'
      onClick={onSelect}
      className={cn(
        'bg-card hover:border-primary/60 w-48 shrink-0 rounded-lg border p-3 text-left shadow-sm transition-all',
        selected && 'border-primary ring-primary/30 ring-2'
      )}
    >
      <div className='mb-1.5 text-sm font-medium'>{node.name}</div>
      <Ai4sNodeBadges badges={node.badges} />
      <div className='text-muted-foreground mt-1.5 text-xs break-all whitespace-pre-line'>{node.count}</div>
    </button>
  );
}

export function Ai4sRoutingPipelineBar({
  nodes,
  selected,
  onSelect,
  requestLabel,
  upstreamLabel,
  failOpenNote,
}: {
  nodes: RoutingStageNodeView[];
  selected: string | null;
  onSelect: (key: string) => void;
  requestLabel: string;
  upstreamLabel: string;
  /** fail-open 说明文案（卡底一行，虚线箭头图标起） */
  failOpenNote: string;
}) {
  return (
    <Card className='mb-6'>
      <CardContent className='overflow-x-auto pt-6 pb-6'>
        <div className='flex min-w-max items-center gap-2'>
          <EndpointChip label={requestLabel} />
          <IconArrowRight className='text-muted-foreground size-5 shrink-0' />
          {nodes.map((n) => (
            <div key={n.key} className='flex items-center gap-2'>
              <NodeCard node={n} selected={selected === n.key} onSelect={() => onSelect(n.key)} />
              <IconArrowRight className='text-muted-foreground size-5 shrink-0' />
            </div>
          ))}
          <EndpointChip label={upstreamLabel} />
        </div>
        <div className='text-muted-foreground mt-4 flex min-w-max items-start gap-1.5 text-xs'>
          <IconCornerDownRight className='mt-0.5 size-3.5 shrink-0' />
          <span>{failOpenNote}</span>
        </div>
      </CardContent>
    </Card>
  );
}
