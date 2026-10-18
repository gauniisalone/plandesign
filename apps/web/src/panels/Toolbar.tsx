import { useEffect, useRef, useState } from "react";
import { useReadOnly } from "../state/account";
import { api } from "../api/client";
import type { Project } from "../api/types.gen";
import type { Tool } from "../editor2d/PlanCanvas";
import {
  IconAlert,
  IconArrowLeft,
  IconCeilings,
  IconChat,
  IconCursor,
  IconDollhouse,
  IconDoor,
  IconDownload,
  IconFit,
  IconGear,
  IconHand,
  IconMaterials,
  IconMinus,
  IconPanel,
  IconPlus,
  IconRedo,
  IconRoofOff,
  IconRoom,
  IconRuler,
  IconSectionFlip,
  IconSectionH,
  IconSectionV,
  IconShare,
  IconSofa,
  IconSparkles,
  IconStairs,
  IconTrash,
  IconUndo,
  IconViewFront,
  IconViewIso,
  IconViewSide,
  IconViewTop,
  IconWall,
  IconWindow,
  IconXray,
} from "../ui/icons";
import type { SectionAxis } from "../viewer3d/view3d";
import type { CameraPreset } from "../viewer3d/Viewer3D";

export type ViewMode = "2d" | "split" | "3d";
/** État de l'atelier : « import » = écran « Ajouter une source » par-dessus le projet,
 *  « edit » = le plan en 2D ou en 3D (selon la vue), « board » = l'espace de travail. */
export type Step = "import" | "edit" | "board";
/** Espaces du projet (B3, maquette validée le 13 oct.) : l'espace de travail, et le plan
 *  édité depuis son cadre (en 2D, en 3D ou les deux). L'écran de résultats a laissé place à
 *  la visionneuse plein écran (T7d2), ouverte depuis les rendus. */
export type Tab = "board" | "plan" | "3d";

/* Barres compactes (maquette validée par augus, 6 oct.) : rangée du haut de 36 px (retour,
   titre tronqué, étapes, panneau, réglages), colonne d'outils à deux colonnes à gauche, barre
   de vues flottante en haut à droite du canevas (partagée par le plan et la 3D), barre du bas
   réduite au zoom et aux exports, bande des photos en bas à gauche. Le recadrage du plan
   réserve ces marges (`Insets`). Chaque bouton à icône a son nom accessible et une infobulle
   avec son raccourci. */

type IconType = (p: React.SVGProps<SVGSVGElement>) => React.JSX.Element;

/** Infobulle « Libellé (raccourci) : précision ». */
function tip(label: string, key?: string, more?: string): string {
  return `${label}${key ? ` (${key})` : ""}${more ? ` : ${more}` : ""}`;
}

function IconButton({
  label,
  shortcut,
  more,
  Icon,
  pressed,
  active,
  disabled,
  danger,
  onClick,
}: {
  label: string;
  shortcut?: string;
  more?: string;
  Icon: IconType;
  /** Bascule : `aria-pressed` ; action ponctuelle : absent. */
  pressed?: boolean;
  /** Mise en avant sans être une bascule (outil courant, volet ouvert). */
  active?: boolean;
  disabled?: boolean;
  danger?: boolean;
  onClick: () => void;
}) {
  const on = pressed ?? active ?? false;
  return (
    <button
      type="button"
      className={`icon${on ? " active" : ""}${danger ? " danger" : ""}`}
      onClick={onClick}
      aria-label={label}
      aria-pressed={pressed}
      aria-keyshortcuts={shortcut ? ariaKeys(shortcut) : undefined}
      title={tip(label, shortcut, more)}
      disabled={disabled}
    >
      <Icon />
    </button>
  );
}

/** « Maj+H » → « Shift+H », « Ctrl+Z » → « Control+Z » (forme de aria-keyshortcuts). */
function ariaKeys(shortcut: string): string {
  return shortcut
    .replace("Maj+", "Shift+")
    .replace("Ctrl+", "Control+")
    .replace("Suppr", "Delete");
}

