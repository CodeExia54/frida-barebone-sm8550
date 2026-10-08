#!/usr/bin/env python3
"""Make NativeFunction calls BTI-safe on a Linux kernel built with
CONFIG_ARM64_BTI_KERNEL + LTO, by routing ffi_call()'s target through a
tiny landing-pad stub instead of calling it directly.

Confirmed root cause (verified live on real SM8550 hardware, not guessed):
on an LTO-built kernel, the compiler's whole-program analysis proves almost
every ordinary kernel function - _printk included - is only ever reached
via a direct branch from within the kernel's own code, and correctly,
deliberately omits its BTI landing pad as an optimisation. That's
permanent, baked into the already-compiled vmlinux. gumquickcore.c's
`ffi_call (cif, implementation, rvalue, avalue)` reaches `implementation`
via a BLR - an indirect branch from *outside* that closed-world analysis,
exactly the case LTO assumed could never happen. The CPU traps that in
hardware the instant the branch lands, before the "called" function runs a
single instruction: an immediate, silent kernel panic. this fork's own
patch-gum-bti-ret.py already fixes the analogous problem for Gum's *own*
tail-jump trampolines (Interceptor's redirect epilogue, the deflector
dispatchers, far put_branch_address) - by design it only covers BR, never
BLR, so it was never going to help this call-and-return site.

Direct branches (B/BL) and RET are architecturally exempt from the BTI
check - only indirect BR/BLR are checked. So instead of handing ffi_call
the raw target pointer, this routes through a tiny cached stub: our own
`bti c` landing pad (so ffi_call's BLR into *us* passes), then an unchecked
transfer onward to the real target - a direct branch when in range (the
common case: ARM64 Linux deliberately places the kernel module region
within +-128MB of vmlinux's own text, precisely so modules and the core
kernel can call each other directly - confirmed on-device, frida-agent.ko
sits ~36MB from vmlinux, well inside that window), or an LDR+RET when it
isn't (RET is exempt regardless of distance - the same trick
patch-gum-bti-ret.py already uses, applied here to a call site it doesn't
cover). Either way X30/LR is left exactly as ffi_call's own BLR set it, so
the target's own eventual RET returns straight back into ffi_call - the
stub never needs its own epilogue.

Skipped entirely when the target already has a landing pad (the normal
case for anything actually registered as a callback somewhere, e.g.
panic()) - no stub built, no overhead, ffi_call calls it exactly as before.

The stub memory comes from GumCodeAllocator's slice API
(gum_code_allocator_alloc_slice + gum_code_allocator_commit) - the same
mechanism this backend's own Interceptor trampolines already use
successfully (see gum_write_thunk in gumcodeallocator.c, and
backend-arm64/guminterceptor-arm64.c's trampoline_slice usage) - not the
generic gum_memory_allocate()+gum_memory_mark_code() pair, which a first
version of this patch used and which is unproven (and, confirmed on real
hardware, broken) on this exotic bare-metal kernel backend: it crashed the
device with zero output, independent of the BTI diagnosis above.
GumCodeSlice separates the writable address (->data) from the address the
code will actually execute at (->pc) - exactly the split this backend may
need and the generic pair doesn't know about.
"""
import sys

MARKER = "gum_arm64_get_bti_safe_call_target"

INCLUDE_OLD = """#include "gumsourcemap.h"

#include <string.h>"""

