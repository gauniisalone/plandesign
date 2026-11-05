/** KAN-130 : les mots de « Guidé par » qu'on désactive d'un clic. Un mot désactivé est noté
 *  sur chaque image des Moodboards reliés à la carte Rendus (`ignored_tags`, `s:<clé>`, les
 *  espaces en « _ » : le motif du schéma) ; le poste ne le retient plus aux rendus suivants. */

import type { BoardItem, BoardLink } from "../api/client";

export const IGNORED_TITLE =
  "Désactivé pour les images actuelles de ce Moodboard";
export const ACTIVE_TITLE = "Désactiver ce mot pour les prochains rendus";

/** L'étiquette d'`ignored_tags` d'un mot du vocabulaire (miroir de `style_words.tag_of`). */
export function tagOf(key: string): string {
  return `s:${key.replace(/ /g, "_")}`;
}

/** Les images qui guident le style de la carte Rendus `cardId` : membres des Moodboards qui
 *  lui sont reliés (C1, la même règle que le serveur). */
export function styleMembers(
  items: BoardItem[],
  links: BoardLink[],
  cardId: string,
): BoardItem[] {
  const sources = new Set(
    links
      .filter((l) => l.kind === "guides_style" && l.target === cardId)
      .map((l) => l.source),
  );
  return items.filter(
    (i) => i.frame != null && sources.has(i.frame) && i.reference && i.ref,
  );
}

/** Les mots désactivés sur au moins une de ces images (clés du vocabulaire). */
export function ignoredKeys(members: BoardItem[]): Set<string> {
  const out = new Set<string>();
  for (const m of members)
    for (const tag of m.ignored_tags ?? [])
      if (tag.startsWith("s:")) out.add(tag.slice(2).replace(/_/g, " "));
  return out;
}

/** Le tableau après un clic sur `key` : désactivé, il l'est sur toutes ces images ; réactivé,
 *  il quitte chacune. Les autres éléments ne changent pas (même objet). */
export function toggleWord(
  items: BoardItem[],
  members: BoardItem[],
  key: string,
  off: boolean,
): BoardItem[] {
  const tag = tagOf(key);
  const ids = new Set(members.map((m) => m.id));
  return items.map((i) => {
    if (!ids.has(i.id)) return i;
    const tags = i.ignored_tags ?? [];
    if (off === tags.includes(tag)) return i;
    return {
      ...i,
      ignored_tags: off ? [...tags, tag] : tags.filter((t) => t !== tag),
    };
  });
}
