import { useEffect, useState } from "react";
import { adminApi, type Usage, type UsageWeek } from "../api/client";

/** KAN-109 L2 : l'usage de l'application, en agrégats par semaine (lundi, UTC). Événements
 *  pseudonymisés, sans contenu ; comptes administrateurs exclus ; un taux calculé sur moins de
 *  5 comptes ne s'affiche pas. Aucune liste de comptes ni de projets. */

const SMALL = "— (trop peu)";
const pct = (v: number | null) =>
  v === null ? SMALL : `${Math.round(v * 100)} %`;
const num = (v: number | null, unit = "") =>
  v === null ? SMALL : `${v.toLocaleString("fr-FR")}${unit}`;
const ddmm = (day: string) => `${day.slice(8, 10)}/${day.slice(5, 7)}`;
const CAUSES: Record<string, string> = {
  lost: "perdu (poste muet)",
  vram: "mémoire graphique",
  refused: "demande refusée",
  other: "autre",
};

/** La dernière semaine où `pick` a une valeur (les cohortes récentes n'ont pas encore J+30). */
function latest(weeks: UsageWeek[], pick: (w: UsageWeek) => number | null) {
  for (let i = weeks.length - 1; i >= 0; i--) {
    const w = weeks[i]!;
    if (pick(w) !== null) return w;
  }
  return null;
}

function ActiveChart({ weeks }: { weeks: UsageWeek[] }) {
  const max = Math.max(1, ...weeks.map((w) => w.active.weekly));
  const step = 100 / weeks.length;
  return (
    <svg
      className="admin-chart"
      viewBox="0 0 100 40"
      preserveAspectRatio="none"
      role="img"
      aria-label={`Comptes actifs par semaine sur ${weeks.length} semaines`}
    >
      {weeks.map((w, i) => {
        const h = (w.active.weekly / max) * 38;
        return (
          <rect
            key={w.week}
            x={i * step + step * 0.15}
            y={40 - h}
            width={step * 0.7}
            height={h}
          >
            <title>{`Semaine du ${ddmm(w.week)} : ${w.active.weekly} actifs`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

export function AdminUsage() {
  const [usage, setUsage] = useState<Usage | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    adminApi.usage(12).then(setUsage, () => setFailed(true));
  }, []);
  const weeks = usage?.weeks ?? [];
  // La dernière semaine complète (la semaine en cours n'est pas finie).
  const done = weeks.length > 1 ? weeks[weeks.length - 2]! : weeks[0];
  const d7 = latest(weeks, (w) => w.retention.d7);
  const d30 = latest(weeks, (w) => w.retention.d30);
  return (
    <section className="admin-card" aria-label="Usage de l'application">
      <h2>Usage de l'application</h2>
      <p className="admin-note">
        Comptes administrateurs exclus · données pseudonymisées, sans contenu ·
        gardées 13 mois.
        {done && ` Semaine du ${ddmm(done.week)}.`}
      </p>
      {failed && (
        <p className="admin-empty" role="alert">
          Indicateurs indisponibles.
        </p>
      )}
      {done && (
        <>
          <dl className="admin-totals">
            <div>
              <dt>Inscriptions</dt>
              <dd>{done.signups}</dd>
            </div>
            <div>
              <dt>Actifs (semaine) · par jour</dt>
              <dd>
                {done.active.weekly} · {done.active.daily_mean}
              </dd>
            </div>
            <div>
              <dt>Activation : 1er plan sous 7 j</dt>
              <dd>{pct(done.activation.plan)}</dd>
            </div>
            <div>
              <dt>puis 1er rendu sous 7 j</dt>
              <dd>{pct(done.activation.render)}</dd>
            </div>
            <div>
              <dt>Délai jusqu'au 1er rendu (médiane)</dt>
              <dd>{num(done.first_render_hours, " h")}</dd>
            </div>
            <div>
              <dt>Réussite des rendus</dt>
              <dd>{pct(done.renders.success)}</dd>
            </div>
            <div>
              <dt>Rétention J+7{d7 ? ` (cohorte du ${ddmm(d7.week)})` : ""}</dt>
              <dd>{pct(d7?.retention.d7 ?? null)}</dd>
            </div>
            <div>
              <dt>
                Rétention J+30{d30 ? ` (cohorte du ${ddmm(d30.week)})` : ""}
              </dt>
              <dd>{pct(d30?.retention.d30 ?? null)}</dd>
            </div>
          </dl>
          <ActiveChart weeks={weeks} />
          <div className="admin-columns">
            <table className="admin-table">
              <caption>Rendus de la semaine</caption>
              <tbody>
                <tr>
                  <td>Demandés · faits · en échec</td>
                  <td>
                    {done.renders.requested} · {done.renders.done} ·{" "}
                    {done.renders.failed}
                  </td>
                </tr>
                {Object.entries(done.renders.causes).map(([cause, n]) => (
                  <tr key={cause}>
                    <td>Échec : {CAUSES[cause] ?? cause}</td>
                    <td>{n}</td>
                  </tr>
                ))}
                <tr>
                  <td>Durée de calcul (médiane)</td>
                  <td>{num(done.renders.median_seconds, " s")}</td>
                </tr>
              </tbody>
            </table>
            <table className="admin-table">
              <caption>Usage</caption>
              <tbody>
                <tr>
                  <td>Projets actifs</td>
                  <td>{done.usage.projects}</td>
                </tr>
                <tr>
                  <td>Plans par projet</td>
                  <td>{num(done.usage.plans_per_project)}</td>
                </tr>
                <tr>
                  <td>Rendus par projet</td>
                  <td>{num(done.usage.renders_per_project)}</td>
                </tr>
                <tr>
                  <td>Rendus guidés par un Moodboard</td>
                  <td>{pct(done.usage.guided)}</td>
                </tr>
                <tr>
                  <td>Moodboards reliés</td>
                  <td>{done.usage.moodboards_linked}</td>
                </tr>
              </tbody>
            </table>
            <table className="admin-table">
              <caption>Du site à l'application</caption>
              <tbody>
                <tr>
                  <td>Visites du site</td>
                  <td>{done.site.visits}</td>
                </tr>
                <tr>
                  <td>Clics vers l'application</td>
                  <td>après l'ouverture</td>
                </tr>
                <tr>
                  <td>Inscriptions</td>
                  <td>{done.site.signups}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
