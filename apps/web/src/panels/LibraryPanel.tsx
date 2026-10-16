import { useEffect, useMemo, useState } from "react";
import {
  catalogApi,
  ROOM_LABELS,
  type Catalog,
  type CatalogAsset,
  type CatalogRoom,
} from "../api/client";
import { STAIR_CARDS, type StairCard } from "../model";
import {
  OPENING_CARDS,
  defaultSwing,
  openingSymbol,
  type OpeningCard,
} from "../editor2d/openingSymbol";

/** Vignette d'une carte d'ouverture (E5) : un bout de mur vu de dessus et le symbole
 *  normalisé, dessinés par la même fonction que le plan. */
function OpeningThumb({ card }: { card: OpeningCard }) {
  const L = Math.max(2.6, card.width * 3);
  const offset = (L - card.width) / 2;
  const half = 0.1;
  const k = 84 / L;
  const X = (x: number) => 6 + x * k;
  const Y = (y: number) => 40 - y * k;
  const o = {
    kind: card.kind,
    offset,
    width: card.width,
    swing: defaultSwing(card.kind),
    leaves: card.leaves,
    split: card.split,
  };
  const sym = openingSymbol(o, { x: 0, y: 0 }, { x: 1, y: 0 }, half);
  const glazed = ["window", "french_window", "sliding_window"].includes(
    card.kind,
  );
  return (
    <svg className="opening-thumb" viewBox="0 0 96 64" aria-hidden>
      <rect
        x={X(0)}
        y={Y(half)}
        width={offset * k}
        height={2 * half * k}
        className="wall"
      />
      <rect
        x={X(offset + card.width)}
        y={Y(half)}
        width={(L - offset - card.width) * k}
        height={2 * half * k}
        className="wall"
      />
      <rect
        x={X(offset)}
        y={Y(half)}
        width={card.width * k}
        height={2 * half * k}
        className={glazed ? "bay glazed" : "bay"}
      />
      {sym.lines.map((l, i) => (
        <polyline
          key={i}
          className={`sym ${l.style}`}
          points={l.points.map((p) => `${X(p.x)},${Y(p.y)}`).join(" ")}
        />
      ))}
      {sym.arcs.map((arc, i) => {
        const a0 = (arc.startDeg * Math.PI) / 180;
        const a1 = ((arc.startDeg + arc.extentDeg) * Math.PI) / 180;
        const r = arc.radius * k;
        return (
          <path
            key={`a${i}`}
            className="sym swing"
            d={`M ${X(arc.center.x + arc.radius * Math.cos(a0))} ${Y(arc.center.y + arc.radius * Math.sin(a0))} A ${r} ${r} 0 0 ${arc.extentDeg > 0 ? 0 : 1} ${X(arc.center.x + arc.radius * Math.cos(a1))} ${Y(arc.center.y + arc.radius * Math.sin(a1))}`}
          />
        );
      })}
    </svg>
  );
}

