# LaneReach — AI + Automation Backend Specification (v1)

## 1. Purpose and scope

Build the backend foundation of LaneReach: the **AI module** and the **Automation module**
(workflow engine with triggers, actions, logic, AI and protection nodes), exposed through a
secured FastAPI REST API. Everything must be testable from the backend alone (pytest, Swagger UI,
HTTP files). No frontend in this phase.

The rest of the product (data hub, full segmentation, real sending engine, dashboards, billing)
will be built on top of this foundation later, so structure, naming and interfaces must be
production-grade from day one.

**In scope**
- Project foundation: config, database, migrations, multi-tenancy with RLS, auth, logging, errors, Docker, CI.
- AI module: LLM client, prompt registry, rules pre-classifier, AI tasks, AI run logging, human review queue, evaluation harness.
- Automation module: node registry, workflow definitions + validation + versioning, event/trigger system, enrollments, executor, scheduler, guardrails, execution logs.
- Inbound reply pipeline: webhook → rules → AI → policy → actions.
- Minimal domain entities needed to exercise the above (offices, senders, companies, contacts, messages, suppressions, leads, templates, country calendars).
- Dry-run sending provider and fake notifier so every flow can be tested without sending anything.

**Out of scope (later phases)**
- Frontend and React Flow canvas (the API must already serve node schemas for it).
- Real sending engine integration (Warmbly / Pigeon / Graph API) — only the `SendingProvider` interface + dry-run implementation now.
- Data hub import/dedupe, billing, self sign-up.

## 2. Authentication and authorization

**Decision:** JWT bearer tokens issued by an external OIDC identity provider, validated by the API.
We do NOT build username/password login. Reasons: enterprise customers expect SSO and MFA, the IdP
handles password security, and it works for many customer tenants.

Implement an `AuthMode` setting with these modes:

| Mode | Use | Behaviour |
|---|---|---|
| `oidc` | staging, production | Validate RS256 JWT via the issuer's JWKS (cached, refreshed on unknown `kid`). Check `iss`, `aud`, `exp`, `nbf`, `iat`. Microsoft Entra ID first; keep it IdP-agnostic (Keycloak/Auth0/Entra External ID later). |
| `dev` | local, tests | `POST /api/v1/auth/dev-token` issues HS256 tokens for any tenant/role. **App must refuse to start with `dev` mode when `ENVIRONMENT=production`.** |

Also implement:
- **Tenant API keys** for machine-to-machine calls: format `lr_live_<random>` / `lr_test_<random>`, shown once, stored as SHA-256 hash with prefix for lookup, scoped (list of scopes), revocable, `last_used_at` tracked.
- **Webhook signatures:** `X-LaneReach-Signature: t=<unix>,v1=<hmac_sha256(secret, t + "." + body)>`; reject if older than 5 minutes; store event IDs to block replays.
- **Principal model:** `Principal(subject, tenant_id, roles, scopes, auth_method)` resolved by the `get_current_principal` dependency.
- **Tenant mapping:** `tenant_identity_providers` table maps (`issuer`, external tenant/org id) → LaneReach `tenant_id`.
- **Roles:** `admin`, `campaign_manager`, `office_owner`, `pricing`, `reviewer`, `viewer`.
- **Scopes (checked per endpoint):** `ai:run`, `ai:review`, `workflows:read`, `workflows:write`, `workflows:publish`, `enrollments:write`, `events:write`, `admin:manage`. Roles map to default scope sets in `auth/roles.py`.
- Rate limiting per tenant and per API key (Redis-backed) on AI endpoints.

## 3. Multi-tenancy

- Every table has `tenant_id UUID NOT NULL` and an RLS policy `tenant_id = current_setting('app.tenant_id')::uuid`.
- `get_tenant_session` dependency opens a transaction and runs `SET LOCAL app.tenant_id = :tenant_id`.
- The app DB role is not the table owner and has no `BYPASSRLS`. Migrations run as a separate owner role.
- Workers (Celery) set the tenant context from the job payload before touching data.
- Tests must prove tenant B cannot read, list, update or trigger tenant A resources (expect 404).

## 4. Project structure

