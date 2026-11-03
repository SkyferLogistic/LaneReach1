# LaneReach

AI-powered outreach platform that turns a freight forwarder's contact database into quote-ready leads for
the pricing team, with drag-and-drop workflows and built-in deliverability guardrails.

This repository holds the **AI + Automation backend** (FastAPI, PostgreSQL + RLS, Redis, Celery) and the
**LaneReach console** (`web/`, React), where workflows are built on a canvas and replies are reviewed.

- Spec: [docs/SPEC_AI_AUTOMATION.md](docs/SPEC_AI_AUTOMATION.md)
- Progress, decisions and open questions: [docs/PROGRESS.md](docs/PROGRESS.md)
- Architecture decisions: [docs/adr/](docs/adr/)
- Rules for contributors (human or AI): [CLAUDE.md](CLAUDE.md)

**Status:** M1–M7 done (foundation, AI core, workflow definitions, engine, reply pipeline, hardening,
console). See [docs/PROGRESS.md](docs/PROGRESS.md).

## What it does

1. **Workflows** built from 27 nodes (triggers, actions, logic, AI, protection), validated and versioned.
   A tag, a new contact, a date, a manual enrollment or a classified reply starts them.
2. **The engine** runs each enrollment step by step (Celery), and every email passes one **guardrail
   chain**: kill switch, suppression, company freeze, 30-day frequency cap, office rotation, recipient's
   business days and sending window, mailbox capacity. Every decision is stored on the step.
3. **Replies** arrive through a signed provider webhook. The **reply pipeline** runs rules first (bounces,
   out-of-office, unsubscribes), then the LLM, then a **deterministic policy**: stop the sequence, suppress,
   freeze, reschedule, create a pricing lead, notify the owner, and draft an answer for the **review queue**.
   Approving a draft sends it in the original thread. Auto-send exists but is off for every bucket until a
   tenant admin enables it, and never applies to uncertain classifications.
4. **AI** is provider-agnostic (Anthropic, Azure OpenAI, and a deterministic mock used by tests and key-less
   development). Outputs are schema-validated; email text is untrusted and never changes policy.

## Prerequisites

