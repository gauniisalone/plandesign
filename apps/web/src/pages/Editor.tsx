import { useCallback, useEffect, useRef, useState } from "react";
import { api, jobActive, journalApi, type JobStatus } from "../api/client";
import type { Point2 } from "../api/types.gen";
import {
  PlanCanvas,
  type Background,
  type Placing,
  type Tool,
} from "../editor2d/PlanCanvas";
import type { Insets } from "../editor2d/view";
import { jobSentence } from "../importFiles";
import {
  firstLevel,
  renderLevelId,
  PLAN_STATUS_LABEL,
  planStatus,
  poseOf,
  readTransform,
  walls,
} from "../model";
import { roomForView } from "../refineView";
import { AiPanel } from "../panels/AiPanel";
import { AmenagerPanel } from "../panels/AmenagerPanel";
import { AssistantPanel } from "../panels/AssistantPanel";
import { AutoDiagnostic } from "../panels/AutoDiagnostic";
import { BlenderPanel } from "../panels/BlenderPanel";
import { ImportScreen } from "../panels/ImportScreen";
import {
  AmbianceDetails,
  Inspector,
  Legend,
  MeasurementsPanel,
} from "../panels/Inspector";
import { LibraryPanel } from "../panels/LibraryPanel";
import { PhotoPanel } from "../panels/PhotoPanel";
import { PhotoStrip } from "../panels/PhotoStrip";
import { RenderPanel } from "../panels/RenderPanel";
import { RecalibrateCard } from "../panels/RecalibrateCard";
import { AccountSection } from "../panels/AccountSection";
import {
  InstallState,
  SettingsPanel,
  ThemeSwitch,
} from "../panels/SettingsPanel";
import { ShareDialog } from "../panels/ShareDialog";
import { Bell } from "../panels/Bell";
import { ActivityPanel } from "../panels/ActivityPanel";
import { BoardSide } from "../panels/BoardSide";
import { takeBellTarget, useBellTargetToken } from "../state/bell";
import { useLive } from "../state/live";
import { useComments } from "../board/useComments";
import {
  authApi,
  leaseApi,
  setSpaceLeft,
  signalSignedOut,
} from "../api/client";
import {
  ReadOnlyContext,
  canEdit,
  useAccount,
  useRole,
} from "../state/account";
import { importGate } from "../state/roleGate";
import {
  BottomBar,
  type Step,
  type Tab,
  ToolPalette,
  TopBar,
  ViewBar,
  type ViewMode,
} from "../panels/Toolbar";
import { NewPlanDialog } from "../panels/NewPlanDialog";
import { RenderViewer } from "../panels/RenderViewer";
import { RenderStrip } from "../panels/RenderStrip";
import { LevelBar } from "../panels/LevelBar";
import type { PickAction } from "../panels/PickCard";
import { latestRenders, staleCameraIds } from "../staleViews";
import { takePendingNotices, takePendingPlan } from "../state/pendingArrival";
import { takePendingFile } from "../state/pendingFile";
import { useProject } from "../state/useProject";
import { BoardCanvas } from "../board/BoardCanvas";
import { ContextualPanel, type ContextualPick } from "../board/ContextualPanel";
import { useBoard } from "../board/useBoard";
import { IconClose } from "../ui/icons";
import {
  type CameraPreset,
  Viewer3D,
  type Viewer3DHandle,
  type ViewPose,
} from "../viewer3d/Viewer3D";
import {
  loadView3D,
  saveView3D,
  type SectionAxis,
  type View3D,
} from "../viewer3d/view3d";

interface Props {
  projectId: string;
  /** Étape demandée par l'adresse (`/import`) ; sinon déduite du projet à l'ouverture. */
  initialStep?: Step;
  onHome: () => void;
}

/** Onglet affiché ; null quand « Ajouter une source » est ouvert par-dessus. */
function tabOf(step: Step, viewMode: ViewMode): Tab | null {
  if (step === "board") return "board";
  if (step === "edit") return viewMode === "3d" ? "3d" : "plan";
  return null;
}

const KEY_TOOLS: Record<string, Tool> = {
  v: "select",
  h: "pan",
  w: "wall",
  d: "door",
  f: "window",
};

const KEY_PRESETS: Record<string, CameraPreset> = {
  "1": "top",
  "2": "front",
  "3": "side",
  "4": "iso",
};

/** Marges du canevas occupées par les barres flottantes (rangée du haut et barre de vues,
 *  colonne d'outils, barre du bas et bande des photos) : « Recadrer » place le plan dans la
 *  zone libre. Valeurs de départ, remplacées par la mesure des barres affichées. */
const CANVAS_INSETS: Insets = { top: 120, right: 24, bottom: 72, left: 100 };

/** Panneau de droite : 300 px (340 px dès 1920 px de large), redimensionnable à la poignée
 *  entre 260 et 480 px ; la largeur choisie est retenue par ce navigateur. */
const SIDE_MIN = 260;
const SIDE_MAX = 480;
const SIDE_KEY = "openplan:side-width";
const clampSide = (w: number) =>
  Math.round(Math.min(SIDE_MAX, Math.max(SIDE_MIN, w)));

function loadSideWidth(): number | null {
  try {
    const raw = window.localStorage.getItem(SIDE_KEY);
    const value = raw === null ? NaN : Number(raw);
    return Number.isFinite(value) && value > 0 ? clampSide(value) : null;
  } catch {
    return null;
  }
}

function saveSideWidth(width: number | null): void {
  try {
    if (width === null) window.localStorage.removeItem(SIDE_KEY);
    else window.localStorage.setItem(SIDE_KEY, String(width));
  } catch {
    // Stockage indisponible (navigation privée) : la largeur vaut pour la session.
  }
}

/** Ce que montre le panneau de droite : l'inspecteur contextuel par défaut, ou un volet
 *  ouvert depuis la palette (Meuble, Aménager) ou la barre du haut (Paramètres avancés). */
type SidePane =
  | "inspector"
  | "catalog"
  | "amenager"
  | "assistant"
  | "advanced"
  | "contextual"
  /** T7d1 : réglages, vues, points de vue et galerie des rendus (ex-écran de résultats). */
  | "renders";

const PANE_TITLE: Record<Exclude<SidePane, "inspector">, string> = {
  catalog: "Meuble",
  amenager: "Aménager",
  assistant: "Assistant",
  advanced: "Paramètres avancés",
  contextual: "Rendu contextualisé",
  renders: "Rendus",
};

/** Atelier à onglets Rendus · Plan · 3D (P1), et « Ajouter une source » : canevas plein écran avec barres
 *  flottantes (maquette d'augus), écran Importer du projet par-dessus le canevas, aperçu 3D
 *  en volet, écran Rendre (points de vue, rendu Blender, galerie) à droite de la 3D, bande de
 *  vignettes, panneau de droite repliable (inspecteur contextuel, ou volet Meuble / Aménager /
 *  Paramètres avancés). */
