import { useCallback, useEffect, useRef, useState } from "react";
import {
  aiApi,
  api,
  ApiError,
  assistantApi,
  type AssistantMessage,
  autoplanApi,
  type AutoPlanResult,
  inspirationApi,
  jobActive,
  type InspirationRun,
  type IntentionBody,
  type IntentionPreview,
  type IntentionRun,
  type JobStatus,
  layoutApi,
  type LayoutBody,
  type LayoutProposal,
  type LayoutRun,
  type CritiqueBody,
  type CritiqueRun,
  type ReviseBody,
  type FurnitureMoveCheck,
  type MeasurementCreate,
  type PerceptionRun,
  planApi,
  recalibrateApi,
  type DimensionsBody,
  type DimensionsSummary,
  type RecalibrateBody,
  type RecalibrateSummary,
  type PlanImportPreview,
  renderApi,
  instructionApi,
  pilotApi,
  type AutopilotRun,
  type GuidedAnswers,
  type Aspect,
  type AutoViews,
  type InstructionOutcome,
  type FinishStyle,
  type RenderEntry,
  type ProjectSettingsUpdate,
  type NewPlanBody,
} from "../api/client";
import type { Op } from "../api/ops";
import type {
  Calibration,
  DerivedLevel,
  Project,
  ValidationReport,
} from "../api/types.gen";
import { hasAnswers, type Requirements } from "../guided";
import { isPlanFile } from "../importFiles";
import { firstLevel, NO_SELECTION, walls, type Selection } from "../model";
import { currentCapabilities } from "./capabilities";
import { takePendingArrival } from "./pendingArrival";

/** `updated_at` en microsecondes (le serveur l'écrit à la microseconde, strictement croissant
 *  par projet) : `Date.parse` s'arrête à la milliseconde. */
function stamp(iso: string): number {
  const m = iso.match(/^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(.*)$/);
  if (!m) return Date.parse(iso) * 1000;
  const micro = Number((m[2] ?? "").padEnd(6, "0"));
  return Date.parse(`${m[1]}${m[3]}`) * 1000 + micro;
}

export interface EditorState {
  project: Project | null;
  levelId: string | null;
  derived: DerivedLevel | null;
  report: ValidationReport | null;
  selection: Selection;
  busy: boolean;
  error: string | null;
  /** B4 : le dernier geste n'a pas pu être enregistré (réseau, serveur), à distinguer d'un
   *  geste refusé par les règles du plan, qui n'est pas un échec d'enregistrement. */
  saveFailed: boolean;
  lastCalibration: Calibration | null;
  /** Avertissements du contrôle qualité, par image importée dans cette session. */
  imageWarnings: Record<string, string[]>;
  lastPerception: PerceptionRun | null;
  lastInspiration: InspirationRun | null;
  inspirationJob: JobStatus | null;
  briefPreview: IntentionPreview | null;
  lastBrief: IntentionRun | null;
  lastLayouts: LayoutRun | null;
  /** Variante montrée en fantôme sur le plan (pas dans la scène). */
  ghost: LayoutProposal | null;
  /** Dernière critique demandée (3.5), attachée à une variante par index et graine. */
  lastCritique: CritiqueRun | null;
  /** Seconde variante de la comparaison côte à côte (dessinée en vert sur le plan). */
  compare: LayoutProposal | null;
  /** Dernier plan vectoriel lu (DXF, PDF, SVG), déjà devenu des murs. */
  planPreview: PlanImportPreview | null;
  /** Calques retenus pour les murs du dernier plan lu. */
  planLayers: string[];
  /** Curseur d'historique juste après l'import du plan : tant qu'il n'a pas bougé, la
   *  lecture du plan (calques, échelle papier) peut être corrigée. */
  planCursor: number | null;
  /** Construction automatique du plan depuis une image (tâche de fond). */
  autoJob: JobStatus | null;
  lastAuto: AutoPlanResult | null;
  /** Curseur d'historique avant la construction automatique : « ce n'est pas une
   *  photo » y revient avant de relire l'image autrement. */
  autoBase: number | null;
  /** Conversation avec l'assistant (directive 3) et éléments à surligner. */
  assistant: AssistantMessage[];
  assistantHighlight: string[];
  assistantUnderstanding: "model" | "phrases" | null;
  assistantModelConfigured: boolean | null;
  /** Dernier meuble dont on a parlé (« déplace-le »). */
  assistantLast: string | null;
  /** Case « Gemini » de l'accueil et modèle distant configuré (rappel du panneau, P5). */
  assistantGemini: { case: boolean; remote: boolean } | null;
  /** Le fil est relu du journal local (sinon il ne vit qu'en mémoire). */
  assistantJournal: boolean | null;
  /** Dernier « Recalibrer » : facteur annoncé, annulable. */
  lastRecalibration: RecalibrateSummary | null;
  /** Le plan estimé a été recalibré : le bandeau n'a plus lieu d'être. */
  autoDone: boolean;
  /** Compte les plans arrivés tout seuls : l'atelier passe alors à l'onglet Plan. */
  arrival: number;
  /** Rendu Blender en cours (tâche de fond, suivie toutes les 2 s). */
  renderJob: JobStatus | null;
  /** Galerie des rendus du projet, du plus récent au plus ancien. */
  renders: RenderEntry[];
  /** Les rendus ont été lus à l'ouverture : l'onglet d'arrivée peut être choisi (P1). */
  rendersLoaded: boolean;
  /** Chemin « zéro geste » en cours (V1, point 6) : n'occupe pas l'éditeur. */
  pilotJob: JobStatus | null;
  /** Ce que le dernier chemin automatique a fait (rendus d'arrivée). */
  pilotRun: AutopilotRun | null;
  /** Compte les chemins arrivés au bout : l'atelier passe alors à Rendre. */
  pilotArrival: number;
}

const INITIAL: EditorState = {
  project: null,
  levelId: null,
  derived: null,
  report: null,
  selection: NO_SELECTION,
  busy: false,
  error: null,
  saveFailed: false,
  lastCalibration: null,
  imageWarnings: {},
  lastPerception: null,
  lastInspiration: null,
  inspirationJob: null,
  briefPreview: null,
  lastBrief: null,
  lastLayouts: null,
  ghost: null,
  lastCritique: null,
  compare: null,
  planPreview: null,
  planLayers: [],
  planCursor: null,
  autoJob: null,
  lastAuto: null,
  autoBase: null,
  assistant: [],
  assistantHighlight: [],
  assistantUnderstanding: null,
  assistantModelConfigured: null,
  assistantLast: null,
  assistantGemini: null,
  assistantJournal: null,
  lastRecalibration: null,
  autoDone: false,
  arrival: 0,
  renderJob: null,
  renders: [],
  rendersLoaded: false,
  pilotJob: null,
  pilotRun: null,
  pilotArrival: 0,
};

