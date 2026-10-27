import { useCapabilities, type Capabilities } from "../state/capabilities";
import type { Requirements } from "../guided";
import { useReadOnly } from "../state/account";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { createPortal } from "react-dom";
import {
  api,
  boardApi,
  inspirationApi,
  renderApi,
  contextualApi,
  type Board,
  type BoardItem,
  type BoardLink,
  type BoardView,
  type ContextualEntry,
  type MediaRef,
  type ProjectSettingsUpdate,
  type RenderEntry,
} from "../api/client";
import type { Project } from "../api/types.gen";
import {
  IMPORT_ACCEPT,
  isPlanFile,
  PHOTO_TYPES,
  QUEUED_TEXT,
} from "../importFiles";
import { walls } from "../model";
import {
  IconAlert,
  IconComment,
  IconCursor,
  IconFrame,
  IconHand,
  IconLink,
  IconMoodboardNode,
  IconNote,
  IconPencil,
  IconPhotosNode,
  IconPlanNode,
  IconPlus,
  IconRendersNode,
  IconReact,
  IconRedo,
  IconSliders,
  IconUndo,
  IconUpload,
} from "../ui/icons";
import { AuthedImg } from "./AuthedImg";
import { ContextualCard, LinkCard, PdfCard, VideoCard } from "./MediaCards";
import { Composer } from "./Composer";
import { PresenceLayer } from "./PresenceLayer";
import { PostitBody, PostitPalette } from "./Postit";
import {
  colorOf,
  lastColor,
  POSTIT_SIDE,
  rememberColor,
  resizedPostit,
} from "./postits";
import { PhotoMeasure } from "./PhotoMeasure";
import { CLOSE_UP_TEXT, PHOTO_GUIDE_LINK, PhotoGuide } from "./PhotoGuide";
import { RealisticRenders } from "./RealisticRenders";
import { WireBanner, WireLayer, WireToast, type WireDrag } from "./BoardLinks";
import { overlapping } from "./wires";
import {
  accepts,
  cardRole,
  cardTitle,
  dropEffect,
  hasPlan,
  linkRefusal,
  newLink,
  NO_PLAN,
  OFF_CARD,
  offCard,
  plansFedBy,
} from "./links";
import { referenceOf } from "../state/photoReference";
import { CommentPins, CommentsPanel } from "./comments";
import { ReactionRows } from "./Reactions";
import { useHoveredItem, type ReactionsController } from "./useReactions";
import { pinPlace, type CommentsController } from "./useComments";
import { usePresence, type Peer, type PresenceOut } from "../state/live";
import {
  hostOf,
  linkFrom,
  MEDIA_SIZE,
  mediaItemKind,
  useDarkTheme,
} from "./mediaItems";

/** Tableau du projet (B2, maquette validée par augus le 13 oct.) : un espace libre type
 *  Figma/Miro où sont posés les photos, la note de la demande, les rendus, le plan et les
 *  fichiers joints. Déplacer, redimensionner, sélectionner à plusieurs, supprimer, dupliquer,
 *  verrouiller, ordonner, ranger dans des cadres nommés ; zoom et déplacement de la vue ;
 *  annuler / rétablir propres au tableau. Ce que montre un élément ne change jamais avec sa
 *  taille : le cadre du plan n'a pas d'échelle. */

type Tool = "select" | "hand" | "note" | "frame" | "comment";

/** KAN-77 (MH10, MH11) : les outils de la barre de gauche, en icônes au trait (ui/icons,
 *  même famille que la barre du haut) ; le libellé sert d'infobulle, son premier mot de nom. */
const RAIL_TOOLS: readonly [
  Tool,
  (p: React.SVGProps<SVGSVGElement>) => React.JSX.Element,
  string,
][] = [
  ["select", IconCursor, "Sélection (V)"],
  ["hand", IconHand, "Main : déplacer la vue (H, ou espace)"],
  ["note", IconNote, "Post-it (N) : cliquer où le poser"],
  [
    "frame",
    IconFrame,
    "Cadre (F) : tracer un cadre, ce qui est dedans lui appartient",
  ],
  [
    "comment",
    IconComment,
    "Commentaire (C) : cliquer où le poser, ou sur un élément",
  ],
];
type Drag =
  | { kind: "pan"; x: number; y: number; view: BoardView }
  | { kind: "move"; x: number; y: number; start: BoardItem[] }
  | { kind: "resize"; x: number; y: number; item: BoardItem }
  | { kind: "marquee"; x: number; y: number; to: { x: number; y: number } }
  | { kind: "frame"; x: number; y: number; to: { x: number; y: number } };

const MIN_SIDE = 40;
const ZOOM_MIN = 0.1;
const ZOOM_MAX = 4;
const FIT_MARGIN = 60;
/** Entrée dans le plan : marge autour du cadre et durée du zoom (B3). */
const ENTER_MARGIN = 24;
const ENTER_MS = 280;
/** Deux appuis sur le même élément en moins de ce délai : un double-clic (T7d2). */
const DOUBLE_MS = 450;
/** KAN-50 : un lecteur dépose ou colle quelque chose : rien n'est ajouté, et il le sait. */
const READ_ONLY_NOTICE = "Lecture seule : rien n'est ajouté à ce projet.";
/** KAN-91 : durée du bandeau « Annuler » (dépôt, lien coupé) et délai avant le recalcul du
 *  plan dont les photos ont changé. */
const TOAST_MS = 6000;
const DRAG_THRESHOLD_PX = 5;
const WALLS_STAY =
  "Les murs déjà construits restent ; refaites le plan depuis les photos du node pour repartir de zéro.";
/** « + Nouveau plan » sous la dernière rangée : écart, et boîte approchée pour le cadrage. */
/** KAN-98 : une carte ajoutée par la barre, 25 % plus grande que celles d'avant (624 × 374) ;
 *  les cartes déjà posées gardent leur taille. */
const NEW_CARD = { w: 780, h: 468 };
const NEW_CARD_GAP = 48;
const PHOTOS_TITLE = "Photos de la pièce";
const MOODBOARD_TITLE = "Moodboard";

interface Props {
  project: Project;
  board: Board;
  renders: RenderEntry[];
  stale: Set<string>;
  notice: string | null;
  canUndo: boolean;
  canRedo: boolean;
  /** Le tableau reçoit le clavier (pas un champ, pas un autre espace). */
  active: boolean;
  planVersion: number;
  onCommit: (items: BoardItem[], record?: boolean) => void;
  onView: (view: BoardView) => void;
  onUndo: () => void;
  onRedo: () => void;
  onDismissNotice: () => void;
  /** Une photo importée sur le tableau entre dans le projet : le projet à jour. */
  onProjectChanged: (project: Project) => void;
  /** T7d2 : un rendu en grand dans la visionneuse ; "" = la première vue. */
  onOpenRender: (runId: string) => void;
  /** KAN-91 : pose ou coupe des liens entre cartes ; résout à vrai si le serveur accepte. */
  onWriteLinks: (set: BoardLink[], cut: string[]) => Promise<boolean>;
  /** KAN-91 : des photos sont entrées dans la carte Photos d'un plan ou en sont sorties (après
   *  le délai d'« Annuler ») : le plan se recalcule. */
  onPlanMembers?: (levelId: string, added: string[], removed: string[]) => void;
  onEditPlan: (levelId: string) => void;
  onVariant: (text: string) => void;
  /** T1 : la note Exigences écrit la consigne et les réponses gardées dans le projet. */
  onSettings: (body: ProjectSettingsUpdate) => void;
  /** T3 : « Générer le plan » depuis le cadre Plan vide ; `planProgress` : la phrase de la
   *  tâche en cours (construction du plan, chemin automatique), montrée dans le cadre. */
  onGeneratePlan: () => void;
  planProgress: string | null;
  /** File d'attente : un calcul de ce projet attend son tour (le cadre Rendus du plan
   *  ouvert le dit, en une ligne, avec « Annuler »). */
  queued?: boolean;
  onCancelQueued?: () => void;
  /** T4 : une photo réelle déposée (projet à jour, id de la première) ; le chemin décide
   *  selon la bascule « générer au dépôt d'une photo ». */
  onPhotoDropped: (project: Project, imageId: string, file?: File) => void;
  /** T4 : « Générer maintenant » (plan s'il manque, sinon chemin automatique). */
  onGenerateNow: (fresh: Requirements) => void;
  /** T4 : il y a déjà des rendus (la barre du déclencheur se retire). */
  hasRenders: boolean;
  /** T7c : « Importer › Photo ou plan de la pièce » ouvre « Ajouter une source ». */
  onAddSource: () => void;
  /** T7a : un plan (DXF, PDF, SVG) importé dans le cadre Photos : lu comme un plan. */
  onImportPlan?: (file: File) => void;
  /** KAN-91 L5 : la dernière construction de plan a vu un gros plan (un détail, pas la pièce) :
   *  l'alerte et le contour de la photo, jusqu'à « fermer » (une fois par construction). */
  closeUp?: { runId: string; imageId: string } | null;
  /** T7d1 : chaque incrément cadre la vue sur le cadre Rendus (arrivée, chemin automatique). */
  focusRenders?: number;
  /** KAN-64 : les zones du panneau de droite où l'espace de travail range l'Assistant (ou
   *  les actions de la sélection) et les commentaires ; null quand le panneau est fermé. */
  side?: { top: HTMLElement | null; comments: HTMLElement | null } | null;
  /** KAN-52 : arrivée par la cloche, la vue se centre sur l'épingle d'un fil ou un élément
   *  (une fois par jeton). */
  focusOn?: {
    token: number;
    threadId: string | null;
    itemId: string | null;
  } | null;
  /** T7d1 : « Re-rendre » les vues périmées depuis l'en-tête du cadre Rendus (T7e : sur le
   *  plan de ce cadre). */
  onRerender: (cameraIds: string[], levelId?: string) => void;
  /** T7d1 : ouvre le volet Rendus (réglages, vues, points de vue, galerie) ; T7e : du plan
   *  de ce cadre, qui devient le plan ouvert. */
  onOpenRendersPane: (levelId?: string) => void;
  /** T7e : « Voir en grand » d'un cadre Rendus : la visionneuse sur la première vue de son
   *  plan. */
  onOpenViewer: (levelId: string) => void;
  /** T7e : le plan ouvert (cadrage sur ses rendus). */
  activeLevelId?: string;
  /** T7e : ouvrir un autre plan. */
  onOpenLevel?: (levelId: string) => void;
  /** « + Nouveau plan » (T7e ; retour d'augus du 19 oct. : sous la dernière rangée Plan +
   *  Rendus, plus dans la barre du haut) ; absent pour un lecteur. */
  onNewPlan?: () => void;
  /** KAN-91 (retours MEP 14) : « Supprimer le plan » (après confirmation) ; absent pour un
   *  lecteur. */
  onDeletePlan?: (levelId: string) => void;
  /** A1 : « Rendu contextualisé… » depuis une photo, un rendu ou le plan. */
  onContextual: (pick: {
    kind: "photo" | "render" | "plan";
    id: string;
    label: string;
  }) => void;
  /** B2b : présence des autres (curseurs, sélections, « Suivre ») ; absente sans comptes. */
  presence?: PresenceOut;
  peers?: Peer[];
  /** KAN-51 : commentaires (épingles, fils, panneau), en local comme avec les comptes. */
  comments?: CommentsController;
  /** KAN-78 : réactions aux éléments (pastilles, sélecteur). */
  reactions?: ReactionsController;
}

/** T2, T7d1 : ce qu'un cadre fixe attend, tant qu'il est vide : une ligne courte (retour
 *  d'augus, 16 oct.) ; l'explication passe dans l'infobulle. */
const FRAME_HINTS: Record<string, string> = {
  photos: "Déposez vos photos",
  inspirations: "Déposez vos images d'ambiance",
  renders: "Les rendus apparaîtront ici",
};
/** KAN-50 : chez un lecteur, un cadre vide le dit sans l'inviter à rien déposer (ni la ligne
 *  « Plan depuis photo », qui ne le concerne pas). */
const READ_ONLY_EMPTY: Record<string, string> = {
  photos: "Aucune photo de la pièce",
  inspirations: "Aucune image d'ambiance",
};
/** T7a (demande d'augus, 18 oct.) : importer droit dans un cadre ; le sélecteur de fichiers
 *  ne propose que ce que le cadre attend. */
const FRAME_IMPORT: Record<string, { accept: string; tip: string }> = {
  photos: { accept: IMPORT_ACCEPT, tip: "Ajouter des photos" },
  inspirations: {
    accept: PHOTO_TYPES.join(","),
    tip: "Ajouter des images d'ambiance",
  },
};
const FRAME_TIPS: Record<string, string> = {
  photos:
    "Les photos réelles de la pièce font le plan. Aussi : coller, ou « Importer » à gauche (plan DXF, PDF, image).",
  inspirations:
    "Vos images d'ambiance : cochées Référence, leur allure guide les rendus.",
  renders:
    "Une image par vue, dès qu'un plan existe ; « Réglages et galerie » pour le reste.",
};

