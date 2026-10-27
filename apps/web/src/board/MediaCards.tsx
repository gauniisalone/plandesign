import { useState } from "react";
import type { ContextualEntry } from "../api/client";
import { hostOf } from "./mediaItems";

/** Cartes des médias du tableau (C1). Le bandeau du haut sert à déplacer la carte ; la
 *  vidéo et les liens reçoivent leurs propres clics. Rien n'est demandé à l'extérieur : un
 *  lien n'est qu'une adresse, ouverte au clic dans un nouvel onglet. */

const stop = (e: React.PointerEvent) => e.stopPropagation();

export function VideoCard({ url, name }: { url: string; name: string }) {
  return (
    <span className="board-media">
      <span className="board-media-head" title={name}>
        {name}
      </span>
      <video
        className="board-video"
        src={url}
        controls
        preload="metadata"
        aria-label={`Vidéo ${name}`}
        onPointerDown={stop}
      />
    </span>
  );
}

export function PdfCard({
  url,
  thumb,
  name,
}: {
  url: string;
  thumb: string | null;
  name: string;
}) {
  return (
    <span className="board-media">
      <span className="board-media-head" title={name}>
        PDF · {name}
      </span>
      {thumb ? (
        <img
          className="board-pdf"
          src={thumb}
          alt={`Première page de ${name}`}
          draggable={false}
        />
      ) : (
        <span className="board-missing">aperçu indisponible</span>
      )}
      <a
        className="board-media-open"
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        onPointerDown={stop}
      >
        Ouvrir
      </a>
    </span>
  );
}

export function LinkCard({ url, title }: { url: string; title: string }) {
  return (
    <span className="board-link">
      <span className="board-link-host">{hostOf(url)}</span>
      <b className="board-link-title" title={url}>
        {title || hostOf(url)}
      </b>
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        onPointerDown={stop}
        title={url}
      >
        Ouvrir ↗
      </a>
    </span>
  );
}

/** Rendu contextualisé (A1) : l'image, « proportions estimées », la source spatiale et
 *  l'horodatage, et le prompt final dépliable. Toujours `generated`. */
export function ContextualCard({
  url,
  title,
  entry,
  kept,
  comment,
  onKeep,
  onComment,
  onDuplicate,
}: {
  url: string;
  title: string;
  entry: ContextualEntry | undefined;
  /** A3 : « À conserver », commentaire attaché, « Dupliquer » sur la carte. */
  kept: boolean;
  comment: string;
  onKeep: () => void;
  onComment: (text: string) => void;
  onDuplicate: () => void;
}) {
  const [writing, setWriting] = useState(false);
  const [draft, setDraft] = useState(comment);
  const when = entry
    ? new Date(entry.created_at).toLocaleString("fr-FR", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";
  return (
    <span className="board-media board-contextual">
      <span className="board-media-head" title={title}>
        {title}
      </span>
      <img src={url} alt={title || "Rendu contextualisé"} draggable={false} />
      <span className="board-contextual-meta" onPointerDown={stop}>
        <span className="board-contextual-actions" role="group" aria-label="Actions de la variante">
          <button
            type="button"
            className={kept ? "on" : ""}
            aria-pressed={kept}
            aria-label={kept ? "Conservée" : "À conserver"}
            onClick={onKeep}
            title={kept ? "Conservée (cliquer pour retirer)" : "À conserver : marquer cette variante comme retenue"}
          >
            {kept ? "★" : "☆"}
          </button>
          <button
            type="button"
            aria-label="Commenter"
            onClick={() => setWriting((w) => !w)}
            title="Commenter : attacher une remarque à cette carte"
          >
            💬
          </button>
          <button
            type="button"
            aria-label="Dupliquer"
            onClick={onDuplicate}
            title="Dupliquer la carte"
          >
            ⧉
          </button>
          {kept && <span className="board-contextual-kept">conservée</span>}
        </span>
        {writing ? (
          <textarea
            autoFocus // NOSONAR S9379 : note ouverte par l'utilisateur
            aria-label="Commentaire"
            rows={2}
            value={draft}
            placeholder="votre remarque sur cette variante…"
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => {
              setWriting(false);
              if (draft !== comment) onComment(draft);
            }}
          />
        ) : (
          comment && <span className="board-contextual-comment">{comment}</span>
        )}
        <span className="plan-status estimated">proportions estimées</span>
        {entry && (
          <span className="muted">
            {entry.source.kind === "photo" ? "photo" : "vue 3D"} · {when} ·
            force {entry.strength} · contraintes {entry.control_scale}
            {entry.guided_by && ` (${entry.guided_by.join(" + ")})`} · graine{" "}
            {entry.seed}
          </span>
        )}
        {entry && entry.references && entry.references.length > 0 && (
          <span className="muted">
            références : {entry.references.join(", ")}
            {entry.reference_scale != null && ` (poids ${entry.reference_scale})`}
          </span>
        )}
        {entry && (
          <details className="board-contextual-prompt">
            <summary>Prompt</summary>
            <p>{entry.prompt}</p>
            {entry.intention && (
              <p className="muted">Intention : {entry.intention}</p>
            )}
          </details>
        )}
      </span>
    </span>
  );
}