```
lanereach/
  CLAUDE.md  README.md  pyproject.toml  Makefile  Jenkinsfile  .env.example
  docker/Dockerfile  docker-compose.yml  docker-compose.test.yml
  alembic/  alembic.ini
  docs/SPEC_AI_AUTOMATION.md  docs/PROGRESS.md  docs/adr/
  http/            # .http request files for manual API testing (VS Code REST Client)
  evals/datasets/labelled_replies.jsonl  evals/run_evals.py  evals/reports/
  src/lanereach/
    main.py                    # create_app()
    core/        config.py clock.py database.py tenancy.py logging.py errors.py pagination.py idempotency.py rate_limit.py
    auth/        dependencies.py oidc.py dev_tokens.py api_keys.py webhook_signatures.py roles.py router.py
    domain/      offices/ senders/ companies/ contacts/ messages/ suppressions/ leads/ templates/ calendars/
                 # each: models.py schemas.py repository.py service.py (router.py only where needed for testing)
    ai/
      llm/       client.py (LLMClient protocol, generate_structured) providers/anthropic.py providers/azure_openai.py providers/mock.py
      prompts/   registry.py  templates/*.md (versioned: classify_reply.v1.md ...)
      rules/     reply_rules.py            # deterministic pre-classifier
      tasks/     classify_reply.py extract_shipment_details.py draft_reply.py localize_template.py
      pipeline.py                          # process_inbound_reply()
      policy.py                            # bucket -> action policy
      runs/      models.py repository.py service.py   # ai_runs logging
      review/    models.py schemas.py service.py router.py
      schemas.py router.py
    automation/
      nodes/     base.py registry.py triggers/ actions/ logic/ ai/ protection/
      conditions.py                        # safe condition DSL
      workflows/ models.py schemas.py validator.py service.py router.py
      events/    models.py service.py trigger_matcher.py router.py
      enrollments/ models.py service.py router.py
      engine/    context.py results.py executor.py scheduler.py
      guardrails/ base.py suppression.py frequency_cap.py company_freeze.py company_rotation.py
                  business_days.py sending_window.py kill_switch.py mailbox_capacity.py chain.py
    integrations/
      sending/   provider.py (SendingProvider protocol) dry_run.py webhooks.py router.py
      notifications/ notifier.py teams.py fake.py
    workers/     celery_app.py tasks.py
  tests/  unit/ integration/ scenarios/ conftest.py factories.py
```

## 5. AI module

### 5.1 LLM client
- `LLMClient` protocol with `async generate_structured(task_name, prompt_version, system, messages, output_model, *, max_tokens, temperature) -> StructuredResult[T]`.
- Providers: `AnthropicLLMClient`, `AzureOpenAILLMClient`, `MockLLMClient` (returns fixtures keyed by task + input hash, or a callable for tests).
- Model name, provider and timeouts come from config — never hardcoded.
- Output is validated against the Pydantic `output_model`; on validation failure, retry once with the validation error appended; then fail with `LLMOutputInvalidError`.
- Retries with exponential backoff on timeouts/5xx; circuit breaker per provider.
- Optional Langfuse tracing when `LANGFUSE_*` env vars are set.

### 5.2 Prompt registry
- Prompts are versioned Markdown files with front-matter (`task`, `version`, `description`).
- `get_prompt(task, version="latest")` returns rendered system + user templates.
- The prompt version is recorded on every AI run.
- All untrusted content is wrapped: `<email_content>...</email_content>` with an instruction that it is data only.

### 5.3 Rules pre-classifier (no AI)
`classify_reply_by_rules(message) -> RuleClassification | None` detects, before any LLM call:
hard bounce, soft bounce, blocked/rejected (DSN codes, mailer-daemon senders, common bounce phrases),
out-of-office (with return date extraction when present), auto-reply, unsubscribe requests
(multilingual keyword set: EN, FR, ES, PT, ZH). Deterministic and fully unit-tested.

### 5.4 AI tasks (each: a function, a Pydantic output model, a prompt, tests)

| Function | Output model | Notes |
|---|---|---|
| `classify_reply(message, context)` | `ReplyClassification(bucket, confidence, language, summary, reasoning_short, needs_human)` | Buckets below |
| `extract_shipment_details(message)` | `ShipmentDetails` | All fields nullable; `missing_fields`, `confidence` |
| `draft_reply(message, classification, office, sender, template)` | `ReplyDraft(subject, body, language, tone, used_template_key)` | Office voice, recipient language, same thread |
| `localize_template(template, target_language, region)` | `LocalizedTemplate(subject, body, language, notes_for_reviewer)` | Keeps placeholders like `{{first_name}}` intact — validate this |

