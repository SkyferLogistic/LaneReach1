# LaneReach AI + Automation backend — progress

Spec: [`SPEC_AI_AUTOMATION.md`](SPEC_AI_AUTOMATION.md) · Working rules: [`../CLAUDE.md`](../CLAUDE.md) ·
Session prompts: [`CLAUDE_CODE_PROMPTS.md`](CLAUDE_CODE_PROMPTS.md) · ADRs: [`adr/`](adr/)

Build order changed on 2026-09-28 at the project owner's request: the goal is a React frontend
where triggers and actions can be set up and tested, built **after** the backend it drives. The
workflow milestones move ahead of the AI milestone, and a frontend milestone (M7) is added, which
the spec originally left out of scope.

| Order | Milestone | Status | Branch |
|---|---|---|---|
| 1 | M1 Foundation | ✅ done (2026-09-28) | `feat/m1-foundation` |
| 2 | M3 Workflow definitions | ✅ done (2026-09-28) | `feat/m3-workflow-definitions` (stacked on M1) |
| 3 | M4 Engine | ✅ done (2026-09-28) | `feat/m4-engine` (stacked on M3) |
| 4 | M2 AI core | ✅ done (2026-09-28) | `feat/m2-ai-core` (stacked on M4) |
| 5 | M5 Reply pipeline | ✅ done (2026-09-28) | `feat/m5-reply-pipeline` (stacked on M2) |
| 6 | M6 Hardening | ✅ done (2026-09-29) | `feat/m6-hardening` (stacked on M5) |
| 7 | M7 React frontend (workflow canvas, contacts, enrollments, review, leads) | ✅ done (2026-09-29) | `feat/m7-frontend` (stacked on M6) |
| 8 | M8 Scripts from SharePoint, countries & languages, agents with signatures | ✅ done (2026-09-29) | `feat/m8-scripts-agents` (stacked on M7) |
| 9 | M9 Console redesign + scripts/agents/countries screens | ✅ done (2026-09-29) | `feat/m9-console-redesign` (stacked on M8) |
| 10 | M10 Insights & polish (lane map, globe, funnel, heatmap, activity, launch checklist, mobile) | ✅ done (2026-09-29) | `feat/m10-insights` (stacked on M9) |
| 11 | M11 Signatures, contact import, lead export, new workflow nodes | ✅ done (2026-09-29) | `feat/m11-signatures-workflows` (stacked on M10) |
| 12 | M12 CRM: pipelines, deals, tasks, notes | ✅ done (2026-09-30) | `feat/m12-crm` (stacked on M11) |
| 13 | M13 Unified inbox | ✅ done (2026-09-30) | `feat/m13-inbox` (stacked on M12) |
| 14 | M14 Campaigns, open/click tracking, deliverability, audit | ✅ done (2026-09-30) | `feat/m14-campaigns` (stacked on M13) |
| 15 | M15 Integrations (webhooks, Slack, meetings, verification) & forms | ✅ done (2026-09-30) | `feat/m15-integrations` (stacked on M14) |
| 16 | M16 Product polish: deletes, CRM nav, login, motion, new nodes | ✅ done (2026-09-30) | `feat/m16-polish` (stacked on M15) |
| 17 | M17 Whole-logistics coverage: all modes, quotes | ✅ done (2026-09-30) | `feat/m17-logistics` (stacked on M16) |
| 18 | M18 Easier workflows, useful nodes, Outbox, login | ✅ done (2026-09-30) | `feat/m18-workflows-outbox` (stacked on M17) |
| 19 | M19 Real email: SMTP sending and IMAP replies per agent mailbox | ✅ done (2026-09-30) | `feat/m19-mailboxes` (stacked on M18) |
| 20 | M20 LaneReach assistant (help articles, RAG with pgvector, draft workflows) | ✅ done (2026-09-30) | `feat/m20-assistant` (stacked on M19) |
| 21 | M21 Login redesign and layout sweep | ✅ done (2026-10-01) | `feat/m21-polish-login` (stacked on M20) |
| 22 | M22 Pricing: rate sheets, rate sources, AI-priced quote drafts | ✅ done (2026-10-01) | `feat/m22-pricing` (stacked on M21) |
| 23 | M23 Public website: what LaneReach does, AI demo, comparison, contact us, sign in | ✅ done (2026-11-02) | `feat/m23-website` (stacked on M22) |
| 24 | M24 Sales intelligence: AI lead scores, weekly AI digest, follow-ups for quiet deals | ✅ done (2026-11-02) | `feat/m24-ai-insights` (stacked on M23) |
| 25 | M25 AI cost controls: built-in messages per reply kind, AI or rules per area, a monthly AI budget enforced in the engine, a light model for reading tasks, AI usage in Settings | ✅ done (2026-11-03) | `feat/m24-ai-insights` |
| 26 | M26 Agentic assistant: "Ask LaneReach" answers from the company's own deals, quotes, contacts, rates, inbox and activity with read-only tools and links to the records | ✅ done (2026-11-03) | `feat/m24-ai-insights` |
| 27 | M27 RAG on your own history: reply drafts grounded in the office's own past answers to similar emails | ✅ done (2026-11-03) | `feat/m24-ai-insights` |
| 28 | M28 AI quality: replies by rules/AI/person, drafts sent as written/edited/rejected, follow-ups used, won vs lost scores | ✅ done (2026-11-03) | `feat/m24-ai-insights` |
| 29 | M29 Online quote acceptance: the customer opens the quote from the email and accepts or declines online | ✅ done (2026-11-03) | `feat/m24-ai-insights` |

---

## 1. System summary

1. LaneReach is a multi-tenant FastAPI backend that turns a forwarder's contacts into quote-ready leads.
2. Tenants are isolated by PostgreSQL Row-Level Security; the app connects as a role that cannot bypass it.
3. Callers authenticate with OIDC JWTs (Entra first), dev tokens (dev only) or hashed tenant API keys; endpoints check scopes.
4. The **AI module** classifies inbound replies (rules first, LLM second), extracts shipment details, drafts replies and localizes templates.
5. Every LLM call goes through a provider-agnostic client, validates output against a Pydantic schema and is logged to `ai_runs`.
6. A deterministic **policy** maps each reply bucket to an action (auto-send, draft for review, forward, none) plus protection actions.
7. The **Automation module** runs versioned workflow graphs (trigger → actions/logic/AI/protection nodes) per enrolled contact.
8. The executor advances enrollments step by step with row locks, idempotent steps, waits and retries, driven by Celery beat.
9. Every outbound email passes the guardrail chain (kill switch, suppression, freeze, frequency cap, rotation, business days, window, capacity) before `SendingProvider`.
10. v1 sends nothing real: `DryRunSendingProvider` writes to `outbox`, `FakeNotifier` records notifications, tests use the mock LLM.

## 2. File tree (M1 built; later milestones planned)

Follows spec §4. Additions/deviations are marked ★.

```
alembic/ alembic.ini                    M1  async env, migrations run as owner role
docker/Dockerfile docker-compose.yml docker-compose.test.yml Makefile Jenkinsfile .env.example   M1
docs/ PROGRESS.md SPEC_AI_AUTOMATION.md CLAUDE_CODE_PROMPTS.md adr/                           M1
http/ *.http http-client.env.json       M1 (auth, health) → M6 (every endpoint)
evals/ datasets/ run_evals.py reports/  M2
src/lanereach/
  main.py  cli.py★ (lanereach-admin bootstrap-db|migrate)                                      M1
  core/    config clock database tenancy logging errors pagination idempotency                 M1
           middleware★ container★ dependencies★ schemas★ db_bootstrap★ model_registry★         M1
           rate_limit                                                                          M6
  auth/    dependencies oidc dev_tokens api_keys webhook_signatures roles router               M1
           principal★ jwt_claims★ models★ repository★ schemas★ service★                        M1
  tenants/ models★                                                                             M1
  health/  router★                                                                             M1
  domain/  offices senders companies contacts messages suppressions leads templates calendars  M2–M5
  ai/      llm/ prompts/ rules/ tasks/ runs/ schemas router                                    M2
           pipeline policy review/                                                             M5
  automation/ nodes/ conditions workflows/                                                     M3
              events/ enrollments/ engine/ guardrails/                                         M4
  integrations/ sending/ notifications/                                                        M4 (webhook router M5)
  workers/ celery_app tasks                                                                    M1 skeleton → M4
tests/ conftest factories★ oidc_support★ unit/ integration/ scenarios/                        M1 → M6
```

## 3. Database tables

Every table has `tenant_id uuid NOT NULL` (FK `tenants.id ON DELETE CASCADE`, indexed) and the policy
`tenant_isolation USING/WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)`,
created by `lanereach.core.tenancy.tenant_rls_statements()`. Exception: `tenants` is keyed by `id` (policy on `id`).
All primary keys are app-generated UUIDs; all timestamps are `timestamptz` set from the `Clock`.

| Table | Milestone | Key columns | Indexes / constraints |
|---|---|---|---|
| `tenants` | M1 ✅ | name, slug, status (`active`/`suspended`) | uq slug; ck status |
| `tenant_identity_providers` | M1 ✅ | issuer, external_tenant_id, jwks_uri, display_name, is_active | uq (issuer, external_tenant_id) |
| `api_keys` | M1 ✅ | name, key_prefix, key_hash (sha256), environment, scopes[], created_by, last_used_at, expires_at, revoked_at | uq key_prefix; ck environment |
| `webhook_receipts` | M1 ✅ | provider, event_id, received_at | uq (tenant_id, provider, event_id) |
| `idempotency_keys` | M1 ✅ | key, request_fingerprint, response_status, response_body jsonb, expires_at | uq (tenant_id, key) |
| `offices` | M2 | name, country, timezone, owner_email, sending_paused | uq (tenant_id, name) |
| `senders` | M2 | office_id, display_name, email, daily_cap, flagged_at | uq (tenant_id, email) |
| `companies` | M2 | name, domain, frozen_at, frozen_reason | ix (tenant_id, domain) |
| `company_branches` | M2 | company_id, country, city | ix (tenant_id, company_id) |
| `contacts` | M2 | company_id, branch_id, email, first/last name, country, timezone, language, stage, owner, tags[] | uq (tenant_id, lower(email)); gin tags |
| `messages` | M2 | contact_id, direction, provider_message_id, thread_id, in_reply_to, subject, body, received_at, processed_at | uq (tenant_id, provider_message_id); ix thread |
| `templates` | M2 | key, language, version, subject, body | uq (tenant_id, key, language, version) |
| `country_calendars` | M4 | country, weekend_days[], extra_holidays, timezone | uq (tenant_id, country) |
| `suppressions` | M4 | contact_id / email, reason, source, expires_at | ix (tenant_id, contact_id) |
| `ai_runs` | M2 | task, prompt_version, provider, model, input_ref, input_hash, output_json, confidence, status, error, latency_ms, tokens_in/out, estimated_cost | ix (tenant_id, created_at), (tenant_id, task) |
| `workflows` | M3 | name, description, status, current_version_id | uq (tenant_id, name) |
| `workflow_versions` | M3 | workflow_id, version, graph jsonb (immutable), created_by, published_at | uq (workflow_id, version) |
| `events` | M4 | type, payload, contact_id, occurred_at, processed_at, idempotency_key | uq (tenant_id, idempotency_key); partial ix unprocessed |
| `enrollments` | M4 | workflow_version_id, contact_id, status, current_node_id, next_run_at, variables, stop_reason, attempt | partial uq (workflow_id, contact_id) WHERE active-ish; ix (status, next_run_at) |
| `enrollment_steps` | M4 | enrollment_id, step_index, node_id, node_type, started/finished_at, result, handle_taken, guardrail_decisions, error, idempotency_key | uq idempotency_key (`enrollment_id:step_index`) |
| `outbox` | M4 | to, from, subject, body, thread refs, headers (incl. List-Unsubscribe), provider_message_id | ix (tenant_id, created_at) |
| `company_office_starts` | M4 | company_id, office_id, iso_week | uq (tenant_id, company_id, iso_week) — rotation guardrail |
| `mailbox_daily_usage` | M4 | sender_id, day, sent_count | uq (sender_id, day) |
| `notifications` | M4 | channel, recipient, payload (FakeNotifier storage) | ix (tenant_id, created_at) |
| `review_items` | M5 | kind, payload, status, reviewer, decided_at, message_id | ix (tenant_id, status) |
| `tenant_reply_policies` | M5 | bucket, action_mode, auto_send_enabled, overrides | uq (tenant_id, bucket) |
| `leads` | M5 | contact_id, message_id, shipment jsonb, status | ix (tenant_id, created_at) |

Pre-authentication cross-tenant reads happen only through `SECURITY DEFINER` functions (M1:
`auth_find_identity_provider`, `auth_find_api_key`, `auth_tenant_is_active`; M4 will add one that lists
tenant IDs with due enrollments for the dispatcher). They are executable by the app role only, not `PUBLIC`.

## 4. API surface and scopes

All under `/api/v1`. `Idempotency-Key` accepted on side-effecting POSTs. Errors are `application/problem+json`.

| Method | Path | Scope | Request → Response | M |
|---|---|---|---|---|
| POST | `/auth/dev-token` | none (dev mode only) | DevTokenRequest → DevTokenResponse | 1 ✅ |
| GET | `/auth/me` | any authenticated | → PrincipalResponse | 1 ✅ |
| POST | `/auth/api-keys` | admin:manage | ApiKeyCreateRequest → ApiKeyCreatedResponse | 1 ✅ |
| GET | `/auth/api-keys` | admin:manage | → Page[ApiKeyResponse] | 1 ✅ |
| DELETE | `/auth/api-keys/{id}` | admin:manage | → ApiKeyResponse | 1 ✅ |
| GET | `/health/live`, `/health/ready` | none | → LivenessResponse / ReadinessResponse | 1 ✅ |
| POST | `/ai/replies/classify` | ai:run | ClassifyReplyRequest → AITaskResponse[ReplyClassification] | 2 |
| POST | `/ai/replies/extract-shipment` | ai:run | ExtractShipmentRequest → AITaskResponse[ShipmentDetails] | 2 |
| POST | `/ai/replies/draft` | ai:run | DraftReplyRequest → AITaskResponse[ReplyDraft] | 2 |
| POST | `/ai/templates/localize` | ai:run | LocalizeTemplateRequest → AITaskResponse[LocalizedTemplate] | 2 |
| POST | `/ai/replies/{message_id}/process` | ai:run | → ReplyProcessingResult | 5 |
| GET | `/ai/runs`, `/ai/runs/{id}` | ai:run | → Page[AIRunResponse] / AIRunResponse | 2 |
| GET | `/ai/reviews`, `/ai/reviews/{id}` | ai:review | → Page[ReviewItemResponse] / ReviewItemResponse | 5 |
| POST | `/ai/reviews/{id}/approve` · `/edit-and-approve` · `/reject` | ai:review | ReviewDecisionRequest → ReviewItemResponse | 5 |
| GET | `/automation/nodes`, `/automation/nodes/{node_type}` | workflows:read | → list[NodeTypeResponse] / NodeTypeResponse | 3 |
| POST | `/automation/workflows` | workflows:write | WorkflowCreateRequest → WorkflowResponse | 3 |
| GET | `/automation/workflows`, `/{id}`, `/{id}/versions` | workflows:read | → Page[WorkflowResponse] / WorkflowResponse / list[WorkflowVersionResponse] | 3 |
| PUT | `/automation/workflows/{id}` | workflows:write | WorkflowUpdateRequest → WorkflowVersionResponse | 3 |
| POST | `/automation/workflows/{id}/validate` | workflows:write | → ValidationReport | 3 |
| POST | `/automation/workflows/{id}/publish` · `/pause` · `/resume` | workflows:publish | → WorkflowResponse | 3/4 |
| POST | `/automation/workflows/{id}/test-run` | workflows:write | TestRunRequest → TestRunResponse | 4 |
| POST | `/automation/workflows/{id}/enrollments` | enrollments:write | EnrollmentCreateRequest → EnrollmentResponse | 4 |
| GET | `/automation/enrollments`, `/{id}`, `/{id}/steps` | workflows:read | → Page[EnrollmentResponse] / … / Page[EnrollmentStepResponse] | 4 |
| POST | `/automation/enrollments/{id}/pause` · `/resume` · `/cancel` | enrollments:write | → EnrollmentResponse | 4 |
| POST | `/automation/events` | events:write | EventCreateRequest → EventResponse | 4 |
| GET | `/automation/events` | workflows:read | → Page[EventResponse] | 4 |
| POST | `/webhooks/sending/{provider}` | HMAC signature | provider payload → WebhookAcceptedResponse | 5 |
| CRUD | `/offices` `/senders` `/companies` `/contacts` `/templates` `/suppressions` | reads workflows:read, writes admin:manage | per resource | 2–4 |
| GET | `/leads` | ai:review | → Page[LeadResponse] | 5 |
| GET | `/outbox` (non-production only) | admin:manage | → Page[OutboxMessageResponse] | 4 |

