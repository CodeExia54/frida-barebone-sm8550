#!/usr/bin/env bash
# Download the SDK + GumJS devkit artifacts from the latest successful
# build-sdk-devkit-generic.yml run on GitHub Actions - the heavy,
# arch-only from-source build that workflow does on GitHub's runners
# instead of locally. Run this once (or after re-running that workflow)
# before build-ko.sh / build-all.sh.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HERE/out"

RUN_ID=$(gh run list --workflow=build-sdk-devkit-generic.yml --status=success --limit=1 --json databaseId --jq '.[0].databaseId')
if [ -z "$RUN_ID" ] || [ "$RUN_ID" = "null" ]; then
  echo "FATAL: no successful build-sdk-devkit-generic.yml run found." >&2
  echo "Trigger one first: gh workflow run build-sdk-devkit-generic.yml" >&2
  exit 1
fi
echo "=== fetching artifacts from run $RUN_ID ==="

TMP="$(mktemp -d)"
gh run download "$RUN_ID" -n sdk-none-arm64-softfloat -D "$TMP/sdk"
gh run download "$RUN_ID" -n gumjs-devkit-none-arm64-softfloat -D "$TMP/devkit"

cp "$TMP/sdk/sdk-none-arm64-softfloat.tar.xz" "$HERE/out/sdk-none-arm64-softfloat.tar.xz"
mkdir -p "$HERE/out/gumjs-devkit"
cp -r "$TMP/devkit"/* "$HERE/out/gumjs-devkit/"
rm -rf "$TMP"

echo "=== ready: out/sdk-none-arm64-softfloat.tar.xz, out/gumjs-devkit/ ==="
ls -la "$HERE/out/gumjs-devkit"
