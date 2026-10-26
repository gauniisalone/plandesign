import {
  chosen,
  DENSITY_LABELS,
  intentOf,
  intentSummary,
  LIGHT_TIME_LABELS,
  MOOD_LABELS,
  STYLE_LABELS,
  SURFACE_LABELS,
  WARMTH_LABELS,
  ZONE_LABELS,
} from "../intent";
import {
  isPair,
  isStackKind,
  OPENING_LABELS,
  roomSideOf,
} from "../editor2d/openingSymbol";
import { useState } from "react";
import {
  type MeasurementCreate,
  type ProjectSettingsUpdate,
} from "../api/client";
import type { Op } from "../api/ops";
import { area, centimetres, decimal, metres, percent } from "../format";
import type {
  RoofWindow,
  Calibration,
  Camera,
  DerivedLevel,
  Furniture,
  Level,
  Opening,
  Post,
  Project,
  RoofPlane,
  Room,
  Source,
  Stairs,
  Truss,
  ValidationReport,
  Vertex,
  Violation,
  Wall,
} from "../api/types.gen";
import {
  formatLength,
  isCamera,
  isFurniture,
  isOpening,
  isRoof,
  isRoom,
  isWall,
  parseLength,
  SOURCE_LABEL,
  toDisplay,
  violationFor,
  type Selection,
  LEVEL_DEFAULT_NAMES,
  LEVEL_KIND_LABELS,
  ROOM_TYPE_LABELS,
  rooms,
  isRoofWindow,
  isPost,
  isStairs,
  isTruss,
  STAIR_LABELS,
} from "../model";
import { ViewActions, ViewSummary } from "./ViewActions";

interface Props {
  project: Project;
  level: Level;
  derived: DerivedLevel | null;
  report: ValidationReport | null;
  selection: Selection;
  lastCalibration: Calibration | null;
  busy: boolean;
  onOp: (op: Op) => void;
  onAddMeasurement: (m: MeasurementCreate) => void;
  onRemoveMeasurement: (id: string) => void;
  onCalibrate: (dryRun: boolean) => void;
  onSettings: (s: ProjectSettingsUpdate) => void;
  onAdoptView: (cameraId: string) => void;
  onRotateView: (cameraId: string) => void;
  /** Ouvre Paramètres avancés (raccourcis « combles », « calibration »). */
  onOpenAdvanced?: () => void;
  /** « Écrire à l'assistant à propos de cette pièce » (P5 : la consigne passe par lui). */
  onAskAssistant?: (roomId: string) => void;
}

/** Inspecteur contextuel : la fiche de ce qui est sélectionné ; sans sélection, le niveau et
 *  les vérifications. Catalogue, calibration détaillée et légende vivent ailleurs (outil
 *  Meuble, Paramètres avancés). */
export function Inspector(props: Props) {
  const { project, level, selection } = props;
  const units = project.units_display;
  return (
    <aside className="inspector">
      {selection.kind === "element" &&
        isCamera(level.elements[selection.id]) && (
          <CameraPanel
            camera={level.elements[selection.id] as Camera}
            project={project}
            violation={violationFor(props.report?.violations, selection.id)}
            busy={props.busy}
            onOp={props.onOp}
            onAdoptView={props.onAdoptView}
            onRotateView={props.onRotateView}
          />
        )}
      {selection.kind === "vertex" && level.vertices[selection.id] && (
        <VertexPanel
          vertex={level.vertices[selection.id]!}
          units={units}
          onOp={props.onOp}
        />
      )}
      {selection.kind === "element" && isWall(level.elements[selection.id]) && (
        <WallPanel
          wall={level.elements[selection.id] as Wall}
          derived={props.derived}
          project={project}
          busy={props.busy}
          onOp={props.onOp}
          onAddMeasurement={props.onAddMeasurement}
          onCalibrate={props.onCalibrate}
          onOpenAdvanced={props.onOpenAdvanced}
        />
      )}
      {selection.kind === "element" &&
        isOpening(level.elements[selection.id]) && (
          <OpeningPanel
            opening={level.elements[selection.id] as Opening}
            units={units}
            onOp={props.onOp}
            roomSign={roomSideOf(
              level,
              Object.values(props.derived?.rooms ?? {}).map((r) => r.polygon),
              level.elements[selection.id] as Opening,
            )}
          />
        )}
      {selection.kind === "element" && isRoof(level.elements[selection.id]) && (
        <RoofPanel
          roof={level.elements[selection.id] as RoofPlane}
          derived={props.derived}
          onOp={props.onOp}
        />
      )}
      {selection.kind === "element" &&
        isRoofWindow(level.elements[selection.id]) && (
          <RoofWindowPanel
            window={level.elements[selection.id] as RoofWindow}
            onOp={props.onOp}
          />
        )}
      {selection.kind === "element" &&
        isStairs(level.elements[selection.id]) && (
          <StairsPanel
            stairs={level.elements[selection.id] as Stairs}
            derived={props.derived}
            onOp={props.onOp}
          />
        )}
      {selection.kind === "element" && isPost(level.elements[selection.id]) && (
        <PostPanel
          post={level.elements[selection.id] as Post}
          height={level.height}
          onOp={props.onOp}
        />
      )}
      {selection.kind === "element" &&
        isTruss(level.elements[selection.id]) && (
          <TrussPanel
            truss={level.elements[selection.id] as Truss}
            derived={props.derived}
            onOp={props.onOp}
          />
        )}
      {selection.kind === "element" && isRoom(level.elements[selection.id]) && (
        <RoomPanel
          room={level.elements[selection.id] as Room}
          floor={
            Object.values(level.elements).find(
              (e) => e.type === "floor" && e.room === selection.id,
            ) ?? null
          }
          derived={props.derived}
          onOp={props.onOp}
          onAskAssistant={props.onAskAssistant}
        />
      )}
      {selection.kind === "element" &&
        isFurniture(level.elements[selection.id]) && (
          <FurniturePanel
            furniture={level.elements[selection.id] as Furniture}
            units={units}
            onOp={props.onOp}
          />
        )}
      {selection.kind === "none" && (
        <LevelPanel
          project={project}
          level={level}
          onOp={props.onOp}
          onOpenAdvanced={props.onOpenAdvanced}
        />
      )}
      <ReportPanel
        report={props.report}
        level={level}
        busy={props.busy}
        onOp={props.onOp}
        onAdoptView={props.onAdoptView}
        onRotateView={props.onRotateView}
      />
    </aside>
  );
}

