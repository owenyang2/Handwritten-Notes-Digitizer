import { expect, test } from "@playwright/test";

test.use({ serviceWorkers: "allow" });

test("development preview recovers from cached modules without clearing preferences", async ({ page }) => {
  const preview = process.env.HND_DEV_PREVIEW_URL;
  test.skip(!preview, "Requires a running Vite development preview; set HND_DEV_PREVIEW_URL.");
  await page.context().route("**/api/auth/me", route => route.fulfill({
    status: 401, contentType: "application/json", body: '{"detail":"Not signed in"}',
  }));
  await page.goto(`${preview}/e2e/fixtures/preview`);
  await expect(page.getByRole("heading", { name: "Handwritten notes" })).toBeVisible();
  await page.evaluate(async () => {
    localStorage.setItem("notes-appearance", "dark");
    await navigator.serviceWorker.register("/e2e/fixtures/legacy-preview-sw.js");
    const cache = await caches.open("notes-digitizer-legacy-regression");
    await cache.put("/src/appearance.ts", new Response("export const stale = true;", {
      headers: { "content-type": "application/javascript" },
    }));
  });
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller?.scriptURL ?? ""))
    .toContain("legacy-preview-sw.js");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Handwritten notes" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Theme", exact: true })).toHaveValue("dark");
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker.controller === null)).toBe(true);
});
