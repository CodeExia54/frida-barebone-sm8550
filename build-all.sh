#!/usr/bin/env bash
# Orchestrator: fetch the arch-only SDK+devkit from the last successful
# build-sdk-devkit-generic.yml GitHub Actions run (the heavy from-source
# build lives there, not locally - see that workflow's own comment), then
# build frida-agent.ko locally, fast, for every already-pulled ddk KMI
# target - so each one can be rebuilt and QEMU-tested quickly without
# re-running CI.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

KMIS=(android12-5.10 android13-5.15 android14-6.1 android15-6.6 android16-6.12 android17-6.18)

if [ ! -f "$HERE/out/sdk-none-arm64-softfloat.tar.xz" ] || [ ! -d "$HERE/out/gumjs-devkit" ]; then
  "$HERE/fetch-ci-artifacts.sh"
fi

for kmi in "${KMIS[@]}"; do
  "$HERE/build-ko.sh" "$kmi"
done

echo "=== all targets built ==="
ls -la "$HERE"/out/*/frida-agent.ko
