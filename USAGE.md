# Using this on a real Android phone, from zero

Everything below assumes you already have a built `frida-agent.ko` for your
phone's exact KMI target and `adb` access to the phone with root (`su`).

**This build has been exercised end-to-end on a real SM8550 phone** (not just
QEMU) — `insmod` → `/dev/frida` → `frida-inject` → a JS script actually
running inside the kernel and printing back out. Two bugs that blocked that
path have been fixed here (upstream frida-core still ships both broken as of
17.19.0) — see "Fixed in this build" below. A feature matrix of what was
verified to work (and what crashes) is at the bottom.

## What each artifact actually does

| File | Role |
|---|---|
| `frida-agent_<kmi>.ko` (matching your phone's kernel — see Step 1) | Runs **inside the kernel**. Exposes `/dev/frida`, a char device. This is the actual instrumentation engine — GumJS running in kernel context. |
| `frida-server-17.19.0-android-arm64-barebone` | Runs as a normal Android process (as root). Opens `/dev/frida` and re-exposes it as an ordinary Frida TCP endpoint for a **PC-side** client. **Built by this repo's CI (`build-frida-server-barebone.yml`) with the Barebone backend compiled in.** |
| `frida-inject-17.19.0-android-arm64-barebone` | Same Barebone backend, but a standalone on-device tool instead of a TCP server — runs a compiled agent script directly against `/dev/frida` with **no PC, no network, nothing to forward**. See "No PC at all" below. **Built by this repo's CI (`build-frida-inject-barebone.yml`).** |
| `linux-kmod.json` | Tells `frida-server`/`frida-inject` *how* to reach the module: the Barebone `device` transport at `/dev/frida`, not the usual ptrace path. **This fork's copy has a required field upstream's doesn't — see below.** |
| `frida` / `frida-ps` (the client, `pip install frida-tools`) | What you type commands into, on any machine, when using `frida-server`. Talks to the server over TCP. Not shipped here — it's a stock pip install. Not needed at all for the `frida-inject` standalone path. |

> **Why a special `frida-server` / `frida-inject`?** The Barebone backend is
> what talks to `/dev/frida`. Frida's **official** prebuilt Android binaries
> are built **without** it (the Barebone backend is only auto-compiled into
> desktop Frida, never Android, upstream). A stock binary has only the
> `local`/`socket`/`remote` backends, so `--device barebone` fails with
> **"Device not found"** even though the module is loaded and `/dev/frida`
> exists. Both binaries in this release are the same 17.19.0 source rebuilt
> with `-Dfrida-core:barebone_backend=enabled` — that's the whole difference.

## Fixed in this build (both reproduced on real hardware, not guessed)

**1. `linux-kmod.json` — upstream's own copy is rejected by Frida itself.**
`frida-core`'s `src/barebone/agent/etc/linux-kmod.json` ships `"agent": {
"transport": {...} }` with no `"type"` field. `BareboneConfig`'s deserializer
(`src/frida.vala`) treats an agent with no recognized `type` as invalid, so
loading that file fails immediately — before `/dev/frida` is even touched —
with:
```
Unable to load /data/local/tmp/linux-kmod.json: Config for 'agent' is invalid
```
Fixed by adding `"type": "resident"` (our kernel module is always-resident;
the only other legal value, `"injected"`, is for the debugger-attach case —
VM/emulator targets, not a loaded `.ko`). This fork's `linux-kmod.json` /
`linux-kmod-socket.json` already have the fix; if you pull a fresh copy
straight from `frida-core`'s own repo, add the field yourself.

**2. The kernel module's own hostlink can fail to come up on a real device.**
`frida_kmod_link_open()` used to `kmalloc()` two 1 MiB buffers as
physically-contiguous order-8 blocks. That's reliably available right after a
fresh boot (which is the only time QEMU ever tests it) but **not** on a phone
that's been running a while — real memory fragmentation leaves no order-8 run
free, and `insmod` "succeeds" (module loads, `lsmod` shows it) while
`/dev/frida` never appears:
```
frida-agent: page allocation failure: order:8, mode:0x40cc0(GFP_KERNEL|__GFP_COMP)
...
kfifo_alloc -> frida_kmod_link_open
frida: failed to connect to peer
```
Fixed (`patch-kfifo-vmalloc.py`) by backing both fifos with `vmalloc()`
instead, which only needs virtual contiguity. Confirmed on-device: module
loads, `/dev/frida` appears, hostlink connects, scripts run.

## Step 1 — pick the right `.ko`

The one step that can't be guessed. On the phone:

```sh
adb shell getprop ro.build.version.release   # Android version, sanity check
adb shell uname -r                            # the actual kernel string
```

`uname -r` gives something like `5.15.148-android13-...`. Match its
major.minor (`5.15`) to one of the 6 targets (`android13-5.15`) and use
**that** `.ko`. If the string doesn't match byte-for-byte (it almost never
will on a real device vs. this repo's generic build), `insmod` will refuse on
vermagic — see Step 3.

## Step 2 — push everything

```sh
adb push frida-agent_<kmi>.ko /data/local/tmp/frida-agent.ko
adb push frida-server-17.19.0-android-arm64-barebone /data/local/tmp/frida-server
adb push frida-inject-17.19.0-android-arm64-barebone /data/local/tmp/frida-inject
adb push linux-kmod.json /data/local/tmp/