const uid = () =>
  globalThis.crypto?.randomUUID?.() ??
  "xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx".replace(/x/g, () =>
    Math.floor(Math.random() * 16).toString(16),
  );

function inside(item: BoardItem, frame: BoardItem): boolean {
  const cx = item.x + item.w / 2;
  const cy = item.y + item.h / 2;
  return (
    cx >= frame.x &&
    cx <= frame.x + frame.w &&
    cy >= frame.y &&
    cy <= frame.y + frame.h
  );
}

/** En-tête d'un cadre Rendus (T7d1, T7d2 ; T7e : un par plan) : re-rendre ses vues
 *  périmées, les voir en grand, ouvrir le volet Rendus de son plan. */
/** Ligne courte d'un cadre quand la capacité manque (lot MVP en ligne sans GPU). */
function missingHint(role: string, caps: Capabilities): string | null {
  if (role === "renders" && !caps.renders)
    return caps.online
      ? "Rendus réalistes : bientôt en ligne"
      : "Rendus réalistes : Blender n'est pas installé";
  if (role === "photos" && !caps.photoPlan)
    return "Plan depuis photo : bientôt en ligne";
  return null;
}

/** KAN-91 (retours MEP 14) : la confirmation dit ce qui part et ce qui reste. Le premier plan
 *  ne se supprime pas encore (ses cartes sont celles de départ) : le bouton le dit. */
const FIRST_PLAN =
  "Le premier plan du projet ne se supprime pas encore : ses nodes Photos de la pièce et Rendus sont ceux de départ.";

function DeletePlanButton({
  name,
  first,
  onDelete,
}: {
  name: string;
  first: boolean;
  onDelete: () => void;
}) {
  return (
    <button
      type="button"
      className="board-plan-delete"
      disabled={first}
      title={first ? FIRST_PLAN : "Supprimer ce plan (Ctrl+Z le rend)"}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={() => {
        if (
          window.confirm(
            `Supprimer le plan « ${name} » ? Son node Photos de la pièce et ses liens partent avec lui ; ses photos restent sur l'espace de travail, hors node. Les autres plans ne changent pas. Ctrl+Z le rend.`,
          )
        )
          onDelete();
      }}
    >
      Supprimer le plan
    </button>
  );
}

function RendersFrameActions({
  levelId,
  stale,
  hasRender,
  noPlan,
  onRerender,
  onOpenViewer,
  onOpenRendersPane,
}: {
  levelId: string;
  stale: string[];
  hasRender: boolean;
  /** KAN-91 (D2) : aucun plan relié à cette carte : elle ne rend pas. */
  noPlan: boolean;
  onRerender: (cameraIds: string[], levelId?: string) => void;
  onOpenViewer: (levelId: string) => void;
  onOpenRendersPane: (levelId?: string) => void;
}) {
  const n = useCapabilities().renders ? stale.length : 0;
  return (
    <span
      className="board-frame-actions"
      onPointerDown={(e) => e.stopPropagation()}
    >
      {noPlan && (
        <span className="board-wire-noplan" role="note">
          {NO_PLAN}
        </span>
      )}
      {n > 0 && (
        <button
          type="button"
          className="board-stale-btn"
          disabled={noPlan}
          onClick={() => onRerender(stale, levelId)}
          title={
            noPlan
              ? NO_PLAN
              : "Le plan, l'ambiance ou le point de vue ont changé depuis ces rendus"
          }
        >
          Re-rendre {n} vue{n > 1 ? "s" : ""} périmée{n > 1 ? "s" : ""}
        </button>
      )}
      {hasRender && (
        <button
          type="button"
          onClick={() => onOpenViewer(levelId)}
          title="Les rendus en grand, un par un : télécharger, affiner, désigner un meuble (← → pour passer de l'un à l'autre)"
        >
          Voir en grand
        </button>
      )}
      {/* T7a : en icône seule, le nom dans l'infobulle et pour les lecteurs d'écran. */}
      <button
        type="button"
        className="icon board-frame-icon"
        aria-label="Réglages et galerie"
        onClick={() => onOpenRendersPane(levelId)}
        title="Réglages et galerie : vues automatiques, points de vue, qualité et éclairage, « Rendre toutes les vues », galerie"
      >
        <IconSliders />
      </button>
    </span>
  );
}

