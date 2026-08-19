-- issue #68 权限根修：存量用户系统档收窄 + 项目级最小能力重发（幂等，可重复执行）。
-- 背景：docs/tests/2026-08-20-full-qa-report.md 坐实系统档全局通行（P1-A/P1-B/P2-A/P2-B）。
-- 执行（DBPW 取 deploy/.env 的 DB_PASSWORD）：
--   docker compose -f deploy/docker-compose.yml exec -T postgres \
--     psql -v ON_ERROR_STOP=1 "postgresql://axonhub:$DB_PASSWORD@localhost:5432/axonhub" -f - < deploy/scripts/issue-68-narrow-scopes.sql
-- 注意：直改 SQL 绕过 axonhub 用户缓存失效，执行后须 docker compose restart axonhub。
-- 新 JIT 用户不受本脚本影响：config.yml default_scopes 已收窄为 []，
-- 项目级能力由 assign-default-project.sh 在首登后下发（同下述最小集）。

BEGIN;

-- 1) 系统档收窄为空：employee-test（id=2）与 SSO 用户老板（id=3，is_owner=t，
--    isOwner 直通不受影响，收窄仅为消除冗余授权面）。
UPDATE users SET scopes = '[]' WHERE id IN (2, 3);

-- 2) employee-test 项目级最小能力：观测（read_requests）+ playground/请求写入（write_requests）。
--    刻意不含 read_api_keys/write_api_keys：上游 v1.0.0-beta6 项目级 key scope 无属主约束
--    （项目内全部非 personal key 明文可见、任意 key profiles/模板可改），下发即重开 P1-A/P1-B/P2-A。
UPDATE user_projects SET scopes = '["read_requests","write_requests"]'
WHERE user_id = 2 AND project_id = 1;

-- 3) 摘除 employee-test 的 Developer 项目角色（user_roles）。
--    上游 userHasProjectScope 把项目角色 scopes 等同 membership scopes 判定，
--    留着该角色会使 #2 的收窄失效（Developer 含 read_users/read_api_keys/write_api_keys）。
DELETE FROM user_roles WHERE user_id = 2 AND role_id = 2;

COMMIT;
