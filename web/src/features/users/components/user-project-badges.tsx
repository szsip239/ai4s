import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Badge } from '@/components/ui/badge';
import { useProjects } from '@/features/projects/data/projects';
import { User } from '../data/schema';

interface UserProjectBadgesProps {
  user: User;
}

// issue #135：人员列表「项目归属」单元格——渲染用户已加入项目的名称徽章，
// 项目所有者身份加 owner 标识；id→name 映射走 useProjects 的 React Query 缓存（同 key 只发一次请求）
export function UserProjectBadges({ user }: UserProjectBadgesProps) {
  const { t } = useTranslation();
  const { data: projectsData } = useProjects({ first: 100 });

  const projectNameById = useMemo(() => {
    const map = new Map<string, string>();
    projectsData?.edges?.forEach((edge) => map.set(edge.node.id, edge.node.name));
    return map;
  }, [projectsData]);

  const memberships = user.projectUsers || [];

  if (memberships.length === 0) {
    return <span className='text-muted-foreground'>{t('users.badges.noProjects')}</span>;
  }

  return (
    <div className='flex flex-wrap gap-1'>
      {memberships.map((membership) => {
        const name = projectNameById.get(membership.projectID);
        // 项目超出 first:100 窗口时无法映射名称，跳过以避免展示原始 ID
        if (!name) return null;
        return (
          <Badge key={membership.projectID} variant={membership.isOwner ? 'default' : 'outline'}>
            {name}
            {membership.isOwner && <span className='ml-1'>({t('users.badges.owner')})</span>}
          </Badge>
        );
      })}
    </div>
  );
}
