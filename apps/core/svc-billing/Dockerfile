FROM rust:1.82-bookworm AS build
WORKDIR /source
COPY libs-rust/tb-adapter ./libs-rust/tb-adapter
COPY apps/core/svc-billing ./apps/core/svc-billing
WORKDIR /source/apps/core/svc-billing
RUN cargo build --locked --release

FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /source/apps/core/svc-billing/target/release/volumeos-svc-billing /usr/local/bin/svc-billing
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/svc-billing"]