export type SaveState = "saved" | "saving" | "error";

const SAVE_LABEL: Record<SaveState, string> = {
  saved: "Enregistré",
  saving: "Enregistrement…",
  error: "Échec de l'enregistrement",
};

/** Nom de l'espace affiché, à côté du bouton de navigation (B3). */
const SPACE_LABEL: Record<Tab, string> = {
  board: "Espace de travail",
  plan: "Plan",
  "3d": "Plan · 3D",
};

export function TopBar({
  project,
  tab,
  sourceOpen,
  panelOpen,
  advancedOpen,
  busy,
  onHome,
  onTab,
  onSource,
  assistantOpen,
  onAssistant,
  onTogglePanel,
  onToggleAdvanced,
  save,
  onRetrySave,
  onNewPlan,
  planName,
}: {
  /** T7e : ouvre « + Nouveau plan » (dans l'espace de travail). */
  onNewPlan?: () => void;
  /** T7e : nom du plan ouvert, dit à côté de « Plan » quand le projet en a plusieurs. */
  planName?: string;
  /** B4 : état réel de l'enregistrement (plan, tableau) ; jamais « Enregistré » avant la
   *  réponse du serveur. */
  save: SaveState;
  /** Relancer un enregistrement en échec, quand c'est possible (le tableau). */
  onRetrySave?: () => void;
  project: Project;
  /** Onglet affiché ; null quand « Ajouter une source » est ouvert par-dessus. */
  tab: Tab | null;
  sourceOpen: boolean;
  panelOpen: boolean;
  advancedOpen: boolean;
  busy: boolean;
  onHome: () => void;
  onTab: (t: Tab) => void;
  onSource: () => void;
  /** Assistant unifié (P5) : le même dans les trois onglets. */
  assistantOpen: boolean;
  onAssistant: () => void;
  onTogglePanel: () => void;
  onToggleAdvanced: () => void;
}) {
  const readOnly = useReadOnly();
  return (
    <div className="float topbar">
      <button className="back" onClick={onHome} title="Retour aux projets">
        <IconArrowLeft />
        Projets
      </button>
      <strong className="project-name" title={project.name}>
        {project.name}
      </strong>
      <span className="save-state" role="status" aria-label="Enregistrement">
        <span className={`save-dot ${save}`}>{SAVE_LABEL[save]}</span>
        {save === "error" && onRetrySave && (
          <button type="button" className="link" onClick={onRetrySave}>
            réessayer
          </button>
        )}
      </span>
      {/* B3 : plus d'onglets ; T7d2 : plus d'écran de résultats (les rendus s'ouvrent en
          grand dans la visionneuse). Le plan, édité depuis son cadre, revient à l'espace. */}
      <nav className="spaces" aria-label="Espaces du projet">
        {(tab === "plan" || tab === "3d") && (
          <button
            type="button"
            onClick={() => onTab("board")}
            title="Revenir à l'espace de travail, là où vous l'aviez laissé"
          >
            ← Espace de travail
          </button>
        )}
        {tab && (
          <span className="space-name">
            {SPACE_LABEL[tab]}
            {planName && tab !== "board" ? ` · ${planName}` : ""}
          </span>
        )}
      </nav>
      {/* T7e : « + Nouveau plan », à la place qu'avait « Ajouter une source » (maquette). */}
      {tab === "board" && onNewPlan && (
        <button
          type="button"
          className="new-plan-btn"
          onClick={onNewPlan}
          title="Variante de ce plan, nouvel étage ou plan vide"
        >
          <IconPlus /> <span className="lbl">Nouveau plan</span>
        </button>
      )}
      {/* T7c : dans l'espace de travail, l'import est dans la barre d'outils de gauche. */}
      {tab !== "board" && !readOnly && (
        <button
          type="button"
          className={`add-source${sourceOpen ? " active" : ""}`}
          aria-pressed={sourceOpen}
          onClick={onSource}
          title="Photos ou plan (image, DXF, PDF, SVG) : le plan se construit ou se complète"
        >
          <IconPlus /> <span className="lbl">Ajouter une source</span>
        </button>
      )}
      {!readOnly && (
        <IconButton
          label="Assistant"
          shortcut="/"
          more="l'ambiance, les meubles, des aménagements : une phrase, chaque geste s'annule"
          Icon={IconChat}
          active={assistantOpen}
          disabled={sourceOpen}
          onClick={onAssistant}
        />
      )}
      {busy && (
        <span className="busy-dot" aria-live="polite">
          <span className="spinner" /> en cours…
        </span>
      )}
      <IconButton
        label={panelOpen ? "Masquer le panneau" : "Afficher le panneau"}
        more="le panneau de droite"
        Icon={IconPanel}
        active={panelOpen && !sourceOpen}
        disabled={sourceOpen}
        onClick={onTogglePanel}
      />
      <IconButton
        label="Paramètres avancés"
        shortcut=","
        more="photos, calibration, assistant IA, provenance, diagnostic"
        Icon={IconGear}
        pressed={advancedOpen}
        onClick={onToggleAdvanced}
      />
    </div>
  );
}

