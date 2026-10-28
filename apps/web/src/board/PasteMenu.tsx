import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { IconLink, IconMoodboardNode, IconPhotosNode } from "../ui/icons";

/** KAN-98 L3 (maquette C2) : un collage sans node choisi ouvre ce mini-menu au pointeur :
 *  où mettre ce qui est collé. Flèches puis Entrée, ou un clic ; Échap ou un clic dehors
 *  annule. */

export type PasteChoice = "photos" | "moodboard" | "link";

const CHOICES: Record<PasteChoice, { label: string; hint: string }> = {
  photos: {
    label: "Photos de la pièce",
    hint: "servira à construire le plan",
  },
  moodboard: { label: "Moodboard", hint: "guidera le style des rendus" },
  link: { label: "Lien seul", hint: "une carte avec le site, sans image" },
};

const ICONS: Record<PasteChoice, typeof IconLink> = {
  photos: IconPhotosNode,
  moodboard: IconMoodboardNode,
  link: IconLink,
};

/** Ce que dit l'en-tête : le nombre d'images, ou l'image d'un lien. */
function pasteHeading(count: number, link: boolean): string {
  if (link) return "Coller l'image de ce lien dans…";
  return count > 1 ? `Coller ${count} images dans…` : "Coller 1 image dans…";
}

export function PasteMenu({
  x,
  y,
  count,
  link,
  preview,
  choices,
  onPick,
  onCancel,
}: {
  /** Position du pointeur, en pixels de la fenêtre. */
  x: number;
  y: number;
  count: number;
  link: boolean;
  /** Aperçu de l'image collée (adresse locale d'un fichier), s'il y en a un. */
  preview: string | null;
  choices: PasteChoice[];
  onPick: (choice: PasteChoice) => void;
  onCancel: () => void;
}) {
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const cancel = useRef(onCancel);
  useEffect(() => {
    cancel.current = onCancel;
  }, [onCancel]);
  useEffect(() => {
    const outside = (e: PointerEvent) => {
      if (box.current && !box.current.contains(e.target as Node))
        cancel.current();
    };
    window.addEventListener("pointerdown", outside, true);
    return () => window.removeEventListener("pointerdown", outside, true);
  }, []);
  // Le menu reste entier dans la fenêtre (le pointeur peut être près d'un bord) : sa taille
  // réelle est mesurée avant d'être peinte, puis il est placé ; sans place dessous, il s'ouvre
  // au-dessus. Le focus ensuite (caché, il ne le prendrait pas) : flèches et Entrée.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const { width: w, height: h } = el.getBoundingClientRect();
    const below = y + h + 8 <= window.innerHeight;
    const top = below ? y : Math.min(y - h, window.innerHeight - h - 8);
    el.style.left = `${Math.max(8, Math.min(x, window.innerWidth - w - 8))}px`;
    el.style.top = `${Math.max(8, top)}px`;
    el.style.visibility = "visible";
    el.focus();
  }, [x, y, choices.length, preview]);
  return (
    <div
      ref={box}
      className="board-paste-menu"
      role="menu"
      aria-label={pasteHeading(count, link)}
      tabIndex={-1}
      style={{ visibility: "hidden" }}
      onKeyDown={(e) => {
        // Les raccourcis de l'espace de travail (écoutés sur la fenêtre) ne voient pas les
        // touches du menu : ni Suppr, ni les flèches, ni une lettre d'outil.
        e.stopPropagation();
        if (e.key === "ArrowDown") {
          e.preventDefault();
          setActive((a) => (a + 1) % choices.length);
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          setActive((a) => (a - 1 + choices.length) % choices.length);
        } else if (e.key === "Enter") {
          e.preventDefault();
          const choice = choices[active];
          if (choice) onPick(choice);
        } else if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          onCancel();
        }
      }}
    >
      <div className="board-paste-head">
        {preview ? (
          <img src={preview} alt="" className="board-paste-preview" />
        ) : (
          <span className="board-paste-preview" aria-hidden="true">
            <IconLink />
          </span>
        )}
        <span>
          Rien n'est sélectionné.
          <br />
          {pasteHeading(count, link)}
        </span>
      </div>
      {choices.map((choice, i) => {
        const Icon = ICONS[choice];
        return (
          <button
            key={choice}
            type="button"
            role="menuitem"
            className={`board-paste-choice type-${choice}${i === active ? " active" : ""}`}
            onPointerEnter={() => setActive(i)}
            onClick={() => onPick(choice)}
          >
            <Icon aria-hidden="true" />
            <span>
              {CHOICES[choice].label}
              <small>{CHOICES[choice].hint}</small>
            </span>
          </button>
        );
      })}
      <div className="board-paste-foot">
        ↑ ↓ puis Entrée · Échap pour annuler
      </div>
    </div>
  );
}
