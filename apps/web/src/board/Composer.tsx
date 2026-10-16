import { useEffect, useRef, useState } from "react";
import {
  homeApi,
  instructionApi,
  type GuidedAnswers,
  type HomeSettings,
  type ProjectSettingsUpdate,
} from "../api/client";
import type { Project } from "../api/types.gen";
import { answersSummary, hasAnswers } from "../guided";
import { GuidedFields } from "../panels/GuidedFields";

/** Le compositeur flottant du tableau (T7b, maquette d'augus du 16 octobre) : il remplace la
 *  note Exigences (T1) et la barre « Déclencheur » (T4). Deux onglets, « Prompt libre » et
 *  « Questions guidées », la case Gemini (texte seul), la ligne « compris », la case « Générer
 *  au dépôt d'une photo » et « Générer maintenant ». Refermé, rien des exigences ne reste sur
 *  le tableau : une pastille « Générer » le rouvre. Le contenu vit dans le projet
 *  (`pending_instruction`, `pending_answers`), lu par le chemin automatique. */

const MAX_TEXT = 300;
const SAVE_DELAY_MS = 600;
const GEMINI_PAUSE_MS = 1200;
const GEMINI_SPACING_MS = 4500;

type Tab = "prompt" | "guided";

function storedOpen(projectId: string): boolean | null {
  try {
    const v = window.localStorage.getItem(`composer:${projectId}`);
    return v === null ? null : v === "1";
  } catch {
    return null;
  }
}

function storeOpen(projectId: string, open: boolean) {
  try {
    window.localStorage.setItem(`composer:${projectId}`, open ? "1" : "0");
  } catch {
    // stockage indisponible : l'état reste celui de la page
  }
}

