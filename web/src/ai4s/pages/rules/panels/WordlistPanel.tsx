/**
 * 商密词表面板（issue #36）：GET/PUT /dlp-admin/wordlist。
 * 行内增删编辑本地草稿，保存时 PUT 整体替换（服务端保留 version/_comment）；
 * edited===null 表示无本地草稿（直接展示服务端数据），有草稿即 dirty（供离开提示）。
 */
import { useEffect, useState } from 'react';
import { IconLoader2, IconPlus, IconTrash } from '@tabler/icons-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { usePutWordlist, useWordlist, type WordlistTerm } from '../api';
import { Ai4sQueryState } from './QueryState';

export function Ai4sWordlistPanel({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) {
  const { data, isLoading, error } = useWordlist();
  const putWordlist = usePutWordlist();
  const [edited, setEdited] = useState<WordlistTerm[] | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const dirty = edited !== null;
  useEffect(() => {
    onDirtyChange?.(dirty);
    return () => onDirtyChange?.(false);
  }, [dirty, onDirtyChange]);

  const terms = edited ?? data?.terms ?? [];

  const mutate = (next: WordlistTerm[]) => {
    setFormError(null);
    setEdited(next);
  };

  const save = () => {
    // 客户端预检（服务端同款校验：非空/去重），省一次往返；失败原因仍以 API 为准
    for (let i = 0; i < terms.length; i++) {
      if (!terms[i].value.trim() || !terms[i].rule_id.trim()) {
        setFormError(`第 ${i + 1} 行：词条与 rule_id 均不能为空`);
        return;
      }
      if (terms.findIndex((t) => t.value === terms[i].value) !== i) {
        setFormError(`第 ${i + 1} 行：词条 "${terms[i].value}" 重复`);
        return;
      }
    }
    putWordlist.mutate(
      terms.map((t) => ({ value: t.value.trim(), rule_id: t.rule_id.trim() })),
      { onSuccess: () => setEdited(null) }
    );
  };

  return (
    <Card>
      <CardHeader>
        <div className='flex items-center justify-between gap-4'>
          <div>
            <CardTitle>商密词表</CardTitle>
            <CardDescription>L2 webhook 词表：命中即 reject（归一化预检同查）；保存即热生效，无需重启</CardDescription>
          </div>
          <Badge variant='secondary'>{terms.length} 词</Badge>
        </div>
      </CardHeader>
      <CardContent className='space-y-4'>
        <Ai4sQueryState isLoading={isLoading} error={error} errorTitle='词表加载失败'>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>词条</TableHead>
                <TableHead className='w-72'>rule_id</TableHead>
                <TableHead className='w-16'>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {terms.map((t, i) => (
                <TableRow key={i}>
                  <TableCell>
                    <Input
                      value={t.value}
                      onChange={(e) => mutate(terms.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))}
                      placeholder='商密词（支持中文）'
                    />
                  </TableCell>
                  <TableCell>
                    <Input
                      value={t.rule_id}
                      className='font-mono text-xs'
                      onChange={(e) => mutate(terms.map((x, j) => (j === i ? { ...x, rule_id: e.target.value } : x)))}
                      placeholder='confidential.xxx'
                    />
                  </TableCell>
                  <TableCell>
                    <Button
                      variant='ghost'
                      size='icon'
                      title='删除该行'
                      onClick={() => mutate(terms.filter((_, j) => j !== i))}
                    >
                      <IconTrash className='size-4 text-destructive' />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {terms.length === 0 && (
                <TableRow>
                  <TableCell colSpan={3} className='text-center text-muted-foreground'>
                    词表为空（保存后生效）
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
          <div className='flex items-center justify-between gap-4'>
            <Button
              variant='outline'
              onClick={() => mutate([...terms, { value: '', rule_id: 'confidential.' }])}
            >
              <IconPlus />
              添加词
            </Button>
            <div className='flex items-center gap-3'>
              {formError && <span className='text-sm text-destructive'>{formError}</span>}
              {dirty && !formError && <span className='text-sm text-amber-600'>有未保存修改</span>}
              <Button onClick={save} disabled={!dirty || putWordlist.isPending}>
                {putWordlist.isPending && <IconLoader2 className='animate-spin' />}
                保存
              </Button>
            </div>
          </div>
        </Ai4sQueryState>
      </CardContent>
    </Card>
  );
}
