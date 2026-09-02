/**
 * Key 绕行名单面板（issue #129）：可信 Key 绕开 DLP 检测的管理入口。
 * 两种粒度：全部层（含网关 L1 密钥红线——须改用 /bv1 专用入口，面板给出地址）；
 * 按层（勾选 shim 侧检测层，同 URL 无感）。名单只存 SHA-256 哈希，明文粘贴一次即弃。
 * 每次绕行服务端写审计条（智能路由 → 日志标签 → Key 绕行视图可查）。
 */
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import { Switch } from '@/components/ui/switch';
import {
  BYPASSABLE_LAYERS,
  useAddBypassKey,
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

function AddForm() {
  const add = useAddBypassKey();
  const [token, setToken] = useState('');
  const [label, setLabel] = useState('');
  const [scope, setScope] = useState<BypassScope>('all');
  const [layers, setLayers] = useState<BypassLayer[]>([]);

  const submit = () => {
    add.mutate(
      { token: token.trim(), label: label.trim(), scope, layers: scope === 'layers' ? layers : undefined },
      {
        onSuccess: () => {
          setToken('');
          setLabel('');
          setScope('all');
          setLayers([]);
        },
      },
    );
  };

  return (
    <div className='space-y-3 rounded-md border p-4'>
      <div className='grid gap-3 md:grid-cols-2'>
        <div className='space-y-1.5'>
          <Label htmlFor='bypass-token'>Key 明文（只算哈希，不落盘）</Label>
          <Input
            id='bypass-token'
            type='password'
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder='粘贴完整 Key'
            autoComplete='off'
          />
        </div>
        <div className='space-y-1.5'>
          <Label htmlFor='bypass-label'>备注</Label>
          <Input
            id='bypass-label'
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder='用途/负责人，如：CI 管道'
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
          disabled={add.isPending || !token.trim() || !label.trim() || (scope === 'layers' && layers.length === 0)}
          onClick={submit}
        >
          登记绕行 Key
        </Button>
      </div>
    </div>
  );
}

function KeyRow({ k }: { k: BypassKey }) {
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
          disabled={update.isPending}
          onCheckedChange={(c) => update.mutate({ id: k.id, enabled: c })}
        />
        <Button
          size='sm'
          variant='ghost'
          disabled={del.isPending}
          onClick={() => {
            if (window.confirm(`删除绕行条目「${k.label}」？该 Key 立即恢复全量检测。`)) del.mutate(k.id);
          }}
        >
          删除
        </Button>
      </div>
    </div>
  );
}

export function Ai4sBypassPanel() {
  const { data, isError } = useBypassKeys();
  return (
    <Card>
      <CardHeader>
        <CardTitle>Key 绕行</CardTitle>
        <CardDescription>
          可信 Key（自动化管道等）绕开 DLP 检测。全部层 = 含 L1 密钥红线，须改用 /bv1 专用入口；
          按层 = 只跳过勾选的检测层，原入口不变。每次绕行都有审计记录（智能路由 → 日志 → Key 绕行）。
        </CardDescription>
      </CardHeader>
      <CardContent className='space-y-4'>
        <AddForm />
        {isError && <p className='text-sm text-destructive'>名单加载失败</p>}
        <div className='space-y-2'>
          {(data?.keys ?? []).map((k) => (
            <KeyRow key={k.id} k={k} />
          ))}
          {data && data.keys.length === 0 && (
            <p className='text-sm text-muted-foreground'>名单为空——所有 Key 均接受全量检测。</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
