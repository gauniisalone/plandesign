import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  Camera,
  DerivedLevel,
  DerivedRoom,
  Furniture,
  Level,
  Point2,
  Project,
} from "../api/types.gen";
import type { Op } from "../api/ops";
import type { Peer, PresenceStore } from "../state/live";
import { PlanPeers } from "./PlanPeers";
import {
  catalogApi,
  type Catalog,
  type FurnitureMoveCheck,
  type FurnitureSymbol,
  type SymbolSet,
} from "../api/client";
import {
  allCameras,
  formatLength,
  furnitureCorners,
  furnitureItems,
  isCamera,
  isFurniture,
  isExcludedView,
  isOpening,
  isStairs,
  isFreeCamera,
  isUncertain,
  openings,
  rooms,
  roofWindowCorners,
  roofWindows,
  walls,
  type Selection,
} from "../model";
import {
  distance,
  fitView,
  NO_INSETS,
  type Insets,
  projectOnSegment,
  snapToGrid,
  toScreen,
  toWorld,
  zoomAt,
  type View,
} from "./view";
import {
  defaultSwing,
  isPair,
  isStackKind,
  OPENING_LABELS,
  openingSymbol,
  roomSide,
  type OpeningCard,
  type OpeningSymbol as OpeningSym,
} from "./openingSymbol";
import {
  degrees,
  smartSnap,
  snapYaw,
  type Segment,
  type Snap,
  type SnapVertex,
} from "./snap";
import {
  boxOf,
  layoutLabels,
  textWidth,
  type LabelBox,
  type LabelRequest,
} from "./labels";

export type Tool = "select" | "pan" | "wall" | "door" | "window" | "scale";

/** Fond de photo : image du projet posée dans le plan (coin bas-gauche en (tx, ty), mètres). */
export interface Background {
  url: string;
  widthPx: number;
  heightPx: number;
  transform: {
    scale: number;
    tx: number;
    ty: number;
    rotation: number;
    opacity: number;
    stretch_x?: number;
    stretch_y?: number;
  };
}

interface Props {
  level: Level;
  derived: DerivedLevel | null;
  units: Project["units_display"];
  tool: Tool;
  selection: Selection;
  background: Background | null;
  /** Largeur en pixels des photos (par identifiant) : champ de vision de la caméra sélectionnée. */
  imageWidths?: Record<string, number>;
  /** Meubles d'une variante proposée, dessinés en fantôme (pas dans la scène). */
  ghost?: Furniture[] | null;
  /** Seconde variante (comparaison côte à côte), en vert. */
  ghostAlt?: Furniture[] | null;
  /** Éléments que l'assistant vient de toucher : surlignés quelques secondes. */
  highlight?: string[];
  /** Bibliothèque : meuble choisi, à poser d'un clic (accroché au mur, règles en direct). */
  placing?: Placing | null;
  /** Règles pour le meuble choisi, à la position essayée sous le curseur. */
  onCheckPlacement?: (
    assetId: string,
    position: Point2,
    yaw: number,
  ) => Promise<FurnitureMoveCheck | null>;
  /** Le meuble est posé (ou Échap) : on sort du mode pose. */
  onPlacingEnd?: () => void;
  /** Glisser d'un meuble : les règles à la position essayée (rougit + message court). */
  onCheckFurniture?: (
    id: string,
    position: Point2,
    yaw: number,
    size?: { width: number; depth: number },
  ) => Promise<FurnitureMoveCheck | null>;
  /** Escalier tourné ou porte retournée (E4) : les règles dans l'état essayé. */
  onCheckElement?: (
    id: string,
    trial: {
      yaw?: number;
      width?: number;
      position?: Point2;
      swing?: SwingT;
    },
  ) => Promise<FurnitureMoveCheck | null>;
  onSelect: (s: Selection) => void;
  onOp: (op: Op) => void;
  fitToken: number;
  /** Zoom demandé par les boutons (facteur autour du centre) ; `id` change à chaque demande. */
  zoomCommand?: { factor: number; id: number } | null;
  /** Échelle courante (pixels par mètre), pour l'afficher dans la barre. */
  onScaleChange?: (scale: number) => void;
  /** Outil « Cote » : deux clics sur le fond de plan → longueur mesurée à corriger. */
  onMeasure?: (a: Point2, b: Point2) => void;
  /** Marges occupées par les barres flottantes : le recadrage évite de passer dessous. */
  insets?: Insets;
  /** B2d : mon curseur sur ce plan, en mètres (null : hors du plan). */
  onCursor?: (point: Point2 | null) => void;
  /** B2d : les curseurs des autres membres sur ce plan, en mètres. */
  peers?: { store: PresenceStore; peers: Peer[] };
}

/** Meuble de la bibliothèque en attente de pose : ses cotes par défaut suffisent au fantôme. */
export interface Placing {
  assetId: string;
  category: string;
  label: string;
  width: number;
  depth: number;
  /** Cage d'escalier (S1) au lieu d'un meuble : `width` = encombrement le long de la montée,
   *  posée le long du mur accroché. */
  stairs?: "straight" | "quarter_turn" | "half_turn" | "spiral";
  /** Ouverture de la bibliothèque (E5) au lieu d'un meuble : posée d'un clic sur un mur. */
  opening?: OpeningCard;
}

/** Symboles chargés une fois pour toute la page ; null tant qu'ils ne sont pas là (le plan
 *  dessine alors les boîtes seules, comme avant). */
/** Catalogue chargé une fois : plages de largeur et profondeur des poignées (E2). */
let catalogOnce: Promise<Catalog | null> | null = null;
function loadCatalog(): Promise<Catalog | null> {
  catalogOnce ??= catalogApi.list().catch(() => null);
  return catalogOnce;
}

/** Largeur d'emmarchement d'un escalier → encombrement en travers (dérivée, E2). */
const STAIR_ACROSS: Record<string, number> = {
  straight: 1,
  quarter_turn: 1,
  half_turn: 2,
  spiral: 2,
};
const OPENING_MIN_M = 0.4;

let symbolsOnce: Promise<SymbolSet | null> | null = null;
function loadSymbols(): Promise<SymbolSet | null> {
  symbolsOnce ??= catalogApi.symbols().catch(() => null);
  return symbolsOnce;
}

/** Contour rond (table ronde, lampadaire) : ellipse inscrite dans la boîte, en unité. */
const ROUND: [number, number][] = Array.from({ length: 32 }, (_, i) => [
  0.5 * Math.cos((2 * Math.PI * i) / 32),
  0.5 * Math.sin((2 * Math.PI * i) / 32),
]);

const FOV_LENGTH_M = 1.5;
const FOV_DEFAULT_HALF_ANGLE = Math.PI / 5;

/** Nom affiché d'une caméra : le sien, sinon « Point de vue » ou « Photo ». */
function cameraLabel(cam: Camera): string {
  return cam.name ?? (isFreeCamera(cam) ? "Point de vue" : "Photo");
}

const GRID_M = 0.05;
const SNAP_WALL_M = 0.35;
const WALL_GAP_M = 0.02;

/** Accroche le dos d'un meuble à la face du mur le plus proche (à moins de 35 cm), tourné
 *  face à la pièce ; null loin des murs (ou avec Alt). */
function snapToWall(
  raw: Point2,
  depth: number,
  level: Level,
  positions: Record<string, Point2>,
): { position: Point2; yaw: number } | null {
  let best: { d: number; position: Point2; yaw: number } | null = null;
  for (const w of walls(level)) {
    const a = positions[w.start_vertex];
    const b = positions[w.end_vertex];
    if (!a || !b) continue;
    const ux = b.x - a.x;
    const uy = b.y - a.y;
    const len = Math.hypot(ux, uy);
    if (len < 1e-6) continue;
    const t = Math.max(
      0,
      Math.min(1, ((raw.x - a.x) * ux + (raw.y - a.y) * uy) / (len * len)),
    );
    const foot = { x: a.x + ux * t, y: a.y + uy * t };
    const dx = raw.x - foot.x;
    const dy = raw.y - foot.y;
    const d = Math.hypot(dx, dy);
    if (d < 1e-6 || d > w.thickness / 2 + depth / 2 + SNAP_WALL_M) continue;
    if (best && d >= best.d) continue;
    const n = { x: dx / d, y: dy / d };
    const back = w.thickness / 2 + depth / 2 + WALL_GAP_M;
    best = {
      d,
      position: { x: foot.x + n.x * back, y: foot.y + n.y * back },
      yaw: Math.atan2(n.y, n.x) - Math.PI / 2,
    };
  }
  return best ? { position: best.position, yaw: best.yaw } : null;
}
/** Gonds et sens d'ouverture d'une porte (E4). */
type SwingT = {
  hinge: "left" | "right";
  direction: "inward" | "outward";
  angle?: number;
};
/** Porte (E4) : un clic fait le tour gauche-intérieur → droite-intérieur → droite-extérieur
 *  → gauche-extérieur ; G change les gonds, O le sens. */
function nextSwing(s: SwingT, how: "cycle" | "hinge" | "direction"): SwingT {
  const hinge = s.hinge === "left" ? "right" : "left";
  const direction = s.direction === "inward" ? "outward" : "inward";
  if (how === "hinge") return { ...s, hinge };
  if (how === "direction") return { ...s, direction };
  const flipHinge = (s.direction === "inward") === (s.hinge === "left");
  return flipHinge ? { ...s, hinge } : { ...s, direction };
}
function swingText(
  s: SwingT,
  kind: string = "door",
  pair = false,
  side = 1,
): string {
  const where = s.hinge === "left" ? "gauche" : "droite";
  // « Intérieur » = vers la pièce (E5c) : `side` dit de quel côté du mur elle est.
  const face =
    (s.direction === "inward") === side >= 0 ? "intérieur" : "extérieur";
  if (isStackKind(kind))
    return `s'efface à ${where}${kind === "sliding_door" ? `, rail côté ${face}` : ""}`;
  return `${pair ? "vantail principal" : "gonds"} à ${where}, ouverture vers l'${face}`;
}
/** Écart (pixels) entre la face avant et le rond de rotation. */
const ROTATE_GAP_PX = 26;
const VERTEX_SNAP_PX = 10;
const WALL_PICK_PX = 8;
const DOOR = { width: 0.83, height: 2.04 };
const WINDOW = { width: 1.2, height: 1.25, sill: 0.9 };

type Drag =
  | { kind: "pan"; startX: number; startY: number; view: View }
  | { kind: "vertex"; id: string; current: Point2 }
  | { kind: "camera"; id: string; current: Point2 }
  | { kind: "aim"; id: string; yaw: number }
  | {
      kind: "furniture";
      id: string;
      current: Point2;
      yaw: number;
      offset: Point2;
    }
  | { kind: "spin"; id: string; yaw: number }
  | { kind: "stairsSpin"; id: string; yaw: number }
  | { kind: "stairsMove"; id: string; current: Point2; offset: Point2 }
  | {
      /** Poignée d'un meuble (E2) : hu, hv ∈ {−1, 0, 1} le long de sa largeur, profondeur. */
      kind: "resize";
      id: string;
      hu: number;
      hv: number;
      start: { center: Point2; width: number; depth: number };
      center: Point2;
      width: number;
      depth: number;
    }
  | { kind: "stairsWidth"; id: string; side: number; width: number }
  | {
      kind: "openingEdge";
      id: string;
      end: number;
      offset: number;
      width: number;
    };

