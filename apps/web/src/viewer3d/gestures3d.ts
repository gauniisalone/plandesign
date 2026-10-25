/** KAN-92 : les gestes de la vue 3D, Pousser/Tirer et Zone de sol (à la SketchUp).
 *
 *  Pousser/Tirer (P) : la face survolée se surligne et dit ce qu'elle tire ; glisser la tire,
 *  ou cliquer, bouger, recliquer ; pendant le geste, taper une valeur (« 12 cm ») puis Entrée,
 *  Échap annule. Zone de sol (Z) : 4 clics sur le sol, accrochés aux angles et faces
 *  intérieures de la pièce ; le 4ᵉ ferme la zone ; Retour arrière défait le dernier point,
 *  Échap abandonne. Chaque geste envoie UNE commande (un pas d'historique). La rotation de la
 *  vue reste au glisser dans le vide. */

import * as THREE from "three";
import type { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Op } from "../api/ops";
import type { Point2 } from "../api/types.gen";
import { snapZoneCorner } from "../editor2d/zoneSnap";
import {
  bound,
  dragged,
  faceKind,
  parseTyped,
  pushPullOp,
  pushText,
  type PushKind,
} from "./pushPull";

export type Tool3D = "pushpull" | "floorzone";

export interface GestureElement {
  type: string;
  thickness?: number;
  height?: number | null;
  elevation?: number;
  /** Mur : le côté de l'épaisseur (« center », « left », « right ») ; une ouverture a aussi un
   *  `offset`, numérique, sans rapport. */
  offset?: string | number;
}

export interface GestureInputs {
  tool: Tool3D | null;
  elements: Record<string, GestureElement>;
  /** Hauteur du niveau (plafond) et altitude de son sol. */
  ceiling: number;
  floorZ: number;
  /** Polygones intérieurs des pièces (accroche des coins de zone). */
  rooms: Point2[][];
  onOp: (op: Op) => void;
}

export interface Cote {
  x: number;
  y: number;
  text: string;
  /** Limite touchée ou consigne (« 60 cm au plus », « 4ᵉ clic : ferme la zone »). */
  note: string | null;
  warn: boolean;
}

interface PushGesture {
  id: string;
  kind: PushKind;
  start: number;
  value: number;
  limit: string | null;
  offset?: string;
  anchor: THREE.Vector3;
  normal: THREE.Vector3;
  from: { x: number; y: number };
  pressed: boolean;
  moved: boolean;
  typed: string;
}

const DRAG_PX = 4;
const HOVER = 0xc4743f;
const ZONE_REACH_M = 0.15;

/** Plan (x, y, z) ↔ three (x, z, −y). */
const toPlan = (v: THREE.Vector3): Point2 => ({ x: v.x, y: -v.z });

