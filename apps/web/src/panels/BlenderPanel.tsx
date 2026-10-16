import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { StepResult } from "../api/types.gen";

interface Props {
  projectId: string;
  levelId: string;
}

/** Lancement de Blender headless (optionnel) et affichage de l'aperçu rendu. */
export function BlenderPanel({ projectId, levelId }: Props) {
  const [status, setStatus] = useState<{
    available: boolean;
    binary: string | null;
  } | null>(null);
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<{ run_id: string; result: StepResult } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [samples, setSamples] = useState(64);
  const [gpu, setGpu] = useState(true);

  useEffect(() => {
    api
      .blenderStatus()
      .then(setStatus, () => setStatus({ available: false, binary: null }));
  }, []);

  const start = (work: Promise<{ run_id: string; result: StepResult }>) => {
    setRunning(true);
    setError(null);
    work.then(
      (r) => {
        setRun(r);
        setRunning(false);
      },
      (e: unknown) => {
        setError(e instanceof Error ? e.message : String(e));
        setRunning(false);
      },
    );
  };
  const launch = () => start(api.runBlender(projectId, levelId));
  const renderPhoto = () =>
    start(api.renderPhoto(projectId, levelId, samples, gpu));

  const preview = run?.result.artifacts.find((a) => a.name === "preview.png");
  const photoRender = run?.result.step === "rendering.photo";
  return (
    <section>
      <h2>Blender</h2>
      {status === null && <p className="muted">…</p>}
      {status && !status.available && (
        <p className="muted">
          Blender n'est pas installé ou introuvable (OPENPLAN_BLENDER_BIN). Le
          viewer ci-contre reste disponible, avec la case « Matières » pour les
          teintes de bois, pierre et enduit ; Blender apporte le fichier .blend
          et le rendu depuis la photo.
        </p>
      )}
      {status?.available && (
        <>
          <p className="muted">{status.binary}</p>
          <button onClick={launch} disabled={running}>
            {running ? "Rendu en cours…" : "Construire .blend, GLB et aperçu"}
          </button>
          <h3>Rendu depuis la photo</h3>
          <p className="muted">
            Image de synthèse de la géométrie estimée, vue par la caméra de la
            photo, matières par défaut (plancher bois, pignons pierre, versants
            bois).
          </p>
          <label className="field">
            <span>Qualité</span>
            <select
              value={samples}
              onChange={(e) => setSamples(Number(e.target.value))}
            >
              <option value={16}>rapide (16)</option>
              <option value={64}>normale (64)</option>
              <option value={256}>fine (256)</option>
            </select>
            <em>éch.</em>
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={gpu}
              onChange={(e) => setGpu(e.target.checked)}
            />
            GPU si disponible (sinon CPU, plus lent)
          </label>
          <button onClick={renderPhoto} disabled={running}>
            {running ? "Rendu en cours…" : "Rendre depuis la photo"}
          </button>
        </>
      )}
      {error && <p className="warn">{error}</p>}
      {photoRender &&
        run?.result.assumptions.map((a) => (
          <p key={a.text} className="warn">
            {a.text}
          </p>
        ))}
      {run && (
        <div>
          <p className="muted">
            {run.result.model_ids.join(", ")} ·{" "}
            {run.result.duration_s.toFixed(1)} s
          </p>
          {preview && (
            <a
              href={api.blenderArtifactUrl(projectId, run.run_id, preview.name)}
              target="_blank"
              rel="noreferrer"
            >
              <img
                className="preview"
                alt={photoRender ? "Rendu depuis la photo" : "Aperçu Blender"}
                src={api.blenderArtifactUrl(
                  projectId,
                  run.run_id,
                  preview.name,
                )}
              />
            </a>
          )}
          <ul className="list">
            {run.result.artifacts.map((a) => (
              <li key={a.name}>
                <a
                  href={api.blenderArtifactUrl(projectId, run.run_id, a.name)}
                  download
                >
                  {a.name}
                </a>{" "}
                <span className="muted">{a.description}</span>
              </li>
            ))}
          </ul>
          {run.result.warnings.map((w, i) => (
            <p key={i} className="warn">
              {w}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
