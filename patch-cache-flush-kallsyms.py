#!/usr/bin/env python3
"""Resolve caches_clean_inval_pou() via kallsyms instead of a direct call.

Reproduced in QEMU: on android12-5.10, `insmod` refuses to load the module
at all - "Unknown symbol caches_clean_inval_pou (err -2)" - because
frida-kmod.c calls it as an ordinary linked symbol, and that symbol simply
does not exist on 5.10 (it's a later addition to the arm64 cache-maintenance
API; -Wno-implicit-function-declaration in Kbuild only silences the compile
warning, it does nothing about the module loader's hard requirement that
every referenced symbol resolve). This resolves it once at init through the
same mechanism the module already uses for set_memory_*(), calls it through
a function pointer, and skips it (with a one-time warning) where absent.

Handles both the 17.17.0 layout (resolver uses frida_kallsyms_lookup_name_impl,
call site flushes alias->first_page) and the 17.18.0+ layout (resolver uses
frida_kmod_find_function, call site is frida_flush_icache_range(start,size)).
If neither matches and the pointer isn't already present, it leaves the file
untouched and exits 0 rather than hard-failing, so a future layout change
doesn't silently block the whole matrix - it just leaves that one icache
flush as a direct call (only android12-5.10 load is affected).
"""
import sys

MARKER = "frida_caches_clean_inval_pou_impl"

# Declaration anchor is common to both layouts.
DECL_OLD = "static typeof (&set_memory_nx) frida_set_memory_nx_impl;"
DECL_NEW = """static typeof (&set_memory_nx) frida_set_memory_nx_impl;
static void (* frida_caches_clean_inval_pou_impl) (unsigned long start, unsigned long end);"""

# --- 17.18.0+ layout ---
RESOLVE19_OLD = '  frida_set_memory_nx_impl = (void *) frida_kmod_find_function ("set_memory_nx");'
RESOLVE19_NEW = '''  frida_set_memory_nx_impl = (void *) frida_kmod_find_function ("set_memory_nx");
  /*
   * caches_clean_inval_pou() is absent (not merely unexported) on some GKI
   * branches (android12-5.10), so it cannot be a hard link-time reference or
   * the loader refuses the .ko. Resolve it like the set_memory_*() family;
   * frida_flush_icache_range() NULL-checks before calling through it.
   */
  frida_caches_clean_inval_pou_impl = (void *) frida_kmod_find_function ("caches_clean_inval_pou");'''

CALL19_OLD = "  caches_clean_inval_pou (start, start + size);"
CALL19_NEW = '''  if (frida_caches_clean_inval_pou_impl != NULL)
    frida_caches_clean_inval_pou_impl (start, start + size);
  else
    printk_once (KERN_WARNING "frida: caches_clean_inval_pou unavailable - writable-alias icache flush skipped\\n");'''

# --- 17.17.0 layout (kept for reproducibility) ---
RESOLVE17_OLD = '''  frida_set_memory_nx_impl = (void *) frida_kallsyms_lookup_name_impl ("set_memory_nx");
}'''
RESOLVE17_NEW = '''  frida_set_memory_nx_impl = (void *) frida_kallsyms_lookup_name_impl ("set_memory_nx");

  frida_caches_clean_inval_pou_impl = (void *) frida_kallsyms_lookup_name_impl ("caches_clean_inval_pou");
}'''

CALL17_OLD = "  caches_clean_inval_pou (alias->first_page, alias->first_page + mapped_size);"
CALL17_NEW = '''  if (frida_caches_clean_inval_pou_impl != NULL)
    frida_caches_clean_inval_pou_impl (alias->first_page, alias->first_page + mapped_size);
  else
    printk_once (KERN_WARNING "frida: caches_clean_inval_pou unavailable - writable-alias icache flush skipped\\n");'''

LAYOUTS = [
    ("17.18.0+", [(DECL_OLD, DECL_NEW), (RESOLVE19_OLD, RESOLVE19_NEW), (CALL19_OLD, CALL19_NEW)]),
    ("17.17.0", [(DECL_OLD, DECL_NEW), (RESOLVE17_OLD, RESOLVE17_NEW), (CALL17_OLD, CALL17_NEW)]),
]


def patch(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()

    if MARKER in text:
        print(f"skip {path}: caches_clean_inval_pou already resolved via pointer")
        return True

    for label, subs in LAYOUTS:
        if all(old in text for old, _ in subs):
            for old, new in subs:
                text = text.replace(old, new, 1)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"patched {path}: caches_clean_inval_pou via kallsyms ({label} layout)")
            return True

    print(f"WARNING: no known caches_clean_inval_pou layout matched in {path}; "
          f"left as a direct call (android12-5.10 load may fail)", file=sys.stderr)
    return True  # tolerant: do not block the matrix


def main():
    if len(sys.argv) < 2:
        print("usage: patch-cache-flush-kallsyms.py <frida-kmod.c>", file=sys.stderr)
        return 1
    ok = True
    for path in sys.argv[1:]:
        ok = patch(path) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
