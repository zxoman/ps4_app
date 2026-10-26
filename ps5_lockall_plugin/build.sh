#!/usr/bin/env bash
# Build the PS5 plugin with the toolchain extracted under ~/.cache/ps5build.
# Override any of the roots with environment variables if yours differ.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BASE="${PS5BUILD_ROOT:-$HOME/.cache/ps5build}"

export PS5_PAYLOAD_SDK="${PS5_PAYLOAD_SDK:-$BASE/ps5-payload-sdk}"
export LLVM_ROOT="${LLVM_ROOT:-$BASE/llvm}"
export TOOLS_ROOT="${TOOLS_ROOT:-$BASE/tools}"
export LLVM_CONFIG="${LLVM_CONFIG:-$LLVM_ROOT/usr/bin/llvm-config}"

export LD_LIBRARY_PATH="$LLVM_ROOT/usr/lib64:$LLVM_ROOT/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$TOOLS_ROOT/usr/bin:$LLVM_ROOT/usr/bin:$PS5_PAYLOAD_SDK/bin:$PATH"

for p in "$PS5_PAYLOAD_SDK/toolchain/prospero.mk" "$LLVM_CONFIG" "$TOOLS_ROOT/usr/bin/make"; do
    if [ ! -e "$p" ]; then
        echo "missing: $p" >&2
        echo "See README.md (Requirements) for how to populate $BASE." >&2
        exit 1
    fi
done

cd "$HERE"
exec make "$@"
