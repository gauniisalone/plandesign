import { useEffect, useState } from "react";
import {
  adminApi,
  type Audience,
  type AudienceDay,
  type Health,
} from "../api/client";
import { APP_NAME } from "../brand";
import { useAccount } from "../state/account";
import { IconPlan } from "../ui/icons";

/** KAN-109 L1 : le back-office d'augus. L'audience du site public (sans cookie ni script :
 *  tirée du journal du serveur, IP tronquée, aucune donnée personnelle gardée) et la santé
 *  technique de l'application. Réservé à l'administrateur (le serveur refuse de toute façon). */

const PERIODS = [7, 30, 90] as const;
type Period = (typeof PERIODS)[number];

const GIB = 1024 ** 3;
const MIB = 1024 ** 2;
const go = (n: number) =>
  `${(n / GIB).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} Go`;
const mo = (n: number) => `${Math.round(n / MIB).toLocaleString("fr-FR")} Mo`;
const ddmm = (day: string) => `${day.slice(8, 10)}/${day.slice(5, 7)}`;
const sum = (days: AudienceDay[], key: keyof Omit<AudienceDay, "day">) =>
  days.reduce((n, d) => n + d[key], 0);

const WORKER: Record<string, string> = {
  online: "en ligne",
  busy: "occupé (rendu en cours)",
  waiting: "en ligne, il passe son tour",
  paused: "en pause",
  offline: "hors ligne",
};