/** Colonne d'outils à gauche, deux par rangée, en trois groupes : dessin, aménagement,
 *  historique. Pièce = détecter les pièces ; Cote = Recalibrer ; Escalier = les escaliers
 *  de la bibliothèque. */
export function ToolPalette({
  project,
  tool,
  viewMode,
  busy,
  hasSelection,
  hasBackground,
  catalogOpen,
  layoutOpen,
  onTool,
  onUndo,
  onRedo,
  onDetectRooms,
  onDeleteSelection,
  onCatalog,
  onStairs,
  onLayout,
}: {
  project: Project;
  tool: Tool;
  viewMode: ViewMode;
  busy: boolean;
  hasSelection: boolean;
  /** Quelque chose à mettre à l'échelle (murs ou fond de plan) : « Recalibrer » apparaît. */
  hasBackground: boolean;
  catalogOpen: boolean;
  layoutOpen: boolean;
  onTool: (t: Tool) => void;
  onUndo: () => void;
  onRedo: () => void;
  onDetectRooms: () => void;
  onDeleteSelection: () => void;
  onCatalog: () => void;
  onStairs: () => void;
  onLayout: () => void;
}) {
  const canUndo = project.history_cursor > 0;
  const canRedo = project.history_cursor < project.history.length;
  const drawing = viewMode !== "3d";
  const toolButton = (
    id: Tool,
    label: string,
    shortcut: string,
    Icon: IconType,
    more?: string,
  ) => (
    <IconButton
      label={label}
      shortcut={shortcut}
      more={more}
      Icon={Icon}
      active={tool === id}
      disabled={!drawing}
      onClick={() => onTool(id)}
    />
  );
  return (
    <div className="float card palette" role="toolbar" aria-label="Outils">
      <div className="tools" role="group" aria-label="Dessin">
        {toolButton("select", "Sélection", "V", IconCursor)}
        {toolButton("pan", "Main", "H", IconHand, "déplacer la vue")}
        {toolButton("wall", "Mur", "W", IconWall)}
        <IconButton
          label="Détecter les pièces"
          shortcut="P"
          more="une pièce par contour fermé de murs"
          Icon={IconRoom}
          disabled={busy || !drawing}
          onClick={onDetectRooms}
        />
        {toolButton("door", "Porte", "D", IconDoor)}
        {toolButton("window", "Fenêtre", "F", IconWindow)}
        <IconButton
          label="Escalier"
          shortcut="E"
          more="choisir un escalier dans la bibliothèque, puis cliquer sur le plan"
          Icon={IconStairs}
          disabled={!drawing}
          onClick={onStairs}
        />
        {hasBackground &&
          toolButton(
            "scale",
            "Recalibrer",
            "K",
            IconRuler,
            "deux clics sur une longueur connue (ou la hauteur sous plafond), puis sa valeur réelle → tout le plan se met à l'échelle",
          )}
      </div>
      <div className="tools" role="group" aria-label="Aménagement">
        <IconButton
          label="Meuble"
          shortcut="B"
          more="catalogue de meubles à poser dans le plan"
          Icon={IconSofa}
          active={catalogOpen}
          disabled={!drawing}
          onClick={onCatalog}
        />
        <IconButton
          label="Aménager"
          shortcut="A"
          more="votre souhait → un brief à relire → des dispositions à comparer"
          Icon={IconSparkles}
          active={layoutOpen}
          disabled={!drawing}
          onClick={onLayout}
        />
      </div>
      <div className="tools" role="group" aria-label="Historique">
        <IconButton
          label="Annuler"
          shortcut="Ctrl+Z"
          Icon={IconUndo}
          disabled={!canUndo || busy}
          onClick={onUndo}
        />
        <IconButton
          label="Rétablir"
          shortcut="Ctrl+Y"
          Icon={IconRedo}
          disabled={!canRedo || busy}
          onClick={onRedo}
        />
        <IconButton
          label="Supprimer la sélection"
          shortcut="Suppr"
          more={
            hasSelection
              ? "supprimer l'élément sélectionné"
              : "sélectionnez d'abord un élément du plan"
          }
          Icon={IconTrash}
          danger
          disabled={!hasSelection || busy}
          onClick={onDeleteSelection}
        />
      </div>
    </div>
  );
}