function Badge({ source, confidence }: { source: Source; confidence: number }) {
  return (
    <span
      className={`badge source-${source}`}
      title={`confiance ${percent(confidence)}`}
    >
      {SOURCE_LABEL[source]} · {percent(confidence)}
    </span>
  );
}

/** Champ numérique validé au blur ou sur Entrée. */
function NumberField({
  label,
  value,
  onCommit,
  step = 0.01,
  suffix,
  disabled,
}: {
  label: string;
  value: number;
  onCommit: (v: number) => void;
  step?: number;
  suffix?: string;
  disabled?: boolean;
}) {
  const [text, setText] = useState(String(value));
  // Resynchronise le brouillon quand la valeur du modèle change (motif « valeur précédente »).
  const [prev, setPrev] = useState(value);
  if (prev !== value) {
    setPrev(value);
    setText(String(value));
  }
  const commit = () => {
    const v = Number(text.replace(",", "."));
    if (Number.isFinite(v) && v !== value) onCommit(v);
    else setText(String(value));
  };
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        step={step}
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
      />
      {suffix && <em>{suffix}</em>}
    </label>
  );
}

function VertexPanel({
  vertex,
  units,
  onOp,
}: {
  vertex: Vertex;
  units: Project["units_display"];
  onOp: (op: Op) => void;
}) {
  const move = (x: number, y: number) =>
    onOp({ op: "move_vertex", vertex_id: vertex.id, to: { x, y } });
  return (
    <section>
      <h2>Sommet</h2>
      <Badge source={vertex.source} confidence={vertex.confidence} />
      <NumberField
        label="X"
        value={toDisplay(vertex.position.x, units)}
        suffix={units}
        disabled={vertex.locked}
        onCommit={(v) =>
          move(
            parseLength(String(v), units) ?? vertex.position.x,
            vertex.position.y,
          )
        }
      />
      <NumberField
        label="Y"
        value={toDisplay(vertex.position.y, units)}
        suffix={units}
        disabled={vertex.locked}
        onCommit={(v) =>
          move(
            vertex.position.x,
            parseLength(String(v), units) ?? vertex.position.y,
          )
        }
      />
      <label className="field">
        <span>Verrouillé</span>
        <input
          type="checkbox"
          checked={vertex.locked}
          onChange={(e) =>
            onOp({
              op: "update_vertex",
              vertex_id: vertex.id,
              fields: { locked: e.target.checked },
            })
          }
        />
      </label>
      <button
        className="danger"
        onClick={() => onOp({ op: "remove_vertex", vertex_id: vertex.id })}
      >
        Supprimer le sommet et ses murs
      </button>
    </section>
  );
}

function WallPanel({
  wall,
  derived,
  project,
  busy,
  onOp,
  onAddMeasurement,
  onCalibrate,
  onOpenAdvanced,
}: {
  wall: Wall;
  derived: DerivedLevel | null;
  project: Project;
  busy: boolean;
  onOp: (op: Op) => void;
  onAddMeasurement: (m: MeasurementCreate) => void;
  onCalibrate: (dryRun: boolean) => void;
  onOpenAdvanced?: () => void;
}) {
  const units = project.units_display;
  const length = derived?.walls[wall.id]?.length_m ?? 0;
  const [measured, setMeasured] = useState("");
  const existing = Object.values(project.measurements).find(
    (m) => m.element_id === wall.id,
  );
  return (
    <section>
      <h2>Mur</h2>
      <Badge source={wall.source} confidence={wall.confidence} />
      <FinishField element={wall} fallback="plaster" onOp={onOp} />
      {wall.uncertainty.sigma != null && (
        <p className="muted">± {formatLength(wall.uncertainty.sigma, units)}</p>
      )}
      <NumberField
        label="Longueur"
        value={toDisplay(length, units)}
        suffix={units}
        disabled={wall.locked}
        onCommit={(v) => {
          const m = parseLength(String(v), units);
          if (m && m > 0)
            onOp({
              op: "set_wall_length",
              wall_id: wall.id,
              length: m,
              anchor: "start",
            });
        }}
      />
      <NumberField
        label="Épaisseur"
        value={toDisplay(wall.thickness, units)}
        suffix={units}
        disabled={wall.locked}
        onCommit={(v) => {
          const m = parseLength(String(v), units);
          if (m && m > 0)
            onOp({
              op: "update_element",
              element_id: wall.id,
              fields: { thickness: m },
            });
        }}
      />
      <label className="field">
        <span>Verrouillé</span>
        <input
          type="checkbox"
          checked={wall.locked}
          onChange={(e) =>
            onOp({
              op: "update_element",
              element_id: wall.id,
              fields: { locked: e.target.checked },
            })
          }
        />
      </label>
      <h3>Mesure connue</h3>
      {existing ? (
        <>
          <p className="muted">
            Ce mur a une mesure : {formatLength(existing.value, units)}.
          </p>
          <div className="row">
            <button
              className="primary"
              disabled={busy}
              onClick={() => onCalibrate(false)}
              title="Met tout le plan à l'échelle de cette mesure (annulable)"
            >
              Calibrer le plan
            </button>
            {onOpenAdvanced && (
              <button
                className="link"
                onClick={onOpenAdvanced}
                title="Prévisualiser, facteur d'échelle, toutes les mesures : Paramètres avancés › Calibration"
              >
                détails
              </button>
            )}
          </div>
        </>
      ) : (
        <div className="row">
          <input
            type="text"
            placeholder={`longueur réelle (${units})`}
            value={measured}
            onChange={(e) => setMeasured(e.target.value)}
          />
          <button
            onClick={() => {
              const m = parseLength(measured, units);
              if (m && m > 0) {
                onAddMeasurement({
                  kind: "wall_length",
                  value: m,
                  element_id: wall.id,
                });
                setMeasured("");
              }
            }}
          >
            Ajouter
          </button>
        </div>
      )}
      <button
        className="danger"
        onClick={() => onOp({ op: "remove_element", element_id: wall.id })}
      >
        Supprimer le mur
      </button>
    </section>
  );
}

