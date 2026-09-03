'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import { z } from 'zod';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { graphqlRequest } from '@/gql/graphql';
import { ROLES_QUERY } from '@/gql/roles';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { useAuthStore } from '@/stores/authStore';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { ConfirmDialog } from '@/components/confirm-dialog';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form';
import { Label } from '@/components/ui/label';
import { SelectDropdown } from '@/components/select-dropdown';
import { Separator } from '@/components/ui/separator';
import { ScopesSelect } from '@/components/scopes-select';
import { useProjects } from '@/features/projects/data/projects';
import { ProjectMembership, User } from '../data/schema';
import { useAddUserToProject, useRemoveUserFromProject, useUserProjectMemberships, useUserProjectRolesMap } from '../data/project-membership';
import { ProjectMembershipEditor } from './project-membership-editor';

// issue #135：本对话框由「加入项目」扩展为「项目归属管理」——
// 顶部列出用户已加入的项目（名称/isOwner/scopes/角色），支持移出与编辑权限；底部保留加入新项目入口

const createFormSchema = (t: (key: string) => string) =>
  z.object({
    projectId: z.string().min(1, t('users.validation.projectRequired')),
    isOwner: z.boolean().optional(),
    roleIDs: z.array(z.string()).optional(),
    scopes: z.array(z.string()).optional(),
  });

interface Role {
  id: string;
  name: string;
  description?: string;
  scopes?: string[];
}

