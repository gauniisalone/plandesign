import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { BoardItem, NoteColor } from "../api/client";
import { colorOf, fitFontSize, POSTIT_COLORS } from "./postits";

/** KAN-65 : le contenu d'un post-it. Double-clic pour écrire ; le texte se réduit pour tenir
 *  dans le post-it (mesuré sur une copie invisible, à la taille de la boîte). */
export function PostitBody({
  item,
  editing,
  onText,
  children,
}: {
  item: BoardItem;
  editing: boolean;
  /** Fin de l'écriture (le champ perd le focus) : le texte saisi. */
  onText: (text: string) => void;
  children?: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const probe = useRef<HTMLSpanElement>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [size, setSize] = useState(24);
  const shown = editing ? (draft ?? item.text) : item.text;
  useLayoutEffect(() => {
    const p = probe.current;
    const b = box.current;
    if (!p || !b) return;
    const width = b.clientWidth;
    const height = b.clientHeight;
    if (width <= 0 || height <= 0) return;
    // Un grand post-it a un grand texte (au plus le quart de son petit côté).
    const max = Math.max(16, Math.round(Math.min(item.w, item.h) / 4));
    const best = fitFontSize(
      (s) => {
        p.style.fontSize = `${s}px`;
        return p.scrollHeight <= height && p.scrollWidth <= width;
      },
      8,
      max,
    );
    // La copie garde la taille retenue : plus grande, elle ferait défiler la boîte.
    p.style.fontSize = `${best}px`;
    setSize(best);
  }, [shown, item.w, item.h]);
  const style = { fontSize: `${size}px` };
  return (
    <>
      <div className="postit-box" ref={box}>
        <span className="postit-probe" ref={probe} aria-hidden="true">
          {(shown || "Post-it vide") + (shown.endsWith("\n") ? " " : "")}
        </span>
        {editing ? (
          <textarea
            autoFocus
            aria-label="Texte du post-it"
            defaultValue={item.text}
            style={style}
            onPointerDown={(e) => e.stopPropagation()}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={(e) => {
              setDraft(null);
              onText(e.target.value.slice(0, 4000));
            }}
          />
        ) : (
          <span
            className={`postit-text${item.text ? "" : " empty"}`}
            style={style}
          >
            {item.text || "Post-it vide"}
          </span>
        )}
      </div>
      {children}
    </>
  );
}

/** KAN-65 : la palette, avec les actions de la sélection ; un clic change la couleur de tous
 *  les post-its sélectionnés. */
export function PostitPalette({
  notes,
  onPick,
}: {
  notes: BoardItem[];
  onPick: (color: NoteColor) => void;
}) {
  return (
    <span
      className="postit-palette"
      role="group"
      aria-label="Couleur du post-it"
    >
      {POSTIT_COLORS.map((c) => (
        <button
          key={c.id}
          type="button"
          className={`postit-swatch postit-${c.id}`}
          aria-label={c.label}
          title={`${c.label}${notes.length > 1 ? ` (${notes.length} post-its)` : ""}`}
          aria-pressed={notes.every((n) => colorOf(n) === c.id)}
          onClick={() => onPick(c.id)}
        />
      ))}
    </span>
  );
}
