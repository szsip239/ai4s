'use client';

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuthStore } from '@/stores/authStore';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/confirm-dialog';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ProjectMember } from '@/features/users/data/schema';
import { useProjectMembers, useRemoveUserFromProject } from '@/features/users/data/project-membership';
import { ProjectMembershipEditor } from '@/features/users/components/project-membership-editor';
import { useProjectsContext } from '../context/projects-context';

// issue #135：项目页行操作「成员」弹窗——只读成员列表 + 行内「编辑权限/移除」
// mutation 与编辑表单复用人员侧 data/project-membership 与 ProjectMembershipEditor

export function ProjectMembersDialog() {
  const { t } = useTranslation();
  const currentUser = useAuthStore((state) => state.auth.user);
  const { membersProject, setMembersProject } = useProjectsContext();
  const [editingUserId, setEditingUserId] = useState<string | null>(null);
  const [removingMember, setRemovingMember] = useState<ProjectMember | null>(null);

  const projectId = membersProject?.id;
  const { data: members = [], isLoading } = useProjectMembers(projectId, { enabled: !!membersProject });
  const removeUserFromProject = useRemoveUserFromProject();

  // 弹窗关闭时重置行内状态
  useEffect(() => {
    if (!membersProject) {
      setEditingUserId(null);
      setRemovingMember(null);
    }
  }, [membersProject]);

  const handleRemoveConfirm = async () => {
    if (!removingMember || !projectId) return;

    try {
      await removeUserFromProject.mutateAsync({
        projectId,
        userId: removingMember.userID,
      });
      setRemovingMember(null);
    } catch (_error) {
      // 错误 toast 已在 mutation hook 内处理，确认框保持打开
    }
  };

  // issue #135 防自锁：不可移出/降级当前用户自己的项目 owner 关系（贴「不可删自己/owner」纪律）
  const isSelfOwnerMembership = (member: ProjectMember) => member.isOwner && member.userID === currentUser?.id;

  const removingDisplayName = removingMember
    ? [removingMember.user.firstName, removingMember.user.lastName].filter(Boolean).join(' ') || removingMember.user.email
    : '';

  return (
    <Dialog open={!!membersProject} onOpenChange={(state) => !state && setMembersProject(null)}>
      <DialogContent className='sm:max-w-2xl'>
        <DialogHeader className='text-left'>
          <DialogTitle>{t('projects.members.title')}</DialogTitle>
          <DialogDescription>{t('projects.members.description', { name: membersProject?.name })}</DialogDescription>
        </DialogHeader>

        <div className='max-h-[60vh] space-y-2 overflow-y-auto'>
          {isLoading ? (
            <div className='text-muted-foreground text-sm'>{t('common.loading')}</div>
          ) : members.length === 0 ? (
            <div className='text-muted-foreground text-sm'>{t('projects.members.empty')}</div>
          ) : (
            members.map((member) => {
              const memberRoles = member.user.roles?.edges?.map((edge) => edge.node) || [];
              const memberScopes = member.scopes || [];
              const locked = isSelfOwnerMembership(member);
              const displayName = [member.user.firstName, member.user.lastName].filter(Boolean).join(' ') || member.user.email;
              // ProjectMember 扩展自 ProjectMembership，去掉 user 边即编辑器所需的成员关系
              const { user: _user, ...membership } = member;
              return (
                <div key={member.id} className='space-y-2 rounded-md border p-3'>
                  <div className='flex items-center justify-between gap-2'>
                    <div className='flex flex-wrap items-center gap-2'>
                      <span className='text-sm font-medium'>{displayName}</span>
                      <span className='text-muted-foreground text-xs'>{member.user.email}</span>
                      {member.isOwner && <Badge variant='default'>{t('users.badges.projectOwner')}</Badge>}
                    </div>
                    <div className='flex shrink-0 gap-1'>
                      <Button
                        type='button'
                        variant='outline'
                        size='sm'
                        onClick={() => setEditingUserId(editingUserId === member.userID ? null : member.userID)}
                      >
                        {t('users.dialogs.manageProjects.editMembership')}
                      </Button>
                      <Button
                        type='button'
                        variant='outline'
                        size='sm'
                        className='text-destructive'
                        disabled={locked}
                        onClick={() => setRemovingMember(member)}
                      >
                        {t('users.buttons.remove')}
                      </Button>
                    </div>
                  </div>
                  {locked && <p className='text-muted-foreground text-xs'>{t('users.dialogs.manageProjects.selfOwnerLock')}</p>}
                  {/* issue #135：所有者走 isOwner 短路（上游 userHasScope：项目内全权），
                      角色/权限点行对其无意义——显示说明代替「无角色/无权限点」误读 */}
                  {member.isOwner ? (
                    <p className='text-muted-foreground text-xs'>{t('users.badges.ownerAllPermissions')}</p>
                  ) : (
                    <>
                      <div className='flex flex-wrap items-center gap-1 text-xs'>
                        <span className='text-muted-foreground'>{t('users.form.projectRoles')}:</span>
                        {memberRoles.length > 0 ? (
                          memberRoles.map((role) => (
                            <Badge key={role.id} variant='outline'>
                              {role.name}
                            </Badge>
                          ))
                        ) : (
                          <span className='text-muted-foreground'>{t('users.badges.noRoles')}</span>
                        )}
                      </div>
                      <div className='flex flex-wrap items-center gap-1 text-xs'>
                        <span className='text-muted-foreground'>{t('users.form.projectScopes')}:</span>
                        {memberScopes.length > 0 ? (
                          memberScopes.map((scope) => (
                            <Badge key={scope} variant='secondary'>
                              {scope}
                            </Badge>
                          ))
                        ) : (
                          <span className='text-muted-foreground'>{t('users.badges.noScopes')}</span>
                        )}
                      </div>
                    </>
                  )}
                  {editingUserId === member.userID && (
                    <ProjectMembershipEditor
                      userId={member.userID}
                      projectId={member.projectID}
                      membership={membership}
                      lockOwner={locked}
                      onDone={() => setEditingUserId(null)}
                    />
                  )}
                </div>
              );
            })
          )}
        </div>
      </DialogContent>

      {/* 移出项目确认 */}
      <ConfirmDialog
        open={!!removingMember}
        onOpenChange={(state) => {
          if (!state) setRemovingMember(null);
        }}
        title={t('users.dialogs.remove.title')}
        desc={
          <div className='space-y-2'>
            <p>{t('users.dialogs.remove.description', { name: removingDisplayName })}</p>
            <p className='text-muted-foreground text-sm'>{t('users.dialogs.remove.warningDescription')}</p>
          </div>
        }
        confirmText={removeUserFromProject.isPending ? t('users.buttons.removing') : t('users.buttons.remove')}
        cancelBtnText={t('common.buttons.cancel')}
        handleConfirm={handleRemoveConfirm}
        isLoading={removeUserFromProject.isPending}
        destructive
      />
    </Dialog>
  );
}
