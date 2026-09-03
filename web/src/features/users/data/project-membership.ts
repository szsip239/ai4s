import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { graphqlRequest } from '@/gql/graphql';
import { useErrorHandler } from '@/hooks/use-error-handler';
import { ProjectMember, ProjectMembership, projectMemberSchema, projectMembershipSchema } from './schema';

// issue #135：项目成员关系（UserProject）查询与变更
// 人员侧「项目归属管理」对话框与项目侧「成员」弹窗共用本数据层

// 按用户查询其全部项目成员关系
const USER_PROJECT_MEMBERSHIPS_QUERY = `
  query UserProjectMemberships($userId: ID!) {
    node(id: $userId) {
      ... on User {
        id
        projectUsers {
          id
          userID
          projectID
          isOwner
          scopes
        }
      }
    }
  }
`;

// 按项目查询成员列表（user 边带项目内角色，沿用已下线 /project/users 页的实证查询模式）
const PROJECT_MEMBERS_QUERY = `
  query ProjectMembers($projectId: ID!) {
    node(id: $projectId) {
      ... on Project {
        id
        name
        projectUsers {
          id
          userID
          projectID
          isOwner
          scopes
          user {
            id
            email
            firstName
            lastName
            status
            roles(where: { projectID: $projectId }) {
              edges {
                node {
                  id
                  name
                }
              }
            }
          }
        }
      }
    }
  }
`;

// 查询用户在某项目内已分配的角色（编辑权限表单的初始勾选）
const USER_PROJECT_ROLES_QUERY = `
  query UserProjectRoles($userId: ID!, $projectId: ID!) {
    node(id: $userId) {
      ... on User {
        id
        roles(where: { projectID: $projectId }) {
          edges {
            node {
              id
              name
            }
          }
        }
      }
    }
  }
`;

// 将用户加入项目（人员侧「项目归属管理」对话框的现有加入流程收编进数据层）
const ADD_USER_TO_PROJECT_MUTATION = `
  mutation AddUserToProject($input: AddUserToProjectInput!) {
    addUserToProject(input: $input) {
      id
      userID
      projectID
      isOwner
      scopes
    }
  }
`;

// 将用户移出项目
const REMOVE_USER_FROM_PROJECT_MUTATION = `
  mutation RemoveUserFromProject($input: RemoveUserFromProjectInput!) {
    removeUserFromProject(input: $input)
  }
`;

// 更新用户在某项目内的成员关系（isOwner/scopes/项目角色增删）
const UPDATE_PROJECT_USER_MUTATION = `
  mutation UpdateProjectUser($input: UpdateProjectUserInput!) {
    updateProjectUser(input: $input) {
      id
      userID
      projectID
      isOwner
      scopes
    }
  }
`;

export interface UpdateProjectUserVariables {
  projectId: string;
  userId: string;
  isOwner?: boolean;
  scopes?: string[];
  addRoleIDs?: string[];
  removeRoleIDs?: string[];
}

export interface AddUserToProjectVariables {
  projectId: string;
  userId: string;
  isOwner?: boolean;
  scopes?: string[];
  roleIDs?: string[];
}

// Query hooks
export function useUserProjectMemberships(userId?: string, options?: { enabled?: boolean }) {
  const { t } = useTranslation();
  const { handleError } = useErrorHandler();

  return useQuery({
    queryKey: ['user-project-memberships', userId],
    queryFn: async () => {
      try {
        const data = await graphqlRequest<{ node: { projectUsers: ProjectMembership[] } | null }>(USER_PROJECT_MEMBERSHIPS_QUERY, { userId });
        return (data.node?.projectUsers || []).map((pu) => projectMembershipSchema.parse(pu));
      } catch (error) {
        handleError(error, t('common.errors.loadFailed'));
        throw error;
      }
    },
    enabled: !!userId && (options?.enabled ?? true),
  });
}

export function useProjectMembers(projectId?: string, options?: { enabled?: boolean }) {
  const { t } = useTranslation();
  const { handleError } = useErrorHandler();

  return useQuery({
    queryKey: ['project-members', projectId],
    queryFn: async () => {
      try {
        const headers = { 'X-Project-ID': projectId! };
        const data = await graphqlRequest<{ node: { projectUsers: ProjectMember[] } | null }>(PROJECT_MEMBERS_QUERY, { projectId }, headers);
        return (data.node?.projectUsers || []).map((pu) => projectMemberSchema.parse(pu));
      } catch (error) {
        handleError(error, t('common.errors.loadFailed'));
        throw error;
      }
    },
    enabled: !!projectId && (options?.enabled ?? true),
  });
}

