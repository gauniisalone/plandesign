/** KAN-91 : l'espace de travail en cartes reliées, côté écran. Miroir des règles du serveur
 *  (`openplan/pipeline/board_links.py`) pour répondre tout de suite pendant un geste (cible
 *  valide, phrase de dépôt, refus expliqué) ; le serveur reste seul juge, et ce qu'une
 *  génération lit vraiment se lit chez lui (`boardApi.inputs`, `boardApi.link`). */
import type { Board, BoardItem, BoardLink, LinkKind } from "../api/client";

export type CardRole = "photos" | "inspirations" | "renders" | "plan";

export const LINK_ROLES: Record<LinkKind, [CardRole, CardRole]> = {
  feeds_plan: ["photos", "plan"],
  guides_style: ["inspirations", "renders"],
  renders_view: ["plan", "renders"],
};

export const LINK_NAMES: Record<LinkKind, string> = {
  feeds_plan: "Photo → plan",
  guides_style: "Guide le style",
  renders_view: "Plan → rendu",
};

export const NO_PLAN = "Reliez un plan pour rendre";
export const OFF_CARD = "hors node : non utilisée";
const NOT_FOR_PLAN = new Set(["file", "render", "contextual"]);

export function cardRole(item: BoardItem | undefined | null): CardRole | null {
  if (!item) return null;
  if (item.kind === "plan") return "plan";
  if (
    item.kind === "frame" &&
    (item.role === "photos" ||
      item.role === "inspirations" ||
      item.role === "renders")
  )
    return item.role;
  return null;
}

export function cardTitle(item: BoardItem | undefined | null): string {
  if (!item) return "?";
  if (item.kind === "plan") return item.text ? `Plan · ${item.text}` : "Plan";
  return (
    item.text ||
    (item.role === "photos"
      ? "Photos de la pièce"
      : item.role === "inspirations"
        ? "Moodboard"
        : "Rendus")
  );
}

export function linkKind(
  source: BoardItem | undefined | null,
  target: BoardItem | undefined | null,
): LinkKind | null {
  const s = cardRole(source);
  const t = cardRole(target);
  for (const [kind, [a, b]] of Object.entries(LINK_ROLES))
    if (s === a && t === b) return kind as LinkKind;
  return null;
}

/** Une carte d'où l'on tire un lien (sa poignée « + »). */
export const canSource = (item: BoardItem): boolean =>
  cardRole(item) === "photos" ||
  cardRole(item) === "inspirations" ||
  cardRole(item) === "plan";

/** Pourquoi un lien de `source` vers `target` serait refusé ; null s'il passe. */
export function linkRefusal(
  board: Board,
  source: BoardItem,
  target: BoardItem,
): string | null {
  if (source.id === target.id) return "Un node ne se relie pas à lui-même.";
  const kind = linkKind(source, target);
  if (!kind) {
    const s = cardRole(source);
    if (s === "photos")
      return "Un node Photos de la pièce se relie à un plan : ses photos le construisent.";
    if (s === "inspirations")
      return "Un Moodboard se relie à un node Rendus : ses images en guident le style.";
    return "Un plan se relie à un node Rendus : il en donne la géométrie.";
  }
  const links = board.links ?? [];
  if (links.some((l) => l.source === source.id && l.target === target.id))
    return "Ces deux cartes sont déjà reliées.";
  if (kind !== "guides_style") {
    const taken = links.find((l) => l.kind === kind && l.target === target.id);
    if (taken) {
      const other = board.items.find((i) => i.id === taken.source);
      return kind === "feeds_plan"
        ? `${cardTitle(target)} lit déjà le node « ${cardTitle(other)} » : coupez ce lien d'abord.`
        : `« ${cardTitle(target)} » rend déjà ${cardTitle(other)} : coupez ce lien d'abord.`;
    }
  }
  return null;
}

/** Ce qu'un lien tiré fera, dit avant de relâcher. */
export function linkEffect(source: BoardItem, target: BoardItem): string {
  const kind = linkKind(source, target);
  if (kind === "feeds_plan")
    return `Relâchez : les photos de « ${cardTitle(source)} » construiront ${cardTitle(target)}`;
  if (kind === "guides_style")
    return `Relâchez : ${cardTitle(source)} guidera aussi ${cardTitle(target)}`;
  return `Relâchez : ${cardTitle(source)} donnera la géométrie de ${cardTitle(target)}`;
}

/** C3 : null si `item` peut entrer dans `card`, sinon la raison du refus. Seule la carte
 *  Photos trie : une inspiration (image jointe, rendu, variante) n'y entre pas. */
export function accepts(
  card: BoardItem | null,
  item: BoardItem,
): string | null {
  if (cardRole(card) === "photos" && NOT_FOR_PLAN.has(item.kind))
    return "Une inspiration ne construit pas le plan : seule une photo réelle de la pièce entre dans le node Photos de la pièce.";
  return null;
}

/** C1 : une image hors de toute carte métier n'est jamais utilisée (pastille). Un rendu dans
 *  sa carte Rendus, une variante dans le Moodboard sont des résultats, pas des images hors
 *  carte. `imageFile` : l'élément `file` est une image jointe (sinon un document). */
