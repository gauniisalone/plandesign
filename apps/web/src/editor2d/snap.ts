/** Accroches du tracé (E1, demande d'augus du 4 oct. 2026), comme SketchUp : extrémités et
 *  milieux des murs, angle par crans (0/45/90° et pas de 15°) depuis le point de départ,
 *  parallèles et perpendiculaires aux murs existants, prolongements, alignements sur un
 *  sommet. Maj : tracé libre. Fonctions pures, sans état : le plan les appelle à chaque
 *  mouvement et dessine les guides rendus. */
import type { Point2 } from "../api/types.gen";

export type SnapKind =
  | "endpoint"
  | "midpoint"
  | "perpendicular"
  | "parallel"
  | "extension"
  | "angle"
  | "alignment"
  | "free";

export const SNAP_LABELS: Record<SnapKind, string> = {
  endpoint: "Extrémité",
  midpoint: "Milieu",
  perpendicular: "Perpendiculaire",
  parallel: "Parallèle",
  extension: "Prolongement",
  angle: "Angle",
  alignment: "Alignement",
  free: "Libre",
};

/** KAN-29 : avec Maj, accroche douce aux crans d'angle, à 2° près seulement. */
export const SOFT_SNAP_DEG = 2;

export interface Segment {
  id: string;
  a: Point2;
  b: Point2;
}

export interface SnapVertex {
  id: string;
  p: Point2;
}

export interface Guide {
  from: Point2;
  to: Point2;
}

export interface Snap {
  point: Point2;
  /** Sommet existant visé (extrémité) : le mur s'y raccorde. */
  vertexId?: string;
  kind?: SnapKind;
  /** Étiquette affichée près du curseur (« Perpendiculaire », « 45° »…). */
  label?: string;
  guides: Guide[];
}

export interface SnapOptions {
  /** Point de départ du segment en cours (mur, cote, ou voisin d'un sommet déplacé). */
  start: Point2 | null;
  segments: Segment[];
  vertices: SnapVertex[];
  /** Tolérance d'accroche, en mètres (≈ 10 pixels à l'écran). */
  tolerance: number;
  /** Maj : rien n'accroche. */
  free?: boolean;
  /** Pas de l'angle, en degrés. */
  stepDeg?: number;
}

const GRID_M = 0.01;

function sub(a: Point2, b: Point2): Point2 {
  return { x: a.x - b.x, y: a.y - b.y };
}

function len(v: Point2): number {
  return Math.hypot(v.x, v.y);
}

function round(p: Point2): Point2 {
  return {
    x: Math.round(p.x / GRID_M) * GRID_M,
    y: Math.round(p.y / GRID_M) * GRID_M,
  };
}

/** Projection de `p` sur la droite (origine `o`, direction unitaire `u`) : point et écart. */
function onLine(
  p: Point2,
  o: Point2,
  u: Point2,
): { point: Point2; t: number; dist: number } {
  const t = (p.x - o.x) * u.x + (p.y - o.y) * u.y;
  const point = { x: o.x + u.x * t, y: o.y + u.y * t };
  return { point, t, dist: len(sub(p, point)) };
}

/** Intersection de deux droites (origine, direction unitaire) ; null si parallèles. */
function intersect(
  o1: Point2,
  u1: Point2,
  o2: Point2,
  u2: Point2,
): Point2 | null {
  const det = u1.x * u2.y - u1.y * u2.x;
  if (Math.abs(det) < 1e-9) return null;
  const t = ((o2.x - o1.x) * u2.y - (o2.y - o1.y) * u2.x) / det;
  return { x: o1.x + u1.x * t, y: o1.y + u1.y * t };
}

/** Direction réelle du tracé, en degrés de 0 à 345 (« 90° » vers le haut du plan). */
function direction(start: Point2, end: Point2): string {
  const deg = (Math.atan2(end.y - start.y, end.x - start.x) * 180) / Math.PI;
  return `${(Math.round(deg) + 360) % 360}°`;
}

interface Ray {
  u: Point2;
  kind: SnapKind;
  label: string;
  ref?: Segment;
}

/** Directions proposées depuis le départ : murs existants (parallèle, perpendiculaire) en
 *  premier, puis les crans d'angle. */
