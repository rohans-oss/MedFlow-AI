import { expect, test, type Page } from "@playwright/test";

// V3 — stockout risk. Requires the stack with demo data and trained forecast + risk models
// (`python -m app.seed --reset && python -m app.ml.train`).

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("Demo@1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

test("risk page lists items by level with probability, date, shortage and reason", async ({ page }) => {
  await login(page, "procurement@sunrise.demo");
  await expect(page.getByTestId("dashboard-risk")).toContainText("Stockout risk");
  await page.getByRole("link", { name: "Stockout risk", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Stockout risk" })).toBeVisible();
  await expect(page.getByTestId("risk-model-banner")).toContainText("PR-AUC");
  const high = Number(await page.getByTestId("count-high").textContent());
  expect(high).toBeGreaterThan(0);
  const table = page.getByTestId("risk-table");
  await expect(table.locator("tbody tr").first()).toContainText(/High|Out of stock/);
  await expect(table).toContainText("%");

  // filter to high only: every row is high / out of stock
  await page.getByRole("group", { name: "Risk level" }).getByRole("button", { name: "High" }).click();
  const rows = table.locator("tbody tr");
  expect(await rows.count()).toBe(high);
  for (const t of await rows.allTextContents()) expect(t).toMatch(/High|Out of stock/);
});

test("item detail explains the risk with a projection, reasons and model factors", async ({ page }) => {
  await login(page, "inventory@sunrise.demo");
  await page.goto("/stockout-risks");
  // first in-stock item at risk (not already out of stock)
  const row = page.getByTestId("risk-table").locator("tbody tr").filter({ hasNotText: "Out of stock" }).first();
  await row.click();
  const detail = page.getByTestId("risk-detail");
  await expect(detail).toBeVisible();
  await expect(page.getByTestId("detail-probability")).toContainText("%");
  await expect(detail).toContainText("Expected stockout");
  await expect(detail).toContainText("Supplier lead time");
  await expect(page.getByTestId("risk-reasons")).toContainText(/run out|covers the full/);
  await expect(detail.getByRole("img", { name: /Projected usable stock/ })).toBeVisible(); // projection chart
  // V3 serves the XGBoost model only when it beats the cover rule on the hospital's own backtest; the synthetic demo
  // ledger ends "yesterday", so which one is served can differ by calendar day. Check the explanation that matches.
  if ((await detail.innerText()).includes("risk model stockout_xgb")) {
    await expect(detail).toContainText("Model factors");
  } else {
    await expect(detail).toContainText("Probability from the cover rule");
  }
});

test("evaluation tab answers: could it have predicted the stockouts that happened?", async ({ page }) => {
  await login(page, "viewer@sunrise.demo");
  await page.goto("/stockout-risks?tab=evaluation");
  const cmp = page.getByTestId("risk-comparison");
  await expect(cmp).toContainText("Precision");
  await expect(cmp).toContainText("False negatives");
  await expect(cmp).toContainText("PR-AUC");
  await expect(cmp).toContainText("Reorder-level rule (V1)");
  await expect(cmp).toContainText("lead time ahead");
  await expect(page.getByTestId("risk-events")).toContainText(/Warned in time|Missed|too late/);
  // viewers can read but not retrain
  await expect(page.getByRole("button", { name: /Retrain risk model|Recalculate/ })).toHaveCount(0);
});

test("a delivery lowers the item's risk immediately and resolves its risk alert", async ({ page }) => {
  await login(page, "inventory@sunrise.demo");
  const ov = await (await page.request.get("/api/stockout-risks")).json();
  const target = ov.items.find((i: { out_of_stock: boolean; risk_level: string }) => !i.out_of_stock && i.risk_level !== "LOW");
  expect(target, "expected an in-stock item at medium/high risk in the demo").toBeTruthy();

  await page.goto("/alerts");
  await page.getByLabel("Type").selectOption({ label: "Stockout risk" });
  await expect(page.getByText(`stockout risk: ${target.name}`)).toBeVisible();

  const expiry = new Date(Date.now() + 700 * 86_400_000).toISOString().slice(0, 10);
  const r = await page.request.post("/api/inventory/receive", {
    data: { consumable_id: target.consumable_id, quantity: Math.max(2000, Math.round(target.forecast_30 * 3)), lot_number: `E2E-${Date.now()}`, expiry_date: expiry },
  });
  expect(r.ok()).toBeTruthy();

  await page.goto(`/stockout-risks?item=${target.consumable_id}`);
  const detail = page.getByTestId("risk-detail");
  await expect(detail).toContainText("Low");
  await expect(detail).toContainText("Not within 30 days");
  await page.goto("/alerts");
  await page.getByLabel("Type").selectOption({ label: "Stockout risk" });
  await expect(page.getByText(`stockout risk: ${target.name}`)).toHaveCount(0);
});
