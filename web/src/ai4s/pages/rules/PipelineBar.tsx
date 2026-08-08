/**
 * 顶部检测管线条（issue #36，Variant C 血统重写）：请求 → L1 格式规则（#39 起 L1/L1.5 合并）→ L2 → L3 → judge → PG → 上游 → 响应侧。
 * 节点为可点卡片（点击=选中该层，与左侧导航同一 state）；状态徽标/规模摘要由调用方按真实查询数据组装。
 */
import { IconArrowRight } from '@tabler/icons-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import type { StatusBadge } from './layers';

export interface Ai4sPipelineNodeView {
  key: string;
  name: string;
  badges: StatusBadge[];
  /** 节点上的规则数/规模摘要（如 "6 条规则"、"阈值 0.7"） */
  count: string;
}

/** 状态徽标组（管线节点与左侧导航共用渲染） */
export function Ai4sNodeBadges({ badges }: { badges: StatusBadge[] }) {
  return (
    <span className='flex flex-wrap items-center gap-1'>
      {badges.map((b) => (
        <Badge key={b.label} variant={b.variant}>
          {b.label}
        </Badge>
      ))}
    </span>
  );
}

function EndpointChip({ label }: { label: string }) {
  return (
    <div className='rounded-full border border-dashed px-4 py-2 text-sm whitespace-nowrap text-muted-foreground'>
      {label}
    </div>
  );
}

function NodeCard({
  node,
  selected,
  onSelect,
}: {
  node: Ai4sPipelineNodeView;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type='button'
      onClick={onSelect}
      className={cn(
        'w-40 shrink-0 rounded-lg border bg-card p-3 text-left shadow-sm transition-all hover:border-primary/60',
        selected && 'border-primary ring-2 ring-primary/30'
      )}
    >
      <div className='mb-1.5 text-sm font-medium'>{node.name}</div>
      <Ai4sNodeBadges badges={node.badges} />
      <div className='mt-1.5 text-xs text-muted-foreground'>{node.count}</div>
    </button>
  );
}

export function Ai4sPipelineBar({
  requestNodes,
  responseNode,
  selected,
  onSelect,
}: {
  requestNodes: Ai4sPipelineNodeView[];
  responseNode: Ai4sPipelineNodeView;
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  return (
    <Card className='mb-6'>
      <CardContent className='overflow-x-auto pt-6 pb-6'>
        <div className='flex min-w-max items-center gap-2'>
          <EndpointChip label='请求' />
          <IconArrowRight className='size-5 shrink-0 text-muted-foreground' />
          {requestNodes.map((n, i) => (
            <div key={n.key} className='flex items-center gap-2'>
              <NodeCard node={n} selected={selected === n.key} onSelect={() => onSelect(n.key)} />
              {/* 最后一个请求侧节点后的箭头指向上游 */}
              {i === requestNodes.length - 1 ? null : <IconArrowRight className='size-5 shrink-0 text-muted-foreground' />}
            </div>
          ))}
          <IconArrowRight className='size-5 shrink-0 text-muted-foreground' />
          <EndpointChip label='上游' />
          <IconArrowRight className='size-5 shrink-0 text-muted-foreground' />
          <NodeCard node={responseNode} selected={selected === responseNode.key} onSelect={() => onSelect(responseNode.key)} />
        </div>
      </CardContent>
    </Card>
  );
}
