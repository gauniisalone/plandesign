/** KAN-91 : les liens entre cartes, dessinés sur l'espace de travail (maquette « carte
 *  reliée » validée par augus). Courbes horizontales de carte à carte, flèche vers la cible,
 *  pastille d'icône au milieu (cible du survol et du clic), « auto » quand PlanDesign l'a posé.
 *  Survol : la phrase tirée de ce que la génération lira (serveur). Clic : la barre avec
 *  « Couper ». Poignée « + » au bord d'une carte : tirer un lien, les cibles valides restent
 *  vives, une cible refusée est expliquée. Trois couleurs, chacune avec son icône (pas la
 *  couleur seule). */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  boardApi,
  type Board,
  type BoardItem,
  type BoardLink,
  type LinkKind,
  type LinkSummary,
} from "../api/client";
import {
  canSource,
  cardRole,
  cardTitle,
  linkEffect,
  linkKind,
  linkRefusal,
  LINK_NAMES,
} from "./links";
import { route, wireEnds, type Point, type WireEnds } from "./wires";

const KIND_CLASS: Record<LinkKind, string> = {
  feeds_plan: "wire-feeds",
  guides_style: "wire-style",
  renders_view: "wire-view",
};

/** Le fil qu'on tire, de la poignée (bord droit de la source) au pointeur. */
function drawing(source: BoardItem, to: Point): string {
  const a = { x: source.x + source.w, y: source.y + source.h / 2 };
  const dx = Math.max(60, Math.abs(to.x - a.x) / 2);
  return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${to.x - dx} ${to.y}, ${to.x} ${to.y}`;
}

/** Le fil d'un lien seul (fantôme d'un lien coupé), ports au milieu des côtés. */
function single(l: BoardLink, s: BoardItem, t: BoardItem): WireEnds {
  return wireEnds([{ id: l.id, kind: l.kind, s, t }]).get(l.id)!;
}

function Glyph({ kind }: { kind: LinkKind }) {
  // Appareil (photo → plan), cube (plan → rendu), palette (inspiration → rendu).
  if (kind === "feeds_plan")
    return (
      <g>
        <rect x={-6} y={-4} width={12} height={9} rx={2} />
        <circle cx={0} cy={0.5} r={2.6} />
        <rect x={-2.5} y={-6} width={5} height={2} rx={1} />
      </g>
    );
  if (kind === "renders_view")
    return (
      <g>
        <path d="M0 -6 L6 -3 L6 4 L0 7 L-6 4 L-6 -3 Z" />
        <path d="M-6 -3 L0 0 L6 -3 M0 0 L0 7" />
      </g>
    );
  return (
    <g>
      <path d="M0 -6 C6 -6 7 -1 5 1 C3 3 1 1 0 3 C-1 6 -6 5 -6 0 C-6 -4 -3 -6 0 -6 Z" />
      <circle cx={-2.5} cy={-2} r={1} />
      <circle cx={2} cy={-3} r={1} />
    </g>
  );
}

export interface WireDrag {
  source: BoardItem;
  to: Point;
  target: BoardItem | null;
  refused: string | null;
}

/** Le calque des liens, en coordonnées de l'espace de travail (il suit la vue). */
export function WireLayer({
  projectId,
  board,
  items,
  zoom,
  readOnly,
  selected,
  ghosts,
  drag,
  onSelect,
  onCut,
  onHandleDown,
}: {
  projectId: string;
  board: Board;
  /** Les éléments tels qu'ils sont montrés (pendant un déplacement, les fils suivent). */
  items: BoardItem[];
  zoom: number;
  readOnly: boolean;
  selected: string | null;
  /** Liens coupés à l'instant : pointillé fantôme marqué de ciseaux. */
  ghosts: BoardLink[];
  drag: WireDrag | null;
  onSelect: (id: string | null) => void;
  onCut: (link: BoardLink) => void;
  onHandleDown: (e: React.PointerEvent, source: BoardItem) => void;
}) {
  const byId = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);
  const [hover, setHover] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<Record<string, LinkSummary>>({});
  const revision = board.revision;
  const asked = useRef(new Map<string, number>());
  // La phrase de survol vient du serveur (generation_inputs) ; relue à chaque révision.
  useEffect(() => {
    const id = hover ?? selected;
    if (!id || asked.current.get(id) === revision) return;
    asked.current.set(id, revision);
    boardApi.link(projectId, id).then(
      (s) => setSummaries((all) => ({ ...all, [id]: s })),
      () => undefined,
    );
  }, [hover, selected, projectId, revision]);

  const scale = 1 / Math.max(zoom, 0.05);
  const cards = items.filter((i) => cardRole(i) !== null);
  const links = (board.links ?? [])
    .map((l) => ({ l, s: byId.get(l.source), t: byId.get(l.target) }))
    .filter((x): x is { l: BoardLink; s: BoardItem; t: BoardItem } =>
      Boolean(x.s && x.t),
    );
  // Retours de la MEP 14 : un port d'entrée par sorte de lien, ports répartis, fils qui ne
  // passent jamais sous une carte, pastille jamais sur une carte (wires.ts).
  const ends = wireEnds(
    links.map(({ l, s, t }) => ({ id: l.id, kind: l.kind, s, t })),
  );
  // La pastille (rayon 15, « auto » dessous) garde sa taille à l'écran : marge et couloirs
  // grandissent au dézoom.
  const routeOf = (_id: string, e: WireEnds, s: BoardItem, t: BoardItem) =>
    route(e, cards, {
      clearance: 20 * Math.max(1, scale),
      gap: 48 * Math.max(1, scale),
      skip: [s.id, t.id],
    });
  const routes = new Map(
    links.map(({ l, s, t }) => [l.id, routeOf(l.id, ends.get(l.id)!, s, t)]),
  );
  const sources = readOnly ? [] : items.filter(canSource);
  const shownId = selected ?? hover;
  const shown = links.find((x) => x.l.id === shownId);
  const summary = shown ? summaries[shown.l.id] : undefined;

  return (
    <>
      <svg className="board-wires" aria-hidden={false}>
        <defs>
          {(["wire-feeds", "wire-style", "wire-view"] as const).map((c) => (
            <marker
              key={c}
              id={`${c}-arrow`}
              className={c}
              viewBox="0 0 10 10"
              refX={9}
              refY={5}
              markerWidth={8}
              markerHeight={8}
              orient="auto-start-reverse"
            >
              <path d="M0 0 L10 5 L0 10 Z" className="board-wire-head" />
            </marker>
          ))}
        </defs>
        {ghosts.map((g) => {
          const s = byId.get(g.source);
          const t = byId.get(g.target);
          if (!s || !t) return null;
          const r = routeOf(g.id, single(g, s, t), s, t);
          const m = r.mid;
          return (
            <g
              key={`ghost-${g.id}`}
              className={`board-wire-ghost ${KIND_CLASS[g.kind]}`}
            >
              <path d={r.d} />
              <text
                x={m.x}
                y={m.y}
                fontSize={16 * scale}
                textAnchor="middle"
                dominantBaseline="central"
              >
                ✂
              </text>
            </g>
          );
        })}
        {links.map(({ l, s, t }) => {
          const a = ends.get(l.id)!.a;
          const { d, mid: m, room } = routes.get(l.id)!;
          // La pastille (et « auto » dessous, ≈ 34 unités) tient dans la place libre : jamais
          // par-dessus une carte, quitte à se faire plus petite entre deux cartes serrées.
          const badge = Math.min(scale, room / 34);
          const cls = KIND_CLASS[l.kind];
          return (
            <g
              key={l.id}
              className={`board-wire ${cls}${l.id === selected ? " selected" : ""}`}
              data-link={l.id}
              data-kind={l.kind}
              data-entry={ends.get(l.id)!.bSide}
            >
              <path d={d} markerEnd={`url(#${cls}-arrow)`} />
              <circle
                cx={a.x}
                cy={a.y}
                r={5 * scale}
                className="board-wire-start"
              />
              <g
                className="board-wire-badge"
                transform={`translate(${m.x} ${m.y}) scale(${badge})`}
                role="button"
                tabIndex={0}
                aria-label={`${LINK_NAMES[l.kind]} : ${cardTitle(s)} → ${cardTitle(t)}`}
                onPointerEnter={() => setHover(l.id)}
                onPointerLeave={() => setHover((h) => (h === l.id ? null : h))}
                onPointerDown={(e) => {
                  e.stopPropagation();
                  onSelect(l.id === selected ? null : l.id);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") onSelect(l.id);
                }}
              >
                <circle r={15} />
                <Glyph kind={l.kind} />
                {l.auto && (
                  <text y={27} textAnchor="middle" className="board-wire-auto">
                    auto
                  </text>
                )}
              </g>
            </g>
          );
        })}
        {drag && (
          <g className={`board-wire-drawing${drag.refused ? " refused" : ""}`}>
            <path d={drawing(drag.source, drag.to)} />
          </g>
        )}
        {drag &&
          items
            .filter((i) => cardRole(i) && i.id !== drag.source.id)
            .map((i) => {
              const ok =
                linkKind(drag.source, i) !== null &&
                linkRefusal(board, drag.source, i) === null;
              return (
                <rect
                  key={`target-${i.id}`}
                  className={ok ? "board-wire-target" : "board-wire-dim"}
                  x={i.x}
                  y={i.y}
                  width={i.w}
                  height={i.h}
                  rx={12}
                />
              );
            })}
      </svg>
      {sources.map((s) => (
        <button
          key={`handle-${s.id}`}
          type="button"
          className="board-wire-handle"
          // Sous le départ des fils (milieu du bord droit) : la poignée ne les couvre pas.
          style={{
            left: s.x + s.w,
            top: s.y + s.h / 2 + 34 * scale,
            transform: `translate(-50%, -50%) scale(${scale})`,
          }}
          aria-label={`Tirer un lien depuis ${cardTitle(s)}`}
          title="Tirer un lien"
          onPointerDown={(e) => onHandleDown(e, s)}
        >
          +
        </button>
      ))}
      {drag && (
        <div
          className={`board-wire-bubble${drag.refused ? " refused" : ""}`}
          role="status"
          style={{
            left: drag.to.x,
            top: drag.to.y,
            transform: `translate(12px, 12px) scale(${scale})`,
          }}
        >
          {drag.refused ??
            (drag.target
              ? linkEffect(drag.source, drag.target)
              : "Relâchez sur une carte pour la relier")}
        </div>
      )}
      {shown && !drag && (
        <div
          className={`board-wire-bubble${selected === shown.l.id ? " bar" : ""}`}
          role={selected === shown.l.id ? "toolbar" : "tooltip"}
          aria-label={selected === shown.l.id ? "Lien choisi" : undefined}
          style={(() => {
            const m = routes.get(shown.l.id)!.mid;
            return {
              left: m.x,
              top: m.y,
              transform: `translate(-50%, 24px) scale(${scale})`,
              transformOrigin: "top center",
            };
          })()}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <strong className="board-wire-type">
            {LINK_NAMES[shown.l.kind]}
          </strong>
          <span className="board-wire-cards">
            {cardTitle(shown.s)} → {cardTitle(shown.t)}
          </span>
          <span className="board-wire-text">
            {summary?.sentence ?? "Lecture de ce que la génération lira…"}
          </span>
          <span className="board-wire-meta">
            {shown.l.auto ? "Créé automatiquement" : "Tiré à la main"}
            {selected === shown.l.id
              ? ""
              : " · cliquer pour modifier ou couper"}{" "}
            · {summary?.rights ?? "modifiable par tous les éditeurs du projet"}
          </span>
          {selected === shown.l.id && (
            <span className="board-wire-actions">
              {!readOnly && (
                <button
                  type="button"
                  className="danger"
                  onClick={() => onCut(shown.l)}
                  title="Couper ce lien (Suppr)"
                >
                  Couper
                </button>
              )}
              <button type="button" onClick={() => onSelect(null)}>
                Fermer
              </button>
            </span>
          )}
        </div>
      )}
    </>
  );
}

