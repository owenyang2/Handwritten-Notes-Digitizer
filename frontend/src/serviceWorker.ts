/** Register the PWA worker without relying on an inline CSP exception. */
export function registerServiceWorker(): void {
  if (!("serviceWorker" in navigator)) return;

  const register = (): void => {
    void navigator.serviceWorker.register("/sw.js").catch(() => {
      // Offline support is optional; application startup must not fail when
      // registration is unavailable (private browsing, unsupported browser).
    });
  };

  if (document.readyState === "complete") {
    register();
  } else {
    window.addEventListener("load", register, { once: true });
  }
}
