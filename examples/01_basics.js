// Verified working: frida-inject -D barebone -p 0 -s 01_basics.js
console.log("Script.runtime=" + Script.runtime);
console.log("Process.arch=" + Process.arch);
console.log("Process.platform=" + Process.platform);
console.log("Process.pointerSize=" + Process.pointerSize);
console.log("Process.id=" + Process.id);
console.log("Process.getCurrentThreadId()=" + Process.getCurrentThreadId());
