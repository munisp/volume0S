# VolumeOS roadmap requirement inventory

Status date: 2026-10-28. This matrix governs the sequential M2 → M3 → M4 → native-application source programme. “Source implemented” means reviewed code and local/CI tests exist; it never means a live cluster, provider, registry image, device build, or staging acceptance has passed. M0/M1 operational acceptance remains a separate blocking gate.

## Normative inputs and boundaries

- `unified-platform-spec.pdf`: eight offers, prepaid money semantics, architecture, security, NFRs, and sequencing.
- `volumeos-data-architecture (1).pdf`: ingestion, medallion tables, quality gates, engine ownership, tenant ACLs, and geospatial processing.
- `VolumeOS___Unified_Open-Source_Platform_Architecture___Geospatial_Analytics_20261026204125.pdf`: milestone outcomes and geospatial architecture.
- `nigeria-volume-playbook (1).pdf` and `q1-launch-kit.pdf`: service-line operations, onboarding, human review, and commercial context.
- `m0-deployment-runbook (1).pdf`: cluster and operational prerequisites.

The unified PDF ends at architecture section 5.8 even though its contents page lists later sections. Requirements are not inferred from unavailable pages. Mojaloop is Phase 3: it remains feature-flagged OFF in v1, is not the internal wallet/ledger, and requires a separate licensing review. VolumeOS connects to payment rails; it does not become a payment service organization or operate a Mojaloop hub in v1.

## M2 — money, billing, and prepaid operations

| ID | Required capability | Source/verification gate | Operational evidence still required |
|---|---|---|---|
| M2-LED-01 | TigerBeetle is the only balance store; no derived balances in PostgreSQL; NGN minor units and u128 amounts. | Account taxonomy, live official-client transport, invariant and migration tests. | Real clustered TigerBeetle deployment, failover, backup/restore, and reconciliation evidence. |
| M2-LED-02 | Two-phase reserve → post or void for every charge; deterministic idempotency; expiry voids; no partial charge. | Adapter and service tests cover replay conflicts, overdraw, expiry, post/void, and u128 values. | Failure-injection test against the live ledger and workers. |
| M2-COL-01 | Paystack, Flutterwave, and Moniepoint signed webhooks with replay protection and independent settlement confirmation. | Raw-body signature verification, provider confirmation, exact amount/currency/reference matching, and replay tests. | Provider sandbox/webhook deliveries and independent dashboard/API reconciliation. |
| M2-COL-02 | Idempotent tenant credit; unmatched or mismatched payments enter suspense and require reviewed reconciliation. | Funding and suspense-resolution invariants; tenant-scoped receipts; immutable review records. | Live unmatched-payment drill and operator approval evidence. |
| M2-BIL-01 | Monthly cycles issue invoices, reserve/post retainers, and bill closed usage rollups. | Temporal workflow tests, closed-week aggregation, late-data conflict tests, and exact minor-unit documents. | Deployed scheduler/workflow run across a real billing boundary. |
| M2-BIL-02 | Dunning reminders, automatic pause, confirmed-funding recovery, and restoration. | State-gated recovery signal, fresh optimistic tenancy transition, and pause/restore tests. | Real messaging and tenancy recovery drill. |
| M2-DOC-01 | Sequential invoice/receipt numbering, seller/buyer TIN, structured e-invoice-ready fields, immutable PDF, and WhatsApp delivery. | Document schema/PDF tests, hash-chained exports, provider-accepted delivery tests; acceptance is not called device delivery. | FIRS/e-invoice review if applicable, object retention, and real WhatsApp delivery evidence. |
| M2-REP-01 | Trial balance, revenue by offer, AR aging, partner-share statements, immutable exports, and FX revaluation. | Balanced-ledger queries, tenant scoping, exact arithmetic, export digest verification, and FX tests. | Accountant review and month-close evidence. |
| M2-OPS-01 | Money libraries reach at least 80% coverage and all runtime wiring fails closed. | Coverage reports plus repository, API, workflow, and manifest tests. | Image builds, deployment, SLOs, restore, and staging acceptance. |

### M2 source verification — passed 2026-10-28

The M2 source gate is green; operational acceptance remains open. Exact local evidence:

