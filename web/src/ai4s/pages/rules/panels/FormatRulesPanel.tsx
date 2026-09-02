/**
 * 格式规则面板（issue #36）：GET/PUT /dlp-admin/format-rules。
 * 增/删/改均走整体 PUT，保存即渲染 agentgateway 配置并热重载（服务端渲染失败自动回滚 JSON，错误原因 toast）。
 * 表格 + 编辑/新增对话框（patterns 逐行）+ 行内删除（AlertDialog 二次确认）+ 行内 enabled 即改即存；
 * 对话框 dirty（表单已改未保存）经 onDirtyChange 上报（review #2，离开提示同款）。
 * issue #41：UI 不暴露层概念（表格无层列、表单无层选择）；后端契约 layer 字段不变——
 * 提交时按 action 隐式映射（reject→L1、mask→L1.5），任何一次对话框保存都会按 action 重写 layer。
 */
import { useEffect, useState } from 'react';
import { IconLoader2, IconPencil, IconPlus, IconTrash } from '@tabler/icons-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { useFormatRules, usePutFormatRules, type FormatRule } from '../api';
import { Ai4sQueryState } from './QueryState';

/** patterns 数组 ↔ 逐行文本 */
const linesToPatterns = (s: string) => s.split('\n').map((x) => x.trim()).filter(Boolean);
const patternsToLines = (ps: string[]) => ps.join('\n');