- Python 3.13 and [uv](https://docs.astral.sh/uv/) (`pip install uv` works if the installer is unavailable)
- Docker (Compose v2) — for the local stack and for the test suite (testcontainers)
- `make` is optional: every target is a plain `uv run …` / `docker compose …` command (see the Makefile)

## Quick start

```bash
cp .env.example .env
uv sync
make up          # postgres, redis, migrate (roles + Alembic), api, worker, beat
make seed        # two demo tenants with offices, senders, 52 companies, templates, 2 workflows
```

- **Console: http://127.0.0.1:8081** (sign in with a seeded tenant; dev mode)
- API: http://127.0.0.1:8080 · Swagger UI: http://127.0.0.1:8080/docs · readiness:
  http://127.0.0.1:8080/api/v1/health/ready
- Host ports default to Postgres 15432, Redis 16379, API 8080, console 8081 (override `LANEREACH_*_PORT`).
- No API key is needed: `LLM_PROVIDER=mock` (the default) classifies, extracts and drafts
  deterministically. Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` for a real model.

Without `make`: `docker compose up -d --build`, `docker compose run --rm migrate lanereach-admin seed`, then
`uv run pytest`, `uv run ruff check .`, `uv run mypy`.

## The console (`web/`)

React 19 + TypeScript + Vite, React Flow for the canvas, TanStack Query for data. API types are generated
from the backend's OpenAPI document (`make web-types`), so a backend change that breaks the console fails
`npm run typecheck`.

| Screen | What you can do |
|---|---|
| Dashboard | Live numbers for 7/30/90 days (emails sent, replies, leads, bounces) with change against the previous period, sent/replies/leads chart, reply mix, **lane map** (great-circle lanes from the sales desk to every destination, sized by emails and coloured by reply rate, with traffic moving on them), **outreach funnel** (contacted → replied → interested → lead → won), **when replies arrive** heatmap in office time with the best slot, **live activity** feed, **launch checklist**, **business by service line**, today's capacity per line, script coverage and held countries, busiest agents |
| Inbox | Every agent mailbox in one place: folders (inbox, unread, awaiting reply, AI drafts, snoozed, done, sent), filters by agent, office, label, assignee and search; the thread reads like a chat (quoted history folded); **reply in place** from the thread's agent with the signature, through every send guardrail; AI suggestion; mark read/unread, done, snooze, assign, labels; keys j/k/e/s/u/r |
| Pipeline & tasks (CRM) | Every rate request opens a deal in **Freight quotes** (Quote requested → Sourcing rates → Quoted → Negotiating → Won / Lost); drag deals across the Kanban board, see value per stage and weighted pipeline, open a deal for its stage stepper, timeline (stage changes, emails, notes, tasks), details, tasks and notes; **Tasks** (today, overdue, upcoming, done); CRM card on every contact; extra pipelines and stages for admins |
| Quotes (CRM) | Build a quotation from a deal: service line and mode (FCL, LCL, FTL, LTL, express, bonded…), incoterm, lane, validity and transit; **charge presets per mode**, live totals, the customer's view; **send in the customer's thread** from their agent, accept/reject (acceptance sets the deal value), duplicate to re-quote, printable page (Save as PDF); sent quotes expire automatically; Quotes page with drafts, sent, **expiring soon** and win rate. Settings → Services chooses which lines the company sells |
| Online quote acceptance | The quote email carries a **View and accept online** link (signed per quote, no sign-in): the customer sees the quote and its charges and clicks **Accept** or **Decline** with a note; the answer is recorded on the quote, the deal's owner is notified and quote workflows run. Expired quotes cannot be accepted online |
| Pricing | **Rate sheets** from Excel/CSV kept in the library's `Rates/` folder (SharePoint/OneDrive, re-imported when the file changes) or uploaded: every tab, column names like POL/POD/40HC/Validity recognised, a column-mapping editor and row problems; searchable rates with validity; **rate websites** opened with the lane filled in (links only, never logins) and **market rates** recorded on the deal; a deal **Pricing** tab with matching options and why they match, **draft a quote from rates** (or *Fill from rate sheets* in the builder), lane price history; the AI only maps unusual place spellings onto places in your sheets |
| AI insights | **Lead scores** (0–100 on every deal, with the reasons: AI-read intent in the latest reply plus shipment completeness, reply speed, history, quotes, idle days), a **weekly AI digest** written only from LaneReach's own numbers (lanes heating up, win/loss by lane, waiting quotes, quiet deals), and **follow-up suggestions** for quiet deals (action + draft in the customer's language, turned into a task; never sent automatically), **conversation summaries** in the Inbox, and **AI cover notes** for quotes that may only use the quote's own numbers |
| AI cost controls | A **monthly AI budget** per company (when spent, no model is called: built-in messages and rules take over), **AI or rules** per area (lead reading, quiet-deal follow-ups, weekly digest), **built-in messages** per kind of reply (no AI call), a **cheaper model for reading tasks**, and this month's AI cost by task in *Settings → AI usage* |
| Grounded drafts and AI quality | Reply drafts follow the office's **own past answers** to similar emails (retrieval over the company's history with PostgreSQL full-text search, no extra AI call; never copying another customer's details), and an **AI quality** view measures the AI from people's decisions: replies sorted by rules/AI/a person, drafts sent as written, edited or rejected, follow-ups used, won vs lost lead scores, failed runs |
| Scripts | The script × language matrix (live, AI draft, missing, held, error), **upload Word/PDF** (drag and drop, saved to SharePoint first), start a script from text, **Sync now**, **translate missing languages** (AI drafts), approve drafts, and an **exact preview** in a sandboxed frame for any destination country and agent; banners and brand images |
| Deleting | Delete buttons everywhere (campaigns, agents, mailboxes, offices, contacts, companies, deals, quote requests, enrollments, forms, webhooks…) with a confirmation dialog; records still in use are refused with the alternative (archive, switch off); contacts are **erased** with all their data while their address stays suppressed |
| Assistant and help | **Ask LaneReach** (top bar, Ctrl+J): an **agentic data assistant** that answers questions about your own deals, quotes, contacts, rate sheets, inbox and activity with read-only tools (links to the records, every number checked against what it looked up); answers how-to questions only from the help articles and the live list of workflow steps, with sources, and says plainly when LaneReach does not do something; **drafts a workflow** from a description, validated and saved as a draft for you to review. **Help center** at `/help`. Search is PostgreSQL full-text plus pgvector semantic search when an Azure OpenAI embeddings deployment is configured |
| Public website | `/` for visitors (and `/welcome`): what LaneReach does with an animated lane map, a scripted AI inbox demo, features, what the AI does and never does, a comparison with other kinds of tools, live product numbers (`GET /site`), **Try it with your own email** (the real reply AI on pasted text, nothing stored, `PUBLIC_AI_DEMO`), a time-saved calculator, **Contact us** (posts to a web form of your own workspace, `PUBLIC_CONTACT_FORM_KEY`) and **Sign in** |
| Sign-in | **Sign in with Microsoft** (OIDC Authorization Code + PKCE) when `AUTH_MODE=oidc`; development sign-in with role tiles otherwise, remembering the last company and role and saying what the chosen role can and cannot do; a user chip in the top bar shows who is signed in, with switch role |
| Integrations | **Signed webhooks** for Zapier, Make, n8n or any system (choose events such as `lead.created`, `deal.won`, `meeting.booked`; HMAC signature, retries, auto switch-off, delivery log, test ping, secret rotation), **Slack** alerts per channel, **Calendly / Cal.com** bookings on the contact and deal (new invitees become contacts; trigger *Meeting booked*), **MillionVerifier** email verification (offline rules until connected). Secrets are encrypted and never shown again |
| Website forms | "Request a quote" forms for the website: copy-paste HTML (no script), honeypot and rate limits, new contacts with a tag and office, submissions list, trigger *Web form submitted* with the answers as variables |
| Agents | **Mailbox** per agent (SMTP to send, IMAP to read replies; presets for Microsoft 365, Google Workspace and Zoho; test the login; status on every card); boards per service line (air, ocean, road, rail, courier, customs, warehousing, project), each agent's signature as recipients see it (agent + QR code, logo + tagline + offices, phone with extension, WhatsApp, fax, email, address, website, badge row, promo banner), title/phone/mobile/cap/line, custom HTML or a **signature picture** (PNG/JPEG), **CSV import** of all agents; **mailbox health** score per agent (bounces, complaints, flag, cap use) |
| Countries & languages | The 53 countries grouped by script language, their `{to_country}` phrase, fallback (held) and on/off switches; add or change countries |
| Campaigns (workflows) | **Results** per campaign: funnel (enrolled → emailed → opened → clicked → replied → positive → quote requests → deals won), status counts, per-step numbers with the branches taken (also as bubbles on the canvas), top links; **schedule** (start/end dates, weekdays); sent/open/click/reply numbers in the list. Node **Wait for open or click** branches on engagement |
| Workflows | Create from starters (intro, scripts, regions, won/lost deals, quote expiring, stale deal, bounces, new quote request…); add steps with the **+ under each output**, the **+ on any edge**, or by dropping a connection on empty canvas (a searchable step picker opens); drag edges to reconnect (loops refused with the reason), **undo/redo**, **tidy up**; drag nodes from the palette, connect handles, edit each node's settings (forms generated from the node's JSON Schema, with office/sender/template pickers and a visual condition builder), live validation with errors on the nodes, save versions, publish, pause/resume/archive, restore old versions, **test-run** a contact with a simulated reply and see every step, guardrail decision and email |
| Contacts / companies | **Import a CSV** (up to 5,000 rows, optional tag that starts workflows), search, create, edit, **add a tag to start workflows**, enroll in a workflow, **simulate a reply, bounce or complaint**, see the email history with the reply pipeline's classification and actions; branches and the company freeze switch |
| Review queue | Read the reply and the AI's reasoning, edit the draft, approve & send in the thread, or reject |
| Leads | Shipment details extracted by AI; move leads through quoting → won/lost (confetti for a win, and "Lead status changed" workflows start); **export to CSV** |
| Enrollments / events | Every step with its guardrail decisions; pause, resume, cancel, run now, skip a wait |
| Offices, templates, outbox, notifications, suppressions | Country, time zone and language pickers, a text or **picture signature** per office, mailboxes and caps, templates per language with AI localization, the dry-run outbox with thread headers |
| AI playground / settings | Run classify/extract/draft on any text; kill switch, sending window (with the weekly grid), reply policy (auto-send per bucket), **branding** for signatures (live preview), **scripts library** (local folder or SharePoint, test connection, last sync), API keys, country calendars |

Look and feel: a navy shell with icons, amber for air and cyan for ocean, animated air and sea lanes
(SVG, no GIFs; they stop when the system asks for reduced motion), count-up numbers, skeleton loaders,
light/dark theme (remembered per browser), **Ctrl+K** to jump to any screen or run an action, bundled
fonts (Inter, Space Grotesk) and flag emojis that also work on Windows. The login page has a globe you can
drag, cards light up under the cursor and rise into view, a bar at the top shows loading, the bell lists
unread notifications, **?** shows the keyboard shortcuts (**g** then a letter jumps to a screen), a won
lead gets confetti, and phones get a slide-in menu.

Run it with hot reload against a local API: `make web-install && make web-dev` (http://localhost:5173).
In Compose it is the `web` service: nginx serves the build and proxies `/api` to the API (one origin, no CORS).

## Email scripts, countries and agents

Outreach emails ("scripts") are **Word files kept in a SharePoint library, which is the master copy**.
Marketing edits them there; LaneReach picks up changes every 5 minutes (or on **Sync now**). Scripts
uploaded or started in the console are written into the library too.

```
LaneReach/
  Scripts/Air/<Script name>/<language>.docx      tr.docx, pt-br.docx, "Turkish.docx"... (+ .pdf copy)
  Scripts/Ocean/<Script name>/<language>.docx
  Banners/Air/<CC or country name>.png            Banners/Air/default.png when a country has none
  Brand/logo.png, Brand/badge-*.png               images for the generated agent signatures
```

In the Word file, the first line is `Subject: …` and placeholders use single braces:
`{first_name|there}`, `{last_name}`, `{company_name}`, `{country_name}`, `{to_country}`, `{network}`,
`{agent_name}`, `{agent_title}`, `{agent_email}`, `{agent_phone}`, `{banner}`, `{signature}`,
`{unsubscribe_link|Unsubscribe here}`. A missing signature or unsubscribe link is added automatically
(with a warning shown next to the script).

- **Countries & languages** (`/countries`): each destination country has a script language (53 countries
  in 18 languages are seeded) and its `{to_country}` phrase in that language (`Türkiye'ye`,
  `vers la France`). Add or change countries at any time. USA and Canada use English (North America) and
  are **held, not sent,** until that script exists.
