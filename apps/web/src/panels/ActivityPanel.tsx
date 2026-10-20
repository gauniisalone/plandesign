import { useEffect, useState } from "react";
import { activityApi, type ActivityKind, type BellLine } from "../api/client";
import { setBellTarget } from "../state/bell";
import { actionText, relativeTime } from "../state/bellText";

const KINDS: { value: ActivityKind | null; label: string }[] = [
  { value: null, label: "Tout" },
  { value: "comments", label: "Commentaires" },
  { value: "board", label: "Espace de travail" },
  { value: "plan", label: "Plan" },
  { value: "members", label: "Membres" },
  { value: "renders", label: "Rendus" },
];

/** KAN-58 : le journal d'activité du projet. Tout le journal (mes gestes compris, et ce que la
 *  cloche ne notifie pas), regroupé comme la cloche ; filtres par personne et par sorte ;
 *  50 lignes puis « Voir plus » ; un clic mène au bon endroit, comme la cloche. Relu à chaque
 *  avis du projet (`tick`). */
export function ActivityPanel({
  projectId,
  tick,
  onClose,
}: {
  projectId: string;
  tick: string;
  onClose: () => void;
}) {
  const [kind, setKind] = useState<ActivityKind | null>(null);
  const [who, setWho] = useState<string | null>(null);
  const [lines, setLines] = useState<BellLine[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<string | null>(null);
  /** Les personnes vues dans le journal (le filtre « par personne »). */
  const [people, setPeople] = useState<Record<string, string>>({});
  const key = `${kind ?? ""}|${who ?? ""}`;

  useEffect(() => {
    let alive = true;
    void activityApi.list(projectId, { who, kind }).then(
      (page) => {
        if (!alive) return;
        setLines(page.items);
        setNext(page.next);
        setLoaded(key);
        setPeople((known) => ({
          ...known,
          ...Object.fromEntries(
            page.items.map((l) => [l.actor_id, l.actor_name]),
          ),
        }));
      },
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [projectId, who, kind, key, tick]);

  const more = async () => {
    if (!next) return;
    const page = await activityApi.list(projectId, { who, kind }, next);
    setLines((all) => [...all, ...page.items]);
    setNext(page.next);
  };

  return (
    <div className="activity-panel" role="dialog" aria-label="Activité">
      <header className="activity-head">
        <strong>Activité</strong>
        <button type="button" className="link" onClick={onClose}>
          Fermer
        </button>
      </header>
      <div className="activity-filters">
        <div role="group" aria-label="Sorte">
          {KINDS.map((k) => (
            <button
              key={k.label}
              type="button"
              aria-pressed={kind === k.value}
              onClick={() => setKind(k.value)}
            >
              {k.label}
            </button>
          ))}
        </div>
        <select
          aria-label="Personne"
          value={who ?? ""}
          onChange={(e) => setWho(e.target.value || null)}
        >
          <option value="">Tout le monde</option>
          {Object.entries(people).map(([id, name]) => (
            <option key={id} value={id}>
              {name}
            </option>
          ))}
        </select>
      </div>
      {lines.length === 0 && loaded === key && (
        <p className="bell-empty">Rien pour l'instant.</p>
      )}
      <ul className="bell-lines">
        {lines.map((line) => (
          <li key={line.id}>
            <button
              type="button"
              className="bell-line"
              onClick={() => setBellTarget(line)}
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
                <span className="bell-meta">{relativeTime(line.at)}</span>
              </span>
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
  );
}
