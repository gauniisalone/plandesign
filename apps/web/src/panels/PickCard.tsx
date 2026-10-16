import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  catalogApi,
  renderApi,
  type Catalog,
  type CatalogAsset,
  type PickResult,
} from "../api/client";

const CARD_W = 250;

/** Image d'un rendu où l'on désigne un élément (P4b) : un clic demande au serveur ce qui se
 *  voit là, pose le contour de cet élément sur l'image (dessiné depuis la carte des
 *  éléments) et ouvre la carte de désignation près du clic. */
export function PickableImage({
  projectId,
  runId,
  url,
  alt,
  busy,
  onAction,
}: {
  projectId: string;
  runId: string;
  url: string;
  alt: string;
  busy: boolean;
  onAction: (action: PickAction) => void;
}) {
  const [picked, setPicked] = useState<{
    x: number;
    y: number;
    left: number;
    top: number;
    above: boolean;
    result: PickResult;
  } | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const close = useCallback(() => setPicked(null), []);
  const onClick = (e: React.MouseEvent<HTMLImageElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const x = Math.min(1, Math.max(0, px / rect.width));
    const y = Math.min(1, Math.max(0, py / rect.height));
    setWaiting(true);
    setError(null);
    renderApi.pick(projectId, runId, x, y).then(
      (result) => {
        setWaiting(false);
        setPicked({
          x,
          y,
          left: Math.max(0, Math.min(px - 20, rect.width - CARD_W)),
          // Dans le bas de l'image, la carte s'ouvre au-dessus du clic (le meuble reste vu).
          above: py > rect.height / 2,
          top: py > rect.height / 2 ? py - 14 : py + 14,
          result,
        });
      },
      (err: unknown) => {
        setWaiting(false);
        setError(err instanceof ApiError ? err.message : String(err));
      },
    );
  };
  return (
    <div className={`pick-frame${waiting ? " waiting" : ""}`}>
      <img
        src={url}
        alt={alt}
        onClick={onClick}
        title="Cliquez un meuble pour le désigner"
      />
      {picked && picked.result.kind !== "none" && (
        <img
          className="pick-outline"
          src={renderApi.outlineUrl(projectId, runId, picked.x, picked.y)}
          alt=""
          aria-hidden="true"
        />
      )}
      {picked && (
        <PickCard
          result={picked.result}
          left={picked.left}
          top={picked.top}
          above={picked.above}
          busy={busy}
          onAction={(action) => {
            setPicked(null);
            onAction(action);
          }}
          onClose={close}
        />
      )}
      {error && (
        <p className="pick-error" role="alert">
          {error}
        </p>
      )}
      <a className="pick-open" href={url} target="_blank" rel="noreferrer">
        agrandir
      </a>
    </div>
  );
}

export type PickAction =
  | { kind: "move"; elementId: string }
  | { kind: "refine"; elementId: string }
  | { kind: "remove"; elementId: string }
  | { kind: "replace"; elementId: string; assetId: string }
  | { kind: "ask"; elementId: string }
  | { kind: "ambiance" };

const TYPE_LABELS: Record<string, string> = {
  wall: "Mur",
  room: "Sol",
  ceiling: "Plafond",
  roof_plane: "Toit",
  opening: "Ouverture",
  roof_window: "Velux",
  stairs: "Escalier",
  furniture: "Meuble",
};
/** Surfaces : on change leur matière par l'ambiance de la pièce. */
const SURFACES = new Set(["wall", "room", "ceiling", "roof_plane"]);

let catalogCache: Promise<Catalog> | null = null;
function loadCatalog(): Promise<Catalog> {
  catalogCache ??= catalogApi.list();
  return catalogCache;
}

/** Carte de désignation (P4b) : ce que le clic a trouvé sur le rendu, et les gestes possibles.
 *  Près du clic ; Échap ou un clic à côté la ferme ; suit le thème (jetons de couleur). */