function OpeningPanel({
  opening,
  units,
  onOp,
  roomSign = 1,
}: {
  opening: Opening;
  units: Project["units_display"];
  onOp: (op: Op) => void;
  /** Côté de la pièce (E5c) : −1 si elle est à droite du mur ; « intérieur » = vers elle. */
  roomSign?: number;
}) {
  const inner = roomSign < 0 ? "outward" : "inward";
  const outer = roomSign < 0 ? "inward" : "outward";
  const set = (fields: Record<string, unknown>) =>
    onOp({ op: "update_element", element_id: opening.id, fields });
  const len = (
    label: string,
    key: "offset" | "width" | "height" | "sill_height",
  ) => (
    <NumberField
      label={label}
      value={toDisplay(opening[key], units)}
      suffix={units}
      onCommit={(v) => {
        const m = parseLength(String(v), units);
        if (m != null && m >= 0) set({ [key]: m });
      }}
    />
  );
  return (
    <section>
      <h2>{OPENING_LABELS[opening.kind] ?? "Ouverture"}</h2>
      <Badge source={opening.source} confidence={opening.confidence} />
      {len("Décalage", "offset")}
      {len("Largeur", "width")}
      {len("Hauteur", "height")}
      {(opening.kind === "window" || opening.kind === "sliding_window") &&
        len("Allège", "sill_height")}
      {opening.kind === "french_window" && (
        <label className="field">
          <span>Vantaux</span>
          <select
            value={opening.leaves ?? 1}
            onChange={(e) => set({ leaves: Number(e.target.value) })}
          >
            <option value={1}>un</option>
            <option value={2}>deux</option>
          </select>
        </label>
      )}
      {isPair(opening) && (
        <label className="field">
          <span>Vantaux</span>
          <select
            value={String(Math.round((opening.split ?? 0.5) * 100))}
            onChange={(e) => set({ split: Number(e.target.value) / 100 })}
          >
            <option value="50">égaux</option>
            <option value="33">1/3 – 2/3</option>
            <option value="67">2/3 – 1/3</option>
          </select>
        </label>
      )}
      {opening.swing && isStackKind(opening.kind) && (
        <>
          <label className="field">
            <span>S'efface vers</span>
            <select
              value={opening.swing.hinge}
              onChange={(e) =>
                set({ swing: { ...opening.swing, hinge: e.target.value } })
              }
            >
              <option value="left">la gauche</option>
              <option value="right">la droite</option>
            </select>
          </label>
          {opening.kind === "sliding_door" && (
            <label className="field">
              <span>Rail côté</span>
              <select
                value={opening.swing.direction}
                onChange={(e) =>
                  set({
                    swing: { ...opening.swing, direction: e.target.value },
                  })
                }
              >
                <option value={inner}>intérieur</option>
                <option value={outer}>extérieur</option>
              </select>
            </label>
          )}
        </>
      )}
      {opening.swing && !isStackKind(opening.kind) && (
        <>
          <label className="field">
            <span>{isPair(opening) ? "Vantail principal" : "Charnières"}</span>
            <select
              value={opening.swing.hinge}
              onChange={(e) =>
                set({ swing: { ...opening.swing, hinge: e.target.value } })
              }
            >
              <option value="left">gauche</option>
              <option value="right">droite</option>
            </select>
          </label>
          <label className="field">
            <span>Ouverture</span>
            <select
              value={opening.swing.direction}
              onChange={(e) =>
                set({ swing: { ...opening.swing, direction: e.target.value } })
              }
            >
              <option value={inner}>vers l'intérieur</option>
              <option value={outer}>vers l'extérieur</option>
            </select>
          </label>
        </>
      )}
      <button
        className="danger"
        onClick={() => onOp({ op: "remove_element", element_id: opening.id })}
      >
        Supprimer
      </button>
    </section>
  );
}

/** Un meuble du catalogue posé dans le plan : dimensions dans la plage de l'asset, position,
 *  rotation, « à conserver » (immobile pour les propositions d'aménagement). */
function FurniturePanel({
  furniture,
  units,
  onOp,
}: {
  furniture: Furniture;
  units: Project["units_display"];
  onOp: (op: Op) => void;
}) {
  const set = (fields: Record<string, unknown>) =>
    onOp({ op: "update_element", element_id: furniture.id, fields });
  const dim = (label: string, key: "width" | "depth" | "height") => (
    <NumberField
      label={label}
      value={toDisplay(furniture.bbox[key], units)}
      suffix={units}
      onCommit={(v) => {
        const m = parseLength(String(v), units);
        if (m != null && m > 0) set({ bbox: { ...furniture.bbox, [key]: m } });
      }}
    />
  );
  const pos = (label: string, key: "x" | "y") => (
    <NumberField
      label={label}
      value={toDisplay(furniture.transform.position[key], units)}
      suffix={units}
      onCommit={(v) => {
        const m = parseLength(String(v), units);
        if (m != null)
          set({
            transform: {
              ...furniture.transform,
              position: { ...furniture.transform.position, [key]: m },
            },
          });
      }}
    />
  );
  return (
    <section>
      <h2>Meuble : {furniture.name ?? furniture.category}</h2>
      <Badge source={furniture.source} confidence={furniture.confidence} />
      <p className="muted">
        Catalogue « {furniture.asset_id} » · {furniture.category}
      </p>
      <label className="field">
        <span>Nom</span>
        <input
          type="text"
          value={furniture.name ?? ""}
          onChange={(e) => set({ name: e.target.value || null })}
        />
      </label>
      {furniture.category === "bed_builtin" ? (
        <>
          <label className="field">
            <span>Couchage</span>
            <select
              aria-label="Couchage"
              value={String(Math.round(furniture.bbox.width * 100))}
              onChange={(e) =>
                set({
                  bbox: {
                    ...furniture.bbox,
                    width: Number(e.target.value) / 100,
                  },
                })
              }
            >
              {[90, 140, 160].map((cm) => (
                <option key={cm} value={cm}>
                  {cm} × 200 cm
                </option>
              ))}
            </select>
          </label>
          <p className="muted">
            Lit encastré sous pente : coffre à tiroirs, tête vers la jambette.
            Il faut 0,90 m libres au-dessus du matelas, à l&apos;oreiller.
          </p>
        </>
      ) : (
        <>
          {dim("Largeur", "width")}
          {dim("Profondeur", "depth")}
          {dim("Hauteur", "height")}
        </>
      )}
      {pos("Position x", "x")}
      {pos("Position y", "y")}
      <NumberField
        label="Rotation"
        value={Math.round((furniture.transform.yaw * 180) / Math.PI)}
        step={15}
        suffix="°"
        onCommit={(deg) =>
          set({
            transform: { ...furniture.transform, yaw: (deg * Math.PI) / 180 },
          })
        }
      />
      <label className="check">
        <input
          type="checkbox"
          checked={furniture.preserve}
          onChange={(e) => set({ preserve: e.target.checked })}
        />
        À conserver (les propositions d'aménagement ne le déplacent pas)
      </label>
      <button
        className="danger"
        onClick={() => onOp({ op: "remove_element", element_id: furniture.id })}
      >
        Supprimer
      </button>
    </section>
  );
}

function RoomPanel({
  room,
  floor,
  derived,
  onOp,
  onAskAssistant,
}: {
  room: Room;
  /** Sol de la pièce : sa matière se choisit ici (D2). */
  floor: { id: string; metadata?: Record<string, unknown> } | null;
  derived: DerivedLevel | null;
  onOp: (op: Op) => void;
  onAskAssistant?: (roomId: string) => void;
}) {
  const d = derived?.rooms[room.id];
  const [name, setName] = useState(room.name ?? "");
  const [prevName, setPrevName] = useState(room.name ?? "");
  if (prevName !== (room.name ?? "")) {
    setPrevName(room.name ?? "");
    setName(room.name ?? "");
  }
  return (
    <section>
      <h2>Pièce</h2>
      <Badge source={room.source} confidence={room.confidence} />
      {floor && (
        <FinishField
          element={floor}
          fallback="wood_floor"
          label="Sol"
          onOp={onOp}
        />
      )}
      <label className="field">
        <span>Nom</span>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() =>
            name !== (room.name ?? "") &&
            onOp({
              op: "update_element",
              element_id: room.id,
              fields: { name },
            })
          }
        />
      </label>
      <label className="field">
        <span>Type</span>
        <select
          value={room.room_type ?? ""}
          onChange={(e) =>
            onOp({
              op: "update_element",
              element_id: room.id,
              fields: { room_type: e.target.value || null },
            })
          }
        >
          <option value="">—</option>
          {Object.entries(ROOM_TYPE_LABELS).map(([t, label]) => (
            <option key={t} value={t}>
              {label}
            </option>
          ))}
          {room.room_type && !(room.room_type in ROOM_TYPE_LABELS) && (
            <option value={room.room_type}>{room.room_type}</option>
          )}
        </select>
      </label>
      <AmbianceField room={room} onOp={onOp} onAskAssistant={onAskAssistant} />
      {d && (
        <p>
          Surface intérieure : <strong>{area(d.area_m2, 2)}</strong> · périmètre{" "}
          {metres(d.perimeter_m)}
          {d.usable_area_m2 != null && d.usable_area_m2 < d.area_m2 - 1e-6
            ? ` · ${area(d.usable_area_m2, 2)} à plus de 1,80 m sous pente`
            : ""}
        </p>
      )}
      <button
        className="danger"
        onClick={() => onOp({ op: "remove_element", element_id: room.id })}
      >
        Supprimer la pièce
      </button>
    </section>
  );
}