- **Agents**: each sender is an agent of a service line (air, ocean, road, rail, courier & express,
  customs, warehousing, project cargo, or any) with a title, phone and a signature (generated from
  the tenant branding, custom HTML, or an image). A `send_email` step picks an agent of its service line
  (fewest emails today) and the same agent keeps the whole thread. Import all agents from CSV with
  `POST /senders/import`.
- **AI translations** of missing languages are saved to the library as drafts and are not sent until
  someone approves them.

Without SharePoint the library is a local folder (`LOCAL_DOCUMENTS_PATH`, a Compose volume), so
everything works before the Azure app is registered. To use SharePoint: register an Azure AD app with
Microsoft Graph `Sites.Selected` (application) granted on the site, put the client secret in
`SHAREPOINT_CLIENT_SECRETS` (`{"<tenant-slug>": "<secret>"}`), then set the site URL, tenant ID and
client ID in `PUT /settings/library` and run `POST /settings/library/test`.

## Try it

In the console: sign in, open **Contacts**, tag a UAE contact `intro` (the intro goes out in the Dubai
sending window), then **Simulate reply** → "Rate request" and open the **Review queue** and **Leads**.

With the API directly:

1. `GET /api/v1/auth/dev-tenants` lists the seeded tenants (dev mode only).
2. `POST /api/v1/auth/dev-token` with `{"tenant_id": "<id>", "roles": ["admin"]}`; click **Authorize** in
   Swagger and paste the `access_token`.
