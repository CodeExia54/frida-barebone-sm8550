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
  ctx->mm = mm;
  mmget (mm);

  frida_kthread_use_mm_impl (mm);
  tid = frida_spawn_thread_compat (frida_spawn_trampoline, ctx);"""

# --- kernel_clone fallback plumbing (for <5.18 without user_mode_thread) ---
KCLONE_TYPEDEF_OLD = "typedef pid_t (* FridaUserModeThreadFunc) (int (* fn) (void *), void * arg, unsigned long flags);"
KCLONE_TYPEDEF_NEW = """typedef pid_t (* FridaUserModeThreadFunc) (int (* fn) (void *), void * arg, unsigned long flags);
typedef pid_t (* FridaKernelCloneFunc) (struct kernel_clone_args * args);"""

KCLONE_DECL_OLD = "static FridaUserModeThreadFunc frida_user_mode_thread_impl;"
KCLONE_DECL_NEW = """static FridaUserModeThreadFunc frida_user_mode_thread_impl;
static FridaKernelCloneFunc frida_kernel_clone_impl;"""

KCLONE_RESOLVE_OLD = '  frida_user_mode_thread_impl = (FridaUserModeThreadFunc) frida_kmod_find_function ("user_mode_thread");'
KCLONE_RESOLVE_NEW = '''  frida_user_mode_thread_impl = (FridaUserModeThreadFunc) frida_kmod_find_function ("user_mode_thread");
  frida_kernel_clone_impl = (FridaKernelCloneFunc) frida_kmod_find_function ("kernel_clone");'''

CTX_MM_OLD = """struct frida_spawn_ctx
{
  u64 entry;
  u64 stack;
  u64 arg;
  u64 tls;
  struct task_struct * leader;
};"""
CTX_MM_NEW = """struct frida_spawn_ctx
{
  u64 entry;
  u64 stack;
  u64 arg;
  u64 tls;
  struct task_struct * leader;
  struct mm_struct * mm;
};"""

# cfi runs first and may add __nocfi to the spawn fn, so match both forms and
# insert the helper definition just before whichever one is present.
COMPAT_FN_ANCHORS = [
    "int __nocfi\nfrida_kmod_process_spawn_thread (int pid,",
    "int\nfrida_kmod_process_spawn_thread (int pid,",
]
COMPAT_HELPER = """static int __nocfi
frida_spawn_thread_compat (int (* fn) (void *), void * arg)
{
  if (frida_user_mode_thread_impl != NULL)
    return frida_user_mode_thread_impl (fn, arg, 0);

  if (frida_kernel_clone_impl != NULL)
  {
    /* Pre-5.18: no user_mode_thread(). kernel_clone() with fn in .stack makes a
     * kernel thread; frida_spawn_trampoline converts it to a user thread. Only
     * fields present on every kernel are set; the rest zero-init. */
    struct kernel_clone_args args = {
      .flags = CLONE_VM | CLONE_UNTRACED,
      .exit_signal = 0,
      .stack = (unsigned long) fn,
      .stack_size = (unsigned long) arg,
    };
    return frida_kernel_clone_impl (&args);
  }

  return -ENOSYS;
}

"""

TRAMP_OLD = """  regs = task_pt_regs (current);

  kfree (data);

  frida_reparent_into_group (ctx.leader);"""
TRAMP_NEW = """  regs = task_pt_regs (current);

  kfree (data);

  /* On the kernel_clone fallback (<5.18) this was created as a kernel thread
   * with no mm; make it a real user thread and adopt the target mm carried in
   * ctx.mm. On the user_mode_thread path current->mm is already set, so just
   * drop the extra ref the caller took. */
  current->flags &= ~PF_KTHREAD;
  if (current->mm == NULL && ctx.mm != NULL)
  {
    mmgrab (ctx.mm);
    current->active_mm = ctx.mm;
    current->mm = ctx.mm;
  }
  else if (ctx.mm != NULL)
  {
    frida_mmput_impl (ctx.mm);
  }

  frida_reparent_into_group (ctx.leader);"""

DETACH_SIG_OLD = "typedef void (* FridaDetachPidFunc) (struct pid ** pids, struct task_struct * task, enum pid_type type);"
DETACH_SIG_NEW = "typedef void (* FridaDetachPidFunc) (struct task_struct * task, enum pid_type type);"

DETACH_CALL_OLD = """  write_lock_irq (frida_tasklist_lock);

  frida_detach_pid_impl (freed, child, PIDTYPE_SID);
  frida_detach_pid_impl (freed, child, PIDTYPE_PGID);
  frida_detach_pid_impl (freed, child, PIDTYPE_TGID);"""

DETACH_CALL_NEW = """  write_lock_irq (frida_tasklist_lock);

  frida_detach_pid_impl (child, PIDTYPE_SID);
  frida_detach_pid_impl (child, PIDTYPE_PGID);
  frida_detach_pid_impl (child, PIDTYPE_TGID);"""

FREE_PIDS_OLD = "  frida_free_pids_impl (freed);"
FREE_PIDS_NEW = """  if (frida_free_pids_impl != NULL)
    frida_free_pids_impl (freed);"""

HIDE_SELF_FN = """static long
frida_kmod_hide_self (void)
{
  list_del_init (&THIS_MODULE->list);
  kobject_del (&THIS_MODULE->mkobj.kobj);
  list_del_init (&THIS_MODULE->mkobj.kobj.entry);
  return 0;
}

