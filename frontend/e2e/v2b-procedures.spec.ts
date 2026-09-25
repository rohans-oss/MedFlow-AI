import { expect, test, type Page } from "@playwright/test";

// V2B — procedure-aware forecasting. Requires the stack running with demo data and a trained model
// (seed + `python -m app.ml.train`). The test creates its own procedure type and cleans up after itself.

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("Demo@1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

/** Business date (Asia/Kolkata) plus n days, as YYYY-MM-DD. */
function businessDay(n: number) {
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const d = new Date(`${today}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

const ITEM = "Bone wax 2.5 g (ORT-BNWAX)";

async function openItem(page: Page, label: string) {
  await page.goto("/forecasts");
  await page.getByLabel("Item", { exact: true }).selectOption({ label });
  await expect(page.getByTestId("forecast-14")).toBeVisible();
}

test("procedures page loads with schedule, types, mappings and a synthetic-data notice", async ({ page }) => {
  await login(page, "procurement@sunrise.demo");
  await page.getByRole("link", { name: "Procedures", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Procedures" })).toBeVisible();
  await expect(page.getByTestId("synthetic-banner")).toContainText("not medically validated");
  await expect(page.getByTestId("schedule-table")).toContainText("scheduled");
  // procurement can view but not manage procedure data
  await expect(page.getByRole("button", { name: "Schedule" })).toHaveCount(0);
  await page.getByRole("tab", { name: "Procedure types" }).click();
  await expect(page.getByTestId("types-table")).toContainText("Total knee replacement");
  await page.getByRole("tab", { name: "Item mapping" }).click();
  await expect(page.getByTestId("mappings-table")).toContainText("Surgical gloves");
});

test("create type, map an item, schedule it — forecast shows the procedure impact and the explanation changes", async ({ page }) => {
  const code = `E2E${Date.now() % 1_000_000}`;
  const name = `E2E test procedure ${code}`;
  await login(page, "admin@sunrise.demo");

  // baseline: procedure impact for the item before we add anything
  await openItem(page, ITEM);
  const impact = page.getByTestId("procedure-impact");
  await expect(impact).toBeVisible();
  const countBefore = Number((await page.getByTestId("impact-count").textContent())!.replace(/[^\d]/g, ""));
  const explanationBefore = await page.getByTestId("forecast-explanation").textContent();

  let typeId: number | undefined;
  try {
    // 1. procedure type
    await page.goto("/procedures?tab=types");
    await page.getByRole("button", { name: "Procedure type" }).click();
    const dlg = page.getByRole("dialog");
    await dlg.getByLabel("Code").fill(code);
    await dlg.getByLabel("Department").selectOption({ label: "Orthopaedics OT" });
    await dlg.getByLabel("Name", { exact: true }).fill(name);
    await dlg.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Procedure type created")).toBeVisible();
    await expect(page.getByTestId("types-table")).toContainText(name);

    // 2. mapping: 3 units of the item per procedure
    await page.getByRole("button", { name: "Map item" }).click();
    const mdlg = page.getByRole("dialog");
    await mdlg.getByLabel("Procedure", { exact: true }).selectOption({ label: `${name} — Orthopaedics OT` });
    await mdlg.getByLabel("Item", { exact: true }).selectOption({ label: ITEM });
    await mdlg.getByLabel("Quantity per procedure").fill("3");
    await mdlg.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Item mapped to procedure")).toBeVisible();
    await page.getByRole("tab", { name: "Item mapping" }).click();
    await page.getByLabel("Procedure filter").selectOption({ label: name });
    await expect(page.getByTestId("mappings-table")).toContainText("Bone wax 2.5 g");

    // 3. schedule 5 procedures in two days
    await page.getByRole("tab", { name: "Schedule" }).click();
    await page.getByRole("button", { name: "Schedule", exact: true }).click();
    const sdlg = page.getByRole("dialog");
    await sdlg.getByLabel("Procedure", { exact: true }).selectOption({ label: `${name} — Orthopaedics OT` });
    await sdlg.getByLabel("Date").fill(businessDay(2));
    await sdlg.getByLabel("Number of procedures").fill("5");
    await sdlg.getByRole("button", { name: "Schedule" }).click();
    await expect(page.getByText("Procedures scheduled")).toBeVisible();
    await page.getByLabel("Procedure", { exact: true }).selectOption({ label: name });
    const row = page.getByTestId("schedule-table").getByRole("row", { name: new RegExp(name) });
    await expect(row).toContainText("5");
    await expect(row).toContainText("scheduled");

    const types = await (await page.request.get("/api/procedures/types")).json();
    typeId = types.find((t: { code: string }) => t.code === code)?.id;

    // 4. forecast page reflects the new schedule and explains it
    await openItem(page, ITEM);
    await expect(page.getByTestId("impact-count")).toHaveText(String(countBefore + 5));
    await expect(page.getByTestId("procedure-impact")).toContainText(name);
    await expect(page.getByTestId("forecast-explanation")).toContainText("retrain");
    const explanationAfter = await page.getByTestId("forecast-explanation").textContent();
    expect(explanationAfter).not.toEqual(explanationBefore);
    await expect(page.getByTestId("model-comparison")).toContainText("Procedure data changed since training");
  } finally {
    // clean up so the demo data and the stored model stay unchanged
    if (typeId) {
      const sched = await (await page.request.get(`/api/procedures/schedule?procedure_type_id=${typeId}&status=SCHEDULED`)).json();
      for (const r of sched.items) await page.request.post(`/api/procedures/schedule/${r.id}/cancel`, { data: { reason: "e2e cleanup" } });
      const maps = await (await page.request.get(`/api/procedures/mappings?procedure_type_id=${typeId}`)).json();
      for (const m of maps) await page.request.patch(`/api/procedures/mappings/${m.id}`, { data: { is_active: false } });
      await page.request.patch(`/api/procedures/types/${typeId}`, { data: { is_active: false } });
    }
  }
});

test("department manager manages only their own department; viewer is read-only", async ({ page, context }) => {
  await login(page, "ortho@sunrise.demo");
  await page.goto("/procedures");
  await page.getByRole("button", { name: "Schedule", exact: true }).click();
  const select = page.getByRole("dialog").getByLabel("Procedure", { exact: true });
  await expect(select.locator("option").nth(1)).toBeAttached(); // wait for procedure types to load
  const options = await select.locator("option").allTextContents();
  expect(options.length).toBeGreaterThan(1);
  for (const o of options.slice(1)) expect(o).toContain("Orthopaedics");
  await page.keyboard.press("Escape");

  await context.clearCookies();
  await login(page, "viewer@sunrise.demo");
  await page.goto("/procedures");
  await expect(page.getByTestId("schedule-table")).toBeVisible();
  await expect(page.getByRole("button", { name: "Schedule", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Procedure type" })).toHaveCount(0);
});

test("forecast page shows model comparison, stock vs forecast, and V2A behaviour for unmapped items", async ({ page }) => {
  await login(page, "inventory@sunrise.demo");
  await openItem(page, "Glucometer test strip (LAB-GLU-STR)");
  await expect(page.getByTestId("model-comparison")).toContainText("V2A WAPE");
  await expect(page.getByTestId("procedure-impact")).toContainText("No scheduled procedures use this item");
  await expect(page.getByTestId("stock-cover")).toContainText("Days of stock remaining");
  await expect(page.getByTestId("forecast-explanation")).toContainText("Expected demand over the next 14 days");

  await page.goto("/forecasts?tab=evaluation");
  await expect(page.getByTestId("candidate-table")).toContainText("Procedure-aware XGBoost (V2B)");
  await expect(page.getByTestId("v2b-folds")).toContainText("holdout");
});
