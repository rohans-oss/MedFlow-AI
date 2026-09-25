import { expect, test, type Page } from "@playwright/test";

// V5 — procurement intelligence. Requires the stack with demo data and trained models
// (`python -m app.seed --reset && python -m app.ml.train`, which also generates PENDING recommendations).

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("Demo@1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

async function regenerate(page: Page) {
  await page.goto("/procurement?tab=recommendations");
  const btn = page.getByRole("button", { name: /Regenerate|Generate recommendations/ });
  await btn.first().click();
  await expect(page.getByText(/recommendations? for review/)).toBeVisible({ timeout: 60_000 });
}

test("needs attention (in-transit aware) leads to scenarios that are not just the cheapest", async ({ page }) => {
  await login(page, "procurement@sunrise.demo");
  await page.getByRole("link", { name: "Procurement", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Procurement intelligence" })).toBeVisible();
  const table = page.getByTestId("attention-table");
  await expect(table.locator("tbody tr").first()).toBeVisible();
  await expect(table).toContainText("Order");
  await table.locator("tbody tr").first().locator("button").first().click();
  await expect(page).toHaveURL(/tab=scenarios/);
  const plan = page.getByTestId("plan-view");
  await expect(plan).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("scenario-table")).toContainText("Recommended");
  await expect(page.getByTestId("scenario-table")).toContainText("No new order");
  await expect(page.getByTestId("plan-explanation")).toContainText("nothing is ordered until a person approves it");
  await expect(page.getByTestId("replenishment-steps")).toContainText("Safety stock");
  await expect(page.getByTestId("replenishment-steps")).toContainText("MOQ-adjusted quantity");
});

test("approve a recommendation: the order is recorded only after approval", async ({ page }) => {
  await login(page, "procurement@sunrise.demo");
  await regenerate(page);
  await expect(page.getByTestId("recommendation-cards")).toContainText("Purchase · expected cost");
  await page.goto("/procurement?tab=approvals");
  const row = page.getByTestId("approval-table").locator("tbody tr").filter({ hasNotText: "No new order" }).first();
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: /^Approve / }).click();
  const dlg = page.getByRole("dialog");
  await expect(dlg).toContainText("does not send anything to the supplier");
  await dlg.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/Approved — recorded SO-\d+/)).toBeVisible();
  await page.getByLabel("Status").selectOption({ label: "Approved" });
  await expect(page.getByTestId("approval-table")).toContainText(/Recorded: SO-\d+/);
  // the recorded order appears in the V4 order log as an open order
  await page.goto("/supplier-intelligence?tab=orders");
  await expect(page.getByTestId("orders-table")).toContainText("open");
});

test("modify needs a reason; reject keeps the reason", async ({ page }) => {
  await login(page, "procurement@sunrise.demo");
  await regenerate(page);
  await page.goto("/procurement?tab=approvals");
  const rows = page.getByTestId("approval-table").locator("tbody tr").filter({ hasNotText: "No new order" });
  await rows.first().getByRole("button", { name: /^Modify / }).click();
  const dlg = page.getByRole("dialog");
  await expect(dlg.getByLabel("Quantity 1")).toBeVisible();
  const q = Number(await dlg.getByLabel("Quantity 1").inputValue());
  await dlg.getByLabel("Quantity 1").fill(String(q + 10));
  await dlg.getByRole("button", { name: "Approve with changes" }).click();
  await expect(dlg.getByText("Give a reason for changing the recommendation")).toBeVisible();
  await dlg.getByLabel("Reason for the change").fill("Round up to full cartons");
  await dlg.getByRole("button", { name: "Approve with changes" }).click();
  await expect(page.getByText(/Approved with changes — recorded SO-\d+/)).toBeVisible();

  const next = page.getByTestId("approval-table").locator("tbody tr").first();
  await next.getByRole("button", { name: /^Reject / }).click();
  const rj = page.getByRole("dialog");
  await rj.getByLabel("Reason").fill("Stock borrowed from the sister hospital");
  await rj.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Recommendation rejected")).toBeVisible();
  await page.getByLabel("Status").selectOption({ label: "Decided" });
  await expect(page.getByTestId("approval-table")).toContainText("Stock borrowed from the sister hospital");
  await expect(page.getByTestId("approval-table")).toContainText("approved (modified)");
});

test("what-if: a supplier delay recalculates risk, shortage, cost and arrival; viewers cannot approve", async ({ page, context }) => {
  await login(page, "inventory@sunrise.demo");
  await page.goto("/procurement?tab=what-if");
  await page.getByLabel("Item").selectOption({ index: 1 });
  await expect(page.getByLabel("Supplier delayed")).toBeEnabled({ timeout: 60_000 });
  await page.getByLabel("Supplier delayed").selectOption({ index: 1 });
  await page.getByLabel("Extra days").fill("2");
  await page.getByRole("button", { name: "Run what-if" }).click();
  const summary = page.getByTestId("whatif-summary");
  await expect(summary).toContainText(/What-if: .*\+2 days/, { timeout: 60_000 });
  await expect(summary).toContainText("stockout probability");
  await expect(summary).toContainText("expected cost");
  await expect(page.getByTestId("whatif-result")).toContainText("→");

  await context.clearCookies();
  await login(page, "viewer@sunrise.demo");
  await page.goto("/procurement?tab=approvals");
  await expect(page.getByText("Approving needs the procurement role")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Approve / })).toHaveCount(0);
  await page.goto("/procurement?tab=cost-model");
  await expect(page.getByTestId("cost-formula")).toContainText("Total expected cost = purchase + expected stockout");
  await expect(page.getByRole("button", { name: "Save cost model" })).toHaveCount(0);
});
