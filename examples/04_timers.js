// Verified working: a real, concurrent event loop inside the kernel agent.
console.log("scheduling timers...");
setTimeout(function () { console.log("setTimeout fired"); }, 300);
var n = 0;
var id = setInterval(function () {
  n++;
  console.log("interval tick " + n);
  if (n >= 2) clearInterval(id);
}, 200);
