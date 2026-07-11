import { chromium } from "playwright";

// launch the user's real Edge in visible (headed) mode
const browser = await chromium.launch({
  channel: "msedge",
  headless: false,
});
const ctx = await browser.newContext({ viewport: null }); // null = use real window size
const page = await ctx.newPage();
await page.goto("http://127.0.0.1:8000", { waitUntil: "domcontentloaded" });

console.log("Edge opened at http://127.0.0.1:8000 — leaving the window open for you.");
console.log("Close the browser window when you're done. This script will stay alive until then.");

// keep the script alive so the browser doesn't close
await new Promise(() => {});
