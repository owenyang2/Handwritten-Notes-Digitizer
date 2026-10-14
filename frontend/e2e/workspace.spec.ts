import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { writeFile } from "node:fs/promises";

test.beforeEach(({ page }) => {
  page.on("pageerror", (error) => {
    throw error;
  });
});

const notebook = {
  document_id: "doc-1",
  title: "Linear algebra — lecture notes",
  created_at: "2026-10-12T12:00:00Z",
  image_ids: ["image-1"],
  pages: [0, 1].map((index) => ({
    page_id: `page-${index + 1}`,
    page_index: index,
    image_id: `image-${index + 1}`,
    width: 600,
    height: 800,
    dpi: 200,
    coordinate_space: "page-raster-v1",
  })),
};
const regions = [
  {
    region_id: "region-1",
    page_id: "page-1",
    content_type: "text",
    bbox: [60, 80, 480, 70],
    order_index: 0,
    prediction_text: "Eigenvectors keep their direction.",
    reviewed_text: null as string | null,
    excluded: false,
    unresolved: true,
    prediction_id: "prediction-1",
    review_version: 1,
    crop_revision: 1,
  },
  {
    region_id: "region-2",
    page_id: "page-1",
    content_type: "math_display",
    bbox: [60, 200, 400, 90],
    order_index: 1,
    prediction_text: "Av = \\lambda v",
    reviewed_text: null as string | null,
    excluded: false,
    unresolved: true,
    prediction_id: "prediction-2",
    review_version: 1,
    crop_revision: 1,
  },
];
const source = `<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="#fffef9"/><g stroke="#e5e4df">${Array.from({ length: 22 }, (_, index) => `<path d="M30 ${60 + index * 32}H570"/>`).join("")}</g><g fill="#303735" font-family="cursive" font-size="24"><text x="60" y="115">Eigenvectors keep their direction.</text><text x="60" y="250">Av = λv</text></g></svg>`;
async function fixture(
  page: Page,
  options: {
    empty?: boolean;
    conflict?: boolean;
    failedImport?: boolean;
    expiredSession?: boolean;
  } = {},
) {
  const saved: Array<Record<string, unknown>> = [];
  const exported: Array<Record<string, unknown>> = [];
  let imported = false;
  let authenticated = !options.expiredSession;
  const state = structuredClone(regions);
  await page.context().route("**/api/**", async (route) => {
    const request = route.request();
    if (!new URL(request.url()).pathname.startsWith("/api/"))
      return route.continue();
    const path = new URL(request.url()).pathname.replace(/^\/api/, "");
    const json = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    if (path === "/auth/login") {
      authenticated = true;
      return json({ user_id: "user-1", username: "Test workspace" });
    }
    if (path === "/auth/me")
      return json({ user_id: "user-1", username: "Test workspace" });
    if (path === "/health")
      return json({
        status: "ok",
        capabilities: Object.fromEntries(
          [
            "upload",
            "recognition",
            "correction",
            "export",
            "calibration",
            "personalization",
          ].map((key) => [
            key,
            { available: !["calibration", "personalization"].includes(key) },
          ]),
        ),
      });
    if (path === "/documents")
      return json(options.empty && !imported ? [] : [notebook]);
    if (path === "/documents/import") {
      if (options.failedImport)
        return json(
          { detail: "File could not be prepared. Try another PDF." },
          422,
        );
      imported = true;
      return json(notebook);
    }
    if (path === "/documents/doc-1") return json(notebook);
    if (path.endsWith("/raster") || path.endsWith("/processed"))
      return route.fulfill({ contentType: "image/svg+xml", body: source });
    if (path === "/pages/page-1/regions") return json(state);
    if (path === "/pages/page-2/regions") return json([]);
    if (path.startsWith("/regions/") && path.endsWith("/review")) {
      if (!authenticated) return json({ detail: "Session expired" }, 401);
      saved.push(request.postDataJSON());
      if (options.conflict)
        return json({ detail: "Review version conflict" }, 409);
      const region = state.find((item) => path.includes(item.region_id))!;
      region.reviewed_text = String(request.postDataJSON().reviewed_text);
      region.review_version += 1;
      return json({
        region_id: region.region_id,
        version: region.review_version,
        document_revision_id: `saved-${saved.length}`,
        excluded: request.postDataJSON().excluded,
        training_example_id: null,
      });
    }
    if (path.endsWith("/exports")) {
      exported.push(request.postDataJSON());
      return json({ format: "markdown", text: "Saved notes" });
    }
    if (path === "/jobs/job-1")
      return json({
        job_id: "job-1",
        state: "succeeded",
        progress_total: 2,
        progress_current: 2,
      });
    if (path.endsWith("recognition-jobs"))
      return json({ job_id: "job-1", state: "queued" });
    if (path.startsWith("/consent")) return json({ granted: false });
    return json({ items: [], conflicts: [], has_more: false, next_cursor: 0 });
  });
  return { saved, exported };
}

