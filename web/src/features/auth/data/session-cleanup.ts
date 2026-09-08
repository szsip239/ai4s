import { removeTokenFromStorage, useAuthStore } from '@/stores/authStore';
import { useProjectStore } from '@/stores/projectStore';

/**
 * issue #139：登出全量清理本地会话态——access token、auth store、React Query 缓存、选中项目。
 * 此前只清 token/auth store：queryClient 残留上一账号的 me/列表缓存，selectedProjectId
 * 留在 localStorage，同浏览器换账号登录会沿用上一账号的缓存数据与项目上下文（串号）。
 * queryClient 以结构类型入参，便于脱离 React 环境单测（见 session-cleanup.test.mjs）。
 */
export function clearSessionState(queryClient: { clear: () => void }) {
  // Clear token from localStorage
  removeTokenFromStorage();

  // Clear auth store
  useAuthStore.getState().auth.reset();

  // Clear selected project (store + localStorage)
  useProjectStore.getState().clearSelectedProjectId();

  // Clear all cached queries/mutations of the previous account
  queryClient.clear();
}
