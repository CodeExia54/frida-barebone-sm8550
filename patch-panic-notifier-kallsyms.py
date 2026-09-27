#!/usr/bin/env python3
"""Resolve panic_notifier_list via kallsyms instead of a direct extern.

Reproduced on GitHub Actions building for android13-5.15 and android14-6.1
(and, since it's the same header content, expected on every generic ddk
target except whichever one this repo's original device-pinned build used):
"use of undeclared identifier 'panic_notifier_list'". The object still
exists in the kernel (defined in kernel/panic.c) - it is just not declared
in these branches' installed headers for out-of-tree modules, unlike
whatever headers the original SM8550-pinned build saw.

panic_notifier_list is a *data* symbol, not a function, so it cannot be
resolved with frida-kmod.c's own kprobe-on-the-symbol-directly trick
(register_kprobe requires a valid instruction address to arm a breakpoint
on - a data object's bytes are not that). Instead this replicates
frida-kmod.c's actual two-step pattern: kprobe a real function
(kallsyms_lookup_name itself, which stopped being exported after 5.7) to
get a working symbol resolver, then use *that* to look up the data symbol
by name - the same thing frida-kmod.c does to resolve `modules` and
`module_mutex`, both data symbols too.
"""
import sys

OLD = """static struct notifier_block die_nb = {
    .notifier_call = die_handler,
};
static struct notifier_block panic_nb = {
    .notifier_call = panic_handler,
};

static int __init panic_capture_init(void)
{
    dump_file = filp_open(DUMP_PATH, O_CREAT | O_WRONLY | O_APPEND, 0644);
    if (IS_ERR(dump_file)) {
        pr_err("panic_capture: cannot open %s (err %ld)\\n", DUMP_PATH,
               PTR_ERR(dump_file));
        dump_file = NULL;
    } else {
        pr_info("panic_capture: dump file %s opened\\n", DUMP_PATH);
    }

    register_die_notifier(&die_nb);
    atomic_notifier_chain_register(&panic_notifier_list, &panic_nb);
    pr_info("panic_capture: installed (die + panic notifiers)\\n");
    return 0;
}

static void __exit panic_capture_exit(void)
{
    atomic_notifier_chain_unregister(&panic_notifier_list, &panic_nb);
    unregister_die_notifier(&die_nb);
    if (dump_file && !IS_ERR(dump_file))
        filp_close(dump_file, NULL);
    pr_info("panic_capture: removed\\n");
}"""

NEW = """static struct notifier_block die_nb = {
    .notifier_call = die_handler,
};
static struct notifier_block panic_nb = {
    .notifier_call = panic_handler,
};

/* panic_notifier_list still exists in the kernel (kernel/panic.c) but is not
 * declared in every GKI branch's installed headers - resolve it via
 * kallsyms instead of an extern, same two-step frida-kmod.c uses: kprobe a
 * real function (kallsyms_lookup_name, unexported since 5.7) to bootstrap a
 * working resolver, then look up the data symbol by name. */
#include <linux/kprobes.h>

static struct atomic_notifier_head *panic_notifier_list_p;

static void *panic_capture_resolve_unexported(const char *name)
{
    struct kprobe kp = { .symbol_name = name };
    void *address;

    if (register_kprobe(&kp) < 0)
        return NULL;
    address = kp.addr;
    unregister_kprobe(&kp);
    return address;
}

static int __init panic_capture_init(void)
{
    unsigned long (*kallsyms_lookup_name_fn)(const char *);

    kallsyms_lookup_name_fn = panic_capture_resolve_unexported("kallsyms_lookup_name");
    if (!kallsyms_lookup_name_fn) {
        pr_err("panic_capture: kallsyms_lookup_name unavailable\\n");
        return -ENOENT;
    }
    panic_notifier_list_p = (struct atomic_notifier_head *)
        kallsyms_lookup_name_fn("panic_notifier_list");
    if (!panic_notifier_list_p) {
        pr_err("panic_capture: panic_notifier_list not found\\n");
        return -ENOENT;
    }

    dump_file = filp_open(DUMP_PATH, O_CREAT | O_WRONLY | O_APPEND, 0644);
    if (IS_ERR(dump_file)) {
        pr_err("panic_capture: cannot open %s (err %ld)\\n", DUMP_PATH,
               PTR_ERR(dump_file));
        dump_file = NULL;
    } else {
        pr_info("panic_capture: dump file %s opened\\n", DUMP_PATH);
    }

    register_die_notifier(&die_nb);
    atomic_notifier_chain_register(panic_notifier_list_p, &panic_nb);
    pr_info("panic_capture: installed (die + panic notifiers)\\n");
    return 0;
}

static void __exit panic_capture_exit(void)
{
    if (panic_notifier_list_p)
        atomic_notifier_chain_unregister(panic_notifier_list_p, &panic_nb);
    unregister_die_notifier(&die_nb);
    if (dump_file && !IS_ERR(dump_file))
        filp_close(dump_file, NULL);
    pr_info("panic_capture: removed\\n");
}"""


def main():
    if len(sys.argv) < 2:
        print("usage: patch-panic-notifier-kallsyms.py <panic_capture.c>", file=sys.stderr)
        return 1
    ok = True
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if OLD not in text:
            print(f"ERROR: pattern not found in {path}", file=sys.stderr)
            ok = False
            continue
        text = text.replace(OLD, NEW, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"patched {path}: panic_notifier_list resolved via kallsyms")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
