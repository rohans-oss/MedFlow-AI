import { expect, test, type Page } from "@playwright/test";

// Requires the stack running with demo data AND a trained model (docker entrypoint or `python -m app.ml.train`).

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("Demo@1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

test("procurement manager sees an item forecast with 7/14/30-day totals and an explanation", async ({ page }) => {
  await login(page, "procurement@sunrise.demo");
  await expect(page.getByTestId("dashboard-forecast")).toContainText("Demand forecast");
  await page.getByRole("link", { name: "Forecasts", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Demand forecast" })).toBeVisible();
  await expect(page.getByText("Active model")).toBeVisible();

  await page.getByLabel("Item", { exact: true }).selectOption({ label: "Surgical gloves, sterile, size 7 (GLV-SRG-7)" });
  for (const d of [7, 14, 30]) await expect(page.getByTestId(`forecast-${d}`)).toContainText("pair");
  const n7 = Number((await page.getByTestId("forecast-7").textContent())!.replace(/[^\d]/g, ""));
  const n30 = Number((await page.getByTestId("forecast-30").textContent())!.replace(/[^\d]/g, ""));
  expect(n30).toBeGreaterThan(n7);
  await expect(page.getByTestId("forecast-explanation")).toContainText("Expected demand over the next 14 days");
  await expect(page.getByText("How accurate was it? (backtest)")).toBeVisible();

  await page.getByRole("button", { name: "30 days" }).click();
  await expect(page.getByTestId("forecast-explanation")).toContainText("next 30 days");
});

test("model evaluation shows baselines vs XGBoost with stored metrics", async ({ page }) => {
  await login(page, "inventory@sunrise.demo");
  await page.goto("/forecasts?tab=evaluation");
  const table = page.getByTestId("candidate-table");
  await expect(table).toContainText("XGBoost");
  await expect(table).toContainText("7-day moving average");
  await expect(table).toContainText("Historical average");
  await expect(table.getByText("Serving")).toHaveCount(1);
  await expect(page.getByText("Model registry")).toBeVisible();
});

test("retrain creates a new model version; viewers cannot retrain", async ({ page, context }) => {
  await login(page, "procurement@sunrise.demo");
  await page.goto("/forecasts");
  // Training (V2A + V2B + rolling validation folds) runs synchronously; on a small CI box it can take a while.
  const started = Date.now();
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith("/api/forecasts/train"), { timeout: 150_000 }),
    page.getByRole("button", { name: "Retrain models" }).click(),
  ]);
  console.log(`training request took ${((Date.now() - started) / 1000).toFixed(1)}s`);
  expect(res.status()).toBe(200);
  await expect(page.getByText(/Trained .* serving/)).toBeVisible();

  await context.clearCookies();
  await login(page, "viewer@sunrise.demo");
  await page.goto("/forecasts");
  await expect(page.getByText("Active model")).toBeVisible();
  await expect(page.getByRole("button", { name: /Retrain/ })).toHaveCount(0);
});
