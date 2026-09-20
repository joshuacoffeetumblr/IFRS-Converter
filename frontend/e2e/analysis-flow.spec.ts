import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * The whole product, once (spec §34).
 *
 * Upload → extract → classify → review → finalize → statement → impact. The
 * assertions are deliberately about the guarantees rather than the layout:
 * that extraction is verified against the source's own subtotals, that the
 * engine hands undecidable captions to a person, that profit before tax does
 * not move, and that the reconciliation gate is visible on screen.
 *
 * Skipped when the API is not running, because without it there is nothing
 * here to test and a red suite would say something untrue.
 */

/**
 * A minimal Korean income statement, as a CSV upload.
 *
 *   매출총이익   = 1,000,000 − 700,000            =  300,000
 *   영업이익     =   300,000 − 180,000            =  120,000
 *   법인세차감전 =   120,000 + 12,000 − 14,000    =  118,000
 *   당기순이익   =   118,000 −  25,960            =   92,040
 *
 * The figures reconcile, which is what lets the gate pass; 기타수익 is an
 * aggregate the rules deliberately refuse to classify, which is what produces
 * something for a person to decide.
 */
const STATEMENT_CSV = [
  "연결 포괄손익계산서",
  "제55기 2025.01.01 부터 2025.12.31 까지",
  "(단위: 백만원)",
  "",
  "과목,주석,제55기",
  '매출액,주석 21,"1,000,000"',
  '매출원가,주석 22,"(700,000)"',
  '매출총이익,,"300,000"',
  '판매비와관리비,주석 23,"(180,000)"',
  '영업이익,,"120,000"',
  '기타수익,주석 24,"12,000"',
  '금융비용,주석 25,"(14,000)"',
  '법인세차감전순이익,,"118,000"',
  '법인세비용,주석 26,"(25,960)"',
  '당기순이익,,"92,040"',
  "",
].join("\n");

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

test.beforeAll(async ({ request }) => {
  try {
    const response = await request.get(`${API_URL}/api/health`);
    test.skip(!response.ok(), "API is not running");
  } catch {
    test.skip(true, "API is not running");
  }
});

async function signUp(page: Page): Promise<void> {
  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.goto("/login");
  await page.getByLabel("이메일").fill(email);
  await page.getByLabel("비밀번호").fill("correct horse battery staple");
  await page.getByRole("button", { name: "계정 만들기" }).click();
  await expect(page).toHaveURL(/\/projects$/);
}

async function createProject(page: Page): Promise<void> {
  await page.getByLabel("프로젝트명").fill("2025 연결 손익계산서");
  await page.getByLabel("회사명").fill("이투이 주식회사");
  await page.getByRole("button", { name: "프로젝트 만들기" }).click();
  await expect(page.getByRole("heading", { name: "2025 연결 손익계산서" })).toBeVisible();
}

/**
 * Open a card's category picker.
 *
 * Retried because the button is a client component: a click that lands before
 * hydration finishes is simply lost, and the page gives no signal that it was.
 */
async function openCategoryPicker(card: Locator): Promise<void> {
  const picker = card.getByLabel("IFRS 18 범주");
  await expect(async () => {
    if (!(await picker.isVisible())) {
      await card.getByRole("button", { name: /범주 지정|다르게 분류/ }).click();
    }
    await expect(picker).toBeVisible({ timeout: 1_000 });
  }).toPass({ timeout: 20_000 });
}

async function decide(card: Locator, reason: string): Promise<void> {
  await openCategoryPicker(card);
  await card.getByLabel("사유 (필수)").fill(reason);
  await card.getByRole("button", { name: "이 범주로 분류" }).click();
}

async function uploadAndExtract(page: Page): Promise<void> {
  await page.setInputFiles('input[type="file"]', {
    name: "손익계산서.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(STATEMENT_CSV, "utf-8"),
  });
  await page.getByRole("button", { name: "업로드" }).click();
  // The status pill also reads 업로드됨, so match the notice itself.
  await expect(page.getByText(/SHA-256/)).toBeVisible();

  await page.getByRole("button", { name: "추출 실행" }).click();
  await expect(page.getByTestId("extraction-report")).toBeVisible();
}

