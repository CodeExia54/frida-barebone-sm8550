#!/usr/bin/env python3
"""Make the module's rodata/data writable before gum_init runs.

On GKI with CONFIG_STRICT_MODULE_RWX the module's read-only sections (.rodata /
.data..ro_after_init, where the prelinked glib/gobject statics live) are mapped
read-only. gum_init_embedded() -> glib_init() -> gobject init writes into those
statics, which faults: "Unable to handle kernel write to read-only memory" in
gobject_perform_init on android12-5.10.

frida_resolve_kallsyms() has already resolved set_memory_rw() by the time
frida_kmod_init() runs, so re-mark those pages writable there, before
frida_agent_start() spawns the worker that calls gum_init_embedded(). .rodata is
protected at load (not re-applied after init returns), so a one-time flip holds.

Idempotent and tolerant.
"""
import sys

HELPER_ANCHOR = "static int __init\nfrida_kmod_init (void)"
HELPER = """static void __nocfi
frida_kmod_make_data_writable (void)
{
  if (frida_set_memory_rw_impl == NULL)
    return;

#if LINUX_VERSION_CODE >= KERNEL_VERSION (6, 4, 0)
  {
    unsigned int t;
    for (t = 0; t < MOD_MEM_NUM_TYPES; t++)
    {
      unsigned long base, size;
      if (t == MOD_TEXT || t == MOD_INIT_TEXT)
        continue;
      base = (unsigned long) THIS_MODULE->mem[t].base;
      size = THIS_MODULE->mem[t].size;
      if (base == 0 || size == 0)
        continue;
      frida_set_memory_rw_impl (base & PAGE_MASK,
          (int) (PAGE_ALIGN (size) >> PAGE_SHIFT));
    }
  }
#else
  {
    unsigned long start = (unsigned long) THIS_MODULE->core_layout.base
        + THIS_MODULE->core_layout.text_size;
    unsigned long end = (unsigned long) THIS_MODULE->core_layout.base
        + THIS_MODULE->core_layout.size;
    start &= PAGE_MASK;
    end = PAGE_ALIGN (end);
    frida_set_memory_rw_impl (start, (int) ((end - start) >> PAGE_SHIFT));
  }
#endif
}

static int __init
frida_kmod_init (void)"""

CALL_ANCHOR = """frida_kmod_init (void)
{
  frida_resolve_kallsyms ();"""
CALL_NEW = """frida_kmod_init (void)
{
  frida_resolve_kallsyms ();
  frida_kmod_make_data_writable ();"""


def patch(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()

    if "frida_kmod_make_data_writable" in text:
        print(f"skip {path}: make_data_writable already present")
        return True

    if HELPER_ANCHOR in text:
        text = text.replace(HELPER_ANCHOR, HELPER, 1)
    else:
        print(f"WARNING: frida_kmod_init anchor not found in {path}", file=sys.stderr)
        return True

    if CALL_ANCHOR in text:
        text = text.replace(CALL_ANCHOR, CALL_NEW, 1)
    else:
        print(f"WARNING: resolve_kallsyms call anchor not found in {path}", file=sys.stderr)

    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"patched {path}: make_data_writable in frida_kmod_init")
    return True


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kmod-rw.py <frida-kmod.c>", file=sys.stderr)
        return 1
    for p in sys.argv[1:]:
        patch(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
