// Verified working: Memory.alloc + NativePointer instance read/write + hexdump.
// Note: the STATIC free functions Memory.readU64/writeByteArray/readPointer etc.
// are NOT implemented on this backend (TypeError: not a function) - use the
// ptr.readXxx()/writeXxx() instance methods instead, as below.
var buf = Memory.alloc(64);
console.log("alloc -> " + buf);
buf.writeU64(0x1122334455667788);
console.log("readU64 -> " + buf.readU64().toString(16));
buf.writeU8(0xab);
console.log("readU8 -> " + buf.readU8().toString(16));
console.log(hexdump(buf, { length: 16 }));
