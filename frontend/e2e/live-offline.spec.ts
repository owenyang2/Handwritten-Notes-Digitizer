import { expect, test } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";

// Opt-in: this creates temporary accounts/notebooks. Use an isolated local API.
test.use({ serviceWorkers: "allow" });
test("live API: saved review, offline draft, shell reload, reconnect and cache isolation", async ({
  page,
  context,
  browserName,
}, testInfo) => {
  test.skip(
    process.env.HND_LIVE_API_E2E !== "1",
    "Requires an isolated local API on port 8000; see frontend/README.md.",
  );
  const username = `offline-check-${randomUUID()}`;
  const registered = await page.request.post("/api/auth/register", {
    data: {
      username,
      email: `${username}@example.test`,
      password: randomUUID(),
    },
  });
  expect(registered.ok()).toBe(true);
  const imported = await page.request.post("/api/documents/import", {
    multipart: {
      file: {
        name: "Synthetic offline source.png",
        mimeType: "image/png",
        buffer: await readFile(
          new URL("./fixtures/offline-source.png", import.meta.url),
        ),
      },
    },
  });
  expect(imported.ok()).toBe(true);
  const document = await imported.json();
  const source = document.pages[0];
  const crop = await page.request.post(`/api/pages/${source.page_id}/regions`, {
    data: {
      bbox: [20, 20, 400, 80],
      content_type: "text",
      coordinate_space: source.coordinate_space,
    },
  });
  expect(crop.ok()).toBe(true);
  const region = await crop.json();
  await page.goto(
    `/notebooks/${document.document_id}/pages/${source.page_id}?region=${region.region_id}`,
  );
  const text = page.getByRole("textbox", { name: "Corrected text" });
  await text.fill("A saved correction from the live offline check.");
  await page.getByTestId("math-notes-save").click();
  await expect(page.getByTestId("math-notes-status")).toContainText(
    "Review saved.",
  );
  await expect
    .poll(() =>
      page.evaluate(() => Boolean(navigator.serviceWorker.controller)),
    )
    .toBe(true);
  await page.reload();
  await expect(text).toHaveValue(
    "A saved correction from the live offline check.",
  );
  await text.fill("This unsaved draft stays while the page remains open.");
  await context.setOffline(true);
  await page.getByTestId("math-notes-save").click();
  await expect(page.getByTestId("math-notes-status")).toContainText(
    "Your correction is still here",
  );
  await expect(text).toHaveValue(
    "This unsaved draft stays while the page remains open.",
  );
  const privateCachedUrls = await page.evaluate(async () => {
    const result: string[] = [];
    for (const name of await caches.keys()) {
      for (const request of await (await caches.open(name)).keys()) {
        if (new URL(request.url).pathname.startsWith("/api/"))
          result.push(request.url);
      }
    }
    return result;
  });
  expect(privateCachedUrls).toEqual([]);
  page.once("dialog", (dialog) => dialog.accept()); // Deliberately leave the in-memory draft; no offline autosave claim.
  await page.reload();
  await expect(page.getByRole("status")).toContainText(
    "Cannot reach the server",
  );
  await context.setOffline(false);
  await page.getByRole("button", { name: "Retry connection" }).click();
  await expect(text).toHaveValue(
    "A saved correction from the live offline check.",
  );
  const evidence = {
    date: new Date().toISOString().slice(0, 10),
    browser: browserName,
    source: "Synthetic PNG/manual correction; no recognition evaluation",
    savedReviewReload: "passed",
    offlineDraftRetention: "passed while page remains open",
    offlineShellReload: "passed",
    reconnectSavedReview: "passed",
    privateApiCacheEntries: privateCachedUrls.length,
    unsavedDraftDurableAcrossReload: false,
  };
  await writeFile(
    `../docs/release-evidence/ui-revamp/live-offline-${browserName}.json`,
    JSON.stringify(evidence, null, 2),
  );
  await testInfo.attach("live-offline-evidence", {
    body: JSON.stringify(evidence, null, 2),
    contentType: "application/json",
  });
});
