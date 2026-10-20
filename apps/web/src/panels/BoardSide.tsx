import { useEffect, useRef, useState, type ReactNode } from "react";
import type { CommentsController } from "../board/useComments";

const MIN_TOP = 120;
const MIN_REST = 160;

/** KAN-64 : le panneau de droite de l'espace de travail, comme chez Figma et Miro. En haut,
 *  l'Assistant (ou les actions de la sélection), rangés là par l'espace de travail ; dessous,
 *  les Commentaires ; en bas, l'Activité, repliée d'office. La séparation entre l'Assistant et
 *  les Commentaires se règle ; chaque zone se replie. Plus rien ne flotte en bas de l'espace de
 *  travail, sauf le zoom. */
export function BoardSide({
  comments,
  commentsOpen,
  onCommentsOpen,
  activity,
  activityOpen,
  onActivityOpen,
  onSlots,
}: {
  comments: CommentsController;
  commentsOpen: boolean;
  onCommentsOpen: (open: boolean) => void;
  activity: ReactNode;
  activityOpen: boolean;
  onActivityOpen: (open: boolean) => void;
  onSlots: (slots: {
    top: HTMLElement | null;
    comments: HTMLElement | null;
  }) => void;
}) {
  const [top, setTop] = useState<HTMLElement | null>(null);
  const [list, setList] = useState<HTMLElement | null>(null);
  /** Hauteur donnée à la zone haute par la séparation (null : selon son contenu). */
  const [topHeight, setTopHeight] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    onSlots({ top, comments: commentsOpen ? list : null });
    return () => onSlots({ top: null, comments: null });
  }, [top, list, commentsOpen, onSlots]);

  const open = comments.threads.filter((t) => !t.resolved).length;

  const drag = (e: React.PointerEvent<HTMLDivElement>) => {
    const zone = top?.getBoundingClientRect();
    const whole = box.current?.getBoundingClientRect();
    if (!zone || !whole) return;
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    const startY = e.clientY;
    const startH = zone.height;
    const move = (ev: PointerEvent) => {
      const max = whole.height - MIN_REST;
      setTopHeight(
        Math.max(MIN_TOP, Math.min(max, startH + ev.clientY - startY)),
      );
    };
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  };

  return (
    <div className="board-side" ref={box}>
      <div
        className="board-zone top"
        ref={setTop}
        style={topHeight !== null ? { height: topHeight } : undefined}
      />
      <div
        className="board-split"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Séparation entre l'Assistant et les Commentaires"
        title="Glisser pour régler la séparation (double-clic : par défaut)"
        onPointerDown={drag}
        onDoubleClick={() => setTopHeight(null)}
      />
      <section
        className={`board-zone comments${commentsOpen ? "" : " collapsed"}`}
        aria-label="Zone des commentaires"
      >
        <header className="board-zone-head">
          <button
            type="button"
            className="board-zone-toggle"
            aria-expanded={commentsOpen}
            aria-label={
              commentsOpen
                ? "Replier les commentaires"
                : "Déplier les commentaires"
            }
            onClick={() => onCommentsOpen(!commentsOpen)}
          >
            <span aria-hidden="true">{commentsOpen ? "▾" : "▸"}</span>{" "}
            Commentaires
          </button>
          <span className="board-zone-meta">
            {open} ouvert{open > 1 ? "s" : ""}
          </span>
          <span className="board-zone-sp" />
          <button
            type="button"
            className="link"
            onClick={() => comments.setHidden(!comments.hidden)}
          >
            {comments.hidden ? "Afficher les épingles" : "Masquer les épingles"}
          </button>
        </header>
        {commentsOpen && <div className="board-zone-body" ref={setList} />}
      </section>
      <section
        className={`board-zone activity${activityOpen ? "" : " collapsed"}`}
        aria-label="Zone de l'activité"
      >
        <header className="board-zone-head">
          <button
            type="button"
            className="board-zone-toggle"
            aria-expanded={activityOpen}
            aria-label={
              activityOpen ? "Replier l'activité" : "Déplier l'activité"
            }
            onClick={() => onActivityOpen(!activityOpen)}
          >
            <span aria-hidden="true">{activityOpen ? "▾" : "▸"}</span> Activité
          </button>
        </header>
        {activityOpen && <div className="board-zone-body">{activity}</div>}
      </section>
    </div>
  );
}
