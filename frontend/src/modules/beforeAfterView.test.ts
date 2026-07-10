// Tests for the BeforeAfterView module (task 19.2).
//
// Validates:
//   - Requirement 7.3: before/after CER, WER, and deltas are displayed
//   - Requirement 5.6: "in_progress" state is shown
//   - Requirement 5.7: rejected/failed states show appropriate messages
//   - Unavailable states show "unavailable" text
//   - formatMetric and formatDelta helpers

import { describe, it, expect } from "vitest";
import {
  BeforeAfterView,
  formatMetric,
  formatDelta,
  reportFromApi,
} from "./beforeAfterView.js";
import type { BeforeAfterReport } from "../types.js";
import type {
  PersonalizationStatusResponse,
  EvaluationApiResponse,
} from "./beforeAfterView.js";

function makeStatus(
  overrides: Partial<PersonalizationStatusResponse> = {},
): PersonalizationStatusResponse {
  return {
    training_state: "none",
    adapter_active: false,
    last_run_outcome: null,
    reject_reason: null,
    notification: null,
    unused_training_pairs: 3,
    training_trigger_threshold: 20,
    max_training_epochs: 50,
    ...overrides,
  };
}

// --------------------------------------------------------------------------- //
// Unit tests for pure helpers
// --------------------------------------------------------------------------- //

describe("formatMetric", () => {
  it("returns 'unavailable' for null", () => {
    expect(formatMetric(null)).toBe("unavailable");
  });

  it("formats zero as 0.0%", () => {
    expect(formatMetric(0)).toBe("0.0%");
  });

  it("formats 0.123 as 12.3%", () => {
    expect(formatMetric(0.123)).toBe("12.3%");
  });

  it("formats 1.0 as 100.0%", () => {
    expect(formatMetric(1.0)).toBe("100.0%");
  });

  it("formats very small value correctly", () => {
    expect(formatMetric(0.005)).toBe("0.5%");
  });
});

describe("formatDelta", () => {
  it("returns 'unavailable' for null", () => {
    expect(formatDelta(null)).toBe("unavailable");
  });

  it("marks positive delta (improvement) with ▼", () => {
    const result = formatDelta(0.05);
    expect(result).toContain("▼");
    expect(result).toContain("5.0");
  });

  it("marks negative delta (worsened) with ▲", () => {
    const result = formatDelta(-0.03);
    expect(result).toContain("▲");
    expect(result).toContain("3.0");
  });

  it("marks zero delta as no change", () => {
    expect(formatDelta(0)).toContain("no change");
  });
});