export function Composer({
  project,
  hasRenders,
  planProgress,
  onSettings,
  onVariant,
  onGenerateNow,
}: {
  project: Project;
  hasRenders: boolean;
  /** Construction du plan en cours : l'étape remplace les boutons. */
  planProgress: string | null;
  onSettings: (body: ProjectSettingsUpdate) => void;
  /** Envoie le texte à l'assistant (« Nouvelle variante »), comme l'ancienne note. */
  onVariant: (text: string) => void;
  onGenerateNow: () => void;
}) {
  // Ouvert d'office tant qu'il n'y a pas de rendu ; ensuite, le dernier choix du projet.
  const [open, setOpen] = useState(() => storedOpen(project.id) ?? !hasRenders);
  const [tab, setTab] = useState<Tab>("prompt");
  const [answers, setAnswers] = useState<GuidedAnswers>(
    () => project.pending_answers ?? {},
  );
  const [text, setText] = useState(project.pending_instruction ?? "");
  const [home, setHome] = useState<HomeSettings | null>(null);
  const [reading, setReading] = useState<{ text: string; items: string[] }>({
    text: "",
    items: [],
  });
  const answersTimer = useRef<number | null>(null);
  const lastModelCall = useRef(0);
  const gemini = Boolean(home?.remote_model && home.gemini_text);

  useEffect(() => {
    homeApi.settings().then(setHome, () => undefined);
  }, []);

  const toggle = (next: boolean) => {
    setOpen(next);
    storeOpen(project.id, next);
  };

  // Les réponses partent au projet après une courte pause (un pas de réglages, pas un pas
  // du plan) ; le texte part quand le champ est quitté.
  const answer = (patch: Partial<GuidedAnswers>) => {
    const next = { ...answers, ...patch };
    setAnswers(next);
    if (answersTimer.current !== null)
      window.clearTimeout(answersTimer.current);
    answersTimer.current = window.setTimeout(
      () => onSettings({ pending_answers: next }),
      SAVE_DELAY_MS,
    );
  };
  const saveText = () => {
    const value = text.trim().slice(0, MAX_TEXT);
    if (value !== (project.pending_instruction ?? ""))
      onSettings({ pending_instruction: value });
  };

  // « compris : … » : les phrases simples en direct ; Gemini (texte seul) si la case est
  // cochée, après une pause et au plus un appel toutes les 4,5 s.
  useEffect(() => {
    const value = text.trim();
    if (value.length < 3) return;
    const wait = gemini
      ? Math.max(
          GEMINI_PAUSE_MS,
          GEMINI_SPACING_MS - (Date.now() - lastModelCall.current),
        )
      : 300;
    const timer = window.setTimeout(() => {
      if (gemini) lastModelCall.current = Date.now();
      instructionApi.preview(value, gemini).then(
        (r) =>
          setReading({
            text: value,
            items: r.translation.summary?.length
              ? r.translation.summary
              : r.translation.understood,
          }),
        () => setReading({ text: value, items: [] }),
      );
    }, wait);
    return () => window.clearTimeout(timer);
  }, [text, gemini]);

  const understood = [
    ...answersSummary(answers),
    ...(reading.text === text.trim() ? reading.items : []),
  ];
  const toggleGemini = (on: boolean) => {
    setHome((s) => (s ? { ...s, gemini_text: on } : s));
    homeApi.setGemini(on).then(setHome, () => {
      setHome((s) => (s ? { ...s, gemini_text: !on } : s));
    });
  };
  const stop = (e: React.PointerEvent) => e.stopPropagation();

  if (!open)
    return (
      <button
        type="button"
        className="primary board-composer-pill"
        onPointerDown={stop}
        onClick={() => toggle(true)}
        aria-label="Ouvrir le compositeur"
        title="Vos exigences, et générer"
      >
        {planProgress ? <span className="spinner" aria-hidden="true" /> : null}
        Générer
      </button>
    );

  return (
    <section
      className="board-composer"
      aria-label="Compositeur"
      onPointerDown={stop}
      onWheel={(e) => e.stopPropagation()}
    >
      <header className="board-composer-head">
        <div
          className="board-composer-tabs"
          role="tablist"
          aria-label="Exigences"
        >
          {(
            [
              ["prompt", "Prompt libre"],
              ["guided", "Questions guidées"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={tab === id}
              className={tab === id ? "active" : undefined}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="icon board-composer-close"
          aria-label="Fermer le compositeur"
          title="Fermer (vos exigences sont gardées)"
          onClick={() => toggle(false)}
        >
          ×
        </button>
      </header>

      {understood.length > 0 && (
        <p className="board-composer-understood" aria-live="polite">
          compris : {understood.join(" · ")}
        </p>
      )}

      {tab === "prompt" ? (
        <div role="tabpanel" aria-label="Prompt libre">
          <textarea
            aria-label="Vos envies et contraintes"
            rows={3}
            maxLength={MAX_TEXT}
            placeholder="Décrivez le projet : pièce, surface, style, contraintes…"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onBlur={saveText}
          />
        </div>
      ) : (
        <div role="tabpanel" aria-label="Questions guidées">
          <GuidedFields answers={answers} disabled={false} onAnswer={answer} />
        </div>
      )}

      {home?.remote_model && (
        <label className="board-composer-gemini">
          <input
            type="checkbox"
            checked={gemini}
            onChange={(e) => toggleGemini(e.target.checked)}
          />
          Mieux comprendre mon texte avec Gemini — le texte seul part chez
          Google, jamais les photos
        </label>
      )}

      <p className="muted board-composer-note">
        {hasAnswers(answers) || text.trim()
          ? "Gardées dans le projet : déposez une photo de la pièce ou générez ; le chemin automatique les lit."
          : "Dites ce que vous voulez : espace, surface, chambres, style, envies. Rien n'est obligatoire."}
        {text.trim() && (
          <>
            {" "}
            <button
              type="button"
              className="link"
              onClick={() => onVariant(text.trim())}
              title="Envoie le texte à l'assistant (un seul moteur)"
            >
              Nouvelle variante → assistant
            </button>
          </>
        )}
      </p>

      <footer
        className="board-composer-foot"
        role="group"
        aria-label="Déclencheur"
      >
        {planProgress ? (
          <span className="board-plan-progress" role="status">
            <span className="spinner" aria-hidden="true" />
            {planProgress}
          </span>
        ) : (
          <>
            <label className="board-trigger-toggle">
              <input
                type="checkbox"
                checked={project.render.auto_on_drop}
                onChange={(e) =>
                  onSettings({
                    render: {
                      ...project.render,
                      auto_on_drop: e.target.checked,
                    },
                  })
                }
              />
              Générer automatiquement au dépôt d'une photo
            </label>
            <span className="board-composer-sp" />
            <button
              type="button"
              className="primary"
              onClick={onGenerateNow}
              title="Plan s'il manque (d'après les photos, sinon vos exigences), puis ambiance, aménagement, vues et rendus"
            >
              Générer maintenant
            </button>
          </>
        )}
      </footer>
    </section>
  );
}