export function useUserProjectRoles(userId?: string, projectId?: string, options?: { enabled?: boolean }) {
  const { t } = useTranslation();
  const { handleError } = useErrorHandler();

  return useQuery({
    queryKey: ['user-project-roles', userId, projectId],
    queryFn: async () => {
      try {
        const data = await graphqlRequest<{
          node: { roles: { edges: Array<{ node: { id: string; name: string } }> } } | null;
        }>(USER_PROJECT_ROLES_QUERY, { userId, projectId });
        return data.node?.roles?.edges?.map((edge) => edge.node) || [];
      } catch (error) {
        handleError(error, t('common.errors.loadFailed'));
        throw error;
      }
    },
    enabled: !!userId && !!projectId && (options?.enabled ?? true),
  });
}

// 人员侧列表按项目批量取角色名（用户加入的项目数量少，逐项目查询可接受）
export function useUserProjectRolesMap(userId: string | undefined, projectIds: string[], options?: { enabled?: boolean }) {
  return useQueries({
    queries: projectIds.map((projectId) => ({
      queryKey: ['user-project-roles', userId, projectId],
      queryFn: async () => {
        const data = await graphqlRequest<{
          node: { roles: { edges: Array<{ node: { id: string; name: string } }> } } | null;
        }>(USER_PROJECT_ROLES_QUERY, { userId, projectId });
        return data.node?.roles?.edges?.map((edge) => edge.node) || [];
      },
      enabled: !!userId && (options?.enabled ?? true),
    })),
    combine: (results) => {
      const rolesByProject: Record<string, Array<{ id: string; name: string }>> = {};
      projectIds.forEach((projectId, index) => {
        rolesByProject[projectId] = results[index]?.data || [];
      });
      return { rolesByProject, isLoading: results.some((r) => r.isLoading) };
    },
  });
}

// Mutation hooks
export function useAddUserToProject() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ projectId, ...rest }: AddUserToProjectVariables) => {
      const headers = { 'X-Project-ID': projectId };
      const data = await graphqlRequest<{ addUserToProject: ProjectMembership }>(
        ADD_USER_TO_PROJECT_MUTATION,
        { input: { projectId, ...rest } },
        headers
      );
      return data.addUserToProject;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['user-project-memberships'] });
      queryClient.invalidateQueries({ queryKey: ['user-project-roles'] });
      queryClient.invalidateQueries({ queryKey: ['project-members'] });
      queryClient.invalidateQueries({ queryKey: ['myProjects'] });
      toast.success(t('users.messages.addToProjectSuccess'));
    },
    onError: (error: any) => {
      const message = error?.message || t('common.errors.somethingWentWrong');
      toast.error(message);
    },
  });
}

export function useRemoveUserFromProject() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ projectId, userId }: { projectId: string; userId: string }) => {
      const headers = { 'X-Project-ID': projectId };
      const data = await graphqlRequest<{ removeUserFromProject: boolean }>(
        REMOVE_USER_FROM_PROJECT_MUTATION,
        { input: { projectId, userId } },
        headers
      );
      return data.removeUserFromProject;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['user-project-memberships'] });
      queryClient.invalidateQueries({ queryKey: ['user-project-roles'] });
      queryClient.invalidateQueries({ queryKey: ['project-members'] });
      queryClient.invalidateQueries({ queryKey: ['myProjects'] });
      toast.success(t('users.messages.removeFromProjectSuccess'));
    },
    onError: (error: any) => {
      const message = error?.message || t('common.errors.internalServerError');
      toast.error(message);
    },
  });
}

export function useUpdateProjectUser() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({ projectId, userId, ...rest }: UpdateProjectUserVariables) => {
      const headers = { 'X-Project-ID': projectId };
      const data = await graphqlRequest<{ updateProjectUser: ProjectMembership }>(
        UPDATE_PROJECT_USER_MUTATION,
        { input: { projectId, userId, ...rest } },
        headers
      );
      return data.updateProjectUser;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['user-project-memberships'] });
      queryClient.invalidateQueries({ queryKey: ['user-project-roles'] });
      queryClient.invalidateQueries({ queryKey: ['project-members'] });
      queryClient.invalidateQueries({ queryKey: ['myProjects'] });
      toast.success(t('users.messages.updateProjectUserSuccess'));
    },
    onError: (error: any) => {
      const message = error?.message || t('common.errors.internalServerError');
      toast.error(message);
    },
  });
}
