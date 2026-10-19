import type { Point2 } from "../api/types.gen";
import { usePresence, type Peer, type PresenceStore } from "../state/live";

/** B2d (KAN-11) : curseurs des autres membres sur ce plan, reçus en mètres et projetés comme
 *  le reste du dessin (`at`, mètres → pixels) : ils suivent le zoom et le déplacement. Seul
 *  ce calque se redessine à chaque mouvement reçu ; rien pour une personne partie. */
export function PlanPeers({
  store,
  peers,
  levelId,
  at,
}: {
  store: PresenceStore;
  peers: Peer[];
  levelId: string;
  at: (p: Point2) => { x: number; y: number };
}) {
  const { planCursors } = usePresence(store);
  const byId = new Map(peers.map((p) => [p.user_id, p]));
  return (
    <g className="plan-peers">
      {Object.entries(planCursors).flatMap(([who, cursor]) => {
        const peer = byId.get(who);
        if (!peer || cursor.level !== levelId) return [];
        const { x, y } = at({ x: cursor.x, y: cursor.y });
        return [
          <g
            key={who}
            className="plan-peer"
            role="img"
            aria-label={`Curseur de ${peer.name}`}
            transform={`translate(${x} ${y})`}
          >
            <path
              d="M0 0 L0 14 L4 10 L7 16 L9.5 15 L6.5 9 L12 9 Z"
              fill={peer.color}
              stroke="#fff"
              strokeWidth={1.5}
            />
            <text x={12} y={22} fill={peer.color}>
              {peer.name}
            </text>
          </g>,
        ];
      })}
    </g>
  );
}