describe("reportFromApi", () => {
  it("converts snake_case API response to camelCase BeforeAfterReport", () => {
    const api: EvaluationApiResponse = {
      availability: "available",
      before_cer: 0.2,
      before_wer: 0.35,
      after_cer: 0.15,
      after_wer: 0.28,
      delta_cer: 0.05,
      delta_wer: 0.07,
      unavailable_reason: null,
    };

    const report = reportFromApi(api);
    expect(report.availability).toBe("available");
    expect(report.beforeCer).toBe(0.2);
    expect(report.beforeWer).toBe(0.35);
    expect(report.afterCer).toBe(0.15);
    expect(report.afterWer).toBe(0.28);
    expect(report.deltaCer).toBe(0.05);
    expect(report.deltaWer).toBe(0.07);
  });

  it("passes through null values correctly", () => {
    const api: EvaluationApiResponse = {
      availability: "unavailable",
      before_cer: null,
      before_wer: null,
      after_cer: null,
      after_wer: null,
      delta_cer: null,
      delta_wer: null,
      unavailable_reason: "No evaluation yet",
    };

    const report = reportFromApi(api);
    expect(report.availability).toBe("unavailable");
    expect(report.beforeCer).toBeNull();
    expect(report.deltaCer).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// BeforeAfterView.render() tests (Req 7.3, 7.6)
// --------------------------------------------------------------------------- //

describe("BeforeAfterView.render() — metrics display (Req 7.3)", () => {
  function makeMount(): HTMLElement {
    const el = document.createElement("div");
    document.body.appendChild(el);
    return el;
  }

  const fullReport: BeforeAfterReport = {
    availability: "available",
    beforeCer: 0.2,
    beforeWer: 0.35,
    afterCer: 0.15,
    afterWer: 0.28,
    deltaCer: 0.05,
    deltaWer: 0.07,
  };

  it("renders the before/after container", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(fullReport);

    expect(mount.querySelector("[data-testid='before-after-container']")).not.toBeNull();
  });

  it("renders the evaluation table (Req 7.3)", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(fullReport);

    const table = mount.querySelector("[data-testid='before-after-table']");
    expect(table).not.toBeNull();
  });

  it("displays before CER value (Req 7.3)", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(fullReport);

    const cell = mount.querySelector("[data-testid='before-cer']");
    expect(cell).not.toBeNull();
    expect(cell!.textContent).toContain("20.0%");
  });

  it("displays after CER value (Req 7.3)", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(fullReport);

    const cell = mount.querySelector("[data-testid='after-cer']");
    expect(cell).not.toBeNull();
    expect(cell!.textContent).toContain("15.0%");
  });

  it("displays delta CER value with improvement indicator (Req 7.3)", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(fullReport);

    const cell = mount.querySelector("[data-testid='delta-cer']");
    expect(cell).not.toBeNull();
    expect(cell!.textContent).toContain("▼"); // improvement
    expect(cell!.textContent).toContain("5.0");
  });

  it("displays before WER value (Req 7.3)", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(fullReport);

    const cell = mount.querySelector("[data-testid='before-wer']");
    expect(cell).not.toBeNull();
    expect(cell!.textContent).toContain("35.0%");
  });

  it("displays after WER value (Req 7.3)", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(fullReport);

    const cell = mount.querySelector("[data-testid='after-wer']");
    expect(cell).not.toBeNull();
    expect(cell!.textContent).toContain("28.0%");
  });

  it("displays delta WER value (Req 7.3)", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(fullReport);

    const cell = mount.querySelector("[data-testid='delta-wer']");
    expect(cell).not.toBeNull();
    expect(cell!.textContent).toContain("▼");
    expect(cell!.textContent).toContain("7.0");
  });

  it("renders CER and WER rows", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(fullReport);

    expect(mount.querySelector("[data-testid='metric-row-cer']")).not.toBeNull();
    expect(mount.querySelector("[data-testid='metric-row-wer']")).not.toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// Unavailable state handling (Req 7.6, 7.7)
// --------------------------------------------------------------------------- //

describe("BeforeAfterView.render() — unavailable states (Req 7.6)", () => {
  function makeMount(): HTMLElement {
    const el = document.createElement("div");
    document.body.appendChild(el);
    return el;
  }

  const unavailableReport: BeforeAfterReport = {
    availability: "unavailable",
    beforeCer: null,
    beforeWer: null,
    afterCer: null,
    afterWer: null,
    deltaCer: null,
    deltaWer: null,
  };

  it("shows unavailable banner when availability is 'unavailable'", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(unavailableReport);

    const banner = mount.querySelector("[data-testid='before-after-unavailable-banner']");
    expect(banner).not.toBeNull();
  });

  it("shows 'unavailable' text for null before CER (Req 7.6)", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(unavailableReport);

    const cell = mount.querySelector("[data-testid='before-cer']");
    expect(cell).not.toBeNull();
    expect(cell!.textContent).toBe("unavailable");
  });

  it("shows 'unavailable' text for null after WER (Req 7.6)", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(unavailableReport);

    const cell = mount.querySelector("[data-testid='after-wer']");
    expect(cell).not.toBeNull();
    expect(cell!.textContent).toBe("unavailable");
  });

  it("shows 'unavailable' for null delta CER (Req 7.6)", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(unavailableReport);

    const cell = mount.querySelector("[data-testid='delta-cer']");
    expect(cell).not.toBeNull();
    expect(cell!.textContent).toBe("unavailable");
  });

  it("displays custom unavailableReason when provided", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(unavailableReport, "No evaluation has been run yet.");

    const banner = mount.querySelector("[data-testid='before-after-unavailable-banner']");
    expect(banner!.textContent).toContain("No evaluation has been run yet.");
  });

  it("handles partially unavailable report: null deltas but available metrics", () => {
    const partialReport: BeforeAfterReport = {
      availability: "unavailable",
      beforeCer: 0.2,
      beforeWer: 0.35,
      afterCer: null,
      afterWer: null,
      deltaCer: null,
      deltaWer: null,
    };

    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.render(partialReport);

    // Before metrics are shown.
    const beforeCer = mount.querySelector("[data-testid='before-cer']");
    expect(beforeCer!.textContent).toContain("20.0%");

    // After metrics are unavailable.
    const afterCer = mount.querySelector("[data-testid='after-cer']");
    expect(afterCer!.textContent).toBe("unavailable");
  });
});

