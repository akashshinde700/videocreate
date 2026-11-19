// Vaanireel Web Studio — Playwright e2e specs
const { test: base, expect } = require("@playwright/test");
const { launch } = require("./fixtures");

// worker-scoped browser: ek launch, saare tests reuse (sparticuz chromium
// repeated launches me flaky hota hai — lambda-optimized build hai)
const test = base.test.extend({
  browser: [
    async ({ playwright }, use) => {
      const browser = await launch(playwright.chromium);
      await use(browser);
      await browser.close();
    },
    { scope: "worker" },
  ],
});

test.describe("Web Studio — static UI", () => {
  test("page load: title + form elements", async ({ browser }) => {
    const page = await browser.newPage();
    await page.goto("/");
    await expect(page).toHaveTitle("Vaanireel Web Studio");
    await expect(page.locator("#topic")).toBeVisible();
    await expect(page.locator("#btn-render")).toBeVisible();
    await expect(page.locator("#btn-doctor")).toBeVisible();
    await expect(page.locator("#btn-audit")).toBeVisible();
    // health pill ffmpeg OK dikhna chahiye
    await expect(page.locator("#ff-pill")).toHaveText(/ffmpeg ✓/, { timeout: 10000 });
    await page.close();
  });

  test("empty topic → validation warning, koi job nahi", async ({ browser }) => {
    const page = await browser.newPage();
    await page.goto("/");
    await page.click("#btn-render");
    await expect(page.locator("#log")).toContainText("Topic likho pehle");
    await page.close();
  });
});

test.describe("Web Studio — doctor & audit", () => {
  test("doctor panel: essential checks ke saath table", async ({ browser }) => {
    const page = await browser.newPage();
    await page.goto("/");
    await page.click("#btn-doctor");
    const rows = page.locator("#doctor-table tr");
    await expect(rows.first()).toBeVisible();
    const count = await rows.count();
    test.expect(count).toBeGreaterThanOrEqual(10);
    await expect(page.locator("#doctor-table")).toContainText("FFmpeg");
    await expect(page.locator("#doctor-table")).toContainText("Python");
    await page.close();
  });

  test("audit panel: exactly 17 repos", async ({ browser }) => {
    const page = await browser.newPage();
    await page.goto("/");
    await page.click("#btn-audit");
    await expect(page.locator("#audit-table")).toContainText("FFmpeg/FFmpeg");
    // header row + 17 data rows
    await expect(page.locator("#audit-table tr")).toHaveCount(18);
    await page.close();
  });
});

test.describe("Web Studio — render flow (silent stub engine)", () => {
  test("full render: job start → done → video + report + tips", async ({ browser }) => {
    test.setTimeout(240000);
    const page = await browser.newPage();
    await page.goto("/");

    await page.fill("#topic", "playwright e2e reel");
    await page.selectOption("#lang", "hi");
    await page.selectOption("#engine", "silent");
    await page.selectOption("#visual", "cards");
    await page.selectOption("#size", "horizontal");
    await page.check("#captions");
    await page.uncheck("#music");
    await page.click("#btn-render");

    // progress aage badhna chahiye
    await expect(page.locator("#log")).toContainText("[1/6]", { timeout: 20000 });

    // done state: result panel + video + tips
    const result = page.locator("#result");
    await expect(result).toBeVisible({ timeout: 180000 });
    await expect(page.locator("#risk")).toContainText(/slideshow risk: (HIGH|LOW)/i);
    await expect(page.locator("#report-meta")).toContainText("playwright e2e reel");
    await expect(page.locator("#tips li").first()).toBeVisible();

    // scene thumbnails render hue
    await expect(page.locator("#thumbs img")).toHaveCount(3);

    // video element source set + playable metadata
    const video = page.locator("#video");
    const src = await video.getAttribute("src");
    test.expect(src).toMatch(/\/media\?job=.*&name=vaanireel_video\.mp4/);
    await expect(video).toHaveJSProperty("readyState", 4, { timeout: 30000 });
    const duration = await video.evaluate((el) => el.duration);
    test.expect(duration).toBeGreaterThan(2);
    test.expect(duration).toBeLessThan(60);
    await page.close();
  });

  test("render API: job status report schema artifact-jaisa", async ({ request }) => {
    const res = await request.post("/api/render", {
      data: { topic: "schema test", engine: "silent", scenes: 3, music: false, captions: true },
    });
    test.expect(res.status()).toBe(200);
    const { id } = await res.json();
    test.expect(id).toMatch(/^job_/);

    // poll till done (max 120s)
    let report = null;
    for (let i = 0; i < 60; i++) {
      const s = await request.get(`/api/status?id=${id}`);
      const data = await s.json();
      if (data.state === "done") { report = data.report; break; }
      if (data.state === "error") throw new Error(`render error: ${data.error}`);
      await new Promise((r) => setTimeout(r, 2000));
    }
    test.expect(report).toBeTruthy();
    // artifact-compatible schema
    for (const key of ["tool", "version", "topic", "lang", "voice_engine", "visual_mode",
      "scenes", "output", "quality", "hardware_note"]) {
      test.expect(report).toHaveProperty(key);
    }
    test.expect(report.voice_engine).toBe("silent");
    test.expect(report.scenes).toHaveLength(3);
    for (const sc of report.scenes) {
      for (const k of ["idx", "title", "voice", "voice_s", "dur_s", "kind", "visual"]) {
        test.expect(sc).toHaveProperty(k);
      }
    }
    test.expect(report.quality).toHaveProperty("total_s");
    test.expect(report.quality).toHaveProperty("slideshow_risk");
    test.expect(Array.isArray(report.quality.tips)).toBe(true);
  });
});

test.describe("Web Studio — security", () => {
  test("media path traversal blocked (400)", async ({ request }) => {
    const r1 = await request.get("/media?job=fake&name=../app.py");
    test.expect(r1.status()).toBe(400);
    const r2 = await request.get("/media?job=fake&name=..%2Fapp.py");
    test.expect(r2.status()).toBe(400);
    const r3 = await request.get("/media?job=fake&name=package.json");
    test.expect(r3.status()).toBe(400); // allowlist ke bahar
  });

  test("unknown job id → 404", async ({ request }) => {
    const r = await request.get("/api/status?id=job_does_not_exist");
    test.expect(r.status()).toBe(404);
  });

  test("render API: invalid body → 400", async ({ request }) => {
    const r1 = await request.post("/api/render", { data: { topic: "" } });
    test.expect(r1.status()).toBe(400);
    const r2 = await request.post("/api/render", { data: {} });
    test.expect(r2.status()).toBe(400);
  });
});
