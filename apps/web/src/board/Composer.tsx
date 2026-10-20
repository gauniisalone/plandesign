import { useEffect, useRef, useState } from "react";
import {
  homeApi,
  instructionApi,
  type GuidedAnswers,
  type HomeSettings,
  type ProjectSettingsUpdate,
} from "../api/client";
import type { Project } from "../api/types.gen";
import { answersSummary, type Requirements } from "../guided";
import { walls } from "../model";
import { useCapabilities } from "../state/capabilities";
import { GuidedFields } from "../panels/GuidedFields";

/** Le compositeur flottant du tableau (T7b, maquette d'augus du 16 octobre) : il remplace la
 *  note Exigences (T1) et la barre « Déclencheur » (T4). Deux onglets, « Prompt libre » et
 *  « Questions guidées », la case Gemini (texte seul), la ligne « compris », la case « Générer
 *  au dépôt d'une photo » et « Générer maintenant ». Refermé, rien des exigences ne reste sur
 *  le tableau : une pastille « Générer » le rouvre. Le contenu vit dans le projet
 *  (`pending_instruction`, `pending_answers`), lu par le chemin automatique. */

const MAX_TEXT = 300;
/** Ancienne ligne d'aide (retirée à la demande d'augus : moins de texte), en infobulle. */
const HELP =
  "Dites ce que vous voulez : espace, surface, chambres, style, envies. Rien n'est " +
  "obligatoire. Vos exigences sont gardées dans le projet ; le chemin automatique les lit.";
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

/** KAN-64 : le texte en cours, par projet. Une sélection fait prendre aux actions la place de
 *  l'Assistant, donc le compositeur se démonte (sans événement « blur ») : le texte saisi est
 *  gardé ici et envoyé au projet au démontage, puis repris tel quel quand il revient. */
