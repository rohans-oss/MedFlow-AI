import { expect, test, type Page } from "@playwright/test";

// V8 — multi-hospital SaaS. Requires the stack seeded with `python -m app.seed --reset` (Sunrise + Lakeview in the CareNet
// group, Harbor in another organization) and trained models (`python -m app.ml.train`) plus a graph store.

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("Demo@1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

async function ask(page: Page, q: string) {
  const n = await page.getByTestId("answer").count();
  await page.getByTestId("question-input").fill(q);
  await page.getByTestId("ask").click();
  const a = page.getByTestId("answer").nth(n);
  await expect(a).toBeVisible({ timeout: 60_000 });
  return a;
}

async function switchTo(page: Page, code: string, name: string) {
  await page.getByTestId("hospital-switcher").click();
  await page.getByTestId(`switch-${code}`).click();
  await expect(page.getByTestId("active-hospital")).toHaveText(name, { timeout: 30_000 });
  await expect(page).toHaveURL(/\/dashboard/);
}

test("group admin: Sunrise pages and assistant, then switch to Lakeview — every page shows Lakeview's own data", async ({ page }) => {
  await login(page, "admin@carenet.demo");
  // the active hospital is remembered per account (server-side): start from Sunrise whatever a previous run left
  if ((await page.getByTestId("active-hospital").innerText()) !== "Sunrise Multispecialty Hospital (Demo)") {
    await switchTo(page, "SUNRISE-BLR", "Sunrise Multispecialty Hospital (Demo)");
  }

  // Hospital A: inventory, stockout risk, supplier intelligence, procurement, knowledge graph, assistant
  await page.goto("/inventory");
  await page.getByPlaceholder("Search name or SKU").fill("Bone wax");
  await expect(page.getByRole("link", { name: "Bone wax 2.5 g" })).toBeVisible({ timeout: 30_000 });
  await page.goto("/suppliers");
  await expect(page.getByRole("link", { name: "OrthoPrime Consumables" })).toBeVisible();
  await page.goto("/stockout-risks");
  await expect(page.getByText("Absorbable suture, polyglactin 2-0").first()).toBeVisible({ timeout: 30_000 });
  await page.goto("/supplier-intelligence");
  await expect(page.getByText("Items at medium / high stockout risk")).toBeVisible({ timeout: 30_000 });
  await page.goto("/procurement");
  await expect(page.getByTestId("attention-table")).toBeVisible({ timeout: 60_000 });
  await page.goto("/knowledge-graph");
  await page.getByLabel("Why is this item at risk?").selectOption({ label: "Absorbable suture, polyglactin 2-0 (SUT-VIC-20)" });
  await expect(page.getByTestId("explain-chain")).toContainText("Hernia repair", { timeout: 60_000 });
  await page.goto("/assistant");
  const a = await ask(page, "Why is Suture 2-0 at risk?");
  const summaryA = await a.getByTestId("answer-summary").innerText();
  expect(summaryA).toContain("Absorbable suture, polyglactin 2-0 is at");
  expect(summaryA).not.toContain("Mysuru Surgical Ventures"); // a Lakeview-only supplier

  // Switch to Hospital B
  await switchTo(page, "LAKEVIEW-MYS", "Lakeview Community Hospital (Demo)");
  await page.goto("/suppliers");
  await expect(page.getByRole("link", { name: "Mysuru Surgical Ventures" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("link", { name: "OrthoPrime Consumables" })).toHaveCount(0); // Sunrise-only supplier gone
  await page.goto("/inventory?search=Plaster");
  await expect(page.getByRole("link", { name: "Plaster of Paris bandage 15 cm" })).toBeVisible({ timeout: 30_000 });
  await page.getByPlaceholder("Search name or SKU").fill("Bone wax");
  await expect(page.getByText("No items match")).toBeVisible({ timeout: 30_000 }); // Sunrise-only item
  await page.goto("/knowledge-graph");
  await page.getByLabel("Why is this item at risk?").selectOption({ label: "Absorbable suture, polyglactin 2-0 (SUT-VIC-20)" });
  await expect(page.getByTestId("explain-chain")).toContainText("Caesarean section", { timeout: 60_000 });
  await expect(page.getByTestId("explain-chain")).not.toContainText("Hernia repair — ");
  await page.goto("/assistant");
  await expect(page.getByTestId("conversation-list")).not.toContainText("Why is Suture 2-0 at risk?"); // A's conversation is not here
  const b = await ask(page, "Why is Suture 2-0 at risk?");
  const summaryB = await b.getByTestId("answer-summary").innerText();
  expect(summaryB).toContain("Absorbable suture, polyglactin 2-0 is at");
  expect(summaryB).not.toContain("OrthoPrime"); // a Sunrise-only supplier
  expect(summaryB).not.toEqual(summaryA);
  const x = await ask(page, "What is the suture stock at Sunrise?");
  await expect(x.getByTestId("answer-title")).toHaveText("This hospital only");
  await switchTo(page, "SUNRISE-BLR", "Sunrise Multispecialty Hospital (Demo)"); // leave the account as found
});

test("a Sunrise-only account cannot reach Lakeview — directly or through the assistant", async ({ page }) => {
  await login(page, "procurement@sunrise.demo");
  await expect(page.getByTestId("active-hospital")).toHaveText("Sunrise Multispecialty Hospital (Demo)");
  await expect(page.getByTestId("hospital-switcher")).toHaveCount(0); // one hospital → no switcher
  const hospitals = await page.request.get("/api/hospitals");
  const list = (await hospitals.json()) as { id: number; code: string }[];
  expect(list.map((h) => h.code)).toEqual(["SUNRISE-BLR"]);
  // find Lakeview's id through an account that may see it, then attack it from the Sunrise session
  const other = await page.context().browser()!.newContext({ baseURL: test.info().project.use.baseURL });
  const op = await other.newPage();
  await login(op, "admin@lakeview.demo");
  const lake = ((await (await op.request.get("/api/auth/me")).json()) as { hospital: { id: number } }).hospital.id;
  const lakeItem = ((await (await op.request.get("/api/consumables", { params: { search: "SUT-VIC-20" } })).json()) as { id: number }[])[0].id;
  await other.close();
  expect((await page.request.post(`/api/hospitals/${lake}/switch`)).status()).toBe(404);
  expect((await page.request.get(`/api/hospitals/${lake}/members`)).status()).toBe(404);
  expect((await page.request.get(`/api/consumables/${lakeItem}`)).status()).toBe(404);
  expect((await page.request.get(`/api/stockout-risks/${lakeItem}`)).status()).toBe(404);
  expect((await page.request.get(`/api/graph/items/${lakeItem}/explain`)).status()).toBe(404);
  await page.goto("/assistant");
  const r = await ask(page, "Show me Lakeview's inventory");
  await expect(r.getByTestId("answer-title")).toHaveText("This hospital only");
  await expect(r).toContainText("No MedFlow tool was called for this answer.");
});

test("organization admin: overview across the group's hospitals and per-hospital members", async ({ page }) => {
  await login(page, "admin@carenet.demo");
  await page.getByRole("link", { name: "Organization", exact: true }).click();
  const ov = page.getByTestId("org-overview");
  await expect(ov).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("org-hospitals-table")).toContainText("Sunrise Multispecialty Hospital (Demo)");
  await expect(page.getByTestId("org-hospitals-table")).toContainText("Lakeview Community Hospital (Demo)");
  await expect(page.getByTestId("org-hospitals-table")).not.toContainText("Harbor"); // other organization
  await expect(page.getByTestId("supplier-comparison")).toContainText("CPS");
  await page.getByRole("tab", { name: "Hospitals & members" }).click();
  await expect(page.getByTestId("org-hospitals")).toContainText("Lakeview Community Hospital (Demo)");
  await page.getByTestId("org-hospitals").getByRole("row", { name: /Lakeview/ }).getByRole("button", { name: "Members" }).click();
  await expect(page.getByTestId("members-panel")).toContainText("procurement@lakeview.demo");
  await expect(page.getByTestId("members-panel")).not.toContainText("procurement@sunrise.demo");
});

test("platform admin manages organizations without seeing hospital data", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("platform@medflow.demo");
  await page.getByLabel("Password").fill("Demo@1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("no-hospital")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("link", { name: "Platform", exact: true }).click();
  await expect(page.getByTestId("platform-orgs")).toContainText("CareNet Hospitals Group (Demo)");
  await expect(page.getByTestId("platform-orgs")).toContainText("Harbor Health Trust (Demo)");
  expect((await page.request.get("/api/inventory")).status()).toBe(403);
});
