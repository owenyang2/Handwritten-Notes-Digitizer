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
            "image_upload",
            "recognition",
            "correction",
            "export",
            "calibration",
            "personalization",
          ].map((key) => [
            key,
            !["calibration", "personalization"].includes(key),
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
    if (path === "/personalization/consent") return json({ detail: "No consent recorded" }, 404);
    if (path === "/personalization/status") return json({
      training_state: "none", adapter_active: false, last_run_outcome: null,
      reject_reason: null, notification: null, unused_training_pairs: 0,
      training_trigger_threshold: 20, max_training_epochs: 50,
    });
    if (path === "/personalization/evaluation") return json({
      availability: "unavailable", before_cer: null, before_wer: null,
      after_cer: null, after_wer: null, delta_cer: null, delta_wer: null,
      unavailable_reason: "No evaluation recorded.",
    });
    if (path === "/personalization/assignments") return json([]);
    return json({ items: [], conflicts: [], has_more: false, next_cursor: 0 });
  });
  return { saved, exported };
}

test("import → source selection → correction → explicit export; drafts survive layout and theme changes", async ({
  page,
}, testInfo) => {
  const { saved, exported } = await fixture(page, { empty: true });
  await page.goto("/library");
  await expect(
    page.getByRole("heading", { name: "No notebooks yet" }),
  ).toBeVisible();
  if (testInfo.project.name === "chromium") {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: testInfo.outputPath("empty-library.png"), fullPage: true });
  }
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

test("phone navigation hands off to import and restores reachable focus", async ({ page }) => {
  await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/library");
  const navigation = page.getByRole("button", { name: "Open navigation", exact: true });
  await navigation.click();
  await page.getByRole("button", { name: "New import", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Import notes", exact: true });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(navigation).toBeFocused();
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
  await expect(page.locator('meta[name="theme-color"]')).toHaveAttribute("content", "#201e1a");
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

test("Theme is visible before sign-in and changes appearance without losing a review draft", async ({ page }) => {
  await fixture(page);
  let signedOut = true;
  await page.route("**/api/auth/me", (route) => signedOut
    ? route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "Not signed in" }) })
    : route.fallback());
  await page.goto("/");
  const theme = page.getByRole("combobox", { name: "Theme", exact: true });
  await page.emulateMedia({ colorScheme: "light" });
  await theme.selectOption("system");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await theme.selectOption("light");
  await page.emulateMedia({ colorScheme: "light" });
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByRole("textbox", { name: "Username" }).fill("Notebook reader");
  await expect(page.locator(".auth-form > label").filter({ hasText: "Username" })).toBeVisible();
  await page.getByRole("button", { name: "Need an account? Sign up", exact: true }).click();
  await expect(page.getByText("Use at least 8 characters.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute("aria-describedby", "auth-password-help");
  await page.getByRole("button", { name: "Already have an account? Log in", exact: true }).click();
  await theme.selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("textbox", { name: "Username" })).toHaveValue("Notebook reader");
  await page.locator(".auth-toggle").evaluate(element => Promise.all(element.getAnimations().map(animation => animation.finished)));
  const authScan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(authScan.violations).toEqual([]);
  await page.reload();
  await expect(theme).toHaveValue("dark");
  signedOut = false;
  await page.goto("/notebooks/doc-1/pages/page-1?region=region-1");
  const correction = page.getByRole("textbox", { name: "Corrected text" });
  await correction.fill("This thought is still mine.");
  await theme.selectOption("light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(correction).toHaveValue("This thought is still mine.");
  await page.setViewportSize({ width: 320, height: 900 });
  await expect(theme).toBeVisible();
  await theme.selectOption("dark");
  await expect(correction).toHaveValue("This thought is still mine.");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("Settings controls, typography and expanded details stay consistent across themes and widths", async ({ page }, testInfo) => {
  await fixture(page);
  await page.route("**/api/health", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ status: "ok", capabilities: {
      image_upload: true, recognition: true, correction: true, export: true,
      calibration: true, personalization: true,
    } }),
  }));
  for (const width of [1440, 320]) {
    await page.setViewportSize({ width, height: 900 });
    for (const theme of ["light", "dark"]) {
      await page.addInitScript(value => localStorage.setItem("notes-appearance", value), theme);
      await page.goto("/settings");
      await expect(page.getByRole("combobox", { name: "Appearance", exact: true })).toBeVisible();
      await page.getByText("Privacy and optional personalization", { exact: true }).click();
      await page.getByText("Training and model details", { exact: true }).click();
      await expect(page.getByRole("table", { name: "Before and after personalization accuracy metrics" })).toBeVisible();
      const themeControl = page.getByRole("combobox", { name: "Theme", exact: true });
      const chevron = await themeControl.evaluate(node => getComputedStyle(node).backgroundImage);
      expect(chevron).not.toBe("none");
      await themeControl.hover();
      expect(await themeControl.evaluate(node => getComputedStyle(node).backgroundImage)).toBe(chevron);
      const geometry = await page.evaluate(() => {
        const visible = (element: Element) => element.checkVisibility({ checkVisibilityCSS: true });
        const select = [...document.querySelectorAll<HTMLSelectElement>("select")].filter(visible).map(element => ({
          height: element.getBoundingClientRect().height,
          rightPadding: parseFloat(getComputedStyle(element).paddingRight),
        }));
        const headings = [...document.querySelectorAll(".settings-group h2, .settings-group h3")].filter(visible).map(element => getComputedStyle(element).fontSize);
        return { select, headings, width: innerWidth, scrollWidth: document.documentElement.scrollWidth };
      });
      expect(geometry.select.every(control => control.height >= 44 && control.rightPadding >= 36)).toBe(true);
      expect(new Set(geometry.headings)).toEqual(new Set(["16px"]));
      expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.width);
      const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
      expect(scan.violations).toEqual([]);
      if (testInfo.project.name === "chromium") {
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: testInfo.outputPath(`settings-expanded-${theme}-${width}.png`), fullPage: true });
        await page.getByText("Privacy and optional personalization", { exact: true }).click();
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: testInfo.outputPath(`settings-${theme}-${width}.png`), fullPage: true });
      }
    }
  }
});

