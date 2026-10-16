import { type CSSProperties, type ReactNode, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  renderApi,
  type RenderEntry,
  type RenderVariant,
  type ViewGroup,
} from "../api/client";
import { firstVariant, titleOf, VARIANT_LABELS, variantsOf } from "../gallery";
import { formatDate } from "../model";
import {
  IconClose,
  IconCompare,
  IconDownload,
  IconExpand,
  IconPencil,
  IconStar,
  IconTrash,
} from "../ui/icons";

/** Galerie des rendus du projet (US2) : groupée par vue, la plus récente d'abord ; pour
 *  chaque rendu, ses images (Blender, référence, inspiration, catalogue) avec la bascule ;
 *  favoris (étoile) et filtre, renommer, supprimer avec « Annuler » quelques secondes,
 *  télécharger une image ou les favoris en zip, comparer deux images (côte à côte ou
 *  curseur avant/après). Elle s'agrandit en plein écran. */

const VARIANT_TIPS: Record<RenderVariant, string> = {
  blender: "Image Blender brute : la géométrie des cotes",
  finished: "Référence : rendu Blender et sa finition légère (image générée par IA)",
  inspiration:
    "Inspiration : image refaite par un modèle d'images, cotes non garanties",
  catalogue:
    "Version catalogue : image refaite librement, matières et cotes non garanties",
};

const GROUPS: { id: ViewGroup; label: string }[] = [
  { id: "overall", label: "Vues d'ensemble" },
  { id: "zone", label: "Zones" },
  { id: "detail", label: "Détails" },
  { id: "hand", label: "Points de vue à la main" },
  { id: "photo", label: "Depuis vos photos" },
  { id: "variant", label: "Variantes d'aménagement" },
];

const VIEW_LABELS: Record<ViewGroup, string> = {
  overall: "vue d'ensemble",
  zone: "zone",
  detail: "détail",
  hand: "point de vue à la main",
  photo: "caméra de photo",
  variant: "variante d'aménagement",
};

const UNDO_MS = 8000;

function fileName(entry: RenderEntry, variant: RenderVariant): string {
  const base = titleOf(entry)
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^A-Za-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .toLowerCase();
  return `${base || "rendu"}-${VARIANT_LABELS[variant].toLowerCase()}.png`;
}

interface Pick {
  runId: string;
  variant: RenderVariant;
}

interface Props {
  projectId: string;
  renders: RenderEntry[];
  busy: boolean;
  /** Relit la liste après un changement (favori, titre, suppression, annulation). */
  onChanged: () => void;
  onError?: (message: string) => void;
  /** Ce qui suit les actions d'un rendu (la finition IA s'il n'en a pas). */
  extra?: (entry: RenderEntry) => ReactNode;
}

