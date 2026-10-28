# ADR-004: Money and prepaid boundaries

Status: Accepted for the M2 source slice; live-ledger acceptance pending.

## Decision

TigerBeetle is the sole financial balance store. `svc-billing` exposes only tenant-scoped account, balance, reserve/post/void, usage, FX, and ledger-report operations over the official client transport. PostgreSQL stores collection expectations/receipts, document metadata, workflow metadata, and report manifests, never available or derived ledger balances.

All service charges use deterministic pending transfers followed by exactly one post or void. Funding is posted only after provider confirmation. Confirmed but unmatched/mismatched receipts enter provider suspense; an authenticated platform review moves the original value once to a reviewed tenant. Payment-provider webhooks authenticate delivery but never directly grant value.

Temporal owns monthly billing, dunning, usage-week collection, and FX-close state. Flink produces usage rollups; it does not decide ledger settlement. Billing documents use integer minor units, sequential numbers, TIN and structured party/line/tax fields, immutable object hashes, and provider-accepted WhatsApp delivery records.

## Consequences

- A lost response is resolved by deterministic lookup/replay, never by creating a parallel charge.
- Insufficient funds and expired reservations fail closed; no relational or in-memory production fallback is allowed.
- Provider cost and tenant usage recovery are separate journal entries.
- Mojaloop remains disabled and outside the v1 wallet/ledger architecture.
- Local transport doubles establish semantics only; clustered TigerBeetle and provider evidence remain mandatory.
