import { createFileRoute, redirect } from '@tanstack/react-router';

// 挂载点 M8：请求详情深链不再进入含原文的上游详情页，统一回审计列表（issue #13）
export const Route = createFileRoute('/_authenticated/project/requests/$requestId')({
  beforeLoad: () => {
    throw redirect({ to: '/project/requests' });
  },
});
