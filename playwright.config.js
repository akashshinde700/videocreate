// Playwright config — Web Studio e2e tests
// Browsers: default playwright-managed chromium, ya (jab download na ho)
// tests/e2e/fixtures.js ka sparticuz fallback automatically use hota hai.
module.exports = {
  testDir: "tests/e2e",
  timeout: 240000, // render job test lamba ho sakta hai
  expect: { timeout: 15000 },
  workers: 1, // server ek waqt me ek render karta hai
  retries: 0,
  outputDir: "test-results",
  use: {
    baseURL: "http://localhost:8788",
    trace: "retain-on-failure",
    viewport: { width: 1360, height: 900 },
  },
  webServer: {
    command: "python3 app.py",
    url: "http://localhost:8788/api/health",
    reuseExistingServer: true,
    timeout: 30000,
    env: { PORT: "8788" },
  },
};
