import { expect, test, type Page } from "@playwright/test";

// V6 — operational knowledge graph. Requires the stack with demo data, trained models and a reachable graph store
// (Neo4j via docker compose, or FalkorDB for development: GRAPH_BACKEND=falkordb GRAPH_URL=redis://…).

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("Demo@1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

test("explain a risk: the chain traverses risk, forecast, procedures, departments and suppliers", async ({ page }) => {
  await login(page, "viewer@sunrise.demo");
  await page.getByRole("link", { name: "Knowledge graph", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Knowledge graph" })).toBeVisible();
  await page.getByLabel("Why is this item at risk?").selectOption({ label: "Absorbable suture, polyglactin 2-0 (SUT-VIC-20)" });
  const chain = page.getByTestId("explain-chain");
  await expect(chain).toBeVisible({ timeout: 60_000 });
  await expect(chain).toContainText("stockout risk");
  await expect(chain).toContainText("Forecast demand");
  await expect(chain).toContainText(/scheduled procedures in the next 14 days use it/);
  await expect(chain).toContainText("Hernia repair");
  await expect(chain).toContainText(/past orders delivered within/);
  await expect(page.getByTestId("graph-view")).toBeVisible();
  await page.getByRole("button", { name: "Show Cypher" }).click();
  await expect(page.getByTestId("cypher")).toContainText("MATCH (p:Procedure)-[u:USES_ITEM]->(i:Item {key: $item})");
});

test("impact analysis: a supplier becoming unavailable and an item running short", async ({ page }) => {
  await login(page, "procurement@sunrise.demo");
  await page.goto("/knowledge-graph?tab=impact");
  await page.getByLabel("Supplier").selectOption({ label: "OrthoPrime Consumables" });
  await expect(page.getByTestId("impact-summary")).toContainText("If OrthoPrime Consumables becomes unavailable", { timeout: 60_000 });
  await expect(page.getByTestId("impact-items")).toContainText("sole source");
  await expect(page.getByTestId("impact-chain").first()).toContainText("Supplier");
  await page.getByLabel("Impact of").selectOption({ label: "An item running short" });
  await page.getByLabel("Item").selectOption({ label: "Absorbable suture, polyglactin 2-0 (SUT-VIC-20)" });
  await expect(page.getByTestId("impact-procedures")).toContainText("Hernia repair", { timeout: 60_000 });
  await expect(page.getByTestId("impact-summary")).toContainText("procedure type(s) use it");
});

test("graph search runs predefined questions and shows the Cypher; sync status is verified", async ({ page }) => {
  await login(page, "inventory@sunrise.demo");
  await page.goto("/knowledge-graph?tab=search");
  await page.getByLabel("Question").selectOption({ label: "Which high/medium-risk items have only one supplier?" });
  await page.getByRole("button", { name: "Run" }).click();
  const res = page.getByTestId("query-result");
  await expect(res).toBeVisible({ timeout: 60_000 });
  await expect(res).toContainText("row(s)");
  await page.getByLabel("Question").selectOption({ label: "Which procedures use this item (and how heavily)?" });
  await page.getByLabel("Item").selectOption({ label: "Absorbable suture, polyglactin 2-0 (SUT-VIC-20)" });
  await page.getByRole("button", { name: "Run" }).click();
  await expect(page.getByTestId("query-result")).toContainText("Hernia repair");

  await page.goto("/knowledge-graph?tab=sync");
  await expect(page.getByTestId("graph-backend")).toBeVisible();
  await page.getByRole("button", { name: "Sync now" }).click();
  await expect(page.getByText(/Graph synced — .* nodes, verified ✓/)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("sync-counts")).toContainText("match");
  await expect(page.getByTestId("sync-counts")).not.toContainText("differs");
  await expect(page.getByTestId("graph-schema")).toContainText("USES_ITEM");
});

test("stockout risk and supplier pages link into the graph; viewers cannot sync", async ({ page }) => {
  await login(page, "viewer@sunrise.demo");
  await page.goto("/stockout-risks");
  await page.getByTestId("risk-table").locator("tbody tr").first().click();
  await page.getByRole("link", { name: "Explain in graph" }).click();
  await expect(page.getByTestId("explain-chain")).toBeVisible({ timeout: 60_000 });
  await page.goto("/knowledge-graph?tab=sync");
  await expect(page.getByTestId("graph-backend")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sync now" })).toHaveCount(0);
});
