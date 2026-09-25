import { expect, test, type Page } from "@playwright/test";

// V10 — real hospital pilot & business validation. (File named v9z-… so it runs after the V1–V9 specs: its V9 sync imports
// simulated ERP data that earlier specs must not see.) Requires the seeded demo stack with the reference ERP simulator
// enabled (REFERENCE_ERP_ENABLED=true REFERENCE_ERP_TOKEN=…). Sunrise is a DEMO hospital: every result is synthetic.

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("Demo@1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

const iso = (days: number) => new Date(Date.now() + days * 86_400_000).toISOString().slice(0, 10);

test("pilot end to end: create, dates, V9 sync, data quality, metrics, feedback, issue, report (synthetic)", async ({ page }) => {
  await login(page, "admin@sunrise.demo");
  // re-runnable: only one pilot may be active per hospital — complete any left active by an earlier run
  const existing = (await (await page.request.get("/api/pilots")).json()) as { id: number; status: string }[];
  for (const p of existing.filter((x) => x.status === "active" || x.status === "paused")) {
    await page.request.patch(`/api/pilots/${p.id}`, { data: { status: "completed" } });
  }
  await page.getByRole("link", { name: "Pilots", exact: true }).click();
  await expect(page.getByText("No real hospital pilot has been run with MedFlow yet")).toBeVisible();
  const name = `E2E pilot ${Date.now()}`;
  await page.getByRole("button", { name: "New pilot" }).click();
  await page.getByLabel("Pilot name").fill(name);
  await page.getByLabel("Baseline start").fill(iso(-60));
  await page.getByLabel("Baseline end").fill(iso(-31));
  await page.getByLabel("Pilot start").fill(iso(-30));
  await page.getByLabel("Pilot end").fill(iso(29));
  await page.getByLabel("Intensive Care Unit").check();
  await page.getByLabel("Priya Raghavan").check();
  await page.getByLabel(/Reference ERP \(simulated\)/).check();
  await page.getByTestId("create-pilot").click();
  await expect(page.getByTestId("pilot-name")).toHaveText(name, { timeout: 30_000 });
  await expect(page.getByTestId("synthetic-banner")).toContainText("NOT REAL HOSPITAL DATA");
  await expect(page.getByTestId("pilot-status")).toHaveText("planned");
  await page.getByRole("button", { name: "Start pilot" }).click();
  await expect(page.getByTestId("pilot-status")).toHaveText("active", { timeout: 30_000 });
  // V9 sync through the pilot (the simulated reference ERP), then data quality from V9 runs
  await page.getByTestId("pilot-sync").click();
  await expect(page.getByTestId("reliability-table")).toContainText("Reference ERP (simulated)", { timeout: 120_000 });
  await expect(page.getByTestId("metric-data_quality_pct")).toBeVisible();
  await expect(page.getByTestId("metric-stockout_days")).toBeVisible();
  // feedback
  await page.getByLabel("Feedback about").selectOption("integration");
  await page.getByLabel("Timing problem").check();
  await page.getByLabel("Feedback comment").fill("ERP stock arrived after the morning count");
  await page.getByRole("button", { name: "Send feedback" }).click();
  await expect(page.getByTestId("metric-feedback_submitted")).toContainText("1", { timeout: 30_000 });
  // baseline vs pilot
  await page.getByRole("link", { name: "Baseline vs pilot" }).click();
  await expect(page.getByText("Descriptive baseline vs pilot comparison")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("causality")).toContainText("does not show that MedFlow caused");
  await expect(page.getByTestId("row-stockout_days")).toBeVisible();
  await page.getByLabel("Factor note").fill("Demo: supplier change in the pilot period");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByTestId("factors")).toContainText("supplier change in the pilot period", { timeout: 30_000 });
  // issue: log, then resolve (never touches inventory)
  await page.getByRole("link", { name: "Issues" }).click();
  await page.getByRole("button", { name: "Log issue" }).click();
  await page.getByLabel("Issue title").fill("Unknown material MAT-00999 in ERP consumption");
  await page.getByLabel("Issue category").selectOption("unknown_item");
  await page.getByRole("button", { name: "Log issue" }).last().click();
  const row = page.getByTestId("issues-table").getByRole("row", { name: /MAT-00999/ });
  await expect(row.getByTestId("issue-status")).toHaveText("open", { timeout: 30_000 });
  await row.getByRole("button", { name: "Resolve" }).click();
  await page.getByLabel("Resolution").fill("Simulator's deliberate invalid posting; ERP team informed");
  await page.getByRole("button", { name: "Mark resolved" }).click();
  await expect(row.getByTestId("issue-status")).toHaveText("resolved", { timeout: 30_000 });
  // readiness: automatic evidence + a manual confirmation
  await page.getByRole("link", { name: "Readiness" }).click();
  await expect(page.getByTestId("readiness-statement")).toContainText("not a claim");
  await expect(page.getByTestId("ready-initial_sync")).toContainText("every linked source has a successful run");
  await page.getByLabel("Confirm No patient data required").click(); // controlled by the server's state
  await expect(page.getByLabel("Confirm No patient data required")).toBeChecked({ timeout: 30_000 });
  // report
  await page.getByRole("link", { name: "Report" }).click();
  await expect(page.getByTestId("report-banner")).toHaveText("DEMO / SYNTHETIC DATA — NOT REAL HOSPITAL DATA", { timeout: 60_000 });
  await expect(page.getByTestId("result-label")).toHaveText("Synthetic/demo result");
  await expect(page.getByTestId("conclusion")).toContainText("No real-world outcome claim can be made yet.");
  await expect(page.getByTestId("limitations")).toContainText("does not show that MedFlow caused");
  await page.getByTestId("save-snapshot").click();
  await expect(page.getByTestId("snapshots")).toContainText("synthetic", { timeout: 30_000 });
});

