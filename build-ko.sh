#!/usr/bin/env bash
# Build frida-agent.ko for one KMI target, against the from-source SDK +
# GumJS devkit built by build-sdk-devkit-generic.yml on GitHub Actions
# (fetch-ci-artifacts.sh pulls them down first). Reuses
# frida-build-env:<kmi> (clang-19+Rust already installed) if the sibling
# frida-barebone repo already built one for this target - same cache,
# shared across both repos, since the toolchain itself is identical.
set -euo pipefail

FRIDA_VERSION="${FRIDA_VERSION:-17.17.0}"
KMI="${1:?usage: build-ko.sh <kmi-target, e.g. android12-5.10>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/out/$KMI"
mkdir -p "$OUT"
CONTAINER="frida-fork-ko-$$"

if [ ! -f "$HERE/out/sdk-none-arm64-softfloat.tar.xz" ] || [ ! -d "$HERE/out/gumjs-devkit" ]; then
  echo "FATAL: run ./fetch-ci-artifacts.sh first (or ./build-all.sh, which calls it)" >&2
  exit 1
fi

ENV_IMAGE="frida-build-env:$KMI"
if docker image inspect "$ENV_IMAGE" >/dev/null 2>&1; then
  echo "=== $KMI: reusing cached $ENV_IMAGE (clang-19/Rust already installed) ==="
  BASE_IMAGE="$ENV_IMAGE"
  SKIP_TOOLCHAIN_INSTALL=1
else
  echo "=== $KMI: using ddk:$KMI (first run for this target - will cache as $ENV_IMAGE) ==="
  BASE_IMAGE="ghcr.io/ylarod/ddk:$KMI"
  SKIP_TOOLCHAIN_INSTALL=0
fi

docker run -d --platform linux/amd64 --privileged --name "$CONTAINER" \
  -v "$HERE":/host -w /build "$BASE_IMAGE" sleep infinity
trap 'docker rm -f "$CONTAINER" >/dev/null 2>&1 || true' EXIT

docker exec -e "FRIDA_VERSION=$FRIDA_VERSION" -e "KMI=$KMI" \
  -e "SKIP_TOOLCHAIN_INSTALL=$SKIP_TOOLCHAIN_INSTALL" "$CONTAINER" bash -c '
  set -euo pipefail
  if [ "$SKIP_TOOLCHAIN_INSTALL" != "1" ]; then
    sysctl -w net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      wget curl git xz-utils ca-certificates python3 python3-pip \
      binutils-aarch64-linux-gnu clang-19 libclang-19-dev lld-19
    python3 -m pip install --break-system-packages -q tomlkit
    curl -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable -q
    . "$HOME/.cargo/env"
    MANIFEST=$(curl -fsSL "https://mirrors.tuna.tsinghua.edu.cn/rustup/dist/channel-rust-stable.toml") || MANIFEST=""
    STD_URL=""; SRC_URL=""
    if [ -n "$MANIFEST" ]; then
      STD_URL=$(printf "%s" "$MANIFEST" | grep -A4 "^\[pkg\.rust-std\.target\.aarch64-unknown-none\]$" | grep -oP "(?<=xz_url = \")[^\"]+" || true)
      SRC_URL=$(printf "%s" "$MANIFEST" | grep -oP "(?<=xz_url = \")[^\"]*rust-src-[^\"]+\.tar\.xz" | head -1 || true)
    fi
    for entry in "rust-std-aarch64-unknown-none:$STD_URL" "rust-src:$SRC_URL"; do
      name="${entry%%:*}"; url="${entry#*:}"
      [ -z "$url" ] && continue
      curl -fsSL -o "/tmp/$name.tar.xz" "$url" || continue
      tar -C /tmp -xf "/tmp/$name.tar.xz"
      dir=$(basename "$url" .tar.xz)
      comp=$(cd "/tmp/$dir" && ./install.sh --list-components | grep "^\* " | sed "s/^\* //" | paste -sd, -)
      (cd "/tmp/$dir" && ./install.sh --prefix="$(rustc --print sysroot)" --components="$comp" --disable-ldconfig)
    done
  else
    . "$HOME/.cargo/env"
  fi

  rm -rf frida-core
  git clone --depth 1 --branch "$FRIDA_VERSION" https://github.com/frida/frida-core.git
  cd frida-core
  git submodule update --init --depth 1 releng

  python3 /host/patch-cfi.py src/barebone/agent/linux/frida-kmod.c
  python3 /host/patch-cache-flush-kallsyms.py src/barebone/agent/linux/frida-kmod.c
  python3 /host/patch-kfifo-vmalloc.py src/barebone/agent/linux/frida-kmod.c
  cat >> src/barebone/agent/linux/Kbuild <<0EOF0

ccflags-y += -Wno-implicit-function-declaration
ccflags-y += -Wno-missing-prototypes -Wno-missing-declarations
0EOF0

  SDK="$HOME/sdk-none-arm64-softfloat"
  mkdir -p "$SDK"
  tar -C "$SDK" -xf /host/out/sdk-none-arm64-softfloat.tar.xz

  export PATH="/opt/ddk/clang/"*"/bin:$PATH"
  unset CLANG_PATH
  export LIBCLANG_PATH=/usr/lib/llvm-19/lib
  export CC_AGENT="clang-19 --target=aarch64-none-elf -mabi=aapcs-soft \
    -mgeneral-regs-only -ffixed-x18 -fno-pic --sysroot=$SDK \
    -isystem $SDK/include -I $SDK/include/glib-2.0 -I $SDK/lib/glib-2.0/include \
    -I $SDK/include/capstone -I $SDK/include/json-glib-1.0"

  cd src/barebone/agent/linux
  make ARCH=arm64 FRIDA_SDK="$SDK" GUMJS_DEVKIT_DIR="/host/out/gumjs-devkit" \
    AGENT_RUSTFLAGS="-Zfixed-x18" \
    AGENT_LD=aarch64-linux-gnu-ld AGENT_AR=aarch64-linux-gnu-ar \
    AGENT_NM=aarch64-linux-gnu-nm AGENT_OBJCOPY=aarch64-linux-gnu-objcopy \
    CC_aarch64_unknown_none="$CC_AGENT" KDIR="$KDIR" LLVM=1

  cp frida-agent.ko "/host/out/$KMI/frida-agent.ko"
'

if [ "$SKIP_TOOLCHAIN_INSTALL" = "0" ]; then
  docker commit "$CONTAINER" "$ENV_IMAGE" >/dev/null
  echo "=== cached toolchain as $ENV_IMAGE ==="
fi

python3 "$HERE/strip-bss-roafter.py" "$OUT/frida-agent.ko"
modinfo "$OUT/frida-agent.ko" | tee "$OUT/modinfo.txt"

mkdir -p "$HERE/out/archive"
KMI_NAME="${KMI//-/_}"
TIMESTAMP="$(date +'%Y%m%d-%H%M%S')"
ARCHIVE_KO="$HERE/out/archive/frida-agent_${KMI_NAME}_${TIMESTAMP}.ko"
cp "$OUT/frida-agent.ko" "$ARCHIVE_KO"
llvm-strip -d "$ARCHIVE_KO" 2>/dev/null || aarch64-linux-gnu-strip --strip-debug "$ARCHIVE_KO" 2>/dev/null || true

echo "=== $KMI done: $OUT/frida-agent.ko ($(du -h "$OUT/frida-agent.ko" | cut -f1)) ==="