export function Editor({ projectId, initialStep, onHome }: Props) {
  const { state, actions } = useProject(projectId);
  // B1d : lecteur et commentateur (jusqu'à B2) en lecture seule ; tant que le rôle
  // n'est pas connu, rien d'éditable n'est montré.
  const role = useRole(projectId);
  const readOnly = !canEdit(role);
  /** KAN-47 : lien direct vers l'import, rôle pas encore lu → l'atelier attend. */
  const gate = importGate(role);
  // B1e : « Partager » pour le propriétaire, avec les comptes seulement.
  const account = useAccount();
  const canShare = account !== null && role === "owner";
  const [shareOpen, setShareOpen] = useState(false);
  // KAN-12 : sur mon projet, la place qui me reste (prévenir avant d'envoyer un gros
  // fichier) ; jamais sur le projet d'un autre (sa place reste à lui). Relue à l'ouverture
  // et quand le nombre de photos ou de rendus change (ajout, suppression), pas à chaque geste.
  const projectStamp = `${Object.keys(state.project?.images ?? {}).length}|${state.renders.length}`;
  useEffect(() => {
    if (!account || role !== "owner") {
      setSpaceLeft(projectId, null);
      return;
    }
    authApi.usage().then(
      (u) =>
        setSpaceLeft(
          projectId,
          u.quota_bytes === null ? null : u.quota_bytes - u.used_bytes,
        ),
      () => setSpaceLeft(projectId, null),
    );
  }, [account, role, projectId, projectStamp]);
  const [tool, setTool] = useState<Tool>("select");
  const [viewMode, setViewMode] = useState<ViewMode>("2d");
  const [step, setStep] = useState<Step>(initialStep ?? "edit");
  const autoStepped = useRef(initialStep !== undefined);
  /** L'espace d'arrivée est choisi (rendus chargés) : jusque-là, l'atelier se dit en
   *  chargement plutôt que de montrer un espace qu'il va quitter. */
  const [arrived, setArrived] = useState(initialStep !== undefined);
  const [showCeilings, setShowCeilings] = useState(false);
  /** Aperçu 3D (E3) : coupe, maison de poupée, rayons X, mémorisés par projet. */
  const projectKey = state.project?.id ?? "";
  const [view3d, setView3dState] = useState<View3D>(() =>
    loadView3D(projectKey),
  );
  const [view3dFor, setView3dFor] = useState(projectKey);
  if (view3dFor !== projectKey) {
    setView3dFor(projectKey);
    setView3dState(loadView3D(projectKey));
  }
  const setView3d = (next: Partial<View3D>) => {
    const merged = { ...view3d, ...next };
    setView3dState(merged);
    if (projectKey) saveView3D(projectKey, merged);
  };
  // Couleurs du viewer : provenance des données (défaut) ou teintes de matière (sans Blender).
  const [materials, setMaterials] = useState(false);
  // Versants du toit : masqués par défaut (vus de dehors, ils cachent l'intérieur).
  const [hideRoof, setHideRoof] = useState(true);
  const [preset, setPreset] = useState<{
    kind: CameraPreset;
    id: number;
  } | null>(null);
  const [circulationToken, setCirculationToken] = useState(0);
  // Largeur du panneau : celle choisie à la poignée, sinon 300 px (340 px dès 1920 px).
  const [sideWidth, setSideWidth] = useState<number | null>(loadSideWidth);
  const [wide, setWide] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(min-width: 1920px)").matches,
  );
  useEffect(() => {
    const query = window.matchMedia("(min-width: 1920px)");
    const onChange = (e: MediaQueryListEvent) => setWide(e.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  const panelWidth = sideWidth ?? (wide ? 340 : 300);
  const [fitToken, setFitToken] = useState(0);
  // T7d1 (décision PO du 16 octobre) : le panneau de droite est fermé au départ et ne
  // s'ouvre jamais tout seul : l'icône du haut, ou une action explicite (« Réglages et
  // galerie », « Changer l'ambiance », l'assistant…).
  const [panelOpen, setPanelOpen] = useState(false);
  // Sous 900 px le tiroir se ferme (le plan reste visible) ; il ne se rouvre pas seul.
  useEffect(() => {
    const narrow = window.matchMedia("(max-width: 900px)");
    const onChange = (e: MediaQueryListEvent) => {
      if (e.matches) setPanelOpen(false);
    };
    narrow.addEventListener("change", onChange);
    return () => narrow.removeEventListener("change", onChange);
  }, []);
  const [pane, setPane] = useState<SidePane>("inspector");
  const [zoomCommand, setZoomCommand] = useState<{
    factor: number;
    id: number;
  } | null>(null);
  const [scale, setScale] = useState(80);
  const [dropOver, setDropOver] = useState(false);
  // Marges du canevas mesurées sur les barres réellement affichées (la palette passe sur deux
  // rangées quand la place manque) : l'aide-mémoire et le recadrage restent hors des barres.
  const stageRef = useRef<HTMLDivElement>(null);
  const [insets, setInsets] = useState<Insets>(CANVAS_INSETS);
  const projectReady = state.project !== null;
  // La bande de rendus (P2) prend place en bas dès qu'une vue a un rendu.
  const hasRenders = latestRenders(state.renders).size > 0;
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const measure = () => {
      const frame = stage.getBoundingClientRect();
      const pane =
        stage
          .querySelector(".canvas-wrap .pane:not(.pane-3d)")
          ?.getBoundingClientRect() ?? frame;
      const over = (box: DOMRect) =>
        box.width > 0 && box.left < pane.right && box.right > pane.left;
      // En haut : la rangée du haut, et la barre de vues si elle passe au-dessus du plan
      // (la boussole se place dessous, en haut à droite).
      let topEdge = 0;
      for (const sel of [".topbar", ".viewbar-wrap"]) {
        const box = stage.querySelector(sel)?.getBoundingClientRect();
        if (box && over(box)) topEdge = Math.max(topEdge, box.bottom);
      }
      const top = Math.max(72, Math.round(topEdge - frame.top) + 26);
      // À gauche : la colonne d'outils.
      const palette = stage.querySelector(".palette")?.getBoundingClientRect();
      const left =
        palette && over(palette)
          ? Math.round(palette.right - pane.left) + 14
          : 24;
      // En bas : seules les barres qui recouvrent le volet du plan comptent. Dans l'espace
      // de travail, le bas (compositeur, barre de la sélection) aussi : les bandeaux passent
      // au-dessus, jamais dessus (KAN-22 : replié, le compositeur est plus bas qu'avant).
      let bottom = 24;
      for (const sel of [
        ".bottombar",
        ".strip",
        ".render-strip",
        ".board-bottom",
      ]) {
        const box = stage.querySelector(sel)?.getBoundingClientRect();
        if (box && over(box))
          bottom = Math.max(bottom, Math.round(frame.bottom - box.top) + 14);
      }
      setInsets((current) =>
        current.top === top &&
        current.bottom === bottom &&
        current.left === left
          ? current
          : { ...current, top, bottom, left },
      );
    };
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    for (const sel of [
      ".palette",
      ".viewbar-wrap",
      ".bottombar",
      ".strip",
      ".render-strip",
      ".board-bottom",
    ]) {
      const el = stage.querySelector(sel);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [viewMode, step, projectReady, hasRenders]);
  const [measure, setMeasure] = useState<{ a: Point2; b: Point2 } | null>(null);
  const [viewFrom, setViewFrom] = useState<{
    pose: ViewPose;
    id: number;
  } | null>(null);
  const [loadedToken, setLoadedToken] = useState(0);
  /** T7d2 : vue montrée dans la visionneuse plein écran (sa caméra) ; null = fermée. */
  const [viewerCam, setViewerCam] = useState<string | null>(null);
  const openViewer = useCallback(
    (cameraId: string) => {
      // Regarder ses rendus n'est pas un geste (journal à part, hors du compte des gestes).
      journalApi.ui(projectId, "viewer_opened", cameraId || "premiere");
      setViewerCam(cameraId);
    },
    [projectId],
  );
  const viewerRef = useRef<Viewer3DHandle>(null);
  const boardSync = [
    Object.keys(state.project?.images ?? {}).join(","),
    state.renders.map((r) => r.run_id).join(","),
    state.project?.pending_instruction ?? "",
    // T7e : un plan ajouté, annulé ou renommé a sa rangée sur l'espace de travail.
    Object.values(state.project?.levels ?? {})
      .map((l) => `${l.id}:${l.name}`)
      .join(","),
  ].join("|");
  // A1 : source choisie pour un rendu contextualisé, et compteur qui resynchronise le
  // tableau quand des variantes arrivent.
  const [contextualPick, setContextualPick] = useState<ContextualPick | null>(
    null,
  );
  const [contextualTick, setContextualTick] = useState(0);
  // B2a : l'espace de travail relu quand un autre membre l'a changé, ou après une coupure.
  const [liveTick, setLiveTick] = useState(0);
  const board = useBoard(
    projectId,
    `${boardSync}|${contextualTick}|${liveTick}`,
  );
  // KAN-51 : commentaires ; relus à chaque avis « comments » d'un autre membre.
  const [commentsTick, setCommentsTick] = useState(0);
  const comments = useComments(
    projectId,
    role === "owner" || role === "editor" || role === "commenter",
    commentsTick,
  );
  const live = useLive(projectId, account !== null, {
    onChanged: (scope) => {
      if (scope === "board") setLiveTick((n) => n + 1);
      else if (scope === "comments") setCommentsTick((n) => n + 1);
      else actions.reload();
    },
    onResync: () => {
      actions.reload();
      setLiveTick((n) => n + 1);
      setCommentsTick((n) => n + 1);
    },
    // Session perdue (KAN-49) : la page de connexion tout de suite, sans attendre la 401
    // d'un rechargement du projet.
    onSignedOut: signalSignedOut,
  });
  // B2b : la personne que je suis, si elle est toujours là.
  const followedPeer =
    live.peers.find((p) => p.user_id === live.presence.following) ?? null;
  // T7e : le plan ouvert (celui qu'on édite, visé par le chemin automatique) ; le premier
  // par défaut.
  const level = state.project
    ? ((state.levelId ? state.project.levels[state.levelId] : undefined) ??
      firstLevel(state.project))
    : undefined;
  const [newPlanOpen, setNewPlanOpen] = useState(false);

  // B2d (KAN-11) : bail d'édition du plan ouvert. Tenu par un autre membre : bandeau, plan
  // en lecture (outils grisés) et « Demander la main » ; « Prendre la main » seulement
  // après l'expiration. Le bail se rend en quittant l'onglet Plan.
  const onPlanTab = tabOf(step, viewMode) === "plan";
  const lease = account && level ? live.presence.leases[level.id] : undefined;
  const leaseOther =
    lease && lease.holder.user_id !== live.me?.user_id ? lease : null;
  const leaseMine = Boolean(lease && !leaseOther);
  const [leaseNow, setLeaseNow] = useState(() => Date.now());
  useEffect(() => {
    if (!leaseOther) return;
    const tick = window.setInterval(() => setLeaseNow(Date.now()), 1000);
    return () => window.clearInterval(tick);
  }, [leaseOther]);
  const leaseExpired = leaseOther !== null && leaseOther.expiresAt <= leaseNow;
  const planLocked = onPlanTab && leaseOther !== null;
  const planLockedRef = useRef(false);
  useEffect(() => {
    planLockedRef.current = planLocked;
  }, [planLocked]);
  const leaseLevel = account && onPlanTab ? (level?.id ?? null) : null;
  useEffect(() => {
    if (!leaseLevel) return;
    return () => {
      // En quittant l'onglet Plan (ou ce plan) : mon bail est rendu ; sans bail, rien.
      void leaseApi.give(projectId, leaseLevel).catch(() => undefined);
      live.presence.planCursor(leaseLevel, null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leaseLevel, projectId]);
  const leaseRequest =
    leaseMine && level && live.presence.leaseRequest?.level === level.id
      ? live.presence.leaseRequest
      : null;

  const fit = useCallback(() => setFitToken((n) => n + 1), []);
  const zoom = useCallback(
    (factor: number) =>
      setZoomCommand((c) => ({ factor, id: (c?.id ?? 0) + 1 })),
    [],
  );
  useEffect(() => {
    if (state.project) fit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.project?.id]);

  // Onglet d'arrivée (P1) : Rendus dès qu'un rendu existe, sinon Plan ; un projet vide (ni
  // mur ni photo) s'ouvre sur « Ajouter une source » ; un fichier déposé sur l'accueil
  // construit le plan tout de suite.
  const rendersLoaded = state.rendersLoaded;
  useEffect(() => {
    if (!rendersLoaded || !state.project || !level) return;
    if (!autoStepped.current) {
      autoStepped.current = true;
      // Consigne gardée depuis l'accueil (L1b) : on veut tracer, donc le plan.
      const empty =
        walls(level).length === 0 &&
        Object.keys(state.project.images).length === 0 &&
        !state.project.pending_instruction;
      // T5 : un projet vide s'ouvre sur son tableau (exigences, photos, plan à générer ou
      // à tracer) ; « Ajouter une source » reste dans la barre du haut.
      if (empty && initialStep === undefined) goStep("board");
      else if (empty) setStep("import");
      else if (latestRenders(state.renders).size > 0) {
        // T7d1 : un projet qui a des rendus s'ouvre sur l'espace de travail, cadré sur
        // eux (volet Rendus fermé : seul le chemin automatique l'ouvre).
        goStep("board");
        setRendersFocus((n) => n + 1);
      }
      // B3 : sans rendu, l'atelier s'ouvre sur l'éditeur (le tableau) ; sans aucun mur,
      // sur le plan, où l'on trace (consigne gardée de l'accueil, L1b).
      else if (walls(level).length > 0) goStep("board");
      // Consigne gardée de l'accueil (P5c) : elle attend dans l'assistant.
      if (state.project.pending_instruction) setPane("assistant");
    }
    setArrived(true);
    const pending = takePendingFile();
    if (pending) {
      setStep("import");
      actions.importFile(pending);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rendersLoaded]);

  // Arrivée par /nouveau avec des exigences (retour d'augus, 19 oct.) : le plan se génère
  // depuis elles, sans geste, une fois l'espace affiché et le rôle connu (propriétaire). En
  // échec (rien de lisible), le message s'affiche et le compositeur reste ouvert.
  const projectLoaded = state.project?.id ?? null;
  useEffect(() => {
    if (!arrived || readOnly || !projectLoaded || !level) return;
    if (!takePendingPlan(projectLoaded)) return;
    if (walls(level).length > 0) return;
    actions.generatePlan();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [arrived, readOnly, projectLoaded, level]);

  // L'adresse suit l'écran « Ajouter une source » (adressable), sans empiler d'historique.
  useEffect(() => {
    const wanted = `#/projects/${projectId}${step === "import" ? "/import" : ""}`;
    if (window.location.hash !== wanted)
      window.history.replaceState(null, "", wanted);
  }, [projectId, step]);

  /** « import » = « Ajouter une source » par-dessus le projet ; « edit » = le plan (2D, 3D
   *  ou les deux) ; « board » = l'espace de travail. Les rendus se regardent dans la
   *  visionneuse (T7d2), par-dessus l'un ou l'autre. */
  const goStep = useCallback((next: Step) => {
    setStep(next);
    if (next === "board") setViewMode("2d");
    else if (next === "edit") setViewMode((m) => (m === "3d" ? "2d" : m));
    else setViewMode("2d");
  }, []);
  // KAN-47 : sur l'import, une fois le rôle lu, qui ne peut pas éditer passe à l'espace de
  // travail (en lecture seule) ; qui peut éditer reste sur l'import.
  useEffect(() => {
    if (step === "import" && gate === "board") goStep("board");
  }, [step, gate, goStep]);
  /** Espaces du projet : l'espace de travail, le plan, la 3D. */
  const goTab = useCallback(
    (next: Tab) => {
      if (next === "board") goStep("board");
      else if (next === "plan") goStep("edit");
      else {
        setStep("edit");
        setViewMode("3d");
      }
    },
    [goStep],
  );

  // Un plan vient d'arriver tout seul (photo analysée, plan lu) : depuis Importer, on
  // passe à Éditer, plan recadré ; le bandeau « plan estimé » est de nouveau montré.
  const [noticeClosed, setNoticeClosed] = useState(false);
  // A2 : ce que l'accueil a à dire (plan ou rendus en échec, fichier ignoré), une fois.
  const [homeNotices, setHomeNotices] = useState<string[]>(() =>
    takePendingNotices(projectId),
  );
  const arrival = state.arrival;
  useEffect(() => {
    if (arrival === 0) return;
    setStep((s) => (s === "import" ? "edit" : s));
    setNoticeClosed(false);
    fit();
  }, [arrival, fit]);

  // Chemin « zéro geste » arrivé au bout (V1, point 6) : l'atelier passe à Rendre, les
  // rendus en premier, avec les accès pour affiner.
  const pilotArrival = state.pilotArrival;
  const [showArrival, setShowArrival] = useState(false);
  /** T7d1 : demande de cadrage de l'espace de travail sur le cadre Rendus. */
  const [rendersFocus, setRendersFocus] = useState(0);
  useEffect(() => {
    if (pilotArrival === 0) return;
    // T7d1 : l'espace de travail cadré sur les rendus, « Vos rendus » ouvert dans le volet.
    // Le volet Rendus (avec « Vos rendus ») ne s'ouvre pas tout seul : il est prêt pour le
    // bouton « Réglages et galerie » du cadre Rendus.
    goStep("board");
    setShowArrival(true);
    setPane("renders");
    setRendersFocus((n) => n + 1);
  }, [pilotArrival, goStep]);
  // KAN-52 : une ligne de la cloche mène ici, au bon endroit : le plan modifié, le cadre
  // Rendus, l'épingle (fil ouvert) ou l'élément ; sinon l'espace de travail.
  const bellToken = useBellTargetToken();
  /** KAN-58 : le panneau « Activité » du projet (KAN-64 : une zone repliée du panneau de
   *  droite dans l'espace de travail, un panneau flottant ailleurs). */
  const [activityOpen, setActivityOpen] = useState(false);
  /** KAN-64 : dans l'espace de travail, le panneau de droite (Assistant, Commentaires,
   *  Activité) est ouvert d'office ; ses zones et l'endroit où l'espace de travail y range
   *  l'Assistant et les commentaires. */
  const [boardSideOpen, setBoardSideOpen] = useState(true);
  const [commentsZoneOpen, setCommentsZoneOpen] = useState(true);
  const [boardSlots, setBoardSlots] = useState<{
    top: HTMLElement | null;
    comments: HTMLElement | null;
  }>({ top: null, comments: null });
  /** KAN-64 : dans l'espace de travail, les zones (Assistant, Commentaires, Activité) tant
   *  que l'utilisateur n'a pas ouvert lui-même un autre volet ; un volet gardé d'avant (posé
   *  par l'arrivée, par exemple) ne les remplace pas. */
  const boardZones = step === "board" && !(panelOpen && pane !== "inspector");
  const shownPane: SidePane = boardZones ? "inspector" : pane;
  const sideOpen =
    step === "board"
      ? boardSideOpen || (panelOpen && pane !== "inspector")
      : panelOpen;
  const toggleSide = () => {
    if (step !== "board") {
      setPanelOpen((o) => !o);
      return;
    }
    if (sideOpen) {
      setBoardSideOpen(false);
      setPanelOpen(false);
      setPane("inspector");
    } else setBoardSideOpen(true);
  };
  /** La bulle de la barre du haut : les Commentaires, dans le panneau de l'espace de
   *  travail. */
  const openComments = () => {
    if (step !== "board") goTab("board");
    setPane("inspector");
    setBoardSideOpen(true);
    setCommentsZoneOpen(true);
  };
  const toggleActivity = () => {
    if (step === "board") {
      setPane("inspector");
      setBoardSideOpen(true);
    }
    setActivityOpen((o) => !o);
  };
  const [boardFocus, setBoardFocus] = useState<{
    token: number;
    threadId: string | null;
    itemId: string | null;
  } | null>(null);
  useEffect(() => {
    if (!projectLoaded) return;
    const target = takeBellTarget(projectId);
    if (!target) return;
    if (target.levelId) actions.openLevel(target.levelId);
    if (target.plan) {
      goTab("plan");
      return;
    }
    goTab("board");
    if (target.runId) setRendersFocus((n) => n + 1);
    if (target.threadId) {
      comments.setHidden(false);
      comments.open(target.threadId);
      comments.setPanelOpen(true);
    }
    if (target.threadId || target.itemId)
      setBoardFocus({
        token: target.token,
        threadId: target.threadId,
        itemId: target.itemId,
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bellToken, projectLoaded, projectId]);
  // Pièce principale : le séjour d'un plan généré (L3), sinon celle qui a une ambiance,
  // sinon la première.
  const rooms = level
    ? Object.values(level.elements).filter((e) => e.type === "room")
    : [];
  const mainRoom =
    rooms.find((e) => e.metadata?.["generated_plan"] !== undefined) ??
    rooms.find((e) => "intent" in e && e.intent) ??
    rooms[0];
  // Plan généré depuis un programme (L3) : ce n'est pas un relevé, et ce qui a été supposé.
  const generatedValue = mainRoom?.metadata?.["generated_plan"];
  const generated =
    generatedValue && typeof generatedValue === "object"
      ? (generatedValue as { notes?: string[] })
      : null;
  const [generatedClosed, setGeneratedClosed] = useState(false);
  // Plan de départ simplifié (L1b) : la pièce porte sa surface demandée.
  const starterValue = mainRoom?.metadata?.["starter"];
  const starterArea = typeof starterValue === "number" ? starterValue : null;
  const [starterClosed, setStarterClosed] = useState(false);

  // Surbrillance des éléments touchés par l'assistant : quelques secondes, puis rien.
  const highlighted = state.assistantHighlight;
  useEffect(() => {
    if (highlighted.length === 0) return;
    const timer = window.setTimeout(actions.clearAssistantHighlight, 4000);
    return () => window.clearTimeout(timer);
  }, [highlighted, actions.clearAssistantHighlight]);

  /** Ouvre l'assistant (P5) depuis n'importe quel onglet, avec un élément en contexte. */
  const openAssistant = useCallback(
    (contextId?: string) => {
      if (contextId) actions.select({ kind: "element", id: contextId });
      setPanelOpen(true);
      setPane("assistant");
    },
    [actions],
  );
  /** Ouvre un volet (ou revient à l'inspecteur si on reclique le même). */
  const togglePane = useCallback((next: Exclude<SidePane, "inspector">) => {
    setPanelOpen(true);
    setPane((p) => (p === next ? "inspector" : next));
  }, []);
  const openAdvanced = useCallback(() => {
    setPanelOpen(true);
    setPane("advanced");
  }, []);
  const showInspector = useCallback(() => {
    setPanelOpen(true);
    setPane("inspector");
  }, []);

  // Bibliothèque : le meuble choisi attend un clic dans le plan ; fermer le volet l'abandonne.
  const [placing, setPlacing] = useState<Placing | null>(null);
  const endPlacing = useCallback(() => setPlacing(null), []);
  useEffect(() => {
    if (!panelOpen || pane !== "catalog" || tool !== "select") setPlacing(null);
  }, [panelOpen, pane, tool]);

  // Sélectionner quelque chose dans le plan ramène l'inspecteur si le volet Meuble est ouvert
  // (on vient de poser un meuble : on veut sa fiche). Aménager et Avancés restent.
  const selectionKey =
    state.selection.kind === "none" ? "" : state.selection.id;
  useEffect(() => {
    if (selectionKey) setPane((p) => (p === "catalog" ? "inspector" : p));
  }, [selectionKey]);

  const deleteSelection = useCallback(() => {
    const s = state.selection;
    if (s.kind === "vertex")
      actions.apply({ op: "remove_vertex", vertex_id: s.id });
    if (s.kind === "element")
      actions.apply({ op: "remove_element", element_id: s.id });
  }, [actions, state.selection]);

  /** Raccourcis des barres (outils, vues, coupes, affichage) : posés à chaque rendu, après le
   *  chargement du projet, pour lire l'état courant. */
  const barKeys = useRef<((e: KeyboardEvent) => boolean) | null>(null);
  const onBoard = useRef(false);
  onBoard.current = step === "board";
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName) ||
          target.isContentEditable)
      )
        return;
      if (onBoard.current) {
        if (
          !e.ctrlKey &&
          !e.metaKey &&
          (e.key === "/" || e.key === ",") &&
          barKeys.current?.(e)
        )
          e.preventDefault();
        return;
      }
      // B2d : un autre membre modifie ce plan : pas de raccourci d'édition.
      if (planLockedRef.current) return;
      if (!e.ctrlKey && !e.metaKey && !e.altKey && barKeys.current?.(e)) {
        e.preventDefault();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) actions.redo();
        else actions.undo();
        return;
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") {
        e.preventDefault();
        actions.redo();
        return;
      }
      if (e.key === "Delete" || e.key === "Backspace") {
        deleteSelection();
        return;
      }
      if (e.key === "Escape") setMeasure(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [actions, deleteSelection]);

  /** Nouveau point de vue dans un coin du plan (à 0,6 m des murs), à hauteur d'œil, tourné
   *  vers le centre : sa vignette montre la pièce, pas un mur vu de près. */
  const addViewpoint = useCallback(() => {
    const b = state.derived?.bounds;
    if (!b) {
      actions.apply({ op: "add_camera", position: { x: 1, y: 1 }, yaw: 0 });
      return;
    }
    const inset = Math.min(
      0.6,
      (b.max.x - b.min.x) / 4,
      (b.max.y - b.min.y) / 4,
    );
    const position = { x: b.min.x + inset, y: b.min.y + inset };
    const center = { x: (b.min.x + b.max.x) / 2, y: (b.min.y + b.max.y) / 2 };
    const yaw = Math.atan2(center.y - position.y, center.x - position.x);
    actions.apply({ op: "add_camera", position, yaw });
  }, [actions, state.derived]);

  const placeViewpoint = useCallback(
    (cameraId: string) => {
      goStep("edit");
      showInspector();
      actions.select({ kind: "element", id: cameraId });
    },
    [actions, goStep, showInspector],
  );

  /** « Recalibrer » : l'outil unique de correction d'échelle (deux clics sur une longueur
   *  connue, ou la hauteur sous plafond dans la carte). */
  const recalibrate = () => {
    goStep("edit");
    setMeasure(null);
    setTool("scale");
  };

  if (!state.project || !level) {
    return (
      <main className="app">
        <p>{state.error ?? "Chargement…"}</p>
        <button onClick={onHome}>← Projets</button>
      </main>
    );
  }

  const glbUrl = api.exportGlbUrl(
    state.project.id,
    level.id,
    state.project.history_cursor,
    materials ? "realistic" : "provenance",
  );
  const show2d = viewMode !== "3d";
  // A3 : statut du plan, montré tant qu'il n'est ni saisi ni recalibré (le bandeau « Plan
  // estimé » d'après import, plus détaillé, prend sa place quand il est là).
  const status = planStatus(state.project, level);
  const estimatedBanner =
    step === "edit" &&
    tool !== "scale" &&
    state.lastAuto !== null &&
    state.lastAuto.run.method !== "none" &&
    state.autoJob?.status !== "running" &&
    !state.autoDone &&
    !noticeClosed;
  // T7d1 : volet Rendus ouvert dans l'espace de travail ; la 3D, cachée sous l'espace de
  // travail, n'est montée que pour les vignettes des points de vue, volet ouvert.
  const rendersPane = step === "board" && panelOpen && pane === "renders";
  const show3d = viewMode !== "2d" || rendersPane;
  const tab = tabOf(step, viewMode);
  const stale = staleCameraIds(state.project, state.renders);
  // File d'attente : un rendu qui attend son tour occupe les boutons comme un rendu lancé.
  const renderRunning = jobActive(state.renderJob);
  /** Ce qui attend son tour (construction du plan, chemin automatique, rendus). */
  const queued = [state.autoJob, state.pilotJob, state.renderJob].some(
    (j) => j?.status === "queued",
  );
  /** « Affiner cette vue » (P3) : l'onglet 3D à la caméra du rendu, la pièce regardée
   *  choisie et sa fiche ouverte (ambiance, meubles) ; la bande dira quoi re-rendre. */
  const refineView = (cameraId: string, elementId?: string) => {
    const camera = level.elements[cameraId];
    if (!camera || camera.type !== "camera") return;
    journalApi.ui(projectId, "refine_clicked", elementId ? "element" : "vue");
    setViewFrom((v) => ({ pose: poseOf(camera), id: (v?.id ?? 0) + 1 }));
    goTab("3d");
    const target = elementId ?? roomForView(camera, state.derived);
    if (target) {
      actions.select({ kind: "element", id: target });
      showInspector();
    }
  };
  /** Un geste de la carte de désignation (P4b), sur l'élément vu dans le rendu d'une vue. */
  const pickAction = (cameraId: string, action: PickAction) => {
    if (action.kind === "ask") openAssistant(action.elementId);
    else if (action.kind === "refine") refineView(cameraId, action.elementId);
    else if (action.kind === "ambiance") refineView(cameraId);
    else if (action.kind === "move") {
      goTab("plan");
      actions.select({ kind: "element", id: action.elementId });
      showInspector();
    } else if (action.kind === "remove")
      actions.apply({ op: "remove_element", element_id: action.elementId });
    else
      actions.apply({
        op: "replace_furniture",
        element_id: action.elementId,
        asset_id: action.assetId,
      });
  };
  /** « Re-rendre les n vues » (P2) : depuis la bande ou la ligne des vues périmées. */
  const rerenderViews = (cameraIds: string[], onLevel?: string) => {
    journalApi.ui(projectId, "render_requested", "perimees");
    actions.renderViews(cameraIds, onLevel);
  };
  // Vus de l'extérieur, plafonds et versants cachent l'intérieur : masqués par défaut ; la
  // maison de poupée les masque toujours.
  const hiddenIds = new Set(
    Object.values(level.elements)
      .filter(
        (e) =>
          (e.type === "ceiling" && (!showCeilings || view3d.dollhouse)) ||
          (e.type === "roof_plane" && (hideRoof || view3d.dollhouse)),
      )
      .map((e) => e.id),
  );
  const wallIds = Object.values(level.elements)
    .filter((e) => e.type === "wall")
    .map((e) => e.id);
  const bounds = state.derived?.bounds;
  /** Plage du curseur de coupe : hauteur du niveau, ou emprise le long de l'axe. */
  const sectionRange = (axis: SectionAxis): [number, number] =>
    axis === "z"
      ? [0, Math.max(level.height, 2.5) + 0.5]
      : axis === "x"
        ? [bounds?.min.x ?? 0, bounds?.max.x ?? 10]
        : [bounds?.min.y ?? 0, bounds?.max.y ?? 10];
  const chooseSection = (axis: SectionAxis) => {
    if (axis === "none") return setView3d({ section: "none" });
    const [lo, hi] = sectionRange(axis);
    const value =
      axis === "z"
        ? Math.min(1.2, hi)
        : Math.round(((lo + hi) / 2) * 100) / 100;
    setView3d({ section: axis, value });
  };

  /** Les commandes de la 3D ouvrent la vue partagée quand le plan est seul à l'écran. */
  const with3d = (run: () => void) => {
    if (viewMode === "2d") setViewMode("split");
    run();
  };
  const choosePreset = (kind: CameraPreset) =>
    with3d(() => setPreset((p) => ({ kind, id: (p?.id ?? 0) + 1 })));
  const toggleView = (
    key: "flip" | "hideRoof" | "xray" | "dollhouse" | "ceilings" | "materials",
  ) =>
    with3d(() => {
      if (key === "hideRoof") setHideRoof((v) => !v);
      else if (key === "ceilings") setShowCeilings((v) => !v);
      else if (key === "materials") setMaterials((v) => !v);
      else if (key === "flip") {
        if (view3d.section !== "none") setView3d({ flip: !view3d.flip });
      } else if (key === "xray") setView3d({ xray: !view3d.xray });
      else setView3d({ dollhouse: !view3d.dollhouse });
    });
  const pickSection = (axis: SectionAxis) => with3d(() => chooseSection(axis));
  const openStairs = () => {
    goStep("edit");
    setPanelOpen(true);
    setPane("catalog");
    setCirculationToken((n) => n + 1);
  };
  const drawingKeys = step !== "import" && viewMode !== "3d";

  barKeys.current = (e: KeyboardEvent): boolean => {
    if (step === "import") return false;
    const key = e.key;
    // Chiffres et signes : pris tels quels (Maj est souvent nécessaire sur AZERTY).
    const asked = KEY_PRESETS[key];
    if (asked) {
      choosePreset(asked);
      return true;
    }
    if (key === "/") {
      // L'assistant (P5) s'ouvre ou se ferme dans les trois onglets.
      togglePane("assistant");
      return true;
    }
    if (key === ",") {
      togglePane("advanced");
      return true;
    }
    if ((key === "+" || key === "=" || key === "-" || key === "0") && show2d) {
      if (key === "0") fit();
      else zoom(key === "-" ? 1 / 1.25 : 1.25);
      return true;
    }
    const letter = key.length === 1 ? key.toLowerCase() : "";
    if (!letter) return false;
    if (e.shiftKey) {
      if (letter === "h") pickSection(view3d.section === "z" ? "none" : "z");
      else if (letter === "v")
        pickSection(
          view3d.section === "x" ? "y" : view3d.section === "y" ? "none" : "x",
        );
      else if (letter === "r") toggleView("flip");
      else if (letter === "d") toggleView("dollhouse");
      else return false;
      return true;
    }
    const t = KEY_TOOLS[letter];
    if (t) {
      setTool(t);
      if (t !== "scale") setMeasure(null);
      return true;
    }
    const display = {
      r: "hideRoof",
      x: "xray",
      c: "ceilings",
      m: "materials",
    } as const;
    if (letter in display) {
      toggleView(display[letter as keyof typeof display]);
      return true;
    }
    if (!drawingKeys) return false;
    if (letter === "p") {
      if (!state.busy) actions.apply({ op: "detect_rooms" });
    } else if (letter === "k") {
      if (hasScaleTarget()) recalibrate();
    } else if (letter === "e") openStairs();
    else if (letter === "b") togglePane("catalog");
    else if (letter === "a") togglePane("amenager");
    else return false;
    return true;
  };
  const bgImage = level.background_image
    ? state.project.images[level.background_image]
    : undefined;
  const background: Background | null = bgImage
    ? {
        url: api.imageFileUrl(state.project.id, bgImage.id),
        widthPx: bgImage.width_px,
        heightPx: bgImage.height_px,
        transform: readTransform(level.background_transform, bgImage.width_px),
      }
    : null;
  /** Quelque chose à mettre à l'échelle (murs ou fond de plan) : Recalibrer est proposé. */
  const hasScaleTarget = () => background !== null || walls(level).length > 0;

  // Un fichier déposé sur le canevas construit ou complète le plan (localement).
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDropOver(false);
    const file = e.dataTransfer.files?.[0];
    // KAN-50 : un lecteur n'importe rien (le navigateur n'ouvre pas non plus le fichier).
    if (!file || readOnly) return;
    actions.importFile(file);
  };

  const inspector = (
    <Inspector
      project={state.project}
      level={level}
      derived={state.derived}
      report={state.report}
      selection={state.selection}
      lastCalibration={state.lastCalibration}
      busy={state.busy}
      onOp={actions.apply}
      onAddMeasurement={actions.addMeasurement}
      onRemoveMeasurement={actions.removeMeasurement}
      onCalibrate={actions.calibrate}
      onSettings={actions.updateSettings}
      onAdoptView={actions.adoptView}
      onRotateView={actions.rotateView}
      onOpenAdvanced={openAdvanced}
      onAskAssistant={(roomId) => openAssistant(roomId)}
    />
  );

  // Assistant unifié (P5) : le même panneau dans Rendus, Plan et 3D ; « À propos de » suit la
  // pièce ou le meuble choisi (ou désigné dans un rendu).
  const contextElement =
    state.selection.kind === "element"
      ? level.elements[state.selection.id]
      : undefined;
  const assistantContext =
    contextElement &&
    (contextElement.type === "room" || contextElement.type === "furniture")
      ? contextElement
      : null;
  const ambianceRoom =
    assistantContext?.type === "room" ? assistantContext : mainRoom;
  const assistantPanel = (
    <AssistantPanel
      messages={state.assistant}
      busy={state.busy}
      modelConfigured={state.assistantModelConfigured}
      onSend={(text) =>
        actions.askAssistant(text, assistantContext?.id ?? null)
      }
      onPreview={actions.previewAssistant}
      consented={
        state.project.privacy.allow_remote_models &&
        state.project.privacy.remote_consent
      }
      onAskConsent={(destination) => {
        const ok = window.confirm(
          `Envoyer du texte à ${destination ?? "un service distant"} ?\n\n` +
            "Ce qui partira : vos phrases à l'assistant et la description du plan (cotes, ouvertures, meubles), jamais la photo.\n" +
            "Ce qui ne partira jamais : les photos, le fichier du projet, votre nom.\n" +
            "Chaque envoi est inscrit dans le journal réseau (Paramètres avancés › Assistant IA).\n\n" +
            "Confirmer pour ce projet ?",
        );
        if (ok && state.project)
          actions.updateSettings({
            privacy: {
              ...state.project.privacy,
              allow_remote_models: true,
              remote_consent: true,
            },
          });
      }}
      onUndo={actions.undoAssistant}
      onShowProposals={(m) => {
        actions.showAssistantProposals(m);
        togglePane("amenager");
      }}
      context={
        assistantContext
          ? {
              label:
                assistantContext.name ??
                (assistantContext.type === "room"
                  ? "la pièce"
                  : assistantContext.category),
            }
          : null
      }
      onClearContext={() => actions.select({ kind: "none" })}
      gemini={state.assistantGemini}
      onOpenGeminiSetting={onHome}
      journalEnabled={state.assistantJournal}
      ambiance={
        ambianceRoom && ambianceRoom.type === "room" ? (
          <AmbianceDetails room={ambianceRoom} onOp={actions.apply} />
        ) : null
      }
      pending={
        state.project.pending_instruction
          ? { text: state.project.pending_instruction, canApply: !!mainRoom }
          : null
      }
      onApplyPending={() =>
        actions.askAssistant(
          state.project?.pending_instruction ?? "",
          mainRoom?.id ?? null,
        )
      }
      onForgetPending={() =>
        actions.updateSettings({ pending_instruction: "" })
      }
    />
  );

  const showScaleCard = tool === "scale" && show2d && step !== "import";

  const renderPanel = (
    <RenderPanel
      project={state.project}
      level={level}
      busy={state.busy}
      renders={state.renders}
      renderJob={state.renderJob}
      loadedToken={loadedToken}
      snapshot={(pose) => viewerRef.current?.snapshotFrom(pose) ?? null}
      onView={(pose) => {
        // Dans Rendus, la 3D est sous les images : « voir en 3D » ouvre l'onglet 3D.
        setViewFrom((v) => ({ pose, id: (v?.id ?? 0) + 1 }));
        goTab("3d");
      }}
      onRender={(cam, samples, gpu, daylight) => {
        journalApi.ui(projectId, "render_requested", "one");
        actions.startRender(
          cam,
          samples,
          gpu,
          daylight,
          view3d.section === "none"
            ? null
            : { axis: view3d.section, value: view3d.value },
        );
      }}
      sectionNote={
        view3d.section === "none"
          ? null
          : view3d.section === "z"
            ? `horizontale à ${view3d.value.toFixed(2).replace(".", ",")} m`
            : `verticale ${view3d.section} = ${view3d.value.toFixed(2).replace(".", ",")} m`
      }
      northDeg={level.north_deg ?? 90}
      onNorth={(deg) =>
        actions.apply({
          op: "update_level",
          fields: { north_deg: deg },
        })
      }
      onAddViewpoint={addViewpoint}
      onArchitectView={actions.addArchitectView}
      arrival={
        showArrival && state.pilotRun
          ? {
              run: state.pilotRun,
              onRefine: () => {
                journalApi.ui(projectId, "refine_clicked", "arrivee");
                goStep("edit");
              },
              onAmbiance: () => {
                journalApi.ui(projectId, "refine_clicked", "ambiance");
                goStep("edit");
                setPane("inspector");
                setPanelOpen(true);
                if (mainRoom)
                  actions.select({
                    kind: "element",
                    id: mainRoom.id,
                  });
              },
              onClose: () => setShowArrival(false),
              onCatalogue: actions.catalogueRender,
              onUndoAreaGuard: actions.undoAreaGuard,
              busy: jobActive(state.renderJob),
            }
          : null
      }
      onAutoViews={actions.addAutoViews}
      onRenderAll={(...args) => {
        journalApi.ui(projectId, "render_requested", "all");
        return actions.renderAll(...args);
      }}
      onSectionView={
        view3d.section === "x" || view3d.section === "y"
          ? () =>
              actions.addSectionView(view3d.section as "x" | "y", view3d.value)
          : null
      }
      sectionViewName={
        view3d.section === "x" || view3d.section === "y"
          ? `Coupe ${view3d.section} = ${view3d.value.toFixed(2).replace(".", ",")} m`
          : null
      }
      onFinish={actions.finishRender}
      onPlace={placeViewpoint}
      onRefresh={actions.refreshRenders}
    />
  );

  return (
    <ReadOnlyContext.Provider value={readOnly}>
      <div
        className={`editor${readOnly ? " read-only" : ""}`}
        data-space={
          arrived && !(step === "import" && gate === "waiting")
            ? (tab ?? "source")
            : "loading"
        }
      >
        <div className={`stage view-${viewMode}`} ref={stageRef}>
          {state.pilotJob?.status === "running" && (
            <PilotProgress job={state.pilotJob} />
          )}
          <div
            className={`canvas-wrap split-${viewMode}${dropOver ? " drop-over" : ""}`}
            onDragOver={(e) => {
              if (e.dataTransfer.types.includes("Files")) {
                e.preventDefault();
                // KAN-50 : pas de zone de dépôt allumée pour un lecteur.
                setDropOver(!readOnly);
              }
            }}
            onDragLeave={() => setDropOver(false)}
            onDrop={onDrop}
          >
            {show2d && (
              <div className="pane">
                <PlanCanvas
                  level={level}
                  derived={state.derived}
                  units={state.project.units_display}
                  tool={planLocked && tool !== "pan" ? "select" : tool}
                  selection={state.selection}
                  background={background}
                  imageWidths={Object.fromEntries(
                    Object.values(state.project.images).map((i) => [
                      i.id,
                      i.width_px,
                    ]),
                  )}
                  ghost={state.ghost?.furniture ?? null}
                  ghostAlt={state.compare?.furniture ?? null}
                  highlight={state.assistantHighlight}
                  onCheckFurniture={actions.checkFurniture}
                  onCheckElement={actions.checkElement}
                  placing={placing}
                  onCheckPlacement={actions.checkPlacement}
                  onPlacingEnd={endPlacing}
                  onSelect={actions.select}
                  // B2d : plan tenu par un autre membre : rien ne part (le serveur dirait 409).
                  onOp={planLocked ? () => undefined : actions.apply}
                  fitToken={fitToken}
                  zoomCommand={zoomCommand}
                  onScaleChange={setScale}
                  onMeasure={(a, b) => setMeasure({ a, b })}
                  insets={insets}
                  onCursor={
                    account
                      ? (p) => live.presence.planCursor(level.id, p)
                      : undefined
                  }
                  peers={
                    account
                      ? { store: live.presence.store, peers: live.peers }
                      : undefined
                  }
                />
              </div>
            )}
            {show3d && (
              <div className="pane pane-3d">
                <Viewer3D
                  ref={viewerRef}
                  url={glbUrl}
                  selection={state.selection}
                  hiddenIds={hiddenIds}
                  onSelect={actions.select}
                  viewFrom={viewFrom}
                  preset={preset}
                  onLoaded={() => setLoadedToken((n) => n + 1)}
                  section={
                    view3d.section === "none"
                      ? null
                      : {
                          axis: view3d.section,
                          value: view3d.value,
                          flip: view3d.flip,
                        }
                  }
                  cutawayWalls={view3d.dollhouse ? wallIds : null}
                  xray={view3d.xray}
                />
              </div>
            )}
          </div>
          {viewerCam !== null && (
            <RenderViewer
              project={state.project}
              level={level}
              renders={state.renders}
              stale={stale}
              busy={state.busy || renderRunning}
              running={renderRunning}
              cameraId={viewerCam}
              onNavigate={setViewerCam}
              onClose={() => setViewerCam(null)}
              onRerender={rerenderViews}
              onRefine={(cameraId) => {
                setViewerCam(null);
                refineView(cameraId);
              }}
              onPickAction={(cameraId, action) => {
                // Retirer ou remplacer un meuble se voit d'ici (la vue devient périmée) ;
                // le reste mène au plan, à la 3D ou à l'assistant : la visionneuse se ferme.
                if (action.kind !== "remove" && action.kind !== "replace")
                  setViewerCam(null);
                pickAction(cameraId, action);
              }}
              onCatalogue={actions.catalogueRender}
              onUndo={actions.undo}
              onRedo={actions.redo}
            />
          )}
          {step !== "import" && step !== "board" && (
            <RenderStrip
              project={state.project}
              level={level}
              renders={state.renders}
              stale={stale}
              running={renderRunning}
              stage={state.renderJob?.stage ?? null}
              onOpen={openViewer}
              onRerender={rerenderViews}
            />
          )}
          {step === "board" && board.board && (
            <BoardCanvas
              project={state.project}
              board={board.board}
              renders={state.renders}
              stale={stale}
              notice={board.notice}
              canUndo={board.canUndo}
              canRedo={board.canRedo}
              active={step === "board"}
              planVersion={state.project.history_cursor}
              onCommit={board.commit}
              onView={board.setView}
              onUndo={board.undo}
              onRedo={board.redo}
              onDismissNotice={board.dismissNotice}
              onProjectChanged={(p) => void actions.adoptProject(p)}
              onOpenRender={(runId) => {
                const entry = state.renders.find((r) => r.run_id === runId);
                // T7e : la visionneuse montre les vues du plan de ce rendu.
                const lid =
                  entry && state.project && renderLevelId(state.project, entry);
                if (lid && lid !== level.id) actions.openLevel(lid);
                openViewer(entry?.camera_id ?? "");
              }}
              onEditPlan={(levelId) => {
                journalApi.ui(projectId, "tab_changed", "board>plan");
                // T7e : le plan de ce cadre devient le plan ouvert.
                if (levelId) actions.openLevel(levelId);
                goTab("plan");
              }}
              activeLevelId={level.id}
              onOpenLevel={actions.openLevel}
              onNewPlan={readOnly ? undefined : () => setNewPlanOpen(true)}
              onOpenViewer={(lid) => {
                if (lid !== level.id) actions.openLevel(lid);
                openViewer("");
              }}
              onVariant={(text) => {
                openAssistant();
                if (text.trim()) actions.askAssistant(text.trim(), null);
              }}
              onSettings={actions.updateSettings}
              onGeneratePlan={() => {
                journalApi.ui(projectId, "generate_clicked", "plan");
                actions.generatePlan();
              }}
              onGenerateNow={(fresh) => {
                journalApi.ui(projectId, "generate_clicked", "now");
                actions.generateNow(fresh);
              }}
              onPhotoDropped={actions.planFromPhoto}
              queued={queued}
              onCancelQueued={actions.cancelQueued}
              hasRenders={state.renders.length > 0}
              planProgress={
                state.autoJob?.status === "queued" ||
                state.pilotJob?.status === "queued"
                  ? "En attente"
                  : state.autoJob?.status === "running"
                    ? `Construction du plan : ${jobSentence(state.autoJob)}`
                    : state.pilotJob?.status === "running"
                      ? `Chemin automatique : ${jobSentence(state.pilotJob)}`
                      : null
              }
              onAddSource={() => goStep("import")}
              onImportPlan={(file) => {
                goStep("import");
                actions.importFile(file);
              }}
              focusRenders={rendersFocus}
              focusOn={boardFocus}
              side={sideOpen && boardZones ? boardSlots : null}
              presence={account ? live.presence : undefined}
              comments={comments}
              peers={live.peers}
              onRerender={rerenderViews}
              onOpenRendersPane={(levelId) => {
                if (levelId) actions.openLevel(levelId);
                setPane("renders");
                setPanelOpen(true);
              }}
              onContextual={(pick) => {
                setContextualPick(pick);
                setPanelOpen(true);
                setPane("contextual");
              }}
            />
          )}
          {step === "import" && gate === "waiting" && (
            // KAN-47 : rôle pas encore lu ; ni import ni espace de travail, rien d'éditable.
            <div className="import-waiting" role="status" aria-busy="true">
              Ouverture du projet…
            </div>
          )}
          {step === "import" && !readOnly && (
            <ImportScreen
              project={state.project}
              level={level}
              busy={state.busy}
              autoJob={state.autoJob}
              lastAuto={state.lastAuto}
              planPreview={state.planPreview}
              planEditable={
                state.planCursor !== null &&
                state.planCursor === state.project.history_cursor
              }
              planLayers={state.planLayers}
              onImport={actions.importFile}
              onRescale={actions.rescaleImportedPlan}
              onLayers={actions.relayerImportedPlan}
              onContinue={() => goStep("edit")}
            />
          )}
          <TopBar
            project={state.project}
            tab={tab}
            sourceOpen={step === "import"}
            panelOpen={sideOpen}
            advancedOpen={panelOpen && pane === "advanced"}
            busy={state.busy}
            onHome={onHome}
            onTab={(next) => {
              // Mesure du parcours (P0) : seuls les changements faits à la main comptent.
              if (next !== tab)
                journalApi.ui(
                  projectId,
                  "tab_changed",
                  `${tab ?? "source"}>${next}`,
                );
              goTab(next);
            }}
            onSource={() => goStep(step === "import" ? "edit" : "import")}
            assistantOpen={panelOpen && pane === "assistant"}
            onAssistant={() => togglePane("assistant")}
            onTogglePanel={toggleSide}
            onToggleAdvanced={() => togglePane("advanced")}
            save={
              board.save === "error" || state.saveFailed
                ? "error"
                : board.save === "saving" || state.busy
                  ? "saving"
                  : "saved"
            }
            onRetrySave={board.save === "error" ? board.retry : undefined}
            onShare={canShare ? () => setShareOpen(true) : undefined}
            bell={<Bell accounts={account !== null} />}
            activityOpen={activityOpen}
            onActivity={toggleActivity}
            commentsCount={comments.threads.filter((t) => !t.resolved).length}
            onComments={openComments}
            peers={live.peers}
            following={live.presence.following}
            onFollow={(userId) => {
              live.presence.follow(userId);
              // Suivre, c'est voir ce que l'autre voit : l'espace de travail.
              if (userId && step !== "board") goTab("board");
            }}
            planName={
              Object.keys(state.project.levels).length > 1
                ? level.name
                : undefined
            }
          />
          {planLocked && leaseOther && (
            <div
              className="lease-banner"
              role="status"
              ref={(el) =>
                el?.style.setProperty("--peer", leaseOther.holder.color)
              }
            >
              {leaseExpired ? (
                <>
                  {leaseOther.holder.name} ne modifie plus ce plan
                  <button
                    type="button"
                    className="link"
                    // Refusé (409) si la personne a repris entre-temps : le bandeau reste.
                    onClick={() =>
                      void leaseApi
                        .take(projectId, level.id)
                        .catch(() => undefined)
                    }
                  >
                    Prendre la main
                  </button>
                </>
              ) : (
                <>
                  {leaseOther.holder.name} modifie ce plan
                  <button
                    type="button"
                    className="link"
                    onClick={() =>
                      void leaseApi
                        .ask(projectId, level.id)
                        .catch(() => undefined)
                    }
                  >
                    Demander la main
                  </button>
                </>
              )}
            </div>
          )}
          {leaseRequest && (
            <div className="lease-banner" role="status">
              {leaseRequest.from} demande la main
              <button
                type="button"
                className="link"
                onClick={() => {
                  live.presence.clearLeaseRequest();
                  void leaseApi
                    .give(projectId, level.id)
                    .catch(() => undefined);
                }}
              >
                Rendre la main
              </button>
            </div>
          )}
          {followedPeer && live.status !== "reconnecting" && (
            <div
              className="follow-banner"
              role="status"
              ref={(el) => el?.style.setProperty("--peer", followedPeer.color)}
            >
              Vous suivez {followedPeer.name}
              <button
                type="button"
                className="link"
                onClick={() => live.presence.follow(null)}
              >
                Arrêter
              </button>
            </div>
          )}
          {live.status === "reconnecting" && (
            <div className="live-banner" role="status">
              <span className="spinner" aria-hidden="true" /> Reconnexion…
            </div>
          )}
          {shareOpen && (
            <ShareDialog
              projectId={projectId}
              onClose={() => setShareOpen(false)}
            />
          )}
          {activityOpen && step !== "board" && (
            <ActivityPanel
              projectId={projectId}
              tick={`${liveTick}|${commentsTick}|${state.project.history_cursor}|${board.board?.revision ?? 0}`}
              onClose={() => setActivityOpen(false)}
            />
          )}
          {newPlanOpen && (
            <NewPlanDialog
              busy={state.busy}
              onClose={() => setNewPlanOpen(false)}
              onCreate={(body) => {
                void actions.newPlan(body).then((lid) => {
                  if (!lid) return;
                  setNewPlanOpen(false);
                  // T7e : le plan créé s'ouvre, prêt à être tracé ou retouché.
                  journalApi.ui(projectId, "tab_changed", "board>plan");
                  goTab("plan");
                });
              }}
            />
          )}
          {step === "edit" && show2d && !readOnly && (
            <ToolPalette
              project={state.project}
              tool={tool}
              viewMode={viewMode}
              busy={state.busy}
              locked={planLocked}
              hasSelection={state.selection.kind !== "none"}
              hasBackground={hasScaleTarget()}
              catalogOpen={panelOpen && pane === "catalog"}
              layoutOpen={panelOpen && pane === "amenager"}
              onTool={(t) => {
                setTool(t);
                if (t !== "scale") setMeasure(null);
              }}
              onUndo={actions.undo}
              onRedo={actions.redo}
              onDetectRooms={() => actions.apply({ op: "detect_rooms" })}
              onDeleteSelection={deleteSelection}
              onCatalog={() => togglePane("catalog")}
              onStairs={openStairs}
              onLayout={() => togglePane("amenager")}
            />
          )}
          {step === "edit" && (
            <ViewBar
              viewMode={viewMode}
              state={{
                section: view3d.section,
                flip: view3d.flip,
                hideRoof,
                xray: view3d.xray,
                dollhouse: view3d.dollhouse,
                ceilings: showCeilings,
                materials,
              }}
              sectionRange={
                view3d.section === "none"
                  ? [0, 1]
                  : sectionRange(view3d.section)
              }
              sectionValue={view3d.value}
              sectionPdfUrl={
                view3d.section === "x" || view3d.section === "y"
                  ? api.exportSectionPdfUrl(
                      state.project.id,
                      level.id,
                      view3d.section,
                      view3d.value,
                    )
                  : null
              }
              onViewMode={(m) => {
                if (m !== viewMode)
                  journalApi.ui(
                    projectId,
                    "tab_changed",
                    `vue:${viewMode}>${m}`,
                  );
                setViewMode(m);
              }}
              onPreset={choosePreset}
              onSection={pickSection}
              onSectionValue={(value) => setView3d({ value })}
              onToggle={toggleView}
            />
          )}
          {show2d && step === "edit" && (
            <PhotoStrip
              project={state.project}
              level={level}
              busy={state.busy}
              selection={state.selection}
              onSelect={actions.select}
              onUpload={actions.importFile}
              onOp={actions.apply}
              onAdvanced={openAdvanced}
            />
          )}
          {showScaleCard && !panelOpen && (
            <RecalibrateCard
              docked={false}
              measure={measure}
              ceilingHeight={level.height}
              busy={state.busy}
              onApply={(body) => {
                actions.recalibrate(body);
                setMeasure(null);
                setTool("select");
              }}
              onPreviewDimensions={actions.previewDimensions}
              onApplyDimensions={(body) => {
                actions.recalibrateDimensions(body);
                setMeasure(null);
                setTool("select");
              }}
              onCancel={() => {
                setMeasure(null);
                setTool("select");
              }}
            />
          )}
          {step !== "import" && step !== "board" && (
            <BottomBar
              project={state.project}
              levelSlot={
                step === "edit" ? (
                  <LevelBar
                    project={state.project}
                    level={level}
                    onOp={actions.apply}
                  />
                ) : null
              }
              levelId={level.id}
              viewMode={viewMode}
              scale={scale}
              violations={state.report?.violations.length ?? 0}
              onZoom={zoom}
              onFit={fit}
              onChecks={showInspector}
            />
          )}
          {/* Au-dessus des barres du bas (bande, barre) : jamais dessus. */}
          <div className="stage-notices" style={{ bottom: insets.bottom + 8 }}>
            {homeNotices.length > 0 && (
              <div
                className="auto-banner estimated"
                role="status"
                aria-label="Depuis l'accueil"
              >
                <span>
                  {homeNotices.map((n, i) => (
                    <span key={i} className="notice">
                      {n}
                    </span>
                  ))}
                </span>
                <button className="link" onClick={() => setHomeNotices([])}>
                  fermer
                </button>
              </div>
            )}
            {/* Pendant Recalibrer, la carte a la place : pas de bandeau. */}
            {step !== "import" &&
              tool !== "scale" &&
              state.autoJob?.status === "running" && (
                <div className="auto-banner" role="status">
                  <span className="spinner" aria-hidden="true" />
                  <span>
                    <strong>Construction du plan</strong> :{" "}
                    {jobSentence(state.autoJob)}
                  </span>
                </div>
              )}
            {step !== "import" &&
              tool !== "scale" &&
              state.lastRecalibration && (
                <div className="auto-banner done" role="status">
                  {state.lastRecalibration.text ? (
                    <span>
                      <strong>Plan recalibré</strong> :{" "}
                      {state.lastRecalibration.text}.
                    </span>
                  ) : (
                    <span>
                      <strong>Plan mis à l'échelle</strong> ×{" "}
                      {state.lastRecalibration.factor.toFixed(3)} :{" "}
                      {state.lastRecalibration.what} ={" "}
                      {state.lastRecalibration.real_m.toFixed(2)} m (était{" "}
                      {state.lastRecalibration.current_m.toFixed(2)} m).
                    </span>
                  )}
                  <button
                    className="link"
                    disabled={state.busy}
                    onClick={() => {
                      actions.undo();
                      actions.clearRecalibration();
                    }}
                  >
                    annuler
                  </button>
                  <button className="link" onClick={actions.clearRecalibration}>
                    fermer
                  </button>
                </div>
              )}
            {step === "edit" &&
              show2d &&
              tool !== "scale" &&
              status &&
              !estimatedBanner && (
                <div
                  className={`plan-status-line ${status}`}
                  role="status"
                  aria-label="Statut du plan"
                >
                  <span className={`plan-status ${status}`}>
                    {PLAN_STATUS_LABEL[status]}
                  </span>
                  <span className="muted">
                    {status === "estimated"
                      ? "reconstruit d'après les photos, pas un relevé : « ≈ » = cote estimée, sans « ≈ » = saisie ou mesurée"
                      : "proposé d'après votre texte : ajustez les cotes, « ≈ » = cote non saisie"}
                  </span>
                  <button
                    className="link"
                    onClick={recalibrate}
                    aria-label="Recalibrer le plan"
                  >
                    Recalibrer
                  </button>
                </div>
              )}
            {estimatedBanner && state.lastAuto && (
              <div className="auto-banner estimated" role="status">
                <span>
                  <strong>Plan estimé</strong> : vérifiez l'échelle avec une
                  longueur que vous connaissez.
                  {state.lastAuto.run.notices.map((n, i) => (
                    <span key={i} className="muted notice">
                      {n}
                    </span>
                  ))}
                </span>
                <button className="primary" onClick={recalibrate}>
                  Recalibrer
                </button>
                {state.project.history_cursor ===
                  state.lastAuto.run.project.history_cursor && (
                  <button
                    className="link"
                    disabled={state.busy}
                    onClick={() =>
                      actions.rereadAs(
                        state.lastAuto?.run.kind === "plan" ? "photo" : "plan",
                      )
                    }
                    title="L'image est relue autrement : ce qui vient d'être construit est remplacé"
                  >
                    {state.lastAuto.run.kind === "plan"
                      ? "ce n'est pas un plan"
                      : "ce n'est pas une photo"}
                  </button>
                )}
                <button className="link" onClick={() => setNoticeClosed(true)}>
                  fermer
                </button>
              </div>
            )}
            {step === "edit" &&
              tool !== "scale" &&
              starterArea !== null &&
              !starterClosed && (
                <div className="auto-banner estimated" role="status">
                  <span>
                    <strong>Plan de départ simplifié</strong> : une pièce de{" "}
                    {String(starterArea).replace(".", ",")} m² en rectangle,
                    déduite de votre texte ; ce n'est pas un relevé. Corrigez
                    murs et ouvertures ici.
                  </span>
                  <button
                    className="link"
                    onClick={() => setStarterClosed(true)}
                  >
                    fermer
                  </button>
                </div>
              )}
            {step !== "import" && generated && !generatedClosed && (
              <div
                className="auto-banner estimated"
                role="status"
                aria-label="Plan généré"
              >
                <span>
                  <strong>Plan généré depuis votre programme</strong> : murs,
                  portes et fenêtres sont inventés, ce n'est pas un relevé ;
                  corrigez-les dans le plan.
                  {(generated.notes ?? []).length > 0 &&
                    ` ${(generated.notes ?? []).map((n) => n.charAt(0).toUpperCase() + n.slice(1)).join(". ")}.`}
                </span>
                <button
                  className="link"
                  onClick={() => setGeneratedClosed(true)}
                >
                  fermer
                </button>
              </div>
            )}
            {state.error && (
              <div className="error-banner" role="alert">
                {state.error}
                <button className="link" onClick={actions.clearError}>
                  fermer
                </button>
              </div>
            )}
          </div>
        </div>
        {/* Le panneau reste monté quand il est replié : ses fiches (Aménager en trois pas,
          conversation) gardent leur état d'un repli à l'autre. */}
        {step !== "import" && (
          <div
            className={`side${step === "board" ? " board" : ""}${sideOpen ? "" : " closed"}`}
            style={{ "--side-w": `${panelWidth}px` } as React.CSSProperties}
          >
            <SideHandle
              width={panelWidth}
              onWidth={setSideWidth}
              onCommit={(w) => saveSideWidth(w)}
            />
            <div className="side-card">
              {
                <>
                  {/* Recalibrer : la carte se range dans le panneau, hors du plan à cliquer. */}
                  {showScaleCard && panelOpen && (
                    <RecalibrateCard
                      docked
                      measure={measure}
                      ceilingHeight={level.height}
                      busy={state.busy}
                      onApply={(body) => {
                        actions.recalibrate(body);
                        setMeasure(null);
                        setTool("select");
                      }}
                      onPreviewDimensions={actions.previewDimensions}
                      onApplyDimensions={(body) => {
                        actions.recalibrateDimensions(body);
                        setMeasure(null);
                        setTool("select");
                      }}
                      onCancel={() => {
                        setMeasure(null);
                        setTool("select");
                      }}
                    />
                  )}
                  {shownPane !== "inspector" && (
                    <div className="side-head">
                      <h2>{PANE_TITLE[shownPane]}</h2>
                      <button
                        className="icon"
                        onClick={showInspector}
                        aria-label={`Fermer ${PANE_TITLE[shownPane]}`}
                        title="Revenir à l'inspecteur"
                      >
                        <IconClose />
                      </button>
                    </div>
                  )}
                  {boardZones && (
                    <BoardSide
                      comments={comments}
                      commentsOpen={commentsZoneOpen}
                      onCommentsOpen={setCommentsZoneOpen}
                      activity={
                        activityOpen && (
                          <ActivityPanel
                            inline
                            projectId={projectId}
                            tick={`${liveTick}|${commentsTick}|${state.project.history_cursor}|${board.board?.revision ?? 0}`}
                            onClose={() => setActivityOpen(false)}
                          />
                        )
                      }
                      activityOpen={activityOpen}
                      onActivityOpen={setActivityOpen}
                      onSlots={setBoardSlots}
                    />
                  )}
                  {shownPane === "inspector" &&
                    !boardZones &&
                    !readOnly &&
                    inspector}
                  {shownPane === "catalog" && !readOnly && (
                    <aside className="inspector">
                      <LibraryPanel
                        chosen={placing?.assetId ?? null}
                        busy={state.busy}
                        circulationToken={circulationToken}
                        onChoose={(a) => {
                          // Poser un meuble se fait avec l'outil Sélection : un autre outil
                          // (Mur, Porte…) choisi ensuite abandonne la pose.
                          if (a) setTool("select");
                          setPlacing(
                            a && {
                              assetId: a.id,
                              category: a.category,
                              label: a.label,
                              width: a.width.default,
                              depth: a.depth.default,
                            },
                          );
                        }}
                        onChooseStairs={(c) => {
                          if (c) setTool("select");
                          setPlacing(
                            c && {
                              assetId: `stairs:${c.kind}`,
                              category: "stairs",
                              label: c.label,
                              width: c.along,
                              depth: c.across,
                              stairs: c.kind,
                            },
                          );
                        }}
                        onChooseOpening={(c) => {
                          if (c) setTool("select");
                          setPlacing(
                            c && {
                              assetId: `opening:${c.id}`,
                              category: "opening",
                              label: c.label,
                              width: c.width,
                              depth: 0.2,
                              opening: c,
                            },
                          );
                        }}
                      />
                    </aside>
                  )}
                  {shownPane === "assistant" && !readOnly && assistantPanel}
                  {shownPane === "renders" && !readOnly && renderPanel}
                  {shownPane === "contextual" &&
                    contextualPick &&
                    !readOnly && (
                      <ContextualPanel
                        key={`${contextualPick.kind}:${contextualPick.id}`}
                        project={state.project}
                        level={level}
                        renders={state.renders}
                        pick={contextualPick}
                        references={(board.board?.items ?? [])
                          .filter((i) => i.reference && i.ref)
                          .map((i) => ({
                            kind: i.kind as
                              "photo" | "render" | "contextual" | "file",
                            ref: i.ref ?? "",
                            label:
                              i.text ||
                              (i.kind === "photo"
                                ? "Photo"
                                : i.kind === "render"
                                  ? "Rendu"
                                  : "Image"),
                          }))}
                        onGenerated={() => setContextualTick((n) => n + 1)}
                        onClose={() => {
                          setContextualPick(null);
                          setPane("inspector");
                        }}
                      />
                    )}
                  {shownPane === "amenager" && !readOnly && (
                    <AmenagerPanel
                      project={state.project}
                      level={level}
                      busy={state.busy}
                      lastBrief={state.lastBrief}
                      lastLayouts={state.lastLayouts}
                      ghost={state.ghost}
                      onSettings={actions.updateSettings}
                      onAskAssistant={() => openAssistant()}
                      onProposeLayouts={actions.proposeLayouts}
                      onPreviewProposal={actions.previewProposal}
                      onApplyProposal={actions.applyProposal}
                      lastCritique={state.lastCritique}
                      onCritique={actions.critiqueProposal}
                      onRevise={actions.reviseProposal}
                      compare={state.compare}
                      onCompare={actions.compareProposal}
                      renders={state.renders}
                      renderJob={state.renderJob}
                      onRenderProposal={actions.renderProposal}
                    />
                  )}
                  {shownPane === "advanced" && readOnly && (
                    <details className="adv-group" open>
                      <summary>Réglages : thème et compte</summary>
                      <aside className="inspector">
                        <ThemeSwitch />
                        <AccountSection />
                      </aside>
                    </details>
                  )}
                  {shownPane === "advanced" && !readOnly && (
                    <>
                      <details className="adv-group" open>
                        <summary>Réglages : unités, nord et thème</summary>
                        <SettingsPanel
                          project={state.project}
                          level={level}
                          onOp={actions.apply}
                          onSettings={actions.updateSettings}
                        />
                        <AccountSection />
                      </details>
                      <details className="adv-group" open>
                        <summary>
                          Assistant IA : modèle, consentement, journal
                        </summary>
                        <AiPanel
                          project={state.project}
                          level={level}
                          busy={state.busy}
                          lastPerception={state.lastPerception}
                          lastBrief={state.lastBrief}
                          onSettings={actions.updateSettings}
                          onPerceive={actions.perceive}
                          onOp={actions.apply}
                        />
                      </details>
                      <details className="adv-group" open>
                        <summary>État de l'installation</summary>
                        <aside className="inspector">
                          <InstallState />
                        </aside>
                      </details>
                      <AutoDiagnostic last={state.lastAuto} />
                      <details className="adv-group experts">
                        <summary>Outils experts (rarement utiles)</summary>
                        <details className="adv-group" open>
                          <summary>Photos : fond du plan et vues</summary>
                          <PhotoPanel
                            project={state.project}
                            level={level}
                            busy={state.busy}
                            report={state.report}
                            selection={state.selection}
                            onSelect={actions.select}
                            onAdoptView={actions.adoptView}
                            onRotateView={actions.rotateView}
                            imageWarnings={state.imageWarnings}
                            lastInspiration={state.lastInspiration}
                            inspirationJob={state.inspirationJob}
                            onInspire={actions.inspire}
                            onUpload={actions.importFile}
                            onRemove={actions.removeImage}
                            onOp={actions.apply}
                          />
                        </details>
                        <details className="adv-group" open>
                          <summary>
                            Calibration : mesures et facteur d'échelle
                          </summary>
                          <aside className="inspector">
                            <MeasurementsPanel
                              project={state.project}
                              level={level}
                              derived={state.derived}
                              report={state.report}
                              selection={state.selection}
                              lastCalibration={state.lastCalibration}
                              busy={state.busy}
                              onOp={actions.apply}
                              onAddMeasurement={actions.addMeasurement}
                              onRemoveMeasurement={actions.removeMeasurement}
                              onCalibrate={actions.calibrate}
                              onSettings={actions.updateSettings}
                              onAdoptView={actions.adoptView}
                              onRotateView={actions.rotateView}
                            />
                          </aside>
                        </details>
                        <details className="adv-group" open>
                          <summary>
                            Blender : fichier .blend et rendu depuis la photo
                          </summary>
                          <aside className="inspector">
                            <BlenderPanel
                              projectId={state.project.id}
                              levelId={level.id}
                            />
                          </aside>
                        </details>
                        <details className="adv-group" open>
                          <summary>Données : provenance et confiance</summary>
                          <aside className="inspector">
                            <Legend />
                          </aside>
                        </details>
                      </details>
                    </>
                  )}
                </>
              }
            </div>
          </div>
        )}
      </div>
    </ReadOnlyContext.Provider>
  );
}

/** Poignée du panneau de droite (bord gauche) : glisser change la largeur entre 260 et
 *  480 px ; au clavier, flèches (Maj : par 40 px), Début / Fin ; double-clic : largeur par
 *  défaut. La largeur choisie est retenue (`onCommit`, null = par défaut). */
function SideHandle({
  width,
  onWidth,
  onCommit,
}: {
  width: number;
  onWidth: (w: number | null) => void;
  onCommit: (w: number | null) => void;
}) {
  const drag = useRef<{ x: number; w: number; last: number } | null>(null);
  const set = (w: number) => {
    const next = clampSide(w);
    onWidth(next);
    return next;
  };
  return (
    <div
      className="side-handle"
      role="separator"
      aria-orientation="vertical"
      aria-label="Largeur du panneau"
      aria-valuemin={SIDE_MIN}
      aria-valuemax={SIDE_MAX}
      aria-valuenow={width}
      tabIndex={0}
      title="Glisser pour élargir ou réduire le panneau (flèches au clavier, double-clic : largeur par défaut)"
      onPointerDown={(e) => {
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        drag.current = { x: e.clientX, w: width, last: width };
      }}
      onPointerMove={(e) => {
        const d = drag.current;
        if (!d) return;
        // Le panneau est à droite : tirer vers la gauche l'élargit.
        d.last = set(d.w + d.x - e.clientX);
      }}
      onPointerUp={(e) => {
        const d = drag.current;
        drag.current = null;
        e.currentTarget.releasePointerCapture(e.pointerId);
        if (d && d.last !== d.w) onCommit(d.last);
      }}
      onDoubleClick={() => {
        onWidth(null);
        onCommit(null);
      }}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 40 : 10;
        let next: number | null = null;
        if (e.key === "ArrowLeft") next = width + step;
        else if (e.key === "ArrowRight") next = width - step;
        else if (e.key === "Home") next = SIDE_MIN;
        else if (e.key === "End") next = SIDE_MAX;
        if (next === null) return;
        e.preventDefault();
        onCommit(set(next));
      }}
    />
  );
}

/** Chemin « zéro geste » en cours (V1, point 6) : l'étape, une barre et le temps restant
 *  estimé ; posé par-dessus la scène sans la bloquer (l'éditeur reste utilisable). */
function PilotProgress({ job }: { job: JobStatus }) {
  // Temps restant annoncé par le serveur (durées mesurées, jamais nul pendant une étape) ;
  // à défaut, l'estimation de départ moins le temps écoulé.
  const remaining =
    job.remaining_s ?? Math.max(0, (job.expected_s ?? 0) - job.elapsed_s);
  const total = job.elapsed_s + remaining;
  const progress = total > 0 ? Math.min(99, (job.elapsed_s / total) * 100) : 0;
  const left =
    remaining >= 5
      ? `environ ${remaining.toFixed(0)} s restantes`
      : "encore quelques secondes";
  return (
    <div
      className="pilot-progress"
      role="status"
      aria-label="Chemin automatique"
    >
      <strong>Rendus en préparation</strong>
      <span>
        {job.stage || job.message} · {left}
      </span>
      <span className="progress">
        <span style={{ width: `${progress}%` }} />
      </span>
      <em className="muted">
        Vous pouvez continuer à éditer pendant ce temps.
      </em>
    </div>
  );
}