/** Pente de toit : hauteur à l'égout, inclinaison et hauteur au faîtage (déduite de la
 *  course) se corrigent ici ; la ligne d'égout suit ses propres points. */
function RoofPanel({
  roof,
  derived,
  onOp,
}: {
  roof: RoofPlane;
  derived: DerivedLevel | null;
  onOp: (op: Op) => void;
}) {
  const d = derived?.roofs[roof.id];
  const run = d?.run_m ?? 0;
  const pitchDeg = (roof.pitch * 180) / Math.PI;
  const ridge = roof.eave_height + run * Math.tan(roof.pitch);
  const update = (fields: Record<string, number>) =>
    onOp({ op: "update_element", element_id: roof.id, fields });
  return (
    <section>
      <h2>Pente : {roof.name ?? "versant"}</h2>
      <Badge source={roof.source} confidence={roof.confidence} />
      <FinishField element={roof} fallback="wood_boards" onOp={onOp} />
      <TimberField roof={roof} onOp={onOp} />
      <NumberField
        label="Hauteur à l'égout"
        value={round2(roof.eave_height)}
        suffix="m"
        step={0.05}
        onCommit={(v) => v >= 0 && update({ eave_height: v })}
      />
      <NumberField
        label="Inclinaison"
        value={Math.round(pitchDeg * 10) / 10}
        suffix="°"
        step={1}
        onCommit={(v) =>
          v > 0 && v < 90 && update({ pitch: (v * Math.PI) / 180 })
        }
      />
      {run > 0 && (
        <NumberField
          label="Hauteur au faîtage"
          value={round2(ridge)}
          suffix="m"
          step={0.05}
          onCommit={(v) =>
            v > roof.eave_height &&
            update({ pitch: Math.atan((v - roof.eave_height) / run) })
          }
        />
      )}
      <p className="muted">
        {run > 0 ? `Course ${metres(run)} · ` : ""}
        la ligne d'égout se déplace par ses murs.
      </p>
      {d && run > 0 && (
        <button
          onClick={() => {
            // Velux standard (78 × 98 cm) au milieu de l'égout, au tiers bas de la pente.
            const [a, b] = d.eave as [
              { x: number; y: number },
              { x: number; y: number },
            ];
            const eave = Math.hypot(b.x - a.x, b.y - a.y);
            const slope = run / Math.cos(roof.pitch);
            onOp({
              op: "add_roof_window",
              roof: roof.id,
              offset: round2(Math.max(0, (eave - 0.78) / 2)),
              distance: round2(
                Math.max(0, Math.min(slope - 0.98, slope * 0.3)),
              ),
            });
          }}
          title="Pose un velux de 78 × 98 cm dans cette pente ; réglez-le ensuite dans sa fiche"
        >
          Ajouter un velux
        </button>
      )}
    </section>
  );
}

/** Velux (D1) : cotes du châssis et position dans sa pente, modifiables ; supprimable. */
function RoofWindowPanel({
  window,
  onOp,
}: {
  window: RoofWindow;
  onOp: (op: Op) => void;
}) {
  const update = (fields: Record<string, number>) =>
    onOp({ op: "update_element", element_id: window.id, fields });
  return (
    <section>
      <h2>Velux : {window.name ?? "fenêtre de toit"}</h2>
      <Badge source={window.source} confidence={window.confidence} />
      <NumberField
        label="Largeur"
        value={round2(window.width)}
        suffix="m"
        onCommit={(v) => v > 0 && update({ width: v })}
      />
      <NumberField
        label="Longueur (le long de la pente)"
        value={round2(window.length)}
        suffix="m"
        onCommit={(v) => v > 0 && update({ length: v })}
      />
      <NumberField
        label="Depuis le début de l'égout"
        value={round2(window.offset)}
        suffix="m"
        onCommit={(v) => v >= 0 && update({ offset: v })}
      />
      <NumberField
        label="Depuis l'égout (le long de la pente)"
        value={round2(window.distance)}
        suffix="m"
        onCommit={(v) => v >= 0 && update({ distance: v })}
      />
      <p className="muted">
        La lumière du ciel entre par le velux dans la vue 3D et le rendu.
      </p>
      <button
        className="danger"
        onClick={() => onOp({ op: "remove_element", element_id: window.id })}
      >
        Supprimer
      </button>
    </section>
  );
}