/** Dans le plan : seul, avec l'aperçu 3D à côté, ou la 3D seule (B3 : plus d'onglet 3D). */
const VIEWS: { id: ViewMode; label: string }[] = [
  { id: "2d", label: "Plan" },
  { id: "split", label: "Plan + 3D" },
  { id: "3d", label: "3D" },
];

const PRESETS: {
  id: CameraPreset;
  label: string;
  key: string;
  Icon: IconType;
}[] = [
  { id: "top", label: "Vue de dessus", key: "1", Icon: IconViewTop },
  { id: "front", label: "Vue de face", key: "2", Icon: IconViewFront },
  { id: "side", label: "Vue de côté", key: "3", Icon: IconViewSide },
  { id: "iso", label: "Vue iso", key: "4", Icon: IconViewIso },
];

export interface ViewBarState {
  section: SectionAxis;
  flip: boolean;
  hideRoof: boolean;
  xray: boolean;
  dollhouse: boolean;
  ceilings: boolean;
  materials: boolean;
}

/** Barre de vues flottante en haut à droite du canevas, partagée par le plan et la 3D :
 *  points de vue de la caméra, coupes, affichage de la 3D, et Plan / Plan + 3D dans l'onglet
 *  Plan (la 3D seule est l'onglet 3D). Les
 *  commandes de la 3D ouvrent la vue partagée quand la 3D est cachée. Une coupe active
 *  déplie dessous son curseur (et le PDF d'une coupe verticale). */