/** 编辑/新增共用对话框（rule=null 为新增：code 可编辑并查重预检） */
function Ai4sFormatRuleDialog({
  rule,
  onDirtyChange,
  onClose,
}: {
  rule: FormatRule | null;
  onDirtyChange?: (dirty: boolean) => void;
  onClose: () => void;
}) {
  const { data } = useFormatRules();
  const putRules = usePutFormatRules();
  const isNew = rule === null;
  // 一次性初始快照：dirty = 当前表单与快照有差异（改回初值自动复位 clean）
  const [initial] = useState(() => ({
    code: rule?.code ?? '',
    action: rule?.action ?? ('reject' as FormatRule['action']),
    enabled: rule?.enabled ?? true,
    message: rule?.message ?? '',
    gwText: patternsToLines(rule?.gateway_patterns ?? []),
    shimText: patternsToLines(rule?.shim_patterns ?? []),
  }));
  const [code, setCode] = useState(initial.code);
  const [action, setAction] = useState(initial.action);
  const [enabled, setEnabled] = useState(initial.enabled);
  const [message, setMessage] = useState(initial.message);
  const [gwText, setGwText] = useState(initial.gwText);
  const [shimText, setShimText] = useState(initial.shimText);
  const [formError, setFormError] = useState<string | null>(null);

  const dirty =
    code !== initial.code ||
    action !== initial.action ||
    enabled !== initial.enabled ||
    message !== initial.message ||
    gwText !== initial.gwText ||
    shimText !== initial.shimText;
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  const submit = () => {
    // 客户端预检（服务端权威校验：code 查重、regex 编译、Rust 不支持构造等，失败原因 toast）
    if (!code.trim()) return setFormError('code 不能为空');
    if (isNew && data?.rules.some((r) => r.code === code.trim()))
      return setFormError(`code 与现有规则重复: ${code.trim()}`);
    if (action === 'reject' && !message.trim()) return setFormError('action=reject 时 message 不能为空');
    if (!data) return;
    const next: FormatRule = {
      code: code.trim(),
      // layer 由 action 隐式映射（issue #41，后端契约字段）：reject→L1、mask→L1.5；
      // 编辑既有规则改动作即随之重映射，表单不再暴露层选择
      layer: action === 'reject' ? 'L1' : 'L1.5',
      action,
      enabled,
      gateway_patterns: linesToPatterns(gwText),
      shim_patterns: linesToPatterns(shimText),
      // mask 规则不带 message 字段（服务端 schema：reject 必填、mask 不用）
      ...(action === 'reject' ? { message: message.trim() } : { message: undefined }),
    };
    const doc = isNew
      ? { ...data, rules: [...data.rules, next] }
      : { ...data, rules: data.rules.map((r) => (r.code === rule.code ? next : r)) };
    putRules.mutate(doc, { onSuccess: onClose });
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className='max-w-2xl'>
        <DialogHeader>
          <DialogTitle>{isNew ? '新增格式规则' : `编辑格式规则：${rule.code}`}</DialogTitle>
          <DialogDescription>保存即渲染网关配置并热重载；渲染失败服务端自动回滚</DialogDescription>
        </DialogHeader>
        <div className='grid gap-4'>
          <div className='grid grid-cols-2 gap-4'>
            <div className='space-y-1.5'>
              <Label>code（唯一，如 secrets.openai_sk / pii.phone）</Label>
              <Input
                value={code}
                disabled={!isNew}
                className='font-mono text-xs'
                onChange={(e) => setCode(e.target.value)}
                placeholder='secrets.custom_token'
              />
            </div>
            <div className='space-y-1.5'>
              <Label>启用</Label>
              <div className='pt-1.5'>
                <Switch checked={enabled} onCheckedChange={setEnabled} />
              </div>
            </div>
          </div>
          <div className='space-y-1.5'>
            <Label>动作</Label>
            <Select value={action} onValueChange={(v) => setAction(v as FormatRule['action'])}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='reject'>reject（451 拦截）</SelectItem>
                <SelectItem value='mask'>mask（脱敏放行）</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {action === 'reject' && (
            <div className='space-y-1.5'>
              <Label>message（reject 拦截提示，必填）</Label>
              <Input value={message} onChange={(e) => setMessage(e.target.value)} placeholder='请求含密钥类敏感信息' />
            </div>
          )}
          <div className='space-y-1.5'>
            <Label>gateway_patterns（逐行；渲染进网关，禁 Rust regex 不支持构造）</Label>
            <Textarea rows={4} className='font-mono text-xs' value={gwText} onChange={(e) => setGwText(e.target.value)} />
          </div>
          <div className='space-y-1.5'>
            <Label>shim_patterns（逐行；shim 归一化预检用，可空）</Label>
            <Textarea rows={3} className='font-mono text-xs' value={shimText} onChange={(e) => setShimText(e.target.value)} />
          </div>
          {formError && <p className='text-sm text-destructive'>{formError}</p>}
        </div>
        <DialogFooter>
          <Button variant='outline' onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} disabled={putRules.isPending}>
            {putRules.isPending && <IconLoader2 className='animate-spin' />}
            保存并渲染
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function Ai4sFormatRulesPanel({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const { data, isLoading, error } = useFormatRules();
  const putRules = usePutFormatRules();
  const [dialogOpen, setDialogOpen] = useState<null | 'new' | string>(null); // 'new' 或 editing code
  const [deleting, setDeleting] = useState<FormatRule | null>(null);

  const rules = data?.rules ?? [];
  const editingRule = dialogOpen === 'new' ? null : (rules.find((r) => r.code === dialogOpen) ?? null);

  /** 行内 enabled 开关：即改即存（PUT 整个文档，保存即渲染热重载） */
  const toggle = (rule: FormatRule, checked: boolean) => {
    if (!data) return;
    putRules.mutate({ ...data, rules: rules.map((r) => (r.code === rule.code ? { ...r, enabled: checked } : r)) });
  };

  /** 删除：PUT 整个文档剔除该 code（保存即渲染，规则从网关配置移除） */
  const remove = (rule: FormatRule) => {
    if (!data) return;
    putRules.mutate({ ...data, rules: rules.filter((r) => r.code !== rule.code) });
  };

  return (
    <Card>
      <CardHeader>
        <div className='flex items-center justify-between gap-4'>
          <div>
            <CardTitle>格式规则</CardTitle>
            <CardDescription>
              API 密钥、私钥等格式特征规则命中即在网关拦截（reject），手机号、身份证等 PII 格式规则命中即打码放行（mask）；本层总开关在顶部管线「L1
              格式规则」节点上（关闭则整层撤防，密钥拦截全敞口，网关规则同步撤下）；<span className='font-medium text-foreground'>保存会重写网关配置并热重载</span>。改坏可用
              render 端点重渲染或 .bak 回滚
            </CardDescription>
          </div>
          <div className='flex items-center gap-3'>
            <Button variant='outline' onClick={() => setDialogOpen('new')}>
              <IconPlus />
              新增规则
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <Ai4sQueryState isLoading={isLoading} error={error} errorTitle='格式规则加载失败' rows={4}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>code</TableHead>
                <TableHead>patterns</TableHead>
                <TableHead className='w-24'>动作</TableHead>
                <TableHead className='w-16'>启用</TableHead>
                <TableHead className='w-24'>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rules.map((r) => (
                <TableRow key={r.code}>
                  <TableCell>
                    <code className='text-xs font-medium'>{r.code}</code>
                  </TableCell>
                  <TableCell className='max-w-[320px]'>
                    {r.gateway_patterns.map((p) => (
                      <div key={p}>
                        <code className='text-xs break-all'>{p}</code>
                      </div>
                    ))}
                    {r.shim_patterns.length > 0 && (
                      <div className='text-xs text-muted-foreground'>shim-only +{r.shim_patterns.length} 条</div>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant={r.action === 'reject' ? 'destructive' : 'secondary'}>{r.action}</Badge>
                  </TableCell>
                  <TableCell>
                    <Switch
                      checked={r.enabled}
                      disabled={putRules.isPending}
                      onCheckedChange={(c) => toggle(r, c)}
                    />
                  </TableCell>
                  <TableCell>
                    <div className='flex gap-1'>
                      <Button variant='ghost' size='icon' title='编辑' onClick={() => setDialogOpen(r.code)}>
                        <IconPencil className='size-4' />
                      </Button>
                      <Button variant='ghost' size='icon' title='删除' onClick={() => setDeleting(r)}>
                        <IconTrash className='size-4 text-destructive' />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {rules.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className='text-center text-muted-foreground'>
                    暂无规则
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Ai4sQueryState>
      </CardContent>

      {dialogOpen !== null && (
        <Ai4sFormatRuleDialog
          key={dialogOpen}
          rule={editingRule}
          onDirtyChange={onDirtyChange}
          onClose={() => setDialogOpen(null)}
        />
      )}

      {/* 删除二次确认（删规则=该族 secrets/PII 格式不再拦截，且从网关渲染移除） */}
      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除格式规则「{deleting?.code}」？</AlertDialogTitle>
            <AlertDialogDescription>
              删除后该规则（{deleting?.action}）从 shim 与网关配置同步移除并热重载，对应内容不再被
              {deleting?.action === 'reject' ? '拦截' : '脱敏'}。此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={putRules.isPending}
              onClick={() => deleting && (remove(deleting), setDeleting(null))}
            >
              {putRules.isPending && <IconLoader2 className='animate-spin' />}
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
