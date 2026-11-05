/** KAN-130 : les mots de « Guidé par » qu'on désactive d'un clic. Un mot désactivé est noté
 *  sur chaque image des Moodboards reliés à la carte Rendus (`ignored_tags`, `s:<clé>`, les
 *  espaces en « _ » : le motif du schéma) ; le poste ne le retient plus aux rendus suivants. */

import type { BoardItem, BoardLink, RealisticRequest } from "../api/client";

/** Le vocabulaire de style du poste (`pipeline.style_words.VOCABULARY`), clé → mot affiché :
 *  un mot désactivé se lit dans `ignored_tags`, sans rendu qui le nomme. Miroir tenu égal par
 *  tests/unit/test_kan130_style_ignore.py. */
export const STYLE_FRENCH: Record<string, string> = {
  scandinavian: "scandinave",
  japandi: "japandi",
  industrial: "industriel",
  bohemian: "bohème",
  "mid-century modern": "années 50",
  minimalist: "minimaliste",
  rustic: "rustique",
  farmhouse: "maison de campagne",
  "art deco": "art déco",
  coastal: "bord de mer",
  mediterranean: "méditerranéen",
  "wabi-sabi": "wabi-sabi",
  contemporary: "contemporain",
  classic: "classique",
  cozy: "chaleureux",
  airy: "aéré",
  bright: "lumineux",
  moody: "intimiste",
  warm: "doux",
  calm: "calme",
  serene: "serein",
  "lived-in": "vécu",
  "white and beige": "blanc et beige",
  "warm neutrals": "tons chauds",
  "earthy tones": "tons terre",
  "sage green accents": "touches de vert sauge",
  "terracotta accents": "touches de terracotta",
  "black accents": "touches de noir",
  "soft pastels": "pastels doux",
  "navy blue accents": "touches de bleu nuit",
  monochrome: "monochrome",
  "indoor plants": "plantes",
  "framed artwork": "cadres aux murs",
  "layered rugs": "tapis superposés",
  "vintage decor": "objets chinés",
};

export interface GuidedWord {
  key: string;
  word: string;
  off: boolean;
}

/** Les mots de « Guidé par » : ceux du dernier rendu (`render`, lus par le poste), puis,
 *  barrés, les mots désactivés qu'il ne nomme pas (sinon un mot désactivé disparaîtrait au
 *  rendu suivant, sans moyen de le réactiver), dans l'ordre du vocabulaire. */
export function guidedWords(
  render: RealisticRequest | undefined,
  ignored: ReadonlySet<string>,
): GuidedWord[] {
  const keys = render?.guided_keys ?? [];
  const words = render?.guided_by ?? [];
  const read =
    render?.guided_kind === "words" && keys.length === words.length
      ? keys.map((key, k) => ({ key, word: words[k]!, off: ignored.has(key) }))
      : [];
  const named = new Set(read.map((w) => w.key));
  const rest = Object.keys(STYLE_FRENCH)
    .filter((key) => ignored.has(key) && !named.has(key))
    .map((key) => ({ key, word: STYLE_FRENCH[key]!, off: true }));
  return [...read, ...rest];
}

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

/** La liste après un clic sur `key` : désactivé, il l'est sur toutes ces images ; réactivé,
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