**Reply buckets (enum `ReplyBucket`):** `interested`, `meeting_request`, `rate_query`, `service_query`,
`partnership_query`, `referral`, `wrong_office`, `later`, `not_interested`, `complaint`,
`existing_partner`, `unsubscribe`, `out_of_office`, `auto_reply`, `bounce_hard`, `bounce_soft`,
`blocked`, `unclear`.

**`ShipmentDetails` fields:** origin (city, country ISO-2, port/airport code), destination (same),
mode (`FCL`, `LCL`, `AIR`, `ROAD`, `RAIL`, `COURIER`, `UNKNOWN`), container_type
(`20GP`, `40GP`, `40HC`, `45HC`, `20RF`, `40RF`, `OTHER`), container_quantity, commodity, weight_kg,
volume_cbm, frequency (`one_off`, `weekly`, `monthly`, `quarterly`, `unknown`), incoterm
(Incoterms 2020: `EXW`, `FCA`, `CPT`, `CIP`, `DAP`, `DPU`, `DDP`, `FAS`, `FOB`, `CFR`, `CIF`),
cargo_ready_date, is_hazardous, special_requirements, missing_fields, confidence.

### 5.5 Policy (deterministic)
`decide_reply_policy(classification, tenant_policy) -> ReplyPolicyDecision(action_mode, protection_actions, handover)`.
`action_mode`: `auto_send`, `draft_for_review`, `forward_to_owner`, `none`.
Defaults (overridable per tenant in `tenant_reply_policies`):

| Bucket | Mode | Protection / handover |
|---|---|---|
| interested, meeting_request | draft_for_review | handover to office owner |
| rate_query, service_query | draft_for_review | extract shipment → create pricing lead → notify |
| partnership_query | forward_to_owner | notify office owner |
| referral | draft_for_review | create referred contact |
| wrong_office | forward_to_owner | re-route to correct office |
| later | none | stop sequence; re-enroll after requested date (default 60 days) |
| not_interested | none | stop; rest 180 days |
| complaint | none | Do Not Contact + freeze company + alert owner |
| existing_partner | none | stop outreach; notify owner |
| unsubscribe | none | Do Not Contact |
| bounce_hard / blocked | none | suppress contact |
| bounce_soft | none | retry rules (see guardrails) |
| out_of_office | none | reschedule after return date (or +7 days) |
| auto_reply | none | ignore |
| unclear | draft_for_review | human review |

Rules: confidence below `AI_REVIEW_CONFIDENCE_THRESHOLD` (default 0.80) → `draft_for_review`.
`auto_send` is disabled for every bucket by default and enabled per tenant per bucket.

### 5.6 Inbound pipeline
`process_inbound_reply(message_id)`:
1. Match message → contact, company, office, sender, thread, active enrollments.
2. Stop all cold-outreach enrollments for that contact (stop-on-reply guardrail).
3. Rules pre-classifier; if matched, skip the LLM.
4. Otherwise `classify_reply`; for rate/service queries also `extract_shipment_details`.
5. `decide_reply_policy`; apply protection actions; create lead/handover; create draft or review item.
6. Emit `reply.classified` event (so workflows can trigger on it).
Runs as a Celery task; idempotent per message.

### 5.7 AI runs and review queue
- `ai_runs`: tenant_id, task, prompt_version, provider, model, input_ref (message id), input_hash, output_json, confidence, status, error, latency_ms, tokens_in, tokens_out, estimated_cost, created_at. Raw email text is not duplicated here.
- `review_items`: kind (`reply_draft`, `classification`, `localized_template`), payload, status (`pending`, `approved`, `edited`, `rejected`), reviewer, decided_at. Approving a reply draft sends it through `SendingProvider` in the same thread (dry-run in dev).

### 5.8 Evaluation harness
- `evals/datasets/labelled_replies.jsonl`: `{id, language, text, expected_bucket, expected_shipment?}`. Seed with ~40 synthetic examples across EN/FR/ES/PT/ZH; real labelled replies are added later (target 300).
- `make eval` runs classification and extraction against the configured real provider, writes a JSON + Markdown report (accuracy per bucket, confusion matrix, field-level extraction accuracy) to `evals/reports/`, exits non-zero if below thresholds in config.
- CI runs evals only when an API key is available.

