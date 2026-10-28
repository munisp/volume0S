FROM rust:1.85-bookworm AS test
RUN apt-get update && apt-get install -y --no-install-recommends protobuf-compiler cmake && rm -rf /var/lib/apt/lists/*
WORKDIR /source/apps/core/svc-query
COPY apps/core/svc-query ./
RUN cargo test --locked && cargo build --locked --release && touch /tests-passed

FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=test /source/apps/core/svc-query/target/release/volumeos-svc-query /usr/local/bin/svc-query
COPY --from=test /tests-passed /.tests-passed
USER 65532:65532
EXPOSE 8080 8081
ENTRYPOINT ["/usr/local/bin/svc-query"]
