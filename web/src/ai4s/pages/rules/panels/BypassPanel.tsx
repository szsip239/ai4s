/**
 * 白名单 Key 面板（原 Key 绕行，issue #129；改名 + 下拉登记随「开关与阈值」tab 收口）：
 * 可信 Key 绕开 DLP 检测的管理入口。登记从既有 Key 列表按名称搜索选择（不手贴明文、不批量拉明文），
 * 「已登记」标记由服务端匹配（POST /dlp-admin/bypass-keys/match，issue #138）给出——
 * 不再浏览器拉全量明文算 SHA-256；明文只在点击登记这一刻单条拉取（同「查看 key」对话框的
 * 刻意单条路径），提交服务端算哈希后即弃，名单只存哈希。两种粒度：全部层（含网关 L1 密钥红线——
 * 须改用 /bv1 专用入口，面板给出地址）；按层（勾选 shim 侧检测层，同 URL 无感）。
 * 每次绕行服务端写审计条（智能路由 → 日志标签 → Key 绕行视图可查）。
 * issue #138：面板按 read_api_keys 门控——所在页路由只保证 read_channels，而候选 Key 列表
 * （GraphQL apiKeys）与服务端匹配都需要 read_api_keys；无权限时整面板禁用并提示。
 */
import { useMemo, useState } from 'react';
import { Check, ChevronsUpDown } from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { usePermissions } from '@/hooks/usePermissions';
import { useRequestPermissions } from '@/hooks/useRequestPermissions';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Switch } from '@/components/ui/switch';
import { useApiKey, useApiKeys } from '@/features/apikeys/data';
import {
  BYPASSABLE_LAYERS,
  matchedKeyIdSet,
  useAddBypassKey,
  useBypassKeyMatch,
  useBypassKeys,
  useDeleteBypassKey,
  useUpdateBypassKey,
  type BypassKey,
  type BypassLayer,
  type BypassScope,
} from '../api';

/** 绕行层中文名（对齐检测链各面板叫法；l1 此处特指 shim 侧归一化变体，非网关原生 regex） */
const LAYER_NAME: Record<BypassLayer, string> = {
  l1: 'L1 归一化变体',
  l2: 'L2 词表/PII',
  edm: 'L3 EDM',
  rules: '注入规则',
  pg: '注入 PG',
  judge: '语义 judge',
  response: '响应侧',
};

interface KeyOption {
  id: string;
  name: string;
}

