#!/usr/bin/env python3
"""Resolve caches_clean_inval_pou() via kallsyms instead of a direct call.

Reproduced in QEMU: on android12-5.10, `insmod` refuses to load the module
at all - "Unknown symbol caches_clean_inval_pou (err -2)" - because
frida-kmod.c calls it as an ordinary linked symbol, and that symbol simply
does not exist on 5.10 (it's a later addition to the arm64 cache-maintenance
API; -Wno-implicit-function-declaration in Kbuild only silences the compile
warning, it does nothing about the module loader's hard requirement that
every referenced symbol resolve). frida-kmod.c already has the exact
established pattern for this class of problem - set_memory_ro/rw/x/nx are
resolved through frida_kallsyms_lookup_name_impl() in
frida_resolve_kallsyms() for the same reason (GKI trims its export table).
This gives caches_clean_inval_pou the same treatment: resolved once at
init, called through a function pointer, and skipped (with a one-time
warning) on a kernel where it's absent - trading a hard load failure for a
soft "this one icache flush on the writable-alias teardown path didn't
happen" on those kernels, which is a real but narrow correctness tradeoff,
not a crash.
"""
import sys

DECL_OLD = "static typeof (&set_memory_nx) frida_set_memory_nx_impl;"
DECL_NEW = """static typeof (&set_memory_nx) frida_set_memory_nx_impl;
static void (* frida_caches_clean_inval_pou_impl) (unsigned long start, unsigned long end);"""

RESOLVE_OLD = """  frida_set_memory_nx_impl = (void *) frida_kallsyms_lookup_name_impl ("set_memory_nx");
}"""
RESOLVE_NEW = """  frida_set_memory_nx_impl = (void *) frida_kallsyms_lookup_name_impl ("set_memory_nx");

  /*
   * caches_clean_inval_pou() does not exist at all on some GKI branches
   * (android12-5.10) - not merely unexported, absent - so this cannot be a
   * hard link-time reference (the module loader refuses to load the .ko:
   * "Unknown symbol caches_clean_inval_pou"). Resolve it the same way as
   * the set_memory_*() family above; frida_kmod_unmap_writable() checks
   * for NULL before calling through it.
   */
  frida_caches_clean_inval_pou_impl = (void *) frida_kallsyms_lookup_name_impl ("caches_clean_inval_pou");
}"""

CALL_OLD = """  /* flush_icache_range() inlines a reference to __icache_flags, which GKI does not
   * export; these two are what it would have called. */
  caches_clean_inval_pou (alias->first_page, alias->first_page + mapped_size);
  kick_all_cpus_sync ();"""
CALL_NEW = """  /* flush_icache_range() inlines a reference to __icache_flags, which GKI does not
   * export; these two are what it would have called. */
  if (frida_caches_clean_inval_pou_impl != NULL)
    frida_caches_clean_inval_pou_impl (alias->first_page, alias->first_page + mapped_size);
  else
    printk (KERN_WARNING "frida: caches_clean_inval_pou unavailable - writable-alias icache flush skipped\\n");
  kick_all_cpus_sync ();"""


def main():
    if len(sys.argv) < 2:
        print("usage: patch-cache-flush-kallsyms.py <frida-kmod.c>", file=sys.stderr)
        return 1
    ok = True
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for old, new, label in ((DECL_OLD, DECL_NEW, "decl"),
                                 (RESOLVE_OLD, RESOLVE_NEW, "resolve"),
                                 (CALL_OLD, CALL_NEW, "call site")):
            if old not in text:
                print(f"ERROR: {label} pattern not found in {path}", file=sys.stderr)
                ok = False
                continue
            text = text.replace(old, new, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"patched {path}: caches_clean_inval_pou now resolved via kallsyms")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
