import { test } from "@playwright/test";

// Run with SCREENSHOTS=1 to regenerate docs/screenshots.
test.skip(!process.env.SCREENSHOTS, "screenshots only on demand");

test("capture screens", async ({ page }) => {
  await page.goto("/login");
  await page.screenshot({ path: "../docs/screenshots/login.png" });
  await page.getByLabel("Email").fill("admin@sunrise.demo");
  await page.getByLabel("Password").fill("Demo@1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/dashboard/);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "../docs/screenshots/dashboard.png", fullPage: true });
  for (const [path, name] of [["/inventory", "inventory"], ["/movements", "movements"], ["/alerts", "alerts"], ["/suppliers", "suppliers"], ["/settings?tab=users", "settings-users"]]) {
    await page.goto(path);
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `../docs/screenshots/${name}.png`, fullPage: true });
  }
  await page.goto("/forecasts");
  await page.getByLabel("Item", { exact: true }).selectOption({ label: "Surgical gloves, sterile, size 7 (GLV-SRG-7)" });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "../docs/screenshots/forecast-item.png", fullPage: true });
  await page.goto("/forecasts?tab=evaluation");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "../docs/screenshots/forecast-evaluation.png", fullPage: true });
  await page.goto("/forecasts?tab=all");
  await page.waitForTimeout(1200);
  await page.screenshot({ path: "../docs/screenshots/forecast-all.png", fullPage: true });
  for (const [path, name] of [["/procedures", "procedures-schedule"], ["/procedures?tab=types", "procedures-types"], ["/procedures?tab=mappings", "procedures-mappings"]]) {
    await page.goto(path);
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `../docs/screenshots/${name}.png`, fullPage: true });
  }
  await page.goto("/forecasts");
  await page.getByLabel("Item", { exact: true }).selectOption({ label: "Absorbable suture, polyglactin 1-0 (SUT-VIC-1)" });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "../docs/screenshots/forecast-procedure-impact.png", fullPage: true });
  await page.goto("/stockout-risks");
  await page.waitForTimeout(1500);
  await page.getByTestId("risk-table").locator("tbody tr").filter({ hasNotText: "Out of stock" }).first().click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "../docs/screenshots/stockout-risk.png", fullPage: true });
  await page.goto("/stockout-risks?tab=evaluation");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "../docs/screenshots/stockout-risk-evaluation.png", fullPage: true });
  await page.goto("/supplier-intelligence");
  await page.waitForTimeout(1500);
  await page.getByTestId("at-risk-table").locator("tbody tr").filter({ hasText: "days (" }).first().click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "../docs/screenshots/supplier-intelligence.png", fullPage: true });
  await page.goto("/supplier-intelligence?tab=scorecards");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "../docs/screenshots/supplier-scorecards.png", fullPage: true });
  await page.goto("/supplier-intelligence?tab=evaluation");
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "../docs/screenshots/supplier-evaluation.png", fullPage: true });
  for (const [path, name] of [["/procurement", "procurement-attention"], ["/procurement?tab=recommendations", "procurement-recommendations"], ["/procurement?tab=approvals", "procurement-approvals"], ["/procurement?tab=cost-model", "procurement-cost-model"]]) {
    await page.goto(path);
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `../docs/screenshots/${name}.png`, fullPage: true });
  }
  await page.goto("/procurement");
  await page.getByTestId("attention-table").locator("tbody tr").first().locator("button").first().click();
  await page.getByTestId("plan-view").waitFor({ timeout: 60_000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "../docs/screenshots/procurement-scenarios.png", fullPage: true });
  await page.goto(page.url().replace("tab=scenarios", "tab=what-if"));
  await page.getByLabel("Supplier delayed").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Run what-if" }).click();
  await page.getByTestId("whatif-summary").waitFor({ timeout: 60_000 });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "../docs/screenshots/procurement-what-if.png", fullPage: true });
  await page.goto("/knowledge-graph");
  await page.getByLabel("Why is this item at risk?").selectOption({ label: "Absorbable suture, polyglactin 2-0 (SUT-VIC-20)" });
  await page.getByTestId("explain-chain").waitFor({ timeout: 60_000 });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "../docs/screenshots/knowledge-graph-explain.png", fullPage: true });
  await page.goto("/knowledge-graph?tab=impact");
  await page.getByLabel("Supplier").selectOption({ label: "Cauvery Pharma & Surgicals" });
  await page.getByTestId("impact-summary").waitFor({ timeout: 60_000 });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "../docs/screenshots/knowledge-graph-impact.png", fullPage: true });
  await page.goto("/knowledge-graph?tab=search");
  await page.getByLabel("Question").selectOption({ label: "Which scheduled procedures could be affected by an item shortage?" });
  await page.getByRole("button", { name: "Run" }).click();
  await page.getByTestId("query-result").waitFor({ timeout: 60_000 });
  await page.screenshot({ path: "../docs/screenshots/knowledge-graph-search.png", fullPage: true });
  await page.goto("/knowledge-graph?tab=sync");
  await page.getByTestId("graph-schema").waitFor({ timeout: 60_000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: "../docs/screenshots/knowledge-graph-sync.png", fullPage: true });
  await page.goto("/assistant?q=Why%20is%20Suture%202-0%20at%20risk%3F");
  await page.getByTestId("answer").first().waitFor({ timeout: 60_000 });
  await page.getByTestId("question-input").fill("Which suppliers can cover it within 4 days?");
  await page.getByTestId("ask").click();
  await page.getByTestId("answer").nth(1).waitFor({ timeout: 60_000 });
  await page.getByTestId("answer").first().getByRole("button", { name: /Evidence/ }).click();
  await page.waitForTimeout(1000);
  await page.screenshot({ path: "../docs/screenshots/assistant.png", fullPage: true });
  await page.goto("/suppliers");
  await page.getByRole("link", { name: "Nandi Medisupplies" }).click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: "../docs/screenshots/supplier-performance.png", fullPage: true });
  await page.goto("/inventory");
  await page.getByRole("link", { name: "Glucometer test strip" }).click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "../docs/screenshots/item-detail.png", fullPage: true });
});