export function PickCard({
  result,
  left,
  top,
  above = false,
  busy,
  onAction,
  onClose,
}: {
  result: PickResult;
  /** Position dans le cadre de l'image (px), déjà ramenée à l'intérieur. */
  left: number;
  top: number;
  /** Ouverte au-dessus du point donné (clic dans le bas de l'image). */
  above?: boolean;
  busy: boolean;
  onAction: (action: PickAction) => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [replacing, setReplacing] = useState(false);
  const [options, setOptions] = useState<CatalogAsset[] | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node | null;
      if (ref.current && target && !ref.current.contains(target)) onClose();
    };
    window.addEventListener("keydown", onKey, true);
    // Le clic qui a ouvert la carte est fini : on écoute les suivants.
    const handle = window.setTimeout(
      () => window.addEventListener("mousedown", onDown),
      0,
    );
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("mousedown", onDown);
      window.clearTimeout(handle);
    };
  }, [onClose]);
  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>("button")?.focus();
  }, [result]);
  useEffect(() => {
    if (!replacing || !result.category) return;
    let cancelled = false;
    loadCatalog().then(
      (c) => {
        if (!cancelled)
          setOptions(
            c.assets.filter(
              (a) => a.category === result.category && a.id !== undefined,
            ),
          );
      },
      () => !cancelled && setOptions([]),
    );
    return () => {
      cancelled = true;
    };
  }, [replacing, result.category]);

  const id = result.element_id;
  const type = result.element_type ?? "";
  const furniture = result.kind === "element" && type === "furniture" && id;
  const title =
    result.kind === "element"
      ? `${TYPE_LABELS[type] ?? "Élément"} : ${result.name}`
      : result.kind === "decor"
        ? `Décor : ${result.name}`
        : "Rien à désigner ici";
  return (
    <div
      ref={ref}
      className={`pick-card${above ? " above" : ""}`}
      role="dialog"
      aria-label="Élément désigné"
      style={{ left, top }}
    >
      <strong>{title}</strong>
      {result.kind === "decor" && (
        <p className="muted">
          Posé automatiquement selon l'ambiance ; il suit la pièce, pas un
          meuble.
        </p>
      )}
      {result.kind === "none" && result.name !== "rien" && (
        <p className="muted">{result.name}.</p>
      )}
      <div className="pick-actions">
        {furniture && (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => onAction({ kind: "move", elementId: id })}
              title="Ouvre le plan, ce meuble choisi : glissez-le"
            >
              Déplacer
            </button>
            <button
              type="button"
              disabled={busy}
              aria-expanded={replacing}
              onClick={() => setReplacing((r) => !r)}
            >
              Remplacer
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onAction({ kind: "refine", elementId: id })}
              title="La 3D depuis la caméra de ce rendu, ce meuble choisi"
            >
              Affiner
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onAction({ kind: "ask", elementId: id })}
              title="Ouvre l'assistant avec ce meuble en contexte : « plus clair », « en bois », « plus près du mur »"
            >
              Demander…
            </button>
            <button
              type="button"
              className="danger"
              disabled={busy}
              onClick={() => onAction({ kind: "remove", elementId: id })}
              title="Retire ce meuble (annulable) ; la vue devient périmée"
            >
              Retirer
            </button>
          </>
        )}
        {result.kind === "element" && !furniture && id && (
          <>
            {SURFACES.has(type) ? (
              <button
                type="button"
                onClick={() => onAction({ kind: "ambiance" })}
                title="Matières et ambiance de la pièce"
              >
                Ambiance et matières
              </button>
            ) : (
              <button
                type="button"
                onClick={() => onAction({ kind: "refine", elementId: id })}
              >
                Affiner
              </button>
            )}
          </>
        )}
        {result.kind === "decor" && (
          <button type="button" onClick={() => onAction({ kind: "ambiance" })}>
            Changer l'ambiance
          </button>
        )}
      </div>
      {furniture && replacing && (
        <ul className="pick-replace" aria-label="Modèles de cette famille">
          {options === null && <li className="muted">Chargement…</li>}
          {options?.length === 0 && (
            <li className="muted">Aucun autre modèle dans cette famille.</li>
          )}
          {options?.map((a) => (
            <li key={a.id}>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  onAction({ kind: "replace", elementId: id, assetId: a.id })
                }
              >
                <img src={catalogApi.previewUrl(a.id)} alt="" />
                <span>{a.label}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
