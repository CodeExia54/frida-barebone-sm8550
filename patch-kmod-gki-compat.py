#!/usr/bin/env python3
"""GKI header-compat fixes for 17.18.0+ frida-kmod.c (the new injector code).

Problems the upstream code has against GKI kernel headers / runtime:

  #1 fs_struct seqlock/lock mismatch (fixed in ccflags)
  #2 sysvsem: guard with CONFIG_SYSVIPC
  #3 kernel_symbol: guard with LINUX_VERSION_CODE >= 6.4.0
  #4 quick_threads: guard with LINUX_VERSION_CODE >= 6.0.0
  #5 kernel text page lookup: check frida_kernel_base before vmalloc_to_page
     (prevents 5.10 vmalloc_to_page block mapping warning & NULL return)
  #6 user_mode_thread NULL check in process_spawn_thread (absent on <=5.13)
"""
import sys

KSYM_MARKER = "FRIDA_KERNEL_SYMBOL_DEFINED"
KSYM_ANCHOR = '#define FRIDA_CONTROL_TOKEN 0x1d5f9e6b2c7a4038ULL'
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

PAGE_FOR_VIRT_OLD = """static struct page *
frida_page_for_virtual (unsigned long address)
{
  if (frida_is_vmalloc_or_module_addr ((void *) address))
    return vmalloc_to_page ((void *) address);"""

PAGE_FOR_VIRT_NEW = """static struct page *
frida_page_for_virtual (unsigned long address)
{
  if (frida_kernel_base != 0 &&
      address >= frida_kernel_base &&
      address < frida_kernel_base + frida_kernel_size)
    return pfn_to_page (__phys_to_pfn (__pa_symbol (address)));

  if (frida_is_vmalloc_or_module_addr ((void *) address))
  {
    struct page * page = vmalloc_to_page ((void *) address);
    if (page != NULL)
      return page;
  }"""

SPAWN_NULL_OLD = """  ctx->leader = leader;

  frida_kthread_use_mm_impl (mm);
  tid = frida_user_mode_thread_impl (frida_spawn_trampoline, ctx, 0);"""

SPAWN_NULL_NEW = """  ctx->leader = leader;

  if (frida_user_mode_thread_impl == NULL)
  {
    kfree (ctx);
    frida_mmput_impl (mm);
    return -ENOSYS;
  }

  frida_kthread_use_mm_impl (mm);
  tid = frida_user_mode_thread_impl (frida_spawn_trampoline, ctx, 0);"""


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

    # #4 quick_threads
    if QT_NEW in text:
        changed.append("quick_threads (already)")
    elif QT_OLD in text:
        text = text.replace(QT_OLD, QT_NEW, 1)
        changed.append("quick_threads")
    else:
        print(f"WARNING: quick_threads anchor not found in {path}", file=sys.stderr)

    # #5 page_for_virtual kernel text check
    if "address >= frida_kernel_base" in text:
        changed.append("page_for_virt (already)")
    elif PAGE_FOR_VIRT_OLD in text:
        text = text.replace(PAGE_FOR_VIRT_OLD, PAGE_FOR_VIRT_NEW, 1)
        changed.append("page_for_virt")
    else:
        print(f"NOTE: page_for_virtual anchor not found in {path}")

    # #6 user_mode_thread NULL check
    if "frida_user_mode_thread_impl == NULL" in text:
        changed.append("spawn_null_check (already)")
    elif SPAWN_NULL_OLD in text:
        text = text.replace(SPAWN_NULL_OLD, SPAWN_NULL_NEW, 1)
        changed.append("spawn_null_check")
    else:
        print(f"NOTE: spawn_null anchor not found in {path}")

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
