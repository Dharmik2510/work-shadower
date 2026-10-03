// Minimal end-to-end smoke test against a running Work Shadower (real backend or mock).
//   BASE_URL=http://localhost:8000 node e2e/smoke.mjs
// Env: BASE_URL (default http://localhost:5173), SMOKE_EMAIL, SMOKE_NAME, SMOKE_TEAM,
//      SMOKE_QUERY (search text, default "claim"), HEADED=1 to watch it run.
// Requires AUTH_MODE=dev on the server. Exits non-zero on failure.
import { chromium } from "playwright";

const BASE = (process.env.BASE_URL ?? "http://localhost:5173").replace(/\/$/, "");
const QUERY = process.env.SMOKE_QUERY ?? "claim";
const t0 = Date.now();
const log = (m) => console.log(`[${((Date.now() - t0) / 1000).toFixed(1)}s] ${m}`);

const browser = await chromium.launch({ headless: !process.env.HEADED });
const page = await browser.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));

try {
  log(`open ${BASE}/login`);
  await page.goto(`${BASE}/login`);
  await page.waitForSelector('input[type="email"]', { timeout: 15000 }).catch(() => {
    throw new Error("Dev-login form not found. Is the server running with AUTH_MODE=dev?");
  });
  await page.fill('input[type="email"]', process.env.SMOKE_EMAIL ?? "smoke@example.com");
  await page.fill('input[autocomplete="name"]', process.env.SMOKE_NAME ?? "Smoke Test");
  if (process.env.SMOKE_TEAM) await page.fill('input[placeholder^="e.g."]', process.env.SMOKE_TEAM);
  await page.click('button:has-text("Sign in")');
  await page.waitForSelector("#ask", { timeout: 15000 });
  log("signed in, library loaded");

  await page.fill("#ask", QUERY);
  await page.waitForFunction(() => !document.querySelector(".filters__count")?.textContent?.includes("Searching"), null, { timeout: 15000 });
  await page.waitForTimeout(400);
  const hits = await page.locator(".skill").count();
  log(`search "${QUERY}" returned ${hits} result(s)`);

  if (hits === 0) {
    const empty = await page.locator(".empty").count();
    if (!empty) throw new Error("No results and no empty state shown");
    log("no published skills match; empty state rendered (seed a skill to test detail)");
  } else {
    const first = page.locator(".skill__title a").first();
    const title = (await first.textContent())?.trim();
    await first.click();
    await page.waitForSelector(".detail__head h1", { timeout: 15000 });
    await page.waitForSelector(".steps .step", { timeout: 15000 });
    const steps = await page.locator(".steps .step").count();
    log(`opened "${title}" with ${steps} step(s)`);
    if (!(await page.locator('button:has-text("Do it for me on my Mac")').count())) throw new Error("Run button missing");
  }
  if (errors.length) throw new Error("Page errors: " + errors.join("; "));
  log("SMOKE OK");
} catch (e) {
  console.error("SMOKE FAILED:", e.message);
  await page.screenshot({ path: "smoke-failure.png", fullPage: true }).catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
}
