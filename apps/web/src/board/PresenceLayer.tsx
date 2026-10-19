import type { BoardItem } from "../api/client";
import { usePresence, type Peer, type PresenceStore } from "../state/live";

/** B2b : curseurs nommés et sélections des autres membres, dans le monde de l'espace de travail
 *  (mêmes coordonnées que les éléments). Seul ce calque se redessine à chaque mouvement reçu.
 *  Rien pour une personne partie ou inconnue. */
export function PresenceLayer({
  store,
  peers,
  items,
}: {
  store: PresenceStore;
  peers: Peer[];
  items: BoardItem[];
}) {
  const { cursors, selections } = usePresence(store);
  const byId = new Map(peers.map((p) => [p.user_id, p]));
  const byItem = new Map(items.map((i) => [i.id, i]));
  return (
    <>
      {Object.entries(selections).flatMap(([who, ids]) => {
        const peer = byId.get(who);
        if (!peer) return [];
        return ids.flatMap((id, n) => {
          const item = byItem.get(id);
          if (!item) return [];
          return [
            <div
              key={`${who}:${id}`}
              className="peer-select"
              data-peer={peer.name}
              aria-hidden="true"
              style={
                {
                  left: item.x,
                  top: item.y,
                  width: item.w,
                  height: item.h,
                  "--peer": peer.color,
                } as React.CSSProperties
              }
            >
              {n === 0 && <span className="peer-label">{peer.name}</span>}
            </div>,
          ];
        });
      })}
      {Object.entries(cursors).flatMap(([who, point]) => {
        const peer = byId.get(who);
        if (!peer) return [];
        return [
          <div
            key={who}
            className="peer-cursor"
            role="img"
            aria-label={`Curseur de ${peer.name}`}
            style={
              {
                left: point.x,
                top: point.y,
                "--peer": peer.color,
              } as React.CSSProperties
            }
          >
            <svg viewBox="0 0 18 18" aria-hidden="true">
              <path d="M1 1 L1 15 L5 11 L8 17 L10.5 16 L7.5 10 L13 10 Z" />
            </svg>
            <span className="peer-label">{peer.name}</span>
          </div>,
        ];
      })}
    </>
  );
}
