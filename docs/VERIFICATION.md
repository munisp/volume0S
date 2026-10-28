# Source verification record

Run date: 2026-10-28. This record separates checks actually executed in the delivery environment from acceptance that requires unavailable infrastructure or toolchains.

## Passed in this run

### Repository and static gates

- `scripts/verify_repo.py`: passed — 84 YAML files, 14 JSON files, 16 M1 acceptance checks, 18 pinned Argo applications, and 8 Kafka topics. The gate includes the new conversation service, offer worker, deployment overlays, exact-artifact/draft controls, RLS migrations, console runtime configuration, and test-required runtime images.
- `scripts/verify_m3.py`: passed — 19 medallion tables, engine ownership, tenant query bounds, and reviewed model gates.
- `scripts/verify_m4.py`: passed — privacy/RLS, redaction, MITRE detections, restore evidence, and open-core boundaries.
- `scripts/verify_native.py`: passed — Android/iOS PKCE, secure storage, encrypted bounded outbox, and resumable census contracts.

### Automated tests

277 Python/script tests passed:

| Suite | Passed |
|---|---:|
| M1 image/staging script unit tests | 14 |
| Native contract and restore-drill tests | 6 |
| `svc-conversations` | 7 |
| `svc-orchestrator` | 39 |
| `svc-privacy` | 15 |
| `svc-leads` | 11 |
| `offer-worker` | 11 |
| `svc-census-sync` | 19 |
| `svc-documents` | 12 |
| `report-search` | 12 |
| `usage-rollup` | 10 |
| `billing-worker` | 10 |
| `campaign-worker` | 48 |
| `studio-worker` | 20 |
| `synthetic-probe` | 9 |
| Lakehouse | 12 |
| Geospatial | 5 |
| Media security | 9 |
| Ray territory jobs | 4 |
| Streaming watches | 4 |

The API suites emitted dependency deprecation warnings; no warning was treated as a passing functional test or hidden as a failure.

### Console and browser

- TypeScript production compilation and Vite build passed.
- PWA inject-manifest service-worker build passed with 25 precached entries.
- ESLint passed.
- `npm audit --omit=dev --audit-level=low` passed with zero production-dependency findings.
- Desktop and 390px mobile views were opened from the production bundle and visually checked. Catalog, unpriced offers, live-only operations, empty-state boundaries, and six-item mobile navigation were checked. A five-column mobile navigation defect was corrected and the build/browser check rerun.

The full development dependency audit reports four low-severity upstream advisories in the PWA build chain. The suggested forced downgrade is breaking and was not applied. This is not represented as a clean full development audit.

## Not executed in this environment

- Go formatting, vetting, tests, and builds: Go toolchain unavailable.
- Rust formatting, tests, coverage, and builds: Rust toolchain unavailable in this run; earlier source results remain documented in the roadmap rather than being re-claimed as new evidence.
- Kubernetes/Kustomize rendering and cluster acceptance: `kubectl` and a cluster unavailable.
- Container builds, nonroot smoke, registry publication, and immutable remote digest verification: Docker/registry infrastructure unavailable.
- Android/iOS compilation, signing, simulator, and physical-device tests: native toolchains unavailable.
- Real Temporal, TigerBeetle, PostgreSQL/RLS, Kafka/Fluvio, object-store, analytics-engine, WhatsApp, AI, booking, payment, registry, and external-provider acceptance.

No statement in this record asserts that those unexecuted checks passed. Source completion and operational acceptance remain separate.

## Reproduction

Install the toolchains and each package's test dependencies, then use `make test`, `make test-offer-operations`, or the package-specific commands in `README.md`. The current sandbox used a project-local Python dependency directory because the global Python installation did not include pytest. The console was also built on a filesystem supporting npm executable links; the persistent source mount rejected npm symlinks with `ENOTSUP`. This was an environment limitation, not an application compilation failure.