/** Les vues par jour, en barres (SVG, sans bibliothèque) ; un jour sans ligne vaut 0. */
function ViewsChart({ days, period }: { days: AudienceDay[]; period: Period }) {
  const byDay = new Map(days.map((d) => [d.day, d.views]));
  const today = new Date();
  const series = Array.from({ length: period }, (_, i) => {
    const d = new Date(today);
    d.setUTCDate(today.getUTCDate() - (period - 1 - i));
    const key = d.toISOString().slice(0, 10);
    return { day: key, views: byDay.get(key) ?? 0 };
  });
  const max = Math.max(1, ...series.map((s) => s.views));
  const w = 100 / period;
  return (
    <svg
      className="admin-chart"
      viewBox="0 0 100 40"
      preserveAspectRatio="none"
      role="img"
      aria-label={`Vues par jour sur ${period} jours`}
    >
      {series.map((s, i) => {
        const h = (s.views / max) * 38;
        return (
          <rect
            key={s.day}
            x={i * w + w * 0.15}
            y={40 - h}
            width={w * 0.7}
            height={h}
          >
            <title>{`${ddmm(s.day)} : ${s.views} vue${s.views > 1 ? "s" : ""}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

export function Admin({ onHome }: { onHome: () => void }) {
  const account = useAccount();
  const [period, setPeriod] = useState<Period>(30);
  const [audience, setAudience] = useState<Audience | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const allowed = account?.me.is_admin ?? false;

  useEffect(() => {
    if (!allowed) return;
    adminApi
      .audience(period)
      .then(setAudience, (e: unknown) =>
        setError(e instanceof Error ? e.message : String(e)),
      );
  }, [allowed, period]);
  useEffect(() => {
    if (!allowed) return;
    adminApi.health().then(setHealth, () => setHealth(null));
  }, [allowed]);

  const days = audience?.days ?? [];
  return (
    <div className="boards admin">
      <header className="boards-top">
        <span className="brand-mark">
          <IconPlan />
        </span>
        <span className="boards-brand">{APP_NAME}</span>
        <h1 className="admin-title">Back-office</h1>
        <button type="button" className="link" onClick={onHome}>
          Retour aux projets
        </button>
      </header>
      {!allowed ? (
        <p className="admin-empty" role="alert">
          Réservé à l'administrateur.
        </p>
      ) : (
        <main className="admin-main">
          <section className="admin-card" aria-label="Audience du site">
            <div className="admin-head">
              <h2>Audience du site</h2>
              <div className="admin-periods" role="group" aria-label="Période">
                {PERIODS.map((p) => (
                  <button
                    key={p}
                    type="button"
                    aria-pressed={p === period}
                    onClick={() => setPeriod(p)}
                  >
                    {p} jours
                  </button>
                ))}
              </div>
            </div>
            <p className="admin-note">
              Sans cookie ni script : tirée du journal du serveur, adresse IP
              tronquée, robots écartés.
              {audience?.since && ` Depuis le ${ddmm(audience.since)}.`}
            </p>
            {error && (
              <p className="admin-empty" role="alert">
                {error}
              </p>
            )}
            {audience && !audience.connected ? (
              <p className="admin-empty">Journal du site non branché.</p>
            ) : audience && days.length === 0 ? (
              <p className="admin-empty">Aucune donnée pour l'instant.</p>
            ) : audience ? (
              <>
                <dl className="admin-totals">
                  <div>
                    <dt>Vues</dt>
                    <dd>{sum(days, "views")}</dd>
                  </div>
                  <div>
                    <dt>Visiteurs (somme des jours)</dt>
                    <dd>{sum(days, "visitors")}</dd>
                  </div>
                  <div>
                    <dt>Arrivées par www</dt>
                    <dd>{sum(days, "www")}</dd>
                  </div>
                  <div>
                    <dt>Robots écartés</dt>
                    <dd>{sum(days, "bots")}</dd>
                  </div>
                  <div>
                    <dt>Erreurs 4xx / 5xx</dt>
                    <dd>
                      {sum(days, "errors_4xx")} / {sum(days, "errors_5xx")}
                    </dd>
                  </div>
                </dl>
                <ViewsChart days={days} period={period} />
                <div className="admin-columns">
                  <table className="admin-table">
                    <caption>Pages</caption>
                    <tbody>
                      {audience.pages.map((p) => (
                        <tr key={p.path}>
                          <td>{p.path}</td>
                          <td>{p.views}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <table className="admin-table">
                    <caption>Sources</caption>
                    <tbody>
                      {audience.sources.length === 0 && (
                        <tr>
                          <td>Accès direct seulement</td>
                          <td />
                        </tr>
                      )}
                      {audience.sources.map((s) => (
                        <tr key={s.source}>
                          <td>{s.source}</td>
                          <td>{s.views}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <table className="admin-table">
                    <caption>Appareils</caption>
                    <tbody>
                      <tr>
                        <td>Ordinateur</td>
                        <td>{audience.devices.desktop}</td>
                      </tr>
                      <tr>
                        <td>Mobile</td>
                        <td>{audience.devices.mobile}</td>
                      </tr>
                      <tr>
                        <td>Tablette</td>
                        <td>{audience.devices.tablet}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </>
            ) : null}
          </section>

          <section className="admin-card" aria-label="Santé technique">
            <h2>Santé technique</h2>
            {health && (
              <dl className="admin-totals">
                <div>
                  <dt>Version déployée</dt>
                  <dd>{health.version}</dd>
                </div>
                <div>
                  <dt>Requêtes (24 h)</dt>
                  <dd>{health.api.requests}</dd>
                </div>
                <div>
                  <dt>Erreurs 5xx (24 h)</dt>
                  <dd>{health.api.errors_5xx}</dd>
                </div>
                <div>
                  <dt>Latence p50 / p95</dt>
                  <dd>
                    {health.api.p50_ms ?? "—"} / {health.api.p95_ms ?? "—"} ms
                  </dd>
                </div>
                <div>
                  <dt>Poste de rendu</dt>
                  <dd>{WORKER[health.worker] ?? health.worker}</dd>
                </div>
                <div>
                  <dt>Disque</dt>
                  <dd>
                    {go(health.disk.free)} libres sur {go(health.disk.total)} ·
                    données {mo(health.disk.data)}
                  </dd>
                </div>
              </dl>
            )}
            {health && (
              <p className="admin-note">
                Mesures de l'application depuis son dernier démarrage (
                {new Date(health.api.since).toLocaleString("fr-FR")}), sur 24 h
                glissantes.
              </p>
            )}
          </section>
        </main>
      )}
    </div>
  );
}
