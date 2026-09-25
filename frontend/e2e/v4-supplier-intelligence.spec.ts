import { expect, test, type Page } from "@playwright/test";

// V4 — supplier intelligence. Requires the stack with demo data (seed includes synthetic order history) and trained
// forecast + risk models (`python -m app.seed --reset && python -m app.ml.train`).

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("Demo@1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

test("at-risk items show which suppliers can deliver before the projected stockout", async ({ page }) => {
  await login(page, "procurement@sunrise.demo");
  await page.getByRole("link", { name: "Supplier intelligence", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Supplier intelligence" })).toBeVisible();
  const table = page.getByTestId("at-risk-table");
  await expect(table.locator("tbody tr").first()).toBeVisible();
  // first in-stock item with a deadline
  const row = table.locator("tbody tr").filter({ hasText: "days (" }).first();
  await row.click();
  const panel = page.getByTestId("item-suppliers");
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("supplier-summary")).toContainText("Projected stockout in");
  await expect(page.getByTestId("supplier-summary")).toContainText("no order has been placed");
  await expect(panel).toContainText(/Likely in time|Uncertain|Unlikely in time/);
  await expect(panel).toContainText("evidence:");
});

test("scorecards show the score formula and its measured components", async ({ page }) => {
  await login(page, "viewer@sunrise.demo");
  await page.goto("/supplier-intelligence?tab=scorecards");
  await expect(page.getByTestId("score-method")).toContainText("OTIF");
  await expect(page.getByTestId("score-method")).toContainText("Wilson");
  const table = page.getByTestId("scorecard-table");
  await expect(table.locator("tbody tr")).toHaveCount(6);
  await expect(table).toContainText(/[ABCD] · \d+/);
  // viewers can read but not record orders
  await expect(page.getByRole("button", { name: "Record order" })).toHaveCount(0);

  await table.getByRole("link", { name: "Nandi Medisupplies" }).click();
  const perf = page.getByTestId("supplier-performance");
  await expect(perf).toContainText("On time and in full (OTIF)");
  await expect(page.getByTestId("score-components")).toContainText("Cancellation rate");
  await expect(page.getByTestId("supplier-items-table")).toContainText("Surgical gloves");
});

test("record an order, receive stock against it, and it counts as delivered", async ({ page, context }) => {
  const ref = `E2E-${Date.now() % 1_000_000}`;
  await login(page, "procurement@sunrise.demo");
  await page.goto("/supplier-intelligence?tab=orders");
  await page.getByRole("button", { name: "Record order" }).click();
  const dlg = page.getByRole("dialog");
  await dlg.getByLabel("Supplier").selectOption({ label: "Karnataka Surgical Distributors" });
  await dlg.getByLabel("Item").selectOption({ label: "Gauze swab 10x10 cm, sterile (pack of 10) (DRS-GAU-10)" });
  await dlg.getByLabel("Quantity ordered").fill("120");
  await dlg.getByLabel("Reference (PO number)").fill(ref);
  await dlg.getByRole("button", { name: "Record order" }).click();
  await expect(page.getByText(`Order ${ref} recorded`)).toBeVisible();
  await expect(page.getByTestId("orders-table")).toContainText(ref);

  await context.clearCookies();
  await login(page, "inventory@sunrise.demo");
  await page.goto("/supplier-intelligence?tab=orders");
  await page.getByRole("button", { name: `Receive ${ref}` }).click();
  const rdlg = page.getByRole("dialog");
  await expect(rdlg.getByLabel("Against supplier order")).toHaveValue(/\d+/);
  await expect(rdlg.getByLabel("Quantity")).toHaveValue("120");
  await rdlg.getByLabel("Lot / batch no.").fill(`LOT-${ref}`);
  await rdlg.getByRole("button", { name: /Receive/ }).last().click();
  await expect(page.getByText(/Received 120/)).toBeVisible();
  await page.getByLabel("Order status").selectOption({ label: "Received" });
  const row = page.getByTestId("orders-table").getByRole("row", { name: new RegExp(ref) });
  await expect(row).toContainText("received");
  await expect(row).toContainText("(0 d");
});

test("stockout risk detail links to supplier options; overdue orders raise supplier-delay alerts", async ({ page }) => {
  await login(page, "inventory@sunrise.demo");
  await page.goto("/stockout-risks");
  await page.getByTestId("risk-table").locator("tbody tr").filter({ hasNotText: "Out of stock" }).first().click();
  await expect(page.getByTestId("risk-supplier-options")).toContainText("Supplier options");
  await expect(page.getByTestId("risk-supplier-options")).toContainText("evidence:");

  await page.goto("/alerts");
  await page.getByLabel("Type").selectOption({ label: "Supplier delay" });
  await expect(page.getByText(/Supplier delivery overdue:/).first()).toBeVisible();
});
