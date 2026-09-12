import { describe, expect, it, vi } from "vitest";
import { ConsentUI } from "./consentUI.js";
import * as apiClient from "../api/client.js";

vi.mock("../api/client.js", () => ({
  getConsent: vi.fn(),
  recordConsent: vi.fn(),
}));

describe("ConsentUI", () => {
  it("explains personal training and records grant/withdraw", async () => {
    vi.mocked(apiClient.getConsent)
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({
        event_id: "e1",
        policy_version: "personal-training-v1",
        scope: "personal_model",
        action: "grant",
        created_at: "2026-09-11T00:00:00Z",
        personal_training_allowed: true,
      });
    vi.mocked(apiClient.recordConsent).mockResolvedValue({
      event_id: "e1",
      policy_version: "personal-training-v1",
      scope: "personal_model",
      action: "grant",
      created_at: "2026-09-11T00:00:00Z",
      personal_training_allowed: true,
    });

    const mount = document.createElement("div");
    document.body.appendChild(mount);
    const ui = new ConsentUI(mount);
    await ui.render();
    expect(mount.querySelector("[data-testid='consent-status']")?.textContent).toMatch(
      /No consent recorded/,
    );
    mount.querySelector<HTMLButtonElement>("[data-testid='consent-grant']")?.click();
    await Promise.resolve();
    await Promise.resolve();
    expect(apiClient.recordConsent).toHaveBeenCalledWith("grant");
    mount.remove();
  });
});