3. Tag a contact `intro` (`POST /api/v1/contacts/{id}/tags`): the published `office-intro-sequence` sends
   the intro (see `GET /api/v1/outbox`) within the recipient's sending window.
4. Simulate their reply with `POST /api/v1/test-tools/sending-events` (for example a rate request), then
   look at `GET /api/v1/leads`, `GET /api/v1/ai/reviews` and approve the draft.

The `.http` files script every endpoint (VS Code REST Client / JetBrains HTTP Client):

| File | Covers |
|---|---|
| [`01-health.http`](http/01-health.http) | Liveness and readiness |
| [`02-auth.http`](http/02-auth.http) | Dev tenants and tokens, `me`, API keys |
| [`03-workflows.http`](http/03-workflows.http) | Node catalog, condition fields, create/validate/publish, versions, test-run |
| [`04-engine.http`](http/04-engine.http) | Offices → senders → companies → contacts → templates → workflow → enrollment → outbox |
| [`05-replies.http`](http/05-replies.http) | Simulated replies and bounces, pipeline result, review queue, webhook |
| [`06-ai.http`](http/06-ai.http) | AI tasks called directly, AI runs, reply policies |
| [`07-data.http`](http/07-data.http) | Reference data, suppressions, calendars, leads, notifications, kill switch, unsubscribe, dashboard numbers |
| [`08-automation-operations.http`](http/08-automation-operations.http) | Graph validation, archiving, enrollment controls, events, dispatcher |
| [`14-deletes.http`](http/14-deletes.http) | Deletes with their 409 rules, contact erase, sign-in config |
| [`16-mailboxes.http`](http/16-mailboxes.http) | Agent mailboxes: presets, connect, test, disconnect; the sent log with queued/sent/failed |
| [`17-pricing.http`](http/17-pricing.http) | Pricing: upload a rate sheet, columns, rates search, websites and lane links, a deal's matching rates, draft a priced quote, market checks |
| [`15-quotes.http`](http/15-quotes.http) | Logistics catalog, quotes (create, send in the thread, decide, duplicate, printable), services on offer |
| [`13-integrations.http`](http/13-integrations.http) | Webhooks, Slack, meetings webhook, email verification, website forms |
| [`12-campaigns.http`](http/12-campaigns.http) | Campaign results and schedule, open/click tracking switches, mailbox health, audit log |
| [`11-inbox.http`](http/11-inbox.http) | Unified inbox: folders, counts, a conversation, read/done/snooze/labels, reply in the thread |
| [`10-crm.http`](http/10-crm.http) | Pipelines, deals, board moves, timeline, tasks, notes, CRM summary |
| [`09-scripts.http`](http/09-scripts.http) | Script library (matrix, preview, upload, sync, translate, approve, banners), countries, agent CSV import and signatures, branding, SharePoint connection |

