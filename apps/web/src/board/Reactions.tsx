import { useEffect, useMemo } from "react";
import { REACTIONS, type BoardItem, type ReactionPill } from "../api/client";
import { IconReact } from "../ui/icons";
import { pillId } from "../state/reactionFx";
import { usePillFx, type ReactionsController } from "./useReactions";

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
  // Must have 10 : les pastilles passées à 0 restent un instant (elles se replient).
  const fx = usePillFx(r.pills);
  const byItem = useMemo(() => {
    const map = new Map<string, { pill: ReactionPill; leaving: boolean }[]>();
    const add = (pill: ReactionPill, leaving: boolean) => {
      const list = map.get(pill.item_id) ?? [];
      list.push({ pill, leaving });
      map.set(pill.item_id, list);
    };
    for (const p of r.pills) add(p, false);
    for (const p of fx.leaving) add(p, true);
    const order = REACTIONS.map((x) => x.key);
    for (const list of map.values())
      list.sort(
        (a, b) => order.indexOf(a.pill.key) - order.indexOf(b.pill.key),
      );
    return map;
  }, [r.pills, fx.leaving]);

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
        const entries = byItem.get(item.id) ?? [];
        const pills = entries.filter((e) => !e.leaving).map((e) => e.pill);
        const showAdd =
          r.canReact &&
          (hovered === item.id || selectedId === item.id || open === item.id);
        if (entries.length === 0 && !showAdd) return null;
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
            {entries.map(({ pill: p, leaving }) => {
              const id = pillId(p);
              if (leaving)
                return (
                  <span
                    key={`${p.key}-leaving`}
                    className="board-reaction leaving"
                    aria-hidden="true"
                  >
                    <span className="board-reaction-emoji">{p.emoji}</span>
                    <span className="board-reaction-count">0</span>
                  </span>
                );
              const motion = fx.pops.includes(id)
                ? " pop"
                : fx.faded.includes(id)
                  ? " faded"
                  : "";
              return (
                <button
                  key={p.key}
                  type="button"
                  className={`board-reaction${p.mine ? " mine" : ""}${motion}`}
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
                  <span
                    // Une nouvelle clé à chaque nombre : le chiffre glisse vers le nouveau.
                    key={fx.slides.includes(id) ? `n${p.count}` : "n"}
                    className={`board-reaction-count${fx.slides.includes(id) ? " slide" : ""}`}
                    aria-hidden="true"
                  >
                    {p.count}
                  </span>
                  {fx.floats.includes(id) && (
                    <span className="board-reaction-float" aria-hidden="true">
                      {p.emoji}
                    </span>
                  )}
                </button>
              );
            })}
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
