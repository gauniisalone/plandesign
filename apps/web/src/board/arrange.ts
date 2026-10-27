/** KAN-100 L1 : « Ranger » l'espace de travail, sans React (testé seul).
 *
 *  Les nodes reliés prennent la pose et les tailles de KAN-98 L2 : une rangée par plan,
 *  Photos de la pièce → Plan → Rendus, les Moodboards reliés empilés au-dessus de la colonne
 *  Rendus de la première rangée ; Photos, Moodboard et Rendus en 780 de large. Le contenu d'un
 *  node (photos, images, rendus) se range dans ses cases, comme le serveur les pose
 *  (`openplan/pipeline/board.py` : `_slot`, `cell_size`) : une photo ajoutée ensuite tombe
 *  dans la case suivante. Le reste (notes, images hors node, nodes non reliés) ne bouge pas,
 *  sauf s'il chevauche la nouvelle pose : il est alors poussé à droite d'elle.
 *
 *  Déterministe : à entrée égale, même sortie (tris par position puis par id). Idempotent :
 *  ranger un espace rangé ne change rien. */
import type { BoardItem, BoardLink } from "../api/client";

/** Les mesures de la pose en grand, les mêmes que côté serveur (board.py, KAN-98 L2). */
export const NODE_W = 780;
export const NODE_H = 468;
export const COL_GAP = 240;
export const ROW_GAP = 160;
const PLAN_H = 700;
const PAD = 24;
const PAD_TOP = 160;
const CELL_RATIO = 190 / 280;
const COLS = 2;
const CELL_GAP = 16;
/** Ce qui se range dans une case (les autres membres gardent leur taille). */
const CELL_KINDS = new Set(["photo", "render", "contextual", "file"]);

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

const byPlace = (a: BoardItem, b: BoardItem) =>
  a.y - b.y || a.x - b.x || a.id.localeCompare(b.id);
const overlap = (a: Box, b: Box, margin = 0) =>
  a.x < b.x + b.w + margin &&
  b.x < a.x + a.w + margin &&
  a.y < b.y + b.h + margin &&
  b.y < a.y + a.h + margin;
const frameOf = (i: BoardItem, role: string) =>
  i.kind === "frame" && i.role === role;

/** La case `index` d'un node de largeur `w` (comme `_slot` côté serveur). */
function slot(w: number, index: number): Box {
  const cw = Math.max(280, (w - PAD * 2 - (COLS - 1) * CELL_GAP) / COLS);
  const ch = cw * CELL_RATIO;
  const row = Math.floor(index / COLS);
  const col = index % COLS;
  return {
    x: PAD + col * (cw + CELL_GAP),
    y: PAD_TOP + row * (ch + CELL_GAP),
    w: cw,
    h: ch,
  };
}