export function ViewBar({
  viewMode,
  state,
  sectionRange,
  sectionValue,
  sectionPdfUrl,
  onViewMode,
  onPreset,
  onSection,
  onSectionValue,
  onToggle,
}: {
  viewMode: ViewMode;
  state: ViewBarState;
  sectionRange: [number, number];
  sectionValue: number;
  sectionPdfUrl: string | null;
  onViewMode: (m: ViewMode) => void;
  onPreset: (p: CameraPreset) => void;
  /** Coupe horizontale, verticale (x puis y), ou aucune. */
  onSection: (axis: SectionAxis) => void;
  onSectionValue: (v: number) => void;
  onToggle: (
    key: "flip" | "hideRoof" | "xray" | "dollhouse" | "ceilings" | "materials",
  ) => void;
}) {
  const vertical = state.section === "x" || state.section === "y";
  return (
    <div className="viewbar-wrap">
      <div
        className={`float viewbar${viewMode === "2d" ? " plan-only" : ""}`}
        role="toolbar"
        aria-label="Vues"
      >
        <div className="grp three-d" role="group" aria-label="Points de vue">
          {PRESETS.map((p) => (
            <IconButton
              key={p.id}
              label={p.label}
              shortcut={p.key}
              Icon={p.Icon}
              onClick={() => onPreset(p.id)}
            />
          ))}
        </div>
        <div className="grp three-d" role="group" aria-label="Coupes">
          <IconButton
            label="Coupe horizontale"
            shortcut="Maj+H"
            Icon={IconSectionH}
            pressed={state.section === "z"}
            onClick={() => onSection(state.section === "z" ? "none" : "z")}
          />
          <IconButton
            label="Coupe verticale"
            shortcut="Maj+V"
            more={
              state.section === "x"
                ? "selon l'axe X (encore : axe Y)"
                : state.section === "y"
                  ? "selon l'axe Y (encore : sans coupe)"
                  : "selon l'axe X, puis l'axe Y"
            }
            Icon={IconSectionV}
            pressed={vertical}
            onClick={() =>
              onSection(
                state.section === "x"
                  ? "y"
                  : state.section === "y"
                    ? "none"
                    : "x",
              )
            }
          />
          <IconButton
            label="Retourner la coupe"
            shortcut="Maj+R"
            more="garder l'autre côté du plan de coupe (aperçu)"
            Icon={IconSectionFlip}
            pressed={state.flip && state.section !== "none"}
            disabled={state.section === "none"}
            onClick={() => onToggle("flip")}
          />
        </div>
        <div
          className="grp three-d"
          role="group"
          aria-label="Affichage de la 3D"
        >
          <IconButton
            label="Masquer le toit"
            shortcut="R"
            more={
              state.hideRoof
                ? "actif : versants masqués pour voir l'intérieur (par défaut)"
                : "inactif : versants visibles"
            }
            Icon={IconRoofOff}
            pressed={state.hideRoof}
            onClick={() => onToggle("hideRoof")}
          />
          <IconButton
            label="Rayons X"
            shortcut="X"
            more="tout semi-transparent"
            Icon={IconXray}
            pressed={state.xray}
            onClick={() => onToggle("xray")}
          />
          <IconButton
            label="Maison de poupée"
            shortcut="Maj+D"
            more="toit, charpente, plafonds et murs tournés vers vous masqués"
            Icon={IconDollhouse}
            pressed={state.dollhouse}
            onClick={() => onToggle("dollhouse")}
          />
          <IconButton
            label="Plafonds"
            shortcut="C"
            more="vus de l'extérieur, les plafonds cachent l'intérieur : masqués par défaut"
            Icon={IconCeilings}
            pressed={state.ceilings}
            onClick={() => onToggle("ceilings")}
          />
          <IconButton
            label="Matières"
            shortcut="M"
            more="teintes de matière (bois, pierre, enduit) au lieu des couleurs de provenance"
            Icon={IconMaterials}
            pressed={state.materials}
            onClick={() => onToggle("materials")}
          />
        </div>
        <div className="segmented" role="group" aria-label="Vue">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              className={viewMode === v.id ? "active" : ""}
              aria-pressed={viewMode === v.id}
              onClick={() => onViewMode(v.id)}
            >
              {v.label}
            </button>
          ))}
        </div>
      </div>
      {state.section !== "none" && (
        <div className="float section-pop" role="group" aria-label="Coupe">
          <span className="muted">
            {state.section === "z"
              ? "Hauteur"
              : `Axe ${state.section.toUpperCase()}`}
          </span>
          <input
            type="range"
            aria-label="Position de la coupe"
            min={sectionRange[0]}
            max={sectionRange[1]}
            step={0.05}
            value={sectionValue}
            onChange={(e) => onSectionValue(Number(e.target.value))}
          />
          <span className="value">
            {sectionValue.toFixed(2).replace(".", ",")} m
          </span>
          {sectionPdfUrl && (
            <a href={sectionPdfUrl} download>
              PDF de la coupe
            </a>
          )}
        </div>
      )}
    </div>
  );
}

