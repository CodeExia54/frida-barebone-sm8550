#!/usr/bin/env python3
"""Back the hostlink kfifos with vmalloc() instead of kfifo_alloc()'s kmalloc().

Reproduced on a real device (not QEMU): insmod loads fine, but
frida_kmod_link_open() then fails -

    frida-agent: page allocation failure: order:8, mode:0x40cc0(GFP_KERNEL|__GFP_COMP)
    ...
    kfifo_alloc -> kmalloc_order -> __alloc_pages_slowpath
    frida_kmod_link_open+0x2c/0xbc [frida_agent]
    frida: failed to connect to peer

kfifo_alloc(fifo, FRIDA_LINK_CAPACITY, GFP_KERNEL) on a DECLARE_KFIFO_PTR
kmalloc()s the whole 1 MiB (FRIDA_LINK_CAPACITY) buffer as one
physically-contiguous, order-8 block. That is routinely unavailable on a
device that has been up for a while - plenty of *fragmented* free memory,
but no order-8 (1 MiB) run left in the buddy allocator - so /dev/frida
never gets created and the module is dead on arrival, config notwithstanding.

The fifo only needs to be virtually contiguous, so we vmalloc() the backing
buffer ourselves and hand it to kfifo_init() (the kfifo API's own documented
"I already have a buffer" entry point) instead of letting kfifo_alloc()
kmalloc() it. Every other kfifo_*() call site (kfifo_in/out, kfifo_to_user,
kfifo_reset, kfifo_is_empty, ...) is untouched - they operate on the same
struct kfifo metadata regardless of which allocator produced the buffer.
"""
import sys

MARKER = "frida_to_client_buf"

DECL_OLD = """static DECLARE_KFIFO_PTR (frida_to_client, u8);
static DECLARE_KFIFO_PTR (frida_from_client, u8);"""
DECL_NEW = """static DECLARE_KFIFO_PTR (frida_to_client, u8);
static DECLARE_KFIFO_PTR (frida_from_client, u8);
static void * frida_to_client_buf;
static void * frida_from_client_buf;"""

OPEN_OLD = """  res = kfifo_alloc (&frida_to_client, FRIDA_LINK_CAPACITY, GFP_KERNEL);
  if (res != 0)
    return res;

  res = kfifo_alloc (&frida_from_client, FRIDA_LINK_CAPACITY, GFP_KERNEL);
  if (res != 0)
    goto free_to_client;

  res = misc_register (&frida_dev);
  if (res != 0)
    goto free_from_client;
  frida_link_registered = true;

  printk (KERN_INFO "frida: listening on /dev/%s\\n", frida_dev.name);

  return 0;

free_from_client:
  kfifo_free (&frida_from_client);
free_to_client:
  kfifo_free (&frida_to_client);

  return res;
}"""
OPEN_NEW = """  frida_to_client_buf = vmalloc (FRIDA_LINK_CAPACITY);
  if (frida_to_client_buf == NULL)
    return -ENOMEM;
  kfifo_init (&frida_to_client, frida_to_client_buf, FRIDA_LINK_CAPACITY);

  frida_from_client_buf = vmalloc (FRIDA_LINK_CAPACITY);
  if (frida_from_client_buf == NULL) {
    res = -ENOMEM;
    goto free_to_client;
  }
  kfifo_init (&frida_from_client, frida_from_client_buf, FRIDA_LINK_CAPACITY);

  res = misc_register (&frida_dev);
  if (res != 0)
    goto free_from_client;
  frida_link_registered = true;

  printk (KERN_INFO "frida: listening on /dev/%s\\n", frida_dev.name);

  return 0;

free_from_client:
  vfree (frida_from_client_buf);
  frida_from_client_buf = NULL;
free_to_client:
  vfree (frida_to_client_buf);
  frida_to_client_buf = NULL;

  return res;
}"""

CLOSE_OLD = """  misc_deregister (&frida_dev);

  kfifo_free (&frida_from_client);
  kfifo_free (&frida_to_client);
}"""
CLOSE_NEW = """  misc_deregister (&frida_dev);

  vfree (frida_from_client_buf);
  frida_from_client_buf = NULL;
  vfree (frida_to_client_buf);
  frida_to_client_buf = NULL;
}"""

SUBS = [DECL_OLD, OPEN_OLD, CLOSE_OLD]


def patch(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()

    if MARKER in text:
        print(f"skip {path}: hostlink kfifos already vmalloc-backed")
        return True

    missing = [old.splitlines()[0] for old in SUBS if old not in text]
    if missing:
        print(f"FATAL: {path} does not match the expected frida_kmod_link_*() "
              f"layout; anchors not found: {missing}", file=sys.stderr)
        return False

    text = text.replace(DECL_OLD, DECL_NEW, 1)
    text = text.replace(OPEN_OLD, OPEN_NEW, 1)
    text = text.replace(CLOSE_OLD, CLOSE_NEW, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"patched {path}: hostlink kfifos now vmalloc-backed (no more order-8 kmalloc)")
    return True


def main():
    if len(sys.argv) < 2:
        print("usage: patch-kfifo-vmalloc.py <frida-kmod.c>", file=sys.stderr)
        return 1
    ok = True
    for path in sys.argv[1:]:
        ok = patch(path) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