## 6. Automation module

### 6.1 Nodes
```python
class BaseNode(ABC):
    """A building block users place on the workflow canvas."""

    node_type: ClassVar[str]  # e.g. "send_email"
    category: ClassVar[NodeCategory]  # trigger | action | logic | ai | protection
    display_name: ClassVar[str]
    description: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]
    output_handles: ClassVar[tuple[str, ...]] = ("next",)

    async def execute(self, context: ExecutionContext, config: BaseModel) -> NodeResult: ...
```
- `@register_node` adds a class to the `NodeRegistry`; duplicates fail at startup.
- `NodeResult` variants: `Continue(handle)`, `Wait(until, reason)`, `Stop(reason)`, `Fail(error, retryable)`.
- `GET /api/v1/automation/nodes` returns every node with category, description, handles and the JSON Schema of its config (this is what the future canvas renders).

**Node catalog v1**

| Category | node_type values |
|---|---|
| Triggers | `trigger_contact_created`, `trigger_tag_added`, `trigger_reply_classified` (config: buckets), `trigger_manual_enroll`, `trigger_scheduled_date` |
| Actions | `send_email` (office, template key), `reply_in_thread`, `create_pricing_lead`, `notify_office_owner`, `notify_teams_channel`, `add_tag`, `remove_tag`, `change_contact_stage`, `assign_owner` |
| Logic | `wait_business_days` (days), `wait_until_date`, `if_condition` (condition DSL; handles `true`/`false`), `branch_on_reply_bucket` (one handle per bucket + `other`), `ab_split` (deterministic hash, percentages), `stop` |
| AI | `ai_classify_reply`, `ai_extract_shipment_details`, `ai_draft_reply`, `ai_localize_template` |
| Protection | `suppress_contact`, `freeze_company`, `pause_workflow_for_company` |

### 6.2 Condition DSL
JSON only, no code: `{"all": [{"field": "contact.country", "op": "in", "value": ["AE", "SA"]}, ...]}`.
Operators: `eq`, `ne`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `contains`, `exists`. Combinators: `all`, `any`, `not`.
Fields are whitelisted per context (`contact.*`, `company.*`, `reply.bucket`, `reply.confidence`, `lead.*`, `enrollment.variables.*`).

### 6.3 Workflow definitions
- Stored as JSON graph: `{trigger: {id, type, config}, nodes: [{id, type, config, position}], edges: [{source, target, handle}]}` (`position` is stored for the future canvas; ignored by the engine).
- `workflows` (name, description, status `draft|published|archived`, current_version) and `workflow_versions` (immutable graph, version number, created_by, published_at).
- Editing a published workflow creates a new draft version; publishing makes it current. Running enrollments finish on the version they started.
- `validate_workflow_graph(graph) -> ValidationReport` (errors + warnings, each with node id). Rules:
  exactly one trigger; all node types registered; every config validates against its schema; every
  edge handle exists on its source node; no orphan nodes; graph is acyclic (v1); every path ends in
  `stop` or a terminal action; number of `send_email` nodes on any path ≤ `MAX_COLD_EMAILS_PER_SEQUENCE`
  (default 3); a `send_email` must be preceded by a wait if it is not the first send.
- Publishing is refused if validation has errors.

### 6.4 Events and triggers
- `events` table (outbox pattern): type, payload, contact_id, occurred_at, processed_at, idempotency key.
- Event types v1: `contact.created`, `contact.tag_added`, `reply.received`, `reply.classified`, `manual.enroll`, `schedule.tick`.
- `publish_event()` stores the event; a worker runs `match_event_to_triggers()` → `enroll_contact_in_workflow()` for each matching published workflow.
- A contact cannot have two active enrollments in the same workflow.
- `POST /api/v1/automation/events` lets testers inject events manually.

