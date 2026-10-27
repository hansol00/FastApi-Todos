// Playwright Test Agents(planner·generator·healer)가 앱을 탐색할 때 출발점이 되는 시드 테스트.
// - planner는 이 테스트를 먼저 실행해 앱을 "사용 가능한 상태"로 만든 뒤 탐색을 시작한다.
// - generator는 이 파일을 새 테스트의 스타일 예시로 삼는다.
// 그래서 여기에는 공통 준비 동작(첫 화면 진입 + 목록 로드 완료 대기)만 둔다.
import { test, expect } from "@playwright/test";

test.describe("seed", () => {
  test("seed", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#count")).toHaveText(/전체 \d+개/);  // 첫 목록 로드 완료
  });
});