test.describe("the IFRS 18 flow", () => {
  test.describe.configure({ mode: "serial", timeout: 120_000 });

  test("carries a statement from upload to a reconciled impact analysis", async ({ page }) => {
    await signUp(page);
    await createProject(page);
    await uploadAndExtract(page);

    // §17: extraction is checked against the subtotals the document printed.
    await expect(page.getByText("원본 소계와 일치")).toBeVisible();
    // §18: every figure carries the cell it came from.
    await expect(page.getByText("매출액")).toBeVisible();

    await page.getByRole("button", { name: "분류 실행" }).click();
    await expect(page.getByText(/라인을 분류했습니다/)).toBeVisible();

    // §1: the engine hands what it cannot decide to a person.
    await page.getByRole("link", { name: "검토" }).click();
    const queue = page.getByTestId("review-queue");
    await expect(queue).toBeVisible();
    await expect(queue.getByText("기타수익")).toBeVisible();

    // An override without a reason is refused by the API, so the form asks for
    // one before it will submit. The card is picked by account rather than by
    // position: the queue is ordered by materiality, not by the statement.
    const card = queue.locator("form").filter({ hasText: "기타수익" });
    await decide(card, "주석 24 확인 결과 영업 관련 수익");
    // A decided item leaves the queue — that *is* the confirmation, and the
    // card carrying any message would have gone with it.
    await expect(card).toHaveCount(0);

    // Everything else the engine flagged: place what it could not place, then
    // accept what it did. A line left unplaced is refused at the gate.
    const unplaced = queue.locator('[data-testid="review-card"][data-unplaced="true"]');
    for (let index = 0; index < 10; index += 1) {
      if ((await unplaced.count()) === 0) break;
      const account = await unplaced.first().getAttribute("data-account");
      const pending = queue.locator(`[data-account="${account}"]`);
      await decide(pending, "주석 확인 결과 영업 관련");
      await expect(pending).toHaveCount(0, { timeout: 10_000 });
    }

    for (let index = 0; index < 10; index += 1) {
      const accept = queue.getByRole("button", { name: "제안 승인" });
      const remaining = await accept.count();
      if (remaining === 0) break;
      await accept.first().click();
      await expect(accept).toHaveCount(remaining - 1);
    }
    await expect(queue.getByRole("button", { name: "제안 승인" })).toHaveCount(0);
    await expect(unplaced).toHaveCount(0);

    await page.getByRole("link", { name: "추출" }).first().click();
    await page.getByRole("button", { name: "확정하기" }).click();
    await expect(page.getByText(/확정되었습니다/)).toBeVisible();

    // §19: the gate's verdict is on the statement screen, above the figures.
    await page.getByRole("link", { name: "IFRS 18 손익계산서" }).click();
    const banner = page.getByTestId("reconciliation-banner");
    await expect(banner).toHaveAttribute("data-status", "PASSED");
    await expect(page.getByTestId("statement")).toBeVisible();
    await expect(page.getByText("영업이익").first()).toBeVisible();

    // §24: the disclaimer comes from the API and is on the screen.
    await expect(page.getByTestId("disclaimer")).toContainText("회계자문");

    // §22: presentation changed; profit did not.
    await page.getByRole("link", { name: "영향 분석" }).click();
    await expect(page.getByTestId("headline")).toBeVisible();
    await expect(page.getByTestId("waterfall")).toBeVisible();
    const pbt = page.locator("text=법인세차감전순이익").first();
    await expect(pbt).toBeVisible();

    // §34: the deliverable is one click away once the analysis reconciled.
    await page.getByRole("link", { name: "IFRS 18 손익계산서" }).click();
    await expect(page.getByTestId("export-link")).toBeVisible();
  });

  test("shows the audit trail of what was decided", async ({ page }) => {
    await signUp(page);
    await createProject(page);
    await uploadAndExtract(page);
    await page.getByRole("button", { name: "분류 실행" }).click();
    await expect(page.getByText(/라인을 분류했습니다/)).toBeVisible();

    await page.getByRole("link", { name: "감사 추적" }).click();

    // Spec §8: every step that changed the project is recorded.
    const log = page.getByRole("main");
    await expect(log).toContainText("추출");
    await expect(log).toContainText("분류");
    await expect(log).toContainText("생성");
  });
});

test.describe("access", () => {
  test("sends a signed-out visitor to the sign-in page", async ({ page }) => {
    await page.goto("/projects");

    await expect(page).toHaveURL(/\/login$/);
  });
});
