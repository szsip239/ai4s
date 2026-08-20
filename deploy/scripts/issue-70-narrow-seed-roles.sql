-- issue #70 清理批（#68 残余）：删除项目级种子角色 Developer/Viewer（幂等，可重复执行）。
-- 背景：Developer/Viewer 含 read_users/read_api_keys/write_api_keys，上游 userHasProjectScope
-- 把项目角色 scopes 等同 membership scopes 判定——admin 在 UI 把角色授给员工即绕开 #68 收窄。
-- 实证（2026-08-20）：删除前 user_roles 全表 0 行（两角色零在效绑定）；
-- 删除后 docker compose restart axonhub 两角色未被种子逻辑重建（roles 表仅剩 Admin）。
-- 执行（DBPW 取 deploy/.env 的 DB_PASSWORD）：
--   docker compose -f deploy/docker-compose.yml exec -T postgres \
--     psql -v ON_ERROR_STOP=1 "postgresql://axonhub:$DB_PASSWORD@localhost:5432/axonhub" -f - < deploy/scripts/issue-70-narrow-seed-roles.sql
-- 注意：直改 SQL 绕过 axonhub 用户缓存失效，执行后须 docker compose restart axonhub。
-- 选择「删除」而非「收窄为空」：零绑定 + 重启实证不复活，删除同时移除 UI 里的误导性可授角色；
-- 若上游未来版本在重启/新建项目时重建这两角色，须改走收窄 scopes='[]' 策略（把下方 DELETE 换成
-- UPDATE roles SET scopes = '[]' WHERE ... 并重新实证）。

BEGIN;

-- 限定 level='project' + name 命中 + scopes 危险键守卫：只删仍带危险授权的种子角色，
-- 不含这些 scope 的同名自定义角色（若有）不删；project_id 不硬编码，覆盖 Default 以外项目。
DELETE FROM roles
WHERE level = 'project' AND name IN ('Developer', 'Viewer')
  AND scopes ?| ARRAY['read_users', 'read_api_keys', 'write_api_keys'];

COMMIT;