adb shell su -c 'chmod 755 /data/local/tmp/frida-server /data/local/tmp/frida-inject'
```

(Push whichever of `frida-server`/`frida-inject` matches the path you're taking — Step 4 below covers both.)

Everything lives in `/data/local/tmp/` — no install, no APK. Pushed files
*do* survive a reboot (it's on `/data`, not tmpfs) — only the loaded module
itself resets, so `insmod` is still required fresh every boot.

## Step 3 — load the module (this *is* the kernel-level step)

```sh
adb shell su -c 'insmod /data/local/tmp/frida-agent.ko'
```

**Required every boot** — the module doesn't survive a reboot; `insmod` it
fresh each session. Root (`su`) is required.

Check it came up:

```sh
adb shell su -c 'ls -l /dev/frida'   # expect a crw------- node
adb shell su -c 'dmesg | grep frida'
# expect:
#   frida: agent starting
#   frida: worker entry
#   frida: listening on /dev/frida
```

If you see `insmod: ... invalid module format` or a vermagic error, the
`.ko`'s target kernel string doesn't match the phone's exact build. Try
`insmod -f` — **note:** stock Android/Toybox `insmod` (as opposed to
`busybox`/`kmod`'s) doesn't understand `-f` at all (`insmod: -f: No such
file or directory`) and, on at least this device, loads across a
vermagic mismatch anyway without needing it — try the plain form first.

If the phone **reboots** instead of reaching the `dmesg` lines, that's a
real crash — capture the log first (`dmesg -w | tee log.txt &` right before
`insmod`). You may also see a burst of unrelated `WARNING:` / "Modules linked
in:" spam right after insmod — on our test device this was `dma_fence_array_create`
(display/GPU driver), a pre-existing issue on that phone unrelated to this
module; `dump_stack()` always lists every loaded module on *any* kernel
warning, so `frida_agent` shows up in its "Modules linked in:" line even when
it isn't the cause.

## Step 4 — pick a path: PC-connected server, or standalone on-device inject

### 4a — `frida-server` (needs a PC-side client)

```sh
adb shell su -c 'FRIDA_BAREBONE_CONFIG=/data/local/tmp/linux-kmod.json \
  /data/local/tmp/frida-server --device barebone -l 127.0.0.1:27042'
```

This blocks (keep the shell open, or background with `nohup … &`). It's now
listening on `127.0.0.1:27042` **on the phone**, bridging to `/dev/frida`.

> **SELinux:** on an enforcing device the client domain may need access to
> `/dev/frida`. Running the server as `su`/root (above) is usually enough; if
> `open(/dev/frida)` is denied, that's an SELinux label issue, not the module.

Then, from any machine with `pip install frida-tools`:

```sh
adb forward tcp:27042 tcp:27042
frida -H 127.0.0.1:27042 -p 0
```

`-p 0` attaches to the **bare-metal target — the kernel / whole system**.
That is what this module is for: a GumJS session running in kernel context.
Your JS runs inside the kernel module via the exact same GumJS engine every
other Frida target uses.

### 4b — `frida-inject` (no PC, no network, entirely on-device)

`frida-inject` talks to `/dev/frida` directly through the same Barebone
backend — there's no TCP server and nothing to `adb forward`. This is the
actual "no PC at all" path: everything after `insmod` happens in one `adb
shell` (or, once you're comfortable, from a Termux shell on the phone with no
PC involved at all).

```sh
adb shell su -c 'FRIDA_BAREBONE_CONFIG=/data/local/tmp/linux-kmod.json \
  /data/local/tmp/frida-inject -D barebone -p 0 -s /data/local/tmp/agent.js'
