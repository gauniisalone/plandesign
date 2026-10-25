/** Connexion lente (25 oct.) : les délais réseau (envoi sans progrès, « hello », battement de
 *  cœur, essais d'une image) peuvent être raccourcis par les e2e via `window.__planDesignNet`,
 *  comme `__planDesignDepthStub` simule la profondeur. Absent en production : les valeurs par
 *  défaut s'appliquent. */
export type NetKey =
  | "stallMs"
  | "helloMs"
  | "pingMs"
  | "silenceMs"
  | "unstableAfter"
  | "imageRetryMs";

export function tuned(key: NetKey, fallback: number): number {
  const all = (window as { __planDesignNet?: Partial<Record<NetKey, unknown>> })
    .__planDesignNet;
  const value = all?.[key];
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : fallback;
}
