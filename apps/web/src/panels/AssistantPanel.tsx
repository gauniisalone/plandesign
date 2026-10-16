import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  aiApi,
  type AiStatus,
  type AssistantMessage,
  type AssistantPreview,
  type SayTurn,
} from "../api/client";

interface Props {
  messages: AssistantMessage[];
  busy: boolean;
  /** Un modèle est configuré côté serveur (sinon la phrase d'installation, une fois). */
  modelConfigured: boolean | null;
  onSend: (text: string) => void;
  onUndo: (message: AssistantMessage) => void;
  onShowProposals: (message: AssistantMessage) => void;
  /** Accord du projet pour un modèle distant (les deux drapeaux). */
  consented: boolean;
  onAskConsent: (destination: string | null) => void;
  onPreview: (text: string) => Promise<AssistantPreview | null>;
  /** « À propos de » (P5) : la pièce ou le meuble choisi, ou désigné dans un rendu. */
  context: { label: string } | null;
  onClearContext: () => void;
  /** Case « Gemini » de l'accueil (P5) : rappel, et lien vers le réglage. */
  gemini: { case: boolean; remote: boolean } | null;
  onOpenGeminiSetting: () => void;
  /** Fil relu du journal local ; faux : il ne vit qu'en mémoire. */
  journalEnabled: boolean | null;
  /** Fiche Ambiance courte de la pièce en contexte, repliée sous la conversation. */
  ambiance: ReactNode;
  /** Consigne gardée de l'accueil (L1b, P5c) : un tour en attente d'une pièce fermée. */
  pending: { text: string; canApply: boolean } | null;
  onApplyPending: () => void;
  onForgetPending: () => void;
}

/** Exemples cliquables : l'ambiance, les meubles, les aménagements, dans une même boîte. */
const EXAMPLES = [
  "plus chaleureux, lumière du soir",
  "déplace le lit loin de la porte",
  "propose-moi trois aménagements",
];

const MARK = { done: "✓", refused: "✗", question: "?" } as const;

function readerLabel(turn: SayTurn): string {
  if (turn.reader !== "model") return "phrases simples";
  const id = (turn.model_id ?? "").replace(/^llm:/, "");
  return /gemini/i.test(id) ? "Gemini" : id || "le modèle";
}

/** L'assistant unifié (P5) : une seule boîte pour l'ambiance et les gestes, la même dans les
 *  trois onglets. Chaque tour dit, partie par partie, ce qui a été fait (✓), refusé (✗) ou
 *  demandé (?), avec la raison, et qui a lu la phrase ; « Annuler » défait tout le tour. Texte
 *  seul : la photo ne part jamais. */
