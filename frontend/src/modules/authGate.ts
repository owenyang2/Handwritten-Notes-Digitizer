// Auth gate module.
//
// Renders a combined login / register form that gates the whole app. On a
// successful login or registration the backend sets an httponly `session`
// cookie (see auth_api.py); subsequent same-origin API calls authenticate
// automatically via that cookie, so nothing needs to be stored client-side.
//
// The module is intentionally self-contained and framework-free so it can be
// unit-tested with jsdom.

import { login, register, ApiError } from "../api/client.js";

export interface AuthGateOptions {
  /** Element the gate renders into (its contents are replaced). */
  mount: HTMLElement;
  /** Called once the user is authenticated (login or register succeeds). */
  onAuthenticated: () => void;
}

type Mode = "login" | "register";

export class AuthGate {
  private readonly mount: HTMLElement;
  private readonly onAuthenticated: () => void;
  private mode: Mode = "login";

  constructor(options: AuthGateOptions) {
    this.mount = options.mount;
    this.onAuthenticated = options.onAuthenticated;
  }

  render(): void {
    this.mount.innerHTML = "";

    const card = document.createElement("div");
    card.className = "auth-card";
    card.setAttribute("data-testid", "auth-gate");

    const heading = document.createElement("h2");
    heading.className = "auth-heading";
    heading.setAttribute("data-testid", "auth-heading");
    card.appendChild(heading);

    const form = document.createElement("form");
    form.className = "auth-form";
    form.setAttribute("data-testid", "auth-form");

    const usernameInput = document.createElement("input");
    usernameInput.type = "text";
    usernameInput.name = "username";
    usernameInput.placeholder = "Username";
    usernameInput.autocomplete = "username";
    usernameInput.required = true;
    usernameInput.setAttribute("data-testid", "auth-username");
    form.appendChild(usernameInput);

    const emailInput = document.createElement("input");
    emailInput.type = "email";
    emailInput.name = "email";
    emailInput.placeholder = "Email";
    emailInput.autocomplete = "email";
    emailInput.setAttribute("data-testid", "auth-email");
    form.appendChild(emailInput);

    const passwordInput = document.createElement("input");
    passwordInput.type = "password";
    passwordInput.name = "password";
    passwordInput.placeholder = "Password";
    passwordInput.autocomplete = "current-password";
    passwordInput.required = true;
    passwordInput.setAttribute("data-testid", "auth-password");
    form.appendChild(passwordInput);

    const submitBtn = document.createElement("button");
    submitBtn.type = "submit";
    submitBtn.className = "auth-submit";
    submitBtn.setAttribute("data-testid", "auth-submit");
    form.appendChild(submitBtn);

    const statusEl = document.createElement("p");
    statusEl.className = "auth-status";
    statusEl.setAttribute("data-testid", "auth-status");
    statusEl.setAttribute("aria-live", "polite");
    form.appendChild(statusEl);

    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "auth-toggle";
    toggle.setAttribute("data-testid", "auth-toggle");
    card.appendChild(form);
    card.appendChild(toggle);

    const applyMode = (): void => {
      const isLogin = this.mode === "login";
      heading.textContent = isLogin ? "Log in" : "Create an account";
      submitBtn.textContent = isLogin ? "Log in" : "Sign up";
      emailInput.style.display = isLogin ? "none" : "";
      passwordInput.autocomplete = isLogin ? "current-password" : "new-password";
      toggle.textContent = isLogin
        ? "Need an account? Sign up"
        : "Already have an account? Log in";
      statusEl.textContent = "";
    };

    toggle.addEventListener("click", () => {
      this.mode = this.mode === "login" ? "register" : "login";
      applyMode();
    });

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const username = usernameInput.value.trim();
      const email = emailInput.value.trim();
      const password = passwordInput.value;

      if (!username || !password) {
        statusEl.textContent = "Username and password are required.";
        return;
      }

      submitBtn.disabled = true;
      statusEl.textContent =
        this.mode === "login" ? "Logging in…" : "Creating account…";

      const action =
        this.mode === "login"
          ? login(username, password)
          : register(username, email, password);

      action
        .then(() => {
          this.onAuthenticated();
        })
        .catch((err: unknown) => {
          let message = "Something went wrong. Please try again.";
          if (err instanceof ApiError) {
            message = err.message;
          } else if (err instanceof Error) {
            message = err.message;
          }
          statusEl.textContent = message;
          submitBtn.disabled = false;
        });
    });

    applyMode();
    this.mount.appendChild(card);
  }
}