export function RenderGallery({
  projectId,
  renders,
  busy,
  onChanged,
  onError,
  extra,
}: Props) {
  const [filter, setFilter] = useState<"all" | "fav">("all");
  const [expanded, setExpanded] = useState(false);
  const [picks, setPicks] = useState<Pick[]>([]);
  const [comparing, setComparing] = useState(false);
  const [removed, setRemoved] = useState<{ entry: RenderEntry } | null>(null);
  const undoTimer = useRef<number | null>(null);
  // Mis à la corbeille mais pas encore relu : caché tout de suite.
  const [hidden, setHidden] = useState<string[]>([]);
  // Retours optimistes : l'étoile et le titre suivent le clic, la liste relue confirme.
  // Ils valent pour la liste reçue : une liste relue (nouvelle référence) les remplace.
  type Local = Record<
    string,
    { favorites?: RenderVariant[]; title?: string | null }
  >;
  const [localState, setLocalState] = useState<{
    base: RenderEntry[];
    map: Local;
  }>({ base: renders, map: {} });
  const local = localState.base === renders ? localState.map : {};
  const setLocal = (fn: (map: Local) => Local) =>
    setLocalState((s) => ({
      base: renders,
      map: fn(s.base === renders ? s.map : {}),
    }));

  const [error, setError] = useState<string | null>(null);
  const report = (e: unknown) => {
    const message = e instanceof Error ? e.message : String(e);
    setError(message);
    onError?.(message);
  };

  useEffect(
    () => () => {
      if (undoTimer.current !== null) window.clearTimeout(undoTimer.current);
    },
    [],
  );
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !comparing) setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded, comparing]);


  const entries = renders
    .filter((r) => !hidden.includes(r.run_id))
    .map((r) => ({ ...r, ...local[r.run_id] }));
  const favoriteCount = entries.reduce(
    (n, e) => n + (e.favorites?.length ?? 0),
    0,
  );
  const shown =
    filter === "fav"
      ? entries.filter((e) => (e.favorites?.length ?? 0) > 0)
      : entries;

  const toggleFavorite = (entry: RenderEntry, variant: RenderVariant) => {
    const current = entry.favorites ?? [];
    const next = current.includes(variant)
      ? current.filter((v) => v !== variant)
      : [...current, variant];
    setLocal((l) => ({
      ...l,
      [entry.run_id]: { ...l[entry.run_id], favorites: next },
    }));
    renderApi
      .update(projectId, entry.run_id, { favorites: next })
      .then(onChanged, (e: unknown) => {
        report(e);
        onChanged();
      });
  };

  const rename = (entry: RenderEntry, title: string) => {
    setLocal((l) => ({
      ...l,
      [entry.run_id]: { ...l[entry.run_id], title: title.trim() || null },
    }));
    renderApi
      .update(projectId, entry.run_id, { title })
      .then(onChanged, (e: unknown) => {
        report(e);
        onChanged();
      });
  };

  const remove = async (entry: RenderEntry) => {
    try {
      await renderApi.remove(projectId, entry.run_id);
    } catch (e) {
      report(e);
      return;
    }
    setHidden((h) => [...h, entry.run_id]);
    setPicks((p) => p.filter((x) => x.runId !== entry.run_id));
    setRemoved({ entry });
    if (undoTimer.current !== null) window.clearTimeout(undoTimer.current);
    undoTimer.current = window.setTimeout(() => setRemoved(null), UNDO_MS);
    onChanged();
  };

  const undo = async () => {
    if (!removed) return;
    const { entry } = removed;
    if (undoTimer.current !== null) window.clearTimeout(undoTimer.current);
    setRemoved(null);
    try {
      await renderApi.restore(projectId, entry.run_id);
    } catch (e) {
      report(e);
    }
    setHidden((h) => h.filter((id) => id !== entry.run_id));
    onChanged();
  };

  const togglePick = (entry: RenderEntry, variant: RenderVariant) =>
    setPicks((p) => {
      const at = p.findIndex(
        (x) => x.runId === entry.run_id && x.variant === variant,
      );
      if (at >= 0) return p.filter((_, i) => i !== at);
      return [...p, { runId: entry.run_id, variant }].slice(-2);
    });

  const pickedEntries = picks
    .map((p) => ({ pick: p, entry: entries.find((e) => e.run_id === p.runId) }))
    .filter((x): x is { pick: Pick; entry: RenderEntry } => Boolean(x.entry));

  const content = (
    <section
      className={`gallery2${expanded ? " expanded" : ""}`}
      aria-label="Galerie des rendus"
      role={expanded ? "dialog" : undefined}
      aria-modal={expanded ? true : undefined}
    >
      <div className="gallery2-head">
        <h2>Galerie</h2>
        <span className="muted gallery2-count">
          {entries.length} rendu{entries.length > 1 ? "s" : ""}
        </span>
        <button
          type="button"
          className="icon"
          onClick={() => setExpanded((x) => !x)}
          aria-label={expanded ? "Réduire la galerie" : "Agrandir la galerie"}
          title={expanded ? "Réduire (Échap)" : "Agrandir la galerie"}
        >
          {expanded ? <IconClose /> : <IconExpand />}
        </button>
      </div>
      {entries.length > 0 && (
        <div className="gallery2-tools">
          <div className="seg" role="group" aria-label="Filtrer la galerie">
            <button
              type="button"
              aria-pressed={filter === "all"}
              onClick={() => setFilter("all")}
            >
              Tous
            </button>
            <button
              type="button"
              aria-pressed={filter === "fav"}
              onClick={() => setFilter("fav")}
            >
              <IconStar filled /> Favoris ({favoriteCount})
            </button>
          </div>
          {favoriteCount > 0 ? (
            <a
              className="btn"
              href={renderApi.favoritesZipUrl(projectId)}
              download
              title="Toutes les images en favori, en un fichier zip"
            >
              <IconDownload /> Favoris en zip
            </a>
          ) : (
            <button
              type="button"
              disabled
              title="Marquez d'abord des images d'une étoile"
            >
              <IconDownload /> Favoris en zip
            </button>
          )}
          <button
            type="button"
            className={picks.length === 2 ? "primary" : undefined}
            disabled={picks.length !== 2}
            onClick={() => setComparing(true)}
            title="Cochez « Comparer » sur deux images, puis ouvrez la comparaison"
          >
            <IconCompare /> Comparer ({picks.length}/2)
          </button>
        </div>
      )}
      {error && (
        <p className="warn" role="alert">
          {error}{" "}
          <button type="button" className="link" onClick={() => setError(null)}>
            fermer
          </button>
        </p>
      )}      {entries.length === 0 && (
        <p className="muted">Aucun rendu pour l'instant.</p>
      )}
      {entries.length > 0 && shown.length === 0 && (
        <p className="muted">
          Aucun favori : cliquez sur l'étoile d'une image pour la garder.
        </p>
      )}
      <div className="gallery2-groups">
      {GROUPS.map((group) => {
        const items = shown.filter(
          (e) => (e.view_group ?? "hand") === group.id,
        );
        if (items.length === 0) return null;
        return (
          <section
            key={group.id}
            className="gallery2-group"
            aria-label={group.label}
            style={{ "--n": Math.min(items.length, 4) } as CSSProperties}
          >
            <h3>
              {group.label} <span className="muted">· {items.length}</span>
            </h3>
            <ul className="gallery2-grid">
              {items.map((entry) => (
                <GalleryCard
                  key={entry.run_id}
                  projectId={projectId}
                  entry={entry}
                  busy={busy}
                  favoritesOnly={filter === "fav"}
                  picks={picks}
                  onFavorite={(v) => toggleFavorite(entry, v)}
                  onRename={(t) => rename(entry, t)}
                  onRemove={() => void remove(entry)}
                  onPick={(v) => togglePick(entry, v)}
                  extra={extra?.(entry)}
                />
              ))}
            </ul>
          </section>
        );
      })}
      </div>
    </section>
  );
  // Le toast et la comparaison passent au-dessus de tout (hors du panneau qui défile) ;
  // agrandie, la galerie couvre la fenêtre.
  return (
    <>
      {expanded ? createPortal(content, document.body) : content}
      {removed &&
        createPortal(
          <div className="gallery2-toast" role="status">
            <span>Rendu « {titleOf(removed.entry)} » supprimé.</span>
            <button type="button" className="link" onClick={() => void undo()}>
              Annuler
            </button>
          </div>,
          document.body,
        )}
      {comparing &&
        pickedEntries.length === 2 &&
        createPortal(
          <CompareDialog
            projectId={projectId}
            items={pickedEntries}
            onClose={() => setComparing(false)}
          />,
          document.body,
        )}
    </>
  );
}

