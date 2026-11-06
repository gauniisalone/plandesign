import { useEffect, useId, useRef, useState } from "react";
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
import { useRenderStatus } from "../state/renderStatus";
import { allowedOf, quotaOf } from "../state/renderStatusState";
import { GuidedFields } from "../panels/GuidedFields";
import { renderApi } from "../api/client";
import { askRooms } from "./askRooms";
import { hasLinkedPlan, isNoPlan, NO_PLAN_TEXT } from "./linkedPlan";
import {
  fits,
  quotaText,
  readSettings,
  RENDERS_EVENT,
  takes,
  todayText,
} from "./renderSettings";
import { hasRooms, roomRows } from "./roomViews";

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
  levelId,
  onProjectChanged,
}: {
  /** KAN-134 : le plan ouvert, celui que « Rendre » rend (ses vues de pièce cochées). */
  levelId?: string;
  /** KAN-134 : le projet après « Rendre » quand les vues de pièce ont dû être calculées. */
  onProjectChanged?: (project: Project) => void;
  /** KAN-64 : rangé dans la zone « Assistant » du panneau de droite : toujours ouvert et
   *  déplié, sans bouton de fermeture (la zone se replie). */
  docked?: boolean;
  project: Project;
  hasRenders: boolean;
  /** Construction du plan en cours : l'étape remplace les boutons. */
  planProgress: string | null;
  onSettings: (body: ProjectSettingsUpdate) => void;
  /** Envoie le texte à l'assistant (l'ambiance) ; résout à vrai quand il a fini. KAN-134 :
   *  « Appliquer à l'ambiance », ou la première moitié de « Rendre ». */
  onVariant: (text: string) => Promise<boolean>;
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
  // KAN-134 : en ligne, plan posé : « Rendre » (ambiance puis une demande par pièce cochée)
  // si les rendus sont ouverts à ce compte, sinon « Appliquer à l'ambiance ».
  const statusState = useRenderStatus(caps.online && !caps.renders);
  const realistic =
    caps.online && !caps.renders && Boolean(allowedOf(statusState));
  const { quota, used } = quotaOf(statusState);
  const [sending, setSending] = useState(false);
  const hintId = useId();
  /** Les tirages réglés dans le node Rendus (relus quand ils changent). */
  const [draws, setDraws] = useState(() => readSettings(project.id).draws);
  useEffect(() => {
    const again = () => setDraws(readSettings(project.id).draws);
    window.addEventListener(RENDERS_EVENT, again);
    return () => window.removeEventListener(RENDERS_EVENT, again);
  }, [project.id]);
  const [said, setSaid] = useState<{ text: string; failed?: boolean } | null>(null);

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

  // KAN-134 : le pied quand le plan est posé. En local avec Blender, « Générer maintenant »
  // reste à côté (le chemin complet, rendu Blender compris).
  const level = levelId ? project.levels[levelId] : undefined;
  const planMode = hasWalls;
  const rows = roomRows(level);
  const ticked = rows.filter((r) => r.on);
  const needViews = rows.length === 0 && hasRooms(level);
  const take = takes(ticked.length, draws);
  const linked = Boolean(levelId && hasLinkedPlan(project, levelId));
  const canRender =
    realistic &&
    linked &&
    !sending &&
    (needViews || (ticked.length > 0 && fits(take, used, quota)));
  const value = () => text.trim().slice(0, MAX_TEXT);
  const fail = (e: unknown) =>
    setSaid({
      text: isNoPlan(e) ? NO_PLAN_TEXT : e instanceof Error ? e.message : String(e),
      failed: true,
    });
  /** « Rendre » : l'ambiance d'abord (s'il y a un texte), puis une demande par pièce cochée et
   *  par tirage ; sans vue de pièce encore, elles sont calculées d'abord (focale réglée). */
  const render = async () => {
    if (!levelId || !canRender) return;
    saveText();
    setSending(true);
    setSaid(null);
    try {
      const wish = value();
      if (wish && !(await onVariant(wish))) {
        setSaid({
          text: "L'ambiance n'a pas pu être mise à jour : rien n'est rendu.",
          failed: true,
        });
        return;
      }
      let ids = ticked.map((r) => r.camera.id);
      if (needViews) {
        const done = await renderApi.roomViews(
          project.id,
          levelId,
          readSettings(project.id).focal,
        );
        onProjectChanged?.(done.project);
        ids = roomRows(done.project.levels[levelId])
          .filter((r) => r.on)
          .map((r) => r.camera.id);
      }
      if (ids.length === 0) {
        setSaid({
          text: "Aucune pièce cochée : cochez une pièce dans le node Rendus.",
          failed: true,
        });
        return;
      }
      const n = await askRooms(project.id, levelId, ids, draws);
      setSaid({
        text:
          (wish ? "Ambiance mise à jour. " : "") +
          (n > 1
            ? `${n} rendus demandés : ils arrivent dans le node Rendus.`
            : "1 rendu demandé : il arrive dans le node Rendus."),
      });
    } catch (e) {
      fail(e);
    } finally {
      setSending(false);
    }
  };
  /** « Appliquer à l'ambiance » : la consigne traduite en intention, sans rendu. */
  const apply = async () => {
    const wish = value();
    if (!wish || sending) return;
    saveText();
    setSending(true);
    setSaid(null);
    try {
      setSaid(
        (await onVariant(wish))
          ? { text: "Ambiance mise à jour. Les rendus existants ne changent pas." }
          : { text: "L'ambiance n'a pas pu être mise à jour.", failed: true },
      );
    } catch (e) {
      fail(e);
    } finally {
      setSending(false);
    }
  };
  const primary = () => {
    if (!planMode) return;
    void (realistic ? render() : apply());
  };
  const renderHint = !linked
    ? NO_PLAN_TEXT
    : needViews
      ? ["Ambiance selon votre texte, puis une vue calculée et rendue par pièce.", todayText(used, quota)]
          .filter(Boolean)
          .join(" · ")
      : ticked.length === 0
        ? "Aucune pièce cochée dans le node Rendus."
        : `Ambiance selon votre texte, puis une demande par pièce cochée. ${quotaText(take, used, quota)}`;
  const applyHint = !text.trim()
    ? "Écrivez une envie (style, matières, couleurs…)."
    : caps.online
      ? "Ambiance changée, sans rendu. Les rendus réalistes ne sont pas encore ouverts à ce compte."
      : "Ambiance changée, sans rendu.";

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
        {/* KAN-134 : « Nouvelle variante → assistant » est devenu le bouton du pied, nommé
            par son effet (« Rendre » ou « Appliquer à l'ambiance »). */}
        {!docked && (
          <button
            type="button"
            className="icon board-composer-close"
            aria-label="Fermer le compositeur"
            data-tip="Fermer (vos exigences sont gardées)"
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
            onKeyDown={(e) => {
              // KAN-134 : Ctrl + Entrée, le bouton principal du pied.
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                primary();
              }
            }}
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
            {/* KAN-120 : plus de « Générer automatiquement au dépôt d'une photo ». */}
            <span className="board-composer-sp" />
            {planMode && (
              <div className="board-composer-act">
                <button
                  type="button"
                  className="primary"
                  disabled={realistic ? !canRender : !text.trim() || sending}
                  aria-describedby={hintId}
                  onClick={primary}
                >
                  {sending && <span className="spinner" aria-hidden="true" />}
                  {realistic ? "Rendre" : "Appliquer à l'ambiance"}
                </button>
                <p id={hintId} className="board-composer-hint">
                  {realistic ? renderHint : applyHint}
                  {" · Ctrl + Entrée"}
                </p>
              </div>
            )}
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
      {said && (
        <p
          className={`board-composer-said${said.failed ? " failed" : ""}`}
          role={said.failed ? "alert" : "status"}
        >
          {said.failed ? "" : "✓ "}
          {said.text}
        </p>
      )}
    </section>
  );
}
