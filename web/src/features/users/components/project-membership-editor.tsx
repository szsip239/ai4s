'use client';

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { graphqlRequest } from '@/gql/graphql';
import { ROLES_QUERY } from '@/gql/roles';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { ScopesSelect } from '@/components/scopes-select';
import { ProjectMembership } from '../data/schema';
import { useUpdateProjectUser, useUserProjectRoles } from '../data/project-membership';

// issue #135：编辑用户在某项目内的成员关系（isOwner/scopes/项目角色增删）
// 行内表单（不嵌套 <form>），人员侧「项目归属管理」对话框与项目侧「成员」弹窗复用

interface Role {
  id: string;
  name: string;
  description?: string;
  scopes?: string[];
}

interface ProjectMembershipEditorProps {
  userId: string;
  projectId: string;
  membership: ProjectMembership;
  // 防自锁：目标是当前用户自己的项目 owner 关系时，禁止降级 isOwner
  lockOwner?: boolean;
  onDone: () => void;
}

export function ProjectMembershipEditor({ userId, projectId, membership, lockOwner = false, onDone }: ProjectMembershipEditorProps) {
  const { t } = useTranslation();
  const [roles, setRoles] = useState<Role[]>([]);
  const [rolesLoading, setRolesLoading] = useState(false);
  const [isOwner, setIsOwner] = useState(membership.isOwner);
  const [scopes, setScopes] = useState<string[]>(membership.scopes || []);
  const [roleIDs, setRoleIDs] = useState<string[]>([]);

  const updateProjectUser = useUpdateProjectUser();
  // 用户当前在该项目已分配的角色（初始勾选；人员侧列表的同 key 查询会命中缓存）
  const { data: assignedRoles, isLoading: assignedRolesLoading } = useUserProjectRoles(userId, projectId);

  // 初始勾选随查询结果就绪后设置一次
  useEffect(() => {
    if (assignedRoles) {
      setRoleIDs(assignedRoles.map((role) => role.id));
    }
  }, [assignedRoles]);

  // 加载该项目的全部角色作为可选项（与 users-add-to-project-dialog 相同的查询方式）
  useEffect(() => {
    const loadRoles = async () => {
      setRolesLoading(true);
      try {
        const rolesData = await graphqlRequest(ROLES_QUERY, {
          first: 100,
          where: { projectID: projectId },
        });

        const rolesResponse = rolesData as {
          roles: {
            edges: Array<{
              node: Role;
            }>;
          };
        };

        setRoles(rolesResponse.roles.edges.map((edge) => edge.node));
      } catch (_error) {
        toast.error(t('common.errors.userLoadFailed'));
      } finally {
        setRolesLoading(false);
      }
    };

    loadRoles();
  }, [projectId, t]);

  const handleRoleToggle = (roleId: string) => {
    setRoleIDs((current) => (current.includes(roleId) ? current.filter((id) => id !== roleId) : [...current, roleId]));
  };

  const handleSave = async () => {
    const initialRoleIDs = (assignedRoles || []).map((role) => role.id);
    const addRoleIDs = roleIDs.filter((id) => !initialRoleIDs.includes(id));
    const removeRoleIDs = initialRoleIDs.filter((id) => !roleIDs.includes(id));

    try {
      await updateProjectUser.mutateAsync({
        projectId,
        userId,
        isOwner,
        scopes,
        addRoleIDs: addRoleIDs.length > 0 ? addRoleIDs : undefined,
        removeRoleIDs: removeRoleIDs.length > 0 ? removeRoleIDs : undefined,
      });
      onDone();
    } catch (_error) {
      // 错误 toast 已在 mutation hook 内处理
    }
  };

  return (
    <div className='space-y-4 rounded-md border p-4'>
      <div className='flex flex-row items-start space-y-0 space-x-3'>
        <Checkbox
          id={`membership-owner-${projectId}`}
          checked={isOwner}
          onCheckedChange={(checked) => setIsOwner(checked === true)}
          disabled={lockOwner}
        />
        <div className='space-y-1 leading-none'>
          <Label htmlFor={`membership-owner-${projectId}`}>{t('users.form.isOwner')}</Label>
          <p className='text-muted-foreground text-sm'>
            {lockOwner ? t('users.dialogs.manageProjects.selfOwnerLock') : t('users.form.ownerDescription')}
          </p>
        </div>
      </div>

      <div className='space-y-3'>
        <Label>{t('users.form.projectRoles')}</Label>
        {rolesLoading || assignedRolesLoading ? (
          <div className='text-muted-foreground text-sm'>{t('users.form.loadingRoles')}</div>
        ) : roles.length === 0 ? (
          <div className='text-muted-foreground text-sm'>{t('users.form.noProjectRoles')}</div>
        ) : (
          <div className='grid grid-cols-2 gap-2'>
            {roles.map((role) => (
              <div key={role.id} className='flex items-center space-x-2'>
                <Checkbox id={`membership-role-${role.id}`} checked={roleIDs.includes(role.id)} onCheckedChange={() => handleRoleToggle(role.id)} />
                <label
                  htmlFor={`membership-role-${role.id}`}
                  className='text-sm leading-none font-medium peer-disabled:cursor-not-allowed peer-disabled:opacity-70'
                >
                  {role.name}
                </label>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className='space-y-2'>
        <Label>{t('users.form.projectScopes')}</Label>
        <ScopesSelect value={scopes} onChange={setScopes} />
      </div>

      <div className='flex justify-end gap-2'>
        <Button type='button' variant='outline' size='sm' onClick={onDone} disabled={updateProjectUser.isPending}>
          {t('common.buttons.cancel')}
        </Button>
        <Button type='button' size='sm' onClick={handleSave} disabled={updateProjectUser.isPending}>
          {updateProjectUser.isPending ? t('common.buttons.saving') : t('common.buttons.save')}
        </Button>
      </div>
    </div>
  );
}
