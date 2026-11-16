# AxonHub beta12 → rc.4 compatibility acceptance

Issue: [#145](https://github.com/szsip239/ai4s/issues/145). Recorded 2026-11-16. Candidate preparation and the subsequently authorized production cutover are recorded separately below. Preparation alone does not authorize production changes.

## Fixed source and scope

| Item | Value |
| --- | --- |
| Branch base | `origin/main`, `582dc0f1483cd90d72a48288c6caf0c0faa4f060` |
| Running backend checked before work | `v1.0.0-beta12`, healthy; `systems.key='system_version'` agrees |
| beta12 source | `2b1a0f9321a4f783a4cceb2b4eb3a8303074487e` |
| Candidate source | `v1.0.0-rc.4`, `824427782241086f3a3632c5e451cdfd0d62c819` |
| Candidate image | `looplj/axonhub@sha256:86b5be6baf7517c7574976ed9c319ba1e6cea2c9a07bd749c678757ad2dfe6ed` |
| Production beta12 rollback image | `looplj/axonhub@sha256:4909d1d49583021d9ef75f690bec1e2db4ca852c3f5620b3c27cfb8ec537c50c` |
| Frontend | beta10 vendor plus beta11/beta12/rc.4 patches; **not** a full rc.4 vendor merge |

The official tag is an RC even though GitHub marks the release `prerelease=false`. Source was obtained with official Git, with the complete beta12…rc.4 diff (115 commits), rather than the truncated GitHub compare file list.

The patch imports the already deployed beta12 Analytics semantics from the controlled original-workspace snapshot. It does not publish that workspace's unrelated README, CLI, pricing, recognizer, mock, gateway, or component-version changes, or its unpushed documentation commit. New tests live in `web/src/ai4s/compat/`; vendor edits are recorded in `web/MOUNTPOINTS.md`.

## Contract review

The complete root GraphQL SDL diff has exactly three breaking changes:

| Removed contract | Actual frontend caller | Adaptation |
| --- | --- | --- |
| `CatalogSettings.refreshSeconds` | System general settings query | Remove selection and interval field |
| `UpdateCatalogSettingsInput.refreshSeconds` | Catalog save form | Submit only `upstreamURL` |
| `Mutation.refreshProvidersCatalog` | Models status and catalog settings buttons | Remove mutation and buttons; backend now loads on demand |

Additions include plugin APIs, model-name preview, DeepSeek quota provider, `Trace.lastRequestAt` and its filters/order, and an optional `resetID` argument. Existing one-argument quota reset remains valid. No plugin UI, plugin grants, new upstream reviewer, or provider credentials are enabled by this patch. DeepSeek receives a collection-setting label; existing generic quota status/limits remain available without importing its new detailed balance UI.

AST validation extracts 212 complete frontend GraphQL documents, including interpolated fragments. Three unused legacy documents (`GetHourlyRequestStats`, `User`, `SignIn`) fail identically against beta12 and rc.4; searches find no callers of those documents. All remaining extracted documents validate against rc.4. This is static contract coverage, not a claim that every mutation was executed.

REST callers were inventoried from the frontend, and corresponding route/handler changes reviewed: signin/refresh, initialize/status, invitation lookup/register/create, OIDC providers/authorize/exchange/link, provider OAuth start/exchange/poll/decode/SSO, playground, request content/preview, and GraphQL upload. Existing required invitation `roleID` and token/project encodings remain. New desktop/plugin routes are additive. Existing OAuth handler bindings, auth middleware, GUID parsing, invitation handler and request-content handler have no diff. OIDC business logic now rejects mismatched UserInfo/ID-token subjects and binds email verification to the same email response; real Casdoor round-trip remains a production acceptance gate.

Authz changes replace broad bypasses with host operations, constrain plugin delegation, require explicit settings/dashboard scopes, and validate ownership/append-scope grants. Direct owner and project-scoped employee behavior was tested. These changes do not establish that #88 or #2281 is fixed: membership removal, project deletion, personal-key visibility/profile mutations, project-role scope inheritance and request-content exposure still require AI4S's existing #68/#70/gateway/shim boundaries.

Protocol review covers the complete `llm/transformer`, API and orchestrator diff: plugin hooks, stream queue/lifecycle and terminal accounting changes, Anthropic tool IDs/stop reasons, Responses strict tools/function-output identity, cached model-price fallback, and accepted upstream cost JSON shapes. Backpressure can now reserve pressured channels as last resort; exhausted channels remain excluded. AI4S keeps `remove_on_exhausted`, with an actual exhausted-channel rejection test.

Sources: [fixed release](https://github.com/looplj/axonhub/releases/tag/v1.0.0-rc.4), [complete source comparison](https://github.com/looplj/axonhub/compare/v1.0.0-beta12...v1.0.0-rc.4), [SDL](https://github.com/looplj/axonhub/tree/824427782241086f3a3632c5e451cdfd0d62c819/internal/server/gql), [authz/scopes](https://github.com/looplj/axonhub/tree/824427782241086f3a3632c5e451cdfd0d62c819/internal/scopes), [protocol code](https://github.com/looplj/axonhub/tree/824427782241086f3a3632c5e451cdfd0d62c819/llm/transformer).

## Migration rehearsal

A fresh, read-only custom-format `pg_dump` of the production `axonhub` database was restored with `pg_restore --exit-on-error` into a separately named PostgreSQL container/database/volume. Credentials, dump and copied configuration stayed in a private temporary directory, outside Git. The candidate had an internal Docker network with no production network membership or Internet egress; an isolated proxy exposed only new `127.0.0.1` ports. Gateway, shim and Presidio used the running images and private copies of their mounts, not production directories.

Observed after rc.4 startup, before synthetic fixtures:

- `system_version=v1.0.0-rc.4`; all ten sampled business-table row counts unchanged.
- `quota_routing_settings={"defaultMode":"remove_on_exhausted"}` retained.
- Existing Developer/Viewer roles containing `read_api_keys`/`write_api_keys`: 0.
- A newly created synthetic project still seeded one overprivileged Developer role. The existing issue-70 scope removal was applied only to that isolated fixture and rechecked at 0; rc.4 does not remove the need for this step.
- Added `plugins` and `plugin_storages` tables, their unique indexes, `traces.last_request_at` with the `1970-01-02` SQL default, and its project/time index.
- No new versioned `datamigrate` implementation; existing migrations correctly skipped. The cloned database had zero trace rows, so no populated-trace backfill result is claimed.

Ent still runs with `WithDropColumn(true)` and `WithDropIndex(true)`. **Migration is a one-way production operation. Restore the pre-upgrade database first, then start the old image and restore the old frontend. Never point beta12 at the migrated rc.4 database to roll back.**

## Executed checks

| Check | Result |
| --- | --- |
| Catalog regressions before patch | 2 failing cases against deleted rc.4 contracts |
| Compatibility regressions after patch | 5/5 pass, including the preserved Analytics local-day boundaries |
| `pnpm exec tsc --noEmit -p tsconfig.app.json` | Exit 2 on both baseline and candidate: 184 existing diagnostics; normalized diagnostic multiset unchanged; new errors 0; TS2304/TS2305/TS2552 0 |
| `pnpm test:unit` | Baseline 178 pass / 7 fail / 1 skip; candidate 183 pass / same 7 fail / 1 skip |
| Existing failing tests | Seven smart-routing validation cases; unchanged tests and implementation |
| `pnpm build` | Production build passes |
| Existing shim authz regressions | GraphQL 49/49; playground 16/16 |
| Isolated API cases | 25/25: owner/employee/invitee authz, self/admin boundaries, numeric project header, three protocol nonstream/SSE/tool rounds, quota and persisted cost |
| Additional protocol checks | 3/3 SSE tool cases; disconnect sample reaches a terminal row with one usage record |
| Provider quota | Exhausted only channel → HTTP 503 `quota_exhausted`; restoring available status → 200 |
| Cost sample | 100 input tokens, 30 cached input, 20 output; configured synthetic prices yield persisted `0.00037` credit |
| DLP primary vectors under copied production policy | 37 pass, 24 skipped, 0 fail; L2 and response layers are disabled in that policy, using the repository's existing layer-gate exemptions |
| Browser | Real form login against isolated stack, SW unregistered and cache disabled; navigation, models/playground, Analytics date ranges, system quota/retry and related project/settings pages observed with screenshots |

OpenAI and Anthropic calls exercised the copied gateway; Responses exercised the candidate backend directly. The copied AgentGateway configuration still returns HTTP 400 on `/v1/responses`, the previously documented gateway limitation. This patch does not change gateway policy. The synthetic upstream used OpenAI wire responses: the checks cover AxonHub's inbound Anthropic/Responses conversion and tool loops, not live Anthropic/Codex subscriptions or every native outbound transformer.

## Review and production gates

Implementation and isolated verification are suitable for a draft PR. Independent review, merge and production deployment remain separate steps.

Before any production execution:

1. Review this narrow compatibility patch and explicitly schedule the RC cutover. Obtain a fresh readable database backup, archive the current frontend, and record old/new image digests in the execution ticket.
2. Apply **only** the AxonHub image change to the current deployment compose. **Do not replace the running compose with this branch's older whole file**: production already uses AgentGateway 1.7.0 and Presidio 2.2.366; this branch intentionally leaves those unrelated historical pins untouched. Casdoor, nginx, DNS and business policies are outside this change.
3. Complete live OIDC/Casdoor login/JIT, invitation/register, approval and batch-tier workflows. Re-run issue-70 seed-role hardening after any new project, and the #88/#2281 boundaries; no old defense is removed here.
4. Complete live provider-specific outbound, cancellation, caching and cost comparisons in the authorized window. Judge/auto external-model probes, OPF, disabled DLP layers and the full mutation-heavy DLP suite were not claimed as passed by this isolated run.
5. Recheck all production navigation after installing the matching frontend, unregister SW/disable cache, verify health/version/quota/cost, and retain the old image and backup.

Recovery order: stop candidate writes; restore the pre-cutover database; select the recorded beta12 digest; restore the matching archived frontend; start only the intended backend service and revalidate authz/DLP/quota. This rehearsal did not write, migrate, restart or deploy production services.


## Authorized production execution — 2026-11-16

The user subsequently approved merge and deployment. [PR #146](https://github.com/szsip239/ai4s/pull/146) was squash-merged as `b83738bc60b2509100cd2d20abb02a4a1ae29d3d`; its source tree equals the reviewed `0665098` tree. Independent Standards and Spec reviews had zero findings, and the independent browser recheck passed its recorded isolated scope. The initial isolated EDM 500 was a missing fixture fingerprint store; the existing ingest API initialized it, with beta12 and rc.4 both passing afterward. No product fallback was added.

Production cutover completed at **2026-11-16 13:03:04 UTC / 21:03:04 Asia/Taipei**:

- AxonHub image is `looplj/axonhub:v1.0.0-rc.4@sha256:86b5be6baf7517c7574976ed9c319ba1e6cea2c9a07bd749c678757ad2dfe6ed`; image and database `system_version` agree and the container is healthy.
- Frontend was built from the merged source archive; deployed index and entry `/assets/index-DUWGuDuu.js` match that build. The other six components were not restarted or upgraded. Existing gateway, OIDC, Casdoor, DLP and pricing configuration remain unchanged.
- Fresh private backup directory: `deploy/backups/rc4-cutover-20261116-125700/` on the deployment host. It retains the readable, fully decoded pre-cutover database dump, frontend archive, configuration archive and old/new image digests. The beta12 rollback image is retained; no image pruning or backup rotation was run.
- Normal public API checks for the available Luna, Sol and local Qwen routes passed 18 cases before and 18 after cutover, including nonstream, SSE, tools and cache samples. Cost samples match existing prices/multipliers. The Claude route was disabled both before and after; it is not counted as passed.
- Production authorization checks passed 17/17. Current enabled-policy DLP checks passed 41, skipped 24 disabled-layer cases, failed 0; global policy settings were not changed for testing.
- An existing linked owner completed a fresh Casdoor/Feishu/OIDC login with the same identity. Major production pages and Analytics queries were checked in a real browser with SW/cache bypass. Canonical IPv4 and IPv6 HTTP checks both returned 200. No new real user, invitation notification or approval notification was created.
- Temporary testing resources were removed. The original working directory's HEAD, twelve protected dirty-file snapshots and tracked diff were preserved, including its unpushed documentation commit.

### Explicit residual scope

Native Anthropic requests using GPT models through the current gateway remain a failed compatibility case, tracked separately in [147](https://github.com/szsip239/ai4s/issues/147). Equivalent valid nonstream/SSE/tools requests returned gateway 400 and direct AxonHub 200. Fixed gateway-v1.7.0 source and backend arrival show default Completions/GPT token normalization removes `max_tokens` while retaining `/v1/messages`. The beta12 and rc.4 inbound decoder/guard are identical. This attributes the mechanism; **an equivalent pre-cutover live beta12 sample was not recorded**, so no historical live pass/fail is claimed. No gateway route or policy was changed in this release.

The known gateway Responses 400, unavailable native Claude channel, new JIT-user creation, notification-producing invitation/approval workflows, changes to real employee tiers, disabled-policy layers and destructive real-key quota/cancellation scenarios are not reported as production passes. Employee thread deep links also retain the documented nested Project-scope limitation; a populated beta12 branch was not exercised.

Recovery still requires restoring the pre-upgrade database before starting the old image and restoring the archived frontend. The deployment was completed without exercising rollback.
