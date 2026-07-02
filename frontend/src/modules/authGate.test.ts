// Tests for the AuthGate module (login / register gate).
//
// The API client is mocked so no real HTTP calls are made.

import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../api/client.js", () => {
  class ApiError extends Error {
    constructor(
      public readonly status: number,
      message: string,
    ) {
      super(message);
      this.name = "ApiError";
    }
  }
  return {
    login: vi.fn(),
    register: vi.fn(),
    ApiError,
  };
});

import { AuthGate } from "./authGate.js";
import * as client from "../api/client.js";

function mount(): HTMLElement {
  const el = document.createElement("div");
  document.body.appendChild(el);
  return el;
}

describe("AuthGate", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    document.body.innerHTML = "";
  });

  it("renders a login form by default (email hidden)", () => {
    const root = mount();
    new AuthGate({ mount: root, onAuthenticated: () => {} }).render();

    expect(root.querySelector("[data-testid='auth-gate']")).not.toBeNull();
    expect(root.querySelector("[data-testid='auth-heading']")!.textContent).toMatch(/log in/i);
    const email = root.querySelector<HTMLElement>("[data-testid='auth-email']")!;
    expect(email.style.display).toBe("none");
  });

  it("toggles to register mode and shows the email field", () => {
    const root = mount();
    new AuthGate({ mount: root, onAuthenticated: () => {} }).render();

    root.querySelector<HTMLButtonElement>("[data-testid='auth-toggle']")!.click();

    expect(root.querySelector("[data-testid='auth-heading']")!.textContent).toMatch(/create an account/i);
    const email = root.querySelector<HTMLElement>("[data-testid='auth-email']")!;
    expect(email.style.display).not.toBe("none");
  });

  it("calls login and fires onAuthenticated on success", async () => {
    vi.mocked(client.login).mockResolvedValue({
      access_token: "t",
      token_type: "bearer",
      user_id: "u1",
    });
    const onAuthenticated = vi.fn();
    const root = mount();
    new AuthGate({ mount: root, onAuthenticated }).render();

    (root.querySelector("[data-testid='auth-username']") as HTMLInputElement).value = "alice";
    (root.querySelector("[data-testid='auth-password']") as HTMLInputElement).value = "pw";
    root.querySelector<HTMLFormElement>("[data-testid='auth-form']")!.requestSubmit();

    await vi.waitFor(() => expect(onAuthenticated).toHaveBeenCalledTimes(1));
    expect(client.login).toHaveBeenCalledWith("alice", "pw");
  });

  it("calls register with email in register mode", async () => {
    vi.mocked(client.register).mockResolvedValue({
      access_token: "t",
      token_type: "bearer",
      user_id: "u1",
    });
    const onAuthenticated = vi.fn();
    const root = mount();
    new AuthGate({ mount: root, onAuthenticated }).render();

    root.querySelector<HTMLButtonElement>("[data-testid='auth-toggle']")!.click();
    (root.querySelector("[data-testid='auth-username']") as HTMLInputElement).value = "bob";
    (root.querySelector("[data-testid='auth-email']") as HTMLInputElement).value = "bob@example.com";
    (root.querySelector("[data-testid='auth-password']") as HTMLInputElement).value = "pw";
    root.querySelector<HTMLFormElement>("[data-testid='auth-form']")!.requestSubmit();

    await vi.waitFor(() => expect(onAuthenticated).toHaveBeenCalledTimes(1));
    expect(client.register).toHaveBeenCalledWith("bob", "bob@example.com", "pw");
  });

  it("shows the backend error message on failure and does not authenticate", async () => {
    const err = new client.ApiError(409, "That username is already taken");
    vi.mocked(client.register).mockRejectedValue(err);
    const onAuthenticated = vi.fn();
    const root = mount();
    new AuthGate({ mount: root, onAuthenticated }).render();

    root.querySelector<HTMLButtonElement>("[data-testid='auth-toggle']")!.click();
    (root.querySelector("[data-testid='auth-username']") as HTMLInputElement).value = "bob";
    (root.querySelector("[data-testid='auth-password']") as HTMLInputElement).value = "pw";
    root.querySelector<HTMLFormElement>("[data-testid='auth-form']")!.requestSubmit();

    await vi.waitFor(() =>
      expect(root.querySelector("[data-testid='auth-status']")!.textContent).toMatch(/already taken/i),
    );
    expect(onAuthenticated).not.toHaveBeenCalled();
  });
});
