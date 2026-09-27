# Using this on a real Android phone, from zero

Everything below assumes you already have a built `frida-agent.ko` for your
phone's exact KMI target (from a `build-ko` matrix leg on
[`build-sdk-devkit-generic.yml`](.github/workflows/build-sdk-devkit-generic.yml)
- see `LOCAL-BUILD.md` for how those get built/fetched) and `adb` access to
the phone with root (`su`).

## What each artifact actually does

| File | Role |
|---|---|
| `frida-agent.ko` (matching your phone's exact kernel - see Step 1) | Runs **inside the kernel**. Exposes `/dev/frida`, a char device. This is the actual instrumentation engine - GumJS running in kernel context. |
| `frida-server-<version>-android-arm64` | Runs as a normal Android process (as root). Talks to `/dev/frida`, exposes the same TCP protocol every other `frida-server` does - official prebuilt from `frida/frida`'s GitHub releases, not built by this repo. |
| `frida-inject-<version>-android-arm64` | Alternative to `frida-server` - one-shot: injects one script into one process, then exits. Also talks to `/dev/frida`. Official prebuilt, same as above. |
| `linux-kmod.json` (from `frida-core`'s own `src/barebone/agent/etc/`) | Tells `frida-server`/`frida-inject` *how* to reach the module (`/dev/frida`, not the usual ptrace path). |
| `frida` / `frida-ps` / `frida-trace` (host side, `pip install frida-tools`) | The client you actually type commands into. Talks to `frida-server` over TCP. |

## Step 1 - pick the right `.ko`

This is the one step that can't be skipped or guessed. Run on the phone:

```sh
adb shell getprop ro.build.version.release   # Android version, sanity check
adb shell uname -r                            # the actual kernel string
```

`uname -r` gives you something like `5.15.148-android13-...`. Match its
major.minor (`5.15`) to one of the 6 targets this repo builds
(`android13-5.15` here) and use **that** target's `.ko`. If the exact
string doesn't match byte-for-byte (it almost never will on a real device
vs. this repo's generic build), `insmod` will refuse on vermagic - see
Step 3.

## Step 2 - push everything

```sh
adb push frida-agent.ko /data/local/tmp/
adb push frida-server-17.17.0-android-arm64 /data/local/tmp/frida-server
adb push frida-inject-17.17.0-android-arm64 /data/local/tmp/frida-inject
adb push linux-kmod.json /data/local/tmp/

adb shell su -c 'chmod 755 /data/local/tmp/frida-server /data/local/tmp/frida-inject'
```

Everything lives in `/data/local/tmp/` - no install, no APK, nothing
persists across reboot.

## Step 3 - load the module (this *is* the kernel-level step)

```sh
adb shell su -c 'insmod /data/local/tmp/frida-agent.ko'
```

**Yes, this is required, every boot.** The module doesn't survive a
reboot - you `insmod` it fresh each session. Root (`su`) is required -
this is a real kernel module load, the single most privileged thing you
can do to the device.

Check it actually came up:

```sh
adb shell su -c 'dmesg | grep frida'
# expect:
#   frida: agent starting
#   frida: worker entry
#   frida: gum_init_embedded done
#   frida: listening on /dev/frida
```

If instead you see `insmod: ... invalid module format` or a vermagic
error: the `.ko`'s target kernel string doesn't match the phone's exact
build. Two options:

- `insmod -f /data/local/tmp/frida-agent.ko` - force-load, ignoring the
  vermagic mismatch. Only reasonable if the KMI major.minor genuinely
  matches (same kernel version family) - GKI's whole design point is that
  modules built for one KMI are ABI-compatible across builds of it, the
  vermagic string is just stricter than that in practice.
- If it doesn't even reach `dmesg | grep frida` output and the phone
  reboots instead - that's a real crash, the same class of bug this
  repo's `patch-cache-flush-kallsyms.py` / `strip-bss-roafter.py` /
  `patch-gum-init.py` already fix for every crash reproduced in QEMU so
  far. If you hit this on a real device with a build from this repo,
  that's a *new* one - capture the `dmesg` output before it reboots if you
  can (`dmesg -w | tee log.txt &` right before `insmod`).

## Step 4 - start the transport (`frida-server`)

```sh
adb shell su -c 'FRIDA_BAREBONE_CONFIG=/data/local/tmp/linux-kmod.json \
  /data/local/tmp/frida-server --device barebone -l 127.0.0.1:27042'
```

This blocks (keep this shell open, or background it with `&` / `nohup`).
It's now listening on `127.0.0.1:27042` **on the phone**, bridging to
`/dev/frida`.

## Step 5 - reach it from your host machine

```sh
adb forward tcp:27042 tcp:27042
```

Now `127.0.0.1:27042` on your laptop is the phone's `frida-server`.

## Step 6 - actually use it against a process

This is where it becomes ordinary Frida - same API you'd use against any
device:

```sh
frida -H 127.0.0.1:27042 -p 0                 # -p 0 = "System", just to confirm connectivity
frida-ps -H 127.0.0.1:27042                    # list running processes on the phone
frida -H 127.0.0.1:27042 -n com.example.app    # attach to a running app by name
```

Or from Python (`pip install frida`):

```python
import frida
device = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
session = device.attach("com.example.app")
script = session.create_script("""
Interceptor.attach(Module.getExportByName(null, "open"), {
  onEnter(args) { console.log("open:", args[0].readUtf8String()); }
});
""")
script.load()
```

That JS runs **inside the kernel module**, via GumJS, the exact same
engine every other Frida target uses - you write the same scripts you
already know.

## Alternative - skip `frida-server` entirely, `frida-inject`

If you just want one script run once against one process, no host
round-trip:

```sh
adb push myscript.js /data/local/tmp/
adb shell su -c 'FRIDA_BAREBONE_CONFIG=/data/local/tmp/linux-kmod.json \
  /data/local/tmp/frida-inject -n com.example.app -s /data/local/tmp/myscript.js'
```

Still needs the module loaded first (Step 3) - `frida-inject` talks to
`/dev/frida` the same way `frida-server` does, just without the
persistent listener.

## Cleanup

```sh
adb shell su -c 'rmmod frida_agent'   # unload the module (or just reboot)
```

## The one thing that's genuinely different from ordinary Frida usage

The `insmod` step. Every other Frida deployment mode (`frida-server`
alone, USB, `frida-gadget`) never touches the kernel - this one does, on
purpose, so injection doesn't need `ptrace` (useful against processes that
detect/block `ptrace`-based attach). That's the entire trade this module
buys you.
