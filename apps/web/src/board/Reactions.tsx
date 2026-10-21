import { useEffect, useMemo } from "react";
import { REACTIONS, type BoardItem, type ReactionPill } from "../api/client";
import { IconReact } from "../ui/icons";
import type { ReactionsController } from "./useReactions";

/** KAN-78 (maquette validée le 21 oct.) : sous chaque élément, une pastille par emoji avec son
 *  nombre ; un clic ajoute ou retire ma réaction (la mienne mise en évidence), le survol dit
 *  qui a réagi. Au survol de l'élément (ou quand il est choisi), un bouton « Réagir » ouvre
 *  le sélecteur des six réactions. Dans le monde de l'espace de travail, taille constante à
 *  tout zoom (comme les épingles des commentaires) ; les pastilles suivent l'élément. */

const GAP = 8;

function pillLabel(p: ReactionPill): string {
  return `${p.emoji} ${p.count} : ${p.names.join(", ")}`;
}

export function ReactionRows({
  r,
  items,
  hovered,
  selectedId,
  onEnter,
  onLeave,
}: {
  r: ReactionsController;
  items: BoardItem[];
  hovered: string | null;
  selectedId: string | null;
  onEnter: (id: string) => void;
  onLeave: () => void;
}) {
  const byItem = useMemo(() => {
    const map = new Map<string, ReactionPill[]>();
    for (const p of r.pills) {
      const list = map.get(p.item_id) ?? [];
      list.push(p);
      map.set(p.item_id, list);
    }
    return map;
  }, [r.pills]);

  // Échap ou un clic ailleurs ferme le sélecteur.
  const open = r.pickerFor;
  const close = r.openPicker;
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(null);
    };
    const down = (e: PointerEvent) => {
      const el = e.target as HTMLElement | null;
      if (
        !el?.closest(".board-react-picker, .board-react-add, .board-react-open")
      )
        close(null);
    };
    window.addEventListener("keydown", key);
    window.addEventListener("pointerdown", down, true);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("pointerdown", down, true);
    };
  }, [open, close]);

  return (
    <>
      {items.map((item) => {
        const pills = byItem.get(item.id) ?? [];
        const showAdd =
          r.canReact &&
          (hovered === item.id || selectedId === item.id || open === item.id);
        if (pills.length === 0 && !showAdd) return null;
        return (
          <div
            key={item.id}
            className="board-reactions"
            role="group"
            aria-label={`Réactions ${item.text || item.kind}`.trim()}
            data-item={item.id}
            style={{ left: item.x, top: item.y + item.h + GAP }}
            onPointerDown={(e) => e.stopPropagation()}
            onPointerEnter={() => onEnter(item.id)}
            onPointerLeave={onLeave}
          >
            {pills.map((p) => (
              <button
                key={p.key}
                type="button"
                className={`board-reaction${p.mine ? " mine" : ""}`}
                aria-pressed={p.mine}
                aria-disabled={!r.canReact || undefined}
                aria-label={pillLabel(p)}
                title={p.names.join(", ")}
                onClick={() => {
                  if (r.canReact) void r.toggle(item.id, p.key);
                }}
              >
                <span className="board-reaction-emoji" aria-hidden="true">
                  {p.emoji}
                </span>
                <span className="board-reaction-count" aria-hidden="true">
                  {p.count}
                </span>
              </button>
            ))}
            {showAdd && (
              <button
                type="button"
                className="board-reaction board-react-add"
                aria-label="Réagir"
                aria-expanded={open === item.id}
                title="Réagir"
                onClick={() => close(open === item.id ? null : item.id)}
              >
                <IconReact aria-hidden="true" />
              </button>
            )}
            {open === item.id && (
              <ReactionPicker r={r} item={item} pills={pills} />
            )}
          </div>
        );
      })}
    </>
  );
}

function ReactionPicker({
  r,
  item,
  pills,
}: {
  r: ReactionsController;
  item: BoardItem;
  pills: ReactionPill[];
}) {
  const mine = new Set(pills.filter((p) => p.mine).map((p) => p.key));
  return (
    <div
      className="board-react-picker"
      role="menu"
      aria-label="Choisir une réaction"
    >
      {REACTIONS.map((x) => (
        <button
          key={x.key}
          type="button"
          role="menuitemcheckbox"
          aria-checked={mine.has(x.key)}
          aria-label={`${x.emoji} ${x.label}`}
          title={x.label}
          className={mine.has(x.key) ? "on" : undefined}
          onClick={() => {
            void r.toggle(item.id, x.key);
            r.openPicker(null);
          }}
        >
          {x.emoji}
        </button>
      ))}
    </div>
  );
}