- `scripts/verify_repo.py`: passed with 63 YAML files, 9 JSON files, 16 M1 acceptance checks, 18 pinned Argo applications, and 8 Kafka topics. Repository script suite: 14 passed.
- `libs-rust/tb-adapter`: 27 tests passed (8 unit and 19 integration). `cargo llvm-cov`: 82.86% total line coverage; `ledger.rs` 87.29%, `lib.rs` 96.39%, and `tigerbeetle.rs` 69.00%. The official-client transport was conversion/configuration tested, not connected to a live cluster.
- `libs-rust/collection-adapters`: 22 tests passed (4 unit, 11 collection, and 7 provider HTTP). Package-own line coverage is 84.34% combined: `processor.rs` 85.50% and `providers.rs` 84.16%. Its path dependency is measured independently above rather than being presented as collection-adapter coverage.
- `apps/core/svc-billing`: 7 API tests passed. `apps/core/svc-collections`: 5 API tests passed.
- Billing worker: 10 passed. Usage rollup: 10 passed. Document service: 12 passed. Orchestrator regression: 37 passed. The two Python service suites each emitted one Starlette deprecation warning.
- Closed-day collection, deterministic ISO-week aggregation, exact provider-cost pairing, fail-closed response validation, immutable week-posting manifests, paginated account snapshots, and full-u128 conversion are covered by these tests.

No real TigerBeetle cluster, payment-provider delivery, FIRS review, WhatsApp delivery, month close, production image, or staging result is implied by this source gate.

## M3 — lakehouse, analytics, lead, and geospatial intelligence

| ID | Required capability | Source/verification gate | Operational evidence still required |
|---|---|---|---|
| M3-ING-01 | Kafka/Fluvio/CDC ingestion with event-ID deduplication and schema-as-arrived immutable bronze. | Versioned envelopes, idempotent consumers, Parquet/Delta append tests, and replay tests. | Real Kafka→object-storage throughput and recovery evidence. |
| M3-MED-01 | Bronze, silver, gold table catalog and Spark batch ETL/maintenance. | Every specified `br_*`, `sl_*`, and `gt_*` table has a schema/owner; bronze is never updated in place. | Spark jobs run against production-shaped Delta tables. |
| M3-MED-02 | Silver conforms/deduplicates, masks PII, and geocodes; gold is tenant-scoped and quality-gated. | PII/redaction tests, geocode validation, tenant ACL tests, and failed-gate publication blocking. | Data-quality and cross-tenant isolation evidence on live data. |
| M3-ENG-01 | Non-overlapping engines: Flink streaming rollups/watches/triggers; Spark ETL; Sedona spatial; Ray training/scoring; DataFusion gold queries; OpenSearch logs/report search. | Job ownership and API boundaries are statically and contract tested. | Each engine’s deployed job health, scaling, and recovery evidence. |
| M3-GEO-01 | H3 resolution 9 neighborhood scoring, resolution 7 corridors, spatial joins, isochrones, density, silence, value, and competition. | Deterministic geospatial fixtures, weight versioning, and geometry/index validation. | Accuracy review against sampled Nigerian field data. |
| M3-AI-01 | Versioned territory weights/models with human review before CRM synchronization. | Model registry, signed review decision, stale-version rejection, and no automatic CRM publication. | Reviewed model promotion and monitored scoring run. |
| M3-FLD-01 | Offline-tolerant, geotagged, resumable field census through Fluvio to Kafka. | Outbox, chunk digest, resume, duplicate suppression, and tenant/device authorization tests. | Poor-network device-to-cluster synchronization evidence. |
| M3-MED-03 | Per-tenant media encryption keys. | Envelope-encryption contract, key scoping/rotation tests, no plaintext key persistence. | KMS/Vault deployment and key-rotation/restore drill. |

## M4 — privacy, hardening, and open core

