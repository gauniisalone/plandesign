import { useEffect, useRef, useState } from "react";
import { bellApi, type BellLine } from "../api/client";
import { setBellTarget, useBell } from "../state/bell";
import {
  actionText,
  badgeText,
  lineText,
  relativeTime,
} from "../state/bellText";
import { IconBell } from "../ui/icons";

/** KAN-52 : la cloche, en haut à droite (accueil et atelier). Badge des non-lus (« 9+ »),
 *  panneau à deux onglets (« Non lues », « Toutes »), 50 lignes puis « Voir plus » ; un clic
 *  sur une ligne la marque comme lue (elle seule) et ouvre le projet au bon endroit ;
 *  « Tout marquer comme lu » remet le compteur à 0. */
export function Bell({ accounts }: { accounts: boolean }) {
  const { unread, tick, refresh } = useBell(accounts);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"unread" | "all">("unread");
  const [lines, setLines] = useState<BellLine[]>([]);
  const [next, setNext] = useState<string | null>(null);
  /** L'onglet dont la liste est arrivée (« Rien de nouveau » seulement après la réponse). */
  const [loaded, setLoaded] = useState<string | null>(null);
  const panel = useRef<HTMLDivElement>(null);

  // Ouvert : relu à chaque avis, et à chaque changement d'onglet.
  useEffect(() => {
    if (!open) return;
    let alive = true;
    void bellApi.list(tab).then(
      (page) => {
        if (!alive) return;
        setLines(page.items);
        setNext(page.next);
        setLoaded(tab);
      },
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [open, tab, tick]);

  // Échap ou un clic ailleurs referme le panneau.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onDown = (e: PointerEvent) => {
      if (panel.current && !panel.current.contains(e.target as Node))
        setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  const more = async () => {
    if (!next) return;
    const page = await bellApi.list(tab, next);
    setLines((all) => [...all, ...page.items]);
    setNext(page.next);
  };

  const go = async (line: BellLine) => {
    try {
      if (line.unread) await bellApi.read(line.id);
    } catch {
      // La navigation compte plus que la marque : la ligne restera non lue.
    }
    setOpen(false);
    refresh();
    setBellTarget(line);
    const target = `#/projects/${line.project_id}/board`;
    if (!window.location.hash.startsWith(`#/projects/${line.project_id}`))
      window.location.assign(target);
  };

  const readAll = async () => {
    await bellApi.readAll().catch(() => undefined);
    refresh();
  };

  const badge = badgeText(unread);
  return (
    <div className="bell" ref={panel}>
      <button
        type="button"
        className={`icon bell-btn${open ? " active" : ""}`}
        aria-label={
          badge ? `Notifications, ${unread} non lues` : "Notifications"
        }
        aria-expanded={open}
        title="Notifications"
        onClick={() => setOpen((o) => !o)}
      >
        <IconBell />
        {badge && (
          <span className="bell-badge" data-testid="bell-badge">
            {badge}
          </span>
        )}
      </button>
      {open && (
        <div className="bell-panel" role="dialog" aria-label="Notifications">
          <header className="bell-head">
            <div role="tablist" aria-label="Notifications">
              {(["unread", "all"] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  role="tab"
                  aria-selected={tab === t}
                  onClick={() => setTab(t)}
                >
                  {t === "unread" ? "Non lues" : "Toutes"}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="link"
              disabled={unread === 0}
              onClick={() => void readAll()}
            >
              Tout marquer comme lu
            </button>
          </header>
          {lines.length === 0 && loaded === tab && (
            <p className="bell-empty">
              {tab === "unread" ? "Rien de nouveau." : "Aucune notification."}
            </p>
          )}
          <ul className="bell-lines">
            {lines.map((line) => (
              <li key={line.id}>
                <button
                  type="button"
                  className={`bell-line${line.unread ? " unread" : ""}`}
                  aria-label={lineText(line)}
                  onClick={() => void go(line)}
                >
                  <span
                    className="bell-avatar"
                    aria-hidden="true"
                    ref={(el) =>
                      el?.style.setProperty("--avatar", line.actor_color)
                    }
                  >
                    {line.type === "render.done" && line.mine
                      ? "✓"
                      : line.actor_name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="bell-text">
                    <span className="bell-action">{actionText(line)}</span>
                    <span className="bell-meta">
                      {line.project_name} · {relativeTime(line.at)}
                    </span>
                  </span>
                  {line.unread && (
                    <span className="bell-dot" aria-hidden="true" />
                  )}
                </button>
              </li>
            ))}
          </ul>
          {next && (
            <button
              type="button"
              className="link bell-more"
              onClick={() => void more()}
            >
              Voir plus
            </button>
          )}
        </div>
      )}
    </div>
  );
}