INCLUDE_NEW = """#include "gumsourcemap.h"

#ifdef HAVE_ARM64
#include <gum/arch-arm64/gumarm64writer.h>
#include <gum/gumcodeallocator.h>

/*
 * On a Linux kernel built with CONFIG_ARM64_BTI_KERNEL + LTO, almost every
 * ordinary kernel function has had its BTI landing pad stripped by the
 * compiler's whole-program analysis, because nothing *inside* the kernel
 * ever calls it indirectly. ffi_call()'s own BLR into an arbitrary native
 * target is exactly the external indirect call that analysis assumed could
 * never happen - the CPU traps it in hardware the instant the branch
 * lands. See the top of this file's patch (patch-gum-bti-nativefunction.py)
 * for the full writeup; this routes the call through a tiny cached stub
 * that supplies our own landing pad, then transfers onward via a direct
 * branch (in range: BTI-exempt by architecture) or LDR+RET (out of range:
 * RET is BTI-exempt regardless of distance). Intentionally never freed -
 * this backend's whole lifetime is one kernel boot, and a handful of
 * one-slice stubs is noise next to everything else already resident.
 *
 * Stub memory comes from GumCodeAllocator - the same mechanism this
 * backend's own Interceptor trampolines already use successfully - not
 * the generic gum_memory_allocate()/gum_memory_mark_code() pair, which is
 * unproven on this exotic bare-metal backend.
 */

static GumCodeAllocator gum_bti_stub_allocator;
static GHashTable * gum_bti_stub_cache = NULL;
G_LOCK_DEFINE_STATIC (gum_bti_stub_cache);

static gboolean
gum_arm64_address_has_bti_landing_pad (gconstpointer address)
{
  guint32 insn;

  if (!gum_memory_is_readable (address, sizeof (insn)))
    return TRUE; /* unknown: leave it to ffi_call, as before */

  insn = *(const guint32 *) address;

  return insn == 0xd503241f || /* bti    */
         insn == 0xd503245f || /* bti c  */
         insn == 0xd503249f || /* bti j  */
         insn == 0xd50324df;   /* bti jc */
}

static gpointer
gum_arm64_get_bti_safe_call_target (gpointer target)
{
  gpointer stub;
  GumCodeSlice * slice;
  GumArm64Writer aw;

  if (gum_arm64_address_has_bti_landing_pad (target))
    return target;

  G_LOCK (gum_bti_stub_cache);

  if (gum_bti_stub_cache == NULL)
  {
    gum_bti_stub_cache = g_hash_table_new (NULL, NULL);
    gum_code_allocator_init (&gum_bti_stub_allocator, 256);
  }

  stub = g_hash_table_lookup (gum_bti_stub_cache, target);
  if (stub != NULL)
  {
    G_UNLOCK (gum_bti_stub_cache);
    return stub;
  }

  slice = gum_code_allocator_alloc_slice (&gum_bti_stub_allocator);

  gum_arm64_writer_init (&aw, slice->data);
  aw.pc = GUM_ADDRESS (slice->pc);

  gum_arm64_writer_put_instruction (&aw, 0xd503245f); /* bti c */

  if (!gum_arm64_writer_put_b_imm (&aw, GUM_ADDRESS (target)))
  {
    gum_arm64_writer_put_ldr_reg_address (&aw, ARM64_REG_X16,
        GUM_ADDRESS (target));
    gum_arm64_writer_put_ret_reg (&aw, ARM64_REG_X16);
  }

  gum_arm64_writer_flush (&aw);
  gum_arm64_writer_clear (&aw);

  gum_code_allocator_commit (&gum_bti_stub_allocator);

  stub = slice->pc;

  g_hash_table_insert (gum_bti_stub_cache, target, stub);

  G_UNLOCK (gum_bti_stub_cache);

  return stub;
}
#endif

#include <string.h>"""

CALL_OLD = "      ffi_call (cif, implementation, rvalue, avalue);"

CALL_NEW = """#ifdef HAVE_ARM64
      ffi_call (cif, GUM_POINTER_TO_FUNCPTR (GCallback,
          gum_arm64_get_bti_safe_call_target (
              GUM_FUNCPTR_TO_POINTER (implementation))),
          rvalue, avalue);
#else
      ffi_call (cif, implementation, rvalue, avalue);
#endif"""

SUBS = [INCLUDE_OLD, CALL_OLD]


def patch(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()

    if MARKER in text:
        print(f"skip {path}: NativeFunction BTI stub already present")
        return True

    missing = [old.splitlines()[0] for old in SUBS if old not in text]
    if missing:
        print(f"FATAL: {path} does not match the expected ffi_call layout; "
              f"anchors not found: {missing}", file=sys.stderr)
        return False

    text = text.replace(INCLUDE_OLD, INCLUDE_NEW, 1)
    text = text.replace(CALL_OLD, CALL_NEW, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"patched {path}: NativeFunction calls now route through a "
          f"BTI-safe stub on arm64")
    return True


def main():
    if len(sys.argv) < 2:
        print("usage: patch-gum-bti-nativefunction.py <gumquickcore.c>",
              file=sys.stderr)
        return 1
    ok = True
    for path in sys.argv[1:]:
        ok = patch(path) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