function AddForm({ canWrite }: { canWrite: boolean }) {
  const add = useAddBypassKey();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<KeyOption | null>(null);
  const [label, setLabel] = useState('');
  const [scope, setScope] = useState<BypassScope>('all');
  const [layers, setLayers] = useState<BypassLayer[]>([]);
  const [fetchingToken, setFetchingToken] = useState(false);

  // 白名单登记面向存量正常 Key（archived 不提供）。候选列表只取 id+name——列表查询不含明文（issue #138）；
  // 「已登记」置灰由服务端匹配给出。本页路由只保证 read_channels，这两个查询实际需 read_api_keys，
  // 门控在面板层（见 Ai4sBypassPanel），到这里必有权限
  const { data: apiKeysData, isLoading } = useApiKeys({
    first: 200,
    orderBy: { field: 'CREATED_AT', direction: 'DESC' },
    where: { statusIn: ['enabled', 'disabled'] },
  });
  const keyOptions = useMemo<KeyOption[]>(
    () => (apiKeysData?.edges ?? []).map((e) => ({ id: e.node.id, name: e.node.name || e.node.id })),
    [apiKeysData]
  );
  const keyIds = useMemo(() => keyOptions.map((k) => k.id), [keyOptions]);
  const { data: matchData } = useBypassKeyMatch(keyIds);
  const matchedIds = useMemo(() => matchedKeyIdSet(matchData?.matches ?? []), [matchData]);

  const isRegistered = (k: KeyOption) => matchedIds.has(k.id);

  // 明文只在点击登记这一刻按 id 单条拉取（刻意单条，同「查看 key」对话框路径），选中候选时不拉
  const detailQuery = useApiKey(selected?.id ?? '', { enabled: false });

  const submit = async () => {
    if (!selected) return;
    setFetchingToken(true);
    try {
      const { data: fresh } = await detailQuery.refetch();
      const token = fresh?.key;
      if (!token) {
        toast.error('未能获取所选 Key 的明文，请刷新后重试');
        return;
      }
      add.mutate(
        { token, label: label.trim(), scope, layers: scope === 'layers' ? layers : undefined },
        {
          onSuccess: () => {
            setSelected(null);
            setLabel('');
            setScope('all');
            setLayers([]);
          },
        },
      );
    } finally {
      setFetchingToken(false);
    }
  };

  return (
    <div className='space-y-3 rounded-md border p-4'>
      <div className='grid gap-3 md:grid-cols-2'>
        <div className='space-y-1.5'>
          <Label>选择 Key（按名称搜索）</Label>
          <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
              <Button variant='outline' role='combobox' aria-expanded={open} className='w-full justify-between font-normal'>
                <span className='truncate'>{selected ? selected.name : isLoading ? '加载中…' : '搜索并选择 Key'}</span>
                <ChevronsUpDown className='ml-2 h-4 w-4 shrink-0 opacity-50' />
              </Button>
            </PopoverTrigger>
            <PopoverContent className='w-[--radix-popover-trigger-width] p-0' align='start'>
              <Command>
                <CommandInput placeholder='输入 Key 名称搜索…' />
                <CommandList>
                  <CommandEmpty>无匹配 Key</CommandEmpty>
                  <CommandGroup>
                    {keyOptions.map((k) => {
                      const registered = isRegistered(k);
                      return (
                        <CommandItem
                          key={k.id}
                          value={k.id}
                          keywords={[k.name]}
                          disabled={registered}
                          onSelect={() => {
                            setSelected(k);
                            setLabel((prev) => prev || k.name);
                            setOpen(false);
                          }}
                        >
                          <Check className={cn('mr-2 h-4 w-4', selected?.id === k.id ? 'opacity-100' : 'opacity-0')} />
                          <span className='truncate'>{k.name}</span>
                          {registered && (
                            <Badge variant='outline' className='ml-auto shrink-0'>
                              已登记
                            </Badge>
                          )}
                        </CommandItem>
                      );
                    })}
                  </CommandGroup>
                </CommandList>
              </Command>
            </PopoverContent>
          </Popover>
        </div>
        <div className='space-y-1.5'>
          <Label htmlFor='bypass-label'>备注</Label>
          <Input
            id='bypass-label'
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder='默认取 Key 名称，可改为用途/负责人'
          />
        </div>
      </div>
      <RadioGroup value={scope} onValueChange={(v) => setScope(v as BypassScope)} className='gap-2'>
        <div className='flex items-start gap-2'>
          <RadioGroupItem value='all' id='scope-all' className='mt-0.5' />
          <Label htmlFor='scope-all' className='font-normal'>
            全部层（含 L1 密钥红线）——须改用专用入口{' '}
            <code className='rounded bg-muted px-1'>{window.location.origin}/bv1</code>
          </Label>
        </div>
        <div className='flex items-start gap-2'>
          <RadioGroupItem value='layers' id='scope-layers' className='mt-0.5' />
          <Label htmlFor='scope-layers' className='font-normal'>按层（勾选下方检测层，原入口不变）</Label>
        </div>
      </RadioGroup>
      {scope === 'layers' && (
        <div className='flex flex-wrap gap-x-4 gap-y-2 pl-6'>
          {BYPASSABLE_LAYERS.map((l) => (
            <label key={l} className='flex items-center gap-1.5 text-sm'>
              <Checkbox
                checked={layers.includes(l)}
                onCheckedChange={(c) =>
                  setLayers((prev) => (c ? [...prev, l] : prev.filter((x) => x !== l)))
                }
              />
              {LAYER_NAME[l]}
            </label>
          ))}
        </div>
      )}
      <div>
        <Button
          size='sm'
          disabled={
            !canWrite || add.isPending || fetchingToken || !selected || !label.trim() || (scope === 'layers' && layers.length === 0)
          }
          title={!canWrite ? '当前账号为只读权限（缺 write_channels），无法登记' : undefined}
          onClick={submit}
        >
          登记白名单 Key
        </Button>
      </div>
    </div>
  );
}

