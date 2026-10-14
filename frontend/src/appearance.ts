export type Appearance = "system" | "light" | "dark";

export function readAppearance(): Appearance {
  try {
    const value = localStorage.getItem("notes-appearance");
    if (value === "light" || value === "dark") return value;
  } catch {
    /* Storage can be unavailable in private browsing. */
  }
  return "system";
}

export function applyAppearance(preference: Appearance): void {
  const dark =
    preference === "dark" ||
    (preference === "system" &&
      window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

export function saveAppearance(preference: Appearance): void {
  try {
    localStorage.setItem("notes-appearance", preference);
  } catch {
    /* Session-only appearance still works. */
  }
  applyAppearance(preference);
}