test("recommendation views and feedback are captured during the active pilot", async ({ page }) => {
  await login(page, "procurement@sunrise.demo");
  const active = await (await page.request.get("/api/pilots/active")).json();
  test.skip(!active, "needs the active pilot from the previous test");
  const before = (await (await page.request.get(`/api/pilots/${active.id}/feedback`)).json()).items.length;
  await page.goto("/procurement?tab=recommendations");
  await expect(page.getByTestId("recommendation-cards").or(page.getByText("No pending recommendations"))).toBeVisible({ timeout: 60_000 });
  const toggle = page.getByRole("button", { name: /Show explanation/ });
  test.skip((await toggle.count()) === 0, "no recommendation left pending by the earlier specs");
  await toggle.first().click();
  const fb = page.getByTestId("rec-feedback");
  await expect(fb).toContainText("was this recommendation useful?", { timeout: 30_000 });
  await fb.getByLabel("Correct recommendation").check();
  await fb.getByRole("button", { name: "Send feedback" }).click();
  await expect(fb).toContainText("Feedback recorded", { timeout: 30_000 });
  const after = await (await page.request.get(`/api/pilots/${active.id}/feedback`)).json();
  expect(after.items.length).toBe(before + 1);
  expect(after.by_target.recommendation).toBeGreaterThanOrEqual(1);
  const decisions = (await (await page.request.get(`/api/pilots/${active.id}/decisions`)).json()) as { viewed: boolean }[];
  expect(decisions.some((d) => d.viewed)).toBeTruthy();
});

test("sandbox, roles and isolation", async ({ page }) => {
  await login(page, "admin@sunrise.demo");
  await page.goto("/pilots");
  await page.getByTestId("create-sandbox").click();
  await expect(page.getByTestId("pilot-name")).toContainText("Sandbox pilot — DEMO / SYNTHETIC DATA", { timeout: 30_000 });
  await expect(page.getByTestId("synthetic-banner")).toBeVisible();
  const sandboxId = Number(page.url().split("/").pop());
  // a viewer can read but not manage
  const other = await page.context().browser()!.newContext({ baseURL: test.info().project.use.baseURL });
  const v = await other.newPage();
  await login(v, "viewer@sunrise.demo");
  await v.goto("/pilots");
  await expect(v.getByTestId("pilots-table")).toBeVisible();
  await expect(v.getByRole("button", { name: "New pilot" })).toHaveCount(0);
  expect((await v.request.post("/api/pilots/sandbox")).status()).toBe(403);
  // another hospital's admin cannot reach it
  const o2 = await page.context().browser()!.newContext({ baseURL: test.info().project.use.baseURL });
  const l = await o2.newPage();
  await login(l, "admin@lakeview.demo");
  for (const path of ["", "/metrics", "/comparison", "/report", "/issues"]) {
    expect((await l.request.get(`/api/pilots/${sandboxId}${path}`)).status()).toBe(404);
  }
  await other.close();
  await o2.close();
});
