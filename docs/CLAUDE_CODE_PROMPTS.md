# Claude Code prompts — LaneReach AI + Automation backend

Run one milestone per session. Start each in plan mode (Shift+Tab), review the plan, then let it build.
Run `/clear` between milestones; CLAUDE.md and the spec carry the context.

---

## Session 0 — Kickoff and plan (no code)

```
Read CLAUDE.md and docs/SPEC_AI_AUTOMATION.md fully.

We are building the LaneReach AI + Automation backend described in the spec. Do not write code yet.

1. Summarise the system in 10 lines so I can confirm you understood it.
2. Propose the exact folder/file tree for milestones M1–M6.
3. List the database tables with key columns, indexes and RLS policies.
4. List every API endpoint with method, path, required scope, request and response model names.
5. List the risks or ambiguities you see in the spec and the choice you recommend for each.
6. Create docs/PROGRESS.md with this plan and a checklist per milestone.

Wait for my approval before starting M1.
```

## Session 1 — M1 Foundation

```
Implement milestone M1 (Foundation) from docs/SPEC_AI_AUTOMATION.md, following CLAUDE.md.

Include: pyproject with uv, ruff, mypy, pytest config; Dockerfile + docker-compose (api, worker, beat,
postgres 16 with pgvector, redis); Makefile targets; settings with pydantic-settings and .env.example;
Clock abstraction; async SQLAlchemy + Alembic with separate owner and app roles; tenancy with RLS and
get_tenant_session; auth in oidc and dev modes, tenant API keys, webhook signature verification,
roles and scopes; RFC 7807 errors; structlog with request IDs; idempotency-key support; health
endpoints; Jenkinsfile (lint, type-check, test, build image).

Tests: auth success/failure per mode, expired/invalid tokens, scope checks, dev mode refused in
production, API key lifecycle, webhook signature and replay protection, RLS isolation between two tenants.

Finish with `make check` green, then update docs/PROGRESS.md and commit.
```

## Session 2 — M2 AI core

```
Implement milestone M2 (AI core) from the spec.

Include: LLMClient protocol with generate_structured and schema-validated retry; Anthropic, Azure OpenAI
and Mock providers (model names from config); versioned prompt registry with untrusted-content
delimiters; classify_reply_by_rules pre-classifier (bounces, OOO with return date, auto-reply,
multilingual unsubscribe); AI tasks classify_reply, extract_shipment_details, draft_reply,
localize_template with Pydantic output models; ai_runs logging with tokens, latency and cost; the AI
endpoints; the eval harness with ~40 synthetic labelled replies in EN/FR/ES/PT/ZH and `make eval`.

Tests use MockLLMClient only. Include prompt-injection cases and placeholder preservation for
localize_template. Finish with `make check` green, update PROGRESS.md, commit.
```

## Session 3 — M3 Workflow definitions

```
Implement milestone M3 (Workflow definitions) from the spec.

Include: BaseNode, NodeResult variants, @register_node and NodeRegistry; every node class in the v1
catalog with its config model, description and handles (execute() may raise NotImplementedError for
nodes whose engine support lands in M4, but configs and schemas must be complete); the safe condition
DSL with field whitelist; workflow and workflow_version models; validate_workflow_graph with every rule
in section 6.3; workflow CRUD, validate, publish, versions and node catalog endpoints.

Tests: each validation rule (errors name node ids), versioning behaviour, node catalog JSON Schemas,
condition DSL operators and rejection of unknown fields. `make check` green, PROGRESS.md, commit.
```

## Session 4 — M4 Engine

```
Implement milestone M4 (Engine) from the spec.

Include: events outbox with publish_event and match_event_to_triggers; enroll_contact_in_workflow
(no duplicate active enrollments); advance_enrollment with row locking, step logging and max steps per
tick; dispatch_due_enrollments on Celery beat; retry/backoff; pause/resume/cancel; the full guardrail
chain in section 6.6 with decisions stored on steps; execute() for all trigger, action, logic and
protection nodes; DryRunSendingProvider with outbox and List-Unsubscribe headers; FakeNotifier and
TeamsWebhookNotifier; enrollment, step and event endpoints; the workflow test-run endpoint using a
simulated clock.

Tests: every guardrail (including Friday–Saturday weekend countries and public holidays), frequency cap
across offices, company rotation, each logic node, executor idempotency and crash recovery.
`make check` green, PROGRESS.md, commit.
```

## Session 5 — M5 Reply pipeline

```
Implement milestone M5 (Reply pipeline) from the spec.

Include: the sending webhook endpoint (dry_run format for tests) for replies, bounces and complaints;
process_inbound_reply as an idempotent Celery task following section 5.6; decide_reply_policy with
tenant overrides and the confidence threshold; protection actions; lead creation with shipment details;
review queue endpoints (approve sends through the provider in the same thread); AI nodes wired into the
engine; the reply.classified event.

Tests: each bucket's policy, low-confidence routing, review approve/edit/reject, thread headers on
replies, idempotent reprocessing. `make check` green, PROGRESS.md, commit.
```

## Session 6 — M6 Hardening

```
Implement milestone M6 (Hardening) from the spec.

Include: all 12 scenario tests in section 10; rate limits on AI endpoints; request size limits;
pip-audit in CI; retention purge task; `make seed` with both tenants and the two sample workflows;
http/*.http files covering every endpoint in a realistic order (dev token → create workflow → publish →
inject event → advance → simulate reply → review → lead); README with setup, architecture, and how to
test everything from Swagger. Reach ≥ 85% coverage on ai/, automation/engine/ and automation/guardrails/.

Then do a self-review: list any spec rule not yet enforced, any endpoint missing tests, and any security
concern. Fix what you can; record the rest in PROGRESS.md. Commit.
```
