// KAN-119 : « Supprimer tous mes projets » (menu « Plus » de l'accueil). Seuls les projets
// dont on est propriétaire partent, un par un, par la route existante DELETE /projects/{id} ;
// un échec n'arrête pas les autres. Sans dépendance : lu par `npm run test:unit`.

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

/** Le mot saisi : la casse et les espaces autour sont ignorés. */
export function confirmMatches(text: string): boolean {
  return text.trim().toUpperCase() === CONFIRM_WORD;
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

export function progressLine(k: number, n: number): string {
  return `Suppression du projet ${k} sur ${n}…`;
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

/** Un projet après l'autre ; `onStep(k)` avant le k-ième (à partir de 1). */
export async function deleteEach(
  projects: readonly Deletable[],
  remove: (id: string) => Promise<unknown>,
  onStep: (k: number) => void = () => undefined,
): Promise<DeleteAllResult> {
  const result: DeleteAllResult = { deleted: [], failed: [] };
  for (const [i, p] of projects.entries()) {
    onStep(i + 1);
    try {
      await remove(p.id);
      result.deleted.push(p.name);
    } catch {
      result.failed.push(p.name);
    }
  }
  return result;
}
