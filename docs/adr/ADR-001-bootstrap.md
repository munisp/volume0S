# ADR-001: M0 bootstrap and upstream packaging

- Status: Accepted
- Date: 2026-10-27

## Context

M0 must be reproducible by a small platform team. The initial scaffold used floating `HEAD` revisions, non-resolvable chart hosts, and an Argo CD namespace that differed from its Application resources.

## Decision

Use Argo CD in `argocd`; pin every upstream chart version or Git commit; consume Bitnami charts through their OCI registry; use the combined open-appsec/APISIX chart; deploy Strimzi custom resources for Kafka and topics. Keep unsupported ListMonk, Postiz, and Wazuh packaging out of the active app-of-apps until reviewed.

## Consequences

Deployments are reproducible and auditable. Sync waves provide ordering, not proof of health. Every environment still needs secrets, DNS/TLS, Keycloak policy, and cluster acceptance. No fake local chart repository is acceptable.

## Revisit

Revisit when an upstream changes its supported distribution channel or when M1 packaging is accepted through licence and security review.