Role → default scopes (`auth/roles.py`): admin = all · campaign_manager = ai:run, workflows:*, enrollments:write,
events:write · office_owner = ai:run, ai:review, workflows:read, enrollments:write · pricing = ai:run, ai:review,
workflows:read · reviewer = ai:review, workflows:read · viewer = workflows:read.

## 5. Risks, ambiguities and decisions

Where the spec was silent, the safest option was chosen. **Open** items need a product decision.

| # | Topic | Decision / recommendation |
|---|---|---|
| D1 | Python version | Spec says 3.12; **project owner chose the installed 3.13** (2026-09-28). CLAUDE.md updated. |
| D2 | `tenants` has no `tenant_id` | Its `id` is the tenant key; RLS policy on `id`. Architecture test exempts only this table. |
| D3 | Lookups before the tenant is known (API key, IdP identity) | Narrow `SECURITY DEFINER` functions instead of a BYPASSRLS role; app role still can't read tables cross-tenant. |
| D4 | OIDC with many customer IdPs | Issuer + external tenant registered per tenant with a stored `jwks_uri`; unregistered issuers rejected *before* any network call (no SSRF via `iss`). RS256 only. |
| D5 | OIDC scopes | Derived from app roles (`roles` claim) only; `scp` ignored in v1. Unknown role names ignored. |
| D6 | `lr_live_` vs `lr_test_` | Production issues `live` keys; every other environment issues `test` keys. |
| D7 | API keys minting API keys | Allowed with `admin:manage`, but only for scopes the caller holds (no escalation). |
| D8 | Idempotent replay of API-key creation | Replays return the original metadata with `secret: null`; the one-time secret is never persisted. |
| D9 | Idempotency storage | Key claimed inside the request transaction, so key + side effects commit atomically; TTL 24 h; same key + different body → 422. |
| D10 | JWT time checks | Done against the injected `Clock` (not PyJWT's wall clock), 30 s leeway, `exp`/`nbf`/`iat` all required. |
| D11 | Host ports | Postgres 15432, Redis 16379, API 8080 — outside Windows' dynamic/Hyper-V reserved ranges and clear of other local projects. |
| D12 | Webhook tenant resolution (M5) | **Recommend:** per-provider HMAC secret; `tenant_id` carried inside the *signed* body (a header would be unsigned). Unknown provider and bad signature return the same 401. |
| D13 | "Every path ends in `stop` or a terminal action" | **Implemented (M3):** node classes declare `is_terminal` (`stop`, `create_pricing_lead`, `notify_office_owner`, `notify_teams_channel`, `suppress_contact`, `freeze_company`, `pause_workflow_for_company`). Every output handle of a non-terminal node must be connected. |
| D14 | "`send_email` preceded by a wait" | **Implemented (M3):** on every path, each `send_email` after the first needs a `wait_*` node between it and the previous send (a wait on only one incoming branch is not enough). |
| D15 | Cold vs thread email | `send_email` = cold (frequency cap + rotation apply); `reply_in_thread` = exempt from cap and rotation, but not from suppression/freeze/kill switch. |
| D16 | Company rotation | The first office to *send* to any branch of a company in an ISO week owns that week; others are deferred to the next ISO week. |
| D17 | Weekends | Stored per country in `country_calendars` (seeded; e.g. SA/EG Fri–Sat, AE Sat–Sun since 2022), holidays from the `holidays` library plus tenant overrides. |
| D18 | Recipient local time | `contact.timezone`, else the country's zone (multi-zone countries fall back to the office's zone and log a warning). |
| D19 | Test-run "persists nothing" (M4) | **Recommend:** run the real engine in a transaction that is always rolled back, with `ManualClock`, mock LLM and dry-run provider — exercises real code paths. |
| D20 | Celery + async code | Each task runs `asyncio.run(...)` with an engine created per worker process; tasks take `tenant_id` and open a tenant session; `acks_late` so tasks must be idempotent. |
| D21 | Dispatcher across tenants (M4) | A `SECURITY DEFINER` function returns only tenant IDs with due enrollments; batches are then processed in per-tenant sessions. |
| D22 | pgvector | Extension installed in bootstrap; no vector columns in v1 (reserved for reply similarity / eval later). |
| D23 | Saving a workflow | Every `PUT` creates a new immutable version (a DB trigger rejects changes to a stored graph). Drafts may be invalid so the canvas can save work in progress; only publishing validates. `base_version` gives optimistic concurrency (409 on a stale save). |
| D24 | Workflow status | `draft` → `published` → `archived`. Pause/resume is a flag (`paused_at`) on a published workflow, not a status. Added `POST /archive` (the spec defines the status but no endpoint); archived workflows are read-only. |
| D25 | Additive endpoints for the future canvas | `POST /automation/workflows/validate-graph` (live validation without saving), `GET /automation/condition-fields` (the DSL whitelist with operators), `GET /workflows/{id}/versions/{version}`. |
| D26 | Condition DSL semantics | String comparisons are case-insensitive. A missing value makes `ne`/`not_in` true and everything else false. A runtime type mismatch is false and never raises. Limits: depth 5, 50 comparisons. |
| D27 | Validation with cycles | Cycles are reported, and the other path rules still run on the graph without the cycle-closing edges, so users see every problem at once (spec scenario 11). |
| D28 | Cross-tenant foreign keys | Postgres checks foreign keys without applying RLS, so child tables reference parents by `(tenant_id, parent_id)` → parent `UNIQUE (tenant_id, id)`. Applied to `workflow_versions`; the pattern for every later child table. |
| D29 | Node references (`office_id`, `template_key`) | Only the shape is validated in M3; checks that they exist are added in M4, when offices and templates exist. |
| D30 | Follow-ups vs the 30-day cap | Spec scenario 1 (follow-up after 5 business days) conflicts with "1 cold email per 30 days" if a follow-up counts as cold. **Decision:** the first `send_email` of an enrollment is `cold`; later ones are `follow_up`, sent in the first email's thread (`Re: <subject>`) and, like thread replies, exempt from the frequency cap and rotation. All other guardrails still apply. |
| D31 | Guardrail chain behaviour | Stops at the first block or defer. A deferred email is re-evaluated in full when it wakes. Approved replies (review queue, M5) skip business days and the window, since a person chose to answer now. |
| D32 | Kill switch re-check | Deferred emails re-check every 15 minutes (`KILL_SWITCH_RECHECK_MINUTES`). After resume, the window and business-day guardrails place them in the next slot (scenario 12). |
| D33 | Rotation week | ISO week in UTC. The office that sends first owns the week (insert on a unique key, race-free). |
| D34 | Business-day waits | `wait_business_days` lands on the recipient's window start on the target business day, in the contact's timezone (else their country's). |
| D35 | Tick = one transaction | A crash rolls back the whole tick. The re-run uses the same `enrollment_id:step_index` key, which the provider honours, so no double send. Nodes run in savepoints, so a failing node rolls back only its own writes. |
| D36 | Test-run | Runs the real engine in a savepoint that is always rolled back, with a `ManualClock` and no task queue. Drafts and paused workflows can be tested; invalid graphs are refused with the validation errors. |
| D37 | Testing aids | `POST /automation/enrollments/{id}/advance` (run now, respects waits), `/wake` (skip a wait, non-production only) and `POST /automation/dispatch` (caller's tenant only, non-production). |
| D38 | Unsubscribe | HMAC-signed tokens (tenant + contact). The public GET/POST endpoint (RFC 8058 one-click) adds a suppression, sets Do Not Contact and stops open enrollments. |
| D39 | Model choice | Model is configuration (`LLM_MODEL`); the documented default for Anthropic is `claude-opus-5` with `LLM_EFFORT=low` (classification/extraction don't need deep reasoning). Swapping to a cheaper model is a setting change, validated with `make eval`. |
| D40 | Refusals | Anthropic's server-side refusal fallback (beta) is on by default (`ANTHROPIC_REFUSAL_FALLBACK`). A final refusal raises `LLMRefusalError`; the pipeline (M5) routes those replies to human review. |
| D41 | Mock provider | Deterministic heuristics (keywords, regex, port gazetteer) so the product and the frontend work end to end without an API key. They follow the prompt contract, e.g. injection attempts -> `unclear` + needs_human. Never used when a real provider is configured. |
| D42 | Rules inside `classify_reply` | The rules pre-classifier runs inside the task (and so in the API), not only in the pipeline, so automatic messages never cost an LLM call. Rule matches are recorded as `rule_matched` runs. |
| D43 | AI run storage | Recorded in their own transaction (a failed call is kept even when the request rolls back); only an input hash and message reference are stored, never the email text. |
| D44 | Placeholder rule | Localization must keep the exact multiset of `{{...}}` tokens including fallbacks; anything else is rejected, retried once, then fails. |
| D45 | Webhook body | `{tenant_id, events[]}` inside the signed body, at most 100 events. Replay protection is per event: a replayed event is reported as `replayed event` in a 202 (a provider retrying a half-processed batch must not get an error), never processed twice. Events matching no contact are skipped: LaneReach only processes answers to its own outreach. |
| D46 | Matching inbound mail | The Message-ID it answers (`in_reply_to`, or `original_message_id` for bounces/complaints) identifies contact and thread; otherwise the sender (replies) or recipient (bounces) address and the latest thread with that contact. |
| D47 | AI failure in the pipeline | An LLM error never drops a reply: it becomes `unclear` (source `fallback`) and goes to human review. The protective actions of the decided bucket still run (only ever less email). |
| D48 | Tenant reply policy | Only conversational buckets (default action is not `none`) can be changed, and never to `none` (a human reply must be answered or forwarded). Auto-send needs `action_mode=auto_send` **and** `auto_send_enabled`, and still only applies to confident classifications. Protective buckets are fixed. Admin-only (`admin:manage`). |
| D49 | Uncertain protective replies | A complaint/unsubscribe/bounce the classifier is unsure about goes to review as a *classification* item, never as a reply draft. |
| D50 | AI nodes vs the pipeline | Nodes reuse what the pipeline stored for the same message (classification, extraction, pending/decided draft). If the pipeline already auto-sent an answer, the draft is marked `sent` so a workflow never answers twice. |
| D51 | `reply_in_thread` + `use_ai_draft` | Reads the draft's live review state: `pending` waits (approval wakes the enrollment at once; hourly re-check as fallback), `sent` continues without sending, `rejected` continues without sending (or uses the template when configured), `approved` (pre-approved by auto-send policy) sends through the guardrails. |
| D52 | Dispatcher safety net | Inbound messages still unprocessed 2 minutes after they were stored are re-enqueued (migration 0005 extends `automation_tenants_with_work`, partial index on unprocessed inbound). |
| D53 | Referrals | The first new address in the reply body becomes a contact (same company and office, tag `referral`). It is not enrolled automatically: a workflow on `contact.created` with that tag can do it. |
| D54 | Later / not interested | "Later" re-enrolls in the stopped workflows at the start of the sending window on the stated date or after `LATER_REENROLL_DAYS` (60). "Not interested" adds a `REST` suppression for `NOT_INTERESTED_REST_DAYS` (180). |
| D55 | Soft bounces | Each soft bounce suppresses for `SOFT_BOUNCE_RETRY_HOURS` (24); the `SOFT_BOUNCE_LIMIT`-th (3) within `SOFT_BOUNCE_WINDOW_DAYS` (30) suppresses permanently and stops outreach. |
| D56 | AI rate limits (closes O1) | Fixed one-minute windows in Redis, per tenant (`AI_RATE_LIMIT_PER_TENANT_PER_MINUTE`, 300) and per caller — user or API key (`AI_RATE_LIMIT_PER_CALLER_PER_MINUTE`, 60) — on the endpoints that call the LLM (classify, extract, draft, localize, process). 429 with `Retry-After`. If Redis is down the request is allowed and a warning logged: the limiter protects cost and must not take AI down. |
| D57 | Data retention | Daily beat task (and `lanereach-admin purge-expired-data`): after `DATA_RETENTION_DAYS` (365) message and outbox bodies, AI run outputs, review payloads and pipeline summaries are removed; rows stay (history, caps, reporting). A purged processed message cannot be re-processed (409). Runs in every tenant, suspended ones included (`maintenance_list_tenants()`, migration 0006). |
| D58 | Seed data | JSON dataset (`src/lanereach/seed/data/demo_tenants.json`) + generic loader, so tenant names stay out of code. Runs as the owner role; idempotent per tenant slug (an existing tenant is never touched). Workflows reference offices as `@office:<key>`. |
| D59 | Duplicate leads | `create_pricing_lead` reuses the lead the reply pipeline already created for the same reply (links the enrollment, raises priority when asked) instead of creating a second one. |
| D60 | Test-runs and the LLM | Test-runs always use the deterministic mock LLM, whatever `LLM_PROVIDER` is: simulations are free, repeatable and never send contact data to an external model. |
| D61 | Unsubscribe links | GET shows a confirmation page; only POST (the button, or the RFC 8058 one-click POST from mail clients) unsubscribes, so link scanners and previewers cannot unsubscribe people. Staging/production refuse the default signing secret. |
| D62 | Idempotency-Key scope | Every POST that changes state accepts it, including run-now `advance`. Exceptions: the signed webhook (event-ID replay store), unsubscribe (idempotent), dev-token, pure validations, rolled-back test-runs, dev/staging helpers, and the stateless AI task endpoints (they only add an audit row; retries cost tokens but change nothing). |
| D63 | Small reference lists | `GET /offices`, `/senders`, `/templates` return whole lists (bounded per tenant, used as pickers) rather than cursor pages; the large collections (contacts, companies, enrollments, events, reviews, leads, outbox, AI runs) are paginated. |
| D64 | Console stack | React 19 + TypeScript (strict) + Vite, React Flow (`@xyflow/react`) for the canvas, TanStack Query, React Router. No UI kit: a small token-based CSS (light/dark). |
| D65 | API contract | TypeScript types generated from the backend OpenAPI (`openapi-typescript`, `openapi-fetch`); `make web-types` refreshes them, so contract drift fails the typecheck. |
| D66 | One origin | The console calls `/api` on its own origin: Vite proxies in dev, nginx in Compose. CORS stays closed. |
| D67 | Console sign-in | Dev mode only (tenant picker + dev token, 12 h). The session is kept in localStorage when available. OIDC sign-in is added with the IdP integration. |
| D68 | Node forms | Config forms are rendered from each node's JSON Schema, so new node types appear in the palette with a working form without frontend changes. Fields named `office_id`, `sender_id`, `template_key`, `condition` get tenant-aware pickers/builders. |
| D69 | Side effects from the console | Every POST/PUT/PATCH/DELETE carries a fresh Idempotency-Key (a retried click never repeats an action). Destructive actions (archive, cancel, lift suppression, revoke key, enable auto-send) ask for confirmation. |
| D70 | Scripts library | SharePoint is the master copy. Layout `Scripts/{Air\|Ocean\|General}/<script name>/<language>.docx` (+ `.pdf` reference copy), `Banners/{Air\|Ocean}/<CC or country name\|default>.png`, `Brand/logo*.png`, `badge*.png`. Script-sourced templates are read-only in the app ("edit the Word document"). A `LocalDocumentStore` with the same layout stands in until the Azure app exists. |
| D71 | Sync | Celery beat every `SCRIPT_SYNC_INTERVAL_SECONDS` (300) plus "Sync now". Graph: client-credentials token, recursive `children` walk (delta omits `parentReference.path`), a changed eTag re-imports, removed files make the template `inactive` (never deleted). An empty listing for a library that had scripts changes nothing (guards against a mistyped folder wiping every script). |
| D72 | Word import | mammoth → HTML, sanitised with nh3, inline styles, 640 px wrapper, `dir="rtl"` for Arabic. First line `Subject: …` (localised labels accepted). Single-brace placeholders (`{first_name\|there}`, `{to_country}`, `{network}`, `{banner}`, `{signature}`, `{unsubscribe_link\|text}`…) map to the canonical `{{…}}` syntax and survive Word splitting them across runs. A missing signature or unsubscribe link is appended, with an import warning. Embedded images become `email_assets`. |
| D73 | Languages | Template languages are variants (`en`, `en-na`, `pt-br`, `pt-pt`, `zh-hans`, `zh-hant`…, pattern `^[a-z]{2}(-[a-z0-9]{2,4})?$`); contact/office languages stay 2-letter. The `countries` table (53 rows seeded from Skyfer's grouping into 18 languages, editable at `/countries`) decides first, then the base language, contact, office, English. |
| D74 | USA / Canada (answer: no preference) | Safest option: they map to **English (North America)** (`en-na`) with `allow_language_fallback=false`, so without an active `en-na` script the step is **held** (re-checked every 12 h) instead of sending another country's wording. |
| D75 | `{to_country}` | The country's phrase is written in its own language (`Türkiye'ye`), so it is used only when the script is in that language. A fallback English script gets "to <country>" ("to the Netherlands"); any other fallback language gets the plain name. Found in the live check: the English sample for Turkey read "from the USA or Canada Türkiye'ye". |
| D76 | Agents | Senders have `service_line` (`air\|ocean\|any`), title, phone and a signature mode (generated from tenant branding like Molly's block, sanitised custom HTML, or an uploaded image). `send_email` has `service_line`; agents rotate within the line (fewest emails today) and the thread's agent is kept for follow-ups while still usable. CSV import (`POST /senders/import`) upserts by email. |
| D77 | AI translations | "Translate missing languages" (max 6 per call, AI rate-limited) writes `<language>.docx` into the library with status `ai_draft`; drafts are never sent until someone with `workflows:publish` approves them. |
| D78 | Email images | Banners, logos and inline images are served at `GET /public/assets/{token}`: HMAC token (key derived from the unsubscribe secret) over tenant + asset, PNG/JPEG/GIF/WEBP only (no SVG), ≤ 5 MB, `nosniff` + CSP + long cache. Unknown or forged tokens get 404. |
| D79 | HTML email | Values are HTML-escaped in `html_mode`; only pre-built `SafeHtml` (signature, banner) is inserted as is. The outbox stores `html_body` (purged by retention with `body`). |
| D80 | Seed top-up | `make seed` on an existing tenant adds what later milestones introduced (countries, branding, agents' lines/titles, sample scripts) and never overwrites anything already there. |
| D81 | Console look | Unique freight theme rather than a copy of the reference site: navy shell, signal blue, amber = air, cyan = ocean, Space Grotesk headings. Motion is SVG/CSS (routes with a plane and a ship, count-ups, skeletons) instead of GIFs: sharper, a few KB, themable, and switched off under `prefers-reduced-motion`. |
| D82 | Dashboard numbers | `GET /dashboard/stats?days=7\|30\|90` (read scope) aggregates in SQL: sent/replies/leads/bounces per UTC day, the previous period, reply buckets, today's capacity per line (on each office's own day, like the caps), top agents, pipeline counts and script coverage with held countries. UTC days keep one clock for the chart across offices. |
| D83 | Email previews | Rendered in an iframe with `sandbox` (no scripts), a CSP that only allows inline styles and images, and `target=_blank` links; the HTML is already sanitised by the backend. `allow-same-origin` is kept only to measure the height (without scripts it cannot be used). |
| D84 | Uploads through nginx | `client_max_body_size` raised from 2 MB to 11 MB so 10 MB Word files reach the API (which enforces `MAX_UPLOAD_BYTES`). Found in the M9 live check. Security headers are repeated in locations that set their own headers (nginx does not inherit them). |
| D85 | Insights | `GET /dashboard/insights` counts distinct contacts per funnel stage (positive = interested, meeting, rate/service/partnership query, referral), emails/replies/leads per contact country, and replies per weekday × hour **in the sending office's time zone** (UTC when a message has no office). `GET /dashboard/activity` merges the latest messages and leads (complaints left out), newest first, at most 50. |
| D86 | Lane origin | Maps start lanes from `branding.lane_origin_countries` (first code = origin; all are left out of destinations), else the first office's country, so no tenant's desk is hardcoded. Seeded as `CA, US` for the demo tenant; set in Settings → Branding. |
| D87 | Maps | d3-geo + world-atlas 1:110m, loaded on demand (own chunks), country codes via i18n-iso-countries; countries too small for that scale (Bahrain, Hong Kong…) get a fixed point. The login globe is canvas (60 fps without re-rendering React), draggable, and static under reduced motion. |
| D88 | Phones | The sidebar becomes a slide-in drawer; secondary top-bar items hide; grid children may shrink (`min-width: 0`) so long lines never widen the page. |
| D89 | Signature pictures | Offices and agents can sign with a picture (PNG/JPEG/GIF/WebP ≤ 5 MB, stored as a `signature` asset, public signed link). An office picture turns plain templates into HTML too (text escaped, paragraphs kept, `{{office.signature}}` replaced or appended); the text signature stays for the text part and AI drafts. Migration 0008 adds `offices.signature_asset_id` and `senders.mobile`. |
| D90 | Generated signature layout | Follows the forwarder's own block: agent, title, accent bar and QR code; logo, tagline, offices; phone (extension dialled after a pause: `tel:9056775000,332`), WhatsApp (`wa.me`), fax, email, address, website; badge row; linked promo banner. Brand images live in the library's `Brand/` folder (`logo*`, `qr*`, `promo*`, `badge*`) and can be uploaded from Settings. |
| D91 | Contact import | `POST /contacts/import` (CSV, ≤ 5,000 rows): upsert by email, companies/offices by name (missing companies created), country as ISO code or countries-table name, language as code or name (stored as the base language). Rows with problems are reported and skipped; an optional tag for every row starts the workflows listening to it. |
| D92 | Lead export | `GET /leads/export` (pricing scope): CSV with the contact, company and extracted shipment, UTF-8 BOM for Excel, cells starting with `= + - @` prefixed (formula injection). |
| D93 | New nodes | `trigger_lead_status_changed` (lead status update publishes `lead.status_changed`; the update moved from the router to `domain/leads/service.py`), `trigger_custom_event` (`custom.event` with `payload.name`; `contact_email` accepted instead of `contact_id`; simple payload values become enrollment variables), `switch_on_field` (multi-way branch on any condition field, `other` for the rest), `enroll_in_workflow` (hands the contact to another published workflow; publish checks the target exists, warns when it is not running). Webhooks to arbitrary URLs were left out on purpose (server-side request forgery risk) — integrations use the event API. |
| D94 | Permissions in the console | Office and agent changes need `admin:manage` (as the API does); the buttons follow that scope. |
| D95 | Warmbly review (2026-09-30) | Adopted for LaneReach: CRM (pipelines, deals, tasks, notes), unified inbox, campaign results + per-campaign schedule, **open and click tracking** (user's choice), mailbox health, audit log, integrations (signed webhooks for Zapier/Make/n8n, Slack, Calendly/Cal.com, email verification), web forms. Left out: mailbox warmup (needs a network of real mailboxes), AI writing assistant (scripts are written in Word). Own CRM, no external CRM sync (user's choice). |
| D96 | Deals and leads | A lead stays the AI record of a rate request; the deal is the pricing team's object. A new lead opens one deal (unique `lead_id`) in the default pipeline's first stage, titled "<company>: <origin> → <destination>" from the extracted shipment; existing leads were backfilled by the seed top-up. Moving to a won/lost stage closes the deal and mirrors won/lost (open → quoting) onto the lead, so M11's lead trigger keeps working; every stage change publishes `deal.stage_changed`. |
| D97 | Pipelines | Default "Freight quotes": Quote requested 10 % → Sourcing rates 30 % → Quoted 50 % → Negotiating 70 % → Won → Lost. Admins add pipelines and edit stages (2–12, at least one open, one won, one lost); stages keep their id (and deals) when renamed or reordered; a stage with deals cannot be removed. Board order is a fractional `board_position` so a drop between two cards changes one row. |
| D98 | CRM scope | New `crm:write` for admin, campaign manager, office owner and pricing; reading follows `workflows:read`. Notes are deleted by their author or an admin. Deal values are summed as stored (the console shows each currency as is). |
| D99 | Dev tooling | Windows Application Control started blocking the compiled Python extensions in `.venv` (2026-09-30). The host security setting was left alone; `docker/Dockerfile.dev` + `make check-docker` run ruff, mypy, pytest and Alembic in Linux. Known flake of that path: under load a few tests can time out connecting to the testcontainers database through `host.docker.internal` (different tests each run; they pass on rerun). |
| D100 | Dialog layers | Drawers and modals render through a portal on `<body>`, so the top bar (its own layer since M10) and page animations never cover them. |
| D101 | Conversations are computed | The inbox groups `messages` by `thread_id` at query time (indexed by tenant and thread) instead of keeping a `conversations` table in sync with every write path; only what people do (read, done, snoozed, assignee, labels) is stored, in `conversation_states`. "Done" and "read" are timestamps compared with the last message, so a new reply brings a conversation back without any trigger. The plan's table was dropped for this (less code, nothing to backfill). |
| D102 | Manual replies | A reply written in the inbox is an `APPROVED_REPLY` (same guardrails as an approved review-queue draft: kill switch, suppression, company freeze, window, mailbox capacity), sent in the thread from the agent who last wrote in it (another agent of the office when that one is at its cap), with the agent's signature (or the office's signature picture) added as text and HTML. The typed text is escaped. Blocked → 422, deferred → 409. |
| D103 | Inbox scopes | Reading follows `workflows:read`; marking and replying needs `ai:review` (reviewers, office owners, pricing, admins), the people who already answer AI drafts. Threads are addressed with a `thread_id` query parameter because provider thread ids can contain `/`. |
| D104 | Open and click tracking | HTML emails only (plain text cannot carry a pixel): at send, after the guardrails allowed it, `deliver_email` gives the message a `tracking_id`, rewrites `http(s)` links to other sites through `/api/v1/t/c/{token}` and appends a pixel `/api/v1/t/o/{token}`. `mailto:`, `tel:`, anchors and LaneReach's own links (unsubscribe, images) stay direct. Tokens are HMAC-signed (key derived from the unsubscribe secret with its own context); a click token carries its target URL, so only links LaneReach put in its own emails redirect (no open redirect) and no row per link is needed. On by default per tenant (Settings), off per `send_email` node. |
| D105 | Bots and privacy | Opens and clicks from mail scanners (user agent), with no user agent, or within 5 s (open) / 10 s (click) of sending are stored with `suspected_bot` and never counted. Only a client class (`outlook`, `gmail-proxy`, `apple-mail`, `browser`, `bot`...) is stored, not the user agent or IP. Apple Mail Privacy Protection preloads images like a person would and counts as an open (stated in the UI). Open/click rows are deleted with the data retention period; per-message counters stay. A broken pixel token still gets the image; a failure while recording never blocks the redirect. |
| D106 | `wait_for_engagement` | Watches the enrollment's last email for N business days (recipient country), `wait_for` open or click; outputs `clicked`, `opened`, `no_engagement`. It re-runs on wake (`Wait(handle=None)`) and keeps its deadline in `engagement:<node>`; the first human open or click wakes the enrollment at once (like an approved draft wakes `reply_in_thread`). An untracked email leaves by `no_engagement` straight away. It counts as a wait for the "wait between emails" rule. Events `email.opened` / `email.clicked` are published once per message. |
| D107 | Campaign schedule | Per workflow: start/end dates and weekdays (recipient local day). Guardrail `campaign_schedule` (after company rotation, before business days) defers cold and follow-up emails to the next allowed day at the window start and blocks them after the end date (the enrollment stops). It narrows the tenant rules, never widens them; replies are not held. A per-campaign time window was left out: the tenant window already applies and a second one could only narrow it further. |
| D108 | Campaign results | Counted from the workflow's own outbound messages (sent, tracked, opened, clicked, links) and, because inbound mail does not carry the enrollment, per enrolled contact after their first enrolment (replied, positive, bounced, unsubscribed, quote requests, deals, won). Steps: enrollments per node and the handle taken. The nav label is now **Campaigns** (the route stays `/workflows`). |
| D109 | Mailbox health | Per agent over 30 days: sent, bounces, complaints, replies, opens, cap use today. Score: 100 minus the bounce rate above 2 % (up to -50) and -30 for complaints above 0.1 %; a flag caps it at 20 (`at_risk`), switched off is `paused`. Below 20 sends it carries a "few emails yet" note. It only informs: the existing flag remains the one automatic stop. |
| D110 | Audit log | Recorded centrally: the tenant-session dependency adds an `audit_events` row for every successful POST/PUT/PATCH/DELETE in the same transaction (failed requests leave none), with the route summary as the text, the endpoint name as the action, the entity from the path and the top-level request fields (secret-looking keys and bodies dropped, lists shown as values or counts). High-volume routes are left out (inbox, notifications, AI runs, events, tasks, notes, test-runs, validation, previews). Admins read it in Settings → Audit log; rows are kept two years. |
| O8 | **Open:** Tracking disclosure | Some countries expect a notice or consent for open tracking in marketing email (for example in the EU). Confirm with Skyfer whether to keep tracking on for every destination, switch it off per campaign, or add a line to the privacy notice. |
| D111 | Integration secrets | Webhook URLs, Slack URLs, signing keys and API keys are stored encrypted with Fernet under `INTEGRATIONS_ENCRYPTION_KEY` (staging/production refuse to start without it; local/test derive one from the unsubscribe secret). The API returns only a hint (`hooks.slack.com/…`, `…abcd`); a webhook's signing secret is shown once at creation or rotation. `cryptography` is now a direct dependency. |
| D112 | Outgoing webhooks | Events are fanned out when the worker processes them (after trigger matching, in its own savepoint so a failure never affects workflows) into `integration_deliveries`; Celery beat sends due ones every 30 s. Body: `{id, type, occurred_at, event_id, data: {...event payload, contact}}`; header `X-LaneReach-Signature: t=<unix>,v1=<hex HMAC-SHA256 of "<t>.<body>">` plus `X-LaneReach-Event` and `X-LaneReach-Delivery` (receivers deduplicate on it). Subscribable: every event type (new: `lead.created`, `meeting.booked`, `meeting.canceled`, `form.submitted`) plus `deal.won` / `deal.lost`. Retries after 1, 5, 30, 120 min; 4xx other than 408/429 is final; five dead deliveries in a row switch the webhook off with the reason. `lead.created` is published when a lead opens its deal. |
| D113 | SSRF protection | Tenant-given URLs must be https, without credentials; the host is resolved and every address must be public (no private, loopback, link-local incl. 169.254.169.254, multicast, reserved, IPv4-mapped IPv6). Checked when saved and again before every call (DNS can change), redirects are not followed, 10 s timeout. `INTEGRATIONS_ALLOW_PRIVATE_URLS` exists for local receivers and is refused in staging/production. Remaining risk: DNS rebinding between the check and the connection; acceptable for v1 (the resolved answer is re-checked on every call). |
| D114 | Slack | Incoming-webhook URLs only (no OAuth app, no Slack account needed on our side); each connection picks alerts: positive replies, new quote requests, deals won, meetings booked, web form requests, link clicks. Block Kit message with a button back to the contact in the console (`CONSOLE_BASE_URL`). Teams stays operator-configured (`TEAMS_WEBHOOK_URLS`). |
| D115 | Meetings | `POST /api/v1/webhooks/meetings/{calendly|cal-com}/{public_key}`: the random key finds the connection's tenant through a SECURITY DEFINER lookup (no other data leaves RLS), then the provider's HMAC signature is checked (Calendly `t=,v1=` with a 5-minute window; Cal.com `X-Cal-Signature-256`). Bookings create the contact when new (office from the connection, tag `meeting-booked`), link the contact's latest open deal, appear on the deal timeline, notify the office owner and publish `meeting.booked` / `meeting.canceled`; reschedules update the same row. New trigger `trigger_meeting_booked`. |
| D116 | Email verification | `contacts.email_status` (unknown, valid, risky, catch_all, invalid) + `email_verified_at`. MillionVerifier when connected, otherwise offline rules (syntax, `.invalid` domains, disposable domains, role mailboxes, `catchall.` test domains). New guardrail `email_verification` (after suppression) blocks cold and follow-up emails to `invalid`; risky and catch-all are only shown. An `unknown` answer never overwrites an earlier verdict. Verify per contact or up to 500 per call. |
| D117 | Website forms | Plain HTML form posts to `POST /api/v1/public/forms/{key}` (also JSON with open CORS). The key is public by design; the endpoint can only create or update a contact (never read), a filled hidden `website` field is accepted and dropped silently, and at most 5 submissions per address per hour and 30 per form per minute are accepted (429 beyond). No CAPTCHA is used or solved. Answers are stored per submission; `form.submitted` carries them (message cut to 300 characters) and trigger `trigger_form_submitted` turns them into `form_<field>` variables. |
| O9 | **Open:** Integration accounts | Needed from Skyfer to switch the integrations on: Slack incoming-webhook URLs per channel, the Calendly (paid plan, API) or Cal.com account, a MillionVerifier API key, and which Zapier/Make flows should receive which events. |
| D118 | Delete rules | Deletes follow the foreign keys: a workflow with running enrollments or used by another workflow, an agent with email history, an office with agents/contacts/history/workflows, a company with contacts and an open or emailing enrollment answer **409 with the alternative** (archive, switch off, pause, move). Everything else deletes with its dependants (cascades already in the schema). Graph references without a foreign key are checked by `automation/workflows/usage.py`. Deletes are audited by the M14 hook. |
| D119 | Contact erase | `DELETE /contacts/{id}` (admins) is the GDPR erase: the contact and its messages, enrollments, leads, deals and meetings cascade; outbox copies, notifications, events and their webhook/Slack deliveries, review drafts, notes and form submissions are deleted explicitly; a permanent do-not-contact suppression of the address is kept so a re-import never emails the person again. The console asks to type the address to confirm. |
| D120 | New nodes | Triggers `trigger_email_opened`, `trigger_email_clicked` (optional URL filter), `trigger_contact_stage_changed` (new event `contact.stage_changed` from the contact API and the stage node). Actions `verify_email` (valid/risky/invalid/unknown, reuses a recent verdict), `send_to_webhook` and `notify_slack` (queued as integration deliveries: signed, retried, SSRF-checked; a stable id per step prevents duplicates on retry), `add_note`, `update_deal`. Logic `wait_until_weekday`, `branch_on_email_status`. 48 nodes in total. |
| D121 | Company sign-in | `GET /auth/config` (public) tells the console how to sign in. With `AUTH_MODE=oidc` it redirects to the identity provider with Authorization Code + PKCE (no client secret in the browser, `state` checked, verifier used once) and exchanges the code at the provider's token endpoint; `/auth/me` now returns the tenant name. The API already validated OIDC tokens per tenant identity provider. |
| D122 | Navigation and look | Contacts and Companies moved to the CRM group; "New" menu in the top bar; the dashboard hero masks its lanes away from the greeting (a port dot covered the text); freight scenes (plane, ship, truck, train, warehouse, customs, parcel, inbox, chart) in empty states and a mode strip on the login; all motion stops under reduced motion. |
| D123 | Bulk and views | Contacts: select rows for add tag, verify emails and erase (typed confirmation); saved filter views per viewer in browser storage (a convenience, never required). |
| O10 | **Open:** Entra ID app | For company sign-in Skyfer's IT registers a single-page app (redirect URI `<console>/auth/callback`), exposes the API scope `api://lanereach/access_as_user` and gives us the client id and tenant; roles come from app roles (`roles` claim). |
| D124 | Every freight line | `ServiceLine` covers air, ocean, road, rail, courier & express, customs brokerage, warehousing and project cargo (plus `any`). The enum is stored as text, so no data migration. Everything keyed by line follows: agents board, pills, colours and icons, pipeline filter, scripts matrix and SharePoint folders (`Scripts/Road/…`), reporting. `domain/logistics.py` holds the modes per line (FCL, LCL, reefer, FTL, LTL, block train, express, bonded…), incoterms 2020, units and charge presets. A mode picks its line when a quote request opens a deal. |
| D125 | Deal cargo | Deals gain incoterm, cargo-ready date, weight (kg), volume (cbm) and pieces/containers (migration 0013), copied from the extracted shipment details when a quote request opens a deal; the mode field is a select of the line's modes. |
| D126 | Quotes | `quotes` + `quote_lines` (RLS, tenant FKs). Numbers `Q-<year>-NNNN` per tenant. Status draft → sent → accepted / rejected / expired. Unit prices are whole cents, quantities three decimals, each line rounds half-up to cents and the total is the sum of rounded lines; the console uses the same rules so the builder shows exactly what is saved. Drafts are editable; a sent quote is fixed (duplicate to re-quote). Validity defaults to 14 days. |
| D127 | Sending a quote | `POST /crm/quotes/{id}/send` renders a branded HTML quotation (plus plain text) and delivers it as `APPROVED_REPLY` through `deliver_email`, so every send guardrail applies (blocked 422, deferred 409). It goes in the latest thread with the contact from that thread's agent and signature, else from an agent of the quote's line. The deal moves to its "Quoted" stage when earlier. `GET …/document` is the printable page (Save as PDF). |
| D128 | Quote outcomes | Acceptance sets the deal value when it had none (the team still moves the deal to Won when booked); every change publishes `quote.status_changed` and the trigger `trigger_quote_status_changed` (status filter) starts follow-up workflows. Hourly Celery beat `lanereach.expire_quotes` expires sent quotes past their validity. Send, create, duplicate and decision accept `Idempotency-Key`. |
| D129 | Services on offer | `GET/PUT /settings/services` (admins): the lines the company sells; empty means all. Menus, filters and the agents board show only those, but a record already on a hidden line keeps showing its line. The CRM summary splits open and won business by line for the dashboard's "Business by service" card. |
| D130 | Actions stay visible | Every delete and restricted action is shown to every role; for a role that may not use it the button looks locked and says who can and how to switch role. Hiding them read as "the feature is missing". |
| D131 | Building on the canvas | Each output is a big coloured dot with its label (green = yes side, red = no side) and a "+" that opens a searchable step picker and connects the new step. Dropping a connection on empty canvas opens the same picker there; every edge has "+" (insert a step, the rest moves down) and "×". A palette click connects to the selected step's first free output, else inserts before the final Stop. Edges can be dragged to a new target; loops, self-links and edges into the trigger are refused with the reason. Undo/redo (50 steps), "Tidy up" layout, new steps never land on another. |
| D132 | New triggers and actions | Triggers: reply received (a person's reply, before classification; auto-replies and bounces excluded), quote request created, meeting canceled, email bounced (hard/soft), quote expiring (1/2/3/5/7 days before validity) and deal idle (3/7/14/30 days in a stage, optional pipeline/stage). The last two come from the hourly job with idempotency keys, so each is published once (per stay in a stage for deals). Actions: create quote draft (charges of the deal's line, prices empty, never sent), update contact field (whitelisted fields, invalid values skipped, not guessed), assign owner round robin (the turn is the count of contacts the step handled: no extra state). 58 nodes in total; four new starters use them. |
| D133 | The Outbox is the sent log | The Outbox is available in every environment (it was dev/staging only): tenant-scoped, read scope. Rows record `status` (`dry_run`, `sent`, `failed`), `provider`, `sent_at` and `error` (migration 0014), so real sending (M19) shows its outcome in the same place. Search (literal text, LIKE wildcards escaped), filters (status, kind, agent, office, contact, enrollment, UTC dates), counts per status, and the conversation (`GET /outbox/{id}/thread`). |
| D134 | Who am I | The login remembers the last company, email and role (browser storage, convenience only), states "You will sign in as <role> at <company>" with what the role can and cannot do (from the real scopes), and says "Welcome back" only to a returning person. The top bar shows a user chip (name, role, company) with switch role, shortcuts and sign out. |
| D135 | Honest answer on "does it just work?" | The engine runs everything built on the canvas for real (triggers, waits, branches, AI, CRM, guardrails). Email however only goes to the dry-run outbox until M19 connects real mailboxes; replies arrive through the provider webhook or the simulator. |
| D136 | Real sending is a transactional outbox | With `SENDING_PROVIDER=smtp` the provider stores the outbox row as `queued` with its final Message-ID (in the agent's domain) inside the step's transaction; the worker `lanereach.deliver_outbox` (every 15 s) sends due rows through the agent's mailbox after commit, rows locked `SKIP LOCKED`. A crash never loses or doubles an email and SMTP never holds the engine's transaction. Temporary failures retry after 1, 5, 15, 60 and 180 minutes, then `failed`; a refused login or recipient fails at once (a refused login also flags the mailbox). |
| D137 | Mailboxes per agent | `sender_mailboxes` (RLS, one per agent): SMTP and IMAP host/port/security, username, password encrypted with the integrations key, never returned or logged; presets for Microsoft 365, Google Workspace and Zoho. Hosts go through the webhook SSRF guard (private addresses refused); unencrypted connections only when `INTEGRATIONS_ALLOW_PRIVATE_URLS` is on (development). "Test" logs in to both, nothing is sent or read. Admins only. |
| D138 | Who sends | Once an office has agents with a verified mailbox, only those agents are chosen (the thread's agent is kept when usable). In SMTP mode the new `mailbox_connected` guardrail defers email from an agent without a verified mailbox for an hour, with the reason on the step; in dry-run mode it allows everything. |
| D139 | Replies over IMAP | `lanereach.poll_mailboxes` (every 60 s) opens each verified mailbox read-only (`BODY.PEEK`, mail stays unread). The first poll only remembers where the inbox ends (old mail is never read; a changed UIDVALIDITY starts again). Replies are matched through `In-Reply-To`/`References` to LaneReach's own Message-IDs, DSN bounces (RFC 3464, 5.x.x hard, 4.x.x soft) through the original Message-ID; the agent's own copies and mail that answers nothing of ours are skipped and not stored. Matched mail goes through the same ingestion and reply pipeline as provider webhooks. |
| D140 | Tests never send email | Every test container uses a fake mail gateway; the real `smtplib`/`imaplib` code is tested against GreenMail (a local test mail server in Docker that keeps mail in memory). |
| O12 | **Open:** Mailbox access | For each agent: the mailbox provider and an app password (Microsoft 365 needs SMTP AUTH enabled per mailbox; Google needs 2-step verification and IMAP on). Confirm the agent mailboxes are dedicated to outreach (LaneReach reads their inbox headers to find replies). Microsoft Graph (no passwords) is not built yet. |
| D141 | Knowledge base | 20 end-user help articles ship with the application (`assistant/articles/*.md`, including "What LaneReach does not do"), plus a workflow steps reference generated from the live node registry, so answers about triggers and steps always match the engine. Articles are split into sections (`slug#n`), shown in a Help center at `/help`. |
| D142 | Retrieval | `help_chunks` (per tenant, RLS) holds each section with a pgvector `vector(1536)` (HNSW, cosine) sent as text (no extra driver). Search always runs PostgreSQL full-text (words OR-ed); with embeddings configured (`EMBEDDINGS_PROVIDER=azure_openai`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`) a vector search runs too and both rankings are merged by reciprocal-rank fusion. Default `none` = full-text only, and the UI says so; `mock` (word hashing) is for tests and never presented as semantic. Only changed sections are re-embedded; embeddings are reused across tenants in a run; built on first use, refreshed daily and by an admin reindex. |
| D143 | Honest answers | `answer_help_question` answers only from the retrieved sections (delimited; the question and conversation are wrapped as untrusted data). With nothing retrieved no model is called ("not found"). Cited IDs must be among the retrieved sections, else the model retries once and then only the closest sections are shown (`sources_only`) instead of an unverified answer. `supported: false` when the help does not say LaneReach does it. Without an AI key answers are quoted passages, labelled as such. AI rate limits apply; every answer is an `ai_runs` row. |
| D144 | Drafting workflows | `draft_workflow` gets the node catalog (types, outputs, config fields) and the tenant's offices, templates and published workflows, returns a graph whose `config_json` must parse, and must pass `validate_workflow_graph` (one retry with the errors). The result is saved as a DRAFT (`workflows:write` needed), with reference warnings and the model's assumptions shown; it is never published, and nobody is enrolled or emailed. |
| O13 | **Open:** Assistant keys | For semantic search and written (not quoted) answers: the Azure OpenAI embeddings deployment (text-embedding-3-small) and an LLM key (Anthropic or Azure OpenAI). Please also confirm the "What LaneReach does not do" article. |
| O11 | **Open:** Service lines and charges | Which lines Skyfer sells (all are on by default), their standard charge names per mode, the default validity and currency, and whether quotes need tax (VAT) lines or a PDF attachment instead of the in-mail quotation. |
| D145 | Rate sheets | Excel (`.xlsx`, openpyxl read-only) and CSV files from the library's `Rates/` folder (any sub-folder; Excel lock files `~$…` ignored) sync with the scripts library and re-import when the etag changes; a file removed from the library removes its sheet (not when the whole listing comes back empty). Uploads are kept separately (`source=upload`) and are not written to SharePoint, so they never overwrite the team's master sheet. A re-import replaces the sheet's rates in one transaction; a file that cannot be opened keeps the previous rates and file, status `failed`. The file is stored (deferred `BYTEA`) so a column-mapping change re-imports without a new upload. |
| D146 | Reading sheets | Every tab; the header row is found in the first 20 rows by column-name synonyms (POL/POD, Rate/All in/O/F/Buy/Sell, Validity…); wide columns per container size (20GP…45HC, `20'`, `40 HQ`) and air weight break (`-45`, `+100kg`) become one rate each; blank lane cells fill down (merged cells); amounts accept symbols, thousands separators and decimal commas; a per-tab mapping (field → header) overrides the guess. Row problems are kept (first 50) with tab and row; status `ok` / `partial` / `failed`. Places are normalised to UN/LOCODE or IATA codes (bracketed codes, `INMAA IN`, a curated alias table of major ports and airports) or to a normalised name. |
| D147 | Matching | Deterministic: same lane (codes, else normalised names), compatible service line/mode/equipment, valid on the cargo-ready date (today when earlier). Rows of one tab, carrier, lane and validity form an option (freight + surcharges); air takes the highest weight break reached by the chargeable weight (max of kg and cbm × 167), LCL quantities are w/m, containers × count. Ranked by exact code match, newest sheet, lowest all-in. The container type comes from the deal's new `equipment` column (set from the extracted request, editable); without one, each size is a separate option, grouped by size and cheapest first. Charges in another currency are shown but not added and are left at 0 on drafts (no exchange rates). |
| D148 | AI in pricing | The AI never prices. `match_lane` only maps an unusual spelling of the deal's lane onto places present in the tenant's sheets (validated against that list; anything else is rejected and retried); it runs on request in the console (`POST /pricing/deals/{id}/match-lane`, AI budget applies) and in the workflow step when nothing matched deterministically. Confidence below 0.6 is not used. |
| D149 | Priced drafts | `POST /pricing/deals/{id}/quotes` and the quote builder's *Fill from rate sheets* create/replace DRAFT lines (optional markup %), validity capped at the rates' validity; the source (sheet ▸ tab ▸ row) goes to the deal timeline, not on the customer's quote. `create_quote_draft` gains `price_from_rate_sheets` (default on) and `markup_percent`; without a matching rate it falls back to the presets with empty prices. Nothing is sent automatically. |
| D150 | Rate websites | LaneReach keeps an https link template per website (`{origin}`, `{destination}`, `{mode}`, `{equipment}`, `{date}`, URL-encoded; no user name or password in the link) and never logs in, stores site passwords or scrapes. People record the rate they saw (*market check*) on the deal; checks are compared with the sheet (same currency and unit only) and feed the lane history with sheet rates. Official API connectors can be added per site once named. |
| O14 | **Open:** Pricing inputs | The 5 rate websites the team uses (and whether any has an API or a partner account), a sample of the rate Excel (to confirm the column layout and whether it holds buy or sell rates), and the SharePoint folder to use as `Rates/`. |
| D151 | Public website | The console serves the product website at `/` to visitors who are not signed in (and at `/welcome` to everyone); signed-in users still land on the dashboard. It is a separate lazy chunk with its own dark style, so the console bundle does not grow. Animations respect reduced motion. |
| D152 | Honest numbers | `GET /site` (public, no tenant data) counts the registered workflow steps, the guardrails in the send chain, the AI prompt tasks and the service lines of the running version; the website and the sign-in page show those numbers, never hand-typed ones. The inbox demo is scripted example data and says so; the comparison describes typical capabilities of tool categories (sales outreach tools, general CRMs, freight software, email + Excel), not named products, so no claim about a competitor can go stale or be wrong. |
| D153 | Contact us | The website form posts to an ordinary web form of the operator's own tenant (`PUBLIC_CONTACT_FORM_KEY`), so an enquiry arrives as a contact with the form's tag (`website-enquiry`), and `form.submitted` can drive workflows and Slack alerts. Honeypot and the existing per-form/per-email limits apply. Without a key the page shows `PUBLIC_CONTACT_EMAIL`. |
| D154 | Try the AI on the website | `POST /site/try-ai` runs the real reply pipeline tasks (rules, `classify_reply`, then `extract_shipment_details` for freight-type replies) on text a visitor pastes, with the same untrusted-content wrapping and schema validation. Nothing is stored: AI runs go to a discarding recorder and no contact, deal or email is created; only the bucket and length are logged. Off unless `PUBLIC_AI_DEMO=true` (each try is a paid LLM call); limited per visitor (the address nginx appends to X-Forwarded-For) and overall per minute. "What happens next" mirrors the default reply policies (a test keeps every bucket covered). |
| D155 | Time-saved calculator | The website's estimate multiplies the visitor's own assumptions (agents, replies, minutes per reply, quotes, minutes per quote, share saved) with 21 working days a month, shows the maths and says it is an estimate. |
| D156 | Lead scores | A transparent points model (base 30, clamped 0-100, every point with a reason) over: the AI's reading of the latest reply (`assess_lead`: intent, urgency, signals, concerns; validated schema, reply wrapped as untrusted), shipment completeness (lane, mode, size, ready date, commodity), reply speed, replies, regular volumes, company won/lost history, quote status, days idle. The AI reading is stored on the deal (`ai_intent`) so the nightly rescore needs no model call; new rate requests are scored in the reply pipeline, and `POST /crm/deals/{id}/score` rereads with AI (AI scope and budget). Scores are advisory: they order lists and never send or move anything. |
| D157 | Weekly digest | `insight_digests` (one per tenant and week, RLS). Statistics are deterministic (Monday-Sunday UTC). `write_digest` gets them as untrusted data and must use only numbers present in them: every number in the headline, summary and highlights is checked; otherwise one retry, then a rule-written digest (`written_by`). Built for the previous week by the daily job when missing (in-app notification) or on demand. |
| D158 | Quiet-deal follow-ups | A deal is quiet after `QUIET_DEAL_DAYS` (5) without a message, stage move or edit. `suggest_follow_up` proposes email / call / wait / close_lost with a draft in the customer's language (no links, no invented prices; the conversation is untrusted). Stored in `follow_up_suggestions` (RLS); at most `FOLLOW_UP_SUGGESTIONS_PER_RUN` (20) per tenant per day, not again within 7 days. "Use" creates a task for the deal owner with the draft; nothing is ever sent from a suggestion. |
| D159 | Daily job | `lanereach.sales_intelligence` (Celery beat, daily): rescore open deals, build last week's digest if missing, suggest follow-ups; one failing tenant does not stop the others. |
| D160 | AI assists | `summarize_thread` (Inbox, *Summarize with AI*: summary, what they want, open questions, next step, sentiment; last 12 messages as untrusted data; not stored) and `draft_quote_note` (quote builder, *Write with AI*: the message above a quote from the quote's own facts; every number must be in the quote and links are refused, else 503 and nothing changes). Both need `ai:run`, count against the AI budget and only fill the screen; sending still goes through the engine. |
| D161 | Website speed | The hero map tested ~8,400 grid points against the world's land shapes in the browser (`geoContains`), freezing the website and sign-in page for ~7.7 s per visit, then drew ~2,600 separate circles. The land dots are now generated once (`npm run world-dots` → `pages/landing/worldDots.ts`) and drawn as one path of round-capped zero-length segments; the landing no longer downloads the 1:110m world data. nginx now serves gzip (precompressed `.gz` files at build time, `gzip_static`): the first visit's scripts and styles went from ~760 KB to ~215 KB. The "AI at work" demo typed each email out (~6 s of empty panel) and paused while the pointer was over it, so clicking a tab froze it empty: the email now arrives whole, the AI reads it at once at a pace a visitor can follow (classified at ~1.2 s, a step every 0.8 s), and hovering only holds a finished example on screen. |
| D162 | Light and dark everywhere | The console already had a sun/moon switch; the website and sign-in page were dark only. They now have the same switch (top bar) and a light palette: their colours are theme tokens in `styles.css` (`.site`/`.auth`, `.light` for the light set; RGB triplets for transparent shades). The website stays dark (its designed look) unless light is picked; the choice is the console's saved `lanereach.theme`, so picking light on any page carries across the application. |
| D163 | AI where it pays, rules where they do | Reading tasks (`classify_reply`, `extract_shipment_details`, `assess_lead`, `summarize_thread`, `match_lane`) can run on a cheaper model (`LLM_MODEL_LIGHT` / `AZURE_OPENAI_DEPLOYMENT_LIGHT`, `TieredLLMProvider`), priced at its own rates; writing keeps the main model. Per company (`tenants.ai_controls`): a monthly AI budget and AI-or-rules for lead reading, quiet-deal follow-ups (rules + the `follow_up_quiet` template) and the digest (numbers only). |
| D164 | The budget lives in the engine | `run_prompted_task` asks the runtime's `AIBudgetGuard` before every model call: month-to-date `ai_runs.estimated_cost` (UTC month) against the budget. When spent it raises `AIBudgetReachedError` (an `LLMError`, 429 `ai-budget-reached` for people): every existing fallback applies (rules classify or a person reads, digests by rules, follow-ups built-in in the daily job). The website demo has no guard (not billed to a tenant). Costs need `LLM_PRICE_*`; without prices a budget never fills (shown on the screen). |
| D165 | Built-in messages | Each answerable reply kind chooses `draft_source` `ai` or `built_in` (`tenant_reply_policies`). Built-in = the company's `reply_<bucket>` template in the contact's, then the office's, then English; else LaneReach's own English text (`ai/builtin_messages.py`); always `Re: <subject>` to stay in the thread. It also stands in for a failed AI draft when the classification is certain (never for an uncertain one, which goes to a person to check the classification), and a stand-in is never auto-sent. Review items record `written_by`. |
| D166 | Agentic data assistant | `POST /assistant/ask-data` (*Your data* tab): the model works in JSON steps (`answer_data_question`, `AgentStep`): pick one of seven read-only tools (`find_deals`, `find_quotes`, `find_contacts`, `find_rates`, `inbox_waiting`, `activity_summary`, `search_help`) with typed arguments, see the result, then pick another or answer; at most three tool calls, then it must answer. Checked in code: cited refs must come from tool results, every number in the answer must appear in the results, the question or today's date (one retry, else the records found are listed, mode `partial`), the same call twice is refused. Tools run in the tenant session (RLS), return at most ten small records with links and never email bodies; nothing is changed. Provider-agnostic (structured JSON, no native tool-use API) so the mock, Claude and Azure OpenAI all run the same loop. |
| D167 | RAG on the company's own history | `draft_reply` v2 gets up to two past answers (`ai/history.py`): earlier inbound emails of the same bucket from the last year, ranked by PostgreSQL full-text search (`simple` configuration: every language, no stemming) over their subject and body with the new email's content words (OR query), each with the first outbound reply in its thread. Lexical, not vector, on purpose: no embeddings cost and no new index to keep in sync; the examples are wrapped as untrusted data and the prompt forbids copying earlier customers' details. Bodies purged by retention are skipped. Review items record `grounded_on`. |
| D168 | AI quality from people's decisions | `GET /insights/ai-quality` measures the AI without any AI call: reply sources (`messages.processing_result.source`: rules/provider, llm, fallback), reply-draft review outcomes (approved = sent as written, edited, rejected, pending) per writer, follow-up suggestions used/dismissed, average lead score of won vs lost deals, failed AI runs. JSON keys are inlined in the SQL (`->> 'key'`): a bound key makes PostgreSQL reject the GROUP BY. |
| D169 | Online quote acceptance | The quote email (HTML button and plain-text link) points to `/q/<token>` on the console host; the token is the tenant and quote IDs under an HMAC (`lanereach:quote-online` key derived from the unsubscribe secret, like tracking links), so the public endpoints (`GET /public/quotes/{token}`, `POST /public/quotes/{token}/decision`) need no login and never look across companies: a tampered token or another company's quote is a 404. The decision goes through `QuoteService.decide_quote` (deal value, events, workflows) with "Online by <name>: <note>", and the deal owner gets a notification. Same answer twice is a no-op (safe retries, so no Idempotency-Key on this public endpoint); the opposite answer afterwards or accepting after the validity date is a 409. Rate limited per visitor (`QUOTE_ONLINE_PER_IP_PER_MINUTE`). |
| O7 | **Open:** Deal currency | Deals keep their own currency and the summary adds values as stored. Confirm whether Skyfer quotes in one currency (USD/CAD) or needs conversion. |
| O4 | **Open:** North America lane wording | USA/Canada contacts are held until an `en-na` script exists (D74). Skyfer to supply the wording. |
| O5 | **Open:** Localised unsubscribe footer | Imported scripts keep the footer the Word file has; an appended footer is English. Confirm each language's Word file carries its own footer. |
| O6 | **Open:** PDF-only uploads | A PDF is kept as the reference copy only; a script needs the Word file to be sendable. Confirm marketing always keeps the .docx. |
| O3 | **Open:** Wrong-office replies | The owner is asked to re-route; automatic re-routing to another office is not built (which office is right needs a person). |
| O2 | **Open:** Who may read `ai_runs` | Proposed `ai:run`; confirm pricing/reviewers need it. |

## 6. Milestone checklists

### M1 Foundation ✅
- [x] pyproject (uv, ruff, mypy strict, pytest), `.python-version`, `uv.lock`
- [x] Dockerfile (multi-stage, non-root, healthcheck) + Compose: postgres/pgvector, redis, one-shot migrate, api, worker, beat
- [x] Makefile targets (`up down logs migrate migrate-local seed run lint format typecheck test check coverage audit eval`)
- [x] Settings (pydantic-settings) + `.env.example` documenting every variable; dev auth refused in production (validator + startup guard)
- [x] `Clock` (`SystemClock`, `ManualClock`)
- [x] Async SQLAlchemy + Alembic; separate owner / app roles (`lanereach-admin bootstrap-db`)
- [x] Tenancy: RLS on every table, `open_tenant_session` (setting re-applied on every transaction), `TenantSession` dependency
- [x] Auth: OIDC (JWKS cache, rotation, throttled refresh), dev tokens, API keys (hash + prefix, scoped, revocable, `last_used_at`), webhook HMAC + replay store, roles → scopes, `Principal`
- [x] RFC 7807 errors, structlog JSON with request ID / tenant ID, email masking + secret redaction
- [x] Idempotency-Key support, cursor pagination, body size limit (413), `X-Request-ID` on every response
- [x] Health `live` / `ready` (DB, Redis, LLM config)
- [x] Jenkinsfile (setup, lint, type-check, test + junit/coverage, pip-audit, image build)
- [x] Tests: 274 passing (unit + integration against real Postgres/Redis), 97 % line coverage

### M2 AI core ✅
- [x] `LLMClient` protocol + `StructuredLLMClient`: strict JSON schema from Pydantic, validation (and task validators) with one corrective retry, exponential backoff on transient errors, circuit breaker per provider
- [x] Providers: Anthropic (official SDK, `output_config` JSON schema + effort, refusal fallback, no sampling params), Azure OpenAI (strict json_schema), Mock (fixtures by task + input hash, responders, deterministic multilingual heuristics for key-less dev)
- [x] Prompt registry: versioned Markdown with front-matter, `${}` rendering, `wrap_untrusted` delimiters that defuse closing tags
- [x] `classify_reply_by_rules`: DSN codes and daemon senders (hard/soft/blocked), unsubscribe EN/FR/ES/PT/ZH, out-of-office with return dates in 5 languages, auto-replies
- [x] Tasks: `classify_reply` (rules first, confidence threshold -> needs_human), `extract_shipment_details` (missing fields recomputed), `draft_reply`, `localize_template` (exact placeholder preservation)
- [x] `ai_runs` (migration 0004, RLS) with tokens, latency, attempts, estimated cost; recorded in its own transaction so failures are kept
- [x] AI endpoints (classify, extract, draft, localize, runs list/get)
- [x] Eval harness: 40 synthetic labelled replies (EN/FR/ES/PT/ZH), `make eval`, JSON + Markdown reports, thresholds, Jenkins stage when a key exists
- [x] Prompt-injection cases (delimiter break-out, hijacked model output rejected by schema, injection routed to humans)
- [x] Tests: 1,003 passing in total

### M3 Workflow definitions ✅
- [x] `BaseNode`, `NodeResult` variants (`Continue`/`Wait`/`Stop`/`Fail`), `@register_node` / `NodeRegistry` (duplicates and malformed nodes fail at import)
- [x] Full v1 catalog: 27 nodes (5 triggers, 9 actions, 6 logic, 4 AI, 3 protection) with strict config models, descriptions, handles, structural flags
- [x] `execute()` for triggers and pure logic nodes (`if_condition`, `branch_on_reply_bucket`, `ab_split` deterministic, `wait_until_date`, `stop`); side-effect nodes land in M4/M5
- [x] Condition DSL: typed field whitelist, operator/type checks, limits, evaluation
- [x] `workflows` / `workflow_versions` (migration 0002, RLS, composite tenant FK, immutability trigger), versioning rules
- [x] `validate_workflow_graph`: every §6.3 rule plus warnings, errors name node IDs (and handles)
- [x] Endpoints: node catalog, condition fields, workflow CRUD, validate (stored + unsaved), publish, pause/resume/archive, versions
- [x] Tests: 568 passing in total (conditions, catalog, logic nodes, validator, API, scopes, isolation, idempotency), 97 % coverage

### M4 Engine ✅
- [x] Domain tables + CRUD APIs: offices, senders, companies/branches, contacts (tags publish events), templates, suppressions, country calendars, leads, messages, tenant sending settings
- [x] Events outbox (`publish_event`, idempotent), `match_event_to_triggers` (tag, contact created + condition, reply buckets, manual), scheduled-date triggers (fire once per version)
- [x] `enroll_contact_in_workflow` (partial unique index: one open enrollment per workflow and contact)
- [x] `advance_enrollment`: `FOR UPDATE SKIP LOCKED`, savepoint per node, step log with idempotency keys, max steps per tick, retry backoff 1/5/30 min then failed, company pauses, archived/paused workflows
- [x] Dispatcher on Celery beat + after-commit enqueueing; `automation_tenants_with_work()` SECURITY DEFINER lookup; inline queue for tests
- [x] Guardrail chain (kill switch, suppression, freeze, frequency cap, rotation, business days, window, mailbox capacity) with every decision stored on the step
- [x] `execute()` for every trigger, action, logic and protection node
- [x] `DryRunSendingProvider` (outbox, idempotent per step, List-Unsubscribe headers) + signed one-click unsubscribe endpoint; recording, Teams and fake notifiers
- [x] Enrollment/step/event endpoints (+ run-now, wake, dispatch testing aids), test-run endpoint (real engine, rolled back), reference checks on publish
- [x] Tests: 855 passing, 91 % coverage; live Compose check through Redis + Celery worker + beat

### M5 Reply pipeline ✅
- [x] `POST /webhooks/sending/{provider}` (HMAC, tenant in the signed body, per-event replay protection), inbound matching and storage, after-commit enqueue + dispatcher safety net
- [x] `process_inbound_reply` (Celery task, idempotent per message): provider hints → rules → LLM (fallback to human review), extraction, `reply.classified` event
- [x] `decide_reply_policy` (deterministic table, confidence threshold, needs_human) + tenant overrides (`GET/PUT /ai/reply-policies`)
- [x] Protection actions: stop outreach (reply workflows exempt), DNC, suppress, freeze company, alert owner, flag mailbox, soft-bounce retry/limit, rest, re-enroll later, reschedule after absence
- [x] Handovers: pricing lead + notifications, office owner, re-route request, referred contact; contact stage updates
- [x] Review queue (`review_items`, migration 0005, RLS): list/get/approve/edit-and-approve/reject; approval sends in the original thread through the guardrails (422 blocked, 409 deferred)
- [x] AI nodes wired into the engine (classify, extract, draft, localize) with reuse of pipeline results; `reply_in_thread` waits for review
- [x] `POST /ai/replies/{id}/process`, `POST /test-tools/sending-events` (dev/staging) to simulate replies, bounces and complaints
- [x] Tests: 1,108 passing in total (policy table, every protective path, idempotency, outage, auto-send threading, webhook signature/replay/tenant, review decisions, scopes and isolation, AI nodes end to end)

### M6 Hardening ✅
- [x] 12 scenario tests (§10) in `tests/scenarios/`
- [x] Redis rate limits on AI endpoints (D56); retention purge task + CLI (D57, migration 0006)
- [x] `make seed`: both tenants, 5+1 offices, 7 senders, 52 + 8 companies in 12 countries with branches and contacts, templates EN/FR/ES, `office-intro-sequence` and `rate-query-handover` published (D58)
- [x] `.http` files for every endpoint (01–08); README rewritten
- [x] Coverage: `ai/` 91.6 %, `automation/engine/` 92.4 %, `automation/guardrails/` 97.9 % (92 % overall)
- [x] Self-review (below) and its fixes; tests: 1,161 passing

---

### M7 React frontend ✅
- [x] `web/`: Vite + React + TypeScript, generated API types, session, layout, 15 screens (see README)
- [x] Workflow canvas: palette, drag/drop and click-to-add, handles per node (dynamic for bucket branches and A/B splits), schema-driven config forms, condition builder, live validation with node highlighting, save/publish/pause/resume/archive, versions (restore), test-run with steps, guardrails, emails and notifications
- [x] Contacts (tag, enroll, simulate reply/bounce/complaint, email history with pipeline results), companies (branches, freeze), offices and senders, templates (+ AI localization), review queue (edit, approve & send, reject), leads, enrollments (steps, controls), events, outbox, notifications, suppressions, AI playground, settings (kill switch, window, reply policy, API keys, calendars)
- [x] Docker `web` service (nginx, SPA routing, `/api` proxy, security headers, healthcheck), Makefile targets, Jenkins frontend stage
- [x] 14 unit tests (graph conversion, handles, layout, formatting, error text); verified live against the Compose stack

### M8 Scripts, countries & agents ✅
- [x] Migration 0007: `countries`, `email_assets`, `document_library_connections` (RLS); template variants/status/source/HTML; sender line/title/phone/signature; tenant branding; contact network; outbox HTML
- [x] Document store: `LocalDocumentStore` + `SharePointDocumentStore` (Graph, token cache, site/drive lookup, children walk, download, upload ≤ 4 MB); `/settings/library` (+ test connection); secrets only from `SHAREPOINT_CLIENT_SECRETS`
- [x] Word import (mammoth + nh3), Word generation for blank scripts and AI drafts (python-docx), placeholder mapping, warnings
- [x] Script library service + `/scripts` API: matrix (script × language), detail, exact preview (country + agent), upload (multipart ≤ 10 MB), blank, sync now, translate missing, approve AI draft, banners, assets; beat sync task
- [x] Countries table + `/countries` API, language routing with a strict hold for USA/Canada, `{to_country}` in the script's language
- [x] Agents: service lines, generated/custom/image signatures, `/senders/import` CSV, signature preview; rotation within air/ocean, same agent for the thread
- [x] HTML email end to end (`SafeHtml`, signed public image links, outbox `html_body`), `contact.network` field + condition
- [x] Seed: 53 countries, branding, agent lines, two sample English scripts in the local library; top-up of existing tenants (D80)
- [x] Tests: 1,312 passing (importer, paths, languages, signatures, Graph via MockTransport, sync create/update/delete/guard, endpoints with auth/scope/cross-tenant, scenario upload → sync → Turkish contact gets Turkish HTML with banner and signature; US contact held)

### M9 Console redesign ✅
- [x] New design system (tokens light/dark, icons, fonts, flags polyfill), app shell with icon sidebar, search button, theme toggle, **Ctrl+K command palette**
- [x] Login hero with animated lanes and multilingual typing line; dashboard with KPIs, sparklines, trend chart, reply donut, capacity meters, work tiles, script coverage, busiest agents (`GET /dashboard/stats`)
- [x] Scripts: matrix with cell states, upload dropzone, new from text, sync now, translate missing, approve, sandboxed exact preview by country and agent, banners/brand images
- [x] Agents: air/ocean boards, signature preview, edit (line, title, phone, cap, signature mode), CSV import, add agent
- [x] Countries & languages editor; Settings → Branding (live signature preview) and Scripts library (SharePoint connection, test, last sync); sending-window grid
- [x] Outbox shows HTML emails as received (plus text and headers); canvas and palette icons per node type; lazy-loaded screens
- [x] Tests: 32 console unit tests (chart maths, flags, cell states, fuzzy search, preview document, window grid, node icons); backend 1,323 passing (dashboard stats incl. isolation and periods)

### M10 Insights & polish ✅
- [x] Backend: `GET /dashboard/insights` (funnel, per-country results, office-time reply heatmap, best slot) and `GET /dashboard/activity`; `branding.lane_origin_countries`
- [x] Lane map on the dashboard (lanes to every destination, traffic along busy lanes, hover details, top lanes), draggable 3D globe on the login page
- [x] Funnel with conversions, reply heatmap, live activity feed (new items slide in), launch checklist with progress ring
- [x] Notification bell with unread popover, keyboard shortcuts (`?`, `g` + letter), loading bar, cursor spotlight on cards, scroll reveal, hero aurora, confetti for won leads, 404 and error screens
- [x] Phone layout: slide-in menu, compact top bar, no horizontal overflow
- [x] Tests: 41 console unit tests; backend 1,329 passing (insights with won leads, office-time heatmap, isolation; activity order, limits, auth)

### M11 Signatures, import/export, workflow nodes ✅
- [x] Office signature picture (upload/remove) and plain-template HTML with it; agent signature picture; agent mobile/WhatsApp; generated signature with QR, extension, WhatsApp, fax, badges and promo banner; brand image upload
- [x] Contact CSV import with tag; lead CSV export; shared CSV helper (agents use it too)
- [x] Nodes: lead status changed, custom event, switch on field, enroll in workflow; reference check for workflow targets; six new starters (air/ocean script sequences, route by region, won → customer, lost → try again, website quote request)
- [x] Console: country / time zone / language dropdowns (offices, contacts), office signature text or picture, agent picture upload, branding images, contact import, lead export, planes fly nose-first, canvas minimap/controls themed, list inputs keep commas while typing
- [x] Tests: backend 1,360 passing; 44 console unit tests

### M12 CRM ✅
- [x] Migration 0009: `pipelines`, `pipeline_stages`, `deals`, `deal_events`, `tasks`, `notes` (RLS); `leads` gets its `(tenant_id, id)` key
- [x] Default pipeline, deal per lead (reply pipeline and `create_pricing_lead`), backfill in the seed top-up, won/lost mirrored onto leads, `deal.stage_changed` events
- [x] `/crm` API: pipelines CRUD, deals list/create/update/move/timeline, tasks, notes, summary; scope `crm:write`
- [x] Nodes: `trigger_deal_stage_changed`, `create_deal`, `move_deal_stage`, `create_task`; starters use them
- [x] Console: Pipeline board (drag and drop, optimistic moves, totals, filters, new deal, stage editor), deal drawer (stepper, timeline, tasks, notes, details), Tasks page, CRM card on contacts, dashboard tiles
- [x] Tests: 1,391 backend tests (1,389 passed in the full run; 2 hit database connection timeouts through Docker Desktop and pass on rerun, see D99); 49 console unit tests

### M13 Unified inbox ✅
- [x] Migration 0010: `conversation_states` (RLS)
- [x] `/inbox` API: conversations (folders, filters, cursor pages), counts, labels, a conversation, update (read, done, snooze, assign, labels), reply in the thread
- [x] Console: 3-pane Inbox (folders and filters, conversation list with bucket, AI draft and label chips, thread as bubbles with quoted text folded, composer with AI suggestion and Ctrl+Enter), unread count in the menu, keys j/k/e/s/u/r, `g i`
- [x] Tests: 1,410 backend tests passing (8 new inbox integration tests (folders, reply in the thread with signature and escaping, idempotent resend, done/snooze coming back, labels/assignee/filters, cursor, suppressed contact blocked, 401/403/404 cross-tenant); 57 console unit tests

### M14 Campaigns, tracking, deliverability, audit ✅
- [x] Migration 0011: `messages` tracking columns, `email_events`, `audit_events` (RLS), `tenants.track_opens/track_clicks`, `workflows.schedule`
- [x] Tracking: signed pixel and link tokens, rewriting at send, public `/t/o` and `/t/c`, bot filtering, counters, `email.opened` / `email.clicked` events, early wake
- [x] Node `wait_for_engagement`; guardrail `campaign_schedule`; `PUT /automation/workflows/{id}/schedule`; `GET /automation/workflows/{id}/stats`
- [x] `GET /senders/health`; dashboard open/click rates; `GET /audit-events` with central recording; retention deletes old open/click events and two-year-old audit rows
- [x] Console: Campaigns list with results, Results drawer (funnel, steps, links, schedule editor), step bubbles on the canvas, tracking switches and Audit log in Settings, health pills and panel on Agents, Mailbox health card and open/click rates on the dashboard, opened/clicked on sent emails in the inbox
- [x] Tests: 1,467 backend tests passing (tokens, rewriting, bots, schedule, health scoring, click → clicked branch, deadline → no_engagement, open → opened, tracking off, forged tokens, schedule defer/stop, audit log incl. 401/403/cross-tenant, mailbox health, stats 404 cross-tenant); 60 console unit tests

### M15 Integrations & forms ✅
- [x] Migration 0012: `integration_connections`, `webhook_subscriptions`, `integration_deliveries`, `meetings`, `web_forms`, `form_submissions` (RLS), `contacts.email_status/email_verified_at`, owner lookups for public keys
- [x] Encrypted secrets; signed outgoing webhooks with fan-out, retries, auto switch-off, test ping, secret rotation, delivery log and retry; SSRF guard
- [x] Slack alerts; Calendly / Cal.com signed webhooks → meetings on contacts and deals, office-owner notice, events, trigger `trigger_meeting_booked`
- [x] Email verification (mock / MillionVerifier), per contact and in bulk, guardrail `email_verification`
- [x] Website forms: builder API, embed snippet, public endpoint with honeypot and rate limits, submissions, trigger `trigger_form_submitted`; event `lead.created`
- [x] Console: Integrations page (catalogue with status, webhook, Slack, Calendly, Cal.com and MillionVerifier panels, operator integrations), Website forms page (builder, embed HTML, submissions), Verify email on contacts
- [x] Tests: 1,562 backend tests passing (secrets, SSRF cases, signatures, Calendly/Cal.com parsing, verifier rules, Slack cards; webhook flow with signature check, unsafe URLs, retries and switch-off, Slack alert, Calendly booking → meeting, deal timeline and workflow, Cal.com new contact, forms incl. honeypot/429/redirect/JSON, verification blocking a send, admin-only and cross-tenant 404); 60 console unit tests

### M16 Product polish ✅
- [x] Delete endpoints with safety rules: workflows, agents, offices, contacts (erase), companies, deals, quote requests, enrollments; console buttons with a confirmation dialog on every screen
- [x] CRM navigation (Contacts, Companies), "New" menu, bulk actions and saved views on Contacts
- [x] Dashboard hero fix, freight illustrations and empty states, new login with company sign-in (OIDC + PKCE) and role tiles
- [x] 10 new nodes and triggers (see D120) with canvas icons and webhook/Slack pickers
- [x] Tests: 1,598 backend tests passing (delete rules incl. 403/404, contact erase, new nodes in a workflow, stage/click triggers, auth config); 68 console unit tests (PKCE RFC vector, saved views)

### M17 Whole-logistics coverage ✅
- [x] Eight service lines with modes, incoterms, units and charge presets; every line-keyed screen, filter, folder and report follows
- [x] Deal cargo fields (incoterm, cargo ready, weight, volume, pieces) filled from quote requests
- [x] Quotes: builder in the deal drawer (presets, live totals, customer preview), send in the thread, accept/reject, duplicate, printable page, expiry, `trigger_quote_status_changed`; Quotes page with status tabs, expiring soon and win rate
- [x] Settings → Services; dashboard "Business by service"
- [x] Tests: 1,626 backend tests passing (money rounding, mode → line, catalog, build → send in the thread → accept → event, expiry, rules/403/404 cross-tenant, services setting, summary by line, idempotent decision); 73 console unit tests (quote maths as stored)

### M18 Easier workflows, useful nodes, Outbox, login ✅
- [x] Canvas: big labelled outputs with "+", step picker, drop-to-connect, insert on edges, reconnect, loop/self/trigger checks, undo/redo, tidy up, change trigger from the node
- [x] 6 triggers and 3 actions (D132) with icons, matcher filters and 4 starters
- [x] Outbox: status, provider, search, filters, counts, conversation; available in production
- [x] Login: remembered last sign-in, role summary, user chip with switch role
- [x] Tests: backend tests for the new nodes/events (drafted quote, contact fields, round robin, expiring and idle once, reply/bounce events) and the sent log (search, wildcard escaping, filters, counts, thread, 401/404 cross-tenant); console tests for the canvas helpers and the remembered sign-in

### M20 LaneReach assistant ✅
- [x] Help articles and the generated steps reference; Help center with search and safe Markdown
- [x] `help_chunks` with pgvector (HNSW) and full-text; hybrid search with reciprocal-rank fusion; Azure OpenAI or mock embeddings; index on first use, daily and on demand
- [x] Answers only from retrieved sections with checked citations, honest "not in the help" and fallbacks; question/conversation delimited as untrusted
- [x] Draft workflows from a description: validated, saved as drafts only
- [x] Console: Ask LaneReach drawer (top bar, Ctrl+J, palette), suggestions, sources, mode labels, Draft a workflow tab
- [x] Tests: chunking, the steps reference covers every node, embeddings, offline answer/draft rules, index sync and re-embedding, hybrid vs full-text, unsupported and not-found answers without a model call, delimiter escape, invented citations, follow-ups, draft saved as draft and valid, 403/404, invalid drafts refused, help endpoints, rate limit; console markdown safety

### M19 Real email: SMTP + IMAP ✅
- [x] Agent mailboxes (presets, encrypted password, SSRF-checked hosts, test login, disconnect) with API and a Mailbox panel in the agent drawer; status on agent cards
- [x] `SmtpSendingProvider` (queued outbox), delivery worker with retries and failure reasons, `queued` in the Outbox
- [x] `mailbox_connected` guardrail and connected agents chosen first
- [x] IMAP poller for replies and bounces into the reply pipeline
- [x] Tests: MIME building (threading, unsubscribe, header injection) and parsing (replies, HTML, DSN hard/soft); API rules (password never returned, 403/404, unsafe hosts); queue → deliver once; retries then failed; refused login; guardrail defer; poller baseline, matching, skipping; real SMTP/IMAP against GreenMail

## Log

### 2026-11-03 — M26–M29 AI assistant, grounded drafts, AI quality, online quotes

- **M26** *Ask LaneReach → Your data*: an agentic assistant over the company's own data with seven read-only tools, checked citations and numbers (D166).
- **M27** reply drafts grounded in the office's own past answers to similar emails, by full-text retrieval over the company's history (D167).
- **M28** an AI quality card on *AI insights*, measured from people's decisions (D168).
- **M29** customers accept or decline quotes online from the quote email (D169).

Note: two full runs each had one setup error in a different integration test (outbox log, help index) that passed when rerun alone: likely contention in the shared test containers, not a code change; worth watching.

### 2026-11-03 — M25 AI cost controls

Not every step needs a model. Built-in messages answer chosen kinds of reply (no AI call); lead reading, quiet-deal follow-ups and the digest can each run on rules; a monthly AI budget per company is enforced before every model call and everything falls back to built-in messages and rules when it is spent; reading tasks can use a cheaper model. *Settings → AI usage* shows the month's cost by task; *Reply policy* gained "Written by". Migration 0019 (`tenants.ai_controls`, `tenant_reply_policies.draft_source`, `follow_up_suggestions.written_by`). Help article `ai-usage`. Decisions D163–D165.

### 2026-11-02 — M24 Sales intelligence

**Built:** lead scores (pipeline card badge, deal **AI** tab with the score ring, every reason and its points, the AI's reading of the latest reply, *Rescore with AI*); the weekly AI digest (**AI insights** page: headline and summary written by AI from the week's numbers, number tiles, highlight cards, lanes heating up, win/loss by lane, waiting quotes by office, week picker, *Write this week's digest*); follow-up suggestions for quiet deals (AI insights and the deal's AI tab: suggested action, reason, draft, *Make it a task*, *Copy draft*, *Dismiss*); the daily job; three AI tasks (`assess_lead`, `write_digest`, `suggest_follow_up`) with prompts, schemas and offline heuristics; help article *AI insights, lead scores and follow-ups*. Migration 0018.

Verified live on Compose (mock model): the daily job rescored the open deals, wrote last week's digest in both workspaces and suggested follow-ups for two deals quiet for 32 days; *Rescore with AI* read 60 % intent on a reply and moved the score from 29 to 39 with its reasons.

Website v2 (merged in the same milestone): an **AI co-pilot** section showing the three new features with example data (lead score ring and reasons, digest with lane sparklines, a quiet deal getting a suggestion; auto-advancing tabs, pause on hover/focus), an **AI command center** orbit of the AI skills (live ones from `GET /site`), a scroll-linked **journey of a reply**, an **integrations** strip (only real ones, no logos), an honest **FAQ**, a cursor glow and tilt; *Try it with your own email* now has examples in English, Spanish, Turkish, Arabic, German, Portuguese, Chinese, Hindi and French and runs on one click; the top scroll line was removed. Fit-checked at 375, 768, 1024 and 1440 px: no horizontal scroll, no clipped boxes (the comparison table scrolls inside its own box on phones).

Then, on request: an alignment audit of the website and every console page at 375, 768, 1024 and 1440 px (page overflow, controls outside their card, overlapping controls): the review queue's detail panel collapsed to 2 px on phones (fixed side column), so fixed two-column pages (review queue, agents, scripts) now stack on narrow screens; on the website the how-it-works connector ran across the cards (now between them), the last feature tile left half a row empty (now full width) and the two AI cards had unequal heights. Two more AI assists: conversation summaries in the Inbox and AI cover notes in the quote builder (D160). Website latency: a 7.7 s main-thread freeze from the hero map and uncompressed assets, both fixed (D161).

**Decisions:** D156–D161 in §5.

### 2026-11-02 — M23 Public website

**Built:** a public website at `/` (visitors) and `/welcome`: hero with rotating service lines and a dot-matrix world map drawn from the world atlas with animated example lanes; a lane ticker; a scripted "AI at work" inbox (a reply types itself, a scan line reads it, the classification, extracted fields, steps and outcome appear, the words used light up in the email; three examples); a feature bento with small live animations (greetings per language, flowing workflow canvas, shuffling inbox, moving deal, rate-sheet row → quote, send checks ring, assistant chat); "what the AI does / never does" from the live AI task list; how it works; an interactive comparison table with an "only LaneReach" filter; trust band; contact form (validation, honeypot, success state) and sign in. `GET /site` serves the live numbers; the sign-in page uses them too and links to the website.

Verified live on Compose at 1024 px and 375 px (no horizontal scroll): a contact enquiry arrived as a contact tagged `website-enquiry` with its answers in the Skyfer workspace (created form *LaneReach website: contact us*, key in the local `.env`); the test enquiry was deleted afterwards.

**Found while building:** gradient-clipped text with a blur animation paints blank in Chromium (the bento greeting): that animation no longer blurs and the greeting uses a solid colour with a glow; the mobile menu needed an opaque background.

Then, on request: a redesigned sign-in page (one dark scene with the lane map, a live-example AI feed of what LaneReach does with replies, live product numbers and a glass sign-in card; the existing sign-in widgets restyle through the card's theme tokens), **Try it with your own email** (the real reply AI on pasted text, see D154; verified live with the mock model: rate request 90 %, Chennai INMAA, 2 × 40HC, FOB and the default next steps), a time-saved calculator, and `/welcome#section` links (Contact us from the sign-in page).

**Decisions:** D151–D155 in §5.

### 2026-10-01 — M22 Pricing

**Built:** rate sheets (Excel/CSV) synced from the library's `Rates/` folder or uploaded, with header detection, wide container and weight-break columns, row problems and a column-mapping editor; rate search with validity; rate websites with lane links (never logins) and market checks recorded on deals; a deal **Pricing** tab (matching options with reasons, links, record rate, comparison, draft quote from rates); *Fill from rate sheets* in the quote builder; lane history (sheet and market rates); `create_quote_draft` priced from the sheets; the AI task `match_lane` (only places present in the sheets); help article *Pricing and rate sheets* and the limitations article updated. Migration 0017 (four tables with RLS).

Verified live on Compose with a throwaway sheet in the local library (`Rates/Ocean/…xlsx`, two tabs): *Sync library now* imported 10 rates and reported the "on request" row; the column editor showed the detected columns and highlighted the header row; a throwaway deal Chennai → Jebel Ali 2 × 40HC showed two 40HC options (cheapest first, the EUR terminal charge flagged, not added); a website link opened with the lane filled in (credentials in a link were refused); *Draft quote from these rates* with 5 % markup gave 2 × 1,596.00 USD valid 14 days, *Fill from rate sheets* re-priced it at the sheet price, both written to the deal timeline; a recorded market rate showed "−2.6 % vs sheet" and appeared in the lane history. Everything was deleted afterwards (sheet, website, deal, quote).

**Found while building:** a deal typed by hand had no container type, so a 20GP rate was ranked against 40HC ones: deals now have an **Equipment** field (filled from the AI-extracted request, editable in Details; `40HQ` reads as `40HC`), and without equipment each size is its own option. Detected columns are shown with the sheet's own header text. Escape closed every open drawer at once (a quote opened from a deal closed the deal too); it now closes only the top one.

**Decisions:** D145–D150, open question O14 in §5.

### 2026-10-01 — M21 Login redesign and layout sweep

**Built:** a new login (animated gradient mesh, an illustrative product preview with example data, factual capability chips, an elevated sign-in card that retries automatically while the API restarts); offices as responsive cards with mailbox rows (name and email truncate, compact cap, state, actions inside the card); the top bar stays on one line on laptops; flush cards scroll their tables inside the card; text in `.row.nowrap` wraps again (it pushed the Settings switches out of their cards). An overflow audit (every route, 1600 and 1280 px, buttons/inputs/badges outside their card or a horizontally scrolling page) is clean; the login has no horizontal scroll at 375 px.

**Found while building:** the login showed "dev tenant list unavailable" after an API rebuild because nginx kept the old API address (fixed before M21: resolve per request).

### 2026-09-30 — M20 LaneReach assistant

**Built:** everything in the M20 checklist. Verified live on Compose (mock LLM, no embeddings key, so answers are quoted passages and search is full-text, both labelled): "How do I send a quote from a deal?" answered from *Quotes › Send* with sources; "Can LaneReach send WhatsApp messages?" answered from *What LaneReach does not do*; a workflow drafted from a sentence (tag → intro → wait 4 days → follow-up → call task → stop) opened on the canvas as a valid, unpublished draft, then deleted.

**Found while building:** `wrap_untrusted` only defuses its own tag, so the prompt delimiters are now the wrapper's tags; an invented citation used to surface as a 502 with the model's output, now the closest sections are shown instead; the offline draft picked the first template alphabetically (now intro for "intro", follow-up for "follow-up").

**Decisions:** D141–D144, open question O13 in §5.

### 2026-09-30 — M19 Real email: SMTP + IMAP

**Built:** everything in the M19 checklist. Verified live on Compose with GreenMail on the Compose network and a temporary API container in SMTP mode (the normal API stayed in dry-run): Sanne's mailbox connected and tested; an inbox reply to Ahmed was queued with a Message-ID in the agent's domain, delivered by the regular worker on the first attempt (multipart text + HTML, threading and unsubscribe headers, read back from GreenMail over IMAP); Ahmed's answer was read over IMAP a minute later, landed in the same conversation, was classified as a rate query and got an AI draft. The test mailbox was disconnected and the temporary containers stopped afterwards.

**Found while building:** the guardrail order test needed the new guardrail; two reply tests counted events before `reply.received` existed (M18).

**Decisions:** D136–D140, open question O12 in §5.

### 2026-09-30 — M18 Easier workflows, useful nodes, Outbox, login

**Built:** everything in the M18 checklist. Verified live on Compose: a throwaway workflow built only with the edge "+", the output "+", a palette click and a connection dropped on empty canvas; undo/redo; tidy up; the Outbox tabs, filters and conversation; the login summary for Pricing and Admin, the toast and the user menu. The four new starters were checked against `validate-graph` (all valid); the throwaway workflow was deleted.

**Found while building:** a palette click with every output used added an unconnected step (now inserted before the final Stop); new steps could land on another step (free-spot search); edge labels repeated the output labels (edges now only carry the colour); the outbox kind filter listed kinds that never occur.

**Decisions:** D130–D135.

### 2026-09-30 — Fixes after user testing

**Reported:** the Scripts page crashed in the built console ("Cannot access 'D' before initialization"); the menu said Campaigns while the URL and editor say workflow; no delete button on workflows and agents.

**Fixed:** the Scripts page read the script matrix before declaring it (M17 edit; the dev server hid it, the minified build did not). "Campaigns" is now **Workflows** everywhere. Delete buttons were there but hidden from roles without the right (the tester was signed in as Office owner; the login remembers the last role) and the list icon only showed on hover: every delete is now always visible, and for a role that cannot use it the button looks locked and says who can and how to switch role; the agent drawer also says why its fields are read-only. Every screen of the built console was opened as Office owner to check for crashes.

**Decision D130:** actions stay visible for every role and explain themselves; hiding them read as a missing feature.

### 2026-09-30 — M17 Whole-logistics coverage

**Built:** everything in the M17 checklist. Verified live on Compose: a throwaway road deal (FTL, DAP, 18.5 t) → a quote from road presets → preview → sent (landed in the dry-run outbox as a reply to the contact) → deal moved to "Quoted" → accepted → deal value 3,336.12 USD; the Quotes page, the dashboard card and the Services settings (toggled and undone, not saved).

**Found while building:** the builder computed amounts from the typed price while the server stores cents (185.555 showed 371.11, saved 371.12) — the builder now rounds like the server; two older tests used "rail" as an unknown line; the quote decision had no idempotency guard; the new-deal contact list looked selected before a click (hint added).

**Decisions:** D124–D129, open question O11 in §5.

### 2026-09-30 — M16 Product polish

**Built:** everything in the M16 checklist, after the user tried the console as an end user. Verified live: the hero greeting clear of the lanes, Contacts/Companies under CRM, the new login, deleting a throwaway draft campaign from the list, the 409 explanation when deleting an agent who has sent email.

**Found while building:** role tiles had no accessible name (labelled); the RFC 7636 test vector was mistyped in the first test.

**Decisions:** D118–D123, open question O10 in §5.

### 2026-09-30 — M15 Integrations & forms

**Built:** everything in the M15 checklist. Verified live on Compose: migration 0012, the Integrations page, a website form created in the console and submitted like a website (thank-you page, submission listed, honeypot post dropped), and the webhook form refusing the cloud metadata address.

**Found while building:** the Integrations schema name `WebhookResponse` clashed with the provider-webhook one (renamed `WebhookSubscriptionResponse`); the reply pipeline now publishes `lead.created` next to `reply.classified`.

**Decisions:** D111–D117, open question O9 in §5.

### 2026-09-30 — M14 Campaigns, tracking, deliverability, audit

**Built:** everything in the M14 checklist. Verified live on Compose: migration 0011, Campaigns list with results, step bubbles on the canvas, the Results drawer with real numbers, saving and clearing a schedule (both in the audit log), tracking switches in Settings, health pills on every agent, the public pixel returning the GIF and a forged link returning 404.

**Found while building:** the first audit filter missed the `validate-graph` route (now excluded); an empty PUT body now clears a schedule like `null`; the tracking switch wrapped under its label on narrow screens.

**Decisions:** D104–D110, open question O8 in §5.

### 2026-09-30 — M13 Unified inbox

**Built:** everything in the M13 checklist. Verified live on Compose: migration 0010, the demo conversation in the inbox, reading it cleared the unread count, a reply sent from the composer landed in the thread with Aisha Rahman's signature, "Suggest with AI" filled the composer, Done moved it to the Done folder.

**Found while building:** the pane's browser returns a promise from `scrollIntoView`, which React took as an effect cleanup and crashed the thread view; effects now use block bodies.

**Decisions:** D101–D103 in §5.

### 2026-09-30 — M12 CRM

**Built:** everything in the M12 checklist, after reviewing warmbly.com with the user (D95). Verified live: migration 0009, seed top-up opened a deal for the existing lead (Chennai → Rotterdam), board totals, a drag to "Quoted" (fired as real drag events; the pane's synthetic mouse drag does not start native drag and drop), deal timeline with the email thread.

**Found while building:** a new pipeline's name check matched the pipeline itself (its id is only set on flush); creating a custom pipeline before the default existed would have made it the default; the top bar covered drawers (portal, D100); the money test depended on the machine locale (en-IN shows lakh).

**Decisions:** D95–D100, open question O7 in §5.

### 2026-09-29 — M11 Signatures, import/export, workflow nodes

**Built:** everything in the M11 checklist. Verified live: migration 0008, office form with the dropdowns and the picture tab, the new signature layout for an agent with an extension, WhatsApp and fax, and a "Route by region" workflow from the starter (validation clean; the draft was archived afterwards).

**Found while building:** the plane glyph had its tail at the front (planes flew backwards); comma-separated inputs on the canvas dropped the comma while typing; the canvas minimap and zoom buttons stayed white in dark mode; office/agent buttons were shown to roles the API refuses.

**Decisions:** D89–D94 in §5.

### 2026-09-29 — M10 Insights & polish

**Built:** everything in the M10 checklist. Verified in the browser against the Compose stack: globe on
the login page, lane map from Canada / United States with the UAE lane active and 50 planned lanes, funnel
and heatmap (best slot Tuesday 09:00 in Dubai time), live feed and launch checklist (3 of 7 done), bell
popover, 404 page, phone layout with the slide-in menu.

**Found while building:** the lane origin was about to be hardcoded (now a branding setting, D86); page
content painted over the bell popover (the top bar is its own layer now); on phones one card with long
text widened the whole page (D88); map code had grown the main bundle past 500 kB (now separate chunks).

**Decisions:** D85–D88 in §5.

### 2026-09-29 — M9 Console redesign

**Built:** everything in the M9 checklist. Verified in the browser against the Compose stack: login hero,
dashboard with live numbers, Scripts matrix (USA/Canada column shown as held), a Turkish Word script
uploaded through nginx became a live cell and its preview showed "Türkiye'ye", the Turkish footer and the
agent signature; agents boards and signature drawer; countries editor; branding; light/dark via Ctrl+K;
workflow canvas with node icons.

**Found while building:** the page entrance animation used `transform`, which made the page the containing
block of drawers (they opened inside the scroll area): now opacity only. nginx limited uploads to 2 MB
(D84). Windows shows flag emojis as letters: a flags-only font is bundled. Country tables overflowed their
cards at three columns; the tab strip showed a stray scrollbar.

**Decisions:** D81–D84 in §5.

### 2026-09-29 — M8 Scripts, countries & agents

**Built:** everything in the M8 checklist. Verified live on Compose: migration 0007; the seed topped up the
existing Skyfer tenant (53 countries, branding, 6 agent lines, 2 sample scripts written to the library
volume as Word files); the matrix lists 18 languages; previews for Turkey, France and the USA render the
branded HTML (signature table, WCA line, unsubscribe link).

**Found while building:** multipart uploads broke the Idempotency-Key fingerprint (the parsed form is
fingerprinted now); Graph delta has no folder paths (children walk instead); Word splits `{placeholders}`
across runs (tags inside braces are kept); the English fallback carried the Turkish destination phrase (D75).

**Decisions:** D70–D80, open questions O4–O6 in §5.

**Skyfer to provide:** an Azure AD app with Graph `Sites.Selected` granted on the scripts site (tenant ID,
client ID, secret → `SHAREPOINT_CLIENT_SECRETS`, site URL); the 24 agents as CSV (name, email, office,
service_line, title, phone, daily_cap); logo and badge images; the 53 scripts and banners; the North
America wording (O4).

### 2026-09-29 — M7 React frontend

**Built:** everything in the M7 checklist. Verified in a browser against the Compose stack: dev sign-in,
dashboard, tagging a UAE contact started the seeded sequence (intro sent through Celery), a simulated rate
request was classified, stopped the sequence, created a lead, queued a draft and ran `rate-query-handover`;
approving the draft sent it in the thread (`In-Reply-To`/`References` shown in the outbox). A workflow was
created from a starter, broken (live validation flagged the nodes and blocked publishing), saved as v2,
restored from v1, published as v3 and test-run for a Spanish contact.

**Found while building:** review reasons read "rate_query reply"; they now say what the reviewer is asked to
do. The canvas could not be screenshotted in the automation browser (the window was not painting), so
dragging was verified through the DOM rather than visually.

**Decisions:** D64–D69 in §5.

### 2026-09-29 — M6 Hardening

**Built:** everything in the M6 checklist.

**Self-review findings, all fixed:**
- Test-runs used the configured LLM (a real provider outside dev); now always the mock (D60).
- The unsubscribe secret had a public default accepted in production; refused now (D61).
- A GET on an unsubscribe link unsubscribed immediately (mail scanners open links); now a confirmation page (D61).
- `POST /automation/enrollments/{id}/advance` took no Idempotency-Key (D62).
- Business logic lived in three routers (unsubscribe, suppressions, templates, country calendars); moved to
  `service.py` modules (rule 5).
- 20 endpoints had no test; `tests/integration/test_reference_data_api.py` covers them with scope and
  cross-tenant checks.
- A rate-query workflow with `create_pricing_lead` would have created a second lead next to the pipeline's
  (D59); AI JSONB outputs purged as JSON `null` were purged again every day (now SQL NULL).

**Decisions:** D56–D63 in §5 (O1 closed by D56).

### 2026-09-28 — M5 Reply pipeline

**Built:** everything in the M5 checklist. A reply now flows webhook → stored message → pipeline (rules, mock
LLM, policy) → protections, lead, review item → `reply.classified` → reply workflows, and an approved draft is
sent in the original thread (`In-Reply-To`/`References` checked in tests).

**Found while building:**
- A reply workflow with draft + reply nodes would have answered twice when the tenant enabled auto-send (the
  pipeline sends, then the workflow sends). The draft node now sees the pipeline's result (D50).
- `reply_in_thread` trusted a draft status copied into enrollment variables, which a decision taken on an
  unlinked review item would never update; it now reads the review item (D51).
- Owners got two notifications for forwarded buckets (handover + forward); the forward is now implied.
- Uncertain complaints were given a reply draft; they now get a classification review only (D49).

**Decisions:** D45–D55 in §5; O3 open.

### 2026-09-28 — M2 AI core

**Built:** everything in the M2 checklist. `make eval` with the mock provider scores 100 % buckets / 100 %
extraction on the synthetic set (the set is the contract a real model must also meet; run it with
`LLM_PROVIDER=anthropic` once a key is available). No real LLM call was made during development.

**Found while building:** a common French unsubscribe phrasing ("je ne veux plus recevoir") was missed by the
first keyword list; the mock classifier followed injected instructions until it was taught the prompt's rule.

**Decisions:** D39–D44 in §5.

### 2026-09-28 — M4 Engine

**Built:** everything in the M4 checklist. Verified on the Compose stack: tagging a contact through the API
reached the Celery worker via Redis, which processed the event, enrolled the contact, sent the email into the
outbox and completed the enrollment; beat dispatches every 30 s.

**Bugs found while building:**
- `session.refresh()` in test-run discarded the executor's unflushed changes, so step indexes were reused
  (a duplicate idempotency key). Replaced with `flush()`.
- `Result.tuples()` behaves differently in SQLAlchemy 2.1; replaced with plain row iteration.
- The dispatch endpoint first processed, and reported counts for, every tenant. It is now scoped to the caller.
- The unsubscribe placeholder was rendered literally; the link is now computed once and used everywhere.
- Scenario 1 could not pass under the frequency cap as first read; resolved by D30.

**Decisions:** D30–D38 in §5.

### 2026-09-28 — M3 Workflow definitions

**Built:** everything in the M3 checklist. Verified on the running Compose stack: migration 0002 applied on
top of an existing database; publishing a graph with two emails and no wait was refused with
`send_without_wait` on the second send; saving v2 with a wait and publishing it worked.

**Bugs found while building:**
- Enum columns declared as `String` load back as plain `str`, so `status is WorkflowStatus.PUBLISHED` would
  always have been false. Added `StrEnumType` (returns enum members) and applied it to every enum column,
  including M1's `tenants.status` and `api_keys.environment`.
- The first validator skipped the path rules when a graph had a cycle, so scenario 11 would have shown only
  the cycle. It now drops just the cycle-closing edges and reports everything (D27).

**Decisions:** D23–D29 in §5.

### 2026-09-28 — M1 Foundation

**Built:** everything in the M1 checklist above. Verified beyond the test suite by running the real Compose
stack: migrate bootstrapped roles and applied `0001`; API, worker and beat started; readiness green; dev token
→ API key (with idempotent replay) → API-key auth → 403 on missing scope, with `tenant_id` in JSON logs.

**Bugs found while verifying:**
- A malformed key in an IdP's JWKS raised `InvalidKeyError` (not a `PyJWKError`) and would have failed every
  login with a 500; now skipped with a warning (unit test added).
- The running app never imported the `Tenant` model, so the first insert failed on an unresolvable
  foreign key; tests had passed only because the test harness imports all models via Alembic. `create_app`
  and the Celery app now call `load_all_models()`; a fresh-interpreter regression test fails without the fix.

**Decisions:** D1–D11 in §5.

**Notes for M2+:**
- `make` is not installed on this Windows machine; the Makefile works in CI/Linux/WSL, and each target is a
  plain `uv run …` / `docker compose …` command that can be run directly.
- `alembic_version` is readable/writable by the app role through default privileges; harmless, but could be
  revoked in a later migration.
