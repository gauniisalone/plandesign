/** Thème de l'interface (retouches b2) : Système (défaut, suit le réglage de l'ordinateur),
 *  Clair ou Sombre, retenu par ce navigateur. Le choix est posé sur `<html data-theme>` ;
 *  sans attribut, la feuille de style suit `prefers-color-scheme`. */

export type ThemeChoice = "system" | "light" | "dark";

const KEY = "openplan:theme";
const EVENT = "openplan-theme";

export function loadTheme(): ThemeChoice {
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw === "light" || raw === "dark" ? raw : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
  window.dispatchEvent(new Event(EVENT));
}

export function saveTheme(choice: ThemeChoice): void {
  try {
    if (choice === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, choice);
  } catch {
    // Stockage indisponible (navigation privée) : le choix vaut pour la session.
  }
  applyTheme(choice);
}

/** T7a : couleur d'accent, à comparer en un clic dans Réglages (augus n'a pas tranché) :
 *  Indigo (défaut, sans attribut), Violet (maquette du 16 oct.) ou Provence (palette
 *  provençale, terre cuite). Posée sur `<html data-accent>`, retenue par ce navigateur. */
export type AccentChoice = "indigo" | "violet" | "provence";

const ACCENT_KEY = "openplan:accent";

export function loadAccent(): AccentChoice {
  try {
    const raw = window.localStorage.getItem(ACCENT_KEY);
    return raw === "violet" || raw === "provence" ? raw : "indigo";
  } catch {
    return "indigo";
  }
}

export function applyAccent(choice: AccentChoice): void {
  const root = document.documentElement;
  if (choice === "indigo") delete root.dataset.accent;
  else root.dataset.accent = choice;
  window.dispatchEvent(new Event(EVENT));
}

export function saveAccent(choice: AccentChoice): void {
  try {
    if (choice === "indigo") window.localStorage.removeItem(ACCENT_KEY);
    else window.localStorage.setItem(ACCENT_KEY, choice);
  } catch {
    // Stockage indisponible (navigation privée) : le choix vaut pour la session.
  }
  applyAccent(choice);
}

/** Le thème réellement affiché est-il sombre ? */
export function isDark(): boolean {
  if (typeof document === "undefined") return false;
  const forced = document.documentElement.dataset.theme;
  if (forced === "dark") return true;
  if (forced === "light") return false;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

/** Prévient à chaque changement : choix dans Réglages ou réglage du système. */
export function onThemeChange(callback: () => void): () => void {
  const media = window.matchMedia?.("(prefers-color-scheme: dark)");
  media?.addEventListener("change", callback);
  window.addEventListener(EVENT, callback);
  return () => {
    media?.removeEventListener("change", callback);
    window.removeEventListener(EVENT, callback);
  };
}