### 6.5 Enrollments and executor
- `enrollments`: workflow_version_id, contact_id, status (`active`, `waiting`, `completed`, `stopped`, `failed`, `paused`), current_node_id, next_run_at, variables (JSON), stop_reason.
- `enrollment_steps`: node_id, node_type, started_at, finished_at, result, handle_taken, guardrail_decisions, error, idempotency_key (`enrollment_id:step_index`).
- `advance_enrollment(enrollment_id)`: lock row (`FOR UPDATE SKIP LOCKED`), execute nodes until a `Wait`/`Stop`/`Fail` or `MAX_STEPS_PER_TICK` (default 20), record every step.
- `dispatch_due_enrollments()` (Celery beat, every 30 s): picks due enrollments in batches per tenant and enqueues `advance_enrollment`.
- Retryable failures back off (1 min, 5 min, 30 min), then `failed`.
- Pause/resume/cancel per enrollment and per workflow.

### 6.6 Guardrails (run inside `send_email` and `reply_in_thread`, before the provider is called)
`evaluate_send_guardrails(context, outbound_email) -> GuardrailDecision(outcome: allow|defer|block, reason, defer_until)`.
Chain order and rules:

| Guardrail | Rule | Outcome |
|---|---|---|
| Kill switch | Tenant or office sending paused | defer |
| Suppression | Contact unsubscribed, hard-bounced, blocked, complained or Do Not Contact | block |
| Company freeze | Company frozen by complaint/block | block |
| Frequency cap | Max 1 cold email per contact per 30 days across all offices (cold only; thread replies exempt) | defer until allowed |
| Company rotation | Only one office may start on a company per ISO week | defer to next week |
| Business days | Recipient country's workdays and public holidays (`country_calendars` + `holidays` lib; e.g. Fri–Sat weekends) | defer to next workday |
| Sending window | Recipient local time within window (default 09:00–11:30) | defer to next window |
| Mailbox capacity | Sender mailbox daily cap reached | defer to next day |

Bounce/complaint handling on inbound events: hard bounce → suppress; soft bounce → retry after 24 h,
suppress after 3 in 30 days; blocked → suppress + flag mailbox; complaint → Do Not Contact + freeze
company + alert; unsubscribe → Do Not Contact.

Every guardrail decision is stored on the enrollment step so reports can show why an email was not sent.

### 6.7 Sending and notifications (interfaces only in v1)
- `SendingProvider` protocol: `send_email(outbound) -> ProviderMessageRef`, `send_reply_in_thread(...)`, `parse_webhook(request) -> list[ProviderEvent]`.
- `DryRunSendingProvider` writes to `outbox` (tenant_id, to, from, subject, body, thread refs, headers incl. `List-Unsubscribe`, created_at) and returns fake Message-IDs.
- Inbound webhook `POST /api/v1/webhooks/sending/{provider}` (HMAC-verified) accepts reply, bounce and complaint events; a `dry_run` provider format is used by tests to simulate replies.
- `Notifier` protocol with `TeamsWebhookNotifier` and `FakeNotifier` (stores notifications for assertions).

## 7. API surface (all under `/api/v1`, OpenAPI tags in brackets)

**[Auth]** `POST /auth/dev-token` (dev only) · `GET /auth/me` · `POST /auth/api-keys` · `GET /auth/api-keys` · `DELETE /auth/api-keys/{id}`

**[AI]**
`POST /ai/replies/classify` · `POST /ai/replies/extract-shipment` · `POST /ai/replies/draft` ·
`POST /ai/templates/localize` · `POST /ai/replies/{message_id}/process` (full pipeline) ·
`GET /ai/runs` · `GET /ai/runs/{id}`

**[AI Review]** `GET /ai/reviews` · `GET /ai/reviews/{id}` · `POST /ai/reviews/{id}/approve` · `POST /ai/reviews/{id}/edit-and-approve` · `POST /ai/reviews/{id}/reject`

**[Automation]**
`GET /automation/nodes` · `GET /automation/nodes/{node_type}` ·
`POST /automation/workflows` · `GET /automation/workflows` · `GET /automation/workflows/{id}` ·
`PUT /automation/workflows/{id}` (new draft version) · `POST /automation/workflows/{id}/validate` ·
`POST /automation/workflows/{id}/publish` · `POST /automation/workflows/{id}/pause` · `POST /automation/workflows/{id}/resume` ·
`POST /automation/workflows/{id}/test-run` (simulate a contact through the graph with the mock LLM and dry-run provider, simulated clock; returns the step-by-step path and rendered emails, persists nothing) ·
`GET /automation/workflows/{id}/versions`

