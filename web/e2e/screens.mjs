// Screenshots every screen in light & dark at 1280px and 390px.
// Usage: start `npm run dev:mock` (or any server) then
//   BASE_URL=http://localhost:5173 node e2e/screens.mjs [filter]
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const OUT = new URL("../screenshots/", import.meta.url).pathname;
const filter = process.argv[2];
mkdirSync(OUT, { recursive: true });

const FNOL = "5f1c0e2a-0001-4000-8000-000000000001";
const RENTAL = "5f1c0e2a-0005-4000-8000-000000000005";

const screens = [
  { name: "01-login", path: "/login", auth: false, wait: "form, .login__sso" },
  { name: "02-library", path: "/", wait: ".skill" },
  { name: "03-library-search", path: "/?q=claim", wait: ".skill" },
  { name: "04-library-no-results", path: "/?q=renew+commercial+fleet", wait: ".empty" },
  { name: "05-skill-detail", path: `/skills/${FNOL}`, wait: ".step img", delay: 900 },
  { name: "06-editor-draft", path: `/skills/${RENTAL}/edit`, wait: ".ed-step img", delay: 900, act: async (p) => {
      await p.fill(".ed-sec input >> nth=0", "Book a rental car for a claimant (OPCF 20)");
    } },
  { name: "07-new-skill", path: "/skills/new", wait: ".ed-sec" },
  { name: "08-recordings", path: "/recordings", wait: ".rec" },
  { name: "09-runs", path: `/skills/${FNOL}/runs`, wait: ".runs tbody tr", act: async (p) => {
      await p.click(".runs__toggle button >> nth=1");
    } },
  { name: "10-admin", path: "/admin", wait: ".tile", delay: 400 },
];

const browser = await chromium.launch();
for (const theme of ["light", "dark"]) {
  for (const width of [1280, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: width > 500 ? 860 : 844 }, colorScheme: theme, deviceScaleFactor: width > 500 ? 1 : 2 });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => console.error("pageerror", e.message));
    // sign in once per context via the dev form
    await page.goto(BASE + "/login");
    await page.fill('input[type="email"]', "dharmik@example.com");
    await page.fill('input[autocomplete="name"]', "Dharmik");
    await page.click('button:has-text("Sign in")');
    await page.waitForURL((u) => !u.pathname.startsWith("/login"));
    for (const s of screens) {
      if (filter && !s.name.includes(filter)) continue;
      let p = page;
      let tmp;
      if (s.auth === false) {
        tmp = await browser.newContext({ viewport: { width, height: width > 500 ? 860 : 844 }, colorScheme: theme, deviceScaleFactor: width > 500 ? 1 : 2 });
        p = await tmp.newPage();
      }
      await p.goto(BASE + s.path);
      await p.waitForSelector(s.wait, { timeout: 10000 }).catch(() => console.warn("wait timeout", s.name));
      if (s.act) await s.act(p);
      await p.waitForTimeout(s.delay ?? 250);
      const file = `${OUT}${s.name}-${theme}-${width}.png`;
      await p.screenshot({ path: file, fullPage: true });
      console.log("saved", file);
      if (tmp) await tmp.close();
    }
    await ctx.close();
  }
}
await browser.close();