/** Les nouvelles places et tailles (seulement ce qui change), par id. Vide : rien à ranger. */
export function arrange(
  items: BoardItem[],
  links: BoardLink[],
): Map<string, Box> {
  const byId = new Map(items.map((i) => [i.id, i]));
  const linked = (kind: BoardLink["kind"], source: string, target: string) =>
    links.some(
      (l) => l.kind === kind && l.source === source && l.target === target,
    );

  // Les rangées : un plan chacune, dans l'ordre de haut en bas ; son node Photos (qui le
  // construit) et son node Rendus (qu'il rend). Un node n'est pris qu'une fois.
  const plans = items.filter((i) => i.kind === "plan").sort(byPlace);
  const taken = new Set<string>();
  const first = (role: string, ok: (f: BoardItem) => boolean) => {
    const found = items
      .filter((f) => frameOf(f, role) && !taken.has(f.id) && ok(f))
      .sort(byPlace)[0];
    if (found) taken.add(found.id);
    return found ?? null;
  };
  const rows = plans.map((plan) => {
    taken.add(plan.id);
    return {
      plan,
      photos: first("photos", (f) => linked("feeds_plan", f.id, plan.id)),
      renders: first("renders", (f) => linked("renders_view", plan.id, f.id)),
    };
  });
  const renders = new Set(
    rows.map((r) => r.renders?.id).filter((id): id is string => !!id),
  );
  const moods = items
    .filter(
      (f) =>
        frameOf(f, "inspirations") &&
        links.some(
          (l) =>
            l.kind === "guides_style" &&
            l.source === f.id &&
            renders.has(l.target),
        ),
    )
    .sort(byPlace);
  moods.forEach((m) => taken.add(m.id));
  if (!rows.length) return new Map();

  // Les nouvelles boîtes des nodes (coordonnées relatives au coin de la pose).
  const nodes = [...taken].map((id) => byId.get(id)!);
  const ax = Math.min(...nodes.map((n) => n.x));
  const ay = Math.min(...nodes.map((n) => n.y));
  const placed = new Map<string, Box>();
  /** La hauteur d'un node métier : la sienne mise à l'échelle, au moins de quoi tenir ses
   *  cases (et celle d'un node neuf). */
  const members = (f: BoardItem) =>
    items.filter((i) => i.frame === f.id).sort(byPlace);
  const nodeBox = (f: BoardItem, x: number, y: number): Box => {
    const inside = members(f).filter((i) => CELL_KINDS.has(i.kind)).length;
    const cells = Math.max(1, Math.ceil(inside / COLS));
    const cell = slot(NODE_W, 0);
    const need = PAD_TOP + PAD + cells * cell.h + (cells - 1) * CELL_GAP;
    const base = f.role === "renders" ? PLAN_H : NODE_H;
    return { x, y, w: NODE_W, h: Math.max(base, need) };
  };
  const planW = Math.max(...rows.map((r) => r.plan.w));
  const colPlan = NODE_W + COL_GAP;
  const colRenders = colPlan + planW + COL_GAP;
  let y = 0;
  for (const m of moods) {
    const box = nodeBox(m, colRenders, y);
    placed.set(m.id, box);
    y += box.h + ROW_GAP;
  }
  for (const { plan, photos, renders: r } of rows) {
    const boxes: Box[] = [{ x: colPlan, y, w: plan.w, h: plan.h }];
    placed.set(plan.id, boxes[0]!);
    if (photos) {
      const box = nodeBox(photos, 0, y);
      placed.set(photos.id, box);
      boxes.push(box);
    }
    if (r) {
      const box = nodeBox(r, colRenders, y);
      placed.set(r.id, box);
      boxes.push(box);
    }
    y += Math.max(...boxes.map((b) => b.h)) + ROW_GAP;
  }
  for (const box of placed.values()) {
    box.x += ax;
    box.y += ay;
  }

  // Les membres d'un node rangé : dans ses cases, dans leur ordre ; les autres membres
  // (notes, liens) à leur place relative, à l'échelle du node.
  const out = new Map<string, Box>(placed);
  for (const [id, box] of placed) {
    const f = byId.get(id)!;
    if (f.kind !== "frame") continue;
    const scale = box.w / f.w;
    let index = 0;
    for (const m of members(f)) {
      if (CELL_KINDS.has(m.kind)) {
        const s = slot(box.w, index++);
        out.set(m.id, { x: box.x + s.x, y: box.y + s.y, w: s.w, h: s.h });
      } else {
        out.set(m.id, {
          x: box.x + (m.x - f.x) * scale,
          y: box.y + (m.y - f.y) * scale,
          w: m.w,
          h: m.h,
        });
      }
    }
  }

  // Le reste (hors des nodes rangés et de leurs membres) ne bouge pas, sauf s'il chevauche
  // la pose : poussé à droite d'elle, à sa hauteur, empilé s'il le faut ; un cadre emporte
  // ses membres.
  const right = Math.max(...[...placed.values()].map((b) => b.x + b.w));
  const pushed: Box[] = [];
  const rest = items.filter((i) => !out.has(i.id) && !i.frame).sort(byPlace);
  for (const i of rest) {
    if (![...placed.values()].some((b) => overlap(i, b, 8))) continue;
    const box = { x: right + COL_GAP, y: i.y, w: i.w, h: i.h };
    while (pushed.some((p) => overlap(box, p, 8)))
      box.y =
        Math.max(
          ...pushed.filter((p) => overlap(box, p, 8)).map((p) => p.y + p.h),
        ) + 48;
    pushed.push(box);
    out.set(i.id, box);
    for (const m of items.filter((c) => c.frame === i.id))
      out.set(m.id, {
        x: box.x + m.x - i.x,
        y: box.y + m.y - i.y,
        w: m.w,
        h: m.h,
      });
  }

  // Seulement ce qui change (au centième près) : un espace rangé ne donne rien.
  const same = (a: Box, b: BoardItem) =>
    Math.abs(a.x - b.x) < 0.01 &&
    Math.abs(a.y - b.y) < 0.01 &&
    Math.abs(a.w - b.w) < 0.01 &&
    Math.abs(a.h - b.h) < 0.01;
  for (const [id, box] of [...out])
    if (same(box, byId.get(id)!)) out.delete(id);
  return out;
}
