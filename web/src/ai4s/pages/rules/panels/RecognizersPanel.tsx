/**
 * PII 规则面板（issue #36）：GET/POST/PUT/DELETE /dlp-admin/recognizers[/<name>]。
 * 编辑/新增走对话框表单（patterns 逐行，name 编辑态锁定——PUT 语义 name 以 URL 为准）；
 * 删除二次确认（AlertDialog）。结构同 recognizers/pii-zh.json，命中走 Presidio mask。
 * 对话框 dirty（表单已改未保存）经 onDirtyChange 上报（review #2，与词表/settings 同款离开提示）。
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import {
  useDeleteRecognizer,
  useRecognizers,
  useSaveRecognizer,
  type Recognizer,
  type RecognizerPattern,
} from '../api';
import { Ai4sQueryState } from './QueryState';

/** context 输入 ↔ 数组：中文分隔符/逗号皆可 */
const ctxToText = (ctx: string[]) => ctx.join('、');
const textToCtx = (s: string) =>
  s
    .split(/[,，、]/)
    .map((x) => x.trim())
    .filter(Boolean);

/** 对话框内表单（key=editing name 重置内部状态；保存由父级 mutation 完成后关窗） */
function Ai4sRecognizerFormDialog({
  editing,
  onDirtyChange,
  onClose,
}: {
  editing: Recognizer | null; // null=新增
  onDirtyChange?: (dirty: boolean) => void;
  onClose: () => void;
}) {
  const save = useSaveRecognizer();
  const isNew = editing === null;
  // 一次性初始快照：dirty = 当前表单与快照有差异（改回初值自动复位 clean）
  const [initial] = useState(() => ({
    name: editing?.name ?? '',
    entity: editing?.entity ?? '',
    replacement: editing?.replacement ?? '',
    ctxText: ctxToText(editing?.context ?? []),
    patterns: editing?.patterns.map((p) => ({ ...p })) ?? [{ name: '', regex: '', score: 0.7 }],
  }));
  const [name, setName] = useState(initial.name);
  const [entity, setEntity] = useState(initial.entity);
  const [replacement, setReplacement] = useState(initial.replacement);
  const [ctxText, setCtxText] = useState(initial.ctxText);
  const [patterns, setPatterns] = useState<RecognizerPattern[]>(initial.patterns);
  const [formError, setFormError] = useState<string | null>(null);

  const dirty =
    name !== initial.name ||
    entity !== initial.entity ||
    replacement !== initial.replacement ||
    ctxText !== initial.ctxText ||
    JSON.stringify(patterns) !== JSON.stringify(initial.patterns);
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  const submit = () => {
    // 客户端预检（服务端权威校验：regex 过 re.compile、score 0~1 等，失败原因 toast 展示）
    if (!name.trim()) return setFormError('name 不能为空');
    if (!entity.trim()) return setFormError('entity 不能为空');
    if (!replacement.trim()) return setFormError('replacement 不能为空');
    for (let i = 0; i < patterns.length; i++) {
      const p = patterns[i];
      if (!p.name.trim() || !p.regex.trim()) return setFormError(`patterns 第 ${i + 1} 行：name/regex 不能为空`);
      if (!(p.score >= 0 && p.score <= 1)) return setFormError(`patterns 第 ${i + 1} 行：score 须在 0~1`);
    }
    const rec: Recognizer = {
      name: name.trim(),
      entity: entity.trim(),
      patterns: patterns.map((p) => ({ name: p.name.trim(), regex: p.regex, score: p.score })),
      context: textToCtx(ctxText),
      replacement: replacement.trim(),
    };
    save.mutate(
      { rec, originalName: isNew ? null : editing.name },
      { onSuccess: onClose }
    );
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className='max-w-2xl'>
        <DialogHeader>
          <DialogTitle>{isNew ? '新增 PII 规则' : `编辑 PII 规则：${editing.name}`}</DialogTitle>
          <DialogDescription>经 shim 以 Presidio ad-hoc recognizer 注入，保存即热更新</DialogDescription>
        </DialogHeader>
        <div className='grid gap-4'>
          <div className='grid grid-cols-2 gap-4'>
            <div className='space-y-1.5'>
              <Label>name</Label>
              <Input value={name} disabled={!isNew} onChange={(e) => setName(e.target.value)} placeholder='zh_phone' />
            </div>
            <div className='space-y-1.5'>
              <Label>entity</Label>
              <Input value={entity} onChange={(e) => setEntity(e.target.value)} placeholder='ZH_PHONE' />
            </div>
          </div>
          <div className='grid grid-cols-2 gap-4'>
            <div className='space-y-1.5'>
              <Label>replacement（命中替换文本）</Label>
              <Input value={replacement} onChange={(e) => setReplacement(e.target.value)} placeholder='【PII:手机号】' />
            </div>
            <div className='space-y-1.5'>
              <Label>context（逗号/顿号分隔，可空）</Label>
              <Input value={ctxText} onChange={(e) => setCtxText(e.target.value)} placeholder='手机、电话、手机号' />
            </div>
          </div>
          <div className='space-y-2'>
            <Label>patterns（逐行；regex 须过 re.compile，score 0~1）</Label>
            {patterns.map((p, i) => (
              <div key={i} className='flex items-center gap-2'>
                <Input
                  className='w-40 font-mono text-xs'
                  value={p.name}
                  placeholder='name'
                  onChange={(e) => setPatterns(patterns.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
                />
                <Input
                  className='flex-1 font-mono text-xs'
                  value={p.regex}
                  placeholder='regex'
                  onChange={(e) => setPatterns(patterns.map((x, j) => (j === i ? { ...x, regex: e.target.value } : x)))}
                />
                <Input
                  className='w-24 font-mono text-xs'
                  type='number'
                  step='0.05'
                  min='0'
                  max='1'
                  value={p.score}
                  onChange={(e) =>
                    setPatterns(patterns.map((x, j) => (j === i ? { ...x, score: Number(e.target.value) } : x)))
                  }
                />
                <Button
                  variant='ghost'
                  size='icon'
                  title='删除该行'
                  disabled={patterns.length <= 1}
                  onClick={() => setPatterns(patterns.filter((_, j) => j !== i))}
                >
                  <IconTrash className='size-4 text-destructive' />
                </Button>
              </div>
            ))}
            <Button
              variant='outline'
              size='sm'
              onClick={() => setPatterns([...patterns, { name: '', regex: '', score: 0.7 }])}
            >
              <IconPlus />
              添加 pattern
            </Button>
          </div>
          {formError && <p className='text-sm text-destructive'>{formError}</p>}
        </div>
        <DialogFooter>
          <Button variant='outline' onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} disabled={save.isPending}>
            {save.isPending && <IconLoader2 className='animate-spin' />}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function Ai4sRecognizersPanel({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const { data, isLoading, error } = useRecognizers();
  const del = useDeleteRecognizer();
  const [dialogOpen, setDialogOpen] = useState<null | 'new' | string>(null); // 'new' 或 editing name
  const [deleting, setDeleting] = useState<Recognizer | null>(null);

  const recs = data?.recognizers ?? [];
  const editingRec = dialogOpen === 'new' ? null : (recs.find((r) => r.name === dialogOpen) ?? null);

  return (
    <Card>
      <CardHeader>
        <div className='flex items-center justify-between gap-4'>
          <div>
            <CardTitle>PII 规则</CardTitle>
            <CardDescription>L2 Presidio ad-hoc recognizer（结构同 recognizers/pii-zh.json），保存即热更新</CardDescription>
          </div>
          <Button variant='outline' onClick={() => setDialogOpen('new')}>
            <IconPlus />
            新增规则
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <Ai4sQueryState isLoading={isLoading} error={error} errorTitle='PII 规则加载失败'>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>名称</TableHead>
                <TableHead>regex</TableHead>
                <TableHead className='w-16'>score</TableHead>
                <TableHead className='w-36'>替换</TableHead>
                <TableHead className='w-24'>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {recs.map((r) => (
                <TableRow key={r.name}>
                  <TableCell>
                    <div className='font-medium'>{r.name}</div>
                    <div className='text-xs text-muted-foreground'>
                      {r.entity}
                      {r.context.length > 0 && ` · 上下文：${r.context.join(' / ')}`}
                    </div>
                  </TableCell>
                  <TableCell className='max-w-[280px]'>
                    <code className='text-xs break-all'>{r.patterns[0]?.regex}</code>
                    {r.patterns.length > 1 && (
                      <span className='text-xs text-muted-foreground'>（+{r.patterns.length - 1} 条）</span>
                    )}
                  </TableCell>
                  <TableCell className='font-mono'>{r.patterns[0]?.score.toFixed(2)}</TableCell>
                  <TableCell>
                    <code className='text-xs'>{r.replacement}</code>
                  </TableCell>
                  <TableCell>
                    <div className='flex gap-1'>
                      <Button variant='ghost' size='icon' title='编辑' onClick={() => setDialogOpen(r.name)}>
                        <IconPencil className='size-4' />
                      </Button>
                      <Button variant='ghost' size='icon' title='删除' onClick={() => setDeleting(r)}>
                        <IconTrash className='size-4 text-destructive' />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {recs.length === 0 && (
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
        <Ai4sRecognizerFormDialog
          key={dialogOpen}
          editing={editingRec}
          onDirtyChange={onDirtyChange}
          onClose={() => setDialogOpen(null)}
        />
      )}

      {/* 删除二次确认（DLP 配置误删=检测失效） */}
      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除 PII 规则「{deleting?.name}」？</AlertDialogTitle>
            <AlertDialogDescription>
              删除后该类 PII（{deleting?.entity}）请求侧不再脱敏，立即热生效。此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              disabled={del.isPending}
              onClick={() => deleting && del.mutate(deleting.name, { onSuccess: () => setDeleting(null) })}
            >
              {del.isPending && <IconLoader2 className='animate-spin' />}
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
