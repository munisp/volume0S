# ADR-006: Privacy and native field applications

Status: Accepted for M4/native source slices; legal, cluster, and device acceptance pending.

## Decision

NDPR capabilities use a tenant-scoped consent/lawful-basis registry, durable DSAR workflows, export manifests, deletion propagation records, and append-only audit evidence. External AI providers are reachable only through a redaction boundary covering Nigerian names, E.164/local phone forms, and addresses; provider prompts and logs must not contain raw subject PII.

Native iOS and Android applications use Authorization Code with PKCE through the system browser, secure platform token storage, and the same tenant-scoped API contracts as the web console. They contain no client secret. Census records carry consent/lawful-basis references, tenant/device identity, geotag accuracy, and deterministic event IDs.

Offline capture writes to an encrypted, bounded outbox before network acknowledgement. Uploads use content-addressed chunks, digest verification, resumable server offsets/tokens, backoff, and idempotent Fluvio event publication. UI state distinguishes local-only, uploading, acknowledged, and failed records.

## Consequences

- A cached application shell or local record is not server persistence or successful synchronization.
- DSAR deletion does not fabricate immediate backup erasure; backup retention and restoration handling require explicit operational evidence.
- Security operations remain disabled until private Wazuh secrets/certificates replace upstream bootstrap inputs.
- Signed mobile release artifacts, physical-device tests, and store approval are separate delivery gates.