```

- `-D barebone` selects the Barebone device (same one `--device barebone`
  selects for `frida-server`), instead of the default local device.
- `-p 0` targets the bare-metal target, same meaning as in 4a.
- `-s agent.js` is your compiled GumJS agent script, pushed alongside the
  other files.

**Device-verified** (SM8550, `android13-5.15` KMI): with the fixes above
applied, `console.log("exia noob he");` as `agent.js` prints `exia noob he`
back to the `adb shell` running `frida-inject`. `frida-inject` does not exit
on its own after the script runs (no `-e`/eternalize or `-i`/interactive
given) — that's normal; Ctrl-C or an outer `timeout` ends it.

## Verified on real hardware — what works, what doesn't

Run against the fixed `frida-agent.ko` + `frida-inject` on a real SM8550
phone (`android13-5.15`, `5.15.206-android13-9`, KernelSU, enforcing
SELinux). "Works" means it ran and returned the data; "crashes" means the
phone rebooted — confirmed by `uptime` resetting to 0 and USB
re-enumerating, every time, immediately, with zero further script output.

| Feature | Result |
|---|---|
| `console.log(...)` | ✅ Works |
| `setTimeout` / `setInterval` / `clearInterval` | ✅ Works — real concurrent event loop, confirmed interleaved ticks |
| `Memory.alloc(n)` | ✅ Works |
| `NativePointer` arithmetic (`.add()`, `.sub()`, ...) | ✅ Works |
| `ptr.readU8()` / `.writeU8()` / `.readU64()` / `.writeU64()` (instance methods) | ✅ Works |
| `hexdump(ptr, {...})` | ✅ Works |
| `Memory.protect(ptr, size, prot)` | ⚠️ Callable, returns `false` (no-op/refused) on our heap allocation |
| `Memory.scanSync` | ⚠️ Present (`typeof` is `"function"`); not exercised |
| `Process.arch` / `.platform` / `.pointerSize` / `.id` | ✅ Works (`arm64` / `linux` / `8` / `0`) |
| `Process.getCurrentThreadId()` | ⚠️ Returns a value, but not a normal small TID (a 48-bit-ish number) |
| `Process.enumerateModules()` | ✅ Works great — 367 entries incl. `vmlinux` (the kernel image) and every loaded `.ko`, with real base/size |
| `Process.getModuleByName()` / `findModuleByName()` | ✅ Works |
| `Process.enumerateThreads()` | ⚠️ Callable, always returns an empty array |
| `Process.enumerateRanges(prot)` | ⚠️ Callable, always returns an empty array |
| `Module#enumerateExports()` (instance method, e.g. on the `vmlinux` module) | ✅ Works great — full `kallsyms` table, **190,958 entries** for `vmlinux`, real names + addresses |
| `Module.findExportByName(name, ...)` (static free function) | ❌ `TypeError: not a function` — not implemented |
| `Module.enumerateExports/Imports/Symbols(name)` (static free functions) | ❌ `TypeError: not a function` / `undefined` — not implemented (use the instance method above instead) |
| `Memory.readByteArray` / `writeByteArray` / `readU64` / `readPointer` (static free functions) | ❌ `TypeError: not a function` — not implemented (use the `ptr.readXxx()`/`writeXxx()` instance methods instead) |
| `DebugSymbol.fromName(name)` | ⚠️ Callable, but returns all-null (`address: "0x0"`) — not actually wired to kallsyms |
| `recv` / `send` / `NativeFunction` / `NativeCallback` (presence check only) | ⚠️ Present as real functions; `NativeFunction` tested live — see below |
| **`NativeFunction` — actually calling a real kernel function** (`new NativeFunction(_printk_addr, 'int', ['pointer'])` then calling it) | ❌ **Crashes the kernel instantly.** Zero script output reached the host; device rebooted (`uptime` → 0) within the same call. Consistent with this kernel being CFI-hardened (`CONFIG_CFI_CLANG`/KCFI) and GumJS's native-call trampoline using an indirect branch the compiler never instrumented with a matching CFI type hash — KCFI traps that as a CFI failure, which panics. |
| `Interceptor.attach(...)` (hooking a live kernel function) | ⚠️ **Not attempted live** — present in the API (`typeof Interceptor.attach === "function"`), but it installs a trampoline via the same kind of indirect-branch mechanism that just crashed the device for `NativeFunction`. Given the confirmed CFI crash above, treat this as **very likely to crash the kernel the same way** until proven otherwise. If you want to test it, do it in QEMU first (this fork's existing QEMU methodology), not on hardware you care about. |
| `rmmod frida_agent` | ❌ **Crashes the kernel — confirmed twice**, once on a module whose init had failed, once on a module that had fully initialized and already run a script successfully. Don't use it; **reboot the phone instead** to reset state. |

**Bottom line:** read-only/introspection use (enumerate modules, resolve
kallsyms symbols, read/alloc/poke memory you own, timers, logging) is solid
and crash-free. Anything that makes GumJS **execute or hook existing kernel
code** (`NativeFunction` calls, almost certainly `Interceptor.attach` too)
is unsafe on this specific CFI-hardened kernel build as shipped — it will
very likely panic the device. `rmmod` is unsafe unconditionally; reboot
instead.

Runnable copies of the scripts behind the ✅ rows above are in
[`examples/`](examples/) — `01_basics.js`, `02_memory.js`,
`03_modules_and_symbols.js`, `04_timers.js`. None of them call into or hook
existing kernel code, so none of them carry the crash risk described above.

## Cleanup

**Do not run `rmmod` — it panics this device, confirmed repeatedly, even on
a module that initialized and ran scripts successfully.** Just reboot:

```sh
adb reboot
```

The module never persists across a reboot anyway, so this is the normal way
to reset state, not just a crash workaround.

## The one thing genuinely different from ordinary Frida

The `insmod` step, and the Barebone backend. Ordinary Frida modes
(`frida-server` alone, USB, `frida-gadget`) never touch the kernel — this one
does, on purpose, so instrumentation runs in kernel context and doesn't rely
on `ptrace`. That's the entire trade this module buys you — and, per the
table above, right now that trade buys you solid introspection but not yet
safe hooking/calling on a CFI-hardened kernel.