export function AssistantPanel({
  messages,
  busy,
  modelConfigured,
  onSend,
  onUndo,
  onShowProposals,
  consented,
  onAskConsent,
  onPreview,
  context,
  onClearContext,
  gemini,
  onOpenGeminiSetting,
  journalEnabled,
  ambiance,
  pending,
  onApplyPending,
  onForgetPending,
}: Props) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [preview, setPreview] = useState<AssistantPreview | null>(null);
  useEffect(() => {
    let cancelled = false;
    aiApi.status().then(
      (st) => {
        if (!cancelled) setStatus(st);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);
  const remote = gemini?.remote ?? (!!status?.configured && !!status.remote);
  const remoteBlocked = remote && !!gemini?.case && !consented;
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);
  const send = (value = text) => {
    const t = value.trim();
    if (!t || busy) return;
    onSend(t);
    setText("");
  };
  return (
    <aside className="inspector assistant" aria-label="Assistant">
      <section>
        <p className="muted">
          Dites ce que vous voulez : l'ambiance (« plus chaleureux »), les
          meubles (« ajoute un bureau »), des aménagements. Chaque geste
          s'annule ; seuls le texte et les cotes du plan sont lus, jamais la
          photo.
        </p>
        {remote && gemini && (
          <p className="muted small gemini-line">
            Gemini (texte seul) :{" "}
            {gemini.case ? "case cochée sur l'accueil" : "case décochée"} ·{" "}
            <button className="link" onClick={onOpenGeminiSetting}>
              régler
            </button>
          </p>
        )}
        {modelConfigured === false && !remote && (
          <p className="muted small install-hint">
            Pour des phrases libres, installez un modèle local (gratuit) :{" "}
            <code>ollama pull qwen2.5vl:3b</code>.
          </p>
        )}
        {remoteBlocked && (
          <div className="consent-box">
            <p>
              Le modèle distant ({status?.destination ?? "service en ligne"})
              attend votre accord pour ce projet : en attendant, phrases
              simples, rien ne part.
            </p>
            <div className="row">
              <button
                className="link"
                disabled={busy || !text.trim()}
                onClick={() => void onPreview(text.trim()).then(setPreview)}
                title="Montre mot pour mot ce qui partirait pour la phrase écrite, sans rien envoyer"
              >
                Voir ce qui partirait
              </button>
              <button
                onClick={() => onAskConsent(status?.destination ?? null)}
                disabled={busy}
              >
                Autoriser pour ce projet
              </button>
            </div>
          </div>
        )}
        {preview && preview.text && (
          <details className="preview-box" open>
            <summary>
              Ce qui partirait vers {preview.destination} ({preview.model_id})
            </summary>
            <pre className="prompt-preview">{preview.text}</pre>
            <p className="muted small">
              Ne part jamais : {preview.never.join(", ")}.
            </p>
          </details>
        )}
        <div className="chat" role="log" aria-live="polite">
          {messages.map((m) => (
            <div key={m.id} className={`bubble ${m.role}`}>
              {m.text && <p>{m.text}</p>}
              {m.say && (
                <>
                  <ul className="actions">
                    {m.say.parts.map((p, i) => (
                      <li key={i} className={`status-${p.status}`}>
                        {p.kind === "answer" && p.status === "done"
                          ? ""
                          : `${MARK[p.status]} `}
                        {p.summary}
                        {p.status !== "done" && p.reason
                          ? ` — ${p.reason}`
                          : ""}
                      </li>
                    ))}
                  </ul>
                  <p className="muted small">
                    lu par {readerLabel(m.say)}
                    {m.say.notice ? ` · ${m.say.notice}` : ""}
                  </p>
                </>
              )}
              <div className="row">
                {m.say &&
                  m.say.committed > 0 &&
                  !m.undone &&
                  !m.fromJournal && (
                    <button
                      className="link"
                      disabled={busy}
                      onClick={() => onUndo(m)}
                      title="Défait tout ce tour (ambiance et gestes)"
                    >
                      Annuler
                    </button>
                  )}
                {m.undone && <span className="muted small">annulé</span>}
                {(m.say?.assistant?.proposals.length ?? 0) > 0 && (
                  <button
                    className="link"
                    onClick={() => onShowProposals(m)}
                    title="Ouvre la fiche Aménager avec ces dispositions"
                  >
                    voir les dispositions
                  </button>
                )}
              </div>
            </div>
          ))}
          {busy && messages.length > 0 && (
            <p className="job-status">
              <span className="spinner" aria-hidden="true" /> l'assistant
              réfléchit…
            </p>
          )}
          {pending && (
            <div
              className="bubble user pending"
              role="status"
              aria-label="Consigne gardée"
            >
              <p>« {pending.text} »</p>
              <p className="muted small">
                {pending.canApply
                  ? "Consigne gardée depuis l'accueil, prête pour la pièce."
                  : "Consigne gardée depuis l'accueil : elle s'appliquera dès qu'une pièce sera fermée (tracez les murs, puis Détecter les pièces)."}
              </p>
              <div className="row">
                {pending.canApply && (
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={onApplyPending}
                  >
                    Appliquer à la pièce
                  </button>
                )}
                <button
                  className="link"
                  disabled={busy}
                  onClick={onForgetPending}
                >
                  oublier
                </button>
              </div>
            </div>
          )}
          <div ref={endRef} />
        </div>
        {journalEnabled === false && (
          <p className="muted small">
            Journal local coupé : ce fil ne vit que le temps de la page.
          </p>
        )}
        {context && (
          <p className="assistant-context">
            À propos de :{" "}
            <span className="chip">
              {context.label}
              <button
                type="button"
                className="link"
                aria-label="Retirer le contexte"
                onClick={onClearContext}
              >
                ✕
              </button>
            </span>
          </p>
        )}
        {/* Deux lignes, toute la largeur ; Entrée envoie, Maj+Entrée passe à la ligne. */}
        <div className="compose">
          <textarea
            rows={2}
            value={text}
            placeholder="Dites ce que vous voulez…"
            aria-label="Votre demande à l'assistant"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            disabled={busy}
          />
          <button
            className="primary"
            onClick={() => send()}
            disabled={busy || !text.trim()}
          >
            Envoyer
          </button>
        </div>
        <ul className="examples">
          {EXAMPLES.map((e) => (
            <li key={e}>
              <button className="link" disabled={busy} onClick={() => send(e)}>
                « {e} »
              </button>
            </li>
          ))}
        </ul>
        {ambiance}
      </section>
    </aside>
  );
}