export function PlanCanvas({
  level,
  derived,
  units,
  tool,
  selection,
  background,
  imageWidths,
  ghost,
  ghostAlt,
  highlight,
  onCheckFurniture,
  onCheckElement,
  placing = null,
  onCheckPlacement,
  onPlacingEnd,
  onSelect,
  onOp,
  fitToken,
  zoomCommand,
  onScaleChange,
  onMeasure,
  insets = NO_INSETS,
  onCursor,
  peers,
}: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [size, setSize] = useState({ width: 800, height: 600 });
  const [view, setView] = useState<View>(() =>
    fitView(derived?.bounds ?? null, 800, 600, insets),
  );
  const [drag, setDrag] = useState<Drag | null>(null);
  const [pendingStart, setPendingStart] = useState<string | Point2 | null>(
    null,
  );
  const [cursor, setCursor] = useState<Point2 | null>(null);
  /** Accroche en cours (E1) : point, guides pointillés et étiquette près du curseur. */
  const [snap, setSnap] = useState<Snap | null>(null);
  /** Pointeur grossier (doigt) : l'aide-mémoire parle de toucher, pas de clic ni de molette. */
  const [coarse] = useState(
    () => window.matchMedia?.("(pointer: coarse)")?.matches ?? false,
  );
  const act = coarse ? "Toucher" : "Clic";
  /** Verdict des règles pendant le glisser d'un meuble. */
  const [live, setLive] = useState<{
    id: string;
    valid: boolean;
    message: string;
    /** Règle souple touchée (E4) : dite sans refuser. */
    warn?: string;
  } | null>(null);
  /** Porte retournée en cours de vérification (E4) ; `invalid` : refusée, montrée en rouge. */
  const [doorTrial, setDoorTrial] = useState<{
    id: string;
    swing: SwingT;
    invalid: boolean;
  } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [symbols, setSymbols] = useState<SymbolSet | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  useEffect(() => {
    let alive = true;
    void loadCatalog().then((c) => {
      if (alive) setCatalog(c);
    });
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    let alive = true;
    void loadSymbols().then((s) => {
      if (alive) setSymbols(s);
    });
    return () => {
      alive = false;
    };
  }, []);
  /** Mode pose : où irait le meuble choisi sous le curseur, et le verdict des règles. */
  const [placeAt, setPlaceAt] = useState<{
    position: Point2;
    yaw: number;
  } | null>(null);
  /** Bibliothèque (E5) : où irait l'ouverture choisie (mur sous le curseur, décalage). */
  const [placeOpening, setPlaceOpening] = useState<{
    wall: string;
    offset: number;
  } | null>(null);
  const [placeLive, setPlaceLive] = useState<{
    valid: boolean;
    message: string;
  } | null>(null);
  /** Doigts posés (pointer events) : deux doigts = pincer pour zoomer et déplacer la vue. */
  const pointers = useRef<Map<number, { x: number; y: number }>>(new Map());
  const pinch = useRef<{ dist: number; mid: { x: number; y: number } } | null>(
    null,
  );
  const checkTimer = useRef<number | null>(null);
  const noticeTimer = useRef<number | null>(null);
  const scheduleCheck = (
    id: string,
    position: Point2,
    yaw: number,
    size?: { width: number; depth: number },
  ) => {
    if (!onCheckFurniture) return;
    if (checkTimer.current !== null) window.clearTimeout(checkTimer.current);
    checkTimer.current = window.setTimeout(() => {
      void onCheckFurniture(id, position, yaw, size).then((r) => {
        if (r) setLive({ id, valid: r.valid, message: r.message });
      });
    }, 120);
  };
  const scheduleElementCheck = (
    id: string,
    trial: { yaw?: number; position?: Point2 },
  ) => {
    if (!onCheckElement) return;
    if (checkTimer.current !== null) window.clearTimeout(checkTimer.current);
    checkTimer.current = window.setTimeout(() => {
      void onCheckElement(id, trial).then((r) => {
        if (r)
          setLive({
            id,
            valid: r.valid,
            message: r.message,
            warn: r.violations.find((v) => v.severity !== "high")?.message,
          });
      });
    }, 120);
  };
  const schedulePlaceCheck = (position: Point2, yaw: number) => {
    if (!onCheckPlacement || !placing || placing.stairs) return;
    const assetId = placing.assetId;
    if (checkTimer.current !== null) window.clearTimeout(checkTimer.current);
    checkTimer.current = window.setTimeout(() => {
      void onCheckPlacement(assetId, position, yaw).then((r) => {
        if (r) setPlaceLive({ valid: r.valid, message: r.message });
      });
    }, 120);
  };
  /** Position du meuble choisi sous le curseur : accroché au mur proche (Alt : libre). */
  const placeFor = (w: Point2, free: boolean) => {
    if (!placing) return null;
    const raw = snapToGrid(w, GRID_M);
    const snapped = free
      ? null
      : snapToWall(raw, placing.depth, level, positions);
    return snapped ?? { position: raw, yaw: placeAt?.yaw ?? 0 };
  };
  const showNotice = (text: string) => {
    setNotice(text);
    if (noticeTimer.current !== null) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 3000);
  };

  // Taille du conteneur
  useEffect(() => {
    const el = svgRef.current?.parentElement;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      setSize({ width: el.clientWidth, height: el.clientHeight });
    });
    observer.observe(el);
    setSize({ width: el.clientWidth, height: el.clientHeight });
    return () => observer.disconnect();
  }, []);

  // Recadrage à la demande (bouton) ou à l'ouverture. Pendant un tracé de murs (chaîne en
  // cours), un changement de taille du conteneur ne recadre pas : la vue ne saute pas sous le
  // doigt ou la souris (sinon le geste qui referme la pièce manque le premier sommet et en
  // pose un cinquième). Une demande explicite (fitToken) recadre toujours.
  const drawingRef = useRef(false);
  useEffect(() => {
    drawingRef.current = pendingStart !== null;
  }, [pendingStart]);
  const fittedToken = useRef<number | null>(null);
  useEffect(() => {
    if (drawingRef.current && fittedToken.current === fitToken) return;
    fittedToken.current = fitToken;
    setView(fitView(derived?.bounds ?? null, size.width, size.height, insets));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitToken, size.width, size.height]);

  // Zoom par les boutons de la barre : autour du centre de la fenêtre.
  useEffect(() => {
    if (!zoomCommand) return;
    setView((v) =>
      zoomAt(v, zoomCommand.factor, size.width / 2, size.height / 2),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoomCommand?.id]);

  useEffect(() => {
    onScaleChange?.(view.scale);
  }, [view.scale, onScaleChange]);

  // Un autre meuble choisi (ou plus aucun) : on repart sans fantôme ni verdict.
  useEffect(() => {
    setPlaceAt(null);
    setPlaceLive(null);
    setPlaceOpening(null);
  }, [placing?.assetId]);

  useEffect(() => {
    if (!placing) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onPlacingEnd?.();
    };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [placing, onPlacingEnd]);

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setPendingStart(null);
        setSnap(null);
      }
    };
    window.addEventListener("keydown", down);
    return () => window.removeEventListener("keydown", down);
  }, []);

  useEffect(() => {
    if (tool !== "wall") setPendingStart(null);
    setSnap(null);
  }, [tool]);

  const localPoint = useCallback((e: React.MouseEvent | MouseEvent) => {
    const rect = svgRef.current?.getBoundingClientRect();
    return {
      x: e.clientX - (rect?.left ?? 0),
      y: e.clientY - (rect?.top ?? 0),
    };
  }, []);

  /** Sommet existant sous le curseur (en pixels), sinon null. */
  const vertexAt = useCallback(
    (sx: number, sy: number): string | null => {
      let best: { id: string; d: number } | null = null;
      for (const [id, v] of Object.entries(level.vertices)) {
        const s = toScreen(view, v.position);
        const d = Math.hypot(s.x - sx, s.y - sy);
        if (d <= VERTEX_SNAP_PX && (!best || d < best.d)) best = { id, d };
      }
      return best?.id ?? null;
    },
    [level.vertices, view],
  );

  /** Mur le plus proche du curseur, avec l'abscisse le long de l'axe. */
  const wallAt = useCallback(
    (p: Point2): { id: string; along: number; length: number } | null => {
      let best: {
        id: string;
        along: number;
        length: number;
        dist: number;
      } | null = null;
      for (const w of walls(level)) {
        const a = level.vertices[w.start_vertex]?.position;
        const b = level.vertices[w.end_vertex]?.position;
        if (!a || !b) continue;
        const proj = projectOnSegment(p, a, b);
        const tolerance = w.thickness / 2 + WALL_PICK_PX / view.scale;
        if (proj.dist <= tolerance && (!best || proj.dist < best.dist)) {
          best = {
            id: w.id,
            along: proj.along,
            length: proj.length,
            dist: proj.dist,
          };
        }
      }
      return best;
    },
    [level, view.scale],
  );

  /** Accroches du tracé (E1) : extrémités, milieux, angle par crans depuis `start`,
   *  parallèles, perpendiculaires, prolongements, alignements ; `free` (Maj) : rien.
   *  `exclude` : sommet déplacé (ses murs et lui-même ne servent pas d'accroche). */
  const snapAt = (
    sx: number,
    sy: number,
    start: Point2 | null,
    free: boolean,
    exclude?: string,
  ): Snap => {
    const segments: Segment[] = [];
    for (const w of walls(level)) {
      if (exclude && (w.start_vertex === exclude || w.end_vertex === exclude))
        continue;
      const a = level.vertices[w.start_vertex]?.position;
      const b = level.vertices[w.end_vertex]?.position;
      if (a && b) segments.push({ id: w.id, a, b });
    }
    const vertices: SnapVertex[] = Object.entries(level.vertices)
      .filter(([id]) => id !== exclude)
      .map(([id, v]) => ({ id, p: v.position }));
    return smartSnap(toWorld(view, sx, sy), {
      start,
      segments,
      vertices,
      tolerance: VERTEX_SNAP_PX / view.scale,
      free,
    });
  };
  /** Voisin d'un sommet (autre bout d'un de ses murs) : départ des angles quand on le
   *  déplace. */
  const neighbourOf = (vid: string): Point2 | null => {
    for (const w of walls(level)) {
      const other =
        w.start_vertex === vid
          ? w.end_vertex
          : w.end_vertex === vid
            ? w.start_vertex
            : null;
      if (other) return level.vertices[other]?.position ?? null;
    }
    return null;
  };
  const snapPoint = (
    sx: number,
    sy: number,
    free: boolean,
  ): string | Point2 => {
    const s = snapAt(sx, sy, pendingStartPoint, free);
    return s.vertexId ?? s.point;
  };

  /** Porte retournée (E4) : vérifiée avant d'être appliquée (un meuble dans le nouveau
   *  débattement la refuse), puis annulable comme toute modification. */
  const flipDoor = async (
    o: {
      id: string;
      kind?: string;
      leaves?: number;
      host_wall?: string;
      offset?: number;
      width?: number;
      swing?: SwingT | null;
    },
    how: "cycle" | "hinge" | "direction",
  ) => {
    if (!o.swing) return;
    const swing = nextSwing(o.swing, how);
    setDoorTrial({ id: o.id, swing, invalid: false });
    const check = await onCheckElement?.(o.id, { swing });
    if (check && !check.valid) {
      setDoorTrial({ id: o.id, swing, invalid: true });
      const why = check.violations.find((v) => v.severity === "high");
      showNotice(`Non retournée : ${why?.message ?? check.message}`);
      window.setTimeout(
        () => setDoorTrial((t) => (t?.id === o.id ? null : t)),
        1500,
      );
      return;
    }
    setDoorTrial(null);
    onOp({ op: "update_element", element_id: o.id, fields: { swing } });
    const kind = (o.kind ?? "door") as keyof typeof OPENING_LABELS;
    showNotice(
      `${OPENING_LABELS[kind] ?? "Porte"} : ${swingText(swing, kind, isPair({ kind, offset: 0, width: 1, leaves: o.leaves }), o.host_wall ? sideOf(o.host_wall, (o.offset ?? 0) + (o.width ?? 0) / 2) : 1)}`,
    );
  };
  // Porte sélectionnée : G retourne les gonds, O le sens d'ouverture (E4).
  useEffect(() => {
    if (selection.kind !== "element") return;
    const el = level.elements[selection.id];
    if (!isOpening(el) || !el.swing) return;
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "SELECT" ||
          t.tagName === "TEXTAREA" ||
          t.isContentEditable)
      )
        return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k !== "g" && k !== "o") return;
      e.preventDefault();
      void flipDoor(el, k === "g" ? "hinge" : "direction");
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });

  /** Côté de la pièce le long d'un mur (E5c) : +1 normale gauche, −1 l'autre, 0 aucun. */
  const roomPolys = Object.values(derived?.rooms ?? {}).map((r) => r.polygon);
  const sideOf = (wallId: string, along: number): number => {
    const w = level.elements[wallId];
    if (!w || w.type !== "wall") return 0;
    const a = positions[w.start_vertex];
    const b = positions[w.end_vertex];
    if (!a || !b) return 0;
    return roomSide(a, b, w.thickness, along, roomPolys);
  };
  /** Une porte posée s'ouvre vers la pièce, quel que soit le sens de tracé du mur. */
  const towardRoom = (wallId: string, along: number) =>
    sideOf(wallId, along) < 0 ? ("outward" as const) : ("inward" as const);
  /** Escalier glissé (E5c) : son flanc s'accroche à un mur parallèle à sa montée, à moins
   *  de 35 cm ; ailleurs, au pas de la grille ; Alt : libre. */
  const snapStairs = (id: string, raw: Point2, free: boolean): Point2 => {
    const st = level.elements[id];
    if (free || !isStairs(st)) return free ? raw : snapToGrid(raw, GRID_M);
    const yaw = st.yaw ?? 0;
    const climb = { x: Math.cos(yaw), y: Math.sin(yaw) };
    const halfAcross = stairsHalf(id);
    let best: { d: number; p: Point2 } | null = null;
    for (const w of walls(level)) {
      const a = positions[w.start_vertex];
      const b = positions[w.end_vertex];
      if (!a || !b) continue;
      const len = distance(a, b);
      if (len < 1e-6) continue;
      const u = { x: (b.x - a.x) / len, y: (b.y - a.y) / len };
      if (Math.abs(u.x * climb.x + u.y * climb.y) < 0.9) continue;
      const n = { x: -u.y, y: u.x };
      const d = (raw.x - a.x) * n.x + (raw.y - a.y) * n.y;
      const reach = w.thickness / 2 + halfAcross;
      if (Math.abs(d) > reach + SNAP_WALL_M || (best && Math.abs(d) >= best.d))
        continue;
      const k = Math.sign(d || 1) * (reach + WALL_GAP_M);
      best = {
        d: Math.abs(d),
        p: { x: raw.x + n.x * (k - d), y: raw.y + n.y * (k - d) },
      };
    }
    return best?.p ?? snapToGrid(raw, GRID_M);
  };

  /** Ouverture choisie dans la bibliothèque (E5) : centrée sous le curseur sur le mur
   *  le plus proche, tenue dans le mur, au pas de la grille ; null hors d'un mur. */
  const openingAt = (w: Point2) => {
    const card = placing?.opening;
    if (!card) return null;
    const hit = wallAt(w);
    if (!hit || hit.length < card.width) return null;
    const offset = Math.max(
      0,
      Math.min(hit.length - card.width, hit.along - card.width / 2),
    );
    return { wall: hit.id, offset: Math.round(offset / GRID_M) * GRID_M };
  };

  const onWheel = (e: React.WheelEvent) => {
    const { x, y } = localPoint(e);
    setView((v) => zoomAt(v, e.deltaY < 0 ? 1.15 : 1 / 1.15, x, y));
  };

  const onMouseDown = (e: React.MouseEvent) => {
    const { x, y } = localPoint(e);
    if (e.button === 1 || tool === "pan") {
      setDrag({ kind: "pan", startX: x, startY: y, view });
      e.preventDefault();
      return;
    }
    if (e.button !== 0) return;
    if (placing?.opening) {
      // Bibliothèque (E5) : un clic sur un mur pose l'ouverture choisie, à ses cotes types.
      const spot = openingAt(toWorld(view, x, y));
      if (!spot) {
        showNotice("Cliquez sur un mur pour poser l'ouverture");
        return;
      }
      const card = placing.opening;
      onOp({
        op: "add_opening",
        host_wall: spot.wall,
        kind: card.kind,
        offset: spot.offset,
        width: card.width,
        height: card.height,
        sill_height: card.sill,
        swing: defaultSwing(
          card.kind,
          towardRoom(spot.wall, spot.offset + card.width / 2),
        ),
        leaves: card.leaves ?? 1,
        split: card.split ?? 0.5,
      });
      onPlacingEnd?.();
      return;
    }
    if (placing) {
      // Bibliothèque : un clic pose le meuble choisi là où montre le fantôme, si aucune règle
      // dure n'est touchée ; sinon le plan dit pourquoi et on reste en mode pose.
      const at = placeFor(toWorld(view, x, y), e.altKey);
      if (!at) return;
      if (placing.stairs) {
        onOp({
          op: "add_stairs",
          kind: placing.stairs,
          position: {
            x: Math.round(at.position.x * 100) / 100,
            y: Math.round(at.position.y * 100) / 100,
          },
          yaw: Math.round(at.yaw * 1000) / 1000,
        });
        onPlacingEnd?.();
        return;
      }
      const assetId = placing.assetId;
      void (async () => {
        const check = await onCheckPlacement?.(assetId, at.position, at.yaw);
        if (check && !check.valid) {
          setPlaceAt(at);
          setPlaceLive({ valid: false, message: check.message });
          showNotice(`Non posé : ${check.message}`);
          return;
        }
        onOp({
          op: "add_furniture",
          asset_id: assetId,
          position: {
            x: Math.round(at.position.x * 100) / 100,
            y: Math.round(at.position.y * 100) / 100,
          },
          yaw: Math.round(at.yaw * 1000) / 1000,
        });
        onPlacingEnd?.();
      })();
      return;
    }
    if (tool === "select") {
      // Poignées de redimensionnement (E2) : avant les sommets, elles sont dessus.
      const handle = (e.target as SVGElement).dataset["handle"];
      if (handle && startResize(handle)) return;
      // Rotation (E4) : rond au-delà de la face avant d'un meuble ou d'un escalier ; porte :
      // rond dans le débattement, un clic la retourne.
      const data = (e.target as SVGElement).dataset;
      const spin = data["spin"];
      const spun = spin ? level.elements[spin] : undefined;
      if (spin && isFurniture(spun)) {
        onSelect({ kind: "element", id: spin });
        setDrag({ kind: "spin", id: spin, yaw: spun.transform.yaw });
        return;
      }
      const spinStairs = data["spinStairs"];
      const st = spinStairs ? level.elements[spinStairs] : undefined;
      if (spinStairs && isStairs(st)) {
        onSelect({ kind: "element", id: spinStairs });
        setDrag({ kind: "stairsSpin", id: spinStairs, yaw: st.yaw ?? 0 });
        return;
      }
      const flip = data["flip"];
      const flipped = flip ? level.elements[flip] : undefined;
      if (flip && isOpening(flipped)) {
        void flipDoor(flipped, "cycle");
        return;
      }
      const vid = vertexAt(x, y);
      if (vid) {
        onSelect({ kind: "vertex", id: vid });
        const v = level.vertices[vid];
        if (v && !v.locked)
          setDrag({ kind: "vertex", id: vid, current: v.position });
        return;
      }
      // Point de vue libre : glisser le rond le déplace, glisser la pointe le fait viser.
      const aim = (e.target as SVGElement).dataset["aim"];
      const aimed = aim ? level.elements[aim] : undefined;
      if (aim && isCamera(aimed)) {
        onSelect({ kind: "element", id: aim });
        setDrag({ kind: "aim", id: aim, yaw: aimed.yaw });
        return;
      }
      const target = (e.target as SVGElement).dataset["element"];
      const element = target ? level.elements[target] : undefined;
      if (target && isCamera(element) && isFreeCamera(element)) {
        onSelect({ kind: "element", id: target });
        setDrag({
          kind: "camera",
          id: target,
          current: { x: element.position.x, y: element.position.y },
        });
        return;
      }
      // Meuble : glisser le déplace (accroché au mur le plus proche, Alt : libre). Les règles
      // sont vérifiées en direct et au dépôt.
      // Escalier (E5c) : glisser le déplace, accroché à un mur parallèle, règles en direct.
      if (target && isStairs(element)) {
        onSelect({ kind: "element", id: target });
        const w = toWorld(view, x, y);
        setDrag({
          kind: "stairsMove",
          id: target,
          current: { x: element.position.x, y: element.position.y },
          offset: {
            x: w.x - element.position.x,
            y: w.y - element.position.y,
          },
        });
        return;
      }
      if (target && isFurniture(element)) {
        onSelect({ kind: "element", id: target });
        const w = toWorld(view, x, y);
        setDrag({
          kind: "furniture",
          id: target,
          current: {
            x: element.transform.position.x,
            y: element.transform.position.y,
          },
          yaw: element.transform.yaw,
          offset: {
            x: w.x - element.transform.position.x,
            y: w.y - element.transform.position.y,
          },
        });
        return;
      }
      onSelect(target ? { kind: "element", id: target } : { kind: "none" });
      return;
    }
    if (tool === "wall") {
      const p = snapPoint(x, y, e.shiftKey);
      if (pendingStart === null) {
        setPendingStart(p);
        return;
      }
      if (samePoint(level, pendingStart, p)) return;
      onOp({ op: "add_wall", start: pendingStart, end: p });
      setPendingStart(p);
      return;
    }
    if (tool === "scale") {
      // Recalibrer : deux clics sur une longueur connue, accrochés aux murs (E1).
      const p = snapAt(x, y, pendingStartPoint, e.shiftKey).point;
      if (pendingStart === null || typeof pendingStart === "string") {
        setPendingStart(p);
        return;
      }
      onMeasure?.(pendingStart, p);
      setPendingStart(null);
      return;
    }
    if (tool === "door" || tool === "window") {
      const hit = wallAt(toWorld(view, x, y));
      if (!hit) return;
      const spec = tool === "door" ? DOOR : WINDOW;
      const offset = Math.max(
        0,
        Math.min(hit.length - spec.width, hit.along - spec.width / 2),
      );
      if (hit.length < spec.width) return;
      onOp({
        op: "add_opening",
        host_wall: hit.id,
        kind: tool,
        offset: Math.round(offset / GRID_M) * GRID_M,
        width: spec.width,
        height: spec.height,
        sill_height: tool === "window" ? WINDOW.sill : 0,
        swing:
          tool === "door"
            ? {
                hinge: "left",
                direction: towardRoom(hit.id, offset + spec.width / 2),
                angle: 1.5708,
              }
            : null,
      });
    }
  };

  /** Plage catalogue d'un meuble (largeur, profondeur), sinon une plage large. */
  const rangesOf = (f: Furniture) => {
    const a = catalog?.assets.find((c) => c.id === f.asset_id);
    return {
      width: a ? [a.width.min, a.width.max] : [0.2, 6],
      depth: a ? [a.depth.min, a.depth.max] : [0.2, 6],
    } as { width: [number, number]; depth: [number, number] };
  };
  const startResize = (handle: string): boolean => {
    const [what, id, p1, p2] = handle.split("|");
    if (!id) return false;
    const el = level.elements[id];
    if (what === "f" && isFurniture(el)) {
      const s = el.transform.scale;
      const start = {
        center: { x: el.transform.position.x, y: el.transform.position.y },
        width: el.bbox.width * s,
        depth: el.bbox.depth * s,
      };
      setDrag({
        kind: "resize",
        id,
        hu: Number(p1),
        hv: Number(p2),
        start,
        ...start,
      });
      return true;
    }
    if (what === "s" && isStairs(el)) {
      setDrag({
        kind: "stairsWidth",
        id,
        side: Number(p1),
        width: el.width ?? 0.9,
      });
      return true;
    }
    if (what === "o" && isOpening(el)) {
      setDrag({
        kind: "openingEdge",
        id,
        end: Number(p1),
        offset: el.offset,
        width: el.width,
      });
      return true;
    }
    return false;
  };
  const clamp = (v: number, lo: number, hi: number) =>
    Math.max(lo, Math.min(hi, v));
  const cm = (v: number) => Math.round(v * 100) / 100;
  /** Axe d'un mur : origine, direction unitaire, longueur. */
  const wallAxis = (wallId: string) => {
    const w = level.elements[wallId];
    if (!w || w.type !== "wall") return null;
    const a = positions[w.start_vertex];
    const b = positions[w.end_vertex];
    if (!a || !b) return null;
    const len = distance(a, b);
    if (len < 1e-9) return null;
    return { a, u: { x: (b.x - a.x) / len, y: (b.y - a.y) / len }, len };
  };
  /** Pendant un glisser de poignée : la nouvelle taille (E2). */
  const moveResize = (w: Point2, alt: boolean): boolean => {
    if (drag?.kind === "resize") {
      const f = level.elements[drag.id];
      if (!isFurniture(f)) return true;
      const yaw = f.transform.yaw;
      const u = { x: Math.cos(yaw), y: Math.sin(yaw) };
      const v = { x: -Math.sin(yaw), y: Math.cos(yaw) };
      const c0 = drag.start.center;
      const pu = (w.x - c0.x) * u.x + (w.y - c0.y) * u.y;
      const pv = (w.x - c0.x) * v.x + (w.y - c0.y) * v.y;
      const r = rangesOf(f);
      const axis = (
        h: number,
        p: number,
        size0: number,
        rng: [number, number],
      ) => {
        if (h === 0) return { size: size0, shift: 0 };
        if (alt)
          return { size: cm(clamp(2 * Math.abs(p), rng[0], rng[1])), shift: 0 };
        const anchor = (-h * size0) / 2;
        const size = cm(clamp((p - anchor) * h, rng[0], rng[1]));
        return { size, shift: anchor + (h * size) / 2 };
      };
      const au = axis(drag.hu, pu, drag.start.width, r.width);
      const av = axis(drag.hv, pv, drag.start.depth, r.depth);
      const center = {
        x: c0.x + u.x * au.shift + v.x * av.shift,
        y: c0.y + u.y * au.shift + v.y * av.shift,
      };
      setDrag({ ...drag, center, width: au.size, depth: av.size });
      scheduleCheck(drag.id, center, yaw, { width: au.size, depth: av.size });
      return true;
    }
    if (drag?.kind === "stairsWidth") {
      const st = level.elements[drag.id];
      if (!isStairs(st)) return true;
      const yaw = st.yaw ?? 0;
      const perp = { x: -Math.sin(yaw), y: Math.cos(yaw) };
      // La rive tirée suit la souris, la rive opposée reste en place.
      const dist =
        drag.side *
        ((w.x - st.position.x) * perp.x + (w.y - st.position.y) * perp.y);
      const half0 = stairsHalf(drag.id);
      const k = STAIR_ACROSS[st.kind ?? "straight"] ?? 1;
      const width = cm(clamp((st.width ?? 0.9) + (dist - half0) / k, 0.7, 1.2));
      setDrag({ ...drag, width });
      return true;
    }
    if (drag?.kind === "openingEdge") {
      const o = level.elements[drag.id];
      if (!isOpening(o)) return true;
      const ax = wallAxis(o.host_wall);
      if (!ax) return true;
      const s = (w.x - ax.a.x) * ax.u.x + (w.y - ax.a.y) * ax.u.y;
      const end0 = o.offset + o.width;
      if (alt) {
        const mid = o.offset + o.width / 2;
        const half = clamp(
          Math.abs(s - mid),
          OPENING_MIN_M / 2,
          Math.min(mid, ax.len - mid),
        );
        setDrag({ ...drag, offset: cm(mid - half), width: cm(2 * half) });
      } else if (drag.end === 0) {
        const offset = cm(clamp(s, 0, end0 - OPENING_MIN_M));
        setDrag({ ...drag, offset, width: cm(end0 - offset) });
      } else {
        setDrag({
          ...drag,
          width: cm(clamp(s - o.offset, OPENING_MIN_M, ax.len - o.offset)),
        });
      }
      return true;
    }
    return false;
  };

  const onMouseMove = (e: React.MouseEvent) => {
    const { x, y } = localPoint(e);
    setCursor(toWorld(view, x, y));
    onCursor?.(toWorld(view, x, y));
    if (placing?.opening && !drag) {
      setPlaceOpening(openingAt(toWorld(view, x, y)));
      return;
    }
    if (placing && !drag) {
      const at = placeFor(toWorld(view, x, y), e.altKey);
      if (at) {
        setPlaceAt(at);
        schedulePlaceCheck(at.position, at.yaw);
      }
      return;
    }
    if (!drag && (tool === "wall" || tool === "scale")) {
      setSnap(snapAt(x, y, pendingStartPoint, e.shiftKey));
      return;
    }
    if (!drag) return;
    if (moveResize(toWorld(view, x, y), e.altKey)) return;
    if (drag.kind === "pan") {
      setView({
        ...drag.view,
        tx: drag.view.tx + x - drag.startX,
        ty: drag.view.ty + y - drag.startY,
      });
    } else if (drag.kind === "camera") {
      setDrag({ ...drag, current: toWorld(view, x, y) });
    } else if (drag.kind === "aim") {
      const cam = level.elements[drag.id];
      if (isCamera(cam)) {
        const w = toWorld(view, x, y);
        setDrag({
          ...drag,
          yaw: Math.atan2(w.y - cam.position.y, w.x - cam.position.x),
        });
      }
    } else if (drag.kind === "furniture") {
      const f = level.elements[drag.id];
      if (!isFurniture(f)) return;
      const w = toWorld(view, x, y);
      const raw = { x: w.x - drag.offset.x, y: w.y - drag.offset.y };
      const snapped = e.altKey
        ? null
        : snapToWall(raw, f.bbox.depth * f.transform.scale, level, positions);
      const next = {
        current: snapped?.position ?? raw,
        yaw: snapped?.yaw ?? drag.yaw,
      };
      setDrag({ ...drag, ...next });
      scheduleCheck(drag.id, next.current, next.yaw);
    } else if (drag.kind === "spin") {
      const f = level.elements[drag.id];
      if (!isFurniture(f)) return;
      const w = toWorld(view, x, y);
      const yaw = snapYaw(
        Math.atan2(w.y - f.transform.position.y, w.x - f.transform.position.x) -
          Math.PI / 2,
        e.shiftKey || e.altKey,
      );
      setDrag({ ...drag, yaw });
      scheduleCheck(
        drag.id,
        { x: f.transform.position.x, y: f.transform.position.y },
        yaw,
      );
    } else if (drag.kind === "stairsMove") {
      const w = toWorld(view, x, y);
      const raw = { x: w.x - drag.offset.x, y: w.y - drag.offset.y };
      const current = snapStairs(drag.id, raw, e.altKey);
      if (distance(current, drag.current) > 1e-9) {
        setDrag({ ...drag, current });
        scheduleElementCheck(drag.id, { position: current });
      }
    } else if (drag.kind === "stairsSpin") {
      // Escalier (E4) : crans de 90° ; Alt : 15° ; Maj : libre. La montée suit.
      const st = level.elements[drag.id];
      if (!isStairs(st)) return;
      const w = toWorld(view, x, y);
      const yaw = snapYaw(
        Math.atan2(w.y - st.position.y, w.x - st.position.x),
        e.shiftKey,
        e.altKey ? Math.PI / 12 : Math.PI / 2,
      );
      if (Math.abs(yaw - drag.yaw) > 1e-9) {
        setDrag({ ...drag, yaw });
        scheduleElementCheck(drag.id, { yaw });
      }
    } else if (drag.kind === "vertex") {
      const s = snapAt(x, y, neighbourOf(drag.id), e.shiftKey, drag.id);
      setSnap(s);
      setDrag({ ...drag, current: s.point });
    }
  };

  const onMouseUp = async () => {
    if (drag?.kind === "resize") {
      const f = level.elements[drag.id];
      const changed =
        Math.abs(drag.width - drag.start.width) > 1e-6 ||
        Math.abs(drag.depth - drag.start.depth) > 1e-6;
      if (isFurniture(f) && changed) {
        const size = { width: drag.width, depth: drag.depth };
        const check = await onCheckFurniture?.(
          drag.id,
          drag.center,
          f.transform.yaw,
          size,
        );
        if (check && !check.valid) {
          showNotice(`Non redimensionné : ${check.message}`);
        } else {
          const s = f.transform.scale;
          onOp({
            op: "update_element",
            element_id: drag.id,
            fields: {
              bbox: { ...f.bbox, width: drag.width / s, depth: drag.depth / s },
              transform: {
                ...f.transform,
                position: {
                  x: cm(drag.center.x),
                  y: cm(drag.center.y),
                  z: f.transform.position.z,
                },
              },
            },
          });
        }
      }
      setLive(null);
    }
    if (drag?.kind === "stairsWidth") {
      const st = level.elements[drag.id];
      if (isStairs(st) && Math.abs(drag.width - (st.width ?? 0.9)) > 1e-6) {
        // Le centre suit la rive tirée (la rive opposée ne bouge pas) ; une trémie qui
        // entrerait dans un mur ou sortirait de la pièce est refusée, avec la raison (E4).
        const yaw = st.yaw ?? 0;
        const k = STAIR_ACROSS[st.kind ?? "straight"] ?? 1;
        const shift = (drag.side * (drag.width - (st.width ?? 0.9)) * k) / 2;
        const position = {
          x: cm(st.position.x - Math.sin(yaw) * shift),
          y: cm(st.position.y + Math.cos(yaw) * shift),
        };
        const check = await onCheckElement?.(drag.id, {
          width: drag.width,
          position,
        });
        if (check && !check.valid) {
          const why = check.violations.find((v) => v.severity === "high");
          showNotice(`Non élargi : ${why?.message ?? check.message}`);
        } else {
          onOp({
            op: "update_element",
            element_id: drag.id,
            fields: { width: drag.width, position },
          });
        }
      }
    }
    if (drag?.kind === "stairsMove") {
      const st = level.elements[drag.id];
      const position = { x: cm(drag.current.x), y: cm(drag.current.y) };
      if (isStairs(st) && distance(position, st.position) > 1e-6) {
        const check = await onCheckElement?.(drag.id, { position });
        if (check && !check.valid) {
          const why = check.violations.find((v) => v.severity === "high");
          showNotice(`Non déplacé : ${why?.message ?? check.message}`);
        } else {
          onOp({
            op: "update_element",
            element_id: drag.id,
            fields: { position },
          });
          const warn = check?.violations.find((v) => v.severity !== "high");
          if (warn) showNotice(`Déplacé ; attention : ${warn.message}`);
        }
      }
      setLive(null);
    }
    if (drag?.kind === "stairsSpin") {
      const st = level.elements[drag.id];
      const yaw = Math.round(drag.yaw * 1000) / 1000;
      if (isStairs(st) && Math.abs(yaw - (st.yaw ?? 0)) > 1e-6) {
        const check = await onCheckElement?.(drag.id, { yaw });
        if (check && !check.valid) {
          const why = check.violations.find((v) => v.severity === "high");
          showNotice(`Non tourné : ${why?.message ?? check.message}`);
        } else {
          onOp({ op: "update_element", element_id: drag.id, fields: { yaw } });
          const warn = check?.violations.find((v) => v.severity !== "high");
          if (warn) showNotice(`Tourné ; attention : ${warn.message}`);
        }
      }
      setLive(null);
    }
    if (drag?.kind === "openingEdge") {
      const o = level.elements[drag.id];
      if (
        isOpening(o) &&
        (Math.abs(drag.offset - o.offset) > 1e-6 ||
          Math.abs(drag.width - o.width) > 1e-6)
      )
        onOp({
          op: "update_element",
          element_id: drag.id,
          fields: { offset: drag.offset, width: drag.width },
        });
    }
    if (drag?.kind === "vertex") setSnap(null);
    if (drag?.kind === "vertex") {
      const original = level.vertices[drag.id]?.position;
      if (original && distance(original, drag.current) > 1e-9) {
        onOp({ op: "move_vertex", vertex_id: drag.id, to: drag.current });
      }
    }
    if (drag?.kind === "camera") {
      const cam = level.elements[drag.id];
      if (
        isCamera(cam) &&
        distance({ x: cam.position.x, y: cam.position.y }, drag.current) > 1e-6
      ) {
        onOp({
          op: "update_element",
          element_id: drag.id,
          fields: {
            position: {
              x: Math.round(drag.current.x * 100) / 100,
              y: Math.round(drag.current.y * 100) / 100,
              z: cam.position.z,
            },
          },
        });
      }
    }
    if (drag?.kind === "aim") {
      const cam = level.elements[drag.id];
      if (isCamera(cam) && Math.abs(cam.yaw - drag.yaw) > 1e-6) {
        onOp({
          op: "update_element",
          element_id: drag.id,
          fields: { yaw: Math.round(drag.yaw * 1000) / 1000 },
        });
      }
    }
    if (drag?.kind === "furniture" || drag?.kind === "spin") {
      const f = level.elements[drag.id];
      if (isFurniture(f)) {
        const here = { x: f.transform.position.x, y: f.transform.position.y };
        const position = drag.kind === "furniture" ? drag.current : here;
        const yaw = drag.yaw;
        const moved =
          distance(here, position) > 1e-6 ||
          Math.abs(yaw - f.transform.yaw) > 1e-6;
        if (moved) {
          const check = await onCheckFurniture?.(drag.id, position, yaw);
          if (check && !check.valid) {
            showNotice(`Non posé : ${check.message}`);
          } else {
            onOp({
              op: "update_element",
              element_id: drag.id,
              fields: {
                transform: {
                  ...f.transform,
                  position: {
                    x: Math.round(position.x * 100) / 100,
                    y: Math.round(position.y * 100) / 100,
                    z: f.transform.position.z,
                  },
                  yaw: Math.round(yaw * 1000) / 1000,
                },
              },
            });
          }
        }
      }
      setLive(null);
    }
    setDrag(null);
  };

  const onDoubleClick = () => setPendingStart(null);

  // Souris, stylet et doigt passent par les pointer events ; le doigt ajoute le pincement.
  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    e.currentTarget.setPointerCapture?.(e.pointerId);
    if (e.pointerType === "touch") {
      const { x, y } = localPoint(e);
      pointers.current.set(e.pointerId, { x, y });
      if (pointers.current.size === 2) {
        // Second doigt : pincement ; le geste commencé avec le premier est abandonné.
        const [a, b] = [...pointers.current.values()] as [
          { x: number; y: number },
          { x: number; y: number },
        ];
        pinch.current = {
          dist: Math.hypot(a.x - b.x, a.y - b.y),
          mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
        };
        setDrag(null);
        setLive(null);
        return;
      }
      if (pointers.current.size > 2) return;
    }
    onMouseDown(e);
  };

  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.pointerType === "touch" && pointers.current.has(e.pointerId)) {
      const { x, y } = localPoint(e);
      pointers.current.set(e.pointerId, { x, y });
      if (pinch.current && pointers.current.size >= 2) {
        const [a, b] = [...pointers.current.values()] as [
          { x: number; y: number },
          { x: number; y: number },
        ];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        const factor = dist / Math.max(pinch.current.dist, 1);
        const previous = pinch.current.mid;
        setView((v) => {
          const zoomed = zoomAt(v, factor, mid.x, mid.y);
          return {
            ...zoomed,
            tx: zoomed.tx + mid.x - previous.x,
            ty: zoomed.ty + mid.y - previous.y,
          };
        });
        pinch.current = { dist, mid };
        return;
      }
    }
    onMouseMove(e);
  };

  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.pointerType === "touch") {
      pointers.current.delete(e.pointerId);
      if (pinch.current) {
        if (pointers.current.size < 2) pinch.current = null;
        return; // fin d'un pincement : rien à poser
      }
    }
    void onMouseUp();
  };

  // Positions effectives des sommets (aperçu du glisser)
  const positions = useMemo(() => {
    const map: Record<string, Point2> = {};
    for (const [id, v] of Object.entries(level.vertices)) map[id] = v.position;
    if (drag?.kind === "vertex") map[drag.id] = drag.current;
    return map;
  }, [level.vertices, drag]);

  const S = (p: Point2) => toScreen(view, p);
  const pts = (poly: Point2[]) =>
    poly.map((p) => `${S(p).x.toFixed(1)},${S(p).y.toFixed(1)}`).join(" ");
  const gridPx = view.scale; // 1 m

  /** Symbole de la famille ; null sans symboles chargés. */
  const symbolOf = (category: string): FurnitureSymbol | null =>
    symbols ? (symbols.symbols[category] ?? symbols.generic) : null;
  /** Traits du symbole d'un meuble, à sa taille, sa place et son lacet (non cliquables). */
  const drawSymbol = (
    symbol: FurnitureSymbol | null,
    at: { x: number; y: number },
    yaw: number,
    w: number,
    d: number,
    extra = "",
  ) => {
    if (!symbol) return null;
    const cos = Math.cos(yaw);
    const sin = Math.sin(yaw);
    const world = ([u, v]: [number, number]): Point2 => ({
      x: at.x + cos * u * w - sin * v * d,
      y: at.y + sin * u * w + cos * v * d,
    });
    return (
      <g className={`furniture-symbols${extra ? ` ${extra}` : ""}`}>
        {symbol.outline === "ellipse" && (
          <polygon className="furniture-round" points={pts(ROUND.map(world))} />
        )}
        {symbol.paths.map((p, i) =>
          p.closed ? (
            <polygon
              key={i}
              className="furniture-symbol"
              points={pts(p.points.map(world))}
            />
          ) : (
            <polyline
              key={i}
              className="furniture-symbol"
              points={pts(p.points.map(world))}
            />
          ),
        )}
      </g>
    );
  };

  /** Transformation affichée d'un meuble : celle du glisser en cours, sinon la sienne. */
  /** Demi-encombrement en travers d'un escalier, d'après son contour en plan. */
  const stairsHalf = (id: string): number => {
    const st = level.elements[id];
    const outline = derived?.stairs?.[id]?.outline;
    if (!isStairs(st) || !outline?.length) return 0.45;
    const yaw = st.yaw ?? 0;
    const perp = { x: -Math.sin(yaw), y: Math.cos(yaw) };
    const across = outline.map(
      (p) => (p.x - st.position.x) * perp.x + (p.y - st.position.y) * perp.y,
    );
    return (Math.max(...across) - Math.min(...across)) / 2;
  };
  /** Meuble en cours de redimensionnement : sa boîte essayée (E2). */
  /** Poignée de rotation (E4) : tige et rond au-delà de la face avant (`dir`, à `reach`
   *  mètres du centre), angle en direct pendant le glisser, message de règle dessous. */
  const rotateHandle = (
    key: string,
    center: Point2,
    dir: number,
    reach: number,
    data: { spin?: string; spinStairs?: string },
    angle: number | null,
    message: string | undefined,
    invalid: boolean,
    title: string,
  ) => {
    const ux = Math.cos(dir);
    const uy = Math.sin(dir);
    const gap = ROTATE_GAP_PX / view.scale;
    const from = S({ x: center.x + ux * reach, y: center.y + uy * reach });
    const at = S({
      x: center.x + ux * (reach + gap),
      y: center.y + uy * (reach + gap),
    });
    return (
      <g key={key} className={`rotate${invalid ? " invalid" : ""}`}>
        <line
          className="rotate-stalk"
          x1={from.x}
          y1={from.y}
          x2={at.x}
          y2={at.y}
        />
        <circle
          className="rotate-handle"
          data-spin={data.spin}
          data-spin-stairs={data.spinStairs}
          cx={at.x}
          cy={at.y}
          r={9}
        >
          <title>{title}</title>
        </circle>
        <text className="rotate-glyph" x={at.x} y={at.y + 4}>
          ↻
        </text>
        {angle !== null && (
          <text className="rotate-angle" x={at.x} y={at.y - 15}>
            {degrees(angle)}°
          </text>
        )}
        {message && (
          <text className="rotate-message" x={at.x} y={at.y + 26}>
            {message}
          </text>
        )}
      </g>
    );
  };
  /** Symbole d'ouverture (E5) : vantaux, vitrages, rails, flèches ; arcs de battement. */
  const drawOpeningSymbol = (sym: OpeningSym, extra = "", invalid = false) => (
    <>
      {sym.lines.map((l, i) => (
        <polyline
          key={`l${i}`}
          className={`opening-symbol ${l.style}${extra ? ` ${extra}` : ""}`}
          points={pts(l.points)}
        />
      ))}
      {sym.arcs.map((arc, i) => {
        const a0 = (arc.startDeg * Math.PI) / 180;
        const a1 = ((arc.startDeg + arc.extentDeg) * Math.PI) / 180;
        const s0 = S({
          x: arc.center.x + arc.radius * Math.cos(a0),
          y: arc.center.y + arc.radius * Math.sin(a0),
        });
        const s1 = S({
          x: arc.center.x + arc.radius * Math.cos(a1),
          y: arc.center.y + arc.radius * Math.sin(a1),
        });
        const r = arc.radius * view.scale;
        // Y de l'écran vers le bas : un arc en sens direct se balaie en sens inverse.
        return (
          <path
            key={`a${i}`}
            className={`swing${invalid ? " invalid" : ""}${extra ? ` ${extra}` : ""}`}
            d={`M ${s0.x} ${s0.y} A ${r} ${r} 0 0 ${arc.extentDeg > 0 ? 0 : 1} ${s1.x} ${s1.y}`}
          />
        );
      })}
    </>
  );
  const shownFurniture = (f: Furniture): Furniture => {
    const t = shownTransform(f);
    if (drag?.kind !== "resize" || drag.id !== f.id)
      return { ...f, transform: t };
    return {
      ...f,
      transform: { ...t, position: { ...t.position, ...drag.center } },
      bbox: {
        ...f.bbox,
        width: drag.width / t.scale,
        depth: drag.depth / t.scale,
      },
    };
  };
  const shownTransform = (f: Furniture) =>
    drag?.kind === "furniture" && drag.id === f.id
      ? {
          ...f.transform,
          position: { ...f.transform.position, ...drag.current },
          yaw: drag.yaw,
        }
      : drag?.kind === "spin" && drag.id === f.id
        ? { ...f.transform, yaw: drag.yaw }
        : f.transform;
  /** Ligne du nom d'un meuble : au centre ; sous le meuble quand le nom est plus large que
   *  lui (lampadaire, chevet) : il ne passe plus sur le symbole (retour PO du 13 oct.). */
  const furnitureLabelY = (f: Furniture, cy: number): number => {
    const t = shownTransform(f);
    const w = f.bbox.width * t.scale * view.scale;
    const d = f.bbox.depth * t.scale * view.scale;
    const across = Math.max(
      Math.abs(Math.cos(t.yaw)) * w + Math.abs(Math.sin(t.yaw)) * d,
      1,
    );
    if (textWidth(f.name ?? f.category) <= across - 4) return cy - 4;
    const down = Math.abs(Math.sin(t.yaw)) * w + Math.abs(Math.cos(t.yaw)) * d;
    return cy + down / 2 + 12;
  };
  const areaText = (d: DerivedRoom) =>
    `${d.area_m2.toFixed(2)} m²${
      d.usable_area_m2 != null && d.usable_area_m2 < d.area_m2 - 1e-6
        ? ` · ${d.usable_area_m2.toFixed(2)} m² à plus de 1,80 m`
        : ""
    }`;
  // Cotes : écart en pixels d'écran (pas en mètres), la cote ne mord jamais sur le mur, quel
  // que soit le zoom ; pour un mur vertical, l'écart tient compte de la demi-largeur du texte.
  const dims = walls(level).flatMap((w) => {
    const a = positions[w.start_vertex];
    const b = positions[w.end_vertex];
    if (!a || !b) return [];
    const len = distance(a, b);
    if (len === 0) return [];
    const nx = -(b.y - a.y) / len;
    const ny = (b.x - a.x) / len;
    const uncertain = isUncertain(w.source);
    const label = `${uncertain ? "≈ " : ""}${formatLength(len, units)}`;
    const gapPx = 10 + Math.abs(nx) * (label.length * 3.1);
    const gap = w.thickness / 2 + gapPx / view.scale;
    const m = S({
      x: (a.x + b.x) / 2 + nx * gap,
      y: (a.y + b.y) / 2 + ny * gap,
    });
    return [{ id: w.id, label, uncertain, x: m.x, y: m.y + 4 }];
  });
  // Étiquettes sans chevauchement (audit responsive 1.3) : les cotes sont fixes ; pièce,
  // meubles et fantômes se décalent d'un ou deux crans s'il le faut.
  const obstacles: LabelBox[] = dims.map((d) => ({
    x: d.x,
    y: d.y,
    width: textWidth(d.label),
    height: 11,
  }));
  const requests: LabelRequest[] = [];
  // Le nom d'une pièce évite aussi ce qui est posé dedans : escaliers et meubles (b2).
  const placedBoxes = [
    ...Object.values(derived?.stairs ?? {}).map((s) => s.outline),
    ...furnitureItems(level).map((f) => furnitureCorners(shownFurniture(f))),
  ]
    .map((outline) => boxOf(outline.map(S)))
    .filter((b): b is LabelBox => b !== null);
  for (const room of rooms(level)) {
    const d = derived?.rooms[room.id];
    if (!d) continue;
    const c = S(d.centroid);
    requests.push({
      maxSteps: 5,
      avoid: placedBoxes,
      key: `room-${room.id}`,
      x: c.x,
      y: c.y + 16,
      width: Math.max(
        textWidth(room.name ?? "Pièce", 7.5),
        textWidth(areaText(d)),
      ),
      height: 28,
    });
  }
  for (const f of furnitureItems(level)) {
    const c = S(shownTransform(f).position);
    requests.push({
      key: `furn-${f.id}`,
      x: c.x,
      y: furnitureLabelY(f, c.y),
      width: textWidth(f.name ?? f.category),
      height: 11,
    });
  }
  for (const [prefix, list] of [
    ["ghost", ghost ?? []],
    ["alt", ghostAlt ?? []],
  ] as const) {
    for (const f of list) {
      const c = S(f.transform.position);
      requests.push({
        key: `${prefix}-${f.id}`,
        x: c.x,
        y: c.y - 4,
        width: textWidth(f.name ?? f.category),
        height: 11,
      });
    }
  }
  // Nom d'un point de vue : à droite de la caméra, il évite meubles, escaliers, noms de
  // pièce et cotes (retour PO du 13 oct. : « Vue d'ensemble » chevauchait la pièce).
  for (const cam of allCameras(level)) {
    if (drag?.kind === "camera" && drag.id === cam.id) continue;
    const c = S({ x: cam.position.x, y: cam.position.y });
    const width = textWidth(cameraLabel(cam), 6.2);
    requests.push({
      key: `cam-${cam.id}`,
      x: c.x + 10 + width / 2,
      y: c.y - 8,
      width,
      height: 12,
      maxSteps: 3,
      avoid: placedBoxes,
    });
  }
  const labelPos = layoutLabels(requests, obstacles);
  const at = (key: string, x: number, y: number) =>
    labelPos.get(key) ?? { x, y };

  const pendingStartPoint =
    pendingStart === null
      ? null
      : typeof pendingStart === "string"
        ? (level.vertices[pendingStart]?.position ?? null)
        : pendingStart;

  return (
    <svg
      ref={svgRef}
      className={`plan tool-${tool}${placing ? " placing-mode" : ""}`}
      width={size.width}
      height={size.height}
      onWheel={onWheel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onPointerLeave={() => onCursor?.(null)}
      onDoubleClick={onDoubleClick}
      onContextMenu={(e) => e.preventDefault()}
    >
      <defs>
        <pattern
          id="grid"
          width={gridPx}
          height={gridPx}
          patternUnits="userSpaceOnUse"
          x={view.tx % gridPx}
          y={view.ty % gridPx}
        >
          <path d={`M ${gridPx} 0 L 0 0 0 ${gridPx}`} className="grid-line" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill="url(#grid)" data-element="" />

      {/* Fond de photo : repère plan (Y vers le haut) → écran, rotation antihoraire en plan = horaire à l'écran */}
      {background && (
        <g
          className="photo"
          opacity={background.transform.opacity}
          transform={`translate(${S({ x: background.transform.tx, y: background.transform.ty }).x} ${S({ x: background.transform.tx, y: background.transform.ty }).y}) rotate(${(-background.transform.rotation * 180) / Math.PI}) scale(${view.scale * background.transform.scale * (background.transform.stretch_x ?? 1)} ${view.scale * background.transform.scale * (background.transform.stretch_y ?? 1)})`}
        >
          <image
            href={background.url}
            x={0}
            y={-background.heightPx}
            width={background.widthPx}
            height={background.heightPx}
            preserveAspectRatio="none"
          />
        </g>
      )}

      {/* Pièces */}
      {rooms(level).map((room) => {
        const d = derived?.rooms[room.id];
        if (!d) return null;
        const c = S(d.centroid);
        const selected =
          selection.kind === "element" && selection.id === room.id;
        return (
          <g key={room.id}>
            <polygon
              className={`room${selected ? " selected" : ""}`}
              points={pts(d.polygon)}
              data-element={room.id}
            />
            <text
              className="room-label"
              x={at(`room-${room.id}`, c.x, c.y + 16).x}
              y={at(`room-${room.id}`, c.x, c.y + 16).y - 16}
              data-element={room.id}
            >
              {room.name ?? "Pièce"}
            </text>
            <text
              className="room-area"
              x={at(`room-${room.id}`, c.x, c.y + 16).x}
              y={at(`room-${room.id}`, c.x, c.y + 16).y}
              data-element={room.id}
            >
              {areaText(d)}
            </text>
          </g>
        );
      })}

      {/* Pentes : égout, zone sous 1,80 m et ligne de hauteur utile */}
      {derived &&
        Object.entries(derived.roofs).map(([id, r]) => {
          const [a, b] = r.eave;
          const d = r.usable_offset_m;
          const shift = (p: Point2, k: number): Point2 => ({
            x: p.x + r.inward.x * k,
            y: p.y + r.inward.y * k,
          });
          const a2 = d ? shift(a, d) : null;
          const b2 = d ? shift(b, d) : null;
          const mid =
            a2 && b2 ? S({ x: (a2.x + b2.x) / 2, y: (a2.y + b2.y) / 2 }) : null;
          return (
            <g key={id} data-element={id}>
              {a2 && b2 && (
                <polygon className="roof-low" points={pts([a, b, b2, a2])} />
              )}
              <polyline className="roof-eave" points={pts([a, b])} />
              {a2 && b2 && (
                <polyline className="roof-usable" points={pts([a2, b2])} />
              )}
              {mid && (
                <text className="roof-label" x={mid.x} y={mid.y - 3}>
                  1,80 m sous pente
                </text>
              )}
            </g>
          );
        })}

      {/* Caméras : vues des photos (grisées si écartées) et points de vue libres (violets,
          déplaçables, pointe de visée à glisser) */}
      {allCameras(level).map((cam) => {
        const free = isFreeCamera(cam);
        const dragged = drag?.kind === "camera" && drag.id === cam.id;
        const aimed = drag?.kind === "aim" && drag.id === cam.id;
        const pos = dragged
          ? drag.current
          : { x: cam.position.x, y: cam.position.y };
        const yaw = aimed ? drag.yaw : cam.yaw;
        const c = S(pos);
        const tip = S({
          x: pos.x + 0.6 * Math.cos(yaw),
          y: pos.y + 0.6 * Math.sin(yaw),
        });
        const excluded = !free && isExcludedView(cam);
        const selected =
          selection.kind === "element" && selection.id === cam.id;
        const mods = `${excluded ? " excluded" : ""}${selected ? " selected" : ""}${free ? " free" : ""}`;
        // Champ de vision de la vue sélectionnée : demi-angle depuis la focale et la largeur
        // de la photo (sinon 36°), sur 1,5 m : ce que la photo couvre dans le plan.
        let fov: string | null = null;
        if (selected) {
          const width = cam.image_id ? imageWidths?.[cam.image_id] : undefined;
          const half =
            cam.focal_px && width
              ? Math.atan(width / (2 * cam.focal_px))
              : FOV_DEFAULT_HALF_ANGLE;
          const edge = (angle: number) =>
            S({
              x: pos.x + FOV_LENGTH_M * Math.cos(angle),
              y: pos.y + FOV_LENGTH_M * Math.sin(angle),
            });
          const a = edge(yaw - half);
          const b = edge(yaw + half);
          fov = `${c.x.toFixed(1)},${c.y.toFixed(1)} ${a.x.toFixed(1)},${a.y.toFixed(1)} ${b.x.toFixed(1)},${b.y.toFixed(1)}`;
        }
        return (
          <g key={cam.id}>
            {fov && <polygon className="camera-fov" points={fov} />}
            <line
              className={`camera-dir${mods}`}
              x1={c.x}
              y1={c.y}
              x2={tip.x}
              y2={tip.y}
            />
            <circle
              className={`camera${mods}`}
              cx={c.x}
              cy={c.y}
              r={7}
              data-element={cam.id}
            >
              <title>
                {cam.name ?? (free ? "Point de vue" : "Photo")}
                {excluded ? " (écartée)" : ""}
              </title>
            </circle>
            {free && (
              <circle
                className="camera-aim"
                cx={tip.x}
                cy={tip.y}
                r={5}
                data-aim={cam.id}
              >
                <title>Glisser pour viser</title>
              </circle>
            )}
            <text
              className={`camera-label${mods}`}
              x={
                at(
                  `cam-${cam.id}`,
                  c.x + 10 + textWidth(cameraLabel(cam), 6.2) / 2,
                  c.y - 8,
                ).x -
                textWidth(cameraLabel(cam), 6.2) / 2
              }
              y={at(`cam-${cam.id}`, c.x, c.y - 8).y}
            >
              {cameraLabel(cam)}
            </text>
          </g>
        );
      })}

      {/* Murs (recalculés localement pendant un glisser, sinon emprise du serveur) */}
      {walls(level).map((w) => {
        const a = positions[w.start_vertex];
        const b = positions[w.end_vertex];
        if (!a || !b) return null;
        const dragging = drag?.kind === "vertex";
        const d = derived?.walls[w.id];
        const poly =
          dragging || !d ? thickPolygon(a, b, w.thickness) : d.polygon;
        const selected = selection.kind === "element" && selection.id === w.id;
        const cls = [
          "wall",
          isUncertain(w.source) ? "uncertain" : "",
          selected ? "selected" : "",
          w.locked ? "locked" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <polygon
            key={w.id}
            className={cls}
            points={pts(poly)}
            data-element={w.id}
          />
        );
      })}

      {/* Ouvertures */}
      {openings(level).map((original) => {
        const edged =
          drag?.kind === "openingEdge" && drag.id === original.id
            ? { ...original, offset: drag.offset, width: drag.width }
            : original;
        const trial = doorTrial?.id === original.id ? doorTrial : null;
        const o = trial
          ? {
              ...edged,
              swing: { ...trial.swing, angle: edged.swing?.angle ?? 1.5708 },
            }
          : edged;
        const w = level.elements[o.host_wall];
        if (!w || w.type !== "wall") return null;
        const a = positions[w.start_vertex];
        const b = positions[w.end_vertex];
        if (!a || !b) return null;
        const shape = openingShape(o, a, b, w.thickness);
        if (!shape) return null;
        const len = distance(a, b);
        const u = { x: (b.x - a.x) / len, y: (b.y - a.y) / len };
        const sym = openingSymbol(o, a, u, w.thickness / 2);
        const selected = selection.kind === "element" && selection.id === o.id;
        const cls = [
          "opening",
          o.kind,
          isUncertain(o.source) ? "uncertain" : "",
          selected ? "selected" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <g key={o.id}>
            <polygon
              className={cls}
              points={pts(shape.rect)}
              data-element={o.id}
            />
            {drawOpeningSymbol(sym, "", trial?.invalid ?? false)}
            {selected &&
              tool === "select" &&
              o.swing &&
              (() => {
                // Rond dans le débattement (E4) : un clic retourne la porte ; pour une
                // coulissante, devant la baie, du côté du rail.
                const arc = sym.arcs[0];
                const side = o.swing.direction === "outward" ? -1 : 1;
                const mid = o.offset + o.width / 2;
                const m = S(
                  arc
                    ? {
                        x:
                          arc.center.x +
                          0.6 *
                            arc.radius *
                            Math.cos(
                              ((arc.startDeg + arc.extentDeg / 2) * Math.PI) /
                                180,
                            ),
                        y:
                          arc.center.y +
                          0.6 *
                            arc.radius *
                            Math.sin(
                              ((arc.startDeg + arc.extentDeg / 2) * Math.PI) /
                                180,
                            ),
                      }
                    : {
                        x:
                          a.x +
                          u.x * mid -
                          u.y * side * (w.thickness / 2 + 0.35),
                        y:
                          a.y +
                          u.y * mid +
                          u.x * side * (w.thickness / 2 + 0.35),
                      },
                );
                return (
                  <g className="flip">
                    <circle
                      className="flip-handle"
                      data-flip={o.id}
                      cx={m.x}
                      cy={m.y}
                      r={9}
                    >
                      <title>
                        {`Retourner (${swingText(o.swing as SwingT, o.kind, isPair(o), sideOf(o.host_wall, o.offset + o.width / 2))}) : clic ; G : côté, O : sens`}
                      </title>
                    </circle>
                    <text className="rotate-glyph" x={m.x} y={m.y + 4}>
                      ⇄
                    </text>
                  </g>
                );
              })()}
          </g>
        );
      })}

      {/* Velux (D1) : emprise au sol, en pointillé bleu ; cliquable pour sa fiche */}
      {derived &&
        roofWindows(level).map((w) => {
          const roof = derived.roofs[w.roof];
          if (!roof) return null;
          const corners = roofWindowCorners(roof, w);
          if (corners.length === 0) return null;
          const selected =
            selection.kind === "element" && selection.id === w.id;
          return (
            <polygon
              key={w.id}
              className={`roof-window${selected ? " selected" : ""}`}
              points={pts(corners)}
              data-element={w.id}
            >
              <title>{w.name ?? "Velux"}</title>
            </polygon>
          );
        })}

      {/* Escaliers (S1) : trémie, marches, ligne de foulée fléchée, coupe, garde-corps */}
      {derived &&
        Object.entries(derived.stairs ?? {}).map(([id, s]) => {
          const selected = selection.kind === "element" && selection.id === id;
          // Rotation en cours (E4) : le dessin tourne autour du centre, la montée suit.
          const st = level.elements[id];
          const stairs = isStairs(st) ? st : null;
          const yaw0 = stairs?.yaw ?? 0;
          const yawNow =
            drag?.kind === "stairsSpin" && drag.id === id ? drag.yaw : yaw0;
          const c = stairs ? S(stairs.position) : null;
          const moved =
            drag?.kind === "stairsMove" && drag.id === id ? drag.current : null;
          const turn =
            c && yawNow !== yaw0
              ? `rotate(${(-(yawNow - yaw0) * 180) / Math.PI} ${c.x} ${c.y})`
              : c && moved
                ? `translate(${S(moved).x - c.x} ${S(moved).y - c.y})`
                : undefined;
          const invalid = live?.id === id && !live.valid;
          const reach = stairs
            ? Math.max(
                0.3,
                ...s.outline.map(
                  (p) =>
                    (p.x - stairs.position.x) * Math.cos(yaw0) +
                    (p.y - stairs.position.y) * Math.sin(yaw0),
                ),
              )
            : 0;
          return (
            <g key={id}>
              <g
                className={`stairs${selected ? " selected" : ""}${invalid ? " invalid" : ""}`}
                data-element={id}
                transform={turn}
              >
                <polygon
                  className="stairs-outline"
                  points={pts(s.outline)}
                  data-element={id}
                />
                {s.treads.map((t, i) => (
                  <polygon
                    key={i}
                    className="stairs-tread"
                    points={pts(t)}
                    data-element={id}
                  />
                ))}
                <polyline
                  className="stairs-walk"
                  points={pts(s.walk)}
                  data-element={id}
                />
                <polyline
                  className="stairs-walk"
                  points={pts(s.arrow)}
                  data-element={id}
                />
                <polyline
                  className="stairs-cut"
                  points={pts(s.cut)}
                  data-element={id}
                />
                {s.railings.map((r, i) => (
                  <polyline
                    key={i}
                    className="stairs-railing"
                    points={pts(r)}
                    data-element={id}
                  />
                ))}
                <title>
                  {`${(level.elements[id] as { name?: string | null } | undefined)?.name ?? "Escalier"} : ${s.risers} marches de ${(s.riser_m * 100).toFixed(1)} cm, giron ${(s.going_m * 100).toFixed(0)} cm`}
                </title>
              </g>
              {selected &&
                tool === "select" &&
                stairs &&
                rotateHandle(
                  `rot-${id}`,
                  moved ?? stairs.position,
                  yawNow,
                  reach,
                  { spinStairs: id },
                  drag?.kind === "stairsSpin" && drag.id === id ? yawNow : null,
                  live?.id === id
                    ? live.valid
                      ? live.warn
                      : live.message
                    : undefined,
                  invalid,
                  "Tourner l'escalier : glisser (crans de 90° ; Alt : 15° ; Maj : libre)",
                )}
            </g>
          );
        })}

      {/* Meubles : boîte cotée du catalogue, tournée de son lacet ; flèche = face avant */}
      {furnitureItems(level).map((stored) => {
        const f = shownFurniture(stored);
        const t = f.transform;
        const corners = furnitureCorners(f);
        const selected = selection.kind === "element" && selection.id === f.id;
        const invalid = live?.id === f.id && !live.valid;
        const symbol = symbolOf(f.category);
        const cls = [
          "furniture",
          symbol?.outline === "ellipse" ? "round" : "",
          isUncertain(f.source) ? "uncertain" : "",
          selected ? "selected" : "",
          f.preserve ? "preserve" : "",
          invalid ? "invalid" : "",
          highlight?.includes(f.id) ? "highlight" : "",
        ]
          .filter(Boolean)
          .join(" ");
        const c = S(t.position);
        const front = S({
          x: t.position.x - Math.sin(t.yaw) * (f.bbox.depth / 2) * 0.8,
          y: t.position.y + Math.cos(t.yaw) * (f.bbox.depth / 2) * 0.8,
        });
        return (
          <g key={f.id}>
            <polygon
              className={cls}
              points={pts(corners)}
              data-element={f.id}
            />
            {drawSymbol(
              symbol,
              t.position,
              t.yaw,
              f.bbox.width * t.scale,
              f.bbox.depth * t.scale,
              [
                isUncertain(f.source) ? "uncertain" : "",
                invalid ? "invalid" : "",
              ]
                .filter(Boolean)
                .join(" "),
            )}
            {(selected || !symbol || symbol === symbols?.generic) && (
              <line
                className="furniture-front"
                x1={c.x}
                y1={c.y}
                x2={front.x}
                y2={front.y}
              />
            )}
            {selected &&
              tool === "select" &&
              !placing &&
              rotateHandle(
                `rot-${f.id}`,
                t.position,
                t.yaw + Math.PI / 2,
                (f.bbox.depth * t.scale) / 2,
                { spin: f.id },
                drag?.kind === "spin" && drag.id === f.id ? t.yaw : null,
                undefined,
                false,
                "Tourner : glisser (crans de 15°, 90° en priorité ; Maj : libre)",
              )}
            <text
              className="furniture-label"
              x={at(`furn-${f.id}`, c.x, furnitureLabelY(f, c.y)).x}
              y={at(`furn-${f.id}`, c.x, furnitureLabelY(f, c.y)).y}
            >
              {f.name ?? f.category}
            </text>
            {invalid && live && (
              <text className="furniture-warn" x={c.x} y={c.y + 12}>
                {live.message}
              </text>
            )}
          </g>
        );
      })}

      {/* Variante proposée : fantômes, rien dans la scène tant que « Appliquer » n'est pas cliqué */}
      {(ghost ?? []).map((f) => {
        const c = S(f.transform.position);
        return (
          <g key={`ghost-${f.id}`}>
            <polygon
              className="furniture ghost"
              points={pts(furnitureCorners(f))}
            />
            {drawSymbol(
              symbolOf(f.category),
              f.transform.position,
              f.transform.yaw,
              f.bbox.width * f.transform.scale,
              f.bbox.depth * f.transform.scale,
              "ghost",
            )}
            <text
              className="furniture-label ghost"
              x={at(`ghost-${f.id}`, c.x, c.y - 4).x}
              y={at(`ghost-${f.id}`, c.x, c.y - 4).y}
            >
              {f.name ?? f.category}
            </text>
          </g>
        );
      })}

      {/* Seconde variante de la comparaison : en vert */}
      {(ghostAlt ?? []).map((f) => {
        const c = S(f.transform.position);
        return (
          <g key={`alt-${f.id}`}>
            <polygon
              className="furniture ghost alt"
              points={pts(furnitureCorners(f))}
            />
            {drawSymbol(
              symbolOf(f.category),
              f.transform.position,
              f.transform.yaw,
              f.bbox.width * f.transform.scale,
              f.bbox.depth * f.transform.scale,
              "ghost alt",
            )}
            <text
              className="furniture-label ghost alt"
              x={at(`alt-${f.id}`, c.x, c.y - 4).x}
              y={at(`alt-${f.id}`, c.x, c.y - 4).y}
            >
              {f.name ?? f.category}
            </text>
          </g>
        );
      })}

      {/* Bibliothèque (E5) : l'ouverture choisie suit le mur sous le curseur, avec son symbole */}
      {placing?.opening &&
        placeOpening &&
        (() => {
          const w = level.elements[placeOpening.wall];
          if (!w || w.type !== "wall") return null;
          const a = positions[w.start_vertex];
          const b = positions[w.end_vertex];
          if (!a || !b) return null;
          const card = placing.opening;
          const ghost = {
            kind: card.kind,
            offset: placeOpening.offset,
            width: card.width,
            swing: defaultSwing(
              card.kind,
              towardRoom(
                placeOpening.wall,
                placeOpening.offset + card.width / 2,
              ),
            ),
            leaves: card.leaves,
            split: card.split,
          };
          const shape = openingShape(ghost, a, b, w.thickness);
          if (!shape) return null;
          const len = distance(a, b);
          const u = { x: (b.x - a.x) / len, y: (b.y - a.y) / len };
          const c = S({
            x: a.x + u.x * (ghost.offset + card.width / 2),
            y: a.y + u.y * (ghost.offset + card.width / 2),
          });
          return (
            <g className="placing opening-ghost" data-testid="opening-ghost">
              <polygon
                className={`opening ${card.kind} ghost`}
                points={pts(shape.rect)}
              />
              {drawOpeningSymbol(
                openingSymbol(ghost, a, u, w.thickness / 2),
                "ghost",
              )}
              <text className="furniture-label ghost" x={c.x} y={c.y - 16}>
                {card.label} · {formatLength(card.width, units)}
              </text>
            </g>
          );
        })()}

      {/* Bibliothèque : le meuble choisi suit le curseur, rouge si une règle dure est touchée */}
      {placing &&
        !placing.opening &&
        placeAt &&
        (() => {
          const cos = Math.cos(placeAt.yaw);
          const sin = Math.sin(placeAt.yaw);
          const corners = [
            [-1, -1],
            [1, -1],
            [1, 1],
            [-1, 1],
          ].map(([sx, sy]) => {
            const dx = (sx! * placing.width) / 2;
            const dy = (sy! * placing.depth) / 2;
            return {
              x: placeAt.position.x + cos * dx - sin * dy,
              y: placeAt.position.y + sin * dx + cos * dy,
            };
          });
          const c = S(placeAt.position);
          const front = S({
            x:
              placeAt.position.x -
              Math.sin(placeAt.yaw) * (placing.depth / 2) * 0.8,
            y:
              placeAt.position.y +
              Math.cos(placeAt.yaw) * (placing.depth / 2) * 0.8,
          });
          const invalid = placeLive !== null && !placeLive.valid;
          return (
            <g className="placing" data-testid="placing-ghost">
              <polygon
                className={`furniture ghost placing${invalid ? " invalid" : ""}`}
                points={pts(corners)}
              />
              {drawSymbol(
                symbolOf(placing.category),
                placeAt.position,
                placeAt.yaw,
                placing.width,
                placing.depth,
                `ghost placing${invalid ? " invalid" : ""}`,
              )}
              {!symbolOf(placing.category)?.paths.length && (
                <line
                  className="furniture-front"
                  x1={c.x}
                  y1={c.y}
                  x2={front.x}
                  y2={front.y}
                />
              )}
              <text
                className="furniture-label ghost"
                x={c.x}
                y={Math.min(...corners.map((q) => S(q).y)) - 6}
              >
                {placing.label}
              </text>
              {invalid && (
                <text className="furniture-warn" x={c.x} y={c.y + 12}>
                  {placeLive.message}
                </text>
              )}
            </g>
          );
        })()}

      {notice && (
        <text className="hud notice" x={insets.left} y={insets.top + 12}>
          {notice}
        </text>
      )}

      {/* Cotations */}
      {dims.map((d) => (
        <text
          key={`dim-${d.id}`}
          className={`dim${d.uncertain ? " uncertain" : ""}`}
          x={d.x}
          y={d.y}
        >
          {d.label}
        </text>
      ))}

      {/* Aperçu du mur en cours (ou de la cote de calibrage) */}
      {(tool === "wall" || tool === "scale") && pendingStartPoint && cursor && (
        <line
          className="pending"
          x1={S(pendingStartPoint).x}
          y1={S(pendingStartPoint).y}
          x2={S(snap?.point ?? cursor).x}
          y2={S(snap?.point ?? cursor).y}
        />
      )}

      {/* Accroches (E1) : guides pointillés, repère et étiquette (« Perpendiculaire »…) */}
      {snap &&
        (tool === "wall" || tool === "scale" || drag?.kind === "vertex") && (
          <g className="snap" data-snap={snap.kind ?? "none"}>
            {snap.guides.map((g, i) => (
              <line
                key={i}
                className="snap-guide"
                x1={S(g.from).x}
                y1={S(g.from).y}
                x2={S(g.to).x}
                y2={S(g.to).y}
              />
            ))}
            {snap.kind && (
              <>
                <circle
                  className="snap-mark"
                  cx={S(snap.point).x}
                  cy={S(snap.point).y}
                  r={5}
                />
                <text
                  className="snap-label"
                  x={S(snap.point).x + 10}
                  y={S(snap.point).y - 10}
                >
                  {snap.label}
                </text>
              </>
            )}
          </g>
        )}

      {/* Poignées de redimensionnement (E2) de l'élément sélectionné */}
      {tool === "select" &&
        !placing &&
        selection.kind === "element" &&
        (() => {
          const el = level.elements[selection.id];
          const handles: { key: string; p: Point2 }[] = [];
          let label: { p: Point2; text: string } | null = null;
          if (isFurniture(el)) {
            const f = shownFurniture(el);
            const yaw = f.transform.yaw;
            const w = f.bbox.width * f.transform.scale;
            const d = f.bbox.depth * f.transform.scale;
            const c = f.transform.position;
            for (const hu of [-1, 0, 1])
              for (const hv of [-1, 0, 1]) {
                if (hu === 0 && hv === 0) continue;
                const lu = (hu * w) / 2;
                const lv = (hv * d) / 2;
                handles.push({
                  key: `f|${el.id}|${hu}|${hv}`,
                  p: {
                    x: c.x + Math.cos(yaw) * lu - Math.sin(yaw) * lv,
                    y: c.y + Math.sin(yaw) * lu + Math.cos(yaw) * lv,
                  },
                });
              }
            if (drag?.kind === "resize" && drag.id === el.id)
              label = {
                p: { x: c.x, y: c.y },
                text: `${formatLength(w, units)} × ${formatLength(d, units)}`,
              };
          } else if (isStairs(el)) {
            const yaw = el.yaw ?? 0;
            const perp = { x: -Math.sin(yaw), y: Math.cos(yaw) };
            const half0 = stairsHalf(el.id);
            const k = STAIR_ACROSS[el.kind ?? "straight"] ?? 1;
            const width =
              drag?.kind === "stairsWidth" && drag.id === el.id
                ? drag.width
                : (el.width ?? 0.9);
            const half = half0 + ((width - (el.width ?? 0.9)) * k) / 2;
            const shift =
              drag?.kind === "stairsWidth" && drag.id === el.id
                ? (drag.side * (width - (el.width ?? 0.9)) * k) / 2
                : 0;
            const c0 = {
              x: el.position.x + perp.x * shift,
              y: el.position.y + perp.y * shift,
            };
            for (const side of [-1, 1])
              handles.push({
                key: `s|${el.id}|${side}`,
                p: {
                  x: c0.x + perp.x * half * side,
                  y: c0.y + perp.y * half * side,
                },
              });
            if (drag?.kind === "stairsWidth" && drag.id === el.id)
              label = {
                p: c0,
                text: `Largeur ${formatLength(width, units)}`,
              };
          } else if (isOpening(el)) {
            const o =
              drag?.kind === "openingEdge" && drag.id === el.id
                ? { ...el, offset: drag.offset, width: drag.width }
                : el;
            const ax = wallAxis(o.host_wall);
            if (ax) {
              for (const [end, s] of [
                [0, o.offset],
                [1, o.offset + o.width],
              ] as const)
                handles.push({
                  key: `o|${el.id}|${end}`,
                  p: { x: ax.a.x + ax.u.x * s, y: ax.a.y + ax.u.y * s },
                });
              if (drag?.kind === "openingEdge" && drag.id === el.id) {
                const m = o.offset + o.width / 2;
                label = {
                  p: { x: ax.a.x + ax.u.x * m, y: ax.a.y + ax.u.y * m },
                  text: formatLength(o.width, units),
                };
              }
            }
          }
          return (
            <g className="resize">
              {handles.map((h) => (
                <rect
                  key={h.key}
                  className="resize-handle"
                  data-handle={h.key}
                  x={S(h.p).x - 5}
                  y={S(h.p).y - 5}
                  width={10}
                  height={10}
                />
              ))}
              {label && (
                <text
                  className="resize-label"
                  x={S(label.p).x}
                  y={S(label.p).y - 14}
                >
                  {label.text}
                </text>
              )}
            </g>
          );
        })()}

      {/* Sommets */}
      {Object.entries(level.vertices).map(([id, v]) => {
        const p = positions[id] ?? v.position;
        const s = S(p);
        const selected = selection.kind === "vertex" && selection.id === id;
        const cls = [
          "vertex",
          isUncertain(v.source) ? "uncertain" : "",
          selected ? "selected" : "",
          v.locked ? "locked" : "",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <circle
            key={id}
            className={cls}
            cx={s.x}
            cy={s.y}
            r={selected ? 6 : 4.5}
          />
        );
      })}

      {/* B2d : les curseurs des autres membres sur ce plan, en mètres. */}
      {peers && (
        <PlanPeers
          store={peers.store}
          peers={peers.peers}
          levelId={level.id}
          at={S}
        />
      )}

      {/* Boussole : le nord du plan (réglable dans Rendre), en haut à droite sous la rangée
          du haut, à côté de la palette : jamais sous la barre du bas ni sous la bande. */}
      {(() => {
        const cx = size.width - 34;
        const cy = insets.top + 2;
        // Projet servi par une ancienne API : pas de nord → 90° (haut du plan), jamais NaN.
        const north = Number.isFinite(level.north_deg) ? level.north_deg : 90;
        const a = (north * Math.PI) / 180;
        return (
          <g className="compass">
            <title>Nord du plan : {north}°</title>
            <circle cx={cx} cy={cy} r={16} />
            <line
              x1={cx}
              y1={cy}
              x2={cx + 13 * Math.cos(a)}
              y2={cy - 13 * Math.sin(a)}
            />
            <text x={cx + 22 * Math.cos(a)} y={cy - 22 * Math.sin(a) + 4}>
              N
            </text>
          </g>
        );
      })()}
      <text className="hud" x={insets.left} y={insets.top - 6}>
        {cursor ? `${cursor.x.toFixed(2)} ; ${cursor.y.toFixed(2)} m` : ""}
        {size.width < 560 ? "" : "  ·  "}
        {size.width < 560
          ? ""
          : placing
            ? ""
            : tool === "wall"
              ? pendingStart
                ? coarse
                  ? "Toucher : mur suivant · double-toucher : terminer"
                  : "Clic : mur suivant · Échap ou double-clic : terminer · Maj : tracé libre"
                : `${act} : point de départ du mur`
              : tool === "door" || tool === "window"
                ? `${act} sur un mur pour poser l'ouverture`
                : tool === "scale"
                  ? pendingStart
                    ? `Second ${act.toLowerCase()} : autre bout de la longueur connue`
                    : `Recalibrer : ${coarse ? "touchez" : "cliquez"} les deux bouts d'une longueur connue`
                  : tool === "select"
                    ? coarse
                      ? "Glisser un sommet pour le déplacer · deux doigts : zoom et déplacement de la vue"
                      : "Glisser un sommet pour le déplacer · molette : zoom · bouton central : déplacer la vue"
                    : "Glisser pour déplacer la vue"}
        {placing && (
          <tspan className="placing-hint">
            {placing.opening
              ? `${act} sur un mur pour poser « ${placing.label} » · Échap pour annuler`
              : `${act} sur le plan pour poser « ${placing.label} » · accroché au mur proche${coarse ? "" : " (Alt : libre)"} · Échap pour annuler`}
          </tspan>
        )}
      </text>
    </svg>
  );
}