test("older image-review warnings remain readable in both themes", async ({ page }) => {
  await fixture(page);
  await page.route("**/api/documents/doc-1", route => route.fulfill({ contentType: "application/json", body: JSON.stringify({ ...notebook, pages: [] }) }));
  await page.route("**/api/images/image-1/result", route => route.fulfill({
    contentType: "application/json", body: JSON.stringify({
      image_id: "image-1", status: "recognized", image_width: 600, image_height: 800,
      lines: [{ line_index: 0, text: "Eigenvectors", region: { x: 60, y: 80, width: 480, height: 70 },
        words: [{ word_index: 0, line_index: 0, text: "Eigenvectors", confidence: 0.62, region: { x: 60, y: 80, width: 170, height: 40 } }] }],
    }),
  }));
  for (const theme of ["light", "dark"]) {
    await page.addInitScript(value => localStorage.setItem("notes-appearance", value), theme);
    await page.goto("/notebooks/doc-1");
    await expect(page.locator(".editor-line-warning")).toBeVisible();
    const scan = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    expect(scan.violations).toEqual([]);
  }
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
    await expect
      .poll(() => page.locator(".notebook-thumbnail img").evaluateAll(
        (images) => images.length > 0 && images.every((image) =>
          (image as HTMLImageElement).complete &&
          (image as HTMLImageElement).naturalWidth > 0),
      ))
      .toBe(true);
    await page.evaluate(() => document.fonts.ready);
    for (const width of [320, 640, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      if (testInfo.project.name === "chromium" && [320, 1440].includes(width))
        await page.screenshot({
          path: process.env.DOCS_SCREENSHOTS_DIR
            ? `${process.env.DOCS_SCREENSHOTS_DIR}/library-${theme}-${width}.png`
            : testInfo.outputPath(`library-${theme}-${width}.png`),
          fullPage: true,
        });
    }
    await page.goto("/notebooks/doc-1/pages/page-1?region=region-1");
    await expect(
      page.getByRole("textbox", { name: "Corrected text" }),
    ).toBeVisible();
    for (const width of [1440, 320]) {
      await page.setViewportSize({ width, height: 900 });
      // Capture each width from a fresh load so retained pane/scroll state from
      // earlier narrow layouts doesn't hide the source in the documentation.
      await page.reload();
      await expect(page.getByRole("textbox", { name: "Corrected text" })).toBeVisible();
      // The editor can be ready before its asynchronous source canvas paints.
      await expect
        .poll(() => page.getByTestId("page-viewer").evaluate(
          (canvas) => (canvas as HTMLCanvasElement).getContext("2d")!
            .getImageData(0, 0, 1, 1).data[3],
        ))
        .toBe(255);
      if (width === 1440)
        await expect(page.getByRole("button", { name: "Select original region 1" })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
      if (testInfo.project.name === "chromium")
        await page.screenshot({
          path: process.env.DOCS_SCREENSHOTS_DIR
            ? `${process.env.DOCS_SCREENSHOTS_DIR}/review-${theme}-${width}.png`
            : testInfo.outputPath(`review-${theme}-${width}.png`),
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
  // Let font layout and browser scroll anchoring settle before recording the
  // position. Firefox/WebKit also expose fractional scroll coordinates.
  await page.evaluate(() => document.fonts.ready);
  await page.locator(".gallery-document").last().scrollIntoViewIfNeeded();
  await page.evaluate(() => new Promise<void>((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
  ));
  const position = await page.evaluate(() => Math.round(scrollY));
  expect(position).toBeGreaterThan(100);
  await page.locator(".gallery-document").last().click();
  await expect(
    page.getByRole("heading", { name: "Formula review" }),
  ).toBeVisible();
  await page.getByRole("link", { name: "Back to Library", exact: true }).click();
  await expect(search).toHaveValue("notes");
  await expect.poll(() => page.evaluate(() => Math.round(scrollY))).toBe(position);
});

test("keeps the current review visible and restores focus while changing pages", async ({ page }) => {
  await fixture(page);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  let requested!: () => void;
  const started = new Promise<void>(resolve => { requested = resolve; });
  await page.route("**/pages/page-2/regions", async route => {
    requested();
    await pending;
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });
  await page.goto("/notebooks/doc-1/pages/page-1?region=region-1");
  const correction = page.getByRole("textbox", { name: "Corrected text" });
  await correction.fill("Retain this unsaved correction.");
  const layout = page.locator(".math-notes-layout");
  await layout.evaluate(node => node.setAttribute("data-retained-review", "true"));
  await page.locator(".page-rail > summary").click();
  try {
    await page.getByRole("link", { name: "Page 2", exact: true }).focus();
    await page.getByRole("link", { name: "Page 2", exact: true }).press("Enter");
    await started;
    await expect(layout).toBeVisible();
    await expect(layout).toHaveAttribute("data-retained-review", "true");
    await expect(correction).toHaveValue("Retain this unsaved correction.");
    await expect(page.getByTestId("math-notes-section")).toHaveAttribute("aria-busy", "true");
    release();
    await expect(page.locator(".page-rail > summary")).toHaveText("Page 2 of 2");
    await expect(page.locator(".page-rail > summary")).toBeFocused();
    await expect(page).toHaveURL(/pages\/page-2$/);
    await page.locator(".page-rail > summary").click();
    await page.getByRole("link", { name: "Page 1", exact: true }).click();
    await page.getByRole("button", { name: /Review text region:/ }).click();
    await expect(correction).toHaveValue("Retain this unsaved correction.");
  } finally { release(); }
});

test("keeps recent notebooks and warm collections stable across navigation", async ({ page }) => {
  await fixture(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  const doc = (id: string, title: string, extra: Record<string, unknown> = {}) => ({
    ...notebook, document_id: id, title, image_ids: [], pages: [], is_favorite: false, ...extra,
  });
  const active = [doc("active-1", "Active notebook one"), doc("active-2", "Active notebook two")];
  const archived = doc("archived-1", "Archived notebook", { archived_at: "2026-10-20T12:00:00Z" });
  const trashed = doc("trashed-1", "Trashed notebook", { trashed_at: "2026-10-20T12:00:00Z" });
  const collectionRequests: string[] = [];
  let favoriteRequests = 0;
  let startFavorite!: () => void;
  let releaseFavorite!: () => void;
  const favoriteStarted = new Promise<void>((resolve) => (startFavorite = resolve));
  const favoriteGate = new Promise<void>((resolve) => (releaseFavorite = resolve));

  await page.route("**/api/documents*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() !== "GET" || url.pathname !== "/api/documents")
      return route.fallback();

    const view = url.searchParams.get("view") ?? "active";
    const query = url.searchParams.get("query") ?? "";
    collectionRequests.push(`${view}:${query}`);
    const items = view === "favorites" ? [] : view === "archived" ? [archived]
      : view === "trash" ? [trashed] : query ? [active[0]] : active;
    if (view === "favorites" && favoriteRequests++ === 0) {
      startFavorite();
      await favoriteGate;
    }

    await route.fulfill({ contentType: "application/json", body: JSON.stringify(items) });
  });

  await page.goto("/library");
  const history = page.getByTestId("document-history");
  const cards = history.locator(".gallery-document");
  const recent = page.locator(".notebook-sidebar .recent-notebooks .nav-link");
  const nav = (name: string) => page.locator(".notebook-sidebar").getByRole("link", { name, exact: true });
  const expectRecent = () => expect(recent).toHaveText(["Active notebook one", "Active notebook two"]);
  const emptyFavorites = history.getByRole("heading", { name: "No favorites yet", exact: true });
  const openLibrary = async () => {
    await nav("Library").click();
    await expect(history.locator("h1")).toHaveText("Library");
    await expect(cards).toHaveCount(2);
    await expect(history.locator(".collection-count")).toHaveText("2 notebooks");
    await expectRecent();
  };
  const openFavorites = async () => {
    await nav("Favorites").click();
    await expect(emptyFavorites).toBeVisible();
    await expect(history.locator(".collection-count")).toHaveText("0 notebooks");
    await expectRecent();
  };
  await expect(cards).toHaveCount(2);
  await expectRecent();

  try {
    await nav("Favorites").click();
    await favoriteStarted;
    await expect(history.locator("h1")).toHaveText("Favorites");
    await expect(cards).toHaveCount(0);
    await expectRecent();
    releaseFavorite();
    await expect(emptyFavorites).toBeVisible();
    await expect(history.getByText("Use a notebook’s menu to add it to Favorites.", { exact: true })).toBeVisible();
    await expect(history.locator(".collection-count")).toHaveText("0 notebooks");

    const warmRequests = [...collectionRequests];
    await page.evaluate(() => {
      const state = window as Window & { __warmLoading?: boolean };
      state.__warmLoading = false;
      new MutationObserver((records) => {
        const loading = (node: Node) => node.textContent?.includes("Loading notebooks") ?? false;
        if (records.some(({ target, addedNodes }) => loading(target) || [...addedNodes].some(loading))) state.__warmLoading = true;
      }).observe(document.body, { subtree: true, childList: true, characterData: true });
    });

    await openLibrary();
    await openFavorites();
    await nav("Settings").click();
    await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
    await openFavorites();
    await openLibrary();
    expect(collectionRequests).toEqual(warmRequests);
    expect(await page.evaluate(() => (window as Window & { __warmLoading?: boolean }).__warmLoading)).toBe(false);

    const view = page.getByRole("combobox", { name: "Document view" });
    for (const [scope, title] of [["archived", "Archived notebook"], ["trash", "Trashed notebook"]]) {
      await view.selectOption(scope);
      await expect(cards).toHaveCount(1);
      await expect(cards).toContainText(title);
      await expectRecent();
    }
    await view.selectOption("active");
    await expect(cards).toHaveCount(2);
    expect(collectionRequests.slice(warmRequests.length)).toEqual(["archived:", "trash:"]);
    const search = page.getByRole("searchbox", { name: "Search notes" });
    await search.fill("needle");
    await expect(cards).toHaveCount(1);
    await expectRecent();
    await search.fill("");
    await expect(cards).toHaveCount(2);
    await expectRecent();
  } finally {
    releaseFavorite();
  }
});

test("keeps account controls aligned and contained on desktop and mobile", async ({ page }) => {
  await fixture(page);
  const username = "averylongworkspaceusername-without-spaces-0123456789-abcdefghijklmnopqrstuvwxyz";
  await page.route("**/api/auth/me", (route) => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ user_id: "user-1", username }),
  }));
  await page.addInitScript(() => {
    const theme = new URL(location.href).searchParams.get("test-theme");
    if (theme) localStorage.setItem("notes-appearance", theme);
  });

  for (const theme of ["light", "dark"]) {
    for (const width of [1440, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`/library?test-theme=${theme}`);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      let sidebar = ".notebook-sidebar";
      if (width === 320) {
        await page.getByRole("button", { name: "Open navigation", exact: true }).click();
        sidebar = ".mobile-navigation";
        await expect(page.getByRole("dialog", { name: "Your workspace" })).toBeVisible();
      }

      const account = page.locator(`${sidebar} [data-testid="account-bar"]`);
      await expect(account.locator(".account-user")).toHaveAttribute("title", username);
      const geometry = await account.evaluate((bar) => {
        const rect = (element: Element) => element.getBoundingClientRect();
        const textX = (element: Element) => {
          const range = document.createRange();
          range.selectNode([...element.childNodes].find((node) => node.nodeType === Node.TEXT_NODE && node.textContent?.trim())!);
          return range.getBoundingClientRect().x;
        };
        const sidebar = bar.closest(".notebook-sidebar, .mobile-navigation")!;
        const row = bar.querySelector(".account-user-row")!;
        const avatar = bar.querySelector(".account-avatar")!;
        const user = bar.querySelector<HTMLElement>(".account-user")!;
        const logout = bar.querySelector<HTMLButtonElement>(".account-logout")!;
        const library = sidebar.querySelector<HTMLAnchorElement>('a[href="/library"]')!;
        const settings = sidebar.querySelector<HTMLAnchorElement>('a[href="/settings"]')!;
        const centerX = (element: Element) => rect(element).x + rect(element).width / 2;
        const icon = (element: Element) => centerX(element.querySelector("svg")!);
        const avatarRect = rect(avatar);
        const userRect = rect(user);
        const glyph = document.createRange();
        glyph.selectNodeContents(avatar);
        const glyphRect = glyph.getBoundingClientRect();
        return {
          rowHeight: rect(row).height, logoutHeight: rect(logout).height,
          avatarCenter: avatarRect.y + avatarRect.height / 2, userCenter: userRect.y + userRect.height / 2,
          avatarColumn: centerX(avatar),
          glyphCenterX: glyphRect.x + glyphRect.width / 2,
          glyphCenterY: glyphRect.y + glyphRect.height / 2,
          iconColumns: [icon(library), icon(settings), icon(logout)],
          textColumns: [textX(user), textX(settings), textX(logout)],
          libraryText: textX(library), clipped: user.scrollWidth > user.clientWidth,
          contained: userRect.right <= rect(bar).right && rect(sidebar).right <= innerWidth && document.documentElement.scrollWidth <= innerWidth,
        };
      });

      expect(geometry.rowHeight).toBeGreaterThanOrEqual(44);
      expect(geometry.logoutHeight).toBeGreaterThanOrEqual(44);
      expect(Math.abs(geometry.avatarCenter - geometry.userCenter)).toBeLessThanOrEqual(1);
      expect(Math.abs(geometry.avatarColumn - geometry.glyphCenterX)).toBeLessThanOrEqual(2);
      expect(Math.abs(geometry.avatarCenter - geometry.glyphCenterY)).toBeLessThanOrEqual(2);
      expect(Math.abs(geometry.avatarColumn - geometry.iconColumns[0])).toBeLessThanOrEqual(2);
      for (const column of geometry.iconColumns) expect(Math.abs(column - geometry.iconColumns[0])).toBeLessThanOrEqual(2);
      for (const column of geometry.textColumns) expect(Math.abs(column - geometry.libraryText)).toBeLessThanOrEqual(2);
      expect(geometry.clipped).toBe(true);
      expect(geometry.contained).toBe(true);
    }
  }
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
      testInfo.outputPath("accessibility-results.json"),
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
    testInfo.outputPath("local-lab-performance.json"),
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
    await page.evaluate(async () => {
      const name = (await caches.keys()).find(value => value.startsWith("notes-digitizer-"))!;
      const cache = await caches.open(name);
      await cache.put("/appearance.js", new Response("stale-ui-sentinel"));
    });
    const appearanceScript = await page.evaluate(async () => (await fetch("/appearance.js")).text());
    expect(appearanceScript).not.toContain("stale-ui-sentinel");
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