function GalleryCard({
  projectId,
  entry,
  busy,
  favoritesOnly,
  picks,
  onFavorite,
  onRename,
  onRemove,
  onPick,
  extra,
}: {
  projectId: string;
  entry: RenderEntry;
  busy: boolean;
  favoritesOnly: boolean;
  picks: Pick[];
  onFavorite: (variant: RenderVariant) => void;
  onRename: (title: string) => void;
  onRemove: () => void;
  onPick: (variant: RenderVariant) => void;
  extra?: ReactNode;
}) {
  const available = variantsOf(entry);
  const [chosen, setChosen] = useState<RenderVariant>(() =>
    firstVariant(entry),
  );
  const variant = available.includes(chosen) ? chosen : available[0]!;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState(false);
  const favorites = entry.favorites ?? [];
  const favorite = favorites.includes(variant);
  const picked = picks.some(
    (p) => p.runId === entry.run_id && p.variant === variant,
  );
  const url = renderApi.variantUrl(projectId, entry, variant);
  const title = titleOf(entry);
  const tabs = favoritesOnly
    ? available.filter((v) => favorites.includes(v))
    : available;

  const commit = () => {
    setEditing(false);
    if (draft.trim() !== (entry.title ?? "")) onRename(draft);
  };

  return (
    <li className="gallery2-card">
      <div className="gallery2-media">
        <a href={url} target="_blank" rel="noreferrer" title="Ouvrir en grand">
          <img
            src={url}
            alt={`${title} · ${VARIANT_LABELS[variant]}`}
            loading="lazy"
          />
        </a>
        <button
          type="button"
          className={`gallery2-star${favorite ? " on" : ""}`}
          aria-pressed={favorite}
          aria-label={`Favori : ${title} (${VARIANT_LABELS[variant]})`}
          title={favorite ? "Retirer des favoris" : "Garder en favori"}
          onClick={() => onFavorite(variant)}
        >
          <IconStar filled={favorite} />
        </button>
        {variant !== "blender" && (
          <span
            className="badge generated gallery2-badge"
            title={VARIANT_TIPS[variant]}
          >
            {variant === "finished" ? "Finition IA" : "Image générée (IA)"}
          </span>
        )}
      </div>
      {tabs.length > 1 && (
        <div
          className="ab-toggle gallery2-variants"
          role="group"
          aria-label={`Images de ${title}`}
        >
          {tabs.map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={v === variant}
              onClick={() => setChosen(v)}
              title={VARIANT_TIPS[v]}
            >
              {VARIANT_LABELS[v]}
              {favorites.includes(v) && (
                <span className="gallery2-mini-star" aria-label="favori">
                  ★
                </span>
              )}
            </button>
          ))}
        </div>
      )}
      <div className="gallery2-title">
        {editing ? (
          <input
            aria-label={`Nouveau titre de « ${title} »`}
            value={draft}
            autoFocus
            maxLength={80}
            placeholder={entry.camera_name}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") commit();
              if (e.key === "Escape") setEditing(false);
            }}
          />
        ) : (
          <>
            <strong title={title}>{title}</strong>
            <button
              type="button"
              className="icon"
              aria-label={`Renommer « ${title} »`}
              title="Renommer (titre court ; vide = nom du point de vue)"
              onClick={() => {
                setDraft(entry.title ?? "");
                setEditing(true);
              }}
            >
              <IconPencil />
            </button>
          </>
        )}
      </div>
      <p className="muted gallery2-meta">
        {formatDate(entry.created_at)}
        {entry.ambiance ? ` · ${entry.ambiance}` : ""} ·{" "}
        {VIEW_LABELS[entry.view_group ?? "hand"]}
        {entry.title ? ` · ${entry.camera_name}` : ""}
        {entry.section ? ` · coupe ${entry.section}` : ""}
      </p>
      <p className="muted gallery2-tech">
        {entry.width_px} × {entry.height_px} · {entry.samples} éch. ·{" "}
        {entry.device}
        {entry.daylight
          ? ` · ${entry.daylight === "heure_doree" ? "heure dorée" : entry.daylight}`
          : ""}
        {entry.render_s != null ? ` · ${entry.render_s.toFixed(0)} s` : ""}
      </p>
      {confirming ? (
        <div className="gallery2-confirm" role="group" aria-label="Confirmer">
          <span>Supprimer ce rendu et ses images ?</span>
          <button
            type="button"
            className="danger"
            autoFocus
            onClick={() => {
              setConfirming(false);
              onRemove();
            }}
          >
            Supprimer
          </button>
          <button type="button" onClick={() => setConfirming(false)}>
            Garder
          </button>
        </div>
      ) : (
        <div className="gallery2-actions">
          <a
            href={url}
            download={fileName(entry, variant)}
            aria-label={`Télécharger ${title} (${VARIANT_LABELS[variant]})`}
            title="Télécharger cette image"
          >
            <IconDownload /> Télécharger
          </a>
          <button
            type="button"
            className="link"
            aria-pressed={picked}
            aria-label={`Comparer ${title} (${VARIANT_LABELS[variant]})`}
            onClick={() => onPick(variant)}
            title="Choisir cette image pour la comparer à une autre"
          >
            <IconCompare /> Comparer
          </button>
          <button
            type="button"
            className="link gallery2-delete"
            disabled={busy}
            aria-label={`Supprimer ${title}`}
            onClick={() => setConfirming(true)}
            title="Supprimer ce rendu (annulable quelques secondes)"
          >
            <IconTrash /> Supprimer
          </button>
        </div>
      )}
      {extra}
    </li>
  );
}

