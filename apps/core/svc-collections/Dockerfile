FROM rust:1.82-bookworm AS build
WORKDIR /source
COPY libs-rust/tb-adapter ./libs-rust/tb-adapter
COPY libs-rust/collection-adapters ./libs-rust/collection-adapters
COPY apps/core/svc-collections ./apps/core/svc-collections
WORKDIR /source/apps/core/svc-collections
RUN cargo build --locked --release

FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /source/apps/core/svc-collections/target/release/volumeos-svc-collections /usr/local/bin/svc-collections
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/svc-collections"]
