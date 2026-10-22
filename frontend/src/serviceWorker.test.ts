import { afterEach, describe, expect, it, vi } from "vitest";

import { registerServiceWorker } from "./serviceWorker.js";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  Reflect.deleteProperty(navigator, "serviceWorker");
});

describe("registerServiceWorker", () => {
  it("registers the worker after the window load event", async () => {
    vi.stubEnv("DEV", false);
    const register = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { register },
    });
    const addEventListener = vi.spyOn(window, "addEventListener");
    Object.defineProperty(document, "readyState", {
      configurable: true,
      value: "loading",
    });

    registerServiceWorker();

    const loadListener = addEventListener.mock.calls.find(([event]) => event === "load")?.[1];
    expect(loadListener).toBeTypeOf("function");
    (loadListener as EventListener)(new Event("load"));
    await Promise.resolve();
    expect(register).toHaveBeenCalledWith("/sw.js");
    Object.defineProperty(document, "readyState", {
      configurable: true,
      value: "complete",
    });
  });

  it("does nothing when service workers are unavailable", () => {
    const addEventListener = vi.spyOn(window, "addEventListener");

    registerServiceWorker();

    expect(addEventListener).not.toHaveBeenCalled();
  });

  it("does not register an offline worker during development", () => {
    vi.stubEnv("DEV", true);
    const register = vi.fn();
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { register },
    });

    registerServiceWorker();

    expect(register).not.toHaveBeenCalled();
  });
});