// --------------------------------------------------------------------------- //
// renderFull() — personalization status section (Req 5.6, 5.7)
// --------------------------------------------------------------------------- //

describe("BeforeAfterView.renderFull() — status section (Req 5.6, 5.7)", () => {
  function makeMount(): HTMLElement {
    const el = document.createElement("div");
    document.body.appendChild(el);
    return el;
  }

  const baseReport: BeforeAfterReport = {
    availability: "available",
    beforeCer: 0.2,
    beforeWer: 0.3,
    afterCer: 0.1,
    afterWer: 0.2,
    deltaCer: 0.1,
    deltaWer: 0.1,
  };

  it("renders the status section with training state (Req 5.6)", () => {
    const status = makeStatus({
      training_state: "in_progress",
      notification: "Personalization training is in progress.",
    });

    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(status, baseReport);

    const stateEl = mount.querySelector("[data-testid='personalization-training-state']");
    expect(stateEl).not.toBeNull();
    expect(stateEl!.textContent).toMatch(/in progress/i);
  });

  it("shows adapter active indicator when adapter is live", () => {
    const status = makeStatus({
      training_state: "completed",
      adapter_active: true,
      last_run_outcome: "accepted",
      notification: "Personalization complete.",
      unused_training_pairs: 5,
    });

    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(status, baseReport);

    const adapterEl = mount.querySelector("[data-testid='personalization-adapter-active']");
    expect(adapterEl).not.toBeNull();
    expect(adapterEl!.textContent).toMatch(/active/i);
  });

  it("shows base model message when no adapter is active", () => {
    const status = makeStatus();

    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(status, baseReport);

    const adapterEl = mount.querySelector("[data-testid='personalization-adapter-active']");
    expect(adapterEl!.textContent).toMatch(/base model/i);
  });

  it("shows rejected outcome message (Req 5.7)", () => {
    const status = makeStatus({
      training_state: "completed",
      last_run_outcome: "rejected",
      reject_reason: "rejected_cer",
      notification: "Personalization did not improve recognition accuracy.",
    });

    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(status, baseReport);

    const outcomeEl = mount.querySelector("[data-testid='personalization-last-outcome']");
    expect(outcomeEl).not.toBeNull();
    expect(outcomeEl!.textContent).toMatch(/rejected/i);
  });

  it("shows reject reason when provided (Req 5.7)", () => {
    const status = makeStatus({
      training_state: "completed",
      last_run_outcome: "rejected",
      reject_reason: "CER exceeded threshold",
      notification: "Personalization did not improve.",
    });

    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(status, baseReport);

    const reasonEl = mount.querySelector("[data-testid='personalization-reject-reason']");
    expect(reasonEl).not.toBeNull();
    expect(reasonEl!.textContent).toContain("CER exceeded threshold");
  });

  it("shows failed outcome message (Req 5.7)", () => {
    const status = makeStatus({
      training_state: "completed",
      last_run_outcome: "failed",
      reject_reason: "Training did not produce a valid adapter.",
      notification: "Adapter training did not complete successfully.",
    });

    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(status, baseReport);

    const outcomeEl = mount.querySelector("[data-testid='personalization-last-outcome']");
    expect(outcomeEl!.textContent).toMatch(/failed/i);
  });

  it("shows backend notification message when provided", () => {
    const status = makeStatus({
      training_state: "completed",
      adapter_active: true,
      last_run_outcome: "accepted",
      notification: "Your adapter has been updated.",
    });

    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(status, baseReport);

    const notifEl = mount.querySelector("[data-testid='personalization-notification']");
    expect(notifEl).not.toBeNull();
    expect(notifEl!.textContent).toContain("Your adapter has been updated.");
  });

  it("does not show last_run_outcome element when outcome is null", () => {
    const status = makeStatus();

    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(status, baseReport);

    const outcomeEl = mount.querySelector("[data-testid='personalization-last-outcome']");
    expect(outcomeEl).toBeNull();
  });

  it("re-render clears old content", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });

    const status1 = makeStatus({
      training_state: "in_progress",
      notification: "In progress",
    });

    const status2 = makeStatus({
      training_state: "completed",
      adapter_active: true,
      last_run_outcome: "accepted",
      notification: "Complete",
    });

    view.renderFull(status1, baseReport);
    view.renderFull(status2, baseReport);

    // Only one container after the second render.
    const containers = mount.querySelectorAll("[data-testid='before-after-container']");
    expect(containers.length).toBe(1);
  });
});