/** Recherche sans accents ni casse : « salle de bain » trouve « Salle de bain ». */
function fold(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** Volet « Meuble » : la bibliothèque en cartes (vignette, nom, pièce), filtrée par pièce et
 *  par recherche. Choisir une carte arme la pose : le clic suivant dans le plan pose le meuble
 *  (accroché au mur, règles en direct) ; Échap ou un second clic sur la carte annule. */
export function LibraryPanel({
  chosen,
  busy,
  onChoose,
  onChooseStairs,
  onChooseOpening,
  circulationToken = 0,
}: {
  chosen: string | null;
  busy: boolean;
  onChoose: (asset: CatalogAsset | null) => void;
  onChooseStairs?: (card: StairCard | null) => void;
  onChooseOpening?: (card: OpeningCard | null) => void;
  /** Change à chaque appel de l'outil Escalier : le volet montre la circulation. */
  circulationToken?: number;
}) {
  const [circulation, setCirculation] = useState(circulationToken > 0);
  const [openingsOnly, setOpeningsOnly] = useState(false);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [room, setRoom] = useState<CatalogRoom | null>(null);
  const [query, setQuery] = useState("");
  // Outil Escalier rappelé : la circulation revient au premier plan.
  const [shownToken, setShownToken] = useState(circulationToken);
  if (shownToken !== circulationToken) {
    setShownToken(circulationToken);
    setCirculation(true);
    setOpeningsOnly(false);
    setRoom(null);
  }
  useEffect(() => {
    let cancelled = false;
    catalogApi.list().then(
      (c) => {
        if (!cancelled) setCatalog(c);
      },
      () => {
        if (!cancelled)
          setCatalog({ version: 0, license_note: "", assets: [] });
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);
  const rooms = useMemo(
    () =>
      (Object.keys(ROOM_LABELS) as CatalogRoom[]).filter((r) =>
        catalog?.assets.some((a) => a.room === r),
      ),
    [catalog],
  );
  const shown = useMemo(() => {
    const words = fold(query).split(/\s+/).filter(Boolean);
    return (catalog?.assets ?? []).filter((a) => {
      if (room && a.room !== room) return false;
      const text = fold(
        `${a.label} ${ROOM_LABELS[a.room]} ${a.tags.join(" ")}`,
      );
      return words.every((w) => text.includes(w));
    });
  }, [catalog, room, query]);
  const openingCards = (() => {
    const words = fold(query).split(/\s+/).filter(Boolean);
    return OPENING_CARDS.filter((c) => {
      const text = fold(`${c.label} porte fenetre ouverture`);
      return words.every((w) => text.includes(w));
    });
  })();
  if (!catalog) return <p className="muted">Chargement des meubles…</p>;
  const picked =
    catalog.assets.find((a) => a.id === chosen) ??
    STAIR_CARDS.find((c) => `stairs:${c.kind}` === chosen) ??
    OPENING_CARDS.find((c) => `opening:${c.id}` === chosen) ??
    null;
  return (
    <section className="library" aria-label="Bibliothèque de meubles">
      <p className={`hint${picked ? " active" : ""}`} role="status">
        {picked
          ? `Cliquez sur le plan pour poser « ${picked.label} » · Échap pour annuler`
          : "Choisissez un meuble, puis cliquez sur le plan."}
      </p>
      <input
        type="search"
        placeholder="Rechercher un meuble"
        aria-label="Rechercher un meuble"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="rooms" role="group" aria-label="Pièce">
        <button
          type="button"
          aria-pressed={room === null && !circulation && !openingsOnly}
          onClick={() => {
            setRoom(null);
            setCirculation(false);
            setOpeningsOnly(false);
          }}
        >
          Toutes
        </button>
        {onChooseOpening && (
          <button
            type="button"
            aria-pressed={openingsOnly}
            onClick={() => {
              setOpeningsOnly(!openingsOnly);
              setCirculation(false);
            }}
          >
            Portes et fenêtres
          </button>
        )}
        {onChooseStairs && (
          <button
            type="button"
            aria-pressed={circulation}
            onClick={() => {
              setCirculation(!circulation);
              setOpeningsOnly(false);
            }}
          >
            Circulation
          </button>
        )}
        {rooms.map((r) => (
          <button
            key={r}
            type="button"
            aria-pressed={room === r}
            onClick={() => setRoom(room === r ? null : r)}
          >
            {ROOM_LABELS[r]}
          </button>
        ))}
      </div>
      {onChooseOpening &&
        !circulation &&
        (openingsOnly || !room) &&
        openingCards.length > 0 && (
          <div className="cards opening-cards" aria-label="Portes et fenêtres">
            {openingCards.map((c) => {
              const id = `opening:${c.id}`;
              return (
                <button
                  key={id}
                  type="button"
                  className="card"
                  data-asset={id}
                  aria-pressed={id === chosen}
                  disabled={busy}
                  title={`${c.label} : ${c.width.toFixed(2)} × ${c.height.toFixed(2)} m, posée d'un clic sur un mur, puis redimensionnable et retournable`}
                  onClick={() => onChooseOpening(id === chosen ? null : c)}
                >
                  <OpeningThumb card={c} />
                  <span className="name">{c.label}</span>
                  <span className="room">Portes et fenêtres</span>
                  <span className="size">
                    {(c.width * 100).toFixed(0)} × {(c.height * 100).toFixed(0)}{" "}
                    cm
                  </span>
                </button>
              );
            })}
          </div>
        )}
      {onChooseStairs &&
        !openingsOnly &&
        (circulation || (!room && !query)) && (
          <div className="cards stairs-cards" aria-label="Circulation">
            {STAIR_CARDS.map((c) => {
              const id = `stairs:${c.kind}`;
              return (
                <button
                  key={id}
                  type="button"
                  className="card"
                  data-asset={id}
                  aria-pressed={id === chosen}
                  disabled={busy}
                  title={`${c.label} : trémie, marches calculées (Blondel) et garde-corps`}
                  onClick={() => onChooseStairs(id === chosen ? null : c)}
                >
                  <span className={`stairs-icon ${c.kind}`} aria-hidden />
                  <span className="name">{c.label}</span>
                  <span className="room">Circulation</span>
                  <span className="size">
                    {(c.along * 100).toFixed(0)} × {(c.across * 100).toFixed(0)}{" "}
                    cm
                  </span>
                </button>
              );
            })}
          </div>
        )}
      {circulation || openingsOnly ? null : shown.length === 0 ? (
        <p className="empty">Aucun meuble ne correspond.</p>
      ) : (
        <div className="cards">
          {shown.map((a) => (
            <button
              key={a.id}
              type="button"
              className="card"
              data-asset={a.id}
              aria-pressed={a.id === chosen}
              disabled={busy}
              title={`${a.label} : ${a.width.default.toFixed(2)} × ${a.depth.default.toFixed(2)} × ${a.height.default.toFixed(2)} m${a.notes ? ` · ${a.notes}` : ""}`}
              onClick={() => onChoose(a.id === chosen ? null : a)}
            >
              {a.preview ? (
                <img
                  src={catalogApi.previewUrl(a.id)}
                  alt=""
                  loading="lazy"
                  draggable={false}
                />
              ) : (
                <span className="img-missing" aria-hidden />
              )}
              <span className="name">{a.label}</span>
              <span className="room">
                {a.tags.includes("couchage") ? "Couchage" : ROOM_LABELS[a.room]}
              </span>
              <span className="size">
                {(a.width.default * 100).toFixed(0)} ×{" "}
                {(a.depth.default * 100).toFixed(0)} cm
              </span>
            </button>
          ))}
        </div>
      )}
      <p className="credit">
        Modèles 3D : Kenney, Furniture Kit (CC0). {catalog.license_note}
      </p>
    </section>
  );
}