export function attachGestures(ctx: {
  canvas: HTMLCanvasElement;
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  scene: THREE.Scene;
  root: () => THREE.Object3D | null;
  inputs: () => GestureInputs;
  setCote: (c: Cote | null) => void;
}): { active: () => boolean; reset: () => void; dispose: () => void } {
  const { canvas, camera, controls, scene } = ctx;
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let push: PushGesture | null = null;
  let hovered: string | null = null;
  let zone: Point2[] = [];
  let downAt: { x: number; y: number } | null = null;
  const preview = new THREE.Line(
    new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({ color: 0x3e5a7e, depthTest: false }),
  );
  preview.renderOrder = 999;
  preview.visible = false;
  scene.add(preview);

  const local = (e: PointerEvent) => {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top, r };
  };

  const hit = (e: PointerEvent) => {
    const root = ctx.root();
    if (!root) return null;
    const { x, y, r } = local(e);
    pointer.set((x / r.width) * 2 - 1, -(y / r.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const found = raycaster
      .intersectObject(root, true)
      .find((h) => h.object.visible && !h.object.userData["cap"]);
    if (!found) return null;
    const name = found.object.name || found.object.parent?.name || "";
    const normal = found.face
      ? found.face.normal.clone().transformDirection(found.object.matrixWorld)
      : new THREE.Vector3(0, 1, 0);
    return {
      id: name.split("#")[0] ?? "",
      point: found.point.clone(),
      normal,
      x,
      y,
    };
  };

  const highlight = (id: string | null) => {
    if (id === hovered) return;
    const root = ctx.root();
    root?.traverse((obj) => {
      if (!(obj instanceof THREE.Mesh) || obj.userData["cap"]) return;
      const m = obj.material as THREE.MeshStandardMaterial;
      const own = (obj.name || obj.parent?.name || "").split("#")[0];
      if (own === id) {
        obj.userData["hoverSaved"] ??= {
          color: m.emissive.getHex(),
          intensity: m.emissiveIntensity,
        };
        m.emissive = new THREE.Color(HOVER);
        m.emissiveIntensity = 0.45;
      } else if (obj.userData["hoverSaved"]) {
        const saved = obj.userData["hoverSaved"] as {
          color: number;
          intensity: number;
        };
        m.emissive = new THREE.Color(saved.color);
        m.emissiveIntensity = saved.intensity;
        delete obj.userData["hoverSaved"];
      }
    });
    hovered = id;
  };

  /** Avancée de la face (mètres le long de sa normale) pour un déplacement à l'écran. */
  const advance = (g: PushGesture, x: number, y: number) => {
    const r = canvas.getBoundingClientRect();
    const screen = (v: THREE.Vector3) => {
      const p = v.clone().project(camera);
      return new THREE.Vector2(
        ((p.x + 1) / 2) * r.width,
        ((1 - p.y) / 2) * r.height,
      );
    };
    const s0 = screen(g.anchor);
    const d = screen(g.anchor.clone().add(g.normal)).sub(s0);
    const ppm = d.length();
    if (ppm < 1) return 0;
    return (
      new THREE.Vector2(x - g.from.x, y - g.from.y).dot(d.divideScalar(ppm)) /
      ppm
    );
  };

  const showPush = (g: PushGesture, x: number, y: number) => {
    const typed = g.typed ? ` → ${g.typed}` : "";
    ctx.setCote({
      x,
      y,
      text: `${pushText(g.kind, g.value)}${typed}`,
      note:
        g.limit ?? (g.pressed ? null : "Entrée : valider · Échap : annuler"),
      warn: g.limit !== null,
    });
  };

  const finishPush = (value?: number) => {
    const g = push;
    if (!g) return;
    const inputs = ctx.inputs();
    const final = bound(g.kind, value ?? g.value, inputs.ceiling).value;
    push = null;
    controls.enabled = true;
    ctx.setCote(null);
    if (Math.abs(final - g.start) > 1e-4)
      inputs.onOp(pushPullOp(g.id, g.kind, final, inputs.ceiling));
  };

  const cancel = () => {
    push = null;
    zone = [];
    preview.visible = false;
    controls.enabled = true;
    ctx.setCote(null);
  };

  const zonePoint = (e: PointerEvent): Point2 | null => {
    const h = hit(e);
    if (!h || h.normal.y < 0.7) return null;
    const type = ctx.inputs().elements[h.id]?.type;
    if (type !== "floor" && type !== "floor_zone") return null;
    return snapZoneCorner(toPlan(h.point), ctx.inputs().rooms, ZONE_REACH_M)
      .point;
  };

  const drawZone = (cursor: Point2 | null) => {
    const pts = cursor ? [...zone, cursor] : zone;
    if (!pts.length) {
      preview.visible = false;
      return;
    }
    const z = ctx.inputs().floorZ + 0.01;
    const loop = pts.length >= 3 ? [...pts, pts[0]!] : pts;
    preview.geometry.setFromPoints(
      loop.map((p) => new THREE.Vector3(p.x, z, -p.y)),
    );
    preview.visible = true;
  };

  const onMove = (e: PointerEvent) => {
    const tool = ctx.inputs().tool;
    if (!tool) return;
    const { x, y } = local(e);
    if (push) {
      const inputs = ctx.inputs();
      const moved = Math.hypot(x - push.from.x, y - push.from.y) > DRAG_PX;
      push.moved ||= moved;
      if (!push.typed) {
        const b = dragged(
          push.kind,
          push.start,
          advance(push, x, y),
          inputs.ceiling,
          push.offset,
        );
        push.value = b.value;
        push.limit = b.limit;
      }
      showPush(push, x, y);
      return;
    }
    if (tool === "pushpull") {
      const h = hit(e);
      const element = h ? ctx.inputs().elements[h.id] : undefined;
      const kind = h ? faceKind(element?.type, h.normal.y) : null;
      highlight(kind ? h!.id : null);
      canvas.style.cursor = kind ? "pointer" : "";
      ctx.setCote(
        kind && element
          ? {
              x,
              y,
              text: pushText(kind, current(kind, element)),
              note: "glisser ou cliquer",
              warn: false,
            }
          : null,
      );
      return;
    }
    const p = zonePoint(e);
    drawZone(p);
    ctx.setCote(
      p
        ? {
            x,
            y,
            text: `Zone de sol : coin ${zone.length + 1} sur 4`,
            note: zone.length === 3 ? "ce clic ferme la zone" : null,
            warn: false,
          }
        : {
            x,
            y,
            text: "Zone de sol : cliquez sur le sol de la pièce",
            note: null,
            warn: false,
          },
    );
  };

  const current = (kind: PushKind, el: GestureElement) => {
    const ceiling = ctx.inputs().ceiling;
    if (kind === "thickness") return el.thickness ?? 0.15;
    if (kind === "elevation") return el.elevation ?? 0;
    return el.height ?? ceiling;
  };

  const onDown = (e: PointerEvent) => {
    const tool = ctx.inputs().tool;
    if (!tool || e.button !== 0) return;
    downAt = { x: e.clientX, y: e.clientY };
    if (tool !== "pushpull") return;
    if (push) {
      // Clic, bouger, reclic : le deuxième clic valide.
      finishPush(
        push.typed ? (parseTyped(push.typed) ?? push.value) : undefined,
      );
      e.stopImmediatePropagation();
      return;
    }
    const h = hit(e);
    const element = h ? ctx.inputs().elements[h.id] : undefined;
    const kind = h && element ? faceKind(element.type, h.normal.y) : null;
    if (!h || !element || !kind) return;
    const start = current(kind, element);
    const normal =
      kind === "thickness"
        ? new THREE.Vector3(h.normal.x, 0, h.normal.z).normalize()
        : new THREE.Vector3(0, 1, 0);
    push = {
      id: h.id,
      kind,
      start,
      value: start,
      limit: null,
      offset: typeof element.offset === "string" ? element.offset : undefined,
      anchor: h.point,
      normal,
      from: { x: h.x, y: h.y },
      pressed: true,
      moved: false,
      typed: "",
    };
    controls.enabled = false;
    e.stopImmediatePropagation();
    showPush(push, h.x, h.y);
  };

  const onUp = (e: PointerEvent) => {
    const tool = ctx.inputs().tool;
    if (!tool || e.button !== 0) return;
    const click =
      downAt &&
      Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) <= DRAG_PX;
    downAt = null;
    if (push?.pressed) {
      if (push.moved) finishPush();
      else push.pressed = false; // armé : bouger puis recliquer, ou taper une valeur
      return;
    }
    if (tool === "floorzone" && click) {
      const p = zonePoint(e);
      if (!p) return;
      zone = [...zone, p];
      if (zone.length === 4) {
        const polygon = zone as [Point2, Point2, Point2, Point2];
        zone = [];
        preview.visible = false;
        ctx.setCote(null);
        ctx.inputs().onOp({ op: "add_floor_zone", polygon });
      } else drawZone(p);
    }
  };

  const onKey = (e: KeyboardEvent) => {
    const tool = ctx.inputs().tool;
    if (!tool || (!push && !zone.length)) return;
    const target = e.target as HTMLElement | null;
    // Une saisie de texte (post-it, commentaire, champ) garde ses touches.
    if (
      target &&
      (["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName) ||
        target.isContentEditable)
    )
      return;
    if (e.key === "Escape") {
      cancel();
    } else if (push && e.key === "Enter") {
      finishPush(
        push.typed ? (parseTyped(push.typed) ?? undefined) : undefined,
      );
    } else if (e.key === "Backspace") {
      if (push) push.typed = push.typed.slice(0, -1);
      else {
        zone = zone.slice(0, -1);
        drawZone(null);
      }
    } else if (push && /^[0-9.,cm ]$/i.test(e.key)) {
      push.typed += e.key;
      const v = parseTyped(push.typed);
      if (v !== null) {
        const b = bound(push.kind, v, ctx.inputs().ceiling);
        push.value = b.value;
        push.limit = b.limit;
      }
    } else return;
    e.preventDefault();
    e.stopPropagation();
    if (push) showPush(push, push.from.x, push.from.y);
  };

  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerdown", onDown, { capture: true });
  canvas.addEventListener("pointerup", onUp);
  window.addEventListener("keydown", onKey, { capture: true });
  return {
    active: () => ctx.inputs().tool !== null,
    reset: () => {
      cancel();
      highlight(null);
      canvas.style.cursor = "";
    },
    dispose: () => {
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerdown", onDown, { capture: true });
      canvas.removeEventListener("pointerup", onUp);
      window.removeEventListener("keydown", onKey, { capture: true });
      highlight(null);
      scene.remove(preview);
      preview.geometry.dispose();
    },
  };
}