| ID | Required capability | Source/verification gate | Operational evidence still required |
|---|---|---|---|
| M4-NDPR-01 | Consent/lawful-basis registry and auditable changes. | Versioned purpose, subject digest, tenant scope, and append-only audit tests. | Legal/privacy-owner review and deployed retention policy. |
| M4-NDPR-02 | DSAR export/delete with durable status and tenant-safe erasure propagation. | Identity verification boundary, export manifest/digest, deletion tombstones, replay/idempotency tests. | End-to-end DSAR drill across live stores and backups. |
| M4-AI-01 | Nigerian name, phone, and address redaction before every external AI call. | Shared redaction gateway tests and provider-boundary deny tests; raw PII never appears in logs. | Provider configuration and sampled live egress inspection. |
| M4-SEC-01 | Wazuh/OpenCTI/OpenSearch security operations with MITRE-mapped detections. | Reviewed rules, private-secret overlay, and purple-team test cases. | Real alert-to-case evidence; Wazuh is not active before secret replacement. |
| M4-OPS-01 | Quarterly restore drills and explicit SLO/error-budget operations. | Drill runbooks and evidence validators; no fabricated passing records. | Timed PostgreSQL, MinIO, ledger, and lakehouse restore evidence. |
| M4-OSS-01 | Public open-core repositories and external-operator deployment path. | License/credit inventory, sanitized examples, deployment documentation, and clean-checkout verification. | Independent external-operator bring-up. |

## Native iOS and Android field applications

| ID | Required capability | Source/verification gate | Device/release evidence still required |
|---|---|---|---|
| APP-ID-01 | Native OIDC Authorization Code + PKCE using the existing tenant-scoped APIs. | System browser/custom-tab flow, state/nonce/PKCE validation, secure token storage, no client secret. | Real Keycloak login/MFA on both platforms. |
| APP-FLD-01 | Census capture with validation, geotagging, consent/lawful-basis reference, and operator review metadata. | Shared API schema, coordinate/accuracy bounds, tenant binding, and unit/UI tests. | GPS permission and field-accuracy checks on physical devices. |
| APP-OFF-01 | Cheap-Android-friendly offline outbox and intermittent-connectivity behavior. | Encrypted local queue, retry/backoff, duplicate-safe event IDs, bounded storage, and offline UI tests. | Low-memory Android and airplane-mode soak test. |
| APP-SYN-01 | Resumable Fluvio synchronization with chunk hashes and server resume tokens. | Interrupted-upload/resume/corruption/replay tests against the real contract. | Device → Fluvio → Kafka → bronze evidence. |
| APP-REL-01 | Reproducible native projects, nonroot secret-free source, release checklist, and accessible UX. | Static analysis/unit tests and build configuration; no signing material committed. | Signed iOS/Android release builds and store review remain operator tasks. |

## Remaining offer vertical slices

| ID | Required capability | Source/verification gate | Operational evidence still required |
|---|---|---|---|
| OFF-03 | Call transcription, rubric scoring, reviewer-bound scorecard, and controlled release. | WhisperX boundary, source digest, rubric/version validation, prepaid workflow, exact-artifact approval, and worker tests. | Real audio/provider execution and reviewed client delivery. |
| OFF-04 | Signed WhatsApp intake, redacted AI drafting, human-approved replies, bookings, consent, and suppression. | 7 conversation API/domain tests plus PostgreSQL/RLS, replay, tenant/channel, exact-draft, and provider source gates. | WhatsApp, AI, booking, database, and staging delivery evidence. |
| OFF-05 | Document parsing, accounting export, reviewer-bound release, and prepaid settlement. | Docling boundary, CSV content digest, export validation, and workflow tests. | Real documents, accounting-target review, and delivery evidence. |
| OFF-07 | Allowlisted registry evidence, compliance paperwork, and controlled release. | Registry/browser boundary, evidence and licence validation, artifact review, and workflow tests. | Live registry access and compliance-owner acceptance. |
| OFF-08 | Redacted-AI funding draft, versioned finance rendering, two-analyst approval, and controlled release. | Distinct-reviewer and exact-digest workflow tests, funding/template validation, and budget gates. | Model/render provider execution and analyst/funder acceptance. |
| OFF-OPS | Tenant-scoped job APIs, catalog entries, deployment wiring, and responsive operator controls without invented prices. | 39 orchestrator tests, 11 offer-worker tests, console production/PWA build and lint, desktop/mobile browser checks, and repository overlay gates. | Published images, deployed OIDC/CORS, live Temporal/object/ledger execution, and commercial pricing review. |

See `docs/OFFER_OPERATIONS.md` for routes, safety invariants, configuration, and the acceptance boundary.

## Milestone completion rule

A roadmap item is complete only when its source gate is green and its requirement status is updated with the exact tests run. Operational acceptance remains open until real evidence exists. Source completion must not be reported as 100% business/platform completion, and no illustrative data may be presented as persisted tenant state or provider success.
