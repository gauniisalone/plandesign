import { useState } from "react";
import type { Op } from "../api/ops";
import type { Level, Project } from "../api/types.gen";
import { typed } from "../format";
import { parseLength, toDisplay } from "../model";

/** T7d1 (décision PO du 16 octobre) : le panneau de droite est fermé par défaut ; le nom et la
 *  hauteur du niveau, qu'on y trouvait sans sélection, sont dans le plan, en tête de la barre du
 *  bas. */
export function LevelBar({
  project,
  level,
  onOp,
}: {
  project: Project;
  level: Level;
  onOp: (op: Op) => void;
}) {
  const units = project.units_display;
  const [name, setName] = useState(level.name);
  const [prevName, setPrevName] = useState(level.name);
  if (prevName !== level.name) {
    setPrevName(level.name);
    setName(level.name);
  }
  const shown = toDisplay(level.height, units);
  const [height, setHeight] = useState(typed(shown));
  const [prevHeight, setPrevHeight] = useState(shown);
  if (prevHeight !== shown) {
    setPrevHeight(shown);
    setHeight(typed(shown));
  }
  const commitName = () => {
    const value = name.trim();
    if (value && value !== level.name)
      onOp({ op: "update_level", fields: { name: value } });
    else setName(level.name);
  };
  const commitHeight = () => {
    const m = parseLength(height, units);
    if (m && m > 0 && Math.abs(m - level.height) > 1e-6)
      onOp({
        op: "update_level",
        fields: { height: m, height_source: "measured" },
      });
    else setHeight(typed(shown));
  };
  return (
    <div className="level-bar" role="group" aria-label="Niveau">
      <label>
        <span className="muted">Niveau</span>
        <input
          aria-label="Nom du niveau"
          value={name}
          maxLength={80}
          onChange={(e) => setName(e.target.value)}
          onBlur={commitName}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
        />
      </label>
      <label
        title={`Hauteur sous plafond (${level.height_source === "measured" ? "mesurée" : "estimée"})`}
      >
        <span className="muted">Hauteur</span>
        <input
          aria-label="Hauteur sous plafond"
          className="level-bar-height"
          inputMode="decimal"
          value={height}
          onChange={(e) => setHeight(e.target.value)}
          onBlur={commitHeight}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
        />
        <span className="muted">{units}</span>
      </label>
    </div>
  );
}
