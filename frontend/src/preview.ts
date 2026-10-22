async function startPreview(): Promise<void> {
  if (import.meta.env.DEV && "serviceWorker" in navigator) {
    const registration = await navigator.serviceWorker.getRegistration().catch(() => undefined);
    if (registration && await registration.unregister().catch(() => false) && navigator.serviceWorker.controller) {
      // Drop the old controller before importing any cached application modules.
      window.location.reload();
      return;
    }
  }
  await import("./entry.js");
}

void startPreview().catch(console.error);