function samePoint(
  level: Level,
  a: string | Point2,
  b: string | Point2,
): boolean {
  const pa = typeof a === "string" ? level.vertices[a]?.position : a;
  const pb = typeof b === "string" ? level.vertices[b]?.position : b;
  if (typeof a === "string" && typeof b === "string") return a === b;
  return !!pa && !!pb && distance(pa, pb) < 1e-9;
}

function thickPolygon(a: Point2, b: Point2, thickness: number): Point2[] {
  const len = distance(a, b);
  if (len === 0) return [a, a, a, a];
  const nx = (-(b.y - a.y) / len) * (thickness / 2);
  const ny = ((b.x - a.x) / len) * (thickness / 2);
  return [
    { x: a.x + nx, y: a.y + ny },
    { x: b.x + nx, y: b.y + ny },
    { x: b.x - nx, y: b.y - ny },
    { x: a.x - nx, y: a.y - ny },
  ];
}

function openingShape(
  o: { offset: number; width: number },
  a: Point2,
  b: Point2,
  thickness: number,
): { rect: Point2[] } | null {
  const len = distance(a, b);
  if (len === 0) return null;
  const ux = (b.x - a.x) / len;
  const uy = (b.y - a.y) / len;
  const nx = -uy;
  const ny = ux;
  const half = thickness / 2 + 0.01;
  const p0 = { x: a.x + ux * o.offset, y: a.y + uy * o.offset };
  const p1 = { x: p0.x + ux * o.width, y: p0.y + uy * o.width };
  return {
    rect: [
      { x: p0.x + nx * half, y: p0.y + ny * half },
      { x: p1.x + nx * half, y: p1.y + ny * half },
      { x: p1.x - nx * half, y: p1.y - ny * half },
      { x: p0.x - nx * half, y: p0.y - ny * half },
    ],
  };
}
