/**
 * 员工侧 Key 配置指南（issue #82）：内嵌「我的 Key」页底部的折叠卡。
 * 内容：接入地址（origin 自适应）/ 模型名 / curl 与 OpenAI SDK、Claude Code 类示例 /
 * 额度档位语义（docs/contracts/quota-tiers.md 为准）/ FAQ（401/403/申请入口）。
 * 员工零 scope 可看（本页即员工页，无额外权限门）。
 */
import { useState } from 'react';
import { IconBook, IconChevronDown } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { GUIDE_MODELS, buildGuideSnippets } from './key-guide';

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h4 className='text-sm font-medium'>{children}</h4>;
}

function Snippet({ code }: { code: string }) {
  return (
    <pre className='bg-muted overflow-x-auto rounded-md p-3 text-xs leading-relaxed'>
      <code>{code}</code>
    </pre>
  );
}

export function KeyGuide() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  // origin 只取这一次：URL 组合（origin→base→片段）全在 key-guide.ts 纯数据层
  const snippets = buildGuideSnippets(window.location.origin);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Card className='mt-4'>
        <CardHeader>
          <div className='flex items-start justify-between gap-3'>
            <div>
              <CardTitle className='flex items-center gap-2'>
                <IconBook className='h-5 w-5' />
                {t('ai4s.myKeys.guide.title')}
              </CardTitle>
              <CardDescription>{t('ai4s.myKeys.guide.description')}</CardDescription>
            </div>
            {/* trigger 落真实 button（ai-elements/plan.tsx 惯例）：保留 button 语义与键盘可达 */}
            <CollapsibleTrigger asChild>
              <Button variant='ghost' size='icon' aria-label={t('ai4s.myKeys.guide.toggle')}>
                <IconChevronDown className={cn('h-4 w-4 transition-transform', open && 'rotate-180')} />
              </Button>
            </CollapsibleTrigger>
          </div>
        </CardHeader>
        <CollapsibleContent>
          <CardContent className='space-y-5'>
            <section className='space-y-1.5'>
              <SectionTitle>{t('ai4s.myKeys.guide.endpoint.title')}</SectionTitle>
              <p className='text-muted-foreground text-sm'>
                {t('ai4s.myKeys.guide.endpoint.api')} <code className='text-xs'>{snippets.base}</code>
              </p>
              <p className='text-muted-foreground text-sm'>{t('ai4s.myKeys.guide.endpoint.key')}</p>
            </section>

            <section className='space-y-1.5'>
              <SectionTitle>{t('ai4s.myKeys.guide.models.title')}</SectionTitle>
              <p className='text-muted-foreground text-sm'>{t('ai4s.myKeys.guide.models.line')}</p>
              <ul className='text-muted-foreground list-disc space-y-1 pl-5 text-sm'>
                {GUIDE_MODELS.map((m) => (
                  <li key={m.name}>
                    <code className='text-xs'>{m.name}</code> — {t(`ai4s.myKeys.guide.model.${m.noteKey}`)}
                  </li>
                ))}
              </ul>
            </section>

            <section className='space-y-2'>
              <SectionTitle>{t('ai4s.myKeys.guide.examples.title')}</SectionTitle>
              <p className='text-muted-foreground text-sm font-medium'>{t('ai4s.myKeys.guide.examples.curl')}</p>
              <Snippet code={snippets.curl} />
              <p className='text-muted-foreground text-sm font-medium'>{t('ai4s.myKeys.guide.examples.python')}</p>
              <Snippet code={snippets.python} />
              <p className='text-muted-foreground text-sm font-medium'>{t('ai4s.myKeys.guide.examples.claudeCode')}</p>
              <Snippet code={snippets.claudeCode} />
              <p className='text-muted-foreground text-sm'>{t('ai4s.myKeys.guide.examples.note')}</p>
            </section>

            <section className='space-y-1.5'>
              <SectionTitle>{t('ai4s.myKeys.guide.tiers.title')}</SectionTitle>
              <p className='text-muted-foreground text-sm'>{t('ai4s.myKeys.guide.tiers.line')}</p>
              <ul className='text-muted-foreground list-disc space-y-1 pl-5 text-sm'>
                <li>{t('ai4s.myKeys.guide.tiers.trial')}</li>
                <li>{t('ai4s.myKeys.guide.tiers.standard')}</li>
                <li>{t('ai4s.myKeys.guide.tiers.premium')}</li>
              </ul>
            </section>

            <section className='space-y-2'>
              <SectionTitle>{t('ai4s.myKeys.guide.faq.title')}</SectionTitle>
              {(['401', '403', 'apply'] as const).map((k) => (
                <div key={k}>
                  <p className='text-sm font-medium'>{t(`ai4s.myKeys.guide.faq.${k}q`)}</p>
                  <p className='text-muted-foreground text-sm'>{t(`ai4s.myKeys.guide.faq.${k}a`)}</p>
                </div>
              ))}
            </section>
          </CardContent>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  );
}
