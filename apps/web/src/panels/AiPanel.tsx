import { useEffect, useState } from "react";
import {
  aiApi,
  type AiStatus,
  type IntentionRun,
  type NetworkLogEntry,
  type PerceptionRun,
  type ProjectSettingsUpdate,
} from "../api/client";
import type { Op } from "../api/ops";
import type { Level, Project } from "../api/types.gen";
import { formatTime, walls } from "../model";

interface Props {
  project: Project;
  level: Level;
  busy: boolean;
  lastPerception: PerceptionRun | null;
  /** Dernier brief (fiche Aménager) : recharge le journal réseau. */
  lastBrief: IntentionRun | null;
  onSettings: (s: ProjectSettingsUpdate) => void;
  onPerceive: (imageId: string) => void;
  onOp: (op: Op) => void;
}

/** Assistant IA (Paramètres avancés) : modèle configuré, consentement par projet, perception
 *  d'une photo, journal réseau. Le rôle Intention est dans la fiche « Aménager ». */
export function AiPanel({
  project,
  level,
  busy,
  lastPerception,
  lastBrief,
  onSettings,
  onPerceive,
  onOp,
}: Props) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [log, setLog] = useState<NetworkLogEntry[]>([]);
  const [imageId, setImageId] = useState<string>("");
  const images = Object.values(project.images);
  const privacy = project.privacy;
  const consented = privacy.allow_remote_models && privacy.remote_consent;

  useEffect(() => {
    aiApi.status().then(setStatus, () =>
      setStatus({
        configured: false,
        model_id: null,
        destination: null,
        remote: null,
        supports_images: null,
      }),
    );
  }, []);

  // Le journal se recharge après chaque perception (et à l'ouverture).
  useEffect(() => {
    let cancelled = false;
    aiApi.networkLog(project.id).then(
      (entries) => {
        if (!cancelled) setLog(entries);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, [project.id, lastPerception, lastBrief]);

  const selected = imageId || images[0]?.id || "";
  const canPerceive =
    !!status?.configured &&
    !!selected &&
    !busy &&
    (!status.remote || consented) &&
    status.supports_images !== false;

  const askConsent = () => {
    if (!status) return;
    const ok = window.confirm(
      `Envoyer des données à ${status.destination ?? "un service distant"} ?\n\n` +
        "Ce qui partira à chaque analyse : la photo choisie (réduite, EXIF retiré) et deux consignes fixes.\n" +
        "Ce qui ne partira jamais : les autres photos, le plan, ton nom, l'emplacement.\n" +
        "Chaque envoi est inscrit dans le journal réseau de ce projet.\n\n" +
        "Confirmer pour ce projet ?",
    );
    if (ok)
      onSettings({
        privacy: {
          ...privacy,
          allow_remote_models: true,
          remote_consent: true,
        },
      });
  };

  return (
    <aside className="inspector">
      <section>
        <h2>Assistant IA</h2>
        {status === null && <p className="muted">…</p>}
        {status && !status.configured && (
          <p className="muted">
            Aucun modèle configuré. Renseigne <code>OPENPLAN_AI_BASE_URL</code>{" "}
            et <code>OPENPLAN_AI_MODEL</code> dans <code>.env</code> (serveur
            local Ollama, OpenAI ou Gemini), puis relance l'API.
          </p>
        )}
        {status?.configured && (
          <>
            <p className="muted">
              {status.model_id} · {status.remote ? "distant" : "local"} ·{" "}
              {status.destination}
            </p>
            {status.remote && !consented && (
              <>
                <p className="warn">
                  Ce modèle est hors de ta machine. Rien ne lui est envoyé sans
                  ta confirmation pour ce projet.
                </p>
                <button onClick={askConsent}>
                  Autoriser l'envoi pour ce projet
                </button>
              </>
            )}
            {status.remote && consented && (
              <p className="row">
                <span className="muted">Envoi autorisé pour ce projet.</span>
                <button
                  className="link"
                  onClick={() =>
                    onSettings({
                      privacy: { ...privacy, remote_consent: false },
                    })
                  }
                >
                  révoquer
                </button>
              </p>
            )}
            {images.length === 0 ? (
              <p className="muted">
                Importe une photo dans le panneau Photos pour l'analyser.
              </p>
            ) : (
              <div className="row">
                <select
                  value={selected}
                  onChange={(e) => setImageId(e.target.value)}
                >
                  {images.map((img, i) => (
                    <option key={img.id} value={img.id}>
                      photo {i + 1} · {img.width_px}×{img.height_px}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => onPerceive(selected)}
                  disabled={!canPerceive}
                  title="Rôle Perception : éléments et dimensions estimés"
                >
                  Analyser
                </button>
              </div>
            )}
          </>
        )}
      </section>
      {lastPerception && (
        <PerceptionView
          projectId={project.id}
          level={level}
          run={lastPerception}
          onOp={onOp}
        />
      )}
      {log.length > 0 && <NetworkLog entries={log} />}
    </aside>
  );
}

function PerceptionView({
  projectId,
  level,
  run,
  onOp,
}: {
  projectId: string;
  level: Level;
  run: PerceptionRun;
  onOp: (op: Op) => void;
}) {
  const overlay = run.result.artifacts.find(
    (a) => a.name === "vlm_overlay.png",
  );
  const room = run.perception.room;
  const levelEmpty = walls(level).length === 0;
  const pct = (v: number) => `${Math.round(v * 100)} %`;
  const createRectangle = () => {
    if (!room.width_m || !room.depth_m) return;
    const w = room.width_m;
    const d = room.depth_m;
    const corners = [
      { x: 0, y: 0 },
      { x: w, y: 0 },
      { x: w, y: d },
      { x: 0, y: d },
    ];
    const confidence = Math.min(0.6, room.confidence);
    for (let i = 0; i < 4; i++) {
      onOp({
        op: "add_wall",
        start: corners[i]!,
        end: corners[(i + 1) % 4]!,
        source: "estimated",
        confidence,
      });
    }
  };
  return (
    <section>
      <h2>Perception</h2>
      <p className="muted">
        {run.result.model_ids.join(", ")} · {run.result.duration_s.toFixed(1)} s
        · confiance déclarée {pct(run.result.confidence)}
      </p>
      {overlay && (
        <a
          href={aiApi.artifactUrl(projectId, run.run_id, overlay.name)}
          target="_blank"
          rel="noreferrer"
        >
          <img
            className="preview"
            alt="Boîtes déclarées par le modèle"
            src={aiApi.artifactUrl(projectId, run.run_id, overlay.name)}
          />
        </a>
      )}
      <ul className="list">
        {run.elements.map((e, i) => (
          <li key={i}>
            {e.category}
            {e.label ? ` · ${e.label}` : ""}{" "}
            <span className="muted">{pct(e.score)}</span>
          </li>
        ))}
      </ul>
      <h3>Dimensions déclarées (estimées, non mesurées)</h3>
      <p>
        forme : {room.shape} · largeur {room.width_m ?? "?"} m · profondeur{" "}
        {room.depth_m ?? "?"} m · hauteur {room.ceiling_height_m ?? "?"} m ·
        confiance {pct(room.confidence)}
      </p>
      <div className="row">
        {room.ceiling_height_m && (
          <button
            onClick={() =>
              onOp({
                op: "update_level",
                fields: {
                  height: room.ceiling_height_m,
                  height_source: "estimated",
                },
              })
            }
            title="Hauteur du niveau, marquée estimée"
          >
            Appliquer la hauteur
          </button>
        )}
        {room.width_m && room.depth_m && (
          <button
            onClick={createRectangle}
            disabled={!levelEmpty}
            title={
              levelEmpty
                ? "Quatre murs estimés"
                : "Le niveau contient déjà des murs"
            }
          >
            Créer le rectangle
          </button>
        )}
      </div>
      {[
        ...run.result.assumptions.map((a) => a.text),
        ...run.result.warnings,
      ].map((t, i) => (
        <p key={i} className="warn">
          {t}
        </p>
      ))}
    </section>
  );
}

function NetworkLog({ entries }: { entries: NetworkLogEntry[] }) {
  const kb = (n: number | null) =>
    n == null ? "?" : `${(n / 1024).toFixed(0)} Ko`;
  return (
    <section>
      <h2>Journal réseau</h2>
      <p className="muted">
        Chaque appel à un modèle, sans son contenu. {entries.length} entrée(s).
      </p>
      <ul className="list">
        {entries
          .slice(-8)
          .reverse()
          .map((e, i) => (
            <li key={i} className={e.ok ? "" : "severity-medium"}>
              {formatTime(e.timestamp)} · {e.purpose}
              {e.probe ? " (vérification sans image)" : ""} → {e.destination} (
              {e.remote ? "distant" : "local"}) · {e.images} image(s) · ↑{" "}
              {kb(e.request_bytes)} ↓ {kb(e.response_bytes)} ·{" "}
              {e.duration_s.toFixed(1)} s
              {e.ok ? "" : ` · ${e.error ?? "échec"}`}
            </li>
          ))}
      </ul>
    </section>
  );
}