## Commands

If Windows blocks the Python environment ("An Application Control policy has blocked this file"),
run the checks in Linux instead: `make check-docker` builds `docker/Dockerfile.dev` and runs ruff,
mypy and pytest there (testcontainers work through the Docker socket).

| Command | What it does |
|---|---|
| `make up` / `make down` / `make logs` | Start / stop / tail the Compose stack |
| `make migrate` | Create DB roles and run migrations (inside Compose) |
| `make seed` · `make seed-local` | Demo tenants (idempotent: existing tenants are left untouched) |
| `make run` | Run the API locally with reload against `.env` |
| `make test` | Full pytest suite (real Postgres + Redis via testcontainers, mock LLM) |
| `make check` | ruff + mypy + pytest — must pass before every commit |
| `make coverage` · `make audit` | Coverage report · `pip-audit` dependency scan |
| `make eval` | AI evaluation set against a real LLM (needs a key) |
| `make purge` | Run the data-retention purge now (it also runs daily) |
| `make web-install` · `make web-dev` · `make web-test` | Console: install, dev server on :5173, typecheck + tests + build |
| `make web-types` | Regenerate the console's API types from the backend's OpenAPI |

## Operations

- **Kill switch:** `PATCH /api/v1/settings/sending {"sending_paused": true}` defers every email of the
  tenant; `PATCH /api/v1/offices/{id} {"sending_paused": true}` pauses one office. Resuming sends deferred
  emails in each recipient's next window.
