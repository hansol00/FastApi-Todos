// 실행: cd ui_tests && npm test            (리포트: npm run report)
// 대상 변경: BASE_URL=http://163.239.77.77:5011 npm test
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  // 팀 공용 서버의 카운터(남은/전체 개수)를 전후 비교하므로 테스트끼리 겹치지 않게 순차 실행
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],
  use: {
    baseURL: process.env.BASE_URL ?? "http://163.239.77.77:5012",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
