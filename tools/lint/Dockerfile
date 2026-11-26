# Linux image for the local rustfmt and clippy check (scripts/lint.sh).
# MODULE.bazel resolves crates for x86_64-unknown-linux-gnu only, so on a
# Windows or macOS host Bazel skips every Rust target. This image runs the
# same Bazel, toolchain and aspects as the BuildBuddy workflow.
FROM ubuntu:24.04

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl git python3 \
      libasound2-dev libogg-dev pkg-config \
 && rm -rf /var/lib/apt/lists/*

# bazelisk reads .bazelversion in the workspace.
RUN curl -sSL -o /usr/local/bin/bazel \
      https://github.com/bazelbuild/bazelisk/releases/download/v1.25.0/bazelisk-linux-amd64 \
 && chmod +x /usr/local/bin/bazel

# Run as the image's uid-1000 user, not root (rules_python's hermetic
# interpreter refuses root).
USER ubuntu
# Created here so the cache volume mounted on it belongs to ubuntu.
RUN mkdir -p /home/ubuntu/.cache
WORKDIR /src
