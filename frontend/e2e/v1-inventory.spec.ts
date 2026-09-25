import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "Demo@1234";

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  await expect(page.getByRole("heading", { name: "Supply health" })).toBeVisible();
}

test("unauthenticated users are sent to login", async ({ page }) => {
  await page.goto("/inventory");
  await expect(page).toHaveURL(/\/login\?next=%2Finventory/);
});

test("wrong password shows an error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("inventory@sunrise.demo");
  await page.getByLabel("Password").fill("wrong-password1");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Invalid email or password")).toBeVisible();
});

test("inventory manager: receive → issue (FEFO) → ledger → alert handling", async ({ page }) => {
  await login(page, "inventory@sunrise.demo");
  await expect(page.getByText("Items monitored")).toBeVisible();

  // Inventory list, filter to out-of-stock items
  await page.getByRole("link", { name: "Inventory", exact: true }).click();
  await page.getByRole("button", { name: /^Out of stock/ }).click();
  const firstRow = page.locator("tbody tr").first();
  const itemName = (await firstRow.locator("a").first().textContent())!.trim();
  await firstRow.locator("a").first().click();
  await expect(page.getByRole("heading", { name: itemName })).toBeVisible();
  await expect(page.getByText("Out of stock").first()).toBeVisible();

  // Receive a delivery
  const lot = `E2E-${Date.now()}`;
  await page.getByRole("button", { name: "Receive" }).click();
  const receive = page.getByRole("dialog", { name: "Receive stock" });
  await receive.getByLabel("Quantity").fill("500");
  await receive.getByLabel("Lot / batch no.").fill(lot);
  await receive.getByLabel("Expiry date").fill("2028-12-31");
  await receive.getByLabel("Reference (GRN / invoice)").fill("GRN-E2E");
  await receive.getByRole("button", { name: "Receive" }).click();
  await expect(page.getByText(/Received 500/)).toBeVisible();
  await expect(page.getByRole("cell", { name: lot }).first()).toBeVisible();

  // Issue to a department
  await page.getByRole("button", { name: "Issue", exact: true }).click();
  const issue = page.getByRole("dialog", { name: "Issue stock" });
  await issue.getByLabel("Quantity").fill("25");
  await issue.getByLabel("Department").selectOption({ label: "Orthopaedics OT" });
  await issue.getByRole("button", { name: "Issue" }).click();
  await expect(page.getByText(/Issued 25/)).toBeVisible();

  // Over-issue is blocked in the form (the API also rejects it — see backend tests)
  await page.getByRole("button", { name: "Issue", exact: true }).click();
  const qty = issue.getByLabel("Quantity");
  await qty.fill("99999");
  await issue.getByLabel("Department").selectOption({ label: "Orthopaedics OT" });
  await issue.getByRole("button", { name: "Issue" }).click();
  expect(await qty.evaluate((el: HTMLInputElement) => el.validity.rangeOverflow)).toBe(true);
  await page.keyboard.press("Escape");

  // Ledger shows both movements with the lot
  await page.getByRole("link", { name: "Stock movements" }).click();
  await page.getByPlaceholder("Item, SKU or reference").fill("GRN-E2E");
  await expect(page.getByRole("cell", { name: lot }).first()).toBeVisible();

  // Alerts: acknowledge the first open alert
  await page.getByRole("link", { name: /^Alerts/ }).click();
  await page.getByRole("combobox", { name: "Status" }).selectOption("OPEN");
  const row = page.getByTestId("alert-row").first();
  await row.getByRole("button", { name: "Acknowledge" }).click();
  await expect(page.getByText("Alert acknowledged")).toBeVisible();
});

test("department manager can only issue to own department and cannot receive", async ({ page }) => {
  await login(page, "ortho@sunrise.demo");
  await expect(page.getByRole("button", { name: "Receive stock" })).toHaveCount(0);
  await page.getByRole("button", { name: "Issue stock" }).click();
  const dept = page.getByRole("dialog", { name: "Issue stock" }).getByLabel("Department");
  await expect(dept).toHaveAttribute("aria-readonly", "true");
  await expect(dept.locator("option:checked")).toHaveText("Orthopaedics OT");
  await expect(page.getByRole("link", { name: "Audit log" })).toHaveCount(0);
});

test("viewer is read-only; admin sees audit log", async ({ page, context }) => {
  await login(page, "viewer@sunrise.demo");
  await page.getByRole("link", { name: "Inventory", exact: true }).click();
  await expect(page.getByRole("button", { name: "New item" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Issue" })).toHaveCount(0);

  await context.clearCookies();
  await login(page, "admin@sunrise.demo");
  await page.getByRole("link", { name: "Audit log" }).click();
  await expect(page.getByText("stock.receive").first()).toBeVisible();
  await page.getByTestId("user-menu").click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
});