/** Le bandeau unique du zéro geste (une fois par projet et par navigateur). */
export function WireBanner({
  projectId,
  board,
}: {
  projectId: string;
  board: Board;
}) {
  const key = `pd-links-banner:${projectId}`;
  const [seen, setSeen] = useState(() => {
    try {
      return window.localStorage.getItem(key) === "1";
    } catch {
      return false;
    }
  });
  if (seen || !(board.links ?? []).some((l) => l.auto)) return null;
  return (
    <div className="board-wire-banner" role="status">
      <span>
        PlanDesign a relié vos photos aux plans et vos inspirations aux rendus.
        Tirez une poignée pour changer un lien.
      </span>
      <button
        type="button"
        onClick={() => {
          try {
            window.localStorage.setItem(key, "1");
          } catch {
            /* stockage indisponible : le bandeau reviendra, rien de plus */
          }
          setSeen(true);
        }}
      >
        Compris
      </button>
    </div>
  );
}

/** Un bandeau avec « Annuler » (lien coupé, image déposée) ; il se retire seul. */
export function WireToast({
  text,
  onUndo,
  onClose,
  ms = 6000,
}: {
  text: string;
  onUndo?: () => void;
  onClose: () => void;
  ms?: number;
}) {
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const t = window.setTimeout(() => close.current(), ms);
    return () => window.clearTimeout(t);
  }, [text, ms]);
  return (
    <div className="board-wire-toast" role="status" aria-live="polite">
      <span>{text}</span>
      {onUndo && (
        <button type="button" onClick={onUndo}>
          Annuler
        </button>
      )}
    </div>
  );
}