export function offCard(
  item: BoardItem,
  byId: Map<string, BoardItem>,
  imageFile: boolean,
): boolean {
  const image =
    item.kind === "photo" ||
    item.kind === "render" ||
    item.kind === "contextual" ||
    (item.kind === "file" && imageFile);
  if (!image) return false;
  const host = item.frame ? byId.get(item.frame) : undefined;
  const role = cardRole(host);
  if (role === "photos" || role === "inspirations") return false;
  if (host?.role === "renders" || host?.role === "moodboard") return false;
  return true;
}

/** Les cartes qu'une carte source nourrit (cibles de ses liens). */
export function targetsOf(board: Board, cardId: string | null): BoardItem[] {
  if (!cardId) return [];
  const byId = new Map(board.items.map((i) => [i.id, i]));
  return (board.links ?? [])
    .filter((l) => l.source === cardId)
    .map((l) => byId.get(l.target))
    .filter((i): i is BoardItem => Boolean(i));
}

const list = (cards: BoardItem[]) =>
  cards.map((c) => cardTitle(c)).join(", ") || "aucun node relié";

/** C3 : la phrase de dépôt d'une image, selon la carte quittée et la carte visée. */
export function dropEffect(
  board: Board,
  item: BoardItem,
  from: BoardItem | null,
  to: BoardItem | null,
): { text: string; ok: boolean } | null {
  const fromRole = cardRole(from);
  const toRole = cardRole(to);
  if ((from?.id ?? null) === (to?.id ?? null)) return null;
  if (
    fromRole !== "photos" &&
    fromRole !== "inspirations" &&
    toRole !== "photos" &&
    toRole !== "inspirations"
  )
    return null;
  const refused = accepts(to, item);
  if (refused) return { text: refused, ok: false };
  const what = item.kind === "photo" ? "cette photo" : "cette image";
  if (toRole === "photos")
    return {
      text: `Déposer : ${what} servira au ${list(targetsOf(board, to?.id ?? null))}`,
      ok: true,
    };
  if (toRole === "inspirations") {
    const now = targetsOf(board, to?.id ?? null);
    const before =
      fromRole === "inspirations" ? targetsOf(board, from?.id ?? null) : [];
    const lost = before.filter((b) => !now.some((n) => n.id === b.id));
    const verb = fromRole === "inspirations" ? "Déplacer" : "Déposer";
    const tail = lost.length ? ` et ne guidera plus ${list(lost)}` : "";
    return { text: `${verb} : ${what} guidera ${list(now)}${tail}`, ok: true };
  }
  // Hors carte (ou dans un cadre libre) : ce qu'elle cesse de nourrir.
  const lost = targetsOf(board, from?.id ?? null);
  const verb = fromRole === "photos" ? "servira" : "guidera";
  return {
    text: `Relâcher hors node : ${what} ne ${verb} plus ${fromRole === "photos" ? "au " : ""}${list(lost)}`,
    ok: true,
  };
}

/** D2 : la carte Rendus a-t-elle un plan relié ? */
export const hasPlan = (board: Board, rendersId: string): boolean =>
  (board.links ?? []).some(
    (l) => l.kind === "renders_view" && l.target === rendersId,
  );

/** Les plans (niveaux) que nourrit la carte Photos `cardId`. */
export function plansFedBy(board: Board, cardId: string | null): string[] {
  return targetsOf(board, cardId)
    .filter((t) => t.kind === "plan" && t.ref)
    .map((t) => t.ref as string);
}

/** C1, comme `generation_inputs` du serveur : les photos (ids d'image) qui construisent le
 *  plan du niveau `levelId`, membres de la carte Photos qui lui est reliée. */
export function planPhotos(board: Board | null, levelId: string): string[] {
  if (!board) return [];
  const plan = board.items.find((i) => i.kind === "plan" && i.ref === levelId);
  const source = (board.links ?? []).find(
    (l) => l.kind === "feeds_plan" && l.target === plan?.id,
  )?.source;
  if (!source) return [];
  return board.items
    .filter((i) => i.frame === source && i.kind === "photo" && i.ref)
    .map((i) => i.ref as string);
}

/** C1 : les images qui guident le style des rendus du niveau `levelId` : membres des cartes
 *  Inspirations reliées à sa carte Rendus (le serveur relit la même chose). */
export function styleImages(
  board: Board | null,
  levelId: string,
  firstLevelId: string,
): BoardItem[] {
  if (!board) return [];
  const frames = board.items.filter((i) => cardRole(i) === "renders");
  const card =
    frames.find((f) => f.ref === levelId) ??
    (levelId === firstLevelId ? frames.find((f) => f.ref === null) : undefined);
  if (!card) return [];
  const sources = new Set(
    (board.links ?? [])
      .filter((l) => l.kind === "guides_style" && l.target === card.id)
      .map((l) => l.source),
  );
  return board.items.filter(
    (i) => i.frame !== null && sources.has(i.frame) && i.reference && i.ref,
  );
}

export function newLink(
  source: BoardItem,
  target: BoardItem,
): BoardLink | null {
  const kind = linkKind(source, target);
  if (!kind) return null;
  const id =
    globalThis.crypto?.randomUUID?.() ??
    "xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx".replace(/x/g, () =>
      Math.floor(Math.random() * 16).toString(16),
    );
  return { id, source: source.id, target: target.id, kind };
}
