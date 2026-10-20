import { useState } from "react";
import type { BoardItem } from "../api/client";
import { MAX_COMMENT, pinPlace, type CommentsController } from "./useComments";

/** KAN-51 : les épingles, le brouillon et le panneau « Commentaires » (voir
 *  `useComments`). Le texte est toujours affiché comme du texte, jamais du HTML. */

const clock = (iso: string) =>
  new Date(iso).toLocaleString("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });

/** Les épingles des fils ouverts, dans le monde de l'espace de travail (taille constante). */
export function CommentPins({
  c,
  items,
}: {
  c: CommentsController;
  items: BoardItem[];
}) {
  if (c.hidden && !c.draft) return null;
  return (
    <>
      {!c.hidden &&
        c.threads
          .filter((t) => !t.resolved)
          .map((t) => {
            const place = pinPlace(t.target, items);
            const first = t.messages[0];
            return (
              <button
                key={t.id}
                type="button"
                className={`comment-pin${c.openId === t.id ? " open" : ""}${place.detached ? " detached" : ""}`}
                style={
                  {
                    left: place.x,
                    top: place.y,
                    "--pin": first?.author_color ?? "#8b8d98",
                  } as React.CSSProperties
                }
                aria-label={`Commentaire de ${first?.author_name ?? "?"}${place.detached ? " (détaché)" : ""}`}
                title={first?.text ?? ""}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => {
                  c.open(t.id);
                  c.setPanelOpen(true);
                }}
              >
                {t.messages.length}
              </button>
            );
          })}
      {c.draft && <CommentDraft c={c} place={pinPlace(c.draft, items)} />}
    </>
  );
}

function CommentDraft({
  c,
  place,
}: {
  c: CommentsController;
  place: { x: number; y: number };
}) {
  const [text, setText] = useState("");
  const send = () => {
    if (text.trim()) void c.publish(text.trim());
  };
  return (
    <div
      className="comment-draft"
      role="dialog"
      aria-label="Nouveau commentaire"
      style={{ left: place.x, top: place.y }}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <textarea
        aria-label="Votre commentaire"
        autoFocus
        maxLength={MAX_COMMENT}
        value={text}
        placeholder="Votre commentaire…"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") c.cancelDraft();
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send();
        }}
      />
      <div className="comment-actions">
        <button type="button" className="link" onClick={c.cancelDraft}>
          Annuler
        </button>
        <button
          type="button"
          className="primary"
          disabled={!text.trim()}
          onClick={send}
        >
          Publier
        </button>
      </div>
    </div>
  );
}

/** Le panneau « Commentaires » : la liste (ouverts, résolus) et le fil choisi. */
export function CommentsPanel({
  c,
  items,
  inline = false,
}: {
  c: CommentsController;
  items: BoardItem[];
  /** KAN-64 : rangé dans la zone « Commentaires » du panneau de droite (toujours là, sans
   *  en-tête : la zone a le sien). */
  inline?: boolean;
}) {
  const [tab, setTab] = useState<"open" | "resolved">("open");
  const [reply, setReply] = useState("");
  if (!c.panelOpen && !inline) return null;
  const current = c.threads.find((t) => t.id === c.openId) ?? null;
  const shown = c.threads.filter((t) =>
    tab === "open" ? !t.resolved : t.resolved,
  );
  return (
    <aside
      className={`comments-panel${inline ? " inline" : ""}`}
      role={inline ? "region" : "dialog"}
      aria-label="Commentaires"
      onPointerDown={(e) => e.stopPropagation()}
    >
      {!inline && (
        <header>
          <strong>Commentaires</strong>
          <button
            type="button"
            className="link"
            onClick={() => {
              c.setPanelOpen(false);
              c.open(null);
            }}
          >
            Fermer
          </button>
        </header>
      )}
      {c.error && (
        <p className="warn" role="alert">
          {c.error}
        </p>
      )}
      {current ? (
        <section aria-label="Fil de commentaires">
          <button type="button" className="link" onClick={() => c.open(null)}>
            ← Tous les commentaires
          </button>
          {pinPlace(current.target, items).detached && (
            <p className="muted">
              Détaché : l'élément commenté a été supprimé.
            </p>
          )}
          <ol className="comment-messages">
            {current.messages.map((m) => (
              <li key={m.id}>
                <span
                  className="comment-author"
                  style={{ color: m.author_color }}
                >
                  {m.author_name}
                </span>{" "}
                <time dateTime={m.created_at}>{clock(m.created_at)}</time>
                {m.edited_at && <span className="muted"> (modifié)</span>}
                <p className="comment-text">{m.text}</p>
                {m.mine && c.canComment && (
                  <button
                    type="button"
                    className="link"
                    onClick={() => void c.removeMessage(current.id, m.id)}
                  >
                    Supprimer mon message
                  </button>
                )}
              </li>
            ))}
          </ol>
          {c.canComment && (
            <>
              <textarea
                aria-label="Répondre"
                maxLength={MAX_COMMENT}
                value={reply}
                placeholder="Répondre…"
                onChange={(e) => setReply(e.target.value)}
              />
              <div className="comment-actions">
                <button
                  type="button"
                  className="primary"
                  disabled={!reply.trim()}
                  onClick={() => {
                    const text = reply.trim();
                    setReply("");
                    void c.reply(current.id, text);
                  }}
                >
                  Répondre
                </button>
                <button
                  type="button"
                  onClick={() => void c.resolve(current.id, !current.resolved)}
                >
                  {current.resolved ? "Rouvrir" : "Résoudre"}
                </button>
                {current.can_delete && (
                  <button
                    type="button"
                    className="link danger"
                    onClick={() => void c.removeThread(current.id)}
                  >
                    Supprimer le fil
                  </button>
                )}
              </div>
            </>
          )}
        </section>
      ) : (
        <>
          <div className="comment-tabs" role="tablist" aria-label="Filtre">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "open"}
              onClick={() => setTab("open")}
            >
              Ouverts ({c.threads.filter((t) => !t.resolved).length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "resolved"}
              onClick={() => setTab("resolved")}
            >
              Résolus ({c.threads.filter((t) => t.resolved).length})
            </button>
          </div>
          {shown.length === 0 ? (
            <p className="muted">
              {tab === "open"
                ? c.canComment
                  ? "Aucun commentaire ouvert. Outil Commentaire (C) : cliquer où le poser."
                  : "Aucun commentaire ouvert."
                : "Aucun commentaire résolu."}
            </p>
          ) : (
            <ul className="comment-list">
              {shown.map((t) => (
                <li key={t.id}>
                  <button type="button" onClick={() => c.open(t.id)}>
                    <span style={{ color: t.messages[0]?.author_color }}>
                      {t.messages[0]?.author_name}
                    </span>{" "}
                    {t.messages[0]?.text.slice(0, 80)}
                    <span className="muted"> · {t.messages.length}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </aside>
  );
}
