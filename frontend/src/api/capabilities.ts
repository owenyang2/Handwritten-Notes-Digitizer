// Capability registry — thin wrapper around GET /health that lets UI modules
// check whether a backend capability is currently available.
//
// The registry is populated at app startup and can be refreshed manually.
// UI modules import `capabilities` to gate features (Req 11.5, 11.6).

import { getHealth, ApiError } from "./client.js";
import type { CapabilityName, HealthResponse } from "./types.js";

/**
 * The live capabilities snapshot.
 * `null` means the health check has not completed yet.
 */
let snapshot: HealthResponse | null = null;

/**
 * Fetch the health endpoint and update the snapshot.
 * Resolves to the updated snapshot.
 * On network failure the snapshot is set to "all unavailable" so the UI
 * degrades gracefully rather than crashing.
 */
export async function refreshCapabilities(): Promise<HealthResponse> {
  try {
    snapshot = await getHealth();
  } catch (err: unknown) {
    // Backend unreachable — mark everything unavailable.
    const reason =
      err instanceof ApiError
        ? `HTTP ${err.status}: ${err.message}`
        : "Backend unreachable";
    snapshot = {
      status: "unavailable",
      model_status: "unknown",
      capabilities: {
        upload: { available: false, reason },
        recognition: { available: false, reason },
        correction: { available: false, reason },
        calibration: { available: false, reason },
        personalization: { available: false, reason },
        export: { available: false, reason },
      },
    };
  }
  return snapshot;
}

/**
 * Return `true` iff the given capability was available in the last snapshot.
 * If `refreshCapabilities()` has not been called yet, returns `true`
 * (optimistic default — the backend will enforce the real gate).\
 */
export function isAvailable(capability: CapabilityName): boolean {
  if (!snapshot) return true; // optimistic until first health check
  return snapshot.capabilities[capability]?.available ?? false;
}

/** The full snapshot, or `null` if not yet fetched. */
export function getSnapshot(): HealthResponse | null {
  return snapshot;
}

/**
 * Return the base model training status.
 * "untrained" means recognition will produce garbage output — the UI should
 * display a prominent warning in this case.
 * Returns `null` if the health check has not completed yet.
 */
export function getModelStatus(): HealthResponse["model_status"] | null {
  return snapshot?.model_status ?? null;
}
