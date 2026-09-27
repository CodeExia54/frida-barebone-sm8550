# Multi-target local build (generic, non-BTI)

This fork's original workflows target one exact device (OnePlus SM8550,
`ghcr.io/ylarod/ddk-min:android13-5.15-20260313`). These additions build
`frida-agent.ko` for every generic `ghcr.io/ylarod/ddk:<target>` KMI image
instead - `android12-5.10`, `android13-5.15`, `android14-6.1`,
`android15-6.6`, `android16-6.12`, `android17-6.18`.

## Split: heavy build on CI, fast build local

The from-source SDK + GumJS devkit build (Meson+Vala+GLib+QuickJS, this
repo's `patch-gum-init.py`/`patch-gum-exception-null.py` applied directly
to `frida-gum`'s own source) is slow and arch-only, not KMI-specific - it
runs **once per Frida version** on GitHub Actions
(`.github/workflows/build-sdk-devkit-generic.yml`), not on your machine.

Everything KMI-specific - the actual kernel module - stays **local and
fast**, so you can rebuild and boot-test any target in QEMU in a couple of
minutes without touching CI again.

```
.github/workflows/build-sdk-devkit-generic.yml   trigger this on GitHub
                                                  (gh workflow run ... or the
                                                  Actions tab) whenever you
                                                  need a fresh SDK+devkit -
                                                  new Frida version, or
                                                  after changing
                                                  patch-gum-init.py /
                                                  patch-gum-exception-null.py

fetch-ci-artifacts.sh   downloads that run's SDK + devkit artifacts into
                        out/ - run once (or after re-running the workflow)

build-ko.sh <kmi>       builds frida-agent.ko for one target, using the
                        fetched SDK+devkit. Reuses frida-build-env:<kmi>
                        (clang-19+Rust) if the sibling frida-barebone repo
                        already built one for that target.

build-all.sh            fetch-ci-artifacts.sh (if needed) + build-ko.sh
                        for all 6 targets
```

## What's different from the frida-barebone repo (this fork's sibling)

`frida-barebone` consumes Frida's *official prebuilt* devkit and works
around the GObject-init bug from the Rust side
(`patch-gum-init-rust.py`) - verified crash-free in QEMU across all 6
targets, ~19-20MB `.ko`. This fork instead builds `frida-gum` from source
and fixes the same bug at its actual root
(`patch-gum-init.py` on `gum.c` itself), which is also expected to produce
a meaningfully smaller `.ko` (official CI runs of this fork's original,
device-pinned workflow report ~7.4MB) - not yet independently confirmed
for the generic multi-target build here.

Applied here: `patch-cfi.py`, `patch-gum-init.py`, `patch-gum-exception-
null.py`, `strip-bss-roafter.py`, plus `frida-barebone`'s own
`patch-cache-flush-kallsyms.py` (android12-5.10's `insmod`-refuses-to-load
bug - not specific to which devkit you consume, so copied over as-is).

**Not applied**: `patch-kmod-rw.py` / `patch-kmod-rocheck.py` - the
runtime `set_memory_rw()` re-flip workaround for the same `.bss`-goes-
read-only bug `strip-bss-roafter.py` already fixes at the ELF level, with
no runtime protection changes. `strip-bss-roafter.py` alone was sufficient
for every crash reproduced in `frida-barebone`'s own QEMU testing, so kept
this fork on the same simpler, lower-risk path unless a case surfaces
where it isn't enough. **Not applied**: `patch-bti-env.py` /
`patch-gum-bti-ret.py` - this is the non-BTI build; wire those two back in
(plus `-mbranch-protection=bti` / `-Zbranch-protection=bti`) for the BTI
variant instead.

## Testing

Boot each `out/<kmi>/frida-agent.ko` in QEMU the same way `frida-barebone`
does (`panic=-1 -no-reboot`, so a crash lands in a captured log instead of
what a real device does - reboot) - see that repo's `USAGE.md`.
