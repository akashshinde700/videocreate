// fixtures.js — chromium launch helper
//
// Strategy (sandbox/CI friendly):
//  1. Playwright-managed browser installed hai (npx playwright install chromium)
//     → default launch.
//  2. Nahi hai + Linux + @sparticuz/chromium available
//     → npm se aaya chromium binary use karo (LD_LIBRARY_PATH al2023 libs ke saath).
//  3. System chrome/chromium mila → executablePath.
const fs = require("fs");
const os = require("os");
const path = require("path");

async function resolveLaunchOptions(chromium) {
  // 1) playwright-managed
  try {
    const exe = chromium.executablePath();
    if (exe && fs.existsSync(exe)) return {};
  } catch {
    /* registry missing → try fallbacks */
  }

  // 3) system chrome
  const sys = ["/usr/bin/chromium", "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]
    .find((p) => fs.existsSync(p));
  if (sys) return { executablePath: sys, args: ["--no-sandbox"] };

  // 2) sparticuz fallback (Linux only)
  if (os.platform() === "linux") {
    try {
      const m = require("@sparticuz/chromium");
      const sparticuz = m.default || m;
      // NSS/GLib libs extract karo (agar missing hain)
      const libDir = path.join(os.tmpdir(), "al2023", "lib");
      if (!fs.existsSync(libDir)) {
        try {
          const binDir = path.join(path.dirname(require.resolve("@sparticuz/chromium/package.json")), "bin");
          if (fs.existsSync(path.join(binDir, "al2023.tar.br"))) {
            await sparticuz.inflate(path.join(binDir, "al2023.tar.br"));
          }
        } catch { /* al2023 optional ho sakte hain */ }
      }
      const existing = process.env.LD_LIBRARY_PATH || "";
      if (fs.existsSync(libDir) && !existing.includes(libDir)) {
        process.env.LD_LIBRARY_PATH = existing ? `${libDir}:${existing}` : libDir;
      }
      const exePath = await sparticuz.executablePath();
      // sparticuz lambda args me --single-process/--no-zygote hote hain —
      // normal containers me unstable (browser first page close pe mar jaata
      // hai). Unhe hatao — baaki hardening args rehte hain.
      const UNSAFE = new Set(["--single-process", "--no-zygote", "--disable-dev-shm-usage"]);
      const args = (sparticuz.args || []).filter((a) => !UNSAFE.has(a));
      return { executablePath: exePath, args, headless: true };
    } catch (e) {
      throw new Error(
        "Koi chromium nahi mila. `npx playwright install chromium` chalao " +
        `ya @sparticuz/chromium install karo. (${e.message})`);
    }
  }
  throw new Error("Koi chromium nahi mila — `npx playwright install chromium` chalao.");
}

async function launch(chromium) {
  const options = await resolveLaunchOptions(chromium);
  return chromium.launch({ headless: true, ...options });
}

module.exports = { resolveLaunchOptions, launch };
