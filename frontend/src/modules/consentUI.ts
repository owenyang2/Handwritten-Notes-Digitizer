import { getConsent, recordConsent } from "../api/client.js";

export class ConsentUI {
  constructor(private readonly mount: HTMLElement) {}

  async render(): Promise<void> {
    this.mount.replaceChildren();
    const heading = document.createElement("h3");
    heading.textContent = "Personal training consent";
    this.mount.appendChild(heading);

    const copy = document.createElement("p");
    copy.className = "consent-copy";
    copy.textContent =
      "Approved corrections improve only your personal model, and only after you grant this consent. Original ink is never rewritten. You can withdraw at any time; the shared base model stays in use until a validated personal candidate exists.";
    this.mount.appendChild(copy);

    const status = document.createElement("p");
    status.setAttribute("data-testid", "consent-status");
    status.setAttribute("aria-live", "polite");
    this.mount.appendChild(status);

    const actions = document.createElement("div");
    actions.className = "consent-actions";
    const grant = document.createElement("button");
    grant.type = "button";
    grant.textContent = "Use my corrections for my model";
    grant.setAttribute("data-testid", "consent-grant");
    const withdraw = document.createElement("button");
    withdraw.type = "button";
    withdraw.textContent = "Withdraw personal training";
    withdraw.setAttribute("data-testid", "consent-withdraw");
    actions.append(grant, withdraw);
    this.mount.appendChild(actions);

    const refresh = async (): Promise<void> => {
      try {
        const current = await getConsent();
        if (!current) {
          status.textContent = "No consent recorded yet. The base model will keep being used.";
          return;
        }
        status.textContent = current.personal_training_allowed
          ? `Personal training allowed (policy ${current.policy_version}).`
          : `Personal training is off (last action: ${current.action}).`;
      } catch (error) {
        status.textContent = error instanceof Error ? error.message : "Could not load consent.";
      }
    };

    grant.addEventListener("click", () => {
      void recordConsent("grant").then(refresh).catch((error: unknown) => {
        status.textContent = error instanceof Error ? error.message : "Could not save consent.";
      });
    });
    withdraw.addEventListener("click", () => {
      void recordConsent("withdraw").then(refresh).catch((error: unknown) => {
        status.textContent = error instanceof Error ? error.message : "Could not save consent.";
      });
    });
    await refresh();
  }
}
