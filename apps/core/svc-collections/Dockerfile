FROM rust:1.82-bookworm AS build
WORKDIR /source
COPY libs-rust/tb-adapter ./libs-rust/tb-adapter
COPY libs-rust/collection-adapters ./libs-rust/collection-adapters
COPY apps/core/svc-collections ./apps/core/svc-collections
WORKDIR /source/apps/core/svc-collections
# The TigerBeetle 0.16 client's build script downloads Zig from pkg.machengine.org, which now redirects to a mirror
# serving different files (the script's SHA-256 check rightly refuses them). This wrapper fetches the same archive
# from ziglang.org, which matches the pinned checksum; the check itself is unchanged.
COPY libs-rust/tb-adapter/zig-curl.sh /usr/local/bin/curl
# The client generates its C bindings with bindgen, which needs libclang.
RUN apt-get update && apt-get install -y --no-install-recommends libclang-dev && rm -rf /var/lib/apt/lists/*
RUN cargo build --locked --release

FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /source/apps/core/svc-collections/target/release/volumeos-svc-collections /usr/local/bin/svc-collections
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/svc-collections"]