function rays(start: Point2, opts: SnapOptions): Ray[] {
  const out: Ray[] = [];
  for (const s of opts.segments) {
    const d = sub(s.b, s.a);
    const l = len(d);
    if (l < 1e-6) continue;
    const u = { x: d.x / l, y: d.y / l };
    // Un mur sur un cran d'angle (à 2° près) est déjà couvert par les crans : ses parallèle et
    // perpendiculaire feraient concurrence au 90° exact (un mur tracé à 0,9° près donnait une
    // « perpendiculaire » à 90,9°).
    const deg = (Math.atan2(u.y, u.x) * 180) / Math.PI;
    const step = opts.stepDeg ?? 15;
    const onStep = Math.abs(deg / step - Math.round(deg / step)) * step < 2;
    if (!onStep) {
      out.push({ u, kind: "parallel", label: SNAP_LABELS.parallel, ref: s });
      out.push({
        u: { x: -u.y, y: u.x },
        kind: "perpendicular",
        label: SNAP_LABELS.perpendicular,
        ref: s,
      });
    }
  }
  const stepDeg = opts.stepDeg ?? 15;
  for (let k = 0; k * stepDeg < 180; k += 1) {
    const a = (k * stepDeg * Math.PI) / 180;
    const deg = k * stepDeg;
    out.push({
      u: { x: Math.cos(a), y: Math.sin(a) },
      kind: "angle",
      label: `${deg}°`,
    });
  }
  void start;
  return out;
}

/** Tracé libre (Maj, KAN-29) : le mur suit le curseur, hors de l'accroche orthogonale. Il se
 *  raccorde encore à un sommet existant (pour refermer une pièce), s'aimante doucement aux
 *  crans d'angle (à `SOFT_SNAP_DEG` près) et affiche toujours son angle. */
function freeSnap(raw: Point2, opts: SnapOptions): Snap {
  for (const v of opts.vertices) {
    if (len(sub(raw, v.p)) <= opts.tolerance)
      return {
        point: v.p,
        vertexId: v.id,
        kind: "endpoint",
        label: opts.start
          ? `${SNAP_LABELS.endpoint} · ${direction(opts.start, v.p)}`
          : SNAP_LABELS.endpoint,
        guides: [],
      };
  }
  const start = opts.start;
  if (!start || len(sub(raw, start)) < 1e-6)
    return { point: round(raw), guides: [] };
  const d = sub(raw, start);
  const deg = (Math.atan2(d.y, d.x) * 180) / Math.PI;
  const step = opts.stepDeg ?? 15;
  const near = Math.round(deg / step) * step;
  if (Math.abs(deg - near) <= SOFT_SNAP_DEG) {
    const a = (near * Math.PI) / 180;
    const along = onLine(raw, start, { x: Math.cos(a), y: Math.sin(a) });
    return {
      point: along.point,
      kind: "angle",
      label: direction(start, along.point),
      guides: [{ from: start, to: along.point }],
    };
  }
  const point = round(raw);
  return {
    point,
    kind: "free",
    label: direction(start, point),
    guides: [],
  };
}

