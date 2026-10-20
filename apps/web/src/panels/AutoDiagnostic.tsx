import type { AutoPlanResult } from "../api/client";

const STATUS_LABEL: Record<string, string> = {
  done: "fait",
  skipped: "non fait",
  failed: "sans résultat",
};

const METHOD_LABEL: Record<string, string> = {
  lines: "lignes de la photo",
  depth: "distances estimées",
  attic: "pentes de toit",
  plan_image: "image de plan",
  view: "photo comparée au plan",
  none: "aucune",
};

/** Bloc « Diagnostic », replié par défaut : ce que la construction automatique a fait, étape
 *  par étape, la méthode gardée et celle mise de côté. Pour comprendre, jamais pour agir. */
export function AutoDiagnostic({ last }: { last: AutoPlanResult | null }) {
  return (
    <details className="adv-group diagnostic">
      <summary>Diagnostic</summary>
      <aside className="inspector">
        {!last && (
          <p className="muted">
            Aucune image analysée dans cette session : le détail de la
            construction du plan s'affiche ici après un import.
          </p>
        )}
        {last && (
          <section>
            <p className="muted">
              Méthode gardée :{" "}
              {METHOD_LABEL[last.run.method] ?? last.run.method}
              {last.run.backup
                ? ` · en secours : ${METHOD_LABEL[last.run.backup] ?? last.run.backup}`
                : ""}{" "}
              {last.run.estimate_confidence
                ? ` · confiance : ${last.run.estimate_confidence.label}`
                : ""}{" "}
              · {last.run.result.duration_s.toFixed(1).replace(".", ",")} s
            </p>
            {last.run.estimate_confidence && (
              <p className="muted">
                Facteurs :{" "}
                {last.run.estimate_confidence.factors
                  .map((f) => f.label)
                  .join(" · ")}
              </p>
            )}
            <ul className="list">
              {last.run.stages.map((stage, i) => (
                <li key={i} className={stage.status === "done" ? "" : "muted"}>
                  {stage.label} : {STATUS_LABEL[stage.status] ?? stage.status}
                  {stage.detail ? ` (${stage.detail})` : ""}
                </li>
              ))}
            </ul>
            {last.run.result.assumptions.map((a, i) => (
              <p key={i} className="muted">
                {a.text}
              </p>
            ))}
          </section>
        )}
      </aside>
    </details>
  );
}