const drafts = new Map<string, string>();

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
  docked = false,
}: {
  /** KAN-64 : rangé dans la zone « Assistant » du panneau de droite : toujours ouvert et
   *  déplié, sans bouton de fermeture (la zone se replie). */
  docked?: boolean;
  project: Project;
  hasRenders: boolean;
  /** Construction du plan en cours : l'étape remplace les boutons. */
  planProgress: string | null;
  onSettings: (body: ProjectSettingsUpdate) => void;
  /** Envoie le texte à l'assistant (« Nouvelle variante »), comme l'ancienne note. */
  onVariant: (text: string) => void;
  /** Les exigences au moment du clic : le texte du champ n'est peut-être pas encore
   *  revenu du serveur (il part quand le champ est quitté, donc par ce même clic). */
  onGenerateNow: (fresh: Requirements) => void;
}) {
  // Ouvert d'office tant qu'il n'y a pas de rendu ; ensuite, le dernier choix du projet.
  const [open, setOpen] = useState(() => storedOpen(project.id) ?? !hasRenders);
  // KAN-22 : replié (champ sur une ligne) tant qu'on n'y a pas touché et qu'aucune exigence
  // n'est saisie ; déplié au focus d'un champ ou au premier clic. Avec des exigences (arrivée
  // par /nouveau, saisie d'avant), déplié d'office. Le cadrage de départ du tableau mesure
  // le compositeur : replié, il garde le tableau lisible sur un petit écran (navigateur à
  // 150 %).
  const [compact, setCompact] = useState(
    () =>
      !project.pending_instruction?.trim() &&
      !Object.values(project.pending_answers ?? {}).some(
        (v) =>
          v !== null && v !== undefined && !(Array.isArray(v) && !v.length),
      ),
  );
  const [tab, setTab] = useState<Tab>("prompt");
  const [answers, setAnswers] = useState<GuidedAnswers>(
    () => project.pending_answers ?? {},
  );
  const [text, setText] = useState(
    () => drafts.get(project.id) ?? project.pending_instruction ?? "",
  );
  // Au démontage : le texte part au projet s'il a changé (le « blur » ne vient pas).
  const unsaved = useRef({
    text,
    saved: project.pending_instruction ?? "",
    onSettings,
  });
  useEffect(() => {
    unsaved.current = {
      text,
      saved: project.pending_instruction ?? "",
      onSettings,
    };
  });
  useEffect(
    () => () => {
      const { text: last, saved, onSettings: save } = unsaved.current;
      const value = last.trim().slice(0, MAX_TEXT);
      if (value !== saved) save({ pending_instruction: value });
    },
    [],
  );
  const [home, setHome] = useState<HomeSettings | null>(null);
  const [reading, setReading] = useState<{ text: string; items: string[] }>({
    text: "",
    items: [],
  });
  const answersTimer = useRef<number | null>(null);
  // Sans rendus (en ligne sans poste de calcul) : « Générer le plan » tant qu'il n'y a pas
  // de mur, puis plus rien (aucun bouton qui finirait en erreur de calcul).
  const caps = useCapabilities();
  const hasWalls = Object.values(project.levels).some(
    (l) => walls(l).length > 0,
  );
  const lastModelCall = useRef(0);
  const gemini = Boolean(home?.remote_model && home.gemini_text);

  useEffect(() => {
    homeApi.settings().then(setHome, () => undefined);
  }, []);

  const toggle = (next: boolean) => {
    setOpen(next);
    storeOpen(project.id, next);
    // Rouvert par la pastille : un geste voulu, le compositeur s'ouvre déplié.
    if (next) setCompact(false);
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

  if (!open && !docked)
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
      className={`board-composer${docked ? " docked" : compact ? " compact" : ""}`}
      aria-label="Compositeur"
      title={HELP}
      onPointerDown={stop}
      // Au clic (après le relâché : rien ne bouge sous le pointeur pendant le clic), ou au
      // focus d'un champ de saisie (clavier, remplissage).
      onClick={() => setCompact(false)}
      onFocus={(e) => {
        if (e.target.matches("textarea, select, input:not([type=checkbox])"))
          setCompact(false);
      }}
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
        <span className="board-composer-sp" />
        {text.trim() && (
          <button
            type="button"
            className="link"
            onClick={() => onVariant(text.trim())}
            title="Envoie le texte à l'assistant (un seul moteur)"
          >
            Nouvelle variante → assistant
          </button>
        )}
        {!docked && (
          <button
            type="button"
            className="icon board-composer-close"
            aria-label="Fermer le compositeur"
            title="Fermer (vos exigences sont gardées)"
            onClick={() => toggle(false)}
          >
            ×
          </button>
        )}
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
            rows={compact && !docked ? 1 : 3}
            maxLength={MAX_TEXT}
            placeholder="Décrivez le projet : pièce, surface, style, contraintes…"
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              drafts.set(project.id, e.target.value);
            }}
            onBlur={saveText}
          />
        </div>
      ) : (
        <div role="tabpanel" aria-label="Questions guidées">
          <GuidedFields answers={answers} disabled={false} onAnswer={answer} />
        </div>
      )}

      {home?.remote_model && (
        <label
          className="board-composer-gemini"
          title="Le texte seul part chez Google, jamais les photos"
        >
          <input
            type="checkbox"
            checked={gemini}
            onChange={(e) => toggleGemini(e.target.checked)}
          />
          {/* KAN-64 : un consentement, visible, sur une seule ligne. */}
          Mieux comprendre mon texte avec Gemini (texte seul)
        </label>
      )}

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
            {caps.renders && (
              <label
                className="board-trigger-toggle"
                title="Générer automatiquement au dépôt d'une photo"
              >
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
                {/* Replié : la case seule, le texte reste son nom (lecteurs d'écran). */}
                <span className={compact ? "sr-only" : undefined}>
                  Générer automatiquement au dépôt d'une photo
                </span>
              </label>
            )}
            <span className="board-composer-sp" />
            {(caps.renders || !hasWalls) && (
              <button
                type="button"
                className="primary"
                onClick={() => {
                  saveText();
                  onGenerateNow({
                    text: text.trim().slice(0, MAX_TEXT),
                    answers,
                  });
                }}
                title="Plan s'il manque (d'après les photos, sinon vos exigences), puis ambiance, aménagement, vues et rendus"
              >
                {caps.renders ? "Générer maintenant" : "Générer le plan"}
              </button>
            )}
          </>
        )}
      </footer>
    </section>
  );
}
