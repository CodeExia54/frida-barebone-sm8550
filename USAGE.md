# Using this on a real Android phone, from zero

Everything below assumes you already have a built `frida-agent.ko` for your
phone's exact KMI target and `adb` access to the phone with root (`su`).

## What each artifact actually does

| File | Role |
|---|---|
| `frida-agent_<kmi>.ko` (matching your phone's kernel — see Step 1) | Runs **inside the kernel**. Exposes `/dev/frida`, a char device. This is the actual instrumentation engine — GumJS running in kernel context. |
| `frida-server-17.19.0-android-arm64-barebone` | Runs as a normal Android process (as root). Opens `/dev/frida` and re-exposes it as an ordinary Frida TCP endpoint for a **PC-side** client. **Built by this repo's CI (`build-frida-server-barebone.yml`) with the Barebone backend compiled in.** |
| `frida-inject-17.19.0-android-arm64-barebone` | Same Barebone backend, but a standalone on-device tool instead of a TCP server — runs a compiled agent script directly against `/dev/frida` with **no PC, no network, nothing to forward**. See "No PC at all" below. **Built by this repo's CI (`build-frida-inject-barebone.yml`).** |
| `linux-kmod.json` | Tells `frida-server`/`frida-inject` *how* to reach the module: the Barebone `device` transport at `/dev/frida`, not the usual ptrace path. |
| `frida` / `frida-ps` (the client, `pip install frida-tools`) | What you type commands into, on any machine, when using `frida-server`. Talks to the server over TCP. Not shipped here — it's a stock pip install. Not needed at all for the `frida-inject` standalone path. |

> **Why a special `frida-server` / `frida-inject`?** The Barebone backend is
> what talks to `/dev/frida`. Frida's **official** prebuilt Android binaries
> are built **without** it (the Barebone backend is only auto-compiled into
> desktop Frida, never Android, upstream). A stock binary has only the
> `local`/`socket`/`remote` backends, so `--device barebone` fails with
> **"Device not found"** even though the module is loaded and `/dev/frida`
> exists. Both binaries in this release are the same 17.19.0 source rebuilt
> with `-Dfrida-core:barebone_backend=enabled` — that's the whole difference.

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

Everything lives in `/data/local/tmp/` — no install, no APK, nothing persists
across reboot.

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
#   frida: gum_init_embedded done
#   frida: transport up, entering main loop
#   frida: listening on /dev/frida
```

If you see `insmod: ... invalid module format` or a vermagic error, the
`.ko`'s target kernel string doesn't match the phone's exact build:

- `insmod -f /data/local/tmp/frida-agent.ko` — force-load, ignoring vermagic.
  Reasonable only when the KMI major.minor genuinely matches (GKI modules are
  ABI-compatible across builds of one KMI; the vermagic string is stricter
  than that in practice).
- If the phone **reboots** instead of reaching the `dmesg` lines, that's a
  real crash — capture the log first (`dmesg -w | tee log.txt &` right before
  `insmod`).

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

> **Not device-verified in this session** — this binary was built and
> confirmed to compile/link cleanly against the Barebone backend
> (`build-frida-inject-barebone.yml`), but the `-D barebone -p 0` invocation
> above follows Frida's standard device-selection CLI convention rather than
> having been exercised against a real `/dev/frida` node. If `-D barebone`
> isn't recognized, run `frida-inject --help` on-device and cross-check
> against `frida-server`'s accepted device names — the Barebone backend
> exposes the same device identity to both tools either way.

## Cleanup

```sh
adb shell su -c 'rmmod frida_agent'   # unload the module (or just reboot)
```

## The one thing genuinely different from ordinary Frida

The `insmod` step, and the Barebone backend. Ordinary Frida modes
(`frida-server` alone, USB, `frida-gadget`) never touch the kernel — this one
does, on purpose, so instrumentation runs in kernel context and doesn't rely
on `ptrace`. That's the entire trade this module buys you.
