/**
 * EDM 语料面板（issue #36）：GET/POST/DELETE /dlp-admin/edm/corpus[/<name>]。
 * 上传 = name + 全文（issue #47 起支持文件读入 .txt/.md/.text/.log 或直接粘贴；issue #48 起支持
 * .pdf/.docx/.xlsx/.pptx 直传、issue #50 起支持 .png/.jpg/.jpeg/.bmp/.tiff 图片与扫描 PDF——
 * 服务端解析/OCR 提取文本后指纹化，前端不预览；服务端指纹化入库，
 * shingle+行级双通道，原文只存单向哈希轮廓）；删除二次确认（指纹库与 corpus 文件同步移除）。
 * 上传对话框 dirty（已输入未提交）经 onDirtyChange 上报（review #2，离开提示同款）。
 */
import { useEffect, useRef, useState } from 'react';
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
import { useDeleteEdmDoc, useEdmCorpus, useUploadEdmDoc, useUploadEdmFile, type EdmDocSummary } from '../api';
import { Ai4sQueryState } from './QueryState';

/** 语料名约束（与服务端 _EDM_NAME_RE 同款）：[A-Za-z0-9_.-]{1,64} */
const NAME_RE = /^[A-Za-z0-9_.-]{1,64}$/;
/** 单文件上限：对齐服务端 EDM corpus POST 体上限（shim admin_api.py _MAX_EDM_BODY = 16MB） */
const MAX_FILE_BYTES = 16 * 1024 * 1024;
/** 文本类扩展名粗判（前端 FileReader 读入，可预览可编辑）；file.type 为 text/* 亦可（如 .markdown 扩展名） */
const TEXT_EXT_RE = /\.(txt|md|text|log)$/i;
/** 直传服务端解析的格式（issue #48 文档 + issue #50 图片 OCR），前端不预览 */
const DIRECT_UPLOAD_EXT_RE = /\.(pdf|docx|xlsx|pptx|png|jpe?g|bmp|tiff?)$/i;
/** 明确拒绝并给出指引的两类（与服务端报错同款文案，前端先拦少一次往返） */
const LEGACY_DOC_EXT_RE = /\.doc$/i;
const GIF_WEBP_EXT_RE = /\.(gif|webp)$/i;

