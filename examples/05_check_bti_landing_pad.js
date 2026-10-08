// Verified working, read-only, zero crash risk: checks whether a kernel
// function has a BTI landing pad before you ever try to call or hook it
// with NativeFunction / Interceptor.
//
// Why this matters: on this fork's test kernel (LTO + CONFIG_ARM64_BTI_KERNEL),
// almost every ordinary kernel function - including _printk, gic_handle_irq,
// get_random_u32, every IRQ handler we sampled - has had its BTI landing pad
// stripped by the compiler's whole-program analysis, because nothing *inside*
// the kernel ever calls them indirectly. Frida's NativeFunction/Interceptor
// calls them via an indirect branch (BLR/BR) from *outside* that analysis,
// which the CPU traps in hardware the instant the branch lands - an instant,
// silent kernel panic, before a single instruction of the "called" function
// runs. See USAGE.md's "Why NativeFunction/Interceptor crash" section.
//
// Usage: edit NAMES below, then:
//   frida-inject -D barebone -p 0 -s 05_check_bti_landing_pad.js

const NAMES = ["_printk", "panic", "get_random_u32"];

const LANDING_PADS = {
  0xd503241f: "BTI",
  0xd503245f: "BTI C",
  0xd503249f: "BTI J",
  0xd50324df: "BTI JC",
};

function firstWord(addr) {
  return (addr.add(3).readU8() << 24 | addr.add(2).readU8() << 16 |
          addr.add(1).readU8() << 8  | addr.readU8()) >>> 0;
}

const vm = Process.getModuleByName("vmlinux");
const exportsByName = new Map(vm.enumerateExports().map(e => [e.name, e]));

for (const name of NAMES) {
  const e = exportsByName.get(name);
  if (!e) {
    console.log(name + ": not found");
    continue;
  }
  const w = firstWord(e.address);
  const kind = LANDING_PADS[w];
  if (kind !== undefined) {
    console.log(name + " @ " + e.address + " -> " + kind +
        "  (has a landing pad - safe to call/hook)");
  } else if (w === 0xd503233f) {
    console.log(name + " @ " + e.address + " -> PACIASP, no BTI" +
        "  (NO landing pad - calling/hooking this WILL crash the kernel)");
  } else {
    console.log(name + " @ " + e.address + " -> 0x" + w.toString(16) +
        "  (unrecognized first word - don't assume this is safe either)");
  }
}