interface Props {
  currentRow?: User;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function UsersAddToProjectDialog({ currentRow, open, onOpenChange }: Props) {
  const { t } = useTranslation();
  const currentUser = useAuthStore((state) => state.auth.user);
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(false);
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [removingMembership, setRemovingMembership] = useState<ProjectMembership | null>(null);
  const [dialogContent, setDialogContent] = useState<HTMLDivElement | null>(null);

  // Fetch all projects
  const { data: projectsData } = useProjects({ first: 100 });

  // 用户已加入的项目成员关系（issue #135）
  const { data: memberships = [], isLoading: membershipsLoading } = useUserProjectMemberships(currentRow?.id, { enabled: open });
  const membershipProjectIds = useMemo(() => memberships.map((m) => m.projectID), [memberships]);
  const { rolesByProject } = useUserProjectRolesMap(currentRow?.id, membershipProjectIds, { enabled: open });

  const addUserToProject = useAddUserToProject();
  const removeUserFromProject = useRemoveUserFromProject();

  // 项目 id→name 映射
  const projectNameById = useMemo(() => {
    const map = new Map<string, string>();
    projectsData?.edges?.forEach((edge) => map.set(edge.node.id, edge.node.name));
    return map;
  }, [projectsData]);

  const formSchema = createFormSchema(t);
  type AddToProjectForm = z.infer<typeof formSchema>;

  const form = useForm<AddToProjectForm>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      projectId: '',
      isOwner: false,
      roleIDs: [],
      scopes: [],
    },
  });

  const selectedProjectId = form.watch('projectId');

  // 对话框关闭时重置行内编辑/移除状态
  useEffect(() => {
    if (!open) {
      setEditingProjectId(null);
      setRemovingMembership(null);
    }
  }, [open]);

  const loadRoles = useCallback(
    async (projectId: string) => {
      if (!projectId) return;

      setLoading(true);
      try {
        const rolesData = await graphqlRequest(ROLES_QUERY, {
          first: 100,
          where: { projectID: projectId },
        });

        const rolesResponse = rolesData as {
          roles: {
            edges: Array<{
              node: {
                id: string;
                name: string;
                description?: string;
                scopes?: string[];
              };
            }>;
          };
        };

        setRoles(rolesResponse.roles.edges.map((edge) => edge.node));
      } catch (_error) {
        toast.error(t('common.errors.userLoadFailed'));
      } finally {
        setLoading(false);
      }
    },
    [t]
  );

  useEffect(() => {
    if (selectedProjectId) {
      loadRoles(selectedProjectId);
    }
  }, [selectedProjectId, loadRoles]);

  const onSubmit = async (values: AddToProjectForm) => {
    if (!currentRow) return;

    try {
      await addUserToProject.mutateAsync({
        projectId: values.projectId,
        userId: currentRow.id,
        isOwner: values.isOwner,
        scopes: values.scopes,
        roleIDs: values.roleIDs,
      });
      form.reset();
      onOpenChange(false);
    } catch (_error) {
      // 错误 toast 已在 mutation hook 内处理
    }
  };

  const handleRemoveConfirm = async () => {
    if (!removingMembership || !currentRow) return;

    try {
      await removeUserFromProject.mutateAsync({
        projectId: removingMembership.projectID,
        userId: currentRow.id,
      });
      setRemovingMembership(null);
    } catch (_error) {
      // 错误 toast 已在 mutation hook 内处理，确认框保持打开
    }
  };

  const handleRoleToggle = (roleId: string) => {
    const currentRoles = form.getValues('roleIDs') || [];
    const newRoles = currentRoles.includes(roleId) ? currentRoles.filter((id: string) => id !== roleId) : [...currentRoles, roleId];
    form.setValue('roleIDs', newRoles);
  };

  // issue #135 防自锁：不可移出/降级当前用户自己的项目 owner 关系（贴「不可删自己/owner」纪律）
  const isSelfOwnerMembership = (membership: ProjectMembership) => membership.isOwner && membership.userID === currentUser?.id;

  // Mark projects that the user is already a member of as disabled
  const projects =
    projectsData?.edges?.map((edge) => ({
      label: edge.node.name,
      value: edge.node.id,
      disabled: membershipProjectIds.includes(edge.node.id),
    })) || [];

  const displayName = [currentRow?.firstName, currentRow?.lastName].filter(Boolean).join(' ') || currentRow?.email || '';

  return (
    <Dialog
      open={open}
      onOpenChange={(state) => {
        if (!state) {
          form.reset();
        }
        onOpenChange(state);
      }}
    >
      <DialogContent className='sm:max-w-2xl' ref={setDialogContent}>
        <DialogHeader className='text-left'>
          <DialogTitle>{t('users.dialogs.manageProjects.title')}</DialogTitle>
          <DialogDescription>
            {currentRow &&
              t('users.dialogs.manageProjects.description', {
                firstName: currentRow.firstName,
                lastName: currentRow.lastName,
              })}
          </DialogDescription>
        </DialogHeader>

        <div className='max-h-[60vh] space-y-6 overflow-y-auto'>
          {/* 已加入项目列表（issue #135） */}
          <div className='space-y-2'>
            <Label>{t('users.dialogs.manageProjects.joinedProjects')}</Label>
            {membershipsLoading ? (
              <div className='text-muted-foreground text-sm'>{t('common.loading')}</div>
            ) : memberships.length === 0 ? (
              <div className='text-muted-foreground text-sm'>{t('users.dialogs.manageProjects.empty')}</div>
            ) : (
              memberships.map((membership) => {
                const memberRoles = rolesByProject[membership.projectID] || [];
                const memberScopes = membership.scopes || [];
                const locked = isSelfOwnerMembership(membership);
                return (
                  <div key={membership.id} className='space-y-2 rounded-md border p-3'>
                    <div className='flex items-center justify-between gap-2'>
                      <div className='flex flex-wrap items-center gap-2'>
                        <span className='text-sm font-medium'>{projectNameById.get(membership.projectID) ?? membership.projectID}</span>
                        {membership.isOwner && <Badge variant='default'>{t('users.badges.projectOwner')}</Badge>}
                      </div>
                      <div className='flex shrink-0 gap-1'>
                        <Button
                          type='button'
                          variant='outline'
                          size='sm'
                          onClick={() => setEditingProjectId(editingProjectId === membership.projectID ? null : membership.projectID)}
                        >
                          {t('users.dialogs.manageProjects.editMembership')}
                        </Button>
                        <Button
                          type='button'
                          variant='outline'
                          size='sm'
                          className='text-destructive'
                          disabled={locked}
                          onClick={() => setRemovingMembership(membership)}
                        >
                          {t('users.buttons.remove')}
                        </Button>
                      </div>
                    </div>
                    {locked && <p className='text-muted-foreground text-xs'>{t('users.dialogs.manageProjects.selfOwnerLock')}</p>}
                    {/* issue #135：所有者走 isOwner 短路（上游 userHasScope：项目内全权），
                        角色/权限点行对其无意义——显示说明代替「无角色/无权限点」误读 */}
                    {membership.isOwner ? (
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
                    {editingProjectId === membership.projectID && currentRow && (
                      <ProjectMembershipEditor
                        userId={currentRow.id}
                        projectId={membership.projectID}
                        membership={membership}
                        lockOwner={locked}
                        onDone={() => setEditingProjectId(null)}
                      />
                    )}
                  </div>
                );
              })
            )}
          </div>

          <Separator />

          {/* 加入新项目（现有加入流程原样保留） */}
          <div className='space-y-4'>
            <Label>{t('users.actions.addToProject')}</Label>
            <Form {...form}>
              <form id='add-to-project-form' onSubmit={form.handleSubmit(onSubmit)} className='space-y-6'>
                <FormField
                  control={form.control}
                  name='projectId'
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>{t('users.form.selectProject')}</FormLabel>
                      <SelectDropdown
                        defaultValue={field.value}
                        onValueChange={field.onChange}
                        placeholder={t('users.form.selectProjectPlaceholder')}
                        items={projects}
                      />
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {selectedProjectId && (
                  <>
                    <FormField
                      control={form.control}
                      name='isOwner'
                      render={({ field }) => (
                        <FormItem className='flex flex-row items-start space-y-0 space-x-3'>
                          <FormControl>
                            <Checkbox checked={field.value} onCheckedChange={field.onChange} />
                          </FormControl>
                          <div className='space-y-1 leading-none'>
                            <FormLabel>{t('users.form.isOwner')}</FormLabel>
                            <p className='text-muted-foreground text-sm'>{t('users.form.ownerDescription')}</p>
                          </div>
                        </FormItem>
                      )}
                    />

                    {/* Roles Section */}
                    <div className='space-y-3'>
                      <FormLabel>{t('users.form.projectRoles')}</FormLabel>
                      {loading ? (
                        <div>{t('users.form.loadingRoles')}</div>
                      ) : roles.length === 0 ? (
                        <div className='text-muted-foreground text-sm'>{t('users.form.noProjectRoles')}</div>
                      ) : (
                        <div className='grid grid-cols-2 gap-2'>
                          {roles.map((role) => (
                            <div key={role.id} className='flex items-center space-x-2'>
                              <Checkbox
                                id={`role-${role.id}`}
                                checked={(form.watch('roleIDs') || []).includes(role.id)}
                                onCheckedChange={() => handleRoleToggle(role.id)}
                              />
                              <label
                                htmlFor={`role-${role.id}`}
                                className='text-sm leading-none font-medium peer-disabled:cursor-not-allowed peer-disabled:opacity-70'
                              >
                                {role.name}
                              </label>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Scopes Section */}
                    <FormField
                      control={form.control}
                      name='scopes'
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>{t('users.form.projectScopes')}</FormLabel>
                          <FormControl>
                            <ScopesSelect value={field.value || []} onChange={field.onChange} portalContainer={dialogContent} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </>
                )}
              </form>
            </Form>
          </div>
        </div>

        <DialogFooter>
          <Button type='submit' form='add-to-project-form' disabled={addUserToProject.isPending}>
            {addUserToProject.isPending ? t('users.buttons.adding') : t('users.buttons.addToProject')}
          </Button>
        </DialogFooter>
      </DialogContent>

      {/* 移出项目确认（issue #135） */}
      <ConfirmDialog
        open={!!removingMembership}
        onOpenChange={(state) => {
          if (!state) setRemovingMembership(null);
        }}
        title={t('users.dialogs.remove.title')}
        desc={
          <div className='space-y-2'>
            <p>
              {t('users.dialogs.remove.description', {
                name: displayName,
              })}
            </p>
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
