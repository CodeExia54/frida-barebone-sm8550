# Using this on a real Android phone, from zero

Everything below assumes you already have a built `frida-agent.ko` for your
phone's exact KMI target and `adb` access to the phone with root (`su`).

## What each artifact actually does

| File | Role |
|---|---|
| `frida-agent_<kmi>.ko` (matching your phone's kernel — see Step 1) | Runs **inside the kernel**. Exposes `/dev/frida`, a char device. This is the actual instrumentation engine — GumJS running in kernel context. |
| `frida-server-17.17.0-android-arm64-barebone` | Runs as a normal Android process (as root). Opens `/dev/frida` and re-exposes it as an ordinary Frida TCP endpoint. **Built by this repo's CI (`build-frida-server-barebone.yml`) with the Barebone backend compiled in.** |
| `linux-kmod.json` | Tells the server *how* to reach the module: the Barebone `device` transport at `/dev/frida`, not the usual ptrace path. |
| `frida` / `frida-ps` (the client, `pip install frida-tools`) | What you type commands into, on any machine. Talks to the server over TCP. Not shipped here — it's a stock pip install. |

> **Why a special `frida-server`?** The Barebone backend is what talks to
> `/dev/frida`. Frida's **official** prebuilt `frida-server` for Android is
> built **without** it (at 17.17.0 the Barebone backend is only auto-compiled
> into desktop Frida, never the Android server). A stock `frida-server` has
> only the `local`/`socket`/`remote` backends, so `--device barebone` fails
> with **"Device not found"** even though the module is loaded and `/dev/frida`
> exists. The `-barebone` server in this release is the same 17.17.0 source
> rebuilt with `-Dfrida-core:barebone_backend=enabled` — that one binary is the
> whole difference.

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
adb push frida-server-17.17.0-android-arm64-barebone /data/local/tmp/frida-server
adb push linux-kmod.json /data/local/tmp/

adb shell su -c 'chmod 755 /data/local/tmp/frida-server'
```

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

## Step 4 — start the transport (`frida-server`, Barebone backend)

```sh
adb shell su -c 'FRIDA_BAREBONE_CONFIG=/data/local/tmp/linux-kmod.json \
  /data/local/tmp/frida-server --device barebone -l 127.0.0.1:27042'
```

This blocks (keep the shell open, or background with `nohup … &`). It's now
listening on `127.0.0.1:27042` **on the phone**, bridging to `/dev/frida`.

> **SELinux:** on an enforcing device the client domain may need access to
> `/dev/frida`. Running the server as `su`/root (above) is usually enough; if
> `open(/dev/frida)` is denied, that's an SELinux label issue, not the module.

## Step 5 — reach it from a client

The client is the stock `frida` CLI (`pip install frida-tools`) on any
machine. Forward the port and connect:

```sh
adb forward tcp:27042 tcp:27042
frida -H 127.0.0.1:27042 -p 0
```

`-p 0` attaches to the **bare-metal target — the kernel / whole system**.
That is what this module is for: a GumJS session running in kernel context.
Your JS runs inside the kernel module via the exact same GumJS engine every
other Frida target uses.

> **No PC at all?** The split is unavoidable: the phone runs the *server*, and
> *something* has to be the *client*. That client is normally the `frida` CLI
> on a PC (nothing we ship — just `pip install frida-tools`). Running the
> client on the phone itself means Termux + Python, which is possible but
> fiddly and out of scope here.

## Cleanup

```sh
adb shell su -c 'rmmod frida_agent'   # unload the module (or just reboot)
```

## The one thing genuinely different from ordinary Frida

The `insmod` step, and the Barebone backend. Ordinary Frida modes
(`frida-server` alone, USB, `frida-gadget`) never touch the kernel — this one
does, on purpose, so instrumentation runs in kernel context and doesn't rely
on `ptrace`. That's the entire trade this module buys you.