export function BoardCanvas(props: Props) {
  const { project, board, renders, active } = props;
  /** T7e : le plan d'un cadre Rendus (`ref`) ; celui d'avant T7e, sans `ref`, est au premier
   *  plan. */
  const firstLevelId = Object.keys(project.levels)[0] ?? "";
  const frameLevel = (frame: BoardItem): string => frame.ref ?? firstLevelId;
  const stage = useRef<HTMLDivElement>(null);
  const lastDown = useRef<{ id: string; t: number } | null>(null);
  const [tool, setTool] = useState<Tool>("select");
  /** KAN-64 : la zone « Assistant » du panneau, repliable. */
  const [assistantOpen, setAssistantOpen] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [drag, setDrag] = useState<Drag | null>(null);
  const [live, setLive] = useState<BoardItem[] | null>(null);
  const [view, setViewState] = useState<BoardView>(board.view);
  const [editing, setEditing] = useState<string | null>(null);
  const [media, setMedia] = useState<MediaRef[]>([]);
  const [contextual, setContextual] = useState<ContextualEntry[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [zooming, setZooming] = useState(false);
  /** Formulaire « Lien » (C1) : adresse et titre saisis ; la page n'est jamais récupérée.
   *  KAN-77 : ouvert par « Ajouter un lien » du cadre Inspirations (`frame`), ou la touche L. */
  const [linkForm, setLinkForm] = useState<{
    url: string;
    title: string;
    frame?: string;
  } | null>(null);
  /** KAN-66 : la photo ouverte dans le calque « Mesurer sur la photo ». */
  const [measureFor, setMeasureFor] = useState<{
    id: string;
    label: string;
  } | null>(null);
  /** KAN-91 : le lien choisi (barre « Couper »), le lien tiré depuis une poignée, les liens
   *  coupés à l'instant (pointillé fantôme), le bandeau avec « Annuler », la phrase de dépôt
   *  d'une image dans (ou hors d') une carte, et le recalcul du plan qui attend son délai. */
  const [wireSelected, setWireSelected] = useState<string | null>(null);
  const [wireDrag, setWireDrag] = useState<WireDrag | null>(null);
  const [ghosts, setGhosts] = useState<BoardLink[]>([]);
  const [toast, setToast] = useState<{
    text: string;
    undo?: () => void;
  } | null>(null);
  const [dropHint, setDropHint] = useState<{
    frame: string | null;
    text: string;
    ok: boolean;
    x: number;
    y: number;
  } | null>(null);
  const recompute = useRef<number | null>(null);
  /** KAN-91 L5 : guide de prise de photo ouvert, pour ce cadre Photos (son « Importer des
   *  photos ») ou sans cadre ; il ne s'ouvre que par son lien. */
  const [guide, setGuide] = useState<{ frame: BoardItem | null } | null>(null);
  /** KAN-91 L5 : construction dont l'alerte « gros plan » a été fermée. */
  const [closeUpDone, setCloseUpDone] = useState<string | null>(null);
  const closeUp =
    props.closeUp && props.closeUp.runId !== closeUpDone ? props.closeUp : null;
  const dark = useDarkTheme();
  /** KAN-78 : l'élément survolé (son bouton « Réagir » sous lui). */
  const hover = useHoveredItem();
  const caps = useCapabilities();
  // B1d : lecteur (et commentateur jusqu'à B2) : ni outils, ni compositeur, ni « Générer ».
  const readOnly = useReadOnly();
  const space = useRef(false);
  const items = live ?? board.items;
  /** B2c : le tableau d'à présent, pour un geste qui se termine après une attente. */
  const itemsNow = useRef(board.items);
  useEffect(() => {
    itemsNow.current = board.items;
  }, [board.items]);
  // KAN-98 : plus de boutons « + Nouveau plan » ni « + Nouvelle carte… » posés dans la toile ;
  // la barre d'ajout, fixe en bas au centre, les remplace.
  const plans = items.filter((i) => i.kind === "plan");
  const byRun = useMemo(
    () => new Map(renders.map((r) => [r.run_id, r])),
    [renders],
  );

  // KAN-56 : médias et rendus contextualisés relus quand les références des éléments
  // changent (un ajout ou un retrait, chez moi ou chez un autre membre), pas à chaque
  // révision du tableau : zoomer, déplacer ou annoter ne les relit plus. Un média ou une
  // variante ne change pas sous la même référence (vignette faite à l'envoi, variante
  // listée seulement une fois son image écrite) ; si un statut y apparaît un jour, il
  // entre dans cette clé.
  const refsKey = useMemo(
    () =>
      board.items
        .filter((i) => i.ref)
        .map((i) => `${i.kind}:${i.ref}`)
        .sort()
        .join("|"),
    [board.items],
  );
  useEffect(() => {
    void boardApi.media(project.id).then(setMedia, () => setMedia([]));
    void contextualApi
      .list(project.id)
      .then(setContextual, () => setContextual([]));
  }, [project.id, refsKey]);

  // Première ouverture : la vue cadre tout ce qui est posé.
  const fitted = useRef(false);
  useEffect(() => {
    if (fitted.current || !stage.current) return;
    fitted.current = true;
    const v = board.view;
    if (v.x !== 0 || v.y !== 0 || v.zoom !== 1) return;
    // Ce cadrage automatique n'est pas un geste : il n'est pas enregistré (B4).
    fit(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // T7d1 : à l'arrivée d'un projet qui a des rendus (ou du chemin automatique), la vue cadre
  // le cadre Rendus et ce qu'il contient ; pas un geste, non enregistré.
  useEffect(() => {
    if (!props.focusRenders || !stage.current) return;
    // T7e : le cadre Rendus du plan ouvert.
    const frames = items.filter(
      (i) => i.kind === "frame" && i.role === "renders",
    );
    const frame =
      frames.find((i) => frameLevel(i) === props.activeLevelId) ?? frames[0];
    if (!frame) return;
    fit(false, [frame, ...items.filter((i) => i.frame === frame.id)]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.focusRenders]);

  // B2b : « Suivre ». Un geste de ma part (clic, molette, zoom, cadrage) arrête de suivre ; la
  // vue de la personne suivie s'applique à la taille de mon écran, sans être enregistrée.
  const presence = props.presence;
  const stopFollowing = () => {
    if (presence?.following) presence.follow(null);
  };
  const followView = usePresence(presence?.store).view;
  useEffect(() => {
    const el = stage.current;
    if (!followView || !presence?.following || !el) return;
    const rect = el.getBoundingClientRect();
    setViewState({
      zoom: followView.zoom,
      x: rect.width / 2 - followView.cx * followView.zoom,
      y: rect.height / 2 - followView.cy * followView.zoom,
    });
  }, [followView, presence?.following]);
  // KAN-52 : arrivée par la cloche : la vue se centre sur l'épingle (ou l'élément, choisi),
  // sans être enregistrée. Un fil pas encore chargé : on réessaie à son arrivée.
  const focusOn = props.focusOn;
  const threads = props.comments?.threads;
  const focused = useRef(0);
  useEffect(() => {
    const el = stage.current;
    if (!focusOn || focused.current === focusOn.token || !el) return;
    let point: { x: number; y: number } | null = null;
    if (focusOn.threadId) {
      const thread = threads?.find((t) => t.id === focusOn.threadId);
      if (thread) point = pinPlace(thread.target, items);
    } else if (focusOn.itemId) {
      const item = items.find((i) => i.id === focusOn.itemId);
      if (item) {
        point = { x: item.x + item.w / 2, y: item.y + item.h / 2 };
        setSelected(new Set([item.id]));
      }
    }
    if (!point) return;
    focused.current = focusOn.token;
    const rect = el.getBoundingClientRect();
    const { x, y } = point;
    setViewState((v) => ({
      zoom: v.zoom,
      x: rect.width / 2 - x * v.zoom,
      y: rect.height / 2 - y * v.zoom,
    }));
  }, [focusOn, threads, items]);
  // Ma vue, si quelqu'un me suit (centre et zoom, à la taille de l'autre écran chez lui).
  const followed = presence?.followed ?? 0;
  const sendView = presence?.view;
  useEffect(() => {
    const el = stage.current;
    if (!followed || !el || !sendView) return;
    const rect = el.getBoundingClientRect();
    sendView({
      cx: (rect.width / 2 - view.x) / view.zoom,
      cy: (rect.height / 2 - view.y) / view.zoom,
      zoom: view.zoom,
    });
  }, [view, followed, sendView]);
  // Ma sélection, à chaque changement.
  const sendSelection = presence?.select;
  useEffect(() => {
    sendSelection?.([...selected]);
  }, [selected, sendSelection]);

  const applyView = useCallback(
    (next: BoardView) => {
      if (props.presence?.following) props.presence.follow(null);
      setViewState(next);
      props.onView(next);
    },
    [props],
  );

  /** Cadre la vue sur `subset` (sinon sur tout ce qui est posé). */
  const fit = (persist = true, subset?: BoardItem[]) => {
    const el = stage.current;
    const pool: { x: number; y: number; w: number; h: number }[] =
      subset && subset.length > 0 ? subset : items;
    if (!el || pool.length === 0) return;
    const minX = Math.min(...pool.map((i) => i.x));
    const minY = Math.min(...pool.map((i) => i.y));
    const maxX = Math.max(...pool.map((i) => i.x + i.w));
    const maxY = Math.max(...pool.map((i) => i.y + i.h));
    const rect = el.getBoundingClientRect();
    // T7b : le compositeur ouvert couvre le bas du tableau ; on cadre au-dessus de lui. KAN-98 :
    // la barre d'ajout aussi (aucun node dessous à l'ouverture).
    const composer = el.querySelector(".board-composer");
    const addbar = el.querySelector(".board-addbar")?.getBoundingClientRect();
    const reserve = Math.max(
      composer ? composer.getBoundingClientRect().height + 16 : 0,
      addbar ? rect.bottom - addbar.top + 8 - FIT_MARGIN : 0,
    );
    const height = Math.max(200, rect.height - reserve);
    const zoom = Math.max(
      ZOOM_MIN,
      Math.min(
        1.5,
        (rect.width - 2 * FIT_MARGIN) / Math.max(1, maxX - minX),
        (height - 2 * FIT_MARGIN) / Math.max(1, maxY - minY),
      ),
    );
    const next = {
      zoom,
      x: (rect.width - (maxX - minX) * zoom) / 2 - minX * zoom,
      y: (height - (maxY - minY) * zoom) / 2 - minY * zoom,
    };
    if (persist) applyView(next);
    else setViewState(next);
  };

  const toWorld = (clientX: number, clientY: number) => {
    const rect = stage.current?.getBoundingClientRect();
    return {
      x: (clientX - (rect?.left ?? 0) - view.x) / view.zoom,
      y: (clientY - (rect?.top ?? 0) - view.y) / view.zoom,
    };
  };

  const zoomAt = (factor: number, clientX?: number, clientY?: number) => {
    const rect = stage.current?.getBoundingClientRect();
    const cx = clientX ?? (rect ? rect.left + rect.width / 2 : 0);
    const cy = clientY ?? (rect ? rect.top + rect.height / 2 : 0);
    const zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, view.zoom * factor));
    const px = cx - (rect?.left ?? 0);
    const py = cy - (rect?.top ?? 0);
    applyView({
      zoom,
      x: px - ((px - view.x) / view.zoom) * zoom,
      y: py - ((py - view.y) / view.zoom) * zoom,
    });
  };

  // ------------------------------------------------------------ gestes
  const commit = (next: BoardItem[]) => props.onCommit(next);
  const selection = items.filter((i) => selected.has(i.id));

  const remove = () => {
    const planned = selection.filter((i) => i.kind === "plan");
    if (planned.length)
      setMessage(
        "Le plan reste dans l'espace de travail : on peut le déplacer ou le réduire, pas le retirer.",
      );
    const gone = new Set(
      selection.filter((i) => i.kind !== "plan" && !i.locked).map((i) => i.id),
    );
    if (!gone.size) return;
    commit(
      items
        .filter((i) => !gone.has(i.id))
        .map((i) => (i.frame && gone.has(i.frame) ? { ...i, frame: null } : i)),
    );
    setSelected(new Set());
  };

  const duplicateItems = (list: BoardItem[]) => {
    const copies = list
      .filter((i) => i.kind !== "plan")
      .map((i) => ({
        ...i,
        id: uid(),
        x: i.x + 24,
        y: i.y + 24,
        role: null,
        locked: false,
        z: i.z + 1,
      }));
    if (!copies.length) return;
    commit([...items, ...copies]);
    setSelected(new Set(copies.map((c) => c.id)));
  };
  const duplicate = () => duplicateItems(selection);

  /** A3 : sur une carte du moodboard, « À conserver » et le commentaire attaché. */
  const patchItem = (id: string, patch: Partial<BoardItem>) =>
    commit(items.map((i) => (i.id === id ? { ...i, ...patch } : i)));

  const toggleLock = () => {
    const lock = selection.some((i) => !i.locked);
    commit(items.map((i) => (selected.has(i.id) ? { ...i, locked: lock } : i)));
  };

  /** A2 : une image du tableau (photo, rendu, variante, image jointe) peut servir de
   *  référence d'allure aux rendus contextualisés. */
  const canReference = (item: BoardItem): boolean =>
    !!item.ref &&
    (item.kind === "photo" ||
      item.kind === "render" ||
      item.kind === "contextual" ||
      (item.kind === "file" &&
        media.find((m) => m.id === item.ref)?.kind === "image"));
  const referenceable = selection.filter(canReference);
  /** KAN-91 : « Référence » ne se coche plus, il suit l'appartenance (C1). « Dans
   *  Inspirations » fait entrer les images choisies dans la carte Inspirations, comme si on
   *  les y glissait : leur allure guide alors les rendus reliés. */
  // La carte Inspirations de départ (sans `ref`), sinon la première posée à la main.
  const inspirationsCard =
    items.find((i) => cardRole(i) === "inspirations" && !i.ref) ??
    items.find((i) => cardRole(i) === "inspirations");
  const outsideInspirations = referenceable.filter(
    (i) => !inspirationsCard || i.frame !== inspirationsCard.id,
  );
  const toInspirations = () => {
    const card = inspirationsCard;
    if (!card || !outsideInspirations.length) return;
    let target = card;
    let inside = items.filter((i) => i.frame === card.id).length;
    const moved = new Map<string, BoardItem>();
    for (const i of outsideInspirations) {
      const placed = placeInFrame(target, inside, [i.w, i.h]);
      moved.set(i.id, {
        ...i,
        x: placed.x,
        y: placed.y,
        frame: card.id,
        reference: true,
      });
      target = placed.frame;
      inside += 1;
    }
    const before = new Map(items.map((i) => [i.id, i]));
    commit(
      items.map((i) => moved.get(i.id) ?? (i.id === card.id ? target : i)),
    );
    afterMembership(before, [...moved.values()]);
  };

  /** KAN-91 (C3) : ce qu'un geste a changé dans l'appartenance des images. Le bandeau dit
   *  l'effet, avec « Annuler » (le geste entier) ; une photo entrée dans la carte Photos d'un
   *  plan, ou sortie, recalcule ce plan après le délai d'« Annuler ». */
  const afterMembership = (
    before: Map<string, BoardItem>,
    after: BoardItem[],
  ) => {
    const byId = new Map(items.map((i) => [i.id, i]));
    let text: string | null = null;
    const plans = new Map<string, { added: string[]; removed: string[] }>();
    for (const item of after) {
      const old = before.get(item.id);
      if (!old || old.frame === item.frame || item.kind === "frame") continue;
      const from = old.frame ? (byId.get(old.frame) ?? null) : null;
      const to = item.frame ? (byId.get(item.frame) ?? null) : null;
      const effect = dropEffect(board, item, from, to);
      if (effect?.ok)
        text ??= effect.text
          .replace(/^Déposer : /, "Déposée : ")
          .replace(/^Déplacer : /, "Déplacée : ")
          .replace(/^Relâcher hors node : /, "Hors node : ");
      if (item.kind !== "photo" || !item.ref) continue;
      const left = plansFedBy(board, from?.id ?? null);
      const entered = plansFedBy(board, to?.id ?? null);
      for (const lid of left.filter((l) => !entered.includes(l))) {
        const p = plans.get(lid) ?? { added: [], removed: [] };
        p.removed.push(item.ref);
        plans.set(lid, p);
      }
      for (const lid of entered.filter((l) => !left.includes(l))) {
        const p = plans.get(lid) ?? { added: [], removed: [] };
        p.added.push(item.ref);
        plans.set(lid, p);
      }
    }
    if (!text && !plans.size) return;
    if (recompute.current !== null) window.clearTimeout(recompute.current);
    recompute.current = null;
    if (plans.size && props.onPlanMembers && !readOnly) {
      const go = props.onPlanMembers;
      recompute.current = window.setTimeout(() => {
        recompute.current = null;
        for (const [lid, p] of plans) go(lid, p.added, p.removed);
      }, TOAST_MS);
    }
    // Le plan ne ment pas (décision du PO) : une photo sortie retire sa caméra, pas les murs.
    const left = [...plans.values()].some((p) => p.removed.length > 0);
    setToast({
      text: `${text ?? "Cartes modifiées"}${plans.size ? " · le plan se recalculera" : ""}${left ? `. ${WALLS_STAY}` : ""}`,
      undo: () => {
        if (recompute.current !== null) window.clearTimeout(recompute.current);
        recompute.current = null;
        props.onUndo();
        setToast(null);
      },
    });
  };

  /** KAN-98 : le compteur d'un node, à côté de son titre ; rien quand il est vide. */
  const frameCount = (frame: BoardItem): string | null => {
    const n = items.filter((i) => i.frame === frame.id).length;
    if (n === 0) return null;
    const nouns: Record<string, string> = {
      photos: "photo",
      inspirations: "image",
      renders: "rendu",
    };
    const noun = nouns[frame.role ?? ""];
    return noun ? `${n} ${noun}${n > 1 ? "s" : ""}` : null;
  };
  /** KAN-98 : le centre de la vue, en coordonnées du tableau (là où la barre d'ajout pose). */
  const viewCentre = () => {
    const rect = stage.current?.getBoundingClientRect();
    return {
      x: ((rect?.width ?? 0) / 2 - view.x) / view.zoom,
      y: ((rect?.height ?? 0) / 2 - view.y) / view.zoom,
    };
  };
  /** KAN-91, KAN-98 : une carte vide posée au centre de la vue. Photos ou Moodboard : sans lien
   *  (`ref` = son propre id : elle n'est la carte d'aucun plan, ni le Moodboard de départ, tant
   *  qu'on ne la relie pas) ; Rendus : celle d'un plan qui n'en a plus (`ref` = son niveau). */
  const addCard = (
    role: "photos" | "inspirations" | "renders",
    text: string,
    hint: string,
    ref?: string | null,
    at?: { x: number; y: number },
  ) => {
    const id = uid();
    const c = viewCentre();
    const card: BoardItem = {
      id,
      kind: "frame",
      x: Math.round(at?.x ?? c.x - NEW_CARD.w / 2),
      y: Math.round(at?.y ?? c.y - NEW_CARD.h / 2),
      w: NEW_CARD.w,
      h: NEW_CARD.h,
      z: Math.min(0, ...items.map((i) => i.z)) - 1,
      locked: false,
      frame: null,
      ref: ref === undefined ? id : ref,
      text,
      role,
    };
    commit([...items, card]);
    setSelected(new Set([id]));
    setToast({ text: hint });
  };
  const addPhotosCard = () =>
    addCard(
      "photos",
      PHOTOS_TITLE,
      "Nouveau node Photos de la pièce, sans lien : tirez sa poignée « + » vers un plan pour que ses photos le construisent.",
    );
  // KAN-98 : un nouveau Moodboard se pose au-dessus du node Rendus (celui du premier plan),
  // dans la même colonne ; sans node Rendus, au centre de la vue.
  const addInspirationsCard = () => {
    const renders = items.filter(
      (i) => i.kind === "frame" && i.role === "renders",
    );
    const under =
      renders.find((r) => frameLevel(r) === firstLevelId) ?? renders[0];
    addCard(
      "inspirations",
      MOODBOARD_TITLE,
      "Nouveau Moodboard, sans lien : tirez sa poignée « + » vers un ou plusieurs nodes Rendus pour que ses images guident leur allure.",
      undefined,
      under
        ? { x: under.x, y: under.y - NEW_CARD.h - NEW_CARD_GAP }
        : undefined,
    );
  };
  // KAN-98 : un plan dont la carte Rendus a été retirée en retrouve une (une par niveau).
  const rendered = new Set(
    items
      .filter((i) => i.kind === "frame" && i.role === "renders")
      .map((f) => frameLevel(f)),
  );
  const unrendered = plans
    .map((p) => p.ref)
    .find((level): level is string => !!level && !rendered.has(level));
  const addRendersCard = () => {
    if (!unrendered) return;
    const plan = plans.find((p) => p.ref === unrendered);
    addCard(
      "renders",
      plan?.text ? `Rendus · ${plan.text}` : "Rendus",
      "Nouveau node Rendus, relié à son plan.",
      unrendered === firstLevelId ? null : unrendered,
    );
  };

  /** KAN-91 : la carte (plan, cadre Photos, Inspirations ou Rendus) sous un point. */
  const cardAt = (at: { x: number; y: number }, except: string) =>
    items
      .filter(
        (i) =>
          i.id !== except &&
          cardRole(i) !== null &&
          at.x >= i.x &&
          at.x <= i.x + i.w &&
          at.y >= i.y &&
          at.y <= i.y + i.h,
      )
      .sort((a, b) => a.w * a.h - b.w * b.h)[0] ?? null;

  const onWireHandleDown = (e: ReactPointerEvent, source: BoardItem) => {
    e.stopPropagation();
    e.preventDefault();
    if (readOnly) return;
    stage.current?.setPointerCapture(e.pointerId);
    setWireSelected(null);
    setWireDrag({
      source,
      to: toWorld(e.clientX, e.clientY),
      target: null,
      refused: null,
    });
  };

  const finishWire = (current: WireDrag) => {
    setWireDrag(null);
    const { source, target, refused } = current;
    if (!target) return;
    if (refused) {
      setMessage(refused);
      return;
    }
    const link = newLink(source, target);
    if (!link) return;
    void props.onWriteLinks([link], []).then((ok) => {
      if (ok)
        setToast({
          text: `Lien posé : ${cardTitle(source)} → ${cardTitle(target)}`,
          undo: () => {
            void props.onWriteLinks([], [link.id]);
            setToast(null);
          },
        });
    });
  };

  const cutText = (link: BoardLink): string => {
    const s = cardTitle(items.find((i) => i.id === link.source));
    const t = cardTitle(items.find((i) => i.id === link.target));
    if (link.kind === "guides_style")
      return `Lien coupé : ${s} ne guide plus ${t}. Les rendus déjà faits restent.`;
    if (link.kind === "feeds_plan")
      return `Lien coupé : les photos de ${s} ne construisent plus ${t}.`;
    return `Lien coupé : ${t} n'a plus de plan relié (« ${NO_PLAN} »). Les rendus déjà faits restent.`;
  };

  const cutLink = (link: BoardLink) => {
    setWireSelected(null);
    setGhosts((g) => [...g, link]);
    window.setTimeout(
      () => setGhosts((g) => g.filter((x) => x.id !== link.id)),
      TOAST_MS,
    );
    void props.onWriteLinks([], [link.id]).then((ok) => {
      if (!ok) return;
      setToast({
        text: cutText(link),
        undo: () => {
          setGhosts((g) => g.filter((x) => x.id !== link.id));
          void props.onWriteLinks([{ ...link, id: uid(), auto: false }], []);
          setToast(null);
        },
      });
    });
  };

  const order = (front: boolean) => {
    const zs = items.map((i) => i.z);
    const edge = front ? Math.max(...zs) + 1 : Math.min(...zs) - 1;
    commit(items.map((i) => (selected.has(i.id) ? { ...i, z: edge } : i)));
  };

  /** KAN-65 : un post-it carré, centré sur (cx, cy), de la dernière couleur utilisée. */
  const addNote = (cx: number, cy: number, text = "") => {
    const note: BoardItem = {
      id: uid(),
      kind: "note",
      x: cx - POSTIT_SIDE / 2,
      y: cy - POSTIT_SIDE / 2,
      w: POSTIT_SIDE,
      h: POSTIT_SIDE,
      z: Math.max(0, ...items.map((i) => i.z)) + 1,
      locked: false,
      frame: null,
      ref: null,
      text,
      role: null,
      color: lastColor(),
    };
    commit([...items, note]);
    setSelected(new Set([note.id]));
    if (!text) setEditing(note.id);
  };

  /** Un lien (C1) : une carte avec le domaine et le titre ; la page n'est jamais récupérée. */
  const addLink = (
    url: string,
    title: string,
    at: { x: number; y: number },
    into?: BoardItem,
  ) => {
    const [w, h] = MEDIA_SIZE.link;
    // KAN-77 : depuis un cadre, le lien se range dans sa grille (le cadre grandit s'il le faut).
    const placed = into
      ? placeInFrame(into, items.filter((i) => i.frame === into.id).length, [
          w,
          h,
        ])
      : null;
    const link: BoardItem = {
      id: uid(),
      kind: "link",
      x: placed ? placed.x : at.x - w / 2,
      y: placed ? placed.y : at.y - h / 2,
      w,
      h,
      z: Math.max(0, ...items.map((i) => i.z)) + 1,
      locked: false,
      frame: placed ? placed.frame.id : null,
      ref: null,
      text: (title.trim() || hostOf(url)).slice(0, 200),
      role: null,
      url,
    };
    const grown = placed?.frame;
    commit([
      ...items.map((i) =>
        grown && i.id === grown.id && i !== grown ? grown : i,
      ),
      link,
    ]);
    setSelected(new Set([link.id]));
  };

  const centre = () => {
    const rect = stage.current?.getBoundingClientRect();
    return toWorld(
      (rect?.left ?? 0) + (rect?.width ?? 0) / 2,
      (rect?.top ?? 0) + (rect?.height ?? 0) / 2,
    );
  };

  const submitLink = () => {
    if (!linkForm) return;
    const url = linkFrom(linkForm.url);
    if (!url) {
      setMessage(
        "Adresse de lien : http:// ou https:// seulement, sans espace.",
      );
      return;
    }
    const into = linkForm.frame
      ? items.find((i) => i.id === linkForm.frame)
      : undefined;
    addLink(url, linkForm.title, centre(), into);
    setLinkForm(null);
  };

  /** Le cadre sous un point du tableau (le plus petit s'ils s'emboîtent). */
  const frameAt = (at: { x: number; y: number }): BoardItem | null =>
    items
      .filter(
        (i) =>
          i.kind === "frame" &&
          at.x >= i.x &&
          at.x <= i.x + i.w &&
          at.y >= i.y &&
          at.y <= i.y + i.h,
      )
      .sort((a, b) => a.w * a.h - b.w * b.h)[0] ?? null;

  /** Place un élément dans la grille d'un cadre (comme le serveur) ; agrandit le cadre d'une
   *  rangée s'il est plein. Renvoie la position et le cadre, peut-être grandi. */
  const placeInFrame = (
    frame: BoardItem,
    inside: number,
    size: [number, number],
  ): { x: number; y: number; frame: BoardItem } => {
    const pad = 24;
    // T7a : sous l'en-tête des cadres posés par le tableau (160 px depuis KAN-77, comme
    // `PAD_TOP` côté serveur) ; sinon le contenu couvrait le titre.
    const top = frame.role ? 160 : pad;
    const cols = Math.max(
      1,
      Math.floor((frame.w - 2 * pad + 16) / (size[0] + 16)),
    );
    const row = Math.floor(inside / cols);
    const col = inside % cols;
    const need = top + pad + (row + 1) * size[1] + row * 16;
    const grown = frame.h < need ? { ...frame, h: need } : frame;
    return {
      x: frame.x + pad + col * (size[0] + 16),
      y: frame.y + top + row * (size[1] + 16),
      frame: grown,
    };
  };

  /** Dépôt de fichiers (T2) : dans le cadre « Inspirations », une image devient une image
   *  jointe cochée Référence (son allure guidera les rendus) ; ailleurs, une photo entre dans
   *  le projet (elle fait le plan) et un autre fichier est joint. Déposé dans un cadre,
   *  l'élément y prend sa place. */
  const importFiles = async (
    files: File[],
    at: { x: number; y: number },
    into?: BoardItem,
  ) => {
    // KAN-50 : passage commun du dépôt, du collage et des sélecteurs de fichiers ; un lecteur
    // n'envoie rien (le serveur refuserait de toute façon).
    if (readOnly) {
      setMessage(READ_ONLY_NOTICE);
      return;
    }
    const added: BoardItem[] = [];
    let changed: Project | null = null;
    let firstPhoto: string | null = null;
    // Connexion lente (25 oct.) : le fichier déposé sert à l'analyse, sans le retélécharger.
    let firstFile: File | undefined;
    let offset = 0;
    let target = into ?? frameAt(at);
    const dropped = target;
    let inside = target
      ? items.filter((i) => i.frame === target?.id).length
      : 0;
    const inspirations = target?.role === "inspirations";
    for (const file of files) {
      // T7a : un plan (DXF, PDF, SVG) mis dans le cadre Photos se lit comme un plan, comme
      // « Importer › Photo ou plan de la pièce ».
      if (target?.role === "photos" && isPlanFile(file) && props.onImportPlan) {
        props.onImportPlan(file);
        continue;
      }
      try {
        const isImage = PHOTO_TYPES.includes(file.type);
        let item: BoardItem;
        if (isImage && !inspirations) {
          const result = await api.uploadImage(project.id, file);
          changed = result.project;
          if (firstPhoto === null) {
            firstPhoto = result.image.id;
            firstFile = file;
          }
          item = {
            id: uid(),
            kind: "photo",
            x: at.x + offset,
            y: at.y + offset,
            w: 280,
            h: 190,
            z: 1,
            locked: false,
            frame: null,
            ref: result.image.id,
            text: file.name,
            role: null,
          };
        } else {
          const ref = await boardApi.upload(project.id, file);
          const kind = mediaItemKind(ref);
          setMedia((m) => [...m, ref]);
          const size: [number, number] =
            ref.kind === "image" ? [280, 190] : MEDIA_SIZE[kind];
          item = {
            id: uid(),
            kind,
            x: at.x + offset,
            y: at.y + offset,
            w: size[0],
            h: size[1],
            z: 1,
            locked: false,
            frame: null,
            ref: ref.id,
            text: ref.name,
            role: null,
            reference: inspirations && ref.kind === "image",
          };
        }
        if (target) {
          const placed = placeInFrame(target, inside, [item.w, item.h]);
          item = { ...item, x: placed.x, y: placed.y, frame: target.id };
          target = placed.frame;
          inside += 1;
        }
        added.push(item);
        offset += 24;
      } catch (e) {
        setMessage(`« ${file.name} » non importé : ${String(e)}`);
      }
    }
    if (added.length) {
      const grown = target;
      // B2c : le tableau d'à présent (l'envoi des fichiers a pris du temps).
      commit([
        ...itemsNow.current.map((i) =>
          grown && i.id === grown.id ? grown : i,
        ),
        ...added,
      ]);
      setSelected(new Set(added.map((a) => a.id)));
      if (changed) props.onProjectChanged(changed);
      // KAN-91 (C1) : une photo ne construit le plan que déposée dans la carte Photos reliée
      // à ce plan ; posée librement, elle attend qu'on l'y glisse (et le dit).
      const level = props.activeLevelId ?? firstLevelId;
      const fed =
        dropped && cardRole(dropped) === "photos"
          ? plansFedBy(board, dropped.id)
          : [];
      if (changed && firstPhoto && fed.includes(level))
        props.onPhotoDropped(changed, firstPhoto, firstFile);
      else if (firstPhoto && fed.length && props.onPlanMembers)
        // La carte d'un autre plan (l'étage) : c'est ce plan-là qui la lit.
        for (const lid of fed) props.onPlanMembers(lid, [firstPhoto], []);
      else if (firstPhoto && cardRole(dropped) !== "photos")
        setToast({
          text: "Photo posée hors node : glissez-la dans « Photos de la pièce » pour qu'elle serve au plan.",
        });
    }
  };

  /** T7a : « Importer » (cadre vide) ou « + » (cadre rempli) : le sélecteur de fichiers du
   *  cadre ; les fichiers choisis y entrent comme s'ils y avaient été déposés. */
  const frameFileRef = useRef<HTMLInputElement>(null);
  const frameTarget = useRef<string | null>(null);
  const pickInto = (frame: BoardItem) => {
    const input = frameFileRef.current;
    if (!input) return;
    frameTarget.current = frame.id;
    input.accept = FRAME_IMPORT[frame.role ?? ""]?.accept ?? "";
    input.click();
  };
  const onFramePicked = (files: File[]) => {
    const frame = items.find((i) => i.id === frameTarget.current);
    frameTarget.current = null;
    if (!frame || !files.length) return;
    void importFiles(
      files,
      { x: frame.x + frame.w / 2, y: frame.y + frame.h / 2 },
      frame,
    );
  };

  // ------------------------------------------------------------ pointeur
  /** Un clic ailleurs termine d'abord l'écriture en cours (note, nom de cadre) : le texte
   *  est enregistré avant que le champ ne disparaisse ; ce clic ne fait rien d'autre. */
  const finishEditing = (): boolean => {
    if (!editing) return false;
    const field = document.activeElement;
    if (field instanceof HTMLElement && stage.current?.contains(field))
      field.blur();
    else setEditing(null);
    return true;
  };

  const onStageDown = (e: ReactPointerEvent) => {
    if (
      e.target !== e.currentTarget &&
      !(e.target as HTMLElement).dataset.world
    )
      return;
    if (finishEditing()) {
      e.preventDefault();
      return;
    }
    stage.current?.setPointerCapture(e.pointerId);
    if (tool === "hand" || space.current || e.button === 1) {
      setDrag({ kind: "pan", x: e.clientX, y: e.clientY, view });
      return;
    }
    const p = toWorld(e.clientX, e.clientY);
    // KAN-51 : une épingle de commentaire, à ce point de l'espace de travail.
    if (tool === "comment") {
      e.preventDefault();
      props.comments?.startDraft({ at: { x: p.x, y: p.y } });
      setTool("select");
      return;
    }
    if (tool === "note") {
      e.preventDefault();
      addNote(p.x, p.y);
      setTool("select");
      return;
    }
    if (tool === "frame") {
      setDrag({ kind: "frame", x: p.x, y: p.y, to: p });
      return;
    }
    if (!e.shiftKey) setSelected(new Set());
    setEditing(null);
    setWireSelected(null);
    setDrag({ kind: "marquee", x: p.x, y: p.y, to: p });
  };

  const onItemDown = (e: ReactPointerEvent, item: BoardItem) => {
    if (editing === item.id) return;
    e.stopPropagation();
    if (finishEditing()) {
      e.preventDefault();
      return;
    }
    // KAN-51 : une épingle sur un élément le suit (position relative à son coin).
    if (tool === "comment") {
      e.preventDefault();
      const p = toWorld(e.clientX, e.clientY);
      props.comments?.startDraft({
        at: { x: p.x - item.x, y: p.y - item.y },
        item_id: item.id,
        last: { x: item.x, y: item.y },
      });
      setTool("select");
      return;
    }
    // Note et Cadre se posent aussi par-dessus un élément (une note sur le plan).
    if (tool === "note" || tool === "frame") {
      const p = toWorld(e.clientX, e.clientY);
      stage.current?.setPointerCapture(e.pointerId);
      if (tool === "note") {
        // Sans cela, le clic qui suit retire le focus du texte de la note tout juste posée.
        e.preventDefault();
        addNote(p.x, p.y);
        setTool("select");
      } else setDrag({ kind: "frame", x: p.x, y: p.y, to: p });
      return;
    }
    if (tool === "hand" || space.current || e.button === 1) {
      stage.current?.setPointerCapture(e.pointerId);
      setDrag({ kind: "pan", x: e.clientX, y: e.clientY, view });
      return;
    }
    // T7d2 : double-clic reconnu ici. La capture du pointeur (déplacement) envoie le clic au
    // canevas : l'événement dblclick n'arrivait jamais à un élément déplaçable.
    if (e.button === 0 && !e.shiftKey) {
      const last = lastDown.current;
      lastDown.current = { id: item.id, t: e.timeStamp };
      if (last && last.id === item.id && e.timeStamp - last.t < DOUBLE_MS) {
        lastDown.current = null;
        // KAN-65 : le champ qui s'ouvre (post-it, nom de cadre) garde le focus ; sans cela,
        // la fin du clic le lui retire aussitôt.
        if (item.kind === "note" || item.kind === "frame") e.preventDefault();
        onDouble(item);
        return;
      }
    }
    let next = selected;
    if (e.shiftKey) {
      next = new Set(selected);
      if (next.has(item.id)) next.delete(item.id);
      else next.add(item.id);
    } else if (!selected.has(item.id)) next = new Set([item.id]);
    setSelected(next);
    const movable = items.filter((i) => next.has(i.id) && !i.locked);
    if (!movable.length) return;
    stage.current?.setPointerCapture(e.pointerId);
    setDrag({ kind: "move", x: e.clientX, y: e.clientY, start: items });
  };

  const onResizeDown = (e: ReactPointerEvent, item: BoardItem) => {
    e.stopPropagation();
    stage.current?.setPointerCapture(e.pointerId);
    setDrag({ kind: "resize", x: e.clientX, y: e.clientY, item });
  };

  const onMove = (e: ReactPointerEvent) => {
    if (wireDrag) {
      // KAN-91 : un lien tiré : la cible sous le pointeur, valide ou refusée (raison dite).
      const to = toWorld(e.clientX, e.clientY);
      const target = cardAt(to, wireDrag.source.id);
      setWireDrag({
        ...wireDrag,
        to,
        target,
        refused: target ? linkRefusal(board, wireDrag.source, target) : null,
      });
      return;
    }
    if (!drag) return;
    if (drag.kind === "pan") {
      setViewState({
        ...drag.view,
        x: drag.view.x + e.clientX - drag.x,
        y: drag.view.y + e.clientY - drag.y,
      });
      return;
    }
    const dx = (e.clientX - drag.x) / view.zoom;
    const dy = (e.clientY - drag.y) / view.zoom;
    if (drag.kind === "move") {
      const moving = new Set(
        drag.start
          .filter((i) => selected.has(i.id) && !i.locked)
          .map((i) => i.id),
      );
      // Un cadre emmène ce qu'il contient.
      for (const i of drag.start)
        if (i.frame && moving.has(i.frame) && !i.locked) moving.add(i.id);
      setLive(
        drag.start.map((i) =>
          moving.has(i.id) ? { ...i, x: i.x + dx, y: i.y + dy } : i,
        ),
      );
      // KAN-91 (C3) : une image glissée vers une carte (ou hors d'elle) : la carte visée
      // s'entoure, une phrase dit ce qui arrivera (au-delà de 5 px : le clic reste un clic).
      const primary = drag.start.find(
        (i) => selected.has(i.id) && i.kind !== "frame" && !i.locked,
      );
      if (
        primary &&
        Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > DRAG_THRESHOLD_PX
      ) {
        const moved = { ...primary, x: primary.x + dx, y: primary.y + dy };
        const host =
          drag.start.find(
            (f) =>
              f.kind === "frame" &&
              f.id !== primary.id &&
              !moving.has(f.id) &&
              inside(moved, f),
          ) ?? null;
        const from = primary.frame
          ? (drag.start.find((f) => f.id === primary.frame) ?? null)
          : null;
        const effect = dropEffect(board, moved, from, host);
        setDropHint(
          effect
            ? {
                frame: host?.id ?? null,
                ...effect,
                x: moved.x + moved.w / 2,
                y: moved.y + moved.h,
              }
            : null,
        );
      }
    } else if (drag.kind === "resize") {
      // KAN-65 : un post-it reste carré, sauf avec Maj.
      const size = {
        w: Math.max(MIN_SIDE, drag.item.w + dx),
        h: Math.max(MIN_SIDE, drag.item.h + dy),
      };
      const { w, h } =
        drag.item.kind === "note"
          ? resizedPostit(size.w, size.h, e.shiftKey)
          : size;
      setLive(
        board.items.map((i) => (i.id === drag.item.id ? { ...i, w, h } : i)),
      );
    } else {
      setDrag({ ...drag, to: toWorld(e.clientX, e.clientY) });
    }
  };

  const onUp = () => {
    if (wireDrag) {
      finishWire(wireDrag);
      return;
    }
    if (!drag) return;
    setDropHint(null);
    if (drag.kind === "pan") applyView(view);
    else if ((drag.kind === "move" || drag.kind === "resize") && live) {
      // Rangement : un élément lâché dans un cadre lui appartient, hors de tout cadre il
      // n'appartient à aucun.
      const frames = live.filter((i) => i.kind === "frame");
      const next = live.map((i) => {
        if (i.kind === "frame" || !selected.has(i.id)) return i;
        const host = frames.find((f) => f.id !== i.id && inside(i, f));
        return { ...i, frame: host ? host.id : null };
      });
      // KAN-91 (C3) : une carte qui n'accepte pas cet élément (une inspiration dans la carte
      // Photos) : le geste est refusé, l'élément revient, la raison est dite.
      const before = new Map(
        (drag.kind === "move" ? drag.start : board.items).map((i) => [i.id, i]),
      );
      const refused = next
        .filter((i) => i.frame && before.get(i.id)?.frame !== i.frame)
        .map((i) => accepts(frames.find((f) => f.id === i.frame) ?? null, i))
        .find((reason) => reason !== null);
      if (refused) {
        setMessage(refused);
        setDrag(null);
        setLive(null);
        return;
      }
      // B2c : seuls les éléments de ce geste partent, posés sur le tableau d'à présent (un
      // autre membre a pu en changer d'autres pendant le geste).
      const base = new Set(drag.kind === "move" ? drag.start : board.items);
      const touched = new Map(
        next.filter((i) => !base.has(i)).map((i) => [i.id, i]),
      );
      commit(board.items.map((i) => touched.get(i.id) ?? i));
      afterMembership(before, [...touched.values()]);
    } else if (drag.kind === "marquee") {
      const x0 = Math.min(drag.x, drag.to.x);
      const y0 = Math.min(drag.y, drag.to.y);
      const x1 = Math.max(drag.x, drag.to.x);
      const y1 = Math.max(drag.y, drag.to.y);
      if (x1 - x0 > 4 || y1 - y0 > 4)
        setSelected(
          new Set(
            items
              .filter(
                (i) =>
                  i.x >= x0 && i.y >= y0 && i.x + i.w <= x1 && i.y + i.h <= y1,
              )
              .map((i) => i.id),
          ),
        );
    } else if (drag.kind === "frame") {
      const x = Math.min(drag.x, drag.to.x);
      const y = Math.min(drag.y, drag.to.y);
      const w = Math.max(160, Math.abs(drag.to.x - drag.x));
      const h = Math.max(120, Math.abs(drag.to.y - drag.y));
      const frame: BoardItem = {
        id: uid(),
        kind: "frame",
        x,
        y,
        w,
        h,
        z: Math.min(0, ...items.map((i) => i.z)) - 1,
        locked: false,
        frame: null,
        ref: null,
        text: "Cadre",
        role: null,
      };
      commit([
        ...items.map((i) =>
          i.kind !== "frame" && !i.frame && inside(i, frame)
            ? { ...i, frame: frame.id }
            : i,
        ),
        frame,
      ]);
      setSelected(new Set([frame.id]));
      setEditing(frame.id);
      setTool("select");
    }
    setDrag(null);
    setLive(null);
  };

  const onWheel = (e: React.WheelEvent) => {
    if (e.ctrlKey || e.metaKey) {
      zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX, e.clientY);
    } else {
      applyView({ ...view, x: view.x - e.deltaX, y: view.y - e.deltaY });
    }
  };

  // ------------------------------------------------------------ clavier, collage
  useEffect(() => {
    if (!active) return;
    const typing = (t: EventTarget | null) =>
      t instanceof HTMLElement &&
      (t.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
    const down = (e: KeyboardEvent) => {
      if (e.key === " " && !typing(e.target)) space.current = true;
      if (typing(e.target)) return;
      const mod = e.ctrlKey || e.metaKey;
      // KAN-50 : en lecture seule, aucun raccourci ne crée, ne duplique, ne supprime, ne
      // verrouille ni ne réordonne d'élément ; regarder (sélection, main, zoom) reste permis.
      const key = e.key.toLowerCase();
      const edits =
        (mod && (key === "d" || key === "l")) ||
        (!mod &&
          ["delete", "backspace", "[", "]", "n", "f", "l"].includes(key));
      if (readOnly && edits) {
        e.preventDefault();
        setMessage(READ_ONLY_NOTICE);
        return;
      }
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) props.onRedo();
        else props.onUndo();
      } else if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        props.onRedo();
      } else if (mod && e.key.toLowerCase() === "d") {
        e.preventDefault();
        duplicate();
      } else if (mod && e.key.toLowerCase() === "l") {
        e.preventDefault();
        toggleLock();
      } else if (mod && e.key === "0") {
        e.preventDefault();
        fit();
      } else if (e.key === "Delete" || e.key === "Backspace") {
        // KAN-91 : un lien choisi se coupe avec Suppr (avant les éléments choisis).
        const link = (board.links ?? []).find((l) => l.id === wireSelected);
        if (link && !readOnly) cutLink(link);
        else remove();
      } else if (e.key === "Escape") {
        // KAN-51 : Échap quitte l'outil Commentaire, les boutons des cadres reviennent.
        setTool((t) => (t === "comment" ? "select" : t));
        setSelected(new Set());
        setEditing(null);
        setWireSelected(null);
      } else if (e.key === "]") order(true);
      else if (e.key === "[") order(false);
      else if (e.key === "v") setTool("select");
      else if (e.key === "h") setTool("hand");
      else if (e.key === "n") setTool("note");
      else if (e.key === "f") setTool("frame");
      else if (e.key === "c" && props.comments?.canComment) setTool("comment");
      else if (e.key === "l") setLinkForm({ url: "", title: "" });
      else if (e.key === "+" || e.key === "=") zoomAt(1.2);
      else if (e.key === "-") zoomAt(1 / 1.2);
    };
    const up = (e: KeyboardEvent) => {
      if (e.key === " ") space.current = false;
    };
    const paste = (e: ClipboardEvent) => {
      if (typing(e.target)) return;
      const files = Array.from(e.clipboardData?.files ?? []);
      const rect = stage.current?.getBoundingClientRect();
      const at = toWorld(
        (rect?.left ?? 0) + (rect?.width ?? 0) / 2,
        (rect?.top ?? 0) + (rect?.height ?? 0) / 2,
      );
      if (files.length) {
        e.preventDefault();
        void importFiles(files, at);
        return;
      }
      const text = e.clipboardData?.getData("text/plain")?.trim();
      if (text) {
        e.preventDefault();
        // KAN-50 : ni note ni lien collés dans l'espace d'un lecteur.
        if (readOnly) {
          setMessage(READ_ONLY_NOTICE);
          return;
        }
        const url = linkFrom(text);
        if (url) addLink(url, "", at);
        else addNote(at.x, at.y, text.slice(0, 4000));
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("paste", paste);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("paste", paste);
    };
  });

  // ------------------------------------------------------------ contenu
  const renderUrl = (item: BoardItem): string | null => {
    if (!item.ref) return null;
    if (item.role === "inspiration")
      return inspirationApi.artifactUrl(
        project.id,
        item.ref,
        "inspiration.png",
      );
    const entry = byRun.get(item.ref);
    if (!entry) return null;
    return renderApi.variantUrl(
      project.id,
      entry,
      entry.finished ? "finished" : "blender",
    );
  };

  const body = (item: BoardItem) => {
    switch (item.kind) {
      case "photo":
        return item.ref && project.images[item.ref] ? (
          <AuthedImg
            src={api.imageFileUrl(project.id, item.ref)}
            alt={item.text || "Photo"}
            missing="photo indisponible"
          />
        ) : (
          <span className="board-missing">photo introuvable</span>
        );
      case "render": {
        const url = renderUrl(item);
        const entry = item.ref ? byRun.get(item.ref) : undefined;
        const stale = entry?.camera_id
          ? props.stale.has(entry.camera_id)
          : false;
        return (
          <>
            {url ? (
              <AuthedImg
                src={url}
                alt={item.text || "Rendu"}
                missing="rendu indisponible"
              />
            ) : (
              <span className="board-missing">rendu introuvable</span>
            )}
            <span className="board-caption">
              {item.role === "inspiration" ? (
                <span className="badge generated">inspiration, sans plan</span>
              ) : (
                item.text
              )}
              {stale && <span className="badge stale">périmée</span>}
            </span>
          </>
        );
      }
      case "plan": {
        const level = item.ref ? project.levels[item.ref] : undefined;
        const emptyPlan = !!level && walls(level).length === 0;
        return (
          <>
            <span className="board-plan-head">
              Plan · {level?.name ?? item.text}
              {emptyPlan && <span className="chip muted">vide</span>}
              {!emptyPlan && !readOnly && (
                <button
                  type="button"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={() => enterPlan(item)}
                >
                  Modifier le plan
                </button>
              )}
              {props.onDeletePlan &&
                item.ref &&
                Object.keys(project.levels).length > 1 && (
                  <DeletePlanButton
                    name={level?.name ?? item.text}
                    first={Object.keys(project.levels)[0] === item.ref}
                    onDelete={() => props.onDeletePlan?.(item.ref!)}
                  />
                )}
            </span>
            {emptyPlan ? (
              // T3 : le plan n'existe pas encore ; deux chemins, et la progression ici.
              <span
                className="board-plan-empty"
                onPointerDown={(e) => e.stopPropagation()}
              >
                {props.planProgress ? (
                  <span className="board-plan-progress" role="status">
                    <span className="spinner" aria-hidden="true" />
                    <span>{props.planProgress}</span>
                  </span>
                ) : (
                  !readOnly && (
                    <>
                      {/* T7d1 : des boutons, sans paragraphe (retour d'augus). KAN-77 :
                          « Importer un plan » quitte la barre de gauche pour ce cadre. */}
                      <span className="board-plan-actions">
                        <button
                          type="button"
                          onClick={() => enterPlan(item)}
                          title="Tracer les murs à la main, dans le plan"
                        >
                          <IconPencil aria-hidden="true" /> Tracer moi-même
                        </button>
                        <button
                          type="button"
                          onClick={() => props.onAddSource()}
                          title="Un plan DXF, PDF ou une image de plan, ou des photos de la pièce : le plan se construit"
                        >
                          <IconUpload aria-hidden="true" /> Importer un plan
                          (DXF, PDF, image)
                        </button>
                        <button
                          type="button"
                          className="primary"
                          onClick={() => props.onGeneratePlan()}
                          title="D'après les photos de la pièce, sinon d'après vos exigences (plan proposé, à ajuster)"
                        >
                          Générer le plan
                        </button>
                      </span>
                    </>
                  )
                )}
              </span>
            ) : level ? (
              <img
                className="board-plan"
                src={`${api.exportSvgUrl(project.id, item.ref ?? "", dark ? "dark" : "light")}&v=${props.planVersion}`}
                alt={`Plan ${level.name}`}
                draggable={false}
              />
            ) : (
              <span className="board-missing">niveau introuvable</span>
            )}
          </>
        );
      }
      case "video": {
        return item.ref ? (
          <VideoCard
            url={boardApi.mediaUrl(project.id, item.ref)}
            name={item.text}
          />
        ) : (
          <span className="board-missing">vidéo introuvable</span>
        );
      }
      case "pdf": {
        const ref = media.find((m) => m.id === item.ref);
        return item.ref ? (
          <PdfCard
            url={boardApi.mediaUrl(project.id, item.ref)}
            thumb={
              ref?.thumb ? boardApi.mediaThumbUrl(project.id, item.ref) : null
            }
            name={item.text}
          />
        ) : (
          <span className="board-missing">PDF introuvable</span>
        );
      }
      case "contextual": {
        const [run, variant] = (item.ref ?? "").split(":");
        const entry = contextual.find(
          (e) => e.run_id === run && e.variant === variant,
        );
        return run && variant ? (
          <ContextualCard
            url={contextualApi.imageUrl(project.id, run, `${variant}.png`)}
            title={item.text}
            entry={entry}
            kept={!!item.kept}
            comment={item.comment ?? ""}
            onKeep={() => patchItem(item.id, { kept: !item.kept })}
            onComment={(text) => patchItem(item.id, { comment: text })}
            onDuplicate={() => duplicateItems([item])}
          />
        ) : (
          <span className="board-missing">rendu introuvable</span>
        );
      }
      case "link":
        return item.url ? (
          <LinkCard url={item.url} title={item.text} />
        ) : (
          <span className="board-missing">lien sans adresse</span>
        );
      case "requirements":
        // T7b : la note Exigences a quitté le tableau (compositeur) ; le serveur la retire.
        return null;
      case "file": {
        const ref = media.find((m) => m.id === item.ref);
        // T2 : une image jointe (inspiration) se montre, avec son nom.
        if (ref?.kind === "image" && item.ref)
          return (
            <>
              <AuthedImg
                src={boardApi.mediaUrl(project.id, item.ref)}
                alt={item.text || "Image"}
                missing="image indisponible"
              />
              <span className="board-caption">{item.text}</span>
            </>
          );
        return (
          <span className="board-file">
            <b title={item.text}>{item.text}</b>
            <span className="muted">
              {ref
                ? `${Math.max(1, Math.round(ref.size / 1024))} Ko`
                : "fichier joint"}
            </span>
            {item.ref && (
              <a
                href={boardApi.mediaUrl(project.id, item.ref)}
                download={item.text}
                onPointerDown={(e) => e.stopPropagation()}
              >
                Télécharger
              </a>
            )}
          </span>
        );
      }
      case "note":
        // KAN-65 : un post-it ; le texte se réduit pour tenir.
        return (
          <PostitBody
            item={item}
            editing={editing === item.id}
            onText={(text) => {
              setEditing(null);
              if (text !== item.text)
                commit(
                  items.map((i) => (i.id === item.id ? { ...i, text } : i)),
                );
            }}
          >
            {editing !== item.id && item.role === "prompt" && (
              <button
                type="button"
                className="primary board-variant"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => props.onVariant(item.text)}
                title="Envoie le texte de la note à l'assistant (un seul moteur)"
              >
                Nouvelle variante → assistant
              </button>
            )}
          </PostitBody>
        );
      case "frame":
        return (
          <>
            {props.queued &&
            item.role === "renders" &&
            frameLevel(item) === (props.activeLevelId ?? firstLevelId) ? (
              <span
                className="board-frame-hint board-frame-queued"
                role="status"
                onPointerDown={(e) => e.stopPropagation()}
              >
                <span className="spinner" aria-hidden="true" />
                {QUEUED_TEXT}
                <button type="button" onClick={props.onCancelQueued}>
                  Annuler
                </button>
              </span>
            ) : readOnly && READ_ONLY_EMPTY[item.role ?? ""] ? (
              !items.some((i) => i.frame === item.id) && (
                <span className="board-frame-hint">
                  {READ_ONLY_EMPTY[item.role ?? ""]}
                </span>
              )
            ) : item.role === "renders" && caps.online && !caps.renders ? (
              // KAN-89 : le compte autorisé y demande ses rendus au poste d'augus ; les
              // autres y lisent toujours « Rendus réalistes : bientôt en ligne ».
              <RealisticRenders
                project={project}
                levelId={frameLevel(item)}
                readOnly={readOnly}
                frameId={item.id}
                title={item.text}
              />
            ) : missingHint(item.role ?? "", caps) ? (
              <span
                className="board-frame-hint board-frame-missing"
                role="note"
              >
                {missingHint(item.role ?? "", caps)}
              </span>
            ) : (
              FRAME_HINTS[item.role ?? ""] &&
              !items.some((i) => i.frame === item.id) && (
                <span
                  className="board-frame-hint"
                  title={FRAME_TIPS[item.role ?? ""]}
                >
                  {FRAME_HINTS[item.role ?? ""]}
                </span>
              )
            )}
            {/* T7a (demande d'augus) : importer droit dans le cadre Photos ou Inspirations ;
                même effet qu'un dépôt dans ce cadre. */}
            {FRAME_IMPORT[item.role ?? ""] &&
              !readOnly &&
              !items.some((i) => i.frame === item.id) && (
                <span
                  className="board-frame-import-row"
                  onPointerDown={(e) => e.stopPropagation()}
                >
                  <button
                    type="button"
                    className="board-frame-import"
                    title={FRAME_IMPORT[item.role ?? ""]!.tip}
                    onClick={() => pickInto(item)}
                  >
                    <IconUpload aria-hidden="true" /> Importer
                  </button>
                  {item.role === "photos" && (
                    // KAN-91 L5 : le guide, à côté de « Importer », seulement sur demande ;
                    // nom court pour tenir dans la carte étroite (KAN-77), entier pour les
                    // lecteurs d'écran et dans l'infobulle.
                    <button
                      type="button"
                      className="link board-frame-guide"
                      aria-label={PHOTO_GUIDE_LINK}
                      title={PHOTO_GUIDE_LINK}
                      onClick={() => setGuide({ frame: item })}
                    >
                      Guide
                    </button>
                  )}
                </span>
              )}
            <span
              className={
                item.role
                  ? "board-frame-title board-frame-head"
                  : "board-frame-title"
              }
            >
              {editing === item.id ? (
                <input
                  autoFocus // NOSONAR S9379 : renommage ouvert par l'utilisateur
                  aria-label="Nom du cadre"
                  defaultValue={item.text}
                  onPointerDown={(e) => e.stopPropagation()}
                  onBlur={(e) => {
                    const text = e.target.value.trim().slice(0, 120) || "Cadre";
                    setEditing(null);
                    if (text !== item.text)
                      commit(
                        items.map((i) =>
                          i.id === item.id ? { ...i, text } : i,
                        ),
                      );
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter")
                      (e.target as HTMLInputElement).blur();
                  }}
                />
              ) : item.role ? (
                // T7a : un nom long (« Rendus · Variante bureau ») se tronque sur une ligne,
                // en entier dans l'infobulle.
                <>
                  <span className="board-frame-name" title={item.text}>
                    {item.text}
                  </span>
                  {/* KAN-98 : le compteur à côté du titre (« 6 photos », « 4 images »). */}
                  {frameCount(item) && (
                    <span className="board-frame-count">
                      {frameCount(item)}
                    </span>
                  )}
                </>
              ) : (
                item.text
              )}
              {FRAME_IMPORT[item.role ?? ""] &&
                editing !== item.id &&
                (items.some((i) => i.frame === item.id) ||
                  (item.role === "inspirations" && !readOnly)) && (
                  // T7a : cadre rempli : un petit « + » dans l'en-tête. KAN-77 : « Lien »
                  // quitte la barre de gauche pour l'en-tête du cadre Inspirations.
                  <span
                    className="board-frame-actions"
                    onPointerDown={(e) => e.stopPropagation()}
                  >
                    {items.some((i) => i.frame === item.id) && (
                      <button
                        type="button"
                        className="icon board-frame-icon"
                        aria-label={FRAME_IMPORT[item.role ?? ""]!.tip}
                        title={FRAME_IMPORT[item.role ?? ""]!.tip}
                        onClick={() => pickInto(item)}
                      >
                        <IconPlus />
                      </button>
                    )}
                    {item.role === "photos" && (
                      // KAN-91 L5 : cadre rempli, le guide à côté du « + » (nom court, le
                      // libellé entier pour les lecteurs d'écran et dans l'infobulle).
                      <button
                        type="button"
                        className="link board-frame-guide"
                        aria-label={PHOTO_GUIDE_LINK}
                        title={PHOTO_GUIDE_LINK}
                        onClick={() => setGuide({ frame: item })}
                      >
                        Guide
                      </button>
                    )}
                    {item.role === "inspirations" && !readOnly && (
                      <button
                        type="button"
                        className="icon board-frame-icon"
                        aria-label="Ajouter un lien"
                        title="Ajouter un lien (la page n'est jamais récupérée)"
                        onClick={() =>
                          setLinkForm({ url: "", title: "", frame: item.id })
                        }
                      >
                        <IconLink />
                      </button>
                    )}
                  </span>
                )}
              {item.role === "renders" && (
                // T7d1–T7d2 : ce que portait l'ancien écran de résultats : les vues
                // périmées à refaire, la visionneuse, le volet Rendus (réglages, galerie).
                // T7e : chaque cadre agit sur les vues de son plan.
                <RendersFrameActions
                  levelId={frameLevel(item)}
                  stale={[...props.stale].filter(
                    (c) => project.levels[frameLevel(item)]?.elements[c],
                  )}
                  hasRender={items.some(
                    (i) =>
                      i.frame === item.id &&
                      i.kind === "render" &&
                      i.role !== "inspiration",
                  )}
                  noPlan={!hasPlan(board, item.id)}
                  onRerender={props.onRerender}
                  onOpenViewer={props.onOpenViewer}
                  onOpenRendersPane={props.onOpenRendersPane}
                />
              )}
            </span>
          </>
        );
    }
  };

  /** Entrer dans le plan (B3) : la vue zoome en continu sur son cadre, puis l'éditeur de plan
   *  prend l'espace. Ce zoom n'est pas enregistré : « ← Tableau » rend la vue d'avant. */
  const enterPlan = (item: BoardItem) => {
    const el = stage.current;
    if (!item.ref || zooming) return;
    if (el) {
      const rect = el.getBoundingClientRect();
      const zoom = Math.min(
        ZOOM_MAX,
        (rect.width - 2 * ENTER_MARGIN) / item.w,
        (rect.height - 2 * ENTER_MARGIN) / item.h,
      );
      setZooming(true);
      setViewState({
        zoom,
        x: (rect.width - item.w * zoom) / 2 - item.x * zoom,
        y: (rect.height - item.h * zoom) / 2 - item.y * zoom,
      });
    }
    const ref = item.ref;
    window.setTimeout(() => props.onEditPlan(ref), ENTER_MS);
  };

  const onDouble = (item: BoardItem) => {
    if (item.kind === "plan") return enterPlan(item);
    if (item.locked) return;
    // KAN-50, KAN-65 : un lecteur ou un commentateur lit les post-its sans les écrire.
    if (item.kind === "note" || item.kind === "frame") {
      if (!readOnly) setEditing(item.id);
    } else if (
      item.kind === "render" &&
      item.ref &&
      item.role !== "inspiration"
    )
      props.onOpenRender(item.ref);
    else if (item.kind === "pdf" && item.ref)
      window.open(
        boardApi.mediaUrl(project.id, item.ref),
        "_blank",
        "noopener",
      );
    else if (item.kind === "link" && item.url)
      window.open(item.url, "_blank", "noopener,noreferrer");
    else if (item.kind === "photo" && item.ref)
      window.open(api.imageFileUrl(project.id, item.ref), "_blank", "noopener");
    else if (item.kind === "render" && item.ref)
      window.open(renderUrl(item) ?? "", "_blank", "noopener");
  };

  const sorted = [...items].sort(
    (a, b) =>
      (a.kind === "frame" ? 0 : 1) - (b.kind === "frame" ? 0 : 1) || a.z - b.z,
  );
  const single = selection.length === 1 ? selection[0] : undefined;
  // KAN-91 : cartes par id (pastille « hors carte »), images jointes, et rendus d'une carte
  // Rendus sans plan relié (D2 : ils restent visibles, atténués).
  const byId = new Map(items.map((i) => [i.id, i]));
  const isImageFile = (i: BoardItem) =>
    i.kind === "file" && media.find((m) => m.id === i.ref)?.kind === "image";
  const unplanned = new Set(
    items
      .filter((i) => cardRole(i) === "renders" && !hasPlan(board, i.id))
      .map((i) => i.id),
  );
  const dimmed = new Set(
    items.filter((i) => i.frame && unplanned.has(i.frame)).map((i) => i.id),
  );
  // Retours de la MEP 14 : une carte métier posée sur une autre (déplacement à la main,
  // permis) le montre ; rien n'est déplacé d'office.
  const overlaps = overlapping(items.filter((i) => cardRole(i) !== null));
  const box =
    selection.length > 0
      ? {
          x: Math.min(...selection.map((i) => i.x)),
          y: Math.min(...selection.map((i) => i.y)),
          w:
            Math.max(...selection.map((i) => i.x + i.w)) -
            Math.min(...selection.map((i) => i.x)),
          bottom: Math.max(...selection.map((i) => i.y + i.h)),
        }
      : null;
  const marquee =
    drag && (drag.kind === "marquee" || drag.kind === "frame") ? drag : null;

  // KAN-65 : les post-its sélectionnés (non verrouillés) changent de couleur en un geste ;
  // la couleur choisie devient celle des prochains.
  const postits = selection.filter((i) => i.kind === "note" && !i.locked);
  const recolor = (color: NonNullable<BoardItem["color"]>) => {
    rememberColor(color);
    const ids = new Set(
      postits.filter((i) => colorOf(i) !== color).map((i) => i.id),
    );
    if (ids.size)
      commit(items.map((i) => (ids.has(i.id) ? { ...i, color } : i)));
  };

  // KAN-64 : les actions de la sélection (« propriétés »), dans la zone haute du panneau
  // de droite, ou en bas au centre quand le panneau est fermé.
  const selectionActions =
    box && !drag ? (
      <div
        className="board-context"
        role="toolbar"
        aria-label="Actions de la sélection"
        onPointerDown={(e) => e.stopPropagation()}
      >
        {!readOnly && postits.length > 0 && (
          <PostitPalette notes={postits} onPick={recolor} />
        )}
        {single?.kind === "plan" && single.ref && (
          <button type="button" onClick={() => enterPlan(single)}>
            Modifier le plan
          </button>
        )}
        {/* KAN-78 : réagir à l'élément choisi (commentateur et plus). */}
        {single && props.reactions?.canReact && (
          <button
            type="button"
            className="board-react-open"
            aria-expanded={props.reactions.pickerFor === single.id}
            onClick={() =>
              props.reactions?.openPicker(
                props.reactions.pickerFor === single.id ? null : single.id,
              )
            }
            title="Réagir à cet élément (👍 ❤️ 😍 🤔 👎 🔥)"
          >
            <IconReact aria-hidden="true" /> Réagir
          </button>
        )}
        {caps.images &&
          single &&
          single.ref &&
          (single.kind === "photo" ||
            single.kind === "plan" ||
            (single.kind === "render" && single.role !== "inspiration")) && (
            <button
              type="button"
              onClick={() =>
                props.onContextual({
                  kind: single.kind as "photo" | "render" | "plan",
                  id: single.ref ?? "",
                  label:
                    single.text ||
                    (single.kind === "photo"
                      ? "Photo"
                      : single.kind === "plan"
                        ? "Plan"
                        : "Vue"),
                })
              }
              title="Une image guidée par cette source et enrichie par le plan (le plan et la 3D ne bougent pas)"
            >
              Rendu contextualisé…
            </button>
          )}
        {/* KAN-66 : mesure de référence sur la photo ; un lecteur voit la mesure posée. */}
        {single?.kind === "photo" &&
          single.ref &&
          project.images[single.ref] &&
          (!readOnly || referenceOf(project, single.ref)) && (
            <button
              type="button"
              onClick={() =>
                setMeasureFor({
                  id: single.ref ?? "",
                  label: single.text || "Photo",
                })
              }
              title="Deux points sur une longueur connue (une porte de 2,04 m…) : l'estimation se recale"
            >
              {readOnly ? "Voir la mesure" : "Mesurer sur la photo"}
            </button>
          )}
        {single?.kind === "render" &&
          single.ref &&
          single.role !== "inspiration" && (
            <button
              type="button"
              onClick={() => props.onOpenRender(single.ref ?? "")}
              title="Ce rendu en grand, dans la visionneuse"
            >
              Voir en grand
            </button>
          )}
        {outsideInspirations.length > 0 && inspirationsCard && !readOnly && (
          <button
            type="button"
            onClick={toInspirations}
            title="Comme un glisser dans le Moodboard : l'allure de cette image (matières, ambiance) guide les rendus reliés, pas sa géométrie"
          >
            Dans le Moodboard
          </button>
        )}
        <button type="button" onClick={duplicate} title="Dupliquer (Ctrl+D)">
          Dupliquer
        </button>
        <button type="button" onClick={toggleLock} title="Verrouiller (Ctrl+L)">
          {selection.every((i) => i.locked) ? "Déverrouiller" : "Verrouiller"}
        </button>
        <button type="button" onClick={() => order(true)} title="Devant (])">
          Devant
        </button>
        <button type="button" onClick={() => order(false)} title="Derrière ([)">
          Derrière
        </button>
        <button
          type="button"
          className="danger"
          onClick={remove}
          title="Supprimer (Suppr)"
        >
          Supprimer
        </button>
      </div>
    ) : null;
  // KAN-65 : sur une ligne (le texte d'un post-it peut en avoir plusieurs), coupé par le CSS.
  const singleText = single ? single.text.split(/\s+/).join(" ").trim() : "";
  const selectionTitle = single
    ? singleText
      ? `« ${singleText} »`
      : "Sélection"
    : `${selection.length} éléments`;

  return (
    <section className="board" aria-label="Espace de travail">
      {/* KAN-51 : le commentateur (espace de travail en lecture) commente quand même. */}
      {readOnly && props.comments?.canComment && (
        <div
          className="board-rail"
          role="toolbar"
          aria-label="Outils de l'espace de travail"
        >
          {RAIL_TOOLS.filter(([id]) => id !== "note" && id !== "frame").map(
            ([id, Icon, label]) => (
              <button
                key={id}
                type="button"
                className={`icon${tool === id ? " active" : ""}`}
                aria-label={label.split(" ")[0]}
                aria-pressed={tool === id}
                title={label}
                onClick={() => setTool(id)}
              >
                <Icon />
              </button>
            ),
          )}
        </div>
      )}
      <div
        className="board-rail"
        hidden={readOnly}
        role="toolbar"
        aria-label="Outils de l'espace de travail"
      >
        {/* KAN-77 (MH10, MH11) : Sélection, Main, Post-it, Cadre, Commentaire, en icônes au
            trait ; Lien et Importer sont dans les cadres (Inspirations, Plan) ; coller une
            adresse et glisser-déposer un fichier marchent toujours. */}
        {RAIL_TOOLS.filter(
          ([id]) => id !== "comment" || props.comments?.canComment,
        ).map(([id, Icon, label]) => (
          <button
            key={id}
            type="button"
            className={`icon${tool === id ? " active" : ""}`}
            aria-label={label.split(" ")[0]}
            aria-pressed={tool === id}
            title={label}
            onClick={() => setTool(id)}
          >
            <Icon />
          </button>
        ))}
        <span className="board-sep" />
        <button
          type="button"
          className="icon"
          aria-label="Annuler dans l'espace de travail"
          title="Annuler dans l'espace de travail (Ctrl+Z)"
          disabled={!props.canUndo}
          onClick={props.onUndo}
        >
          <IconUndo />
        </button>
        <button
          type="button"
          className="icon"
          aria-label="Rétablir dans l'espace de travail"
          title="Rétablir dans l'espace de travail (Ctrl+Maj+Z)"
          disabled={!props.canRedo}
          onClick={props.onRedo}
        >
          <IconRedo />
        </button>
      </div>

      {/* T7a : le sélecteur des boutons « Importer » / « + » des cadres. */}
      <input
        ref={frameFileRef}
        type="file"
        multiple
        hidden
        data-testid="frame-file-input"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          onFramePicked(files);
        }}
      />
      <div
        ref={stage}
        className={`board-stage tool-${tool}${drag?.kind === "pan" ? " panning" : ""}`}
        data-world="1"
        onPointerDown={onStageDown}
        onPointerMove={(e) => {
          props.presence?.cursor(toWorld(e.clientX, e.clientY));
          onMove(e);
        }}
        onPointerLeave={() => props.presence?.cursor(null)}
        // B2b : tout geste de ma part arrête de suivre.
        onPointerDownCapture={stopFollowing}
        onWheelCapture={stopFollowing}
        onPointerUp={onUp}
        onWheel={onWheel}
        onDragOver={(e) => {
          if (Array.from(e.dataTransfer.types).includes("Files"))
            e.preventDefault();
        }}
        onDrop={(e) => {
          const files = Array.from(e.dataTransfer.files);
          if (!files.length) return;
          e.preventDefault();
          void importFiles(files, toWorld(e.clientX, e.clientY));
        }}
      >
        <div
          className={`board-world${zooming ? " zooming" : ""}`}
          data-world="1"
          style={
            {
              transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})`,
              // Titres et boutons des cadres gardent une taille lisible à tout zoom.
              "--inv": 1 / view.zoom,
            } as React.CSSProperties
          }
        >
          {sorted.map((item) => (
            <div
              key={item.id}
              className={`board-item kind-${item.kind}${selected.has(item.id) ? " selected" : ""}${item.locked ? " locked" : ""}${item.reference ? " reference" : ""}${item.kept ? " kept" : ""}${item.role ? ` role-${item.role}` : ""}${item.kind === "note" ? ` postit-${colorOf(item)}` : ""}${dropHint?.frame === item.id ? (dropHint.ok ? " board-drop-ok" : " board-drop-refused") : ""}${dimmed.has(item.id) ? " board-noplan" : ""}${offCard(item, byId, isImageFile(item)) ? " board-offcard-item" : ""}${closeUp && item.kind === "photo" && item.ref === closeUp.imageId ? " photo-closeup" : ""}${overlaps.has(item.id) ? " board-overlap" : ""}`}
              style={{
                left: item.x,
                top: item.y,
                width: item.w,
                height: item.h,
                zIndex: item.kind === "frame" ? 0 : 10 + item.z,
              }}
              data-kind={item.kind}
              aria-label={`${item.kind === "frame" ? "Cadre" : item.kind} ${item.text}`.trim()}
              onPointerDown={(e) => onItemDown(e, item)}
              onPointerEnter={() => hover.enter(item.id)}
              onPointerLeave={hover.leave}
            >
              {body(item)}
              {item.reference && (
                <span
                  className="board-reference"
                  title="Dans un Moodboard : son allure guide les rendus reliés"
                >
                  Référence
                </span>
              )}
              {offCard(item, byId, isImageFile(item)) && (
                // KAN-91 (C1) : une image libre n'est jamais utilisée ; elle dit où la glisser.
                <span
                  className="board-offcard"
                  title={
                    item.kind === "photo"
                      ? "Glissez-la dans « Photos de la pièce » pour qu'elle serve au plan"
                      : "Glissez-la dans le Moodboard pour qu'elle guide un rendu"
                  }
                >
                  {OFF_CARD}
                </span>
              )}
              {overlaps.has(item.id) && (
                <span
                  className="board-overlap-tag"
                  title="Ce node en recouvre un autre : écartez-les pour que les liens restent lisibles"
                >
                  recouvre un node
                </span>
              )}
              {item.locked && (
                <span className="board-lock" aria-label="verrouillé">
                  🔒
                </span>
              )}
              {single?.id === item.id && !item.locked && (
                <span
                  className="board-resize"
                  aria-label="Redimensionner"
                  onPointerDown={(e) => onResizeDown(e, item)}
                />
              )}
            </div>
          ))}
          {/* KAN-91 : les liens entre cartes, au-dessus des cadres, sous les cartes. */}
          <WireLayer
            projectId={project.id}
            board={board}
            items={items}
            zoom={view.zoom}
            readOnly={readOnly}
            selected={wireSelected}
            ghosts={ghosts}
            drag={wireDrag}
            onSelect={setWireSelected}
            onCut={cutLink}
            onHandleDown={onWireHandleDown}
          />
          {dropHint && (
            <div
              className={`board-wire-bubble board-drop-hint${dropHint.ok ? "" : " refused"}`}
              role="status"
              style={{
                left: dropHint.x,
                top: dropHint.y,
                transform: `translate(-50%, 8px) scale(${1 / view.zoom})`,
                transformOrigin: "top center",
              }}
            >
              {dropHint.text}
            </div>
          )}
          {props.presence && (
            <PresenceLayer
              store={props.presence.store}
              peers={props.peers ?? []}
              items={items}
            />
          )}
          {props.comments && <CommentPins c={props.comments} items={items} />}
          {/* KAN-78 : pastilles des réactions sous chaque élément, et le sélecteur. */}
          {props.reactions && (
            <ReactionRows
              r={props.reactions}
              items={items}
              hovered={hover.hovered}
              selectedId={single?.id ?? null}
              onEnter={hover.enter}
              onLeave={hover.leave}
            />
          )}
          {marquee && (
            <div
              className={`board-marquee${marquee.kind === "frame" ? " frame" : ""}`}
              style={{
                left: Math.min(marquee.x, marquee.to.x),
                top: Math.min(marquee.y, marquee.to.y),
                width: Math.abs(marquee.to.x - marquee.x),
                height: Math.abs(marquee.to.y - marquee.y),
              }}
            />
          )}
        </div>

        {/* KAN-64 : panneau ouvert, la zone haute montre les actions de la sélection, sinon
            l'Assistant (le compositeur) ; panneau fermé, seules les actions de la sélection
            restent, en bas au centre. Plus rien d'autre en bas que le zoom. */}
        {props.side?.top
          ? createPortal(
              selectionActions ? (
                <section className="board-zone-inner" aria-label="Sélection">
                  <header className="board-zone-head">
                    <strong className="board-zone-title" title={selectionTitle}>
                      {selectionTitle}
                    </strong>
                    <span className="board-zone-sp" />
                    <button
                      type="button"
                      className="link"
                      onClick={() => setSelected(new Set())}
                      title="Désélectionner et revenir à l'Assistant"
                    >
                      ← Assistant
                    </button>
                  </header>
                  {selectionActions}
                </section>
              ) : !readOnly ? (
                <section className="board-zone-inner" aria-label="Assistant">
                  <header className="board-zone-head">
                    <button
                      type="button"
                      className="board-zone-toggle"
                      aria-expanded={assistantOpen}
                      aria-label={
                        assistantOpen
                          ? "Replier l'Assistant"
                          : "Déplier l'Assistant"
                      }
                      onClick={() => setAssistantOpen((o) => !o)}
                    >
                      <span aria-hidden="true">
                        {assistantOpen ? "▾" : "▸"}
                      </span>{" "}
                      Assistant
                    </button>
                  </header>
                  {assistantOpen && (
                    <Composer
                      key={project.id}
                      docked
                      project={project}
                      hasRenders={props.hasRenders}
                      planProgress={props.planProgress}
                      onSettings={props.onSettings}
                      onVariant={props.onVariant}
                      onGenerateNow={props.onGenerateNow}
                    />
                  )}
                </section>
              ) : null,
              props.side.top,
            )
          : selectionActions && (
              <div className="board-bottom">{selectionActions}</div>
            )}
        {props.comments &&
          props.side?.comments &&
          createPortal(
            <CommentsPanel c={props.comments} items={items} inline />,
            props.side.comments,
          )}
        {!readOnly && <WireBanner projectId={project.id} board={board} />}
        {toast && (
          <WireToast
            text={toast.text}
            onUndo={readOnly ? undefined : toast.undo}
            onClose={() => setToast(null)}
            ms={TOAST_MS}
          />
        )}
        {!readOnly && (
          // KAN-98 : barre d'ajout fixe, en bas au centre, même place et même taille à tout
          // zoom ; un clic pose un node vide (Plan : la boîte « Nouveau plan »). Sans raccourci.
          <div
            className="board-addbar"
            role="group"
            aria-label="Ajouter"
            onPointerDown={(e) => e.stopPropagation()}
          >
            <span className="board-addbar-label" aria-hidden="true">
              Ajouter
            </span>
            {props.onNewPlan && (
              <button
                type="button"
                className="board-addbar-btn type-plan"
                onClick={props.onNewPlan}
                title="Plan : variante d'un plan, nouvel étage ou plan vide"
              >
                <IconPlanNode aria-hidden="true" /> Plan
              </button>
            )}
            <button
              type="button"
              className="board-addbar-btn type-photos"
              onClick={addPhotosCard}
              title="Photos de la pièce : un node vide au centre de la vue (ou glissez-le où vous voulez)"
            >
              <IconPhotosNode aria-hidden="true" /> {PHOTOS_TITLE}
            </button>
            <button
              type="button"
              className="board-addbar-btn type-moodboard"
              onClick={addInspirationsCard}
              title="Moodboard : un node vide au-dessus du node Rendus ; ses images guident le style des rendus"
            >
              <IconMoodboardNode aria-hidden="true" /> {MOODBOARD_TITLE}
            </button>
            <button
              type="button"
              className="board-addbar-btn type-renders"
              onClick={addRendersCard}
              disabled={!unrendered}
              title={
                unrendered
                  ? "Rendus : le node Rendus d'un plan qui n'en a plus"
                  : "Rendus : chaque plan a déjà son node Rendus"
              }
            >
              <IconRendersNode aria-hidden="true" /> Rendus
            </button>
          </div>
        )}
        <div
          className="board-zoom"
          role="group"
          aria-label="Zoom de l'espace de travail"
        >
          <button
            type="button"
            className="icon"
            aria-label="Dézoomer"
            onClick={() => zoomAt(1 / 1.2)}
          >
            −
          </button>
          <button
            type="button"
            className="link"
            onClick={() => fit()}
            title="Tout voir (Ctrl+0)"
          >
            {Math.round(view.zoom * 100)} %
          </button>
          <button
            type="button"
            className="icon"
            aria-label="Zoomer"
            onClick={() => zoomAt(1.2)}
          >
            +
          </button>
        </div>

        {linkForm && (
          <form
            className="board-linkform"
            role="dialog"
            aria-label="Ajouter un lien"
            onPointerDown={(e) => e.stopPropagation()}
            onSubmit={(e) => {
              e.preventDefault();
              submitLink();
            }}
          >
            <label>
              Adresse
              <input
                autoFocus
                type="url"
                inputMode="url"
                placeholder="https://…"
                value={linkForm.url}
                onChange={(e) =>
                  setLinkForm({ ...linkForm, url: e.target.value })
                }
              />
            </label>
            <label>
              Titre
              <input
                placeholder="facultatif : le domaine sinon"
                maxLength={200}
                value={linkForm.title}
                onChange={(e) =>
                  setLinkForm({ ...linkForm, title: e.target.value })
                }
              />
            </label>
            <span className="muted">La page n'est jamais récupérée.</span>
            <span className="board-linkform-actions">
              <button
                type="button"
                className="link"
                onClick={() => setLinkForm(null)}
              >
                Annuler
              </button>
              <button type="submit" className="primary">
                Ajouter le lien
              </button>
            </span>
          </form>
        )}
        {(props.notice || message) && (
          <div className="board-notice" role="alert">
            <span>{props.notice ?? message}</span>
            <button
              type="button"
              className="link"
              onClick={() => {
                props.onDismissNotice();
                setMessage(null);
              }}
            >
              fermer
            </button>
          </div>
        )}
        {closeUp && (
          // KAN-91 L5 : après l'import, la photo montre un détail, pas la pièce.
          <div className="board-closeup" role="alert">
            <IconAlert aria-hidden="true" />
            <span>{CLOSE_UP_TEXT}</span>
            <button
              type="button"
              className="link"
              onClick={() => {
                // Le cadre Photos de cette photo, sinon le premier cadre Photos.
                const frames = items.filter(
                  (i) => i.kind === "frame" && i.role === "photos",
                );
                setGuide({
                  frame:
                    frames.find((f) =>
                      items.some(
                        (p) => p.frame === f.id && p.ref === closeUp.imageId,
                      ),
                    ) ??
                    frames[0] ??
                    null,
                });
              }}
            >
              {PHOTO_GUIDE_LINK}
            </button>
            <button
              type="button"
              className="link"
              onClick={() => setCloseUpDone(closeUp.runId)}
            >
              fermer
            </button>
          </div>
        )}
        {guide && (
          <PhotoGuide
            onClose={() => setGuide(null)}
            onImport={
              guide.frame && !readOnly
                ? () => pickInto(guide.frame as BoardItem)
                : undefined
            }
          />
        )}
        {measureFor && project.images[measureFor.id] && (
          <PhotoMeasure
            project={project}
            imageId={measureFor.id}
            label={measureFor.label}
            readOnly={readOnly}
            onClose={() => setMeasureFor(null)}
            onProjectChanged={props.onProjectChanged}
          />
        )}
      </div>
    </section>
  );
}