"""

PRCTL_HIDE_OLD = """    case FRIDA_PROCESS_OP_CLOAK_RANGE:
      return frida_prctl_cloak_range (uargs);"""

PRCTL_HIDE_NEW = """    case FRIDA_PROCESS_OP_CLOAK_RANGE:
      return frida_prctl_cloak_range (uargs);
    case 71UL:
      return frida_kmod_hide_self ();"""


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

    # #6 user_mode_thread -> kernel_clone fallback for <5.18 (5.10/5.15)
    if "frida_spawn_thread_compat" in text:
        changed.append("spawn_fallback (already)")
    else:
        # ctx->mm field
        if CTX_MM_OLD in text:
            text = text.replace(CTX_MM_OLD, CTX_MM_NEW, 1); changed.append("ctx_mm")
        else:
            print(f"WARNING: frida_spawn_ctx anchor not found in {path}", file=sys.stderr)
        # kernel_clone typedef / decl / resolve
        if KCLONE_TYPEDEF_OLD in text:
            text = text.replace(KCLONE_TYPEDEF_OLD, KCLONE_TYPEDEF_NEW, 1)
        else:
            print(f"WARNING: kclone typedef anchor not found in {path}", file=sys.stderr)
        if KCLONE_DECL_OLD in text:
            text = text.replace(KCLONE_DECL_OLD, KCLONE_DECL_NEW, 1)
        else:
            print(f"WARNING: kclone decl anchor not found in {path}", file=sys.stderr)
        if KCLONE_RESOLVE_OLD in text:
            text = text.replace(KCLONE_RESOLVE_OLD, KCLONE_RESOLVE_NEW, 1)
        else:
            print(f"WARNING: kclone resolve anchor not found in {path}", file=sys.stderr)
        # compat helper fn (inserted before frida_kmod_process_spawn_thread)
        _fn_done = False
        for _anchor in COMPAT_FN_ANCHORS:
            if _anchor in text:
                text = text.replace(_anchor, COMPAT_HELPER + _anchor, 1)
                _fn_done = True
                break
        if not _fn_done:
            print(f"WARNING: spawn_thread fn anchor not found in {path}", file=sys.stderr)
        # trampoline PF_KTHREAD clear + mm adoption
        if TRAMP_OLD in text:
            text = text.replace(TRAMP_OLD, TRAMP_NEW, 1)
        else:
            print(f"WARNING: trampoline anchor not found in {path}", file=sys.stderr)
        # spawn call site
        if SPAWN_NULL_OLD in text:
            text = text.replace(SPAWN_NULL_OLD, SPAWN_NULL_NEW, 1); changed.append("spawn_fallback")
        else:
            print(f"NOTE: spawn call-site anchor not found in {path}")

    # #7 detach_pid signature & invocation fix (2 args in Linux >= 6.1)
    if "typedef void (* FridaDetachPidFunc) (struct task_struct * task" in text:
        changed.append("detach_pid_sig (already)")
    elif DETACH_SIG_OLD in text:
        text = text.replace(DETACH_SIG_OLD, DETACH_SIG_NEW, 1)
        changed.append("detach_pid_sig")
    else:
        print(f"NOTE: detach_pid typedef anchor not found in {path}")

    if DETACH_CALL_NEW in text:
        changed.append("detach_pid_call (already)")
    elif DETACH_CALL_OLD in text:
        text = text.replace(DETACH_CALL_OLD, DETACH_CALL_NEW, 1)
        changed.append("detach_pid_call")
    else:
        print(f"NOTE: detach_pid call anchor not found in {path}")

    if "if (frida_free_pids_impl != NULL)" in text:
        changed.append("free_pids (already)")
    elif FREE_PIDS_OLD in text:
        text = text.replace(FREE_PIDS_OLD, FREE_PIDS_NEW, 1)
        changed.append("free_pids")
    else:
        print(f"NOTE: free_pids anchor not found in {path}")

    # #8 hide_module ported from pvm-shadow (OP 71)
    if "frida_kmod_hide_self" in text:
        changed.append("hide_module (already)")
    else:
        # Insert fn before frida_sys_prctl
        prctl_anchor = None
        for cand in ("static long __nocfi\nfrida_sys_prctl", "static long\nfrida_sys_prctl"):
            if cand in text:
                prctl_anchor = cand
                break
        if prctl_anchor is not None:
            text = text.replace(prctl_anchor, HIDE_SELF_FN + prctl_anchor, 1)
            if PRCTL_HIDE_OLD in text:
                text = text.replace(PRCTL_HIDE_OLD, PRCTL_HIDE_NEW, 1)
                changed.append("hide_module")
            else:
                print(f"WARNING: prctl hide anchor not found in {path}", file=sys.stderr)
        else:
            print(f"NOTE: frida_sys_prctl anchor not found in {path}")

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
