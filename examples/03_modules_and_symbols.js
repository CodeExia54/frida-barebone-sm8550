// Verified working: full kernel symbol table enumeration via kallsyms.
// Process.enumerateModules() lists every loaded .ko plus "vmlinux" itself.
// Module.findExportByName() (the static free function) is NOT implemented;
// resolve a symbol by getting the module object and calling its OWN
// enumerateExports(), then filtering - that path works and is fast enough
// for a one-off lookup even across ~190k kernel symbols.
var mods = Process.enumerateModules();
console.log("loaded modules: " + mods.length);
mods.slice(0, 5).forEach(function (m) {
  console.log("  " + m.name + " @ " + m.base + " (" + m.size + " bytes)");
});

var vmlinux = Process.getModuleByName("vmlinux");
var exports_ = vmlinux.enumerateExports();
console.log("vmlinux exports: " + exports_.length);

var printk = exports_.find(function (e) { return e.name === "_printk"; });
console.log("_printk resolved to " + (printk ? printk.address : "(not found)"));
// Do NOT call it via `new NativeFunction(printk.address, ...)` - see
// USAGE.md's feature matrix: calling into real kernel code this way panics
// this CFI-hardened kernel build instantly.