export function BottomBar({
  project,
  levelId,
  viewMode,
  scale,
  violations,
  onZoom,
  onFit,
  onChecks,
  levelSlot,
}: {
  project: Project;
  levelId: string;
  viewMode: ViewMode;
  /** T7d1 : nom et hauteur du niveau, en tête de la barre du bas (panneau fermé par défaut). */
  levelSlot?: React.ReactNode;
  /** pixels par mètre du plan ; 100 % = 100 px/m */
  scale: number;
  /** nombre de vérifications en défaut (rapport courant) */
  violations: number;
  onZoom: (factor: number) => void;
  onFit: () => void;
  onChecks: () => void;
}) {
  const [exportsOpen, setExportsOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!exportsOpen) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setExportsOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [exportsOpen]);

  const copyLink = () => {
    void navigator.clipboard?.writeText(window.location.href).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };
  const plan = viewMode !== "3d";
  return (
    <div className="float bottombar">
      {plan && levelSlot}
      {plan && (
        <>
          <IconButton
            label="Zoom arrière"
            shortcut="-"
            more="ou la molette"
            Icon={IconMinus}
            onClick={() => onZoom(1 / 1.25)}
          />
          <span
            className="zoom-value"
            title="Échelle du plan : 100 % = 100 px par mètre"
          >
            {Math.round(scale)} %
          </span>
          <IconButton
            label="Zoom avant"
            shortcut="+"
            more="ou la molette"
            Icon={IconPlus}
            onClick={() => onZoom(1.25)}
          />
          <IconButton
            label="Recadrer"
            shortcut="0"
            more="tout le plan dans la zone libre, hors des barres"
            Icon={IconFit}
            onClick={onFit}
          />
          <span className="sep" />
        </>
      )}
      {violations > 0 && (
        <button
          className="checks"
          onClick={onChecks}
          title="Vérifications en défaut : ouvre la liste dans le panneau"
        >
          <IconAlert />
          {violations}
        </button>
      )}
      <div className="menu" ref={menuRef}>
        <button
          type="button"
          className="icon"
          onClick={() => setExportsOpen((o) => !o)}
          aria-label="Exporter"
          aria-expanded={exportsOpen}
          title="Exporter : SVG, PDF, DXF, GLB, JSON"
        >
          <IconDownload />
        </button>
        {exportsOpen && (
          <div className="menu-pop" role="menu">
            <a
              href={api.exportSvgUrl(project.id, levelId)}
              target="_blank"
              rel="noreferrer"
            >
              SVG
              <small>plan vectoriel, s'ouvre dans un onglet</small>
            </a>
            <a href={api.exportPdfUrl(project.id, levelId)} download>
              PDF
              <small>plan coté, échelle normalisée, cartouche</small>
            </a>
            <a href={api.exportDxfUrl(project.id, levelId)} download>
              DXF
              <small>mètres, un calque par nature d'élément (CAO)</small>
            </a>
            <a
              href={api.exportGlbUrl(
                project.id,
                levelId,
                project.history_cursor,
              )}
              download={`${project.name}.glb`}
            >
              GLB
              <small>scène 3D (Blender, viewers)</small>
            </a>
            <a href={api.exportArchiveUrl(project.id)} download>
              Archive (.zip)
              <small>
                sauvegarde complète : plan et historique, espace de travail,
                photos, fichiers joints, rendus
              </small>
            </a>
            <a href={api.exportJsonUrl(project.id)} download>
              JSON
              <small>
                le plan seul : historique et chemins des images, à garder pour
                soi
              </small>
            </a>
            <a href={api.exportJsonUrl(project.id, true)} download>
              JSON à partager
              <small>mêmes plans et mesures, sans historique ni chemins</small>
            </a>
          </div>
        )}
      </div>
      <button
        type="button"
        className="icon"
        onClick={copyLink}
        aria-label="Copier le lien"
        title={
          copied
            ? "Lien copié"
            : "Copier le lien de ce projet (pour qui a accès à ce PlanDesign)"
        }
      >
        <IconShare />
      </button>
    </div>
  );
}
