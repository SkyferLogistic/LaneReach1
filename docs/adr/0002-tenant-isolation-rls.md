# ADR 0002 — Tenant isolation with PostgreSQL Row-Level Security

**Status:** accepted (M1, 2026-09-28)

## Context
A missing `WHERE tenant_id = …` in one query would leak one forwarder's contacts or replies to another.
Isolation must not depend on every developer remembering a filter.

## Decision
- Every table has `tenant_id uuid NOT NULL` and a `tenant_isolation` policy comparing it with the
  transaction-local setting `app.tenant_id` (`tenants` compares its `id`). `NULLIF(current_setting(...), '')`
  makes an unset tenant match nothing, so unscoped queries fail closed.
- Two database roles: the **owner** owns tables and runs migrations; the **app** role (API and workers) owns
  nothing and has `NOBYPASSRLS`. Default privileges grant the app role DML on the owner's tables and
  `EXECUTE` on its functions (revoked from `PUBLIC`). `lanereach-admin bootstrap-db` sets this up idempotently.
- `open_tenant_session()` stores the tenant on the SQLAlchemy session; an `after_begin` listener runs
  `set_config('app.tenant_id', …, true)` at the start of every transaction, so the scope survives commits
  and never leaks through the connection pool. Workers do the same from the job payload.
- The only cross-tenant reads are narrow `SECURITY DEFINER` functions for lookups that happen before the
  tenant is known (API key prefix, IdP identity, tenant active). They pin `search_path` and return the
  minimum columns for active tenants only.
- Another tenant's resource returns **404**, never 403, because RLS makes it invisible.

## Consequences
- Isolation is enforced by the database and proven by integration tests (reads, updates, inserts with a
  foreign `tenant_id`, pool leakage, role privileges, every table has an enabled policy).
- `FORCE ROW LEVEL SECURITY` is deliberately not used so the owner can run migrations and the
  `SECURITY DEFINER` functions; the app role is never the owner.
- Cross-tenant background work (the M4 dispatcher) needs its own narrow function returning tenant IDs only.
