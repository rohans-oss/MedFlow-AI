import { expect, test } from "@playwright/test";

// Audit regression (V0–V10 audit, bug UI-1): at phone width (390 px) cards inside single-column grids were as wide as
// their tables' content, so the whole page scrolled sideways (dashboard 47 px, item 113 px, supplier 115 px too wide).
// Tables must scroll inside their card instead.
test.use({ viewport: { width: 390, height: 844 } });

test("no horizontal page overflow at phone width", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("admin@sunrise.demo");
  await page.getByLabel("Password").fill("Demo@1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  const items = (await (await page.request.get("/api/consumables")).json()) as { id: number }[];
  const suppliers = (await (await page.request.get("/api/suppliers")).json()) as { id: number }[];
  for (const path of ["/dashboard", `/inventory/${items[0].id}`, `/suppliers/${suppliers[0].id}`, "/procurement", "/pilots"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `${path} overflows by ${overflow}px`).toBeLessThanOrEqual(0);
  }
});
