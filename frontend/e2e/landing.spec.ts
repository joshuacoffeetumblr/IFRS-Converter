import { expect, test } from "@playwright/test";

/**
 * The landing page. The full spec §34 flow lives in `analysis-flow.spec.ts`.
 */
test.describe("landing page", () => {
  test("shows the primary call to action", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "Impact Analyzer" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Upload Financial Statements" })).toBeVisible();
  });

  test("renders the §24 disclaimer served by the API", async ({ page }) => {
    await page.goto("/");

    // Sourced from GET /api/meta/disclaimer, never hardcoded in the client.
    await expect(
      page.getByText("does not constitute accounting advice", { exact: false }),
    ).toBeVisible();
  });

  test("states the MVP scope limitations rather than omitting them", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByText("Restructures the statement of profit or loss only.")).toBeVisible();
    await expect(page.getByText(/management-defined performance measure/)).toBeVisible();
  });
});