// --------------------------------------------------------------------------- //
// renderFull() — pipeline guide (Req 5.1 clarity)
// --------------------------------------------------------------------------- //

describe("BeforeAfterView.renderFull() — pipeline guide", () => {
  function makeMount(): HTMLElement {
    const el = document.createElement("div");
    document.body.appendChild(el);
    return el;
  }

  const baseReport: BeforeAfterReport = {
    availability: "unavailable",
    beforeCer: null,
    beforeWer: null,
    afterCer: null,
    afterWer: null,
    deltaCer: null,
    deltaWer: null,
  };

  it("renders the three-step learning guide", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(makeStatus(), baseReport);

    const guide = mount.querySelector("[data-testid='personalization-guide']");
    expect(guide).not.toBeNull();

    const steps = mount.querySelectorAll("[data-testid='personalization-guide-steps'] li");
    expect(steps.length).toBe(3);
    expect(steps[0].textContent).toMatch(/base model/i);
    expect(steps[1].textContent).toMatch(/calibration/i);
  });

  it("shows progress toward the training threshold", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(
      makeStatus({ unused_training_pairs: 7, training_trigger_threshold: 20 }),
      baseReport,
    );

    const label = mount.querySelector(".personalization-progress-label");
    expect(label!.textContent).toMatch(/7 of 20/);
    expect(label!.textContent).toMatch(/13 more/i);

    const bar = mount.querySelector<HTMLProgressElement>(
      "[data-testid='personalization-progress-bar']",
    );
    expect(bar!.value).toBe(7);
    expect(bar!.max).toBe(20);
  });

  it("falls back to safe progress values for non-finite numbers", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(
      makeStatus({
        training_trigger_threshold: Number.NaN,
        unused_training_pairs: Number.POSITIVE_INFINITY,
      }),
      baseReport,
    );

    const bar = mount.querySelector<HTMLProgressElement>(
      "[data-testid='personalization-progress-bar']",
    );
    expect(bar).not.toBeNull();
    expect(Number.isFinite(bar!.max)).toBe(true);
    expect(Number.isFinite(bar!.value)).toBe(true);
  });

  it("shows max epochs info for each training round", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(makeStatus({ max_training_epochs: 12 }), baseReport);
    expect(mount.textContent).toContain("up to 12 epochs");
  });

  it("renders training timeline section", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(makeStatus(), baseReport);
    expect(mount.querySelector("[data-testid='personalization-timeline']")).not.toBeNull();
    expect(mount.querySelectorAll("[data-testid='personalization-timeline-item']").length).toBe(4);
  });

  it("shows training-in-progress progress message", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(
      makeStatus({
        training_state: "in_progress",
        unused_training_pairs: 22,
        training_trigger_threshold: 20,
      }),
      baseReport,
    );

    const label = mount.querySelector(".personalization-progress-label");
    expect(label!.textContent).toMatch(/training your personal model/i);
  });
});

// --------------------------------------------------------------------------- //
// Training example management
// --------------------------------------------------------------------------- //

