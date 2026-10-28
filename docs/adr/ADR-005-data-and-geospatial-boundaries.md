# ADR-005: Data and geospatial engine boundaries

Status: Accepted for the M3 source slice; deployed-engine acceptance pending.

## Decision

The lakehouse uses S3-compatible object storage, Parquet files, and Delta transactional tables. Bronze is append-only schema-as-arrived evidence; silver contains conformed, deduplicated, PII-masked and geocoded facts/dimensions; gold contains tenant-scoped serving marts. Corrections are forward records, never bronze mutation. Failed quality gates block gold publication.

Engine ownership is exclusive:

- Flink: streaming usage rollups, SLA/cost watches, and ledger triggers.
- Spark: batch bronze→silver→gold ETL and table maintenance.
- Sedona: spatial joins, H3 aggregation, isochrones, and geospatial analytics.
- Ray: model training, territory-weight refinement, and batch scoring.
- DataFusion: low-latency tenant-scoped gold queries.
- OpenSearch: logs and report search, not authoritative business state.

Event consumers deduplicate by `event_id`. H3 resolution 9 is used for neighborhood scoring and resolution 7 for corridor reporting. Model/weight versions require human review before CRM synchronization. Field census uses a durable device outbox and resumable Fluvio→Kafka synchronization. Media encryption keys are scoped per tenant.

## Consequences

- A gold API must enforce tenant scope independently of caller-supplied filters.
- Late data can produce forward silver/gold corrections but cannot silently rewrite a closed ledger charge.
- Engine health and data-quality evidence are separate from source/schema tests.
- Model output is advisory until the exact version receives human approval.