- **Rate limits:** AI endpoints allow `AI_RATE_LIMIT_PER_TENANT_PER_MINUTE` (300) per tenant and
  `AI_RATE_LIMIT_PER_CALLER_PER_MINUTE` (60) per user or API key; excess requests get 429 + `Retry-After`.
- **Retention:** email bodies, AI outputs and review payloads older than `DATA_RETENTION_DAYS` (365) are
  purged daily; the rows stay so history, caps and reporting keep working.
- **Sending:** `SENDING_PROVIDER=dry_run` (the default everywhere) writes every email to the `outbox`
  table instead of sending it. `SENDING_PROVIDER=smtp` sends through each agent's own mailbox:
  connect it under Agents → Mailbox (Microsoft 365, Google Workspace, Zoho or any SMTP/IMAP server;
  an app password is encrypted at rest), test the login, and LaneReach queues each email in the
  outbox and a worker delivers it (retries 1, 5, 15, 60, 180 minutes, then `failed`). Replies and
  bounces are read from the same mailbox over IMAP every minute (read-only: they stay unread) and
  go through the normal reply pipeline. Agents without a verified mailbox are held by the
  `mailbox_connected` guardrail.

## Architecture in brief

- **Multi-tenancy:** every table has `tenant_id` and an RLS policy. The API connects as a role that owns no
  tables and cannot bypass RLS; each request's transaction sets `app.tenant_id`. Another tenant's resource
  is invisible, so it returns 404. See [ADR 0002](docs/adr/0002-tenant-isolation-rls.md).
- **Auth:** OIDC JWTs (Entra first) validated via each tenant's registered JWKS, dev tokens in dev only,
  hashed and scoped tenant API keys, HMAC-signed webhooks with replay protection. Endpoints check scopes.
  See [ADR 0001](docs/adr/0001-authentication.md).
- **Guardrails live in the engine:** the LLM never decides whether an email may be sent; all email leaves
  through `SendingProvider` after `evaluate_send_guardrails()`.
- **Layers:** thin routers → `service.py` business logic → `repository.py` data access. Time comes from an
  injected `Clock`. Errors are RFC 7807 `application/problem+json`; logs are JSON with `request_id` and
  `tenant_id`, with email addresses masked and secrets redacted.

## Layout

```
src/lanereach/   core/ auth/ tenants/ health/ workers/ main.py cli.py
                 ai/          llm/ prompts/ rules/ tasks/ runs/ pipeline.py policy.py review/ policies/
                 automation/  nodes/ workflows/ engine/ guardrails/ enrollments/ events/ conditions.py
                 domain/      offices senders companies contacts messages suppressions leads templates calendars
                 integrations/ sending/ notifications/
                 maintenance/ (retention)   seed/ (demo dataset + loader)
web/             console: src/{api,auth,components,forms,workflows,pages} Dockerfile nginx.conf
alembic/         migrations (run as the owner role)
tests/           unit/ integration/ scenarios/ (spec §10) factories.py engine_support.py reply_support.py
evals/ docker/ http/ docs/
```
