FROM rust:1.88-bookworm AS build

WORKDIR /src
COPY packages/rust ./packages/rust
COPY examples/rust/minimal-agent ./examples/rust/minimal-agent

WORKDIR /src/examples/rust/minimal-agent
RUN cargo build --release --locked

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 appuser
USER appuser

COPY --from=build /src/examples/rust/minimal-agent/target/release/castia-rust-minimal-agent /usr/local/bin/castia-rust-minimal-agent

ENV HOST=0.0.0.0
ENV PORT=8088
EXPOSE 8088

CMD ["castia-rust-minimal-agent", "--serve"]