function Ai4sEdmUploadDialog({
  onDirtyChange,
  onClose,
}: {
  onDirtyChange?: (dirty: boolean) => void;
  onClose: () => void;
}) {
  const upload = useUploadEdmDoc();
  const uploadFile = useUploadEdmFile();
  const [name, setName] = useState('');
  const [text, setText] = useState('');
  // Office 文件（issue #48）：选中后直传服务端解析，不读入预览；与 text 互斥
  const [file, setFile] = useState<File | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  // 文件名自动带入的最近值：用户手改过 name 后不再覆盖
  const nameAutoRef = useRef<string | null>(null);

  // dirty = 任一字段已有输入（清空即复位 clean；文件读入/选中同样算 dirty）
  const dirty = name !== '' || text !== '' || file !== null;
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  /** 文档名自动带入：读文件/选文件期间用户手输 name 不被过期闭包覆盖；nameAutoRef 仅在真正带入时更新 */
  const autoFillName = (fileName: string) => {
    const base = fileName.replace(/\.[^.]+$/, '');
    setName((prev) => {
      if (prev === '' || prev === nameAutoRef.current) {
        nameAutoRef.current = base;
        return base;
      }
      return prev;
    });
  };

  /**
   * 文件选择：文本类（issue #47）FileReader 读全文填入文本域（保持可预览/可编辑，GBK 护栏不回退）；
   * Office 文档（issue #48）不读入，记下 File 对象提交时直传服务端解析；.doc/图片前端先拦（同服务端文案）
   */
  const onFilePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0];
    if (!picked) return;
    if (LEGACY_DOC_EXT_RE.test(picked.name)) {
      setFormError(`「${picked.name}」是老式 .doc（二进制格式），暂不支持：请在 Word/WPS 中另存为 .docx 后上传`);
      e.target.value = ''; // 复位，允许重选同一文件
      return;
    }
    if (GIF_WEBP_EXT_RE.test(picked.name)) {
      setFormError(`「${picked.name}」是 GIF/WebP 图片，暂不支持：请转 PNG/JPG 后上传`);
      e.target.value = '';
      return;
    }
    const isText = TEXT_EXT_RE.test(picked.name) || picked.type.startsWith('text/');
    if (!isText && !DIRECT_UPLOAD_EXT_RE.test(picked.name)) {
      setFormError(
        `「${picked.name}」不是支持的文件类型：支持 .txt/.md/.text/.log 纯文本、.pdf/.docx/.xlsx/.pptx 文档与 .png/.jpg/.jpeg/.bmp/.tiff 图片`,
      );
      e.target.value = '';
      return;
    }
    if (picked.size > MAX_FILE_BYTES) {
      setFormError(
        `「${picked.name}」大小 ${(picked.size / 1024 / 1024).toFixed(1)}MB 超过约 16MB 上限，请拆分文档或改用 CLI scripts/edm-add.py 入库`,
      );
      e.target.value = '';
      return;
    }
    if (!isText) {
      // Office 直传路径：不预览，清空已粘贴文本（互斥），文档名自动带入（可手改）
      e.target.value = '';
      setFile(picked);
      setText('');
      setFormError(null);
      autoFillName(picked.name);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      e.target.value = ''; // 读完即复位（成功/乱码路径一致），允许重选同一文件
      const content = typeof reader.result === 'string' ? reader.result : '';
      // GBK 等非 UTF-8 护栏：readAsText 按 UTF-8 解码产生 U+FFFD；乱码入库即成永不命中的死规则，前端拦住
      if (content.includes('\uFFFD')) {
        setFormError(`「${picked.name}」检测到乱码字符，文件可能不是 UTF-8 编码，请另存为 UTF-8 后再传`);
        return; // 不填入，name/text 保持原值
      }
      setFile(null); // 文本读入与 Office 直传互斥
      setText(content);
      setFormError(null);
      autoFillName(picked.name);
    };
    reader.onerror = () => {
      e.target.value = '';
      setFormError('文件读取失败，请重试或改用粘贴');
    };
    reader.readAsText(picked);
  };

  const submit = () => {
    // 客户端预检（服务端权威校验：归一化后 <12 字符拒收——入库即死规则）
    if (!NAME_RE.test(name)) return setFormError('name 须为 [A-Za-z0-9_.-]（1~64 字符）');
    if (file) return uploadFile.mutate({ name, file }, { onSuccess: onClose });
    if (!text.trim()) return setFormError('请粘贴全文或从文件读入');
    upload.mutate({ name, text }, { onSuccess: onClose });
  };

  const pending = upload.isPending || uploadFile.isPending;

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className='max-w-2xl'>
        <DialogHeader>
          <DialogTitle>上传语料文档</DialogTitle>
          <DialogDescription>语料原文即商密文档，上传即指纹化（SHA-256 单向存储，不存原文哈希外内容）</DialogDescription>
        </DialogHeader>
        <div className='grid gap-4'>
          <div className='space-y-1.5'>
            <Label htmlFor='edm-upload-file'>从文件读入（可选）</Label>
            <Input
              id='edm-upload-file'
              type='file'
              accept='.txt,.md,.text,.log,.pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.bmp,.tiff,.tif,text/plain,text/markdown,image/png,image/jpeg,image/bmp,image/tiff'
              onChange={onFilePick}
            />
            <p className='text-xs text-muted-foreground'>
              支持 .txt/.md/.text/.log 纯文本（读入后自动填入下方全文与文档名，可再编辑）、.pdf/.docx/.xlsx/.pptx
              文档与 .png/.jpg/.jpeg/.bmp/.tiff 图片（服务端解析，扫描件/图片走本地 OCR：中文印刷体识别一般、
              手写体差、表格版面会丢失，均不预览）；单文件 ≤ 16MB
            </p>
          </div>
          <div className='space-y-1.5'>
            <Label>文档名（[A-Za-z0-9_.-]，1~64 字符）</Label>
            <Input
              value={name}
              onChange={(e) => {
                nameAutoRef.current = null;
                setName(e.target.value);
              }}
              placeholder='contract-2026-q3'
            />
          </div>
          {file ? (
            <div className='flex items-center justify-between rounded-md border px-3 py-2 text-sm'>
              <span>
                已选文件 <span className='font-medium'>{file.name}</span>
                <span className='text-muted-foreground'>（{(file.size / 1024).toFixed(0)}KB，服务端解析提取文本后指纹化）</span>
              </span>
              <Button variant='ghost' size='sm' onClick={() => setFile(null)}>
                移除，改粘贴文本
              </Button>
            </div>
          ) : (
            <div className='space-y-1.5'>
              <Label>文档全文（粘贴文本或从文件读入；过短或乱序后不足 12 字符归一化长度将被拒收）</Label>
              <Textarea
                rows={12}
                className='font-mono text-xs'
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder='粘贴商密文档全文，或从上方选择文件读入…'
              />
            </div>
          )}
          {formError && <p className='text-sm text-destructive'>{formError}</p>}
        </div>
        <DialogFooter>
          <Button variant='outline' onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} disabled={pending}>
            {pending && <IconLoader2 className='animate-spin' />}
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
              整份商密文档的指纹库，防整段粘贴外发（命中阈值即 451）；新商密文档纳入管理时上传（上传即指纹化），文档作废时删除。语料不入库，误删只能重新上传原文
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
