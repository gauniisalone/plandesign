import type { DoctorReport } from "../api/client";

/** « État de l'installation » : un état par ligne, la commande à lancer dessous quand il
 *  manque quelque chose ; replié par défaut, le titre résume. Sur l'accueil et dans les
 *  Paramètres avancés. */
export function DoctorBlock({ report }: { report: DoctorReport }) {
  const missing = report.checks.filter((c) => c.status === "missing");
  const okCount = report.checks.filter((c) => c.status === "ok").length;
  return (
    <details className="doctor">
      <summary>
        État de l'installation (version {report.version}) : {okCount} OK
        {missing.length > 0
          ? ` · ${missing.length} à installer si besoin`
          : " · tout est là"}
        {!report.ok ? " · une pièce requise manque" : ""}
      </summary>
      <ul>
        {report.checks.map((c) => (
          <li key={c.key} className={`status-${c.status}`}>
            <span>
              <strong>{c.label}</strong> · {c.detail}
            </span>
            {c.fix && <code>{c.fix}</code>}
          </li>
        ))}
      </ul>
      <p className="muted">
        Même bilan en ligne de commande : <code>uv run openplan doctor</code>.
        Rien n'est téléchargé ni modifié.
      </p>
    </details>
  );
}