describe("BeforeAfterView — training example management", () => {
  function makeMount(): HTMLElement {
    const el = document.createElement("div");
    document.body.appendChild(el);
    return el;
  }

  const baseReport: BeforeAfterReport = {
    availability: "unavailable",
    beforeCer: null,
    beforeWer: null,
    afterCer: null,
    afterWer: null,
    deltaCer: null,
    deltaWer: null,
  };

  const pair = {
    pair_id: "pair-1",
    origin: "correction" as const,
    corrected_text: "bad lable",
    created_at: new Date("2026-07-03T12:00:00Z").toISOString(),
    used: false,
  };

  it("saves edited training-pair label text", async () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    const originalFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return new Response(
        JSON.stringify({
          ...pair,
          corrected_text: "good label",
          used: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as typeof fetch;

    try {
      view.renderFull(makeStatus(), baseReport, [pair]);
      const textarea = mount.querySelector<HTMLTextAreaElement>(
        "[data-testid='training-example-textarea']",
      )!;
      textarea.value = "good label";
      mount.querySelector<HTMLButtonElement>("[data-testid='training-example-save']")!.click();

      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(calls[0].url).toContain("/personalization/training-pairs/pair-1");
      expect(calls[0].init?.method).toBe("PATCH");
      expect(calls[0].init?.body).toBe(JSON.stringify({ corrected_text: "good label" }));
      expect(mount.querySelector(".training-example-text")!.textContent).toBe("good label");
      expect(mount.querySelector("[data-testid='training-example-action-status']")!.textContent)
        .toMatch(/label saved/i);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("deletes a stored training pair card", async () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    const originalFetch = globalThis.fetch;
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = (async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return new Response(null, { status: 204 });
    }) as typeof fetch;

    try {
      view.renderFull(makeStatus(), baseReport, [pair]);
      expect(mount.querySelectorAll("[data-testid='training-example-card']")).toHaveLength(1);

      mount.querySelector<HTMLButtonElement>("[data-testid='training-example-delete']")!.click();
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(calls[0].url).toContain("/personalization/training-pairs/pair-1");
      expect(calls[0].init?.method).toBe("DELETE");
      expect(mount.querySelectorAll("[data-testid='training-example-card']")).toHaveLength(0);
      expect(mount.textContent).toContain("2/20 samples currently stored.");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("opens and closes a zoomed training sample", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });
    view.renderFull(makeStatus(), baseReport, [pair]);

    mount.querySelector<HTMLButtonElement>("[data-testid='training-example-zoom']")!.click();

    const dialog = document.querySelector<HTMLElement>("[data-testid='training-example-zoom-dialog']");
    expect(dialog).not.toBeNull();
    expect(dialog!.querySelector<HTMLImageElement>("img")!.src).toContain(
      "/personalization/training-pairs/pair-1/image",
    );

    dialog!.querySelector<HTMLButtonElement>("[data-testid='training-example-zoom-close']")!.click();
    expect(document.querySelector("[data-testid='training-example-zoom-dialog']")).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// Loading / error states
// --------------------------------------------------------------------------- //

describe("BeforeAfterView — loading and error states", () => {
  function makeMount(): HTMLElement {
    const el = document.createElement("div");
    document.body.appendChild(el);
    return el;
  }

  it("shows loading indicator while fetching (by inspecting internal method)", () => {
    const mount = makeMount();
    const view = new BeforeAfterView({ mount });

    // Trigger loading by calling renderLoading indirectly via the refresh path.
    // We test the rendered HTML from renderLoading by setting up a mock that
    // never resolves, then checking the DOM after the refresh call starts.
    let resolveStatus!: (v: unknown) => void;
    const pendingStatus = new Promise((r) => (resolveStatus = r));

    const fetchSpy = (url: string) => {
      if (url.includes("status")) return pendingStatus as unknown as Promise<Response>;
      return pendingStatus as unknown as Promise<Response>;
    };

    // Monkey-patch global fetch for this test.
    const originalFetch = globalThis.fetch;
    globalThis.fetch = fetchSpy as typeof fetch;

    try {
      void view.refresh(); // does not await — loading state should be set synchronously before first await
      const loadingEl = mount.querySelector("[data-testid='before-after-loading']");
      expect(loadingEl).not.toBeNull();
    } finally {
      globalThis.fetch = originalFetch;
      resolveStatus(null); // allow promise to settle
    }
  });
});

// --------------------------------------------------------------------------- //
// setAuthToken
// --------------------------------------------------------------------------- //

describe("BeforeAfterView.setAuthToken", () => {
  it("can set an auth token after construction", () => {
    const mount = document.createElement("div");
    const view = new BeforeAfterView({ mount });
    // Should not throw.
    expect(() => view.setAuthToken("test-token-abc")).not.toThrow();
  });
});