/** Matières de rendu (D2) : finition de l'élément, lue par le rendu (texture CC0 installée, sinon
 *  matière dessinée). Gardée dans `metadata.finish`. */
const FINISH_LABELS: Record<string, string> = {
  plaster: "Enduit",
  wood_floor: "Parquet clair (pin)",
  wood_dark: "Parquet ancien",
  wood_boards: "Lambris (chêne)",
  stone: "Pierre",
  stone_rubble: "Moellons",
  concrete: "Béton",
  limewash: "Chaux",
  concrete_floor: "Béton de sol",
  oak_dark: "Chêne foncé",
  painted_wood: "Bois peint blanc",
};

function FinishField({
  element,
  fallback,
  label = "Matière",
  onOp,
}: {
  element: { id: string; metadata?: Record<string, unknown> };
  fallback: string;
  label?: string;
  onOp: (op: Op) => void;
}) {
  const current =
    (element.metadata?.["finish"] as string | undefined) ?? fallback;
  return (
    <label className="field">
      <span>{label}</span>
      <select
        aria-label={label}
        value={current}
        onChange={(e) =>
          onOp({
            op: "update_element",
            element_id: element.id,
            fields: {
              // Choisie à la main : prioritaire sur l'ambiance (V1).
              metadata: {
                ...(element.metadata ?? {}),
                finish: e.target.value,
                finish_source: "user_defined",
              },
            },
          })
        }
      >
        {Object.entries(FINISH_LABELS).map(([k, v]) => (
          <option key={k} value={k}>
            {v}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Charpente apparente sous la pente (C1) : chevrons, pannes, faîtière ; avec ou sans,
 *  et l'entraxe des chevrons. Gardée dans `metadata.timber` ; rendu seulement. */
function TimberField({
  roof,
  onOp,
}: {
  roof: RoofPlane;
  onOp: (op: Op) => void;
}) {
  const raw = (roof.metadata?.["timber"] ?? {}) as {
    enabled?: boolean;
    spacing?: number;
  };
  const enabled = raw.enabled !== false;
  const spacing = raw.spacing ?? 0.6;
  const save = (next: { enabled: boolean; spacing: number }) =>
    onOp({
      op: "update_element",
      element_id: roof.id,
      fields: { metadata: { ...(roof.metadata ?? {}), timber: next } },
    });
  return (
    <>
      <label className="check">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => save({ enabled: e.target.checked, spacing })}
        />
        Charpente apparente
      </label>
      {enabled && (
        <label className="field">
          <span>Chevrons</span>
          <select
            aria-label="Entraxe des chevrons"
            value={spacing}
            onChange={(e) => save({ enabled, spacing: Number(e.target.value) })}
          >
            <option value={0.45}>serrés (45 cm)</option>
            <option value={0.6}>normaux (60 cm)</option>
            <option value={0.8}>espacés (80 cm)</option>
          </select>
        </label>
      )}
    </>
  );
}

/** Dernière consigne de la pièce (P5) : rappelée telle qu'écrite, avec qui l'a lue ; une
 *  nouvelle consigne s'écrit à l'assistant, avec cette pièce en contexte. */
function InstructionLink({
  room,
  onAskAssistant,
}: {
  room: Room;
  onAskAssistant?: (roomId: string) => void;
}) {
  const last = room.intent?.instruction;
  const by = room.intent?.translated_by ?? null;
  return (
    <div className="instruction-box">
      {last && (
        <p className="muted">
          Dernière consigne : « {last} »
          {by &&
            ` · lue par ${by === "phrases" ? "les phrases simples" : by.replace("llm:", "")}`}
        </p>
      )}
      {onAskAssistant && (
        <button
          type="button"
          className="link"
          onClick={() => onAskAssistant(room.id)}
          title="Ouvre l'assistant avec cette pièce en contexte : ambiance, meubles, aménagements"
        >
          Écrire à l'assistant à propos de cette pièce
        </button>
      )}
    </div>
  );
}

/** Fiche Ambiance repliée sous la conversation de l'assistant (P5) : la phrase qui la résume,
 *  et les réglages champ par champ. */
export function AmbianceDetails({
  room,
  onOp,
}: {
  room: Room;
  onOp: (op: Op) => void;
}) {
  return (
    <details className="assistant-ambiance">
      <summary>
        Ambiance de {room.name ?? "la pièce"} : {intentSummary(intentOf(room))}
      </summary>
      <AmbianceField room={room} onOp={onOp} />
    </details>
  );
}

/** Fiche « Ambiance » de la pièce (V1) : son intention de design, lisible en une phrase et
 *  champ par champ ; « choisi » = l'utilisateur l'a dit (prioritaire, jamais réécrit par
 *  l'automatique), « déduit » = calculé. En compact (fiche du niveau) : le style seul. */
function AmbianceField({
  room,
  onOp,
  onAskAssistant,
  compact = false,
}: {
  room: Room;
  onOp: (op: Op) => void;
  onAskAssistant?: (roomId: string) => void;
  compact?: boolean;
}) {
  const intent = intentOf(room);
  const set = (fields: Record<string, unknown>) =>
    onOp({ op: "set_intent", room_id: room.id, fields });
  const tag = (field: string) => (
    <span
      className={`intent-tag${chosen(intent, field) ? " chosen" : ""}`}
      title={
        chosen(intent, field)
          ? "Choisi par vous : une relance automatique n'y touche pas"
          : "Déduit : le calcul peut l'ajuster"
      }
    >
      {chosen(intent, field) ? "choisi" : "déduit"}
    </span>
  );
  const style = (
    <label className="field">
      <span>Ambiance {tag("style")}</span>
      <select
        aria-label={`Ambiance : ${room.name ?? "pièce"}`}
        value={intent.style}
        onChange={(e) => set({ style: e.target.value })}
      >
        {Object.entries(STYLE_LABELS).map(([k, v]) => (
          <option key={k} value={k}>
            {v}
          </option>
        ))}
      </select>
    </label>
  );
  if (compact) return style;
  const toggle = (list: string[], value: string, max: number) =>
    list.includes(value)
      ? list.filter((v) => v !== value)
      : [...list, value].slice(-max);
  return (
    <div className="ambiance-card" aria-label="Fiche Ambiance">
      <p className="intent-summary">{intentSummary(intent)}</p>
      <InstructionLink room={room} onAskAssistant={onAskAssistant} />
      {style}
      <div className="field chips-field">
        <span>Humeur {tag("mood")}</span>
        <div className="chips" role="group" aria-label="Humeur (deux au plus)">
          {Object.entries(MOOD_LABELS).map(([k, v]) => (
            <button
              key={k}
              type="button"
              aria-pressed={intent.mood.includes(k as never)}
              onClick={() => set({ mood: toggle(intent.mood, k, 2) })}
            >
              {v}
            </button>
          ))}
        </div>
      </div>
      <label className="field">
        <span>Décor {tag("decor_density")}</span>
        <select
          aria-label="Densité du décor"
          value={intent.decor_density}
          onChange={(e) => set({ decor_density: e.target.value })}
        >
          {Object.entries(DENSITY_LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      <div className="field">
        <span>Lumière {tag("light")}</span>
        <div className="pair">
          <select
            aria-label="Moment de la lumière"
            value={intent.light.time}
            onChange={(e) =>
              set({ light: { ...intent.light, time: e.target.value } })
            }
          >
            {Object.entries(LIGHT_TIME_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <select
            aria-label="Chaleur de la lumière"
            value={intent.light.warmth}
            onChange={(e) =>
              set({ light: { ...intent.light, warmth: e.target.value } })
            }
          >
            {Object.entries(WARMTH_LABELS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="field chips-field">
        <span>Zones {tag("zones")}</span>
        <div className="chips" role="group" aria-label="Zones voulues">
          {Object.entries(ZONE_LABELS).map(([k, v]) => (
            <button
              key={k}
              type="button"
              aria-pressed={intent.zones.includes(k as never)}
              onClick={() => set({ zones: toggle(intent.zones, k, 5) })}
            >
              {v}
            </button>
          ))}
        </div>
      </div>
      <div className="field chips-field">
        <span>Vues {tag("views")}</span>
        <div className="chips" role="group" aria-label="Vues automatiques">
          {(
            [
              ["overall", "ensemble"],
              ["zone", "zone"],
              ["detail", "détail"],
            ] as const
          ).map(([k, v]) => (
            <button
              key={k}
              type="button"
              aria-pressed={intent.views[k]}
              onClick={() =>
                set({ views: { ...intent.views, [k]: !intent.views[k] } })
              }
            >
              {v}
            </button>
          ))}
          <select
            aria-label="Format des vues"
            value={intent.views.aspect}
            onChange={(e) =>
              set({ views: { ...intent.views, aspect: e.target.value } })
            }
          >
            <option value="4:3">4:3</option>
            <option value="4:5">4:5</option>
          </select>
        </div>
      </div>
      <p className="muted intent-more">
        Palette :{" "}
        {intent.palette.length ? intent.palette.join(", ") : "selon le style"} ·
        matières :{" "}
        {Object.entries(intent.materials)
          .filter(([, v]) => v)
          .map(([k, v]) => `${SURFACE_LABELS[k] ?? k} ${v}`)
          .join(", ") || "selon le style"}
      </p>
    </div>
  );
}

const cm = (m: number | null | undefined, fallback: number) =>
  Math.round((m ?? fallback) * 100);

/** KAN-94 : poteau seul : section (5 à 40 cm), orientation, haut (automatique : jusqu'à la
 *  charpente au-dessus, sinon sous la pente ou au plafond). */
function PostPanel({
  post,
  height,
  onOp,
}: {
  post: Post;
  height: number;
  onOp: (op: Op) => void;
}) {
  const update = (fields: Record<string, unknown>) =>
    onOp({ op: "update_element", element_id: post.id, fields });
  const section = (v: number) => v >= 5 && v <= 40;
  return (
    <section>
      <h2>Poteau : {post.name ?? "Poteau"}</h2>
      <Badge source={post.source} confidence={post.confidence} />
      <NumberField
        label="Largeur"
        value={cm(post.width, 0.15)}
        suffix="cm"
        step={1}
        onCommit={(v) => section(v) && update({ width: v / 100 })}
      />
      <NumberField
        label="Profondeur"
        value={cm(post.depth, 0.12)}
        suffix="cm"
        step={1}
        onCommit={(v) => section(v) && update({ depth: v / 100 })}
      />
      <NumberField
        label="Orientation"
        value={Math.round((((post.yaw ?? 0) * 180) / Math.PI) * 10) / 10}
        suffix="°"
        step={15}
        onCommit={(v) => update({ yaw: (v * Math.PI) / 180 })}
      />
      <label className="check">
        <input
          type="checkbox"
          checked={post.top == null}
          onChange={(e) =>
            update({
              top: e.target.checked ? null : Math.round(height * 100) / 100,
            })
          }
        />
        Hauteur automatique (jusqu'à la charpente ou au plafond)
      </label>
      {post.top != null && (
        <NumberField
          label="Haut du poteau"
          value={round2(post.top)}
          suffix="m"
          step={0.05}
          onCommit={(v) => v > 0 && v <= height && update({ top: v })}
        />
      )}
      <p>
        Section 5 à 40 cm ; glisser le poteau le déplace (Alt : sans accroche au
        mur).
      </p>
    </section>
  );
}

/** KAN-94 : ferme intermédiaire : distance depuis l'un ou l'autre pignon, écartement des
 *  poteaux, hauteur sous poutre ; ce que le serveur a calculé (poutre abaissée, hors faîtage). */
function TrussPanel({
  truss,
  derived,
  onOp,
}: {
  truss: Truss;
  derived: DerivedLevel | null;
  onOp: (op: Op) => void;
}) {
  const update = (fields: Record<string, unknown>) =>
    onOp({ op: "update_element", element_id: truss.id, fields });
  const gables = derived?.ridge?.gables ?? [];
  const [from, setFrom] = useState<string | null>(null);
  const gable = gables.find((g) => g.wall_id === from) ?? gables[0];
  const d = derived?.trusses?.[truss.id];
  const distance = gable ? d?.distances?.[gable.wall_id] : undefined;
  return (
    <section>
      <h2>Ferme intermédiaire : {truss.name ?? "Ferme"}</h2>
      <Badge source={truss.source} confidence={truss.confidence} />
      {d?.problem && (
        <p className="truss-problem" role="alert">
          {d.problem.charAt(0).toUpperCase() + d.problem.slice(1)} : à replacer
          ou à supprimer.
        </p>
      )}
      {d?.lowered && d.beam_height != null && (
        <p className="truss-problem">
          Poutre abaissée à {metres(d.beam_height)} : la pente passe plus bas
          que la hauteur demandée.
        </p>
      )}
      {gable && (
        <>
          <label className="field">
            <span>Mesurée depuis</span>
            <select
              aria-label="Pignon de référence"
              value={gable.wall_id}
              onChange={(e) => setFrom(e.target.value)}
            >
              {gables.map((g) => (
                <option key={g.wall_id} value={g.wall_id}>
                  {g.name}
                </option>
              ))}
            </select>
          </label>
          {distance != null && (
            <NumberField
              label="Distance au pignon"
              value={round2(distance)}
              suffix="m"
              step={0.05}
              onCommit={(v) =>
                v >= 0 && update({ distance: v, from_wall: gable.wall_id })
              }
            />
          )}
        </>
      )}
      <NumberField
        label="Écartement des poteaux"
        value={round2(truss.spacing ?? 1)}
        suffix="m"
        step={0.05}
        onCommit={(v) => v > 0 && v <= 6 && update({ spacing: v })}
      />
      <NumberField
        label="Hauteur sous poutre"
        value={round2(truss.beam_height ?? 2)}
        suffix="m"
        step={0.05}
        onCommit={(v) => v > 0 && v <= 6 && update({ beam_height: v })}
      />
      <p>
        Poteaux {cm(truss.post_width, 0.15)} × {cm(truss.post_depth, 0.12)} cm,
        poutre {cm(truss.beam_width, 0.15)} × {cm(truss.beam_depth, 0.2)} cm ;
        glisser la ferme la déplace le long du faîtage.
      </p>
    </section>
  );
}

/** Cage d'escalier (S1) : type, largeur, sens, garde-corps, hauteur d'étage ; marches,
 *  giron et échappée calculés par le serveur. */
function StairsPanel({
  stairs,
  derived,
  onOp,
}: {
  stairs: Stairs;
  derived: DerivedLevel | null;
  onOp: (op: Op) => void;
}) {
  const d = derived?.stairs?.[stairs.id];
  const update = (fields: Record<string, unknown>) =>
    onOp({ op: "update_element", element_id: stairs.id, fields });
  const kind = stairs.kind ?? "straight";
  return (
    <section>
      <h2>Escalier : {stairs.name ?? STAIR_LABELS[kind]}</h2>
      <Badge source={stairs.source} confidence={stairs.confidence} />
      <label className="field">
        <span>Type</span>
        <select
          aria-label="Type d'escalier"
          value={kind}
          onChange={(e) => update({ kind: e.target.value })}
        >
          {Object.entries(STAIR_LABELS).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      <NumberField
        label="Largeur"
        value={round2(stairs.width ?? 0.9)}
        suffix="m"
        step={0.05}
        onCommit={(v) => v >= 0.7 && v <= 1.2 && update({ width: v })}
      />
      {kind !== "straight" && (
        <label className="field">
          <span>Tourne vers</span>
          <select
            aria-label="Sens de rotation"
            value={stairs.turn ?? "left"}
            onChange={(e) => update({ turn: e.target.value })}
          >
            <option value="left">la gauche</option>
            <option value="right">la droite</option>
          </select>
        </label>
      )}
      <NumberField
        label="Hauteur d'étage"
        value={round2(stairs.rise ?? 2.7)}
        suffix="m"
        step={0.05}
        onCommit={(v) => v > 0.5 && v <= 6 && update({ rise: v })}
      />
      <label className="check">
        <input
          type="checkbox"
          checked={stairs.railing ?? true}
          onChange={(e) => update({ railing: e.target.checked })}
        />
        Garde-corps sur les côtés ouverts (1,00 m)
      </label>
      <NumberField
        label="Sens de montée"
        value={Math.round((((stairs.yaw ?? 0) * 180) / Math.PI) * 10) / 10}
        suffix="°"
        step={15}
        onCommit={(v) => update({ yaw: (v * Math.PI) / 180 })}
      />
      {d && (
        <p>
          {d.risers} marches de {centimetres(d.riser_m, 1)}, giron{" "}
          {centimetres(d.going_m)} (Blondel {centimetres(d.blondel_m)})
          {d.headroom_m != null &&
            ` · échappée ${metres(d.headroom_m)}${d.headroom_m < 1.9 ? " (moins de 1,90 m)" : ""}`}
        </p>
      )}
      <button
        className="danger"
        onClick={() => onOp({ op: "remove_element", element_id: stairs.id })}
      >
        Supprimer l'escalier
      </button>
    </section>
  );
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

function LevelPanel({
  project,
  level,
  onOp,
  onOpenAdvanced,
}: {
  project: Project;
  level: Level;
  onOp: (op: Op) => void;
  onOpenAdvanced?: () => void;
}) {
  const levelRooms = rooms(level);
  return (
    <section>
      <h2>Niveau : {level.name}</h2>
      {level.kind && <p className="muted">{LEVEL_KIND_LABELS[level.kind]}</p>}
      <p className="muted">
        Nom et hauteur sous plafond : dans le plan, en haut à gauche.
      </p>
      <label className="field">
        <span>Niveau</span>
        <select
          aria-label="Sorte de niveau"
          value={level.kind ?? ""}
          onChange={(e) => {
            const kind = e.target.value || null;
            // Le nom suit la sorte tant qu'il est un nom par défaut (jamais un nom choisi).
            const renamed =
              kind && Object.values(LEVEL_DEFAULT_NAMES).includes(level.name)
                ? { name: LEVEL_DEFAULT_NAMES[kind]! }
                : {};
            onOp({ op: "update_level", fields: { kind, ...renamed } });
          }}
        >
          <option value="">— à préciser</option>
          {Object.entries(LEVEL_KIND_LABELS).map(([k, label]) => (
            <option key={k} value={k}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {levelRooms.map((r) => (
        <AmbianceField key={`ambiance-${r.id}`} room={r} onOp={onOp} compact />
      ))}
      {levelRooms.map((r) => (
        <label className="field" key={r.id}>
          <span>{r.name ?? "Pièce"}</span>
          <select
            aria-label={`Type de pièce : ${r.name ?? "pièce"}`}
            value={r.room_type ?? ""}
            onChange={(e) =>
              onOp({
                op: "update_element",
                element_id: r.id,
                fields: { room_type: e.target.value || null },
              })
            }
          >
            <option value="">— type à préciser</option>
            {Object.entries(ROOM_TYPE_LABELS).map(([t, label]) => (
              <option key={t} value={t}>
                {label}
              </option>
            ))}
            {r.room_type && !(r.room_type in ROOM_TYPE_LABELS) && (
              <option value={r.room_type}>{r.room_type}</option>
            )}
          </select>
        </label>
      ))}
      <p className="muted">
        {Object.keys(level.vertices).length} sommets ·{" "}
        {Object.keys(level.elements).length} éléments · {project.history.length}{" "}
        modifications
      </p>
      {onOpenAdvanced && (
        <p className="muted">
          Unités, nord du plan, assistant IA, installation :{" "}
          <button className="link" onClick={onOpenAdvanced}>
            ouvrir les réglages avancés
          </button>
        </p>
      )}
    </section>
  );
}

/** Calibration détaillée (Paramètres avancés) : mesures, prévisualisation, facteur. */
export function MeasurementsPanel({
  project,
  level,
  lastCalibration,
  busy,
  onRemoveMeasurement,
  onCalibrate,
}: Props) {
  const units = project.units_display;
  const measurements = Object.values(project.measurements);
  const label = (id: string | null | undefined) => {
    const e = id ? level.elements[id] : undefined;
    if (!e) return "?";
    return e.type === "wall"
      ? `mur ${e.name ?? e.id.slice(0, 4)}`
      : `${e.type} ${e.name ?? e.id.slice(0, 4)}`;
  };
  return (
    <section>
      <h2>Calibration</h2>
      {measurements.length === 0 ? (
        <p className="muted">
          Sélectionne un mur et saisis sa longueur réelle pour calibrer le plan.
        </p>
      ) : (
        <ul className="list">
          {measurements.map((m) => (
            <li key={m.id}>
              {label(m.element_id)} : {formatLength(m.value, units)}
              <button
                className="link"
                onClick={() => onRemoveMeasurement(m.id)}
                title="Supprimer la mesure"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="row">
        <button
          disabled={busy || measurements.length === 0}
          onClick={() => onCalibrate(true)}
        >
          Prévisualiser
        </button>
        <button
          disabled={busy || measurements.length === 0}
          onClick={() => onCalibrate(false)}
        >
          Calibrer
        </button>
      </div>
      {lastCalibration && (
        <p className="muted">
          Facteur ×{decimal(lastCalibration.scale_factor, 4)} · RMS{" "}
          {formatLength(lastCalibration.rms_m, units)}
          {lastCalibration.inconsistent.length > 0 && (
            <>
              {" "}
              ·{" "}
              <strong className="warn">
                {lastCalibration.inconsistent.length} mesure(s) incohérente(s)
              </strong>
            </>
          )}
        </p>
      )}
    </section>
  );
}

function ReportPanel({
  report,
  level,
  busy,
  onOp,
  onAdoptView,
  onRotateView,
}: {
  report: ValidationReport | null;
  level: Level;
  busy: boolean;
  onOp: (op: Op) => void;
  onAdoptView: (cameraId: string) => void;
  onRotateView: (cameraId: string) => void;
}) {
  if (!report || report.violations.length === 0) return null;
  return (
    <section>
      <h2>Vérifications</h2>
      <ul className="list">
        {report.violations.map((v, i) => {
          const camera =
            v.type === "inconsistent_views"
              ? level.elements[v.element_ids[0] ?? ""]
              : undefined;
          return (
            <li key={i} className={`severity-${v.severity}`}>
              {v.message}
              {isCamera(camera) && (
                <ViewActions
                  camera={camera}
                  violation={undefined}
                  busy={busy}
                  onOp={onOp}
                  onAdoptView={onAdoptView}
                  onRotateView={onRotateView}
                />
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Caméra d'une photo sélectionnée dans le plan : ce qu'elle a vu et la décision. */
function CameraPanel({
  camera,
  project,
  violation,
  busy,
  onOp,
  onAdoptView,
  onRotateView,
}: {
  camera: Camera;
  project: Project;
  violation: Violation | undefined;
  busy: boolean;
  onOp: (op: Op) => void;
  onAdoptView: (cameraId: string) => void;
  onRotateView: (cameraId: string) => void;
}) {
  const image = camera.image_id ? project.images[camera.image_id] : undefined;
  if (camera.image_id === null) {
    // Point de vue libre (écran Rendre) : position, hauteur d'œil, visée, inclinaison.
    const set = (fields: Record<string, unknown>) =>
      onOp({ op: "update_element", element_id: camera.id, fields });
    const deg = (rad: number) => Math.round((rad * 180) / Math.PI);
    return (
      <section>
        <h2>Point de vue « {camera.name ?? "libre"} »</h2>
        <p className="muted">
          Glissez le rond dans le plan pour le déplacer, la pointe pour viser.
          Le rendu se lance depuis le cadre Rendus de l'espace de travail.
        </p>
        <NumberField
          label="Hauteur"
          value={Math.round(camera.position.z * 100) / 100}
          suffix="m"
          onCommit={(v) =>
            v > 0 && set({ position: { ...camera.position, z: v } })
          }
        />
        <NumberField
          label="Visée"
          value={deg(camera.yaw)}
          step={1}
          suffix="°"
          onCommit={(v) => set({ yaw: (v * Math.PI) / 180 })}
        />
        <NumberField
          label="Inclinaison"
          value={deg(camera.pitch)}
          step={1}
          suffix="°"
          onCommit={(v) => set({ pitch: (v * Math.PI) / 180 })}
        />
        <button
          className="danger"
          onClick={() => onOp({ op: "remove_element", element_id: camera.id })}
        >
          Supprimer le point de vue
        </button>
      </section>
    );
  }
  return (
    <section>
      <h2>Vue « {camera.name ?? "Photo"} »</h2>
      {image && (
        <p className="muted">
          Photo {image.width_px} × {image.height_px} px · caméra{" "}
          <span className={`badge source-${camera.source}`}>
            {SOURCE_LABEL[camera.source]}
          </span>
        </p>
      )}
      <p>
        <ViewSummary camera={camera} />
      </p>
      <ViewActions
        camera={camera}
        violation={violation}
        busy={busy}
        onOp={onOp}
        onAdoptView={onAdoptView}
        onRotateView={onRotateView}
      />
      <p className="muted">
        Supprimer (touche Suppr) enlève la caméra ; écarter la garde sans effet.
      </p>
    </section>
  );
}

/** Légende des provenances (Paramètres avancés › Données). */
export function Legend() {
  return (
    <section className="legend">
      <h3>Provenance</h3>
      {(Object.keys(SOURCE_LABEL) as Source[]).map((s) => (
        <span key={s} className={`badge source-${s}`}>
          {SOURCE_LABEL[s]}
        </span>
      ))}
      <p className="muted">
        Trait plein : mesuré ou saisi. Pointillé et ≈ : estimé, inféré ou
        généré. Reconstruction estimée, pas un relevé certifié.
      </p>
    </section>
  );
}
