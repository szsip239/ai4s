import { useMemo } from 'react';
import { IconShieldCheck } from '@tabler/icons-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useQueryPromptProtectionRules } from '@/features/prompt-protection-rules/data/rules';
import { Header } from '@/components/layout/header';
import { Main } from '@/components/layout/main';

/**
 * ai4s 脱敏规则页（issue #13，link-ai 式规则表）
 * 列：优先级 / 名称 / 类型 / 匹配内容 / 动作 / 状态。
 * 「类型」与「优先级」为展示层派生（上游模型无此字段，缺失项已记录并转能力归属判定）：
 *  - 类型：按规则名前缀映射（secrets- / confidential- / pii-）
 *  - 优先级：列表顺序（编号越小越先评估）；多层命中时与 agentgateway 的评估顺序一致维护
 */

const TYPE_MAP: [RegExp, { label: string; variant: 'destructive' | 'secondary' | 'outline' }][] = [
  [/^secrets-/i, { label: 'Secrets', variant: 'destructive' }],
  [/^confidential-/i, { label: '商密词表', variant: 'secondary' }],
  [/^pii-/i, { label: 'PII', variant: 'outline' }],
];

function ruleType(name: string) {
  for (const [re, v] of TYPE_MAP) if (re.test(name)) return v;
  return { label: '其他', variant: 'outline' as const };
}

export default function Ai4sRulesPage() {
  const { data, isLoading } = useQueryPromptProtectionRules({ first: 100 });
  const rules = useMemo(() => data?.edges?.map((e: any) => e.node) ?? [], [data]);

  return (
    <>
      <Header title="脱敏规则" />
      <Main>
        <Alert className='mb-4 border-primary/30 bg-accent text-foreground'>
          <IconShieldCheck className='size-4' />
          <AlertTitle>双层 DLP</AlertTitle>
          <AlertDescription>
            主层在 agentgateway（Secrets regex + 商密词表 webhook → Presidio）；本页为 axonhub 纵深层规则（reject/mask）。
            类型与优先级为展示层派生，与主层评估顺序保持一致维护。
          </AlertDescription>
        </Alert>

        <Card>
          <CardHeader>
            <CardTitle>规则列表</CardTitle>
            <CardDescription>命中即按动作处置；维护改词表请同步检查 agentgateway 主层</CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className='space-y-2'>
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className='h-10 w-full' />
                ))}
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className='w-16'>优先级</TableHead>
                    <TableHead>名称</TableHead>
                    <TableHead className='w-28'>类型</TableHead>
                    <TableHead>匹配内容</TableHead>
                    <TableHead className='w-24'>动作</TableHead>
                    <TableHead className='w-24'>状态</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rules.map((r: any, idx: number) => {
                    const t = ruleType(r.name);
                    return (
                      <TableRow key={r.id}>
                        <TableCell className='font-mono text-muted-foreground'>{String(idx + 1).padStart(2, '0')}</TableCell>
                        <TableCell>
                          <div className='font-medium'>{r.name}</div>
                          {r.description && <div className='text-xs text-muted-foreground'>{r.description}</div>}
                        </TableCell>
                        <TableCell>
                          <Badge variant={t.variant}>{t.label}</Badge>
                        </TableCell>
                        <TableCell className='max-w-[320px]'>
                          <code className='text-xs break-all'>{r.pattern}</code>
                        </TableCell>
                        <TableCell>
                          <Badge variant={r.settings?.action === 'reject' ? 'destructive' : 'secondary'}>
                            {r.settings?.action === 'reject' ? '拒绝' : '脱敏'}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <Badge variant={r.status === 'enabled' ? 'default' : 'outline'}>
                            {r.status === 'enabled' ? '启用' : r.status === 'disabled' ? '停用' : '归档'}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {rules.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={6} className='text-center text-muted-foreground'>
                        暂无规则
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </Main>
    </>
  );
}