function CompareDialog({
  projectId,
  items,
  onClose,
}: {
  projectId: string;
  items: { pick: Pick; entry: RenderEntry }[];
  onClose: () => void;
}) {
  const [mode, setMode] = useState<"side" | "slider">("side");
  const [split, setSplit] = useState(50);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const [a, b] = items as [
    { pick: Pick; entry: RenderEntry },
    { pick: Pick; entry: RenderEntry },
  ];
  const label = (x: { pick: Pick; entry: RenderEntry }) =>
    `${titleOf(x.entry)} · ${VARIANT_LABELS[x.pick.variant]}`;
  const src = (x: { pick: Pick; entry: RenderEntry }) =>
    renderApi.variantUrl(projectId, x.entry, x.pick.variant);
  return (
    <div className="compare-backdrop" onClick={onClose}>
      <div
        className="compare-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Comparer deux images"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="compare-head">
          <h2>Comparer</h2>
          <div className="seg" role="group" aria-label="Mode de comparaison">
            <button
              type="button"
              aria-pressed={mode === "side"}
              onClick={() => setMode("side")}
            >
              Côte à côte
            </button>
            <button
              type="button"
              aria-pressed={mode === "slider"}
              onClick={() => setMode("slider")}
            >
              Avant / après
            </button>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="icon"
            aria-label="Fermer la comparaison"
            onClick={onClose}
          >
            <IconClose />
          </button>
        </div>
        {mode === "side" ? (
          <div className="compare-side">
            {[a, b].map((x) => (
              <figure key={`${x.pick.runId}-${x.pick.variant}`}>
                <img src={src(x)} alt={label(x)} />
                <figcaption>{label(x)}</figcaption>
              </figure>
            ))}
          </div>
        ) : (
          <div className="compare-slider">
            <div className="compare-fit">
            <div className="compare-stack">
              <img src={src(b)} alt={label(b)} />
              <img
                src={src(a)}
                alt={label(a)}
                className="compare-top"
                style={{ clipPath: `inset(0 ${100 - split}% 0 0)` }}
              />
              <span
                className="compare-line"
                style={{ left: `${split}%` }}
                aria-hidden="true"
              />
            </div>
            <input
              type="range"
              min={0}
              max={100}
              value={split}
              aria-label="Curseur avant/après"
              onChange={(e) => setSplit(Number(e.target.value))}
            />
            </div>
            <p className="muted compare-legend">
              À gauche : {label(a)} · à droite : {label(b)}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
