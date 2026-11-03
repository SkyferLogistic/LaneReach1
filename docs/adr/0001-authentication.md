# ADR 0001 — Authentication: external OIDC, dev tokens, tenant API keys

**Status:** accepted (M1, 2026-09-28)

## Context
LaneReach serves many customer tenants (freight forwarders). Enterprise customers expect SSO and MFA,
and we do not want to own password storage. Integrations (CRM sync, sending providers) need
machine-to-machine access. The API must be testable locally without an identity provider.

## Decision
- **Humans** authenticate with RS256 JWTs from an external OIDC IdP (Microsoft Entra ID first). Each tenant
  registers `(issuer, external tenant/org id, jwks_uri)` in `tenant_identity_providers`. The validator reads
  the unverified `iss`/`tid` only to find the registration; unregistered issuers are rejected before any
  network call, and the JWKS URI comes from the registration, never from the token (prevents SSRF).
  Only RS256 is accepted (no algorithm confusion). `exp`, `nbf`, `iat`, `iss`, `aud`, `sub` are required and
  checked against the injected `Clock` with a 30 s leeway. JWKS are cached (TTL 1 h) and refreshed on an
  unknown `kid` at most once per minute.
- **Local/tests** use `AUTH_MODE=dev`: `POST /auth/dev-token` mints HS256 tokens for any tenant/role.
  The route is only mounted in dev mode and the app refuses to start with dev mode in production.
- **Machines** use tenant API keys `lr_live_…` / `lr_test_…` (256-bit random). Only a SHA-256 hash and a
  16-character lookup prefix are stored; the key is shown once. Keys carry explicit scopes, can be revoked
  and expire; a key can only be created with scopes the creator holds.
- Endpoints authorize by **scope**; roles expand to default scope sets in `auth/roles.py`.

## Consequences
- No password handling in LaneReach. Onboarding a tenant requires registering its IdP.
- Role changes take effect at the next token issuance (tokens are not introspected).
- Pre-authentication lookups need cross-tenant reads, handled by ADR 0002's `SECURITY DEFINER` functions.
