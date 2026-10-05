// To-Do 화면 E2E 테스트 — 팀 공용 서버에서 돌기 때문에 다음 규칙을 지킨다.
//  - 테스트마다 uuid(tag)를 만들어 제목에 붙이고, 그 tag가 들어간 항목만 조작한다.
//  - "첫 번째 항목"·"모든 항목" 같은 선택자는 쓰지 않는다 (항상 tag로 특정한 li 하나).
//  - afterEach에서 tag가 붙은 항목을 API로 지운다 (UI 단계가 실패해도 정리되도록).
import { randomUUID } from "node:crypto";
import { test, expect, type Page, type Locator } from "@playwright/test";

const APP_VERSION = "3.0.0";
const PAST_DATE = "2020-01-01";
const FUTURE_DATE = "2099-12-31";

type Todo = { id: number; title: string; description: string; completed: boolean; due_date: string | null };

let tag: string;                                   // 이 테스트가 만든 항목 표식
let dialogs: { type: string; message: string }[];  // 뜬 다이얼로그 기록
let promptAnswers: string[];                       // prompt()에 차례로 넣을 값

// tag 하나로 특정되는 li — 같은 제목이 둘 이상이면 strict mode 위반으로 실패한다
const itemOf = (page: Page, title: string): Locator =>
  page.locator("#todo-list li").filter({ hasText: title });

async function readCount(page: Page) {
  const text = (await page.locator("#count").textContent()) ?? "";
  const m = text.match(/남은 할 일 (\d+)개 \/ 전체 (\d+)개/);
  if (!m) throw new Error(`카운터 형식이 예상과 다름: "${text}"`);
  return { remaining: Number(m[1]), total: Number(m[2]) };
}

async function addTodo(page: Page, todo: { title: string; description?: string; due?: string }) {
  await page.locator("#title").fill(todo.title);
  await page.locator("#description").fill(todo.description ?? "");
  await page.locator("#due_date").fill(todo.due ?? "");
  await page.getByRole("button", { name: "Add To-Do" }).click();
  await expect(itemOf(page, todo.title)).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  tag = randomUUID();
  dialogs = [];
  promptAnswers = [];

  // 리스너가 없으면 Playwright가 다이얼로그를 자동 취소(dismiss)한다 → 삭제·수정이 동작하지 않음
  page.on("dialog", async (dialog) => {
    dialogs.push({ type: dialog.type(), message: dialog.message() });
    if (dialog.type() === "prompt") {
      await dialog.accept(promptAnswers.shift() ?? dialog.defaultValue());
    } else {
      await dialog.accept();
    }
  });

  await page.goto("/");
  await expect(page.locator("#count")).toHaveText(/전체 \d+개/);  // 첫 목록 로드 완료
});

test.afterEach(async ({ request }) => {
  const res = await request.get("/todos");
  expect(res.ok()).toBeTruthy();
  const mine = ((await res.json()) as Todo[]).filter(
    (t) => t.title.includes(tag) || t.description.includes(tag));
  for (const t of mine) {
    const del = await request.delete(`/todos/${t.id}`);
    expect([204, 404]).toContain(del.status());    // 404 = 테스트가 이미 지움
  }
});

test("페이지 로드 — 제목, 버전, 서버 상태 배지", async ({ page }) => {
  await expect(page).toHaveTitle("To-Do List");
  const heading = page.getByRole("heading", { level: 1 });
  await expect(heading).toContainText("To-Do List");
  await expect(heading.locator("small")).toHaveText(`v${APP_VERSION}`);

  const badge = page.locator("#health");
  await expect(badge).toHaveClass("ok");
  await expect(page.locator("#health-text")).toHaveText(
    new RegExp(`^정상 · v${APP_VERSION.replace(/\./g, "\\.")} · \\d+s$`));
});

test("할 일 추가 — 목록에 나타나고 카운터 증가", async ({ page }) => {
  const title = `e2e-${tag}`;
  const before = await readCount(page);

  await addTodo(page, { title, description: "추가 테스트", due: FUTURE_DATE });

  const item = itemOf(page, title);
  await expect(item.locator(":scope > span")).toHaveText(`${title} — 추가 테스트마감 ${FUTURE_DATE}`);
  await expect(item.getByRole("checkbox")).not.toBeChecked();
  await expect(page.locator("#title")).toHaveValue("");        // 폼 초기화
  expect(await readCount(page)).toEqual({ remaining: before.remaining + 1, total: before.total + 1 });
});

