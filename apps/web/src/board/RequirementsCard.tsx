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

/** La note Exigences du tableau (T1, principe d'augus : « un projet = un tableau »). Les
 *  mêmes champs que les questions guidées de l'accueil, le texte libre, la case Gemini (texte
 *  seul) et la ligne « compris ». Le contenu vit dans le projet (`pending_answers`,
 *  `pending_instruction`) : le chemin automatique le lit, le tableau ne garde que la place. */

const MAX_TEXT = 300;
const SAVE_DELAY_MS = 600;
const GEMINI_PAUSE_MS = 1200;
const GEMINI_SPACING_MS = 4500;

export function RequirementsCard({
  project,
  onSettings,
  onVariant,
}: {
  project: Project;
  onSettings: (body: ProjectSettingsUpdate) => void;
  /** Envoie le texte à l'assistant (« Nouvelle variante »), comme l'ancienne note. */
  onVariant: (text: string) => void;
}) {
  const stop = (e: React.PointerEvent) => e.stopPropagation();
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

  // Les réponses partent au projet après une courte pause (un pas de réglages, pas un pas
  // du plan) ; le texte part quand le champ est quitté.
  const answer = (patch: Partial<GuidedAnswers>) => {
    const next = { ...answers, ...patch };
    setAnswers(next);
    if (answersTimer.current !== null) window.clearTimeout(answersTimer.current);
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

  // Le bandeau du haut sert à déplacer la carte ; les champs reçoivent leurs propres clics.
  return (
    <div className="board-requirements" aria-label="Exigences du projet">
      <span className="board-media-head">Exigences</span>
      <div className="board-requirements-body" onPointerDown={stop}>
      {understood.length > 0 && (
        <p className="board-requirements-understood" aria-live="polite">
          compris : {understood.join(" · ")}
        </p>
      )}
      <GuidedFields answers={answers} disabled={false} onAnswer={answer} />
      <textarea
        aria-label="Vos envies et contraintes"
        rows={3}
        maxLength={MAX_TEXT}
        placeholder="Vos envies et contraintes… (ex. : grenier chaleureux, bois et lin, deux lits)"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={saveText}
      />
      {home?.remote_model && (
        <label className="board-requirements-gemini">
          <input
            type="checkbox"
            checked={gemini}
            onChange={(e) => toggleGemini(e.target.checked)}
          />
          Mieux comprendre mon texte avec Gemini — le texte seul part chez Google,
          jamais les photos
        </label>
      )}
      <span className="muted board-requirements-note">
        {hasAnswers(answers) || text.trim()
          ? "Gardées : déposez une photo de la pièce ou générez le plan pour lancer ; le chemin automatique les lit. « Nouvelle variante » les envoie à l'assistant."
          : "Dites ce que vous voulez : espace, surface, chambres, style, envies. Rien n'est obligatoire."}
      </span>
      {text.trim() && (
        <button
          type="button"
          className="primary board-variant"
          onClick={() => onVariant(text.trim())}
          title="Envoie le texte à l'assistant (un seul moteur)"
        >
          Nouvelle variante → assistant
        </button>
      )}
      </div>
    </div>
  );
}