/** Le point accroché sous le curseur, avec ses guides et son étiquette. */
export function smartSnap(raw: Point2, opts: SnapOptions): Snap {
  if (opts.free) return freeSnap(raw, opts);
  const tol = opts.tolerance;
  // 1. Extrémité : un sommet existant.
  let best: { v: SnapVertex; d: number } | null = null;
  for (const v of opts.vertices) {
    const d = len(sub(raw, v.p));
    if (d <= tol && (!best || d < best.d)) best = { v, d };
  }
  if (best)
    return {
      point: best.v.p,
      vertexId: best.v.id,
      kind: "endpoint",
      label: SNAP_LABELS.endpoint,
      guides: [],
    };
  // 2. Milieu d'un mur.
  for (const s of opts.segments) {
    const m = { x: (s.a.x + s.b.x) / 2, y: (s.a.y + s.b.y) / 2 };
    if (len(sub(raw, m)) <= tol)
      return {
        point: m,
        kind: "midpoint",
        label: SNAP_LABELS.midpoint,
        guides: [{ from: s.a, to: s.b }],
      };
  }
  // 3. Pied de la perpendiculaire depuis le départ sur un mur.
  if (opts.start) {
    for (const s of opts.segments) {
      const d = sub(s.b, s.a);
      const l = len(d);
      if (l < 1e-6) continue;
      const u = { x: d.x / l, y: d.y / l };
      const foot = onLine(opts.start, s.a, u);
      if (foot.t < -1e-6 || foot.t > l + 1e-6 || foot.dist < tol) continue;
      if (len(sub(raw, foot.point)) <= tol)
        return {
          point: foot.point,
          kind: "perpendicular",
          label: SNAP_LABELS.perpendicular,
          guides: [{ from: s.a, to: s.b }],
        };
    }
  }
  // 4. Directions depuis le départ, croisées avec les prolongements et alignements.
  const lines: { o: Point2; u: Point2; kind: SnapKind; guide: Guide }[] = [];
  for (const s of opts.segments) {
    const d = sub(s.b, s.a);
    const l = len(d);
    if (l < 1e-6) continue;
    const u = { x: d.x / l, y: d.y / l };
    const proj = onLine(raw, s.a, u);
    if (proj.t < -1e-6 || proj.t > l + 1e-6) {
      const near = proj.t < 0 ? s.a : s.b;
      lines.push({
        o: s.a,
        u,
        kind: "extension",
        guide: { from: near, to: proj.point },
      });
    }
  }
  for (const v of opts.vertices) {
    lines.push({
      o: v.p,
      u: { x: 1, y: 0 },
      kind: "alignment",
      guide: { from: v.p, to: { x: raw.x, y: v.p.y } },
    });
    lines.push({
      o: v.p,
      u: { x: 0, y: 1 },
      kind: "alignment",
      guide: { from: v.p, to: { x: v.p.x, y: raw.y } },
    });
  }
  const near = lines.filter((ln) => onLine(raw, ln.o, ln.u).dist <= tol);
  if (opts.start && len(sub(raw, opts.start)) > tol) {
    const start = opts.start;
    const candidates = rays(start, opts)
      .map((r) => {
        const along = onLine(raw, start, r.u);
        return { r, along };
      })
      .filter(({ along }) => along.dist <= tol)
      .sort((p, q) => {
        // Murs existants d'abord, puis l'écart au rayon.
        const w = (r: Ray) => (r.kind === "angle" ? 1 : 0);
        return w(p.r) - w(q.r) || p.along.dist - q.along.dist;
      });
    const ray = candidates[0];
    if (ray) {
      const guides: Guide[] = [{ from: start, to: ray.along.point }];
      if (ray.r.ref) guides.push({ from: ray.r.ref.a, to: ray.r.ref.b });
      // Le rayon croise un prolongement ou un alignement proche : on prend l'intersection.
      for (const ln of near) {
        const x = intersect(start, ray.r.u, ln.o, ln.u);
        if (x && len(sub(x, raw)) <= tol * 1.5) {
          return {
            point: x,
            kind: ray.r.kind,
            label: `${ray.r.label} · ${SNAP_LABELS[ln.kind]}`,
            guides: [{ from: start, to: x }, ln.guide, ...guides.slice(1)],
          };
        }
      }
      return {
        point: ray.along.point,
        kind: ray.r.kind,
        label:
          ray.r.kind === "angle"
            ? direction(start, ray.along.point)
            : ray.r.label,
        guides,
      };
    }
  }
  // 5. Prolongement ou alignement seul.
  const line = near.sort(
    (p, q) => onLine(raw, p.o, p.u).dist - onLine(raw, q.o, q.u).dist,
  )[0];
  if (line) {
    const point = onLine(raw, line.o, line.u).point;
    return {
      point,
      kind: line.kind,
      label: SNAP_LABELS[line.kind],
      guides: [{ ...line.guide, to: point }],
    };
  }
  return { point: round(raw), guides: [] };
}

const RIGHT = Math.PI / 2;
const RIGHT_PRIORITY = (7 * Math.PI) / 180;

/** Lacet sous la souris (E4) : crans de 90° en priorité (à 7° près), sinon crans de `step`
 *  (15° par défaut ; 90° pour un escalier) ; `free` (Maj) : l'angle exact. */
export function snapYaw(
  raw: number,
  free: boolean,
  step: number = Math.PI / 12,
): number {
  if (free) return raw;
  const right = Math.round(raw / RIGHT) * RIGHT;
  if (step >= RIGHT - 1e-9 || Math.abs(raw - right) < RIGHT_PRIORITY)
    return right;
  return Math.round(raw / step) * step;
}

/** Angle affiché (degrés entiers, 0 à 359). */
export function degrees(yaw: number): number {
  return ((Math.round((yaw * 180) / Math.PI) % 360) + 360) % 360;
}
