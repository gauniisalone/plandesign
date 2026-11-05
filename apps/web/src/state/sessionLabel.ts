/** KAN-104 : Réglages › Compte, une session ouverte en mots (« Firefox · Windows », « par
 *  code », « vue hier »). Le serveur ne garde que l'en-tête User-Agent brut ; le nom est tiré
 *  ici, sans bibliothèque, et sans prétendre deviner ce qu'on ne lit pas. */

const BROWSERS: [RegExp, string][] = [
  [/\bEdg(e|A|iOS)?\//, "Edge"],
  [/\b(OPR|Opera)\//, "Opera"],
  [/\bFirefox\/|\bFxiOS\//, "Firefox"],
  [/\bChrome\/|\bCriOS\//, "Chrome"],
  [/\bSafari\//, "Safari"],
];
const SYSTEMS: [RegExp, string][] = [
  [/\biPhone\b/, "iPhone"],
  [/\biPad\b/, "iPad"],
  [/\bAndroid\b/, "Android"],
  [/\bWindows\b/, "Windows"],
  [/\bMac OS X\b|\bMacintosh\b/, "macOS"],
  [/\bCrOS\b/, "ChromeOS"],
  [/\bLinux\b/, "Linux"],
];

function first(pairs: [RegExp, string][], text: string): string | null {
  for (const [pattern, name] of pairs) if (pattern.test(text)) return name;
  return null;
}

/** « Firefox · Windows », « Safari · iPhone », ou « Navigateur inconnu ». */
export function browserLabel(userAgent: string): string {
  const browser = first(BROWSERS, userAgent);
  const system = first(SYSTEMS, userAgent);
  if (browser && system) return `${browser} · ${system}`;
  return browser ?? system ?? "Navigateur inconnu";
}

export function methodLabel(method: string): string {
  return method === "password" ? "par mot de passe" : "par code";
}

const DAY_MS = 24 * 60 * 60 * 1000;

/** « 5 nov. » : le jour d'ouverture. */
export function dayLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("fr-FR", {
    day: "numeric",
    month: "short",
  });
}

/** « à l'instant », « il y a 12 min », « aujourd'hui », « hier », « il y a 6 jours ». */
export function seenLabel(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  const minutes = Math.floor((now.getTime() - then.getTime()) / 60_000);
  if (minutes < 2) return "à l'instant";
  if (minutes < 60) return `il y a ${minutes} min`;
  const midnight = new Date(now);
  midnight.setHours(0, 0, 0, 0);
  if (then >= midnight) return "aujourd'hui";
  const days = Math.ceil((midnight.getTime() - then.getTime()) / DAY_MS);
  return days <= 1 ? "hier" : `il y a ${days} jours`;
}
