# ADR-002: Fail-closed tenancy isolation

- Status: Accepted
- Date: 2026-10-27

## Context

P5/TEN-4 require tenant scope in every row and request. A trusted HTTP header alone is not authentication, and a super-operator token must not silently become a cross-tenant database wildcard.

## Decision

Require a validated JWT with issuer, audience, expiry, RS256 signature, `tenant_id`, and roles. Scope every store operation by that claim. Add PostgreSQL RLS as defence in depth. Header claims exist only when `ALLOW_INSECURE_HEADER_AUTH=true` outside prod. Tenant creation uses the target tenant claim; lifecycle changes use optimistic versions.

## Consequences

Cross-tenant access fails closed. Internal onboarding must deliberately obtain or exchange a token for the target tenant rather than bypass scope. Development auth can never be enabled in prod.

## Revisit

Introduce a separate, audited control-plane impersonation flow only when onboarding automation requires it.