function KeyRow({ k, canWrite }: { k: BypassKey; canWrite: boolean }) {
  const update = useUpdateBypassKey();
  const del = useDeleteBypassKey();
  return (
    <div className='flex items-center justify-between gap-3 rounded-md border px-3 py-2'>
      <div className='min-w-0 space-y-1'>
        <div className='flex flex-wrap items-center gap-2'>
          <span className='font-medium'>{k.label}</span>
          <Badge variant={k.scope === 'all' ? 'destructive' : 'secondary'}>
            {k.scope === 'all' ? '全部层' : '按层'}
          </Badge>
          {k.scope === 'layers' && k.layers.map((l) => (
            <Badge key={l} variant='outline'>{LAYER_NAME[l]}</Badge>
          ))}
          {!k.enabled && <Badge variant='outline'>已停用</Badge>}
        </div>
        <div className='text-xs text-muted-foreground'>
          {k.scope === 'all' && (
            <>
              专用入口 <code className='rounded bg-muted px-1'>{window.location.origin}/bv1</code>
              （不支持 model=auto） ·{' '}
            </>
          )}
          登记于 {new Date(k.added_at * 1000).toLocaleString()}
        </div>
      </div>
      <div className='flex shrink-0 items-center gap-2'>
        <Switch
          checked={k.enabled}
          disabled={update.isPending || !canWrite}
          title={!canWrite ? '当前账号为只读权限（缺 write_channels）' : undefined}
          onCheckedChange={(c) => update.mutate({ id: k.id, enabled: c })}
        />
        <Button
          size='sm'
          variant='ghost'
          disabled={del.isPending || !canWrite}
          title={!canWrite ? '当前账号为只读权限（缺 write_channels）' : undefined}
          onClick={() => {
            if (window.confirm(`删除白名单条目「${k.label}」？该 Key 立即恢复全量检测。`)) del.mutate(k.id);
          }}
        >
          删除
        </Button>
      </div>
    </div>
  );
}

export function Ai4sBypassPanel() {
  // issue #138：面板数据查询（候选 Key 列表 + 服务端匹配）都需 read_api_keys，而本页路由只保证
  // read_channels——无权限时整面板禁用并提示，避免两个查询被服务端 403 打回
  const { canViewApiKeys } = useRequestPermissions();
  if (!canViewApiKeys) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>白名单 Key</CardTitle>
          <CardDescription>可信 Key（自动化管道等）绕开 DLP 检测的管理入口。</CardDescription>
        </CardHeader>
        <CardContent>
          <p className='text-sm text-muted-foreground'>
            当前账号缺少 read_api_keys 权限，无法查询候选 Key 与白名单匹配状态；请联系管理员开通后重试。
          </p>
        </CardContent>
      </Card>
    );
  }
  return <BypassPanelContent />;
}

function BypassPanelContent() {
  const { data, isError } = useBypassKeys();
  // 写控件按 canWrite 禁用（issue #139）：登记/启停/删除均写 shim /dlp-admin/bypass-keys（write_channels 鉴权）
  const { channelPermissions } = usePermissions();
  const canWrite = channelPermissions.canWrite;
  return (
    <Card>
      <CardHeader>
        <CardTitle>白名单 Key</CardTitle>
        <CardDescription>
          可信 Key（自动化管道等）绕开 DLP 检测。从既有 Key 中按名称搜索登记，名单只存哈希不明文落盘。
          全部层 = 含 L1 密钥红线，须改用 /bv1 专用入口；按层 = 只跳过勾选的检测层，原入口不变。
          每次绕行都有审计记录（智能路由 → 日志 → Key 绕行）。
        </CardDescription>
      </CardHeader>
      <CardContent className='space-y-4'>
        {!canWrite && (
          <p className='text-xs text-amber-600'>当前账号缺 write_channels 权限，为只读视图：登记、启停与删除不可用</p>
        )}
        <AddForm canWrite={canWrite} />
        {isError && <p className='text-sm text-destructive'>名单加载失败</p>}
        <div className='space-y-2'>
          {(data?.keys ?? []).map((k) => (
            <KeyRow key={k.id} k={k} canWrite={canWrite} />
          ))}
          {data && data.keys.length === 0 && (
            <p className='text-sm text-muted-foreground'>名单为空——所有 Key 均接受全量检测。</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
