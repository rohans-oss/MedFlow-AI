import { expect, test, type Page } from "@playwright/test";

// V7 — AI operations assistant (default deterministic planner, no LLM). Requires the stack with demo data, trained models
// and a reachable graph store (the graph-backed answers say so when it is not).

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

test("ask why an item is at risk, follow up with 'it', and inspect the evidence", async ({ page }) => {
  await login(page, "procurement@sunrise.demo");
  await page.getByRole("link", { name: "AI assistant", exact: true }).click();
  await expect(page.getByTestId("assistant-status")).toContainText("Deterministic planner (no LLM configured)");
  await expect(page.getByTestId("examples")).toContainText("Why is Suture 2-0 at risk?");

  await page.getByTestId("examples").getByRole("button", { name: "Why is Suture 2-0 at risk?" }).click();
  const a = page.getByTestId("answer").first();
  await expect(a).toBeVisible({ timeout: 60_000 });
  await expect(a.getByTestId("answer-title")).toHaveText("Why Absorbable suture, polyglactin 2-0 is at risk");
  await expect(a.getByTestId("answer-summary")).toContainText(/stockout risk \(\d+% within 14 days\)/);
  await expect(a.getByTestId("answer-mode")).toHaveText("Deterministic planner");
  await a.getByRole("button", { name: /Evidence: 2 tool calls/ }).click();
  await expect(a.getByTestId("evidence-list")).toContainText("item_status");
  await expect(a.getByTestId("evidence-list")).toContainText("graph_explain");

  const b = await ask(page, "Which suppliers can cover it within 4 days?");
  await expect(b.getByTestId("answer-title")).toHaveText("Suppliers for Absorbable suture, polyglactin 2-0");
  await expect(b.getByTestId("answer-points")).toContainText(/Cauvery Pharma & Surgicals .* in \d+ of \d+ past orders/);

  await expect(page.getByTestId("conversation-list")).toContainText("Why is Suture 2-0 at risk?");
});

test("supplier impact, single-source items and the daily review", async ({ page }) => {
  await login(page, "inventory@sunrise.demo");
  await page.goto("/assistant");
  const a = await ask(page, "What happens if CPS becomes unavailable?");
  await expect(a.getByTestId("answer-summary")).toContainText("If Cauvery Pharma & Surgicals becomes unavailable");
  const b = await ask(page, "Show me all high-risk single-source items");
  await expect(b.getByTestId("answer-summary")).toContainText("Which high/medium-risk items have only one supplier?");
  await expect(b.getByTestId("answer-points")).toContainText(/sku: .*suppliers: OrthoPrime Consumables/); // demo single-source items
  const c = await ask(page, "What should I review today?");
  await expect(c.getByTestId("answer-summary")).toContainText(/Today: \d+ high-risk item\(s\)/);
});

test("the assistant is read-only: write requests are refused before any tool runs", async ({ page }) => {
  await login(page, "procurement@sunrise.demo");
  await page.goto("/assistant");
  const a = await ask(page, "Approve the recommendation for suture 2-0");
  await expect(a.getByTestId("answer-title")).toHaveText("Read-only assistant");
  await expect(a.getByTestId("answer-summary")).toContainText("I can't approve, order, reject or change anything");
  await expect(a).toContainText("No MedFlow tool was called for this answer.");
  await expect(a.getByRole("link", { name: "Approval queue" })).toBeVisible();
});

test("a conversation can be reopened from the list", async ({ page }) => {
  await login(page, "viewer@sunrise.demo");
  await page.goto("/assistant?q=Which%20supplier%20has%20the%20most%20delays%3F");
  await expect(page.getByTestId("answer").first().getByTestId("answer-title")).toHaveText("Supplier delays", { timeout: 60_000 });
  await page.getByTestId("new-conversation").click();
  await expect(page.getByTestId("examples")).toBeVisible();
  await page.getByTestId("conversation-list").getByRole("button", { name: /Which supplier has the most delays\?/ }).first().click();
  await expect(page.getByTestId("question").first()).toHaveText("Which supplier has the most delays?");
  await expect(page.getByTestId("answer").first().getByTestId("answer-title")).toHaveText("Supplier delays");
});
