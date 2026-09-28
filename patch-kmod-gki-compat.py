#!/usr/bin/env python3
"""GKI header-compat fixes for 17.18.0+ frida-kmod.c (the new injector code).

Two problems the upstream code has against GKI kernel headers (all 6 KMI
targets fail identically without these):

  #2 sysvsem: frida_adopt_target_context() reads leader->sysvsem.undo_list and
     writes current->sysvsem.undo_list unconditionally, but GKI builds with
     CONFIG_SYSVIPC off have no `sysvsem` member in task_struct
     ("no member named 'sysvsem'"). Guard both with #ifdef CONFIG_SYSVIPC -
     with no SysV IPC there is no undo list to adopt.

  #3 kernel_symbol: the "expose module symbols" feature dereferences
     struct kernel_symbol (mod->syms[i], sym->name_offset/value_offset), but
     GKI module.h keeps it forward-declared only ("incomplete type"). Complete
     it with the kernel's own layout.

(#1, the fs_struct seqlock/lock mismatch, is fixed with -DFRIDA_HAVE_FS_STRUCT_LOCK
in ccflags, not here.)

Idempotent and tolerant: re-running is a no-op, and an unmatched anchor warns
rather than hard-failing.
"""
import sys

KSYM_MARKER = "FRIDA_KERNEL_SYMBOL_DEFINED"
KSYM_ANCHOR = '#define FRIDA_CONTROL_TOKEN 0x1d5f9e6b2c7a4038ULL'
# struct kernel_symbol is defined in <linux/export.h> on <=6.1 (5.10/5.15/6.1)
# but only forward-declared (opaque) on 6.4+ (6.6/6.12/6.18), where the
# module-symbol enumeration below fails to dereference it. Define it ONLY on
# the kernels where it is opaque, or we hit "redefinition" on the older ones.
KSYM_BLOCK = '''#define FRIDA_CONTROL_TOKEN 0x1d5f9e6b2c7a4038ULL

#include <linux/version.h>
#if LINUX_VERSION_CODE >= KERNEL_VERSION (6, 4, 0)
/* GKI keeps struct kernel_symbol forward-declared only here; the module-symbol
 * enumeration below dereferences it, so complete it with the kernel's own
 * layout (see include/linux/export-internal.h). */
#ifndef FRIDA_KERNEL_SYMBOL_DEFINED
#define FRIDA_KERNEL_SYMBOL_DEFINED
struct kernel_symbol {
#ifdef CONFIG_HAVE_ARCH_PREL32_RELOCATIONS
  int value_offset;
  int name_offset;
  int namespace_offset;
#else
  unsigned long value;
  const char * name;
  const char * namespace;
#endif
};
#endif
#endif'''

# signal_struct.quick_threads was added in 6.0; absent on 5.10/5.15.
QT_OLD = "  leader->signal->quick_threads++;"
QT_NEW = """#if LINUX_VERSION_CODE >= KERNEL_VERSION (6, 0, 0)
  leader->signal->quick_threads++;
#endif"""

SYSVSEM_DECL_OLD = "  void * undo_list = leader->sysvsem.undo_list;"
SYSVSEM_DECL_NEW = """#ifdef CONFIG_SYSVIPC
  void * undo_list = leader->sysvsem.undo_list;
#endif"""

SYSVSEM_USE_OLD = """  if (undo_list != NULL)
  {
    refcount_inc (&((struct frida_sem_undo_list *) undo_list)->refcnt);
    current->sysvsem.undo_list = undo_list;
  }
}"""
SYSVSEM_USE_NEW = """#ifdef CONFIG_SYSVIPC
  if (undo_list != NULL)
  {
    refcount_inc (&((struct frida_sem_undo_list *) undo_list)->refcnt);
    current->sysvsem.undo_list = undo_list;
  }
#endif
}"""


def patch(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    changed = []

    # #3 kernel_symbol completion
    if KSYM_MARKER in text:
        changed.append("kernel_symbol (already)")
    elif KSYM_ANCHOR in text:
        text = text.replace(KSYM_ANCHOR, KSYM_BLOCK, 1)
        changed.append("kernel_symbol")
    else:
        print(f"WARNING: kernel_symbol anchor not found in {path}", file=sys.stderr)

    # #2 sysvsem guards
    if "#ifdef CONFIG_SYSVIPC\n  void * undo_list" in text:
        changed.append("sysvsem-decl (already)")
    elif SYSVSEM_DECL_OLD in text:
        text = text.replace(SYSVSEM_DECL_OLD, SYSVSEM_DECL_NEW, 1)
        changed.append("sysvsem-decl")
    else:
        print(f"WARNING: sysvsem decl anchor not found in {path}", file=sys.stderr)

    if SYSVSEM_USE_NEW in text:
        changed.append("sysvsem-use (already)")
    elif SYSVSEM_USE_OLD in text:
        text = text.replace(SYSVSEM_USE_OLD, SYSVSEM_USE_NEW, 1)
        changed.append("sysvsem-use")
    else:
        print(f"WARNING: sysvsem use anchor not found in {path}", file=sys.stderr)

    # #4 quick_threads (signal_struct field, 6.0+)
    if QT_NEW in text:
        changed.append("quick_threads (already)")
    elif QT_OLD in text:
        text = text.replace(QT_OLD, QT_NEW, 1)
        changed.append("quick_threads")
    else:
        print(f"WARNING: quick_threads anchor not found in {path}", file=sys.stderr)

    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"patched {path}: GKI compat [{', '.join(changed)}]")
    return True


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kmod-gki-compat.py <frida-kmod.c>", file=sys.stderr)
        return 1
    for path in sys.argv[1:]:
        patch(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
