# M1 production image delivery

This runbook builds and publishes the six M1 runtime images: `svc-tenancy:0.2.0`, `svc-provisioning:0.1.0`, `svc-orchestrator:0.3.0`, `campaign-worker:0.2.0`, `studio-worker:0.2.0`, and `synthetic-probe:0.1.0`. It does not declare staging acceptance. Local source tags, compatibility-built images, and local image digests are not evidence of GHCR publication.

## Prerequisites

- A standard Linux container builder with multi-UID/GID support. Do not use a single-UID compatibility environment for production artifacts.
- Podman and skopeo on `PATH`.
- A clean Git checkout whose `HEAD` is the reviewed full commit SHA.
- An existing authorized GHCR session for `ghcr.io/munisp`. The tooling accepts no registry credential argument and writes no credential to its reports.
- Network access to the reviewed base-image and package registries.

Run the repository test gate before delivery:

```bash
make test
```

The Python Dockerfiles also make their test stages mandatory dependencies of the runtime images. A failed pytest run prevents the corresponding runtime image from being built; test source and pytest are not copied into the final image.

## Build and smoke-test

Use a new output directory for each reviewed revision:

```bash
export SOURCE_REVISION='<full-40-character-reviewed-commit>'
export IMAGE_RUN_DIR="$HOME/volumeos-evidence/images-${SOURCE_REVISION:0:12}"

python3 scripts/m1_image_delivery.py build \
  --source-revision "$SOURCE_REVISION" \
  --output "$IMAGE_RUN_DIR"

python3 scripts/m1_image_delivery.py smoke \
  --source-revision "$SOURCE_REVISION" \
  --output "$IMAGE_RUN_DIR"
```

The build uses the unmodified Dockerfiles with no cache and pulls current reviewed base tags. Every image carries the reviewed commit as its `org.opencontainers.image.revision` OCI label. The smoke command runs as the image-declared nonroot user with a read-only root filesystem, dropped capabilities, no-new-privileges, and a PID limit. It checks Core health/metrics, the orchestrator ASGI health endpoint with synthetic startup-only configuration, campaign/Studio metrics imports and scrape endpoints, and fail-closed startup for provisioning and the synthetic probe. The orchestrator smoke check is not evidence of a working OIDC provider, Temporal connection, object store, or tenant workflow.

The smoke scope is deliberately limited. It does not replace PostgreSQL/RLS, Temporal execution, real provider, OIDC, GPU/ComfyUI, or staging acceptance evidence.

## Publish and verify immutable references

Publication fails unless both reports match the reviewed revision, smoke passed, the Git tree is still clean, and every local image ID and runtime user is unchanged since the build:

```bash
python3 scripts/m1_image_delivery.py publish \
  --source-revision "$SOURCE_REVISION" \
  --output "$IMAGE_RUN_DIR"
```

After each push, the tool independently retrieves the registry digest with skopeo. A partial failure is recorded as failed; it is not reported as complete publication.

Before staging deployment, retrieve and compare every registry digest again:

```bash
python3 scripts/m1_image_delivery.py verify \
  --source-revision "$SOURCE_REVISION" \
  --output "$IMAGE_RUN_DIR"
```

`images.json` is the handoff containing the six immutable `sha256:` references. `verification.json` records the subsequent comparison. Keep both with the reviewed source revision. Tags remain mutable operational aliases; staging should deploy the verified digests through its authorized GitOps change process.

Only after the images are deployed and their runtime identities are confirmed should the operator continue with `docs/m1/STAGING_ACCEPTANCE.md`.