export function useProject(projectId: string) {
  const [state, setState] = useState<EditorState>(INITIAL);
  /** Dernier fichier de plan lu, pour le relire à une autre échelle papier. */
  const lastPlanFile = useRef<File | null>(null);

  const run = useCallback(
    async <T>(work: () => Promise<T>): Promise<T | undefined> => {
      setState((s) => ({ ...s, busy: true, error: null }));
      try {
        const result = await work();
        setState((s) => ({ ...s, busy: false, saveFailed: false }));
        return result;
      } catch (e) {
        const message = e instanceof ApiError ? e.message : String(e);
        const report = e instanceof ApiError ? (e.report ?? null) : null;
        setState((s) => ({
          ...s,
          busy: false,
          error: message,
          saveFailed: !(e instanceof ApiError) || e.status >= 500,
          report: report ?? s.report,
        }));
        return undefined;
      }
    },
    [],
  );

  /** T7e : plan ouvert (le niveau édité, visé par le chemin automatique) ; le premier par
   *  défaut, gardé d'un changement à l'autre tant qu'il existe (un plan annulé rend le
   *  premier). */
  const levelRef = useRef<string | null>(null);

  /** Recharge les dérivés après un changement qui ne les renvoie pas (undo, mesures...). */
  const applyProject = useCallback(
    async (project: Project, wanted?: string) => {
      const keep = wanted ?? levelRef.current;
      const level =
        (keep ? project.levels[keep] : undefined) ?? firstLevel(project);
      levelRef.current = level?.id ?? null;
      const derived = level ? await api.derived(project.id, level.id) : null;
      const report = await api.validation(project.id);
      // Chaque pose attend `derived` et `validation` : deux poses peuvent se croiser (un geste
      // pendant qu'une tâche rend son résultat). L'écran ne revient jamais en arrière : un
      // projet plus ancien que celui affiché ne l'écrase pas (gestes, annuler, rétablir et
      // imports avancent `updated_at`).
      setState((s) =>
        s.project &&
        s.project.id === project.id &&
        stamp(project.updated_at) < stamp(s.project.updated_at)
          ? s
          : {
              ...s,
              project,
              levelId: level?.id ?? null,
              derived,
              report,
              selection: pruneSelection(
                s.selection,
                project,
                level?.id ?? null,
              ),
            },
      );
    },
    [],
  );

  /** T7e : ouvrir un autre plan du projet (cadre « Plan · nom » de l'espace de travail). */
  const openLevel = useCallback(
    (levelId: string) => {
      if (!state.project || levelId === levelRef.current) return;
      const project = state.project;
      void run(async () => applyProject(project, levelId));
    },
    [state.project, run, applyProject],
  );

  /** T7e : « + Nouveau plan » ; le plan créé s'ouvre. */
  const newPlan = useCallback(
    async (body: NewPlanBody): Promise<string | undefined> => {
      if (!state.project) return undefined;
      const id = state.project.id;
      const source = levelRef.current;
      return run(async () => {
        const result = await api.newPlan(id, {
          ...body,
          source_level_id: source,
        });
        await applyProject(result.project, result.level_id);
        return result.level_id;
      });
    },
    [state.project, run, applyProject],
  );

  useEffect(() => {
    let cancelled = false;
    // À l'ouverture, le nom automatique suit la pièce ; un ancien nom de fichier
    // (« WhatsApp Image… ») devient « Grenier · 6 oct. » ; un nom donné à la main reste.
    api
      .autoName(projectId)
      .catch(() => api.getProject(projectId))
      .then(async (project) => {
        if (cancelled) return;
        await applyProject(project);
        // Les rendus dès l'ouverture : l'onglet Rendus est celui d'arrivée s'il y en a (P1).
        const renders = await renderApi
          .list(projectId)
          .catch(() => [] as RenderEntry[]);
        if (cancelled) return;
        // Arrivée depuis l'accueil : le chemin a fini là-bas, l'atelier montre « Vos rendus ».
        const arrived = takePendingArrival(projectId);
        setState((s) => ({
          ...s,
          renders,
          rendersLoaded: true,
          ...(arrived
            ? { pilotRun: arrived, pilotArrival: s.pilotArrival + 1 }
            : {}),
        }));
      })
      .catch((e: unknown) => {
        if (!cancelled) setState((s) => ({ ...s, error: String(e) }));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, applyProject]);

  const apply = useCallback(
    (op: Op) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      if (jobActive(state.autoJob)) {
        setState((s) => ({
          ...s,
          error:
            "Le plan est en cours de construction : il sera modifiable dans quelques secondes.",
        }));
        return;
      }
      void run(async () => {
        const result = await api.applyOp(project.id, levelId, op);
        setState((s) => ({
          ...s,
          project: result.project,
          derived: result.derived,
          report: result.report,
          selection: pruneSelection(s.selection, result.project, s.levelId),
        }));
      });
    },
    [state, run],
  );

  const undo = useCallback(() => {
    if (!state.project) return;
    const id = state.project.id;
    void run(async () => applyProject(await api.undo(id)));
  }, [state.project, run, applyProject]);

  const redo = useCallback(() => {
    if (!state.project) return;
    const id = state.project.id;
    void run(async () => applyProject(await api.redo(id)));
  }, [state.project, run, applyProject]);

  const select = useCallback((selection: Selection) => {
    setState((s) => ({ ...s, selection }));
  }, []);

  const addMeasurement = useCallback(
    (body: MeasurementCreate) => {
      if (!state.project) return;
      const id = state.project.id;
      void run(async () => applyProject(await api.addMeasurement(id, body)));
    },
    [state.project, run, applyProject],
  );

  const removeMeasurement = useCallback(
    (measurementId: string) => {
      if (!state.project) return;
      const id = state.project.id;
      void run(async () =>
        applyProject(await api.removeMeasurement(id, measurementId)),
      );
    },
    [state.project, run, applyProject],
  );

  const calibrate = useCallback(
    (dryRun: boolean) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      void run(async () => {
        const result = await api.calibrate(project.id, levelId, dryRun);
        setState((s) => ({ ...s, lastCalibration: result.calibration }));
        if (result.applied) await applyProject(result.project);
      });
    },
    [state, run, applyProject],
  );

  const updateSettings = useCallback(
    (body: ProjectSettingsUpdate) => {
      if (!state.project) return;
      const id = state.project.id;
      void run(async () => applyProject(await api.updateSettings(id, body)));
    },
    [state.project, run, applyProject],
  );

  /** Chemin « zéro geste » (V1, point 6) après le plan : ambiance, aménagement, trois vues,
   *  rendus et finition, en tâche de fond suivie toutes les 2 s. L'éditeur reste
   *  utilisable (pas de `busy`) ; la galerie se remplit au fil des étapes ; à la fin, le
   *  projet est relu (avec les gestes faits entre-temps) et l'atelier passe à Rendre. */
  const startAutopilot = useCallback(
    (pid: string, lid: string) => {
      let stage = "";
      const fail = (e: unknown) =>
        setState((s) => ({
          ...s,
          pilotJob: null,
          error: e instanceof ApiError ? e.message : String(e),
        }));
      const poll = async (jobId: string): Promise<void> => {
        const job = await inspirationApi.job(pid, jobId);
        setState((s) => ({ ...s, pilotJob: job }));
        if ((job.stage ?? "") !== stage && jobActive(job)) {
          stage = job.stage ?? "";
          const renders = await renderApi.list(pid);
          setState((s) => ({ ...s, renders }));
        }
        if (jobActive(job)) {
          window.setTimeout(() => void poll(jobId).catch(fail), 2000);
          return;
        }
        if (job.status === "done") {
          await applyProject(await api.getProject(pid));
          const renders = await renderApi.list(pid);
          setState((s) => ({
            ...s,
            renders,
            pilotRun: job.result as unknown as AutopilotRun,
            pilotArrival: s.pilotArrival + 1,
          }));
        } else if (job.status === "failed") {
          setState((s) => ({
            ...s,
            error: `Chemin automatique : ${job.error ?? "échec"}`,
          }));
        }
      };
      pilotApi
        .start(pid, lid)
        .then((job) => {
          setState((s) => ({ ...s, pilotJob: job }));
          return poll(job.id);
        })
        .catch(fail);
    },
    [applyProject],
  );

  /** Construction automatique du plan depuis une image : tâche de fond suivie chaque
   *  seconde ; la page reste utilisable, le plan arrive dès qu'il est prêt. */
  const autoplan = useCallback(
    (
      pid: string,
      lid: string,
      imageId: string,
      base: number,
      kind?: "photo" | "plan",
    ) => {
      const fail = (e: unknown) =>
        setState((s) => ({
          ...s,
          autoJob: null,
          error: e instanceof ApiError ? e.message : String(e),
        }));
      const poll = async (jobId: string): Promise<void> => {
        const job = await inspirationApi.job(pid, jobId);
        setState((s) => ({ ...s, autoJob: job }));
        if (jobActive(job)) {
          window.setTimeout(() => void poll(jobId).catch(fail), 1000);
        } else if (job.status === "done") {
          const result = job.result as AutoPlanResult;
          await applyProject(result.run.project);
          setState((s) => ({
            ...s,
            lastAuto: result,
            autoBase: base,
            autoDone: false,
            lastRecalibration: null,
            planPreview: null,
            planCursor: null,
            arrival: s.arrival + 1,
          }));
          if (
            result.run.method !== "none" &&
            result.run.project.render.auto_path &&
            currentCapabilities().renders
          )
            startAutopilot(pid, lid);
        } else if (job.status === "failed") {
          setState((s) => ({
            ...s,
            error: `Construction du plan : ${job.error ?? "échec"}`,
          }));
        }
      };
      autoplanApi
        .start(pid, lid, imageId, kind)
        .then((job) => {
          setState((s) => ({ ...s, autoJob: job, error: null }));
          return poll(job.id);
        })
        .catch(fail);
    },
    [applyProject, startAutopilot],
  );

  /** Plan vectoriel (DXF, PDF, SVG) : lu puis appliqué tout de suite avec les calques
   *  retenus. `correction` : relecture du même plan (autre échelle papier, autres
   *  calques) ; les murs sont refaits tant que rien n'a été modifié depuis. */
  const importPlan = useCallback(
    (
      file: File,
      options: {
        paperScale?: number;
        layers?: string[];
        correction?: boolean;
      } = {},
    ) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      const level = project.levels[levelId];
      const replace =
        state.planCursor !== null &&
        project.history_cursor === state.planCursor;
      lastPlanFile.current = file;
      void run(async () => {
        if (level && walls(level).length > 0 && !replace)
          throw new ApiError(
            "Ce projet a déjà des murs : un plan ne s'importe que dans un projet vide (annulez les murs, ou créez un autre projet).",
            409,
          );
        const preview = await planApi.preview(
          project.id,
          levelId,
          file,
          options.paperScale,
        );
        const names =
          options.layers ??
          preview.layers.filter((l) => l.selected).map((l) => l.name);
        const segments = preview.segments.filter((s) =>
          names.includes(s.layer),
        );
        if (segments.length === 0)
          throw new ApiError(
            "Aucun trait de mur trouvé dans ce plan : vérifiez le fichier.",
            409,
          );
        if (replace) await api.undo(project.id);
        const result = await planApi.apply(project.id, levelId, {
          segments,
          kind: preview.kind,
          source: "observed",
        });
        setState((s) => ({
          ...s,
          project: result.project,
          derived: result.derived,
          report: result.report,
          planPreview: preview,
          planLayers: names,
          planCursor: result.project.history_cursor,
          lastAuto: null,
          arrival: options.correction ? s.arrival : s.arrival + 1,
        }));
        if (
          !options.correction &&
          result.project.render.auto_path &&
          currentCapabilities().renders
        )
          startAutopilot(project.id, levelId);
      });
    },
    [state, run, startAutopilot],
  );

  /** Tout fichier déposé construit le plan : photo ou image de plan (chaîne automatique
   *  en tâche de fond), plan vectoriel (lu et appliqué). Aucune question. */
  const importFile = useCallback(
    (file: File) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      if (jobActive(state.autoJob)) return;
      if (isPlanFile(file)) {
        importPlan(file);
        return;
      }
      void run(async () => {
        const result = await api.uploadImage(project.id, file);
        setState((s) => ({
          ...s,
          imageWarnings: {
            ...s.imageWarnings,
            [result.image.id]: result.warnings,
          },
        }));
        await applyProject(result.project);
        // En ligne sans estimation de profondeur : la photo est rangée, le plan ne se
        // construit pas depuis elle (« Plan depuis photo : bientôt en ligne »).
        if (!currentCapabilities().photoPlan) {
          setState((s) => ({ ...s, arrival: s.arrival + 1 }));
          return;
        }
        autoplan(
          project.id,
          levelId,
          result.image.id,
          result.project.history_cursor,
        );
      });
    },
    [state, run, applyProject, autoplan, importPlan],
  );

  /** Les exigences telles qu'elles sont dans le compositeur au moment du clic. */
  /** « Générer le plan » depuis le cadre Plan vide (T3) : une photo du projet → plan estimé
   *  puis chemin automatique ; sinon la note Exigences (texte, réponses) → plan proposé ou
   *  plan de départ (L3, L1b) puis chemin automatique ; sinon rien à lire, et on le dit. */
  const generatePlan = useCallback(
    (fresh?: Requirements) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      if (jobActive(state.autoJob) || jobActive(state.pilotJob)) return;
      const photo = Object.keys(project.images)[0];
      if (photo && currentCapabilities().photoPlan) {
        autoplan(project.id, levelId, photo, project.history_cursor);
        return;
      }
      // Les exigences du compositeur au moment du clic, sinon celles du projet.
      const text = fresh ? fresh.text : (project.pending_instruction ?? "");
      const answers = (
        fresh
          ? hasAnswers(fresh.answers)
            ? fresh.answers
            : undefined
          : (project.pending_answers ?? undefined)
      ) as GuidedAnswers | undefined;
      if (!text && !answers) {
        setState((s) => ({
          ...s,
          error:
            "Rien à lire pour générer le plan : déposez une photo de la pièce ou remplissez la note Exigences (surface, logement, envies), ou tracez le plan vous-même.",
        }));
        return;
      }
      void run(async () => {
        const result = await api.programme(project.id, levelId, text, answers);
        await applyProject(result.project);
        setState((s) => ({ ...s, arrival: s.arrival + 1 }));
        if (result.kind === "none")
          throw new ApiError(
            "Sans surface ni logement décrit, pas de plan à proposer : donnez une surface dans la note Exigences, déposez une photo, ou tracez la pièce.",
            409,
          );
        if (result.project.render.auto_path && currentCapabilities().renders)
          startAutopilot(project.id, levelId);
      });
    },
    [state, run, applyProject, autoplan, startAutopilot],
  );

  /** Une photo déposée sur le tableau (T4) : projet sans plan et bascule « générer au dépôt
   *  d'une photo » active → construction du plan puis chemin automatique, sans autre geste. */
  const planFromPhoto = useCallback(
    (project: Project, imageId: string) => {
      const { levelId } = state;
      if (!levelId || !project.render.auto_on_drop) return;
      const level = project.levels[levelId];
      if (!level || walls(level).length > 0) return;
      if (jobActive(state.autoJob) || jobActive(state.pilotJob)) return;
      if (!currentCapabilities().photoPlan) return; // photo rangée, plan bientôt en ligne
      autoplan(project.id, levelId, imageId, project.history_cursor);
    },
    [state, autoplan],
  );

  /** « Générer maintenant » (T4) : sans plan, comme « Générer le plan » ; avec un plan, le
   *  chemin automatique (ambiance, aménagement, vues, rendus). */
  const generateNow = useCallback(
    (fresh?: Requirements) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      if (jobActive(state.autoJob) || jobActive(state.pilotJob)) return;
      const level = project.levels[levelId];
      if (!level || walls(level).length === 0) generatePlan(fresh);
      else if (currentCapabilities().renders)
        startAutopilot(project.id, levelId);
    },
    [state, generatePlan, startAutopilot],
  );

  /** « Ce n'est pas une photo » / « ce n'est pas un plan » : ce que la construction a
   *  fait est annulé, puis l'image est relue autrement. Possible tant que le plan n'a
   *  pas été modifié depuis. */
  const rereadAs = useCallback(
    (kind: "photo" | "plan") => {
      const { project, levelId, lastAuto, autoBase } = state;
      if (!project || !levelId || !lastAuto || autoBase === null) return;
      if (jobActive(state.autoJob)) return;
      void run(async () => {
        if (project.history_cursor !== lastAuto.run.project.history_cursor)
          throw new ApiError(
            "Le plan a été modifié depuis : corrigez-le dans le plan, ou annulez vos modifications d'abord.",
            409,
          );
        let current = project;
        while (current.history_cursor > autoBase)
          current = await api.undo(project.id);
        await applyProject(current);
        setState((s) => ({ ...s, lastAuto: null }));
        autoplan(project.id, levelId, lastAuto.run.image_id, autoBase, kind);
      });
    },
    [state, run, applyProject, autoplan],
  );

  /** Corrections de la lecture du dernier plan : échelle papier, calques. */
  const rescaleImportedPlan = useCallback(
    (paperScale: number) => {
      if (lastPlanFile.current)
        importPlan(lastPlanFile.current, {
          paperScale,
          layers: state.planLayers,
          correction: true,
        });
    },
    [importPlan, state.planLayers],
  );
  const relayerImportedPlan = useCallback(
    (layers: string[]) => {
      if (lastPlanFile.current)
        importPlan(lastPlanFile.current, {
          paperScale: state.planPreview?.paper_scale ?? undefined,
          layers,
          correction: true,
        });
    },
    [importPlan, state.planPreview],
  );

  const removeImage = useCallback(
    (imageId: string) => {
      if (!state.project) return;
      const id = state.project.id;
      void run(async () => applyProject(await api.deleteImage(id, imageId)));
    },
    [state.project, run, applyProject],
  );

  /** Perception par le modèle IA : ne modifie pas la scène, seulement l'état local. */
  const perceive = useCallback(
    (imageId: string) => {
      if (!state.project) return;
      const id = state.project.id;
      void run(async () => {
        const result = await aiApi.perceive(id, imageId);
        setState((s) => ({ ...s, lastPerception: result }));
      });
    },
    [state.project, run],
  );

  /** Aperçu de ce qui partirait au modèle pour le brief d'aménagement : aucun envoi. */
  const previewBrief = useCallback(
    (body: IntentionBody) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      void run(async () => {
        const preview = await aiApi.briefPreview(project.id, levelId, body);
        setState((s) => ({ ...s, briefPreview: preview }));
      });
    },
    [state, run],
  );

  /** Rôle Intention : le brief (meubles, priorités, préférences ; aucune position) ; la
   *  scène ne change pas. */
  const requestBrief = useCallback(
    (body: IntentionBody) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      void run(async () => {
        const result = await aiApi.brief(project.id, levelId, body);
        setState((s) => ({ ...s, lastBrief: result }));
      });
    },
    [state, run],
  );

  /** Générateur local (3.4) : variantes calculées depuis le brief relu ; la scène ne change
   *  pas, la première variante est montrée en fantôme. */
  const proposeLayouts = useCallback(
    (body: LayoutBody) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      void run(async () => {
        const result = await layoutApi.propose(project.id, levelId, body);
        setState((s) => ({
          ...s,
          lastLayouts: result,
          ghost: result.proposals[0] ?? null,
          lastCritique: null,
          compare: null,
        }));
      });
    },
    [state, run],
  );

  const previewProposal = useCallback(
    (proposal: LayoutProposal | null) =>
      setState((s) => ({ ...s, ghost: proposal })),
    [],
  );

  /** Rôle Critique (3.5) : remarques sur une variante (règles locales, puis modèle si
   *  autorisé) ; la variante critiquée passe en fantôme ; rien n'est appliqué. */
  const critiqueProposal = useCallback(
    (body: CritiqueBody) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      void run(async () => {
        const result = await layoutApi.critique(project.id, levelId, body);
        setState((s) => ({ ...s, lastCritique: result, ghost: body.proposal }));
      });
    },
    [state, run],
  );

  /** Action d'une remarque : la variante révisée remplace l'ancienne dans la liste et en
   *  fantôme, à relire ; « Appliquer » reste un geste séparé. */
  const reviseProposal = useCallback(
    (body: ReviseBody) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      void run(async () => {
        const revised = await layoutApi.revise(project.id, levelId, body);
        setState((s) => ({
          ...s,
          lastLayouts: s.lastLayouts
            ? {
                ...s.lastLayouts,
                proposals: s.lastLayouts.proposals.map((p) =>
                  p.index === revised.index ? revised : p,
                ),
              }
            : s.lastLayouts,
          ghost: revised,
          lastCritique: null,
        }));
      });
    },
    [state, run],
  );

  /** Comparaison côte à côte (3.6) : la seconde variante est dessinée en vert sur le plan. */
  const compareProposal = useCallback(
    (proposal: LayoutProposal | null) =>
      setState((s) => ({ ...s, compare: proposal })),
    [],
  );

  /** Glisser-déposer d'un meuble : les règles à la position essayée, sans rien modifier ;
   *  null si la vérification n'a pas pu être faite (le dépôt suit alors les invariants). */
  const checkFurniture = useCallback(
    (
      id: string,
      position: { x: number; y: number },
      yaw: number,
      size?: { width: number; depth: number },
    ): Promise<FurnitureMoveCheck | null> => {
      const { project, levelId } = state;
      if (!project || !levelId) return Promise.resolve(null);
      return layoutApi
        .checkMove(project.id, levelId, id, { position, yaw, ...size })
        .catch(() => null);
    },
    [state],
  );
  /** Rotation d'un escalier ou retournement d'une porte (E4) : règles dans l'état essayé. */
  const checkElement = useCallback(
    (
      id: string,
      trial: {
        yaw?: number;
        width?: number;
        position?: { x: number; y: number };
        swing?: {
          hinge: "left" | "right";
          direction: "inward" | "outward";
          angle?: number;
        };
      },
    ): Promise<FurnitureMoveCheck | null> => {
      const { project, levelId } = state;
      if (!project || !levelId) return Promise.resolve(null);
      return layoutApi
        .checkElement(project.id, levelId, id, trial)
        .catch(() => null);
    },
    [state],
  );

  /** Bibliothèque : le meuble choisi, essayé sous le curseur avant d'être posé. */
  const checkPlacement = useCallback(
    (
      assetId: string,
      position: { x: number; y: number },
      yaw: number,
    ): Promise<FurnitureMoveCheck | null> => {
      const { project, levelId } = state;
      if (!project || !levelId) return Promise.resolve(null);
      return layoutApi
        .checkPlace(project.id, levelId, { asset_id: assetId, position, yaw })
        .catch(() => null);
    },
    [state],
  );

  /** « Appliquer cette variante » : geste utilisateur, meubles `generated`, annulable. */
  const applyProposal = useCallback(
    (proposal: LayoutProposal) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      void run(async () => {
        const result = await layoutApi.apply(project.id, levelId, proposal);
        setState((s) => ({
          ...s,
          project: result.project,
          derived: result.derived,
          report: result.report,
          ghost: null,
          compare: null,
        }));
      });
    },
    [state, run],
  );

  /** Image d'inspiration locale : tâche de fond côté serveur, suivie toutes les 2 s. La page
   *  reste utilisable (pas de `busy`) et rien n'attend des minutes dans une requête. */
  const inspire = useCallback(
    (imageId: string, prompt: string, seed: number, steps: number) => {
      if (!state.project || jobActive(state.inspirationJob)) return;
      const id = state.project.id;
      const poll = async (jobId: string): Promise<void> => {
        const job = await inspirationApi.job(id, jobId);
        setState((s) => ({ ...s, inspirationJob: job }));
        if (jobActive(job)) {
          window.setTimeout(() => void poll(jobId).catch(fail), 2000);
        } else if (job.status === "done") {
          setState((s) => ({
            ...s,
            lastInspiration: job.result as InspirationRun,
          }));
        } else if (job.status === "failed") {
          setState((s) => ({
            ...s,
            error: `Image d'inspiration : ${job.error ?? "échec"}`,
          }));
        }
      };
      const fail = (e: unknown) =>
        setState((s) => ({
          ...s,
          inspirationJob: null,
          error: e instanceof ApiError ? e.message : String(e),
        }));
      inspirationApi
        .start(id, imageId, prompt, seed, steps)
        .then((job) => {
          setState((s) => ({ ...s, inspirationJob: job, error: null }));
          return poll(job.id);
        })
        .catch(fail);
    },
    [state.project, state.inspirationJob],
  );

  /** Galerie des rendus (écran Rendre). */
  /** Vue d'architecte (D3) : le serveur la calcule, l'ajout passe par le chemin habituel
   *  des opérations (geste de l'utilisateur, annulable). */
  const addArchitectView = useCallback(() => {
    const { project, levelId } = state;
    if (!project || !levelId) return;
    void run(async () => {
      const op = await renderApi.architectView(project.id, levelId);
      const result = await api.applyOp(project.id, levelId, op);
      setState((s) => ({
        ...s,
        project: result.project,
        derived: result.derived,
        report: result.report,
      }));
    });
  }, [state, run]);

  /** Vues automatiques (V1) : ensemble, zone, détail, en un pas d'historique annulable ; les
   *  vues déplacées à la main sont gardées ; renvoie le compte rendu (vues manquées…). */
  const addAutoViews = useCallback(
    (aspect: Aspect | null = null): Promise<AutoViews | undefined> => {
      const { project, levelId } = state;
      if (!project || !levelId) return Promise.resolve(undefined);
      return run(async () => {
        const result = await renderApi.autoViews(project.id, levelId, aspect);
        setState((s) => ({
          ...s,
          project: result.project,
          derived: result.derived,
          report: result.report,
        }));
        return result.views;
      });
    },
    [state, run],
  );

  /** Consigne libre → ambiance de la pièce (V1, point 5) : traduite par le modèle configuré
   *  ou les phrases simples, appliquée en un pas annulable ; renvoie ce qui a été compris. */
  const translateInstruction = useCallback(
    (roomId: string, text: string): Promise<InstructionOutcome | undefined> => {
      const { project, levelId } = state;
      if (!project || !levelId) return Promise.resolve(undefined);
      return run(async () => {
        const result = await instructionApi.apply(
          project.id,
          levelId,
          roomId,
          text,
        );
        setState((s) => ({
          ...s,
          project: result.project,
          derived: result.derived,
          report: result.report,
        }));
        return {
          translation: result.translation,
          understanding: result.understanding,
          notice: result.notice,
        };
      });
    },
    [state, run],
  );

  /** « Rendre toutes les vues » (V1) : une tâche de fond ; la galerie se remplit vue après
   *  vue (rechargée à chaque changement d'étape). */
  const renderAll = useCallback(
    (samples: number, gpu: boolean, daylight: string | null = null) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      followRenderJob(project.id, "Rendre toutes les vues", () =>
        renderApi.renderAll(project.id, levelId, samples, gpu, daylight),
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state],
  );

  /** « Re-rendre les n vues » périmées (P2) : même suivi que « Rendre toutes les vues ». */
  const renderViews = useCallback(
    (cameraIds: string[], onLevel?: string) => {
      const { project } = state;
      // T7e : les vues d'un autre plan (cadre « Rendus · nom ») se refont sur leur plan.
      const levelId = onLevel ?? state.levelId;
      if (!project || !levelId || cameraIds.length === 0) return;
      followRenderJob(project.id, "Re-rendre les vues", () =>
        renderApi.renderViews(project.id, levelId, cameraIds),
      );
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state],
  );

  /** Une tâche de rendus en série : suivie toutes les 2 s, la galerie rechargée à chaque vue. */
  function followRenderJob(
    id: string,
    label: string,
    start: () => Promise<JobStatus>,
  ): void {
    if (jobActive(state.renderJob)) return;
    let stage = "";
    const fail = (e: unknown) =>
      setState((s) => ({
        ...s,
        renderJob: null,
        error: e instanceof ApiError ? e.message : String(e),
      }));
    const poll = async (jobId: string): Promise<void> => {
      const job = await inspirationApi.job(id, jobId);
      setState((s) => ({ ...s, renderJob: job }));
      if ((job.stage ?? "") !== stage || !jobActive(job)) {
        stage = job.stage ?? "";
        const renders = await renderApi.list(id);
        setState((s) => ({ ...s, renders }));
      }
      if (jobActive(job)) {
        window.setTimeout(() => void poll(jobId).catch(fail), 2000);
      } else if (job.status === "failed") {
        setState((s) => ({
          ...s,
          error: `${label} : ${job.error ?? "échec"}`,
        }));
      }
    };
    start()
      .then((job) => {
        setState((s) => ({ ...s, renderJob: job, error: null }));
        return poll(job.id);
      })
      .catch(fail);
  }

  /** Perspective de coupe (E3b) : même chemin que la vue d'architecte. */
  const addSectionView = useCallback(
    (axis: "x" | "y", value: number) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      void run(async () => {
        const op = await renderApi.sectionView(
          project.id,
          levelId,
          axis,
          value,
        );
        const result = await api.applyOp(project.id, levelId, op);
        setState((s) => ({
          ...s,
          project: result.project,
          derived: result.derived,
          report: result.report,
        }));
      });
    },
    [state, run],
  );

  /** Finition IA d'un rendu (D5) : geste explicite, l'image Blender reste ; la galerie se
   *  recharge à la fin. */
  const finishRender = useCallback(
    (runId: string, strength: number, style: FinishStyle | null) => {
      const { project } = state;
      if (!project || jobActive(state.renderJob)) return;
      const id = project.id;
      const fail = (e: unknown) =>
        setState((s) => ({
          ...s,
          renderJob: null,
          error: e instanceof ApiError ? e.message : String(e),
        }));
      const poll = async (jobId: string): Promise<void> => {
        const job = await inspirationApi.job(id, jobId);
        setState((s) => ({ ...s, renderJob: job }));
        if (jobActive(job)) {
          window.setTimeout(() => void poll(jobId).catch(fail), 2000);
        } else if (job.status === "done") {
          const renders = await renderApi.list(id);
          setState((s) => ({ ...s, renders }));
        } else if (job.status === "failed") {
          setState((s) => ({
            ...s,
            error: `Finition IA : ${job.error ?? "échec"}`,
          }));
        }
      };
      renderApi
        .finish(id, runId, { strength, style })
        .then((job) => {
          setState((s) => ({ ...s, renderJob: job, error: null }));
          return poll(job.id);
        })
        .catch(fail);
    },
    [state],
  );

  /** « Version catalogue » d'une vue (V1.7b) : à la demande, tâche de fond ; la galerie se
   *  recharge à la fin. Jamais lancée par le chemin automatique. */
  const catalogueRender = useCallback(
    (runId: string) => {
      const { project } = state;
      if (!project || jobActive(state.renderJob)) return;
      const id = project.id;
      const fail = (e: unknown) =>
        setState((s) => ({
          ...s,
          renderJob: null,
          error: e instanceof ApiError ? e.message : String(e),
        }));
      const poll = async (jobId: string): Promise<void> => {
        const job = await inspirationApi.job(id, jobId);
        setState((s) => ({ ...s, renderJob: job }));
        if (jobActive(job)) {
          window.setTimeout(() => void poll(jobId).catch(fail), 2000);
        } else if (job.status === "done") {
          const renders = await renderApi.list(id);
          setState((s) => ({ ...s, renders }));
        } else if (job.status === "failed") {
          setState((s) => ({
            ...s,
            error: `Version catalogue : ${job.error ?? "échec"}`,
          }));
        }
      };
      renderApi
        .catalogue(id, runId)
        .then((job) => {
          setState((s) => ({ ...s, renderJob: job, error: null }));
          return poll(job.id);
        })
        .catch(fail);
    },
    [state],
  );

  const refreshRenders = useCallback(() => {
    if (!state.project) return;
    const id = state.project.id;
    renderApi.list(id).then(
      (renders) => setState((s) => ({ ...s, renders })),
      () => undefined,
    );
  }, [state.project]);

  /** B2a : relire le projet et ses rendus (un autre membre l'a changé, ou retour après une
   *  coupure). La règle « jamais en arrière » d'`applyProject` garde l'écran cohérent. */
  const reload = useCallback(() => {
    if (!state.project) return;
    const id = state.project.id;
    api.getProject(id).then(
      (project) => void applyProject(project),
      () => undefined,
    );
    renderApi.list(id).then(
      (renders) => setState((s) => ({ ...s, renders })),
      () => undefined,
    );
  }, [state.project, applyProject]);

  /** Rendu Blender depuis un point de vue : tâche de fond côté serveur, suivie toutes les
   *  2 s ; la page reste utilisable ; le résultat rejoint la galerie. */
  const startRender = useCallback(
    (
      cameraId: string,
      samples: number,
      gpu: boolean,
      daylight: string | null = null,
      section: { axis: string; value: number } | null = null,
    ) => {
      const { project, levelId } = state;
      if (!project || !levelId || jobActive(state.renderJob)) return;
      const id = project.id;
      const fail = (e: unknown) =>
        setState((s) => ({
          ...s,
          renderJob: null,
          error: e instanceof ApiError ? e.message : String(e),
        }));
      const poll = async (jobId: string): Promise<void> => {
        const job = await inspirationApi.job(id, jobId);
        setState((s) => ({ ...s, renderJob: job }));
        if (jobActive(job)) {
          window.setTimeout(() => void poll(jobId).catch(fail), 2000);
        } else if (job.status === "done") {
          const renders = await renderApi.list(id);
          setState((s) => ({ ...s, renders }));
        } else if (job.status === "failed") {
          setState((s) => ({
            ...s,
            error: `Rendu : ${job.error ?? "échec"}`,
          }));
        }
      };
      renderApi
        .start(id, levelId, cameraId, samples, gpu, daylight, section)
        .then((job) => {
          setState((s) => ({ ...s, renderJob: job, error: null }));
          return poll(job.id);
        })
        .catch(fail);
    },
    [state],
  );

  /** Vignette Blender d'une variante (3.7) : tâche de fond, même suivi que les rendus ;
   *  la variante n'est pas appliquée ; le résultat rejoint la galerie avec son numéro. */
  const renderProposal = useCallback(
    (proposal: LayoutProposal, samples = 32) => {
      const { project, levelId } = state;
      if (!project || !levelId || jobActive(state.renderJob)) return;
      const id = project.id;
      const fail = (e: unknown) =>
        setState((s) => ({
          ...s,
          renderJob: null,
          error: e instanceof ApiError ? e.message : String(e),
        }));
      const poll = async (jobId: string): Promise<void> => {
        const job = await inspirationApi.job(id, jobId);
        setState((s) => ({ ...s, renderJob: job }));
        if (jobActive(job)) {
          window.setTimeout(() => void poll(jobId).catch(fail), 2000);
        } else if (job.status === "done") {
          const renders = await renderApi.list(id);
          setState((s) => ({ ...s, renders }));
        } else if (job.status === "failed") {
          setState((s) => ({
            ...s,
            error: `Vignette : ${job.error ?? "échec"}`,
          }));
        }
      };
      layoutApi
        .render(id, levelId, { proposal, samples })
        .then((job) => {
          setState((s) => ({ ...s, renderJob: job, error: null }));
          return poll(job.id);
        })
        .catch(fail);
    },
    [state],
  );

  /** « Garder cette photo » : le mur le moins sûr suit ce que la photo a vu. */
  const adoptView = useCallback(
    (cameraId: string) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      void run(async () => {
        const result = await api.adoptView(project.id, levelId, cameraId);
        setState((s) => ({
          ...s,
          project: result.project,
          derived: result.derived,
          report: result.report,
          selection: pruneSelection(s.selection, result.project, s.levelId),
        }));
      });
    },
    [state, run],
  );

  /** « Tourner la vue » : un quart de tour de plus pour la caméra d'une photo (pièce
   *  symétrique) ; les murs ne bougent pas, la cohérence est recontrôlée. */
  const rotateView = useCallback(
    (cameraId: string) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      void run(async () => {
        const result = await api.rotateView(project.id, levelId, cameraId);
        setState((s) => ({
          ...s,
          project: result.project,
          derived: result.derived,
          report: result.report,
          selection: pruneSelection(s.selection, result.project, s.levelId),
        }));
      });
    },
    [state, run],
  );

  /** « Recalibrer » : deux points, un mur ou la hauteur sous plafond ; tout le niveau
   *  (fond compris) est mis à l'échelle, le facteur est annoncé, le geste s'annule. */
  const recalibrate = useCallback(
    (body: RecalibrateBody) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      void run(async () => {
        const result = await recalibrateApi.apply(project.id, levelId, body);
        setState((s) => ({
          ...s,
          project: result.project,
          derived: result.derived,
          report: result.report,
          lastRecalibration: result.summary,
          autoDone: true,
          selection: pruneSelection(s.selection, result.project, s.levelId),
        }));
      });
    },
    [state, run],
  );
  /** R3 : longueur, largeur, hauteur de la pièce ; même suite que « Recalibrer ». */
  const recalibrateDimensions = useCallback(
    (body: DimensionsBody) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      void run(async () => {
        const result = await recalibrateApi.dimensions(
          project.id,
          levelId,
          body,
        );
        setState((s) => ({
          ...s,
          project: result.project,
          derived: result.derived,
          report: result.report,
          lastRecalibration: result.summary,
          autoDone: true,
          selection: pruneSelection(s.selection, result.project, s.levelId),
        }));
      });
    },
    [state, run],
  );

  /** Aperçu des dimensions (aucune écriture) ; l'erreur (valeur invraisemblable) est rendue
   *  telle quelle pour être affichée dans la carte. */
  const previewDimensions = useCallback(
    async (body: DimensionsBody): Promise<DimensionsSummary | string> => {
      const { project, levelId } = state;
      if (!project || !levelId) return "pas de niveau ouvert";
      try {
        return await recalibrateApi.previewDimensions(
          project.id,
          levelId,
          body,
        );
      } catch (e) {
        return e instanceof ApiError ? e.message : String(e);
      }
    },
    [state],
  );

  /** Une phrase à l'assistant : il agit sur le plan (commits « assistant »), le tour
   *  est ajouté à la conversation, les éléments touchés sont surlignés. */
  const askAssistant = useCallback(
    (text: string, contextId: string | null = null) => {
      const { project, levelId } = state;
      if (!project || !levelId) return;
      const id = Date.now();
      setState((s) => ({
        ...s,
        assistant: [...s.assistant, { id, role: "user", text }],
      }));
      void run(async () => {
        // Assistant unifié (P5) : ambiance de la pièce en contexte et gestes, en un tour.
        const result = await assistantApi.say(
          project.id,
          levelId,
          text,
          contextId,
          state.assistantLast,
        );
        const turn = result.turn;
        // Mémoire courte : le meuble touché ou visé par ce tour.
        const cited =
          turn.parts
            .flatMap((p) => p.element_ids ?? [])
            .find(
              (eid) =>
                result.project.levels[levelId]?.elements[eid]?.type ===
                "furniture",
            ) ?? null;
        const layouts = turn.assistant?.layouts ?? null;
        setState((s) => ({
          ...s,
          project: result.project,
          derived: result.derived,
          report: result.report,
          assistant: [
            ...s.assistant,
            { id: id + 1, role: "assistant", text: "", say: turn },
          ],
          assistantHighlight: turn.highlighted,
          assistantUnderstanding: turn.reader,
          assistantGemini: {
            case: result.gemini_case,
            remote: result.remote_model,
          },
          assistantLast: cited ?? s.assistantLast,
          lastLayouts: layouts ?? s.lastLayouts,
          ghost: layouts?.proposals[0] ?? s.ghost,
          selection: pruneSelection(s.selection, result.project, s.levelId),
        }));
      });
    },
    [state, run],
  );

  // Le fil de l'assistant (P5) : relu du journal local à l'ouverture du projet.
  const threadFor = state.project?.id ?? null;
  useEffect(() => {
    if (!threadFor) return;
    let cancelled = false;
    assistantApi.thread(threadFor).then(
      (thread) => {
        if (cancelled) return;
        const base = Date.now() - thread.turns.length * 2;
        const read: AssistantMessage[] = thread.turns.flatMap((t, i) => [
          { id: base + 2 * i, role: "user" as const, text: t.text },
          {
            id: base + 2 * i + 1,
            role: "assistant" as const,
            text: "",
            fromJournal: true,
            say: {
              text: t.text,
              parts: t.parts,
              reader: t.by && t.by !== "phrases" ? "model" : "phrases",
              model_id: t.by && t.by !== "phrases" ? t.by : null,
              notice: "",
              committed: 0,
              room_id: null,
              highlighted: [],
              assistant: null,
            },
          },
        ]);
        setState((s) => ({
          ...s,
          assistant: s.assistant.length ? s.assistant : read,
          assistantJournal: thread.journal_enabled,
        }));
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [threadFor]);

  /** « Annuler » sur un message : autant d'annulations que de gestes de ce tour. */
  const undoAssistant = useCallback(
    (message: AssistantMessage) => {
      const { project } = state;
      const count = message.say?.committed ?? message.turn?.committed ?? 0;
      if (!project || count === 0) return;
      const id = project.id;
      void run(async () => {
        let current = project;
        for (let i = 0; i < count; i++) current = await api.undo(id);
        await applyProject(current);
        setState((s) => ({
          ...s,
          assistant: s.assistant.map((m) =>
            m.id === message.id ? { ...m, undone: true } : m,
          ),
          assistantHighlight: [],
        }));
      });
    },
    [state, run, applyProject],
  );
  /** Aperçu de ce qui partirait au modèle pour une phrase : aucun envoi. */
  const previewAssistant = useCallback(
    async (text: string) => {
      const { project, levelId } = state;
      if (!project || !levelId || !text) return null;
      try {
        return await assistantApi.preview(project.id, levelId, text);
      } catch {
        return null;
      }
    },
    [state],
  );
  const clearAssistantHighlight = useCallback(
    () =>
      setState((s) =>
        s.assistantHighlight.length ? { ...s, assistantHighlight: [] } : s,
      ),
    [],
  );
  const showAssistantProposals = useCallback((message: AssistantMessage) => {
    const layouts = message.say?.assistant?.layouts ?? message.turn?.layouts;
    if (!layouts) return;
    setState((s) => ({
      ...s,
      lastLayouts: layouts,
      ghost: layouts.proposals[0] ?? null,
      lastCritique: null,
      compare: null,
    }));
  }, []);

  const clearRecalibration = useCallback(
    () => setState((s) => ({ ...s, lastRecalibration: null })),
    [],
  );

  const clearError = useCallback(
    () => setState((s) => ({ ...s, error: null })),
    [],
  );

  /** « Annuler le recalage » (L2) : le plan revient à l'échelle estimée, en un pas ; la
   *  ligne « Surface » du contrôle le dit. */
  const undoAreaGuard = useCallback(() => {
    const { project, levelId } = state;
    if (!project || !levelId) return;
    void run(async () => {
      const after = await api.areaGuardUndo(project.id, levelId);
      await applyProject(after);
      setState((s) => ({
        ...s,
        pilotRun: s.pilotRun
          ? {
              ...s.pilotRun,
              request: (s.pilotRun.request ?? []).map((i) =>
                i.label === "Surface"
                  ? {
                      ...i,
                      status: "partial",
                      detail: "recalage annulé : plan à l'échelle estimée",
                    }
                  : i,
              ),
            }
          : s.pilotRun,
      }));
    });
  }, [state, run, applyProject]);

  /** File d'attente : annule ce qui attend son tour (construction du plan, chemin
   *  automatique, rendus, image d'inspiration) ; ce qui calcule déjà continue. */
  const cancelQueued = useCallback(() => {
    const { project } = state;
    if (!project) return;
    const waiting = [
      state.autoJob,
      state.pilotJob,
      state.renderJob,
      state.inspirationJob,
    ].filter((j): j is JobStatus => j?.status === "queued");
    for (const job of waiting)
      void inspirationApi.cancel(project.id, job.id).then(
        (cancelled) =>
          setState((s) => ({
            ...s,
            autoJob: s.autoJob?.id === job.id ? cancelled : s.autoJob,
            pilotJob: s.pilotJob?.id === job.id ? cancelled : s.pilotJob,
            renderJob: s.renderJob?.id === job.id ? cancelled : s.renderJob,
            inspirationJob:
              s.inspirationJob?.id === job.id ? cancelled : s.inspirationJob,
          })),
        () => undefined, // déjà démarrée : elle se suit comme avant
      );
  }, [state]);

  return {
    state,
    actions: {
      cancelQueued,
      adoptProject: applyProject,
      undoAreaGuard,
      apply,
      undo,
      redo,
      select,
      addMeasurement,
      removeMeasurement,
      calibrate,
      updateSettings,
      importFile,
      generatePlan,
      planFromPhoto,
      generateNow,
      rereadAs,
      recalibrate,
      recalibrateDimensions,
      previewDimensions,
      clearRecalibration,
      askAssistant,
      previewAssistant,
      undoAssistant,
      clearAssistantHighlight,
      showAssistantProposals,
      rescaleImportedPlan,
      relayerImportedPlan,
      removeImage,
      perceive,
      previewBrief,
      requestBrief,
      proposeLayouts,
      previewProposal,
      applyProposal,
      critiqueProposal,
      reviseProposal,
      compareProposal,
      checkFurniture,
      checkElement,
      checkPlacement,
      inspire,
      adoptView,
      rotateView,
      refreshRenders,
      reload,
      finishRender,
      addArchitectView,
      addAutoViews,
      renderAll,
      renderViews,
      translateInstruction,
      startAutopilot,
      catalogueRender,
      addSectionView,
      openLevel,
      newPlan,
      startRender,
      renderProposal,
      clearError,
    },
  };
}

function pruneSelection(
  selection: Selection,
  project: Project,
  levelId: string | null,
): Selection {
  const level =
    (levelId ? project.levels[levelId] : undefined) ?? firstLevel(project);
  if (!level) return NO_SELECTION;
  if (selection.kind === "vertex" && !level.vertices[selection.id])
    return NO_SELECTION;
  if (selection.kind === "element" && !level.elements[selection.id])
    return NO_SELECTION;
  return selection;
}