test("import → source selection → correction → explicit export; drafts survive layout and theme changes", async ({
  page,
}) => {
  const { saved, exported } = await fixture(page, { empty: true });
  await page.goto("/library");
  await expect(
    page.getByRole("heading", { name: "Your next idea starts here" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Import notes", exact: true })
    .first()
    .click();
  await page.getByTestId("upload-file-input").setInputFiles({
    name: "lecture.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF fixture"),
  });
  await page.getByTestId("upload-submit-btn").click();
  await expect(
    page.getByRole("heading", { name: "Formula review" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Select original region 1" }).click();
  const text = page.getByRole("textbox", { name: "Corrected text" });
  await text.fill("Eigenvectors preserve their direction.");
  await page.getByRole("button", { name: /Review math region/ }).click();
  await page.getByRole("button", { name: /Review text region/ }).click();
  await expect(text).toHaveValue("Eigenvectors preserve their direction.");
  await page.setViewportSize({ width: 320, height: 700 });
  await expect(text).toHaveValue("Eigenvectors preserve their direction.");
  await page.getByRole("button", { name: "Original", exact: true }).click();
  await expect(page.getByTestId("page-viewer")).toBeVisible();
  await page.getByRole("button", { name: "Result", exact: true }).click();
  await page
    .getByRole("button", { name: "Open navigation", exact: true })
    .click();
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page.getByRole("combobox", { name: "Appearance" }).selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.goBack();
  await expect(text).toHaveValue("Eigenvectors preserve their direction.");
  await text.fill("Eigenvectors preserve their direction.");
  await page
    .getByRole("button", { name: "Export", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog", { name: "Export notebook" });
  await dialog
    .getByRole("button", { name: "Export last saved version", exact: true })
    .click();
  await expect.poll(() => exported.length).toBe(1);
  expect(saved).toHaveLength(0);
  await dialog
    .getByRole("button", { name: "Save and export", exact: true })
    .click();
  await expect.poll(() => exported.length).toBe(2);
  expect(saved[0].reviewed_text).toBe("Eigenvectors preserve their direction.");
  expect(saved[0].include_in_training).toBe(false);
  expect(exported[1].revision_id).toBe("saved-1");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("conflict retains the draft and exposes comparison; deep link and browser back preserve context", async ({
  page,
}) => {
  await fixture(page, { conflict: true });
  await page.goto("/notebooks/doc-1/pages/page-1?region=region-1");
  const text = page.getByRole("textbox", { name: "Corrected text" });
  await text.fill("My correction stays here.");
  await page.getByTestId("math-notes-save").click();
  await expect(page.getByTestId("math-notes-status")).toContainText(
    "Your correction is still here",
  );
  await expect(text).toHaveValue("My correction stays here.");
  await page.getByRole("button", { name: "Compare saved review" }).click();
  await expect(
    page.getByRole("heading", { name: "Last saved review" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Appearance" })
    .selectOption("light");
  await page.goBack();
  await expect(text).toHaveValue("My correction stays here.");
  await expect(page.getByTestId("math-notes-status")).toContainText(
    "Your correction is still here",
  );
});

test("system appearance follows OS, overrides persist; failed import stays recoverable", async ({
  page,
}) => {
  await fixture(page, { failedImport: true });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/settings");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("combobox", { name: "Appearance" }).selectOption("dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Import notes", exact: true }).click();
  await page.getByTestId("upload-file-input").setInputFiles({
    name: "bad.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF fixture"),
  });
  await page.getByTestId("upload-submit-btn").click();
  await expect(page.getByTestId("upload-status")).toContainText(
    "File could not be prepared",
  );
  await expect(page.getByTestId("upload-submit-btn")).toBeEnabled();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Import notes", exact: true }),
  ).toBeFocused();
});

test("visual states reflow at 320–1440px in both appearances", async ({
  page,
}, testInfo) => {
  await fixture(page);
  for (const theme of ["light", "dark"]) {
    await page.addInitScript(
      (value) => localStorage.setItem("notes-appearance", value),
      theme,
    );
    await page.goto("/library");
    await expect(
      page.getByRole("heading", { name: notebook.title }),
    ).toBeVisible();
    for (const width of [320, 640, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      if (testInfo.project.name === "chromium" && [320, 1440].includes(width))
        await page.screenshot({
          path: `../docs/release-evidence/ui-revamp/library-${theme}-${width}.png`,
          fullPage: true,
        });
    }
    await page.goto("/notebooks/doc-1/pages/page-1?region=region-1");
    await expect(
      page.getByRole("textbox", { name: "Corrected text" }),
    ).toBeVisible();
    for (const width of [1440, 320]) {
      await page.setViewportSize({ width, height: 900 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      if (testInfo.project.name === "chromium")
        await page.screenshot({
          path: `../docs/release-evidence/ui-revamp/review-${theme}-${width}.png`,
          fullPage: true,
        });
    }
  }
});

test("returning to Library preserves search and scroll position", async ({
  page,
}) => {
  await fixture(page);
  await page.route("**/api/documents?*", (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        ...Array.from({ length: 24 }, (_, index) => ({
          ...notebook,
          document_id: `extra-${index}`,
          title: `Lecture notes ${index + 1}`,
        })),
        notebook,
      ]),
    }),
  );
  await page.goto("/library");
  const search = page.getByRole("searchbox", { name: "Search notes" });
  const filtered = page.waitForResponse((response) =>
    response.url().includes("query=notes"),
  );
  await search.fill("notes");
  await filtered;
  await expect(page.locator(".gallery-document")).toHaveCount(25);
  await page.locator(".gallery-document").last().scrollIntoViewIfNeeded();
  const position = await page.evaluate(() => scrollY);
  expect(position).toBeGreaterThan(100);
  await page.locator(".gallery-document").last().click();
  await expect(
    page.getByRole("heading", { name: "Formula review" }),
  ).toBeVisible();
  await page.goBack();
  await expect(search).toHaveValue("notes");
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(position);
});

test("accessibility scan covers Library, Settings, review and dialogs in both appearances", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  await fixture(page);
  const scans: Array<{
    theme: string;
    screen: string;
    violations: unknown[];
    incomplete: unknown[];
  }> = [];
  const scan = async (theme: string, screen: string) => {
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    scans.push({
      theme,
      screen: `${page.viewportSize()?.width}px ${screen}`,
      violations: result.violations,
      incomplete: result.incomplete.map((rule) => ({
        id: rule.id,
        help: rule.help,
        targets: rule.nodes.map((node) => node.target),
      })),
    });
    expect.soft(result.violations, `${theme} ${screen}`).toEqual([]);
  };
  for (const width of [1280, 320]) {
    await page.setViewportSize({ width, height: 900 });
    for (const theme of ["light", "dark"]) {
      await page.addInitScript(
        (value) => localStorage.setItem("notes-appearance", value),
        theme,
      );
      await page.goto("/library");
      await expect(
        page.getByRole("heading", { name: notebook.title }),
      ).toBeVisible();
      await scan(theme, "Library");
      if (width === 320) {
        await page
          .getByRole("button", { name: "Open navigation", exact: true })
          .click();
        await scan(theme, "Navigation drawer");
        await page.keyboard.press("Escape");
      }
      await page
        .getByRole("button", { name: `Actions for ${notebook.title}` })
        .click();
      await scan(theme, "Notebook menu");
      await page.keyboard.press("Escape");
      await page
        .getByRole("button", { name: "Import notes", exact: true })
        .click();
      await scan(theme, "Import dialog");
      await page.getByRole("button", { name: "Close", exact: true }).click();
      await page.goto("/settings");
      await expect(
        page.getByRole("combobox", { name: "Appearance" }),
      ).toBeVisible();
      await scan(theme, "Settings");
      await page.goto("/notebooks/doc-1/pages/page-1?region=region-1");
      await expect(
        page.getByRole("textbox", { name: "Corrected text" }),
      ).toBeVisible();
      await scan(theme, "Text review");
      await page.getByRole("button", { name: /Review math region/ }).click();
      await scan(theme, "Formula review");
      await page
        .getByRole("button", { name: "Export", exact: true })
        .first()
        .click();
      await scan(theme, "Export dialog");
      await page
        .getByRole("dialog", { name: "Export notebook" })
        .getByRole("button", { name: "Close", exact: true })
        .click();
    }
  }
  if (testInfo.project.name === "chromium")
    await writeFile(
      "../docs/release-evidence/ui-revamp/accessibility-results.json",
      JSON.stringify(scans, null, 2),
    );
  await testInfo.attach("accessibility-results", {
    body: JSON.stringify(scans, null, 2),
    contentType: "application/json",
  });
});

test("keyboard navigation traps dialogs, restores focus, and selects source regions", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/library");
  const trigger = page.getByRole("button", {
    name: "Import notes",
    exact: true,
  });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Import notes" });
  await expect(dialog).toBeVisible();
  for (let index = 0; index < 14; index++) {
    await page.keyboard.press(index < 7 ? "Tab" : "Shift+Tab");
    expect(
      await dialog.evaluate((node) => node.contains(document.activeElement)),
    ).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await page.goto("/notebooks/doc-1/pages/page-1?region=region-1");
  const sourceButton = page.getByRole("button", {
    name: "Select original region 2",
  });
  await sourceButton.focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("button", { name: /Review math region/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(sourceButton).toBeFocused();
});

test("failed network save retains correction and succeeds after retry", async ({
  page,
}) => {
  const { saved } = await fixture(page);
  await page.goto("/notebooks/doc-1/pages/page-1?region=region-1");
  const text = page.getByRole("textbox", { name: "Corrected text" });
  await text.fill("Keep this correction while disconnected.");
  await page.route("**/api/regions/*/review", (route) =>
    route.abort("internetdisconnected"),
  );
  await page.getByTestId("math-notes-save").click();
  await expect(page.getByTestId("math-notes-status")).toContainText(
    "Your correction is still here",
  );
  await expect(text).toHaveValue("Keep this correction while disconnected.");
  expect(saved).toHaveLength(0);
  await page.unroute("**/api/regions/*/review");
  await page.getByTestId("math-notes-save").click();
  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0].reviewed_text).toBe(
    "Keep this correction while disconnected.",
  );
});

test("expired session can sign in again without losing the correction", async ({
  page,
}) => {
  const { saved } = await fixture(page, { expiredSession: true });
  await page.goto("/notebooks/doc-1/pages/page-1?region=region-1");
  const text = page.getByRole("textbox", { name: "Corrected text" });
  await text.fill("Keep this correction through sign in.");
  await page.getByTestId("math-notes-save").click();
  await expect(page.getByTestId("math-notes-status")).toContainText(
    "Your session expired",
  );
  await page.getByRole("link", { name: "Settings", exact: true }).click();
  await page
    .getByRole("button", { name: "Sign in again", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Sign in again" });
  await dialog
    .getByRole("textbox", { name: "Username", exact: true })
    .fill("sample");
  await dialog.getByLabel("Password", { exact: true }).fill("sample-password");
  await dialog.getByRole("button", { name: "Log in", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page.goBack();
  await expect(text).toHaveValue("Keep this correction through sign in.");
  await page.getByTestId("math-notes-save").click();
  await expect.poll(() => saved.length).toBe(1);
});

test("record local lab render and interaction measurements", async ({
  page,
  browserName,
  browser,
}, testInfo) => {
  test.skip(
    browserName !== "chromium",
    "Event Timing and CPU throttling baseline uses Chromium.",
  );
  test.setTimeout(120_000);
  await fixture(page);
  await page.addInitScript(() => {
    const metrics = {
      lcp: 0,
      shifts: [] as Array<{ value: number; time: number; sources: unknown[] }>,
      interactions: [] as number[],
    };
    (window as any).__uiLab = metrics;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) metrics.lcp = entry.startTime;
    }).observe({ type: "largest-contentful-paint", buffered: true });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as any)
        if (!entry.hadRecentInput)
          metrics.shifts.push({
            value: entry.value,
            time: entry.startTime,
            sources: entry.sources.map((source: any) => ({
              node: source.node?.className,
              previous: source.previousRect,
              current: source.currentRect,
            })),
          });
    }).observe({ type: "layout-shift", buffered: true });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as any)
        if (entry.interactionId) metrics.interactions.push(entry.duration);
    }).observe({ type: "event", buffered: true, durationThreshold: 16 } as any);
  });
  const session = await page.context().newCDPSession(page);
  const samples = [];
  for (const profile of [
    { name: "desktop", width: 1440, cpu: 1 },
    { name: "compact-4x-cpu", width: 390, cpu: 4 },
  ]) {
    await session.send("Emulation.setCPUThrottlingRate", { rate: profile.cpu });
    await page.setViewportSize({ width: profile.width, height: 900 });
    for (let run = 1; run <= 5; run++) {
      await page.goto("/notebooks/doc-1/pages/page-1?region=region-1");
      const correction = page.getByRole("textbox", { name: "Corrected text" });
      await expect(correction).toBeVisible();
      await page.waitForTimeout(1000); // Collect paint/shift observers after rendering settles.
      await correction.click();
      await correction.pressSequentially(" Keep the original close.", {
        delay: 30,
      });
      await page.getByRole("button", { name: /Review math region/ }).click();
      await page.waitForTimeout(250); // Allow Event Timing delivery after the interaction paints.
      const metrics = await page.evaluate(() => {
        const data = (window as any).__uiLab;
        let cls = 0,
          total = 0,
          first = 0,
          previous = 0;
        for (const shift of data.shifts) {
          if (shift.time - previous > 1000 || shift.time - first > 5000) {
            total = 0;
            first = shift.time;
          }
          total += shift.value;
          previous = shift.time;
          cls = Math.max(cls, total);
        }
        return {
          lcpMs: data.lcp,
          cls,
          layoutShiftSources: data.shifts,
          maxObservedInteractionMs: data.interactions.length
            ? Math.max(...data.interactions)
            : null,
        };
      });
      samples.push({ profile: profile.name, run, ...metrics });
    }
  }
  await session.send("Emulation.setCPUThrottlingRate", { rate: 1 });
  const report = {
    date: new Date().toISOString().slice(0, 10),
    browser: browser.version(),
    scope:
      "Synthetic two-region notebook; local production preview, mocked API, first run can include cold assets; five runs per profile. CPU throttling is not a real mobile-device measurement. Largest observed Event Timing duration is not field INP. No field p75 claim.",
    samples,
  };
  await writeFile(
    "../docs/release-evidence/ui-revamp/local-lab-performance.json",
    JSON.stringify(report, null, 2),
  );
  await testInfo.attach("local-lab-performance", {
    body: JSON.stringify(report, null, 2),
    contentType: "application/json",
  });
});

test.describe("offline shell recovery", () => {
  test.use({ serviceWorkers: "allow" });
  test("cached shell explains offline reopening and reconnects without caching private API data", async ({
    page,
    context,
    browserName,
  }) => {
    test.skip(
      browserName === "webkit",
      "WebKit service-worker requests bypass API interception; run live-offline.spec.ts against an isolated API instead.",
    );
    await fixture(page);
    await page.goto("/notebooks/doc-1/pages/page-1?region=region-1");
    await expect(
      page.getByRole("textbox", { name: "Corrected text" }),
    ).toBeVisible();
    await expect
      .poll(() =>
        page.evaluate(() => Boolean(navigator.serviceWorker.controller)),
      )
      .toBe(true);
    await page.reload(); // Warm the assets through the active worker, including the deferred review chunk.
    const text = page.getByRole("textbox", { name: "Corrected text" });
    await expect(text).toBeVisible();
    await text.fill("Keep my in-memory correction while offline.");
    await context.unroute("**/api/**");
    await context.setOffline(true);
    await page.getByTestId("math-notes-save").click();
    await expect(page.getByTestId("math-notes-status")).toContainText(
      "Your correction is still here",
    );
    await expect(text).toHaveValue(
      "Keep my in-memory correction while offline.",
    );
    const cachedUrls = await page.evaluate(async () => {
      const urls: string[] = [];
      for (const name of await caches.keys()) {
        const cache = await caches.open(name);
        urls.push(...(await cache.keys()).map((request) => request.url));
      }
      return urls;
    });
    expect(
      cachedUrls.some((url) => new URL(url).pathname.startsWith("/api/")),
    ).toBe(false);
    page.once("dialog", (dialog) => dialog.accept()); // Explicitly leave the unsaved in-memory draft to test offline reload.
    await page.reload();
    await expect(page.getByRole("status")).toContainText(
      "Cannot reach the server",
    );
    await expect(
      page.getByRole("button", { name: "Retry connection" }),
    ).toBeEnabled();
    await context.setOffline(false);
    await fixture(page);
    await page.getByRole("button", { name: "Retry connection" }).click();
    await expect(
      page.getByRole("textbox", { name: "Corrected text" }),
    ).toBeVisible();
  });
});
