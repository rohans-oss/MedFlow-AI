import { expect, test, type Page } from "@playwright/test";

// V9 — integrations & data exchange. Requires the stack seeded (`python -m app.seed --reset`) and the API started with the
// local reference ERP simulator enabled: REFERENCE_ERP_ENABLED=true REFERENCE_ERP_TOKEN=<any> (SIMULATED data only).

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("Demo@1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

async function openSource(page: Page, name: string) {
  await page.goto("/integrations?tab=sources");
  await page.getByTestId("source-list").getByRole("button").filter({ hasText: name }).click();
  await expect(page.getByTestId("source-title")).toContainText(name);
}

test("monitoring is honest: the demo ERP is labelled simulated and the limitation is stated", async ({ page }) => {
  await login(page, "admin@sunrise.demo");
  await page.getByRole("link", { name: "Integrations", exact: true }).click();
  await expect(page.getByTestId("integration-notice")).toContainText("have not been claimed unless actually tested");
  const table = page.getByTestId("monitoring-table");
  await expect(table).toContainText("Reference ERP (simulated)");
  await expect(table.getByRole("row", { name: /Reference ERP/ })).toContainText("simulated");
  await expect(table).toContainText("Stores spreadsheet (CSV / Excel)");
  await expect(table).toContainText("Procurement system (API push)");
});

test("pull from the reference ERP simulator: sync, rejected records, reconciliation resolved by a person", async ({ page }) => {
  await login(page, "admin@sunrise.demo");
  await openSource(page, "Reference ERP (simulated)");
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByTestId("test-result")).toContainText("SIMULATED", { timeout: 30_000 });
  await page.getByTestId("sync-now").click();
  const result = page.getByTestId("sync-result");
  await expect(result).toBeVisible({ timeout: 120_000 });
  for (const e of ["departments", "suppliers", "items", "supplier_items", "purchase_orders", "deliveries", "consumption", "inventory"]) {
    await expect(result).toContainText(e);
  }
  // the simulator deliberately sends an unknown material and a negative quantity: they are rejected, not imported
  await result.getByRole("row", { name: /consumption/ }).getByRole("button", { name: "Details" }).click();
  const detail = page.getByTestId("run-detail");
  await expect(detail).toBeVisible();
  const rejected = page.getByTestId("rejected-records");
  if (await rejected.count()) await expect(rejected).toContainText(/unknown item|negative quantity/);
  await page.keyboard.press("Escape");

  // the ERP's stock count differs from MedFlow's ledger for GLV-EXM-M (+30) → reconciliation issue, resolved by a person
  await page.goto("/integrations?tab=reconciliation");
  await page.getByLabel("Reconciliation status").selectOption("ALL");
  const recon = page.getByTestId("reconciliation-table");
  await expect(recon.getByRole("row", { name: /GLV-EXM-M/ }).first()).toContainText("+30", { timeout: 30_000 });
  await page.getByLabel("Reconciliation status").selectOption("OPEN");
  const open = recon.getByRole("row", { name: /GLV-EXM-M/ });
  if (await open.count()) {
    await open.getByRole("button", { name: "Resolve" }).click();
    await page.getByLabel("Decision").selectOption("adjust");
    await page.getByLabel("Note").fill("Physical recount confirmed the ERP figure");
    await page.getByRole("button", { name: "Resolve" }).last().click();
    await expect(open).toHaveCount(0, { timeout: 30_000 });
  }
  // the movements page shows the integration's own account as the actor of the imported consumption
  await page.goto("/movements");
  await expect(page.getByText("Integration: Reference ERP (simulated)").first()).toBeVisible({ timeout: 30_000 });
});

test("CSV upload: valid rows applied through the ledger, invalid rows rejected with reasons", async ({ page }) => {
  await login(page, "admin@sunrise.demo");
  await openSource(page, "Stores spreadsheet");
  await page.getByLabel("Upload entity").selectOption("consumption");
  const stamp = Date.now();
  const csv = [
    "external_id,item_code,department_code,quantity,occurred_at",
    `E2E-${stamp}-1,GLV-EXM-M,ICU,2,${new Date().toISOString()}`,
    `E2E-${stamp}-2,NOT-A-SKU,ICU,2,${new Date().toISOString()}`,
    `E2E-${stamp}-3,GLV-EXM-M,ICU,-5,${new Date().toISOString()}`,
  ].join("\n");
  await page.getByLabel("Upload file").setInputFiles({ name: "consumption.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByTestId("upload-submit").click();
  const res = page.getByTestId("upload-result");
  await expect(res).toContainText("1 new", { timeout: 60_000 });
  await expect(res.getByTestId("rejected-records")).toContainText("unknown item");
  await expect(res.getByTestId("rejected-records")).toContainText("negative quantity");
});

test("API push with a key shown once; another hospital cannot see the run", async ({ page }) => {
  await login(page, "admin@sunrise.demo");
  await openSource(page, "Procurement system (API push)");
  await page.getByPlaceholder("e.g. SAP PI production").fill(`e2e ${Date.now()}`);
  await page.getByRole("button", { name: "Create key" }).click();
  const key = (await page.getByTestId("new-api-key").innerText()).trim();
  expect(key).toMatch(/^mfk_/);
  await page.getByRole("button", { name: "Done" }).click();
  const po = `E2E-PO-${Date.now()}`;
  const today = new Date().toISOString().slice(0, 10);
  const r = await page.request.post("/api/ingest/v1/purchase_orders", {
    headers: { Authorization: `Bearer ${key}` },
    data: { records: [{ po_number: po, supplier_code: "CPS", item_code: "GLV-EXM-M", quantity: 100, order_date: today }] },
  });
  expect(r.status()).toBe(200);
  const body = (await r.json()) as { created: number; run_id: number };
  expect(body.created).toBe(1);
  expect((await page.request.post("/api/ingest/v1/purchase_orders", { headers: { Authorization: "Bearer mfk_bad_key" }, data: { records: [{}] } })).status()).toBe(401);
  await page.goto("/integrations?tab=runs");
  await expect(page.getByTestId("runs-table")).toContainText("Procurement system (API push)");
  // Lakeview's admin: own sources only, and Sunrise's run id is not found
  const other = await page.context().browser()!.newContext({ baseURL: test.info().project.use.baseURL });
  const op = await other.newPage();
  await login(op, "admin@lakeview.demo");
  expect((await op.request.get(`/api/integrations/runs/${body.run_id}`)).status()).toBe(404);
  const runs = (await (await op.request.get("/api/integrations/runs", { params: { page_size: 200 } })).json()) as { items: { id: number }[] };
  expect(runs.items.map((x) => x.id)).not.toContain(body.run_id);
  await other.close();
});

test("roles: a viewer has no integrations page", async ({ page }) => {
  await login(page, "viewer@sunrise.demo");
  await expect(page.getByRole("link", { name: "Integrations", exact: true })).toHaveCount(0);
  expect((await page.request.get("/api/integrations/overview")).status()).toBe(403);
});