test("필터 — 전체/진행 중/완료", async ({ page }) => {
  const activeTitle = `e2e-${tag}-active`;
  const doneTitle = `e2e-${tag}-done`;
  await addTodo(page, { title: activeTitle });
  await addTodo(page, { title: doneTitle });

  await itemOf(page, doneTitle).getByRole("checkbox").check();
  await expect(itemOf(page, doneTitle).locator(":scope > span")).toHaveClass("done");

  const filterBtn = (name: string) => page.locator("#toolbar").getByRole("button", { name, exact: true });

  await filterBtn("진행 중").click();
  await expect(filterBtn("진행 중")).toHaveAttribute("aria-pressed", "true");
  await expect(itemOf(page, activeTitle)).toBeVisible();
  await expect(itemOf(page, doneTitle)).toHaveCount(0);

  await filterBtn("완료").click();
  await expect(filterBtn("완료")).toHaveAttribute("aria-pressed", "true");
  await expect(itemOf(page, doneTitle)).toBeVisible();
  await expect(itemOf(page, activeTitle)).toHaveCount(0);

  await filterBtn("전체").click();
  await expect(filterBtn("전체")).toHaveAttribute("aria-pressed", "true");
  await expect(itemOf(page, activeTitle)).toBeVisible();
  await expect(itemOf(page, doneTitle)).toBeVisible();
});

test("삭제 — confirm 수락 후 목록에서 사라지고 카운터 감소", async ({ page }) => {
  const title = `e2e-${tag}`;
  await addTodo(page, { title });
  const before = await readCount(page);

  await itemOf(page, title).getByRole("button", { name: "Delete" }).click();

  await expect(itemOf(page, title)).toHaveCount(0);
  expect(dialogs).toEqual([{ type: "confirm", message: "정말 삭제할까요?" }]);
  expect(await readCount(page)).toEqual({ remaining: before.remaining - 1, total: before.total - 1 });
});

test("빈 제목으로 Add — 추가되지 않음", async ({ page }) => {
  const before = await readCount(page);
  let posts = 0;
  page.on("request", (r) => { if (r.method() === "POST" && r.url().endsWith("/todos")) posts++; });

  // 1) 완전히 빈 제목: required 속성 때문에 브라우저가 제출 자체를 막는다
  await page.locator("#description").fill(tag);
  await page.getByRole("button", { name: "Add To-Do" }).click();
  expect(await page.locator("#title").evaluate((el: HTMLInputElement) => el.validity.valueMissing)).toBe(true);
  await page.waitForTimeout(500);                  // 요청이 나가지 않는다는 것을 확인할 여유
  expect(posts).toBe(0);

  // 2) 공백만 있는 제목: 화면이 trim 후 보내므로 서버가 422로 거절한다
  await page.locator("#title").fill("   ");
  await page.getByRole("button", { name: "Add To-Do" }).click();
  await expect(page.locator("#error")).toBeVisible();
  await expect(page.locator("#error")).toContainText("추가 실패: 422");

  await expect(itemOf(page, tag)).toHaveCount(0);
  expect(await readCount(page)).toEqual(before);
});

test("지난 마감일 — ⚠ 기한 지남 표시", async ({ page }) => {
  const pastTitle = `e2e-${tag}-past`;
  const futureTitle = `e2e-${tag}-future`;
  await addTodo(page, { title: pastTitle, due: PAST_DATE });
  await addTodo(page, { title: futureTitle, due: FUTURE_DATE });

  const past = itemOf(page, pastTitle);
  await expect(past.locator(":scope > span")).toHaveClass("overdue");
  await expect(past.locator("small.due")).toHaveText(`⚠ 마감 ${PAST_DATE} (기한 지남)`);

  const future = itemOf(page, futureTitle);
  await expect(future.locator(":scope > span")).not.toHaveClass("overdue");
  await expect(future.locator("small.due")).toHaveText(`마감 ${FUTURE_DATE}`);

  // 완료 처리하면 기한이 지났어도 경고를 지운다
  await past.getByRole("checkbox").check();
  await expect(past.locator(":scope > span")).toHaveClass("done");
  await expect(past.locator("small.due")).toHaveText(`마감 ${PAST_DATE}`);
});

test("수정 — prompt 3회 입력이 반영됨", async ({ page }) => {
  const oldTitle = `e2e-${tag}-before`;
  const newTitle = `e2e-${tag}-after`;
  await addTodo(page, { title: oldTitle, description: "old" });

  promptAnswers = [newTitle, "new", FUTURE_DATE];
  await itemOf(page, oldTitle).getByRole("button", { name: "Edit" }).click();

  await expect(itemOf(page, newTitle).locator(":scope > span")).toHaveText(`${newTitle} — new마감 ${FUTURE_DATE}`);
  await expect(itemOf(page, oldTitle)).toHaveCount(0);
  expect(dialogs.map((d) => d.type)).toEqual(["prompt", "prompt", "prompt"]);
});
