import { defineConfig, devices } from "@playwright/test";

const visualServerPort = 1427;
const visualServerUrl = `http://127.0.0.1:${visualServerPort}`;

export default defineConfig({
  testDir: "./tests",
  outputDir: "test-results",
  // Screenshot assertions share one local Vite/WebGL/font-rendering budget.
  // Keep the parity suite serial: the Settings connectivity fixture contains
  // progressive status cards, and parallel workers can capture different
  // compositor frames even after the DOM reaches the same terminal state.
  workers: 1,
  fullyParallel: false,
  retries: process.env.CI === "true" ? 1 : 0,
  reporter: process.env.CI === "true" ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: visualServerUrl,
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "light",
    screenshot: "only-on-failure",
  },
  expect: {
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      maxDiffPixelRatio: 0.0075,
    },
  },
  webServer: {
    // Never borrow a developer server: it may belong to a different worktree
    // and silently refresh this branch's baseline with stale UI output.
    command: `pnpm --dir ../.. --filter @nimo/desktop exec vite --host 127.0.0.1 --port ${visualServerPort} --strictPort`,
    url: visualServerUrl,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
