# VolumeOS edge SmartModules (Rust)

Fluvio SmartModules run at the edge before events hit Kafka (spec §FR-STR-3):
- `normalize` — harmonize provider webhook payloads to `proto/volumeos/v1` schemas
- `redact` — strip/mask PII at the edge (defense in depth for NDPR, FR-AI-8)
- `geotag` — attach lat/lon/LGA from lookup tables for census pings

Build with the SMDK (`cargo build --release` via fluvio-smartmodule crate) and register
per-connector. TODO: implement transforms with property tests; wasm output committed by CI.
