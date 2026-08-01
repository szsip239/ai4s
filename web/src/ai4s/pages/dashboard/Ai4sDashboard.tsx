import { Link } from '@tanstack/react-router';
import { IconBook, IconHeadset, IconShieldQuestion, IconCoin } from '@tabler/icons-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { TotalRequestsCard } from '@/features/dashboard/components/total-requests-card';
import { SuccessRateCard } from '@/features/dashboard/components/success-rate-card';
import { TokenStatsCard } from '@/features/dashboard/components/token-stats-card';
import { TodayRequestsCard } from '@/features/dashboard/components/today-requests-card';
import { DailyRequestStats } from '@/features/dashboard/components/daily-requests-stats';
import { ChannelSuccessRate } from '@/features/dashboard/components/channel-success-rate';

/**
 * ai4s 仪表盘（C 结构高密度看板，issue #11）
 * 主区：四统计卡 + 每日消耗概览；右栏：渠道成功率（现有 GraphQL 数据）+ 公告 + 快捷入口。
 * 图表组件全部复用上游 features/dashboard，仅做版式组合。
 */

const ANNOUNCEMENTS = [
  'OAE-1 起 claudecode-订阅-02 进入维护窗口，期间流量自动切换至其他渠道。',
  '新增 Secrets 正则规则「银行卡号」灰度中，命中后默认 mask。',
];

const QUICK_LINKS = [
  { title: '客户端接入指南', icon: IconBook, href: '/system' },
  { title: '额度申请', icon: IconCoin, href: '/project/api-keys' },
  { title: 'DLP 申诉', icon: IconShieldQuestion, href: '/prompt-protection-rules' },
  { title: '联系网关管理员', icon: IconHeadset, href: '/users' },
];

export default function Ai4sDashboard() {
  return (
    <div className='flex-1 p-6 pt-6'>
      <div className='grid grid-cols-1 gap-6 xl:grid-cols-3'>
        {/* 主区（2/3） */}
        <div className='space-y-6 xl:col-span-2'>
          <div className='grid gap-4 md:grid-cols-2 lg:grid-cols-4'>
            <TotalRequestsCard />
            <SuccessRateCard />
            <TokenStatsCard />
            <TodayRequestsCard />
          </div>
          <Card>
            <CardHeader>
              <CardTitle>每日消耗概览</CardTitle>
            </CardHeader>
            <CardContent className='pl-2'>
              <DailyRequestStats />
            </CardContent>
          </Card>
        </div>

        {/* 右栏（1/3，信息面板） */}
        <aside className='space-y-4 xl:col-span-1'>
          <Card>
            <CardHeader>
              <CardTitle>渠道成功率</CardTitle>
              <CardDescription>各上游渠道近 7 天请求成功率</CardDescription>
            </CardHeader>
            <CardContent>
              <ChannelSuccessRate />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>公告</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className='space-y-2 text-sm text-muted-foreground'>
                {ANNOUNCEMENTS.map((a, i) => (
                  <li key={i} className='leading-relaxed'>
                    · {a}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>快捷入口</CardTitle>
            </CardHeader>
            <CardContent>
              <div className='grid grid-cols-2 gap-2'>
                {QUICK_LINKS.map((l) => (
                  <Link
                    key={l.title}
                    to={l.href}
                    className='flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm transition-colors hover:bg-accent/50'
                  >
                    <l.icon className='size-4 text-primary' />
                    {l.title}
                  </Link>
                ))}
              </div>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