**[Enrollments]** `POST /automation/workflows/{id}/enrollments` · `GET /automation/enrollments` · `GET /automation/enrollments/{id}` · `GET /automation/enrollments/{id}/steps` · `POST /automation/enrollments/{id}/pause|resume|cancel`

**[Events]** `POST /automation/events` · `GET /automation/events`

**[Webhooks]** `POST /webhooks/sending/{provider}`

**[Test data]** minimal CRUD for `offices`, `senders`, `companies`, `contacts`, `templates`, `suppressions`, `leads` (read), `outbox` (read, dev/staging only).

**[Health]** `GET /health/live` · `GET /health/ready` (DB, Redis, LLM config).

Conventions: plural nouns; cursor pagination (`limit`, `cursor`); `Idempotency-Key` on side-effecting POSTs;
errors as RFC 7807 `application/problem+json` with `type`, `title`, `status`, `detail`, `instance`, `errors[]`;
every response includes `X-Request-ID`.

## 8. Security requirements
- Request body size limits; strict Pydantic models (`extra="forbid"`).
- CORS closed by default (no frontend yet).
- Secrets only from environment / Azure Key Vault; `.env.example` documents every variable.
- No PII or email bodies in logs; mask emails in log fields.
- Prompt-injection test cases in the test suite (email text that tries to change the bucket, request data, or trigger actions must not change policy outcomes beyond its genuine classification).
- Dependency scanning (`pip-audit`) in CI.
- Data retention setting for message bodies and AI outputs (default 365 days) with a purge task.

## 9. Seed data (`make seed`)
Tenant "Skyfer Logistics"; offices USA/Canada, France, Spain, UAE, Sri Lanka with senders (Mavis, Maya,
Julien, Hugo, UAE sender, Sri Lanka sender); ~50 companies with branches and contacts across 12 countries
(including AE, SA, EG, IN, CN, FR, ES, BR, US, CA, LK, DE); country calendars; templates `intro`,
`follow_up_1`, `reply_rate_query`, `reply_interested` in EN plus FR/ES; two published workflows:
`office-intro-sequence` and `rate-query-handover`; a second tenant "Demo Forwarder" for isolation tests.

## 10. Required scenario tests (`tests/scenarios/`)
1. Intro sent → no reply → follow-up after 5 business days for a UAE contact → stop (checks calendar and window).
2. Reply "Please quote 2x40HC Chennai to Rotterdam, monthly, FOB" → `rate_query` → shipment extracted → lead created → owner notified → sequence stopped.
3. Unsubscribe reply → Do Not Contact → later send attempt is blocked and logged.
4. Hard bounce webhook → contact suppressed.
5. Complaint → company frozen → a different office's workflow is blocked for all branches.
6. Frequency cap: second office tries within 30 days → deferred.
7. Low-confidence classification → review item → approve → reply sent in the same thread (outbox has correct thread headers).
8. Out-of-office with return date → enrollment rescheduled after that date.
9. Tenant isolation across workflows, enrollments, AI runs and reviews (404s).
10. Prompt-injection reply does not bypass policy or guardrails.
11. Invalid workflow (4 sends, missing stop, cycle) cannot be published; errors name the node ids.
12. Kill switch pauses sending; resuming sends deferred emails in the next window.

## 11. Milestones
- **M1 Foundation** — skeleton, config, Docker Compose, DB + Alembic, tenancy + RLS, auth (all modes), errors, logging, health, Jenkinsfile, Makefile, tests.
- **M2 AI core** — LLM client + providers + mock, prompt registry, rules pre-classifier, 4 AI tasks, AI runs, AI endpoints, eval harness.
- **M3 Workflow definitions** — node base + registry, node catalog, condition DSL, workflow CRUD, validation, versioning, node endpoints.
- **M4 Engine** — events + triggers, enrollments, executor, scheduler, guardrails, logic/action/protection nodes, dry-run provider, notifier, execution logs, test-run endpoint.
- **M5 Reply pipeline** — webhook, pipeline, policy, review queue, leads, AI nodes wired into the engine.
- **M6 Hardening** — all scenario tests, security checks, rate limits, seed data, README, `.http` files, coverage ≥ 85% on `ai/`, `automation/engine/`, `automation/guardrails/`.

Definition of done for every milestone: `make check` passes, new code has docstrings and tests,
Swagger shows every endpoint with descriptions and examples, `docs/PROGRESS.md` updated.
