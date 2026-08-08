/**
 * EDM 语料面板（issue #36）：GET/POST/DELETE /dlp-admin/edm/corpus[/<name>]。
 * 上传 = name + 粘贴全文（服务端指纹化入库，shingle+行级双通道，原文只存单向哈希轮廓）；
 * 删除二次确认（指纹库与 corpus 文件同步移除）。
 * 上传对话框 dirty（已输入未提交）经 onDirtyChange 上报（review #2，离开提示同款）。
 */
import { useEffect, useState } from 'react';
import { IconLoader2, IconTrash, IconUpload } from '@tabler/icons-react';
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
import { Textarea } from '@/components/ui/textarea';
import { useDeleteEdmDoc, useEdmCorpus, useUploadEdmDoc, type EdmDocSummary } from '../api';
import { Ai4sQueryState } from './QueryState';

/** 语料名约束（与服务端 _EDM_NAME_RE 同款）：[A-Za-z0-9_.-]{1,64} */
const NAME_RE = /^[A-Za-z0-9_.-]{1,64}$/;

function Ai4sEdmUploadDialog({
  onDirtyChange,
  onClose,
}: {
  onDirtyChange?: (dirty: boolean) => void;
  onClose: () => void;
}) {
  const upload = useUploadEdmDoc();
  const [name, setName] = useState('');
  const [text, setText] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  // dirty = 任一字段已有输入（清空即复位 clean）
  const dirty = name !== '' || text !== '';
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  const submit = () => {
    // 客户端预检（服务端权威校验：归一化后 <12 字符拒收——入库即死规则）
    if (!NAME_RE.test(name)) return setFormError('name 须为 [A-Za-z0-9_.-]（1~64 字符）');
    if (!text.trim()) return setFormError('请粘贴文档全文');
    upload.mutate({ name, text }, { onSuccess: onClose });
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className='max-w-2xl'>
        <DialogHeader>
          <DialogTitle>上传语料文档</DialogTitle>
          <DialogDescription>语料原文即商密文档，上传即指纹化（SHA-256 单向存储，不存原文哈希外内容）</DialogDescription>
        </DialogHeader>
        <div className='grid gap-4'>
          <div className='space-y-1.5'>
            <Label>文档名（[A-Za-z0-9_.-]，1~64 字符）</Label>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder='contract-2026-q3' />
          </div>
          <div className='space-y-1.5'>
            <Label>文档全文（粘贴文本；过短或乱序后不足 12 字符归一化长度将被拒收）</Label>
            <Textarea
              rows={12}
              className='font-mono text-xs'
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder='粘贴商密文档全文…'
            />
          </div>
          {formError && <p className='text-sm text-destructive'>{formError}</p>}
        </div>
        <DialogFooter>
          <Button variant='outline' onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} disabled={upload.isPending}>
            {upload.isPending && <IconLoader2 className='animate-spin' />}
            上传并指纹化
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function Ai4sEdmCorpusPanel({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const { data, isLoading, error } = useEdmCorpus();
  const del = useDeleteEdmDoc();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [deleting, setDeleting] = useState<EdmDocSummary | null>(null);

  const docs = data ?? [];

  return (
    <Card>
      <CardHeader>
        <div className='flex items-center justify-between gap-4'>
          <div>
            <CardTitle>EDM 语料</CardTitle>
            <CardDescription>
              L3 精确数据匹配：整段粘贴商密文档命中阈值即 451；语料原文即商密文档，上传即指纹化
            </CardDescription>
          </div>
          <Button variant='outline' onClick={() => setUploadOpen(true)}>
            <IconUpload />
            上传语料文档
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <Ai4sQueryState isLoading={isLoading} error={error} errorTitle='语料列表加载失败' rows={2}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>文档</TableHead>
                <TableHead className='w-28'>shingle 数</TableHead>
                <TableHead className='w-28'>行级数</TableHead>
                <TableHead className='w-40'>入库时间</TableHead>
                <TableHead className='w-16'>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {docs.map((d) => (
                <TableRow key={d.name}>
                  <TableCell className='font-medium'>{d.name}</TableCell>
                  <TableCell className='font-mono'>{d.shingle_count}</TableCell>
                  <TableCell className='font-mono'>{d.line_count}</TableCell>
                  <TableCell className='text-muted-foreground'>{d.added_at ?? '—（旧格式）'}</TableCell>
                  <TableCell>
                    <Button variant='ghost' size='icon' title='删除' onClick={() => setDeleting(d)}>
                      <IconTrash className='size-4 text-destructive' />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {docs.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className='text-center text-muted-foreground'>
                    暂无语料文档
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Ai4sQueryState>
      </CardContent>

      {uploadOpen && <Ai4sEdmUploadDialog onDirtyChange={onDirtyChange} onClose={() => setUploadOpen(false)} />}

      {/* 删除二次确认（删语料=该文档不再受 L3 保护） */}
      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除语料「{deleting?.name}」？</AlertDialogTitle>
            <AlertDialogDescription>
              该文档的全部指纹（shingle {deleting?.shingle_count} / 行级 {deleting?.line_count}）与 corpus
              原文文件将同步删除，删除后整段粘贴此文档不再被 L3 拦截。此操作不可撤销。
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
