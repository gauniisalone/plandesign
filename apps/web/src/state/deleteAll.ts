// KAN-119 : « Supprimer tous mes projets » (menu « Plus » de l'accueil). Un seul appel,
// POST /projects/delete-all, avec le mot saisi : le serveur le vérifie, ne supprime que les
// projets dont on est propriétaire, et un échec n'arrête pas les autres. Sans dépendance :
// lu par `npm run test:unit`.

export const CONFIRM_WORD = "SUPPRIMER";
/** Au-delà, la liste des noms se replie (« et 7 autres… »). */
export const NAMES_SHOWN = 10;

export interface Deletable {
  id: string;
  name: string;
  role?: string;
}

/** Les projets à soi (en mode local, le rôle est absent : tout est à soi). */
export function ownedProjects<T extends Deletable>(list: readonly T[]): T[] {
  return list.filter((p) => (p.role ?? "owner") === "owner");
}

/** Le mot saisi tel qu'il part au serveur : la casse et les espaces autour sont ignorés. */
export function confirmWord(text: string): string {
  return text.trim().toUpperCase();
}

export function confirmMatches(text: string): boolean {
  return confirmWord(text) === CONFIRM_WORD;
}

const plural = (n: number, one: string, many: string) => (n > 1 ? many : one);

export function deleteAllTitle(n: number): string {
  return n > 1 ? `Supprimer vos ${n} projets ?` : "Supprimer votre projet ?";
}

export function deleteAllButton(n: number): string {
  return n > 1 ? `Supprimer mes ${n} projets` : "Supprimer mon projet";
}

/** « « A » », « « A » et « B » », « « A », « B » et « C » ». */
export function quotedNames(names: readonly string[]): string {
  const quoted = names.map((n) => `« ${n} »`);
  if (quoted.length < 2) return quoted.join("");
  return `${quoted.slice(0, -1).join(", ")} et ${quoted[quoted.length - 1]}`;
}

/** Les noms montrés d'emblée, et ceux qui se replient au-delà de NAMES_SHOWN. */
export function splitNames(names: readonly string[]): {
  shown: string[];
  folded: string[];
} {
  if (names.length <= NAMES_SHOWN) return { shown: [...names], folded: [] };
  return {
    shown: names.slice(0, NAMES_SHOWN),
    folded: names.slice(NAMES_SHOWN),
  };
}

export function foldedLabel(n: number): string {
  return `et ${n} ${plural(n, "autre", "autres")}…`;
}

export function progressLine(n: number): string {
  return n > 1 ? `Suppression de vos ${n} projets…` : "Suppression de votre projet…";
}

export interface DeleteAllResult {
  deleted: string[];
  /** Noms des projets restés, dans l'ordre. */
  failed: string[];
}

/** Le message de fin : tout est parti, ou le cas partiel qui nomme ce qui reste. */
export function resultLine({ deleted, failed }: DeleteAllResult): string {
  const d = deleted.length;
  if (failed.length === 0)
    return `${d} ${plural(d, "projet supprimé", "projets supprimés")}. La place est libérée.`;
  const f = failed.length;
  const stays = plural(f, "reste", "restent");
  return `${d} ${plural(d, "supprimé", "supprimés")}, ${f} en échec : ${quotedNames(failed)} ${stays}, réessayez.`;
}

/** Réponse de POST /projects/delete-all : des ids. */
export interface DeleteAllReport {
  deleted: string[];
  failed: string[];
}

/** Le bilan nommé ; sans réponse (réseau, refus), rien n'est compté supprimé. */
export function namedResult(
  projects: readonly Deletable[],
  report: DeleteAllReport | null,
): DeleteAllResult {
  const gone = new Set(report?.deleted ?? []);
  return {
    deleted: projects.filter((p) => gone.has(p.id)).map((p) => p.name),
    failed: projects.filter((p) => !gone.has(p.id)).map((p) => p.name),
  };
}
