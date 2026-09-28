#!/usr/bin/env python3
"""Patch frida-kmod.c: mark __nocfi all functions that indirectly call
kprobe-resolved unexported symbols, otherwise CFI (CONFIG_CFI_CLANG / KCFI)
panics with "CFI failure". """
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    s = f.read()

subs = [
    ("static void\nfrida_resolve_kallsyms (void)",
     "static void __nocfi\nfrida_resolve_kallsyms (void)"),
    ("int\nfrida_kmod_protect (u64 address",
     "int __nocfi\nfrida_kmod_protect (u64 address"),
    ("u64\nfrida_kmod_find_symbol (const char * name)",
     "u64 __nocfi\nfrida_kmod_find_symbol (const char * name)"),
    ("int\nfrida_kmod_enumerate_symbols (FridaFoundSymbolFunc func",
     "int __nocfi\nfrida_kmod_enumerate_symbols (FridaFoundSymbolFunc func"),
    ("void\nfrida_kmod_unmap_writable (void * mapping)",
     "void __nocfi\nfrida_kmod_unmap_writable (void * mapping)"),
    ("static void\nfrida_flush_icache_range (unsigned long start, unsigned long size)",
     "static noinline void __nocfi\nfrida_flush_icache_range (unsigned long start, unsigned long size)"),
    ("u64\nfrida_kmod_process_alloc (int pid,",
     "u64 __nocfi\nfrida_kmod_process_alloc (int pid,"),
    ("int\nfrida_kmod_process_free (int pid,",
     "int __nocfi\nfrida_kmod_process_free (int pid,"),
    ("u64\nfrida_kmod_process_write (int pid,",
     "u64 __nocfi\nfrida_kmod_process_write (int pid,"),
    ("u64\nfrida_kmod_process_read (int pid,",
     "u64 __nocfi\nfrida_kmod_process_read (int pid,"),
    ("int\nfrida_kmod_process_spawn_thread (int pid,",
     "int __nocfi\nfrida_kmod_process_spawn_thread (int pid,"),
    ("static struct mm_struct *\nfrida_grab_process_mm (int pid)",
     "static struct mm_struct * __nocfi\nfrida_grab_process_mm (int pid)"),
    ("static struct task_struct *\nfrida_grab_process_leader (int pid)",
     "static struct task_struct * __nocfi\nfrida_grab_process_leader (int pid)"),
    ("static void\nfrida_reparent_into_group (struct task_struct * leader)",
     "static void __nocfi\nfrida_reparent_into_group (struct task_struct * leader)"),
    ("static void\nfrida_adopt_target_context (struct task_struct * leader)",
     "static void __nocfi\nfrida_adopt_target_context (struct task_struct * leader)"),
    ("static struct frida_cloak *\nfrida_cloak_get (pid_t tgid)",
     "static struct frida_cloak * __nocfi\nfrida_cloak_get (pid_t tgid)"),
    ("void *\nfrida_kmod_alloc_code (size_t size)",
     "void * __nocfi\nfrida_kmod_alloc_code (size_t size)"),
    ("void\nfrida_kmod_free_code (void * ptr,",
     "void __nocfi\nfrida_kmod_free_code (void * ptr,"),
]

for old, new in subs:
    if old not in s:
        # Tolerant: some functions might not exist in older frida versions (e.g. 17.17.0)
        print(f"NOTE: pattern not found (may be pre-17.18): {old.splitlines()[0]!r}")
    else:
        s = s.replace(old, new, 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(s)

print("patched:", path)
