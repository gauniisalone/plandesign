import type { Camera, DerivedLevel, Point2 } from "./api/types.gen";

/** Distance devant la caméra où l'on cherche la pièce regardée (m). */
const LOOK_AHEAD_M = 1.2;

function inside(p: Point2, polygon: Point2[]): boolean {
  let hit = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i]!;
    const b = polygon[j]!;
    if (
      a.y > p.y !== b.y > p.y &&
      p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x
    )
      hit = !hit;
  }
  return hit;
}

/** « Affiner cette vue » (P3) : la pièce que la vue regarde. D'abord celle qui contient le
 *  point à 1,20 m devant la caméra, sinon celle où elle se tient, sinon la plus proche. Les
 *  contours viennent du serveur (`derived.rooms`) ; seul le test de point est fait ici. */
export function roomForView(
  camera: Camera,
  derived: DerivedLevel | null,
): string | null {
  const rooms = Object.entries(derived?.rooms ?? {});
  if (rooms.length === 0) return null;
  const at = camera.position;
  const ahead = {
    x: at.x + LOOK_AHEAD_M * Math.cos(camera.yaw),
    y: at.y + LOOK_AHEAD_M * Math.sin(camera.yaw),
  };
  for (const p of [ahead, at]) {
    const found = rooms.find(([, r]) => inside(p, r.polygon));
    if (found) return found[0];
  }
  const nearest = rooms
    .map(([id, r]) => ({
      id,
      d: Math.hypot(r.centroid.x - at.x, r.centroid.y - at.y),
    }))
    .sort((a, b) => a.d - b.d)[0];
  return nearest?.id ?? null;
}
