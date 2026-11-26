#!/usr/bin/env bash
# Local rustfmt and clippy check with Bazel, the same check CI runs.
#
#   scripts/lint.sh         check: rustfmt and clippy on every Rust target
#   scripts/lint.sh fmt     format the tree in place with Bazel's rustfmt
#   scripts/lint.sh -- ARGS run "bazel ARGS" in the lint container
#
# On Linux with Bazel installed it runs Bazel directly. Elsewhere (Windows,
# macOS) it runs Bazel in a Linux container (tools/lint/Dockerfile), because
# MODULE.bazel resolves crates for x86_64-unknown-linux-gnu only and a
# Windows host skips every Rust target. The container keeps its Bazel output
# base and caches in Docker volumes, so only the first run is slow.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

IMAGE=electric-sunflowers-lint
CHECK=(build --keep_going --output_groups=rustfmt_checks,clippy_checks //...)
FMT=(run @rules_rust//tools/rustfmt)

case "${1:-check}" in
    check) ARGS=("${CHECK[@]}") ;;
    fmt) ARGS=("${FMT[@]}") ;;
    --) shift; ARGS=("$@") ;;
    *) echo "usage: scripts/lint.sh [check|fmt|-- BAZEL_ARGS...]" >&2; exit 2 ;;
esac

# BuildBuddy: .bazelrc.local (see .bazelrc.local.example) is in the mounted
# tree, so its key and --config=buildbuddy apply in the container too. A
# BUILDBUDDY_API_KEY in the environment is used when there is no such file.
REMOTE=()
if [ -n "${BUILDBUDDY_API_KEY:-}" ] \
    && ! grep -qs 'x-buildbuddy-api-key=' .bazelrc.local; then
    REMOTE=(--config=buildbuddy "--remote_header=x-buildbuddy-api-key=$BUILDBUDDY_API_KEY")
fi

if [ "$(uname -s)" = Linux ] && command -v bazel >/dev/null; then
    exec bazel "${ARGS[0]}" "${REMOTE[@]}" "${ARGS[@]:1}"
fi

command -v docker >/dev/null || { echo "lint: needs Docker on this host" >&2; exit 1; }

if ! docker image inspect "$IMAGE" >/dev/null 2>&1 \
    || [ "${LINT_REBUILD:-0}" = 1 ]; then
    docker build -t "$IMAGE" tools/lint
fi

# Git Bash on Windows rewrites /paths in arguments; stop it, and give Docker
# the Windows form of the repo path.
export MSYS_NO_PATHCONV=1
HOST_DIR="$REPO_ROOT"
if command -v cygpath >/dev/null; then HOST_DIR="$(cygpath -w "$REPO_ROOT")"; fi

TTY=()
[ -t 1 ] && TTY=(-t)

# --symlink_prefix=/ : no bazel-* links in the bind-mounted tree (they would
# point into the container and break the host's view of the repo).
exec docker run --rm -i "${TTY[@]}" \
    -v "$HOST_DIR:/src" \
    -v electric-sunflowers-bazel:/home/ubuntu/.cache \
    "$IMAGE" \
    bazel "${ARGS[0]}" --symlink_prefix=/ "${REMOTE[@]}" "${ARGS[@]:1}"
